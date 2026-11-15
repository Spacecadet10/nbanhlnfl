"""Probability scoring helpers (pure functions)."""
import math
from typing import Optional, Sequence

from .schemas import CalibrationBin

EPS = 1e-15


def brier(probs: Sequence[float], outcomes: Sequence[bool]) -> Optional[float]:
    if not probs:
        return None
    return sum((p - float(y)) ** 2 for p, y in zip(probs, outcomes)) / len(probs)


def log_loss(probs: Sequence[float], outcomes: Sequence[bool]) -> Optional[float]:
    if not probs:
        return None
    total = 0.0
    for p, y in zip(probs, outcomes):
        p = min(1 - EPS, max(EPS, p))
        total -= math.log(p if y else 1 - p)
    return total / len(probs)


def accuracy(probs: Sequence[float], outcomes: Sequence[bool]) -> Optional[float]:
    if not probs:
        return None
    return sum((p >= 0.5) == bool(y) for p, y in zip(probs, outcomes)) / len(probs)


API_CALIBRATION_BINS = 10   # 0.2-wide bins put most games in 0.4-0.6, hiding miscalibration where most picks are made


def calibration(probs: Sequence[float], outcomes: Sequence[bool], bins: int = 5) -> list[CalibrationBin]:
    out = []
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        members = [(p, y) for p, y in zip(probs, outcomes) if lo <= p < hi or (i == bins - 1 and p == 1.0)]
        out.append(CalibrationBin(
            lower=lo, upper=hi, count=len(members),
            mean_predicted=sum(p for p, _ in members) / len(members) if members else None,
            observed_rate=sum(float(y) for _, y in members) / len(members) if members else None,
        ))
    return out


def brier_multi(vectors: Sequence[Sequence[float]], realised: Sequence[int]) -> Optional[float]:
    """Multi-outcome Brier score: mean over games of sum_k (p_k - 1[k = realised])^2 (0 best, 2 worst)."""
    if not vectors:
        return None
    return sum(sum((p - float(k == r)) ** 2 for k, p in enumerate(v)) for v, r in zip(vectors, realised)) / len(vectors)


def log_loss_multi(vectors: Sequence[Sequence[float]], realised: Sequence[int]) -> Optional[float]:
    if not vectors:
        return None
    return -sum(math.log(min(1 - EPS, max(EPS, v[r]))) for v, r in zip(vectors, realised)) / len(vectors)


def regression_errors(predicted: Sequence[float], actual: Sequence[float]) -> Optional[dict]:
    """Point-prediction errors (totals, margins, goals): MAE, RMSE, median absolute error (less moved by blowouts),
    mean error actual − predicted (bias) and R² against the actual mean."""
    if not predicted:
        return None
    err = [a - p for p, a in zip(predicted, actual)]
    n = len(err)
    mean_a = sum(actual) / n
    ss_tot = sum((a - mean_a) ** 2 for a in actual)
    sse = sum(e * e for e in err)
    absolute = sorted(abs(e) for e in err)
    mid = n // 2
    return {"n": n, "mae": sum(absolute) / n, "rmse": math.sqrt(sse / n),
            "medae": absolute[mid] if n % 2 else (absolute[mid - 1] + absolute[mid]) / 2,
            "bias": sum(err) / n, "r2": 1 - sse / ss_tot if ss_tot else None}


def confusion(probs: Sequence[float], outcomes: Sequence[bool], threshold: float = 0.5) -> Optional[dict]:
    """Picks at `threshold` (p ≥ threshold → home) against outcomes: counts, precision, recall and F1 for the home side."""
    if not probs:
        return None
    tp = sum(p >= threshold and bool(y) for p, y in zip(probs, outcomes))
    fp = sum(p >= threshold and not y for p, y in zip(probs, outcomes))
    fn = sum(p < threshold and bool(y) for p, y in zip(probs, outcomes))
    tn = len(probs) - tp - fp - fn
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    if precision is None or recall is None:
        f1 = None
    else:
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"home_picked_home_won": tp, "home_picked_away_won": fp, "away_picked_home_won": fn,
            "away_picked_away_won": tn, "precision": precision, "recall": recall, "f1": f1}
