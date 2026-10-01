-- Codex step 2 re-review:
-- P1: observations were keyed by raw file (content), so identical bytes re-downloaded after a correction (12 → 13 → 12)
--     recorded nothing. They are now keyed by retrieval event (raw_fetches.fetch_id); observed_at is that fetch's time.
-- P2: UPDATE/DELETE triggers do not stop INSERT OR REPLACE when recursive_triggers is off (SQLite's default), so a
--     BEFORE INSERT conflict trigger is added, as in 0002. raw_fetches, which now carries observation timing, gets the same
--     append-only protection.
-- The table only holds rows derived from cached files and their fetch log, so it is rebuilt (ingest.rebuild_team_box).
DROP TABLE team_possession_observations;
CREATE TABLE team_possession_observations (
  game_id      TEXT NOT NULL REFERENCES games(game_id),
  team_id      TEXT NOT NULL REFERENCES teams(team_id),
  fetch_id     INTEGER NOT NULL REFERENCES raw_fetches(fetch_id),
  raw_id       INTEGER NOT NULL REFERENCES raw_responses(raw_id),
  is_home      INTEGER NOT NULL CHECK (is_home IN (0, 1)),
  points       INTEGER,
  fga          INTEGER,
  fta          INTEGER,
  oreb         INTEGER,
  dreb         INTEGER,
  tov          INTEGER,   -- total turnovers (player + team)
  observed_at  TEXT NOT NULL CHECK (observed_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  available_at TEXT NOT NULL CHECK (available_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  availability_basis TEXT NOT NULL CHECK (availability_basis IN ('historical_backfill_assumed', 'observed_retrieval')),
  PRIMARY KEY (game_id, team_id, fetch_id),
  CHECK (available_at <= observed_at)
);
CREATE INDEX possession_obs_asof ON team_possession_observations(game_id, team_id, available_at, observed_at);
CREATE TRIGGER possession_obs_no_update BEFORE UPDATE ON team_possession_observations
BEGIN SELECT RAISE(ABORT, 'team_possession_observations are append-only'); END;
CREATE TRIGGER possession_obs_no_delete BEFORE DELETE ON team_possession_observations
BEGIN SELECT RAISE(ABORT, 'team_possession_observations are append-only'); END;
CREATE TRIGGER possession_obs_no_conflicting_insert BEFORE INSERT ON team_possession_observations
WHEN EXISTS (SELECT 1 FROM team_possession_observations
             WHERE game_id = NEW.game_id AND team_id = NEW.team_id AND fetch_id = NEW.fetch_id)
BEGIN SELECT RAISE(ABORT, 'team_possession_observations are append-only: conflicting insert rejected'); END;

CREATE TRIGGER raw_fetches_no_update BEFORE UPDATE ON raw_fetches
BEGIN SELECT RAISE(ABORT, 'raw_fetches are append-only'); END;
CREATE TRIGGER raw_fetches_no_delete BEFORE DELETE ON raw_fetches
BEGIN SELECT RAISE(ABORT, 'raw_fetches are append-only'); END;
CREATE TRIGGER raw_fetches_no_conflicting_insert BEFORE INSERT ON raw_fetches
WHEN EXISTS (SELECT 1 FROM raw_fetches WHERE fetch_id = NEW.fetch_id)
BEGIN SELECT RAISE(ABORT, 'raw_fetches are append-only: conflicting insert rejected'); END;
