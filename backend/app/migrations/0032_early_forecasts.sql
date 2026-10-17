-- Early-forecast test (docs/evidence/early-forecast-test/PREREGISTRATION.md). Research only: a separate table, so no
-- primary read, score, vote, cohort, candidate, adaptive or selector query can see these rows. NHL winner forecasts made
-- earlier than the two-hour cycle (arm 'open': when the game first has a bookmaker line; arm 'news': after an injury
-- capture that changed a team's report), paper-traded at the first quote retrieved after them (app/paper_trading.py).
CREATE TABLE early_forecasts (
  early_id        INTEGER PRIMARY KEY,
  sport           TEXT NOT NULL,
  arm             TEXT NOT NULL CHECK (arm IN ('open', 'news')),
  member          TEXT NOT NULL,
  model_version   TEXT NOT NULL,            -- research definition (open) or adaptive definition (news)
  game_id         TEXT NOT NULL REFERENCES games(game_id),
  market          TEXT NOT NULL CHECK (market = 'winner'),
  status          TEXT NOT NULL CHECK (status IN ('forecast', 'unavailable')),
  reason          TEXT,                     -- why no probability (status 'unavailable')
  p_a             REAL CHECK (p_a IS NULL OR p_a BETWEEN 0 AND 1),
  p_b             REAL CHECK (p_b IS NULL OR p_b BETWEEN 0 AND 1),
  information_at  TEXT NOT NULL,            -- nothing retrieved after this time was used
  trigger_detail  TEXT NOT NULL,            -- JSON: the first quote (open) or the capture and changed teams (news)
  provenance      TEXT NOT NULL,            -- JSON: fit applied, results used, digests
  tipoff_at       TEXT NOT NULL,
  generated_at    TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  CHECK (information_at <= generated_at AND generated_at < tipoff_at),
  CHECK ((status = 'forecast' AND p_a IS NOT NULL AND p_b IS NOT NULL) OR (status = 'unavailable' AND reason IS NOT NULL))
);
CREATE INDEX early_forecasts_game ON early_forecasts(game_id, arm);
CREATE TRIGGER early_forecasts_pregame BEFORE INSERT ON early_forecasts
WHEN NOT EXISTS (SELECT 1 FROM games g WHERE g.game_id = NEW.game_id AND g.status = 'scheduled'
                 AND g.tipoff_at = NEW.tipoff_at)
BEGIN SELECT RAISE(ABORT, 'early forecast must be for a scheduled game at its current start time'); END;
CREATE TRIGGER early_forecasts_no_update BEFORE UPDATE ON early_forecasts
BEGIN SELECT RAISE(ABORT, 'early_forecasts are append-only'); END;
CREATE TRIGGER early_forecasts_no_delete BEFORE DELETE ON early_forecasts
BEGIN SELECT RAISE(ABORT, 'early_forecasts are append-only'); END;

-- One row per early-forecast run that reached the store (PREREGISTRATION.md §7: outages must be visible, and "no news"
-- must be told apart from "the job was not running"). A run that could not take the store lock writes nothing here; it
-- is in the job's log only.
CREATE TABLE early_runs (
  run_id          INTEGER PRIMARY KEY,
  information_at  TEXT NOT NULL,            -- the run's `now`
  finished_at     TEXT NOT NULL,
  status          TEXT NOT NULL CHECK (status IN ('success', 'partial')),
  detail          TEXT NOT NULL             -- JSON: rows created and refused per arm, capture, news reason, bad captures
);
CREATE TRIGGER early_runs_no_update BEFORE UPDATE ON early_runs
BEGIN SELECT RAISE(ABORT, 'early_runs are append-only'); END;
CREATE TRIGGER early_runs_no_delete BEFORE DELETE ON early_runs
BEGIN SELECT RAISE(ABORT, 'early_runs are append-only'); END;
