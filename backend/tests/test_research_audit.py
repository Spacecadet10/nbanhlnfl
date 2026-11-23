"""app/research_audit.py: append-only, chained, fail-closed audit of research records."""
import json

import pytest

from app import research_audit as ra

P, NHL = "sports-autoresearch-v2-9cb21e25c613", "nhl"


def add(root, status="invalid", pid=P, sport=NHL, **kw):
    d = root / pid / sport
    d.mkdir(parents=True, exist_ok=True)
    if kw.get("trial_id"):
        with (d / "trials.jsonl").open("a") as fh:
            fh.write(json.dumps({"trial_id": kw["trial_id"]}) + "\n")
    return ra.append(root, status=status, protocol_id=pid, sport=sport, reason=kw.pop("reason", "made by unintended code"), **kw)


def test_no_file_is_no_entry_and_an_invalid_protocol_blocks_every_trial_of_it(tmp_path):
    assert ra.load(tmp_path) == [] and ra.verdict([], P, NHL) is None
    add(tmp_path)
    e = ra.load(tmp_path)
    assert ra.verdict(e, P, NHL)["status"] == "invalid" and ra.verdict(e, P, NHL, "t1")["status"] == "invalid"
    assert ra.verdict(e, P, "nba") is None and ra.verdict(e, "other", NHL) is None


def test_an_invalid_trial_blocks_itself_only_and_verified_never_lifts_invalid(tmp_path):
    add(tmp_path, trial_id="t1")
    add(tmp_path, "verified", trial_id="t1")
    e = ra.load(tmp_path)
    assert ra.verdict(e, P, NHL, "t1")["status"] == "invalid"
    assert ra.verdict(e, P, NHL, "t2") is None and ra.verdict(e, P, NHL) is None


def test_unverified_blocks_until_the_same_scope_is_verified(tmp_path):
    add(tmp_path, "unverified", sport="nfl")
    assert ra.verdict(ra.load(tmp_path), P, "nfl")["status"] == "unverified"
    add(tmp_path, "verified", sport="nfl", trial_id="x")                  # another scope
    assert ra.verdict(ra.load(tmp_path), P, "nfl") is not None
    add(tmp_path, "verified", sport="nfl")
    assert ra.verdict(ra.load(tmp_path), P, "nfl") is None
    add(tmp_path, "unverified", sport="nfl")
    assert ra.verdict(ra.load(tmp_path), P, "nfl") is not None             # a later problem blocks again


@pytest.mark.parametrize("damage", ["edit", "drop_first", "swap", "truncate", "garbage", "bad_status", "no_reason", "drop_last", "deleted", "no_head"])
def test_a_changed_removed_reordered_or_malformed_entry_makes_the_file_unusable(tmp_path, damage):
    for i in range(3):
        add(tmp_path, trial_id=f"t{i}")
    p = tmp_path / ra.FILE
    lines = p.read_text().splitlines()
    if damage == "edit":
        lines[0] = lines[0].replace("t0", "t9")
    elif damage == "drop_first":
        lines = lines[1:]
    elif damage == "drop_last":
        lines = lines[:-1]
    elif damage == "swap":
        lines[0], lines[1] = lines[1], lines[0]
    elif damage == "garbage":
        lines.append("{oops")
    elif damage in ("bad_status", "no_reason"):
        e = json.loads(lines[-1])
        e.update({"status": "fine"} if damage == "bad_status" else {"reason": " "})
        lines[-1] = json.dumps(e, sort_keys=True)
    p.write_text("\n".join(lines) + ("" if damage == "truncate" else "\n"))
    if damage == "deleted":
        p.unlink()
    elif damage == "no_head":
        (tmp_path / ra.HEAD).unlink()
    with pytest.raises(ra.AuditError):
        ra.load(tmp_path)
    if damage != "truncate":
        with pytest.raises(ra.AuditError):
            add(tmp_path)                                                   # nothing is appended to a broken file


@pytest.mark.parametrize("seq,trial_blocked,protocol_blocked", [
    ([("unverified", None), ("unverified", "T"), ("verified", "T")], True, True),          # the protocol is still unverified
    ([("unverified", "T"), ("unverified", None), ("verified", None)], True, False),         # T was never verified
    ([("unverified", "T"), ("unverified", None), ("verified", None), ("verified", "T")], False, False),
    ([("unverified", None), ("invalid", "T"), ("verified", None)], True, False)])
def test_overlapping_scopes_are_lifted_one_by_one(tmp_path, seq, trial_blocked, protocol_blocked):
    for status, trial in seq:
        add(tmp_path, status, trial_id=trial)
    e = ra.load(tmp_path)
    assert (ra.verdict(e, P, NHL, "T") is not None, ra.verdict(e, P, NHL) is not None) == (trial_blocked, protocol_blocked)


def test_an_entry_about_records_that_do_not_exist_is_refused(tmp_path):
    add(tmp_path)
    for kw in (dict(pid="sports-autoresearch-v2-typo", sport=NHL), dict(pid=P, sport="NHL"), dict(pid=P, sport=NHL, trial_id="nope")):
        with pytest.raises(ValueError):
            ra.append(tmp_path, status="invalid", protocol_id=kw["pid"], sport=kw["sport"], reason="x", trial_id=kw.get("trial_id"))
    assert len(ra.load(tmp_path)) == 1


def test_a_crash_between_the_line_and_the_head_neither_hides_the_entry_nor_blocks_the_next(tmp_path):
    add(tmp_path, trial_id="t1")
    head = (tmp_path / ra.HEAD).read_text()
    add(tmp_path, trial_id="t2")
    (tmp_path / ra.HEAD).write_text(head)                                    # the second head was never written
    assert ra.verdict(ra.load(tmp_path), P, NHL, "t2")
    add(tmp_path, trial_id="t3")
    assert len(ra.load(tmp_path)) == 3
    (tmp_path / ra.HEAD).write_text(head)                                    # two behind is damage, not a crash
    with pytest.raises(ra.AuditError):
        ra.load(tmp_path)


def test_readers_never_see_half_an_append(tmp_path):
    import threading
    add(tmp_path)
    stop, errors = threading.Event(), []

    def read():
        while not stop.is_set():
            try:
                ra.load(tmp_path)
            except ra.AuditError as exc:
                errors.append(exc)
    t = threading.Thread(target=read)
    t.start()
    for _ in range(60):
        add(tmp_path)
    stop.set()
    t.join()
    assert errors == [] and len(ra.load(tmp_path)) == 61


@pytest.mark.parametrize("how", ["both_deleted", "emptied", "empty_with_head", "root_gone"])
def test_a_pin_next_to_the_root_notices_a_deleted_audit(tmp_path, how):
    exp = tmp_path / "experiments"
    exp.mkdir()
    add(exp)
    (tmp_path / "PIN.json").write_text(json.dumps({"audit_head": ra.load(exp)[0]["sha"]}))
    add(exp)
    assert len(ra.load(exp)) == 2
    if how == "root_gone":
        exp.rename(tmp_path / "gone")
    else:
        (exp / ra.HEAD).unlink()
        (exp / ra.FILE).unlink() if how == "both_deleted" else (exp / ra.FILE).write_text("")
        if how == "empty_with_head":
            (exp / ra.HEAD).write_text("0 " + "0" * 64)
    with pytest.raises(ra.AuditError):
        ra.load(exp)


def test_evidence_is_hashed_and_originals_are_only_read(tmp_path):
    f = tmp_path / "trials.jsonl"
    f.write_text('{"a": 1}\n')
    e = add(tmp_path, evidence=[f])
    assert f.read_text() == '{"a": 1}\n' and len(e["evidence"][0]["sha256"]) == 64 and e["evidence"][0]["path"] == str(f)
