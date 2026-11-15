-- Replaces the latest-only team_possession_stats (0005) after Codex step 2 review: late data was backdated, and an older
-- file could overwrite newer values. team_possession_stats held only rows derived from cached raw files, so it is
-- dropped and rebuilt from the raw cache (ingest.rebuild_team_box).
-- One append-only observation per (game, team, raw file). Values are never updated; corrections are new observations.
-- observed_at = the raw file's retrieval time; available_at = when a forecast may use it (see ingest.import_team_box_raw).
DROP TABLE team_possession_stats;
CREATE TABLE team_possession_observations (
  game_id      TEXT NOT NULL REFERENCES games(game_id),
  team_id      TEXT NOT NULL REFERENCES teams(team_id),
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
  PRIMARY KEY (game_id, team_id, raw_id),
  CHECK (available_at <= observed_at)
);
CREATE INDEX possession_obs_asof ON team_possession_observations(game_id, team_id, available_at, observed_at);
CREATE TRIGGER possession_obs_no_update BEFORE UPDATE ON team_possession_observations
BEGIN SELECT RAISE(ABORT, 'team_possession_observations are append-only'); END;
CREATE TRIGGER possession_obs_no_delete BEFORE DELETE ON team_possession_observations
BEGIN SELECT RAISE(ABORT, 'team_possession_observations are append-only'); END;
