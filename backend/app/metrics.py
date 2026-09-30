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
