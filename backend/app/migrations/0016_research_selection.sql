-- Research cohorts, selector decisions and research selections (docs/MODEL_SELECTION.md §7). Append-only; existing rows unchanged.
-- Member forecasts stay in `predictions` (winner) and `market_forecasts` (other markets); a cohort references them.
CREATE TABLE research_cohorts (
  cohort_id       INTEGER PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  sport           TEXT NOT NULL CHECK (sport IN ('nba', 'nhl')),
  game_id         TEXT NOT NULL REFERENCES games(game_id),
  market          TEXT NOT NULL CHECK (market IN ('winner', 'reg3', 'spread', 'total', 'team_total_home', 'team_total_away')),
  line            REAL,
  line_source     TEXT CHECK (line_source IN ('market', 'model')),
  market_quote_id INTEGER REFERENCES market_quotes(quote_id),
  bookmaker       TEXT NOT NULL DEFAULT 'null',     -- de-vigged vector at this exact line, provider, retrieval time; or null
  definition      TEXT NOT NULL,                    -- research definition (member code, settings, library versions)
  primary_name    TEXT NOT NULL,
  information_at  TEXT NOT NULL CHECK (information_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  generated_at    TEXT NOT NULL CHECK (generated_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  tipoff_at       TEXT NOT NULL,                    -- scheduled start known when generated
  members         TEXT NOT NULL,                    -- [{name, ref, p}] (ref: table and id of the stored member forecast)
  missing         TEXT NOT NULL DEFAULT '{}',       -- {name: reason}
  average         TEXT NOT NULL DEFAULT 'null',
  stack           TEXT NOT NULL DEFAULT 'null',
  agreement       TEXT NOT NULL,
  CHECK (information_at <= generated_at),
  CHECK ((market IN ('winner', 'reg3')) = (line IS NULL)),
  CHECK ((line IS NULL) = (line_source IS NULL)),
  CHECK ((line_source = 'market') = (market_quote_id IS NOT NULL))
);
CREATE INDEX research_cohorts_game ON research_cohorts(game_id, market, generated_at);

CREATE TABLE selector_decisions (
  decision_id      INTEGER PRIMARY KEY,
  idempotency_key  TEXT NOT NULL UNIQUE,
  sport            TEXT NOT NULL CHECK (sport IN ('nba', 'nhl')),
  market           TEXT NOT NULL CHECK (market IN ('winner', 'reg3', 'spread', 'total', 'team_total_home', 'team_total_away')),
  selector_version TEXT NOT NULL,
  definition       TEXT NOT NULL,
  scheduled_for    TEXT NOT NULL,                   -- the scheduled decision time = evidence cutoff
  decided_at       TEXT NOT NULL,                   -- actual write time = start of effect
  effective_until  TEXT NOT NULL,                   -- next scheduled decision time
  status           TEXT NOT NULL CHECK (status IN ('active', 'abstain', 'insufficient_evidence')),
  rule             TEXT NOT NULL DEFAULT 'null',
  lower_bound      REAL,
  reason           TEXT NOT NULL,
  evidence         TEXT NOT NULL,
  CHECK (scheduled_for <= decided_at AND decided_at < effective_until),
  CHECK ((status = 'active') = (rule <> 'null')),
  UNIQUE (sport, market, selector_version, definition, scheduled_for)
);

CREATE TABLE research_selections (
  selection_id    INTEGER PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  cohort_id       INTEGER NOT NULL REFERENCES research_cohorts(cohort_id),
  decision_id     INTEGER REFERENCES selector_decisions(decision_id),
  sport           TEXT NOT NULL CHECK (sport IN ('nba', 'nhl')),
  game_id         TEXT NOT NULL REFERENCES games(game_id),
  market          TEXT NOT NULL,
  generated_at    TEXT NOT NULL,
  tipoff_at       TEXT NOT NULL,
  context         TEXT NOT NULL,                    -- the set of cohorts compared for this game in one run
  status          TEXT NOT NULL CHECK (status IN ('pick', 'no_pick')),
  side            TEXT CHECK (side IN ('a', 'mid', 'b')),
  probability     REAL,
  threshold       REAL,
  reason          TEXT NOT NULL,
  recommended     INTEGER NOT NULL CHECK (recommended IN (0, 1)),
  game_reason     TEXT NOT NULL,
  CHECK ((status = 'pick') = (side IS NOT NULL)),
  CHECK (recommended = 0 OR status = 'pick')
);
CREATE INDEX research_selections_game ON research_selections(game_id, generated_at);

-- Append-only.
CREATE TRIGGER research_cohorts_no_update BEFORE UPDATE ON research_cohorts BEGIN SELECT RAISE(ABORT, 'research_cohorts are append-only'); END;
CREATE TRIGGER research_cohorts_no_delete BEFORE DELETE ON research_cohorts BEGIN SELECT RAISE(ABORT, 'research_cohorts are append-only'); END;
CREATE TRIGGER selector_decisions_no_update BEFORE UPDATE ON selector_decisions BEGIN SELECT RAISE(ABORT, 'selector_decisions are append-only'); END;
CREATE TRIGGER selector_decisions_no_delete BEFORE DELETE ON selector_decisions BEGIN SELECT RAISE(ABORT, 'selector_decisions are append-only'); END;
CREATE TRIGGER research_selections_no_update BEFORE UPDATE ON research_selections BEGIN SELECT RAISE(ABORT, 'research_selections are append-only'); END;
CREATE TRIGGER research_selections_no_delete BEFORE DELETE ON research_selections BEGIN SELECT RAISE(ABORT, 'research_selections are append-only'); END;

-- Same sport as the store; the two-hour production window against the game's CURRENT scheduled start.
CREATE TRIGGER research_cohorts_guard BEFORE INSERT ON research_cohorts
WHEN NEW.sport IS NOT (SELECT sport FROM store_identity)
  OR NEW.tipoff_at IS NOT (SELECT tipoff_at FROM games WHERE game_id = NEW.game_id)
  OR (SELECT status FROM games WHERE game_id = NEW.game_id) IS NOT 'scheduled'
  OR NEW.information_at < strftime('%Y-%m-%dT%H:%M:%SZ', NEW.tipoff_at, '-2 hours')
  OR NEW.generated_at >= NEW.tipoff_at
BEGIN SELECT RAISE(ABORT, 'research cohort must be made within two hours before the current scheduled start'); END;
CREATE TRIGGER research_cohorts_quote_matches BEFORE INSERT ON research_cohorts
WHEN NEW.market_quote_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM market_quotes q WHERE q.quote_id = NEW.market_quote_id AND q.game_id = NEW.game_id
    AND q.market = NEW.market AND q.line = NEW.line AND q.retrieved_at <= NEW.information_at)
BEGIN SELECT RAISE(ABORT, 'research cohort quote does not match its game, market, line or time'); END;
CREATE TRIGGER selector_decisions_guard BEFORE INSERT ON selector_decisions
WHEN NEW.sport IS NOT (SELECT sport FROM store_identity)
BEGIN SELECT RAISE(ABORT, 'selector decision sport does not match this store'); END;
-- A selection uses its own cohort and only a decision already in force when the selection was made.
CREATE TRIGGER research_selections_guard BEFORE INSERT ON research_selections
WHEN NEW.sport IS NOT (SELECT sport FROM store_identity)
  OR NOT EXISTS (SELECT 1 FROM research_cohorts c WHERE c.cohort_id = NEW.cohort_id AND c.game_id = NEW.game_id
                 AND c.market = NEW.market AND c.generated_at <= NEW.generated_at)
  OR NEW.tipoff_at IS NOT (SELECT tipoff_at FROM games WHERE game_id = NEW.game_id)
  OR NEW.generated_at >= NEW.tipoff_at
  OR NEW.generated_at < strftime('%Y-%m-%dT%H:%M:%SZ', NEW.tipoff_at, '-2 hours')
  OR (NEW.decision_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM selector_decisions d WHERE d.decision_id = NEW.decision_id AND d.market = NEW.market
          AND d.decided_at <= NEW.generated_at AND NEW.generated_at < d.effective_until))
BEGIN SELECT RAISE(ABORT, 'research selection must use its cohort and a decision in force, within two hours before the start'); END;
