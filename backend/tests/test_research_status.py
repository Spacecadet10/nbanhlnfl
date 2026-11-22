"""/api/research/autoresearch: read-only status of the sports autoresearch experiment directory."""
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("NBA_API_MODE", "real")
    monkeypatch.setattr(main.app, "dependency_overrides", {})
    return TestClient(main.app)


def _w(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj) if isinstance(obj, dict) else "".join(json.dumps(x) + "\n" for x in obj))


def test_not_configured_and_missing_directory_are_explicit(client, monkeypatch, tmp_path):
    monkeypatch.delenv("NBA_AUTORESEARCH_DIR", raising=False)
    r = client.get("/api/research/autoresearch").json()
    assert r["configured"] is False and "not set" in r["reason"] and "Research only" in r["note"]
    monkeypatch.setenv("NBA_AUTORESEARCH_DIR", str(tmp_path / "nope"))
    assert "does not exist" in client.get("/api/research/autoresearch").json()["reason"]


def test_demo_mode_shows_no_research(monkeypatch, tmp_path):
    monkeypatch.setenv("NBA_API_MODE", "demo")
    monkeypatch.setenv("NBA_AUTORESEARCH_DIR", str(tmp_path))
    monkeypatch.setattr(main.app, "dependency_overrides", {})
    r = TestClient(main.app).get("/api/research/autoresearch").json()
    assert r["mode"] == "demo" and r["configured"] is False


def test_trials_failures_incumbents_runs_and_stop_are_reported(client, monkeypatch, tmp_path):
    exp = tmp_path / "exp"
    monkeypatch.setenv("NBA_AUTORESEARCH_DIR", str(exp))
    meta = {"protocol_id": "p-new", "sport": "nhl", "registered_at": "2026-10-14T10:00:00Z",
            "protocol": {"scored_seasons": {"nhl": [2022]}, "holdout_seasons": {"nhl": [2025]}}, "cohort": {"reg3": 10},
            "start_incumbent": {"reg3": {"member": "goals_draw", "log_loss": 1.03}, "winner": {"member": "x", "log_loss": 0.65}}}
    _w(exp / "p-new" / "nhl" / "protocol.json", meta)
    _w(exp / "p-old" / "nhl" / "protocol.json", {**meta, "protocol_id": "p-old", "registered_at": "2026-10-01T00:00:00Z"})
    mk = {"reg3": {"decision": "keep", "vs_incumbent": {"mean": -0.008, "ci95": [-0.01, -0.006]}, "forecast": 10, "cohort": 10},
          "winner": {"decision": "reject", "vs_incumbent": {"mean": 0.001, "ci95": [-0.001, 0.003]}}}
    trials = [{"event": "started", "trial_id": "t1"}, {"event": "finished", "trial_id": "t1", "commit": "a" * 40, "status": "keep",
                                                       "markets": mk, "agent_run": "r1", "description": "poisson draw"},
              {"event": "started", "trial_id": "t2"}, {"event": "finished", "trial_id": "t2", "commit": "b" * 40, "status": "crash",
                                                       "reason": "candidate process ended"},
              {"event": "finished", "trial_id": "t3", "commit": "c" * 40, "status": "invalid", "reason": "import os"},
              {"event": "started", "trial_id": "t4"}]
    p = exp / "p-new" / "nhl" / "trials.jsonl"
    _w(p, trials)
    with p.open("a") as fh:
        fh.write('{"event": "fini')                                # torn last line of a writer still appending
    _w(exp / "agent" / "runs.jsonl", [{"event": "started", "run_id": "r1", "at": "2026-10-14T11:00:00Z", "budget": {"max_trials": 3}},
                                       {"event": "finished", "run_id": "r1", "at": "2026-10-14T11:20:00Z", "stopped": "agent finished",
                                        "outcome": "kept vs incumbent (exploratory, not a promotion): nhl:reg3", "cost_usd": 1.2},
                                       {"event": "started", "run_id": "r0", "at": "2026-10-14T10:00:00Z"},
                                       {"event": "finished", "run_id": "r0", "at": "2026-10-14T10:01:00Z", "stopped": "stopped",
                                        "cost_usd": 0},
                                       {"event": "correction", "run_id": "r0", "at": "2026-10-14T12:00:00Z",
                                        "fields": {"cost_usd": None}, "reason": "killed agent: cost unknown, not 0"}])
    _w(exp / "agent" / "active.json", {"run_id": "r2", "driver_pid": 999999, "max_trials": 3})
    (exp / "agent" / "STOP").write_text("2026-10-14T12:00:00Z stopped by a person\n")
    r = client.get("/api/research/autoresearch").json()
    assert r["configured"] and r["mode"] == "real" and r["stopped"].endswith("stopped by a person")
    assert r["active_run"]["run_id"] == "r2" and r["active_run"]["driver_alive"] is False
    run, r0 = r["agent_runs"]
    assert run["end"] == "finished" and run["cost_usd"] == 1.2 and "nhl:reg3" in run["outcome"]
    assert r0["cost_usd"] is None and r0["end"] == "finished" and r0["corrections"][0]["reason"].startswith("killed agent")
    s = r["sports"]["nhl"]
    assert s["protocol_id"] == "p-new"                                # the latest registered protocol
    assert s["by_status"] == {"keep": 1, "crash": 1, "invalid": 1} and s["failures"] == 2
    assert s["running"] == ["t4"] and s["unreadable_lines"] == 1
    assert s["incumbents"]["reg3"]["source"] == "kept trial" and s["incumbents"]["winner"]["label"] == "x"
    assert [t["trial_id"] for t in s["recent"]] == ["t3", "t2", "t1"]
    assert s["recent"][2]["markets"]["reg3"]["delta"] == -0.008 and s["recent"][2]["agent_run"] == "r1"


def test_the_endpoint_writes_nothing(client, monkeypatch, tmp_path):
    exp = tmp_path / "exp"
    _w(exp / "p" / "nba" / "protocol.json", {"protocol_id": "p", "sport": "nba", "start_incumbent": {}})
    before = {q: q.stat().st_mtime_ns for q in exp.rglob("*")}
    os.chmod(exp / "p" / "nba", 0o555)
    try:
        monkeypatch.setenv("NBA_AUTORESEARCH_DIR", str(exp))
        assert client.get("/api/research/autoresearch").status_code == 200
    finally:
        os.chmod(exp / "p" / "nba", 0o755)
    assert {q: q.stat().st_mtime_ns for q in exp.rglob("*")} == before


def test_a_reused_driver_pid_is_not_reported_as_running(client, monkeypatch, tmp_path):
    # Review finding 7: a live process with the recorded pid but a different start time is not the driver.
    exp = tmp_path / "exp"
    _w(exp / "agent" / "active.json", {"run_id": "r9", "driver_pid": os.getpid(), "driver_token": "Thu Jan  1 00:00:00 1970"})
    monkeypatch.setenv("NBA_AUTORESEARCH_DIR", str(exp))
    assert client.get("/api/research/autoresearch").json()["active_run"]["driver_alive"] is False


def test_no_absolute_paths_are_exposed(client, monkeypatch, tmp_path):
    exp = tmp_path / "exp"
    _w(exp / "agent" / "runs.jsonl", [{"event": "started", "run_id": "r1", "at": "2026-10-14T11:00:00Z"},
                                       {"event": "finished", "run_id": "r1", "at": "2026-10-14T11:01:00Z",
                                        "report": str(exp / "agent" / "runs" / "r1" / "report.json")}])
    monkeypatch.setenv("NBA_AUTORESEARCH_DIR", str(exp))
    assert str(tmp_path) not in client.get("/api/research/autoresearch").text


# ===== Protocol v3 (five-sport research evaluation, app/autoresearch.py "Protocol v3") =====================================
import hashlib
import math
import random
import sqlite3
from datetime import datetime, timedelta, timezone

from app import autoresearch as ar

FMT = "%Y-%m-%dT%H:%M:%SZ"
T0 = datetime(2024, 1, 1, 12, tzinfo=timezone.utc)


def _t(days, hours=0):
    return (T0 + timedelta(days=days, hours=hours)).strftime(FMT)


def rec(i, market="winner", p=0.6, outcome="a", season=2020, line=None, mid=None, quote=None, days=None, **kw):
    d = i if days is None else days
    probs = {"a": p, "b": 1 - p} if mid is None else {"a": p, "mid": mid, "b": 1 - p - mid}
    return {"game_id": f"g{i}", "season": season, "start_at": _t(d, 12), "forecast_cutoff": _t(d), "market": market, "line": line,
            "probs": probs, "outcome": outcome, **({"quote": quote} if quote else {}), **kw}


def fixture_adapter(by_model, roles=None, markets=None, **kw):
    """A fixture adapter: `by_model` is {model: records}; roles default to incumbent for 'inc', candidate for names with 'cand'."""
    roles = roles or {n: ("candidate" if "cand" in n else "incumbent") for n in by_model}
    calls = {"walk": []}

    def walk(conn, seasons, name):
        calls["walk"].append((name, tuple(seasons)))
        return [r for r in by_model[name] if r["season"] in seasons]
    a = {"sport": "nba", "data_version": "dv1", "markets": markets or {"winner": {"kind": "binary", "settlement": "incl. OT"}},
         "unsupported": {"player_props": "no history"},
         "periods": {"search": [2020], "validation": [2021], "holdout": [2022]},
         "models": {n: {"role": r, "description": n, "version": "v1"} for n, r in roles.items()}, "walk": walk,
         "connect": lambda: None} | kw
    a["_calls"] = calls
    return a


def synthetic(n=400, seed=1, edge=0.0, spread_market=False):
    """Games with a true win probability; incumbent = noisy estimate; candidate = closer to the truth by `edge`."""
    rng = random.Random(seed)
    inc, cand = [], []
    for i in range(n):
        truth = rng.uniform(0.1, 0.9)
        out = "a" if rng.random() < truth else "b"
        season = 2020 if i < n // 2 else 2021
        noise = rng.uniform(-0.3, 0.3)
        pi = min(0.97, max(0.03, truth + noise))
        pc = min(0.97, max(0.03, truth + noise * (1 - edge)))
        inc.append(rec(i, p=pi, outcome=out, season=season))
        cand.append(rec(i, p=pc, outcome=out, season=season))
    return {"inc": inc, "cand": cand}


# --- metrics against hand-computed values ---
def test_log_loss_brier_accuracy_and_calibration_match_hand_computation():
    recs = [rec(0, p=0.8, outcome="a"), rec(1, p=0.8, outcome="b"), rec(2, p=0.3, outcome="b")]
    m = ar.model_metrics(recs, "binary", universe=4)
    assert m["n"] == 3 and m["coverage"] == 0.75
    assert m["log_loss"] == pytest.approx((-math.log(0.8) - math.log(0.2) - math.log(0.7)) / 3)
    # 3-vector Brier convention (the app's): (a, 0, b) vs the realised side
    assert m["brier"] == pytest.approx(((0.04 + 0.04) + (0.64 + 0.64) + (0.09 + 0.09)) / 3)
    assert m["accuracy"] == pytest.approx(2 / 3) and (m["wins"], m["losses"]) == (2, 1)
    cal = m["calibration"]
    assert cal["n_pairs"] == 3
    b8 = cal["bins"][8]
    assert b8["n"] == 2 and b8["observed"] == pytest.approx(0.5) and b8["mean_p"] == pytest.approx(0.8)
    assert cal["ece"] == pytest.approx((2 * 0.3 + 1 * 0.3) / 3)        # bin 8: |0.5-0.8|; bin 3: |0-0.3|


def test_push_is_scored_for_line_markets_with_mid_probability_and_excluded_without():
    with_mid = [rec(0, "spread", p=0.45, mid=0.1, outcome="push", line=-3.0)]
    assert ar.model_metrics(with_mid, "line")["log_loss"] == pytest.approx(-math.log(0.1))
    no_mid = [rec(0, "spread", p=0.5, outcome="push", line=-3.0), rec(1, "spread", p=0.5, outcome="void", line=-3.0)]
    m = ar.model_metrics(no_mid, "line")
    assert m["n"] == 0 and m["push_unscored"] == 1 and m["void"] == 1


def test_score_errors_only_where_point_predictions_exist():
    recs = [rec(0, point_pred={"home": 110, "away": 100}, point_actual={"home": 100, "away": 100}),
            rec(1, point_pred={"home": 100, "away": 100}, point_actual={"home": 104, "away": 96}), rec(2)]
    e = ar.point_errors(recs)
    assert e["home"]["n"] == 2 and e["home"]["mae"] == pytest.approx(7.0) and e["margin"]["bias"] == pytest.approx((10 - 8) / 2)
    assert e["total"]["rmse"] == pytest.approx(math.sqrt((100 + 0) / 2))
    assert ar.point_errors([rec(0)]) is None


# --- contract checks ---
def test_records_with_information_after_the_cutoff_or_bad_probabilities_are_refused():
    a = fixture_adapter({"inc": []})
    bad_cut = rec(0)
    bad_cut["forecast_cutoff"] = _t(0, 13)                      # after the start (12:00): uses the future
    with pytest.raises(ar.AdapterInvalid, match="after the start"):
        ar.validate_records(a, [bad_cut], [2020])
    with pytest.raises(ar.AdapterInvalid, match="summing to 1"):
        ar.validate_records(a, [rec(0, p=0.7) | {"probs": {"a": 0.7, "b": 0.7}}], [2020])
    with pytest.raises(ar.AdapterInvalid, match="duplicate"):
        ar.validate_records(a, [rec(0), rec(0)], [2020])
    with pytest.raises(ar.AdapterInvalid, match="outside the requested"):
        ar.validate_records(a, [rec(0, season=2022)], [2020, 2021])
    with pytest.raises(ar.AdapterInvalid, match="does not declare"):
        ar.validate_records(a, [rec(0, market="total", line=200.5)], [2020])
    with pytest.raises(ar.AdapterInvalid, match="keys"):
        ar.validate_records(a, [rec(0) | {"probs": {"a": 0.5, "mid": 0.1, "b": 0.4}}], [2020])


def test_adapter_shape_and_period_overlap_are_validated():
    ok = fixture_adapter({"inc": []})
    ar.validate_adapter(ok)
    with pytest.raises(ar.AdapterInvalid, match="holdout seasons must come after"):
        ar.validate_adapter({**ok, "periods": {"search": [2020], "validation": [2023], "holdout": [2022]}})
    with pytest.raises(ar.AdapterInvalid, match="both"):
        ar.validate_adapter({**ok, "periods": {"search": [2020], "validation": [2020], "holdout": [2022]}})
    with pytest.raises(ar.AdapterInvalid, match="no incumbent"):
        ar.validate_adapter({**ok, "models": {"c": {"role": "candidate", "version": "1"}}})
    with pytest.raises(ar.AdapterInvalid, match="lacks"):
        ar.validate_adapter({k: v for k, v in ok.items() if k != "walk"})


# --- sportsbook no-vig and returns: only valid, executable, matching quotes ---
def q(price_a=1.9, price_b=1.9, at=None, line=None, executable=True, synthetic=False, mid=None):
    out = {"provider": "book", "retrieved_at": at or _t(0, -1), "price_a": price_a, "price_b": price_b, "line": line,
           "executable": executable, "synthetic": synthetic}
    return out | ({"price_mid": mid} if mid else {})


def test_no_vig_probabilities_sum_to_one_and_use_decimal_prices():
    a, mid, b = ar.no_vig(q(1.8, 2.2))
    assert mid == 0.0 and a + b == pytest.approx(1.0) and a == pytest.approx((1 / 1.8) / (1 / 1.8 + 1 / 2.2))


def test_book_comparison_ignores_late_synthetic_and_other_line_quotes():
    recs = [rec(0, quote=q()), rec(1, quote=q(at=_t(1, 1))),            # retrieved after the cutoff
            rec(2, quote=q(synthetic=True)), rec(3, "spread", line=-3.0, quote=q(line=-2.5)),
            rec(4, "spread", line=-3.0, quote=q(line=-3.0))]
    kind = {0: "binary", 1: "binary", 2: "binary", 3: "line", 4: "line"}
    valid = [r["game_id"] for r in recs if ar.valid_quote(r)]
    assert valid == ["g0", "g4"]
    out = ar.book_compare(recs[:3], "binary")
    assert out["valid"] == 1 and out["synthetic"] == 1 and out["quoted"] == 3 and out["invalid_or_late"] == 2
    none = ar.book_compare([rec(0, quote=q(synthetic=True))], "binary")
    assert none["status"] == "unavailable" and "no valid" in none["reason"]
    assert ar.book_compare([rec(0)], "binary")["status"] == "unavailable"


def test_returns_use_only_executable_matching_quotes_refund_pushes_and_never_count_synthetic():
    recs = [rec(0, p=0.7, outcome="a", quote=q(2.0, 1.9)),                    # bet a at 2.0 (0.7*2 > 1): +1
            rec(1, p=0.7, outcome="b", quote=q(2.0, 1.9)),                    # bet a at 2.0: -1
            rec(2, "spread", p=0.6, mid=0.1, outcome="push", line=-3.0, quote=q(2.0, 1.9, line=-3.0)),   # refund: 0
            rec(3, p=0.7, outcome="void", quote=q(2.0, 1.9)),                 # void: excluded
            rec(4, p=0.7, outcome="a", quote=q(2.0, 1.9, executable=False)),  # not executable
            rec(5, p=0.7, outcome="a", quote=q(2.0, 1.9, synthetic=True)),    # synthetic
            rec(6, p=0.7, outcome="a", quote=q(1.2, 1.9))]                    # 0.7*1.2 < 1: no signal
    out = ar.roi(recs, "line")
    assert out["bets"] == 3 and out["staked"] == 3 and out["profit"] == pytest.approx(0.0) and out["roi"] == pytest.approx(0.0)
    assert out["synthetic_quotes_excluded"] == 1 and out["non_executable_quotes_excluded"] == 1
    assert ar.roi([rec(0, quote=q(synthetic=True))], "binary")["status"] == "unavailable"
    assert ar.roi([rec(0)], "binary")["status"] == "unavailable"


# --- baselines ---
def test_base_rate_baseline_uses_an_outcome_only_after_the_settle_lag():
    def r(i, start, cutoff, outcome):
        return rec(i, outcome=outcome) | {"start_at": start, "forecast_cutoff": cutoff}
    first = r(0, "2024-01-01T12:00:00Z", "2024-01-01T00:00:00Z", "a")
    close = r(1, "2024-01-02T01:00:00Z", "2024-01-01T17:59:00Z", "a")      # cutoff 17:59 < 12:00 + 6 h: first not yet usable
    later = r(2, "2024-01-03T12:00:00Z", "2024-01-02T00:00:00Z", "b")      # first usable; close (01:00 + 6 h) is not
    out = ar.baselines_for([first, close, later], "binary")["baseline:base_rate"]
    assert out[0]["probs"]["a"] == pytest.approx(0.5) and out[1]["probs"]["a"] == pytest.approx(0.5)
    assert out[2]["probs"]["a"] == pytest.approx(2 / 3)
    uni = ar.baselines_for([rec(0, "reg3", p=0.3, mid=0.3, outcome="mid")], "three_way")["baseline:uniform"][0]["probs"]
    assert uni == {"a": pytest.approx(1 / 3), "mid": pytest.approx(1 / 3), "b": pytest.approx(1 / 3)}


# --- the keep rule is the one the v2 trials use ---
def test_v3_decision_equals_the_v2_score_market_decision_on_the_same_forecasts():
    s = synthetic(n=300, seed=3, edge=0.7)
    rows = [{"game_id": r["game_id"], "season": 2022, "cutoff_at": r["forecast_cutoff"], "line": None,
             "realised": 0 if r["outcome"] == "a" else 2, "vectors": {"inc": (r["probs"]["a"], 0.0, r["probs"]["b"])}}
            for r in s["inc"]]
    data = {"sport": "nba", "primary": {"winner": "inc"}, "tables": {"winner": rows}}
    cand = {r["game_id"]: (r["probs"]["a"], 0.0, r["probs"]["b"]) for r in s["cand"]}
    v2 = ar.score_market(data, "winner", cand, {"trial": None, "member": "inc", "label": "inc"}, [2022])
    v3 = ar.decide_candidate(s["cand"], s["inc"], "binary")
    assert v3["decision"] == v2["decision"] == "keep"
    assert v3["vs_incumbent"]["mean"] == pytest.approx(v2["vs_incumbent"]["mean"])
    assert v3["vs_incumbent"]["ci95"] == pytest.approx(v2["vs_incumbent"]["ci95"])
    worse = ar.decide_candidate(s["inc"], s["cand"], "binary")
    assert worse["decision"] == "reject"


def test_candidate_with_missing_games_or_too_few_games_is_rejected_not_dropped():
    s = synthetic(n=200, seed=4, edge=0.8)
    assert "coverage loss" in ar.decide_candidate(s["cand"][:-3], s["inc"], "binary")["reason"]
    assert "only 20 scored" in ar.decide_candidate(s["cand"][:20], s["inc"][:20], "binary")["reason"]


def test_evaluate_records_reports_every_metric_per_market_and_keeps_only_a_real_improvement():
    s = synthetic(n=600, seed=5, edge=0.8)
    worse = [r | {"probs": {"a": 0.5, "b": 0.5}} for r in s["inc"]]
    a = fixture_adapter({"inc": s["inc"], "cand_good": s["cand"], "cand_flat": worse})
    ev = ar.evaluate_records(a, {n: a["walk"](None, [2020, 2021], n) for n in a["models"]})
    m = ev["markets"]["winner"]
    assert ev["unsupported"] == {"player_props": "no history"} and ev["exploratory"] is True
    assert m["incumbent"] == "inc" and m["universe"] == 600
    assert {"baseline:uniform", "baseline:base_rate", "inc", "cand_good", "cand_flat"} <= set(m["models"])
    for k in ("n", "coverage", "brier", "log_loss", "calibration", "vs_book", "returns", "by_stage"):
        assert k in m["models"]["cand_good"]
    assert m["comparisons"]["cand_good"]["decision"] == "keep"
    assert set(m["comparisons"]["cand_good"]["vs_incumbent"]) == {"search", "validation"}
    assert m["comparisons"]["cand_flat"]["decision"] == "reject"
    assert "baseline:uniform" in m["comparisons"]["cand_good"]["vs_baselines"]
    assert ev["kept"] == ["cand_good:winner"]


# --- runner: explicit outcomes, bounded, resumable, per sport ---
@pytest.fixture
def exp(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path / "snapshot"))
    (tmp_path / "snapshot").mkdir()
    return tmp_path / "experiments"


def _adapters(**by_sport):
    return lambda sport: by_sport.get(sport)


def test_outcomes_are_explicit_per_sport(exp):
    s = synthetic(n=400, seed=6, edge=0.8)
    good = fixture_adapter({"inc": s["inc"], "cand": s["cand"]})
    nocand = fixture_adapter({"inc": s["inc"]}) | {"sport": "nhl"}

    def broken(*a):
        raise RuntimeError("adapter exploded")
    failing = fixture_adapter({"inc": s["inc"]}) | {"sport": "nfl", "walk": broken}
    rep = ar.daily_v3(["nba", "nhl", "nfl", "soccer", "ncaab"], exp, adapter_fn=_adapters(nba=good, nhl=nocand, nfl=failing))
    assert rep["outcomes"] == {"nba": "evaluated", "nhl": "no_candidates", "nfl": "failed", "soccer": "refused", "ncaab": "refused"}
    assert "no research adapter" in rep["sports"]["soccer"]["reason"]
    assert "adapter exploded" in rep["sports"]["nfl"]["reason"]
    assert "not a successful search" in rep["sports"]["nhl"]["reason"]
    nba = rep["sports"]["nba"]
    assert nba["evaluated_markets"] == ["winner"] and nba["candidates"] == ["cand"] and nba["kept"] == ["cand:winner"]
    assert nba["last_success_at"] and nba["last_attempt"]["outcome"] == "evaluated"
    assert any("player_props" in b for b in nba["blockers"]) and nba["queue"]["remaining"] == 0
    assert json.loads(Path(rep["report_file"]).read_text())["outcomes"] == rep["outcomes"]


def test_a_budget_stop_is_skipped_and_the_next_run_resumes_without_rescoring(exp):
    s = synthetic(n=400, seed=7, edge=0.8)
    a = fixture_adapter({"inc": s["inc"], "cand": s["cand"], "cand2": s["cand"]})
    r1 = ar.run_sport_v3("nba", exp, max_items=1, adapter_fn=_adapters(nba=a))
    assert r1["outcome"] == "skipped" and r1["queue"]["remaining"] == 2 and "resumes with cand" in r1["next_run"]
    r2 = ar.run_sport_v3("nba", exp, max_items=1, adapter_fn=_adapters(nba=a))
    assert r2["outcome"] == "evaluated" and r2["queue"]["remaining"] == 1 and "1 of 2 candidates" in r2["reason"]
    r3 = ar.run_sport_v3("nba", exp, max_items=5, adapter_fn=_adapters(nba=a))
    assert r3["queue"]["remaining"] == 0 and r3["queue"]["ran_now"] == 1
    walked = [n for n, _s in a["_calls"]["walk"]]
    assert sorted(walked) == ["cand", "cand2", "inc"]                 # each item scored exactly once across three runs
    assert all(seasons == (2020, 2021) for _n, seasons in a["_calls"]["walk"])      # never a holdout season (2022)
    assert r3["last_success_at"] >= r2["last_success_at"]


def test_a_time_budget_of_zero_skips_and_one_sport_cannot_use_anothers_budget(exp):
    s = synthetic(n=300, seed=8, edge=0.8)
    a = fixture_adapter({"inc": s["inc"], "cand": s["cand"]})
    b = fixture_adapter({"inc": s["inc"], "cand": s["cand"]}) | {"sport": "nhl"}
    rep = ar.daily_v3(["nba", "nhl"], exp, max_items=1, adapter_fn=_adapters(nba=a, nhl=b))
    assert [rep["sports"][x]["queue"]["ran_now"] for x in ("nba", "nhl")] == [1, 1]      # both got their own budget
    z = ar.run_sport_v3("nba", exp, max_seconds=-1, adapter_fn=_adapters(nba=a))
    assert z["outcome"] == "skipped" and z["reason"] == "time budget reached" and z["queue"]["remaining"] == 1


def test_daily_rotation_moves_the_starting_sport_each_run(exp):
    order = lambda rep: list(rep["sports"])          # noqa: E731
    r1 = ar.daily_v3(["nba", "nhl", "nfl"], exp, adapter_fn=_adapters())
    r2 = ar.daily_v3(["nba", "nhl", "nfl"], exp, adapter_fn=_adapters())
    r3 = ar.daily_v3(["nba", "nhl", "nfl"], exp, adapter_fn=_adapters())
    assert order(r1) == ["nba", "nhl", "nfl"] and order(r2) == ["nhl", "nfl", "nba"] and order(r3) == ["nfl", "nba", "nhl"]


def test_the_live_data_directory_is_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("NBA_DATA_DIR", str(ar.REPO_ROOT / "data"))
    r = ar.run_sport_v3("nba", tmp_path / "e", adapter_fn=_adapters(nba=fixture_adapter({"inc": []})))
    assert r["outcome"] == "refused" and "live store" in r["reason"]


def test_a_failing_candidate_does_not_hide_the_reference_and_is_a_blocker(exp):
    s = synthetic(n=300, seed=9, edge=0.8)
    a = fixture_adapter({"inc": s["inc"], "cand": s["cand"], "cand_bad": s["cand"]})
    inner = a["walk"]

    def walk(conn, seasons, name):
        if name == "cand_bad":
            raise ValueError("fit failed")
        return inner(conn, seasons, name)
    r = ar.run_sport_v3("nba", exp, adapter_fn=_adapters(nba=a | {"walk": walk}))
    assert r["outcome"] == "evaluated" and "1 of 2 candidates" in r["reason"]
    r = ar.run_sport_v3("nba", exp, adapter_fn=_adapters(nba=a | {"walk": walk}))          # retries: bounded
    r = ar.run_sport_v3("nba", exp, adapter_fn=_adapters(nba=a | {"walk": walk}))
    r = ar.run_sport_v3("nba", exp, adapter_fn=_adapters(nba=a | {"walk": walk}))
    assert r["queue"]["failed"] == ["cand_bad"] and any("cand_bad" in b for b in r["blockers"])


# --- holdout: once, logged first, only a kept candidate ---
def test_holdout_is_refused_without_a_kept_candidate_asks_first_and_is_used_once(exp):
    s = synthetic(n=800, seed=10, edge=0.9)
    flat = [r | {"probs": {"a": 0.5, "b": 0.5}} for r in s["inc"]]
    hold_inc = [r | {"season": 2022} for r in s["inc"][:120]]
    hold_cand = [r | {"season": 2022} for r in s["cand"][:120]]
    a = fixture_adapter({"inc": s["inc"] + hold_inc, "cand": s["cand"] + hold_cand}, data_version="dv2")
    bad = fixture_adapter({"inc": s["inc"], "cand": flat})
    ar.run_sport_v3("nba", exp, adapter_fn=_adapters(nba=bad))
    with pytest.raises(ar.Refused, match="no candidate was kept"):
        ar.holdout_v3("nba", exp, ask=lambda p: True, adapter_fn=_adapters(nba=bad))
    ar.run_sport_v3("nba", exp, adapter_fn=_adapters(nba=a))
    assert not any(seasons == (2022,) or 2022 in seasons for _n, seasons in a["_calls"]["walk"])
    with pytest.raises(ar.Refused, match="not confirmed"):
        ar.holdout_v3("nba", exp, ask=lambda p: False, adapter_fn=_adapters(nba=a))
    assert not list((exp / "v3").glob("*/nba/holdout_access.jsonl"))          # declined: nothing logged, nothing read
    res = ar.holdout_v3("nba", exp, ask=lambda p: True, adapter_fn=_adapters(nba=a))
    assert res["seasons"] == [2022] and res["markets"]["winner"]["models"]["cand"]["n"] == 120 and "Report only" in res["note"]
    log = [json.loads(x) for p in (exp / "v3").glob("*/nba/holdout_access.jsonl") for x in p.read_text().splitlines()]
    assert [h["event"] for h in log] == ["pending", "completed"]
    with pytest.raises(ar.Refused, match="already used"):
        ar.holdout_v3("nba", exp, ask=lambda p: True, adapter_fn=_adapters(nba=a))


# --- forward test store ---
def _official(tmp_path, final=True):
    p = tmp_path / "official.sqlite"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE games (game_id TEXT, status TEXT, home_score INT, away_score INT)")
    c.execute("INSERT INTO games VALUES ('g1','final',100,90)")
    c.commit()
    c.close()
    return p


def _settle(conn, records):
    out = []
    for r in records:
        g = conn.execute("SELECT * FROM games WHERE game_id=?", (r["game_id"],)).fetchone()
        if g and g["status"] == "final":
            out.append({"game_id": r["game_id"], "market": r["market"], "line": r["line"],
                        "outcome": "a" if g["home_score"] > g["away_score"] else "b"})
    return out


def test_forward_store_is_append_only_and_rejects_hindsight(tmp_path):
    path = tmp_path / "research" / ar.FORWARD_FILE
    kw = dict(sport="nba", protocol_id="p", model="m", role="candidate", model_version="v1", data_version="d")
    ok = rec(1, p=0.6, outcome=None, days=3)
    ok["outcome"] = None
    late = rec(2, outcome=None, days=3) | {"forecast_cutoff": _t(3, 5)}
    started = rec(3, outcome=None, days=1)
    settled = rec(4, outcome="a", days=3)
    r = ar.forward_record(path, forecasted_at=_t(3, 2), records=[ok, late, started, settled], **kw)
    assert r["inserted"] == 1 and len(r["rejected"]) == 3
    reasons = " ".join(x["reason"] for x in r["rejected"])
    assert "after the forecast time" in reasons and "not before the start" in reasons and "carry an outcome" in reasons
    again = ar.forward_record(path, forecasted_at=_t(3, 2), records=[ok], **kw)
    assert again["inserted"] == 0 and again["already_recorded"] == 1
    conn = ar.forward_connect(path)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE forward_forecasts SET p_a = 0.9")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM forward_forecasts")


def test_forward_grading_reads_the_official_store_read_only(tmp_path):
    off = _official(tmp_path)
    before = hashlib.sha256(off.read_bytes()).hexdigest()
    path = tmp_path / "fw.sqlite"
    kw = dict(sport="nba", protocol_id="p", model="m", role="candidate", model_version="v1", data_version="d")
    ar.forward_record(path, forecasted_at=_t(0, 1), records=[rec(1, outcome=None, days=0)], **kw)
    oc = sqlite3.connect(off)
    oc.row_factory = sqlite3.Row
    a = {"sport": "nba", "settle": _settle}
    out = ar.forward_grade(path, a, oc, now=_t(5))
    assert out["graded"] == 1
    with pytest.raises(sqlite3.OperationalError):
        oc.execute("INSERT INTO games VALUES ('x','final',1,1)")               # the official handle is forced read-only
    oc.close()
    assert hashlib.sha256(off.read_bytes()).hexdigest() == before
    assert ar.forward_grade(path, {"sport": "nba"}, None)["status"] == "unavailable"
    by_model, info = ar.forward_records(path, "nba")
    assert by_model["m"][0]["outcome"] == "a" and info["m"]["role"] == "candidate"


def _fill_forward(path, n, edge, sport="nba", seed=11):
    s = synthetic(n=n, seed=seed, edge=edge)
    for model, role, recs in (("inc", "incumbent", s["inc"]), ("cand", "candidate", s["cand"])):
        fw = [r | {"outcome": None, "start_at": _t(i, 12), "forecast_cutoff": _t(i)} for i, r in enumerate(recs)]
        ar.forward_record(path, sport=sport, protocol_id="p", model=model, role=role, model_version="v1", data_version="d",
                          forecasted_at=_t(0, 0) if False else _t(-1), records=[dict(r, forecast_cutoff=_t(-2)) for r in fw])
    conn = ar.forward_connect(path)
    with conn:
        for i, r in enumerate(s["inc"]):
            conn.execute("INSERT INTO forward_grades (sport, game_id, market, line_key, outcome, point_actual, graded_at) "
                         "VALUES (?,?,?,?,?,?,?)", (sport, r["game_id"], "winner", "", r["outcome"], None, _now_str()))
    conn.close()


def _now_str():
    return datetime.now(timezone.utc).strftime(FMT)


def test_promotion_recommendation_needs_the_registered_count_beats_the_incumbent_and_never_promotes(tmp_path):
    a = fixture_adapter({"inc": []})
    small = tmp_path / "small.sqlite"
    _fill_forward(small, 80, 0.8)
    r = ar.forward_evaluate(small, a, "p")["recommendation"]
    assert r["never_promotes"] is True and r["manual_decision_required"] is True
    m = r["candidates"]["cand"]["markets"]["winner"]
    assert m["status"] == "insufficient_evidence" and m["criteria"]["min_settled_forward"] == {"status": "failed", "n": 80, "need": 200}
    assert m["criteria"]["paired_log_loss"]["status"] == "not_evaluable"
    big = tmp_path / "big.sqlite"
    _fill_forward(big, 700, 0.8)
    res = ar.forward_evaluate(big, a, "p")
    m = res["recommendation"]["candidates"]["cand"]["markets"]["winner"]
    assert m["status"] == "recommend_for_review" and m["criteria"]["sportsbook"]["status"] == "not_evaluable"
    assert m["look_label"].startswith("confirmatory")
    again = ar.forward_evaluate(big, a, "p")["recommendation"]["candidates"]["cand"]["markets"]["winner"]
    assert again["look_label"].startswith("exploratory")                       # only the first look at the minimum is confirmatory
    worse = tmp_path / "worse.sqlite"
    _fill_forward(worse, 700, -1.0)
    wm = ar.forward_evaluate(worse, a, "p")["recommendation"]["candidates"]["cand"]["markets"]["winner"]
    assert wm["status"] == "not_recommended" and "paired_log_loss" in wm["failed"]


def test_no_forward_candidates_is_reported_as_such(tmp_path):
    a = fixture_adapter({"inc": []})
    assert ar.forward_evaluate(tmp_path / "empty.sqlite", a, "p")["status"] == "no_forward_forecasts"
    assert ar.promotion_recommendation("nba", {"candidates": [], "markets": {}})["status"] == "no_candidates"


def test_the_forward_store_is_a_separate_file_under_the_research_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    assert ar.forward_path() == tmp_path / "research" / "forward-candidates.sqlite"


# --- NBA/NHL through the new path give the numbers of the existing study tables ---
def test_nba_adapter_numbers_equal_the_existing_study_summary(monkeypatch):
    rng = random.Random(12)
    rows = []
    for i in range(200):
        p = rng.uniform(0.3, 0.7)
        rows.append({"game_id": f"g{i}", "season": 2024, "cutoff_at": _t(i), "tipoff_at": _t(i, 12), "line": None,
                     "realised": 0 if rng.random() < p else 2, "vectors": {"elo": (p, 0.0, 1 - p)}})
    lines = [{**r, "line": 220.5, "realised": rng.choice((0, 1, 2)), "vectors": {"points_normal": (0.45, 0.1, 0.45)}} for r in rows]
    tables = {"winner": rows, "total": lines}
    monkeypatch.setitem(ar._STUDY, ("nba", "validation", str(ar.config.db_path("nba"))),
                        {"tables": tables, "members": {"winner": ["elo"], "lines": ["points_normal"]}, "rows": [], "events": {},
                         "recorder": None, "dataset": "x", "features": None, "walk": None, "specs": {}})
    a = ar.nba_nhl_adapter("nba", candidates=[])
    walked = a["walk"](None, [2024], "elo")
    assert len(walked) == 200 and all(r["forecast_cutoff"] <= r["start_at"] for r in walked)
    kind = a["markets"]["winner"]["kind"]
    new = ar.model_metrics(walked, kind)
    old = ar._summary("winner", rows, lambda r: r["vectors"]["elo"])
    assert new["log_loss"] == pytest.approx(old["log_loss"], abs=1e-12) and new["brier"] == pytest.approx(old["brier"], abs=1e-12)
    assert (new["wins"], new["losses"]) == (old["wins"], old["losses"])
    tot = ar.model_metrics(a["walk"](None, [2024], "points_normal"), "line")
    old_t = ar._summary("total", lines, lambda r: r["vectors"]["points_normal"])
    assert tot["log_loss"] == pytest.approx(old_t["log_loss"], abs=1e-12) and tot["n"] == 200       # pushes scored via mid mass
    assert a["markets"]["total"]["line_basis"].startswith("model line")
    assert a["periods"]["validation"] == [2024] and not set(a["periods"]["holdout"]) & {2022, 2023, 2024}


# --- status ---
def test_v3_status_reports_each_sport_with_attempts_blockers_and_next_run(client, monkeypatch, exp):
    s = synthetic(n=300, seed=13, edge=0.8)
    a = fixture_adapter({"inc": s["inc"], "cand": s["cand"], "cand2": s["cand"]})
    ar.daily_v3(["nba", "soccer"], exp, max_items=1, adapter_fn=_adapters(nba=a))
    monkeypatch.setenv("NBA_AUTORESEARCH_DIR", str(exp))
    out = client.get("/api/research/autoresearch").json()
    v3 = out["v3"]
    assert v3["protocol"] == "sports-research-v3" and set(v3["sports"]) == {"nba", "soccer"}
    nba, soc = v3["sports"]["nba"], v3["sports"]["soccer"]
    assert nba["last_attempt"]["outcome"] == "skipped" and "resumes with" in nba["next_run"] and nba["candidate_count"] == 2
    assert soc["last_attempt"]["outcome"] == "refused" and soc["last_success_at"] is None and soc["blockers"]
    assert v3["last_daily"]["outcomes"] == {"nba": "skipped", "soccer": "refused"}
    assert str(exp) not in json.dumps(out)


def test_nhl_adapter_excludes_candidates_that_use_postgame_goalie_inputs():
    names = [n for n, m in ar.nba_nhl_adapter("nhl")["models"].items() if m["role"] == "candidate"]
    assert names and not any("goalie" in n for n in names)
    assert not any("goalie" in n for n in ar.nba_nhl_adapter("nba")["models"])


def test_a_missing_store_is_refused_and_never_created(monkeypatch, tmp_path):
    snap = tmp_path / "snap"
    snap.mkdir()
    monkeypatch.setenv("NBA_DATA_DIR", str(snap))
    a = fixture_adapter({"inc": []})
    a.pop("connect")
    r = ar.run_sport_v3("nba", tmp_path / "e", adapter_fn=_adapters(nba=a))
    assert r["outcome"] == "refused" and "no nba store" in r["reason"] and not list(snap.iterdir())


def test_reference_line_results_are_reported_apart_and_never_as_bookmaker_skill():
    recs = [rec(i, "spread", p=0.5, mid=0.0, outcome="a" if i % 2 else "b", line=-3.0, line_source="reference_model") for i in range(60)]
    a = fixture_adapter({"inc": recs}, markets={"spread": {"kind": "line", "settlement": "push refunds"}})
    e = ar.evaluate_records(a, {"inc": recs})["markets"]["spread"]
    assert e["line_sources"] == {"reference_model": 60} and "never as skill against a bookmaker" in e["reference_line_note"]
    assert e["by_line_source"]["reference_model"]["n"] == 60


def test_adapter_locations_are_one_mapping_and_an_adapter_data_check_can_refuse(exp):
    assert set(ar.ADAPTER_FUNCTIONS) == {"nfl", "ncaab", "soccer"} and ar.ADAPTER_FUNCTIONS["nfl"].startswith("nfl_challengers:")
    a = fixture_adapter({"inc": []}, validate_data=lambda conn: ["snapshot is stale"])
    r = ar.run_sport_v3("nba", exp, adapter_fn=_adapters(nba=a))
    assert r["outcome"] == "refused" and "snapshot is stale" in r["reason"]


def test_push_probability_alias_and_chunked_walks_resume_without_rescoring(exp, monkeypatch):
    a = fixture_adapter({"inc": []}, markets={"total": {"kind": "line", "settlement": "push refunds"}})
    out = ar.validate_records(a, [rec(0, "total", line=2.0) | {"probs": {"a": 0.45, "push": 0.1, "b": 0.45}, "outcome": "push"}], [2020])
    assert out[0]["probs"] == {"a": 0.45, "mid": 0.1, "b": 0.45}
    recs = [rec(i, p=0.6, outcome="a", season=2020, days=i) for i in range(10)]
    calls, jump, real = [], [0], ar.time.time
    monkeypatch.setattr(ar.time, "time", lambda: real() + jump[0])

    def walk(conn, seasons, name, max_work=None, resume=None, state=None):
        calls.append(resume)
        jump[0] = 10 ** 6 if len(calls) == 1 else 0                  # the first chunk uses up the time budget
        start = 0 if resume is None else int(resume)
        chunk = recs[start:start + max_work]
        state.update(next=str(start + len(chunk)), done=start + len(chunk) >= len(recs), work=len(chunk))
        return chunk
    ad = fixture_adapter({"inc": recs}, chunk_work=4) | {"walk": walk}
    r = ar.run_sport_v3("nba", exp, max_seconds=900, adapter_fn=_adapters(nba=ad))        # one chunk, then out of time
    assert r["outcome"] == "skipped" and calls == [None]
    r = ar.run_sport_v3("nba", exp, adapter_fn=_adapters(nba=ad))
    assert calls == [None, "4", "8"] and r["outcome"] == "no_candidates" and r["evaluation"]["winner"]["n"] == 10
