#!/bin/bash
# Scheduled autoresearch GENERATION (PAID, disabled by default). All gating lives in backend/app/autoresearch_budget.py, shared with
# scripts/autoresearch_agent_manual.sh; see that module for the budget file fields, counting and refusal rules. Exit 78 = refused.
# Runs through app.learning_run (heavy lock, game-window deferral), then records the run to budget-ledger.jsonl.
set -u
HOME_AR="${AUTORESEARCH_HOME:-$HOME/autoresearch-sports}"
export AUTORESEARCH_HOME="$HOME_AR"
PREPARE="${AUTORESEARCH_PREPARE:-$HOME_AR/workspace/prepare.py}"
BACKEND="${LEARNING_BACKEND:-$(cd "$(dirname "$0")/.." && pwd)/backend}"
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
