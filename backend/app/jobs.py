"""Bounded operational jobs: refresh → predict → score, with retries, request quotas and a job_runs log."""
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from . import agreement, config, coverage, ingest, injuries, live, market_live, near_tipoff, research_live
from .db import store_sport, utc_now
from .sources import espn, nhl_stats

EASTERN = ZoneInfo("America/New_York")
MAX_REQUESTS_PER_REFRESH = 3                   # quota: months fetched per refresh
MIN_ODDS_INTERVAL = timedelta(minutes=10)      # quota: at most one successful odds snapshot per 10 minutes
ODDS_WINDOW = timedelta(hours=36)              # snapshot days with a scheduled game tipping off this soon
MAX_REQUESTS_PER_ODDS = 2                      # quota: day scoreboards fetched per snapshot
SCHEDULER_MIN_INTERVAL = {"daily": 30, "odds": 10}  # minutes
BOX_LOOKBACK = timedelta(days=14)             # only recent finals are refreshed; older gaps are reported, not chased
BOX_RECHECK_AFTER = timedelta(hours=24)        # one re-check for boxes first seen sooner than this after tipoff
MAX_BOX_REQUESTS = 30                          # quota: HTTP attempts per run, counting every retry and failure
MAX_BOX_ATTEMPTS = 5                           # quota: HTTP attempts ever made per game, counting failures
# Connection failures (no connection was made: DNS, refused, unreachable; the source was never asked) do not use the
# permanent allowance above: on 2026-10-22/26/27 a local DNS outage exhausted it for 13 games, which then stayed missing.
# They are bounded separately and retried after a backoff, and stay visible in every run's report. httpx also raises
# ConnectError for TLS/certificate failures: those share this bucket and its bound.
TRANSIENT_BOX_ERRORS = ("ConnectError", "ConnectTimeout")
MAX_TRANSIENT_BOX_ATTEMPTS = 120               # lifetime connection failures per game (~40 failed runs, >1 day of outage)
TRANSIENT_BOX_BACKOFF = timedelta(minutes=15)  # after the n-th consecutive failed run: 15 min x 2^(n-1), capped below
TRANSIENT_BOX_BACKOFF_CAP = timedelta(hours=1) # a short outage never parks a game for long once the network is back
RETRIES = 3
BACKOFF_SECONDS = (2, 8)


def _parse(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _start(conn: sqlite3.Connection, job: str) -> int:
    return conn.execute("INSERT INTO job_runs (job, started_at, status) VALUES (?, ?, 'running')",
                        (job, utc_now())).lastrowid


def record_lock_busy(conn: sqlite3.Connection, job: str, reason: str) -> int:
    """A cycle that gave up waiting for the store lock leaves a 'skipped' row, so a missed forecast window is visible."""
    return _finish(conn, _start(conn, job), "skipped", 0, {"reason": reason, "lock_busy": True})["run_id"]


def _finish(conn: sqlite3.Connection, run_id: int, status: str, attempts: int, detail: dict) -> dict:
    if os.environ.get("NBA_RELEASE_COMMIT"):        # set by the release guard (app/release.py): which commit ran this job
        detail = {**detail, "release": os.environ["NBA_RELEASE_COMMIT"]}
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
    """Schedule, status and results refresh for this store's sport, when the activity policy says it is due
    (app/freshness.py; replaces the fixed 30-minute quota). Broad scope: NBA ESPN months around today (<= 3 requests),
    NHL the current season's game list (1 request). Dates scope (a game active or starting within two hours): only those
    games' Eastern dates (<= 3 small requests). `force` runs a broad refresh now. Failures back off and never count as
    fresh data. Dispatch is explicit per sport: an unknown store is refused, never treated as NBA."""
    from . import freshness
    run_id = _start(conn, "refresh")
    now = now or datetime.now(timezone.utc)
    plan = freshness.plan(conn, now)
    if not force and not plan["due"]:
        return _finish(conn, run_id, "skipped", 0, {"reason": f"not due until {plan['next_due_at']} ({plan['activity']}; "
                                                              f"every {plan['interval_seconds']} s)", "policy": _policy(plan)})
    scope = "broad" if force else plan["scope"]
    name = store_sport(conn) or "nba"       # an unlabelled store is the original NBA store (db.claim_store binds it)
    if name == "nhl":
        if scope == "broad":
            keys, label = [nhl_season_for(now)], "seasons"
            fetch = fetch or nhl_stats.fetch_season
            one = lambda y: ingest.ingest_nhl_season(conn, y, fetch=fetch)
        else:
            keys, label = plan["dates"], "dates"
            fetch = fetch or nhl_stats.fetch_day
            one = lambda d: ingest.ingest_nhl_games_day(conn, nhl_season_for(_eastern_noon(d)), d, fetch=fetch)
    elif name == "nba":
        if scope == "broad":
            keys, label = refresh_months(now)[:MAX_REQUESTS_PER_REFRESH], "months"
            fetch = fetch or espn.fetch_month
            one = lambda m: ingest.ingest_month(conn, m, fetch=fetch)
        else:
            keys, label = [d.replace("-", "") for d in plan["dates"]], "dates"
            fetch = fetch or espn.fetch_day
            one = lambda d: ingest.ingest_day(conn, d, fetch=fetch)
    else:
        return _finish(conn, run_id, "failed", 0, {"error": f"no refresh is defined for a {name!r} store"})
    results, attempts, failure = _fetch_each(keys, one, sleep)
    detail = {"scope": scope, label: keys, "policy": _policy(plan), "forced": force}
    if failure:
        return _finish(conn, run_id, "failed", attempts, {**detail, f"failed_{label[:-1]}": failure[0], "error": failure[1],
                                                          "completed": results})
    return _finish(conn, run_id, "success", attempts, {**detail, "imports": results})


def _policy(plan: dict) -> dict:
    return {k: plan[k] for k in ("activity", "interval_seconds", "next_due_at", "failures")}


def _eastern_noon(day: str) -> datetime:
    return datetime.strptime(day, "%Y-%m-%d").replace(hour=12, tzinfo=EASTERN)


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


def collect_props(conn: sqlite3.Connection, odds_run: Optional[dict] = None, now: Optional[datetime] = None) -> dict:
    """Forward raw prop-line snapshots (collection only, docs/MARKETS.md). Never raises and never affects another job's
    status. With `odds_run` (a successful odds snapshot of this cycle) the outcome is added to that run's stored detail, so
    the periodic cycle can collect props AFTER the time-critical two-hour forecasts and still leave its trace."""
    try:
        from . import props_collect
        out = props_collect.collect(conn, sport(conn), now=now)
    except Exception as exc:
        conn.rollback()
        out = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    if odds_run and odds_run.get("status") == "success" and odds_run.get("run_id") is not None:
        row = conn.execute("SELECT detail FROM job_runs WHERE run_id=?", (odds_run["run_id"],)).fetchone()
        detail = {**json.loads(row["detail"] or "{}"), "props": out} if row else None
        if detail is not None:
            conn.execute("UPDATE job_runs SET detail=? WHERE run_id=?", (json.dumps(detail, default=str), odds_run["run_id"]))
    return out


def odds_snapshot(conn: sqlite3.Connection, *, force: bool = False, fetch: Optional[Callable[[str], bytes]] = None,
                  sleep: Callable[[float], None] = time.sleep, now: Optional[datetime] = None,
                  props: bool = True) -> dict:
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
    detail = {"dates": dates, "imports": results}
    if props:   # the periodic cycle passes props=False and collects them after its forecasts (collect_props)
        detail["props"] = collect_props(conn, now=now)
    return _finish(conn, run_id, "success", attempts, detail)


def box_refresh_plan(conn: sqlite3.Connection, now: datetime) -> dict:
    """Which recent eligible finals need a summary fetch. `missing`: no retrieval yet holds complete lines for both
    teams. `recheck`: the latest complete retrieval came sooner than BOX_RECHECK_AFTER after tipoff and that time has
    now passed (stat corrections). `gave_up`: still needed after MAX_BOX_ATTEMPTS fetches or MAX_TRANSIENT_BOX_ATTEMPTS
    connection failures. `network_wait`: needed, but inside the backoff after consecutive connection failures."""
    since = _fmt(now - BOX_LOOKBACK)
    types = live.ELIGIBLE_TYPES
    games = conn.execute(
        f"SELECT game_id, tipoff_at FROM games WHERE status='final' AND tipoff_at >= ? AND tipoff_at <= ? "
        f"AND season_type IN ({', '.join('?' * len(types))}) ORDER BY tipoff_at, game_id",
        (since, _fmt(now), *types)).fetchall()
    plan = {"missing": [], "recheck": [], "gave_up": [], "network_wait": [], "window_start": since,
            "recent_finals": len(games)}
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
        transient, last_transient = transient_box_attempts(conn, g["game_id"])
        consecutive = consecutive_transient_box_attempts(conn, g["game_id"]) if transient else 0
        if box_attempts(conn, g["game_id"]) >= MAX_BOX_ATTEMPTS or transient >= MAX_TRANSIENT_BOX_ATTEMPTS:
            plan["gave_up"].append(g["game_id"])
        elif consecutive and now < _parse(last_transient) + transient_box_backoff(consecutive):
            plan["network_wait"].append(g["game_id"])
        else:
            plan[kind].append(g["game_id"])
    return plan


_TRANSIENT_SQL = " OR ".join(f"error LIKE '{name}:%'" for name in TRANSIENT_BOX_ERRORS)


def is_transient_box_error(error: Optional[str]) -> bool:
    return bool(error) and any(error.startswith(f"{name}:") for name in TRANSIENT_BOX_ERRORS)


def box_attempts(conn: sqlite3.Connection, game_id: str) -> int:
    """HTTP attempts ever made for this game's summary by the refresh job (successes and failures), except connection
    failures (TRANSIENT_BOX_ERRORS), which are counted by transient_box_attempts."""
    return conn.execute(f"SELECT COUNT(*) FROM box_fetch_attempts WHERE game_id = ? AND NOT (outcome = 'failed' "
                        f"AND ({_TRANSIENT_SQL}))", (game_id,)).fetchone()[0]


def transient_box_attempts(conn: sqlite3.Connection, game_id: str) -> tuple[int, Optional[str]]:
    """(connection failures ever logged for this game's summary, the latest one's attempt time)."""
    row = conn.execute(f"SELECT COUNT(*), MAX(attempted_at) FROM box_fetch_attempts WHERE game_id = ? "
                       f"AND outcome = 'failed' AND ({_TRANSIENT_SQL})", (game_id,)).fetchone()
    return row[0], row[1]


def consecutive_transient_box_attempts(conn: sqlite3.Connection, game_id: str) -> int:
    """Connection failures since this game's last other attempt (a fetch or a non-connection failure)."""
    return conn.execute(f"SELECT COUNT(*) FROM box_fetch_attempts WHERE game_id = ? AND outcome = 'failed' "
                        f"AND ({_TRANSIENT_SQL}) AND attempt_id > IFNULL((SELECT MAX(attempt_id) FROM box_fetch_attempts "
                        f"WHERE game_id = ? AND NOT (outcome = 'failed' AND ({_TRANSIENT_SQL}))), 0)",
                        (game_id, game_id)).fetchone()[0]


def transient_box_backoff(consecutive: int) -> timedelta:
    """Wait after `consecutive` connection failures: grows per failed run (RETRIES attempts each), capped at 1 h."""
    runs = max(-(-consecutive // RETRIES), 1)
    return min(TRANSIENT_BOX_BACKOFF * (2 ** (runs - 1)), TRANSIENT_BOX_BACKOFF_CAP)


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
        transient_left = MAX_TRANSIENT_BOX_ATTEMPTS - transient_box_attempts(conn, game_id)[0]
        while result is None and used < RETRIES and budget > 0 and allowance > 0 and transient_left > 0:
            if used:
                sleep(BACKOFF_SECONDS[min(used - 1, len(BACKOFF_SECONDS) - 1)])
            budget, allowance, used = budget - 1, allowance - 1, used + 1
            started = clock()
            try:
                body = fetch(game_id)
            except Exception as exc:  # network/HTTP failure: counted, then retried within the budgets
                error = f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"
                _log_box_attempt(conn, game_id, started, error=error)
                if is_transient_box_error(error):   # never reached the source: the permanent allowance is kept
                    allowance, transient_left = allowance + 1, transient_left - 1
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
            failed.append({"game_id": game_id, "error": error, "attempts": used,
                           "transient": is_transient_box_error(error)})
    after = box_refresh_plan(conn, now)
    attempts = MAX_BOX_REQUESTS - budget
    detail = {"recent_finals": plan["recent_finals"], "window_start": plan["window_start"],
              "requested": len(fetched) + len(failed), "http_attempts": attempts, "attempt_budget": MAX_BOX_REQUESTS,
              "deferred": len(deferred), "fetched": fetched, "failed": failed, "still_missing": after["missing"],
              "gave_up": after["gave_up"], "network_wait": after["network_wait"]}
    if not queue:
        reason = (f"{len(plan['network_wait'])} game(s) waiting to retry after connection failures"
                  if plan["network_wait"] else "no recent final needs a box score")
        return _finish(conn, run_id, "skipped", 0, {**detail, "reason": reason})
    return _finish(conn, run_id, "failed" if failed else "success", attempts, detail)


NHL_PBP_PER_RUN = 60          # bounded per cycle; a backlog is worked off over later cycles


def nhl_pbp(conn: sqlite3.Connection, *, fetch: Optional[Callable] = None, limit: int = NHL_PBP_PER_RUN) -> dict:
    """NHL shot import for finals of the latest two seasons that have none yet (docs/MODEL_SELECTION.md §12), reported as
    a step of the calling job. Feeds only the xG research member; never gates a primary forecast."""
    from . import nhl_shots
    seasons = [r[0] for r in conn.execute("SELECT DISTINCT season FROM games WHERE status='final' ORDER BY season DESC LIMIT 2")]
    try:
        kw = {"fetch": fetch} if fetch else {}
        out = nhl_shots.import_pending(conn, seasons, limit=limit, log=lambda _m: None, **kw)
    except Exception as exc:
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    status = "failed" if out["errors"] and not out["imported"] else "success" if out["imported"] else "skipped"
    return {"status": status, **out, "errors": dict(list(out["errors"].items())[:10]), "seasons": seasons}


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


def nhl_injury_capture() -> dict:
    """NHL: one capture of the live ESPN injuries page for the NHL injury member (opt-in NHL_INJURY_CAPTURE=1, like the
    other collectors; one request per cycle). Reported as a step of the two-hour cycle's job run. Never fatal: a failed
    capture leaves the member 'report missing' (it abstains)."""
    from .sources import nhl_injuries
    if not config.nhl_injury_capture_enabled():
        return {"status": "disabled", "reason": "NHL_INJURY_CAPTURE is not set to 1; no request was made"}
    try:
        meta = nhl_injuries.capture_live()
        return {"status": "success", "captured_at": meta["captured_at"], "rows": len(meta["rows"])}
    except Exception as exc:
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}


def predict_now(conn: sqlite3.Connection, *, dry_run: bool = False, now: Optional[str] = None,
                refresh_kw: Optional[dict] = None, odds_kw: Optional[dict] = None, box_kw: Optional[dict] = None, injury_kw: Optional[dict] = None,
                pbp_kw: Optional[dict] = None) -> dict:
    """Near-tipoff forecasts (app/near_tipoff.py): refresh schedule and results, odds and possession stats, then
    forecast games tipping off within two hours with the frozen Elo, or refuse them with a reason. The forecast is
    time-stamped after the refresh finishes. The possession refresh does not gate the Elo forecast (Elo does not use
    it). Stale data or a missing result makes the job 'failed' so it is visible; a dry run stores no forecast."""
    # Prop lines are never collected in front of the forecasts (up to ~50 s of requests): the odds cycle does it afterwards.
    steps = {"refresh": refresh(conn, **(refresh_kw or {})), "odds": odds_snapshot(conn, **{"props": False, **(odds_kw or {})})}
    if sport(conn) == "nba":
        steps["boxscores"] = boxscores(conn, **(box_kw or {}))
        from . import availability
        if (availability.research_dir() / 'forward-facts.json').exists():
            steps["injuries"] = injury_reports(conn, enabled=True, **(injury_kw or {}))
    else:
        steps["nhl_injuries"] = nhl_injury_capture() if not dry_run else {"status": "skipped", "reason": "dry run"}
        if research_live.enabled():
            steps["nhl_pbp"] = nhl_pbp(conn, **(pbp_kw or {}))
    run_id = None if dry_run else _start(conn, "near_tipoff")
    try:
        out = near_tipoff.predict_now(conn, now=now, write=not dry_run)
    except Exception as exc:
        error = {"error": f"{type(exc).__name__}: {exc}", "steps": {k: v.get("status") for k, v in steps.items()}}
        # The primary step failing does not stop every-game coverage: it records the other sources or the reasons.
        error["coverage"] = {k: v for k, v in coverage.record(conn, now=now, write=not dry_run).items() if k != "rows"}
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
    if accepted and research_live.enabled():
        # adaptive-v1 forecasts from the artifacts in force (docs/ADAPTIVE_SYSTEM.md); no fit here, never raises. Before
        # the research steps: it reads games, results, features, the primary/market rows and quotes written above, never
        # this cycle's research, pool or tracking rows (review of 5423fbc).
        out["adaptive"] = adaptive_record(conn, game_ids=accepted, now=now, write=not dry_run)
    # Every game (preseason included) and market: the adaptive forecast, else the sealed primary, else a recorded reason.
    # Right after the primary, market and adaptive steps and BEFORE the slow research work (Nov 10 investigation: run
    # 1886 recorded coverage 41 minutes after its forecasts, when three games had started, and lost them). Isolated per
    # game and market; never changes the primary run's status.
    cov = coverage.record(conn, now=now, write=not dry_run)
    out["coverage"] = {k: v for k, v in cov.items() if k != "rows"}
    if accepted:
        collect = {} if research_live.enabled() else None
        run = research_forecasts(conn, write=not dry_run, now=now, game_ids=accepted, collect=collect, keep=True)
        winner = run.pop("forecasts", [])
        out["agreement"] = _refusal_is_not_failure(run)
        if out["agreement"].get("status") == "failed":
            status = "failed"
        if collect is not None:   # research cohorts and selections; never blocks or changes the primary forecasts above
            # Reported in the detail (and the CLI exit code); the primary run's status is not downgraded by research.
            out["research"] = research_selection(conn, game_ids=accepted, winner_run={**run, "forecasts": winner},
                                                 history=collect, now=now, write=not dry_run)
            # Candidate pool (docs/CANDIDATE_POOL.md): after the cohorts, so line markets use the core cohort's exact line.
            out["pool"] = candidate_pool(conn, game_ids=accepted, now=now, write=not dry_run)
            # Pool selections: after the candidate forecasts of this cycle, under the pool decisions in force.
            out["pool_selection"] = pool_selection(conn, game_ids=accepted, now=now, write=not dry_run,
                                                   defn=(out["research"] or {}).get("definition"))
            # Agreement tracking (tracking-v2): one observation per registered group and cohort; never raises.
            out["tracking"] = agreement_tracking(conn, game_ids=accepted, now=now, write=not dry_run)
        else:
            out["research"] = {"status": "disabled", "reason": "RESEARCH_SELECTION=0"}
    return {"status": status, **out} if dry_run else _finish(conn, run_id, status, 1, out)


# Members that refuse by design when their input is not usable (README: the injury-aware member skips while the others
# run). Their refusal is reported per member and never makes the run 'failed'; any other error or skip still does.
REFUSING_MEMBERS = {"availability_two_hour": "AvailabilityError:"}


def _refusal_is_not_failure(run: dict) -> dict:
    """The agreement step's result with status 'partial' instead of 'failed' when the ONLY problem is a refusing member's
    own refusal. The member's error stays in `errors` (cohorts record it as missing) and is repeated in `refused`."""
    errors, skipped = run.get("errors") or {}, run.get("skipped") or {}
    if run.get("status") != "failed" or "error" in run or not errors:
        return run
    shadow = agreement.SHADOW.get(run.get("sport"), ())
    refused = all(str(errors[m]).startswith(REFUSING_MEMBERS.get(m, "\0")) for m in errors if m not in shadow)
    refusals = {m: errors[m] for m in errors if m in REFUSING_MEMBERS}
    if not refused or not refusals or any(m not in shadow for m in skipped):
        return run
    return {**run, "status": "partial", "refused": refusals}


NIGHT_BEFORE_DISCONTINUED = "night-before forecasting is discontinued: new forecasts are made only within two hours of the start"


def market_forecasts(conn: sqlite3.Connection, *, policy: str = "near_tipoff", write: bool = True, **kw) -> dict:
    """Market forecasts with the sport's sealed market model. A sport without one is 'skipped' with the reason
    (never a fabricated forecast); any other error is 'failed'. Only the two-hour policy may write (audit P14)."""
    if policy != "near_tipoff":
        raise ValueError(f"{NIGHT_BEFORE_DISCONTINUED} (policy {policy!r})")
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
    """Discontinued night-before Elo job (audit P14): refuses before writing anything. Two-hour forecasts: predict_now."""
    raise RuntimeError(NIGHT_BEFORE_DISCONTINUED)


def score(conn: sqlite3.Connection) -> dict:
    run_id = _start(conn, "score")
    try:
        return _finish(conn, run_id, "success", 1, live.score(conn))
    except Exception as exc:
        return _finish(conn, run_id, "failed", 1, {"error": f"{type(exc).__name__}: {exc}"})


def shadow_forecasts(conn: sqlite3.Connection, **kw) -> dict:
    """Discontinued night-before shadow recording (as jobs.predict; review finding 6): refuses before writing anything.
    The CLI `shadow-predict --record` is already disabled; existing shadow rows remain unchanged."""
    raise RuntimeError(NIGHT_BEFORE_DISCONTINUED)


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


def candidate_pool(conn: sqlite3.Connection, **kw) -> dict:
    """Two-hour candidate-pool forecasts (research only; never votes, never touches primary or cohort rows). The pool's
    own record() never raises; this guard also covers its import."""
    try:
        from .candidates import live as pool_live
        r = pool_live.record(conn, **kw)
        from .candidates import calibrated
        try:
            r["calibrated_shadows"] = calibrated.record(conn, **kw)
        except Exception as exc:
            r["calibrated_shadows"] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        for field in ("errors", "skipped"):
            r.setdefault(field, {}).update(r["calibrated_shadows"].get(field, {}))
        if r["calibrated_shadows"].get("error"):
            r.setdefault("errors", {})["calibrated_shadows"] = r["calibrated_shadows"]["error"]
        if r["calibrated_shadows"]["status"] != "success" and r["status"] == "success":
            r["status"] = "partial"
        return {k: v for k, v in r.items() if k != "forecasts"}
    except Exception as exc:
        conn.rollback()
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}


def agreement_tracking(conn: sqlite3.Connection, **kw) -> dict:
    """tracking-v2 observations (app/agreement_tracking.py), research only; record() never raises, the import is guarded."""
    try:
        from . import agreement_tracking as at
        return at.record(conn, **kw)
    except Exception as exc:
        conn.rollback()
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}


def adaptive_record(conn: sqlite3.Connection, **kw) -> dict:
    """adaptive-v1 forecasts (app/adaptive.py): artifacts in force only, separate append-only table; never raises."""
    try:
        from . import adaptive
        r = adaptive.record(conn, **kw)
        from . import config
        if config.daily_agreement_enabled():
            with adaptive.cadence("daily"):
                daily = adaptive.record(conn, **kw)
            r["daily_agreement"] = {k: v for k, v in daily.items() if k != "forecasts"}
        return {k: v for k, v in r.items() if k != "forecasts"}
    except Exception as exc:
        conn.rollback()
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}


def adaptive_train(conn: sqlite3.Connection, **kw) -> dict:
    """adaptive-v1 training status for the nightly report. Training itself never runs inside this job: it holds the
    store's job lock, which would make the two-hour cycles skip. `adaptive-train` (its own scheduled command) snapshots
    and publishes under brief locks and trains under a separate training lock. Never raises."""
    try:
        from . import adaptive
        d = adaptive.due(conn, kw.get("now"))
        return {"status": "due" if d.get("due") else "not_due", **d,
                "note": "training runs under its own lock via `adaptive-train` (scripts/launchd/com.nba-predictor.adaptive-train.plist)"}
    except Exception as exc:
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}


def pool_selection(conn: sqlite3.Connection, **kw) -> dict:
    """Candidate-pool selections (app/candidates/selection.py), separate tables; never touches core or primary rows."""
    try:
        from .candidates import selection
        r = selection.select(conn, **kw)
        return {k: v for k, v in r.items() if k != "selections"}
    except Exception as exc:
        conn.rollback()
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}


def selector_decisions(conn: sqlite3.Connection, now: Optional[str] = None) -> dict:
    """The weekly selector decisions (Monday 14:00 UTC), written by the first odds cycle after that time; then the pool
    decisions for the same week (their failure never affects the core decisions)."""
    if not research_live.enabled():
        return {"status": "disabled", "reason": "RESEARCH_SELECTION=0"}
    try:
        out = {"status": "success", **research_live.decide_due(conn, now=now)}
    except Exception as exc:
        conn.rollback()
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    try:
        from .candidates import selection
        out["pool"] = {"status": "success", **selection.decide_due(conn, now=now, defn=out["definition"])}
    except Exception as exc:
        conn.rollback()
        out["pool"] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    return out


PLAYER_BOX_MAX_AGE_HOURS = 20


def player_box_refresh(conn: sqlite3.Connection, *, now: Optional[str] = None, get=None, enabled: Optional[bool] = None) -> dict:
    """NBA: re-downloads the current season's player box file (verified against the digest the source publishes) once it
    is older than PLAYER_BOX_MAX_AGE_HOURS, so the player-rating members do not go stale between retrains. Only where the
    player box cache exists (the members' inputs) and NBA_PLAYER_BOX_REFRESH=1 (opt-in, like the other collectors); never
    fatal. The fitted members stay valid: their guard covers only
    the rows of games they consumed (player_ratings.LiveState.input_mismatch)."""
    from . import availability
    if sport(conn) != "nba":
        return {"status": "skipped", "reason": "NBA only"}
    if not (config.player_box_refresh_enabled() if enabled is None else enabled):
        return {"status": "disabled", "reason": "NBA_PLAYER_BOX_REFRESH is not set to 1; no request was made"}
    folder = availability.research_dir() / "player_box"
    if not folder.is_dir() or not any(folder.glob("player_box_*.csv")):
        return {"status": "skipped", "reason": f"no player box cache under {folder}"}
    now = now or utc_now()
    row = conn.execute("SELECT MAX(season) FROM games WHERE tipoff_at <= ? AND season_type IN ('regular', 'play_in', "
                       "'postseason', 'preseason')", (now,)).fetchone()
    season = row[0] if row else None
    if season is None:
        return {"status": "skipped", "reason": "no current season in the store"}
    try:
        return _player_box_refresh(folder, season, get)
    except Exception as exc:                        # never fatal: the nightly job continues
        return {"status": "failed", "season": season, "error": f"{type(exc).__name__}: {exc}"}


def _cached_seasons(folder) -> list[int]:
    """Seasons with a player box file in the cache; any other file name (a backup, a partial download) is ignored."""
    import re
    return sorted(int(m[1]) for p in folder.glob("player_box_*.csv") if (m := re.fullmatch(r"player_box_(\d{4})", p.stem)))


def _player_box_refresh(folder, season: int, get) -> dict:
    from . import availability
    cached = _cached_seasons(folder)
    if not cached:
        return {"status": "skipped", "reason": f"no player box cache under {folder}"}
    # Due: every season missing from the cache between the oldest cached one and the current season (a season the source
    # published while this cache was not refreshed, e.g. 2024-25 and 2025-26, or one whose download failed earlier), and
    # the current season again once its file is stale.
    due = [s for s in range(cached[0], season + 1) if s not in cached]
    path = availability.player_box_path(season)
    if path.exists():
        age = (time.time() - path.stat().st_mtime) / 3600          # file times are wall-clock times
        if age >= PLAYER_BOX_MAX_AGE_HOURS:
            due.append(season)
        elif not due:
            return {"status": "skipped", "reason": f"player_box_{season}.csv is {age:.1f} h old", "season": season,
                    "newest_season": max(cached)}
    import httpx
    fetch = get or (lambda url: httpx.get(url, timeout=120, follow_redirects=True))
    try:
        listing = fetch(availability.PLAYER_BOX_RELEASE)
        release = listing.json()
    except Exception as exc:
        return {"status": "failed", "season": season, "error": f"release listing: {type(exc).__name__}: {exc}"}

    def get(url):        # the release listing is read once per run; download_player_box asks for it again
        return listing if url == availability.PLAYER_BOX_RELEASE else fetch(url)
    published = {a.get("name") for a in release.get("assets", [])}
    downloaded, not_published, errors = [], [], {}
    for s in due:
        if f"player_box_{s}.csv" not in published:
            not_published.append(s)             # the source has not published this season (yet): reported, never a failure
            continue
        try:
            downloaded.append(availability.download_player_box(s, get=get, live=True))
        except Exception as exc:
            errors[s] = f"{type(exc).__name__}: {exc}"
    out = {"season": season, "newest_season": max(_cached_seasons(folder)), "downloaded": downloaded,
           "not_published": not_published}
    if errors:
        return {**out, "status": "partial" if downloaded else "failed", "errors": errors,
                "error": "; ".join(f"{s}: {e}" for s, e in errors.items())}
    if downloaded:
        return {**out, "status": "success"}
    return {**out, "status": "skipped", "reason": ", ".join(f"player_box_{s}.csv" for s in not_published) +
            f" not published by the source; newest season cached: {out['newest_season']}"}


def daily(conn: sqlite3.Connection, *, force_refresh: bool = False, **refresh_kw) -> dict:
    """Nightly maintenance only. User policy: new forecasts are made within two hours of tipoff by odds cycles."""
    out = {"refresh": refresh(conn, force=force_refresh, **refresh_kw)}
    if sport(conn) == "nba" and config.shadow_enabled():
        out["boxscores"] = boxscores(conn)
    if sport(conn) == "nba":
        out["player_box"] = player_box_refresh(conn)
    if sport(conn) == "nhl" and research_live.enabled():
        out["nhl_pbp"] = nhl_pbp(conn)
    out["score"] = score(conn)
    out["two_hour_score"] = near_tipoff.score(conn)
    out["market_score"] = market_live.score(conn, policy="near_tipoff")
    out["forecast_policy"] = "two-hour-v1; night-before and sealed night-before shadow recording discontinued"
    try:                     # reports newly settled results per fit and input freshness; never refits (non-fatal)
        from . import training_monitor
        out["training_check"] = training_monitor.status(conn)
    except Exception as exc:
        out["training_check"] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    # adaptive-v1 (docs/ADAPTIVE_SYSTEM.md): due state only; the refits run as `adaptive-train` under their own lock.
    out["adaptive_train"] = adaptive_train(conn) if research_live.enabled() else {"status": "disabled", "reason": "RESEARCH_SELECTION=0"}
    return out


def recover_missed(conn: sqlite3.Connection) -> dict:
    from . import recovery
    try:
        return recovery.recover(conn, limit=3)
    except Exception as exc:
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}


def recover_adaptive_missed(conn: sqlite3.Connection) -> dict:
    from . import recovery
    try:
        return recovery.recover_adaptive(conn, limit=ADAPTIVE_RECOVERY_LIMIT)
    except Exception as exc:
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}


ADAPTIVE_RECOVERY_LIMIT = 1     # replayed games per cycle (about 40 s each on the real NHL store, under the job lock; live work comes first)


def forecast_due(conn: sqlite3.Connection) -> dict:
    # Current pregame windows take priority; then recover a bounded batch of missed primary markets, then (needing
    # their lines) a bounded batch of missed adaptive research forecasts into their own retrospective ledger.
    result = _forecast_due(conn)
    result["recovery"] = recover_missed(conn)
    result["adaptive_recovery"] = recover_adaptive_missed(conn)
    return result


def _forecast_due(conn: sqlite3.Connection) -> dict:
    """Called by the existing periodic odds command for both sports; no eligible games means no extra requests."""
    decisions = selector_decisions(conn)
    # Every forecastable game type (preseason included) opens the cycle: the primary steps forecast their eligible games
    # and the every-game coverage step (coverage.record) records every game.
    window = coverage.forecast_window(conn, utc_now())
    if window != "upcoming":
        if window == "recent":
            return {"refresh": refresh(conn), "status": "skipped", "reason": "results refresh; no upcoming game within two hours",
                    "selector": decisions}
        return {"status": "skipped", "reason": "no forecastable game within two hours", "selector": decisions}
    return {**predict_now(conn), "selector": decisions}


def odds_cycle(conn: sqlite3.Connection, sport_name: str, *, force: bool = False) -> dict:
    """The periodic odds command: odds snapshot, due forecasts (with bounded recovery), then props -- after the
    time-critical forecasts, never before them. The same steps as the `odds` command of release 18fa0bf. Its own odds
    row is marked `cycle` so the cadence clock ignores the odds snapshots the forecast step and refusals also write."""
    snap = odds_snapshot(conn, force=force, props=False)
    conn.execute("UPDATE job_runs SET detail = json_set(COALESCE(detail, '{}'), '$.cycle', json('true')) WHERE run_id = ?",
                 (snap["run_id"],))
    result = {"odds": snap, "forecasts": forecast_due(conn)}
    snap["props"] = collect_props(conn, snap)
    return result


# The odds cycle keeps its installed cadence inside `tick` (launchd StartInterval: NBA 900 s, NHL 1800 s).
ODDS_CYCLE_EVERY = {"nba": timedelta(minutes=15), "nhl": timedelta(minutes=30)}


def last_cycle_at(conn: sqlite3.Connection) -> Optional[str]:
    """Start of the last odds cycle (an odds row marked `cycle`); rows written before this release have no mark."""
    return conn.execute("SELECT MAX(started_at) FROM job_runs WHERE job = 'odds' AND json_extract(detail, '$.cycle') = 1"
                        ).fetchone()[0]


def cycle_due(conn: sqlite3.Connection, sport_name: str, now: datetime) -> bool:
    """Whether the odds cycle (odds snapshot, forecasts, recovery, props) is due at its usual cadence. Forecast timing is
    therefore that of release 18fa0bf: `tick` never runs the forecast step between cycles."""
    from . import freshness
    last = last_cycle_at(conn)
    return last is None or now - _parse(last) >= ODDS_CYCLE_EVERY[sport_name] - freshness.TICK_SLACK


def tick(conn: sqlite3.Connection, *, now: Optional[datetime] = None, refresh_kw: Optional[dict] = None,
         cycle: Optional[Callable[[], dict]] = None) -> dict:
    """One coordinator step, meant to run every minute under the store's job lock (docs/REFRESH_PLAN.md). Refreshes
    schedule/status/results when the activity policy says it is due, then runs the odds cycle when its usual cadence
    (NBA 15 min, NHL 30 min) is due. A tick with nothing due makes no request and writes no job row."""
    from . import freshness
    now = now or datetime.now(timezone.utc)
    name = store_sport(conn) or "nba"
    if name not in ("nba", "nhl"):
        raise ValueError(f"no tick is defined for a {name!r} store")
    plan = freshness.plan(conn, now)
    out = {"sport": name, "as_of": _fmt(now), "policy": _policy(plan)}
    out["refresh"] = refresh(conn, now=now, **(refresh_kw or {})) if plan["due"] else \
        {"status": "not_due", "next_due_at": plan["next_due_at"], "activity": plan["activity"]}
    out["cycle"] = cycle_due(conn, name, now)
    if out["cycle"]:
        out.update((cycle or (lambda: odds_cycle(conn, name)))())
    return out


def scheduler(conn: sqlite3.Connection, *, interval_minutes: float, max_runs: int, job: str = "daily",
              log: Callable[[str], None] = print, sleep: Callable[[float], None] = time.sleep,
              lock: Optional[Callable] = None) -> list[dict]:
    """Runs `daily` or `odds` at a fixed interval, a bounded number of times (no unbounded background process). `lock`, if
    given, returns the store's job lock: it is held per iteration, never through the sleep between iterations."""
    if job not in SCHEDULER_MIN_INTERVAL:
        raise ValueError(f"job must be one of {sorted(SCHEDULER_MIN_INTERVAL)}")
    minimum = SCHEDULER_MIN_INTERVAL[job]
    if max_runs < 1 or interval_minutes < minimum:
        raise ValueError(f"max_runs must be >= 1 and interval_minutes >= {minimum} ({job} quota)")
    from contextlib import nullcontext
    out = []
    for i in range(max_runs):
        if i:
            sleep(interval_minutes * 60)
        with (lock() if lock else nullcontext(True)) as held:
            if not held:
                result = {"status": "skipped", "reason": "another job is running on this store"}
                log(json.dumps(result))
            elif job == "daily":
                result = daily(conn)
                log(json.dumps({k: v.get("status") for k, v in result.items() if isinstance(v, dict)}))
            else:
                result = {"odds": odds_snapshot(conn), "forecasts": forecast_due(conn)}
                log(json.dumps({"odds": result["odds"].get("status"), "forecasts": result["forecasts"].get("status")}))
        out.append(result)
    return out
