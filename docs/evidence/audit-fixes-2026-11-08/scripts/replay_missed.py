"""Replay the real Nov 8 trigger inputs (saved reviewer snapshots) through the fixed helper, on copies only.
Usage: NBA_DATA_DIR=<replay dir> NBA_TELEGRAM_LEDGER=<ledger copy> python replay_missed.py"""
import json, glob, os, sys
from app import tickets as t, config
assert "scratchpad" in str(config.data_dir()) and "scratchpad" in os.environ["NBA_TELEGRAM_LEDGER"], "copies only"
t.prior_ticket_games = lambda day: {}          # lock lookup needs the chat id (credentials); not part of this replay
for path in sorted(glob.glob("/tmp/ticket-snapshot-20261108-*.json")):
    snap = json.load(open(path))
    if not ("2026-11-08T12:00:00Z" <= snap["as_of"] <= "2026-11-08T22:00:00Z"):
        continue
    state = t.review_state(snap)
    n = t.record_observations(state)
    missed = [f"{m['matchup']}: blocker_observed={m['blocker_observed']} explained={m['explained']} | {m['explanation']}" for m in state["missed_before_start"]]
    print(snap["as_of"][11:19], "| recorded", n, [(w["sport"], (w["game_id"] or "-")[-9:], w["issue"]) for w in state.get("not_reviewed_now", [])], "| missed", missed)
