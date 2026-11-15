"""Retrospective combination and selection study (docs/MODEL_SELECTION.md §5). Exploratory, run once per sport.

Reads only already-used development seasons (NBA warm-up + validation, NHL warm-up + development), writes a report file and no
ledger row. Every member forecast is a walk-forward prediction made at the night-before cutoff with results available then.
Line markets are evaluated at the primary's model-only line (the live rule), as no historical bookmaker lines exist.
`stored()` (end of file) analyses combinations of the SAVED out-of-sample forecasts instead, refitting nothing.
"""
import json
import math
from functools import lru_cache
from itertools import combinations
from pathlib import Path
from typing import Callable, Optional

from . import backtest, combine, config, markets, metrics, nhl_eval, selector
from .errors import InvalidRequest
from .db import utc_now

STACK_MIN_TRAIN = 300
AGREE_CONF = {"default": (0.55, 0.60), "reg3": (0.45, 0.50)}
NBA_EVAL, NHL_EVAL = backtest.PERIODS["validation"], nhl_eval.PERIODS["development"]
SIDE_IDX = {"a": 0, "mid": 1, "b": 2}


# --- Member forecasts -------------------------------------------------------------------------------------------------
def _realised(market, line, home, away, decision=None):
    s = markets.settle(market, line, status="final", home=home, away=away, decision=decision)
    return SIDE_IDX.get(s)


def _tables(rows, winner_models, dist_models, line_markets, events=None, decision_of=None,
            book_lines=None) -> tuple[dict, dict]:
    """market -> [row] with member vectors, and each model's first out-of-sample season. With `book_lines`
    ({game_id: {market: line}}), the line markets other than reg3 use the bookmaker's line instead of the primary's model
    line, for every member's vector and the settlement; a game without one keeps the model line, marked
    `line_basis` "model" (for learning only: the replay scores "book" rows), so earlier seasons still train combiners."""
    first = {}
    win = {}
    for m in winner_models:
        win[m.name] = {p["game_id"]: p["p_home"] for p in backtest.run(m, rows, events)}
        first[m.name] = getattr(m, "oos_from_season", lambda: None)() or getattr(m, "first_fit_season", None)
    dists = {}
    for m in dist_models:
        out = backtest.run(m, rows, events, extra=lambda model, g: {"dist": model.distribution(g)})
        dists[m.name] = {p["game_id"]: p["dist"] for p in out}
        first[m.name] = getattr(m, "oos_from_season", lambda: None)()
    primary_dist = dist_models[0].name
    tables = {"winner": []}
    for m in line_markets:
        tables[m] = []
    for r in rows:
        g = r.game
        base = {"game_id": g.game_id, "season": g.season, "cutoff_at": r.cutoff_at, "tipoff_at": g.tipoff_at,
                "result_available_at": r.result_available_at}
        dec = decision_of(r) if decision_of else None
        tables["winner"].append({**base, "line": None, "realised": 0 if r.home_score > r.away_score else 2,
                                 "vectors": {n: (v[g.game_id], 0.0, 1 - v[g.game_id]) for n, v in win.items()}})
        prim = dists[primary_dist][g.game_id]
        for m in line_markets:
            line = None if m == "reg3" else markets.model_line(prim, m)
            basis = {}
            if m != "reg3" and book_lines is not None:
                book = book_lines.get(g.game_id, {}).get(m)
                basis = {"line_basis": "model" if book is None else "book"}    # model-line rows: learning only
                line = line if book is None else book
            vecs = {n: tuple(d[g.game_id].market(m, line)) for n, d in dists.items() if d[g.game_id] is not None}
            tables[m].append({**base, "line": line, "realised": _realised(m, line, r.home_score, r.away_score, dec),
                              "vectors": vecs, **basis})
    return tables, first


def nba_tables(conn):
    from .models.efficiency import EfficiencyMargin
    from .models.gbt import GradientBoostedTrees
    from .models.logistic import LogisticElo, LogisticV2
    from .models.elo import Elo
    from .models.score_candidates import GbtPoints, RidgePoints
    from . import nba_markets
    _, cfg = backtest.load_frozen_decision(conn)
    rows, events = backtest.load_rows(conn, "validation"), backtest.load_box_events(conn, "validation")
    if {r.game.season for r in rows} - set(backtest.LOADABLE["validation"]):
        raise ValueError("study read seasons outside warm-up/validation")
    points, _ = nba_markets.sealed_model(conn)
    tables, first = _tables(rows, [Elo(cfg), LogisticElo(cfg), LogisticV2(cfg), EfficiencyMargin(), GradientBoostedTrees(cfg)],
                            [points, RidgePoints(cfg), GbtPoints(cfg)], ("spread", "total", "team_total_home", "team_total_away"),
                            events)
    first["elo"] = first["points_normal"] = min(backtest.PERIODS["warmup"])
    return tables, first, backtest.dataset_version(rows), {"winner": ("elo", "logistic", "logistic_v2", "efficiency_margin", "gbt_v1"),
                                                            "lines": ("points_normal", "ridge_points", "gbt_points")}, NBA_EVAL


def nhl_tables(conn):
    from .models.hockey import GoalsConfig, HockeyEloConfig, hockey_gbt, hockey_logistic, GoalsPoisson, HockeyElo
    from .models.score_candidates import GoalsDraw, GoalsGlm
    from . import sports
    d = nhl_eval.load_decision(conn)
    rows = nhl_eval.load_rows(conn, "development")
    if {r.game.season for r in rows} - set(nhl_eval.LOADABLE["development"]):
        raise ValueError("study read seasons outside warm-up/development")
    ec, gc, key = HockeyEloConfig(**d["hockey_elo_config"]), GoalsConfig(**d["goals_config"]), sports.NHL.rating_key
    tables, first = _tables(rows, [HockeyElo(ec, key), GoalsPoisson(gc, key), hockey_logistic(ec, gc, key), hockey_gbt(ec, gc, key)],
                            [GoalsPoisson(gc, key), GoalsDraw(gc, key), GoalsGlm(ec, key)],
                            ("reg3", "spread", "total", "team_total_home", "team_total_away"), None, lambda r: r.decision)
    first["hockey_elo"] = first["goals_poisson"] = min(nhl_eval.PERIODS["warmup"])
    return tables, first, nhl_eval.dataset_version(rows), {"winner": ("hockey_elo", "goals_poisson", "hockey_logistic", "hockey_gbt"),
                                                          "lines": ("goals_poisson", "goals_draw", "goals_glm")}, NHL_EVAL


# --- Analysis -------------------------------------------------------------------------------------------------------
def _result(market, vec, realised):
    side = combine.pick(vec, market)
    return None if side is None else selector.outcome(market, side, realised)


def _summary(market, rows, key):
    vecs = [r["vectors"][key] for r in rows]
    real = [r["realised"] for r in rows]
    res = [_result(market, v, y) for v, y in zip(vecs, real)]
    w, l, p = res.count("win"), res.count("loss"), res.count("push")
    bins = [[0, 0.0, 0] for _ in range(5)]
    for v, y, o in zip(vecs, real, res):
        side = combine.pick(v, market)
        if side is None or o == "push":
            continue
        lo = 1 / len(combine.sides(market))
        b = min(4, int((v[side] - lo) / (1 - lo) * 5))
        bins[b][0] += 1
        bins[b][1] += v[side]
        bins[b][2] += o == "win"
    return {"n": len(rows), "wins": w, "losses": l, "pushes": p, "accuracy": w / (w + l) if w + l else None,
            "accuracy_ci95": nhl_eval._wilson(w, w + l), "log_loss": metrics.log_loss_multi(vecs, real),
            "brier": metrics.brier_multi(vecs, real),
            "calibration": [{"n": n, "mean_p": s / n, "win_rate": k / n} for n, s, k in bins if n]}


def _stack_walk(market, rows, members, first, seasons):
    """Season-start refits on earlier seasons' out-of-fold member forecasts only. Adds 'stack' to rows it covers."""
    fits = []
    oos = max(first[m] for m in members)
    for s in seasons:
        target = [r for r in rows if r["season"] == s]
        train = [r for r in rows if oos <= r["season"] < s]
        if len(train) < STACK_MIN_TRAIN or not target:
            fits.append({"season": s, "training_games": len(train), "fitted": False})
            continue
        start = min(r["cutoff_at"] for r in target)
        if any(r["result_available_at"] > start for r in train):
            raise ValueError("stacker training result not available before its season")
        model = combine.fit_stack([([r["vectors"][m] for m in members], r["realised"]) for r in train], market)
        for r in target:
            r["vectors"]["stack"] = combine.stack(model, [r["vectors"][m] for m in members])
        fits.append({"season": s, "training_games": model["n"], "training_seasons": sorted({r["season"] for r in train}),
                     "weights": dict(zip(members, model["weights"])), "bias": model["bias"], "converged": model["converged"],
                     "fitted": True})
    return fits


def _top(market, rows, key, count):
    ranked = sorted(rows, key=lambda r: (-max(r["vectors"][key][i] for i in combine.sides(market)), r["game_id"]))
    return ranked[:count]


def _flags(market, rows, key, side_of=None):
    out = []
    for r in rows:
        side = side_of(r) if side_of else combine.pick(r["vectors"][key], market)
        if side is None:
            continue
        o = selector.outcome(market, side, r["realised"])
        if o != "push":
            out.append({"cutoff_at": r["cutoff_at"], "win": int(o == "win")})
    return out


def _vs_confidence(market, rows, chosen, side_of, primary, label, best="best_prior"):
    """Agreement-selected games against the most-confident games of the primary, the average and the best single member at
    the same coverage. The best single member for a season is chosen on EARLIER seasons only (`best_prior`); comparisons with
    it use only the games that have such a choice."""
    out = {"rule": label, "games": len(chosen), "coverage": len(chosen) / len(rows) if rows else None}
    for ref in dict.fromkeys((primary, "average", best)):
        base = [r for r in rows if ref in r["vectors"]]
        mine = [r for r in chosen if ref in r["vectors"]]
        sel = _flags(market, mine, None, side_of)
        top = _flags(market, _top(market, base, ref, len(mine)), ref)
        if ref == primary:
            out["record"] = {"wins": sum(f["win"] for f in sel), "decided": len(sel),
                             "accuracy": sum(f["win"] for f in sel) / len(sel) if sel else None}
        out[f"vs_{ref}_confidence"] = {"games": len(mine), "accuracy": sum(f["win"] for f in top) / len(top) if top else None,
                                       "diff": nhl_eval._subset_diff_bootstrap(sel, top, "win", "win") if sel and top else None}
    return out


def _agreement_side(market, members):
    def f(kind):
        def side_of(r):
            a = combine.agreement({m: r["vectors"][m] for m in members}, members, market)
            return SIDE_IDX[a["side"]] if a[kind] else None
        return side_of
    return f


HISTORICAL_REFERENCE = ("outcome base rate of the picked side among earlier settled games at the same line; a line with "
                        "too few earlier games is not judged (lines are never pooled; no historical bookmaker odds exist; "
                        "lift is over that base rate, not over a market)")


def history_items(market, rows, members):
    """Selector items from walk-forward rows. A member or combination missing on a row is simply absent from its vectors
    (the selector treats it as 'source unavailable' there); agreement counts the members present against all of them."""
    items = []
    for r in rows:
        a = combine.agreement({m: r["vectors"][m] for m in members if m in r["vectors"]}, members, market)
        items.append({"game_id": r["game_id"], "week": selector.week_of(r["tipoff_at"]), "tipoff_at": r["tipoff_at"],
                      "result_available_at": r["result_available_at"], "vectors": r["vectors"], "line": r.get("line"),
                      "agreement": {"majority_side": a["side"] if a["majority"] else None,
                                    "unanimous_side": a["side"] if a["unanimous"] else None},
                      "bookmaker": None, "realised": r["realised"]})
    return items


def _simulate(market, rows, sources, members, best=None, filters=selector.FILTERS):
    """Weekly selector over the evaluated seasons, evidence starting empty, judged on the historical basis (outcome base
    rate at the line: no historical odds exist). Returns per-game selections and a summary."""
    from datetime import datetime, timedelta
    items = history_items(market, rows, members)
    items.sort(key=lambda i: (i["tipoff_at"], i["game_id"]))
    t0 = datetime.strptime(items[0]["tipoff_at"][:10], "%Y-%m-%d")
    t = t0 - timedelta(days=t0.weekday()) + timedelta(hours=selector.DECISION_HOUR_UTC)
    end = datetime.strptime(items[-1]["tipoff_at"][:10], "%Y-%m-%d") + timedelta(days=8)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    decisions, picks, incumbent, changes = [], [], None, 0
    while t < end:
        cut, nxt = t.strftime(fmt), (t + timedelta(days=7)).strftime(fmt)
        evidence = [i for i in items if i["result_available_at"] <= cut and i["tipoff_at"] < cut]
        d = selector.decide(evidence, market, sources, incumbent, "historical", filters) if evidence else None
        week_items = [i for i in items if cut <= i["tipoff_at"] < nxt]
        status = d["status"] if d else "insufficient_evidence"
        if d and d["status"] == "active" and incumbent and d["rule"] != incumbent:
            changes += 1
        incumbent = d["rule"] if d else None          # as live: the previous decision's rule, none after an abstention
        if d and d["status"] == "active":
            rates = selector.reference_rates(evidence, market, "historical")
            for i in week_items:
                s = selector.apply(d, i, market)
                if s["status"] == "pick":
                    side = SIDE_IDX[s["side"]]
                    picks.append({"game_id": i["game_id"], "cutoff_at": i["tipoff_at"], "side": side, "lower": d["lower"],
                                  "result": selector.outcome(market, side, i["realised"]),
                                  "reference": selector.reference(i, side, rates, market, "historical"), "p": s["probability"]})
        decisions.append({"at": cut, "status": status, "rule": d["rule"] if d else None,
                          "lower": d.get("lower") if d else None, "games": len(week_items)})
        t += timedelta(days=7)
    decided = [p for p in picks if p["result"] != "push"]
    wins = sum(p["result"] == "win" for p in decided)
    # Every pick counts in the record; only the base-rate comparison is limited to lines with enough reference games.
    judged = [p for p in decided if p["reference"] is not None]
    summary = {"unjudged_picks": sum(p["reference"] is None for p in picks), "judged_decided": len(judged),
               "reference": HISTORICAL_REFERENCE, "weeks": len(decisions), "active_weeks": sum(d["status"] == "active" for d in decisions),
               "rule_changes": changes, "games": len(items), "picks": len(picks), "pushes": len(picks) - len(decided),
               "coverage": len(picks) / len(items), "wins": wins, "losses": len(decided) - wins,
               "accuracy": wins / len(decided) if decided else None,
               "lift": (sum(p["result"] == "win" for p in judged) - sum(p["reference"] for p in judged)) / len(judged) if judged else None,
               "accuracy_ci95": nhl_eval._wilson(wins, len(decided)),
               "rules_used": sorted({json.dumps(d["rule"], sort_keys=True) for d in decisions if d["rule"]})}
    if decided:
        # Most-confident games over the same weeks at the same count: does selection beat confidence alone?
        span = [r for r in rows if picks[0]["cutoff_at"] <= r["tipoff_at"]]
        mine = [{"cutoff_at": p["cutoff_at"], "win": int(p["result"] == "win")} for p in decided]
        have = {r["game_id"] for r in rows if best and best in r["vectors"]}
        for name, ref in (("primary", sources[0]), ("best_single", best)):
            if ref is None:
                continue
            base = span if name == "primary" else [r for r in span if r["game_id"] in have]
            sel = mine if name == "primary" else [{"cutoff_at": p["cutoff_at"], "win": int(p["result"] == "win")}
                                                  for p in decided if p["game_id"] in have]
            top = _flags(market, _top(market, base, ref, len(sel)), ref)
            summary[f"{name}_confidence_same_count"] = {
                "model": ref, "games": len(sel), "accuracy": sum(f["win"] for f in top) / len(top) if top else None,
                "diff": nhl_eval._subset_diff_bootstrap(sel, top, "win", "win") if top and sel else None,
                "note": "ranked within the same span, using the span's own confidence distribution (descriptive)"
                        + ("; the best single member of each season is chosen on earlier seasons only" if name == "best_single" else "")}
    return {"summary": summary, "decisions": decisions}, picks


def analyse(market, table, members, first, seasons):
    primary = members[0]
    complete = [r for r in table if all(m in r["vectors"] for m in members) and r["realised"] is not None]
    for r in complete:
        r["vectors"]["average"] = combine.average([r["vectors"][m] for m in members])
    fits = _stack_walk(market, complete, members, first, seasons)
    # Strongest single member per season, chosen by log loss on EARLIER out-of-sample seasons only (never the evaluated one).
    oos = max(first[m] for m in members)
    best_prior = {}
    for s_ in seasons:
        prior = [r for r in complete if oos <= r["season"] < s_]
        if not prior:
            best_prior[s_] = {"model": None, "games": 0}
            continue
        b = min(members, key=lambda m: (sum(combine.log_loss(r["vectors"][m], r["realised"]) for r in prior), members.index(m)))
        best_prior[s_] = {"model": b, "games": len(prior), "seasons": sorted({r["season"] for r in prior})}
        for r in complete:
            if r["season"] == s_:
                r["vectors"]["best_prior"] = r["vectors"][b]
    rows = [r for r in complete if r["season"] in seasons]
    stack_rows = [r for r in rows if "stack" in r["vectors"]]
    methods = list(members) + ["average"]
    out = {"market": market, "members": list(members), "primary": primary, "games": len(rows),
           "seasons": sorted({r["season"] for r in rows}), "excluded_incomplete": len([r for r in table if r["season"] in seasons]) - len(rows),
           "methods": {m: _summary(market, rows, m) for m in methods},
           "stack": {"fits": fits, "seasons": sorted({r["season"] for r in stack_rows}), "games": len(stack_rows),
                     "methods_on_stack_games": {m: _summary(market, stack_rows, m) for m in methods + ["stack"]} if stack_rows else None}}
    best = min(members, key=lambda m: out["methods"][m]["log_loss"])
    out["best_single_by_log_loss"] = best          # descriptive only (hindsight); comparisons use best_prior
    out["best_single_prior"] = best_prior
    paired = {}
    for a, b, rs in (("average", primary, rows), (best, primary, rows), ("stack", primary, stack_rows),
                     ("stack", "average", stack_rows), ("stack", best, stack_rows)):
        if a == b or not rs:
            continue
        diffs = [combine.log_loss(r["vectors"][a], r["realised"]) - combine.log_loss(r["vectors"][b], r["realised"]) for r in rs]
        paired[f"{a}-{b}"] = backtest.block_bootstrap(diffs, [r["cutoff_at"] for r in rs])
    out["log_loss_diff"] = paired
    # Agreement and agreement + confidence, each against confidence-only filters at matched coverage.
    side = _agreement_side(market, members)
    agree = []
    for kind in ("majority", "unanimous"):
        chosen = [r for r in rows if side(kind)(r) is not None]
        agree.append(_vs_confidence(market, rows, chosen, side(kind), primary, kind))
        for t in AGREE_CONF.get(market, AGREE_CONF["default"]):
            sub = [r for r in chosen if r["vectors"]["average"][side(kind)(r)] + 1e-9 >= t]
            agree.append(_vs_confidence(market, rows, sub, side(kind), primary, f"{kind}+average>={t}"))
    for m in members[1:]:
        def pair_side(r, m=m):
            a, b = combine.pick(r["vectors"][primary], market), combine.pick(r["vectors"][m], market)
            return a if a is not None and a == b else None
        chosen = [r for r in rows if pair_side(r) is not None]
        agree.append(_vs_confidence(market, rows, chosen, pair_side, primary, f"pair:{primary}+{m}"))
    out["agreement"] = agree
    # Conditional disagreement: when a member and the primary pick differently, who was right.
    cond = {}
    for m in members[1:]:
        dis = [r for r in rows if combine.pick(r["vectors"][m], market) != combine.pick(r["vectors"][primary], market)]
        right = lambda r, k: _result(market, r["vectors"][k], r["realised"]) == "win"
        la = [combine.log_loss(r["vectors"][primary], r["realised"]) for r in rows]
        lb = [combine.log_loss(r["vectors"][m], r["realised"]) for r in rows]
        ma, mb = sum(la) / len(la), sum(lb) / len(lb)
        cov = sum((x - ma) * (y - mb) for x, y in zip(la, lb))
        corr = cov / math.sqrt(sum((x - ma) ** 2 for x in la) * sum((y - mb) ** 2 for y in lb))
        cond[m] = {"disagreements": len(dis), "rate": len(dis) / len(rows), "primary_right": sum(right(r, primary) for r in dis),
                   "member_right": sum(right(r, m) for r in dis), "log_loss_correlation": corr}
    out["disagreement_vs_primary"] = cond
    # Fixed-threshold tables (descriptive) and the weekly selector simulation.
    out["thresholds"] = {m: _thresholds(market, rows, m) for m in methods}
    sources = list(members) + ["average", "stack"]
    sim, picks = _simulate(market, rows, sources, members, "best_prior")
    out["selector_simulation"] = sim
    return out, picks


def _thresholds(market, rows, key):
    out = []
    for t in selector.thresholds(market):
        sel = [r for r in rows if max(r["vectors"][key][i] for i in combine.sides(market)) + 1e-9 >= t]
        f = _flags(market, sel, key)
        w = sum(x["win"] for x in f)
        out.append({"threshold": t, "picks": len(sel), "coverage": len(sel) / len(rows), "decided": len(f),
                    "accuracy": w / len(f) if f else None, "ci95": nhl_eval._wilson(w, len(f))})
    return out


def _market_choice(per_market_picks):
    """Game-level recommendation: among markets the simulated selector picked, the highest decision lower bound."""
    by_game = {}
    for m, picks in per_market_picks.items():
        for p in picks:
            by_game.setdefault(p["game_id"], []).append((p["lower"], m, p))
    chosen = [max(v, key=lambda x: (x[0], -sorted(per_market_picks).index(x[1])))[1:] for v in by_game.values()]   # = selector.choose_market
    decided = [(m, p) for m, p in chosen if p["result"] != "push"]
    wins = sum(p["result"] == "win" for _, p in decided)
    return {"games_with_recommendation": len(chosen), "by_market": {m: sum(1 for c, _ in chosen if c == m) for m in per_market_picks},
            "wins": wins, "losses": len(decided) - wins, "pushes": len(chosen) - len(decided),
            "accuracy": wins / len(decided) if decided else None,
            "lift": (lambda j: (sum(p["result"] == "win" for p in j) - sum(p["reference"] for p in j)) / len(j) if j else None)(
                [p for _, p in decided if p["reference"] is not None])}


def run(conn, sport: str, out_dir: Optional[Path] = None, log: Callable[[str], None] = print) -> dict:
    started = utc_now()
    tables, first, version, members, seasons = (nba_tables if sport == "nba" else nhl_tables)(conn)
    report = {"kind": "retrospective exploratory study", "sport": sport, "generated_at": started, "dataset_version": version,
              "evaluated_seasons": list(seasons), "first_out_of_sample_season": first, "selector_version": selector.version(),
              "design": "docs/MODEL_SELECTION.md (5ea11f5)", "markets": {}}
    picks = {}
    for market, table in tables.items():
        log(f"{sport} {market}: {len(table)} games")
        mem = members["winner"] if market == "winner" else members["lines"]
        report["markets"][market], picks[market] = analyse(market, table, mem, first, seasons)
    report["market_choice_simulation"] = _market_choice(picks)
    report["note"] = ("Exploratory: these seasons were already used to choose the primary models. Line markets use the "
                      "primary's model-only line; no bookmaker comparison exists. Not prospective evidence.")
    out_dir = Path(out_dir or config.data_dir() / "reports" / f"combination-{sport}-{started.replace(':', '')}")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=1, default=str))
    report["output_dir"] = str(out_dir)
    log(f"Report written to {out_dir}")
    return report


EVIDENCE_DIR = Path(__file__).resolve().parents[2] / "docs" / "evidence" / "combination-study-2026-10-02"


def compact(sport: str) -> dict:
    """The recorded one-time study, trimmed for the app. Retrospective and exploratory: never prospective evidence."""
    path = EVIDENCE_DIR / f"{sport}.json"
    if not path.is_file():
        return {"sport": sport, "status": "unavailable", "markets": {}}
    r = json.loads(path.read_text())
    out = {}
    for market, m in r["markets"].items():
        out[market] = {
            "games": m["games"], "seasons": m["seasons"], "members": m["members"], "primary": m["primary"],
            "best_single_by_log_loss": m["best_single_by_log_loss"], "best_single_prior": m.get("best_single_prior"),
            "methods": {k: {x: v[x] for x in ("n", "accuracy", "log_loss", "brier", "calibration")} for k, v in m["methods"].items()},
            "stack": {"seasons": m["stack"]["seasons"], "games": m["stack"]["games"],
                      "methods": None if not m["stack"]["methods_on_stack_games"] else
                      {k: {x: v[x] for x in ("n", "accuracy", "log_loss")} for k, v in m["stack"]["methods_on_stack_games"].items()}},
            "log_loss_diff": {k: {"mean": v["mean"], "ci95": v["ci95"]} for k, v in m["log_loss_diff"].items()},
            "agreement": [{"rule": a["rule"], "coverage": a["coverage"], "accuracy": a["record"]["accuracy"],
                           "vs_primary_confidence": (a.get(f"vs_{m['primary']}_confidence") or {}).get("diff"),
                           "vs_average_confidence": (a.get("vs_average_confidence") or {}).get("diff"),
                           "vs_best_prior_confidence": (a.get("vs_best_prior_confidence") or {}).get("diff"),
                           "best_prior_games": (a.get("vs_best_prior_confidence") or {}).get("games")}
                          for a in m["agreement"]],
            "disagreement_vs_primary": m["disagreement_vs_primary"],
            "selector_simulation": {k: v for k, v in m["selector_simulation"]["summary"].items()},
        }
    return {"sport": sport, "kind": r["kind"], "generated_at": r["generated_at"], "evaluated_seasons": r["evaluated_seasons"],
            "design": r["design"], "markets": out, "market_choice_simulation": r["market_choice_simulation"],
            "note": r["note"] + " At a model-only line the primary is close to 50/50 by construction, so comparisons with the "
                                "primary's own confidence are not informative for NBA line markets."}


# --- Combinations of stored forecasts (no member is refitted) -------------------------------------------------------------
# Reads the saved walk-forward out-of-sample tables (candidate pool stages 1–2, agreement_tracking._oof): every member's
# night-before probability on the same games. Equal-weight averages and vote agreement only — nothing is fitted here, so a
# combination cannot see a result. Choices for the later-period check are made on earlier seasons only, then frozen.
LATER_SEASONS = 2                 # the most recent seasons form the later period
MIN_LATER = 300                   # decided later games before a frozen comparison is more than "insufficient evidence"
PAIR_CI_TOP = 10                  # pairs with a bootstrap interval (by earlier-period log loss); the rest are point estimates
POSTGAME_INPUT = "_goalie"        # candidates given the actual starting goalie (known only after the start)
STORED_NOTE = ("Historical out-of-sample forecasts saved by the candidate pool (walk-forward, night-before cutoff). Every "
               "season here was already used to choose or screen models, including the spent reserved-test seasons: all "
               "results are exploratory, never an untouched test. No historical bookmaker odds: nothing here measures profit. "
               "Research only: nothing here changes the live forecasts or selects a model.")
LINE_NOTE = "Line markets are settled at the primary model's own line (no historical bookmaker lines exist)."


def _source_identity(sport: str) -> list:
    import hashlib
    base = config.data_dir() / "research" / "candidates"
    out = []
    for stage in ("screen", "assess"):
        p = base / f"oof-{sport}-{stage}.json.gz"
        if p.is_file():
            out.append({"stage": stage, "file": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()[:16]})
    return out


def _wins(market, vec, realised):
    """1 win, 0 loss, None for a push or no pick (exact tie)."""
    r = _result(market, vec, realised)
    return None if r in (None, "push") else int(r == "win")


def _metrics(market, rows, key):
    s = _summary(market, rows, key)
    return {k: s[k] for k in ("n", "wins", "losses", "pushes", "accuracy", "accuracy_ci95", "log_loss", "brier")} | \
        {"no_pick": s["n"] - s["wins"] - s["losses"] - s["pushes"]}


def _paired(rows, a, b, metric):
    """Per-game a − b on identical games with the week-block bootstrap. metric 'log_loss' (negative = a better) or
    'accuracy' (games where both pick and neither pushes; positive = a better)."""
    if metric == "log_loss":
        vals = [(combine.log_loss(r["vectors"][a], r["realised"]) - combine.log_loss(r["vectors"][b], r["realised"]),
                 r["cutoff_at"]) for r in rows]
    else:
        vals = [(wa - wb, r["cutoff_at"]) for r in rows
                for wa, wb in [(r["w"].get(a), r["w"].get(b))] if wa is not None and wb is not None]
    if not vals:
        return None
    out = backtest.block_bootstrap([v for v, _ in vals], [c for _, c in vals])
    return {"mean": out["mean"], "ci95": out["ci95"], "games": out["games"], "weeks": out["weeks"]}


def _key(members):
    return "avg:" + "+".join(members)


def _ensure(rows, market, members):
    """Adds the equal-weight average of `members` (and its pick result) to each row; a single member is itself."""
    k = members[0] if len(members) == 1 else _key(members)
    for r in rows:
        if k not in r["vectors"]:
            r["vectors"][k] = combine.average([r["vectors"][m] for m in members])
        if k not in r["w"]:
            r["w"][k] = _wins(market, r["vectors"][k], r["realised"])
    return k


def _log_loss(rows, k):
    return sum(combine.log_loss(r["vectors"][k], r["realised"]) for r in rows) / len(rows)


def _pair_detail(market, rows, a, b, strongest):
    k = _key([a, b])
    picks = [(combine.pick(r["vectors"][a], market), combine.pick(r["vectors"][b], market), r) for r in rows]
    agree = [(x, r) for x, y, r in picks if x is not None and x == y]
    dis = [r for x, y, r in picks if x != y]
    agree_dec = [r["w"][a] for _, r in agree if r["w"][a] is not None]
    both_wrong = sum(1 for r in rows if r["w"][a] == 0 and r["w"][b] == 0)
    return {"members": [a, b], "metrics": _metrics(market, rows, k), "strongest_member": strongest,
            "log_loss_vs_strongest": None, "agreement_rate": len(agree) / len(rows),
            "agree": {"games": len(agree), "decided": len(agree_dec), "wins": sum(agree_dec),
                      "accuracy": sum(agree_dec) / len(agree_dec) if agree_dec else None},
            "disagree": {"games": len(dis), "first_right": sum(r["w"][a] == 1 for r in dis),
                         "second_right": sum(r["w"][b] == 1 for r in dis)},
            "shared_mistakes": {"games": both_wrong, "rate": both_wrong / len(rows)}}


def _add_model(market, rows, base, candidates, later=None):
    """A+B (base) against A+B+C on identical games, for each candidate C."""
    kb = _ensure(rows, market, base)
    out = []
    for c in candidates:
        kc = _ensure(rows, market, base + [c])
        corrected = sum(1 for r in rows if r["w"][kb] == 0 and r["w"][kc] == 1)
        introduced = sum(1 for r in rows if r["w"][kb] == 1 and r["w"][kc] == 0)
        changed = sum(1 for r in rows if combine.pick(r["vectors"][kb], market) != combine.pick(r["vectors"][kc], market))
        out.append({"added": c, "members": base + [c], "metrics": _metrics(market, rows, kc),
                    "log_loss_change": _paired(rows, kc, kb, "log_loss"), "accuracy_change": _paired(rows, kc, kb, "accuracy"),
                    "changed_picks": changed, "corrected": corrected, "introduced": introduced})
    out.sort(key=lambda x: x["log_loss_change"]["mean"])
    return {"base": base, "base_metrics": _metrics(market, rows, kb), "games": len(rows), "candidates": len(candidates),
            "rows": out}


def _agreement_table(market, rows, members):
    """Vote count of the members (agreement) kept apart from the average's probability (confidence)."""
    lo = 1 / len(combine.sides(market))
    by_count, by_conf = {}, {}
    edges = [lo + i * 0.05 for i in range(1, 6)]
    k = _ensure(rows, market, members)
    for r in rows:
        a = combine.agreement({m: r["vectors"][m] for m in members}, members, market)
        if a["side"] is None:
            label = "tie (no plurality)"
            res = None
        else:
            label = a["agreeing"]
            res = selector.outcome(market, SIDE_IDX[a["side"]], r["realised"])
        c = by_count.setdefault(label, {"agreeing": label, "games": 0, "wins": 0, "decided": 0, "conf_sum": 0.0})
        c["games"] += 1
        c["conf_sum"] += max(r["vectors"][k][i] for i in combine.sides(market))
        if res in ("win", "loss"):
            c["decided"] += 1
            c["wins"] += res == "win"
        p = max(r["vectors"][k][i] for i in combine.sides(market))
        band = next((f"{e - 0.05:.2f}–{e:.2f}" for e in edges if p < e), f"≥{edges[-1]:.2f}")
        w = r["w"][k]
        b = by_conf.setdefault(band, {"band": band, "games": 0, "wins": 0, "decided": 0, "unanimous": 0})
        b["games"] += 1
        b["unanimous"] += bool(a["unanimous"])
        if w is not None:
            b["decided"] += 1
            b["wins"] += w
    count_rows = []
    for c in sorted(by_count.values(), key=lambda c: (isinstance(c["agreeing"], str), -c["agreeing"] if isinstance(c["agreeing"], int) else 0)):
        count_rows.append({"agreeing": c["agreeing"], "of": len(members), "games": c["games"], "share": c["games"] / len(rows),
                           "decided": c["decided"], "wins": c["wins"], "accuracy": c["wins"] / c["decided"] if c["decided"] else None,
                           "accuracy_ci95": nhl_eval._wilson(c["wins"], c["decided"]), "mean_average_confidence": c["conf_sum"] / c["games"]})
    conf_rows = [{**b, "accuracy": b["wins"] / b["decided"] if b["decided"] else None,
                  "accuracy_ci95": nhl_eval._wilson(b["wins"], b["decided"]), "unanimous_share": b["unanimous"] / b["games"]}
                 for b in sorted(by_conf.values(), key=lambda b: b["band"])]
    return {"members": members, "games": len(rows), "by_count": count_rows, "by_confidence": conf_rows,
            "tie_rule": "a member at exactly 50/50 abstains; equal leading vote counts are a tie and make no pick",
            "confidence": "the equal-weight average's probability for its most probable side"}


def later_seasons(rows: list) -> Optional[list]:
    """The later period: the last LATER_SEASONS seasons of the saved table, whichever models or results a row holds (so
    later forecast presence can never move the split). None when there is no earlier season."""
    seasons = sorted({r["season"] for r in rows})
    return seasons[-LATER_SEASONS:] if len(seasons) > LATER_SEASONS else None


COVERAGE_DEFINITION = ("Per period: games = every saved game (pending included); settled = games with a result; pending = "
                       "games without one; used = settled games with a forecast from every compared model; "
                       "excluded_missing_member = settled games without (settled − used); missing = per model, settled games "
                       "without its forecast. No forecast is filled in. A rule or average over fewer models needs only its "
                       "own members.")


def coverage(rows: list, models: list) -> dict:
    """One period's games and the compared models' forecast coverage (see COVERAGE_DEFINITION)."""
    settled = [r for r in rows if r["realised"] is not None]
    used = sum(all(m in r["vectors"] for m in models) for r in settled)
    missing = {m: sum(m not in r["vectors"] for r in settled) for m in models}
    return {"games": len(rows), "settled": len(settled), "pending": len(rows) - len(settled), "used": used,
            "excluded_missing_member": len(settled) - used, "missing": {m: n for m, n in missing.items() if n}}


def coverage_by_period(rows: list, models: list) -> dict:
    later_s = later_seasons(rows) or []
    return {p: coverage([r for r in rows if (r["season"] in later_s) == (p == "later")], models) for p in ("earlier", "later")}


def split(rows: list) -> Optional[dict]:
    later_s = later_seasons(rows)
    if later_s is None:
        return None
    return {"earlier_seasons": sorted({r["season"] for r in rows} - set(later_s)), "later_seasons": list(later_s)}


def evidence_state(rows: list, models: list, min_models: int) -> Optional[tuple[str, str]]:
    """(status, reason) when the saved table cannot support a choice on the earlier period checked on the later one;
    None when it can. Decided from the table's seasons, earlier results and the earlier-only roster: never later data.
    An empty shortlist from a valid search is not this: it is a result."""
    if not rows:
        return "no_stored_forecasts", "no saved forecasts for this market"
    sp = split(rows)
    if sp is None:
        n = len({r["season"] for r in rows})
        return "insufficient_evidence", (f"needs more than {LATER_SEASONS} seasons for an earlier/later split "
                                         f"({n} saved season{'s' if n != 1 else ''})")
    if not any(r["realised"] is not None for r in rows if r["season"] in sp["earlier_seasons"]):
        return "insufficient_evidence", "no settled game in the earlier period"
    if not models:
        return "insufficient_evidence", "saved forecasts exist but no model is eligible (see excluded)"
    if len(models) < min_models:
        return "insufficient_evidence", f"only {len(models)} eligible model; at least {min_models} are needed"
    return None


def prepare_stored(market: str, rows: list, models: list) -> Optional[dict]:
    """Rows used (settled, every member present), split into the earlier and later periods; None if too little data.
    A game where a compared model has no saved forecast is excluded from both sides of every comparison and counted in
    `coverage`; the compared models are never reduced."""
    later_s = later_seasons(rows)
    if len(models) < 2 or later_s is None:
        return None
    settled = [r for r in rows if r["realised"] is not None]
    cov = coverage_by_period(rows, models)
    rows = [{**r, "vectors": {m: tuple(r["vectors"][m]) for m in models}, "w": {}} for r in settled
            if all(m in r["vectors"] for m in models)]
    for r in rows:
        for m in models:
            r["w"][m] = _wins(market, r["vectors"][m], r["realised"])
    early = [r for r in rows if r["season"] not in later_s]
    if not early:
        return None
    ll = {m: _log_loss(early, m) for m in models}
    return {"market": market, "rows": rows, "early": early, "later": [r for r in rows if r["season"] in later_s],
            "ll": ll, "ranked": sorted(models, key=lambda m: (ll[m], models.index(m))), "coverage": cov,
            "later_seasons": list(later_s)}


def _period(rs):
    return {"seasons": sorted({r["season"] for r in rs}), "games": len(rs),
            "date_from": rs[0]["tipoff_at"][:10] if rs else None, "date_to": rs[-1]["tipoff_at"][:10] if rs else None}


def stored_core(ctx: dict) -> dict:
    """Everything except the one-model additions: individual models, pairs, agreement, and the later-period check."""
    market, early, later, ll, ranked = ctx["market"], ctx["early"], ctx["later"], ctx["ll"], ctx["ranked"]
    best = ranked[0]
    # 1. Individual models on the earlier period, ranked by log loss.
    singles = [{"model": m, "rank": i + 1, "metrics": _metrics(market, early, m),
                "log_loss_vs_best": None if m == best else _paired(early, m, best, "log_loss")} for i, m in enumerate(ranked)]
    # 2. Every pair's equal-weight average against its stronger member, on the same earlier games.
    pairs = []
    for a, b in combinations(ranked, 2):
        k = _ensure(early, market, [a, b])
        pairs.append(_pair_detail(market, early, a, b, a if ll[a] <= ll[b] else b) | {"_ll": _log_loss(early, k)})
    pairs.sort(key=lambda p: (p["_ll"], p["members"]))
    for p in pairs[:PAIR_CI_TOP]:
        p["log_loss_vs_strongest"] = _paired(early, _key(p["members"]), p["strongest_member"], "log_loss")
    for p in pairs:
        p.pop("_ll")
    best_pair = pairs[0]["members"]
    # 3. Agreement count vs confidence over all compared models.
    agree = _agreement_table(market, early, list(ranked))
    # 4. Choose on the earlier period, freeze, evaluate on the later period (no retuning).
    rest = [m for m in ranked if m not in best_pair]
    third = min(rest, key=lambda m: (_log_loss(early, _ensure(early, market, best_pair + [m])), ranked.index(m))) if rest else None
    # With exactly two models the best pair is every model: there is no third member to add (shown once, not repeated).
    frozen = [("best single", [best]), ("best pair", best_pair)] + (
        [("best pair + one", best_pair + [third]), ("all models (fixed, not chosen)", list(ranked))] if third else [])
    checks, decided_later = [], 0
    for name, members in frozen:
        ke, kl = _ensure(early, market, members), _ensure(later, market, members)
        m_later = _metrics(market, later, kl) if later else None
        decided_later = max(decided_later, (m_later or {}).get("wins", 0) + (m_later or {}).get("losses", 0))
        vs = members != [best] and later
        checks.append({"choice": name, "members": members, "earlier": _metrics(market, early, ke), "later": m_later,
                       "later_log_loss_vs_best_single": _paired(later, kl, best, "log_loss") if vs else None,
                       "later_accuracy_vs_best_single": _paired(later, kl, best, "accuracy") if vs else None})
    return {"status": "available", "models": list(ranked), "games": len(ctx["rows"]), "default_base": best_pair,
            "periods": {"all": _period(ctx["rows"]), "earlier": _period(early), "later": _period(later)},
            "coverage": ctx["coverage"], "coverage_definition": COVERAGE_DEFINITION,
            "search": {"singles": len(ranked), "pairs": len(pairs), "pairs_with_interval": min(PAIR_CI_TOP, len(pairs)),
                       "additions_per_base": len(ranked) - len(best_pair), "frozen_choices": len(frozen),
                       "rule": "equal-weight probability average; vote agreement reported separately; no subset search "
                               "beyond pairs and one-model additions"},
            "singles": singles, "pairs": pairs, "agreement": agree,
            "holdout": {"status": "ok" if decided_later >= MIN_LATER else "insufficient_evidence", "min_decided": MIN_LATER,
                        "chosen_on": "earlier period, by log loss", "checks": checks}}


def stored_add(ctx: dict, base: Optional[list] = None) -> dict:
    """A+B (base, default the best earlier pair) against A+B+C for each other model C, on the same earlier games."""
    models = ctx["ranked"]
    if base:
        if any(m not in models for m in base) or len(set(base)) != len(base) or not 1 <= len(base) < len(models):
            raise InvalidRequest(f"base must be 1 to {len(models) - 1} distinct models from: {', '.join(models)}")
    else:
        base = min(([a, b] for a, b in combinations(models, 2)),
                   key=lambda p: (_log_loss(ctx["early"], _ensure(ctx["early"], ctx["market"], p)), p))
    return _add_model(ctx["market"], ctx["early"], list(base), [m for m in models if m not in base])


def analyse_stored(market: str, rows: list, models: list, base: Optional[list] = None) -> dict:
    """Pure. rows: saved out-of-sample rows (game_id, season, cutoff_at, tipoff_at, realised, vectors); models: the members
    compared. Earlier period = all but the last LATER_SEASONS seasons; the later period only evaluates frozen choices."""
    ctx = prepare_stored(market, rows, models)
    if ctx is None:
        return {"status": "insufficient_evidence", "reason": "fewer than two models or too few seasons for an earlier/later split",
                "models": models}
    return {**stored_core(ctx), "add_model": stored_add(ctx, base)}


def earlier_roster(rows: list, complete: bool) -> tuple[list, list]:
    """(models, excluded) decided on the earlier period only: which models exist there (in order of first appearance) and,
    if `complete`, whether each was saved for every earlier game. Later forecasts and results never change the roster;
    a model first seen in the later period is listed as excluded. Missing later forecasts are reported as coverage."""
    later_s = later_seasons(rows)
    if later_s is None:            # no earlier period: nothing may be chosen, so no model is compared
        return [], [{"model": m, "reason": f"no earlier period (needs more than {LATER_SEASONS} seasons)"}
                    for m in dict.fromkeys(k for r in rows for k in r["vectors"])]
    early = [r for r in rows if r["season"] not in later_s]
    present = list(dict.fromkeys(k for r in early for k in r["vectors"]))
    models = [m for m in present if not complete or all(m in r["vectors"] for r in early)]
    excluded = [{"model": m, "reason": "not saved for every earlier-period game"} for m in present if m not in models]
    excluded += [{"model": m, "reason": "no forecast in the earlier period (saved only in the later period)"}
                 for m in dict.fromkeys(k for r in rows for k in r["vectors"]) if m not in present]
    return models, excluded


def _table(sport: str, market: str) -> tuple[list, list, list]:
    """(rows, compared models, excluded models with reasons). The compared models are chosen on the earlier period only."""
    from . import agreement_tracking
    table = agreement_tracking._oof(sport).get(market, [])
    complete, excluded = earlier_roster(table, complete=True)
    excluded = [{"model": m, "reason": "used the actual starting goalie, known only after the start (postgame input)"}
                for m in complete if POSTGAME_INPUT in m] + excluded
    return table, [m for m in complete if POSTGAME_INPUT not in m], excluded


@lru_cache(maxsize=12)
def _ctx(sport: str, market: str, stamp: tuple) -> tuple:
    table, models, excluded = _table(sport, market)
    meta = {"excluded": excluded, "stored_games": len(table),
            "excluded_games": {"no_result": sum(r["realised"] is None for r in table)},
            "stages": sorted({r.get("stage") for r in table if r.get("stage")}), "split": split(table)}
    state = evidence_state(table, models, 2)
    ctx = None if state else prepare_stored(market, table, models)
    if ctx is None and state is None:
        state = ("insufficient_evidence", "no settled earlier game has every compared model")
    return ctx, meta, state, models


@lru_cache(maxsize=12)
def _core(sport: str, market: str, stamp: tuple) -> dict:
    return stored_core(_ctx(sport, market, stamp)[0])


@lru_cache(maxsize=64)
def _add(sport: str, market: str, base: tuple, stamp: tuple) -> dict:
    return stored_add(_ctx(sport, market, stamp)[0], list(base) or None)


def stored(sport: str, market: str, base: Optional[list] = None) -> dict:
    """Combination analysis of the saved out-of-sample forecasts for one sport and market (backend-calculated)."""
    src = _source_identity(sport)
    stamp = tuple(s["sha256"] for s in src)
    ctx, meta, state, models = _ctx(sport, market, stamp)
    head = {"sport": sport, "market": market, "kind": "historical out-of-sample (exploratory)", "sources": src, **meta,
            "note": STORED_NOTE + ("" if market in ("winner", "reg3") else " " + LINE_NOTE),
            # from the season split, not from the later games evaluated (those can all be excluded)
            "later_reserved_test_seasons": sorted(set((meta["split"] or {}).get("later_seasons", [])) & _reserved(sport))}
    if state:
        return {**head, "status": state[0], "reason": state[1], "models": models}
    core = _core(sport, market, stamp)
    default = core["default_base"]
    if len(default) >= len(core["models"]):     # two models: the best pair is every model; add the other to the best single
        default = core["models"][:1]
    return {**head, **core, "add_model": _add(sport, market, tuple(base or default), stamp)}


def _reserved(sport: str) -> set:
    from . import agreement_tracking
    return agreement_tracking.reserved_seasons(sport)
