"""Market-specific selective-combination study `selective-v1` (docs/MODEL_SELECTION.md §15). Exploratory.

Point-in-time member tables are built once and saved (data/research/tables/*.json.gz, with fingerprints) so later studies
reuse them. Every registered rule is scored; selection is chronological (earlier seasons only, then frozen).
"""
import gzip
import hashlib
import json
import math
from datetime import datetime
from itertools import combinations
from pathlib import Path
from statistics import NormalDist
from typing import Callable, Optional

from . import backtest, combination_research as cr, combination_search as cs, combine, config, nhl_eval, selector
from .db import utc_now

STUDY = "selective-v1"
THRESHOLDS = {"winner": (0.60, 0.65, 0.70, 0.75, 0.80), "reg3": (0.45, 0.50, 0.55, 0.60), "line": (0.55, 0.60, 0.65)}
DISAGREE = (0.03, 0.06)
CLEAR, LOW_DISAGREEMENT = 0.60, 0.05
LEVELS = ("all", "close", "clear", "low", "high")
MIN_PICKS, MIN_COVERAGE, ALPHA = 100, 0.05, 0.05
TRIPLES = {
    ("nba", "winner"): [["elo", "logistic_v2", "gbt_v1"], ["elo", "efficiency_margin", "logistic"],
                        ["logistic_v2", "efficiency_margin", "gbt_v1"]],
    ("nba_evening", "winner"): [["elo", "logistic_v2", "avail_v1"], ["avail_v1", "efficiency_margin", "gbt_v1"],
                                ["elo", "logistic", "avail_v1"]],
    ("nba", "line"): [["points_normal", "ridge_points", "gbt_points"], ["points_normal", "ridge_points@weekly", "gbt_points@weekly"],
                      ["points_normal", "ridge_points@weekly", "gbt_points"]],
    ("nhl", "winner"): [["hockey_logistic_xg", "hockey_elo", "hockey_logistic"], ["hockey_logistic_xg", "hockey_elo", "goals_poisson"],
                        ["hockey_logistic_xg", "hockey_logistic", "hockey_gbt"]],
}


def kind_of(market: str) -> str:
    return "winner" if market == "winner" else "reg3" if market == "reg3" else "line"


# --- Saved point-in-time tables -------------------------------------------------------------------------------------
def tables_dir() -> Path:
    return config.data_dir() / "research" / "tables"


def _save(name: str, payload: dict) -> Path:
    path = tables_dir() / f"{name}.json.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(gzip.compress(json.dumps(payload, default=list).encode(), mtime=0))
    tmp.replace(path)
    return path


def _load(name: str, fingerprint: str) -> Optional[dict]:
    path = tables_dir() / f"{name}.json.gz"
    if not path.is_file():
        return None
    d = json.loads(gzip.decompress(path.read_bytes()))
    return d if d.get("fingerprint") == fingerprint else None


def _code_digest() -> str:
    root = Path(__file__).parent
    h = hashlib.sha256()
    for p in sorted((root / "models").glob("*.py")) + [root / n for n in ("backtest.py", "combination_research.py",
                                                                        "markets.py", "availability.py", "nhl_shots.py")]:
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def build_nba(conn, log) -> dict:
    from . import combination_search
    rows = backtest.load_rows(conn, "validation")
    fp = hashlib.sha256(f"nba|{backtest.dataset_version(rows)}|{_code_digest()}".encode()).hexdigest()[:24]
    cached = _load("nba", fp)
    if cached:
        log("nba table: reused")
        return cached
    tables, first, version, members, seasons = combination_search.nba_tables(conn)
    d = {"fingerprint": fp, "dataset_version": version, "tables": tables, "first": first,
         "members": {"winner": members["winner"], "line": members["lines"]}, "seasons": list(seasons), "cutoff": "night-before"}
    _save("nba", d)
    log("nba table: built")
    return json.loads(json.dumps(d, default=list))


def build_nba_evening(conn, log) -> dict:
    """Winner members at the injury report's 17:45 ET cutoff (availability.evaluate's rows), avail_v1 included."""
    import dataclasses
    from . import availability
    from .models.efficiency import EfficiencyMargin
    from .models.elo import Elo
    from .models.gbt import GradientBoostedTrees
    from .models.logistic import LogisticElo, LogisticV2
    _, cfg = backtest.load_frozen_decision(conn)
    rows = backtest.load_rows(conn, "validation")
    rows = [dataclasses.replace(r, cutoff_at=availability.evening_cutoff(r.game.tipoff_at) or r.cutoff_at) for r in rows]
    fp = hashlib.sha256(f"nba_evening|{backtest.dataset_version(rows)}|{_code_digest()}".encode()).hexdigest()[:24]
    cached = _load("nba_evening", fp)
    if cached:
        log("nba_evening table: reused")
        return cached
    box = {s: availability.load_player_box(s) for s in availability.SEASONS}
    facts, _ = availability.build_report_facts(conn, box)
    events = backtest.load_box_events(conn, "validation")
    scored = {r.game.game_id for r in rows if availability.evening_cutoff(r.game.tipoff_at)}
    models = [Elo(cfg), LogisticElo(cfg), LogisticV2(cfg), EfficiencyMargin(), GradientBoostedTrees(cfg), availability.avail_v1(cfg, facts)]
    win, first = {}, {}
    for m in models:
        win[m.name] = {p["game_id"]: p["p_home"] for p in backtest.run(m, rows, events)}
        first[m.name] = getattr(m, "oos_from_season", lambda: None)() or min(backtest.PERIODS["warmup"])
    table = [{"game_id": r.game.game_id, "season": r.game.season, "cutoff_at": r.cutoff_at, "tipoff_at": r.game.tipoff_at,
              "result_available_at": r.result_available_at, "line": None, "realised": 0 if r.home_score > r.away_score else 2,
              "vectors": {n: [v[r.game.game_id], 0.0, 1 - v[r.game.game_id]] for n, v in win.items()}}
             for r in rows if r.game.game_id in scored]
    d = {"fingerprint": fp, "dataset_version": backtest.dataset_version(rows), "tables": {"winner": table}, "first": first,
         "members": {"winner": [m.name for m in models]}, "seasons": list(backtest.PERIODS["validation"]),
         "cutoff": "17:45 ET game day (assumed report publication times)"}
    _save("nba_evening", d)
    log("nba_evening table: built")
    return d


def build_nhl(conn, log) -> dict:
    from .expanded_search import nhl_winner_table
    rows = nhl_eval.load_rows(conn, "development")
    shots = conn.execute("SELECT count(*), coalesce(max(fetch_id), 0) FROM nhl_pbp_imports").fetchone()
    fp = hashlib.sha256(f"nhl|{nhl_eval.dataset_version(rows)}|{tuple(shots)}|{_code_digest()}".encode()).hexdigest()[:24]
    cached = _load("nhl", fp)
    if cached:
        log("nhl table: reused")
        return cached
    tables, first, version, members, seasons = cr.nhl_tables(conn)
    wt, wfirst, _, wmembers, _ = nhl_winner_table(conn)
    tables["winner"] = wt
    first.update(wfirst)
    d = {"fingerprint": fp, "dataset_version": version, "tables": tables, "first": first,
         "members": {"winner": wmembers, "reg3": list(members["lines"]), "line": list(members["lines"])},
         "seasons": list(seasons), "cutoff": "night-before"}
    _save("nhl", d)
    log("nhl table: built")
    return json.loads(json.dumps(d, default=list))


# --- Rules ----------------------------------------------------------------------------------------------------------
def registry(table: str, market: str, members: list[str], with_stack: bool) -> list[dict]:
    k = kind_of(market)
    ts = THRESHOLDS[k]
    out = [{"kind": "single", "members": [m], "t": t} for m in members for t in ts]
    for pair in combinations(members, 2):
        out += [{"kind": "unanimous_min", "members": list(pair), "t": t} for t in ts]
        out += [{"kind": "avg", "members": list(pair), "t": t} for t in ts]
    triples = TRIPLES.get((table, "winner" if k == "winner" else "line"), []) if len(members) > 3 else []
    groups = [t for t in triples if all(m in members for m in t)] + ([list(members)] if len(members) >= 3 else [])
    for g in groups:
        out += [{"kind": "unanimous_min", "members": g, "t": t} for t in ts]
        out += [{"kind": "avg", "members": g, "t": t} for t in ts]
        out += [{"kind": "unanimous_maxdis", "members": g, "t": d} for d in DISAGREE]
    if len(members) >= 3:
        out.append({"kind": "majority", "members": list(members), "t": None})
    if with_stack:
        out += [{"kind": "stack", "members": list(members), "t": t} for t in ts]
    return [{**r, "level": lv, "id": rule_id(r, lv)} for r in out for lv in LEVELS]


def rule_id(r: dict, level: str) -> str:
    return f"{r['kind']}|{'+'.join(r['members'])}|{r['t']}|{level}"


def side_of(rule: dict, vectors: dict, market: str) -> Optional[int]:
    """Picked side or None (abstain). Uses member probabilities only, never the outcome."""
    kind = rule["kind"]
    if kind == "stack":
        v = vectors.get("stack")
        if v is None:
            return None
        s = combine.pick(v, market)
        return s if s is not None and v[s] + 1e-9 >= rule["t"] else None
    if any(m not in vectors for m in rule["members"]):
        return None
    vecs = [vectors[m] for m in rule["members"]]
    if kind == "single":
        s = combine.pick(vecs[0], market)
        return s if s is not None and vecs[0][s] + 1e-9 >= rule["t"] else None
    if kind == "avg":
        a = combine.average(vecs)
        s = combine.pick(a, market)
        return s if s is not None and a[s] + 1e-9 >= rule["t"] else None
    picks = [combine.pick(v, market) for v in vecs]
    if kind == "majority":
        counts = {i: picks.count(i) for i in combine.sides(market)}
        top = max(counts.values())
        leaders = [i for i, c in counts.items() if c == top]
        return leaders[0] if len(leaders) == 1 and top > len(picks) / 2 else None
    if picks[0] is None or any(p != picks[0] for p in picks):
        return None
    s = picks[0]
    ps = [v[s] for v in vecs]
    if kind == "unanimous_min":
        return s if min(ps) + 1e-9 >= rule["t"] else None
    if kind == "unanimous_maxdis":
        return s if max(ps) - min(ps) <= rule["t"] + 1e-12 else None
    raise ValueError(kind)


def disagreement(vectors: dict, members: list[str], primary: str, market: str) -> Optional[float]:
    if primary not in vectors or any(m not in vectors for m in members):
        return None
    s = combine.pick(vectors[primary], market)
    if s is None:
        return None
    ps = [vectors[m][s] for m in members]
    return max(ps) - min(ps)


def in_level(row: dict, level: str) -> bool:
    if level == "all":
        return True
    if level in ("close", "clear"):
        return (row["favourite"] >= CLEAR) == (level == "clear")
    if row["disagreement"] is None:
        return False
    return (row["disagreement"] <= LOW_DISAGREEMENT) == (level == "low")


# --- Scoring --------------------------------------------------------------------------------------------------------
def outcomes(market, rows, rule) -> list[tuple[dict, str]]:
    out = []
    for r in rows:
        if not in_level(r, rule["level"]):
            continue
        s = side_of(rule, r["vectors"], market)
        if s is not None:
            out.append((r, selector.outcome(market, s, r["realised"])))
    return out


def stats(res, eligible: int) -> dict:
    w = sum(o == "win" for _, o in res)
    l = sum(o == "loss" for _, o in res)
    p = sum(o == "push" for _, o in res)
    n = w + l
    return {"picks": len(res), "wins": w, "losses": l, "pushes": p, "voids": 0, "pending": 0,
            "coverage": len(res) / eligible if eligible else 0.0, "accuracy": w / n if n else None,
            "ci95": nhl_eval._wilson(w, n)}


def matched(curve, picks):
    return cs._matched(curve, picks)


def score_rule(market, rows, rule, curves, best) -> dict:
    eligible = sum(1 for r in rows if in_level(r, rule["level"]))
    res = outcomes(market, rows, rule)
    s = stats(res, eligible)
    s["coverage_all"] = len(res) / len(rows) if rows else 0.0     # share of every game, not only the condition's games
    lv = rule["level"]
    cmp_rate, cmp_n = matched(curves[(lv, best)], s["picks"])
    s["comparator"], s["comparator_n"] = best, cmp_n
    s["comparator_accuracy"] = cmp_rate
    if s["accuracy"] is not None and cmp_rate is not None:
        n = s["wins"] + s["losses"]
        s["lift"] = s["accuracy"] - cmp_rate
        s["se"] = math.sqrt(s["accuracy"] * (1 - s["accuracy"]) / n + cmp_rate * (1 - cmp_rate) / max(1, cmp_n)) or 1e-9
    else:
        s["lift"] = s["se"] = None
    cons = [m for m in rule["members"] if m != "stack"] if rule["kind"] != "stack" else list(rule["members"])
    s["vs_constituents"] = {}
    for m in cons:
        a = matched(curves[(lv, m)], s["picks"])[0]
        s["vs_constituents"][m] = None if s["accuracy"] is None or a is None else s["accuracy"] - a
    return s


def _curves(market, rows, members):
    return {(lv, m): cs._top_curve(market, [r for r in rows if in_level(r, lv)], m) for lv in LEVELS for m in members}


def fold(market, rows, members, rules, select, assess) -> dict:
    sel = [r for r in rows if r["season"] in select]
    ass = [r for r in rows if r["season"] == assess]
    best = cs._best_single(sel, members)
    z = NormalDist().inv_cdf(1 - ALPHA / len(rules))
    cs_sel, cs_ass = _curves(market, sel, members), _curves(market, ass, members)
    scored = []
    for rule in rules:
        s = score_rule(market, sel, rule, cs_sel, best)
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
    by_id = {r["id"]: r for r in rules}
    sel_by_id = {s["id"]: s for s in scored}
    assessed = {}
    for rid, why in frozen.items():
        a = score_rule(market, ass, by_id[rid], cs_ass, best)
        a["why"] = why
        a["selection"] = {k: sel_by_id[rid][k] for k in ("picks", "coverage", "accuracy", "lift", "lower", "comparator_accuracy")}
        assessed[rid] = a
    return {"select_seasons": list(select), "assess_season": assess, "best_single": best, "rules": len(rules), "z": z,
            "qualified": [r for r, w in frozen.items() if w == "qualified"], "assessed": assessed,
            "all_rules": [{k: s[k] for k in ("id", "picks", "wins", "losses", "pushes", "coverage", "accuracy", "lift", "lower",
                                             "comparator_accuracy")} for s in scored]}


def frontier(market, rows, members, rules) -> dict:
    """Exploratory, all evaluated seasons (hindsight): best accuracy per coverage band, against the strongest single (all-season
    log loss, also hindsight) and each constituent at matched coverage, with per-season accuracy."""
    best = cs._best_single(rows, members)
    curves = _curves(market, rows, members)
    seasons = sorted({r["season"] for r in rows})
    scored = []
    for rule in rules:
        s = score_rule(market, rows, rule, curves, best)
        if s["wins"] + s["losses"] >= MIN_PICKS:
            scored.append((rule, s))
    bands = [(0.05, 0.10), (0.10, 0.20), (0.20, 0.35), (0.35, 0.60), (0.60, 1.01)]
    out = []
    for lo, hi in bands:
        cand = [(r, s) for r, s in scored if lo <= s["coverage_all"] < hi]
        if not cand:
            out.append({"band": [lo, hi], "rule": None})
            continue
        rule, s = max(cand, key=lambda x: (x[1]["accuracy"], x[1]["picks"]))
        per = {}
        for se in seasons:
            st = stats(outcomes(market, [r for r in rows if r["season"] == se], rule),
                       sum(1 for r in rows if r["season"] == se and in_level(r, rule["level"])))
            per[str(se)] = {k: st[k] for k in ("picks", "accuracy", "coverage")}
        out.append({"band": [lo, hi], "rule": rule["id"], **{k: s[k] for k in ("picks", "wins", "losses", "pushes", "coverage", "coverage_all",
                                                                                  "accuracy", "ci95", "comparator_accuracy",
                                                                                  "vs_constituents", "lift")},
                    "comparator": best, "per_season": per})
    high = [{"rule": r["id"], "picks": s["picks"], "coverage": s["coverage"], "coverage_all": s["coverage_all"],
             "accuracy": s["accuracy"], "comparator_accuracy": s["comparator_accuracy"]} for r, s in scored if s["accuracy"] >= 0.80]
    beats = [h for h in high if h["comparator_accuracy"] is not None and h["accuracy"] - h["comparator_accuracy"] >= 0.02]
    return {"strongest_single_hindsight": best, "bands": out, "rules_with_100_picks": len(scored),
            "rules_at_or_above_80pct": sorted(high, key=lambda h: -h["picks"])[:20], "count_at_or_above_80pct": len(high),
            "count_at_or_above_80pct_beating_confidence_by_2pts": len(beats),
            "at_or_above_80pct_beating_confidence": sorted(beats, key=lambda h: -h["picks"])[:10]}


def calibration_gap(market, rows, key) -> Optional[float]:
    have = [r for r in rows if key in r["vectors"]]
    if not have:
        return None
    bins = cr._summary(market, have, key)["calibration"]
    gaps = [abs(b["mean_p"] - b["win_rate"]) for b in bins if b["n"] >= 50]
    return max(gaps) if gaps else None


def diagnose(market, rows, folds, members) -> dict:
    sel_all = [r for f in folds for r in f["all_rules"]]
    small_pos = sum(1 for r in sel_all if r["lift"] is not None and r["lift"] > 0 and r["wins"] + r["losses"] < MIN_PICKS)
    big = [r for r in sel_all if r["wins"] + r["losses"] >= MIN_PICKS and r["lift"] is not None]
    unstable = [(rid, a["selection"]["lift"], a["lift"]) for f in folds for rid, a in f["assessed"].items()
                if a["selection"]["lift"] is not None and a["selection"]["lift"] > 0 and (a["lift"] is None or a["lift"] <= 0)]
    return {"rule_scores_across_folds": len(sel_all),
            "too_few_observations": f"{small_pos} rule×fold scores had a positive lift on fewer than {MIN_PICKS} picks",
            "no_gain_over_confidence": (f"{sum(r['lift'] <= 0 for r in big)} of {len(big)} rule×fold scores with ≥{MIN_PICKS} picks "
                                        f"did not beat the strongest single's confidence; best lower bound "
                                        f"{max((r['lower'] for r in big if r['lower'] is not None), default=None)}"),
            "unstable": f"{len(unstable)} frozen rules with a positive selection lift were ≤ 0 on the later season",
            "calibration_max_gap": {k: calibration_gap(market, rows, k) for k in members + ["average", "stack"]},
            "implementation_checks": "tests/test_selective_study.py (outcome-blind rules, earlier-only selection, pushes, "
                                     "missing members, three-way)"}


def analyse(table_name, market, table, members, first, seasons, primary_winner) -> dict:
    complete = [r for r in table if all(m in r["vectors"] for m in members) and r["realised"] is not None]
    for r in complete:
        r["favourite"] = primary_winner.get(r["game_id"], 0.5)
        r["disagreement"] = disagreement(r["vectors"], members, members[0], market)
        r["vectors"]["average"] = combine.average([r["vectors"][m] for m in members])
    stack_fits = cr._stack_walk(market, complete, members, first, seasons)
    rows = [r for r in complete if r["season"] in seasons]
    rules = registry(table_name, market, members, any("stack" in r["vectors"] for r in rows))
    folds = [fold(market, rows, members, rules, [s for s in seasons if s < a], a) for a in seasons[1:]]
    return {"table": table_name, "market": market, "members": members, "games": len(rows), "seasons": seasons,
            "rules": len(rules), "stack_fits": [{k: f[k] for k in ("season", "fitted") if k in f} for f in stack_fits],
            "folds": folds, "frontier": frontier(market, rows, members, rules), "diagnosis": diagnose(market, rows, folds, members)}


def run(conn, sport: str, out_dir: Optional[Path] = None, log: Callable[[str], None] = print) -> dict:
    started = utc_now()
    report = {"kind": "selective-combination", "study": STUDY, "sport": sport, "started_at": started, "exploratory": True,
              "markets": {}}
    built = [("nba", build_nba(conn, log)), ("nba_evening", build_nba_evening(conn, log))] if sport == "nba" \
        else [("nhl", build_nhl(conn, log))]
    for name, d in built:
        report.setdefault("tables", {})[name] = {"fingerprint": d["fingerprint"], "dataset_version": d["dataset_version"],
                                                 "cutoff": d["cutoff"], "members": d["members"]}
        wprimary = d["members"]["winner"][0]
        pw = {r["game_id"]: max(r["vectors"][wprimary][0], r["vectors"][wprimary][2]) for r in d["tables"]["winner"]}
        for market, table in d["tables"].items():
            members = d["members"]["winner"] if market == "winner" else d["members"]["reg3" if market == "reg3" else "line"]
            t0 = datetime.now()
            a = analyse(name, market, table, list(members), d["first"], list(d["seasons"]), pw)
            report["markets"][f"{name}:{market}"] = a
            log(f"{name}:{market}: {a['games']} games, {a['rules']} rules, qualified per fold "
                f"{[len(f['qualified']) for f in a['folds']]}, ≥80% rules {a['frontier']['count_at_or_above_80pct']}, "
                f"{(datetime.now() - t0).seconds} s")
    report["finished_at"] = utc_now()
    out_dir = out_dir or config.data_dir() / "reports" / f"selective-{sport}-{started.replace(':', '')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=1, default=str))
    report["output_dir"] = str(out_dir)
    log(f"wrote {out_dir / 'report.json'}")
    return report
