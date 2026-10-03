"""discovery-v1 (MODEL_SELECTION §18): confidence definitions, abstentions, three-way picks, earlier-only training and
selection, budget order."""
import random
from datetime import datetime, timedelta

import numpy as np

from app import discovery_study as ds


def rows(n_per=300, seasons=(2020, 2021, 2022, 2023), market="winner", seed=1):
    rng = random.Random(seed)
    out, t = [], datetime(2019, 10, 1)
    for s in seasons:
        for i in range(n_per):
            t += timedelta(hours=7)
            y = 0 if rng.random() < 0.55 else 2
            sig = 0.65 if y == 0 else 0.35
            v = {}
            for m, noise in (("a", 0.15), ("b", 0.25), ("c", 0.35)):
                p = min(0.95, max(0.05, sig + rng.uniform(-noise, noise)))
                v[m] = (p, 0.0, 1 - p)
            out.append({"game_id": f"g{s}-{i}", "season": s, "cutoff_at": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "tipoff_at": (t + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "result_available_at": (t + timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "line": None, "realised": y, "vectors": v})
    return out


def arrays(vecs, realised, market="winner"):
    rs = [{"game_id": str(i), "season": 2020, "cutoff_at": "2020-01-01T00:00:00Z", "tipoff_at": "2020-01-01T01:00:00Z",
           "result_available_at": "2020-01-01T04:00:00Z", "line": None, "realised": y, "vectors": v}
          for i, (v, y) in enumerate(zip(vecs, realised))]
    return ds.Arrays(market, rs, sorted(vecs[0]))


def test_confidence_definitions_are_kept_separate():
    ar = arrays([{"a": (0.8, 0, 0.2), "b": (0.6, 0, 0.4), "c": (0.3, 0, 0.7)}], [0])
    side, conf, V, _ = ds.side_conf(ar, {}, {"method": "unanimous_min", "members": ["a", "b"]})
    assert side[0] == 0 and abs(conf[0] - 0.6) < 1e-12 and V is None          # minimum member probability
    side, conf, V, _ = ds.side_conf(ar, {}, {"method": "avg", "members": ["a", "b"]})
    assert side[0] == 0 and abs(conf[0] - 0.7) < 1e-12                         # averaged probability
    side, conf, _, votes = ds.side_conf(ar, {}, {"method": "majority", "members": ["a", "b", "c"]})
    assert side[0] == 0 and votes[0] == 2 and abs(conf[0] - 0.7) < 1e-12        # mean of the two supporting members
    side, _, _, _ = ds.side_conf(ar, {}, {"method": "unanimous_min", "members": ["a", "c"]})
    assert side[0] == -1                                                       # disagreement abstains


def test_majority_ties_and_exact_probability_ties_abstain():
    ar = arrays([{"a": (0.7, 0, 0.3), "b": (0.3, 0, 0.7), "c": (0.5, 0, 0.5), "d": (0.6, 0, 0.4)}], [0])
    side, _, _, _ = ds.side_conf(ar, {}, {"method": "majority", "members": ["a", "b", "c", "d"]})
    assert side[0] == -1                                                       # 2 of 4 (c abstains): no strict majority
    assert ds.side_conf(ar, {}, {"method": "single", "members": ["c"]})[0][0] == -1


def test_three_way_picks_can_be_a_draw_and_draws_are_scored():
    ar = arrays([{"a": (0.30, 0.40, 0.30)}, {"a": (0.50, 0.20, 0.30)}], [1, 1], market="reg3")
    side, conf, V, _ = ds.side_conf(ar, {}, {"method": "single", "members": ["a"]})
    assert list(side) == [1, 0]
    out = ar.outcome(side)
    assert list(out) == [1, 0]                                                 # a draw is a result, never a push
    st = ds.stats(out, conf, np.array([True, True]), 2, V, ar.realised, three_way=True)
    assert st["wins"] == 1 and st["losses"] == 1 and st["pushes"] == 0 and st["log_loss"] > 0


def test_stackers_use_only_earlier_seasons():
    rs = rows()
    ar = ds.Arrays("winner", rs, ["a", "b", "c"])
    m1, _ = ds.train_stackers(ar, [2020, 2021, 2022, 2023])
    flipped = [dict(r) for r in rs]
    for r in flipped:
        if r["season"] == 2023:
            r["realised"] = 2 - r["realised"]
    m2, log = ds.train_stackers(ds.Arrays("winner", flipped, ["a", "b", "c"]), [2020, 2021, 2022, 2023])
    early = ar.season < 2023
    for k in m1:
        assert np.allclose(np.nan_to_num(m1[k][early]), np.nan_to_num(m2[k][early]))   # later outcomes change nothing earlier
        assert np.isnan(m1[k][ar.season == 2020]).all()                                 # no fit in the first season
    assert log["stack_l2"][0]["training_seasons"] == [2020]


def test_family_order_and_budget():
    groups, plan = ds.family(["a", "b", "c", "x"], {"x"}, ["stack_l2"], "winner", budget=200)
    assert plan["configurations"] <= 200
    assert plan["evaluated"]["single"] == ["a", "b", "c", "x", "stack_l2"]
    assert len(plan["evaluated"]["pair"]) == 6
    trip = plan["evaluated"].get("triple", []) + plan["omitted"].get("triple", [])
    assert trip.index("a+b+c") == 3                                            # the existing-only triple comes last
    assert any(g["method"] == "stack" for g in groups)


def test_analyse_chooses_on_earlier_seasons_only():
    rs = rows()
    a, _ = ds.analyse("nhl", "winner", rs, budget=400, log=lambda *_: None)
    flipped = [dict(r) for r in rs]
    for r in flipped:
        if r["season"] == 2023:
            r["realised"] = 2 - r["realised"]
    b, _ = ds.analyse("nhl", "winner", flipped, budget=400, log=lambda *_: None)
    fa, fb = a["folds"][-1], b["folds"][-1]
    assert [x["group"] for x in fa["assessed"]] == [x["group"] for x in fb["assessed"]]
    assert fa["best_single"] == fb["best_single"]
