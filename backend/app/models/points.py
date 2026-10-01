"""NBA score model `points_normal` (docs/EVALUATION_PLAN.md, "NBA spread and total research").

Online offence/defence point ratings; margin and total are independent discretised Normals whose standard deviations are
the running root-mean-square residuals of earlier available results (markets.MarginTotalNormal).
"""
import math
from collections import defaultdict
from dataclasses import asdict, dataclass

from .. import markets
from .base import PregameGame, Result


@dataclass(frozen=True)
class PointsConfig:
    eta: float = 0.05     # gradient step per result on squared error; 0 = league reference (home edge only)
    carry: float = 0.75   # share of offence/defence kept at a new season


LEAGUE_RATE = 0.01        # weight of each new result in the running league means and residual variances (fixed)
PRIOR_POINTS = 110.0
PRIOR_SD_MARGIN = 13.0
PRIOR_SD_TOTAL = 19.0


class PointsNormal:
    def __init__(self, cfg: PointsConfig = PointsConfig()):
        self.cfg = cfg
        self.off: dict[str, float] = defaultdict(float)
        self.dfn: dict[str, float] = defaultdict(float)
        self.home_mean = PRIOR_POINTS
        self.away_mean = PRIOR_POINTS
        self.var_margin = PRIOR_SD_MARGIN ** 2
        self.var_total = PRIOR_SD_TOTAL ** 2
        self.season = None

    @property
    def name(self) -> str:
        return "points_league" if self.cfg.eta == 0 else "points_normal"

    def config(self) -> dict:
        return {**asdict(self.cfg), "league_rate": LEAGUE_RATE}

    def _roll(self, season: int) -> None:
        if self.season is not None and season > self.season:
            for d in (self.off, self.dfn):
                for t in d:
                    d[t] *= self.cfg.carry
        if self.season is None or season > self.season:
            self.season = season

    def expected(self, game: PregameGame) -> tuple[float, float]:
        self._roll(game.season)
        if game.neutral_site:
            base_h = base_a = (self.home_mean + self.away_mean) / 2
        else:
            base_h, base_a = self.home_mean, self.away_mean
        h, a = game.home_team_id, game.away_team_id
        return base_h + self.off[h] - self.dfn[a], base_a + self.off[a] - self.dfn[h]

    def distribution(self, game: PregameGame) -> markets.MarginTotalNormal:
        ph, pa = self.expected(game)
        return markets.MarginTotalNormal(ph - pa, math.sqrt(self.var_margin), ph + pa, math.sqrt(self.var_total))

    def predict(self, game: PregameGame) -> float:
        return self.distribution(game).winner()[0]

    def update(self, result: Result) -> None:
        g = result.game
        ph, pa = self.expected(g)
        eh, ea = result.home_score - ph, result.away_score - pa
        self.var_margin += LEAGUE_RATE * ((eh - ea) ** 2 - self.var_margin)
        self.var_total += LEAGUE_RATE * ((eh + ea) ** 2 - self.var_total)
        eta = self.cfg.eta
        h, a = g.home_team_id, g.away_team_id
        self.off[h] += eta * eh
        self.dfn[a] -= eta * eh
        self.off[a] += eta * ea
        self.dfn[h] -= eta * ea
        if not g.neutral_site:
            self.home_mean += LEAGUE_RATE * (result.home_score - self.home_mean)
            self.away_mean += LEAGUE_RATE * (result.away_score - self.away_mean)


def gaussian_nll(x: float, mean: float, sd: float) -> float:
    return 0.5 * math.log(2 * math.pi * sd * sd) + (x - mean) ** 2 / (2 * sd * sd)
