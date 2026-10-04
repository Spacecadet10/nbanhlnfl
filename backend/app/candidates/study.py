"""Two-stage candidate-pool study (docs/CANDIDATE_POOL.md §5–§6). Exploratory on spent seasons; stage 2 assesses a frozen
shortlist once on later seasons that were not used to choose it.

Stage `screen` loads only development seasons, scores every registry candidate per market, measures how its errors differ
from the existing members, and freezes a shortlist and its combination rules to `shortlist.json` by the declared rule.
Stage `assess` refuses to run without that file or twice; it logs its access and evaluates exactly the frozen rules.
"""
import json
import math
import time
from pathlib import Path
from typing import Callable, Optional

from .. import backtest, combination_research as cr, combine, config, nhl_eval, selector
from ..db import utc_now
from . import deep, engine, features, registry

STAGES = {"nba": {"screen": ("validation", (2022, 2023, 2024)), "assess": ("test", (2025, 2026))},
          "nhl": {"screen": ("development", (2018, 2019, 2020, 2021)), "assess": ("test", (2022, 2023, 2024, 2025, 2026))}}
LINE_MARKETS = {"nba": ("spread", "total", "team_total_home", "team_total_away"),
                "nhl": ("reg3", "spread", "total", "team_total_home", "team_total_away")}
THRESHOLDS = {"default": (0.55, 0.60, 0.65, 0.70), "reg3": (0.45, 0.50, 0.55)}
MAX_SHORTLIST = 3            # candidates per sport·market
SHORTLIST_STANDALONE = 0.002 # log loss within this of the strongest existing member, or …
# … a negative pair-average gain (avg(strongest, candidate) − strongest) on screening seasons.
TARGET_ACCURACY = (0.80, 0.85)


def out_dir(sport: str) -> Path:
    return config.data_dir() / "reports" / f"pool-{sport}"


def experiment_log() -> Path:
    return config.data_dir() / "research" / "candidates" / "experiments.jsonl"


def write_oof(sport: str, stage: str, tables: dict, seasons) -> str:
    """Out-of-fold member and candidate vectors in the study row format (one file per stage), for combination research."""
    import gzip
    p = config.data_dir() / "research" / "candidates" / f"oof-{sport}-{stage}.json.gz"
    p.parent.mkdir(parents=True, exist_ok=True)
    keep = {m: [{k: r[k] for k in ("game_id", "season", "cutoff_at", "tipoff_at", "result_available_at", "line", "realised")}
                | {"vectors": {n: list(v) for n, v in r["vectors"].items()
                               if v is not None and not n.startswith(("avg:", "stack"))}}
                for r in t if r["season"] in seasons] for m, t in tables.items()}
    with gzip.open(p, "wt") as fh:
        json.dump({"sport": sport, "stage": stage, "seasons": list(seasons), "tables": keep}, fh)
    return str(p)


def compact(report: dict) -> dict:
    """The committed evidence summary (docs/evidence/candidate-pool/) read by the roster: no per-game rows."""
    out = {k: report.get(k) for k in ("stage", "sport", "started_at", "dataset_version", "seasons", "libraries", "definitions",
                                      "runtime_s", "feature_table", "goalie_source")}
    out["markets"] = {}
    for m, v in report["markets"].items():
        if not v.get("games"):
            out["markets"][m] = {"games": 0}
            continue
        cands = {}
        for c, d in (v.get("candidates") or {}).items():
            cands[c] = {"log_loss": d["summary"]["log_loss"], "brier": d["summary"]["brier"],
                        "accuracy": d["summary"]["accuracy"], "per_season": d["per_season"],
                        "minus_strongest": {k: d["minus_strongest"][k] for k in ("mean", "ci95")}}
            if "pair_average_minus_strongest" in d:
                cands[c]["pair_gain"] = {k: d["pair_average_minus_strongest"][k] for k in ("mean", "ci95")}
                swm = d.get("stack_with_minus_without")
                cands[c]["stack_gain"] = {k: swm[k] for k in ("mean", "ci95")} if swm else None
                cands[c]["error_correlation"] = d["error_correlation"]
                cands[c]["disagreement_with_strongest"] = d["disagreement_with_strongest"]
            else:
                cands[c]["error_correlation_with_strongest"] = d["error_correlation_with_strongest"]
                cands[c]["disagreement_with_strongest"] = d["disagreement_with_strongest"]
                cands[c]["curve"] = d["curve"]
        mm = {"games": v["games"], "seasons": v["seasons"], "strongest_existing": v["strongest_existing"],
              "existing": {e: {k: x[k] for k in ("log_loss", "brier", "accuracy")} for e, x in v["existing"].items()},
              "candidates": cands}
        for k in ("shortlist", "triple_partner", "combinations", "rules", "selector", "strongest_curve"):
            if k in v:
                mm[k] = v[k]
        out["markets"][m] = mm
    return out


def _write_evidence(report: dict) -> str:
    from .live import EVIDENCE_DIR
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    p = EVIDENCE_DIR / f"{report['sport']}-{report['stage']}.json"
    p.write_text(json.dumps(compact(report), indent=1, default=str) + "\n")
    return str(p)


def not_run_entries(sport: str, market: str, not_run: dict, base: dict) -> list:
    """Experiment-log rows for every registry candidate of `market` that was skipped or failed, with its reason, so the
    log lists every configuration of the declared search, not only those that produced a result (audit P7)."""
    return [{**base, "sport": sport, "market": market, "candidate": s.name, "definition": registry.definition(s),
             "status": not_run[s.name]["status"], "reason": not_run[s.name]["reason"]}
            for s in registry.for_sport(sport) if market in s.markets and s.name in not_run]


def _log(entry: dict) -> None:
    p = experiment_log()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as fh:
        fh.write(json.dumps(entry, sort_keys=True, default=str) + "\n")


# --- Tables ---------------------------------------------------------------------------------------------------------
def existing_tables(conn, sport: str, period: str):
    """§5 study tables of the existing members (identical lines, outcomes, cutoffs) and the rows/events used."""
    if sport == "nba":
        from .. import nba_markets
        from ..models.efficiency import EfficiencyMargin
        from ..models.elo import Elo
        from ..models.gbt import GradientBoostedTrees
        from ..models.logistic import LogisticElo, LogisticV2
        from ..models.score_candidates import GbtPoints, RidgePoints
        from ..retrain_cadence import Cadenced
        _, cfg = backtest.load_frozen_decision(conn)
        rows, events = backtest.load_rows(conn, period), backtest.load_box_events(conn, period)
        points, _ = nba_markets.sealed_model(conn)
        dist = [points, RidgePoints(cfg), GbtPoints(cfg), Cadenced(RidgePoints(cfg), "weekly"), Cadenced(GbtPoints(cfg), "weekly")]
        winner = [Elo(cfg), LogisticElo(cfg), LogisticV2(cfg), EfficiencyMargin(), GradientBoostedTrees(cfg)]
        tables, first = cr._tables(rows, winner, dist, LINE_MARKETS["nba"], events)
        for m in dist[3:]:
            first[m.name] = m.member.oos_from_season()
        first["elo"] = first["points_normal"] = min(backtest.PERIODS["warmup"])
        recorder = features.NbaRecorder(cfg)
        return tables, first, {"winner": [m.name for m in winner], "lines": [m.name for m in dist]}, rows, events, recorder, \
            backtest.dataset_version(rows)
    from .. import nhl_shots, sports
    from ..models.hockey import GoalsConfig, GoalsPoisson, HockeyElo, HockeyEloConfig, hockey_gbt, hockey_logistic
    from ..models.hockey_xg import hockey_logistic_xg
    from ..models.score_candidates import GoalsDraw, GoalsGlm
    from . import goalies
    d = nhl_eval.load_decision(conn)
    rows = nhl_eval.load_rows(conn, period)
    events = nhl_shots.load_events(conn, rows)
    ec, gc, key = HockeyEloConfig(**d["hockey_elo_config"]), GoalsConfig(**d["goals_config"]), sports.NHL.rating_key
    winner = [HockeyElo(ec, key), GoalsPoisson(gc, key), hockey_logistic(ec, gc, key), hockey_gbt(ec, gc, key)]
    dist = [GoalsPoisson(gc, key), GoalsDraw(gc, key), GoalsGlm(ec, key)]
    tables, first = cr._tables(rows, winner, dist, LINE_MARKETS["nhl"], None, lambda r: r.decision)
    xg = hockey_logistic_xg(ec, gc, key)      # shots reach only the xG member (as expanded_search)
    pxg = {p["game_id"]: p["p_home"] for p in backtest.run(xg, rows, events)}
    for r in tables["winner"]:
        r["vectors"][xg.name] = (pxg[r["game_id"]], 0.0, 1 - pxg[r["game_id"]])
    first[xg.name] = xg.oos_from_season()
    winner.append(xg)
    first["hockey_elo"] = first["goals_poisson"] = min(nhl_eval.PERIODS["warmup"])
    detail = goalies.load(conn)
    starters = goalies.actual_starters(conn, detail)
    recorder = features.NhlRecorder(ec, gc, key, detail,
                                    starters=lambda g: (starters.get(g.game_id, {}), {"goalie_source": "actual_starter"}))
    return tables, first, {"winner": [m.name for m in winner], "lines": [m.name for m in dist]}, rows, events, recorder, \
        nhl_eval.dataset_version(rows)


def add_candidates(sport, tables, first, rows, events, recorder, log) -> tuple[dict, dict]:
    """Runs the feature walk once and every available registry candidate on it; adds their vectors to the tables."""
    t0 = time.time()
    walk = features.walk(recorder, rows, events)
    log(f"{sport}: feature walk {len(walk)} games in {time.time() - t0:.1f} s (table {features.table_version(walk)})")
    by_table = {m: {r["game_id"]: r for r in t} for m, t in tables.items()}
    fits, runtimes, not_run = {}, {}, {}
    for spec in registry.for_sport(sport):
        if deep.is_deep(spec):               # screened by its own registered study, not by the pool screen
            not_run[spec.name] = {"status": "skipped", "reason": "deep candidate: evaluated in deep-v1 (MODEL_SELECTION §22)"}
            continue
        if not registry.available(spec):
            not_run[spec.name] = {"status": "skipped", "reason": f"optional dependency {spec.extra} not installed"}
            log(f"{spec.name}: skipped (optional dependency {spec.extra} not installed)")
            continue
        t0 = time.time()
        try:
            out, fit_records, _ = engine.walk(spec, list(walk.values()))
        except Exception as exc:             # logged as a failed configuration; the other candidates still run
            not_run[spec.name] = {"status": "failed", "reason": f"{type(exc).__name__}: {exc}"}
            log(f"{spec.name}: FAILED ({type(exc).__name__}: {exc})")
            continue
        runtimes[spec.name] = round(time.time() - t0, 1)
        fits[spec.name] = fit_records
        seasons = sorted({walk[g].season for g in out})
        first[spec.name] = seasons[0] if seasons else None
        for market in spec.markets:
            for gid, o in out.items():
                row = by_table[market].get(gid)
                if row is not None:
                    row["vectors"][spec.name] = engine.vector(o, market, row["line"])
        log(f"{spec.name}: {len(out)} games, {len(fit_records)} fits, {runtimes[spec.name]} s")
    return {"fits": fits, "runtime_s": runtimes, "not_run": not_run, "feature_table": features.table_version(walk),
            "goalie_source": "actual starter (retrospective; assumes the pregame listing was right)" if sport == "nhl" else None}, walk


# --- Metrics --------------------------------------------------------------------------------------------------------
def _ll(rows, key):
    return sum(combine.log_loss(r["vectors"][key], r["realised"]) for r in rows) / len(rows)


def _diff(rows, a, b):
    d = [combine.log_loss(r["vectors"][a], r["realised"]) - combine.log_loss(r["vectors"][b], r["realised"]) for r in rows]
    return backtest.block_bootstrap(d, [r["cutoff_at"] for r in rows])


def _resid_corr(rows, a, b):
    """Correlation of the two members' errors on side a (probability minus outcome)."""
    xs = [r["vectors"][a][0] - (r["realised"] == 0) for r in rows]
    ys = [r["vectors"][b][0] - (r["realised"] == 0) for r in rows]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return sxy / (sx * sy) if sx and sy else None


def _disagree(rows, a, b, market):
    """Share of games where the two pick different sides, and each one's accuracy on those games."""
    dis = [r for r in rows if combine.pick(r["vectors"][a], market) != combine.pick(r["vectors"][b], market)]
    out = {"share": len(dis) / len(rows) if rows else None, "games": len(dis)}
    for k in (a, b):
        res = [cr._result(market, r["vectors"][k], r["realised"]) for r in dis]
        w, l = res.count("win"), res.count("loss")
        out[f"{k}_accuracy"] = w / (w + l) if w + l else None
    return out


def _avg_key(ms):
    return "avg:" + "+".join(ms)


def _add_avg(rows, ms):
    k = _avg_key(ms)
    for r in rows:
        if all(m in r["vectors"] for m in ms):
            r["vectors"][k] = combine.average([r["vectors"][m] for m in ms])
    return k


def _complete(table, names, seasons):
    return [r for r in table if r["season"] in seasons and r["realised"] is not None and all(n in r["vectors"] for n in names)]


# --- Stage 1: screening ---------------------------------------------------------------------------------------------
def screen_market(market, table, existing, candidates, first, seasons) -> dict:
    names = existing + candidates
    rows = _complete(table, names, seasons)
    if not rows:
        return {"games": 0}
    strongest = min(existing, key=lambda m: (_ll(rows, m), existing.index(m)))
    out = {"games": len(rows), "seasons": list(seasons), "strongest_existing": strongest,
           "existing": {m: cr._summary(market, rows, m) for m in existing}, "candidates": {}}
    for c in candidates:
        pair = _add_avg(rows, [strongest, c])
        out["candidates"][c] = {
            "summary": cr._summary(market, rows, c),
            "per_season": {str(s): _ll([r for r in rows if r["season"] == s], c) for s in seasons},
            "minus_strongest": _diff(rows, c, strongest),
            "pair_average_minus_strongest": _diff(rows, pair, strongest),
            "error_correlation": {m: _resid_corr(rows, c, m) for m in existing},
            "disagreement_with_strongest": _disagree(rows, c, strongest, market),
        }
    # Stacking ablation: existing members with and without each candidate (out-of-fold, season-start refits).
    stack_rows = [dict(r, vectors=dict(r["vectors"])) for r in rows]
    base = cr._stack_walk(market, stack_rows, existing, first, list(seasons))
    for r in stack_rows:
        if "stack" in r["vectors"]:
            r["vectors"]["stack:existing"] = r["vectors"].pop("stack")
    for c in candidates:
        cr._stack_walk(market, stack_rows, existing + [c], first, list(seasons))
        have = [r for r in stack_rows if "stack" in r["vectors"] and "stack:existing" in r["vectors"]]
        for r in have:
            r["vectors"][f"stack:+{c}"] = r["vectors"].pop("stack")
        out["candidates"][c]["stack_with_minus_without"] = _diff(have, f"stack:+{c}", "stack:existing") if have else None
    out["stack_existing_fits"] = base
    # Shortlist (declared rule; module constants).
    ranked = []
    for c, v in out["candidates"].items():
        standalone = v["minus_strongest"]["mean"]
        pair = v["pair_average_minus_strongest"]["mean"]
        if standalone <= SHORTLIST_STANDALONE or pair < 0:
            ranked.append((pair, standalone, c))
    ranked.sort()
    short = [c for _, _, c in ranked[:MAX_SHORTLIST]]
    partners = {}
    for c in short:
        corr = out["candidates"][c]["error_correlation"]
        others = [m for m in existing if m != strongest and corr.get(m) is not None]
        partners[c] = min(others, key=lambda m: (corr[m], m)) if others else None
    out["shortlist"] = short
    out["triple_partner"] = partners
    return out


DRY_SEASONS = {"nba": (2021,), "nhl": (2017,)}   # warm-up seasons: exercise the code without reading any scored season


def run_screen(conn, sport: str, log: Callable[[str], None] = print, dry: bool = False) -> dict:
    period, seasons = STAGES[sport]["screen"]
    if dry:
        seasons = DRY_SEASONS[sport]
    started = utc_now()
    t0 = time.time()
    tables, first, members, rows, events, recorder, dataset = existing_tables(conn, sport, period)
    if {r.game.season for r in rows} & set(STAGES[sport]["assess"][1]):
        raise ValueError("screening loaded assessment seasons")
    log(f"{sport}: existing members done in {time.time() - t0:.0f} s")
    cand, _ = add_candidates(sport, tables, first, rows, events, recorder, log)
    report = {"stage": "screen", "sport": sport, "started_at": started, "dataset_version": dataset, "seasons": list(seasons),
              "libraries": registry.libraries(), "definitions": {s.name: registry.definition(s) for s in registry.for_sport(sport)},
              "existing_members": members, "first_oos_season": first, **cand, "markets": {}}
    shortlist = {"sport": sport, "frozen_at": None, "screen_dataset_version": dataset, "markets": {}}
    for market in ("winner",) + LINE_MARKETS[sport]:
        existing = members["winner"] if market == "winner" else members["lines"]
        cands = [s.name for s in registry.for_sport(sport) if market in s.markets and s.role == "candidate"
                 and registry.available(s) and not deep.is_deep(s)]
        refs = [s.name for s in registry.for_sport(sport) if market in s.markets and s.role == "reference"
                and registry.available(s)]
        res = screen_market(market, tables[market], existing, cands + refs, first, seasons)
        if res.get("games"):
            res["shortlist"] = [c for c in res["shortlist"] if c in cands]      # references are never shortlisted
        report["markets"][market] = res
        if res.get("games"):
            shortlist["markets"][market] = {"strongest_existing": res["strongest_existing"], "candidates": res["shortlist"],
                                            "triple_partner": {c: res["triple_partner"][c] for c in res["shortlist"]},
                                            "existing": existing}
            log(f"{sport} {market}: {res['games']} games; strongest {res['strongest_existing']}; shortlist {res['shortlist']}")
        for c in cands + refs:
            v = res.get("candidates", {}).get(c)
            if v and not dry:
                _log({"at": started, "stage": "screen", "sport": sport, "market": market, "candidate": c, "status": "evaluated",
                      "definition": report["definitions"][c], "dataset_version": dataset, "seasons": list(seasons),
                      "log_loss": v["summary"]["log_loss"], "minus_strongest": v["minus_strongest"]["mean"],
                      "pair_gain": v["pair_average_minus_strongest"]["mean"],
                      "stack_gain": (v["stack_with_minus_without"] or {}).get("mean"),
                      "runtime_s": cand["runtime_s"].get(c), "fits": cand["fits"].get(c)})
        if not dry:
            for e in not_run_entries(sport, market, cand["not_run"], {"at": started, "stage": "screen",
                                                                      "dataset_version": dataset, "seasons": list(seasons)}):
                _log(e)
    shortlist["frozen_at"] = utc_now()
    report["runtime_s"] = round(time.time() - t0, 1)
    if dry:
        return report
    report["oof"] = write_oof(sport, "screen", tables, seasons)
    report["evidence"] = _write_evidence(report)
    d = out_dir(sport)
    d.mkdir(parents=True, exist_ok=True)
    (d / "screen.json").write_text(json.dumps(report, indent=1, default=str))
    (d / "shortlist.json").write_text(json.dumps(shortlist, indent=1))
    return report


# --- Stage 2: assessment --------------------------------------------------------------------------------------------
def _rule_flags(market, rows, side_of):
    return cr._flags(market, rows, None, side_of)


def _rule(market, rows, label, side_of, strongest, avg_key=None):
    """Picks, accuracy (Wilson), coverage; matched-coverage comparison with the strongest member's (and the average's)
    most confident games in the same rows."""
    sel = [r for r in rows if side_of(r) is not None]
    flags = _rule_flags(market, sel, side_of)
    w = sum(f["win"] for f in flags)
    out = {"rule": label, "picks": len(sel), "decided": len(flags), "coverage": len(sel) / len(rows) if rows else None,
           "accuracy": w / len(flags) if flags else None, "accuracy_ci95": nhl_eval._wilson(w, len(flags))}
    for name, ref in (("strongest", strongest), ("average", avg_key)):
        if ref is None or not flags:
            continue
        top = cr._flags(market, cr._top(market, rows, ref, len(sel)), ref)
        out[f"vs_{name}_same_count"] = {"model": ref, "accuracy": sum(f["win"] for f in top) / len(top) if top else None,
                                        "diff": nhl_eval._subset_diff_bootstrap(flags, top, "win", "win") if top else None}
    out["per_season"] = {}
    for s in sorted({r["season"] for r in rows}):
        fs = _rule_flags(market, [r for r in sel if r["season"] == s], side_of)
        out["per_season"][str(s)] = {"decided": len(fs), "accuracy": sum(f["win"] for f in fs) / len(fs) if fs else None}
    return out


def _threshold_side(key, t, market):
    def side_of(r):
        v = r["vectors"].get(key)
        side = None if v is None else combine.pick(v, market)
        return side if side is not None and v[side] + 1e-9 >= t else None
    return side_of


def _agree_side(a, b, key, t, market):
    def side_of(r):
        sa, sb = combine.pick(r["vectors"][a], market), combine.pick(r["vectors"][b], market)
        v = r["vectors"][key]
        return sa if sa is not None and sa == sb and v[sa] + 1e-9 >= t else None
    return side_of


def _accuracy_curve(market, rows, key):
    """Accuracy of the member's most confident games at fixed coverages, and the largest coverage reaching each target."""
    ranked = cr._top(market, rows, key, len(rows))
    flags = cr._flags(market, ranked, key)
    out, wins = {}, 0
    reach = {t: None for t in TARGET_ACCURACY}
    for i, f in enumerate(flags, 1):
        wins += f["win"]
        if i >= 30:
            for t in TARGET_ACCURACY:
                if wins / i >= t:
                    reach[t] = {"games": i, "coverage": i / len(rows), "accuracy": wins / i,
                                "ci95": nhl_eval._wilson(wins, i)}
    for cov in (0.05, 0.10, 0.20, 0.30, 0.50, 1.0):
        n = max(1, int(round(cov * len(flags))))
        w = sum(f["win"] for f in flags[:n])
        out[str(cov)] = {"games": n, "accuracy": w / n, "ci95": nhl_eval._wilson(w, n)}
    return {"by_coverage": out, "largest_coverage_reaching": {str(k): v for k, v in reach.items()}}


def assess_market(market, table, plan, first, seasons, screen_seasons) -> dict:
    existing, strongest, short = plan["existing"], plan["strongest_existing"], plan["candidates"]
    names = existing + short
    rows = _complete(table, names, seasons)
    if not rows:
        return {"games": 0}
    ts = THRESHOLDS["reg3" if market == "reg3" else "default"]
    out = {"games": len(rows), "seasons": list(seasons), "strongest_existing": strongest, "existing": {}, "candidates": {},
           "combinations": [], "rules": []}
    for m in existing:
        out["existing"][m] = cr._summary(market, rows, m)
    out["strongest_curve"] = _accuracy_curve(market, rows, strongest)
    # Stacks: existing only vs existing + all shortlisted (out-of-fold; trained on earlier seasons incl. screening ones).
    hist = _complete(table, names, tuple(screen_seasons) + tuple(seasons))
    for r in hist:
        r["vectors"] = dict(r["vectors"])
    cr._stack_walk(market, hist, existing, first, list(seasons))
    for r in hist:
        if "stack" in r["vectors"]:
            r["vectors"]["stack:existing"] = r["vectors"].pop("stack")
    if short:
        cr._stack_walk(market, hist, existing + short, first, list(seasons))
        for r in hist:
            if "stack" in r["vectors"]:
                r["vectors"]["stack:pool"] = r["vectors"].pop("stack")
    hv = {r["game_id"]: r["vectors"] for r in hist}
    for r in rows:
        for k in ("stack:existing", "stack:pool"):
            if k in hv.get(r["game_id"], {}):
                r["vectors"][k] = hv[r["game_id"]][k]
    stacked = [r for r in rows if "stack:existing" in r["vectors"] and "stack:pool" in r["vectors"]]
    if stacked:
        out["combinations"].append({"combination": "stack:existing+shortlist vs stack:existing (ablation)",
                                    "games": len(stacked), "log_loss_with": _ll(stacked, "stack:pool"),
                                    "log_loss_without": _ll(stacked, "stack:existing"),
                                    "with_minus_without": _diff(stacked, "stack:pool", "stack:existing"),
                                    "with_minus_strongest": _diff(stacked, "stack:pool", strongest)})
    for c in short:
        out["candidates"][c] = {"summary": cr._summary(market, rows, c),
                                "per_season": {str(s): _ll([r for r in rows if r["season"] == s], c) for s in seasons},
                                "minus_strongest": _diff(rows, c, strongest),
                                "error_correlation_with_strongest": _resid_corr(rows, c, strongest),
                                "disagreement_with_strongest": _disagree(rows, c, strongest, market),
                                "curve": _accuracy_curve(market, rows, c)}
        pair = _add_avg(rows, [strongest, c])
        out["combinations"].append({"combination": f"{pair} vs {strongest} (ablation: remove {c})", "games": len(rows),
                                    "log_loss_with": _ll(rows, pair), "log_loss_without": _ll(rows, strongest),
                                    "with_minus_without": _diff(rows, pair, strongest),
                                    "per_season": {str(s): {"with": _ll([r for r in rows if r["season"] == s], pair),
                                                            "without": _ll([r for r in rows if r["season"] == s], strongest)}
                                                   for s in seasons}})
        partner = plan["triple_partner"].get(c)
        if partner:
            tri, duo = _add_avg(rows, [strongest, partner, c]), _add_avg(rows, [strongest, partner])
            out["combinations"].append({"combination": f"{tri} vs {duo} (ablation: remove {c})", "games": len(rows),
                                        "log_loss_with": _ll(rows, tri), "log_loss_without": _ll(rows, duo),
                                        "with_minus_without": _diff(rows, tri, duo),
                                        "with_minus_strongest": _diff(rows, tri, strongest)})
        for t in ts:
            out["rules"].append(_rule(market, rows, f"{c} ≥ {t}", _threshold_side(c, t, market), strongest))
            out["rules"].append(_rule(market, rows, f"{pair} ≥ {t}", _threshold_side(pair, t, market), strongest, pair))
            out["rules"].append(_rule(market, rows, f"agree({strongest}, {c}) & average ≥ {t}",
                                      _agree_side(strongest, c, pair, t, market), strongest, pair))
            out["rules"].append(_rule(market, rows, f"{strongest} ≥ {t} (without {c})",
                                      _threshold_side(strongest, t, market), strongest))
    for k in ("stack:existing", "stack:pool"):
        if stacked and k in stacked[0]["vectors"]:
            for t in ts:
                out["rules"].append(_rule(market, stacked, f"{k} ≥ {t}", _threshold_side(k, t, market), strongest))
    # Selector over the pool (weekly, evidence only from earlier settled games), counted on assessment seasons only.
    sources = [strongest] + short + [_avg_key([strongest, c]) for c in short] + (["stack:pool"] if stacked else [])
    # Every game where the strongest member forecast is a selector item; a candidate, average or stack missing on a game is
    # "source unavailable" there, so single members are never dropped because a combination could not be formed.
    sim_rows = [r for r in hist if strongest in r["vectors"]]
    for r in sim_rows:
        for c in short:
            if c in r["vectors"]:
                _add_avg([r], [strongest, c])
    if sim_rows:
        sim, picks = cr._simulate(market, sim_rows, sources, [strongest] + short, strongest)
        ids = {r["game_id"] for r in rows}
        mine = [p for p in picks if p["game_id"] in ids]
        dec = [p for p in mine if p["result"] != "push"]
        w = sum(p["result"] == "win" for p in dec)
        out["selector"] = {"sources": sources, "evidence_from": sim_rows[0]["tipoff_at"][:10],
                           "assessment_picks": len(mine), "assessment_coverage": len(mine) / len(rows),
                           "assessment_accuracy": w / len(dec) if dec else None,
                           "assessment_accuracy_ci95": nhl_eval._wilson(w, len(dec)),
                           "summary_all_weeks": sim["summary"],
                           "rules_used": sim["summary"]["rules_used"]}
    return out


def run_assess(conn, sport: str, log: Callable[[str], None] = print) -> dict:
    d = out_dir(sport)
    plan_path, report_path = d / "shortlist.json", d / "assess.json"
    if not plan_path.exists():
        raise ValueError("no frozen shortlist: run the screening stage first")
    if report_path.exists():
        raise ValueError(f"{report_path} exists: the assessment stage runs once")
    plan = json.loads(plan_path.read_text())
    period, seasons = STAGES[sport]["assess"]
    started = utc_now()
    access = config.data_dir() / "eval" / f"pool_assess_access_{sport}.jsonl"
    access.parent.mkdir(parents=True, exist_ok=True)
    with access.open("a") as fh:
        fh.write(json.dumps({"at": started, "stage": "pool-v1 assess", "seasons": list(seasons),
                             "shortlist_frozen_at": plan["frozen_at"]}) + "\n")
    t0 = time.time()
    tables, first, members, rows, events, recorder, dataset = existing_tables(conn, sport, period)
    cand, _ = add_candidates(sport, tables, first, rows, events, recorder, log)
    report = {"stage": "assess", "sport": sport, "started_at": started, "dataset_version": dataset, "seasons": list(seasons),
              "plan": plan, "libraries": registry.libraries(),
              "definitions": {s.name: registry.definition(s) for s in registry.for_sport(sport)},
              "first_oos_season": first, **cand, "markets": {}}
    screen_seasons = STAGES[sport]["screen"][1]
    for market, mplan in plan["markets"].items():
        res = assess_market(market, tables[market], mplan, first, seasons, screen_seasons)
        report["markets"][market] = res
        log(f"{sport} {market}: assessed {res.get('games')} games, shortlist {mplan['candidates']}")
        for c in mplan["candidates"]:
            v = res.get("candidates", {}).get(c)
            if v:
                _log({"at": started, "stage": "assess", "sport": sport, "market": market, "candidate": c, "status": "evaluated",
                      "definition": report["definitions"][c], "dataset_version": dataset, "seasons": list(seasons),
                      "log_loss": v["summary"]["log_loss"], "minus_strongest": v["minus_strongest"]["mean"]})
        for e in not_run_entries(sport, market, {c: x for c, x in cand["not_run"].items() if c in mplan["candidates"]},
                                 {"at": started, "stage": "assess", "dataset_version": dataset, "seasons": list(seasons)}):
            _log(e)
    report["runtime_s"] = round(time.time() - t0, 1)
    report["oof"] = write_oof(sport, "assess", tables, seasons)
    report["evidence"] = _write_evidence(report)
    report_path.write_text(json.dumps(report, indent=1, default=str))
    return report
