-- Fit register (docs/MODEL_SELECTION.md §13): one append-only row per distinct trained state of a learned research member, as
-- used by a live run. Identifies the trained artifact (digest of its fitted parameters), the training data (fingerprint of the
-- result ids it could use and the time through which results were available) and when it was first used. New outcomes create a
-- new fit row on the member's declared schedule; they never alter issued forecasts or earlier fit rows.
CREATE TABLE model_fits (
  fit_id        INTEGER PRIMARY KEY,
  sport         TEXT NOT NULL,
  member        TEXT NOT NULL,
  definition    TEXT NOT NULL,              -- research definition digest the member ran under
  schedule      TEXT NOT NULL,              -- 'season_start' or 'weekly'
  fit_kind      TEXT NOT NULL,              -- what triggered the fit in force
  trained_season INTEGER,
  training_games INTEGER NOT NULL,
  results_fingerprint TEXT NOT NULL,
  results_available_through TEXT NOT NULL,
  params_digest TEXT NOT NULL,
  libraries     TEXT NOT NULL,              -- JSON: scikit-learn, numpy, scipy versions
  first_used_at TEXT NOT NULL,              -- the run time it was first used (effective from)
  UNIQUE (sport, member, definition, params_digest, results_fingerprint)
);
CREATE INDEX model_fits_member ON model_fits(sport, member, first_used_at);
CREATE TRIGGER model_fits_no_update BEFORE UPDATE ON model_fits
BEGIN SELECT RAISE(ABORT, 'model_fits are append-only'); END;
CREATE TRIGGER model_fits_no_delete BEFORE DELETE ON model_fits
BEGIN SELECT RAISE(ABORT, 'model_fits are append-only'); END;
