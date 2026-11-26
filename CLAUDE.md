# Claude entry point (a map; AGENTS.md holds the shared rules)

Read HANDOFF.md "Current state" only (its history is for tasks that need it), then AGENTS.md, then only the PLAN.md/docs sections the task needs.
The assignment comes from the user's prompt or HANDOFF.md "Next". One writer at a time; Codex reviews afterward. Frame non-trivial tasks with docs/TASK_TEMPLATE.md.
Skills (.claude/skills): `release` = moving a live/delivery/backup checkout or plist; `learning-evidence` = forecasts, candidates, forward tests, colours, grading, promotion.
- Read the code you will change and search for an existing implementation first. Keep the diff to the task.
- Bug fixes and model/data behaviour changes: add a failing test first, then fix. If impractical, say why in the handoff.
- Model/data work: check leakage, look-ahead, cutoff boundaries (information available at forecast time, not calendar date), train/evaluation overlap.
- `data/` is the live store. Work on `NBA_DATA_DIR` copies; never migrate, train or write against it unless the task says so.
- The repo is PUBLIC: no secrets, tokens, chat ids, .env files or data in commits.
- Report every check as passed, failed, or not run, with the exact command. Never claim a command, review or result you did not observe.
- Resolve review findings or record them as unresolved in HANDOFF.md; do not drop them.
