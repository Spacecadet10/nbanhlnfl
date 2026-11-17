"""NCAAB evaluation stages on a synthetic multi-season store: development tuning reads only warm-up and development seasons,
validation runs once and seals the decision, the test period runs once and every access is logged."""
import json
import random
from datetime import datetime, timedelta, timezone

import pytest

import ncaab_support as sup
from test_ncaab_eval import add
from app import ncaab_eval
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
