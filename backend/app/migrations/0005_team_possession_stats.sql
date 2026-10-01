-- Possession inputs (turnovers, offensive/defensive rebounds, FGA, FTA) from the sportsdataverse ESPN team box release.
-- Kept apart from team_game_stats so scoreboard refreshes never overwrite this source's values or provenance.
CREATE TABLE team_possession_stats (
  game_id      TEXT NOT NULL REFERENCES games(game_id),
  team_id      TEXT NOT NULL REFERENCES teams(team_id),
  is_home      INTEGER NOT NULL CHECK (is_home IN (0, 1)),
  points       INTEGER,
  fga          INTEGER,
  fta          INTEGER,
  oreb         INTEGER,
  dreb         INTEGER,
  tov          INTEGER,   -- total turnovers (player + team)
  raw_id       INTEGER NOT NULL REFERENCES raw_responses(raw_id),
  available_at TEXT NOT NULL CHECK (available_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  availability_basis TEXT NOT NULL CHECK (availability_basis IN ('assumed_with_result', 'observed_retrieval')),
  PRIMARY KEY (game_id, team_id)
);
