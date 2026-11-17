-- NCAAB (docs/NCAAB_PLAN.md): the sport CHECKs of the three tables an NCAAB store writes through the shared paths allow
-- 'ncaab'. store_identity (as 0034), market_quotes (as 0033) and market_forecasts (0012) are rebuilt with their rows, indexes
-- and triggers exactly as they stood, so nothing but the sport CHECK changes. A store that already allows 'ncaab' is rebuilt
-- the same way (idempotent in effect). Every row is copied unchanged; the append-only triggers are recreated.
-- Parent-table rebuild: db.migrate disables foreign keys outside this transaction and checks all references before commit.
PRAGMA legacy_alter_table = ON;
CREATE TABLE store_identity_new (
  sport      TEXT PRIMARY KEY CHECK (sport IN ('nba', 'nhl', 'nfl', 'soccer', 'ncaab')),
  claimed_at TEXT NOT NULL
);
INSERT INTO store_identity_new SELECT * FROM store_identity;
DROP TABLE store_identity;
ALTER TABLE store_identity_new RENAME TO store_identity;
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
DROP TABLE market_quotes;
ALTER TABLE market_quotes_new RENAME TO market_quotes;
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
CREATE TABLE market_forecasts_new (
  forecast_id     INTEGER PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  sport           TEXT NOT NULL CHECK (sport IN ('nba', 'nhl', 'ncaab')),
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
INSERT INTO market_forecasts_new SELECT * FROM market_forecasts;
DROP TABLE market_forecasts;
ALTER TABLE market_forecasts_new RENAME TO market_forecasts;
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
CREATE TRIGGER market_forecasts_quote_matches BEFORE INSERT ON market_forecasts
WHEN NEW.market_quote_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM market_quotes q WHERE q.quote_id = NEW.market_quote_id AND q.game_id = NEW.game_id
    AND q.market = NEW.market AND q.line = NEW.line
    AND q.retrieved_at <= NEW.generated_at AND q.retrieved_at <= NEW.cutoff_at)
BEGIN SELECT RAISE(ABORT, 'market forecast quote does not match its game, market, line or time'); END;
CREATE TRIGGER market_forecasts_tipoff_valid BEFORE INSERT ON market_forecasts
WHEN NEW.tipoff_at NOT GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'
  OR (NEW.run_type = 'live' AND NEW.tipoff_at IS NOT (SELECT tipoff_at FROM games WHERE game_id = NEW.game_id))
BEGIN SELECT RAISE(ABORT, 'market forecast tipoff_at must be the game''s scheduled start'); END;
CREATE TRIGGER markets_two_hour_window BEFORE INSERT ON market_forecasts
WHEN NEW.run_type='live' AND NEW.model_version LIKE '%-window=2h-policy=near_tipoff'
  AND (NEW.generated_at NOT GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'
       OR NEW.generated_at < strftime('%Y-%m-%dT%H:%M:%SZ', NEW.tipoff_at, '-2 hours')
       OR NEW.generated_at >= NEW.tipoff_at OR NEW.cutoff_at IS NOT NEW.tipoff_at
       OR NEW.tipoff_at IS NOT (SELECT tipoff_at FROM games WHERE game_id=NEW.game_id)
       OR (SELECT status FROM games WHERE game_id=NEW.game_id) IS NOT 'scheduled')
BEGIN SELECT RAISE(ABORT, 'two-hour market forecast must be within two hours before the current scheduled start of a scheduled game'); END;
PRAGMA legacy_alter_table = OFF;
