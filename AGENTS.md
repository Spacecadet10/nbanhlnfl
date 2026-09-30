# Shared agent instructions

Read HANDOFF.md first, then relevant sections of PLAN.md. The user wants a frontend app and trustworthy NBA predictions with low token use.

- Work on the assigned milestone only. One implementation writer at a time.
- Keep context and updates concise. Inspect relevant files/diffs, not the full repository on every turn.
- Preserve timestamp integrity, append-only forecasts, and separation of demo/backtest/live results.
- Never invent model performance, source coverage, injuries, odds, or successful checks.
- Keep API keys server-side and out of committed files.
- Run checks appropriate to the change. Do not repeat unchanged checks without a reason.
- No purchases or unrequested deployment. Ordinary local implementation within the assigned task may proceed.
- End every task by updating HANDOFF.md with changes, actual verification, blockers, and the next task. Keep it short.

## Implementation guards — required for both agents
- Before editing: inspect existing relevant files and git status/diff if Git is initialized. Reuse the existing implementation. Never scaffold over a populated app.
- Canonical layout: frontend/ is the ONLY React app; backend/ is the ONLY Python project. Use npm and frontend/package-lock.json. No second app, alternate backend, copied component tree, or *_old / *_copy / *_v2 files.
- Search for an existing component, API client, route, schema, or helper before creating one. Extend it when it already owns the responsibility. Do not duplicate fixtures in both UI and API: API owns demo data.
- Make small, scoped edits. No broad rewrites, dependency upgrades unrelated to the task, destructive Git commands, database resets, or deletion of another agent's work. Resolve routine implementation details autonomously; report actual conflicting edits before touching them.
- Do not edit these guards or weaken checks just to make a failure disappear. Explain any necessary architecture change in the handoff before implementing it in a later assigned task.
- Keep one API client, one backend entry point, and one configuration path. All four UI screens must consume the backend contract. No hardcoded fallback that disguises an API failure as success.
- When storage is introduced, enforce uniqueness in the database and test repeated ingestion. Use a stable idempotency key for prediction retries while permitting genuinely new forecast versions. UI deduplication is not a substitute for correct storage.
- Run python3 scripts/check_project.py during implementation. Before claiming today’s app ready, run the backend environment's Python with scripts/check_project.py --ready. Missing checks or dependencies are failures, not passes.
- Never delete/skip tests or replace real assertions with unconditional passes to get green checks. Fix the cause. After two failed attempts at the same issue, record the exact failure for review; continue unrelated work instead of rebuilding the project.
- Verify the running API and frontend in a browser at desktop and phone widths. Record exact commands/results, URLs, tested flows, and remaining gaps in docs/SMOKE_TEST.md. If browser verification is unavailable, explicitly mark it unverified.
- Done means working navigation, API-backed data, visible Demo labels, working loading/error/empty states, passing checks, and reproducible startup instructions. A screenshot or successful build alone is insufficient.

These instructions and the structure checker reduce mistakes; they are not a security boundary and cannot detect all duplicate logic. Codex review remains required before accepting the milestone.
