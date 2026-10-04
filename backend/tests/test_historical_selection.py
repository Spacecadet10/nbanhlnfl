"""Historical selection without odds, the historical starting policy, selector coverage, NHL draw-fit parity, the earlier
NHL market test label, narrower research identities and explicit tracking membership. Synthetic data only."""
import json
import random
from datetime import datetime, timedelta, timezone

import pytest

from app import agreement, agreement_tracking, combination_research as cr, combine, historical_start, selector
from app.candidates import registry
from app.models import score_candidates as sc
from test_pool_selection import FMT, START, add_cohort, add_game, settle, store  # noqa: F401  (fixture)


def _items(n, accuracy, book=None, line=None, seed=3):
    """Two-way winner items over many weeks: source "p" picks the realised side with probability `accuracy` at 0.7."""
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        realised = 0 if rnd.random() < 0.5 else 2
        right = rnd.random() < accuracy
        side = realised if right else 2 - realised
        vec = (0.7, 0.0, 0.3) if side == 0 else (0.3, 0.0, 0.7)
        out.append({"game_id": f"g{i}", "week": f"2020-W{i // 20:02d}", "vectors": {"p": vec},
                    "agreement": {"majority_side": None, "unanimous_side": None},
                    "bookmaker": book, "line": line, "realised": realised})
    return out


def test_historical_basis_selects_without_odds_where_the_live_basis_cannot():
    items = _items(400, 0.8)                       # no bookmaker quote anywhere (history has none)
    live = selector.decide(items, "winner", ["p"])
    assert live["status"] == "insufficient_evidence" and live["judged_items"] == 0
    hist = selector.decide(items, "winner", ["p"], basis="historical")
    assert hist["status"] == "active" and hist["rule"]["source"] == "p" and hist["basis"] == "historical"
    assert hist["lower"] > 0


def test_historical_reference_is_the_base_rate_at_the_same_line_not_fifty_percent():
    rnd = random.Random(5)
    items = []
    for i in range(400):               # line -1.5: home covers ~30%; line +1.5: home covers ~75%
        line = -1.5 if i % 2 else 1.5
        realised = 0 if rnd.random() < (0.3 if line == -1.5 else 0.75) else 2
        items.append({"game_id": str(i), "week": "w", "vectors": {}, "agreement": {}, "bookmaker": None, "line": line,
                      "realised": realised})
    rates = selector.reference_rates(items, "spread", "historical")
    neg, pos = rates[("line", -1.5)][0], rates[("line", 1.5)][0]
    assert neg < 0.4 < 0.65 < pos
    assert selector.reference({"line": -1.5, "bookmaker": None}, 0, rates, "spread", "historical") == neg
    assert selector.reference({"line": 1.5, "bookmaker": None}, 2, rates, "spread", "historical") == pytest.approx(1 - pos)
    # A line with too few games is not judged: never pooled with other lines, never a flat 50%.
    assert selector.reference({"line": 2.5, "bookmaker": None}, 0, rates, "spread", "historical") is None
    with pytest.raises(ValueError):
        selector.reference({"line": 1.5}, 0, rates, "spread", "neither")


def test_live_coverage_counts_only_quoted_games_and_their_pushes():
    items = []
    for i in range(10):
        quoted = i < 4
        items.append({"game_id": str(i), "week": f"w{i}", "vectors": {"p": (0.8, 0.0, 0.2)}, "agreement": {},
                      "bookmaker": (0.5, 0.0, 0.5) if quoted else None, "realised": 1 if i in (0, 5, 6) else 0})
    s = next(x for x in selector.evaluate(items, "spread", ["p"]) if x["filter"] == "none" and x["threshold"] == 0.5)
    # Quoted: games 0-3 -> one push (game 0), three wins. Unquoted pushes (5, 6) and wins are not counted anywhere.
    assert (s["wins"], s["pushes"], s["picks"]) == (3, 1, 3)
    assert s["coverage"] == pytest.approx(4 / 4)
    h = next(x for x in selector.evaluate(items, "spread", ["p"], basis="historical") if x["filter"] == "none"
             and x["threshold"] == 0.5)
    # Historical: these 10 games share one line, below MIN_LINE_REFERENCE: none is judged, none counts in coverage.
    assert (h["wins"], h["pushes"], h["judged_items"], h["coverage"]) == (0, 0, 0, None)


def _rows(n, seed=7):
    """Walk-forward-like rows: members a, b; 'stack' formed on the second half only (a combination that often fails)."""
    rnd = random.Random(seed)
    t0 = datetime(2019, 10, 1, tzinfo=timezone.utc)
    out = []
    for i in range(n):
        realised = 0 if rnd.random() < 0.5 else 2
        good = (0.75, 0.0, 0.25) if realised == 0 else (0.25, 0.0, 0.75)
        bad = (0.75, 0.0, 0.25) if rnd.random() < 0.5 else (0.25, 0.0, 0.75)
        tip = t0 + timedelta(hours=8 * i)
        vec = {"a": good if rnd.random() < 0.8 else tuple(reversed(good)), "b": bad}
        if i >= n // 2:
            vec["stack"] = vec["a"]
        out.append({"game_id": f"g{i}", "season": 2019, "tipoff_at": tip.strftime(FMT), "cutoff_at": tip.strftime(FMT),
                    "result_available_at": (tip + timedelta(hours=3)).strftime(FMT), "line": None, "realised": realised,
                    "vectors": vec})
    return out


def test_weekly_historical_simulation_picks_single_members_without_a_combination():
    rows = _rows(900)
    del rows[10]["vectors"]["b"]                 # a member missing on one game must not break agreement
    sim, picks = cr._simulate("winner", rows, ["a", "b", "stack"], ["a", "b"])
    assert sim["summary"]["picks"] > 100 and sim["summary"]["active_weeks"] > 0
    assert sim["summary"]["reference"] == cr.HISTORICAL_REFERENCE
    used = {json.loads(r)["source"] for r in sim["summary"]["rules_used"]}
    assert "a" in used                           # chosen while the stack did not exist yet
    assert all(p["reference"] is not None for p in picks)


def test_draw_fit_terms_are_the_predicted_grid_and_refit_maximises_its_likelihood():
    rnd = random.Random(11)
    for _ in range(50):
        lh, la, w = rnd.uniform(1.5, 4.5), rnd.uniform(1.5, 4.5), rnd.choice(sc.DRAW_GRID)
        pmf = sc.regulation_pmf(lh, la, w, empty_net_goals=True)
        for (h, a) in [(0, 0), (1, 0), (2, 0), (3, 1), (0, 3), (5, 2), (4, 4)]:
            c, d, tie = sc._draw_fit_terms(lh, la, h, a)
            assert (1 - w) * c + (w * c / d if tie else 0.0) == pytest.approx(pmf[(h, a)], rel=1e-12, abs=1e-15)
    model = sc.GoalsDraw.__new__(sc.GoalsDraw)
    model.examples, model.fits, model.first_ready_season, model.weight = [], [], None, None
    for _ in range(sc.MIN_HOCKEY_EXAMPLES):
        lh, la = rnd.uniform(2.4, 3.6), rnd.uniform(2.2, 3.2)
        h, a = min(8, int(rnd.expovariate(1 / lh))), min(8, int(rnd.expovariate(1 / la)))
        model.examples.append((2019, lh, la, (h, a) if rnd.random() > 0.1 else (h, h)))
    model._refit(2020)
    exact = max(sc.DRAW_GRID, key=lambda w: (sum(__import__("math").log(sc.regulation_pmf(lh, la, w, True)[s])
                                                  for _, lh, la, s in model.examples), -w))
    assert model.weight == exact


def test_earlier_nhl_market_test_is_labelled_as_a_different_model(tmp_path, monkeypatch):
    from app.providers import real
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    d = tmp_path / "reports" / "nhl-test-1"
    d.mkdir(parents=True)
    entry = {"goals_poisson": {"n": 10, "log_loss": 0.6, "brier": 0.2, "pick_accuracy_excluding_mid": 0.6},
             "league_poisson": {"log_loss": 0.61}}
    (d / "report.json").write_text(json.dumps({"markets": {"spread -1.5": entry, "spread +1.5": entry}}))
    now = real._market_reference("nhl", "spread", "goals_poisson-eta0.01-c0.75-empty_net=l1=0.29")
    assert now["matches_live_model"] is False and "Earlier model version" in now["model_note"]
    same = real._market_reference("nhl", "spread", "goals_poisson-eta0.01-c0.75")
    assert same["matches_live_model"] is True and same["model_note"] is None


def _names(files):
    return {f.name for f in files}


def test_agreement_identity_hashes_only_the_files_its_members_use():
    from app.models.efficiency import EfficiencyMargin
    from app.models.elo import Elo, EloConfig
    from app.models.gbt import GradientBoostedTrees
    from app.models.hockey import GoalsConfig, GoalsPoisson, HockeyElo, HockeyEloConfig, hockey_gbt, hockey_logistic
    from app.models.logistic import LogisticElo, LogisticV2
    cfg, ec, gc = EloConfig(), HockeyEloConfig(), GoalsConfig()
    nba = _names(agreement.model_files([Elo(cfg), LogisticElo(cfg), LogisticV2(cfg), EfficiencyMargin(), GradientBoostedTrees(cfg)]))
    nhl = _names(agreement.model_files([HockeyElo(ec), GoalsPoisson(gc), hockey_logistic(ec, gc), hockey_gbt(ec, gc)]))
    assert {"elo.py", "logistic.py", "gbt.py", "efficiency.py", "base.py"} <= nba
    assert not nba & {"hockey.py", "score_candidates.py", "points.py", "markets.py"}       # NHL / line edits: no NBA reset
    assert {"hockey.py", "markets.py", "logistic.py", "gbt.py"} <= nhl                      # what the NHL members run
    assert "score_candidates.py" not in nhl
    from app.models.points import PointsConfig, PointsNormal
    from app.models.score_candidates import GbtPoints, RidgePoints
    from app.retrain_cadence import Cadenced
    lines = _names(agreement.model_files([PointsNormal(PointsConfig()), RidgePoints(cfg), GbtPoints(cfg),
                                          Cadenced(GbtPoints(cfg), "weekly")]))
    assert {"points.py", "score_candidates.py", "retrain_cadence.py"} <= lines
    # The wrapper's study helpers (combination_research, nhl_eval) and the NHL goal models are not NBA line identity.
    assert not lines & {"combination_research.py", "nhl_eval.py", "hockey.py", "selector.py"}


def test_tracking_excludes_an_unregistered_optional_group_explicitly(monkeypatch):
    spec = registry.Spec("netx:nhl_base", "nhl", "mlp", "binary", "nhl_base", "candidate", "test", extra="deep")
    monkeypatch.setattr(registry, "SPECS", list(registry.SPECS) + [spec])
    assert spec.name not in agreement_tracking.candidates_for("nhl", "winner", live_ok=True)
    assert spec.name not in agreement_tracking.candidates_for("nhl", "winner", live_ok=False)


def test_historical_policy_sources_only_where_the_live_meaning_is_the_same():
    s, f, ex = historical_start._sources("nba", "winner", ["elo", "logistic", "logistic_v2", "efficiency_margin", "gbt_v1"])
    assert "availability_two_hour" in ex and f == ("none",) and "average" in s and "stack" in s
    s, f, ex = historical_start._sources("nhl", "winner", ["hockey_elo", "goals_poisson", "hockey_logistic", "hockey_gbt"])
    assert f == selector.FILTERS and "hockey_logistic_xg" in ex
    s, f, ex = historical_start._sources("nhl", "spread", ["goals_poisson", "goals_draw"])
    assert "average" not in s and "average" in ex


def test_policy_applies_only_to_its_definition_after_freezing_and_keeps_the_bookmaker_apart(store):  # noqa: F811
    frozen = "2026-10-06T05:00:00Z"
    doc = {"definition": "core-test-defn", "frozen_at": frozen,
           "markets": {"spread": {"status": "active", "rule": {"source": "goals_poisson", "filter": "none", "threshold": 0.55},
                                  "lower": 0.01, "reason": "test"}}}
    for i, (gen_h, defn, p, book) in enumerate([(-30, "core-test-defn", 0.7, (0.6, 0.0, 0.4)),  # before freezing
                                                (8, "core-test-defn", 0.7, (0.6, 0.0, 0.4)),    # quoted pick, wins
                                                (14, "core-test-defn", 0.7, None),              # unquoted pick, loses
                                                (20, "other-defn", 0.7, (0.5, 0.0, 0.5)),       # another definition
                                                (26, "core-test-defn", 0.52, None)]):           # below threshold
        tip = START + timedelta(hours=gen_h + 1)
        gid, tipoff = f"nhl:h{i}", tip.strftime(FMT)
        add_game(store, gid, tipoff)
        add_cohort(store, gid, tipoff, (tip - timedelta(hours=1)).strftime(FMT), defn=defn, members_p=(p, 0.0, 1 - p), book=book)
        settle(store, gid, tipoff, 4 if i != 2 else 1, 1 if i != 2 else 3)
    store.commit()
    live = historical_start.live_record(store, doc, "spread", "2026-10-09T00:00:00Z")
    assert live["cohorts_before_freeze"] == 1 and live["cohorts_after_freeze"] == 3
    assert (live["picks"], live["wins"], live["losses"]) == (2, 1, 1)
    assert live["bookmaker"]["quoted_decided"] == 1 and live["bookmaker"]["mean_implied"] == pytest.approx(0.6)


def test_sparse_asymmetric_lines_are_not_pooled_into_apparent_skill():
    """Codex's reproduction: 900 games at -1.5 with 50% home covers; five other lines of 20 games each with 80% home
    covers. The source says home .8 only on those 100 games (exactly their base rate) and ties elsewhere. Pooling the
    sparse lines with -1.5 made this look like +0.27 lift; unsupported lines must not be judged."""
    items, rnd = [], random.Random(13)
    for i in range(900):
        items.append({"game_id": f"m{i}", "week": f"w{i % 30}", "line": -1.5, "bookmaker": None, "agreement": {},
                      "vectors": {"p": (0.5, 0.0, 0.5)}, "realised": 0 if i % 2 else 2})
    for k, line in enumerate((-3.5, -2.5, 0.5, 2.5, 3.5)):
        for i in range(20):
            items.append({"game_id": f"s{k}-{i}", "week": f"w{i}", "line": line, "bookmaker": None, "agreement": {},
                          "vectors": {"p": (0.8, 0.0, 0.2)}, "realised": 0 if rnd.random() < 0.8 else 2})
    d = selector.decide(items, "spread", ["p"], basis="historical")
    assert d["status"] != "active"
    assert all(s["picks"] == 0 for s in d["stats"])          # the sparse-line picks are not judged at all
    assert d["judged_items"] == 900


def test_historical_stacker_is_refit_like_the_live_one():
    from app import research_live
    rows = []
    for season in (2017, 2018, 2019, 2020):
        for i in range(400):
            tip = datetime(season, 11, 1, tzinfo=timezone.utc) + timedelta(hours=10 * i)
            y = 0 if (i * 7 + season) % 3 else 2
            rows.append({"season": season, "game_id": f"{season}-{i}", "cutoff_at": tip.strftime(FMT),
                         "result_available_at": (tip + timedelta(hours=3)).strftime(FMT), "realised": y,
                         "vectors": {"a": (0.6, 0.0, 0.4) if y == 0 else (0.45, 0.0, 0.55), "b": (0.55, 0.0, 0.45)}})
    fits = historical_start._live_stack_walk("winner", rows, rows, ["a", "b"], [2018, 2019, 2020])
    assert [f["training_seasons"] for f in fits] == [[2017], [2017, 2018], [2018, 2019]]   # last STACK_SEASONS, as live
    assert research_live.STACK_SEASONS == 2 and all(f["fitted"] for f in fits)
    assert all("stack" in r["vectors"] for r in rows if r["season"] >= 2018)
    assert not any("stack" in r["vectors"] for r in rows if r["season"] == 2017)


def test_roster_reports_earlier_version_activity_apart_from_the_current(store, monkeypatch):  # noqa: F811
    from app.candidates import live as pool_live
    monkeypatch.setattr(pool_live, "_current_definitions", lambda conn: ("digest-now", "core-test-defn"))
    store.execute("INSERT INTO model_fits (sport, member, definition, schedule, fit_kind, trained_season, training_games, "
                  "results_fingerprint, results_available_through, params_digest, libraries, first_used_at) VALUES "
                  "('nhl','goals_draw','older-defn','season_start','season_start',2026,100,'f','2026-09-01T00:00:00Z','p','{}',"
                  "'2026-10-02T00:00:00Z')")
    tip = START + timedelta(hours=2)
    add_game(store, "nhl:v1", tip.strftime(FMT))
    add_cohort(store, "nhl:v1", tip.strftime(FMT), (tip - timedelta(hours=1)).strftime(FMT), defn="older-defn")
    store.commit()
    m = {(x["markets"], x["name"]): x for x in pool_live.core_members(store, "nhl", "2026-10-07T00:00:00Z")}
    draw = m[("line and 3-way markets", "goals_draw")]
    assert draw["fit_register"] is None and draw["earlier_fit"]["definition"] == "older-defn"
    assert draw["forecasting"] is False and draw["earlier_forecast_at"] is not None
