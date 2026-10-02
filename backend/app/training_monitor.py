"""Daily training check (docs/MODEL_SELECTION.md §13 schedule; §17): for every learned member with a registered fit, the fit
in force, its declared schedule, and how many newly settled eligible results exist since the fit's training cutoff; plus
input freshness. Reports only: it never refits a sealed member or changes a schedule (deliberate changes are new versions)."""
from datetime import datetime, timedelta, timezone
from typing import Optional

from .db import utc_now

FMT = "%Y-%m-%dT%H:%M:%SZ"
STALE_JOB_HOURS = {"refresh": 26, "odds": 2}
SCHEDULE_NOTE = {"season_start": "refits at the first forecast of a new season only",
                 "weekly": "refits at the first forecast of each ISO week (and at season start)"}


def _has(conn, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


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
            stale = last is None or datetime.strptime(last, FMT).replace(tzinfo=timezone.utc) < \
                datetime.strptime(now, FMT).replace(tzinfo=timezone.utc) - timedelta(hours=hours)
            jobs[job] = {"last_success": last, "stale": stale, "stale_after_hours": hours}
    return {"as_of": now, "fits": fits, "inputs": jobs,
            "note": "Online members (Elo, points model, goals model) update after every result and have no stored fit. "
                    "Learned members follow their declared schedule; this check never refits or retunes."}
