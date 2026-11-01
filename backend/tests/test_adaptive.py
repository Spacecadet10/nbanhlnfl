"""adaptive-v1 (docs/ADAPTIVE_SYSTEM.md): scheduled within-season refits, learned combination, ledger, failure handling.
Synthetic NHL store (three teams, 36 games a season, 2016–2027), no network, no deep libraries required."""
import json
import sqlite3
from dataclasses import replace

import pytest

from app import adaptive, combine, ingest, jobs, research_live
from app.candidates import engine, features, registry
from app.models import score_candidates
from test_live_markets import add_upcoming, quote, sealed  # noqa: F401  (fixture)
from test_nhl_eval import nhl_store  # noqa: F401  (fixture)
from test_research_live import GID, run

TRAIN_AT = "2026-10-12T20:40:00Z"     # before the two-hour window of the 23:00 UTC start
CYCLE_AT = "2026-10-12T21:30:00Z"     # inside it
SPECS = ("logit_l2:nhl_base", "multinomial_l2:nhl_base", "poisson_draw:nhl_base", "torch_mlp:nhl_base")


class _test_clock:
    """Advances one second per reading from `start` (reset with `at`); never reaches `before`."""
    def __init__(self, start="2026-10-12T20:00:00Z", before="2026-10-12T20:50:00Z"):
        self.at(start, before)

    def at(self, start, before):
        from datetime import datetime
        self.t, self.before = datetime.strptime(start, adaptive.FMT), datetime.strptime(before, adaptive.FMT)

    def __call__(self):
        from datetime import timedelta
        self.t += timedelta(seconds=1)
        assert self.t < self.before, "test clock ran past the forecast times"
        return self.t.strftime(adaptive.FMT)


@pytest.fixture
def store(sealed, monkeypatch):
    monkeypatch.setattr(score_candidates, "MIN_HOCKEY_EXAMPLES", 100)
    monkeypatch.setattr(research_live, "STACK_MIN_TRAIN", 30)
    monkeypatch.setattr(registry, "MIN_TRAIN", {"nba": 200, "nhl": 100})
    monkeypatch.setattr(registry, "SPECS", tuple(registry.BY_NAME[s] for s in SPECS))
    monkeypatch.setattr(adaptive, "MIN_TRAIN", 30)
    # Fit rows and artifacts are stamped with the clock. A clock that starts before TRAIN_AT and advances one second per
    # reading keeps the tests independent of the real date (publication must still precede the forecast times).
    monkeypatch.setattr(adaptive, "utc_now", _test_clock())
    # The boosted members refit slowly (hundreds of weekly fits in the walk): kept out of the test members only.
    monkeypatch.setattr(adaptive, "LEARNED", {**adaptive.LEARNED, "nhl": ("hockey_logistic", "hockey_logistic_xg", "goals_draw")})
    add_upcoming(sealed, start="2026-10-12T19:00:00", at="2026-10-12T20:30:00Z")
    sealed.execute("INSERT INTO job_runs (job, started_at, finished_at, status) VALUES "
                   "('refresh', '2026-10-12T21:00:00Z', '2026-10-12T21:00:00Z', 'success')")
    quote(sealed, "2026-10-12T21:05:00Z", date="2026-10-12T23:00Z")
    return sealed


def _settle(conn, gid=2026020001, hs=4, vs=2, at="2026-10-13T03:00:00Z"):
    add_upcoming(conn, gid=gid, start="2026-10-12T19:00:00", state=7, hs=hs, vs=vs, period=3, at=at)


def _members(conn):
    return {r["name"]: r for r in conn.execute("SELECT * FROM adaptive_fits WHERE kind='member' ORDER BY fit_id")}


def test_train_then_record_end_to_end_is_research_only_and_idempotent(store):
    t = adaptive.train(store, now=TRAIN_AT)
    assert t["status"] in ("success", "partial"), t.get("errors")
    fits = _members(store)
    assert fits["hockey_logistic@adaptive"]["status"] == "success" and fits["cand:logit_l2:nhl_base@adaptive"]["status"] == "success"
    from app.candidates import deep
    torch_here = deep.libraries_present(registry.BY_NAME["torch_mlp:nhl_base"])
    # Without the pinned library the deep fit is skipped with the reason (visible, never substituted); with it, it is fitted.
    assert fits["cand:torch_mlp:nhl_base@adaptive"]["status"] == ("success" if torch_here else "skipped")
    assert json.loads(fits["hockey_logistic@adaptive"]["detail"])["fit_info"]["cadence"] == "weekly"
    comb = adaptive.latest_fit(store, "nhl", "combiner", "combiner", t["definition"])
    assert comb is not None and comb["period"] == "2027:2026-W42"
    detail = json.loads(comb["detail"])
    assert detail["markets"]["winner"]["weights"] and detail["markets"]["winner"]["training"]["prospective"] == 0
    assert "hockey_logistic" in detail["excluded"] and "hockey_logistic@adaptive" in detail["members_available"]
    # The registered member fits are visible to the existing training monitor through model_fits.
    assert store.execute("SELECT count(*) FROM model_fits WHERE member LIKE 'adaptive:%' AND schedule='weekly'").fetchone()[0] >= 4
    assert adaptive.due(store, "2026-10-12T20:50:00Z")["due"] is False
    run(store, CYCLE_AT)                                      # primary two-hour forecasts: the lines to combine at
    primary = ("predictions", "market_forecasts", "research_cohorts", "candidate_forecasts", "selector_decisions")
    before = [store.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in primary]
    r = adaptive.record(store, game_ids=[GID], now=CYCLE_AT)
    assert r["status"] in ("success", "partial"), r
    rows = {x["market"]: x for x in store.execute("SELECT * FROM adaptive_forecasts")}
    assert set(rows) == {"winner", "reg3", "spread", "total", "team_total_home", "team_total_away"}
    w = rows["winner"]
    assert w["status"] == "forecast" and abs(w["p_a"] + w["p_mid"] + w["p_b"] - 1) < 1e-6, (w["reason"], w["missing"])
    names = {m["name"] for m in json.loads(w["members"]) if m["role"] == "in the combination"}
    assert "hockey_logistic@adaptive" in names and "hockey_elo" in names
    if torch_here:
        assert "cand:torch_mlp:nhl_base@adaptive" in names
    else:
        assert json.loads(w["missing"])["cand:torch_mlp:nhl_base@adaptive"].startswith("no adaptive fit")
    assert w["odds_status"] == "available" and json.loads(w["bookmaker"])["p"][0] > 0.5      # moneyline -150 retrieved before
    assert rows["team_total_home"]["odds_status"].startswith("unavailable")                 # no quote at that line
    assert rows["spread"]["line"] == -1.5 and rows["spread"]["line_ref"].startswith("market_forecasts:")
    sel = json.loads(w["selection"])
    assert sel["status"] == "no_pick" and "insufficient_evidence" in sel["reason"]           # thresholds unchanged, no evidence
    assert w["training_cutoff"] <= TRAIN_AT and w["weights_updated_at"] == comb["finished_at"]
    assert all(x["generated_at"] < x["tipoff_at"] for x in rows.values())
    again = adaptive.record(store, game_ids=[GID], now=CYCLE_AT)
    assert again["created"] == 0 and again["already_recorded"] == len(rows)
    after = [store.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in primary]
    assert after == before and before[0] > 0                                                  # nothing reaches the existing tables
    assert store.execute("SELECT count(*) FROM adaptive_forecasts").fetchone()[0] == len(rows)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.execute("UPDATE adaptive_forecasts SET p_a=0.5")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.execute("DELETE FROM adaptive_fits")
    d = adaptive.game_detail(store, GID, CYCLE_AT)
    assert d["status"] == "recorded" and d["markets"][0]["contributing"] and d["markets"][0]["weights_updated_at"]
    s = adaptive.status(store, CYCLE_AT)
    assert s["combiner"]["fit_id"] == comb["fit_id"] and s["prospective"]["pending"] == len(rows)


def test_new_outcomes_enter_the_next_fit_as_prospective_rows_and_can_change_weights(store):
    adaptive.train(store, now=TRAIN_AT)
    run(store, CYCLE_AT)
    adaptive.record(store, game_ids=[GID], now=CYCLE_AT)
    first = json.loads(adaptive.latest_fit(store, "nhl", "combiner", "combiner", adaptive.definition(store))["detail"])
    add_upcoming(store, gid=2026020002, start="2026-10-20T19:00:00", at="2026-10-13T02:30:00Z")   # a next game to prepare for
    d = adaptive.due(store, "2026-10-13T02:40:00Z")
    assert d["due"] is False and "no eligible result" in d["reason"]                          # new period, nothing settled yet
    _settle(store)                                                                            # the game ends 4–2
    d = adaptive.due(store, "2026-10-13T04:00:00Z")
    assert d["due"] and "new period" in d["reason"]
    t = adaptive.train(store, now="2026-10-13T04:00:00Z")
    assert t["status"] in ("success", "partial"), t.get("errors")
    second = t["combiner"]["markets"]
    assert second["winner"]["training"]["prospective"] == 1 and second["spread"]["training"]["prospective"] == 1
    assert second["winner"]["training"]["results_through"] == "2026-10-13T03:00:00Z"
    assert second["winner"]["weights"] != first["markets"]["winner"]["weights"]
    pro = adaptive.prospective_rows(store, "nhl", adaptive.definition(store), "2026-10-13T04:00:00Z")
    assert {p["market"] for p in pro} == {"winner", "reg3", "spread", "total", "team_total_home", "team_total_away"}
    assert all(p["result_available_at"] == "2026-10-13T03:00:00Z" for p in pro)
    rec = adaptive.prospective_record(store, "nhl", adaptive.definition(store), "2026-10-13T04:00:00Z")
    assert rec["markets"]["winner"]["settled"] == 1 and rec["markets"]["winner"]["bookmaker"]["games"] == 1


def test_outcomes_available_after_the_cutoff_never_enter_a_fit(monkeypatch):
    monkeypatch.setattr(adaptive, "MIN_TRAIN", 20)
    rows = []
    for i in range(80):
        avail = f"2026-01-{i % 28 + 1:02d}T05:00:00Z"
        rows.append({"game_id": f"g{i}", "season": 2026, "cutoff_at": avail[:11] + "04:00:00Z", "result_available_at": avail,
                     "line": None, "realised": 0 if i % 3 else 2, "vectors": {"a": (0.6, 0.0, 0.4), "b": (0.55 + (i % 5) / 100, 0.0, 0.45 - (i % 5) / 100)}})
    cutoff = "2026-01-15T04:00:00Z"
    out = adaptive.fit_combiner(rows, ["a", "b"], "winner", cutoff)
    assert out["rows_after_cutoff"] > 0 and out["training"]["results_through"] <= cutoff
    flipped = [dict(r, realised=2 - r["realised"]) if r["result_available_at"] > cutoff else r for r in rows]
    assert adaptive.fit_combiner(flipped, ["a", "b"], "winner", cutoff)["weights"] == out["weights"]
    earlier = [dict(r, realised=2 - r["realised"]) if r["result_available_at"] <= cutoff else r for r in rows]
    assert adaptive.fit_combiner(earlier, ["a", "b"], "winner", cutoff)["weights"] != out["weights"]


def _feature_rows(n=160, seed=3):
    import random
    from datetime import datetime, timedelta, timezone
    rng = random.Random(seed)
    rows = []
    t0 = datetime(2025, 10, 1, 0, 0, tzinfo=timezone.utc)
    for i in range(n):
        tip = t0 + timedelta(days=i)
        x = {k: rng.gauss(0, 1) for k in features.NHL_BASE}
        x["neutral"] = 0.0
        p = 1 / (1 + pow(2.718, -(x["elo_diff"] + 0.3)))
        won = rng.random() < p
        reg = (2, 1) if won else (1, 2)
        rows.append(features.Row(f"g{i}", 2026 if tip.month >= 10 or tip.year == 2026 else 2025, tip.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                 (tip - timedelta(hours=20)).strftime("%Y-%m-%dT%H:%M:%SZ"), x,
                                 (tip + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                 {"home_won": won, "margin": 1 if won else -1, "total": 3, "home": reg[0], "away": reg[1],
                                  "regulation": reg, "decision": "REG"}))
    return rows


def test_scheduled_walk_refits_only_with_new_results_and_future_flips_never_change_earlier_periods(monkeypatch):
    monkeypatch.setattr(registry, "MIN_TRAIN", {"nba": 200, "nhl": 40})
    spec = registry.BY_NAME["logit_l2:nhl_base"]
    rows = _feature_rows()
    store = adaptive.scheduled_walk(spec, rows, {})
    fits = [f for f in store["fits"] if f["refit"]]
    assert fits and all(f["results_through"] <= f["cutoff"] for f in fits)
    assert all(f["refit"] or f.get("reason") for f in store["fits"])
    # A period with no new delivered result keeps the fit (same training fingerprint).
    gap = list(rows)
    gap[90] = replace(gap[90], result_available_at="2027-06-01T00:00:00Z", y=None)
    s2 = adaptive.scheduled_walk(spec, gap, {})
    assert any("no new results" in (f.get("reason") or "") for f in s2["fits"]) or len([f for f in s2["fits"] if f["refit"]]) <= len(fits)
    cut = rows[100].cutoff_at
    flipped = [replace(r, y={**r.y, "home_won": not r.y["home_won"]}) if r.result_available_at > cut else r for r in rows]
    s3 = adaptive.scheduled_walk(spec, flipped, {})
    for r in rows[:100]:
        if r.game_id in store["outputs"]:
            assert s3["outputs"][r.game_id] == store["outputs"][r.game_id]
    assert any(s3["outputs"].get(r.game_id) != store["outputs"].get(r.game_id) for r in rows[110:])
    # Restartable: resuming from a checkpoint reproduces the from-scratch history exactly.
    partial = adaptive.scheduled_walk(spec, rows[:120], {})
    resumed = adaptive.scheduled_walk(spec, rows, partial)
    assert resumed["outputs"] == store["outputs"] and resumed["periods"] == store["periods"]


def test_combine_now_uses_leave_one_out_for_one_missing_member_and_abstains_for_more(monkeypatch):
    monkeypatch.setattr(adaptive, "MIN_TRAIN", 20)
    rows = [{"game_id": str(i), "season": 2026, "cutoff_at": f"2026-01-{i % 28 + 1:02d}T04:00:00Z",
             "result_available_at": f"2026-01-{i % 28 + 1:02d}T05:00:00Z", "line": None, "realised": 0 if i % 2 else 2,
             "vectors": {"a": (0.6, 0.0, 0.4), "b": (0.58, 0.0, 0.42), "c": (0.45, 0.0, 0.55)}} for i in range(60)]
    cm = adaptive.fit_combiner(rows, ["a", "b", "c"], "winner", "2026-02-01T00:00:00Z")
    assert cm["members"] == ["a", "b", "c"] and set(cm["fallbacks"]) == {"a", "b", "c"}
    full, _, names, reason = adaptive.combine_now(cm, {"a": (0.6, 0, 0.4), "b": (0.58, 0, 0.42), "c": (0.45, 0, 0.55)})
    assert full is not None and names == ["a", "b", "c"] and reason is None
    one, _, names, reason = adaptive.combine_now(cm, {"a": (0.6, 0, 0.4), "c": (0.45, 0, 0.55)})
    assert one is not None and names == ["a", "c"]
    none, _, _, reason = adaptive.combine_now(cm, {"a": (0.6, 0, 0.4)})
    assert none is None and "missing members b, c" in reason
    assert adaptive.combine_now({"stack": None, "reason": "only 1 members"}, {})[3].startswith("no combination trained")


def test_combine_now_uses_a_group_fallback_only_when_exactly_that_group_is_missing(monkeypatch):
    """Members that share external inputs (the player box / injury cache guards) abstain together, so the combiner also
    trains one stack without the whole group. Any other missing subset still abstains: weights are never substituted."""
    monkeypatch.setattr(adaptive, "MIN_TRAIN", 20)
    vec = {"a": (0.6, 0.0, 0.4), "b": (0.58, 0.0, 0.42), "c": (0.45, 0.0, 0.55), "d": (0.52, 0.0, 0.48)}
    rows = [{"game_id": str(i), "season": 2026, "cutoff_at": f"2026-01-{i % 28 + 1:02d}T04:00:00Z",
             "result_available_at": f"2026-01-{i % 28 + 1:02d}T05:00:00Z", "line": None, "realised": 0 if i % 2 else 2,
             "vectors": dict(vec)} for i in range(60)]
    cm = adaptive.fit_combiner(rows, ["a", "b", "c", "d"], "winner", "2026-02-01T00:00:00Z", groups=[["c", "d"]])
    assert cm["members"] == ["a", "b", "c", "d"] and set(cm["fallbacks"]) == {"a", "b", "c", "d"}
    assert [(f["missing"], f["members"]) for f in cm["group_fallbacks"]] == [(["c", "d"], ["a", "b"])]
    p, stack, names, reason = adaptive.combine_now(cm, {"a": vec["a"], "b": vec["b"]})
    assert p is not None and names == ["a", "b"] and reason is None and stack is cm["group_fallbacks"][0]["stack"]
    one, _, names, _ = adaptive.combine_now(cm, {"a": vec["a"], "b": vec["b"], "d": vec["d"]})
    assert one is not None and names == ["a", "b", "d"]                     # one of the group: leave-one-out as before
    none, _, _, reason = adaptive.combine_now(cm, {"a": vec["a"]})            # the group plus another member: not trained
    assert none is None and "missing members b, c, d" in reason
    none, _, _, reason = adaptive.combine_now(cm, {"c": vec["c"], "d": vec["d"]})       # a different pair: not trained
    assert none is None and "missing members a, b" in reason and "no combination trained for this subset" in reason
    # A group with a single chosen member is already covered by its leave-one-out stack; fallbacks=False trains none.
    assert adaptive.fit_combiner(rows, ["a", "b", "c"], "winner", "2026-02-01T00:00:00Z", groups=[["c", "d"]])["group_fallbacks"] == []
    assert adaptive.fit_combiner(rows, ["a", "b", "c", "d"], "winner", "2026-02-01T00:00:00Z", fallbacks=False,
                                 groups=[["c", "d"]])["group_fallbacks"] == []


def test_a_failed_member_refit_keeps_the_previous_artifact_in_force_and_is_visible(store, monkeypatch):
    adaptive.train(store, now=TRAIN_AT)
    ok = adaptive.latest_fit(store, "nhl", "member", "cand:logit_l2:nhl_base@adaptive", adaptive.definition(store))
    _settle(store)
    add_upcoming(store, gid=2026020002, start="2026-10-20T19:00:00", at="2026-10-13T03:00:00Z")
    real = engine.fit

    def boom(spec, train):
        if spec.name == "logit_l2:nhl_base":
            raise RuntimeError("solver exploded")
        return real(spec, train)
    monkeypatch.setattr(engine, "fit", boom)
    t = adaptive.train(store, now="2026-10-13T04:00:00Z")
    assert "solver exploded" in t["errors"]["cand:logit_l2:nhl_base@adaptive"] and t["status"] == "partial", t["errors"]
    assert adaptive.latest_fit(store, "nhl", "member", "cand:logit_l2:nhl_base@adaptive", adaptive.definition(store))["fit_id"] == ok["fit_id"]
    bad = adaptive.latest_fit(store, "nhl", "member", "cand:logit_l2:nhl_base@adaptive", adaptive.definition(store), status="failed")
    assert bad is not None and "solver exploded" in json.loads(bad["detail"])["error"]
    s = adaptive.status(store, "2026-10-13T05:00:00Z")
    m = next(x for x in s["members"] if x["name"] == "cand:logit_l2:nhl_base@adaptive")
    assert m["fit_in_force"]["fit_id"] == ok["fit_id"] and "solver exploded" in m["last_failure"]["error"]
    # The combiner of the second period still trained on the member's intact history, but the run is 'partial' (a member
    # failed), so the week is retried by the next scheduled run instead of being treated as done.
    assert t["combiner"]["status"] == "partial" and "member refit failed" in t["combiner"]["partial_reason"]
    d = adaptive.due(store, "2026-10-13T05:00:00Z")
    assert d["due"] and "partial" in d["reason"]
    monkeypatch.setattr(engine, "fit", real)
    t2 = adaptive.train(store, now="2026-10-13T05:30:00Z")
    assert t2["status"] == "success" and t2["combiner"]["status"] == "success"
    assert adaptive.due(store, "2026-10-13T06:00:00Z")["due"] is False


def test_a_corrupt_artifact_makes_the_member_missing_never_substituted(store):
    adaptive.train(store, now=TRAIN_AT)
    fit = adaptive.latest_fit(store, "nhl", "member", "hockey_logistic@adaptive", adaptive.definition(store))
    with open(fit["artifact_path"], "ab") as fh:
        fh.write(b"corrupt")
    run(store, CYCLE_AT)
    r = adaptive.record(store, game_ids=[GID], now=CYCLE_AT)
    w = store.execute("SELECT * FROM adaptive_forecasts WHERE market='winner'").fetchone()
    assert "ArtifactMismatch" in json.loads(w["missing"])["hockey_logistic@adaptive"]
    assert w["status"] == "forecast"                                  # one missing member: the leave-one-out stack
    assert "hockey_logistic@adaptive" not in json.loads(w["weights"])["members"]
    assert r["status"] in ("success", "partial")


def test_record_without_a_combiner_fit_writes_an_unavailable_row_with_the_reason(store):
    run(store, CYCLE_AT)
    r = adaptive.record(store, game_ids=[GID], now=CYCLE_AT)
    assert r["unavailable"] > 0 and r["created"] == 0
    w = store.execute("SELECT * FROM adaptive_forecasts WHERE market='winner'").fetchone()
    assert w["status"] == "unavailable" and w["p_a"] is None and "no combiner fit" in w["reason"]
    assert json.loads(w["selection"])["status"] == "no_pick"


def test_jobs_run_training_nightly_and_recording_in_the_two_hour_cycle(store, monkeypatch):
    monkeypatch.setattr(jobs, "refresh", lambda conn, **kw: {"status": "skipped"})
    monkeypatch.setattr(jobs, "odds_snapshot", lambda conn, **kw: {"status": "skipped"})
    monkeypatch.setattr(jobs, "nhl_pbp", lambda conn, **kw: {"status": "skipped"})
    calls = []
    monkeypatch.setattr(adaptive, "train", lambda conn, **kw: calls.append(kw) or {"status": "skipped", "reason": "test"})
    out = jobs.daily(store)
    # The nightly job only reports the due state: training never runs inside the store's job lock (it would make the
    # two-hour cycles skip); `adaptive-train` runs it under its own lock.
    assert out["adaptive_train"]["status"] == "due" and calls == [] and "own lock" in out["adaptive_train"]["note"]
    monkeypatch.setattr(adaptive, "record", lambda conn, **kw: (_ for _ in ()).throw(RuntimeError("adaptive down")))
    out = jobs.predict_now(store, now=CYCLE_AT)
    assert out["status"] == "success" and out["adaptive"]["status"] == "failed" and "adaptive down" in out["adaptive"]["error"]
    monkeypatch.setenv("RESEARCH_SELECTION", "0")
    assert jobs.daily(store)["adaptive_train"]["status"] == "disabled"


def test_two_hour_window_and_start_time_are_enforced_for_adaptive_rows(store):
    adaptive.train(store, now=TRAIN_AT)
    run(store, CYCLE_AT)
    adaptive.record(store, game_ids=[GID], now=CYCLE_AT)
    r = dict(store.execute("SELECT * FROM adaptive_forecasts WHERE market='winner'").fetchone())
    r.pop("forecast_id")
    for when in ("2026-10-12T20:59:59Z", "2026-10-12T23:00:00Z"):
        r.update(generated_at=when, idempotency_key=f"x{when}")
        with pytest.raises(sqlite3.IntegrityError, match="two hours"):
            store.execute(f"INSERT INTO adaptive_forecasts ({','.join(r)}) VALUES ({','.join('?' * len(r))})", list(r.values()))


def test_freeze_and_thaw_round_trip_a_function_local_member_class(store):
    mems, _ = adaptive.members(store)
    m = next(x for x in mems if x.name == "hockey_logistic_xg@adaptive")
    m.obj.delivered.append("x")
    m.obj.member.season = 2026
    blob = adaptive.freeze(m.obj)
    fresh = next(x for x in adaptive.members(store)[0] if x.name == "hockey_logistic_xg@adaptive").obj
    adaptive.thaw(fresh, blob)
    assert fresh.delivered == ["x"] and fresh.member.season == 2026 and fresh.name == m.obj.name


def test_state_rejects_non_dict_getstate_so_external_cache_cannot_leak():
    # A member defines __getstate__ to keep its external cache out of the artifact. If that returns a
    # non-dict we must raise, never silently fall back to vars(obj) and re-include the cache.
    class Leaky:
        def __init__(self): self._inputs = "external cache"
        def __getstate__(self): return ("not", "a", "dict")

    with pytest.raises(TypeError, match="must return a dict"):
        adaptive._state(Leaky())

    class Clean:
        def __init__(self): self._inputs = "external cache"
        def __getstate__(self): return {k: v for k, v in vars(self).items() if k != "_inputs"}

    assert adaptive._state(Clean()) == {}      # cache excluded by __getstate__, no vars(obj) fallback


def test_walk_member_history_is_out_of_sample_and_blind_to_later_and_own_outcomes(store):
    from dataclasses import replace as rep
    from app import live
    now = "2026-10-12T20:00:00Z"
    rows = live.available_rows(store, now)
    seasons = {r.game.season for r in rows}

    def history(rows_):
        m = next(x for x in adaptive.members(store)[0] if x.name == "hockey_logistic@adaptive")
        lines = {}
        return adaptive.walk_member(m, rows_, [], now, seasons, lines)
    base = history(rows)
    assert len(base) == len(rows) and all("winner" in v for v in base.values())
    order = sorted(rows, key=lambda r: (r.result_available_at, r.game.tipoff_at, r.game.game_id))
    cut = order[-30].result_available_at
    flipped = [rep(r, home_score=r.away_score, away_score=r.home_score) if r.result_available_at >= cut else r for r in rows]
    other = history(flipped)
    for r in order[:-30]:
        assert other[r.game.game_id] == base[r.game.game_id]            # earlier forecasts never see later outcomes
    assert any(other[r.game.game_id] != base[r.game.game_id] for r in order[-29:])   # later ones do learn from them
    own = order[-1]
    solo = history([rep(r, home_score=r.away_score, away_score=r.home_score) if r is own else r for r in rows])
    assert solo[own.game.game_id] == base[own.game.game_id]            # a forecast never sees its own game's outcome


def test_line_primary_walk_sets_model_lines_for_line_markets_only():
    from app.models.points import PointsConfig, PointsNormal
    from app.sports import NBA
    from test_backtest import synthetic_rows
    rows = synthetic_rows(seasons=range(2020, 2023), per_season=40)
    m = adaptive.Member("points_normal", "walk", PointsNormal(PointsConfig(eta=0.02, carry=0.75)), markets_=NBA.markets, line_primary=True)
    lines = {}
    hist = adaptive.walk_member(m, rows, None, None, {2022}, lines, line_primary=True)
    g = next(r.game.game_id for r in rows if r.game.season == 2022)
    assert set(lines[g]) == {"spread", "total", "team_total_home", "team_total_away"}       # never a "winner" line
    assert set(hist[g]) == set(NBA.markets) and all(abs(sum(v) - 1) < 1e-6 for v in hist[g].values())


def test_artifacts_are_content_addressed_and_fit_rows_load_only_their_own_bytes(store, tmp_path):
    base = tmp_path / "arts" / "x.pkl"
    p1, sha1 = adaptive._save(base, {"a": 1})
    p2, sha2 = adaptive._save(base, {"a": 2})
    assert p1 != p2 and sha1 != sha2 and adaptive._save(base, {"a": 1}) == (p1, sha1)      # a retry never overwrites
    assert adaptive._load(p1, sha1) == {"a": 1} and adaptive._load(p2, sha2) == {"a": 2}
    with pytest.raises(adaptive.ArtifactMismatch):
        adaptive._load(p2, sha1)                                                           # a row bound to other bytes
    # End to end: two fits of the same member keep two artifacts, and each fit row loads exactly the state it recorded.
    adaptive.train(store, now=TRAIN_AT)
    first = adaptive.latest_fit(store, "nhl", "member", "cand:logit_l2:nhl_base@adaptive", adaptive.definition(store))
    _settle(store)
    add_upcoming(store, gid=2026020002, start="2026-10-20T19:00:00", at="2026-10-13T03:00:00Z")
    adaptive.train(store, now="2026-10-13T04:00:00Z")
    second = adaptive.latest_fit(store, "nhl", "member", "cand:logit_l2:nhl_base@adaptive", adaptive.definition(store))
    assert second["fit_id"] != first["fit_id"] and second["artifact_path"] != first["artifact_path"]
    a, b = adaptive.load_fit(first), adaptive.load_fit(second)
    assert a["results_fingerprint"] == first["results_fingerprint"] != b["results_fingerprint"]
    # Bytes replaced under a row's path (even with a matching sidecar) are refused, never applied.
    import shutil
    shutil.copy(second["artifact_path"], first["artifact_path"])
    shutil.copy(second["artifact_path"] + ".sha256", first["artifact_path"] + ".sha256")
    with pytest.raises(adaptive.ArtifactMismatch):
        adaptive.load_fit(first)


def test_resume_after_the_live_preparation_reproduces_the_uninterrupted_walk(monkeypatch):
    """Codex's reproduction: checkpoint after 101 rows, prepare the live fit, resume. The history must keep the period's
    own fit, so g101–g103 (the rest of the last covered period) are predicted exactly as in one uninterrupted walk."""
    monkeypatch.setattr(registry, "MIN_TRAIN", {"nba": 200, "nhl": 40})
    spec = registry.BY_NAME["logit_l2:nhl_base"]
    rows = _feature_rows()
    whole = adaptive.scheduled_walk(spec, rows, {})
    partial = adaptive.scheduled_walk(spec, rows[:101], {})
    labelled = [r for r in rows[:101] if r.y is not None]
    rec, prepared = adaptive.live_fit(spec, partial, labelled, "next", 2026, rows[101].cutoff_at)
    assert rec["refit"] and prepared["model"] is not partial["model"]                     # the live fit never enters the history
    resumed = adaptive.scheduled_walk(spec, rows, partial)
    assert resumed["outputs"] == whole["outputs"] and resumed["periods"] == whole["periods"]
    assert all(resumed["outputs"][r.game_id] == whole["outputs"][r.game_id] for r in rows[101:104] if r.game_id in whole["outputs"])


def test_a_market_without_a_new_combination_keeps_the_earlier_one_and_the_week_is_retried(store, monkeypatch):
    adaptive.train(store, now=TRAIN_AT)
    first = adaptive.latest_fit(store, "nhl", "combiner", "combiner", adaptive.definition(store))
    _settle(store)
    add_upcoming(store, gid=2026020002, start="2026-10-20T19:00:00", at="2026-10-13T03:00:00Z")
    real = adaptive.fit_combiner

    def no_total(rows, candidates, market, cutoff, fallbacks=True, groups=()):
        if market == "total":
            return {"market": market, "cutoff": cutoff, "excluded": {}, "members": [], "stack": None, "fallbacks": {},
                    "reason": "simulated: no usable rows"}
        return real(rows, candidates, market, cutoff, fallbacks, groups)
    monkeypatch.setattr(adaptive, "fit_combiner", no_total)
    t = adaptive.train(store, now="2026-10-13T04:00:00Z")
    second = adaptive.latest_fit(store, "nhl", "combiner", "combiner", adaptive.definition(store))
    assert t["combiner"]["status"] == "partial" and "no combination for total" in t["combiner"]["partial_reason"]
    assert second["status"] == "partial" and second["fit_id"] != first["fit_id"]
    per, reason = adaptive.combiners_in_force(store, "nhl", adaptive.definition(store), "2026-10-13T05:00:00Z")
    assert reason is None and per["total"][0]["fit_id"] == first["fit_id"] and per["winner"][0]["fit_id"] == second["fit_id"]
    s = adaptive.status(store, "2026-10-13T05:00:00Z")
    assert s["combiner"]["status"] == "partial" and s["combiner"]["market_fit_ids"]["total"] == first["fit_id"]
    assert adaptive.due(store, "2026-10-13T05:00:00Z")["due"] is True
    monkeypatch.setattr(adaptive, "fit_combiner", real)
    t2 = adaptive.train(store, now="2026-10-13T05:30:00Z")
    assert t2["combiner"]["status"] == "success" and adaptive.due(store, "2026-10-13T06:00:00Z")["due"] is False


def test_a_member_whose_library_is_missing_keeps_its_fit_and_stays_in_the_combination(store, monkeypatch):
    adaptive.train(store, now=TRAIN_AT)
    _settle(store)
    add_upcoming(store, gid=2026020002, start="2026-10-20T19:00:00", at="2026-10-13T03:00:00Z")
    real = registry.available
    monkeypatch.setattr(registry, "available", lambda spec: False if spec.name == "logit_l2:nhl_base" else real(spec))
    t = adaptive.train(store, now="2026-10-13T04:00:00Z")                   # the scheduled environment lacks the library
    m = t["members"]["cand:logit_l2:nhl_base@adaptive"]
    assert m["status"] == "success" and m["fit"]["kept_for_dependency"] and not m["fit"]["refit"]
    assert "cand:logit_l2:nhl_base@adaptive" in t["combiner"]["markets"]["winner"]["members"]
    assert t["combiner"]["status"] == "success" and adaptive.due(store, "2026-10-13T05:00:00Z")["due"] is False
    monkeypatch.setattr(registry, "available", real)                         # the research environment has it: due again
    d = adaptive.due(store, "2026-10-13T05:00:00Z")
    assert d["due"] and "kept for a missing library" in d["reason"]
    t2 = adaptive.train(store, now="2026-10-13T05:30:00Z")
    assert t2["members"]["cand:logit_l2:nhl_base@adaptive"]["fit"]["refit"] is True
    assert adaptive.due(store, "2026-10-13T06:00:00Z")["due"] is False


def test_scheduled_training_never_holds_the_store_lock_while_fitting_and_publishes_briefly(store, monkeypatch):
    """The two-hour cycles take the store lock with a short wait; a refit takes up to an hour. The scheduled command
    snapshots and publishes under the lock and trains from the snapshot under a separate training lock."""
    import threading
    from app import db
    started, release = threading.Event(), threading.Event()
    real = adaptive.train

    def slow(conn, **kw):
        started.set()
        assert release.wait(60), "test release never came"
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1                 # trains from the read-only snapshot
        return real(conn, **kw)
    monkeypatch.setattr(adaptive, "train", slow)
    result = {}
    t = threading.Thread(target=lambda: result.update(adaptive.run_scheduled("nhl", now=TRAIN_AT)))
    t.start()
    assert started.wait(60)
    with db.store_lock("nhl", wait_seconds=0) as held:                               # a cycle can take the job lock now
        assert held
        run(store, CYCLE_AT)                                                         # ... and forecast while training runs
        r = adaptive.record(store, game_ids=[GID], now=CYCLE_AT)
        assert r["unavailable"] > 0 and "no combiner fit" in r["forecasts"][0]["reason"]
    with adaptive.training_lock("nhl") as held:
        assert not held                                                              # the training lock is the one taken
    release.set()
    t.join(600)
    assert result["status"] == "success", result
    assert result["publication"]["published"] > 0 and not result["publication"]["failed"]
    assert adaptive.latest_fit(store, "nhl", "combiner", "combiner", adaptive.definition(store)) is not None
    assert store.execute("SELECT count(*) FROM model_fits WHERE member LIKE 'adaptive:%'").fetchone()[0] > 0
    assert not list((adaptive.root("nhl") / "snapshots").glob("*.sqlite"))           # the snapshot is gone
    again = adaptive.run_scheduled("nhl", now="2026-10-12T20:50:00Z")
    assert again["status"] == "skipped" and "already prepared" in again["reason"]     # due-only, outside the lock


def test_overlapping_trainers_never_touch_each_others_snapshot_and_a_second_run_is_refused(store, monkeypatch):
    import threading
    from app import db
    started, release = threading.Event(), threading.Event()
    real = adaptive.train

    def slow(conn, **kw):
        started.set()
        assert release.wait(60)
        return real(conn, **kw)
    monkeypatch.setattr(adaptive, "train", slow)
    result = {}
    t = threading.Thread(target=lambda: result.update(adaptive.run_scheduled("nhl", now=TRAIN_AT)))
    t.start()
    assert started.wait(60)
    snaps = list((adaptive.root("nhl") / "snapshots").glob("*.sqlite"))
    assert len(snaps) == 1
    second = adaptive.run_scheduled("nhl", now=TRAIN_AT)                            # refused before touching anything
    assert second["status"] == "skipped" and "training lock" in second["reason"]
    assert list((adaptive.root("nhl") / "snapshots").glob("*.sqlite")) == snaps        # the first run's snapshot survives
    with db.store_lock("nhl", wait_seconds=0) as held:
        assert held                                                                  # and the job lock was never held
    release.set()
    t.join(600)
    assert result["status"] == "success" and not list((adaptive.root("nhl") / "snapshots").glob("*.sqlite"))


def test_a_member_artifact_failing_at_publication_leaves_the_week_retryable(store):
    """A damaged member artifact is published as 'failed' and its combiner as 'partial' (never 'success'), so `due`
    retries the period instead of treating it as complete."""
    with adaptive.training_lock("nhl"):
        snap = adaptive.snapshot(store, "nhl")
    pend = adaptive.train(adaptive.open_snapshot(snap), now=TRAIN_AT, force=True, publish=False)
    victim = next(p for p in pend["pending"] if p["kind"] == "member" and p["status"] == "success")
    victim["sha"] = "0" * 64
    pub = adaptive.publish(store, pend)
    assert any(f["name"] == victim["name"] for f in pub["failed"]) and pub["combiner_status"] == "partial"
    bad = adaptive.latest_fit(store, "nhl", "member", victim["name"], adaptive.definition(store), status="failed")
    assert bad is not None and "did not verify" in json.loads(bad["detail"])["error"]
    comb = adaptive.latest_fit(store, "nhl", "combiner", "combiner", adaptive.definition(store))
    assert comb["status"] == "partial" and victim["name"] in json.loads(comb["detail"])["partial_reason"]
    d = adaptive.due(store, "2026-10-12T20:50:00Z")
    assert d["due"] and "partial" in d["reason"]
    # A clean publication of the same run then completes the week.
    clean = adaptive.train(adaptive.open_snapshot(snap), now=TRAIN_AT, force=True, publish=False)
    pub2 = adaptive.publish(store, clean)
    assert not pub2["failed"] and pub2["combiner_status"] == "success"
    assert adaptive.due(store, "2026-10-12T20:50:00Z")["due"] is False


def test_a_new_week_with_new_results_is_trained_and_published_for_nba(monkeypatch, tmp_path):
    """Ordinary next-period scheduling on the synthetic NBA store: train, forecast, settle, next week → the settled
    prospective forecast enters the new fit through the scheduled command (snapshot + publish), not a forced rerun."""
    from app import backtest, ingest, live, market_live, near_tipoff
    from app.sports import NBA
    from test_backtest import synthetic_rows
    from test_live import T_GAME, event, ingest_events
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    from app import db
    conn = db.connect(tmp_path / "nba.sqlite")
    db.migrate(conn)
    history = [event(r.game.game_id, r.game.tipoff_at, r.game.home_team_id.split(":")[1], r.game.away_team_id.split(":")[1],
                     "STATUS_FINAL", r.home_score, r.away_score, r.game.season) for r in synthetic_rows(per_season=12)]
    ingest_events(conn, history, "2026-09-30T00:00:00Z", month="202601")
    val = backtest.evaluate(conn, "validation", out_dir=tmp_path / "val", log=lambda _: None)
    (tmp_path / "decision.json").write_text(json.dumps(backtest.decision_from_validation_report(val, "synthetic")))
    monkeypatch.setattr(backtest, "FROZEN_DECISION_PATH", tmp_path / "decision.json")
    db.claim_store(conn, "nba")
    monkeypatch.setattr(score_candidates, "MIN_EXAMPLES", 30)
    monkeypatch.setattr(score_candidates, "MIN_RESIDUALS", 10, raising=False)
    monkeypatch.setattr(engine, "MIN_RESIDUALS", 10)
    monkeypatch.setattr(research_live, "STACK_MIN_TRAIN", 20)
    monkeypatch.setattr(registry, "MIN_TRAIN", {"nba": 40, "nhl": 100})
    monkeypatch.setattr(registry, "SPECS", tuple(registry.BY_NAME[s] for s in ("logit_l2:nba_base", "ridge:nba_base")))
    monkeypatch.setattr(adaptive, "MIN_TRAIN", 20)
    monkeypatch.setattr(adaptive, "LEARNED", {**adaptive.LEARNED, "nba": ("logistic", "ridge_points")})
    ingest_events(conn, [event("g1", T_GAME, "1", "2", odds=("-150", "+130"))], "2026-10-19T12:00:00Z")
    clock = _test_clock("2026-10-21T20:00:00Z", "2026-10-21T22:00:00Z")      # publication before the forecast below
    monkeypatch.setattr(adaptive, "utc_now", clock)
    first = adaptive.run_scheduled("nba", now="2026-10-21T20:00:00Z")
    assert first["status"] == "success", (first["training"]["members"], first["training"]["combiner"])
    conn.execute("INSERT INTO job_runs (job, started_at, finished_at, status) VALUES ('refresh', '2026-10-21T22:00:00Z', "
                 "'2026-10-21T22:00:00Z', 'success')")
    now = "2026-10-21T22:00:00Z"
    ids = [f["game_id"] for f in near_tipoff.predict_now(conn, now=now)["forecasts"]]
    assert ids == ["espn:g1"]
    market_live.forecast(conn, now=now, policy="near_tipoff", game_ids=ids)
    r = adaptive.record(conn, game_ids=ids, now=now)
    assert r["created"] >= 1 and {f["market"] for f in r["forecasts"] if f["status"] == "forecast"} >= {"winner", "spread"}, r
    ingest_events(conn, [event("g1", T_GAME, "1", "2", "STATUS_FINAL", 104, 99)], "2026-10-22T03:00:00Z")   # settled
    ingest_events(conn, [event("g2", "2026-10-28T23:30:00Z", "3", "1")], "2026-10-22T03:30:00Z")            # next week's game
    d = adaptive.due(conn, "2026-10-22T04:00:00Z")
    assert d["due"] and "new period" in d["reason"]
    clock.at("2026-10-22T04:00:00Z", "2026-10-22T05:00:00Z")
    second = adaptive.run_scheduled("nba", now="2026-10-22T04:00:00Z")
    assert second["status"] == "success", second
    fit = adaptive.latest_fit(conn, "nba", "combiner", "combiner", adaptive.definition(conn))
    detail = json.loads(fit["detail"])
    assert detail["markets"]["winner"]["training"]["prospective"] == 1 and fit["training_cutoff"] == "2026-10-22T03:00:00Z"
    assert set(NBA.markets) <= set(detail["markets"])


def test_a_member_record_write_failure_leaves_the_week_retryable_and_an_ordinary_retry_completes_it(store, monkeypatch):
    """Codex's reproduction: a valid artifact whose member row cannot be written must not leave the combiner 'success'."""
    with adaptive.training_lock("nhl"):
        snap = adaptive.snapshot(store, "nhl")
    pend = adaptive.train(adaptive.open_snapshot(snap), now=TRAIN_AT, force=True, publish=False)
    victim = next(p for p in pend["pending"] if p["kind"] == "member" and p["status"] == "success")["name"]
    real = adaptive._insert_fit

    def flaky(conn, sport, kind, name, *a, **kw):
        if name == victim:
            raise RuntimeError("disk full")
        return real(conn, sport, kind, name, *a, **kw)
    monkeypatch.setattr(adaptive, "_insert_fit", flaky)
    pub = adaptive.publish(store, pend)
    assert [f["name"] for f in pub["failed"]] == [victim] and pub["combiner_status"] == "partial"
    comb = adaptive.latest_fit(store, "nhl", "combiner", "combiner", adaptive.definition(store))
    assert comb["status"] == "partial" and victim in json.loads(comb["detail"])["partial_reason"]
    assert adaptive.latest_fit(store, "nhl", "member", victim, adaptive.definition(store)) is None
    d = adaptive.due(store, "2026-10-12T20:50:00Z")
    assert d["due"] and "partial" in d["reason"]
    monkeypatch.setattr(adaptive, "_insert_fit", real)
    retry = adaptive.run_scheduled("nhl", now=TRAIN_AT)                          # the ordinary scheduled retry, unforced
    assert retry["status"] == "success" and not retry["publication"]["failed"] and retry["publication"]["combiner_status"] == "success"
    assert adaptive.latest_fit(store, "nhl", "member", victim, adaptive.definition(store)) is not None
    assert adaptive.due(store, "2026-10-12T20:50:00Z")["due"] is False


def test_nba_player_injury_member_is_excluded_without_the_research_cache_and_identity_ignores_the_environment(monkeypatch, tmp_path):
    from test_player_ratings import _write_box
    from app import backtest, db, player_ratings as pr
    from test_backtest import synthetic_rows
    from test_live import event, ingest_events
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    conn = db.connect(tmp_path / "nba.sqlite")
    db.migrate(conn)
    ingest_events(conn, [event(r.game.game_id, r.game.tipoff_at, r.game.home_team_id.split(":")[1], r.game.away_team_id.split(":")[1],
                               "STATUS_FINAL", r.home_score, r.away_score, r.game.season) for r in synthetic_rows(per_season=12)],
                  "2026-09-30T00:00:00Z", month="202601")
    val = backtest.evaluate(conn, "validation", out_dir=tmp_path / "val", log=lambda _: None)
    (tmp_path / "decision.json").write_text(json.dumps(backtest.decision_from_validation_report(val, "synthetic")))
    monkeypatch.setattr(backtest, "FROZEN_DECISION_PATH", tmp_path / "decision.json")
    db.claim_store(conn, "nba")
    mems, excluded = adaptive.members(conn)
    assert pr.ADAPTIVE_MEMBER in excluded and "player_box" in excluded[pr.ADAPTIVE_MEMBER]
    assert not any(m.name.startswith(pr.ADAPTIVE_MEMBER) for m in mems)
    defn = adaptive.definition(conn)
    _write_box(tmp_path / "research" / "availability", 2026, [("x1", "2026-01-02", {"espn:1": [("p1", 240)], "espn:2": [("p2", 240)]})])
    mems, excluded = adaptive.members(conn)
    member = next(m for m in mems if m.name == f"{pr.ADAPTIVE_MEMBER}@adaptive")
    assert member.learned and member.markets == ("winner",) and isinstance(member.obj, adaptive.Adaptive)
    assert pr.ADAPTIVE_MEMBER not in excluded and pr.ADAPTIVE_MEMBER in adaptive.BOX_DEPENDENT["nba"]
    assert adaptive.definition(conn) == defn                              # the identity is code, never the environment
    conn.close()


def test_nba_player_injury_member_trains_records_and_keeps_its_artifact_small(monkeypatch, tmp_path):
    """The member walks the synthetic NBA store with a synthetic research cache (box scores for every game, two reports),
    is fitted and published, forecasts in the two-hour cycle after a thaw, and its artifact carries ratings and the
    delivered-game index, never the box."""
    import pickle
    from pathlib import Path
    from app import availability as av, db, player_ratings as pr
    from test_backtest import synthetic_rows
    from test_player_ratings import _write_box, _fake_reports, _entry
    rdir = tmp_path / "research" / "availability"
    by_season = {}
    for r in synthetic_rows(per_season=12):
        g = r.game
        day = pr.et_day(g.tipoff_at)
        teams = {t: [(f"{t.split(':')[1]}p{j}", m) for j, m in enumerate((36, 34, 32, 30, 28, 26, 24, 30))]
                 for t in (g.home_team_id, g.away_team_id)}
        by_season.setdefault(g.season, []).append((g.game_id, day, teams))          # the store id is espn:<this>
    for season, games in by_season.items():
        _write_box(rdir, season, games)
    day_before, (_, next_day, next_teams) = by_season[2026][5][1], by_season[2026][6]        # consecutive days
    team = next(iter(next_teams)).split(":")[1]
    # The served games g1 (2026-10-21) and g2 (2026-10-28) have a usable same-day report listing both teams, published
    # 17:30 ET (21:30Z) before their forecasts: without one the member abstains (missing information is never healthy).
    served = {av.report_name(d): {"header": f"{d}T21:30:00Z", "entries": [_entry(d, f"Team {t}", f"{t}p0", "Probable")
                                                                          for t in teams]}
              for d, teams in (("2026-10-21", ("1", "2")), ("2026-10-28", ("3", "1")))}
    _fake_reports(monkeypatch, rdir / "reports", {av.report_name(day_before): {
        "header": f"{day_before}T22:30:00Z",                                                  # 17:30 ET, before midnight
        "entries": [_entry(next_day, f"Team {team}", f"{team}p0", "Out")]}, **served})
    trained_with = []                     # training reads the stored live reports too (train/serve consistency)
    real_prepare = pr.PlayersInjuriesV1.prepare_training_inputs
    monkeypatch.setattr(pr.PlayersInjuriesV1, "prepare_training_inputs",
                        lambda self, c, now: trained_with.append(now) or real_prepare(self, c, now))
    test_a_new_week_with_new_results_is_trained_and_published_for_nba(monkeypatch, tmp_path)
    assert "2026-10-21T20:00:00Z" in trained_with
    conn = db.connect(tmp_path / "nba.sqlite")
    name = f"{pr.ADAPTIVE_MEMBER}@adaptive"
    fit = adaptive.latest_fit(conn, "nba", "member", name, adaptive.definition(conn))
    assert fit is not None and fit["status"] == "success"
    detail = json.loads(fit["detail"])
    assert detail["inputs"]["box_seasons"] == sorted(by_season) and detail["inputs"]["reports"] == 3
    assert detail["inputs"]["players"]["rating_updates"] > 0 and detail["inputs"]["facts"]["usable"] >= 1
    art = adaptive.load_fit(fit)
    state = pickle.loads(art["state"])
    players = state["inner"]["players"]
    assert isinstance(players, pr.LiveState) and players._state is None
    # Delivered = applied (review finding B3): g1 (2026-27) is final but the cache has no box rows for it, so it is counted
    # in result_without_box and absent from the delivered index, which otherwise equals the wrapper's delivered results.
    coverage = detail["inputs"]["players"]
    assert coverage["result_without_box"] == 1
    assert len(players.delivered) == coverage["rating_updates"] == fit["training_games"] - 1
    assert Path(fit["artifact_path"]).stat().st_size < 300_000
    comb = adaptive.latest_fit(conn, "nba", "combiner", "combiner", adaptive.definition(conn))
    assert name in json.loads(comb["detail"])["members_available"]
    rows = conn.execute("SELECT members, missing FROM adaptive_forecasts WHERE market='winner' ORDER BY forecast_id").fetchall()
    assert rows and all(name in {m["name"] for m in json.loads(r["members"])} for r in rows), [r["missing"] for r in rows]
    conn.close()


def test_nba_player_injury_member_abstains_on_stale_or_changed_player_box_inputs(monkeypatch, tmp_path):
    """Review findings B2/B1 and recheck C2/C3 (2026-10-13). g1 (2026-27) was final at training with no player-box rows, so
    the fit was offered it and skipped it. Recording g2: the member is missing as stale (B2). The cache then gets g1's rows:
    a fresh walk would now apply g1, so the member is missing with 'inputs changed' (C2) — and the run is DUE (C3), naming
    the player members; after the retrain the member forecasts. Rows for a game the fit was never offered (a later game)
    are growth and never block (2026-10-13 fix of the whole-file digest)."""
    import csv
    from app import db, player_ratings as pr
    from test_player_ratings import _write_box
    from test_shadow import CSV_HEADER
    test_nba_player_injury_member_trains_records_and_keeps_its_artifact_small(monkeypatch, tmp_path)
    conn = db.connect(tmp_path / "nba.sqlite")
    name = f"{pr.ADAPTIVE_MEMBER}@adaptive"
    fit = adaptive.latest_fit(conn, "nba", "member", name, adaptive.definition(conn))
    recorded = json.loads(fit["detail"])["inputs"]
    assert recorded["box_digest"] and recorded["box_seasons"] and recorded["players"]["result_without_box"] == 1
    # The live possession box for g1 arrives (as the ESPN summaries do): the other box-dependent members stay current.
    g1_box = (CSV_HEADER + "g1,2027,2,1,home,104,88,22,10,33,13\ng1,2027,2,2,away,99,88,22,10,33,12\n").encode()
    assert ingest.ingest_team_box(conn, 2027, fetch=lambda _s: g1_box, retrieved_at="2026-10-22T05:00:00Z")["recorded"] == 2
    clock = _test_clock("2026-10-28T21:00:00Z", "2026-10-28T23:00:00Z")
    monkeypatch.setattr(adaptive, "utc_now", clock)
    r = adaptive.record(conn, game_ids=["espn:g2"], now="2026-10-28T21:40:00Z")      # g2 tips off 23:30Z; g1 is final
    w = next(f for f in r["forecasts"] if f["market"] == "winner")
    assert "efficiency_margin" in w["members"], w["missing"]        # a possession-box member forecasts: that box is current
    assert name in w["missing"] and "player box" in w["missing"][name] and "stale" in w["missing"][name], w["missing"]
    assert name not in w["members"]
    rdir = tmp_path / "research" / "availability"
    _write_box(rdir, 2027, [("g1", "2026-10-21", {"espn:1": [("1p0", 240)], "espn:2": [("2p0", 240)]})])   # rows for g1 appear
    pr._INPUTS.clear()
    r = adaptive.record(conn, game_ids=["espn:g2"], now="2026-10-28T21:50:00Z")
    w = next(f for f in r["forecasts"] if f["market"] == "winner")
    assert name in w["missing"] and "inputs changed since the fit" in w["missing"][name], w["missing"]   # C2: blocked
    d = adaptive.due(conn, "2026-10-28T21:50:00Z")
    assert d["due"] is True and name in d["reason"], d                                                # C3: retrain due
    clock.at("2026-10-28T21:00:00Z", "2026-10-28T23:00:00Z")
    t = adaptive.train(conn, now="2026-10-28T21:20:00Z")
    assert t["members"][name]["status"] == "success", t["members"][name]
    assert adaptive.due(conn, "2026-10-28T21:50:00Z")["due"] is False
    pr._INPUTS.clear()
    r = adaptive.record(conn, game_ids=["espn:g2"], now="2026-10-28T21:55:00Z")
    w = next(f for f in r["forecasts"] if f["market"] == "winner")
    assert name not in w["missing"], w["missing"].get(name)                                           # forecasts again
    path = rdir / "player_box" / "player_box_2027.csv"
    rows = list(csv.DictReader(path.open()))
    rows += [{**rows[0], "game_id": "g9", "game_date_time": "2026-10-30T19:30:00-04:00"}]            # a game never offered
    with path.open("w", newline="") as f:
        out = csv.DictWriter(f, fieldnames=list(rows[0]))
        out.writeheader()
        out.writerows(rows)
    pr._INPUTS.clear()
    r = adaptive.record(conn, game_ids=["espn:g2"], now="2026-10-28T22:00:00Z")
    w = next(f for f in r["forecasts"] if f["market"] == "winner")
    assert name not in w["missing"], w["missing"].get(name)                                           # growth never blocks
    conn.close()


def test_score_member_without_distribution_never_contributes_a_fake_winner():
    from app.models.base import PregameGame
    from app import backtest
    class Unready:
        name = 'unready_score'
        def predict(self, game): return .5
        def distribution(self, game): return None
        def update(self, result): pass
    g = PregameGame('test:missing', 2024, 'regular', '2024-01-01T23:00:00Z', 'h', 'a', False)
    row = backtest.Row(g, '2024-01-01T21:00:00Z', 3, 2, '2024-01-02T03:00:00Z')
    member = adaptive.Member('unready_score', 'walk', Unready(), markets_=('winner', 'total'))
    hist = adaptive.walk_member(member, [row], [], None, {2024}, {g.game_id: {'total': 5.5}})
    assert hist[g.game_id] == {}


@pytest.mark.parametrize('vector', [
    (float('nan'), 0., .5), (float('inf'), 0., 0.), (-.1, 0., 1.1),
    (.8, .4, -.2), (.2, .2, .2), (.5, 0., float('nan')),
])
def test_member_probability_vectors_reject_invalid_outputs(vector):
    with pytest.raises(ValueError, match='probability vector'):
        adaptive._vec(vector)


def test_member_probability_vector_rounding_preserves_nonnegative_unit_sum():
    vector = adaptive._vec((.5000000006, .4999999994, 0.))
    assert all(0 <= p <= 1 for p in vector)
    assert sum(vector) == pytest.approx(1.)
    assert adaptive._vec((.2, .3, .50000000001)) == (.2, .3, .5)


@pytest.mark.parametrize('kind', ['walk', 'candidate'])
def test_failed_score_member_does_not_leave_partial_market_votes(store, monkeypatch, kind):
    adaptive.train(store, now=TRAIN_AT)
    run(store, CYCLE_AT)
    if kind == 'walk':
        original = adaptive.markets.ScoreGrid.market
        def fail_total(self, market, line=None):
            if market == 'total':
                raise ValueError('synthetic broken total')
            return original(self, market, line)
        monkeypatch.setattr(adaptive.markets.ScoreGrid, 'market', fail_total)
        name = 'goals_poisson@market'
    else:
        original = engine.vector
        def fail_total(output, market, line=None):
            if market == 'total':
                raise ValueError('synthetic broken total')
            return original(output, market, line)
        monkeypatch.setattr(engine, 'vector', fail_total)
        name = 'cand:poisson_draw:nhl_base@adaptive'
    result = adaptive.record(store, game_ids=[GID], now=CYCLE_AT)
    if kind == 'walk':
        assert name in result['errors']
    assert any(name in f['missing'] for f in result['forecasts'])
    # Earlier successful markets must not conceal the subsequent member failure.
    for forecast in result['forecasts']:
        assert name not in forecast['members']


@pytest.mark.parametrize('with_candidates', [True, False])
def test_feature_walk_failure_blocks_box_dependent_walk_members(store, monkeypatch, with_candidates):
    # Use the fixture's fitted walk member to exercise the box-dependency gate.
    monkeypatch.setattr(adaptive, 'BOX_DEPENDENT', {**adaptive.BOX_DEPENDENT, 'nhl': ('hockey_logistic',)})
    if not with_candidates:
        monkeypatch.setattr(registry, 'SPECS', ())
    adaptive.train(store, now=TRAIN_AT)
    run(store, CYCLE_AT)
    def broken(*args, **kwargs):
        raise ValueError('synthetic feature walk failure')
    monkeypatch.setattr(adaptive.pool_live, '_recorder', broken)
    result = adaptive.record(store, game_ids=[GID], now=CYCLE_AT)
    assert 'feature_walk' in result['errors']
    winner = next(f for f in result['forecasts'] if f['market'] == 'winner')
    assert 'hockey_logistic@adaptive' not in winner['members']
    assert 'feature walk failed' in winner['missing']['hockey_logistic@adaptive']


def test_the_training_time_forecast_of_the_next_game_is_not_frozen_into_the_artifact(store):
    """train() predicts the next game at the training cutoff (to trigger the scheduled refit). Score members cache that
    game's features; frozen into the artifact, the cache was served after newer results had been delivered (a train/serve
    difference: the walk computes each game's features at its own cutoff). Reproduction from the 2026-10-12 audit."""
    from app import live
    add_upcoming(store, gid=2026020000, start="2026-10-12T14:00:00", state=3, home=8, away=16, at="2026-10-12T20:30:00Z")
    adaptive.train(store, now=TRAIN_AT)
    # the game in progress at training finishes before the cycle
    add_upcoming(store, gid=2026020000, start="2026-10-12T14:00:00", state=7, hs=9, vs=0, period=3, home=8, away=16,
                 at="2026-10-12T21:00:00Z")
    defn = adaptive.definition(store)
    m = next(x for x in adaptive.members(store)[0] if x.name == "goals_draw@adaptive")
    arts, _ = adaptive._load_member_artifacts(store, "nhl", defn, [m], CYCLE_AT)
    row, art = arts[m.name]
    w = adaptive.thaw(m.obj, art["state"])
    w.frozen = True
    assert "nhl:2026020001" not in w.member.pending
    rows = adaptive.available_rows(store, CYCLE_AT)
    assert adaptive._advance(w, art, rows, [], {r.game.game_id: r for r in rows}, CYCLE_AT) == 1
    nxt = store.execute("SELECT * FROM games WHERE game_id='nhl:2026020001'").fetchone()
    game = replace(live.engine(store).pregame(nxt), cutoff_at=CYCLE_AT)
    served = w.distribution(game).market("total", 6.5)
    w.member.pending.pop(game.game_id, None)
    assert served == w.distribution(game).market("total", 6.5)


def test_a_late_box_of_a_delivered_game_reaches_the_frozen_state_when_a_fresh_walk_would_deliver_it():
    """Serving advances a frozen state through the results and box scores that became available after its cutoff. A box
    of a game whose result the fit already consumed was delivered before every newer result and forecast, so a game whose
    cutoff (19:30) precedes the box (19:31) was forecast with it; a fresh walk forecasts it without, and its residual (and
    the next season's sigma) differed. Reduction of the review's seed-39 property example (2026-10-16)."""
    from app import backtest
    from app.models.efficiency import EfficiencyMargin
    from test_efficiency import box, game, row, season_rows
    history = season_rows(20)
    a = row(game("A", "2021-11-21T23:30:00Z", "T1", "T2"), 130, 90)
    b = backtest.Row(game("B", "2021-11-22T21:30:00Z", "T1", "T3"), "2021-11-22T19:30:00Z", 100, 100,
                     "2021-11-23T01:30:00Z")
    cut, now = a.result_available_at, "2021-11-23T12:00:00Z"
    late_box = box(a, 99, available_at="2021-11-22T19:31:00Z")
    events = [box(r, i) for i, r in enumerate(history)]
    frozen = EfficiencyMargin()
    backtest.run(frozen, history + [a], events, deliver_until=cut)
    rows = history + [a, b]
    asked, predict = [], frozen.predict
    frozen.predict = lambda g: asked.append(g.game_id) or predict(g)
    assert adaptive._advance(frozen, {"cutoff": cut}, rows, events + [late_box], {r.game.game_id: r for r in rows}, now) == 1
    del frozen.predict
    assert asked == [b.game.game_id]            # A rides along only for its box: its result and forecast were consumed
    fresh = EfficiencyMargin()
    backtest.run(fresh, rows, events + [late_box], deliver_until=now)
    assert frozen.eff.lines == fresh.eff.lines
    assert [x for r in frozen.residuals for x in r] == pytest.approx([x for r in fresh.residuals for x in r], abs=1e-12)
    nxt = game("C", "2021-11-24T23:30:00Z", "T1", "T3", season=2023)
    assert frozen.predict(nxt) == pytest.approx(fresh.predict(nxt), abs=1e-12)


@pytest.mark.parametrize("change", ["corrected", "late, stuck", "late after postponed"])
def test_results_that_changed_before_the_fit_cutoff_make_a_walk_member_missing_until_a_retrain(store, change):
    """A walk member's frozen state consumed exactly the results available at its cutoff. A score corrected after it
    (ingest moves result_available_at to the correction time) was re-delivered on top of the old score; a final ingested
    late with an assumed availability before the cutoff was never delivered. Neither can be replayed into the frozen
    state in order, so the member abstains with the reason until the next fit (2026-10-12 audit reproduction).
    A game still marked scheduled or in progress more than `ingest.OBSERVED_WINDOW` after its start (here months) is
    stuck, not in flight (`adaptive._in_flight`): online members are not frozen before it, and its late result makes
    every walk member abstain, as one marked postponed at training (in flight: the next test)."""
    g = store.execute("SELECT * FROM games WHERE status='final' AND result_available_at < ? "
                      "ORDER BY result_available_at DESC LIMIT 1", (TRAIN_AT,)).fetchone()
    assert adaptive._parse(TRAIN_AT) - adaptive._parse(g["tipoff_at"]) > ingest.OBSERVED_WINDOW
    if change != "corrected":
        status = "scheduled" if change == "late, stuck" else "postponed"
        store.execute("UPDATE games SET status=?, home_score=NULL, away_score=NULL, result_available_at=NULL, "
                      "result_availability_basis=NULL WHERE game_id=?", (status, g["game_id"]))
        store.commit()
    trained = adaptive.train(store, now=TRAIN_AT)
    frozen = trained["members"]["hockey_elo"].get("frozen_at")
    assert frozen is None or frozen >= adaptive.forecast_cutoff(g["tipoff_at"]), frozen
    assert "frozen_at" not in trained["members"]["hockey_logistic@adaptive"]
    run(store, CYCLE_AT)
    clean = adaptive.record(store, game_ids=[GID], now=CYCLE_AT, write=False)
    assert "hockey_elo" in next(f for f in clean["forecasts"] if f["market"] == "winner")["members"]
    if change == "corrected":
        store.execute("UPDATE games SET home_score=?, away_score=?, result_available_at=?, "
                      "result_availability_basis='observed_retrieval' WHERE game_id=?",
                      (g["away_score"], g["home_score"], "2026-10-12T21:00:00Z", g["game_id"]))
    else:
        store.execute("UPDATE games SET status='final', home_score=?, away_score=?, result_available_at=?, "
                      "result_availability_basis='assumed_tipoff_plus_4h' WHERE game_id=?",
                      (g["home_score"], g["away_score"], g["result_available_at"], g["game_id"]))
    store.commit()
    r = adaptive.record(store, game_ids=[GID], now=CYCLE_AT, write=False)
    w = next(f for f in r["forecasts"] if f["market"] == "winner")
    assert "results changed since the fit" in w["missing"]["hockey_logistic@adaptive"], w["missing"]
    assert "hockey_elo" not in w["members"]
    assert "results changed since the fit" in w["missing"]["hockey_elo"], w["missing"]


def test_a_game_in_progress_at_training_freezes_online_members_before_it_and_its_result_is_replayed(store):
    """A game whose forecast cutoff passed before the fit and whose result arrives after it is in flight: online members
    are frozen before its cutoff (`adaptive.frozen_at`) and serving replays it in walk order, so they keep forecasting
    after its result; learned members are frozen at the cutoff and take it as a new result (the other side of the
    stuck-game bound in `adaptive._in_flight`)."""
    gid = 2026020003
    add_upcoming(store, gid=gid, start="2026-10-12T14:00:00", state=3, hs=1, vs=0, home=8, away=16,
                 at="2026-10-12T19:30:00Z")
    g = store.execute("SELECT * FROM games WHERE game_id=?", (f"nhl:{gid}",)).fetchone()
    assert g["status"] == "in_progress" and adaptive.forecast_cutoff(g["tipoff_at"]) < TRAIN_AT
    trained = adaptive.train(store, now=TRAIN_AT)
    frozen = trained["members"]["hockey_elo"].get("frozen_at")
    assert frozen is not None and frozen < adaptive.forecast_cutoff(g["tipoff_at"]), frozen
    assert "frozen_at" not in trained["members"]["hockey_logistic@adaptive"]
    add_upcoming(store, gid=gid, start="2026-10-12T14:00:00", state=7, hs=3, vs=1, period=3, home=8, away=16,
                 at="2026-10-12T21:00:00Z")
    assert store.execute("SELECT status FROM games WHERE game_id=?", (f"nhl:{gid}",)).fetchone()[0] == "final"
    run(store, CYCLE_AT)
    r = adaptive.record(store, game_ids=[GID], now=CYCLE_AT, write=False)
    w = next(f for f in r["forecasts"] if f["market"] == "winner")
    assert "hockey_elo" not in w["missing"] and "hockey_logistic@adaptive" not in w["missing"], w["missing"]


def test_a_stuck_game_whose_result_arrives_after_the_fit_is_replayed_exactly_for_online_members_without_forecast_state(store):
    """A game still scheduled more than `ingest.OBSERVED_WINDOW` after its start is not in flight (`adaptive._in_flight`),
    so online members are frozen after its cutoff. Tracked live, its result is stamped when learned (after the fit) and
    serving replays it at its cutoff with the fit's state: exact for members whose forecast is not part of their state
    (hockey_elo keeps serving; the pinned serving cases compare with a fresh walk and cover efficiency_margin, which is
    missing until the next fit, `adaptive._landed_in_flight`); learned members take it as a new result."""
    gid = 2026020004
    add_upcoming(store, gid=gid, start="2026-10-11T10:00:00", home=8, away=16, at="2026-10-11T04:00:00Z")
    g = store.execute("SELECT * FROM games WHERE game_id=?", (f"nhl:{gid}",)).fetchone()
    assert g["status"] == "scheduled" and adaptive._parse(TRAIN_AT) - adaptive._parse(g["tipoff_at"]) > ingest.OBSERVED_WINDOW
    trained = adaptive.train(store, now=TRAIN_AT)
    assert "frozen_at" not in trained["members"]["hockey_elo"]
    add_upcoming(store, gid=gid, start="2026-10-11T10:00:00", state=7, hs=3, vs=1, period=3, home=8, away=16,
                 at="2026-10-12T21:00:00Z")
    g = store.execute("SELECT * FROM games WHERE game_id=?", (f"nhl:{gid}",)).fetchone()
    assert (g["status"], g["result_available_at"]) == ("final", "2026-10-12T21:00:00Z")
    run(store, CYCLE_AT)
    r = adaptive.record(store, game_ids=[GID], now=CYCLE_AT, write=False)
    w = next(f for f in r["forecasts"] if f["market"] == "winner")
    assert "hockey_elo" not in w["missing"] and "hockey_logistic@adaptive" not in w["missing"], w["missing"]


@pytest.mark.parametrize("new_season", [False, True])
def test_a_landed_stuck_game_that_starts_a_season_ahead_of_an_old_season_result_changes_production_elo(new_season):
    """Why `_landed_in_flight` also guards a season start: production Elo regresses ratings toward the mean at its first
    forecast of a season (carry 0.75). A fresh walk forecasts the stuck game S at its cutoff, before the old season's
    result O (available later), so O is applied after the regression; replaying S after the fit applied O before it.
    Same season: the replay is exact and the member keeps serving (review of 2026-10-17: the property worlds' Elo has
    carry 1.0, where a season roll changes nothing)."""
    from app import backtest
    from app.models.elo import Elo
    from test_efficiency import game, season_rows
    history = season_rows(20)
    o = backtest.Row(game("O", "2021-11-22T00:00:00Z", "T1", "T2"), "2021-11-21T22:00:00Z", 120, 90, "2021-11-22T04:00:00Z")
    s = backtest.Row(game("S", "2021-11-22T01:00:00Z", "T3", "T1", season=2023 if new_season else 2022),
                     "2021-11-21T23:00:00Z", 100, 95, "2021-11-23T12:00:00Z")
    cut, now = "2021-11-23T09:00:00Z", "2021-11-23T13:00:00Z"            # S still unfinished 32 h after its start at the fit
    rows = history + [o, s]
    frozen = Elo()
    backtest.run(frozen, history + [o], [], deliver_until=cut)            # S is stuck, not in flight: not waited for
    reason = adaptive._landed_in_flight(frozen, {"cutoff": cut}, rows)
    assert adaptive._advance(frozen, {"cutoff": cut}, rows, [], {r.game.game_id: r for r in rows}, now) == 1
    fresh = Elo()
    backtest.run(fresh, rows, [], deliver_until=now)
    exact = all(frozen.ratings[t] == pytest.approx(fresh.ratings[t], abs=1e-9) for t in fresh.ratings)
    assert exact == (not new_season) and (reason is not None) == new_season, (reason, dict(frozen.ratings), dict(fresh.ratings))


def test_one_markets_combiner_exception_leaves_the_other_markets_fitted_and_the_market_on_its_earlier_fit(store, monkeypatch):
    """Fault injection (no natural trigger was found): an exception while fitting one market's combination failed the
    whole combiner fit, so every market stayed on the previous fit (and, when deterministic, on every retry)."""
    adaptive.train(store, now=TRAIN_AT)
    first = adaptive.latest_fit(store, "nhl", "combiner", "combiner", adaptive.definition(store))
    _settle(store)
    add_upcoming(store, gid=2026020002, start="2026-10-20T19:00:00", at="2026-10-13T03:00:00Z")
    real = adaptive.fit_combiner

    def broken_total(rows, candidates, market, cutoff, fallbacks=True, groups=()):
        if market == "total":
            raise FloatingPointError("synthetic failure in one market")
        return real(rows, candidates, market, cutoff, fallbacks, groups)
    monkeypatch.setattr(adaptive, "fit_combiner", broken_total)
    t = adaptive.train(store, now="2026-10-13T04:00:00Z")
    assert t["combiner"]["status"] == "partial", t["combiner"]
    assert "total" in t["combiner"]["partial_reason"] and "FloatingPointError" in t["combiner"]["partial_reason"]
    second = adaptive.latest_fit(store, "nhl", "combiner", "combiner", adaptive.definition(store))
    assert second["fit_id"] != first["fit_id"] and second["status"] == "partial"
    per, reason = adaptive.combiners_in_force(store, "nhl", adaptive.definition(store), "2026-10-13T05:00:00Z")
    assert reason is None and per["winner"][0]["fit_id"] == second["fit_id"] and per["total"][0]["fit_id"] == first["fit_id"]
    assert adaptive.due(store, "2026-10-13T05:00:00Z")["due"] is True                 # the market is retried


def test_one_markets_combination_exception_does_not_stop_the_other_markets_forecasts(store, monkeypatch):
    """Fault injection: an exception while combining one market's member vectors raised out of the whole forecast cycle,
    so no market of any game was recorded."""
    adaptive.train(store, now=TRAIN_AT)
    run(store, CYCLE_AT)
    real = adaptive.combine_now

    def broken_total(cm, have):
        if cm is not None and cm.get("market") == "total":
            raise ValueError("synthetic failure in one market")
        return real(cm, have)
    monkeypatch.setattr(adaptive, "combine_now", broken_total)
    r = adaptive.record(store, game_ids=[GID], now=CYCLE_AT)
    assert r["status"] == "partial", r.get("error")
    assert "total" in r["errors"] and "ValueError" in r["errors"]["total"]
    recorded = {x["market"]: x["status"] for x in store.execute("SELECT market, status FROM adaptive_forecasts")}
    assert recorded.get("winner") == "forecast" and "total" not in recorded


def test_a_result_committed_after_the_training_snapshot_is_delivered_not_a_reason_to_abstain(store, monkeypatch):
    """The scheduled run stamped its training cutoff after the snapshot was taken and the store lock released. A job that
    took the lock in between committed a result observed before that cutoff: absent from the snapshot, so the digest
    guard (correctly) marked every walk member missing until the next fit. The cutoff is now taken under the lock, before
    the snapshot, so that result is after the cutoff and is delivered at serving."""
    add_upcoming(store, gid=2026020000, start="2026-10-12T14:00:00", state=3, home=8, away=16, at="2026-10-12T19:30:00Z")
    real = adaptive.snapshot

    def then_a_result_arrives(conn, sport):
        path = real(conn, sport)
        # the next job on the store: retrieves the final (stamped with its own retrieval time) and commits it
        add_upcoming(store, gid=2026020000, start="2026-10-12T14:00:00", state=7, hs=9, vs=0, period=3, home=8, away=16,
                     at=adaptive.utc_now())
        return path
    monkeypatch.setattr(adaptive, "snapshot", then_a_result_arrives)
    res = adaptive.run_scheduled("nhl")
    assert res["status"] in ("success", "partial"), res
    run(store, CYCLE_AT)
    r = adaptive.record(store, game_ids=[GID], now=CYCLE_AT, write=False)
    w = next(f for f in r["forecasts"] if f["market"] == "winner")
    # its vector is produced (members without one are listed in `missing`; the combination itself may still abstain
    # because the new final has no shot data yet, a separate staleness guard)
    assert "hockey_elo" not in w["missing"], w["missing"]["hockey_elo"]


def test_a_backfilled_shot_import_dated_before_the_fit_cutoff_makes_the_shot_member_missing_until_a_retrain(store):
    """Historical backfills of box/shot data are stamped with the game's result time (nhl_shots.import_game: max(result,
    start); ingest._record_observation for NBA boxes). One imported after training but dated before the training cutoff
    was skipped at serving (`_advance` treats it as already delivered) while a fresh walk uses it; nothing detected it."""
    from app import nhl_shots
    from test_nhl_xg import fake_fetch
    adaptive.train(store, now=TRAIN_AT)
    run(store, CYCLE_AT)
    xg = "hockey_logistic_xg@adaptive"
    clean = adaptive.record(store, game_ids=[GID], now=CYCLE_AT, write=False)
    assert "box/shot inputs changed" not in next(f for f in clean["forecasts"] if f["market"] == "winner")["missing"].get(xg, "")
    g = store.execute("SELECT game_id, tipoff_at, result_available_at FROM games WHERE status='final' AND result_available_at < ? "
                      "ORDER BY result_available_at DESC LIMIT 1", (TRAIN_AT,)).fetchone()
    imp = nhl_shots.import_game(store, g, fake_fetch(store)(g["game_id"]), "2026-10-12T21:00:00Z")   # the backfill lands
    assert imp["complete"] and imp["policy"] == "historical_backfill"
    assert store.execute("SELECT available_at FROM nhl_pbp_imports").fetchone()[0] < TRAIN_AT          # dated before the fit
    r = adaptive.record(store, game_ids=[GID], now=CYCLE_AT, write=False)
    w = next(f for f in r["forecasts"] if f["market"] == "winner")
    assert "box/shot inputs changed since the fit" in w["missing"].get(xg, ""), w["missing"].get(xg)
    assert "hockey_elo" not in w["missing"]                                    # members without these inputs are unaffected


def test_a_previous_season_result_arriving_after_a_fit_that_started_the_new_season_keeps_online_members_exact_and_learned_ones_missing(store):
    """The training-time forecast of the next game (season 2027) ran each member's season start (carry, season-start
    refit) while the last 2026 game was still in progress. Its final arrives after the fit: a fresh walk delivers it before
    the season starts, the frozen state only after, so the served forecast differed from the fresh walk (found by
    test_serving_properties: NBA points_normal -0.028, efficiency_margin +0.001; Elo too with the production carry 0.75).
    Online members are now frozen as walked and start the season in walk order at serving; a learned member, whose
    season-start refit is the fit in force, is missing and the run is due; the retrain consumes the final first."""
    add_upcoming(store, gid=2026021999, start="2026-10-12T14:00:00", state=3, at="2026-10-12T19:30:00Z", season=2026)
    adaptive.train(store, now=TRAIN_AT)
    run(store, CYCLE_AT)
    assert adaptive.due(store, CYCLE_AT)["due"] is False
    add_upcoming(store, gid=2026021999, start="2026-10-12T14:00:00", state=7, hs=3, vs=1, period=3, at="2026-10-12T20:45:00Z",
                 season=2026)                                                               # the 2026 final, after the fit
    learned = "hockey_logistic@adaptive"
    r = adaptive.record(store, game_ids=[GID], now=CYCLE_AT, write=False)
    w = next(f for f in r["forecasts"] if f["market"] == "winner")
    assert "season 2026 arrived after the fit had started season 2027" in w["missing"].get(learned, ""), w["missing"]
    assert "hockey_elo" not in w["missing"], w["missing"]["hockey_elo"]
    d = adaptive.due(store, CYCLE_AT)
    assert d["due"] is True and learned in d["reason"] and "hockey_elo" not in d["reason"]
    adaptive.train(store, now="2026-10-12T20:50:00Z")                      # the scheduled retrain the due state triggers
    assert adaptive.due(store, CYCLE_AT)["due"] is False
    again = adaptive.record(store, game_ids=[GID], now=CYCLE_AT, write=False)
    w = next(f for f in again["forecasts"] if f["market"] == "winner")
    assert learned not in w["missing"], w["missing"].get(learned)


@pytest.mark.parametrize("change", ["corrected_old_score", "changed_decision"])
def test_a_changed_input_of_the_fit_in_force_schedules_a_retrain_that_restores_the_members(store, change):
    """The serving guard correctly abstains when results the frozen state consumed changed, but `due` stayed False until the
    next period (weekly: up to ~7 days of every market unavailable). Now the change makes the run due; the retrain's fit
    consumes the corrected inputs and the members forecast again (reviewer reproductions R1 / R1b, 2026-10-13)."""
    adaptive.train(store, now=TRAIN_AT)
    run(store, CYCLE_AT)
    assert adaptive.due(store, CYCLE_AT)["due"] is False
    if change == "corrected_old_score":
        g = store.execute("SELECT * FROM games WHERE status='final' AND result_available_at < ? ORDER BY result_available_at "
                          "LIMIT 1", (TRAIN_AT,)).fetchone()                                        # a first-season game
        store.execute("UPDATE games SET home_score=home_score+1, result_available_at=?, result_availability_basis='observed_retrieval' "
                      "WHERE game_id=?", ("2026-10-12T20:45:00Z", g["game_id"]))
    else:   # the source changes a decision in place (game_decisions has no availability time); the NHL digest includes it
        g = store.execute("SELECT g.game_id, d.decision FROM games g JOIN game_decisions d USING(game_id) WHERE g.status='final' "
                          "AND g.result_available_at < ? ORDER BY g.result_available_at DESC LIMIT 1", (TRAIN_AT,)).fetchone()
        store.execute("UPDATE game_decisions SET decision=? WHERE game_id=?", ("OT" if g["decision"] != "OT" else "REG", g["game_id"]))
    store.commit()
    stale = adaptive.record(store, game_ids=[GID], now=CYCLE_AT, write=False)
    assert "results changed since the fit" in next(f for f in stale["forecasts"] if f["market"] == "winner")["missing"]["hockey_elo"]
    d = adaptive.due(store, CYCLE_AT)
    assert d["due"] is True and "hockey_elo" in d["reason"] and "changed since it was trained" in d["reason"]
    adaptive.train(store, now="2026-10-12T20:50:00Z")                      # the scheduled retrain the due state triggers
    assert adaptive.due(store, CYCLE_AT)["due"] is False
    again = adaptive.record(store, game_ids=[GID], now=CYCLE_AT, write=False)
    w = next(f for f in again["forecasts"] if f["market"] == "winner")
    assert "hockey_elo" not in w["missing"], w["missing"].get("hockey_elo")


def test_nba_box_events_reach_only_members_that_consume_them():
    """A backfilled NBA box changes the events digest only of members that read boxes; Elo-style members never see them."""
    from types import SimpleNamespace
    eng = SimpleNamespace(sport="nba")
    events = ["box"]

    class NoBox:
        pass

    class Box:
        def update_box(self, e):
            pass
    assert adaptive._events_for(NoBox(), eng, events) == []
    assert adaptive._events_for(Box(), eng, events) == events
    wrapped = lambda m: SimpleNamespace(member=m, update_box=lambda e: None)      # noqa: E731  (as the Adaptive wrapper)
    assert adaptive._events_for(wrapped(NoBox()), eng, events) == []           # the wrapper passes boxes through only
    assert adaptive._events_for(wrapped(Box()), eng, events) == events


def test_a_market_on_an_earlier_combination_applies_the_newest_daily_rule_assessment(tmp_path, monkeypatch):
    """Reviewer reproduction (2026-10-13): the newest daily fit had no stack for the market (too few members, not an error),
    so the market kept the earlier fit's combination — and, wrongly, the earlier fit's daily rule instead of the newest
    assessment."""
    import sqlite3
    from pathlib import Path
    schema = (Path(adaptive.__file__).parent / "migrations" / "0024_adaptive.sql").read_text()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create = schema[schema.index("CREATE TABLE adaptive_fits"):]
    conn.execute(create[:create.index(");") + 2].replace("REFERENCES model_fits(fit_id)", ""))
    clock = iter(["2026-10-11T10:00:00Z", "2026-10-12T10:00:00Z"])
    monkeypatch.setattr(adaptive, "utc_now", lambda: next(clock))
    old_rule = {"members": ["a", "b"], "thresholds": [0.5, 0.5], "rule": "OLD"}
    new_rule = {"members": ["c", "d"], "thresholds": [0.6, 0.6], "rule": "NEW"}
    arts = [{"markets": {"winner": {"stack": {"weights": [1]}}}, "daily_agreement": {"winner": {"status": "selected", "rule": old_rule}}},
            {"markets": {"winner": {"stack": None, "reason": "only 1 members with 400 common out-of-sample rows"}},
             "daily_agreement": {"winner": {"status": "selected", "rule": new_rule}}}]
    for i, art in enumerate(arts):
        p, sha = adaptive._save(tmp_path / f"c{i}.pkl", art)
        adaptive._insert_fit(conn, "nba", "combiner", "combiner", "D", f"p{i}", "partial" if i else "success",
                             "2026-10-1%dT09:00:00Z" % (1 + i), {}, cutoff="2026-10-1%dT09:00:00Z" % (1 + i), path=str(p), sha=sha, fp=str(i))
    in_force, err = adaptive.combiners_in_force(conn, "nba", "D", "2026-10-13T00:00:00Z")
    row, art = in_force["winner"]
    assert err is None and art["markets"]["winner"]["stack"] == {"weights": [1]}          # the earlier combination ...
    assert art["daily_agreement"]["winner"]["rule"]["rule"] == "NEW"                       # ... with the newest rule
    assert art["daily_agreement_fit_id"] != row["fit_id"]


def test_a_failed_daily_rule_assessment_keeps_the_markets_combination_and_carries_the_earlier_rule(store, monkeypatch):
    """Reviewer suspicion (2026-10-13), reproduced: the daily rule assessment shared the market's guard with the combiner fit,
    so an assessment error dropped the market's combination too."""
    from app import daily_agreement
    with adaptive.cadence("daily"):
        adaptive.train(store, now=TRAIN_AT)
        real = daily_agreement.assess

        def broken(market, rows, cutoff, incumbent=None):
            if market == "total":
                raise ValueError("synthetic assessment failure")
            return real(market, rows, cutoff, incumbent)
        monkeypatch.setattr(daily_agreement, "assess", broken)
        t = adaptive.train(store, now="2026-10-12T20:45:00Z", force=True)
    assert t["combiner"]["status"] == "partial" and "daily rule total assessment failed" in t["combiner"]["partial_reason"]
    assert t["combiner"]["markets"]["total"]["weights"]                       # the combination itself was still fitted


def test_one_invalid_historical_vector_is_left_out_not_the_members_whole_history(store, monkeypatch):
    """Reviewer suspicion (2026-10-13), reproduced by injection: `_vec` raising on one historical game failed the member's
    entire training walk (the member then had no fit at all)."""
    real = adaptive._vec
    calls = {"n": 0}

    def once_invalid(v):
        calls["n"] += 1
        if calls["n"] == 50:
            raise ValueError("invalid probability vector: synthetic")
        return real(v)
    monkeypatch.setattr(adaptive, "_vec", once_invalid)
    t = adaptive.train(store, now=TRAIN_AT)
    bad = [n for n, m in t["members"].items() if m.get("invalid_vectors")]
    assert len(bad) == 1 and t["members"][bad[0]]["status"] == "success"
    assert t["members"][bad[0]]["invalid_vectors"]["count"] == 1
    assert "synthetic" in t["members"][bad[0]]["invalid_vectors"]["first"][0][2]


def test_scheduled_training_refuses_a_data_directory_missing_cached_source_files_before_any_snapshot_or_fit(store, monkeypatch, capsys):
    """Guard for retrain attempt 1 (2026-10-14): the training data directory held the stores but not their raw response cache,
    so the candidate feature walk failed with FileNotFoundError after the walk members had already run, and the combiner was
    published as partial. A store that references a cached response whose file is absent is refused by `adaptive-train`
    before any training starts (training is stubbed here: this pins the guard, it does not reproduce the walk failure).
    The guard lives in the command, not in adaptive.py, whose bytes are part of the adaptive definition."""
    from app import cli, config, raw_cache
    raw_id = raw_cache.store(store, source="nhl_api_web", endpoint="test", request_key="completeness-probe", body=b'{"probe": 1}',
                             retrieved_at="2026-10-01T00:00:00Z", importer_version="test")
    store.commit()
    rel = store.execute("SELECT path FROM raw_responses WHERE raw_id=?", (raw_id,)).fetchone()["path"]
    (config.raw_dir() / rel).unlink()
    defn = adaptive.definition(store)
    called = []

    def never(step):
        def stub(*a, **k):
            called.append(step)
            raise AssertionError(f"{step} started although cached source files are missing")
        return stub
    for step in ("run_scheduled", "snapshot", "train"):
        monkeypatch.setattr(adaptive, step, never(step))
    for argv in (["--force"], ["--force", "--dry-run"], ["--cadence", "daily", "--force"]):
        capsys.readouterr()
        assert cli.main(["--sport", "nhl", "adaptive-train", *argv]) == 1, argv
        res = json.loads(capsys.readouterr().out)
        assert res["status"] == "failed" and not called, (argv, res, called)
        assert "1 cached source response" in res["reason"] and rel in res["reason"], res["reason"]
        assert res["missing_raw"] == {"count": 1, "first": [rel]}
    assert adaptive.definition(store) == defn


def test_adaptive_train_command_runs_training_when_every_cached_source_file_is_present(store, monkeypatch, capsys):
    """The other side of the completeness guard: a complete cache is not refused (the guard must not over-refuse)."""
    from app import cli, raw_cache
    raw_cache.store(store, source="nhl_api_web", endpoint="test", request_key="completeness-probe", body=b'{"probe": 1}',
                    retrieved_at="2026-10-01T00:00:00Z", importer_version="test")
    store.commit()
    calls = []
    from app import training_inputs     # the input-readiness gate is covered in test_training_inputs.py
    monkeypatch.setattr(training_inputs, "check", lambda conn, now=None: {"ready": True, "blocking": {}, "report": {}})
    monkeypatch.setattr(adaptive, "run_scheduled", lambda sport, **kw: calls.append((sport, kw)) or {"sport": sport, "status": "success"})
    assert cli.main(["--sport", "nhl", "adaptive-train", "--force"]) == 0
    assert calls and calls[0][0] == "nhl" and calls[0][1]["force"] is True
    assert json.loads(capsys.readouterr().out)["status"] == "success"
