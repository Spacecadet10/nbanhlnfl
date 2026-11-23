"""app/research_audit.py: append-only, chained, fail-closed audit of research records."""
import json

import pytest

from app import research_audit as ra

P, NHL = "sports-autoresearch-v2-9cb21e25c613", "nhl"


def add(root, status="invalid", pid=P, sport=NHL, **kw):
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


def test_evidence_is_hashed_and_originals_are_only_read(tmp_path):
    f = tmp_path / "trials.jsonl"
    f.write_text('{"a": 1}\n')
    e = add(tmp_path, evidence=[f])
    assert f.read_text() == '{"a": 1}\n' and len(e["evidence"][0]["sha256"]) == 64 and e["evidence"][0]["path"] == str(f)
