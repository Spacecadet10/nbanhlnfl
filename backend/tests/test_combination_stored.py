"""Combinations of saved out-of-sample forecasts (combination_research.stored): synthetic tables only."""
import gzip
import json
import random

import pytest
from fastapi.testclient import TestClient

from app import combination_research as cr
from app import main


def _rows(seed=1, seasons=(2021, 2022, 2023, 2024), per=200, models=("a", "b", "c")):
    rng = random.Random(seed)
    out = []
    for s in seasons:
        for i in range(per):
            truth = rng.random()
            realised = 0 if rng.random() < truth else 2
            vec = {}
            for k, m in enumerate(models):
                p = min(0.95, max(0.05, truth + rng.gauss(0, 0.1 + 0.05 * k)))
                vec[m] = [p, 0.0, 1 - p]
            day = f"{s}-0{1 + i % 9}-{10 + i % 18:02d}"
            out.append({"game_id": f"{s}-{i}", "season": s, "cutoff_at": f"{day}T12:00:00Z", "tipoff_at": f"{day}T23:00:00Z",
                        "result_available_at": f"{day}T23:59:00Z", "line": None, "realised": realised, "vectors": vec})
    return out


def test_frozen_choices_ignore_the_later_period():
    rows = _rows()
    first = cr.analyse_stored("winner", rows, ["a", "b", "c"])
    changed = json.loads(json.dumps(rows))
    for r in changed:
        if r["season"] >= 2023:             # the later period: flip every result
            r["realised"] = 2 - r["realised"]
    second = cr.analyse_stored("winner", changed, ["a", "b", "c"])
    assert first["periods"]["later"]["seasons"] == [2023, 2024]
    assert [c["members"] for c in first["holdout"]["checks"]] == [c["members"] for c in second["holdout"]["checks"]]
    assert [c["earlier"] for c in first["holdout"]["checks"]] == [c["earlier"] for c in second["holdout"]["checks"]]
    assert first["singles"] == second["singles"] and first["pairs"] == second["pairs"]
    assert first["holdout"]["checks"][0]["later"] != second["holdout"]["checks"][0]["later"]


def test_add_model_counts_corrected_and_introduced_errors_on_identical_games():
    rows = [{"game_id": str(i), "season": 2020 + i // 2, "cutoff_at": f"202{i // 2}-01-0{1 + i % 2}T12:00:00Z",
             "tipoff_at": f"202{i // 2}-01-0{1 + i % 2}T23:00:00Z", "line": None, "realised": 0, "vectors": {}} for i in range(8)]
    # Base A alone is wrong on games 0 and 1; adding B flips game 0 to right (corrected) and game 2 to wrong (introduced).
    a = [0.45, 0.45, 0.55, 0.6, 0.6, 0.6, 0.6, 0.6]
    b = [0.75, 0.50, 0.30, 0.6, 0.6, 0.6, 0.6, 0.6]
    for r, pa, pb in zip(rows, a, b):
        r["vectors"] = {"A": [pa, 0, 1 - pa], "B": [pb, 0, 1 - pb]}
    ctx = cr.prepare_stored("winner", rows, ["A", "B"])
    add = cr.stored_add(ctx, ["A"])
    row = add["rows"][0]
    assert add["base"] == ["A"] and row["members"] == ["A", "B"]
    assert (row["corrected"], row["introduced"], row["changed_picks"]) == (1, 1, 2)
    assert row["metrics"]["n"] == add["base_metrics"]["n"] == len(ctx["early"])
    with pytest.raises(ValueError):
        cr.stored_add(ctx, ["A", "B"])          # nothing left to add
    with pytest.raises(ValueError):
        cr.stored_add(ctx, ["Z"])


def test_agreement_count_is_separate_from_confidence_and_ties_make_no_pick():
    def row(i, vecs, realised=0):
        return {"game_id": str(i), "season": 2020, "cutoff_at": "2020-01-01T12:00:00Z", "tipoff_at": "2020-01-01T23:00:00Z",
                "realised": realised, "vectors": {m: (p, 0.0, 1 - p) for m, p in vecs.items()}, "w": {}}
    rows = [row(0, {"a": 0.6, "b": 0.6, "c": 0.4, "d": 0.4}),      # 2–2: a tie, no plurality pick
            row(1, {"a": 0.5, "b": 0.7, "c": 0.7, "d": 0.3}),      # a abstains at exactly 50/50: 2 agree, 1 dissents
            row(2, {"a": 0.9, "b": 0.9, "c": 0.9, "d": 0.9})]      # unanimous
    for r in rows:
        for m in "abcd":
            r["w"][m] = cr._wins("winner", r["vectors"][m], r["realised"])
    t = cr._agreement_table("winner", rows, ["a", "b", "c", "d"])
    by = {c["agreeing"]: c for c in t["by_count"]}
    assert by["tie (no plurality)"]["games"] == 1 and by["tie (no plurality)"]["decided"] == 0
    assert by[2]["games"] == 1 and by[2]["wins"] == 1 and by[4]["accuracy"] == 1.0
    assert by[4]["mean_average_confidence"] == pytest.approx(0.9)
    assert sum(b["games"] for b in t["by_confidence"]) == 3


def _write_tables(base, sport, rows):
    d = base / "research" / "candidates"
    d.mkdir(parents=True, exist_ok=True)
    early = [r for r in rows if r["season"] < 2023]
    later = [r for r in rows if r["season"] >= 2023]
    for stage, rs in (("screen", early), ("assess", later)):
        body = {"sport": sport, "stage": stage, "seasons": sorted({r["season"] for r in rs}), "tables": {"winner": rs}}
        (d / f"oof-{sport}-{stage}.json.gz").write_bytes(gzip.compress(json.dumps(body).encode()))


def test_stored_excludes_postgame_and_partial_models_and_labels_evidence(tmp_path, monkeypatch):
    rows = _rows(models=("a", "b", "c", "logit:nhl_goalie", "partial"))
    for r in rows[::7]:
        del r["vectors"]["partial"]
    _write_tables(tmp_path, "nhl", rows)
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    out = cr.stored("nhl", "winner")
    assert out["status"] == "available" and set(out["models"]) == {"a", "b", "c"}
    assert {e["model"] for e in out["excluded"]} == {"logit:nhl_goalie", "partial"}
    assert out["games"] == out["stored_games"] == len(rows)
    assert out["kind"].startswith("historical out-of-sample") and "exploratory" in out["note"]
    assert out["later_reserved_test_seasons"] == []          # NHL reserved test seasons are 2025-26, not in this table
    assert {s["stage"] for s in out["sources"]} == {"screen", "assess"}
    assert out["search"]["pairs"] == 3 and out["add_model"]["base"] == out["default_base"]
    assert cr.stored("nhl", "spread")["status"] == "no_stored_forecasts"


def test_combinations_api_demo_withheld_real_served_and_bad_base_rejected(tmp_path, monkeypatch):
    _write_tables(tmp_path, "nba", _rows(seed=3))
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(main.app, "dependency_overrides", {})
    c = TestClient(main.app)
    monkeypatch.setenv("NBA_API_MODE", "demo")
    demo = c.get("/api/research/combinations").json()
    assert demo["mode"] == "demo" and demo["status"] == "unavailable" and demo["models"] == []
    monkeypatch.setenv("NBA_API_MODE", "real")
    real = c.get("/api/research/combinations", params={"sport": "nba", "base": "a"}).json()
    assert real["mode"] == "real" and real["status"] == "available" and real["add_model"]["base"] == ["a"]
    assert real["later_reserved_test_seasons"] == []
    assert c.get("/api/research/combinations", params={"sport": "nba", "base": "a,zz"}).status_code == 422


# --- Model presence: only earlier data decides which models are compared (loaded through the saved tables) ----------------
def _stored_from_files(base, monkeypatch, rows):
    _write_tables(base, "nba", rows)
    monkeypatch.setenv("NBA_DATA_DIR", str(base))
    return cr.stored("nba", "winner")


def _earlier_view(out):
    """Everything chosen or measured on the earlier period: must not depend on any later forecast or result."""
    return {"models": out["models"], "default_base": out["default_base"], "search": out["search"], "singles": out["singles"],
            "pairs": out["pairs"], "agreement": out["agreement"], "add_model": out["add_model"],
            "earlier": out["periods"]["earlier"], "excluded_earlier": [e for e in out["excluded"] if "later" not in e["reason"]],
            "chosen": [(c["choice"], c["members"], c["earlier"]) for c in out["holdout"]["checks"]]}


def _later_change(rows, change):
    rows = json.loads(json.dumps(rows))
    later = [r for r in rows if r["season"] >= 2023]
    if change == "drop_one_later_forecast":
        del later[5]["vectors"]["c"]
    elif change == "drop_every_later_forecast":
        for r in later:
            del r["vectors"]["c"]
    elif change == "model_only_in_later_period":          # a perfect model that exists only later must not enter the search
        for r in later:
            r["vectors"]["late"] = [0.99, 0.0, 0.01] if r["realised"] == 0 else [0.01, 0.0, 0.99]
    elif change == "flip_later_outcomes":
        for r in later:
            r["realised"] = 2 - r["realised"]
    return rows


@pytest.mark.parametrize("change", ["drop_one_later_forecast", "drop_every_later_forecast", "model_only_in_later_period",
                                    "flip_later_outcomes"])
def test_later_presence_or_outcomes_never_change_the_compared_models(tmp_path, monkeypatch, change):
    rows = _rows(models=("a", "b", "c"))
    base = _stored_from_files(tmp_path / "base", monkeypatch, rows)
    out = _stored_from_files(tmp_path / "changed", monkeypatch, _later_change(rows, change))
    assert out["status"] == "available" and out["models"] == base["models"] and set(base["models"]) == {"a", "b", "c"}
    assert _earlier_view(out) == _earlier_view(base)
    assert base["periods"]["later"]["seasons"] == [2023, 2024]
    if change != "drop_every_later_forecast":           # there no later game has every model: nothing later is evaluated
        assert out["periods"]["later"]["seasons"] == [2023, 2024]
    assert base["coverage"]["later"] == {"games": 400, "settled": 400, "pending": 0, "used": 400, "excluded_missing_member": 0, "missing": {}}
    cov = out["coverage"]["later"]
    if change == "drop_one_later_forecast":           # the game is excluded and reported; membership is never reduced
        assert cov == {"games": 400, "settled": 400, "pending": 0, "used": 399, "excluded_missing_member": 1, "missing": {"c": 1}}
        assert out["holdout"]["checks"][-1]["members"] == base["holdout"]["checks"][-1]["members"]
    if change == "drop_every_later_forecast":
        assert cov == {"games": 400, "settled": 400, "pending": 0, "used": 0, "excluded_missing_member": 400, "missing": {"c": 400}}
        assert all(c["later"] is None for c in out["holdout"]["checks"]) and out["holdout"]["status"] == "insufficient_evidence"
    if change == "model_only_in_later_period":
        assert [e["model"] for e in out["excluded"]] == ["late"] and "later" in out["excluded"][0]["reason"]
        assert cov == base["coverage"]["later"]


# --- Honest states and explicit coverage (loaded through the saved tables) ------------------------------------------------
def _stored_rows(tmp_path, monkeypatch, rows, name):
    return _stored_from_files(tmp_path / name, monkeypatch, rows)


def test_no_earlier_later_split_is_insufficient_and_nothing_is_treated_as_earlier(tmp_path, monkeypatch):
    out = _stored_rows(tmp_path, monkeypatch, _rows(seasons=(2023, 2024)), "two")
    assert out["status"] == "insufficient_evidence" and "more than 2 seasons" in out["reason"]
    assert out["models"] == [] and "singles" not in out and "holdout" not in out
    assert {e["model"] for e in out["excluded"]} == {"a", "b", "c"}
    assert all("no earlier period" in e["reason"] for e in out["excluded"])


def test_no_settled_earlier_game_is_insufficient_with_its_own_reason(tmp_path, monkeypatch):
    rows = _rows()
    for r in rows:
        if r["season"] < 2023:
            r["realised"] = None
    out = _stored_rows(tmp_path, monkeypatch, rows, "unsettled")
    assert out["status"] == "insufficient_evidence" and out["reason"] == "no settled game in the earlier period"


def test_saved_forecasts_without_an_eligible_model_are_not_reported_as_no_stored_forecasts(tmp_path, monkeypatch):
    rows = _rows()
    early = [r for r in rows if r["season"] < 2023]
    for i, m in enumerate("abc"):
        del early[i]["vectors"][m]                 # every model misses one earlier game: none is complete there
    out = _stored_rows(tmp_path, monkeypatch, rows, "none")
    assert out["status"] == "insufficient_evidence" and "no model is eligible" in out["reason"]
    assert out["stored_games"] == len(rows) and {e["model"] for e in out["excluded"]} == {"a", "b", "c"}


def test_reserved_test_seasons_follow_the_split_even_when_every_later_game_is_excluded(tmp_path, monkeypatch):
    rows = _rows(seasons=(2021, 2022, 2025, 2026))       # NBA reserved test seasons are 2025 and 2026
    for r in rows:
        if r["season"] >= 2025:
            del r["vectors"]["c"]                        # no later game has every compared model
    out = _stored_rows(tmp_path, monkeypatch, rows, "reserved")
    assert out["status"] == "available" and out["coverage"]["later"]["used"] == 0
    assert out["split"] == {"earlier_seasons": [2021, 2022], "later_seasons": [2025, 2026]}
    assert out["later_reserved_test_seasons"] == [2025, 2026]


def test_coverage_separates_total_settled_used_and_missing_member_games(tmp_path, monkeypatch):
    rows = _rows()
    later = [r for r in rows if r["season"] >= 2023]
    for r in later[:3]:
        r["realised"] = None                           # pending
    del later[3]["vectors"]["c"]                       # settled, missing c
    del later[0]["vectors"]["b"]                       # pending and missing b: not in the settled-game denominator
    out = _stored_rows(tmp_path, monkeypatch, rows, "cov")
    assert out["coverage"]["later"] == {"games": 400, "settled": 397, "pending": 3, "used": 396,
                                        "excluded_missing_member": 1, "missing": {"c": 1}}
    assert out["coverage"]["earlier"] == {"games": 400, "settled": 400, "pending": 0, "used": 400,
                                          "excluded_missing_member": 0, "missing": {}}
    assert "settled" in out["coverage_definition"]


def test_two_eligible_models_get_the_analysis_and_internal_errors_are_never_a_bad_request(tmp_path, monkeypatch):
    """Reviewer reproduction (2026-10-13): with exactly two models `min()` ran over an empty set and the API answered 422
    'min() iterable argument is empty'. Only InvalidRequest is a 422; any other failure is a 500."""
    from fastapi.testclient import TestClient
    from app import main
    _write_tables(tmp_path, "nba", _rows(seed=5, models=("a", "b")))
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NBA_API_MODE", "real")
    monkeypatch.setattr(main.app, "dependency_overrides", {})
    c = TestClient(main.app, raise_server_exceptions=False)
    r = c.get("/api/research/combinations", params={"sport": "nba"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["models"] and len(body["models"]) == 2 and len(body["add_model"]["base"]) == 1
    assert [x["choice"] for x in body["holdout"]["checks"]] == ["best single", "best pair"]
    assert c.get("/api/research/combinations", params={"sport": "nba", "base": "a,b,c"}).status_code == 422   # a bad request
    monkeypatch.setattr(cr, "stored", lambda *a, **k: (_ for _ in ()).throw(ValueError("Expecting value: line 1 column 1")))
    assert c.get("/api/research/combinations", params={"sport": "nba"}).status_code == 500                  # an internal failure
