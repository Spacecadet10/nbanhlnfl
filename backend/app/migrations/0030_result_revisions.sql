-- Traceable result corrections (settlement C). games keeps the CURRENT result (overwritten when a source corrects it);
-- this table keeps every final result a source delivered, in order, append-only, so a correction never erases the
-- earlier result and a forecast's grade under each result stays traceable. Forecast rows are never touched.
-- basis 'observed': recorded by ingest at the retrieval that delivered it. basis 'backfill': the result stored when this
-- table was created; any earlier versions of it were not recorded and are not reconstructed.
CREATE TABLE result_revisions (
  revision_id  INTEGER PRIMARY KEY,
  game_id      TEXT NOT NULL REFERENCES games(game_id),
  revision     INTEGER NOT NULL CHECK (revision >= 1),
  home_score   INTEGER,
  away_score   INTEGER,
  decision     TEXT CHECK (decision IS NULL OR decision IN ('REG', 'OT', 'SO')),
  observed_at  TEXT NOT NULL,
  raw_id       INTEGER REFERENCES raw_responses(raw_id),
  basis        TEXT NOT NULL CHECK (basis IN ('observed', 'backfill')),
  UNIQUE (game_id, revision)
);
CREATE TRIGGER result_revisions_no_update BEFORE UPDATE ON result_revisions
BEGIN SELECT RAISE(ABORT, 'result_revisions are append-only'); END;
CREATE TRIGGER result_revisions_no_delete BEFORE DELETE ON result_revisions
BEGIN SELECT RAISE(ABORT, 'result_revisions are append-only'); END;
INSERT INTO result_revisions (game_id, revision, home_score, away_score, decision, observed_at, raw_id, basis)
SELECT g.game_id, 1, g.home_score, g.away_score, d.decision,
       COALESCE(CASE WHEN g.result_availability_basis = 'observed_retrieval' THEN g.result_available_at END,
                g.result_first_seen_at, g.updated_at),
       g.last_raw_id, 'backfill'
FROM games g LEFT JOIN game_decisions d ON d.game_id = g.game_id
WHERE g.status = 'final';
