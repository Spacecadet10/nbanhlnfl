"""Review fixes 2026-10-03: NBA market comparison uses the current price, forecast notes no longer promise the discontinued
daily job, a game card's agreement never mixes research definitions, and the injury-aware member's own refusal does not
mark a two-hour run failed; earlier-rule model-only lines are labelled and the always-50% baseline has no picked-winner
rate (synthetic stores only)."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import agreement, backtest, jobs, live
from app.ledger import Forecast, record
from app.providers import real as real_provider
from app.providers.real import RealProvider
from test_api_markets import api  # noqa: F401  (fixtures)
from test_live import T_GAME, T_OTHER, client, event, ingest_events, store  # noqa: F401
from test_live_markets import sealed  # noqa: F401
from test_near_tipoff import NOW, ready  # noqa: F401
from test_nhl_eval import nhl_store  # noqa: F401
from test_record_counts import settled_markets  # noqa: F401
from test_research_live import game  # noqa: F401


def test_nba_market_comparison_prefers_the_current_price_at_equal_retrieval_times(store, client):
    """Every NBA odds snapshot stores an opening and a current price with one retrieval time: the comparison uses current.
    The old query (ORDER BY retrieved_at only) returned whichever row SQLite's index walk met first: 'current' sorts before
    'open' within one provider, so it was right by accident. g0's opening row is therefore stored under a provider name
    that sorts first, which makes the old query return the opening price."""
    ingest_events(store, [event("g0", T_OTHER, "3", "4")], "2026-10-19T11:00:00Z")
    raw = store.execute("SELECT MAX(raw_id) FROM raw_responses").fetchone()[0]
    at = "2026-10-19T14:00:00Z"                    # later than g1's 12:00Z quote, before the 15:00Z forecast
    for gid, order in (("espn:g0", ("open", "current")), ("espn:g1", ("current", "open"))):   # either insertion order
        for label in order:
            provider = "A-Book" if (gid, label) == ("espn:g0", "open") else "Book"
            store.execute("INSERT INTO odds_quotes (game_id, provider, market, price_label, home_american, away_american, "
                          "home_prob_devig, quote_time, retrieved_at, raw_id) VALUES (?, ?, 'moneyline', ?, -150, 130, ?, NULL, ?, ?)",
                          (gid, provider, label, 0.6 if label == "current" else 0.9, at, raw))
    live.predict(store, now="2026-10-19T15:00:00Z")
    ingest_events(store, [event("g0", T_OTHER, "3", "4", "STATUS_FINAL", 101, 99),
                          event("g1", T_GAME, "1", "2", "STATUS_FINAL", 110, 100)], "2026-10-22T03:00:00Z")
    market = client.get("/api/performance", params={"run_type": "live"}).json()["market"]
    assert market["matched_games"] == 2
    assert market["market_brier"] == pytest.approx((1 - 0.6) ** 2)        # both home wins, both scored at the current 0.6


def test_forecast_note_does_not_promise_the_discontinued_daily_job(store, client):
    client.now = "2026-10-01T12:00:00Z"                                    # weeks before the game: nothing recorded
    note = client.get("/api/games/espn:g1").json()["forecast_note"]
    assert "discontinued" in note and "within two hours" in note
    assert "records it" not in note and "by the daily job" not in note


def test_game_card_agreement_never_mixes_research_definitions(store):
    """A code change on unchanged data gives a new definition with the same dataset version: members of the earlier
    definition are not combined with it."""
    def put(name, definition, at, p):
        record(store, Forecast("espn:g1", "live", f"research-v2:{name}:{definition}", "a", agreement.FEATURE_VERSION,
                               T_GAME, at, p))
    for name in agreement.NAMES["nba"]:
        put(name, "old", "2026-10-21T21:35:00Z", 0.7)
    for name in agreement.NAMES["nba"][:2]:
        put(name, "new", "2026-10-21T21:45:00Z", 0.4)
    mixed = agreement.summary(store, "espn:g1", "nba", "2026-10-21T22:45:00Z")
    assert len({m["model_version"].rsplit(":", 1)[-1] for m in mixed["models"]}) == 2      # the unguarded summary mixes
    card = RealProvider(store, now="2026-10-21T22:45:00Z")._agreement("espn:g1", "near_tipoff")
    assert {m["model_version"].rsplit(":", 1)[-1] for m in card["models"]} == {"new"}
    assert (card["status"], card["available"], card["home_votes"], card["away_votes"]) == ("partial", 2, 0, 2)
    before = RealProvider(store, now="2026-10-21T21:40:00Z")._agreement("espn:g1", "near_tipoff")   # one definition: unchanged
    assert before["status"] == "complete" and before["home_votes"] == len(agreement.NAMES["nba"])


# --- The injury-aware member's own refusal is not a failed run (jobs.py; agreement.py is identity-hashed and unchanged) ---
REFUSAL = "AvailabilityError: No injury report actually received in the last 60 minutes"


def agreement_run(errors, skipped=None, **extra):
    return {"sport": "nba", "status": "failed", "eligible_games": 1, "created": 5, "already_recorded": 0,
            "errors": errors, "skipped": skipped or {}, "forecasts": [], **extra}


def test_only_a_refusing_members_refusal_is_downgraded():
    ok = jobs._refusal_is_not_failure(agreement_run({"availability_two_hour": REFUSAL}))
    assert ok["status"] == "partial" and ok["refused"] == {"availability_two_hour": REFUSAL}
    assert ok["errors"] == {"availability_two_hour": REFUSAL}              # still reported per member; cohorts see it missing
    for run in (agreement_run({"availability_two_hour": REFUSAL, "gbt_v1": "ValueError: boom"}),     # another member failed
                agreement_run({"availability_two_hour": "KeyError: 'x'"}),                             # not a refusal: a crash
                agreement_run({"gbt_v1": "AvailabilityError: not its error to raise"}),                # not a refusing member
                agreement_run({"availability_two_hour": REFUSAL}, skipped={"efficiency_margin": ["espn:g1"]}),
                {"status": "failed", "error": "RuntimeError: the whole step crashed"},
                agreement_run({}, skipped={"logistic": ["espn:g1"]})):
        assert jobs._refusal_is_not_failure(run) == run and run["status"] == "failed"
    fine = {"sport": "nba", "status": "success", "errors": {}, "skipped": {}}
    assert jobs._refusal_is_not_failure(fine) is fine


def test_two_hour_run_is_not_failed_by_the_injury_members_refusal(ready, monkeypatch):
    empty = json.dumps({"events": []}).encode()
    at = datetime(2026, 10, 21, 22, 28, tzinfo=timezone.utc)
    kw = dict(refresh_kw={"fetch": lambda m: empty, "sleep": lambda s: None, "now": at},
              odds_kw={"fetch": lambda d: empty, "sleep": lambda s: None, "now": at},
              box_kw={"fetch": lambda g: b"{}", "sleep": lambda s: None, "now": at})
    monkeypatch.setattr(jobs, "research_forecasts", lambda conn, **k: agreement_run({"availability_two_hour": REFUSAL}))
    out = jobs.predict_now(ready, now=NOW, **kw)
    assert out["created"] == 1 and out["agreement"]["status"] == "partial" and out["status"] != "failed"
    assert out["agreement"]["refused"] == {"availability_two_hour": REFUSAL}
    monkeypatch.setattr(jobs, "research_forecasts",
                        lambda conn, **k: agreement_run({"availability_two_hour": REFUSAL, "gbt_v1": "ValueError: boom"}))
    again = jobs.predict_now(ready, now=NOW, **kw)
    assert again["status"] == "failed" and again["agreement"]["status"] == "failed"      # a real member failure still fails


# --- Labels only: earlier model-only line rule, and the no-pick baseline -------------------------------------------------
def test_model_only_lines_made_before_the_rule_change_are_labelled_not_changed(settled_markets, monkeypatch):
    def rows(market):
        return settled_markets.get("/api/market-predictions", params={"sport": "nhl", "market": market}).json()
    before = {m: rows(m) for m in ("team_total_home", "reg3")}
    assert not any(r["forecast"]["earlier_line_rule"] for m in before for r in before[m]["predictions"])   # made after the change
    monkeypatch.setattr(real_provider, "MODEL_LINE_RULE_SINCE", "2027-01-01T00:00:00Z")   # now every stored row is "before"
    after = rows("team_total_home")
    assert after["predictions"] and all(r["forecast"]["line_source"] == "model" and r["forecast"]["earlier_line_rule"]
                                        and real_provider.EARLIER_LINE_RULE_NOTE in r["forecast"]["limitations"]
                                        for r in after["predictions"])
    assert not any(r["forecast"]["earlier_line_rule"] for r in rows("reg3")["predictions"])       # no line: never labelled
    assert after["record"] == before["team_total_home"]["record"]                                 # a label: nothing rescored
    assert [(r["forecast"]["line"], r["forecast"]["result"]) for r in after["predictions"]] == \
           [(r["forecast"]["line"], r["forecast"]["result"]) for r in before["team_total_home"]["predictions"]]
    perf = settled_markets.get("/api/market-performance", params={"sport": "nhl", "market": "team_total_home"}).json()
    assert any("earlier line rule" in x for x in perf["limitations"])


def test_always_50_percent_baseline_has_no_picked_winner_rate(store, client):
    """A constant 50% forecast picks nobody: its accuracy is null (shown as a dash), its Brier score and log loss remain."""
    primary = backtest.model_version(live.primary_model(store)[1])
    ingest_events(store, [event("b1", "2026-03-10T23:00:00Z", "1", "2", "STATUS_FINAL", 110, 100, season=2026),
                          event("b2", "2026-03-11T23:00:00Z", "3", "4", "STATUS_FINAL", 90, 100, season=2026)],
                  "2026-03-12T12:00:00Z", month="202603")
    for gid, tip in (("espn:b1", "2026-03-10T23:00:00Z"), ("espn:b2", "2026-03-11T23:00:00Z")):
        for mv, p in ((primary, 0.7), ("constant-p=0.5", 0.5), ("home_rate-smoothing=laplace", 0.6)):
            record(store, Forecast(gid, "backtest", mv, "d", backtest.FEATURE_VERSION, backtest.cutoff_for(tip),
                                   "2026-09-30T00:00:00Z", p))
    perf = client.get("/api/performance", params={"run_type": "backtest"}).json()
    assert perf["sample_size"] == 2 and perf["accuracy"] == 0.5
    by_name = {b["name"]: b for b in perf["baselines"]}
    assert by_name["Always 50%"]["accuracy"] is None and by_name["Always 50%"]["brier_score"] == pytest.approx(0.25)
    assert by_name["Home-win rate"]["accuracy"] == 0.5


# --- Read-only commands never take the job lock; a lock-busy odds cycle leaves a job_runs row ----------------------------
@pytest.fixture
def cli_store(store, tmp_path):
    """The synthetic store copied to where the CLI looks for the NBA store (NBA_DATA_DIR is tmp_path in `store`)."""
    import sqlite3
    dst = sqlite3.connect(tmp_path / "nba.sqlite")
    store.backup(dst)
    dst.close()
    conn = sqlite3.connect(tmp_path / "nba.sqlite")
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def _state(conn):
    return (conn.execute("SELECT COUNT(*) FROM job_runs").fetchone()[0],
            conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0])


def test_read_only_commands_run_while_a_job_holds_the_store_and_cannot_write(cli_store, monkeypatch, capsys):
    from app import cli, db
    monkeypatch.setattr(cli, "LOCK_WAIT_SECONDS", 0)                 # a locking command would fail at once
    before = _state(cli_store)
    with db.store_lock("nba"):                                        # a long job is running on this store
        for cmd in ("score-near-tipoff", "score-markets", "thresholds", "coverage"):
            assert cmd in cli.READ_ONLY_COMMANDS and cli.main([cmd]) == 0, cmd
            json.loads(capsys.readouterr().out)                       # each printed its report
        assert cli.main(["score"]) == 1                               # a writing command still needs the lock: fails visibly
        assert json.loads(capsys.readouterr().out)["status"] == "failed"
    assert _state(cli_store) == before                                # nothing written, nothing migrated
    conn = db.connect_readonly("nba")
    with pytest.raises(Exception, match="readonly|read-only|query_only"):
        conn.execute("INSERT INTO job_runs (job, started_at, status) VALUES ('odds', '2026-10-21T00:00:00Z', 'running')")
    conn.close()


def test_lock_busy_odds_cycle_is_recorded_as_a_skipped_job_run(cli_store, monkeypatch, capsys):
    from app import cli, db
    monkeypatch.setattr(cli, "ODDS_LOCK_WAIT_SECONDS", 0.1)
    with db.store_lock("nba"):
        assert cli.main(["odds"]) == 0
    out = json.loads(capsys.readouterr().out)
    row = cli_store.execute("SELECT job, status, detail FROM job_runs WHERE run_id=?", (out["run_id"],)).fetchone()
    assert (row["job"], row["status"]) == ("odds", "skipped") and json.loads(row["detail"])["lock_busy"] is True
    assert out["status"] == "skipped" and "another job" in out["reason"]


# --- Result availability: late results after an outage, and corrected final scores ---------------------------------------
def _avail(conn, gid):
    r = conn.execute("SELECT result_available_at, result_availability_basis, result_first_seen_at, home_score FROM games "
                     "WHERE game_id=?", (gid,)).fetchone()
    return tuple(r)


def test_a_tracked_games_late_result_is_stamped_when_learned_and_a_backfilled_one_is_assumed(store):
    # o1 was in the store before it started; its result is first seen three days later (the machine was off).
    ingest_events(store, [event("o1", "2026-11-03T00:30:00Z", "1", "2")], "2026-11-01T12:00:00Z", month="202611")
    ingest_events(store, [event("o1", "2026-11-03T00:30:00Z", "1", "2", "STATUS_FINAL", 100, 90),
                          event("o2", "2026-11-03T00:30:00Z", "3", "4", "STATUS_FINAL", 100, 90)],     # o2: first met already final
                  "2026-11-06T12:00:00Z", month="202611")
    assert _avail(store, "espn:o1")[:2] == ("2026-11-06T12:00:00Z", "observed_retrieval")       # never backdated
    assert _avail(store, "espn:o2")[:2] == ("2026-11-03T04:30:00Z", "assumed_tipoff_plus_4h")   # historical backfill: unchanged


def test_a_corrected_final_score_is_available_from_the_correction_not_from_the_first_result(store):
    ingest_events(store, [event("k1", "2026-11-03T00:30:00Z", "1", "2", "STATUS_FINAL", 100, 90)], "2026-11-03T04:00:00Z", month="202611")
    first = _avail(store, "espn:k1")
    assert first[:2] == ("2026-11-03T04:00:00Z", "observed_retrieval")
    ingest_events(store, [event("k1", "2026-11-03T00:30:00Z", "1", "2", "STATUS_FINAL", 100, 90)], "2026-11-04T04:00:00Z", month="202611")
    assert _avail(store, "espn:k1") == first                                                     # same score again: nothing moves
    ingest_events(store, [event("k1", "2026-11-03T00:30:00Z", "1", "2", "STATUS_FINAL", 101, 90)], "2026-11-05T04:00:00Z", month="202611")
    corrected = _avail(store, "espn:k1")
    assert corrected == ("2026-11-05T04:00:00Z", "observed_retrieval", "2026-11-03T04:00:00Z", 101)   # first sighting kept


# --- Monitoring and threshold table --------------------------------------------------------------------------------------
def test_odds_job_is_not_stale_when_it_checked_and_had_nothing_to_do_but_is_after_a_lock_busy_skip(store):
    from app import training_monitor
    now = "2026-11-10T12:00:00Z"
    def run(finished, status, detail):
        store.execute("INSERT INTO job_runs (job, started_at, finished_at, status, attempts, detail) VALUES ('odds', ?, ?, ?, 0, ?)",
                      (finished, finished, status, json.dumps(detail)))
    run("2026-11-09T12:00:00Z", "success", {})                                    # a day ago: too old on its own
    run("2026-11-10T11:30:00Z", "skipped", {"reason": "another job is running", "lock_busy": True})
    assert training_monitor.status(store, now)["inputs"]["odds"]["stale"] is True  # a lock-busy skip is not a check
    run("2026-11-10T11:45:00Z", "skipped", {"reason": "no scheduled game tips off within 36 h"})
    odds = training_monitor.status(store, now)["inputs"]["odds"]
    assert odds["stale"] is False and odds["last_checked"] == "2026-11-10T11:45:00Z" and odds["last_success"] == "2026-11-09T12:00:00Z"


def test_threshold_table_treats_exactly_50_percent_as_no_pick(store):
    from app import near_tipoff, tracking
    mv = near_tipoff.model_version(live.primary_model(store)[1])
    fv = near_tipoff.feature_version(live.engine(store))
    record(store, Forecast("espn:g1", "live", mv, "d", fv, T_GAME, "2026-10-21T22:00:00Z", 0.5))
    rep = tracking.report(store, "2026-10-22T12:00:00Z")
    assert not [g for g in rep["groups"] if g["market"] == "winner" and g["line_source"] == "not_applicable"]


# --- Candidate artifacts: a corrected training result gives a new fit; artifacts fitted earlier stay in force -------------
def test_candidate_fit_identity_includes_training_results(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.candidates import live as pool_live, registry
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    spec = registry.for_sport("nhl")[0]
    monkeypatch.setitem(registry.MIN_TRAIN, spec.sport, 1)
    fits = []
    monkeypatch.setattr(pool_live, "_fit", lambda spec, train, season, defn, fp: fits.append(fp) or {"results_fingerprint": fp})
    def write(path, art):
        path.write_bytes(b"model")
        pool_live._sidecar(path).write_text("digest\n")
    monkeypatch.setattr(pool_live, "_write", write)
    monkeypatch.setattr(pool_live, "_load_verified", lambda path: {"loaded": path.name})
    def rows(score):
        return [SimpleNamespace(game_id="nhl:1", season=2025, y={"home": score, "away": 2}, result_available_at="2025-01-01T00:00:00Z"),
                SimpleNamespace(game_id="nhl:2", season=2025, y={"home": 1, "away": 4}, result_available_at="2025-01-02T00:00:00Z")]
    assert pool_live._fingerprint(rows(3)) == pool_live._fingerprint(rows(5))                    # the old identity cannot see it
    assert pool_live._results_fingerprint(rows(3)) != pool_live._results_fingerprint(rows(5))
    first = pool_live.ensure_fit(spec, rows(3), 2026)
    again = pool_live.ensure_fit(spec, rows(3), 2026)
    assert len(fits) == 1 and again["loaded"] == Path(first["artifact_path"]).name               # unchanged results: reused
    corrected = pool_live.ensure_fit(spec, rows(5), 2026)
    assert len(fits) == 2 and corrected["artifact_path"] != first["artifact_path"]               # corrected score: a new fit
    # An artifact fitted under the earlier (ids-only) name stays in force while no results-aware fit exists for that set.
    other = [SimpleNamespace(game_id="nhl:9", season=2025, y={"home": 2, "away": 1}, result_available_at="2025-01-03T00:00:00Z")]
    folder = pool_live.artifact_dir(spec.sport) / spec.name.replace(":", "__")
    legacy = folder / f"{registry.definition(spec)}-2026-{pool_live._fingerprint(other)}.pkl"
    write(legacy, {})
    kept = pool_live.ensure_fit(spec, other, 2026)
    assert kept["loaded"] == legacy.name and len(fits) == 2


# --- Demo: no real study, one store build under concurrency; freshness is about missed games, not job age -----------------
def test_demo_research_store_is_built_once_under_concurrent_requests(monkeypatch):
    import threading
    import time
    from app import research_demo
    research_demo._store.cache_clear()
    builds = []
    real = research_demo._store.__wrapped__
    def slow():
        builds.append(1)
        time.sleep(0.2)                       # long enough for every thread to arrive during the build
        return object()
    monkeypatch.setattr(research_demo, "_store", __import__("functools").lru_cache(maxsize=1)(slow))
    got = []
    threads = [threading.Thread(target=lambda: got.append(research_demo.store())) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(builds) == 1 and len({id(x) for x in got}) == 1 and callable(real)


def test_forecast_freshness_reports_missed_games_not_the_age_of_a_job(store):
    def row(now):
        return next(s for s in RealProvider(store, now=now).sources() if s.name == "Elo forecasts")
    quiet = row("2026-10-10T12:00:00Z")                      # days before any eligible game: nothing was due
    assert quiet.freshness == "fresh" and "No eligible game started" in quiet.note
    missed = row("2026-10-22T02:00:00Z")                     # g1 (23:30Z) started without a two-hour forecast
    assert missed.freshness == "stale" and "0 of 1" in missed.note
    mv = __import__("app.near_tipoff", fromlist=["x"]).model_version(live.primary_model(store)[1])
    fv = __import__("app.near_tipoff", fromlist=["x"]).feature_version(live.engine(store))
    record(store, Forecast("espn:g1", "live", mv, "d", fv, T_GAME, "2026-10-21T22:00:00Z", 0.6))
    covered = row("2026-10-22T02:00:00Z")
    assert covered.freshness == "fresh" and "1 of 1" in covered.note and str(covered.last_updated).startswith("2026-10-21")


# --- Rest days are a property of the game, not of how often it is predicted ----------------------------------------------
def test_predicting_a_game_twice_does_not_turn_it_into_a_back_to_back():
    from app.models.base import PregameGame
    from app.models.elo import EloConfig
    from app.models.hockey import GoalsConfig, HockeyEloConfig, HockeyFeatures
    from app.models.logistic import TeamFeatures
    from app.models.score_candidates import GoalsGlm
    g1 = PregameGame("x:1", 2027, "regular", "2026-11-01T00:00:00Z", "A", "B", False)
    g2 = PregameGame("x:2", 2027, "regular", "2026-11-05T00:00:00Z", "A", "C", False)
    for feats in (TeamFeatures(EloConfig()), HockeyFeatures(HockeyEloConfig(), GoalsConfig())):
        feats.compute(g1)
        first, again = feats.compute(g2), feats.compute(g2)
        assert first == again and first["b2b_home"] == 0.0                    # four days of rest both times
    glm = GoalsGlm(HockeyEloConfig())
    glm._vector(g1)
    assert glm._vector(g2) == glm._vector(g2)


# --- Combination-rule record: one research definition per record ---------------------------------------------------------
def test_rule_record_never_pools_research_definitions(game):
    from app import rule_tracking as rt
    from test_research_live import run
    from test_rule_tracking import registry
    run(game, "2026-10-12T21:30:00Z")
    row = dict(game.execute("SELECT * FROM research_cohorts WHERE market='winner' AND definition NOT LIKE '%:expanded-v1' "
                            "ORDER BY cohort_id LIMIT 1").fetchone())
    first = row["definition"]
    row.pop("cohort_id")
    row.update(idempotency_key=row["idempotency_key"] + "-later-code", definition="feedfacefeedface",
               generated_at="2026-10-12T21:45:00Z")
    game.execute(f"INSERT INTO research_cohorts ({','.join(row)}) VALUES ({','.join('?' * len(row))})", list(row.values()))
    rep = rt.report(game, "nhl", "winner", "all", registry())
    assert [d["definition"] for d in rep["definitions"]] == ["feedfacefeedface", first]         # newest first, both listed
    assert rep["definition"] == "feedfacefeedface" and rep["cohorts"] == 1
    earlier = rt.report(game, "nhl", "winner", "all", registry(), definition=first)
    assert earlier["definition"] == first and earlier["cohorts"] == 1
    made = lambda r: {x["generated_at"] for rule in r["rules"] for x in rule["prospective"]["recent"]}
    assert made(rep) == {"2026-10-12T21:45:00Z"} and "2026-10-12T21:45:00Z" not in made(earlier)   # each from its own cohort


# --- Prop lines are collected after the two-hour forecasts, never in front of them ---------------------------------------
def test_periodic_cycle_collects_props_after_the_forecasts(cli_store, monkeypatch, capsys):
    from app import cli
    order = []
    def snapshot(conn, **kw):
        order.append(("odds", kw.get("props")))
        return {"status": "success", "run_id": None}
    monkeypatch.setattr(jobs, "odds_snapshot", snapshot)
    monkeypatch.setattr(jobs, "forecast_due", lambda conn: order.append("forecasts") or {"status": "skipped"})
    monkeypatch.setattr(jobs, "collect_props", lambda conn, snap=None, **kw: order.append("props") or {"status": "success"})
    assert cli.main(["odds"]) == 0
    assert order == [("odds", False), "forecasts", "props"]
    assert json.loads(capsys.readouterr().out)["odds"]["props"] == {"status": "success"}


def test_two_hour_job_never_collects_props_before_forecasting(ready, monkeypatch):
    called = []
    monkeypatch.setattr(jobs, "collect_props", lambda *a, **k: called.append(1) or {"status": "success"})
    empty = json.dumps({"events": []}).encode()
    at = datetime(2026, 10, 21, 22, 28, tzinfo=timezone.utc)
    jobs.predict_now(ready, now=NOW, refresh_kw={"fetch": lambda m: empty, "sleep": lambda s: None, "now": at},
                     odds_kw={"fetch": lambda d: empty, "sleep": lambda s: None, "now": at},
                     box_kw={"fetch": lambda g: b"{}", "sleep": lambda s: None, "now": at})
    assert called == []


# --- API: store-backed requests run one at a time (concurrent small reads contended for seconds) --------------------------
def test_store_backed_requests_are_served_one_at_a_time(api, sealed, monkeypatch):
    import threading
    import time
    state = {"active": 0, "max": 0}
    real_meta = RealProvider.meta
    def meta(self):
        state["active"] += 1
        state["max"] = max(state["max"], state["active"])
        time.sleep(0.03)
        try:
            return real_meta(self)
        finally:
            state["active"] -= 1
    monkeypatch.setattr(RealProvider, "meta", meta)
    codes = []
    threads = [threading.Thread(target=lambda: codes.append(api.get("/api/meta", params={"sport": "nhl"}).status_code))
               for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert codes == [200] * 6 and state["max"] == 1
    assert api.get("/api/games/nhl:nope", params={"sport": "nhl"}).status_code == 404      # an error releases the lock
    assert api.get("/api/meta", params={"sport": "nhl"}).status_code == 200


# --- Records split without dropping anything: lead-time bands, backtest periods; bookmaker-implied rate; reserved seasons --
def test_pre_game_archive_record_is_split_by_registered_lead_bands_and_the_parts_add_up(store, client):
    live.predict(store, now="2026-10-19T15:00:00Z")                  # two days (Eastern) before g1 on 2026-10-21
    body = client.get("/api/predictions", params={"run_type": "live"}).json()
    assert [p["key"] for p in body["parts"]] == ["1_day", "2_days", "3_7_days"]
    assert sum(p["record"]["selected"] for p in body["parts"]) == body["record"]["selected"] == body["total"]
    g1 = next(r for r in body["predictions"] if r["game_id"] == "espn:g1")
    assert g1["lead_days"] == 2 and g1["horizon_hours"] == pytest.approx(56.5)
    assert next(p for p in body["parts"] if p["key"] == "2_days")["record"]["selected"] >= 1
    assert client.get("/api/predictions", params={"run_type": "live", "policy": "near_tipoff"}).json()["parts"] == []


def test_backtest_record_is_split_by_period_and_the_test_part_equals_performance(store, client):
    primary = backtest.model_version(live.primary_model(store)[1])
    v = store.execute("SELECT game_id, tipoff_at FROM games WHERE status='final' AND season IN (2022, 2023, 2024) "
                      "ORDER BY tipoff_at LIMIT 1").fetchone()        # an existing validation-season game (data unchanged)
    ingest_events(store, [event("t1", "2026-03-10T23:00:00Z", "1", "2", "STATUS_FINAL", 110, 100, season=2026),
                          event("t2", "2026-03-11T23:00:00Z", "3", "4", "STATUS_FINAL", 90, 100, season=2026)],
                  "2026-03-12T12:00:00Z", month="202603")
    for gid, tip in ((v["game_id"], v["tipoff_at"]), ("espn:t1", "2026-03-10T23:00:00Z"), ("espn:t2", "2026-03-11T23:00:00Z")):
        record(store, Forecast(gid, "backtest", primary, "d", backtest.FEATURE_VERSION, backtest.cutoff_for(tip),
                               "2026-09-30T00:00:00Z", 0.7))
    body = client.get("/api/predictions", params={"run_type": "backtest"}).json()
    parts = {p["key"]: p["record"] for p in body["parts"]}
    assert set(parts) == {"validation", "test"} and body["record"]["selected"] == 3           # no row hidden
    assert parts["validation"]["selected"] == 1 and parts["test"]["selected"] == 2
    assert {r["game_id"]: r["period"] for r in body["predictions"]} == {v["game_id"]: "validation", "espn:t1": "test", "espn:t2": "test"}
    perf = client.get("/api/performance", params={"run_type": "backtest"}).json()
    assert perf["sample_size"] == parts["test"]["settled"] == 2                                # same population as Performance


def test_market_record_reports_what_the_bookmaker_implied_for_quoted_picks(settled_markets):
    get = lambda m: settled_markets.get("/api/market-predictions", params={"sport": "nhl", "market": m}).json()
    quoted = [get(m)["record"] for m in ("spread", "total")]
    assert any(r["quoted_decided"] == 1 for r in quoted)                    # game 1 was forecast at the bookmaker's line
    for r in quoted:
        if r["quoted_decided"]:
            assert 0 < r["mean_implied"] < 1 and 0 <= r["quoted_wins"] <= r["quoted_decided"] <= r["wins"] + r["losses"]
    home = get("team_total_home")
    assert home["record"]["quoted_decided"] == 0 and home["record"]["mean_implied"] is None     # model-only lines: no claim
    assert sum(p["record"]["selected"] for p in home["parts"]) == home["record"]["selected"]


def test_research_folds_in_reserved_test_seasons_are_flagged():
    from app import agreement_tracking as at, nhl_eval
    folds = at._flag_reserved([{"season": 2024}, {"season": 2025}, {"season": 2026, "chosen": None}], "nhl")
    assert [f["reserved_test_season"] for f in folds] == [s in nhl_eval.PERIODS["test"] for s in (2024, 2025, 2026)] == [False, True, True]
    assert at.reserved_seasons("nba") == set(backtest.PERIODS["test"])
