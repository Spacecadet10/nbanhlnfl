"""CatBoost challenger harness (docs/MODEL_SELECTION.md §10): identical inputs and chronological fits. CatBoost itself is the
optional research extra, so the learner is replaced by a recording stub here."""
from app import catboost_challenger as cc
from app.backtest import run
from app.models.gbt import GradientBoostedTrees
from test_backtest import synthetic_rows
from test_candidates import FROZEN, boxes


def test_recorded_features_are_the_members_own_pregame_vectors():
    rows = synthetic_rows(seasons=range(2020, 2024))
    rec = cc.Recorder(GradientBoostedTrees(FROZEN))
    plain = GradientBoostedTrees(FROZEN)
    a = {p["game_id"]: p["p_home"] for p in run(rec, rows, boxes(rows), deliver_until="9999")}
    b = {p["game_id"]: p["p_home"] for p in run(plain, rows, boxes(rows), deliver_until="9999")}
    assert a == b                                          # pass-through: the member's forecasts are unchanged
    assert sorted(x for _, x, _ in plain.examples) == sorted(rec.x.values())


def test_each_season_is_fitted_on_earlier_seasons_only(monkeypatch):
    calls = []

    def fake(kind, params, train_x, train_y, test_x):
        calls.append((len(train_x), len(test_x)))
        return [0.5] * len(test_x)
    monkeypatch.setattr(cc, "_fit_predict", fake)
    data = {f"g{s}-{i}": {"game_id": f"g{s}-{i}", "season": s, "cutoff": f"{s}-01-{i + 1:02d}T05:00:00Z", "x": [i], "y": i % 2 == 0}
            for s in (2020, 2021, 2022) for i in range(10)}
    out = cc.walk_catboost("winner", cc.GRID[0], data, (2021, 2022))
    assert calls == [(10, 10), (20, 10)] and len(out) == 20
    assert all(v["log_loss"] > 0 and v["_season"] in (2021, 2022) for v in out.values())


def test_search_budget_is_fixed():
    assert len(cc.GRID) == 8 and cc.FIXED["iterations"] == 500 and cc.FIXED["random_seed"] == 20260930
