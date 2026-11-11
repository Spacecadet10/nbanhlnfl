-- Soccer goal and corner markets (docs/SOCCER_MARKETS_PLAN.md; app/soccer_markets*.py). All stores get the tables; only a
-- soccer store may write them (store-sport triggers), as with 0034/0037/0038. Every table is append-only.
--
-- 1. soccer_match_stats: the team corner counts ESPN delivered for a final match (scoreboard `wonCorners`), one row per
--    distinct pair of values, observed at the retrieval that delivered it. ESPN counts include extra time when extra time
--    was played; the 90-minute corner count of such a match is therefore unknown (soccer_matches.decided_in tells).
--    available_at (never earlier than the match result's): the first row of a match = the later of the retrieval itself
--    (a final seen within the observed window after kickoff) or kickoff + 4 h (first seen long after kickoff) and the
--    result's result_available_at; a later different count = the retrieval that delivered it (never backdated).
CREATE TABLE soccer_match_stats (
  stat_id        INTEGER PRIMARY KEY,
  match_id       TEXT NOT NULL REFERENCES soccer_matches(match_id),
  revision       INTEGER NOT NULL CHECK (revision >= 1),
  home_corners   INTEGER NOT NULL CHECK (home_corners >= 0),
  away_corners   INTEGER NOT NULL CHECK (away_corners >= 0),
  observed_at    TEXT NOT NULL CHECK (observed_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  available_at   TEXT NOT NULL CHECK (available_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  raw_id         INTEGER NOT NULL REFERENCES raw_responses(raw_id),
  parser_version TEXT NOT NULL,
  UNIQUE (match_id, revision)
);
CREATE INDEX soccer_match_stats_match ON soccer_match_stats(match_id, revision);
CREATE TRIGGER soccer_match_stats_store_sport BEFORE INSERT ON soccer_match_stats
WHEN (SELECT sport FROM store_identity) IS NOT 'soccer'
BEGIN SELECT RAISE(ABORT, 'soccer match stats belong only in a soccer store'); END;
CREATE TRIGGER soccer_match_stats_no_update BEFORE UPDATE ON soccer_match_stats
BEGIN SELECT RAISE(ABORT, 'soccer_match_stats is append-only'); END;
CREATE TRIGGER soccer_match_stats_no_delete BEFORE DELETE ON soccer_match_stats
BEGIN SELECT RAISE(ABORT, 'soccer_match_stats is append-only'); END;

-- 2. soccer_odds_quotes: bookmaker prices exactly as the ESPN scoreboard lists them for a scheduled match (provider as ESPN
--    names it; today DraftKings): the 3-way result (home/draw/away, 90 minutes), the match goals total (over/under) and the
--    goal handicap (each side's own line, e.g. home -0.5 / away +0.5). `current` = the scoreboard's latest ('close') price
--    while the match is scheduled; `open` = its opening price. A row is appended when a price or line changes, and a
--    withdrawn row (offered 0, no price) when a previously listed price disappears; the latest row per key is the state.
--    Retrieved before kickoff only. No other market is listed there: corners, both teams to score, team totals, double
--    chance and draw no bet have no bookmaker price in this source.
CREATE TABLE soccer_odds_quotes (
  quote_id       INTEGER PRIMARY KEY,
  match_id       TEXT NOT NULL REFERENCES soccer_matches(match_id),
  provider       TEXT NOT NULL,
  market         TEXT NOT NULL CHECK (market IN ('1x2', 'total_goals', 'handicap')),
  side           TEXT NOT NULL CHECK (side IN ('home', 'draw', 'away', 'over', 'under')),
  price_kind     TEXT NOT NULL CHECK (price_kind IN ('open', 'current')),
  offered        INTEGER NOT NULL CHECK (offered IN (0, 1)),
  line           REAL,
  american       INTEGER,
  decimal_odds   REAL,
  retrieved_at   TEXT NOT NULL CHECK (retrieved_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  raw_id         INTEGER NOT NULL REFERENCES raw_responses(raw_id),
  parser_version TEXT NOT NULL,
  UNIQUE (match_id, provider, market, side, price_kind, retrieved_at),
  CHECK ((offered = 1) = (american IS NOT NULL AND decimal_odds IS NOT NULL)),
  CHECK (decimal_odds IS NULL OR decimal_odds > 1),
  CHECK (offered = 0 OR (market = '1x2') = (line IS NULL)),
  CHECK (market <> '1x2' OR side IN ('home', 'draw', 'away')),
  CHECK (market <> 'total_goals' OR side IN ('over', 'under')),
  CHECK (market <> 'handicap' OR side IN ('home', 'away'))
);
CREATE INDEX soccer_odds_quotes_match ON soccer_odds_quotes(match_id, market, side, price_kind, retrieved_at);
CREATE TRIGGER soccer_odds_quotes_store_sport BEFORE INSERT ON soccer_odds_quotes
WHEN (SELECT sport FROM store_identity) IS NOT 'soccer'
BEGIN SELECT RAISE(ABORT, 'soccer odds belong only in a soccer store'); END;
CREATE TRIGGER soccer_odds_quotes_before_kickoff BEFORE INSERT ON soccer_odds_quotes
WHEN NEW.retrieved_at >= (SELECT kickoff_at FROM soccer_matches WHERE match_id = NEW.match_id)
BEGIN SELECT RAISE(ABORT, 'a soccer price is recorded only before kickoff'); END;
CREATE TRIGGER soccer_odds_quotes_no_update BEFORE UPDATE ON soccer_odds_quotes
BEGIN SELECT RAISE(ABORT, 'soccer_odds_quotes is append-only'); END;
CREATE TRIGGER soccer_odds_quotes_no_delete BEFORE DELETE ON soccer_odds_quotes
BEGIN SELECT RAISE(ABORT, 'soccer_odds_quotes is append-only'); END;

-- 3. soccer_market_forecasts: live goal / corner forecasts of a sealed, tested count model (app/soccer_markets_live.py).
--    One row per forecast version and family; the row stores the model's whole joint distribution of the 90-minute counts
--    (home x away, as a JSON grid `joint[h][a]` over 0..max_count, the last cell of each axis = that count or more), so
--    every market and line is an exact function of the stored row (app/soccer_markets.py) and never of later code state.
--    Same point in time and append-only rules as soccer_live_forecasts: generated now, at or before the 06:00 UTC cutoff on
--    the match date, before kickoff, for a scheduled match.
CREATE TABLE soccer_market_forecasts (
  forecast_id         INTEGER PRIMARY KEY,
  match_id            TEXT NOT NULL REFERENCES soccer_matches(match_id),
  competition_id      TEXT NOT NULL,
  family              TEXT NOT NULL CHECK (family IN ('goals', 'corners')),
  version             INTEGER NOT NULL CHECK (version >= 1),
  candidate_sha256    TEXT NOT NULL CHECK (length(candidate_sha256) = 64),
  model               TEXT NOT NULL,
  idempotency_key     TEXT NOT NULL UNIQUE,
  inputs_digest       TEXT NOT NULL,
  inputs_through      TEXT,
  source_retrieved_at TEXT NOT NULL,
  mean_home           REAL NOT NULL CHECK (mean_home > 0),
  mean_away           REAL NOT NULL CHECK (mean_away > 0),
  max_count           INTEGER NOT NULL CHECK (max_count >= 5),
  joint_json          TEXT NOT NULL,
  cutoff_at           TEXT NOT NULL CHECK (cutoff_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T06:00:00Z'),
  generated_at        TEXT NOT NULL CHECK (generated_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  UNIQUE (match_id, family, version),
  CHECK (generated_at <= cutoff_at),
  CHECK (source_retrieved_at <= generated_at),
  CHECK (inputs_through IS NULL OR inputs_through <= cutoff_at)
);
CREATE INDEX soccer_market_forecasts_match ON soccer_market_forecasts(match_id, family, version);
CREATE TRIGGER soccer_market_forecasts_store_sport BEFORE INSERT ON soccer_market_forecasts
WHEN (SELECT sport FROM store_identity) IS NOT 'soccer'
BEGIN SELECT RAISE(ABORT, 'soccer market forecasts belong only in a soccer store'); END;
CREATE TRIGGER soccer_market_forecasts_no_update BEFORE UPDATE ON soccer_market_forecasts
BEGIN SELECT RAISE(ABORT, 'soccer_market_forecasts is append-only'); END;
CREATE TRIGGER soccer_market_forecasts_no_delete BEFORE DELETE ON soccer_market_forecasts
BEGIN SELECT RAISE(ABORT, 'soccer_market_forecasts is append-only'); END;
CREATE TRIGGER soccer_market_forecasts_generated_now BEFORE INSERT ON soccer_market_forecasts
WHEN NEW.generated_at < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-10 minutes')
  OR NEW.generated_at > strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '+1 minute')
BEGIN SELECT RAISE(ABORT, 'generated_at must be the time the row is written (never backdated)'); END;
CREATE TRIGGER soccer_market_forecasts_before_kickoff BEFORE INSERT ON soccer_market_forecasts
WHEN NEW.cutoff_at > (SELECT kickoff_at FROM soccer_matches WHERE match_id = NEW.match_id)
  OR (SELECT status FROM soccer_matches WHERE match_id = NEW.match_id) <> 'scheduled'
BEGIN SELECT RAISE(ABORT, 'a market forecast is made before kickoff for a scheduled match'); END;

-- 4. soccer_market_checks: every live market run's completeness verdict per family, competition and match date (visible
--    refusals; a refused family records no forecast and never blocks another family or the 1X2 forecast).
CREATE TABLE soccer_market_checks (
  check_id        INTEGER PRIMARY KEY,
  checked_at      TEXT NOT NULL CHECK (checked_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z'),
  family          TEXT NOT NULL CHECK (family IN ('goals', 'corners')),
  competition_id  TEXT NOT NULL,
  match_date      TEXT NOT NULL,
  ok              INTEGER NOT NULL CHECK (ok IN (0, 1)),
  problems_json   TEXT NOT NULL,
  forecasts       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX soccer_market_checks_recent ON soccer_market_checks(family, competition_id, checked_at);
CREATE TRIGGER soccer_market_checks_store_sport BEFORE INSERT ON soccer_market_checks
WHEN (SELECT sport FROM store_identity) IS NOT 'soccer'
BEGIN SELECT RAISE(ABORT, 'soccer market checks belong only in a soccer store'); END;
CREATE TRIGGER soccer_market_checks_no_update BEFORE UPDATE ON soccer_market_checks
BEGIN SELECT RAISE(ABORT, 'soccer_market_checks is append-only'); END;
CREATE TRIGGER soccer_market_checks_no_delete BEFORE DELETE ON soccer_market_checks
BEGIN SELECT RAISE(ABORT, 'soccer_market_checks is append-only'); END;
