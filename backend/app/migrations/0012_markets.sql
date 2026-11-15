-- Multi-sport and multi-market foundations (docs/MULTISPORT_PLAN.md, docs/MARKETS.md).
-- Each sport has its own store; store_identity binds a store to one sport (one row, never changed).
CREATE TABLE store_identity (
  sport      TEXT PRIMARY KEY CHECK (sport IN ('nba', 'nhl')),
  claimed_at TEXT NOT NULL
);
CREATE TRIGGER store_identity_single BEFORE INSERT ON store_identity
WHEN EXISTS (SELECT 1 FROM store_identity)
BEGIN SELECT RAISE(ABORT, 'store_identity is set once per store'); END;
CREATE TRIGGER store_identity_no_update BEFORE UPDATE ON store_identity
BEGIN SELECT RAISE(ABORT, 'store_identity is immutable'); END;
CREATE TRIGGER store_identity_no_delete BEFORE DELETE ON store_identity
BEGIN SELECT RAISE(ABORT, 'store_identity is immutable'); END;

-- How a final game was decided (NHL: REG/OT/SO; NBA: REG/OT when known). Latest response wins, like games;
-- needed to settle the NHL regulation three-way market and to rebuild regulation scores.
CREATE TABLE game_decisions (
  game_id     TEXT PRIMARY KEY REFERENCES games(game_id),
  decision    TEXT NOT NULL CHECK (decision IN ('REG', 'OT', 'SO')),
  periods     INTEGER NOT NULL CHECK (periods >= 1),
  last_raw_id INTEGER NOT NULL REFERENCES raw_responses(raw_id)
);

-- Bookmaker quotes with a line (and moneylines) per retrieval event. Append-only; identical content fetched again after
-- a change is a new row at its own retrieval time. quote_time stays NULL: the free source publishes none.
CREATE TABLE market_quotes (
  quote_id     INTEGER PRIMARY KEY,
  sport        TEXT NOT NULL CHECK (sport IN ('nba', 'nhl')),
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

-- Append-only ledger for markets other than the winner (the winner stays in `predictions`). One row = one game, market,
-- exact line, policy and model version at one cutoff. p_a / p_mid / p_b follow docs/MARKETS.md.
CREATE TABLE market_forecasts (
  forecast_id     INTEGER PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  sport           TEXT NOT NULL CHECK (sport IN ('nba', 'nhl')),
  game_id         TEXT NOT NULL REFERENCES games(game_id),
  market          TEXT NOT NULL CHECK (market IN ('reg3', 'spread', 'total', 'team_total_home', 'team_total_away')),
  line            REAL,
  line_source     TEXT CHECK (line_source IN ('market', 'model')),
  market_quote_id INTEGER REFERENCES market_quotes(quote_id),
  policy          TEXT NOT NULL CHECK (policy IN ('pregame', 'near_tipoff')),
  run_type        TEXT NOT NULL CHECK (run_type IN ('live', 'backtest', 'demo')),
  model_version   TEXT NOT NULL,
  dataset_version TEXT NOT NULL,
  feature_version TEXT NOT NULL,
  cutoff_at       TEXT NOT NULL CHECK (cutoff_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  generated_at    TEXT NOT NULL CHECK (generated_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  tipoff_at       TEXT NOT NULL,   -- the game's scheduled start when the forecast was generated
  p_a             REAL NOT NULL CHECK (p_a >= 0 AND p_a <= 1),
  p_mid           REAL NOT NULL CHECK (p_mid >= 0 AND p_mid <= 1),
  p_b             REAL NOT NULL CHECK (p_b >= 0 AND p_b <= 1),
  params          TEXT NOT NULL DEFAULT '{}',   -- distribution parameters, for inspection only
  CHECK (abs(p_a + p_mid + p_b - 1) < 1e-6),
  CHECK ((market = 'reg3') = (line IS NULL)),
  CHECK ((line IS NULL) = (line_source IS NULL)),
  CHECK ((line_source = 'market') = (market_quote_id IS NOT NULL)),
  CHECK (market <> 'reg3' OR sport = 'nhl')
);
CREATE INDEX market_forecasts_game ON market_forecasts(game_id, market, policy, generated_at);
CREATE TRIGGER market_forecasts_no_update BEFORE UPDATE ON market_forecasts
BEGIN SELECT RAISE(ABORT, 'market_forecasts are append-only'); END;
CREATE TRIGGER market_forecasts_no_delete BEFORE DELETE ON market_forecasts
BEGIN SELECT RAISE(ABORT, 'market_forecasts are append-only'); END;
CREATE TRIGGER market_forecasts_no_conflicting_insert BEFORE INSERT ON market_forecasts
WHEN EXISTS (SELECT 1 FROM market_forecasts
             WHERE forecast_id = NEW.forecast_id OR idempotency_key = NEW.idempotency_key)
BEGIN SELECT RAISE(ABORT, 'market_forecasts are append-only: conflicting insert rejected'); END;
CREATE TRIGGER market_forecasts_live_pregame BEFORE INSERT ON market_forecasts
WHEN NEW.run_type = 'live' AND (
  NEW.generated_at > NEW.cutoff_at
  OR NEW.cutoff_at > (SELECT tipoff_at FROM games WHERE game_id = NEW.game_id))
BEGIN SELECT RAISE(ABORT, 'live market forecasts must be generated before cutoff and tipoff'); END;
CREATE TRIGGER market_forecasts_store_sport BEFORE INSERT ON market_forecasts
WHEN NEW.sport IS NOT (SELECT sport FROM store_identity)
BEGIN SELECT RAISE(ABORT, 'market forecast sport does not match this store'); END;
-- A quote used as the forecast's line must be for the same game and market, already retrieved, and carry that exact line.
CREATE TRIGGER market_forecasts_quote_matches BEFORE INSERT ON market_forecasts
WHEN NEW.market_quote_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM market_quotes q WHERE q.quote_id = NEW.market_quote_id AND q.game_id = NEW.game_id
    AND q.market = NEW.market AND q.line = NEW.line AND q.retrieved_at <= NEW.generated_at)
BEGIN SELECT RAISE(ABORT, 'market forecast quote does not match its game, market, line or time'); END;

-- Allows the market and NHL jobs in job_runs (rebuilt with its rows, as in 0004/0008/0010/0011).
CREATE TABLE job_runs_new (
  run_id      INTEGER PRIMARY KEY,
  job         TEXT NOT NULL CHECK (job IN ('refresh', 'predict', 'score', 'odds', 'boxscores', 'shadow', 'injuries',
                                           'near_tipoff', 'markets')),
  started_at  TEXT NOT NULL CHECK (started_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  finished_at TEXT CHECK (finished_at IS NULL OR finished_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  status      TEXT NOT NULL CHECK (status IN ('running', 'success', 'failed', 'skipped')),
  attempts    INTEGER NOT NULL DEFAULT 0,
  detail      TEXT NOT NULL DEFAULT '{}'
);
INSERT INTO job_runs_new SELECT run_id, job, started_at, finished_at, status, attempts, detail FROM job_runs;
DROP TABLE job_runs;
ALTER TABLE job_runs_new RENAME TO job_runs;
CREATE INDEX job_runs_job_status ON job_runs(job, status, finished_at);
