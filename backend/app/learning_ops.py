"""Operational status of adaptive learning, per sport: what is installed, what the fit and job rows say. Read-only.

Nothing here is hashed into a model definition and nothing here infers that forecasts use fits: that is shown only by
`learning_coverage` from recorded forecast rows. Stores are opened with `mode=ro` (immutable snapshot as a stated fallback)."""
import json
import os
import plistlib
import re
import sqlite3
from pathlib import Path
from typing import Optional

from . import config

TRAINER_LABEL = "com.nba-predictor.adaptive-train"
ODDS_LABEL = "com.nba-predictor.odds"
RECORDING_VAR = "NBA_DAILY_AGREEMENT"
DEEP_MARKERS = ("torch", "tensorflow", "keras")
NOTE = ("Operational facts only: what is installed and what the stored fit and job rows say. They do not show that "
        "forecasts use these fits; see the coverage matrix for recorded forecasts.")


def agents_dir() -> Path:
    return Path(os.environ.get("LEARNING_LAUNCHAGENTS_DIR") or Path.home() / "Library" / "LaunchAgents")


def open_ro(data_dir: Path, sport: str) -> tuple[Optional[sqlite3.Connection], str]:
    """(connection, read mode) or (None, reason). Never writes; falls back to an immutable snapshot (WAL ignored) only
    when a plain read-only open is refused."""
    path = Path(data_dir) / f"{sport}.sqlite"
    if not path.is_file():
        return None, "missing"
    for mode in ("mode=ro", "immutable=1"):
        try:
            conn = sqlite3.connect(f"{path.resolve().as_uri()}?{mode}", uri=True)
            conn.row_factory = sqlite3.Row
            conn.execute("SELECT 1 FROM sqlite_master LIMIT 1")
            return conn, "read-only" if mode == "mode=ro" else "immutable snapshot (WAL not read)"
        except sqlite3.Error:
            continue
    return None, "unreadable"


def has_table(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def load_plist(folder: Path, label: str) -> Optional[dict]:
    p = Path(folder) / f"{label}.plist"
    try:
        return plistlib.loads(p.read_bytes())
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None


def trainer(sport: str, folder: Path) -> dict:
    p = load_plist(folder, TRAINER_LABEL)
    if p is None:
        return {"installed": False, "covers_sport": False, "label": TRAINER_LABEL, "times": [], "sports": [],
                "reason": f"not scheduled: {TRAINER_LABEL}.plist not found in {folder} (a file shows installed, not loaded)"}
    command = " ".join(str(a) for a in p.get("ProgramArguments", []))
    loop = re.search(r"for s in ([a-z0-9 ]+);", command)
    sports = loop.group(1).split() if loop else []
    steps = [s for s in ("training-inputs", "adaptive-train --cadence daily", "adaptive-train") if s in command]
    calendar = p.get("StartCalendarInterval") or []
    calendar = [calendar] if isinstance(calendar, dict) else calendar
    times = [f"{int(c.get('Hour', 0)):02d}:{int(c.get('Minute', 0)):02d}" for c in calendar]
    covers = sport in sports and "adaptive-train" in command
    return {"installed": True, "covers_sport": covers, "label": p.get("Label", TRAINER_LABEL), "times": times, "sports": sports,
            "program_summary": f"per sport in [{', '.join(sports)}]: {', '.join(steps) or 'unrecognised command'}",
            "reason": None if covers else f"not scheduled: {sport} is not in the trainer's sport loop"}


def recording(folder: Path) -> dict:
    p = load_plist(folder, ODDS_LABEL)
    base = {"env_var": RECORDING_VAR, "job_label": ODDS_LABEL}
    if p is None:
        return {**base, "configured": None, "note": f"{ODDS_LABEL}.plist not found in {folder}: not known"}
    value = (p.get("EnvironmentVariables") or {}).get(RECORDING_VAR)
    return {**base, "configured": value == "1", "value": value,
            "note": "Configuration of the installed job only; recorded rows are in the coverage matrix."}


def _fits(conn, sport: str) -> list[dict]:
    if not has_table(conn, "adaptive_fits"):
        return []
    out = []
    for d in conn.execute("SELECT definition, MAX(fit_id) last FROM adaptive_fits WHERE sport=? GROUP BY definition ORDER BY last DESC", (sport,)):
        c = conn.execute("SELECT fit_id, status, finished_at, period, schedule FROM adaptive_fits WHERE sport=? AND definition=? AND kind='combiner' "
                         "ORDER BY fit_id DESC LIMIT 1", (sport, d["definition"])).fetchone()
        members = {"success": 0, "partial": 0, "skipped": 0, "failed": 0}
        reasons: list[str] = []
        failures: list[str] = []
        period = conn.execute("SELECT period FROM adaptive_fits WHERE sport=? AND definition=? AND kind='member' ORDER BY fit_id DESC LIMIT 1",
                              (sport, d["definition"])).fetchone()
        latest = {}
        if period:     # latest row per member within the latest period (a retry may follow a failure)
            for r in conn.execute("SELECT name, status, detail, fit_id FROM adaptive_fits WHERE sport=? AND definition=? AND kind='member' AND period=? "
                                  "ORDER BY fit_id", (sport, d["definition"], period["period"])):
                latest[r["name"]] = r
        for r in latest.values():
            members[r["status"]] += 1
            if r["status"] in ("skipped", "failed"):
                try:
                    detail = json.loads(r["detail"])
                    reason = detail.get("reason") or detail.get("error")
                except (ValueError, AttributeError):
                    reason = None
                bucket = reasons if r["status"] == "skipped" else failures
                if reason and reason not in bucket:
                    bucket.append(reason)
        out.append({"definition": d["definition"], "combiner": dict(c) if c else None, "period": period["period"] if period else None,
                    "members": members, "skipped_reasons": reasons, "failed_reasons": failures})
    return out


def _job_runs(conn) -> list[dict]:
    if not has_table(conn, "job_runs"):
        return []
    out = []
    for r in conn.execute("SELECT run_id, job, status, started_at, finished_at, detail FROM job_runs WHERE job='training_inputs' "
                          "ORDER BY run_id DESC LIMIT 5"):
        try:
            deferred = json.loads(r["detail"] or "{}").get("deferred_by")
        except (ValueError, AttributeError):
            deferred = None
        out.append({"job": r["job"], "status": r["status"], "started_at": r["started_at"], "finished_at": r["finished_at"], "deferred_by": deferred})
    return out


def status(sport: str, data_dir: Optional[Path] = None, folder: Optional[Path] = None) -> dict:
    data_dir = Path(data_dir) if data_dir else config.data_dir()
    folder = Path(folder) if folder else agents_dir()
    t = trainer(sport, folder)
    out = {"sport": sport, "trainer": t, "recording": recording(folder), "fits": [], "job_runs": [], "deep_members": None,
           "adaptive_training_job_note": "adaptive-train has no job_runs job name: its outcome is the adaptive_fits rows; a run "
                                         "deferred by the input check is a failed training_inputs row (deferred_by).",
           "note": NOTE}
    conn, mode = open_ro(data_dir, sport)
    if conn is None:
        return {**out, "store": mode}
    try:
        out["store"] = mode
        out["fits"] = _fits(conn, sport)
        out["job_runs"] = _job_runs(conn)
    except sqlite3.Error as exc:
        out["store"] = f"error: {exc}"
    finally:
        conn.close()
    skipped = [r for f in out["fits"] for r in f["skipped_reasons"]]
    out["deep_members"] = "unverified" if any(m in r.lower() for r in skipped for m in DEEP_MARKERS) else "none_skipped"
    if out["deep_members"] == "unverified":
        out["deep_members_note"] = "Deep members are skipped in the trainer's environment; they are not fitted and not verified."
    return out
