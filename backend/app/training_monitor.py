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
    for f in fits:
        f.update(_versions_and_next(conn, f, now))
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
    return {"next_update": nxt, "versions": {"definition": row["definition"] if row else None,
                                             "libraries": row["libraries"] if row else None,
                                             "params": f["params_digest"], "calibration": CALIBRATION}}


def _online(conn, now: str) -> list:
    """Online members update after every result: their last update is the latest delivered final result."""
    from . import agreement, research_live
    sport = conn.execute("SELECT sport FROM store_identity").fetchone()
    if not sport:
        return []
    sport = sport[0]
    last = conn.execute("SELECT max(result_available_at) FROM games WHERE status='final' AND result_available_at<=?",
                        (now,)).fetchone()[0]
    online = {"nba": ("elo", "points_normal", "efficiency_margin (ratings; spread refits at season start)"),
              "nhl": ("hockey_elo", "goals_poisson")}[sport]
    known = set(agreement.NAMES[sport]) | set(research_live.LINE_MEMBERS[sport])
    return [{"member": m, "schedule": "after every result (replayed at each forecast)", "last_update_input": last,
             "next_update": "the next delivered final result", "registered": m.split(" ")[0] in known} for m in online]


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
        "WHERE c.market IN ('winner', 'reg3') AND c.definition NOT LIKE '%:expanded-v1' AND c.cohort_id = (SELECT x.cohort_id "
        "FROM research_cohorts x WHERE x.game_id=c.game_id AND x.market=c.market AND x.definition NOT LIKE '%:expanded-v1' "
        "AND x.generated_at < x.tipoff_at ORDER BY x.generated_at DESC, x.cohort_id DESC LIMIT 1) ORDER BY c.tipoff_at").fetchall()
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
