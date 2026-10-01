"""Research round 3: learned stacking over walk-forward base predictions (docs/EVALUATION_PLAN.md).

Pure functions over tables of base-model predictions that were each made before their game. A stacker for season S
is fitted only on rows from earlier seasons, then applied to season S. Development research only; never imported by
the API or the jobs.
"""
import math
from typing import Callable, Optional, Sequence

from .calibration import logit
from .logistic import fit_logistic, sigmoid

BASE_MODELS = ("elo", "efficiency_margin", "logistic", "logistic_v2", "gbt_v1")
CLIP = (0.02, 0.98)
L2 = 10.0
INTERACTIONS = ("m_x_disagreement", "m_x_abs_rest_diff", "m_x_abs_net_diff")


def clipped_logits(probs: Sequence[float]) -> list[float]:
    return [logit(min(CLIP[1], max(CLIP[0], p))) for p in probs]


def features_logit(row: dict) -> list[float]:
    """Stacker A: the five base probabilities as clipped logits."""
    return clipped_logits([row[m] for m in BASE_MODELS])


def features_conditional(row: dict) -> list[float]:
    """Stacker B: A's inputs plus mean logit × disagreement, × |rest_diff| and × |net_diff|."""
    logits = features_logit(row)
    m = sum(logits) / len(logits)
    probs = [row[name] for name in BASE_MODELS]
    return [*logits, m * (max(probs) - min(probs)), m * abs(row["rest_diff"]), m * abs(row["net_diff"])]


def _standardiser(xs: list[list[float]]) -> tuple[list[float], list[float]]:
    k, n = len(xs[0]), len(xs)
    means = [sum(x[j] for x in xs) / n for j in range(k)]
    stds = [math.sqrt(sum((x[j] - means[j]) ** 2 for x in xs) / n) or 1.0 for j in range(k)]
    return means, stds


def fit(rows: list[dict], features: Callable[[dict], list[float]], l2: float = L2) -> dict:
    """Fits one stacker on `rows` (each has the base probabilities, any context and `home_won`)."""
    xs = [features(r) for r in rows]
    means, stds = _standardiser(xs)
    z = [[(v - mu) / sd for v, mu, sd in zip(x, means, stds)] for x in xs]
    return {"weights": fit_logistic(z, [r["home_won"] for r in rows], l2), "means": means, "stds": stds, "n": len(rows)}


def predict(model: dict, row: dict, features: Callable[[dict], list[float]]) -> float:
    z = [(v - mu) / sd for v, mu, sd in zip(features(row), model["means"], model["stds"])]
    return sigmoid(model["weights"][0] + sum(w * v for w, v in zip(model["weights"][1:], z)))


def walk_forward(rows: list[dict], features: Callable[[dict], list[float]], seasons: Sequence[int],
                 first_training_season: int, l2: float = L2) -> tuple[list[dict], list[dict]]:
    """For each season in `seasons`: fit on rows of first_training_season … season−1, predict that season.
    Returns (predictions, one fit record per season). Raises if a training result was not available before the
    season's first cutoff."""
    out, fits = [], []
    for season in seasons:
        train = [r for r in rows if first_training_season <= r["season"] < season]
        target = [r for r in rows if r["season"] == season]
        if not train or not target:
            raise ValueError(f"no training or target rows for season {season}")
        first_cutoff = min(r["cutoff_at"] for r in target)
        late = [r["game_id"] for r in train if r["result_available_at"] > first_cutoff]
        if late:
            raise ValueError(f"{len(late)} training results were not available before season {season} began")
        model = fit(train, features, l2)
        fits.append({"season": season, "training_games": model["n"],
                     "training_seasons": sorted({r["season"] for r in train}), "weights": model["weights"]})
        for r in target:
            out.append({**{k: r[k] for k in ("game_id", "season", "season_type", "tipoff_at", "cutoff_at", "home_team_id",
                                             "away_team_id", "neutral_site", "home_won")},
                        "p_home": predict(model, r, features)})
    return out, fits


def most_confident(preds: list[dict], coverage: float) -> list[dict]:
    """The `coverage` share of games with the largest |p − 0.5| (ties broken by game id). Uses no outcome."""
    ranked = sorted(preds, key=lambda p: (-abs(p["p_home"] - 0.5), p["game_id"]))
    return ranked[:round(coverage * len(preds))]


def wilson(wins: int, n: int, z: float = 1.959964) -> list[Optional[float]]:
    if not n:
        return [None, None]
    p, d = wins / n, 1 + z * z / n
    c, h = (p + z * z / (2 * n)) / d, z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [c - h, c + h]


def accuracy_on(preds: list[dict]) -> dict:
    wins = sum((p["p_home"] >= 0.5) == p["home_won"] for p in preds)
    return {"games": len(preds), "wins": wins, "accuracy": wins / len(preds) if preds else None, "wilson95": wilson(wins, len(preds))}
