"""NCAAB evaluation stages on a synthetic multi-season store: development tuning reads only warm-up and development seasons,
validation runs once and seals the decision, the test period runs once and every access is logged."""
import json
import random
from datetime import datetime, timedelta, timezone

import pytest

import ncaab_support as sup
from test_ncaab_eval import add
from app import ncaab_cards, ncaab_eval
from app.models import college

SEASONS = {"warmup": (2013,), "development": (2014, 2015), "validation": (2016,), "test": (2017,)}


def _season(conn, rng, season, strengths, start_event):
    day = datetime(season - 1, 11, 10, 0, 0, tzinfo=timezone.utc)
    teams = list(strengths)
    event = start_event
    for d in range(60):
        rng.shuffle(teams)
        for i in range(0, len(teams) - 1, 2):
            h, a = teams[i], teams[i + 1]
            neutral = int(rng.random() < 0.15)
            poss = 68 + rng.gauss(0, 4)
            mh = poss * (1.0 + strengths[h] / 100) + (0 if neutral else 3) + rng.gauss(0, 9)
            ma = poss * (1.0 + strengths[a] / 100) + rng.gauss(0, 9)
            hs, as_ = int(round(mh)), int(round(ma))
            if hs == as_:
                hs += 1
            add(conn, str(event), (day + timedelta(days=d, hours=i % 5)).strftime("%Y-%m-%dT%H:%M:%SZ"), h, a, hs, as_,
                neutral=neutral, season=season, box=((int(poss * 0.85), int(poss * 0.3)), (int(poss * 0.85), int(poss * 0.3))))
            event += 1
    return event


@pytest.fixture
def multi(tmp_path, monkeypatch):
    conn = sup.make_store(tmp_path, monkeypatch)
    rng = random.Random(7)
    strengths = {str(t): rng.gauss(0, 8) for t in range(10, 22)}
    event = 1000
    for season in range(2013, 2018):
        event = _season(conn, rng, season, strengths, event)
        strengths = {t: 0.7 * s + rng.gauss(0, 3) for t, s in strengths.items()}
    # a non-D-I game in every season that would be a huge outlier if it were rated
    for season in range(2013, 2018):
        add(conn, str(event), f"{season - 1}-12-01T00:00:00Z", "10", "99", 150, 20, season=season, d1=False)
        event += 1
    monkeypatch.setattr(ncaab_eval, "PERIODS", SEASONS)
    monkeypatch.setattr(ncaab_eval, "ELO_GRID", {"k": (20, 40), "home_advantage": (0, 75), "carry": (0.8, 1.0),
                                                "mov": (False, True)})
    monkeypatch.setattr(ncaab_eval, "EFF_GRID", {"eta": (0.02, 0.08), "carry": (0.7,), "w0": (4, 8)})
    monkeypatch.setattr(ncaab_eval, "BOOTSTRAP_SAMPLES", 200)
    college.clear_cache()
    return conn


def test_development_tuning_refuses_validation_and_test_seasons(multi):
    rows, box = ncaab_eval.load(multi, "validation", ncaab_eval.DEFAULT_CUTOFF)
    with pytest.raises(ncaab_eval.NcaabEvaluationError):
        ncaab_eval.tune(rows, box)
    dev_rows, dev_box = ncaab_eval.load(multi, "development", ncaab_eval.DEFAULT_CUTOFF)
    assert {r.game.season for r in dev_rows} <= {2013, 2014, 2015}
    assert all(r.game.season_type in ("regular", "postseason") for r in dev_rows)


def test_loading_a_period_never_reads_later_seasons(multi):
    for period, allowed in (("development", {2013, 2014, 2015}), ("validation", {2013, 2014, 2015, 2016})):
        rows, _ = ncaab_eval.load(multi, period, ncaab_eval.DEFAULT_CUTOFF)
        assert {r.game.season for r in rows} == allowed


def test_grid_ties_follow_the_preregistered_order():
    trials = [{"config": {"k": 40, "home_advantage": 75, "carry": 1.0, "mov": False}, "log_loss": 0.5},
              {"config": {"k": 20, "home_advantage": 75, "carry": 1.0, "mov": False}, "log_loss": 0.5 + 1e-12},
              {"config": {"k": 20, "home_advantage": 75, "carry": 0.8, "mov": False}, "log_loss": 0.5},
              {"config": {"k": 20, "home_advantage": 75, "carry": 1.0, "mov": True}, "log_loss": 0.5}]
    assert ncaab_eval.pick_elo(trials)["config"] == {"k": 20, "home_advantage": 75, "carry": 1.0, "mov": False}
    eff = [{"config": {"eta": 0.08, "carry": 0.9, "w0": 4}, "log_loss": 0.4},
           {"config": {"eta": 0.02, "carry": 0.9, "w0": 8}, "log_loss": 0.4},
           {"config": {"eta": 0.02, "carry": 0.9, "w0": 4}, "log_loss": 0.4}]
    assert ncaab_eval.pick_eff(eff)["config"] == {"eta": 0.02, "carry": 0.9, "w0": 4}


def test_validation_once_then_test_once_with_the_access_log(multi, tmp_path, monkeypatch):
    path = tmp_path / "decision.json"
    monkeypatch.setattr(college, "DECISION_PATH", path)
    report = ncaab_eval.evaluate_validation(multi, decision_path=path, out_dir=tmp_path / "val", cutoff=ncaab_eval.DEFAULT_CUTOFF,
                                            command="synthetic", log=lambda m: None)
    d = college.load_decision(path)
    assert d["selected_on"] == "validation" and d["validation_seasons"] == [2016]
    assert d["winner_model"] in ("college_elo", "college_eff")
    assert set(d["markets"]) == {"spread", "total", "team_total_home", "team_total_away"}
    assert all(v["model"] in ("college_eff", "elo_margin", "running_points", "research") for v in d["markets"].values())
    assert report["validation"]["winner"]["college_elo"]["n"] > 0
    assert report["development"]["winner"]["college_elo"]["n"] > 0
    assert {r["season"] for r in report["validation"]["winner"]["college_elo"]["by_season"]} == {2016}
    assert "winner_minus_elo_log_loss" in report["validation"]["selection"]
    for m in ("spread", "total", "team_total_home", "team_total_away"):
        assert report["validation"]["markets"][m]["decision"] == d["markets"][m]["model"]
        assert report["validation"]["markets"][m]["after_game_lines"]["games"] == 0
    assert (tmp_path / "val" / "report.json").exists() and (tmp_path / "val" / "predictions.csv").exists()
    with pytest.raises(ncaab_eval.NcaabEvaluationError):            # validation is not rerun
        ncaab_eval.evaluate_validation(multi, decision_path=path, out_dir=tmp_path / "val2", cutoff=ncaab_eval.DEFAULT_CUTOFF,
                                       command="synthetic", log=lambda m: None)
    access = tmp_path / "eval" / "test_access_ncaab.jsonl"
    assert not access.exists()
    test = ncaab_eval.evaluate_test(multi, decision_path=path, out_dir=tmp_path / "test", cutoff=ncaab_eval.DEFAULT_CUTOFF,
                                    log=lambda m: None)
    entries = [json.loads(x) for x in access.read_text().splitlines()]
    assert len(entries) == 1 and entries[0]["decision_sha256"] == d["sha256"] and entries[0]["seasons"] == [2017]
    assert test["test"]["winner"][d["winner_model"]]["n"] > 0
    assert {r["season"] for r in test["test"]["winner"]["college_elo"]["by_season"]} == {2017}
    with pytest.raises(ncaab_eval.NcaabEvaluationError):            # the test period is never rerun
        ncaab_eval.evaluate_test(multi, decision_path=path, out_dir=tmp_path / "test2", cutoff=ncaab_eval.DEFAULT_CUTOFF,
                                 log=lambda m: None)
    assert len(access.read_text().splitlines()) == 1


def test_the_test_stage_refuses_a_modified_decision(multi, tmp_path, monkeypatch):
    path = tmp_path / "decision.json"
    ncaab_eval.evaluate_validation(multi, decision_path=path, out_dir=tmp_path / "val", cutoff=ncaab_eval.DEFAULT_CUTOFF,
                                   command="synthetic", log=lambda m: None)
    d = json.loads(path.read_text())
    d["winner_model"] = "college_eff" if d["winner_model"] == "college_elo" else "college_elo"
    path.write_text(json.dumps(d))
    with pytest.raises(college.FrozenDecisionError):
        ncaab_eval.evaluate_test(multi, decision_path=path, out_dir=tmp_path / "test", cutoff=ncaab_eval.DEFAULT_CUTOFF,
                                 log=lambda m: None)
    assert not (tmp_path / "eval" / "test_access_ncaab.jsonl").exists()


def test_non_division_i_outliers_never_reach_the_walk(multi):
    rows, box = ncaab_eval.load(multi, "validation", ncaab_eval.DEFAULT_CUTOFF)
    assert not any("ncaab:99" in (r.game.home_team_id, r.game.away_team_id) for r in rows)


# --- Research adapter (all-sports research system): contract, settlement, leakage, validation, candidates ---------------
@pytest.fixture
def research(multi, tmp_path, monkeypatch):
    from test_ncaab_eval import sealed_decision
    path = tmp_path / "ncaab_frozen_decision.json"
    monkeypatch.setattr(college, "DECISION_PATH", path)
    sealed_decision(multi, path)
    ncaab_cards.clear_research_cache()
    return multi


def test_adapter_contract(research):
    a = ncaab_cards.research_adapter()
    assert a["sport"] == "ncaab" and a["data_version"]
    assert set(a["markets"]) == {"winner", "spread", "total", "team_total_home", "team_total_away"}
    assert a["markets"]["winner"]["kind"] == "binary" and a["markets"]["spread"]["kind"] == "line"
    assert all(m["settlement"] for m in a["markets"].values())
    assert {"search", "validation", "holdout"} <= set(a["periods"])
    assert not set(a["periods"]["holdout"]) & (set(a["periods"]["search"]) | set(a["periods"]["validation"]) | set(a["periods"]["warmup"]))
    roles = {n: m["role"] for n, m in a["models"].items()}
    assert roles["ncaab-incumbent"] == "incumbent" and sum(r == "candidate" for r in roles.values()) >= 3
    assert sum(r == "baseline" for r in roles.values()) >= 3
    assert all(m["description"] and m["version"] for m in a["models"].values())
    assert callable(a["walk"]) and callable(a["forecast_upcoming"])
    assert isinstance(a["unsupported"], dict)


def test_walk_records_follow_the_contract_and_settle_on_stored_scores(research):
    recs = ncaab_cards.research_walk(research, (2016,), "ncaab-incumbent")
    assert recs and {r["market"] for r in recs} == {"winner", "spread", "total", "team_total_home", "team_total_away"}
    rows = {g["game_id"]: g for g in research.execute("SELECT * FROM games WHERE season = 2016")}
    for r in recs:
        g = rows[r["game_id"]]
        assert r["season"] == 2016 and r["start_at"] == g["tipoff_at"] and r["forecast_cutoff"] < g["tipoff_at"]
        assert abs(sum(r["probs"].values()) - 1) < 1e-6
        h, a = g["home_score"], g["away_score"]       # neutral-site games too: the stored home/away is the orientation
        want = {"winner": "a" if h > a else "b",
                "spread": markets_settle("spread", r["line"], h, a), "total": markets_settle("total", r["line"], h, a),
                "team_total_home": markets_settle("team_total_home", r["line"], h, a),
                "team_total_away": markets_settle("team_total_away", r["line"], h, a)}[r["market"]]
        assert r["outcome"] == want
        if r["market"] != "winner":
            assert r["line_source"] == "reference_model" and r["quote"] is None and r["point_actual"] is not None
    keys = [(r["forecast_cutoff"], r["start_at"]) for r in recs]
    assert keys == sorted(keys)


def markets_settle(market, line, h, a):
    from app import markets
    side = markets.settle(market, line, status="final", home=h, away=a)
    return "push" if side == "mid" else side


def test_walk_refuses_the_holdout_and_does_not_open_unknown_seasons(research):
    with pytest.raises(ncaab_eval.NcaabEvaluationError):
        ncaab_cards.research_walk(research, (2017,), "ncaab-incumbent")
    with pytest.raises(ncaab_eval.NcaabEvaluationError):
        ncaab_cards.research_walk(research, (2016,), "no-such-model")


@pytest.mark.parametrize("name", ["ncaab-incumbent", "ncaab-baseline-elo", "ncaab-cand-blend-v1", "ncaab-cand-phase-v1",
                                  "ncaab-cand-hetero-v1"])
def test_walk_uses_nothing_after_the_forecast_cutoff(research, name):
    base = ncaab_cards.research_walk(research, (2016,), name)
    late = sorted(research.execute("SELECT * FROM games WHERE season = 2016 ORDER BY tipoff_at DESC LIMIT 1"))[0]
    gid = late["game_id"]
    research.execute("UPDATE games SET home_score = home_score + 40 WHERE game_id = ?", (gid,))
    research.commit()
    ncaab_cards.clear_research_cache()
    again = ncaab_cards.research_walk(research, (2016,), name)
    assert len(base) == len(again)
    for a, b in zip(base, again):
        assert (a["game_id"], a["market"]) == (b["game_id"], b["market"])
        assert a["probs"] == b["probs"] and a["line"] == b["line"] and a["point_pred"] == b["point_pred"]
        if a["game_id"] != gid:
            assert a["outcome"] == b["outcome"] or a["start_at"] >= late["tipoff_at"][:10]


def test_a_dk_quote_before_the_cutoff_sets_the_line_and_pushes_settle(research):
    g = research.execute("SELECT * FROM games WHERE season = 2016 AND status = 'final' ORDER BY tipoff_at LIMIT 1").fetchone()
    diff, tot = g["home_score"] - g["away_score"], g["home_score"] + g["away_score"]
    before = "2015-11-01T00:00:00Z"
    sup.add_quote(research, g["game_id"], "spread", float(-diff), -110, -110, before)       # home - away + line = 0: a push
    sup.add_quote(research, g["game_id"], "total", float(tot), -110, -110, "2099-01-01T00:00:00Z")   # after the cutoff: unusable
    ncaab_cards.clear_research_cache()
    recs = {r["market"]: r for r in ncaab_cards.research_walk(research, (2016,), "ncaab-incumbent") if r["game_id"] == g["game_id"]}
    assert recs["spread"]["line"] == float(-diff) and recs["spread"]["line_source"] == "bookmaker"
    assert recs["spread"]["outcome"] == "push" and recs["spread"]["probs"]["mid"] > 0
    assert recs["spread"]["quote"]["provider"] == "DraftKings" and recs["spread"]["quote"]["synthetic"] is False
    assert recs["total"]["quote"] is None and recs["total"]["line_source"] == "reference_model"


def test_candidate_fit_uses_only_earlier_seasons(research):
    recs = ncaab_cards.research_walk(research, (2015, 2016), "ncaab-cand-blend-v1")
    fits = {r["season"]: r["fit_through"] for r in recs if r["market"] == "spread"}
    assert all(t is None or t < s for s, t in fits.items())
    assert fits[2016] == 2015                      # warm-up 2013 is burn-in; 2014 and 2015 are fit seasons


def test_validate_data_reports_orientation_duplicates_and_coverage(research):
    ok = ncaab_cards.validate_data(research)
    assert ok["fixtures"]["duplicate_game_ids"] == 0 and ok["fixtures"]["duplicate_games"] == 0
    assert ok["orientation"]["stats_points_mismatch"] == 0 and ok["finals"]["missing_scores"] == 0
    assert ok["coverage"]["2016"]["games"] > 300 and ok["coverage"]["2016"]["quotes"]["spread"] == 0
    g = research.execute("SELECT game_id, home_team_id FROM games WHERE season = 2016 AND neutral_site = 1 LIMIT 1").fetchone()
    research.execute("UPDATE team_game_stats SET is_home = 1 - is_home WHERE game_id = ?", (g["game_id"],))
    dup = research.execute("SELECT * FROM games WHERE season = 2016 AND season_type = 'regular' LIMIT 1").fetchone()
    research.execute("INSERT INTO games SELECT 'ncaab:dup', season, season_type, tipoff_at, home_team_id, away_team_id, neutral_site, "
                     "status, home_score, away_score, first_seen_at, result_first_seen_at, result_available_at, "
                     "result_availability_basis, last_raw_id, updated_at, last_seen_at, last_seen_fetch_id FROM games WHERE game_id = ?",
                     (dup["game_id"],))
    research.execute("INSERT INTO ncaab_game_context SELECT 'ncaab:dup', season_type_id, tournament_note, conference_competition, "
                     "home_conference_id, away_conference_id, both_d1, ot_periods, source_status, updated_at, last_raw_id "
                     "FROM ncaab_game_context WHERE game_id = ?", (dup["game_id"],))
    research.commit()
    bad = ncaab_cards.validate_data(research)
    assert bad["orientation"]["stats_points_mismatch"] >= 1 and bad["fixtures"]["duplicate_games"] >= 1
    assert not bad["ok"] and bad["issues"]


def test_candidates_are_fit_per_season_and_fall_back_without_history(research):
    recs = ncaab_cards.research_walk(research, (2013, 2014), "ncaab-cand-hetero-v1")
    inc = {(r["game_id"], r["market"]): r for r in ncaab_cards.research_walk(research, (2013, 2014), "ncaab-incumbent")}
    first = [r for r in recs if r["season"] == 2013 and r["market"] in ("spread", "total")]
    assert first and all(r["fit_through"] is None for r in first)
    assert all(r["probs"] == inc[(r["game_id"], r["market"])]["probs"] for r in first)     # no history: the incumbent's numbers


def test_forecast_upcoming_has_no_outcome_and_ignores_later_results(research):
    add(research, "9001", "2027-01-01T20:00:00Z", "10", "11", status="scheduled", season=2027)
    now = "2026-12-31T12:00:00Z"
    sup.add_quote(research, "ncaab:9001", "total", 141.5, -110, -110, "2026-12-31T11:00:00Z")
    sup.add_quote(research, "ncaab:9001", "total", 150.5, -110, -110, "2026-12-31T13:00:00Z")      # after `now`
    recs = ncaab_cards.research_forecast_upcoming(research, now, "ncaab-cand-blend-v1")
    assert recs and {r["game_id"] for r in recs} == {"ncaab:9001"}
    assert all(r["outcome"] is None and r["forecast_cutoff"] == now for r in recs)
    t = [r for r in recs if r["market"] == "total"][0]
    assert t["line"] == 141.5 and t["quote"]["line"] == 141.5 and t["line_source"] == "bookmaker"
