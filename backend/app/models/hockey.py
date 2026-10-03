"""NHL models (docs/EVALUATION_PLAN.md, "NHL evaluation plan"). No NBA parameter value is reused.

`HockeyElo` is its own class (the sealed NBA Elo and its version string are untouched). `GoalsPoisson` models regulation
goals as independent Poisson counts with online attack/defence strengths and turns them into a final-score distribution
(markets.poisson_grid), from which every NHL market is derived.
"""
import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Optional

from .. import markets
from .base import PregameGame, Result
from .elo import INITIAL, Elo


@dataclass(frozen=True)
class HockeyResult(Result):
    """A final NHL game with how it was decided. Regulation goals remove the deciding OT/SO goal from the winner."""
    decision: Optional[str] = None

    @property
    def regulation(self) -> Optional[tuple[int, int]]:
        if self.decision == "REG":
            return self.home_score, self.away_score
        if self.decision in ("OT", "SO"):
            return (self.home_score - 1, self.away_score) if self.home_won else (self.home_score, self.away_score - 1)
        return None


@dataclass(frozen=True)
class HockeyEloConfig:
    k: float = 8.0
    home_advantage: float = 30.0
    carry: float = 0.75
    mov: bool = False   # multiplier ln(|margin| + 1) / ln 2 (1 for a one-goal game)


class HockeyElo(Elo):
    name = "hockey_elo"

    def __init__(self, cfg: HockeyEloConfig = HockeyEloConfig(), rating_key: Optional[dict] = None):
        super().__init__(cfg)  # type: ignore[arg-type]  # same fields; Elo only reads k/home_advantage/carry
        self.key = dict(rating_key or {})

    def _team(self, team_id: str) -> str:
        return self.key.get(team_id, team_id)

    def _diff(self, game: PregameGame) -> float:
        h = 0.0 if game.neutral_site else self.cfg.home_advantage
        return self.ratings[self._team(game.home_team_id)] + h - self.ratings[self._team(game.away_team_id)]

    def update(self, result: Result) -> None:
        g = result.game
        self._roll_season(g.season)
        expected = 1 / (1 + 10 ** (-self._diff(g) / 400))
        mult = math.log(abs(result.home_score - result.away_score) + 1) / math.log(2) if self.cfg.mov else 1.0
        delta = self.cfg.k * mult * ((1.0 if result.home_won else 0.0) - expected)
        self.ratings[self._team(g.home_team_id)] += delta
        self.ratings[self._team(g.away_team_id)] -= delta


@dataclass(frozen=True)
class GoalsConfig:
    eta: float = 0.01     # gradient step on the Poisson log-likelihood per result; 0 = league_poisson (no team strength)
    carry: float = 0.75   # share of attack/defence kept at a new season


LEAGUE_RATE = 0.01        # exponential weight of each new result in the running league home/away goal means (fixed)
PRIOR_GOALS = 3.0         # starting league mean per team in regulation; washed out during the warm-up seasons


class GoalsPoisson:
    """log λ_home = μ + h + att_home − def_away; log λ_away = μ + att_away − def_home (regulation goals)."""

    def __init__(self, cfg: GoalsConfig = GoalsConfig(), rating_key: Optional[dict] = None):
        self.cfg = cfg
        self.key = dict(rating_key or {})
        self.att: dict[str, float] = defaultdict(float)
        self.dfn: dict[str, float] = defaultdict(float)
        self.home_mean = PRIOR_GOALS
        self.away_mean = PRIOR_GOALS
        self.ot_home_wins = 0
        self.ot_games = 0
        self.season: Optional[int] = None

    @property
    def name(self) -> str:
        return "league_poisson" if self.cfg.eta == 0 else "goals_poisson"

    def config(self) -> dict:
        return {**asdict(self.cfg), "league_rate": LEAGUE_RATE, "prior_goals": PRIOR_GOALS}

    def _team(self, team_id: str) -> str:
        return self.key.get(team_id, team_id)

    def _roll(self, season: int) -> None:
        if self.season is not None and season > self.season:
            for d in (self.att, self.dfn):
                for t in d:
                    d[t] *= self.cfg.carry
        if self.season is None or season > self.season:
            self.season = season

    def rates(self, game: PregameGame) -> tuple[float, float]:
        self._roll(game.season)
        h, a = self._team(game.home_team_id), self._team(game.away_team_id)
        mu = math.log(self.away_mean)
        adv = 0.0 if game.neutral_site else math.log(self.home_mean / self.away_mean)
        return (math.exp(mu + adv + self.att[h] - self.dfn[a]), math.exp(mu + self.att[a] - self.dfn[h]))

    def ot_home_share(self) -> float:
        return (self.ot_home_wins + 1) / (self.ot_games + 2)

    def distribution(self, game: PregameGame) -> markets.ScoreGrid:
        lh, la = self.rates(game)
        return markets.poisson_grid(lh, la, 0.5 if game.neutral_site else self.ot_home_share())

    def predict(self, game: PregameGame) -> float:
        return self.distribution(game).winner()[0]

    def update(self, result: Result) -> None:
        reg = getattr(result, "regulation", None)
        g = result.game
        if reg is None:   # decision unknown: no regulation score; the result is not used rather than guessed
            return
        lh, la = self.rates(g)
        eh, ea = reg[0] - lh, reg[1] - la
        h, a = self._team(g.home_team_id), self._team(g.away_team_id)
        eta = self.cfg.eta
        self.att[h] += eta * eh
        self.dfn[a] -= eta * eh
        self.att[a] += eta * ea
        self.dfn[h] -= eta * ea
        if not g.neutral_site:
            self.home_mean += LEAGUE_RATE * (reg[0] - self.home_mean)
            self.away_mean += LEAGUE_RATE * (reg[1] - self.away_mean)
            if reg[0] == reg[1]:
                self.ot_games += 1
                self.ot_home_wins += int(result.home_won)


def regulation_nll(lam_home: float, lam_away: float, reg: tuple[int, int]) -> float:
    """Negative log-likelihood of a regulation score under independent Poisson goals (the goals-model tuning loss)."""
    def lp(lam, k):
        return -lam + k * math.log(lam) - math.lgamma(k + 1)
    return -(lp(lam_home, reg[0]) + lp(lam_away, reg[1]))


INITIAL_RATING = INITIAL


# --- Learned candidates (EVALUATION_PLAN "NHL learned candidates"); development research only ---------------------
LEARNED_FEATURES = ("elo_diff", "xg_diff", "gd10_diff", "rest_diff", "b2b_home", "b2b_away", "neutral")
LEARNED_SCALED = ("elo_diff", "xg_diff", "gd10_diff", "rest_diff")
LEARNED_MONOTONIC = ("elo_diff", "xg_diff", "gd10_diff")


class HockeyFeatures:
    """Point-in-time NHL features with the TeamFeatures interface (compute / update / elo): ratings, expected goals and
    recent goal differential change only through update() (available results); rest uses earlier scheduled tipoffs."""

    def __init__(self, elo_cfg: HockeyEloConfig, goals_cfg: GoalsConfig, rating_key: Optional[dict] = None):
        from collections import deque
        self.elo = HockeyElo(elo_cfg, rating_key)
        self.goals = GoalsPoisson(goals_cfg, rating_key)
        self.margins: dict = defaultdict(lambda: deque(maxlen=10))
        self.last_tipoff: dict = {}
        self.rest_seen: dict = {}     # game_id -> rest days: a game predicted again never rests "0 days"

    def compute(self, g: PregameGame) -> dict:
        from .logistic import REST_CAP, _hours
        self.elo.predict(g)
        lh, la = self.goals.rates(g)
        rest = self.rest_seen.get(g.game_id)
        if rest is None:
            rest = self.rest_seen[g.game_id] = {}
            for side, team in (("home", g.home_team_id), ("away", g.away_team_id)):
                prev = self.last_tipoff.get((g.season, team))
                rest[side] = REST_CAP if prev is None else min(REST_CAP, _hours(g.tipoff_at, prev) / 24)
                self.last_tipoff[(g.season, team)] = g.tipoff_at
        mh, ma = self.margins[self.elo._team(g.home_team_id)], self.margins[self.elo._team(g.away_team_id)]
        return {"elo_diff": (self.elo.ratings[self.elo._team(g.home_team_id)] - self.elo.ratings[self.elo._team(g.away_team_id)]) / 100,
                "xg_diff": lh - la,
                "gd10_diff": (sum(mh) / len(mh) if mh else 0.0) - (sum(ma) / len(ma) if ma else 0.0),
                "rest_diff": rest["home"] - rest["away"], "b2b_home": float(rest["home"] < 1.5),
                "b2b_away": float(rest["away"] < 1.5), "neutral": float(g.neutral_site)}

    def update(self, r: Result) -> None:
        self.elo.update(r)
        self.goals.update(r)
        m = r.home_score - r.away_score
        self.margins[self.elo._team(r.game.home_team_id)].append(m)
        self.margins[self.elo._team(r.game.away_team_id)].append(-m)


def hockey_logistic(elo_cfg: HockeyEloConfig, goals_cfg: GoalsConfig, rating_key: Optional[dict] = None):
    from .logistic import LogisticElo

    class HockeyLogistic(LogisticElo):
        name = "hockey_logistic"
        feature_names = LEARNED_FEATURES
        scaled_names = LEARNED_SCALED

        def config(self) -> dict:
            return {"l2": self.l2, "features": "+".join(self.feature_names),
                    "elo": "k{k}-h{home_advantage}-c{carry}-mov{mov}".format(**asdict(elo_cfg)),
                    "goals": "eta{eta}-c{carry}".format(**asdict(goals_cfg))}
    m = HockeyLogistic(elo_cfg)   # type: ignore[arg-type]
    m.features = HockeyFeatures(elo_cfg, goals_cfg, rating_key)
    return m


def hockey_gbt(elo_cfg: HockeyEloConfig, goals_cfg: GoalsConfig, rating_key: Optional[dict] = None):
    from .gbt import GradientBoostedTrees

    class HockeyGBT(GradientBoostedTrees):
        name = "hockey_gbt"
        feature_names = LEARNED_FEATURES
        monotonic = LEARNED_MONOTONIC

        def _vector(self, g: PregameGame) -> list[float]:
            feats = self.team.compute(g)
            return [float(feats[f]) for f in self.feature_names]

        def config(self) -> dict:
            return {**super().config(), "elo": "k{k}-h{home_advantage}-c{carry}-mov{mov}".format(**asdict(elo_cfg)),
                    "goals": "eta{eta}-c{carry}".format(**asdict(goals_cfg))}
    m = HockeyGBT(elo_cfg)   # type: ignore[arg-type]
    m.team = HockeyFeatures(elo_cfg, goals_cfg, rating_key)
    return m
