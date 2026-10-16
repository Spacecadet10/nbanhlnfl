"""players-v1 research (docs/PLAYER_RATINGS_STUDY.md): ratings, rosters, report adjustment and walk-forward timing."""
from datetime import datetime, timedelta, timezone

import pytest

from app import availability as av
from app import backtest
from app import player_ratings as pr
from app.models.base import PregameGame

H, A, C = "espn:2", "espn:3", "espn:4"


def _tip(day: str) -> str:
    """19:30 ET on an ET date, as UTC."""
    local = datetime.fromisoformat(f"{day}T19:30:00").replace(tzinfo=av.ET)
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _box(games, season=2023):
    """games: [(game id, ET day, {team: [(athlete id, minutes)]})] -> Box built through load_box's row parser."""
    rows = []
    for gid, day, teams in games:
        local = datetime.fromisoformat(f"{day}T19:30:00").replace(tzinfo=av.ET).isoformat()
        for team, players in teams.items():
            for pid, m in players:
                rows.append({"game_id": gid, "game_date_time": local, "athlete_id": pid, "athlete_display_name": f"P {pid}",
                             "team_id": team.split(":")[1], "minutes": str(m), "did_not_play": "false" if m else "true"})
    return pr.load_box((season,), loader=lambda s: rows)


def _row(gid, day, home, away, hs, as_, season=2023, available=None):
    g = PregameGame(f"espn:{gid}", season, "regular", _tip(day), home, away, False)
    avail = available or (datetime.strptime(g.tipoff_at, "%Y-%m-%dT%H:%M:%SZ") + timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return backtest.Row(g, av.evening_cutoff(g.tipoff_at), hs, as_, avail)


FIVE_H = [("h1", 48), ("h2", 38.4), ("h3", 38.4), ("h4", 38.4), ("h5", 38.4), ("h6", 38.4)]
FIVE_A = [("a1", 40), ("a2", 40), ("a3", 40), ("a4", 40), ("a5", 40), ("a6", 40)]


def _season(n=8, margins=None):
    margins = margins or [10, -4, 7, 3, -9, 12, 1, 5]
    games, rows = [], []
    for i in range(n):
        day = f"2023-01-{i * 2 + 2:02d}"
        games.append((str(100 + i), day, {H: FIVE_H, A: FIVE_A}))
        rows.append(_row(100 + i, day, H, A, 100 + margins[i], 100))
    return games, rows


class Probe:
    """Records PlayerState.diffs at each prediction (same timing as the logistic variants)."""
    name = "probe"

    def __init__(self, box, facts=None, k=0.05):
        self.state = pr.PlayerState(box, k, facts)
        self.seen = {}

    def predict(self, g):
        self.seen[g.game_id] = self.state.diffs(g)
        return 0.5

    def update(self, r):
        self.state.update(r)

    def config(self):
        return {}


def _walk(games, rows, facts=None):
    p = Probe(_box(games), facts)
    backtest.run(p, rows)
    return p.seen


def test_newcomer_prior_season_carry_and_update_direction():
    r = pr.PlayerRatings(k=0.1)
    assert r.get("x", 2023) == pr.NEWCOMER_PRIOR
    e = r.update(2023, [("a", 48)], [("b", 48)], margin=10, neutral=False)
    assert e == pytest.approx(10 - pr.HOME_EDGE)                          # equal priors: only the home edge is expected
    assert r.get("a", 2023) == pytest.approx(-1 + 0.1 * e) and r.get("b", 2023) == pytest.approx(-1 - 0.1 * e)
    assert r.get("a", 2024) == pytest.approx((-1 + 0.1 * e) * pr.SEASON_CARRY)   # shrunk once at a new season
    r.update(2024, [("a", 24)], [("b", 24)], margin=0, neutral=True)
    assert r.season["a"] == 2024


def test_roster_uses_only_earlier_games_and_a_traded_player_keeps_his_rating():
    ros = pr.Rosters()
    for day in ("2023-01-02", "2023-01-04", "2023-01-06"):
        ros.add(day, 2023, {"2": [("star", 36), ("h2", 30)], "3": [("a1", 30)]})
    ros.add("2023-01-10", 2023, {"4": [("star", 34), ("c1", 30)], "3": [("a1", 30)]})       # traded to C
    before = dict(ros.expected("2", "2023-01-08", 2023))
    assert set(before) == {"star", "h2"} and sum(before.values()) == pytest.approx(240)
    assert ros.expected("2", "2023-01-02", 2023) == []                    # nothing before the first game
    after_h, after_c = dict(ros.expected("2", "2023-01-12", 2023)), dict(ros.expected("4", "2023-01-12", 2023))
    assert "star" not in after_h and "star" in after_c                    # latest appearance decides the team
    ratings = pr.PlayerRatings(k=0.1)
    ratings.update(2023, [("star", 48)], [("a1", 48)], margin=20, neutral=False)
    assert ratings.get("star", 2023) > pr.NEWCOMER_PRIOR                  # rating is per player, not per team


def test_rosters_follow_minutes_history_rule():
    ros, apps = pr.Rosters(), []
    for i, m in enumerate([30, 20, 10, 40]):
        ros.add(f"2023-01-{i * 2 + 2:02d}", 2023, {"2": [("p", m)]})
    assert ros.minutes("p", "2023-01-07", 2023) == pytest.approx(20)        # first three only
    assert ros.minutes("p", "2023-01-07", 2024) == pytest.approx(20)        # previous season fallback
    assert ros.minutes("p", "2023-01-02", 2023) == 0.0


def test_report_removes_and_halves_minutes_then_rescales():
    players = [("s", 36), ("b", 24), ("c", 60), ("d", 120)]
    out = dict(pr.without_absent(players, {"s": 1.0, "b": 0.5}))
    assert "s" not in out and sum(out.values()) == pytest.approx(240)
    assert out["b"] / out["c"] == pytest.approx(12 / 60)
    assert pr.without_absent([("s", 240)], {"s": 1.0}) == []


def test_missing_or_unsubmitted_report_falls_back_to_the_full_roster():
    games, rows = _season(4)
    day = "2023-01-08"
    ok = pr.PlayerFacts(True, "ok", "2023-01-08T22:30:00Z", players={"2": {"h1": 1.0}}, teams_on_report={"2", "3"})
    seen = _walk(games, rows, {day: ok})
    d = seen["espn:103"]
    assert d["report_used"] and d["player_diff_report"] != d["player_diff_full"]
    ok.unsubmitted.add("3")
    d = _walk(games, rows, {day: ok})["espn:103"]
    assert not d["report_used"] and d["player_diff_report"] == d["player_diff_full"]
    d = _walk(games, rows, {})["espn:103"]
    assert not d["report_used"] and d["player_diff_report"] == d["player_diff_full"]
    unlisted = pr.PlayerFacts(True, "ok", "2023-01-08T22:30:00Z", players={"2": {"h1": 1.0}}, teams_on_report={"2"})
    d = _walk(games, rows, {day: unlisted})["espn:103"]                # the away team appears nowhere on the report
    assert not d["report_used"] and d["report_missing"] == 1.0 and d["player_diff_report"] == d["player_diff_full"]


def test_own_outcome_and_box_never_change_that_games_features():
    games, rows = _season()
    base = _walk(games, rows)
    rows2 = list(rows)
    rows2[4] = backtest.Row(rows[4].game, rows[4].cutoff_at, 160, 90, rows[4].result_available_at)
    games2 = list(games)
    games2[4] = (games[4][0], games[4][1], {H: [("h1", 48), ("zz", 192)], A: FIVE_A})
    changed = _walk(games2, rows2)
    for i in range(5):                                                   # games up to and including the mutated one
        assert changed[f"espn:{100 + i}"] == base[f"espn:{100 + i}"]
    assert changed["espn:105"] != base["espn:105"]                       # later games do see it


def test_ratings_use_only_results_delivered_before_the_cutoff():
    games, rows = _season()
    late = list(rows)                                                    # game 101's result arrives after every cutoff
    late[1] = backtest.Row(rows[1].game, rows[1].cutoff_at, rows[1].home_score, rows[1].away_score, "2023-03-01T00:00:00Z")
    a = _walk(games, late)
    late_changed = list(late)
    late_changed[1] = backtest.Row(rows[1].game, rows[1].cutoff_at, 150, 90, "2023-03-01T00:00:00Z")
    b = _walk(games, late_changed)
    assert a == b                                                        # its margin never reached the ratings
    on_time = _walk(games, rows)
    assert on_time["espn:105"] != a["espn:105"]                       # 3+ earlier games: non-empty rosters


def test_select_k_uses_the_warm_up_season_only():
    games, rows = _season()
    rows = [backtest.Row(r.game.__class__(**{**r.game.__dict__, "season": 2021}), r.cutoff_at, r.home_score, r.away_score,
                         r.result_available_at) for r in rows]
    box = _box([(g, d, t) for g, d, t in games], season=2021)
    out = pr.select_k(rows, box)
    assert out["k"] in pr.K_GRID and set(out["mse"]) == set(pr.K_GRID) and out["season"] == 2021


def test_delayed_result_box_stays_unseen_until_delivered():
    """Codex review: a delayed game's minutes/lineup must not reach later rosters before its result is delivered."""
    games, rows = _season()
    late = list(rows)
    late[3] = backtest.Row(rows[3].game, rows[3].cutoff_at, rows[3].home_score, rows[3].away_score, "2023-03-01T00:00:00Z")
    base = _walk(games, late)
    games2 = list(games)
    games2[3] = (games[3][0], games[3][1], {H: [("h1", 240)], A: FIVE_A})
    assert _walk(games2, late) == base                                    # game 103's box never delivered in time
    assert _walk(games2, rows)["espn:104"] != _walk(games, rows)["espn:104"]   # on time, it is used


def test_week_bootstrap_matches_backtest_and_adjusted_interval_is_wider():
    import random
    rnd = random.Random(7)
    vals = [rnd.gauss(-0.001, 0.05) for _ in range(600)]
    cut = [f"2023-{1 + i // 120:02d}-{1 + (i % 120) // 5:02d}T23:00:00Z" for i in range(600)]
    ref = backtest.block_bootstrap(vals, cut)
    stats = pr.week_bootstrap(vals, cut)
    assert pr.percentile_interval(stats, 0.05) == ref["ci95"]
    lo, hi = pr.percentile_interval(stats, pr.PRIMARY_ALPHA)
    n = len(stats)
    assert (lo, hi) == (stats[int(0.0125 * n)], stats[int(0.9875 * n) - 1])
    assert lo <= ref["ci95"][0] and hi >= ref["ci95"][1]


def _report(day, team, entries, present=("2", "3")):
    """Facts for one date: `team`'s entries, and every team in `present` positively listed on the report (a team listed
    nowhere has usually not reported yet: that is the report_missing state, not a healthy team)."""
    f = pr.PlayerFacts(True, "ok", f"{day}T21:30:00Z")
    f.teams_on_report.update((team, *present))
    f.absent[team] = {"new": 0.0, "all": 0.0, "unresolved": 0, "no_history": 0}
    f.entries[team] = entries
    f.players[team] = {p: w for p, w in entries}
    return {day: f}


def test_gated_injury_minutes_ignore_a_delayed_box_until_delivered():
    games, rows = _season()
    facts = _report("2023-01-10", "2", [("h1", 1.0), ("h2", 0.5)])               # game 104
    late = list(rows)
    late[3] = backtest.Row(rows[3].game, rows[3].cutoff_at, rows[3].home_score, rows[3].away_score, "2023-03-01T00:00:00Z")
    base = _walk(games, late, facts)["espn:104"]
    assert base["all_absent_diff"] != 0 and base["report_missing"] == 0.0
    games2 = list(games)
    games2[3] = (games[3][0], games[3][1], {H: [("h1", 12), ("h2", 12), ("zz", 216)], A: FIVE_A})
    assert _walk(games2, late, facts)["espn:104"] == base                    # delayed box unseen
    assert _walk(games2, rows, facts)["espn:104"]["all_absent_diff"] != base["all_absent_diff"]


def test_date_gated_recomputation_equals_availability_minutes_history():
    games, _ = _season()
    box = _box(games)
    hist, ros = av.MinutesHistory(box.apps), pr.Rosters.from_apps(box.apps)
    entries = [("h1", 1.0), ("h3", 0.5), ("nobody", 1.0)]
    for day in ("2023-01-03", "2023-01-09", "2023-01-20"):
        ref = {"new": 0.0, "all": 0.0}
        for pid, w in entries:
            m, _ = hist.expected(pid, day, 2023)
            ref["all"] += w * m
            ref["new"] += w * m if hist.played_last_team_game(pid, "2", day) else 0.0
        assert ros.absent(entries, "2", day, 2023) == pytest.approx(ref)


def test_same_date_appearances_keep_minutes_history_order():
    """All-Star weekend: several appearances on one date; the last-10 window must cut where MinutesHistory cuts."""
    rows = []
    apps = [("2023-01-01", "999", 9), ("2023-01-01", "999", 5), ("2023-01-01", "999", 7)]       # window cuts this date
    apps += [(f"2023-01-{d:02d}", "2", 30 + d) for d in range(2, 20, 2)]
    for i, (day, team, m) in enumerate(apps):
        local = datetime.fromisoformat(f"{day}T19:30:00").replace(tzinfo=av.ET).isoformat()
        rows.append({"game_id": str(i), "game_date_time": local, "athlete_id": "p", "athlete_display_name": "P",
                     "team_id": team, "minutes": str(m), "did_not_play": "false"})
    box = pr.load_box((2023,), loader=lambda s: rows)
    hist, ros = av.MinutesHistory(box.apps), pr.Rosters.from_apps(box.apps)
    assert ros.minutes("p", "2023-02-20", 2023) == pytest.approx(hist.expected("p", "2023-02-20", 2023)[0])


# --- Adaptive member (docs/PLAYER_RATINGS_STUDY.md §11): logistic_v2 + injury report + player ratings, row-cutoff gated ---
import csv
import json
import pickle
from email.utils import format_datetime

from app import adaptive
from app.models.elo import EloConfig

TEAM_NAMES = {"Team 2": "2", "Team 3": "3", "Team 4": "4"}


class _Inputs:
    """In-memory stand-in for pr.AdaptiveInputs: a Box and {ET date: PlayerFacts}."""

    def __init__(self, box, facts=None):
        self.box, self.facts = box, facts or {}

    def get(self, day):
        return self.facts.get(day)

    def status(self):
        return {"synthetic": True}


def _member(games, facts=None, season=2023):
    return pr.adaptive_member(EloConfig(), TEAM_NAMES, inputs=_Inputs(_box(games, season), facts))


def _member_walk(games, rows, facts=None):
    """Walk the adaptive member; returns {game id: the exact feature vector the member used for that forecast}."""
    m = _member(games, facts)
    backtest.run(m, rows)
    return m, {gid: feats for _, feats, _, gid in m.examples}


def test_adaptive_member_own_outcome_and_box_never_change_its_own_features():
    games, rows = _season()
    _, base = _member_walk(games, rows)
    assert set(base) == {r.game.game_id for r in rows[:-1]}                # every delivered game (the last result is after every cutoff)
    assert set(base["espn:104"]) == set(pr.PlayersInjuriesV1.feature_names)
    rows2 = list(rows)
    rows2[4] = backtest.Row(rows[4].game, rows[4].cutoff_at, 160, 90, rows[4].result_available_at)
    games2 = list(games)
    games2[4] = (games[4][0], games[4][1], {H: [("h1", 48), ("zz", 192)], A: FIVE_A})
    _, changed = _member_walk(games2, rows2)
    for i in range(5):                                                   # games up to and including the mutated one
        assert changed[f"espn:{100 + i}"] == base[f"espn:{100 + i}"]
    assert changed["espn:105"] != base["espn:105"]                       # later forecasts do learn from it


def test_adaptive_member_future_results_boxes_and_reports_never_change_earlier_features():
    games, rows = _season()
    later_day = games[6][1]                                              # game 106
    _, base = _member_walk(games, rows, _report(later_day, "2", [("h1", 1.0)]))
    rows2 = [backtest.Row(r.game, r.cutoff_at, r.away_score, r.home_score, r.result_available_at) if i >= 6 else r
             for i, r in enumerate(rows)]
    games2 = [(g, d, {H: [("h1", 240)], A: FIVE_A}) if i >= 6 else (g, d, t) for i, (g, d, t) in enumerate(games)]
    _, changed = _member_walk(games2, rows2, _report(later_day, "2", [("h2", 1.0), ("h3", 0.5)]))
    for i in range(6):
        assert changed[f"espn:{100 + i}"] == base[f"espn:{100 + i}"]
    assert changed["espn:106"] != base["espn:106"]


def test_adaptive_member_uses_only_results_delivered_before_the_cutoff():
    games, rows = _season()
    late = list(rows)                                                    # game 101's result arrives after every cutoff
    late[1] = backtest.Row(rows[1].game, rows[1].cutoff_at, rows[1].home_score, rows[1].away_score, "2023-03-01T00:00:00Z")
    m, a = _member_walk(games, late)
    assert "espn:101" not in m.players.delivered and len(m.players.delivered) == len(rows) - 2
    late_changed = list(late)
    late_changed[1] = backtest.Row(rows[1].game, rows[1].cutoff_at, 150, 90, "2023-03-01T00:00:00Z")
    _, b = _member_walk(games, late_changed)
    assert a == b                                                        # its margin and box never reached the member
    _, on_time = _member_walk(games, rows)
    assert on_time["espn:105"] != a["espn:105"]


def test_adaptive_member_abstains_cleanly_without_a_usable_report():
    games, rows = _season(6)
    day = games[4][1]                                                    # game 104
    g = rows[4].game
    used = _member(games, _report(day, "2", [("h1", 1.0)]))
    backtest.run(used, rows)
    d = used.players.diffs(g)
    assert d["report_missing"] == 0.0 and d["all_absent_diff"] != 0.0 and d["player_diff_report"] != d["player_diff_full"]
    unsubmitted = _report(day, "2", [("h1", 1.0)])
    unsubmitted[day].unsubmitted.add("3")
    unreconciled = {day: pr.PlayerFacts(False, "unreconciled", f"{day}T22:30:00Z")}
    unlisted = _report(day, "2", [("h1", 1.0)], present=())              # the away team is not on the report at all
    for facts in ({}, unsubmitted, unreconciled, unlisted):
        m = _member(games, facts)
        backtest.run(m, rows)
        d = m.players.diffs(g)
        assert d["report_missing"] == 1.0 and d["new_absent_diff"] == 0.0 and d["all_absent_diff"] == 0.0
        assert d["player_diff_report"] == d["player_diff_full"] and not d["report_used"]


def test_a_team_not_listed_on_the_report_is_report_missing_not_healthy():
    """Review finding A1: on the prior-evening report a team listed nowhere has usually not reported yet (83% of such
    team-games had absences on the same-day report), so it is the declared report_missing state, never "healthy". A team
    positively present with no absences (e.g. an Available-only listing) counts as submitted with zero absences."""
    games, rows = _season(6)
    day, g = games[4][1], rows[4].game                                   # game 104: team 2 at home, team 3 away
    only_home = _report(day, "2", [("h1", 1.0)], present=())            # team 3 appears nowhere on the report
    m = _member(games, only_home)
    backtest.run(m, rows)
    d = m.players.diffs(g)
    assert d["report_missing"] == 1.0 and not d["report_used"]
    assert d["new_absent_diff"] == 0.0 and d["all_absent_diff"] == 0.0 and d["player_diff_report"] == d["player_diff_full"]
    both = _report(day, "2", [("h1", 1.0)], present=("3",))             # team 3 listed with nobody absent
    m = _member(games, both)
    backtest.run(m, rows)
    d = m.players.diffs(g)
    assert d["report_missing"] == 0.0 and d["report_used"] and d["all_absent_diff"] != 0.0
    assert d["player_diff_report"] != d["player_diff_full"]


def test_adaptive_member_records_a_result_as_delivered_only_when_its_box_was_applied():
    """Review finding B3: a result without box rows updates neither ratings nor rosters, so it must not enter the
    delivered index either; otherwise a later cache extension would rebuild rosters from a game the ratings never saw."""
    games, rows = _season(6)
    without_102 = [g for g in games if g[0] != "102"]                    # game 102's result has no box rows
    m = _member(without_102)
    backtest.run(m, rows)
    assert "espn:102" not in m.players.delivered
    assert m.players.coverage["result_without_box"] == 1
    assert len(m.players.delivered) == m.players.coverage["rating_updates"] == len(rows) - 2
    assert m.players.status()["delivered_results"] == len(rows) - 2
    state = pickle.loads(adaptive._dumps(m.players))
    state._inputs = _Inputs(_box(games))                                 # the cache later gains game 102's box
    assert state._resolve().rosters.expected("2", games[5][1], 2023) == m.players._resolve().rosters.expected("2", games[5][1], 2023)


def test_adaptive_member_undelivered_box_minutes_never_seed_rosters_or_absent_minutes():
    """Review finding C1: an undelivered (late) game's box MINUTES and lineup, not only its score, stay unseen by the
    member's rosters, expected minutes and absent minutes until its result is delivered."""
    games, rows = _season()
    late = list(rows)                                                    # game 103's result arrives after every cutoff
    late[3] = backtest.Row(rows[3].game, rows[3].cutoff_at, rows[3].home_score, rows[3].away_score, "2023-03-01T00:00:00Z")
    facts = _report("2023-01-10", "2", [("h1", 1.0), ("h2", 0.5)])      # game 104's report
    m, base = _member_walk(games, late, facts)
    assert "espn:103" not in m.players.delivered and base["espn:104"]["all_absent_diff"] != 0.0
    games2 = list(games)
    games2[3] = (games[3][0], games[3][1], {H: [("h1", 12), ("h2", 12), ("zz", 216)], A: FIVE_A})
    _, changed = _member_walk(games2, late, facts)
    assert changed == base                                               # 103's minutes and lineup reached no forecast
    _, on_time = _member_walk(games2, rows, facts)
    assert on_time["espn:104"] != base["espn:104"]                       # delivered on time, they do


def test_member_report_cutoff_is_the_walk_rows_cutoff():
    """Review finding C4: the member gates reports by the walk rows' cutoff, backtest.cutoff_for (midnight ET of the
    game's Eastern date): pinned across DST changes, late tip-offs that cross UTC midnight, matinees and the bubble."""
    for tip in ("2023-01-10T00:30:00Z", "2023-01-10T17:00:00Z", "2023-03-12T23:00:00Z", "2023-03-13T02:00:00Z",
                "2023-11-05T17:30:00Z", "2023-11-06T03:30:00Z", "2020-08-01T22:00:00Z"):
        assert pr._midnight_utc(pr.et_day(tip)) == backtest.cutoff_for(tip)
    _, rows = _season()
    assert all(pr._midnight_utc(pr.et_day(r.game.tipoff_at)) == backtest.cutoff_for(r.game.tipoff_at) for r in rows)


def test_adaptive_member_reports_stale_inputs_when_recent_finals_have_no_player_box_rows():
    """Review finding B2: a team's recent finals of the season without player-box rows mean the ratings and rosters are
    last season's; the member must say so (and abstain in live) instead of forecasting on them silently."""
    games, rows = _season(6)
    report = _report(games[5][1], "2", [("h1", 1.0)])                   # a usable report: only the box rule is tested here
    m = _member(games[:4], report)                                       # the box ends with game 103
    backtest.run(m, rows)
    g = rows[5].game
    stale = m.players.stale_inputs(g, {(2023, H): ["espn:104", "espn:103"], (2023, A): ["espn:104", "espn:103"]}, 5)
    assert stale and "player box" in stale and "1 recent final" in stale
    assert m.players.stale_inputs(g, {(2023, H): ["espn:103", "espn:102"], (2023, A): ["espn:103"]}, 5) is None
    assert m.players.stale_inputs(g, {}, 5) is None                      # season start: nothing final yet
    assert m.players.stale_inputs(g, {(2023, H): ["espn:104", "espn:103"]}, 1) is not None


def test_adaptive_member_early_game_uses_a_report_published_before_its_cutoff():
    """Under the row cutoff (midnight ET) a report published the evening before is available to a matinee too; the
    17:45 ET research design keeps excluding games before 18:00 ET."""
    games, rows = _season(6)
    day = games[4][1]
    g = rows[4].game
    early = PregameGame(g.game_id, g.season, g.season_type,
                        datetime.fromisoformat(f"{day}T15:00:00").replace(tzinfo=av.ET).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        g.home_team_id, g.away_team_id, False)
    facts = _report(day, "2", [("h1", 1.0)])
    m = _member(games, facts)
    backtest.run(m, rows[:4])
    assert m.players.diffs(early)["report_missing"] == 0.0
    research = pr.PlayerState(_box(games), 0.02, facts)
    for r in rows[:4]:
        research.update(r.result)
    assert research.diffs(early)["report_missing"] == 1.0


def test_adaptive_member_state_pickles_without_the_box_and_rebuilds_rosters():
    games, rows = _season()
    m = _member(games, _report(games[7][1], "2", [("h1", 1.0)]))
    backtest.run(m, rows[:7])
    nxt = rows[7].game
    blob = adaptive._dumps(m.players)
    assert len(blob) < 20_000
    state = pickle.loads(blob)
    assert not hasattr(state, "_state") or state._state is None
    assert state.delivered == m.players.delivered and state.ratings.r == m.players.ratings.r
    state._inputs = m.players._inputs                                    # a thaw re-resolves the shared inputs
    assert state.diffs(nxt) == m.players.diffs(nxt)
    wrapped = adaptive.Adaptive(m)
    frozen = adaptive.freeze(wrapped)
    assert len(frozen) < 60_000
    fresh = adaptive.thaw(adaptive.Adaptive(_member(games)), frozen)
    fresh.member.players._inputs = m.players._inputs                   # production re-resolves them from the data directory
    assert fresh.member.players.diffs(nxt) == m.players.diffs(nxt) and fresh.member.weights == m.weights


def _fake_reports(monkeypatch, rdir, reports):
    """Writes `reports` ({file name: {header, entries, not_submitted, reconciled}}) as JSON stand-ins for the PDFs, with
    matching Last-Modified metadata, and makes av.parse_report decode them (no pypdf in these tests)."""
    rdir.mkdir(parents=True, exist_ok=True)
    for name, spec in reports.items():
        (rdir / name).write_bytes(json.dumps(spec).encode())
        lm = format_datetime(datetime.fromisoformat(spec.get("last_modified", spec["header"]).replace("Z", "+00:00")), usegmt=True)
        (rdir / (name + ".json")).write_text(json.dumps({"last_modified": lm}))

    def parse(pdf, name, team_names):
        spec = json.loads(pdf)
        r = av.ParsedReport(name, datetime.fromisoformat(spec["header"].replace("Z", "+00:00")),
                            entries=list(spec["entries"]), not_submitted=list(spec.get("not_submitted", [])))
        r.loose_lines = len(r.entries) + (0 if spec.get("reconciled", True) else 1)
        return r
    monkeypatch.setattr(pr.av, "parse_report", parse)


def _write_box(rdir, season, games):
    path = rdir / "player_box" / f"player_box_{season}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["game_id", "game_date_time", "athlete_id", "athlete_display_name", "team_id", "minutes", "did_not_play"])
        w.writeheader()
        for gid, day, teams in games:
            local = datetime.fromisoformat(f"{day}T19:30:00").replace(tzinfo=av.ET).isoformat()
            for team, players in teams.items():
                for pid, m in players:
                    w.writerow({"game_id": gid, "game_date_time": local, "athlete_id": pid, "athlete_display_name": _name(pid),
                                "team_id": team.split(":")[1], "minutes": m, "did_not_play": "false" if m else "true"})


_LETTERS = str.maketrans("0123456789", "abcdefghij")


def _name(pid):
    """Distinct alphabetic display name per athlete id (availability.norm_name drops digits)."""
    return "P " + pid.translate(_LETTERS)


def _entry(day, team, pid, status):
    return {"game_date": day, "team": team, "last": _name(pid).split(" ")[1], "first": "P", "status": status}


def test_adaptive_inputs_use_only_reports_published_before_the_row_cutoff(tmp_path, monkeypatch):
    """The latest report published before midnight ET of the game date that lists the game: in practice the previous
    evening's 5:30 PM report's next-day section. The same-day report is never used; a report whose publication time is
    after midnight, an unreconciled one, or one without metadata is excluded (counted, never guessed)."""
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    games, _ = _season()
    rdir = tmp_path / "research" / "availability"
    _write_box(rdir, 2023, games)
    day, prev = "2023-01-10", "2023-01-09"                                       # game 104 is on 01-10
    _fake_reports(monkeypatch, rdir / "reports", {
        av.report_name(prev): {"header": "2023-01-09T22:30:00Z", "entries": [_entry(prev, "Team 2", "h2", "Out"),
                                                                            _entry(day, "Team 2", "h1", "Out"),
                                                                            _entry(day, "Team 3", "a1", "Questionable")],
                               "not_submitted": [{"game_date": day, "team": "Team 4"}]},
        av.report_name(day): {"header": "2023-01-10T22:30:00Z", "entries": [_entry(day, "Team 2", "h3", "Out")]},
        av.report_name("2023-01-11"): {"header": "2023-01-12T05:30:00Z",                 # published after midnight ET of 01-12
                                       "entries": [_entry("2023-01-12", "Team 2", "h1", "Out")]},
        av.report_name("2023-01-13"): {"header": "2023-01-13T22:30:00Z", "reconciled": False,     # metadata present
                                       "entries": [_entry("2023-01-14", "Team 2", "h1", "Out")]},
        av.report_name("2023-01-15"): {"header": "2023-01-15T22:30:00Z",
                                       "entries": [_entry("2023-01-16", "Team 2", "h1", "Out")]},
    })
    (rdir / "reports" / (av.report_name("2023-01-15") + ".json")).unlink()          # no Last-Modified
    inputs = pr.adaptive_inputs(TEAM_NAMES)
    f = inputs.get(day)
    assert f.usable and f.published_at == "2023-01-09T22:30:00Z" and f.unsubmitted == {"4"}
    assert f.players == {"2": {"h1": 1.0}, "3": {"a1": 0.5}} and f.entries == {"2": [("h1", 1.0)], "3": [("a1", 0.5)]}
    assert set(f.absent) == {"2", "3"} and f.teams_on_report == {"2", "3"}
    assert inputs.get(prev) is None                              # its own 5:30 PM report is after its cutoff; no 01-08 report
    assert inputs.get("2023-01-12") is None and inputs.get("2023-01-14") is None and inputs.get("2023-01-16") is None
    assert inputs.get("2023-01-20") is None
    cov = inputs.status()["facts"]
    assert cov["usable"] == 1 and cov["excluded_published_after_cutoff"] == 2 and cov["missing"] == 1
    assert cov["excluded_unreconciled"] == 1 and cov["excluded_no_last_modified"] == 1 and cov["dates"] == 6
    assert inputs.status()["box_seasons"] == [2023] and inputs.box.games["espn:104"]


def test_adaptive_inputs_take_the_latest_eligible_report_and_exclude_one_rewritten_after_midnight(tmp_path, monkeypatch):
    """Review finding C3. (1) Two reports published before the cutoff list the game (a report names games up to two days
    ahead): the LATEST one is used, not the earliest. (2) A report whose Last-Modified is after midnight ET — rewritten
    after the cutoff — is excluded although its header says 5:30 PM the evening before."""
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    games, _ = _season()
    rdir = tmp_path / "research" / "availability"
    _write_box(rdir, 2023, games)
    day = "2023-01-10"                                                           # game 104
    _fake_reports(monkeypatch, rdir / "reports", {
        av.report_name("2023-01-08"): {"header": "2023-01-08T22:30:00Z",
                                       "entries": [_entry(day, "Team 2", "h2", "Out"), _entry(day, "Team 3", "a1", "Out")]},
        av.report_name("2023-01-09"): {"header": "2023-01-09T22:30:00Z",
                                       "entries": [_entry(day, "Team 2", "h1", "Out"), _entry(day, "Team 3", "a1", "Questionable")]},
        av.report_name("2023-01-11"): {"header": "2023-01-11T22:30:00Z", "last_modified": "2023-01-12T05:30:00Z",
                                       "entries": [_entry("2023-01-12", "Team 2", "h1", "Out"),      # rewritten after midnight ET
                                                   _entry("2023-01-12", "Team 3", "a1", "Out")]},
    })
    inputs = pr.adaptive_inputs(TEAM_NAMES)
    f = inputs.get(day)
    assert f.published_at == "2023-01-09T22:30:00Z"                              # the 01-09 report, not the earlier 01-08 one
    assert f.players == {"2": {"h1": 1.0}, "3": {"a1": 0.5}}
    assert inputs.get("2023-01-12") is None
    cov = inputs.status()["facts"]
    assert cov["dates"] == 2 and cov["usable"] == 1 and cov["excluded_published_after_cutoff"] == 1


def test_adaptive_inputs_missing_cache_is_reported_and_a_box_without_reports_degrades_visibly(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    assert "player_box" in pr.adaptive_inputs_missing()
    games, rows = _season(5)
    _write_box(tmp_path / "research" / "availability", 2023, games)
    assert pr.adaptive_inputs_missing() is None
    m = pr.adaptive_member(EloConfig(), TEAM_NAMES)                                  # inputs resolved lazily from the data dir
    backtest.run(m, rows)
    assert all(f["report_missing"] == 1.0 for _, f, _, _ in m.examples)
    status = m.input_status()
    assert status["reports"] == 0 and status["facts"]["missing"] == len(rows) and status["players"]["rating_updates"] == len(rows) - 1


def test_two_hour_reports_differ_for_matinee_and_evening_on_same_day(tmp_path, monkeypatch):
    from dataclasses import replace
    from app import adaptive
    games, rows = _season(5)
    day = '2023-01-10'
    rdir = tmp_path / 'research' / 'availability'
    _write_box(rdir, 2023, games)
    _fake_reports(monkeypatch, rdir / 'reports', {
        'Injury-Report_2023-01-10_12_00PM.pdf': {'header': '2023-01-10T17:00:00Z',
            'entries': [_entry(day, 'Team 2', 'h1', 'Available'), _entry(day, 'Team 3', 'a1', 'Available')]},
        'Injury-Report_2023-01-10_05_30PM.pdf': {'header': '2023-01-10T22:30:00Z',
            'entries': [_entry(day, 'Team 2', 'h1', 'Out'), _entry(day, 'Team 3', 'a1', 'Available')]},
        'Injury-Report_2023-01-10_06_30PM.pdf': {'header': '2023-01-10T23:30:00Z',
            'entries': [_entry(day, 'Team 2', 'h2', 'Out'), _entry(day, 'Team 3', 'a1', 'Available')]}})
    inp = pr.AdaptiveInputs(rdir, TEAM_NAMES)
    early = replace(rows[-1].game, tipoff_at='2023-01-10T20:00:00Z', cutoff_at='2023-01-10T18:00:00Z')
    late = replace(rows[-1].game, tipoff_at='2023-01-11T00:30:00Z', cutoff_at='2023-01-10T22:30:00Z')
    assert inp.for_game(early).players.get('2', {}) == {}
    assert inp.for_game(late).players['2'] == {'h1': 1.}
    assert inp.for_game(early).players.get('2', {}) == {}  # per-cutoff cache, not date-only
    for tip in ('2023-03-12T19:00:00Z', '2023-11-05T19:00:00Z', '2020-08-01T18:00:00Z'):
        assert (datetime.fromisoformat(tip.replace('Z', '+00:00')) -
                datetime.fromisoformat(adaptive.forecast_cutoff(tip).replace('Z', '+00:00'))).total_seconds() == 7200


def test_score_member_injuries_change_total_and_margin_and_refresh_at_new_cutoff():
    import numpy as np
    from dataclasses import replace
    from app import markets
    from app.models.score_candidates import NBA_FEATURES
    games, rows = _season(5)
    facts = _report('2023-01-10', '2', [('h1', 1.)])
    m = pr.PlayersInjuriesPoints(EloConfig(), TEAM_NAMES, _Inputs(_box(games), facts))
    backtest.run(m, rows[:-1], deliver_until=rows[-1].cutoff_at)
    g = replace(rows[-1].game, cutoff_at=rows[-1].cutoff_at)
    n = len(NBA_FEATURES) + len(pr.SCORE_FEATURES)
    margin = np.zeros(n + 1); total = np.zeros(n + 1)
    margin[0] = 3.; total[0] = 220.
    margin[1 + len(NBA_FEATURES) + pr.SCORE_FEATURES.index('all_absent_diff')] = 2.
    total[1 + len(NBA_FEATURES) + pr.SCORE_FEATURES.index('all_absent_sum')] = -4.
    m.fitted = (np.zeros(n), np.ones(n), (margin, total)); m.sd = (12., 18.)
    injured = m.distribution(g)
    m.players._inputs.facts.clear()
    healthy = m.distribution(replace(g, cutoff_at='2023-01-10T23:00:00Z'))
    assert injured.m < healthy.m == 3.
    assert injured.t < healthy.t == 220.
    for market, line in [('winner', None), ('spread', -1.5), ('total', 220.5),
                         ('team_total_home', 110.5), ('team_total_away', 110.5)]:
        vector = injured.winner() if market == 'winner' else injured.market(market, line)
        assert sum(vector) == pytest.approx(1.)


def test_unresolved_absent_player_does_not_count_as_a_complete_report():
    games, rows = _season(6)
    day = games[4][1]
    facts = _report(day, '2', [])
    facts[day].absent['2']['unresolved'] = 1
    model = _member(games, facts)
    backtest.run(model, rows)
    diff = model.players.diffs(rows[4].game)
    assert not diff['report_used'] and diff['report_missing'] == 1.


def test_live_staleness_requires_rows_for_the_team_not_just_game_id():
    from types import SimpleNamespace
    state = pr.LiveState(.05, {})
    state._state = SimpleNamespace(box=SimpleNamespace(games={'espn:prior': {'3': [('a', 40)]}}),
                                   diffs=lambda g: {'report_used': True})      # a usable report: only the box rule here
    game = _row('next', '2023-01-10', H, A, 100, 90).game
    recent = {(2023, H): ['espn:prior']}
    assert state.stale_inputs(game, recent, 5) is not None
    state._state.box.games['espn:prior']['2'] = [('h', 40)]
    assert state.stale_inputs(game, recent, 5) is None


@pytest.mark.parametrize('minutes', [float('nan'), float('inf'), -1.])
def test_player_box_rejects_nonfinite_or_negative_minutes(minutes):
    with pytest.raises(ValueError, match='invalid player minutes'):
        _box([('bad', '2023-01-02', {H: [('h1', minutes)], A: FIVE_A})])


def test_duplicate_player_game_rows_are_rejected_before_rating_updates():
    with pytest.raises(ValueError, match='duplicate player game'):
        _box([('bad', '2023-01-02', {H: [('h1', 40), ('h1', 40)], A: FIVE_A})])


def test_an_unparseable_live_report_is_excluded_and_counted_not_raised_on_every_cycle(tmp_path, monkeypatch):
    """A stored live PDF that cannot be parsed (the collector only checks the magic bytes and length) must be excluded
    like a cached research report, not raise out of every forecast cycle while it stays in the two-day window."""
    import sqlite3
    from pathlib import Path
    from app import raw_cache
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE injury_report_attempts(file_name, completed_at, last_modified, fetch_id, outcome)")
    conn.execute("CREATE TABLE raw_fetches(fetch_id, raw_id)")
    conn.execute("INSERT INTO injury_report_attempts VALUES('Injury-Report_2026-01-05_05_30PM.pdf', "
                 "'2026-01-05T22:31:00Z', 'Mon, 05 Jan 2026 22:30:00 GMT', 1, 'stored')")
    conn.execute("INSERT INTO raw_fetches VALUES(1, 7)")
    loads = []
    monkeypatch.setattr(raw_cache, "load", lambda c, rid: loads.append(rid) or b"%PDF-1.4 truncated")
    inputs = pr.AdaptiveInputs(tmp_path, {"Atlanta Hawks": "1"})
    for _ in range(2):
        inputs.add_live_reports(conn, "2026-01-05T23:00:00Z")
    key = Path("live-7-Injury-Report_2026-01-05_05_30PM.pdf")
    assert inputs._parsed[key]["error"].startswith("parse_error:")
    assert loads == [7]                                   # parsed once, then the recorded exclusion is reused
    assert key not in inputs.reports.get("2026-01-05", [])


def test_adaptive_report_names_resolve_only_from_box_rows_before_the_report(tmp_path, monkeypatch):
    """Identity must not use the future: a name whose only box row is dated after the report (a debut or a call-up two
    months later) is unresolved for this report, so whether the report counts as usable never depends on later data.
    A player already seen in an earlier box still resolves."""
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    games, _ = _season()
    later = ("900", "2023-03-01", {H: [*FIVE_H[:4], ("n1", 48)], A: FIVE_A})
    rdir = tmp_path / "research" / "availability"
    _write_box(rdir, 2023, [*games, later])
    day, prev = "2023-01-10", "2023-01-09"
    _fake_reports(monkeypatch, rdir / "reports", {
        av.report_name(prev): {"header": "2023-01-09T22:30:00Z", "entries": [_entry(day, "Team 2", "h1", "Out"),
                                                                            _entry(day, "Team 2", "n1", "Out"),
                                                                            _entry(day, "Team 3", "a1", "Out")]}})
    f = pr.adaptive_inputs(TEAM_NAMES).get(day)
    assert f.players["2"] == {"h1": 1.0} and f.absent["2"]["unresolved"] == 1


def test_a_player_listed_on_another_teams_report_leaves_his_former_expected_roster():
    """Traded while injured: his delivered appearances are all for the home team, but the pre-cutoff report lists him on
    the opponent's report. He no longer plays for the home team, so its report-adjusted strength drops exactly as if
    the home team had listed him out (same rule as the NHL member)."""
    games, rows = _season()
    moved = _walk(games, rows, _report("2023-01-10", "3", [("h1", 1.0)]))["espn:104"]
    out = _walk(games, rows, _report("2023-01-10", "2", [("h1", 1.0)]))["espn:104"]
    assert moved["report_used"] and out["report_used"]
    assert moved["player_home_report"] == pytest.approx(out["player_home_report"])


def test_a_late_result_of_an_earlier_season_does_not_move_a_players_season_back_and_carry_twice():
    """Results are delivered by availability: a season-S score corrected after season S+1 started reaches the walk after
    S+1 results. `update` set the player's season back to S, so the next S+1 read applied the season carry again."""
    r = pr.PlayerRatings(0.1)
    r.update(2025, [("p", 48)], [("q", 48)], 10, False)
    current = r.r["p"]
    r.update(2024, [("p", 48)], [("q", 48)], 5, False)
    assert r.season["p"] == 2025
    assert r.get("p", 2025) == r.r["p"] and r.r["p"] != current          # the late result is applied, without a carry


def test_research_report_names_resolve_only_from_box_rows_before_the_report(tmp_path, monkeypatch):
    """The research builders (players-v1 `build_facts`, avail_v1 `build_report_facts`) matched report names against the
    whole box, so a name whose only box row is dated after the report (a debut two months later) resolved. Measured on a
    copy of the players-v1 study cache this changed no feature of any scored game (zero-minute names); it decides only
    whether the name counts as known, i.e. report usability under the unresolved-name rule. Same gate as the adaptive path."""
    import sqlite3
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    games, _ = _season()
    later = ("900", "2023-03-01", {H: [*FIVE_H[:4], ("n1", 48)], A: FIVE_A})
    rdir = tmp_path / "research" / "availability"
    _write_box(rdir, 2023, [*games, later])
    day = "2023-01-10"                                                           # game 104, 19:30 ET
    _fake_reports(monkeypatch, rdir / "reports", {
        av.report_name(day): {"header": "2023-01-10T18:00:00Z", "entries": [_entry(day, "Team 2", "h1", "Out"),
                                                                           _entry(day, "Team 2", "n1", "Out")]}})
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE teams (team_id TEXT, name TEXT)")
    conn.executemany("INSERT INTO teams VALUES (?, ?)", [("espn:2", "Team 2"), ("espn:3", "Team 3")])
    conn.execute("CREATE TABLE games (tipoff_at TEXT, season INTEGER, status TEXT, season_type TEXT)")
    conn.execute("INSERT INTO games VALUES ('2023-01-11T00:30:00Z', 2023, 'final', 'regular')")
    box = pr.load_box((2023,))
    facts, _ = pr.build_facts(conn, box, (2023,))
    f = facts[day]
    assert f.usable and f.players["2"] == {"h1": 1.0} and f.absent["2"]["unresolved"] == 1
    ref, _ = av.build_report_facts(conn, box.apps)
    assert ref[day].absent["2"]["unresolved"] == 1


def test_a_republished_season_box_with_only_new_games_keeps_the_fit_usable_but_a_changed_delivered_game_does_not(tmp_path, monkeypatch):
    """Reviewer investigation (2026-10-13), reproduced: the member's train/serve guard hashed the WHOLE player-box files, so
    the routine republication of the current season (rows for games played since the fit appended) made the member
    'inputs changed … retrain' until the next fit — it could never use a refreshed box between retrains. Now the guard
    covers exactly the box rows of the games the frozen state consumed; a change to one of those still blocks it."""
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    games, rows = _season()
    rdir = tmp_path / "research" / "availability"
    _write_box(rdir, 2023, games[:6])                                   # the box as published at training time
    pr._INPUTS.clear()
    m = pr.adaptive_member(EloConfig(), TEAM_NAMES, inputs=pr.adaptive_inputs(TEAM_NAMES))
    backtest.run(m, rows[:6])                                            # the fit consumed games 100-105
    recorded = m.input_status()
    _write_box(rdir, 2023, games)                                        # republished: games 106-107 appended
    pr._INPUTS.clear()
    served = pickle.loads(pickle.dumps(m.players))
    served._inputs = pr.adaptive_inputs(TEAM_NAMES)
    assert served.input_mismatch(recorded) is None
    changed = [(gid, day, {H: [*FIVE_H[:5], ("h9", 38.4)], A: FIVE_A}) if gid == "103" else (gid, day, t) for gid, day, t in games]
    _write_box(rdir, 2023, changed)                                      # a consumed game's box rows changed
    pr._INPUTS.clear()
    fresh = pickle.loads(pickle.dumps(m.players))                       # each serving cycle thaws a fresh state
    fresh._inputs = pr.adaptive_inputs(TEAM_NAMES)
    assert "inputs changed since the fit" in fresh.input_mismatch(recorded)


def test_the_current_season_player_box_is_refreshed_daily_and_verified(tmp_path, monkeypatch):
    """Reviewer investigation (2026-10-13), reproduced: the only download path skipped existing files and refused any season
    outside the study window, so the current season's box was never fetched and the members went stale after five games."""
    import hashlib
    from app import db, jobs
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    conn = db.connect(tmp_path / "nba.sqlite")
    db.migrate(conn)
    from test_live import event, ingest_events
    ingest_events(conn, [event("g1", "2026-10-21T23:30:00Z", "1", "2", "STATUS_FINAL", 104, 99, 2027)], "2026-10-22T03:00:00Z")
    with pytest.raises(av.AvailabilityError):
        av.download_player_box(2027, get=lambda url: None)                 # the study never reads a season outside its window
    assert jobs.player_box_refresh(conn, now="2026-10-22T10:00:00Z")["status"] == "disabled"    # opt-in: no request
    assert jobs.player_box_refresh(conn, now="2026-10-22T10:00:00Z", enabled=True)["status"] == "skipped"   # no cache here
    monkeypatch.setenv("NBA_PLAYER_BOX_REFRESH", "1")
    # The cache holds the season before the current one (no gap: a gap is fetched again every run, tested separately).
    _write_box(tmp_path / "research" / "availability", 2026, _season()[0])
    body = b"game_id,athlete_id\n1,2\n"

    class Resp:
        def __init__(self, content=b"", js=None):
            self.content, self.status_code, self._js = content, 200, js

        def json(self):
            return self._js
    calls = []

    def get(url):
        calls.append(url)
        if url == av.PLAYER_BOX_RELEASE:
            return Resp(js={"assets": [{"name": "player_box_2027.csv", "digest": "sha256:" + hashlib.sha256(body).hexdigest()}]})
        return Resp(content=body)
    out = jobs.player_box_refresh(conn, now="2026-10-22T10:00:00Z", get=get)
    assert out["status"] == "success" and out["season"] == 2027 and av.player_box_path(2027).read_bytes() == body
    assert not av.player_box_path(2027).with_name("player_box_2027.csv.part").exists()
    again = jobs.player_box_refresh(conn, now="2026-10-22T10:00:00Z", get=get)
    assert again["status"] == "skipped" and "h old" in again["reason"] and len(calls) == 2     # fresh: no second request


def test_the_refresh_fetches_every_published_season_up_to_the_current_one_and_names_the_unpublished_one(tmp_path, monkeypatch):
    """Reproduced 2026-10-14 on an isolated copy of the live store with the real source: during the 2026-27 preseason the
    job asked only for player_box_2027.csv (not published yet: 404, status failed) and never fetched 2025 or 2026, which
    the source publishes, so the cache stayed at 2023-24. Now every published season after the newest cached one is
    fetched (each verified), and a current season the source has not published is reported, not failed."""
    import hashlib
    from app import db, jobs
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    conn = db.connect(tmp_path / "nba.sqlite")
    db.migrate(conn)
    from test_live import event, ingest_events
    ingest_events(conn, [event("p1", "2026-10-05T23:30:00Z", "1", "2", "STATUS_FINAL", 104, 99, 2027, stype=1)],
                  "2026-10-06T03:00:00Z")                                              # 2026-27 preseason has started
    _write_box(tmp_path / "research" / "availability", 2024, _season()[0])
    bodies = {2025: b"game_id,athlete_id\n1,25\n", 2026: b"game_id,athlete_id\n1,26\n"}

    class Resp:
        def __init__(self, content=b"", js=None, status=200):
            self.content, self.status_code, self._js = content, status, js

        def json(self):
            return self._js
    calls = []

    def get(url):
        calls.append(url)
        if url == av.PLAYER_BOX_RELEASE:
            return Resp(js={"assets": [{"name": f"player_box_{y}.csv", "digest": "sha256:" + hashlib.sha256(b).hexdigest()}
                                       for y, b in bodies.items()]})
        y = int(url.rsplit("_", 1)[1].split(".")[0])
        return Resp(content=bodies[y]) if y in bodies else Resp(content=b"Not Found", status=404)
    out = jobs.player_box_refresh(conn, now="2026-10-14T04:00:00Z", get=get, enabled=True)
    assert out["status"] == "success", out
    assert [d["season"] for d in out["downloaded"]] == [2025, 2026] and out["not_published"] == [2027]
    assert out["newest_season"] == 2026
    assert av.player_box_path(2025).read_bytes() == bodies[2025] and av.player_box_path(2026).read_bytes() == bodies[2026]
    assert not av.player_box_path(2027).exists() and not any(u.endswith("player_box_2027.csv") for u in calls)
    n = len(calls)
    again = jobs.player_box_refresh(conn, now="2026-10-14T05:00:00Z", get=get, enabled=True)
    # Nothing new is due (2026 is fresh); the current season is still unpublished and is named, never a failure.
    assert again["status"] == "skipped" and "2027" in again["reason"] and again["newest_season"] == 2026
    assert len(calls) == n + 1                               # one release lookup to see whether 2027 appeared; no download


def _refresh_store(tmp_path, monkeypatch):
    from app import db
    from test_live import event, ingest_events
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    conn = db.connect(tmp_path / "nba.sqlite")
    db.migrate(conn)
    ingest_events(conn, [event("p1", "2026-10-05T23:30:00Z", "1", "2", "STATUS_FINAL", 104, 99, 2027, stype=1)],
                  "2026-10-06T03:00:00Z")
    _write_box(tmp_path / "research" / "availability", 2024, _season()[0])
    return conn


class _Resp:
    def __init__(self, content=b"", js=None, status=200):
        self.content, self.status_code, self._js = content, status, js

    def json(self):
        return self._js


def test_a_season_that_failed_is_fetched_again_once_a_later_one_is_cached(tmp_path, monkeypatch):
    """Review 2 finding 4: a gap season whose download failed was never retried once a later season was cached."""
    import hashlib
    from app import jobs
    conn = _refresh_store(tmp_path, monkeypatch)
    bodies = {2025: b"a\n", 2026: b"b\n", 2027: b"c\n"}
    corrupt = {2025}

    def get(url):
        if url == av.PLAYER_BOX_RELEASE:
            return _Resp(js={"assets": [{"name": f"player_box_{y}.csv", "digest": "sha256:" + hashlib.sha256(b).hexdigest()}
                                        for y, b in bodies.items()]})
        y = int(url.rsplit("_", 1)[1].split(".")[0])
        return _Resp(content=b"garbage" if y in corrupt else bodies[y])
    first = jobs.player_box_refresh(conn, now="2026-10-14T04:00:00Z", get=get, enabled=True)
    assert first["status"] == "partial" and 2025 in first["errors"]
    corrupt.clear()
    again = jobs.player_box_refresh(conn, now="2026-10-14T05:00:00Z", get=get, enabled=True)
    assert again["status"] == "success" and [d["season"] for d in again["downloaded"]] == [2025]


def test_an_unrelated_file_in_the_cache_never_breaks_the_job(tmp_path, monkeypatch):
    """Review 2 finding 5: a stray file name raised ValueError out of a job documented as never fatal."""
    from app import jobs
    conn = _refresh_store(tmp_path, monkeypatch)
    (tmp_path / "research" / "availability" / "player_box" / "player_box_2024_backup.csv").write_text("x")
    out = jobs.player_box_refresh(conn, now="2026-10-14T04:00:00Z", get=lambda u: _Resp(js={"assets": []}), enabled=True)
    assert out["status"] == "skipped" and "player_box_2025.csv" in out["reason"]


def test_a_cache_newer_than_the_store_season_names_what_is_missing(tmp_path, monkeypatch):
    """Review 2 finding 6: the reason read 'player_box_ not published …' with an empty season."""
    from app import jobs
    conn = _refresh_store(tmp_path, monkeypatch)
    _write_box(tmp_path / "research" / "availability", 2028, _season()[0])
    out = jobs.player_box_refresh(conn, now="2026-10-14T04:00:00Z", get=lambda u: _Resp(js={"assets": []}), enabled=True)
    assert "player_box_ " not in out.get("reason", "") and "player_box_2027.csv" in out["reason"]


def test_rows_appearing_for_a_result_the_fit_skipped_without_box_rows_block_the_member(tmp_path, monkeypatch):
    """Reviewer recheck C2 (2026-10-13): a result delivered before the fit while its game had no player-box rows yet is
    skipped by the frozen ratings. When the nightly refresh then adds those rows, the consumed-games digest (delivered games
    only) did not change, so the member served a state a fresh walk on the same inputs would not produce."""
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    games, rows = _season()
    rdir = tmp_path / "research" / "availability"
    _write_box(rdir, 2023, games[:6])                          # games 100-105 have rows at training time
    pr._INPUTS.clear()
    m = pr.adaptive_member(EloConfig(), TEAM_NAMES, inputs=pr.adaptive_inputs(TEAM_NAMES))
    backtest.run(m, rows[:8])                                   # results through 106 delivered; 106 had no rows
    assert m.players.skipped_no_box == {"espn:106"}
    recorded = m.input_status()
    _write_box(rdir, 2023, games)                               # the refresh adds rows for 106-107
    pr._INPUTS.clear()
    served = pickle.loads(pickle.dumps(m.players))
    served._inputs = pr.adaptive_inputs(TEAM_NAMES)
    assert "inputs changed since the fit" in served.input_mismatch(recorded)


def test_the_adaptive_member_abstains_at_serving_without_a_usable_injury_report():
    """Player/injury fix (approved direction 2026-10-14: missing information never means healthy; the NHL member already
    abstains): with no usable report for the game (none, unsubmitted, unreconciled, a team not listed) the NBA member used
    to forecast on full rosters with report_missing=1. It now abstains, with the reason, like a stale player box."""
    games, rows = _season(6)
    day = games[4][1]
    g = rows[4].game
    used = _member(games, _report(day, "2", [("h1", 1.0)]))
    backtest.run(used, rows)
    assert used.stale_inputs(g, {}, 5) is None                          # a usable report: the member forecasts
    unsubmitted = _report(day, "2", [("h1", 1.0)])
    unsubmitted[day].unsubmitted.add("3")
    unreconciled = {day: pr.PlayerFacts(False, "unreconciled", f"{day}T22:30:00Z")}
    unlisted = _report(day, "2", [("h1", 1.0)], present=())
    for facts in ({}, unsubmitted, unreconciled, unlisted):
        m = _member(games, facts)
        backtest.run(m, rows)
        reason = m.stale_inputs(g, {}, 5)
        assert reason and "injury report" in reason
    points = pr.PlayersInjuriesPoints(EloConfig(), TEAM_NAMES, inputs=_Inputs(_box(games, 2023), {}))
    assert "injury report" in (points.stale_inputs(g, {}, 5) or "")     # the score member shares the rule


def test_training_reads_every_stored_live_report_while_serving_reads_the_last_two_days(tmp_path, monkeypatch):
    """Train/serve skew (training audit): serving added the official reports the live collector stored (last two days),
    training never did, so rows of seasons covered only by the live collector trained with report_missing=1 while serving
    used reports. `full=True` (training) loads every stored report retrieved by `now`; each is still used only by rows
    whose cutoff is after its publication and retrieval (AdaptiveInputs.get)."""
    import sqlite3
    from pathlib import Path
    from app import raw_cache
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE injury_report_attempts(file_name, completed_at, last_modified, fetch_id, outcome)")
    conn.execute("CREATE TABLE raw_fetches(fetch_id, raw_id)")
    for i, (name, done) in enumerate((("Injury-Report_2026-01-05_05_30PM.pdf", "2026-01-05T22:31:00Z"),     # 10 days old
                                      ("Injury-Report_2026-01-14_05_30PM.pdf", "2026-01-14T22:31:00Z"),     # recent
                                      ("Injury-Report_2026-01-16_05_30PM.pdf", "2026-01-16T22:31:00Z")), 1):  # after now
        conn.execute("INSERT INTO injury_report_attempts VALUES(?, ?, 'Mon, 05 Jan 2026 22:30:00 GMT', ?, 'stored')", (name, done, i))
        conn.execute("INSERT INTO raw_fetches VALUES(?, ?)", (i, 10 + i))
    monkeypatch.setattr(raw_cache, "load", lambda c, rid: b"%PDF")
    monkeypatch.setattr(av, "parse_report", lambda pdf, name, teams: av.ParsedReport(name, None, entries=[]))
    now = "2026-01-15T23:00:00Z"
    serving = pr.AdaptiveInputs(tmp_path, {"Atlanta Hawks": "1"})
    serving.add_live_reports(conn, now)
    assert sorted(serving.reports) == ["2026-01-14"]
    training = pr.AdaptiveInputs(tmp_path, {"Atlanta Hawks": "1"})
    training.add_live_reports(conn, now, full=True)
    assert sorted(training.reports) == ["2026-01-05", "2026-01-14"]                 # never one retrieved after `now`
    assert Path("live-11-Injury-Report_2026-01-05_05_30PM.pdf") in training._parsed


def test_adaptive_training_loads_the_live_reports_before_walking_the_member(monkeypatch):
    calls = []
    member = pr.adaptive_member(EloConfig(), TEAM_NAMES, inputs=_Inputs(_box(_season(2)[0], 2023), {}))
    monkeypatch.setattr(pr.AdaptiveInputs, "add_live_reports", lambda self, conn, now, full=False: calls.append((now, full)))
    member.players._inputs = pr.AdaptiveInputs.__new__(pr.AdaptiveInputs)
    member.prepare_training_inputs("conn", "2026-10-21T20:00:00Z")
    assert calls == [("2026-10-21T20:00:00Z", True)]
