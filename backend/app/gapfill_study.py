"""Gap-filling combination study `gapfill-v1` (docs/MODEL_SELECTION.md §16): the compatible subsets the evidence audit found
never evaluated, scored from saved chronological out-of-sample predictions only (no refit). Exploratory."""
import gzip
import hashlib
import json
import math
import random
from datetime import datetime
from itertools import combinations
from pathlib import Path
from statistics import NormalDist
from typing import Callable, Optional

from . import combination_research as cr, combination_search as cs, combine, config
from . import selective_study as ss
from .db import utc_now

STUDY = "gapfill-v1"
SEED = 20261002
BUDGET = 500
THRESHOLDS = {"winner": (0.60, 0.70, 0.80), "reg3": (0.45, 0.50, 0.55), "line": (0.55, 0.60, 0.65)}
DISAGREE = (0.03, 0.06)
MIN_PICKS, MIN_COVERAGE, ALPHA, BOOT = 100, 0.05, 0.05, 1000
# Rules previously evaluated per market (§11 per fold, §14, §15, pool stage 2; docs/EVIDENCE_AUDIT.md §3): the multiple-
# testing bound counts the whole search, not only this family.
PRIOR_RULES = {("nba", "winner"): 1655 + 895 + 1170 + 40, ("nba", "line"): 1655 + 27 + 555 + 56,
               ("nhl", "winner"): 925 + 36 + 895 + 40, ("nhl", "reg3"): 345 + 255 + 42, ("nhl", "line"): 405 + 195 + 40}
PRIMARY = {"nba": {"winner": "elo", "line": "points_normal"}, "nhl": {"winner": "hockey_elo", "reg3": "goals_poisson",
                                                                     "line": "goals_poisson"}}


def _kind(market: str) -> str:
    return ss.kind_of(market)


# --- Family -----------------------------------------------------------------------------------------------------------
def family(members: list[str], candidates: list[str], market: str, budget: int = BUDGET) -> tuple[list[dict], dict]:
    """The registered family in its fixed order, truncated at `budget` configurations (never on results)."""
    ts = THRESHOLDS[_kind(market)]
    rng = random.Random(SEED)
    full = list(members)
    blocks = [("full", [full], lambda g: [{"kind": k, "members": g, "t": t} for k in ("avg", "unanimous_min") for t in ts]
               + [{"kind": "majority", "members": g, "t": None}]
               + [{"kind": "unanimous_maxdis", "members": g, "t": d} for d in DISAGREE]
               + [{"kind": "stack", "members": g, "t": t} for t in ts])]
    pairs = [list(p) for p in combinations(members, 2)]
    blocks.append(("pair", pairs, lambda g: [{"kind": k, "members": g, "t": t} for k in ("avg", "unanimous_min") for t in ts]))
    triples = [list(t) for t in combinations(members, 3)] if len(members) > 3 else []
    mixed = [t for t in triples if any(m in candidates for m in t) and any(m not in candidates for m in t)]
    rest = [t for t in triples if t not in mixed]
    rng.shuffle(mixed)
    rng.shuffle(rest)
    blocks.append(("triple", mixed + rest, lambda g: [{"kind": k, "members": g, "t": t} for k in ("avg", "unanimous_min") for t in ts]
                   + [{"kind": "majority", "members": g, "t": None}]
                   + [{"kind": "unanimous_maxdis", "members": g, "t": d} for d in DISAGREE]))
    larger = []
    for size in range(4, len(members)):
        group = [list(c) for c in combinations(members, size)]
        rng.shuffle(group)
        larger += group
    blocks.append(("larger", larger, lambda g: [{"kind": "avg", "members": g, "t": t} for t in ts]
                   + [{"kind": "majority", "members": g, "t": None}]))
    rules, evaluated, omitted = [], {}, {}
    for name, subsets, make in blocks:
        for g in subsets:
            rs = make(g)
            if len(rules) + len(rs) > budget:
                omitted.setdefault(name, []).append("+".join(g))
                continue
            rules += rs
            evaluated.setdefault(name, []).append("+".join(g))
    return [{**r, "level": "all", "id": ss.rule_id(r, "all")} for r in rules], {"evaluated": evaluated, "omitted": omitted}


# --- Probability level ------------------------------------------------------------------------------------------------
def _week(row) -> str:
    d = datetime.strptime(row["tipoff_at"][:10], "%Y-%m-%d").isocalendar()
    return f"{d[0]}-{d[1]:02d}"


def _boot(diffs: list[tuple[str, float]], seed: int = SEED) -> dict:
    """Mean of per-game differences with a 95% week-block bootstrap interval."""
    if not diffs:
        return {"mean": None, "ci95": None, "games": 0}
    by: dict = {}
    for w, d in diffs:
        by.setdefault(w, []).append(d)
    weeks = sorted(by)
    rng = random.Random(seed)
    means = []
    for _ in range(BOOT):
        tot = n = 0
        for _w in range(len(weeks)):
            b = by[weeks[rng.randrange(len(weeks))]]
            tot += sum(b)
            n += len(b)
        means.append(tot / n)
    means.sort()
    return {"mean": sum(d for _, d in diffs) / len(diffs), "ci95": [means[int(0.025 * BOOT)], means[int(0.975 * BOOT) - 1]],
            "games": len(diffs), "weeks": len(weeks)}


def _ll(vec, realised) -> float:
    return combine.log_loss(vec, realised)


def _avg_ll(row, subset):
    return _ll(combine.average([row["vectors"][m] for m in subset]), row["realised"])


def probability(rows_by_fold: list[tuple[list, str]], subsets: list[list[str]], candidates: list[str]) -> dict:
    """Equal-average log loss minus the best prior single per subset (assess seasons), and route B with/without."""
    per_subset, route_b = {}, {}
    for subset in subsets:
        diffs = [( _week(r), _avg_ll(r, subset) - _ll(r["vectors"][best], r["realised"])) for rows, best in rows_by_fold for r in rows]
        bd = [combine.brier(combine.average([r["vectors"][m] for m in subset]), r["realised"])
              - combine.brier(r["vectors"][best], r["realised"]) for rows, best in rows_by_fold for r in rows]
        per_subset["+".join(subset)] = {**_boot(diffs), "brier_mean": sum(bd) / len(bd) if bd else None}
        for c in subset:
            if c not in candidates or len(subset) < 2:
                continue
            without = [m for m in subset if m != c]
            d = [(_week(r), _avg_ll(r, subset) - (_avg_ll(r, without) if len(without) > 1 else _ll(r["vectors"][without[0]], r["realised"])))
                 for rows, _ in rows_by_fold for r in rows]
            seasons = {}
            for rows, _ in rows_by_fold:
                if rows:
                    seasons[str(rows[0]["season"])] = sum(_avg_ll(r, subset) - (_avg_ll(r, without) if len(without) > 1 else
                                                          _ll(r["vectors"][without[0]], r["realised"])) for r in rows) / len(rows)
            route_b[f"{'+'.join(subset)} vs without {c}"] = {**_boot(d), "candidate": c, "subset": subset, "per_season": seasons}
    return {"per_subset": per_subset, "route_b": route_b}


# --- Pick rules -------------------------------------------------------------------------------------------------------
def fold(market, rows, members, rules, select, assess, prior_rules: int, primary: str) -> dict:
    sel = [r for r in rows if r["season"] in select]
    ass = [r for r in rows if r["season"] == assess]
    best = cs._best_single(sel, members)
    z = NormalDist().inv_cdf(1 - ALPHA / (len(rules) + prior_rules))
    keys = list(members) + ["average"]
    cur_sel = {("all", m): cs._top_curve(market, sel, m) for m in keys}
    cur_ass = {("all", m): cs._top_curve(market, ass, m) for m in keys}
    scored = []
    for rule in rules:
        s = ss.score_rule(market, sel, rule, cur_sel, best)
        s["lower"] = s["lift"] - z * s["se"] if s["lift"] is not None else None
        big = s["wins"] + s["losses"] >= MIN_PICKS and s["coverage"] >= MIN_COVERAGE
        s["qualifies"] = bool(big and s["lower"] is not None and s["lower"] > 0
                              and all(v is not None and v > 0 for v in s["vs_constituents"].values()))
        scored.append({"id": rule["id"], **s})
    big = [s for s in scored if s["wins"] + s["losses"] >= MIN_PICKS and s["coverage"] >= MIN_COVERAGE and s["accuracy"] is not None]
    frozen = {s["id"]: "qualified" for s in scored if s["qualifies"]}
    if big:
        frozen.setdefault(max(big, key=lambda s: (s["accuracy"], s["picks"], s["id"]))["id"], "highest accuracy (≥100 picks, ≥5%)")
        lifted = [s for s in big if s["lift"] is not None]
        if lifted:
            frozen.setdefault(max(lifted, key=lambda s: (s["lift"], s["id"]))["id"], "top point lift")
    by_id, sel_by_id = {r["id"]: r for r in rules}, {s["id"]: s for s in scored}
    assessed = {}
    for rid, why in frozen.items():
        rule = by_id[rid]
        a = ss.score_rule(market, ass, rule, cur_ass, best)
        cons = [m for m in rule["members"] if m in members]
        strongest = cs._best_single(sel, cons) if cons else None
        a["matched"] = {name: cs._matched(cur_ass[("all", key)], a["picks"])[0] for name, key in
                        (("best_prior_single", best), ("primary", primary), ("strongest_constituent", strongest),
                         ("all_member_average", "average")) if key is not None}
        a["strongest_constituent"] = strongest
        a["why"] = why
        a["selection"] = {k: sel_by_id[rid][k] for k in ("picks", "coverage", "accuracy", "lift", "lower", "comparator_accuracy")}
        assessed[rid] = a
    return {"select_seasons": list(select), "assess_season": assess, "best_single": best, "rules": len(rules),
            "bonferroni_count": len(rules) + prior_rules, "z": z,
            "qualified": [r for r, w in frozen.items() if w == "qualified"], "assessed": assessed,
            "all_rules": [{k: s[k] for k in ("id", "picks", "wins", "losses", "pushes", "coverage", "accuracy", "lift", "lower",
                                             "comparator_accuracy")} for s in scored]}


def analyse(market, table, members, candidates, seasons, prior_rules, primary, budget=BUDGET) -> dict:
    rows = [r for r in table if all(m in r["vectors"] for m in members) and r["realised"] is not None and r["season"] in seasons]
    for r in rows:
        r["vectors"] = dict(r["vectors"])
        r["vectors"]["average"] = combine.average([r["vectors"][m] for m in members])
        r["favourite"], r["disagreement"] = 0.5, None
    first = {m: seasons[0] for m in members}
    stack_fits = cr._stack_walk(market, rows, members, first, seasons)
    rules, plan = family(members, candidates, market, budget)
    folds = [fold(market, rows, members, rules, [s for s in seasons if s < a], a, prior_rules, primary) for a in seasons[1:]]
    subsets = sorted({tuple(r["members"]) for r in rules if r["kind"] != "stack"}, key=lambda g: (len(g), g))
    scorable = [r for r in rows if market in combine.THREE_WAY or r["realised"] != 1]     # a push has no two-way log loss
    prob = probability([([r for r in scorable if r["season"] == f["assess_season"]], f["best_single"]) for f in folds],
                       [list(g) for g in subsets], candidates)
    return {"market": market, "members": members, "candidates": candidates, "games": len(rows), "seasons": seasons,
            "configurations": len(rules), "plan": plan, "subsets_evaluated": len(subsets),
            "stack_fits": [{k: f[k] for k in ("season", "fitted", "training_games") if k in f} for f in stack_fits],
            "folds": folds, "probability": prob}


# --- Run --------------------------------------------------------------------------------------------------------------
def _load(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    return json.loads(gzip.decompress(raw)), hashlib.sha256(raw).hexdigest()[:24]


def run(out_dir: Optional[Path] = None, log: Callable[[str], None] = print, budget: int = BUDGET) -> dict:
    started = utc_now()
    root = config.data_dir()
    report = {"kind": "gapfill", "study": STUDY, "started_at": started, "exploratory": True, "budget": budget, "inputs": {},
              "markets": {}}
    jobs = []
    for sport in ("nhl", "nba"):
        oof, fp = _load(root / "research" / "candidates" / f"oof-{sport}-assess.json.gz")
        short = json.loads((root / "reports" / f"pool-{sport}" / "shortlist.json").read_text())
        report["inputs"][f"oof-{sport}-assess"] = {"sha256_24": fp, "seasons": oof["seasons"],
                                                   "shortlist_frozen_at": short["frozen_at"]}
        for market, m in short["markets"].items():
            if not m["candidates"]:
                report["markets"][f"{sport}:{market}"] = {"configurations": 0, "reason": "no shortlisted candidate; every subset of "
                                                          "the existing members already evaluated (§11, §15)"}
                continue
            k = _kind(market)
            jobs.append((f"{sport}:{market}", market, oof["tables"][market], list(m["existing"]) + list(m["candidates"]),
                         list(m["candidates"]), list(oof["seasons"]), PRIOR_RULES[(sport, k)], PRIMARY[sport][k]))
    ev, fp = _load(root / "research" / "tables" / "nba_evening.json.gz")
    report["inputs"]["nba_evening"] = {"sha256_24": fp, "seasons": ev["seasons"], "cutoff": ev.get("cutoff")}
    jobs.append(("nba_evening:winner", "winner", ev["tables"]["winner"], list(ev["members"]["winner"]), ["avail_v1"],
                 list(ev["seasons"]), PRIOR_RULES[("nba", "winner")], "elo"))
    for name, market, table, members, cands, seasons, prior, primary in jobs:
        t0 = datetime.now()
        a = analyse(market, table, members, cands, seasons, prior, primary, budget)
        report["markets"][name] = a
        log(f"{name}: {a['games']} games, {a['configurations']} configurations, {a['subsets_evaluated']} subsets, "
            f"qualified per fold {[len(f['qualified']) for f in a['folds']]}, {(datetime.now() - t0).seconds} s")
    report["finished_at"] = utc_now()
    out_dir = out_dir or root / "reports" / f"gapfill-{started.replace(':', '')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=1, default=str))
    report["output_dir"] = str(out_dir)
    log(f"wrote {out_dir / 'report.json'}")
    return report
