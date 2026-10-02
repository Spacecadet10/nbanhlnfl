"""Daily training check: counts eligible results settled after each fit's cutoff (preseason excluded), never refits."""
from app import training_monitor
from test_candidate_pool import upcoming, _record, GID  # noqa: F401
from test_live_markets import sealed  # noqa: F401
from test_nhl_eval import nhl_store  # noqa: F401


def test_new_settled_results_since_each_fit_are_reported_without_refitting(upcoming):
    _record(upcoming, lines={(GID, "spread"): {"line": -1.5, "source": "core_cohort", "ref": "cohort:1"}})
    before = upcoming.execute("SELECT count(*) FROM model_fits").fetchone()[0]
    through = upcoming.execute("SELECT max(results_available_through) FROM model_fits").fetchone()[0]
    upcoming.execute("UPDATE games SET status='final', home_score=3, away_score=2, result_available_at=? WHERE game_id=?",
                     ("2026-10-13T03:00:00Z", GID))
    upcoming.commit()
    out = training_monitor.status(upcoming, "2026-10-13T12:00:00Z")
    assert out["fits"] and all(f["results_available_through"] <= through for f in out["fits"])
    assert all(f["new_settled_results_since_fit"] == 1 for f in out["fits"])
    upcoming.execute("UPDATE games SET season_type='preseason' WHERE game_id=?", (GID,))
    upcoming.commit()
    assert all(f["new_settled_results_since_fit"] == 0 for f in training_monitor.status(upcoming, "2026-10-13T12:00:00Z")["fits"])
    assert upcoming.execute("SELECT count(*) FROM model_fits").fetchone()[0] == before
