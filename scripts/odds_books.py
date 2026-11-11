#!/usr/bin/env python3
"""Prices from several sportsbooks (TheRundown free plan: DraftKings, FanDuel, BetMGM; pre-game moneyline, spread, total).

Separate from the app: it only saves what TheRundown returns as write-once JSON files under data/odds-books/ and reads
them back for scripts/early_board.py. It never touches the app's stores, forecasts or Telegram. The key is read from
.env.odds (THERUNDOWN_API_KEY=...), which git ignores; it is never printed.

    python3 scripts/odds_books.py fetch        # today and the next two UTC dates, NBA / NHL / NFL (about 9 requests)
    python3 scripts/odds_books.py show         # what the latest files hold
"""
import argparse, glob, json, os, sys, time, urllib.error, urllib.request
from datetime import datetime, timedelta, timezone

BASE = "https://therundown.io/api/v2"
SPORT_IDS = {"nba": 4, "nhl": 6, "nfl": 2}
BOOKS = {"19": "DraftKings", "22": "BetMGM", "23": "FanDuel"}
MARKET = {1: "winner", 2: "spread", 3: "total"}
# TheRundown and ESPN mostly agree on abbreviations; these are the usual differences, mapped to one spelling.
ALIAS = {"UTA": "UTAH", "NYK": "NY", "GSW": "GS", "SAS": "SA", "NOP": "NO", "WAS": "WSH", "PHO": "PHX", "TBL": "TB", "NJD": "NJ", "SJS": "SJ",
         "LAK": "LA", "MON": "MTL", "CLB": "CBJ", "WAS_NHL": "WSH", "NAS": "NSH", "KCC": "KC", "GBP": "GB", "NEP": "NE", "NOS": "NO", "SFO": "SF",
         "TBB": "TB", "LVR": "LV", "JAC": "JAX", "WAS_NFL": "WSH", "VEG": "VGK", "UTAH_NHL": "UTA"}
FMT = "%Y-%m-%dT%H:%M:%SZ"


def canon(abbr):
    a = (abbr or "").upper()
    return ALIAS.get(a, a)


def root_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_key(root):
    path = os.path.join(root, ".env.odds")
    if not os.path.exists(path):
        sys.exit("No .env.odds file with THERUNDOWN_API_KEY=...")
    for line in open(path):
        if line.startswith("THERUNDOWN_API_KEY="):
            return line.split("=", 1)[1].strip()
    sys.exit(".env.odds has no THERUNDOWN_API_KEY line")


def fetch(root, days=3):
    key = read_key(root)
    out_dir = os.path.join(root, "data", "odds-books")
    os.makedirs(out_dir, exist_ok=True)
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    total = 0
    for sport, sid in SPORT_IDS.items():
        for d in range(days):
            date = (now + timedelta(days=d)).strftime("%Y-%m-%d")
            url = f"{BASE}/sports/{sid}/events/{date}?market_ids=1,2,3&affiliate_ids={','.join(BOOKS)}&main_line=true"
            req = urllib.request.Request(url, headers={"X-TheRundown-Key": key, "User-Agent": "prop51-early-board"})
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    body, points = resp.read(), resp.headers.get("X-Datapoints")
            except urllib.error.HTTPError as exc:      # never echo the request (it carries the key)
                print(f"{sport} {date}: TheRundown answered HTTP {exc.code}")
                continue
            except (urllib.error.URLError, TimeoutError) as exc:
                print(f"{sport} {date}: no connection ({type(exc).__name__})")
                continue
            data = json.loads(body)
            data["_fetched_at"], data["_sport"], data["_date"] = now.strftime(FMT), sport, date
            with open(os.path.join(out_dir, f"{stamp}-{sport}-{date}.json"), "x") as fh:
                json.dump(data, fh)
            priced = sum(1 for e in data.get("events", []) if e.get("markets"))
            total += int(points) if points and points.isdigit() else 0
            print(f"{sport} {date}: {len(data.get('events', []))} games, {priced} with prices" + (f", {points} data points" if points else ""))
            time.sleep(1.1)                             # free plan: one request per second
    print(f"saved under {out_dir}" + (f"; {total} data points used (free plan: 20,000 a day)" if total else ""))


def parse_event(e):
    """{market: {side: {book: (line, american price)}}} with the app's sides: a = home / over, b = away / under."""
    home = next((t for t in e.get("teams", []) if t.get("is_home")), None)
    away = next((t for t in e.get("teams", []) if t.get("is_away")), None)
    if not home or not away:
        return None
    out = {}
    for m in e.get("markets") or []:
        market = MARKET.get(m.get("market_id"))
        if market is None or m.get("period_id") not in (0, None):
            continue
        for p in m.get("participants", []):
            if market == "total":
                side = "a" if p.get("name", "").lower() == "over" else "b" if p.get("name", "").lower() == "under" else None
            else:
                side = "a" if p.get("id") == home.get("team_id") else "b" if p.get("id") == away.get("team_id") else None
            if side is None:
                continue
            for ln in p.get("lines", []):
                try:
                    value = None if market == "winner" else float(ln.get("value"))
                except (TypeError, ValueError):
                    continue
                if market == "spread" and side == "b":
                    value = -value                       # the app stores a spread as the HOME handicap for both sides
                for book_id, pr in (ln.get("prices") or {}).items():
                    price = pr.get("price")
                    if book_id in BOOKS and isinstance(price, (int, float)) and abs(price) >= 100 and pr.get("is_main_line", True):
                        out.setdefault(market, {}).setdefault(side, {})[BOOKS[book_id]] = (value, int(price))
    return dict(sport=None, home=canon(home.get("abbreviation")), away=canon(away.get("abbreviation")), start=e.get("event_date"), markets=out)


def load_latest(root, now=None, max_age_hours=3):
    """Latest saved prices per game, from files fetched within max_age_hours: {(sport, home, away): parsed event}."""
    now = now or datetime.now(timezone.utc)
    games, newest = {}, None
    for path in sorted(glob.glob(os.path.join(root, "data", "odds-books", "*.json"))):
        try:
            data = json.load(open(path))
            fetched = datetime.strptime(data["_fetched_at"], FMT).replace(tzinfo=timezone.utc)
        except (ValueError, KeyError, OSError):
            continue
        if now - fetched > timedelta(hours=max_age_hours) or fetched > now:
            continue
        newest = max(newest or fetched, fetched)
        for e in data.get("events", []):
            ev = parse_event(e)
            if ev and ev["markets"]:
                ev["sport"], ev["fetched_at"] = data["_sport"], data["_fetched_at"]
                games[(data["_sport"], ev["home"], ev["away"], ev["start"][:13])] = ev   # later files replace earlier ones
    return games, newest


def match(games, sport, home, away, tipoff):
    """The saved prices of one game of the app: same sport and teams, start within three hours."""
    t = datetime.strptime(tipoff, FMT)
    best = None
    for (sp, h, a, _), ev in games.items():
        if sp == sport and h == canon(home) and a == canon(away):
            gap = abs((datetime.strptime(ev["start"][:19] + "Z", FMT) - t).total_seconds())
            if gap <= 3 * 3600 and (best is None or gap < best[0]):
                best = (gap, ev)
    return best[1] if best else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("fetch", "show"))
    a = ap.parse_args()
    root = root_dir()
    if a.command == "fetch":
        return fetch(root)
    games, newest = load_latest(root, max_age_hours=24)
    print(f"{len(games)} games with prices; newest file {newest}")
    for (sport, home, away, _), ev in sorted(games.items(), key=lambda kv: kv[1]["start"]):
        print(f"{ev['start']} {sport.upper()} {away} @ {home}: " + "; ".join(
            f"{m} " + ", ".join(f"{b} {'' if l is None else format(l, '+g') + ' '}{p:+d}" for b, (l, p) in sorted(sides.get('a', {}).items()))
            for m, sides in ev["markets"].items()))


if __name__ == "__main__":
    main()
