"""Live international (national-team) forecasts: the sealed international Elo applied before kickoff.

Model and protocol: app/intl_eval.py (sealed candidate 10547b47…, never edited here), docs/INTERNATIONAL_PROTOCOL.md.
Held-out test (2022-2025, read by the pre-registered rules): launch scope = continental finals, continental
qualification, friendlies, Nations Leagues, World Cup qualification; World Cup finals not in scope (n 64 < 300).
Evidence: docs/evidence/international-2026-11-08/.

Inputs: the pinned CC0 history (martj42/international_results @ b0948cb5, every listed result through its last date) plus
ESPN national-team results after that date (soccer_matches, migration 0039), mapped to the history's team names by the
explicit reviewed map app/intl_teams.json. A match dated D (its day = kickoff − 6 h, so evening kickoffs in the Americas
keep their local date) is forecast before D 06:00 UTC from every result of earlier days. A target waits while one of its
teams is affected by an unsettled earlier match (affected_teams: exact for this pairwise Elo). A run is refused, visibly,
when ESPN data is older than 26 h, an ESPN date since the history's end was never fetched, or a team is not in the map. Results of national teams in competitions ESPN does not list here are not inputs (disclosed).
Market: the 90-minute home/draw/away result only; a match that went to extra time counts as a 90-minute draw.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from . import intl_eval, soccer, soccer_live
from .db import utc_now

CANDIDATE_SHA256 = "10547b47146e579bb6f53e6adf4ac57b1896c0f7723cca564f9be93a49206d79"
CANDIDATE_FILE = Path(__file__).with_name("intl_sealed_candidate.json")
TEAMS_FILE = Path(__file__).with_name("intl_teams.json")
DATA_COMMIT = "b0948cb558c927c357db4067eac5b37b96d0e4bb"
GROUP = {"uefa.nations": "nations_league", "concacaf.nations.league": "nations_league", "fifa.friendly": "friendly",
         "caf.nations_qual": "continental_qualification", "uefa.euroq": "continental_qualification",
         "afc.cupq": "continental_qualification", "fifa.worldq.uefa": "wc_qualification",
         "fifa.worldq.conmebol": "wc_qualification", "fifa.worldq.concacaf": "wc_qualification",
         "fifa.worldq.afc": "wc_qualification", "fifa.worldq.caf": "wc_qualification", "fifa.worldq.ofc": "wc_qualification",
         "uefa.euro": "continental_finals", "conmebol.america": "continental_finals", "caf.nations": "continental_finals",
         "afc.asian.cup": "continental_finals", "concacaf.gold": "continental_finals", "fifa.world": "world_cup"}
COMPETITIONS = tuple(GROUP)
SCOPE = ("continental_finals", "continental_qualification", "friendly", "nations_league", "wc_qualification")
LAUNCH = tuple(c for c, g in GROUP.items() if g in SCOPE)
MAX_AGE = timedelta(hours=26)
DAY_SHIFT = timedelta(hours=6)


def _t(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def sealed_candidate() -> dict:
    s = json.loads(CANDIDATE_FILE.read_text())
    if s.get("sha256") != CANDIDATE_SHA256 or not intl_eval.valid_seal(s):
        raise soccer_live.LiveRefused("the sealed international candidate file is not the tested candidate 10547b47…")
    return s


def teams() -> dict:
    return json.loads(TEAMS_FILE.read_text())


def history() -> list:
    """Every listed match of the pinned file (all years: serving, not evaluation), sha256-checked against the manifest."""
    cache = intl_eval.cache_dir() / DATA_COMMIT
    manifest = json.loads((cache / "manifest.json").read_text())
    files = {}
    for n in intl_eval.FILES:
        b = (cache / n).read_bytes()
        if intl_eval.sha(b) != manifest["files"][n]:
            raise soccer_live.LiveRefused(f"{n} differs from its pinned manifest")
        files[n] = b
    return intl_eval.parse(files)


def match_day(kickoff_at: str) -> str:
    return (_t(kickoff_at) - DAY_SHIFT).date().isoformat()


def espn_rows(conn: sqlite3.Connection, after_day: str) -> list:
    q = ",".join("?" * len(COMPETITIONS))
    rows = conn.execute(f"SELECT m.*, ht.name AS home_name, at.name AS away_name FROM soccer_matches m "
                        f"JOIN teams ht ON ht.team_id = m.home_team_id JOIN teams at ON at.team_id = m.away_team_id "
                        f"WHERE m.competition_id IN ({q}) ORDER BY m.kickoff_at, m.match_id", COMPETITIONS).fetchall()
    return [r for r in rows if match_day(r["kickoff_at"]) > after_day]


def check(conn, competition: str, now: str, horizon: str, rows: list, team_map: dict, file_end: str) -> dict:
    problems = []
    last = soccer.last_refresh_at(conn, competition)
    if last is None or _t(now) - _t(last) > MAX_AGE:
        problems.append(f"ESPN data last retrieved {last or 'never'}; older than 26 h")
    fetched = set(soccer.fetched_dates(conn, competition))
    d, end, missing = date.fromisoformat(file_end) + timedelta(days=1), _t(horizon).date(), []
    while d < end:
        if d.isoformat() not in fetched:
            missing.append(d.isoformat())
        d += timedelta(days=1)
    if missing:
        problems.append(f"{len(missing)} date(s) since the history ends never fetched (first {missing[0]})")
    unmapped = set()
    for r in rows:
        if r["competition_id"] != competition:
            continue
        for team, name in ((r["home_team_id"], r["home_name"]), (r["away_team_id"], r["away_name"])):
            if team not in team_map["espn"]:
                unmapped.add(f"{name} ({team})")
        # Unsettled earlier matches are handled per target (affected_teams), not as a refusal of the competition.
    if unmapped:
        problems.append("teams not in the team map: " + ", ".join(sorted(unmapped)))
    return {"competition": competition, "ok": not problems, "problems": problems, "retrieved_at": last}


def affected_teams(inputs: list, pending: list, day: str) -> set:
    """Teams whose rating at `day` could still change: the teams of every unsettled match dated before `day`, and every
    team that plays one of them afterwards (before `day`). This Elo is pairwise (no pooled or league-level terms), so a
    target with neither team affected has exactly the forecast it would have once everything before `day` is settled."""
    affected = {t for m in pending if m.day < day for t in (m.home, m.away)}
    if not affected:
        return affected
    start = min(m.day for m in pending if m.day < day)
    for m in sorted([m for m in inputs if start <= m.day < day], key=lambda m: (m.day, m.mid)):
        if m.home in affected or m.away in affected:
            affected |= {m.home, m.away}
    return affected


def forecast_core(sealed: dict, inputs: list, targets: list, day: str) -> dict:
    """The sealed model after observing, in date order, every usable result dated before `day` (exactly what
    intl_eval.walk does before forecasting `day`); then each target's probabilities."""
    model = intl_eval.model_of(sealed)
    for m in sorted([m for m in inputs if m.label is not None and m.day < day], key=lambda m: (m.day, m.mid)):
        model.observe(m)
    return {m.mid: tuple(float(v) for v in model.predict(m)) for m in targets}


def run(conn: sqlite3.Connection, *, day: Optional[str] = None, write: bool = True, record_refusal: bool = True) -> dict:
    now = utc_now()
    day = day or _t(now).date().isoformat()
    cutoff = intl_eval.cutoff_for(day)
    report = {"as_of": now, "date": day, "cutoff_at": cutoff, "competitions": {}}
    if now > cutoff or day < _t(now).date().isoformat():
        report.update(status="refused", reason=f"after the cutoff {cutoff}: a live forecast is never made after its cutoff")
        return report
    sealed, team_map = sealed_candidate(), teams()
    hist = history()
    file_end = max(m.day for m in hist if m.hs is not None)
    rows = espn_rows(conn, file_end)
    checks = {c: check(conn, c, now, cutoff, rows, team_map, file_end) for c in COMPETITIONS}
    shared = [f"input competition {c}: {p}" for c in COMPETITIONS for p in checks[c]["problems"]]
    probs, digest, through = {}, None, None
    if not shared:
        current, inputs_current, pending = [], [], []
        tmap = team_map["espn"]
        for r in rows:
            g, d = GROUP[r["competition_id"]], match_day(r["kickoff_at"])
            neutral = bool(r["neutral_site"])
            if r["status"] == "final" and d < day:
                hs, as_ = r["home_score"], r["away_score"]
                label = 1 if r["decided_in"] in ("extra_time", "penalties") else (0 if hs > as_ else (1 if hs == as_ else 2))
                current.append(intl_eval.Match(r["match_id"], d, tmap[r["home_team_id"]], tmap[r["away_team_id"]], neutral,
                                               r["competition_id"], g, hs, as_, label, None))
                inputs_current.append((r["match_id"], r["result_available_at"], hs, as_, r["decided_in"]))
            elif d < day and r["status"] not in ("final", "postponed", "canceled", "abandoned"):
                pending.append(intl_eval.Match(r["match_id"], d, tmap[r["home_team_id"]], tmap[r["away_team_id"]], neutral,
                                               r["competition_id"], g, None, None, None, "pending"))
            elif d == day and r["status"] == "scheduled" and r["competition_id"] in LAUNCH:
                current.append(intl_eval.Match(r["match_id"], d, tmap[r["home_team_id"]], tmap[r["away_team_id"]], neutral,
                                               r["competition_id"], g, None, None, None, "target"))
        waiting = affected_teams(hist + current, pending, day)
        ready = [m for m in current if m.exclusion == "target" and m.home not in waiting and m.away not in waiting]
        report["waiting_on_unsettled"] = sorted(m.mid for m in current if m.exclusion == "target" and m not in ready)
        probs = forecast_core(sealed, hist + current, ready, day)
        digest = hashlib.sha256(json.dumps([CANDIDATE_SHA256, DATA_COMMIT, sorted(inputs_current)]).encode()).hexdigest()
        # Results through: the later of the pinned history (its last date, available the next day) and ESPN since then.
        through = max([intl_eval.available_at(file_end), *(x[1] for x in inputs_current if x[1] and x[1] <= cutoff)])
    comp_of = {r["match_id"]: r["competition_id"] for r in rows}
    for c in LAUNCH:
        ok = not shared
        mine = [(mid, p) for mid, p in probs.items() if comp_of[mid] == c] if ok else []
        inserted = 0
        for mid, p in mine:
            inserted += _record(conn, mid, c, p, cutoff=cutoff, digest=digest, through=through,
                                retrieved=checks[c]["retrieved_at"], write=write)
        problems = checks[c]["problems"] + [x for x in shared if not x.startswith(f"input competition {c}:")]
        if write and (ok or record_refusal):
            conn.execute("INSERT INTO soccer_live_checks (checked_at, competition_id, match_date, ok, problems_json, forecasts) "
                         "VALUES (?, ?, ?, ?, ?, ?)", (now, c, day, int(ok), json.dumps(problems), inserted))
        report["competitions"][c] = {"ok": ok, "problems": problems, "matches": len(mine), "recorded": inserted}
    report["status"] = "ok" if not shared else "refused"
    return report


def _record(conn, match_id, competition, p, *, cutoff, digest, through, retrieved, write) -> int:
    key = hashlib.sha256(json.dumps([match_id, soccer_live.MARKET, cutoff, CANDIDATE_SHA256, digest]).encode()).hexdigest()
    old = conn.execute("SELECT p_home, p_draw, p_away FROM soccer_live_forecasts WHERE idempotency_key = ?", (key,)).fetchone()
    if old:
        if tuple(old) != tuple(p):
            raise ValueError(f"same inputs, different probabilities for {match_id}")
        return 0
    latest = conn.execute("SELECT p_home, p_draw, p_away, cutoff_at FROM soccer_live_forecasts WHERE match_id = ? AND "
                          "market = ? ORDER BY version DESC LIMIT 1", (match_id, soccer_live.MARKET)).fetchone()
    if latest and tuple(latest[:3]) == tuple(p) and latest[3] == cutoff:
        return 0                                   # unchanged forecast: no new version for unrelated new inputs
    if not write:
        return 1
    version = conn.execute("SELECT IFNULL(MAX(version), 0) + 1 FROM soccer_live_forecasts WHERE match_id = ? AND market = ?",
                           (match_id, soccer_live.MARKET)).fetchone()[0]
    conn.execute("INSERT INTO soccer_live_forecasts (match_id, competition_id, market, version, candidate_sha256, model, "
                 "idempotency_key, inputs_digest, inputs_through, source_retrieved_at, p_home, p_draw, p_away, cutoff_at, "
                 "generated_at) VALUES (?, ?, ?, ?, ?, 'intl-elo', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (match_id, competition, soccer_live.MARKET, version, CANDIDATE_SHA256, key, digest, through, retrieved,
                  p[0], p[1], p[2], cutoff, utc_now()))
    return 1
