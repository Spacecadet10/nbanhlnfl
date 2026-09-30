"""Walk-forward backtester, baselines, Elo and leakage tests (synthetic data, no network)."""
import dataclasses
import json
import random
from datetime import datetime, timedelta, timezone

import pytest

from app import backtest, db, ingest
from app.backtest import Row, cutoff_for, run
from app.models.base import PregameGame, Result
from app.models.baselines import HomeRate
from app.models.elo import Elo, EloConfig

TEAMS = [f"espn:{i}" for i in range(1, 7)]


def ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_row(gid, tip: datetime, home, away, home_won, season=2022, neutral=False, stype="regular"):
    tip_s = ts(tip)
    game = PregameGame(gid, season, stype, tip_s, home, away, neutral)
    hs, as_ = (110, 100) if home_won else (100, 110)
    return Row(game, cutoff_for(tip_s), hs, as_, ts(tip + timedelta(hours=4)))


def synthetic_rows(seasons=range(2020, 2027), per_season=60, seed=7):
    rng = random.Random(seed)
    strength = {t: rng.gauss(0, 1) for t in TEAMS}
    rows = []
    for season in seasons:
        start = datetime(season - 1, 11, 1, 0, 30, tzinfo=timezone.utc)  # 19:30/20:30 ET the previous day
        for i in range(per_season):
            home, away = rng.sample(TEAMS, 2)
            p = 1 / (1 + pow(10, -(strength[home] - strength[away] + 0.3)))
            rows.append(make_row(f"g{season}-{i}", start + timedelta(days=i), home, away, rng.random() < p, season))
    return rows


def flip(row: Row) -> Row:
    return dataclasses.replace(row, home_score=row.away_score, away_score=row.home_score)


def preds_by_id(model, rows):
    return {p["game_id"]: p["p_home"] for p in run(model, rows)}


def test_cutoff_is_midnight_eastern_on_game_date():
    assert cutoff_for("2024-01-16T00:30:00Z") == "2024-01-15T05:00:00Z"   # 19:30 EST Jan 15
    assert cutoff_for("2024-07-01T23:00:00Z") == "2024-07-01T04:00:00Z"   # 19:00 EDT Jul 1
    assert cutoff_for("2024-03-10T17:00:00Z") == "2024-03-10T05:00:00Z"   # DST change day


def test_predictors_receive_no_outcome_fields():
    fields = {f.name for f in dataclasses.fields(PregameGame)}
    assert not fields & {"home_score", "away_score", "home_won", "result_available_at", "status"}


@pytest.mark.parametrize("model_factory", [HomeRate, lambda: Elo(EloConfig(mov=True))])
def test_current_game_label_does_not_change_its_prediction(model_factory):
    rows = synthetic_rows(seasons=[2022], per_season=40)
    base = preds_by_id(model_factory(), rows)
    for i in (0, 10, 39):
        changed = rows[:i] + [flip(rows[i])] + rows[i + 1:]
        assert preds_by_id(model_factory(), changed)[rows[i].game.game_id] == base[rows[i].game.game_id]


def test_same_day_earlier_game_is_not_used():
    day = datetime(2024, 1, 15, tzinfo=timezone.utc)
    early = make_row("early", day + timedelta(hours=18), "espn:1", "espn:2", True)   # 13:00 ET
    late = make_row("late", day + timedelta(hours=27), "espn:1", "espn:3", True)     # 22:00 ET same day
    assert early.cutoff_at == late.cutoff_at
    p1 = preds_by_id(Elo(), [early, late])["late"]
    p2 = preds_by_id(Elo(), [flip(early), late])["late"]
    assert p1 == p2 == 1 / (1 + 10 ** (-100 / 400))  # still the prior: no same-day information


def test_previous_night_result_available_after_midnight_is_excluded():
    d = datetime(2024, 1, 15, tzinfo=timezone.utc)
    afternoon = make_row("afternoon", d + timedelta(hours=18), "espn:1", "espn:2", True)   # available 17:00 ET
    late_night = make_row("late", d + timedelta(hours=27, minutes=30), "espn:3", "espn:4", True)  # avail 02:30 ET
    next_day = make_row("next", d + timedelta(days=1, hours=24), "espn:1", "espn:3", True)
    with_afternoon = preds_by_id(Elo(), [afternoon, late_night, next_day])["next"]
    flipped_late = preds_by_id(Elo(), [afternoon, flip(late_night), next_day])["next"]
    flipped_afternoon = preds_by_id(Elo(), [flip(afternoon), late_night, next_day])["next"]
    assert with_afternoon == flipped_late            # late-night result not yet available
    assert with_afternoon != flipped_afternoon       # afternoon result was available


@pytest.mark.parametrize("model_factory", [HomeRate, lambda: Elo(EloConfig(carry=0.5, mov=True))])
def test_future_rows_do_not_change_past_predictions(model_factory):
    rows = synthetic_rows()
    split = "2023-06-01T00:00:00Z"
    base = preds_by_id(model_factory(), rows)
    past = [r for r in rows if r.cutoff_at < split]
    altered = past + [flip(r) for r in rows if r.cutoff_at >= split]
    for variant in (past, altered):
        got = preds_by_id(model_factory(), variant)
        assert all(got[r.game.game_id] == base[r.game.game_id] for r in past)


def test_tuning_rejects_reserved_test_rows():
    rows = synthetic_rows(per_season=30)
    with pytest.raises(ValueError, match="non-validation seasons"):
        backtest.tune_elo(rows)
    allowed = [r for r in rows if r.game.season in backtest.LOADABLE["validation"]]
    assert backtest.tune_elo(allowed)[0] == backtest.tune_elo(list(allowed))[0]


def test_run_rejects_a_result_available_before_its_own_cutoff():
    r = make_row("x", datetime(2024, 1, 15, 18, tzinfo=timezone.utc), "espn:1", "espn:2", True)
    with pytest.raises(backtest.LeakageError):
        run(Elo(), [dataclasses.replace(r, result_available_at="2024-01-15T04:00:00Z")])


def test_home_rate_baseline():
    m = HomeRate()
    g = PregameGame("a", 2022, "regular", "2024-01-15T00:00:00Z", "espn:1", "espn:2", False)
    assert m.predict(g) == 0.5
    for won in (True, True, False):
        m.update(Result(g, 110 if won else 100, 100 if won else 110))
    m.update(Result(dataclasses.replace(g, neutral_site=True), 110, 100))  # neutral games are ignored
    assert m.predict(g) == pytest.approx(3 / 5)
    assert m.predict(dataclasses.replace(g, neutral_site=True)) == 0.5


def test_elo_updates_are_zero_sum_and_carry_toward_mean():
    m = Elo(EloConfig(k=20, home_advantage=100, carry=0.5))
    g = PregameGame("a", 2022, "regular", "t", "espn:1", "espn:2", False)
    p = m.predict(g)
    m.update(Result(g, 110, 100))
    assert m.ratings["espn:1"] - 1500 == pytest.approx(20 * (1 - p))
    assert m.ratings["espn:1"] + m.ratings["espn:2"] == pytest.approx(3000)
    before = m.ratings["espn:1"]
    m.predict(dataclasses.replace(g, season=2023))
    assert m.ratings["espn:1"] - 1500 == pytest.approx(0.5 * (before - 1500))
    neutral = dataclasses.replace(g, season=2023, neutral_site=True)
    assert m.predict(neutral) + m.predict(dataclasses.replace(neutral, home_team_id="espn:2",
                                                             away_team_id="espn:1")) == pytest.approx(1)


def test_elo_margin_multiplier_scales_bigger_wins():
    g = PregameGame("a", 2022, "regular", "t", "espn:1", "espn:2", False)
    close, blowout = Elo(EloConfig(mov=True)), Elo(EloConfig(mov=True))
    close.update(Result(g, 101, 100))
    blowout.update(Result(g, 130, 100))
    assert blowout.ratings["espn:1"] > close.ratings["espn:1"] > 1500


def test_paired_bootstrap_is_seeded_and_zero_for_identical_models():
    preds = run(Elo(), synthetic_rows(seasons=[2022], per_season=40))
    assert backtest.paired_bootstrap(preds, preds)["ci95"] == [0.0, 0.0]
    other = run(HomeRate(), synthetic_rows(seasons=[2022], per_season=40))
    assert backtest.paired_bootstrap(preds, other) == backtest.paired_bootstrap(preds, other)


def _db_with_games(tmp_path, monkeypatch, name="bt", seasons=range(2020, 2027), flip_seasons=()):
    """Loads synthetic finals through the real ingest path so load_rows filtering is exercised too."""
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path / name))
    conn = db.connect(tmp_path / name / "bt.sqlite")
    db.migrate(conn)
    events = []
    for r in synthetic_rows(per_season=12):
        if r.game.season not in seasons:
            continue
        if r.game.season in flip_seasons:
            r = flip(r)
        comps = [{"homeAway": side, "team": {"id": tid.split(":")[1], "abbreviation": tid, "displayName": tid},
                  "score": str(score)} for side, tid, score in
                 (("home", r.game.home_team_id, r.home_score), ("away", r.game.away_team_id, r.away_score))]
        events.append({"id": r.game.game_id, "date": r.game.tipoff_at, "season": {"year": r.game.season, "type": 2},
                       "competitions": [{"neutralSite": False, "status": {"type": {"name": "STATUS_FINAL"}},
                                         "competitors": comps}]})
    events.append({"id": "pre", "date": "2021-10-01T23:00:00Z", "season": {"year": 2022, "type": 1},
                   "competitions": [{"neutralSite": False, "status": {"type": {"name": "STATUS_FINAL"}},
                                     "competitors": [{"homeAway": "home", "team": {"id": "1"}, "score": "1"},
                                                     {"homeAway": "away", "team": {"id": "2"}, "score": "2"}]}]})
    body = json.dumps({"events": events}).encode()
    ingest.ingest_month(conn, "202601", fetch=lambda _m: body, retrieved_at="2026-09-30T00:00:00Z")
    return conn


def test_load_rows_filters_and_uses_assumed_availability(tmp_path, monkeypatch):
    conn = _db_with_games(tmp_path, monkeypatch)
    rows = backtest.load_rows(conn, "test")
    assert len(rows) == 7 * 12 and all(r.game.season_type == "regular" for r in rows)
    assert all(r.result_available_at > r.cutoff_at for r in rows)
    assert backtest.dataset_version(rows) == backtest.dataset_version(list(reversed(rows)))


def test_evaluate_writes_report_logs_test_access_and_records_idempotently(tmp_path, monkeypatch):
    conn = _db_with_games(tmp_path, monkeypatch)
    val = backtest.evaluate(conn, "validation", record_predictions=True, out_dir=tmp_path / "val", log=lambda _: None)
    assert (tmp_path / "val" / "report.json").exists() and (tmp_path / "val" / "predictions.csv").exists()
    assert val["models"]["elo"]["n"] == 36 and len(val["elo_tuning"]["trials"]) == len(backtest.elo_grid()) == 150
    assert val["ledger_rows_created"] == 6 * 36  # constant, home_rate, elo, elo_platt, logistic, logistic_platt
    again = backtest.evaluate(conn, "validation", record_predictions=True, out_dir=tmp_path / "v2", log=lambda _: None)
    assert again["ledger_rows_created"] == 0
    assert conn.execute("SELECT COUNT(*) FROM predictions WHERE run_type='backtest'").fetchone()[0] == 216

    access_log = tmp_path / "bt" / "eval" / "test_access.jsonl"
    assert not access_log.exists()
    with pytest.raises(backtest.FrozenDecisionError):
        backtest.evaluate(conn, "test", decision_path=tmp_path / "missing.json", log=lambda _: None)
    with pytest.raises(backtest.FrozenDecisionError):  # configuration may only come from the frozen decision
        backtest.evaluate(conn, "test", EloConfig(), decision_path=tmp_path / "missing.json", log=lambda _: None)
    assert not access_log.exists()
    decision_path = freeze(tmp_path, val)
    test = backtest.evaluate(conn, "test", decision_path=decision_path, out_dir=tmp_path / "test", log=lambda _: None)
    entries = [json.loads(line) for line in access_log.read_text().splitlines()]
    assert len(entries) == 1 and entries[0]["elo_config"] == val["elo_tuning"]["selected"]
    assert test["primary_model"]["name"] == "elo" and test["primary_model"]["config"] == val["elo_tuning"]["selected"]
    assert "candidate_selection" not in test and "elo_tuning" not in test


def freeze(tmp_path, validation_report, reseal=True, **overrides):
    decision = backtest.decision_from_validation_report(validation_report, "synthetic")
    decision.update(overrides)
    if reseal:
        decision = backtest.seal(decision)
    path = tmp_path / f"decision-{len(list(tmp_path.glob('decision-*')))}.json"
    path.write_text(json.dumps(decision))
    return path


def test_validation_never_loads_reserved_test_rows(tmp_path, monkeypatch):
    """Isolation at the data-loading boundary: validation SQL returns no test-season rows, and the whole validation
    report (fingerprint, predictions, tuning) is identical whether test seasons are present, flipped, or absent."""
    test_seasons = set(backtest.PERIODS["test"])
    full = _db_with_games(tmp_path, monkeypatch, "full")
    assert {r.game.season for r in backtest.load_rows(full, "test")} >= test_seasons
    assert not {r.game.season for r in backtest.load_rows(full, "validation")} & test_seasons

    statements = []
    full.set_trace_callback(statements.append)
    backtest.load_rows(full, "validation")
    full.set_trace_callback(None)
    assert any("season IN" in s and "2024" in s and "2025" not in s for s in statements)

    def report(conn, name):
        r = backtest.evaluate(conn, "validation", out_dir=tmp_path / name / "out", log=lambda _: None)
        return {k: r[k] for k in ("dataset_version", "models", "elo_tuning", "elo_vs_home_rate_brier")}

    variants = {
        "full": full,
        "flipped": _db_with_games(tmp_path, monkeypatch, "flipped", flip_seasons=test_seasons),
        "absent": _db_with_games(tmp_path, monkeypatch, "absent", seasons=range(2020, 2025)),
    }
    reports = {name: report(conn, name) for name, conn in variants.items()}
    assert reports["full"] == reports["flipped"] == reports["absent"]
    preds = (tmp_path / "full" / "out" / "predictions.csv").read_text()
    assert ",2025," not in preds and ",2026," not in preds


# --- Logistic regression and calibration candidates ------------------------------------------------------------
from app.models.calibration import PlattCalibrated, logit  # noqa: E402
from app.models.logistic import LogisticElo, Standardizer, TeamFeatures, fit_logistic  # noqa: E402

FROZEN = EloConfig(k=15, home_advantage=50, carry=0.75, mov=True)
CANDIDATE_FACTORIES = [lambda: LogisticElo(FROZEN), lambda: PlattCalibrated(Elo(FROZEN)),
                       lambda: PlattCalibrated(LogisticElo(FROZEN))]


def rows_until(rows, season):
    """Rows before `season` plus that season's first game: running them stops right after its first predict."""
    first = min((r for r in rows if r.game.season == season), key=lambda r: r.cutoff_at)
    return [r for r in rows if r.game.season < season] + [first]


@pytest.mark.parametrize("factory", CANDIDATE_FACTORIES)
def test_candidates_future_rows_do_not_change_past_predictions(factory):
    rows = synthetic_rows()
    split = "2023-02-01T00:00:00Z"  # mid-season: exercises both in-season state and the season-start refits
    base = preds_by_id(factory(), rows)
    past = [r for r in rows if r.cutoff_at < split]
    altered = past + [flip(r) for r in rows if r.cutoff_at >= split]
    for variant in (past, altered):
        got = preds_by_id(factory(), variant)
        assert all(got[r.game.game_id] == base[r.game.game_id] for r in past)


@pytest.mark.parametrize("factory", CANDIDATE_FACTORIES)
def test_candidates_current_game_label_does_not_change_its_prediction(factory):
    rows = synthetic_rows()
    base = preds_by_id(factory(), rows)
    for i in (5, len(rows) // 2, len(rows) - 1):  # includes fitted seasons
        changed = rows[:i] + [flip(rows[i])] + rows[i + 1:]
        assert preds_by_id(factory(), changed)[rows[i].game.game_id] == base[rows[i].game.game_id]


def test_logistic_preprocessing_and_fit_use_only_earlier_seasons():
    rows = synthetic_rows()
    m = LogisticElo(FROZEN)
    run(m, rows_until(rows, 2023))
    train = [f for s, f, _, _ in m.examples if s < 2023]
    assert len(train) == len([r for r in rows if r.game.season < 2023]) and all(e[0] < 2023 for e in m.examples)
    assert m.scaler == Standardizer.fit(train)
    assert m.weights == fit_logistic([m.scaler.vector(f) for f in train],
                                     [y for s, _, y, _ in m.examples if s < 2023], m.l2)
    assert m.first_fit_season == 2021  # 2019-20 is fallback-only warm-up


def test_logistic_warmup_falls_back_to_elo_before_first_fit():
    rows = synthetic_rows(seasons=[2020, 2021], per_season=20)
    lg, elo = preds_by_id(LogisticElo(FROZEN), rows), preds_by_id(Elo(FROZEN), rows)
    assert all(lg[r.game.game_id] == elo[r.game.game_id] for r in rows if r.game.season == 2020)
    assert any(lg[r.game.game_id] != elo[r.game.game_id] for r in rows if r.game.season == 2021)


def test_calibration_fits_only_on_earlier_out_of_sample_predictions():
    rows = synthetic_rows()
    uncalibrated = preds_by_id(LogisticElo(FROZEN), rows)
    cal = PlattCalibrated(LogisticElo(FROZEN))
    run(cal, rows_until(rows, 2023))
    by_game = {r.game.game_id: r for r in rows}
    used = [(s, p, y) for s, p, y in cal.history if 2021 <= s < 2023]
    assert {s for s, _, _ in used} == {2021, 2022}  # 2019-20 fallback predictions are excluded
    b, a = fit_logistic([[logit(p)] for _, p, _ in used], [y for _, _, y in used], cal.l2)
    assert cal.params == (a, b)
    # The stored base probabilities are exactly the logistic model's walk-forward (out-of-sample) predictions.
    history_ids = [gid for gid, r in by_game.items() if 2021 <= r.game.season < 2023]
    assert sorted(p for _, p, _ in used) == sorted(uncalibrated[g] for g in history_ids)


def test_rest_feature_uses_schedule_not_results():
    tf = TeamFeatures(FROZEN)
    d = datetime(2024, 1, 15, 0, 30, tzinfo=timezone.utc)
    g1 = make_row("a", d, "espn:1", "espn:2", True).game
    g2 = make_row("b", d + timedelta(days=1), "espn:1", "espn:3", True).game
    first = tf.compute(g1)
    second = tf.compute(g2)  # g1's result was never delivered
    assert (first["rest_diff"], first["b2b_home"]) == (0.0, 0.0)
    assert second["b2b_home"] == 1.0 and second["rest_diff"] == pytest.approx(1 - 4)
    assert second["elo_diff"] == 0.0 and second["mov10_diff"] == 0.0


def test_selection_keeps_elo_unless_candidate_is_clearly_better():
    rows = synthetic_rows(seasons=[2022], per_season=60)
    elo = run(Elo(FROZEN), rows)
    same = [dict(p) for p in elo]
    perfect = [dict(p, p_home=0.99 if p["home_won"] else 0.01) for p in elo]
    worse = [dict(p, p_home=0.5) for p in elo]
    out = backtest.select_candidate({"elo": elo, "elo_platt": same, "logistic": worse, "logistic_platt": perfect})
    assert out["selected"] == "logistic_platt" and not out["comparisons"]["elo_platt"]["eligible"]
    out = backtest.select_candidate({"elo": elo, "elo_platt": same, "logistic": worse, "logistic_platt": same})
    assert out["selected"] == "elo"


def test_training_features_are_pregame_snapshots_not_post_result():
    """A game's training example must hold the features computed before tipoff; flipping its own result may change
    only the label, never its features (catches recomputing features after the update)."""
    rows = synthetic_rows()
    target = next(r for r in rows if r.game.season == 2022)
    examples = []
    for variant in (rows, [flip(r) if r is target else r for r in rows]):
        m = LogisticElo(FROZEN)
        run(m, variant)
        examples.append(next(e for e in m.examples if e[3] == target.game.game_id))
    assert examples[0][1] == examples[1][1] and examples[0][2] != examples[1][2]


# --- Frozen validation decision: the test path never reselects ------------------------------------------------

def _validated_db(tmp_path, monkeypatch):
    conn = _db_with_games(tmp_path, monkeypatch)
    val = backtest.evaluate(conn, "validation", out_dir=tmp_path / "val", log=lambda _: None)
    return conn, val


def test_test_evaluation_never_calls_selection_or_tuning(tmp_path, monkeypatch):
    conn, val = _validated_db(tmp_path, monkeypatch)
    decision_path = freeze(tmp_path, val)

    def forbidden(*_a, **_k):
        raise AssertionError("selection/tuning must not run during test evaluation")
    monkeypatch.setattr(backtest, "select_candidate", forbidden)
    monkeypatch.setattr(backtest, "tune_elo", forbidden)
    report = backtest.evaluate(conn, "test", decision_path=decision_path, out_dir=tmp_path / "t", log=lambda _: None)
    assert report["primary_model"]["name"] == "elo"
    assert "Descriptive only" in report["descriptive_comparisons"]["note"]
    assert not any(k in json.dumps(report["descriptive_comparisons"]) for k in ("eligible", "selected"))


def test_primary_stays_elo_even_when_another_model_scores_better_on_test(tmp_path, monkeypatch):
    conn, val = _validated_db(tmp_path, monkeypatch)
    decision_path = freeze(tmp_path, val)
    outcomes = {r.game.game_id: r.home_score > r.away_score for r in backtest.load_rows(conn, "test")}

    class Oracle:  # test-only model that "knows" outcomes, to make a non-primary model clearly better
        name = "logistic"
        def predict(self, g): return 0.99 if outcomes[g.game_id] else 0.01
        def update(self, r): pass
        def config(self): return {"oracle": True}

    original = backtest.candidate_models
    monkeypatch.setattr(backtest, "candidate_models",
                        lambda cfg: [m if m.name != "logistic" else Oracle() for m in original(cfg)])
    report = backtest.evaluate(conn, "test", decision_path=decision_path, out_dir=tmp_path / "t", log=lambda _: None)
    oracle = report["descriptive_comparisons"]["brier_diff_vs_primary"]["logistic"]
    assert oracle["ci95"][1] < 0  # clearly better on test...
    assert report["primary_model"]["name"] == "elo"  # ...yet the primary model is unchanged
    assert report["primary_model"]["config"] == val["elo_tuning"]["selected"]


@pytest.mark.parametrize("override,reseal", [
    ({"primary_model": "logistic"}, True),
    ({"validation_dataset_version": "0000000000000000"}, True),
    ({"selected_on": "test"}, True),
    ({"validation_seasons": [2022, 2023]}, True),
    ({"elo_config": {"k": 15}}, True),                     # partial config would silently use defaults
    ({"elo_config": {"k": 30, "home_advantage": 50, "carry": 0.75, "mov": True}}, False),  # edited, not resealed
])
def test_bad_frozen_decision_is_rejected_before_any_test_row_is_read(tmp_path, monkeypatch, override, reseal):
    conn, val = _validated_db(tmp_path, monkeypatch)
    decision_path = freeze(tmp_path, val, reseal=reseal, **override)
    loaded = []
    real_load = backtest.load_rows
    monkeypatch.setattr(backtest, "load_rows", lambda c, period: loaded.append(period) or real_load(c, period))
    with pytest.raises(backtest.FrozenDecisionError):
        backtest.evaluate(conn, "test", decision_path=decision_path, log=lambda _: None)
    assert "test" not in loaded
    assert not (tmp_path / "bt" / "eval" / "test_access.jsonl").exists()


def test_access_is_logged_before_test_rows_are_loaded(tmp_path, monkeypatch):
    conn, val = _validated_db(tmp_path, monkeypatch)
    decision_path = freeze(tmp_path, val)
    events = []
    real_load, real_log = backtest.load_rows, backtest.log_test_access
    monkeypatch.setattr(backtest, "load_rows", lambda c, period: events.append(("load", period)) or real_load(c, period))
    monkeypatch.setattr(backtest, "log_test_access", lambda e, path=None: events.append(("log", None)) or real_log(e, path))
    backtest.evaluate(conn, "test", decision_path=decision_path, out_dir=tmp_path / "t", log=lambda _: None)
    assert events == [("load", "validation"), ("log", None), ("load", "test")]


def test_committed_frozen_decision_is_the_validation_choice():
    d = json.loads(backtest.FROZEN_DECISION_PATH.read_text())
    assert d["primary_model"] == "elo" and d["selected_on"] == "validation"
    assert d["elo_config"] == {"k": 15, "home_advantage": 50, "carry": 0.75, "mov": True}
    assert d["validation_dataset_version"] == "08fe2552c1aa4579"
    assert d["sha256"] == backtest.seal(d)["sha256"]
