#!/bin/bash
# Scheduled autoresearch GENERATION (PAID, disabled by default). All gating lives in backend/app/autoresearch_budget.py, shared with
# scripts/autoresearch_agent_manual.sh; see that module for the budget file fields, counting and refusal rules. Exit 78 = refused.
# Runs through app.learning_run (heavy lock, game-window deferral), then records the run to budget-ledger.jsonl.
# The gate and the paid run use one home, ~/autoresearch-sports (the home prepare.py hardcodes); an override that differs refuses.
# gate_sha256 in the budget file pins this script, autoresearch_agent_manual.sh and autoresearch_budget.py (see the module docstring).
set -u
HOME_AR="$HOME/autoresearch-sports"
if [ -n "${AUTORESEARCH_HOME:-}" ] && [ "$AUTORESEARCH_HOME" != "$HOME_AR" ]; then
  echo "refused: AUTORESEARCH_HOME $AUTORESEARCH_HOME differs from $HOME_AR, the home prepare.py uses"
  exit 78
fi
PREPARE="$HOME_AR/workspace/prepare.py"
if [ -n "${AUTORESEARCH_PREPARE:-}" ] && [ "$AUTORESEARCH_PREPARE" != "$PREPARE" ]; then
  echo "refused: AUTORESEARCH_PREPARE $AUTORESEARCH_PREPARE differs from $PREPARE"
  exit 78
fi
BACKEND="${LEARNING_BACKEND:-$(cd "$(dirname "$0")/.." && pwd)/backend}"
HERE="$(cd "$(dirname "$0")" && pwd)"
WANT=$(sed -n 's/.*"gate_sha256"[[:space:]]*:[[:space:]]*"\([0-9a-f]\{64\}\)".*/\1/p' "$HOME_AR/AGENT_BUDGET.json" 2>/dev/null | head -1)
GOT=$(cat "$BACKEND/app/autoresearch_budget.py" "$HERE/autoresearch_agent_scheduled.sh" "$HERE/autoresearch_agent_manual.sh" 2>/dev/null | shasum -a 256 | cut -d' ' -f1)
if [ -z "$WANT" ] || [ "$WANT" != "$GOT" ]; then
  echo "refused: gate_sha256 in AGENT_BUDGET.json (${WANT:-absent}) does not match the gate files ($GOT); the owner must re-approve this gate version"
  exit 78
fi
cd "$BACKEND" || exit 78
PY="${LEARNING_PYTHON:-python3}"
ARGS=$("$PY" -m app.autoresearch_budget check)
if [ $? -ne 0 ]; then
  echo "$ARGS"
  exit 78
fi
ARGV=()
while IFS= read -r line; do ARGV+=("$line"); done <<< "$ARGS"
"$PY" -m app.learning_run --name autoresearch-agent --max-minutes 45 --max-rss-gb 6 -- \
  "${AUTORESEARCH_PYTHON:-python3}" "$PREPARE" "${ARGV[@]}"
RC=$?
"$PY" -m app.autoresearch_budget record || echo "WARNING: budget ledger record failed" >&2
exit $RC
