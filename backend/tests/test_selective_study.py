"""Selective-combination study (docs/MODEL_SELECTION.md §15): rule semantics, conditions, pushes, three-way, selection."""
from app import selective_study as ss


def rule(kind, members, t=None, level="all"):
    return {"kind": kind, "members": members, "t": t, "level": level}


V = {"a": [0.80, 0.0, 0.20], "b": [0.70, 0.0, 0.30], "c": [0.45, 0.0, 0.55]}


def test_rules_use_member_probabilities_only():
    assert ss.side_of(rule("unanimous_min", ["a", "b"], 0.70), V, "winner") == 0
    assert ss.side_of(rule("unanimous_min", ["a", "b"], 0.75), V, "winner") is None      # b below the minimum
    assert ss.side_of(rule("unanimous_maxdis", ["a", "b"], 0.06), V, "winner") is None   # 0.10 apart
    assert ss.side_of(rule("unanimous_maxdis", ["a", "b"], 0.10), V, "winner") == 0
    assert ss.side_of(rule("majority", ["a", "b", "c"]), V, "winner") == 0
    assert ss.side_of(rule("unanimous_min", ["a", "missing"], 0.5), V, "winner") is None  # a missing member abstains
    assert ss.side_of(rule("stack", ["a", "b"], 0.6), V, "winner") is None               # no stacker vector: abstain


def test_three_way_draw_pick_and_pushes_are_not_wins():
    v = {"a": [0.30, 0.42, 0.28], "b": [0.29, 0.45, 0.26]}
    assert ss.side_of(rule("unanimous_min", ["a", "b"], 0.40), v, "reg3") == 1
    rows = [{"game_id": "g1", "realised": 1, "vectors": V, "favourite": 0.7, "disagreement": 0.1},
            {"game_id": "g2", "realised": 0, "vectors": V, "favourite": 0.7, "disagreement": 0.1}]
    st = ss.stats(ss.outcomes("spread", rows, rule("single", ["a"], 0.55)), 2)
    assert (st["wins"], st["losses"], st["pushes"], st["accuracy"]) == (1, 0, 1, 1.0)


def test_conditions_and_registry_bounds():
    r = {"favourite": 0.55, "disagreement": 0.02}
    assert ss.in_level(r, "close") and ss.in_level(r, "low") and not ss.in_level(r, "clear")
    assert not ss.in_level({"favourite": 0.7, "disagreement": None}, "low")
    members = ["elo", "logistic", "logistic_v2", "efficiency_margin", "gbt_v1"]
    rules = ss.registry("nba", "winner", members, True)
    assert len(rules) == 179 * 5 and len({r["id"] for r in rules}) == len(rules)
    assert len(ss.registry("nhl", "reg3", ["goals_poisson", "goals_draw", "goals_glm"], True)) == 51 * 5


def test_selection_never_reads_the_assessed_season():
    rows = []
    for i in range(300):
        for season in (2022, 2023):
            y = 0 if i % 4 else 2
            rows.append({"game_id": f"{season}-{i}", "season": season, "cutoff_at": f"{season - 1}-12-{1 + i % 28:02d}T05:00:00Z",
                         "realised": y if season == 2022 else 2 - y, "favourite": 0.7, "disagreement": 0.02,
                         "vectors": {"a": [0.8, 0, 0.2], "b": [0.66, 0, 0.34]}})
    rules = ss.registry("t", "winner", ["a", "b"], False)
    f = ss.fold("winner", rows, ["a", "b"], rules, [2022], 2023)
    flipped = [dict(r, realised=0) if r["season"] == 2023 else r for r in rows]
    g = ss.fold("winner", flipped, ["a", "b"], rules, [2022], 2023)
    assert f["all_rules"] == g["all_rules"] and list(f["assessed"]) == list(g["assessed"])
