"""gapfill-v1 (docs/MODEL_SELECTION.md §16): registered family order and budget, earlier-only selection, tie abstention,
route B with/without a candidate."""
import random
from datetime import datetime, timedelta

from app import gapfill_study as gf, selective_study as ss


def test_family_order_budget_and_omissions_are_fixed_before_results():
    members = ["a", "b", "c", "x", "y"]
    rules, plan = gf.family(members, ["x", "y"], "winner", budget=200)
    assert len(rules) <= 200
    assert plan["evaluated"]["full"] == ["a+b+c+x+y"] and len(plan["evaluated"]["pair"]) == 10
    tri = plan["evaluated"].get("triple", []) + plan["omitted"].get("triple", [])
    assert len(tri) == 10
    # mixed (candidate + existing) triples come before the existing-only triple
    assert tri.index("a+b+c") == 9
    assert rules == gf.family(members, ["x", "y"], "winner", budget=200)[0]     # deterministic
    big, plan_big = gf.family(members, ["x", "y"], "winner")
    assert not plan_big["omitted"] and len(big) == 12 + 10 * 6 + 10 * 9 + 5 * 4


def test_majority_of_four_abstains_on_a_two_two_split():
    rule = {"kind": "majority", "members": ["a", "b", "c", "d"], "t": None}
    v = {"a": (0.7, 0, 0.3), "b": (0.6, 0, 0.4), "c": (0.4, 0, 0.6), "d": (0.3, 0, 0.7)}
    assert ss.side_of(rule, v, "winner") is None
    v["d"] = (0.55, 0, 0.45)
    assert ss.side_of(rule, v, "winner") == 0


def _rows(seasons=(2022, 2023, 2024), n=400, seed=1):
    rng = random.Random(seed)
    rows, t = [], datetime(2021, 10, 1)
    for s in seasons:
        for i in range(n):
            t += timedelta(hours=9)
            y = rng.random() < 0.5
            signal = 0.62 if y else 0.38                      # "x" is informative only together with "a" (noise cancels)
            noise = rng.uniform(-0.3, 0.3)
            a = min(0.95, max(0.05, signal + noise))
            x = min(0.95, max(0.05, signal - noise))
            rows.append({"game_id": f"g{s}-{i}", "season": s, "cutoff_at": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "tipoff_at": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "result_available_at": (t + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "line": None, "realised": 0 if y else 2,
                         "vectors": {"a": (a, 0.0, 1 - a), "b": (0.5 + rng.uniform(-0.1, 0.1), 0.0, 0.0),
                                     "x": (x, 0.0, 1 - x)}})
    for r in rows:
        r["vectors"]["b"] = (r["vectors"]["b"][0], 0.0, 1 - r["vectors"]["b"][0])
    return rows


def test_selection_uses_earlier_seasons_only():
    rows = _rows()
    a = gf.analyse("winner", rows, ["a", "b", "x"], ["x"], [2022, 2023, 2024], 0, "a")
    flipped = [dict(r) for r in rows]
    for r in flipped:
        if r["season"] == 2024:
            r["realised"] = 2 - r["realised"]
    b = gf.analyse("winner", flipped, ["a", "b", "x"], ["x"], [2022, 2023, 2024], 0, "a")
    for fa, fb in zip(a["folds"], b["folds"]):
        assert sorted(fa["assessed"]) == sorted(fb["assessed"])          # frozen rules do not depend on the assess season
        assert fa["best_single"] == fb["best_single"]
    assert a["folds"][1]["assessed"] != b["folds"][1]["assessed"] or not a["folds"][1]["assessed"]


def test_route_b_measures_a_candidate_only_inside_its_combination():
    a = gf.analyse("winner", _rows(), ["a", "b", "x"], ["x"], [2022, 2023, 2024], 0, "a")
    rb = a["probability"]["route_b"]["a+x vs without x"]
    assert rb["mean"] < 0 and rb["ci95"][1] < 0                            # adding x to a helps
    assert "x vs without x" not in a["probability"]["route_b"]             # never alone
