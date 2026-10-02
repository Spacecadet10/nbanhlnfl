"""Prospective recording of registry candidates and the candidate roster (docs/CANDIDATE_POOL.md §6).

`record` runs inside the two-hour cycle after the core research step. It never raises into its caller: every failure is
reported per candidate, and a missing or stale input skips only that candidate for that game. Forecasts go only to the
append-only `candidate_forecasts` table under model_version 'research-cand:<name>:<definition>', so no primary read, score,
vote, average or stacker can see them. Fitted artifacts are persisted per (candidate, definition, season) with their
training cutoff and fingerprints and refit only on the declared schedule (season start).
"""
import hashlib
import json
import pickle
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .. import backtest, config, live
from ..db import transaction, utc_now
from . import engine, features, registry

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
    return hashlib.sha256("\n".join(sorted(r.game_id for r in rows)).encode()).hexdigest()[:16]


def ensure_fit(spec, rows: list, season: int) -> Optional[dict]:
    """The season-start fit for `season` (earlier seasons only), loaded from disk when its training set is unchanged."""
    train = [r for r in rows if r.season < season and r.y is not None]
    if len(train) < registry.MIN_TRAIN[spec.sport]:
        return None
    defn = registry.definition(spec)
    fp = _fingerprint(train)
    path = artifact_dir(spec.sport) / spec.name.replace(":", "__") / f"{defn}-{season}-{fp}.pkl"
    if path.exists():
        with path.open("rb") as fh:
            return pickle.load(fh)
    model = engine.fit(spec, train)
    sd = None
    if spec.head == "normal":
        _, _, residuals = engine.walk(spec, train)
        sd = engine.residual_sd(residuals, season)
    blob = pickle.dumps(model.objects)
    art = {"model": model, "sd": sd, "season": season, "definition": defn, "training_games": len(train),
           "results_fingerprint": fp, "results_available_through": max(r.result_available_at for r in train),
           "artifact_sha256": hashlib.sha256(blob).hexdigest(), "info": model.info, "fitted_at": utc_now()}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("wb") as fh:
        pickle.dump(art, fh)
    tmp.replace(path)
    return art


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


def core_lines(conn, game_ids, now: str) -> dict:
    """(game_id, market) -> the exact line of the latest core research cohort generated at or before `now` (never the
    expanded experiment's), with its cohort id as the reference. Used when the caller passes no lines."""
    out = {}
    for gid in game_ids:
        for r in conn.execute("SELECT cohort_id, market, line, line_source FROM research_cohorts WHERE game_id=? AND line IS NOT NULL "
                              "AND generated_at<=? AND definition NOT LIKE '%:expanded-v1' ORDER BY generated_at, cohort_id",
                              (gid, now)):
            out[(gid, r["market"])] = {"line": r["line"], "source": f"core_cohort:{r['line_source']}", "ref": f"research_cohorts:{r['cohort_id']}"}
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
            if not registry.available(spec):
                out["skipped"][spec.name] = {"all": f"optional dependency {spec.extra} not installed"}
                continue
            _record_spec(conn, eng, spec, history, upcoming, recent, rec, lines, now, fixed, write, dataset, table, out)
        except Exception as exc:
            out["errors"][spec.name] = f"{type(exc).__name__}: {exc}"


def _record_spec(conn, eng, spec, history, upcoming, recent, rec, lines, now, fixed, write, dataset, table, out):
    mv = model_version(spec)
    by_season: dict = {}
    for gid, (game, row) in upcoming.items():
        by_season.setdefault(game.season, []).append(gid)
    for season, gids in by_season.items():
        art = ensure_fit(spec, history, season)
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
            reason = _stale(eng, rec, game, recent)
            if reason is None and not engine.ready(o):
                reason = "not ready: no out-of-sample residual spread yet"
            if reason:
                out["skipped"].setdefault(spec.name, {})[game.game_id] = reason
                continue
            for market in spec.markets:
                if market in ("winner", "reg3"):
                    line, source, ref = None, "none", None
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
                key = _key(mv, game.game_id, market, line, ref, dataset, art["artifact_sha256"], params)
                generated = now if fixed else utc_now()
                if generated >= game.tipoff_at:
                    out["skipped"].setdefault(spec.name, {})[f"{game.game_id}:{market}"] = "calculation finished at or after tipoff"
                    continue
                item = {"game_id": game.game_id, "candidate": spec.name, "market": market, "line": line,
                        "p": [pa, pm, pb], "model_version": mv, "generated_at": generated}
                if write:
                    with transaction(conn):
                        cur = conn.execute(
                            "INSERT OR IGNORE INTO candidate_forecasts (game_id, candidate, model_version, market, line, "
                            "line_source, line_ref, p_a, p_mid, p_b, params, inputs, fit_id, artifact_sha256, training_cutoff, "
                            "dataset_version, tipoff_at, generated_at, idempotency_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (game.game_id, spec.name, mv, market, line, source, ref, pa, pm, pb, json.dumps(params, sort_keys=True),
                             json.dumps(inputs, sort_keys=True, default=str), fit_id, art["artifact_sha256"],
                             art["results_available_through"], dataset, game.tipoff_at, generated, key))
                    new = cur.rowcount == 1
                    out["created" if new else "already_recorded"] += 1
                out["forecasts"].append(item)


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
    combination participation and selection eligibility; plus the family audit."""
    from .. import selector
    as_of = as_of or utc_now()
    ev = _evidence(sport)
    items = []
    for spec in registry.for_sport(sport):
        fit = conn.execute("SELECT * FROM model_fits WHERE sport=? AND member=? ORDER BY first_used_at DESC, fit_id DESC LIMIT 1",
                           (sport, f"cand:{spec.name}")).fetchone() if _has(conn, "model_fits") else None
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
        status = ("dependency missing" if not registry.available(spec) else
                  "trained" if fit else "registered (trains at first eligible game)")
        confirmed = bool(combos)
        eligible = confirmed and settled >= selector.MIN_PICKS
        items.append({"name": spec.name, "family": spec.family, "head": spec.head, "features": spec.features,
                      "role": spec.role, "why": spec.why, "markets": list(spec.markets), "refit": spec.refit,
                      "definition": registry.definition(spec), "training_status": status,
                      "latest_fit": dict(fit) if fit else None, "evaluation": evaluation,
                      "prospective": {"games": n, "settled": settled},
                      "combination_participation": combos,
                      "selection": ("eligible" if eligible else
                                    f"selectable after {selector.MIN_PICKS} settled prospective picks (held-out gain in "
                                    f"{', '.join(combos)})" if confirmed else
                                    "reference only (never selected)" if spec.role == "reference" else
                                    "shortlisted, but no held-out gain: recorded for complementarity only" if shortlisted else
                                    "recorded for complementarity only (not shortlisted)")})
    return {"sport": sport, "pool": registry.POOL, "as_of": as_of, "candidates": items,
            "family_audit": list(registry.FAMILY_AUDIT),
            "note": "Research only. Evaluations are exploratory (spent seasons); only prospective forecasts can show "
                    "improvement. Candidates never vote and never replace the primary."}


def _has(conn, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None
