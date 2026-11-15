# Claude entry point

Read HANDOFF.md, then AGENTS.md (shared rules and implementation guards), then only the PLAN.md/docs sections the task needs.
The current assignment comes from the user's prompt or HANDOFF.md "Next"; this file holds no task. One writer at a time; Codex reviews afterward. Use docs/TASK_TEMPLATE.md to frame non-trivial tasks.

Working rules (in addition to AGENTS.md):
- Read the code you will change and search for an existing implementation before writing new code. Keep the diff to the task.
- Bug fixes and model/data behaviour changes: first add a test that fails for the actual problem, then fix it. If impractical, say why in the handoff.
- Model/data work: check leakage, look-ahead, timestamp/cutoff boundaries (information available at forecast time, not calendar date), and train/evaluation overlap. Ask "what could make this result wrong?" before reporting it.
- `data/` is the live store. Work on `NBA_DATA_DIR` copies; never migrate, train or write against `data/` unless the task says so.
- Report every check as passed, failed, or not run, with the exact command. Never claim a command, review or result you did not observe.
- Resolve review findings or record them as unresolved in HANDOFF.md; do not drop them.
