"""Live soccer forecasts: the sealed draw-aware Elo applied before kickoff to current ESPN fixtures and results.

Scope (the reserved-test reading, docs/evidence/soccer-test-reading-plan-2026-11-08/result/reading.json): EPL, La Liga,
Bundesliga, Serie A, Ligue 1, UEFA Champions League. Market: the 90-minute home/draw/away result only; extra time and
penalties never count. No other soccer market is forecast. Plan and gates: docs/SOCCER_LIVE_PLAN.md.

Point in time = the evaluated one: a match dated D (its local date) is forecast at or before D 06:00 UTC from every
usable result available by then (never a same-day result). The frozen model and its walk-forward come unchanged from
app/soccer_eval.py (a sealed file, never edited here): history = openfootball seasons <= 2025 (soccer_eval.load, the read
logged to the live access log); the current season = ESPN soccer_matches (app/soccer.py), clubs identified through the
explicit, versioned map app/soccer_live_clubs.json (no similarity matching at run time).

A run is refused, visibly (soccer_live_checks) and with an alert, when any input competition's ESPN retrieval is older
than 26 h, a season date before the cutoff was never fetched, a match kicking off before the cutoff is not settled, a final
was not decided in regulation, or a club is not in the map. A refused run records no forecast.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import sqlite3
from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from . import config, soccer, soccer_eval
from .db import utc_now

CANDIDATE_SHA256 = "7dbc668c214f33862541156c0630913194e8a3421c0e7c3495f4de296ecd9c47"
CANDIDATE_FILE = Path(__file__).with_name("soccer_sealed_candidate.json")
CLUBS_FILE = Path(__file__).with_name("soccer_live_clubs.json")
LAUNCH = ("eng.1", "esp.1", "ger.1", "ita.1", "fra.1", "uefa.champions")
INPUT_ONLY = ("uefa.europa",)            # results feed ratings as in the evaluation; never forecast (failed R3)
ZONES = {"eng.1": "Europe/London", "esp.1": "Europe/Madrid", "ger.1": "Europe/Berlin", "ita.1": "Europe/Rome",
         "fra.1": "Europe/Paris", "uefa.champions": "Europe/Paris", "uefa.europa": "Europe/Paris"}
SEASON = 2026                            # 2026-27
SEASON_FETCH_FROM = "2026-08-01"         # every date from here to yesterday must have been fetched
UEFA_STAGES = ("league-phase",)          # UEFA main stage before the knockouts; deciding matches never used
MAX_AGE = timedelta(hours=26)
MARKET = "1x2_regulation"
OUTCOMES = ("home", "draw", "away")
LABEL = "Upcoming forecast · 90-minute result"
SETTLEMENT_RULE = ("Home win, draw or away win after 90 minutes plus stoppage time. Extra time and penalties do not "
                   "count; a postponed, cancelled or abandoned match is void.")


class LiveRefused(RuntimeError):
    pass


def _t(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _fmt(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sealed_candidate() -> dict:
    s = json.loads(CANDIDATE_FILE.read_text())
    if s.get("sha256") != CANDIDATE_SHA256 or not soccer_eval._valid_seal(s):
        raise LiveRefused("the sealed soccer candidate file is not the tested candidate 7dbc668c…")
    return s


def clubs() -> dict:
    return json.loads(CLUBS_FILE.read_text())


def access_log_path() -> Path:
    return config.data_dir() / "research" / "soccer_live" / "inputs_access.jsonl"


def local_date(kickoff_at: str, competition: str) -> str:
    return _t(kickoff_at).astimezone(ZoneInfo(ZONES[competition])).date().isoformat()


def _in_scope(r) -> bool:
    return not r["competition_id"].startswith("uefa.") or (r["stage"] in UEFA_STAGES)


def season_rows(conn: sqlite3.Connection, competitions) -> list:
    q = ",".join("?" * len(competitions))
    rows = conn.execute(f"SELECT m.*, ht.name AS home_name, at.name AS away_name FROM soccer_matches m "
                        f"JOIN teams ht ON ht.team_id = m.home_team_id JOIN teams at ON at.team_id = m.away_team_id "
                        f"WHERE m.season = ? AND m.competition_id IN ({q}) ORDER BY m.kickoff_at, m.match_id",
                        (SEASON, *competitions)).fetchall()
    return [r for r in rows if _in_scope(r)]


def check(conn: sqlite3.Connection, competition: str, now: str, horizon: str, club_map: dict) -> dict:
    """Whether `competition`'s current-season inputs are complete for a forecast whose cutoff is `horizon`: data retrieved
    within 26 h of `now`, every season date before the horizon's date fetched, every match that kicks off before the
    horizon settled (final in regulation, or postponed/cancelled/abandoned), and every club in the map."""
    problems = []
    last = soccer.last_refresh_at(conn, competition)
    if last is None or _t(now) - _t(last) > MAX_AGE:
        problems.append(f"ESPN data last retrieved {last or 'never'}; older than {int(MAX_AGE.total_seconds() // 3600)} h")
    fetched = set(soccer.fetched_dates(conn, competition))
    d, end = date.fromisoformat(SEASON_FETCH_FROM), _t(horizon).date()
    missing_dates = []
    while d < end:
        if d.isoformat() not in fetched:
            missing_dates.append(d.isoformat())
        d += timedelta(days=1)
    if missing_dates:
        problems.append(f"{len(missing_dates)} season date(s) never fetched (first {missing_dates[0]})")
    unmapped = set()
    for r in season_rows(conn, (competition,)):
        for team, name in ((r["home_team_id"], r["home_name"]), (r["away_team_id"], r["away_name"])):
            if team not in club_map["espn"]:
                unmapped.add(f"{name} ({team})")
        if r["kickoff_at"] >= horizon or r["status"] in ("postponed", "canceled", "abandoned"):
            continue
        if r["status"] != "final":
            problems.append(f"{r['match_id']} kicked off {r['kickoff_at']} but has no result yet ({r['status']})")
        elif r["decided_in"] != "regulation":
            problems.append(f"{r['match_id']} final not decided in regulation ({r['decided_in']})")
    if unmapped:
        problems.append("clubs not in the club map: " + ", ".join(sorted(unmapped)))
    return {"competition": competition, "ok": not problems, "problems": problems, "retrieved_at": last}


def dataset(conn: sqlite3.Connection, sealed: dict, club_map: dict) -> tuple[soccer_eval.Dataset, list]:
    """openfootball history (<= 2025) plus the ESPN current season, as one soccer_eval.Dataset."""
    hist = soccer_eval.load(conn, through=2025, sealed=sealed, access_log=access_log_path(),
                            purpose="live forecast inputs (seasons <= 2025)")
    cmap = club_map["espn"]
    matches, revisions, meta = list(hist.matches), list(hist.revisions), dict(hist.meta)
    members = defaultdict(set, {k: set(v) for k, v in hist.members.items()})
    current = []
    for r in season_rows(conn, (*LAUNCH, *INPUT_ONLY)):
        if r["home_team_id"] not in cmap or r["away_team_id"] not in cmap:
            continue                                        # its competition is refused by check()
        home, away, comp = cmap[r["home_team_id"]], cmap[r["away_team_id"]], r["competition_id"]
        day = local_date(r["kickoff_at"], comp)
        m = soccer_eval.Match(r["match_id"], comp, SEASON, day, home, away, r["stage"] or "", None)
        meta[m.mid] = (comp, SEASON, home, away, day)
        if comp in soccer_eval.LEAGUES:
            members[(comp, SEASON)].update({home, away})
        current.append((m, r))
        if r["status"] == "final" and r["result_available_at"]:
            usable = r["decided_in"] == "regulation"
            revisions.append(soccer_eval.Revision(m.mid, 1, r["result_available_at"], usable, r["home_score"], r["away_score"]))
            matches.append(replace(m, label=soccer_eval.label_of(r["home_score"], r["away_score"])) if usable else m)
        else:
            matches.append(m)
    ds = soccer_eval.Dataset(matches, revisions, {k: frozenset(v) for k, v in members.items()}, SEASON, meta)
    return ds, current


def forecast_day(sealed: dict, ds: soccer_eval.Dataset, current: list, day: str) -> tuple[dict, Optional[str], str]:
    """The frozen walk-forward over the current season, exactly as soccer_eval.walk: every earlier played match is
    forecast on its own date (so the model's state evolves as in the evaluation), then `day`'s scheduled matches at
    `day`'s cutoff. Returns {mid: (p_home, p_draw, p_away)} for `day`, the latest input availability, the inputs digest."""
    model = soccer_eval.frozen_model(sealed, ds)
    if hasattr(model, "match_meta"):
        model.match_meta = ds.meta
    targets = defaultdict(list)
    for m, r in current:
        if m.local_date < day and m.label is not None:
            targets[m.local_date].append(m)
        elif m.local_date == day and r["status"] == "scheduled" and m.competition in LAUNCH:
            targets[day].append(m)
    revs, avail = ds.revisions, [r.available_at for r in ds.revisions]
    i, out, fed = 0, {}, []
    for d in sorted(targets):
        cutoff = soccer_eval.cutoff_for(d)
        j = bisect.bisect_right(avail, cutoff)
        soccer_eval.feed(model, revs[i:j], cutoff_at=cutoff)
        fed.extend(revs[i:j])
        i = j
        probs = model.predict([replace(m, label=None) for m in targets[d]], cutoff)
        if d == day:
            out = {m.mid: tuple(float(x) for x in probs[m.mid]) for m in targets[d]}
    if sealed.get("calibration"):
        out = {k: soccer_eval.apply_calibration(sealed["calibration"], p) for k, p in out.items()}
    current_inputs = sorted((r.mid, r.available_at, r.hg, r.ag) for r in fed if r.mid.startswith("soccer:"))
    digest = hashlib.sha256(json.dumps([CANDIDATE_SHA256, current_inputs]).encode()).hexdigest()
    through = max((r.available_at for r in fed if r.mid.startswith("soccer:")), default=None)
    return out, through, digest


def run(conn: sqlite3.Connection, *, day: Optional[str] = None, write: bool = True, record_refusal: bool = True) -> dict:
    """One forecast run for the matches dated `day` (default: today, UTC) in the launch competitions. It is the evaluated
    point in time: made before `day` 06:00 UTC, and only when every match of all input competitions kicking off before
    that cutoff is settled, so the inputs are exactly those the cutoff would see. Any incomplete input competition
    refuses every launch competition (ratings are shared across competitions); refusals are recorded and visible."""
    now = utc_now()
    day = day or _t(now).date().isoformat()
    cutoff = soccer_eval.cutoff_for(day)
    report = {"as_of": now, "date": day, "cutoff_at": cutoff, "competitions": {}}
    if now > cutoff or day < _t(now).date().isoformat():
        report.update(status="refused", reason=f"after the cutoff {cutoff}: a live forecast is never made after its cutoff")
        return report
    sealed, club_map = sealed_candidate(), clubs()
    checks = {c: check(conn, c, now, cutoff, club_map) for c in (*LAUNCH, *INPUT_ONLY)}
    shared = [f"input competition {c}: {p}" for c in (*LAUNCH, *INPUT_ONLY) for p in checks[c]["problems"]]
    probs, through, digest, by_mid = {}, None, None, {}
    if not shared:
        ds, current = dataset(conn, sealed, club_map)
        probs, through, digest = forecast_day(sealed, ds, current, day)
        by_mid = {m.mid: m for m, _ in current}
    for c in LAUNCH:
        ok = not shared
        rows = [(mid, p) for mid, p in probs.items() if by_mid[mid].competition == c] if ok else []
        inserted = sum(record(conn, mid, c, p, cutoff_at=cutoff, inputs_digest=digest, inputs_through=through,
                              retrieved_at=checks[c]["retrieved_at"], write=write) for mid, p in rows)
        problems = checks[c]["problems"] + [x for x in shared if not x.startswith(f"input competition {c}:")]
        if write and (ok or record_refusal):     # an early attempt that must still wait is not a refusal
            conn.execute("INSERT INTO soccer_live_checks (checked_at, competition_id, match_date, ok, problems_json, forecasts) "
                         "VALUES (?, ?, ?, ?, ?, ?)", (now, c, day, int(ok), json.dumps(problems), inserted))
        report["competitions"][c] = {"ok": ok, "problems": problems, "retrieved_at": checks[c]["retrieved_at"],
                                     "matches": len(rows), "recorded": inserted}
    report["status"] = "ok" if not shared else "refused"
    return report


def record(conn, match_id, competition, p, *, cutoff_at, inputs_digest, inputs_through, retrieved_at, write=True) -> int:
    key = hashlib.sha256(json.dumps([match_id, MARKET, cutoff_at, CANDIDATE_SHA256, inputs_digest]).encode()).hexdigest()
    old = conn.execute("SELECT p_home, p_draw, p_away FROM soccer_live_forecasts WHERE idempotency_key = ?", (key,)).fetchone()
    if old:
        if tuple(old) != tuple(p):
            raise ValueError(f"same inputs, different probabilities for {match_id}")
        return 0
    if not write:
        return 1
    version = conn.execute("SELECT IFNULL(MAX(version), 0) + 1 FROM soccer_live_forecasts WHERE match_id = ? AND market = ?",
                           (match_id, MARKET)).fetchone()[0]
    conn.execute("INSERT INTO soccer_live_forecasts (match_id, competition_id, market, version, candidate_sha256, model, "
                 "idempotency_key, inputs_digest, inputs_through, source_retrieved_at, p_home, p_draw, p_away, cutoff_at, "
                 "generated_at) VALUES (?, ?, ?, ?, ?, 'elo', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (match_id, competition, MARKET, version, CANDIDATE_SHA256, key, inputs_digest, inputs_through,
                  retrieved_at, p[0], p[1], p[2], cutoff_at, utc_now()))
    return 1


# --- Reads (app, tickets, settlement) -----------------------------------------------------------------------------------
BASE_SQL = ("SELECT f.*, m.kickoff_at, m.status, m.decided_in, m.home_score, m.away_score, m.result_available_at, "
            "ht.name AS home_name, at.name AS away_name FROM soccer_live_forecasts f "
            "JOIN soccer_matches m ON m.match_id = f.match_id "
            "JOIN teams ht ON ht.team_id = m.home_team_id JOIN teams at ON at.team_id = m.away_team_id ")
LATEST_SQL = BASE_SQL + "WHERE f.version = (SELECT MAX(version) FROM soccer_live_forecasts g WHERE g.match_id = f.match_id) "


def has_tables(conn) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='soccer_live_forecasts'").fetchone() is not None


def outcome_of(r) -> Optional[int]:
    if r["status"] != "final" or r["decided_in"] != "regulation" or r["home_score"] is None:
        return None
    return soccer_eval.label_of(r["home_score"], r["away_score"])


def forecast_dict(r, as_of: str) -> dict:
    p = (r["p_home"], r["p_draw"], r["p_away"])
    known = r["result_available_at"] and r["result_available_at"] <= as_of
    result = OUTCOMES[outcome_of(r)] if known and outcome_of(r) is not None else None
    return {"forecast_id": r["forecast_id"], "match_id": r["match_id"], "competition": r["competition_id"],
            "kickoff_at": r["kickoff_at"], "home": r["home_name"], "away": r["away_name"],
            "probabilities": dict(zip(OUTCOMES, p)), "most_likely": OUTCOMES[max(range(3), key=lambda k: p[k])],
            "generated_at": r["generated_at"], "cutoff_at": r["cutoff_at"], "source_retrieved_at": r["source_retrieved_at"],
            "inputs_through": r["inputs_through"], "candidate_sha256": r["candidate_sha256"], "status": r["status"],
            "result": result, "label": LABEL, "settlement_rule": SETTLEMENT_RULE}


def forecasts(conn, *, competition: Optional[str] = None, day: Optional[str] = None, as_of: Optional[str] = None) -> list:
    if not has_tables(conn):
        return []
    as_of = as_of or utc_now()
    rows = conn.execute(LATEST_SQL + "ORDER BY m.kickoff_at, f.match_id").fetchall()
    out = []
    for r in rows:
        if competition and r["competition_id"] != competition:
            continue
        if day and local_date(r["kickoff_at"], r["competition_id"]) != day:
            continue
        out.append(forecast_dict(r, as_of))
    return out


def latest_checks(conn) -> dict:
    if not has_tables(conn):
        return {}
    rows = conn.execute("SELECT c.* FROM soccer_live_checks c WHERE c.check_id = (SELECT MAX(check_id) FROM "
                        "soccer_live_checks d WHERE d.competition_id = c.competition_id)").fetchall()
    return {r["competition_id"]: {"checked_at": r["checked_at"], "ok": bool(r["ok"]), "problems": json.loads(r["problems_json"]),
                                  "match_date": r["match_date"], "forecasts": r["forecasts"]} for r in rows}


def checks_for(conn, day: str) -> dict:
    """The latest completeness check of each competition for the matches dated `day`."""
    if not has_tables(conn):
        return {}
    rows = conn.execute("SELECT c.* FROM soccer_live_checks c WHERE c.match_date = ? AND c.check_id = (SELECT MAX(check_id) "
                        "FROM soccer_live_checks d WHERE d.competition_id = c.competition_id AND d.match_date = c.match_date)",
                        (day,)).fetchall()
    return {r["competition_id"]: {"checked_at": r["checked_at"], "ok": bool(r["ok"]), "problems": json.loads(r["problems_json"]),
                                  "match_date": r["match_date"], "forecasts": r["forecasts"]} for r in rows}


def grade(conn, forecast_id: int, outcome: str, as_of: str) -> dict:
    """Settles one selected outcome of a live forecast on the 90-minute result known at `as_of`."""
    r = conn.execute(BASE_SQL + "WHERE f.forecast_id = ?", (forecast_id,)).fetchone()
    if r is None or outcome not in OUTCOMES:
        raise ValueError("Archived soccer forecast identity mismatch")
    if r["status"] in ("postponed", "canceled", "abandoned"):
        g = "void"
    elif r["status"] != "final" or not r["result_available_at"] or r["result_available_at"] > as_of:
        g = "pending"
    elif outcome_of(r) is None:
        g = "unscorable"
    else:
        g = "win" if OUTCOMES[outcome_of(r)] == outcome else "loss"
    return {"grade": g, "match_id": r["match_id"], "home_score": r["home_score"] if g in ("win", "loss") else None,
            "away_score": r["away_score"] if g in ("win", "loss") else None, "result_available_at": r["result_available_at"]}
