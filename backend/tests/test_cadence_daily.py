"""daily-v1 (MODEL_SELECTION §17): a refit only on a new Eastern date with newly delivered results; never on future results."""
from types import SimpleNamespace

from app.cadence_daily import Daily


class Fake:
    name = "fake"

    def __init__(self):
        self.season, self.examples, self.fits = None, [], []

    def _refit(self, season):
        self.fits.append((season, len(self.examples)))

    def predict(self, g):
        if self.season != g.season:
            self.season = g.season
            self._refit(g.season)
        return 0.5

    def update(self, r):
        self.examples.append(r)

    def config(self):
        return {}


def game(gid, season, tip):
    return SimpleNamespace(game_id=gid, season=season, tipoff_at=tip)


def test_daily_refits_only_on_new_dates_with_new_results():
    m = Fake()
    d = Daily(m)
    d.predict(game("a", 2021, "2021-01-01T00:00:00Z"))                   # first season: never refitted within it
    d.update(SimpleNamespace(game=game("a", 2021, "")))
    d.predict(game("b", 2022, "2022-01-10T23:00:00Z"))                   # season start (member's own fit)
    d.predict(game("c", 2022, "2022-01-11T23:00:00Z"))                   # new date, no new result: no refit
    assert d.refits == [] and d.skipped_no_new_results == 1
    d.update(SimpleNamespace(game=game("b", 2022, "")))
    d.predict(game("d", 2022, "2022-01-11T23:30:00Z"))                   # same Eastern date: no refit
    assert d.refits == []
    d.predict(game("e", 2022, "2022-01-12T23:00:00Z"))                   # new date with a new result: refit on 2 examples
    assert len(d.refits) == 1 and d.refits[0]["training_games"] == 2 and m.fits[-1] == (2023, 2)
