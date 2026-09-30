# Milestone 2 review — Codex, 2026-09-30

Decision: fix two reproduced integrity issues before milestone 3. Production data was not modified during review.

## P1: Older responses overwrite newer team stats
Location: backend/app/ingest.py, import_raw and _upsert_stats (lines 148–158).
_upsert_game rejects an older response, but import_raw still calls _upsert_stats, which has no source-order check.
Reproduced in a temporary database: import a final home score of 130 at 2024-01-17T02:00:00Z,
then an older final score of 124 at 2024-01-16T02:00:00Z. games.home_score remains 130;
team_game_stats.points becomes 124. Newer partial stats can also replace existing fields with NULL.
Required: explicitly define ordering and missing-field policy for stats and maintain source provenance.
Test out-of-order imports, stats-only changes, partial responses, and replay consistency.
Do not gate stats updates solely on whether game fields changed: stats can change independently.

## P1: SQLite REPLACE bypasses append-only protections
Location: backend/app/db.py connection settings and migrations/0001_initial.sql append-only triggers.
PRAGMA recursive_triggers is 0 on the project connection. INSERT OR REPLACE deletes a conflicting row
without invoking its delete trigger under that setting. Reproduced with a stored prediction:
INSERT OR REPLACE of the same row changed home_win_prob from 0.6 to 0.99 successfully.
Required: protect conflicting inserts at the schema level (including primary-key and unique-key conflicts),
using a new forward migration; preserve legitimate application-level idempotent retries.
Cover predictions, raw_responses, and odds_quotes. Test REPLACE as well as UPDATE/DELETE, and verify existing
stored rows remain intact. Do not rewrite the applied migration or reset the existing database.

## Verification
- backend/.venv/bin/python scripts/check_project.py --ready: passes, 29 backend tests; one TestClient warning.
- Existing SQLite inspected read-only: 9,635 games; 9,412 final; latest final 2026-06-14T00:30:00Z.
- No finals after 2026-09-30; 0 current score/stat disagreements. Findings are reproduced vulnerabilities,
  not evidence that current data is already corrupted.
- Both reproductions used synthetic payloads and isolated temporary databases.
- Source licensing and external season totals were not independently verified in this review.

## After fixes
Proceed with home-rate and Elo baselines before advanced team features. Missing turnovers/offensive rebounds
need not block Elo. For historical evaluation, document the assumed availability and use a conservative
prior-day cutoff; do not claim exact point-in-time integrity from a tipoff-plus-four-hours assumption.
Keep real forecasts distinct from the existing demo API/UI until explicitly connected.


## Re-review of fixes — 2026-09-30
The two reproduced P1 issues are resolved for the reviewed scenarios. Stats now enforce independent source ordering
and preserve missing fields. Migration 0002 blocks conflicting inserts on primary/unique keys for all three append-only
tables; db.connect also enables recursive triggers. Regression coverage includes external connections with recursive
triggers off and migration preservation. Current readiness command passes: 52 tests, frontend typecheck/build.
This verifies current code/tests, not an independently witnessed pre-migration backup or production checksum history.

Workspace also contains milestone 3 part 1, beyond the pasted update. Before final-test evaluation:
backtest.evaluate currently calls load_rows without a period bound; tune_elo/run process all seasons and only then
filter reported scores. Validation should load only warmup/validation rows and fingerprint that subset. Reserved
outcomes must not be read, hashed, predicted or fed into model state by validation. No future-to-past leakage is
established by this finding; it is a failure of the promised holdout isolation. Add a regression proving validation
excludes reserved rows at the loading boundary. Leave the final test unopened until logistic/calibration candidates
and selection rules are finalized. Full milestone 3 review remains pending.
