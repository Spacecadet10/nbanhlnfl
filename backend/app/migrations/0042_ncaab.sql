-- NCAA men's Division I basketball data store (docs/NCAAB_PLAN.md, app/ncaab.py). Applied ONLY to a store file named
-- ncaab.sqlite (db.migration_paths, like 0041 for soccer): the NBA, NHL, NFL and Soccer stores never receive it, so their
-- schema, rows and release schema number stay as they are. Parent-table rebuild (db.PARENT_REBUILDS): foreign keys are
-- off around this file and checked before commit.
-- 1. store_identity may be claimed as 'ncaab' (row and three immutability triggers rebuilt as in 0034).
-- 2. market_quotes accepts sport 'ncaab' (rebuilt as in 0033); every other table keeps its own sport check.
-- 3. NCAAB context tables: team-season conference membership (with the Division I flag), per-game context, the current D-I
--    team list as listed by the source, and after-game odds read from game summaries (benchmark only, never a pregame quote).
PRAGMA legacy_alter_table = ON;
CREATE TABLE store_identity_new (
  sport      TEXT PRIMARY KEY CHECK (sport IN ('nba', 'nhl', 'nfl', 'soccer', 'ncaab')),
  claimed_at TEXT NOT NULL
);
INSERT INTO store_identity_new SELECT sport, claimed_at FROM store_identity;
DROP TABLE store_identity;
ALTER TABLE store_identity_new RENAME TO store_identity;
PRAGMA legacy_alter_table = OFF;
CREATE TRIGGER store_identity_single BEFORE INSERT ON store_identity
WHEN EXISTS (SELECT 1 FROM store_identity)
BEGIN SELECT RAISE(ABORT, 'store_identity is set once per store'); END;
CREATE TRIGGER store_identity_no_update BEFORE UPDATE ON store_identity
BEGIN SELECT RAISE(ABORT, 'store_identity is immutable'); END;
CREATE TRIGGER store_identity_no_delete BEFORE DELETE ON store_identity
BEGIN SELECT RAISE(ABORT, 'store_identity is immutable'); END;

CREATE TABLE market_quotes_new (
  quote_id     INTEGER PRIMARY KEY,
  sport        TEXT NOT NULL CHECK (sport IN ('nba', 'nhl', 'nfl', 'ncaab')),
  game_id      TEXT NOT NULL REFERENCES games(game_id),
  provider     TEXT NOT NULL,
  market       TEXT NOT NULL CHECK (market IN ('winner', 'reg3', 'spread', 'total', 'team_total_home', 'team_total_away')),
  price_label  TEXT NOT NULL CHECK (price_label IN ('open', 'current')),
  line         REAL,
  price_a      INTEGER NOT NULL,
  price_mid    INTEGER,
  price_b      INTEGER NOT NULL,
  p_a_devig    REAL NOT NULL CHECK (p_a_devig > 0 AND p_a_devig < 1),
  p_mid_devig  REAL,
  p_b_devig    REAL NOT NULL CHECK (p_b_devig > 0 AND p_b_devig < 1),
  quote_time   TEXT,
  retrieved_at TEXT NOT NULL CHECK (retrieved_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  fetch_id     INTEGER NOT NULL REFERENCES raw_fetches(fetch_id),
  raw_id       INTEGER NOT NULL REFERENCES raw_responses(raw_id),
  UNIQUE (game_id, provider, market, price_label, fetch_id),
  CHECK ((market IN ('winner', 'reg3')) = (line IS NULL)),
  CHECK ((price_mid IS NULL) = (p_mid_devig IS NULL)),
  CHECK (abs(p_a_devig + coalesce(p_mid_devig, 0) + p_b_devig - 1) < 1e-6)
);
INSERT INTO market_quotes_new SELECT * FROM market_quotes;
PRAGMA legacy_alter_table = ON;
DROP TABLE market_quotes;
ALTER TABLE market_quotes_new RENAME TO market_quotes;
PRAGMA legacy_alter_table = OFF;
CREATE INDEX market_quotes_game ON market_quotes(game_id, market, retrieved_at);
CREATE TRIGGER market_quotes_no_update BEFORE UPDATE ON market_quotes
BEGIN SELECT RAISE(ABORT, 'market_quotes are append-only'); END;
CREATE TRIGGER market_quotes_no_delete BEFORE DELETE ON market_quotes
BEGIN SELECT RAISE(ABORT, 'market_quotes are append-only'); END;
CREATE TRIGGER market_quotes_no_conflicting_insert BEFORE INSERT ON market_quotes
WHEN EXISTS (SELECT 1 FROM market_quotes
             WHERE quote_id = NEW.quote_id
                OR (game_id = NEW.game_id AND provider = NEW.provider AND market = NEW.market
                    AND price_label = NEW.price_label AND fetch_id = NEW.fetch_id))
BEGIN SELECT RAISE(ABORT, 'market_quotes are append-only: conflicting insert rejected'); END;
CREATE TRIGGER market_quotes_store_sport BEFORE INSERT ON market_quotes
WHEN NEW.sport IS NOT (SELECT sport FROM store_identity)
BEGIN SELECT RAISE(ABORT, 'market quote sport does not match this store'); END;

-- Conference membership per team and season, as the game scoreboards carry it (conferenceId; ESPN omits it for teams
-- outside Division I). is_d1 = the team carried a conference id in that season. One row per team-season, latest value kept.
CREATE TABLE ncaab_team_seasons (
  season        INTEGER NOT NULL,
  team_id       TEXT NOT NULL REFERENCES teams(team_id) CHECK (team_id GLOB 'ncaab:[0-9]*'),
  conference_id TEXT,
  is_d1         INTEGER NOT NULL CHECK (is_d1 IN (0, 1)),
  first_seen_at TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  PRIMARY KEY (season, team_id),
  CHECK (is_d1 = (conference_id IS NOT NULL))
);
CREATE TRIGGER ncaab_team_seasons_store_sport BEFORE INSERT ON ncaab_team_seasons
WHEN (SELECT sport FROM store_identity) IS NOT 'ncaab'
BEGIN SELECT RAISE(ABORT, 'ncaab tables belong only in an ncaab store'); END;
CREATE TRIGGER ncaab_team_seasons_no_delete BEFORE DELETE ON ncaab_team_seasons
BEGIN SELECT RAISE(ABORT, 'ncaab_team_seasons are never deleted'); END;

-- Per-game context from the game's scoreboard entry (current state, like games). both_d1: both teams carried a conference
-- id on this scoreboard (the contract's Division I rule; games.season_type is 'other' when it is 0). ot_periods: periods
-- played beyond the two halves (NULL until the game is final).
CREATE TABLE ncaab_game_context (
  game_id                TEXT PRIMARY KEY REFERENCES games(game_id),
  season_type_id         INTEGER,
  tournament_note        TEXT,
  conference_competition INTEGER NOT NULL CHECK (conference_competition IN (0, 1)),
  home_conference_id     TEXT,
  away_conference_id     TEXT,
  both_d1                INTEGER NOT NULL CHECK (both_d1 IN (0, 1)),
  ot_periods             INTEGER CHECK (ot_periods IS NULL OR ot_periods >= 0),
  source_status          TEXT NOT NULL,
  updated_at             TEXT NOT NULL,
  last_raw_id            INTEGER NOT NULL REFERENCES raw_responses(raw_id),
  CHECK (both_d1 = (home_conference_id IS NOT NULL AND away_conference_id IS NOT NULL))
);
CREATE TRIGGER ncaab_game_context_store_sport BEFORE INSERT ON ncaab_game_context
WHEN (SELECT sport FROM store_identity) IS NOT 'ncaab'
BEGIN SELECT RAISE(ABORT, 'ncaab tables belong only in an ncaab store'); END;
CREATE TRIGGER ncaab_game_context_no_delete BEFORE DELETE ON ncaab_game_context
BEGIN SELECT RAISE(ABORT, 'ncaab_game_context rows are never deleted'); END;

-- The source's Division I team list (teams?groups=50) at a retrieval, for the season it was retrieved in. Used only to
-- cross-check the scoreboard rule; disagreements are reported, not resolved. Append-only.
CREATE TABLE ncaab_d1_listings (
  season       INTEGER NOT NULL,
  team_id      TEXT NOT NULL REFERENCES teams(team_id),
  retrieved_at TEXT NOT NULL CHECK (retrieved_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  fetch_id     INTEGER NOT NULL REFERENCES raw_fetches(fetch_id),
  raw_id       INTEGER NOT NULL REFERENCES raw_responses(raw_id),
  PRIMARY KEY (season, team_id)
);
CREATE TRIGGER ncaab_d1_listings_no_update BEFORE UPDATE ON ncaab_d1_listings
BEGIN SELECT RAISE(ABORT, 'ncaab_d1_listings are append-only'); END;
CREATE TRIGGER ncaab_d1_listings_no_delete BEFORE DELETE ON ncaab_d1_listings
BEGIN SELECT RAISE(ABORT, 'ncaab_d1_listings are append-only'); END;

-- Odds the game summary carried when it was fetched AFTER the game (pickcenter). Benchmark only: retrieved after the
-- game, not a verified pregame quote; never shown as a same-time quote. spread_home = the home team's handicap.
CREATE TABLE ncaab_after_game_lines (
  line_id      INTEGER PRIMARY KEY,
  game_id      TEXT NOT NULL REFERENCES games(game_id),
  provider     TEXT NOT NULL,
  details      TEXT,
  spread_home  REAL,
  over_under   REAL,
  basis        TEXT NOT NULL DEFAULT 'retrieved_after_game' CHECK (basis = 'retrieved_after_game'),
  retrieved_at TEXT NOT NULL CHECK (retrieved_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  fetch_id     INTEGER NOT NULL REFERENCES raw_fetches(fetch_id),
  raw_id       INTEGER NOT NULL REFERENCES raw_responses(raw_id),
  UNIQUE (game_id, provider, fetch_id),
  CHECK (spread_home IS NOT NULL OR over_under IS NOT NULL)
);
CREATE TRIGGER ncaab_after_game_lines_no_update BEFORE UPDATE ON ncaab_after_game_lines
BEGIN SELECT RAISE(ABORT, 'ncaab_after_game_lines are append-only'); END;
CREATE TRIGGER ncaab_after_game_lines_no_delete BEFORE DELETE ON ncaab_after_game_lines
BEGIN SELECT RAISE(ABORT, 'ncaab_after_game_lines are append-only'); END;
