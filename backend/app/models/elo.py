import math
from collections import defaultdict
from dataclasses import asdict, dataclass

from .base import PregameGame, Result

INITIAL = 1500.0


@dataclass(frozen=True)
class EloConfig:
    k: float = 20.0
    home_advantage: float = 100.0
    carry: float = 0.75  # fraction of (rating - 1500) kept at a new season
    mov: bool = False    # FiveThirtyEight-style margin-of-victory multiplier


class Elo:
    name = "elo"

    def __init__(self, cfg: EloConfig = EloConfig()):
        self.cfg = cfg
        self.ratings: dict[str, float] = defaultdict(lambda: INITIAL)
        self.season: int | None = None

    def _roll_season(self, season: int) -> None:
        # Season regression uses only ratings built from earlier, already-available results.
        if self.season is not None and season > self.season:
            for team, r in self.ratings.items():
                self.ratings[team] = INITIAL + self.cfg.carry * (r - INITIAL)
        if self.season is None or season > self.season:
            self.season = season

    def _diff(self, game: PregameGame) -> float:
        h = 0.0 if game.neutral_site else self.cfg.home_advantage
        return self.ratings[game.home_team_id] + h - self.ratings[game.away_team_id]

    def predict(self, game: PregameGame) -> float:
        self._roll_season(game.season)
        return 1 / (1 + 10 ** (-self._diff(game) / 400))

    def update(self, result: Result) -> None:
        g = result.game
        self._roll_season(g.season)
        diff = self._diff(g)
        expected = 1 / (1 + 10 ** (-diff / 400))
        actual = 1.0 if result.home_won else 0.0
        mult = 1.0
        if self.cfg.mov:
            margin = abs(result.home_score - result.away_score)
            winner_diff = diff if result.home_won else -diff
            mult = ((margin + 3) ** 0.8) / (7.5 + 0.006 * winner_diff)
        delta = self.cfg.k * mult * (actual - expected)
        self.ratings[g.home_team_id] += delta
        self.ratings[g.away_team_id] -= delta

    def config(self) -> dict:
        return asdict(self.cfg)
