"""Activity-aware data refresh policy (docs/REFRESH_PLAN.md).

One policy for every store (NBA and NHL). It decides only when schedule/status/result data is
fetched and how wide the request is; it never decides or enables forecasts. A refresh failure never counts as fresh data:
only successful `refresh` job runs move the source time.

Activity of a store at `now`, from its own stored games:
  active   a game is in progress, or past its scheduled start and not final yet (awaiting confirmation from the source),
           within AWAITING_LIMIT of its start; or a game's final result was first seen within CONFIRM_FINAL (one more
           fetch confirms it)
  pregame  a scheduled game starts within PREGAME_LEAD
  idle     otherwise
A store that is active or pregame fetches only the Eastern dates of those games ('dates' scope, a small request); the
broad fetch (ESPN months / the NHL season list) still runs at least every BROAD_INTERVAL so later schedule changes and
corrections keep arriving. Intervals are initial targets (REFRESH_PLAN), not a promise of upstream live coverage.
"""
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
INTERVALS = {"active": timedelta(seconds=60), "pregame": timedelta(minutes=2), "idle": timedelta(minutes=60)}
BROAD_INTERVAL = timedelta(minutes=60)
PREGAME_LEAD = timedelta(hours=2)           # the forecast window length (near_tipoff.WINDOW)
AWAITING_LIMIT = timedelta(hours=6)         # = near_tipoff.RECENT_TIPOFF: an unfinished game this old no longer keeps a store active
CONFIRM_FINAL = timedelta(minutes=10)       # a newly final game keeps the active cadence this long (confirmation fetch)
TICK_SLACK = timedelta(seconds=20)          # a once-a-minute tick starts a few seconds late; due within the slack counts as due
RETRY_BASE = timedelta(minutes=2)           # after a failed refresh: 2, 4, 8, 16 ... minutes, capped below
RETRY_CAP = timedelta(minutes=30)
MAX_DATES = 3                               # date requests per focused refresh


def _parse(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def activity(conn: sqlite3.Connection, now: datetime) -> tuple[str, list[sqlite3.Row]]:
    """(activity, the games that make the store active or pregame). Status is the stored one: a game past its start is
    never treated as in progress here, only as awaiting confirmation (it still gets the active cadence)."""
    n, back, lead = _fmt(now), _fmt(now - AWAITING_LIMIT), _fmt(now + PREGAME_LEAD)
    active = conn.execute(
        "SELECT game_id, tipoff_at, status FROM games WHERE tipoff_at > ? AND tipoff_at <= ? AND status IN ('scheduled', 'in_progress') "
        "UNION SELECT game_id, tipoff_at, status FROM games WHERE status = 'final' AND result_first_seen_at > ? AND tipoff_at > ? "
        "ORDER BY tipoff_at, game_id", (back, n, _fmt(now - CONFIRM_FINAL), back)).fetchall()
    if active:
        return "active", active
    pregame = conn.execute("SELECT game_id, tipoff_at, status FROM games WHERE status = 'scheduled' AND tipoff_at > ? "
                           "AND tipoff_at <= ? ORDER BY tipoff_at, game_id", (n, lead)).fetchall()
    return ("pregame", pregame) if pregame else ("idle", [])


def eastern_dates(games: list[sqlite3.Row]) -> list[str]:
    """Distinct Eastern dates (YYYY-MM-DD) of these games' starts, in order, at most MAX_DATES."""
    out: list[str] = []
    for g in games:
        d = f"{_parse(g['tipoff_at']).astimezone(EASTERN):%Y-%m-%d}"
        if d not in out:
            out.append(d)
    return out[:MAX_DATES]


def _runs(conn: sqlite3.Connection) -> dict:
    """Last successful refresh, last successful broad refresh, and the failed refresh runs since the last success.
    Rows written before this policy have no scope and were broad (months / season list)."""
    ok = conn.execute("SELECT MAX(finished_at) FROM job_runs WHERE job='refresh' AND status='success'").fetchone()[0]
    broad = conn.execute("SELECT MAX(finished_at) FROM job_runs WHERE job='refresh' AND status='success' AND "
                         "(detail IS NULL OR json_extract(detail, '$.scope') IS NULL OR json_extract(detail, '$.scope')='broad')"
                         ).fetchone()[0]
    failed = conn.execute("SELECT COUNT(*), MAX(finished_at) FROM job_runs WHERE job='refresh' AND status='failed' "
                          "AND finished_at > ?", (ok or "",)).fetchone()
    return {"last_success": ok, "last_broad": broad, "failures": failed[0], "last_failure": failed[1]}


def retry_wait(failures: int) -> timedelta:
    return min(RETRY_BASE * (2 ** max(failures - 1, 0)), RETRY_CAP)


def plan(conn: sqlite3.Connection, now: datetime) -> dict:
    """Whether a refresh is due now, its scope ('broad' or 'dates') and when the next one is due. Pure read."""
    act, games = activity(conn, now)
    interval = INTERVALS[act]
    runs = _runs(conn)
    broad_due_at = _parse(runs["last_broad"]) + BROAD_INTERVAL if runs["last_broad"] else now
    if act == "idle":
        due_at, scope = broad_due_at, "broad"
    else:
        due_at = min(_parse(runs["last_success"]) + interval if runs["last_success"] else now, broad_due_at)
        scope = "broad" if broad_due_at <= now + TICK_SLACK else "dates"
    backoff_until = None
    if runs["failures"]:
        backoff_until = _parse(runs["last_failure"]) + retry_wait(runs["failures"])
        due_at = max(due_at, backoff_until) if runs["last_success"] else backoff_until
    return {"activity": act, "interval_seconds": int(interval.total_seconds()), "scope": scope,
            "dates": eastern_dates(games) if scope == "dates" else [], "due": due_at <= now + TICK_SLACK,
            "next_due_at": _fmt(due_at), "backoff_until": _fmt(backoff_until) if backoff_until else None, **runs}
