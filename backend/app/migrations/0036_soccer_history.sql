-- Soccer history from openfootball (CC0), offline research only (app/soccer_history.py). Separate from the ESPN-based
-- soccer_matches: openfootball has names, not ids, local dates and times without a stated zone, and its own 90-minute /
-- extra-time notation. Nothing here is read by NBA/NHL/NFL code; other stores get the empty tables, as with 0034.
--
-- Provenance: every downloaded file is a raw_responses row (exact bytes, content hash, importer version) plus one
-- raw_fetches row per retrieval; soccer_source_files adds the pinned git commit and path of that retrieval.
CREATE TABLE soccer_source_files (
  fetch_id       INTEGER PRIMARY KEY REFERENCES raw_fetches(fetch_id),
  raw_id         INTEGER NOT NULL REFERENCES raw_responses(raw_id),
  repo           TEXT NOT NULL CHECK (repo IN ('football.json', 'champions-league')),
  commit_sha     TEXT NOT NULL CHECK (length(commit_sha) = 40 AND commit_sha NOT GLOB '*[^0-9a-f]*'),
  path           TEXT NOT NULL,
  kind           TEXT NOT NULL CHECK (kind IN ('commit', 'tree', 'results')),
  competition_id TEXT REFERENCES soccer_competitions(competition_id),
  season         INTEGER CHECK (season IS NULL OR season BETWEEN 1990 AND 2100),   -- start year: 2023 = 2023-24
  retrieved_at   TEXT NOT NULL,
  CHECK ((kind = 'results') = (competition_id IS NOT NULL AND season IS NOT NULL))
);
CREATE INDEX soccer_source_files_file ON soccer_source_files(competition_id, season, retrieved_at, fetch_id);
CREATE TRIGGER soccer_source_files_no_update BEFORE UPDATE ON soccer_source_files
BEGIN SELECT RAISE(ABORT, 'soccer_source_files is append-only'); END;
CREATE TRIGGER soccer_source_files_no_delete BEFORE DELETE ON soccer_source_files
BEGIN SELECT RAISE(ABORT, 'soccer_source_files is append-only'); END;

-- Clubs: one row per real club across competitions. The name -> club mapping is the versioned table in
-- app/soccer_clubs.py (explicit entries only); the map version that resolved each source name is kept.
CREATE TABLE soccer_clubs (
  club_id  TEXT PRIMARY KEY CHECK (club_id GLOB '[A-Z][A-Z][A-Z]:?*'),     -- '<country code>:<canonical name>'
  country  TEXT NOT NULL CHECK (length(country) = 3),
  name     TEXT NOT NULL,
  CHECK (club_id = country || ':' || name)
);
CREATE TABLE soccer_club_names (
  competition_id TEXT NOT NULL REFERENCES soccer_competitions(competition_id),
  source_name    TEXT NOT NULL,                 -- exactly as written in the source, incl. a '(XXX)' country suffix
  club_id        TEXT NOT NULL REFERENCES soccer_clubs(club_id),
  map_version    TEXT NOT NULL,
  first_seen_at  TEXT NOT NULL,
  PRIMARY KEY (competition_id, source_name, map_version)
);

-- Matches, current state per source identity. Identity = (competition, season, round, home name, away name) as written
-- in the source: a league pairing is unique per season, a UEFA pairing per round; a date change is a correction.
--   status                   'result_missing' = dated before the file's retrieval day but listed without a score;
--                            'awarded' = a result decided off the pitch (listed score is not a played result)
--   local_date / local_time  as listed; the zone is not stated by the source (local_time NULL = not listed);
--                            local_time_listed 0 = the txt line had no time and continues the time of the line above
--                            in the same date block (football.txt layout), 1 = written on the line itself
--   ft_* = the 90-minute score as the source gives it (after extra time: the "(90-min, HT)" part)
--   aet_* / pen_*            the after-extra-time score and shoot-out, when the source notes them
--   deciding                 1 = a match that could go to extra time (UEFA final, single-leg knockout, second leg)
--   grading                  whether the 90-minute 1X2 may be graded / used: 'gradable' or the exclusion reason
--   result_available_at      when the current result counts as known: the first result is ASSUMED known the next day
--                            06:00 UTC (the source publishes no time); a later different result (a correction) is
--                            known only from the retrieval that delivered it ('observed_correction'), and so is a
--                            first result that appears in a later retrieval of a match already listed without one
--                            ('observed_retrieval'); never before the day after the match
CREATE TABLE soccer_hist_matches (
  hist_match_id   TEXT PRIMARY KEY,
  competition_id  TEXT NOT NULL REFERENCES soccer_competitions(competition_id),
  season          INTEGER NOT NULL CHECK (season BETWEEN 1990 AND 2100),
  round           TEXT NOT NULL,
  stage           TEXT NOT NULL CHECK (stage IN ('league', 'group', 'league_phase', 'knockout')),
  home_name       TEXT NOT NULL,
  away_name       TEXT NOT NULL,
  home_club_id    TEXT NOT NULL REFERENCES soccer_clubs(club_id),
  away_club_id    TEXT NOT NULL REFERENCES soccer_clubs(club_id),
  local_date      TEXT NOT NULL CHECK (local_date GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'),
  local_time      TEXT CHECK (local_time IS NULL OR local_time GLOB '[0-2][0-9]:[0-5][0-9]'),
  local_time_listed INTEGER CHECK (local_time_listed IS NULL OR local_time_listed IN (0, 1)),
  status          TEXT NOT NULL CHECK (status IN ('final', 'scheduled', 'result_missing', 'cancelled', 'awarded')),
  ft_home         INTEGER CHECK (ft_home IS NULL OR ft_home >= 0),
  ft_away         INTEGER CHECK (ft_away IS NULL OR ft_away >= 0),
  ht_home         INTEGER CHECK (ht_home IS NULL OR ht_home >= 0),
  ht_away         INTEGER CHECK (ht_away IS NULL OR ht_away >= 0),
  aet_home        INTEGER CHECK (aet_home IS NULL OR aet_home >= 0),
  aet_away        INTEGER CHECK (aet_away IS NULL OR aet_away >= 0),
  pen_home        INTEGER CHECK (pen_home IS NULL OR pen_home >= 0),
  pen_away        INTEGER CHECK (pen_away IS NULL OR pen_away >= 0),
  deciding        INTEGER NOT NULL CHECK (deciding IN (0, 1)),
  grading         TEXT NOT NULL CHECK (grading IN ('gradable', 'not_final', 'cancelled', 'awarded', 'extra_time_noted',
                                                   'deciding_match_unverified')),
  result_available_at       TEXT CHECK (result_available_at IS NULL OR result_available_at GLOB
                                        '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  result_availability_basis TEXT CHECK (result_availability_basis IS NULL OR result_availability_basis IN
                                        ('assumed_next_day_0600Z', 'observed_retrieval', 'observed_correction')),
  in_latest_file  INTEGER NOT NULL CHECK (in_latest_file IN (0, 1)),   -- 0: a later file of its season no longer lists it
  first_seen_at   TEXT NOT NULL,
  last_seen_at    TEXT NOT NULL,
  last_seen_fetch_id INTEGER NOT NULL REFERENCES raw_fetches(fetch_id),
  last_raw_id     INTEGER NOT NULL REFERENCES raw_responses(raw_id),
  importer_version TEXT NOT NULL,
  map_version     TEXT NOT NULL,                 -- soccer_clubs.MAP_VERSION that resolved home/away_club_id
  UNIQUE (competition_id, season, round, home_name, away_name),
  CHECK (home_club_id <> away_club_id),
  CHECK ((status IN ('final', 'awarded')) = (ft_home IS NOT NULL AND ft_away IS NOT NULL)),
  CHECK ((aet_home IS NULL) = (aet_away IS NULL) AND (pen_home IS NULL) = (pen_away IS NULL)),
  CHECK (pen_home IS NULL OR pen_home <> pen_away),
  CHECK ((grading = 'gradable') = (status = 'final' AND aet_home IS NULL AND pen_home IS NULL AND deciding = 0)),
  CHECK ((result_available_at IS NULL) = (status NOT IN ('final', 'awarded'))),
  CHECK ((result_available_at IS NULL) = (result_availability_basis IS NULL)),
  CHECK ((local_time IS NULL) = (local_time_listed IS NULL)),
  -- never known before the day after the match (06:00 UTC), whatever the basis
  CHECK (result_available_at IS NULL OR result_available_at >= date(local_date, '+1 day') || 'T06:00:00Z')
);
CREATE INDEX soccer_hist_matches_date ON soccer_hist_matches(local_date, competition_id);
CREATE INDEX soccer_hist_matches_season ON soccer_hist_matches(competition_id, season);

-- Every distinct state a match was seen in (append-only). A changed date, score, status or extra-time note appends a
-- revision observed at the retrieval that delivered it; the same retrieval replayed appends nothing. `deciding` is
-- derived from the whole file (a second leg appearing makes the first leg non-deciding), so it is part of each revision.
CREATE TABLE soccer_hist_revisions (
  revision_id    INTEGER PRIMARY KEY,
  hist_match_id  TEXT NOT NULL REFERENCES soccer_hist_matches(hist_match_id),
  revision       INTEGER NOT NULL CHECK (revision >= 1),
  local_date     TEXT NOT NULL,
  local_time     TEXT,
  status         TEXT NOT NULL,
  ft_home INTEGER, ft_away INTEGER, ht_home INTEGER, ht_away INTEGER,
  aet_home INTEGER, aet_away INTEGER, pen_home INTEGER, pen_away INTEGER,
  deciding       INTEGER NOT NULL CHECK (deciding IN (0, 1)),
  in_file        INTEGER NOT NULL CHECK (in_file IN (0, 1)),
  result_available_at       TEXT,
  result_availability_basis TEXT,
  observed_at    TEXT NOT NULL,
  fetch_id       INTEGER NOT NULL REFERENCES raw_fetches(fetch_id),
  raw_id         INTEGER NOT NULL REFERENCES raw_responses(raw_id),
  UNIQUE (hist_match_id, revision),
  CHECK (result_available_at IS NULL OR result_available_at >= date(local_date, '+1 day') || 'T06:00:00Z')
);
CREATE TRIGGER soccer_hist_revisions_no_update BEFORE UPDATE ON soccer_hist_revisions
BEGIN SELECT RAISE(ABORT, 'soccer_hist_revisions is append-only'); END;
CREATE TRIGGER soccer_hist_revisions_no_delete BEFORE DELETE ON soccer_hist_revisions
BEGIN SELECT RAISE(ABORT, 'soccer_hist_revisions is append-only'); END;

-- Source lines or matches that were not imported, with the reason (unparsed line, unmapped name, same club twice,
-- duplicate identity within one file, count disagreeing with the file header). Append-only; one row per retrieval.
CREATE TABLE soccer_hist_rejections (
  rejection_id   INTEGER PRIMARY KEY,
  fetch_id       INTEGER NOT NULL REFERENCES raw_fetches(fetch_id),
  raw_id         INTEGER NOT NULL REFERENCES raw_responses(raw_id),
  competition_id TEXT NOT NULL REFERENCES soccer_competitions(competition_id),
  season         INTEGER NOT NULL,
  locator        TEXT NOT NULL,                 -- 'line 156' (txt) or 'matches[12]' (json) or 'file'
  reason         TEXT NOT NULL,
  detail         TEXT NOT NULL,
  UNIQUE (fetch_id, locator, reason)
);
CREATE TRIGGER soccer_hist_rejections_no_update BEFORE UPDATE ON soccer_hist_rejections
BEGIN SELECT RAISE(ABORT, 'soccer_hist_rejections is append-only'); END;
CREATE TRIGGER soccer_hist_rejections_no_delete BEFORE DELETE ON soccer_hist_rejections
BEGIN SELECT RAISE(ABORT, 'soccer_hist_rejections is append-only'); END;
