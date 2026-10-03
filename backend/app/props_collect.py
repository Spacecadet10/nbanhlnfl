"""Forward raw snapshots of ESPN prop lines (docs/MARKETS.md "Player props: prerequisites"). Collection only.

For games starting within the next two hours, the ESPN core `propBets` resource is fetched at most once per game per
PER_GAME_INTERVAL and stored unparsed in the raw cache (source `espn_props`). Nothing here parses props, forecasts or
shows them: the snapshots exist to measure which player/team prop lines are available, and when, before any model is
considered. A 404 (no props listed) is counted, not stored. Failures never affect the odds job that calls this.
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from . import ingest, raw_cache
from .db import transaction
from .sources import espn

SOURCE = "espn_props"
IMPORTER = "espn-props-raw-1"
WINDOW = timedelta(hours=2)
PER_GAME_INTERVAL = timedelta(minutes=55)
MAX_PER_RUN = 20
PATHS = {"nba": "basketball/leagues/nba", "nhl": "hockey/leagues/nhl"}
FMT = "%Y-%m-%dT%H:%M:%SZ"


def endpoint(sport: str, event_id: str) -> str:
    return (f"https://sports.core.api.espn.com/v2/sports/{PATHS[sport]}/events/{event_id}/competitions/{event_id}"
            f"/odds/100/propBets")


def _fetch(url: str, timeout: float = 20) -> Optional[bytes]:
    import httpx
    resp = httpx.get(url, params={"limit": 1000}, headers=espn.HEADERS, timeout=timeout)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.content


def targets(conn: sqlite3.Connection, sport: str, now: datetime) -> list[tuple[str, str]]:
    """(game_id, ESPN event id) for scheduled games starting within two hours, from the latest stored day scoreboards."""
    lo, hi = now.strftime(FMT), (now + WINDOW).strftime(FMT)
    games = {r["game_id"] for r in conn.execute("SELECT game_id FROM games WHERE status='scheduled' AND tipoff_at > ? "
                                                "AND tipoff_at <= ?", (lo, hi))}
    if not games:
        return []
    if sport == "nba":                       # ESPN event ids are the store's ids
        return sorted((g, g.split(":", 1)[1]) for g in games)
    src = espn.NHL_SOURCE
    out = {}
    for r in conn.execute("SELECT r.raw_id FROM raw_responses r WHERE r.source=? ORDER BY r.raw_id DESC LIMIT 4", (src,)):
        quotes, _ = espn.parse_lines(json.loads(raw_cache.load(conn, r["raw_id"])))
        for q in quotes:
            gid = ingest._match_game(conn, "nhl", q)
            if gid in games:
                out.setdefault(gid, q.event_id)
    return sorted(out.items())


def _recent(conn, sport: str, event_id: str, now: datetime) -> bool:
    since = (now - PER_GAME_INTERVAL).strftime(FMT)
    return conn.execute("SELECT 1 FROM raw_fetches f JOIN raw_responses r ON r.raw_id=f.raw_id WHERE r.source=? "
                        "AND r.request_key=? AND f.retrieved_at>=?", (SOURCE, f"{sport}:{event_id}", since)).fetchone() is not None


def collect(conn: sqlite3.Connection, sport: str, *, now: Optional[datetime] = None,
            fetch: Callable[[str], Optional[bytes]] = _fetch) -> dict:
    now = now or datetime.now(timezone.utc)
    out = {"status": "success", "targets": 0, "stored": 0, "not_listed": 0, "recent": 0, "failed": {}}
    try:
        tg = targets(conn, sport, now)
        out["targets"] = len(tg)
        for gid, eid in tg[:MAX_PER_RUN]:
            if _recent(conn, sport, eid, now):
                out["recent"] += 1
                continue
            try:
                body = fetch(endpoint(sport, eid))
            except Exception as exc:
                out["failed"][gid] = f"{type(exc).__name__}: {exc}"[:200]
                continue
            if body is None:
                out["not_listed"] += 1
                continue
            with transaction(conn):
                raw_cache.store_fetch(conn, source=SOURCE, endpoint=endpoint(sport, eid), request_key=f"{sport}:{eid}",
                                      body=body, retrieved_at=now.strftime(FMT), importer_version=IMPORTER)
            out["stored"] += 1
        if out["failed"]:
            out["status"] = "partial"
    except Exception as exc:
        out.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    return out
