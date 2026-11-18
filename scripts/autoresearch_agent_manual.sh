#!/bin/bash
# MANUAL paid autoresearch generation under the owner's weekly budget. Same gate, same counter and same ledger as the scheduled
# wrapper (it IS the scheduled wrapper): a manual run counts toward $5/week, 2 runs/week and the 4-run stop.
# Running `prepare.py agent` directly bypasses the budget and must not be done under this authorization.
exec /bin/bash "$(dirname "$0")/autoresearch_agent_scheduled.sh" "$@"
