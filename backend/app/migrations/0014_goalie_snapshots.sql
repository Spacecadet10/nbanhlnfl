-- Prospective NHL starting-goalie snapshots from ESPN scoreboards (docs/DATA_SOURCES.md, NHL and market sources).
-- One append-only row per team per retrieval of a game that had not started. ESPN overwrites `probables` after the game and
-- publishes no announcement time, so the only defensible availability time is our retrieval (completed download) time.
-- The source's status is kept as published ('expected', 'confirmed', or whatever else it says); a team with no listed goalie
-- is recorded as 'none_listed', never filled in.
CREATE TABLE goalie_snapshots (
  snapshot_id   INTEGER PRIMARY KEY,
  game_id       TEXT NOT NULL REFERENCES games(game_id),
  side          TEXT NOT NULL CHECK (side IN ('home', 'away')),
  fetch_id      INTEGER NOT NULL REFERENCES raw_fetches(fetch_id),
  raw_id        INTEGER NOT NULL REFERENCES raw_responses(raw_id),
  retrieved_at  TEXT NOT NULL CHECK (retrieved_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  source_status TEXT NOT NULL,              -- e.g. 'expected', 'confirmed'; 'none_listed' when the team has no entry
  source_status_label TEXT,                 -- the source's display text, e.g. 'Expected'
  espn_player_id TEXT,
  player_name   TEXT,
  UNIQUE (game_id, side, fetch_id),
  CHECK ((source_status = 'none_listed') = (espn_player_id IS NULL))
);
CREATE INDEX goalie_snapshots_game ON goalie_snapshots(game_id, side, retrieved_at);
CREATE TRIGGER goalie_snapshots_no_update BEFORE UPDATE ON goalie_snapshots
BEGIN SELECT RAISE(ABORT, 'goalie_snapshots are append-only'); END;
CREATE TRIGGER goalie_snapshots_no_delete BEFORE DELETE ON goalie_snapshots
BEGIN SELECT RAISE(ABORT, 'goalie_snapshots are append-only'); END;
CREATE TRIGGER goalie_snapshots_no_conflicting_insert BEFORE INSERT ON goalie_snapshots
WHEN EXISTS (SELECT 1 FROM goalie_snapshots WHERE snapshot_id = NEW.snapshot_id
             OR (game_id = NEW.game_id AND side = NEW.side AND fetch_id = NEW.fetch_id))
BEGIN SELECT RAISE(ABORT, 'goalie_snapshots are append-only: conflicting insert rejected'); END;
CREATE TRIGGER goalie_snapshots_nhl_only BEFORE INSERT ON goalie_snapshots
WHEN (SELECT sport FROM store_identity) IS NOT 'nhl'
BEGIN SELECT RAISE(ABORT, 'goalie snapshots belong to the NHL store'); END;
