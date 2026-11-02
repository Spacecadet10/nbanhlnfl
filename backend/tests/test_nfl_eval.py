"""NFL winner evaluation (docs/NFL_PLAN.md, "NFL evaluation plan"): guards and pinned rules on synthetic data only.
No real NFL result, rate or score is read here."""
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app import db, nfl_eval
from app.models.base import PregameGame, Result

UTC = timezone.utc


def _fmt(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    conn = db.connect(tmp_path / "nfl.sqlite")
    db.migrate(conn)
    db.claim_store(conn, "nfl")
    conn.execute("INSERT INTO raw_responses (raw_id, source, endpoint, request_key, content_hash, byte_size, path, "
                 "importer_version, first_retrieved_at) VALUES (1, 'synthetic', 'none', 'k', 'h', 0, 'none', 'test', "
                 "'2026-11-01T00:00:00Z')")
    return conn


def add(conn, gid, season, kickoff, home="1", away="2", hs=24, as_=17, stype="regular", neutral=False, status="final"):
    avail = _fmt(kickoff + timedelta(hours=4)) if status == "final" else None
    conn.execute("INSERT INTO teams (team_id, abbr, name, first_seen_at) VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING", (home, f"T{home}", f"Team {home}", "2026-11-01T00:00:00Z"))
    conn.execute("INSERT INTO teams (team_id, abbr, name, first_seen_at) VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING", (away, f"T{away}", f"Team {away}", "2026-11-01T00:00:00Z"))
    conn.execute(
        "INSERT INTO games (game_id, season, season_type, tipoff_at, home_team_id, away_team_id, neutral_site, status, "
        "home_score, away_score, first_seen_at, result_first_seen_at, result_available_at, result_availability_basis, updated_at, last_raw_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
        (gid, season, stype, _fmt(kickoff), home, away, int(neutral), status, hs if status == "final" else None,
         as_ if status == "final" else None, "2026-11-01T00:00:00Z", avail, avail, "assumed_tipoff_plus_4h" if avail else None,
         "2026-11-01T00:00:00Z"))
    conn.commit()


def season_games(conn, season, n=40, start=None, tie_every=0):
    start = start or datetime(season, 9, 10, 17, tzinfo=UTC)
    for i in range(n):
        h, a = str(1 + i % 8), str(1 + (i + 3) % 8)
        hs, as_ = (20, 20) if tie_every and i % tie_every == 0 else ((27, 13) if i % 3 else (10, 24))
        add(conn, f"nfl:{season}-{i}", season, start + timedelta(days=7 * (i // 4), hours=i % 4), h, a, hs, as_)


# --- Reserved test seasons --------------------------------------------------------------------------------------
def test_validation_never_reads_reserved_seasons(store):
    season_games(store, 2021, n=8)
    season_games(store, 2022, n=8)
    rows = nfl_eval.load_rows(store, through=2021)
    assert {r.game.season for r in rows} == {2021}
    for through in (2022, 2025):
        with pytest.raises(nfl_eval.TestSeasonsLocked):
            nfl_eval.load_rows(store, through=through)


def test_test_run_needs_a_sealed_choice_and_logs_every_access(store, tmp_path):
    season_games(store, 2022, n=8)
    log = tmp_path / "eval" / "nfl_test_access.jsonl"
    with pytest.raises(nfl_eval.TestSeasonsLocked):
        nfl_eval.load_rows(store, through=2025, sealed=None, access_log=log)
    choice = nfl_eval.seal_choice({"model": "constant", "config": {}, "calibration": {"accepted": False}})
    tampered = {**choice, "model": "elo"}
    with pytest.raises(nfl_eval.TestSeasonsLocked):
        nfl_eval.load_rows(store, through=2025, sealed=tampered, access_log=log)
    assert not log.exists()
    rows = nfl_eval.load_rows(store, through=2025, sealed=choice, access_log=log, purpose="unit test")
    assert {r.game.season for r in rows} == {2022}
    entries = [json.loads(x) for x in log.read_text().splitlines()]
    assert len(entries) == 1 and entries[0]["choice_sha256"] == choice["sha256"] and entries[0]["purpose"] == "unit test"


# --- Point in time: two-hour cutoff ------------------------------------------------------------------------------
def test_cutoff_is_two_hours_before_kickoff():
    assert nfl_eval.cutoff_for("2025-11-02T18:00:00Z") == "2025-11-02T16:00:00Z"


def test_same_day_results_reach_the_home_rate_only_when_available_at_the_cutoff(store):
    sunday = datetime(2016, 10, 2, tzinfo=UTC)
    early = sunday.replace(hour=17)                       # 1 p.m. ET game, available 5 p.m. ET (21:00Z)
    add(store, "nfl:early", 2016, early, "1", "2", 30, 10)
    add(store, "nfl:late", 2016, sunday.replace(hour=20, minute=25), "3", "4")       # cutoff 18:25Z: early not yet
    add(store, "nfl:night", 2016, sunday.replace(hour=23, minute=59) + timedelta(hours=1, minutes=21), "5", "6")  # cutoff 23:20Z
    preds = {p["game_id"]: p for p in nfl_eval.walk(nfl_eval.NflHomeRate(), nfl_eval.load_rows(store, through=2016))}
    assert preds["nfl:late"]["p_home"] == 0.5               # nothing available yet: Laplace (0+1)/(0+2)
    assert preds["nfl:night"]["p_home"] == pytest.approx(2 / 3)   # the 1 p.m. home win counts


# --- Ties, neutral sites, multiplier ------------------------------------------------------------------------------
def _g(gid="g", season=2010, neutral=False, home="1", away="2"):
    return PregameGame(gid, season, "regular", "2010-10-10T17:00:00Z", home, away, neutral)


def test_tie_is_half_a_win_for_elo_and_home_rate_and_moves_ratings_with_the_multiplier():
    for mov in (False, True):
        elo = nfl_eval.NflElo(nfl_eval.EloConfig(k=20, home_advantage=0, carry=1.0, mov=mov))
        elo.update(Result(_g(home="1", away="2"), 20, 20))
        assert elo.ratings["1"] == elo.ratings["2"] == 1500.0              # equal teams, tie = expectation: no change
        elo.ratings["1"] = 1600.0
        elo.update(Result(_g(home="1", away="2"), 13, 13))
        assert elo.ratings["1"] < 1600.0 and elo.ratings["2"] > 1500.0     # favourite tied: loses rating
    rate = nfl_eval.NflHomeRate()
    rate.update(Result(_g(), 17, 17))
    rate.update(Result(_g(), 24, 3))
    assert rate.predict(_g()) == pytest.approx((1.5 + 1) / (2 + 2))
    rate.update(Result(_g(neutral=True), 30, 0))                            # neutral results are not counted
    assert rate.predict(_g()) == pytest.approx((1.5 + 1) / (2 + 2)) and rate.predict(_g(neutral=True)) == 0.5


def test_margin_multiplier_formula():
    m = nfl_eval.margin_multiplier
    assert m(7, 100) == pytest.approx(math.log(8) * 2.2 / (0.001 * 100 + 2.2))
    assert m(0, 55) == pytest.approx(math.log(2))                          # tie: margin 1, winner_diff 0


def test_neutral_site_has_no_home_advantage():
    elo = nfl_eval.NflElo(nfl_eval.EloConfig(k=20, home_advantage=75, carry=1.0, mov=False))
    assert elo.predict(_g(neutral=True)) == 0.5 and elo.predict(_g()) > 0.5


def test_ties_are_void_for_scoring_and_counted(store):
    season_games(store, 2010, n=12, tie_every=4)
    preds = nfl_eval.walk(nfl_eval.Constant(), nfl_eval.load_rows(store, through=2010))
    scored, ties = nfl_eval.scorable(preds)
    assert len(ties) == 3 and len(scored) == 9 and all(not p["tie"] for p in scored)


# --- Development tie-break and the validation choice ----------------------------------------------------------------
def test_tiebreak_order():
    C = nfl_eval.EloConfig
    tied = [C(k=20, home_advantage=50, carry=0.75, mov=True), C(k=20, home_advantage=50, carry=0.75, mov=False),
            C(k=25, home_advantage=0, carry=1.0, mov=False), C(k=10, home_advantage=75, carry=1.0, mov=False),
            C(k=10, home_advantage=25, carry=1.0, mov=False)]
    assert nfl_eval.best_config([(c, 0.2412341) for c in tied]) == C(k=10, home_advantage=25, carry=1.0, mov=False)
    assert nfl_eval.best_config([(tied[0], 0.2412339), (tied[4], 0.2412351)]) == tied[0]     # rounds apart: lower wins
    assert len(nfl_eval.elo_grid()) == 5 * 4 * 4 * 2


def _pairs(ps_a, ps_b, ys):
    mk = lambda ps: [{"game_id": f"g{i}", "p_home": p, "home_won": y, "cutoff_at": _fmt(datetime(2018, 9, 1, tzinfo=UTC) + timedelta(days=i))}
                     for i, (p, y) in enumerate(zip(ps, ys))]
    return mk(ps_a), mk(ps_b)


def test_choice_rule_with_both_fallbacks():
    ys = [i % 3 != 0 for i in range(300)]
    good = [0.8 if y else 0.3 for y in ys]
    mid = [0.62 if y else 0.5 for y in ys]
    flat = [0.5] * 300
    pick = lambda e, h: nfl_eval.choose({"constant": _pairs(flat, flat, ys)[0], "home_rate": _pairs(h, h, ys)[0],
                                         "elo": _pairs(e, e, ys)[0]})["model"]
    assert pick(good, mid) == "elo"
    assert pick(mid, good) == "home_rate"                 # Elo not lowest → home rate, which beats 0.5
    worse = [0.45 if y else 0.55 for y in ys]
    assert pick(worse, worse) == "constant"               # neither beats 0.5 → constant


# --- Platt scaling ----------------------------------------------------------------------------------------------
def _base_preds(seasons, n=60, p=0.7, rate=0.55):
    out = []
    for s in seasons:
        for i in range(n):
            out.append({"game_id": f"{s}-{i}", "season": s, "p_home": p, "home_won": (i / n) < rate, "tie": False,
                        "cutoff_at": _fmt(datetime(s, 10, 1, tzinfo=UTC) + timedelta(hours=i))})
    return out


def test_platt_uses_only_earlier_scored_seasons_and_falls_back():
    preds = _base_preds([2003, 2004]) + _base_preds([2005], n=40) + _base_preds([2006, 2007])
    preds.append({**preds[-1], "game_id": "tie", "season": 2006, "tie": True})
    cal, notes = nfl_eval.platt_walk(preds)
    by = {p["game_id"]: p for p in cal}
    assert by["2005-0"]["p_home"] == 0.7 and notes[2005] == "fallback: 0 pairs (< 50)"     # warm-up never used
    assert by["2006-0"]["p_home"] == 0.7 and notes[2006] == "fallback: 40 pairs (< 50)"
    assert notes[2007] == "fitted on 2005-2006 (100 pairs)" and by["2007-0"]["p_home"] != 0.7


def test_platt_fit_failure_or_bad_slope_uses_raw(monkeypatch):
    def boom(*_a, **_k):
        raise ZeroDivisionError("singular")
    monkeypatch.setattr(nfl_eval, "fit_logistic", boom)
    a_b, note = nfl_eval.fit_platt([(0.7, True)] * 60)
    assert a_b is None and "ZeroDivisionError" in note
    monkeypatch.setattr(nfl_eval, "fit_logistic", lambda *_a, **_k: [0.1, -0.5])
    assert nfl_eval.fit_platt([(0.7, True)] * 60)[0] is None


def test_calibration_acceptance_needs_brier_log_loss_and_the_brier_interval():
    ys = [i % 3 != 0 for i in range(400)]
    raw = [0.55] * 400
    better = [0.8 if y else 0.3 for y in ys]
    acc = nfl_eval.accept_calibration(*_pairs(better, raw, ys))
    assert acc["accepted"] and acc["brier_ci95"][1] < 0 and acc["log_loss_calibrated"] < acc["log_loss_raw"]
    flipped = [i for i, y in enumerate(ys) if y][:40]                     # 40 near-certain misses: log loss explodes,
    ys2 = [False if i in flipped else y for i, y in enumerate(ys)]       # Brier still clearly better than raw
    overconfident = [0.99999 if i in flipped else (0.99 if y else 0.35) for i, y in enumerate(ys2)]
    acc = nfl_eval.accept_calibration(*_pairs(overconfident, raw, ys2))
    assert not acc["accepted"] and "log loss" in acc["reason"]


def test_final_calibrator_is_fitted_on_2005_2021_only_and_frozen_for_the_test():
    preds = _base_preds(range(2003, 2022), n=30) + _base_preds([2022], n=30, rate=0.9)
    with pytest.raises(ValueError):
        nfl_eval.final_calibrator(preds)                  # a 2022 prediction must never reach the final fit
    a_b = nfl_eval.final_calibrator([p for p in preds if p["season"] <= 2021])
    assert a_b["seasons"] == [2005, 2021] and a_b["pairs"] == 17 * 30
    frozen = nfl_eval.apply_frozen(_base_preds([2023]), a_b)
    assert len({p["p_home"] for p in frozen}) == 1


# --- Line markets and thresholds (preregistered 2026-11-01) -------------------------------------------------------
def test_nfl_distribution_keeps_ties_and_handles_pushes():
    d = nfl_eval.NflMarginTotal(margin=0.0, sd_margin=13.5, total=44.0, sd_total=13.5)
    a, mid, b = d.market("spread", 0.0)                        # pick'em: a zero margin is a push, possible in the NFL
    assert mid > 0 and a == pytest.approx(b)
    a, mid, b = d.market("spread", -3.0)                       # home −3: push when the home team wins by exactly 3
    assert mid > 0 and a < b
    a, mid, b = d.market("total", 44.5)
    assert mid == 0 and a == pytest.approx(0.5, abs=0.02)


def test_points_model_uses_the_nfl_priors_and_refuses_seasons_after_development(store):
    m = nfl_eval.NflPoints(nfl_eval.PointsConfig(eta=0.05, carry=1.0))
    ph, pa = m.expected(_g())
    assert ph == pa == 22.0 and m.var_margin == 13.5 ** 2 and m.var_total == 13.5 ** 2
    season_games(store, 2017, n=4)
    with pytest.raises(ValueError):
        nfl_eval.tune_points(nfl_eval.load_rows(store, through=2017))
    assert len(nfl_eval.points_grid()) == 4 * 3


def test_points_tiebreak_prefers_carry_one_then_smaller_eta():
    P = nfl_eval.PointsConfig
    trials = [(P(eta=0.05, carry=0.75), 300.00004), (P(eta=0.1, carry=1.0), 300.00001), (P(eta=0.02, carry=1.0), 300.00003)]
    assert nfl_eval.best_points(trials) == P(eta=0.02, carry=1.0)


def test_implied_team_lines():
    assert nfl_eval.implied_team_lines(spread=-3.0, total=44.5) == (23.5, 20.5)    # home −3: (44.5+3)/2=23.75 → 23.5
    assert nfl_eval.implied_team_lines(spread=6.5, total=41.0) == (17.5, 23.5)     # 17.25 → 17.5, 23.75 → 23.5


def test_line_collection_never_targets_reserved_seasons(store):
    season_games(store, 2021, n=3)
    season_games(store, 2022, n=3)
    assert {e[:4] for e in nfl_eval.line_events(store)} == {"2021"}               # synthetic ids are "<season>-<i>"
    with pytest.raises(nfl_eval.TestSeasonsLocked):
        nfl_eval.line_events(store, through=2022)


def test_grading_removes_pushes_and_counts_missing_lines():
    params = {"g1": {"margin": 3.0, "sd_margin": 13.5, "total": 44.0, "sd_total": 13.5},
              "g2": {"margin": -1.0, "sd_margin": 13.5, "total": 40.0, "sd_total": 13.5},
              "g3": {"margin": 0.0, "sd_margin": 13.5, "total": 40.0, "sd_total": 13.5}}
    meta = {g: {"season": 2018, "cutoff_at": "2018-10-01T15:00:00Z", "home_score": hs, "away_score": as_}
            for g, (hs, as_) in {"g1": (27, 24), "g2": (20, 17), "g3": (10, 13)}.items()}
    lines = {"g1": {"spread": -3.0, "total": 44.5}, "g2": {"spread": 1.5, "total": 37.5}}      # g3: no line
    graded, counts = nfl_eval.grade_market("spread", params, meta, lines)
    assert counts == {"scored": 1, "push": 1, "no_line": 1}                                  # g1 home by 3 at −3: push
    assert graded[0]["game_id"] == "g2" and graded[0]["home_won"] is True                    # 3 + 1.5 > 0: home covers


def test_skill_verdict_and_line_markets_without_skill_never_get_a_best_pick():
    ys = [i % 2 == 0 for i in range(600)]
    sharp = _pairs([0.7 if y else 0.3 for y in ys], [0.5] * 600, ys)[0]
    coin = _pairs([0.52 if i % 4 else 0.48 for i in range(600)], [0.5] * 600, ys)[0]
    assert nfl_eval.skill_verdict(sharp)["proven"] and not nfl_eval.skill_verdict(coin)["proven"]
    table = nfl_eval.threshold_table(sharp, line_market=True, proven=False)
    assert table["best"] is None and "no proven skill" in table["reason"]
    table = nfl_eval.threshold_table(sharp, line_market=True, proven=True)
    assert table["rows"][0]["threshold"] == 0.5 and table["rows"][0]["graded"] == 600 and table["break_even"] == pytest.approx(110 / 210)


# --- Review findings (Codex, 2026-11-01): regressions ------------------------------------------------------------
def test_implied_team_totals_never_get_a_best_pick_even_with_strong_data():
    ys = [i % 2 == 0 for i in range(600)]
    sharp = _pairs([0.8 if y else 0.2 for y in ys], [0.5] * 600, ys)[0]
    for market in ("team_total_home", "team_total_away"):
        t = nfl_eval.threshold_table(sharp, line_market=True, proven=True, market=market)
        assert t["best"] is None and t["strong"] is False and "implied" in t["reason"]


def test_closing_line_results_are_retrospective_and_never_unlock_a_best_pick():
    ys = [i % 2 == 0 for i in range(600)]
    sharp = _pairs([0.8 if y else 0.2 for y in ys], [0.5] * 600, ys)[0]
    v = nfl_eval.skill_verdict(sharp)
    assert v["proven"] and v["scope"] == "retrospective closing-line test" and "not a profitable edge" in v["note"]
    t = nfl_eval.threshold_table(sharp, line_market=True, proven=True, market="spread")
    assert t["best_pick_allowed"] is False and "forecast-time" in t["best_pick_note"]


def test_winner_thresholds_use_the_probability_version_being_sealed():
    raw = [{"game_id": "g", "season": 2018, "p_home": 0.6, "tie": False}]
    cal = [{"game_id": "g", "season": 2018, "p_home": 0.66, "p_raw": 0.6, "tie": False}]
    assert nfl_eval.sealed_version(raw, cal, accepted=True) is cal
    assert nfl_eval.sealed_version(raw, cal, accepted=False) is raw
    assert nfl_eval.sealed_version(raw, None, accepted=False) is raw


# --- Reserved test run (docs/NFL_PLAN.md "Reserved test run protocol"): one shot, frozen, logged -----------------
def _test_store(store):
    for season in (2002, 2017, 2021, 2022, 2025):
        season_games(store, season, n=12)


def _frozen(store, **over):
    rows = nfl_eval.load_rows(store, through=nfl_eval.LAST_OPEN)
    body = {"sport": "nfl", "market": "winner", "model": "elo",
            "config": {"k": 20, "home_advantage": 50, "carry": 0.5, "mov": True},
            "cutoff": "kickoff - 120 min", "calibration": {"accepted": False, "final": None},
            "line_markets": {"score_model": {"name": "nfl_points_normal", "eta": 0.05, "carry": 0.75},
                             "markets": {"winner": {"best_threshold": 0.75, "strong": True}}},
            "dataset_version": nfl_eval.backtest.dataset_version(rows)}
    body.update(over)
    return nfl_eval.seal_choice(body)


def _log(path):
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def test_test_run_refuses_bad_seals_before_reading_or_logging(store, tmp_path):
    _test_store(store)
    log = tmp_path / "log" / "access.jsonl"
    good = _frozen(store)
    for bad in ({**good, "config": {**good["config"], "k": 30}},                              # tampered
                _frozen(store, line_markets={"markets": {}}),                                 # no frozen winner threshold
                _frozen(store, dataset_version="0" * 16)):                                    # store changed since sealing
        with pytest.raises(nfl_eval.TestSeasonsLocked):
            nfl_eval.run_test(store, bad, access_log=log, out_dir=tmp_path / "out")
    assert _log(log) == [] and not (tmp_path / "out").exists()


def test_test_run_is_one_shot_and_logs_start_and_completion(store, tmp_path):
    _test_store(store)
    log, out = tmp_path / "log" / "access.jsonl", tmp_path / "out"
    sealed = _frozen(store)
    report = nfl_eval.run_test(store, sealed, access_log=log, out_dir=out)
    entries = _log(log)
    assert [e["purpose"] for e in entries] == ["test run: start", "test run: complete"]
    assert all(e["choice_sha256"] == sealed["sha256"] for e in entries)
    assert (out / "nfl_test_report.json").is_file() and report["seasons"]["test"] == list(nfl_eval.TEST)
    with pytest.raises(nfl_eval.TestRunAlreadyDone):                                          # second run refused
        nfl_eval.run_test(store, sealed, access_log=log, out_dir=tmp_path / "out2")
    log2 = tmp_path / "log2.jsonl"
    with pytest.raises(nfl_eval.TestRunAlreadyDone):                                          # existing output refused
        nfl_eval.run_test(store, sealed, access_log=log2, out_dir=out)
    assert _log(log2) == [] and len(_log(log)) == 2


def test_test_run_fits_nothing_and_scores_only_reserved_seasons(store, tmp_path, monkeypatch):
    _test_store(store)
    for name in ("tune", "tune_points", "fit_platt", "final_calibrator", "platt_walk", "choose"):
        monkeypatch.setattr(nfl_eval, name, lambda *a, **k: (_ for _ in ()).throw(AssertionError(f"{name} called")))
    report = nfl_eval.run_test(store, _frozen(store), access_log=tmp_path / "l.jsonl", out_dir=tmp_path / "o")
    w = report["winner"]
    assert w["config"] == {"k": 20, "home_advantage": 50, "carry": 0.5, "mov": True}
    assert w["games"]["scored"] == 24 and set(w["by_season"]["elo"]) == {2022, 2025}        # 12 + 12 test games
    assert set(w["summary"]) == {"elo", "home_rate", "constant", "chosen"} and "confirmed" in w
    assert w["summary"]["chosen"] == w["summary"]["elo"] and w["probabilities"] == "raw"


def test_test_run_reports_the_frozen_threshold_and_never_rechooses(store, tmp_path):
    _test_store(store)
    report = nfl_eval.run_test(store, _frozen(store), access_log=tmp_path / "l.jsonl", out_dir=tmp_path / "o")
    th = report["winner"]["frozen_threshold"]
    assert th["threshold"] == 0.75 and set(th) >= {"graded", "won", "win_rate", "ci95", "holds"}
    assert th["holds"] == bool(th["ci95"] and th["ci95"][0] > 0.5)
    table = report["winner"]["thresholds_reported_only"]
    assert "best" not in table and "strong" not in table


def test_test_run_line_markets_not_run_without_reserved_lines(store, tmp_path):
    _test_store(store)
    report = nfl_eval.run_test(store, _frozen(store), access_log=tmp_path / "l.jsonl", out_dir=tmp_path / "o")
    assert report["line_markets"]["status"] == "not_run" and report["winner"]["summary"]["elo"]["n"] == 24


def test_reserved_line_events_need_the_seal_and_are_logged(store, tmp_path):
    _test_store(store)
    log = tmp_path / "access.jsonl"
    with pytest.raises(nfl_eval.TestSeasonsLocked):
        nfl_eval.line_events(store, through=2025, sealed={"sha256": "x"}, access_log=log)
    sealed = _frozen(store)
    events = nfl_eval.line_events(store, through=2025, sealed=sealed, access_log=log)
    assert {e[:4] for e in events} == {"2022", "2025"}
    assert [e["purpose"] for e in _log(log)] == ["collect reserved lines (odds only)"]


def test_test_run_applies_an_accepted_frozen_calibrator_without_refitting(store, tmp_path, monkeypatch):
    _test_store(store)
    monkeypatch.setattr(nfl_eval, "fit_platt", lambda *a, **k: (_ for _ in ()).throw(AssertionError("refit")))
    sealed = _frozen(store, calibration={"accepted": True, "final": {"a": 2.0, "b": 0.0}})
    w = nfl_eval.run_test(store, sealed, access_log=tmp_path / "l.jsonl", out_dir=tmp_path / "o")["winner"]
    assert w["probabilities"] == "calibrated (frozen)" and w["summary"]["chosen"] != w["summary"]["elo"]
