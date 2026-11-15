"""Guards of the preregistered goal/corner evaluation (app/soccer_markets_eval.py). SYNTHETIC history (allowed for guard
tests by the preregistered-evaluation workflow): seasons of an 8-club league plus a few UEFA matches, written in the real
ESPN scoreboard format (a real 2026-11-08 Premier League event is the template) and imported through soccer.ingest_month,
so every row passes the real parser and availability rules. No result here is a real-world claim."""
import copy
import json
import math
import random
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from app import backtest, db, soccer, soccer_markets as sm, soccer_markets_eval as ev
from app.backtest import LeakageError

REAL = Path(__file__).parent / "fixtures" / "soccer_markets_real"
TEMPLATE = json.loads((REAL / "eng1_20261108_finals.json").read_bytes())
RULES = {"stage_rules": {"uefa_group_stages": ["group-stage", "league-phase"]}}


def make_event(eid, kickoff, season, stype, slug, home, away, hs=None, as_=None, hc=None, ac=None, status="STATUS_FULL_TIME"):
    e = copy.deepcopy(TEMPLATE["events"][0])
    e["id"], e["date"] = str(eid), kickoff
    e["season"] = {"year": season, "type": stype, "slug": slug}
    comp = e["competitions"][0]
    comp["id"] = str(eid)
    comp["status"]["type"].update(name=status, state="post" if status != "STATUS_SCHEDULED" else "pre",
                                   completed=status != "STATUS_SCHEDULED", id="28" if status == "STATUS_FULL_TIME" else "1")
    comp["status"]["period"], comp["status"]["clock"], comp["status"]["displayClock"] = 2, 5400.0, "90'+4'"
    comp["details"] = []
    comp.pop("series", None)
    comp.pop("leg", None)
    for side, team, goals, corners in (("home", home, hs, hc), ("away", away, as_, ac)):
        c = next(x for x in comp["competitors"] if x["homeAway"] == side)
        c["team"] = {**c["team"], "id": team, "abbreviation": f"T{team}", "displayName": f"Team {team}", "name": f"Team {team}"}
        c["score"] = str(goals) if goals is not None else "0"
        c["statistics"] = [] if corners is None else [{"name": "wonCorners", "displayValue": str(corners)}]
    return e


def payload(competition, events):
    return json.dumps({"leagues": [{"slug": competition, "season": {"type": {"type": 1, "name": "x"}}}],
                       "events": events}).encode()


STRENGTH = {str(100 + i): 0.35 * (3.5 - i) for i in range(8)}


def synthetic_league(c, seasons=range(2010, 2026), seed=7, corners_missing_season=None):
    rng = np.random.default_rng(seed)
    teams = list(STRENGTH)
    eid = 1000
    for season in seasons:
        by_month = {}
        pairs = [(h, a) for h in teams for a in teams if h != a]
        rng.shuffle(pairs)
        for k, (h, a) in enumerate(pairs):
            day = 1 + (k % 28)
            month = 8 + (k // 28) % 10
            y = season + (1 if month > 12 else 0)
            m = month - 12 if month > 12 else month
            kickoff = f"{y}-{m:02d}-{day:02d}T15:00Z"
            lh = math.exp(0.25 + 0.2 + STRENGTH[h] - STRENGTH[a] * 0.5)
            la = math.exp(0.25 + STRENGTH[a] - STRENGTH[h] * 0.5)
            hs, as_ = int(rng.poisson(lh)), int(rng.poisson(la))
            hc, ac = int(rng.poisson(5 + 2 * STRENGTH[h])), int(rng.poisson(4 + 2 * STRENGTH[a]))
            if corners_missing_season == season:
                hc = ac = None
            eid += 1
            by_month.setdefault(f"{y}{m:02d}", []).append(
                make_event(eid, kickoff, season, 5000 + season, f"{season}-league", h, a, hs, as_, hc, ac))
        for month, events in sorted(by_month.items()):
            soccer.ingest_month(c, "eng.1", month, fetch=lambda *_, e=events: payload("eng.1", e),
                                retrieved_at="2026-11-09T20:00:00Z")


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SOCCER_MARKETS_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(ev, "P", {**ev.P, "amendments": [RULES]})
    c = db.connect(sport="soccer")
    db.migrate(c)
    db.claim_store(c, "soccer")
    yield c
    c.close()


def seal(family="goals", **kw):
    return backtest.seal({"family": family, "model": "poisson", "config": {"half_life_days": 365, "tau": 3, "D": 0.1},
                          "extra": {}, "frequency": {}, "protocol_sha256": ev.protocol_sha256(), "code": ev.code_digest(),
                          **kw})


def test_nothing_loads_before_the_stage_rules_are_written(tmp_path, monkeypatch):
    monkeypatch.setattr(ev, "P", {**ev.P, "amendments": []})
    with pytest.raises(RuntimeError, match="amendment 1"):
        ev.collect(None, "goals", 2015)


def test_reserved_seasons_need_a_valid_family_seal_and_are_logged_before_the_read(store, tmp_path):
    log = tmp_path / "logs" / "access.jsonl"
    with pytest.raises(ev.TestSeasonsLocked):
        ev.load(store, "goals", through=2023)
    bad = {**seal(), "config": {"half_life_days": 180, "tau": 3, "D": 0.1}}          # edited after sealing
    with pytest.raises(ev.TestSeasonsLocked):
        ev.load(store, "goals", through=2023, sealed=bad, access_log=log)
    with pytest.raises(ev.TestSeasonsLocked):
        ev.load(store, "goals", through=2023, sealed=seal("corners"), access_log=log)
    with pytest.raises(ev.TestSeasonsLocked):
        ev.load(store, "goals", through=2023, sealed=seal())                          # no access log
    with pytest.raises(ev.TestSeasonsLocked, match="prospective"):
        ev.load(store, "goals", through=2026, sealed=seal(), access_log=log)
    assert not log.exists()
    ev.load(store, "goals", through=2025, sealed=seal(), access_log=log, purpose="probe")
    entry = json.loads(log.read_text().splitlines()[0])
    assert entry["through"] == 2025 and entry["family"] == "goals" and entry["purpose"] == "probe"


def test_open_seasons_never_read_later_rows(store):
    synthetic_league(store, seasons=range(2020, 2025))
    d = ev.load(store, "goals", through=2022)
    assert {o.season for o in d.obs} == {2020, 2021, 2022}


def test_only_regulation_finals_of_the_regular_season_or_uefa_group_stage_are_usable(store):
    t = lambda *a, **k: make_event(*a, **k)
    soccer.ingest_month(store, "eng.1", "201509", retrieved_at="2026-11-09T20:00:00Z", fetch=lambda *_: payload("eng.1", [
        t(1, "2015-09-05T15:00Z", 2015, 1, "lg", "1", "2", 1, 0, 5, 3), t(2, "2015-09-12T15:00Z", 2015, 1, "lg", "3", "4", 2, 2, 4, 4),
        t(3, "2015-09-19T15:00Z", 2015, 1, "lg", "1", "3", 0, 0, 6, 2),
        t(4, "2015-09-26T15:00Z", 2015, 2, "relegation-playoff", "2", "9", 1, 1, 3, 3)]))
    soccer.ingest_month(store, "uefa.champions", "201509", retrieved_at="2026-11-09T20:00:00Z", fetch=lambda *_: payload(
        "uefa.champions", [t(5, "2015-09-15T19:00Z", 2015, 7, "group-stage", "1", "50", 3, 1, 8, 2),
                           t(6, "2015-09-16T19:00Z", 2015, 8, "round-of-16", "3", "51", 1, 0, 4, 4),
                           t(7, "2015-09-17T19:00Z", 2015, 7, "group-stage", "4", "52", 1, 1, 4, 4, status="STATUS_FINAL_AET")]))
    d = ev.load(store, "goals", through=2015)
    assert sorted(o.mid.split(":")[-1] for o in d.obs) == ["1", "2", "3", "5"]
    assert d.members[("eng.1", 2015)] == frozenset({"soccer:1", "soccer:2", "soccer:3", "soccer:4"})
    c = ev.load(store, "corners", through=2015)
    assert {o.mid.split(":")[-1]: (o.h, o.a) for o in c.obs}["5"] == (8, 2)


def test_a_forecast_never_sees_same_day_or_later_available_labels(store, monkeypatch):
    synthetic_league(store, seasons=range(2013, 2015))
    d = ev.load(store, "goals", through=2014)
    seen = []
    real_fit = ev.fit_strengths

    def spy(family, cfg, members, inputs, cutoff, targets):
        seen.append((cutoff, [o.local_date for o in inputs], [o.available_at for o in inputs]))
        return real_fit(family, cfg, members, inputs, cutoff, targets)
    monkeypatch.setattr(ev, "fit_strengths", spy)
    ev.walk(d, ev.Config(365, 3, 0.1), (2014,))
    assert seen
    for cutoff, days, avail in seen:
        assert all(x < cutoff[:10] for x in days) and all(a <= cutoff for a in avail)
    late = replace(d.obs[0], available_at="2099-01-01T00:00:00Z")
    d2 = ev.Data(d.family, [late, *d.obs[1:]], d.members, d.through, d.counts)
    seen.clear()
    ev.walk(d2, ev.Config(365, 3, 0.1), (2014,))
    assert all(late.mid not in str(x) for x in seen)


def test_a_promoted_team_starts_below_the_league_and_others_at_zero():
    members = {("eng.1", 2014): frozenset({"a", "b"}), ("eng.1", 2015): frozenset({"a", "c"})}
    assert ev.centre(members, "c", 2015, 0.25) == -0.25
    assert ev.centre(members, "a", 2015, 0.25) == 0.0
    assert ev.centre(members, "a", 2014, 0.25) == 0.0              # league not covered before: no promotion
    assert ev.centre(members, "x", 2015, 0.25) == 0.0              # not a league member (UEFA only)


def test_selection_tie_break_and_the_validation_choice_rule():
    a, b, c = ev.Config(180, 10, 0.1), ev.Config(365, 3, 0.25), ev.Config(365, 10, 0.25)
    assert ev.select_config([(1.0, a), (1.0 + 5e-10, b), (1.0, c)]) == c
    assert ev.select_config([(0.9, a), (1.0, c)]) == a

    def rows(vals, start="2020-09-0"):
        return [{"mid": str(i), "nll": v, "cutoff_at": f"2020-{1 + i // 28:02d}-{1 + i % 28:02d}T06:00:00Z"}
                for i, v in enumerate(vals)]
    rng = random.Random(1)
    base = rows([1.0 + rng.random() * 0.01 for _ in range(200)])
    po = rows([r["nll"] - 0.05 for r in base])
    better = rows([r["nll"] - 0.02 for r in po])
    same = rows([r["nll"] + (0.001 if i % 2 else -0.001) for i, r in enumerate(po)])
    assert ev.choose("dixon_coles", better, po, base)[1:] == ("dixon_coles", ev.paired(better, base), True)[0:1] + \
        (ev.paired(better, base), True)
    _, chosen, _, ok = ev.choose("dixon_coles", same, po, base)
    assert chosen == "poisson" and ok
    worse = rows([r["nll"] + 0.05 for r in base])
    _, chosen, vs, ok = ev.choose("negative_binomial", worse, worse, base)
    assert chosen == "poisson" and not ok and vs["ci95"][1] > 0


def test_second_stage_fits_refuse_any_season_outside_development_fit():
    p = {"mh": 1.4, "ma": 1.1, "h": 1, "a": 0, "season": 2020}
    with pytest.raises(LeakageError):
        ev.fit_rho([p])
    with pytest.raises(LeakageError):
        ev.fit_dispersion([{**p, "season": 2017}])


def test_grids_fold_the_tail_and_sum_to_one():
    for g in (ev.poisson_joint(9.0, 0.2, 10), ev.dc_joint(1.3, 0.9, 0.1, 10), ev.nb_joint(6.0, 4.0, 8.0, 25)):
        assert g.sum() == pytest.approx(1.0) and (g >= 0).all()
    g = ev.poisson_joint(9.0, 0.2, 10)
    assert g[10].sum() == pytest.approx(1 - sum(ev._poisson.pmf(k, 9.0) for k in range(10)))
    clipped = ev.dc_joint(4.0, 3.0, 0.3, 10)                 # rho beyond 1/(lh*la) is clipped: no negative cell
    assert (clipped >= 0).all()


def test_vectorised_events_equal_the_settlement_module():
    g = ev.nb_joint(5.6, 4.3, 9.0, 25)
    masks = ev.event_masks("corners")
    names = {"corners_total_over": ("corners_total", "over"), "corners_home_over": ("corners_home", "over"),
             "corners_away_over": ("corners_away", "over"), "corners_handicap_home": ("corners_handicap", "home")}
    for (m, line), mask in masks.items():
        market, side = names[m]
        assert float(g[mask].sum()) == pytest.approx(sm.probabilities(g.tolist(), market, side, line)["win"], abs=1e-12)
    g = ev.dc_joint(1.5, 1.1, -0.08, 10)
    gm = ev.event_masks("goals")
    assert float(g[gm[("btts_yes", None)]].sum()) == pytest.approx(sm.probabilities(g.tolist(), "btts", "yes", None)["win"])
    assert float(g[gm[("goals_total_over", 2.5)]].sum()) == pytest.approx(
        sm.probabilities(g.tolist(), "goals_total", "over", 2.5)["win"])


def test_development_runs_once_seals_and_the_test_runs_once(store, tmp_path, monkeypatch):
    synthetic_league(store)
    monkeypatch.setattr(ev, "GRID", {"goals": {"half_life_days": [365, 730], "tau": [3], "D": [0.1]},
                                     "corners": {"half_life_days": [365], "tau": [3], "D": [0.0]}})
    log = ev.experiments_log_path()
    with pytest.raises(ev.FrozenMismatch):                       # protocol not logged yet
        ev.develop(store, "goals", out_dir=tmp_path / "dev", experiments_log=log, workers=1)
    ev._append(log, {"stage": "protocol_written", "protocol_sha256": ev.protocol_sha256()})
    res = ev.develop(store, "goals", out_dir=tmp_path / "dev", experiments_log=log, workers=1, progress=lambda m: None)
    rep = res["report"]
    assert rep["development_tune"]["selected"]["half_life_days"] in (365, 730)
    assert rep["validation"]["choice"]["chosen"] in ("poisson", "dixon_coles")
    assert set(rep["validation"]["poisson"]["by_competition"]) == {"eng.1"}
    with pytest.raises(ev.RunAlreadyDone):
        ev.develop(store, "goals", out_dir=tmp_path / "dev2", experiments_log=log, workers=1)
    sealed = res["sealed"]
    assert sealed is not None, "the synthetic league has strong team effects: the model must beat the baseline"
    tampered = {**sealed, "model": "dixon_coles"}
    with pytest.raises(ev.FrozenMismatch):
        ev.run_test(store, tampered, access_log=ev.access_log_path(), experiments_log=log, out_dir=tmp_path / "t0")
    monkeypatch.setattr(ev, "code_digest", lambda: {"changed": "x"})
    with pytest.raises(ev.FrozenMismatch):
        ev.run_test(store, sealed, access_log=ev.access_log_path(), experiments_log=log, out_dir=tmp_path / "t0")
    monkeypatch.undo()
    monkeypatch.setenv("SOCCER_MARKETS_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(ev, "P", {**ev.P, "amendments": [RULES]})
    monkeypatch.setattr(ev, "GRID", {"goals": {"half_life_days": [365, 730], "tau": [3], "D": [0.1]},
                                     "corners": {"half_life_days": [365], "tau": [3], "D": [0.0]}})
    out = ev.run_test(store, sealed, access_log=ev.access_log_path(), experiments_log=log, out_dir=tmp_path / "t1")
    assert out["test"]["overall"]["n"] > 0 and set(out["reading"]) >= {"R1_family_skill", "verdict"}
    assert (tmp_path / "t1" / "reading.json").exists()
    with pytest.raises(ev.RunAlreadyDone):
        ev.run_test(store, sealed, access_log=ev.access_log_path(), experiments_log=log, out_dir=tmp_path / "t2")


def test_corner_seasons_with_too_few_labels_are_not_scored(store):
    synthetic_league(store, seasons=range(2013, 2016), corners_missing_season=2014)
    d = ev.load(store, "corners", through=2015)
    ok = ev.corner_season_ok(d)
    assert ok[("eng.1", 2014)] is False and ok[("eng.1", 2015)] is True
    assert {o.season for o in ev.scored(d, (2013, 2014, 2015))} == {2013, 2015}


def test_the_reading_applies_r1_to_r3_mechanically(monkeypatch):
    from app import soccer_live
    monkeypatch.setattr(soccer_live, "LAUNCH", ("eng.1", "esp.1", "uefa.champions"))
    mk = lambda ece: {"ece": ece, "mean_predicted": 0.5, "observed": 0.5}
    rep = {"test": {"overall": {"vs_frequency": {"ci95": [-0.03, -0.01]},
                                "markets": {"goals_total_over@2.5": mk(0.02), "goals_total_over@3.5": mk(0.04),
                                            "btts_yes@None": mk(0.06)}},
                    "by_competition": {"eng.1": {"n": 380, "vs_frequency": {"ci95": [-0.05, -0.001]}},
                                       "esp.1": {"n": 380, "vs_frequency": {"ci95": [-0.05, 0.001]}},
                                       "uefa.champions": {"n": 299, "vs_frequency": {"ci95": [-0.09, -0.02]}},
                                       "uefa.europa": {"n": 400, "vs_frequency": {"ci95": [-0.09, -0.02]}}}}}
    r = ev.read_test(rep)
    assert r["served_competitions"] == ["eng.1"] and r["served_markets"] == ["goals_total_over"]
    assert r["R2_market_calibration"]["btts_yes"] is False and r["verdict"] == "serve"
    rep["test"]["overall"]["vs_frequency"]["ci95"] = [-0.03, 0.0]
    r = ev.read_test(rep)
    assert r["verdict"] == "do_not_serve" and r["served_competitions"] == [] and r["served_markets"] == []


# Amendment 2 (2026-11-10, user's go after the corners v1 development): a both-zero corner count is "not delivered".
def test_a_both_zero_corner_count_is_not_a_label(store):
    t = lambda *a, **k: make_event(*a, **k)
    soccer.ingest_month(store, "eng.1", "201609", retrieved_at="2026-11-09T20:00:00Z", fetch=lambda *_: payload("eng.1", [
        t(1, "2016-09-05T15:00Z", 2016, 1, "lg", "1", "2", 1, 0, 0, 0), t(2, "2016-09-12T15:00Z", 2016, 1, "lg", "3", "4", 2, 2, 0, 3)]))
    c = ev.load(store, "corners", through=2016)
    labels = {o.mid.split(":")[-1]: (o.h, o.a) for o in c.obs}
    assert labels == {"1": (None, None), "2": (0, 3)}
    assert c.counts["zero_zero:eng.1:2016"] == 1 and c.counts["unlabelled:eng.1:2016"] == 1
    g = ev.load(store, "goals", through=2016)                       # goals are unaffected
    assert {o.mid.split(":")[-1]: (o.h, o.a) for o in g.obs}["1"] == (1, 0)


def test_only_a_retired_candidate_allows_a_second_development_and_never_a_test(store, tmp_path, monkeypatch):
    log = ev.experiments_log_path()
    ev._append(log, {"stage": "protocol_written", "protocol_sha256": ev.protocol_sha256()})
    ev._append(log, {"stage": "develop_completed", "family": "corners", "candidate_sha256": "a" * 64})
    with pytest.raises(ev.RunAlreadyDone):
        ev.develop(store, "corners", out_dir=tmp_path / "d", experiments_log=log, workers=1)
    ev._append(log, {"stage": "candidate_retired", "family": "corners", "candidate_sha256": "a" * 64, "amendment": 2})
    monkeypatch.setattr(ev, "load", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("reached the data")))
    with pytest.raises(RuntimeError, match="reached the data"):           # past the once-only gate
        ev.develop(store, "corners", out_dir=tmp_path / "d", experiments_log=log, workers=1)
    retired = backtest.seal({"family": "corners", "model": "poisson", "config": {"half_life_days": 365, "tau": 3, "D": 0.0},
                             "extra": {}, "frequency": {}, "protocol_sha256": ev.protocol_sha256(), "code": ev.code_digest()})
    ev._append(log, {"stage": "candidate_retired", "family": "corners", "candidate_sha256": retired["sha256"], "amendment": 2})
    with pytest.raises(ev.RunAlreadyDone, match="retired"):
        ev.run_test(store, retired, access_log=ev.access_log_path(), experiments_log=log, out_dir=tmp_path / "t")
