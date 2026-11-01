-- Lets a store be claimed by the NFL (data-only sport, docs/NFL_PLAN.md). Only store_identity changes: its one row and
-- its three immutability triggers are rebuilt as they were, with 'nfl' added to the allowed sports. Tables that carry a
-- `sport` column for markets and research keep allowing only 'nba' and 'nhl': nothing NFL writes there yet.
-- legacy_alter_table keeps the rename from re-checking other tables' triggers that read store_identity while it is
-- briefly absent (their text names the table, so they bind to the rebuilt one).
PRAGMA legacy_alter_table = ON;
CREATE TABLE store_identity_new (
  sport      TEXT PRIMARY KEY CHECK (sport IN ('nba', 'nhl', 'nfl')),
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
