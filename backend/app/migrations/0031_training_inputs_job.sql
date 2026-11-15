-- Allows the pre-training input job in job_runs (training_inputs.py; rebuilt with its rows, as in 0004/0008/0010/0011/0012).
CREATE TABLE job_runs_new (
  run_id      INTEGER PRIMARY KEY,
  job         TEXT NOT NULL CHECK (job IN ('refresh', 'predict', 'score', 'odds', 'boxscores', 'shadow', 'injuries',
                                           'near_tipoff', 'markets', 'training_inputs')),
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
