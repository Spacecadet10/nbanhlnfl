-- Preserve existing forecast rows while allowing a kickoff deadline for the two-hour live window.
ALTER TABLE soccer_live_forecasts RENAME TO soccer_live_forecasts_old;
CREATE TABLE soccer_live_forecasts (
  forecast_id         INTEGER PRIMARY KEY,
  match_id            TEXT NOT NULL REFERENCES soccer_matches(match_id),
  competition_id      TEXT NOT NULL,
  market              TEXT NOT NULL CHECK (market = '1x2_regulation'),
  version             INTEGER NOT NULL CHECK (version >= 1),
  candidate_sha256    TEXT NOT NULL CHECK (length(candidate_sha256) = 64),
  model               TEXT NOT NULL,
  idempotency_key     TEXT NOT NULL UNIQUE,
  inputs_digest       TEXT NOT NULL,
  inputs_through      TEXT,                -- latest result availability time among the inputs (NULL: no current-season result)
  source_retrieved_at TEXT NOT NULL,       -- latest ESPN retrieval of this competition before generation (freshness)
  p_home              REAL NOT NULL CHECK (p_home > 0 AND p_home < 1),
  p_draw              REAL NOT NULL CHECK (p_draw > 0 AND p_draw < 1),
  p_away              REAL NOT NULL CHECK (p_away > 0 AND p_away < 1),
  cutoff_at           TEXT NOT NULL CHECK (cutoff_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  generated_at        TEXT NOT NULL CHECK (generated_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  UNIQUE (match_id, market, version),
  CHECK (abs(p_home + p_draw + p_away - 1.0) <= 1e-9),
  CHECK (generated_at <= cutoff_at),
  CHECK (source_retrieved_at <= generated_at),
  CHECK (inputs_through IS NULL OR inputs_through <= cutoff_at)
);
INSERT INTO soccer_live_forecasts SELECT * FROM soccer_live_forecasts_old;
DROP TABLE soccer_live_forecasts_old;

CREATE INDEX soccer_live_forecasts_match ON soccer_live_forecasts(match_id, version);
CREATE TRIGGER soccer_live_forecasts_no_update BEFORE UPDATE ON soccer_live_forecasts
BEGIN SELECT RAISE(ABORT, 'soccer_live_forecasts is append-only'); END;
CREATE TRIGGER soccer_live_forecasts_no_delete BEFORE DELETE ON soccer_live_forecasts
BEGIN SELECT RAISE(ABORT, 'soccer_live_forecasts is append-only'); END;
-- Generated now (database clock), never backdated, and only before the match starts.
CREATE TRIGGER soccer_live_forecasts_generated_now BEFORE INSERT ON soccer_live_forecasts
WHEN NEW.generated_at < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-10 minutes')
  OR NEW.generated_at > strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '+1 minute')
BEGIN SELECT RAISE(ABORT, 'generated_at must be the time the row is written (never backdated)'); END;
CREATE TRIGGER soccer_live_forecasts_before_kickoff BEFORE INSERT ON soccer_live_forecasts
WHEN NEW.cutoff_at > (SELECT kickoff_at FROM soccer_matches WHERE match_id = NEW.match_id)
  OR NEW.generated_at >= (SELECT kickoff_at FROM soccer_matches WHERE match_id = NEW.match_id)
  OR (NEW.cutoff_at = (SELECT kickoff_at FROM soccer_matches WHERE match_id = NEW.match_id)
      AND NEW.generated_at < strftime('%Y-%m-%dT%H:%M:%SZ', NEW.cutoff_at, '-2 hours'))
  OR (substr(NEW.cutoff_at, 12) <> '06:00:00Z'
      AND NEW.cutoff_at <> (SELECT kickoff_at FROM soccer_matches WHERE match_id = NEW.match_id))
  OR (SELECT status FROM soccer_matches WHERE match_id = NEW.match_id) <> 'scheduled'
BEGIN SELECT RAISE(ABORT, 'a live forecast is made before kickoff for a scheduled match'); END;
