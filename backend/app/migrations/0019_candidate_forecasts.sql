-- Candidate pool prospective forecasts (docs/CANDIDATE_POOL.md §6). Research only: kept out of `predictions` and
-- `market_forecasts`, so no primary read, score or vote can see them. Append-only; a retry with identical inputs is a no-op
-- (idempotency key); changed inputs or a new fit are a new row. Two-hour window and before tipoff, as migration 0015.
CREATE TABLE candidate_forecasts (
  forecast_id     INTEGER PRIMARY KEY,
  game_id         TEXT NOT NULL REFERENCES games(game_id),
  candidate       TEXT NOT NULL,
  model_version   TEXT NOT NULL CHECK (model_version LIKE 'research-cand:%'),
  market          TEXT NOT NULL CHECK (market IN ('winner', 'reg3', 'spread', 'total', 'team_total_home', 'team_total_away')),
  line            REAL,
  line_source     TEXT NOT NULL,            -- 'none' (winner, reg3) or where the exact line came from, e.g. 'core_cohort'
  line_ref        TEXT,                     -- identity of the line's record (cohort / quote), for exact alignment
  p_a             REAL NOT NULL CHECK (p_a BETWEEN 0 AND 1),
  p_mid           REAL NOT NULL CHECK (p_mid BETWEEN 0 AND 1),
  p_b             REAL NOT NULL CHECK (p_b BETWEEN 0 AND 1),
  params          TEXT NOT NULL,            -- JSON: distribution parameters
  inputs          TEXT NOT NULL,            -- JSON: input provenance (feature table, goalie listings, staleness checks)
  fit_id          INTEGER REFERENCES model_fits(fit_id),
  artifact_sha256 TEXT NOT NULL,
  training_cutoff TEXT NOT NULL,            -- results available through, for the fit in force
  dataset_version TEXT NOT NULL,
  tipoff_at       TEXT NOT NULL,
  generated_at    TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  CHECK (abs(p_a + p_mid + p_b - 1) < 1e-6),
  CHECK ((market IN ('winner', 'reg3')) = (line IS NULL))
);
CREATE INDEX candidate_forecasts_game ON candidate_forecasts(game_id, market, candidate, generated_at);
CREATE TRIGGER candidate_forecasts_no_update BEFORE UPDATE ON candidate_forecasts
BEGIN SELECT RAISE(ABORT, 'candidate_forecasts are append-only'); END;
CREATE TRIGGER candidate_forecasts_no_delete BEFORE DELETE ON candidate_forecasts
BEGIN SELECT RAISE(ABORT, 'candidate_forecasts are append-only'); END;
CREATE TRIGGER candidate_forecasts_two_hour_window BEFORE INSERT ON candidate_forecasts
WHEN NEW.generated_at < strftime('%Y-%m-%dT%H:%M:%SZ', NEW.tipoff_at, '-2 hours')
  OR NEW.generated_at >= NEW.tipoff_at
  OR NEW.tipoff_at IS NOT (SELECT tipoff_at FROM games WHERE game_id = NEW.game_id)
BEGIN SELECT RAISE(ABORT, 'candidate forecast must be within two hours before tipoff and strictly before it'); END;
