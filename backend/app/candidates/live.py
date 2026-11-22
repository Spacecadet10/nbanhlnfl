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
from ..models.score_candidates import usable_rates
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


DEGENERATE_GOALS = "not ready: goal rates put too much mass beyond the score grid"


def usable_goals(o: Optional[dict]) -> bool:
    """A goals-head output whose rates leave more than 10% of the Poisson mass beyond the score grid is not a forecast
    (its renormalised, draw-inflated grid would be misleading). Checked when applying outputs, not in `engine.ready`:
    engine.py is part of every candidate's sealed identity."""
    return o is None or o["head"] != "goals" or usable_rates(o["lh"], o["la"])


def _record_game(conn, spec, mv, art, fit_id, game, row, o, rec, recent, lines, now, fixed, write, dataset, table, eng, out):
    reason = _stale(eng, rec, game, recent)
    if reason is None and not engine.ready(o):
        reason = "not ready: no out-of-sample residual spread yet"
    if reason is None and not usable_goals(o):
        reason = DEGENERATE_GOALS
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


# --- Forward-test recorder and grader (five sports; light jobs, never in an official store) ----------------------------
# Registered candidate models are forecast BEFORE their games through each sport's research adapter
# (`forecast_upcoming(conn, now, model_name)`, a dict entry or an attribute) and settled AFTER the official result is final.
# The only store written is `<data dir>/research/forward-candidates.sqlite`; official stores are opened read-only (mode=ro,
# query_only). Both jobs run through `python -m app.learning_run --light --name forward-<kind> ...` (per-name lock, no heavy lock).
#   forward_models(sport, model_name, registered_at, active)   the registry; filled by the research driver
#   forward_forecasts   one row per (sport, model, game_id, market, line): the FIRST forecast made while the game is ahead; the
#                       whole contract record is kept (probs vector, line, line_source, quote, cutoff)
#   forward_results     outcome a|b|mid|push|void, log_loss and Brier on the full probs vector (push/void are not scored)
#   forward_runs        per-sport outcome of every run (evaluated | no_candidates | skipped | failed)
# Contract record: {"game_id","start_at","forecast_cutoff","market","line","probs": {"a","b"[,"mid"][,"push"]},"line_source","quote",...}.
# A record is rejected unless start_at > now and forecast_cutoff <= now. Settlement: the adapter's `settle(conn, row)` when it has
# one; otherwise generic, from the official store read-only (`_settle`): games table + markets.settle rules for nba/nhl/ncaab/nfl
# (OT counts as the market says, whole-line pushes, NFL tie voids the winner, a moved start voids), and the 90-minute score or
# corner counts with the soccer adapter's own rules for soccer.
FORWARD_DB = "forward-candidates.sqlite"
FORWARD_ADAPTERS = {"nba": "nba_eval", "nhl": "nhl_eval", "ncaab": "ncaab_cards", "nfl": "nfl_challengers", "soccer": "soccer_markets_eval"}
FORWARD_SPORTS = tuple(FORWARD_ADAPTERS)
FORWARD_CALL_TIMEOUT_S = 60
THREE_WAY = {"reg3", "result_1x2"}
FORWARD_SCHEMA = """
CREATE TABLE IF NOT EXISTS forward_models (sport TEXT NOT NULL, model_name TEXT NOT NULL, registered_at TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1, PRIMARY KEY (sport, model_name));
CREATE TABLE IF NOT EXISTS forward_forecasts (id INTEGER PRIMARY KEY AUTOINCREMENT, sport TEXT NOT NULL, model_name TEXT NOT NULL,
  game_id TEXT NOT NULL, market TEXT NOT NULL, line_key TEXT NOT NULL DEFAULT '', line REAL, line_source TEXT, issued_at TEXT NOT NULL,
  forecast_cutoff TEXT NOT NULL, start_at TEXT NOT NULL, probs TEXT NOT NULL, record TEXT NOT NULL,
  UNIQUE (sport, model_name, game_id, market, line_key));
CREATE TABLE IF NOT EXISTS forward_results (forecast_id INTEGER PRIMARY KEY REFERENCES forward_forecasts(id), graded_at TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('a','b','mid','push','void')), log_loss REAL, brier REAL);
CREATE TABLE IF NOT EXISTS forward_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, sport TEXT NOT NULL,
  at TEXT NOT NULL, outcome TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '');
CREATE TRIGGER IF NOT EXISTS forward_forecasts_no_update BEFORE UPDATE ON forward_forecasts
  BEGIN SELECT RAISE(ABORT, 'forward forecasts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS forward_forecasts_no_delete BEFORE DELETE ON forward_forecasts
  BEGIN SELECT RAISE(ABORT, 'forward forecasts are append-only'); END;
"""


class _Timeout(Exception):
    pass


@contextmanager
def _deadline(seconds: float):
    """SIGALRM time limit for one adapter call (main thread only; elsewhere the guard's wall clock is the limit)."""
    import signal
    import threading
    if not seconds or threading.current_thread() is not threading.main_thread():
        yield
        return

    def fire(_s, _f):
        raise _Timeout(f"adapter call exceeded {seconds} s")
    old = signal.signal(signal.SIGALRM, fire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def forward_connect(data_dir: Path):
    import sqlite3
    path = Path(data_dir) / "research" / FORWARD_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.executescript(FORWARD_SCHEMA)
    return conn


def _official(data_dir: Path, sport: str):
    """The official store, strictly read-only; None when this sport has no store."""
    import sqlite3
    from .. import sports
    p = Path(data_dir) / sports.store(sport).db_file
    if not p.is_file():
        return None
    conn = sqlite3.connect(f"{p.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def _adapter(sport: str, adapters: Optional[dict]):
    """A dict (research_adapter() result) or an object; None when the sport has none yet."""
    if adapters is not None:
        return adapters.get(sport)
    import importlib
    try:
        get = getattr(importlib.import_module("app.autoresearch"), "get_adapter", None)
        if get:
            return get(sport)
        return importlib.import_module(f"app.{FORWARD_ADAPTERS[sport]}").research_adapter()
    except (ImportError, AttributeError, KeyError):
        return None


def _fn(adapter, name: str):
    return adapter.get(name) if isinstance(adapter, dict) else getattr(adapter, name, None)


def _sealed_kwargs(produce) -> dict:
    """The NFL adapter reads the reserved seasons' results as forward state only through the sealed, logged read (nfl_live's
    activation): pass that choice and the access log when `forecast_upcoming` asks for them. Without an activation the call is
    refused by the adapter (reported as that model's error), never bypassed."""
    import inspect
    try:
        names = set(inspect.signature(produce).parameters)
    except (TypeError, ValueError):
        return {}
    if not {"sealed", "access_log"} <= names:
        return {}
    from .. import nfl_eval, nfl_live
    act = nfl_live.activation()
    return {"sealed": act["sealed"] if act else None, "access_log": nfl_eval.access_log_path()}


def _note(fwd, kind: str, sport: str, outcome: str, detail: str, at: str) -> None:
    fwd.execute("INSERT INTO forward_runs (kind, sport, at, outcome, detail) VALUES (?,?,?,?,?)", (kind, sport, at, outcome, detail[:500]))
    fwd.commit()


def forward_register(data_dir: Path, sport: str, model_name: str, now: Optional[str] = None, active: bool = True) -> None:
    fwd = forward_connect(data_dir)
    try:
        fwd.execute("INSERT INTO forward_models (sport, model_name, registered_at, active) VALUES (?,?,?,?) "
                    "ON CONFLICT(sport, model_name) DO UPDATE SET active = excluded.active", (sport, model_name, now or utc_now(), int(active)))
        fwd.commit()
    finally:
        fwd.close()


def _line_key(line) -> str:
    return "" if line is None else repr(float(line))


def forward_record(data_dir: Path, *, now: Optional[str] = None, sports=FORWARD_SPORTS, adapters: Optional[dict] = None,
                   timeout_s: float = FORWARD_CALL_TIMEOUT_S) -> dict:
    """One light pass: per sport and registered active model, ask the adapter for contract records of games still ahead and
    append the first record of each (model, game, market, line). Returns {sport: {"outcome", "recorded", "rejected", "errors"}}
    with outcome evaluated | no_candidates (no registered model, or nothing ahead) | skipped (no adapter or store) | failed.
    A failure in one model or sport never stops the others."""
    now = now or utc_now()
    fwd = forward_connect(data_dir)
    out = {}
    try:
        for sport in sports:
            res = out[sport] = {"outcome": "no_candidates", "recorded": 0, "rejected": 0, "errors": []}
            models = [r[0] for r in fwd.execute("SELECT model_name FROM forward_models WHERE sport=? AND active=1 ORDER BY model_name", (sport,))]
            if not models:
                _note(fwd, "record", sport, "no_candidates", "no registered model", now)
                continue
            adapter, conn = _adapter(sport, adapters), _official(data_dir, sport)
            produce = _fn(adapter, "forecast_upcoming") if adapter is not None else None
            if produce is None or conn is None:
                res["outcome"] = "skipped"
                res["errors"].append("no adapter" if produce is None else "no store")
                _note(fwd, "record", sport, "skipped", res["errors"][-1], now)
                if conn is not None:
                    conn.close()
                continue
            seen = 0
            try:
                for name in models:
                    try:
                        with _deadline(timeout_s):
                            rows = list(produce(conn, now, name, **_sealed_kwargs(produce)) or [])
                    except Exception as exc:                      # one model never stops the rest
                        res["errors"].append(f"{name}: {type(exc).__name__}: {exc}")
                        continue
                    seen += len(rows)
                    for r in rows:
                        try:
                            start, cut, probs = _parse(r["start_at"]).strftime(FMT), _parse(r["forecast_cutoff"]).strftime(FMT), r["probs"]
                            if start <= now or cut > now:
                                res["rejected"] += 1             # game already started, or the forecast uses later information
                                continue
                            if not probs or any(not 0.0 <= float(v) <= 1.0 for v in probs.values()):
                                res["rejected"] += 1
                                continue
                            line = r.get("line")
                            cur = fwd.execute(
                                "INSERT OR IGNORE INTO forward_forecasts (sport, model_name, game_id, market, line_key, line, line_source,"
                                " issued_at, forecast_cutoff, start_at, probs, record) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                                (sport, name, str(r["game_id"]), str(r["market"]), _line_key(line), None if line is None else float(line),
                                 r.get("line_source"), now, cut, start, json.dumps({k: float(v) for k, v in probs.items()}, sort_keys=True),
                                 json.dumps(r, sort_keys=True, default=str)))
                            res["recorded"] += cur.rowcount
                        except (KeyError, ValueError, TypeError) as exc:
                            res["rejected"] += 1
                            res["errors"].append(f"{name}: bad record ({type(exc).__name__}: {exc})")
                    fwd.commit()
            finally:
                conn.close()
            res["outcome"] = "failed" if res["errors"] and not res["recorded"] else ("evaluated" if res["recorded"] or seen else "no_candidates")
            _note(fwd, "record", sport, res["outcome"], f"recorded={res['recorded']} rejected={res['rejected']} {'; '.join(res['errors'])}", now)
    finally:
        fwd.close()
    return out


def _settle(sport: str, conn, row: dict) -> Optional[str]:
    """Realised a|b|mid|push|void from the official store (read-only), or None while the result is not final/available."""
    from .. import markets
    market, line = row["market"], row["line"]
    if sport == "soccer":
        return _settle_soccer(conn, row)
    g = conn.execute("SELECT status, home_score, away_score, tipoff_at FROM games WHERE game_id = ?", (row["game_id"],)).fetchone()
    if g is None:
        return None
    try:
        s = markets.settle(market, line, status=g["status"], home=g["home_score"], away=g["away_score"],
                           tipoff_at=g["tipoff_at"], forecast_tipoff_at=row["start_at"])
    except KeyError:
        return None
    if s in ("pending", "unscorable"):
        return None
    if s == "mid" and market not in THREE_WAY:
        return "push"
    return s


def _settle_soccer(conn, row: dict) -> Optional[str]:
    """90-minute result / goals / corners. A match decided after 90 minutes has no 90-minute score in the store: void."""
    from .. import soccer_markets_eval as sme
    m = conn.execute("SELECT status, decided_in, home_score, away_score, kickoff_at FROM soccer_matches WHERE match_id = ?", (row["game_id"],)).fetchone()
    if m is None:
        return None
    if m["status"] in ("postponed", "canceled", "abandoned"):
        return "void"
    if m["status"] != "final":
        return None
    if m["kickoff_at"] != row["start_at"] or m["decided_in"] != "regulation" or m["home_score"] is None or m["away_score"] is None:
        return "void"
    market, h, a = row["market"], m["home_score"], m["away_score"]
    if market.startswith("corners"):
        c = conn.execute("SELECT home_corners, away_corners FROM soccer_match_stats WHERE match_id = ? ORDER BY revision DESC LIMIT 1", (row["game_id"],)).fetchone()
        if c is None:
            return None                                          # corner counts may still arrive
        if c[0] == 0 and c[1] == 0:
            return "void"                                        # a both-zero count is "not delivered" (adapter amendment 2)
        h, a = c[0], c[1]
    if market in ("result_1x2", "double_chance_1x", "double_chance_x2", "double_chance_12", "draw_no_bet_home"):
        label = 0 if h > a else (1 if h == a else 2)
        recs = sme.one_x_two_records(row["game_id"], 0, row["start_at"], row["start_at"], (1 / 3, 1 / 3, 1 / 3), label)
        return next(r["outcome"] for r in recs if r["market"] == market)
    return sme.settle_count(market, row["line"], h, a)


def _score(probs: dict, outcome: str):
    """(log loss, Brier) of the full probability vector; push and void are not scored."""
    import math
    if outcome not in probs:
        return None, None
    ll = -math.log(max(min(probs[outcome], 1.0), 1e-12))
    return ll, sum((p - (1.0 if k == outcome else 0.0)) ** 2 for k, p in probs.items())


def forward_grade(data_dir: Path, *, now: Optional[str] = None, sports=FORWARD_SPORTS, adapters: Optional[dict] = None,
                  timeout_s: float = FORWARD_CALL_TIMEOUT_S) -> dict:
    """Settles ungraded forward forecasts whose start has passed, reading official results read-only: the adapter's own
    `settle(conn, row)` when it has one (None = not final yet), else `_settle`. A final result is graded exactly once. Same
    outcome vocabulary as `forward_record` (no_candidates = nothing waiting)."""
    now = now or utc_now()
    fwd = forward_connect(data_dir)
    out = {}
    try:
        for sport in sports:
            res = out[sport] = {"outcome": "no_candidates", "graded": 0, "waiting": 0, "errors": []}
            cols = ("id", "model_name", "game_id", "market", "line", "line_source", "start_at", "forecast_cutoff", "probs")
            due = [dict(zip(cols, r)) for r in fwd.execute(
                "SELECT f.id, f.model_name, f.game_id, f.market, f.line, f.line_source, f.start_at, f.forecast_cutoff, f.probs FROM forward_forecasts f "
                "LEFT JOIN forward_results r ON r.forecast_id = f.id WHERE r.forecast_id IS NULL AND f.sport=? AND f.start_at <= ? "
                "ORDER BY f.start_at, f.id", (sport, now))]
            if not due:
                _note(fwd, "grade", sport, "no_candidates", "nothing waiting", now)
                continue
            adapter, conn = _adapter(sport, adapters), _official(data_dir, sport)
            if conn is None:
                res["outcome"], res["waiting"] = "skipped", len(due)
                res["errors"].append("no store")
                _note(fwd, "grade", sport, "skipped", "no store", now)
                continue
            own = _fn(adapter, "settle") if adapter is not None else None
            try:
                for row in due:
                    row["probs"] = json.loads(row["probs"])
                    try:
                        with _deadline(timeout_s):
                            y = own(conn, row) if own else _settle(sport, conn, row)
                    except Exception as exc:
                        res["errors"].append(f"{row['game_id']}: {type(exc).__name__}: {exc}")
                        continue
                    if isinstance(y, dict):
                        y = y.get("outcome")
                    if not y:
                        res["waiting"] += 1
                        continue
                    ll, br = (None, None) if y in ("push", "void") else _score(row["probs"], y)
                    fwd.execute("INSERT OR IGNORE INTO forward_results (forecast_id, graded_at, outcome, log_loss, brier) VALUES (?,?,?,?,?)",
                                (row["id"], now, y, ll, br))
                    res["graded"] += 1
                fwd.commit()
            finally:
                conn.close()
            res["outcome"] = "failed" if res["errors"] and not res["graded"] else "evaluated"
            _note(fwd, "grade", sport, res["outcome"], f"graded={res['graded']} waiting={res['waiting']} {'; '.join(res['errors'])}", now)
    finally:
        fwd.close()
    return out


def forward_main(argv: Optional[list] = None) -> int:
    """`python -m app.learning_run --light --name forward-record ... -- python -m app.candidates.live record|grade`.
    One line per sport: `forward-<kind>: sport=<s> outcome=<o> ...`; exit 1 if any sport failed, else 0."""
    import argparse
    ap = argparse.ArgumentParser(prog="python -m app.candidates.live")
    ap.add_argument("kind", choices=("record", "grade"))
    ap.add_argument("--sport", action="append", choices=FORWARD_SPORTS)
    ap.add_argument("--call-timeout", type=float, default=FORWARD_CALL_TIMEOUT_S)
    ap.add_argument("--now", help="UTC time (tests and rehearsals only)")
    a = ap.parse_args(argv)
    fn = forward_record if a.kind == "record" else forward_grade
    res = fn(config.data_dir(), now=a.now, sports=tuple(a.sport or FORWARD_SPORTS), timeout_s=a.call_timeout)
    for sport, r in res.items():
        extra = " ".join(f"{k}={v}" for k, v in r.items() if k not in ("outcome", "errors"))
        print(f"forward-{a.kind}: sport={sport} outcome={r['outcome']} {extra} {'; '.join(r['errors'])}".rstrip())
    return 1 if any(r["outcome"] == "failed" for r in res.values()) else 0


if __name__ == "__main__":
    import sys
    sys.exit(forward_main())
