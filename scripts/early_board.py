#!/usr/bin/env python3
"""Early board (paper only): the app's own sealed models, run a day ahead against the first DraftKings line.

Why: the two-hour forecasts meet a line that has stopped moving (94% of lines unchanged after the forecast). A read-only
check on 9 Nov 2026 found that, measured from the first price the store saw (about 31 hours before the start), the market
later moved toward the model's side more often than away (NBA totals: 62% toward, 26% away, 106 picks in one month;
docs/evidence/early-board/README.md). That is a lead worth testing forward, not a demonstrated profit.

Read-only: copies the stores with the SQLite backup API into a temporary folder (removed afterwards) and reads the
copies. Writes only its own write-once boards and grade files under <root>/data/early-board/. Sends nothing (no Telegram,
no ticket), changes no forecast, trains nothing. Prices of other sportsbooks come from the files the scheduled price job
already saved (app.rundown_odds, data/odds-books); this script never fetches.

    PYTHONPATH=backend backend/.venv/bin/python scripts/early_board.py --root <live root>            # board, games 3-40 h ahead
    PYTHONPATH=backend backend/.venv/bin/python scripts/early_board.py --root <live root> --grade    # grade earlier boards
"""
import argparse, copy, glob, json, math, os, sqlite3, sys, tempfile, traceback
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from app import coverage, markets, rundown_odds

FMT = "%Y-%m-%dT%H:%M:%SZ"
TORONTO = ZoneInfo("America/Toronto")
SPORTS = ("nba", "nhl")
MARKETS = ("winner", "spread", "total")


def ts(s): return datetime.strptime(s, FMT).replace(tzinfo=timezone.utc)
def dec(a): return 1 + (a / 100 if a > 0 else 100 / -a)


def copy_store(root, sport, dest):
    out = os.path.join(dest, sport + ".sqlite")
    src = sqlite3.connect(f"file:{os.path.join(root, 'data', sport + '.sqlite')}?mode=ro", uri=True, timeout=60)
    dst = sqlite3.connect(out)
    src.backup(dst); dst.close(); src.close()
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def label(market, side, line, home, away):
    if market == "winner":
        return f"{home if side == 'a' else away} win"
    if market == "spread":
        return f"{home} {line:+g}" if side == "a" else f"{away} {-line:+g}"
    return f"{'Over' if side == 'a' else 'Under'} {line:g}"


def better(market, side, x, y):
    """True when (line, price) x is a better deal than y for someone taking this side."""
    if market != "winner" and x[0] != y[0]:
        higher_is_better = (market == "spread") == (side == "a") if market == "spread" else side == "b"
        return (x[0] > y[0]) == higher_is_better
    return dec(x[1]) > dec(y[1])


def book_offers(sport, home, away, tipoff, market, side, line, now, cache):
    """{book: (line, price)} for this side at TheRundown's three books: the exact line plus the books' other main lines."""
    pick = dict(market=market, matchup=f"{away} @ {home}", tipoff=tipoff, percentage_history={"side": side},
                quote_check=dict(line_at_forecast=line, line_source="bookmaker", line_moved=False))
    found = rundown_odds.comparison(sport, pick, cache, now)
    offers = {book: (line if market != "winner" else None, price) for book, price in found["prices"].items()}
    for other in found["other_lines"]:
        offers.setdefault(other["book"], (other["line"], other["price"]))
    return offers, found["checked_at"]


def board(root, now, lo_h, hi_h, dest):
    stamp = now.strftime(FMT)
    picks, waiting = [], []
    cache = rundown_odds.read_cache(now, Path(root) / "data" / "odds-books")
    for sport in SPORTS:
        try:
            sport_board(root, sport, now, lo_h, hi_h, dest, cache, picks, waiting)
        except Exception as exc:     # one sport failing never stops the other's board (review of 6d2715f), but it is a failure:
            traceback.print_exc()    # full traceback in the log, and main() exits non-zero after writing the board
            waiting.append(dict(sport=sport, reason=f"board failed: {type(exc).__name__}", failed=True))
    return picks, waiting


def sport_board(root, sport, now, lo_h, hi_h, dest, cache, picks, waiting):
    stamp = now.strftime(FMT)
    conn = copy_store(root, sport, dest)
    try:
        direct = coverage._Direct(conn, stamp)
        if direct.stale or direct.market_model is None:
            waiting.append(dict(sport=sport, reason=direct.stale or direct.market_error))
            return
        teams = {r["team_id"]: r["abbr"] for r in conn.execute("SELECT team_id, abbr FROM teams")}
        games = conn.execute("SELECT * FROM games WHERE status='scheduled' AND season_type IN ('regular','play_in','postseason') AND tipoff_at>? AND tipoff_at<=? "
                             "ORDER BY tipoff_at", ((now + timedelta(hours=lo_h)).strftime(FMT), (now + timedelta(hours=hi_h)).strftime(FMT))).fetchall()
        for g in games:
            home, away = teams.get(g["home_team_id"], "?"), teams.get(g["away_team_id"], "?")
            reason = direct.guard(g)
            if reason:     # a team still has a game to finish before this one: the model will change, so no early lean yet
                waiting.append(dict(sport=sport, game=f"{away} @ {home}", tipoff=g["tipoff_at"], reason="a team plays again before this game"))
                continue
            game = direct.eng.pregame(g)
            dist = copy.deepcopy(direct.market_model).distribution(game)
            p_home = copy.deepcopy(direct.elo).predict(game)
            for market in MARKETS:
                q = conn.execute("SELECT * FROM market_quotes WHERE game_id=? AND market=? AND price_label='current' AND price_a IS NOT NULL AND price_b IS NOT NULL "
                                 "AND retrieved_at<=? ORDER BY retrieved_at DESC, quote_id DESC LIMIT 1", (g["game_id"], market, stamp)).fetchone()
                if q is None or (now - ts(q["retrieved_at"])) > timedelta(hours=2):
                    continue
                first = conn.execute("SELECT line, retrieved_at FROM market_quotes WHERE game_id=? AND market=? AND price_label='current' "
                                     "AND price_a IS NOT NULL AND price_b IS NOT NULL AND retrieved_at<=? ORDER BY retrieved_at, quote_id LIMIT 1",
                                     (g["game_id"], market, stamp)).fetchone()
                if market == "winner":
                    pa, pb = p_home, 1 - p_home
                else:
                    a, _mid, b = dist.market(market, q["line"])
                    pa, pb = a / (a + b), b / (a + b)
                side = "a" if pa >= pb else "b"
                price = q["price_a"] if side == "a" else q["price_b"]
                ia, ib = 1 / dec(q["price_a"]), 1 / dec(q["price_b"])
                fair = None if market == "winner" else round(-dist.median("spread") if market == "spread" else dist.median("total"), 1)
                picks.append(dict(made_at=stamp, sport=sport, game_id=g["game_id"], matchup=f"{away} @ {home}", tipoff=g["tipoff_at"], market=market, side=side,
                                  pick=label(market, side, q["line"], home, away), line=q["line"], price=price, quote_id=q["quote_id"],
                                  quote_retrieved_at=q["retrieved_at"], model_prob=round(max(pa, pb), 4), break_even=round(1 / dec(price), 4),
                                  market_prob=round((ia if side == "a" else ib) / (ia + ib), 4), edge=round(max(pa, pb) - 1 / dec(price), 4),
                                  model_line=fair, hours_ahead=round((ts(g["tipoff_at"]) - now).total_seconds() / 3600, 1),
                                  model=direct.winner_version if market == "winner" else direct.market_version,
                                  first_line=first["line"], first_quote_at=first["retrieved_at"],
                                  hours_since_first_line=round((now - ts(first["retrieved_at"])).total_seconds() / 3600, 1)))
                offers, books_at = book_offers(sport, home, away, g["tipoff_at"], market, side, q["line"], now, cache)
                if offers:
                    offers["DraftKings"] = (q["line"], price)   # the app's own DraftKings quote is fresher than the relayed one
                    best = None
                    for book, offer in sorted(offers.items()):
                        if best is None or better(market, side, offer, best[1]):
                            best = (book, offer)
                    lines = sorted({o[0] for o in offers.values() if o[0] is not None})
                    picks[-1].update(books={b: list(o) for b, o in offers.items()}, books_fetched_at=books_at, best_book=best[0],
                                     best_line=best[1][0], best_price=best[1][1], books_disagree_points=round(lines[-1] - lines[0], 1) if len(lines) > 1 else 0.0)
    finally:
        conn.close()


def best_text(p):
    if "books" not in p:
        return "—"
    line = "" if p["best_line"] is None else label(p["market"], p["side"], p["best_line"], *reversed(p["matchup"].split(" @ "))) + " "
    same = p["best_book"] == "DraftKings" or (p["best_line"], p["best_price"]) == (p["line"], p["price"])
    return f"{'DraftKings is best' if same else '**' + p['best_book'] + '** ' + line + format(p['best_price'], '+d')}"


def write_board(picks, waiting, out_dir, now):
    os.makedirs(out_dir, exist_ok=True)
    day = now.astimezone(TORONTO).strftime("%Y-%m-%d-%H%M")
    log = os.path.join(out_dir, f"board-{day}.jsonl")
    with open(log, "x") as fh:                       # never overwrites an earlier board
        for p in picks:
            fh.write(json.dumps(p, sort_keys=True) + "\n")
    leans = sorted((p for p in picks if p["edge"] > 0), key=lambda p: -p["edge"])
    L = [f"# Early board — made {now.astimezone(TORONTO):%a %b %d, %I:%M %p %Z}", "",
         "**Paper only.** The app's own models priced against the current DraftKings line, a day ahead instead of two hours ahead. "
         "No pick here is a demonstrated betting opportunity: this board exists to be graded against the closing line (`--grade`).", "",
         f"{len(picks)} game-markets priced; {len(leans)} where the model's probability is above the price's break-even.", ""]
    if leans:
        L += ["| Start (Toronto) | Sport | Game | Pick | DraftKings | Model | Needs | Gap | Model's own line | Best of the three books |", "|---|---|---|---|---|---|---|---|---|---|"]
        for p in leans:
            L.append(f"| {ts(p['tipoff']).astimezone(TORONTO):%a %I:%M %p} | {p['sport'].upper()} | {p['matchup']} | **{p['pick']}** | {p['price']:+d} | "
                     f"{p['model_prob']:.1%} | {p['break_even']:.1%} | {100 * p['edge']:+.1f} pts | "
                     + ('—' if p['model_line'] is None else f"{p['matchup'].split(' @ ')[1]} {p['model_line']:+g}" if p['market'] == 'spread' else f"{p['model_line']:g}") + " | " + best_text(p) + " |")
    split = sorted((p for p in picks if p.get("books_disagree_points", 0) >= 1), key=lambda p: -p["books_disagree_points"])
    if any("books" in p for p in picks):
        L += ["", "## Where the three books disagree by a point or more", ""]
        if split:
            L += ["| Game | Market | " + " | ".join(sorted(rundown_odds.BOOKS.values())) + " | Model leans |", "|---|---|" + "---|" * (len(rundown_odds.BOOKS) + 1)]
            for p in split:
                cells = [("—" if b not in p["books"] else f"{label(p['market'], p['side'], p['books'][b][0], *reversed(p['matchup'].split(' @ ')))} {p['books'][b][1]:+d}")
                         for b in sorted(rundown_odds.BOOKS.values())]
                L.append(f"| {p['sport'].upper()} {p['matchup']} | {p['market']} | " + " | ".join(cells) + f" | {p['pick']} ({p['model_prob']:.0%}) |")
            L += ["", "The same bet at a different number. Whatever you bet, take the best number; a full point on a spread or total is worth more than most model gaps."]
        else:
            L.append("None right now: the three books show the same lines.")
    else:
        L += ["", "Other sportsbooks: no prices saved in the last three hours (the scheduled price job fills data/odds-books)."]
    if waiting:
        L += ["", "Not priced yet: " + "; ".join(f"{w.get('game', w['sport'].upper())} ({w['reason']})" for w in waiting) + "."]
    L += ["", "\"Needs\" is the win rate the price requires to break even. \"Gap\" is model minus that; the model has been wrong against DraftKings often "
          "enough (docs/VALUE_AUDIT.md) that a gap is a lean to test, not an edge."]
    md = os.path.join(out_dir, f"board-{day}.md")
    with open(md, "x") as fh:
        fh.write("\n".join(L) + "\n")
    return log, md, leans


def first_entries(out_dir):
    """Each game-market once, from the EARLIEST board that priced it: the test is the first posted line, and a game that
    appears on two boards must not count twice."""
    first = {}
    for path in sorted(glob.glob(os.path.join(out_dir, "board-*.jsonl"))):
        for line in open(path):
            if not line.strip():
                continue
            p = json.loads(line)
            key = (p["sport"], p["game_id"], p["market"])
            if key not in first or p["made_at"] < first[key]["made_at"]:
                first[key] = p
    return [first[k] for k in sorted(first)]


# Fixed before any grade was read (review of 6d2715f): a cell needs this many lines that MOVED (toward + away) before its
# toward-rate is called readable. 136 moved lines give a two-sided 5% sign test 80% power to tell 62% toward (the Nov 9
# lead) from 50%: n = (1.96 + 0.84)^2 * 0.25 / 0.12^2. It is a reading threshold, not a promise of profit.
MIN_MOVED = 136


def sign_test(toward, away):
    """Two-sided exact binomial p-value of `toward` out of toward+away moved lines against 50%."""
    n = toward + away
    if n == 0:
        return None
    k = max(toward, away)
    tail = sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n
    return round(min(1.0, 2 * tail), 4)


def direction(r):
    """+1 when the market moved toward the pick by the close, -1 away, 0 unchanged."""
    v = r["market_prob_change"] if r["market"] == "winner" else r["line_move_toward_pick"]
    return (v > 0) - (v < 0)


def grade(root, out_dir, dest, now):
    conns = {s: copy_store(root, s, dest) for s in SPORTS}
    rows, skipped = [], Counter()
    for p in first_entries(out_dir):
        c = conns[p["sport"]]
        g = c.execute("SELECT * FROM games WHERE game_id=?", (p["game_id"],)).fetchone()
        if g is None:
            skipped["game missing from the store"] += 1
            continue
        if g["tipoff_at"] != p["tipoff"]:
            skipped["start time changed after the board"] += 1
            continue
        if g["tipoff_at"] > now.strftime(FMT):
            skipped["not started yet"] += 1
            continue
        cl = c.execute("SELECT * FROM market_quotes WHERE game_id=? AND market=? AND price_label='current' AND price_a IS NOT NULL AND price_b IS NOT NULL "
                       "AND retrieved_at<? ORDER BY retrieved_at DESC, quote_id DESC LIMIT 1", (p["game_id"], p["market"], g["tipoff_at"])).fetchone()
        if cl is None or (ts(g["tipoff_at"]) - ts(cl["retrieved_at"])) > timedelta(minutes=30):
            skipped["no stored price in the last 30 minutes before the start"] += 1
            continue
        ia, ib = 1 / dec(cl["price_a"]), 1 / dec(cl["price_b"])
        close_prob = (ia if p["side"] == "a" else ib) / (ia + ib)
        move = None
        if p["market"] != "winner":
            d = cl["line"] - p["line"]
            move = (-d if p["side"] == "a" else d) if p["market"] == "spread" else (d if p["side"] == "a" else -d)
        real = markets.settle(p["market"], p["line"], status=g["status"], home=g["home_score"], away=g["away_score"])
        ret = None if real not in ("a", "b", "mid") else 0.0 if real == "mid" else (dec(p["price"]) - 1 if real == p["side"] else -1.0)
        rows.append(dict(p, close_line=cl["line"], line_move_toward_pick=move, close_market_prob=round(close_prob, 4),
                         market_prob_change=round(close_prob - p["market_prob"], 4), result=real if real in ("a", "b", "mid") else None, unit_return=ret))
    leans = [r for r in rows if r["edge"] > 0]
    summary = dict(graded_at=now.strftime(FMT), graded=len(rows), leans=len(leans), skipped=dict(skipped), min_moved=MIN_MOVED, by_sport_market={})
    lines = [f"graded {len(rows)} logged game-markets (first board only) with a closing quote; {len(leans)} were leans (model above break-even)"]
    if skipped:
        lines.append("  not graded: " + "; ".join(f"{n} {why}" for why, n in sorted(skipped.items())))
    readable = []
    for sport in SPORTS:
        for m in MARKETS:
            rs = [r for r in leans if r["market"] == m and r["sport"] == sport]
            base = [r for r in rows if r["market"] == m and r["sport"] == sport and r["edge"] <= 0]
            if not rs:
                continue
            done = [r for r in rs if r["unit_return"] is not None]
            tow, aw = sum(direction(r) > 0 for r in rs), sum(direction(r) < 0 for r in rs)
            btow, baw = sum(direction(r) > 0 for r in base), sum(direction(r) < 0 for r in base)
            ok = tow + aw >= MIN_MOVED
            readable.append(ok)
            entry = dict(leans=len(rs), toward=tow, away=aw, unchanged=len(rs) - tow - aw, sign_test_p=sign_test(tow, aw),
                         baseline_not_leans=dict(n=len(base), toward=btow, away=baw, unchanged=len(base) - btow - baw),
                         settled=len(done), paper_return_units=round(sum(r["unit_return"] for r in done), 2),
                         readable=ok)
            if m == "winner":
                entry.update(mean_market_prob_change_toward_pick=round(sum(r["market_prob_change"] for r in rs) / len(rs), 4))
            what = "market probability" if m == "winner" else "line"
            lines.append(f"  {sport} {m}: {len(rs)} leans; {what} moved toward {tow}, away {aw}, unchanged {len(rs) - tow - aw} "
                         f"(sign test p={entry['sign_test_p']}); other priced picks: toward {btow}, away {baw} of {len(base)}; "
                         f"settled {len(done)}, paper return {entry['paper_return_units']:+.2f} units; "
                         + ("readable" if ok else f"too small ({tow + aw} of {MIN_MOVED} moved lines needed)"))
            summary["by_sport_market"][f"{sport} {m}"] = entry
    summary["sample_note"] = ("too small to judge in every sport and market" if not any(readable) else
                              "readable in some sport-markets only (see each line)" if not all(readable) else
                              "every sport-market has enough moved lines to read the direction; still not proof of profit")
    lines.append(f"Sample: {summary['sample_note']}. Judge on line movement toward the pick against the other priced picks, never on return alone.")
    os.makedirs(os.path.join(out_dir, "grades"), exist_ok=True)
    path = os.path.join(out_dir, "grades", f"grade-{now.strftime('%Y%m%dT%H%M%SZ')}.json")
    with open(path, "x") as fh:                       # write-once: every daily grade is kept
        json.dump(dict(summary=summary, rows=rows), fh, sort_keys=True)
    print("\n".join(lines))
    print(f"saved {path}")
    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--out", default=None, help="default: <root>/data/early-board")
    ap.add_argument("--from-hours", type=float, default=3)
    ap.add_argument("--to-hours", type=float, default=40)
    ap.add_argument("--grade", action="store_true")
    a = ap.parse_args()
    out_dir = a.out or os.path.join(a.root, "data", "early-board")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with tempfile.TemporaryDirectory(prefix="early-board-") as dest:   # store copies are large: always removed
        os.environ["NBA_DATA_DIR"] = dest      # the app code must never look at the live data folder from here
        if a.grade:
            grade(a.root, out_dir, dest, now)
            return
        picks, waiting = board(a.root, now, a.from_hours, a.to_hours, dest)
        log, md, leans = write_board(picks, waiting, out_dir, now)
        print(open(md).read())
        print(f"saved {md} and {log}")
        if any(w.get("failed") for w in waiting):
            sys.exit(1)


if __name__ == "__main__":
    main()
