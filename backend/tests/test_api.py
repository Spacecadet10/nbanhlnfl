"""API contract smoke tests for milestone 1 (demo fixtures)."""
import math

import pytest
from fastapi.testclient import TestClient

from app import fixtures, metrics
from app.main import api_mode, app

app.dependency_overrides[api_mode] = lambda: "demo"  # this module tests the demo provider
client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "mode": "demo", "api_version": "0.3.0"}


def test_meta_lists_dates_and_teams():
    body = client.get("/api/meta").json()
    assert body["mode"] == "demo"
    assert body["default_date"] == "2026-10-30"
    assert "2026-10-25" not in body["available_dates"]
    assert body["default_date"] in body["available_dates"]
    assert len(body["teams"]) == 12


def test_games_for_default_date_are_scheduled_demo_games():
    body = client.get("/api/games", params={"date": "2026-10-30"}).json()
    assert body["mode"] == "demo"
    assert len(body["games"]) == fixtures.GAMES_PER_DAY
    for g in body["games"]:
        assert g["mode"] == "demo" and g["prediction"]["run_type"] == "demo"
        assert g["status"] == "scheduled" and g["outcome"] is None
        assert 0 <= g["prediction"]["home_win_prob"] <= 1
        assert g["prediction"]["generated_at"] < g["prediction"]["cutoff_at"] < g["tipoff"]
        assert "home_stats" not in g and "stats_as_of" not in g
    assert {s["freshness"] for s in body["sources"]} == {"fresh", "stale"}


def test_games_empty_date_and_invalid_date():
    assert client.get("/api/games", params={"date": "2026-10-25"}).json()["games"] == []
    assert client.get("/api/games", params={"date": "not-a-date"}).status_code == 422
    assert client.get("/api/games").status_code == 422


def test_some_games_have_no_market_quote():
    all_games = fixtures.games()
    assert any(g.market is None for g in all_games)
    assert any(g.market is not None for g in all_games)


def test_game_detail_and_404():
    game_id = client.get("/api/games", params={"date": "2026-10-22"}).json()["games"][0]["game_id"]
    body = client.get(f"/api/games/{game_id}").json()
    assert body["game_id"] == game_id
    assert body["status"] == "final" and body["outcome"] is not None
    assert body["projected_score_available"] is False
    assert {"wins", "last10_games", "points_for_avg", "rest_days"} <= body["home_stats"].keys()
    assert client.get("/api/games/does-not-exist").status_code == 404


def test_fixtures_are_deterministic_and_ids_unique():
    ids = [g.game_id for g in fixtures.games()]
    assert len(ids) == len(set(ids))
    fixtures.games.cache_clear()
    assert [g.model_dump() for g in fixtures.games()] == [g.model_dump() for g in fixtures.games()]
    assert [g.game_id for g in fixtures.games()] == ids


def test_history_filters():
    body = client.get("/api/predictions").json()
    assert body["mode"] == "demo" and body["total"] == len(fixtures.games())
    all_rows = body["predictions"]
    assert len(all_rows) == len(fixtures.games())
    tipoffs = [r["tipoff"] for r in all_rows]
    assert tipoffs == sorted(tipoffs, reverse=True)
    assert all(r["generated_at"] < r["cutoff_at"] < r["tipoff"] and r["run_type"] == "demo" for r in all_rows)

    rows = client.get("/api/predictions", params={"team": "bos"}).json()["predictions"]
    assert rows and all("BOS" in (r["home"]["abbr"], r["away"]["abbr"]) for r in rows)

    rows = client.get("/api/predictions", params={"date_from": "2026-10-22", "date_to": "2026-10-23"}).json()
    assert {r["game_date"] for r in rows["predictions"]} == {"2026-10-22", "2026-10-23"}

    assert client.get("/api/predictions", params={"team": "XXX"}).json()["predictions"] == []
    bad = client.get("/api/predictions", params={"date_from": "2026-10-23", "date_to": "2026-10-22"})
    assert bad.status_code == 422


def test_history_correct_flag_matches_outcome():
    for r in client.get("/api/predictions").json()["predictions"]:
        if r["outcome"] is None:
            assert r["correct"] is None
        else:
            assert r["correct"] == ((r["home_win_prob"] >= 0.5) == r["outcome"]["home_won"])


def test_performance_matches_fixtures():
    body = client.get("/api/performance").json()
    scored = [g for g in fixtures.games() if g.outcome is not None]
    assert body["mode"] == "demo" and body["run_type"] == "demo" and body["baselines"] == []
    assert body["sample_size"] == len(scored) > 0
    assert body["date_to"] < fixtures.DEFAULT_DATE.isoformat()
    probs = [g.prediction.home_win_prob for g in scored]
    ys = [g.outcome.home_won for g in scored]
    assert body["brier_score"] == pytest.approx(metrics.brier(probs, ys))
    assert sum(b["count"] for b in body["calibration"]) == len(scored)
    assert 0 < body["market"]["coverage"] < 1
    assert body["market"]["matched_games"] == sum(g.market is not None for g in scored)
    assert "demo" in body["note"].lower()


def test_metric_helpers():
    assert metrics.brier([1.0, 0.0], [True, False]) == 0
    assert metrics.brier([0.5], [True]) == 0.25
    assert metrics.log_loss([0.5], [False]) == pytest.approx(math.log(2))
    assert metrics.accuracy([0.6, 0.4], [True, True]) == 0.5
    assert metrics.brier([], []) is None
    bins = metrics.calibration([0.1, 0.9, 1.0], [False, True, True])
    assert [b.count for b in bins] == [1, 0, 0, 0, 2]
    fine = metrics.calibration([0.1, 0.45, 0.55, 0.9, 1.0], [False, True, False, True, True], metrics.API_CALIBRATION_BINS)
    assert [b.count for b in fine] == [0, 1, 0, 0, 1, 1, 0, 0, 0, 2] and fine[-1].upper == 1.0


def test_team_stats_are_internally_consistent():
    for g in fixtures.games():
        for s in (g.home_stats, g.away_stats):
            assert 0 <= s.last10_wins <= min(10, s.wins)
            assert 10 - s.last10_wins <= s.losses


def test_demo_history_supports_the_confidence_filter():
    from app.schemas import pick_confidence
    games = fixtures.games()
    body = client.get("/api/predictions", params={"min_confidence": 0.65}).json()
    expected = [g for g in games if pick_confidence(g.prediction.home_win_prob) >= 0.65 - 1e-9]
    assert body["mode"] == "demo" and body["total"] == len(expected) and 0 < len(expected) < len(games)
    assert all(r["confidence"] >= 0.65 - 1e-9 and r["pick"] in ("home", "away") for r in body["predictions"])
    assert {r["pick"] for r in client.get("/api/predictions").json()["predictions"]} == {"home", "away"}
    settled = [g for g in expected if g.outcome is not None]
    wins = sum((g.prediction.home_win_prob >= 0.5) == g.outcome.home_won for g in settled)
    assert body["record"] == {"min_confidence": 0.65, "selected": len(expected), "settled": len(settled), "wins": wins,
                              "losses": len(settled) - wins, "pending": len(expected) - len(settled), "void": 0,
                              "unscorable": 0, "win_rate": pytest.approx(wins / len(settled)) if settled else None}


def test_demo_slate_selection_counts_match_the_picks():
    day = fixtures.DEFAULT_DATE.isoformat()
    body = client.get("/api/games", params={"date": day, "min_confidence": 0.65}).json()
    picks = [g["slate_pick"] for g in body["games"]]
    assert body["selection"] == {"market": "winner", "policy": "pregame", "min_confidence": 0.65, "games": len(picks),
                                 "with_forecast": sum(p is not None for p in picks),
                                 "selected": sum(bool(p and p["meets_confidence"]) for p in picks)}
    near = client.get("/api/games", params={"date": day, "policy": "near_tipoff"}).json()
    assert near["selection"]["with_forecast"] == 0 and all(g["slate_pick"] is None for g in near["games"])
