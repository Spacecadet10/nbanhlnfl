"""Step 4: shadow-candidate models (efficiency margin, logistic v2), their leakage guards and the screening rule."""
import json
import math

import pytest

from app import backtest
from app.backtest import run
from app.models.efficiency import BoxEvent, BoxLine, EfficiencyMargin
from app.models.elo import EloConfig
from app.models.logistic import V2_FEATURES, V2_SCALED, LogisticV2, Standardizer, fit_logistic
from test_backtest import _db_with_games, flip, rows_until, synthetic_rows

FROZEN = EloConfig(k=15, home_advantage=50, carry=0.75, mov=True)
FACTORIES = [EfficiencyMargin, lambda: LogisticV2(FROZEN)]


def boxes(rows):
    """One complete box per row, available with its result; points equal the row's scores (100 possessions)."""
    return [BoxEvent(r.game, r.result_available_at, r.result_available_at, i,
                     BoxLine(r.home_score, 100, 0, 0, 0), BoxLine(r.away_score, 100, 0, 0, 0))
            for i, r in enumerate(rows)]


def preds(model, rows):
    return {p["game_id"]: p["p_home"] for p in run(model, rows, boxes(rows))}


@pytest.mark.parametrize("factory", FACTORIES)
def test_future_rows_and_boxes_do_not_change_past_predictions(factory):
    rows = synthetic_rows()
    split = "2023-02-01T00:00:00Z"
    base = preds(factory(), rows)
    past = [r for r in rows if r.cutoff_at < split]
    altered = past + [flip(r) for r in rows if r.cutoff_at >= split]
    for variant in (past, altered):
        got = preds(factory(), variant)
        assert all(got[r.game.game_id] == base[r.game.game_id] for r in past)


@pytest.mark.parametrize("factory", FACTORIES)
def test_current_game_label_and_box_do_not_change_its_prediction(factory):
    rows = synthetic_rows()
    base = preds(factory(), rows)
    for i in (5, len(rows) // 2, len(rows) - 1):
        changed = rows[:i] + [flip(rows[i])] + rows[i + 1:]  # boxes() rebuilds the flipped game's box as well
        assert preds(factory(), changed)[rows[i].game.game_id] == base[rows[i].game.game_id]


@pytest.mark.parametrize("factory", FACTORIES)
def test_candidates_use_box_scores(factory):
    rows = synthetic_rows()
    with_boxes, without = preds(factory(), rows), {p["game_id"]: p["p_home"] for p in run(factory(), rows)}
    assert any(with_boxes[k] != without[k] for k in with_boxes)
    assert all(0 < p < 1 for p in with_boxes.values())


def test_efficiency_sigma_is_fit_only_on_earlier_seasons():
    rows = synthetic_rows()
    m = EfficiencyMargin()
    part = rows_until(rows, 2023)
    run(m, part, boxes(part))
    earlier = [r for s, r in m.residuals if s < 2023]
    assert len(earlier) == len([r for r in rows if r.game.season < 2023])
    assert m.sigma == math.sqrt(sum(r * r for r in earlier) / len(earlier)) and m.first_fit_season == 2021


def test_efficiency_warmup_uses_the_fixed_sigma_before_its_first_fit():
    rows = synthetic_rows(seasons=[2020], per_season=20)
    m = EfficiencyMargin()
    run(m, rows, boxes(rows))
    assert m.sigma == m.sigma0 == 12.0 and m.first_fit_season is None


def test_logistic_v2_preprocessing_and_fit_use_only_earlier_seasons():
    rows = synthetic_rows()
    m = LogisticV2(FROZEN)
    part = rows_until(rows, 2023)
    run(m, part, boxes(part))
    train = [f for s, f, _, _ in m.examples if s < 2023]
    assert len(train) == len([r for r in rows if r.game.season < 2023])
    assert m.scaler == Standardizer.fit(train, V2_FEATURES, V2_SCALED) and "net_diff" in m.scaler.means
    assert m.weights == fit_logistic([m.scaler.vector(f) for f in train],
                                     [y for s, _, y, _ in m.examples if s < 2023], m.l2)
    assert len(m.weights) == len(V2_FEATURES) + 1 and "mov10_diff" not in m.config()["features"]


def _preds(name_to_p, ys):
    return {name: [{"game_id": f"g{i}", "p_home": p, "home_won": y, "cutoff_at": f"2022-0{1 + i % 9}-1{i % 9}T05:00:00Z",
                    "tipoff_at": "2022-01-01T00:00:00Z"} for i, (p, y) in enumerate(zip(ps, ys))]
            for name, ps in name_to_p.items()}


def test_screening_rule_requires_lower_brier_and_a_tight_upper_bound_and_caps_at_two(monkeypatch):
    ys = [i % 3 != 0 for i in range(300)]
    elo = [0.6] * 300
    better = [0.7 if y else 0.4 for y in ys]
    best = [0.8 if y else 0.3 for y in ys]
    worse = [0.5] * 300
    out = backtest.screen_candidates(_preds({"elo": elo, "efficiency_margin": better, "logistic": worse,
                                             "logistic_v2": best}, ys))
    assert out["passed"] == ["logistic_v2", "efficiency_margin"] and not out["comparisons"]["logistic"]["passed"]
    assert out["shadow_shortlist"] == ["logistic_v2", "efficiency_margin"]
    monkeypatch.setattr(backtest, "SCREEN_CAP", 1)
    assert backtest.screen_candidates(_preds({"elo": elo, "efficiency_margin": better, "logistic": worse,
                                              "logistic_v2": best}, ys))["shadow_shortlist"] == ["logistic_v2"]
    # A lower point estimate is not enough when the interval's upper bound is not below the threshold.
    noisy = [0.6 + (0.3 if i % 2 else -0.29) * (1 if y else -1) for i, y in enumerate(ys)]
    res = backtest.screen_candidates(_preds({"elo": elo, "efficiency_margin": noisy, "logistic": worse,
                                             "logistic_v2": worse}, ys))["comparisons"]["efficiency_margin"]
    assert res["ci95"][1] >= backtest.SCREEN_UPPER_BOUND and not res["passed"]


def test_screening_run_reads_no_test_rows_and_leaves_ledger_decision_and_access_log_alone(tmp_path, monkeypatch):
    conn = _db_with_games(tmp_path, monkeypatch)
    val = backtest.evaluate(conn, "validation", out_dir=tmp_path / "val", log=lambda _: None)
    decision_path = tmp_path / "decision.json"
    decision_path.write_text(json.dumps(backtest.decision_from_validation_report(val, "synthetic")))
    sealed = decision_path.read_bytes()
    seasons_loaded = []
    real_load = backtest.load_rows
    monkeypatch.setattr(backtest, "load_rows", lambda c, period: seasons_loaded.append(period) or real_load(c, period))
    report = backtest.evaluate_screening(conn, decision_path=decision_path, out_dir=tmp_path / "screen",
                                         log=lambda _: None)
    assert set(seasons_loaded) == {"validation"}
    assert report["kind"] == "screening" and report["seasons"] == backtest.PERIODS["validation"]
    assert set(report["screening"]["comparisons"]) == set(backtest.SCREEN_CANDIDATES)
    assert report["elo_config"] == val["elo_tuning"]["selected"]  # frozen config, not retuned
    assert report["models"]["elo"]["brier"] == val["models"]["elo"]["brier"]
    assert report["models"]["logistic"]["brier"] == val["models"]["logistic"]["brier"]  # existing model unchanged
    assert conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 0
    assert not (tmp_path / "bt" / "eval" / "test_access.jsonl").exists()
    assert decision_path.read_bytes() == sealed and (tmp_path / "screen" / "report.json").is_file()
    conn.close()
