"""Candidates, combinations and the selector (docs/MODEL_SELECTION.md)."""
import math
import random
from datetime import datetime, timedelta, timezone

import pytest

from app import backtest, combine, markets, selector
from app.backtest import run
from app.models.base import PregameGame
from app.models.hockey import GoalsConfig, HockeyEloConfig
from app.models.score_candidates import GbtPoints, GoalsDraw, GoalsGlm, RidgePoints, grid_from_regulation, regulation_pmf
from app.nhl_eval import HockeyRow
from test_backtest import flip, synthetic_rows
from test_candidates import FROZEN, boxes


# --- combine ---------------------------------------------------------------------------------------------------------
def test_draw_is_a_pick_only_in_the_three_way_market_and_ties_abstain():
    assert combine.pick((0.3, 0.4, 0.3), "reg3") == 1
    assert combine.pick((0.3, 0.4, 0.3), "spread") is None      # the push is never a pick; 0.3 v 0.3 ties
    assert combine.pick((0.45, 0.1, 0.45), "total") is None
    assert combine.pick((0.5, 0.0, 0.5), "winner") is None
    assert combine.pick((0.46, 0.1, 0.44), "total") == 0


def test_agreement_counts_missing_members_against_majority_and_unanimity():
    a = combine.agreement({"x": (0.6, 0, 0.4), "y": (0.7, 0, 0.3)}, ["x", "y", "z"], "winner")
    assert a["side"] == "a" and a["majority"] and not a["unanimous"] and not a["complete"]
    b = combine.agreement({"x": (0.6, 0, 0.4)}, ["x", "y", "z"], "winner")
    assert not b["majority"]                                    # 1 of 3 expected is not a majority
    c = combine.agreement({"x": (0.6, 0, 0.4), "y": (0.5, 0, 0.5), "z": (0.7, 0, 0.3)}, ["x", "y", "z"], "winner")
    assert c["abstentions"] == 1 and c["majority"] and not c["unanimous"]
    d = combine.agreement({"x": (0.3, .4, .3), "y": (.2, .5, .3), "z": (.5, .3, .2)}, ["x", "y", "z"], "reg3")
    assert d["side"] == "mid" and d["majority"]                 # a draw majority is a real pick


def _stack_rows(market, n=2000, seed=3):
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        p = rng.uniform(0.2, 0.8)
        y = 0 if rng.random() < p else 2
        good = (p, 0.0, 1 - p)
        noise = rng.uniform(0.2, 0.8)
        rows.append(([good, (noise, 0.0, 1 - noise)], y))
    return rows


def test_stacker_weights_the_informative_member_and_preserves_push_probability():
    model = combine.fit_stack(_stack_rows("total"), "total")
    assert model["weights"][0] > 0.7 and abs(model["weights"][1]) < 0.2
    out = combine.stack(model, [(0.45, 0.1, 0.45), (0.5, 0.1, 0.4)])
    assert abs(out[1] - 0.1) < 1e-12 and abs(sum(out) - 1) < 1e-12
    three = combine.fit_stack([([(0.5, 0.2, 0.3), (0.4, 0.3, 0.3)], k) for k in (0, 1, 2, 0)], "reg3")
    v = combine.stack(three, [(0.5, 0.2, 0.3), (0.4, 0.3, 0.3)])
    assert abs(sum(v) - 1) < 1e-12 and v[1] > 0


def test_stacker_is_deterministic():
    rows = _stack_rows("winner", 500)
    assert combine.fit_stack(rows, "winner") == combine.fit_stack(rows, "winner")


# --- NBA candidates ----------------------------------------------------------------------------------------------------
NBA_FACTORIES = [lambda: RidgePoints(FROZEN), lambda: GbtPoints(FROZEN)]


def _dists(model, rows):
    out = run(model, rows, boxes(rows), extra=lambda m, g: {"d": m.distribution(g)})
    return {p["game_id"]: (None if p["d"] is None else (p["d"].m, p["d"].sm, p["d"].t, p["d"].st)) for p in out}


@pytest.mark.parametrize("factory", NBA_FACTORIES)
def test_nba_candidates_ignore_future_and_own_results(factory):
    rows = synthetic_rows(per_season=150)
    base = _dists(factory(), rows)
    assert any(v is not None for v in base.values())
    split = "2025-02-01T00:00:00Z"
    past = [r for r in rows if r.cutoff_at < split]
    got = _dists(factory(), past + [flip(r) for r in rows if r.cutoff_at >= split])
    assert all(got[r.game.game_id] == base[r.game.game_id] for r in past)
    i = len(rows) - 5
    changed = rows[:i] + [flip(rows[i])] + rows[i + 1:]
    assert _dists(factory(), changed)[rows[i].game.game_id] == base[rows[i].game.game_id]


def test_nba_candidate_is_not_ready_until_it_has_own_out_of_sample_residuals():
    m = RidgePoints(FROZEN)
    rows = synthetic_rows(per_season=150)
    out = _dists(m, rows)
    ready = sorted({r.game.season for r in rows if out[r.game.game_id] is not None})
    fit_seasons = [f["season"] for f in m.fits]
    # First fit needs 200 earlier games (2022); its own residuals need another 200 games (ready 2024), never sooner.
    assert fit_seasons[0] == 2022 and ready[0] == m.oos_from_season() == 2024
    assert m.residuals and all(s >= fit_seasons[0] for s, _, _ in m.residuals)


# --- NHL candidates ----------------------------------------------------------------------------------------------------
def hockey_rows(seasons=range(2016, 2022), per_season=400, seed=5):
    rng = random.Random(seed)
    teams = [f"nhl:{i}" for i in range(1, 9)]
    strength = {t: rng.gauss(0, 0.2) for t in teams}
    rows = []
    for season in seasons:
        t0 = datetime(season - 1, 10, 10, 23, 0, tzinfo=timezone.utc)
        for i in range(per_season):
            h, a = rng.sample(teams, 2)
            lh, la = 3.0 * math.exp(0.08 + strength[h] - strength[a]), 2.8 * math.exp(strength[a] - strength[h])
            gh, ga = _pois(rng, lh), _pois(rng, la)
            dec = "REG"
            if gh == ga or rng.random() < 0.05:          # extra draws: what the inflated model should find
                ga = gh
                dec = rng.choice(("OT", "SO"))
                gh, ga = (gh + 1, ga) if rng.random() < 0.52 else (gh, ga + 1)
            tip = t0 + timedelta(hours=10 * i)
            g = PregameGame(f"h{season}-{i}", season, "regular", tip.strftime("%Y-%m-%dT%H:%M:%SZ"), h, a, False)
            rows.append(HockeyRow(g, backtest.cutoff_for(g.tipoff_at), gh, ga,
                                  (tip + timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%SZ"), dec))
    return rows


def _pois(rng, lam):
    k, p, L = 0, 1.0, math.exp(-lam)
    while True:
        p *= rng.random()
        if p <= L:
            return k
        k += 1


NHL_FACTORIES = [lambda: GoalsDraw(GoalsConfig()), lambda: GoalsGlm(HockeyEloConfig())]


def _grids(model, rows):
    out = run(model, rows, extra=lambda m, g: {"d": m.distribution(g)})
    return {p["game_id"]: (None if p["d"] is None else tuple(p["d"].market("total", 5.5)) + p["d"].reg3()) for p in out}


@pytest.mark.parametrize("factory", NHL_FACTORIES)
def test_nhl_candidates_ignore_future_and_own_results(factory):
    rows = hockey_rows()
    base = _grids(factory(), rows)
    split = "2020-01-01T00:00:00Z"
    past = [r for r in rows if r.cutoff_at < split]
    got = _grids(factory(), past + [flip(r) for r in rows if r.cutoff_at >= split])
    assert any(base[r.game.game_id] is not None for r in past)
    assert all(got[r.game.game_id] == base[r.game.game_id] for r in past)


def test_draw_inflation_matches_poisson_grid_at_zero_and_is_found_when_draws_are_frequent():
    g0 = grid_from_regulation(regulation_pmf(3.1, 2.7), 0.55, {})
    ref = markets.poisson_grid(3.1, 2.7, 0.55)
    for m, line in (("winner", None), ("reg3", None), ("spread", -1.5), ("total", 5.5), ("team_total_home", 2.5)):
        assert all(abs(x - y) < 1e-12 for x, y in zip(g0.market(m, line), ref.market(m, line)))
    model = GoalsDraw(GoalsConfig())
    _grids(model, hockey_rows())
    assert model.weight is not None and model.weight > 0
    d = model.distribution(PregameGame("x", 2022, "regular", "2021-11-01T23:00:00Z", "nhl:1", "nhl:2", False))
    inflated = d.reg3()[1]
    plain = markets.poisson_grid(*model.goals.rates(PregameGame("y", 2022, "regular", "2021-11-01T23:00:00Z", "nhl:1", "nhl:2", False)), 0.5).reg3()[1]
    assert inflated > plain


def test_candidate_markets_are_coherent():
    model = GoalsGlm(HockeyEloConfig())
    _grids(model, hockey_rows())
    d = model.distribution(PregameGame("x", 2022, "regular", "2021-11-01T23:00:00Z", "nhl:1", "nhl:2", False))
    for m in ("spread", "total", "team_total_home", "team_total_away"):
        markets.check_consistency(d, m, [x / 2 for x in range(-9, 20)])
    assert abs(d.winner()[0] - (d.reg3()[0] + d.reg3()[1] * model.ot_share)) < 1e-9


# --- selector ----------------------------------------------------------------------------------------------------------
def _items(n, acc, seed=1, start="2026-10-05", conf=0.7, market="winner"):
    rng = random.Random(seed)
    t0 = datetime.strptime(start, "%Y-%m-%d")
    out = []
    for i in range(n):
        side = 0 if rng.random() < 0.5 else 2
        realised = side if rng.random() < acc else 2 - side
        vec = (conf, 0.0, 1 - conf) if side == 0 else (1 - conf, 0.0, conf)
        out.append({"game_id": f"g{i}", "week": selector.week_of((t0 + timedelta(days=i // 10)).strftime("%Y-%m-%d")),
                    "vectors": {"p": vec, "q": vec}, "agreement": {"majority_side": combine.SIDES[side], "unanimous_side": None},
                    "bookmaker": None, "realised": realised})
    return out


def test_minimum_evidence_and_abstention():
    assert selector.decide(_items(90, 0.95), "winner", ["p", "q"])["status"] == "insufficient_evidence"
    assert selector.decide(_items(400, 0.52), "winner", ["p", "q"])["status"] == "abstain"
    d = selector.decide(_items(400, 0.85), "winner", ["p", "q"])
    assert d["status"] == "active" and d["rule"]["source"] == "p" and d["lower"] > 0


def test_bonferroni_bound_tightens_with_more_rules():
    assert selector.z_for(1) < selector.z_for(18) < selector.z_for(144)
    one = selector.evaluate(_items(400, 0.7), "winner", ["p"])
    two = selector.evaluate(_items(400, 0.7), "winner", ["p", "q"])
    assert two[0]["lower"] < one[0]["lower"] and two[0]["lift"] == one[0]["lift"]


def test_reference_removes_class_balance():
    items = _items(400, 0.5)
    for i in items:                        # always pick home, home always wins 60%: a home-rate rule has no lift
        i["vectors"] = {"p": (0.7, 0.0, 0.3)}
        i["realised"] = 0 if random.Random(i["game_id"]).random() < 0.6 else 2
    s = selector.evaluate(items, "winner", ["p"])[0]
    assert abs(s["lift"]) < 1e-12 and s["accuracy"] > 0.55


def test_bookmaker_reference_used_at_the_exact_line():
    item = {"bookmaker": (0.55, 0.05, 0.40)}
    assert abs(selector.reference(item, 0, {0: 0.5, 2: 0.5}, "spread") - 0.55 / 0.95) < 1e-12


def test_hysteresis_keeps_the_incumbent():
    items = _items(600, 0.8)
    d = selector.decide(items, "winner", ["p", "q"])
    other = {"source": "q", "filter": d["rule"]["filter"], "threshold": d["rule"]["threshold"]}
    kept = selector.decide(items, "winner", ["p", "q"], incumbent=other)
    assert kept["rule"] == other                     # identical evidence: the incumbent is not displaced


def test_apply_and_market_choice_record_reasons():
    d = selector.decide(_items(400, 0.85), "winner", ["p", "q"])
    item = _items(1, 1.0, seed=9)[0]
    assert selector.apply(None, item, "winner")["status"] == "no_pick"
    s = selector.apply(d, item, "winner")
    assert s["status"] == "pick"
    strict = {**d, "rule": {"source": "p", "filter": "majority", "threshold": 0.6}}
    low = {**item, "vectors": {"p": (0.52, 0, 0.48), "q": (0.52, 0, 0.48)}}
    assert "below threshold" in selector.apply(strict, low, "winner")["reason"]
    other = {**item, "agreement": {"majority_side": "b" if s["side"] == "a" else "a"}}
    assert "other side" in selector.apply(strict, other, "winner")["reason"]
    assert "selector abstain" in selector.apply({"status": "abstain", "reason": "r"}, item, "winner")["reason"]
    chosen, reasons = selector.choose_market({"winner": (s, {"lower": 0.05}), "total": (s, {"lower": 0.09}),
                                              "spread": ({"status": "no_pick", "reason": "x"}, {"lower": None})})
    assert chosen == "total" and "outranked" in reasons["winner"] and reasons["spread"] == "x"


# --- retrospective study machinery (synthetic data) --------------------------------------------------------------------
def _study_tables():
    from app import combination_research as cr
    from app.models.hockey import GoalsPoisson, HockeyElo
    rows = hockey_rows(per_season=300)
    tables, first = cr._tables(rows, [HockeyElo(HockeyEloConfig()), GoalsPoisson(GoalsConfig())],
                               [GoalsPoisson(GoalsConfig()), GoalsDraw(GoalsConfig()), GoalsGlm(HockeyEloConfig())],
                               ("reg3", "total"), None, lambda r: r.decision)
    first["hockey_elo"] = first["goals_poisson"] = 2016
    return tables, first


def test_study_compares_on_identical_games_and_stacks_out_of_fold():
    from app import combination_research as cr
    tables, first = _study_tables()
    out, picks = cr.analyse("reg3", tables["reg3"], ("goals_poisson", "goals_draw", "goals_glm"), first, (2019, 2020, 2021))
    n = out["games"]
    assert all(m["n"] == n for m in out["methods"].values())
    fitted = [f for f in out["stack"]["fits"] if f["fitted"]]
    assert fitted and all(max(f["training_seasons"]) < f["season"] for f in fitted)
    assert {a["rule"] for a in out["agreement"]} >= {"majority", "unanimous", "pair:goals_poisson+goals_draw"}
    assert out["selector_simulation"]["summary"]["weeks"] > 0
    # winner market: two members, the line from nothing
    w, _ = cr.analyse("winner", tables["winner"], ("hockey_elo", "goals_poisson"), first, (2019, 2020, 2021))
    assert w["games"] == n


def test_stacker_refuses_training_results_not_available_before_its_season():
    from app import combination_research as cr
    tables, first = _study_tables()
    rows = [r for r in tables["total"] if all(m in r["vectors"] for m in ("goals_poisson", "goals_draw", "goals_glm"))]
    late = next(r for r in rows if r["season"] == 2019)
    late["result_available_at"] = "2030-01-01T00:00:00Z"
    with pytest.raises(ValueError, match="not available"):
        cr._stack_walk("total", rows, ("goals_poisson", "goals_draw", "goals_glm"), first, (2020,))


def test_regulation_draw_is_an_outcome_and_integer_lines_push():
    assert selector.outcome("reg3", 0, 1) == "loss"          # picked home, regulation draw: a loss, never a refund
    assert selector.outcome("reg3", 1, 1) == "win"           # a draw pick that lands
    assert selector.outcome("total", 0, 1) == "push"         # integer line landed exactly
    assert selector.outcome("spread", 2, 0) == "loss"
    items = _items(400, 0.5, market="reg3")
    for i in items:
        i["vectors"] = {"p": (0.5, 0.3, 0.2)}
        i["realised"] = 1
    s = selector.evaluate(items, "reg3", ["p"])[0]
    assert s["pushes"] == 0 and s["losses"] == 400
