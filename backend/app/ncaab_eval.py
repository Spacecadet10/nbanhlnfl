"""NCAAB walk-forward evaluation under the preregistered plan (docs/NCAAB_PLAN.md "Evaluation plan", amendment 2026-11-17).

Stages (CLI `python -m app.ncaab_eval {development,validation,test}`):
* development: each candidate's grid scored on development seasons only (warm-up seasons are burn-in, never scored);
  sigma of margin/total/team points fitted on development residuals. Rerunnable (reads no validation row).
* validation (once): tuned candidates compared on 2022-2024; winner = best validation log loss, shipped only if its paired
  weekly-block bootstrap interval vs `college_elo` is below 0, else `college_elo`; per market `college_eff` vs the
  preregistered baseline and the 2-point decile calibration rule. Seals `app/models/ncaab_frozen_decision.json` and
  refuses to run again once it exists.
* test (once): the frozen choice on 2025-2026; the access is appended to `<NBA_DATA_DIR>/eval/test_access_ncaab.jsonl`
  before any test row is read, and a second access is refused.

The walk is backtest.run (leakage checks: a result is delivered only once available at the game's cutoff, 00:00
America/New_York on its date). Rows come from app.ncaab.load_rows (Division I, regular/postseason, finals). There are no
historical sportsbook prices; markets are graded at each model's own line, at both models' lines (the comparison), and at
the after-game DraftKings line where `ncaab_after_game_lines` has one (benchmark only, retrieved after the game).
"""
import argparse
import csv
import hashlib
import itertools
import json
import math
import os
import shlex
import sqlite3
import sys
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from . import backtest, config, markets, metrics, ncaab_app
from .db import has_table, store_sport, utc_now
from .models import college
from .models.baselines import Constant, HomeRate
from .models.base import PregameGame
from .models.college import CollegeEff, CollegeEffConfig, CollegeElo, CollegeEloConfig, RunningPoints

PERIODS = dict(ncaab_app.PERIODS)
ELO_GRID = {"k": (20, 30, 40, 50), "home_advantage": (0, 50, 75, 100), "carry": (0.5, 0.65, 0.8, 1.0), "mov": (False, True)}
EFF_GRID = {"eta": (0.02, 0.04, 0.08), "carry": (0.5, 0.7, 0.9), "w0": (4, 8, 12)}
BOOTSTRAP_SEED = backtest.BOOTSTRAP_SEED
BOOTSTRAP_SAMPLES = 2000
DEFAULT_CUTOFF = "2026-11-17T12:00:00Z"
FEATURE_VERSION = ncaab_app.FEATURE_VERSION + "+scoreboard-poss-fga-fta-v1"
CALIBRATION_POINTS = 2.0        # a market model is calibrated when every validation decile is within 2 points
EARLY_DAYS = 14                 # "first weeks": games within 14 days of the season's first eligible game
TIE_DECIMALS = 6                # grid log losses equal to 6 decimals are ties
WINNER_CANDIDATES = ("constant", "home_rate", "college_elo", "college_eff")
BASELINE = {"spread": "elo_margin", "total": "running_points", "team_total_home": "running_points",
            "team_total_away": "running_points"}
SELECTION_RULE = ("best validation log loss among tuned winner candidates; it ships only if the paired weekly-block "
                  "bootstrap 95% interval of its log loss minus college_elo's is entirely below 0, otherwise college_elo")
MARKET_RULE = ("college_eff ships if its validation log loss at both models' lines is below the baseline's and it is "
               "calibrated (every decile of predicted points within 2 points of the observed mean); otherwise the "
               "baseline if calibrated; if the log-loss winner is not calibrated the other ships if calibrated; "
               "neither calibrated -> research")


class NcaabEvaluationError(RuntimeError):
    pass


def loadable(period: str) -> tuple:
    order = ("warmup", "development", "validation", "test")
    return tuple(s for p in order[:order.index(period) + 1] for s in PERIODS[p])


def load(conn: sqlite3.Connection, period: str, cutoff: str) -> tuple[list, dict]:
    """Rows (and scoreboard FGA/FTA) of only the seasons `period` may read, filtered in SQL."""
    if store_sport(conn) != "ncaab":
        raise NcaabEvaluationError("NCAAB evaluation needs the NCAAB store")
    return college.rows_at(conn, cutoff, seasons=loadable(period))


def in_period(preds: list[dict], period: str) -> list[dict]:
    return [p for p in preds if p["season"] in PERIODS[period]]


# --- Walk ------------------------------------------------------------------------------------------------------------
def walk(rows: list, box: dict, elo_cfg: CollegeEloConfig, eff_cfg: CollegeEffConfig) -> dict[str, list[dict]]:
    """Every candidate over every row (backtest.run), with the means each market model needs."""
    scores = {r.game.game_id: (r.home_score, r.away_score) for r in rows}

    def tag(preds):
        for p in preds:
            p["home_score"], p["away_score"] = scores[p["game_id"]]
        return preds
    return {
        "constant": tag(backtest.run(Constant(), rows)),
        "home_rate": tag(backtest.run(HomeRate(), rows)),
        "college_elo": tag(backtest.run(CollegeElo(elo_cfg), rows, extra=lambda m, g: {"elo_margin": m.margin(g)})),
        "college_eff": tag(backtest.run(CollegeEff(eff_cfg, box), rows,
                                        extra=lambda m, g: dict(zip(("mu_h", "mu_a"), m.expected(g))))),
        "running_points": tag(backtest.run(RunningPoints(), rows,
                                           extra=lambda m, g: dict(zip(("pts_h", "pts_a"), m.expected(g))))),
    }


def _rms(xs: list[float]) -> float:
    return math.sqrt(sum(x * x for x in xs) / len(xs))


def fit_sigmas(preds: dict[str, list[dict]]) -> dict:
    """Residual RMS of each market quantity on the given (development) predictions; rho = residual correlation of margin
    and total for the joint college_eff distribution."""
    eff = preds["college_eff"]
    rm = [(p["home_score"] - p["away_score"]) - (p["mu_h"] - p["mu_a"]) for p in eff]
    rt = [(p["home_score"] + p["away_score"]) - (p["mu_h"] + p["mu_a"]) for p in eff]
    sm, st = _rms(rm), _rms(rt)
    pts = preds["running_points"]
    elo = preds["college_elo"]
    return {"elo_margin": _rms([(p["home_score"] - p["away_score"]) - p["elo_margin"] for p in elo]),
            "eff_margin": sm, "eff_total": st, "eff_rho": sum(a * b for a, b in zip(rm, rt)) / (len(rm) * sm * st),
            "points_total": _rms([(p["home_score"] + p["away_score"]) - (p["pts_h"] + p["pts_a"]) for p in pts]),
            "points_home": _rms([p["home_score"] - p["pts_h"] for p in pts]),
            "points_away": _rms([p["away_score"] - p["pts_a"] for p in pts]), "fitted_on": "development"}


def eff_probs(preds: list[dict], sigma_margin: float) -> list[dict]:
    return [{**p, "p_home": college._phi((p["mu_h"] - p["mu_a"]) / sigma_margin)} for p in preds]


# --- Development ------------------------------------------------------------------------------------------------------
def _ll(preds: list[dict]) -> float:
    return metrics.log_loss([p["p_home"] for p in preds], [p["home_won"] for p in preds])


def pick_elo(trials: list[dict]) -> dict:
    """Lowest development log loss; ties (equal to TIE_DECIMALS) -> MOV off, carry 1.0, smallest K, then smallest H."""
    return min(trials, key=lambda t: (round(t["log_loss"], TIE_DECIMALS), t["config"]["mov"], -t["config"]["carry"],
                                      t["config"]["k"], t["config"]["home_advantage"]))


def pick_eff(trials: list[dict]) -> dict:
    """Lowest development log loss; ties -> smallest eta, largest carry, smallest w0 (fixed before any NCAAB output)."""
    return min(trials, key=lambda t: (round(t["log_loss"], TIE_DECIMALS), t["config"]["eta"], -t["config"]["carry"],
                                      t["config"]["w0"]))


def _ties(trials: list[dict], best: dict) -> int:
    return sum(round(t["log_loss"], TIE_DECIMALS) == round(best["log_loss"], TIE_DECIMALS) for t in trials)


def fit_platt(preds: list[dict]) -> list[float]:
    """Logistic recalibration y ~ a * logit(p) + b by Newton's method (development predictions only)."""
    xs = [math.log(min(1 - 1e-12, max(1e-12, p["p_home"])) / (1 - min(1 - 1e-12, max(1e-12, p["p_home"])))) for p in preds]
    ys = [float(p["home_won"]) for p in preds]
    a, b = 1.0, 0.0
    for _ in range(50):
        g = [0.0, 0.0]
        h = [[0.0, 0.0], [0.0, 0.0]]
        for x, y in zip(xs, ys):
            q = 1 / (1 + math.exp(-(a * x + b)))
            g[0] += (q - y) * x
            g[1] += q - y
            w = q * (1 - q)
            h[0][0] += w * x * x
            h[0][1] += w * x
            h[1][1] += w
        det = h[0][0] * h[1][1] - h[0][1] ** 2
        da = (h[1][1] * g[0] - h[0][1] * g[1]) / det
        db = (h[0][0] * g[1] - h[0][1] * g[0]) / det
        a, b = a - da, b - db
        if abs(da) + abs(db) < 1e-10:
            break
    return [a, b]


def calibrated(preds: list[dict], cal: Optional[list]) -> list[dict]:
    return [{**p, "p_home": college.platt(p["p_home"], cal)} for p in preds]


def tune(rows: list, box: dict) -> dict:
    """Each candidate's grid on development seasons (criterion: development log loss). Reads warm-up/development only."""
    allowed = set(loadable("development"))
    if any(r.game.season not in allowed for r in rows):
        raise NcaabEvaluationError("Tuning may only read warm-up and development seasons")
    elo_trials = []
    for values in itertools.product(*ELO_GRID.values()):
        cfg = CollegeEloConfig(**dict(zip(ELO_GRID, values)))
        preds = in_period(backtest.run(CollegeElo(cfg), rows), "development")
        elo_trials.append({"config": asdict(cfg), "log_loss": _ll(preds), "brier": metrics.brier(
            [p["p_home"] for p in preds], [p["home_won"] for p in preds]), "n": len(preds)})
    elo_best = pick_elo(elo_trials)
    scores = {r.game.game_id: (r.home_score, r.away_score) for r in rows}
    eff_trials = []
    for values in itertools.product(*EFF_GRID.values()):
        cfg = CollegeEffConfig(**dict(zip(EFF_GRID, values)))
        preds = in_period(backtest.run(CollegeEff(cfg, box), rows,
                                       extra=lambda m, g: dict(zip(("mu_h", "mu_a"), m.expected(g)))), "development")
        sm = _rms([(scores[p["game_id"]][0] - scores[p["game_id"]][1]) - (p["mu_h"] - p["mu_a"]) for p in preds])
        probs = eff_probs(preds, sm)
        eff_trials.append({"config": asdict(cfg), "log_loss": _ll(probs), "sigma_margin": sm, "n": len(preds),
                           "brier": metrics.brier([p["p_home"] for p in probs], [p["home_won"] for p in probs])})
    eff_best = pick_eff(eff_trials)
    return {"elo": {"grid": ELO_GRID, "criterion": "development log loss", "tie_rule": "MOV off, carry 1.0, smallest K, smallest H",
                    "selected": elo_best["config"], "selected_log_loss": elo_best["log_loss"],
                    "configs_tied_with_best": _ties(elo_trials, elo_best), "trial_count": len(elo_trials),
                    "trials": sorted(elo_trials, key=lambda t: t["log_loss"])},
            "eff": {"grid": EFF_GRID, "criterion": "development log loss (sigma of margin fitted on development per config)",
                    "tie_rule": "smallest eta, largest carry, smallest w0", "selected": eff_best["config"],
                    "selected_log_loss": eff_best["log_loss"], "configs_tied_with_best": _ties(eff_trials, eff_best),
                    "trial_count": len(eff_trials), "trials": sorted(eff_trials, key=lambda t: t["log_loss"])}}


# --- Scoring ----------------------------------------------------------------------------------------------------------
def _season_starts(preds: list[dict]) -> dict:
    starts: dict = {}
    for p in preds:
        starts[p["season"]] = min(starts.get(p["season"], p["tipoff_at"]), p["tipoff_at"])
    return starts


def _days(a: str, b: str) -> float:
    f = "%Y-%m-%dT%H:%M:%SZ"
    return (datetime.strptime(a, f) - datetime.strptime(b, f)).total_seconds() / 86400


def winner_summary(preds: list[dict]) -> dict:
    if not preds:
        return {"n": 0}
    probs, ys = [p["p_home"] for p in preds], [p["home_won"] for p in preds]
    starts = _season_starts(preds)
    early = [p for p in preds if _days(p["tipoff_at"], starts[p["season"]]) < EARLY_DAYS]
    later = [p for p in preds if _days(p["tipoff_at"], starts[p["season"]]) >= EARLY_DAYS]

    def short(group):
        if not group:
            return {"n": 0}
        pr, y = [p["p_home"] for p in group], [p["home_won"] for p in group]
        return {"n": len(group), "accuracy": metrics.accuracy(pr, y), "brier": metrics.brier(pr, y),
                "log_loss": metrics.log_loss(pr, y), "mean_predicted": sum(pr) / len(pr),
                "observed_home_rate": sum(map(float, y)) / len(y)}
    return {"n": len(preds), "accuracy": metrics.accuracy(probs, ys), "brier": metrics.brier(probs, ys),
            "log_loss": metrics.log_loss(probs, ys),
            "reliability": [b.model_dump() for b in metrics.calibration(probs, ys, bins=metrics.API_CALIBRATION_BINS)],
            "by_season": [{"season": s, **short([p for p in preds if p["season"] == s])}
                          for s in sorted({p["season"] for p in preds})],
            "first_two_weeks": {**short(early), "reliability": [b.model_dump() for b in metrics.calibration(
                [p["p_home"] for p in early], [p["home_won"] for p in early], bins=metrics.API_CALIBRATION_BINS)]},
            "after_two_weeks": short(later),
            "neutral_site": short([p for p in preds if p["neutral_site"]]),
            "home_site": short([p for p in preds if not p["neutral_site"]])}


def paired_log_loss(a: list[dict], b: list[dict]) -> dict:
    """Mean per-game log-loss difference a - b on the same games, seeded ISO-week block bootstrap. Negative favours a."""
    pb = {p["game_id"]: p for p in b}
    diffs = [metrics.log_loss([p["p_home"]], [p["home_won"]]) - metrics.log_loss([pb[p["game_id"]]["p_home"]], [p["home_won"]])
             for p in a]
    return backtest.block_bootstrap(diffs, [p["cutoff_at"] for p in a], seed=BOOTSTRAP_SEED, samples=BOOTSTRAP_SAMPLES)


def _quantity(market: str, h: int, a: int) -> int:
    return {"spread": h - a, "total": h + a, "team_total_home": h, "team_total_away": a}[market]


def _means(p_eff: dict, p_elo: dict, p_pts: dict) -> dict:
    return {"eff": (p_eff["mu_h"], p_eff["mu_a"]), "elo_margin": p_elo["elo_margin"], "points": (p_pts["pts_h"], p_pts["pts_a"])}


def _graded(market: str, line: float, mean: float, sd: float, h: int, a: int) -> tuple:
    side = markets.settle(market, line, status="final", home=h, away=a)
    real = {"a": 0, "mid": 1, "b": 2}[side]
    vec = college.market_probs(market, line, mean, sd)
    return vec, real


def _point_log_score(market: str, x: int, mean: float, sd: float) -> float:
    p = college._phi((x + 0.5 - mean) / sd) - college._phi((x - 0.5 - mean) / sd)
    return -math.log(max(p, 1e-15))


def _deciles(pred: list[float], obs: list[float]) -> list[dict]:
    order = sorted(range(len(pred)), key=lambda i: pred[i])
    out = []
    for k in range(10):
        idx = order[k * len(order) // 10:(k + 1) * len(order) // 10]
        if not idx:
            continue
        mp, mo = sum(pred[i] for i in idx) / len(idx), sum(obs[i] for i in idx) / len(idx)
        out.append({"decile": k + 1, "n": len(idx), "mean_predicted": mp, "mean_observed": mo, "gap": mo - mp})
    return out


def after_game_lines(conn: sqlite3.Connection, game_ids: set) -> dict:
    """game_id -> {spread_home, over_under, provider, retrieved_at}: the earliest after-game line retrieval per game
    (DraftKings first). Labelled retrieved_after_game: a benchmark, never a pregame quote."""
    out: dict = {}
    for r in conn.execute("SELECT game_id, provider, spread_home, over_under, retrieved_at, basis FROM ncaab_after_game_lines "
                          "ORDER BY game_id, provider != 'DraftKings', retrieved_at, line_id"):
        if r[0] in game_ids and r[0] not in out:
            out[r[0]] = {"provider": r[1], "spread_home": r[2], "over_under": r[3], "retrieved_at": r[4], "basis": r[5]}
    return out


def market_eval(market: str, preds: dict[str, list[dict]], sigmas: dict, kinds: tuple, agl: dict) -> tuple[dict, list]:
    """Each market model in `kinds` (the first is college_eff, the second the baseline) graded on the same games at each
    model's own line, at both models' lines, and at the after-game line where present."""
    elo = {p["game_id"]: p for p in preds["college_elo"]}
    pts = {p["game_id"]: p for p in preds["running_points"]}
    games = preds["college_eff"]
    res: dict = {k: {"own": [], "common": [], "point": [], "pred": [], "obs": [], "agl": []} for k in kinds}
    diffs, diff_point, cutoffs, rows_out = [], [], [], []
    for p in games:
        gid, h, a = p["game_id"], p["home_score"], p["away_score"]
        means = _means(p, elo[gid], pts[gid])
        mom = {k: college.market_moments(k, means, sigmas, market) for k in kinds}
        own = {k: college.own_line(market, mom[k][0]) for k in kinds}
        x = _quantity(market, h, a)
        per_game = {}
        for k in kinds:
            mean, sd = mom[k]
            vec, real = _graded(market, own[k], mean, sd, h, a)
            res[k]["own"].append((vec, real))
            rows_out.append([market, k, gid, p["season"], p["cutoff_at"], "own_line", own[k], *vec, real])
            common = []
            for j in kinds:
                v, r = _graded(market, own[j], mean, sd, h, a)
                common.append((v, r))
                res[k]["common"].append((v, r))
                if j != k:
                    rows_out.append([market, k, gid, p["season"], p["cutoff_at"], f"{j}_line", own[j], *v, r])
            per_game[k] = sum(metrics.log_loss_multi([v], [r]) for v, r in common) / len(common)
            res[k]["point"].append(_point_log_score(market, x, mean, sd))
            res[k]["pred"].append(mean)
            res[k]["obs"].append(x)
            line = None
            if gid in agl:
                line = agl[gid]["spread_home"] if market == "spread" else agl[gid]["over_under"] if market == "total" else None
            if line is not None:
                v, r = _graded(market, float(line), mean, sd, h, a)
                res[k]["agl"].append((v, r, float(line), x, mean))
                rows_out.append([market, k, gid, p["season"], p["cutoff_at"], "after_game_line", line, *v, r])
        diffs.append(per_game[kinds[0]] - per_game[kinds[1]])
        diff_point.append(res[kinds[0]]["point"][-1] - res[kinds[1]]["point"][-1])
        cutoffs.append(p["cutoff_at"])
    out: dict = {"n": len(games), "models": {}}
    for k in kinds:
        r = res[k]
        dec = _deciles(r["pred"], r["obs"])
        gap = max(abs(d["gap"]) for d in dec) if dec else None
        ag = r["agl"]
        out["models"][k] = {
            "own_line": {"log_loss": metrics.log_loss_multi(*zip(*r["own"])), "brier": metrics.brier_multi(*zip(*r["own"]))},
            "both_lines": {"events": len(r["common"]), "log_loss": metrics.log_loss_multi(*zip(*r["common"])),
                           "brier": metrics.brier_multi(*zip(*r["common"]))},
            "point_log_score": sum(r["point"]) / len(r["point"]),
            "errors": metrics.regression_errors(r["pred"], r["obs"]),
            "deciles": dec, "max_decile_gap": gap, "calibrated": gap is not None and gap <= CALIBRATION_POINTS,
            "after_game_line": None if not ag else {
                "games": len(ag), "log_loss": metrics.log_loss_multi([v for v, *_ in ag], [x for _, x, *_ in ag]),
                "brier": metrics.brier_multi([v for v, *_ in ag], [x for _, x, *_ in ag]),
                "model_mae": sum(abs(q[3] - q[4]) for q in ag) / len(ag),
                "line_mae": sum(abs(q[3] - (-q[2] if market == "spread" else q[2])) for q in ag) / len(ag)}}
    out["eff_minus_baseline_both_lines_log_loss"] = backtest.block_bootstrap(diffs, cutoffs, seed=BOOTSTRAP_SEED,
                                                                             samples=BOOTSTRAP_SAMPLES) if diffs else None
    out["eff_minus_baseline_point_log_score"] = backtest.block_bootstrap(diff_point, cutoffs, seed=BOOTSTRAP_SEED,
                                                                         samples=BOOTSTRAP_SAMPLES) if diffs else None
    n_agl = sum(1 for p in games if p["game_id"] in agl)
    out["after_game_lines"] = {"games": n_agl, "basis": "retrieved_after_game (benchmark only, not a pregame quote)",
                               "graded_markets": "spread (spread_home) and total (over_under); none for team totals"}
    return out, rows_out


def market_decision(m: dict, eff: str = "college_eff") -> str:
    kinds = list(m["models"])
    base = [k for k in kinds if k != eff][0]
    ll = {k: m["models"][k]["both_lines"]["log_loss"] for k in kinds}
    preferred, other = (eff, base) if ll[eff] < ll[base] else (base, eff)
    if m["models"][preferred]["calibrated"]:
        return preferred
    if m["models"][other]["calibrated"]:
        return other
    return "research"


def score(preds_all: dict[str, list[dict]], period: str, sigmas: dict, cals: dict, agl: dict) -> tuple[dict, list, dict]:
    preds = {k: in_period(v, period) for k, v in preds_all.items()}
    winner = {"constant": preds["constant"], "home_rate": preds["home_rate"], "college_elo": preds["college_elo"],
              "college_eff": eff_probs(preds["college_eff"], sigmas["eff_margin"])}
    for name in ("college_elo", "college_eff"):
        if cals.get(name):
            winner[name + "+platt"] = calibrated(winner[name], cals[name])
    out = {"winner": {k: winner_summary(v) for k, v in winner.items()}, "markets": {}}
    rows_out = []
    for market, base in BASELINE.items():
        m, r = market_eval(market, preds, sigmas, ("college_eff", base), agl)
        out["markets"][market] = m
        rows_out += r
    ag = [p for p in winner["college_eff"] if p["game_id"] in agl and agl[p["game_id"]]["spread_home"] is not None]
    if ag:
        bench = [{**p, "p_home": college._phi(-agl[p["game_id"]]["spread_home"] / sigmas["eff_margin"])} for p in ag]
        ids = {p["game_id"] for p in ag}
        out["winner_after_game_benchmark"] = {
            "games": len(ag), "basis": "after-game DraftKings spread turned into a win probability with the development "
                                       "sigma of margin (retrieved after the game; benchmark only)",
            "line": winner_summary(bench),
            "models": {k: winner_summary([p for p in v if p["game_id"] in ids]) for k, v in winner.items()}}
    else:
        out["winner_after_game_benchmark"] = {"games": 0, "note": "no after-game line in this period"}
    return out, rows_out, winner


def coverage(rows: list, box: dict, agl: dict) -> dict:
    out: dict = {}
    for r in rows:
        s = out.setdefault(r.game.season, {"games": 0, "with_fga_fta": 0, "neutral": 0, "postseason": 0, "after_game_line": 0})
        s["games"] += 1
        s["with_fga_fta"] += r.game.game_id in box
        s["neutral"] += r.game.neutral_site
        s["postseason"] += r.game.season_type == "postseason"
        s["after_game_line"] += r.game.game_id in agl
    return {str(k): v for k, v in sorted(out.items())}


def _rows_version(rows: list, seasons) -> str:
    return college.rows_version([r for r in rows if r.game.season in set(seasons)])


# --- Stages -------------------------------------------------------------------------------------------------------------
def _write(report: dict, winner_preds: dict, market_rows: list, out_dir: Path, log) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report["output_dir"] = str(out_dir)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, default=str))
    with (out_dir / "predictions.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["period", "model", "game_id", "season", "season_type", "tipoff_at", "cutoff_at", "neutral_site", "p_home",
                    "home_won", "home_score", "away_score", "mu_home", "mu_away", "elo_margin"])
        for period, models in winner_preds.items():
            for name, preds in models.items():
                for p in preds:
                    w.writerow([period, name, p["game_id"], p["season"], p["season_type"], p["tipoff_at"], p["cutoff_at"],
                                int(p["neutral_site"]), f"{p['p_home']:.6f}", int(p["home_won"]), p["home_score"], p["away_score"],
                                p.get("mu_h", ""), p.get("mu_a", ""), p.get("elo_margin", "")])
    with (out_dir / "markets.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["period", "market", "model", "game_id", "season", "cutoff_at", "line_source", "line", "p_a", "p_push", "p_b",
                    "realised_0a_1push_2b"])
        w.writerows(market_rows)
    log(f"Report written to {out_dir}")
    return out_dir


def _development(conn, cutoff):
    rows, box = load(conn, "development", cutoff)
    tuning = tune(rows, box)
    elo_cfg, eff_cfg = CollegeEloConfig(**tuning["elo"]["selected"]), CollegeEffConfig(**tuning["eff"]["selected"])
    preds = walk(rows, box, elo_cfg, eff_cfg)
    dev = {k: in_period(v, "development") for k, v in preds.items()}
    sigmas = fit_sigmas(dev)
    cals = {"college_elo": fit_platt(dev["college_elo"]),
            "college_eff": fit_platt(eff_probs(dev["college_eff"], sigmas["eff_margin"]))}
    return rows, box, tuning, elo_cfg, eff_cfg, preds, sigmas, cals


def evaluate_development(conn: sqlite3.Connection, *, out_dir: Path, cutoff: str = DEFAULT_CUTOFF, command: str = "",
                         log: Callable[[str], None] = print) -> dict:
    started = utc_now()
    rows, box, tuning, elo_cfg, eff_cfg, preds, sigmas, cals = _development(conn, cutoff)
    agl = after_game_lines(conn, {r.game.game_id for r in rows})
    dev, dev_rows, dev_winner = score(preds, "development", sigmas, cals, agl)
    report = {"sport": "ncaab", "stage": "development", "generated_at": started, "cutoff": cutoff, "command": command,
              "seasons": {k: PERIODS[k] for k in ("warmup", "development")}, "feature_version": FEATURE_VERSION,
              "dataset_version": _rows_version(rows, loadable("development")), "coverage": coverage(rows, box, agl),
              "tuning": tuning, "sigmas": sigmas, "platt_fits": cals,
              "development": {**dev, "note": "in-sample for sigma and the grids"}}
    _write(report, {"development": dev_winner}, [["development", *r] for r in dev_rows], out_dir, log)
    return report


def evaluate_validation(conn: sqlite3.Connection, *, decision_path: Optional[Path] = None, out_dir: Path,
                        cutoff: str = DEFAULT_CUTOFF, command: str = "", log: Callable[[str], None] = print) -> dict:
    decision_path = Path(decision_path or college.COMMITTED_DECISION_PATH)
    if decision_path.exists():
        raise NcaabEvaluationError(f"{decision_path} exists: the NCAAB validation stage was already run and sealed")
    started = utc_now()
    dev_rows, _, tuning, elo_cfg, eff_cfg, _, sigmas, cals = _development(conn, cutoff)
    rows, box = load(conn, "validation", cutoff)
    preds = walk(rows, box, elo_cfg, eff_cfg)
    agl = after_game_lines(conn, {r.game.game_id for r in rows})
    dev, dev_market_rows, dev_winner = score(preds, "development", sigmas, cals, agl)
    val, val_market_rows, val_winner = score(preds, "validation", sigmas, cals, agl)
    # winner selection (tuned candidates, uncalibrated), then the calibration layer only if it improves validation
    lls = {k: val["winner"][k]["log_loss"] for k in WINNER_CANDIDATES}
    best = min(WINNER_CANDIDATES, key=lambda k: (round(lls[k], TIE_DECIMALS), k != "college_elo"))
    boot = {k: paired_log_loss(val_winner[k], val_winner["college_elo"]) for k in WINNER_CANDIDATES if k != "college_elo"}
    chosen = best if best != "college_elo" and boot[best]["ci95"][1] < 0 else "college_elo"
    cal_ll = val["winner"].get(chosen + "+platt", {}).get("log_loss")
    use_cal = cal_ll is not None and cal_ll < val["winner"][chosen]["log_loss"]
    selection = {"rule": SELECTION_RULE, "validation_log_loss": lls, "best": best, "selected": chosen,
                 "winner_minus_elo_log_loss": boot,
                 "winner_minus_elo_brier": {k: backtest.paired_bootstrap(val_winner[k], val_winner["college_elo"], seed=BOOTSTRAP_SEED,
                                                                         samples=BOOTSTRAP_SAMPLES)
                                            for k in WINNER_CANDIDATES if k != "college_elo"},
                 "calibration_layer": {"rule": "development-fit Platt only if it improves validation log loss",
                                       "development_fit": cals.get(chosen), "validation_log_loss_with": cal_ll,
                                       "validation_log_loss_without": val["winner"][chosen]["log_loss"], "applied": use_cal}}
    val["selection"] = selection
    market_choice = {}
    for market, m in val["markets"].items():
        m["rule"] = MARKET_RULE
        m["decision"] = market_decision(m)
        market_choice[market] = m["decision"]
    version = _rows_version(rows, PERIODS["validation"])
    decision = {"sport": "ncaab", "winner_model": chosen, "winner_calibration": cals[chosen] if use_cal else None,
                "college_elo_config": asdict(elo_cfg), "college_eff_config": asdict(eff_cfg), "sigmas": sigmas,
                "markets": {m: {"model": k, **({} if k == "research" else {"model_version": None})} for m, k in market_choice.items()},
                "validation_dataset_version": version, "validation_seasons": list(PERIODS["validation"]),
                "development_dataset_version": _rows_version(dev_rows, loadable("development")),
                "selected_on": "validation", "data_cutoff": cutoff, "feature_version": FEATURE_VERSION,
                "training_command": command, "frozen_at": started, "validation_report": str(out_dir)}
    decision["model_version"] = college.winner_version(decision)
    for m, spec in decision["markets"].items():
        if spec["model"] != "research":
            spec["model_version"] = college.market_version(decision, spec["model"])
    decision = college.seal(decision)
    report = {"sport": "ncaab", "stage": "validation", "generated_at": started, "cutoff": cutoff, "command": command,
              "seasons": PERIODS, "feature_version": FEATURE_VERSION, "dataset_version": version,
              "loaded_dataset_version": _rows_version(rows, loadable("validation")), "coverage": coverage(rows, box, agl),
              "tuning": tuning, "sigmas": sigmas, "platt_fits": cals,
              "development": {**dev, "note": "development seasons, in-sample for the grids and sigma"},
              "validation": val, "decision": decision}
    _write(report, {"development": dev_winner, "validation": val_winner},
           [["development", *r] for r in dev_market_rows] + [["validation", *r] for r in val_market_rows], out_dir, log)
    decision_path.write_text(json.dumps(decision, indent=2) + "\n")
    return report


def test_access_path() -> Path:
    return config.data_dir() / "eval" / "test_access_ncaab.jsonl"


def evaluate_test(conn: sqlite3.Connection, *, decision_path: Optional[Path] = None, out_dir: Path,
                  cutoff: str = DEFAULT_CUTOFF, command: str = "", log: Callable[[str], None] = print) -> dict:
    d = college.load_decision(decision_path or college.COMMITTED_DECISION_PATH)
    val_rows, _ = load(conn, "validation", cutoff)          # no test-season row is read before the access is logged
    if _rows_version(val_rows, d["validation_seasons"]) != d["validation_dataset_version"]:
        raise college.FrozenDecisionError("NCAAB validation data changed since the decision was sealed")
    access = test_access_path()
    if access.exists() and access.read_text().strip():
        raise NcaabEvaluationError(f"The NCAAB test period was already accessed ({access}); it is not rerun")
    started = utc_now()
    access.parent.mkdir(parents=True, exist_ok=True)
    with access.open("a") as f:
        f.write(json.dumps({"at": started, "stage": "test", "seasons": list(PERIODS["test"]), "decision_sha256": d["sha256"],
                            "model_version": d["model_version"], "validation_dataset_version": d["validation_dataset_version"],
                            "cutoff": cutoff, "command": command, "out_dir": str(out_dir)}) + "\n")
    rows, box = load(conn, "test", cutoff)
    preds = walk(rows, box, CollegeEloConfig(**d["college_elo_config"]), CollegeEffConfig(**d["college_eff_config"]))
    agl = after_game_lines(conn, {r.game.game_id for r in rows})
    cal = {d["winner_model"]: d["winner_calibration"]} if d.get("winner_calibration") else {}
    test, market_rows, winner = score(preds, "test", d["sigmas"], cal, agl)
    shipped = d["winner_model"] + ("+platt" if cal else "")
    test["primary_winner_model"] = {"name": shipped, "model_version": d["model_version"]}
    test["descriptive"] = {"note": "Descriptive only; the choice was sealed on validation.",
                           **{f"{k}_minus_elo_log_loss": paired_log_loss(winner[k], winner["college_elo"])
                              for k in winner if k != "college_elo"}}
    for market, m in test["markets"].items():
        m["shipped"] = d["markets"][market]["model"]
    report = {"sport": "ncaab", "stage": "test", "generated_at": started, "cutoff": cutoff, "command": command,
              "seasons": PERIODS["test"], "feature_version": d["feature_version"],
              "dataset_version": _rows_version(rows, PERIODS["test"]), "loaded_dataset_version": _rows_version(rows, loadable("test")),
              "coverage": {k: v for k, v in coverage(rows, box, agl).items() if int(k) in PERIODS["test"]},
              "decision": d, "test": test}
    _write(report, {"test": winner}, [["test", *r] for r in market_rows], out_dir, log)
    return report


# --- Research adapter (all-sports research system) ----------------------------------------------------------------------
# Research only: nothing here writes a store, changes a live model, a forecast, a card or an official record. Candidate names
# are new model identities ("ncaab-cand-*"); the incumbent is the sealed production decision, reproduced unchanged.
RESEARCH_MARKETS = ("winner",) + college.LINE_MARKETS
FAR = "9999-12-31T00:00:00Z"
MIN_FIT_ROWS = 300              # a candidate is fitted for a season only from at least this many earlier games, else it IS the incumbent
MIN_PHASE_ROWS = 200
EARLY_GAMES = 6                 # "early season": the fewer-played team has played fewer than this many games this season
HETERO_SHRINK = 300             # rows of weight the pooled sigma carries against a cell's own (phase x level) sigma
QUOTE_PROVIDER = "DraftKings"
CANDIDATE_KINDS = {"ncaab-cand-blend-v1": "blend", "ncaab-cand-phase-v1": "phase", "ncaab-cand-hetero-v1": "hetero"}
MODEL_SPECS = {
    "ncaab-incumbent": {"role": "incumbent", "version": "sealed-decision",
                        "description": "The production decision: winner (Platt-calibrated) and each line market from the model the "
                                       "sealed decision names (college_eff margin/team totals, running_points total), frozen sigmas."},
    "ncaab-baseline-coin": {"role": "baseline", "version": "coin-v1", "description": "Every side 50 % (no push mass)."},
    "ncaab-baseline-elo": {"role": "baseline", "version": "elo-runningpoints-v1",
                           "description": "Market-free baselines of the preregistered plan: college_elo winner and margin/12, "
                                          "running_points totals and team totals."},
    "ncaab-baseline-league": {"role": "baseline", "version": "league-v1",
                              "description": "Home advantage only (0 at neutral sites) for winner and margin; league-average total "
                                             "(and half of it per team); frozen sigmas."},
    "ncaab-cand-blend-v1": {"role": "candidate", "version": "blend-v1",
                            "description": "Margin and total means = OLS blend of college_eff and the Elo margin / running-points "
                                           "total, coefficients and residual sigma refitted each season on earlier seasons only."},
    "ncaab-cand-phase-v1": {"role": "candidate", "version": "phase-v1",
                            "description": "As blend, with separate coefficients and sigma for early-season games (a team has played "
                                           f"fewer than {EARLY_GAMES} games) and established ones."},
    "ncaab-cand-hetero-v1": {"role": "candidate", "version": "hetero-v1",
                             "description": "Blend means; sigma varies with games played (<4, <10, 10+) and predicted level (terciles of "
                                            "|margin| / total), shrunk to the pooled sigma, fitted on earlier seasons only."},
}


def research_periods() -> dict:
    p = PERIODS
    return {"warmup": tuple(p["warmup"]), "search": tuple(p["development"]), "validation": tuple(p["validation"]),
            "holdout": tuple(p["test"])}


def forward_from() -> int:
    return max(PERIODS["test"]) + 1


def research_adapter() -> dict:
    """What the research system consumes (docs/AUTORESEARCH.md): markets with settlement rules, periods, models, `walk` and
    `forecast_upcoming`. The input hash covers the sealed decision, the feature version and every model's spec."""
    try:
        sha = college.load_decision()["sha256"]
    except (college.FrozenDecisionError, OSError):
        sha = "no-sealed-decision"
    inputs = json.dumps({"feature": FEATURE_VERSION, "decision": sha, "models": MODEL_SPECS, "periods": research_periods()},
                        sort_keys=True)
    ot = "overtime counts (full-game score); neutral-site games keep the stored home/away; "
    return {
        "sport": "ncaab",
        "data_version": hashlib.sha256(inputs.encode()).hexdigest()[:16],
        "markets": {
            "winner": {"kind": "binary", "settlement": ot + "home wins if stored home score > away score (a tie voids)"},
            "spread": {"kind": "line", "settlement": ot + "home handicap: home covers (a) when home - away + line > 0, away (b) "
                                                      "when < 0, push on exactly 0 (whole-number lines only)"},
            "total": {"kind": "line", "settlement": ot + "over (a) when home + away > line, under (b) when <, push on equal"},
            "team_total_home": {"kind": "line", "settlement": ot + "home points over (a) / under (b) the line, push on equal"},
            "team_total_away": {"kind": "line", "settlement": ot + "away points over (a) / under (b) the line, push on equal"},
        },
        "unsupported": {},
        "caveats": {
            "line_source": "No historical NCAAB bookmaker line exists before 2026-11-17 (market_quotes) and the after-game DraftKings "
                           "lines cover only 2025-11-24 onward (the holdout). Search/validation records are therefore graded at "
                           "a market-free REFERENCE line (line_source 'reference_model': the Elo-margin / running-points line, "
                           "shared by every model so comparisons are paired) - never presented as a bookmaker result. A record "
                           "with a DraftKings quote retrieved at or before its cutoff uses that line (line_source 'bookmaker').",
            "team_total_home": "No bookmaker price for team totals is stored for any game: reference-line results only.",
            "team_total_away": "No bookmaker price for team totals is stored for any game: reference-line results only.",
            "validation_reuse": "2022-2024 already selected the production decision; candidate parameters are fitted on earlier "
                                "seasons only, but the validation look is a second look, not a fresh holdout.",
        },
        "periods": {k: list(v) for k, v in research_periods().items()},      # warmup = burn-in, never scored
        "models": {n: dict(s) for n, s in MODEL_SPECS.items()},
        "walk": research_walk,
        "forecast_upcoming": research_forecast_upcoming,
        "validate_data": validate_data,
    }


# Features: everything a model may know at a game's cutoff, from one chronological bundle of rating states.
class _Bundle:
    name = "ncaab-research-bundle"

    def __init__(self, decision: dict, box: dict):
        self.elo = CollegeElo(CollegeEloConfig(**decision["college_elo_config"]))
        self.eff = CollegeEff(CollegeEffConfig(**decision["college_eff_config"]), box)
        self.pts = RunningPoints()
        self.n: dict = {}
        self.season: Optional[int] = None
        self.total = [0.0, 0]

    def update(self, result) -> None:
        g = result.game
        self.elo.update(result)
        self.eff.update(result)
        self.pts.update(result)
        if g.season != self.season:
            self.n, self.season = {}, g.season
        for team in (g.home_team_id, g.away_team_id):
            self.n[team] = self.n.get(team, 0) + 1
        self.total[0] += result.home_score + result.away_score
        self.total[1] += 1

    def predict(self, game) -> float:
        return 0.5

    def config(self) -> dict:
        return {}

    def features(self, game) -> dict:
        mh, ma = self.eff.expected(game)
        ph, pa = self.pts.expected(game)
        same = game.season == self.season
        return {"elo_margin": self.elo.margin(game), "elo_p": self.elo.predict(game), "mu_h": mh, "mu_a": ma, "pts_h": ph,
                "pts_a": pa, "n_h": self.n.get(game.home_team_id, 0) if same else 0,
                "n_a": self.n.get(game.away_team_id, 0) if same else 0,
                "league_total": self.total[0] / self.total[1] if self.total[1] else 140.0, "ha": self.eff.ha,
                "neutral": bool(game.neutral_site)}


_BASE_CACHE: dict = {}


def clear_research_cache() -> None:
    _BASE_CACHE.clear()


def _base(conn: sqlite3.Connection, last: int) -> tuple[list, dict]:
    """Chronological per-game features (backtest.run: a result reaches the bundle only once available at the game's cutoff)
    for every eligible game of the warm-up through `last`, plus the sealed decision. Scores ride along for fitting EARLIER
    seasons and for settlement only."""
    if store_sport(conn) != "ncaab":
        raise NcaabEvaluationError("NCAAB research needs the NCAAB store")
    d = college.load_decision()
    rows, box = college.rows_at(conn, FAR, seasons=range(min(PERIODS["warmup"]), last + 1))
    key = (last, college.rows_version(rows), d["sha256"])
    if key not in _BASE_CACHE:
        scores = {r.game.game_id: (r.home_score, r.away_score) for r in rows}
        out = backtest.run(_Bundle(d, box), rows, extra=lambda m, g: m.features(g))
        for p in out:
            p["home_score"], p["away_score"] = scores[p["game_id"]]
        _BASE_CACHE.clear()
        _BASE_CACHE[key] = out
    return _BASE_CACHE[key], d


def _lstsq(X, y):
    return np.linalg.lstsq(X, y, rcond=None)[0]


def _xrow(market: str, f: dict) -> tuple:
    if market == "spread":
        return (1.0, f["mu_h"] - f["mu_a"], f["elo_margin"])
    return (1.0, f["mu_h"] + f["mu_a"], f["pts_h"] + f["pts_a"])


def _target(market: str, f: dict) -> float:
    return f["home_score"] - f["away_score"] if market == "spread" else f["home_score"] + f["away_score"]


def _phase(kind: str, f: dict) -> int:
    n = min(f["n_h"], f["n_a"])
    if kind == "hetero":
        return 0 if n < 4 else 1 if n < 10 else 2
    return 0 if kind == "blend" else int(n >= EARLY_GAMES)


class CandFit:
    """A candidate fitted on `train` (feature dicts WITH scores, all from seasons before the games it will price)."""

    def __init__(self, kind: str, train: list):
        self.kind = kind
        self.coef: dict = {}
        self.sd: dict = {}
        self.cuts: dict = {}
        ph = np.array([_phase(kind, f) for f in train])
        mean_pred = {}
        for market in ("spread", "total"):
            X = np.array([_xrow(market, f) for f in train])
            y = np.array([_target(market, f) for f in train], dtype=float)
            glob = _lstsq(X, y)
            coefs = {}
            for p in (0, 1, 2):
                mask = ph == p
                coefs[p] = _lstsq(X[mask], y[mask]) if kind == "phase" and mask.sum() >= MIN_PHASE_ROWS else glob
            self.coef[market] = coefs
            pred = np.array([X[i] @ coefs[ph[i]] for i in range(len(train))])
            mean_pred[market] = pred
            res = y - pred
            sg = _rms(res)
            if kind == "hetero":
                level = np.abs(pred) if market == "spread" else pred
                self.cuts[market] = [float(c) for c in np.quantile(level, [1 / 3, 2 / 3])]
                lv = np.digitize(level, self.cuts[market])
                cells = {}
                for p in (0, 1, 2):
                    for q in (0, 1, 2):
                        m = (ph == p) & (lv == q)
                        n = int(m.sum())
                        s2 = float(np.mean(res[m] ** 2)) if n else sg * sg
                        cells[(p, q)] = math.sqrt((n * s2 + HETERO_SHRINK * sg * sg) / (n + HETERO_SHRINK))
                self.sd[market] = cells
            elif kind == "phase":
                self.sd[market] = {p: (_rms(res[ph == p]) if (ph == p).sum() >= MIN_PHASE_ROWS else sg) for p in (0, 1, 2)}
            else:
                self.sd[market] = {p: sg for p in (0, 1, 2)}
        mh = (mean_pred["total"] + mean_pred["spread"]) / 2
        ma = (mean_pred["total"] - mean_pred["spread"]) / 2
        hs = np.array([f["home_score"] for f in train], dtype=float)
        as_ = np.array([f["away_score"] for f in train], dtype=float)
        sg_t = _rms(np.concatenate([hs - mh, as_ - ma]))
        self.sd_team = {p: (_rms(np.concatenate([(hs - mh)[ph == p], (as_ - ma)[ph == p]]))
                            if kind != "blend" and (ph == p).sum() >= MIN_PHASE_ROWS else sg_t) for p in (0, 1, 2)}

    def moments(self, f: dict) -> dict:
        p = _phase(self.kind, f)
        out, mean = {}, {}
        for market in ("spread", "total"):
            x = _xrow(market, f)
            mean[market] = float(sum(a * b for a, b in zip(x, self.coef[market][p])))
            if self.kind == "hetero":
                level = abs(mean[market]) if market == "spread" else mean[market]
                sd = self.sd[market][(p, int(np.digitize(level, self.cuts[market])))]
            else:
                sd = self.sd[market][p]
            out[market] = (mean[market], sd)
        out["team_total_home"] = ((mean["total"] + mean["spread"]) / 2, self.sd_team[p])
        out["team_total_away"] = ((mean["total"] - mean["spread"]) / 2, self.sd_team[p])
        out["winner"] = college._phi(out["spread"][0] / out["spread"][1])
        return out


class _FitBook:
    """Candidate fits by (kind, season), from base records of seasons strictly before it (and never after the last validation season)."""

    def __init__(self, base: list):
        self.base, self.cache = base, {}
        self.first = max(PERIODS["warmup"])
        self.last = max(PERIODS["validation"])

    def get(self, kind: str, season: int):
        key = (kind, season)
        if key not in self.cache:
            train = [f for f in self.base if self.first <= f["season"] < season and f["season"] <= self.last]
            self.cache[key] = (CandFit(kind, train), max(f["season"] for f in train)) if len(train) >= MIN_FIT_ROWS else (None, None)
        return self.cache[key]


def _incumbent(f: dict, d: dict) -> dict:
    sig = d["sigmas"]
    means = {"elo_margin": f["elo_margin"], "eff": (f["mu_h"], f["mu_a"]), "points": (f["pts_h"], f["pts_a"])}
    if d["winner_model"] == "college_elo":
        p = f["elo_p"]
    else:
        p = college._phi((f["mu_h"] - f["mu_a"]) / sig["eff_margin"])
    out = {"winner": float(college.platt(p, d.get("winner_calibration")))}
    for market in college.LINE_MARKETS:
        kind = d["markets"][market]["model"]
        if kind != "research":
            out[market] = college.market_moments(kind, means, sig, market)
    return out


def _baseline_moments(name: str, f: dict, d: dict) -> dict:
    sig = d["sigmas"]
    if name == "ncaab-baseline-coin":
        return {"winner": 0.5, **{m: "coin" for m in college.LINE_MARKETS}}
    ha = 0.0 if f["neutral"] else f["ha"]
    if name == "ncaab-baseline-elo":
        means = {"elo_margin": f["elo_margin"], "points": (f["pts_h"], f["pts_a"])}
        out = {"winner": f["elo_p"], "spread": college.market_moments("elo_margin", means, sig, "spread")}
        for m in ("total", "team_total_home", "team_total_away"):
            out[m] = college.market_moments("running_points", means, sig, m)
        return out
    half = f["league_total"] / 2
    return {"winner": college._phi(ha / sig["eff_margin"]), "spread": (ha, sig["eff_margin"]),
            "total": (f["league_total"], sig["points_total"]), "team_total_home": (half + ha / 2, sig["points_home"]),
            "team_total_away": (half - ha / 2, sig["points_away"])}


def _reference_lines(f: dict) -> dict:
    """The market-free reference line of each line market (shared by every model): the baselines' own half-point lines."""
    return {"spread": college.own_line("spread", f["elo_margin"]), "total": college.own_line("total", f["pts_h"] + f["pts_a"]),
            "team_total_home": college.own_line("team_total_home", f["pts_h"]),
            "team_total_away": college.own_line("team_total_away", f["pts_a"])}


def _moments(name: str, f: dict, d: dict, book: "_FitBook") -> tuple[dict, Optional[int]]:
    if name == "ncaab-incumbent":
        return _incumbent(f, d), None
    if name in CANDIDATE_KINDS:
        fit, through = book.get(CANDIDATE_KINDS[name], f["season"])
        if fit is None:
            return _incumbent(f, d), None
        return fit.moments(f), through
    return _baseline_moments(name, f, d), None


def _quotes(conn: sqlite3.Connection) -> dict:
    """(game_id, market) -> [(retrieved_at, quote dict)] of DraftKings 'current' spread/total quotes, oldest first."""
    if not has_table(conn, "market_quotes"):
        return {}
    out: dict = {}
    for r in conn.execute("SELECT game_id, market, line, price_a, price_b, p_a_devig, provider, retrieved_at FROM market_quotes "
                          "WHERE provider = ? AND price_label = 'current' AND market IN ('spread', 'total') AND line IS NOT NULL "
                          "ORDER BY retrieved_at, quote_id", (QUOTE_PROVIDER,)):
        out.setdefault((r[0], r[1]), []).append((r[7], {"provider": r[6], "retrieved_at": r[7], "price_a": r[3], "price_b": r[4],
                                                        "line": r[2], "p_a_devig": r[5], "executable": False,
                                                        "synthetic": False}))
    return out


def _quote_at(quotes: dict, game_id: str, market: str, at: str) -> Optional[dict]:
    best = None
    for t, q in quotes.get((game_id, market), ()):
        if t <= at:
            best = q
    return best


def _records(f: dict, name: str, d: dict, book: "_FitBook", quotes: dict, *, outcome: bool, cutoff: str) -> list[dict]:
    mm, through = _moments(name, f, d, book)
    ref = _reference_lines(f)
    base = {"game_id": f["game_id"], "season": f["season"], "start_at": f["tipoff_at"], "forecast_cutoff": cutoff, "model": name,
            "fit_through": through}
    out = []
    for market in RESEARCH_MARKETS:
        if market not in mm:
            continue
        hs, as_ = f.get("home_score"), f.get("away_score")
        if market == "winner":
            p = min(1 - 1e-9, max(1e-9, float(mm["winner"])))
            res = None if not outcome else ("void" if hs == as_ else "a" if hs > as_ else "b")
            out.append({**base, "market": "winner", "line": None, "line_source": None, "probs": {"a": p, "b": 1 - p},
                        "outcome": res, "point_pred": None, "point_actual": None, "quote": None})
            continue
        q = _quote_at(quotes, f["game_id"], market, cutoff) if market in ("spread", "total") else None
        line = float(q["line"]) if q else ref[market]
        if mm[market] == "coin":
            probs, mean, sd = (0.5, 0.0, 0.5), None, None
        else:
            mean, sd = mm[market]
            probs = college.market_probs(market, line, mean, sd)
        actual = None
        res = None
        if outcome:
            side = markets.settle(market, line, status="final", home=hs, away=as_)
            res = "push" if side == "mid" else side
            actual = {"spread": hs - as_, "total": hs + as_, "team_total_home": hs, "team_total_away": as_}[market]
        out.append({**base, "market": market, "line": line, "line_source": "bookmaker" if q else "reference_model",
                    "probs": {"a": probs[0], "mid": probs[1], "b": probs[2]}, "outcome": res, "point_pred": mean,
                    "point_actual": actual, "sd": sd, "quote": q})
    return out


def _check_model(name: str) -> None:
    if name not in MODEL_SPECS:
        raise NcaabEvaluationError(f"unknown research model {name!r}")


def research_walk(conn: sqlite3.Connection, seasons, model_name: str) -> list[dict]:
    """Chronological forecast records of `model_name` for the games of `seasons` (search, validation, warm-up or forward
    seasons; the holdout is refused). Each forecast uses only results available at its cutoff (backtest.run) and, for
    candidates, parameters fitted on strictly earlier seasons."""
    _check_model(model_name)
    seasons = tuple(sorted(set(seasons)))
    holdout = set(research_periods()["holdout"])
    if not seasons or set(seasons) & holdout:
        raise NcaabEvaluationError(f"seasons {sorted(set(seasons) & holdout) or 'none'}: the holdout is not opened by a walk")
    base, d = _base(conn, max(seasons))
    book = _FitBook(base)
    wanted = set(seasons)
    quotes = _quotes(conn)
    out = []
    for f in base:
        if f["season"] in wanted:
            out += _records(f, model_name, d, book, quotes, outcome=True, cutoff=f["cutoff_at"])
    return out


def research_forecast_upcoming(conn: sqlite3.Connection, now: str, model_name: str) -> list[dict]:
    """Records (no outcome) for every scheduled Division I game starting after `now`, from results available at `now` and
    candidate parameters fitted on seasons up to the last validation season only."""
    _check_model(model_name)
    if store_sport(conn) != "ncaab":
        raise NcaabEvaluationError("NCAAB research needs the NCAAB store")
    base, d = _base(conn, max(PERIODS["validation"]))
    book = _FitBook(base)
    rows, box = college.rows_at(conn, now)
    bundle = _Bundle(d, box)
    for r in sorted(rows, key=lambda r: (r.result_available_at, r.game.tipoff_at, r.game.game_id)):
        bundle.update(r.result)
    quotes = _quotes(conn)
    out = []
    q = ("SELECT g.* FROM games g JOIN ncaab_game_context c ON c.game_id = g.game_id WHERE g.status = 'scheduled' AND c.both_d1 = 1 "
         "AND g.season_type IN ('regular', 'postseason') AND g.tipoff_at > ? ORDER BY g.tipoff_at, g.game_id")
    for g in conn.execute(q, (now,)):
        game = PregameGame(g["game_id"], g["season"], g["season_type"], g["tipoff_at"], g["home_team_id"], g["away_team_id"],
                           bool(g["neutral_site"]), now)
        f = {**bundle.features(game), "game_id": g["game_id"], "season": g["season"], "tipoff_at": g["tipoff_at"]}
        out += _records(f, model_name, d, book, quotes, outcome=False, cutoff=now)
    return out


# --- Data validation --------------------------------------------------------------------------------------------------
def validate_data(conn: sqlite3.Connection, today: Optional[str] = None) -> dict:
    """Fixture identity, neutral-site orientation, finals with scores, missing inputs, and per-season coverage by market. Read-only."""
    if store_sport(conn) != "ncaab":
        raise NcaabEvaluationError("NCAAB validation needs the NCAAB store")
    today = today or utc_now()
    one = lambda sql, *a: conn.execute(sql, a).fetchone()[0]     # noqa: E731
    elig = ("FROM games g JOIN ncaab_game_context c ON c.game_id = g.game_id WHERE c.both_d1 = 1 "
            "AND g.season_type IN ('regular', 'postseason')")
    issues, notes = [], []
    scored_from = min(PERIODS["development"])
    fixtures = {"games": one("SELECT COUNT(*) FROM games"), "duplicate_game_ids": one("SELECT COUNT(*) - COUNT(DISTINCT game_id) FROM games"),
                "duplicate_games": one(f"SELECT COUNT(*) FROM (SELECT 1 {elig} GROUP BY g.tipoff_at, g.home_team_id, g.away_team_id "
                                       "HAVING COUNT(*) > 1)"),
                "duplicate_games_by_season": {str(r[0]): r[1] for r in conn.execute(
                    f"SELECT season, COUNT(*) FROM (SELECT g.season {elig} GROUP BY g.tipoff_at, g.home_team_id, g.away_team_id "
                    "HAVING COUNT(*) > 1) GROUP BY season")},
                "swapped_duplicates": one(f"SELECT COUNT(*) FROM games a JOIN games b ON a.home_team_id = b.away_team_id AND "
                                          "a.away_team_id = b.home_team_id AND substr(a.tipoff_at, 1, 10) = substr(b.tipoff_at, 1, 10) "
                                          "AND a.game_id < b.game_id AND a.season_type IN ('regular', 'postseason') "
                                          "AND b.season_type IN ('regular', 'postseason')"),
                "teams_missing": one("SELECT COUNT(*) FROM games g WHERE NOT EXISTS (SELECT 1 FROM teams t WHERE t.team_id = g.home_team_id) "
                                     "OR NOT EXISTS (SELECT 1 FROM teams t WHERE t.team_id = g.away_team_id)")}
    orient = {"neutral_games": one(f"SELECT COUNT(*) {elig} AND g.neutral_site = 1 AND g.status = 'final'"),
              "stats_points_mismatch": one(
                  "SELECT COUNT(DISTINCT g.game_id) FROM games g JOIN team_game_stats s ON s.game_id = g.game_id WHERE g.status = 'final' "
                  "AND ((s.is_home = 1 AND (s.team_id != g.home_team_id OR s.points != g.home_score)) OR "
                  "(s.is_home = 0 AND (s.team_id != g.away_team_id OR s.points != g.away_score)))"),
              "stats_points_mismatch_neutral": one(
                  "SELECT COUNT(DISTINCT g.game_id) FROM games g JOIN team_game_stats s ON s.game_id = g.game_id WHERE g.status = 'final' "
                  "AND g.neutral_site = 1 AND ((s.is_home = 1 AND (s.team_id != g.home_team_id OR s.points != g.home_score)) OR "
                  "(s.is_home = 0 AND (s.team_id != g.away_team_id OR s.points != g.away_score)))"),
              "finals_without_stats": one(f"SELECT COUNT(*) {elig} AND g.status = 'final' AND NOT EXISTS "
                                          "(SELECT 1 FROM team_game_stats s WHERE s.game_id = g.game_id)")}
    finals = {"missing_scores": one("SELECT COUNT(*) FROM games WHERE status = 'final' AND (home_score IS NULL OR away_score IS NULL)"),
              "tied": one(f"SELECT COUNT(*) {elig} AND g.status = 'final' AND g.home_score = g.away_score"),
              "tied_by_season": {str(r[0]): r[1] for r in conn.execute(
                  f"SELECT g.season, COUNT(*) {elig} AND g.status = 'final' AND g.home_score = g.away_score GROUP BY g.season")},
              "scheduled_past_start_over_2d": one(f"SELECT COUNT(*) {elig} AND g.status = 'scheduled' AND g.tipoff_at < "
                                                  "strftime('%Y-%m-%dT%H:%M:%SZ', ?, '-2 days')", today),
              "result_available_before_tipoff": one(f"SELECT COUNT(*) {elig} AND g.status = 'final' AND g.result_available_at <= g.tipoff_at")}
    quotes_by = {}
    if has_table(conn, "market_quotes"):
        for r in conn.execute("SELECT game_id, market, MIN(retrieved_at) FROM market_quotes WHERE provider = ? AND price_label = 'current' "
                              "GROUP BY game_id, market", (QUOTE_PROVIDER,)):
            quotes_by[(r[0], r[1])] = r[2]
    after = {r[0] for r in conn.execute("SELECT DISTINCT game_id FROM ncaab_after_game_lines")} if has_table(conn, "ncaab_after_game_lines") else set()
    cov: dict = {}
    for r in conn.execute(f"SELECT g.game_id, g.season, g.status, g.tipoff_at, g.neutral_site, "
                          "EXISTS (SELECT 1 FROM team_game_stats s WHERE s.game_id = g.game_id AND s.fga IS NOT NULL AND s.fta IS NOT NULL "
                          "GROUP BY s.game_id HAVING COUNT(*) = 2) " + elig):
        s = cov.setdefault(str(r[1]), {"games": 0, "finals": 0, "first": r[3], "last": r[3], "neutral": 0, "missing_fga_fta": 0,
                                       "after_game_line": 0, "quotes": {m: 0 for m in ("winner", "spread", "total")}})
        s["games"] += 1
        s["finals"] += r[2] == "final"
        s["first"], s["last"] = min(s["first"], r[3]), max(s["last"], r[3])
        s["neutral"] += r[4]
        s["missing_fga_fta"] += r[2] == "final" and not r[5]
        s["after_game_line"] += r[0] in after
        for m in s["quotes"]:
            s["quotes"][m] += (r[0], m) in quotes_by
    gaps = {}
    for season, s in cov.items():
        days = {r[0] for r in conn.execute(f"SELECT DISTINCT substr(g.tipoff_at, 1, 10) {elig} AND g.season = ?", (int(season),))}
        d0, d1 = datetime.fromisoformat(min(days)), datetime.fromisoformat(max(days))
        every = [(d0 + timedelta(days=i)).strftime("%Y-%m-%d") for i in range((d1 - d0).days + 1)]
        gaps[season] = [d for d in every if d not in days and d[5:7] in ("12", "01", "02")]
    dup_seasons = {int(k): v for k, v in fixtures["duplicate_games_by_season"].items()}
    tie_seasons = {int(k): v for k, v in finals["tied_by_season"].items()}
    if fixtures["duplicate_game_ids"] or any(k >= scored_from for k in dup_seasons) or fixtures["swapped_duplicates"] > 5:
        issues.append(f"duplicate fixtures: {fixtures}")
    elif dup_seasons:
        notes.append(f"{sum(dup_seasons.values())} duplicate fixtures (same tip-off and teams under two ids), seasons {sorted(dup_seasons)} "
                     "only: warm-up burn-in, never scored")
    if any(k >= scored_from for k in tie_seasons):
        issues.append(f"tied finals in scored seasons: {tie_seasons}")
    elif tie_seasons:
        notes.append(f"{sum(tie_seasons.values())} finals tied (0-0 placeholders), seasons {sorted(tie_seasons)} only: warm-up")
    if fixtures["swapped_duplicates"]:
        notes.append(f"{fixtures['swapped_duplicates']} same-UTC-day pairs with home/away swapped (rematches or relists; not removed)")
    if orient["stats_points_mismatch"]:
        issues.append(f"{orient['stats_points_mismatch']} finals whose team_game_stats home/away or points disagree with games")
    if finals["missing_scores"] or finals["result_available_before_tipoff"]:
        issues.append(f"finals without scores or available before tip-off: {finals}")
    if fixtures["teams_missing"]:
        issues.append(f"{fixtures['teams_missing']} games reference unknown teams")
    return {"sport": "ncaab", "checked_at": today, "ok": not issues, "issues": issues, "notes": notes, "fixtures": fixtures, "orientation": orient,
            "finals": finals, "coverage": dict(sorted(cov.items())), "empty_winter_days": gaps,
            "quote_note": "DraftKings quotes (market_quotes) start 2026-11-17; ncaab_after_game_lines are retrieved after the game "
                          "(benchmark only). Earlier seasons have no bookmaker line at all."}


# --- Evaluation of research models ------------------------------------------------------------------------------------
def _pairs(records: list[dict]) -> list[tuple]:
    """(p_a given a decisive result, 1 if a won) for records with a non-push, non-void outcome."""
    return [(r["probs"]["a"] / (r["probs"]["a"] + r["probs"]["b"]), r["outcome"] == "a") for r in records
            if r["outcome"] in ("a", "b")]


def score_records(records: list[dict]) -> dict:
    """n, Brier and log loss (pushes/voids excluded, a/(a+b) renormalised), Gaussian point log score and RMSE where the record has a
    point forecast, and the worst decile gap of predicted vs observed frequency."""
    pr = _pairs(records)
    if not pr:
        return {"n": 0}
    p = [x for x, _ in pr]
    y = [o for _, o in pr]
    out = {"n": len(pr), "pushes": sum(r["outcome"] == "push" for r in records), "brier": metrics.brier(p, y),
           "log_loss": metrics.log_loss(p, y)}
    pts = [r for r in records if r.get("point_pred") is not None and r.get("point_actual") is not None and r.get("sd")]
    if pts:
        out["gauss_nll"] = sum(0.5 * math.log(2 * math.pi * r["sd"] ** 2) + (r["point_actual"] - r["point_pred"]) ** 2 / (2 * r["sd"] ** 2)
                               for r in pts) / len(pts)
        out["rmse"] = math.sqrt(sum((r["point_actual"] - r["point_pred"]) ** 2 for r in pts) / len(pts))
    dec = _deciles(p, [float(v) for v in y])
    out["max_decile_gap"] = max(abs(x["gap"]) for x in dec) if dec else None
    return out


def compare_records(a: list[dict], b: list[dict]) -> dict:
    """Mean per-game Brier difference a - b on the games both decided (negative favours a), with the shared week-block bootstrap."""
    pb = {r["game_id"]: r for r in b if r["outcome"] in ("a", "b")}
    vals, cuts = [], []
    for r in a:
        q = pb.get(r["game_id"])
        if r["outcome"] not in ("a", "b") or q is None:
            continue
        y = float(r["outcome"] == "a")
        pa = r["probs"]["a"] / (r["probs"]["a"] + r["probs"]["b"])
        pq = q["probs"]["a"] / (q["probs"]["a"] + q["probs"]["b"])
        vals.append((pa - y) ** 2 - (pq - y) ** 2)
        cuts.append(r["forecast_cutoff"])
    if not vals:
        return {"games": 0}
    boot = backtest.block_bootstrap(vals, cuts, samples=BOOTSTRAP_SAMPLES)
    return {"games": len(vals), "mean_brier_diff": boot["mean"], "ci95": boot["ci95"]}


def quote_benchmark(records: list[dict], incumbent: list[dict]) -> dict:
    """Records graded at a DraftKings line with a stored pre-cutoff quote: model Brier vs the no-vig price's Brier on the same games."""
    q = [r for r in records if r["quote"] and r["outcome"] in ("a", "b")]
    inc = {(r["game_id"], r["market"]): r for r in incumbent}
    if not q:
        return {"n": 0}
    y = [r["outcome"] == "a" for r in q]
    dk = [r["quote"]["p_a_devig"] for r in q]
    mod = [r["probs"]["a"] / (r["probs"]["a"] + r["probs"]["b"]) for r in q]
    ii = [inc[(r["game_id"], r["market"])] for r in q if (r["game_id"], r["market"]) in inc]
    out = {"n": len(q), "model_brier": metrics.brier(mod, y), "dk_novig_brier": metrics.brier(dk, y), "coin_brier": 0.25}
    if len(ii) == len(q):
        out["incumbent_brier"] = metrics.brier([r["probs"]["a"] / (r["probs"]["a"] + r["probs"]["b"]) for r in ii], y)
    return out


def evaluate_research(conn: sqlite3.Connection, periods=("search", "validation"), models=None) -> dict:
    """Every research model's score per market over the search and validation periods (never the holdout), the paired difference
    against the incumbent, and, for the forward seasons, DraftKings-quote benchmarks."""
    models = list(models or MODEL_SPECS)
    rp = research_periods()
    out: dict = {"periods": {p: list(rp[p]) for p in periods}, "line_source": "reference_model (market-free Elo-margin / "
                 "running-points half-point line, shared by every model)", "results": {}, "vs_incumbent": {}}
    recs = {m: research_walk(conn, [s for p in periods for s in rp[p]], m) for m in models}
    for p in periods:
        seasons = set(rp[p])
        for m in models:
            for market in RESEARCH_MARKETS:
                sel = [r for r in recs[m] if r["season"] in seasons and r["market"] == market]
                if sel:
                    out["results"].setdefault(p, {}).setdefault(market, {})[m] = score_records(sel)
                    if m != "ncaab-incumbent":
                        inc = [r for r in recs["ncaab-incumbent"] if r["season"] in seasons and r["market"] == market]
                        out["vs_incumbent"].setdefault(p, {}).setdefault(market, {})[m] = compare_records(sel, inc)
    return out


def evaluate_forward(conn: sqlite3.Connection, models=None) -> dict:
    """Forward seasons (after the holdout): games with a DraftKings quote retrieved at or before the forecast cutoff, graded at
    the quote's line against the no-vig price. Parameters of candidates stop at the last validation season."""
    models = list(models or MODEL_SPECS)
    seasons = [s for (s,) in conn.execute("SELECT DISTINCT season FROM games WHERE season >= ?", (forward_from(),))]
    out: dict = {"seasons": seasons, "quote_provider": QUOTE_PROVIDER, "results": {}}
    if not seasons:
        return out
    recs = {m: research_walk(conn, seasons, m) for m in models}
    for market in ("spread", "total"):
        inc = [r for r in recs["ncaab-incumbent"] if r["market"] == market]
        for m in models:
            sel = [r for r in recs[m] if r["market"] == market]
            out["results"].setdefault(market, {})[m] = quote_benchmark(sel, inc)
    return out


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def main(argv: Optional[list] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(prog="python -m app.ncaab_eval", description=__doc__.split("\n")[0])
    ap.add_argument("stage", choices=("development", "validation", "test", "research"))
    ap.add_argument("--cutoff", default=DEFAULT_CUTOFF, help="information time of the store snapshot (results available by then)")
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--decision-path", type=Path, default=None)
    args = ap.parse_args(argv)
    if config.protected_data_dir(config.data_dir()):
        raise SystemExit(f"Refusing to evaluate against the live data folder {config.data_dir()}; set NBA_DATA_DIR to a copy")
    conn = _connect(config.db_path("ncaab"))
    command = f"cd backend && NBA_DATA_DIR={shlex.quote(str(config.data_dir()))} .venv/bin/python -m app.ncaab_eval " + shlex.join(argv)
    if args.stage == "research":      # research candidates: search + validation walks and the forward quote benchmark; never the holdout
        args.out_dir.mkdir(parents=True, exist_ok=True)
        for name, body in (("validate_data", validate_data(conn)), ("research", evaluate_research(conn)),
                           ("forward", evaluate_forward(conn))):
            (args.out_dir / f"{name}.json").write_text(json.dumps(body, indent=1, default=str))
        print(json.dumps({"stage": "research", "output_dir": str(args.out_dir)}))
        return 0
    fn = {"development": evaluate_development, "validation": evaluate_validation, "test": evaluate_test}[args.stage]
    kw = {} if args.stage == "development" else {"decision_path": args.decision_path}
    report = fn(conn, out_dir=args.out_dir, cutoff=args.cutoff, command=command, **kw)
    print(json.dumps({"stage": report["stage"], "output_dir": report["output_dir"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
