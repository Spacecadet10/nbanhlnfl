"""NCAAB scheduled work: refresh, odds, the two-hour cycle and its window, the tick, the daily job, backup and the CLI's
refusals (synthetic scratch store; a stand-in for the data agent's `app/ncaab.py`; no network)."""
import json
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

from app import backup, cli, jobs, journal, near_tipoff, ncaab_app
from ncaab_support import GAME, GAME_START, NOW, add_game, install_college, make_store, refreshed, seed

NOW_DT = datetime(2026, 11, 17, 22, 30, tzinfo=timezone.utc)


class FakeData(types.ModuleType):
    """`app.ncaab.ingest_day(conn, yyyymmdd, fetch=)` as the jobs expect it; records the days asked for."""

    def __init__(self):
        super().__init__("app.ncaab")
        self.days = []

    def ingest_day(self, conn, day, *, fetch=None):
        self.days.append(day)
        fetch(day)
        return {"day": day, "games": 0}


@pytest.fixture
def data(monkeypatch):
    fake = FakeData()
    import app
    monkeypatch.setitem(sys.modules, "app.ncaab", fake)
    monkeypatch.setattr(app, "ncaab", fake, raising=False)
    source = types.ModuleType("app.sources.espn_ncaab")
    source.fetch_day = lambda day: b"{}"
    import app.sources
    monkeypatch.setitem(sys.modules, "app.sources.espn_ncaab", source)
    monkeypatch.setattr(app.sources, "espn_ncaab", source, raising=False)
    return fake


@pytest.fixture
def store(tmp_path, monkeypatch):
    conn = make_store(tmp_path, monkeypatch)
    seed(conn)
    yield conn
    conn.close()


def test_a_broad_refresh_asks_for_four_eastern_days_and_a_focused_one_for_the_days_in_play(store, data):
    out = jobs.refresh(store, force=True, now=NOW_DT, sleep=lambda _: None)
    assert out["status"] == "success" and out["scope"] == "broad"
    assert data.days == ["20261116", "20261117", "20261118", "20261119"] and out["days"] == data.days
    data.days.clear()
    store.execute("DELETE FROM job_runs")
    refreshed(store, "2026-11-17T22:00:00Z")
    focused = jobs.refresh(store, now=NOW_DT, sleep=lambda _: None)
    assert focused["status"] == "success" and focused["scope"] == "dates" and data.days == ["20261117"]


def test_a_failing_source_fails_the_refresh_visibly_and_never_counts_as_fresh(store, monkeypatch):
    from app.sources import espn_ncaab
    def down(yyyymmdd, timeout=60):
        raise OSError("NCAAB scoreboard unreachable")
    monkeypatch.setattr(espn_ncaab, "fetch_day", down)
    out = jobs.refresh(store, force=True, now=NOW_DT, sleep=lambda _: None)
    assert out["status"] == "failed" and "NCAAB" in out["error"]
    assert near_tipoff.results_refreshed_at(store, "2026-11-17T22:31:00Z") == "2026-11-17T22:20:00Z"   # the earlier one stands


def test_the_odds_snapshot_requests_at_most_two_days_of_the_one_scoreboard(store, data):
    add_game(store, "700", "2026-11-19T23:00:00Z", "3", "4")
    add_game(store, "701", "2026-11-18T23:00:00Z", "1", "3")
    out = jobs.odds_snapshot(store, force=True, now=NOW_DT, sleep=lambda _: None, props=True)
    assert out["status"] == "success" and len(data.days) <= jobs.MAX_REQUESTS_PER_ODDS
    assert data.days == ["20261117", "20261118"]


def test_prop_collection_is_not_defined_for_ncaab_and_makes_no_request(store):
    out = jobs.collect_props(store, now=NOW_DT)
    assert out["status"] == "skipped" and "ncaab" in out["reason"]


def test_the_two_hour_window_is_the_nba_one_exactly(store, monkeypatch):
    install_college(monkeypatch)
    assert near_tipoff.WINDOW == timedelta(hours=2)
    add_game(store, "800", "2026-11-18T00:30:00Z", "3", "5")             # tips off exactly 2 h after NOW
    add_game(store, "801", "2026-11-18T00:30:01Z", "4", "3")             # one second later: outside
    add_game(store, "802", "2026-11-18T00:30:00Z", "1", "4")
    out = near_tipoff.predict_now(store, now=NOW, write=False)
    ids = {f["game_id"] for f in out["forecasts"]} | {r["game_id"] for r in out["refused"]}
    assert "ncaab:800" in ids and "ncaab:802" in ids and "ncaab:801" not in ids and GAME in ids
    pending = {r["game_id"] for r in jobs.pending_forecasts(store, NOW_DT)}
    assert GAME in pending and "ncaab:801" not in pending and "ncaab:402" not in pending       # a non-D-I game never waits for a forecast


def test_tick_runs_for_ncaab_and_does_not_reach_the_nba_or_nhl_research_steps(store, data, monkeypatch):
    install_college(monkeypatch)
    seen = {}
    monkeypatch.setattr(jobs, "odds_cycle", lambda conn, name, **kw: seen.setdefault("cycle", name) and {"odds": {}})
    out = jobs.tick(store, now=NOW_DT)
    assert out["sport"] == "ncaab" and seen["cycle"] == "ncaab" and out["trigger"] == "cycle"


def test_the_forecast_cycle_skips_selection_and_recovery_for_ncaab(store, data, monkeypatch):
    install_college(monkeypatch)
    monkeypatch.setattr(jobs.research_live, "decide_due", lambda *a, **k: pytest.fail("research selection ran for NCAAB"))
    monkeypatch.setattr(jobs.coverage, "forecast_window", lambda *a, **k: None)
    out = jobs.forecast_due(store)
    assert out["selector"]["status"] == "not_defined" and out["recovery"]["status"] == "not_defined"


def test_daily_scores_without_research_or_the_training_monitor(store, data, monkeypatch):
    install_college(monkeypatch)
    monkeypatch.setattr(jobs, "adaptive_train", lambda *a, **k: pytest.fail("adaptive training ran for NCAAB"))
    out = jobs.daily(store, now=NOW_DT, sleep=lambda _: None) if False else None
    out = jobs.daily(store, force_refresh=False, now=NOW_DT, sleep=lambda _: None)
    assert out["training_check"]["status"] == "not_defined" and out["adaptive_train"]["status"] == "not_defined"
    assert out["score"]["status"] == "success" and out["two_hour_score"]["policy"] == "near_tipoff"


def test_backup_and_journal_include_the_ncaab_store(store, tmp_path):
    store.commit()
    out = backup.run(tmp_path, tmp_path / "backups", stamp="s")
    assert "ncaab" in out["stores"] and out["stores"]["ncaab"]["identity"] == "ncaab" and out["stores"]["ncaab"]["integrity"] == "ok"
    assert "ncaab" in journal._stores(tmp_path)


@pytest.mark.parametrize("command", ["agreement", "thresholds", "research-report", "selector-decide", "adaptive-train", "predict"])
def test_cli_refuses_research_commands_for_ncaab_by_name(command, capsys):
    with pytest.raises(SystemExit):
        cli.main(["--sport", "ncaab", command])
    assert "not defined for NCAAB" in capsys.readouterr().err


def test_launchd_jobs_for_ncaab_mirror_the_nhl_ones_and_are_not_installed_by_this_change():
    from pathlib import Path
    import plistlib
    folder = Path(__file__).resolve().parents[2] / "scripts" / "launchd"
    odds = plistlib.loads((folder / "com.nba-predictor.ncaab-odds.plist").read_bytes())
    daily = plistlib.loads((folder / "com.nba-predictor.ncaab-daily.plist").read_bytes())
    nhl = plistlib.loads((folder / "com.nba-predictor.nhl-odds.plist").read_bytes())
    assert odds["ProgramArguments"][-3:] == ["--sport", "ncaab", "tick"] and odds["StartInterval"] == nhl["StartInterval"] == 60
    assert "ncaab odds" in daily["ProgramArguments"][-1] and "ncaab daily" in daily["ProgramArguments"][-1]
    assert odds["EnvironmentVariables"]["NBA_RELEASE_GUARD"] == "1" and "NHL_INJURY_CAPTURE" not in odds["EnvironmentVariables"]
    assert (daily["StartCalendarInterval"]["Hour"], daily["StartCalendarInterval"]["Minute"]) != (22, 15)   # not the NHL minute
