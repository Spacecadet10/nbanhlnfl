"""NFL data-only store (docs/NFL_PLAN.md): coverage of the ESPN NFL schedule and results.

NFL is registered for storage but not served (sports.DATA_ONLY): no model, ledger rows, API or UI yet. Everything here
reads or writes only a store whose identity is 'nfl'.
"""
import sqlite3
from collections import defaultdict

from . import sports
from .db import StoreMismatch, store_sport


def _require_nfl(conn: sqlite3.Connection) -> None:
    current = store_sport(conn)
    if current != sports.NFL.id:
        raise StoreMismatch(f"This store holds {current or 'unlabelled'!r} data, not 'nfl'")


def coverage(conn: sqlite3.Connection) -> dict:
    """Per season and season type: games, final, scheduled, ties, neutral-site games and distinct teams; plus the number
    of final results in eligible season types (what a future model could learn from) and how their availability time
    was set (observed retrieval vs assumed kickoff + 4 h for historical backfill)."""
    _require_nfl(conn)
    seasons: dict = defaultdict(dict)
    rows = conn.execute(
        "SELECT season, season_type, COUNT(*) AS games, SUM(status = 'final') AS final, SUM(status = 'scheduled') AS scheduled, "
        "SUM(status = 'final' AND home_score = away_score) AS ties, SUM(neutral_site) AS neutral FROM games "
        "GROUP BY season, season_type ORDER BY season, season_type").fetchall()
    for r in rows:
        teams = conn.execute("SELECT COUNT(*) FROM (SELECT home_team_id AS t FROM games WHERE season=? AND season_type=? "
                             "UNION SELECT away_team_id FROM games WHERE season=? AND season_type=?)",
                             (r["season"], r["season_type"], r["season"], r["season_type"])).fetchone()[0]
        seasons[str(r["season"])][r["season_type"]] = {
            "games": r["games"], "final": r["final"] or 0, "scheduled": r["scheduled"] or 0, "ties": r["ties"] or 0,
            "neutral": r["neutral"] or 0, "teams": teams}
    types = sports.NFL.eligible_season_types
    marks = ", ".join("?" * len(types))
    eligible = conn.execute(f"SELECT COUNT(*) FROM games WHERE status = 'final' AND season_type IN ({marks})",
                            types).fetchone()[0]
    basis = {r[0]: r[1] for r in conn.execute(
        f"SELECT result_availability_basis, COUNT(*) FROM games WHERE status = 'final' AND season_type IN ({marks}) "
        "GROUP BY 1", types)}
    other = {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM games WHERE status NOT IN ('final', 'scheduled') "
                                              "GROUP BY 1")}
    return {"sport": "nfl", "seasons": dict(seasons), "eligible_final_results": eligible,
            "result_availability_basis": basis, "other_statuses": other,
            "raw_responses": conn.execute("SELECT COUNT(*) FROM raw_responses").fetchone()[0]}
