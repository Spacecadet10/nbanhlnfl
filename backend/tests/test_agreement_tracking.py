"""Persistent agreement tracking tracking-v2 (docs/MODEL_SELECTION.md §18)."""
import json
import sqlite3

import pytest

from app import agreement_tracking as at
from test_research_live import GID, game, run  # noqa: F401  (fixture)
from test_live_markets import sealed  # noqa: F401
from test_nhl_eval import nhl_store  # noqa: F401

NOW = "2026-10-12T21:30:00Z"


# --- Pure rule semantics ----------------------------------------------------------------------------------------------
def g(kind, *members, **kw):
    return {"kind": kind, "members": list(members), "id": f"{kind}|{'+'.join(members)}", "cohort": "core", "priority": "t", **kw}


def test_unanimous_confidence_is_the_minimum_member_probability_for_the_agreed_outcome():
    v = {"x": (0.70, 0.0, 0.30), "y": (0.62, 0.0, 0.38)}
    r = at.classify(g("unanimous", "x", "y"), v, "winner")
    assert r["status"] == "pick" and r["side"] == 0 and r["statistic"] == pytest.approx(0.62) and r["vector"] is None
    r = at.classify(g("unanimous", "x", "y"), {**v, "y": (0.45, 0.0, 0.55)}, "winner")
    assert r["status"] == "disagree" and r["side"] is None and r["statistic"] is None


def test_average_confidence_is_the_average_probability_of_the_selected_outcome():
    r = at.classify(g("avg", "x", "y"), {"x": (0.70, 0.0, 0.30), "y": (0.40, 0.0, 0.60)}, "winner")
    assert r["side"] == 0 and r["statistic"] == pytest.approx(0.55) and r["vector"] == pytest.approx((0.55, 0.0, 0.45))


def test_majority_records_votes_abstentions_ties_and_its_defined_confidence():
    v = {"a": (0.70, 0.0, 0.30), "b": (0.60, 0.0, 0.40), "c": (0.20, 0.0, 0.80)}
    r = at.classify(g("majority", "a", "b", "c"), v, "winner")
    assert r["side"] == 0 and r["votes"] == {"a": 2, "b": 1, "abstain": 0, "n": 3}
    assert r["statistic"] == pytest.approx((0.70 + 0.60 + 0.20) / 3)       # mean over ALL members, dissenter included
    # An exact 50% member abstains: 1 vote each way is a tie, not a pick.
    r = at.classify(g("majority", "a", "b", "c"), {**v, "b": (0.5, 0.0, 0.5)}, "winner")
    assert r["status"] == "tie" and r["votes"]["abstain"] == 1
    # 2 of 4 is not a strict majority of all members.
    r = at.classify(g("majority", "a", "b", "c", "d"), {**v, "d": (0.3, 0.0, 0.7)}, "winner")
    assert r["status"] == "tie"
    r = at.classify(g("majority", "a", "b", "c", "d"), {**v, "b": (0.5, 0.0, 0.5), "d": (0.6, 0.0, 0.4)}, "winner")
    assert r["status"] == "no_majority" and r["votes"]["a"] == 2 and r["votes"]["abstain"] == 1   # 2 of 4: not strict
    r = at.classify(g("majority", "a", "b", "c", "d", "e"),
                    {**v, "b": (0.5, 0.0, 0.5), "d": (0.6, 0.0, 0.4), "e": (0.55, 0.0, 0.45)}, "winner")
    assert r["status"] == "pick" and r["side"] == 0 and r["votes"] == {"a": 3, "b": 1, "abstain": 1, "n": 5}
    r = at.classify(g("majority", "a", "b", "c", "d", "e"),
                    {**v, "d": (0.5, 0.0, 0.5), "e": (0.5, 0.0, 0.5)}, "winner")
    assert r["status"] == "no_majority"                                     # 2 of 5 leads but is not > 2.5


def test_a_missing_member_makes_the_group_incomplete_never_a_subset():
    r = at.classify(g("avg", "x", "y"), {"x": (0.9, 0.0, 0.1)}, "winner")
    assert r["status"] == "incomplete" and r["missing"] == {"y": "member forecast missing"} and r["side"] is None
    r = at.classify(g("stack", "x", "y", stack_of="core"), {"x": (0.9, 0, 0.1), "y": (0.8, 0, 0.2)}, "winner")
    assert r["status"] == "incomplete" and "stack:core" in r["missing"]
    r = at.classify(g("stack_pool", "x", "y"), {"x": (0.9, 0, 0.1), "y": (0.8, 0, 0.2)}, "winner", None)
    assert r["status"] == "incomplete" and "pool_stacker" in r["missing"]


def test_nhl_three_way_can_pick_the_regulation_draw_and_line_markets_never_pick_the_push():
    v = {"x": (0.30, 0.40, 0.30), "y": (0.32, 0.36, 0.32)}
    r = at.classify(g("unanimous", "x", "y"), v, "reg3")
    assert r["side"] == 1 and r["statistic"] == pytest.approx(0.36)
    r = at.classify(g("single", "x"), {"x": (0.30, 0.40, 0.30)}, "total")
    assert r["status"] == "tie"                                             # the middle is not a side in a line market
    assert at.bands("reg3")[0] == [0.0, 0.40] and at.band_label("reg3", 0.0, 0.40) == "0.33–0.40"
    assert at.band_label("winner", 0.0, 0.55) == "0.50–0.55" and at.band_label("winner", 0.80, 1.0 + 1e-9) == "≥ 0.80"


def items_from(stats, market="winner", realised=0, state="settled"):
    return [{"game_id": f"g{i}", "status": "pick", "side": 0, "statistic": s, "vector": (s, 0.0, 1 - s), "state": state,
             "realised": realised, "missing": {}} for i, s in enumerate(stats)]


def test_threshold_and_band_boundaries_are_inclusive_below_and_exclusive_above():
    items = items_from([0.55, 0.599, 0.6 - 1e-12, 0.65, 0.80, 0.81])     # 0.6 − 1e-12 is float noise for 0.60
    s = at.summarise(g("single", "m"), items, at._Ranked({}, "winner"), "winner", None)
    cum = {c["threshold"]: c["selected"] for c in s["thresholds"]}
    assert cum[0.55] == 6 and cum[0.60] == 4 and cum[0.65] == 3 and cum[0.80] == 2      # 0.599 is below 0.60
    band = {tuple(b["band"]): b["selected"] for b in s["bands"]}
    assert band[(0.55, 0.60)] == 2 and band[(0.60, 0.65)] == 1 and band[(0.80, 1.0)] == 2
    assert sum(b["selected"] for b in s["bands"]) == s["all_picks"]["selected"] == 6       # bands never overlap
    assert s["thresholds"][0]["evidence"] == "insufficient evidence"                     # sparse but visible
    assert s["probabilistic"]["games"] == 6 and s["thresholds"][0]["brier"] is not None


def test_pushes_voids_pending_and_matched_coverage_comparison():
    items = items_from([0.7, 0.7], market="total", realised=1)          # exact line: push
    items += [{**items_from([0.7])[0], "game_id": "v", "state": "void"},
              {**items_from([0.7])[0], "game_id": "p", "state": "pending", "realised": None}]
    items += items_from([0.66], market="total", realised=2)               # a loss
    for i, it in enumerate(items):
        it["game_id"] = f"x{i}"
    single = {it["game_id"]: {**it, "statistic": 0.9 - 0.01 * k} for k, it in enumerate(items)}
    s = at.summarise(g("avg", "m", "n"), items, at._Ranked({"anchor": single}, "total"), "total", "anchor")
    c = s["thresholds"][0]
    assert (c["pushes"], c["voids"], c["pending"], c["losses"], c["wins"], c["decided"]) == (2, 1, 1, 1, 0, 1)
    assert c["win_rate"] == 0.0 and c["matched"][0]["model"] == "anchor" and c["matched"][0]["games"] == 1


# --- Registry ---------------------------------------------------------------------------------------------------------
def test_sealed_registry_respects_the_allocation_cap_and_lists_skipped_groups():
    reg = at.load_registry()
    assert reg["study"] == "tracking-v2" and reg["cap"] == 500
    for key, m in reg["markets"].items():
        n_t = len(at.THRESHOLDS[at.kind_of(m["market"])])
        assert m["configurations"] == len(m["groups"]) * n_t <= 500, key
        ids = [x["id"] for x in m["groups"]]
        assert len(ids) == len(set(ids))
        assert all(s["reason"].startswith("allocation cap") for s in m["skipped"])
        for grp in m["groups"]:
            known = set(m["core"]) | set(m["shadow"]) | set(m["candidates"])
            assert set(grp["members"]) <= known, grp
    assert {k.split(":")[0] for k in reg["markets"]} == {"nba", "nhl"}
    assert "nhl:reg3" in reg["markets"] and "nba:reg3" not in reg["markets"]
    # The least-evaluated triples come first (the audit's gaps): every kept NBA winner triple has availability_two_hour.
    tri = [x for x in reg["markets"]["nba:winner"]["groups"] if x["priority"] == "P5 triple"]
    assert len(tri) >= 20 and all("availability_two_hour" in x["members"] for x in tri[:20])


def test_registry_seal_detects_tampering(tmp_path):
    reg = json.loads(at.REGISTRY_PATH.read_text())
    reg["markets"]["nhl:winner"]["groups"][0]["members"] = ["hockey_elo", "goals_poisson"]
    p = tmp_path / "t.json"
    p.write_text(json.dumps(reg))
    with pytest.raises(at.RegistryError):
        at.load_registry(p)


# --- Recording on a store ---------------------------------------------------------------------------------------------
def small_registry(extra_groups=()):
    groups = [g("single", "hockey_elo", cohort="core", priority="t"),
              g("unanimous", "hockey_elo", "hockey_logistic_xg", cohort="expanded", priority="t"),
              g("avg", "hockey_elo", "cand:x", cohort="core", priority="t"),
              g("majority", "hockey_elo", "goals_poisson", "hockey_logistic", cohort="core", priority="t"),
              g("stack", "hockey_elo", "goals_poisson", "hockey_logistic", "hockey_gbt", stack_of="core", cohort="core",
                priority="t"), *extra_groups]
    for x in groups:
        if x["kind"] == "stack":
            x["id"] = f"stack:core|{'+'.join(x['members'])}"
    m = {"sport": "nhl", "market": "winner", "anchor": "hockey_elo", "core": ["hockey_elo"], "shadow": ["hockey_logistic_xg"],
         "stack_inputs": [], "candidates": ["cand:x"], "thresholds": list(at.THRESHOLDS["winner"]),
         "bands": at.bands("winner"), "configurations": len(groups) * 6, "groups": groups, "skipped": [], "pool_stacker": None}
    return at.seal({"study": "tracking-v2", "version": 1, "effective_from": "2026-10-01T00:00:00Z", "cap": 500,
                    "min_evidence": 100, "kinds": at.KIND_SEMANTICS, "markets": {"nhl:winner": m}})


def candidate(conn, cohort_id, line=None, p=(0.6, 0.0, 0.4), at_="2026-10-12T21:35:00Z", name="cand:x"):
    conn.execute(
        "INSERT INTO candidate_forecasts (game_id, candidate, model_version, market, line, line_source, line_ref, p_a, p_mid, "
        "p_b, params, inputs, fit_id, artifact_sha256, training_cutoff, dataset_version, tipoff_at, generated_at, "
        "idempotency_key) VALUES (?, ?, 'research-cand:x:v1', 'winner', ?, 'none', ?, ?, ?, ?, '{}', '{}', NULL, 'sha', "
        "'2026-06-30T00:00:00Z', 'd', '2026-10-12T23:00:00Z', ?, ?)",
        (GID, name, line, f"research_cohorts:{cohort_id}", *p, at_, f"k{cohort_id}{at_}{name}"))
    conn.commit()


def core_winner(conn):
    return conn.execute("SELECT * FROM research_cohorts WHERE market='winner' AND definition NOT LIKE '%:expanded-v1' "
                        "ORDER BY cohort_id DESC").fetchone()


def obs(conn, group_id=None):
    sql = "SELECT * FROM agreement_observations"
    rows = conn.execute(sql + (" WHERE group_id=?" if group_id else "") + " ORDER BY observation_id",
                        (group_id,) if group_id else ()).fetchall()
    return rows


def test_recording_is_idempotent_immutable_and_uses_the_exact_cohort_and_expanded_pair(game):
    run(game, NOW)
    c = core_winner(game)
    candidate(game, c["cohort_id"])
    reg = small_registry()
    out = at.record(game, game_ids=[GID], now="2026-10-12T21:40:00Z", reg=reg)
    assert out["status"] == "success" and out["created"] == 5, out
    again = at.record(game, game_ids=[GID], now="2026-10-12T21:41:00Z", reg=reg)
    assert again["created"] == 0 and again["already_recorded"] == 5
    u = obs(game, "unanimous|hockey_elo+hockey_logistic_xg")[0]
    assert u["source_cohort_id"] != u["cohort_id"]                    # shadows come from the paired expanded cohort
    a = obs(game, "avg|hockey_elo+cand:x")[0]
    mem = {m["name"]: m for m in json.loads(a["members"])}
    assert mem["cand:x"]["version"] == "research-cand:x:v1" and mem["hockey_elo"]["version"]
    assert a["status"] == "pick" and a["information_at"] == "2026-10-12T21:35:00Z"
    with pytest.raises(sqlite3.DatabaseError):
        game.execute("UPDATE agreement_observations SET statistic=0.99")
    with pytest.raises(sqlite3.DatabaseError):
        game.execute("DELETE FROM agreement_observations")
    game.rollback()


def test_candidate_made_for_another_cohort_or_line_is_not_merged(game):
    run(game, NOW)
    c = core_winner(game)
    candidate(game, c["cohort_id"] + 999)                            # refers to another cohort
    reg = small_registry()
    at.record(game, game_ids=[GID], now="2026-10-12T21:40:00Z", reg=reg)
    a = obs(game, "avg|hockey_elo+cand:x")[0]
    assert a["status"] == "incomplete" and json.loads(a["missing"]) == {"cand:x": "member forecast missing"}


def test_late_information_never_reaches_an_earlier_observation(game):
    run(game, NOW)
    first = core_winner(game)
    reg = small_registry()
    at.record(game, game_ids=[GID], now="2026-10-12T21:40:00Z", reg=reg)
    before = obs(game, "avg|hockey_elo+cand:x")
    assert len(before) == 1 and before[0]["status"] == "incomplete"     # the candidate had not been made yet
    candidate(game, first["cohort_id"], at_="2026-10-12T21:50:00Z")    # arrives later for the same cohort and line
    at.record(game, game_ids=[GID], now="2026-10-12T21:55:00Z", reg=reg)
    rows = obs(game, "avg|hockey_elo+cand:x")
    assert len(rows) == 2 and dict(rows[0]) == dict(before[0])          # the earlier row is unchanged
    assert rows[1]["status"] == "pick" and rows[1]["information_at"] == "2026-10-12T21:50:00Z"
    assert rows[1]["information_at"] > rows[0]["information_at"]
    # A group whose inputs did not change adds nothing.
    assert len(obs(game, "single|hockey_elo")) == 1
    rep = at.report(game, "nhl", "winner", reg=reg)
    s = next(x for x in rep["groups"] if x["id"] == "avg|hockey_elo+cand:x")
    assert s["eligible"] == 1 and s["status_counts"] == {"pick": 1} and s["all_picks"]["pending"] == 1


def test_nothing_is_recorded_at_or_after_the_start(game):
    run(game, NOW)
    reg = small_registry()
    out = at.record(game, game_ids=[GID], now="2026-10-12T23:00:00Z", reg=reg)
    assert out["created"] == 0 and out["cohorts"] == 0
    c = core_winner(game)
    with pytest.raises(sqlite3.DatabaseError):                           # the trigger is the backstop
        game.execute("INSERT INTO agreement_observations (registry, group_id, sport, game_id, market, line, line_source, "
                     "cohort_id, source_cohort_id, definition, kind, members, version_sig, status, information_at, tipoff_at, "
                     "recorded_at) VALUES ('r', 'x', 'nhl', ?, 'winner', NULL, NULL, ?, ?, 'd', 'single', '[]', 's', "
                     "'incomplete', ?, ?, '2026-10-12T23:00:01Z')", (GID, c["cohort_id"], c["cohort_id"], c["information_at"],
                                                                     c["tipoff_at"]))
    game.rollback()


def test_research_recording_continues_while_recommendations_abstain(game):
    """No selector has evidence (every decision is insufficient evidence / no pick), yet every group is recorded."""
    from app import selection_status
    run(game, NOW)
    st = selection_status.status(game, NOW)
    assert all(m.get("status") != "active" for m in st.get("markets", []))
    reg = small_registry()
    out = at.record(game, game_ids=[GID], now="2026-10-12T21:40:00Z", reg=reg)
    assert out["created"] == len(reg["markets"]["nhl:winner"]["groups"])


def test_settled_game_is_scored(game):
    from test_live_markets import add_upcoming
    run(game, NOW)
    reg = small_registry()
    at.record(game, game_ids=[GID], now="2026-10-12T21:40:00Z", reg=reg)
    add_upcoming(game, start="2026-10-12T19:00:00", state=7, hs=4, vs=1, period=3, at="2026-10-13T02:00:00Z")
    rep = at.report(game, "nhl", "winner", reg=reg)
    s = next(x for x in rep["groups"] if x["id"] == "single|hockey_elo")["all_picks"]
    assert s["wins"] + s["losses"] == 1 and s["pending"] == 0 and s["voids"] == 0


def test_a_rescheduled_game_is_void(game):
    from test_live_markets import add_upcoming
    run(game, NOW)
    reg = small_registry()
    at.record(game, game_ids=[GID], now="2026-10-12T21:40:00Z", reg=reg)
    add_upcoming(game, start="2026-10-14T19:00:00", at="2026-10-12T22:00:00Z")    # start moved after the observation
    s = next(x for x in at.report(game, "nhl", "winner", reg=reg)["groups"] if x["id"] == "single|hockey_elo")["all_picks"]
    assert s["voids"] == 1 and s["decided"] == 0 and s["pending"] == 0


def test_two_hour_cycle_records_tracking_after_the_pool(game, monkeypatch):
    from app import jobs
    seen = {}
    monkeypatch.setattr(jobs, "agreement_tracking", lambda conn, **kw: seen.setdefault("kw", kw) and {"status": "success"})
    import inspect
    src = inspect.getsource(jobs.predict_now)
    assert src.index("candidate_pool(") < src.index("pool_selection(") < src.index("agreement_tracking(")


def test_tracking_and_training_api_match_the_frontend_types(game):
    """API/frontend consistency on a real store: every field the TypeScript types declare is in the JSON."""
    from fastapi.testclient import TestClient
    from app import main
    from app.providers.real import RealProvider
    from test_research_contract import fields
    run(game, NOW)
    at.record(game, game_ids=[GID], now="2026-10-12T21:40:00Z")              # the sealed registry
    from app import db
    game.commit()
    path = game.execute("PRAGMA database_list").fetchone()["file"]

    def provider():                     # like production: a fresh connection per request
        conn = db.connect(path, check_same_thread=False)
        try:
            yield RealProvider(conn, now="2026-10-12T21:45:00Z")
        finally:
            conn.close()
    main.app.dependency_overrides[main.get_provider] = provider
    try:
        c = TestClient(main.app)
        r = c.get("/api/research/tracking", params={"sport": "nhl", "market": "winner"}).json()
        assert fields("TrackingReport") - {"recommendation"} <= set(r) and r["status"] == "recording" and r["games"] == 1
        g = r["groups"][0]
        assert fields("TrackingGroup") <= set(g)
        assert fields("TrackingCell") - {"threshold", "matched", "band", "label"} <= set(g["thresholds"][0])
        assert {"band", "label"} <= set(g["bands"][0]) and "threshold" in g["thresholds"][0]
        assert len(g["bands"]) == len(at.THRESHOLDS["winner"]) + 1
        # Filters: rule kind and member narrow the groups; the leaderboard stays exploratory and empty without evidence.
        maj = c.get("/api/research/tracking", params={"sport": "nhl", "market": "winner", "kind": "majority"}).json()
        assert maj["groups"] and all(x["kind"] == "majority" for x in maj["groups"]) and maj["leaderboard"] == []
        mem = c.get("/api/research/tracking", params={"sport": "nhl", "market": "winner", "member": "hockey_elo,hockey_gbt"}).json()
        assert all({"hockey_elo", "hockey_gbt"} <= set(x["members"]) for x in mem["groups"])
        hist = c.get("/api/research/tracking", params={"sport": "nhl", "market": "winner", "evidence": "historical"}).json()
        assert hist["evidence"] == "historical" and "never recorded live" in hist["note"]
        gm = c.get(f"/api/research/tracking/game/{GID}", params={"sport": "nhl"}).json()
        assert fields("TrackingGame") <= set(gm) and fields("TrackingGameMarket") <= set(gm["markets"][0])
        tr = c.get("/api/research/training", params={"sport": "nhl"}).json()
        assert fields("TrainingStatus") <= set(tr)
        if tr["fits"]:
            assert fields("TrainingFit") <= set(tr["fits"][0])
    finally:
        main.app.dependency_overrides.pop(main.get_provider, None)
