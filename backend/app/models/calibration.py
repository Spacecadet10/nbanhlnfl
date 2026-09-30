"""Platt scaling fit only on a base model's earlier out-of-sample predictions."""
import math
from typing import Optional

from .base import PregameGame, Result
from .logistic import fit_logistic, sigmoid

EPS = 1e-6


def logit(p: float) -> float:
    p = min(1 - EPS, max(EPS, p))
    return math.log(p / (1 - p))


class PlattCalibrated:
    """Wraps a base model. The base probability is stored at predict time (out-of-sample by construction) and paired
    with the label when the result arrives. At each season start the map is refit on pairs from earlier seasons,
    counting only seasons in which the base model was itself out-of-sample (for a refit model, after its first fit)."""

    def __init__(self, base, l2: float = 1e-3):
        self.base, self.l2 = base, l2
        self.name = f"{base.name}_platt"
        self.pending: dict[str, tuple[int, float]] = {}
        self.history: list[tuple[int, float, bool]] = []
        self.season: Optional[int] = None
        self.params: Optional[tuple[float, float]] = None

    def _refit(self, season: int) -> None:
        # Seasons in which the base model was out-of-sample: all of them for an online model (Elo),
        # otherwise only seasons from the base model's first fit onward (None = not fitted yet).
        start = self.base.oos_from_season() if hasattr(self.base, "oos_from_season") else -1
        if start is None:
            return
        pairs = [(p, y) for s, p, y in self.history if start <= s < season]
        if len(pairs) < 50:
            return
        b, a = fit_logistic([[logit(p)] for p, _ in pairs], [y for _, y in pairs], self.l2)
        self.params = (a, b)

    def predict(self, g: PregameGame) -> float:
        if self.season is None or g.season > self.season:
            self.season = g.season
            self._refit(g.season)
        p = self.base.predict(g)
        self.pending[g.game_id] = (g.season, p)
        if self.params is None:
            return p
        a, b = self.params
        return sigmoid(a * logit(p) + b)

    def update(self, r: Result) -> None:
        self.base.update(r)
        season, p = self.pending.pop(r.game.game_id)
        self.history.append((season, p, r.home_won))

    def config(self) -> dict:
        return {"base": f"{self.base.name}({','.join(f'{k}={v}' for k, v in sorted(self.base.config().items()))})",
                "method": "platt", "l2": self.l2}
