-- Codex step 6 review: the possession refresh must budget actual HTTP attempts, including retries and failures.
-- One append-only row per attempt, written outside the import transaction so a failed or rolled-back import still counts.
CREATE TABLE box_fetch_attempts (
  attempt_id   INTEGER PRIMARY KEY,
  game_id      TEXT NOT NULL REFERENCES games(game_id),
  attempted_at TEXT NOT NULL CHECK (attempted_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  outcome      TEXT NOT NULL CHECK (outcome IN ('fetched', 'failed')),
  fetch_id     INTEGER REFERENCES raw_fetches(fetch_id),
  error        TEXT,
  CHECK ((outcome = 'fetched') = (fetch_id IS NOT NULL))
);
CREATE INDEX box_fetch_attempts_game ON box_fetch_attempts(game_id);
CREATE TRIGGER box_fetch_attempts_no_update BEFORE UPDATE ON box_fetch_attempts
BEGIN SELECT RAISE(ABORT, 'box_fetch_attempts are append-only'); END;
CREATE TRIGGER box_fetch_attempts_no_delete BEFORE DELETE ON box_fetch_attempts
BEGIN SELECT RAISE(ABORT, 'box_fetch_attempts are append-only'); END;
CREATE TRIGGER box_fetch_attempts_no_conflicting_insert BEFORE INSERT ON box_fetch_attempts
WHEN EXISTS (SELECT 1 FROM box_fetch_attempts WHERE attempt_id = NEW.attempt_id)
BEGIN SELECT RAISE(ABORT, 'box_fetch_attempts are append-only: conflicting insert rejected'); END;
