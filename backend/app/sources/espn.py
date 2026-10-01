"""Adapter for ESPN's public NBA scoreboard JSON (site.api.espn.com).

Unofficial, undocumented endpoint: no API key, no published terms of use or SLA. Suitable for local research;
re-check permitted use before any public deployment. One request per calendar month (dates=YYYYMM), or per
Eastern day (dates=YYYYMMDD) for pregame odds snapshots.
Provides schedule, status, final scores, basic team box stats, and (for upcoming games only) a bookmaker moneyline.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from ..odds import devig_two_way, parse_american

SOURCE = "espn"
ENDPOINT = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
IMPORTER_VERSION = "espn-scoreboard-2"
# Default python-httpx User-Agent. A custom UA string was rejected with 403 (2026-09-30); no browser impersonation.
HEADERS = {"Accept": "application/json"}

SEASON_TYPES = {1: "preseason", 2: "regular", 3: "postseason", 5: "play_in"}
# ESPN ids of the 30 NBA franchises (checked against /teams on 2026-09-30). Competitive games involving any
# other team (All-Star weekend events such as Team LeBron or Rising Stars) are classified as 'other'.
NBA_FRANCHISE_IDS = frozenset(str(i) for i in range(1, 31))
STATUS = {
    "STATUS_SCHEDULED": "scheduled", "STATUS_FINAL": "final", "STATUS_POSTPONED": "postponed",
    "STATUS_CANCELED": "canceled", "STATUS_IN_PROGRESS": "in_progress", "STATUS_HALFTIME": "in_progress",
    "STATUS_END_PERIOD": "in_progress", "STATUS_DELAYED": "in_progress",
}
STAT_FIELDS = {"fieldGoalsMade": "fgm", "fieldGoalsAttempted": "fga", "threePointFieldGoalsMade": "fg3m",
               "threePointFieldGoalsAttempted": "fg3a", "freeThrowsMade": "ftm", "freeThrowsAttempted": "fta",
               "rebounds": "rebounds", "assists": "assists"}


def month_request(yyyymm: str) -> tuple[str, dict]:
    if len(yyyymm) != 6 or not yyyymm.isdigit():
        raise ValueError(f"Expected YYYYMM, got {yyyymm!r}")
    params = {"dates": yyyymm, "limit": "1000"}
    return "&".join(f"{k}={v}" for k, v in sorted(params.items())), params


def day_request(yyyymmdd: str) -> tuple[str, dict]:
    """One Eastern calendar day (≈10–40 KB): used for frequent pregame odds snapshots."""
    if len(yyyymmdd) != 8 or not yyyymmdd.isdigit():
        raise ValueError(f"Expected YYYYMMDD, got {yyyymmdd!r}")
    params = {"dates": yyyymmdd}
    return "&".join(f"{k}={v}" for k, v in sorted(params.items())), params


def _get(params: dict, timeout: float, endpoint: str = ENDPOINT) -> bytes:
    import httpx  # imported lazily so parsing/tests never need the network stack

    resp = httpx.get(endpoint, params=params, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    return resp.content


def fetch_month(yyyymm: str, timeout: float = 60) -> bytes:
    return _get(month_request(yyyymm)[1], timeout)


def fetch_day(yyyymmdd: str, timeout: float = 30) -> bytes:
    return _get(day_request(yyyymmdd)[1], timeout)


@dataclass
class TeamRow:
    team_id: str
    abbr: str
    name: str


@dataclass
class OddsRow:
    provider: str
    price_label: str
    home_american: int
    away_american: int
    home_prob_devig: float


@dataclass
class GameRow:
    game_id: str
    season: int
    season_type: str
    tipoff_at: str
    home: TeamRow
    away: TeamRow
    neutral_site: bool
    status: str
    home_score: Optional[int]
    away_score: Optional[int]
    home_stats: dict = field(default_factory=dict)
    away_stats: dict = field(default_factory=dict)
    odds: list[OddsRow] = field(default_factory=list)


def normalize_time(value: str) -> str:
    """ESPN uses '2024-01-15T18:00Z' (no seconds); store canonical 'YYYY-MM-DDTHH:MM:SSZ'."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_scoreboard(payload: dict) -> list[GameRow]:
    games = []
    for ev in payload.get("events", []):
        comp = ev["competitions"][0]
        sides = {c["homeAway"]: c for c in comp["competitors"]}
        if set(sides) != {"home", "away"}:
            continue
        status = STATUS.get(comp["status"]["type"]["name"], "other")
        teams = {k: TeamRow(team_id=f"espn:{c['team']['id']}", abbr=c["team"].get("abbreviation", "?"),
                            name=c["team"].get("displayName") or c["team"].get("name", "?"))
                 for k, c in sides.items()}
        final = status == "final"
        season_type = SEASON_TYPES.get(ev["season"].get("type"), "other")
        if season_type != "preseason" and not all(c["team"]["id"] in NBA_FRANCHISE_IDS for c in sides.values()):
            season_type = "other"
        g = GameRow(
            game_id=f"espn:{ev['id']}", season=int(ev["season"]["year"]),
            season_type=season_type,
            tipoff_at=normalize_time(ev["date"]), home=teams["home"], away=teams["away"],
            neutral_site=bool(comp.get("neutralSite")), status=status,
            home_score=int(sides["home"]["score"]) if final else None,
            away_score=int(sides["away"]["score"]) if final else None,
        )
        if final:
            g.home_stats = _stats(sides["home"])
            g.away_stats = _stats(sides["away"])
        if status == "scheduled":
            g.odds = _odds(comp)
        games.append(g)
    return games


def _stats(competitor: dict) -> dict:
    out = {"points": int(competitor["score"])}
    for s in competitor.get("statistics", []):
        col = STAT_FIELDS.get(s.get("name"))
        if col:
            try:
                out[col] = int(float(s["displayValue"]))
            except (KeyError, ValueError):
                pass
    return out


def _odds(comp: dict) -> list[OddsRow]:
    """Moneylines for games not yet started. ESPN's 'close' on a pre-game line is the current price, so it is
    stored as 'current'; lines on finished games are skipped because they are not a verified close."""
    rows = []
    for o in comp.get("odds") or []:
        ml = o.get("moneyline") or {}
        provider = (o.get("provider") or {}).get("name", "unknown")
        for src_label, label in (("open", "open"), ("close", "current")):
            h = parse_american(((ml.get("home") or {}).get(src_label) or {}).get("odds"))
            a = parse_american(((ml.get("away") or {}).get(src_label) or {}).get("odds"))
            if h is not None and a is not None:
                rows.append(OddsRow(provider, label, h, a, round(devig_two_way(h, a)[0], 6)))
    return rows


# --- Game summary: per-game team box score with the possession inputs the scoreboard lacks (in-season refresh). ---
SUMMARY_SOURCE = "espn_summary"   # separate raw-cache source: these responses are not scoreboards
SUMMARY_ENDPOINT = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary"
SUMMARY_IMPORTER_VERSION = "espn-summary-box-1"


def summary_request(game_id: str) -> tuple[str, dict]:
    event = game_id.split(":", 1)[1] if game_id.startswith("espn:") else game_id
    if not event.isdigit():
        raise ValueError(f"Expected an ESPN event id, got {game_id!r}")
    return f"event={event}", {"event": event}


def fetch_summary(game_id: str, timeout: float = 30) -> bytes:
    return _get(summary_request(game_id)[1], timeout, SUMMARY_ENDPOINT)


@dataclass
class SummaryLine:
    team_id: str
    is_home: int
    points: Optional[int]
    fga: Optional[int]
    fta: Optional[int]
    oreb: Optional[int]
    dreb: Optional[int]
    tov: Optional[int]


@dataclass
class SummaryBox:
    game_id: str
    completed: bool
    lines: list[SummaryLine]


def _stat_int(value: Optional[str], part: Optional[int] = None) -> Optional[int]:
    """'12' -> 12; with part=1, '41-88' -> 88. Missing or malformed values are None, never guessed."""
    try:
        if value is None:
            return None
        return int(float(value.split("-")[part] if part is not None else value))
    except (ValueError, IndexError):
        return None


def parse_summary_box(payload: dict) -> SummaryBox:
    """Team lines from a summary response. `completed` is ESPN's own final flag; callers must not use the box of a
    game that is not completed. tov is totalTurnovers (player + team), matching the backfill source."""
    comp = ((payload.get("header") or {}).get("competitions") or [{}])[0]
    scores = {c.get("id"): _stat_int(c.get("score")) for c in comp.get("competitors") or []}
    completed = bool(((comp.get("status") or {}).get("type") or {}).get("completed"))
    lines = []
    for t in (payload.get("boxscore") or {}).get("teams") or []:
        team, side = (t.get("team") or {}).get("id"), t.get("homeAway")
        if team is None or side not in ("home", "away"):
            continue
        stats = {x.get("name"): x.get("displayValue") for x in t.get("statistics") or []}
        lines.append(SummaryLine(
            team_id=f"espn:{team}", is_home=int(side == "home"), points=scores.get(team),
            fga=_stat_int(stats.get("fieldGoalsMade-fieldGoalsAttempted"), 1),
            fta=_stat_int(stats.get("freeThrowsMade-freeThrowsAttempted"), 1),
            oreb=_stat_int(stats.get("offensiveRebounds")), dreb=_stat_int(stats.get("defensiveRebounds")),
            tov=_stat_int(stats.get("totalTurnovers"))))
    return SummaryBox(game_id=f"espn:{(payload.get('header') or {}).get('id')}", completed=completed, lines=lines)
