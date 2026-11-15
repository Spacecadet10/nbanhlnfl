"""Research round 2: the gradient-boosted tree candidate, its leakage guards and its evaluation run (synthetic data)."""
import json
import subprocess
import sys

import pytest

from app import backtest
from app.backtest import run
from app.models.elo import Elo
from app.models.gbt import FEATURES, MONOTONIC_INCREASING, SETTINGS, GradientBoostedTrees
from test_backtest import _db_with_games, flip, rows_until, synthetic_rows
from test_candidates import FROZEN, boxes


def preds(rows):
    return {p["game_id"]: p["p_home"] for p in run(GradientBoostedTrees(FROZEN), rows, boxes(rows))}


def test_settings_are_the_registered_ones():
    assert SETTINGS == {"loss": "log_loss", "learning_rate": 0.05, "max_iter": 200, "max_depth": 3, "max_leaf_nodes": 8,
                        "min_samples_leaf": 50, "l2_regularization": 1.0, "max_bins": 255, "early_stopping": False,
                        "random_state": 20260930}
    assert len(FEATURES) == 12 and set(MONOTONIC_INCREASING) == {"elo_diff", "net_diff", "mov10_diff"}
    assert set(MONOTONIC_INCREASING) <= set(FEATURES)


def test_future_rows_and_boxes_do_not_change_past_predictions():
    rows = synthetic_rows()
    split = "2023-02-01T00:00:00Z"
    base = preds(rows)
    past = [r for r in rows if r.cutoff_at < split]
    for variant in (past, past + [flip(r) for r in rows if r.cutoff_at >= split]):
        got = preds(variant)
        assert all(got[r.game.game_id] == base[r.game.game_id] for r in past)


def test_current_game_label_and_box_do_not_change_its_prediction():
    rows = synthetic_rows()
    base = preds(rows)
    for i in (5, len(rows) // 2, len(rows) - 1):
        changed = rows[:i] + [flip(rows[i])] + rows[i + 1:]
        assert preds(changed)[rows[i].game.game_id] == base[rows[i].game.game_id]


def test_each_fit_uses_only_earlier_seasons_and_features_are_pregame_snapshots():
    rows = synthetic_rows()
    m = GradientBoostedTrees(FROZEN)
    part = rows_until(rows, 2023)
    run(m, part, boxes(part))
    assert [f["season"] for f in m.fits] == [2021, 2022, 2023]
    assert all(max(f["training_seasons"]) < f["season"] for f in m.fits)
    assert m.fits[-1]["training_games"] == len([r for r in rows if r.game.season < 2023])
    assert all(season < 2023 for season, _, _ in m.examples) and all(len(x) == len(FEATURES) for _, x, _ in m.examples)
    assert m.first_fit_season == 2021


def test_warmup_returns_the_frozen_elo_probability():
    rows = synthetic_rows(seasons=[2020, 2021], per_season=20)
    got = preds(rows)
    elo = {p["game_id"]: p["p_home"] for p in run(Elo(FROZEN), rows)}
    assert all(got[r.game.game_id] == elo[r.game.game_id] for r in rows if r.game.season == 2020)
    assert any(got[r.game.game_id] != elo[r.game.game_id] for r in rows if r.game.season == 2021)


def test_runs_are_deterministic_and_probabilities_valid():
    rows = synthetic_rows()
    first, second = preds(rows), preds(rows)
    assert first == second and all(0 < p < 1 for p in first.values())


def test_paired_accuracy_bootstrap_counts_discordant_games():
    def mk(ps):
        return [{"game_id": f"g{i}", "p_home": p, "home_won": True, "cutoff_at": f"2022-01-{1 + i % 28:02d}T05:00:00Z"}
                for i, p in enumerate(ps)]
    a, b = mk([0.7] * 60 + [0.3] * 40), mk([0.7] * 50 + [0.3] * 50)
    out = backtest.paired_accuracy_bootstrap(a, b)
    assert (out["only_first_right"], out["only_second_right"], out["games"]) == (10, 0, 100)
    assert out["diff"] == pytest.approx(0.10) and out["ci95"][0] > 0
    assert backtest.paired_accuracy_bootstrap(a, a)["ci95"] == [0.0, 0.0]
    assert backtest.paired_accuracy_bootstrap(a, b) == out   # seeded


def test_research_run_reads_only_validation_and_changes_nothing(tmp_path, monkeypatch):
    conn = _db_with_games(tmp_path, monkeypatch)
    val = backtest.evaluate(conn, "validation", out_dir=tmp_path / "val", log=lambda _: None)
    decision_path = tmp_path / "decision.json"
    decision_path.write_text(json.dumps(backtest.decision_from_validation_report(val, "synthetic")))
    sealed = decision_path.read_bytes()
    periods = []
    real_load = backtest.load_rows
    monkeypatch.setattr(backtest, "load_rows", lambda c, period: periods.append(period) or real_load(c, period))
    report = backtest.evaluate_research_gbt(conn, decision_path=decision_path, out_dir=tmp_path / "research",
                                            log=lambda _: None)
    assert set(periods) == {"validation"} and report["kind"] == "research" and report["candidate"] == "gbt_v1"
    assert set(report["comparisons"]) == {"elo", "logistic_v2"}
    assert {"brier_diff", "accuracy_diff"} == set(report["comparisons"]["elo"])
    assert report["models"]["elo"]["brier"] == val["models"]["elo"]["brier"]   # frozen configuration, not retuned
    assert all(max(f["training_seasons"]) < f["season"] <= 2024 for f in report["fits"])
    assert conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 0
    assert not (tmp_path / "bt" / "eval" / "test_access.jsonl").exists()
    assert decision_path.read_bytes() == sealed and (tmp_path / "research" / "report.json").is_file()
    conn.close()


def test_api_and_jobs_never_import_scikit_learn():
    code = ("import sys; import app.main, app.jobs, app.shadow, app.shadow_report, app.cli; "
            "print(any(m == 'sklearn' or m.startswith('sklearn.') for m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"
