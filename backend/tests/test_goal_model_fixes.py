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


def test_goals_glm_abstains_on_a_degenerate_rate_instead_of_crashing():
    """A near-constant feature column with a rare outlier can standardize into a huge linear predictor, so the GLM
    emits an enormous finite Poisson rate; exp(-rate) then underflows and the regulation grid sums to zero. The model
    must abstain (distribution None, predict 0.5), not raise ZeroDivisionError. This is the path the NHL injury member
    hit during the 2026-10-12 daily retrain (nhl_players_injuries@adaptive: ZeroDivisionError: float division by zero)."""
    import numpy as np
    from app.models.base import PregameGame
    from app.models.hockey import HockeyEloConfig
    from app.models.score_candidates import GoalsGlm, NHL_FEATURES, regulation_pmf

    class _Huge:
        def predict(self, z):
            return [1e6]

    n = len(NHL_FEATURES)
    assert sum(regulation_pmf(1e6, 1e6, empty_net_goals=True).values()) == 0.0   # the degenerate grid that crashed

    m = GoalsGlm(HockeyEloConfig())
    g = PregameGame("nhl:degenerate", 2024, "regular", "2024-01-01T23:00:00Z", "nhl:1", "nhl:2", False)
    m.season = g.season
    m.pending[g.game_id] = (g.season, [0.0] * n, g.neutral_site)   # bypass the feature walk; force the fitted rate below
    m.fitted = (np.zeros(n), np.ones(n), (_Huge(), _Huge()))

    assert m.distribution(g) is None
    assert m.predict(g) == 0.5


def test_goals_glm_abstains_when_the_rate_puts_mass_beyond_the_score_grid():
    """The degenerate-rate guard must not stop at exact underflow: a finite huge rate (here 40 goals) leaves almost all
    of its mass beyond MAX_GOALS, and renormalising the remainder produced a ~100% winner and a truncated total. The
    model abstains instead. Realistic NHL rates (≤ 6 goals) keep well over 99% of the mass and still forecast."""
    import numpy as np
    from app.models.base import PregameGame
    from app.models.hockey import HockeyEloConfig
    from app.models.score_candidates import GoalsGlm, NHL_FEATURES

    class _Rate:
        def __init__(self, rate):
            self.rate = rate

        def predict(self, z):
            return [self.rate]

    n = len(NHL_FEATURES)
    g = PregameGame("nhl:edge", 2024, "regular", "2024-01-01T23:00:00Z", "nhl:1", "nhl:2", False)

    def model(home_rate):
        m = GoalsGlm(HockeyEloConfig())
        m.season = g.season
        m.pending[g.game_id] = (g.season, [0.0] * n, g.neutral_site)
        m.fitted = (np.zeros(n), np.ones(n), (_Rate(home_rate), _Rate(1.0)))
        return m

    assert model(40.0).distribution(g) is None and model(40.0).predict(g) == 0.5
    assert model(6.0).distribution(g) is not None


def test_draw_inflated_goal_grids_abstain_when_the_rate_puts_mass_beyond_the_score_grid():
    """GoalsDraw and the registry goals head (poisson_draw candidates) renormalised a truncated grid without GoalsGlm's
    guard. With draw inflation the tie mass stays w while the rest shrinks with the kept mass, so an extreme rate (forced:
    λ=40 is far outside realistic NHL rates) turned a near-certain home win into a 55% winner, ~0% regulation home win."""
    from app.candidates import live as pool_live
    from app.models.base import PregameGame
    g = PregameGame("nhl:edge", 2024, "regular", "2024-01-01T23:00:00Z", "nhl:1", "nhl:2", False)

    def draw(home_rate):
        m = sc.GoalsDraw(GoalsConfig())
        m.season, m.weight = g.season, 0.05
        m.pending[g.game_id] = (g.season, home_rate, 1.0, 0.5)
        return m
    assert draw(40.0).distribution(g) is None and draw(40.0).predict(g) == 0.5
    assert draw(6.0).distribution(g) is not None and draw(8.45).distribution(g) is not None   # realistic / study maximum
    goals = lambda r: {"head": "goals", "lh": r, "la": 1.0, "w": 0.05, "ot": 0.5}            # noqa: E731
    # candidates: checked where outputs are applied (engine.py is part of every candidate's sealed identity)
    assert not pool_live.usable_goals(goals(40.0)) and not pool_live.usable_goals(goals(float("nan")))
    assert pool_live.usable_goals(goals(6.0)) and pool_live.usable_goals(goals(8.45))
