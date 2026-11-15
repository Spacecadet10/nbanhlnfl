-- Soccer 1X2 forecast ledger (app/soccer_forecasts.py), append-only. Market: the 90-minute result only
-- ('1x2_regulation'); qualification, extra time and shoot-outs are separate outcomes with no forecasts here. Rows of other
-- markets (totals, both teams to score, ...) cannot be written: each needs its own evaluation and settlement rules first.
--
-- A run is one model version applied to one purpose. run_type says what a row is and how it may be labelled:
--   'backtest'     retrospective research forecasts of past matches (development / validation periods); never Live
--   'reserved_test' the one-shot test run (not executed in this task)
--   'prospective'  forecasts made before kickoff (not enabled; needs the user's go)
-- Timestamps kept apart:
--   cutoff_at     the information cutoff the forecast used (inputs available at or before it)
--   computed_at   wall-clock time the numbers were computed (a backtest computes long after its cutoff)
--   generated_at  wall-clock time the row was written to this ledger
--   published_at  when it was first shown as a forecast to users; NULL for research/backtest rows (never published)
CREATE TABLE soccer_forecast_runs (
  run_id          TEXT PRIMARY KEY,
  run_type        TEXT NOT NULL CHECK (run_type IN ('backtest', 'reserved_test', 'prospective')),
  period          TEXT NOT NULL CHECK (period IN ('development_tune', 'development_calibration', 'validation',
                                                  'reserved_test', 'prospective')),
  model           TEXT NOT NULL CHECK (model IN ('frequency', 'elo', 'poisson')),
  model_version   TEXT NOT NULL,                 -- sha256 of the code + protocol files that computed it
  config_json     TEXT NOT NULL,                 -- the configuration used (as selected on development)
  calibrated      INTEGER NOT NULL CHECK (calibrated IN (0, 1)),
  protocol_sha256 TEXT NOT NULL,
  data_digest     TEXT NOT NULL,                 -- open-data digest of the inputs
  candidate_sha256 TEXT,                         -- the sealed candidate, when the run belongs to one
  source_run      TEXT NOT NULL,                 -- the development run (soccer_eval run id) the rows come from
  label           TEXT NOT NULL,                 -- how the UI must label the rows: one fixed label per run type
  created_at      TEXT NOT NULL CHECK (created_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  CHECK ((run_type = 'backtest') = (period IN ('development_tune', 'development_calibration', 'validation'))),
  CHECK ((run_type = 'reserved_test') = (period = 'reserved_test')),
  CHECK ((run_type = 'backtest' AND label = 'Research backtest')
         OR (run_type = 'reserved_test' AND label = 'Reserved test (one-shot)')
         OR (run_type = 'prospective' AND label = 'Prospective research forecast'))
);
CREATE TRIGGER soccer_forecast_runs_no_update BEFORE UPDATE ON soccer_forecast_runs
BEGIN SELECT RAISE(ABORT, 'soccer_forecast_runs is append-only'); END;
CREATE TRIGGER soccer_forecast_runs_no_delete BEFORE DELETE ON soccer_forecast_runs
BEGIN SELECT RAISE(ABORT, 'soccer_forecast_runs is append-only'); END;

-- One row per forecast version. A retry of the same forecast (same run, match, cutoff and inputs) has the same
-- idempotency_key and is not stored twice; a genuinely new forecast (new inputs, e.g. after a result correction) gets a
-- new key and the next version.
CREATE TABLE soccer_forecasts (
  forecast_id     INTEGER PRIMARY KEY,
  run_id          TEXT NOT NULL REFERENCES soccer_forecast_runs(run_id),
  hist_match_id   TEXT NOT NULL REFERENCES soccer_hist_matches(hist_match_id),
  market          TEXT NOT NULL CHECK (market = '1x2_regulation'),
  version         INTEGER NOT NULL CHECK (version >= 1),
  idempotency_key TEXT NOT NULL UNIQUE,
  inputs_digest   TEXT NOT NULL,
  p_home          REAL NOT NULL CHECK (p_home > 0 AND p_home < 1),
  p_draw          REAL NOT NULL CHECK (p_draw > 0 AND p_draw < 1),
  p_away          REAL NOT NULL CHECK (p_away > 0 AND p_away < 1),
  cutoff_at       TEXT NOT NULL CHECK (cutoff_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  computed_at     TEXT NOT NULL CHECK (computed_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  generated_at    TEXT NOT NULL CHECK (generated_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  published_at    TEXT CHECK (published_at IS NULL OR published_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  UNIQUE (run_id, hist_match_id, market, version),
  CHECK (abs(p_home + p_draw + p_away - 1.0) <= 1e-9),
  CHECK (computed_at <= generated_at),
  CHECK (published_at IS NULL OR published_at >= generated_at)
);
CREATE INDEX soccer_forecasts_match ON soccer_forecasts(hist_match_id, run_id, version);
CREATE TRIGGER soccer_forecasts_no_update BEFORE UPDATE ON soccer_forecasts
BEGIN SELECT RAISE(ABORT, 'soccer_forecasts is append-only'); END;
CREATE TRIGGER soccer_forecasts_no_delete BEFORE DELETE ON soccer_forecasts
BEGIN SELECT RAISE(ABORT, 'soccer_forecasts is append-only'); END;
-- Every row: generated now (the database clock, within 10 minutes), never backdated; cutoff at or before 06:00 UTC on
-- the match's listed date.
CREATE TRIGGER soccer_forecasts_generated_now BEFORE INSERT ON soccer_forecasts
WHEN NEW.generated_at < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-10 minutes')
  OR NEW.generated_at > strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '+1 minute')
BEGIN SELECT RAISE(ABORT, 'generated_at must be the time the row is written (never backdated)'); END;
CREATE TRIGGER soccer_forecasts_cutoff_before_match BEFORE INSERT ON soccer_forecasts
WHEN NEW.cutoff_at > (SELECT local_date || 'T06:00:00Z' FROM soccer_hist_matches WHERE hist_match_id = NEW.hist_match_id)
BEGIN SELECT RAISE(ABORT, 'a forecast cutoff is at or before 06:00 UTC on the match date'); END;
-- A research/backtest or reserved-test row is never published.
CREATE TRIGGER soccer_forecasts_backtest_rules BEFORE INSERT ON soccer_forecasts
WHEN (SELECT run_type FROM soccer_forecast_runs WHERE run_id = NEW.run_id) IN ('backtest', 'reserved_test')
 AND NEW.published_at IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'a research forecast is never published'); END;
-- A prospective forecast must be generated at or before its cutoff (never backdated).
CREATE TRIGGER soccer_forecasts_prospective_rules BEFORE INSERT ON soccer_forecasts
WHEN (SELECT run_type FROM soccer_forecast_runs WHERE run_id = NEW.run_id) = 'prospective' AND NEW.generated_at > NEW.cutoff_at
BEGIN SELECT RAISE(ABORT, 'a prospective forecast must be generated at or before its cutoff'); END;
