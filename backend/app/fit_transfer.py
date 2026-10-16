"""Transfer of published adaptive-v1 fits from the data directory they were trained in to this one (docs/ADAPTIVE_SYSTEM.md §13).

The 2026-10-14 retrain ran on an isolated copy of the stores and published its fit rows there; the release moves those fits
into the live store instead of training again. Per cadence, for the definition THIS code and store compute:
- every artifact of the source rows (and the history checkpoint) is verified against its fit row and sidecar BEFORE anything
  is written; one that does not verify refuses the cadence;
- the artifacts are copied under this data directory's research/adaptive/<sport> (same relative path, never overwritten);
- the model_fits identities are written with the training environment's library versions, first used now;
- the fit rows go through adaptive.publish, which re-verifies each artifact and refuses when this store's definition differs.
Nothing is created for a definition the source has no fits of. A target that already has fits of it is refused, unless they
are all rows of the source (an interrupted transfer): then only the missing rows are added. Every in-force artifact is also
loaded (unpickled) in this environment before anything is written. The serving guards (results and box/shot digests at each
fit's cutoff) still decide, in this store, whether a member forecasts; `warnings` names what would not serve now.
The caller holds this sport's store lock (as for adaptive.publish); the transfer takes the sport's training lock without
waiting, so no training run can publish or rewrite the same definition meanwhile. The source store is opened read-only.
Imported rows keep the source's `started_at`; `finished_at` is the transfer time. The model_fits identities are written just
before publish (autocommit): an interrupted or refused publish can leave identity rows no fit row uses yet; they are keyed
by identity, so a retry reuses them.
A member written as 'failed' at publication (or a combiner downgraded to 'partial') differs from the source's row, so a
retry is refused with the reason rather than reported as transferred; the definition then needs a decision (or a retrain)."""
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Optional

from . import adaptive, config, sports
from .candidates import live as pool_live

IDENT = ("schedule", "fit_kind", "trained_season", "training_games", "results_fingerprint", "results_available_through",
         "params_digest")


class Refused(RuntimeError):
    pass


def _source(source_dir: Path, sport: str) -> sqlite3.Connection:
    path = source_dir / sports.get(sport).db_file
    if not path.is_file():
        raise Refused(f"no {sport} store at {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def _verified(path: Path, sha: Optional[str] = None) -> bytes:
    """The file's bytes, if they match the digest its fit row records (when given) and its sidecar."""
    data = path.read_bytes()
    got = hashlib.sha256(data).hexdigest()
    side = pool_live._sidecar(path)
    recorded = side.read_text().strip() if side.is_file() else None
    if (sha is not None and got != sha) or got != recorded:
        raise Refused(f"source artifact {path} does not verify: digest {got[:12]}, fit row {(sha or '-')[:12]}, "
                      f"sidecar {(recorded or 'missing')[:12]}")
    return data


def _same(dest: Path, sha: str) -> str:
    """'absent'; 'same' (the file and its sidecar carry `sha`); 'sidecar' (the bytes carry `sha`, the sidecar is missing or
    stale: a write interrupted between the two renames of adaptive._write_bytes); 'other' (different bytes)."""
    if not dest.exists():
        return "absent"
    if hashlib.sha256(dest.read_bytes()).hexdigest() != sha:
        return "other"
    side = pool_live._sidecar(dest)
    return "same" if side.is_file() and side.read_text().strip() == sha else "sidecar"


def _place(data: bytes, dest: Path, replaceable: bool = False) -> str:
    """Copies `data` to `dest` (and its sidecar) unless the same content is there. Other content is never overwritten, except
    a `replaceable` file (the history checkpoint: mutable by design, no fit row refers to it). Identical bytes with a missing
    or stale sidecar are rewritten (the same bytes: nothing different is overwritten)."""
    sha = hashlib.sha256(data).hexdigest()
    dest.parent.mkdir(parents=True, exist_ok=True)
    with pool_live._locked(dest):          # the same per-file lock adaptive._save and save_history take
        state = _same(dest, sha)
        if state == "same":
            return "present"
        if state == "other" and not replaceable:
            raise Refused(f"{dest} already exists with other content; nothing is overwritten")
        adaptive._write_bytes(dest, data)
        return {"absent": "copied", "sidecar": "repaired", "other": "replaced"}[state]


def _cadence(conn, src, source_root: Path, sport: str, cadence: str, now: str) -> dict:
    defn = adaptive.definition(conn)
    out = {"definition": defn}
    rows = src.execute("SELECT * FROM adaptive_fits WHERE sport=? AND definition=? AND schedule=? ORDER BY fit_id",
                       (sport, defn, cadence)).fetchall()
    if not rows:
        return {**out, "status": "failed", "reason": f"the source has no fits of this definition ({defn}, {cadence}): "
                                                     "the code or environment differs from the one that trained them"}
    # Rows of an interrupted transfer already here are matched by (idempotency key, status, artifact digest) and counted:
    # the key alone omits the status, so a combiner that publish downgraded to 'partial' (or a member written as 'failed')
    # would otherwise pass for the source's row. Any row of the definition that does not match refuses the transfer.
    sig = lambda r: (r["idempotency_key"], r["status"], r["artifact_sha256"])     # noqa: E731
    have = Counter(sig(r) for r in conn.execute("SELECT idempotency_key, status, artifact_sha256 FROM adaptive_fits "
                                                "WHERE sport=? AND definition=? AND schedule=?", (sport, defn, cadence)))
    extra = have - Counter(sig(r) for r in rows)
    if extra:
        return {**out, "status": "failed", "reason": f"this store already has {sum(extra.values())} fit row(s) of {defn} "
                                                     f"({cadence}) that differ from the source's (another fit, or a status "
                                                     "changed at publication); nothing transferred. A combiner left "
                                                     "'partial' by a failed publication is superseded by the next "
                                                     "scheduled `adaptive-train` of this cadence (a partial run is due)"}
    out["resumed"] = sum(have.values())
    target_root = adaptive.root(sport).resolve()
    source_root = source_root.resolve()
    files = {}                                   # source path -> (bytes, destination)
    for r in rows:
        if r["artifact_path"]:
            p = Path(r["artifact_path"]).resolve()
            dest = (target_root / p.relative_to(source_root)).resolve() if p.is_relative_to(source_root) else None
            if dest is None or not dest.is_relative_to(target_root):
                raise Refused(f"fit {r['fit_id']}: artifact {r['artifact_path']} is outside the source's {source_root}")
            files[Path(r["artifact_path"])] = (_verified(p, r["artifact_sha256"]), dest)
            if r["status"] in adaptive.IN_FORCE:         # unpickled here: an artifact this environment cannot load is refused
                try:
                    adaptive._load(p, r["artifact_sha256"])
                except Exception as exc:
                    raise Refused(f"fit {r['fit_id']} ({r['name']}): artifact cannot be loaded in this environment: "
                                  f"{type(exc).__name__}: {exc}") from exc
    history = source_root / f"history-{defn}.pkl"
    if history.is_file():
        files[history] = (_verified(history), target_root / history.name)
    out["history"] = "absent from the source: the next training run walks the full history again"
    idents = {}
    for r in rows:
        if r["model_fit_id"] is not None:
            mf = src.execute("SELECT * FROM model_fits WHERE fit_id=?", (r["model_fit_id"],)).fetchone()
            if mf is None:
                raise Refused(f"fit {r['fit_id']}: its model_fits row {r['model_fit_id']} is missing in the source")
            idents[r["fit_id"]] = mf
    for src_path, (data, dest) in files.items():      # every destination checked before the first file is written
        if src_path != history and _same(dest, hashlib.sha256(data).hexdigest()) == "other":
            raise Refused(f"{dest} already exists with other content; nothing is overwritten")
    # Everything verified: copy, register the identities with their original library versions, then publish.
    out["files"] = {"copied": 0, "present": 0, "repaired": 0, "replaced": 0}
    for src_path, (data, dest) in files.items():
        placed = _place(data, dest, replaceable=src_path == history)
        out["files"][placed] += 1
        if src_path == history:
            out["history"] = placed
    for mf in idents.values():
        conn.execute("INSERT OR IGNORE INTO model_fits (sport, member, definition, schedule, fit_kind, trained_season, "
                     "training_games, results_fingerprint, results_available_through, params_digest, libraries, first_used_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (*(mf[c] for c in ("sport", "member", "definition", *IDENT[:6])),
                                                          mf["params_digest"], mf["libraries"], now))
    left, todo = Counter(have), []
    for r in rows:                         # each matching row already here accounts for one source row
        if left[sig(r)]:
            left[sig(r)] -= 1
        else:
            todo.append(r)
    pending = [{"sport": r["sport"], "kind": r["kind"], "name": r["name"], "defn": defn, "period": r["period"],
                "status": r["status"], "started": r["started_at"], "detail": json.loads(r["detail"] or "{}"),
                "cutoff": r["training_cutoff"], "games": r["training_games"], "fp": r["results_fingerprint"],
                "path": str(files[Path(r["artifact_path"])][1]) if r["artifact_path"] else None, "sha": r["artifact_sha256"],
                "ident": {c: idents[r["fit_id"]][c] for c in IDENT} if r["fit_id"] in idents else None, "now": now}
               for r in todo]
    pub = adaptive.publish(conn, {"pending": pending, "definition": defn})
    out["publication"] = pub
    out["source_fits"] = len(rows)
    ok = not pub.get("error") and not pub["failed"] and pub["published"] == len(pending)
    out["status"] = "success" if ok else "failed"
    # Checked as of now, after publication: the rows are stamped when written, so the transfer's start time precedes them.
    after = adaptive.utc_now()
    comb = adaptive.latest_fit(conn, sport, "combiner", "combiner", defn, after)
    out["in_force"] = {"at": after, "combiner": comb["fit_id"] if comb else None}
    out["due"] = adaptive.due(conn, after)
    out["inputs_changed"] = adaptive.inputs_changed_since_fit(conn, sport, defn, after)
    out["warnings"] = ([] if comb else ["no combiner fit of this definition is in force"]) + \
        ([f"members whose inputs changed since their fit cannot forecast until a retrain: {', '.join(out['inputs_changed'])}"]
         if out["inputs_changed"] else []) + \
        ([f"due for a refit now: {out['due'].get('reason')}"] if out["due"].get("due") else []) + \
        ([f"due check failed: {out['due'].get('reason')}"] if not out["due"].get("due") and
         str(out["due"].get("reason", "")).startswith("definition unavailable") else [])
    return out


def transfer(conn, source_dir, sport: str, *, cadences=("weekly", "daily"), now: Optional[str] = None) -> dict:
    """Transfers the source's fits of each cadence's current definition into `conn`'s store (see the module docstring)."""
    now = now or adaptive.utc_now()
    source_dir = Path(source_dir).resolve()
    out = {"sport": sport, "source": str(source_dir), "target": str(config.data_dir().resolve()), "cadences": {}}
    if source_dir == config.data_dir().resolve():
        return {**out, "status": "failed", "reason": "source and target are the same data directory"}
    try:
        src = _source(source_dir, sport)
    except Refused as exc:
        return {**out, "status": "failed", "reason": str(exc)}
    lock = adaptive.training_lock(sport)
    if not lock.__enter__():
        src.close()
        return {**out, "status": "failed", "reason": f"another process holds the {sport} training lock (a training run): "
                                                     "nothing transferred; retry when it has finished"}
    try:
        for cadence in cadences:
            with adaptive.cadence(cadence):
                try:
                    out["cadences"][cadence] = _cadence(conn, src, source_dir / "research" / "adaptive" / sport, sport,
                                                        cadence, now)
                except Refused as exc:
                    out["cadences"][cadence] = {"status": "failed", "reason": str(exc)}
    finally:
        lock.__exit__(None, None, None)
        src.close()
    out["status"] = "success" if all(c["status"] == "success" for c in out["cadences"].values()) else "failed"
    return out
