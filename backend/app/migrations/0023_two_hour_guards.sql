-- Audit 2026-10-03: the two-hour guard of `market_forecasts` (0015) compared only the row's own columns. It now matches
-- the cohort and candidate guards (0016, 0020): the forecast must be made for the game's CURRENT start, the game must
-- still be scheduled and the completion time well formed. Existing rows are unchanged; triggers act on inserts only.
DROP TRIGGER markets_two_hour_window;
CREATE TRIGGER markets_two_hour_window BEFORE INSERT ON market_forecasts
WHEN NEW.run_type='live' AND NEW.model_version LIKE '%-window=2h-policy=near_tipoff'
  AND (NEW.generated_at NOT GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'
       OR NEW.generated_at < strftime('%Y-%m-%dT%H:%M:%SZ', NEW.tipoff_at, '-2 hours')
       OR NEW.generated_at >= NEW.tipoff_at OR NEW.cutoff_at IS NOT NEW.tipoff_at
       OR NEW.tipoff_at IS NOT (SELECT tipoff_at FROM games WHERE game_id=NEW.game_id)
       OR (SELECT status FROM games WHERE game_id=NEW.game_id) IS NOT 'scheduled')
BEGIN SELECT RAISE(ABORT, 'two-hour market forecast must be within two hours before the current scheduled start of a scheduled game'); END;
