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

    def no_total(rows, candidates, market, cutoff, fallbacks=True):
        if market == "total":
            return {"market": market, "cutoff": cutoff, "excluded": {}, "members": [], "stack": None, "fallbacks": {},
                    "reason": "simulated: no usable rows"}
        return real(rows, candidates, market, cutoff, fallbacks)
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
