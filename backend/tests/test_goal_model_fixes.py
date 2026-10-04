"""Empty-net adjustment of NHL score grids and the goal-GLM penalty (review fixes, 2026-10-03)."""
import pytest

from app import markets
from app.models import score_candidates as sc
from app.models.hockey import GoalsConfig, GoalsPoisson


def test_default_grid_is_unchanged_and_the_adjustment_is_opt_in():
    plain = markets.poisson_grid(3.1, 2.7, 0.55)
    assert "empty_net" not in plain.params
    adj = markets.poisson_grid(3.1, 2.7, 0.55, empty_net_goals=True)
    assert adj.params["empty_net"] == markets.EMPTY_NET
    assert sum(adj.pmf.values()) == pytest.approx(1.0, abs=1e-9)


def test_empty_net_moves_one_and_two_goal_leads_without_changing_who_wins():
    pmf = {(2, 1): 0.5, (1, 3): 0.3, (2, 2): 0.2}
    out = markets.empty_net(pmf)
    e1, e2 = markets.EMPTY_NET["lead1"], markets.EMPTY_NET["lead2"]
    assert out[(2, 1)] == pytest.approx(0.5 * (1 - e1)) and out[(3, 1)] == pytest.approx(0.5 * e1)
    assert out[(1, 3)] == pytest.approx(0.3 * (1 - e2)) and out[(1, 4)] == pytest.approx(0.3 * e2)
    assert out[(2, 2)] == 0.2                                     # ties are untouched
    assert sum(out.values()) == pytest.approx(1.0)
    plain, adj = (markets.poisson_grid(3.0, 2.8, 0.5, empty_net_goals=x) for x in (False, True))
    lam = markets.EMPTY_NET["rate_scale"]
    same_rates = markets.poisson_grid(3.0 * lam, 2.8 * lam, 0.5)
    assert adj.reg3() == pytest.approx(same_rates.reg3(), abs=1e-9)   # 3-way result depends only on the rates
    assert adj.market("spread", -1.5)[0] > plain.market("spread", -1.5)[0]   # favourite covers -1.5 more often


def test_goals_poisson_version_changes_only_with_the_adjustment():
    off, on = GoalsPoisson(GoalsConfig(eta=0.003)), GoalsPoisson(GoalsConfig(eta=0.003, empty_net=True))
    assert "empty_net" not in off.config()                       # sealed version string is unchanged
    assert on.config()["empty_net"].startswith("l1=")


def test_research_goal_models_use_the_adjustment_and_a_light_penalty():
    assert sc.GLM_ALPHA <= 0.01
    # The sealed pool candidates (candidates/learners.py, pool_rules.json) keep their sealed settings: changing them would
    # change every sealed definition. A corrected pool goal model has to qualify as a new candidate.
    pmf = sc.regulation_pmf(3.0, 2.8, 0.05, empty_net_goals=True)
    assert sum(pmf.values()) == pytest.approx(1.0, abs=1e-6)
