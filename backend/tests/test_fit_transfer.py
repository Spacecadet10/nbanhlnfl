"""Transfer of published adaptive-v1 fits from the data directory they were trained in to another one (fit_transfer.py).
The release moves the fits of the isolated retrain (2026-10-14) into the live store instead of training again; the target
must then forecast exactly as the training store does, with the same artifacts, and refuse anything it cannot verify."""
import json
import shutil
import sqlite3

import pytest

from app import adaptive, db, fit_transfer
from test_adaptive import CYCLE_AT, TRAIN_AT, store  # noqa: F401  (fixture)
from test_live_markets import add_upcoming, quote, sealed  # noqa: F401  (fixture)
from test_nhl_eval import nhl_store  # noqa: F401  (fixture)
from test_research_live import GID, run

FIT_COLUMNS = ("kind", "name", "definition", "period", "schedule", "status", "training_cutoff", "training_games",
               "results_fingerprint", "artifact_sha256", "idempotency_key", "started_at")


def _clone(conn, src_dir, dest_dir):
    """The data directory as it was before training: every file but the store, plus a consistent copy of the store."""
    shutil.copytree(src_dir, dest_dir, ignore=shutil.ignore_patterns("*.sqlite", "*.sqlite-*"))
    target = sqlite3.connect(dest_dir / "nhl.sqlite")
    conn.backup(target)
    target.close()


def _forecasts(conn):
    rows = {}
    for r in conn.execute("SELECT * FROM adaptive_forecasts ORDER BY market"):
        rows[r["market"]] = {"status": r["status"], "p": (r["p_a"], r["p_mid"], r["p_b"]), "line": r["line"],
                             "members": json.loads(r["members"] or "[]"), "missing": json.loads(r["missing"] or "{}"),
                             "weights": json.loads(r["weights"] or "{}"), "reason": r["reason"]}
    return rows


@pytest.fixture
def trained(store, tmp_path, monkeypatch):
    target_dir = tmp_path.parent / f"{tmp_path.name}-target"
    _clone(store, tmp_path, target_dir)
    t = adaptive.train(store, now=TRAIN_AT)
    assert t["status"] in ("success", "partial"), t.get("errors")
    return {"source": store, "source_dir": tmp_path, "target_dir": target_dir, "definition": t["definition"]}


def _target(trained, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(trained["target_dir"]))
    return db.connect(sport="nhl")


def test_transferred_fits_forecast_exactly_as_in_the_store_they_were_trained_in(trained, monkeypatch):
    src, defn = trained["source"], trained["definition"]
    src_fits = [tuple(r[c] for c in FIT_COLUMNS) for r in src.execute("SELECT * FROM adaptive_fits ORDER BY fit_id")]
    src_libraries = {r["member"]: r["libraries"] for r in src.execute("SELECT * FROM model_fits WHERE member LIKE 'adaptive:%'")}
    run(src, CYCLE_AT)
    assert adaptive.record(src, game_ids=[GID], now=CYCLE_AT)["status"] in ("success", "partial")
    expected = _forecasts(src)
    assert expected["winner"]["status"] == "forecast"

    tgt = _target(trained, monkeypatch)
    assert tgt.execute("SELECT count(*) FROM adaptive_fits").fetchone()[0] == 0
    # An unrelated identity first, so the target's model_fits ids differ from the source's: the mapping is by identity.
    tgt.execute("INSERT INTO model_fits (sport, member, definition, schedule, fit_kind, trained_season, training_games, "
                "results_fingerprint, results_available_through, params_digest, libraries, first_used_at) "
                "VALUES ('nhl', 'other', 'x', 'weekly', 'weekly', 2026, 1, 'fp', '2026-01-01T00:00:00Z', 'pd', '{}', '2026-01-01T00:00:00Z')")
    out = fit_transfer.transfer(tgt, trained["source_dir"], "nhl", cadences=("weekly",))      # the clock: before TRAIN_AT
    assert out["status"] == "success", out
    weekly = out["cadences"]["weekly"]
    assert weekly["definition"] == defn and weekly["publication"]["published"] == len(src_fits) and not weekly["publication"]["failed"]
    # Same rows (identity, period, status, digests, cutoffs, idempotency keys); artifacts now under the target directory.
    assert [tuple(r[c] for c in FIT_COLUMNS) for r in tgt.execute("SELECT * FROM adaptive_fits ORDER BY fit_id")] == src_fits
    paths = [r["artifact_path"] for r in tgt.execute("SELECT artifact_path FROM adaptive_fits WHERE artifact_path IS NOT NULL")]
    assert paths and all(p.startswith(str(adaptive.root("nhl"))) for p in paths)
    assert not any(p.startswith(str(trained["source_dir"]) + "/") for p in paths)
    # The model_fits identities keep the training environment's library versions.
    assert {r["member"]: r["libraries"] for r in tgt.execute("SELECT * FROM model_fits WHERE member LIKE 'adaptive:%'")} == src_libraries
    src_ident = {r["name"]: (r["member"], r["params_digest"]) for r in src.execute(
        "SELECT f.name, m.member, m.params_digest FROM adaptive_fits f JOIN model_fits m ON m.fit_id=f.model_fit_id")}
    tgt_ident = {r["name"]: (r["member"], r["params_digest"]) for r in tgt.execute(
        "SELECT f.name, m.member, m.params_digest FROM adaptive_fits f JOIN model_fits m ON m.fit_id=f.model_fit_id")}
    assert src_ident and tgt_ident == src_ident
    assert weekly["history"] == "copied" and weekly["warnings"] == []
    # Checked after publication (rows are stamped when written): the transferred fits are in force, nothing is due.
    assert weekly["inputs_changed"] == [] and weekly["due"]["due"] is False, weekly
    assert weekly["in_force"]["combiner"] == tgt.execute("SELECT max(fit_id) FROM adaptive_fits WHERE kind='combiner'").fetchone()[0]
    # The checkpoint of the out-of-sample history travels too, so the next scheduled run continues instead of re-walking.
    assert (adaptive.root("nhl") / f"history-{defn}.pkl").is_file()

    run(tgt, CYCLE_AT)
    assert adaptive.record(tgt, game_ids=[GID], now=CYCLE_AT)["status"] in ("success", "partial")
    assert _forecasts(tgt) == expected

    again = fit_transfer.transfer(tgt, trained["source_dir"], "nhl", cadences=("weekly",), now=TRAIN_AT)
    # A repeat finds every row already there: nothing is added (idempotent), nothing is overwritten.
    assert again["status"] == "success" and again["cadences"]["weekly"]["resumed"] == len(src_fits), again
    assert again["cadences"]["weekly"]["publication"]["published"] == 0 and again["cadences"]["weekly"]["files"]["copied"] == 0
    assert tgt.execute("SELECT count(*) FROM adaptive_fits").fetchone()[0] == len(src_fits)


def test_a_source_artifact_that_does_not_verify_refuses_the_whole_transfer_before_anything_is_written(trained, monkeypatch):
    src = trained["source"]
    member = src.execute("SELECT artifact_path FROM adaptive_fits WHERE kind='member' AND status='success' LIMIT 1").fetchone()[0]
    with open(member, "ab") as f:
        f.write(b"tampered")
    tgt = _target(trained, monkeypatch)
    out = fit_transfer.transfer(tgt, trained["source_dir"], "nhl", cadences=("weekly",), now=TRAIN_AT)
    assert out["status"] == "failed" and "digest" in out["cadences"]["weekly"]["reason"], out
    assert tgt.execute("SELECT count(*) FROM adaptive_fits").fetchone()[0] == 0
    assert tgt.execute("SELECT count(*) FROM model_fits WHERE member LIKE 'adaptive:%'").fetchone()[0] == 0
    assert not adaptive.root("nhl").exists() or not any(adaptive.root("nhl").rglob("*.pkl"))


def test_a_target_whose_code_gives_another_definition_receives_nothing(trained, monkeypatch):
    tgt = _target(trained, monkeypatch)
    monkeypatch.setattr(adaptive, "definition", lambda conn: "0123456789abcdef")
    out = fit_transfer.transfer(tgt, trained["source_dir"], "nhl", cadences=("weekly",), now=TRAIN_AT)
    assert out["status"] == "failed", out
    assert "no fits of this definition" in out["cadences"]["weekly"]["reason"]
    assert tgt.execute("SELECT count(*) FROM adaptive_fits").fetchone()[0] == 0


def test_transfer_into_the_source_directory_itself_is_refused(trained):
    out = fit_transfer.transfer(trained["source"], trained["source_dir"], "nhl", cadences=("weekly",), now=TRAIN_AT)
    assert out["status"] == "failed" and "same data directory" in out["reason"], out


def test_cli_command_reports_the_transfer_and_fails_on_refusal(trained, monkeypatch, capsys):
    from app import cli
    monkeypatch.setenv("NBA_DATA_DIR", str(trained["target_dir"]))
    assert cli.main(["--sport", "nhl", "adaptive-import", "--from", str(trained["source_dir"]), "--cadence", "weekly"]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["status"] == "success" and res["cadences"]["weekly"]["publication"]["published"] > 0
    with adaptive.training_lock("nhl"):          # a refusal (a training run of the sport) exits 1
        assert cli.main(["--sport", "nhl", "adaptive-import", "--from", str(trained["source_dir"]), "--cadence", "weekly"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "failed"


def test_a_running_training_of_the_sport_refuses_the_transfer(trained, monkeypatch):
    tgt = _target(trained, monkeypatch)
    with adaptive.training_lock("nhl") as held:
        assert held
        out = fit_transfer.transfer(tgt, trained["source_dir"], "nhl", cadences=("weekly",), now=TRAIN_AT)
    assert out["status"] == "failed" and "training lock" in out["reason"], out
    assert tgt.execute("SELECT count(*) FROM adaptive_fits").fetchone()[0] == 0


def test_an_artifact_that_cannot_be_loaded_refuses_the_transfer_before_anything_is_written(trained, monkeypatch):
    tgt = _target(trained, monkeypatch)
    real = adaptive._load

    def load(path, sha=None):
        if "hockey_logistic@adaptive" in str(path):
            raise ModuleNotFoundError("No module named 'torch'")
        return real(path, sha)
    monkeypatch.setattr(adaptive, "_load", load)
    out = fit_transfer.transfer(tgt, trained["source_dir"], "nhl", cadences=("weekly",), now=TRAIN_AT)
    assert out["status"] == "failed" and "cannot be loaded" in out["cadences"]["weekly"]["reason"], out
    assert tgt.execute("SELECT count(*) FROM adaptive_fits").fetchone()[0] == 0
    assert tgt.execute("SELECT count(*) FROM model_fits WHERE member LIKE 'adaptive:%'").fetchone()[0] == 0


def test_an_interrupted_transfer_resumes_with_the_missing_rows_only(trained, monkeypatch):
    src = trained["source"]
    src_fits = [tuple(r[c] for c in FIT_COLUMNS) for r in src.execute("SELECT * FROM adaptive_fits ORDER BY fit_id")]
    tgt = _target(trained, monkeypatch)
    real = adaptive.publish

    def interrupted(conn, out, log=None):
        real(conn, {**out, "pending": out["pending"][:3]})
        raise KeyboardInterrupt
    monkeypatch.setattr(adaptive, "publish", interrupted)
    with pytest.raises(KeyboardInterrupt):
        fit_transfer.transfer(tgt, trained["source_dir"], "nhl", cadences=("weekly",))
    monkeypatch.setattr(adaptive, "publish", real)
    out = fit_transfer.transfer(tgt, trained["source_dir"], "nhl", cadences=("weekly",))
    assert out["status"] == "success" and out["cadences"]["weekly"]["resumed"] == 3, out
    assert [tuple(r[c] for c in FIT_COLUMNS) for r in tgt.execute("SELECT * FROM adaptive_fits ORDER BY fit_id")] == src_fits


def test_an_artifact_path_that_escapes_the_source_directory_is_refused(trained, monkeypatch):
    src, defn = trained["source"], trained["definition"]
    row = src.execute("SELECT * FROM adaptive_fits WHERE kind='member' AND status='success' LIMIT 1").fetchone()
    outside = trained["source_dir"].parent / f"{trained['source_dir'].name}-outside.pkl"
    shutil.copy(row["artifact_path"], outside)
    shutil.copy(row["artifact_path"] + ".sha256", str(outside) + ".sha256")
    sneaky = str(adaptive.root("nhl") / ".." / ".." / ".." / ".." / outside.name)
    with adaptive.cadence("weekly"):
        adaptive._insert_fit(src, "nhl", "member", "escape@adaptive", defn, "2027:2026-W42", "success", row["started_at"], {},
                             cutoff=row["training_cutoff"], games=1, fp="escape", path=sneaky, sha=row["artifact_sha256"])
    tgt = _target(trained, monkeypatch)
    out = fit_transfer.transfer(tgt, trained["source_dir"], "nhl", cadences=("weekly",), now=TRAIN_AT)
    assert out["status"] == "failed" and "outside" in out["cadences"]["weekly"]["reason"], out
    assert tgt.execute("SELECT count(*) FROM adaptive_fits").fetchone()[0] == 0


def test_a_retry_after_a_publication_failure_is_refused_not_reported_as_success(trained, monkeypatch):
    """A member whose row could not be written makes publish record the combiner as 'partial' under the same idempotency
    key as the source's 'success' combiner: a retry must not count it as already transferred."""
    tgt = _target(trained, monkeypatch)
    real = adaptive._apply

    def flaky(conn, rec):
        if rec["name"] == "hockey_logistic@adaptive":
            raise sqlite3.OperationalError("disk I/O error")
        return real(conn, rec)
    monkeypatch.setattr(adaptive, "_apply", flaky)
    first = fit_transfer.transfer(tgt, trained["source_dir"], "nhl", cadences=("weekly",))
    assert first["status"] == "failed"
    assert tgt.execute("SELECT status FROM adaptive_fits WHERE kind='combiner'").fetchone()[0] == "partial"
    monkeypatch.setattr(adaptive, "_apply", real)
    again = fit_transfer.transfer(tgt, trained["source_dir"], "nhl", cadences=("weekly",))
    assert again["status"] == "failed" and "differ" in again["cadences"]["weekly"]["reason"], again


def test_warnings_name_a_definition_that_is_due_after_the_transfer(trained, monkeypatch):
    tgt = _target(trained, monkeypatch)
    monkeypatch.setattr(adaptive, "due", lambda conn, now=None: {"due": True, "reason": "new period"})
    out = fit_transfer.transfer(tgt, trained["source_dir"], "nhl", cadences=("weekly",))
    assert out["status"] == "success" and any("due" in w for w in out["cadences"]["weekly"]["warnings"]), out


def test_a_different_history_checkpoint_in_the_target_is_replaced_not_a_refusal(trained, monkeypatch):
    """The checkpoint is mutable by design (save_history rewrites it; no fit row refers to it): a stale one left by a target
    training run that never published is replaced by the source's; artifacts are never overwritten."""
    tgt = _target(trained, monkeypatch)
    adaptive.save_history("nhl", trained["definition"], {"candidates": {"stale": 1}})
    out = fit_transfer.transfer(tgt, trained["source_dir"], "nhl", cadences=("weekly",))
    assert out["status"] == "success" and out["cadences"]["weekly"]["history"] == "replaced", out
    assert adaptive.load_history("nhl", trained["definition"]) != {"candidates": {"stale": 1}}


def _nba_store(monkeypatch, tmp_path):
    """The synthetic NBA store of test_adaptive's NBA scheduling test (two seasons of results, one upcoming game with odds)."""
    from app import backtest, research_live
    from app.candidates import engine, registry
    from app.models import score_candidates
    from test_adaptive import _test_clock
    from test_backtest import synthetic_rows
    from test_live import T_GAME, event, ingest_events
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
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
    conn.execute("INSERT INTO job_runs (job, started_at, finished_at, status) VALUES ('refresh', '2026-10-21T22:00:00Z', "
                 "'2026-10-21T22:00:00Z', 'success')")
    monkeypatch.setattr(adaptive, "utc_now", _test_clock("2026-10-21T20:00:00Z", "2026-10-21T22:00:00Z"))
    return conn


def _nba_cycle(conn, now="2026-10-21T22:00:00Z"):
    from app import market_live, near_tipoff
    ids = [f["game_id"] for f in near_tipoff.predict_now(conn, now=now)["forecasts"]]
    assert ids == ["espn:g1"]
    market_live.forecast(conn, now=now, policy="near_tipoff", game_ids=ids)
    r = adaptive.record(conn, game_ids=ids, now=now)
    assert r["status"] in ("success", "partial"), r
    return _forecasts(conn)


def test_transferred_nba_fits_forecast_exactly_as_in_the_store_they_were_trained_in(monkeypatch, tmp_path):
    """NBA's five markets (fixture data): the target forecasts exactly as the training store."""
    from app.sports import NBA
    src = _nba_store(monkeypatch, tmp_path)
    target_dir = tmp_path.parent / f"{tmp_path.name}-target"
    shutil.copytree(tmp_path, target_dir, ignore=shutil.ignore_patterns("*.sqlite", "*.sqlite-*"))
    t = sqlite3.connect(target_dir / "nba.sqlite")
    src.backup(t)
    t.close()
    first = adaptive.run_scheduled("nba", now="2026-10-21T20:00:00Z")
    assert first["status"] == "success", first
    expected = _nba_cycle(src)
    assert set(expected) == set(NBA.markets) and {m for m, f in expected.items() if f["status"] == "forecast"} >= {"winner", "spread"}
    monkeypatch.setenv("NBA_DATA_DIR", str(target_dir))
    tgt = db.connect(sport="nba")
    out = fit_transfer.transfer(tgt, tmp_path, "nba", cadences=("weekly",))
    assert out["status"] == "success" and out["cadences"]["weekly"]["inputs_changed"] == [], out
    assert _nba_cycle(tgt) == expected


def test_an_intact_artifact_whose_sidecar_is_missing_is_repaired_not_refused(trained, monkeypatch):
    """A transfer killed between writing an artifact and its digest file leaves the data but no sidecar: the retry
    completes it (the same bytes; nothing different is overwritten)."""
    tgt = _target(trained, monkeypatch)
    real = adaptive.publish

    def killed(conn, out, log=None):
        raise KeyboardInterrupt
    monkeypatch.setattr(adaptive, "publish", killed)
    with pytest.raises(KeyboardInterrupt):
        fit_transfer.transfer(tgt, trained["source_dir"], "nhl", cadences=("weekly",))
    side = next(adaptive.root("nhl").rglob("*.pkl.sha256"))
    side.unlink()
    monkeypatch.setattr(adaptive, "publish", real)
    out = fit_transfer.transfer(tgt, trained["source_dir"], "nhl", cadences=("weekly",))
    assert out["status"] == "success" and out["cadences"]["weekly"]["files"]["repaired"] == 1, out
    assert side.is_file()


def test_a_due_check_that_errors_is_a_warning(trained, monkeypatch):
    tgt = _target(trained, monkeypatch)
    monkeypatch.setattr(adaptive, "due", lambda conn, now=None: {"due": False, "reason": "definition unavailable: OSError: x"})
    out = fit_transfer.transfer(tgt, trained["source_dir"], "nhl", cadences=("weekly",))
    assert any("due check failed" in w for w in out["cadences"]["weekly"]["warnings"]), out


def test_a_refused_retry_names_the_recovery(trained, monkeypatch):
    """After a publication failure the definition's combiner is 'partial'; the refusal says how it recovers (the scheduled
    training run treats a partial combiner as due and supersedes it)."""
    tgt = _target(trained, monkeypatch)
    real = adaptive._apply

    def flaky(conn, rec):
        if rec["name"] == "hockey_logistic@adaptive":
            raise sqlite3.OperationalError("disk I/O error")
        return real(conn, rec)
    monkeypatch.setattr(adaptive, "_apply", flaky)
    fit_transfer.transfer(tgt, trained["source_dir"], "nhl", cadences=("weekly",))
    monkeypatch.setattr(adaptive, "_apply", real)
    again = fit_transfer.transfer(tgt, trained["source_dir"], "nhl", cadences=("weekly",))
    assert "adaptive-train" in again["cadences"]["weekly"]["reason"], again
