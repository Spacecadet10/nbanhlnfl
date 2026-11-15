"""Source status (GET /api/sources, Today's source list): every input a forecast depends on has an entry, and an input with
no data is 'missing' or 'stale', never left out (which reads as healthy). NBA: official injury reports and the player box
cache; NHL: injuries (no live collector); both: the canonical every-game ledger."""
import os
from datetime import datetime, timezone

from app import availability, coverage, injuries
from app.providers.real import RealProvider
from test_live import store  # noqa: F401  (fixture: NBA store, NBA_DATA_DIR = tmp_path)
from test_live_markets import sealed  # noqa: F401
from test_nhl_eval import nhl_store  # noqa: F401
from test_research_live import GID, game, run  # noqa: F401  (fixture: NHL store with an upcoming game)


def _chip(conn, now, kind):
    return next(s for s in RealProvider(conn, now=now).sources() if s.kind == kind)


def test_nba_injury_reports_are_missing_with_nothing_due_and_stale_when_the_due_report_is_not_stored(store):
    quiet = _chip(store, "2026-10-10T12:00:00Z", "injuries")          # no game within the report window: nothing is due
    assert quiet.freshness == "missing" and quiet.last_updated is None and "No official injury report is due" in quiet.note
    now = "2026-10-21T20:00:00Z"                                        # game day (T_GAME 23:30Z): reports are due
    plan = injuries.plan(store, datetime(2026, 10, 21, 20, tzinfo=timezone.utc))
    assert plan["due"] and not plan["stored"]
    due = _chip(store, now, "injuries")
    assert due.freshness == "stale" and "not stored" in due.note
    from test_injuries import ok as response, pdf
    injuries.run(store, now=datetime(2026, 10, 21, 19, 45, tzinfo=timezone.utc), fetch=lambda name: response(pdf(name)),
                 sleep=lambda _s: None, clock=lambda: "2026-10-21T19:45:00Z")      # the real collector stores the reports
    ok = _chip(store, now, "injuries")
    assert ok.freshness == "fresh" and str(ok.last_updated).startswith("2026-10-21 19:45")


def test_the_nba_player_box_cache_is_missing_stale_or_fresh_by_its_file(store):
    now = "2026-10-21T20:00:00Z"
    assert _chip(store, now, "players").freshness == "missing"
    season = store.execute("SELECT MAX(season) FROM games WHERE tipoff_at <= ?", (now,)).fetchone()[0]
    path = availability.player_box_path(season)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("game_id,player\n")
    stamp = datetime(2026, 10, 21, 10, tzinfo=timezone.utc).timestamp()      # 10 h old at `now`
    os.utime(path, (stamp, stamp))
    fresh = _chip(store, now, "players")
    assert fresh.freshness == "fresh" and "NBA_PLAYER_BOX_REFRESH" in fresh.note
    old = datetime(2026, 10, 20, 10, tzinfo=timezone.utc).timestamp()        # 34 h old
    os.utime(path, (old, old))
    assert _chip(store, now, "players").freshness == "stale"


def test_nhl_injuries_follow_the_newest_capture_the_member_could_use(game, monkeypatch):
    """No capture → missing (the member abstains); a capture of the live ESPN page is fresh while the NHL member would use
    it (nhl_player_ratings.MAX_AGE_HOURS), stale after; captures after `now` never count; the note names the switch."""
    import httpx
    from app import config
    from app.sources import nhl_injuries
    from test_nhl_player_ratings import page
    chip = _chip(game, "2026-10-12T21:00:00Z", "injuries")
    assert chip.freshness == "missing" and "NHL_INJURY_CAPTURE" in chip.note
    assert not [s for s in RealProvider(game, now="2026-10-12T21:00:00Z").sources() if s.kind == "players"]
    get = lambda url: httpx.Response(200, content=page(), request=httpx.Request("GET", url))
    nhl_injuries.capture_live(get=get, clock=lambda: "2026-10-12T20:30:00Z")
    fresh = _chip(game, "2026-10-12T21:00:00Z", "injuries")
    assert fresh.freshness == "fresh" and str(fresh.last_updated).startswith("2026-10-12 20:30")
    assert _chip(game, "2026-10-13T20:30:01Z", "injuries").freshness == "stale"
    assert _chip(game, "2026-10-12T20:29:59Z", "injuries").freshness == "missing"



def test_the_canonical_ledger_chip_reports_started_games_without_a_canonical_record(game):
    assert _chip(game, "2026-10-12T21:00:00Z", "canonical").freshness == "missing"     # nothing ever recorded
    run(game, "2026-10-12T21:30:00Z")
    coverage.record(game, now="2026-10-12T21:30:00Z")
    before = _chip(game, "2026-10-12T22:00:00Z", "canonical")
    assert before.freshness == "fresh" and "No regular-season, play-in or playoff game started" in before.note
    after = _chip(game, "2026-10-13T00:00:00Z", "canonical")                           # the game started at 23:00Z
    assert after.freshness == "fresh" and "1 of 1" in after.note
    # A second game that started without any canonical record (the cycle never ran for it): stale, never hidden.
    from test_live_markets import add_upcoming
    add_upcoming(game, gid=2026020002, start="2026-10-12T19:30:00", home=8, away=10, at="2026-10-12T22:30:00Z")
    missed = _chip(game, "2026-10-13T00:00:00Z", "canonical")
    assert missed.freshness == "stale" and "1 of 2" in missed.note


# --- Review fixes ------------------------------------------------------------------------------------------------------
def test_a_ledger_holding_only_missing_reasons_is_not_healthy(game):
    """Review (HIGH): rows recording why there is no forecast counted as 'recorded', so a ledger with zero forecasts read
    fresh. Now: fresh only when every started game has a forecast in every market."""
    coverage.record(game, now="2026-10-12T22:30:00Z")          # results last refreshed 21:00Z: stale, every market missing
    rows = game.execute("SELECT DISTINCT status FROM game_forecasts WHERE game_id=?", (GID,)).fetchall()
    assert [r[0] for r in rows] == ["missing"]
    chip = _chip(game, "2026-10-13T00:00:00Z", "canonical")
    assert chip.freshness == "stale" and "0 of 1" in chip.note and "missing" in chip.note


def test_games_before_the_ledger_began_are_not_counted_as_unrecorded(game):
    from test_live_markets import add_upcoming
    add_upcoming(game, gid=2026020003, start="2026-10-12T14:00:00", home=8, away=10, at="2026-10-12T12:00:00Z")  # 18:00Z
    run(game, "2026-10-12T21:30:00Z")
    coverage.record(game, now="2026-10-12T21:30:00Z")          # the ledger begins here, after game 3 started
    chip = _chip(game, "2026-10-13T00:00:00Z", "canonical")
    assert chip.freshness == "fresh" and "1 of 1" in chip.note


def test_a_player_box_file_written_after_now_is_not_available_at_now(store):
    now = "2026-03-01T03:00:00Z"                               # a replay time before the wall clock
    season = store.execute("SELECT MAX(season) FROM games WHERE tipoff_at <= ?", (now,)).fetchone()[0]
    path = availability.player_box_path(season)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("game_id,player\n")                       # file time = the wall clock, after this replay's `now`
    assert datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc) > datetime(2026, 3, 1, 3, tzinfo=timezone.utc)
    chip = _chip(store, now, "players")
    assert chip.freshness == "missing" and chip.last_updated is None and "after" in chip.note


def test_an_injury_report_stored_after_now_does_not_count_at_now(store):
    from test_injuries import ok as response, pdf
    injuries.run(store, now=datetime(2026, 10, 21, 19, 45, tzinfo=timezone.utc), fetch=lambda name: response(pdf(name)),
                 sleep=lambda _s: None, clock=lambda: "2026-10-21T19:45:00Z")
    assert _chip(store, "2026-10-21T19:44:00Z", "injuries").freshness == "stale"


def test_preseason_games_are_not_canonical_gaps(game):
    """Preseason has no canonical forecast by rule (no validated model; 'unvalidated', not missing): the ledger entry counts
    regular-season, play-in and playoff games only."""
    from test_coverage import _preseason
    _preseason(game)                                             # nhl:2026010099 at 23:30Z
    run(game, "2026-10-12T21:30:00Z")
    coverage.record(game, now="2026-10-12T21:30:00Z")
    chip = _chip(game, "2026-10-13T00:00:00Z", "canonical")
    assert chip.freshness == "fresh" and "1 of 1" in chip.note


def test_a_preseason_game_the_cycle_never_recorded_is_a_gap(game):
    """Re-review: preseason games with rows are 'unvalidated' by rule, but one with no row at all means the cycle did not
    run for it; that is a gap, not health."""
    from test_coverage import _preseason
    run(game, "2026-10-12T21:30:00Z")
    coverage.record(game, now="2026-10-12T21:30:00Z")
    _preseason(game)                                             # appears after the cycle ran: never recorded
    chip = _chip(game, "2026-10-13T00:00:00Z", "canonical")
    assert chip.freshness == "stale" and "1 preseason game not recorded" in chip.note
