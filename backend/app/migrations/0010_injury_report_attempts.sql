-- Injury-report collector pilot (docs/INJURY_COLLECTOR_PLAN.md): allows the 'injuries' job (job_runs rebuilt with its rows)
-- and adds the append-only attempt log.
CREATE TABLE job_runs_new (
  run_id      INTEGER PRIMARY KEY,
  job         TEXT NOT NULL CHECK (job IN ('refresh', 'predict', 'score', 'odds', 'boxscores', 'shadow', 'injuries')),
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

-- One row per HTTP attempt (GET or HEAD), successful or not. started_at is read before the request; completed_at only
-- after the whole response has arrived (or the error was observed). The PDF itself lives in the raw cache.
CREATE TABLE injury_report_attempts (
  attempt_id     INTEGER PRIMARY KEY,
  file_name      TEXT NOT NULL,
  slot_at        TEXT NOT NULL CHECK (slot_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  method         TEXT NOT NULL CHECK (method IN ('GET', 'HEAD')),
  purpose        TEXT NOT NULL CHECK (purpose IN ('collect', 'overwrite_check', 'redownload')),
  started_at     TEXT NOT NULL CHECK (started_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  completed_at   TEXT NOT NULL CHECK (completed_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  outcome        TEXT NOT NULL CHECK (outcome IN ('stored', 'not_available', 'failed', 'unchanged', 'changed_upstream')),
  http_status    INTEGER,
  last_modified  TEXT,
  etag           TEXT,
  content_length INTEGER,
  sha256         TEXT,
  byte_size      INTEGER,
  fetch_id       INTEGER REFERENCES raw_fetches(fetch_id),
  late           INTEGER NOT NULL DEFAULT 0 CHECK (late IN (0, 1)),
  error          TEXT,
  CHECK (completed_at >= started_at),
  CHECK ((outcome = 'stored') = (fetch_id IS NOT NULL))
);
CREATE INDEX injury_report_attempts_file ON injury_report_attempts(file_name, outcome);
CREATE INDEX injury_report_attempts_started ON injury_report_attempts(started_at);
CREATE TRIGGER injury_report_attempts_no_update BEFORE UPDATE ON injury_report_attempts
BEGIN SELECT RAISE(ABORT, 'injury_report_attempts are append-only'); END;
CREATE TRIGGER injury_report_attempts_no_delete BEFORE DELETE ON injury_report_attempts
BEGIN SELECT RAISE(ABORT, 'injury_report_attempts are append-only'); END;
CREATE TRIGGER injury_report_attempts_no_conflicting_insert BEFORE INSERT ON injury_report_attempts
WHEN EXISTS (SELECT 1 FROM injury_report_attempts WHERE attempt_id = NEW.attempt_id)
BEGIN SELECT RAISE(ABORT, 'injury_report_attempts are append-only: conflicting insert rejected'); END;
