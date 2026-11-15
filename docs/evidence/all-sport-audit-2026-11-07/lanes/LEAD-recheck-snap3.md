# Lead re-check of ce's post-audit fixes — snap3 (frozen 20:55:17Z, git diff sha 330feed… = ce GITDIFF_BEFORE of gate 2)

Diff snap2→snap3: backend ingest.py, main.py, migrations/0035, schemas.py, soccer.py, tests (test_replay_same_store, test_soccer);
frontend api/types.ts, pages/SoccerPages.tsx ($SP/lead/snap2_to_snap3_{backend,frontend}.diff).

| Finding (lane) | Re-check on snap3 | Result |
|---|---|---|
| Team change on a final, latest revision pre-0035 (NULL teams) not recorded (NBA-A F1, shared ingest) | `PYTHONPATH=snap3/backend:snap3/backend/tests python work/NBA-A/scripts/probe.py work/lead-recheck/nba_probe` → S7 revs `[(1,NULL,NULL,124,115,T0),(2,espn:20,espn:11,124,115,T1)]` (snap2 had 1 rev); S1 A→B→A 3/3/3 revisions, S2 same-second 2/2, S3 box equal | FIXED |
| Migrated store vs rebuild disagree on tie_facts_available_at (SOCCER-A F1) | SOCCER-A's HEAD-built schema-34 store (58 matches, 20 with tie facts incl. 733144) migrated with snap3 + replay, vs fresh rebuild from same log (work/lead-recheck/{m,r}): 0/58 differ in tie_facts_available_at, result_available_at, decided_in | FIXED (on this store) |
| Replay reclassification appends unlabelled same-observed_at revision (SOCCER-C M1, SOCCER-A F2) | same migrated store: basis observed 58, reclassified 2 (169930, 416637, same observed_at as rev 1); ce says UI counts only observed versions (SoccerPages diff; not browser-checked by lead) | FIXED as labelled design; UI count not browser-verified |
| 503/empty-state told users to run soccer ingest/migrate without NBA_DATA_DIR (SOCCER-C M2) | main.py and SoccerPages.tsx now say `NBA_DATA_DIR=<scratch or development data directory>` and "not clearly permitted"; test asserts both strings | FIXED (code + test) |
Not re-checked on snap3: everything else (lanes audited snap / snap2). The NBA/NHL/NFL lanes audited snap (20:28Z); the shared
ingest.py change in snap3 was re-exercised only by the NBA-A probe above and ce's focused runs (446 passed, reported by ce, not
observed by lead) and by the readiness gate (ready-final-2.log, result pending).
