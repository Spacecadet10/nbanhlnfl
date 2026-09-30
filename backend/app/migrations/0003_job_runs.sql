-- Operational log of refresh/predict/score jobs; drives "last successful update" in the app and refresh quotas.
CREATE TABLE job_runs (
  run_id      INTEGER PRIMARY KEY,
  job         TEXT NOT NULL CHECK (job IN ('refresh', 'predict', 'score')),
  started_at  TEXT NOT NULL CHECK (started_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  finished_at TEXT CHECK (finished_at IS NULL OR finished_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  status      TEXT NOT NULL CHECK (status IN ('running', 'success', 'failed', 'skipped')),
  attempts    INTEGER NOT NULL DEFAULT 0,
  detail      TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX job_runs_job_status ON job_runs(job, status, finished_at);
