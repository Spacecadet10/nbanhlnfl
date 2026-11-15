"""Calibration study calibration-v1 (docs/MODEL_SELECTION.md, registered before any code or result). Research only.

Per member and market: raw, `platt` (two-way: logistic map of logit P(side a) on decided games; 3-way: one temperature on
the log probabilities) and `shrink` (two-way: 0.5 + λ(p − 0.5); 3-way: toward 1/3 each), each fitted at every season
start on that member's out-of-sample forecasts from EARLIER seasons only. Compared with the existing equal average, the
live-parity stacker and the average of `platt` members on identical later games and lines. Nothing here changes a
forecast, member, selector, identity or stored row.
"""
import json
import math
from pathlib import Path
from typing import Callable, Optional

from . import combine, config, historical_start, model_audit as ma
from .db import utc_now
from .models.calibration import logit
from .models.logistic import fit_logistic, sigmoid

STUDY = "calibration-v1"
MIN_FIT = 300
SHRINK_RANGE = (0.0, 1.5)       # λ
TEMP_RANGE = (0.5, 2.0)         # T


def _two(v) -> float:
    return v[0] / (v[0] + v[2])


def _apply(kind: str, params, v, market: str) -> tuple:
    if market in combine.THREE_WAY:
        if kind == "platt":                     # temperature: p_i ∝ p_i^(1/T)
            w = [max(1e-12, x) ** (1 / params) for x in v]
        else:                                   # shrink toward uniform
            w = [1 / 3 + params * (x - 1 / 3) for x in v]
            w = [max(1e-9, x) for x in w]
        s = sum(w)
        return tuple(x / s for x in w)
    p = _two(v)
    if kind == "platt":
        intercept, slope = params
        q = sigmoid(intercept + slope * logit(p))
    else:
        q = 0.5 + params * (p - 0.5)
    q = min(1 - 1e-9, max(1e-9, q))
    return (q, 0.0, 1 - q)


def _fit(kind: str, market: str, pairs: list):
    """pairs: (vector, realised) of decided earlier games."""
    def loss(params):
        return sum(combine.log_loss(_apply(kind, params, v, market), y) for v, y in pairs)
    if kind == "platt" and market not in combine.THREE_WAY:
        intercept, slope = fit_logistic([[logit(_two(v))] for v, _ in pairs], [y == 0 for _, y in pairs], 1e-3)
        return (intercept, slope)
    lo, hi = TEMP_RANGE if kind == "platt" else SHRINK_RANGE
    for _ in range(60):             # one-parameter losses are convex here: ternary search to ~1e-9
        a, b = lo + (hi - lo) / 3, hi - (hi - lo) / 3
        if loss(a) <= loss(b):
            hi = b
        else:
            lo = a
    return round((lo + hi) / 2, 6)


def _market(market: str, table: list, members: list, first: dict, seasons) -> dict:
    settled = [r for r in table if r["realised"] is not None]
    complete = [r for r in settled if all(m in r["vectors"] for m in members)]
    ma._add_avg(complete, members, "average")
    stack_fits = historical_start._live_stack_walk(market, settled, complete, members, list(seasons))
    decided = ma._decided(market, complete)
    fits: dict = {}
    for m in members:
        for kind in ("platt", "shrink"):
            for s in seasons:
                earlier = [(r["vectors"][m], r["realised"]) for r in decided
                           if (first.get(m) or 0) <= r["season"] < s]
                if len(earlier) < MIN_FIT:
                    fits.setdefault(m, {}).setdefault(kind, {})[str(s)] = {"fitted": False, "games": len(earlier)}
                    continue
                params = _fit(kind, market, earlier)
                fits.setdefault(m, {}).setdefault(kind, {})[str(s)] = {"fitted": True, "games": len(earlier), "params": params}
                for r in complete:
                    if r["season"] == s:
                        r["vectors"][f"{m}:{kind}"] = _apply(kind, params, r["vectors"][m], market)
    ma._add_avg(complete, [f"{m}:platt" for m in members], "average_platt")
    names = [f"{m}:{k}" for m in members for k in ("platt", "shrink")]
    rows = [r for r in ma._decided(market, complete) if r["season"] in seasons
            and all(n in r["vectors"] for n in names + ["stack", "average_platt"])]
    if not rows:
        return {"members": members, "games": 0, "note": "no later games with every variant fitted"}
    # Best member for each season chosen on earlier comparison seasons by raw log loss (as the audit), and its variants.
    chosen = {}
    for s in sorted({r["season"] for r in rows}):
        prior = [r for r in rows if r["season"] < s]
        if not prior:
            continue
        b = min(members, key=lambda m: (sum(ma._ll(market, r, m) for r in prior), members.index(m)))
        chosen[str(s)] = b
        for r in rows:
            if r["season"] == s:
                for k in ("", ":platt", ":shrink"):
                    r["vectors"][f"best{k}"] = r["vectors"][b + k]
    later = [r for r in rows if "best" in r["vectors"]]
    sources = list(members) + names + ["average", "average_platt", "stack"]
    out = {"members": members, "games": len(rows), "seasons": sorted({r["season"] for r in rows}),
           "stack_fits": stack_fits, "fits": fits, "best_by_season": chosen,
           "sources": {s: ma._summary(market, rows, s) for s in sources},
           "member_effects": {m: {k: ma._paired(market, rows, f"{m}:{k}", m) for k in ("platt", "shrink")} for m in members},
           "combinations": {
               "average_platt_vs_average": ma._paired(market, rows, "average_platt", "average"),
               "stack_vs_average": ma._paired(market, rows, "stack", "average")}}
    if later:
        cmp = {k: ma._paired(market, later, a, b) for k, (a, b) in {
            "best_raw_vs_average": ("best", "average"), "best_platt_vs_average": ("best:platt", "average"),
            "best_shrink_vs_average": ("best:shrink", "average"), "best_platt_vs_best_raw": ("best:platt", "best"),
            "best_shrink_vs_best_raw": ("best:shrink", "best")}.items()}
        raw_worse = bool(cmp["best_raw_vs_average"] and cmp["best_raw_vs_average"]["ci95"][0] > 0)
        cal_ok = [k for k in ("best_platt_vs_average", "best_shrink_vs_average")
                  if cmp[k] and not cmp[k]["ci95"][0] > 0]
        out["mechanism"] = {**cmp, "games": len(later),
                            "calibration_explains_combination_gain": (
                                "supported" if raw_worse and cal_ok else
                                "not applicable (raw best member not worse than the average)" if not raw_worse else
                                "not supported")}
    return out


def run(conn, sport: str, log: Callable[[str], None] = print, out_dir: Optional[Path] = None) -> dict:
    started = utc_now()
    tables, first, version, members, seasons = historical_start._history(conn, sport)
    report = {"study": STUDY, "sport": sport, "started_at": started, "dataset_version": version,
              "historical_seasons": list(seasons), "first_out_of_sample_season": first,
              "note": "Exploratory: seasons already used to choose the models. Research only; registered design in "
                      "docs/MODEL_SELECTION.md (calibration-v1).", "markets": {}}
    for market, table in tables.items():
        mem = members["winner"] if market == "winner" else members["lines"]
        log(f"{sport} {market}")
        report["markets"][market] = _market(market, table, mem, first, seasons)
    report["finished_at"] = utc_now()
    out_dir = Path(out_dir or config.data_dir() / "reports" / f"{STUDY}-{sport}")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=1, default=str))
    report["output_dir"] = str(out_dir)
    return report
