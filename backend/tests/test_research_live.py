"""Live research cohorts, weekly decisions and selections (docs/MODEL_SELECTION.md §6-7). Synthetic NHL store, no network."""
import json
import sqlite3

import pytest

from app import agreement, jobs, market_live, near_tipoff, research_live, selector
from app.models import score_candidates
from test_live_markets import add_upcoming, quote, sealed  # noqa: F401  (fixture)
from test_nhl_eval import nhl_store  # noqa: F401  (fixture)

START = "2026-10-12T23:00:00Z"      # Monday 19:00 ET
GID = "nhl:2026020001"
MONDAY = "2026-10-12T14:00:00Z"


@pytest.fixture
def game(sealed, monkeypatch):
    monkeypatch.setattr(score_candidates, "MIN_HOCKEY_EXAMPLES", 100)
    monkeypatch.setattr(research_live, "STACK_MIN_TRAIN", 30)
    add_upcoming(sealed, start="2026-10-12T19:00:00", at="2026-10-12T20:30:00Z")
    sealed.execute("INSERT INTO job_runs (job, started_at, finished_at, status) VALUES "
                   "('refresh', '2026-10-12T21:00:00Z', '2026-10-12T21:00:00Z', 'success')")
    quote(sealed, "2026-10-12T21:05:00Z", date="2026-10-12T23:00Z")
    return sealed


def run(conn, now, write=True):
    """The predict_now research sequence without network steps."""
    out = near_tipoff.predict_now(conn, now=now, write=write)
    ids = [f["game_id"] for f in out["forecasts"]]
    market_live.forecast(conn, now=now, policy="near_tipoff", game_ids=ids, write=write)
    collect = {}
    winner = agreement.predict(conn, now=now, write=write, game_ids=ids, collect=collect)
    return research_live.record(conn, game_ids=ids, winner_run=winner, history=collect, now=now, write=write)


def cohorts(conn):
    """Core cohorts only (the expanded-v1 experiment is tested separately)."""
    return {r["market"]: r for r in conn.execute("SELECT * FROM research_cohorts WHERE definition NOT LIKE '%:expanded-v1' "
                                                 "ORDER BY cohort_id")}


def test_cohorts_cover_every_market_at_the_primary_line_and_retries_add_nothing(game):
    out = run(game, "2026-10-12T21:30:00Z")
    assert out["cohorts_created"] == 6 and not out["errors"], out
    c = cohorts(game)
    assert set(c) == {"winner", "reg3", "spread", "total", "team_total_home", "team_total_away"}
    for market in ("spread", "total"):
        prim = game.execute("SELECT * FROM market_forecasts WHERE market=? AND model_version NOT LIKE 'research-%'",
                            (market,)).fetchone()
        assert (c[market]["line"], c[market]["line_source"], c[market]["market_quote_id"]) == \
               (prim["line"], prim["line_source"], prim["market_quote_id"])
        assert prim["line_source"] == "market"                     # the bookmaker line, never mixed with a model line
        for m in json.loads(c[market]["members"])[1:]:
            row = game.execute("SELECT * FROM market_forecasts WHERE forecast_id=?", (m["ref"]["id"],)).fetchone()
            assert row["line"] == prim["line"] and row["model_version"].startswith("research-v3:")
        assert json.loads(c[market]["bookmaker"])["quote_id"] == prim["market_quote_id"]
    reg3 = json.loads(c["reg3"]["members"])
    assert len(reg3) == 3 and json.loads(c["reg3"]["missing"]) == {}
    assert json.loads(c["reg3"]["stack"])["p"] and abs(sum(json.loads(c["reg3"]["average"])) - 1) < 1e-9
    win = json.loads(c["winner"]["members"])
    assert {m["name"] for m in win} == set(agreement.NAMES["nhl"]) and all(m["ref"]["id"] for m in win)
    sel = game.execute("SELECT * FROM research_selections").fetchall()
    assert len(sel) == 6 and all(s["status"] == "no_pick" and "no selector decision" in s["reason"] for s in sel)
    counts = [game.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
              for t in ("research_cohorts", "research_selections", "market_forecasts", "predictions")]
    again = run(game, "2026-10-12T21:30:00Z")
    assert again["cohorts_created"] == 0 and again["cohorts_existing"] == 6
    assert counts == [game.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                      for t in ("research_cohorts", "research_selections", "market_forecasts", "predictions")]


def test_not_ready_candidates_are_missing_never_substituted(sealed):
    add_upcoming(sealed, start="2026-10-12T19:00:00", at="2026-10-12T20:30:00Z")
    sealed.execute("INSERT INTO job_runs (job, started_at, finished_at, status) VALUES "
                   "('refresh', '2026-10-12T21:00:00Z', '2026-10-12T21:00:00Z', 'success')")
    run(sealed, "2026-10-12T21:30:00Z")
    c = cohorts(sealed)["total"]
    missing = json.loads(c["missing"])
    assert set(missing) == {"goals_draw", "goals_glm"} and all(v.startswith("not_ready") for v in missing.values())
    assert len(json.loads(c["members"])) == 1 and json.loads(c["average"]) is None
    assert json.loads(c["stack"])["reason"] == "incomplete cohort"
    assert c["line_source"] == "model"                               # no quote: the model-only line, labelled
    assert json.loads(c["agreement"])["complete"] is False


def test_missing_primary_market_forecast_skips_that_market_with_a_reason(game):
    out = near_tipoff.predict_now(game, now="2026-10-12T21:30:00Z")
    collect = {}
    winner = agreement.predict(game, now="2026-10-12T21:30:00Z", game_ids=[GID], collect=collect)
    res = research_live.record(game, game_ids=[GID], winner_run=winner, history=collect, now="2026-10-12T21:30:00Z")
    assert set(cohorts(game)) == {"winner"} and out["created"] == 1
    assert all("no primary two-hour market forecast" in s[0]["reason"] for m, s in res["skipped"].items())
    assert res["status"] == "partial"


def test_two_hour_window_and_start_time_are_enforced_by_the_database(game):
    run(game, "2026-10-12T21:30:00Z")
    row = dict(game.execute("SELECT * FROM research_cohorts LIMIT 1").fetchone())
    for info, gen in (("2026-10-12T20:59:59Z", "2026-10-12T21:30:00Z"), ("2026-10-12T22:00:00Z", START)):
        r = {**row, "idempotency_key": f"x{info}", "information_at": info, "generated_at": gen}
        r.pop("cohort_id")
        with pytest.raises(sqlite3.IntegrityError, match="two hours"):
            game.execute(f"INSERT INTO research_cohorts ({','.join(r)}) VALUES ({','.join('?' * len(r))})", list(r.values()))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        game.execute("UPDATE research_cohorts SET line=1")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        game.execute("DELETE FROM research_selections")
    # Rescheduled game: the old cohort no longer matches the start, so it is void and new rows for it are refused.
    add_upcoming(game, start="2026-10-12T20:00:00", at="2026-10-12T21:40:00Z")
    old = research_live.canonical_cohorts(game, "total")[0]
    assert research_live.state(old, "2026-10-13T12:00:00Z") == "void"
    with pytest.raises(sqlite3.IntegrityError):
        r = {**row, "idempotency_key": "late"}
        r.pop("cohort_id")
        game.execute(f"INSERT INTO research_cohorts ({','.join(r)}) VALUES ({','.join('?' * len(r))})", list(r.values()))


def test_calculation_finishing_after_the_start_records_nothing(game, monkeypatch):
    out = near_tipoff.predict_now(game, now="2026-10-12T21:30:00Z")
    market_live.forecast(game, now="2026-10-12T21:30:00Z", policy="near_tipoff", game_ids=[GID])
    collect = {}
    winner = agreement.predict(game, now="2026-10-12T21:30:00Z", game_ids=[GID], collect=collect)
    monkeypatch.setattr(research_live, "utc_now", lambda: "2026-10-12T23:00:01Z")
    res = research_live.record(game, game_ids=[GID], winner_run=winner, history=collect)   # real clock path
    assert res["cohorts_created"] == 0 and game.execute("SELECT count(*) FROM research_cohorts").fetchone()[0] == 0
    assert out["created"] == 1


def test_weekly_decision_is_frozen_and_only_used_after_it_was_written(game):
    early = research_live.decide_due(game, now="2026-10-12T13:00:00Z")      # before Monday 14:00: last week's slot
    assert early["scheduled_for"] == "2026-10-05T14:00:00Z"
    d = research_live.decide_due(game, now="2026-10-12T21:00:00Z")
    assert d["scheduled_for"] == MONDAY and len(d["written"]) == 6
    assert all(w["status"] == "insufficient_evidence" for w in d["written"])
    again = research_live.decide_due(game, now="2026-10-14T21:00:00Z")
    assert again["written"] == [] and len(again["existing"]) == 6           # never recomputed within its interval
    row = game.execute("SELECT * FROM selector_decisions WHERE market='total' AND scheduled_for=?", (MONDAY,)).fetchone()
    assert row["decided_at"] == "2026-10-12T21:00:00Z" and row["effective_until"] == "2026-10-19T14:00:00Z"
    run(game, "2026-10-12T21:30:00Z")
    sel = game.execute("SELECT * FROM research_selections WHERE market='total'").fetchone()
    assert sel["decision_id"] == row["decision_id"] and sel["status"] == "no_pick" and "insufficient_evidence" in sel["reason"]
    # A selection may not reference a decision that was not yet in force when it was made.
    s = dict(sel)
    s.pop("selection_id")
    s.update(idempotency_key="early", generated_at="2026-10-12T21:10:00Z", decision_id=row["decision_id"] + 100)
    with pytest.raises(sqlite3.IntegrityError, match="decision in force"):
        game.execute(f"INSERT INTO research_selections ({','.join(s)}) VALUES ({','.join('?' * len(s))})", list(s.values()))


def test_evidence_uses_only_results_available_before_the_scheduled_time(game):
    run(game, "2026-10-12T21:30:00Z")
    add_upcoming(game, start="2026-10-12T19:00:00", state=7, hs=4, vs=2, period=3, at="2026-10-13T02:00:00Z")
    defn = research_live.definition(game)
    assert research_live.evidence(game, "total", defn, "2026-10-13T01:00:00Z") == []
    ev = research_live.evidence(game, "total", defn, "2026-10-19T14:00:00Z")
    assert len(ev) == 1 and ev[0]["realised"] in (0, 2)
    assert ev[0]["vectors"].keys() >= {"goals_poisson", "goals_draw", "goals_glm", "average", "stack"}


def test_selections_choose_one_market_and_record_reasons(game, monkeypatch):
    run(game, "2026-10-12T21:30:00Z")
    # Install active decisions directly (as if evidence had accumulated) to exercise selection and market choice.
    defn = research_live.definition(game)
    for i, (market, lower) in enumerate((("total", 0.02), ("spread", 0.05), ("winner", 0.01))):
        rule = {"source": "average", "filter": "none", "threshold": 0.0 if market != "winner" else 0.99}
        game.execute("INSERT INTO selector_decisions (idempotency_key, sport, market, selector_version, definition, scheduled_for, "
                     "decided_at, effective_until, status, rule, lower_bound, reason, evidence) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (f"k{i}", "nhl", market, selector.version(), defn, MONDAY, "2026-10-12T21:35:00Z", "2026-10-19T14:00:00Z",
                      "active", json.dumps(rule), lower, "test", "{}"))
    out = run(game, "2026-10-12T21:45:00Z")
    assert out["recommendations"] == [{"game_id": GID, "market": "spread", "side": out["recommendations"][0]["side"]}]
    rows = {r["market"]: r for r in game.execute("SELECT * FROM research_selections ORDER BY selection_id")}   # latest per market
    assert rows["spread"]["generated_at"] == "2026-10-12T21:45:00Z"
    assert rows["spread"]["recommended"] == 1 and rows["total"]["recommended"] == 0 and "outranked" in rows["total"]["game_reason"]
    assert rows["winner"]["status"] == "no_pick" and "below threshold" in rows["winner"]["reason"]
    assert rows["reg3"]["status"] == "no_pick" and "no selector decision" in rows["reg3"]["reason"]
    # The new decisions changed this run's context, so every market has a row in it: one complete, consistent set.
    assert rows["reg3"]["generated_at"] == "2026-10-12T21:45:00Z" and rows["reg3"]["context"] == rows["spread"]["context"]
    detail = research_live.game_detail(game, GID, "2026-10-12T21:50:00Z")
    assert detail["recommendation"]["market"] == "spread"
    assert sum(m["selection"]["recommended"] for m in detail["markets"]) == 1
    earlier = research_live.game_detail(game, GID, "2026-10-12T21:40:00Z")      # as known before the new run
    assert earlier["recommendation"] is None and all(m["selection"]["generated_at"] == "2026-10-12T21:30:00Z"
                                                     for m in earlier["markets"])


def test_jobs_keep_primary_forecasts_when_research_fails_and_respect_the_off_switch(game, monkeypatch):
    monkeypatch.setattr(jobs, "refresh", lambda conn, **kw: {"status": "skipped"})
    monkeypatch.setattr(jobs, "odds_snapshot", lambda conn, **kw: {"status": "skipped"})
    monkeypatch.setattr(research_live, "record", lambda conn, **kw: (_ for _ in ()).throw(RuntimeError("research down")))
    out = jobs.predict_now(game, now="2026-10-12T21:30:00Z")
    assert out["research"]["status"] == "failed" and "research down" in out["research"]["error"]
    assert out["status"] == "success"                   # the primary run is not reported as failed because research failed
    assert game.execute("SELECT count(*) FROM predictions WHERE model_version LIKE '%near_tipoff'").fetchone()[0] == 1
    assert game.execute("SELECT count(*) FROM market_forecasts WHERE model_version NOT LIKE 'research-%'").fetchone()[0] == 5
    monkeypatch.setenv("RESEARCH_SELECTION", "0")
    off = jobs.predict_now(game, now="2026-10-12T21:45:00Z")
    assert off["research"]["status"] == "disabled"
    assert jobs.selector_decisions(game)["status"] == "disabled"


def test_real_api_shows_recorded_research_and_explicit_empty_states(game, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setenv("NBA_API_MODE", "real")
    monkeypatch.setattr(main.app, "dependency_overrides", {})   # test_api.py forces demo mode module-wide
    c = TestClient(main.app)
    empty = c.get(f"/api/research/game/{GID}", params={"sport": "nhl"}).json()
    assert empty["status"] == "no_research_forecast" and empty["markets"] == []
    run(game, "2026-10-12T21:30:00Z")
    monkeypatch.setattr(research_live, "utc_now", lambda: "2026-10-12T21:40:00Z")
    from app.providers import real
    monkeypatch.setattr(real, "utc_now", lambda: "2026-10-12T21:40:00Z")
    got = c.get(f"/api/research/game/{GID}", params={"sport": "nhl"}).json()
    assert got["mode"] == "real" and len(got["markets"]) == 6 and got["recommendation"] is None
    total = next(m for m in got["markets"] if m["market"] == "total")
    assert total["line_label"] == "bookmaker line" and total["minutes_before_start"] == 90.0
    assert total["selection"]["status"] == "no_pick"
    perf = c.get("/api/research/performance", params={"sport": "nhl", "market": "reg3"}).json()
    assert perf["groups"][0]["pending"] == 1 and perf["groups"][0]["settled"] == 0


def test_cached_line_state_reproduces_the_computed_members_and_tracks_inputs(game):
    first = run(game, "2026-10-12T21:30:00Z")
    assert first["line_state"] == "computed" and research_live._cache_path("nhl").is_file()
    before = {r["market"]: r["members"] for r in game.execute("SELECT market, members FROM research_cohorts")}
    again = run(game, "2026-10-12T21:40:00Z")
    assert again["line_state"] == "cache" and again["cohorts_created"] == 0      # identical members, so identical cohorts
    research_live._cache_path("nhl").unlink()
    fresh = run(game, "2026-10-12T21:45:00Z")
    assert fresh["line_state"] == "computed" and fresh["cohorts_created"] == 0
    assert {r["market"]: r["members"] for r in game.execute("SELECT market, members FROM research_cohorts")} == before
    # A new result changes the inputs: the cache is not reused and member identities change (no ledger conflict).
    add_upcoming(game, gid=2026020002, start="2026-10-12T12:00:00", state=7, hs=3, vs=1, period=3, at="2026-10-12T21:50:00Z")
    game.execute("INSERT INTO job_runs (job, started_at, finished_at, status) VALUES "
                 "('refresh', '2026-10-12T21:50:00Z', '2026-10-12T21:50:00Z', 'success')")
    new = run(game, "2026-10-12T21:55:00Z")
    assert new["line_state"] == "computed" and not new["errors"], new


# --- Gap-closing tests (2026-10-02 follow-up) ----------------------------------------------------------------------------
def test_line_a_b_a_keeps_every_revision_and_the_current_line_is_latest(game):
    run(game, "2026-10-12T21:30:00Z")                                              # A: home -1.5
    quote(game, "2026-10-12T21:35:00Z", spread=("+1.5", "-1.5"), date="2026-10-12T23:00Z")
    run(game, "2026-10-12T21:40:00Z")                                              # B: home +1.5
    quote(game, "2026-10-12T21:45:00Z", spread=("-1.5", "+1.5"), date="2026-10-12T23:00Z")
    run(game, "2026-10-12T21:50:00Z")                                              # A again
    rows = game.execute("SELECT line, market_quote_id, generated_at FROM research_cohorts WHERE market='spread' "
                        "ORDER BY generated_at").fetchall()
    assert [r["line"] for r in rows] == [-1.5, 1.5, -1.5] and len({r["market_quote_id"] for r in rows}) == 3
    members = game.execute("SELECT line FROM market_forecasts WHERE market='spread' AND model_version LIKE 'research-v3:goals_draw:%' "
                           "ORDER BY generated_at").fetchall()
    assert [m["line"] for m in members] == [-1.5, 1.5, -1.5]                       # the A forecast is a new revision, not a retry
    assert research_live.canonical_cohorts(game, "spread")[0]["line"] == -1.5
    assert research_live.game_detail(game, GID, "2026-10-12T21:55:00Z")["markets"][-1]["market"]


def test_quotes_retrieved_after_the_information_time_are_never_used(game):
    quote(game, "2026-10-12T21:35:00Z", spread=("+1.5", "-1.5"), total=("o6.5", "u6.5"), ml=("+120", "-140"),
          date="2026-10-12T23:00Z")
    run(game, "2026-10-12T21:30:00Z")
    c = cohorts(game)
    early = game.execute("SELECT quote_id FROM market_quotes WHERE market='spread' AND retrieved_at='2026-10-12T21:05:00Z'").fetchone()[0]
    assert c["spread"]["line"] == -1.5 and c["spread"]["market_quote_id"] == early
    win = json.loads(c["winner"]["bookmaker"])
    assert win["retrieved_at"] == "2026-10-12T21:05:00Z" and win["quote_table"] == "market_quotes" and win["quote_id"]
    late = game.execute("SELECT quote_id FROM market_quotes WHERE market='spread' AND retrieved_at='2026-10-12T21:35:00Z'").fetchone()[0]
    r = dict(c["spread"])
    r.pop("cohort_id")
    r.update(idempotency_key="late-quote", market_quote_id=late, line=1.5)
    with pytest.raises(sqlite3.IntegrityError, match="quote does not match"):
        game.execute(f"INSERT INTO research_cohorts ({','.join(r)}) VALUES ({','.join('?' * len(r))})", list(r.values()))


def test_a_slow_download_that_ends_after_the_start_records_nothing(game, monkeypatch):
    from app import agreement as ag, market_live as ml, near_tipoff as nt
    clock = {"now": "2026-10-12T22:58:00Z"}
    for mod in (nt, ml, ag, research_live):
        monkeypatch.setattr(mod, "utc_now", lambda: clock["now"])

    def slow_refresh(conn, **kw):        # the download finishes after the scheduled start
        clock["now"] = "2026-10-12T23:00:05Z"
        conn.execute("INSERT INTO job_runs (job, started_at, finished_at, status) VALUES ('refresh', ?, ?, 'success')",
                     ("2026-10-12T22:58:00Z", clock["now"]))
        return {"status": "success"}
    monkeypatch.setattr(jobs, "refresh", slow_refresh)
    monkeypatch.setattr(jobs, "odds_snapshot", lambda conn, **kw: {"status": "skipped"})
    out = jobs.predict_now(game)
    assert out["forecasts"] == [] and all(r["reason"] == "after_tipoff" for r in out["refused"])
    for table in ("research_cohorts", "research_selections", "market_forecasts"):
        assert game.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    assert game.execute("SELECT count(*) FROM predictions WHERE run_type='live'").fetchone()[0] == 0


def test_definition_changes_are_reported_as_separate_groups(game, monkeypatch):
    monkeypatch.setattr(research_live, "definition", lambda conn: "defA")
    run(game, "2026-10-12T21:30:00Z")
    monkeypatch.setattr(research_live, "definition", lambda conn: "defB")
    run(game, "2026-10-12T21:40:00Z")
    rep = research_live.report(game, "total", "2026-10-12T21:45:00Z")
    assert sorted(g["definition"] for g in rep["groups"]) == ["defA", "defB"]
    assert all(g["cohorts"] == 1 for g in rep["groups"])                        # one canonical cohort per game and definition
    assert rep["settlement_version"].startswith("settle-")


def test_old_rows_without_quote_id_or_settlement_version_stay_unknown(game):
    research_live.decide_due(game, now="2026-10-12T21:00:00Z")
    row = game.execute("SELECT evidence FROM selector_decisions LIMIT 1").fetchone()
    assert json.loads(row["evidence"])["settlement_version"].startswith("settle-")
    # A decision row recorded before settlement versions existed: reported as unknown, never backfilled.
    game.execute("INSERT INTO selector_decisions (idempotency_key, sport, market, selector_version, definition, scheduled_for, "
                 "decided_at, effective_until, status, rule, lower_bound, reason, evidence) VALUES "
                 "('old', 'nhl', 'reg3', 'selector-v1:old', 'old', '2026-10-05T14:00:00Z', '2026-10-05T15:00:00Z', "
                 "'2026-10-12T14:00:00Z', 'insufficient_evidence', 'null', NULL, 'r', '{}')")
    rep = research_live.report(game, "reg3", "2026-10-12T21:05:00Z")
    old = next(d for d in rep["decisions"] if d["selector_version"] == "selector-v1:old")
    assert old["settlement_version"] is None


def test_slate_reports_each_market_with_line_missing_inputs_and_timing(sealed):
    add_upcoming(sealed, start="2026-10-12T19:00:00", at="2026-10-12T20:30:00Z")
    sealed.execute("INSERT INTO job_runs (job, started_at, finished_at, status) VALUES "
                   "('refresh', '2026-10-12T21:00:00Z', '2026-10-12T21:00:00Z', 'success')")
    run(sealed, "2026-10-12T21:30:00Z")                    # candidates not ready: missing with reasons
    s = research_live.slate(sealed, [GID], "2026-10-12T21:35:00Z")[GID]
    t = s["markets"]["total"]
    assert t["line_source"] == "model" and t["line"] is not None and t["minutes_before_start"] == 90.0
    assert set(t["missing"]) == {"goals_draw", "goals_glm"} and t["status"] == "no_pick"
    assert set(s["markets"]) == {"winner", "reg3", "spread", "total", "team_total_home", "team_total_away"}


# --- Fit register (docs/MODEL_SELECTION.md §13) ----------------------------------------------------------------------------
def test_fit_register_identifies_each_learned_fit_once_and_is_append_only(game):
    run(game, "2026-10-12T21:30:00Z")
    fits = {r["member"]: dict(r) for r in game.execute("SELECT * FROM model_fits")}
    assert {"hockey_logistic", "hockey_gbt"} <= set(fits), fits.keys()
    assert "hockey_elo" not in fits and "goals_poisson" not in fits          # online ratings only: no learned fit
    for f in fits.values():
        assert f["schedule"] == "season_start" and f["results_available_through"] <= "2026-10-12T21:30:00Z"
        assert f["training_games"] > 0 and len(f["params_digest"]) == 24 and json.loads(f["libraries"])["scikit-learn"]
    run(game, "2026-10-12T21:40:00Z")                                        # same data, same fits: no new rows
    assert game.execute("SELECT count(*) FROM model_fits").fetchone()[0] == len(fits)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        game.execute("UPDATE model_fits SET training_games = 0")


def test_a_fit_register_failure_never_blocks_forecasts_or_cohorts(game, monkeypatch):
    from app import fit_register

    def broken(*a, **k):
        raise RuntimeError("register unavailable")
    monkeypatch.setattr(fit_register, "record", broken)
    out = run(game, "2026-10-12T21:30:00Z")
    assert out["cohorts_created"] > 0 and "line_members" not in out["errors"] and out["fit_register_errors"]


# --- NHL xG shadow member (docs/MODEL_SELECTION.md §12) ---------------------------------------------------------------------
def test_xg_shadow_member_is_recorded_but_never_votes(game):
    from app import nhl_shots
    from test_nhl_xg import fake_fetch
    nhl_shots.import_pending(game, None, pause=0, fetch=fake_fetch(game), log=lambda *_: None)
    out = run(game, "2026-10-12T21:30:00Z")
    c = cohorts(game)["winner"]
    members = {m["name"] for m in json.loads(c["members"])}
    assert "hockey_logistic_xg" in members, (members, out)
    agree = json.loads(c["agreement"])
    assert "hockey_logistic_xg" not in agree["picks"] and agree["expected"] == 4
    assert "hockey_logistic_xg" in research_live.sources("nhl", "winner")
    row = game.execute("SELECT model_version FROM predictions WHERE model_version LIKE 'research-v2:hockey_logistic_xg:%'").fetchone()
    assert row is not None
    assert game.execute("SELECT count(*) FROM model_fits WHERE member='hockey_logistic_xg'").fetchone()[0] == 1


def test_xg_member_skips_when_recent_shots_are_missing(game, monkeypatch):
    from app import agreement as ag
    monkeypatch.setattr(ag, "STALE_SHOT_GAMES", 3)
    # no shots imported at all: the member is skipped with a reason, every other member still records
    add_upcoming(game, gid=2026020002, start="2026-10-11T19:00:00", state=7, hs=3, vs=1, period=3, at="2026-10-12T20:00:00Z")
    out = run(game, "2026-10-12T21:30:00Z")
    c = cohorts(game)["winner"]
    assert "hockey_logistic_xg" in json.loads(c["missing"])
    assert {m["name"] for m in json.loads(c["members"])} >= {"hockey_elo", "goals_poisson"}


def test_predict_now_imports_nhl_shots_in_a_bounded_step(game, monkeypatch):
    from test_nhl_xg import fake_fetch
    monkeypatch.setattr(jobs, "refresh", lambda conn, **kw: {"status": "skipped"})
    monkeypatch.setattr(jobs, "odds_snapshot", lambda conn, **kw: {"status": "skipped"})
    out = jobs.predict_now(game, now="2026-10-12T21:30:00Z", pbp_kw={"fetch": fake_fetch(game), "limit": 5})
    assert out["steps"]["nhl_pbp"] == "success"
    assert game.execute("SELECT count(*) FROM nhl_pbp_imports").fetchone()[0] == 5


def test_line_shadow_members_never_vote_or_enter_the_average():
    g = {"game_id": "g", "tipoff_at": "2026-10-21T23:00:00Z"}
    core = [{"name": n, "ref": {}, "p": [0.6, 0.0, 0.4]} for n in research_live.LINE_MEMBERS["nba"]]
    shadow = [{"name": n, "ref": {}, "p": [0.1, 0.0, 0.9]} for n in research_live.SHADOW_LINE_MEMBERS["nba"]]
    c = research_live._cohort("nba", g, "total", 220.5, "model", None, None, "d", "points_normal", "t", "t", core + shadow, {},
                              research_live.LINE_MEMBERS["nba"], None, None)
    assert c["agreement"]["unanimous"] and set(c["agreement"]["picks"]) == set(research_live.LINE_MEMBERS["nba"])
    assert abs(c["average"][0] - 0.6) < 1e-9


# --- Expanded-combination experiment expanded-v1 (docs/MODEL_SELECTION.md §14) ----------------------------------------------
def test_expanded_cohort_is_recorded_separately_and_never_selected(game):
    from app import nhl_shots
    from test_nhl_xg import fake_fetch
    nhl_shots.import_pending(game, None, pause=0, fetch=fake_fetch(game), log=lambda *_: None)
    out = run(game, "2026-10-12T21:30:00Z")
    assert out["expanded_created"] == 1 and out["cohorts_created"] == 6        # winner only for the NHL (xG is a winner member)
    rows = game.execute("SELECT * FROM research_cohorts WHERE market='winner' ORDER BY cohort_id").fetchall()
    core, x = rows
    assert x["definition"] == research_live.expanded_definition(core["definition"])
    ax = json.loads(x["agreement"])
    assert ax["expected"] == 5 and "hockey_logistic_xg" in ax["picks"]       # the new member votes here, not in the core
    assert json.loads(core["agreement"])["expected"] == 4
    assert len(json.loads(x["average"])) == 3 and json.loads(x["average"]) != json.loads(core["average"])
    sel = game.execute("SELECT cohort_id FROM research_selections").fetchall()
    assert x["cohort_id"] not in {s[0] for s in sel}                         # recording only: never selected
    d = research_live.game_detail(game, GID, "2026-10-12T21:35:00Z")
    assert all(not research_live.is_expanded(m["definition"]) for m in d["markets"]) and len(d["markets"]) == 6
    assert [e["market"] for e in d["expanded"]] == ["winner"] and research_live.is_expanded(d["expanded"][0]["definition"])
    rep = research_live.report(game, "winner", "2026-10-12T21:35:00Z")
    groups = {g["experiment"]: g for g in rep["groups"]}
    assert groups["expanded-v1"]["recording_only"] and groups["expanded-v1"]["selector"] is None
    assert groups["core"]["selector"] is not None
    again = run(game, "2026-10-12T21:30:00Z")
    assert again["expanded_created"] == 0 and again["expanded_existing"] == 1


def test_primary_market_reads_and_scores_never_include_research_member_rows(game):
    run(game, "2026-10-12T21:30:00Z")
    assert game.execute("SELECT count(*) FROM market_forecasts WHERE model_version LIKE 'research-%'").fetchone()[0] > 0
    rows = market_live.latest(game, policy="near_tipoff")
    assert rows and all(not r["model_version"].startswith("research-") for r in rows)
    assert len({(r["game_id"], r["market"]) for r in rows}) == len(rows)       # one primary forecast per game and market


# --- Candidate pool integration (docs/CANDIDATE_POOL.md) ---------------------------------------------------------------------
def test_predict_now_records_the_candidate_pool_after_cohorts_and_its_failure_never_fails_the_run(game, monkeypatch):
    from app.candidates import live as pool_live
    monkeypatch.setattr(jobs, "refresh", lambda conn, **kw: {"status": "skipped"})
    monkeypatch.setattr(jobs, "odds_snapshot", lambda conn, **kw: {"status": "skipped"})
    seen = {}

    def fake(conn, **kw):
        seen["cohorts_before"] = conn.execute("SELECT count(*) FROM research_cohorts").fetchone()[0]
        seen.update(kw)
        raise RuntimeError("pool down")
    monkeypatch.setattr(pool_live, "record", fake)
    out = jobs.predict_now(game, now="2026-10-12T21:30:00Z")
    assert seen["cohorts_before"] > 0 and seen["game_ids"] == [GID] and seen["now"] == "2026-10-12T21:30:00Z"
    assert out["pool"]["status"] == "failed" and "pool down" in out["pool"]["error"]
    assert out["status"] == "success" and out["research"]["cohorts_created"] > 0


def test_a_game_that_starts_during_the_cycle_is_a_member_skip_not_an_error(game):
    """Timing bug (live run 108): a game accepted at the start of a cycle that starts (status no longer 'scheduled') before
    the line members' state is computed must be reported as missing members with a reason, never an AttributeError."""
    now = "2026-10-12T21:30:00Z"
    out = near_tipoff.predict_now(game, now=now)
    ids = [f["game_id"] for f in out["forecasts"]]
    market_live.forecast(game, now=now, policy="near_tipoff", game_ids=ids)
    collect = {}
    winner = agreement.predict(game, now=now, game_ids=ids, collect=collect)
    game.execute("UPDATE games SET status='in_progress' WHERE game_id=?", (GID,))     # the game started mid-cycle
    game.commit()
    res = research_live.record(game, game_ids=ids, winner_run=winner, history=collect, now=now)
    assert not any("AttributeError" in str(v) for v in res["errors"].values()), res["errors"]
    assert all("AttributeError" not in json.dumps(r) for r in res.get("skipped", {}).values())


def test_a_cohort_refused_by_the_database_is_a_skip_not_an_aborted_step(game):
    """Reconciliation review L5: if the guard trigger refuses a cohort (e.g. a race with a start change), the step reports
    a skip with a reason and keeps going; it never raises out of record()."""
    run(game, "2026-10-12T21:30:00Z")
    c = dict(game.execute("SELECT * FROM research_cohorts WHERE market='winner' LIMIT 1").fetchone())
    cohort = {k: (json.loads(c[k]) if k in ("members", "missing", "average", "stack", "agreement", "bookmaker") else c[k])
              for k in c}
    cohort["generated_at"] = cohort["information_at"] = "2026-10-12T20:00:00Z"      # 3 h before the start: trigger refuses
    cohort["line_source"] = "model-x"                                                 # a new idempotency key
    out = {"skipped": {}, "cohorts_created": 0, "cohorts_existing": 0}
    research_live._store(game, cohort, {GID: {}}, out, True)
    assert out["cohorts_created"] == 0 and out["skipped"]["winner"][0]["reason"].startswith("game no longer scheduled")
