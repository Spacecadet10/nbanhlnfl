"""Adapter for NHL play-by-play from api-web.nhle.com (free, no key; NHL.com terms: personal, non-commercial research).
Parses unblocked shot attempts for the shot-quality candidate (docs/MODEL_SELECTION.md §12). Probed 2026-10-02: seasons
2015-16 and 2024-25 carry coordinates, zone, shot type, situation code and goalie in net."""
from dataclasses import dataclass
from typing import Optional

SOURCE = "nhl_api_web"
ENDPOINT = "https://api-web.nhle.com/v1/gamecenter/{gid}/play-by-play"
IMPORTER_VERSION = "nhl-api-web-pbp-1"
SHOT_EVENTS = ("shot-on-goal", "missed-shot", "goal")
OBSERVED = "observed"
HISTORICAL_BACKFILL = "historical_backfill"


def api_id(game_id: str) -> str:
    """'nhl:2017020001' -> '2017020001'."""
    return game_id.split(":", 1)[1]


def request_key(game_id: str, policy: str) -> str:
    if policy not in (OBSERVED, HISTORICAL_BACKFILL):
        raise ValueError(policy)
    return f"game={api_id(game_id)}&policy={policy}"


def policy_of(key: str) -> str:
    return dict(p.split("=", 1) for p in key.split("&"))["policy"]


def fetch(game_id: str, timeout: float = 30) -> bytes:
    import httpx
    resp = httpx.get(ENDPOINT.format(gid=api_id(game_id)), timeout=timeout)
    resp.raise_for_status()
    return resp.content


@dataclass(frozen=True)
class Shot:
    idx: int                 # order in the feed (sortOrder)
    period: int
    seconds: int             # elapsed in the period
    side: str                # 'home' / 'away': the shooting team
    event: str
    x: Optional[float]
    y: Optional[float]
    zone: Optional[str]
    shot_type: Optional[str]
    situation: Optional[str]
    goal: bool
    empty_net: bool


def _secs(clock: Optional[str]) -> int:
    if not clock:
        return 0
    m, s = clock.split(":")
    return int(m) * 60 + int(s)


def parse(payload: dict) -> tuple[bool, list[Shot]]:
    """(complete, shots). Complete = the feed reports a finished game. Shootout attempts are excluded."""
    complete = payload.get("gameState") in ("OFF", "FINAL")
    home, away = payload["homeTeam"]["id"], payload["awayTeam"]["id"]
    out = []
    for p in payload.get("plays", []):
        if p.get("typeDescKey") not in SHOT_EVENTS:
            continue
        pd = p.get("periodDescriptor") or {}
        if pd.get("periodType") == "SO":
            continue
        d = p.get("details") or {}
        owner = d.get("eventOwnerTeamId")
        if owner not in (home, away):
            continue
        out.append(Shot(int(p.get("sortOrder", 0)), int(pd.get("number", 0)), _secs(p.get("timeInPeriod")),
                        "home" if owner == home else "away", p["typeDescKey"], d.get("xCoord"), d.get("yCoord"),
                        d.get("zoneCode"), d.get("shotType"), p.get("situationCode"), p["typeDescKey"] == "goal",
                        d.get("goalieInNetId") is None))
    return complete, out
