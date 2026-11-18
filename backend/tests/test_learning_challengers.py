"""Prospective challengers (app/learning_challengers.py): versioned policy files, a separate append-only store, registration
is never promotion. Fixtures reuse the candidate-pool NHL store; nothing here reads a live store or the network."""
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

import pytest

from app import learning_challengers as lc
from app.candidates import registry
from test_candidate_pool import GID, NOW, TIPOFF, upcoming  # noqa: F401  (fixtures)
from test_live_markets import sealed  # noqa: F401
from test_nhl_eval import nhl_store  # noqa: F401

REF = Path(lc.__file__).parent / "reference" / "learning_policies"


def spec_dict(name="poisson_draw:nhl_base"):
    return asdict(registry.BY_NAME[name])


def prov(**over):
    p = {"source": "existing_pool", "trial_id": None, "commit": None, "trial_decision": None,
         "registered_by": "owner", "registered_at": "2026-11-18T12:00:00Z", "note": "seed from the existing pool"}
    p.update(over)
    return p


def policy_file(tmp_path, challengers, pid="nhl-challengers-v9", sport="nhl", **extra):
    path = tmp_path / f"{pid}.json"
    path.write_text(json.dumps({"policy_id": pid, "sport": sport, "version": 9, "description": "test", "challengers": challengers, **extra}))
    return path


def ch(spec=None, **p):
    return {"spec": spec or spec_dict(), "provenance": prov(**p)}


# ---- policy files ---------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("sport", ["nba", "nhl"])
def test_seeded_policies_load_and_use_only_existing_pool_specs(sport):
    pols = lc.load_policies(sport)
    assert [p.policy_id for p in pols] == [f"{sport}-challengers-v1"]
    for pol in pols:
        assert 1 <= len(pol.challengers) <= 2 and len(pol.sha) == 64
        for c in pol.challengers:
            assert c.provenance["source"] == "existing_pool" and c.spec == registry.BY_NAME[c.spec.name]


def test_discard_trial_needs_an_explicit_note_and_is_never_promotion(tmp_path):
    base = dict(source="autoresearch_trial", trial_id="20261118T085934Z-3f18:1", commit="4e816db", trial_decision="discard")
    with pytest.raises(lc.PolicyError, match="discard.*note"):
        lc.load_policy(policy_file(tmp_path, [ch(**base, note="")]))
    with pytest.raises(lc.PolicyError, match="discard.*note"):
        lc.load_policy(policy_file(tmp_path, [ch(**base, note="   ")]))
    pol = lc.load_policy(policy_file(tmp_path, [ch(**base, note="operator wants prospective evidence on the discarded trial")]))
    assert pol.challengers[0].provenance["trial_decision"] == "discard"
    assert not hasattr(lc, "promote")


@pytest.mark.parametrize("mutate,match", [
    (lambda d: d["challengers"][0]["provenance"].pop("registered_by"), "registered_by"),
    (lambda d: d["challengers"][0]["provenance"].update(source="guess"), "source"),
    (lambda d: d["challengers"][0]["provenance"].update(registered_at="yesterday"), "registered_at"),
    (lambda d: d["challengers"][0]["spec"].update(family="no_such_family"), "family"),
    (lambda d: d["challengers"][0]["spec"].update(features="no_such_features"), "features"),
    (lambda d: d["challengers"][0]["spec"].update(sport="nba"), "sport"),
    (lambda d: d["challengers"][0]["spec"].update(why="edited"), "existing_pool"),
    (lambda d: d.update(policy_id="other-id"), "policy_id"),
    (lambda d: d["challengers"].append(d["challengers"][0]), "duplicate"),
    (lambda d: d["challengers"][0]["provenance"].update(source="autoresearch_trial"), "trial_id"),
])
def test_invalid_policies_are_refused(tmp_path, mutate, match):
    d = json.loads(policy_file(tmp_path, [ch()]).read_text())
    mutate(d)
    path = tmp_path / "nhl-challengers-v9.json"
    path.write_text(json.dumps(d))
    with pytest.raises(lc.PolicyError, match=match):
        lc.load_policy(path)


def test_a_manual_spec_outside_the_registry_is_allowed_with_a_complete_spec(tmp_path):
    spec = {**spec_dict(), "name": "poisson_draw:nhl_base:manual-test", "why": "manual operator spec"}
    pol = lc.load_policy(policy_file(tmp_path, [ch(spec, source="manual")]))
    assert pol.challengers[0].spec.name.endswith("manual-test") and len(pol.challengers[0].spec_hash) == 64


# ---- recorder -------------------------------------------------------------------------------------------------------
LINES = {(GID, "spread"): {"line": -1.5, "source": "core_cohort", "ref": "cohort:1"},
         (GID, "total"): {"line": 6.5, "source": "core_cohort", "ref": "cohort:2"}}


def policy(tmp_path):
    return lc.load_policy(policy_file(tmp_path, [ch(spec_dict("poisson_draw:nhl_base")), ch(spec_dict("multinomial_l2:nhl_base"))]))


def counts(conn):
    return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in ("candidate_forecasts", "predictions", "market_forecasts", "adaptive_forecasts")}


@pytest.fixture
def seeded(upcoming, tmp_path):
    """The stored artifacts the live cycle would have written (made on a copy of the store, so the fixture's rows stay untouched)."""
    from app.candidates import live
    copy = sqlite3.connect(tmp_path / "copy.sqlite")
    copy.row_factory = sqlite3.Row
    upcoming.backup(copy)
    live.record(copy, now=NOW, lines=LINES, write=True, specs=[registry.BY_NAME[n] for n in ("poisson_draw:nhl_base", "multinomial_l2:nhl_base")])
    copy.close()


def test_a_spec_without_a_stored_artifact_is_skipped_never_refitted(upcoming, tmp_path):
    from app.candidates import live
    store = tmp_path / "research" / "challengers.sqlite"
    out = lc.record(upcoming, store, policies=[policy(tmp_path)], now=NOW, lines=LINES)
    assert out["created"] == 0 and out["status"] == "partial"
    assert all("no artifact" in v["all"] for v in out["skipped"].values()) and len(out["skipped"]) == 2
    assert not live.artifact_dir("nhl").exists()          # nothing fitted or written


def test_dry_run_reports_and_writes_nothing(upcoming, seeded, tmp_path):
    store = tmp_path / "research" / "challengers.sqlite"
    before = counts(upcoming)
    out = lc.record(upcoming, store, policies=[policy(tmp_path)], now=NOW, lines=LINES, dry_run=True)
    assert out["dry_run"] is True and out["would_record"] > 0 and out["created"] == 0 and out["errors"] == {}
    assert not store.exists() and counts(upcoming) == before


def test_record_writes_a_separate_append_only_idempotent_store(upcoming, seeded, tmp_path):
    store = tmp_path / "research" / "challengers.sqlite"
    pol = policy(tmp_path)
    before = counts(upcoming)
    dry = lc.record(upcoming, store, policies=[pol], now=NOW, lines=LINES, dry_run=True)
    out = lc.record(upcoming, store, policies=[pol], now=NOW, lines=LINES)
    assert out["created"] == dry["would_record"] > 0 and out["errors"] == {}
    assert counts(upcoming) == before                     # serving, cards, betting, candidate_forecasts untouched
    c = sqlite3.connect(store)
    c.row_factory = sqlite3.Row
    rows = c.execute("SELECT * FROM challenger_forecasts").fetchall()
    assert len(rows) == out["created"]
    r = [x for x in rows if x["market"] == "total"][0]
    assert r["policy_id"] == pol.policy_id and r["policy_sha"] == pol.sha and r["line"] == 6.5 and r["game_id"] == GID
    assert r["spec_hash"] in {x.spec_hash for x in pol.challengers} and json.loads(r["spec_json"])["name"] == r["spec_name"]
    assert r["cutoff_at"] == NOW and r["generated_at"] < r["tipoff_at"] == TIPOFF and r["dataset_version"]
    assert abs(r["p_a"] + r["p_mid"] + r["p_b"] - 1) < 1e-6 and r["definition"]
    assert c.execute("SELECT policy_sha, content FROM challenger_policies").fetchone()["policy_sha"] == pol.sha
    again = lc.record(upcoming, store, policies=[pol], now=NOW, lines=LINES)
    assert again["created"] == 0 and again["already_recorded"] == out["created"]
    later = lc.record(upcoming, store, policies=[pol], now="2026-10-12T22:30:00Z", lines=LINES)
    assert later["created"] == 0                          # one forecast per (policy, spec, game, market, line): the first cutoff wins
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        c.execute("UPDATE challenger_forecasts SET p_a=0.5")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        c.execute("DELETE FROM challenger_forecasts")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        c.execute("UPDATE challenger_policies SET sport='x'")
    row = dict(rows[0])
    row.pop("forecast_id")
    for when in ("2026-10-12T20:59:59Z", TIPOFF):
        row.update(generated_at=when, cutoff_at=when, spec_hash="x" + when)
        with pytest.raises(sqlite3.IntegrityError, match="two hours"):
            c.execute(f"INSERT INTO challenger_forecasts ({','.join(row)}) VALUES ({','.join('?' * len(row))})", list(row.values()))


def test_a_changed_policy_is_a_new_policy_sha_with_its_own_rows(upcoming, seeded, tmp_path):
    store = tmp_path / "research" / "challengers.sqlite"
    a = lc.load_policy(policy_file(tmp_path, [ch(spec_dict("poisson_draw:nhl_base"))]))
    b = lc.load_policy(policy_file(tmp_path, [ch(spec_dict("poisson_draw:nhl_base"), note="a different note")]))
    assert a.sha != b.sha
    n1 = lc.record(upcoming, store, policies=[a], now=NOW, lines=LINES)["created"]
    n2 = lc.record(upcoming, store, policies=[b], now=NOW, lines=LINES)["created"]
    assert n1 == n2 > 0


def test_games_outside_the_two_hour_window_are_not_recorded(upcoming, tmp_path):
    out = lc.record(upcoming, tmp_path / "c.sqlite", policies=[policy(tmp_path)], now="2026-10-12T20:30:00Z", lines=LINES)
    assert out["eligible_games"] == 0 and out["created"] == 0


def test_a_failing_spec_is_reported_and_never_blocks_the_others(upcoming, seeded, tmp_path, monkeypatch):
    real = lc.registry.available
    monkeypatch.setattr(lc.registry, "available", lambda s: False if s.name.startswith("multinomial") else real(s))
    out = lc.record(upcoming, tmp_path / "c.sqlite", policies=[policy(tmp_path)], now=NOW, lines=LINES)
    assert out["created"] > 0 and "multinomial_l2:nhl_base" in out["skipped"]


# ---- review fixes -------------------------------------------------------------------------------------------------------
def test_rows_carry_the_real_write_time_separate_from_the_cutoff(upcoming, seeded, tmp_path):
    store = tmp_path / "research" / "challengers.sqlite"
    lc.record(upcoming, store, policies=[policy(tmp_path)], now=NOW, lines=LINES)
    c = sqlite3.connect(store)
    at = c.execute("SELECT DISTINCT recorded_at FROM challenger_forecasts").fetchall()
    assert len(at) == 1 and at[0][0] > NOW and at[0][0][:4] == str(__import__("datetime").datetime.utcnow().year)


def test_a_replayed_now_is_refused_for_the_default_store_and_allowed_with_an_explicit_store_or_dry_run(tmp_path, monkeypatch, capsys):
    data = tmp_path / "data"
    data.mkdir()
    (data / "nhl.sqlite").write_bytes(b"")
    monkeypatch.setenv("NBA_DATA_DIR", str(data))
    assert lc.main(["record", "--sport", "nhl", "--now", NOW]) == 2
    assert "--store" in capsys.readouterr().out and not (data / "research").exists()
    monkeypatch.setattr(lc.sqlite3, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("reached the recorder")))
    for extra in (["--dry-run"], ["--store", str(tmp_path / "x" / "c.sqlite")]):
        with pytest.raises(RuntimeError, match="reached the recorder"):
            lc.main(["record", "--sport", "nhl", "--now", NOW, *extra])
    with pytest.raises(RuntimeError, match="reached the recorder"):
        lc.main(["record", "--sport", "nhl"])                     # no replay: the default store is fine


def test_the_same_spec_name_with_a_different_definition_is_refused_across_policies(tmp_path):
    other = {**spec_dict(), "why": "a different why: same name, different hash"}
    a = policy_file(tmp_path, [ch(spec_dict("poisson_draw:nhl_base"))], pid="nhl-challengers-v8")
    b = policy_file(tmp_path, [ch(other, source="manual")], pid="nhl-challengers-v9")
    with pytest.raises(lc.PolicyError, match="same name|different definition"):
        lc.load_policies("nhl", tmp_path)
    b.unlink()
    assert len(lc.load_policies("nhl", tmp_path)) == 1
    a.unlink()
    pols = [lc.load_policy(policy_file(tmp_path, [ch(spec_dict("poisson_draw:nhl_base"))], pid="nhl-challengers-v8")),
            lc.load_policy(policy_file(tmp_path, [ch(other, source="manual")], pid="nhl-challengers-v9"))]
    out = lc.record(sqlite3.connect(":memory:"), tmp_path / "c.sqlite", policies=pols, now=NOW)
    assert out["status"] == "failed" and "same name" in out["errors"]["recorder"]


@pytest.mark.parametrize("over", [{"trial_decision": "maybe"}, {"trial_decision": "KEEP"}])
def test_trial_decision_must_be_from_the_fixed_set(tmp_path, over):
    base = dict(source="autoresearch_trial", trial_id="t:1", commit="abc1234", trial_decision="keep", note="n")
    base.update(over)
    with pytest.raises(lc.PolicyError, match="trial_decision"):
        lc.load_policy(policy_file(tmp_path, [ch(**base)]))
    for ok in ("keep", "discard", "crash", "invalid", "n/a"):
        lc.load_policy(policy_file(tmp_path, [ch(**{**base, "trial_decision": ok, "note": "operator note"})]))


@pytest.mark.parametrize("n", [0, -3, 2.5, "50", True])
def test_preregistered_n_is_a_whole_number_of_at_least_one_or_null(tmp_path, n):
    c = ch()
    c["preregistered_n"] = n
    with pytest.raises(lc.PolicyError, match="preregistered_n"):
        lc.load_policy(policy_file(tmp_path, [c]))
    c["preregistered_n"] = 40
    assert lc.load_policy(policy_file(tmp_path, [c])).challengers[0].preregistered_n == 40


def test_the_release_guard_refuses_a_recording_run_from_an_unclean_release_and_allows_a_clean_one(tmp_path, monkeypatch, capsys):
    from app import release
    data = tmp_path / "data"
    data.mkdir()
    (data / "nhl.sqlite").write_bytes(b"")
    monkeypatch.setenv("NBA_DATA_DIR", str(data))
    monkeypatch.setattr(release, "enabled", lambda: True)
    monkeypatch.setattr(release, "check", lambda sport: {"problems": ["checkout has uncommitted changes"]})
    assert lc.main(["record", "--sport", "nhl"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "refused" and "release guard" in out["reason"] and "uncommitted" in out["reason"]
    monkeypatch.setattr(lc.sqlite3, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("reached the recorder")))
    with pytest.raises(RuntimeError, match="reached the recorder"):
        lc.main(["record", "--sport", "nhl", "--dry-run"])           # a dry run writes nothing: not guarded
    monkeypatch.setattr(release, "check", lambda sport: {"problems": []})
    with pytest.raises(RuntimeError, match="reached the recorder"):
        lc.main(["record", "--sport", "nhl"])
