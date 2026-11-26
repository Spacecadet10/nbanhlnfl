# Shared agent instructions (Claude and Codex)

Read HANDOFF.md "Current state" first (history only when the task needs it), then the relevant PLAN.md/docs sections.
The user wants trustworthy predictions, a working frontend and low token use.

- Work on the assigned task only. One implementation writer at a time. Keep context and updates concise; inspect relevant files/diffs, not the whole repo.
- Preserve timestamp integrity, append-only forecasts, and separation of demo/backtest/live results.
- Never invent model performance, source coverage, injuries, odds, or successful checks.
- Keep API keys server-side and out of committed files. The repo is PUBLIC: scan diffs for secrets and data before any push.
- Run checks appropriate to the change; do not repeat unchanged checks without a reason.
- No purchases or unrequested deployment. Local implementation within the task may proceed.
- End every task by adding a short entry to HANDOFF.md: changes, actual verification, blockers, next task.
- Procedures live in skills: `.claude/skills/release` (moving checkouts/plists) and `.claude/skills/learning-evidence` (forecast, forward-test, colour, grading and promotion rules, hashed do-not-edit files).
- Which checkout runs which jobs: docs/RELEASE_CHECKOUTS.md.

## Implementation guards (both agents)
- Before editing: inspect relevant files and git status/diff. Reuse the existing implementation. Never scaffold over a populated app.
- Layout: frontend/ is the ONLY React app (npm, frontend/package-lock.json); backend/ is the ONLY Python project. No second app, alternate backend,
  copied component tree, or *_old / *_copy / *_v2 files.
- Search for an existing component, API client, route, schema or helper before creating one; extend it. API owns demo data; do not duplicate fixtures in the UI.
- Small, scoped edits. No broad rewrites, unrelated dependency upgrades, destructive Git commands, database resets, or deletion of another agent's work.
  Report conflicting edits before touching them.
- Do not edit these guards or weaken checks to make a failure disappear. Explain any necessary architecture change in the handoff first.
- One API client, one backend entry point, one configuration path. UI screens consume the backend contract; no hardcoded fallback that disguises an API failure as success.
- Storage: enforce uniqueness in the database and test repeated ingestion. Stable idempotency key for prediction retries, while allowing genuinely new
  forecast versions. UI deduplication is not a substitute.
- Run `python3 scripts/check_project.py` during implementation; before claiming the app ready, run it with the backend Python and `--ready`.
  Missing checks or dependencies are failures, not passes.
- Never delete/skip tests or replace real assertions with unconditional passes. Fix the cause. After two failed attempts at the same issue, record the
  exact failure for review and continue unrelated work.
- Frontend changes (and any claim that the app works): verify the running API and frontend in a browser at desktop and phone widths; record commands/results and gaps in docs/SMOKE_TEST.md.
  If browser verification is unavailable, mark it unverified.
- Done means working navigation, API-backed data, visible Demo labels, loading/error/empty states, passing checks and reproducible startup.
  A screenshot or successful build alone is insufficient.

These instructions and the structure checker reduce mistakes; they are not a security boundary. Codex review remains required before accepting a milestone.
