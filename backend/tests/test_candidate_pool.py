"""Candidate pool (docs/CANDIDATE_POOL.md): registry, point-in-time feature walk, fitting, goalie inputs, prospective
recording and roster. Synthetic NHL store, no network."""
import json
import sqlite3
from dataclasses import replace

import pytest

from app import backtest, nhl_eval, nhl_shots
from app.candidates import engine, features, goalies, learners, live as pool_live, registry
from app.models.hockey import GoalsConfig, HockeyEloConfig
from test_live_markets import add_upcoming, sealed  # noqa: F401  (fixture)
from test_nhl_eval import nhl_store  # noqa: F401  (fixture)
from test_nhl_xg import fake_fetch

NOW = "2026-10-12T22:00:00Z"            # one hour before the upcoming game (23:00Z)
TIPOFF = "2026-10-12T23:00:00Z"
GID = "nhl:2026020001"


def test_registry_names_settings_and_audit():
    names = [s.name for s in registry.SPECS]
    assert len(names) == len(set(names))
    for s in registry.SPECS:
        assert s.family in learners.SETTINGS and s.features in features.FEATURE_SETS and s.head in registry.HEAD_MARKETS
        assert s.role in ("reference", "candidate") and s.why
        assert len(registry.definition(s)) == 16
    audited = " ".join(a["family"] for a in registry.FAMILY_AUDIT).lower()
    for fam in ("logistic", "multinomial", "elastic", "extra trees", "xgboost", "catboost", "ngboost", "negative binomial",
                "bivariate", "neural"):
        assert fam in audited
    # A reference re-expresses an existing member and is never offered for selection.
    assert {s.name for s in registry.SPECS if s.role == "reference"} == {"logit_l2:nba_base", "ridge:nba_base", "logit_l2:nhl_base"}


def _nhl_walk(conn, rows=None):
    d = nhl_eval.load_decision(conn)
    rows = rows or nhl_eval.load_rows(conn, "test")
    rec = features.NhlRecorder(HockeyEloConfig(**d["hockey_elo_config"]), GoalsConfig(**d["goals_config"]), None)
    return features.walk(rec, rows, None), rows


def test_feature_walk_never_sees_later_results(sealed):
    walk, rows = _nhl_walk(sealed)
    last = max(r.game.season for r in rows)
    flipped = [replace(r, home_score=r.away_score, away_score=r.home_score) if r.game.season == last else r for r in rows]
    walk2, _ = _nhl_walk(sealed, flipped)
    for gid, row in walk.items():
        if row.season < last:
            assert walk2[gid].x == row.x                         # earlier features unchanged by later results
    changed = [gid for gid, r in walk.items() if r.season == last and walk2[gid].x != r.x]
    assert changed                                               # within the season, later games do react


def test_fits_use_earlier_seasons_with_available_results_only(sealed, monkeypatch):
    monkeypatch.setattr(registry, "MIN_TRAIN", {"nba": 200, "nhl": 100})
    walk, _ = _nhl_walk(sealed)
    rows = list(walk.values())
    first_2020 = min(r.cutoff_at for r in rows if r.season == 2020)
    train = engine.training_rows(rows, 2020, first_2020)
    assert train and all(r.season < 2020 and r.result_available_at <= first_2020 for r in train)
    out, fits, _ = engine.walk(registry.BY_NAME["multinomial_l2:nhl_base"], rows)
    assert fits and all(f["results_through"] <= min(r.cutoff_at for r in rows if r.season == f["season"]) for f in fits)
    o = next(iter(out.values()))
    w, r3 = engine.vector(o, "winner", None), engine.vector(o, "reg3", None)
    assert abs(sum(w) - 1) < 1e-9 and abs(sum(r3) - 1) < 1e-9 and engine.vector(o, "total", 5.5) is None
    g, _, _ = engine.walk(registry.BY_NAME["poisson_draw:nhl_base"], rows)
    og = next(iter(g.values()))
    for m, line in (("winner", None), ("reg3", None), ("spread", -1.5), ("total", 5.5), ("team_total_home", 2.5)):
        assert abs(sum(engine.vector(og, m, line)) - 1) < 1e-9


def test_normal_head_spread_comes_from_earlier_out_of_sample_residuals():
    res = [(2021, 10.0, 20.0)] * engine.MIN_RESIDUALS + [(2022, 100.0, 100.0)]
    assert engine.residual_sd(res, 2022) == (10.0, 20.0)         # same-season residuals excluded
    assert engine.residual_sd(res[:10], 2022) is None


def test_ngboost_is_reproducible_or_reported_missing():
    """NGBoost is an optional learner (the `research` extra, not installed in the live venv). Without it the candidate must be
    reported unavailable; with it, fits must be reproducible."""
    from app.candidates import registry
    ng = [s for s in registry.SPECS if s.extra is not None]
    try:
        import ngboost  # noqa: F401
    except ImportError:
        assert ng and not any(registry.available(s) for s in ng)
        return
    import numpy as np
    rng = np.random.default_rng(0)
    X = rng.normal(size=(600, 4))
    sd = np.exp(0.5 * X[:, 0]) * 5
    m, t = X @ [3, 1, 0, 0] + rng.normal(scale=sd), 200 + X @ [0, 0, 2, 0] + rng.normal(scale=sd)
    seasons = [2020] * 300 + [2021] * 300
    a = learners.fit_normal("ngboost", X, m, t, seasons).predict(X[:5])
    b = learners.fit_normal("ngboost", X, m, t, seasons).predict(X[:5])
    assert a == b


def test_goalie_parse_and_ratings():
    body = {"homeTeam": {"id": 1}, "awayTeam": {"id": 2},
            "plays": [{"sortOrder": 3, "details": {"goalieInNetId": 31, "eventOwnerTeamId": 2}, "periodDescriptor": {"periodType": "REG"}},
                      {"sortOrder": 5, "details": {"goalieInNetId": 30, "eventOwnerTeamId": 1}, "periodDescriptor": {"periodType": "REG"}},
                      {"sortOrder": 9, "details": {"goalieInNetId": 35, "eventOwnerTeamId": 2}, "periodDescriptor": {"periodType": "REG"}},
                      {"sortOrder": 11, "details": {"goalieInNetId": 31, "eventOwnerTeamId": 2}, "periodDescriptor": {"periodType": "SO"}}],
            "rosterSpots": [{"playerId": 31, "positionCode": "G", "firstName": {"default": "Jöel"}, "lastName": {"default": "Hofer"}},
                            {"playerId": 7, "positionCode": "C", "firstName": {"default": "A"}, "lastName": {"default": "B"}}]}
    d = goalies.parse(body)
    assert d["starter"] == {"home": 31, "away": 30} and d["shots"] == {3: 31, 5: 30, 9: 35}
    assert d["names"] == {31: "joel hofer"} and goalies.normalise("Joël Hofer") == "joel hofer"
    gi = goalies.GoalieInputs(xg=None, detail={})
    gi.xga[31], gi.ga[31] = 75.0, 60.0
    assert gi.rating(31) == pytest.approx(15 / 100) and gi.rating(None) == 0.0 and gi.rating(99) == 0.0


@pytest.fixture
def upcoming(sealed, monkeypatch):
    monkeypatch.setattr(registry, "MIN_TRAIN", {"nba": 200, "nhl": 100})
    add_upcoming(sealed, start="2026-10-12T19:00:00", at="2026-10-12T20:30:00Z")
    nhl_shots.import_pending(sealed, None, pause=0, fetch=fake_fetch(sealed), log=lambda *_: None)
    return sealed


SPECS = ["multinomial_l2:nhl_base", "poisson_draw:nhl_base", "logit_l2:nhl_goalie"]


def _record(conn, now=NOW, **kw):
    return pool_live.record(conn, now=now, specs=[registry.BY_NAME[s] for s in SPECS], **kw)


def test_live_record_is_research_only_two_hour_append_only_and_idempotent(upcoming):
    before = upcoming.execute("SELECT count(*) FROM predictions").fetchone()[0], \
        upcoming.execute("SELECT count(*) FROM market_forecasts").fetchone()[0]
    assert _record(upcoming, now="2026-10-12T20:30:00Z")["eligible_games"] == 0          # 2.5 h before: outside the window
    lines = {(GID, "spread"): {"line": -1.5, "source": "core_cohort", "ref": "cohort:1"},
             (GID, "total"): {"line": 6.5, "source": "core_cohort", "ref": "cohort:2"}}
    out = _record(upcoming, lines=lines)
    assert out["errors"] == {} and out["created"] > 0, out
    rows = upcoming.execute("SELECT * FROM candidate_forecasts").fetchall()
    assert {r["model_version"].split(":")[0] for r in rows} == {"research-cand"}
    by = {(r["candidate"], r["market"]): r for r in rows}
    assert by[("poisson_draw:nhl_base", "total")]["line"] == 6.5 and by[("poisson_draw:nhl_base", "total")]["line_ref"] == "cohort:2"
    assert ("poisson_draw:nhl_base", "team_total_home") not in by                         # no exact line → not recorded
    assert "no exact line" in out["skipped"]["poisson_draw:nhl_base"][f"{GID}:team_total_home"]
    assert by[("multinomial_l2:nhl_base", "reg3")]["line"] is None
    goalie = json.loads(by[("logit_l2:nhl_goalie", "winner")]["inputs"])
    assert goalie["goalie_source"] == "listing" and goalie["listing"]["home"]["status"] == "no_snapshot"
    assert all(r["generated_at"] < r["tipoff_at"] for r in rows)
    again = _record(upcoming, lines=lines)
    assert again["created"] == 0 and again["already_recorded"] == out["created"]
    after = upcoming.execute("SELECT count(*) FROM predictions").fetchone()[0], \
        upcoming.execute("SELECT count(*) FROM market_forecasts").fetchone()[0]
    assert after == before                                                                # nothing reaches primary tables
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        upcoming.execute("UPDATE candidate_forecasts SET p_a=0.5")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        upcoming.execute("DELETE FROM candidate_forecasts")
    r = dict(rows[0])
    r.pop("forecast_id")
    for when in ("2026-10-12T20:59:59Z", TIPOFF):
        r.update(generated_at=when, idempotency_key=f"x{when}")
        with pytest.raises(sqlite3.IntegrityError, match="two hours"):
            upcoming.execute(f"INSERT INTO candidate_forecasts ({','.join(r)}) VALUES ({','.join('?' * len(r))})", list(r.values()))
    fits = upcoming.execute("SELECT * FROM model_fits WHERE member LIKE 'cand:%'").fetchall()
    assert {f["member"] for f in fits} == {f"cand:{s}" for s in SPECS} and all(f["trained_season"] == 2027 for f in fits)


def test_lines_default_to_the_latest_core_cohort(upcoming):
    def cohort(market, line, at, defn="d"):
        upcoming.execute("INSERT INTO research_cohorts (idempotency_key, sport, game_id, market, line, line_source, definition, "
                         "primary_name, information_at, generated_at, tipoff_at, members, agreement) VALUES "
                         "(?,?,?,?,?,?,?,?,?,?,?,?,?)", (f"{market}{line}{at}{defn}", "nhl", GID, market, line, "model", defn,
                                                         "goals_poisson", at, at, TIPOFF, "[]", "{}"))
    cohort("total", 5.5, "2026-10-12T21:30:00Z")
    cohort("total", 6.5, "2026-10-12T21:50:00Z")
    cohort("total", 7.5, "2026-10-12T21:55:00Z", defn="d:expanded-v1")       # the expanded experiment is never used
    cohort("spread", -1.5, "2026-10-12T22:30:00Z")                            # after `now`: not yet known
    lines = pool_live.core_lines(upcoming, [GID], NOW)
    assert lines[(GID, "total")]["line"] == 6.5 and lines[(GID, "total")]["ref"].startswith("research_cohorts:")
    assert (GID, "spread") not in lines
    out = _record(upcoming)
    rows = upcoming.execute("SELECT market, line, line_source FROM candidate_forecasts WHERE candidate='poisson_draw:nhl_base' "
                            "AND line IS NOT NULL").fetchall()
    assert [(r["market"], r["line"], r["line_source"]) for r in rows] == [("total", 6.5, "core_cohort:model")]
    assert "no exact line" in out["skipped"]["poisson_draw:nhl_base"][f"{GID}:spread"]


def test_a_failing_candidate_never_blocks_the_others(upcoming, monkeypatch):
    real = engine.fit

    def broken(spec, train):
        if spec.name == "multinomial_l2:nhl_base":
            raise RuntimeError("boom")
        return real(spec, train)
    monkeypatch.setattr(engine, "fit", broken)
    out = _record(upcoming)
    assert out["status"] == "partial" and "boom" in out["errors"]["multinomial_l2:nhl_base"]
    assert {r["candidate"] for r in upcoming.execute("SELECT candidate FROM candidate_forecasts")} == \
        {"poisson_draw:nhl_base", "logit_l2:nhl_goalie"}
    monkeypatch.setattr(pool_live, "_record", lambda *a: (_ for _ in ()).throw(ValueError("all down")))
    assert pool_live.record(upcoming, now=NOW)["status"] == "failed"                     # reported, never raised


def test_stale_inputs_skip_with_a_reason():
    from types import SimpleNamespace
    from app.models.base import PregameGame
    g = PregameGame(GID, 2027, "regular", TIPOFF, "nhl:6", "nhl:16", False)
    eng = SimpleNamespace(sport="nhl")
    rec = SimpleNamespace(xg=SimpleNamespace(lines={"a", "b", "c"}))
    recent = {(2027, "nhl:6"): ["a", "b", "c"], (2027, "nhl:16"): ["b", "x"]}
    assert "stale inputs: 1 recent final(s) without shots (nhl:16)" == pool_live._stale(eng, rec, g, recent)
    recent[(2027, "nhl:16")] = ["b", "c", "a", "x"]           # only the 3 most recent are checked
    assert pool_live._stale(eng, rec, g, recent) is None
    assert pool_live._stale(eng, rec, g, {}) is None           # season opener: nothing to be stale


def test_artifacts_are_reused_and_roster_reports_every_candidate(upcoming):
    _record(upcoming)
    files = sorted(p.name for p in pool_live.artifact_dir("nhl").rglob("*.pkl"))
    assert len(files) == 3
    _record(upcoming)
    assert sorted(p.name for p in pool_live.artifact_dir("nhl").rglob("*.pkl")) == files
    r = pool_live.roster(upcoming, "nhl", as_of="2026-10-12T22:30:00Z")
    names = {c["name"]: c for c in r["candidates"]}
    assert set(names) == {s.name for s in registry.for_sport("nhl")}
    assert names["poisson_draw:nhl_base"]["prospective"] == {"games": 1, "settled": 0}
    assert names["poisson_draw:nhl_base"]["latest_fit"]["trained_season"] == 2027
    assert names["logit_l2:nhl_base"]["selection"] == "reference only (never selected)"
    assert r["family_audit"] and "Research only" in r["note"]


def test_selectable_only_with_a_held_out_gain_that_beats_the_strongest_member():
    ci = lambda lo, hi: {"mean": (lo + hi) / 2, "ci95": [lo, hi]}
    m = {"strongest_existing": "s", "existing": {"s": {"log_loss": 0.660}},
         "candidates": {"c": {"minus_strongest": ci(-0.001, 0.001)}, "d": {"minus_strongest": ci(-0.003, -0.001)}},
         "combinations": [
             {"combination": "avg:s+x+c vs avg:s+x (ablation: remove c)", "log_loss_with": 0.662,      # repairs a weak triple
              "with_minus_without": ci(-0.002, -0.001)},
             {"combination": "avg:s+c vs s (ablation: remove c)", "log_loss_with": 0.659, "with_minus_without": ci(-0.002, 0.0001)}]}
    assert pool_live.held_out_gains(m, "c") == []
    assert pool_live.held_out_gains(m, "d") == ["alone vs strongest member"]
    m["combinations"][1]["with_minus_without"] = ci(-0.002, -0.0001)
    assert pool_live.held_out_gains(m, "c") == ["avg:s+c vs s"]


def test_pool_trigger_rejects_malformed_times_and_games_no_longer_scheduled(upcoming):
    """Audit P13 (migration 0020): the candidate-forecast guard matches the research-cohort guard."""
    lines = {(GID, "spread"): {"line": -1.5, "source": "core_cohort", "ref": "cohort:1"}}
    assert _record(upcoming, lines=lines)["created"] > 0
    r = dict(upcoming.execute("SELECT * FROM candidate_forecasts LIMIT 1").fetchone())
    r.pop("forecast_id")

    def insert(**kw):
        row = {**r, **kw}
        upcoming.execute(f"INSERT INTO candidate_forecasts ({','.join(row)}) VALUES ({','.join('?' * len(row))})",
                         list(row.values()))
    for bad in ("2026-10-12 22:00:00", "2026-10-12T22:00:00", "2026-10-12T22:00:00.5Z"):     # in the window, but malformed
        with pytest.raises(sqlite3.IntegrityError, match="two hours"):
            insert(generated_at=bad, idempotency_key=f"m{bad}")
    insert(generated_at="2026-10-12T22:30:00Z", idempotency_key="ok")                         # well formed: accepted
    add_upcoming(upcoming, start="2026-10-12T19:00:00", state=7, hs=3, vs=1, period=3, at="2026-10-12T22:40:00Z")
    assert upcoming.execute("SELECT status FROM games WHERE game_id=?", (GID,)).fetchone()[0] != "scheduled"
    with pytest.raises(sqlite3.IntegrityError, match="two hours"):
        insert(generated_at="2026-10-12T22:45:00Z", idempotency_key="final")


def test_tampered_or_undigested_artifact_makes_only_that_candidate_unavailable(upcoming):
    """Audit P8: a stored fit is verified before it is unpickled; a mismatch is an error for that candidate only (no refit,
    no substitute), and the other candidates still record."""
    lines = {(GID, "spread"): {"line": -1.5, "source": "core_cohort", "ref": "cohort:1"}}
    first = _record(upcoming, lines=lines, write=False)
    assert first["errors"] == {}
    path = next(p for p in pool_live.artifact_dir("nhl").rglob("*.pkl") if "poisson_draw__nhl_base" in str(p))
    assert pool_live._sidecar(path).exists()
    path.write_bytes(path.read_bytes() + b"x")                                     # tampered
    out = _record(upcoming, lines=lines)
    assert "ArtifactIntegrityError" in out["errors"]["poisson_draw:nhl_base"] and "mismatch" in out["errors"]["poisson_draw:nhl_base"]
    cands = {r["candidate"] for r in upcoming.execute("SELECT candidate FROM candidate_forecasts")}
    assert "poisson_draw:nhl_base" not in cands and {"multinomial_l2:nhl_base", "logit_l2:nhl_goalie"} <= cands
    assert path.read_bytes().endswith(b"x")                                         # never silently refit over
    pool_live._sidecar(path).unlink()                                             # written after 0020: never adopted
    assert "no digest" in _record(upcoming, lines=lines)["errors"]["poisson_draw:nhl_base"]


def test_legacy_artifact_without_digest_is_adopted_once_and_then_verified(upcoming):
    """Artifacts saved before migration 0020 (no digest yet) are adopted once — digest written, adoption reported — so
    live research keeps recording after the upgrade; afterwards a change to the file is an error."""
    import os
    lines = {(GID, "spread"): {"line": -1.5, "source": "core_cohort", "ref": "cohort:1"}}
    _record(upcoming, lines=lines, write=False)
    path = next(p for p in pool_live.artifact_dir("nhl").rglob("*.pkl") if "poisson_draw__nhl_base" in str(p))
    pool_live._sidecar(path).unlink()
    os.utime(path, (946684800, 946684800))                                        # written in 2000: before 0020
    out = _record(upcoming, lines=lines)
    assert "poisson_draw:nhl_base" not in out["errors"] and [a["artifact"] for a in out["artifacts_adopted"]] == [path.name]
    assert pool_live._sidecar(path).exists()
    assert _record(upcoming, lines=lines)["artifacts_adopted"] == []               # once only
    path.write_bytes(path.read_bytes() + b"x")
    assert "mismatch" in _record(upcoming, lines=lines)["errors"]["poisson_draw:nhl_base"]


def test_experiment_log_lists_skipped_and_failed_configurations(monkeypatch):
    """Audit P7: every declared configuration appears in the experiment log, with a status and reason when it did not run."""
    from app.candidates import study
    not_run = {"ngboost:nba_base": {"status": "skipped", "reason": "optional dependency research not installed"},
               "mlp:nba_base": {"status": "failed", "reason": "ValueError: boom"}}
    rows = study.not_run_entries("nba", "total", not_run, {"at": "t", "stage": "screen"})
    by = {r["candidate"]: r for r in rows}
    assert by["ngboost:nba_base"]["status"] == "skipped" and "not installed" in by["ngboost:nba_base"]["reason"]
    assert all(r["market"] == "total" and r["definition"] for r in rows)
    assert ("mlp:nba_base" in by) == ("total" in registry.BY_NAME["mlp:nba_base"].markets)
