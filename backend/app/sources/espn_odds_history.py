"""Historical bookmaker lines per finished game from ESPN's core odds endpoint (free, no key; same provider as the
scoreboard, whose finished games carry no odds). One request per game returns every listed bookmaker with its opening and
closing moneyline, spread and total and their prices.

Research input only: raw responses are stored immutably (file name = event id + content digest) under
NBA_DATA_DIR/research/odds_history/<sport>, every attempt is appended to attempts.jsonl, and collection resumes where it
stopped. Nothing here writes to a store, forecasts or trains. The opening line's time is not published; "close" is the
book's last pre-start line as ESPN keeps it. Lines are validated per book (`parse`: both sides priced, implied
probabilities summing to OVERROUND), and consensus uses the median of the valid books (`consensus`)."""
import hashlib
import json
import os
import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from .. import config
from ..db import utc_now

VERSION = "espn-odds-history-1"
LEAGUE = {"nba": "basketball/nba", "nhl": "hockey/nhl", "nfl": "football/nfl"}               # site API path (scoreboard)
CORE = {"nba": "basketball/leagues/nba", "nhl": "hockey/leagues/nhl", "nfl": "football/leagues/nfl"}  # core API path (odds)
ODDS_URL = "https://sports.core.api.espn.com/v2/sports/{league}/events/{id}/competitions/{id}/odds"
SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/{league}/scoreboard"
OVERROUND = (1.0, 1.12)       # a two-way line whose implied probabilities sum outside this is stale, live or mislabelled
NOT_BOOKS = ("live", "accuscore", "numberfire", "consensus", "teamrankings", "betegy")  # in-play feeds, ESPN's own
# aggregate (it would double-count the books) and projection/rating sites are not bookmaker lines; matched by name
# because provider ids differ between sports
BOUNDS = {"nba": {"spread": 30, "total": (150, 300)}, "nhl": {"spread": 4, "total": (3, 10)},
          "nfl": {"spread": 30, "total": (20, 80)},
          None: {"spread": 30, "total": (1, 400)}}   # a value outside is a price written into the line field
OUTLIER = 0.15     # a book whose de-vigged probability is this far from the median of the other books (3+) is left out


def root(sport: str) -> Path:
    return config.data_dir() / "research" / "odds_history" / sport


def _get(url, params=None, timeout=20.):
    import httpx  # lazily: parsing and tests never need the network stack
    return httpx.get(url, params=params, timeout=timeout)


def _decimal(price) -> Optional[float]:
    """Decimal odds from an ESPN price object or number, preferring the exact American value."""
    if isinstance(price, dict):
        american = price.get("american") or price.get("alternateDisplayValue")
        try:
            a = float(str(american).replace("+", ""))
        except (TypeError, ValueError):
            a = None
        if a is not None and abs(a) >= 100:
            return 1 + (a / 100 if a > 0 else 100 / -a)
        price = price.get("decimal")
    try:
        d = float(price)
    except (TypeError, ValueError):
        return None
    return d if d > 1 else None


def _line(obj) -> Optional[float]:
    if not isinstance(obj, dict):
        return None
    for key in ("american", "value", "alternateDisplayValue"):
        if str(obj.get(key)).strip().upper() in ("PK", "EVEN", "PICK"):
            return 0.0
        try:
            return float(str(obj.get(key)).replace("+", ""))
        except (TypeError, ValueError):
            continue
    return None


def _valid(da, db) -> Optional[str]:
    if da is None or db is None:
        return "a side is not priced"
    s = 1 / da + 1 / db
    if not OVERROUND[0] <= s <= OVERROUND[1]:
        return f"implied probabilities sum to {s:.3f}"
    return None


def _bad_line(market, line, away_line, sport) -> Optional[str]:
    b = BOUNDS.get(sport, BOUNDS[None])
    if market == "spread":
        if abs(line) > b["spread"]:
            return f"spread {line} outside +/-{b['spread']} (a price in the line field)"
        if away_line is not None and away_line != -line:
            return f"away handicap {away_line} is not minus the home {line}"
    if market == "total" and not b["total"][0] <= line <= b["total"][1]:
        return f"total {line} outside {b['total']} (a price in the line field)"
    return None


def parse(body: bytes, sport: Optional[str] = None) -> list[dict]:
    """One row per bookmaker, phase (open/close) and market (moneyline/spread/total): line (home spread or total),
    decimal prices of side a (home / over) and side b (away / under), de-vigged probability of side a, and `invalid`
    (None when the line passes the checks)."""
    out = []
    for item in json.loads(body).get("items", []):
        prov = item.get("provider") or {}
        name = str(prov.get("name") or "")
        book = {"provider_id": str(prov.get("id")), "provider": name}
        excluded = next((f"not a pregame bookmaker ({w})" for w in NOT_BOOKS if w in name.lower()), None)
        home, away = item.get("homeTeamOdds") or {}, item.get("awayTeamOdds") or {}
        for phase in ("open", "close"):
            # Older games carry only "current", which for a finished game is the last line ESPN kept: used as the close
            # when no explicit close exists, and labelled so (`source`).
            src = phase if phase == "open" or home.get("close") or item.get("close") else "current"
            h, a, tot = home.get(src) or {}, away.get(src) or {}, item.get(src) or {}
            away_line = _line(a.get("pointSpread"))
            rows = [("moneyline", None, _decimal(h.get("moneyLine")), _decimal(a.get("moneyLine"))),
                    ("spread", _line(h.get("pointSpread")), _decimal(h.get("spread")), _decimal(a.get("spread"))),
                    ("total", _line(tot.get("total")), _decimal(tot.get("over")), _decimal(tot.get("under")))]
            for market, line, pa, pb in rows:
                if pa is None and pb is None and line is None:
                    continue
                bad = (excluded or ((market != "moneyline" and line is None) and "no line")
                       or (market != "moneyline" and _bad_line(market, line, away_line, sport)) or _valid(pa, pb))
                p = None if bad else (1 / pa) / (1 / pa + 1 / pb)
                out.append({**book, "phase": phase, "source": src, "market": market, "line": line, "price_a": pa, "price_b": pb,
                            "p_a": p, "invalid": bad})
    return out


def consensus_rows(rows: list[dict]) -> list[dict]:
    """`rows` with outliers marked invalid: per phase, market and line, a book whose de-vigged probability is more than
    OUTLIER from the median of the other valid books (when 2+ others exist; e.g. an opening line with the sides swapped)."""
    out = [dict(r) for r in rows]
    groups: dict = {}
    for r in out:
        if r["invalid"] is None:
            groups.setdefault((r["phase"], r["market"], r["line"]), []).append(r)
    for group in groups.values():
        for r in group:
            others = [o["p_a"] for o in group if o is not r]
            if len(others) >= 2 and abs(r["p_a"] - statistics.median(others)) > OUTLIER:
                r["flag"] = f"outlier: {r['p_a']:.3f} vs the other books' median {statistics.median(others):.3f}"
    for r in out:
        if r.pop("flag", None) is not None:
            r["invalid"] = "outlier vs the other books"
            r["p_a"] = None
    return out


def consensus(rows: list[dict], phase: str, market: str) -> Optional[dict]:
    """Median de-vigged probability of side a over the valid, non-outlier books, at the most common line (spread/total;
    a tie goes to the line nearest the median of all valid lines); the books at other lines are left out, not adjusted.
    `best_price_*` is the best price among those books (line shopping; optimistic by construction)."""
    rows = consensus_rows(rows)
    ok = [r for r in rows if r["phase"] == phase and r["market"] == market and r["invalid"] is None]
    if not ok:
        return None
    if market != "moneyline":
        lines = [r["line"] for r in ok]
        mid = statistics.median(lines)
        line = max(set(lines), key=lambda x: (lines.count(x), -abs(x - mid), -x))
        ok = [r for r in ok if r["line"] == line]
    else:
        line = None
    return {"line": line, "p_a": statistics.median(r["p_a"] for r in ok), "books": len(ok),
            "best_price_a": max(r["price_a"] for r in ok), "best_price_b": max(r["price_b"] for r in ok)}


def _write_once(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def stored(sport: str) -> dict:
    """event id -> stored raw file, and event ids recorded as not available (404)."""
    d = root(sport)
    files = {p.name.split("-")[0]: p for p in d.glob("[0-9]*-*.json")} if d.is_dir() else {}
    missing = set()
    log = d / "attempts.jsonl"
    if log.is_file():
        for line in log.read_text().splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue                       # a torn last line from an interrupted run
            if rec.get("outcome") == "not_available":
                missing.add(rec["event"])
    return {"files": files, "not_available": missing - set(files)}


def collect(sport: str, events: list[str], *, get: Callable = _get, delay: float = 0.6, limit: Optional[int] = None,
            log: Callable = print) -> dict:
    """Fetches the odds of each event not already stored or recorded as not available, newest first as given."""
    d = root(sport)
    d.mkdir(parents=True, exist_ok=True)
    have = stored(sport)
    todo = [e for e in events if e not in have["files"] and e not in have["not_available"]]
    counts = {"stored": 0, "not_available": 0, "failed": 0, "already": len(events) - len(todo)}
    if limit is not None:
        todo = todo[:limit]
    log_path = d / "attempts.jsonl"
    if log_path.is_file() and log_path.stat().st_size and not log_path.read_bytes().endswith(b"\n"):
        with log_path.open("a") as f:      # an interrupted run left a torn last line: end it so the next record stands alone
            f.write("\n")
    with log_path.open("a") as attempts:
        for i, event in enumerate(todo):
            url = ODDS_URL.format(league=CORE[sport], id=event)
            rec = {"event": event, "url": url, "requested_at": utc_now(), "version": VERSION}
            try:
                resp = get(url)
                rec["http_status"] = resp.status_code
                if resp.status_code == 200:
                    body = resp.content
                    json.loads(body)                   # never store an unparseable body
                    sha = hashlib.sha256(body).hexdigest()
                    _write_once(d / f"{event}-{sha[:16]}.json", body)
                    rec.update(outcome="stored", sha256=sha, bytes=len(body))
                elif resp.status_code == 404:
                    rec["outcome"] = "not_available"
                else:
                    rec["outcome"] = "failed"
            except Exception as exc:                   # recorded; the next run retries it
                rec.update(outcome="failed", error=f"{type(exc).__name__}: {exc}")
            counts[rec["outcome"]] += 1
            attempts.write(json.dumps(rec) + "\n")
            attempts.flush()
            if (i + 1) % 200 == 0:
                log(f"{sport}: {i + 1}/{len(todo)} {counts}")
            time.sleep(delay)
    return counts


def _hours(a: str, b: str) -> float:
    t = lambda x: datetime.fromisoformat(x.replace("Z", "+00:00"))  # noqa: E731
    return (t(a) - t(b)).total_seconds() / 3600


def nhl_event_map(conn, *, game_ids=None, get: Callable = _get, delay: float = 0.6, log: Callable = print) -> dict:
    """NHL game ids (NHL API) -> ESPN event ids, matched by ET-adjacent date and both team names (team_key). Scoreboards
    are stored raw like the odds; the map is rewritten after each date so an interrupted run resumes."""
    from ..nhl_player_ratings import team_key
    d = root("nhl")
    (d / "scoreboards").mkdir(parents=True, exist_ok=True)
    path = d / "event-map.json"
    mapping = json.loads(path.read_text()) if path.is_file() else {}
    games = conn.execute("SELECT g.game_id, g.tipoff_at, h.name, a.name FROM games g JOIN teams h ON h.team_id = g.home_team_id "
                         "JOIN teams a ON a.team_id = g.away_team_id WHERE g.status = 'final' ORDER BY g.tipoff_at DESC").fetchall()
    by_day: dict = {}
    for gid, tip, hn, an in games:
        if gid in mapping or (game_ids is not None and gid not in game_ids):
            continue
        day = (datetime.fromisoformat(tip.replace("Z", "+00:00")) - timedelta(hours=10)).strftime("%Y%m%d")  # US date
        by_day.setdefault(day, []).append((gid, tip, team_key(hn), team_key(an)))
    unmatched = failed_days = 0
    for day, wanted in by_day.items():
        sb = d / "scoreboards" / f"{day}.json"
        if not sb.is_file():
            try:
                resp = get(SCOREBOARD_URL.format(league=LEAGUE["nhl"]), params={"dates": day})
            except Exception as exc:               # a timeout skips this date; the next run asks again
                log(f"nhl scoreboard {day}: {type(exc).__name__}: {exc}")
                failed_days += 1
                time.sleep(delay)
                continue
            if resp.status_code != 200:
                log(f"nhl scoreboard {day}: HTTP {resp.status_code}")
                failed_days += 1
                time.sleep(delay)
                continue
            _write_once(sb, resp.content)
            time.sleep(delay)
        events: dict = {}
        for e in json.loads(sb.read_bytes()).get("events", []):
            teams = {c["homeAway"]: team_key(c["team"]["displayName"]) for c in e["competitions"][0]["competitors"]}
            events.setdefault((teams.get("home"), teams.get("away")), []).append((e.get("date"), e["id"]))
        for gid, tip, hk, ak in wanted:
            # The same teams can meet twice in a day (split-squad preseason): the event must start within 3 h of the
            # game, and exactly one may; otherwise the game stays unmatched rather than borrowing another game's odds.
            near = [i for d_, i in events.get((hk, ak), []) if d_ is None or abs(_hours(d_, tip)) <= 3]
            if len(near) == 1:
                mapping[gid] = near[0]
            else:
                unmatched += 1
        _write_once(path, json.dumps(mapping, sort_keys=True).encode())
    return {"mapped": len(mapping), "unmatched": unmatched, "failed_days": failed_days}
