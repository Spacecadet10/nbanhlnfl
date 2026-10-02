"""NHL play-by-play shot import and point-in-time delivery (docs/MODEL_SELECTION.md §12)."""
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Iterable, Optional

from . import raw_cache
from .db import transaction, utc_now
from .models.base import PregameGame
from .sources import nhl_pbp

FMT = "%Y-%m-%dT%H:%M:%SZ"
OBSERVED_WINDOW = timedelta(hours=24)     # same rule as results (ingest.OBSERVED_WINDOW)


@dataclass(frozen=True)
class ShotEvent:
    """One retrieval of a game's shots; delivered to models like a box score (backtest.run `box_events`)."""
    game: PregameGame
    available_at: str
    observed_at: str
    fetch_id: int
    shots: tuple


def _policy(retrieved_at: str, tipoff_at: str) -> str:
    gap = datetime.strptime(retrieved_at, FMT) - datetime.strptime(tipoff_at, FMT)
    return nhl_pbp.OBSERVED if gap <= OBSERVED_WINDOW else nhl_pbp.HISTORICAL_BACKFILL


def pending(conn, seasons: Optional[Iterable[int]] = None) -> list:
    from .sports import NHL
    types = NHL.eligible_season_types
    q = (f"SELECT game_id, tipoff_at, result_available_at FROM games WHERE status='final' AND result_available_at IS NOT NULL "
         f"AND season_type IN ({','.join('?' * len(types))}) "
         "AND game_id NOT IN (SELECT game_id FROM nhl_pbp_imports WHERE complete=1)")
    params: list = list(types)
    if seasons:
        seasons = list(seasons)
        q += f" AND season IN ({','.join('?' * len(seasons))})"
        params += seasons
    return list(conn.execute(q + " ORDER BY tipoff_at, game_id", params))


def import_game(conn, g, body: bytes, retrieved_at: str) -> dict:
    policy = _policy(retrieved_at, g["tipoff_at"])
    available = retrieved_at if policy == nhl_pbp.OBSERVED else max(g["result_available_at"], g["tipoff_at"])
    complete, shots = nhl_pbp.parse(json.loads(body))
    with transaction(conn):
        raw_id, fetch_id = raw_cache.store_fetch(conn, source=nhl_pbp.SOURCE, endpoint=nhl_pbp.ENDPOINT,
                                                 request_key=nhl_pbp.request_key(g["game_id"], policy), body=body,
                                                 retrieved_at=retrieved_at, importer_version=nhl_pbp.IMPORTER_VERSION)
        conn.execute("INSERT INTO nhl_pbp_imports VALUES (?,?,?,?,?,?,?,?,?)",
                     (fetch_id, raw_id, g["game_id"], retrieved_at, available, policy, int(complete), len(shots),
                      nhl_pbp.IMPORTER_VERSION))
        conn.executemany("INSERT INTO nhl_shots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         [(fetch_id, s.idx, g["game_id"], s.period, s.seconds, s.side, s.event, s.x, s.y, s.zone,
                           s.shot_type, s.situation, int(s.goal), int(s.empty_net)) for s in shots])
    return {"game_id": g["game_id"], "policy": policy, "complete": complete, "shots": len(shots)}


def import_pending(conn, seasons=None, limit: Optional[int] = None, pause: float = 0.25,
                   fetch: Callable = nhl_pbp.fetch, log: Callable[[str], None] = print) -> dict:
    todo = pending(conn, seasons)[:limit] if limit else pending(conn, seasons)
    out = {"pending": len(todo), "imported": 0, "incomplete": 0, "errors": {}}
    for i, g in enumerate(todo):
        try:
            body = fetch(g["game_id"])
            r = import_game(conn, g, body, utc_now())
            out["imported"] += 1
            out["incomplete"] += not r["complete"]
        except Exception as exc:   # reported; the game stays pending for the next run
            out["errors"][g["game_id"]] = f"{type(exc).__name__}: {exc}"
        if i % 250 == 0:
            log(f"nhl pbp: {i + 1}/{len(todo)} imported {out['imported']} errors {len(out['errors'])}")
        if pause:
            time.sleep(pause)
    return out


def load_events(conn, rows) -> list[ShotEvent]:
    """The first complete import of each game in `rows` (the eligible rows a run may use)."""
    by_id = {r.game.game_id: r.game for r in rows}
    imports = {}
    for imp in conn.execute("SELECT * FROM nhl_pbp_imports WHERE complete=1 ORDER BY available_at, fetch_id"):
        if imp["game_id"] in by_id and imp["game_id"] not in imports:
            imports[imp["game_id"]] = imp
    shots: dict = {}
    for s in conn.execute("SELECT * FROM nhl_shots WHERE fetch_id IN (SELECT fetch_id FROM nhl_pbp_imports WHERE complete=1) "
                          "ORDER BY fetch_id, idx"):
        shots.setdefault(s["fetch_id"], []).append(nhl_pbp.Shot(s["idx"], s["period"], s["seconds"], s["side"], s["event"],
                                                                s["x"], s["y"], s["zone"], s["shot_type"], s["situation"],
                                                                bool(s["goal"]), bool(s["empty_net"])))
    return [ShotEvent(by_id[gid], imp["available_at"], imp["retrieved_at"], imp["fetch_id"], tuple(shots.get(imp["fetch_id"], ())))
            for gid, imp in imports.items()]
