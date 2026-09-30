"""Bounded operational jobs: refresh → predict → score, with retries, request quotas and a job_runs log."""
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from . import ingest, live
from .db import utc_now
from .sources import espn

EASTERN = ZoneInfo("America/New_York")
MIN_REFRESH_INTERVAL = timedelta(minutes=30)   # quota: at most one successful refresh per 30 minutes
MAX_REQUESTS_PER_REFRESH = 3                   # quota: months fetched per refresh
MIN_ODDS_INTERVAL = timedelta(minutes=10)      # quota: at most one successful odds snapshot per 10 minutes
ODDS_WINDOW = timedelta(hours=36)              # snapshot days with a scheduled game tipping off this soon
MAX_REQUESTS_PER_ODDS = 2                      # quota: day scoreboards fetched per snapshot
SCHEDULER_MIN_INTERVAL = {"daily": 30, "odds": 10}  # minutes
RETRIES = 3
BACKOFF_SECONDS = (2, 8)


def _parse(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _start(conn: sqlite3.Connection, job: str) -> int:
    return conn.execute("INSERT INTO job_runs (job, started_at, status) VALUES (?, ?, 'running')",
                        (job, utc_now())).lastrowid


def _finish(conn: sqlite3.Connection, run_id: int, status: str, attempts: int, detail: dict) -> dict:
    conn.execute("UPDATE job_runs SET finished_at=?, status=?, attempts=?, detail=? WHERE run_id=?",
                 (utc_now(), status, attempts, json.dumps(detail, default=str), run_id))
    return {"run_id": run_id, "status": status, "attempts": attempts, **detail}


def last_success(conn: sqlite3.Connection, job: str) -> Optional[str]:
    row = conn.execute("SELECT MAX(finished_at) FROM job_runs WHERE job=? AND status='success'", (job,)).fetchone()
    return row[0]


def refresh_months(now: datetime) -> list[str]:
    """Eastern months covering yesterday (late results) through the next 8 days (upcoming schedule)."""
    start, end = (now - timedelta(days=1)).astimezone(EASTERN), (now + timedelta(days=8)).astimezone(EASTERN)
    return ingest.months(f"{start:%Y%m}", f"{end:%Y%m}")


def refresh(conn: sqlite3.Connection, *, force: bool = False, fetch: Callable[[str], bytes] = espn.fetch_month,
            sleep: Callable[[float], None] = time.sleep, now: Optional[datetime] = None) -> dict:
    run_id = _start(conn, "refresh")
    now = now or datetime.now(timezone.utc)
    last = last_success(conn, "refresh")
    if last and not force and now - _parse(last) < MIN_REFRESH_INTERVAL:
        return _finish(conn, run_id, "skipped", 0, {"reason": f"last successful refresh {last} is under "
                                                              f"{MIN_REFRESH_INTERVAL} old (quota)"})
    months = refresh_months(now)[:MAX_REQUESTS_PER_REFRESH]
    results, attempts, failure = _fetch_each(months, lambda m: ingest.ingest_month(conn, m, fetch=fetch), sleep)
    if failure:
        return _finish(conn, run_id, "failed", attempts, {"months": months, "failed_month": failure[0],
                                                          "error": failure[1], "completed": results})
    return _finish(conn, run_id, "success", attempts, {"months": months, "imports": results})


def _fetch_each(keys: list[str], ingest_one: Callable[[str], dict], sleep: Callable[[float], None]
                ) -> tuple[list[dict], int, Optional[tuple[str, str]]]:
    """Ingests each key with retries and backoff. Returns (results, attempts, (failed key, error) or None)."""
    results, attempts = [], 0
    for key in keys:
        for attempt in range(1, RETRIES + 1):
            attempts += 1
            try:
                results.append(ingest_one(key))
                break
            except Exception as exc:  # network/HTTP/parse failure: retry with backoff, then fail the job
                if attempt == RETRIES:
                    return results, attempts, (key, f"{type(exc).__name__}: "
                                                    f"{str(exc).splitlines()[0] if str(exc) else ''}")
                sleep(BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)])
    return results, attempts, None


def odds_dates(conn: sqlite3.Connection, now: datetime) -> list[str]:
    """Eastern dates (YYYYMMDD) of stored scheduled games tipping off within ODDS_WINDOW. The schedule itself
    comes from `refresh`; with no game soon, no request is made."""
    rows = conn.execute("SELECT DISTINCT tipoff_at FROM games WHERE status='scheduled' AND tipoff_at > ? "
                        "AND tipoff_at <= ? ORDER BY tipoff_at",
                        (_fmt(now), _fmt(now + ODDS_WINDOW))).fetchall()
    dates = []
    for r in rows:
        d = f"{_parse(r[0]).astimezone(EASTERN):%Y%m%d}"
        if d not in dates:
            dates.append(d)
    return dates[:MAX_REQUESTS_PER_ODDS]


def odds_snapshot(conn: sqlite3.Connection, *, force: bool = False, fetch: Callable[[str], bytes] = espn.fetch_day,
                  sleep: Callable[[float], None] = time.sleep, now: Optional[datetime] = None) -> dict:
    """Pregame odds snapshot: re-fetches the ESPN day scoreboards of games starting soon, so quotes (each stamped
    with its retrieval time, append-only) accumulate up to tipoff. Also updates schedule/status for those days."""
    run_id = _start(conn, "odds")
    now = now or datetime.now(timezone.utc)
    last = last_success(conn, "odds")
    if last and not force and now - _parse(last) < MIN_ODDS_INTERVAL:
        return _finish(conn, run_id, "skipped", 0, {"reason": f"last successful odds snapshot {last} is under "
                                                              f"{MIN_ODDS_INTERVAL} old (quota)"})
    dates = odds_dates(conn, now)
    if not dates:
        return _finish(conn, run_id, "skipped", 0, {"reason": f"no scheduled game tips off within "
                                                              f"{ODDS_WINDOW.total_seconds() / 3600:g} h"})
    results, attempts, failure = _fetch_each(dates, lambda d: ingest.ingest_day(conn, d, fetch=fetch), sleep)
    if failure:
        return _finish(conn, run_id, "failed", attempts, {"dates": dates, "failed_date": failure[0],
                                                          "error": failure[1], "completed": results})
    return _finish(conn, run_id, "success", attempts, {"dates": dates, "imports": results})


def predict(conn: sqlite3.Connection, **kw) -> dict:
    run_id = _start(conn, "predict")
    try:
        return _finish(conn, run_id, "success", 1, live.predict(conn, **kw))
    except Exception as exc:
        return _finish(conn, run_id, "failed", 1, {"error": f"{type(exc).__name__}: {exc}"})


def score(conn: sqlite3.Connection) -> dict:
    run_id = _start(conn, "score")
    try:
        return _finish(conn, run_id, "success", 1, live.score(conn))
    except Exception as exc:
        return _finish(conn, run_id, "failed", 1, {"error": f"{type(exc).__name__}: {exc}"})


def daily(conn: sqlite3.Connection, *, force_refresh: bool = False, **refresh_kw) -> dict:
    """Refresh, then predict (even if the refresh failed or was skipped: forecasts then use the data already stored,
    and the refresh status stays visible), then score."""
    return {"refresh": refresh(conn, force=force_refresh, **refresh_kw), "predict": predict(conn), "score": score(conn)}


def scheduler(conn: sqlite3.Connection, *, interval_minutes: float, max_runs: int, job: str = "daily",
              log: Callable[[str], None] = print, sleep: Callable[[float], None] = time.sleep) -> list[dict]:
    """Runs `daily` or `odds` at a fixed interval, a bounded number of times (no unbounded background process)."""
    if job not in SCHEDULER_MIN_INTERVAL:
        raise ValueError(f"job must be one of {sorted(SCHEDULER_MIN_INTERVAL)}")
    minimum = SCHEDULER_MIN_INTERVAL[job]
    if max_runs < 1 or interval_minutes < minimum:
        raise ValueError(f"max_runs must be >= 1 and interval_minutes >= {minimum} ({job} quota)")
    out = []
    for i in range(max_runs):
        if i:
            sleep(interval_minutes * 60)
        if job == "daily":
            result = daily(conn)
            log(json.dumps({k: v.get("status") for k, v in result.items()}))
        else:
            result = odds_snapshot(conn)
            log(json.dumps({"odds": result.get("status"), "dates": result.get("dates")}))
        out.append(result)
    return out
