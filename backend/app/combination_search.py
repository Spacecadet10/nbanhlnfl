"""Predefined combination search with rolling-origin frozen assessment (docs/MODEL_SELECTION.md §11). Exploratory.

Uses the §5 study tables, so every member is compared at the same outcome, market line and information cutoff. Every rule in
the registry is scored and reported; a rule is selected only on seasons before the one it is assessed on.
"""
import json
import math
from datetime import datetime
from itertools import combinations
from pathlib import Path
from statistics import NormalDist
from typing import Callable, Optional

from . import backtest, combination_research as cr, combine, config, nhl_eval, selector
from .db import utc_now

SIMILAR = 0.05
CLEAR_FAVOURITE = 0.60
MIN_PICKS = 100
MIN_COVERAGE = 0.05
ALPHA = 0.05
LEVELS = ("all", "close", "clear", "early", "late")


# --- Tables ---------------------------------------------------------------------------------------------------------
def nba_tables(conn):
    from . import nba_markets
    from .models.efficiency import EfficiencyMargin
    from .models.elo import Elo
    from .models.gbt import GradientBoostedTrees
    from .models.logistic import LogisticElo, LogisticV2
    from .models.score_candidates import GbtPoints, RidgePoints
    from .retrain_cadence import Cadenced
    _, cfg = backtest.load_frozen_decision(conn)
    rows, events = backtest.load_rows(conn, "validation"), backtest.load_box_events(conn, "validation")
    if {r.game.season for r in rows} - set(backtest.LOADABLE["validation"]):
        raise ValueError("search read seasons outside warm-up/validation")
    points, _ = nba_markets.sealed_model(conn)
    dist = [points, RidgePoints(cfg), GbtPoints(cfg), Cadenced(RidgePoints(cfg), "weekly"), Cadenced(GbtPoints(cfg), "weekly")]
    winner = [Elo(cfg), LogisticElo(cfg), LogisticV2(cfg), EfficiencyMargin(), GradientBoostedTrees(cfg)]
    tables, first = cr._tables(rows, winner, dist, ("spread", "total", "team_total_home", "team_total_away"), events)
    for m in dist[3:]:
        first[m.name] = m.member.oos_from_season()
    first["elo"] = first["points_normal"] = min(backtest.PERIODS["warmup"])
    members = {"winner": [m.name for m in winner], "lines": [m.name for m in dist]}
    return tables, first, backtest.dataset_version(rows), members, cr.NBA_EVAL


def nhl_tables(conn):
    tables, first, version, members, seasons = cr.nhl_tables(conn)
    return tables, first, version, {"winner": list(members["winner"]), "lines": list(members["lines"])}, seasons


# --- Rules ----------------------------------------------------------------------------------------------------------
def registry(market: str, members: list[str], with_stack: bool) -> list[dict]:
    ts = selector.thresholds(market)
    subsets = [list(c) for k in (2, 3) for c in combinations(members, k) if k < len(members)] + [list(members)]
    out = [{"kind": "single", "members": [m], "t": t} for m in members for t in ts]
    for s in subsets:
        out += [{"kind": "avg", "members": s, "t": t} for t in ts]
        out.append({"kind": "unanimous", "members": s, "t": None})
        out += [{"kind": "unanimous_avg", "members": s, "t": t} for t in ts]
        out.append({"kind": "unanimous_similar", "members": s, "t": None})
    if len(members) >= 3:
        out.append({"kind": "majority", "members": list(members), "t": None})
    if with_stack:
        out += [{"kind": "stack", "members": ["stack"], "t": t} for t in ts]
    return [{**r, "level": lv, "id": f"{r['kind']}|{'+'.join(r['members'])}|{r['t']}|{lv}"} for r in out for lv in LEVELS]


def side_of(rule: dict, row: dict, market: str) -> Optional[int]:
    """Picked side under the rule, or None (no pick). Uses no outcome."""
    v = row["vectors"]
    if any(m not in v for m in rule["members"]):
        return None
    kind = rule["kind"]
    if kind in ("single", "stack"):
        vec = v[rule["members"][0]]
        s = combine.pick(vec, market)
        return s if s is not None and vec[s] + 1e-9 >= rule["t"] else None
    vecs = [v[m] for m in rule["members"]]
    if kind == "avg":
        a = combine.average(vecs)
        s = combine.pick(a, market)
        return s if s is not None and a[s] + 1e-9 >= rule["t"] else None
    picks = [combine.pick(x, market) for x in vecs]
    if kind == "majority":
        counts = {i: picks.count(i) for i in combine.sides(market)}
        top = max(counts.values())
        leaders = [i for i, c in counts.items() if c == top]
        return leaders[0] if len(leaders) == 1 and top > len(picks) / 2 else None
    if picks[0] is None or any(p != picks[0] for p in picks):
        return None
    s = picks[0]
    if kind == "unanimous":
        return s
    if kind == "unanimous_avg":
        return s if combine.average(vecs)[s] + 1e-9 >= rule["t"] else None
    if kind == "unanimous_similar":
        ps = [x[s] for x in vecs]
        return s if max(ps) - min(ps) <= SIMILAR + 1e-12 else None
    raise ValueError(kind)


def in_level(row: dict, level: str) -> bool:
    if level == "all":
        return True
    if level in ("close", "clear"):
        clear = row["favourite"] >= CLEAR_FAVOURITE
        return clear if level == "clear" else not clear
    early = row["tipoff_at"][:10] < f"{row['season'] - 1}-12-01"
    return early if level == "early" else not early


# --- Scoring --------------------------------------------------------------------------------------------------------
def _flags(market, rows, rule):
    out = []
    for r in rows:
        s = side_of(rule, r, market)
        if s is None:
            continue
        o = selector.outcome(market, s, r["realised"])
        out.append(o)
    return out


def _top_curve(market, rows, ref):
    """Win flags (pushes dropped) of `ref`'s picks in descending confidence: prefix sums give the matched-count comparator."""
    ranked = sorted((r for r in rows if ref in r["vectors"]), key=lambda r: (-max(r["vectors"][ref][i] for i in combine.sides(market)), r["game_id"]))
    flags = []
    for r in ranked:
        s = combine.pick(r["vectors"][ref], market)
        if s is None:
            continue
        o = selector.outcome(market, s, r["realised"])
        flags.append(None if o == "push" else int(o == "win"))
    return flags


def _matched(curve, n_picks):
    """Win rate among the comparator's `n_picks` most confident picks (pushes in that span not counted)."""
    span = [f for f in curve[:n_picks] if f is not None]
    return (sum(span) / len(span), len(span)) if span else (None, 0)


def score(market, rows, rule, curve) -> dict:
    res = _flags(market, rows, rule)
    w, l = res.count("win"), res.count("loss")
    n = w + l
    out = {"picks": len(res), "wins": w, "losses": l, "pushes": res.count("push"), "coverage": len(res) / len(rows) if rows else 0,
           "accuracy": w / n if n else None}
    cmp_rate, cmp_n = _matched(curve, len(res))
    out["comparator_accuracy"], out["comparator_n"] = cmp_rate, cmp_n
    if n and cmp_rate is not None:
        p1 = w / n
        out["lift"] = p1 - cmp_rate
        out["se"] = math.sqrt(p1 * (1 - p1) / n + cmp_rate * (1 - cmp_rate) / cmp_n) or 1e-9
    else:
        out["lift"], out["se"] = None, None
    return out


def _log_loss(rows, key):
    rs = [r for r in rows if key in r["vectors"]]
    return sum(combine.log_loss(r["vectors"][key], r["realised"]) for r in rs) / len(rs) if rs else None


def _best_single(rows, members):
    return min(members, key=lambda m: (_log_loss(rows, m), members.index(m)))


def fold(market, rows, members, rules, select_seasons, assess_season, carry=()) -> dict:
    """Select on `select_seasons` only, then score the frozen qualifiers (plus rules frozen in earlier folds, `carry`, and
    the best point-lift rule for transparency) once on `assess_season`."""
    sel = [r for r in rows if r["season"] in select_seasons]
    ass = [r for r in rows if r["season"] == assess_season]
    best = _best_single(sel, members)
    z = NormalDist().inv_cdf(1 - ALPHA / len(rules))
    curves = {(part, lv): _top_curve(market, [r for r in rs if in_level(r, lv)], best)
              for part, rs in (("sel", sel), ("ass", ass)) for lv in LEVELS}
    scored = []
    for rule in rules:
        rs = [r for r in sel if in_level(r, rule["level"])]
        s = score(market, rs, rule, curves[("sel", rule["level"])])
        s["lower"] = s["lift"] - z * s["se"] if s["lift"] is not None else None
        s["qualifies"] = (s["lower"] is not None and s["lower"] > 0 and s["wins"] + s["losses"] >= MIN_PICKS
                          and s["coverage"] >= MIN_COVERAGE)
        scored.append({"id": rule["id"], **s})
    by_id = {r["id"]: r for r in rules}
    eligible = [s for s in scored if s["lift"] is not None and s["wins"] + s["losses"] >= MIN_PICKS and s["coverage"] >= MIN_COVERAGE]
    frozen = [s["id"] for s in scored if s["qualifies"]]
    top_point = max(eligible, key=lambda s: (s["lift"], s["id"]))["id"] if eligible else None
    assessed = {}
    for rid in dict.fromkeys(list(carry) + frozen + ([top_point] if top_point else [])):
        rule = by_id[rid]
        rs = [r for r in ass if in_level(r, rule["level"])]
        a = score(market, rs, rule, curves[("ass", rule["level"])])
        for ref in (members[0], "average"):
            a[f"vs_{ref}_matched_accuracy"] = _matched(_top_curve(market, rs, ref), a["picks"])[0]
        a["selected_because"] = ("qualified" if rid in frozen else "frozen in an earlier fold" if rid in carry
                                 else "best point lift (transparency only; did not qualify)")
        assessed[rid] = a
    return {"select_seasons": list(select_seasons), "assess_season": assess_season, "best_single": best,
            "rules_tried": len(rules), "bonferroni_z": z, "qualified": frozen, "top_point_rule": top_point,
            "selection_games": len(sel), "assessment_games": len(ass), "assessed": assessed,
            "top10_selection": sorted(eligible, key=lambda s: -s["lift"])[:10], "all_rules": scored}


def probability_quality(rows, members, seasons) -> dict:
    out = {"per_subset": {}, "complementarity": []}
    subsets = [c for k in range(1, len(members) + 1) for c in combinations(members, k)]
    for s in subsets:
        key = "+".join(s)
        for r in rows:
            r["_avg"] = combine.average([r["vectors"][m] for m in s])
        out["per_subset"][key] = {str(se): sum(combine.log_loss(r["_avg"], r["realised"]) for r in rows if r["season"] == se)
                                  / max(1, sum(1 for r in rows if r["season"] == se)) for se in seasons}
        out["per_subset"][key]["all"] = sum(combine.log_loss(r["_avg"], r["realised"]) for r in rows) / len(rows)
    for r in rows:
        r.pop("_avg", None)
    for s in subsets:
        for m in members:
            if m in s:
                continue
            bigger = tuple(x for x in members if x in s or x == m)
            d = out["per_subset"]["+".join(bigger)]["all"] - out["per_subset"]["+".join(s)]["all"]
            out["complementarity"].append({"subset": "+".join(s), "add": m, "log_loss_change": d})
    out["complementarity"].sort(key=lambda c: c["log_loss_change"])
    return out


def analyse(market, table, winner_table, members, first, seasons) -> dict:
    fav = {r["game_id"]: max(r["vectors"][winner_table["primary"]][0], r["vectors"][winner_table["primary"]][2])
           for r in winner_table["rows"]}
    complete = [r for r in table if all(m in r["vectors"] for m in members) and r["realised"] is not None]
    for r in complete:
        r["favourite"] = fav[r["game_id"]]
        r["vectors"]["average"] = combine.average([r["vectors"][m] for m in members])
    stack_fits = cr._stack_walk(market, complete, members, first, seasons)
    rows = [r for r in complete if r["season"] in seasons]
    with_stack = any("stack" in r["vectors"] for r in rows)
    rules = registry(market, members, with_stack)
    folds, frozen_at = [], {}
    for a in seasons[1:]:
        f = fold(market, rows, members, rules, [s for s in seasons if s < a], a, carry=list(frozen_at))
        for rid in f["qualified"]:
            frozen_at.setdefault(rid, a)
        folds.append(f)
    # Deployable: qualified on selection data and a positive lift in every assessment season from its first freeze on.
    deploy = [rid for rid, a0 in frozen_at.items()
              if all((f["assessed"][rid]["lift"] or 0) > 0 for f in folds if f["assess_season"] >= a0)]
    return {"market": market, "members": members, "games": len(rows), "seasons": seasons,
            "rules_per_fold": len(rules), "stack_fits": stack_fits, "folds": folds,
            "probability_quality": probability_quality(rows, members, seasons), "deploy": deploy}


def run(conn, sport: str, out_dir: Optional[Path] = None, log: Callable[[str], None] = print) -> dict:
    started = utc_now()
    tables, first, version, members, seasons = (nba_tables if sport == "nba" else nhl_tables)(conn)
    seasons = list(seasons)
    winner_table = {"primary": members["winner"][0], "rows": tables["winner"]}
    report = {"kind": "combination-search", "sport": sport, "started_at": started, "dataset_version": version,
              "exploratory": True, "markets": {}}
    for market, table in tables.items():
        ms = members["winner"] if market == "winner" else members["lines"]
        t0 = datetime.now()
        report["markets"][market] = analyse(market, table, winner_table, ms, first, seasons)
        a = report["markets"][market]
        log(f"{sport} {market}: {a['games']} games, {a['rules_per_fold']} rules/fold, qualified per fold "
            f"{[len(f['qualified']) for f in a['folds']]}, deploy {a['deploy']}, {(datetime.now() - t0).seconds} s")
    report["finished_at"] = utc_now()
    out_dir = out_dir or config.data_dir() / "reports" / f"combination-search-{sport}-{started.replace(':', '')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=1, default=str))
    report["output_dir"] = str(out_dir)
    log(f"wrote {out_dir / 'report.json'}")
    return report
