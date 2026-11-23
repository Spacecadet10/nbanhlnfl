"""Append-only audit of the research records (<experiment root>/audit.jsonl): which protocols, trials or runs must not be used.

A record is never edited or deleted. An entry says what it is about (`scope`), what it is now (`status`) and why, with the sha256
of the original files it judges. Entries are chained (`prev` = sha256 of the previous line), so an edited, removed or reordered
line makes the whole file unusable and every reader refuses (AuditError), never "no invalidations". `audit.head` (count and last
sha, rewritten after each entry) makes a deleted file or removed last entries an error too.

status: 'invalid'     never usable again: not a model to beat, not a reusable result, not in a holdout, not in forward tracking
        'unverified'  made by unintended code; not usable until a later 'verified' entry for the same scope
        'verified'    the recomputation under the intended code matched; lifts 'unverified', never 'invalid'
scope:  {protocol_id, sport} = every record of that protocol and sport; plus trial_id = that trial (and results copied from it).

Standard library only: scripts/research_runner.py reads the same file outside the evaluator.
"""
import fcntl
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

FILE = "audit.jsonl"
HEAD = "audit.head"
STATUSES = ("invalid", "unverified", "verified")
FIRST = "0" * 64


class AuditError(Exception):
    """The audit file is unreadable, malformed or its chain is broken: nothing may be selected from these records."""


def line_sha(line: str) -> str:
    return hashlib.sha256(line.encode()).hexdigest()


def load(exp_root) -> list:
    """Every entry, oldest first, with its line sha as `sha`. [] when the root has no audit file."""
    p = Path(exp_root) / FILE
    try:
        head = (Path(exp_root) / HEAD).read_text().split()
    except FileNotFoundError:
        head = None
    except (OSError, UnicodeDecodeError) as exc:
        raise AuditError(f"{Path(exp_root) / HEAD} cannot be read: {exc}") from exc
    try:
        text = p.read_text()
    except FileNotFoundError:
        if head is not None:
            raise AuditError(f"{p} is missing but {HEAD} says it existed") from None
        return []
    except (OSError, UnicodeDecodeError) as exc:
        raise AuditError(f"{p} cannot be read: {exc}") from exc
    out, prev = [], FIRST
    for n, line in enumerate(text.splitlines(), 1):
        try:
            e = json.loads(line)
            scope = e["scope"]
            ok = (e["prev"] == prev and e["status"] in STATUSES and isinstance(e["reason"], str) and e["reason"].strip()
                  and isinstance(scope["protocol_id"], str) and isinstance(scope["sport"], str)
                  and (scope.get("trial_id") is None or isinstance(scope["trial_id"], str)))
        except (ValueError, KeyError, TypeError) as exc:
            raise AuditError(f"{p} line {n} is malformed") from exc
        if not ok:
            raise AuditError(f"{p} line {n} is malformed or the chain is broken")
        prev = line_sha(line)
        out.append({**e, "sha": prev})
    if text and not text.endswith("\n"):
        raise AuditError(f"{p} ends in a partial line")
    if (out and head is None) or (head is not None and head != [str(len(out)), prev]):
        raise AuditError(f"{p} has {len(out)} entries ending {prev[:12]}, {HEAD} says {head}")
    return out


def append(exp_root, *, status: str, protocol_id: str, sport: str, reason: str, trial_id: Optional[str] = None,
           evidence: Optional[list] = None, by: str = "", at: Optional[str] = None) -> dict:
    """Adds one entry. `evidence`: files judged; their sha256 is stored, the files are only read."""
    if status not in STATUSES or not reason.strip():
        raise ValueError("a known status and a reason are required")
    p = Path(exp_root) / FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        entries = load(exp_root)
        e = {"at": at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "by": by, "status": status, "reason": reason,
             "scope": {"protocol_id": protocol_id, "sport": sport, **({"trial_id": trial_id} if trial_id else {})},
             "evidence": [{"path": str(f), "sha256": hashlib.sha256(Path(f).read_bytes()).hexdigest()} for f in evidence or []],
             "prev": entries[-1]["sha"] if entries else FIRST}
        fh.write(json.dumps(e, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
        tmp = p.with_name(HEAD + ".tmp")
        tmp.write_text(f"{len(entries) + 1} {line_sha(json.dumps(e, sort_keys=True))}\n")
        tmp.replace(p.with_name(HEAD))
    return e


def verdict(entries: list, protocol_id: str, sport: str, trial_id: Optional[str] = None) -> Optional[dict]:
    """The entry that blocks this protocol and sport (or this trial of it), or None. 'invalid' is permanent; 'unverified' blocks until
    a later 'verified' of the same scope. A trial is blocked by its own entry or by its protocol's."""
    found = None
    for e in entries:
        s = e["scope"]
        if s["protocol_id"] != protocol_id or s["sport"] != sport or s.get("trial_id") not in (None, trial_id):
            continue
        if e["status"] == "invalid":
            return e
        same = found is not None and found["scope"] == s
        if e["status"] == "unverified":
            found = e
        elif same:
            found = None
    return found


def why(e: dict) -> str:
    return f"{e['status']} (audit {e['at']}): {e['reason']}"
