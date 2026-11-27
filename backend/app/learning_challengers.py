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
import importlib
import json
import sqlite3
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import config, release
from .candidates import features, learners, live, registry
from .db import utc_now

POLICY_DIR = Path(__file__).parent / "reference" / "learning_policies"
RETIRED = {"ncaab-challengers-v1": "pre-as-of (574ba5f), excluded: priced at the recorder's own clock, not as of a published bar; rows kept, never paired or scored"}
SOURCES = ("existing_pool", "autoresearch_trial", "manual")
SPORTS = ("nba", "nhl", "ncaab", "nfl", "soccer")
SPORT_MODULES = {"ncaab": "ncaab_challengers", "nfl": "nfl_challengers", "soccer": "soccer_challengers"}   # own spec families and recorders
TRIAL_DECISIONS = ("keep", "discard", "crash", "invalid", "n/a")
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
    if sport in SPORT_MODULES:
        return importlib.import_module(f"{__package__}.{SPORT_MODULES[sport]}").spec(raw, sport)
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
    if p["trial_decision"] is not None and p["trial_decision"] not in TRIAL_DECISIONS:
        raise PolicyError(f"{name}: trial_decision must be one of {TRIAL_DECISIONS} or null")
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
        n = c.get("preregistered_n")
        if n is not None and (isinstance(n, bool) or not isinstance(n, int) or n < 1):
            raise PolicyError(f"{path.name} challenger {i}: preregistered_n must be a whole number >= 1 or null")
        ch = Challenger(_spec(c.get("spec"), d["sport"], prov["source"]), prov, n)
        if ch.spec_hash in seen:
            raise PolicyError(f"{path.name}: duplicate spec {ch.spec.name}")
        seen.add(ch.spec_hash)
        items.append(ch)
    if not items:
        raise PolicyError(f"{path.name}: no challengers")
    return Policy(d["policy_id"], d["sport"], hashlib.sha256(raw).hexdigest(), d, items)


def check_unique_names(policies: list) -> None:
    """A spec name identifies a challenger in reports: the same name with a different definition (hash) is refused."""
    seen: dict = {}
    for pol in policies:
        for c in pol.challengers:
            if seen.setdefault(c.spec.name, c.spec_hash) != c.spec_hash:
                raise PolicyError(f"spec name {c.spec.name!r} is used by two policies with a different definition (same name, different hash)")


def load_policies(sport: str, folder: Optional[Path] = None) -> list:
    pols = [load_policy(p) for p in sorted(Path(folder or POLICY_DIR).glob(f"{sport}-challengers-*.json"))]
    check_unique_names(pols)
    return pols


# --- Store ----------------------------------------------------------------------------------------------------------
MARKETS = ("winner", "reg3", "spread", "total", "team_total_home", "team_total_away",
           "soccer_1x2", "soccer_goals_total", "soccer_goals_home", "soccer_goals_away", "soccer_btts")
NO_LINE = ("winner", "reg3", "soccer_1x2", "soccer_btts")
TABLE = """
CREATE TABLE {name} (
  forecast_id INTEGER PRIMARY KEY, policy_id TEXT NOT NULL, policy_sha TEXT NOT NULL, spec_name TEXT NOT NULL,
  spec_json TEXT NOT NULL, spec_hash TEXT NOT NULL, definition TEXT NOT NULL, game_id TEXT NOT NULL,
  market TEXT NOT NULL CHECK (market IN ({markets})),
  line REAL, p_a REAL NOT NULL CHECK (p_a BETWEEN 0 AND 1), p_mid REAL NOT NULL CHECK (p_mid BETWEEN 0 AND 1),
  p_b REAL NOT NULL CHECK (p_b BETWEEN 0 AND 1), cutoff_at TEXT NOT NULL, tipoff_at TEXT NOT NULL, generated_at TEXT NOT NULL,
  dataset_version TEXT NOT NULL, model_version TEXT NOT NULL, recorded_at TEXT,
  CHECK (abs(p_a + p_mid + p_b - 1) < 1e-6), CHECK ((market IN ({no_line})) = (line IS NULL)),
  UNIQUE (policy_sha, spec_hash, game_id, market, cutoff_at));
""".format(name="{name}", markets=", ".join(f"'{m}'" for m in MARKETS), no_line=", ".join(f"'{m}'" for m in NO_LINE))
COLUMNS = ("forecast_id, policy_id, policy_sha, spec_name, spec_json, spec_hash, definition, game_id, market, line, p_a, p_mid, p_b, "
           "cutoff_at, tipoff_at, generated_at, dataset_version, model_version, recorded_at")
_POLICIES = ("CREATE TABLE IF NOT EXISTS challenger_policies (\n"
             "  policy_sha TEXT PRIMARY KEY, policy_id TEXT NOT NULL, sport TEXT NOT NULL, content TEXT NOT NULL, first_seen_at TEXT NOT NULL)")
_FORECASTS = TABLE.replace("CREATE TABLE {name}", "CREATE TABLE IF NOT EXISTS challenger_forecasts").strip().rstrip(";")
GUARDS = (      # the index and the append-only / window triggers: created in the same transaction as any table rebuild
    "CREATE INDEX IF NOT EXISTS challenger_forecasts_game ON challenger_forecasts(game_id, market, spec_hash)",
    "CREATE TRIGGER IF NOT EXISTS challenger_forecasts_no_update BEFORE UPDATE ON challenger_forecasts\n"
    "BEGIN SELECT RAISE(ABORT, 'challenger_forecasts are append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS challenger_forecasts_no_delete BEFORE DELETE ON challenger_forecasts\n"
    "BEGIN SELECT RAISE(ABORT, 'challenger_forecasts are append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS challenger_policies_no_update BEFORE UPDATE ON challenger_policies\n"
    "BEGIN SELECT RAISE(ABORT, 'challenger_policies are append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS challenger_policies_no_delete BEFORE DELETE ON challenger_policies\n"
    "BEGIN SELECT RAISE(ABORT, 'challenger_policies are append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS challenger_forecasts_two_hour_window BEFORE INSERT ON challenger_forecasts\n"
    "WHEN NEW.generated_at >= NEW.tipoff_at\n"
    "  OR (substr(NEW.market, 1, 7) <> 'soccer_' AND NEW.generated_at < strftime('%Y-%m-%dT%H:%M:%SZ', NEW.tipoff_at, '-2 hours'))\n"
    "BEGIN SELECT RAISE(ABORT, 'challenger forecast must be within two hours before tipoff and strictly before it (soccer: before kickoff)'); END",
)
SCHEMA = ";\n".join((_POLICIES, _FORECASTS, *GUARDS)) + ";\n"


def _needs_migration(conn: sqlite3.Connection) -> bool:
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='challenger_forecasts'").fetchone()[0]
    trig = conn.execute("SELECT sql FROM sqlite_master WHERE name='challenger_forecasts_two_hour_window'").fetchone()
    return "soccer_1x2" not in sql or (trig is not None and "soccer_" not in trig[0])


def _migrate(conn: sqlite3.Connection) -> None:
    """Stores made before the soccer markets: rebuild challenger_forecasts with the wider market CHECK (rows and ids kept) and
    replace the window trigger (soccer forecasts are made at the 06:00 UTC cutoff, not within two hours of kickoff). The swap,
    the index and every trigger are one transaction, and the need is re-read after the write lock is taken, so a concurrent
    opener never repeats the rebuild and no reader sees the table without its append-only triggers."""
    if not _needs_migration(conn):
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        if _needs_migration(conn):
            sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='challenger_forecasts'").fetchone()[0]
            if "soccer_1x2" not in sql:
                conn.execute(TABLE.format(name="challenger_forecasts_new"))
                conn.execute(f"INSERT INTO challenger_forecasts_new ({COLUMNS}) SELECT {COLUMNS} FROM challenger_forecasts")
                conn.execute("DROP TABLE challenger_forecasts")
                conn.execute("ALTER TABLE challenger_forecasts_new RENAME TO challenger_forecasts")
            conn.execute("DROP TRIGGER IF EXISTS challenger_forecasts_two_hour_window")
            for stmt in GUARDS:
                conn.execute(stmt)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def store_path(data_dir: Optional[Path] = None) -> Path:
    return Path(data_dir or config.data_dir()) / "research" / "challengers.sqlite"


KEEP_BACKUPS = 3


def _backup_before_migration(path: Path) -> Optional[Path]:
    """An existing store that open_store would migrate in place (missing column, old table or trigger) is copied first, with the
    sqlite backup API, to challengers.pre-migrate-<ts>.sqlite; at most KEEP_BACKUPS of them are kept. None when no migration is due."""
    if not path.is_file():
        return None
    ro = open_ro(path)
    try:
        has = {r[0] for r in ro.execute("SELECT name FROM sqlite_master")}
        if "challenger_forecasts" not in has:
            return None
        needs = "recorded_at" not in {r[1] for r in ro.execute("PRAGMA table_info(challenger_forecasts)")} or _needs_migration(ro)
        if not needs:
            return None
        dest = path.with_name(f"{path.stem}.pre-migrate-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}.sqlite")
        out = sqlite3.connect(dest)
        try:
            ro.backup(out)
        finally:
            out.close()
    finally:
        ro.close()
    for old in sorted(path.parent.glob(f"{path.stem}.pre-migrate-*.sqlite"))[:-KEEP_BACKUPS]:
        old.unlink(missing_ok=True)
    return dest


def open_store(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _backup_before_migration(path)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    if "recorded_at" not in {r[1] for r in conn.execute("PRAGMA table_info(challenger_forecasts)")}:
        conn.execute("ALTER TABLE challenger_forecasts ADD COLUMN recorded_at TEXT")     # stores created before the column
    _migrate(conn)
    return conn


def open_ro(path: Path) -> Optional[sqlite3.Connection]:
    path = Path(path)
    if not path.is_file():
        return None
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def append_rows(path: Path, pol, c, rows: list, *, definition: str, dataset_version: str, now: str, dry_run: bool, out: dict,
                cutoff_at: Optional[str] = None) -> None:
    """Append one challenger's forecast rows (dicts: game_id, market, line, p, generated_at, tipoff_at, optional cutoff_at) to the store, once per
    (policy, spec, game, market, line): the first cutoff wins. Dry runs only count. `out` collects created/would_record/errors."""
    store = open_ro(path) if dry_run else open_store(path)
    try:
        if store is not None and not dry_run:
            store.execute("INSERT OR IGNORE INTO challenger_policies VALUES (?,?,?,?,?)",
                          (pol.sha, pol.policy_id, pol.sport, json.dumps(pol.content, sort_keys=True), now))
        for f in rows:
            key = (pol.sha, c.spec_hash, f["game_id"], f["market"], f["line"])
            if store is not None and store.execute(
                    "SELECT 1 FROM challenger_forecasts WHERE policy_sha=? AND spec_hash=? AND game_id=? AND market=? AND line IS ?",
                    key).fetchone():
                out["already_recorded"] += 1
            elif dry_run:
                out["would_record"] += 1
            else:
                try:
                    store.execute(
                        "INSERT INTO challenger_forecasts (policy_id, policy_sha, spec_name, spec_json, spec_hash, definition, game_id, "
                        "market, line, p_a, p_mid, p_b, cutoff_at, tipoff_at, generated_at, dataset_version, model_version, recorded_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (pol.policy_id, pol.sha, c.spec.name, c.spec_json, c.spec_hash, definition, f["game_id"], f["market"], f["line"],
                         *f["p"], f.get("cutoff_at") or cutoff_at or now, f["tipoff_at"], f["generated_at"], dataset_version, f"challenger:{c.spec.name}:{definition}", utc_now()))
                    out["created"] += 1
                except sqlite3.Error as exc:
                    out["errors"][f"{c.spec.name}:{f['game_id']}:{f['market']}"] = f"{type(exc).__name__}: {exc}"
    finally:
        if store is not None:
            store.close()


# --- Recorder -------------------------------------------------------------------------------------------------------
class NoArtifact(RuntimeError):
    pass


def _no_fit(*_a, **_k):
    raise NoArtifact("no verified stored artifact for this spec and season")


def _record_stored_only(conn, now, lines, specs) -> dict:
    """live.record(write=False) with fitting disabled: ensure_fit(write=False) reaches `_fit` only when no verified stored artifact
    exists, so with `_fit` replaced such a spec (deep ones included) is reported 'skipped: no artifact' and never refitted in memory.
    live.py itself is unchanged."""
    real = live._fit
    live._fit = _no_fit
    try:
        res = live.record(conn, now=now, lines=lines, write=False, specs=specs)
    finally:
        live._fit = real
    for name, msg in list(res["errors"].items()):
        if msg.startswith("NoArtifact:"):
            del res["errors"][name]
            res["skipped"][name] = {"all": "skipped: no artifact"}
    if res["status"] == "failed" and not res["errors"]:
        res["status"] = "partial" if res["skipped"] else "success"
    return res


def record(conn, path: Path, *, policies: list, now: Optional[str] = None, lines: Optional[dict] = None, dry_run: bool = False) -> dict:
    """Challenger forecasts for games tipping within two hours of `now`. `conn` is the sport's store, used read-only (the
    pool recorder runs with write=False). Never raises: failures are reported in `errors`."""
    now = now or utc_now()
    out = {"status": "success", "dry_run": dry_run, "now": now, "created": 0, "would_record": 0, "already_recorded": 0,
           "eligible_games": 0, "skipped": {}, "errors": {}, "policies": [{"policy_id": p.policy_id, "sha": p.sha} for p in policies]}
    try:
        check_unique_names(policies)
    except PolicyError as exc:
        out["status"], out["errors"]["recorder"] = "failed", str(exc)
        return out
    specs = list({c.spec.name: c.spec for p in policies for c in p.challengers}.values())
    try:
        res = _record_stored_only(conn, now, lines, specs)
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
                            "market, line, p_a, p_mid, p_b, cutoff_at, tipoff_at, generated_at, dataset_version, model_version, recorded_at) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (pol.policy_id, pol.sha, c.spec.name, c.spec_json, c.spec_hash, defn, f["game_id"], f["market"], f["line"],
                             *f["p"], now, tip[f["game_id"]], f["generated_at"], res.get("dataset_version", ""), f"challenger:{c.spec.name}:{defn}", utc_now()))
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
    r.add_argument("--now", help="fixed cutoff (UTC, for rehearsals and tests); default the clock. Needs --dry-run or --store")
    r.add_argument("--day", help="soccer only: the match date to price (default the UTC date of now); needs --now")
    r.add_argument("--store", help="challenger store path (a rehearsal copy); the default is <data dir>/research/challengers.sqlite")
    a = p.parse_args(argv)
    if a.day and (a.sport != "soccer" or not a.now):
        print(json.dumps({"status": "refused", "reason": "--day is for soccer rehearsals and needs --now"}))
        return 2
    if a.now and not a.dry_run and (not a.store or Path(a.store).resolve() == store_path().resolve()):
        print(json.dumps({"status": "refused", "reason": "--now backdates the cutoff: use --dry-run, or an explicit --store that is not the live store"}))
        return 2
    if not a.dry_run and release.enabled():       # the same release checks as the other delivery jobs that write data
        problems = release.check(a.sport)["problems"]
        if problems:
            print(json.dumps({"status": "refused", "reason": "release guard: " + "; ".join(problems)}))
            return 2
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
    recorder = importlib.import_module(f"{__package__}.{SPORT_MODULES[a.sport]}").record if a.sport in SPORT_MODULES else record
    extra = {"day": a.day} if a.day else {}
    out = recorder(conn, Path(a.store) if a.store else store_path(), policies=policies, now=a.now, dry_run=a.dry_run, **extra)
    out.pop("skipped", None) if not out["skipped"] else None
    print(json.dumps(out, indent=2, sort_keys=True, default=str))
    return 0 if out["status"] in ("success", "partial") else 1


if __name__ == "__main__":
    sys.exit(main())
