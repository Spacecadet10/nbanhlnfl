"""Prospective challengers: versioned policy files, a recorder and a separate append-only store.

A policy (reference/learning_policies/<sport>-challengers-<id>.json) lists challenger specs with provenance. Registering a
spec is NOT promotion: nothing here touches serving, cards, betting, `candidate_forecasts` or the hashed candidate registry.
The recorder reuses `candidates.live.record` with `write=False` (same two-hour window, same point-in-time fit and staleness
rules, in-memory fits unless a verified artifact exists) and appends the forecasts to <data dir>/research/challengers.sqlite.
Scoring against the incumbent is read-time only (learning_eval.py).

  python -m app.learning_challengers record --sport nba [--dry-run]
"""
import argparse
import hashlib
import json
import sqlite3
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import config
from .candidates import features, learners, live, registry
from .db import utc_now

POLICY_DIR = Path(__file__).parent / "reference" / "learning_policies"
SOURCES = ("existing_pool", "autoresearch_trial", "manual")
SPORTS = ("nba", "nhl")
SPEC_FIELDS = {"name", "sport", "family", "head", "features", "role", "why", "refit", "extra"}
PROVENANCE_FIELDS = ("source", "trial_id", "commit", "trial_decision", "registered_by", "registered_at", "note")
FMT = "%Y-%m-%dT%H:%M:%SZ"


class PolicyError(ValueError):
    pass


class Challenger:
    def __init__(self, spec, provenance: dict, preregistered_n: Optional[int]):
        self.spec, self.provenance, self.preregistered_n = spec, provenance, preregistered_n
        self.spec_json = json.dumps(asdict(spec), sort_keys=True)
        self.spec_hash = hashlib.sha256(self.spec_json.encode()).hexdigest()


class Policy:
    def __init__(self, policy_id: str, sport: str, sha: str, content: dict, challengers: list):
        self.policy_id, self.sport, self.sha, self.content, self.challengers = policy_id, sport, sha, content, challengers


def _text(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _spec(raw: dict, sport: str, source: str):
    if not isinstance(raw, dict) or set(raw) - SPEC_FIELDS or not {"name", "sport", "family", "head", "features", "role", "why"} <= set(raw):
        raise PolicyError(f"spec fields must be within {sorted(SPEC_FIELDS)} and include name, sport, family, head, features, role, why")
    if raw["sport"] != sport:
        raise PolicyError(f"spec {raw['name']}: sport {raw['sport']!r} is not the policy's sport {sport!r}")
    if raw["family"] not in learners.SETTINGS:
        raise PolicyError(f"spec {raw['name']}: unknown family {raw['family']!r}")
    if raw["features"] not in features.FEATURE_SETS:
        raise PolicyError(f"spec {raw['name']}: unknown features {raw['features']!r}")
    if raw["head"] not in registry.HEAD_MARKETS:
        raise PolicyError(f"spec {raw['name']}: unknown head {raw['head']!r}")
    if raw.get("refit", "season") != "season":
        raise PolicyError(f"spec {raw['name']}: only the season refit is implemented")
    spec = registry.Spec(**raw)
    if source == "existing_pool" and registry.BY_NAME.get(spec.name) != spec:
        raise PolicyError(f"spec {spec.name}: source existing_pool must equal the registry spec exactly")
    return spec


def _provenance(p: dict, name: str) -> dict:
    if not isinstance(p, dict) or any(k not in p for k in PROVENANCE_FIELDS):
        raise PolicyError(f"{name}: provenance must carry {', '.join(PROVENANCE_FIELDS)}")
    if p["source"] not in SOURCES:
        raise PolicyError(f"{name}: provenance source must be one of {SOURCES}")
    if not _text(p["registered_by"]):
        raise PolicyError(f"{name}: provenance registered_by is required")
    try:
        datetime.strptime(p["registered_at"], FMT)
    except (TypeError, ValueError):
        raise PolicyError(f"{name}: provenance registered_at must be a UTC timestamp like 2026-11-18T12:00:00Z") from None
    if p["source"] == "autoresearch_trial":
        for k in ("trial_id", "commit", "trial_decision"):
            if not _text(p[k]):
                raise PolicyError(f"{name}: an autoresearch_trial needs {k}")
        if p["trial_decision"] == "discard" and not _text(p["note"]):
            raise PolicyError(f"{name}: a discarded trial may be registered only with an explicit operator note (discard needs a note)")
    return p


def load_policy(path: Path) -> Policy:
    path = Path(path)
    raw = path.read_bytes()
    try:
        d = json.loads(raw)
    except ValueError as exc:
        raise PolicyError(f"{path.name}: not JSON ({exc})") from None
    if not isinstance(d, dict) or d.get("policy_id") != path.stem:
        raise PolicyError(f"{path.name}: policy_id must equal the file name {path.stem!r}")
    if d.get("sport") not in SPORTS:
        raise PolicyError(f"{path.name}: sport must be one of {SPORTS}")
    items, seen = [], set()
    for i, c in enumerate(d.get("challengers") or []):
        prov = _provenance(c.get("provenance"), f"{path.name} challenger {i}")
        ch = Challenger(_spec(c.get("spec"), d["sport"], prov["source"]), prov, c.get("preregistered_n"))
        if ch.spec_hash in seen:
            raise PolicyError(f"{path.name}: duplicate spec {ch.spec.name}")
        seen.add(ch.spec_hash)
        items.append(ch)
    if not items:
        raise PolicyError(f"{path.name}: no challengers")
    return Policy(d["policy_id"], d["sport"], hashlib.sha256(raw).hexdigest(), d, items)


def load_policies(sport: str, folder: Optional[Path] = None) -> list:
    return [load_policy(p) for p in sorted(Path(folder or POLICY_DIR).glob(f"{sport}-challengers-*.json"))]


# --- Store ----------------------------------------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS challenger_policies (
  policy_sha TEXT PRIMARY KEY, policy_id TEXT NOT NULL, sport TEXT NOT NULL, content TEXT NOT NULL, first_seen_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS challenger_forecasts (
  forecast_id INTEGER PRIMARY KEY, policy_id TEXT NOT NULL, policy_sha TEXT NOT NULL, spec_name TEXT NOT NULL,
  spec_json TEXT NOT NULL, spec_hash TEXT NOT NULL, definition TEXT NOT NULL, game_id TEXT NOT NULL,
  market TEXT NOT NULL CHECK (market IN ('winner','reg3','spread','total','team_total_home','team_total_away')),
  line REAL, p_a REAL NOT NULL CHECK (p_a BETWEEN 0 AND 1), p_mid REAL NOT NULL CHECK (p_mid BETWEEN 0 AND 1),
  p_b REAL NOT NULL CHECK (p_b BETWEEN 0 AND 1), cutoff_at TEXT NOT NULL, tipoff_at TEXT NOT NULL, generated_at TEXT NOT NULL,
  dataset_version TEXT NOT NULL, model_version TEXT NOT NULL,
  CHECK (abs(p_a + p_mid + p_b - 1) < 1e-6), CHECK ((market IN ('winner','reg3')) = (line IS NULL)),
  UNIQUE (policy_sha, spec_hash, game_id, market, cutoff_at));
CREATE INDEX IF NOT EXISTS challenger_forecasts_game ON challenger_forecasts(game_id, market, spec_hash);
CREATE TRIGGER IF NOT EXISTS challenger_forecasts_no_update BEFORE UPDATE ON challenger_forecasts
BEGIN SELECT RAISE(ABORT, 'challenger_forecasts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS challenger_forecasts_no_delete BEFORE DELETE ON challenger_forecasts
BEGIN SELECT RAISE(ABORT, 'challenger_forecasts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS challenger_policies_no_update BEFORE UPDATE ON challenger_policies
BEGIN SELECT RAISE(ABORT, 'challenger_policies are append-only'); END;
CREATE TRIGGER IF NOT EXISTS challenger_policies_no_delete BEFORE DELETE ON challenger_policies
BEGIN SELECT RAISE(ABORT, 'challenger_policies are append-only'); END;
CREATE TRIGGER IF NOT EXISTS challenger_forecasts_two_hour_window BEFORE INSERT ON challenger_forecasts
WHEN NEW.generated_at < strftime('%Y-%m-%dT%H:%M:%SZ', NEW.tipoff_at, '-2 hours') OR NEW.generated_at >= NEW.tipoff_at
BEGIN SELECT RAISE(ABORT, 'challenger forecast must be within two hours before tipoff and strictly before it'); END;
"""


def store_path(data_dir: Optional[Path] = None) -> Path:
    return Path(data_dir or config.data_dir()) / "research" / "challengers.sqlite"


def open_store(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    return conn


def open_ro(path: Path) -> Optional[sqlite3.Connection]:
    path = Path(path)
    if not path.is_file():
        return None
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# --- Recorder -------------------------------------------------------------------------------------------------------
def record(conn, path: Path, *, policies: list, now: Optional[str] = None, lines: Optional[dict] = None, dry_run: bool = False) -> dict:
    """Challenger forecasts for games tipping within two hours of `now`. `conn` is the sport's store, used read-only (the
    pool recorder runs with write=False). Never raises: failures are reported in `errors`."""
    now = now or utc_now()
    out = {"status": "success", "dry_run": dry_run, "now": now, "created": 0, "would_record": 0, "already_recorded": 0,
           "eligible_games": 0, "skipped": {}, "errors": {}, "policies": [{"policy_id": p.policy_id, "sha": p.sha} for p in policies]}
    specs = list({c.spec.name: c.spec for p in policies for c in p.challengers}.values())
    try:
        res = live.record(conn, now=now, lines=lines, write=False, specs=specs)
    except Exception as exc:
        out["status"], out["errors"]["recorder"] = "failed", f"{type(exc).__name__}: {exc}"
        return out
    out["eligible_games"] = res.get("eligible_games", 0)
    out["skipped"], out["errors"] = res["skipped"], dict(res["errors"])
    if res["status"] == "failed":
        out["status"] = "failed"
    tip = {r[0]: r[1] for r in conn.execute(f"SELECT game_id, tipoff_at FROM games WHERE game_id IN ({','.join('?' * len(res['forecasts'])) or 'NULL'})",
                                           [f["game_id"] for f in res["forecasts"]])}
    store = open_store(path) if not dry_run else open_ro(path)
    try:
        for pol in policies:
            if store is not None and not dry_run:
                store.execute("INSERT OR IGNORE INTO challenger_policies VALUES (?,?,?,?,?)",
                              (pol.sha, pol.policy_id, pol.sport, json.dumps(pol.content, sort_keys=True), now))
            for c in pol.challengers:
                defn = registry.definition(c.spec)
                for f in (x for x in res["forecasts"] if x["candidate"] == c.spec.name):
                    key = (pol.sha, c.spec_hash, f["game_id"], f["market"], f["line"])
                    if store is not None and store.execute(
                            "SELECT 1 FROM challenger_forecasts WHERE policy_sha=? AND spec_hash=? AND game_id=? AND market=? AND line IS ?",
                            key).fetchone():
                        out["already_recorded"] += 1
                        continue
                    if dry_run:
                        out["would_record"] += 1
                        continue
                    try:
                        store.execute(
                            "INSERT INTO challenger_forecasts (policy_id, policy_sha, spec_name, spec_json, spec_hash, definition, game_id, "
                            "market, line, p_a, p_mid, p_b, cutoff_at, tipoff_at, generated_at, dataset_version, model_version) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (pol.policy_id, pol.sha, c.spec.name, c.spec_json, c.spec_hash, defn, f["game_id"], f["market"], f["line"],
                             *f["p"], now, tip[f["game_id"]], f["generated_at"], res.get("dataset_version", ""), f"challenger:{c.spec.name}:{defn}"))
                        out["created"] += 1
                    except sqlite3.Error as exc:
                        out["errors"][f"{c.spec.name}:{f['game_id']}:{f['market']}"] = f"{type(exc).__name__}: {exc}"
    finally:
        if store is not None:
            store.close()
    if out["status"] == "success" and (out["errors"] or out["skipped"]):
        out["status"] = "partial"
    return out


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.learning_challengers")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--sport", choices=SPORTS, required=True)
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--now", help="fixed cutoff (UTC, for rehearsals and tests); default the clock")
    a = p.parse_args(argv)
    try:
        policies = load_policies(a.sport)
    except PolicyError as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)}))
        return 2
    src = config.data_dir() / f"{a.sport}.sqlite"
    if not src.is_file():
        print(json.dumps({"status": "refused", "reason": f"no {a.sport} store at {src}"}))
        return 2
    conn = sqlite3.connect(f"{src.resolve().as_uri()}?mode=ro", uri=True)       # the sport store is never written
    conn.row_factory = sqlite3.Row
    out = record(conn, store_path(), policies=policies, now=a.now, dry_run=a.dry_run)
    out.pop("skipped", None) if not out["skipped"] else None
    print(json.dumps(out, indent=2, sort_keys=True, default=str))
    return 0 if out["status"] in ("success", "partial") else 1


if __name__ == "__main__":
    sys.exit(main())
