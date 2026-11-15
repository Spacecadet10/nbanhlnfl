"""Trained-artifact identity for learned research members (docs/MODEL_SELECTION.md §13). Append-only; never read by forecasts.

A member's fit in force is identified by a digest of its fitted parameters and by the results it could have used: for a
season-start member, every available result of earlier seasons; for a cadence member, its own fit record."""
import hashlib
import json
import pickle
from importlib.metadata import version
from typing import Optional

FITTED_ATTRS = ("weights", "scaler", "model", "fitted", "weight", "sd", "sigma")


def _libraries() -> str:
    return json.dumps({p: version(p) for p in ("scikit-learn", "numpy", "scipy")}, sort_keys=True)


def _digest(obj) -> str:
    from .research_live import _dumps
    return hashlib.sha256(_dumps(obj)).hexdigest()[:24]


def identity(model, rows, now: str) -> Optional[dict]:
    """None for members without learned weights (online ratings, averages)."""
    inner = getattr(model, "member", model)
    fitted = {a: getattr(inner, a) for a in FITTED_ATTRS if getattr(inner, a, None) is not None}
    if not hasattr(inner, "_refit") or not fitted:
        return None
    info = model.fit_info() if hasattr(model, "fit_info") else None
    if info is not None:
        avail = {r.game.game_id: r.result_available_at for r in rows}
        ids_through = avail.get(info.get("last_result")) or now
        return {"schedule": info["cadence"], "fit_kind": info["kind"], "trained_season": getattr(inner, "season", None),
                "training_games": info["delivered_results"], "results_fingerprint": info["results_fingerprint"],
                "results_available_through": ids_through, "params_digest": _digest(fitted)}
    season = getattr(inner, "season", None)
    if season is None:
        return None
    ids = sorted(r.game.game_id for r in rows if r.game.season < season and r.result_available_at <= now)
    through = max((r.result_available_at for r in rows if r.game.season < season and r.result_available_at <= now), default=now)
    return {"schedule": "season_start", "fit_kind": "season_start", "trained_season": season, "training_games": len(ids),
            "results_fingerprint": hashlib.sha256("\n".join(ids).encode()).hexdigest()[:16],
            "results_available_through": through, "params_digest": _digest(fitted)}


def record(conn, sport: str, member: str, definition: str, ident: Optional[dict], now: str) -> Optional[int]:
    if ident is None:
        return None
    row = conn.execute("SELECT fit_id FROM model_fits WHERE sport=? AND member=? AND definition=? AND params_digest=? "
                       "AND results_fingerprint=?", (sport, member, definition, ident["params_digest"],
                                                     ident["results_fingerprint"])).fetchone()
    if row:
        return row[0]
    return conn.execute(
        "INSERT INTO model_fits (sport, member, definition, schedule, fit_kind, trained_season, training_games, "
        "results_fingerprint, results_available_through, params_digest, libraries, first_used_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (sport, member, definition, ident["schedule"], ident["fit_kind"], ident["trained_season"], ident["training_games"],
         ident["results_fingerprint"], ident["results_available_through"], ident["params_digest"], _libraries(), now)).lastrowid
