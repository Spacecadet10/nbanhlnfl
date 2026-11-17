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
import itertools
import json
import math
import os
import shlex
import sqlite3
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from . import backtest, config, markets, metrics, ncaab_app
from .db import store_sport, utc_now
from .models import college
from .models.baselines import Constant, HomeRate
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


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def main(argv: Optional[list] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(prog="python -m app.ncaab_eval", description=__doc__.split("\n")[0])
    ap.add_argument("stage", choices=("development", "validation", "test"))
    ap.add_argument("--cutoff", default=DEFAULT_CUTOFF, help="information time of the store snapshot (results available by then)")
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--decision-path", type=Path, default=None)
    args = ap.parse_args(argv)
    if config.protected_data_dir(config.data_dir()):
        raise SystemExit(f"Refusing to evaluate against the live data folder {config.data_dir()}; set NBA_DATA_DIR to a copy")
    conn = _connect(config.db_path("ncaab"))
    command = f"cd backend && NBA_DATA_DIR={shlex.quote(str(config.data_dir()))} .venv/bin/python -m app.ncaab_eval " + shlex.join(argv)
    fn = {"development": evaluate_development, "validation": evaluate_validation, "test": evaluate_test}[args.stage]
    kw = {} if args.stage == "development" else {"decision_path": args.decision_path}
    report = fn(conn, out_dir=args.out_dir, cutoff=args.cutoff, command=command, **kw)
    print(json.dumps({"stage": report["stage"], "output_dir": report["output_dir"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
