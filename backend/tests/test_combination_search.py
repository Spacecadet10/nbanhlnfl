"""Combination search (docs/MODEL_SELECTION.md §11): rule semantics, matched-coverage comparator, earlier-data selection."""
from app import combination_search as cs


def row(gid, season, vecs, realised, fav=0.7, tip="2023-01-10T00:00:00Z"):
    return {"game_id": gid, "season": season, "tipoff_at": tip, "cutoff_at": tip, "vectors": vecs, "realised": realised,
            "favourite": fav}


def test_rule_semantics_use_no_outcome():
    r = row("g", 2023, {"a": (0.7, 0, 0.3), "b": (0.66, 0, 0.34), "c": (0.4, 0, 0.6)}, 0)
    rule = lambda kind, ms, t=None: {"kind": kind, "members": ms, "t": t}
    assert cs.side_of(rule("unanimous", ["a", "b"]), r, "winner") == 0
    assert cs.side_of(rule("unanimous_similar", ["a", "b"]), r, "winner") == 0          # 0.70 vs 0.66: within 0.05
    assert cs.side_of(rule("unanimous", ["a", "c"]), r, "winner") is None
    assert cs.side_of(rule("majority", ["a", "b", "c"]), r, "winner") == 0
    assert cs.side_of(rule("avg", ["a", "c"], 0.55), r, "winner") == 0                # average 0.55: thresholds are inclusive
    assert cs.side_of(rule("avg", ["a", "c"], 0.56), r, "winner") is None
    assert cs.side_of(rule("unanimous_avg", ["a", "b"], 0.70), r, "winner") is None
    flipped = dict(r, realised=2)
    assert cs.side_of(rule("unanimous", ["a", "b"]), flipped, "winner") == 0              # outcome never consulted


def test_conditions_are_fixed_pregame_levels():
    early = row("g", 2023, {}, 0, fav=0.59, tip="2022-11-20T00:00:00Z")
    late = row("h", 2023, {}, 0, fav=0.61, tip="2023-02-01T00:00:00Z")
    assert cs.in_level(early, "close") and cs.in_level(early, "early") and not cs.in_level(early, "clear")
    assert cs.in_level(late, "clear") and cs.in_level(late, "late")


def test_selection_never_reads_the_assessed_season():
    rows = []
    for i in range(300):   # 2022: member a is right whenever confident; 2023: the opposite
        rows.append(row(f"s{i}", 2022, {"a": (0.8, 0, 0.2), "b": (0.6, 0, 0.4)}, 0 if i % 4 else 2))
        rows.append(row(f"t{i}", 2023, {"a": (0.8, 0, 0.2), "b": (0.6, 0, 0.4)}, 2 if i % 4 else 0))
    rules = cs.registry("winner", ["a", "b"], False)
    f = cs.fold("winner", rows, ["a", "b"], rules, [2022], 2023)
    again = cs.fold("winner", [r if r["season"] == 2022 else dict(r, realised=0) for r in rows], ["a", "b"], rules, [2022], 2023)
    assert f["qualified"] == again["qualified"] and f["top_point_rule"] == again["top_point_rule"]
    assert f["best_single"] in ("a", "b") and f["rules_tried"] == len(rules)


def test_matched_comparator_counts_the_same_number_of_picks():
    curve = [1, 1, None, 0, 1]
    assert cs._matched(curve, 3) == (1.0, 2) and cs._matched(curve, 5) == (0.75, 4)
