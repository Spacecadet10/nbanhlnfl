"""Shadow forecasts for the sealed challenger (logistic_v2), recorded next to the frozen Elo without replacing it.

The challenger's complete definition and the promotion rule are sealed in models/shadow_decision.json before its first
live forecast (docs/EVALUATION_PLAN.md "Prospective candidates"). Loading fails if the seal is broken or if the code's
constants no longer match the sealed definition, so the model cannot drift during the season. Shadow rows use
run_type 'live' with their own model_version; the app, the Elo forecast job and Elo scoring filter on the Elo
model_version and never read them.

Model state is rebuilt on every run by replaying history with the backtest's delivery rules (results and box scores
only once available), then advanced to the generation time. A game is skipped, not forecast, when a team's recent
box scores are missing (stale inputs); the skip is reported and the game can be forecast by a later run.
"""
import hashlib
import json
import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Optional

from . import backtest, live
from .db import utc_now
from .ledger import Forecast, record
from .models import efficiency
from .models.elo import Elo, EloConfig
from .models.logistic import REST_CAP, V2_FEATURES, V2_SCALED, LogisticV2

SHADOW_DECISION_PATH = Path(__file__).parent / "models" / "shadow_decision.json"
CANDIDATE = "logistic_v2"
STALE_RECENT_GAMES = 5   # a team's most recent finals (current season) that must all have a complete box score

PROMOTION_RULE = {
    "season": "2026-27",
    "decision_set": "regular-season and play-in games where frozen Elo and the candidate each have a live forecast "
                    "generated before the cutoff, and a final result; games missing either forecast are excluded and counted",
    "comparison": "paired per-game Brier difference (candidate - Elo); ISO-week block bootstrap, seed 20260930, 2000 resamples",
    "candidates_in_shadow": 1,
    "interval": "95% (Bonferroni: 97.5% with 2 candidates, 95% with 1)",
    "promote_if": "interval upper bound < 0 AND candidate log loss <= Elo log loss",
    "minimum_games": 800,
    "below_minimum": "inconclusive; Elo stays",
    "applied": "once, after the last play-in game; interim reports are operational only and never used for decisions",
    "baselines_reported": ["constant 0.5", "home-rate", "frozen Elo", "DraftKings via ESPN, latest quote retrieved no "
                           "later than the forecast's generated_at, proportional de-vig, coverage reported"],
    "market": "reported alongside; does not affect promotion",
    "changes": "none mid-season; a changed definition is a new model_version, reported separately",
}


class ShadowDecisionError(ValueError):
    pass


def current_definition(elo_cfg: EloConfig) -> dict:
    """The challenger's complete definition as implemented by the code right now."""
    model = LogisticV2(elo_cfg)
    return {
        "candidate": CANDIDATE,
        "logistic": {
            "l2": model.l2, "intercept_penalised": False, "features": list(V2_FEATURES),
            "standardised_on_training_examples_only": list(V2_SCALED),
            "refit": "at each season start on every labelled example from earlier seasons; features are the pregame "
                     "snapshots captured at predict time",
            "before_first_fit": "frozen Elo probability",
            "rest_cap_days": REST_CAP, "back_to_back_below_days": 1.5,
        },
        "elo": Elo(elo_cfg).config(),
        "efficiency": {
            "possessions": "FGA - OREB + TOV + 0.44*FTA per team, mean of both teams",
            "half_life_days": efficiency.HALF_LIFE_DAYS, "prior_games": efficiency.PRIOR_GAMES,
            "iterations": efficiency.ITERATIONS, "hca_prior": efficiency.HCA_PRIOR,
            "hca_prior_games": efficiency.HCA_PRIOR_GAMES, "default_ratings": efficiency.DEFAULT,
            "missing_data": "latest complete observation among those delivered; incomplete observations are counted "
                            "and ignored",
        },
        "delivery": "a box score is used only when both its own availability and its game's result availability are at "
                    "or before the generation time",
        "cutoff": "00:00 America/New_York on the game's Eastern date; the forecast must be generated before it",
        "feature_version": backtest.SCREEN_FEATURE_VERSION,
        "stale_inputs": {"recent_games": STALE_RECENT_GAMES,
                         "rule": "no shadow forecast is recorded for a game if either team lacks a complete delivered box "
                                 "score for any of its most recent finals of the current season (results available at "
                                 "generation time); the skip is reported and a later run may record it. Older gaps are "
                                 "tolerated and counted."},
    }


def definition_hash(definition: dict) -> str:
    return hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()


def model_version(definition: dict) -> str:
    return f"{CANDIDATE}-shadow-{definition_hash(definition)[:12]}"


def build_decision(conn: sqlite3.Connection, screening_report: dict, report_path: str) -> dict:
    """Seals the challenger from a screening report in which it was shortlisted. Reads validation rows only (via the
    frozen Elo decision check)."""
    frozen, elo_cfg = backtest.load_frozen_decision(conn)
    screen = screening_report.get("screening") or {}
    if screening_report.get("kind") != "screening" or CANDIDATE not in screen.get("shadow_shortlist", []):
        raise ShadowDecisionError(f"{CANDIDATE} is not on the shadow shortlist of this screening report")
    if screening_report.get("elo_config") != Elo(elo_cfg).config():
        raise ShadowDecisionError("The screening report was not run with the frozen Elo configuration")
    definition = current_definition(elo_cfg)
    return backtest.seal({
        "candidate": CANDIDATE, "model_version": model_version(definition), "definition": definition,
        "promotion_rule": PROMOTION_RULE, "frozen_elo_sha256": frozen["sha256"],
        "screening": {"report": report_path, "generated_at": screening_report["generated_at"],
                      "dataset_version": screening_report["dataset_version"],
                      "box_version": screening_report["box_version"], "rule": screen["rule"],
                      "elo": screen["elo"], "result": screen["comparisons"][CANDIDATE],
                      "shadow_shortlist": screen["shadow_shortlist"],
                      "note": "screening on validation seasons only; not evidence of improvement"},
        "sealed_at": utc_now(),
    })


def load_shadow_decision(conn: sqlite3.Connection, path: Optional[Path] = None) -> tuple[dict, EloConfig]:
    """Loads the sealed challenger and checks the seal, the link to the frozen Elo decision and that the code still
    implements exactly the sealed definition."""
    path = Path(path or SHADOW_DECISION_PATH)
    if not path.is_file():
        raise ShadowDecisionError(f"No sealed shadow decision at {path}")
    frozen, elo_cfg = backtest.load_frozen_decision(conn)
    try:
        decision = json.loads(path.read_text())
        if decision.get("sha256") != backtest.seal(decision)["sha256"]:
            raise ShadowDecisionError(f"Shadow decision {path} was modified after sealing (digest mismatch)")
        definition, sealed_version = decision["definition"], decision["model_version"]
        if decision["frozen_elo_sha256"] != frozen["sha256"]:
            raise ShadowDecisionError("Shadow decision was sealed against a different frozen Elo decision")
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ShadowDecisionError(f"Malformed shadow decision {path}: {exc}") from exc
    if definition != current_definition(elo_cfg):
        raise ShadowDecisionError("The code no longer matches the sealed challenger definition; a changed definition "
                                  "must be sealed as a new version")
    if sealed_version != model_version(definition) or decision.get("promotion_rule") != PROMOTION_RULE:
        raise ShadowDecisionError("Sealed model version or promotion rule does not match the code")
    return decision, elo_cfg


def _schedule(conn: sqlite3.Connection, known: set[str], horizon: str) -> list:
    """Eligible games without an available result (scheduled, in progress, or final but not yet available), up to the
    horizon, in tipoff order. Rest features need every such game's tipoff, as in the backtest."""
    types = live.ELIGIBLE_TYPES
    rows = conn.execute(
        f"SELECT * FROM games WHERE status NOT IN ('postponed', 'canceled') AND tipoff_at <= ? "
        f"AND season_type IN ({', '.join('?' * len(types))}) ORDER BY tipoff_at, game_id", (horizon, *types)).fetchall()
    return [g for g in rows if g["game_id"] not in known]


def predict(conn: sqlite3.Connection, *, now: Optional[str] = None, write: bool = False,
            horizon_days: int = live.HORIZON_DAYS, decision_path: Optional[Path] = None) -> dict:
    """Computes shadow forecasts for eligible games whose cutoff is after `now` and within the horizon. With
    write=False (the default) nothing is stored. `now` is for tests; jobs use the real clock."""
    decision, elo_cfg = load_shadow_decision(conn, decision_path)
    now = now or utc_now()
    horizon = live._ts(live._parse(now) + timedelta(days=horizon_days))
    rows = live.available_rows(conn, now)
    by_id = {r.game.game_id: r for r in rows}
    events = [e for e in backtest.load_box_events(conn, "live") if e.game.game_id in by_id]
    delivered = [e for e in events if max(e.available_at, by_id[e.game.game_id].result_available_at) <= now]
    model = LogisticV2(elo_cfg)
    backtest.run(model, rows, events, deliver_until=now)

    schedule = _schedule(conn, set(by_id), horizon)
    data_version = hashlib.sha256("|".join([
        backtest.dataset_version(rows), backtest.box_version(delivered),
        repr([(g["game_id"], g["tipoff_at"], g["home_team_id"], g["away_team_id"], g["neutral_site"])
              for g in schedule])]).encode()).hexdigest()[:16]
    recent: dict[tuple[int, str], list[str]] = {}   # (season, team) -> game ids with an available result, newest first
    for r in sorted(rows, key=lambda r: r.game.tipoff_at, reverse=True):
        for team in (r.game.home_team_id, r.game.away_team_id):
            recent.setdefault((r.game.season, team), []).append(r.game.game_id)

    mv = decision["model_version"]
    out = {"as_of": now, "horizon_end": horizon, "model_version": mv, "dataset_version": data_version,
           "results_used": len(rows), "boxes_used": len(model.eff.lines), "eligible_games": 0, "created": 0,
           "already_recorded": 0, "skipped_stale_inputs": [], "tolerated_gaps": 0, "written": write, "forecasts": []}
    for g in schedule:
        game = backtest._pregame(g)
        p = round(model.predict(game), 9)   # every scheduled game is passed through: its tipoff feeds later rest days
        cutoff = backtest.cutoff_for(g["tipoff_at"])
        if g["status"] != "scheduled" or not (now < cutoff <= horizon):
            continue
        out["eligible_games"] += 1
        stale = {}
        for team in (game.home_team_id, game.away_team_id):
            played = recent.get((game.season, team), [])
            missing = [gid for gid in played[:STALE_RECENT_GAMES] if gid not in model.eff.lines]
            out["tolerated_gaps"] += sum(gid not in model.eff.lines for gid in played[STALE_RECENT_GAMES:])
            if missing:
                stale[team] = missing
        if stale:
            out["skipped_stale_inputs"].append({"game_id": game.game_id, "missing_box_scores": stale})
            continue
        out["forecasts"].append({"game_id": game.game_id, "cutoff_at": cutoff, "home_win_prob": p})
        if write:
            _, new = record(conn, Forecast(game.game_id, "live", mv, data_version, backtest.SCREEN_FEATURE_VERSION,
                                           cutoff, now, p))
            out["created"] += new
            out["already_recorded"] += not new
    return out
