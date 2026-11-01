"""Live NHL winner forecasts, market forecasts with exact lines, settlement and the NHL jobs (synthetic, no network)."""
import json

import pytest

from app import db, ingest, jobs, live, market_live, near_tipoff, nhl_eval, nba_markets
from test_multisport import lines_event
from test_nhl_eval import nhl_store  # noqa: F401  (fixture)
from test_storage import payload

NOW = "2026-10-10T15:00:00Z"


@pytest.fixture
def sealed(nhl_store, tmp_path, monkeypatch):
    path = tmp_path / "nhl_decision.json"
    nhl_eval.evaluate_validation(nhl_store, decision_path=path, out_dir=tmp_path / "val", log=lambda _m: None)
    monkeypatch.setattr(nhl_eval, "DECISION_PATH", path)
    return nhl_store


def add_upcoming(c, gid=2026020001, start="2026-10-10T19:00:00", state=1, hs=0, vs=0, period=1, at="2026-10-10T12:00:00Z",
                 sched=1, home=6, away=16, season=2027):
    body = json.dumps({"data": [{"id": gid, "easternStartTime": start, "gameScheduleStateId": sched, "gameStateId": state,
                                 "gameType": 2, "homeScore": hs, "homeTeamId": home, "period": period, "season": int(f"{season - 1}{season}"),
                                 "visitingScore": vs, "visitingTeamId": away}]}).encode()
    ingest.ingest_nhl_season(c, season, fetch=lambda _s: body, retrieved_at=at)


def quote(c, at, spread=("-1.5", "+1.5"), total=("o6.5", "u6.5"), ml=("-150", "+130"), date="2026-10-11T23:00Z"):
    e = lines_event(eid="401", date=date, home="Boston Bruins", away="Chicago Blackhawks",
                    spread=spread, total=total)
    e["competitions"][0]["odds"][0]["moneyline"]["home"]["close"]["odds"] = ml[0]
    e["competitions"][0]["odds"][0]["moneyline"]["away"]["close"]["odds"] = ml[1]
    return ingest.ingest_nhl_day(c, "20261010", fetch=lambda _d: payload(e), retrieved_at=at)


def test_nhl_night_before_forecast_uses_the_sealed_hockey_model(sealed):
    add_upcoming(sealed, start="2026-10-11T19:00:00")
    out = live.predict(sealed, now=NOW)
    assert out["sport"] == "nhl" and out["created"] == 1 and out["model_version"].startswith("hockey_elo-")
    row = sealed.execute("SELECT * FROM predictions WHERE run_type='live'").fetchone()
    assert row["feature_version"] == nhl_eval.FEATURE_VERSION and row["cutoff_at"] == "2026-10-11T04:00:00Z"
    assert live.predict(sealed, now=NOW)["already_recorded"] == 1


def test_market_forecasts_keep_exact_lines_and_ignore_price_only_moves(sealed):
    add_upcoming(sealed, start="2026-10-11T19:00:00")
    quote(sealed, "2026-10-10T14:00:00Z")
    first = market_live.forecast(sealed, now=NOW)
    by = {f["market"]: f for f in first["forecasts"]}
    assert set(by) == {"reg3", "spread", "total", "team_total_home", "team_total_away"}
    assert (by["spread"]["line"], by["spread"]["line_source"]) == (-1.5, "market")
    assert (by["total"]["line"], by["total"]["line_source"]) == (6.5, "market")
    assert by["team_total_home"]["line_source"] == "model" and by["team_total_home"]["line"] % 1 == 0.5
    assert by["reg3"]["line"] is None and by["reg3"]["p"][1] > 0
    # price moves, same line, unchanged results: the stored forecasts are returned, nothing new
    quote(sealed, "2026-10-10T15:30:00Z", ml=("-170", "+150"))
    again = market_live.forecast(sealed, now="2026-10-10T16:00:00Z")
    assert again["created"] == 0 and again["already_recorded"] == 5
    # the line moves: a new spread forecast at the new line; the old one is kept
    quote(sealed, "2026-10-10T16:30:00Z", spread=("-2.5", "+2.5"))
    moved = market_live.forecast(sealed, now="2026-10-10T17:00:00Z")
    assert moved["created"] == 1
    lines = [r[0] for r in sealed.execute("SELECT line FROM market_forecasts WHERE market='spread' ORDER BY forecast_id")]
    assert lines == [-1.5, -2.5]
    latest = {r["market"]: r for r in market_live.latest(sealed)}
    assert latest["spread"]["line"] == -2.5 and latest["spread"]["quote_retrieved_at"] == "2026-10-10T16:30:00Z"


def test_a_quote_retrieved_after_generation_is_never_used(sealed):
    add_upcoming(sealed, start="2026-10-11T19:00:00")
    quote(sealed, "2026-10-10T16:00:00Z")
    out = market_live.forecast(sealed, now=NOW, write=False)
    assert {f["line_source"] for f in out["forecasts"] if f["market"] in ("spread", "total")} == {"model"}


def test_settlement_win_loss_push_and_void(sealed):
    add_upcoming(sealed, gid=2026020001, start="2026-10-11T19:00:00")
    add_upcoming(sealed, gid=2026020002, start="2026-10-11T21:00:00")
    market_live.forecast(sealed, now=NOW)
    # game 1 final 4-2 in regulation; game 2 postponed
    body = json.dumps({"data": [
        {"id": 2026020001, "easternStartTime": "2026-10-11T19:00:00", "gameScheduleStateId": 1, "gameStateId": 7,
         "gameType": 2, "homeScore": 4, "homeTeamId": 6, "period": 3, "season": 20262027, "visitingScore": 2,
         "visitingTeamId": 16},
        {"id": 2026020002, "easternStartTime": "2026-10-11T21:00:00", "gameScheduleStateId": 5, "gameStateId": 1,
         "gameType": 2, "homeScore": 0, "homeTeamId": 6, "period": 1, "season": 20262027, "visitingScore": 0,
         "visitingTeamId": 16}]}).encode()
    ingest.ingest_nhl_season(sealed, 2027, fetch=lambda _s: body, retrieved_at="2026-10-12T03:00:00Z")
    s = market_live.score(sealed)["markets"]
    assert s["reg3"]["void"] == 1 and s["reg3"]["settled"] == 1
    total = s["total"]
    assert total["win"] + total["loss"] + total["push"] == 1 and total["void"] == 1
    rows = {(r["game_id"], r["market"]): r for r in market_live.latest(sealed)}
    assert market_live.outcome(rows[("nhl:2026020001", "reg3")]) == "a"
    assert market_live.outcome(rows[("nhl:2026020002", "spread")]) == "void"
    filtered = market_live.score(sealed, min_confidence=0.99)["markets"]
    assert all(m["forecasts"] == 0 for m in filtered.values()) or filtered == {}


def test_nhl_near_tipoff_forecast_and_market_snapshot(sealed):
    add_upcoming(sealed, start="2026-10-10T19:00:00", at="2026-10-10T21:30:00Z")
    sealed.execute("INSERT INTO job_runs (job, started_at, finished_at, status) VALUES "
                   "('refresh', '2026-10-10T21:30:00Z', '2026-10-10T21:30:00Z', 'success')")
    quote(sealed, "2026-10-10T21:40:00Z", date="2026-10-10T23:00Z")
    out = near_tipoff.predict_now(sealed, now="2026-10-10T22:00:00Z")
    assert out["created"] == 1 and out["model_version"].endswith("-policy=near_tipoff")
    row = sealed.execute("SELECT * FROM predictions WHERE model_version LIKE '%near_tipoff'").fetchone()
    assert row["cutoff_at"] == "2026-10-10T23:00:00Z" and row["feature_version"].endswith("tipoff-cutoff+two-hour-v1")
    near = market_live.forecast(sealed, now="2026-10-10T22:00:00Z", policy="near_tipoff", game_ids=["nhl:2026020001"])
    assert near["created"] == 5 and all(f["line_source"] in (None, "market", "model") for f in near["forecasts"])
    assert sealed.execute("SELECT count(*) FROM market_forecasts WHERE policy='near_tipoff' AND cutoff_at=?",
                          ("2026-10-10T23:00:00Z",)).fetchone()[0] == 5


def test_nhl_jobs_refresh_quota_and_daily(sealed):
    from datetime import datetime, timezone
    calls = []

    def fetch(season):
        calls.append(season)
        return json.dumps({"data": []}).encode()
    now = datetime.now(timezone.utc)   # the quota compares with job finish times, which use the real clock
    season = jobs.nhl_season_for(now)
    assert jobs.refresh(sealed, fetch=fetch, now=now)["status"] == "success" and calls == [season]
    assert jobs.refresh(sealed, fetch=fetch, now=now)["status"] == "skipped" and calls == [season]
    failing = jobs.refresh(sealed, fetch=lambda s: (_ for _ in ()).throw(OSError("down")), force=True,
                           sleep=lambda _s: None, now=now)
    assert failing["status"] == "failed" and failing["attempts"] == jobs.RETRIES
    assert jobs.nhl_season_for(datetime(2027, 3, 1, tzinfo=timezone.utc)) == 2027
    assert jobs.nhl_season_for(datetime(2026, 7, 2, tzinfo=timezone.utc)) == 2027


def test_nba_markets_are_skipped_without_a_sealed_model(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(nba_markets, "DECISION_PATH", tmp_path / "missing.json")
    c = db.connect(tmp_path / "nba.sqlite")
    db.migrate(c)
    db.claim_store(c, "nba")
    out = jobs.market_forecasts(c)                     # two-hour policy by default
    assert out["status"] == "skipped" and "No sealed NBA market model" in out["reason"]


def test_night_before_job_paths_refuse_before_writing(tmp_path, monkeypatch):
    """Audit P14: the discontinued night-before job paths cannot write, even when called directly."""
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    c = db.connect(tmp_path / "nba.sqlite")
    db.migrate(c)
    db.claim_store(c, "nba")
    runs = c.execute("SELECT count(*) FROM job_runs").fetchone()[0]
    with pytest.raises(ValueError, match="discontinued"):
        jobs.market_forecasts(c, policy="pregame")
    with pytest.raises(RuntimeError, match="discontinued"):
        jobs.predict(c)
    assert c.execute("SELECT count(*) FROM job_runs").fetchone()[0] == runs
    assert c.execute("SELECT count(*) FROM predictions").fetchone()[0] == 0


def test_near_tipoff_market_forecast_is_void_on_any_start_change(sealed):
    """Audit P12: a market forecast is void when the start changes at all (here 30 minutes), in every reader: the
    settlement used by the API, tracking and the CLI/daily score (market_live.score)."""
    add_upcoming(sealed, start="2026-10-10T19:00:00", at="2026-10-10T02:00:00Z")
    pre = market_live.forecast(sealed, now="2026-10-10T03:00:00Z", game_ids=["nhl:2026020001"])  # night-before (pregame)
    assert pre["created"] > 0
    sealed.execute("INSERT INTO job_runs (job, started_at, finished_at, status) VALUES "
                   "('refresh', '2026-10-10T21:30:00Z', '2026-10-10T21:30:00Z', 'success')")
    quote(sealed, "2026-10-10T21:40:00Z", date="2026-10-10T23:00Z")
    near = market_live.forecast(sealed, now="2026-10-10T22:00:00Z", policy="near_tipoff", game_ids=["nhl:2026020001"])
    assert near["created"] == 5
    # The start moves 30 minutes later; the game is then played (4-2 in regulation).
    add_upcoming(sealed, start="2026-10-10T19:30:00", state=7, hs=4, vs=2, period=3, at="2026-10-11T03:00:00Z")
    near_rows = market_live.latest(sealed, policy="near_tipoff")
    night_rows = market_live.latest(sealed, policy="pregame")
    assert near_rows and night_rows
    assert {market_live.outcome(r) for r in near_rows} == {"void"}
    assert {market_live.outcome(r) for r in night_rows} == {"void"}               # same rule for the archive
    daily = market_live.score(sealed, policy="near_tipoff")["markets"]              # CLI score-markets / daily summary
    assert all(m["void"] == m["forecasts"] and m["win"] + m["loss"] + m["push"] == 0 for m in daily.values())
