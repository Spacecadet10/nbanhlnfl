"""Live goal and corner market forecasts (docs/SOCCER_MARKETS_PLAN.md): the sealed, reserved-test-read count models of
app/soccer_markets_eval.py applied before kickoff to ESPN fixtures, plus the read views the app, tickets and settlement use.

What is served: a family (goals, corners) only when its sealed candidate file is present, matches the pinned sha256, and
its reserved-test reading says `serve`; then only the reading's served competitions and markets. Double chance and draw no
bet are exact functions of the tested 90-minute 1X2 forecast (app/soccer_live.py, app/intl_live.py), never of these models,
and corner markets never read a 1X2 forecast.

Point in time = the evaluated one (protocol `live`): a match dated D (league-zone local date) is forecast at or before
D 06:00 UTC from every label available by then of earlier dates; the frozen model refits from those inputs exactly as
soccer_markets_eval.walk does (no state between cutoffs). A family's run is refused, visibly (soccer_market_checks), when
any input competition's ESPN data is older than 26 h, a season day before the cutoff was never fetched, an eligible match
before the cutoff is unsettled, a corner count is missing (corners only), or the previous season's history is absent. A
refusal records nothing for that family and never affects the other family or the 1X2 forecast.

Bookmaker offers: only prices the ESPN scoreboard lists with an explicit side and line (soccer_odds_quotes: DraftKings
3-way result, match goals total, goal handicap), the latest offered before the reading time and before kickoff. Every
other line is a model line (fair odds from the forecast), labelled as such. No market here has proven skill against the
bookmaker (no historical prices exist in a free source), so a price comparison is information, never a recommendation.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np

from . import config, soccer, soccer_live, soccer_markets as sm, soccer_markets_eval as ev
from .backtest import seal as _seal
from .db import utc_now
from .odds import devig_two_way

HERE = Path(__file__).parent
FAMILIES = ("goals", "corners")
# Pinned after the development run and the reserved test (docs/evidence/soccer-markets-2026-11-09/). Empty = not sealed:
# the family is reported unavailable and nothing is forecast.
CANDIDATES: dict[str, str] = {"goals": "cfe78bf98a8e48215474de26d020fc5a0fdb45830957b38e24e47db88efbd432",
                              "corners": "f50224ddd00ef8abd3d949180b8324ab934ac4fa65035cb2c0ff49c364d90377"}
FAMILY_MARKETS = {"goals": ("goals_total", "goals_home", "goals_away", "btts"),
                  "corners": ("corners_total", "corners_home", "corners_away", "corners_handicap")}
PROTOCOL_NAME = {"goals_total": "goals_total_over", "goals_home": "goals_home_over", "goals_away": "goals_away_over",
                 "btts": "btts_yes", "corners_total": "corners_total_over", "corners_home": "corners_home_over",
                 "corners_away": "corners_away_over", "corners_handicap": "corners_handicap_home"}
DERIVED = ("double_chance", "draw_no_bet")
INPUTS = ev.COMPETITIONS                         # the model's ratings use every one of the seven competitions
LABEL = {"goals": "Upcoming forecast · 90-minute goals", "corners": "Upcoming forecast · 90-minute corners"}
SETTLEMENT_RULE = ("Settled on 90 minutes plus stoppage time; extra time and penalties do not count. A whole line pushes "
                   "(stake back) on equality; a quarter line splits the stake between the two neighbouring lines (half "
                   "win / half loss possible); draw no bet pushes on a draw. A postponed, cancelled or abandoned match is void; a corner "
                   "count listed as 0-0 is not trusted and settles nothing.")
MODEL_LINE = "Model line: fair odds from the model's forecast; no bookmaker price for this line in our free source."
BOOKMAKER_LINE = "Bookmaker offer: price as listed by DraftKings on ESPN, with the time it was retrieved."
NO_PROVEN_SKILL = ("No proven skill against the bookmaker: no free source has historical prices for these markets, so "
                   "the model was tested on outcomes only. A price comparison is information, not a recommendation.")
NO_BOOKMAKER_CORNERS = ("No bookmaker corner prices: the only free source used (ESPN's DraftKings listing) offers no corner "
                        "market. Corner lines are model lines only.")
MIN_HISTORY_LEAGUE = 300


class LiveRefused(RuntimeError):
    pass


def _t(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def access_log_path() -> Path:
    return config.data_dir() / "research" / "soccer_markets_live" / "inputs_access.jsonl"


# --- Sealed models and their test reading ------------------------------------------------------------------------------
def _file(kind: str, family: str) -> Path:
    return HERE / f"soccer_markets_{kind}_{family}.json"


def sealed(family: str) -> Optional[dict]:
    """The family's sealed candidate (byte copy of the sealed file), or None when the family has no pinned candidate."""
    pin = CANDIDATES.get(family)
    path = _file("sealed", family)
    if not pin or not path.exists():
        return None
    s = json.loads(path.read_text())
    if s.get("sha256") != pin or _seal(s).get("sha256") != pin or s.get("family") != family:
        raise LiveRefused(f"the sealed {family} file is not the pinned candidate {pin[:8]}…")
    return s


def reading(family: str) -> Optional[dict]:
    path = _file("reading", family)
    if not path.exists():
        return None
    r = json.loads(path.read_text())
    s = sealed(family)
    if s is None or r.get("candidate_sha256") != s["sha256"]:
        raise LiveRefused(f"the {family} test reading does not belong to the pinned candidate")
    return r


def state(family: str) -> dict:
    """Whether the family is served and where: from the pinned seal and its reserved-test reading only."""
    try:
        s = sealed(family)
        r = reading(family) if s is not None else None
    except LiveRefused as exc:
        return {"family": family, "served": False, "reason": str(exc), "competitions": [], "markets": []}
    if s is None:
        return {"family": family, "served": False, "reason": "No tested model: development or the reserved test has not "
                "produced a sealed candidate for this family.", "competitions": [], "markets": []}
    if r is None or r.get("verdict") != "serve":
        why = ("the reserved test has not been read" if r is None else
               "the reserved test did not support serving it (protocol R1-R3)")
        return {"family": family, "served": False, "reason": f"Not served: {why}.", "candidate_sha256": s["sha256"],
                "model": s["model"], "competitions": [], "markets": []}
    markets = [m for m in FAMILY_MARKETS[family] if PROTOCOL_NAME[m] in r["served_markets"]]
    return {"family": family, "served": bool(markets and r["served_competitions"]), "reason": None,
            "candidate_sha256": s["sha256"], "model": s["model"], "competitions": list(r["served_competitions"]),
            "markets": markets, "withheld_markets": [m for m in FAMILY_MARKETS[family] if m not in markets],
            "disclosed_lines": r.get("R4_disclosed_lines", [])}


# --- Completeness checks -----------------------------------------------------------------------------------------------
def check(conn: sqlite3.Connection, family: str, competition: str, now: str, cutoff: str, data: ev.Data) -> dict:
    """Inputs of `competition` complete for a forecast at `cutoff`: retrieved within 26 h, every season day before the
    cutoff's date fetched, every eligible match before the cutoff settled (corners: with its count), previous season
    present (a league: at least 300 labelled usable matches)."""
    problems = []
    last = soccer.last_refresh_at(conn, competition)
    if last is None or _t(now) - _t(last) > soccer_live.MAX_AGE:
        problems.append(f"ESPN data last retrieved {last or 'never'}; older than 26 h")
    fetched = set(soccer.fetched_dates(conn, competition))
    d, end = date.fromisoformat(soccer_live.SEASON_FETCH_FROM), _t(cutoff).date()
    missing = []
    while d < end:
        if d.isoformat() not in fetched:
            missing.append(d.isoformat())
        d += timedelta(days=1)
    if missing:
        problems.append(f"{len(missing)} season date(s) never fetched (first {missing[0]})")
    rules, main = ev.stage_rules(), ev._main_types(conn, soccer_live.SEASON)
    labelled = {o.mid for o in data.obs if o.h is not None and o.available_at <= cutoff}
    if family == "corners":           # delivered = any stats row by the cutoff, also a both-zero one (amendment 2)
        labelled |= {r[0] for r in conn.execute("SELECT DISTINCT s.match_id FROM soccer_match_stats s JOIN soccer_matches m ON "
                                                "m.match_id = s.match_id WHERE m.competition_id = ? AND m.season = ? AND "
                                                "s.available_at <= ?", (competition, soccer_live.SEASON, cutoff))}
    for r in conn.execute("SELECT match_id, season, season_type_id, stage, kickoff_at, status, decided_in FROM soccer_matches "
                          "WHERE competition_id = ? AND season = ? AND kickoff_at < ? ORDER BY kickoff_at",
                          (competition, soccer_live.SEASON, cutoff)):
        if not ev.eligible_stage(competition, r["season"], r["season_type_id"], r["stage"], main, rules):
            continue
        if r["status"] in ("postponed", "canceled", "abandoned"):
            continue
        if r["status"] != "final":
            problems.append(f"{r['match_id']} kicked off {r['kickoff_at']} but has no result yet ({r['status']})")
        elif r["decided_in"] == "regulation" and r["match_id"] not in labelled:
            what = "corner count" if family == "corners" else "result"
            problems.append(f"{r['match_id']} has no {what} available by the cutoff")
    prev = sum(1 for o in data.obs if o.competition == competition and o.season == soccer_live.SEASON - 1 and o.h is not None)
    need = MIN_HISTORY_LEAGUE if competition in ev.LEAGUES else 1
    if prev < need:
        problems.append(f"previous season history missing ({prev} labelled matches; run `soccer history-months`)")
    return {"competition": competition, "ok": not problems, "problems": problems, "retrieved_at": last}


# --- Forecast run ------------------------------------------------------------------------------------------------------
def targets_on(conn: sqlite3.Connection, competitions, day: str) -> list[ev.Obs]:
    rules, main = ev.stage_rules(), ev._main_types(conn, soccer_live.SEASON)
    q = ",".join("?" * len(competitions))
    out = []
    for r in conn.execute(f"SELECT match_id, competition_id, season, season_type_id, stage, kickoff_at, home_team_id, "
                          f"away_team_id, status FROM soccer_matches WHERE season = ? AND status = 'scheduled' AND "
                          f"competition_id IN ({q}) ORDER BY kickoff_at, match_id", (soccer_live.SEASON, *competitions)):
        if not ev.eligible_stage(r["competition_id"], r["season"], r["season_type_id"], r["stage"], main, rules):
            continue
        d = ev.local_date(r["kickoff_at"], r["competition_id"])
        if d == day and r["home_team_id"] != r["away_team_id"]:
            out.append(ev.Obs(r["match_id"], r["competition_id"], r["season"], d, r["home_team_id"], r["away_team_id"],
                              None, None, None))
    return out


def _log_access(family: str, sha: str, purpose: str) -> None:
    p = access_log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps({"at": utc_now(), "family": family, "candidate_sha256": sha, "purpose": purpose}) + "\n")


def run_family(conn: sqlite3.Connection, family: str, *, day: str, now: str, write: bool = True,
               record_refusal: bool = True) -> dict:
    st = state(family)
    cutoff = ev.cutoff_for(day)
    if not st["served"]:
        return {"family": family, "status": "not_served", "reason": st["reason"]}
    s = sealed(family)
    _log_access(family, s["sha256"], f"live {family} inputs for {day}")
    data = ev.collect(conn, family, soccer_live.SEASON)
    checks = {c: check(conn, family, c, now, cutoff, data) for c in INPUTS}
    shared = [f"input competition {c}: {p}" for c in INPUTS for p in checks[c]["problems"]]
    rows = []
    if not shared:
        targets = targets_on(conn, st["competitions"], day)
        rows = ev.forecast_targets(s, data, targets) if targets else []
    current = sorted((o.mid, o.available_at, o.h, o.a) for o in data.obs
                     if o.season == soccer_live.SEASON and o.h is not None and o.available_at <= cutoff)
    digest = hashlib.sha256(json.dumps([s["sha256"], current]).encode()).hexdigest()
    through = max((x[1] for x in current), default=None)
    report = {"family": family, "status": "ok" if not shared else "refused", "competitions": {}}
    for c in st["competitions"]:
        mine = [r for r in rows if r["competition"] == c]
        n = sum(record(conn, family, r, s, cutoff_at=cutoff, inputs_digest=digest, inputs_through=through,
                       retrieved_at=checks[c]["retrieved_at"], write=write) for r in mine)
        problems = checks[c]["problems"] + [x for x in shared if not x.startswith(f"input competition {c}:")]
        if write and (not shared or record_refusal):
            conn.execute("INSERT INTO soccer_market_checks (checked_at, family, competition_id, match_date, ok, problems_json, "
                         "forecasts) VALUES (?, ?, ?, ?, ?, ?, ?)", (now, family, c, day, int(not shared), json.dumps(problems), n))
        report["competitions"][c] = {"ok": not shared, "problems": problems, "matches": len(mine), "recorded": n}
    return report


def run(conn: sqlite3.Connection, *, day: Optional[str] = None, write: bool = True, record_refusal: bool = True) -> dict:
    """Both families for the matches dated `day` (default today, UTC), before its 06:00 UTC cutoff."""
    now = utc_now()
    day = day or _t(now).date().isoformat()
    cutoff = ev.cutoff_for(day)
    if now > cutoff or day < _t(now).date().isoformat():
        return {"as_of": now, "date": day, "status": "refused",
                "reason": f"after the cutoff {cutoff}: a live forecast is never made after its cutoff"}
    out = {"as_of": now, "date": day, "cutoff_at": cutoff, "families": {}}
    for f in FAMILIES:
        try:
            out["families"][f] = run_family(conn, f, day=day, now=now, write=write, record_refusal=record_refusal)
        except LiveRefused as exc:
            out["families"][f] = {"family": f, "status": "refused", "reason": str(exc)}
    out["status"] = "ok" if all(v["status"] in ("ok", "not_served") for v in out["families"].values()) else "refused"
    return out


def record(conn, family: str, r: dict, s: dict, *, cutoff_at, inputs_digest, inputs_through, retrieved_at,
           write: bool = True) -> int:
    key = hashlib.sha256(json.dumps([r["mid"], family, cutoff_at, s["sha256"], inputs_digest]).encode()).hexdigest()
    joint = [[float(x) for x in row] for row in r["joint"]]
    old = conn.execute("SELECT joint_json FROM soccer_market_forecasts WHERE idempotency_key = ?", (key,)).fetchone()
    if old:
        if json.loads(old[0]) != joint:
            raise ValueError(f"same inputs, different {family} forecast for {r['mid']}")
        return 0
    if not write:
        return 1
    version = conn.execute("SELECT IFNULL(MAX(version), 0) + 1 FROM soccer_market_forecasts WHERE match_id = ? AND family = ?",
                           (r["mid"], family)).fetchone()[0]
    conn.execute("INSERT INTO soccer_market_forecasts (match_id, competition_id, family, version, candidate_sha256, model, "
                 "idempotency_key, inputs_digest, inputs_through, source_retrieved_at, mean_home, mean_away, max_count, "
                 "joint_json, cutoff_at, generated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (r["mid"], r["competition"], family, version, s["sha256"], s["model"], key, inputs_digest, inputs_through,
                  retrieved_at, r["mh"], r["ma"], ev.CAP[family], json.dumps(joint), cutoff_at, utc_now()))
    return 1


# --- Reads -------------------------------------------------------------------------------------------------------------
def has_tables(conn) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='soccer_market_forecasts'").fetchone() is not None


BASE = ("SELECT f.*, m.kickoff_at, m.status, m.decided_in, m.home_score, m.away_score, m.result_available_at, "
        "ht.name AS home_name, at.name AS away_name FROM soccer_market_forecasts f JOIN soccer_matches m ON m.match_id = "
        "f.match_id JOIN teams ht ON ht.team_id = m.home_team_id JOIN teams at ON at.team_id = m.away_team_id ")
LATEST = BASE + ("WHERE f.version = (SELECT MAX(version) FROM soccer_market_forecasts g WHERE g.match_id = f.match_id "
                 "AND g.family = f.family) ")


def counts_of(conn, r, as_of: str) -> Optional[tuple[int, int]]:
    """The family's 90-minute counts known at `as_of`, or None (not final, not regulation, not yet available)."""
    if r["status"] != "final" or r["decided_in"] != "regulation" or not r["result_available_at"] or r["result_available_at"] > as_of:
        return None
    if r["family"] == "goals":
        return (r["home_score"], r["away_score"])
    s = conn.execute("SELECT home_corners, away_corners FROM soccer_match_stats WHERE match_id = ? AND available_at <= ? "
                     "ORDER BY revision DESC LIMIT 1", (r["match_id"], as_of)).fetchone()
    if s is None or (s[0] == 0 and s[1] == 0):        # a both-zero count is "not delivered" (protocol amendment 2)
        return None
    return (s[0], s[1])


def _zero_zero(conn, match_id: str, as_of: str) -> bool:
    s = conn.execute("SELECT home_corners, away_corners FROM soccer_match_stats WHERE match_id = ? AND available_at <= ? "
                     "ORDER BY revision DESC LIMIT 1", (match_id, as_of)).fetchone()
    return s is not None and s[0] == 0 and s[1] == 0


def quotes(conn, match_id: str, as_of: str) -> list[dict]:
    """Latest offered current bookmaker price per (provider, market, side) retrieved at or before `as_of`."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='soccer_odds_quotes'").fetchone():
        return []
    rows = conn.execute("SELECT q.* FROM soccer_odds_quotes q WHERE q.match_id = ? AND q.price_kind = 'current' AND "
                        "q.retrieved_at <= ? AND q.quote_id = (SELECT MAX(p.quote_id) FROM soccer_odds_quotes p WHERE "
                        "p.match_id = q.match_id AND p.provider = q.provider AND p.market = q.market AND p.side = q.side "
                        "AND p.price_kind = 'current' AND p.retrieved_at <= ?)", (match_id, as_of, as_of)).fetchall()
    return [dict(r) for r in rows if r["offered"]]


def _selection(market: str, side: str, line: Optional[float], probs: dict, source: str, **extra) -> dict:
    fair = sm.fair_decimal(probs)
    return {"market": market, "market_title": sm.TITLES[market], "side": side, "line": line, "source": source,
            "probabilities": probs, "win_share": sm.win_share(probs), "fair_decimal": fair, **extra}


def market_selections(joint, family: str, markets, match_quotes: list[dict]) -> list[dict]:
    """Per served market: the model's fair line (both sides) and, for a market a bookmaker prices, each offered line."""
    out = []
    for m in markets:
        sides = sm.SIDES[m]
        if m in sm.LINED:
            ln = sm.fair_line(joint, m)
            for side in sides:
                own = -ln if (m == "corners_handicap" and side == "away") else ln
                out.append(_selection(m, side, own, sm.probabilities(joint, m, side, own), "model", note=MODEL_LINE))
        else:
            for side in sides:
                out.append(_selection(m, side, None, sm.probabilities(joint, m, side, None), "model", note=MODEL_LINE))
        if m == "goals_total":
            by_line = defaultdict(dict)
            for q in match_quotes:
                if q["market"] == "total_goals":
                    by_line[(q["provider"], q["line"])][q["side"]] = q
            for (provider, line), qs in sorted(by_line.items(), key=lambda x: (x[0][0], x[0][1])):
                devig = (devig_two_way(qs["over"]["american"], qs["under"]["american"])
                         if {"over", "under"} <= set(qs) else None)
                for side, q in sorted(qs.items()):
                    probs = sm.probabilities(joint, m, side, line)
                    out.append(_selection(m, side, line, probs, "bookmaker", note=BOOKMAKER_LINE, provider=provider,
                                          american=q["american"], decimal=q["decimal_odds"], retrieved_at=q["retrieved_at"],
                                          market_probability=(devig[0] if side == "over" else devig[1]) if devig else None,
                                          expected_return=sm.expected_return(probs, q["decimal_odds"])))
    return out


def derived_selections(p1x2) -> list[dict]:
    """Double chance and draw no bet from the tested 1X2 forecast (model lines; no bookmaker lists them in the source)."""
    out = []
    for m in DERIVED:
        for side in sm.SIDES[m]:
            out.append(_selection(m, side, None, sm.probabilities_1x2(p1x2, m, side), "model", note=MODEL_LINE))
    return out


def forecast_row(conn, r, as_of: str, markets) -> dict:
    joint = json.loads(r["joint_json"])
    counts = counts_of(conn, r, as_of)
    sels = market_selections(joint, r["family"], markets, quotes(conn, r["match_id"], min(as_of, r["kickoff_at"])))
    for s in sels:
        s["result"] = sm.grade_market(s["market"], s["side"], s["line"], counts) if counts else None
    return {"forecast_id": r["forecast_id"], "family": r["family"], "match_id": r["match_id"],
            "competition": r["competition_id"], "kickoff_at": r["kickoff_at"], "home": r["home_name"], "away": r["away_name"],
            "mean_home": r["mean_home"], "mean_away": r["mean_away"], "generated_at": r["generated_at"],
            "cutoff_at": r["cutoff_at"], "source_retrieved_at": r["source_retrieved_at"], "inputs_through": r["inputs_through"],
            "candidate_sha256": r["candidate_sha256"], "model": r["model"], "status": r["status"], "counts": counts,
            "label": LABEL[r["family"]], "selections": sels}


# Ticket snapshots read the same window as GET /api/soccer/markets (kickoff within the last 2 days, or later), so a
# snapshot never recomputes every stored forecast ever (review MEDIUM 1, Nov 9).
TICKET_WINDOW_DAYS = 2


def match_markets(conn, *, competition: Optional[str] = None, day: Optional[str] = None, since: Optional[str] = None,
                  as_of: Optional[str] = None) -> list[dict]:
    """Per match: the served families' latest forecasts with their selections, and double chance / draw no bet from the
    match's latest 1X2 forecast. Only stored rows; nothing is computed for a match without a stored forecast."""
    as_of = as_of or utc_now()
    states = {f: state(f) for f in FAMILIES}
    by_match: dict = {}
    if has_tables(conn):
        for r in conn.execute(LATEST + "ORDER BY m.kickoff_at, f.match_id, f.family").fetchall():
            st = states[r["family"]]
            if not st["served"] or r["competition_id"] not in st["competitions"]:
                continue
            if competition and r["competition_id"] != competition:
                continue
            if day and soccer_live.local_date(r["kickoff_at"], r["competition_id"]) != day:
                continue
            if since and r["kickoff_at"] < since:
                continue
            m = by_match.setdefault(r["match_id"], {"match_id": r["match_id"], "competition": r["competition_id"],
                                                    "kickoff_at": r["kickoff_at"], "home": r["home_name"],
                                                    "away": r["away_name"], "families": {}, "derived": None})
            m["families"][r["family"]] = forecast_row(conn, r, as_of, st["markets"])
    for f in soccer_live.forecasts(conn, competition=competition, day=day, as_of=as_of):
        if since and f["kickoff_at"] < since:
            continue
        p = (f["probabilities"]["home"], f["probabilities"]["draw"], f["probabilities"]["away"])
        m = by_match.setdefault(f["match_id"], {"match_id": f["match_id"], "competition": f["competition"],
                                                "kickoff_at": f["kickoff_at"], "home": f["home"], "away": f["away"],
                                                "families": {}, "derived": None})
        sels = derived_selections(p)
        score = None
        r = conn.execute("SELECT status, decided_in, home_score, away_score, result_available_at FROM soccer_matches WHERE "
                         "match_id = ?", (f["match_id"],)).fetchone()
        if r and r["status"] == "final" and r["decided_in"] == "regulation" and r["result_available_at"] <= as_of:
            score = (r["home_score"], r["away_score"])
        for s in sels:
            s["result"] = sm.grade_1x2_market(score, s["market"], s["side"]) if score else None
        m["derived"] = {"forecast_id": f["forecast_id"], "generated_at": f["generated_at"], "cutoff_at": f["cutoff_at"],
                        "candidate_sha256": f["candidate_sha256"], "probabilities_1x2": f["probabilities"],
                        "selections": sels, "label": "From the tested 90-minute 1X2 forecast"}
    for m in by_match.values():
        m["prices"] = match_prices(conn, m["match_id"], as_of)
    return sorted(by_match.values(), key=lambda m: (m["kickoff_at"], m["match_id"]))


def match_prices(conn, match_id: str, as_of: str) -> dict:
    """Owner request Nov 11: Bet365's double chance as a sportsbook quote with its retrieval time (app/bet365_dc.py), and
    double chance / draw no bet derived from DraftKings' stored 1X2 prices (app/implied_lines.py; never a price to bet).
    Both as of min(as_of, kickoff). A problem here never hides a forecast."""
    from . import bet365_dc, implied_lines
    out = {"bet365_double_chance": None, "derived_from_match_result_prices": None}
    try:
        r = conn.execute("SELECT home_team_id, away_team_id, kickoff_at FROM soccer_matches WHERE match_id = ?",
                         (match_id,)).fetchone()
        if r is None:
            return out
        at = min(as_of, r["kickoff_at"])
        out["bet365_double_chance"] = bet365_dc.latest(match_id, r["home_team_id"], r["away_team_id"], at)
        out["derived_from_match_result_prices"] = implied_lines.derived_dc_dnb(quotes(conn, match_id, at))
    except Exception as exc:
        out["error"] = type(exc).__name__
    return out


def ranking(matches: list[dict], limit: int = 50) -> list[dict]:
    """Every selection of the given matches, ordered by the model's win share (most likely first). Ties: earlier kickoff,
    then match id, market, side. A price never changes the order; it is shown beside the model's number."""
    rows = []
    for m in matches:
        if m["kickoff_at"] <= utc_now():
            continue
        groups = [*(f["selections"] for f in m["families"].values()), *([m["derived"]["selections"]] if m["derived"] else [])]
        for sels in groups:
            for s in sels:
                rows.append({**s, "match_id": m["match_id"], "competition": m["competition"], "kickoff_at": m["kickoff_at"],
                             "home": m["home"], "away": m["away"]})
    rows.sort(key=lambda s: (-s["win_share"], s["kickoff_at"], s["match_id"], s["market"], s["side"]))
    return rows[:limit]


def latest_checks(conn) -> dict:
    if not has_tables(conn):
        return {}
    out = defaultdict(dict)
    for r in conn.execute("SELECT c.* FROM soccer_market_checks c WHERE c.check_id = (SELECT MAX(check_id) FROM "
                          "soccer_market_checks d WHERE d.family = c.family AND d.competition_id = c.competition_id)"):
        out[r["family"]][r["competition_id"]] = {"checked_at": r["checked_at"], "ok": bool(r["ok"]),
                                                 "problems": json.loads(r["problems_json"]), "match_date": r["match_date"],
                                                 "forecasts": r["forecasts"]}
    return dict(out)


def history(conn, *, competition: Optional[str] = None, as_of: Optional[str] = None) -> dict:
    """Settled live forecasts and their record per market line (the protocol's half lines: mean predicted probability vs
    observed rate, Brier), plus every settled forecast with its counts. Live rows only; never backtest rows."""
    as_of = as_of or utc_now()
    out = {"as_of": as_of, "families": {}}
    if not has_tables(conn):
        return out
    masks = {f: ev.event_masks(f) for f in FAMILIES}
    for family in FAMILIES:
        rows, rec = [], defaultdict(list)
        for r in conn.execute(LATEST + "AND f.family = ? ORDER BY m.kickoff_at DESC", (family,)).fetchall():
            if competition and r["competition_id"] != competition:
                continue
            counts = counts_of(conn, r, as_of)
            if counts is None:
                continue
            joint = np.array(json.loads(r["joint_json"]))
            cap = ev.CAP[family]
            for (m, ln), mask in masks[family].items():
                rec[f"{m}@{ln}"].append((float(joint[mask].sum()), bool(mask[min(counts[0], cap), min(counts[1], cap)])))
            rows.append({"forecast_id": r["forecast_id"], "match_id": r["match_id"], "competition": r["competition_id"],
                         "kickoff_at": r["kickoff_at"], "home": r["home_name"], "away": r["away_name"], "counts": counts,
                         "mean_home": r["mean_home"], "mean_away": r["mean_away"], "generated_at": r["generated_at"]})
        record = {k: {"n": len(v), "mean_predicted": sum(p for p, _ in v) / len(v), "observed": sum(y for _, y in v) / len(v),
                      "brier": sum((p - y) ** 2 for p, y in v) / len(v)} for k, v in rec.items() if v}
        out["families"][family] = {"settled": len(rows), "record": record, "matches": rows[:200]}
    return out


# --- Settlement (tickets) ----------------------------------------------------------------------------------------------
def grade(conn, forecast_id: int, market: str, side: str, line: Optional[float], as_of: str) -> dict:
    """Settles one selection of a stored goal/corner forecast on the counts known at `as_of`."""
    r = conn.execute(BASE + "WHERE f.forecast_id = ?", (forecast_id,)).fetchone()
    if r is None or sm.FAMILY_OF.get(market) != r["family"]:
        raise ValueError("Archived soccer market forecast identity mismatch")
    if r["status"] in ("postponed", "canceled", "abandoned"):
        g = "void"
    elif r["status"] != "final" or not r["result_available_at"] or r["result_available_at"] > as_of:
        g = "pending"
    elif r["decided_in"] != "regulation":
        g = "unscorable"
    elif r["family"] == "corners" and _zero_zero(conn, r["match_id"], as_of):
        g = "unscorable"                               # ESPN listed 0-0 corners: not credible (amendment 2)
    else:
        counts = counts_of(conn, r, as_of)
        g = "pending" if counts is None else sm.grade_market(market, side, line, counts)
    counts = counts_of(conn, r, as_of) if g not in ("void", "pending", "unscorable") else None
    return {"grade": g, "match_id": r["match_id"], "counts": counts, "result_available_at": r["result_available_at"]}


def grade_derived(conn, forecast_id: int, market: str, side: str, as_of: str) -> dict:
    """Settles a double-chance / draw-no-bet selection of a stored 1X2 forecast on the 90-minute score."""
    r = conn.execute(soccer_live.BASE_SQL + "WHERE f.forecast_id = ?", (forecast_id,)).fetchone()
    if r is None or market not in DERIVED or side not in sm.SIDES[market]:
        raise ValueError("Archived soccer forecast identity mismatch")
    if r["status"] in ("postponed", "canceled", "abandoned"):
        g = "void"
    elif r["status"] != "final" or not r["result_available_at"] or r["result_available_at"] > as_of:
        g = "pending"
    elif r["decided_in"] != "regulation":
        g = "unscorable"
    else:
        g = sm.grade_1x2_market((r["home_score"], r["away_score"]), market, side)
    known = g not in ("void", "pending", "unscorable")
    return {"grade": g, "match_id": r["match_id"], "home_score": r["home_score"] if known else None,
            "away_score": r["away_score"] if known else None, "result_available_at": r["result_available_at"]}
