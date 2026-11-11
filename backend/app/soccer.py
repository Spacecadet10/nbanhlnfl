"""European soccer data-only store: fixtures, results and matchup facts for seven ESPN competitions.

One store (sports.SOCCER, `soccer.sqlite`, identity 'soccer') holds all seven competitions; every match row carries its
competition and its id embeds it (soccer:<competition>:<ESPN event id>), unique per competition in the database. Matches
live in soccer_* tables (migration 0034), never in `games`, so no NBA/NHL model, ledger or served path can read them.

Results keep soccer's distinctions: a draw is a result; how a match was decided (regulation, extra time, penalties) comes
from ESPN's status; the shoot-out, the leg and the aggregate/qualifier of a two-legged tie are separate facts. ESPN's
score fields include extra time; a 90-minute score could be derived from goal minutes (S1 report) but that rule is not
validated yet, so the regulation result after extra time is reported as unavailable. A qualifier is never presented as
the winner of the match.

This module holds match facts only: no forecast, odds, injury or metric is stored or derived here. Live 90-minute
forecasts are recorded separately (app/soccer_live.py, app/intl_live.py); the read views below report where a
competition's forecasts stand (forecast_state) and return them beside, never inside, a match. The API reads this store
through /api/soccer/* only.
"""
import json
import sqlite3
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from . import raw_cache, sports
from .db import StoreMismatch, claim_store, store_sport, transaction, utc_now
from .ingest import OBSERVED_WINDOW, RESULT_ASSUMED_DELAY, _fmt, _ts, _upsert_team, is_newer_retrieval
from .sources import espn

COMPETITIONS = espn.SOCCER_COMPETITIONS
EASTERN = ZoneInfo("America/New_York")
MAX_INGEST_DAYS = 62            # one CLI run requests at most this many days per competition
RESULT_FIELDS = ("decided_in", "home_score", "away_score", "home_shootout", "away_shootout", "aggregate_home",
                 "aggregate_away", "qualified_team_id")
# The result itself: a change of any of these on a final (or of either team) is a correction, knowable only from the
# retrieval that delivered it (result_available_at moves there).
CORE_FIELDS = ("decided_in", "home_score", "away_score")
# Tie facts: learned with or after the result; their own availability is tie_facts_available_at.
TIE_FIELDS = ("home_shootout", "away_shootout", "aggregate_home", "aggregate_away", "qualified_team_id")
# A later response may simply omit `series`, `leg`, `shootoutScore` or `notes`: absence keeps the stored value, but only
# while both teams and the core result are unchanged (otherwise the old facts may belong to another result or side).
KEPT_WHEN_ABSENT = ("leg", "note", *TIE_FIELDS)

def _require_soccer(conn: sqlite3.Connection) -> None:
    current = store_sport(conn)
    if current != sports.SOCCER.id:
        raise StoreMismatch(f"This store holds {current or 'unlabelled'!r} data, not 'soccer'")


def _competition(competition: str) -> str:
    if competition not in COMPETITIONS:
        raise ValueError(f"Unknown soccer competition {competition!r}; expected one of {', '.join(COMPETITIONS)}")
    return competition


# --- Ingestion: network -> raw cache (bytes kept as delivered) -> idempotent import of the cached bytes. ---
def days(start: str, end: str) -> list[str]:
    a, b = datetime.strptime(start, "%Y%m%d").date(), datetime.strptime(end, "%Y%m%d").date()
    if b < a:
        raise ValueError("--to is before --from")
    n = (b - a).days + 1
    if n > MAX_INGEST_DAYS:
        raise ValueError(f"{n} days requested; at most {MAX_INGEST_DAYS} per run")
    return [(a + timedelta(days=i)).strftime("%Y%m%d") for i in range(n)]


def ingest_day(conn: sqlite3.Connection, competition: str, yyyymmdd: str, *,
               fetch: Callable[[str, str], bytes] = espn.fetch_soccer_day, retrieved_at: Optional[str] = None,
               cache_dir: Optional[Path] = None) -> dict:
    """Fetches one competition's scoreboard for one day, caches the exact bytes with their retrieval time, imports them.
    A payload naming another competition is rejected before anything is written."""
    request_key, endpoint, _ = espn.soccer_request(_competition(competition), yyyymmdd)
    body = fetch(competition, yyyymmdd)
    espn.parse_soccer_scoreboard(json.loads(body), competition)     # validate first: a mismatch writes nothing
    retrieved_at = retrieved_at or utc_now()
    with transaction(conn):
        claim_store(conn, sports.SOCCER.id)
        raw_id, fetch_id = raw_cache.store_fetch(conn, source=espn.SOCCER_SOURCE, endpoint=endpoint,
                                                 request_key=request_key, body=body, retrieved_at=retrieved_at,
                                                 importer_version=espn.SOCCER_IMPORTER_VERSION, cache_dir=cache_dir)
        return {"competition": competition, "date": yyyymmdd,
                **import_raw(conn, raw_id, competition, retrieved_at=retrieved_at, fetch_id=fetch_id, cache_dir=cache_dir)}


def _request_competition(request_key: str) -> str:
    return dict(p.split("=", 1) for p in request_key.split("&"))["competition"]


def import_raw(conn: sqlite3.Connection, raw_id: int, competition: str, *, retrieved_at: str,
               fetch_id: Optional[int] = None, cache_dir: Optional[Path] = None) -> dict:
    """Upserts one cached scoreboard as seen at its retrieval (`retrieved_at`, `fetch_id` from raw_fetches). Must run inside
    a transaction on a soccer store. An event the store cannot hold (home and away the same team) is recorded in
    soccer_rejections and skipped; the other matches of the day are still imported."""
    _require_soccer(conn)
    rows = espn.parse_soccer_scoreboard(json.loads(raw_cache.load(conn, raw_id, cache_dir)), competition)
    stats = {"raw_id": raw_id, "matches_seen": len(rows), "matches_inserted": 0, "matches_updated": 0, "revisions": 0,
             "rejected": 0, "reclassified": 0}
    for m in rows:
        if m.home.team_id == m.away.team_id:
            conn.execute("INSERT INTO soccer_rejections (competition_id, event_id, reason, raw_id, retrieved_at) "
                         "VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                         (competition, m.event_id, f"home and away are the same team ({m.home.team_id})", raw_id, retrieved_at))
            stats["rejected"] += 1
            continue
        for t in (m.home, m.away):
            _upsert_team(conn, t, retrieved_at)
        before = conn.execute("SELECT home_team_id, away_team_id FROM soccer_matches WHERE match_id = ?", (m.match_id,)).fetchone()
        outcome = _upsert_match(conn, m, raw_id, retrieved_at, fetch_id)
        if outcome in ("inserted", "updated", "reclassified"):
            stats["reclassified" if outcome == "reclassified" else f"matches_{outcome}"] += 1
            teams_changed = before is not None and tuple(before) != (m.home.team_id, m.away.team_id)
            stats["revisions"] += _record_revision(conn, m.match_id, raw_id, retrieved_at, fetch_id, teams_changed=teams_changed,
                                                   basis="reclassified" if outcome == "reclassified" else "observed")
    return stats


def _upsert_match(conn: sqlite3.Connection, m: espn.SoccerMatchRow, raw_id: int, retrieved_at: str,
                  fetch_id: Optional[int] = None) -> Optional[str]:
    """'inserted', 'updated', 'seen' (a newer retrieval that changed nothing) or 'stale' (not newer than the last retrieval
    that contained the match: skipped as a whole). Rules: a final never regresses to unfinished; a final keeps its kickoff;
    a result correction (core result or either team) is knowable only from the retrieval that delivered it; tie facts get
    their own availability, never earlier than the retrieval that delivered them."""
    row = conn.execute("SELECT * FROM soccer_matches WHERE match_id = ?", (m.match_id,)).fetchone()
    seen = {"last_seen_at": retrieved_at, "last_seen_fetch_id": fetch_id}
    fields = {"season": m.season, "season_type_id": m.season_type_id, "stage": m.stage, "stage_label": m.stage_label,
              "kickoff_at": m.kickoff_at, "home_team_id": m.home.team_id, "away_team_id": m.away.team_id,
              "neutral_site": None if m.neutral_site is None else int(m.neutral_site), "venue": m.venue,
              "status": m.status, "source_status": m.source_status, "leg": m.leg, "note": m.note,
              "decision_note": m.decision_note, **{k: getattr(m, k) for k in RESULT_FIELDS}}
    final = m.status == "final"
    has_ties = final and any(fields[k] is not None for k in TIE_FIELDS)
    if final:
        tracked = (row is not None and row["status"] != "final"
                   and _ts(row["first_seen_at"]) - _ts(m.kickoff_at) <= OBSERVED_WINDOW)
        observed = tracked or _ts(retrieved_at) - _ts(m.kickoff_at) <= OBSERVED_WINDOW
        available = retrieved_at if observed else _fmt(_ts(m.kickoff_at) + RESULT_ASSUMED_DELAY)
        availability = {"result_first_seen_at": retrieved_at, "result_available_at": available,
                        "result_availability_basis": "observed_retrieval" if observed else "assumed_tipoff_plus_4h"}
    else:
        availability = {"result_first_seen_at": None, "result_available_at": None, "result_availability_basis": None}
    if row is None:
        cols = {**fields, **availability, **seen, "match_id": m.match_id, "competition_id": m.competition_id,
                "event_id": m.event_id, "tie_facts_available_at": availability["result_available_at"] if has_ties else None,
                "first_seen_at": retrieved_at, "last_raw_id": raw_id, "updated_at": retrieved_at}
        conn.execute(f"INSERT INTO soccer_matches ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                     list(cols.values()))
        return "inserted"
    if not is_newer_retrieval(retrieved_at, fetch_id, row):
        # Replaying the very retrieval the row was last seen in may re-derive how the match ended from those same cached
        # bytes (rows imported before the ending classifier kept ESPN's status label). Only the ending fields change; no
        # new source information, so availability does not move. Any older retrieval stays skipped.
        same = retrieved_at == row["last_seen_at"] and (fetch_id or 0) == (row["last_seen_fetch_id"] or 0)
        ending = {"decided_in": m.decided_in, "decision_note": m.decision_note}
        if same and final and row["status"] == "final" and any(row[k] != v for k, v in ending.items()):
            if m.decided_in != "penalties":
                ending.update(home_shootout=None, away_shootout=None)
            conn.execute(f"UPDATE soccer_matches SET {', '.join(f'{k}=?' for k in ending)} WHERE match_id=?",
                         [*ending.values(), m.match_id])
            return "reclassified"
        return "stale"
    conn.execute("UPDATE soccer_matches SET last_seen_at = ?, last_seen_fetch_id = ? WHERE match_id = ?",
                 (retrieved_at, fetch_id, m.match_id))
    if row["status"] == "final" and not final:
        return "seen"
    same_sides = (row["home_team_id"], row["away_team_id"]) == (m.home.team_id, m.away.team_id)
    core_changed = row["status"] == "final" and any(row[k] != fields[k] for k in CORE_FIELDS)
    for k in KEPT_WHEN_ABSENT:
        keep = same_sides and (k == "leg" or not core_changed)
        if keep and fields[k] is None and row[k] is not None:
            fields[k] = row[k]
    if not final:
        for k in TIE_FIELDS:
            fields[k] = None
    changes = {k: v for k, v in fields.items() if row[k] != v}
    extra: dict = {}
    if row["status"] == "final":
        changes.pop("kickoff_at", None)
        kicks_corrected = any(row[k] is not None and k in changes for k in ("home_shootout", "away_shootout"))
        corrected = core_changed or not same_sides or kicks_corrected
        if corrected and row["result_available_at"] < retrieved_at:
            extra.update(result_available_at=retrieved_at, result_availability_basis="observed_retrieval")
        if any(k in changes for k in TIE_FIELDS):
            extra["tie_facts_available_at"] = retrieved_at if has_ties else None
    else:
        extra = {k: v for k, v in availability.items() if row[k] != v}
        if final:
            extra["tie_facts_available_at"] = availability["result_available_at"] if has_ties else None
    if not changes and not extra:
        return "seen"
    upd = {**changes, **extra, "last_raw_id": raw_id, "updated_at": retrieved_at}
    conn.execute(f"UPDATE soccer_matches SET {', '.join(f'{k}=?' for k in upd)} WHERE match_id=?",
                 [*upd.values(), m.match_id])
    return "updated"


def _record_revision(conn: sqlite3.Connection, match_id: str, raw_id: int, retrieved_at: str,
                     fetch_id: Optional[int] = None, teams_changed: bool = False, basis: str = "observed") -> int:
    """Appends the match's current final result (with both teams) when it differs from its latest revision. Only called
    for a newer retrieval; a retrieval older than the latest revision never appends (revisions stay in time order). A
    revision written before migration 0035 has no teams recorded: teams are compared only when both rows have them."""
    g = conn.execute(f"SELECT status, home_team_id, away_team_id, {', '.join(RESULT_FIELDS)} FROM soccer_matches "
                     "WHERE match_id=?", (match_id,)).fetchone()
    if g is None or g["status"] != "final":
        return 0
    last = conn.execute("SELECT * FROM soccer_result_revisions WHERE match_id=? ORDER BY revision DESC LIMIT 1",
                        (match_id,)).fetchone()
    if last is not None:
        if retrieved_at < last["observed_at"]:
            return 0
        teams_same = not teams_changed and (last["home_team_id"] is None or
                                            (last["home_team_id"], last["away_team_id"]) == (g["home_team_id"], g["away_team_id"]))
        if teams_same and all(last[k] == g[k] for k in RESULT_FIELDS):
            return 0
    conn.execute(f"INSERT INTO soccer_result_revisions (match_id, revision, {', '.join(RESULT_FIELDS)}, home_team_id, "
                 f"away_team_id, observed_at, raw_id, fetch_id, basis) "
                 f"VALUES (?, ?, {', '.join('?' * len(RESULT_FIELDS))}, ?, ?, ?, ?, ?, ?)",
                 (match_id, 1 if last is None else last["revision"] + 1, *(g[k] for k in RESULT_FIELDS), g["home_team_id"],
                  g["away_team_id"], retrieved_at, raw_id, fetch_id, basis))
    return 1


def ingest_range(conn: sqlite3.Connection, competitions: list[str], start: str, end: str, *, sleep: float = 1.0,
                 fetch: Callable[[str, str], bytes] = espn.fetch_soccer_day, log: Callable[[str], None] = print) -> list[dict]:
    """Day by day for each competition; a failed day is reported and the rest continue (nothing partial is committed)."""
    out, first = [], True
    for competition in competitions:
        for day in days(start, end):
            if not first and sleep:
                time.sleep(sleep)
            first = False
            try:
                stats = ingest_day(conn, competition, day, fetch=fetch)
                stats.pop("raw_id", None)
            except Exception as exc:
                stats = {"competition": competition, "date": day,
                         "error": f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"}
            log(json.dumps(stats))
            out.append(stats)
    return out


def _replay_fetches(conn: sqlite3.Connection) -> list:
    return conn.execute("SELECT f.fetch_id, f.raw_id, f.retrieved_at, r.request_key FROM raw_fetches f "
                        "JOIN raw_responses r ON r.raw_id = f.raw_id WHERE r.source = ? ORDER BY f.retrieved_at, f.fetch_id",
                        (espn.SOCCER_SOURCE,)).fetchall()


def replay_missing(conn: sqlite3.Connection, cache_dir: Optional[Path] = None) -> list[int]:
    """raw_ids of the cached files `replay` would read that are absent; read-only, checked by the CLI before it migrates
    or replays anything (replay commits one transaction per retrieval and would otherwise stop half-way)."""
    return raw_cache.missing_raw_ids(conn, {f["raw_id"] for f in _replay_fetches(conn)}, cache_dir)


def replay(conn: sqlite3.Connection, cache_dir: Optional[Path] = None) -> dict:
    """Re-imports every logged soccer retrieval in retrieval order, each at its own fetch time, from the cached bytes."""
    totals = {"responses": 0, "matches_inserted": 0, "matches_updated": 0, "revisions": 0, "reclassified": 0}
    for f in _replay_fetches(conn):
        with transaction(conn):
            s = import_raw(conn, f["raw_id"], _request_competition(f["request_key"]), retrieved_at=f["retrieved_at"],
                           fetch_id=f["fetch_id"], cache_dir=cache_dir)
        totals["responses"] += 1
        for k in ("matches_inserted", "matches_updated", "revisions", "reclassified"):
            totals[k] += s[k]
    return totals


def coverage(conn: sqlite3.Connection) -> dict:
    """Per competition and season: matches by status, draws, and how finals were decided; plus the requested days."""
    _require_soccer(conn)
    out: dict = defaultdict(dict)
    for r in conn.execute(
            "SELECT competition_id, season, COUNT(*) AS matches, SUM(status = 'final') AS final, "
            "SUM(status = 'scheduled') AS scheduled, SUM(status NOT IN ('final', 'scheduled')) AS not_final, "
            "SUM(status = 'final' AND decided_in = 'regulation' AND home_score = away_score) AS regulation_draws, "
            "SUM(decided_in = 'extra_time') AS extra_time, SUM(decided_in = 'penalties') AS penalties "
            "FROM soccer_matches GROUP BY 1, 2 ORDER BY 1, 2"):
        out[r["competition_id"]][str(r["season"])] = {k: r[k] or 0 for k in
                                                      ("matches", "final", "scheduled", "not_final", "regulation_draws", "extra_time", "penalties")}
    return {"sport": "soccer", "competitions": dict(out),
            "requested_days": {c: len(fetched_dates(conn, c)) for c in COMPETITIONS},
            "raw_responses": conn.execute("SELECT COUNT(*) FROM raw_responses WHERE source = ?",
                                          (espn.SOCCER_SOURCE,)).fetchone()[0]}


# --- Read-only views for /api/soccer/*: facts from the store only. ---
RECENT = 5
HEAD_TO_HEAD = 10

CAPABILITIES = [
    ("forecasts", "Match forecasts", "This data store holds fixtures and results only; it records no live soccer forecasts."),
    ("odds", "Bookmaker odds", "Soccer odds are not collected."),
    ("injuries", "Injuries and line-ups", "Soccer injuries, suspensions and line-ups are not collected."),
    ("performance", "Model performance", "No forecast of this competition has been graded, so there is no accuracy or "
                                         "calibration record."),
    ("history", "Prediction history", "No forecast has been recorded for this competition."),
    ("research", "Research", "No soccer research models, combinations or thresholds exist."),
]
# What the forecasts line says for each state of a competition (forecast_state). The page text follows these, so it can
# never say "no forecasts" beside recorded ones (live-state audit, 2026-11-08).
FORECAST_STATES = ("recorded", "awaiting", "out_of_scope", "no_live_store")
FORECASTS_AWAITING = ("A sealed 90-minute model covers this competition, but no forecast has been recorded for it yet. Each "
                      "match is forecast by 06:00 UTC on its match day, once the earlier results it depends on are stored; "
                      "none is made after that cutoff.")
# One match past its cutoff (or no longer scheduled) with no recorded forecast: closed, whatever the competition's state.
FORECASTS_MATCH_CLOSED = ("No forecast was recorded for this match before its cutoff (06:00 UTC on its match day), and "
                          "none is made after it.")
FORECASTS_OUT_OF_SCOPE = "No live forecast: this competition did not pass its held-out test (too few test matches)."
# With live forecasts recorded, "none recorded / none to grade" would be false; the pages themselves are not built yet.
RECORDED_PAGES = {
    "performance": "Not built for live soccer forecasts yet: no accuracy or calibration figure is computed from them.",
    "history": "Not built for live soccer forecasts yet: recorded forecasts and their 90-minute results are shown on the "
               "match-day page and on each match.",
}

MATCH_SQL = """
    SELECT m.*, h.abbr AS home_abbr, h.name AS home_name, a.abbr AS away_abbr, a.name AS away_name
    FROM soccer_matches m JOIN teams h ON h.team_id = m.home_team_id JOIN teams a ON a.team_id = m.away_team_id"""


LIVE_FORECASTS = ("Match forecasts", "Live: the 90-minute home/draw/away result only, from the sealed Elo model for this "
                                     "competition (held-out test passed). No totals, both-teams-to-score, double "
                                     "chance, qualification or other soccer market is forecast.")


def capabilities(state: str = "no_live_store") -> list[dict]:
    """The capability lines for a competition whose forecasts are in `state` (forecast_state)."""
    if state not in FORECAST_STATES:
        raise ValueError(f"unknown soccer forecast state {state!r}")
    out = [{"id": i, "label": label, "available": False, "reason": reason} for i, label, reason in CAPABILITIES]
    if state == "recorded":
        out[0] = {"id": "forecasts", "label": LIVE_FORECASTS[0], "available": True, "reason": LIVE_FORECASTS[1]}
        out = [dict(c, reason=RECORDED_PAGES.get(c["id"], c["reason"])) for c in out]
    elif state == "awaiting":
        out[0]["reason"] = FORECASTS_AWAITING
    elif state == "out_of_scope":
        out[0]["reason"] = FORECASTS_OUT_OF_SCOPE
    return out


def _side(score_h: Optional[int], score_a: Optional[int]) -> Optional[str]:
    if score_h is None or score_a is None:
        return None
    return "draw" if score_h == score_a else "home" if score_h > score_a else "away"


def _match(r: sqlite3.Row) -> dict:
    final = r["status"] == "final"
    decided = r["decided_in"] if final else None
    # The 90-minute result is known only when the match ended in regulation; extra time means it was not recorded.
    regulation = _side(r["home_score"], r["away_score"]) if decided == "regulation" else None
    # Separate facts, never merged into one "winner": the score when play ended (after extra time if played), the shoot-out
    # (which decides a tie or a final, not the goals), and the qualifier of a two-legged tie.
    end_of_play = _side(r["home_score"], r["away_score"]) if final else None
    shootout = _side(r["home_shootout"], r["away_shootout"]) if decided == "penalties" else None
    two_legged = r["aggregate_home"] is not None
    return {
        "match_id": r["match_id"], "competition_id": r["competition_id"], "competition": COMPETITIONS[r["competition_id"]],
        "event_id": r["event_id"], "season": r["season"], "season_type_id": r["season_type_id"], "stage": r["stage"],
        "stage_label": r["stage_label"], "kickoff": r["kickoff_at"], "status": r["status"], "source_status": r["source_status"],
        "home": {"team_id": r["home_team_id"], "abbr": r["home_abbr"], "name": r["home_name"]},
        "away": {"team_id": r["away_team_id"], "abbr": r["away_abbr"], "name": r["away_name"]},
        "neutral_site": None if r["neutral_site"] is None else bool(r["neutral_site"]), "venue": r["venue"],
        "decided_in": decided, "decision_note": r["decision_note"] if final else None,
        "home_score": r["home_score"] if final else None, "away_score": r["away_score"] if final else None,
        "regulation_result": regulation, "end_of_play_result": end_of_play, "shootout_winner": shootout,
        "home_shootout": r["home_shootout"], "away_shootout": r["away_shootout"],
        "leg": r["leg"], "aggregate_home": r["aggregate_home"] if two_legged else None,
        "aggregate_away": r["aggregate_away"] if two_legged else None, "qualified_team_id": r["qualified_team_id"],
        "note": r["note"], "first_seen_at": r["first_seen_at"], "updated_at": r["updated_at"],
        "result_available_at": r["result_available_at"], "result_availability_basis": r["result_availability_basis"],
        "tie_facts_available_at": r["tie_facts_available_at"],
    }


def _eastern_date(iso: str) -> str:
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(EASTERN).date().isoformat()


def _day_bounds(day: date) -> tuple[str, str]:
    start = datetime(day.year, day.month, day.day, tzinfo=EASTERN)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return start.astimezone(timezone.utc).strftime(fmt), (start + timedelta(days=1)).astimezone(timezone.utc).strftime(fmt)


def match_dates(conn: sqlite3.Connection, competition: str) -> list[str]:
    return sorted({_eastern_date(r[0]) for r in conn.execute("SELECT kickoff_at FROM soccer_matches WHERE competition_id = ?",
                                                             (competition,))})


def fetched_dates(conn: sqlite3.Connection, competition: str) -> list[str]:
    """Scoreboard days requested for this competition (YYYY-MM-DD), whatever they returned: a date not in this list was
    never asked for, so an empty day there says nothing about whether matches exist."""
    keys = conn.execute("SELECT DISTINCT request_key FROM raw_responses WHERE source = ? AND request_key LIKE ?",
                        (espn.SOCCER_SOURCE, f"competition={competition}&%")).fetchall()
    out = set()
    for (k,) in keys:
        d = dict(p.split("=", 1) for p in k.split("&")).get("dates", "")
        if len(d) == 8:
            out.add(f"{d[:4]}-{d[4:6]}-{d[6:]}")
    return sorted(out)


def last_refresh_at(conn: sqlite3.Connection, competition: str) -> Optional[str]:
    return conn.execute("SELECT MAX(f.retrieved_at) FROM raw_fetches f JOIN raw_responses r ON r.raw_id = f.raw_id "
                        "WHERE r.source = ? AND r.request_key LIKE ?",
                        (espn.SOCCER_SOURCE, f"competition={competition}&%")).fetchone()[0]


def meta(conn: sqlite3.Connection, competition: str, today: date) -> dict:
    _require_soccer(conn)
    _competition(competition)
    dates = match_dates(conn, competition)
    t = today.isoformat()
    upcoming = [d for d in dates if d >= t]
    counts = {r[0]: r[1] for r in conn.execute("SELECT competition_id, COUNT(*) FROM soccer_matches GROUP BY 1")}
    state = forecast_state(conn, competition)
    return {"sport": "soccer", "competition_id": competition, "competition": COMPETITIONS[competition],
            "competitions": [{"id": c, "label": label, "matches_stored": counts.get(c, 0)} for c, label in COMPETITIONS.items()],
            "default_date": upcoming[0] if upcoming else (dates[-1] if dates else None), "match_dates": dates,
            "fetched_dates": fetched_dates(conn, competition),
            "seasons": [r[0] for r in conn.execute("SELECT DISTINCT season FROM soccer_matches WHERE competition_id = ? "
                                                   "ORDER BY 1", (competition,))],
            "matches_stored": counts.get(competition, 0), "last_refresh_at": last_refresh_at(conn, competition),
            "forecast_state": state, "capabilities": capabilities(state)}


def forecast_state(conn: sqlite3.Connection, competition: str) -> str:
    """Where this competition's live forecasts stand in this store (app/soccer_live.py, app/intl_live.py):
    'recorded' (forecasts exist), 'awaiting' (in the sealed scope, none recorded yet), 'out_of_scope' (not forecast: it did
    not pass its held-out test) or 'no_live_store' (the store has no live forecast tables)."""
    from . import intl_live, soccer_live
    if not soccer_live.has_tables(conn):
        return "no_live_store"
    if competition not in soccer_live.LAUNCH and competition not in intl_live.LAUNCH:
        return "out_of_scope"
    recorded = conn.execute("SELECT 1 FROM soccer_live_forecasts WHERE competition_id = ? LIMIT 1", (competition,)).fetchone()
    return "recorded" if recorded else "awaiting"


def forecast_cutoff(competition: str, kickoff_at: str) -> Optional[str]:
    """The time by which this match's live forecast must be recorded (none is ever made after it), by the rule of the
    model that covers the competition; None for a competition that is never forecast."""
    from . import intl_eval, intl_live, soccer_eval, soccer_live
    if competition in intl_live.LAUNCH:
        return intl_eval.cutoff_for(intl_live.match_day(kickoff_at))
    if competition in soccer_live.LAUNCH:
        return soccer_eval.cutoff_for(soccer_live.local_date(kickoff_at, competition))
    return None


def forecast_day_of(competition: str, kickoff_at: str) -> Optional[str]:
    """The match date its live checks are recorded under (soccer_live_checks.match_date); None if never forecast."""
    from . import intl_live, soccer_live
    if competition in intl_live.LAUNCH:
        return intl_live.match_day(kickoff_at)
    if competition in soccer_live.LAUNCH:
        return soccer_live.local_date(kickoff_at, competition)
    return None


def forecast_blockers(conn: sqlite3.Connection, rows: list, forecasts: dict, now: Optional[str] = None) -> dict:
    """match_id -> why the match has no recorded live forecast, from stored facts only (read-only; never a guess):
    {"code", "text", "checked_at"?, "problems"?}. Matches with a recorded forecast are absent.
    Codes: no_live_store, out_of_scope, void, listed_after_cutoff, refused, missed, waiting, not_checked."""
    from . import intl_live, soccer_live
    now = now or soccer_live.utc_now()
    live = soccer_live.has_tables(conn)
    checks: dict = {}
    out = {}
    for r in rows:
        mid, comp = r["match_id"], r["competition_id"]
        if mid in forecasts:
            continue
        if not live:
            out[mid] = {"code": "no_live_store", "text": "This data store records no live soccer forecasts."}
            continue
        cutoff = forecast_cutoff(comp, r["kickoff_at"])
        club_out = comp in soccer_live.LAUNCH and comp.startswith("uefa.") and r["stage"] not in soccer_live.UEFA_STAGES
        if cutoff is None or club_out:
            why = ("its stage is outside the model's tested scope (league phase only)" if club_out
                   else "this competition is outside the live models' tested scope")
            out[mid] = {"code": "out_of_scope", "text": f"Not forecast: {why}."}
            continue
        if r["status"] in ("postponed", "canceled", "abandoned"):
            out[mid] = {"code": "void", "text": f"Not forecast: the match is {r['status']}."}
            continue
        if r["first_seen_at"] and r["first_seen_at"] > cutoff:
            out[mid] = {"code": "listed_after_cutoff",
                        "text": f"Not forecast: ESPN data first listed this match at {r['first_seen_at']}, after its forecast "
                                f"cutoff {cutoff}. No forecast is ever made after the cutoff."}
            continue
        day = forecast_day_of(comp, r["kickoff_at"])
        key = (comp, day)
        if key not in checks:
            c = conn.execute("SELECT checked_at, ok, problems_json FROM soccer_live_checks WHERE competition_id = ? AND "
                             "match_date = ? ORDER BY check_id DESC LIMIT 1", key).fetchone()
            checks[key] = c
        c = checks[key]
        passed = now >= cutoff
        if not passed:                  # a newer refusal of the competition (shared inputs) outranks an older pass
            if comp not in checks:
                checks[comp] = conn.execute("SELECT checked_at, ok, problems_json, match_date FROM soccer_live_checks WHERE "
                                            "competition_id = ? ORDER BY check_id DESC LIMIT 1", (comp,)).fetchone()
            newest = checks[comp]
            if newest is not None and not newest["ok"] and (c is None or newest["checked_at"] > c["checked_at"]):
                problems = json.loads(newest["problems_json"])
                out[mid] = {"code": "refused", "checked_at": newest["checked_at"], "problems": problems,
                            "text": f"Blocked: the latest input check for this competition ({newest['checked_at']}, for "
                                    f"{newest['match_date']}) refused its inputs: " + "; ".join(problems) + ". Forecasts "
                                    f"resume once that is fixed, if before the cutoff {cutoff}."}
                continue
        if c is None:
            out[mid] = ({"code": "missed", "text": f"No forecast: no completed input check was recorded for its date before "
                                                   f"the cutoff {cutoff}, and none is made after it."} if passed else
                        {"code": "not_checked", "text": f"No completed input check for its date yet. Forecasts are attempted "
                                                        f"hourly from 7 days before the match day until the cutoff {cutoff}."})
            continue
        problems = json.loads(c["problems_json"])
        if not c["ok"]:
            out[mid] = {"code": "refused", "checked_at": c["checked_at"], "problems": problems,
                        "text": ("No forecast: " if passed else "Blocked: ") + f"the input check at {c['checked_at']} refused "
                                f"its date: " + "; ".join(problems) + "."}
            continue
        if comp in intl_live.LAUNCH:
            reason = ("an earlier match involving one of its teams (or a team they have since played) was not settled "
                      "yet, so its rating could still change")
        else:
            reason = "this match was not among those forecast at that check; the hourly run retries until the cutoff"
        out[mid] = ({"code": "missed", "checked_at": c["checked_at"],
                     "text": f"No forecast before the cutoff {cutoff}: the last check for its date ({c['checked_at']}) found "
                             f"no input problem, but no forecast was recorded for this match."} if passed else
                    {"code": "waiting", "checked_at": c["checked_at"],
                     "text": f"Waiting: the last check for its date ({c['checked_at']}) passed, but {reason}. It is forecast "
                             f"once that is settled, if before the cutoff {cutoff}."})
    return out


def live_forecasts_of(conn: sqlite3.Connection, match_ids: list) -> dict:
    """match_id -> its latest recorded live forecast (soccer_live.forecast_dict), for the matches that have one."""
    from . import soccer_live
    if not match_ids or not soccer_live.has_tables(conn):
        return {}
    as_of, out = soccer_live.utc_now(), {}
    for i in range(0, len(match_ids), 500):
        chunk = match_ids[i:i + 500]
        for r in conn.execute(soccer_live.LATEST_SQL + f"AND f.match_id IN ({','.join('?' * len(chunk))})", chunk):
            out[r["match_id"]] = soccer_live.forecast_dict(r, as_of)
    return out


RECORD_LIMITATIONS = (
    "Every recorded live forecast is listed, including superseded versions; none is hidden for performing poorly. "
    "The model's pick is its most likely 90-minute outcome. A match that went to extra time or penalties is not graded "
    "(the 90-minute score is not validated). The models were tested once on held-out seasons; a short live record says "
    "little, and none of this is a betting edge.")


def forecast_record(conn: sqlite3.Connection, competition: Optional[str] = None, match_id: Optional[str] = None) -> dict:
    """Every recorded live forecast version (append-only soccer_live_forecasts), newest kickoff first, each with its
    probabilities, pick, kickoff and eventual 90-minute result; plus a summary over each match's latest pre-cutoff version.
    Read-only; a result counts only once stored (result_available_at <= now)."""
    import math
    from . import soccer_live
    now = soccer_live.utc_now()
    if not soccer_live.has_tables(conn):
        return {"as_of": now, "competition": competition, "forecasts": [], "summary": None, "limitations": RECORD_LIMITATIONS}
    where = [(c, v) for c, v in (("f.competition_id = ?", competition), ("f.match_id = ?", match_id)) if v]
    q = soccer_live.BASE_SQL + ("WHERE " + " AND ".join(c for c, _ in where) + " " if where else "") + \
        "ORDER BY m.kickoff_at DESC, f.match_id, f.version DESC"
    rows = conn.execute(q, tuple(v for _, v in where)).fetchall()
    latest = {}
    for r in rows:
        latest.setdefault(r["match_id"], r["version"])
    out, graded, correct, logloss = [], 0, 0, 0.0
    for r in rows:
        f = soccer_live.forecast_dict(r, now)
        f["version"] = r["version"]
        f["latest"] = r["version"] == latest[r["match_id"]]
        f["model"] = r["model"]
        f["match_status"] = r["status"]
        f["pick_correct"] = None if f["result"] is None else f["result"] == f["most_likely"]
        if f["latest"] and f["result"] is not None:
            graded += 1
            correct += f["pick_correct"]
            logloss -= math.log(max(f["probabilities"][f["result"]], 1e-12))
        out.append(f)
    summary = {"matches_forecast": len(latest), "versions": len(rows), "graded": graded, "picks_correct": correct,
               "mean_log_loss": logloss / graded if graded else None, "uniform_log_loss": math.log(3)}
    return {"as_of": now, "competition": competition, "forecasts": out, "summary": summary,
            "limitations": RECORD_LIMITATIONS}


def refresh_state(conn: sqlite3.Connection, competition: str, now: datetime) -> dict:
    """How old this competition's ESPN data is against the age beyond which a live forecast is refused (soccer_live.MAX_AGE)."""
    from . import soccer_live
    last = last_refresh_at(conn, competition)
    return {"last_refresh_at": last, "max_age_hours": int(soccer_live.MAX_AGE.total_seconds() // 3600),
            "stale": last is None or now - _ts(last) > soccer_live.MAX_AGE}


def matches_on(conn: sqlite3.Connection, competition: str, day: date) -> dict:
    """One competition's matches whose kickoff falls on this Eastern date, with the nearest earlier/later match dates."""
    _require_soccer(conn)
    _competition(competition)
    lo, hi = _day_bounds(day)
    rows = conn.execute(f"{MATCH_SQL} WHERE m.competition_id = ? AND m.kickoff_at >= ? AND m.kickoff_at < ? "
                        "ORDER BY m.kickoff_at, m.match_id", (competition, lo, hi)).fetchall()
    dates, d = match_dates(conn, competition), day.isoformat()
    earlier, later = [x for x in dates if x < d], [x for x in dates if x > d]
    return {"competition_id": competition, "date": d, "fetched": d in fetched_dates(conn, competition),
            "matches": [_match(r) for r in rows], "live_forecasts": live_forecasts_of(conn, [r["match_id"] for r in rows]),
            "forecast_cutoffs": {r["match_id"]: c for r in rows if (c := forecast_cutoff(competition, r["kickoff_at"]))},
            "forecast_blockers": forecast_blockers(conn, rows, live_forecasts_of(conn, [r["match_id"] for r in rows])),
            "prev_date": earlier[-1] if earlier else None,
            "next_date": later[0] if later else None, "last_refresh_at": last_refresh_at(conn, competition)}


def match_detail(conn: sqlite3.Connection, match_id: str) -> Optional[dict]:
    """One match, earlier meetings of the two teams and each team's latest results before this kickoff — all within the
    same competition — and every recorded version of its result."""
    _require_soccer(conn)
    r = conn.execute(f"{MATCH_SQL} WHERE m.match_id = ?", (match_id,)).fetchone()
    if r is None:
        return None
    before = "m.competition_id = ? AND m.status = 'final' AND m.kickoff_at < ?"
    h2h = conn.execute(f"{MATCH_SQL} WHERE {before} AND ((m.home_team_id = ? AND m.away_team_id = ?) OR "
                       "(m.home_team_id = ? AND m.away_team_id = ?)) ORDER BY m.kickoff_at DESC LIMIT ?",
                       (r["competition_id"], r["kickoff_at"], r["home_team_id"], r["away_team_id"], r["away_team_id"],
                        r["home_team_id"], HEAD_TO_HEAD)).fetchall()

    def recent(team: str) -> list[dict]:
        return [_match(x) for x in conn.execute(
            f"{MATCH_SQL} WHERE {before} AND (m.home_team_id = ? OR m.away_team_id = ?) ORDER BY m.kickoff_at DESC LIMIT ?",
            (r["competition_id"], r["kickoff_at"], team, team, RECENT))]
    state = forecast_state(conn, r["competition_id"])
    revisions = [dict(x) for x in conn.execute(
        f"SELECT revision, {', '.join(RESULT_FIELDS)}, home_team_id, away_team_id, observed_at, basis FROM soccer_result_revisions "
        "WHERE match_id = ? "
        "ORDER BY revision", (match_id,))]
    live = live_forecasts_of(conn, [match_id]).get(match_id)
    cutoff = forecast_cutoff(r["competition_id"], r["kickoff_at"])
    caps = capabilities(state)
    closed = r["status"] != "scheduled" or (cutoff is not None and datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ") >= cutoff)
    if live is None and closed and state in ("awaiting", "recorded"):   # never "not recorded yet" nor "available" here
        caps[0] = {"id": "forecasts", "label": "Match forecasts", "available": False, "reason": FORECASTS_MATCH_CLOSED}
    return {"match": _match(r), "head_to_head": [_match(x) for x in h2h], "home_recent": recent(r["home_team_id"]),
            "away_recent": recent(r["away_team_id"]), "result_revisions": revisions, "live_forecast": live,
            "forecast_cutoff_at": cutoff, "forecast_state": state, "capabilities": caps,
            "forecast_blocker": forecast_blockers(conn, [r], {match_id: live} if live else {}).get(match_id),
            "forecast_versions": forecast_record(conn, match_id=match_id)["forecasts"]}
