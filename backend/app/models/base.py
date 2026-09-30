from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class PregameGame:
    """The only information a predictor receives: no scores, no outcome, no post-cutoff fields."""
    game_id: str
    season: int
    season_type: str
    tipoff_at: str
    home_team_id: str
    away_team_id: str
    neutral_site: bool


@dataclass(frozen=True)
class Result:
    """A completed game, delivered to models only once it was available before the current cutoff."""
    game: PregameGame
    home_score: int
    away_score: int

    @property
    def home_won(self) -> bool:
        return self.home_score > self.away_score


class Model(Protocol):
    name: str

    def predict(self, game: PregameGame) -> float: ...

    def update(self, result: Result) -> None: ...

    def config(self) -> dict: ...
