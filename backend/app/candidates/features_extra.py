"""NHL shot volume and special-teams finishing `nhl_special` (docs/MODEL_SELECTION.md §19, features-v1). Exploratory.

Three inputs from our own play-by-play shots of games delivered before the cutoff (20-game team window, carried across
seasons like the xG inputs): all-situation shot-attempt share (shots on goal, missed, blocked and goals), power-play goals
for per game and short-handed goals against per game (goals conceded while the team had fewer skaters). They extend the
`nhl_base` features; the existing feature sets and every existing candidate are unchanged. This module is NOT part of
`candidates.registry.definition`: if an `nhl_special` spec is ever registered, that function must hash this file too.
"""
import json
import math
from collections import defaultdict, deque
from statistics import NormalDist
from typing import Callable, Optional

from ..models.hockey_xg import strength
from . import features

WINDOW = 20
ATTEMPTS = ("shot-on-goal", "missed-shot", "blocked-shot", "goal")
SPECIAL = ("shot_share_diff", "pp_goals_diff", "sh_goals_against_diff")
features.FEATURE_SETS.setdefault("nhl_special", features.NHL_BASE + SPECIAL)


class SpecialInputs:
    """Per team: deque of (attempts for, attempts against, PP goals for, goals against while short-handed)."""

    def __init__(self, team_of=lambda t: t):
        self.team_of = team_of
        self.games: dict[str, deque] = defaultdict(lambda: deque(maxlen=WINDOW))

    def update_box(self, e) -> None:
        g = e.game
        tot = {side: {"att": 0, "ppg": 0, "shga": 0} for side in ("home", "away")}
        for s in e.shots:
            if s.event in ATTEMPTS:
                tot[s.side]["att"] += 1
            if s.goal and not s.empty_net:
                st = strength(s.situation, s.side)
                if st == "pp":
                    tot[s.side]["ppg"] += 1
                    tot["away" if s.side == "home" else "home"]["shga"] += 1
        for side, opp, team in (("home", "away", g.home_team_id), ("away", "home", g.away_team_id)):
            self.games[self.team_of(team)].append((tot[side]["att"], tot[opp]["att"], tot[side]["ppg"], tot[side]["shga"]))

    def _team(self, team: str) -> tuple[float, float, float]:
        d = self.games[self.team_of(team)]
        if not d:
            return 0.5, 0.0, 0.0
        f, a = sum(x[0] for x in d), sum(x[1] for x in d)
        return (f / (f + a) if f + a else 0.5), sum(x[2] for x in d) / len(d), sum(x[3] for x in d) / len(d)

    def features(self, g) -> dict:
        h, a = self._team(g.home_team_id), self._team(g.away_team_id)
        return {"shot_share_diff": h[0] - a[0], "pp_goals_diff": h[1] - a[1], "sh_goals_against_diff": h[2] - a[2]}


class NhlSpecialRecorder(features.NhlRecorder):
    """The NHL recorder plus the three special inputs; every base feature is computed exactly as before."""
    name = "features:nhl+special"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.special = SpecialInputs(self.team.elo._team)

    def predict(self, g) -> float:
        new = g.game_id not in self.rows
        out = super().predict(g)
        if new:
            self.rows[g.game_id].x.update(self.special.features(g))
        return out

    def update_box(self, e) -> None:
        super().update_box(e)
        self.special.update_box(e)


def _log(entry: dict) -> None:
    from .. import config
    from ..db import utc_now
    p = config.data_dir() / "research" / "features-v1" / "experiments.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps({"logged_at": utc_now(), **entry}, default=str) + "\n")


def evaluate(conn, log: Callable[[str], None] = print, out_dir=None) -> dict:
    """§19 NHL: the candidate learners on nhl_base vs nhl_special on one shared walk; paired log loss per season split."""
    from pathlib import Path
    from .. import backtest, combine, config, markets, nhl_eval, nhl_shots, sports
    from ..db import utc_now
    from ..models.hockey import GoalsConfig, HockeyEloConfig
    from . import engine
    from .registry import Spec
    d = nhl_eval.load_decision(conn)
    rows = nhl_eval.load_rows(conn, "test")           # every season; all spent (exploratory), access logged
    store = conn.execute("PRAGMA database_list").fetchone()[2]
    _log({"study": "features-v1", "part": "nhl_special", "access": "nhl_eval.load_rows('test')", "store": store})
    events = nhl_shots.load_events(conn, rows)
    rec = NhlSpecialRecorder(HockeyEloConfig(**d["hockey_elo_config"]), GoalsConfig(**d["goals_config"]), sports.NHL.rating_key)
    walk = list(features.walk(rec, rows, events).values())
    specs = {
        "logit_l2:nhl_base": Spec("logit_l2:nhl_base", "nhl", "logit_l2", "binary", "nhl_base", "reference", "same-walk reference"),
        "logit_l2:nhl_special": Spec("logit_l2:nhl_special", "nhl", "logit_l2", "binary", "nhl_special", "candidate", "§19"),
        "poisson_draw:nhl_base": Spec("poisson_draw:nhl_base", "nhl", "poisson_draw", "goals", "nhl_base", "reference", "same-walk reference"),
        "poisson_draw:nhl_special": Spec("poisson_draw:nhl_special", "nhl", "poisson_draw", "goals", "nhl_special", "candidate", "§19"),
    }
    outs = {}
    for name, spec in specs.items():
        try:
            outs[name], fits, _ = engine.walk(spec, walk)
            if "special" in name:
                _log({"study": "features-v1", "config": name, "outcome": "success", "games": len(outs[name]),
                      "fits": [{k: f[k] for k in ("season", "training_games")} for f in fits]})
        except Exception as exc:
            _log({"study": "features-v1", "config": name, "outcome": "failed", "error": f"{type(exc).__name__}: {exc}"})
            raise
    by = {r.game_id: r for r in walk}
    pl = {r["game_id"]: r["line"] for r in _oof_lines("spread")}
    from .. import agreement_tracking
    anchors = {m: agreement_tracking.load_registry()["markets"][f"nhl:{m}"]["anchor"] for m in ("winner", "reg3", "spread")}
    anchor_vec = {m: {r["game_id"]: r["vectors"].get(anchors[m]) for r in _oof_lines(m)} for m in anchors}
    tests = [("winner", "logit_l2"), ("winner", "poisson_draw"), ("reg3", "poisson_draw"), ("spread", "poisson_draw")]
    z = NormalDist().inv_cdf(1 - 0.05 / 2 / len(tests))
    results = {}
    for market, fam in tests:
        a, b = outs[f"{fam}:nhl_special"], outs[f"{fam}:nhl_base"]
        diffs = {"screen": [], "assess": []}
        cut = {"screen": [], "assess": []}
        adiffs = {"screen": [], "assess": []}         # with/without in the average with the market's anchor (§19)
        acut = {"screen": [], "assess": []}
        for gid in sorted(set(a) & set(b), key=lambda g: (by[g].cutoff_at, g)):
            r = by[gid]
            if r.season < 2018 or r.y is None:
                continue
            line = pl.get(gid, -1.5) if market == "spread" else None
            s = markets.settle(market, line, status="final", home=r.y["home"], away=r.y["away"], decision=r.y["decision"])
            realised = {"a": 0, "mid": 1, "b": 2}.get(s)
            if realised is None:
                continue
            va, vb = engine.vector(a[gid], market, line), engine.vector(b[gid], market, line)
            if va is None or vb is None:
                continue
            if market == "spread" and realised == 1:
                continue
            part = "screen" if r.season <= 2021 else "assess"
            diffs[part].append(combine.log_loss(va, realised) - combine.log_loss(vb, realised))
            cut[part].append(r.cutoff_at)
            av = anchor_vec[market].get(gid)
            if av is not None and (market != "spread" or pl.get(gid) is not None):
                adiffs[part].append(combine.log_loss(combine.average([av, va]), realised)
                                    - combine.log_loss(combine.average([av, vb]), realised))
                acut[part].append(r.cutoff_at)
        res = {}
        for part in diffs:
            if not diffs[part]:
                continue
            bs = backtest.block_bootstrap(diffs[part], cut[part])
            sd = (bs["ci95"][1] - bs["ci95"][0]) / (2 * 1.959964)
            res[part] = {**bs, "games": len(diffs[part]), "bonferroni_interval": [bs["mean"] - z * sd, bs["mean"] + z * sd]}
        for part in adiffs:
            if adiffs[part]:
                bs = backtest.block_bootstrap(adiffs[part], acut[part])
                sd = (bs["ci95"][1] - bs["ci95"][0]) / (2 * 1.959964)
                res[f"{part}_with_anchor_{anchors[market]}"] = {**bs, "games": len(adiffs[part]),
                                                                "bonferroni_interval": [bs["mean"] - z * sd, bs["mean"] + z * sd]}
        results[f"{market}:{fam}"] = res
    report = {"study": "features-v1", "part": "NHL nhl_special", "exploratory": True, "generated_at": utc_now(),
              "walk_games": len(walk), "comparison": "special − base log loss (negative favours the new inputs)",
              "results": results,
              "reading": {k: ("adds information" if v.get("assess", {}).get("bonferroni_interval", [0, 0])[1] < 0
                              else "no evidence of added information") for k, v in results.items()},
              "note": "Same learner, same walk; only the three inputs differ. Seasons were spent before (exploratory)."}
    out_dir = Path(out_dir or config.data_dir() / "reports" / f"features-v1-nhl-{report['generated_at'].replace(':', '')}")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=1, default=str))
    report["output_dir"] = str(out_dir)
    log(f"wrote {out_dir / 'report.json'}")
    return report


def _oof_lines(market: str) -> list:
    import gzip
    from .. import config
    out = []
    for stage in ("screen", "assess"):
        p = config.data_dir() / "research" / "candidates" / f"oof-nhl-{stage}.json.gz"
        if p.is_file():
            out += json.loads(gzip.decompress(p.read_bytes()))["tables"].get(market, [])
    return out
