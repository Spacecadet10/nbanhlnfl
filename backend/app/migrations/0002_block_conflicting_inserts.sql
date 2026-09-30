-- 0001's UPDATE/DELETE triggers do not stop INSERT OR REPLACE: with recursive_triggers off (SQLite's default),
-- the conflicting row is deleted without firing its DELETE trigger. These BEFORE INSERT triggers reject any insert
-- that would collide with an existing primary or unique key, whatever the conflict clause (REPLACE, IGNORE, ABORT).
-- Application retries check for the existing row first and never reach these triggers.

CREATE TRIGGER predictions_no_conflicting_insert BEFORE INSERT ON predictions
WHEN EXISTS (SELECT 1 FROM predictions
             WHERE prediction_id = NEW.prediction_id OR idempotency_key = NEW.idempotency_key)
BEGIN SELECT RAISE(ABORT, 'predictions are append-only: conflicting insert rejected'); END;

CREATE TRIGGER raw_responses_no_conflicting_insert BEFORE INSERT ON raw_responses
WHEN EXISTS (SELECT 1 FROM raw_responses
             WHERE raw_id = NEW.raw_id
                OR (source = NEW.source AND request_key = NEW.request_key AND content_hash = NEW.content_hash))
BEGIN SELECT RAISE(ABORT, 'raw_responses are immutable: conflicting insert rejected'); END;

CREATE TRIGGER odds_quotes_no_conflicting_insert BEFORE INSERT ON odds_quotes
WHEN EXISTS (SELECT 1 FROM odds_quotes
             WHERE quote_id = NEW.quote_id
                OR (game_id = NEW.game_id AND provider = NEW.provider AND market = NEW.market
                    AND price_label = NEW.price_label AND raw_id = NEW.raw_id))
BEGIN SELECT RAISE(ABORT, 'odds_quotes are append-only: conflicting insert rejected'); END;
