#!/usr/bin/env python3
"""Closing price check (owner request, Nov 10): for every ticket leg and every game card's recommended pick, was the price
at pick time better or worse than the last stored price before the game started?

Facts only. Once a game has started, one row per pick is appended to <root>/data/closing-value/records.jsonl and never
changed (a pick already recorded is skipped on later runs). It compares the stored DraftKings quotes (the app's own
market_quotes, read from copies of the stores) and, when TheRundown prices were saved around both moments, the same pick
on the same line at DraftKings, FanDuel and BetMGM (app.rundown_odds, files under data/odds-books). It never fetches,
sends, trains or changes a forecast. "Better" means the bettor got a better number at pick time than at the close: a
better line, or the same line at a higher payout. It is not a profit claim; scripts/value_audit.py reports the counts.

    PYTHONPATH=backend backend/.venv/bin/python scripts/closing_value.py --root <live root>
"""
import argparse, json, os, re, sqlite3, tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import rundown_odds
from app.coverage import pick_side

FMT = "%Y-%m-%dT%H:%M:%SZ"
SPORTS = ("nba", "nhl", "nfl")
PRICED = ("winner", "spread", "total")
VERSION = "closing-value-v1"


def ts(s): return datetime.strptime(s, FMT).replace(tzinfo=timezone.utc)
def dec(a): return 1 + (a / 100 if a > 0 else 100 / -a)


def copy_db(src, dest):
    out = os.path.join(dest, os.path.basename(src))
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=60)
    d = sqlite3.connect(out)
    s.backup(d); d.close(); s.close()
    c = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def quote_at(conn, game_id, market, at, before=False):
    """The latest stored DraftKings quote (both sides) at or before `at` (strictly before when before=True)."""
    op = "<" if before else "<="
    return conn.execute(f"SELECT * FROM market_quotes WHERE game_id=? AND market=? AND price_label='current' AND price_a IS NOT NULL "
                        f"AND price_b IS NOT NULL AND retrieved_at{op}? ORDER BY retrieved_at DESC, quote_id DESC LIMIT 1",
                        (game_id, market, at)).fetchone()


def margin_free(q, side):
    ia, ib = 1 / dec(q["price_a"]), 1 / dec(q["price_b"])
    return (ia if side == "a" else ib) / (ia + ib)


def compare(market, side, pick_line, pick_price, close_line, close_price):
    """'better' / 'worse' / 'same' for the pick-time number against the close, from the bettor's side.
    Spread lines are the HOME handicap (side a = home); total side a = over."""
    if market != "winner" and pick_line != close_line:
        if market == "spread":
            pick_better = pick_line > close_line if side == "a" else pick_line < close_line
        else:
            pick_better = pick_line < close_line if side == "a" else pick_line > close_line
        return "better" if pick_better else "worse"
    a, b = dec(pick_price), dec(close_price)
    return "better" if a > b + 1e-9 else "worse" if a < b - 1e-9 else "same"


def books_at(sport, matchup, tipoff, market, side, line, now, odds_dir):
    """Prices of the same pick on the same line at the three books from files saved within three hours before `now`."""
    pick = dict(market=market, matchup=matchup, tipoff=tipoff, percentage_history={"side": side},
                quote_check=dict(line_at_forecast=line, line_source="bookmaker", line_moved=False))
    found = rundown_odds.comparison(sport, pick, rundown_odds.read_cache(now, odds_dir), now)
    return dict(status=found["status"], checked_at=found["checked_at"], prices=found["prices"])


def row(kind, key, sport, conn, game, market, side, pick_at, pick_quote, odds_dir, extra):
    """One record. pick_quote is the stored quote the pick was shown with (None: none stored at pick time)."""
    teams = {r["team_id"]: r["abbr"] for r in conn.execute("SELECT team_id, abbr FROM teams")}
    matchup = f"{teams.get(game['away_team_id'], '?')} @ {teams.get(game['home_team_id'], '?')}"
    close = quote_at(conn, game["game_id"], market, game["tipoff_at"], before=True)
    out = dict(version=VERSION, kind=kind, key=key, sport=sport, game_id=game["game_id"], matchup=matchup, tipoff=game["tipoff_at"],
               market=market, side=side, pick_at=pick_at, **extra)
    if pick_quote is None or close is None:
        return dict(out, status="no_pick_price" if pick_quote is None else "no_closing_price")
    pick_price = pick_quote["price_a"] if side == "a" else pick_quote["price_b"]
    close_price = close["price_a"] if side == "a" else close["price_b"]
    pick_line = None if market == "winner" else pick_quote["line"]
    close_line = None if market == "winner" else close["line"]
    out.update(status="recorded", pick_quote_at=pick_quote["retrieved_at"], pick_line=pick_line, pick_price=pick_price,
               close_quote_at=close["retrieved_at"], close_line=close_line, close_price=close_price,
               close_minutes_before_start=round((ts(game["tipoff_at"]) - ts(close["retrieved_at"])).total_seconds() / 60, 1),
               pick_market_prob=round(margin_free(pick_quote, side), 4), close_market_prob=round(margin_free(close, side), 4),
               draftkings=compare(market, side, pick_line, pick_price, close_line, close_price))
    tip = ts(game["tipoff_at"])
    out["books_at_pick"] = books_at(sport, matchup, game["tipoff_at"], market, side, pick_line, ts(pick_at), odds_dir)
    out["books_at_close"] = books_at(sport, matchup, game["tipoff_at"], market, side, pick_line, tip - timedelta(seconds=1), odds_dir)
    both = sorted(set(out["books_at_pick"]["prices"]) & set(out["books_at_close"]["prices"]))
    out["books"] = {b: compare(market, side, pick_line, out["books_at_pick"]["prices"][b], pick_line, out["books_at_close"]["prices"][b]) for b in both}
    return out


def ticket_legs(ledger):
    """(key, sport, forecast_id, side, market, shown_quote_time, sent_at) for every leg of every sent ticket batch."""
    for b in ledger.execute("SELECT batch_key, created_at, payload FROM ticket_batches ORDER BY created_at"):
        for t in json.loads(b["payload"]).get("resolved") or []:
            for leg in t.get("legs") or []:
                side = (leg.get("percentage_history") or {}).get("side")
                q = leg.get("quote_check") or {}
                yield (f"ticket:{t['ticket_id']}:{leg['sport']}:{leg['forecast_id']}", leg["sport"], leg["forecast_id"], side,
                       leg["market"], q.get("retrieved_at") if q.get("price_now") is not None else None, b["created_at"], t["ticket_id"])


def card_pick(text):
    """(sport, away, home, pick label) of a live game card, else None."""
    lines = text.split("\n")
    if len(lines) < 3 or not lines[0].startswith("<b>PROP51 PICKS · ") or "<b>Recommended pick</b>" not in lines:
        return None
    clean = lambda s: re.sub(r"<[^>]+>", "", s).strip()
    sport = clean(lines[0]).split("·")[-1].strip().lower()
    matchup = clean(lines[1])
    if " @ " not in matchup:
        return None
    away, home = matchup.split(" @ ")
    return sport, away.strip(), home.strip(), clean(lines[lines.index("<b>Recommended pick</b>") + 1])


def parse_label(label, home, away):
    """(market, side, line as stored) from a card pick label: 'OKC win', 'OKC -1.5', 'Over 226.5'. None otherwise."""
    m = re.fullmatch(r"(Over|Under) (\d+(?:\.\d+)?)", label)
    if m:
        return "total", "a" if m.group(1) == "Over" else "b", float(m.group(2))
    m = re.fullmatch(r"(\S+) win", label)
    if m and m.group(1) in (home, away):
        return "winner", "a" if m.group(1) == home else "b", None
    m = re.fullmatch(r"(\S+) ([+-]\d+(?:\.\d+)?)", label)
    if m and m.group(1) in (home, away):
        value = float(m.group(2))
        return "spread", "a" if m.group(1) == home else "b", value if m.group(1) == home else -value
    return None


def collect(root, ledger_path, dest, now, done):
    odds_dir = Path(root) / "data" / "odds-books"
    ledger = copy_db(ledger_path, dest)
    stores = {s: copy_db(os.path.join(root, "data", s + ".sqlite"), dest) for s in SPORTS if os.path.exists(os.path.join(root, "data", s + ".sqlite"))}
    stamp = now.strftime(FMT)
    rows = []

    def started(conn, game_id):
        g = conn.execute("SELECT * FROM games WHERE game_id=?", (game_id,)).fetchone()
        return g if g is not None and g["tipoff_at"] <= stamp else None

    for key, sport, fid, side, market, shown_at, sent_at, ticket_id in ticket_legs(ledger):
        if key in done or sport not in stores or market not in PRICED:
            continue
        conn = stores[sport]
        f = conn.execute("SELECT game_id FROM game_forecasts WHERE forecast_id=?", (fid,)).fetchone()
        g = f and started(conn, f["game_id"])
        if not g:
            continue
        # the price printed on the ticket; older tickets did not keep it: the latest stored quote when the ticket was sent
        basis = "price shown on the ticket" if shown_at else "stored quote at send time"
        q = quote_at(conn, g["game_id"], market, shown_at or sent_at)
        if side not in ("a", "b"):
            f = conn.execute("SELECT * FROM game_forecasts WHERE forecast_id=?", (fid,)).fetchone()
            side = pick_side(market, (f["p_a"], f["p_mid"], f["p_b"]))
        rows.append(row("ticket_leg", key, sport, conn, g, market, side, sent_at, q, odds_dir,
                        dict(ticket_id=ticket_id, forecast_id=fid, pick_price_basis=basis)))
        done.add(key)

    for d in ledger.execute("SELECT delivery_key, text, attempted_at FROM deliveries WHERE status='sent' AND length(delivery_key)=64 ORDER BY attempted_at"):
        key = f"card:{d['delivery_key']}"
        parsed = card_pick(d["text"])
        if key in done or parsed is None or parsed[0] not in stores:
            continue
        sport, away, home, label = parsed
        conn = stores[sport]
        ids = {r["abbr"]: r["team_id"] for r in conn.execute("SELECT team_id, abbr FROM teams")}
        g = conn.execute("SELECT * FROM games WHERE home_team_id=? AND away_team_id=? AND tipoff_at>? ORDER BY tipoff_at LIMIT 1",
                         (ids.get(home), ids.get(away), d["attempted_at"])).fetchone()
        if g is None or g["tipoff_at"] > stamp:
            continue
        pick = parse_label(label, home, away)
        if pick is None:   # team totals and three-way picks have no stored sportsbook price to compare
            rows.append(dict(version=VERSION, kind="card_pick", key=key, sport=sport, game_id=g["game_id"], tipoff=g["tipoff_at"],
                             pick_at=d["attempted_at"], label=label, status="not_priced_market"))
            done.add(key)
            continue
        market, side, line = pick
        q = quote_at(conn, g["game_id"], market, d["attempted_at"])
        if q is not None and market != "winner" and q["line"] != line:
            q = None    # the card showed a line the latest stored quote no longer had: no price to compare at that line
        rows.append(row("card_pick", key, sport, conn, g, market, side, d["attempted_at"], q, odds_dir,
                        dict(label=label, pick_price_basis="stored quote when the card was sent")))
        done.add(key)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--ledger", default=None, help="default: <root>/.telegram-delivery.sqlite")
    ap.add_argument("--out", default=None, help="default: <root>/data/closing-value/records.jsonl")
    a = ap.parse_args()
    out = Path(a.out or os.path.join(a.root, "data", "closing-value", "records.jsonl"))
    out.parent.mkdir(parents=True, exist_ok=True)
    done = {json.loads(x)["key"] for x in out.read_text().splitlines() if x.strip()} if out.exists() else set()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with tempfile.TemporaryDirectory(prefix="closing-value-") as dest:
        os.environ["NBA_DATA_DIR"] = dest       # app code never looks at the live data folder from here
        rows = collect(a.root, a.ledger or os.path.join(a.root, ".telegram-delivery.sqlite"), dest, now, done)
    with out.open("a") as fh:                     # append-only: earlier rows are never rewritten
        for r in rows:
            fh.write(json.dumps(dict(r, recorded_at=now.strftime(FMT)), sort_keys=True) + "\n")
    counts = {}
    for r in rows:
        counts[(r["kind"], r.get("draftkings") or r["status"])] = counts.get((r["kind"], r.get("draftkings") or r["status"]), 0) + 1
    print(json.dumps({"appended": len(rows), "by_kind": {f"{k} {v}": n for (k, v), n in sorted(counts.items())}, "file": str(out)}))


if __name__ == "__main__":
    main()
