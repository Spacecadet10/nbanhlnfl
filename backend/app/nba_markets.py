"""NBA spread / total / team-total research and the sealed NBA market model (docs/EVALUATION_PLAN.md,
"NBA spread and total research"). Development research only: it reads warm-up and validation seasons (2019-20 …
2023-24) through backtest.load_rows("validation"), never the spent 2024-25 / 2025-26 test seasons, and it never touches
the sealed Elo or the logistic_v2 shadow experiment.
"""
import itertools
import json
import math
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Optional

from . import backtest, config, markets, metrics
from .db import utc_now
from .models.points import PointsConfig, PointsNormal, gaussian_nll

TUNE_SEASONS = (2022, 2023)
HOLDOUT_SEASONS = (2024,)
GRID = {"eta": (0.02, 0.05, 0.1), "carry": (0.5, 0.75, 1.0)}
DECISION_PATH = Path(__file__).parent / "models" / "nba_market_decision.json"


class NbaMarketError(RuntimeError):
    pass


def _extra(model, game) -> dict:
    d = model.distribution(game)
    return {"margin": d.m, "sd_margin": d.sm, "total": d.t, "sd_total": d.st}


def _run(cfg: PointsConfig, rows) -> list[dict]:
    return backtest.run(PointsNormal(cfg), rows, extra=_extra)


def _nll(preds: list[dict], scores: dict) -> float:
    total = 0.0
    for p in preds:
        h, a = scores[p["game_id"]]
        total += gaussian_nll(h - a, p["margin"], p["sd_margin"]) + gaussian_nll(h + a, p["total"], p["sd_total"])
    return total / len(preds)


def _interval_coverage(preds: list[dict], scores: dict, key: str, sd_key: str, value: Callable, z: float) -> float:
    inside = sum(abs(value(*scores[p["game_id"]]) - p[key]) <= z * p[sd_key] for p in preds)
    return inside / len(preds)


def _summary(preds: list[dict], scores: dict) -> dict:
    margin_err = [scores[p["game_id"]][0] - scores[p["game_id"]][1] - p["margin"] for p in preds]
    total_err = [sum(scores[p["game_id"]]) - p["total"] for p in preds]
    probs, ys = [p["p_home"] for p in preds], [p["home_won"] for p in preds]
    return {
        "n": len(preds), "nll_margin_plus_total": _nll(preds, scores),
        "margin_mae": sum(map(abs, margin_err)) / len(preds),
        "margin_rmse": math.sqrt(sum(e * e for e in margin_err) / len(preds)),
        "total_mae": sum(map(abs, total_err)) / len(preds),
        "total_rmse": math.sqrt(sum(e * e for e in total_err) / len(preds)),
        # central-interval coverage of the predictive Normals (nominal 50% and 80%)
        "margin_coverage_50_80": [_interval_coverage(preds, scores, "margin", "sd_margin", lambda h, a: h - a, z)
                                  for z in (0.6745, 1.2816)],
        "total_coverage_50_80": [_interval_coverage(preds, scores, "total", "sd_total", lambda h, a: h + a, z)
                                 for z in (0.6745, 1.2816)],
        "winner_accuracy": metrics.accuracy(probs, ys), "winner_brier": metrics.brier(probs, ys),
    }


def research(conn: sqlite3.Connection, *, out_dir: Optional[Path] = None, log: Callable[[str], None] = print) -> dict:
    rows = backtest.load_rows(conn, "validation")
    if any(r.game.season in backtest.PERIODS["test"] for r in rows):
        raise NbaMarketError("NBA market research must not read the spent test seasons")
    scores = {r.game.game_id: (r.home_score, r.away_score) for r in rows}
    trials = []
    for values in itertools.product(*GRID.values()):
        cfg = PointsConfig(**dict(zip(GRID, values)))
        preds = [p for p in _run(cfg, rows) if p["season"] in TUNE_SEASONS]
        trials.append({"config": asdict(cfg), "nll": _nll(preds, scores), "n": len(preds)})
    best = min(trials, key=lambda t: (t["nll"], t["config"]["eta"], -t["config"]["carry"]))
    cfg = PointsConfig(**best["config"])
    holdout = {name: [p for p in _run(c, rows) if p["season"] in HOLDOUT_SEASONS]
               for name, c in (("points_normal", cfg), ("points_league", PointsConfig(eta=0.0, carry=1.0)))}
    diffs, cuts = [], []
    ref = {p["game_id"]: p for p in holdout["points_league"]}
    for p in holdout["points_normal"]:
        h, a = scores[p["game_id"]]
        q = ref[p["game_id"]]
        mine = gaussian_nll(h - a, p["margin"], p["sd_margin"]) + gaussian_nll(h + a, p["total"], p["sd_total"])
        theirs = gaussian_nll(h - a, q["margin"], q["sd_margin"]) + gaussian_nll(h + a, q["total"], q["sd_total"])
        diffs.append(mine - theirs)
        cuts.append(p["cutoff_at"])
    started = utc_now()
    report = {"sport": "nba", "stage": "development research", "generated_at": started,
              "dataset_version": backtest.dataset_version(rows), "tune_seasons": TUNE_SEASONS,
              "holdout_seasons": HOLDOUT_SEASONS, "grid": GRID, "criterion": "mean Gaussian NLL of margin plus total",
              "trials": sorted(trials, key=lambda t: t["nll"]), "selected": asdict(cfg),
              "holdout": {n: _summary(p, scores) for n, p in holdout.items()},
              "holdout_nll_diff_vs_league": backtest.block_bootstrap(diffs, cuts),
              "note": "Development research on reused seasons; no historical lines exist, so no bookmaker comparison."}
    out_dir = Path(out_dir or config.data_dir() / "reports" / f"nba-markets-{started.replace(':', '')}")
    out_dir.mkdir(parents=True, exist_ok=True)
    report["output_dir"] = str(out_dir)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, default=str))
    log(f"Report written to {out_dir}")
    return report


def seal(report: dict, path: Path = DECISION_PATH) -> dict:
    if path.exists():
        raise NbaMarketError(f"{path} exists; a sealed NBA market model is never overwritten")
    decision = backtest.seal({"sport": "nba", "market_model": "points_normal", "config": report["selected"],
                              "research_dataset_version": report["dataset_version"], "sealed_at": report["generated_at"],
                              "research_report": report["output_dir"],
                              "status": "development research; prospective 2026-27 forecasts are the only independent evidence"})
    path.write_text(json.dumps(decision, indent=2) + "\n")
    return decision


def sealed_model(conn: sqlite3.Connection, path: Path = DECISION_PATH):
    """(model, base version) for live NBA market forecasts, or MarketModelUnavailable if nothing is sealed."""
    from .market_live import MarketModelUnavailable
    if not path.is_file():
        raise MarketModelUnavailable("No sealed NBA market model (run `app.cli nba-market-research --seal`)")
    d = json.loads(path.read_text())
    if d.get("sha256") != backtest.seal(d)["sha256"]:
        raise MarketModelUnavailable(f"{path} was modified after sealing")
    model = PointsNormal(PointsConfig(**{k: d["config"][k] for k in ("eta", "carry")}))
    return model, backtest.model_version(model)
