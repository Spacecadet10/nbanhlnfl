"""Prospective tracking of registered combination rules (docs/MODEL_SELECTION.md §15) on stored cohorts."""
import json

import pytest

from app import rule_tracking as rt
from test_research_live import GID, game, run  # noqa: F401  (fixture)
from test_live_markets import sealed  # noqa: F401
from test_nhl_eval import nhl_store  # noqa: F401


def registry(effective="2026-10-01T00:00:00Z"):
    members = ["hockey_elo", "goals_poisson", "hockey_logistic", "hockey_gbt", "hockey_logistic_xg"]
    rules = [
        {"id": "single|hockey_elo|0.5|all", "kind": "single", "members": ["hockey_elo"], "t": 0.5, "level": "all",
         "selected": "unqualified research", "why": "test", "prospective_only": False, "historical": []},
        {"id": "unanimous_min|hockey_elo+hockey_logistic_xg|0.5|all", "kind": "unanimous_min",
         "members": ["hockey_elo", "hockey_logistic_xg"], "t": 0.5, "level": "all", "selected": None, "why": None,
         "prospective_only": False, "historical": []},
        {"id": "single|goals_glm|0.99|all", "kind": "single", "members": ["goals_glm"], "t": 0.99, "level": "all",
         "selected": "unqualified research", "why": "test", "prospective_only": True, "historical": []}]
    reg = {"study": "selective-v1", "version": 1, "effective_from": effective,
           "markets": {"nhl:winner": {"sport": "nhl", "market": "winner", "cohort": "core", "members": members,
                                      "comparator": "hockey_elo", "frontier": [], "rules": rules}}}
    return rt.seal(reg)


def test_registry_seal_detects_any_change(tmp_path):
    reg = registry()
    p = tmp_path / "r.json"
    p.write_text(json.dumps(reg))
    assert rt.load_registry(p)["sha256"] == reg["sha256"]
    reg["effective_from"] = "2020-01-01T00:00:00Z"
    p.write_text(json.dumps(reg))
    with pytest.raises(rt.RegistryError):
        rt.load_registry(p)


def test_one_canonical_decision_per_game_and_abstention_reasons(game):
    run(game, "2026-10-12T21:30:00Z")
    run(game, "2026-10-12T21:40:00Z")                     # retries never add games
    rep = rt.report(game, "nhl", "winner", "all", registry())
    assert rep["cohorts"] == 1
    single = next(r for r in rep["rules"] if r["id"].startswith("single|hockey_elo"))
    assert single["prospective"]["picks"] == 1 and single["prospective"]["pending"] == 1 and single["prospective"]["accuracy"] is None
    glm = next(r for r in rep["rules"] if r["id"].startswith("single|goals_glm"))
    assert glm["prospective"]["picks"] == 0 and glm["prospective"]["recent"][0]["reason"].startswith("member missing")
    assert len(rt.report(game, "nhl", "winner", "selected", registry())["rules"]) == 2


def test_cohorts_before_the_effective_date_do_not_count(game):
    run(game, "2026-10-12T21:30:00Z")
    rep = rt.report(game, "nhl", "winner", "all", registry(effective="2026-10-12T22:00:00Z"))
    assert rep["cohorts"] == 0 and all(r["prospective"]["picks"] == 0 for r in rep["rules"])


def test_settled_outcomes_count_wins_and_compare_at_matched_coverage(game):
    from test_live_markets import add_upcoming
    run(game, "2026-10-12T21:30:00Z")
    add_upcoming(game, start="2026-10-12T19:00:00", state=7, hs=4, vs=1, period=3, at="2026-10-13T02:00:00Z")
    rep = rt.report(game, "nhl", "winner", "all", registry())
    single = next(r for r in rep["rules"] if r["id"].startswith("single|hockey_elo"))["prospective"]
    assert single["win"] + single["loss"] == 1 and single["pending"] == 0
    assert single["strongest_single_matched"]["games"] == 1
