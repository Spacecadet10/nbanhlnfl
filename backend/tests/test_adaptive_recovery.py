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
    assert recovery.recover_adaptive(store, now=AFTER)["created"] == 6
