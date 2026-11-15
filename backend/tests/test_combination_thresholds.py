"""Thresholds, agreement and exact subsets on saved out-of-sample forecasts (combination_thresholds): synthetic tables only."""
import gzip
import json
import random
from itertools import combinations, product

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import combination_thresholds as ct
from app import main


def _rows(seed=1, seasons=(2020, 2021, 2022, 2023), per=150, models=("a", "b", "c"), market="winner"):
    rng = random.Random(seed)
    out = []
    for s in seasons:
        for i in range(per):
            truth = rng.random()
            realised = 0 if rng.random() < truth else 2
            vec = {}
            for k, m in enumerate(models):
                p = min(0.97, max(0.03, truth + rng.gauss(0, 0.08 + 0.04 * k)))
                vec[m] = [p, 0.0, 1 - p]
            day = f"{s}-{1 + i % 9:02d}-{10 + i % 18:02d}"
            out.append({"game_id": f"{s}-{i}", "season": s, "cutoff_at": f"{day}T12:00:00Z", "tipoff_at": f"{day}T23:00:00Z",
                        "result_available_at": f"{day}T23:59:00Z", "line": None, "realised": realised, "vectors": vec})
    return out


def _brute(rows, market, model, lo, hi, seasons):
    """Picks, wins, losses, pushes of `model` with lo <= confidence < hi (hi None = no upper bound)."""
    from app import combine, selector
    picks = w = l = p = 0
    for r in rows:
        if r["season"] not in seasons or model not in r["vectors"]:
            continue
        v = r["vectors"][model]
        side = combine.pick(v, market)
        if side is None:
            continue
        conf = v[side] / sum(v[i] for i in combine.sides(market))
        if conf < lo - 1e-12 or (hi is not None and conf >= hi - 1e-12):
            continue
        picks += 1
        o = selector.outcome(market, side, r["realised"])
        w, l, p = w + (o == "win"), l + (o == "loss"), p + (o == "push")
    return picks, w, l, p


def test_cumulative_and_band_rows_match_a_direct_count_and_exclude_pushes_from_the_win_rate():
    rows = _rows(models=("a", "b"))
    for r in rows[::5]:
        r["realised"] = 1                     # a push in a two-way line market
    t = ct.Table("spread", rows, ["a", "b"])
    out = ct.thresholds(t, ["a", "b"])
    a = next(s for s in out["sources"] if s["key"] == "a")
    later = {2022, 2023}
    for r in a["later"]["cumulative"]:
        picks, w, l, p = _brute(rows, "spread", "a", r["lo"], None, later)
        assert (r["picks"], r["wins"], r["losses"], r["pushes"]) == (picks, w, l, p)
        assert r["win_rate"] == pytest.approx(w / (w + l))
    for r in a["later"]["bands"]:
        hi = None if r["hi"] == 1.0 else r["hi"]
        assert (r["picks"], r["wins"], r["losses"]) == _brute(rows, "spread", "a", r["lo"], hi, later)[:3]
    assert sum(r["picks"] for r in a["later"]["bands"]) == a["later"]["cumulative"][0]["picks"]
    assert [r["rule"] for r in a["earlier"]["cumulative"]][:2] == ["50%+", "55%+"]
    assert a["earlier"]["bands"][-1]["rule"] == "90–100%"
    assert any(s["kind"] == "average" for s in out["sources"])


def test_a_certain_pick_falls_in_the_top_band():
    """Bands partition the cumulative record: a confidence of exactly 1.0 belongs to the top band (it fell in none)."""
    rows = [{"game_id": f"g{i}", "season": 2020, "tipoff_at": f"2020-01-0{i+1}T00:00:00Z", "line": None, "realised": 0,
             "vectors": {"m": [1.0, 0.0, 0.0] if i < 3 else [0.95, 0.0, 0.05]}} for i in range(6)]
    t = ct.Table("winner", rows, ["m"], later_seasons=[])
    lad = ct._ladder(t, "m", t.present[:, 0], np.ones(6, bool), ct._levels("winner"))
    assert lad["cumulative"][-1]["picks"] == 6
    assert lad["bands"][-1]["picks"] == 6 and sum(b["picks"] for b in lad["bands"]) == 6


def test_three_way_picks_below_fifty_percent_are_kept_in_their_own_bands():
    rows = []
    for i, (ph, pd) in enumerate([(0.38, 0.30), (0.42, 0.30), (0.47, 0.25), (0.60, 0.20)]):
        rows.append({"game_id": str(i), "season": 2020 + i % 2 * 3, "cutoff_at": "2020-01-01T12:00:00Z",
                     "tipoff_at": f"2020-01-0{i + 1}T23:00:00Z", "line": None, "realised": 0,
                     "vectors": {"a": [ph, pd, 1 - ph - pd]}})
    t = ct.Table("reg3", rows, ["a"], later_seasons=[2023])
    out = ct.thresholds(t, ["a"])["sources"][0]
    bands = {r["rule"]: r["picks"] for r in out["earlier"]["bands"] + out["later"]["bands"] if r["picks"]}
    assert bands == {"35–40%": 1, "40–45%": 1, "45–50%": 1, "60–65%": 1}
    assert out["earlier"]["cumulative"][0]["rule"] == "33.3%+"


def test_a_missing_member_excludes_the_game_and_never_turns_partial_agreement_into_unanimity():
    rows = _rows(models=("a", "b", "c"))
    for r in rows[::4]:
        del r["vectors"]["c"]
    t = ct.Table("winner", rows, ["a", "b", "c"])
    ag = ct.agreement(t, ["a", "b", "c"])
    assert ag["excluded_games_missing_member"] == len(rows[::4])
    for pn in ("earlier", "later"):
        for r in ag["by_count"][pn]:
            assert r["of"] == 3
            assert (r["kind"] == "unanimous") == (r["agreeing"] == 3)
        all_picks = [r for r in ag["by_count"][pn] if r["level"] == 0.5]
        eligible = sum(1 for r in rows if "c" in r["vectors"] and r["season"] in ({2020, 2021} if pn == "earlier" else {2022, 2023}))
        assert sum(r["picks"] for r in all_picks) == eligible
        assert all_picks[0]["eligible"] == eligible
    rule = ct.evaluate_rule(t, ["a", "c"], [0.5, 0.5])
    assert rule["excluded_games_missing_member"] == len(rows[::4])


def test_subset_search_counts_equal_direct_rule_evaluation_with_unequal_member_thresholds():
    rows = _rows(models=("a", "b", "c", "d"), per=300)
    t = ct.Table("winner", rows, ["a", "b", "c", "d"])
    grid = ct.TWO_WAY_GRID
    rng = random.Random(7)
    early = t.periods["earlier"] & t.settled
    for members in (("a", "b"), ("b", "d"), ("a", "c", "d")):
        for ths in rng.sample(list(product(grid, repeat=len(members))), 8):
            ev = ct.evaluate_rule(t, list(members), list(ths))["earlier"]["record"]
            # direct count: all members pick the same side, each at its own threshold
            w = l = 0
            for i, r in enumerate(rows):
                if not early[i]:
                    continue
                sides = {t.side[m][i] for m in members}
                if len(sides) != 1 or -1 in sides or any(t.conf[m][i] < x - 1e-12 for m, x in zip(members, ths)):
                    continue
                code = t.O[i, sides.pop()]
                w, l = w + (code == 1), l + (code == 0)
            assert (ev["wins"], ev["losses"]) == (w, l)
    out = ct.search_subsets(t, ["a", "b", "c", "d"], "a")
    assert out["per_size"] == {"2": {"subsets": 6, "rules": 6 * 49}, "3": {"subsets": 4, "rules": 4 * 343}}
    assert out["rules_searched"] == 6 * 49 + 4 * 343
    for f in out["frozen"][:5]:      # the search's own counts agree with the rule's direct evaluation
        assert f["earlier"]["record"]["wins"] + f["earlier"]["record"]["losses"] >= ct.MIN_RULE_DECIDED


def test_frozen_rules_ignore_the_later_period():
    rows = _rows(models=("a", "b", "c"), per=400)
    first = ct.search_subsets(ct.Table("winner", rows, ["a", "b", "c"]), ["a", "b", "c"], "a")
    flipped = json.loads(json.dumps(rows))
    for r in flipped:
        if r["season"] >= 2022:
            r["realised"] = 2 - r["realised"]
    second = ct.search_subsets(ct.Table("winner", flipped, ["a", "b", "c"]), ["a", "b", "c"], "a")
    assert [f["rule"] for f in first["frozen"]] == [f["rule"] for f in second["frozen"]]
    assert [f["rule"] for f in first["frozen_lift"]] == [f["rule"] for f in second["frozen_lift"]]
    assert [f["earlier"] for f in first["frozen"]] == [f["earlier"] for f in second["frozen"]]
    assert first["frozen"][0]["later"]["record"] != second["frozen"][0]["later"]["record"]


def test_adding_a_model_partitions_picks_into_retained_switched_new_and_dropped():
    rows = _rows(models=("a", "b", "c"))
    t = ct.Table("winner", rows, ["a", "b", "c"])
    out = ct.add_model(t, ["a"], ["b", "c"])
    for row in out["rows"]:
        for pn in ("earlier", "later"):
            for x in row[pn]:
                assert x["base"]["picks"] == x["retained"]["picks"] + x["switched_base"]["picks"] + x["dropped"]["picks"]
                assert x["with"]["picks"] == x["retained"]["picks"] + x["switched_with"]["picks"] + x["new"]["picks"]
                assert x["base"]["wins"] == x["retained"]["wins"] + x["switched_base"]["wins"] + x["dropped"]["wins"]


def test_rule_detail_compares_members_alone_and_a_coverage_matched_best_single():
    rows = _rows(models=("a", "b"), per=300)
    t = ct.Table("winner", rows, ["a", "b"])
    ev = ct.evaluate_rule(t, ["a", "b"], [0.6, 0.7], best_single="a")
    for pn in ("earlier", "later"):
        rec = ev[pn]["record"]
        m = ev[pn]["coverage_matched_best_single"]
        assert m["picks"] == rec["wins"] + rec["losses"] + rec["pushes"]
        for alone in ev[pn]["alone"]:      # the member's own picks = the rule's picks + the member's excluded picks
            assert alone["own"]["picks"] == rec["picks"] + alone["excluded"]["picks"]
        assert set(ev[pn]["member_mean_p"]) == {"a", "b"}
        assert ev[pn]["member_mean_p"]["b"] >= 0.7 - 1e-9
    with pytest.raises(ValueError):
        ct._parse_rule("a@0.6,a@0.7", ["a", "b"])
    with pytest.raises(ValueError):
        ct._parse_rule("z@0.6", ["a", "b"])
    assert ct._parse_rule("a@0.6,b@0.7", ["a", "b"]) == (["a", "b"], [0.6, 0.7])


def _write_pool(base, sport, rows):
    d = base / "research" / "candidates"
    d.mkdir(parents=True, exist_ok=True)
    body = {"sport": sport, "stage": "screen", "seasons": sorted({r["season"] for r in rows}), "tables": {"winner": rows}}
    (d / f"oof-{sport}-screen.json.gz").write_bytes(gzip.compress(json.dumps(body).encode()))


def test_api_demo_withheld_real_served_adaptive_source_listed_and_bad_input_rejected(tmp_path, monkeypatch):
    _write_pool(tmp_path, "nba", _rows(seed=3))
    ad = tmp_path / "research" / "adaptive" / "nba"
    ad.mkdir(parents=True)
    arows = _rows(seed=4, models=("cand:x@adaptive", "cand:y@adaptive"))
    head = {"kind": "adaptive-candidate-history", "sport": "nba", "definition": "0123456789abcdef", "in_force": True,
            "exported_at": "2026-10-10T00:00:00Z", "not_stored": [{"model": "elo", "reason": "walk member"}]}
    (ad / "oof-0123456789abcdef.json.gz").write_bytes(gzip.compress(json.dumps({"head": head, "tables": {"winner": arows}}).encode()))
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(main.app, "dependency_overrides", {})
    ct._table.cache_clear()
    ct._search.cache_clear()
    c = TestClient(main.app)
    monkeypatch.setenv("NBA_API_MODE", "demo")
    for path in ("/api/research/thresholds", "/api/research/subsets"):
        demo = c.get(path).json()
        assert demo["mode"] == "demo" and demo["status"] == "unavailable" and demo["models"] == []
    monkeypatch.setenv("NBA_API_MODE", "real")
    real = c.get("/api/research/thresholds", params={"sport": "nba", "rule": "a@0.6,b@0.65"}).json()
    assert real["status"] == "available" and real["rule"]["members"] == ["a", "b"]
    assert [s["key"] for s in real["sources"]] == ["pool", "adaptive:0123456789abcdef"]
    assert "nothing here measures profit" in real["note"] and real["win_rate_denominator"].startswith("wins + losses")
    adaptive = c.get("/api/research/thresholds", params={"sport": "nba", "source": "adaptive:0123456789abcdef"}).json()
    assert adaptive["models"] == ["cand:x@adaptive", "cand:y@adaptive"] and adaptive["not_stored"][0]["model"] == "elo"
    sub = c.get("/api/research/subsets", params={"sport": "nba"}).json()
    assert sub["status"] == "available" and sub["rules_searched"] == 3 * 49 + 343
    empty = c.get("/api/research/thresholds", params={"sport": "nba", "market": "spread",
                                                       "source": "adaptive:0123456789abcdef"}).json()
    assert empty["status"] == "no_stored_forecasts"       # an empty source still lists every source, so the UI can switch back
    assert [s["key"] for s in empty["sources"]] == ["pool", "adaptive:0123456789abcdef"]
    assert c.get("/api/research/thresholds", params={"sport": "nba", "group": "a,zz"}).status_code == 422
    assert c.get("/api/research/thresholds", params={"sport": "nba", "rule": "a@2"}).status_code == 422
    assert c.get("/api/research/thresholds", params={"sport": "nba", "source": "../etc"}).status_code == 422
    assert c.get("/api/research/thresholds", params={"sport": "nba", "source": "adaptive:ffffffffffffffff"}).status_code == 422
    ct._table.cache_clear()
    ct._search.cache_clear()


def _shortlist_rows():
    """120 earlier games where A, B, C are all confident on the home side (identical earlier picks for every subset), then four
    later games: AB qualifies later games 1, 2, 4 and AC qualifies 1, 3, 4 (the Codex review reproduction)."""
    rows = []
    for i in range(120):
        day = f"2021-{1 + i % 9:02d}-{10 + i % 18:02d}"
        rows.append({"game_id": f"e{i}", "season": 2021, "cutoff_at": f"{day}T12:00:00Z", "tipoff_at": f"{day}T23:{i % 60:02d}:00Z",
                     "line": None, "realised": 0 if i % 6 else 2,
                     "vectors": {m: [0.85, 0.0, 0.15] for m in "ABC"}})
    later = {1: "ABC", 2: "AB", 3: "AC", 4: "ABC"}
    for g, hi in later.items():
        rows.append({"game_id": f"l{g}", "season": 2023, "cutoff_at": f"2023-01-0{g}T12:00:00Z", "tipoff_at": f"2023-01-0{g}T23:00:00Z",
                     "line": None, "realised": 0,
                     "vectors": {m: ([0.85, 0.0, 0.15] if m in hi else [0.4, 0.0, 0.6]) for m in "ABC"}})
    return rows


def _shortlists(rows):
    t = ct.Table("winner", rows, ["A", "B", "C"], later_seasons=[2023])
    out = ct.search_subsets(t, ["A", "B", "C"], "A")
    return [f["rule"] for f in out["frozen"]], [f["rule"] for f in out["frozen_lift"]]


def test_later_outcomes_never_change_the_frozen_shortlists():
    rows = _shortlist_rows()
    base = _shortlists(rows)
    for gid in ("l1", "l2", "l3", "l4"):
        changed = json.loads(json.dumps(rows))
        next(r for r in changed if r["game_id"] == gid)["realised"] = 2
        assert _shortlists(changed) == base, f"flipping later game {gid} changed the frozen shortlist"


def test_later_forecast_presence_never_changes_the_frozen_shortlists():
    rows = _shortlist_rows()
    base = _shortlists(rows)
    for gid in ("l1", "l2", "l3", "l4"):
        changed = json.loads(json.dumps(rows))
        del next(r for r in changed if r["game_id"] == gid)["vectors"]["C"]
        assert _shortlists(changed) == base, f"removing C's later forecast on {gid} changed the frozen shortlist"


def test_rules_are_collapsed_only_when_their_earlier_picks_are_identical():
    out = ct.search_subsets(ct.Table("winner", _shortlist_rows(), ["A", "B", "C"], later_seasons=[2023]), ["A", "B", "C"], "A")
    assert len(out["frozen"]) == 1          # every rule picks the same 120 earlier games on the same side: one representative
    assert out["duplicates_collapsed"]["reliable"] > 0


def test_rule_detail_has_same_games_comparison_and_seasons_that_sum_to_the_record():
    rows = _rows(models=("a", "b", "c"), per=300)
    t = ct.Table("winner", rows, ["a", "b", "c"])
    ev = ct.evaluate_rule(t, ["a", "b"], [0.6, 0.7], best_single="c")
    for pn in ("earlier", "later"):
        rec, same = ev[pn]["record"], ev[pn]["same_games_best_single"]
        assert same["picks"] + same["abstained"] == rec["picks"] and not same["member"]
        assert same["same_side"] <= rec["picks"]
        assert sum(s["picks"] for s in ev[pn]["by_season"]) == rec["picks"]
        assert sum(s["wins"] for s in ev[pn]["by_season"]) == rec["wins"]
    member = ct.evaluate_rule(t, ["a", "b"], [0.6, 0.7], best_single="a")["later"]
    assert member["same_games_best_single"]["member"]
    assert (member["same_games_best_single"]["wins"], member["same_games_best_single"]["losses"]) == \
        (member["record"]["wins"], member["record"]["losses"])      # a member's record on the rule's games is the rule's


def test_ledger_lists_every_qualifying_game_with_member_probabilities_and_csv(tmp_path, monkeypatch):
    rows = _rows(seed=5, models=("a", "b", "c"))
    _write_pool(tmp_path, "nba", rows)
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    ct._table.cache_clear()
    led = ct.ledger("nba", "winner", "pool", "a@0.6,b@0.65", "later")
    ev = ct.explore("nba", "winner", "pool", rule="a@0.6,b@0.65")["rule"]["later"]["record"]
    assert led["games"] == ev["picks"] and led["exploratory"]
    assert sum(r["result"] == "win" for r in led["rows"]) == ev["wins"]
    assert sum(r["result"] == "loss" for r in led["rows"]) == ev["losses"]
    for r in led["rows"]:
        assert r["period"] == "later" and {m["pick"] for m in r["members"]} == {r["shared_pick"]}
        assert r["members"][0]["p"] >= 0.6 - 1e-9 and r["members"][1]["p"] >= 0.65 - 1e-9
    # Review finding D1: the ledger carries the rule members' earlier-period pick overlap, so two members that agree by
    # construction are not shown as independent agreeing members.
    overlap = led["member_overlap"]
    assert overlap["period"] == "earlier" and [(p["a"], p["b"]) for p in overlap["pairs"]] == [("a", "b")]
    assert overlap["pairs"][0]["games"] > 0 and 0.0 <= overlap["pairs"][0]["share"] <= 1.0 and "not independent" in overlap["note"]
    csv_text = ct.ledger_csv(led)
    assert csv_text.splitlines()[0].startswith("date (US Eastern),game_id") and len(csv_text.splitlines()) == led["games"] + 1
    with pytest.raises(ValueError):
        ct.ledger("nba", "winner", "pool", "a@0.6", "sometime")
    ct._table.cache_clear()


# --- Model presence through the actual loading path (pool tables and adaptive exports) ---------------------------------
def _write_adaptive(base, sport, rows, definition="0123456789abcdef"):
    ad = base / "research" / "adaptive" / sport
    ad.mkdir(parents=True, exist_ok=True)
    head = {"kind": "adaptive-candidate-history", "sport": sport, "definition": definition, "in_force": True,
            "exported_at": "2026-10-10T00:00:00Z", "not_stored": []}
    (ad / f"oof-{definition}.json.gz").write_bytes(gzip.compress(json.dumps({"head": head, "tables": {"winner": rows}}).encode()))


def _search_view(out):
    return {"models": out["models"], "rules_searched": out["rules_searched"], "best_single": out["best_single"],
            "per_size": out["per_size"], "earlier": out["periods"]["earlier"],
            "frozen": [(f["rule"], f["earlier"]["record"]) for f in out["frozen"]],
            "frozen_lift": [(f["rule"], f["earlier"]["record"]) for f in out["frozen_lift"]]}


def _subsets_from_files(base, monkeypatch, rows, source):
    (_write_pool if source == "pool" else _write_adaptive)(base, "nba", rows)
    monkeypatch.setenv("NBA_DATA_DIR", str(base))
    ct._table.cache_clear()
    ct._search.cache_clear()
    try:
        return ct.subsets("nba", "winner", source)
    finally:
        ct._table.cache_clear()
        ct._search.cache_clear()


@pytest.mark.parametrize("source", ["pool", "adaptive:0123456789abcdef"])
@pytest.mark.parametrize("change", ["drop_one_later_forecast", "drop_every_later_forecast", "model_only_in_later_period",
                                    "flip_later_outcomes"])
def test_later_presence_or_outcomes_never_change_the_searched_models_or_shortlists(tmp_path, monkeypatch, source, change):
    rows = _rows(models=("a", "b", "c"))                      # seasons 2020–2023: the later period is 2022–2023
    changed = json.loads(json.dumps(rows))
    later = [r for r in changed if r["season"] >= 2022]
    if change == "drop_one_later_forecast":
        del later[5]["vectors"]["c"]
    elif change == "drop_every_later_forecast":
        for r in later:
            del r["vectors"]["c"]
    elif change == "model_only_in_later_period":
        for r in later:
            r["vectors"]["late"] = [0.99, 0.0, 0.01] if r["realised"] == 0 else [0.01, 0.0, 0.99]
    else:
        for r in later:
            r["realised"] = 2 - r["realised"]
    base = _subsets_from_files(tmp_path / "base", monkeypatch, rows, source)
    out = _subsets_from_files(tmp_path / "changed", monkeypatch, changed, source)
    assert base["rules_searched"] == 3 * 49 + 343
    assert _search_view(out) == _search_view(base)
    assert base["coverage"]["later"] == {"games": 300, "settled": 300, "pending": 0, "used": 300, "excluded_missing_member": 0, "missing": {}}
    if change == "drop_one_later_forecast":
        assert out["coverage"]["later"] == {"games": 300, "settled": 300, "pending": 0, "used": 299, "excluded_missing_member": 1, "missing": {"c": 1}}
    if change == "drop_every_later_forecast":
        assert out["coverage"]["later"] == {"games": 300, "settled": 300, "pending": 0, "used": 0, "excluded_missing_member": 300, "missing": {"c": 300}}
    if change == "model_only_in_later_period":
        assert [e["model"] for e in out["excluded"]] == ["late"] and "later" in out["excluded"][0]["reason"]


# --- Honest states and explicit coverage (loaded through the saved tables) ------------------------------------------------
def _both_from_files(base, monkeypatch, rows, source):
    (_write_pool if source == "pool" else _write_adaptive)(base, "nba", rows)
    monkeypatch.setenv("NBA_DATA_DIR", str(base))
    ct._table.cache_clear()
    ct._search.cache_clear()
    try:
        return ct.explore("nba", "winner", source), ct.subsets("nba", "winner", source)
    finally:
        ct._table.cache_clear()
        ct._search.cache_clear()


SOURCES = ["pool", "adaptive:0123456789abcdef"]


@pytest.mark.parametrize("source", SOURCES)
def test_no_earlier_later_split_is_insufficient_in_both_views(tmp_path, monkeypatch, source):
    ex, sub = _both_from_files(tmp_path, monkeypatch, _rows(seasons=(2022, 2023)), source)
    for out in (ex, sub):
        assert out["status"] == "insufficient_evidence" and "more than 2 seasons" in out["reason"]
        assert out["models"] == [] and "frozen" not in out and "thresholds" not in out
        assert {e["model"] for e in out["excluded"]} == {"a", "b", "c"}


@pytest.mark.parametrize("source", SOURCES)
def test_no_settled_earlier_game_is_insufficient_in_both_views(tmp_path, monkeypatch, source):
    rows = _rows()
    for r in rows:
        if r["season"] < 2022:
            r["realised"] = None
    ex, sub = _both_from_files(tmp_path, monkeypatch, rows, source)
    for out in (ex, sub):
        assert out["status"] == "insufficient_evidence" and out["reason"] == "no settled game in the earlier period"
        assert "frozen" not in out


def test_saved_pool_forecasts_without_an_eligible_model_are_not_no_stored_forecasts(tmp_path, monkeypatch):
    rows = _rows()
    early = [r for r in rows if r["season"] < 2022]
    for i, m in enumerate("abc"):
        del early[i]["vectors"][m]
    ex, sub = _both_from_files(tmp_path, monkeypatch, rows, "pool")
    for out in (ex, sub):
        assert out["status"] == "insufficient_evidence" and "no model is eligible" in out["reason"]
        assert {e["model"] for e in out["excluded"]} == {"a", "b", "c"}


def test_a_valid_search_where_no_rule_has_enough_picks_stays_available(tmp_path, monkeypatch):
    _, sub = _both_from_files(tmp_path, monkeypatch, _rows(per=20), "pool")      # 40 earlier games < MIN_RULE_DECIDED
    assert sub["status"] == "available" and sub["rules_with_min_picks"] == 0
    assert sub["frozen"] == [] and sub["frozen_lift"] == [] and sub["rules_searched"] == 3 * 49 + 343


def test_coverage_matches_the_combinations_card_and_summary_discloses_both_periods(tmp_path, monkeypatch):
    from app import combination_research as cr
    rows = _rows()
    early = [r for r in rows if r["season"] < 2022]
    later = [r for r in rows if r["season"] >= 2022]
    for r in later[:3]:
        r["realised"] = None
    del later[3]["vectors"]["c"]
    del later[0]["vectors"]["b"]
    ex, sub = _both_from_files(tmp_path, monkeypatch, rows, "adaptive:0123456789abcdef")
    del early[7]["vectors"]["a"]                       # adaptive keeps a partial model; the pool would exclude it
    ex2, _ = _both_from_files(tmp_path / "partial", monkeypatch, rows, "adaptive:0123456789abcdef")
    assert ex["coverage"]["later"] == sub["coverage"]["later"] == {
        "games": 300, "settled": 297, "pending": 3, "used": 296, "excluded_missing_member": 1, "missing": {"c": 1}}
    assert ex2["coverage"]["earlier"] == {"games": 300, "settled": 300, "pending": 0, "used": 299,
                                          "excluded_missing_member": 1, "missing": {"a": 1}}
    assert ex["coverage_definition"] == cr.COVERAGE_DEFINITION
    md = ct._summary_md(ex2, sub)
    assert "Missing earlier forecasts" in md and "a 1 of 300 settled" in md
    assert "Missing later forecasts" in md and "c 1 of 297 settled" in md
    ov = sub["later_overlap"]["reliable"]
    assert ct._count(ov['beats_matched']['distinct_later_pick_sets'], "distinct later pick set") + "." in md
    assert "rules make " + ct._count(ov['distinct_later_pick_sets'], "distinct later pick set") in md and "not independent" in md


def test_unavailable_summary_states_the_reason(tmp_path, monkeypatch):
    ex, sub = _both_from_files(tmp_path, monkeypatch, _rows(seasons=(2022, 2023)), "pool")
    assert "more than 2 seasons" in ct._summary_md(ex, sub)


# --- Distinct later pick sets of the frozen rules (descriptive overlap only) ----------------------------------------------
def _rule(members, ths):
    return {"members": members, "thresholds": ths, "rule": ct._rule_label(members, ths)}


def test_identical_later_records_on_different_games_are_different_pick_sets():
    t = ct.Table("winner", _shortlist_rows(), ["A", "B", "C"], later_seasons=[2023])
    ab, ac = ct.evaluate_rule(t, ["A", "B"], [0.8, 0.8]), ct.evaluate_rule(t, ["A", "C"], [0.8, 0.8])
    assert (ab["later"]["record"]["wins"], ab["later"]["record"]["losses"]) == (ac["later"]["record"]["wins"], ac["later"]["record"]["losses"]) == (3, 0)
    ov = ct.later_pick_sets(t, [_rule(["A", "B"], [0.8, 0.8]), _rule(["A", "C"], [0.8, 0.8])])
    assert ov["rules"] == 2 and ov["distinct_later_pick_sets"] == 2 and ov["set_of_rule"] == [1, 2] and ov["shared"] == []


def test_genuinely_identical_later_picks_share_one_set():
    t = ct.Table("winner", _shortlist_rows(), ["A", "B", "C"], later_seasons=[2023])
    # every later forecast is 85% or 40%: thresholds of 70% and 80% qualify exactly the same games and sides
    ov = ct.later_pick_sets(t, [_rule(["A", "B"], [0.8, 0.8]), _rule(["A", "C"], [0.8, 0.8]), _rule(["A", "B"], [0.7, 0.7])])
    assert ov["distinct_later_pick_sets"] == 2 and ov["set_of_rule"] == [1, 2, 1]
    assert ov["shared"] == [{"set": 1, "rules": [1, 3], "picks": 3}]


def test_same_games_with_opposite_picked_sides_are_different_pick_sets():
    rows = [{"game_id": f"e{i}", "season": 2021, "cutoff_at": "2021-01-01T12:00:00Z", "tipoff_at": f"2021-01-01T23:{i:02d}:00Z",
             "line": None, "realised": 0, "vectors": {"A": [0.9, 0.0, 0.1], "B": [0.9, 0.0, 0.1]}} for i in range(5)]
    rows += [{"game_id": f"l{i}", "season": 2023, "cutoff_at": "2023-01-01T12:00:00Z", "tipoff_at": f"2023-01-01T23:{i:02d}:00Z",
              "line": None, "realised": 0, "vectors": {"A": [0.9, 0.0, 0.1], "B": [0.1, 0.0, 0.9]}} for i in range(3)]
    t = ct.Table("winner", rows, ["A", "B"], later_seasons=[2023])
    ov = ct.later_pick_sets(t, [_rule(["A"], [0.8]), _rule(["B"], [0.8])])
    assert ov["distinct_later_pick_sets"] == 2          # the same three game IDs, picked on opposite sides


def _shared_later_picks_rows(earlier=(2021,), later=(2023,)):
    """Earlier seasons: three disjoint game groups where exactly one pair (AB, AC or BC) is confident on the home side while
    the third model picks away, so every pair has DIFFERENT earlier picks (nothing is collapsed), each has ≥ MIN_RULE_DECIDED
    decided picks, and no triple qualifies. Later seasons: ten games where A, B and C all pick home at different probabilities
    (0.85, 0.80, 0.90), one of them pending, so the three frozen pairs make exactly the same later picks with different
    member probabilities."""
    rows, results = [], {"AB": [0] * 100 + [2] * 20, "AC": [0] * 96 + [2] * 24, "BC": [0] * 90 + [2] * 30}
    for season in earlier:
        for pair, outcomes in results.items():
            for i, realised in enumerate(outcomes):
                day = f"{season}-{1 + i % 9:02d}-{10 + i % 18:02d}"
                rows.append({"game_id": f"{season}{pair}{i}", "season": season, "cutoff_at": f"{day}T12:00:00Z",
                             "tipoff_at": f"{day}T23:{i % 60:02d}:00Z", "line": None, "realised": realised,
                             "vectors": {m: ([0.85, 0.0, 0.15] if m in pair else [0.4, 0.0, 0.6]) for m in "ABC"}})
    for g in range(10):
        season = later[g % len(later)]
        rows.append({"game_id": f"l{g}", "season": season, "cutoff_at": f"{season}-01-{10 + g:02d}T12:00:00Z",
                     "tipoff_at": f"{season}-01-{10 + g:02d}T23:00:00Z", "line": None, "realised": None if g == 9 else (0 if g < 7 else 2),
                     "vectors": {"A": [0.85, 0.0, 0.15], "B": [0.80, 0.0, 0.20], "C": [0.90, 0.0, 0.10]}})
    return rows


# Identical picks (same games, same sides) force identical outcomes; a rule's probabilities depend on its members.
SAME_PICKS_FIELDS = ("picks", "wins", "losses", "pushes", "pending", "win_rate", "win_rate_ci95", "first", "last")
MEMBER_DEPENDENT_FIELDS = ("mean_p", "calibration_gap")


def test_rules_with_different_members_sharing_later_picks_match_on_outcomes_but_not_probabilities():
    t = ct.Table("winner", _shared_later_picks_rows(), ["A", "B", "C"], later_seasons=[2023])
    out = ct.search_subsets(t, ["A", "B", "C"], "A")
    for name, key in (("reliable", "frozen"), ("lift", "frozen_lift")):
        ov, rules = out["later_overlap"][name], out[key]
        assert sorted(tuple(f["members"]) for f in rules) == [("A", "B"), ("A", "C"), ("B", "C")]   # different members, nothing collapsed
        assert ov["rules"] == 3 and ov["distinct_later_pick_sets"] == 1 and ov["set_of_rule"] == [1, 1, 1]
        assert ov["shared"] == [{"set": 1, "rules": [1, 2, 3], "picks": 10}]
        assert [f["later_pick_set"] for f in rules] == ov["set_of_rule"]
        recs = [f["later"]["record"] for f in rules]
        assert recs[0]["picks"] == 10 and recs[0]["pending"] == 1 and (recs[0]["wins"], recs[0]["losses"]) == (7, 2)
        for a, b in combinations(recs, 2):
            assert {k: a[k] for k in SAME_PICKS_FIELDS} == {k: b[k] for k in SAME_PICKS_FIELDS}
        assert len({round(r["mean_p"], 6) for r in recs}) == 3            # (0.85+0.80)/2, (0.85+0.90)/2, (0.80+0.90)/2
        assert len({round(r["calibration_gap"], 6) for r in recs}) == 3
        beats = out["later_beats_matched"][name]
        assert ov["beats_matched"]["rules"] == beats and ov["beats_matched"]["distinct_later_pick_sets"] <= min(beats, 1)
    lbs = [f["earlier_wilson_lower"] for f in out["frozen"]]
    assert lbs == sorted(lbs, reverse=True)                  # still ranked on the earlier period only
    assert "not independent" in out["later_overlap_definition"]


def test_overlap_wording_claims_identical_picks_and_outcomes_not_identical_records():
    text = ct.LATER_OVERLAP_DEFINITION
    assert "identical later records" not in text
    assert "same picks" in text and "win" in text and "probabilit" in text


def test_summary_counts_use_singular_and_plural_correctly(tmp_path, monkeypatch):
    # four seasons so the file-based split exists (the last two are the later period); the frozen pairs are the same
    ex, sub = _both_from_files(tmp_path, monkeypatch, _shared_later_picks_rows(earlier=(2019, 2020), later=(2022, 2023)), "pool")
    ov = sub["later_overlap"]["reliable"]
    assert ov["rules"] == 3 and ov["distinct_later_pick_sets"] == 1 and ov["beats_matched"]["distinct_later_pick_sets"] <= 1
    md = ct._summary_md(ex, sub)
    assert "The 3 rules make 1 distinct later pick set;" in md and "1 distinct later pick sets" not in md
    assert ("0 distinct later pick sets." in md) or ("1 distinct later pick set." in md)
    assert "(10 picks)" in md and ct._count(1, "pick") == "1 pick" and ct._count(2, "rule") == "2 rules"


@pytest.mark.parametrize('market', ['winner', 'spread', 'total', 'team_total_home', 'team_total_away', 'reg3'])
@pytest.mark.parametrize('vector', [[.6, float('nan'), .4], [.6, 0., float('inf')], [.8, .3, -.1], [.2, .2, .2]])
def test_threshold_tables_reject_invalid_saved_vectors(market, vector):
    rows = _rows(per=1, models=('a',), market=market)
    rows[0]['vectors']['a'] = vector
    with pytest.raises(ValueError, match='invalid saved probability'):
        ct.Table(market, rows, ['a'])


def test_exported_adaptive_history_is_in_force_per_training_schedule(tmp_path, monkeypatch):
    """Reviewer reproduction (2026-10-13): 'in force' took the single newest combiner, so the weekly definition was labelled
    older history while it still served beside the daily one."""
    import sqlite3
    from app import adaptive, live
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE adaptive_fits (fit_id INTEGER PRIMARY KEY, sport TEXT, kind TEXT, name TEXT, definition TEXT, "
                 "schedule TEXT, status TEXT, finished_at TEXT)")
    conn.execute("INSERT INTO adaptive_fits (sport,kind,name,definition,schedule,status,finished_at) VALUES "
                 "('nhl','combiner','combiner','OLDWEEKLY','weekly','success','2026-10-05T10:00:00Z'),"
                 "('nhl','combiner','combiner','WEEKLYDEF','weekly','success','2026-10-12T10:00:00Z'),"
                 "('nhl','combiner','combiner','DAILYDEF','daily','success','2026-10-12T11:00:00Z')")
    d = tmp_path / "adaptive"
    d.mkdir()
    for n in ("OLDWEEKLY", "WEEKLYDEF", "DAILYDEF"):
        (d / f"history-{n}.pkl").write_bytes(b"x")
    monkeypatch.setattr(ct, "adaptive_dir", lambda sport: d)
    monkeypatch.setattr(adaptive, "_load", lambda p: {"candidates": {}})
    monkeypatch.setattr(live, "available_rows", lambda conn, now: [])
    out = ct.export_adaptive(conn, "nhl")["exported"]
    assert {k: v["in_force"] for k, v in out.items()} == {"OLDWEEKLY": False, "WEEKLYDEF": True, "DAILYDEF": True}
