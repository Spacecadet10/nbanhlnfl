"""European soccer data-only store: fixtures, results and matchup facts for seven ESPN competitions.

One store (sports.SOCCER, `soccer.sqlite`, identity 'soccer') holds all seven competitions; every match row carries its
competition and its id embeds it (soccer:<competition>:<ESPN event id>), unique per competition in the database. Matches
live in soccer_* tables (migration 0034), never in `games`, so no NBA/NHL model, ledger or served path can read them.

Results keep soccer's distinctions: a draw is a result; how a match was decided (regulation, extra time, penalties) comes
from ESPN's status; the shoot-out, the leg and the aggregate/qualifier of a two-legged tie are separate facts. ESPN's
score fields include extra time; a 90-minute score could be derived from goal minutes (S1 report) but that rule is not
validated yet, so the regulation result after extra time is reported as unavailable. A qualifier is never presented as
the winner of the match.

There is no soccer model: no forecast, odds, injury or metric exists here and none is derived. The API reads this store
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
    ("forecasts", "Match forecasts", "No soccer model exists yet. A soccer forecast needs three outcomes (home win, draw, "
                                     "away win) and a separately validated model; none is shown or estimated."),
    ("odds", "Bookmaker odds", "Soccer odds are not collected."),
    ("injuries", "Injuries and line-ups", "Soccer injuries, suspensions and line-ups are not collected."),
    ("performance", "Model performance", "There are no soccer forecasts to grade, so there is no accuracy or calibration record."),
    ("history", "Prediction history", "No soccer forecast has been recorded."),
    ("research", "Research", "No soccer research models, combinations or thresholds exist."),
]

MATCH_SQL = """
    SELECT m.*, h.abbr AS home_abbr, h.name AS home_name, a.abbr AS away_abbr, a.name AS away_name
    FROM soccer_matches m JOIN teams h ON h.team_id = m.home_team_id JOIN teams a ON a.team_id = m.away_team_id"""


LIVE_FORECASTS = ("Match forecasts", "Live: the 90-minute home/draw/away result only, from the sealed draw-aware Elo "
                                     "(held-out test passed for this competition). No totals, both-teams-to-score, double "
                                     "chance, qualification or other soccer market is forecast.")


def capabilities(live: bool = False) -> list[dict]:
    out = [{"id": i, "label": label, "available": False, "reason": reason} for i, label, reason in CAPABILITIES]
    if live:
        out[0] = {"id": "forecasts", "label": LIVE_FORECASTS[0], "available": True, "reason": LIVE_FORECASTS[1]}
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
    return {"sport": "soccer", "competition_id": competition, "competition": COMPETITIONS[competition],
            "competitions": [{"id": c, "label": label, "matches_stored": counts.get(c, 0)} for c, label in COMPETITIONS.items()],
            "default_date": upcoming[0] if upcoming else (dates[-1] if dates else None), "match_dates": dates,
            "fetched_dates": fetched_dates(conn, competition),
            "seasons": [r[0] for r in conn.execute("SELECT DISTINCT season FROM soccer_matches WHERE competition_id = ? "
                                                   "ORDER BY 1", (competition,))],
            "matches_stored": counts.get(competition, 0), "last_refresh_at": last_refresh_at(conn, competition),
            "capabilities": capabilities(_live(conn, competition))}


def _live(conn: sqlite3.Connection, competition: str) -> bool:
    """True when this store records live forecasts and the competition is in their launch scope (app/soccer_live.py)."""
    from . import intl_live, soccer_live
    return (competition in soccer_live.LAUNCH or competition in intl_live.LAUNCH) and soccer_live.has_tables(conn) and conn.execute(
        "SELECT 1 FROM soccer_live_forecasts WHERE competition_id = ? LIMIT 1", (competition,)).fetchone() is not None


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
            "matches": [_match(r) for r in rows], "prev_date": earlier[-1] if earlier else None,
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
    revisions = [dict(x) for x in conn.execute(
        f"SELECT revision, {', '.join(RESULT_FIELDS)}, home_team_id, away_team_id, observed_at, basis FROM soccer_result_revisions "
        "WHERE match_id = ? "
        "ORDER BY revision", (match_id,))]
    return {"match": _match(r), "head_to_head": [_match(x) for x in h2h], "home_recent": recent(r["home_team_id"]),
            "away_recent": recent(r["away_team_id"]), "result_revisions": revisions, "capabilities": capabilities()}
