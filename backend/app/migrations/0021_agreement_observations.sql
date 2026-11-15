-- Persistent agreement tracking `tracking-v2` (docs/MODEL_SELECTION.md §18). Research only; append-only.
-- One row per registered group and research cohort: the group's classification of that cohort from the member forecasts
-- stored for it (core/expanded cohort members and the candidate forecasts made against that cohort's exact line).
-- Thresholds and confidence bands are NOT stored: they are derived from `statistic` when read, so one forecast is never
-- duplicated per threshold. Outcomes are not stored here either (games / game_decisions own them).
CREATE TABLE agreement_observations (
  observation_id  INTEGER PRIMARY KEY,
  registry        TEXT NOT NULL,                    -- sealed tracking registry sha256
  group_id        TEXT NOT NULL,
  sport           TEXT NOT NULL CHECK (sport IN ('nba', 'nhl')),
  game_id         TEXT NOT NULL REFERENCES games(game_id),
  market          TEXT NOT NULL CHECK (market IN ('winner', 'reg3', 'spread', 'total', 'team_total_home', 'team_total_away')),
  line            REAL,                             -- the cohort's exact line (never merged across lines)
  line_source     TEXT,
  cohort_id       INTEGER NOT NULL REFERENCES research_cohorts(cohort_id),   -- core cohort (candidate line reference)
  source_cohort_id INTEGER NOT NULL REFERENCES research_cohorts(cohort_id),  -- cohort that supplied the member vectors
  definition      TEXT NOT NULL,                    -- research definition of the source cohort
  kind            TEXT NOT NULL CHECK (kind IN ('single', 'unanimous', 'avg', 'majority', 'stack', 'stack_pool')),
  members         TEXT NOT NULL,                    -- [{name, version, ref, p, generated_at}] for the members present
  missing         TEXT NOT NULL DEFAULT '{}',       -- {name: reason}; a group with a missing member is 'incomplete'
  version_sig     TEXT NOT NULL,                    -- digest of the members' model versions (version filter)
  status          TEXT NOT NULL CHECK (status IN ('pick', 'disagree', 'tie', 'no_majority', 'incomplete')),
  side            TEXT CHECK (side IN ('a', 'mid', 'b')),
  statistic       REAL,                             -- the rule's confidence for `side` (semantics: registry `kinds`)
  vector          TEXT NOT NULL DEFAULT 'null',     -- group probability vector when the rule produces one (single/avg/stack)
  votes           TEXT NOT NULL DEFAULT 'null',     -- majority: {a, mid, b, abstain, n}
  information_at  TEXT NOT NULL CHECK (information_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  tipoff_at       TEXT NOT NULL,
  recorded_at     TEXT NOT NULL CHECK (recorded_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  CHECK ((status = 'pick') = (side IS NOT NULL)),
  CHECK ((status = 'pick') = (statistic IS NOT NULL)),
  CHECK (information_at < tipoff_at AND information_at <= recorded_at),
  UNIQUE (registry, group_id, source_cohort_id)
);
CREATE INDEX agreement_observations_market ON agreement_observations(registry, market, group_id, game_id);
CREATE TRIGGER agreement_observations_no_update BEFORE UPDATE ON agreement_observations
BEGIN SELECT RAISE(ABORT, 'agreement_observations are append-only'); END;
CREATE TRIGGER agreement_observations_no_delete BEFORE DELETE ON agreement_observations
BEGIN SELECT RAISE(ABORT, 'agreement_observations are append-only'); END;
-- Recorded before the start the source cohort was made for, against that game's current start, from a cohort of the same
-- game and market; every input (latest member forecast) precedes the start.
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
