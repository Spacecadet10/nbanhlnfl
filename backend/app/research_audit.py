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
SPORTS = ("nba", "nhl", "ncaab", "nfl", "soccer")
STATUSES = ("invalid", "unverified", "verified")
FIRST = "0" * 64


class AuditError(Exception):
    """The audit file is unreadable, malformed or its chain is broken: nothing may be selected from these records."""


def line_sha(line: str) -> str:
    return hashlib.sha256(line.encode()).hexdigest()


def _parse(p: Path, text: str, head: Optional[list]) -> list:
    out, prev = [], FIRST
    if text and not text.endswith("\n"):
        raise AuditError(f"{p} ends in a partial line")
    for n, line in enumerate(text.splitlines(), 1):
        try:
            e = json.loads(line)
            scope = e["scope"]
            ok = (e["prev"] == prev and e["status"] in STATUSES and isinstance(e["reason"], str) and e["reason"].strip()
                  and isinstance(e["at"], str) and isinstance(scope["protocol_id"], str) and scope["sport"] in SPORTS
                  and (scope.get("trial_id") is None or isinstance(scope["trial_id"], str)))
        except (ValueError, KeyError, TypeError) as exc:
            raise AuditError(f"{p} line {n} is malformed") from exc
        if not ok:
            raise AuditError(f"{p} line {n} is malformed or the chain is broken")
        prev = line_sha(line)
        out.append({**e, "sha": prev})
    # The head is written after its line, so after a crash it may be exactly one entry behind (the next append brings it up to date).
    # Anything else (entries gone, head gone, another head) is damage.
    if head is None:
        good = len(out) <= 1
    else:
        try:
            n, sha = int(head[0]), head[1]
        except (ValueError, IndexError):
            n, sha = -1, ""
        good = 1 <= n <= len(out) <= n + 1 and out[n - 1]["sha"] == sha
    if not good:
        raise AuditError(f"{p} holds {len(out)} entries, {HEAD} says {head}")
    return out


def _read(p: Path, fh) -> list:
    """Both files under the caller's lock on the audit file."""
    try:
        head = p.with_name(HEAD).read_text().split()
    except FileNotFoundError:
        head = None
    fh.seek(0)
    return _parse(p, fh.read(), head)


def _anchored(exp_root: Path, entries: list) -> list:
    """The pin next to the experiment root (PIN.json) names an entry that must still be there: deleting the files is noticed."""
    pin = exp_root.parent / "PIN.json"
    try:
        want = json.loads(pin.read_text()).get("audit_head")
    except FileNotFoundError:
        return entries
    except (OSError, ValueError, AttributeError) as exc:
        raise AuditError(f"{pin} cannot be read: {exc}") from exc
    if want and want not in [e["sha"] for e in entries]:
        raise AuditError(f"{exp_root / FILE} no longer holds the entry named by {pin}")
    return entries


def load(exp_root) -> list:
    """Every entry, oldest first, with its line sha as `sha`. [] only when the root never had an audit file."""
    p = Path(exp_root) / FILE
    try:
        with p.open() as fh:
            fcntl.flock(fh, fcntl.LOCK_SH)
            return _anchored(p.parent, _read(p, fh))
    except FileNotFoundError:
        if p.with_name(HEAD).exists():
            raise AuditError(f"{p} is missing but {HEAD} says it existed") from None
        return _anchored(p.parent, [])
    except (OSError, UnicodeDecodeError) as exc:
        raise AuditError(f"{p} cannot be read: {exc}") from exc


def append(exp_root, *, status: str, protocol_id: str, sport: str, reason: str, trial_id: Optional[str] = None,
           evidence: Optional[list] = None, by: str = "", at: Optional[str] = None) -> dict:
    """Adds one entry. `evidence`: files judged; their sha256 is stored, the files are only read. The records it is about must
    exist (a typing error would otherwise block nothing)."""
    root = Path(exp_root)
    if status not in STATUSES or not reason.strip() or sport not in SPORTS:
        raise ValueError("a known status, a known sport and a reason are required")
    folder = next((d for d in (root / protocol_id / sport, root / "v3" / protocol_id / sport) if d.is_dir()), None)
    if folder is None:
        raise ValueError(f"no {sport} records of {protocol_id} under {root}")
    if trial_id is not None:
        log = folder / "trials.jsonl"
        if not log.is_file() or f'"{trial_id}"' not in log.read_text():
            raise ValueError(f"no trial {trial_id} in {log}")
    p = root / FILE
    with p.open("a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        entries = _read(p, fh)
        e = {"at": at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "by": by, "status": status, "reason": reason,
             "scope": {"protocol_id": protocol_id, "sport": sport, **({"trial_id": trial_id} if trial_id else {})},
             "evidence": [{"path": str(f), "sha256": hashlib.sha256(Path(f).read_bytes()).hexdigest()} for f in evidence or []],
             "prev": entries[-1]["sha"] if entries else FIRST}
        line = json.dumps(e, sort_keys=True)
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())
        tmp = p.with_name(HEAD + ".tmp")
        tmp.write_text(f"{len(entries) + 1} {line_sha(line)}\n")
        tmp.replace(p.with_name(HEAD))
    return e


def verdict(entries: list, protocol_id: str, sport: str, trial_id: Optional[str] = None) -> Optional[dict]:
    """The entry that blocks this protocol and sport (or this trial of it), or None. 'invalid' is permanent; 'unverified' blocks until
    a later 'verified' of the SAME scope. A trial is blocked by its own entry or by its protocol's."""
    open_: dict = {}
    for e in entries:
        s = e["scope"]
        if s["protocol_id"] != protocol_id or s["sport"] != sport or s.get("trial_id") not in (None, trial_id):
            continue
        if e["status"] == "invalid":
            return e
        if e["status"] == "unverified":
            open_[s.get("trial_id")] = e
        else:
            open_.pop(s.get("trial_id"), None)
    return next(iter(open_.values()), None)


def why(e: dict) -> str:
    return f"{e['status']} (audit {e['at']}): {e['reason']}"
