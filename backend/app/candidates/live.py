"""Prospective recording of registry candidates and the candidate roster (docs/CANDIDATE_POOL.md §6).

`record` runs inside the two-hour cycle after the core research step. It never raises into its caller: every failure is
reported per candidate, and a missing or stale input skips only that candidate for that game. Forecasts go only to the
append-only `candidate_forecasts` table under model_version 'research-cand:<name>:<definition>', so no primary read, score,
vote, average or stacker can see them. Fitted artifacts are persisted per (candidate, definition, season) with their
training cutoff and fingerprints and refit only on the declared schedule (season start).
"""
import fcntl
import hashlib
import json
import os
import pickle
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .. import backtest, config, live
from ..db import transaction, utc_now
from . import deep, engine, features, registry

PREFIX = "research-cand"
WINDOW = timedelta(hours=2)
STALE_BOX_GAMES = 5          # as shadow.STALE_RECENT_GAMES (NBA box scores)
STALE_SHOT_GAMES = 3         # as agreement.STALE_SHOT_GAMES (NHL shots)
FMT = "%Y-%m-%dT%H:%M:%SZ"
EVIDENCE_DIR = Path(__file__).resolve().parents[3] / "docs" / "evidence" / "candidate-pool"


def model_version(spec) -> str:
    return f"{PREFIX}:{spec.name}:{registry.definition(spec)}"


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, FMT).replace(tzinfo=timezone.utc)


# --- Artifacts ------------------------------------------------------------------------------------------------------
def artifact_dir(sport: str) -> Path:
    return config.data_dir() / "research" / "candidates" / "artifacts" / sport


def _fingerprint(rows) -> str:
    """Training set identity as first used: game ids only. Kept so artifacts fitted under it are still found."""
    return hashlib.sha256("\n".join(sorted(r.game_id for r in rows)).encode()).hexdigest()[:16]


def _results_fingerprint(rows) -> str:
    """Training set identity including each game's labels, so a later score correction gives a new fit instead of
    silently reusing the artifact trained on the old score. Used for every new fit (prefix r2)."""
    body = "\n".join(f"{r.game_id}|{json.dumps(r.y, sort_keys=True, default=str)}" for r in sorted(rows, key=lambda r: r.game_id))
    return "r2" + hashlib.sha256(body.encode()).hexdigest()[:14]


def ensure_fit(spec, rows: list, season: int, adopted: Optional[list] = None, write: bool = True) -> Optional[dict]:
    """The season-start fit for `season` (earlier seasons only), loaded from disk when its training set is unchanged and
    its bytes match their digest. A stored file without a digest (saved before digests existed) is never loaded: it is
    kept as `<name>.legacy-<sha12>` and superseded by a deterministic refit, whose provenance goes to `adopted`.
    With `write=False` (dry run) nothing on disk is touched: a missing or undigested artifact is fitted in memory only."""
    train = [r for r in rows if r.season < season and r.y is not None]
    if len(train) < registry.MIN_TRAIN[spec.sport]:
        return None
    defn = registry.definition(spec)
    fp = _results_fingerprint(train)
    folder = artifact_dir(spec.sport) / spec.name.replace(":", "__")
    path = folder / f"{defn}-{season}-{fp}.pkl"
    # An artifact fitted before results were part of the fingerprint is kept in force (its training results cannot be
    # re-checked, and refitting every candidate mid-season would change every fit at once); new fits use the new name.
    earlier = folder / f"{defn}-{season}-{_fingerprint(train)}.pkl"
    if not path.exists() and earlier.exists() and _sidecar(earlier).exists():
        path = earlier
    if not write:
        if path.exists() and _sidecar(path).exists():
            return _load_verified(path)
        if path.exists() and adopted is not None:
            adopted.append({"artifact": path.name, "dry_run": True, "action": "would be superseded by a refit (not done: dry run)"})
        return {**_fit(spec, train, season, defn, fp), "artifact_path": None}
    path.parent.mkdir(parents=True, exist_ok=True)
    with _locked(path):                  # one writer per artifact: the file and its digest always belong together
        if path.exists() and _sidecar(path).exists():
            return {**_load_verified(path), "artifact_path": str(path)}
        if path.exists():                # no digest: never unpickled
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            path.replace(path.with_name(f"{path.name}.legacy-{digest[:12]}"))
        art = _fit(spec, train, season, defn, fp)
        _write(path, art)
        return {**art, "artifact_path": str(path)}


def legacy_files(art: dict) -> list:
    """Superseded legacy files beside this artifact (kept, never loaded), with what is known about each."""
    if not art.get("artifact_path"):
        return []
    path = Path(art["artifact_path"])
    out = []
    for f in sorted(path.parent.glob(path.name + ".legacy*")):
        data = f.read_bytes()
        out.append({"artifact": path.name, "legacy_file": f.name, "legacy_sha256": hashlib.sha256(data).hexdigest(),
                    "legacy_file_written_at": datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).strftime(FMT),
                    "new_sha256": _sidecar(path).read_text().strip(), "definition": art["definition"],
                    "season": art["season"], "training_games": art["training_games"],
                    "results_fingerprint": art["results_fingerprint"], "content_digest": art["artifact_sha256"]})
    return out


def _fit(spec, train: list, season: int, defn: str, fp: str) -> dict:
    is_deep = deep.is_deep(spec)       # fitted with torch / tensorflow, stored as NumPy weights (candidates/deep.py)
    model = deep.fit(spec, train) if is_deep else engine.fit(spec, train)
    sd = None
    if spec.head == "normal":
        residuals = deep.residuals(spec, train) if is_deep else engine.walk(spec, train)[2]
        sd = engine.residual_sd(residuals, season)
    blob = pickle.dumps(model.objects)
    return {"model": model, "sd": sd, "season": season, "definition": defn, "training_games": len(train),
            "results_fingerprint": fp, "results_available_through": max(r.result_available_at for r in train),
            "artifact_sha256": hashlib.sha256(blob).hexdigest(), "info": model.info, "fitted_at": utc_now()}


def _write(path: Path, art: dict) -> None:
    """Data first, then its digest, each via a unique temporary file and an atomic rename (a crash in between leaves a
    file without a digest, which is superseded by a refit, never loaded)."""
    data = pickle.dumps(art)
    tag = f".{os.getpid()}.{time.monotonic_ns()}.tmp"
    tmp, side = path.with_name(path.name + tag), _sidecar(path)
    tmp.write_bytes(data)
    tmp.replace(path)
    stmp = side.with_name(side.name + tag)
    stmp.write_text(hashlib.sha256(data).hexdigest() + "\n")
    stmp.replace(side)


@contextmanager
def _locked(path: Path):
    with open(path.with_name(path.name + ".lock"), "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


ADOPTION_LIMITATION = (
    "The legacy file had no digest, so it was not trusted or loaded; it is kept beside the artifact as '.legacy'. It was "
    "superseded by a deterministic refit from the same earlier-season data (same definition, season and training-set "
    "fingerprint). When the refit's content digest equals the digest the earlier code registered in model_fits at first "
    "use, the forecasts recorded before came from a model with identical content. This does not establish that the "
    "legacy file's bytes were unmodified afterwards (they are no longer used).")


class ArtifactIntegrityError(RuntimeError):
    """A stored fit whose bytes do not match its recorded digest: the candidate is unavailable (never refit or replaced
    silently, never a substitute model)."""


def _sidecar(path: Path) -> Path:
    return path.with_name(path.name + ".sha256")


def _load_verified(path: Path) -> dict:
    """Checks the file's sha256 against its digest BEFORE unpickling (a pickle round trip is not byte-stable, so the whole
    stored file is what is verified)."""
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != _sidecar(path).read_text().strip():
        raise ArtifactIntegrityError(f"artifact {path.name}: digest mismatch")
    return pickle.loads(data)


def _record_adoption(conn, spec, rec: dict) -> bool:
    """Links a superseded legacy artifact to the fit the earlier code registered for the same identity (definition,
    season, training-set fingerprint) and records whether the refit reproduced its content digest (append-only)."""
    rec["candidate"] = spec.name
    # Looked up before this cycle registers its own fit, so a match was registered by the earlier code.
    # Only a fit registered before digests existed (before migration 0020) can be the earlier code's: a retried adoption
    # must never compare the refit with its own registration.
    fit = conn.execute("SELECT fit_id, first_used_at, params_digest FROM model_fits WHERE member=? AND definition=? "
                       "AND trained_season=? AND results_fingerprint=? AND first_used_at < "
                       "COALESCE((SELECT applied_at FROM schema_migrations WHERE version=20), '9999') "
                       "ORDER BY first_used_at, fit_id LIMIT 1",
                       (f"cand:{spec.name}", rec["definition"], rec["season"], rec["results_fingerprint"])).fetchone() \
        if _has(conn, "model_fits") else None
    rec["registered_fit"] = None if fit is None else {"fit_id": fit[0], "first_used_at": fit[1], "params_digest": fit[2]}
    rec["registered_digest_match"] = None if fit is None else fit[2] == rec["content_digest"][:24]
    rec["limitation"] = ADOPTION_LIMITATION
    if not _has(conn, "artifact_adoptions") or conn.execute(
            "SELECT 1 FROM artifact_adoptions WHERE artifact=? AND legacy_sha256=?", (rec["artifact"], rec["legacy_sha256"])).fetchone():
        return False
    rec["superseded_at"] = utc_now()
    with transaction(conn):
        conn.execute(
                "INSERT INTO artifact_adoptions (candidate, artifact, legacy_sha256, legacy_file_written_at, superseded_at, "
                "new_sha256, definition, trained_season, training_games, results_fingerprint, content_digest, "
                "registered_fit_id, registered_digest_match, limitation) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (spec.name, rec["artifact"], rec["legacy_sha256"], rec["legacy_file_written_at"], rec["superseded_at"],
                 rec["new_sha256"], rec["definition"], rec["season"], rec["training_games"], rec["results_fingerprint"],
                 rec["content_digest"], None if fit is None else fit[0],
                 None if fit is None else int(rec["registered_digest_match"]), rec["limitation"]))
    return True


def _register_fit(conn, sport, spec, art, now) -> Optional[int]:
    from .. import fit_register
    ident = {"schedule": "season_start", "fit_kind": "season_start", "trained_season": art["season"],
             "training_games": art["training_games"], "results_fingerprint": art["results_fingerprint"],
             "results_available_through": art["results_available_through"], "params_digest": art["artifact_sha256"][:24]}
    return fit_register.record(conn, sport, f"cand:{spec.name}", art["definition"], ident, now)


# --- Recording ------------------------------------------------------------------------------------------------------
def _recorder(conn, eng, upcoming_ids, now):
    if eng.sport == "nba":
        _, cfg = backtest.load_frozen_decision(conn)
        return features.NbaRecorder(cfg)
    from .. import nhl_eval, sports
    from ..models.hockey import GoalsConfig, HockeyEloConfig
    from . import goalies
    d = nhl_eval.load_decision(conn)
    detail = goalies.load(conn)
    actual = goalies.actual_starters(conn, detail)
    rec = None

    def starters(g):
        if g.game_id in upcoming_ids:      # pregame: the latest listing retrieved before the forecast only
            listed = goalies.listed_starters(conn, [g.game_id], now, rec.goalies.names)[g.game_id]
            return ({side: v[0] for side, v in listed.items()},
                    {"goalie_source": "listing", "listing": {side: {"status": v[1], "retrieved_at": v[2], "name": v[3],
                                                                   "matched": v[0] is not None} for side, v in listed.items()}})
        return actual.get(g.game_id, {}), {"goalie_source": "actual_starter"}
    rec = features.NhlRecorder(HockeyEloConfig(**d["hockey_elo_config"]), GoalsConfig(**d["goals_config"]),
                               sports.NHL.rating_key, detail, starters=starters)
    return rec


def _events(conn, eng, rows, known, now):
    if eng.sport == "nba":
        return [e for e in backtest.load_box_events(conn, "live")
                if e.game.game_id in known and max(e.available_at, known[e.game.game_id].result_available_at) <= now]
    from .. import nhl_shots
    return [e for e in nhl_shots.load_events(conn, rows) if max(e.available_at, known[e.game.game_id].result_available_at) <= now]


def _stale(eng, rec, game, recent) -> Optional[str]:
    if eng.sport == "nba":
        lines, n, what = rec.eff.lines, STALE_BOX_GAMES, "box score"
    else:
        lines, n, what = rec.xg.lines, STALE_SHOT_GAMES, "shots"
    for team in (game.home_team_id, game.away_team_id):
        missing = [gid for gid in recent.get((game.season, team), [])[:n] if gid not in lines]
        if missing:
            return f"stale inputs: {len(missing)} recent final(s) without {what} ({team})"
    return None


def _key(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def record(conn, *, game_ids: Optional[list] = None, now: Optional[str] = None, lines: Optional[dict] = None,
           write: bool = True, specs: Optional[list] = None) -> dict:
    """Two-hour candidate forecasts for eligible games. `lines` maps (game_id, market) -> {"line", "source", "ref"}: the
    exact line the core cohort used (default: read from the latest core cohort at `now`); a line market without one is
    skipped (never a substitute line)."""
    out = {"status": "success", "created": 0, "already_recorded": 0, "skipped": {}, "errors": {}, "forecasts": []}
    try:
        _record(conn, game_ids, now, lines, write, specs, out)
    except Exception as exc:         # never fails the caller's cycle
        out["status"] = "failed"
        out["errors"]["pool"] = f"{type(exc).__name__}: {exc}"
    if out["status"] == "success" and (out["errors"] or out["skipped"]):
        out["status"] = "partial"
    return out


def fit_deep_artifacts(conn, now: Optional[str] = None, write: bool = True) -> dict:
    """Fits (or finds) the current season's artifact of every deep candidate of this store's sport, on the same
    point-in-time history a live cycle would build now. Run in the research environment; it reads the store and writes only
    artifact files, so the release can then apply them without torch or tensorflow."""
    now = now or utc_now()
    eng = live.engine(conn)
    rows = live.available_rows(conn, now)
    known = {r.game.game_id: r for r in rows}
    walk = features.walk(_recorder(conn, eng, set(), now), rows, _events(conn, eng, rows, known, now), deliver_until=now)
    history = list(walk.values())
    types = eng.eligible_types
    nxt = conn.execute(f"SELECT season FROM games WHERE status='scheduled' AND tipoff_at > ? AND season_type IN "
                       f"({','.join('?' * len(types))}) ORDER BY tipoff_at LIMIT 1", (now, *types)).fetchone()
    out = {"sport": eng.sport, "as_of": now, "season": nxt["season"] if nxt else None, "artifacts": {}}
    if nxt is None:
        return out
    for spec in registry.for_sport(eng.sport):
        if not deep.is_deep(spec):
            continue
        art = ensure_fit(spec, history, nxt["season"], write=write)
        out["artifacts"][spec.name] = None if art is None else {
            "definition": art["definition"], "training_games": art["training_games"], "artifact_sha256": art["artifact_sha256"],
            "results_fingerprint": art["results_fingerprint"], "epochs": art["info"].get("epochs"),
            "path": art.get("artifact_path")}
    return out


def core_lines(conn, game_ids, now: str) -> dict:
    """(game_id, market) -> the exact line of the latest core research cohort generated at or before `now` (never the
    expanded experiment's), with its cohort id as the reference — for every market, so a winner/reg3 forecast also names
    the cohort it was made against (a new cohort gives a new candidate row). Used when the caller passes no lines."""
    out = {}
    for gid in game_ids:
        for r in conn.execute("SELECT cohort_id, market, line, line_source FROM research_cohorts WHERE game_id=? "
                              "AND generated_at<=? AND definition NOT LIKE '%:expanded-v1' "
                              "AND tipoff_at = (SELECT tipoff_at FROM games WHERE game_id=?) ORDER BY generated_at, cohort_id",
                              (gid, now, gid)):
            out[(gid, r["market"])] = {"line": r["line"], "source": f"core_cohort:{r['line_source']}" if r["line"] is not None
                                       else "none", "ref": f"research_cohorts:{r['cohort_id']}"}
    return out


def _record(conn, game_ids, now, lines, write, specs, out) -> None:
    fixed = now is not None
    now = now or utc_now()
    eng = live.engine(conn)
    horizon = live._ts(_parse(now) + WINDOW)
    rows = live.available_rows(conn, now)
    known = {r.game.game_id: r for r in rows}
    types = eng.eligible_types
    schedule = [g for g in conn.execute(
        f"SELECT * FROM games WHERE status NOT IN ('postponed','canceled') AND tipoff_at <= ? "
        f"AND season_type IN ({','.join('?' * len(types))}) ORDER BY tipoff_at, game_id", (horizon, *types))
        if g["game_id"] not in known]
    eligible = [g for g in schedule if g["status"] == "scheduled" and now < g["tipoff_at"] <= horizon
                and (game_ids is None or g["game_id"] in game_ids)]
    out["eligible_games"] = len(eligible)
    if not eligible:
        return
    ids = {g["game_id"] for g in eligible}
    if lines is None:
        lines = core_lines(conn, ids, now) if _has(conn, "research_cohorts") else {}
    rec = _recorder(conn, eng, ids, now)
    events = _events(conn, eng, rows, known, now)
    walk = features.walk(rec, rows, events, deliver_until=now)
    history = list(walk.values())
    upcoming = {}
    for g in schedule:
        game = eng.pregame(g)
        rec.predict(game)
        if game.game_id in ids:
            row = rec.rows[game.game_id]
            row.cutoff_at = now
            upcoming[game.game_id] = (game, row)
    recent = {}
    for r in sorted(rows, key=lambda r: r.game.tipoff_at, reverse=True):
        for team in (r.game.home_team_id, r.game.away_team_id):
            recent.setdefault((r.game.season, team), []).append(r.game.game_id)
    table = features.table_version(walk)
    dataset = _key(eng.dataset_version(rows), table, sorted((g["game_id"], g["tipoff_at"]) for g in schedule))[:24]
    out["dataset_version"], out["feature_table"] = dataset, table
    for spec in specs or registry.for_sport(eng.sport):
        try:
            if not registry.available(spec) and not deep.is_deep(spec):
                out["skipped"][spec.name] = {"all": f"optional dependency {spec.extra} not installed"}
                continue
            _record_spec(conn, eng, spec, history, upcoming, recent, rec, lines, now, fixed, write, dataset, table, out)
        except deep.DependencyMissing as exc:      # no stored artifact for this season and no library here: skipped, visible
            out["skipped"][spec.name] = {"all": str(exc)}
        except Exception as exc:
            out["errors"][spec.name] = f"{type(exc).__name__}: {exc}"


def _record_spec(conn, eng, spec, history, upcoming, recent, rec, lines, now, fixed, write, dataset, table, out):
    mv = model_version(spec)
    by_season: dict = {}
    for gid, (game, row) in upcoming.items():
        by_season.setdefault(game.season, []).append(gid)
    for season, gids in by_season.items():
        adopted: list = []
        art = ensure_fit(spec, history, season, adopted, write=write)
        out.setdefault("artifacts_adopted", []).extend(adopted)      # dry run: reported only
        if write and art is not None:
            for entry in legacy_files(art):  # retried each cycle until recorded (a failed insert never loses provenance)
                if _record_adoption(conn, spec, entry):
                    out.setdefault("artifacts_adopted", []).append(entry)
        if art is None:
            for gid in gids:
                out["skipped"].setdefault(spec.name, {})[gid] = "not trained: too few earlier-season games"
            continue
        fit_id = None
        if write:
            try:
                fit_id = _register_fit(conn, eng.sport, spec, art, now)
            except Exception as exc:     # a fit record never withholds a forecast
                out.setdefault("fit_register_errors", {})[spec.name] = f"{type(exc).__name__}: {exc}"
        todo = [upcoming[g] for g in gids]
        outputs = engine.outputs(spec, art["model"], [r for _, r in todo], art["sd"])
        for (game, row), o in zip(todo, outputs):
            try:
                _record_game(conn, spec, mv, art, fit_id, game, row, o, rec, recent, lines, now, fixed, write, dataset,
                             table, eng, out)
            except Exception as exc:     # one game's failure (e.g. a start change mid-cycle) never withholds the others
                conn.rollback()
                out["errors"][f"{spec.name}:{game.game_id}"] = f"{type(exc).__name__}: {exc}"


def _record_game(conn, spec, mv, art, fit_id, game, row, o, rec, recent, lines, now, fixed, write, dataset, table, eng, out):
    reason = _stale(eng, rec, game, recent)
    if reason is None and not engine.ready(o):
        reason = "not ready: no out-of-sample residual spread yet"
    if reason:
        out["skipped"].setdefault(spec.name, {})[game.game_id] = reason
        return
    for market in spec.markets:
        if market in ("winner", "reg3"):
            ln = lines.get((game.game_id, market))
            line, source, ref = None, "none", ln.get("ref") if ln else None
        else:
            ln = lines.get((game.game_id, market))
            if ln is None:
                out["skipped"].setdefault(spec.name, {})[f"{game.game_id}:{market}"] = "no exact line from the core cohort"
                continue
            line, source, ref = ln["line"], ln.get("source", "core_cohort"), ln.get("ref")
        vec = engine.vector(o, market, line)
        if vec is None:
            continue
        pa, pm = round(vec[0], 9), round(vec[1], 9)
        pb = round(1 - pa - pm, 9)
        params = {k: (round(v, 6) if isinstance(v, float) else v) for k, v in o.items()}
        inputs = {"feature_table": table, "features": {k: round(float(v), 6) for k, v in row.x.items()}, **row.meta}
        # Same fit, same inputs and outputs for the same cohort line → the same row (a retry on the real clock is a
        # no-op); `dataset` is not in the key because its feature-table digest includes the cycle's cutoff time.
        key = _key(mv, game.game_id, market, line, ref, game.tipoff_at, art["artifact_sha256"], params,
                   inputs["features"])
        generated = now if fixed else utc_now()
        if generated >= game.tipoff_at:
            out["skipped"].setdefault(spec.name, {})[f"{game.game_id}:{market}"] = "calculation finished at or after tipoff"
            continue
        item = {"game_id": game.game_id, "candidate": spec.name, "market": market, "line": line,
                "p": [pa, pm, pb], "model_version": mv, "generated_at": generated}
        if write:
            with transaction(conn):
                new = insert_forecast(conn, (
                    game.game_id, spec.name, mv, market, line, source, ref, pa, pm, pb, json.dumps(params, sort_keys=True),
                    json.dumps(inputs, sort_keys=True, default=str), fit_id, art["artifact_sha256"],
                    art["results_available_through"], dataset, game.tipoff_at, generated, key))
            out["created" if new else "already_recorded"] += 1
        out["forecasts"].append(item)


def insert_forecast(conn, values) -> bool:
    """Shared ledger insertion; caller owns the transaction. Constraints reject invalid rows; retries are no-ops."""
    if conn.execute("SELECT 1 FROM candidate_forecasts WHERE idempotency_key=?", (values[-1],)).fetchone():
        return False
    conn.execute(
        "INSERT INTO candidate_forecasts (game_id, candidate, model_version, market, line, "
        "line_source, line_ref, p_a, p_mid, p_b, params, inputs, fit_id, artifact_sha256, training_cutoff, "
        "dataset_version, tipoff_at, generated_at, idempotency_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values)
    return True


# --- Reads ----------------------------------------------------------------------------------------------------------
def latest(conn, game_id: str, as_of: Optional[str] = None) -> list:
    """Latest pregame forecast per candidate and market for one game (research display only)."""
    as_of = as_of or "9999"
    rows = conn.execute("SELECT * FROM candidate_forecasts WHERE game_id=? AND generated_at<=? AND generated_at<tipoff_at "
                        "ORDER BY generated_at DESC, forecast_id DESC", (game_id, as_of)).fetchall()
    seen, outl = set(), []
    for r in rows:
        k = (r["candidate"], r["market"])
        if k in seen:
            continue
        seen.add(k)
        outl.append({"candidate": r["candidate"], "market": r["market"], "line": r["line"],
                     "probabilities": [r["p_a"], r["p_mid"], r["p_b"]], "model_version": r["model_version"],
                     "generated_at": r["generated_at"], "inputs": json.loads(r["inputs"])})
    return outl


def _evidence(sport: str) -> dict:
    out = {}
    for stage in ("screen", "assess"):
        p = EVIDENCE_DIR / f"{sport}-{stage}.json"
        out[stage] = json.loads(p.read_text()) if p.exists() else None
    return out


def held_out_gains(market_assess: dict, name: str) -> list:
    """Stage-2 comparisons where adding the candidate helped with a 95% upper bound below zero: the candidate against the
    strongest member, or the same pair/triple average with versus without it — counted only when that combination is also
    better than the strongest member alone (repairing a weak combination is not a gain)."""
    out = []
    c = (market_assess.get("candidates") or {}).get(name) or {}
    if c and c["minus_strongest"]["ci95"][1] < 0:
        out.append("alone vs strongest member")
    strongest = (market_assess.get("existing") or {}).get(market_assess.get("strongest_existing"), {}).get("log_loss")
    for combo in market_assess.get("combinations") or []:
        if (combo["combination"].endswith(f"(ablation: remove {name})") and combo["with_minus_without"]["ci95"][1] < 0
                and strongest is not None and combo["log_loss_with"] < strongest):
            out.append(combo["combination"].split(" (ablation")[0])
    return out


def roster(conn, sport: str, as_of: Optional[str] = None) -> dict:
    """Every registry candidate with training status, markets, latest fit, evaluation, prospective sample size,
    combination participation and selection eligibility; plus the family audit. Eligibility is the sealed pool
    registry's route (docs/CANDIDATE_POOL.md §7) and the pool decision in force, never a count on its own."""
    from .. import agreement_tracking
    from . import selection as pool_selection
    as_of = as_of or utc_now()
    ev = _evidence(sport)
    try:
        reg = pool_selection.load_registry()
    except pool_selection.RegistryError as exc:
        reg, reg_error = None, str(exc)
    else:
        reg_error = None
    defn = pool_selection.core_definition(conn) if reg else None
    items = []
    for spec in registry.for_sport(sport):
        fit = conn.execute("SELECT * FROM model_fits WHERE sport=? AND member=? AND first_used_at<=? "
                           "ORDER BY first_used_at DESC, fit_id DESC LIMIT 1",
                           (sport, f"cand:{spec.name}", as_of)).fetchone() if _has(conn, "model_fits") else None
        n = settled = 0
        if _has(conn, "candidate_forecasts"):
            r = conn.execute("SELECT COUNT(DISTINCT cf.game_id) AS n, COUNT(DISTINCT CASE WHEN g.status='final' AND "
                             "g.result_available_at<=? THEN cf.game_id END) AS s FROM candidate_forecasts cf JOIN games g "
                             "USING (game_id) WHERE cf.candidate=? AND cf.generated_at<=?", (as_of, spec.name, as_of)).fetchone()
            n, settled = r["n"], r["s"]
        evaluation, combos = {}, []
        for market in spec.markets:
            sm = ((ev["screen"] or {}).get("markets") or {}).get(market) or {}
            c = (sm.get("candidates") or {}).get(spec.name)
            if c:
                evaluation[market] = {"stage": "screen (exploratory)", "seasons": sm.get("seasons"),
                                      "log_loss": c["log_loss"], "vs_strongest": c["minus_strongest"],
                                      "strongest": sm.get("strongest_existing"), "pair_gain": c.get("pair_gain"),
                                      "stack_gain": c.get("stack_gain"), "shortlisted": spec.name in (sm.get("shortlist") or [])}
            am = ((ev["assess"] or {}).get("markets") or {}).get(market) or {}
            a = (am.get("candidates") or {}).get(spec.name)
            if a:
                confirmed = held_out_gains(am, spec.name)
                evaluation[market]["assess"] = {"seasons": am.get("seasons"), "log_loss": a["log_loss"],
                                                "vs_strongest": a["minus_strongest"], "confirmed_gains": confirmed}
                if confirmed:
                    combos.append(market)
        shortlisted = any(e.get("shortlisted") for e in evaluation.values())
        status = ("trained" if fit else "awaiting a fit in the research environment" if deep.is_deep(spec) else
                  "dependency missing" if not registry.available(spec) else "registered (trains at first eligible game)")
        state = pool_selection.candidate_state(conn, reg, sport, spec.name, as_of, defn) if reg else {}
        alone = [m for m, st in state.items() if any(x["route"] == "standalone" for x in st["sources"])]
        inside = sorted({x["source"] for st in state.values() for x in st["sources"] if x["route"] == "combination"})
        selected = [f"{m}: {st['decision']['rule']['source']}" for m, st in state.items() if st["selected_now"]]
        off = sorted(m for m, st in state.items() if st.get("suspended"))
        label = ("reference only (never selected)" if spec.role == "reference" else
                 f"suspended in {', '.join(off)}: registered on evidence that used the actual starting goalie (known only "
                 f"after the start); recorded, never selectable" if off and not alone and not inside else
                 f"selected now ({'; '.join(selected)})" if selected else
                 f"selectable alone in {', '.join(alone)} (standalone held-out value); picked only when the pool selector's "
                 f"evidence thresholds are met" if alone else
                 f"selectable only inside {', '.join(inside)} (incremental value in a combination); never alone" if inside else
                 "shortlisted, but no supported standalone or combination value: research only" if shortlisted else
                 "research only (no supported standalone or combination value)")
        # Coverage of the CURRENT candidate identity only; earlier identities are reported apart.
        cdef = registry.definition(spec)
        cur_fit = conn.execute("SELECT 1 FROM model_fits WHERE sport=? AND member=? AND definition=? AND first_used_at<=? "
                               "LIMIT 1", (sport, f"cand:{spec.name}", cdef, as_of)).fetchone() if _has(conn, "model_fits") else None
        latest_at, earlier_at = (conn.execute(
            "SELECT MAX(CASE WHEN model_version=? THEN generated_at END), MAX(CASE WHEN model_version<>? THEN generated_at END) "
            "FROM candidate_forecasts WHERE candidate=? AND generated_at<=?",
            (model_version(spec), model_version(spec), spec.name, as_of)).fetchone()
            if _has(conn, "candidate_forecasts") else (None, None))
        tracked = [m for m in spec.markets if spec.name in agreement_tracking.candidates_for(sport, m, live_ok=True)]
        items.append({"coverage": {"fitted": cur_fit is not None, "earlier_fit": fit is not None and cur_fit is None,
                                   "latest_forecast_at": latest_at, "earlier_forecast_at": earlier_at,
                                   "forecasting": _recent(latest_at, as_of),
                                   "in_agreement_tracking": tracked, "votes": False,
                                   "selectable": {"alone": alone, "inside": inside}},
                      "name": spec.name, "family": spec.family, "head": spec.head, "features": spec.features,
                      "role": spec.role, "why": spec.why, "markets": list(spec.markets), "refit": spec.refit,
                      "definition": registry.definition(spec), "training_status": status,
                      "latest_fit": dict(fit) if fit else None, "evaluation": evaluation,
                      "prospective": {"games": n, "settled": settled},
                      "combination_participation": sorted(m for m, st in state.items() if st["sources"]) if reg else combos,
                      "pool": state, "selection": label})
    from . import calibrated
    items.extend(calibrated.roster(conn, sport, as_of))
    return {"sport": sport, "pool": registry.POOL, "as_of": as_of, "candidates": items, "members": core_members(conn, sport, as_of),
            "coverage_note": f"Fitted: a model_fits row for the current version. Forecasting: a current-version forecast in "
                             f"the {RECENT_DAYS} days before as_of; earlier versions are listed apart. Candidates never vote; 'agreement tracking' means recorded in the sealed tracking groups. "
                             "Selectable: a registered pool source (alone or inside an average), not suspended.",
            "registry": None if reg is None else {"id": pool_selection.registry_id(reg), "effective_from": reg["effective_from"],
                                                  "routes": reg["routes"], "rule": reg["rule"],
                                                  "suspended": pool_selection.suspended(reg)},
            "registry_error": reg_error,
            "family_audit": list(registry.FAMILY_AUDIT),
            "note": "Research only. Evaluations are exploratory (spent seasons); only prospective forecasts can show "
                    "improvement. Candidates never vote and never replace the primary."}


RECENT_DAYS = 7


def _recent(ts: Optional[str], as_of: str) -> bool:
    return ts is not None and _parse(as_of) - _parse(ts) <= timedelta(days=RECENT_DAYS)


def _current_definitions(conn) -> tuple:
    """(agreement digest, research definition) of the code running now; (None, None) when the store cannot build them."""
    from .. import agreement, research_live
    try:
        return agreement.definition_hash(agreement.models(conn)), research_live.definition(conn)
    except Exception:
        return None, None


def core_members(conn, sport: str, as_of: str) -> list:
    """The research members per market: whether each votes in agreement, its fit and its latest forecast in a research
    cohort (two-hour window), for the CURRENT definition (winner fits: agreement digest; line fits and cohorts: research
    definition). Activity of earlier definitions is reported separately (`earlier_*`), never as current. Read-only."""
    from .. import agreement, research_live
    digest, defn = _current_definitions(conn)
    current_cohorts = {defn, research_live.expanded_definition(defn)} if defn else set()
    since = (_parse(as_of) - timedelta(days=RECENT_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    last: dict = {}
    if _has(conn, "research_cohorts"):
        for r in conn.execute("SELECT market, members, generated_at, definition FROM research_cohorts WHERE generated_at<=? "
                              "AND generated_at>=? ORDER BY generated_at", (as_of, since)):
            now_ = r["definition"] in current_cohorts
            for m in json.loads(r["members"]):
                last[(r["market"] == "winner", m["name"], now_)] = r["generated_at"]
    out = []
    for winner, voters, shadow in ((True, agreement.NAMES[sport], agreement.SHADOW[sport]),
                                   (False, research_live.LINE_MEMBERS[sport], research_live.SHADOW_LINE_MEMBERS[sport])):
        fit_defn = digest if winner else defn
        for name in list(voters) + list(shadow):
            fits = conn.execute("SELECT member, definition, first_used_at, training_games FROM model_fits WHERE sport=? AND "
                                "member=? AND first_used_at<=? ORDER BY first_used_at DESC, fit_id DESC",
                                (sport, name, as_of)).fetchall() if _has(conn, "model_fits") else []
            cur = next((dict(f) for f in fits if fit_defn and f["definition"] == fit_defn), None)
            old = next((dict(f) for f in fits if not fit_defn or f["definition"] != fit_defn), None)
            at, old_at = last.get((winner, name, True)), last.get((winner, name, False))
            out.append({"name": name, "markets": "winner" if winner else "line and 3-way markets",
                        "votes": name in voters, "role": "voting member" if name in voters else "shadow (recorded, never votes)",
                        "current_definition": fit_defn, "fit_register": cur, "earlier_fit": old,
                        "latest_forecast_at": at, "forecasting": at is not None, "earlier_forecast_at": old_at,
                        "selectable": True})
    return out


def _has(conn, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None
