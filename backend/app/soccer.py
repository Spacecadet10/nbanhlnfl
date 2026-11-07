"""European soccer data-only store: fixtures, results and matchup facts for seven ESPN competitions.

One store (sports.SOCCER, `soccer.sqlite`, identity 'soccer') holds all seven competitions; every match row carries its
competition and its id embeds it (soccer:<competition>:<ESPN event id>), unique per competition in the database. Matches
live in soccer_* tables (migration 0034), never in `games`, so no NBA/NHL model, ledger or served path can read them.

Results keep soccer's distinctions: a draw is a result; how a match was decided (regulation, extra time, penalties) comes
from ESPN's status; the shoot-out, the leg and the aggregate/qualifier of a two-legged tie are separate facts. ESPN
publishes no 90-minute score for a match that went to extra time, so its regulation result is reported as unavailable,
and a qualifier is never presented as the winner of the match.

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
from .ingest import OBSERVED_WINDOW, RESULT_ASSUMED_DELAY, _fmt, _ts, _upsert_team
from .sources import espn

COMPETITIONS = espn.SOCCER_COMPETITIONS
EASTERN = ZoneInfo("America/New_York")
MAX_INGEST_DAYS = 62            # one CLI run requests at most this many days per competition
RESULT_FIELDS = ("decided_in", "home_score", "away_score", "home_shootout", "away_shootout", "aggregate_home",
                 "aggregate_away", "qualified_team_id")


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
        raw_id, _fetch_id = raw_cache.store_fetch(conn, source=espn.SOCCER_SOURCE, endpoint=endpoint,
                                                  request_key=request_key, body=body, retrieved_at=retrieved_at,
                                                  importer_version=espn.SOCCER_IMPORTER_VERSION, cache_dir=cache_dir)
        return {"competition": competition, "date": yyyymmdd,
                **import_raw(conn, raw_id, competition, retrieved_at=retrieved_at, cache_dir=cache_dir)}


def _request_competition(request_key: str) -> str:
    return dict(p.split("=", 1) for p in request_key.split("&"))["competition"]


def import_raw(conn: sqlite3.Connection, raw_id: int, competition: str, *, retrieved_at: str,
               cache_dir: Optional[Path] = None) -> dict:
    """Upserts one cached scoreboard as seen at `retrieved_at`. Must run inside a transaction on a soccer store."""
    _require_soccer(conn)
    rows = espn.parse_soccer_scoreboard(json.loads(raw_cache.load(conn, raw_id, cache_dir)), competition)
    stats = {"raw_id": raw_id, "matches_seen": len(rows), "matches_inserted": 0, "matches_updated": 0, "revisions": 0}
    for m in rows:
        for t in (m.home, m.away):
            _upsert_team(conn, t, retrieved_at)
        outcome = _upsert_match(conn, m, raw_id, retrieved_at)
        if outcome:
            stats[f"matches_{outcome}"] += 1
            stats["revisions"] += _record_revision(conn, m.match_id, raw_id, retrieved_at)
    return stats


def _upsert_match(conn: sqlite3.Connection, m: espn.SoccerMatchRow, raw_id: int, retrieved_at: str) -> Optional[str]:
    """Same ordering rules as ingest._upsert_game: an older response never overwrites a newer one, a final result never
    regresses to unfinished, a final match keeps its kickoff, and a corrected result is only knowable from the retrieval
    that delivered it (its availability moves; result_first_seen_at keeps the first sighting)."""
    row = conn.execute("SELECT m.*, r.first_retrieved_at AS last_retrieved_at FROM soccer_matches m "
                       "JOIN raw_responses r ON r.raw_id = m.last_raw_id WHERE m.match_id = ?", (m.match_id,)).fetchone()
    fields = {"season": m.season, "season_type_id": m.season_type_id, "stage": m.stage, "stage_label": m.stage_label,
              "kickoff_at": m.kickoff_at, "home_team_id": m.home.team_id, "away_team_id": m.away.team_id,
              "neutral_site": None if m.neutral_site is None else int(m.neutral_site), "venue": m.venue,
              "status": m.status, "source_status": m.source_status, "leg": m.leg, "note": m.note,
              **{k: getattr(m, k) for k in RESULT_FIELDS}}
    if m.status == "final":
        tracked = (row is not None and row["status"] != "final"
                   and _ts(row["first_seen_at"]) - _ts(m.kickoff_at) <= OBSERVED_WINDOW)
        observed = tracked or _ts(retrieved_at) - _ts(m.kickoff_at) <= OBSERVED_WINDOW
        availability = {"result_first_seen_at": retrieved_at,
                        "result_available_at": retrieved_at if observed else _fmt(_ts(m.kickoff_at) + RESULT_ASSUMED_DELAY),
                        "result_availability_basis": "observed_retrieval" if observed else "assumed_tipoff_plus_4h"}
    else:
        availability = {"result_first_seen_at": None, "result_available_at": None, "result_availability_basis": None}
    if row is None:
        cols = {**fields, **availability, "match_id": m.match_id, "competition_id": m.competition_id,
                "event_id": m.event_id, "first_seen_at": retrieved_at, "last_raw_id": raw_id, "updated_at": retrieved_at}
        conn.execute(f"INSERT INTO soccer_matches ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                     list(cols.values()))
        return "inserted"
    if row["status"] == "final" and m.status != "final":
        return None
    if retrieved_at < row["last_retrieved_at"]:
        return None
    changes = {k: v for k, v in fields.items() if row[k] != v}
    if row["status"] == "final":
        changes.pop("kickoff_at", None)
        corrected = any(k in changes for k in RESULT_FIELDS)
        availability = ({"result_available_at": retrieved_at, "result_availability_basis": "observed_retrieval"}
                        if corrected and row["result_available_at"] < retrieved_at else {})
    else:
        availability = {k: v for k, v in availability.items() if row[k] != v}
    if not changes and not availability:
        return None
    upd = {**changes, **availability, "last_raw_id": raw_id, "updated_at": retrieved_at}
    conn.execute(f"UPDATE soccer_matches SET {', '.join(f'{k}=?' for k in upd)} WHERE match_id=?",
                 [*upd.values(), m.match_id])
    return "updated"


def _record_revision(conn: sqlite3.Connection, match_id: str, raw_id: int, retrieved_at: str) -> int:
    """Appends the match's current final result when it differs from its latest revision (1 = first result)."""
    g = conn.execute(f"SELECT status, {', '.join(RESULT_FIELDS)} FROM soccer_matches WHERE match_id=?", (match_id,)).fetchone()
    if g is None or g["status"] != "final":
        return 0
    last = conn.execute("SELECT * FROM soccer_result_revisions WHERE match_id=? ORDER BY revision DESC LIMIT 1",
                        (match_id,)).fetchone()
    if last is not None and all(last[k] == g[k] for k in RESULT_FIELDS):
        return 0
    conn.execute(f"INSERT INTO soccer_result_revisions (match_id, revision, {', '.join(RESULT_FIELDS)}, observed_at, raw_id) "
                 f"VALUES (?, ?, {', '.join('?' * len(RESULT_FIELDS))}, ?, ?)",
                 (match_id, 1 if last is None else last["revision"] + 1, *(g[k] for k in RESULT_FIELDS), retrieved_at, raw_id))
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


def replay(conn: sqlite3.Connection, cache_dir: Optional[Path] = None) -> dict:
    """Re-imports every logged soccer retrieval in retrieval order, each at its own fetch time, from the cached bytes."""
    totals = {"responses": 0, "matches_inserted": 0, "matches_updated": 0, "revisions": 0}
    fetches = conn.execute("SELECT f.raw_id, f.retrieved_at, r.request_key FROM raw_fetches f "
                           "JOIN raw_responses r ON r.raw_id = f.raw_id WHERE r.source = ? ORDER BY f.retrieved_at, f.fetch_id",
                           (espn.SOCCER_SOURCE,)).fetchall()
    for f in fetches:
        with transaction(conn):
            s = import_raw(conn, f["raw_id"], _request_competition(f["request_key"]), retrieved_at=f["retrieved_at"],
                           cache_dir=cache_dir)
        totals["responses"] += 1
        for k in ("matches_inserted", "matches_updated", "revisions"):
            totals[k] += s[k]
    return totals


def coverage(conn: sqlite3.Connection) -> dict:
    """Per competition and season: matches by status, draws, and how finals were decided; plus the requested days."""
    _require_soccer(conn)
    out: dict = defaultdict(dict)
    for r in conn.execute(
            "SELECT competition_id, season, COUNT(*) AS matches, SUM(status = 'final') AS final, "
            "SUM(status = 'scheduled') AS scheduled, SUM(status NOT IN ('final', 'scheduled')) AS not_final, "
            "SUM(status = 'final' AND decided_in = 'regulation' AND home_score = away_score) AS draws, "
            "SUM(decided_in = 'extra_time') AS extra_time, SUM(decided_in = 'penalties') AS penalties "
            "FROM soccer_matches GROUP BY 1, 2 ORDER BY 1, 2"):
        out[r["competition_id"]][str(r["season"])] = {k: r[k] or 0 for k in
                                                      ("matches", "final", "scheduled", "not_final", "draws", "extra_time", "penalties")}
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


def capabilities() -> list[dict]:
    return [{"id": i, "label": label, "available": False, "reason": reason} for i, label, reason in CAPABILITIES]


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
        "decided_in": decided, "home_score": r["home_score"] if final else None, "away_score": r["away_score"] if final else None,
        "regulation_result": regulation, "end_of_play_result": end_of_play, "shootout_winner": shootout,
        "home_shootout": r["home_shootout"], "away_shootout": r["away_shootout"],
        "leg": r["leg"], "aggregate_home": r["aggregate_home"] if two_legged else None,
        "aggregate_away": r["aggregate_away"] if two_legged else None, "qualified_team_id": r["qualified_team_id"],
        "note": r["note"], "first_seen_at": r["first_seen_at"], "updated_at": r["updated_at"],
        "result_available_at": r["result_available_at"], "result_availability_basis": r["result_availability_basis"],
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
            "capabilities": capabilities()}


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
        f"SELECT revision, {', '.join(RESULT_FIELDS)}, observed_at FROM soccer_result_revisions WHERE match_id = ? "
        "ORDER BY revision", (match_id,))]
    return {"match": _match(r), "head_to_head": [_match(x) for x in h2h], "home_recent": recent(r["home_team_id"]),
            "away_recent": recent(r["away_team_id"]), "result_revisions": revisions, "capabilities": capabilities()}
