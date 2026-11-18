"""Owner budget gate for PAID autoresearch generation: one code path for the scheduled and the manual wrapper.

`python -m app.autoresearch_budget check` prints the prepare.py argv (one per line) or `refused: ...` (exit 1);
`python -m app.autoresearch_budget record` appends finished runs to agent/budget-ledger.jsonl and, if a run used a model other
than the requested one, writes agent/MODEL_MISMATCH_STOP, which the gate then refuses on until the owner deletes it.
Authority: AGENT_BUDGET.json (owner). gate_digest = sha256 of the bytes of backend/app/autoresearch_budget.py, scripts/autoresearch_agent_scheduled.sh
and scripts/autoresearch_agent_manual.sh concatenated in that order (`cat` of the three | shasum -a 256); the shell refuses on mismatch. Counting: every run in agent/runs.jsonl started at or after counting_from counts, however it
was started. A run's spend is its recorded cost_usd (research_agent._finish sums total_cost_usd of ALL attempts, so the launch
retry is included), raised to the sum of the result records in its agent-*.jsonl transcripts if that is larger; a missing cost
counts at max_usd_per_run. Unreadable runs.jsonl or report.json, or a negative/non-finite cost: refuse (cost tracking failed).
Reads only; the single write is the ledger and the marker. Never starts anything."""
import argparse
import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

FMT = "%Y-%m-%dT%H:%M:%SZ"
NIGHT_START_H, NIGHT_END_H = 20.75, 7.0       # UTC: no paid start from 20:45Z (21:30Z minus the 45 min guard limit) to 07:00Z
TRAINER_LABEL = "com.nba-predictor.adaptive-train"
MARKER = "MODEL_MISMATCH_STOP"
LEDGER = "budget-ledger.jsonl"


class Refused(Exception):
    pass


def home() -> Path:
    return Path.home() / "autoresearch-sports"      # the home prepare.py hardcodes; no override


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value, field):
    try:
        return datetime.strptime(value, FMT).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        raise Refused(f"{field} must be a UTC timestamp like 2026-11-18T09:00:00Z")


def _num(b, field, integer=False):
    v = b.get(field)
    if not isinstance(v, int if integer else (int, float)) or isinstance(v, bool) or not math.isfinite(v) or v < (1 if integer else 1e-12):
        raise Refused(f"{field} must be a {'whole number >= 1' if integer else 'number > 0'}")
    return v


def load_budget(path: Path, now: datetime) -> dict:
    try:
        b = json.load(open(path))
    except (OSError, ValueError) as exc:
        raise Refused(f"owner budget file {path} is absent or unreadable ({exc}); a paid run needs it")
    if not isinstance(b, dict):
        raise Refused(f"{path} must hold a JSON object")
    if not isinstance(b.get("approved_by"), str) or not b["approved_by"].strip():
        raise Refused("approved_by must name the approving owner")
    if _utc(b.get("approved_at"), "approved_at") > now:
        raise Refused("approved_at is in the future")
    if _utc(b.get("expires_at"), "expires_at") <= now:
        raise Refused(f"budget expired at {b['expires_at']}")
    b["_counting_from"] = _utc(b.get("counting_from"), "counting_from")
    if b.get("period") != "weekly":
        raise Refused("period must be weekly (ISO week, UTC, Monday 00:00Z)")
    if not isinstance(b.get("gate_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", b["gate_sha256"]):
        raise Refused("gate_sha256 must be the 64 hex sha256 of the gate (see gate_digest in the module docstring)")
    cap, per_run = _num(b, "period_usd_cap"), _num(b, "max_usd_per_run")
    _num(b, "max_runs_per_period", True), _num(b, "max_total_runs", True), _num(b, "max_trials", True)
    if per_run > cap:
        raise Refused("max_usd_per_run must not exceed period_usd_cap")
    if not isinstance(b.get("model"), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:\-]*", b["model"]):
        raise Refused("model must be an explicit model id (letters, digits, . _ : -), never empty or a default")
    sports = b.get("sports")
    if not isinstance(sports, list) or not sports or any(s not in ("nba", "nhl") for s in sports):
        raise Refused("sports must be a non-empty list drawn from nba, nhl")
    return b


def week_start(now: datetime) -> datetime:
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return day - timedelta(days=day.weekday())


def _jsonl(path: Path) -> list:
    out = []
    for line in path.read_text().splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def transcript_cost(rdir: Path) -> float:
    """Sum of total_cost_usd over the result records of every agent-*.jsonl (launch and retry)."""
    total = 0.0
    for p in sorted(rdir.glob("agent-*.jsonl")):
        for line in p.read_text(errors="replace").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            c = e.get("total_cost_usd") if e.get("type") == "result" else None
            if isinstance(c, (int, float)) and not isinstance(c, bool) and math.isfinite(c) and c >= 0:
                total += c
    return total


def transcript_models(rdir: Path) -> set:
    """Models of the MAIN agent: init events and top-level assistant messages. Helper (sub-agent, parent_tool_use_id set),
    synthetic and modelUsage entries are ignored, so a helper model cannot false-STOP while a different main model still does."""
    found = set()
    for p in sorted(rdir.glob("agent-*.jsonl")):
        for line in p.read_text(errors="replace").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("type") == "system" and e.get("subtype") == "init":
                m = e.get("model")
            elif e.get("type") == "assistant" and not e.get("parent_tool_use_id"):
                m = (e.get("message") or {}).get("model")
            else:
                continue
            if isinstance(m, str) and not m.startswith("<"):
                found.add(m)
    return found


def check_models(agent: Path, runs: list, now: datetime) -> None:
    """Refuse (and write the marker) when any run in `runs` used a main model other than the one it requested."""
    for r in runs:
        used = sorted(transcript_models(r["rdir"])) if r["rdir"].is_dir() else []
        if used and used != [r["model"]]:      # no transcript model (died before init) is not a mismatch
            msg = f"{now.strftime(FMT)} run {r['run_id']} requested {r['model']!r} but the transcript shows {used}; owner review required\n"
            (agent / MARKER).write_text(msg)
            raise Refused(f"{agent / MARKER} written: {msg.strip()}")


def read_runs(h: Path, per_run: float) -> list:
    """Every run in runs.jsonl: {run_id, at, model, cost, finished, event, report, rdir}. Raises Refused if tracking is unreadable."""
    agent = h / "experiments" / "agent"
    path = agent / "runs.jsonl"
    try:
        events = _jsonl(path) if path.exists() else []
        runs = {}
        for e in events:
            if e["event"] == "started":
                runs[e["run_id"]] = {"run_id": e["run_id"], "at": _utc(e["at"], "at"), "model": e.get("model"), "finished": None}
            elif e["event"] == "finished":
                runs[e["run_id"]]["finished"] = e
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise Refused(f"spend log {path} is unreadable ({type(exc).__name__}: {exc}); cost tracking failed")
    for r in runs.values():
        rdir = agent / "runs" / r["run_id"]
        r["rdir"], cost = rdir, None
        fin = r["finished"]
        try:
            if fin is not None:
                c = fin.get("cost_usd")
                if c is not None and (isinstance(c, bool) or not isinstance(c, (int, float)) or not math.isfinite(c) or c < 0):
                    raise Refused(f"cost_usd {c!r} of run {r['run_id']} in {path} is negative, non-finite or not a number; cost tracking failed")
                rel = fin.get("report")
                if not rel:
                    raise ValueError("finished record names no report")
                r["report"] = json.loads((h / "experiments" / rel).read_text())
                cost = c
            tc = transcript_cost(rdir) if rdir.is_dir() else 0.0
        except Refused:
            raise
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            raise Refused(f"report of run {r['run_id']} is unreadable ({type(exc).__name__}: {exc}); cost tracking failed")
        r["cost"] = per_run if cost is None and tc == 0 else max(cost if cost is not None else per_run, tc)
    return sorted(runs.values(), key=lambda r: r["at"])


def trainer_running() -> bool:
    try:
        out = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{TRAINER_LABEL}"], capture_output=True, text=True, timeout=20).stdout
        if re.search(r"^\s*state = running", out, re.M):
            return True
        ps = subprocess.run(["pgrep", "-f", r"app\.cli.*adaptive-train"], capture_output=True, text=True, timeout=20)
        return ps.returncode == 0 and bool(ps.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        raise Refused("cannot tell whether the adaptive trainer is running; failing closed")


def gate(h: Path, now: datetime, trainer=None) -> dict:
    b = load_budget(h / "AGENT_BUDGET.json", now)
    agent = h / "experiments" / "agent"
    if (agent / MARKER).exists():
        raise Refused(f"{agent / MARKER} exists: a run used a model other than the requested one; the owner must review and delete it")
    hour = now.hour + now.minute / 60
    if hour >= NIGHT_START_H or hour < NIGHT_END_H:
        raise Refused("between 20:45Z and 07:00Z (a 45 min run would reach the 21:30Z live forecast windows): no paid run")
    if (trainer_running if trainer is None else trainer)():
        raise Refused("the adaptive trainer is running; no overlapping heavy job")
    per_run, cap = float(b["max_usd_per_run"]), float(b["period_usd_cap"])
    runs = [r for r in read_runs(h, per_run) if r["at"] >= b["_counting_from"]]
    week = [r for r in runs if r["at"] >= week_start(now)]
    spent = sum(r["cost"] for r in week)
    check_models(agent, runs, now)
    if len(runs) >= b["max_total_runs"]:
        raise Refused(f"{len(runs)} runs since {b['counting_from']} (max_total_runs {b['max_total_runs']}): four-run assessment required; the owner must assess and issue a new budget")
    if len(week) >= b["max_runs_per_period"]:
        raise Refused(f"{len(week)} runs already this week (max_runs_per_period {b['max_runs_per_period']})")
    if spent + per_run > cap + 1e-9:
        raise Refused(f"no room: spent ${spent:.4f} this week, cap ${cap:.4f}, a run may cost ${per_run:g}")
    argv = ["agent"]
    for s in b["sports"]:
        argv += ["--sport", s]
    return {"argv": argv + ["--max-usd", f"{per_run:g}", "--model", b["model"], "--max-trials", str(b["max_trials"]), "--max-minutes", "30"]}


def record(h: Path, now: datetime) -> list:
    """Append each finished run not yet in the ledger. A model other than the requested one writes the STOP marker."""
    agent = h / "experiments" / "agent"
    ledger = agent / LEDGER
    since = load_budget(h / "AGENT_BUDGET.json", now)["_counting_from"]
    seen = {json.loads(x)["run_id"] for x in ledger.read_text().splitlines() if x.strip()} if ledger.exists() else set()
    out = []
    for r in read_runs(h, 0.0):
        fin = r["finished"]
        if fin is None or r["run_id"] in seen or r["at"] < since:
            continue
        used = sorted(transcript_models(r["rdir"]))
        row = {"run_id": r["run_id"], "model_requested": r["model"], "model_used": used, "cost_usd": fin.get("cost_usd"),
               "cost_counted_usd": r["cost"], "trials": fin.get("trials"), "outcome": fin.get("outcome"), "recorded_at": now.strftime(FMT)}
        with ledger.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
        out.append(row)
        if used and used != [r["model"]]:      # no transcript model (died before init) is recorded as [], not a mismatch
            (agent / MARKER).write_text(f"{now.strftime(FMT)} run {r['run_id']} requested {r['model']!r} but the transcript shows {used}; owner review required\n")
    return out


def summary(h: Path, now: datetime) -> dict:
    """Read-only figures for the weekly report. Raises Refused when cost tracking is unreadable."""
    b = load_budget(h / "AGENT_BUDGET.json", now)
    runs = [r for r in read_runs(h, float(b["max_usd_per_run"])) if r["at"] >= b["_counting_from"]]
    week = [r for r in runs if r["at"] >= week_start(now)]
    decisions = {"keep": 0, "discard": 0, "other": 0}
    for r in runs:
        for t in (r.get("report") or {}).get("trials", []):
            for m in t.get("markets", {}).values():
                d = m.get("decision")
                decisions[d if d in ("keep", "discard") else "other"] += 1
    ledger = h / "experiments" / "agent" / LEDGER
    models = sorted({m for x in (_jsonl(ledger) if ledger.exists() else []) for m in x.get("model_used") or []})
    return {"week_spend": sum(r["cost"] for r in week), "period_usd_cap": b["period_usd_cap"], "week_runs": len(week),
            "max_runs_per_period": b["max_runs_per_period"], "total_runs": len(runs), "max_total_runs": b["max_total_runs"],
            "trials": sum((r["finished"] or {}).get("trials") or 0 for r in runs), "decisions": decisions,
            "outcomes": [(r["run_id"], (r["finished"] or {}).get("outcome", "not finished")) for r in week],
            "models": models, "model_requested": b["model"], "stop_marker": (h / "experiments" / "agent" / MARKER).exists()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("check", "record"))
    a = ap.parse_args(argv)
    try:
        if a.cmd == "check":
            print("\n".join(gate(home(), now_utc())["argv"]))
        else:
            for row in record(home(), now_utc()):
                print(json.dumps(row))
    except Refused as exc:
        print(f"refused: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
