"""L2 logistic regression (Newton/IRLS, pure Python) and the point-in-time team feature builder."""
import math
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

from .base import PregameGame, Result
from .efficiency import BoxEvent, EfficiencyRatings
from .elo import Elo, EloConfig

FEATURES = ("elo_diff", "mov10_diff", "rest_diff", "b2b_home", "b2b_away", "neutral")
SCALED = ("elo_diff", "mov10_diff", "rest_diff")
V2_FEATURES = ("elo_diff", "net_diff", "rest_diff", "b2b_home", "b2b_away", "neutral")
V2_SCALED = ("elo_diff", "net_diff", "rest_diff")
REST_CAP = 4.0


def sigmoid(z: float) -> float:
    if z >= 0:
        return 1 / (1 + math.exp(-z))
    e = math.exp(z)
    return e / (1 + e)


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting (small dense systems)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[piv] = m[piv], m[col]
        if abs(m[col][col]) < 1e-12:
            raise ValueError("Singular system in logistic fit")
        for r in range(col + 1, n):
            f = m[r][col] / m[col][col]
            for c in range(col, n + 1):
                m[r][c] -= f * m[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][c] * x[c] for c in range(r + 1, n))) / m[r][r]
    return x


def fit_logistic(x: Sequence[Sequence[float]], y: Sequence[bool], l2: float = 1.0, iters: int = 50,
                 tol: float = 1e-10) -> list[float]:
    """Returns [intercept, w1..wk]; the intercept is not penalised."""
    rows = [[1.0, *r] for r in x]
    k = len(rows[0])
    w = [0.0] * k
    for _ in range(iters):
        grad = [l2 * w[j] if j else 0.0 for j in range(k)]
        hess = [[(l2 if (i == j and i) else 0.0) for j in range(k)] for i in range(k)]
        for r, t in zip(rows, y):
            p = sigmoid(sum(wj * rj for wj, rj in zip(w, r)))
            d, s = p - float(t), p * (1 - p)
            for i in range(k):
                grad[i] += d * r[i]
                ri_s = r[i] * s
                for j in range(i, k):
                    hess[i][j] += ri_s * r[j]
        for i in range(k):
            for j in range(i):
                hess[i][j] = hess[j][i]
        step = _solve(hess, grad)
        w = [wj - sj for wj, sj in zip(w, step)]
        if max(abs(s) for s in step) < tol:
            break
    return w


@dataclass(frozen=True)
class Standardizer:
    means: dict
    stds: dict
    features: tuple = FEATURES
    scaled: tuple = SCALED

    @classmethod
    def fit(cls, examples: Sequence[dict], features: tuple = FEATURES, scaled: tuple = SCALED) -> "Standardizer":
        means, stds = {}, {}
        for f in scaled:
            vals = [e[f] for e in examples]
            mu = sum(vals) / len(vals)
            sd = math.sqrt(sum((v - mu) ** 2 for v in vals) / len(vals)) or 1.0
            means[f], stds[f] = mu, sd
        return cls(means, stds, features, scaled)

    def vector(self, feats: dict) -> list[float]:
        return [(feats[f] - self.means[f]) / self.stds[f] if f in self.scaled else feats[f] for f in self.features]


def _hours(a: str, b: str) -> float:
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return (datetime.strptime(a, fmt) - datetime.strptime(b, fmt)).total_seconds() / 3600


class TeamFeatures:
    """Point-in-time features. Ratings and margins change only via update() (available results); rest uses the
    schedule (previous tipoffs of games already predicted), which is known before tipoff."""

    def __init__(self, elo_cfg: EloConfig):
        self.elo = Elo(elo_cfg)
        self.margins: dict[str, deque] = defaultdict(lambda: deque(maxlen=10))
        self.last_tipoff: dict[tuple[int, str], str] = {}
        self.rest_seen: dict[str, dict] = {}     # game_id -> rest days: a game predicted again never rests "0 days"

    def compute(self, g: PregameGame) -> dict:
        self.elo.predict(g)  # applies any between-season rating carry before reading ratings
        rest = self.rest_seen.get(g.game_id)
        if rest is None:
            rest = self.rest_seen[g.game_id] = {}
            for side, team in (("home", g.home_team_id), ("away", g.away_team_id)):
                prev = self.last_tipoff.get((g.season, team))
                rest[side] = REST_CAP if prev is None else min(REST_CAP, _hours(g.tipoff_at, prev) / 24)
                self.last_tipoff[(g.season, team)] = g.tipoff_at
        mh, ma = self.margins[g.home_team_id], self.margins[g.away_team_id]
        return {
            "elo_diff": (self.elo.ratings[g.home_team_id] - self.elo.ratings[g.away_team_id]) / 100,
            "mov10_diff": (sum(mh) / len(mh) if mh else 0.0) - (sum(ma) / len(ma) if ma else 0.0),
            "rest_diff": rest["home"] - rest["away"],
            "b2b_home": float(rest["home"] < 1.5), "b2b_away": float(rest["away"] < 1.5),
            "neutral": float(g.neutral_site),
        }

    def update(self, r: Result) -> None:
        self.elo.update(r)
        m = r.home_score - r.away_score
        self.margins[r.game.home_team_id].append(m)
        self.margins[r.game.away_team_id].append(-m)


class LogisticElo:
    """Refit at each season start on labelled examples from earlier seasons (features captured at predict time)."""
    name = "logistic"
    feature_names = FEATURES
    scaled_names = SCALED

    def __init__(self, elo_cfg: EloConfig, l2: float = 1.0):
        self.elo_cfg, self.l2 = elo_cfg, l2
        self.features = TeamFeatures(elo_cfg)
        self.pending: dict[str, tuple[int, dict]] = {}
        self.examples: list[tuple[int, dict, bool, str]] = []  # (season, pregame features, label, game_id)
        self.season: Optional[int] = None
        self.scaler: Optional[Standardizer] = None
        self.weights: Optional[list[float]] = None
        self.first_fit_season: Optional[int] = None

    def _refit(self, season: int) -> None:
        train = [(f, y) for s, f, y, _ in self.examples if s < season]
        if not train:
            return
        self.scaler = Standardizer.fit([f for f, _ in train], self.feature_names, self.scaled_names)
        self.weights = fit_logistic([self.scaler.vector(f) for f, _ in train], [y for _, y in train], self.l2)
        if self.first_fit_season is None:
            self.first_fit_season = season

    def predict(self, g: PregameGame) -> float:
        if self.season is None or g.season > self.season:
            self.season = g.season
            self._refit(g.season)
        feats = self._features(g)
        self.pending[g.game_id] = (g.season, feats)
        if self.weights is None:  # warm-up season before the first fit
            return self.features.elo.predict(g)
        x = self.scaler.vector(feats)
        return sigmoid(self.weights[0] + sum(w * v for w, v in zip(self.weights[1:], x)))

    def _features(self, g: PregameGame) -> dict:
        return self.features.compute(g)

    def update(self, r: Result) -> None:
        self.features.update(r)
        season, feats = self.pending.pop(r.game.game_id)
        self.examples.append((season, feats, r.home_won, r.game.game_id))

    def config(self) -> dict:
        return {"l2": self.l2, "features": "+".join(self.feature_names), "elo": "k{k}-h{home_advantage}-c{carry}-mov{mov}".format(
            **self.elo_cfg.__dict__)}

    def oos_from_season(self) -> Optional[int]:
        return self.first_fit_season


class LogisticV2(LogisticElo):
    """Candidate (c), docs/EVALUATION_PLAN.md: the same refit schedule, penalty and scaling rule as `logistic`, with the
    last-10 margin replaced by the point-in-time efficiency net-rating difference. No feature search."""
    name = "logistic_v2"
    feature_names = V2_FEATURES
    scaled_names = V2_SCALED

    def __init__(self, elo_cfg: EloConfig, l2: float = 1.0):
        super().__init__(elo_cfg, l2)
        self.eff = EfficiencyRatings()

    def _features(self, g: PregameGame) -> dict:
        feats = self.features.compute(g)
        feats["net_diff"] = self.eff.features(g)["net_diff"]
        return feats

    def update_box(self, e: BoxEvent) -> None:
        self.eff.update_box(e)
