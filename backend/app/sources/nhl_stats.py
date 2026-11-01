"""Adapter for the NHL's own stats REST game list (api.nhle.com/stats/rest/en/game), one request per season.

NHL-owned, public, no key; undocumented (no published SLA). Checked 2026-09-30: seasons 2015-16 … 2026-27 return every
scheduled game of the season (preseason, regular season, playoffs, exhibitions) with start time, final score and the last
period played. Final scores include the single goal credited to a shootout winner (as sportsbooks settle).
No neutral-site flag is published here: games are home/away as listed, except the preregistered 2020 hub window
(app/sports.py). Team names come from the separate /team list (one request).
"""
import json
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from .espn import GameRow, TeamRow

SOURCE = "nhl_stats"
GAME_ENDPOINT = "https://api.nhle.com/stats/rest/en/game"
TEAM_ENDPOINT = "https://api.nhle.com/stats/rest/en/team"
IMPORTER_VERSION = "nhl-stats-game-1"
HEADERS = {"Accept": "application/json"}
EASTERN = ZoneInfo("America/New_York")

GAME_TYPES = {1: "preseason", 2: "regular", 3: "postseason"}   # others (4, 19, 20: exhibitions, All-Star) -> 'other'
FINAL_STATES = {6, 7}           # FINAL, OFF
LIVE_STATES = {3, 4, 5}         # LIVE, CRIT, OVER
POSTPONED_SCHEDULE_STATE = 5    # gameScheduleStateId seen on a postponed 2024-25 preseason game
# A game still listed as not started this long after its date was never played ("if necessary" playoff games, or a
# regular-season game that was postponed and then dropped). It is never treated as scheduled.
UNPLAYED_AFTER = timedelta(days=2)


def season_request(season: int) -> tuple[str, dict]:
    """`season` is the ending year (2025 = 2024-25)."""
    if not 1918 <= season <= 2100:
        raise ValueError(f"Expected a season ending year, got {season!r}")
    sid = f"{season - 1}{season}"
    return f"season={sid}", {"cayenneExp": f"season={sid}"}


def day_request(season: int, day: str) -> tuple[str, dict]:
    """One Eastern game date (YYYY-MM-DD) of a season: the same game list rows as `season_request`, filtered to that date
    (≈1 KB instead of ≈350 KB; checked against the season list on 2026-11-01). Used by the frequent status refresh."""
    season_request(season)
    if len(day) != 10 or day[4] != "-" or day[7] != "-" or not (day[:4] + day[5:7] + day[8:]).isdigit():
        raise ValueError(f"Expected YYYY-MM-DD, got {day!r}")
    sid = f"{season - 1}{season}"
    return f"season={sid}&gameDate={day}", {"cayenneExp": f'season={sid} and gameDate="{day}"'}


def team_request() -> tuple[str, dict]:
    return "teams", {}


def _get(endpoint: str, params: dict, timeout: float) -> bytes:
    import httpx

    resp = httpx.get(endpoint, params=params, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    return resp.content


def fetch_season(season: int, timeout: float = 60) -> bytes:
    return _get(GAME_ENDPOINT, season_request(season)[1], timeout)


def fetch_day(season: int, day: str, timeout: float = 30) -> bytes:
    return _get(GAME_ENDPOINT, day_request(season, day)[1], timeout)


def fetch_teams(timeout: float = 30) -> bytes:
    return _get(TEAM_ENDPOINT, {}, timeout)


def parse_teams(payload: dict) -> dict[int, TeamRow]:
    return {t["id"]: TeamRow(team_id=f"nhl:{t['id']}", abbr=t.get("triCode") or "?", name=t.get("fullName") or "?")
            for t in payload.get("data", []) if isinstance(t.get("id"), int)}


def decision(game_type: int, period: int) -> Optional[str]:
    """REG / OT / SO from the last period played (docs/EVALUATION_PLAN.md, NHL plan). None if not interpretable."""
    if period == 3:
        return "REG"
    if period > 3:
        if game_type == 3:
            return "OT"   # playoffs: continuous overtime, no shootout
        return {4: "OT", 5: "SO"}.get(period)   # regular season, preseason and exhibitions: 5 = shootout
    return None


def tipoff_utc(eastern_local: str) -> str:
    """'2024-10-11T22:00:00' (US Eastern wall time, DST-aware) -> canonical UTC."""
    local = datetime.fromisoformat(eastern_local).replace(tzinfo=EASTERN)
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_season(payload: dict, teams: dict[int, TeamRow], retrieved_at: str) -> list[tuple[GameRow, Optional[str], int]]:
    """(game, decision, periods) per listed game. Status rules: final states are final; live states in progress;
    a not-started game whose date is more than UNPLAYED_AFTER before retrieval is canceled (playoffs) or postponed
    (otherwise), never scheduled. Unknown team ids get a placeholder name, never a guess."""
    seen = datetime.strptime(retrieved_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    out = []
    for g in payload.get("data", []):
        gt, state, sched = g.get("gameType"), g.get("gameStateId"), g.get("gameScheduleStateId")
        tip = tipoff_utc(g["easternStartTime"])
        if state in FINAL_STATES:
            status = "final"
        elif state in LIVE_STATES:
            status = "in_progress"
        elif sched == POSTPONED_SCHEDULE_STATE:
            status = "postponed"
        elif datetime.strptime(tip, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) < seen - UNPLAYED_AFTER:
            status = "canceled" if gt == 3 else "postponed"
        else:
            status = "scheduled"
        home = teams.get(g["homeTeamId"]) or TeamRow(f"nhl:{g['homeTeamId']}", "?", f"NHL team {g['homeTeamId']}")
        away = teams.get(g["visitingTeamId"]) or TeamRow(f"nhl:{g['visitingTeamId']}", "?", f"NHL team {g['visitingTeamId']}")
        final = status == "final"
        row = GameRow(game_id=f"nhl:{g['id']}", season=int(str(g["season"])[4:]),
                      season_type=GAME_TYPES.get(gt, "other"), tipoff_at=tip, home=home, away=away,
                      neutral_site=False, status=status,
                      home_score=int(g["homeScore"]) if final else None,
                      away_score=int(g["visitingScore"]) if final else None)
        periods = int(g.get("period") or 0)
        out.append((row, decision(gt, periods) if final else None, periods))
    return out


def load(body: bytes) -> dict:
    return json.loads(body)
