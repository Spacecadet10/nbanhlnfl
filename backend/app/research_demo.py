"""Demo research store: an isolated in-memory SQLite store, built deterministically from synthetic data and passed through
the real cohort, decision, selection and report code. Demo mode only; it never touches a real store, and every number in it
is synthetic (no real game, model or accuracy)."""
import json
import math
import random
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from . import combine, db, fixtures, markets, research_live, selector
from .agreement import NAMES

DEFINITION = "demo-synthetic"
AS_OF = "2026-10-30T22:45:00Z"
HISTORY_GAMES = 900
FMT = "%Y-%m-%dT%H:%M:%SZ"
LINES = ("spread", "total", "team_total_home", "team_total_away")


def _game_row(conn, gid, tip, home, away, season):
    conn.execute("INSERT INTO games (game_id, season, season_type, tipoff_at, home_team_id, away_team_id, neutral_site, status, "
                 "first_seen_at, last_raw_id, updated_at) VALUES (?,?,?,?,?,?,0,'scheduled',?,1,?)",
                 (gid, season, "regular", tip, f"demo:{home}", f"demo:{away}", tip, tip))


def _members(rng, p, margin, total):
    """Synthetic member forecasts around a synthetic truth; one member is sometimes unavailable."""
    win = {n: min(0.97, max(0.03, p + rng.gauss(0, 0.03))) for n in NAMES["nba"]}
    win["elo"] = p
    if rng.random() < 0.3:
        win.pop("availability_two_hour")
    dists = {"points_normal": markets.MarginTotalNormal(margin, 12.5, total, 18.0),
             "ridge_points": markets.MarginTotalNormal(margin + rng.gauss(0, 1.5), 12.2, total + rng.gauss(0, 3), 17.5),
             "gbt_points": markets.MarginTotalNormal(margin + rng.gauss(0, 2.0), 12.8, total + rng.gauss(0, 4), 18.5)}
    return win, dists


def _cohorts(conn, gid, tip, win, dists, book, stacks):
    at = (datetime.strptime(tip, FMT) - timedelta(minutes=115)).strftime(FMT)
    g = conn.execute("SELECT * FROM games WHERE game_id=?", (gid,)).fetchone()
    cohorts, out = {gid: {}}, {"cohorts_created": 0, "cohorts_existing": 0, "skipped": {}, "recommendations": [],
                               "selections_created": 0}
    members = [{"name": n, "ref": {"table": "demo", "id": None}, "p": [v, 0.0, 1 - v]} for n, v in win.items()]
    missing = {n: "skipped: injury report not published within 60 minutes" for n in NAMES["nba"] if n not in win}
    stack = None
    if "winner" in stacks:
        stack = {"p": list(combine.stack(stacks["winner"], [[win[n], 0, 1 - win[n]] for n in NAMES["nba"][:5]])),
                 "weights": stacks["winner"]["weights"], "training_games": stacks["winner"]["n"]}
    c = research_live._cohort("nba", g, "winner", None, None, None, book, DEFINITION, "elo", at, at, members, missing,
                              NAMES["nba"][:5], stack, "demo stacker not fitted yet")
    research_live._store(conn, c, cohorts, out, True)
    for m in LINES:
        line = markets.model_line(dists["points_normal"], m)
        mem = [{"name": n, "ref": {"table": "demo", "id": None}, "p": list(research_live._vec(d.market(m, line)))}
               for n, d in dists.items()]
        st = None
        if m in stacks:
            st = {"p": list(combine.stack(stacks[m], [x["p"] for x in mem])), "weights": stacks[m]["weights"],
                  "training_games": stacks[m]["n"]}
        c = research_live._cohort("nba", g, m, line, "model", None, None, DEFINITION, "points_normal", at, at, mem, {},
                                  research_live.LINE_MEMBERS["nba"], st, "demo stacker not fitted yet")
        research_live._store(conn, c, cohorts, out, True)
    research_live._select(conn, "nba", DEFINITION, cohorts, out, lambda: at)
    return cohorts[gid]


@lru_cache(maxsize=1)
def store():
    conn = db.connect(Path(":memory:"), check_same_thread=False)
    db.migrate(conn)
    db.claim_store(conn, "nba")
    conn.execute("INSERT INTO raw_responses VALUES (1,'demo','demo','demo','demo',0,'demo','demo','2025-01-01T00:00:00Z')")
    for abbr, t in fixtures.TEAMS.items():
        conn.execute("INSERT INTO teams VALUES (?,?,?,?)", (f"demo:{abbr}", abbr, t.name, "2025-01-01T00:00:00Z"))
    rng = random.Random(fixtures.SEED + 1)
    abbrs = sorted(fixtures.TEAMS)
    events = []      # (tipoff, game id, home, away, p, margin, total, outcome or None, book)
    t0 = datetime(2025, 11, 3, 23, 30, tzinfo=timezone.utc)
    for i in range(HISTORY_GAMES):
        home, away = rng.sample(abbrs, 2)
        p = fixtures._prob(home, away)
        margin = 6 * math.log(p / (1 - p))
        total = 224 + rng.gauss(0, 6)
        tip = (t0 + timedelta(hours=9 * i)).strftime(FMT)
        m = round(rng.gauss(margin, 12)) or 1
        t = max(abs(m) + 150, round(rng.gauss(total, 18)))
        hs, as_ = (t + m) // 2 + ((t + m) % 2), (t - m) // 2
        events.append((tip, f"demo-hist-{i:04d}", home, away, p, margin, total, (hs, as_) if hs != as_ else (hs + 1, as_), None))
    for g in fixtures.games():
        p = g.prediction.home_win_prob
        margin = 6 * math.log(p / (1 - p))
        out = (g.outcome.home_score, g.outcome.away_score) if g.outcome else None
        book = None if g.market is None else {"p": [g.market.home_win_prob, 0.0, 1 - g.market.home_win_prob],
                                              "provider": g.market.bookmaker, "retrieved_at": g.market.retrieved_at.strftime(FMT)}
        events.append((g.tipoff.strftime(FMT), g.game_id, g.home.abbr, g.away.abbr, p, margin, 224.0, out, book))
    events.sort()
    stacks, train = {}, {m: [] for m in ("winner",) + LINES}
    next_monday = None
    for tip, gid, home, away, p, margin, total, outcome, book in events:
        when = datetime.strptime(tip, FMT)
        monday = datetime.strptime(research_live.scheduled_time(tip), FMT)
        if next_monday is None or monday > next_monday:
            next_monday = monday
            research_live.decide_due(conn, now=(monday + timedelta(minutes=5)).strftime(FMT), defn=DEFINITION)
            for m, rows in train.items():           # season-style refit on earlier settled games only
                if len(rows) >= research_live.STACK_MIN_TRAIN:
                    stacks[m] = combine.fit_stack(rows, m)
        _game_row(conn, gid, tip, home, away, 2026 if when < datetime(2026, 7, 1) else 2027)
        win, dists = _members(rng, p, margin, total)
        made = _cohorts(conn, gid, tip, win, dists, book, stacks)
        if outcome is not None:
            avail = (when + timedelta(hours=3)).strftime(FMT)
            conn.execute("UPDATE games SET status='final', home_score=?, away_score=?, result_available_at=?, "
                         "result_availability_basis='observed_retrieval' WHERE game_id=?", (*outcome, avail, gid))
            for m, (_, c) in made.items():
                s = markets.settle(m, c["line"], status="final", home=outcome[0], away=outcome[1])
                if s in research_live.SIDE:
                    vecs = [c["members"][k]["p"] for k in range(5)] if m == "winner" else [x["p"] for x in c["members"]]
                    if m != "winner" or len(c["members"]) >= 5:
                        train[m].append((vecs, research_live.SIDE[s]))
    return conn


def game_detail(game_id: str) -> dict:
    out = research_live.game_detail(store(), game_id, AS_OF)
    return {**out, "mode": "demo", "note": "Demo: synthetic research data, not a model or a real game."}


def slate(game_ids: list) -> dict:
    return research_live.slate(store(), game_ids, AS_OF)


def report(market: str) -> dict:
    return {**research_live.report(store(), market, AS_OF), "mode": "demo",
            "note": "Demo: synthetic research data run through the real selector code; not evidence of anything."}


def history() -> dict:
    return {**research_live.history(store(), AS_OF), "mode": "demo"}
