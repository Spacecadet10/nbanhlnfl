"""Every-game coverage (app/coverage.py, docs/COVERAGE.md): a recorded source or a recorded reason for every scheduled game
and market (preseason included), fallback provenance, grading of every recorded forecast regardless of confidence, and
the coverage report. Synthetic NHL store, no network."""
import json

import pytest

from app import adaptive, coverage, ingest
from test_adaptive import CYCLE_AT, TRAIN_AT, store  # noqa: F401  (fixture)
from test_live_markets import add_upcoming, sealed  # noqa: F401  (fixture)
from test_nhl_eval import nhl_store  # noqa: F401  (fixture)
from test_research_live import GID, game, run  # noqa: F401  (fixture)

PRE = "nhl:2026010099"
MARKETS = {"winner", "reg3", "spread", "total", "team_total_home", "team_total_away"}


def _preseason(c, state=1, hs=0, vs=0, period=1, at="2026-10-12T20:30:00Z", start="2026-10-12T19:30:00", gid=2026010099):
    body = json.dumps({"data": [{"id": gid, "easternStartTime": start, "gameScheduleStateId": 1, "gameStateId": state,
                                 "gameType": 1, "homeScore": hs, "homeTeamId": 8, "period": period, "season": 20262027,
                                 "visitingScore": vs, "visitingTeamId": 16}]}).encode()
    ingest.ingest_nhl_season(c, 2027, fetch=lambda _s: body, retrieved_at=at)


def _rows(conn, gid):
    return {r["market"]: r for r in conn.execute("SELECT * FROM game_forecasts WHERE game_id=? ORDER BY forecast_id", (gid,))}


def test_a_preseason_game_gets_every_market_from_the_sealed_primary_with_its_provenance(game):
    """Preseason games were never forecast (one season-type list drives training and forecasting). They are now recorded
    for every market by the sealed primary computed for this ledger, with the reason the ensemble was not used."""
    _preseason(game)
    assert game.execute("SELECT season_type FROM games WHERE game_id=?", (PRE,)).fetchone()[0] == "preseason"
    out = coverage.record(game, now=CYCLE_AT)
    assert out["status"] == "success", out["errors"]
    rows = _rows(game, PRE)
    assert set(rows) == MARKETS
    for m, r in rows.items():
        assert r["status"] == "forecast" and r["source"] == "primary_direct" and r["fallback_rank"] == 3
        assert r["season_type"] == "preseason" and "preseason" in r["fallback_reason"]
        assert abs(r["p_a"] + r["p_mid"] + r["p_b"] - 1) < 1e-6
        assert (0 < r["confidence"] <= 1) if m == "reg3" else (0.5 <= r["confidence"] <= 1)
    assert rows["winner"]["line_source"] == "none" and rows["reg3"]["line"] is None
    assert rows["team_total_home"]["line_source"] == "model"          # no bookmaker quote for this game: a model line
    # The preseason game never enters the primary ledgers or training (the competitive types only).
    assert not game.execute("SELECT 1 FROM predictions WHERE game_id=?", (PRE,)).fetchone()
    assert not game.execute("SELECT 1 FROM market_forecasts WHERE game_id=?", (PRE,)).fetchone()


def test_the_recorded_primary_is_the_fallback_when_no_adaptive_forecast_exists_and_bookmaker_lines_are_kept(game):
    run(game, CYCLE_AT)                                       # primary two-hour forecasts (no adaptive fit in this store)
    coverage.record(game, now=CYCLE_AT)
    rows = _rows(game, GID)
    assert set(rows) == MARKETS
    assert all(r["source"] == "primary_recorded" and r["fallback_rank"] == 2 for r in rows.values())
    assert all(r["fallback_reason"].startswith("adaptive:") for r in rows.values())
    w = rows["winner"]
    src = game.execute("SELECT * FROM predictions WHERE prediction_id=?", (w["source_id"],)).fetchone()
    assert w["source_table"] == "predictions" and w["p_a"] == src["home_win_prob"] and w["model_version"] == src["model_version"]
    assert rows["spread"]["line"] == -1.5 and rows["spread"]["line_source"] == "bookmaker"   # the quoted line
    assert rows["total"]["line"] == 6.5 and rows["total"]["line_source"] == "bookmaker"
    assert rows["team_total_home"]["line_source"] == "model"


def test_an_adaptive_forecast_is_research_only_never_the_canonical_source(store):
    """Product decision 2026-10-14 (coverage-v2): no adaptive definition is approved for canonical use, so an adaptive
    forecast row is not used; the sealed primary is, with the rejection on record. (Was: adaptive is the main source.)"""
    adaptive.train(store, now=TRAIN_AT)
    run(store, CYCLE_AT)
    adaptive.record(store, game_ids=[GID], now=CYCLE_AT)
    coverage.record(store, now=CYCLE_AT)
    rows = _rows(store, GID)
    assert set(rows) == MARKETS
    defn = adaptive.definition(store)
    for m, r in rows.items():
        a = store.execute("SELECT * FROM adaptive_forecasts WHERE game_id=? AND market=? AND status='forecast'", (GID, m)).fetchone()
        assert a is not None                                          # the ensemble did forecast (kept as research)
        assert r["source"] == "primary_recorded" and r["fallback_rank"] == 2 and r["order_version"] == "coverage-v2"
        assert f"definition {defn}" in r["fallback_reason"] and "not approved" in r["fallback_reason"]


def test_when_every_advanced_member_abstains_the_sealed_primary_covers_the_game_with_the_reason(store, monkeypatch):
    adaptive.train(store, now=TRAIN_AT)
    run(store, CYCLE_AT)
    monkeypatch.setattr(adaptive, "combine_now", lambda cm, have: (None, None, [], "missing members a, b: no combination trained for this subset"))
    adaptive.record(store, game_ids=[GID], now=CYCLE_AT)
    assert store.execute("SELECT count(*) FROM adaptive_forecasts WHERE status='unavailable'").fetchone()[0] == 6
    coverage.record(store, now=CYCLE_AT)
    rows = _rows(store, GID)
    assert all(r["source"] == "primary_recorded" and "no combination trained for this subset" in r["fallback_reason"]
               for r in rows.values())


def test_stale_results_are_never_bypassed_a_missing_row_records_why(game):
    _preseason(game)
    late = "2026-10-12T22:30:00Z"                              # the last results refresh (21:00) is 90 minutes old
    out = coverage.record(game, now=late)
    rows = _rows(game, PRE)
    assert set(rows) == MARKETS and out["missing"] >= 6
    for r in rows.values():
        assert r["status"] == "missing" and r["source"] == "none" and r["p_a"] is None
        assert "stale results" in r["reason"] and "adaptive:" in r["reason"]


def test_recording_is_idempotent_and_isolated_per_game_and_market(game, monkeypatch):
    _preseason(game)
    first = coverage.record(game, now=CYCLE_AT)
    again = coverage.record(game, now=CYCLE_AT)
    assert first["created"] == 12 and again["created"] == 0 and again["already_recorded"] == 12
    assert game.execute("SELECT count(*) FROM game_forecasts").fetchone()[0] == 12
    real = coverage._Direct.forecast

    def broken_total(self, g, market):
        if market == "total" and g["game_id"] == PRE:
            raise RuntimeError("synthetic failure")
        return real(self, g, market)
    monkeypatch.setattr(coverage._Direct, "forecast", broken_total)
    later = "2026-10-12T21:31:00Z"
    out = coverage.record(game, now=later)
    assert out["status"] == "partial" and "RuntimeError: synthetic failure" in out["errors"][f"{PRE}:total"]
    latest = game.execute("SELECT * FROM game_forecasts WHERE game_id=? AND market='total' ORDER BY forecast_id DESC LIMIT 1",
                          (PRE,)).fetchone()
    assert latest["status"] == "missing" and "synthetic failure" in latest["reason"]
    assert all(r["status"] == "forecast" for m, r in _rows(game, GID).items())     # the other game and markets still recorded
    with pytest.raises(Exception, match="append-only"):
        game.execute("UPDATE game_forecasts SET p_a=0.5")


def test_rows_after_the_start_are_refused_by_the_ledger(game):
    _preseason(game)
    coverage.record(game, now=CYCLE_AT)
    row = dict(game.execute("SELECT * FROM game_forecasts LIMIT 1").fetchone())
    row.update(forecast_id=None, generated_at=row["tipoff_at"], idempotency_key="x")
    cols = [k for k in row if k != "forecast_id"]
    with pytest.raises(Exception, match="within two hours before the start"):
        game.execute(f"INSERT INTO game_forecasts ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [row[c] for c in cols])


def _settle(c, hs=4, vs=1, at="2026-10-13T03:00:00Z"):
    add_upcoming(c, start="2026-10-12T19:00:00", state=7, hs=hs, vs=vs, period=3, at=at)     # a regulation final


def test_every_recorded_forecast_is_graded_whatever_its_confidence_and_thresholds_only_filter(game):
    run(game, CYCLE_AT)
    coverage.record(game, now=CYCLE_AT)
    _settle(game)
    rep = coverage.games_report(game, start="2026-10-12T00:00:00Z", end="2026-10-14T00:00:00Z", as_of="2026-10-13T04:00:00Z",
                                with_source=False)
    g = next(x for x in rep["games"] if x["game_id"] == GID)
    states = {c["market"]: c["status"] for c in g["markets"]}
    assert set(states.values()) <= {"win", "loss", "push", "unscorable"} and "pending" not in states.values()
    all_rec = rep["record"]["all"]
    assert all_rec["forecasts"] == 6 and all_rec["graded"] + all_rec["unscorable"] == 6
    assert rep["record"]["fallback"]["forecasts"] == 6 and rep["record"]["main"]["forecasts"] == 0
    # Each market on its own grid (approved 2026-10-14): two-way 50-90%, regulation three-way 35-70%; never pooled.
    grids = {m: [t["threshold"] for t in rows] for m, rows in rep["thresholds_by_market"].items()}
    assert grids["winner"] == grids["total"] == [0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9]
    assert grids["reg3"] == [0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7]
    for m, rows in rep["thresholds_by_market"].items():            # a threshold only filters; the lowest keeps the market's forecasts
        assert rows[0]["all"]["forecasts"] <= 1 and all(a["all"]["forecasts"] >= b["all"]["forecasts"] for a, b in zip(rows, rows[1:]))
    assert sum(rows[0]["all"]["forecasts"] for rows in rep["thresholds_by_market"].values()) <= all_rec["forecasts"]
    low = [c for c in g["markets"] if c["confidence"] < 0.9]
    assert low and all(c["status"] != "missing" for c in low)        # below-threshold forecasts are recorded and graded


def test_a_corrected_result_regrades_once_without_new_rows(game):
    run(game, CYCLE_AT)
    coverage.record(game, now=CYCLE_AT)
    _settle(game, hs=4, vs=1)
    before = game.execute("SELECT count(*) FROM game_forecasts").fetchone()[0]
    args = dict(start="2026-10-12T00:00:00Z", end="2026-10-14T00:00:00Z", as_of="2026-10-13T06:00:00Z", with_source=False)
    first = coverage.games_report(game, **args)
    _settle(game, hs=1, vs=4, at="2026-10-13T05:00:00Z")            # the source corrects the final score
    second = coverage.games_report(game, **args)
    again = coverage.games_report(game, **args)
    w1 = next(c for c in first["games"][0]["markets"] if c["market"] == "winner")["status"]
    w2 = next(c for c in second["games"][0]["markets"] if c["market"] == "winner")["status"]
    assert {w1, w2} == {"win", "loss"} and second["schedule"]["corrected_results"] == 1
    assert second["record"] == again["record"]                       # re-reading never grades twice
    assert game.execute("SELECT count(*) FROM game_forecasts").fetchone()[0] == before


def test_preseason_is_graded_apart_from_the_competitive_record(game):
    _preseason(game)
    run(game, CYCLE_AT)
    coverage.record(game, now=CYCLE_AT)
    _preseason(game, state=7, hs=3, vs=2, period=3, at="2026-10-13T02:00:00Z")
    rep = coverage.games_report(game, start="2026-10-12T00:00:00Z", end="2026-10-14T00:00:00Z", as_of="2026-10-13T04:00:00Z",
                                with_source=False)
    assert rep["preseason_record"]["forecasts"] == 6 and rep["preseason_record"]["graded"] >= 5
    assert rep["record"]["all"]["forecasts"] == 6                    # the regular game only
    assert rep["sources"]["preseason"] == {"primary_direct": 6}


def test_an_unresolved_game_is_not_pending_forever(game):
    run(game, CYCLE_AT)
    coverage.record(game, now=CYCLE_AT)
    rep = coverage.games_report(game, start="2026-10-12T00:00:00Z", end="2026-10-14T00:00:00Z", as_of="2026-10-15T00:00:00Z",
                                with_source=False)
    assert {c["status"] for c in rep["games"][0]["markets"]} == {"unresolved"}   # still 'scheduled' 49 h after the start


def test_missing_cells_carry_their_reason_and_fallback_rows_never_reach_ensemble_training(game):
    run(game, CYCLE_AT)
    coverage.record(game, now=CYCLE_AT)
    _settle(game)
    rep = coverage.games_report(game, start="2026-10-01T00:00:00Z", end="2026-10-14T00:00:00Z", as_of="2026-10-13T04:00:00Z",
                                with_source=False)
    assert all(m["reason"] for m in rep["missing_reasons"])
    assert adaptive.prospective_rows(game, "nhl", "any", "2026-10-13T04:00:00Z") == []   # only adaptive_forecasts feed it


def test_a_start_time_edit_after_the_final_does_not_void_the_graded_forecasts(game):
    """A source that edits a final game's start time (seconds or minutes) used to move tipoff_at, which voided every
    forecast already graded for the game played at the original start."""
    run(game, CYCLE_AT)
    coverage.record(game, now=CYCLE_AT)
    _settle(game)
    add_upcoming(game, start="2026-10-12T19:07:00", state=7, hs=4, vs=1, period=3, at="2026-10-13T08:00:00Z")
    assert game.execute("SELECT tipoff_at FROM games WHERE game_id=?", (GID,)).fetchone()[0] == "2026-10-12T23:00:00Z"
    rep = coverage.games_report(game, start="2026-10-12T00:00:00Z", end="2026-10-14T00:00:00Z", as_of="2026-10-13T09:00:00Z",
                                with_source=False)
    assert "void" not in {c["status"] for c in rep["games"][0]["markets"]}


def test_the_coverage_api_reports_the_window_against_the_source_schedule(game, monkeypatch):
    from fastapi.testclient import TestClient
    from app import db, main
    from app.providers.real import RealProvider
    _preseason(game)
    run(game, CYCLE_AT)
    coverage.record(game, now=CYCLE_AT)
    game.commit()
    store = game.execute("PRAGMA database_list").fetchone()["file"]

    def provider():
        conn = db.connect(store, check_same_thread=False)
        try:
            yield RealProvider(conn, now="2026-10-13T12:00:00Z")
        finally:
            conn.close()
    monkeypatch.setitem(main.app.dependency_overrides, main.get_provider, provider)
    c = TestClient(main.app, raise_server_exceptions=False)
    r = c.get("/api/coverage", params={"sport": "nhl", "date_from": "2026-10-12", "date_to": "2026-10-12"})
    assert r.status_code == 200, r.text
    body = r.json()
    # The preseason game has no canonical forecast in any market (no validated model): unvalidated, not "fully forecast".
    assert body["schedule"]["games"] == 2 and body["schedule"]["games_fully_forecast"] == 1
    assert body["schedule"]["games_unvalidated"] == 1
    assert body["sources"]["preseason"] == {"primary_direct": 6} and body["sources"]["competitive"] == {"primary_recorded": 6}
    assert body["source_schedule"]["available"] is True and body["source_schedule"]["missing_from_store"] == []
    assert any(x["ledger"] == "game_forecasts" for x in body["ledgers"])
    assert c.get("/api/coverage", params={"sport": "nhl", "date_from": "2026-10-13", "date_to": "2026-10-12"}).status_code == 422
    assert c.get("/api/coverage", params={"sport": "nhl", "date_from": "2026-08-01", "date_to": "2026-10-12"}).status_code == 422


def _no_network(monkeypatch):
    from app import jobs
    for step in ("refresh", "odds_snapshot", "nhl_pbp", "boxscores"):
        monkeypatch.setattr(jobs, step, lambda *a, **k: {"status": "skipped", "reason": "test"})
    return jobs


def test_a_window_with_only_a_preseason_game_opens_the_cycle_and_the_cycle_records_coverage(game, monkeypatch):
    jobs = _no_network(monkeypatch)
    add_upcoming(game, start="2026-10-12T19:00:00", sched=5, at="2026-10-12T21:00:00Z")      # postponed: only preseason left
    _preseason(game)
    called = {}
    monkeypatch.setattr(jobs, "predict_now", lambda conn, **kw: called.setdefault("ran", {"status": "ok"}))
    monkeypatch.setattr(jobs, "utc_now", lambda: CYCLE_AT)
    assert jobs._forecast_due(game)["status"] == "ok" and called      # it used to need a regular/play-in/playoff game


def test_the_two_hour_cycle_records_every_game_even_when_the_primary_step_fails(game, monkeypatch):
    jobs = _no_network(monkeypatch)
    _preseason(game)
    out = jobs.predict_now(game, now=CYCLE_AT)
    assert out["coverage"]["games"] == 2 and out["coverage"]["created"] == 12
    from app import near_tipoff
    monkeypatch.setattr(near_tipoff, "predict_now", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("synthetic")))
    failed = jobs.predict_now(game, now="2026-10-12T21:40:00Z")
    assert failed["status"] == "failed" and failed["coverage"]["games"] == 2


def test_a_game_that_has_not_started_is_upcoming_not_missing(game):
    rep = coverage.games_report(game, start="2026-10-12T00:00:00Z", end="2026-10-14T00:00:00Z", as_of="2026-10-12T12:00:00Z",
                                with_source=False)
    assert {c["status"] for c in rep["games"][0]["markets"]} == {"upcoming"}
    assert rep["missing_reasons"] == [] and rep["schedule"]["upcoming_games"] == 1 and rep["by_market"]["winner"]["upcoming"] == 1


def test_a_daily_unavailable_row_never_hides_the_weekly_ensembles_forecast(store):
    """Reviewer recheck C1 (2026-10-13): with daily agreement recording on, the daily definition records after the weekly
    one in the same cycle; coverage took the single newest adaptive row, so a daily 'unavailable' row hid the weekly
    forecast (and a daily forecast would have become 'main' by write order)."""
    adaptive.train(store, now=TRAIN_AT)                                   # weekly fit only; no daily fit exists
    run(store, CYCLE_AT)
    assert adaptive.record(store, game_ids=[GID], now=CYCLE_AT)["created"] == 6
    with adaptive.cadence("daily"):
        assert adaptive.record(store, game_ids=[GID], now=CYCLE_AT)["unavailable"] == 6
    coverage.record(store, now=CYCLE_AT)
    rows = _rows(store, GID)
    weekly = adaptive.definition(store)
    # coverage-v2: the weekly forecast is still seen (named, not hidden by the daily row) but not approved, so the sealed
    # primary is used. (Was: the weekly forecast was the main source.)
    assert all(r["source"] == "primary_recorded" and f"definition {weekly} (weekly schedule) forecast, not approved"
               in r["fallback_reason"] for r in rows.values())


# --- coverage-v2 (2026-10-14): no adaptive definition is approved; an adaptive row, fit or retrain is not approval -------
def _ledger(conn, table):
    return [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]


def test_neither_the_weekly_nor_the_daily_adaptive_definition_is_approved_and_their_rows_stay_unchanged(store):
    adaptive.train(store, now=TRAIN_AT)
    with adaptive.cadence("daily"):
        adaptive.train(store, now=TRAIN_AT)
    run(store, CYCLE_AT)
    assert adaptive.record(store, game_ids=[GID], now=CYCLE_AT)["created"] > 0
    with adaptive.cadence("daily"):
        assert adaptive.record(store, game_ids=[GID], now=CYCLE_AT)["created"] > 0
        daily = adaptive.definition(store)
    weekly = adaptive.definition(store)
    before = _ledger(store, "adaptive_forecasts")
    assert {r["definition"] for r in before if r["status"] == "forecast"} == {weekly, daily}
    out = coverage.record(store, now=CYCLE_AT)
    assert out["order_version"] == "coverage-v2" and out["by_source"] == {"primary_recorded": 6}
    for r in _rows(store, GID).values():
        assert r["source"] == "primary_recorded"
        assert f"definition {weekly} (weekly schedule) forecast, not approved" in r["fallback_reason"]
        assert f"definition {daily} (daily schedule) forecast, not approved" in r["fallback_reason"]
    assert _ledger(store, "adaptive_forecasts") == before                # research rows untouched


def test_an_adaptive_forecast_without_an_eligible_baseline_is_missing_not_adaptive(store, monkeypatch):
    adaptive.train(store, now=TRAIN_AT)
    run(store, CYCLE_AT)
    adaptive.record(store, game_ids=[GID], now=CYCLE_AT)
    monkeypatch.setattr(coverage, "_primary_recorded", lambda *a: (None, "primary: no recorded two-hour forecast"))
    monkeypatch.setattr(coverage._Direct, "forecast", lambda self, g, m: (None, "primary: stale results: synthetic"))
    out = coverage.record(store, now=CYCLE_AT)
    assert out["missing"] == 6 and out["forecast"] == 0
    for r in _rows(store, GID).values():
        assert r["status"] == "missing" and r["source"] == "none" and r["p_a"] is None
        assert "not approved" in r["reason"] and "stale results: synthetic" in r["reason"]


def test_the_policy_revision_appends_once_keeps_earlier_rows_and_is_never_written_after_the_start(store, monkeypatch):
    adaptive.train(store, now=TRAIN_AT)
    run(store, CYCLE_AT)
    adaptive.record(store, game_ids=[GID], now=CYCLE_AT)
    with monkeypatch.context() as m:                    # what the coverage-v1 writer recorded (adaptive taken without approval)
        m.setattr(coverage, "ORDER_VERSION", "coverage-v1")
        m.setattr(coverage, "_adaptive_rejected", lambda row, schedule: None)
        assert coverage.record(store, now=CYCLE_AT)["by_source"] == {"adaptive": 6}
    v1 = _ledger(store, "game_forecasts")
    later = "2026-10-12T21:31:00Z"
    first = coverage.record(store, now=later)
    assert first["created"] == 6 and first["by_source"] == {"primary_recorded": 6}
    for t in (later, "2026-10-12T21:45:00Z"):          # retries with unchanged inputs: no duplicate revision
        again = coverage.record(store, now=t)
        assert again["created"] == 0 and again["already_recorded"] == 6
    rows = _ledger(store, "game_forecasts")
    assert rows[:6] == v1 and len(rows) == 12           # the earlier rows are unchanged; one revision per market
    g = store.execute("SELECT * FROM games WHERE game_id=?", (GID,)).fetchone()
    canon = coverage.canonical(store, [g], "winner", "2026-10-12T22:00:00Z")[GID]
    assert canon["record"]["source"] == "primary_recorded" and canon["record"]["forecast_id"] == rows[6 + [r["market"] for r in rows[6:]].index("winner")]["forecast_id"]
    after = coverage.record(store, now="2026-10-12T23:05:00Z")                     # the game has started: nothing new
    assert after["games"] == 0 and len(_ledger(store, "game_forecasts")) == 12


def test_preseason_stays_outside_the_ensemble_and_the_accepted_record(store):
    adaptive.train(store, now=TRAIN_AT)
    run(store, CYCLE_AT)
    adaptive.record(store, game_ids=[GID], now=CYCLE_AT)
    _preseason(store)
    coverage.record(store, now=CYCLE_AT)
    for r in _rows(store, PRE).values():
        assert r["source"] != "adaptive" and r["season_type"] == "preseason"
        assert (r["fallback_reason"] or r["reason"]).startswith("adaptive: preseason is outside the ensemble's scope")
    rep = coverage.games_report(store, start="2026-10-12T00:00:00Z", end="2026-10-14T00:00:00Z", as_of=CYCLE_AT,
                                with_source=False)
    pre = next(g for g in rep["games"] if g["game_id"] == PRE)
    # Preseason is unvalidated in every market (canonical selection); a stored row is experimental only.
    assert all(c["status"] == "unvalidated" and c.get("experimental", {}).get("label", "Experimental / unvalidated") ==
               "Experimental / unvalidated" for c in pre["markets"])
    assert rep["record"]["main"]["forecasts"] == 0


def test_the_report_never_counts_an_unapproved_adaptive_row_from_before_every_game_recording(store):
    """Games before the every-game ledger are read from the existing ledgers in the same order: an unapproved adaptive row is
    not the game's forecast there either (it was 'main' in the accepted record)."""
    adaptive.train(store, now=TRAIN_AT)
    run(store, CYCLE_AT)
    adaptive.record(store, game_ids=[GID], now=CYCLE_AT)
    assert store.execute("SELECT count(*) FROM game_forecasts").fetchone()[0] == 0     # before every-game recording
    _settle(store)
    rep = coverage.games_report(store, start="2026-10-12T00:00:00Z", end="2026-10-14T00:00:00Z", as_of="2026-10-13T04:00:00Z",
                                with_source=False)
    g = next(x for x in rep["games"] if x["game_id"] == GID)
    assert {c["source"] for c in g["markets"]} == {"primary_recorded"}
    assert all(c["recorded_in"] == "existing ledgers" for c in g["markets"]) and rep["record"]["main"]["forecasts"] == 0


# --- Settlement A: the Coverage card grades a pre-ledger night-before forecast by the same void rule as its History ----------
def _night_before(c, moved_start):
    """A game forecast the night before (winner by the night-before primary, the other markets by the pregame market
    model), then moved to `moved_start` before it is played and settled. No every-game row exists (pre-ledger game)."""
    from app import live, market_live
    add_upcoming(c, start="2026-10-11T19:00:00", at="2026-10-10T12:00:00Z")
    live.predict(c, now="2026-10-10T15:00:00Z")
    market_live.forecast(c, now="2026-10-10T15:00:00Z")
    add_upcoming(c, start=moved_start, at="2026-10-11T12:00:00Z")
    add_upcoming(c, start=moved_start, state=7, hs=4, vs=2, period=3, at="2026-10-13T03:00:00Z")
    rep = coverage.games_report(c, start="2026-10-10T00:00:00Z", end="2026-10-14T00:00:00Z", as_of="2026-10-13T05:00:00Z",
                                with_source=False, with_ledgers=False)
    cells = {x["market"]: x for x in rep["games"][0]["markets"]}
    from app.providers.real import RealProvider
    p = RealProvider(c, now="2026-10-13T05:00:00Z")
    hist = {m: p.market_predictions(m, "pregame", None, None, None, 50, None).predictions for m in MARKETS - {"winner"}}
    winner = p.predictions("live", None, None, None, 50, None, policy="pregame").predictions
    return cells, hist, winner


def test_a_night_before_market_forecast_whose_start_moved_the_same_day_is_void_as_in_its_history(sealed):
    """Same row: start moved +60 min on the same Eastern date. Market History voids it (markets.settle: any start change);
    the Coverage card passed no forecast start for night-before rows and graded it as a win or loss."""
    cells, hist, winner = _night_before(sealed, "2026-10-11T20:00:00")
    for m, rows in hist.items():
        assert len(rows) == 1 and rows[0].forecast.result == "void", m
        assert cells[m]["status"] == "void", (m, cells[m]["status"])
    # The night-before winner rule is the Eastern date (live.day_changed): same date, so graded in both views.
    assert len(winner) == 1 and winner[0].void is False and winner[0].correct is not None
    assert cells["winner"]["status"] == ("win" if winner[0].correct else "loss")


def test_a_night_before_forecast_whose_game_moved_to_another_date_is_void_not_missing(sealed):
    """A game moved to another Eastern date: History shows the night-before forecast as void; the Coverage card looked only
    for a row with the new date's cutoff and reported 'no pregame forecast in any ledger'."""
    cells, hist, winner = _night_before(sealed, "2026-10-12T19:00:00")
    assert len(winner) == 1 and winner[0].void is True
    for m, rows in hist.items():
        assert len(rows) == 1 and rows[0].forecast.result == "void", m
    assert {m: c["status"] for m, c in cells.items()} == {m: "void" for m in MARKETS}


def test_a_game_reforecast_for_its_new_date_is_graded_from_the_new_forecast_in_both_views(sealed):
    """Latest-row selection after a move: the night-before forecast for the new date replaces the void one, in Coverage as
    in History (independent review, settlement A)."""
    from app import live, market_live
    from app.providers.real import RealProvider
    add_upcoming(sealed, start="2026-10-11T19:00:00", at="2026-10-10T12:00:00Z")
    live.predict(sealed, now="2026-10-10T15:00:00Z")
    market_live.forecast(sealed, now="2026-10-10T15:00:00Z")
    add_upcoming(sealed, start="2026-10-12T19:00:00", at="2026-10-11T12:00:00Z")
    live.predict(sealed, now="2026-10-11T15:00:00Z")
    market_live.forecast(sealed, now="2026-10-11T15:00:00Z")
    add_upcoming(sealed, start="2026-10-12T19:00:00", state=7, hs=4, vs=2, period=3, at="2026-10-13T03:00:00Z")
    rep = coverage.games_report(sealed, start="2026-10-10T00:00:00Z", end="2026-10-14T00:00:00Z", as_of="2026-10-13T05:00:00Z",
                                with_source=False, with_ledgers=False)
    cells = {x["market"]: x["status"] for x in rep["games"][0]["markets"]}
    p = RealProvider(sealed, now="2026-10-13T05:00:00Z")
    hist = {m: p.market_predictions(m, "pregame", None, None, None, 50, None).predictions[0].forecast.result
            for m in MARKETS - {"winner"}}
    (w,) = p.predictions("live", None, None, None, 50, None, policy="pregame").predictions
    hist["winner"] = "void" if w.void else "win" if w.correct else "loss"
    assert cells == hist and "void" not in cells.values()


def test_a_slow_research_step_after_the_start_does_not_lose_the_games_canonical_forecast(game, monkeypatch):
    """Nov 10 investigation: on Oct 28 run 1886 wrote the primary forecasts at 01:53Z, but coverage.record ran only at
    02:34Z after the research/pool/tracking steps, when the games had started, and recorded nothing for them (3 NHL games
    lost). The canonical record must be taken right after the primary and market steps, before the research work."""
    from app import db, near_tipoff
    jobs = _no_network(monkeypatch)
    clock = {"now": CYCLE_AT}
    for module in (db, near_tipoff, jobs):
        if hasattr(module, "utc_now"):
            monkeypatch.setattr(module, "utc_now", lambda: clock["now"])
    real = jobs.research_forecasts

    def slow_research(*a, **k):
        clock["now"] = "2026-10-12T23:59:00Z"          # the research steps finish after the game has started
        return real(*a, **k)
    monkeypatch.setattr(jobs, "research_forecasts", slow_research)
    out = jobs.predict_now(game)
    assert out["coverage"]["games"] >= 1 and out["coverage"]["forecast"] >= 1, out["coverage"]
    assert game.execute("SELECT COUNT(*) FROM game_forecasts WHERE game_id=? AND status='forecast'", (GID,)).fetchone()[0] >= 1
