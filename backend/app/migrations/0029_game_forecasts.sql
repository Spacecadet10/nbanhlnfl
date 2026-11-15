-- Every-game coverage ledger (app/coverage.py, docs/COVERAGE.md). One row per game, market and cycle outcome for EVERY
-- scheduled game of a forecastable season type (preseason, regular, play-in, playoffs) within two hours of its start:
-- the adaptive ensemble when it produced a probability, else the sealed primary model (as recorded by the two-hour
-- primary writers, or computed here with the same stale-data and missing-result guards), else a 'missing' row with the
-- reasons. Records the actual source and why each earlier source was not used. Nothing trains on these rows.
CREATE TABLE game_forecasts (
  forecast_id      INTEGER PRIMARY KEY,
  game_id          TEXT NOT NULL REFERENCES games(game_id),
  sport            TEXT NOT NULL,
  season_type      TEXT NOT NULL,
  market           TEXT NOT NULL CHECK (market IN ('winner', 'reg3', 'spread', 'total', 'team_total_home', 'team_total_away')),
  order_version    TEXT NOT NULL,              -- the fallback order in force, e.g. 'coverage-v1'
  status           TEXT NOT NULL CHECK (status IN ('forecast', 'missing')),
  source           TEXT NOT NULL CHECK (source IN ('adaptive', 'primary_recorded', 'primary_direct', 'none')),
  fallback_rank    INTEGER,                    -- 1 adaptive, 2 recorded primary, 3 primary computed here; NULL if missing
  fallback_reason  TEXT,                       -- why the earlier sources were not used (NULL for the adaptive ensemble)
  reason           TEXT,                       -- status 'missing': why no source could forecast
  model_version    TEXT,
  source_table     TEXT,                       -- the ledger row the probability was taken from, if any
  source_id        INTEGER,
  source_generated_at TEXT,
  line             REAL,
  line_source      TEXT NOT NULL CHECK (line_source IN ('bookmaker', 'model', 'none')),
  market_quote_id  INTEGER,
  p_a              REAL CHECK (p_a IS NULL OR p_a BETWEEN 0 AND 1),
  p_mid            REAL CHECK (p_mid IS NULL OR p_mid BETWEEN 0 AND 1),
  p_b              REAL CHECK (p_b IS NULL OR p_b BETWEEN 0 AND 1),
  confidence       REAL CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
  generated_at     TEXT NOT NULL,
  tipoff_at        TEXT NOT NULL,
  idempotency_key  TEXT NOT NULL UNIQUE,
  CHECK ((status = 'forecast') = (p_a IS NOT NULL)),
  CHECK ((status = 'forecast') = (source != 'none')),
  CHECK ((status = 'missing') = (reason IS NOT NULL)),
  CHECK (p_a IS NULL OR abs(p_a + p_mid + p_b - 1) < 1e-6),
  CHECK (market IN ('winner', 'reg3') OR status = 'missing' OR line IS NOT NULL)
);
CREATE INDEX game_forecasts_game ON game_forecasts(game_id, market, generated_at);
CREATE INDEX game_forecasts_time ON game_forecasts(sport, tipoff_at);
CREATE TRIGGER game_forecasts_no_update BEFORE UPDATE ON game_forecasts
BEGIN SELECT RAISE(ABORT, 'game_forecasts are append-only'); END;
CREATE TRIGGER game_forecasts_no_delete BEFORE DELETE ON game_forecasts
BEGIN SELECT RAISE(ABORT, 'game_forecasts are append-only'); END;
CREATE TRIGGER game_forecasts_pregame BEFORE INSERT ON game_forecasts
WHEN NEW.generated_at NOT GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'
  OR NEW.generated_at < strftime('%Y-%m-%dT%H:%M:%SZ', NEW.tipoff_at, '-2 hours')
  OR NEW.generated_at >= NEW.tipoff_at
  OR NEW.tipoff_at IS NOT (SELECT tipoff_at FROM games WHERE game_id = NEW.game_id)
  OR (SELECT status FROM games WHERE game_id = NEW.game_id) IS NOT 'scheduled'
BEGIN SELECT RAISE(ABORT, 'game_forecasts must be generated within two hours before the start of a scheduled game'); END;
