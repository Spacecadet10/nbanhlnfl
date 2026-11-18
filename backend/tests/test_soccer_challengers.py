"""Prospective soccer spec challengers (app/soccer_challengers.py): new SPECS on the served Poisson goals joint, not refits of the
sealed models. Recorded point-in-time into the shared challenger store, scored at read time against the served incumbent.
Synthetic store (test_soccer_markets_live fixture), the real model code, no network."""
import json
import sqlite3
from pathlib import Path

import pytest

from app import learning_challengers as lc, learning_eval as le, soccer_challengers as sc, soccer_live, soccer_markets as sm
from app import soccer_markets_eval as ev, soccer_markets_live as sml
from test_soccer_markets_live import IDS, SEASON, STAMP, TODAY, TOMORROW, final, ingest_day, live  # noqa: F401

SERVED = ("soccer_matches", "soccer_market_forecasts", "soccer_live_forecasts", "soccer_market_checks")
OLD_SCHEMA = """
CREATE TABLE challenger_policies (policy_sha TEXT PRIMARY KEY, policy_id TEXT NOT NULL, sport TEXT NOT NULL, content TEXT NOT NULL, first_seen_at TEXT NOT NULL);
CREATE TABLE challenger_forecasts (
  forecast_id INTEGER PRIMARY KEY, policy_id TEXT NOT NULL, policy_sha TEXT NOT NULL, spec_name TEXT NOT NULL,
  spec_json TEXT NOT NULL, spec_hash TEXT NOT NULL, definition TEXT NOT NULL, game_id TEXT NOT NULL,
  market TEXT NOT NULL CHECK (market IN ('winner','reg3','spread','total','team_total_home','team_total_away')),
  line REAL, p_a REAL NOT NULL CHECK (p_a BETWEEN 0 AND 1), p_mid REAL NOT NULL CHECK (p_mid BETWEEN 0 AND 1),
  p_b REAL NOT NULL CHECK (p_b BETWEEN 0 AND 1), cutoff_at TEXT NOT NULL, tipoff_at TEXT NOT NULL, generated_at TEXT NOT NULL,
  dataset_version TEXT NOT NULL, model_version TEXT NOT NULL, recorded_at TEXT,
  CHECK (abs(p_a + p_mid + p_b - 1) < 1e-6), CHECK ((market IN ('winner','reg3')) = (line IS NULL)),
  UNIQUE (policy_sha, spec_hash, game_id, market, cutoff_at));
CREATE TRIGGER challenger_forecasts_no_delete BEFORE DELETE ON challenger_forecasts
BEGIN SELECT RAISE(ABORT, 'challenger_forecasts are append-only'); END;
CREATE TRIGGER challenger_forecasts_two_hour_window BEFORE INSERT ON challenger_forecasts
WHEN NEW.generated_at < strftime('%Y-%m-%dT%H:%M:%SZ', NEW.tipoff_at, '-2 hours') OR NEW.generated_at >= NEW.tipoff_at
BEGIN SELECT RAISE(ABORT, 'challenger forecast must be within two hours before tipoff and strictly before it'); END;
"""


def pols():
    p = lc.load_policies("soccer")
    assert len(p) == 1
    return p


def serve(live):
    """The served goals forecast (incumbent) for tomorrow's two matches, plus a served 1X2 row for the first."""
    rep = sml.run(live, day=TOMORROW.isoformat())
    assert rep["status"] == "ok", rep
    cut = f"{TOMORROW.isoformat()}T06:00:00Z"
    soccer_live.record(live, "soccer:eng.1:90003", "eng.1", (0.5, 0.25, 0.25), cutoff_at=cut, inputs_digest="d",
                       inputs_through=STAMP, retrieved_at=STAMP)


def counts(c):
    return {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in SERVED}


def run(live, tmp_path, **kw):
    from app.db import utc_now
    return sc.record(live, tmp_path / "ch.sqlite", policies=pols(), now=utc_now(), day=TOMORROW.isoformat(), **kw)


def rows_of(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c.execute("SELECT * FROM challenger_forecasts ORDER BY forecast_id").fetchall()


# ---- policy ---------------------------------------------------------------------------------------------------------
def test_seeded_policy_has_three_specs_each_one_known_change_with_manual_provenance():
    pol = pols()[0]
    assert pol.policy_id == "soccer-challengers-v1" and pol.sport == "soccer"
    names = [c.spec.name for c in pol.challengers]
    assert len(names) == len(set(names)) == 3
    assert {c.provenance["source"] for c in pol.challengers} == {"manual"}
    assert all(c.provenance["note"].strip() and c.preregistered_n is None for c in pol.challengers)
    kinds = sorted(next(iter(c.spec.extra)) for c in pol.challengers)
    assert kinds == ["dispersion", "dixon_coles", "half_life_days"]


@pytest.mark.parametrize("extra,msg", [({"half_life_days": 90}, "half_life"), ({"dispersion": "team"}, "dispersion"),
                                       ({"dixon_coles": "yes"}, "dixon_coles"), ({}, "at least one"), ({"asian": 1}, "extra")])
def test_a_spec_outside_the_existing_pieces_is_refused(tmp_path, extra, msg):
    pol = json.loads((Path(lc.POLICY_DIR) / "soccer-challengers-v1.json").read_text())
    pol["challengers"] = pol["challengers"][:1]
    pol["challengers"][0]["spec"]["extra"] = extra
    p = tmp_path / "soccer-challengers-v1.json"
    p.write_text(json.dumps(pol))
    with pytest.raises(lc.PolicyError, match=msg):
        lc.load_policy(p)


# ---- recording ------------------------------------------------------------------------------------------------------
def test_records_served_markets_only_once_and_never_writes_served_tables(live, tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "fair_line", lambda joint, market: 1.5 if market != "goals_total" else 2.5)      # synthetic fair lines can be quarter lines
    serve(live)
    before = counts(live)
    dry = run(live, tmp_path, dry_run=True)
    assert dry["status"] in ("success", "partial") and dry["would_record"] > 0 and not (tmp_path / "ch.sqlite").exists()
    out = run(live, tmp_path)
    assert out["status"] in ("success", "partial") and out["created"] == dry["would_record"] and not out["errors"], out
    assert counts(live) == before
    rows = rows_of(tmp_path / "ch.sqlite")
    markets = {r["market"] for r in rows}
    assert {"soccer_goals_total", "soccer_goals_home", "soccer_goals_away", "soccer_btts", "soccer_1x2"} <= markets
    assert {r["spec_name"] for r in rows} == {c.spec.name for c in pols()[0].challengers}
    for r in rows:
        assert abs(r["p_a"] + r["p_mid"] + r["p_b"] - 1) < 1e-6 and r["generated_at"] < r["tipoff_at"]
        assert r["cutoff_at"] == f"{TOMORROW.isoformat()}T06:00:00Z" and r["recorded_at"] and r["generated_at"] != r["cutoff_at"] and r["policy_id"] == "soccer-challengers-v1"
        assert (r["line"] is None) == (r["market"] in ("soccer_1x2", "soccer_btts"))
        assert r["line"] is None or (r["line"] * 2) % 1 == 0          # half and whole lines only
    again = run(live, tmp_path)
    assert again["created"] == 0 and again["already_recorded"] == out["created"]
    assert len(rows_of(tmp_path / "ch.sqlite")) == out["created"]


def test_lines_are_the_served_lines_and_1x2_needs_the_served_1x2(live, tmp_path):
    serve(live)
    run(live, tmp_path)
    rows = rows_of(tmp_path / "ch.sqlite")
    served = {r["match_id"]: json.loads(r["joint_json"]) for r in live.execute("SELECT * FROM soccer_market_forecasts WHERE family='goals'")}
    for r in rows:
        if r["market"] == "soccer_goals_total":
            assert r["line"] == sm.fair_line(served[r["game_id"]], "goals_total")
    assert {r["game_id"] for r in rows if r["market"] == "soccer_1x2"} == {"soccer:eng.1:90003"}     # only this match has a served 1X2
    assert {r["game_id"] for r in rows} == {"soccer:eng.1:90003", "soccer:eng.1:90004"}


def test_no_served_goals_forecast_means_nothing_recorded(live, tmp_path):
    out = run(live, tmp_path)
    assert out["created"] == 0 and any("no served goals forecast" in v for v in out["skipped"].values())


def test_quarter_lines_are_not_representable_and_are_skipped_visibly(live, tmp_path, monkeypatch):
    serve(live)
    monkeypatch.setattr(sm, "fair_line", lambda joint, market: 2.75)
    out = run(live, tmp_path)
    assert out["skipped_quarter_lines"] > 0
    assert not [r for r in rows_of(tmp_path / "ch.sqlite") if r["market"] == "soccer_goals_total"]


def test_stale_inputs_refuse_like_the_served_run_and_record_nothing(live, tmp_path, monkeypatch):
    serve(live)
    monkeypatch.setattr(sml.soccer, "last_refresh_at", lambda *_: "2020-01-01T00:00:00Z")
    out = sc.record(live, tmp_path / "ch.sqlite", policies=pols(), now=STAMP, day=TOMORROW.isoformat())
    assert out["created"] == 0 and out["status"] == "failed" and "older than 26 h" in json.dumps(out["errors"])
    assert not (tmp_path / "ch.sqlite").exists() or not rows_of(tmp_path / "ch.sqlite")


# ---- the fits are point in time and each variant really differs --------------------------------------------------------
def test_inputs_exclude_results_available_after_the_cutoff():
    day = "2026-11-20"
    mk = lambda mid, d, av: ev.Obs(mid, "eng.1", 2026, d, "1", "2", av, 1, 0)
    data = ev.Data("goals", [mk("a", "2026-11-10", "2026-11-10T22:00:00Z"), mk("b", "2026-11-19", "2026-11-19T22:00:00Z"),
                             mk("late", "2026-11-19", "2026-11-20T07:00:00Z"), mk("same_day", "2026-11-20", "2026-11-20T05:00:00Z")], {}, 2026, {})
    assert [o.mid for o in sc.day_inputs(data, day)] == ["a", "b"]


def test_each_variant_changes_the_joint_and_the_sealed_means_are_shared(live, tmp_path):
    serve(live)
    data = ev.collect(live, "goals", SEASON)
    targets = sml.targets_on(live, ("eng.1",), TOMORROW.isoformat())
    sealed = sml.sealed("goals")
    base = {r["mid"]: r for r in ev.forecast_targets(sealed, data, targets)}
    by_name = {c.spec.name: c.spec for c in pols()[0].challengers}
    fit = {n: sc.joints(sp, sealed, data, TOMORROW.isoformat(), targets) for n, sp in by_name.items()}
    for n, sp in by_name.items():
        for t in targets:
            j = fit[n][t.mid]["joint"]
            assert abs(float(j.sum()) - 1) < 1e-9 and j.shape == base[t.mid]["joint"].shape
            assert abs(j - base[t.mid]["joint"]).max() > 1e-6, f"{n} equals the sealed Poisson"
    dc = next(n for n, sp in by_name.items() if "dixon_coles" in sp.extra)
    for t in targets:
        assert fit[dc][t.mid]["mh"] == pytest.approx(base[t.mid]["mh"])      # same means; only the low-score cells move


# ---- shared store migration -------------------------------------------------------------------------------------------
def test_an_older_store_is_migrated_keeping_rows_ids_and_append_only_triggers(tmp_path):
    p = tmp_path / "old.sqlite"
    c = sqlite3.connect(p, isolation_level=None)
    c.executescript(OLD_SCHEMA)
    c.execute("INSERT INTO challenger_forecasts (policy_id, policy_sha, spec_name, spec_json, spec_hash, definition, game_id, market, line, "
              "p_a, p_mid, p_b, cutoff_at, tipoff_at, generated_at, dataset_version, model_version) VALUES "
              "('p','s','n','{}','h','d','g1','winner',NULL,.6,0,.4,'2026-11-18T10:00:00Z','2026-11-18T11:00:00Z','2026-11-18T10:00:00Z','v','m')")
    assert "soccer_1x2" not in c.execute("SELECT sql FROM sqlite_master WHERE name='challenger_forecasts'").fetchone()[0]
    c.close()
    s = lc.open_store(p)
    assert s.execute("SELECT forecast_id, game_id FROM challenger_forecasts").fetchall()[0][:2] == (1, "g1")
    assert "soccer_1x2" in s.execute("SELECT sql FROM sqlite_master WHERE name='challenger_forecasts'").fetchone()[0]
    s.execute("INSERT INTO challenger_forecasts (policy_id, policy_sha, spec_name, spec_json, spec_hash, definition, game_id, market, line, "
              "p_a, p_mid, p_b, cutoff_at, tipoff_at, generated_at, dataset_version, model_version) VALUES "
              "('p','s','n','{}','h','d','g2','soccer_btts',NULL,.5,0,.5,'2026-11-18T06:00:00Z','2026-11-18T20:00:00Z','2026-11-18T06:00:00Z','v','m')")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        s.execute("DELETE FROM challenger_forecasts")
    with pytest.raises(sqlite3.DatabaseError, match="two hours"):          # the two-hour rule still holds for every other market
        s.execute("INSERT INTO challenger_forecasts (policy_id, policy_sha, spec_name, spec_json, spec_hash, definition, game_id, market, line, "
                  "p_a, p_mid, p_b, cutoff_at, tipoff_at, generated_at, dataset_version, model_version) VALUES "
                  "('p','s','n','{}','h2','d','g3','winner',NULL,.5,0,.5,'2026-11-18T06:00:00Z','2026-11-18T20:00:00Z','2026-11-18T06:00:00Z','v','m')")
    s.close()
    again = lc.open_store(p)          # idempotent
    assert again.execute("SELECT count(*) FROM challenger_forecasts").fetchone()[0] == 2


# ---- read-time evaluation vs the served incumbent -----------------------------------------------------------------------
def test_evaluation_pairs_with_the_served_forecast_derives_double_chance_and_claims_nothing(live, tmp_path):
    serve(live)
    run(live, tmp_path)
    cc = lc.open_ro(tmp_path / "ch.sqlite")
    out = le.evaluate(live, cc)
    assert all(g["n_scored"] == 0 and g["n_unsettled"] >= 1 for g in out["groups"]) and out["groups"]
    ingest_day(live, TOMORROW, [final(90003, TOMORROW, IDS[0], IDS[2], 2, 1), final(90004, TOMORROW, IDS[4], IDS[5], 0, 0)],
               at=f"{TOMORROW.isoformat()}T23:00:00Z")
    out = le.evaluate(live, cc)
    g = {(x["spec_name"], x["market"]): x for x in out["groups"]}
    total = [x for x in out["groups"] if x["market"] == "soccer_goals_total"]
    assert total and all(x["n_scored"] == 2 and x["n_no_incumbent"] == 0 for x in total)
    assert all("no claim" in x["claim"] for x in out["groups"])
    x12 = [x for x in out["groups"] if x["market"] == "soccer_1x2"]
    assert x12 and all(x["n_scored"] == 1 for x in x12)                  # only the match with a served 1X2
    dc = [x for x in out["groups"] if x["market"] == "soccer_double_chance"]
    assert dc and all(x["n_scored"] == 1 for x in dc)                    # derived from the 1X2 row at read time, never stored
    assert not [r for r in rows_of(tmp_path / "ch.sqlite") if "double_chance" in r["market"]]
    assert all(x["log_loss_diff"] is not None for x in total)
    assert g


def _old_store(p):
    c = sqlite3.connect(p, isolation_level=None)
    c.executescript(OLD_SCHEMA)
    c.close()


def test_migration_restores_index_and_triggers_atomically_and_blocks_update_delete_and_late_rows(tmp_path):
    p = tmp_path / "old.sqlite"
    _old_store(p)
    s = lc.open_store(p)
    names = {r[0] for r in s.execute("SELECT name FROM sqlite_master WHERE tbl_name IN ('challenger_forecasts','challenger_policies')")}
    assert {"challenger_forecasts_game", "challenger_forecasts_no_update", "challenger_forecasts_no_delete", "challenger_policies_no_update",
            "challenger_policies_no_delete", "challenger_forecasts_two_hour_window"} <= names
    ins = ("INSERT INTO challenger_forecasts (policy_id, policy_sha, spec_name, spec_json, spec_hash, definition, game_id, market, line, "
           "p_a, p_mid, p_b, cutoff_at, tipoff_at, generated_at, dataset_version, model_version) VALUES "
           "('p','s','n','{}','h','d','g',?,NULL,.5,0,.5,'c',?,?,'v','m')")
    s.execute(ins, ("soccer_btts", "2026-11-18T20:00:00Z", "2026-11-18T06:00:00Z"))
    with pytest.raises(sqlite3.DatabaseError, match="kickoff|two hours"):
        s.execute(ins, ("soccer_btts", "2026-11-18T20:00:00Z", "2026-11-18T20:00:00Z"))          # at kickoff
    with pytest.raises(sqlite3.DatabaseError, match="kickoff|two hours"):
        s.execute(ins, ("soccer_btts", "2026-11-18T20:00:00Z", "2026-11-18T21:00:00Z"))          # after kickoff
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        s.execute("UPDATE challenger_forecasts SET p_a=.4, p_b=.6")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        s.execute("DELETE FROM challenger_forecasts")


def test_a_second_opener_does_not_repeat_the_rebuild(tmp_path, monkeypatch):
    p = tmp_path / "old.sqlite"
    _old_store(p)
    lc.open_store(p).close()
    c = sqlite3.connect(p, isolation_level=None)
    root = lambda: c.execute("SELECT rootpage FROM sqlite_master WHERE name='challenger_forecasts'").fetchone()[0]
    before = root()
    real, calls = lc._needs_migration, []
    monkeypatch.setattr(lc, "_needs_migration", lambda conn: calls.append(1) or (len(calls) == 1 or real(conn)))     # stale first look
    lc._migrate(c)
    assert len(calls) == 2 and root() == before                      # re-read under the lock: no rebuild


def test_draw_no_bet_is_scored_from_the_1x2_triple_with_draws_void(live, tmp_path):
    serve(live)
    run(live, tmp_path)
    cc = lc.open_ro(tmp_path / "ch.sqlite")
    ingest_day(live, TOMORROW, [final(90003, TOMORROW, IDS[0], IDS[2], 1, 1), final(90004, TOMORROW, IDS[4], IDS[5], 0, 0)],
               at=f"{TOMORROW.isoformat()}T23:00:00Z")
    out = le.evaluate(live, cc)
    dnb = [x for x in out["groups"] if x["market"] == "soccer_draw_no_bet"]
    assert dnb and all(x["n_scored"] == 0 and x["n_void"] == 1 for x in dnb)                    # the only 1X2-served match drew
    ingest_day(live, TOMORROW, [final(90003, TOMORROW, IDS[0], IDS[2], 2, 1)], at=f"{TOMORROW.isoformat()}T23:30:00Z")
    out = le.evaluate(live, cc)
    dnb = [x for x in out["groups"] if x["market"] == "soccer_draw_no_bet"]
    inc = 0.5 / (0.5 + 0.25)                                                                   # served Elo (0.5, 0.25, 0.25), home won
    assert all(x["n_scored"] == 1 and x["log_loss_incumbent"] == pytest.approx(-__import__("math").log(inc)) for x in dnb)
