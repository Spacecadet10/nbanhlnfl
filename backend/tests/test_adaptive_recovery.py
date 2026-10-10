"""Retrospective adaptive reconstructions (HANDOFF design, Oct 9): a separate ledger, a read-only replay of the existing
adaptive evaluator, and only fits trained on and informed by data before the simulated pregame cutoff."""
import json
import sqlite3

import pytest

from app import adaptive, jobs, recovery
from test_adaptive import CYCLE_AT, TRAIN_AT, _settle, store  # noqa: F401  (fixture)
from test_live_markets import add_upcoming, sealed  # noqa: F401  (fixture)
from test_nhl_eval import nhl_store  # noqa: F401  (fixture)
from test_research_live import GID, run

TIPOFF = "2026-10-12T23:00:00Z"
CUTOFF = "2026-10-12T22:59:59Z"
AFTER = "2026-10-13T04:00:00Z"
LIVE_TABLES = ("adaptive_forecasts", "predictions", "market_forecasts", "research_cohorts", "candidate_forecasts",
               "selector_decisions", "recovered_forecasts")


def _train(conn, monkeypatch, at):
    """Train with the fit rows stamped at `at` (the real clock would make publication times depend on the test date)."""
    monkeypatch.setattr(adaptive, "utc_now", lambda: at)
    t = adaptive.train(conn, now=at)
    assert t["status"] in ("success", "partial"), t.get("errors")
    return t


def _later_game(conn):
    """A scheduled game after the training times below, so a training run has a next game to prepare for."""
    add_upcoming(conn, gid=2026020002, start="2026-10-14T19:00:00", at="2026-10-12T20:30:00Z")


def _earlier_definition_in_force(conn):
    """A combiner of an earlier definition was in force before the game (as when a dependency change re-defines the
    system): the adaptive era has begun, but nothing of the current definition exists yet."""
    conn.execute("INSERT INTO adaptive_fits (sport, kind, name, definition, period, schedule, status, training_cutoff, detail, "
                 "started_at, finished_at, idempotency_key) VALUES ('nhl', 'combiner', 'combiner', 'earlier-definition', "
                 "'2027:2026-W41', 'weekly', 'success', '2026-10-06T04:00:00Z', '{}', '2026-10-07T09:30:00Z', "
                 "'2026-10-07T10:00:00Z', 'earlier')")


def _counts(conn):
    return [conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in LIVE_TABLES]


def _rows(conn):
    return {r["market"]: r for r in conn.execute("SELECT * FROM adaptive_recovered_forecasts WHERE game_id=?", (GID,))}


def test_a_missed_game_is_replayed_exactly_as_the_live_evaluation_into_its_own_ledger(store, monkeypatch):
    _train(store, monkeypatch, TRAIN_AT)
    run(store, CYCLE_AT)                         # the primary two-hour forecasts; the adaptive step itself was missed
    live_eval = {f["market"]: f for f in adaptive.record(store, game_ids=[GID], now=CUTOFF, write=False)["forecasts"]}
    _settle(store)
    before = _counts(store)
    out = recovery.recover_adaptive(store, now=AFTER)
    assert out["created"] == 6 and not out["errors"] and not out["deferred"], out
    rows = _rows(store)
    assert set(rows) == {"winner", "reg3", "spread", "total", "team_total_home", "team_total_away"}
    for m, r in rows.items():
        assert r["status"] == "reconstructed" and r["cutoff_at"] == CUTOFF and r["reconstructed_at"] == AFTER
        assert r["combiner_training_cutoff"] <= r["cutoff_at"] and r["latest_result_at"] <= r["cutoff_at"]
        assert r["combiner_sha256"] and abs(r["p_a"] + r["p_mid"] + r["p_b"] - 1) < 1e-6
        # The replay IS the live evaluator at the cutoff: identical probabilities and line.
        assert [r["p_a"], r["p_mid"], r["p_b"]] == live_eval[m]["p"] and r["line"] == live_eval[m]["line"]
        assert all(x["artifact_sha256"] and x["training_cutoff"] <= CUTOFF and x["information_at"] <= CUTOFF
                   for x in json.loads(r["members"]) if x["fit_id"] is not None)
    assert rows["spread"]["line_ref"].startswith("market_forecasts:")
    assert rows["winner"]["p_mid"] == 0.0                          # binary market: no draw mass, still a valid forecast
    # Nothing reaches live, selection or training tables; prospective rows ignore the ledger.
    assert _counts(store) == before and store.execute("SELECT count(*) FROM adaptive_forecasts").fetchone()[0] == 0
    assert adaptive.prospective_rows(store, "nhl", out["definition"], AFTER) == []
    assert recovery.recover_adaptive(store, now="2026-10-13T05:00:00Z")["created"] == 0          # idempotent
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.execute("UPDATE adaptive_recovered_forecasts SET p_a=0.5")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.execute("DELETE FROM adaptive_recovered_forecasts")
    rep = recovery.adaptive_report(store, AFTER)
    assert len(rep["records"]) == 6 and {r["outcome"] for r in rep["records"]} <= {"win", "loss", "push", "unscorable"}
    assert rep["status"] == "retrospective" and rep["groups"]


def test_the_games_own_outcome_never_changes_its_reconstruction(store, monkeypatch):
    _train(store, monkeypatch, TRAIN_AT)
    run(store, CYCLE_AT)
    _settle(store, hs=4, vs=2)
    clone = sqlite3.connect(":memory:", isolation_level=None)
    clone.row_factory = sqlite3.Row
    store.backup(clone)
    clone.execute("UPDATE games SET home_score=0, away_score=9 WHERE game_id=?", (GID,))
    recovery.recover_adaptive(clone, now=AFTER)
    recovery.recover_adaptive(store, now=AFTER)
    vec = lambda c: {r["market"]: (r["p_a"], r["p_mid"], r["p_b"]) for r in
                     c.execute("SELECT * FROM adaptive_recovered_forecasts WHERE game_id=?", (GID,))}
    assert vec(store) and vec(clone) == vec(store)
    clone.close()


def test_fits_trained_on_results_after_the_cutoff_are_never_applied(store, monkeypatch):
    run(store, CYCLE_AT)
    _settle(store)                                       # this game's result available 03:00
    _later_game(store)
    _earlier_definition_in_force(store)
    _train(store, monkeypatch, AFTER)                    # first combiner trained on results through 03:00: after the cutoff
    out = recovery.recover_adaptive(store, now="2026-10-13T05:00:00Z")
    rows = _rows(store)
    assert out["created"] == 0 and out["unreconstructable"] == 6 and len(rows) == 6
    assert all(r["status"] == "unreconstructable" and r["p_a"] is None and "never applied" in r["reason"] for r in rows.values())
    assert recovery.recover_adaptive(store, now="2026-10-13T06:00:00Z")["unreconstructable"] == 0    # recorded once
    assert recovery.adaptive_report(store, "2026-10-13T06:00:00Z")["unreconstructable"][0]["rows"] == 6


def test_a_fit_informed_after_the_cutoff_is_refused_even_when_its_results_end_before(store, monkeypatch):
    run(store, CYCLE_AT)
    _later_game(store)
    _earlier_definition_in_force(store)
    _train(store, monkeypatch, "2026-10-13T00:30:00Z")   # after the start, before this game's result: results end earlier
    comb = store.execute("SELECT * FROM adaptive_fits WHERE kind='combiner' AND definition!='earlier-definition'").fetchone()
    assert comb["training_cutoff"] <= CUTOFF
    _settle(store)
    out = recovery.recover_adaptive(store, now=AFTER)
    rows = _rows(store)
    assert out["created"] == 0 and len(rows) == 6, out
    assert all(r["status"] == "unreconstructable" and "information time" in r["reason"] for r in rows.values())


def test_pending_training_and_missing_lines_defer_without_writing(store, monkeypatch):
    # No combiner for the current definition yet: wait (its training snapshot may still precede the game).
    run(store, CYCLE_AT)
    _settle(store)
    assert recovery.recover_adaptive(store, now=AFTER)["reason"] == "adaptive system not yet in force in this store"
    _earlier_definition_in_force(store)
    out = recovery.recover_adaptive(store, now=AFTER)
    assert out["created"] == out["unreconstructable"] == 0 and "training pending" in out["deferred"][GID]
    assert not _rows(store)


def test_a_fully_missed_game_waits_for_primary_recovery_then_uses_its_line(store, monkeypatch):
    _train(store, monkeypatch, TRAIN_AT)
    store.execute("INSERT INTO job_runs (job, started_at, finished_at, status) VALUES "
                  "('near_tipoff', '2026-10-12T20:45:00Z', '2026-10-12T20:45:00Z', 'success')")
    _settle(store)                                       # no primary or adaptive two-hour forecast was made at all
    first = recovery.recover_adaptive(store, now=AFTER)
    assert first["created"] == 0 and "awaiting a primary line" in first["deferred"][GID] and not _rows(store)
    assert recovery.recover(store, now=AFTER)["created"] == 6
    out = recovery.recover_adaptive(store, now=AFTER)
    rows = _rows(store)
    assert out["created"] == 6 and not out["errors"], out
    assert rows["spread"]["line_ref"].startswith("recovered_forecasts:") and rows["winner"]["line_ref"] is None
    rec = store.execute("SELECT line FROM recovered_forecasts WHERE game_id=? AND market='total'", (GID,)).fetchone()
    assert rows["total"]["line"] == rec["line"]
    assert all(r["quote_retrieved_at"] is None or r["quote_retrieved_at"] <= CUTOFF for r in rows.values())


def test_the_cycle_runs_live_work_then_primary_then_adaptive_recovery(monkeypatch):
    calls = []
    monkeypatch.setattr(jobs, "_forecast_due", lambda conn: calls.append("live") or {"status": "skipped"})
    monkeypatch.setattr(jobs, "recover_missed", lambda conn: calls.append("primary") or {"status": "success"})
    monkeypatch.setattr(recovery, "recover_adaptive", lambda conn, limit: calls.append(("adaptive", limit)) or {"created": 1})
    out = jobs.forecast_due(None)
    assert calls == ["live", "primary", ("adaptive", jobs.ADAPTIVE_RECOVERY_LIMIT)] and out["adaptive_recovery"]["created"] == 1
    monkeypatch.setattr(recovery, "recover_adaptive", lambda conn, limit: (_ for _ in ()).throw(RuntimeError("down")))
    assert jobs.forecast_due(None)["adaptive_recovery"]["status"] == "failed"


def test_replay_is_read_only_and_needs_a_pregame_cutoff(store):
    g = store.execute("SELECT * FROM games WHERE game_id=?", (GID,)).fetchone()
    for kw in ({"now": CUTOFF, "write": True}, {"now": TIPOFF, "write": False}, {"now": None, "write": False}):
        with pytest.raises(ValueError):
            adaptive.record(store, replay=g, **kw)


def test_a_later_refit_that_saw_the_result_is_skipped_for_the_earlier_fit(store, monkeypatch):
    first = _train(store, monkeypatch, TRAIN_AT)
    run(store, CYCLE_AT)
    expected = {f["market"]: f["p"] for f in adaptive.record(store, game_ids=[GID], now=CUTOFF, write=False)["forecasts"]}
    _settle(store)
    _later_game(store)
    monkeypatch.setattr(adaptive, "utc_now", lambda: AFTER)
    again = adaptive.train(store, now=AFTER, force=True)               # newer fits: results through 03:00 (this game)
    assert again["status"] in ("success", "partial"), again.get("errors")
    fits = store.execute("SELECT fit_id, training_cutoff FROM adaptive_fits WHERE kind='combiner' ORDER BY fit_id").fetchall()
    assert len(fits) == 2 and fits[0]["training_cutoff"] <= CUTOFF < fits[1]["training_cutoff"]
    out = recovery.recover_adaptive(store, now="2026-10-13T05:00:00Z")
    rows = _rows(store)
    assert out["created"] == 6, out
    assert {r["combiner_fit_id"] for r in rows.values()} == {fits[0]["fit_id"]}
    assert {m: [r["p_a"], r["p_mid"], r["p_b"]] for m, r in rows.items()} == expected
    assert all(x["training_cutoff"] <= CUTOFF for r in rows.values() for x in json.loads(r["members"]) if x["fit_id"])
    assert first["definition"] == out["definition"]


def test_a_fit_trained_before_the_game_but_published_after_its_start_is_used(store, monkeypatch):
    # The Oct 8 case: an earlier definition was in force; the new definition's training snapshot precedes the game but
    # its publication follows the start (the machine slept meanwhile).
    _earlier_definition_in_force(store)
    run(store, CYCLE_AT)
    monkeypatch.setattr(adaptive, "utc_now", lambda: "2026-10-13T02:27:00Z")
    t = adaptive.train(store, now=TRAIN_AT)
    assert t["status"] in ("success", "partial"), t.get("errors")
    live_now = adaptive.record(store, game_ids=[GID], now=CUTOFF, write=False)["forecasts"]
    assert live_now and all(f["status"] == "unavailable" for f in live_now)        # not in force at the cutoff live
    _settle(store)
    out = recovery.recover_adaptive(store, now=AFTER)
    rows = _rows(store)
    assert out["created"] == 6, out
    assert all(r["combiner_finished_at"] > TIPOFF and r["combiner_training_cutoff"] <= CUTOFF for r in rows.values())


def test_an_evaluation_error_records_nothing_and_is_retried(store, monkeypatch):
    _train(store, monkeypatch, TRAIN_AT)
    run(store, CYCLE_AT)
    _settle(store)
    from app.candidates import live as pool_live
    real = pool_live._recorder
    monkeypatch.setattr(pool_live, "_recorder", lambda *a: (_ for _ in ()).throw(FileNotFoundError("raw cache missing")))
    out = recovery.recover_adaptive(store, now=AFTER)
    assert out["status"] == "partial" and "raw cache missing" in json.dumps(out["errors"]) and not _rows(store)
    monkeypatch.setattr(pool_live, "_recorder", real)
    assert GID in recovery.recover_adaptive(store, now="2026-10-13T04:10:00Z")["backing_off"]     # waits out the backoff
    assert recovery.recover_adaptive(store, now="2026-10-13T04:31:00Z")["created"] == 6
    assert store.execute("SELECT count(*) FROM adaptive_recovery_attempts").fetchone()[0] == 0     # cleared once recorded


# --- Review of 8fda360 (Oct 10): transient failures stay retryable; no starvation; component information times ------
LATER = "2026-10-13T05:00:00Z"


def _flaky(monkeypatch, broken):
    real = adaptive.load_fit
    monkeypatch.setattr(adaptive, "load_fit", lambda row: (_ for _ in ()).throw(FileNotFoundError("volume not mounted"))
                        if broken(row) else real(row))
    return lambda: monkeypatch.setattr(adaptive, "load_fit", real)


@pytest.mark.parametrize("broken", [lambda row: row["kind"] == "combiner",
                                    lambda row: row["kind"] == "member" and row["name"].startswith("hockey_elo")])
def test_an_unreadable_artifact_records_nothing_and_is_retried(store, monkeypatch, broken):
    _train(store, monkeypatch, TRAIN_AT)
    run(store, CYCLE_AT)
    _settle(store)
    restore = _flaky(monkeypatch, broken)
    out = recovery.recover_adaptive(store, now=AFTER)
    assert out["status"] == "partial" and "ReplayUnavailable" in json.dumps(out["errors"]), out
    assert out["unreconstructable"] == 0 and not _rows(store)                 # never a permanent row, never a fallback
    restore()
    assert recovery.recover_adaptive(store, now=LATER)["created"] == 6
    assert {r["status"] for r in _rows(store).values()} == {"reconstructed"}


def test_a_candidate_evaluation_error_in_a_replay_is_retried(store, monkeypatch):
    from app.candidates import engine
    _train(store, monkeypatch, TRAIN_AT)
    run(store, CYCLE_AT)
    _settle(store)
    real = engine.outputs
    monkeypatch.setattr(engine, "outputs", lambda *a, **k: (_ for _ in ()).throw(MemoryError("transient")))
    assert any(m.kind == "candidate" for m in adaptive.members(store)[0])     # the fixture has candidate members
    out = recovery.recover_adaptive(store, now=AFTER)
    assert out["status"] == "partial" and "MemoryError" in json.dumps(out["errors"]), out
    assert not _rows(store)
    monkeypatch.setattr(engine, "outputs", real)
    assert recovery.recover_adaptive(store, now=LATER)["created"] == 6


def test_a_failing_game_rotates_backs_off_and_stops_without_blocking_later_games(store, monkeypatch):
    _train(store, monkeypatch, TRAIN_AT)
    run(store, CYCLE_AT)
    _settle(store)
    add_upcoming(store, gid=2026020002, start="2026-10-12T20:00:00", state=7, hs=1, vs=3, period=3, at="2026-10-13T04:00:00Z")
    monkeypatch.setattr(adaptive, "_primary_line", lambda *a, **k: {"line": 5.5})
    calls = []

    def rec(conn, **kw):
        gid = kw["replay"]["game_id"]
        calls.append(gid)
        if gid == GID:
            return {"status": "failed", "error": "RuntimeError: persistent", "errors": {}, "forecasts": [], "skipped": {}}
        return {"status": "success", "errors": {}, "forecasts": [], "skipped": {}}
    monkeypatch.setattr(adaptive, "record", rec)
    other = "nhl:2026020002"
    first_two = [recovery.recover_adaptive(store, now=t, limit=1) for t in ("2026-10-13T05:00:00Z", "2026-10-13T05:10:00Z")]
    assert len(calls) == 2 and set(calls) == {GID, other}                   # strict limit, and the later game got its turn
    assert GID in first_two[1]["backing_off"] or calls[0] == other
    # Bounded: backoff doubles and the game leaves automatic retries after MAX_ATTEMPTS.
    t = "2026-10-13T05:00:00Z"
    from datetime import timedelta
    from app import live
    for _ in range(recovery.MAX_ATTEMPTS + 3):
        t = live._ts(live._parse(t) + recovery.RETRY_CAP + timedelta(minutes=1))
        recovery.recover_adaptive(store, now=t, limit=2)
    assert calls.count(GID) == recovery.MAX_ATTEMPTS
    last = recovery.recover_adaptive(store, now=live._ts(live._parse(t) + recovery.RETRY_CAP * 2), limit=2)
    assert last["retries_exhausted"][GID]["attempts"] == recovery.MAX_ATTEMPTS and last["status"] == "partial"
    assert not _rows(store)                                                  # still nothing permanent in the ledger
    retrying = {r["game_id"]: r for r in recovery.adaptive_report(store, t)["retrying"]}
    assert retrying[GID]["exhausted"] and "persistent" in retrying[GID]["last_error"]


def test_a_candidate_artifact_without_a_spread_time_is_not_informed():
    art = {"kind": "candidate", "cutoff": "2025-11-10T05:00:00Z", "sd": (9.0, 1.0)}
    assert adaptive.information_at(art) is None and not adaptive._informed_by(art, "2030-01-01T00:00:00Z")
    art["components"] = {"model": "2025-11-10T05:00:00Z", "sd": "2026-10-22T03:00:00Z"}
    assert adaptive.information_at(art) == "2026-10-22T03:00:00Z"
    assert not adaptive._informed_by(art, "2026-10-21T23:29:59Z") and adaptive._informed_by(art, "2026-10-22T03:00:00Z")
    assert adaptive.information_at({"kind": "combiner", "cutoff": "2026-10-21T20:00:00Z"}) == "2026-10-21T20:00:00Z"


def _nba_kept_fit_store(tmp_path, monkeypatch, final):
    """NBA synthetic store (review reproduction B): a first training run, the primary two-hour forecasts for g1, then
    g1's result `final` and a second training run in which ridge cannot refit, so its model is kept while its residual
    spread is recomputed from results that include g1."""
    from app import backtest, db, market_live, near_tipoff, research_live
    from app.candidates import engine, registry
    from app.models import score_candidates
    from test_adaptive import _test_clock
    from test_backtest import synthetic_rows
    from test_live import T_GAME, event, ingest_events
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    conn = db.connect(tmp_path / "nba.sqlite")
    db.migrate(conn)
    history = [event(r.game.game_id, r.game.tipoff_at, r.game.home_team_id.split(":")[1], r.game.away_team_id.split(":")[1],
                     "STATUS_FINAL", r.home_score, r.away_score, r.game.season) for r in synthetic_rows(per_season=12)]
    ingest_events(conn, history, "2026-09-30T00:00:00Z", month="202601")
    val = backtest.evaluate(conn, "validation", out_dir=tmp_path / "val", log=lambda _: None)
    (tmp_path / "decision.json").write_text(json.dumps(backtest.decision_from_validation_report(val, "synthetic")))
    monkeypatch.setattr(backtest, "FROZEN_DECISION_PATH", tmp_path / "decision.json")
    db.claim_store(conn, "nba")
    monkeypatch.setattr(score_candidates, "MIN_EXAMPLES", 30)
    monkeypatch.setattr(engine, "MIN_RESIDUALS", 10)
    monkeypatch.setattr(research_live, "STACK_MIN_TRAIN", 20)
    monkeypatch.setattr(registry, "MIN_TRAIN", {"nba": 40, "nhl": 100})
    monkeypatch.setattr(registry, "SPECS", tuple(registry.BY_NAME[s] for s in ("logit_l2:nba_base", "ridge:nba_base")))
    monkeypatch.setattr(adaptive, "MIN_TRAIN", 20)
    monkeypatch.setattr(adaptive, "LEARNED", {**adaptive.LEARNED, "nba": ("logistic", "ridge_points")})
    ingest_events(conn, [event("g1", T_GAME, "1", "2", odds=("-150", "+130"))], "2026-10-19T12:00:00Z")
    clock = _test_clock("2026-10-21T20:00:00Z", "2026-10-21T22:00:00Z")
    monkeypatch.setattr(adaptive, "utc_now", clock)
    assert adaptive.run_scheduled("nba", now="2026-10-21T20:00:00Z")["status"] == "success"
    conn.execute("INSERT INTO job_runs (job, started_at, finished_at, status) VALUES ('refresh', '2026-10-21T22:00:00Z', "
                 "'2026-10-21T22:00:00Z', 'success')")
    ids = [f["game_id"] for f in near_tipoff.predict_now(conn, now="2026-10-21T22:00:00Z")["forecasts"]]
    market_live.forecast(conn, now="2026-10-21T22:00:00Z", policy="near_tipoff", game_ids=ids)   # adaptive step missed
    ingest_events(conn, [event("g1", T_GAME, "1", "2", "STATUS_FINAL", *final)], "2026-10-22T03:00:00Z")
    ingest_events(conn, [event("g2", "2026-10-28T23:30:00Z", "3", "1")], "2026-10-22T03:30:00Z")
    real = registry.available
    monkeypatch.setattr(registry, "available", lambda spec: False if spec.name == "ridge:nba_base" else real(spec))
    clock.at("2026-10-22T04:00:00Z", "2026-10-22T05:00:00Z")
    assert adaptive.run_scheduled("nba", now="2026-10-22T04:00:00Z")["status"] in ("success", "partial")
    monkeypatch.setattr(registry, "available", real)
    return conn, ids[0]


def test_a_games_outcome_cannot_reach_its_reconstruction_through_a_reused_model(tmp_path, monkeypatch):
    """Every artifact a replay may apply at the cutoff must be identical whatever the game's own result was; the kept
    ridge fit (model from before, spread recomputed after the result) is therefore not eligible."""
    import pickle
    cutoff = "2026-10-21T23:29:59Z"
    eligible, vectors = [], []
    for i, final in enumerate(((140, 80), (80, 140))):
        conn, gid = _nba_kept_fit_store(tmp_path / f"v{i}", monkeypatch, final)
        defn = adaptive.definition(conn)
        found = {}
        for row in conn.execute("SELECT * FROM adaptive_fits WHERE definition=? AND status IN ('success','partial') "
                                "AND training_cutoff<=? ORDER BY fit_id", (defn, cutoff)):
            art = adaptive.load_fit(row)
            if adaptive._informed_by(art, cutoff):
                content = {k: v for k, v in art.items() if k not in ("fitted_at",)}
                found[(row["kind"], row["name"], row["fit_id"])] = pickle.dumps(content.get("sd")) + pickle.dumps(
                    content.get("model", content.get("state", content.get("markets"))))
        kept = [r for r in conn.execute("SELECT * FROM adaptive_fits WHERE name LIKE 'cand:ridge%' ORDER BY fit_id")][-1]
        art = adaptive.load_fit(kept)
        assert art["components"]["sd"] > cutoff and not adaptive._informed_by(art, cutoff)   # the reused model's spread
        eligible.append(found)
        out = recovery.recover_adaptive(conn, now="2026-10-22T05:00:00Z")
        assert out["created"] >= 1 and not out["errors"], out
        rows = conn.execute("SELECT * FROM adaptive_recovered_forecasts WHERE game_id=? ORDER BY market", (gid,)).fetchall()
        assert all(x["information_at"] <= cutoff for r in rows for x in json.loads(r["members"]) if x["fit_id"])
        vectors.append({r["market"]: (r["status"], r["p_a"], r["p_mid"], r["p_b"]) for r in rows})
        conn.close()
    assert eligible[0] and eligible[0].keys() == eligible[1].keys()
    assert eligible[0] == eligible[1]                       # no eligible artifact depends on the game's own outcome
    assert vectors[0] and vectors[0] == vectors[1]


# --- Review of deac94a (Oct 10): an older combiner is loaded only when the newer one leaves a market uncombined ------
def _older_combiner(conn):
    """A copy of the trained combiner as an earlier fit (earlier training cutoff and publication, still after the adaptive
    era began); returns (newest, older) ids."""
    newest = conn.execute("SELECT fit_id FROM adaptive_fits WHERE kind='combiner' ORDER BY fit_id DESC").fetchone()[0]
    cols = [r[1] for r in conn.execute("PRAGMA table_info(adaptive_fits)") if r[1] != "fit_id"]
    over = {"training_cutoff": "'2026-01-15T00:00:00Z'", "started_at": "'2026-10-12T19:00:00Z'",
            "finished_at": "'2026-10-12T20:00:00Z'", "idempotency_key": "'older-combiner'"}
    conn.execute(f"INSERT INTO adaptive_fits ({','.join(cols)}) SELECT {','.join(over.get(c, c) for c in cols)} "
                 f"FROM adaptive_fits WHERE fit_id=?", (newest,))
    older = conn.execute("SELECT max(fit_id) FROM adaptive_fits").fetchone()[0]
    cut = dict(conn.execute("SELECT fit_id, training_cutoff FROM adaptive_fits WHERE fit_id IN (?, ?)", (newest, older)).fetchall())
    assert cut[older] < cut[newest]                     # older by training cutoff, the replay's ordering
    return newest, older


def _combiners(monkeypatch, newest, older, *, drop=None, older_readable=True):
    """load_fit with the newest combiner lacking `drop`'s combination and the older one optionally unreadable."""
    import copy
    real = getattr(adaptive.load_fit, "real", adaptive.load_fit)     # re-patching replaces, never wraps, the stub

    def load(row):
        if row["fit_id"] == older and not older_readable:
            raise FileNotFoundError("older artifact pruned")
        art = real(row)
        if row["fit_id"] == newest and drop:
            art = copy.deepcopy(art)
            art["markets"][drop]["stack"] = None
        return art
    load.real = real
    monkeypatch.setattr(adaptive, "load_fit", load)


def test_a_complete_newest_combiner_is_used_despite_an_unreadable_older_artifact(store, monkeypatch):
    _train(store, monkeypatch, TRAIN_AT)
    run(store, CYCLE_AT)
    _settle(store)
    newest, older = _older_combiner(store)
    _combiners(monkeypatch, newest, older, older_readable=False)
    out = recovery.recover_adaptive(store, now=AFTER)
    assert out["created"] == 6 and not out["errors"], out
    assert {r["combiner_fit_id"] for r in _rows(store).values()} == {newest}


def test_an_incomplete_newest_combiner_takes_the_missing_market_from_an_older_fit(store, monkeypatch):
    _train(store, monkeypatch, TRAIN_AT)
    run(store, CYCLE_AT)
    _settle(store)
    newest, older = _older_combiner(store)
    _combiners(monkeypatch, newest, older, drop="total")
    out = recovery.recover_adaptive(store, now=AFTER)
    assert out["created"] == 6 and not out["errors"], out
    used = {m: r["combiner_fit_id"] for m, r in _rows(store).items()}
    assert used.pop("total") == older and set(used.values()) == {newest}


def test_an_unreadable_older_fit_needed_for_a_missing_market_is_retried_and_records_nothing(store, monkeypatch):
    _train(store, monkeypatch, TRAIN_AT)
    run(store, CYCLE_AT)
    _settle(store)
    newest, older = _older_combiner(store)
    _combiners(monkeypatch, newest, older, drop="total", older_readable=False)
    out = recovery.recover_adaptive(store, now=AFTER)
    assert out["status"] == "partial" and "ReplayUnavailable" in json.dumps(out["errors"]), out
    assert out["unreconstructable"] == 0 and not _rows(store)
    assert store.execute("SELECT attempts FROM adaptive_recovery_attempts WHERE game_id=?", (GID,)).fetchone()[0] == 1
    _combiners(monkeypatch, newest, older, drop="total")                    # the artifact is readable again
    again = recovery.recover_adaptive(store, now=LATER)
    assert again["created"] == 6 and not again["errors"], again
