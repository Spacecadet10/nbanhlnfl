-- National-team competitions for the international live forecasts (app/intl_live.py; docs/INTERNATIONAL_PROTOCOL.md).
-- Parent-table rebuild (db.PARENT_REBUILDS): 0034 fixed soccer_competitions to the seven club competitions with a CHECK;
-- the rebuilt table keeps those seven rows unchanged and adds the ESPN national-team competition codes. db.migrate
-- disables foreign keys outside this transaction, checks every reference before commit, then restores enforcement.
CREATE TABLE soccer_competitions_new (
  competition_id TEXT PRIMARY KEY CHECK (competition_id IN
    ('eng.1', 'esp.1', 'ger.1', 'ita.1', 'fra.1', 'uefa.champions', 'uefa.europa',
     'uefa.nations', 'concacaf.nations.league', 'fifa.friendly', 'caf.nations_qual', 'uefa.euroq', 'afc.cupq',
     'fifa.worldq.uefa', 'fifa.worldq.conmebol', 'fifa.worldq.concacaf', 'fifa.worldq.afc', 'fifa.worldq.caf',
     'fifa.worldq.ofc', 'uefa.euro', 'conmebol.america', 'caf.nations', 'afc.asian.cup', 'concacaf.gold', 'fifa.world')),
  label          TEXT NOT NULL
);
INSERT INTO soccer_competitions_new SELECT competition_id, label FROM soccer_competitions;
INSERT INTO soccer_competitions_new VALUES
  ('uefa.nations', 'UEFA Nations League'), ('concacaf.nations.league', 'CONCACAF Nations League'),
  ('fifa.friendly', 'International friendly'), ('caf.nations_qual', 'Africa Cup of Nations qualifying'),
  ('uefa.euroq', 'UEFA European Championship qualifying'), ('afc.cupq', 'AFC Asian Cup qualifying'),
  ('fifa.worldq.uefa', 'World Cup qualifying (UEFA)'), ('fifa.worldq.conmebol', 'World Cup qualifying (CONMEBOL)'),
  ('fifa.worldq.concacaf', 'World Cup qualifying (CONCACAF)'), ('fifa.worldq.afc', 'World Cup qualifying (AFC)'),
  ('fifa.worldq.caf', 'World Cup qualifying (CAF)'), ('fifa.worldq.ofc', 'World Cup qualifying (OFC)'),
  ('uefa.euro', 'UEFA European Championship'), ('conmebol.america', 'Copa América'),
  ('caf.nations', 'Africa Cup of Nations'), ('afc.asian.cup', 'AFC Asian Cup'), ('concacaf.gold', 'CONCACAF Gold Cup'),
  ('fifa.world', 'FIFA World Cup');
DROP TRIGGER soccer_competitions_no_update;
DROP TRIGGER soccer_competitions_no_delete;
PRAGMA legacy_alter_table = ON;
DROP TABLE soccer_competitions;
ALTER TABLE soccer_competitions_new RENAME TO soccer_competitions;
PRAGMA legacy_alter_table = OFF;
CREATE TRIGGER soccer_competitions_no_update BEFORE UPDATE ON soccer_competitions
BEGIN SELECT RAISE(ABORT, 'soccer_competitions are fixed'); END;
CREATE TRIGGER soccer_competitions_no_delete BEFORE DELETE ON soccer_competitions
BEGIN SELECT RAISE(ABORT, 'soccer_competitions are fixed'); END;
