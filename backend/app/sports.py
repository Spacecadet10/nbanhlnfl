"""Sport registry: which store, id namespace, seasons and evaluation periods belong to each sport.

Each sport has its own SQLite store created by the same migrations (docs/MULTISPORT_PLAN.md). Sport-specific rules
live here and in the sport's own source adapter and models; shared infrastructure never assumes a sport.
"""
from dataclasses import dataclass, field

# Served sports: the API, the UI, live jobs, research and every `--sport` command. Code that branches on the sport
# (if "nba" ... else NHL) may assume one of these two, so a sport joins this tuple only once every such path handles it.
SPORT_IDS = ("nba", "nhl")


@dataclass(frozen=True)
class Sport:
    id: str
    label: str
    db_file: str
    id_prefix: str                       # games and teams in this store all start with this
    eligible_season_types: tuple[str, ...]
    markets: tuple[str, ...]             # markets defined for the sport (docs/MARKETS.md)
    # Neutral-site windows known before the games (NBA 2020 bubble; NHL 2020 playoff hubs).
    neutral_windows: tuple[tuple[str, str], ...] = ()
    # Ratings carried across team-id changes (relocations): team id -> rating key.
    rating_key: dict = field(default_factory=dict)


NBA = Sport(
    id="nba", label="NBA", db_file="nba.sqlite", id_prefix="espn:",
    eligible_season_types=("regular", "play_in", "postseason"),
    markets=("winner", "spread", "total", "team_total_home", "team_total_away"),
    neutral_windows=(("2020-07-30T00:00:00Z", "2020-10-13T00:00:00Z"),),
)

NHL = Sport(
    id="nhl", label="NHL", db_file="nhl.sqlite", id_prefix="nhl:",
    eligible_season_types=("regular", "postseason"),
    markets=("winner", "reg3", "spread", "total", "team_total_home", "team_total_away"),
    # 2020 return-to-play qualifying round and playoffs in the Toronto and Edmonton hubs (announced beforehand).
    neutral_windows=(("2020-08-01T00:00:00Z", "2020-09-30T00:00:00Z"),),
    # Arizona Coyotes (53) relocated to Utah (Utah Hockey Club 59, renamed Utah Mammoth 68) with the hockey roster.
    # The NHL lists Utah as a new franchise id; the ratings follow the players, as preregistered in EVALUATION_PLAN.md.
    rating_key={"nhl:59": "nhl:53", "nhl:68": "nhl:53"},
)

# NFL: a data-only store (schedule and results from ESPN's NFL scoreboard; docs/NFL_PLAN.md). It is not served and no
# model forecasts it yet; `get` refuses it, so no served path can open or branch on an NFL store by accident.
# International games (London, Germany, Brazil, ...) and the Super Bowl carry ESPN's neutral-site flag per game.
NFL = Sport(
    id="nfl", label="NFL", db_file="nfl.sqlite", id_prefix="nfl:",
    eligible_season_types=("regular", "postseason"),
    markets=("winner",),
)

SPORTS = {s.id: s for s in (NBA, NHL)}
DATA_ONLY = {NFL.id: NFL}


class UnsupportedSport(ValueError):
    """A store or request for a sport that is registered but not served (or not registered at all)."""


def get(sport: str) -> Sport:
    """A served sport. Raises for NFL (data-only) and unknown ids."""
    try:
        return SPORTS[sport]
    except KeyError:
        raise UnsupportedSport(f"Unknown sport {sport!r}; expected one of {', '.join(SPORT_IDS)}") from None


def store(sport: str) -> Sport:
    """A sport that has a store: served or data-only. Only for store paths, identity and data commands."""
    if sport in SPORTS:
        return SPORTS[sport]
    if sport in DATA_ONLY:
        return DATA_ONLY[sport]
    raise UnsupportedSport(f"Unknown sport {sport!r}; expected one of {', '.join([*SPORT_IDS, *DATA_ONLY])}")


def is_neutral(sport: Sport, flagged: bool, tipoff_at: str) -> bool:
    return flagged or any(a <= tipoff_at < b for a, b in sport.neutral_windows)
