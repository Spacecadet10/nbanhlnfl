# Task template

Copy, fill in, and paste as the prompt. Delete lines that don't apply.

```text
Objective: <one sentence: what should be true when done>
Scope: <files/modules in play>. Out of scope: <live data, definitions, deployment, unrelated refactors, ...>
Acceptance criteria:
- <observable behaviour or result>
- <test that must exist and pass>
Verification: <exact commands, e.g. PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_x.py -q;
  python3 scripts/check_project.py; browser check if UI changed>
What could make this result wrong? Check leakage, look-ahead, timestamp cutoffs, train/eval overlap,
  data copies vs live data/, and anything this task specifically risks: <...>
Finish: update HANDOFF.md (changes, checks passed/failed/not run, unresolved findings, next). Don't commit unless asked.
```
