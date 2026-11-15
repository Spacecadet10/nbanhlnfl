"""One point-in-time feature walk per sport, shared by every candidate learner (docs/CANDIDATE_POOL.md §3).

`Recorder` is a walk model for backtest.run: it captures each game's features when the game is forecast (state built only
from results and box/shot events available at that cutoff) and its labels when the result is delivered. Learners then fit
on rows whose results were available before the fit's own cutoff, so an offline fit equals the refit the walk would make.
"""
import hashlib
import json
from dataclasses import dataclass, field
from typing import Optional

from ..models.efficiency import EfficiencyRatings
from ..models.logistic import TeamFeatures
from ..models.score_candidates import NBA_FEATURES, _NbaRefit

NHL_BASE = ("elo_diff", "xg_diff", "gd10_diff", "rest_diff", "b2b_home", "b2b_away", "neutral",
            "lam_home", "lam_away", "ev_xg_share_diff", "st_xg_net_diff")
NHL_GOALIE = ("goalie_home", "goalie_away", "goalie_diff", "goalie_exp_home", "goalie_exp_away")
FEATURE_SETS = {
    "nba_base": NBA_FEATURES,
    "nhl_base": NHL_BASE,
    "nhl_goalie": NHL_BASE + NHL_GOALIE,
}


@dataclass
class Row:
    game_id: str
    season: int
    tipoff_at: str
    cutoff_at: str
    x: dict
    result_available_at: Optional[str] = None
    y: Optional[dict] = None                 # labels (used for training only once result_available_at <= the fit cutoff)
    meta: dict = field(default_factory=dict)  # input provenance, e.g. which goalie listing was used


class NbaRecorder:
    name = "features:nba"

    def __init__(self, elo_cfg):
        self.team = TeamFeatures(elo_cfg)
        self.eff = EfficiencyRatings()
        self.rows: dict[str, Row] = {}

    def predict(self, g) -> float:
        if g.game_id not in self.rows:
            self.rows[g.game_id] = Row(g.game_id, g.season, g.tipoff_at, "", dict(zip(NBA_FEATURES, _NbaRefit._vector(self, g))))
        return 0.5

    def update(self, r) -> None:
        self.team.update(r)

    def update_box(self, e) -> None:
        self.eff.update_box(e)


class NhlRecorder:
    name = "features:nhl"

    def __init__(self, elo_cfg, goals_cfg, rating_key, goalie_detail: Optional[dict] = None, starters=None):
        from ..models.hockey import HockeyFeatures
        from ..models.hockey_xg import XgInputs
        from .goalies import GoalieInputs
        self.team = HockeyFeatures(elo_cfg, goals_cfg, rating_key)
        self.xg = XgInputs(self.team.elo._team)
        self.goalies = GoalieInputs(self.xg, goalie_detail) if goalie_detail is not None else None
        self.starters = starters            # callable(game) -> ({"home": id|None, "away": id|None}, provenance dict)
        self.rows: dict[str, Row] = {}

    def predict(self, g) -> float:
        if g.game_id in self.rows:
            return 0.5
        lh, la = self.team.goals.rates(g)
        x = {**self.team.compute(g), "lam_home": lh, "lam_away": la, **self.xg.features(g)}
        meta = {"xg_ready": self.xg.valued > 0}
        if self.goalies is not None:
            ids, prov = self.starters(g) if self.starters else ({}, {"goalie_source": "none"})
            h, a = ids.get("home"), ids.get("away")
            x.update({"goalie_home": self.goalies.rating(h), "goalie_away": self.goalies.rating(a),
                      "goalie_exp_home": self.goalies.experience(h), "goalie_exp_away": self.goalies.experience(a)})
            x["goalie_diff"] = x["goalie_home"] - x["goalie_away"]
            meta.update(prov)
        self.rows[g.game_id] = Row(g.game_id, g.season, g.tipoff_at, "", x, meta=meta)
        return 0.5

    def update(self, r) -> None:
        self.team.update(r)

    def update_box(self, e) -> None:
        self.xg.update_box(e)
        if self.goalies is not None:
            self.goalies.update_box(e)


def walk(recorder, rows, events, deliver_until: Optional[str] = None) -> dict[str, Row]:
    """Runs the recorder over `rows` (point-in-time) and attaches cutoff and result availability to every row."""
    from .. import backtest
    out = backtest.run(recorder, rows, events, deliver_until=deliver_until)
    by = {r.game.game_id: r for r in rows}
    for p in out:
        row = recorder.rows[p["game_id"]]
        row.cutoff_at = p["cutoff_at"]
        src = by[p["game_id"]]
        row.result_available_at = src.result_available_at
        res = src.result
        row.y = {"home_won": res.home_score > res.away_score, "margin": res.home_score - res.away_score,
                 "total": res.home_score + res.away_score, "home": res.home_score, "away": res.away_score,
                 "regulation": getattr(res, "regulation", None), "decision": getattr(res, "decision", None)}
    return recorder.rows


def table_version(rows: dict) -> str:
    h = hashlib.sha256()
    for gid in sorted(rows):
        r = rows[gid]
        h.update(json.dumps([gid, r.cutoff_at, r.result_available_at, r.x, r.y], sort_keys=True, default=str).encode())
    return h.hexdigest()[:16]
