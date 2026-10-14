-- exact-rules-v1 (docs/MODEL_SELECTION.md, amendment "exact-rules-v1"): registered exact subsets with PER-MEMBER thresholds
-- are tracked through the same observation table as tracking-v2. Two CHECK lists widen (SQLite cannot alter a CHECK):
--   kind   + 'exact'            an explicit member/threshold rule from the sealed exact-rule registry (`registry` = the rule's
--                               own sha256, `group_id` = its id); `statistic` = the minimum member confidence for the agreed
--                               side (descriptive, not an ensemble probability; `vector` stays null, never scored by Brier/log loss);
--   status + 'below_threshold'  every member present and agreeing, but at least one below its own threshold; `missing` then
--                               holds {member: reason} for the members below, as it holds the missing members of 'incomplete'
--                               and each member's side for 'disagree'.
-- Rebuilt exactly as 0022 did: every existing row is copied unchanged (same observation_id), the UNIQUE identity, index and
-- the three triggers are recreated verbatim. Nothing is backfilled: exact-rule rows exist only from the recording cycles after
-- a rule's registration.
CREATE TABLE agreement_observations_new (
  observation_id  INTEGER PRIMARY KEY,
  registry        TEXT NOT NULL,
  group_id        TEXT NOT NULL,
  sport           TEXT NOT NULL CHECK (sport IN ('nba', 'nhl')),
  game_id         TEXT NOT NULL REFERENCES games(game_id),
  market          TEXT NOT NULL CHECK (market IN ('winner', 'reg3', 'spread', 'total', 'team_total_home', 'team_total_away')),
  line            REAL,
  line_source     TEXT,
  cohort_id       INTEGER NOT NULL REFERENCES research_cohorts(cohort_id),
  source_cohort_id INTEGER NOT NULL REFERENCES research_cohorts(cohort_id),
  definition      TEXT NOT NULL,
  kind            TEXT NOT NULL CHECK (kind IN ('single', 'unanimous', 'avg', 'majority', 'stack', 'stack_pool', 'exact')),
  members         TEXT NOT NULL,
  missing         TEXT NOT NULL DEFAULT '{}',
  version_sig     TEXT NOT NULL,
  inputs_digest   TEXT NOT NULL,
  status          TEXT NOT NULL CHECK (status IN ('pick', 'disagree', 'tie', 'no_majority', 'incomplete', 'below_threshold')),
  side            TEXT CHECK (side IN ('a', 'mid', 'b')),
  statistic       REAL,
  vector          TEXT NOT NULL DEFAULT 'null',
  votes           TEXT NOT NULL DEFAULT 'null',
  information_at  TEXT NOT NULL CHECK (information_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  tipoff_at       TEXT NOT NULL,
  recorded_at     TEXT NOT NULL CHECK (recorded_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  CHECK ((status = 'pick') = (side IS NOT NULL)),
  CHECK ((status = 'pick') = (statistic IS NOT NULL)),
  CHECK (information_at < tipoff_at AND information_at <= recorded_at),
  UNIQUE (registry, group_id, source_cohort_id, inputs_digest)
);
INSERT INTO agreement_observations_new (observation_id, registry, group_id, sport, game_id, market, line, line_source, cohort_id,
  source_cohort_id, definition, kind, members, missing, version_sig, inputs_digest, status, side, statistic, vector, votes,
  information_at, tipoff_at, recorded_at)
SELECT observation_id, registry, group_id, sport, game_id, market, line, line_source, cohort_id, source_cohort_id, definition,
  kind, members, missing, version_sig, inputs_digest, status, side, statistic, vector, votes, information_at, tipoff_at, recorded_at
FROM agreement_observations;
DROP TABLE agreement_observations;
ALTER TABLE agreement_observations_new RENAME TO agreement_observations;
CREATE INDEX agreement_observations_market ON agreement_observations(registry, market, group_id, game_id);
CREATE TRIGGER agreement_observations_no_update BEFORE UPDATE ON agreement_observations
BEGIN SELECT RAISE(ABORT, 'agreement_observations are append-only'); END;
CREATE TRIGGER agreement_observations_no_delete BEFORE DELETE ON agreement_observations
BEGIN SELECT RAISE(ABORT, 'agreement_observations are append-only'); END;
CREATE TRIGGER agreement_observations_guard BEFORE INSERT ON agreement_observations
WHEN NEW.sport IS NOT (SELECT sport FROM store_identity)
  OR NEW.recorded_at >= NEW.tipoff_at
  OR NEW.tipoff_at IS NOT (SELECT tipoff_at FROM games WHERE game_id = NEW.game_id)
  OR NOT EXISTS (SELECT 1 FROM research_cohorts c WHERE c.cohort_id = NEW.source_cohort_id AND c.game_id = NEW.game_id
                 AND c.market = NEW.market AND c.tipoff_at = NEW.tipoff_at AND c.line IS NEW.line
                 AND c.information_at <= NEW.information_at)
  OR NOT EXISTS (SELECT 1 FROM research_cohorts c WHERE c.cohort_id = NEW.cohort_id AND c.game_id = NEW.game_id
                 AND c.market = NEW.market AND c.line IS NEW.line)
BEGIN SELECT RAISE(ABORT, 'agreement observation must be recorded before the start from a cohort of the same game, market and line'); END;
