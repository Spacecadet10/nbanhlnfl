-- New two-hour policy only. Previously recorded forecasts and backtests remain unchanged.
CREATE TRIGGER predictions_two_hour_window BEFORE INSERT ON predictions
WHEN NEW.run_type='live' AND NEW.feature_version LIKE '%two-hour%'
  AND (NEW.generated_at < strftime('%Y-%m-%dT%H:%M:%SZ', NEW.cutoff_at, '-2 hours')
       OR NEW.generated_at >= NEW.cutoff_at
       OR NEW.cutoff_at IS NOT (SELECT tipoff_at FROM games WHERE game_id=NEW.game_id))
BEGIN SELECT RAISE(ABORT, 'two-hour forecast must be within two hours and before cutoff and tipoff'); END;
CREATE TRIGGER markets_two_hour_window BEFORE INSERT ON market_forecasts
WHEN NEW.run_type='live' AND NEW.model_version LIKE '%-window=2h-policy=near_tipoff'
  AND (NEW.generated_at < strftime('%Y-%m-%dT%H:%M:%SZ', NEW.tipoff_at, '-2 hours')
       OR NEW.generated_at >= NEW.tipoff_at OR NEW.cutoff_at IS NOT NEW.tipoff_at)
BEGIN SELECT RAISE(ABORT, 'two-hour market forecast must be within two hours and before tipoff'); END;
