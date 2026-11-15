-- Result integrity after the 2026-11-07 audit (all stores; soccer tables only matter in a soccer store).
-- 1. Replay identity: every game remembers the LAST RETRIEVAL that contained it (time + raw_fetches id), not only the last
--    one that changed it. A retrieval at or before that point is skipped as a whole (game, stats, decision, revision), so
--    replaying an already-imported log adds nothing, two different responses in the same second are ordered by fetch id,
--    and an older retrieval imported late cannot overwrite newer data. Existing rows are backfilled from recorded facts
--    (see below); nothing is invented.
-- 2. Revisions record the two teams, so a team change on a final with an unchanged score is a recorded correction.
--    Earlier revision rows keep NULL teams (not reconstructed).
-- 3. Soccer: decided_in may be 'unknown' when ESPN's status, period, clock and goal minutes disagree (decision_note says
--    why); tie facts (aggregate, qualifier, shoot-out) get their own availability time; malformed events are recorded in
--    an append-only rejection table instead of aborting the day. soccer_matches and soccer_result_revisions are rebuilt
--    (parent rebuild: db.migrate turns foreign keys off around this file and checks them before commit).
-- The backfill below looks up the retrieval log per game: index it (also used by replay and rebuild lookups by raw id).
CREATE INDEX IF NOT EXISTS raw_fetches_raw_time ON raw_fetches(raw_id, retrieved_at);
ALTER TABLE games ADD COLUMN last_seen_at TEXT CHECK (last_seen_at IS NULL OR last_seen_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z');
ALTER TABLE games ADD COLUMN last_seen_fetch_id INTEGER REFERENCES raw_fetches(fetch_id);
ALTER TABLE result_revisions ADD COLUMN home_team_id TEXT;
ALTER TABLE result_revisions ADD COLUMN away_team_id TEXT;
ALTER TABLE result_revisions ADD COLUMN fetch_id INTEGER;
-- Backfill from recorded facts only: the last retrieval known to have contained each game is the later of its last change
-- (updated_at) and its latest recorded result revision (NHL decision changes never moved updated_at); its fetch id is the
-- largest logged retrieval of the corresponding raw response at that time (NULL when the log has none). Retrievals that
-- changed nothing were never recorded, so this is a lower bound -- exactly the old ordering rule, without ties re-applied.
UPDATE games SET last_seen_at = MAX(updated_at, IFNULL((SELECT MAX(r.observed_at) FROM result_revisions r
                                                        WHERE r.game_id = games.game_id), ''));
UPDATE games SET last_seen_fetch_id = (
  SELECT MAX(f.fetch_id) FROM raw_fetches f
  WHERE f.retrieved_at = games.last_seen_at
    AND f.raw_id IN (games.last_raw_id, IFNULL((SELECT r.raw_id FROM result_revisions r WHERE r.game_id = games.game_id
                                                ORDER BY r.revision DESC LIMIT 1), games.last_raw_id)));

CREATE TABLE soccer_matches_new (
  match_id          TEXT PRIMARY KEY,
  competition_id    TEXT NOT NULL REFERENCES soccer_competitions(competition_id),
  event_id          TEXT NOT NULL CHECK (event_id GLOB '[0-9]*' AND event_id NOT GLOB '*[^0-9]*'),
  season            INTEGER NOT NULL,
  season_type_id    INTEGER NOT NULL,
  stage             TEXT,
  stage_label       TEXT,
  kickoff_at        TEXT NOT NULL CHECK (kickoff_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  home_team_id      TEXT NOT NULL REFERENCES teams(team_id),
  away_team_id      TEXT NOT NULL REFERENCES teams(team_id),
  neutral_site      INTEGER CHECK (neutral_site IS NULL OR neutral_site IN (0, 1)),
  venue             TEXT,
  status            TEXT NOT NULL CHECK (status IN ('scheduled', 'in_progress', 'final', 'postponed', 'canceled',
                                                    'abandoned', 'suspended', 'other')),
  source_status     TEXT NOT NULL,
  decided_in        TEXT CHECK (decided_in IS NULL OR decided_in IN ('regulation', 'extra_time', 'penalties', 'unknown')),
  decision_note     TEXT,
  home_score        INTEGER CHECK (home_score IS NULL OR home_score >= 0),
  away_score        INTEGER CHECK (away_score IS NULL OR away_score >= 0),
  home_shootout     INTEGER CHECK (home_shootout IS NULL OR home_shootout >= 0),
  away_shootout     INTEGER CHECK (away_shootout IS NULL OR away_shootout >= 0),
  leg               INTEGER CHECK (leg IS NULL OR leg >= 1),
  aggregate_home    INTEGER CHECK (aggregate_home IS NULL OR aggregate_home >= 0),
  aggregate_away    INTEGER CHECK (aggregate_away IS NULL OR aggregate_away >= 0),
  qualified_team_id TEXT REFERENCES teams(team_id),
  note              TEXT,
  first_seen_at        TEXT NOT NULL,
  result_first_seen_at TEXT,
  result_available_at  TEXT CHECK (result_available_at IS NULL OR result_available_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  result_availability_basis TEXT CHECK (result_availability_basis IN ('assumed_tipoff_plus_4h', 'observed_retrieval')),
  tie_facts_available_at TEXT CHECK (tie_facts_available_at IS NULL OR tie_facts_available_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  last_seen_at      TEXT CHECK (last_seen_at IS NULL OR last_seen_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  last_seen_fetch_id INTEGER REFERENCES raw_fetches(fetch_id),
  last_raw_id       INTEGER NOT NULL REFERENCES raw_responses(raw_id),
  updated_at        TEXT NOT NULL,
  UNIQUE (competition_id, event_id),
  CHECK (match_id = 'soccer:' || competition_id || ':' || event_id),
  CHECK (home_team_id <> away_team_id),
  CHECK ((status = 'final') = (home_score IS NOT NULL AND away_score IS NOT NULL AND decided_in IS NOT NULL
                               AND result_available_at IS NOT NULL)),
  CHECK ((home_shootout IS NULL) = (away_shootout IS NULL)),
  CHECK (home_shootout IS NULL OR decided_in = 'penalties'),
  CHECK (home_shootout IS NULL OR home_shootout <> away_shootout),
  CHECK ((decision_note IS NULL) = (COALESCE(decided_in, '') <> 'unknown')),
  CHECK ((aggregate_home IS NULL) = (aggregate_away IS NULL)),
  CHECK (qualified_team_id IS NULL OR qualified_team_id IN (home_team_id, away_team_id)),
  CHECK (tie_facts_available_at IS NULL OR status = 'final')
);
INSERT INTO soccer_matches_new (match_id, competition_id, event_id, season, season_type_id, stage, stage_label, kickoff_at,
  home_team_id, away_team_id, neutral_site, venue, status, source_status, decided_in, decision_note, home_score, away_score,
  home_shootout, away_shootout, leg, aggregate_home, aggregate_away, qualified_team_id, note, first_seen_at,
  result_first_seen_at, result_available_at, result_availability_basis, tie_facts_available_at, last_seen_at,
  last_seen_fetch_id, last_raw_id, updated_at)
SELECT match_id, competition_id, event_id, season, season_type_id, stage, stage_label, kickoff_at, home_team_id, away_team_id,
  neutral_site, venue, status, source_status, decided_in, NULL, home_score, away_score, home_shootout, away_shootout, leg,
  aggregate_home, aggregate_away, qualified_team_id, note, first_seen_at, result_first_seen_at, result_available_at,
  result_availability_basis,
  -- Tie facts stored before this migration: derived from the revision history exactly as a rebuild from the retrieval
  -- log would set them -- the observation of the latest revision whose tie facts differ from the previous revision's, or
  -- the result's availability when they arrived with the first result (revision 1). Revisions before 0035 recorded every
  -- change of these fields, so the history is complete for them.
  CASE WHEN status = 'final' AND (home_shootout IS NOT NULL OR aggregate_home IS NOT NULL OR qualified_team_id IS NOT NULL)
       THEN COALESCE((
         SELECT CASE WHEN v.revision = 1 THEN NULL ELSE v.observed_at END FROM soccer_result_revisions v
         WHERE v.match_id = soccer_matches.match_id AND (v.revision = 1 OR NOT EXISTS (
           SELECT 1 FROM soccer_result_revisions p WHERE p.match_id = v.match_id AND p.revision = v.revision - 1
             AND p.home_shootout IS v.home_shootout AND p.away_shootout IS v.away_shootout
             AND p.aggregate_home IS v.aggregate_home AND p.aggregate_away IS v.aggregate_away
             AND p.qualified_team_id IS v.qualified_team_id))
         ORDER BY v.revision DESC LIMIT 1), result_available_at) END,
  -- last retrieval known to have contained the match (same backfill rule as games above)
  MAX(updated_at, IFNULL((SELECT MAX(v.observed_at) FROM soccer_result_revisions v WHERE v.match_id = soccer_matches.match_id), '')),
  (SELECT MAX(f.fetch_id) FROM raw_fetches f WHERE f.raw_id = soccer_matches.last_raw_id
     AND f.retrieved_at = MAX(soccer_matches.updated_at, IFNULL((SELECT MAX(v.observed_at) FROM soccer_result_revisions v
                                                                 WHERE v.match_id = soccer_matches.match_id), ''))),
  last_raw_id, updated_at
FROM soccer_matches;

CREATE TABLE soccer_result_revisions_new (
  revision_id    INTEGER PRIMARY KEY,
  match_id       TEXT NOT NULL REFERENCES soccer_matches(match_id),
  revision       INTEGER NOT NULL CHECK (revision >= 1),
  decided_in     TEXT NOT NULL CHECK (decided_in IN ('regulation', 'extra_time', 'penalties', 'unknown')),
  home_score     INTEGER NOT NULL,
  away_score     INTEGER NOT NULL,
  home_shootout  INTEGER,
  away_shootout  INTEGER,
  aggregate_home INTEGER,
  aggregate_away INTEGER,
  qualified_team_id TEXT,
  home_team_id   TEXT,
  away_team_id   TEXT,
  observed_at    TEXT NOT NULL CHECK (observed_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  raw_id         INTEGER NOT NULL REFERENCES raw_responses(raw_id),
  fetch_id       INTEGER,
  -- 'observed': a result the source delivered; 'reclassified': the same cached retrieval re-read by a newer importer
  -- (e.g. how the match ended), not new source information.
  basis          TEXT NOT NULL DEFAULT 'observed' CHECK (basis IN ('observed', 'reclassified')),
  UNIQUE (match_id, revision)
);
INSERT INTO soccer_result_revisions_new (revision_id, match_id, revision, decided_in, home_score, away_score, home_shootout,
  away_shootout, aggregate_home, aggregate_away, qualified_team_id, home_team_id, away_team_id, observed_at, raw_id, fetch_id,
  basis)
SELECT revision_id, match_id, revision, decided_in, home_score, away_score, home_shootout, away_shootout, aggregate_home,
  aggregate_away, qualified_team_id, NULL, NULL, observed_at, raw_id, NULL, 'observed'
FROM soccer_result_revisions;

PRAGMA legacy_alter_table = ON;
DROP TABLE soccer_result_revisions;
DROP TABLE soccer_matches;
ALTER TABLE soccer_matches_new RENAME TO soccer_matches;
ALTER TABLE soccer_result_revisions_new RENAME TO soccer_result_revisions;
PRAGMA legacy_alter_table = OFF;

CREATE INDEX soccer_matches_kickoff ON soccer_matches(competition_id, kickoff_at);
CREATE TRIGGER soccer_matches_store_sport BEFORE INSERT ON soccer_matches
WHEN (SELECT sport FROM store_identity) IS NOT 'soccer'
BEGIN SELECT RAISE(ABORT, 'soccer matches belong only in a soccer store'); END;
CREATE TRIGGER soccer_matches_no_delete BEFORE DELETE ON soccer_matches
BEGIN SELECT RAISE(ABORT, 'soccer_matches are never deleted'); END;
CREATE TRIGGER soccer_matches_identity_fixed BEFORE UPDATE OF match_id, competition_id, event_id ON soccer_matches
BEGIN SELECT RAISE(ABORT, 'a soccer match keeps its competition and event id'); END;
CREATE TRIGGER soccer_result_revisions_no_update BEFORE UPDATE ON soccer_result_revisions
BEGIN SELECT RAISE(ABORT, 'soccer_result_revisions are append-only'); END;
CREATE TRIGGER soccer_result_revisions_no_delete BEFORE DELETE ON soccer_result_revisions
BEGIN SELECT RAISE(ABORT, 'soccer_result_revisions are append-only'); END;
CREATE TRIGGER soccer_result_revisions_no_conflicting_insert BEFORE INSERT ON soccer_result_revisions
WHEN EXISTS (SELECT 1 FROM soccer_result_revisions
             WHERE revision_id = NEW.revision_id OR (match_id = NEW.match_id AND revision = NEW.revision))
BEGIN SELECT RAISE(ABORT, 'soccer_result_revisions are append-only: conflicting insert rejected'); END;

-- Events the soccer importer refused (e.g. home and away the same team), one row per retrieval that delivered them.
CREATE TABLE soccer_rejections (
  rejection_id   INTEGER PRIMARY KEY,
  competition_id TEXT NOT NULL REFERENCES soccer_competitions(competition_id),
  event_id       TEXT NOT NULL,
  reason         TEXT NOT NULL,
  raw_id         INTEGER NOT NULL REFERENCES raw_responses(raw_id),
  retrieved_at   TEXT NOT NULL CHECK (retrieved_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  UNIQUE (competition_id, event_id, raw_id, retrieved_at)
);
CREATE TRIGGER soccer_rejections_store_sport BEFORE INSERT ON soccer_rejections
WHEN (SELECT sport FROM store_identity) IS NOT 'soccer'
BEGIN SELECT RAISE(ABORT, 'soccer rejections belong only in a soccer store'); END;
CREATE TRIGGER soccer_rejections_no_update BEFORE UPDATE ON soccer_rejections
BEGIN SELECT RAISE(ABORT, 'soccer_rejections are append-only'); END;
CREATE TRIGGER soccer_rejections_no_delete BEFORE DELETE ON soccer_rejections
BEGIN SELECT RAISE(ABORT, 'soccer_rejections are append-only'); END;
