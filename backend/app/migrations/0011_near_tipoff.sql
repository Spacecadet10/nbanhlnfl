-- Near-tipoff forecasts (docs/EVALUATION_PLAN.md): allows the 'near_tipoff' job (job_runs rebuilt with its rows)
-- and adds the append-only freshness record kept for each near-tipoff forecast.
CREATE TABLE job_runs_new (
  run_id      INTEGER PRIMARY KEY,
  job         TEXT NOT NULL CHECK (job IN ('refresh', 'predict', 'score', 'odds', 'boxscores', 'shadow', 'injuries', 'near_tipoff')),
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

-- One row per near-tipoff forecast in the ledger: what data it was made from and how fresh that data was.
CREATE TABLE near_tipoff_forecasts (
  prediction_id              INTEGER PRIMARY KEY REFERENCES predictions(prediction_id),
  game_id                    TEXT NOT NULL REFERENCES games(game_id),
  generated_at               TEXT NOT NULL CHECK (generated_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  tipoff_at                  TEXT NOT NULL,   -- scheduled tipoff when generated; equals the forecast's cutoff
  results_refreshed_at       TEXT NOT NULL,   -- last successful schedule and results refresh before generation
  latest_result_available_at TEXT,            -- newest result included in the model state
  results_used               INTEGER NOT NULL,
  market_quote_id            INTEGER REFERENCES odds_quotes(quote_id),   -- latest quote retrieved at or before generation
  market_home_prob           REAL CHECK (market_home_prob IS NULL OR (market_home_prob > 0 AND market_home_prob < 1)),
  market_retrieved_at        TEXT,
  CHECK (generated_at < tipoff_at),
  CHECK (results_refreshed_at <= generated_at),
  CHECK ((market_quote_id IS NULL) = (market_home_prob IS NULL))
);
CREATE INDEX near_tipoff_forecasts_game ON near_tipoff_forecasts(game_id, generated_at);
CREATE TRIGGER near_tipoff_forecasts_no_update BEFORE UPDATE ON near_tipoff_forecasts
BEGIN SELECT RAISE(ABORT, 'near_tipoff_forecasts are append-only'); END;
CREATE TRIGGER near_tipoff_forecasts_no_delete BEFORE DELETE ON near_tipoff_forecasts
BEGIN SELECT RAISE(ABORT, 'near_tipoff_forecasts are append-only'); END;
CREATE TRIGGER near_tipoff_forecasts_no_conflicting_insert BEFORE INSERT ON near_tipoff_forecasts
WHEN EXISTS (SELECT 1 FROM near_tipoff_forecasts WHERE prediction_id = NEW.prediction_id)
BEGIN SELECT RAISE(ABORT, 'near_tipoff_forecasts are append-only: conflicting insert rejected'); END;
