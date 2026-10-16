"""Daily training check (docs/MODEL_SELECTION.md §13 schedule; §17): for every learned member with a registered fit, the fit
in force, its declared schedule, and how many newly settled eligible results exist since the fit's training cutoff; plus
input freshness. Reports only: it never refits a sealed member or changes a schedule (deliberate changes are new versions)."""
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .db import utc_now

FMT = "%Y-%m-%dT%H:%M:%SZ"
STALE_JOB_HOURS = {"refresh": 26, "odds": 2, "training_inputs": 30}   # training_inputs: 09:30 run + 15:30 retry
SCHEDULE_NOTE = {"season_start": "refits at the first forecast of a new season only",
                 "weekly": "refits at the first forecast of each ISO week (and at season start)"}


def _expanded() -> str:
    from .research_live import EXPANDED     # the one definition suffix of the expanded experiment
    return EXPANDED


def _has(conn, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _failure_reason(detail: Optional[str]) -> str:
    try:
        d = json.loads(detail or "{}")
    except ValueError:
        return "unreadable detail"
    blocking = (d.get("check") or {}).get("blocking")
    if blocking:                          # training_inputs: what training waits for
        return "training inputs not ready: " + ", ".join(sorted(blocking))
    if d.get("failed_steps"):             # training_inputs: a collector failed although training could proceed
        return "collection step(s) failed: " + ", ".join(d["failed_steps"])
    return str(d.get("error") or d.get("reason") or "failed")[:200]


def status(conn, now: Optional[str] = None) -> dict:
    now = now or utc_now()
    fits = []
    if _has(conn, "model_fits"):
        rows = conn.execute(
            "SELECT f.* FROM model_fits f WHERE f.first_used_at<=? AND f.fit_id = (SELECT x.fit_id FROM model_fits x "
            "WHERE x.sport=f.sport AND x.member=f.member AND x.first_used_at<=? ORDER BY x.first_used_at DESC, x.fit_id DESC "
            "LIMIT 1) ORDER BY f.member", (now, now)).fetchall()
        for f in rows:
            new = conn.execute("SELECT count(*) FROM games WHERE status='final' AND season_type<>'preseason' "
                               "AND result_available_at IS NOT NULL "
                               "AND result_available_at>? AND result_available_at<=?",
                               (f["results_available_through"], now)).fetchone()[0]
            fits.append({"member": f["member"], "schedule": f["schedule"], "schedule_note": SCHEDULE_NOTE.get(f["schedule"]),
                         "fit_kind": f["fit_kind"], "trained_season": f["trained_season"],
                         "training_games": f["training_games"], "results_available_through": f["results_available_through"],
                         "params_digest": f["params_digest"], "results_fingerprint": f["results_fingerprint"],
                         "fit_in_force_since": f["first_used_at"], "new_settled_results_since_fit": new,
                         "action": "none: the next refit happens on the declared schedule" if new else "none: no new results"})
    jobs = {}
    if _has(conn, "job_runs"):
        for job, hours in STALE_JOB_HOURS.items():
            r = conn.execute("SELECT finished_at FROM job_runs WHERE job=? AND status='success' ORDER BY finished_at DESC LIMIT 1",
                             (job,)).fetchone()
            last = r[0] if r else None
            # A run that finished 'skipped' because nothing was due (no game near, quota) is a healthy check; a skip
            # because the store lock was busy is not.
            c = conn.execute("SELECT finished_at FROM job_runs WHERE job=? AND finished_at IS NOT NULL AND (status='success' OR "
                             "(status='skipped' AND COALESCE(detail, '') NOT LIKE '%\"lock_busy\": true%')) "
                             "ORDER BY finished_at DESC LIMIT 1", (job,)).fetchone()
            checked = c[0] if c else None
            stale = checked is None or datetime.strptime(checked, FMT).replace(tzinfo=timezone.utc) < \
                datetime.strptime(now, FMT).replace(tzinfo=timezone.utc) - timedelta(hours=hours)
            jobs[job] = {"last_success": last, "last_checked": checked, "stale": stale, "stale_after_hours": hours}
            # The latest finished run, so a failure shows at once rather than only once the job turns stale.
            r = conn.execute("SELECT status, finished_at, detail FROM job_runs WHERE job=? AND finished_at IS NOT NULL "
                             "ORDER BY finished_at DESC, run_id DESC LIMIT 1", (job,)).fetchone()
            jobs[job]["last_status"] = r[0] if r else None
            jobs[job]["last_failure"] = {"at": r[1], "reason": _failure_reason(r[2])} if r and r[0] == "failed" else None
            # A run killed mid-way never finishes: its 'running' row shows as a failure once it is older than two hours.
            u = conn.execute("SELECT started_at FROM job_runs WHERE job=? AND finished_at IS NULL AND status='running' "
                             "ORDER BY started_at DESC LIMIT 1", (job,)).fetchone()
            if u and (r is None or u[0] > r[1]) and datetime.strptime(u[0], FMT).replace(tzinfo=timezone.utc) < \
                    datetime.strptime(now, FMT).replace(tzinfo=timezone.utc) - timedelta(hours=2):
                jobs[job]["last_status"] = "running"
                jobs[job]["last_failure"] = {"at": u[0], "reason": "did not finish (killed, or still running after 2 h)"}
    for f in fits:
        f.update(_versions_and_next(conn, f, now))
        if f["member"].startswith("cand:calibrated:"):
            f["schedule_note"] = "frozen calibration; explicit preparation required for each new season"
            f["action"] = "no automatic refit; prepare and review the next season calibration artifact"
    return {"as_of": now, "fits": fits, "inputs": jobs, "online": _online(conn, now), "updates": _update_events(conn),
            "monitoring": _monitoring(conn, now),
            "note": "Online members (Elo, points model, goals model) update after every result and have no stored fit. "
                    "Learned members follow their declared schedule; this check never refits or retunes."}


ISO_WEEK_NOTE = "first two-hour forecast of the next ISO week (Monday, US Eastern)"
CALIBRATION = "none: no fitted calibration layer (reliability tables only); stackers refit their own weights"


def _next_monday(now: str) -> str:
    from zoneinfo import ZoneInfo
    et = datetime.strptime(now, FMT).replace(tzinfo=timezone.utc).astimezone(ZoneInfo("America/New_York"))
    monday = (et + timedelta(days=7 - et.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return monday.astimezone(timezone.utc).strftime(FMT)


def _versions_and_next(conn, f: dict, now: str) -> dict:
    """Next scheduled update and the versions that identify the fit in force. Never triggers anything."""
    row = conn.execute("SELECT definition, libraries FROM model_fits WHERE member=? AND params_digest=? ORDER BY fit_id DESC LIMIT 1",
                       (f["member"], f["params_digest"])).fetchone()
    nxt_season = (f["trained_season"] or 0) + 1
    first = conn.execute("SELECT min(tipoff_at) FROM games WHERE season=? AND season_type<>'preseason'", (nxt_season,)).fetchone()[0]
    if f["schedule"] == "weekly":
        nxt = {"when": _next_monday(now), "rule": ISO_WEEK_NOTE}
    else:
        nxt = {"when": first, "rule": f"first two-hour forecast of season {nxt_season}"
               + ("" if first else " (schedule not yet published)")}
    calibration = CALIBRATION
    if f["member"].startswith("cand:calibrated:"):
        calibration = "frozen earlier-season OOF Platt/temperature calibration; shadow only"
        nxt = {"when": None, "rule": f"explicit calibration artifact preparation for season {nxt_season}"}
    return {"next_update": nxt, "versions": {"definition": row["definition"] if row else None,
                                             "libraries": row["libraries"] if row else None,
                                             "params": f["params_digest"], "calibration": calibration}}


def _online(conn, now: str) -> list:
    """Separate available eligible results from consumption proven by forecast provenance."""
    from . import agreement, research_live, live, near_tipoff
    sport = conn.execute("SELECT sport FROM store_identity").fetchone()
    if not sport:
        return []
    sport = sport[0]
    last = conn.execute("SELECT max(result_available_at) FROM games WHERE status='final' AND result_available_at<=? "
                        "AND season_type IN (" + ",".join("?" for _ in live.engine(conn).eligible_types) + ")",
                        (now, *live.engine(conn).eligible_types)).fetchone()[0]
    online = {"nba": ("elo", "points_normal", "efficiency_margin (ratings; spread refits at season start)"),
              "nhl": ("hockey_elo", "goals_poisson")}[sport]
    known = set(agreement.NAMES[sport]) | set(research_live.LINE_MEMBERS[sport])
    proof = None
    if _has(conn, "near_tipoff_forecasts"):
        try:
            version = near_tipoff.current_version(conn)
        except (ValueError, RuntimeError):
            version = None
        if version:
            proof = conn.execute(
                "SELECT n.* FROM near_tipoff_forecasts n JOIN predictions p USING(prediction_id) "
                "WHERE p.model_version=? AND p.run_type='live' AND n.generated_at<=? "
                "ORDER BY n.generated_at DESC, n.prediction_id DESC LIMIT 1", (version, now)).fetchone()
    primary = "elo" if sport == "nba" else "hockey_elo"
    return [{"member": m, "schedule": "result updates replayed at forecast time",
             "latest_available_result": last,
             "last_update_input": proof["latest_result_available_at"] if proof and m == primary else None,
             "consumption_verified": bool(proof and m == primary),
             "verified_at": proof["generated_at"] if proof and m == primary else None,
             "results_used": proof["results_used"] if proof and m == primary else None,
             "next_update": "next forecast using newly available eligible results",
             "registered": m.split(" ")[0] in known} for m in online]



def _update_events(conn, limit: int = 40) -> list:
    """Skipped or failed member updates and recordings with their reasons, from the latest two-hour job runs."""
    import json
    if not _has(conn, "job_runs"):
        return []
    out = []
    for r in conn.execute("SELECT run_id, started_at, status, detail FROM job_runs WHERE job='near_tipoff' "
                          "ORDER BY run_id DESC LIMIT ?", (limit,)):
        try:
            d = json.loads(r["detail"] or "{}")
        except ValueError:
            continue
        for step in ("agreement", "research", "pool", "pool_selection", "tracking"):
            sd = d.get(step) or {}
            if not isinstance(sd, dict):
                continue
            if sd.get("status") == "failed":
                out.append({"run_id": r["run_id"], "at": r["started_at"], "step": step, "member": None, "outcome": "failed",
                            "reason": sd.get("error") or "failed"})
            for k, v in (sd.get("errors") or {}).items():
                out.append({"run_id": r["run_id"], "at": r["started_at"], "step": step, "member": k, "outcome": "failed",
                            "reason": str(v)[:240]})
            for member, games in (sd.get("skipped") or {}).items():
                reasons = sorted(set(games.values())) if isinstance(games, dict) else [str(games)]
                out.append({"run_id": r["run_id"], "at": r["started_at"], "step": step, "member": member, "outcome": "skipped",
                            "reason": "; ".join(reasons)[:240]})
    return out[:200]


def _monitoring(conn, now: str, window: int = 100) -> dict:
    """Rolling log loss of each core member's two-hour winner/reg3 forecasts (canonical research cohorts): the latest
    `window` settled games against the previous `window`. An alert needs a full window in both and a difference beyond two
    standard errors; it only reports — nothing is retuned after a short losing streak."""
    import json
    import math
    from . import combine, markets
    if not _has(conn, "research_cohorts"):
        return {"members": [], "alerts": []}
    rows = conn.execute(
        "SELECT c.*, g.status AS g_status, g.home_score, g.away_score, g.result_available_at, g.tipoff_at AS game_tipoff_at, "
        "d.decision FROM research_cohorts c JOIN games g ON g.game_id=c.game_id LEFT JOIN game_decisions d ON d.game_id=c.game_id "
        "WHERE c.market IN ('winner', 'reg3') AND c.definition NOT LIKE ? AND c.cohort_id = (SELECT x.cohort_id "
        "FROM research_cohorts x WHERE x.game_id=c.game_id AND x.market=c.market AND x.definition NOT LIKE ? "
        "AND x.generated_at < x.tipoff_at ORDER BY x.generated_at DESC, x.cohort_id DESC LIMIT 1) ORDER BY c.tipoff_at",
        (f"%:{_expanded()}", f"%:{_expanded()}")).fetchall()
    series, missing = {}, {}
    for r in rows:
        for name, why in json.loads(r["missing"]).items():
            missing.setdefault((r["market"], name), []).append(why)
        if r["g_status"] != "final" or r["tipoff_at"] != r["game_tipoff_at"] or not r["result_available_at"] \
                or r["result_available_at"] > now:
            continue
        s = markets.settle(r["market"], None, status=r["g_status"], home=r["home_score"], away=r["away_score"], decision=r["decision"])
        realised = {"a": 0, "mid": 1, "b": 2}.get(s)
        if realised is None:
            continue
        for m in json.loads(r["members"]):
            series.setdefault((r["market"], m["name"]), []).append(combine.log_loss(m["p"], realised))
    members, alerts = [], []
    for (market, name), ll in sorted(series.items()):
        recent, prev = ll[-window:], ll[-2 * window:-window]
        item = {"market": market, "member": name, "settled": len(ll), "recent_log_loss": sum(recent) / len(recent),
                "previous_log_loss": sum(prev) / len(prev) if prev else None, "window": window,
                "missing_rate": len(missing.get((market, name), [])) / max(1, sum(1 for r in rows if r["market"] == market))}
        if len(recent) == window and len(prev) == window:
            def var(x):
                m = sum(x) / len(x)
                return sum((v - m) ** 2 for v in x) / (len(x) - 1)
            se = math.sqrt(var(recent) / window + var(prev) / window)
            diff = item["recent_log_loss"] - item["previous_log_loss"]
            item["difference"], item["se"] = diff, se
            if diff > 2 * se:
                alerts.append({**item, "alert": "rolling log loss deteriorated beyond 2 standard errors (report only)"})
        else:
            item["status"] = f"insufficient evidence (needs {2 * window} settled two-hour forecasts)"
        members.append(item)
    return {"members": members, "alerts": alerts, "window": window,
            "note": "Monitoring reports only; it never retunes or replaces a model after a short run of results."}


# --- Training-run status (adaptive-train): what is running, what finished, what serves. Read-only: no lock is taken,
# nothing is written. "Running" is reported only with evidence (a process holding the sport's training lock file).
SNAPSHOT_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{6}Z)-(\d+)\.sqlite$")


def lock_holders(path: Path) -> Optional[list]:
    """PIDs with the training lock file open ([] if none), or None when that cannot be determined. The trainer keeps the
    file open for its whole run; `lsof` only lists open files, so this never contends for the lock."""
    if not path.exists():
        return []
    try:
        r = subprocess.run(["lsof", "-t", "--", str(path)], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode not in (0, 1) or (r.returncode == 1 and r.stdout.strip()):
        return None
    try:
        return sorted({int(x) for x in r.stdout.split()})
    except ValueError:
        return None


def _command(pid: int) -> Optional[str]:
    try:
        r = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip()[:300] or None


def _mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime(FMT)


def artifacts_written(train_root: Path, since: Optional[str] = None) -> dict:
    """Completed artifacts on disk per definition (a digest file is written last, after the artifact itself): distinct
    members with at least one, whether a combination artifact exists, and the newest write. Optionally only files
    written at or after `since`."""
    out: dict = {}
    for kind in ("members", "combiner"):
        base = train_root / kind
        if not base.is_dir():
            continue
        for digest in base.glob("*.pkl.sha256" if kind == "combiner" else "*/*.pkl.sha256"):
            at = _mtime(digest)
            if since and at < since:
                continue
            defn = digest.name.split("-", 1)[0]
            d = out.setdefault(defn, {"members": set(), "combiner": False, "last_written_at": None})
            if kind == "members":
                d["members"].add(digest.parent.name)
            else:
                d["combiner"] = True
            d["last_written_at"] = max(filter(None, (d["last_written_at"], at)))
    return {k: {"members_written": len(v["members"]), "combiner_written": v["combiner"],
                "last_written_at": v["last_written_at"]} for k, v in out.items()}


def _run_row(conn, row) -> Optional[dict]:
    """One training run, identified by its combination record: when, outcome, and the member records of that run."""
    if row is None:
        return None
    counts = dict(conn.execute("SELECT status, count(*) FROM adaptive_fits WHERE sport=? AND kind='member' AND definition=? "
                               "AND period=? AND finished_at<=? GROUP BY status",
                               (row["sport"], row["definition"], row["period"], row["finished_at"])).fetchall())
    detail = json.loads(row["detail"] or "{}")
    return {"definition": row["definition"], "period": row["period"], "status": row["status"],
            "started_at": row["started_at"], "finished_at": row["finished_at"], "training_cutoff": row["training_cutoff"],
            "members_succeeded": counts.get("success", 0) + counts.get("partial", 0),
            "members_failed": counts.get("failed", 0), "members_skipped": counts.get("skipped", 0),
            "error": detail.get("error") or detail.get("partial_reason")}


def run_status(live_conn, train_root: Path, sport: str, now: str, *, code_definition: Optional[str] = None,
               expected_members: Optional[int] = None, holders=lock_holders) -> dict:
    """Training state for one sport from the live store and training folder. Never estimates progress or time left:
    reports counts and timestamps that exist, and says so when something cannot be determined."""
    pids = holders(train_root / "train.lock")
    running = None if pids is None else bool(pids)
    current = None
    if running:
        snaps = []
        for f in (train_root / "snapshots").glob("*.sqlite") if (train_root / "snapshots").is_dir() else []:
            m = SNAPSHOT_NAME.match(f.name)
            if m and int(m.group(2)) in pids:
                snaps.append(datetime.strptime(m.group(1), "%Y-%m-%dT%H%M%SZ").strftime(FMT))
        started = max(snaps, default=None)
        written = artifacts_written(train_root, since=started) if started else {}
        current = {"started_at": started, "processes": [{"pid": p, "command": _command(p)} for p in pids],
                   "by_definition": written,
                   "last_progress_at": max((w["last_written_at"] for w in written.values()), default=None) or started}
    has = _has(live_conn, "adaptive_fits")
    last_run = last_success = last_failure = None
    published = None
    if has:
        last_run = _run_row(live_conn, live_conn.execute(
            "SELECT * FROM adaptive_fits WHERE sport=? AND kind='combiner' AND finished_at<=? ORDER BY finished_at DESC, "
            "fit_id DESC LIMIT 1", (sport, now)).fetchone())
        last_success = _run_row(live_conn, live_conn.execute(
            "SELECT * FROM adaptive_fits WHERE sport=? AND kind='combiner' AND status IN ('success', 'partial') AND "
            "finished_at<=? ORDER BY finished_at DESC, fit_id DESC LIMIT 1", (sport, now)).fetchone())
        f = live_conn.execute("SELECT * FROM adaptive_fits WHERE sport=? AND status='failed' AND finished_at<=? "
                              "ORDER BY finished_at DESC, fit_id DESC LIMIT 1", (sport, now)).fetchone()
        if f is not None:
            last_failure = {"definition": f["definition"], "kind": f["kind"], "name": f["name"], "at": f["finished_at"],
                            "error": json.loads(f["detail"] or "{}").get("error")}
        if code_definition:
            counts = dict(live_conn.execute(
                "SELECT status, count(*) FROM adaptive_fits WHERE sport=? AND kind='member' AND definition=? AND finished_at<=? "
                "GROUP BY status", (sport, code_definition, now)).fetchall())
            comb = live_conn.execute("SELECT status, finished_at FROM adaptive_fits WHERE sport=? AND kind='combiner' AND "
                                     "definition=? AND finished_at<=? ORDER BY finished_at DESC, fit_id DESC LIMIT 1",
                                     (sport, code_definition, now)).fetchone()
            published = {"members_succeeded": counts.get("success", 0) + counts.get("partial", 0),
                         "members_failed": counts.get("failed", 0),
                         "combiner": dict(comb) if comb else None}
    serving_adaptive = serving_primary = None
    recording: dict = {}        # definition -> rows recorded (forecast / unavailable) and the latest of each
    if _has(live_conn, "adaptive_forecasts"):
        for r in live_conn.execute("SELECT definition, status, count(*) n, MAX(generated_at) last FROM adaptive_forecasts "
                                   "WHERE sport=? AND generated_at<=? GROUP BY definition, status", (sport, now)):
            d = recording.setdefault(r["definition"], {"definition": r["definition"], "forecasts": 0, "unavailable": 0,
                                                       "last_forecast_at": None, "last_row_at": None})
            d["forecasts" if r["status"] == "forecast" else "unavailable"] += r["n"]
            if r["status"] == "forecast":
                d["last_forecast_at"] = r["last"]
            d["last_row_at"] = max(d["last_row_at"] or "", r["last"])
        # The definition of the latest COMBINED forecast (never an 'unavailable' row); every recording definition is listed.
        latest = max((d for d in recording.values() if d["last_forecast_at"]), key=lambda d: d["last_forecast_at"], default=None)
        if latest is not None:
            serving_adaptive = {**latest, "definitions": sorted(recording.values(), key=lambda d: d["last_row_at"], reverse=True)}
    if _has(live_conn, "near_tipoff_forecasts"):
        r = live_conn.execute("SELECT p.model_version, n.generated_at FROM near_tipoff_forecasts n JOIN predictions p "
                              "USING(prediction_id) WHERE p.run_type='live' AND n.generated_at<=? "
                              "ORDER BY n.generated_at DESC, n.prediction_id DESC LIMIT 1", (now,)).fetchone()
        if r is not None:
            serving_primary = {"model_version": r["model_version"], "last_forecast_at": r["generated_at"]}
    disk = artifacts_written(train_root)
    on_disk = disk.get(code_definition) if code_definition else None
    # Trained but not making forecasts: model files on disk whose version has no published record (an unfinished or
    # unpublished run), and the newest published run when the latest live forecast used a different version.
    known = {r[0] for r in live_conn.execute("SELECT DISTINCT definition FROM adaptive_fits WHERE sport=?", (sport,))} if has else set()
    not_serving = [{"definition": d, "state": "written, not published", "members_written": a["members_written"],
                    "at": a["last_written_at"]} for d, a in sorted(disk.items(), key=lambda x: x[1]["last_written_at"] or "")
                   if d not in known and a["members_written"]]
    if last_success:            # published: is it recording since? (judged per definition, not by the single newest row)
        rec = recording.get(last_success["definition"])
        since = rec is not None and (rec["last_row_at"] or "") >= (last_success["finished_at"] or "")
        state = (None if since and rec["last_forecast_at"] and rec["last_forecast_at"] >= last_success["finished_at"]
                 else "published, recording only 'unavailable' rows (no combined probability) since" if since
                 else "published, no live forecast yet")
        if state:
            not_serving.append({"definition": last_success["definition"], "state": state,
                                "members_written": last_success["members_succeeded"], "at": last_success["finished_at"]})
    return {"sport": sport, "as_of": now, "running": running,
            "running_note": {None: "Could not determine whether training is running (lsof unavailable or failed).",
                             True: "A process holds this sport's training lock.",
                             False: "No process holds this sport's training lock."}[running],
            "current_run": current, "last_run": last_run, "last_success": last_success, "last_failure": last_failure,
            "code_definition": code_definition, "expected_members": expected_members,
            "code_definition_published": published, "code_definition_on_disk": on_disk, "not_serving": not_serving,
            "serving": {"adaptive": serving_adaptive, "primary": serving_primary},
            "note": "Training completion is not predictive performance: a finished run says nothing about accuracy, which is "
                    "measured only on settled games. Progress is counted from artifacts written; no percentage or time left "
                    "is estimated."}
