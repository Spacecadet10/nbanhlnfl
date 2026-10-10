-- Retry state for adaptive reconstructions whose replay failed for a reason that is not evidence about the game (an
-- unreadable artifact, an evaluation error). Operational state, not a record: a row is replaced on every failed attempt
-- and removed once the game is recorded. Bounded: exponential backoff between attempts and a maximum attempt count, so
-- one broken game can neither block later games nor be retried forever. Never read by forecasts, training or reports
-- of forecast quality.
CREATE TABLE adaptive_recovery_attempts (
  game_id          TEXT NOT NULL REFERENCES games(game_id),
  tipoff_at        TEXT NOT NULL,
  definition       TEXT NOT NULL,
  attempts         INTEGER NOT NULL CHECK (attempts >= 1),
  first_failed_at  TEXT NOT NULL,
  last_attempt_at  TEXT NOT NULL,
  next_attempt_at  TEXT NOT NULL,
  last_error       TEXT NOT NULL,
  PRIMARY KEY (game_id, tipoff_at, definition)
);
