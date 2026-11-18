"""Helpers for the learning ops / coverage tests: tiny migrated stores and fake LaunchAgents folders. No network."""
import json
import plistlib
from pathlib import Path

from app import db

T0 = "2026-11-17T10:00:00Z"
DEF = "d" * 64


def make_store(data_dir: Path, sport: str):
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = db.connect(data_dir / f"{sport}.sqlite", sport=sport)
    db.migrate(conn)
    db.claim_store(conn, sport)
    return conn


def add_fit(conn, sport, kind, name, status, finished=T0, definition=DEF, schedule="daily", detail=None, fit_id=None, period="2027:2026-11-17"):
    cur = conn.execute(
        "INSERT INTO adaptive_fits (sport, kind, name, definition, period, schedule, status, training_cutoff, detail, started_at, "
        "finished_at, idempotency_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (sport, kind, name, definition, period, schedule, status, finished if status in ("success", "partial") else None,
         json.dumps(detail or {}), finished, finished, f"{sport}-{kind}-{name}-{definition[:3]}-{finished}-{status}-{schedule}-{period}"))
    return cur.lastrowid


def combiner_detail(*trained):
    return {"markets": {m: {"weights": {"a": 1.0}, "reason": None, "members": ["a"]} for m in trained}}


def add_forecast(conn, sport, market, status, fit_id, generated, n=1, definition=DEF):
    """A recorded adaptive forecast; the schema's two-hour trigger and foreign key are bypassed for the fixture only."""
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DROP TRIGGER IF EXISTS adaptive_forecasts_two_hour_window")
    conn.execute("INSERT OR IGNORE INTO teams (team_id, name, abbreviation) VALUES ('t1','T1','T1'), ('t2','T2','T2')") if False else None
    for i in range(n):
        p = (0.5, 0.0, 0.5) if status == "forecast" else (None, None, None)
        line = None if market in ("winner", "reg3") else 5.5
        conn.execute(
            "INSERT INTO adaptive_forecasts (game_id, sport, market, line, line_source, odds_status, model_version, definition, "
            "combiner_fit_id, status, reason, p_a, p_mid, p_b, members, missing, weights, selection, information_at, generated_at, "
            "tipoff_at, idempotency_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"g{market}{generated}{i}", sport, market, line, "none" if line is None else "market", "available", "adaptive-v1:x", definition,
             fit_id, status, None if status == "forecast" else "no fit", *p, "[]", "{}", "null", "{}", generated, generated,
             generated, f"{sport}{market}{status}{generated}{i}{fit_id}"))


def write_agents(folder: Path, *, trainer=True, odds_env=None, trainer_sports="nhl nba", hours=((9, 30), (15, 30))):
    folder.mkdir(parents=True, exist_ok=True)
    if trainer:
        cmd = (f"PY=/x/python; rc=0; for s in {trainer_sports}; do if $PY -m app.cli --sport $s training-inputs; then "
               "$PY -m app.cli --sport $s adaptive-train || rc=1; $PY -m app.cli --sport $s adaptive-train --cadence daily || rc=1; "
               "else rc=1; fi; done; exit $rc")
        (folder / "com.nba-predictor.adaptive-train.plist").write_bytes(plistlib.dumps({
            "Label": "com.nba-predictor.adaptive-train", "ProgramArguments": ["/usr/bin/caffeinate", "-i", "/bin/sh", "-c", cmd],
            "StartCalendarInterval": [{"Hour": h, "Minute": m} for h, m in hours]}))
    if odds_env is not None:
        (folder / "com.nba-predictor.odds.plist").write_bytes(plistlib.dumps({
            "Label": "com.nba-predictor.odds", "ProgramArguments": ["/x/python", "-m", "app.cli", "odds"],
            "EnvironmentVariables": odds_env, "StartInterval": 60}))
