"""Prospective NFL weekly challengers (app/nfl_challengers.py): Elo variants (K, home advantage) and points-model variants
(eta, carry) that already exist as configurable parameters, recorded at the served cutoff into the challenger store and scored
at read time against the served NFL forecast. The NFL serving path (nfl_live, coverage, markets, models) is never touched."""
import json
import sqlite3
from pathlib import Path

import pytest

from app import learning_challengers as lc, learning_eval as le, nfl_challengers as nc, nfl_eval, nfl_live
from test_nfl_live import NOW, START, live_store  # noqa: F401

SERVED = ("games", "game_forecasts", "market_forecasts")


def pols():
    p = lc.load_policies("nfl")
    assert len(p) == 1
    return p


def counts(c):
    return {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in SERVED}


def rows_of(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c.execute("SELECT * FROM challenger_forecasts ORDER BY forecast_id").fetchall()


def test_policy_uses_only_parameters_that_already_exist_and_is_labelled_manual():
    pol = pols()[0]
    assert pol.policy_id == "nfl-challengers-v1" and pol.sport == "nfl"
    assert {c.provenance["source"] for c in pol.challengers} == {"manual"}
    assert all(c.provenance["note"].strip() and c.preregistered_n is None for c in pol.challengers)
    heads = {next(iter(c.spec.extra)) for c in pol.challengers}
    assert heads == {"elo", "points"}
    for c in pol.challengers:
        for head, params in c.spec.extra.items():
            grid = nfl_eval.GRID if head == "elo" else nfl_eval.POINTS_GRID
            assert params and all(k in grid and v in grid[k] for k, v in params.items())


@pytest.mark.parametrize("extra,msg", [({"elo": {"k": 17}}, "grid"), ({"elo": {"mov": True}}, "elo"), ({"points": {"eta": 0.2}}, "grid"),
                                       ({"elo": {"k": 25}, "points": {"eta": 0.1}}, "exactly one"), ({"neural": {}}, "extra")])
def test_values_or_families_that_do_not_exist_are_refused(tmp_path, extra, msg):
    pol = json.loads((Path(lc.POLICY_DIR) / "nfl-challengers-v1.json").read_text())
    pol["challengers"] = pol["challengers"][:1]
    pol["challengers"][0]["spec"]["extra"] = extra
    p = tmp_path / "nfl-challengers-v1.json"
    p.write_text(json.dumps(pol))
    with pytest.raises(lc.PolicyError, match=msg):
        lc.load_policy(p)


def test_nothing_is_recorded_before_the_served_cutoff_or_without_the_served_forecast(live_store, tmp_path):
    c = live_store
    assert nc.record(c, tmp_path / "ch.sqlite", policies=pols(), now="2026-11-02T22:14:59Z")["created"] == 0      # before kickoff - 120 min
    out = nc.record(c, tmp_path / "ch.sqlite", policies=pols(), now=NOW)                                       # served has not recorded yet
    assert out["created"] == 0 and any("no served forecast" in v for v in out["skipped"].values())


def test_records_at_the_served_lines_once_without_touching_served_tables(live_store, tmp_path):
    c = live_store
    nfl_live.record(c, now=NOW)
    before = counts(c)
    dry = nc.record(c, tmp_path / "ch.sqlite", policies=pols(), now=NOW, dry_run=True)
    assert dry["would_record"] > 0 and not (tmp_path / "ch.sqlite").exists()
    out = nc.record(c, tmp_path / "ch.sqlite", policies=pols(), now=NOW)
    assert out["created"] == dry["would_record"] and not out["errors"], out
    assert counts(c) == before
    rows = rows_of(tmp_path / "ch.sqlite")
    served = {r["market"]: r for r in c.execute("SELECT * FROM game_forecasts")}
    assert {r["market"] for r in rows} == set(served)
    for r in rows:
        s = served[r["market"]]
        assert r["line"] == s["line"] and r["generated_at"] == NOW and r["policy_id"] == "nfl-challengers-v1"
        assert abs(r["p_a"] + r["p_mid"] + r["p_b"] - 1) < 1e-6
    assert nc.record(c, tmp_path / "ch.sqlite", policies=pols(), now="2026-11-02T23:01:00Z")["created"] == 0


def test_each_variant_differs_from_the_served_forecast(live_store, tmp_path):
    c = live_store
    nfl_live.record(c, now=NOW)
    nc.record(c, tmp_path / "ch.sqlite", policies=pols(), now=NOW)
    rows = rows_of(tmp_path / "ch.sqlite")
    served = {r["market"]: r["p_a"] for r in c.execute("SELECT * FROM game_forecasts")}
    names = {r["spec_name"] for r in rows}
    assert names == {ch.spec.name for ch in pols()[0].challengers}
    diffs = [r for r in rows if abs(r["p_a"] - served[r["market"]]) > 1e-9]
    assert diffs


def test_evaluation_scores_against_the_served_row_and_claims_nothing(live_store, tmp_path):
    c = live_store
    nfl_live.record(c, now=NOW)
    nc.record(c, tmp_path / "ch.sqlite", policies=pols(), now=NOW)
    cc = lc.open_ro(tmp_path / "ch.sqlite")
    out = le.evaluate(c, cc)
    assert out["groups"] and all(g["n_scored"] == 0 and g["n_unsettled"] == 1 for g in out["groups"])
    c.execute("UPDATE games SET status='final', home_score=27, away_score=20, result_available_at='2026-11-03T04:00:00Z' WHERE game_id='nfl:live'")
    out = le.evaluate(c, cc)
    assert all(g["n_scored"] == 1 and g["n_no_incumbent"] == 0 for g in out["groups"]), out["groups"]
    assert all("no claim" in g["claim"] and g["log_loss_diff"] is not None for g in out["groups"])


def test_a_dry_run_does_not_write_the_nfl_access_log_and_the_latest_served_row_wins(live_store, tmp_path):
    c = live_store
    nfl_live.record(c, now=NOW)
    log = nfl_eval.access_log_path()
    size = log.stat().st_size if log.exists() else 0
    nc.record(c, tmp_path / "ch.sqlite", policies=pols(), now=NOW, dry_run=True)
    assert (log.stat().st_size if log.exists() else 0) == size
    mine = nc.challenger_access_log_path()
    assert not mine.exists()
    nc.record(c, tmp_path / "ch.sqlite", policies=pols(), now=NOW)
    assert mine.is_file() and len(mine.read_text().splitlines()) == 1        # a real run is logged, in the challenger log
    assert (log.stat().st_size if log.exists() else 0) == size               # the official test-access log is not touched
    nc.record(c, tmp_path / "ch3.sqlite", policies=pols(), now=NOW)
    assert len(mine.read_text().splitlines()) == 1                           # the same policies and games are not logged again
    # a later served row for the same market with another line is the one that is paired
    c.execute("PRAGMA foreign_keys = OFF")
    c.execute("DROP TRIGGER IF EXISTS game_forecasts_no_update")
    row = c.execute("SELECT * FROM game_forecasts WHERE market='total'").fetchone()
    cols = [k for k in row.keys() if k != "forecast_id"]
    vals = {k: row[k] for k in cols}
    vals.update(idempotency_key="later-row", line=row["line"] + 1, generated_at="2026-11-02T23:00:30Z", source_generated_at="2026-11-02T23:00:30Z")
    c.execute(f"INSERT INTO game_forecasts ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", [vals[k] for k in cols])
    nc.record(c, tmp_path / "ch2.sqlite", policies=pols(), now="2026-11-02T23:01:00Z")
    assert {r["line"] for r in rows_of(tmp_path / "ch2.sqlite") if r["market"] == "total"} == {row["line"] + 1}
