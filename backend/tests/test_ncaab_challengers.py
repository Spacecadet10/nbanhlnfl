"""Prospective NCAAB challengers (app/ncaab_challengers.py): refits of the frozen parameters, recorded point-in-time before
tip-off into the separate append-only challenger store, scored at read time against the served forecasts. The served NCAAB
models, tables and files are never touched. Synthetic store, the real model code, no network."""
import json
import math
import sqlite3
from pathlib import Path

import pytest

from app import jobs, learning_challengers as lc, learning_eval as le, market_live, near_tipoff, ncaab_challengers as nc
from app.models import college
from ncaab_support import GAME, NOW, add_game, add_quote, make_store, seed

SERVED_TABLES = ("predictions", "near_tipoff_forecasts", "market_forecasts", "adaptive_forecasts", "candidate_forecasts")


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(college, "VALIDATION_COMPLETE_AT", "9999-01-01T00:00:00Z")      # the synthetic store has no validation seasons
    conn = make_store(tmp_path, monkeypatch)
    seed(conn)
    n = 0
    # earlier seasons: 2024 (validation), 2025 (test, extreme scores), 2026 (test); 2013 is warm-up burn-in
    for season, day, hs, aws in ((2013, "2012-12", 90, 60), (2024, "2023-12", 78, 70), (2025, "2024-12", 120, 50),
                                 (2026, "2025-12", 75, 72), (2024, "2024-01", 70, 69), (2026, "2026-01", 99, 40)):
        for i in range(12):
            n += 1
            add_game(conn, f"9{n:03d}", f"{day}-{1 + i:02d}T23:00:00Z", str(1 + i % 4), str(1 + (i + 1) % 4), "final",
                     hs + i % 3, aws + i % 2, season=season)
    add_quote(conn, GAME, "spread", -4.5, -110, -110, "2026-11-17T22:00:00Z")
    add_quote(conn, GAME, "total", 140.5, -110, -110, "2026-11-17T22:05:00Z")
    yield conn
    conn.close()


def policies():
    pols = lc.load_policies("ncaab")
    assert len(pols) == 1
    return pols


@pytest.fixture(autouse=True)
def tiny_minimum(monkeypatch):
    monkeypatch.setattr(nc, "MIN_FIT_GAMES", 5)


def counts(conn):
    return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in SERVED_TABLES}


def rows_of(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c.execute("SELECT * FROM challenger_forecasts ORDER BY forecast_id").fetchall()


# ---- policy ---------------------------------------------------------------------------------------------------------
def test_seeded_policy_has_four_distinct_variants_with_manual_provenance():
    pol = policies()[0]
    names = [c.spec.name for c in pol.challengers]
    assert pol.policy_id == "ncaab-challengers-v1" and len(names) == len(set(names)) == 4
    assert {c.provenance["source"] for c in pol.challengers} == {"manual"}
    assert all(c.provenance["note"].strip() and c.preregistered_n is None for c in pol.challengers)
    fits = {c.spec.name: c.spec.extra["fit_seasons"] for c in pol.challengers}
    assert sorted(set(fits.values())) == ["all", "exclude_test"] and sum(v == "all" for v in fits.values()) == 1   # C2 only
    assert {m for c in pol.challengers for m in c.spec.extra["markets"]} == {"winner", "spread", "total", "team_total_home", "team_total_away"}


def test_a_spec_with_unknown_fields_or_markets_is_refused(tmp_path):
    pol = json.loads((Path(lc.POLICY_DIR) / "ncaab-challengers-v1.json").read_text())
    pol["challengers"][0]["spec"]["extra"]["markets"] = ["moneyline"]
    p = tmp_path / "ncaab-challengers-v1.json"
    p.write_text(json.dumps(pol))
    with pytest.raises(lc.PolicyError, match="market"):
        lc.load_policy(p)


# ---- recording --------------------------------------------------------------------------------------------------------
def test_dry_run_reports_and_writes_nothing(store, tmp_path):
    path = tmp_path / "research" / "challengers.sqlite"
    before = counts(store)
    out = nc.record(store, path, policies=policies(), now=NOW, dry_run=True)
    assert out["status"] in ("success", "partial") and out["would_record"] == 9 and out["created"] == 0
    assert not path.exists() and not (path.parent / "challengers" / "ncaab").exists() and counts(store) == before


def test_record_is_separate_append_only_idempotent_and_prices_the_served_line(store, tmp_path):
    path = tmp_path / "research" / "challengers.sqlite"
    before = counts(store)
    out = nc.record(store, path, policies=policies(), now=NOW)
    assert out["created"] == 9 and out["errors"] == {} and counts(store) == before          # served tables untouched
    rows = rows_of(path)
    by = {(r["spec_name"], r["market"]): r for r in rows}
    assert {(r["spec_name"].split("_c")[0], r["market"]) for r in rows} >= {("ncaab_refit", "winner")}
    served = {(e["market"]): e for e in market_live.forecast(store, now=NOW, policy="near_tipoff", game_ids=[GAME], write=False)["forecasts"]}
    for r in rows:
        assert r["game_id"] == GAME and r["cutoff_at"] == NOW and r["generated_at"] == NOW and r["recorded_at"] >= NOW
        assert r["tipoff_at"] == "2026-11-17T23:30:00Z" and abs(r["p_a"] + r["p_mid"] + r["p_b"] - 1) < 1e-6
        if r["market"] == "winner":
            assert r["line"] is None and r["p_mid"] == 0
        else:
            assert r["line"] == served[r["market"]]["line"]
    assert served["spread"]["line"] == -4.5 and served["total"]["line"] == 140.5
    counts_by_spec = {}
    for r in rows:
        counts_by_spec[r["spec_name"]] = counts_by_spec.get(r["spec_name"], 0) + 1
    assert sorted(counts_by_spec.values()) == [1, 2, 3, 3]
    again = nc.record(store, path, policies=policies(), now=NOW)
    assert again["created"] == 0 and again["already_recorded"] == 9
    c = sqlite3.connect(path)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        c.execute("UPDATE challenger_forecasts SET p_a=0.5")


def test_the_replayed_window_is_the_near_tipoff_one(store, tmp_path):
    out = nc.record(store, tmp_path / "c.sqlite", policies=policies(), now="2026-11-17T20:00:00Z")      # tip-off is 3.5 h away
    assert out["created"] == 0 and out["eligible_games"] == 0


def test_a_game_the_served_cycle_refuses_is_not_recorded(store, tmp_path):
    store.execute("DELETE FROM job_runs")                       # no results refresh: the served cycle refuses (stale_results)
    store.commit()
    out = nc.record(store, tmp_path / "c.sqlite", policies=policies(), now=NOW)
    assert out["created"] == 0 and out["eligible_games"] == 0


# ---- fits -------------------------------------------------------------------------------------------------------------
def fits(out):
    return out["fits"]


def test_c1_excludes_the_spent_test_seasons_and_c2_includes_them(store, tmp_path):
    out = nc.record(store, tmp_path / "c.sqlite", policies=policies(), now=NOW, dry_run=True)
    f = fits(out)
    assert f["all"]["n"] > f["exclude_test"]["n"] > 0
    assert f["all"]["sigmas"]["eff_margin"] != f["exclude_test"]["sigmas"]["eff_margin"]
    assert f["exclude_test"]["seasons"] == [2024, 2027] and 2025 in f["all"]["seasons"] and 2013 not in f["all"]["seasons"]
    path = tmp_path / "research" / "challengers.sqlite"
    nc.record(store, path, policies=policies(), now=NOW)
    p = {r["spec_name"]: r for r in rows_of(path) if r["market"] == "winner"}
    assert len(p) == 2 and len({round(r["p_a"], 9) for r in p.values()}) == 2         # C1 and C2 give different probabilities


def test_the_fit_uses_only_results_available_at_the_week_cutoff(store, tmp_path):
    base = fits(nc.record(store, tmp_path / "c.sqlite", policies=policies(), now=NOW, dry_run=True))
    assert base["exclude_test"]["cutoff"] == "2026-11-16T00:00:00Z"
    # result of game 301 became available 2026-11-16T23:00Z: after the week cutoff, so not in the fit but in the rating state
    add_game(store, "950", "2026-11-17T10:00:00Z", "1", "3", "final", 200, 10, available="2026-11-17T12:00:00Z")
    after = fits(nc.record(store, tmp_path / "c.sqlite", policies=policies(), now=NOW, dry_run=True))
    assert after["exclude_test"]["n"] == base["exclude_test"]["n"] and after["exclude_test"]["sigmas"] == base["exclude_test"]["sigmas"]
    # a result that only becomes available after `now` changes nothing at all
    out1 = nc.record(store, tmp_path / "a.sqlite", policies=policies(), now=NOW)
    add_game(store, "951", "2026-11-17T21:00:00Z", "2", "4", "final", 150, 20, available="2026-11-17T23:59:00Z")
    out2 = nc.record(store, tmp_path / "b.sqlite", policies=policies(), now=NOW)
    pa = [(r["spec_name"], r["market"], r["p_a"], r["p_mid"]) for r in rows_of(tmp_path / "a.sqlite")]
    pb = [(r["spec_name"], r["market"], r["p_a"], r["p_mid"]) for r in rows_of(tmp_path / "b.sqlite")]
    assert out1["created"] == out2["created"] == 9 and pa == pb


def test_weekly_refits_are_cached_by_week_and_data_fingerprint(store, tmp_path, monkeypatch):
    path = tmp_path / "research" / "challengers.sqlite"
    cache = path.parent / "challengers" / "ncaab"
    nc.record(store, path, policies=policies(), now=NOW)
    files = sorted(cache.glob("refit-*.json"))
    assert len(files) == 1 and files[0].name.startswith("refit-2026-11-16-")
    monkeypatch.setattr(nc, "_walk_fit", lambda *a, **k: pytest.fail("refit recomputed instead of read from the cache"))
    out = nc.record(store, path, policies=policies(), now=NOW)
    assert out["fits_cache"] == "hit" and out["created"] == 0
    monkeypatch.undo()
    monkeypatch.setattr(college, "VALIDATION_COMPLETE_AT", "9999-01-01T00:00:00Z")
    monkeypatch.setattr(nc, "MIN_FIT_GAMES", 5)
    add_game(store, "960", "2026-11-14T23:00:00Z", "1", "4", "final", 70, 65)       # new data before the week cutoff: new fingerprint
    nc.record(store, path, policies=policies(), now=NOW)
    assert len(sorted(cache.glob("refit-*.json"))) == 2


def test_too_few_fit_games_refuses_the_variant_without_inventing_a_fit(store, tmp_path, monkeypatch):
    monkeypatch.setattr(nc, "MIN_FIT_GAMES", 10 ** 6)
    out = nc.record(store, tmp_path / "c.sqlite", policies=policies(), now=NOW)
    assert out["created"] == 0 and out["status"] == "partial" and all("fit" in v for v in out["skipped"].values()) and len(out["skipped"]) == 4


# ---- read-time evaluation ---------------------------------------------------------------------------------------------
def test_eval_scores_challengers_against_the_served_incumbent_at_read_time(store, tmp_path):
    path = tmp_path / "research" / "challengers.sqlite"
    near_tipoff.predict_now(store, now=NOW, write=True)
    market_live.forecast(store, now=NOW, policy="near_tipoff", game_ids=[GAME], write=True)
    nc.record(store, path, policies=policies(), now=NOW)
    cc = lc.open_ro(path)
    out = le.evaluate(store, cc)
    assert "no claim" in out["label"].lower() and all(g["n_scored"] == 0 and g["n_unsettled"] == 1 for g in out["groups"])
    store.execute("PRAGMA foreign_keys = OFF")
    store.execute("UPDATE games SET status='final', home_score=80, away_score=71, result_available_at='2026-11-18T03:00:00Z', "
                  "result_availability_basis='observed_retrieval' WHERE game_id=?", (GAME,))
    out = le.evaluate(store, cc)
    g = {(x["spec_name"], x["market"]): x for x in out["groups"]}
    assert len(g) == 9 and all(x["n_scored"] == 1 and x["n_no_incumbent"] == 0 for x in g.values())
    w = [x for k, x in g.items() if k[1] == "winner"][0]
    served_p = store.execute("SELECT home_win_prob FROM predictions").fetchone()[0]
    assert w["log_loss_incumbent"] == pytest.approx(-math.log(served_p))                 # home won (80-71)
    assert w["log_loss_diff"] == pytest.approx(w["log_loss_challenger"] - w["log_loss_incumbent"])
    assert all(x["claim"].startswith("no claim") for x in out["groups"])


def test_eval_pairs_only_at_the_served_line(store, tmp_path):
    path = tmp_path / "research" / "challengers.sqlite"
    near_tipoff.predict_now(store, now=NOW, write=True)
    market_live.forecast(store, now=NOW, policy="near_tipoff", game_ids=[GAME], write=True)        # served at total 140.5
    add_quote(store, GAME, "total", 141.5, -110, -110, "2026-11-17T22:10:00Z")                     # the book moves before the challengers run
    nc.record(store, path, policies=policies(), now=NOW)
    assert {r["line"] for r in rows_of(path) if r["market"] == "total"} == {141.5}
    store.execute("PRAGMA foreign_keys = OFF")
    store.execute("UPDATE games SET status='final', home_score=80, away_score=71, result_available_at='2026-11-18T03:00:00Z', "
                  "result_availability_basis='observed_retrieval' WHERE game_id=?", (GAME,))
    g = {(x["spec_name"], x["market"]): x for x in le.evaluate(store, lc.open_ro(path))["groups"]}
    assert all(x["n_no_incumbent"] == 1 and x["n_scored"] == 0 for k, x in g.items() if k[1] == "total")
    assert all(x["n_scored"] == 1 for k, x in g.items() if k[1] != "total")
