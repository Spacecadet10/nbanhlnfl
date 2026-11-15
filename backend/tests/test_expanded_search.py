"""Expanded-combination experiment (docs/MODEL_SELECTION.md §14): registered family and constituent comparisons."""
from app import expanded_search as ex

NHL = ["hockey_elo", "goals_poisson", "hockey_logistic", "hockey_gbt", "hockey_logistic_xg"]
NBA = ["points_normal", "ridge_points", "gbt_points", "ridge_points@weekly", "gbt_points@weekly"]


def test_registered_family_sizes_and_new_members_in_every_combination():
    subsets, stacks = ex.nhl_subsets(NHL)
    rules = ex.registry(subsets, stacks, NHL)
    assert len(rules) == 36 and all("hockey_logistic_xg" in s for s in subsets)
    subsets, stacks = ex.nba_subsets(NBA)
    rules = ex.registry(subsets, stacks, NBA)
    assert len(rules) == 27 and all(set(s) & set(ex.NBA_WEEKLY) for s in subsets)
    assert all(set(ms) & set(ex.NBA_WEEKLY) for ms in stacks.values())


def row(gid, season, vecs, realised):
    return {"game_id": gid, "season": season, "tipoff_at": f"{season - 1}-12-15T00:00:00Z", "cutoff_at": f"{season - 1}-12-15T05:00:00Z",
            "vectors": vecs, "realised": realised}


def test_a_rule_must_beat_every_constituent_and_selection_ignores_the_assessed_season():
    rows = []
    for i in range(400):
        for season in (2018, 2019):
            # a is confident and usually right; x adds nothing: unanimous(a, x) cannot beat constituent a
            y = 0 if i % 5 else 2
            rows.append(row(f"{season}-{i}", season, {"a": (0.8, 0, 0.2), "x": (0.6, 0, 0.4)}, y if season == 2018 else 2 - y))
    rules = [{"kind": "unanimous", "members": ["a", "x"], "t": None, "level": "all", "id": "u"}]
    f = ex.fold("winner", rows, ["a", "x"], rules, [2018], 2019)
    r = f["all_rules"][0]
    assert set(r["vs_constituents"]) == {"a", "x"} and not r["qualifies"]
    flipped = [dict(x, realised=0) if x["season"] == 2019 else x for x in rows]
    assert ex.fold("winner", flipped, ["a", "x"], rules, [2018], 2019)["all_rules"] == f["all_rules"]
