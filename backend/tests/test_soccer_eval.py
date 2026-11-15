"""Soccer 1X2 evaluation guards (app/soccer_eval.py, protocol app/soccer_protocol.json). Written before the module.

Synthetic data only. What these tests pin down: reserved seasons are refused without a valid seal and every sealed read is
logged before anything is read; the protocol file is pinned by digest; a forecast never sees its own match, a same-day
result, a later correction, an unfinished/cancelled/awarded/extra-time/deciding match; frequencies, the Elo draw model and
the calibrator are fitted only on their preregistered seasons; the model-choice and calibration-acceptance rules; league
first seasons are never scored; probabilities sum to one; the one-shot runner refuses a second run."""
import json
import math

import pytest

from app import db, soccer_eval as ev, soccer_history as h
from app.backtest import LeakageError

SHA = "c" * 40


def mt(mid, comp, season, day, home, away, label=0, stage="league"):
    return ev.Match(mid, comp, season, day, home, away, stage, label)


def rev(mid, day, hg, ag, usable=True, available_at=None, revision=1):
    return ev.Revision(mid, revision, available_at or h.assumed_available_at(day), usable, hg, ag)


def dataset(matches, revisions, members=None, through=2022):
    if members is None:
        members = {}
        for m in matches:
            if m.competition in ev.LEAGUES:
                members.setdefault((m.competition, m.season), set()).update({m.home, m.away})
    return ev.Dataset(matches, revisions, {k: frozenset(v) for k, v in members.items()}, through)


class Spy:
    """Records every observed revision and every forecast request; predicts uniform."""
    name = "spy"

    def __init__(self):
        self.seen, self.asked = [], []

    def observe(self, r):
        self.seen.append(r)

    def predict(self, matches, cutoff_at):
        self.asked.append((cutoff_at, [m.mid for m in matches], [r.mid for r in self.seen]))
        return {m.mid: (1 / 3, 1 / 3, 1 / 3) for m in matches}


# --- Protocol and reserved seasons -----------------------------------------------------------------------------------
def test_protocol_is_pinned_by_digest(tmp_path, monkeypatch):
    p = ev.load_protocol()
    assert p["periods"]["last_open_season"] == 2022 and p["periods"]["reserved_test"] == [2023, 2024, 2025]
    changed = tmp_path / "p.json"
    text = ev.PROTOCOL_PATH.read_text()
    assert '"version": 2' in text
    changed.write_text(text.replace('"version": 2', '"version": 3'))
    monkeypatch.setattr(ev, "PROTOCOL_PATH", changed)
    with pytest.raises(ev.ProtocolChanged):
        ev.load_protocol()


def _store(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    c = db.connect(sport="soccer")
    db.migrate(c)
    db.claim_store(c, "soccer")
    c.commit()
    body = {}
    for season in (2021, 2023):
        body[f"{season}-{(season + 1) % 100:02d}/en.1.json"] = json.dumps({"name": "x", "matches": [
            {"round": "Matchday 1", "date": f"{season}-08-14", "time": "15:00", "team1": "Arsenal FC", "team2": "Burnley FC",
             "score": {"ft": [2, 1]}}]}).encode()
    from tests.test_soccer_history import FakeHub
    hub = FakeHub({SHA: body})
    h.download(c, pins={"football.json": SHA}, fetch=hub, sleep=0, only={"football.json"},
               now=iter(["2026-11-08T10:00:00Z"] * 3).__next__)
    h.replay(c)
    return c


def test_reserved_seasons_are_refused_without_a_valid_seal_and_logged_with_one(tmp_path, monkeypatch):
    c = _store(tmp_path, monkeypatch)
    log = tmp_path / "access.jsonl"
    ds = ev.load(c, through=2022)
    assert {m.season for m in ds.matches} == {2021}
    assert not log.exists()
    with pytest.raises(ev.TestSeasonsLocked):
        ev.load(c, through=2023)
    sealed = ev.seal_candidate({"model": "elo", "config": {}})
    with pytest.raises(ev.TestSeasonsLocked):
        ev.load(c, through=2023, sealed={**sealed, "model": "poisson"}, access_log=log)        # tampered
    with pytest.raises(ev.TestSeasonsLocked):
        ev.load(c, through=2023, sealed=sealed)                                                 # no log given
    assert not log.exists()
    ds = ev.load(c, through=2023, sealed=sealed, access_log=log, purpose="test")
    assert {m.season for m in ds.matches} == {2021, 2023}
    entry = json.loads(log.read_text().splitlines()[0])
    assert entry["through"] == 2023 and entry["candidate_sha256"] == sealed["sha256"] and entry["purpose"] == "test"


def test_loader_takes_inputs_only_from_usable_revisions(tmp_path, monkeypatch):
    c = _store(tmp_path, monkeypatch)
    ds = ev.load(c, through=2022)
    (r,) = ds.revisions
    assert (r.hg, r.ag, r.usable, r.available_at) == (2, 1, True, "2021-08-15T06:00:00Z")
    assert ds.matches[0].label == 0 and ds.members[("eng.1", 2021)] == {"ENG:Arsenal FC", "ENG:Burnley FC"}


# --- Point in time ---------------------------------------------------------------------------------------------------
def test_cutoff_is_six_utc_on_the_match_date():
    assert ev.cutoff_for("2021-08-14") == "2021-08-14T06:00:00Z"


def test_a_forecast_never_sees_its_own_or_a_same_day_result():
    ms = [mt("a", "eng.1", 2020, "2020-09-12", "A", "B"), mt("b", "eng.1", 2020, "2020-09-12", "C", "D"),
          mt("c", "eng.1", 2020, "2020-09-13", "A", "C")]
    rs = [rev("a", "2020-09-12", 1, 0), rev("b", "2020-09-12", 0, 0), rev("c", "2020-09-13", 2, 2)]
    spy = Spy()
    ev.walk(spy, dataset(ms, rs), seasons=[2020])
    (c1, asked1, seen1), (c2, asked2, seen2) = spy.asked
    assert (c1, sorted(asked1), seen1) == ("2020-09-12T06:00:00Z", ["a", "b"], [])
    assert (c2, asked2, sorted(seen2)) == ("2020-09-13T06:00:00Z", ["c"], ["a", "b"])


def test_a_correction_available_after_the_cutoff_is_not_used():
    ms = [mt("a", "eng.1", 2020, "2020-09-12", "A", "B"), mt("c", "eng.1", 2020, "2020-09-20", "A", "B")]
    rs = [rev("a", "2020-09-12", 1, 0), rev("a", "2020-09-12", 0, 1, available_at="2020-09-25T09:00:00Z", revision=2),
          rev("c", "2020-09-20", 2, 2)]
    spy = Spy()
    ev.walk(spy, dataset(ms, rs), seasons=[2020])
    assert [(r.mid, r.revision) for r in spy.seen if r.available_at <= "2020-09-20T06:00:00Z"] == [("a", 1)]
    assert spy.asked[-1][2] == ["a"]                                   # only revision 1 had been observed


def test_unusable_revisions_never_reach_a_model():
    ms = [mt("a", "eng.1", 2020, "2020-09-12", "A", "B"), mt("z", "eng.1", 2020, "2020-09-30", "A", "B")]
    rs = [rev("a", "2020-09-12", 1, 0, usable=False), rev("b", "2020-09-13", 3, 0, usable=False)]
    spy = Spy()
    ev.walk(spy, dataset(ms, rs), seasons=[2020])
    assert spy.seen == []


def test_the_engine_refuses_a_model_fed_a_future_revision():
    class Cheat(Spy):
        pass
    ms = [mt("a", "eng.1", 2020, "2020-09-12", "A", "B")]
    rs = [rev("a", "2020-09-12", 1, 0)]
    with pytest.raises(LeakageError):
        ev.feed(Cheat(), rs, cutoff_at="2020-09-12T06:00:00Z", start=0)


def test_league_first_seasons_and_unscored_seasons_are_not_scored():
    ms = [mt("a", "ita.1", 2013, "2013-08-24", "A", "B"), mt("b", "ita.1", 2014, "2014-08-30", "A", "B"),
          mt("c", "eng.1", 2012, "2012-08-18", "C", "D"), mt("d", "uefa.europa", 2020, "2020-10-22", "A", "C", stage="group")]
    ds = dataset(ms, [])
    assert [m.mid for m in ev.scored(ds, [2012, 2013, 2014, 2020])] == ["b", "d"]


# --- Fits only on their preregistered seasons ---------------------------------------------------------------------
def test_frequencies_use_only_2010_to_2019_and_europa_uses_champions():
    ms = [mt("a", "eng.1", 2019, "2019-08-10", "A", "B", label=0), mt("b", "eng.1", 2019, "2019-08-11", "C", "D", label=1),
          mt("v", "eng.1", 2020, "2020-09-12", "A", "B", label=2),
          mt("u", "uefa.champions", 2019, "2019-09-17", "A", "E", label=2, stage="group")]
    f = ev.fit_frequencies(dataset(ms, []))
    assert f["eng.1"] == pytest.approx((0.5, 0.5, 0.0)) and f["uefa.europa"] == f["uefa.champions"] == (0.0, 0.0, 1.0)


def test_draw_model_and_calibrator_refuse_rows_outside_their_seasons():
    row = lambda season: {"season": season, "competition": "eng.1", "x": 0.1, "label": 0,                 # noqa: E731
                          "p": (0.5, 0.3, 0.2), "cutoff_at": f"{season}-09-01T06:00:00Z"}
    with pytest.raises(LeakageError):
        ev.fit_draw_model([row(2017), row(2018)])
    with pytest.raises(LeakageError):
        ev.fit_calibration([row(2019)] * 1000 + [row(2020)])
    with pytest.raises(ValueError, match="1000"):
        ev.fit_calibration([row(2019)] * 999)


def test_draw_model_gives_three_probabilities_summing_to_one():
    rows = [{"season": 2015, "competition": comp, "x": x / 10, "label": lab, "cutoff_at": "2015-09-01T06:00:00Z"}
            for comp in ("eng.1", "uefa.champions") for x in range(-5, 6)
            for lab in [0] * (6 + x) + [1] * 4 + [2] * (6 - x)]
    dm = ev.fit_draw_model(rows)
    for x in (-1.0, 0.0, 1.0):
        p = ev.draw_probs(dm, "eng.1", x)
        assert sum(p) == pytest.approx(1) and all(0 < q < 1 for q in p)
    assert ev.draw_probs(dm, "uefa.europa", 0.3) == ev.draw_probs(dm, "uefa.champions", 0.3)
    assert ev.draw_probs(dm, "eng.1", 1.0)[0] > ev.draw_probs(dm, "eng.1", -1.0)[0]


# --- Models ----------------------------------------------------------------------------------------------------------
def test_elo_promoted_and_new_league_initialisation():
    cfg = ev.EloConfig(K=20, H=0, C=0.5, P=100, F=200, mov=False)
    m = ev.EloModel(cfg, members={("eng.1", 2019): frozenset({"A", "B"}), ("eng.1", 2020): frozenset({"A", "N"}),
                                  ("ita.1", 2020): frozenset({"I", "J"})}, draw_model=None)
    m.ratings.update({"A": 1600, "B": 1400, "I": 1550})
    m.touch("eng.1", 2020)
    assert m.ratings["A"] == pytest.approx(1500 + 0.5 * 100) and m.ratings["N"] == pytest.approx(1400)
    m.touch("ita.1", 2020)                                             # ita.1 not covered in 2019: everyone 1500
    assert m.ratings["I"] == m.ratings["J"] == 1500
    m.touch_club("X", "uefa.champions", 2020)
    assert m.ratings["X"] == 1300


def test_elo_update_and_goal_multiplier():
    assert ev.goal_multiplier(1) == 1 and ev.goal_multiplier(2) == 1.5 and ev.goal_multiplier(5) == 2.0
    cfg = ev.EloConfig(K=20, H=0, C=1.0, P=0, F=0, mov=False)
    m = ev.EloModel(cfg, members={("eng.1", 2020): frozenset({"A", "B"})}, draw_model=None)
    m.match_meta = {"a": ("eng.1", 2020, "A", "B")}
    m.observe(ev.Revision("a", 1, "2020-09-13T06:00:00Z", True, 1, 1))
    assert m.ratings["A"] == m.ratings["B"] == 1500                    # draw between equals
    m.observe(ev.Revision("a", 2, "2020-09-20T06:00:00Z", True, 3, 0))  # correction: Elo never re-applies a match
    assert m.ratings["A"] == 1500


def test_poisson_probabilities_sum_to_one_and_favour_the_stronger_side():
    p = ev.poisson_probs(2.0, 0.6)
    assert sum(p) == pytest.approx(1) and p[0] > p[2]
    assert ev.poisson_probs(1.2, 1.2)[0] == pytest.approx(ev.poisson_probs(1.2, 1.2)[2])


# --- Choice and calibration rules --------------------------------------------------------------------------------------
def _preds(losses, start="2020-09-07"):
    from datetime import date, timedelta
    d0 = date.fromisoformat(start)
    out = []
    for i, l in enumerate(losses):
        p = math.exp(-l)
        out.append({"mid": f"m{i}", "competition": "eng.1", "season": 2020 + i % 3, "label": 0,
                    "cutoff_at": f"{d0 + timedelta(days=i)}T06:00:00Z", "p": (p, (1 - p) / 2, (1 - p) / 2)})
    return out


def test_choice_keeps_the_simpler_model_unless_the_better_one_is_clearly_better():
    base = _preds([1.0] * 300)
    slightly = _preds([1.0 - (0.002 if i % 2 else -0.0019) for i in range(300)])
    clearly = _preds([0.9] * 300)
    assert ev.choose_model({"frequency": base, "elo": slightly, "poisson": base})["selected"] == "frequency"
    assert ev.choose_model({"frequency": base, "elo": clearly, "poisson": base})["selected"] == "elo"
    assert ev.choose_model({"frequency": base, "elo": clearly, "poisson": _preds([0.8] * 300)})["selected"] == "poisson"
    assert ev.choose_model({"frequency": base, "elo": base, "poisson": clearly})["selected"] == "poisson"


def test_calibration_is_kept_only_if_it_clearly_helps_without_worse_reliability():
    unc = _preds([1.0] * 300)
    assert ev.accept_calibration(unc, unc)["keep"] is False
    better = _preds([0.95] * 300)
    assert ev.accept_calibration(unc, better)["keep"] is True


# --- One-shot runner -----------------------------------------------------------------------------------------------
def test_one_shot_runner_refuses_bad_seal_second_run_and_existing_output(tmp_path, monkeypatch):
    c = _store(tmp_path, monkeypatch)
    log = tmp_path / "access.jsonl"
    with pytest.raises(ev.TestSeasonsLocked):
        ev.run_test(c, {"model": "frequency", "sha256": "bad"}, access_log=log, experiments_log=tmp_path / 'x.jsonl', out_dir=tmp_path / "out")
    out = tmp_path / "exists"
    out.mkdir()
    sealed = ev.seal_candidate({"model": "frequency", "config": {}, "frequencies": {}, "calibration": None,
                                "data_digest": "x", "protocol_sha256": ev.PROTOCOL_SHA256})
    with pytest.raises(ev.TestRunAlreadyDone):
        ev.run_test(c, sealed, access_log=log, experiments_log=tmp_path / 'x.jsonl', out_dir=out)
    log.write_text(json.dumps({"candidate_sha256": sealed["sha256"], "purpose": ev.RUN_START}) + "\n")
    with pytest.raises(ev.TestRunAlreadyDone):
        ev.run_test(c, sealed, access_log=log, experiments_log=tmp_path / 'x.jsonl', out_dir=tmp_path / "out2")


def test_a_match_whose_own_result_is_available_before_it_is_refused():
    # inconsistent data (a result available before its match date) must stop the walk, not leak into the forecast
    ms = [mt("a", "eng.1", 2020, "2020-09-12", "A", "B")]
    rs = [rev("a", "2020-09-12", 1, 0, available_at="2020-09-11T00:00:00Z")]
    with pytest.raises(LeakageError, match="own result"):
        ev.walk(Spy(), dataset(ms, rs), seasons=[2020])


# --- Review S3 (REQUEST CHANGES) follow-up: written before the fixes ------------------------------------------------
def test_a_model_never_receives_the_label_of_the_match_it_forecasts():
    class Peek(Spy):
        def predict(self, matches, cutoff_at):
            self.labels = [m.label for m in matches]
            return super().predict(matches, cutoff_at)
    spy = Peek()
    ev.walk(spy, dataset([mt("a", "eng.1", 2020, "2020-09-12", "A", "B", label=2)], []), seasons=[2020])
    assert spy.labels == [None]


def _league_world(seasons=(2018, 2019, 2020, 2021), clubs="ABCDEF", seed=1):
    import random
    rnd = random.Random(seed)
    ms, rs, members = [], [], {}
    for s in seasons:
        members[("eng.1", s)] = frozenset(clubs)
        day = date(s, 8, 10)
        k = 0
        for h_ in clubs:
            for a_ in clubs:
                if h_ == a_:
                    continue
                d = (day + timedelta(days=7 * (k // 3))).isoformat()
                hg, ag = rnd.randint(0, 3), rnd.randint(0, 2)
                mid = f"m{s}-{h_}{a_}"
                ms.append(mt(mid, "eng.1", s, d, h_, a_, label=ev.label_of(hg, ag)))
                rs.append(rev(mid, d, hg, ag))
                k += 1
    return ms, rs, members


from datetime import date, timedelta   # noqa: E402


def _models(members):
    dm = {"b": 1.0, "cuts": {"eng.1": (-0.9, 0.9)}, "n": 0, "converged": True}
    return [lambda: ev.EloModel(ev.EloConfig(20, 60, 0.8, 100, 100, True), members, dm),
            lambda: ev.PoissonModel(ev.PoissonConfig(365, 3, 0.1), members)]


def test_forecasts_do_not_change_when_anything_after_their_cutoff_changes():
    ms, rs, members = _league_world()
    base = dataset(ms, rs, members)
    later = [r for r in rs if r.mid.startswith("m2021")]
    flipped = [rev(r.mid, r.available_at[:10], r.ag, r.hg, available_at=r.available_at) if r in later else r for r in rs]
    corrected = rs + [rev("m2020-AB", "2020-08-10", 9, 0, available_at="2026-01-01T00:00:00Z", revision=2)]
    relegated = {**members, ("eng.1", 2021): frozenset("ABCDEG")}
    variants = [dataset(ms, flipped, members), dataset(ms, corrected, members), dataset(ms, rs, relegated),
                dataset([m for m in ms if m.season <= 2020], [r for r in rs if not r.mid.startswith("m2021")],
                        {k: v for k, v in members.items() if k[1] <= 2020})]
    for make in _models(members):
        want = ev.walk(make(), base, [2019, 2020])
        for v in variants:
            model = make()
            if hasattr(model, "members"):
                model.members = v.members
                if hasattr(model, "club_league"):
                    model.club_league = v.club_league
            assert ev.walk(model, v, [2019, 2020]) == want


def test_poisson_uses_a_correction_once_available_and_drops_a_withdrawn_match():
    m = ev.PoissonModel(ev.PoissonConfig(365, 3, 0.1), {})
    m.match_meta = {"a": ("eng.1", 2020, "A", "B", "2020-09-12")}
    m.observe(ev.Revision("a", 1, "2020-09-13T06:00:00Z", True, 1, 0))
    m.observe(ev.Revision("a", 2, "2020-09-20T06:00:00Z", True, 0, 4))
    assert m.snap["a"][4:6] == (0, 4)
    m.withdraw(ev.Revision("a", 3, "2020-09-25T06:00:00Z", False, None, None))
    assert "a" not in m.snap


def test_elo_regresses_clubs_outside_covered_leagues_to_their_pool_mean():
    cfg = ev.EloConfig(K=20, H=0, C=0.5, P=0, F=100, mov=False)
    m = ev.EloModel(cfg, members={("eng.1", 2021): frozenset({"A"})}, draw_model=None)
    m.touch_club("X", "uefa.champions", 2020)
    m.touch_club("Y", "uefa.champions", 2020)
    m.ratings["X"], m.ratings["Y"] = 1600.0, 1200.0
    m.touch_club("X", "uefa.champions", 2021)                       # pool mean of non-league clubs = 1400
    assert m.ratings["X"] == pytest.approx(1400 + 0.5 * 200)


def test_poisson_promoted_prior_uses_the_previous_fit_and_the_forecast_season():
    members = {("eng.1", 2019): frozenset({"A", "B"}), ("eng.1", 2020): frozenset({"A", "P"})}
    m = ev.PoissonModel(ev.PoissonConfig(365, 3, 0.25), members)
    m.params = {"att": {"A": 0.4, "B": -0.2}, "def": {"A": 0.3, "B": -0.1}, "mu": {}, "h": {}}
    centre = m.prior_centres({"P": 2020, "A": 2020}, m.params)
    assert centre["P"] == pytest.approx((0.4 - 0.25, 0.3 - 0.25)) and centre["A"] == (0.0, 0.0)
    assert m.prior_centres({"P": 2019}, m.params)["P"] == (0.0, 0.0)  # in July 2020 a 2019-20 match: not yet promoted


def test_configuration_ties_are_broken_in_the_preregistered_order():
    a, b = ev.EloConfig(20, 50, 1.0, 0, 0, False), ev.EloConfig(15, 75, 0.6, 200, 200, True)
    assert ev.select_config([(0.5, ev.elo_tiebreak(a), a), (0.5 + 5e-10, ev.elo_tiebreak(b), b)])[2] == b
    assert ev.select_config([(0.5, ev.elo_tiebreak(a), a), (0.5 + 2e-9, ev.elo_tiebreak(b), b)])[2] == a
    p, q = ev.PoissonConfig(365, 3, 0.1), ev.PoissonConfig(730, 3, 0.25)
    assert ev.select_config([(0.5, ev.poisson_tiebreak(p), p), (0.5, ev.poisson_tiebreak(q), q)])[2] == q


def test_calibration_is_refused_when_reliability_worsens(monkeypatch):
    unc, better = _preds([1.0] * 300), _preds([0.95] * 300)
    calls = iter([0.01, 0.01, 0.01, 0.02, 0.01, 0.01])               # uncalibrated home/draw/away, then calibrated
    monkeypatch.setattr(ev, "ece", lambda preds, k, bins=10: next(calls))
    v = ev.accept_calibration(unc, better)
    assert v["log_loss_better"] is True and v["reliability_not_worse"] is False and v["keep"] is False


def _seasons_preds(by_season):
    out = []
    for s, losses in by_season.items():
        for p in _preds(losses, start=f"{s}-09-07"):
            out.append({**p, "mid": f"{s}-{p['mid']}", "season": s})
    return out


def test_choice_needs_the_candidate_better_in_two_of_three_validation_seasons():
    base = _seasons_preds({2020: [1.0] * 100, 2021: [1.0] * 100, 2022: [1.0] * 100})
    one_season = _seasons_preds({2020: [0.5] * 100, 2021: [1.01] * 100, 2022: [1.01] * 100})
    two = _seasons_preds({2020: [0.9] * 100, 2021: [0.9] * 100, 2022: [1.01] * 100})
    assert ev.choose_model({"frequency": base, "elo": one_season, "poisson": base})["selected"] == "frequency"
    assert ev.choose_model({"frequency": base, "elo": two, "poisson": base})["selected"] == "elo"


def test_loader_marks_awarded_extra_time_deciding_and_dropped_rows_unusable(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    c = db.connect(sport="soccer")
    db.migrate(c)
    db.claim_store(c, "soccer")
    c.commit()
    from tests.test_soccer_history import FakeHub, CL
    hub = FakeHub({SHA: {"2023-24/cl.txt": CL.encode()}})
    h.download(c, pins={"champions-league": SHA}, fetch=hub, sleep=0, only={"champions-league"},
               now=iter(["2026-11-08T10:00:00Z"] * 3).__next__)
    h.replay(c)
    ds = ev.load(c, through=2023, sealed=ev.seal_candidate({}), access_log=tmp_path / "a.jsonl", purpose="unit test")
    usable = {ds.meta[r.mid][2:4]: r.usable for r in ds.revisions}
    assert usable[("ENG:Arsenal FC", "POR:FC Porto")] is False          # a.e.t./pens
    assert usable[("ESP:FC Barcelona", "ITA:SSC Napoli")] is False      # deciding
    assert usable[("POR:FC Porto", "ENG:Arsenal FC")] is True           # first leg
    assert {m.mid for m in ds.matches} == {r.mid for r in ds.revisions if r.usable}


def test_seasons_after_the_reserved_test_are_refused_even_with_a_seal(tmp_path, monkeypatch):
    c = _store(tmp_path, monkeypatch)
    with pytest.raises(ev.TestSeasonsLocked, match="prospective"):
        ev.load(c, through=2026, sealed=ev.seal_candidate({}), access_log=tmp_path / "a.jsonl")


def test_one_test_run_per_access_log_whatever_the_seal(tmp_path, monkeypatch):
    c = _store(tmp_path, monkeypatch)
    log, xlog = tmp_path / "access.jsonl", tmp_path / "exp.jsonl"
    log.write_text(json.dumps({"candidate_sha256": "something-else", "purpose": ev.RUN_START}) + "\n")
    sealed = ev.seal_candidate({"model": "frequency"})
    with pytest.raises(ev.TestRunAlreadyDone):
        ev.run_test(c, sealed, access_log=log, experiments_log=xlog, out_dir=tmp_path / "o")


def test_the_candidate_must_be_the_only_seal_the_development_log_recorded(tmp_path, monkeypatch):
    c = _store(tmp_path, monkeypatch)
    log, xlog = tmp_path / "access.jsonl", tmp_path / "exp.jsonl"
    sealed = ev.seal_candidate({"model": "frequency", "development_run": "r1"})
    with pytest.raises(ev.FrozenMismatch, match="start"):                      # empty log: no development run at all
        ev.run_test(c, sealed, access_log=log, experiments_log=xlog, out_dir=tmp_path / "o")
    xlog.write_text(json.dumps({"run": "r1", "stage": "start"}) + "\n" +
                    json.dumps({"run": "r1", "stage": "sealed", "candidate_sha256": sealed["sha256"]}) + "\n" +
                    json.dumps({"run": "r2", "stage": "sealed", "candidate_sha256": "other"}) + "\n")
    with pytest.raises(ev.FrozenMismatch, match="more than one"):
        ev.run_test(c, sealed, access_log=log, experiments_log=xlog, out_dir=tmp_path / "o")
    assert not log.exists()


@pytest.mark.parametrize("what", ["protocol_sha256", "code", "open_data_digest"])
def test_check_frozen_refuses_any_changed_input(tmp_path, monkeypatch, what):
    c = _store(tmp_path, monkeypatch)
    good = {"protocol_sha256": ev.PROTOCOL_SHA256, "code": ev.code_digest(), "open_data_digest": ev.open_data_digest(c)}
    ev.check_frozen(c, good)
    with pytest.raises(ev.FrozenMismatch):
        ev.check_frozen(c, {**good, what: "changed"})


def test_code_digest_covers_the_shared_metric_bootstrap_and_seal_code():
    assert {"backtest.py", "metrics.py", "soccer_eval.py", "soccer_protocol.json"} <= set(ev.code_digest())


def test_development_end_to_end_never_reads_a_reserved_season_and_logs_its_start(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    c = db.connect(sport="soccer")
    db.migrate(c)
    db.claim_store(c, "soccer")
    c.commit()
    import random
    rnd, files = random.Random(7), {}
    clubs = ["Arsenal FC", "Burnley FC", "Chelsea FC", "Everton FC"]
    for s in range(2010, 2025):
        ms = []
        for k, (hc, ac) in enumerate((x, y) for x in clubs for y in clubs if x != y):
            d = (date(s, 8, 14) + timedelta(days=7 * k)).isoformat()
            ms.append({"round": f"Matchday {k + 1}" if k else "Matchday 1", "date": d, "time": "15:00", "team1": hc,
                       "team2": ac, "score": {"ft": [rnd.randint(0, 3), rnd.randint(0, 3)]}})
        files[f"{s}-{(s + 1) % 100:02d}/en.1.json"] = json.dumps({"name": "x", "matches": ms}).encode()
    from tests.test_soccer_history import FakeHub
    h.download(c, pins={"football.json": SHA}, fetch=FakeHub({SHA: files}), sleep=0, only={"football.json"},
               now=iter(["2026-11-08T10:00:00Z"] * 40).__next__)
    h.replay(c)
    c.commit()
    monkeypatch.setitem(ev._P["dataset"]["source_pins"], "football.json", SHA)
    calls, real_load = [], ev.load
    monkeypatch.setattr(ev, "load", lambda conn, **kw: calls.append(kw) or real_load(conn, **kw))
    monkeypatch.setattr(ev, "ELO_GRID", {"K": [20], "H": [50], "C": [1.0], "P": [0], "F": [0], "mov": [False]})
    monkeypatch.setattr(ev, "POISSON_GRID", {"half_life_days": [365], "tau": [3], "D": [0.1]})
    xlog = tmp_path / "exp.jsonl"
    with pytest.raises(TypeError):
        ev.develop(c, out_dir=tmp_path / "out0")                   # the experiments log is mandatory
    res = ev.develop(c, out_dir=tmp_path / "out", experiments_log=xlog, progress=None)
    assert calls and all(kw["through"] <= ev.LAST_OPEN and not kw.get("sealed") for kw in calls)
    entries = [json.loads(x) for x in xlog.read_text().splitlines()]
    assert entries[0]["stage"] == "start" and entries[0]["code"] == ev.code_digest()
    assert entries[-1]["stage"] == "sealed" and entries[-1]["candidate_sha256"] == res["sealed"]["sha256"]
    seasons = {json.loads(x)["season"] for x in (tmp_path / "out" / "forecasts.jsonl").read_text().splitlines()}
    assert max(seasons) <= ev.LAST_OPEN and not (tmp_path / "research").exists()


# --- S3 re-review notes N1/N2 (fixed before the single development run) -----------------------------------------------
def test_eval_logs_live_in_one_folder_shared_by_every_data_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("SOCCER_EVAL_LOG_DIR", str(tmp_path / "logs"))
    for d in ("a", "b"):
        monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path / d))
        assert ev.access_log_path() == tmp_path / "logs" / "soccer_test_access.jsonl"
        assert ev.experiments_log_path() == tmp_path / "logs" / "soccer_experiments.jsonl"


def test_the_test_run_needs_exactly_one_development_start(tmp_path, monkeypatch):
    c = _store(tmp_path, monkeypatch)
    log, xlog = tmp_path / "access.jsonl", tmp_path / "exp.jsonl"
    sealed = ev.seal_candidate({"model": "frequency", "development_run": "r2"})
    xlog.write_text("\n".join(json.dumps(e) for e in (
        {"run": "r1", "stage": "start"}, {"run": "r1", "stage": "validation choice"},       # aborted before sealing
        {"run": "r2", "stage": "start"}, {"run": "r2", "stage": "sealed", "candidate_sha256": sealed["sha256"]})) + "\n")
    with pytest.raises(ev.FrozenMismatch, match="start"):
        ev.run_test(c, sealed, access_log=log, experiments_log=xlog, out_dir=tmp_path / "o")
    assert not log.exists()


def test_the_test_run_refuses_a_store_without_reserved_matches(tmp_path, monkeypatch):
    c = _store(tmp_path, monkeypatch)                     # holds 2021 and 2023 only: 2024 and 2025 have no matches
    with pytest.raises(ev.FrozenMismatch, match="no scored match"):
        ev.check_reserved_present(c)
