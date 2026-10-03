"""Regressions for the 2026-10-03 correctness audit: candidate pool, rule tracking, selection status and tracking-v2.
Each test fails on ab0f190 (HANDOFF.md)."""
import json
import math
from datetime import timedelta

from app import agreement_tracking as at, research_live, rule_tracking as rt, selection_status, selector
from app.candidates import engine, selection
from test_agreement_tracking import candidate, core_winner, g, obs, small_registry  # noqa: F401
from test_candidate_pool import GID as POOL_GID, SPECS, _record, upcoming  # noqa: F401
from test_live_markets import sealed  # noqa: F401
from test_nhl_eval import nhl_store  # noqa: F401
from test_pool_selection import (CAND, DEFN, START, add_candidate, add_cohort, add_game, history, monday_after,  # noqa: F401
                                 pool_registry, settle, store, ts)
from test_research_live import GID, game, run  # noqa: F401

NOW = "2026-10-12T21:30:00Z"


# --- Candidate pool / selection -------------------------------------------------------------------------------------
def test_pool_report_counts_settled_games_before_the_first_pool_decision(store):
    end = history(store, 20)
    rep = selection.report(store, "nhl", as_of=ts(end + timedelta(days=1)), reg=pool_registry())
    assert rep["markets"]["spread"]["settled_cohorts"] == 20


def test_status_shows_the_record_of_an_incumbent_rule_kept_by_hysteresis(store):
    for i in range(150):                                   # every core member shares one informative vector (right 6 of 7)
        tip = START + timedelta(hours=5 * i)
        gid, tipoff = f"nhl:s{i}", ts(tip)
        add_game(store, gid, tipoff)
        home = i % 2 == 0
        p = (0.8, 0.0, 0.2) if home == (i % 7 != 0) else (0.2, 0.0, 0.8)
        cid = add_cohort(store, gid, tipoff, ts(tip - timedelta(hours=1)), members_p=p)
        add_candidate(store, CAND, gid, tipoff, ts(tip - timedelta(minutes=59)), p, ref=f"research_cohorts:{cid}")
        settle(store, gid, tipoff, 4 if home else 2, 1 if home else 3)
    store.commit()
    now = monday_after(START + timedelta(hours=5 * 150))
    reg = pool_registry()
    rid = selection.registry_id(reg)
    sched = research_live.scheduled_time(now)
    prev = ts(research_live._parse(sched) - timedelta(days=7))
    inc = {"source": "goals_glm", "filter": "none", "threshold": 0.75}
    store.execute("INSERT INTO pool_decisions (idempotency_key, sport, market, selector_version, definition, registry, "
                  "scheduled_for, decided_at, effective_until, status, rule, lower_bound, reason, evidence) VALUES "
                  "('prev','nhl','spread',?,?,?,?,?,?,'active',?,0.2,'prev','{}')",
                  (selector.version(), DEFN, rid, prev, prev, sched, json.dumps(inc)))
    selection.decide_due(store, now=now, defn=DEFN, reg=reg)
    row = selection.decision_in_force(store, "nhl", "spread", DEFN, rid, now)
    assert json.loads(row["rule"]) == inc                  # kept by hysteresis, not among the ten highest lower bounds
    assert not any((s["source"], s["threshold"]) == ("goals_glm", 0.75) and s["filter"] == "none"
                   for s in json.loads(row["evidence"])["top_rules"])
    record = selection_status._rule_view(row, "pool", selection_status._pool_stats(store, reg, "nhl"))["evidence"]["record"]
    assert record is not None and record["picks"] == 150 and record["wins"] + record["losses"] == 150


def test_rule_tracking_recent_decisions_are_the_latest_games(store):
    for i in range(14):
        tip = START + timedelta(hours=5 * i)
        add_game(store, f"nhl:s{i}", ts(tip))
        add_cohort(store, f"nhl:s{i}", ts(tip), ts(tip - timedelta(hours=1)), members_p=(0.8, 0.0, 0.2))
    store.commit()
    members = list(research_live.LINE_MEMBERS["nhl"])
    reg = rt.seal({"study": "selective-v1", "version": 1, "effective_from": "2026-10-01T00:00:00Z", "markets": {"nhl:spread": {
        "sport": "nhl", "market": "spread", "cohort": "core", "members": members, "comparator": members[0], "frontier": [],
        "rules": [{"id": "single|goals_glm|0.6|all", "kind": "single", "members": ["goals_glm"], "t": 0.6, "level": "all",
                   "selected": "qualified", "why": "t", "prospective_only": False, "historical": []}]}}})
    p = rt.report(store, "nhl", "spread", "all", reg)["rules"][0]["prospective"]
    assert [r["game_id"] for r in p["recent"]] == [f"nhl:s{i}" for i in range(4, 14)] and p["pending"] == 14


def test_a_refused_candidate_insert_is_an_error_not_an_existing_forecast(upcoming, monkeypatch):
    real = engine.vector
    monkeypatch.setattr(engine, "vector", lambda o, m, l: (math.nan, 0.0, math.nan) if o["head"] == "reg3" else real(o, m, l))
    out = _record(upcoming, lines={(POOL_GID, "spread"): {"line": -1.5, "source": "core_cohort", "ref": "cohort:1"}})
    assert upcoming.execute("SELECT count(*) FROM candidate_forecasts WHERE candidate='multinomial_l2:nhl_base'").fetchone()[0] == 0
    assert out["already_recorded"] == 0 and out["errors"] and out["created"] > 0      # the other markets are still recorded


# --- tracking-v2 ----------------------------------------------------------------------------------------------------
def test_one_failing_group_does_not_drop_the_other_groups_of_its_cohort(game, monkeypatch):
    run(game, NOW)
    candidate(game, core_winner(game)["cohort_id"])
    real, bad = at.classify, "unanimous|hockey_elo+hockey_logistic_xg"
    monkeypatch.setattr(at, "classify", lambda grp, *a, **k: 1 / 0 if grp["id"] == bad else real(grp, *a, **k))
    out = at.record(game, game_ids=[GID], now="2026-10-12T21:40:00Z", reg=small_registry())
    ids = [r["group_id"] for r in obs(game)]
    assert len(ids) == 4 and bad not in ids and out["status"] == "partial"
    assert any(bad in k for k in out["errors"])


def test_an_observation_rejected_by_the_database_is_an_error_not_already_recorded(game, monkeypatch):
    run(game, NOW)
    real = at.classify

    def fake(grp, *a, **k):
        r = real(grp, *a, **k)
        if grp["id"] == "single|hockey_elo":
            r["statistic"] = float("nan")              # stored as NULL: violates the pick/statistic CHECK
        return r
    monkeypatch.setattr(at, "classify", fake)
    out = at.record(game, game_ids=[GID], now="2026-10-12T21:40:00Z", reg=small_registry())
    assert len(obs(game, "single|hockey_elo")) == 0 and out["already_recorded"] == 0
    assert out["status"] == "partial" and any("single|hockey_elo" in k for k in out["errors"]) and out["created"] == 4


def test_the_report_never_scores_a_group_from_an_older_cohort_than_the_games_latest(game):
    run(game, NOW)
    reg = small_registry()
    at.record(game, game_ids=[GID], now="2026-10-12T21:35:00Z", reg=reg)
    first = core_winner(game)["cohort_id"]
    # A newer winner cohort (a member changed) for which one group has no observation.
    from test_live_markets import add_upcoming
    add_upcoming(game, gid=2026020099, start="2026-10-11T19:00:00", state=7, hs=3, vs=1, period=3, at="2026-10-12T21:40:00Z",
                 home=1, away=2)                         # a new result elsewhere: new dataset, new member forecasts
    run(game, "2026-10-12T21:45:00Z")
    second = core_winner(game)["cohort_id"]
    assert second != first
    real, skip = at.classify, "single|hockey_elo"
    import pytest
    mp = pytest.MonkeyPatch()
    mp.setattr(at, "classify", lambda grp, *a, **k: 1 / 0 if grp["id"] == skip else real(grp, *a, **k))
    try:
        at.record(game, game_ids=[GID], now="2026-10-12T21:50:00Z", reg=reg)
    finally:
        mp.undo()
    assert {r["cohort_id"] for r in obs(game, skip)} == {first}
    items = at.prospective_items(game, reg, "nhl", "winner")
    assert GID not in items.get(skip, {})                                  # no observation at the canonical cohort
    assert all(GID in items[x["id"]] for x in reg["markets"]["nhl:winner"]["groups"] if x["id"] != skip)


def test_historical_statistics_never_round_up_across_a_threshold():
    assert at._stored(0.599996) < 0.6 and at._stored(0.6) == 0.6 and at._stored(0.35) == 0.35
    assert at._stored(0.5999999995) == 0.6                # inside the documented 1e-9 boundary tolerance
    assert at._stored(None) is None
    assert at._stored(1 / 3) >= 1 / 3 and at._stored(0.333335) >= 1 / 3 and at._stored(0.33332) < 1 / 3   # three-way floor


# --- Goalie sources: historical evidence used the actual starter (a postgame fact) --------------------------------------
def test_goalie_pool_sources_are_suspended_without_editing_the_sealed_registry():
    reg = selection.load_registry()
    sealed_reg = json.loads(selection.REGISTRY_PATH.read_text())
    assert reg["sports"] == sealed_reg["sports"] and reg["sha256"] == sealed_reg["sha256"]      # the seal is untouched
    live = [(sp, m, e["source"]) for sp, ms in reg["sports"].items() for m in ms for e in selection.entries(reg, sp, m)]
    assert live and not any("_goalie" in s for _, _, s in live)
    assert ("nhl", "spread", "cand:poisson_draw:nhl_base") in live                               # base twins stay eligible
    assert ("nba", "team_total_home", "avg:points_normal+cand:hetero_linear:nba_base") in live
    suspended = selection.suspended(reg)
    assert len(suspended) == 9 and all("_goalie" in s["source"] and s["reason"] for s in suspended)
    assert not any("_goalie" in s for s in selection.all_sources(reg, "nhl", "reg3"))
    assert selection.registry_id(reg) != sealed_reg["sha256"][:16]     # decisions after the suspension have their own identity
    assert reg["suspensions"]["suspended_at"] <= "2026-10-03T04:30:00Z"


def test_a_suspension_list_for_another_registry_is_refused(tmp_path):
    import pytest
    other = selection.seal({"registry": "0" * 64, "version": 1, "suspended_at": "2026-10-03T05:00:00Z", "sources": []})
    path = tmp_path / "s.json"
    path.write_text(json.dumps(other))
    with pytest.raises(selection.RegistryError):
        selection.apply_suspensions(json.loads(selection.REGISTRY_PATH.read_text()), path)


def test_a_game_whose_latest_cohort_has_no_observation_is_not_scored_from_an_older_one(game, monkeypatch):
    run(game, NOW)
    reg = small_registry()
    at.record(game, game_ids=[GID], now="2026-10-12T21:35:00Z", reg=reg)
    from test_live_markets import add_upcoming
    add_upcoming(game, gid=2026020099, start="2026-10-11T19:00:00", state=7, hs=3, vs=1, period=3, at="2026-10-12T21:40:00Z",
                 home=1, away=2)
    run(game, "2026-10-12T21:45:00Z")                     # a newer core cohort, never observed (recording failed)
    items = at.prospective_items(game, reg, "nhl", "winner")
    assert not any(GID in v for v in items.values())


def test_goalie_groups_are_not_ranked_in_the_historical_leaderboard():
    m = {"candidates": ["cand:poisson_draw:nhl_goalie", "cand:poisson_draw:nhl_base"],
         "groups": [g("single", "cand:poisson_draw:nhl_goalie"), g("single", "cand:poisson_draw:nhl_base"),
                    g("avg", "goals_draw", "cand:poisson_draw:nhl_goalie"), {**g("single", "x"), "kind": "stack_pool", "id": "stack_pool|x"}]}
    assert at._optimistic(m) == {"single|cand:poisson_draw:nhl_goalie", "avg|goals_draw+cand:poisson_draw:nhl_goalie", "stack_pool|x"}
    cell = {"threshold": 0.6, "sealed": True, "decided": 500, "coverage": 0.5, "win_rate": 0.7, "ci95": [0.6, 0.8],
            "matched": [{"model": "a", "win_rate": 0.5, "games": 500}]}
    groups = [{"id": i, "kind": "single", "members": [i], "thresholds": [cell]} for i in ("single|cand:poisson_draw:nhl_goalie", "single|b")]
    assert [r["id"] for r in at.leaderboard(groups, "spread", 10, at._optimistic(m))] == ["single|b"]


def test_a_selection_refused_by_the_database_is_a_skip_not_an_aborted_step(game, monkeypatch):
    real = research_live._select

    def moved(conn, *a, **k):                             # the start changes after the cohorts were written
        conn.execute("UPDATE games SET tipoff_at='2026-10-13T00:00:00Z' WHERE game_id=?", (GID,))
        return real(conn, *a, **k)
    monkeypatch.setattr(research_live, "_select", moved)
    out = run(game, NOW)
    assert out["selections_created"] == 0 and out["status"] == "partial"
    assert any("selection refused" in s["reason"] for v in out["skipped"].values() for s in v)
