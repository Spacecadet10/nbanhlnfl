"""Research round 3: learned stacking over walk-forward base predictions (synthetic data, no network)."""
import json
import random

import pytest

from app import backtest
from app.models import efficiency, stacking
from test_backtest import _db_with_games


def table(seasons=(2021, 2022, 2023, 2024), per_season=200, seed=3, flip_seasons=()):
    """Base predictions where logistic_v2 is informative and elo is noise; one game per day."""
    rng, rows = random.Random(seed), []
    for s in seasons:
        for i in range(per_season):
            p_true = rng.uniform(0.1, 0.9)
            won = rng.random() < p_true
            if s in flip_seasons:
                won = not won
            day = f"{s - 1}-11-01" if i < 30 else f"{s}-01-{1 + i % 28:02d}"
            rows.append({"game_id": f"g{s}-{i}", "season": s, "season_type": "regular", "tipoff_at": f"{day}T23:00:00Z",
                         "cutoff_at": f"{day}T04:00:00Z", "result_available_at": f"{day}T23:59:00Z", "home_team_id": "a",
                         "away_team_id": "b", "neutral_site": False, "home_won": won, "elo": rng.uniform(0.3, 0.7),
                         "efficiency_margin": min(0.99, max(0.01, p_true + rng.gauss(0, 0.2))),
                         "logistic": min(0.99, max(0.01, p_true + rng.gauss(0, 0.1))), "logistic_v2": p_true,
                         "gbt_v1": min(0.999, max(0.001, p_true + rng.gauss(0, 0.3))),
                         "rest_diff": rng.choice([-1.0, 0.0, 2.0]), "net_diff": rng.gauss(0, 4)})
    return rows


def test_registered_definition():
    assert stacking.BASE_MODELS == ("elo", "efficiency_margin", "logistic", "logistic_v2", "gbt_v1")
    assert stacking.CLIP == (0.02, 0.98) and stacking.L2 == 10.0 and len(stacking.INTERACTIONS) == 3


def test_features_clip_probabilities_and_build_the_three_interactions():
    row = {"elo": 0.999, "efficiency_margin": 0.001, "logistic": 0.5, "logistic_v2": 0.6, "gbt_v1": 0.7,
           "rest_diff": -2.0, "net_diff": 3.0}
    a = stacking.features_logit(row)
    assert a[0] == pytest.approx(stacking.logit(0.98)) and a[1] == pytest.approx(stacking.logit(0.02)) and a[2] == 0.0
    b = stacking.features_conditional(row)
    m = sum(a) / 5
    assert b[:5] == a and b[5:] == pytest.approx([m * (0.999 - 0.001), m * 2.0, m * 3.0])


def test_each_season_is_fitted_only_on_earlier_seasons():
    preds, fits = stacking.walk_forward(table(), stacking.features_logit, (2022, 2023, 2024), 2021)
    assert [(f["season"], f["training_seasons"], f["training_games"]) for f in fits] == \
        [(2022, [2021], 200), (2023, [2021, 2022], 400), (2024, [2021, 2022, 2023], 600)]
    assert len(preds) == 600 and {p["season"] for p in preds} == {2022, 2023, 2024}
    assert all(0 < p["p_home"] < 1 for p in preds)


def test_later_seasons_and_own_season_results_do_not_change_predictions():
    base = {p["game_id"]: p["p_home"] for p in stacking.walk_forward(table(), stacking.features_conditional, (2022, 2023), 2021)[0]}
    # Flipping every result in 2023 and 2024 leaves 2022's predictions unchanged, and 2023's too (its own labels are unused).
    flipped = {p["game_id"]: p["p_home"] for p in stacking.walk_forward(table(flip_seasons=(2023, 2024)),
                                                                        stacking.features_conditional, (2022, 2023), 2021)[0]}
    assert flipped == base
    # Flipping 2022's results changes 2023's predictions (they train on 2022) but not 2022's own.
    changed = {p["game_id"]: p["p_home"] for p in stacking.walk_forward(table(flip_seasons=(2022,)),
                                                                        stacking.features_conditional, (2022, 2023), 2021)[0]}
    assert all(changed[g] == base[g] for g in base if g.startswith("g2022"))
    assert any(changed[g] != base[g] for g in base if g.startswith("g2023"))


def test_training_results_must_be_available_before_the_season_starts():
    rows = table()
    late = next(r for r in rows if r["season"] == 2021)
    late["result_available_at"] = "2021-12-01T00:00:00Z"      # after 2021-22's first cutoff (2021-11-01)
    with pytest.raises(ValueError, match="not available before season 2022"):
        stacking.walk_forward(rows, stacking.features_logit, (2022,), 2021)
    with pytest.raises(ValueError, match="no training or target rows"):
        stacking.walk_forward(table(seasons=(2022,)), stacking.features_logit, (2022,), 2021)


def test_stacker_weights_the_informative_model_and_is_deterministic():
    rows = table(per_season=600)
    preds, fits = stacking.walk_forward(rows, stacking.features_logit, (2024,), 2021)
    weights = dict(zip(stacking.BASE_MODELS, fits[0]["weights"][1:]))
    assert weights["logistic_v2"] == max(weights.values()) and weights["logistic_v2"] > 5 * abs(weights["elo"])
    again, _ = stacking.walk_forward(rows, stacking.features_logit, (2024,), 2021)
    assert again == preds
    brier = lambda ps, key=None: sum(((p["p_home"] if key is None else p[key]) - p["home_won"]) ** 2 for p in ps) / len(ps)  # noqa: E731
    target = [r for r in rows if r["season"] == 2024]
    assert brier(preds) < brier(target, "elo") and brier(preds) < brier(target, "gbt_v1")


def test_confidence_ranking_uses_no_outcome_and_wilson_interval():
    preds = [{"game_id": f"g{i}", "p_home": p, "home_won": w} for i, (p, w) in
             enumerate([(0.9, True), (0.2, False), (0.55, False), (0.5, True), (0.7, True)])]
    top = stacking.most_confident(preds, 0.6)
    assert [p["game_id"] for p in top] == ["g0", "g1", "g4"]
    flipped = [{**p, "home_won": not p["home_won"]} for p in preds]
    assert [p["game_id"] for p in stacking.most_confident(flipped, 0.6)] == ["g0", "g1", "g4"]
    acc = stacking.accuracy_on(top)
    assert (acc["games"], acc["wins"], acc["accuracy"]) == (3, 3, 1.0) and acc["wilson95"][0] < 1.0
    assert stacking.wilson(0, 0) == [None, None] and len(stacking.most_confident(preds, 1.0)) == 5


def test_research_run_reads_only_validation_and_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(efficiency, "MIN_SIGMA_EXAMPLES", 5)      # the synthetic store has only 12 games a season
    conn = _db_with_games(tmp_path, monkeypatch)
    val = backtest.evaluate(conn, "validation", out_dir=tmp_path / "val", log=lambda _: None)
    decision_path = tmp_path / "decision.json"
    decision_path.write_text(json.dumps(backtest.decision_from_validation_report(val, "synthetic")))
    sealed = decision_path.read_bytes()
    periods = []
    real_load = backtest.load_rows
    monkeypatch.setattr(backtest, "load_rows", lambda c, period: periods.append(period) or real_load(c, period))
    report = backtest.evaluate_research_stacking(conn, decision_path=decision_path, out_dir=tmp_path / "stack",
                                                 log=lambda _: None)
    assert set(periods) == {"validation"} and report["experiment"] == "stacking"
    assert set(report["models"]) == {*stacking.BASE_MODELS, "equal_average", "stack_logit", "stack_conditional"}
    assert len({m["n"] for m in report["models"].values()}) == 1                         # identical games
    assert report["models"]["elo"]["brier"] == val["models"]["elo"]["brier"]               # base model as standalone
    assert report["models"]["logistic"]["brier"] == val["models"]["logistic"]["brier"]
    for fits in report["fits"].values():
        assert [f["season"] for f in fits] == [2022, 2023, 2024]
        assert all(min(f["training_seasons"]) == 2021 and max(f["training_seasons"]) < f["season"] for f in fits)
    assert set(report["selective_prediction"]) == {"0.5", "0.7", "0.85", "1.0"}
    assert set(report["comparisons"]["stack_conditional"]) == {"logistic_v2", "elo", "equal_average"}
    assert conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 0
    assert not (tmp_path / "bt" / "eval" / "test_access.jsonl").exists()
    assert decision_path.read_bytes() == sealed and (tmp_path / "stack" / "report.json").is_file()
    conn.close()
