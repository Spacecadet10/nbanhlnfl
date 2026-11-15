"""NHL shot-quality inputs and the `hockey_logistic_xg` candidate (docs/MODEL_SELECTION.md §12).

Shots reach this module only through `update_box` (backtest.run delivers a game's shots once both they and its result are
available at the cutoff). A season's shots are valued by an expected-goals fit on earlier seasons' delivered shots only.
"""
import math
from collections import defaultdict, deque
from typing import Optional

from .base import PregameGame

NET_X = 89.0
SHOT_TYPES = ("wrist", "snap", "slap", "backhand", "tip-in", "deflected", "wrap-around")
REBOUND_SECONDS = 3
WINDOW = 20
XG_C = 1.0


def strength(situation: Optional[str], side: str) -> Optional[str]:
    """'ev5' (5v5, both goalies in), 'ev' (other even), 'pp' (shooter has more skaters), 'sh'; None when unknown."""
    if not situation or len(situation) != 4 or not situation.isdigit():
        return None
    away_g, away_s, home_s, home_g = (int(c) for c in situation)
    own, opp = (home_s, away_s) if side == "home" else (away_s, home_s)
    if own == opp:
        return "ev5" if own == 5 and away_g == 1 and home_g == 1 else "ev"
    return "pp" if own > opp else "sh"


def shot_vector(s, rebound: bool) -> Optional[list[float]]:
    if s.x is None or s.y is None or s.zone is None:
        return None
    sign = 1.0 if s.x >= 0 else -1.0
    net = sign * NET_X if s.zone == "O" else -sign * NET_X
    dx, dy = abs(net - s.x), abs(s.y)
    dist = math.hypot(dx, dy)
    angle = math.degrees(math.atan2(dy, dx)) if dx or dy else 0.0
    st = strength(s.situation, s.side)
    return [dist, angle, dist * angle / 100.0, *[float(s.shot_type == t) for t in SHOT_TYPES], float(rebound),
            float(st == "pp"), float(st == "sh")]


def vectors(shots) -> list[tuple]:
    """(shot, vector) for every non-empty-net attempt with coordinates, with the rebound flag from the same team's previous
    attempt in the same period."""
    out, last = [], {}
    for s in sorted(shots, key=lambda s: s.idx):
        prev = last.get(s.side)
        rebound = prev is not None and prev.period == s.period and 0 <= s.seconds - prev.seconds <= REBOUND_SECONDS
        last[s.side] = s
        if s.empty_net:
            continue
        v = shot_vector(s, rebound)
        if v is not None:
            out.append((s, v))
    return out


class XgInputs:
    """Lagged team shot-quality inputs: last WINDOW valued games per team, carried across seasons."""

    def __init__(self, team_of=lambda t: t):
        self.team_of = team_of
        self.shots: dict[int, list] = defaultdict(list)   # season -> [(vector, goal)] of delivered games
        self.fits: dict[int, object] = {}
        self.games: dict[str, deque] = defaultdict(lambda: deque(maxlen=WINDOW))
        self.valued = self.unvalued = 0
        self.lines: set = set()                            # games whose shots were delivered (stale-input check)

    def _fit(self, season: int):
        if season not in self.fits:
            train = [x for s, xs in self.shots.items() if s < season for x in xs]
            model = None
            if train and len({g for _, g in train}) == 2:
                from sklearn.linear_model import LogisticRegression
                model = LogisticRegression(C=XG_C, max_iter=2000)
                model.fit([v for v, _ in train], [int(g) for _, g in train])
            self.fits[season] = model
        return self.fits[season]

    def update_box(self, e) -> None:
        g = e.game
        self.lines.add(g.game_id)
        vs = vectors(e.shots)
        model = self._fit(g.season)        # fitted before this game's own shots are added
        self.shots[g.season].extend((v, s.goal) for s, v in vs)
        if model is None:
            self.unvalued += 1
            return
        xg = model.predict_proba([v for _, v in vs])[:, 1] if vs else []
        tot = {side: {"ev5": 0.0, "pp": 0.0, "sh": 0.0} for side in ("home", "away")}
        for (s, _), p in zip(vs, xg):
            st = strength(s.situation, s.side)
            if st in ("ev5", "pp", "sh"):
                tot[s.side][st] += float(p)
        for side, opp, team in (("home", "away", g.home_team_id), ("away", "home", g.away_team_id)):
            self.games[self.team_of(team)].append((tot[side]["ev5"], tot[opp]["ev5"], tot[side]["pp"], tot[opp]["pp"]))
        self.valued += 1

    def _team(self, team: str) -> tuple[float, float]:
        d = self.games[self.team_of(team)]
        if not d:
            return 0.5, 0.0
        f, a = sum(x[0] for x in d), sum(x[1] for x in d)
        share = f / (f + a) if f + a > 0 else 0.5
        return share, (sum(x[2] for x in d) - sum(x[3] for x in d)) / len(d)

    def features(self, g: PregameGame) -> dict:
        hs, hn = self._team(g.home_team_id)
        as_, an = self._team(g.away_team_id)
        return {"ev_xg_share_diff": hs - as_, "st_xg_net_diff": hn - an}


XG_FEATURES = ("ev_xg_share_diff", "st_xg_net_diff")


def hockey_logistic_xg(elo_cfg, goals_cfg, rating_key: Optional[dict] = None):
    from dataclasses import asdict

    from .hockey import LEARNED_FEATURES, LEARNED_SCALED, HockeyFeatures
    from .logistic import LogisticElo

    class HockeyLogisticXg(LogisticElo):
        name = "hockey_logistic_xg"
        feature_names = LEARNED_FEATURES + XG_FEATURES
        scaled_names = LEARNED_SCALED + XG_FEATURES

        def _features(self, g):
            return {**self.features.compute(g), **self.xg.features(g)}

        def update_box(self, e) -> None:
            self.xg.update_box(e)

        def config(self) -> dict:
            return {"l2": self.l2, "features": "+".join(self.feature_names), "xg": f"window{WINDOW}-c{XG_C}-rebound{REBOUND_SECONDS}",
                    "elo": "k{k}-h{home_advantage}-c{carry}-mov{mov}".format(**asdict(elo_cfg)),
                    "goals": "eta{eta}-c{carry}".format(**asdict(goals_cfg))}
    m = HockeyLogisticXg(elo_cfg)   # type: ignore[arg-type]
    m.features = HockeyFeatures(elo_cfg, goals_cfg, rating_key)
    m.xg = XgInputs(m.features.elo._team)
    return m
