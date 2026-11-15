"""Fit and apply registry candidates on the shared feature walk (docs/CANDIDATE_POOL.md §3).

Season-start refits: the fit for season s uses rows of earlier seasons whose results were available before the first
cutoff of season s. For `normal` learners without a per-game spread, the spread is the RMS of the candidate's own
out-of-sample residuals in earlier seasons (≥ 200 needed), exactly like ridge_points / gbt_points.
"""
import math
from typing import Optional

from .. import markets
from ..models.score_candidates import grid_from_regulation, regulation_pmf
from . import learners
from .features import FEATURE_SETS
from . import registry
from .registry import Spec

MIN_RESIDUALS = 200


def _X(rows, names):
    return [[float(r.x[k]) for k in names] for r in rows]


def fit(spec: Spec, train: list) -> learners.Fitted:
    names = FEATURE_SETS[spec.features]
    X = _X(train, names)
    ys = [r.y for r in train]
    if spec.head == "binary":
        return learners.fit_binary(spec.family, X, [y["home_won"] for y in ys])
    if spec.head == "normal":
        return learners.fit_normal(spec.family, X, [y["margin"] for y in ys], [y["total"] for y in ys],
                                   [r.season for r in train])
    reg = [y["regulation"] for y in ys]
    won = [y["home_won"] for y in ys]
    neutral = [bool(r.x.get("neutral")) for r in train]
    if spec.head == "reg3":
        return learners.fit_reg3(spec.family, X, reg, won, neutral)
    if spec.head == "goals":
        return learners.fit_goals(spec.family, X, reg, won, neutral)
    raise KeyError(spec.head)


def training_rows(rows: list, season: int, cutoff: str) -> list:
    return [r for r in rows if r.season < season and r.y is not None and r.result_available_at is not None
            and r.result_available_at <= cutoff]


def residual_sd(residuals: list, season: int) -> Optional[tuple]:
    res = [r for r in residuals if r[0] < season]
    if len(res) < MIN_RESIDUALS:
        return None
    return (max(learners.MIN_SD, math.sqrt(sum(r[1] ** 2 for r in res) / len(res))),
            max(learners.MIN_SD, math.sqrt(sum(r[2] ** 2 for r in res) / len(res))))


def outputs(spec: Spec, model: learners.Fitted, rows: list, sd: Optional[tuple]) -> list:
    """One output per row (None when a normal-head spread is not available yet)."""
    out = []
    for r, p in zip(rows, model.predict(_X(rows, FEATURE_SETS[spec.features]))):
        if spec.head == "normal":
            m, sm, t, st = (float(v) if v is not None else None for v in p)
            if sm is None:
                if sd is None:
                    out.append({"head": "normal", "m": m, "t": t, "sm": None, "st": None})
                    continue
                sm, st = sd
            out.append({"head": "normal", "m": m, "sm": sm, "t": t, "st": st})
        elif spec.head == "binary":
            out.append({"head": "binary", "p": float(p)})
        elif spec.head == "reg3":
            ot = 0.5 if r.x.get("neutral") else model.info["ot_home_share"]
            out.append({"head": "reg3", "p": [float(v) for v in p], "ot": ot})
        else:
            ot = 0.5 if r.x.get("neutral") else model.info["ot_home_share"]
            out.append({"head": "goals", "lh": float(p[0]), "la": float(p[1]), "w": model.info["draw_weight"], "ot": ot})
    return out


def ready(o: Optional[dict]) -> bool:
    return o is not None and not (o["head"] == "normal" and o["sm"] is None)


def walk(spec: Spec, rows: list) -> tuple[dict, list, list]:
    """Out-of-sample outputs {game_id: output} for every row of a season with a fit, the fit records, and the
    (season, margin error, total error) residuals of normal heads."""
    rows = sorted(rows, key=lambda r: (r.cutoff_at, r.tipoff_at, r.game_id))
    seasons = sorted({r.season for r in rows})
    out, fits, residuals = {}, [], []
    for s in seasons:
        season_rows = [r for r in rows if r.season == s]
        train = training_rows(rows, s, season_rows[0].cutoff_at)
        if len(train) < registry.MIN_TRAIN[spec.sport]:
            continue
        model = fit(spec, train)
        sd = residual_sd(residuals, s) if spec.head == "normal" else None
        fits.append({"season": s, "training_games": len(train), "results_through": max(r.result_available_at for r in train),
                     **{k: v for k, v in model.info.items() if k != "training_rows"}})
        for r, o in zip(season_rows, outputs(spec, model, season_rows, sd)):
            if spec.head == "normal" and r.y is not None:
                residuals.append((s, r.y["margin"] - o["m"], r.y["total"] - o["t"]))
            if ready(o):
                out[r.game_id] = o
    return out, fits, residuals


def distribution(o: dict):
    if o["head"] == "normal":
        return markets.MarginTotalNormal(o["m"], o["sm"], o["t"], o["st"])
    if o["head"] == "goals":
        return grid_from_regulation(regulation_pmf(o["lh"], o["la"], o["w"]), o["ot"],
                                    {"lambda_home": round(o["lh"], 6), "lambda_away": round(o["la"], 6),
                                     "ot_home_share": round(o["ot"], 6), "draw_weight": o["w"]})
    return None


def vector(o: dict, market: str, line: Optional[float]) -> Optional[tuple]:
    """(p_a, p_mid, p_b) for a market at a line, or None when the head does not support the market."""
    if o["head"] == "binary":
        return (o["p"], 0.0, 1 - o["p"]) if market == "winner" else None
    if o["head"] == "reg3":
        ph, pd, pa = o["p"]
        if market == "reg3":
            return (ph, pd, pa)
        if market == "winner":
            p = ph + pd * o["ot"]
            return (p, 0.0, 1 - p)
        return None
    return tuple(distribution(o).market(market, line))
