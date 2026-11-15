"""Per-game view of every candidate's recorded forecasts (candidate_forecasts, migration 0019), including the neural ones.

Research only: these rows are never a canonical forecast, never vote and are not graded here. Each candidate model version is
shown on its own (the latest pregame row of that version per market), never pooled with another version. Only rows made
strictly before the start are shown: before the start stored with the row and before the game's current start. A row whose
start has moved since is flagged. A registry candidate with no row for this game is listed as such; nothing is inferred.
"""
import json
from typing import Optional

from . import markets as market_specs
from .candidates import deep, registry

NEURAL_FAMILIES = ("mlp",)   # scikit-learn multilayer perceptron; torch/keras networks are app.candidates.deep


def neural(spec) -> bool:
    return bool(spec) and (deep.is_deep(spec) or spec.family in NEURAL_FAMILIES)


NOTE = ("Research only: individual candidate models, including neural networks. None of these forecasts is canonical, "
        "votes, or is used for a pick; each model version is shown separately.")


def game(conn, sport: str, game_id: str, as_of: str) -> Optional[dict]:
    g = conn.execute("SELECT * FROM games WHERE game_id=?", (game_id,)).fetchone()
    if g is None:
        return None
    abbr = {t["team_id"]: t["abbr"] for t in conn.execute("SELECT team_id, abbr FROM teams WHERE team_id IN (?, ?)",
                                                            (g["home_team_id"], g["away_team_id"]))}
    rows = conn.execute(
        "SELECT * FROM candidate_forecasts WHERE game_id=? AND generated_at<=? AND generated_at<tipoff_at AND generated_at<? "
        "ORDER BY generated_at DESC, forecast_id DESC", (game_id, as_of, g["tipoff_at"])).fetchall()
    seen, forecasts = set(), []
    for r in rows:
        k = (r["candidate"], r["market"], r["model_version"])
        if k in seen:
            continue
        seen.add(k)
        spec = registry.BY_NAME.get(r["candidate"])
        a, mid, b = market_specs.side_labels(r["market"], r["line"], abbr.get(g["home_team_id"], "Home"),
                                             abbr.get(g["away_team_id"], "Away"))
        forecasts.append({
            "candidate": r["candidate"], "family": spec.family if spec else None, "neural": neural(spec),
            "market": r["market"], "model_version": r["model_version"], "line": r["line"], "line_source": r["line_source"],
            "line_ref": r["line_ref"],
            "sides": [{"label": a, "p": r["p_a"]}] + ([{"label": mid, "p": r["p_mid"]}] if mid else []) + [{"label": b, "p": r["p_b"]}],
            "fit_id": r["fit_id"], "artifact_sha256": r["artifact_sha256"], "training_cutoff": r["training_cutoff"],
            "dataset_version": r["dataset_version"], "generated_at": r["generated_at"], "start_at_forecast": r["tipoff_at"],
            "start_moved": r["tipoff_at"] != g["tipoff_at"], "inputs": json.loads(r["inputs"]),
            "research": True, "canonical": False})
    forecasts.sort(key=lambda f: (f["market"], f["candidate"], f["model_version"]))
    have = {f["candidate"] for f in forecasts}
    return {"game_id": game_id, "sport": sport, "start": g["tipoff_at"], "status": g["status"], "as_of": as_of, "note": NOTE,
            "forecasts": forecasts,
            "without_forecast": [{"candidate": s.name, "family": s.family, "neural": neural(s),
                                  "reason": "no pregame forecast recorded for this game"}
                                 for s in registry.for_sport(sport) if s.name not in have]}
