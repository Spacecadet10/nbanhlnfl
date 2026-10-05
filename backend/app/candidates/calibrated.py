"""Frozen calibration overlays of core forecasts, stored in the existing candidate ledger.

Research only: no voter, combination or selector reads this registry. See MODEL_SELECTION calibrated-shadow-v1.
"""
import hashlib
import json
from datetime import timedelta
from pathlib import Path

from .. import calibration_study as study, combine, config, fit_register, historical_start, live, research_live
from ..db import transaction, utc_now
from . import live as candidates

VERSION = "calibrated-shadow-v1"
PAIRS = {
    "nba": (("ridge_points", "total"), ("ridge_points", "team_total_home"),
            ("gbt_points", "total"), ("gbt_points", "team_total_home"), ("gbt_points", "team_total_away"),
            ("efficiency_margin", "winner")),
    "nhl": (("goals_poisson", "reg3"), ("goals_glm", "reg3"), ("goals_glm", "total")),
}


def name(sport, member, market):
    return f"calibrated:{sport}:{member}:{market}"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def definition(raw_definition):
    h = hashlib.sha256((VERSION + raw_definition).encode())
    # Include calibration and underlying numerical routines without changing the raw members' identities.
    for p in (Path(__file__), Path(study.__file__), Path(study.fit_logistic.__code__.co_filename),
              Path(study.logit.__code__.co_filename)):
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def artifact_path(sport, defn, season):
    return config.data_dir() / "research" / "calibrated" / f"{sport}-{defn}-{season}.json"


def fit_artifact(tables, first, sport, season, cutoff, raw_definition, dataset, created_at):
    fits = {}
    for member, market in PAIRS[sport]:
        rows = [r for r in tables.get(market, []) if (first.get(member) or 0) <= r["season"] < season
                and r["realised"] in combine.sides(market) and member in r["vectors"]
                and r["result_available_at"] <= cutoff]
        if len(rows) < study.MIN_FIT:
            raise ValueError(f"{member}/{market}: only {len(rows)} eligible earlier OOF games; need {study.MIN_FIT}")
        rows.sort(key=lambda r: (r["cutoff_at"], r["game_id"]))
        params = study._fit("platt", market, [(r["vectors"][member], r["realised"]) for r in rows])
        fits[name(sport, member, market)] = {
            "params": params, "training_games": len(rows),
            "results_available_through": max(r["result_available_at"] for r in rows),
            "training_seasons": sorted({r["season"] for r in rows}),
            "results_fingerprint": digest([(r["game_id"], r["line"], r["vectors"][member], r["realised"],
                                             r["result_available_at"]) for r in rows]),
        }
    return {"version": VERSION, "sport": sport, "season": season, "raw_definition": raw_definition,
            "definition": definition(raw_definition), "training_cutoff": cutoff, "created_at": created_at,
            "dataset_version": dataset, "fits": fits}


def load_artifact(sport, defn, season):
    p = artifact_path(sport, defn, season)
    if not p.exists():
        return None
    doc = json.loads(p.read_text())
    if not isinstance(doc, dict) or not isinstance(doc.get("artifact"), dict):
        raise ValueError("invalid calibration artifact structure")
    art = doc["artifact"]
    if not isinstance(art.get("fits"), dict):
        raise ValueError("invalid calibration artifact fits")
    if digest(art) != doc["sha256"]:
        raise ValueError("calibration artifact digest mismatch")
    if (art["version"], art["sport"], art["definition"], art["season"]) != (VERSION, sport, defn, season):
        raise ValueError("calibration artifact identity mismatch")
    return art


def prepare(conn, sport, season, cutoff):
    """Read-only store; freeze once, never overwrite or refit an existing season's artifact."""
    created = utc_now()
    candidates._parse(cutoff)  # strict timestamp validation
    if cutoff > created:
        raise ValueError("training cutoff cannot be in the future")
    raw = research_live.definition(conn)
    defn = definition(raw)
    p = artifact_path(sport, defn, season)
    p.parent.mkdir(parents=True, exist_ok=True)
    with candidates._locked(p):
        existing = load_artifact(sport, defn, season)
        if existing is not None:
            return {"path": str(p), "reused": True, "artifact": existing}
        tables, first, dataset, _, _ = historical_start._history(conn, sport)
        art = fit_artifact(tables, first, sport, season, cutoff, raw, dataset, utc_now())
        art["created_at"] = utc_now()  # completion time: a replay cannot use an unfinished fit
        # Exclusive create also protects against accidental overwrite by a second writer.
        with p.open("x") as f:
            json.dump({"artifact": art, "sha256": digest(art)}, f, sort_keys=True, allow_nan=False)
    return {"path": str(p), "reused": False, "artifact": art}


def apply(params, vector, market):
    if any(not 0 <= x <= 1 for x in vector) or abs(sum(vector) - 1) > 1e-6:
        raise ValueError("invalid raw probability vector")
    if market not in combine.THREE_WAY and vector[0] + vector[2] <= 0:
        return tuple(vector)
    v = study._apply("platt", params, vector, market)
    if market in combine.THREE_WAY:
        return v
    # Fit is conditional on a decided outcome; it does not estimate push probability.
    return (v[0] * (1 - vector[1]), vector[1], v[2] * (1 - vector[1]))


def fit_unavailable(art, member, raw_definition, season, as_of):
    if art["raw_definition"] != raw_definition or max(art["created_at"], art["training_cutoff"]) > as_of:
        return "calibration fit unavailable at this forecast time or wrong raw definition"
    f = art["fits"].get(member)
    if f is None:
        return "no frozen calibration fit for this member"
    if f["results_available_through"] > min(as_of, art["training_cutoff"]) or any(s >= season for s in f["training_seasons"]):
        return "calibration training is not earlier available history"
    return None


def record(conn, *, game_ids=None, now=None, write=True, **_):
    fixed = now is not None
    now = now or utc_now()
    sport = live.engine(conn).sport
    raw_def = research_live.definition(conn)
    defn = definition(raw_def)
    out = {"status": "success", "created": 0, "already_recorded": 0, "skipped": {}, "errors": {}}
    horizon = (candidates._parse(now) + timedelta(hours=2)).strftime(candidates.FMT)
    cohorts = conn.execute(
        "SELECT c.*, g.season FROM research_cohorts c JOIN games g USING(game_id) WHERE c.definition=? "
        "AND c.generated_at<=? AND c.tipoff_at=g.tipoff_at AND g.status='scheduled' "
        "AND g.tipoff_at>? AND g.tipoff_at<=? ORDER BY c.generated_at DESC,c.cohort_id DESC",
        (raw_def, now, now, horizon)).fetchall()
    seen = set()
    for c in cohorts:
        key = (c["game_id"], c["market"])
        if key in seen or (game_ids is not None and c["game_id"] not in game_ids):
            continue
        seen.add(key)
        for member, market in PAIRS[sport]:
            if market != c["market"]:
                continue
            nm = name(sport, member, market)
            label = f"{nm}:{c['game_id']}"
            try:
                art = load_artifact(sport, defn, c["season"])
                if art is None:
                    out["skipped"][label] = "no frozen calibration fit; run calibration-shadow-fit"
                    continue
                reason = fit_unavailable(art, nm, raw_def, c["season"], now)
                if reason:
                    out["skipped"][label] = reason
                    continue
                raw = next((m for m in json.loads(c["members"]) if m["name"] == member), None)
                if raw is None:
                    out["skipped"][label] = "raw member unavailable in the current cohort"
                    continue
                f = art["fits"][nm]
                v = apply(f["params"], raw["p"], market)
                generated = now if fixed else utc_now()
                if generated >= c["tipoff_at"]:
                    out["skipped"][label] = "calculation finished at or after tipoff"
                    continue
                sha = digest(art)
                mv = f"research-cand:{nm}:{defn}"
                idem = digest([mv, c["cohort_id"], sha, raw])
                inputs = {"raw_member": member, "raw_probability": raw["p"], "raw_ref": raw.get("ref"),
                          "raw_definition": raw_def, "cohort_id": c["cohort_id"], "line_source": c["line_source"],
                          "calibration_domain": "model lines; transfer to bookmaker lines unverified",
                          "calibration_created_at": art["created_at"], "training_seasons": f["training_seasons"]}
                if write:
                    with transaction(conn):
                        ident = {**f, "params_digest": digest([f["params"], c["season"]]), "schedule": "season_start",
                                 "fit_kind": VERSION, "trained_season": c["season"]}
                        fit_id = fit_register.record(conn, sport, f"cand:{nm}", defn, ident, generated)
                        new = candidates.insert_forecast(conn, (
                            c["game_id"], nm, mv, market, c["line"], c["line_source"] or "none",
                            f"research_cohorts:{c['cohort_id']}", *v, json.dumps({"platt": f["params"]}),
                            json.dumps(inputs, sort_keys=True), fit_id, sha, f["results_available_through"],
                            art["dataset_version"], c["tipoff_at"], generated, idem))
                    out["created" if new else "already_recorded"] += 1
            except Exception as exc:
                out["errors"][label] = f"{type(exc).__name__}: {exc}"
    for g in conn.execute("SELECT game_id FROM games WHERE status='scheduled' AND tipoff_at>? AND tipoff_at<=?", (now, horizon)):
        if game_ids is not None and g["game_id"] not in game_ids:
            continue
        for member, market in PAIRS[sport]:
            if (g["game_id"], market) not in seen:
                out["skipped"][f"{name(sport, member, market)}:{g['game_id']}"] = "no current core cohort"
    if out["errors"] or out["skipped"]:
        out["status"] = "partial"
    return out


def roster(conn, sport, as_of):
    """Same contract as the existing candidate roster, explicitly ineligible for selection."""
    _, raw = candidates._current_definitions(conn)
    defn = definition(raw) if raw else "unavailable"
    season = conn.execute("SELECT season FROM games WHERE status='scheduled' AND tipoff_at>? ORDER BY tipoff_at LIMIT 1",
                          (as_of,)).fetchone()
    art, artifact_error = None, None
    if raw and season:
        try:
            art = load_artifact(sport, defn, season[0])
        except (ValueError, KeyError, OSError) as exc:
            artifact_error = str(exc)
    items = []
    for member, market in PAIRS[sport]:
        nm = name(sport, member, market)
        ready, error = False, artifact_error
        if art is not None:
            try:
                error = fit_unavailable(art, nm, raw, season[0], as_of)
                ready = error is None
            except (ValueError, KeyError, TypeError) as exc:
                error = str(exc)
        mv = f"research-cand:{nm}:{defn}"
        fit = conn.execute("SELECT * FROM model_fits WHERE sport=? AND member=? AND definition=? AND first_used_at<=? "
                           "ORDER BY first_used_at DESC,fit_id DESC LIMIT 1", (sport, f"cand:{nm}", defn, as_of)).fetchone()
        earlier_fit = conn.execute("SELECT 1 FROM model_fits WHERE sport=? AND member=? AND definition<>? AND first_used_at<=? LIMIT 1",
                                   (sport, f"cand:{nm}", defn, as_of)).fetchone() is not None
        earlier_at = conn.execute("SELECT MAX(generated_at) FROM candidate_forecasts WHERE candidate=? AND model_version<>? "
                                  "AND generated_at<=?", (nm, mv, as_of)).fetchone()[0]
        row = conn.execute("SELECT COUNT(DISTINCT cf.game_id) AS n, MAX(cf.generated_at) AS latest, "
                           "COUNT(DISTINCT CASE WHEN g.status='final' AND g.result_available_at<=? THEN cf.game_id END) AS settled "
                           "FROM candidate_forecasts cf JOIN games g USING(game_id) WHERE cf.model_version=? AND cf.generated_at<=?",
                           (as_of, mv, as_of)).fetchone()
        items.append({"name": nm, "family": "calibration", "head": "reg3" if market == "reg3" else "binary",
                      "features": "raw cohort", "role": "shadow", "why": "Exploratory calibration-v1 gain; no live gain established",
                      "markets": [market], "refit": "season", "definition": defn,
                      "training_status": ("trained; shadow only" if fit else f"calibration artifact unavailable: {error}" if error
                                          else "frozen fit ready; awaiting first forecast" if ready else "awaiting calibration-shadow-fit"),
                      "latest_fit": dict(fit) if fit else None, "evaluation": {}, "combination_participation": [], "pool": {},
                      "prospective": {"games": row["n"], "settled": row["settled"]}, "selection": "shadow only; never votes or selected",
                      "coverage": {"fitted": fit is not None, "earlier_fit": earlier_fit, "latest_forecast_at": row["latest"],
                                   "earlier_forecast_at": earlier_at, "forecasting": candidates._recent(row["latest"], as_of),
                                   "in_agreement_tracking": [], "votes": False, "selectable": {"alone": [], "inside": []}}})
    return items
