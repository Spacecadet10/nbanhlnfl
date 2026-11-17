"""Adapter for ESPN's public NCAA men's basketball JSON (site.api.espn.com), data only (app/ncaab.py).

Unofficial endpoints, personal non-commercial use only (docs/DATA_SOURCES.md), same terms as the NBA/NFL adapters. Checked
2026-11-17 on days from 2012-03 to 2026-11 (docs/NCAAB_PLAN.md "Evidence"):
- `scoreboard?dates=YYYYMMDD&groups=50&limit=400`: one request per Eastern day. `groups=50` (Division I) is required: without
  it a day returns a fraction of the games. A team outside Division I (an exhibition or lower-division opponent) appears with
  no `conferenceId`; every Division I team carries one. `neutralSite`, `conferenceCompetition`, `notes` (tournament
  headline) and per-period `linescores` (two halves, then one entry per overtime) are on the competition. Season type 2 is the
  regular season (including conference tournaments), 3 the postseason. `statistics` carry points, FGM/FGA, 3PM/3PA, FTM/FTA,
  rebounds and assists but NOT offensive rebounds or turnovers: possessions need the game summary.
- Odds (DraftKings spread, total, moneyline, open and current) are on scheduled games only; finals carry none.
- `summary?event=ID`: team box score (offensive rebounds, turnovers) and `pickcenter` odds. A summary fetched after the game
  is retrieved after the game: its odds are never a verified pregame quote.
- `teams?groups=50&limit=1000`: the current Division I team list (365 teams on 2026-11-17).
"""
from dataclasses import dataclass
from typing import Optional

from . import espn
from .espn import GameRow, TeamRow, normalize_time

SOURCE = "espn_ncaab"
ENDPOINT = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"
IMPORTER_VERSION = "espn-ncaab-scoreboard-1"
TEAMS_SOURCE = "espn_ncaab_teams"
TEAMS_ENDPOINT = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/teams"
TEAMS_IMPORTER_VERSION = "espn-ncaab-teams-1"
TEAMS_REQUEST_KEY = "groups=50&limit=1000"
SUMMARY_SOURCE = "espn_ncaab_summary"
SUMMARY_ENDPOINT = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/summary"
SUMMARY_IMPORTER_VERSION = "espn-ncaab-summary-1"
PREFIX = "ncaab:"
GROUP_DIVISION_I = "50"

# ESPN season type: 1 preseason (exhibitions), 2 regular season (with conference tournaments), 3 postseason (NCAA
# Tournament, NIT and the other postseason events). Anything else is 'other'.
SEASON_TYPES = {1: "preseason", 2: "regular", 3: "postseason"}


def day_request(yyyymmdd: str) -> tuple[str, dict]:
    if len(yyyymmdd) != 8 or not yyyymmdd.isdigit():
        raise ValueError(f"Expected YYYYMMDD, got {yyyymmdd!r}")
    params = {"dates": yyyymmdd, "groups": GROUP_DIVISION_I, "limit": "400"}
    return "&".join(f"{k}={v}" for k, v in sorted(params.items())), params


def fetch_day(yyyymmdd: str, timeout: float = 60) -> bytes:
    return espn._get(day_request(yyyymmdd)[1], timeout, ENDPOINT)


def fetch_teams(timeout: float = 60) -> bytes:
    return espn._get({"groups": GROUP_DIVISION_I, "limit": "1000"}, timeout, TEAMS_ENDPOINT)


def summary_request(game_id: str) -> tuple[str, dict]:
    event = game_id.split(":", 1)[1] if game_id.startswith(PREFIX) else game_id
    if not event.isdigit():
        raise ValueError(f"Expected an ESPN event id, got {game_id!r}")
    return f"event={event}", {"event": event}


def fetch_summary(game_id: str, timeout: float = 60) -> bytes:
    return espn._get(summary_request(game_id)[1], timeout, SUMMARY_ENDPOINT)


@dataclass
class NcaabGame(GameRow):
    conference_competition: bool = False
    home_conference_id: Optional[str] = None
    away_conference_id: Optional[str] = None
    tournament_note: Optional[str] = None
    season_type_id: Optional[int] = None
    ot_periods: Optional[int] = None          # periods beyond the two halves; None until the game is final
    source_status: str = ""

    @property
    def both_d1(self) -> bool:
        """Division I game: both teams carry a conference id on this scoreboard (docs/NCAAB_PLAN.md)."""
        return bool(self.home_conference_id and self.away_conference_id)


def _conference(competitor: dict) -> Optional[str]:
    value = (competitor.get("team") or {}).get("conferenceId")
    return str(value) if value not in (None, "") else None


def _score(competitor: dict) -> Optional[int]:
    try:
        return int(competitor["score"])
    except (KeyError, TypeError, ValueError):
        return None


def _ot_periods(sides: dict) -> Optional[int]:
    periods = max(len(c.get("linescores") or []) for c in sides.values())
    return max(periods - 2, 0) if periods >= 2 else None


def parse_scoreboard(payload: dict) -> list[NcaabGame]:
    """Every game of a day scoreboard. A final result needs both scores; a 'final' without them is stored as status 'other'
    (never a result). A game with a team outside Division I is season_type 'other': stored (so a rerun is idempotent) but
    never rated, forecast, graded or counted."""
    games = []
    for ev in payload.get("events", []):
        comp = ev["competitions"][0]
        sides = {c["homeAway"]: c for c in comp["competitors"]}
        if set(sides) != {"home", "away"}:
            continue
        source_status = comp["status"]["type"]["name"]
        status = espn.STATUS.get(source_status, "other")
        scores = {k: _score(c) for k, c in sides.items()}
        if status == "final" and None in scores.values():
            status = "other"
        final = status == "final"
        teams = {k: TeamRow(team_id=f"{PREFIX}{c['team']['id']}", abbr=c["team"].get("abbreviation", "?"),
                            name=c["team"].get("displayName") or c["team"].get("name", "?")) for k, c in sides.items()}
        type_id = (ev.get("season") or {}).get("type")
        notes = [n.get("headline") for n in comp.get("notes") or [] if n.get("headline")]
        g = NcaabGame(
            game_id=f"{PREFIX}{ev['id']}", season=int(ev["season"]["year"]), season_type=SEASON_TYPES.get(type_id, "other"),
            tipoff_at=normalize_time(ev["date"]), home=teams["home"], away=teams["away"],
            neutral_site=bool(comp.get("neutralSite")), status=status,
            home_score=scores["home"] if final else None, away_score=scores["away"] if final else None,
            conference_competition=bool(comp.get("conferenceCompetition")),
            home_conference_id=_conference(sides["home"]), away_conference_id=_conference(sides["away"]),
            tournament_note=notes[0] if notes else None, season_type_id=type_id,
            ot_periods=_ot_periods(sides) if final else None, source_status=source_status)
        if not g.both_d1:
            g.season_type = "other"
        if final:
            g.home_stats = espn._stats(sides["home"])
            g.away_stats = espn._stats(sides["away"])
        games.append(g)
    return games


def parse_teams(payload: dict) -> list[TeamRow]:
    """The Division I team list (id, abbreviation, display name), as ESPN lists it."""
    out = []
    for sport in payload.get("sports", []):
        for league in sport.get("leagues", []):
            for item in league.get("teams", []):
                t = item.get("team") or {}
                if t.get("id"):
                    out.append(TeamRow(team_id=f"{PREFIX}{t['id']}", abbr=t.get("abbreviation", "?"),
                                       name=t.get("displayName") or t.get("name", "?")))
    return out


def parse_summary(payload: dict) -> espn.SummaryBox:
    """Team lines of a summary (same fields as the NBA summary), under NCAAB ids."""
    box = espn.parse_summary_box(payload)
    lines = [espn.SummaryLine(team_id=PREFIX + l.team_id.split(":", 1)[1], is_home=l.is_home, points=l.points, fga=l.fga,
                              fta=l.fta, oreb=l.oreb, dreb=l.dreb, tov=l.tov) for l in box.lines]
    return espn.SummaryBox(game_id=PREFIX + box.game_id.split(":", 1)[1], completed=box.completed, lines=lines)


@dataclass
class AfterGameLine:
    provider: str
    details: Optional[str]
    spread_home: Optional[float]     # the home team's handicap (negative: home favoured)
    over_under: Optional[float]


def _float(value) -> Optional[float]:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def parse_summary_lines(payload: dict) -> list[AfterGameLine]:
    """Odds listed in a summary's `pickcenter`, exactly as given. Read after the game: a benchmark, never a pregame quote.
    A spread whose sign disagrees with which side the entry marks as the favourite is not kept (the total is)."""
    out = []
    for p in payload.get("pickcenter") or []:
        spread, total = _float(p.get("spread")), _float(p.get("overUnder"))
        home_fav = (p.get("homeTeamOdds") or {}).get("favorite")
        if spread is not None and spread != 0 and home_fav is not None and (spread < 0) != bool(home_fav):
            spread = None
        if spread is None and total is None:
            continue
        out.append(AfterGameLine(provider=(p.get("provider") or {}).get("name", "unknown"), details=p.get("details"),
                                 spread_home=spread, over_under=total))
    return out
