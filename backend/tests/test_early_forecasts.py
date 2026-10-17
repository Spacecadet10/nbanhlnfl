"""Early-forecast test (docs/evidence/early-forecast-test/PREREGISTRATION.md): NHL winner forecasts made earlier than the
two-hour cycle, appended to `early_forecasts` and paper-traded at the first quote retrieved after them.
Synthetic NHL store of test_adaptive (game GID at 23:00 UTC, no network)."""
import json
import sqlite3

import pytest

from app import adaptive, agreement, config, early_forecasts as ef, nhl_shots
from test_adaptive import CYCLE_AT, TRAIN_AT, nhl_store, sealed, store  # noqa: F401  (fixtures)
from test_live_markets import add_upcoming, quote
from test_research_live import GID, run

EARLY = "2026-10-12T20:45:00Z"        # 2 h 15 min before the 23:00 UTC start: outside the two-hour cycle


@pytest.fixture
def xstore(store):
    """The store with every final's shots imported (a backfill, dated at the result time): the xG member can forecast."""
    from test_nhl_xg import fake_fetch
    nhl_shots.import_pending(store, None, pause=0, fetch=fake_fetch(store), log=lambda *_: None)
    return store


def _names(conn):
    return {r["team_id"]: r["name"] for r in conn.execute("SELECT team_id, name FROM teams")}


def _capture(at, rows):
    """An NHL injury capture as nhl_injuries.capture_live stores its metadata: {team name: [(player, status, weight)]}."""
    d = config.data_dir() / "research" / "nhl_players" / "injuries"
    d.mkdir(parents=True, exist_ok=True)
    stamp = at.replace("-", "").replace(":", "").replace("T", "").replace("Z", "")
    body = [{"team": t, "player": p, "status": s, "weight": w} for t, items in rows.items() for p, s, w in items]
    (d / f"live-{stamp}.json").write_text(json.dumps({"captured_at": at, "retrieved_at": at, "teams": sorted(rows),
                                                      "rows": body}))


def _rows(conn, arm=None):
    q = "SELECT * FROM early_forecasts" + (" WHERE arm = ?" if arm else "") + " ORDER BY early_id"
    return conn.execute(q, (arm,) if arm else ()).fetchall()


def test_the_open_arm_forecast_is_the_research_xg_forecast_at_the_same_time(xstore):
    store = xstore
    ref = agreement.predict(store, now=CYCLE_AT, write=False, game_ids=[GID])
    want = [f["home_win_prob"] for f in ref["forecasts"] if f["model"] == "hockey_logistic_xg"]
    assert want, ref.get("skipped")
    p, reason = ef.xg_forecasts(store, CYCLE_AT, [GID])[GID]
    assert reason is None and p == pytest.approx(want[0], abs=1e-9)


def test_an_early_xg_forecast_uses_only_results_and_shots_available_at_its_time(xstore):
    from test_nhl_xg import fake_fetch
    store = xstore
    before, reason = ef.xg_forecasts(store, EARLY, [GID])[GID]
    assert reason is None and 0 < before < 1                                  # 2 h 15 min out: the cycle cannot make it
    add_upcoming(store, gid=2026020050, start="2026-10-12T13:00:00", state=7, hs=6, vs=0, period=3, home=6, away=16,
                 at="2026-10-12T20:50:00Z")                                    # a final retrieved after EARLY ...
    g = store.execute("SELECT * FROM games WHERE game_id='nhl:2026020050'").fetchone()
    nhl_shots.import_game(store, g, fake_fetch(store)(g["game_id"]), "2026-10-12T20:51:00Z")    # ... and its shots
    assert ef.xg_forecasts(store, EARLY, [GID])[GID] == (before, None)
    assert ef.xg_forecasts(store, "2026-10-12T20:55:00Z", [GID])[GID][0] != before


def test_the_open_arm_forecasts_each_game_once_when_it_first_has_a_bookmaker_line(xstore):
    store = xstore
    add_upcoming(store, gid=2026020002, start="2026-10-13T19:00:00", at="2026-10-12T12:00:00Z")   # no line yet
    assert ef.run(store, now=EARLY, capture=False)["open"]["candidates"] == []    # the fixture's line comes at 21:05
    quote(store, "2026-10-12T20:41:00Z", date="2026-10-12T23:00Z")
    dry = ef.run(store, now=EARLY, write=False, capture=False)
    assert [r["game_id"] for r in dry["open"]["rows"]] == [GID] and _rows(store) == []
    out = ef.run(store, now=EARLY, capture=False)
    rows = _rows(store, "open")
    assert out["open"]["created"] == 1 and len(rows) == 1
    r = rows[0]
    assert (r["game_id"], r["member"], r["status"], r["information_at"], r["generated_at"]) == (
        GID, "hockey_logistic_xg", "forecast", EARLY, EARLY)
    assert r["p_a"] == pytest.approx(ef.xg_forecasts(store, EARLY, [GID])[GID][0]) and r["p_b"] == pytest.approx(1 - r["p_a"])
    assert json.loads(r["trigger_detail"])["first_quote_at"] == "2026-10-12T20:41:00Z"
    assert ef.run(store, now="2026-10-12T20:48:00Z", capture=False)["open"]["created"] == 0    # once per game


def test_changed_teams_compares_each_teams_listed_players_statuses_and_weights():
    prev = {"rows": [{"team": "Boston Bruins", "player": "A", "status": "Out", "weight": 1.0},
                     {"team": "Chicago Blackhawks", "player": "B", "status": "Day-To-Day", "weight": 0.5}]}
    same = {"rows": list(reversed(prev["rows"]))}
    assert ef.changed_teams(prev, same) == set()
    moved = {"rows": [prev["rows"][0], {**prev["rows"][1], "status": "Out", "weight": 1.0}]}
    assert ef.changed_teams(prev, moved) == {"chicagoblackhawks"}
    gone = {"rows": [prev["rows"][1], {"team": "Toronto Maple Leafs", "player": "C", "status": "Out", "weight": 1.0}]}
    assert ef.changed_teams(prev, gone) == {"bostonbruins", "torontomapleleafs"}


def test_the_news_arm_forecasts_only_games_of_teams_whose_report_changed_and_each_capture_once(store):
    names = _names(store)
    home, away = names["nhl:6"], names["nhl:16"]
    quote(store, "2026-10-12T20:41:00Z", date="2026-10-12T23:00Z")
    _capture("2026-10-12T20:00:00Z", {home: [("Pat One", "Out", 1.0)], away: [("Pat Two", "Out", 1.0)]})
    _capture("2026-10-12T20:30:00Z", {home: [("Pat One", "Out", 1.0)], away: [("Pat Two", "Out", 1.0)]})
    assert ef.run(store, now=EARLY, capture=False)["news"]["changed_teams"] == []     # nothing changed: no forecast
    _capture("2026-10-12T20:44:00Z", {home: [("Pat One", "Out", 1.0), ("Pat Three", "Day-To-Day", 0.5)],
                                      away: [("Pat Two", "Out", 1.0)]})
    out = ef.run(store, now=EARLY, capture=False)
    rows = _rows(store, "news")
    assert out["news"]["changed_teams"] == [ef.npr.team_key(home)] and [r["game_id"] for r in rows] == [GID]
    r = rows[0]
    assert r["member"] == ef.NEWS_MEMBER and r["information_at"] == EARLY
    assert json.loads(r["trigger_detail"])["capture_at"] == "2026-10-12T20:44:00Z"
    if r["status"] == "unavailable":                     # the synthetic store has no player caches: a stated reason
        assert r["reason"] and r["p_a"] is None
    assert ef.run(store, now="2026-10-12T20:50:00Z", capture=False)["news"]["created"] == 0   # same capture: once


def test_a_capture_older_than_the_news_window_triggers_nothing(store):
    names = _names(store)
    quote(store, "2026-10-12T20:41:00Z", date="2026-10-12T23:00Z")
    _capture("2026-10-12T19:00:00Z", {names["nhl:6"]: [("Pat One", "Out", 1.0)]})
    _capture("2026-10-12T19:30:00Z", {names["nhl:6"]: []})
    _capture("2026-10-12T19:40:00Z", {names["nhl:16"]: [("Pat Two", "Out", 1.0)]})
    out = ef.run(store, now=EARLY, capture=False)
    assert out["news"]["created"] == 0 and "older than" in out["news"]["reason"]


def test_an_adaptive_member_served_early_is_the_vector_the_live_cycle_records(store):
    adaptive.train(store, now=TRAIN_AT)
    run(store, CYCLE_AT)
    adaptive.record(store, game_ids=[GID], now=CYCLE_AT)
    stored = store.execute("SELECT members FROM adaptive_forecasts WHERE game_id=? AND market='winner'", (GID,)).fetchone()
    live = {m["name"]: m["p"] for m in json.loads(stored["members"])}
    assert "hockey_logistic@adaptive" in live
    game = store.execute("SELECT * FROM games WHERE game_id=?", (GID,)).fetchone()
    got = ef.adaptive_member(store, CYCLE_AT, game, "hockey_logistic@adaptive")
    assert got["reason"] is None and got["p"] == pytest.approx(live["hockey_logistic@adaptive"], abs=1e-12)
    assert got["provenance"]["fit_id"] and got["provenance"]["artifact_sha256"]
    early = ef.adaptive_member(store, EARLY, game, "hockey_logistic@adaptive")
    assert early["reason"] is None and early["provenance"]["finished_at"] <= EARLY     # a fit published by then


def test_an_excluded_adaptive_member_is_unavailable_with_its_exclusion_reason(store):
    # The synthetic store has no NHL player/injury research cache: the injury member is excluded by adaptive.members,
    # and the early row carries that reason (not a generic one).
    excluded = adaptive.members(store)[1]
    assert ef.NEWS_MEMBER.split("@")[0] in excluded
    game = store.execute("SELECT * FROM games WHERE game_id=?", (GID,)).fetchone()
    got = ef.adaptive_member(store, EARLY, game, ef.NEWS_MEMBER)
    assert got["p"] is None and got["reason"] == f"excluded: {excluded[ef.NEWS_MEMBER.split('@')[0]]}"


def test_a_fit_published_after_the_forecast_time_is_never_applied(store, monkeypatch):
    # The read-only evaluation chooses fits by the results they were trained on; a fit row published after `now` (possible
    # only for a backdated --now) must not inform an early forecast.
    game = store.execute("SELECT * FROM games WHERE game_id=?", (GID,)).fetchone()
    row = {"name": "hockey_logistic@adaptive", "p": [0.6, 0.0, 0.4], "fit_id": 7, "artifact_sha256": "s",
           "training_cutoff": "2026-10-12T19:00:00Z", "information_at": "2026-10-12T20:40:00Z"}
    for finished, ok in (("2026-10-12T20:44:59Z", True), ("2026-10-12T20:45:01Z", False)):
        fake = {"definition": "d", "forecasts": [{"game_id": GID, "market": "winner", "missing": {},
                                                  "member_rows": [{**row, "finished_at": finished}]}]}
        monkeypatch.setattr(adaptive, "record", lambda *a, **k: fake)
        got = ef.adaptive_member(store, EARLY, game, "hockey_logistic@adaptive")
        assert (got["p"] is not None) == ok and (ok or "published after" in got["reason"]), got


def test_early_forecasts_are_append_only_and_made_before_the_start(store):
    quote(store, "2026-10-12T20:41:00Z", date="2026-10-12T23:00Z")
    ef.run(store, now=EARLY, capture=False)
    with pytest.raises(sqlite3.IntegrityError):
        store.execute("UPDATE early_forecasts SET p_a = 0.9")
    with pytest.raises(sqlite3.IntegrityError):
        store.execute("DELETE FROM early_forecasts")
    row = dict(_rows(store, "open")[0])
    for k in ("early_id",):
        row.pop(k)
    with pytest.raises(sqlite3.IntegrityError):              # made at the start: refused by the store
        ef.store_row(store, {**row, "idempotency_key": "late", "generated_at": "2026-10-12T23:00:00Z"})
    with pytest.raises(sqlite3.IntegrityError):              # a start time that is not the game's
        ef.store_row(store, {**row, "idempotency_key": "moved", "tipoff_at": "2026-10-12T23:30:00Z"})


def _week(i):
    from datetime import datetime, timedelta, timezone
    return (datetime(2026, 10, 12, 23, tzinfo=timezone.utc) + timedelta(days=7 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_the_preregistered_verdict_needs_enough_bets_and_a_bootstrap_lower_bound_above_zero():
    few = [{"tipoff_at": _week(i % 20), "excess_clv": 0.05, "profit": 0.1} for i in range(ef.MIN_BETS - 1)]
    assert ef.verdict(few)["verdict"] == "inconclusive"
    good = [{"tipoff_at": _week(i % 25), "excess_clv": 0.03 + 0.01 * (i % 3), "profit": 0.1} for i in range(ef.MIN_BETS)]
    v = ef.verdict(good)
    assert v["verdict"] == "pass" and v["excess_clv_ci"][0] > 0 and v["bets"] == ef.MIN_BETS
    noise = [{"tipoff_at": _week(i % 25), "excess_clv": (-0.05, 0.05)[i % 2] - 0.01, "profit": -0.1} for i in range(300)]
    assert ef.verdict(noise)["verdict"] == "fail"
    assert ef.verdict(good) == ef.verdict(list(reversed(good)))          # fixed seed and order: one reproducible answer


def test_the_command_plans_read_only_appends_under_the_lock_and_reports(xstore, capsys):
    from app import cli
    quote(xstore, "2026-10-12T20:41:00Z", date="2026-10-12T23:00Z")
    xstore.commit()
    assert cli.main(["--sport", "nhl", "early-forecasts", "--dry-run", "--now", EARLY]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert [r["game_id"] for r in dry["open"]["rows"]] == [GID] and dry["open"]["created"] == 0 and _rows(xstore) == []
    assert cli.main(["--sport", "nhl", "early-forecasts", "--no-capture", "--now", EARLY]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["open"]["created"] == 1 and out["capture"] is None and len(_rows(xstore, "open")) == 1
    assert cli.main(["--sport", "nhl", "early-forecasts-report"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["rows"] == {"open": {"forecast": 1}}
    cov = report["coverage"]["open"]
    assert cov["unavailable_reasons"] == {} and list(cov["model_versions"].values()) == [1]
    assert cov["hours_from_first_quote"] == {"median": 0.07, "max": 0.07}        # 20:41 quote, 20:45 forecast
    v = report["verdict"]["early:open:hockey_logistic_xg"]          # bet at the 21:05 quote; no later close yet
    assert (v["bets"], v["verdict"]) == (0, "inconclusive")


def test_the_scheduled_command_obeys_the_release_guard_before_any_request_or_write(xstore, capsys, monkeypatch):
    from app import cli, jobs, release
    quote(xstore, "2026-10-12T20:41:00Z", date="2026-10-12T23:00Z")
    xstore.commit()
    monkeypatch.setenv(release.GUARD_ENV, "1")
    monkeypatch.setattr(jobs, "nhl_injury_capture", lambda: pytest.fail("captured before the release check"))
    monkeypatch.setattr(release, "check", lambda sport: {"commit": "c0ffee", "problems": ["not the release checkout"]})
    assert cli.main(["--sport", "nhl", "early-forecasts", "--now", EARLY]) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "release guard" and _rows(xstore) == []
    monkeypatch.setattr(release, "check", lambda sport: {"commit": "c0ffee", "problems": []})
    assert cli.main(["--sport", "nhl", "early-forecasts", "--no-capture", "--now", EARLY]) == 0
    capsys.readouterr()
    assert json.loads(_rows(xstore, "open")[0]["provenance"])["release"] == "c0ffee"
