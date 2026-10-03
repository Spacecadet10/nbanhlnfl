"""Regressions for the 2026-10-03 correctness audit (HANDOFF.md). Each test fails on ab0f190."""
import json

import pytest

from app import db, ingest, live, market_live, near_tipoff, tracking
from app.providers.real import RealProvider
from test_live_markets import add_upcoming, quote, sealed  # noqa: F401
from test_nhl_eval import nhl_store  # noqa: F401

GID = "nhl:2026020001"


def final(c, start, hs=4, vs=2, period=3, at="2026-10-11T03:00:00Z"):
    body = json.dumps({"data": [{"id": 2026020001, "easternStartTime": start, "gameScheduleStateId": 1, "gameStateId": 7,
                                 "gameType": 2, "homeScore": hs, "homeTeamId": 6, "period": period, "season": 20262027,
                                 "visitingScore": vs, "visitingTeamId": 16}]}).encode()
    ingest.ingest_nhl_season(c, 2027, fetch=lambda _s: body, retrieved_at=at)


def two_hour_forecast(c):
    """An NHL two-hour winner forecast for a 23:00Z start with a moneyline retrieved 20 minutes earlier, then the final."""
    add_upcoming(c, start="2026-10-10T19:00:00", at="2026-10-10T21:30:00Z")
    c.execute("INSERT INTO job_runs (job, started_at, finished_at, status) VALUES "
              "('refresh', '2026-10-10T21:30:00Z', '2026-10-10T21:30:00Z', 'success')")
    quote(c, "2026-10-10T21:40:00Z", date="2026-10-10T23:00Z")
    assert near_tipoff.predict_now(c, now="2026-10-10T22:00:00Z")["created"] == 1


def test_nhl_two_hour_forecast_shows_the_quote_it_could_see(sealed):
    two_hour_forecast(sealed)
    quote(sealed, "2026-10-10T22:30:00Z", ml=("-300", "+250"), date="2026-10-10T23:00Z")   # after the forecast: never used
    final(sealed, "2026-10-10T19:00:00")
    p = RealProvider(sealed, now="2026-10-11T05:00:00Z")
    stored = sealed.execute("SELECT p_a_devig FROM market_quotes WHERE market='winner' AND price_label='current' "
                            "AND retrieved_at='2026-10-10T21:40:00Z'"
                            ).fetchone()[0]
    near = p.game(GID).near_tipoff
    assert near.market_home_prob == pytest.approx(stored) and near.market_retrieved_at is not None
    score = near_tipoff.score(sealed)["same_games_as_market"]
    assert score["coverage"] == 1.0 and score["market"]["games"] == 1
    perf = p.performance("live", policy="near_tipoff")
    assert perf.market is not None and perf.market.matched_games == 1


def test_nhl_night_before_record_is_compared_with_the_market(sealed):
    add_upcoming(sealed, start="2026-10-11T19:00:00")
    quote(sealed, "2026-10-10T14:00:00Z")
    live.predict(sealed, now="2026-10-10T15:00:00Z")
    final(sealed, "2026-10-11T19:00:00", at="2026-10-12T03:00:00Z")
    perf = RealProvider(sealed, now="2026-10-12T05:00:00Z").performance("live")
    assert perf.sample_size == 1 and perf.market is not None and perf.market.matched_games == 1


def test_night_before_winner_forecast_is_void_when_the_game_moves_to_another_day(sealed):
    add_upcoming(sealed, start="2026-10-11T19:00:00")
    live.predict(sealed, now="2026-10-10T15:00:00Z")
    final(sealed, "2026-10-12T19:00:00", at="2026-10-13T03:00:00Z")      # moved one day later, then played
    s = live.score(sealed)
    assert s["scored"] == 0 and s["start_changed"] == 1
    sealed.execute("INSERT INTO job_runs (job, started_at, finished_at, status) VALUES "
                   "('refresh', '2026-10-12T21:30:00Z', '2026-10-12T21:30:00Z', 'success')")
    p = RealProvider(sealed, now="2026-10-13T05:00:00Z")
    hist = p.predictions("live", None, None, None, 50)
    assert hist.total == 1 and hist.record.settled == 0
    assert hist.predictions[0].correct is None and hist.predictions[0].void is True
    assert p.performance("live").sample_size == 0


def test_two_hour_winner_history_marks_a_moved_game_void(sealed):
    two_hour_forecast(sealed)
    final(sealed, "2026-10-10T20:00:00")                                  # started an hour later than forecast
    p = RealProvider(sealed, now="2026-10-11T05:00:00Z")
    row = p.predictions("live", None, None, None, 50, policy="near_tipoff").predictions[0]
    assert row.void is True and row.correct is None
    assert p.game(GID).near_tipoff.void is True


def test_threshold_table_for_the_three_way_market_starts_below_fifty_percent():
    rows = [(0.42, "win"), (0.38, "loss"), (0.36, "loss")]
    reg3 = tracking.table(rows, "reg3")
    assert reg3[0]["threshold"] == 0.35 and reg3[0]["settled"] == 3 and reg3[0]["win"] == 1
    assert [t["threshold"] for t in tracking.table(rows, "spread")][0] == 0.5


# --- Storage guards and job overlap ---------------------------------------------------------------------------------
def _market_row(tipoff, generated="2026-10-10T21:30:00Z", line=6.5):
    from app.ledger import MarketForecast
    return MarketForecast("nhl", GID, "total", line, "model", None, "near_tipoff", "live", "m-window=2h-policy=near_tipoff",
                          "d", "f", tipoff, generated, tipoff, 0.5, 0.0, 0.5)


def test_two_hour_market_forecast_must_match_the_games_current_scheduled_start(sealed):
    import sqlite3
    from app.ledger import record_market
    add_upcoming(sealed, start="2026-10-10T19:00:00", at="2026-10-10T21:00:00Z")           # 23:00Z, scheduled
    with pytest.raises(sqlite3.IntegrityError, match="current scheduled start"):
        record_market(sealed, _market_row("2026-10-10T22:30:00Z"))                         # not the game's start
    assert record_market(sealed, _market_row("2026-10-10T23:00:00Z"))[1] is True
    add_upcoming(sealed, start="2026-10-10T19:00:00", state=3, at="2026-10-10T21:10:00Z")  # no longer scheduled (live state)
    assert sealed.execute("SELECT status FROM games WHERE game_id=?", (GID,)).fetchone()[0] == "in_progress"
    with pytest.raises(sqlite3.IntegrityError, match="current scheduled start"):
        record_market(sealed, _market_row("2026-10-10T23:00:00Z", generated="2026-10-10T21:40:00Z", line=7.5))


def test_overlapping_jobs_on_one_store_do_not_run_together(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    with db.store_lock("nhl") as first:
        assert first is True
        with db.store_lock("nhl", wait_seconds=0) as second:          # a second job on the same store: refused, not queued
            assert second is False
        with db.store_lock("nba", wait_seconds=0) as other:           # another sport's store is independent
            assert other is True
    with db.store_lock("nhl", wait_seconds=0) as again:               # released when the first job ends
        assert again is True


def test_the_periodic_odds_command_skips_while_another_job_holds_the_store(tmp_path, monkeypatch, capsys):
    from app import cli
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "ODDS_LOCK_WAIT_SECONDS", 0.2)          # a short job is waited out; a long one is not
    with db.store_lock("nhl"):
        assert cli.main(["--sport", "nhl", "odds"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "skipped" and "another job" in out["reason"]
    assert not (tmp_path / "nhl.sqlite").exists()                    # nothing was opened or migrated


def test_market_records_count_a_game_once_after_the_market_model_is_resealed(sealed):
    from app.ledger import MarketForecast, record_market
    add_upcoming(sealed, start="2026-10-11T19:00:00")
    market_live.forecast(sealed, now="2026-10-10T15:00:00Z")
    r = dict(sealed.execute("SELECT * FROM market_forecasts WHERE market='total' AND policy='pregame'").fetchone())
    record_market(sealed, MarketForecast(                       # the same game forecast by an earlier model version
        r["sport"], r["game_id"], r["market"], r["line"], r["line_source"], r["market_quote_id"], r["policy"], "live",
        r["model_version"] + "-earlier-seal", r["dataset_version"], r["feature_version"], r["cutoff_at"],
        "2026-10-10T16:00:00Z", r["tipoff_at"], 0.2, 0.0, 0.8, r["params"], ""))
    final(sealed, "2026-10-11T19:00:00", at="2026-10-12T03:00:00Z")
    p = RealProvider(sealed, now="2026-10-12T05:00:00Z")
    hist = p.market_predictions("total", "pregame", None, None, None, 50, None)
    assert hist.total == 1 and hist.record.selected == 1 and hist.predictions[0].forecast.model_version == r["model_version"]
    assert p.market_performance("total", "pregame").forecasts == 1
    assert market_live.score(sealed, policy="pregame", market="total")["markets"]["total"]["forecasts"] == 1
    # The game page lists each version's forecast, labelled (Codex review): the current one first, never merged into a record.
    assert [(f.model_version, f.current_version) for f in p.game(GID).market_forecasts
            if f.market == "total" and f.policy == "pregame"] == [(r["model_version"], True), (r["model_version"] + "-earlier-seal", False)]


def test_two_hour_score_never_compares_with_a_void_night_before_forecast(sealed):
    add_upcoming(sealed, start="2026-10-10T19:00:00", at="2026-10-09T12:00:00Z")
    live.predict(sealed, now="2026-10-09T15:00:00Z")                       # night-before forecast for 10 October
    add_upcoming(sealed, start="2026-10-11T19:00:00", at="2026-10-11T21:30:00Z")   # moved to 11 October
    sealed.execute("INSERT INTO job_runs (job, started_at, finished_at, status) VALUES "
                   "('refresh', '2026-10-11T21:30:00Z', '2026-10-11T21:30:00Z', 'success')")
    assert near_tipoff.predict_now(sealed, now="2026-10-11T22:00:00Z")["created"] == 1
    final(sealed, "2026-10-11T19:00:00", at="2026-10-12T03:00:00Z")
    s = near_tipoff.score(sealed)
    assert s["scored"]["games"] == 1 and s["same_games_as_night_before"]["night_before"]["games"] == 0


def test_the_scheduler_takes_the_store_lock_per_iteration_not_through_its_sleep(tmp_path, monkeypatch):
    from app import jobs
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    seen = []

    def sleep(_seconds):                                  # while the scheduler sleeps, another job can take the store
        with db.store_lock("nhl", wait_seconds=0) as free:
            seen.append(free)
    monkeypatch.setattr(jobs, "daily", lambda conn: {"refresh": {"status": "skipped"}})
    out = jobs.scheduler(None, interval_minutes=30, max_runs=2, job="daily", log=lambda _m: None, sleep=sleep,
                         lock=lambda: db.store_lock("nhl", wait_seconds=0))
    assert seen == [True] and len(out) == 2
    with db.store_lock("nhl"):                            # and an iteration that finds the store busy is skipped, visibly
        out = jobs.scheduler(None, interval_minutes=30, max_runs=1, job="daily", log=lambda _m: None,
                             lock=lambda: db.store_lock("nhl", wait_seconds=0))
    assert out == [{"status": "skipped", "reason": "another job is running on this store"}]


# --- Codex review 2026-10-03: older market-model forecasts stay reachable -------------------------------------------------
def _older_version_forecast(c, market="total", generated="2026-10-10T16:00:00Z"):
    from app.ledger import MarketForecast, record_market
    r = dict(c.execute("SELECT * FROM market_forecasts WHERE market=? AND policy='pregame' ORDER BY forecast_id LIMIT 1",
                       (market,)).fetchone())
    old = r["model_version"] + "-earlier-seal"
    record_market(c, MarketForecast(r["sport"], r["game_id"], r["market"], r["line"], r["line_source"], r["market_quote_id"],
                                    r["policy"], "live", old, r["dataset_version"], r["feature_version"], r["cutoff_at"],
                                    generated, r["tipoff_at"], 0.2, 0.0, 0.8, r["params"], ""))
    return r["model_version"], old


def test_an_earlier_market_model_versions_forecasts_are_reachable_by_version(sealed):
    add_upcoming(sealed, start="2026-10-11T19:00:00")
    market_live.forecast(sealed, now="2026-10-10T15:00:00Z")
    current, old = _older_version_forecast(sealed)
    final(sealed, "2026-10-11T19:00:00", at="2026-10-12T03:00:00Z")
    p = RealProvider(sealed, now="2026-10-12T05:00:00Z")
    default = p.market_predictions("total", "pregame", None, None, None, 50, None)
    assert default.model_version == current and default.total == 1
    assert [(v.model_version, v.current, v.games) for v in default.versions] == [(current, True, 1), (old, False, 1)]
    archive = p.market_predictions("total", "pregame", None, None, None, 50, None, model_version=old)
    assert archive.model_version == old and archive.total == 1 and archive.record.selected == 1
    assert archive.predictions[0].forecast.model_version == old and archive.predictions[0].forecast.current_version is False
    perf = p.market_performance("total", "pregame", model_version=old)
    assert perf.model_version == old and perf.forecasts == 1 and [v.model_version for v in perf.versions] == [current, old]
    shown = [(f.model_version, f.current_version) for f in p.game(GID).market_forecasts if f.market == "total" and f.policy == "pregame"]
    assert shown == [(current, True), (old, False)]          # every version on the game page, labelled; counted in neither twice


def test_a_game_forecast_only_by_an_earlier_model_version_is_still_shown(sealed, monkeypatch):
    add_upcoming(sealed, start="2026-10-11T19:00:00")
    market_live.forecast(sealed, now="2026-10-10T15:00:00Z")
    stored = sealed.execute("SELECT DISTINCT model_version FROM market_forecasts").fetchone()[0]
    # The market model is re-sealed afterwards: the stored forecasts now belong to an earlier version.
    monkeypatch.setattr(market_live, "model_version", lambda base, policy: f"resealed-{policy}")
    final(sealed, "2026-10-11T19:00:00", at="2026-10-12T03:00:00Z")
    p = RealProvider(sealed, now="2026-10-12T05:00:00Z")
    default = p.market_predictions("total", "pregame", None, None, None, 50, None)
    assert default.total == 0 and [(v.model_version, v.current, v.games) for v in default.versions] == [(stored, False, 1)]
    assert p.market_predictions("total", "pregame", None, None, None, 50, None, model_version=stored).total == 1
    game = [f for f in p.game(GID).market_forecasts if f.market == "total"]
    assert len(game) == 1 and game[0].model_version == stored and game[0].current_version is False
    # On the slate the earlier version's forecast is display-only: labelled, never a pick of the current model, and outside
    # its counts, confidence filter and performance (only the explicit version view above scores it).
    for level in (None, 0.5):
        resp = p.games(game_date(sealed), "pregame", level, "total")
        shown = resp.games[0].market_forecast
        assert shown is not None and shown.current_version is False and shown.meets_confidence is False
        assert (resp.selection.with_forecast, resp.selection.selected) == (0, 0)
    perf = p.market_performance("total", "pregame")
    assert perf.forecasts == 0 and perf.record.selected == 0 and perf.model_version == "resealed-pregame"
    assert market_live.score(sealed, policy="pregame", market="total")["markets"] == {}
    assert p.market_predictions("total", "pregame", None, None, None, 50, 0.5).record.selected == 0


def game_date(c):
    from app.providers.real import eastern_date
    return eastern_date(c.execute("SELECT tipoff_at FROM games WHERE game_id=?", (GID,)).fetchone()[0])
