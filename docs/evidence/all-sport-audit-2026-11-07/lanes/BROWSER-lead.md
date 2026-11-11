# Lead browser checks — 2026-11-07 ~15:40–15:55 ET

Setup: Chrome (claude-in-chrome), tab on http://127.0.0.1:5271 = vite from $SP/snap (frozen 20:28Z) proxying API :8871
(uvicorn from $SP/snap/backend, NBA_API_MODE=real, NBA_DATA_DIR=$SP/work/devapi = migrated (→35) backups of live
nba/nhl/nfl taken 15:29 ET + ce's scratch soccer store). NBA_LIVE_DATA_DIR=$SP/stores. No publisher running (differs from
dev.sh; freshness chips therefore not representative of dev.sh).
Method: same-origin <iframe> of exact width (390×844 or 1440×844) → real CSS viewport of that width; wait until main has no
"Loading" (≤20 s); measure documentElement.scrollWidth vs innerWidth; for each <table> report scrollWidth / nearest
non-visible-overflow ancestor clientWidth. Harness functions window.measure2 / errTest2 in tab.

## Layout (actual measurements)
| Flow (same per sport) | NBA 390 | NHL 390 | NFL 390 | NBA/NHL/NFL 1440 |
|---|---|---|---|---|
| Today `/` | sw 390 | 390 | 390 | 1440 |
| Game `/games/<final id>` (espn:401909294, nhl:2026020263, nfl:401873037) | 390 | 390 | 390 | 1440 |
| History | 390 | 390 | 390 | 1440 |
| Performance | 390 (10 tables: wide ones 510/358/369/388 inside 320 px overflow:auto wrappers; 2×308 unwrapped but fit) | 390 | 390 | 1440 |
| Daily learning | 390 | 390 | 390 | n/a |
Earlier-audit finding "393/390 overflow on NBA/NHL Performance (Canonical.tsx Calibration table)": NOT reproduced on snap → fixed.

Soccer (390 and 1440): all seven competitions' list pages, UCL match 733605 (Liverpool v PSG "0–1 after extra time, 1–4 on
penalties", "90-minute result Not shown: the match went to extra time"), UEL match 733642 opened with ?competition=eng.1
(header shows UEFA Europa League — match's own competition wins), ?competition=bogus (→ Premier League, documented default),
soccer History/Performance ("Not available: soccer predictions are not ready … no soccer forecast has been recorded"):
sw = viewport in all 12×390 + 9×1440 loads; no Loading left.

Intermediate widths (prompted by NHL-C §5: `.table.history` collapses to cards only at ≤580 px):
| width | NBA /history | NHL /history | NHL /performance | NFL /history | NHL game |
|---|---|---|---|---|---|
| 600 | **694** | **710** | 600 | 600 | 600 |
| 700 | 700 | **710** | 700 | 700 | 700 |
| 800 | 800 | 800 | 800 | 800 | 800 |
Offender: `TABLE.table.history` directly under a SECTION (w=676 NBA / 692 NHL at 600 px) — Canonical.tsx:135, no
`.table-wrap`. MEDIUM-LOW layout defect (page scrolls sideways on small tablets / landscape phones). NEW.

## Identity / no cross-sport fallback
- /games/nhl:2026020263 (no sport) → "404: Game nhl:2026020263 not found"; /games/espn:401909294?sport=nhl → 404;
  /games/nfl:401873037 (no sport) → 404; /games/nhl:2026020263?sport=nfl → "404: No NFL game 'nhl:2026020263' in the store".
  No other sport's game shown. PASS.
- ?sport=bogus and ?sport=NHL (uppercase) → NBA screens (documented default; case-sensitive). LOW: uppercase NHL silently
  shows NBA data — selector shows NBA, so not mislabelled, but a hand-typed link is misread.
- Daily Learning ignores ?sport and always shows separately labelled NBA and NHL sections (also under ?sport=nfl). LOW.

## Error → recovery (fetch made to reject inside the iframe, client-side navigation, then fetch restored + every Retry clicked)
| Flow | error shown ("Cannot reach the API" count) | after retry (count, full text) |
|---|---|---|
| NBA / → /performance | 4 | 0 (6931 chars rendered) |
| NHL / → /history | 2 | 0 |
| NFL / → /history | 1 | 0 |
| Soccer eng.1 → uefa.champions | 1 | 0 |
No page showed another sport's data or demo data while failing. PASS.

Not run: real backend stop/restart (shared server used by reviewers), loading-state screenshots, dev.sh-launched instance.
