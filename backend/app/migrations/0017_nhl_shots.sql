-- NHL unblocked shot attempts parsed from api-web play-by-play (docs/MODEL_SELECTION.md §12). Append-only: one import row per
-- retrieval, shots keyed by (fetch_id, idx). `available_at` follows the recorded policy: retrieval time ('observed') or the
-- game's result availability for retrospective imports ('historical_backfill'), the same assumption as the results.
CREATE TABLE nhl_pbp_imports (
  fetch_id      INTEGER PRIMARY KEY REFERENCES raw_fetches(fetch_id),
  raw_id        INTEGER NOT NULL REFERENCES raw_responses(raw_id),
  game_id       TEXT NOT NULL REFERENCES games(game_id),
  retrieved_at  TEXT NOT NULL,
  available_at  TEXT NOT NULL,
  policy        TEXT NOT NULL CHECK (policy IN ('observed', 'historical_backfill')),
  complete      INTEGER NOT NULL CHECK (complete IN (0, 1)),
  shots         INTEGER NOT NULL,
  importer_version TEXT NOT NULL
);
CREATE INDEX nhl_pbp_imports_game ON nhl_pbp_imports(game_id, available_at);
CREATE TABLE nhl_shots (
  fetch_id   INTEGER NOT NULL REFERENCES nhl_pbp_imports(fetch_id),
  idx        INTEGER NOT NULL,
  game_id    TEXT NOT NULL,
  period     INTEGER NOT NULL,
  seconds    INTEGER NOT NULL,
  side       TEXT NOT NULL CHECK (side IN ('home', 'away')),
  event      TEXT NOT NULL,
  x REAL, y REAL, zone TEXT, shot_type TEXT, situation TEXT,
  goal       INTEGER NOT NULL CHECK (goal IN (0, 1)),
  empty_net  INTEGER NOT NULL CHECK (empty_net IN (0, 1)),
  PRIMARY KEY (fetch_id, idx)
);
CREATE TRIGGER nhl_pbp_imports_no_update BEFORE UPDATE ON nhl_pbp_imports
BEGIN SELECT RAISE(ABORT, 'nhl_pbp_imports are append-only'); END;
CREATE TRIGGER nhl_pbp_imports_no_delete BEFORE DELETE ON nhl_pbp_imports
BEGIN SELECT RAISE(ABORT, 'nhl_pbp_imports are append-only'); END;
CREATE TRIGGER nhl_shots_no_update BEFORE UPDATE ON nhl_shots
BEGIN SELECT RAISE(ABORT, 'nhl_shots are append-only'); END;
CREATE TRIGGER nhl_shots_no_delete BEFORE DELETE ON nhl_shots
BEGIN SELECT RAISE(ABORT, 'nhl_shots are append-only'); END;
CREATE TRIGGER nhl_pbp_imports_nhl_only BEFORE INSERT ON nhl_pbp_imports
WHEN (SELECT sport FROM store_identity) IS NOT 'nhl'
BEGIN SELECT RAISE(ABORT, 'NHL play-by-play belongs to the NHL store'); END;
