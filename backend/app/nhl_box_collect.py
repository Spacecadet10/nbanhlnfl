"""NHL player boxes in requests the stats API answers completely (HANDOFF 2026-11-25).

Since 2026-11-18 api.nhle.com/stats/rest returns at most 100 rows whatever `limit` says. nhl_player_ratings.collect_bulk_boxes
asks for every missing game at once (36 skater rows each), so from three missing games on it rejected its own response as
truncated, and the backlog could only grow. That module is hashed into the NHL model definitions (adaptive.definition): an
edit would retire every fit in force. So the collection is done here, with its parser and its file format, two games per
request (72 rows); a truncated pair is asked again one game at a time, and a truncated game is never saved. A file is complete
or absent (written aside, then linked into place), never replaced."""
import contextlib
import hashlib
import json
import os
import time
from pathlib import Path

from .db import utc_now
from .nhl_player_ratings import bulk_players
from .sources import nhl_injuries, nhl_pbp

BATCH = 2
URL = "https://api.nhle.com/stats/rest/en/{group}/summary"


class Truncated(ValueError):
    """The response holds fewer rows than the source says exist."""


def _write_new(path: Path, body: bytes) -> None:
    """The whole file or no file; an existing file is kept as it is."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(body)
        with contextlib.suppress(FileExistsError):
            os.link(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _fetch(client, batch: list, delay: float) -> dict:
    """{'skater'|'goalie': (rows, response)} for exactly these games, or an error: never an incomplete answer."""
    out = {}
    for group in ("skater", "goalie"):
        params = {"isAggregate": "false", "isGame": "true", "start": 0, "limit": -1,
                  "cayenneExp": "gameId in (" + ",".join(nhl_pbp.api_id(g["game_id"]) for g in batch) + ")"}
        time.sleep(max(1., delay))
        resp = client.get(URL.format(group=group), params=params)
        resp.raise_for_status()
        payload = resp.json()
        if len(payload["data"]) != payload["total"]:
            raise Truncated(f"truncated NHL stats response: {len(payload['data'])} of {payload['total']} {group} rows")
        out[group] = (payload["data"], resp)
    return out


def _save(directory: Path, batch: list, names: dict, got: dict, out: dict) -> None:
    first, last = nhl_pbp.api_id(batch[0]["game_id"]), nhl_pbp.api_id(batch[-1]["game_id"])
    raw_files, digests, endpoints = [], [], []
    for group, (_, resp) in got.items():
        sha = hashlib.sha256(resp.content).hexdigest()
        raw_files.append(f"bulk-{group}-{first}-{last}-{sha}.raw"); digests.append(sha); endpoints.append(str(resp.url))
    teams = {}
    for game in batch:
        try:
            teams[game["game_id"]] = bulk_players(game, got["skater"][0], got["goalie"][0])
        except Exception as exc:        # this game is not complete at the source: the others are kept
            _failed(out, [game], exc)
    if not teams:
        return
    for name, (_, resp) in zip(raw_files, got.values()):
        _write_new(directory / name, resp.content)
    for game in batch:
        if game["game_id"] not in teams:
            continue
        try:
            t = teams[game["game_id"]]
            meta = {"game_id": game["game_id"], "home_team_id": game["home_team_id"], "away_team_id": game["away_team_id"],
                    "teams": t, "names": {i: names[i] for i in t}, "raw_files": raw_files, "raw_hashes": digests,
                    "retrieved_at": utc_now(), "endpoints": endpoints,
                    "availability_basis": "historical per-game stats delivered with stored result; publication time assumed"}
            _write_new(directory / f"{nhl_pbp.api_id(game['game_id'])}.json", json.dumps(meta).encode())
            out["stored"] += 1
        except Exception as exc:
            _failed(out, [game], exc)


def _failed(out: dict, games: list, exc: Exception) -> None:
    out["failed"] += len(games)
    out["errors"] += [{"game_id": g["game_id"], "error": f"{type(exc).__name__}: {exc}"} for g in games]


def collect(conn, start: int, end: int, *, delay: float = 1., directory=None) -> dict:
    """Every final of seasons start..end without a box file. `missing`: games still without one afterwards; `not_requested`:
    the part of them left because the source refused (403/429). Reads the store; writes only the box folder."""
    import httpx
    directory = Path(directory or nhl_injuries.root() / "boxes")
    directory.mkdir(parents=True, exist_ok=True)
    games = [dict(g) for g in conn.execute("SELECT game_id,season,home_team_id,away_team_id FROM games WHERE status='final' AND "
                                           "season BETWEEN ? AND ? AND season_type IN ('regular','postseason') "
                                           "ORDER BY game_id", (start, end))]
    names = {r["team_id"]: r["name"] for r in conn.execute("SELECT team_id,name FROM teams")}

    def absent(g):
        return not (directory / f"{nhl_pbp.api_id(g['game_id'])}.json").exists()
    pending = [g for g in games if absent(g)]
    out = {"stored": 0, "cached": len(games) - len(pending), "failed": 0, "missing": 0, "not_requested": 0, "errors": []}
    todo = [pending[i:i + BATCH] for i in range(0, len(pending), BATCH)]
    with httpx.Client(timeout=60) as client:
        while todo:
            batch = todo.pop(0)
            try:
                _save(directory, batch, names, _fetch(client, batch, delay), out)
            except Truncated as exc:
                if len(batch) > 1:
                    todo[:0] = [[g] for g in batch]
                else:
                    _failed(out, batch, exc)
            except Exception as exc:
                _failed(out, batch, exc)
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (403, 429):
                    out["not_requested"] = sum(len(b) for b in todo)
                    break
    out["missing"] = sum(absent(g) for g in pending)
    run = directory / "last-bulk-run.json"
    tmp = run.with_name(f".{run.name}.tmp")
    tmp.write_text(json.dumps(out, indent=2))
    tmp.replace(run)
    return out
