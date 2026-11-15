# Renamed evidence files (2026-11-08, integration writer)

The structure guard (`scripts/check_project.py`, unchanged) rejects file names ending in `-final`. Two files were renamed,
byte-for-byte unchanged; nothing was deleted. Earlier reports and HANDOFF entries still use the old names.

| Old name (cited in BATCH1_FINAL_REPORT.md §11, RESTORE_FAILURE_ADDENDUM.md, SMOKE_TEST.md) | New name | SHA-1 of the file |
|---|---|---|
| `batch1-final.patch` (round-4 tree `3c2ec4a…`) | `batch1-round4-3c2ec4a.patch` | see below |
| `batch1-final.diffstat.txt` | `batch1-round4-3c2ec4a.diffstat.txt` | see below |

The final reviewed batch-1 tree remains `batch1-final-r5.patch` (`adacc372…`), whose name passes the guard.
    3c2ec4a6023b2602e160bdae0d701d76aa19529b  batch1-round4-3c2ec4a.patch
    22cfe6c184bb6ca6e4187d7cd23df4e5ccd11c57  batch1-round4-3c2ec4a.diffstat.txt
