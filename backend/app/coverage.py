"""Coverage report over stored data. Reports what exists; never fills gaps."""
import sqlite3
from datetime import datetime

from .ingest import POSSESSION_LATEST_SQL as POSSESSION_LATEST


def report(conn: sqlite3.Connection) -> dict:
    seasons = [dict(r) for r in conn.execute(f"""
        WITH complete_possession AS (
            SELECT game_id FROM ({POSSESSION_LATEST}) p
            WHERE p.fga IS NOT NULL AND p.fta IS NOT NULL AND p.oreb IS NOT NULL AND p.tov IS NOT NULL
            GROUP BY game_id HAVING COUNT(*) = 2)
        SELECT g.season,
               SUM(g.season_type = 'regular')                        AS regular,
               SUM(g.season_type = 'play_in')                        AS play_in,
               SUM(g.season_type = 'postseason')                     AS postseason,
               SUM(g.season_type = 'preseason')                      AS preseason,
               SUM(g.season_type = 'other')                          AS other,
               SUM(g.status = 'final')                               AS final,
               SUM(g.status = 'scheduled')                           AS scheduled,
               SUM(g.status IN ('postponed', 'canceled'))            AS postponed_or_canceled,
               SUM(g.status = 'final' AND (SELECT COUNT(*) FROM team_game_stats s
                   WHERE s.game_id = g.game_id AND s.fga IS NOT NULL) = 2) AS final_with_box_stats,
               SUM(g.result_availability_basis = 'assumed_tipoff_plus_4h') AS results_assumed_availability,
               SUM(g.result_availability_basis = 'observed_retrieval')     AS results_observed_availability,
               SUM(EXISTS (SELECT 1 FROM odds_quotes o WHERE o.game_id = g.game_id)) AS games_with_odds,
               SUM(g.status = 'final' AND g.season_type IN ('regular', 'play_in', 'postseason')) AS final_competitive,
               SUM(g.status = 'final' AND g.season_type IN ('regular', 'play_in', 'postseason')
                   AND g.game_id IN (SELECT game_id FROM complete_possession))
                                                                     AS final_competitive_with_possession_stats,
               MIN(substr(g.tipoff_at, 1, 10)) AS first_date,
               MAX(substr(g.tipoff_at, 1, 10)) AS last_date
        FROM games g GROUP BY g.season ORDER BY g.season""")]
    totals = {
        "games": conn.execute("SELECT COUNT(*) FROM games").fetchone()[0],
        "teams": conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0],
        "team_game_stats": conn.execute("SELECT COUNT(*) FROM team_game_stats").fetchone()[0],
        "odds_quotes": conn.execute("SELECT COUNT(*) FROM odds_quotes").fetchone()[0],
        "team_possession_observations": conn.execute("SELECT COUNT(*) FROM team_possession_observations").fetchone()[0],
        "raw_responses": conn.execute("SELECT COUNT(*) FROM raw_responses").fetchone()[0],
        "raw_fetches": conn.execute("SELECT COUNT(*) FROM raw_fetches").fetchone()[0],
        "predictions": conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0],
    }
    return {"seasons": seasons, "totals": totals, "closing_lines": closing_summary(conn)}


NEAR_CLOSE_MINUTES = 60

# Per started game and provider: the last pregame 'current' moneyline confirmed (fetched) at or before tipoff.
# Uses raw_fetches, so an unchanged response re-fetched later still counts as a later confirmation of the price.
LAST_PREGAME_SQL = """
    SELECT game_id, tipoff_at, provider, home_american, away_american, home_prob_devig, confirmed_at FROM (
        SELECT g.game_id, g.tipoff_at, q.provider, q.home_american, q.away_american, q.home_prob_devig,
               f.retrieved_at AS confirmed_at,
               ROW_NUMBER() OVER (PARTITION BY g.game_id, q.provider
                                  ORDER BY f.retrieved_at DESC, q.quote_id DESC) AS rn
        FROM games g
        JOIN odds_quotes q ON q.game_id = g.game_id AND q.price_label = 'current'
        JOIN raw_fetches f ON f.raw_id = q.raw_id AND f.retrieved_at <= g.tipoff_at
        WHERE g.status IN ('final', 'in_progress') AND g.season_type IN ('regular', 'play_in', 'postseason'))
    WHERE rn = 1"""


def closing_summary(conn: sqlite3.Connection) -> dict:
    """Last pregame snapshot per started game. A snapshot taken long before tipoff is not a closing line, so the
    gap is reported and only snapshots within NEAR_CLOSE_MINUTES count as near-close."""
    started = conn.execute("SELECT COUNT(*) FROM games WHERE status IN ('final', 'in_progress') "
                           "AND season_type IN ('regular', 'play_in', 'postseason')").fetchone()[0]
    gaps = {}
    for r in conn.execute(LAST_PREGAME_SQL):
        mins = (datetime.fromisoformat(r["tipoff_at"].replace("Z", "+00:00"))
                - datetime.fromisoformat(r["confirmed_at"].replace("Z", "+00:00"))).total_seconds() / 60
        gaps[r["game_id"]] = min(mins, gaps.get(r["game_id"], mins))
    ordered = sorted(gaps.values())
    return {
        "started_competitive_games": started,
        "games_with_pregame_quote": len(gaps),
        f"games_with_quote_within_{NEAR_CLOSE_MINUTES}_min": sum(g <= NEAR_CLOSE_MINUTES for g in ordered),
        "median_minutes_before_tipoff": round(ordered[len(ordered) // 2], 1) if ordered else None,
        "note": "Last pregame snapshot (confirmed at or before tipoff), not a bookmaker-published closing line.",
    }
