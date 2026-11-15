"""Goalie inputs for NHL candidates: goals saved above expected from our own shot xG (docs/CANDIDATE_POOL.md §4).

Goalie identities come from the play-by-play already cached for the shot import (`goalieInNetId`, roster names); no new
requests. Two different questions are kept apart:
- *Ratings* use only shots of games whose shots and result were delivered before the forecast (backtest.run box rule).
- *Who starts* is not known from history before a game. Retrospective studies use the goalie who actually started
  (first goalie in net), which assumes the pregame listing was right — an optimistic assumption, labelled in every report.
  Live forecasts use only the latest ESPN probable/confirmed listing retrieved before the forecast (`goalie_snapshots`).
"""
import gzip
import json
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Optional

from .. import config, raw_cache
from ..models.hockey_xg import strength, vectors

PRIOR_XG = 25.0          # shrinkage: a goalie's rating is (xGA − GA) / (xGA + PRIOR_XG); declared, not tuned
SEASON_DECAY = 0.5       # earlier seasons' totals are halved at each new season (declared, not tuned)
PARSER_VERSION = "goalies-v1"


def _cache_path() -> Path:
    return config.data_dir() / "research" / "candidates" / f"{PARSER_VERSION}.json.gz"


def _name(first, last) -> str:
    raw = f"{(first or {}).get('default', '')} {(last or {}).get('default', '')}".strip().lower()
    return "".join(c for c in unicodedata.normalize("NFKD", raw) if not unicodedata.combining(c))


def normalise(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    raw = name.strip().lower()
    return "".join(c for c in unicodedata.normalize("NFKD", raw) if not unicodedata.combining(c))


def parse(payload: dict) -> dict:
    """{shot sortOrder: goalie id} for attempts with a goalie in net, the starter per side (first goalie faced), names."""
    home, away = payload["homeTeam"]["id"], payload["awayTeam"]["id"]
    shots, starter = {}, {}
    for p in payload.get("plays", []):
        d = p.get("details") or {}
        gid = d.get("goalieInNetId")
        owner = d.get("eventOwnerTeamId")
        if gid is None or owner not in (home, away):
            continue
        if (p.get("periodDescriptor") or {}).get("periodType") == "SO":
            continue
        defending = "away" if owner == home else "home"
        starter.setdefault(defending, gid)
        shots[int(p.get("sortOrder", 0))] = gid
    names = {s["playerId"]: _name(s.get("firstName"), s.get("lastName"))
             for s in payload.get("rosterSpots", []) if s.get("positionCode") == "G"}
    return {"shots": shots, "starter": starter, "names": names}


def load(conn) -> dict:
    """fetch_id -> parsed goalie detail for every complete import, cached on disk and extended incrementally."""
    path = _cache_path()
    cache = {}
    if path.exists():
        with gzip.open(path, "rt") as fh:
            cache = {int(k): v for k, v in json.load(fh).items()}
    todo = [r for r in conn.execute("SELECT fetch_id, raw_id FROM nhl_pbp_imports WHERE complete=1 ORDER BY fetch_id")
            if r["fetch_id"] not in cache]
    for r in todo:
        d = parse(json.loads(raw_cache.load(conn, r["raw_id"])))
        cache[r["fetch_id"]] = {"shots": {str(k): v for k, v in d["shots"].items()},
                                "starter": d["starter"], "names": {str(k): v for k, v in d["names"].items()}}
    if todo:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with gzip.open(tmp, "wt") as fh:
            json.dump({str(k): v for k, v in cache.items()}, fh)
        tmp.replace(path)
    return cache


class GoalieInputs:
    """Point-in-time goalie ratings. `update_box` receives a game's delivered shots with the xG fit in force for its
    season (the same fit `XgInputs` used), so a game's own shots never rate its own goalie before it is played."""

    def __init__(self, xg, detail: dict):
        self.xg = xg                       # XgInputs: the season's xG fit
        self.detail = detail               # fetch_id -> parsed goalie detail
        self.xga: dict = defaultdict(float)
        self.ga: dict = defaultdict(float)
        self.season: Optional[int] = None
        self.names: dict = {}              # normalised name -> goalie id (every goalie seen so far)

    def _new_season(self, season: int) -> None:
        if self.season is not None and season > self.season:
            for k in list(self.xga):
                self.xga[k] *= SEASON_DECAY
                self.ga[k] *= SEASON_DECAY
        self.season = season if self.season is None else max(self.season, season)

    def update_box(self, e) -> None:
        det = self.detail.get(e.fetch_id)
        if det is None:
            return
        for gid, name in det["names"].items():
            self.names.setdefault(name, int(gid))
        model = self.xg.fits.get(e.game.season)
        if model is None:
            return
        self._new_season(e.game.season)
        vs = vectors(e.shots)
        if not vs:
            return
        ps = model.predict_proba([v for _, v in vs])[:, 1]
        for (s, _), p in zip(vs, ps):
            goalie = det["shots"].get(str(s.idx))
            if goalie is None or strength(s.situation, s.side) is None:
                continue
            self.xga[goalie] += float(p)
            self.ga[goalie] += float(s.goal)

    def rating(self, goalie: Optional[int]) -> float:
        """Goals saved above expected per expected goal faced, shrunk toward 0 (league average). Unknown goalie: 0."""
        if goalie is None:
            return 0.0
        return (self.xga.get(goalie, 0.0) - self.ga.get(goalie, 0.0)) / (self.xga.get(goalie, 0.0) + PRIOR_XG)

    def experience(self, goalie: Optional[int]) -> float:
        return 0.0 if goalie is None else self.xga.get(goalie, 0.0)


def actual_starters(conn, detail: dict) -> dict:
    """game_id -> {"home": id, "away": id} from the first complete import (retrospective studies only; postgame fact)."""
    out = {}
    for r in conn.execute("SELECT game_id, fetch_id FROM nhl_pbp_imports WHERE complete=1 ORDER BY available_at, fetch_id"):
        det = detail.get(r["fetch_id"])
        if det and r["game_id"] not in out:
            out[r["game_id"]] = {k: int(v) for k, v in det["starter"].items()}
    return out


def listed_starters(conn, game_ids, at: str, names: dict) -> dict:
    """game_id -> {side: (goalie id or None, status, retrieved_at, name)} from the latest listing retrieved at or before
    `at`. A name that matches no goalie seen in the play-by-play gives id None (rated as league average)."""
    out = {}
    for gid in game_ids:
        sides = {}
        for side in ("home", "away"):
            r = conn.execute("SELECT * FROM goalie_snapshots WHERE game_id=? AND side=? AND retrieved_at<=? "
                             "ORDER BY retrieved_at DESC, snapshot_id DESC LIMIT 1", (gid, side, at)).fetchone()
            if r is None or r["source_status"] == "none_listed":
                sides[side] = (None, "none_listed" if r else "no_snapshot", r["retrieved_at"] if r else None, None)
            else:
                sides[side] = (names.get(normalise(r["player_name"])), r["source_status"], r["retrieved_at"], r["player_name"])
        out[gid] = sides
    return out
