-- Retrospective adaptive reconstructions for games the two-hour cycle missed (machine asleep, fit not yet published).
-- Separate from adaptive_forecasts: never read by live forecasts, the selector, prospective rows or combiner training.
-- Only artifacts whose training results end at or before the simulated pregame cutoff may be applied; otherwise the
-- game/market is recorded as 'unreconstructable' with the reason (future-trained weights are never used).
CREATE TABLE adaptive_recovered_forecasts (
  recovery_id              INTEGER PRIMARY KEY,
  game_id                  TEXT NOT NULL REFERENCES games(game_id),
  sport                    TEXT NOT NULL,
  market                   TEXT NOT NULL CHECK (market IN ('winner', 'reg3', 'spread', 'total', 'team_total_home', 'team_total_away')),
  tipoff_at                TEXT NOT NULL,
  cutoff_at                TEXT NOT NULL,             -- simulated pregame information time (one second before the start)
  reconstructed_at         TEXT NOT NULL,             -- actual time the reconstruction ran (after the start)
  definition               TEXT NOT NULL,
  model_version            TEXT NOT NULL CHECK (model_version LIKE 'adaptive-v1:%'),
  status                   TEXT NOT NULL CHECK (status IN ('reconstructed', 'unreconstructable')),
  reason                   TEXT,
  line                     REAL,
  line_source              TEXT,                      -- 'none', 'market' or 'model'
  line_ref                 TEXT,                      -- market_forecasts:<id> (live primary) or recovered_forecasts:<id>
  quote_id                 INTEGER,
  quote_retrieved_at       TEXT,
  bookmaker                TEXT,                      -- JSON de-vigged quote at the same line, retrieved before the cutoff
  p_a                      REAL CHECK (p_a IS NULL OR p_a BETWEEN 0 AND 1),
  p_mid                    REAL CHECK (p_mid IS NULL OR p_mid BETWEEN 0 AND 1),
  p_b                      REAL CHECK (p_b IS NULL OR p_b BETWEEN 0 AND 1),
  members                  TEXT NOT NULL,             -- JSON: [{name, p, weight, fit_id, artifact_sha256, training_cutoff, finished_at}]
  missing                  TEXT NOT NULL,             -- JSON: {name: reason}
  weights                  TEXT,                      -- JSON: the stack applied, or NULL
  combiner_fit_id          INTEGER REFERENCES adaptive_fits(fit_id),
  combiner_sha256          TEXT,
  combiner_training_cutoff TEXT,
  combiner_finished_at     TEXT,                      -- may be after the start: the reconstruction is retrospective
  results_used             INTEGER,
  latest_result_at         TEXT,
  UNIQUE (game_id, market, tipoff_at, definition),
  CHECK (cutoff_at < tipoff_at AND reconstructed_at >= tipoff_at),
  CHECK ((status = 'reconstructed') = (p_a IS NOT NULL)),
  CHECK (status = 'reconstructed' OR reason IS NOT NULL),
  CHECK (p_a IS NULL OR abs(p_a + p_mid + p_b - 1) < 0.000001),
  CHECK (status = 'unreconstructable' OR (combiner_fit_id IS NOT NULL AND combiner_training_cutoff <= cutoff_at)),
  CHECK (latest_result_at IS NULL OR latest_result_at <= cutoff_at),
  CHECK (quote_retrieved_at IS NULL OR quote_retrieved_at <= cutoff_at)
);
CREATE INDEX adaptive_recovered_time ON adaptive_recovered_forecasts(sport, definition, tipoff_at);
CREATE TRIGGER adaptive_recovered_no_update BEFORE UPDATE ON adaptive_recovered_forecasts
BEGIN SELECT RAISE(ABORT, 'adaptive reconstructions are append-only'); END;
CREATE TRIGGER adaptive_recovered_no_delete BEFORE DELETE ON adaptive_recovered_forecasts
BEGIN SELECT RAISE(ABORT, 'adaptive reconstructions are append-only'); END;
