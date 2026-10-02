"""Bounded CatBoost challenger (docs/MODEL_SELECTION.md §10). Exploratory, development seasons only, no ledger rows.

Feature vectors are the ones the challenged member records at forecast time in the walk-forward, so CatBoost sees identical
pregame inputs. Each season is predicted by a fit on all earlier seasons only. CatBoost is the optional `research` extra and is
imported here only.
"""
import itertools
import json
import math
from pathlib import Path
from typing import Callable, Optional

from . import backtest, config, nhl_eval
from .db import utc_now
from .retrain_cadence import _gauss_logscore

GRID = [{"depth": d, "learning_rate": lr, "l2_leaf_reg": l2}
        for d, lr, l2 in itertools.product((4, 6), (0.03, 0.08), (3, 10))]
FIXED = {"iterations": 500, "random_seed": 20260930, "thread_count": 4, "verbose": 0, "allow_writing_files": False}
EPS = 1e-15


class Recorder:
    """Pass-through that keeps each game's pregame feature vector as the member captured it at forecast time."""

    def __init__(self, member):
        self.member, self.name = member, member.name
        self.x: dict[str, list] = {}

    def predict(self, g):
        p = self.member.predict(g)
        self.x.setdefault(g.game_id, list(self.member.pending[g.game_id][1]))
        return p

    def distribution(self, g):
        return self.member.distribution(g)

    def update(self, r):
        self.member.update(r)

    def update_box(self, e):
        if hasattr(self.member, "update_box"):
            self.member.update_box(e)


def _ll(p: float, y: bool) -> float:
    p = min(1 - EPS, max(EPS, p))
    return -math.log(p if y else 1 - p)


def _winner_losses(p: float, y: bool) -> dict:
    return {"log_loss": _ll(p, y), "brier": (p - float(y)) ** 2}


def _fit_predict(kind: str, params: dict, train_x, train_y, test_x):
    from catboost import CatBoostClassifier, CatBoostRegressor
    if kind == "winner":
        m = CatBoostClassifier(loss_function="Logloss", **params, **FIXED)
        m.fit(train_x, [int(y) for y in train_y])
        return [float(p) for p in m.predict_proba(test_x)[:, 1]]
    m = CatBoostRegressor(loss_function="RMSEWithUncertainty", **params, **FIXED)
    m.fit(train_x, train_y)
    return [(float(mu), math.sqrt(max(1e-9, float(var)))) for mu, var in m.predict(test_x)]


def walk_catboost(kind: str, params: dict, data: dict, seasons) -> dict:
    """game_id -> losses for every game in `seasons`, each season fitted on all earlier seasons only."""
    out = {}
    for s in seasons:
        train = [g for g in data.values() if g["season"] < s]
        test = [g for g in data.values() if g["season"] == s]
        if kind == "winner":
            preds = _fit_predict(kind, params, [g["x"] for g in train], [g["y"] for g in train], [g["x"] for g in test])
            for g, p in zip(test, preds):
                out[g["game_id"]] = {**_winner_losses(p, g["y"]), "_season": s, "_cutoff": g["cutoff"]}
        else:
            pm = _fit_predict(kind, params, [g["x"] for g in train], [g["margin"] for g in train], [g["x"] for g in test])
            pt = _fit_predict(kind, params, [g["x"] for g in train], [g["total"] for g in train], [g["x"] for g in test])
            for g, (mm, sm), (mt, st) in zip(test, pm, pt):
                out[g["game_id"]] = {"margin_logscore": _gauss_logscore(g["margin"], mm, sm),
                                     "total_logscore": _gauss_logscore(g["total"], mt, st), "_season": s, "_cutoff": g["cutoff"]}
    return out


# --- Data -----------------------------------------------------------------------------------------------------------
def _harvest(member, rows, events, kind):
    """Runs the challenged member once: its recorded feature vectors and its own per-game losses (a comparator)."""
    rec = Recorder(member)
    preds = backtest.run(rec, rows, events, extra=(lambda m, g: {"dist": m.distribution(g)}) if kind != "winner" else None)
    by_id = {r.game.game_id: r for r in rows}
    data, losses = {}, {}
    for p in preds:
        r = by_id[p["game_id"]]
        base = {"game_id": p["game_id"], "season": p["season"], "cutoff": p["cutoff_at"], "x": rec.x[p["game_id"]],
                "y": p["home_won"], "margin": r.home_score - r.away_score, "total": r.home_score + r.away_score}
        data[p["game_id"]] = base
        losses[p["game_id"]] = _member_losses(kind, p, base)
    return data, losses


def _member_losses(kind, p, g) -> Optional[dict]:
    if kind == "winner":
        return {**_winner_losses(p["p_home"], p["home_won"]), "_season": p["season"], "_cutoff": p["cutoff_at"]}
    d = p.get("dist")
    if d is None:
        return None
    return {"margin_logscore": _gauss_logscore(g["margin"], d.m, d.sm), "total_logscore": _gauss_logscore(g["total"], d.t, d.st),
            "_season": p["season"], "_cutoff": p["cutoff_at"]}


def _baseline(member, rows, events, kind):
    out = {}
    preds = backtest.run(member, rows, events, extra=(lambda m, g: {"dist": m.distribution(g)}) if kind != "winner" else None)
    by_id = {r.game.game_id: r for r in rows}
    for p in preds:
        r = by_id[p["game_id"]]
        out[p["game_id"]] = _member_losses(kind, p, {"margin": r.home_score - r.away_score, "total": r.home_score + r.away_score})
    return out


def tasks(conn, sport: str) -> list[dict]:
    if sport == "nba":
        from .models.efficiency import EfficiencyMargin
        from .models.elo import Elo
        from .models.gbt import GradientBoostedTrees
        from .models.logistic import LogisticElo, LogisticV2
        from .models.score_candidates import GbtPoints, RidgePoints
        _, cfg = backtest.load_frozen_decision(conn)
        rows, events = backtest.load_rows(conn, "validation"), backtest.load_box_events(conn, "validation")
        if {r.game.season for r in rows} - set(backtest.LOADABLE["validation"]):
            raise ValueError("challenger read seasons outside warm-up/validation")
        return [{"name": "nba_winner", "kind": "winner", "rows": rows, "events": events, "replaces": "gbt_v1",
                 "harvest": lambda: GradientBoostedTrees(cfg), "primary": "log_loss", "tune": (2022, 2023), "hold": (2024,),
                 "baselines": {"elo": lambda: Elo(cfg), "logistic": lambda: LogisticElo(cfg), "logistic_v2": lambda: LogisticV2(cfg),
                               "efficiency_margin": EfficiencyMargin}},
                {"name": "nba_points", "kind": "points", "rows": rows, "events": events, "replaces": "gbt_points",
                 "harvest": lambda: GbtPoints(cfg), "primary": "margin_logscore", "tune": (2022, 2023), "hold": (2024,),
                 "baselines": {"ridge_points": lambda: RidgePoints(cfg)}}]
    from . import sports
    from .models.hockey import GoalsConfig, GoalsPoisson, HockeyElo, HockeyEloConfig, hockey_gbt, hockey_logistic
    d = nhl_eval.load_decision(conn)
    rows = nhl_eval.load_rows(conn, "development")
    if {r.game.season for r in rows} - set(nhl_eval.LOADABLE["development"]):
        raise ValueError("challenger read seasons outside warm-up/development")
    ec, gc, key = HockeyEloConfig(**d["hockey_elo_config"]), GoalsConfig(**d["goals_config"]), sports.NHL.rating_key
    return [{"name": "nhl_winner", "kind": "winner", "rows": rows, "events": None, "replaces": "hockey_gbt",
             "harvest": lambda: hockey_gbt(ec, gc, key), "primary": "log_loss", "tune": (2018, 2019), "hold": (2020, 2021),
             "baselines": {"hockey_elo": lambda: HockeyElo(ec, key), "goals_poisson": lambda: GoalsPoisson(gc, key),
                           "hockey_logistic": lambda: hockey_logistic(ec, gc, key)}}]


# --- Evaluation -----------------------------------------------------------------------------------------------------
def _mean(losses: dict, ids, metric) -> float:
    return sum(losses[g][metric] for g in ids) / len(ids)


def _paired(a: dict, b: dict, ids, metric) -> dict:
    from .retrain_cadence import week_bootstrap
    diffs = [a[g][metric] - b[g][metric] for g in ids]
    r = week_bootstrap(diffs, [a[g]["_cutoff"] for g in ids], alphas=(0.05, 0.05), samples=backtest.BOOTSTRAP_SAMPLES)
    return {k: r[k] for k in ("mean", "ci95", "weeks", "games")}


def run_task(t: dict, log: Callable[[str], None]) -> dict:
    kind, primary = t["kind"], t["primary"]
    data, own = _harvest(t["harvest"](), t["rows"], t["events"], kind)
    members = {t["replaces"]: own}
    for name, f in t["baselines"].items():
        members[name] = _baseline(f(), t["rows"], t["events"], kind)
        log(f"{t['name']}: baseline {name} walked")
    seasons = sorted(set(t["tune"]) | set(t["hold"]))

    def usable(ids_season):
        return [g for g in ids_season if all(m.get(g) is not None for m in members.values())]
    tune_ids = usable([g for g, v in data.items() if v["season"] in t["tune"]])
    hold_ids = usable([g for g, v in data.items() if v["season"] in t["hold"]])
    trials = []
    for i, params in enumerate(GRID):
        cb = walk_catboost(kind, params, data, t["tune"])
        ids = [g for g in tune_ids if g in cb]
        per = {s: _mean(cb, [g for g in ids if data[g]["season"] == s], primary) for s in t["tune"]}
        trials.append({"trial": i, "params": params, "tune_loss": sum(per.values()) / len(per), "per_season": per})
        log(f"{t['name']}: trial {i} {params} tune {trials[-1]['tune_loss']:.5f}")
    best = min(trials, key=lambda tr: (tr["tune_loss"], tr["trial"]))
    strongest = min(t["baselines"].keys() | {t["replaces"]},
                    key=lambda n: (sum(_mean(members[n], [g for g in tune_ids if data[g]["season"] == s], primary)
                                       for s in t["tune"]), n))
    cb = walk_catboost(kind, best["params"], data, t["hold"])
    ids = [g for g in hold_ids if g in cb]
    metrics = [k for k in cb[ids[0]] if not k.startswith("_")]
    held = {"catboost": {m: _mean(cb, ids, m) for m in metrics}}
    for n, mem in members.items():
        held[n] = {m: _mean(mem, ids, m) for m in metrics}
    vs = {n: {m: _paired(cb, members[n], ids, m) for m in metrics} for n in {t["replaces"], strongest}}
    qualifies = (held["catboost"][primary] < held[t["replaces"]][primary]
                 and held["catboost"][primary] < held[strongest][primary]
                 and vs[strongest][primary]["ci95"][1] < 0)
    per_season = {s: {"catboost": _mean(cb, [g for g in ids if data[g]["season"] == s], primary),
                      **{n: _mean(members[n], [g for g in ids if data[g]["season"] == s], primary) for n in members}}
                  for s in t["hold"]}
    return {"task": t["name"], "kind": kind, "primary": primary, "tune_seasons": list(t["tune"]),
            "hold_seasons": list(t["hold"]), "grid_size": len(GRID), "trials": trials, "chosen": best,
            "replaces": t["replaces"], "strongest_on_tuning_seasons": strongest, "held_out_games": len(ids),
            "held_out": held, "held_out_per_season": per_season, "paired": vs, "qualifies": qualifies,
            "seasons_scored": seasons}


def run(conn, sport: str, out_dir: Optional[Path] = None, log: Callable[[str], None] = print) -> dict:
    import catboost
    started = utc_now()
    report = {"kind": "catboost-challenger", "sport": sport, "started_at": started, "catboost_version": catboost.__version__,
              "fixed": FIXED, "grid": GRID, "exploratory": True, "tasks": []}
    for t in tasks(conn, sport):
        report["dataset_version"] = (backtest.dataset_version(t["rows"]) if sport == "nba"
                                     else nhl_eval.dataset_version(t["rows"]))
        report["tasks"].append(run_task(t, log))
    report["finished_at"] = utc_now()
    out_dir = out_dir or config.data_dir() / "reports" / f"catboost-{sport}-{started.replace(':', '')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, default=str))
    report["output_dir"] = str(out_dir)
    log(f"wrote {out_dir / 'report.json'}")
    return report
