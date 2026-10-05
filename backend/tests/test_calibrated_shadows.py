"""Calibration overlays preserve the original forecast and cannot join voters or selectors."""
import copy
import json
import sqlite3

import pytest

from app import agreement, ingest, research_live, training_monitor
from app.candidates import calibrated as cal, live as candidates, registry
from test_live_markets import sealed, add_upcoming  # noqa: F401
from test_nhl_eval import nhl_store  # noqa: F401
from test_calibration_study import _table
from test_storage import conn, event, payload

NOW = "2026-10-12T22:00:00Z"
GID = "nhl:2026020001"


def test_fits_exclude_current_season_and_unavailable_results(monkeypatch):
    monkeypatch.setitem(cal.PAIRS, "nhl", (("a", "total"),))
    rows = _table()
    args = ({"a": 2017}, "nhl", 2020, "2020-10-01T00:00:00Z", "raw", "dataset", NOW)
    one = cal.fit_artifact({"total": rows}, *args)
    altered = copy.deepcopy(rows)
    for r in altered:
        if r["season"] == 2020:
            r["realised"] = 2 - r["realised"]
    altered.append({**rows[0], "game_id": "late-result", "result_available_at": "2021-01-01T00:00:00Z"})
    two = cal.fit_artifact({"total": altered}, *args)
    assert one == two
    f = one["fits"][cal.name("nhl", "a", "total")]
    assert f["training_games"] == 1500 and f["training_seasons"] == [2017, 2018, 2019]
    with pytest.raises(ValueError, match="eligible earlier"):
        cal.fit_artifact({"total": rows[:5]}, *args)


def test_apply_preserves_push_and_three_way_normalisation():
    v = cal.apply((0, .5), (.72, .1, .18), "total")
    assert v[1] == .1 and sum(v) == pytest.approx(1)
    assert .5 < v[0] / (v[0] + v[2]) < .8
    assert cal.apply((0, .5), (0, 1, 0), "total") == (0, 1, 0)
    r3 = cal.apply(1.5, (.7, .2, .1), "reg3")
    assert sum(r3) == pytest.approx(1) and r3[1] != .2
    with pytest.raises(ValueError, match="invalid"):
        cal.apply((0, 1), (.8, .3, .1), "total")


def _cohort(conn, raw, members, line=6.5, ref="one"):
    g = dict(conn.execute("SELECT * FROM games WHERE game_id=?", (GID,)).fetchone())
    c = research_live._cohort("nhl", g, "total", line, "model", None, None, raw, "goals_poisson",
                             NOW, NOW, members, {}, ["goals_glm"], None, "test")
    # Each raw ref produces a genuinely new cohort.
    c["members"][0]["ref"] = ref
    out = {"cohorts_existing": 0, "cohorts_created": 0, "skipped": {}}
    research_live._store(conn, c, {GID: {}}, out, True)


def test_ledger_idempotency_provenance_unavailability_and_shadow_isolation(sealed, monkeypatch, tmp_path):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    add_upcoming(sealed, start="2026-10-12T19:00:00", at="2026-10-12T20:30:00Z")
    raw = research_live.definition(sealed)
    defn = cal.definition(raw)
    _cohort(sealed, raw, [{"name": "goals_glm", "p": [.72, .1, .18], "ref": "one"}])
    assert cal.record(sealed, now=NOW)["skipped"]
    season = sealed.execute("SELECT season FROM games WHERE game_id=?", (GID,)).fetchone()[0]
    nm = cal.name("nhl", "goals_glm", "total")
    f = {"params": [0, .5], "training_games": 500, "results_available_through": "2021-05-01T00:00:00Z",
         "training_seasons": [2020], "results_fingerprint": "test-results"}
    art = {"version": cal.VERSION, "sport": "nhl", "season": season, "definition": defn, "raw_definition": raw,
           "created_at": "2026-10-01T00:00:00Z", "training_cutoff": "2026-10-01T00:00:00Z",
           "dataset_version": "test", "fits": {nm: f}}
    p = cal.artifact_path("nhl", defn, season)
    p.parent.mkdir(parents=True)
    def save():
        p.write_text(json.dumps({"artifact": art, "sha256": cal.digest(art)}))
    save()
    before = {t: sealed.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
              for t in ("predictions", "research_cohorts", "market_forecasts")}
    assert cal.record(sealed, now=NOW, write=False)["created"] == 0
    assert sealed.execute("SELECT count(*) FROM model_fits").fetchone()[0] == 0
    out = cal.record(sealed, now=NOW)
    assert out["created"] == 1 and not out["errors"], out
    assert cal.record(sealed, now=NOW)["already_recorded"] == 1
    r = sealed.execute("SELECT * FROM candidate_forecasts WHERE candidate=?", (nm,)).fetchone()
    assert r["line"] == 6.5 and r["p_mid"] == .1 and r["fit_id"]
    assert json.loads(r["inputs"])["raw_probability"] == [.72, .1, .18]
    assert r["line_ref"].startswith("research_cohorts:")
    monitored = next(f for f in training_monitor.status(sealed, NOW)["fits"] if f["member"] == f"cand:{nm}")
    assert "Platt/temperature" in monitored["versions"]["calibration"]
    assert "explicit" in monitored["next_update"]["rule"]
    for t, n in before.items():
        assert sealed.execute(f"SELECT count(*) FROM {t}").fetchone()[0] == n
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        sealed.execute("UPDATE candidate_forecasts SET p_a=.5")
    assert not cal.record(sealed, now="2026-10-12T23:00:00Z")["created"]
    _cohort(sealed, raw, [{"name": "goals_glm", "p": [.7, .1, .2], "ref": "two"}], 7.5, "two")
    assert cal.record(sealed, now=NOW)["created"] == 1
    # A corrupted file is never silently refitted or used.
    p.write_text(p.read_text().replace('"test-results"', '"corrupt"'))
    assert "digest mismatch" in str(cal.record(sealed, now=NOW)["errors"])
    art["created_at"] = "2026-10-13T00:00:00Z"
    save()
    assert "unavailable at this forecast time" in str(cal.record(sealed, now=NOW)["skipped"])
    art["created_at"] = "2026-10-01T00:00:00Z"
    save()
    rows = candidates.roster(sealed, "nhl", NOW)["candidates"]
    shadow = next(x for x in rows if x["name"] == nm)
    assert shadow["coverage"]["fitted"] and shadow["prospective"]["games"] == 1
    assert not shadow["coverage"]["votes"] and shadow["coverage"]["selectable"] == {"alone": [], "inside": []}
    assert nm not in registry.BY_NAME and nm not in agreement.NAMES["nhl"]
    assert nm not in research_live.sources("nhl", "total")


def test_registered_pairs_exclude_inconclusive_ridge_away():
    assert sum(map(len, cal.PAIRS.values())) == 9
    assert ("ridge_points", "team_total_away") not in cal.PAIRS["nba"]


def test_prepare_is_immutable_and_never_retrains_a_frozen_season(monkeypatch, tmp_path):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    monkeypatch.setitem(cal.PAIRS, "nhl", (("a", "total"),))
    monkeypatch.setattr(cal.research_live, "definition", lambda _: "raw")
    monkeypatch.setattr(cal.historical_start, "_history", lambda *_: ({"total": _table()}, {"a": 2017}, "dataset", {}, []))
    ticks = iter(["2026-10-04T10:00:00Z", "2026-10-04T10:00:01Z", "2026-10-04T10:01:00Z", "2026-10-04T10:02:00Z"])
    monkeypatch.setattr(cal, "utc_now", lambda: next(ticks))
    one = cal.prepare(None, "nhl", 2020, "2020-10-01T00:00:00Z")
    assert one["artifact"]["created_at"] == "2026-10-04T10:01:00Z"
    original = cal.artifact_path("nhl", one["artifact"]["definition"], 2020).read_bytes()
    def forbidden(*_):
        raise AssertionError("a frozen season must not retrain")
    monkeypatch.setattr(cal.historical_start, "_history", forbidden)
    two = cal.prepare(None, "nhl", 2020, "2021-10-01T00:00:00Z")
    assert two["reused"] and two["artifact"]["fits"] == json.loads(original)["artifact"]["fits"]
    assert cal.artifact_path("nhl", one["artifact"]["definition"], 2020).read_bytes() == original


def test_job_records_calibration_even_when_other_candidates_fail(monkeypatch):
    from app import jobs
    monkeypatch.setattr(candidates, "record", lambda *a, **k: {"status": "failed", "errors": {"pool": "test failure"}})
    calls = []
    def shadow(*a, **k):
        calls.append(k)
        return {"status": "partial", "created": 1, "skipped": {"calibrated:test": "no fit"}}
    monkeypatch.setattr(cal, "record", shadow)
    result = jobs.candidate_pool(None, game_ids=[GID], now=NOW)
    assert calls == [{"game_ids": [GID], "now": NOW}]
    assert result["calibrated_shadows"]["created"] == 1 and result["status"] == "failed"
    assert result["skipped"]["calibrated:test"] == "no fit"


@pytest.fixture(params=["nba", "nhl"])
def shadow_cohorts(request, conn, monkeypatch):
    sport = request.param
    if sport == "nhl":
        add_upcoming(conn, start="2026-10-12T19:00:00", at="2026-10-12T20:30:00Z")
    else:
        e = event(date="2026-10-12T23:00Z")
        e["season"]["year"] = 2027
        ingest.ingest_month(conn, "202610", fetch=lambda _: payload(e), retrieved_at="2026-10-12T20:30:00Z")
    g = dict(conn.execute("SELECT * FROM games").fetchone())
    monkeypatch.setattr(research_live, "definition", lambda _: "raw")
    monkeypatch.setattr(candidates, "_current_definitions", lambda _: (None, "raw"))
    defn = cal.definition("raw")
    fits = {}
    for market in sorted({m for _, m in cal.PAIRS[sport]}):
        names = [n for n, m in cal.PAIRS[sport] if m == market]
        vector = [.7, .2, .1] if market == "reg3" else [.7, 0, .3] if market == "winner" else [.72, .1, .18]
        members = [{"name": n, "p": vector, "ref": f"raw:{n}:{market}"} for n in names]
        line = None if market in ("winner", "reg3") else 220.0 if sport == "nba" else 6.0
        c = research_live._cohort(sport, g, market, line, None if line is None else "model", None, None,
                                 "raw", names[0], NOW, NOW, members, {}, names, None, "test")
        out = {"cohorts_existing": 0, "cohorts_created": 0, "skipped": {}}
        research_live._store(conn, c, {g["game_id"]: {}}, out, True)
        assert out["cohorts_created"] == 1
        for n in names:
            fits[cal.name(sport, n, market)] = {
                "params": 1.5 if market == "reg3" else [0, .5], "training_games": 500,
                "results_available_through": "2024-06-01T00:00:00Z", "training_seasons": [2024],
                "results_fingerprint": f"test:{n}:{market}"}
    art = {"version": cal.VERSION, "sport": sport, "season": 2027, "definition": defn, "raw_definition": "raw",
           "created_at": "2026-10-01T00:00:00Z", "training_cutoff": "2026-10-01T00:00:00Z",
           "dataset_version": "test", "fits": fits}
    p = cal.artifact_path(sport, defn, 2027)
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"artifact": art, "sha256": cal.digest(art)}))
    return conn, sport, g["game_id"], art, p


def test_every_supported_pair_records_exact_raw_cohort_and_stays_isolated(shadow_cohorts):
    conn, sport, gid, art, _ = shadow_cohorts
    assert all("frozen fit ready" in r["training_status"] for r in cal.roster(conn, sport, NOW))
    before = [tuple(r) for r in conn.execute("SELECT * FROM research_cohorts")]
    result = cal.record(conn, game_ids=[gid], now=NOW)
    assert not result["errors"], str(result["errors"])
    assert result["status"] == "success" and result["created"] == len(cal.PAIRS[sport]), result
    assert cal.record(conn, game_ids=[gid], now=NOW)["already_recorded"] == len(cal.PAIRS[sport])
    for r in conn.execute("SELECT * FROM candidate_forecasts"):
        inputs = json.loads(r["inputs"])
        c = conn.execute("SELECT * FROM research_cohorts WHERE cohort_id=?", (inputs["cohort_id"],)).fetchone()
        assert r["line"] == c["line"] and r["line_source"] == (c["line_source"] or "none")
        assert inputs["line_source"] == c["line_source"]
        assert r["line_ref"] == f"research_cohorts:{c['cohort_id']}"
        assert inputs["raw_ref"] == f"raw:{inputs['raw_member']}:{r['market']}"
        assert (r["p_a"], r["p_mid"], r["p_b"]) == pytest.approx(
            cal.apply(art["fits"][r["candidate"]]["params"], inputs["raw_probability"], r["market"]))
        assert r["artifact_sha256"] == cal.digest(art)
        assert r["candidate"] not in registry.BY_NAME and r["candidate"] not in agreement.NAMES[sport]
        assert r["candidate"] not in research_live.sources(sport, r["market"])
    assert len(candidates.latest(conn, gid, NOW)) == len(cal.PAIRS[sport])
    assert [tuple(r) for r in conn.execute("SELECT * FROM research_cohorts")] == before
    assert conn.execute("SELECT count(*) FROM predictions").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM market_forecasts").fetchone()[0] == 0


@pytest.mark.parametrize("problem", ["missing_pair", "wrong_raw", "malformed"])
def test_roster_never_claims_unusable_artifacts_are_ready(shadow_cohorts, problem):
    conn, sport, gid, art, p = shadow_cohorts
    nm = next(iter(art["fits"]))
    if problem == "missing_pair":
        del art["fits"][nm]
    elif problem == "wrong_raw":
        art["raw_definition"] = "different-core-version"
    else:
        art = []
    p.write_text(json.dumps({"artifact": art, "sha256": cal.digest(art)}))
    rows = {r["name"]: r for r in cal.roster(conn, sport, NOW)}
    assert "unavailable" in rows[nm]["training_status"]
    assert "frozen fit ready" not in rows[nm]["training_status"]
    if problem == "missing_pair":
        assert all("frozen fit ready" in r["training_status"] for n, r in rows.items() if n != nm)
        result = cal.record(conn, game_ids=[gid], now=NOW)
        assert f"{nm}:{gid}" in result["skipped"] and not result["errors"]
