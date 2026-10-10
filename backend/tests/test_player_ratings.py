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
    ok = pr.PlayerFacts(True, "ok", "2023-01-08T22:30:00Z", players={"2": {"h1": 1.0}})
    seen = _walk(games, rows, {day: ok})
    d = seen["espn:103"]
    assert d["report_used"] and d["player_diff_report"] != d["player_diff_full"]
    ok.unsubmitted.add("3")
    d = _walk(games, rows, {day: ok})["espn:103"]
    assert not d["report_used"] and d["player_diff_report"] == d["player_diff_full"]
    d = _walk(games, rows, {})["espn:103"]
    assert not d["report_used"] and d["player_diff_report"] == d["player_diff_full"]


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


def _report(day, team, entries):
    f = pr.PlayerFacts(True, "ok", f"{day}T21:30:00Z")
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
