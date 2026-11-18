#!/bin/bash
# Scheduled autoresearch GENERATION (PAID, disabled by default): refuses (exit 78) unless the owner budget file
# ~/autoresearch-sports/AGENT_BUDGET.json exists, validates, and the period's recorded spend leaves room for one more run.
# Fields: approved_by, approved_at (UTC), period (weekly|daily), max_usd_per_run > 0, max_runs_per_period >= 1, model (explicit
# id), max_trials >= 1, sports (nba/nhl), expires_at (UTC). Period = current UTC day, or ISO week from Monday 00:00 UTC.
# Spend = cost_usd of finished runs in experiments/agent/runs.jsonl; a run started but not finished counts at max_usd_per_run.
# Room = runs started < max_runs_per_period AND spend + max_usd_per_run <= max_usd_per_run * max_runs_per_period.
set -u
HOME_AR="${AUTORESEARCH_HOME:-$HOME/autoresearch-sports}"
BUDGET="$HOME_AR/AGENT_BUDGET.json"
RUNS="$HOME_AR/experiments/agent/runs.jsonl"
PREPARE="${AUTORESEARCH_PREPARE:-$HOME_AR/workspace/prepare.py}"
ARGS=$(python3 - "$BUDGET" "$RUNS" <<'PY'
import json, re, sys
from datetime import datetime, timedelta, timezone
budget_path, runs_path = sys.argv[1:3]
FMT = "%Y-%m-%dT%H:%M:%SZ"


def refuse(msg):
    print(f"refused: {msg}")
    sys.exit(1)


def utc(value, field):
    try:
        return datetime.strptime(value, FMT).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        refuse(f"{field} must be a UTC timestamp like 2026-11-18T09:00:00Z")


def number(b, field, integer=False):
    v = b.get(field)
    ok = isinstance(v, int if integer else (int, float)) and not isinstance(v, bool)
    if not ok or v < (1 if integer else 1e-12):
        refuse(f"{field} must be a {'whole number >= 1' if integer else 'number > 0'}")
    return v


try:
    b = json.load(open(budget_path))
except (OSError, ValueError) as exc:
    refuse(f"owner budget file {budget_path} is absent or unreadable ({exc}); a paid run needs it")
if not isinstance(b, dict):
    refuse(f"{budget_path} must hold a JSON object")
now = datetime.now(timezone.utc)
if not isinstance(b.get("approved_by"), str) or not b["approved_by"].strip():
    refuse("approved_by must name the approving owner")
if utc(b.get("approved_at"), "approved_at") > now:
    refuse("approved_at is in the future")
expires = utc(b.get("expires_at"), "expires_at")
if expires <= now:
    refuse(f"budget expired at {b['expires_at']}")
if b.get("period") not in ("weekly", "daily"):
    refuse("period must be weekly or daily")
per_run = number(b, "max_usd_per_run")
max_runs = number(b, "max_runs_per_period", integer=True)
max_trials = number(b, "max_trials", integer=True)
if not isinstance(b.get("model"), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:\-]*", b["model"]):
    refuse("model must be an explicit model id (letters, digits, . _ : -), never empty or a default")
sports = b.get("sports")
if not isinstance(sports, list) or not sports or any(s not in ("nba", "nhl") for s in sports):
    refuse("sports must be a non-empty list drawn from nba, nhl")
day = now.replace(hour=0, minute=0, second=0, microsecond=0)
start = day if b["period"] == "daily" else day - timedelta(days=day.weekday())
started, finished = {}, {}
try:
    lines = open(runs_path).read().splitlines() if __import__("os").path.exists(runs_path) else []
    for line in filter(None, lines):
        e = json.loads(line)
        at = datetime.strptime(e["at"], FMT).replace(tzinfo=timezone.utc)
        if at < start:
            continue
        if e["event"] == "started":
            started[e["run_id"]] = at
        elif e["event"] == "finished":
            finished[e["run_id"]] = float(e["cost_usd"])
except (OSError, ValueError, KeyError, TypeError) as exc:
    refuse(f"spend log {runs_path} is unreadable ({type(exc).__name__}: {exc}); cannot prove the period has room")
spent = sum(finished.get(r, per_run) for r in started)
if len(started) >= max_runs:
    refuse(f"{len(started)} runs already started this {b['period']} period (max_runs_per_period {max_runs})")
if spent + per_run > per_run * max_runs + 1e-9:
    refuse(f"no room: spent ${spent:.4f} this period, cap ${per_run * max_runs:.4f}, a run may cost ${per_run}")
out = ["agent"]
for s in sports:
    out += ["--sport", s]
out += ["--max-usd", f"{per_run:g}", "--model", b["model"], "--max-trials", str(max_trials), "--max-minutes", "30"]
print("\n".join(out))
PY
)
if [ $? -ne 0 ]; then
  echo "$ARGS"
  exit 78
fi
BACKEND="${LEARNING_BACKEND:-$(cd "$(dirname "$0")/.." && pwd)/backend}"
cd "$BACKEND" || exit 78
ARGV=()
while IFS= read -r line; do ARGV+=("$line"); done <<< "$ARGS"
exec "${LEARNING_PYTHON:-python3}" -m app.learning_run --name autoresearch-agent --max-minutes 35 --max-rss-gb 6 -- \
  "${AUTORESEARCH_PYTHON:-python3}" "$PREPARE" "${ARGV[@]}"
