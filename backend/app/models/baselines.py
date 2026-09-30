from .base import PregameGame, Result


class Constant:
    name = "constant"

    def predict(self, game: PregameGame) -> float:
        return 0.5

    def update(self, result: Result) -> None:
        pass

    def config(self) -> dict:
        return {"p": 0.5}


class HomeRate:
    """Laplace-smoothed share of home wins among non-neutral results seen so far; 0.5 at neutral sites."""
    name = "home_rate"

    def __init__(self):
        self.home_wins = 0
        self.games = 0

    def predict(self, game: PregameGame) -> float:
        if game.neutral_site:
            return 0.5
        return (self.home_wins + 1) / (self.games + 2)

    def update(self, result: Result) -> None:
        if result.game.neutral_site:
            return
        self.games += 1
        self.home_wins += int(result.home_won)

    def config(self) -> dict:
        return {"smoothing": "laplace"}
