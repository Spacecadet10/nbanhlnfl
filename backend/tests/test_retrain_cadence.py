"""Retraining-cadence wrapper (docs/MODEL_SELECTION.md §9): leakage guards and reproduction (synthetic data)."""
import pytest

from app.backtest import run
from app.models.logistic import LogisticElo, LogisticV2
from app.models.score_candidates import RidgePoints
from app.retrain_cadence import Cadenced, compare, period_key
from test_backtest import flip, synthetic_rows
from test_candidates import FROZEN, boxes

FACTORIES = [lambda: LogisticElo(FROZEN), lambda: LogisticV2(FROZEN), lambda: RidgePoints(FROZEN)]


def preds(model, rows):
    return {p["game_id"]: p["p_home"] for p in run(model, rows, boxes(rows))}


def test_period_keys_use_the_eastern_game_date():
    assert period_key("weekly", "2024-01-01T00:30:00Z") == (2023, 52)    # 19:30 ET on Sunday 31 December
    assert period_key("monthly", "2024-02-01T01:00:00Z") == (2024, 1)    # 20:00 ET on 31 January
    assert period_key("season", "2024-02-01T01:00:00Z") == ()
    with pytest.raises(ValueError):
        Cadenced(LogisticElo(FROZEN), "daily")


@pytest.mark.parametrize("factory", FACTORIES)
def test_season_cadence_reproduces_the_member(factory):
    rows = synthetic_rows()
    assert preds(Cadenced(factory(), "season"), rows) == preds(factory(), rows)


@pytest.mark.parametrize("cadence", ["monthly", "weekly"])
def test_each_within_season_refit_trains_on_exactly_the_results_available_at_its_cutoff(cadence):
    rows = synthetic_rows()
    m = Cadenced(LogisticElo(FROZEN), cadence)
    run(m, rows)
    assert m.refits, "expected within-season refits"
    for f in m.refits:
        assert f["training_games"] == sum(r.result_available_at <= f["cutoff_at"] for r in rows)
    assert min(f["season"] for f in m.refits) == 2021   # none in the walk's first season


@pytest.mark.parametrize("factory", FACTORIES)
def test_weekly_refits_change_forecasts_but_future_rows_never_change_past_ones(factory):
    rows = synthetic_rows()
    base = preds(Cadenced(factory(), "weekly"), rows)
    assert base != preds(factory(), rows)
    split = "2023-12-01T00:00:00Z"   # mid-season
    past = [r for r in rows if r.cutoff_at < split]
    for variant in (past, past + [flip(r) for r in rows if r.cutoff_at >= split]):
        got = preds(Cadenced(factory(), "weekly"), variant)
        assert all(got[r.game.game_id] == base[r.game.game_id] for r in past)


def test_current_game_label_does_not_change_its_weekly_forecast():
    rows = synthetic_rows()
    base = preds(Cadenced(LogisticV2(FROZEN), "weekly"), rows)
    for i in (100, len(rows) // 2, len(rows) - 1):
        changed = rows[:i] + [flip(rows[i])] + rows[i + 1:]
        assert preds(Cadenced(LogisticV2(FROZEN), "weekly"), changed)[rows[i].game.game_id] == base[rows[i].game.game_id]


def test_compare_pairs_games_and_applies_the_reading_rule():
    def walk(loss_by_season):
        return {f"g{s}-{i}": {"brier": loss_by_season[s], "_season": s, "_cutoff": f"{s - 1}-12-{1 + i:02d}T05:00:00Z"}
                for s in (2022, 2023, 2024) for i in range(20)}
    walks = {"season": walk({2022: 0.25, 2023: 0.25, 2024: 0.25}),
             "monthly": walk({2022: 0.20, 2023: 0.20, 2024: 0.26}),
             "weekly": walk({2022: 0.25, 2023: 0.25, 2024: 0.25})}
    walks["weekly"].pop("g2022-0")   # a game one cadence did not forecast is dropped from every comparison
    out = compare(walks, "brier", (2022, 2023, 2024))
    assert out["games"] == 59
    assert out["monthly"]["seasons_not_worse"] == 2 and out["monthly"]["supports_trial"] is True   # 2 of 3 seasons, bound < 0
    assert out["weekly"]["brier"]["mean"] == 0 and out["weekly"]["supports_trial"] is False
    walks["monthly"] = walk({2022: 0.10, 2023: 0.26, 2024: 0.26})   # better overall but worse in 2 of 3 seasons
    assert compare(walks, "brier", (2022, 2023, 2024))["monthly"]["supports_trial"] is False
