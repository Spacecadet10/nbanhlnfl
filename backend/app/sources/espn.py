"""Adapter for ESPN's public NBA scoreboard JSON (site.api.espn.com).

Unofficial, undocumented endpoint: no API key, no published terms of use or SLA. Suitable for local research;
re-check permitted use before any public deployment. One request per calendar month (dates=YYYYMM), or per
Eastern day (dates=YYYYMMDD) for pregame odds snapshots.
Provides schedule, status, final scores, basic team box stats, and (for upcoming games only) a bookmaker moneyline.
"""
import collections
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

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
    return _parse_scoreboard(payload, prefix="espn:", season_types=SEASON_TYPES, franchise_ids=NBA_FRANCHISE_IDS,
                             box=True)


def parse_nfl_scoreboard(payload: dict) -> list[GameRow]:
    """ESPN NFL scoreboard: schedule, status and final scores only (no box stats, no odds yet). Ties are final results
    with equal scores. Season is ESPN's season year (the year the season starts: 2024 = the 2024-25 season)."""
    return _parse_scoreboard(payload, prefix="nfl:", season_types=NFL_SEASON_TYPES, franchise_ids=NFL_FRANCHISE_IDS,
                             box=False, adjust=_nfl_adjust)


def _nfl_adjust(ev: dict, comp: dict, g: "GameRow") -> None:
    """NFL rules taken from the feed's own fields (checked 2026-11-01):
    - neutral site: ESPN's flag, or a venue outside the USA (London, Germany, Mexico, Brazil, the Bills' Toronto series),
      or a Super Bowl (event note). Before 2014 ESPN leaves the flag false for international games and Super Bowls.
    - a game dated outside August of its season year … March of the next (ESPN lists a 1988 game in its 2004-11 feed)
      is 'other': never a result of that season."""
    country = ((comp.get("venue") or {}).get("address") or {}).get("country")
    super_bowl = any(str(n.get("headline", "")).startswith("Super Bowl") for n in comp.get("notes") or [])
    if (country and country != "USA") or (g.season_type == "postseason" and super_bowl):
        g.neutral_site = True
    year, month = int(g.tipoff_at[:4]), int(g.tipoff_at[5:7])
    if not ((year == g.season and month >= 8) or (year == g.season + 1 and month <= 3)):
        g.season_type = "other"


def _parse_scoreboard(payload: dict, *, prefix: str, season_types: dict, franchise_ids: frozenset,
                      box: bool, adjust: Optional[Callable] = None) -> list[GameRow]:
    games = []
    for ev in payload.get("events", []):
        comp = ev["competitions"][0]
        sides = {c["homeAway"]: c for c in comp["competitors"]}
        if set(sides) != {"home", "away"}:
            continue
        status = STATUS.get(comp["status"]["type"]["name"], "other")
        teams = {k: TeamRow(team_id=f"{prefix}{c['team']['id']}", abbr=c["team"].get("abbreviation", "?"),
                            name=c["team"].get("displayName") or c["team"].get("name", "?"))
                 for k, c in sides.items()}
        final = status == "final"
        season_type = season_types.get(ev["season"].get("type"), "other")
        if season_type != "preseason" and not all(c["team"]["id"] in franchise_ids for c in sides.values()):
            season_type = "other"
        g = GameRow(
            game_id=f"{prefix}{ev['id']}", season=int(ev["season"]["year"]),
            season_type=season_type,
            tipoff_at=normalize_time(ev["date"]), home=teams["home"], away=teams["away"],
            neutral_site=bool(comp.get("neutralSite")), status=status,
            home_score=int(sides["home"]["score"]) if final else None,
            away_score=int(sides["away"]["score"]) if final else None,
        )
        if final and box:
            g.home_stats = _stats(sides["home"])
            g.away_stats = _stats(sides["away"])
        if status == "scheduled" and box:
            g.odds = _odds(comp)
        if adjust:
            adjust(ev, comp, g)
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


# --- Lines with prices (moneyline, point spread / puck line, total) for scheduled games, both sports. ---
NHL_SOURCE = "espn_nhl"   # the NHL store caches ESPN NHL day scoreboards under its own source name
NFL_SOURCE = "espn_nfl"
NFL_ENDPOINT = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
NFL_IMPORTER_VERSION = "espn-nfl-scoreboard-1"
NFL_SEASON_TYPES = {1: "preseason", 2: "regular", 3: "postseason"}
# ESPN ids of the 32 NFL franchises (checked against /teams on 2026-11-01: 1-30, 33 BAL, 34 HOU). 31/32 are the Pro Bowl
# AFC/NFC sides; any game involving a non-franchise team outside the preseason is 'other' (never a team result).
NFL_FRANCHISE_IDS = frozenset(str(i) for i in (*range(1, 31), 33, 34))


def fetch_nfl_month(yyyymm: str, timeout: float = 60) -> bytes:
    """One calendar month of the NFL scoreboard (dates=YYYYMM; a date range is refused by ESPN, checked 2026-11-01)."""
    return _get(month_request(yyyymm)[1], timeout, NFL_ENDPOINT)


def fetch_nfl_day(yyyymmdd: str, timeout: float = 30) -> bytes:
    """One Eastern day of the NFL scoreboard (dates=YYYYMMDD): the frequent game-day status refresh."""
    return _get(day_request(yyyymmdd)[1], timeout, NFL_ENDPOINT)


NHL_ENDPOINT = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard"
LINES_IMPORTER_VERSION = "espn-lines-1"


def fetch_nhl_day(yyyymmdd: str, timeout: float = 30) -> bytes:
    return _get(day_request(yyyymmdd)[1], timeout, NHL_ENDPOINT)


@dataclass
class LineQuote:
    """One bookmaker price pair for a scheduled game. `line` is the home handicap (spread) or the total; None for the
    moneyline (market 'winner'). Sides follow docs/MARKETS.md: a = home / over, b = away / under."""
    event_id: str
    tipoff_at: str
    home_name: str
    away_name: str
    provider: str
    market: str
    price_label: str
    line: Optional[float]
    price_a: int
    price_b: int


def _line_value(text) -> Optional[float]:
    """'-1.5' / '+1.5' / 'o6.5' / 'u221.5' -> float; anything else -> None."""
    if text is None:
        return None
    s = str(text).strip().lower().lstrip("ou")
    try:
        return float(s)
    except ValueError:
        return None


def parse_lines(payload: dict) -> tuple[list[LineQuote], dict]:
    """Every complete price pair on scheduled games. Pairs whose two sides disagree on the line (spread home/away not
    opposite, over/under totals different) or lack a price are skipped and counted, never repaired."""
    quotes, skipped = [], {"incomplete": 0, "inconsistent_line": 0}
    for ev in payload.get("events", []):
        comp = ev["competitions"][0]
        if STATUS.get(comp["status"]["type"]["name"], "other") != "scheduled":
            continue
        sides = {c["homeAway"]: c for c in comp["competitors"]}
        if set(sides) != {"home", "away"}:
            continue
        names = {k: c["team"].get("displayName") or c["team"].get("name", "?") for k, c in sides.items()}
        base = dict(event_id=str(ev["id"]), tipoff_at=normalize_time(ev["date"]),
                    home_name=names["home"], away_name=names["away"])
        for o in comp.get("odds") or []:
            provider = (o.get("provider") or {}).get("name", "unknown")
            for src_label, label in (("open", "open"), ("close", "current")):
                specs = (("winner", o.get("moneyline") or {}, "home", "away"),
                         ("spread", o.get("pointSpread") or {}, "home", "away"),
                         ("total", o.get("total") or {}, "over", "under"))
                for market, block, ka, kb in specs:
                    qa = (block.get(ka) or {}).get(src_label) or {}
                    qb = (block.get(kb) or {}).get(src_label) or {}
                    if not qa and not qb:
                        continue
                    pa, pb = parse_american(qa.get("odds")), parse_american(qb.get("odds"))
                    line = None
                    if market != "winner":
                        la, lb = _line_value(qa.get("line")), _line_value(qb.get("line"))
                        if pa is None or pb is None or la is None or lb is None:
                            skipped["incomplete"] += 1
                            continue
                        if (market == "spread" and abs(la + lb) > 1e-9) or (market == "total" and abs(la - lb) > 1e-9):
                            skipped["inconsistent_line"] += 1
                            continue
                        line = la
                    elif pa is None or pb is None:
                        skipped["incomplete"] += 1
                        continue
                    quotes.append(LineQuote(**base, provider=provider, market=market, price_label=label, line=line,
                                            price_a=pa, price_b=pb))
    return quotes, skipped


@dataclass
class GoalieListing:
    event_id: str
    tipoff_at: str
    home_name: str
    away_name: str
    side: str
    status: str                  # the source's status type ('expected', 'confirmed', ...) or 'none_listed'
    status_label: Optional[str]
    espn_player_id: Optional[str]
    player_name: Optional[str]


def parse_goalies(payload: dict) -> list[GoalieListing]:
    """Probable starting goalies for games that have not started, exactly as listed (one per team; a team without an
    entry is 'none_listed'). Started or finished games are skipped: ESPN overwrites their `probables` afterwards."""
    out = []
    for ev in payload.get("events", []):
        comp = ev["competitions"][0]
        if STATUS.get(comp["status"]["type"]["name"], "other") != "scheduled":
            continue
        sides = {c["homeAway"]: c for c in comp["competitors"]}
        if set(sides) != {"home", "away"}:
            continue
        names = {k: c["team"].get("displayName") or c["team"].get("name", "?") for k, c in sides.items()}
        for side, c in sides.items():
            listed = [p for p in c.get("probables") or [] if p.get("name") == "probableStartingGoalie"]
            base = dict(event_id=str(ev["id"]), tipoff_at=normalize_time(ev["date"]), home_name=names["home"],
                        away_name=names["away"], side=side)
            if not listed:
                out.append(GoalieListing(**base, status="none_listed", status_label=None, espn_player_id=None,
                                         player_name=None))
                continue
            p = listed[0]
            st = p.get("status") or {}
            athlete = p.get("athlete") or {}
            pid = p.get("playerId") or athlete.get("id")
            out.append(GoalieListing(**base, status=str(st.get("type") or st.get("name") or "unknown").lower(),
                                     status_label=st.get("name"), espn_player_id=None if pid is None else str(pid),
                                     player_name=athlete.get("fullName") or athlete.get("displayName")))
    return out


# --- European soccer (data only; app/soccer.py). One ESPN scoreboard per competition, one Eastern day per request
# (dates=YYYYMMDD; a date range returned HTTP 400, checked 2026-11-07). Fields read here were checked on real payloads of
# all seven competitions (2026-11-04/05/07) and on 2024-25 UEFA knockout days with extra time and shoot-outs:
# - `score` is the goal count when play ended: after extra time when extra time was played, never shoot-out kicks;
# - `shootoutScore` is the shoot-out, `series` / `leg` / `aggregateScore` describe a two-legged tie;
# - a competitor's `winner` flag changes meaning by leg: on first legs it marks the match winner (Club Brugge 1-3 Aston
#   Villa, 2025-03-04), on decided ties the team that went through (Atlético 1-0 Real Madrid, 2025-03-12: flag on Real
#   Madrid, who advanced on penalties). It is never read; results come from scores, the qualifier from `series`;
# - the scoreboard has no per-period score fields; goal `details` carry minute strings ("90'+3'" vs "99'") and the event
#   summary has period linescores (S1 report, docs/evidence/soccer-s1-sources-2026-11-07). Deriving a 90-minute score from
#   them is not done here: it needs the validated rule planned for S2/S3, so the regulation result after extra time stays NULL.
SOCCER_SOURCE = "espn_soccer"
SOCCER_IMPORTER_VERSION = "espn-soccer-scoreboard-2"   # 2: ending classified from status + period/clock/minutes/tie (2026-11-07)
SOCCER_ENDPOINT = "https://site.api.espn.com/apis/site/v2/sports/soccer/{competition}/scoreboard"
SOCCER_COMPETITIONS = {
    "eng.1": "Premier League", "esp.1": "La Liga", "ger.1": "Bundesliga", "ita.1": "Serie A", "fra.1": "Ligue 1",
    "uefa.champions": "UEFA Champions League", "uefa.europa": "UEFA Europa League",
    # National teams (migration 0039; app/intl_live.py)
    "uefa.nations": "UEFA Nations League", "concacaf.nations.league": "CONCACAF Nations League",
    "fifa.friendly": "International friendly", "caf.nations_qual": "Africa Cup of Nations qualifying",
    "uefa.euroq": "UEFA European Championship qualifying", "afc.cupq": "AFC Asian Cup qualifying",
    "fifa.worldq.uefa": "World Cup qualifying (UEFA)", "fifa.worldq.conmebol": "World Cup qualifying (CONMEBOL)",
    "fifa.worldq.concacaf": "World Cup qualifying (CONCACAF)", "fifa.worldq.afc": "World Cup qualifying (AFC)",
    "fifa.worldq.caf": "World Cup qualifying (CAF)", "fifa.worldq.ofc": "World Cup qualifying (OFC)",
    "uefa.euro": "UEFA European Championship", "conmebol.america": "Copa América", "caf.nations": "Africa Cup of Nations",
    "afc.asian.cup": "AFC Asian Cup", "concacaf.gold": "CONCACAF Gold Cup", "fifa.world": "FIFA World Cup",
}
# Only these three ESPN statuses are final results (all three observed). How the match was decided comes from the status.
SOCCER_FINAL = {"STATUS_FULL_TIME": "regulation", "STATUS_FINAL_AET": "extra_time", "STATUS_FINAL_PEN": "penalties"}
SOCCER_STATUS = {"STATUS_SCHEDULED": "scheduled", "STATUS_POSTPONED": "postponed", "STATUS_CANCELED": "canceled",
                 "STATUS_ABANDONED": "abandoned", "STATUS_SUSPENDED": "suspended"}


class SoccerPayloadMismatch(ValueError):
    """A scoreboard that is not the requested competition's: never imported under that competition."""


def soccer_request(competition: str, yyyymmdd: str) -> tuple[str, str, dict]:
    """(request_key, endpoint, params) for one competition's scoreboard on one day."""
    if competition not in SOCCER_COMPETITIONS:
        raise ValueError(f"Unknown soccer competition {competition!r}; expected one of {', '.join(SOCCER_COMPETITIONS)}")
    key, params = day_request(yyyymmdd)
    return f"competition={competition}&{key}", SOCCER_ENDPOINT.format(competition=competition), params


def fetch_soccer_day(competition: str, yyyymmdd: str, timeout: float = 30) -> bytes:
    _, endpoint, params = soccer_request(competition, yyyymmdd)
    return _get(params, timeout, endpoint)


@dataclass
class SoccerMatchRow:
    match_id: str
    competition_id: str
    event_id: str
    season: int
    season_type_id: int
    stage: Optional[str]
    stage_label: Optional[str]
    kickoff_at: str
    home: TeamRow
    away: TeamRow
    neutral_site: Optional[bool]
    venue: Optional[str]
    status: str
    source_status: str
    decided_in: Optional[str] = None
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    home_shootout: Optional[int] = None
    away_shootout: Optional[int] = None
    leg: Optional[int] = None
    aggregate_home: Optional[int] = None
    aggregate_away: Optional[int] = None
    qualified_team_id: Optional[str] = None
    note: Optional[str] = None
    decision_note: Optional[str] = None     # why decided_in is 'unknown' (conflicting source fields), else None


def _whole(value) -> Optional[int]:
    """A non-negative whole number from the feed ('2', 2, 2.0), else None (missing or not a count)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return int(f) if f >= 0 and f == int(f) else None


# How a final match ended is decided from ALL of: the status name, `status.period` (ESPN: 1-2 halves, 3-4 extra time,
# 5 shoot-out; some 2005 rows show period 1), the clock, the goal minutes and the competition context. The status alone is
# wrong both ways in real payloads (audit 2026-11-07): STATUS_FULL_TIME on matches that went to extra time (169930 Juventus
# 2-0 Real Madrid 2005: period 4, clock 122'; 416637 Ajax 2-1 Dnipro 2015: period 4, goals 97'/117') and STATUS_FINAL_AET on
# league matches and a 2001 UEFA group match (period 2, clock 0'). Any disagreement gives 'unknown': never a guessed ending,
# never a derived 90-minute score. Derived 90-minute scores stay disabled until a preregistered validation rule exists.
SOCCER_LEAGUES = frozenset({"eng.1", "esp.1", "ger.1", "ita.1", "fra.1"})
REGULATION_MAX_MINUTES = 105       # 90 + generous stoppage; a later clock or goal minute is extra time
_GOAL_CLOCK = __import__("re").compile(r"^(\d+)'(?:\+(\d+)')?$")


FINAL_TYPE_IDS = frozenset({"28", "45", "47"})   # observed ids of STATUS_FULL_TIME, STATUS_FINAL_AET, STATUS_FINAL_PEN


def _extra_time_possible(competition: str, stage: Optional[str], comp: dict) -> bool:
    """Extra time exists only in the second leg of a two-legged tie (leg 2; or a 2-match series that is not leg 1, when a
    response omits the leg) or in a final (slug 'final', no series, no leg). Leagues, group / league phases, first legs and
    unknown stages: never. UEFA slugs are inconsistent between seasons (UEL 2009-2015 group stages are
    '<year>-first-round'), so the tie structure decides."""
    if competition in SOCCER_LEAGUES:
        return False
    series = comp.get("series") or {}
    leg = _whole((comp.get("leg") or {}).get("value"))
    if leg == 2 or (leg is None and _whole(series.get("totalCompetitions")) == 2):
        return True
    return (stage or "").lower() == "final" and not series and not leg


def soccer_goal_minutes(comp: dict, total_goals: int) -> Optional[list[tuple[int, bool]]]:
    """Non-shoot-out goals as (base minute, written with '+' stoppage). Only exact duplicates are dropped -- identical in
    team, clock, type, flags and players -- and only when that makes every team's goals add up to its score (some pre-2010
    rows list a goal twice); distinct goals in the same minute are kept. None when the goal list cannot be used for the
    score: missing, an unparseable clock, or counts that do not match."""
    goals = [g for g in comp.get("details") or [] if g.get("scoringPlay") and not g.get("shootout")]
    if not goals and total_goals:
        return None
    scores = {str((c.get("team") or {}).get("id")): _whole(c.get("score")) for c in comp.get("competitors") or []}

    def adds_up(gs: list) -> bool:
        per = collections.Counter(str((g.get("team") or {}).get("id")) for g in gs)
        return len(gs) == total_goals and all(per.get(t, 0) == n for t, n in scores.items() if n is not None)

    def ident(g: dict) -> tuple:
        return ((g.get("team") or {}).get("id"), (g.get("clock") or {}).get("displayValue"),
                (g.get("type") or {}).get("id"), (g.get("type") or {}).get("text"), bool(g.get("ownGoal")),
                bool(g.get("penaltyKick")), tuple(sorted(str(a.get("id")) for a in g.get("athletesInvolved") or [])))
    if not adds_up(goals):
        unique, seen = [], set()
        for g in goals:
            if ident(g) not in seen:
                seen.add(ident(g))
                unique.append(g)
        if not adds_up(unique):
            return None
        goals = unique
    out = []
    for g in goals:
        m = _GOAL_CLOCK.match((g.get("clock") or {}).get("displayValue") or "")
        if not m:
            return None
        out.append((int(m.group(1)), m.group(2) is not None))
    return out


def _extra_time_minutes(comp: dict, season: int) -> list[str]:
    """Minute strings of any detail entry (goal, card, shoot-out kick; raw, whatever the goal count) that can only fall in
    extra time: base minute > 105; '+' stoppage after any base other than 45 and 90 (e.g. 105'+1'); or 91'-105' without '+'
    in data that writes regulation stoppage as 90'+N' (seasons from 2005, or an event using '+' anywhere). 2001-02 rows
    write regulation stoppage as 91'-95' and are not read that way."""
    clocks = [(g.get("clock") or {}).get("displayValue") or "" for g in comp.get("details") or []]
    parsed = [(c, _GOAL_CLOCK.match(c)) for c in clocks]
    plus_style = season >= 2005 or any(m and m.group(2) for _, m in parsed)
    out = []
    for c, m in parsed:
        if not m:
            continue
        base, plus = int(m.group(1)), m.group(2) is not None
        if base > REGULATION_MAX_MINUTES or (plus and base not in (45, 90) and base > 90) or \
                (not plus and 91 <= base <= REGULATION_MAX_MINUTES and plus_style):
            out.append(c)
    return out


def _clock_minutes(status: dict) -> Optional[int]:
    """Game clock at the end in whole minutes, from `clock` seconds (0 = not reported)."""
    try:
        sec = float(status.get("clock") or 0)
    except (TypeError, ValueError):
        return None
    return int(sec // 60) if sec > 0 else None


def _soccer_ending(name: str, status: dict, comp: dict, competition: str, stage: Optional[str],
                   season: int) -> tuple[str, Optional[str]]:
    """(decided_in, note): the status claim when period, clock, event minutes and shoot-out evidence agree with it in this
    competition context, else ('unknown', what disagreed)."""
    claim = SOCCER_FINAL[name]
    try:
        period = int(status.get("period")) if status.get("period") is not None else None
    except (TypeError, ValueError):
        period = None
    clock = _clock_minutes(status)
    late = _extra_time_minutes(comp, season)
    et_seen = [w for w, hit in (("period %s" % period, period is not None and period >= 3),
                                ("clock %s'" % clock, clock is not None and clock > REGULATION_MAX_MINUTES),
                                ("event minutes %s" % ", ".join(late[:3]), bool(late))) if hit]
    kicks = any(g.get("shootout") for g in comp.get("details") or []) or any(
        _whole(c.get("shootoutScore")) is not None for c in comp.get("competitors") or [])
    if claim == "regulation":
        if et_seen or kicks:
            return "unknown", f"{name} but {', '.join(et_seen + (['shoot-out entries'] if kicks else []))} indicate more than 90 minutes"
        return "regulation", None
    if not _extra_time_possible(competition, stage, comp):
        return "unknown", f"{name} where extra time is not played ({competition}, {stage or 'stage unknown'}, not a second leg or final)"
    if claim == "extra_time" and not et_seen:
        return "unknown", f"{name} without any period, clock or event-minute evidence of extra time"
    if claim == "penalties" and not (et_seen or kicks):
        return "unknown", f"{name} without extra-time or shoot-out evidence"
    return claim, None


def parse_soccer_scoreboard(payload: dict, competition: str) -> list[SoccerMatchRow]:
    """Matches of one competition's ESPN scoreboard. Raises SoccerPayloadMismatch when the payload names another league.
    Scores, shoot-out and aggregate/qualifier only for final matches (live scores are not collected); the leg for every match."""
    slugs = {lg.get("slug") for lg in payload.get("leagues") or []}
    if slugs != {competition}:
        raise SoccerPayloadMismatch(f"Scoreboard is for {sorted(s for s in slugs if s) or 'no league'}, not {competition!r}")
    league_type = ((payload["leagues"][0].get("season") or {}).get("type") or {})
    out = []
    for ev in payload.get("events", []):
        comp = ev["competitions"][0]
        sides = {c.get("homeAway"): c for c in comp.get("competitors", [])}
        if set(sides) != {"home", "away"}:
            continue
        name = comp["status"]["type"]["name"]
        state = comp["status"]["type"].get("state")
        stype = comp["status"]["type"]
        # A final status name counts only on a finished record: state 'post', not completed=false, a final type id.
        finished = (state in (None, "post") and stype.get("completed") is not False
                    and (stype.get("id") is None or str(stype.get("id")) in FINAL_TYPE_IDS))
        status = ("final" if name in SOCCER_FINAL and finished
                  else "other" if name in SOCCER_FINAL
                  else SOCCER_STATUS.get(name) or ("in_progress" if state == "in" else "other"))
        teams = {k: TeamRow(team_id=f"soccer:{c['team']['id']}", abbr=c["team"].get("abbreviation") or "?",
                            name=c["team"].get("displayName") or c["team"].get("name") or "?") for k, c in sides.items()}
        season = ev.get("season") or {}
        series = comp.get("series") or {}
        type_id = int(season.get("type"))
        venue = comp.get("venue") or {}
        city = (venue.get("address") or {}).get("city")
        neutral = comp.get("neutralSite")
        row = SoccerMatchRow(
            match_id=f"soccer:{competition}:{ev['id']}", competition_id=competition, event_id=str(ev["id"]),
            season=int(season["year"]), season_type_id=type_id, stage=season.get("slug"),
            stage_label=series.get("title") or (league_type.get("name") if league_type.get("type") == type_id else None),
            kickoff_at=normalize_time(ev["date"]), home=teams["home"], away=teams["away"],
            neutral_site=None if neutral is None else bool(neutral),
            venue=", ".join(x for x in (venue.get("fullName"), city) if x) or None,
            status=status, source_status=name, leg=_whole((comp.get("leg") or {}).get("value")) or None)
        if status == "final":
            row.home_score, row.away_score = _whole(sides["home"].get("score")), _whole(sides["away"].get("score"))
            if row.home_score is None or row.away_score is None:
                row.status = "other"          # a final without both scores is not a usable result
            else:
                row.decided_in, row.decision_note = _soccer_ending(name, comp.get("status") or {}, comp, competition,
                                                                   row.stage, row.season)
                if row.decided_in == "penalties":
                    h, a = _whole(sides["home"].get("shootoutScore")), _whole(sides["away"].get("shootoutScore"))
                    if h is not None and a is not None and h != a:
                        row.home_shootout, row.away_shootout = h, a
                agg = {str(c.get("id")): c for c in series.get("competitors") or []}
                hid, aid = sides["home"]["team"]["id"], sides["away"]["team"]["id"]
                if hid in agg and aid in agg:
                    h, a = _whole(agg[hid].get("aggregateScore")), _whole(agg[aid].get("aggregateScore"))
                    if h is not None and a is not None:
                        row.aggregate_home, row.aggregate_away = h, a
                    winners = [t for t in (hid, aid) if agg[t].get("winner") is True]
                    if series.get("completed") is True and len(winners) == 1:
                        row.qualified_team_id = f"soccer:{winners[0]}"
                notes = [n.get("headline") for n in comp.get("notes") or [] if n.get("headline")]
                row.note = notes[0] if notes else None
        out.append(row)
    return out
