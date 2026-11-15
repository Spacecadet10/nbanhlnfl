-- Review of 0012 (independent review, 2026-09-30):
-- * A quote used as a forecast's line must also have been retrieved by the forecast's cutoff, not only by its generation
--   time: backtest forecasts are generated long after their cutoff and must not see later lines.
-- * The forecast's stored tipoff_at (used by the 12-hour void rule) must be a timestamp, and for live forecasts must be
--   the game's scheduled start at the moment of insertion.
-- market_forecasts is append-only and checked by triggers only, so the triggers are replaced; no rows change.
DROP TRIGGER market_forecasts_quote_matches;
CREATE TRIGGER market_forecasts_quote_matches BEFORE INSERT ON market_forecasts
WHEN NEW.market_quote_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM market_quotes q WHERE q.quote_id = NEW.market_quote_id AND q.game_id = NEW.game_id
    AND q.market = NEW.market AND q.line = NEW.line
    AND q.retrieved_at <= NEW.generated_at AND q.retrieved_at <= NEW.cutoff_at)
BEGIN SELECT RAISE(ABORT, 'market forecast quote does not match its game, market, line or time'); END;
CREATE TRIGGER market_forecasts_tipoff_valid BEFORE INSERT ON market_forecasts
WHEN NEW.tipoff_at NOT GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'
  OR (NEW.run_type = 'live' AND NEW.tipoff_at IS NOT (SELECT tipoff_at FROM games WHERE game_id = NEW.game_id))
BEGIN SELECT RAISE(ABORT, 'market forecast tipoff_at must be the game''s scheduled start'); END;
