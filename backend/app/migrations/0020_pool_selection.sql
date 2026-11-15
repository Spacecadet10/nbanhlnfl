-- Candidate-pool selection (docs/CANDIDATE_POOL.md §7). Append-only; existing rows and the core selector tables unchanged.

-- 1. The candidate-forecast guard matches the research-cohort guard (0016): the game must still be scheduled and the
--    calculation-completion time well formed, besides the two-hour window against the game's current start.
DROP TRIGGER candidate_forecasts_two_hour_window;
CREATE TRIGGER candidate_forecasts_two_hour_window BEFORE INSERT ON candidate_forecasts
WHEN NEW.generated_at NOT GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'
  OR NEW.generated_at < strftime('%Y-%m-%dT%H:%M:%SZ', NEW.tipoff_at, '-2 hours')
  OR NEW.generated_at >= NEW.tipoff_at
  OR NEW.tipoff_at IS NOT (SELECT tipoff_at FROM games WHERE game_id = NEW.game_id)
  OR (SELECT status FROM games WHERE game_id = NEW.game_id) IS NOT 'scheduled'
BEGIN SELECT RAISE(ABORT, 'candidate forecast must be within two hours before the current scheduled start of a scheduled game'); END;

-- 2. Weekly pool decisions: the same selector arithmetic as selector_decisions, over the core sources plus the sealed
--    pool registry's sources. Kept in their own table so every core (v1) reader and decision is unchanged.
CREATE TABLE pool_decisions (
  decision_id      INTEGER PRIMARY KEY,
  idempotency_key  TEXT NOT NULL UNIQUE,
  sport            TEXT NOT NULL CHECK (sport IN ('nba', 'nhl')),
  market           TEXT NOT NULL CHECK (market IN ('winner', 'reg3', 'spread', 'total', 'team_total_home', 'team_total_away')),
  selector_version TEXT NOT NULL,
  definition       TEXT NOT NULL,                   -- core research definition whose cohorts are the items
  registry         TEXT NOT NULL,                   -- sealed pool registry sha256
  scheduled_for    TEXT NOT NULL,                   -- evidence cutoff
  decided_at       TEXT NOT NULL,                   -- start of effect
  effective_until  TEXT NOT NULL,
  status           TEXT NOT NULL CHECK (status IN ('active', 'abstain', 'insufficient_evidence')),
  rule             TEXT NOT NULL DEFAULT 'null',
  lower_bound      REAL,
  reason           TEXT NOT NULL,
  evidence         TEXT NOT NULL,
  CHECK (scheduled_for <= decided_at AND decided_at < effective_until),
  CHECK ((status = 'active') = (rule <> 'null')),
  UNIQUE (sport, market, selector_version, definition, registry, scheduled_for)
);

-- 3. Pool selections: one row per core cohort and pool decision in force, made after the candidate forecasts of the
--    same two-hour cycle. `inputs` lists the stored candidate forecasts each pool source used.
CREATE TABLE pool_selections (
  selection_id    INTEGER PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  cohort_id       INTEGER NOT NULL REFERENCES research_cohorts(cohort_id),
  decision_id     INTEGER REFERENCES pool_decisions(decision_id),
  sport           TEXT NOT NULL CHECK (sport IN ('nba', 'nhl')),
  game_id         TEXT NOT NULL REFERENCES games(game_id),
  market          TEXT NOT NULL,
  generated_at    TEXT NOT NULL CHECK (generated_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  tipoff_at       TEXT NOT NULL,
  context         TEXT NOT NULL,
  status          TEXT NOT NULL CHECK (status IN ('pick', 'no_pick')),
  source          TEXT,
  side            TEXT CHECK (side IN ('a', 'mid', 'b')),
  probability     REAL,
  threshold       REAL,
  reason          TEXT NOT NULL,
  inputs          TEXT NOT NULL DEFAULT '{}',
  recommended     INTEGER NOT NULL CHECK (recommended IN (0, 1)),
  game_reason     TEXT NOT NULL,
  CHECK ((status = 'pick') = (side IS NOT NULL)),
  CHECK (recommended = 0 OR status = 'pick')
);
CREATE INDEX pool_selections_game ON pool_selections(game_id, generated_at);

CREATE TRIGGER pool_decisions_no_update BEFORE UPDATE ON pool_decisions BEGIN SELECT RAISE(ABORT, 'pool_decisions are append-only'); END;
CREATE TRIGGER pool_decisions_no_delete BEFORE DELETE ON pool_decisions BEGIN SELECT RAISE(ABORT, 'pool_decisions are append-only'); END;
CREATE TRIGGER pool_selections_no_update BEFORE UPDATE ON pool_selections BEGIN SELECT RAISE(ABORT, 'pool_selections are append-only'); END;
CREATE TRIGGER pool_selections_no_delete BEFORE DELETE ON pool_selections BEGIN SELECT RAISE(ABORT, 'pool_selections are append-only'); END;

CREATE TRIGGER pool_decisions_guard BEFORE INSERT ON pool_decisions
WHEN NEW.sport IS NOT (SELECT sport FROM store_identity)
BEGIN SELECT RAISE(ABORT, 'pool decision sport does not match this store'); END;
-- As research_selections_guard: its own cohort, a pool decision in force, within two hours before the current start.
CREATE TRIGGER pool_selections_guard BEFORE INSERT ON pool_selections
WHEN NEW.sport IS NOT (SELECT sport FROM store_identity)
  OR NOT EXISTS (SELECT 1 FROM research_cohorts c WHERE c.cohort_id = NEW.cohort_id AND c.game_id = NEW.game_id
                 AND c.market = NEW.market AND c.generated_at <= NEW.generated_at AND c.tipoff_at = NEW.tipoff_at)
  OR NEW.tipoff_at IS NOT (SELECT tipoff_at FROM games WHERE game_id = NEW.game_id)
  OR (SELECT status FROM games WHERE game_id = NEW.game_id) IS NOT 'scheduled'
  OR NEW.generated_at >= NEW.tipoff_at
  OR NEW.generated_at < strftime('%Y-%m-%dT%H:%M:%SZ', NEW.tipoff_at, '-2 hours')
  OR (NEW.decision_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM pool_decisions d WHERE d.decision_id = NEW.decision_id AND d.market = NEW.market
          AND d.decided_at <= NEW.generated_at
          AND NEW.generated_at < strftime('%Y-%m-%dT%H:%M:%SZ', d.effective_until, '+7 days')
          AND NOT EXISTS (SELECT 1 FROM pool_decisions n WHERE n.sport = d.sport AND n.market = d.market
                          AND n.selector_version = d.selector_version AND n.definition = d.definition
                          AND n.registry = d.registry AND n.decided_at > d.decided_at AND n.decided_at <= NEW.generated_at)))
BEGIN SELECT RAISE(ABORT, 'pool selection must use its cohort and a pool decision in force, within two hours before the start'); END;

-- 4. Provenance of candidate artifacts saved before digests existed: never loaded, superseded by a deterministic refit;
--    records whether the refit reproduced the content digest the earlier code registered. Append-only; `limitation`
--    states what this cannot establish.
CREATE TABLE artifact_adoptions (
  adoption_id         INTEGER PRIMARY KEY,
  candidate           TEXT NOT NULL,
  artifact            TEXT NOT NULL,
  legacy_sha256       TEXT NOT NULL,
  legacy_file_written_at TEXT NOT NULL,
  superseded_at       TEXT NOT NULL,
  new_sha256          TEXT NOT NULL,
  definition          TEXT NOT NULL,
  trained_season      INTEGER NOT NULL,
  training_games      INTEGER NOT NULL,
  results_fingerprint TEXT NOT NULL,
  content_digest      TEXT NOT NULL,
  registered_fit_id   INTEGER REFERENCES model_fits(fit_id),
  registered_digest_match INTEGER CHECK (registered_digest_match IN (0, 1)),
  limitation          TEXT NOT NULL,
  UNIQUE (artifact, legacy_sha256)
);
CREATE TRIGGER artifact_adoptions_no_update BEFORE UPDATE ON artifact_adoptions BEGIN SELECT RAISE(ABORT, 'artifact_adoptions are append-only'); END;
CREATE TRIGGER artifact_adoptions_no_delete BEFORE DELETE ON artifact_adoptions BEGIN SELECT RAISE(ABORT, 'artifact_adoptions are append-only'); END;
