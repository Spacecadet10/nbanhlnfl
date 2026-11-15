"""Step 3: point-in-time efficiency features and their leakage guards (synthetic data, no network)."""
from datetime import datetime, timedelta, timezone

import pytest

from app import backtest, db, ingest
from app.models.base import PregameGame
from app.models.efficiency import BoxEvent, BoxLine, EfficiencyRatings, is_finite


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def game(gid, tip, home, away, season=2022):
    return PregameGame(gid, season, "regular", tip, home, away, False)


def row(g, hs, as_):
    avail = _fmt(datetime.strptime(g.tipoff_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) + timedelta(hours=4))
    return backtest.Row(g, backtest.cutoff_for(g.tipoff_at), hs, as_, avail)


def box(r, fetch_id, available_at=None, observed_at=None, home_pts=None, away_pts=None, poss=100):
    """A box event whose possessions come out at `poss` for both teams (FGA = poss, no OREB/TOV/FTA)."""
    avail = available_at or r.result_available_at
    return BoxEvent(r.game, avail, observed_at or avail, fetch_id,
                    BoxLine(home_pts if home_pts is not None else r.home_score, poss, 0, 0, 0),
                    BoxLine(away_pts if away_pts is not None else r.away_score, poss, 0, 0, 0))


class Probe:
    """Captures the efficiency features each game would receive; predicts a constant."""
    name = "probe"

    def __init__(self):
        self.eff, self.seen = EfficiencyRatings(), {}

    def predict(self, g):
        self.seen[g.game_id] = self.eff.features(g)
        return 0.5

    def update(self, r):
        pass

    def update_box(self, e):
        self.eff.update_box(e)

    def config(self):
        return {}


def run(rows, events):
    p = Probe()
    backtest.run(p, rows, events)
    return p.seen


def season_rows(n_days=20, start="2021-11-01T23:30:00Z", season=2022):
    """Four teams, one game per day, rotating matchups; home margins vary."""
    teams, t0, rows = ["T1", "T2", "T3", "T4"], datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ"), []
    for d in range(n_days):
        h, a = teams[d % 4], teams[(d + 1 + d // 4) % 4]
        if h == a:
            a = teams[(d + 2) % 4]
        rows.append(row(game(f"s{season}g{d}", _fmt(t0 + timedelta(days=d)), h, a, season), 100 + (d * 7) % 13, 100))
    return rows


def test_possessions_formula():
    assert BoxLine(100, 85, 20, 10, 14).possessions() == pytest.approx(85 - 10 + 14 + 0.44 * 20)


def test_own_box_score_available_before_its_cutoff_is_rejected():
    r = row(game("g1", "2021-11-02T23:30:00Z", "T1", "T2"), 110, 100)
    with pytest.raises(backtest.LeakageError, match="box score available before its own cutoff"):
        backtest.run(Probe(), [r], [box(r, 1, available_at=r.cutoff_at)])


def test_same_day_games_never_see_each_others_box_scores():
    early = row(game("early", "2021-11-02T23:00:00Z", "T1", "T2"), 130, 90)   # 7 pm ET
    late = row(game("late", "2021-11-03T02:30:00Z", "T1", "T3"), 100, 100)    # 10:30 pm ET, same Eastern date
    assert early.cutoff_at == late.cutoff_at
    with_early = run([early, late], [box(early, 1)])
    without = run([early, late], [])
    assert with_early["late"] == without["late"] and with_early["late"]["games_home"] == 0


def test_features_are_unchanged_by_future_rows_and_box_scores():
    rows = season_rows(20)
    events = [box(r, i) for i, r in enumerate(rows)]
    full = run(rows, events)
    cut = rows[11].game.tipoff_at
    prefix = run([r for r in rows if r.game.tipoff_at <= cut], [e for e in events if e.game.tipoff_at <= cut])
    assert prefix == {k: v for k, v in full.items() if k in prefix}
    assert all(is_finite(f) for f in full.values())


def test_a_correction_is_used_only_after_it_is_available():
    first = row(game("g1", "2021-11-01T23:30:00Z", "T1", "T2"), 100, 100)
    before = row(game("g2", "2021-11-03T23:30:00Z", "T1", "T3"), 100, 100)
    after = row(game("g3", "2021-11-06T23:30:00Z", "T1", "T4"), 100, 100)
    original = box(first, 1)                                                   # T1 +0, available 11-02 03:30
    corrected = box(first, 2, available_at="2021-11-05T12:00:00Z", home_pts=120)  # T1 +20, known from 11-05
    older_copy = box(first, 3, available_at="2021-11-05T13:00:00Z",           # arrives later but was observed
                     observed_at="2021-11-02T00:00:00Z", home_pts=80)          # earlier: must not replace
    seen = run([first, before, after], [original, corrected, older_copy])
    # Before 11-05 only the original is known; afterwards the correction raises T1, and the late older copy is ignored.
    assert seen["g2"] == run([first, before], [original])["g2"]
    assert seen["g3"] == run([first, before, after], [original, corrected])["g3"]
    assert seen["g3"]["net_home"] > seen["g2"]["net_home"] + 1


def delayed(r, result_at):
    return backtest.Row(r.game, r.cutoff_at, r.home_score, r.away_score, result_at)


def test_codex_p1_box_waits_for_its_result_and_does_not_block_other_events():
    # Codex step 3 P1: Nov 1 game, box available Nov 2 12:00Z, result available Nov 4 12:00Z.
    slow = delayed(row(game("slow", "2021-11-01T23:30:00Z", "T1", "T2"), 120, 100), "2021-11-04T12:00:00Z")
    quick = row(game("quick", "2021-11-02T00:30:00Z", "T3", "T4"), 115, 100)   # box and result available Nov 2 04:30Z
    nov3 = row(game("nov3", "2021-11-03T23:30:00Z", "T1", "T3"), 100, 100)     # cutoff Nov 3 04:00Z
    nov5 = row(game("nov5", "2021-11-05T23:30:00Z", "T1", "T3"), 100, 100)     # cutoff Nov 5 04:00Z
    events = [box(slow, 1, available_at="2021-11-02T12:00:00Z"), box(quick, 2)]
    seen = run([slow, quick, nov3, nov5], events)
    # Nov 3: the slow game's stats are withheld (result not available), while the later-listed quick game is delivered.
    assert (seen["nov3"]["games_home"], seen["nov3"]["games_away"]) == (0, 1)
    assert seen["nov3"] == run([quick, nov3], [events[1]])["nov3"]
    # Nov 5: both the box and the result are available, so the slow game now counts.
    assert (seen["nov5"]["games_home"], seen["nov5"]["games_away"]) == (1, 1)
    assert seen["nov5"]["net_home"] > seen["nov3"]["net_home"]


def test_box_without_a_matching_eligible_result_is_rejected():
    r = row(game("g1", "2021-11-01T23:30:00Z", "T1", "T2"), 110, 100)
    orphan = row(game("orphan", "2021-11-01T23:30:00Z", "T3", "T4"), 110, 100)
    with pytest.raises(backtest.LeakageError, match="without a matching eligible result"):
        backtest.run(Probe(), [r], [box(orphan, 1)])


def test_incomplete_correction_keeps_the_latest_complete_observation_in_any_order():
    first = row(game("g1", "2021-11-01T23:30:00Z", "T1", "T2"), 100, 100)
    later = row(game("g2", "2021-11-10T23:30:00Z", "T1", "T3"), 100, 100)
    a = box(first, 1, observed_at="2021-11-02T04:00:00Z")                                   # complete, T1 +0
    c = box(first, 2, available_at="2021-11-06T00:00:00Z", observed_at="2021-11-03T00:00:00Z", home_pts=110)  # complete
    b = BoxEvent(first.game, "2021-11-05T00:00:00Z", "2021-11-04T00:00:00Z", 3,            # newest, but incomplete
                 BoxLine(130, 100, 0, 0, None), BoxLine(100, 100, 0, 0, 0))
    expected = run([first, later], [a, c])["g2"]          # latest complete observation is c (observed Nov 3)
    assert expected["net_home"] > run([first, later], [a])["g2"]["net_home"]
    for order in ([a, b, c], [c, b, a], [b, a, c]):
        e = EfficiencyRatings()
        for ev in order:
            e.update_box(ev)
        assert e.lines["g1"] is c and e.skipped_incomplete == 1
    assert run([first, later], [a, b, c])["g2"] == expected
    # The incomplete observation alone does not invalidate the earlier complete one.
    assert run([first, later], [a, b])["g2"] == run([first, later], [a])["g2"]


def test_opponent_adjustment_credits_a_win_over_a_strong_team():
    t = iter(range(100))
    tip = lambda: _fmt(datetime(2021, 11, 1, 23, 30) + timedelta(days=next(t)))  # noqa: E731
    rows = [row(game("a", tip(), "STRONG", "WEAK"), 130, 90), row(game("b", tip(), "STRONG", "WEAK"), 128, 92),
            row(game("c", tip(), "X", "WEAK"), 110, 100), row(game("d", tip(), "Y", "STRONG"), 110, 100)]
    probe = row(game("p", tip(), "X", "Y"), 100, 100)
    seen = run(rows + [probe], [box(r, i) for i, r in enumerate(rows)])
    assert seen["p"]["net_away"] > seen["p"]["net_home"]  # Y beat the strong team by the margin X beat the weak one


def test_previous_season_rating_carries_into_the_new_season():
    last = [row(game(f"old{i}", _fmt(datetime(2021, 3, 1, 23, 30) + timedelta(days=2 * i)), "T1", "T2", 2021), 120, 95)
            for i in range(8)]
    opener = row(game("opener", "2021-10-20T23:30:00Z", "T1", "T2", 2022), 100, 100)
    seen = run(last + [opener], [box(r, i) for i, r in enumerate(last)])
    f = seen["opener"]
    assert f["games_home"] == 0 and f["net_home"] > 5 and f["net_diff"] > 10  # months later, not decayed to average


def test_incomplete_lines_are_skipped_and_counted():
    r = row(game("g1", "2021-11-01T23:30:00Z", "T1", "T2"), 110, 100)
    nxt = row(game("g2", "2021-11-03T23:30:00Z", "T1", "T2"), 100, 100)
    bad = BoxEvent(r.game, r.result_available_at, r.result_available_at, 1, BoxLine(110, 90, 20, None, 12),
                   BoxLine(100, 90, 20, 10, 12))
    p = Probe()
    backtest.run(p, [r, nxt], [bad])
    assert p.eff.skipped_incomplete == 1 and p.seen["g2"]["games_home"] == 0


def test_box_events_respect_period_season_isolation(tmp_path, monkeypatch):
    from test_live import event, ingest_events
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    conn = db.connect(tmp_path / "iso.sqlite")
    db.migrate(conn)
    ingest_events(conn, [event("v1", "2022-01-10T23:30:00Z", "1", "2", "STATUS_FINAL", 110, 100, season=2022),
                         event("t1", "2025-01-10T23:30:00Z", "1", "2", "STATUS_FINAL", 105, 100, season=2025)],
                  "2026-09-30T00:00:00Z", month="202201")
    body = ("game_id,season,season_type,team_id,team_home_away,team_score,field_goals_attempted,free_throws_attempted,"
            "offensive_rebounds,defensive_rebounds,total_turnovers\n"
            "v1,2022,2,1,home,110,85,20,10,30,12\nv1,2022,2,2,away,100,85,20,10,30,12\n"
            "t1,2025,2,1,home,105,85,20,10,30,12\nt1,2025,2,2,away,100,85,20,10,30,12\n").encode()
    ingest.ingest_team_box(conn, 2022, fetch=lambda _s: body, retrieved_at="2026-09-30T01:00:00Z",
                           historical_backfill=True)
    assert [e.game.game_id for e in backtest.load_box_events(conn, "validation")] == ["espn:v1"]
    assert sorted(e.game.game_id for e in backtest.load_box_events(conn, "live")) == ["espn:t1", "espn:v1"]
    ev = backtest.load_box_events(conn, "validation")[0]
    assert ev.available_at == "2022-01-11T03:30:00Z" and ev.home.tov == 12  # assumed with the result: tipoff + 4 h
    assert backtest.box_version(backtest.load_box_events(conn, "validation")) != \
        backtest.box_version(backtest.load_box_events(conn, "live"))
    conn.close()
