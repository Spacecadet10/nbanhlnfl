"""exact-rules-v1 (docs/MODEL_SELECTION.md, amendment): registered exact subsets with per-member thresholds, tracked
prospectively through tracking-v2 (kind 'exact'). Registry sealing and refusals, classification consistency with the
explorer, recording in the two-hour tracking step, settlement/report, versioning, migration 0028 and isolation."""
import json
import sqlite3

import pytest

from app import agreement_tracking as at, db, exact_rules as er
from test_research_live import GID, game, run  # noqa: F401  (fixture)
from test_live_markets import sealed  # noqa: F401
from test_nhl_eval import nhl_store  # noqa: F401
from test_agreement_tracking import NOW, candidate, core_winner, obs, small_registry

REG_AT = "2026-10-12T21:35:00Z"        # registration time: before the synthetic game's 23:00Z start
REC_AT = "2026-10-12T21:40:00Z"


def register(tmp_path, spec="hockey_elo@0.65,goals_poisson@0.6", sport="nhl", market="winner", now=REG_AT, **kw):
    kw.setdefault("note", "test rule")
    return er.register(sport, market, spec, now=now, path=tmp_path / "exact_rules.json", **kw)


# --- Registry: mapping, refusals, sealing, versioning ----------------------------------------------------------------
def test_pregame_mapping_names_core_shadow_and_live_candidates_only():
    src = er.pregame_sources("nhl", "winner")
    assert src["hockey_elo"].startswith("core cohort") and src["hockey_logistic_xg"].startswith("shadow member")
    assert src["logit_l2:nhl_base"].startswith("candidate forecast")
    assert "keras_mlp:nhl_base" not in src and "torch_mlp:nhl_base" not in src       # deep candidates: never tracked live
    assert "cand:logit_l2:nhl_base@adaptive" not in src                                 # adaptive exports: no pregame path
    assert er.validate("nhl", "winner", ["hockey_elo", "hockey_logistic_xg"], [0.6, 0.6])["cohort"] == "expanded"
    assert er.validate("nhl", "winner", ["hockey_elo", "goals_poisson"], [0.6, 0.6])["cohort"] == "core"


@pytest.mark.parametrize("spec, sport, market, kw, reason", [
    ("cand:logit_l2:nhl_base@adaptive@0.6,hockey_elo@0.6", "nhl", "winner", {}, "adaptive-source"),
    ("hockey_elo@0.6,logit_l2:nhl_goalie@0.6", "nhl", "winner", {}, "goalie"),
    ("hockey_elo@0.6,keras_mlp:nhl_base@0.6", "nhl", "winner", {}, "not recorded in the pregame path"),
    ("hockey_elo@0.6,no_such_model@0.6", "nhl", "winner", {}, "not recorded in the pregame path"),
    ("hockey_elo@0.65", "nhl", "winner", {}, "at least two members"),
    ("hockey_elo@0.63,goals_poisson@0.6", "nhl", "winner", {}, "not on the explorer grid"),
    ("hockey_elo@0.6,hockey_elo@0.7", "nhl", "winner", {}, "distinct"),
    ("elo@0.6,logistic@0.6", "nba", "reg3", {}, "not a NBA market"),
    ("hockey_elo@0.6,goals_poisson@0.6", "nhl", "winner", {"note": ""}, "provenance needed"),
])
def test_registration_refuses_unsupported_mappings_explicitly(tmp_path, spec, sport, market, kw, reason):
    with pytest.raises(er.RegistryError, match=reason):
        register(tmp_path, spec, sport=sport, market=market, **kw)
    assert not (tmp_path / "exact_rules.json").exists()                               # nothing written on refusal


def test_registration_is_sealed_versioned_and_never_edits_an_earlier_rule(tmp_path):
    p = tmp_path / "exact_rules.json"
    one = register(tmp_path)
    reg = er.load_registry(p)
    assert reg["version"] == 1 and reg["sha256"] == one["sha256"] and len(reg["rules"]) == 1
    r = reg["rules"][0]
    assert r["id"] == "exact|nhl:winner|hockey_elo@0.65+goals_poisson@0.6" and r["thresholds"] == [0.65, 0.6]
    assert r["effective_from"] == REG_AT == r["registered_at"] and r["cohort"] == "core" and r["registered_version"] == 1
    assert r["provenance"]["members"]["hockey_elo"].startswith("core cohort") and r["rule_sha256"] == er.rule_sha256(r)
    with pytest.raises(er.RegistryError, match="already registered"):
        register(tmp_path, now="2026-10-12T21:36:00Z")
    two = register(tmp_path, "hockey_elo@0.7,hockey_gbt@0.55", now="2026-10-12T21:36:00Z")
    reg2 = er.load_registry(p)
    assert reg2["version"] == 2 == two["version"] and reg2["rules"][0] == r                   # the earlier rule is byte-identical
    assert [h["version"] for h in reg2["history"]] == [1, 2] and reg2["history"][1]["previous_sha256"] == one["sha256"]
    assert reg2["rules"][1]["rule_sha256"] != r["rule_sha256"]
    # Tampering: a changed byte breaks the seal; a resealed file with an edited rule breaks the rule's own digest.
    raw = json.loads(p.read_text())
    raw["rules"][0]["thresholds"] = [0.5, 0.5]
    p.write_text(json.dumps(raw))
    with pytest.raises(er.RegistryError, match="seal"):
        er.load_registry(p)
    p.write_text(json.dumps(er.seal({k: v for k, v in raw.items() if k != "sha256"})))
    with pytest.raises(er.RegistryError, match="digest"):
        er.load_registry(p)
    assert er.load_registry(tmp_path / "absent.json") is None


def test_registration_is_never_backdated(tmp_path):
    with pytest.raises(er.RegistryError, match="never backdated"):
        register(tmp_path, effective_from="2026-10-12T21:34:59Z")
    out = register(tmp_path, effective_from="2026-10-12T22:00:00Z")           # later than now: allowed, kept as given
    assert out["rule"]["effective_from"] == "2026-10-12T22:00:00Z" and out["rule"]["registered_at"] == REG_AT
    with pytest.raises(er.RegistryError, match="ISO UTC"):
        register(tmp_path, "hockey_elo@0.7,hockey_gbt@0.55", effective_from="yesterday")


def test_evidence_provenance_needs_the_rule_in_a_frozen_list(tmp_path):
    rule = {"members": ["hockey_elo", "goals_poisson"], "thresholds": [0.65, 0.6], "later_pick_set": 3,
            "earlier": {"record": {"picks": 120, "wins": 80, "losses": 40}}, "later": {"record": {"picks": 50, "wins": 30, "losses": 20}}}
    ev = tmp_path / "subsets.json"
    ev.write_text(json.dumps({"sport": "nhl", "market": "winner", "source": "pool", "frozen": [{"members": ["x"], "thresholds": [0.5],
                              "earlier": {"record": {}}, "later": {"record": {}}}, rule], "frozen_lift": []}))
    out = register(tmp_path, evidence=ev, note="")
    px = out["rule"]["provenance"]["explorer"]
    assert px["list"] == "reliable" and px["rank"] == 2 and px["later"] == rule["later"]["record"] and px["later_pick_set"] == 3
    assert px["sha256"] and px["file"] == str(ev) and out["rule"]["provenance"]["source"] == "pool"
    with pytest.raises(er.RegistryError, match="not in a frozen list"):
        register(tmp_path, "hockey_elo@0.7,hockey_gbt@0.55", evidence=ev, note="")


# --- Classification -----------------------------------------------------------------------------------------------
def x(*members, thresholds):
    return {"kind": "exact", "members": list(members), "thresholds": list(thresholds), "id": "x", "cohort": "core", "priority": "t"}


def test_exact_classification_reasons_and_descriptive_minimum_statistic():
    v = {"a": (0.70, 0.0, 0.30), "b": (0.62, 0.0, 0.38)}
    r = at.classify(x("a", "b", thresholds=[0.65, 0.60]), v, "winner")
    assert r["status"] == "pick" and r["side"] == 0 and r["statistic"] == pytest.approx(0.62) and r["vector"] is None
    r = at.classify(x("a", "b", thresholds=[0.65, 0.65]), v, "winner")
    assert r["status"] == "below_threshold" and r["side"] is None and r["statistic"] is None
    assert r["missing"] == {"b": "confidence 0.6200 below its threshold 0.65"}
    r = at.classify(x("a", "b", thresholds=[0.5, 0.5]), {**v, "b": (0.45, 0.0, 0.55)}, "winner")
    assert r["status"] == "disagree" and r["missing"] == {"a": "picks a", "b": "picks b"}
    r = at.classify(x("a", "b", thresholds=[0.5, 0.5]), {**v, "b": (0.5, 0.0, 0.5)}, "winner")
    assert r["status"] == "tie" and list(r["missing"]) == ["b"]
    r = at.classify(x("a", "b", thresholds=[0.5, 0.5]), {"a": v["a"]}, "winner")
    assert r["status"] == "incomplete" and r["missing"] == {"b": "member forecast missing"}
    # Line markets: confidence is the probability among the allowed sides (the push mass is left out), as in the explorer.
    r = at.classify(x("a", "b", thresholds=[0.7, 0.7]), {"a": (0.6, 0.2, 0.2), "b": (0.56, 0.2, 0.24)}, "spread")
    assert r["status"] == "pick" and r["statistic"] == pytest.approx(0.7)
    assert at.exact_confidence((0.6, 0.2, 0.2), 0, "spread") == pytest.approx(0.75)
    # A boundary value counts (the explorer's tolerance), and an exact rule is never an ensemble probability.
    r = at.classify(x("a", "b", thresholds=[0.7, 0.7 + 1e-12]), {"a": (0.7, 0.0, 0.3), "b": (0.7, 0.0, 0.3)}, "winner")
    assert r["status"] == "pick" and "exact" not in at.PROBABILISTIC
    with pytest.raises(ValueError):
        at.classify(x("a", "b", thresholds=[0.7]), v, "winner")


def test_exact_classification_picks_exactly_the_explorer_qualifying_games():
    from app import combination_thresholds as ct
    import random
    rng = random.Random(3)
    rows = []
    for i in range(300):
        season = 2021 if i < 200 else 2023
        vec = {}
        for m in "AB":
            if rng.random() < 0.1:
                continue                                         # a missing forecast (ineligible in both)
            p = rng.choice([0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.45, 0.3])
            vec[m] = [p, 0.0, round(1 - p, 10)]
        rows.append({"game_id": f"g{i}", "season": season, "cutoff_at": f"{season}-01-01T12:00:00Z", "tipoff_at": f"{season}-01-01T23:00:00Z",
                     "line": None, "realised": rng.choice([0, 2]), "vectors": vec})
    t = ct.Table("winner", rows, ["A", "B"], later_seasons=[2023])
    members, ths = ["A", "B"], [0.65, 0.6]
    elig, s0, qual = ct._qualifying(t, members, ths)
    picks = 0
    for i, r in enumerate(t.rows):                                   # the table's own (sorted) row order
        res = at.classify(x(*members, thresholds=ths), {m: tuple(v) for m, v in r["vectors"].items()}, "winner")
        assert (res["status"] == "pick") == bool(qual[i]), (i, r["vectors"], res)
        if res["status"] == "pick":
            picks += 1
            assert res["side"] == int(s0[i])
            assert res["statistic"] == pytest.approx(min(float(t.conf[m][i]) for m in members))
    assert picks == int(qual.sum()) > 10


# --- Recording in the tracking step ------------------------------------------------------------------------------------
def exact_rows(conn):
    return conn.execute("SELECT * FROM agreement_observations WHERE kind='exact' ORDER BY observation_id").fetchall()


def test_exact_rules_are_recorded_with_the_sealed_groups_and_retries_add_nothing(game, tmp_path):
    run(game, NOW)
    ex = er.load_registry(register(tmp_path)["path"] and tmp_path / "exact_rules.json")
    out = at.record(game, game_ids=[GID], now=REC_AT, reg=small_registry(), exact_registry=ex)
    assert out["status"] == "success" and out["created"] == 5 and out["exact"]["created"] == 1, out
    assert out["exact"]["status"] == "recording" and out["exact"]["rules"] == 1 and out["exact"]["registry_version"] == 1
    (row,) = exact_rows(game)
    rule = ex["rules"][0]
    assert row["registry"] == rule["rule_sha256"] and row["group_id"] == rule["id"] and row["kind"] == "exact"
    assert row["status"] in ("pick", "below_threshold", "disagree", "tie") and row["vector"] == "null" and row["votes"] == "null"
    mem = {m["name"]: m for m in json.loads(row["members"])}
    assert set(mem) == {"hockey_elo", "goals_poisson"} and mem["hockey_elo"]["threshold"] == 0.65 and mem["hockey_elo"]["version"]
    assert row["recorded_at"] == REC_AT >= rule["effective_from"] and row["tipoff_at"] > rule["effective_from"]
    assert row["information_at"] < row["tipoff_at"]
    # The classification equals the pure rule on the cohort's stored inputs.
    c = core_winner(game)
    vectors, _, _ = at._inputs(game, c, None, REC_AT)
    res = at.classify({"kind": "exact", "members": rule["members"], "thresholds": rule["thresholds"]}, vectors, "winner")
    assert row["status"] == res["status"] and (row["statistic"] or None) == (res["statistic"] and pytest.approx(res["statistic"]))
    again = at.record(game, game_ids=[GID], now="2026-10-12T21:41:00Z", reg=small_registry(), exact_registry=ex)
    assert again["exact"]["created"] == 0 and again["exact"]["already_recorded"] == 1 and again["already_recorded"] == 5
    assert len(exact_rows(game)) == 1
    with pytest.raises(sqlite3.DatabaseError):
        game.execute("UPDATE agreement_observations SET status='pick', side='a', statistic=0.9 WHERE kind='exact'")
    with pytest.raises(sqlite3.DatabaseError):
        game.execute("DELETE FROM agreement_observations WHERE kind='exact'")
    game.rollback()


def test_no_observation_is_backfilled_before_registration(game, tmp_path):
    run(game, NOW)
    reg = small_registry()
    # Registered after the game starts: the game is never observed, the sealed groups are.
    late = er.load_registry(register(tmp_path, now="2026-10-12T22:30:00Z", effective_from="2026-10-12T23:30:00Z")["path"] and tmp_path / "exact_rules.json")
    out = at.record(game, game_ids=[GID], now="2026-10-12T22:40:00Z", reg=reg, exact_registry=late)
    assert out["created"] == 5 and out["exact"]["created"] == 0 and out["exact"]["before_registration"] == 1 and exact_rows(game) == []
    # Effective later than the cycle: skipped now, recorded by a later cycle before the start.
    soon = er.load_registry(register(tmp_path / "b", now="2026-10-12T21:30:00Z", effective_from="2026-10-12T22:00:00Z")["path"] and tmp_path / "b" / "exact_rules.json")
    out = at.record(game, game_ids=[GID], now="2026-10-12T21:50:00Z", reg=reg, exact_registry=soon)
    assert out["exact"]["created"] == 0 and out["exact"]["before_registration"] == 1
    out = at.record(game, game_ids=[GID], now="2026-10-12T22:10:00Z", reg=reg, exact_registry=soon)
    assert out["exact"]["created"] == 1 and exact_rows(game)[0]["recorded_at"] == "2026-10-12T22:10:00Z"
    # The trigger stays the backstop for anything at or after the start.
    out = at.record(game, game_ids=[GID], now="2026-10-12T23:00:00Z", reg=reg, exact_registry=soon)
    assert out["exact"]["created"] == 0 and out["cohorts"] == 0


def test_changed_inputs_add_a_row_and_rule_errors_stay_visible_without_blocking_the_groups(game, tmp_path):
    run(game, NOW)
    reg = small_registry()
    ex = er.load_registry(register(tmp_path, "hockey_elo@0.6,logit_l2:nhl_base@0.6")["path"] and tmp_path / "exact_rules.json")
    out = at.record(game, game_ids=[GID], now=REC_AT, reg=reg, exact_registry=ex)
    (first,) = exact_rows(game)
    assert first["status"] == "incomplete" and json.loads(first["missing"]) == {"logit_l2:nhl_base": "member forecast missing"}
    c = core_winner(game)
    candidate(game, c["cohort_id"], at_="2026-10-12T21:50:00Z", name="logit_l2:nhl_base", p=(0.7, 0.0, 0.3))
    out = at.record(game, game_ids=[GID], now="2026-10-12T21:55:00Z", reg=reg, exact_registry=ex)
    rows = exact_rows(game)
    assert out["exact"]["created"] == 1 and len(rows) == 2 and dict(rows[0]) == dict(first)
    assert rows[1]["status"] != "incomplete" and rows[1]["information_at"] == "2026-10-12T21:50:00Z"
    mem = {m["name"]: m for m in json.loads(rows[1]["members"])}
    assert mem["logit_l2:nhl_base"]["version"] == "research-cand:x:v1" and rows[1]["version_sig"] != first["version_sig"]
    # A corrupted rule (thresholds do not match the members) is an error entry; the sealed groups and other rules proceed.
    broken = json.loads(json.dumps(ex))
    broken["rules"][0]["thresholds"] = [0.6]
    out = at.record(game, game_ids=[GID], now="2026-10-12T21:56:00Z", reg=reg, exact_registry=broken)
    assert out["status"] == "partial" and any(k.endswith(ex["rules"][0]["id"]) for k in out["errors"]) and out["already_recorded"] == 5
    # A store without migration 0028: exact rules are skipped with the reason, the sealed groups are recorded.
    game.execute("DELETE FROM schema_migrations WHERE version=28")
    game.commit()
    out = at.record(game, game_ids=[GID], now="2026-10-12T21:57:00Z", reg=reg, exact_registry=ex)
    assert out["exact"]["status"] == "skipped" and "0028" in out["exact"]["reason"] and out["status"] == "success"
    assert out["already_recorded"] == 5 and len(exact_rows(game)) == 2


def test_a_broken_registry_file_is_reported_and_never_stops_the_cycle(game, tmp_path, monkeypatch):
    from app import jobs
    run(game, NOW)
    p = tmp_path / "exact_rules.json"
    p.write_text("{not json")
    monkeypatch.setattr(er, "REGISTRY_PATH", p)
    monkeypatch.setattr(at, "load_registry", lambda path=None: small_registry())
    out = jobs.agreement_tracking(game, game_ids=[GID], now=REC_AT)          # the production path: both registries from files
    assert out["status"] == "partial" and out["created"] == 5 and out["exact"]["status"] == "failed"
    assert "exact_rules" in out["errors"] and exact_rows(game) == []


# --- Settlement, report, API, versioning, isolation ---------------------------------------------------------------------
def test_settlement_report_api_and_separation_from_historical_evidence(game, tmp_path, monkeypatch):
    from test_live_markets import add_upcoming
    from fastapi.testclient import TestClient
    from app import main
    from app.providers.real import RealProvider
    from test_research_contract import fields
    run(game, NOW)
    reg = small_registry()
    path = tmp_path / "exact_rules.json"
    register(tmp_path, "hockey_elo@0.5,goals_poisson@0.5")                  # the lowest grid value: a pick whenever they agree
    ex = er.load_registry(path)
    at.record(game, game_ids=[GID], now=REC_AT, reg=reg, exact_registry=ex)
    rule = ex["rules"][0]
    before = at.exact_report(game, "nhl", "winner", reg=reg, exact_registry=ex)
    (s,) = before["rules"]
    assert before["status"] == "recording" and s["id"] == rule["id"] and s["eligible"] == 1 and s["member_thresholds"] == [0.5, 0.5]
    assert s["kind"] == "exact" and s["probabilistic"] is None and s["effective_from"] == REG_AT and s["rule_sha256"] == rule["rule_sha256"]
    picked = s["status_counts"].get("pick", 0) == 1
    assert s["all_picks"]["pending"] == (1 if picked else 0) and s["all_picks"]["decided"] == 0
    add_upcoming(game, start="2026-10-12T19:00:00", state=7, hs=4, vs=1, period=3, at="2026-10-13T02:00:00Z")
    rep = at.report(game, "nhl", "winner", reg=reg, exact_registry=ex)
    (s,) = rep["exact_rules"]["rules"]
    c = s["all_picks"]
    if picked:
        assert c["wins"] + c["losses"] == 1 and c["pending"] == 0 and c["voids"] == 0 and c["win_rate"] in (0.0, 1.0)
        assert c["mean_confidence"] is not None and c["brier"] is None         # descriptive statistic, no probability score
    else:
        assert c["selected"] == 0 and s["status_counts"] and "pick" not in s["status_counts"]
    assert s["versions"] and s["first_observed"] == s["last_observed"] == "2026-10-12T23:00:00Z"
    # Historical evidence never carries an exact-rule record; the sealed groups' report is unchanged by exact rules.
    hist = at.report(game, "nhl", "winner", evidence="historical", reg=reg, exact_registry=ex)
    assert hist["exact_rules"]["status"] == "not_applicable" and hist["exact_rules"]["rules"] == []
    assert [g["id"] for g in rep["groups"]] == [g["id"] for g in reg["markets"]["nhl:winner"]["groups"]]
    # API: the production path loads the registry file; the frontend types name every field.
    monkeypatch.setattr(er, "REGISTRY_PATH", path)
    monkeypatch.setattr(at, "load_registry", lambda path=None: reg)
    game.commit()
    store = game.execute("PRAGMA database_list").fetchone()["file"]

    def provider():
        conn = db.connect(store, check_same_thread=False)
        try:
            yield RealProvider(conn, now="2026-10-13T03:00:00Z")
        finally:
            conn.close()
    main.app.dependency_overrides[main.get_provider] = provider
    try:
        cl = TestClient(main.app)
        r = cl.get("/api/research/tracking", params={"sport": "nhl", "market": "winner"}).json()
        optional = {"registry_version", "registry_sha256", "statistic", "kinds", "registration", "versions_note",
                    "error", "reason", "registered_rules"}          # state-specific fields (failed / schema_required)
        assert fields("ExactRulesReport") - optional <= set(r["exact_rules"]) and "versions_note" in r["exact_rules"]
        assert r["exact_rules"]["status"] == "recording" and fields("ExactRule") - {"provenance"} <= set(r["exact_rules"]["rules"][0])
        assert "CLI only" in r["exact_rules"]["registration"]
        h = cl.get("/api/research/tracking", params={"sport": "nhl", "market": "winner", "evidence": "historical"}).json()
        assert h["exact_rules"]["status"] == "not_applicable"
        gm = cl.get(f"/api/research/tracking/game/{GID}", params={"sport": "nhl"}).json()
        kinds = {p["kind"] for m in gm["markets"] for p in m["picks"]}
        assert gm["status"] == "recorded" and (not picked or "exact" in kinds)       # the game card sees the rule's own digest
    finally:
        main.app.dependency_overrides.pop(main.get_provider, None)


def test_recording_changes_no_other_table_and_keeps_the_sealed_groups_unchanged(game, tmp_path):
    run(game, NOW)
    reg = small_registry()
    tables = ("predictions", "near_tipoff_forecasts", "market_forecasts", "research_cohorts", "research_selections",
              "selector_decisions", "candidate_forecasts", "pool_selections", "adaptive_forecasts", "adaptive_fits", "games", "job_runs")
    digest = lambda: {t: game.execute(f"SELECT COUNT(*), COALESCE(SUM(LENGTH(CAST(rowid AS TEXT))), 0) FROM {t}").fetchone()[:] for t in tables}  # noqa: E731
    base = at.record(game, game_ids=[GID], now=REC_AT, reg=reg)                  # no exact registry given: sealed groups only
    assert base["exact"]["status"] == "none" and base["created"] == 5
    before = digest()
    sealed_rows = [dict(r) for r in game.execute("SELECT * FROM agreement_observations WHERE kind!='exact' ORDER BY observation_id")]
    ex = er.load_registry(register(tmp_path)["path"] and tmp_path / "exact_rules.json")
    out = at.record(game, game_ids=[GID], now="2026-10-12T21:41:00Z", reg=reg, exact_registry=ex)
    assert out["exact"]["created"] == 1 and out["created"] == 0 and out["already_recorded"] == 5
    assert digest() == before
    assert [dict(r) for r in game.execute("SELECT * FROM agreement_observations WHERE kind!='exact' ORDER BY observation_id")] == sealed_rows


# --- Migration 0028 --------------------------------------------------------------------------------------------------
def test_migration_0028_keeps_rows_identity_index_and_triggers_and_widens_the_checks(game, tmp_path):
    """Rebuild the table under the 0022 definition from real rows, then apply 0028 again on this disposable store."""
    run(game, NOW)
    reg = small_registry()
    at.record(game, game_ids=[GID], now=REC_AT, reg=reg)
    old = (db.MIGRATIONS_DIR / "0022_agreement_observation_inputs.sql").read_text()
    with db.transaction(game):
        for stmt in db._split(old):          # 0022 recreates the table (narrow CHECKs), copies rows, index and triggers
            game.execute(stmt)
        game.execute("DELETE FROM schema_migrations WHERE version=28")
    rows_before = [dict(r) for r in game.execute("SELECT * FROM agreement_observations ORDER BY observation_id")]
    assert len(rows_before) == 5 and {r["inputs_digest"] for r in rows_before} == {"0021"}   # the pre-0028 shape
    with pytest.raises(sqlite3.IntegrityError):
        game.execute("INSERT INTO agreement_observations (registry, group_id, sport, game_id, market, cohort_id, source_cohort_id, "
                     "definition, kind, members, version_sig, inputs_digest, status, information_at, tipoff_at, recorded_at) "
                     "SELECT registry, 'k', sport, game_id, market, cohort_id, source_cohort_id, definition, 'exact', members, "
                     "version_sig, 'd', status, information_at, tipoff_at, recorded_at FROM agreement_observations LIMIT 1")
    game.rollback()
    assert db.migrate(game) == ["0028_exact_rule_observations.sql"]
    rows_after = [dict(r) for r in game.execute("SELECT * FROM agreement_observations ORDER BY observation_id")]
    assert rows_after == rows_before
    names = {r[0] for r in game.execute("SELECT name FROM sqlite_master WHERE tbl_name='agreement_observations'")}
    assert {"agreement_observations_market", "agreement_observations_no_update", "agreement_observations_no_delete",
            "agreement_observations_guard"} <= names and "agreement_observations_new" not in names
    sql = game.execute("SELECT sql FROM sqlite_master WHERE name='agreement_observations'").fetchone()[0]
    assert "'exact'" in sql and "'below_threshold'" in sql and "UNIQUE (registry, group_id, source_cohort_id, inputs_digest)" in sql
    base = rows_before[0]
    cols = ("registry", "group_id", "sport", "game_id", "market", "line", "line_source", "cohort_id", "source_cohort_id", "definition",
            "members", "version_sig", "information_at", "tipoff_at", "recorded_at")
    ins = ("INSERT INTO agreement_observations (" + ", ".join(cols) + ", kind, inputs_digest, status, missing) VALUES (" +
           ", ".join("?" * len(cols)) + ", ?, ?, ?, ?)")
    vals = [base[c] for c in cols]
    game.execute(ins, [*vals, "exact", "d1", "below_threshold", '{"m": "confidence 0.5 below its threshold 0.6"}'])
    with pytest.raises(sqlite3.IntegrityError):                                   # still only the listed kinds and statuses
        game.execute(ins, [*vals, "weird", "d2", "pick", "{}"])
    with pytest.raises(sqlite3.IntegrityError):
        game.execute(ins, [*vals, "exact", "d3", "almost", "{}"])
    with pytest.raises(sqlite3.IntegrityError):                                   # the identity is unchanged
        game.execute(ins, [*vals, "exact", "d1", "below_threshold", "{}"])
    with pytest.raises(sqlite3.DatabaseError):                                    # the triggers are back
        game.execute("UPDATE agreement_observations SET statistic=0.5")
    with pytest.raises(sqlite3.DatabaseError):
        game.execute("DELETE FROM agreement_observations")
    with pytest.raises(sqlite3.DatabaseError):                                    # the pregame guard is back
        game.execute(ins, [*vals[:-1], "2026-10-12T23:00:01Z", "exact", "d4", "incomplete", "{}"])
    game.rollback()


# --- CLI ---------------------------------------------------------------------------------------------------------------
def test_cli_registration_is_file_only_and_refusals_exit_nonzero(tmp_path, monkeypatch, capsys):
    from app import cli
    p = tmp_path / "exact_rules.json"
    monkeypatch.setattr(er, "REGISTRY_PATH", p)
    assert cli.main(["--sport", "nhl", "exact-rules-register", "--market", "winner", "--rule", "hockey_elo@0.65,goals_poisson@0.6",
                     "--note", "chosen in the explorer", "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "dry_run" and not p.exists()
    assert cli.main(["--sport", "nhl", "exact-rules-register", "--market", "winner", "--rule", "hockey_elo@0.63,goals_poisson@0.6",
                     "--note", "x"]) == 1
    assert "not on the explorer grid" in json.loads(capsys.readouterr().out)["reason"] and not p.exists()
    assert cli.main(["--sport", "nhl", "exact-rules-register", "--market", "winner", "--rule", "hockey_elo@0.65,goals_poisson@0.6",
                     "--note", "chosen in the explorer"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "registered" and p.exists() and er.load_registry(p)["version"] == 1
    assert cli.main(["exact-rules-list"]) == 0 and json.loads(capsys.readouterr().out)["rules"][0]["id"] == out["rule"]["id"]


# --- Review corrections (independent review 2026-10-11): reproductions first --------------------------------------------
def _reg_bytes(tmp_path):
    return (tmp_path / "exact_rules.json").read_bytes()


def test_reordered_members_are_the_same_rule_and_the_registry_is_untouched(tmp_path):
    register(tmp_path, "hockey_elo@0.65,goals_poisson@0.6")
    before = _reg_bytes(tmp_path)
    with pytest.raises(er.RegistryError, match="already registered"):
        register(tmp_path, "goals_poisson@0.6,hockey_elo@0.65", now="2026-10-12T21:36:00Z")
    assert _reg_bytes(tmp_path) == before
    # The same thresholds on swapped members are a different rule.
    register(tmp_path, "goals_poisson@0.65,hockey_elo@0.6", now="2026-10-12T21:36:00Z")
    assert len(er.load_registry(tmp_path / "exact_rules.json")["rules"]) == 2
    # A registry file holding the same mapping twice (hand-made) is a registry error, never two tracked rules.
    raw = json.loads(_reg_bytes(tmp_path))
    dup = json.loads(json.dumps(raw["rules"][0]))
    dup["members"], dup["thresholds"] = dup["members"][::-1], dup["thresholds"][::-1]
    dup["id"] = er.rule_id(dup["sport"], dup["market"], dup["members"], dup["thresholds"])
    dup["rule_sha256"] = er.rule_sha256(dup)
    raw["rules"].append(dup)
    (tmp_path / "exact_rules.json").write_text(json.dumps(er.seal({k: v for k, v in raw.items() if k != "sha256"})))
    with pytest.raises(er.RegistryError, match="same member"):
        er.load_registry(tmp_path / "exact_rules.json")


def test_provenance_matches_the_frozen_row_regardless_of_member_order(tmp_path):
    rule = {"members": ["goals_poisson", "hockey_elo"], "thresholds": [0.6, 0.65], "later_pick_set": 1,
            "earlier": {"record": {"picks": 1}}, "later": {"record": {"picks": 2}}}
    ev = tmp_path / "subsets.json"
    ev.write_text(json.dumps({"sport": "nhl", "market": "winner", "source": "pool", "frozen": [rule], "frozen_lift": []}))
    out = register(tmp_path, "hockey_elo@0.65,goals_poisson@0.6", evidence=ev, note="")
    assert out["rule"]["provenance"]["explorer"]["rank"] == 1


@pytest.mark.parametrize("head", [{"sport": "nba", "market": "winner", "source": "pool"},
                                  {"sport": "nhl", "market": "reg3", "source": "pool"},
                                  {"sport": "nhl", "market": "winner", "source": "adaptive:xyz"},
                                  {}])
def test_evidence_from_another_sport_market_or_source_is_refused(tmp_path, head):
    """The explorer file is the rule's evidence: a matching member/threshold row saved for another sport, market or
    forecast source (or a file that does not say which) is not evidence for this registration."""
    rule = {"members": ["goals_poisson", "hockey_elo"], "thresholds": [0.6, 0.65],
            "earlier": {"record": {"picks": 1}}, "later": {"record": {"picks": 2}}}
    ev = tmp_path / "subsets.json"
    ev.write_text(json.dumps({**head, "frozen": [rule], "frozen_lift": []}))
    with pytest.raises(er.RegistryError, match="evidence file"):
        register(tmp_path, "hockey_elo@0.65,goals_poisson@0.6", evidence=ev, note="")
    assert not (tmp_path / "exact_rules.json").exists()


def test_an_interleaved_writer_never_loses_a_rule(tmp_path, monkeypatch):
    """Writer B registers between writer A's load and write: B is refused while A holds the lock (or both survive)."""
    p = tmp_path / "exact_rules.json"
    register(tmp_path)
    orig, state = er.load_registry, {"raced": False, "b": None}

    def racing_load(path=None):
        reg = orig(path)
        if not state["raced"]:
            state["raced"] = True
            try:
                er.register("nhl", "winner", "hockey_elo@0.7,hockey_gbt@0.55", now="2026-10-12T21:36:00Z", note="B",
                            path=p, wait_seconds=0)
                state["b"] = "registered"
            except er.RegistryError as exc:
                state["b"] = str(exc)
        return reg
    monkeypatch.setattr(er, "load_registry", racing_load)
    er.register("nhl", "winner", "hockey_elo@0.6,hockey_logistic@0.6", now="2026-10-12T21:36:30Z", note="A", path=p)
    reg = orig(p)
    ids = [r["id"] for r in reg["rules"]]
    assert any("hockey_logistic" in i for i in ids)
    if state["b"] == "registered":
        assert any("hockey_gbt" in i for i in ids)
    else:
        assert "lock" in state["b"] and not any("hockey_gbt" in i for i in ids)
    assert [h["version"] for h in reg["history"]] == list(range(1, reg["version"] + 1))
    for a, b in zip(reg["history"], reg["history"][1:]):
        assert b["previous_sha256"]


def test_concurrent_processes_register_every_rule_in_order(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    p = tmp_path / "exact_rules.json"
    specs = {"a": ["hockey_elo@0.65,goals_poisson@0.6", "hockey_elo@0.7,hockey_gbt@0.55", "hockey_elo@0.6,hockey_logistic@0.6"],
             "b": ["goals_poisson@0.7,hockey_gbt@0.6", "hockey_elo@0.75,hockey_logistic@0.55", "goals_poisson@0.55,hockey_logistic@0.65"]}
    code = ("import sys, json; from app import exact_rules as er\n"
            "from pathlib import Path\n"
            "for s in sys.argv[2:]:\n"
            "    er.register('nhl', 'winner', s, note='c', path=Path(sys.argv[1]))\n")
    procs = [subprocess.Popen([sys.executable, "-c", code, str(p), *v], cwd=Path(__file__).resolve().parents[1],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE) for v in specs.values()]
    outs = [(pr.wait(timeout=120), pr.stderr.read().decode()) for pr in procs]
    assert all(rc == 0 for rc, _ in outs), outs
    reg = er.load_registry(p)
    assert len(reg["rules"]) == 6 and reg["version"] == 6
    assert [r["registered_version"] for r in reg["rules"]] == [h["version"] for h in reg["history"]] == list(range(1, 7))
    assert all(h["previous_sha256"] for h in reg["history"][1:])


def test_a_failed_write_leaves_the_earlier_registry_intact(tmp_path, monkeypatch):
    import os
    p = tmp_path / "exact_rules.json"
    register(tmp_path)
    before = p.read_bytes()

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        register(tmp_path, "hockey_elo@0.7,hockey_gbt@0.55", now="2026-10-12T21:36:00Z")
    assert p.read_bytes() == before and sorted(x.name for x in tmp_path.iterdir()) == ["exact_rules.json", "exact_rules.json.lock"]
    monkeypatch.undo()
    # A file changed under a writer that did not take the lock is refused, never overwritten.
    orig = er._write_atomic

    def tamper_then_write(path, reg, expected):
        other = json.loads(path.read_text())
        other["updated_at"] = "2026-10-12T21:37:00Z"
        path.write_text(json.dumps(er.seal({k: v for k, v in other.items() if k != "sha256"})))
        return orig(path, reg, expected)
    monkeypatch.setattr(er, "_write_atomic", tamper_then_write)
    with pytest.raises(er.RegistryError, match="changed"):
        register(tmp_path, "hockey_elo@0.7,hockey_gbt@0.55", now="2026-10-12T21:38:00Z")
    assert len(er.load_registry(p)["rules"]) == 1


def _api(game, monkeypatch, reg, now="2026-10-12T22:00:00Z"):
    from fastapi.testclient import TestClient
    from app import main
    from app.providers.real import RealProvider
    monkeypatch.setattr(at, "load_registry", lambda path=None: reg)
    game.commit()
    store = game.execute("PRAGMA database_list").fetchone()["file"]

    def provider():
        conn = db.connect(store, check_same_thread=False)
        try:
            yield RealProvider(conn, now=now)
        finally:
            conn.close()
    main.app.dependency_overrides[main.get_provider] = provider
    return TestClient(main.app, raise_server_exceptions=False), main


def test_a_broken_or_tampered_registry_file_keeps_the_sealed_groups_and_reports_the_failure(game, tmp_path, monkeypatch, capsys):
    from app import cli
    run(game, NOW)
    reg = small_registry()
    at.record(game, game_ids=[GID], now=REC_AT, reg=reg)
    p = tmp_path / "exact_rules.json"
    register(tmp_path)
    good = p.read_text()
    monkeypatch.setattr(er, "REGISTRY_PATH", p)
    cl, main = _api(game, monkeypatch, reg)
    try:
        for label, text in (("invalid JSON", "{not json"), ("tampered", None)):
            if text is None:
                raw = json.loads(good)
                raw["rules"][0]["thresholds"] = [0.5, 0.5]
                text = json.dumps(raw)
            p.write_text(text)
            r = cl.get("/api/research/tracking", params={"sport": "nhl", "market": "winner"})
            assert r.status_code == 200, (label, r.text)
            body = r.json()
            assert [g["id"] for g in body["groups"]] == [g["id"] for g in reg["markets"]["nhl:winner"]["groups"]]
            assert body["exact_rules"]["status"] == "failed" and body["exact_rules"]["rules"] == [] and body["exact_rules"]["error"]
            g = cl.get(f"/api/research/tracking/game/{GID}", params={"sport": "nhl"})
            assert g.status_code == 200 and g.json()["status"] == "recorded" and g.json()["exact_rules_error"], label
            # CLI: a clean refusal, nonzero exit, no traceback.
            assert cli.main(["exact-rules-list"]) == 1
            out = json.loads(capsys.readouterr().out)
            assert out["status"] == "failed" and "exact_rules.json" in out["reason"]
            assert cli.main(["--sport", "nhl", "exact-rules-register", "--market", "winner", "--rule",
                             "hockey_elo@0.7,hockey_gbt@0.55", "--note", "x"]) == 1
            assert json.loads(capsys.readouterr().out)["status"] == "refused" and p.read_text() == text
    finally:
        main.app.dependency_overrides.pop(main.get_provider, None)


def test_missing_migration_0028_is_reported_as_schema_required_and_the_api_never_migrates(game, tmp_path, monkeypatch):
    run(game, NOW)
    reg = small_registry()
    old = (db.MIGRATIONS_DIR / "0022_agreement_observation_inputs.sql").read_text()
    with db.transaction(game):
        for stmt in db._split(old):
            game.execute(stmt)
        game.execute("DELETE FROM schema_migrations WHERE version=28")
    at.record(game, game_ids=[GID], now=REC_AT, reg=reg)
    p = tmp_path / "exact_rules.json"
    register(tmp_path)
    ex = er.load_registry(p)
    rep = at.exact_report(game, "nhl", "winner", reg=reg, exact_registry=ex)
    assert rep["status"] == "schema_required" and "0028" in rep["reason"] and rep["rules"] == []
    assert rep["registered_rules"] == [ex["rules"][0]["id"]]
    assert at.exact_report(game, "nhl", "reg3", reg=reg, exact_registry=ex)["status"] == "no_registered_rules"
    monkeypatch.setattr(er, "REGISTRY_PATH", p)
    cl, main = _api(game, monkeypatch, reg)
    try:
        r = cl.get("/api/research/tracking", params={"sport": "nhl", "market": "winner"}).json()
        assert r["exact_rules"]["status"] == "schema_required" and r["groups"]
        assert db.has_table(game, "schema_migrations") and not game.execute("SELECT 1 FROM schema_migrations WHERE version = 28").fetchone()
    finally:
        main.app.dependency_overrides.pop(main.get_provider, None)


def test_exact_tolerance_equals_the_explorer_at_the_boundary():
    from app import combination_thresholds as ct
    assert at.EXACT_EPS == ct.EPS
    for p in (0.65 - 5e-10, 0.65 - 5e-13, 0.65, 0.65 + 5e-13):
        rows = [{"game_id": f"g{i}", "season": 2021 if i < 3 else 2023, "cutoff_at": "2021-01-01T12:00:00Z",
                 "tipoff_at": "2021-01-01T23:00:00Z", "line": None, "realised": 0,
                 "vectors": {"A": [p, 0.0, 1 - p], "B": [0.7, 0.0, 0.3]}} for i in range(4)]
        t = ct.Table("winner", rows, ["A", "B"], later_seasons=[2023])
        _, _, qual = ct._qualifying(t, ["A", "B"], [0.65, 0.6])
        res = at.classify(x("A", "B", thresholds=[0.65, 0.6]), {"A": (p, 0.0, 1 - p), "B": (0.7, 0.0, 0.3)}, "winner")
        assert bool(qual[0]) == (res["status"] == "pick"), p


def test_exact_members_carry_fit_provenance_or_its_absence(game, tmp_path):
    run(game, NOW)
    reg = small_registry()
    c = core_winner(game)
    candidate(game, c["cohort_id"], at_="2026-10-12T21:36:00Z", name="logit_l2:nhl_base", p=(0.7, 0.0, 0.3))
    ex = er.load_registry(register(tmp_path, "hockey_elo@0.6,logit_l2:nhl_base@0.6")["path"] and tmp_path / "exact_rules.json")
    at.record(game, game_ids=[GID], now=REC_AT, reg=reg, exact_registry=ex)
    (row,) = exact_rows(game)
    mem = {m["name"]: m for m in json.loads(row["members"])}
    cand = mem["logit_l2:nhl_base"]["fit"]
    assert cand["artifact_sha256"] == "sha" and cand["training_cutoff"] == "2026-06-30T00:00:00Z" and "fit_id" in cand
    assert mem["hockey_elo"]["fit"] is None and "no fit reference" in mem["hockey_elo"]["fit_note"]
    assert mem["hockey_elo"]["version"] and mem["logit_l2:nhl_base"]["version"] == "research-cand:x:v1"
    rep = at.exact_report(game, "nhl", "winner", reg=reg, exact_registry=ex)
    assert "definition" in rep["versions_note"].lower() and "fit" in rep["versions_note"].lower()


def test_a_grid_threshold_typed_as_printed_is_the_grid_value(tmp_path):
    """The reg3 grid holds 1/3, which rule ids print as 0.333333 and errors as 0.3333: either spelling names that grid
    value (one rule), while a value off the grid is still refused."""
    out = register(tmp_path, "goals_poisson@0.333333,goals_draw@0.5", market="reg3")
    assert out["rule"]["thresholds"][0] == pytest.approx(1 / 3, abs=1e-12)
    with pytest.raises(er.RegistryError, match="already registered"):
        register(tmp_path, "goals_poisson@0.3333,goals_draw@0.5", market="reg3")
    with pytest.raises(er.RegistryError, match="not on the explorer grid"):
        register(tmp_path, "goals_poisson@0.34,goals_draw@0.5", market="reg3")


@pytest.mark.parametrize("eff", ["2026-10-1T00:00:00Z", "2026-10-12T9:00:00Z", "2026-10-12 23:00:00Z", "2026-10-12T23:00:00"])
def test_a_non_canonical_effective_from_is_refused_never_compared_as_a_string(tmp_path, eff):
    """Reviewer reproduction (2026-10-13): '2026-10-1T00:00:00Z' (eleven days before registration) passed the no-backdating
    check because strptime accepts unpadded fields while the check compared strings; '…T9:00:00Z' then dropped a valid
    game as before_registration."""
    with pytest.raises(er.RegistryError, match="exactly like"):
        er.register("nhl", "winner", "hockey_elo@0.5,goals_poisson@0.5", now="2026-10-12T21:35:00Z", effective_from=eff,
                    path=tmp_path / "exact_rules.json", note="t")
    assert not (tmp_path / "exact_rules.json").exists()


def test_a_registry_file_with_a_non_canonical_time_does_not_verify(tmp_path):
    p = tmp_path / "exact_rules.json"
    er.register("nhl", "winner", "hockey_elo@0.5,goals_poisson@0.5", now="2026-10-12T21:35:00Z", path=p, note="t")
    raw = json.loads(p.read_text())
    raw["rules"][0]["effective_from"] = "2026-10-1T00:00:00Z"
    raw["rules"][0]["rule_sha256"] = er.rule_sha256(raw["rules"][0])
    raw["sha256"] = er.seal(raw)["sha256"]
    p.write_text(json.dumps(raw))
    with pytest.raises(er.RegistryError, match="non-canonical or backdated"):
        er.load_registry(p)
