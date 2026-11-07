-- European soccer data-only store (app/soccer.py): fixtures and results of seven ESPN soccer competitions in one store
-- claimed as 'soccer'. Only store_identity changes for existing stores: its row and three immutability triggers are
-- rebuilt as in 0032, with 'soccer' added. Every other table keeps its own sport checks; nothing soccer writes there.
-- Soccer results have their own tables: a match's regulation result, extra time, penalty shoot-out and two-legged
-- aggregate qualification are separate facts, and the NBA/NHL `games` columns cannot hold them.
PRAGMA legacy_alter_table = ON;
CREATE TABLE store_identity_new (
  sport      TEXT PRIMARY KEY CHECK (sport IN ('nba', 'nhl', 'nfl', 'soccer')),
  claimed_at TEXT NOT NULL
);
INSERT INTO store_identity_new SELECT sport, claimed_at FROM store_identity;
DROP TABLE store_identity;
ALTER TABLE store_identity_new RENAME TO store_identity;
PRAGMA legacy_alter_table = OFF;
CREATE TRIGGER store_identity_single BEFORE INSERT ON store_identity
WHEN EXISTS (SELECT 1 FROM store_identity)
BEGIN SELECT RAISE(ABORT, 'store_identity is set once per store'); END;
CREATE TRIGGER store_identity_no_update BEFORE UPDATE ON store_identity
BEGIN SELECT RAISE(ABORT, 'store_identity is immutable'); END;
CREATE TRIGGER store_identity_no_delete BEFORE DELETE ON store_identity
BEGIN SELECT RAISE(ABORT, 'store_identity is immutable'); END;

-- The seven competitions (ESPN league slugs). Fixed: a competition is never renamed into another one.
CREATE TABLE soccer_competitions (
  competition_id TEXT PRIMARY KEY CHECK (competition_id IN
    ('eng.1', 'esp.1', 'ger.1', 'ita.1', 'fra.1', 'uefa.champions', 'uefa.europa')),
  label          TEXT NOT NULL
);
INSERT INTO soccer_competitions VALUES
  ('eng.1', 'Premier League'), ('esp.1', 'La Liga'), ('ger.1', 'Bundesliga'), ('ita.1', 'Serie A'),
  ('fra.1', 'Ligue 1'), ('uefa.champions', 'UEFA Champions League'), ('uefa.europa', 'UEFA Europa League');
CREATE TRIGGER soccer_competitions_no_update BEFORE UPDATE ON soccer_competitions
BEGIN SELECT RAISE(ABORT, 'soccer_competitions are fixed'); END;
CREATE TRIGGER soccer_competitions_no_delete BEFORE DELETE ON soccer_competitions
BEGIN SELECT RAISE(ABORT, 'soccer_competitions are fixed'); END;

-- Current state of each match (updated from newer retrievals, like games); every final result it ever had is kept in
-- soccer_result_revisions. Teams use the shared `teams` table with 'soccer:<ESPN team id>' ids.
--   home_score/away_score  goals when play ended: after extra time if extra time was played; never shoot-out kicks
--   decided_in             'regulation' (90 minutes + stoppage), 'extra_time' or 'penalties' (shoot-out after extra time)
--   *_shootout             shoot-out kicks scored, only when decided_in = 'penalties' (NULL there if the source omits it)
--   leg / aggregate_* / qualified_team_id  two-legged ties as the source reports them (NULL when it reports none);
--                          the qualifier is a tie fact, never this match's winner
CREATE TABLE soccer_matches (
  match_id          TEXT PRIMARY KEY,
  competition_id    TEXT NOT NULL REFERENCES soccer_competitions(competition_id),
  event_id          TEXT NOT NULL CHECK (event_id GLOB '[0-9]*' AND event_id NOT GLOB '*[^0-9]*'),
  season            INTEGER NOT NULL,                    -- ESPN season year (2026 = 2026-27)
  season_type_id    INTEGER NOT NULL,                    -- ESPN's competition-specific numeric season type
  stage             TEXT,                                -- ESPN season slug of the event, e.g. 'league-phase', 'round-of-16'
  stage_label       TEXT,                                -- e.g. 'Round of 16' (series title), when the source has one
  kickoff_at        TEXT NOT NULL CHECK (kickoff_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  home_team_id      TEXT NOT NULL REFERENCES teams(team_id),
  away_team_id      TEXT NOT NULL REFERENCES teams(team_id),
  neutral_site      INTEGER CHECK (neutral_site IS NULL OR neutral_site IN (0, 1)),   -- NULL: the source does not say
  venue             TEXT,                                -- venue name and city as listed, when listed
  status            TEXT NOT NULL CHECK (status IN ('scheduled', 'in_progress', 'final', 'postponed', 'canceled',
                                                    'abandoned', 'suspended', 'other')),
  source_status     TEXT NOT NULL,                       -- ESPN status name as delivered, e.g. STATUS_FULL_TIME
  decided_in        TEXT CHECK (decided_in IS NULL OR decided_in IN ('regulation', 'extra_time', 'penalties')),
  home_score        INTEGER CHECK (home_score IS NULL OR home_score >= 0),
  away_score        INTEGER CHECK (away_score IS NULL OR away_score >= 0),
  home_shootout     INTEGER CHECK (home_shootout IS NULL OR home_shootout >= 0),
  away_shootout     INTEGER CHECK (away_shootout IS NULL OR away_shootout >= 0),
  leg               INTEGER CHECK (leg IS NULL OR leg >= 1),
  aggregate_home    INTEGER CHECK (aggregate_home IS NULL OR aggregate_home >= 0),
  aggregate_away    INTEGER CHECK (aggregate_away IS NULL OR aggregate_away >= 0),
  qualified_team_id TEXT REFERENCES teams(team_id),
  note              TEXT,                                -- the source's own result note, e.g. '... advance 4-2 on penalties'
  first_seen_at        TEXT NOT NULL,
  result_first_seen_at TEXT,
  result_available_at  TEXT CHECK (result_available_at IS NULL OR result_available_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  result_availability_basis TEXT CHECK (result_availability_basis IN ('assumed_tipoff_plus_4h', 'observed_retrieval')),
  last_raw_id       INTEGER NOT NULL REFERENCES raw_responses(raw_id),
  updated_at        TEXT NOT NULL,
  UNIQUE (competition_id, event_id),
  CHECK (match_id = 'soccer:' || competition_id || ':' || event_id),
  CHECK (home_team_id <> away_team_id),
  CHECK ((status = 'final') = (home_score IS NOT NULL AND away_score IS NOT NULL AND decided_in IS NOT NULL
                               AND result_available_at IS NOT NULL)),
  CHECK ((home_shootout IS NULL) = (away_shootout IS NULL)),
  CHECK (home_shootout IS NULL OR decided_in = 'penalties'),     -- penalties without a reported shoot-out score stay NULL
  CHECK (home_shootout IS NULL OR home_shootout <> away_shootout),
  CHECK ((aggregate_home IS NULL) = (aggregate_away IS NULL)),
  CHECK (qualified_team_id IS NULL OR qualified_team_id IN (home_team_id, away_team_id))
);
CREATE INDEX soccer_matches_kickoff ON soccer_matches(competition_id, kickoff_at);
CREATE TRIGGER soccer_matches_store_sport BEFORE INSERT ON soccer_matches
WHEN (SELECT sport FROM store_identity) IS NOT 'soccer'
BEGIN SELECT RAISE(ABORT, 'soccer matches belong only in a soccer store'); END;
CREATE TRIGGER soccer_matches_no_delete BEFORE DELETE ON soccer_matches
BEGIN SELECT RAISE(ABORT, 'soccer_matches are never deleted'); END;
CREATE TRIGGER soccer_matches_identity_fixed BEFORE UPDATE OF match_id, competition_id, event_id ON soccer_matches
BEGIN SELECT RAISE(ABORT, 'a soccer match keeps its competition and event id'); END;

-- Every final result a source delivered for a match, in order (first = revision 1, each correction the next).
CREATE TABLE soccer_result_revisions (
  revision_id    INTEGER PRIMARY KEY,
  match_id       TEXT NOT NULL REFERENCES soccer_matches(match_id),
  revision       INTEGER NOT NULL CHECK (revision >= 1),
  decided_in     TEXT NOT NULL CHECK (decided_in IN ('regulation', 'extra_time', 'penalties')),
  home_score     INTEGER NOT NULL,
  away_score     INTEGER NOT NULL,
  home_shootout  INTEGER,
  away_shootout  INTEGER,
  aggregate_home INTEGER,
  aggregate_away INTEGER,
  qualified_team_id TEXT,
  observed_at    TEXT NOT NULL CHECK (observed_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  raw_id         INTEGER NOT NULL REFERENCES raw_responses(raw_id),
  UNIQUE (match_id, revision)
);
CREATE TRIGGER soccer_result_revisions_no_update BEFORE UPDATE ON soccer_result_revisions
BEGIN SELECT RAISE(ABORT, 'soccer_result_revisions are append-only'); END;
CREATE TRIGGER soccer_result_revisions_no_delete BEFORE DELETE ON soccer_result_revisions
BEGIN SELECT RAISE(ABORT, 'soccer_result_revisions are append-only'); END;
CREATE TRIGGER soccer_result_revisions_no_conflicting_insert BEFORE INSERT ON soccer_result_revisions
WHEN EXISTS (SELECT 1 FROM soccer_result_revisions
             WHERE revision_id = NEW.revision_id OR (match_id = NEW.match_id AND revision = NEW.revision))
BEGIN SELECT RAISE(ABORT, 'soccer_result_revisions are append-only: conflicting insert rejected'); END;
