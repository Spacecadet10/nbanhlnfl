-- Timestamps are UTC ISO-8601 text 'YYYY-MM-DDTHH:MM:SSZ'; the ts_ok() pattern below is enforced by CHECKs.
-- Timestamp kinds are kept separate:
--   event time        games.tipoff_at
--   availability time games.result_available_at (+ basis), odds quote time when the source provides it
--   retrieval time    raw_responses.first_retrieved_at / raw_fetches.retrieved_at, *_first_seen_at

CREATE TABLE raw_responses (
  raw_id             INTEGER PRIMARY KEY,
  source             TEXT NOT NULL,
  endpoint           TEXT NOT NULL,
  request_key        TEXT NOT NULL,           -- canonical query parameters
  content_hash       TEXT NOT NULL,           -- sha256 of the exact response bytes
  byte_size          INTEGER NOT NULL,
  path               TEXT NOT NULL,           -- relative to the raw cache directory
  importer_version   TEXT NOT NULL,
  first_retrieved_at TEXT NOT NULL CHECK (first_retrieved_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  UNIQUE (source, request_key, content_hash)
);

-- Every fetch is logged, including fetches that returned already-cached bytes.
CREATE TABLE raw_fetches (
  fetch_id     INTEGER PRIMARY KEY,
  raw_id       INTEGER NOT NULL REFERENCES raw_responses(raw_id),
  retrieved_at TEXT NOT NULL CHECK (retrieved_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z')
);

CREATE TRIGGER raw_responses_no_update BEFORE UPDATE ON raw_responses
BEGIN SELECT RAISE(ABORT, 'raw_responses are immutable'); END;
CREATE TRIGGER raw_responses_no_delete BEFORE DELETE ON raw_responses
BEGIN SELECT RAISE(ABORT, 'raw_responses are immutable'); END;

CREATE TABLE teams (
  team_id    TEXT PRIMARY KEY,                -- 'espn:<id>'; stable across seasons and name changes
  abbr       TEXT NOT NULL,
  name       TEXT NOT NULL,
  first_seen_at TEXT NOT NULL
);

CREATE TABLE games (
  game_id        TEXT PRIMARY KEY,            -- 'espn:<event id>'
  season         INTEGER NOT NULL,            -- ending year: 2024 = 2023-24
  season_type    TEXT NOT NULL CHECK (season_type IN ('preseason', 'regular', 'postseason', 'play_in', 'other')),
  tipoff_at      TEXT NOT NULL CHECK (tipoff_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  home_team_id   TEXT NOT NULL REFERENCES teams(team_id),
  away_team_id   TEXT NOT NULL REFERENCES teams(team_id),
  neutral_site   INTEGER NOT NULL CHECK (neutral_site IN (0, 1)),
  status         TEXT NOT NULL CHECK (status IN ('scheduled', 'in_progress', 'final', 'postponed', 'canceled', 'other')),
  home_score     INTEGER,
  away_score     INTEGER,
  -- Retrieval: when our pipeline first saw the game and first saw its final result.
  first_seen_at        TEXT NOT NULL,
  result_first_seen_at TEXT,
  -- Availability: when the result could be known. Historical imports use a documented assumption.
  result_available_at       TEXT CHECK (result_available_at IS NULL OR result_available_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  result_availability_basis TEXT CHECK (result_availability_basis IN ('assumed_tipoff_plus_4h', 'observed_retrieval')),
  last_raw_id    INTEGER NOT NULL REFERENCES raw_responses(raw_id),
  updated_at     TEXT NOT NULL,
  CHECK (home_team_id <> away_team_id),
  CHECK ((status = 'final') = (home_score IS NOT NULL AND away_score IS NOT NULL AND result_available_at IS NOT NULL))
);
CREATE INDEX games_tipoff ON games(tipoff_at);
CREATE INDEX games_season ON games(season, season_type);

CREATE TABLE team_game_stats (
  game_id  TEXT NOT NULL REFERENCES games(game_id),
  team_id  TEXT NOT NULL REFERENCES teams(team_id),
  is_home  INTEGER NOT NULL CHECK (is_home IN (0, 1)),
  points   INTEGER NOT NULL,
  fgm INTEGER, fga INTEGER, fg3m INTEGER, fg3a INTEGER, ftm INTEGER, fta INTEGER,
  rebounds INTEGER, assists INTEGER,
  last_raw_id INTEGER NOT NULL REFERENCES raw_responses(raw_id),
  PRIMARY KEY (game_id, team_id)
);

-- Append-only market snapshots. quote_time is NULL when the source does not publish one.
CREATE TABLE odds_quotes (
  quote_id      INTEGER PRIMARY KEY,
  game_id       TEXT NOT NULL REFERENCES games(game_id),
  provider      TEXT NOT NULL,
  market        TEXT NOT NULL CHECK (market = 'moneyline'),
  price_label   TEXT NOT NULL CHECK (price_label IN ('open', 'current')),
  home_american INTEGER NOT NULL,
  away_american INTEGER NOT NULL,
  home_prob_devig REAL NOT NULL CHECK (home_prob_devig > 0 AND home_prob_devig < 1),
  quote_time    TEXT,
  retrieved_at  TEXT NOT NULL,
  raw_id        INTEGER NOT NULL REFERENCES raw_responses(raw_id),
  UNIQUE (game_id, provider, market, price_label, raw_id)
);
CREATE TRIGGER odds_no_update BEFORE UPDATE ON odds_quotes
BEGIN SELECT RAISE(ABORT, 'odds_quotes are append-only'); END;
CREATE TRIGGER odds_no_delete BEFORE DELETE ON odds_quotes
BEGIN SELECT RAISE(ABORT, 'odds_quotes are append-only'); END;

-- Append-only forecast ledger. Outcomes live in games, never here.
CREATE TABLE predictions (
  prediction_id   INTEGER PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  game_id         TEXT NOT NULL REFERENCES games(game_id),
  run_type        TEXT NOT NULL CHECK (run_type IN ('live', 'backtest', 'demo')),
  model_version   TEXT NOT NULL,
  dataset_version TEXT NOT NULL,
  feature_version TEXT NOT NULL,
  cutoff_at       TEXT NOT NULL CHECK (cutoff_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  generated_at    TEXT NOT NULL CHECK (generated_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  home_win_prob   REAL NOT NULL CHECK (home_win_prob >= 0 AND home_win_prob <= 1)
);
CREATE INDEX predictions_game ON predictions(game_id);

CREATE TRIGGER predictions_no_update BEFORE UPDATE ON predictions
BEGIN SELECT RAISE(ABORT, 'predictions are append-only'); END;
CREATE TRIGGER predictions_no_delete BEFORE DELETE ON predictions
BEGIN SELECT RAISE(ABORT, 'predictions are append-only'); END;
-- A live forecast must be generated by its cutoff, and the cutoff must not be after tipoff.
CREATE TRIGGER predictions_live_pregame BEFORE INSERT ON predictions
WHEN NEW.run_type = 'live' AND (
  NEW.generated_at > NEW.cutoff_at
  OR NEW.cutoff_at > (SELECT tipoff_at FROM games WHERE game_id = NEW.game_id))
BEGIN SELECT RAISE(ABORT, 'live predictions must be generated before cutoff and tipoff'); END;
