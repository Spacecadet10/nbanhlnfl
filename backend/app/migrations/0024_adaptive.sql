-- Adaptive system adaptive-v1 (docs/ADAPTIVE_SYSTEM.md). Research only: separate tables, so no primary read, score,
-- vote, cohort, candidate or selector query can see these rows.

-- Every training attempt (member refit or combiner refit): success, partial (a combiner with a market lacking a usable
-- combination or a failed member refit: its usable markets apply, the week is retried), failed or skipped, with the reason.
-- The forecast path loads usable artifacts only, so a failed refit leaves the previous valid one in force and stays visible.
CREATE TABLE adaptive_fits (
  fit_id          INTEGER PRIMARY KEY,
  sport           TEXT NOT NULL,
  kind            TEXT NOT NULL CHECK (kind IN ('member', 'combiner')),
  name            TEXT NOT NULL,            -- member name, or 'combiner'
  definition      TEXT NOT NULL,
  period          TEXT NOT NULL,            -- refit period the fit was prepared for, e.g. '2027-W03'
  schedule        TEXT NOT NULL,
  status          TEXT NOT NULL CHECK (status IN ('success', 'partial', 'failed', 'skipped')),
  training_cutoff TEXT,                     -- results available through (success only)
  training_games  INTEGER,
  results_fingerprint TEXT,
  artifact_path   TEXT,
  artifact_sha256 TEXT,
  model_fit_id    INTEGER REFERENCES model_fits(fit_id),
  detail          TEXT NOT NULL,            -- JSON: weights, members, exclusions, l2 choice, selection decision, error
  started_at      TEXT NOT NULL,
  finished_at     TEXT NOT NULL,
  idempotency_key TEXT NOT NULL
);
CREATE INDEX adaptive_fits_lookup ON adaptive_fits(sport, kind, name, definition, status, finished_at);
CREATE UNIQUE INDEX adaptive_fits_once ON adaptive_fits(idempotency_key) WHERE status IN ('success', 'partial');
CREATE TRIGGER adaptive_fits_no_update BEFORE UPDATE ON adaptive_fits
BEGIN SELECT RAISE(ABORT, 'adaptive_fits are append-only'); END;
CREATE TRIGGER adaptive_fits_no_delete BEFORE DELETE ON adaptive_fits
BEGIN SELECT RAISE(ABORT, 'adaptive_fits are append-only'); END;

-- One adaptive forecast per game, market and exact line per cycle (identical inputs → the same row). Also written when the
-- combination is unavailable (status 'unavailable', probabilities NULL) so the reason is on record and visible.
CREATE TABLE adaptive_forecasts (
  forecast_id     INTEGER PRIMARY KEY,
  game_id         TEXT NOT NULL REFERENCES games(game_id),
  sport           TEXT NOT NULL,
  market          TEXT NOT NULL CHECK (market IN ('winner', 'reg3', 'spread', 'total', 'team_total_home', 'team_total_away')),
  line            REAL,
  line_source     TEXT NOT NULL,            -- 'none' (winner, reg3), 'market' or 'model' (the primary forecast's line source)
  line_ref        TEXT,                     -- the primary two-hour market forecast the line came from
  market_quote_id INTEGER,
  bookmaker       TEXT,                     -- JSON de-vigged quote at the same line, or NULL
  odds_status     TEXT NOT NULL,            -- 'available' or why not
  model_version   TEXT NOT NULL CHECK (model_version LIKE 'adaptive-v1:%'),
  definition      TEXT NOT NULL,
  combiner_fit_id INTEGER REFERENCES adaptive_fits(fit_id),
  status          TEXT NOT NULL CHECK (status IN ('forecast', 'unavailable')),
  reason          TEXT,                     -- why no combined probability (status 'unavailable')
  p_a             REAL CHECK (p_a IS NULL OR p_a BETWEEN 0 AND 1),
  p_mid           REAL CHECK (p_mid IS NULL OR p_mid BETWEEN 0 AND 1),
  p_b             REAL CHECK (p_b IS NULL OR p_b BETWEEN 0 AND 1),
  members         TEXT NOT NULL,            -- JSON: [{name, p, fit_id, weight}]
  missing         TEXT NOT NULL,            -- JSON: {name: reason}
  weights         TEXT NOT NULL,            -- JSON: the stack applied (weights, bias, members) or NULL
  selection       TEXT NOT NULL,            -- JSON: {status, side, probability, reason}
  training_cutoff TEXT,                     -- combiner training rows' outcomes available through
  weights_updated_at TEXT,                  -- when the combiner fit in force finished
  information_at  TEXT NOT NULL,
  generated_at    TEXT NOT NULL,
  tipoff_at       TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  CHECK ((status = 'forecast') = (p_a IS NOT NULL)),
  CHECK (p_a IS NULL OR abs(p_a + p_mid + p_b - 1) < 1e-6),
  CHECK ((market IN ('winner', 'reg3')) = (line IS NULL))
);
CREATE INDEX adaptive_forecasts_game ON adaptive_forecasts(game_id, market, generated_at);
CREATE INDEX adaptive_forecasts_time ON adaptive_forecasts(sport, definition, tipoff_at);
CREATE TRIGGER adaptive_forecasts_no_update BEFORE UPDATE ON adaptive_forecasts
BEGIN SELECT RAISE(ABORT, 'adaptive_forecasts are append-only'); END;
CREATE TRIGGER adaptive_forecasts_no_delete BEFORE DELETE ON adaptive_forecasts
BEGIN SELECT RAISE(ABORT, 'adaptive_forecasts are append-only'); END;
CREATE TRIGGER adaptive_forecasts_two_hour_window BEFORE INSERT ON adaptive_forecasts
WHEN NEW.generated_at NOT GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'
  OR NEW.generated_at < strftime('%Y-%m-%dT%H:%M:%SZ', NEW.tipoff_at, '-2 hours')
  OR NEW.generated_at >= NEW.tipoff_at
  OR NEW.tipoff_at IS NOT (SELECT tipoff_at FROM games WHERE game_id = NEW.game_id)
  OR (SELECT status FROM games WHERE game_id = NEW.game_id) IS NOT 'scheduled'
BEGIN SELECT RAISE(ABORT, 'adaptive forecast must be within two hours before the current scheduled start of a scheduled game'); END;
