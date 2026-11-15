"""Score-distribution research candidates for line and 3-way markets (docs/MODEL_SELECTION.md §2).

Each candidate gives a full final-score distribution (markets.MarginTotalNormal for the NBA, markets.ScoreGrid for the NHL), so
all its markets are coherent. Same walk-forward discipline as the winner research models: features are captured when the game
is forecast, the label joins the training set only when the result is delivered, and the model is refit at each season start on
earlier seasons only. A candidate that is not fitted yet returns `None` from `distribution` and reports `not_ready`; it is
never replaced by another model's numbers.
"""
import math
from collections import defaultdict, deque
from typing import Optional

from .. import markets
from .base import PregameGame, Result
from .efficiency import BoxEvent, EfficiencyRatings, TeamRating, DEFAULT
from .elo import EloConfig
from .logistic import REST_CAP, TeamFeatures, _hours

MIN_EXAMPLES = 200          # training games needed before a fit
MIN_RESIDUALS = 200         # own out-of-sample residuals needed before an NBA spread is trusted
MIN_SD = 1.0                # points; guards a degenerate spread (real residual SDs are about 12 and 18)
RIDGE_L2 = 10.0
GBT_SETTINGS = {"loss": "squared_error", "learning_rate": 0.05, "max_iter": 200, "max_depth": 3, "max_leaf_nodes": 8,
                "min_samples_leaf": 50, "l2_regularization": 1.0, "max_bins": 255, "early_stopping": False,
                "random_state": 20260930}
NBA_FEATURES = ("elo_diff", "mov10_diff", "rest_diff", "b2b_home", "b2b_away", "neutral",
                "ortg_home", "drtg_home", "ortg_away", "drtg_away", "pace", "margin_est")
DRAW_GRID = tuple(round(0.005 * i, 3) for i in range(61))     # 0 … 0.30
GLM_ALPHA = 0.01   # sklearn penalises the mean deviance: 1.0 shrank goal-rate effects by ~25% (validation log loss worse in every market)
MIN_HOCKEY_EXAMPLES = 500
NHL_FEATURES = ("elo_diff", "gf10_home", "ga10_home", "gf10_away", "ga10_away", "rest_diff", "b2b_home", "b2b_away")
MAX_GOALS = 15


def _standardise(xs):
    import numpy as np
    x = np.asarray(xs, dtype=float)
    mu, sd = x.mean(axis=0), x.std(axis=0)
    sd[sd == 0] = 1.0
    return mu, sd


class _NbaRefit:
    """Shared NBA machinery: point-in-time features, season-start refits, own out-of-sample residual spreads."""
    name = "nba_refit"

    def __init__(self, elo_cfg: EloConfig):
        self.elo_cfg = elo_cfg
        self.team = TeamFeatures(elo_cfg)
        self.eff = EfficiencyRatings()
        self.pending: dict[str, tuple] = {}          # game_id -> (season, x, predicted (margin, total) or None)
        self.examples: list[tuple] = []              # (season, x, margin, total)
        self.residuals: list[tuple] = []             # (season, margin error, total error) of fitted predictions only
        self.season: Optional[int] = None
        self.fitted = None
        self.sd: Optional[tuple[float, float]] = None
        self.first_ready_season: Optional[int] = None
        self.fits: list[dict] = []

    def _vector(self, g: PregameGame) -> list[float]:
        t = self.team.compute(g)
        ratings, hca = self.eff.ratings(g.season, g.tipoff_at[:10] + "T00:00:00Z")
        fb = TeamRating(DEFAULT["ortg"], DEFAULT["drtg"], DEFAULT["pace"], 0)
        h, a = ratings.get(g.home_team_id, fb), ratings.get(g.away_team_id, fb)
        pace = (h.pace + a.pace) / 2
        edge = 0.0 if g.neutral_site else 2 * hca
        f = {**t, "ortg_home": h.ortg, "drtg_home": h.drtg, "ortg_away": a.ortg, "drtg_away": a.drtg, "pace": pace,
             "margin_est": (h.net - a.net + edge) * pace / 100}
        return [float(f[k]) for k in NBA_FEATURES]

    def _fit(self, train) -> object:
        raise NotImplementedError

    def _means(self, fitted, x) -> tuple[float, float]:
        raise NotImplementedError

    def _refit(self, season: int) -> None:
        train = [e for e in self.examples if e[0] < season]
        if len(train) >= MIN_EXAMPLES:
            self.fitted = self._fit(train)
            self.fits.append({"season": season, "training_games": len(train)})
        res = [r for r in self.residuals if r[0] < season]
        if len(res) >= MIN_RESIDUALS:
            self.sd = (max(MIN_SD, math.sqrt(sum(r[1] ** 2 for r in res) / len(res))),
                       max(MIN_SD, math.sqrt(sum(r[2] ** 2 for r in res) / len(res))))
        if self.ready and self.first_ready_season is None:
            self.first_ready_season = season

    @property
    def ready(self) -> bool:
        return self.fitted is not None and self.sd is not None

    def _prepare(self, g: PregameGame):
        if self.season is None or g.season > self.season:
            self.season = g.season
            self._refit(g.season)
        if g.game_id not in self.pending:
            x = self._vector(g)
            pred = self._means(self.fitted, x) if self.fitted is not None else None
            self.pending[g.game_id] = (g.season, x, pred)
        return self.pending[g.game_id]

    def distribution(self, g: PregameGame) -> Optional[markets.MarginTotalNormal]:
        _, _, pred = self._prepare(g)
        if pred is None or self.sd is None:
            return None
        return markets.MarginTotalNormal(pred[0], self.sd[0], pred[1], self.sd[1])

    def predict(self, g: PregameGame) -> float:
        d = self.distribution(g)
        return 0.5 if d is None else d.winner()[0]

    def update(self, r: Result) -> None:
        self.team.update(r)
        season, x, pred = self.pending.pop(r.game.game_id)
        margin, total = r.home_score - r.away_score, r.home_score + r.away_score
        self.examples.append((season, x, margin, total))
        if pred is not None:
            self.residuals.append((season, margin - pred[0], total - pred[1]))

    def update_box(self, e: BoxEvent) -> None:
        self.eff.update_box(e)

    def oos_from_season(self) -> Optional[int]:
        return self.first_ready_season


class RidgePoints(_NbaRefit):
    name = "ridge_points"

    def _fit(self, train):
        import numpy as np
        mu, sd = _standardise([e[1] for e in train])
        z = (np.asarray([e[1] for e in train]) - mu) / sd
        z1 = np.hstack([np.ones((len(z), 1)), z])
        pen = RIDGE_L2 * np.eye(z1.shape[1])
        pen[0, 0] = 0.0
        coef = [np.linalg.solve(z1.T @ z1 + pen, z1.T @ np.asarray([e[k] for e in train], dtype=float)) for k in (2, 3)]
        return mu, sd, coef

    def _means(self, fitted, x):
        import numpy as np
        mu, sd, coef = fitted
        z = np.concatenate([[1.0], (np.asarray(x) - mu) / sd])
        return float(z @ coef[0]), float(z @ coef[1])

    def config(self) -> dict:
        return {"features": "+".join(NBA_FEATURES), "l2": RIDGE_L2, "min_examples": MIN_EXAMPLES,
                "min_residuals": MIN_RESIDUALS, "elo": "k{k}-h{home_advantage}-c{carry}-mov{mov}".format(**self.elo_cfg.__dict__)}


class GbtPoints(_NbaRefit):
    name = "gbt_points"

    def _fit(self, train):
        from sklearn.ensemble import HistGradientBoostingRegressor
        xs = [e[1] for e in train]
        out = []
        for k in (2, 3):
            m = HistGradientBoostingRegressor(**GBT_SETTINGS)
            m.fit(xs, [e[k] for e in train])
            out.append(m)
        return out

    def _means(self, fitted, x):
        return float(fitted[0].predict([x])[0]), float(fitted[1].predict([x])[0])

    def config(self) -> dict:
        return {"features": "+".join(NBA_FEATURES), **GBT_SETTINGS, "min_examples": MIN_EXAMPLES,
                "min_residuals": MIN_RESIDUALS, "elo": "k{k}-h{home_advantage}-c{carry}-mov{mov}".format(**self.elo_cfg.__dict__)}


# --- NHL ------------------------------------------------------------------------------------------------------------
def _pois(lam: float) -> list[float]:
    out, p = [], math.exp(-lam)
    for k in range(MAX_GOALS + 1):
        out.append(p)
        p *= lam / (k + 1)
    return out


MIN_RETAINED = 0.9   # as GoalsGlm: a grid that keeps less of the Poisson mass is not renormalised into a forecast


def retained_mass(lam_home: float, lam_away: float, empty_net_goals: bool = False) -> float:
    """The share of the independent-Poisson mass inside the 0..MAX_GOALS grid, before any draw inflation (the inflated
    grid always keeps the tie weight, so its sum cannot reveal a truncated tail). Non-finite for a non-finite rate."""
    scale = markets.EMPTY_NET["rate_scale"] if empty_net_goals else 1.0
    return sum(_pois(lam_home * scale)) * sum(_pois(lam_away * scale))


def usable_rates(lam_home: float, lam_away: float, empty_net_goals: bool = False) -> bool:
    m = retained_mass(lam_home, lam_away, empty_net_goals)
    return math.isfinite(m) and m >= MIN_RETAINED


def regulation_pmf(lam_home: float, lam_away: float, draw_weight: float = 0.0, empty_net_goals: bool = False) -> dict:
    """Independent Poisson regulation scores, diagonally inflated: (1 − w)·p + w·p·[h = a] / P(h = a). With
    `empty_net_goals`, rates are scaled and leads adjusted as in markets.poisson_grid (markets.EMPTY_NET)."""
    if empty_net_goals:
        lam_home, lam_away = lam_home * markets.EMPTY_NET["rate_scale"], lam_away * markets.EMPTY_NET["rate_scale"]
    ph, pa = _pois(lam_home), _pois(lam_away)
    pmf = {(h, a): ph[h] * pa[a] for h in range(MAX_GOALS + 1) for a in range(MAX_GOALS + 1)}
    if draw_weight:
        diag = sum(p for (h, a), p in pmf.items() if h == a)
        pmf = {(h, a): (1 - draw_weight) * p + (draw_weight * p / diag if h == a else 0.0) for (h, a), p in pmf.items()}
    return markets.empty_net(pmf) if empty_net_goals else pmf


def _draw_fit_terms(lam_home: float, lam_away: float, h: int, a: int) -> tuple[float, float, bool]:
    """(c, diag, tie) with regulation_pmf(lam_home, lam_away, w, empty_net_goals=True)[(h, a)] == (1 − w)·c + w·c/diag·tie
    for every w. The empty-net step moves mass only out of one- and two-goal leads (never out of a tie), so the probability
    of a score is linear in the inflated pre-step grid: its own cell kept, plus the cell one goal closer that moved to it."""
    scale = markets.EMPTY_NET["rate_scale"]
    ph, pa = _pois(lam_home * scale), _pois(lam_away * scale)
    diag = sum(ph[k] * pa[k] for k in range(MAX_GOALS + 1))
    if h == a:
        return ph[h] * pa[a], diag, True
    move = {1: markets.EMPTY_NET["lead1"], 2: markets.EMPTY_NET["lead2"]}
    lead = abs(h - a)
    c = ph[h] * pa[a] * (1 - move.get(lead, 0.0))
    if lead - 1 in move:
        sh, sa = (h - 1, a) if h > a else (h, a - 1)
        c += ph[sh] * pa[sa] * move[lead - 1]
    return c, diag, False


def grid_from_regulation(pmf: dict, ot_home_share: float, params: dict) -> markets.ScoreGrid:
    """Final-score grid exactly as markets.poisson_grid builds it: a regulation tie k-k becomes k+1-k with the OT share."""
    out: dict = {}
    reg = [0.0, 0.0, 0.0]
    for (h, a), p in pmf.items():
        if h == a:
            reg[1] += p
            out[(h + 1, a)] = out.get((h + 1, a), 0.0) + p * ot_home_share
            out[(h, a + 1)] = out.get((h, a + 1), 0.0) + p * (1 - ot_home_share)
        else:
            reg[0 if h > a else 2] += p
            out[(h, a)] = out.get((h, a), 0.0) + p
    s = sum(reg)
    return markets.ScoreGrid(out, reg3=(reg[0] / s, reg[1] / s, reg[2] / s), params=params)


class GoalsDraw:
    """`goals_poisson` rates with a diagonally inflated regulation distribution; w refit each season start by maximum
    likelihood over earlier seasons' regulation scores and the rates recorded when those games were forecast."""
    name = "goals_draw"

    def __init__(self, goals_cfg, rating_key: Optional[dict] = None):
        from .hockey import GoalsPoisson
        self.goals = GoalsPoisson(goals_cfg, rating_key)
        self.goals_cfg = goals_cfg
        self.pending: dict[str, tuple] = {}
        self.examples: list[tuple] = []     # (season, lam_home, lam_away, regulation score)
        self.season: Optional[int] = None
        self.weight: Optional[float] = None
        self.first_ready_season: Optional[int] = None
        self.fits: list[dict] = []

    def _refit(self, season: int) -> None:
        train = [e for e in self.examples if e[0] < season]
        if len(train) < MIN_HOCKEY_EXAMPLES:
            return
        # Per example: the observed score's probability before inflation and the diagonal mass, on the same grid that
        # `distribution` predicts with (scaled rates, empty-net step); the likelihood of each w follows.
        stats = []
        for _, lh, la, (h, a) in train:
            if h > MAX_GOALS or a > MAX_GOALS:
                continue
            stats.append(_draw_fit_terms(lh, la, h, a))
        best = max(DRAW_GRID, key=lambda w: (sum(math.log(max(1e-300, (1 - w) * p + (w * p / d if tie else 0.0)))
                                                  for p, d, tie in stats), -w))
        self.weight = best
        self.fits.append({"season": season, "training_games": len(stats), "draw_weight": best})
        if self.first_ready_season is None:
            self.first_ready_season = season

    def _prepare(self, g: PregameGame):
        if self.season is None or g.season > self.season:
            self.season = g.season
            self._refit(g.season)
        if g.game_id not in self.pending:
            lh, la = self.goals.rates(g)
            ot = 0.5 if g.neutral_site else self.goals.ot_home_share()
            self.pending[g.game_id] = (g.season, lh, la, ot)
        return self.pending[g.game_id]

    def distribution(self, g: PregameGame) -> Optional[markets.ScoreGrid]:
        _, lh, la, ot = self._prepare(g)
        if self.weight is None or not usable_rates(lh, la, empty_net_goals=True):     # degenerate rate: abstain
            return None
        return grid_from_regulation(regulation_pmf(lh, la, self.weight, empty_net_goals=True), ot,
                                    {"lambda_home": round(lh, 6), "lambda_away": round(la, 6), "ot_home_share": round(ot, 6),
                                     "draw_weight": self.weight, "empty_net": dict(markets.EMPTY_NET)})

    def predict(self, g: PregameGame) -> float:
        d = self.distribution(g)
        return 0.5 if d is None else d.winner()[0]

    def update(self, r: Result) -> None:
        season, lh, la, _ = self.pending.pop(r.game.game_id)
        reg = getattr(r, "regulation", None)
        if reg is not None:
            self.examples.append((season, lh, la, reg))
        self.goals.update(r)

    def config(self) -> dict:
        return {"goals": "eta{eta}-c{carry}".format(**self.goals_cfg.__dict__), "draw_grid": [DRAW_GRID[0], DRAW_GRID[-1], 0.005],
                "min_examples": MIN_HOCKEY_EXAMPLES, "max_goals": MAX_GOALS, "empty_net": dict(markets.EMPTY_NET)}

    def oos_from_season(self) -> Optional[int]:
        return self.first_ready_season


class GoalsGlm:
    """Two L2 Poisson GLMs (home and away regulation goals) on Elo difference, recent goals for/against, rest."""
    name = "goals_glm"

    def __init__(self, elo_cfg, rating_key: Optional[dict] = None):
        from .hockey import HockeyElo
        self.elo_cfg = elo_cfg
        self.elo = HockeyElo(elo_cfg, rating_key)
        self.gf: dict = defaultdict(lambda: deque(maxlen=10))
        self.ga: dict = defaultdict(lambda: deque(maxlen=10))
        self.last_tipoff: dict = {}
        self.rest_seen: dict = {}     # game_id -> rest days: a game predicted again never rests "0 days"
        self.pending: dict[str, tuple] = {}
        self.examples: list[tuple] = []     # (season, x, home reg goals, away reg goals, reg tie, home won)
        self.season: Optional[int] = None
        self.fitted = None
        self.ot_share = 0.5
        self.first_ready_season: Optional[int] = None
        self.fits: list[dict] = []

    def _team(self, t: str) -> str:
        return self.elo._team(t)

    def _vector(self, g: PregameGame) -> list[float]:
        self.elo.predict(g)
        rest = self.rest_seen.get(g.game_id)
        if rest is None:
            rest = self.rest_seen[g.game_id] = {}
            for side, team in (("home", g.home_team_id), ("away", g.away_team_id)):
                prev = self.last_tipoff.get((g.season, self._team(team)))
                rest[side] = REST_CAP if prev is None else min(REST_CAP, _hours(g.tipoff_at, prev) / 24)
                self.last_tipoff[(g.season, self._team(team))] = g.tipoff_at
        h, a = self._team(g.home_team_id), self._team(g.away_team_id)
        mean = lambda d: sum(d) / len(d) if d else 3.0
        f = {"elo_diff": (self.elo.ratings[h] - self.elo.ratings[a]) / 100, "gf10_home": mean(self.gf[h]),
             "ga10_home": mean(self.ga[h]), "gf10_away": mean(self.gf[a]), "ga10_away": mean(self.ga[a]),
             "rest_diff": rest["home"] - rest["away"], "b2b_home": float(rest["home"] < 1.5), "b2b_away": float(rest["away"] < 1.5)}
        return [float(f[k]) for k in NHL_FEATURES]

    def _refit(self, season: int) -> None:
        train = [e for e in self.examples if e[0] < season]
        if len(train) < MIN_HOCKEY_EXAMPLES:
            return
        import numpy as np
        from sklearn.linear_model import PoissonRegressor
        mu, sd = _standardise([e[1] for e in train])
        z = (np.asarray([e[1] for e in train]) - mu) / sd
        models = []
        for k in (2, 3):
            m = PoissonRegressor(alpha=GLM_ALPHA, max_iter=1000)
            m.fit(z, [e[k] for e in train])
            models.append(m)
        ties = [e for e in train if e[4]]
        self.ot_share = (sum(e[5] for e in ties) + 1) / (len(ties) + 2)
        self.fitted = (mu, sd, models)
        self.fits.append({"season": season, "training_games": len(train), "ot_home_share": self.ot_share})
        if self.first_ready_season is None:
            self.first_ready_season = season

    def _prepare(self, g: PregameGame):
        if self.season is None or g.season > self.season:
            self.season = g.season
            self._refit(g.season)
        if g.game_id not in self.pending:
            self.pending[g.game_id] = (g.season, self._vector(g), g.neutral_site)
        return self.pending[g.game_id]

    def distribution(self, g: PregameGame) -> Optional[markets.ScoreGrid]:
        _, x, neutral = self._prepare(g)
        if self.fitted is None:
            return None
        import numpy as np
        mu, sd, (mh, ma) = self.fitted
        z = ((np.asarray(x) - mu) / sd).reshape(1, -1)
        lh, la = float(mh.predict(z)[0]), float(ma.predict(z)[0])
        pmf = regulation_pmf(lh, la, empty_net_goals=True)
        s = sum(pmf.values())
        # A degenerate rate (non-finite, or so large that more than 10% of its mass lies beyond MAX_GOALS, up to exp(-rate)
        # underflowing the grid to zero) has no usable distribution: abstain, never renormalise a mostly truncated grid.
        # (Rates up to ~11 goals keep >= 90%; the synthetic study's largest, 8.45, keeps 99%.)
        if not math.isfinite(s) or s < 0.9:
            return None
        ot = 0.5 if neutral else self.ot_share
        return grid_from_regulation(pmf, ot,
                                    {"lambda_home": round(lh, 6), "lambda_away": round(la, 6), "ot_home_share": round(ot, 6)})

    def predict(self, g: PregameGame) -> float:
        d = self.distribution(g)
        return 0.5 if d is None else d.winner()[0]

    def update(self, r: Result) -> None:
        season, x, neutral = self.pending.pop(r.game.game_id)
        self.elo.update(r)
        reg = getattr(r, "regulation", None)
        if reg is None:          # decision unknown: the regulation score is not guessed
            return
        h, a = self._team(r.game.home_team_id), self._team(r.game.away_team_id)
        self.gf[h].append(reg[0]); self.ga[h].append(reg[1])
        self.gf[a].append(reg[1]); self.ga[a].append(reg[0])
        self.examples.append((season, x, reg[0], reg[1], reg[0] == reg[1] and not neutral, r.home_won))

    def config(self) -> dict:
        return {"features": "+".join(NHL_FEATURES), "alpha": GLM_ALPHA, "min_examples": MIN_HOCKEY_EXAMPLES,
                "empty_net": dict(markets.EMPTY_NET),
                "elo": "k{k}-h{home_advantage}-c{carry}-mov{mov}".format(**self.elo_cfg.__dict__)}

    def oos_from_season(self) -> Optional[int]:
        return self.first_ready_season
