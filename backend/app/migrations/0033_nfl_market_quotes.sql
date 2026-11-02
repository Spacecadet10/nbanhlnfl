-- Parent-table rebuild: db.migrate disables foreign keys outside this transaction, checks all references before commit, then restores enforcement.
CREATE TABLE market_quotes_new (
  quote_id     INTEGER PRIMARY KEY,
  sport        TEXT NOT NULL CHECK (sport IN ('nba', 'nhl', 'nfl')),
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

