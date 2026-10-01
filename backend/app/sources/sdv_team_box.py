"""Adapter for the sportsdataverse ESPN NBA team box-score release (github.com/sportsdataverse/sportsdataverse-data,
release tag `espn_nba_team_boxscores`, one CSV per season, e.g. team_box_2025.csv = 2024-25).

Repo license MIT (checked 2026-09-30), but the content is ESPN's: personal, non-commercial research only (docs/DATA_SOURCES.md).
Files are rewritten upstream, so every download is verified against the SHA-256 digest GitHub publishes for the asset.
Provides the possession inputs the ESPN scoreboard lacks: turnovers and offensive/defensive rebounds.
"""
import csv
import hashlib
import io
from dataclasses import dataclass
from typing import Optional

SOURCE = "sportsdataverse"
RELEASE_API = "https://api.github.com/repos/sportsdataverse/sportsdataverse-data/releases/tags/espn_nba_team_boxscores"
DOWNLOAD = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_nba_team_boxscores/{name}"
IMPORTER_VERSION = "sdv-team-box-csv-1"
NBA_FRANCHISE_IDS = frozenset(range(1, 31))


class DigestMismatch(RuntimeError):
    pass


def file_name(season: int) -> str:
    return f"team_box_{season}.csv"


# Availability policy is part of the cached request key, so replay applies exactly the policy used at import time.
OBSERVED = "observed"                         # default: usable from our retrieval time
HISTORICAL_BACKFILL = "historical_backfill"   # explicit: games whose results were themselves imported retrospectively
                                              # are assumed available with the result (same assumption as results)
POLICIES = (OBSERVED, HISTORICAL_BACKFILL)


def request_key(season: int, policy: str = OBSERVED) -> str:
    if policy not in POLICIES:
        raise ValueError(f"policy must be one of {POLICIES}")
    return f"file={file_name(season)}&policy={policy}"


def policy_of(key: str) -> str:
    """Policy recorded in a cached request key. Keys without one ('file=team_box_YYYY.csv') are the seven 2026-09-30
    imports of completed seasons 2020-2026, made and stored as historical backfills before the policy was explicit."""
    params = dict(p.split("=", 1) for p in key.split("&"))
    return params.get("policy", HISTORICAL_BACKFILL)


def fetch_season(season: int, timeout: float = 60) -> bytes:
    """Downloads one season CSV and verifies it against the release's published SHA-256 digest."""
    import httpx

    name = file_name(season)
    release = httpx.get(RELEASE_API, headers={"Accept": "application/vnd.github+json"}, timeout=timeout)
    release.raise_for_status()
    asset = next((a for a in release.json().get("assets", []) if a["name"] == name), None)
    if asset is None:
        raise FileNotFoundError(f"{name} is not in the release")
    body = httpx.get(DOWNLOAD.format(name=name), follow_redirects=True, timeout=timeout)
    body.raise_for_status()
    verify(body.content, asset.get("digest"))
    return body.content


def verify(body: bytes, digest: Optional[str]) -> None:
    if not digest or not digest.startswith("sha256:"):
        raise DigestMismatch("release asset has no sha256 digest; refusing an unverifiable file")
    actual = hashlib.sha256(body).hexdigest()
    if actual != digest.split(":", 1)[1]:
        raise DigestMismatch(f"downloaded sha256 {actual} does not match published {digest}")


@dataclass
class BoxRow:
    game_id: str
    team_id: str
    is_home: int
    franchise: bool
    points: Optional[int]
    fga: Optional[int]
    fta: Optional[int]
    oreb: Optional[int]
    dreb: Optional[int]
    tov: Optional[int]


def _int(value: Optional[str]) -> Optional[int]:
    try:
        return int(float(value)) if value not in (None, "", "NA") else None
    except ValueError:
        return None


def parse(body: bytes) -> list[BoxRow]:
    rows = []
    for r in csv.DictReader(io.StringIO(body.decode("utf-8"))):
        team = _int(r.get("team_id"))
        side = r.get("team_home_away")
        if team is None or side not in ("home", "away") or not r.get("game_id"):
            continue
        rows.append(BoxRow(game_id=f"espn:{r['game_id']}", team_id=f"espn:{team}", is_home=int(side == "home"),
                           franchise=team in NBA_FRANCHISE_IDS, points=_int(r.get("team_score")),
                           fga=_int(r.get("field_goals_attempted")), fta=_int(r.get("free_throws_attempted")),
                           oreb=_int(r.get("offensive_rebounds")), dreb=_int(r.get("defensive_rebounds")),
                           tov=_int(r.get("total_turnovers"))))
    return rows
