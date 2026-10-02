"""Bounded operational jobs: refresh → predict → score, with retries, request quotas and a job_runs log."""
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from . import agreement, config, ingest, injuries, live, market_live, near_tipoff, research_live, shadow
from .db import store_sport, utc_now
from .sources import espn, nhl_stats

EASTERN = ZoneInfo("America/New_York")
MIN_REFRESH_INTERVAL = timedelta(minutes=30)   # quota: at most one successful refresh per 30 minutes
MAX_REQUESTS_PER_REFRESH = 3                   # quota: months fetched per refresh
MIN_ODDS_INTERVAL = timedelta(minutes=10)      # quota: at most one successful odds snapshot per 10 minutes
ODDS_WINDOW = timedelta(hours=36)              # snapshot days with a scheduled game tipping off this soon
MAX_REQUESTS_PER_ODDS = 2                      # quota: day scoreboards fetched per snapshot
SCHEDULER_MIN_INTERVAL = {"daily": 30, "odds": 10}  # minutes
BOX_LOOKBACK = timedelta(days=14)             # only recent finals are refreshed; older gaps are reported, not chased
BOX_RECHECK_AFTER = timedelta(hours=24)        # one re-check for boxes first seen sooner than this after tipoff
MAX_BOX_REQUESTS = 30                          # quota: HTTP attempts per run, counting every retry and failure
MAX_BOX_ATTEMPTS = 5                           # quota: HTTP attempts ever made per game, counting failures
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


def sport(conn: sqlite3.Connection) -> str:
    return store_sport(conn) or "nba"


def nhl_season_for(now: datetime) -> int:
    """Season ending year of the NHL season running (or next to start) on this Eastern date: July onwards is next season."""
    d = now.astimezone(EASTERN)
    return d.year + 1 if d.month >= 7 else d.year


def refresh(conn: sqlite3.Connection, *, force: bool = False, fetch: Optional[Callable] = None,
            sleep: Callable[[float], None] = time.sleep, now: Optional[datetime] = None) -> dict:
    """Schedule and results refresh for this store's sport. NBA: ESPN months around today (≤ 3 requests). NHL: the
    current season's NHL stats game list (1 request). Retries and the 30-minute quota are shared."""
    run_id = _start(conn, "refresh")
    now = now or datetime.now(timezone.utc)
    last = last_success(conn, "refresh")
    if last and not force and now - _parse(last) < MIN_REFRESH_INTERVAL:
        return _finish(conn, run_id, "skipped", 0, {"reason": f"last successful refresh {last} is under "
                                                              f"{MIN_REFRESH_INTERVAL} old (quota)"})
    if sport(conn) == "nhl":
        season = nhl_season_for(now)
        fetch = fetch or nhl_stats.fetch_season
        results, attempts, failure = _fetch_each([season], lambda y: ingest.ingest_nhl_season(conn, y, fetch=fetch), sleep)
        if failure:
            return _finish(conn, run_id, "failed", attempts, {"seasons": [season], "failed_season": failure[0],
                                                              "error": failure[1], "completed": results})
        return _finish(conn, run_id, "success", attempts, {"seasons": [season], "imports": results})
    fetch = fetch or espn.fetch_month
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


def odds_snapshot(conn: sqlite3.Connection, *, force: bool = False, fetch: Optional[Callable[[str], bytes]] = None,
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
    if sport(conn) == "nhl":   # ESPN NHL day scoreboards: line quotes only (schedule and results come from the NHL)
        fetch = fetch or espn.fetch_nhl_day
        results, attempts, failure = _fetch_each(dates, lambda d: ingest.ingest_nhl_day(conn, d, fetch=fetch), sleep)
    else:
        fetch = fetch or espn.fetch_day
        results, attempts, failure = _fetch_each(dates, lambda d: ingest.ingest_day(conn, d, fetch=fetch), sleep)
    if failure:
        return _finish(conn, run_id, "failed", attempts, {"dates": dates, "failed_date": failure[0],
                                                          "error": failure[1], "completed": results})
    return _finish(conn, run_id, "success", attempts, {"dates": dates, "imports": results})


def box_refresh_plan(conn: sqlite3.Connection, now: datetime) -> dict:
    """Which recent eligible finals need a summary fetch. `missing`: no retrieval yet holds complete lines for both
    teams. `recheck`: the latest complete retrieval came sooner than BOX_RECHECK_AFTER after tipoff and that time has
    now passed (stat corrections). `gave_up`: still needed after MAX_BOX_ATTEMPTS fetches."""
    since = _fmt(now - BOX_LOOKBACK)
    types = live.ELIGIBLE_TYPES
    games = conn.execute(
        f"SELECT game_id, tipoff_at FROM games WHERE status='final' AND tipoff_at >= ? AND tipoff_at <= ? "
        f"AND season_type IN ({', '.join('?' * len(types))}) ORDER BY tipoff_at, game_id",
        (since, _fmt(now), *types)).fetchall()
    plan = {"missing": [], "recheck": [], "gave_up": [], "window_start": since, "recent_finals": len(games)}
    for g in games:
        complete = conn.execute(
            "SELECT MAX(h.observed_at) FROM team_possession_observations h JOIN team_possession_observations a "
            "ON a.game_id = h.game_id AND a.fetch_id = h.fetch_id AND a.is_home = 0 WHERE h.game_id = ? AND h.is_home = 1 "
            "AND h.points IS NOT NULL AND h.fga IS NOT NULL AND h.fta IS NOT NULL AND h.oreb IS NOT NULL AND h.tov IS NOT NULL "
            "AND a.points IS NOT NULL AND a.fga IS NOT NULL AND a.fta IS NOT NULL AND a.oreb IS NOT NULL AND a.tov IS NOT NULL",
            (g["game_id"],)).fetchone()[0]
        recheck_at = _parse(g["tipoff_at"]) + BOX_RECHECK_AFTER
        if complete is None:
            kind = "missing"
        elif _parse(complete) < recheck_at <= now:
            kind = "recheck"
        else:
            continue
        plan["gave_up" if box_attempts(conn, g["game_id"]) >= MAX_BOX_ATTEMPTS else kind].append(g["game_id"])
    return plan


def box_attempts(conn: sqlite3.Connection, game_id: str) -> int:
    """HTTP attempts ever made for this game's summary by the refresh job (successes and failures)."""
    return conn.execute("SELECT COUNT(*) FROM box_fetch_attempts WHERE game_id = ?", (game_id,)).fetchone()[0]


def _log_box_attempt(conn: sqlite3.Connection, game_id: str, at: str, fetch_id: Optional[int] = None,
                     error: Optional[str] = None) -> None:
    conn.execute("INSERT INTO box_fetch_attempts (game_id, attempted_at, outcome, fetch_id, error) VALUES (?, ?, ?, ?, ?)",
                 (game_id, at, "fetched" if fetch_id is not None else "failed", fetch_id, error))


def boxscores(conn: sqlite3.Connection, *, fetch: Callable[[str], bytes] = espn.fetch_summary,
              sleep: Callable[[float], None] = time.sleep, now: Optional[datetime] = None,
              pause_seconds: float = 1.0, clock: Optional[Callable[[], str]] = None) -> dict:
    """Possession refresh: fetches ESPN game summaries for recent finals that lack a complete box (then rechecks).

    Budgets count actual HTTP attempts, including retries and failures: at most MAX_BOX_REQUESTS per run and
    MAX_BOX_ATTEMPTS per game ever. Every attempt is logged in box_fetch_attempts before anything else happens with
    its result, so a failed request or a failed import still uses its allowance. A game is retried at most RETRIES
    times per run and never past either budget. One game failing does not stop the others; the job is 'failed' if
    any game could not be fetched, and unresolved games are always listed.

    Two times are kept per attempt: the attempt log holds when the request started, while the observation's
    retrieval time (observed_at / available_at) is read from the clock only AFTER the response has arrived, so a
    slow response can never appear available before it was actually received. `clock` and `now` are for tests."""
    run_id = _start(conn, "boxscores")
    if clock is None:
        fixed = _fmt(now) if now else None
        clock = (lambda: fixed) if fixed else utc_now
    now = now or datetime.now(timezone.utc)
    plan = box_refresh_plan(conn, now)
    queue = plan["missing"] + plan["recheck"]
    budget, fetched, failed, deferred = MAX_BOX_REQUESTS, [], [], []
    for game_id in queue:
        if budget <= 0:
            deferred.append(game_id)
            continue
        if budget < MAX_BOX_REQUESTS and pause_seconds:
            sleep(pause_seconds)
        allowance, used, error, result = MAX_BOX_ATTEMPTS - box_attempts(conn, game_id), 0, None, None
        while result is None and used < RETRIES and budget > 0 and allowance > 0:
            if used:
                sleep(BACKOFF_SECONDS[min(used - 1, len(BACKOFF_SECONDS) - 1)])
            budget, allowance, used = budget - 1, allowance - 1, used + 1
            started = clock()
            try:
                body = fetch(game_id)
            except Exception as exc:  # network/HTTP failure: counted, then retried within the budgets
                error = f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"
                _log_box_attempt(conn, game_id, started, error=error)
                continue
            received = clock()   # the retrieval time: taken only once the response is in hand
            try:
                result = ingest.ingest_box_summary(conn, game_id, fetch=lambda _g: body, retrieved_at=received)
                _log_box_attempt(conn, game_id, started, fetch_id=result["fetch_id"])
            except Exception as exc:  # parse/import failure: the request was made and counts; not retried this run
                error = f"import {type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"
                _log_box_attempt(conn, game_id, started, error=error)
                break
        if result is not None:
            fetched.append({k: result[k] for k in ("game_id", "recorded", "complete_lines", "changed_vs_previous",
                                                   "skipped_not_completed")})
        else:
            failed.append({"game_id": game_id, "error": error, "attempts": used})
    after = box_refresh_plan(conn, now)
    attempts = MAX_BOX_REQUESTS - budget
    detail = {"recent_finals": plan["recent_finals"], "window_start": plan["window_start"],
              "requested": len(fetched) + len(failed), "http_attempts": attempts, "attempt_budget": MAX_BOX_REQUESTS,
              "deferred": len(deferred), "fetched": fetched, "failed": failed, "still_missing": after["missing"],
              "gave_up": after["gave_up"]}
    if not queue:
        return _finish(conn, run_id, "skipped", 0, {**detail, "reason": "no recent final needs a box score"})
    return _finish(conn, run_id, "failed" if failed else "success", attempts, detail)


def injury_reports(conn: sqlite3.Connection, *, enabled: Optional[bool] = None, **kw) -> dict:
    """Raw injury-report collection (app/injuries.py), logged as job 'injuries'. Makes no request and writes nothing
    unless collection is switched on (config.injury_collection_enabled()). A dry run never needs the switch."""
    if kw.get("dry_run"):
        return injuries.run(conn, **kw)
    if not (config.injury_collection_enabled() if enabled is None else enabled):
        return {"status": "disabled", "reason": "NBA_INJURY_COLLECTION is not set to 1; no request was made"}
    run_id = _start(conn, "injuries")
    try:
        out = injuries.run(conn, **kw)
    except Exception as exc:
        return _finish(conn, run_id, "failed", 0, {"error": f"{type(exc).__name__}: {exc}"})
    status = out.pop("status")
    return _finish(conn, run_id, status, out["attempts"], out)


def predict_now(conn: sqlite3.Connection, *, dry_run: bool = False, now: Optional[str] = None,
                refresh_kw: Optional[dict] = None, odds_kw: Optional[dict] = None, box_kw: Optional[dict] = None, injury_kw: Optional[dict] = None) -> dict:
    """Near-tipoff forecasts (app/near_tipoff.py): refresh schedule and results, odds and possession stats, then
    forecast games tipping off within two hours with the frozen Elo, or refuse them with a reason. The forecast is
    time-stamped after the refresh finishes. The possession refresh does not gate the Elo forecast (Elo does not use
    it). Stale data or a missing result makes the job 'failed' so it is visible; a dry run stores no forecast."""
    steps = {"refresh": refresh(conn, **(refresh_kw or {})), "odds": odds_snapshot(conn, **(odds_kw or {}))}
    if sport(conn) == "nba":
        steps["boxscores"] = boxscores(conn, **(box_kw or {}))
        from . import availability
        if (availability.research_dir() / 'forward-facts.json').exists():
            steps["injuries"] = injury_reports(conn, enabled=True, **(injury_kw or {}))
    run_id = None if dry_run else _start(conn, "near_tipoff")
    try:
        out = near_tipoff.predict_now(conn, now=now, write=not dry_run)
    except Exception as exc:
        error = {"error": f"{type(exc).__name__}: {exc}", "steps": {k: v.get("status") for k, v in steps.items()}}
        return {"status": "failed", **error} if dry_run else _finish(conn, run_id, "failed", 1, error)
    blocked = [r for r in out["refused"] if r["reason"] in ("stale_results", "missing_result")]
    status = "failed" if blocked else "success" if out["forecasts"] else "skipped"
    out["steps"] = {k: v.get("status") for k, v in steps.items()}
    accepted = [f["game_id"] for f in out["forecasts"]]
    if accepted:   # market forecasts at the same moment for exactly the games the winner step accepted
        m = market_forecasts(conn, policy="near_tipoff", write=not dry_run, now=now, game_ids=accepted)
        out["steps"]["markets"] = m.get("status")
        if m.get("status") == "failed":
            status = "failed"
        out["markets"] = {k: m.get(k) for k in ("created", "already_recorded", "reason", "error") if k in m}
    if accepted:
        collect = {} if research_live.enabled() else None
        run = research_forecasts(conn, write=not dry_run, now=now, game_ids=accepted, collect=collect, keep=True)
        winner = run.pop("forecasts", [])
        out["agreement"] = run
        if out["agreement"].get("status") == "failed":
            status = "failed"
        if collect is not None:   # research cohorts and selections; never blocks or changes the primary forecasts above
            # Reported in the detail (and the CLI exit code); the primary run's status is not downgraded by research.
            out["research"] = research_selection(conn, game_ids=accepted, winner_run={**run, "forecasts": winner},
                                                 history=collect, now=now, write=not dry_run)
        else:
            out["research"] = {"status": "disabled", "reason": "RESEARCH_SELECTION=0"}
    return {"status": status, **out} if dry_run else _finish(conn, run_id, status, 1, out)


def market_forecasts(conn: sqlite3.Connection, *, policy: str = "pregame", write: bool = True, **kw) -> dict:
    """Market forecasts with the sport's sealed market model. A sport without one is 'skipped' with the reason
    (never a fabricated forecast); any other error is 'failed'."""
    run_id = _start(conn, "markets") if write else None
    try:
        out = market_live.forecast(conn, policy=policy, write=write, **kw)
    except market_live.MarketModelUnavailable as exc:
        detail = {"reason": str(exc)}
        return _finish(conn, run_id, "skipped", 0, detail) if write else {"status": "skipped", **detail}
    except Exception as exc:
        detail = {"error": f"{type(exc).__name__}: {exc}"}
        return _finish(conn, run_id, "failed", 1, detail) if write else {"status": "failed", **detail}
    out.pop("forecasts")
    return _finish(conn, run_id, "success", 1, out) if write else {"status": "dry_run", **out}


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


def shadow_forecasts(conn: sqlite3.Connection, **kw) -> dict:
    """Records the sealed challenger's shadow forecasts (app/shadow.py). Stale-input skips make the job 'failed' so
    they are visible; forecasts for other games are still recorded."""
    run_id = _start(conn, "shadow")
    try:
        out = shadow.predict(conn, write=True, **kw)
    except Exception as exc:
        return _finish(conn, run_id, "failed", 1, {"error": f"{type(exc).__name__}: {exc}"})
    out.pop("forecasts")
    return _finish(conn, run_id, "failed" if out["skipped_stale_inputs"] else "success", 1, out)


def research_forecasts(conn: sqlite3.Connection, keep: bool = False, **kw) -> dict:
    # Daily JSON and exit status expose failures, independently of the primary forecast steps.
    try:
        result = agreement.predict(conn, **kw)
        if not keep:
            result.pop("forecasts", None)
        return result
    except Exception as exc:
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}


def research_selection(conn: sqlite3.Connection, **kw) -> dict:
    """Research cohorts and selections (app/research_live.py). Errors are visible here and never touch primary rows."""
    try:
        return research_live.record(conn, **kw)
    except Exception as exc:
        conn.rollback()
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}


def selector_decisions(conn: sqlite3.Connection, now: Optional[str] = None) -> dict:
    """The weekly selector decisions (Monday 14:00 UTC), written by the first odds cycle after that time."""
    if not research_live.enabled():
        return {"status": "disabled", "reason": "RESEARCH_SELECTION=0"}
    try:
        return {"status": "success", **research_live.decide_due(conn, now=now)}
    except Exception as exc:
        conn.rollback()
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}


def daily(conn: sqlite3.Connection, *, force_refresh: bool = False, **refresh_kw) -> dict:
    """Nightly maintenance only. User policy: new forecasts are made within two hours of tipoff by odds cycles."""
    out = {"refresh": refresh(conn, force=force_refresh, **refresh_kw)}
    if sport(conn) == "nba" and config.shadow_enabled():
        out["boxscores"] = boxscores(conn)
    out["score"] = score(conn)
    out["two_hour_score"] = near_tipoff.score(conn)
    out["market_score"] = market_live.score(conn, policy="near_tipoff")
    out["forecast_policy"] = "two-hour-v1; night-before and sealed night-before shadow recording discontinued"
    return out


def forecast_due(conn: sqlite3.Connection) -> dict:
    """Called by the existing periodic odds command for both sports; no eligible games means no extra requests."""
    decisions = selector_decisions(conn)
    now = utc_now()
    eng = live.engine(conn)
    end = near_tipoff._fmt(near_tipoff._parse(now) + near_tipoff.WINDOW)
    exists = conn.execute(f"SELECT 1 FROM games WHERE status='scheduled' AND tipoff_at>? AND tipoff_at<=? "
        f"AND season_type IN ({','.join('?' for _ in eng.eligible_types)}) LIMIT 1",
        (now, end, *eng.eligible_types)).fetchone()
    if not exists:
        recent = conn.execute("SELECT 1 FROM games WHERE tipoff_at<=? AND tipoff_at>? "
                              "AND status IN ('scheduled','in_progress') LIMIT 1",
                              (now, near_tipoff._fmt(near_tipoff._parse(now) - near_tipoff.RECENT_TIPOFF))).fetchone()
        if recent:
            return {"refresh": refresh(conn), "status": "skipped", "reason": "results refresh; no upcoming game within two hours",
                    "selector": decisions}
        return {"status": "skipped", "reason": "no eligible game within two hours", "selector": decisions}
    return {**predict_now(conn), "selector": decisions}


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
            log(json.dumps({k: v.get("status") for k, v in result.items() if isinstance(v, dict)}))
        else:
            result = {"odds": odds_snapshot(conn), "forecasts": forecast_due(conn)}
            log(json.dumps({"odds": result["odds"].get("status"), "forecasts": result["forecasts"].get("status")}))
        out.append(result)
    return out
