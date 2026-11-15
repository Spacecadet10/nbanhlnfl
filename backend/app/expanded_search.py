"""Expanded-combination experiment `expanded-v1`, retrospective part (docs/MODEL_SELECTION.md §14). Exploratory.

A small registered rule family involving the members with new information (NHL `hockey_logistic_xg`; NBA weekly score
members), scored with the §11 machinery: identical outcome, line and cutoff; selection on earlier seasons, frozen assessment
on later ones; every rule reported. Comparators: the strongest single (earlier data) and every constituent at matched coverage.
"""
import json
import math
from itertools import combinations
from pathlib import Path
from statistics import NormalDist
from typing import Callable, Optional

from . import backtest, combination_research as cr, combination_search as cs, combine, config, nhl_eval, selector
from .db import utc_now

EXPERIMENT = "expanded-v1"
THRESHOLDS = (0.55, 0.60, 0.65)
NHL_XG = "hockey_logistic_xg"
NBA_WEEKLY = ("ridge_points@weekly", "gbt_points@weekly")


def nhl_subsets(members):
    others = [m for m in members if m != NHL_XG]
    pairs = [[NHL_XG, m] for m in others]
    triples = [[NHL_XG, "hockey_elo", "hockey_logistic"], [NHL_XG, "hockey_elo", "goals_poisson"],
               [NHL_XG, "hockey_logistic", "hockey_gbt"]]
    return pairs + triples + [list(members)], {"stack:all": list(members)}


def nba_subsets(members):
    p, r, g = "points_normal", *NBA_WEEKLY
    triple = [p, r, g]
    return [[p, r], [p, g], [r, g], triple, list(members)], {"stack:triple": triple, "stack:all": list(members)}


def registry(subsets, stacks, members) -> list[dict]:
    out = []
    for s in subsets:
        out.append({"kind": "unanimous", "members": s, "t": None})
        out += [{"kind": "avg", "members": s, "t": t} for t in THRESHOLDS]
    out.append({"kind": "majority", "members": list(members), "t": None})
    for key, ms in stacks.items():
        out += [{"kind": "single", "members": [key], "t": t, "stack_of": ms} for t in THRESHOLDS]
    return [{**r, "level": "all", "id": f"{'stack' if r.get('stack_of') else r['kind']}|{'+'.join(r.get('stack_of') or r['members'])}|{r['t']}"}
            for r in out]


# --- Tables ---------------------------------------------------------------------------------------------------------
def nhl_winner_table(conn):
    """§5 winner table for the NHL with the xG member; shots go only to the xG member (box-event rule)."""
    from . import nhl_shots, sports
    from .models.hockey import GoalsConfig, GoalsPoisson, HockeyElo, HockeyEloConfig, hockey_gbt, hockey_logistic
    from .models.hockey_xg import hockey_logistic_xg
    d = nhl_eval.load_decision(conn)
    rows = nhl_eval.load_rows(conn, "development")
    if {r.game.season for r in rows} - set(nhl_eval.LOADABLE["development"]):
        raise ValueError("expanded study read seasons outside warm-up/development")
    events = nhl_shots.load_events(conn, rows)
    ec, gc, key = HockeyEloConfig(**d["hockey_elo_config"]), GoalsConfig(**d["goals_config"]), sports.NHL.rating_key
    models = [HockeyElo(ec, key), GoalsPoisson(gc, key), hockey_logistic(ec, gc, key), hockey_gbt(ec, gc, key),
              hockey_logistic_xg(ec, gc, key)]
    win, first = {}, {}
    for m in models:
        win[m.name] = {p["game_id"]: p["p_home"] for p in backtest.run(m, rows, events if hasattr(m, "xg") else None)}
        first[m.name] = getattr(m, "oos_from_season", lambda: None)() or min(nhl_eval.PERIODS["warmup"])
    table = [{"game_id": r.game.game_id, "season": r.game.season, "cutoff_at": r.cutoff_at, "tipoff_at": r.game.tipoff_at,
              "result_available_at": r.result_available_at, "line": None, "realised": 0 if r.home_score > r.away_score else 2,
              "vectors": {n: (v[r.game.game_id], 0.0, 1 - v[r.game.game_id]) for n, v in win.items()}} for r in rows]
    return table, first, nhl_eval.dataset_version(rows), [m.name for m in models], list(nhl_eval.PERIODS["development"])


# --- Scoring --------------------------------------------------------------------------------------------------------
def _wilson(w, n):
    return nhl_eval._wilson(w, n)


def fold(market, rows, members, rules, select, assess, carry=()):
    sel = [r for r in rows if r["season"] in select]
    ass = [r for r in rows if r["season"] == assess]
    best = cs._best_single(sel, members)
    z = NormalDist().inv_cdf(1 - 0.05 / len(rules))
    curves = {(part, m): cs._top_curve(market, rs, m) for part, rs in (("sel", sel), ("ass", ass)) for m in members}
    scored, by_id = [], {r["id"]: r for r in rules}
    for rule in rules:
        s = cs.score(market, sel, rule, curves[("sel", best)])
        s["lower"] = s["lift"] - z * s["se"] if s["lift"] is not None else None
        constituents = rule.get("stack_of") or rule["members"]
        s["vs_constituents"] = {m: (None if s["accuracy"] is None or cs._matched(curves[("sel", m)], s["picks"])[0] is None
                                    else s["accuracy"] - cs._matched(curves[("sel", m)], s["picks"])[0]) for m in constituents}
        s["qualifies"] = (s["lower"] is not None and s["lower"] > 0 and s["wins"] + s["losses"] >= cs.MIN_PICKS
                          and s["coverage"] >= cs.MIN_COVERAGE and all(v is not None and v > 0 for v in s["vs_constituents"].values()))
        scored.append({"id": rule["id"], **s})
    eligible = [s for s in scored if s["lift"] is not None and s["wins"] + s["losses"] >= cs.MIN_PICKS and s["coverage"] >= cs.MIN_COVERAGE]
    frozen = [s["id"] for s in scored if s["qualifies"]]
    top = max(eligible, key=lambda s: (s["lift"], s["id"]))["id"] if eligible else None
    assessed = {}
    for rid in dict.fromkeys(list(carry) + frozen + ([top] if top else [])):
        rule = by_id[rid]
        a = score(market, ass, rule, curves[("ass", best)])
        constituents = rule.get("stack_of") or rule["members"]
        a["vs_constituents"] = {m: (None if a["accuracy"] is None or cs._matched(curves[("ass", m)], a["picks"])[0] is None
                                    else a["accuracy"] - cs._matched(curves[("ass", m)], a["picks"])[0]) for m in constituents}
        a["accuracy_ci95"] = _wilson(a["wins"], a["wins"] + a["losses"])
        a["selected_because"] = ("qualified" if rid in frozen else "frozen in an earlier fold" if rid in carry
                                 else "best point lift (transparency only; did not qualify)")
        assessed[rid] = a
    return {"select_seasons": list(select), "assess_season": assess, "best_single": best, "rules": len(rules),
            "bonferroni_z": z, "qualified": frozen, "top_point_rule": top, "assessed": assessed, "all_rules": scored}


score = cs.score


def probability(market, rows, subsets, stacks, members, seasons) -> dict:
    """Average of each registered subset and each stacker vs each constituent: log loss, Brier, reliability, paired diffs."""
    out = {}
    keys = [("avg:" + "+".join(s), s) for s in subsets] + [(k, ms) for k, ms in stacks.items()]
    for key, ms in keys:
        have = [r for r in rows if key in r["vectors"]]
        if not have:
            out[key] = {"games": 0}
            continue
        per = {}
        for s in seasons:
            rs = [r for r in have if r["season"] == s]
            if rs:
                per[str(s)] = {"games": len(rs), "log_loss": cs._log_loss(rs, key)}
        diffs = {}
        for m in ms:
            d = [combine.log_loss(r["vectors"][key], r["realised"]) - combine.log_loss(r["vectors"][m], r["realised"]) for r in have]
            diffs[m] = backtest.block_bootstrap(d, [r["cutoff_at"] for r in have])
        summ = cr._summary(market, have, key)
        out[key] = {"games": len(have), "log_loss": summ["log_loss"], "brier": summ["brier"], "calibration": summ["calibration"],
                    "per_season": per, "minus_constituent_log_loss": diffs}
    return out


def analyse(market, table, members, first, seasons, subsets, stacks) -> dict:
    complete = [r for r in table if all(m in r["vectors"] for m in members) and r["realised"] is not None]
    for r in complete:
        for s in subsets:
            r["vectors"]["avg:" + "+".join(s)] = combine.average([r["vectors"][m] for m in s])
    fits = {}
    for key, ms in stacks.items():
        fits[key] = cr._stack_walk(market, complete, ms, first, seasons)
        for r in complete:
            if "stack" in r["vectors"]:
                r["vectors"][key] = r["vectors"].pop("stack")
    rows = [r for r in complete if r["season"] in seasons]
    rules = registry(subsets, stacks, members)
    folds, frozen_at = [], {}
    for a in seasons[1:]:
        f = fold(market, rows, members, rules, [s for s in seasons if s < a], a, carry=list(frozen_at))
        for rid in f["qualified"]:
            frozen_at.setdefault(rid, a)
        folds.append(f)
    deploy = [rid for rid, a0 in frozen_at.items()
              if all((f["assessed"][rid]["lift"] or 0) > 0 for f in folds if f["assess_season"] >= a0)]
    return {"market": market, "members": members, "games": len(rows), "seasons": seasons, "rules": [r["id"] for r in rules],
            "stack_fits": fits, "folds": folds, "probability": probability(market, rows, subsets, stacks, members, seasons),
            "deploy_candidates": deploy}


def run(conn, sport: str, out_dir: Optional[Path] = None, log: Callable[[str], None] = print) -> dict:
    started = utc_now()
    report = {"kind": "expanded-combination", "experiment": EXPERIMENT, "sport": sport, "started_at": started,
              "exploratory": True, "markets": {}}
    if sport == "nhl":
        table, first, version, members, seasons = nhl_winner_table(conn)
        subsets, stacks = nhl_subsets(members)
        report["dataset_version"] = version
        report["markets"]["winner"] = analyse("winner", table, members, first, seasons, subsets, stacks)
    else:
        tables, first, version, ms, seasons = cs.nba_tables(conn)
        report["dataset_version"] = version
        subsets, stacks = nba_subsets(ms["lines"])
        for market in ("spread", "total", "team_total_home", "team_total_away"):
            report["markets"][market] = analyse(market, tables[market], ms["lines"], first, list(seasons), subsets, stacks)
    for m, a in report["markets"].items():
        log(f"{sport} {m}: {a['games']} games, {len(a['rules'])} rules, qualified per fold "
            f"{[len(f['qualified']) for f in a['folds']]}, deploy candidates {a['deploy_candidates']}")
    report["finished_at"] = utc_now()
    out_dir = out_dir or config.data_dir() / "reports" / f"expanded-{sport}-{started.replace(':', '')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=1, default=str))
    report["output_dir"] = str(out_dir)
    log(f"wrote {out_dir / 'report.json'}")
    return report
