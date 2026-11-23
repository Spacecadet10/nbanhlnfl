"""An invalidated or unverified research record cannot be used: not as the model to beat, not as a reusable result, not in a holdout,
not in forward tracking, and the status page says why. Originals stay byte-identical. Nov 23 2026: the unintended NHL "keep"."""
import hashlib
import json

import pytest

from app import autoresearch as ar, research_audit as ra, research_status
from test_autoresearch import env, git, records  # noqa: F401  (fixture)
from test_research_status import _w, client, exp, fixture_adapter, synthetic  # noqa: F401  (fixtures)

WHY = "2026-11-23 06:00: scored by app 63922174 (delivery), not the pinned fac8c698"


def block(env, status="invalid", trial=None):
    ra.append(env["exp"], status=status, protocol_id=env["meta"]["protocol_id"], sport="nba", reason=WHY, trial_id=trial,
              evidence=[env["root"] / "trials.jsonl"])


def digest(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()
            and p.name != "run.lock"}


def test_an_invalid_keep_is_not_the_model_to_beat_not_reused_and_not_in_the_holdout(env):
    kept = ar.run_trial("nba", env["exp"], env["ws"])
    assert kept["status"] == "keep"                                                  # the situation: a trial marked "kept"
    start = env["meta"]["start_incumbent"]["spread"]["member"]
    before = digest(env["root"])
    block(env, trial=kept["trial_id"])
    assert digest(env["root"]) == before                                              # the original records are untouched
    inc = ar.incumbents(env["root"], "nba", env["meta"])                              # 1. baseline selection
    assert inc["spread"]["trial"] is None and inc["spread"]["member"] == start
    with pytest.raises(ar.Refused, match="no market has a kept trial"):               # 3. holdout
        ar.holdout("nba", env["exp"], env["ws"], ask=lambda p: True)
    assert ar._holdout_log(env["root"]) == []
    again = ar.run_trial("nba", env["exp"], env["ws"])                                # 2. the stored result of this commit is not returned
    assert not again.get("cached") and again["trial_id"] != kept["trial_id"]
    git(env["ws"], "commit", "--allow-empty", "-qm", "same code, new commit")
    block(env, trial=again["trial_id"])
    third = ar.run_trial("nba", env["exp"], env["ws"])                                #    and is not copied to another commit
    assert third.get("cached_from") not in (kept["trial_id"], again["trial_id"])
    assert {r["trial_id"] for r in records(env["root"])} >= {kept["trial_id"], again["trial_id"]}    # all still recorded


def test_an_invalid_trial_does_not_hide_an_earlier_valid_keep_and_its_cached_copies_are_invalid_too(env):
    kept = ar.run_trial("nba", env["exp"], env["ws"])
    git(env["ws"], "commit", "--allow-empty", "-qm", "e1")
    second = ar.run_trial("nba", env["exp"], env["ws"])
    git(env["ws"], "commit", "--allow-empty", "-qm", "e2")
    copy = ar.run_trial("nba", env["exp"], env["ws"])
    assert copy["cached_from"] == second["trial_id"]
    block(env, trial=second["trial_id"])
    assert ar.incumbents(env["root"], "nba", env["meta"])["spread"]["trial"] == kept["trial_id"]
    assert {r["trial_id"] for r in ar._valid_records(env["root"]) if r["event"] == "finished"} == {kept["trial_id"]}


@pytest.mark.parametrize("status", ["invalid", "unverified"])
def test_a_blocked_protocol_refuses_every_use_with_the_reason(env, status):
    ar.run_trial("nba", env["exp"], env["ws"])
    block(env, status)
    for use in (lambda: ar.run_trial("nba", env["exp"], env["ws"]), lambda: ar.status(env["exp"], "nba"),
                lambda: ar.holdout("nba", env["exp"], env["ws"], ask=lambda p: True), lambda: ar.prepare("nba", env["exp"], env["ws"])):
        with pytest.raises(ar.Refused, match="63922174"):
            use()
    if status == "unverified":
        block(env, "verified")
        assert ar.status(env["exp"], "nba")["trials"] == 1


def test_a_damaged_audit_file_refuses_instead_of_meaning_no_invalidations(env):
    ar.run_trial("nba", env["exp"], env["ws"])
    block(env, trial="some-other-trial")
    (env["exp"] / ra.FILE).write_text("{oops\n")
    for use in (lambda: ar.run_trial("nba", env["exp"], env["ws"]), lambda: ar.holdout("nba", env["exp"], env["ws"], ask=lambda p: True),
                lambda: ar.incumbents(env["root"], "nba", env["meta"])):
        with pytest.raises(ar.Refused, match="audit"):
            use()


# --- protocol v3: evaluation, holdout and forward tracking (promotion) ---------------------------------------------------------
def kept_v3(exp):
    s = synthetic(n=400, seed=6, edge=0.8)
    ad = fixture_adapter({"inc": s["inc"], "cand": s["cand"]})
    fn = lambda sport: ad if sport == "nba" else None          # noqa: E731
    st = ar.run_sport_v3("nba", exp, adapter_fn=fn)
    assert st["outcome"] == "evaluated" and st["forward_candidates"]
    return st, fn


@pytest.mark.parametrize("status", ["invalid", "unverified"])
def test_a_blocked_v3_protocol_is_not_tracked_forward_not_rescored_and_not_in_the_holdout(exp, status):
    st, fn = kept_v3(exp)
    assert ar.registered_candidates(exp, "nba") == st["forward_candidates"]
    root = exp / "v3" / st["protocol_id"] / "nba"
    before = {k: v for k, v in digest(root).items() if k.startswith("items") or k == "evaluation.json"}
    ra.append(exp, status=status, protocol_id=st["protocol_id"], sport="nba", reason=WHY)
    assert ar.registered_candidates(exp, "nba") == []
    again = ar.run_sport_v3("nba", exp, adapter_fn=fn)
    assert again["outcome"] == "refused" and "63922174" in again["reason"] and not again.get("forward_candidates")
    with pytest.raises(ar.Refused, match="63922174"):
        ar.holdout_v3("nba", exp, ask=lambda p: True, adapter_fn=fn)
    assert {k: v for k, v in digest(root).items() if k.startswith("items") or k == "evaluation.json"} == before
    if status == "unverified":
        ra.append(exp, status="verified", protocol_id=st["protocol_id"], sport="nba", reason="recomputed under the pin: identical")
        assert ar.run_sport_v3("nba", exp, adapter_fn=fn)["outcome"] == "evaluated"
        assert ar.registered_candidates(exp, "nba") == st["forward_candidates"]


def test_an_older_valid_protocol_is_not_replaced_by_a_newer_blocked_one_and_a_damaged_audit_tracks_nothing(exp):
    st, _ = kept_v3(exp)
    _w(exp / "v3" / "newer" / "nba" / "state.json", {"at": "2999-01-01T00:00:00Z", "forward_candidates": ["bad"], "outcome": "evaluated"})
    assert ar.registered_candidates(exp, "nba") == ["bad"]
    ra.append(exp, status="invalid", protocol_id="newer", sport="nba", reason=WHY)
    assert ar.registered_candidates(exp, "nba") == st["forward_candidates"]
    (exp / ra.FILE).write_text("{oops\n")
    with pytest.raises(ar.Refused, match="audit"):
        ar.registered_candidates(exp, "nba")


# --- the status page -------------------------------------------------------------------------------------------------------
def test_the_status_page_shows_the_invalidation_and_never_the_invalid_keep_as_the_model_to_beat(client, monkeypatch, tmp_path):
    exp = tmp_path / "exp"
    monkeypatch.setenv("NBA_AUTORESEARCH_DIR", str(exp))
    meta = {"sport": "nhl", "start_incumbent": {"reg3": {"member": "goals_draw", "log_loss": 1.03}}, "protocol": {}, "cohort": {}}
    keep = {"event": "finished", "trial_id": "t-keep", "commit": "a" * 40, "status": "keep",
            "markets": {"reg3": {"decision": "keep", "vs_incumbent": {"mean": -0.01}}}}
    _w(exp / "p-bad" / "nhl" / "protocol.json", {**meta, "protocol_id": "p-bad", "registered_at": "2026-11-23T11:07:53Z"})
    _w(exp / "p-bad" / "nhl" / "trials.jsonl", [keep])
    _w(exp / "p-good" / "nhl" / "protocol.json", {**meta, "protocol_id": "p-good", "registered_at": "2026-11-22T00:00:00Z"})
    _w(exp / "p-good" / "nhl" / "trials.jsonl", [{**keep, "trial_id": "t-2"}, {**keep, "trial_id": "t-ok", "status": "discard", "markets": {}}])
    ra.append(exp, status="invalid", protocol_id="p-bad", sport="nhl", reason=WHY)
    ra.append(exp, status="invalid", protocol_id="p-good", sport="nhl", trial_id="t-2", reason=WHY)
    b = client.get("/api/research/autoresearch").json()
    nhl = b["sports"]["nhl"]
    assert nhl["protocol_id"] == "p-good" and nhl["incumbents"]["reg3"]["label"] == "goals_draw"
    assert next(t for t in nhl["recent"] if t["trial_id"] == "t-2")["audit"].startswith("invalid") and nhl["invalidated"] == 1
    assert [(e["status"], e["protocol_id"], e["trial_id"]) for e in b["audit"]["entries"]] == [("invalid", "p-good", "t-2"), ("invalid", "p-bad", None)]
    assert WHY in b["audit"]["entries"][0]["reason"] and b["audit"]["error"] is None
    (exp / ra.FILE).write_text("{oops\n")
    b = client.get("/api/research/autoresearch").json()
    assert b["audit"]["error"] and b["sports"] == {}                                   # nothing is presented as usable


def test_the_v3_status_marks_an_unverified_sport(tmp_path):
    _w(tmp_path / "v3" / "pid" / "nfl" / "state.json", {"sport": "nfl", "at": "2026-11-23T11:15:02Z", "outcome": "evaluated", "protocol_id": "pid"})
    ra.append(tmp_path, status="unverified", protocol_id="pid", sport="nfl", reason=WHY)
    assert research_status.v3_status(tmp_path)["sports"]["nfl"]["audit"].startswith("unverified")
