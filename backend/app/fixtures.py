"""Deterministic demo fixtures. The single source of demo data for the app; the frontend never embeds its own.

Everything here is synthetic (run_type="demo"). Probabilities come from made-up team ratings, not a model,
and outcomes are simulated. None of it describes real NBA games or real prediction accuracy.
"""
import math
import random
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

from .schemas import GameDetail, Market, Outcome, Prediction, SourceStatus, Team, TeamStats

RUN_TYPE = "demo"
MODEL_VERSION = "demo-fixture-0"
SEED = 2026

# (abbr, name, synthetic strength rating)
_TEAMS = [
    ("BOS", "Boston Celtics", 5.5), ("NYK", "New York Knicks", 3.0), ("MIL", "Milwaukee Bucks", 1.5),
    ("CLE", "Cleveland Cavaliers", 4.0), ("MIA", "Miami Heat", 0.0), ("PHI", "Philadelphia 76ers", -1.0),
    ("DEN", "Denver Nuggets", 4.5), ("OKC", "Oklahoma City Thunder", 6.0), ("LAL", "Los Angeles Lakers", 1.0),
    ("GSW", "Golden State Warriors", 0.5), ("PHX", "Phoenix Suns", -0.5), ("DAL", "Dallas Mavericks", 2.0),
]
TEAMS = {abbr: Team(abbr=abbr, name=name) for abbr, name, _ in _TEAMS}
_RATING = {abbr: rating for abbr, _, rating in _TEAMS}

FIRST_DATE = date(2026, 10, 21)
DEFAULT_DATE = date(2026, 10, 30)  # the demo "today": scheduled games, no outcomes yet
OFF_DAYS = {date(2026, 10, 25)}  # deliberately empty slate to exercise the empty state
GAMES_PER_DAY = 4
HOME_EDGE = 2.5
MISSING_MARKET_EVERY = 4  # every 4th game has no market quote, to exercise missing-data states

SOURCES = [
    SourceStatus(name="Demo schedule", kind="schedule", mode="demo",
                 last_updated=datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc), freshness="fresh",
                 note="Synthetic fixture, not a live feed."),
    SourceStatus(name="Demo results", kind="results", mode="demo",
                 last_updated=datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc), freshness="fresh",
                 note="Synthetic fixture, not a live feed."),
    SourceStatus(name="Demo odds", kind="odds", mode="demo",
                 last_updated=datetime(2026, 10, 29, 15, 0, tzinfo=timezone.utc), freshness="stale",
                 note="Deliberately stale to demonstrate the stale-data state."),
]


def _prob(home: str, away: str) -> float:
    return 1 / (1 + math.exp(-(_RATING[home] - _RATING[away] + HOME_EDGE) / 6))


def demo_dates() -> list[date]:
    days = (DEFAULT_DATE - FIRST_DATE).days + 1
    return [d for d in (FIRST_DATE + timedelta(n) for n in range(days)) if d not in OFF_DAYS]


@lru_cache(maxsize=1)
def games() -> tuple[GameDetail, ...]:
    rng = random.Random(SEED)
    out = []
    index = 0
    for day in demo_dates():
        abbrs = sorted(TEAMS)
        rng.shuffle(abbrs)
        for slot in range(GAMES_PER_DAY):
            home, away = abbrs[2 * slot], abbrs[2 * slot + 1]
            game_id = f"demo-{day:%Y%m%d}-{away}-{home}"
            tipoff = datetime(day.year, day.month, day.day, 23, tzinfo=timezone.utc) + timedelta(minutes=30 * slot)
            p = round(_prob(home, away), 3)
            market = None
            if index % MISSING_MARKET_EVERY != MISSING_MARKET_EVERY - 1:
                mp = min(0.97, max(0.03, p + rng.uniform(-0.06, 0.06)))
                market = Market(bookmaker="Demo book", price_label="current", quote_time=tipoff - timedelta(hours=2),
                                retrieved_at=tipoff - timedelta(hours=2), home_win_prob=round(mp, 3))
            outcome = None
            if day < DEFAULT_DATE:
                home_won = rng.random() < p
                base = rng.randint(100, 118)
                margin = rng.randint(1, 18)
                hs, as_ = (base + margin, base) if home_won else (base, base + margin)
                outcome = Outcome(home_score=hs, away_score=as_, home_won=home_won)
            out.append(GameDetail(
                game_id=game_id, mode="demo", game_date=day, tipoff=tipoff,
                status="final" if outcome else "scheduled", season_type="regular",
                home=TEAMS[home], away=TEAMS[away],
                prediction=Prediction(model_version=MODEL_VERSION, run_type=RUN_TYPE,
                                      generated_at=tipoff - timedelta(hours=6),
                                      cutoff_at=tipoff - timedelta(hours=5), home_win_prob=p),
                forecast_note=None, market=market, outcome=outcome, stats_as_of=tipoff - timedelta(hours=5),
                home_stats=_stats(rng, home), away_stats=_stats(rng, away),
                projected_score_available=False,
            ))
            index += 1
    return tuple(out)


def _stats(rng: random.Random, abbr: str) -> TeamStats:
    r = _RATING[abbr]
    played = rng.randint(10, 16)
    wins = min(played, max(0, round(played * (0.5 + r / 15) + rng.uniform(-1.5, 1.5))))
    last10 = min(10, wins, max(0, 10 - (played - wins), round(5 + r / 1.5 + rng.uniform(-1, 1))))
    return TeamStats(
        wins=wins, losses=played - wins, last10_wins=last10, last10_games=10,
        points_for_avg=round(113 + r / 2 + rng.uniform(-2, 2), 1),
        points_against_avg=round(113 - r / 2 + rng.uniform(-2, 2), 1),
        rest_days=float(rng.randint(1, 4)),
    )
