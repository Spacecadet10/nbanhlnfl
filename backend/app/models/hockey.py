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
