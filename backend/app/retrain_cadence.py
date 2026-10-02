"""Retraining-cadence comparison (docs/MODEL_SELECTION.md §9). Exploratory, development seasons only, no ledger rows.

`Cadenced` wraps an existing learned member without changing its definition: the member still refits at each season start
itself; with a monthly or weekly cadence the wrapper also refits it at the first forecast of each new month / ISO week. A
member's `_refit(s)` trains on its examples from seasons before `s`, and examples join only when backtest.run delivers a
result available by the current cutoff, so `_refit(current season + 1)` trains on exactly the results delivered so far.
"""
import hashlib
import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from . import backtest, combination_research, config, nhl_eval
from .db import utc_now

CADENCES = ("season", "monthly", "weekly")
SAMPLES = 20000
COMPARISONS = 18          # 9 members x 2 non-default cadences, primary metric each (Bonferroni)
_FIRST_ATTRS = ("first_fit_season", "first_ready_season")


def period_key(cadence: str, tipoff_at: str) -> tuple:
    """Refit period of a forecast, by the game's Eastern calendar date (the date its cutoff belongs to)."""
    d = datetime.strptime(tipoff_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(backtest.EASTERN).date()
    if cadence == "monthly":
        return d.year, d.month
    if cadence == "weekly":
        return tuple(d.isocalendar()[:2])
    return ()


class Cadenced:
    def __init__(self, member, cadence: str):
        if cadence not in CADENCES:
            raise ValueError(f"unknown cadence {cadence}")
        self.member, self.cadence = member, cadence
        self.name = f"{member.name}@{cadence}"
        self.key: Optional[tuple] = None
        self.first_season: Optional[int] = None
        self.refits: list[dict] = []   # within-season refits: cutoff and training size
        self.delivered: list[str] = []  # game ids of results delivered so far, in delivery order (append-only)
        self.last_fit: Optional[dict] = None

    def _maybe_refit(self, g) -> None:
        if self.first_season is None:
            self.first_season = g.season
        if getattr(self.member, "season", None) is not None and g.season > self.member.season:
            self._log("season_start", g)   # the member's own refit at this forecast trains on every delivered result
        if self.cadence == "season" or g.season == self.first_season:
            return   # no within-season refits in the walk's first season: no earlier-season data exists yet
        key = (g.season, *period_key(self.cadence, g.tipoff_at))
        if key == self.key:
            return
        self.key = key
        if getattr(self.member, "season", None) != g.season:
            return   # first forecast of a season: the member's own season-start refit runs (on the same delivered results)
        before = {a: getattr(self.member, a, None) for a in _FIRST_ATTRS}
        self.member._refit(g.season + 1)
        for a, v in before.items():   # a first fit made mid-season belongs to the current season
            if v is None and getattr(self.member, a, None) is not None:
                setattr(self.member, a, g.season)
        self.refits.append({"cutoff_at": backtest.cutoff_for(g.tipoff_at), "season": g.season,
                            "training_games": len(self.member.examples)})
        self._log(self.cadence, g)

    def _log(self, kind: str, g) -> None:
        self.last_fit = {"kind": kind, "trigger_game": g.game_id, "trigger_cutoff": backtest.cutoff_for(g.tipoff_at),
                         "delivered_results": len(self.delivered)}

    def fit_info(self) -> Optional[dict]:
        """Identity of the fit in force: what triggered it and a fingerprint of the delivered results it could use."""
        if self.last_fit is None:
            return None
        ids = self.delivered[:self.last_fit["delivered_results"]]
        return {**self.last_fit, "cadence": self.cadence,
                "results_fingerprint": hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest()[:16]}

    def predict(self, g) -> float:
        self._maybe_refit(g)
        return self.member.predict(g)

    def distribution(self, g):
        self._maybe_refit(g)
        return self.member.distribution(g)

    def update(self, r) -> None:
        self.member.update(r)
        self.delivered.append(r.game.game_id)

    def update_box(self, e) -> None:
        if hasattr(self.member, "update_box"):
            self.member.update_box(e)

    def config(self) -> dict:
        return {**self.member.config(), "cadence": self.cadence}


# --- Walks ----------------------------------------------------------------------------------------------------------
def _nba_members():
    from .models.gbt import GradientBoostedTrees
    from .models.logistic import LogisticElo, LogisticV2
    from .models.score_candidates import GbtPoints, RidgePoints
    return {"logistic": (LogisticElo, False), "logistic_v2": (LogisticV2, False), "gbt_v1": (GradientBoostedTrees, False),
            "ridge_points": (RidgePoints, True), "gbt_points": (GbtPoints, True)}


def _gauss_logscore(x: float, mu: float, sd: float) -> float:
    """Negative log density (lower is better)."""
    return 0.5 * math.log(2 * math.pi * sd * sd) + (x - mu) ** 2 / (2 * sd * sd)


def _walk(member, rows, events, scorer) -> dict:
    """game_id -> {metric: per-game loss}; games a member cannot forecast yet are absent."""
    out = {}
    preds = backtest.run(member, rows, events, extra=(lambda m, g: {"dist": m.distribution(g)})
                         if hasattr(getattr(member, "member", member), "distribution") else None)
    by_id = {r.game.game_id: r for r in rows}
    for p in preds:
        losses = scorer(p, by_id[p["game_id"]])
        if losses is not None:
            out[p["game_id"]] = {**losses, "_season": p["season"], "_cutoff": p["cutoff_at"], "_p": p["p_home"]}
    return out


def _nba_score(p, row):
    y = float(p["home_won"])
    if "dist" not in p:
        return {"brier": (p["p_home"] - y) ** 2}
    d = p["dist"]
    if d is None:
        return None
    return {"brier": (p["p_home"] - y) ** 2,
            "margin_logscore": _gauss_logscore(row.home_score - row.away_score, d.m, d.sm),
            "total_logscore": _gauss_logscore(row.home_score + row.away_score, d.t, d.st)}


def _nhl_score(p, row):
    y = float(p["home_won"])
    if "dist" not in p:
        return {"brier": (p["p_home"] - y) ** 2}
    d = p["dist"]
    if d is None:
        return None
    real = combination_research._realised("reg3", None, row.home_score, row.away_score, row.decision)
    vec = d.market("reg3", None)
    return {"brier": (p["p_home"] - y) ** 2, "reg3_logloss": -math.log(min(1 - 1e-15, max(1e-15, vec[real])))}


def nba_setup(conn):
    _, cfg = backtest.load_frozen_decision(conn)
    rows, events = backtest.load_rows(conn, "validation"), backtest.load_box_events(conn, "validation")
    if {r.game.season for r in rows} - set(backtest.LOADABLE["validation"]):
        raise ValueError("cadence study read seasons outside warm-up/validation")
    factories = {n: (lambda c=c: c(cfg), dist) for n, (c, dist) in _nba_members().items()}
    primary = {n: ("margin_logscore" if dist else "brier") for n, (_, dist) in factories.items()}
    return rows, events, factories, primary, _nba_score, backtest.PERIODS["validation"], backtest.dataset_version(rows)


def nhl_setup(conn):
    from . import sports
    from .models.hockey import GoalsConfig, HockeyEloConfig, hockey_gbt, hockey_logistic
    from .models.score_candidates import GoalsDraw, GoalsGlm
    d = nhl_eval.load_decision(conn)
    rows = nhl_eval.load_rows(conn, "development")
    if {r.game.season for r in rows} - set(nhl_eval.LOADABLE["development"]):
        raise ValueError("cadence study read seasons outside warm-up/development")
    ec, gc, key = HockeyEloConfig(**d["hockey_elo_config"]), GoalsConfig(**d["goals_config"]), sports.NHL.rating_key
    factories = {"hockey_logistic": (lambda: hockey_logistic(ec, gc, key), False),
                 "hockey_gbt": (lambda: hockey_gbt(ec, gc, key), False),
                 "goals_glm": (lambda: GoalsGlm(ec, key), True), "goals_draw": (lambda: GoalsDraw(gc, key), True)}
    primary = {n: ("reg3_logloss" if dist else "brier") for n, (_, dist) in factories.items()}
    return rows, None, factories, primary, _nhl_score, nhl_eval.PERIODS["development"], nhl_eval.dataset_version(rows)


# --- Statistics -----------------------------------------------------------------------------------------------------
def week_bootstrap(values: list[float], cutoffs: list[str], alphas=(0.05, 0.05 / COMPARISONS),
                   seed: int = backtest.BOOTSTRAP_SEED, samples: int = SAMPLES) -> dict:
    """Same ISO-week blocks and seed as backtest.block_bootstrap, with two-sided intervals at each alpha."""
    blocks: dict[tuple, list[float]] = {}
    for v, c in zip(values, cutoffs):
        iso = datetime.strptime(c, "%Y-%m-%dT%H:%M:%SZ").isocalendar()
        blocks.setdefault((iso[0], iso[1]), []).append(v)
    weeks = list(blocks.values())
    rng, stats = random.Random(seed), []
    for _ in range(samples):
        pick = [weeks[rng.randrange(len(weeks))] for _ in weeks]
        stats.append(sum(sum(w) for w in pick) / sum(len(w) for w in pick))
    stats.sort()
    ci = lambda a: [stats[int(a / 2 * samples)], stats[max(0, int((1 - a / 2) * samples) - 1)]]  # noqa: E731
    return {"mean": sum(values) / len(values), "ci95": ci(alphas[0]), "ci_bonferroni": ci(alphas[1]),
            "weeks": len(weeks), "games": len(values), "seed": seed, "samples": samples}


def compare(walks: dict, primary: str, evaluated: tuple) -> dict:
    """walks: cadence -> game_id -> losses. Paired on games every cadence forecast in the evaluated seasons."""
    common = set.intersection(*(set(g for g, v in w.items() if v["_season"] in evaluated) for w in walks.values()))
    common = sorted(common)
    base = walks["season"]
    metrics = [k for k in base[common[0]] if not k.startswith("_")] if common else []
    out = {"games": len(common), "primary": primary,
           "levels": {c: {m: sum(walks[c][g][m] for g in common) / len(common) for m in metrics} for c in walks}}
    for c in walks:
        if c == "season":
            continue
        cmp = {}
        for m in metrics:
            diffs = [walks[c][g][m] - base[g][m] for g in common]
            cmp[m] = week_bootstrap(diffs, [base[g]["_cutoff"] for g in common])
            per = {}
            for s in evaluated:
                ds = [d for d, g in zip(diffs, common) if base[g]["_season"] == s]
                per[s] = sum(ds) / len(ds) if ds else None
            cmp[m]["per_season"] = per
        need = len(evaluated) - 1 if len(evaluated) == 4 else 2
        p = cmp[primary]
        nonpos = sum(1 for v in p["per_season"].values() if v is not None and v <= 0)
        cmp["supports_trial"] = p["ci_bonferroni"][1] < 0 and nonpos >= need
        cmp["seasons_not_worse"] = nonpos
        out[c] = cmp
    return out


def run(conn, sport: str, out_dir: Optional[Path] = None, log: Callable[[str], None] = print,
        members: Optional[list[str]] = None) -> dict:
    started = utc_now()
    rows, events, factories, primary, scorer, evaluated, version = (nba_setup if sport == "nba" else nhl_setup)(conn)
    report = {"kind": "retrain-cadence", "sport": sport, "started_at": started, "dataset_version": version,
              "evaluated_seasons": list(evaluated), "exploratory": True, "members": {}}
    if events is not None:
        report["box_version"] = backtest.box_version(events)
    for name, (factory, _) in factories.items():
        if members and name not in members:
            continue
        walks, info = {}, {}
        plain = _walk(factory(), rows, events, scorer)   # the unwrapped member, as the §5 study runs it
        for c in CADENCES:
            t0 = datetime.now()
            w = Cadenced(factory(), c)
            walks[c] = _walk(w, rows, events, scorer)
            info[c] = {"refits_within_season": len(w.refits), "season_fits": getattr(w.member, "fits", None),
                       "seconds": round((datetime.now() - t0).total_seconds(), 1), "config": w.config()}
            log(f"{sport} {name}@{c}: {len(walks[c])} games, {len(w.refits)} within-season refits, {info[c]['seconds']} s")
        report["members"][name] = {"walks": info, **compare(walks, primary[name], evaluated)}
        report["members"][name]["season_reproduces_default"] = plain == walks["season"]
        if plain != walks["season"]:
            raise RuntimeError(f"{name}: season cadence does not reproduce the unwrapped member")
    report["finished_at"] = utc_now()
    out_dir = out_dir or config.data_dir() / "reports" / f"retrain-cadence-{sport}-{started.replace(':', '')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, default=str))
    report["output_dir"] = str(out_dir)
    log(f"wrote {out_dir / 'report.json'}")
    return report
