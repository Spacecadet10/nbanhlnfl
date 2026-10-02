"""Candidate-pool selection (docs/CANDIDATE_POOL.md §7): registry routes, exact-cohort joins, weekly pool decisions and
live pool selections. Synthetic stores: every row is inserted through the real tables and their database guards (two-hour
window, current start, scheduled game), then the games are settled. No network, no thresholds lowered."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app import db, research_live, selector
from app.candidates import live as pool_live, registry, selection

DEFN = "core-test-defn"
FMT = "%Y-%m-%dT%H:%M:%SZ"
START = datetime(2026, 10, 6, 0, 0, tzinfo=timezone.utc)      # Tuesday; settled games run over five weeks
CAND = "poisson_draw:nhl_base"
CAND_B = "poisson_draw:nhl_goalie"


def ts(dt):
    return dt.strftime(FMT)


def pool_registry(effective="2026-10-01T00:00:00Z", combination_only=False):
    """spread: CAND is a standalone source and in an average; with `combination_only`, CAND_B qualifies only inside
    avg:goals_glm+cand:CAND_B (never alone)."""
    ev = {"file": "test", "key": "test", "mean": -0.01, "ci95": [-0.02, -0.005], "games": 1000, "weeks": 100}
    spread = [{"source": f"cand:{CAND}", "kind": "single", "members": [f"cand:{CAND}"], "candidates": [CAND],
               "route": "standalone", "evidence": ev}]
    if combination_only:
        spread = [{"source": f"avg:goals_glm+cand:{CAND_B}", "kind": "average", "members": ["goals_glm", f"cand:{CAND_B}"],
                   "candidates": [CAND_B], "route": "combination", "evidence": ev, "qualified_by": [CAND_B]}]
    return selection.seal({"registry": "pool-test", "effective_from": effective, "selector": "selector-v1",
                           "routes": {}, "rule": "test",
                           "definitions": {CAND: registry.definition(registry.BY_NAME[CAND]),
                                           CAND_B: registry.definition(registry.BY_NAME[CAND_B])},
                           "sports": {"nhl": {"spread": spread}}})


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    c = db.connect(tmp_path / "nhl.sqlite")
    db.migrate(c)
    db.claim_store(c, "nhl")
    c.execute("INSERT INTO raw_responses (raw_id, source, endpoint, request_key, content_hash, byte_size, path, "
              "importer_version, first_retrieved_at) VALUES (1,'test','test','k','h',0,'p','t','2026-10-01T00:00:00Z')")
    for t in ("1", "2"):
        c.execute("INSERT INTO teams VALUES (?,?,?,?)", (f"nhl:{t}", f"T{t}", f"Team {t}", "2026-10-01T00:00:00Z"))
    c.commit()
    return c


def add_game(c, gid, tipoff):
    c.execute("INSERT INTO games (game_id, season, season_type, tipoff_at, home_team_id, away_team_id, neutral_site, status, "
              "first_seen_at, last_raw_id, updated_at) VALUES (?,2027,'regular',?,'nhl:1','nhl:2',0,'scheduled',?,1,?)",
              (gid, tipoff, "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z"))


def add_cohort(c, gid, tipoff, generated, line=-1.5, market="spread", defn=DEFN, members_p=(0.5, 0.0, 0.5)):
    members = [{"name": n, "ref": None, "p": list(members_p)} for n in research_live.LINE_MEMBERS["nhl"]]
    agree = {"side": None, "majority": False, "unanimous": False}
    cur = c.execute(
        "INSERT INTO research_cohorts (idempotency_key, sport, game_id, market, line, line_source, market_quote_id, bookmaker, "
        "definition, primary_name, information_at, generated_at, tipoff_at, members, missing, average, stack, agreement) "
        "VALUES (?,?,?,?,?,?,NULL,'null',?,?,?,?,?,?,'{}',?,?,?)",
        (f"{gid}:{market}:{generated}:{defn}", "nhl", gid, market, line, None if line is None else "model", defn,
         "goals_poisson", generated,
         generated, tipoff, json.dumps(members), json.dumps(list(members_p)), json.dumps({"p": None, "reason": "test"}),
         json.dumps(agree)))
    return cur.lastrowid


def add_candidate(c, name, gid, tipoff, generated, p, line=-1.5, ref=None, market="spread", defn=None):
    spec = registry.BY_NAME[name]
    mv = f"{pool_live.PREFIX}:{name}:{defn or registry.definition(spec)}"
    cur = c.execute(
        "INSERT INTO candidate_forecasts (game_id, candidate, model_version, market, line, line_source, line_ref, p_a, p_mid, "
        "p_b, params, inputs, fit_id, artifact_sha256, training_cutoff, dataset_version, tipoff_at, generated_at, "
        "idempotency_key) VALUES (?,?,?,?,?,?,?,?,?,?,'{}','{}',NULL,'sha','2026-09-30T00:00:00Z','ds',?,?,?)",
        (gid, name, mv, market, line, "core_cohort:model", ref, p[0], p[1], p[2], tipoff, generated,
         f"{gid}:{name}:{market}:{generated}:{ref}"))
    return cur.lastrowid


def settle(c, gid, tipoff, home, away):
    avail = ts(datetime.strptime(tipoff, FMT).replace(tzinfo=timezone.utc) + timedelta(hours=3))
    c.execute("UPDATE games SET status='final', home_score=?, away_score=?, result_available_at=?, "
              "result_availability_basis='observed_retrieval' WHERE game_id=?", (home, away, avail, gid))


def history(c, n, name=CAND, accuracy_every=7):
    """`n` settled spread games (line -1.5), one every 5 hours. The candidate picks the realised side except every
    `accuracy_every`-th game; the core members are uninformative (no pick). Outcomes alternate, so the reference is 50%."""
    for i in range(n):
        tip = START + timedelta(hours=5 * i)
        gid, tipoff, gen = f"nhl:s{i}", ts(tip), ts(tip - timedelta(hours=1))
        add_game(c, gid, tipoff)
        cid = add_cohort(c, gid, tipoff, gen)
        home_covers = i % 2 == 0
        right = i % accuracy_every != 0
        favours_home = home_covers == right
        add_candidate(c, name, gid, tipoff, ts(tip - timedelta(minutes=59)),
                      (0.8, 0.0, 0.2) if favours_home else (0.2, 0.0, 0.8), ref=f"research_cohorts:{cid}")
        settle(c, gid, tipoff, 4 if home_covers else 2, 1 if home_covers else 3)
    c.commit()
    return START + timedelta(hours=5 * n)


def monday_after(end):
    d = end + timedelta(days=(7 - end.weekday()) % 7 or 7)
    return ts(d.replace(hour=14, minute=30, second=0))


# --- Registry routes ------------------------------------------------------------------------------------------------
def test_routes_from_held_out_evidence_and_combination_only_never_alone():
    """reg3: A qualifies alone (route a); B only inside avg:goals_draw+B (route b); C has neither (research-only)."""
    a, b, c = "poisson_draw:nhl_base", "multinomial_l2:nhl_base", "multinomial_l2:nhl_goalie"
    ci = lambda lo, hi: {"mean": (lo + hi) / 2, "ci95": [lo, hi], "games": 500, "weeks": 50}
    am = {"strongest_existing": "goals_draw", "seasons": [2025], "existing": {"goals_draw": {"log_loss": 0.99}},
          "candidates": {a: {"minus_strongest": ci(-0.01, -0.002), "log_loss": 0.98},
                         b: {"minus_strongest": ci(-0.004, 0.003), "log_loss": 0.99},
                         c: {"minus_strongest": ci(-0.002, 0.004), "log_loss": 1.0}},
          "combinations": [
              {"combination": f"avg:goals_draw+{b} vs goals_draw (ablation: remove {b})",
               "with_minus_without": ci(-0.006, -0.001), "log_loss_with": 0.985},
              {"combination": f"avg:goals_draw+{c} vs goals_draw (ablation: remove {c})",
               "with_minus_without": ci(-0.003, 0.001), "log_loss_with": 0.988},
              {"combination": f"avg:goals_draw+{a} vs goals_draw (ablation: remove {a})",       # helps, but the average
               "with_minus_without": ci(-0.004, -0.001), "log_loss_with": 0.995}]}              # is worse than goals_draw
    reg = selection.build_registry("2026-10-02T00:00:00Z", assess={"nhl": {"markets": {"reg3": am}}, "nba": {"markets": {}}})
    srcs = {e["source"]: e for e in reg["sports"]["nhl"]["reg3"]}
    assert set(srcs) == {f"cand:{a}", f"avg:goals_draw+cand:{b}"}
    assert srcs[f"cand:{a}"]["route"] == "standalone" and srcs[f"avg:goals_draw+cand:{b}"]["route"] == "combination"
    assert srcs[f"avg:goals_draw+cand:{b}"]["evidence"]["ci95"] == [-0.006, -0.001]
    assert set(reg["definitions"]) == {a, b}                                            # C is research-only
    assert reg["sha256"] == selection.seal({k: v for k, v in reg.items() if k != "sha256"})["sha256"]


def test_committed_registry_is_sealed_and_hetero_linear_is_a_combination_source_only():
    reg = selection.load_registry()
    nba = reg["sports"]["nba"]["team_total_home"]
    assert [e["source"] for e in nba] == ["avg:points_normal+cand:hetero_linear:nba_base"]
    assert all(e["source"] != "cand:hetero_linear:nba_base" for m in reg["sports"]["nba"].values() for e in m)
    for sport, ms in reg["sports"].items():
        for market, es in ms.items():
            for e in es:
                assert e["route"] in ("standalone", "combination") and e["evidence"]["ci95"][1] < 0
                assert all(n in reg["definitions"] for n in e["candidates"])
    assert reg["definitions"]["poisson_draw:nhl_base"] == registry.definition(registry.BY_NAME["poisson_draw:nhl_base"])


# --- Joins ------------------------------------------------------------------------------------------------------------
def test_vectors_join_only_the_exact_cohort_line_and_identity(store):
    reg = pool_registry()
    tip = START + timedelta(days=1)
    tipoff, gen = ts(tip), ts(tip - timedelta(hours=1))
    add_game(store, "nhl:j", tipoff)
    cid = add_cohort(store, "nhl:j", tipoff, gen)
    other = add_cohort(store, "nhl:j", tipoff, ts(tip - timedelta(minutes=50)), line=-2.5)
    row = lambda i: store.execute("SELECT * FROM research_cohorts WHERE cohort_id=?", (i,)).fetchone()
    add_candidate(store, CAND, "nhl:j", tipoff, ts(tip - timedelta(minutes=58)), (0.7, 0.0, 0.3), ref=f"research_cohorts:{other}")
    add_candidate(store, CAND, "nhl:j", tipoff, ts(tip - timedelta(minutes=57)), (0.7, 0.0, 0.3), line=-2.5,
                  ref=f"research_cohorts:{cid}")                                     # right ref, wrong line
    add_candidate(store, CAND, "nhl:j", tipoff, ts(tip - timedelta(minutes=56)), (0.7, 0.0, 0.3), ref=f"research_cohorts:{cid}",
                  defn="stale-identity")                                             # right line, other identity
    as_of = ts(tip - timedelta(minutes=30))
    assert selection.pool_vectors(store, reg, "nhl", row(cid), as_of) == ({}, {})   # no fallback to any of them
    fid = add_candidate(store, CAND, "nhl:j", tipoff, ts(tip - timedelta(minutes=55)), (0.66, 0.0, 0.34),
                        ref=f"research_cohorts:{cid}")
    vec, used = selection.pool_vectors(store, reg, "nhl", row(cid), as_of)
    assert vec == {f"cand:{CAND}": (0.66, 0.0, 0.34)} and used == {f"cand:{CAND}": [fid]}
    early = ts(tip - timedelta(minutes=56))                                          # known only after its generation
    assert selection.pool_vectors(store, reg, "nhl", row(cid), early) == ({}, {})
    for when in (tipoff, ts(tip + timedelta(minutes=1))):                            # at or after the start: rejected
        with pytest.raises(sqlite3.IntegrityError, match="two hours"):
            add_candidate(store, CAND, "nhl:j", tipoff, when, (0.6, 0.0, 0.4), ref=f"research_cohorts:{cid}")


# --- Decisions --------------------------------------------------------------------------------------------------------
def test_selector_chooses_an_eligible_individual_candidate(store):
    end = history(store, 150)
    now = monday_after(end)
    v1_before = store.execute("SELECT count(*) FROM selector_decisions").fetchone()[0]
    out = selection.decide_due(store, now=now, defn=DEFN, reg=pool_registry())
    (w,) = out["written"]
    assert w["status"] == "active" and w["rule"]["source"] == f"cand:{CAND}", w
    d = store.execute("SELECT * FROM pool_decisions").fetchone()
    ev = json.loads(d["evidence"])
    st = ev["source_stats"][f"cand:{CAND}"]
    assert st["picks"] >= selector.MIN_PICKS and st["coverage"] >= selector.MIN_COVERAGE and st["weeks"] >= selector.MIN_WEEKS
    assert d["lower_bound"] > 0 and ev["sources"][-1] == f"cand:{CAND}" and ev["settled_items"] == 150
    assert ev["rules"] == len(selector.rules("spread", ev["sources"]))                 # Bonferroni over every rule
    assert store.execute("SELECT count(*) FROM selector_decisions").fetchone()[0] == v1_before   # core untouched
    again = selection.decide_due(store, now=now, defn=DEFN, reg=pool_registry())       # frozen for its week
    assert again["written"] == [] and again["existing"] == ["spread"]


def test_selector_chooses_an_eligible_combination_whose_candidate_is_not_selectable_alone(store):
    end = history(store, 150, name=CAND_B)
    reg = pool_registry(combination_only=True)
    out = selection.decide_due(store, now=monday_after(end), defn=DEFN, reg=reg)
    (w,) = out["written"]
    assert w["status"] == "active" and w["rule"]["source"] == f"avg:goals_glm+cand:{CAND_B}", w
    ev = json.loads(store.execute("SELECT evidence FROM pool_decisions").fetchone()["evidence"])
    assert f"cand:{CAND_B}" not in ev["sources"] and ev["pool_sources"] == [f"avg:goals_glm+cand:{CAND_B}"]
    # Even a forged rule naming the candidate alone cannot pick: its vector is never offered as a source.
    row = store.execute("SELECT * FROM research_cohorts LIMIT 1").fetchone()
    it, _ = selection._item(store, reg, "nhl", row, "2099-01-01T00:00:00Z")
    forged = {"status": "active", "rule": {"source": f"cand:{CAND_B}", "filter": "none", "threshold": 0.5},
              "lower": 0.1, "reason": "forged"}
    assert selector.apply(forged, it, "spread")["reason"] == f"source cand:{CAND_B} unavailable"


def test_insufficient_or_unconvincing_evidence_never_selects(store):
    end = history(store, 60)                                                         # fewer than 100 picks
    out = selection.decide_due(store, now=monday_after(end), defn=DEFN, reg=pool_registry())
    assert out["written"][0]["status"] == "insufficient_evidence"


def test_a_candidate_no_better_than_the_reference_abstains(store):
    end = history(store, 150, accuracy_every=2)                                       # right half of the time
    out = selection.decide_due(store, now=monday_after(end), defn=DEFN, reg=pool_registry())
    assert out["written"][0]["status"] == "abstain"


def test_cohorts_made_before_the_registry_took_effect_are_not_evidence(store):
    """The registry is frozen before the evidence it is judged on: the same 150 games that select the candidate when
    the registry predates them leave only the 70 later cohorts (< 100 picks) when it took effect at game 80."""
    end = history(store, 150)
    reg = pool_registry(effective=ts(START + timedelta(hours=5 * 80 - 2)))
    cutoff = monday_after(end)
    assert len(selection.evidence(store, reg, "nhl", "spread", DEFN, cutoff)) == 70
    assert len(selection.evidence(store, pool_registry(), "nhl", "spread", DEFN, cutoff)) == 150
    out = selection.decide_due(store, now=cutoff, defn=DEFN, reg=reg)
    assert out["written"][0]["status"] == "insufficient_evidence"


def test_not_before_the_registry_takes_effect(store):
    end = history(store, 150)
    out = selection.decide_due(store, now=monday_after(end), defn=DEFN, reg=pool_registry(effective="2099-01-01T00:00:00Z"))
    assert out["status"] == "not_yet_effective" and store.execute("SELECT count(*) FROM pool_decisions").fetchone()[0] == 0


# --- Live selections --------------------------------------------------------------------------------------------------
def upcoming(c, cand_p=(0.8, 0.0, 0.2), with_candidate=True, decided=None):
    """An upcoming game one day after the pool decision (default: the 150-game history's decision)."""
    tip = (datetime.strptime(decided, FMT).replace(tzinfo=timezone.utc) if decided else datetime(2026, 11, 9, 14, 30,
                                                                                                 tzinfo=timezone.utc)) + timedelta(days=1)
    tipoff, gen = ts(tip), ts(tip - timedelta(hours=1))
    add_game(c, "nhl:u", tipoff)
    cid = add_cohort(c, "nhl:u", tipoff, gen)
    fid = add_candidate(c, CAND, "nhl:u", tipoff, ts(tip - timedelta(minutes=58)), cand_p,
                        ref=f"research_cohorts:{cid}") if with_candidate else None
    c.commit()
    return ts(tip - timedelta(minutes=55)), cid, fid


def test_live_pool_selection_picks_the_candidate_and_is_idempotent(store):
    end = history(store, 150)
    selection.decide_due(store, now=monday_after(end), defn=DEFN, reg=pool_registry())
    now, cid, fid = upcoming(store)
    core_sel = store.execute("SELECT count(*) FROM research_selections").fetchone()[0]
    out = selection.select(store, game_ids=["nhl:u"], now=now, defn=DEFN, reg=pool_registry())
    (s,) = out["selections"]
    assert s["status"] == "pick" and s["side"] == "a" and s["source"] == f"cand:{CAND}"
    row = store.execute("SELECT * FROM pool_selections").fetchone()
    assert row["cohort_id"] == cid and json.loads(row["inputs"]) == {f"cand:{CAND}": [fid]} and row["recommended"] == 1
    assert row["generated_at"] < row["tipoff_at"]
    selection.select(store, game_ids=["nhl:u"], now=now, defn=DEFN, reg=pool_registry())    # retry: no duplicate
    assert store.execute("SELECT count(*) FROM pool_selections").fetchone()[0] == 1
    assert store.execute("SELECT count(*) FROM research_selections").fetchone()[0] == core_sel   # core untouched
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.execute("UPDATE pool_selections SET status='no_pick'")


def test_missing_candidate_is_source_unavailable_never_a_substitute(store):
    end = history(store, 150)
    selection.decide_due(store, now=monday_after(end), defn=DEFN, reg=pool_registry())
    now, _, _ = upcoming(store, with_candidate=False)
    (s,) = selection.select(store, game_ids=["nhl:u"], now=now, defn=DEFN, reg=pool_registry())["selections"]
    assert s["status"] == "no_pick" and s["reason"] == f"source cand:{CAND} unavailable"


def test_research_collection_continues_while_evidence_is_insufficient(store):
    """Insufficient evidence gives a no-pick with its reason, and the cohort and candidate rows are still recorded."""
    end = history(store, 40)
    selection.decide_due(store, now=monday_after(end), defn=DEFN, reg=pool_registry())
    now, cid, fid = upcoming(store, decided=monday_after(end))
    (s,) = selection.select(store, game_ids=["nhl:u"], now=now, defn=DEFN, reg=pool_registry())["selections"]
    assert s["status"] == "no_pick" and "insufficient_evidence" in s["reason"]
    assert store.execute("SELECT count(*) FROM candidate_forecasts WHERE forecast_id=?", (fid,)).fetchone()[0] == 1
    row = store.execute("SELECT * FROM pool_selections").fetchone()
    assert row["status"] == "no_pick" and row["cohort_id"] == cid


def test_report_scores_pool_sources_prospectively(store):
    end = history(store, 150)
    selection.decide_due(store, now=monday_after(end), defn=DEFN, reg=pool_registry())
    rep = selection.report(store, "nhl", as_of=monday_after(end), reg=pool_registry())
    src = rep["markets"]["spread"]["sources"][0]
    assert src["source"] == f"cand:{CAND}" and src["settled_with_vector"] == 150
    best = src["best_rule"]
    assert best["wins"] + best["losses"] == 150 and best["wins"] > best["losses"] and best["eligible"]
    assert rep["markets"]["spread"]["decisions"][0]["status"] == "active"


def test_roster_eligibility_is_the_registry_route_and_the_decision_in_force(store, monkeypatch):
    """Audit P2: eligibility comes from the sealed route and the pool decision — never from a prospective count alone."""
    monkeypatch.setattr(selection, "core_definition", lambda _c: DEFN)
    end = history(store, 150)
    as_of = monday_after(end)
    monkeypatch.setattr(selection, "load_registry", lambda path=None: pool_registry())
    before = {c["name"]: c for c in pool_live.roster(store, "nhl", as_of=as_of)["candidates"]}
    assert before[CAND]["prospective"]["settled"] == 150                                  # 150 settled games ...
    assert before[CAND]["selection"].startswith("selectable alone in spread")             # ... but no decision yet
    assert "research only" in before[CAND_B]["selection"]
    selection.decide_due(store, now=as_of, defn=DEFN, reg=pool_registry())
    after = {c["name"]: c for c in pool_live.roster(store, "nhl", as_of=as_of)["candidates"]}
    assert after[CAND]["selection"] == f"selected now (spread: cand:{CAND})"
    st = after[CAND]["pool"]["spread"]
    assert st["selected_now"] and st["sources"][0]["prospective"]["picks"] == 150 and st["decision"]["status"] == "active"
    monkeypatch.setattr(selection, "load_registry", lambda path=None: pool_registry(combination_only=True))
    combo = {c["name"]: c for c in pool_live.roster(store, "nhl", as_of=as_of)["candidates"]}
    assert combo[CAND_B]["selection"] == f"selectable only inside avg:goals_glm+cand:{CAND_B} (incremental value in a combination); never alone"
    assert "research only" in combo[CAND]["selection"]       # not in this registry: 150 games do not make it eligible


# --- Review fixes (2026-10-02) ----------------------------------------------------------------------------------------
def reg3_registry():
    ev = {"file": "test", "key": "test", "mean": -0.01, "ci95": [-0.02, -0.005], "games": 1000, "weeks": 100}
    return selection.seal({"registry": "pool-test", "effective_from": "2026-10-01T00:00:00Z", "selector": "selector-v1",
                           "routes": {}, "rule": "test", "definitions": {CAND: registry.definition(registry.BY_NAME[CAND])},
                           "sports": {"nhl": {"reg3": [{"source": f"cand:{CAND}", "kind": "single", "members": [f"cand:{CAND}"],
                                                         "candidates": [CAND], "route": "standalone", "evidence": ev}]}}})


def test_winner_and_reg3_vectors_follow_their_own_cohort(store):
    """Review finding 1: a winner/reg3 candidate forecast names its core cohort; a later cohort is never joined with the
    earlier (de-duplicated) forecast, and a forecast without a cohort reference is never joined."""
    tip = START + timedelta(days=2)
    tipoff = ts(tip)
    add_game(store, "nhl:r", tipoff)
    c1 = add_cohort(store, "nhl:r", tipoff, ts(tip - timedelta(minutes=110)), line=None, market="reg3")
    add_candidate(store, CAND, "nhl:r", tipoff, ts(tip - timedelta(minutes=109)), (0.5, 0.2, 0.3), line=None, market="reg3")
    f1 = add_candidate(store, CAND, "nhl:r", tipoff, ts(tip - timedelta(minutes=108)), (0.5, 0.2, 0.3), line=None,
                       market="reg3", ref=f"research_cohorts:{c1}")
    c2 = add_cohort(store, "nhl:r", tipoff, ts(tip - timedelta(minutes=50)), line=None, market="reg3")
    assert pool_live.core_lines(store, ["nhl:r"], ts(tip - timedelta(minutes=49)))[("nhl:r", "reg3")] == \
        {"line": None, "source": "none", "ref": f"research_cohorts:{c2}"}
    row = lambda i: store.execute("SELECT * FROM research_cohorts WHERE cohort_id=?", (i,)).fetchone()
    as_of = ts(tip - timedelta(minutes=10))
    assert selection.pool_vectors(store, reg3_registry(), "nhl", row(c1), as_of)[1] == {f"cand:{CAND}": [f1]}
    assert selection.pool_vectors(store, reg3_registry(), "nhl", row(c2), as_of) == ({}, {})   # not the c1 forecast
    f2 = add_candidate(store, CAND, "nhl:r", tipoff, ts(tip - timedelta(minutes=48)), (0.4, 0.3, 0.3), line=None,
                       market="reg3", ref=f"research_cohorts:{c2}")
    assert selection.pool_vectors(store, reg3_registry(), "nhl", row(c2), as_of)[1] == {f"cand:{CAND}": [f2]}


def test_a_rescheduled_game_is_skipped_and_never_blocks_the_others(store):
    """Review finding 2: the old cohort of a rescheduled game is void and skipped; the other game is still selected."""
    end = history(store, 150)
    selection.decide_due(store, now=monday_after(end), defn=DEFN, reg=pool_registry())
    now, cid, _ = upcoming(store)
    tip = datetime(2026, 11, 10, 14, 30, tzinfo=timezone.utc)
    add_game(store, "nhl:moved", ts(tip))
    moved = add_cohort(store, "nhl:moved", ts(tip), ts(tip - timedelta(hours=1)))
    add_candidate(store, CAND, "nhl:moved", ts(tip), ts(tip - timedelta(minutes=58)), (0.8, 0.0, 0.2),
                  ref=f"research_cohorts:{moved}")
    store.execute("UPDATE games SET tipoff_at=? WHERE game_id='nhl:moved'", (ts(tip + timedelta(minutes=30)),))
    out = selection.select(store, game_ids=["nhl:moved", "nhl:u"], now=now, defn=DEFN, reg=pool_registry())
    assert out["status"] == "success" and [s["game_id"] for s in out["selections"]] == ["nhl:u"]
    assert store.execute("SELECT game_id FROM pool_selections").fetchall()[0][0] == "nhl:u"


def test_report_counts_each_game_once_across_definitions(store):
    """Review finding 5: a game with cohorts under two core definitions is counted once (the latest cohort)."""
    end = history(store, 150)
    reg = pool_registry()
    selection.decide_due(store, now=monday_after(end), defn=DEFN, reg=reg)
    for r in store.execute("SELECT * FROM research_cohorts WHERE definition=?", (DEFN,)).fetchall()[:10]:
        store.execute("UPDATE games SET status='scheduled', home_score=NULL, away_score=NULL, result_available_at=NULL "
                      "WHERE game_id=?", (r["game_id"],))
        cid = add_cohort(store, r["game_id"], r["tipoff_at"], ts(datetime.strptime(r["generated_at"], FMT).replace(
            tzinfo=timezone.utc) + timedelta(minutes=5)), defn="core-test-defn-2")
        add_candidate(store, CAND, r["game_id"], r["tipoff_at"], ts(datetime.strptime(r["generated_at"], FMT).replace(
            tzinfo=timezone.utc) + timedelta(minutes=6)), (0.8, 0.0, 0.2), ref=f"research_cohorts:{cid}")
        settle(store, r["game_id"], r["tipoff_at"], 4, 1)
    selection.decide_due(store, now=monday_after(end), defn="core-test-defn-2", reg=reg)
    rep = selection.report(store, "nhl", as_of=monday_after(end), reg=reg)
    assert rep["markets"]["spread"]["settled_cohorts"] == 150
