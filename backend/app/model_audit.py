"""Decision and model-contribution audit (read-only; exploratory). Per sport and market it answers: what the system uses,
whether combining models improves on the best single model, what each model adds, how reliable the probabilities are,
how complete the inputs were before each forecast, and what evidence is still missing.

Sources of evidence, never mixed:
  historical  — walk-forward forecasts of the live research members' own classes and configurations
                (historical_start._history) on already-used seasons (NBA validation 2022-24, NHL development 2018-21); the
                stacker is refit as live. Seasons were used to choose the models: exploratory, not held-out.
  candidates  — the saved candidate-pool screening tables (same shared walk, screening seasons only; reserved test seasons
                excluded) and the registered deep-v1 study (docs/evidence/deep-v1), quoted, not recomputed.
  prospective — settled two-hour research cohorts in the store, per research definition (small samples).
  inputs      — what was present before each two-hour forecast in the store (quotes, members, missed games, job runs).
Two-way markets are scored on decided games with the push removed and the two sides renormalised; 3-way on all three.
Nothing here changes a forecast, a model, a selector or a stored row.
"""
import gzip
import json
import math
from datetime import timedelta
from pathlib import Path
from statistics import NormalDist
from typing import Callable, Optional

from . import backtest, combine, config, historical_start, metrics, nhl_eval, research_live
from .db import utc_now

COVERAGES = (0.10, 0.25, 0.50, 1.00)
BINS = 10
RECENT_DAYS = 14
DEEP_EVIDENCE = Path(__file__).resolve().parents[2] / "docs" / "evidence" / "deep-v1" / "evaluate-report.json"
Z80 = NormalDist().inv_cdf(0.975) + NormalDist().inv_cdf(0.80)


# --- Scoring helpers ----------------------------------------------------------------------------------------------
def _vec(market: str, v) -> tuple:
    """Two-way: the decided-game probabilities (push removed, renormalised); 3-way: as given."""
    if market in combine.THREE_WAY:
        return tuple(v)
    s = v[0] + v[2]
    return (v[0] / s, 0.0, v[2] / s)


def _decided(market: str, rows: list) -> list:
    return [r for r in rows if r["realised"] in combine.sides(market)]


def _ll(market, r, src) -> float:
    return combine.log_loss(_vec(market, r["vectors"][src]), r["realised"])


def _summary(market: str, rows: list, src: str) -> dict:
    """Probability quality, calibration and pick accuracy for one source on `rows` (decided games)."""
    vecs = [_vec(market, r["vectors"][src]) for r in rows]
    real = [r["realised"] for r in rows]
    picks = [combine.pick(v, market) for v in vecs]
    hits = [(v[p], p == y) for v, p, y in zip(vecs, picks, real) if p is not None]
    bins = [[0, 0.0, 0] for _ in range(BINS)]
    lo = 1 / len(combine.sides(market))
    for p, ok in hits:
        b = min(BINS - 1, int((p - lo) / (1 - lo) * BINS))
        bins[b][0] += 1
        bins[b][1] += p
        bins[b][2] += ok
    n = len(hits)
    ece = sum(abs(s / k - w / k) * k for k, s, w in bins if k) / n if n else None
    out = {"games": len(rows), "log_loss": metrics.log_loss_multi(vecs, real), "brier": metrics.brier_multi(vecs, real),
           "accuracy": sum(ok for _, ok in hits) / n if n else None,
           "mean_confidence": sum(p for p, _ in hits) / n if n else None, "ece": ece,
           "calibration": [{"n": k, "mean_p": s / k, "win_rate": w / k} for k, s, w in bins if k]}
    if market not in combine.THREE_WAY:     # calibration in the large: mean P(side a) vs observed share of side a
        out["mean_p_a"] = sum(v[0] for v in vecs) / len(vecs) if vecs else None
        out["observed_a"] = sum(y == 0 for y in real) / len(real) if real else None
    return out


def _coverage(market: str, rows: list, src: str) -> dict:
    """Accuracy of the most confident share of games: comparable across sources at the same coverage."""
    ranked = sorted(rows, key=lambda r: (-max(_vec(market, r["vectors"][src])[i] for i in combine.sides(market)), r["game_id"]))
    out = {}
    for q in COVERAGES:
        top = ranked[:max(1, int(round(q * len(ranked))))]
        res = [combine.pick(_vec(market, r["vectors"][src]), market) == r["realised"] for r in top]
        w = sum(res)
        out[f"{q:.2f}"] = {"games": len(res), "accuracy": w / len(res), "ci95": nhl_eval._wilson(w, len(res))}
    return out


def _paired(market: str, rows: list, a: str, b: str) -> Optional[dict]:
    """Log loss of a minus b on the same games, week-block bootstrap; negative = a better. Also the games a test of this
    size would need to detect the observed mean difference (80% power, two-sided 5%, games treated as independent)."""
    rs = [r for r in rows if a in r["vectors"] and b in r["vectors"]]
    if len(rs) < 30:
        return None
    diffs = [_ll(market, r, a) - _ll(market, r, b) for r in rs]
    bb = backtest.block_bootstrap(diffs, [r["cutoff_at"] for r in rs])
    mean = sum(diffs) / len(diffs)
    sd = math.sqrt(sum((d - mean) ** 2 for d in diffs) / (len(diffs) - 1))
    need = math.ceil((Z80 * sd / abs(mean)) ** 2) if mean else None
    ci = bb.get("ci95")
    verdict = ("a better" if ci and ci[1] < 0 else "b better" if ci and ci[0] > 0 else "no detectable difference")
    return {"a": a, "b": b, "games": len(rs), "mean": mean, "ci95": ci, "verdict": verdict, "games_to_detect": need}


def _residual_corr(market: str, rows: list, members: list) -> dict:
    """How much the members' forecasts overlap. Correlating errors against the shared outcome is dominated by that outcome
    (every model looks ~1.0), so this uses the forecasts themselves: pairwise correlation of each member's logit P(side a)
    across games, the share of games where two members pick different sides, and the effective number of independent
    members k / (1 + (k − 1)·mean correlation). Members that move together are not independent confirmations."""
    def logit(v):
        p = min(1 - 1e-9, max(1e-9, _vec(market, v)[0]))
        return math.log(p / (1 - p))
    sig = {m: [logit(r["vectors"][m]) for r in rows] for m in members}
    pick = {m: [combine.pick(_vec(market, r["vectors"][m]), market) for r in rows] for m in members}
    pairs, vals = {}, []
    for i, x in enumerate(members):
        for y in members[i + 1:]:
            c = _corr(sig[x], sig[y])
            pairs[f"{x}|{y}"] = {"forecast_corr": c,
                                 "pick_disagreement": sum(a != b for a, b in zip(pick[x], pick[y])) / len(rows) if rows else None}
            vals.append(c)
    mean = sum(vals) / len(vals) if vals else None
    k = len(members)
    return {"pairs": pairs, "mean_forecast_corr": mean, "members": k,
            "effective_members": k / (1 + (k - 1) * max(0.0, mean)) if mean is not None else None}


def _corr(a, b) -> float:
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va, vb = sum((x - ma) ** 2 for x in a), sum((y - mb) ** 2 for y in b)
    return cov / math.sqrt(va * vb) if va and vb else 0.0


def _add_avg(rows, members, key):
    for r in rows:
        if all(m in r["vectors"] for m in members):
            r["vectors"][key] = combine.average([r["vectors"][m] for m in members])


# --- Historical (live members, walk-forward) ----------------------------------------------------------------------
def _historical_market(market: str, table: list, members: list, first: dict, seasons) -> dict:
    settled = [r for r in table if r["realised"] is not None]
    complete = [r for r in settled if all(m in r["vectors"] for m in members)]
    _add_avg(complete, members, "average")
    fits = historical_start._live_stack_walk(market, settled, complete, members, list(seasons))
    rows = _decided(market, [r for r in complete if r["season"] in seasons])
    common = [r for r in rows if "stack" in r["vectors"]]
    sources = list(members) + ["average", "stack"]
    out = {"members": list(members), "games": len(rows), "games_with_stack": len(common),
           "seasons": sorted({r["season"] for r in rows}), "stack_fits": fits,
           "sources": {s: {**_summary(market, common, s), "coverage": _coverage(market, common, s)} for s in sources}}
    # Best single member for each season, chosen on EARLIER evaluated seasons only (never with hindsight).
    best_of, chosen = {}, {}
    for s in sorted({r["season"] for r in common}):
        prior = [r for r in common if r["season"] < s]
        if not prior:
            continue
        b = min(members, key=lambda m: (sum(_ll(market, r, m) for r in prior), members.index(m)))
        chosen[str(s)] = b
        for r in common:
            if r["season"] == s:
                r["vectors"]["best_prior"] = r["vectors"][b]
                best_of[r["game_id"]] = b
    later = [r for r in common if "best_prior" in r["vectors"]]
    out["best_prior_by_season"] = chosen
    out["comparisons"] = {k: _paired(market, later, a, b) for k, (a, b) in {
        "average_vs_best_prior": ("average", "best_prior"), "stack_vs_best_prior": ("stack", "best_prior"),
        "stack_vs_average": ("stack", "average")}.items()}
    out["later_seasons_summary"] = {s: _summary(market, later, s) for s in ("best_prior", "average", "stack")}
    # What each member adds: the equal average without it vs with it (positive = removing it makes the average worse).
    loo = {}
    for m in members:
        rest = [x for x in members if x != m]
        key = f"avg_without:{m}"
        _add_avg(common, rest, key)
        p = _paired(market, common, key, "average")
        loo[m] = p and {**p, "removing_worsens": p["ci95"] is not None and p["ci95"][0] > 0,
                        "removing_improves": p["ci95"] is not None and p["ci95"][1] < 0}
    out["leave_one_out"] = loo
    out["error_correlation"] = _residual_corr(market, common, list(members))
    return out


# --- Candidates (saved screening tables) and deep networks (registered study) ------------------------------------
def _candidates(sport: str, core: dict) -> dict:
    path = config.data_dir() / "research" / "candidates" / f"oof-{sport}-screen.json.gz"
    out = {"source": str(path.name), "markets": {}}
    if not path.is_file():
        out["status"] = "unavailable"
        return out
    d = json.loads(gzip.decompress(path.read_bytes()))
    out["seasons"] = d.get("seasons")
    for market, rows in d["tables"].items():
        members = [m for m in core.get(market, []) if rows and m in rows[0]["vectors"]]
        rows = _decided(market, [dict(r, vectors=dict(r["vectors"])) for r in rows if r["realised"] is not None])
        rows = [r for r in rows if all(m in r["vectors"] for m in members)]
        if len(members) < 2 or not rows:
            continue
        _add_avg(rows, members, "core_avg")
        cands = sorted({k for r in rows for k in r["vectors"]} - set(members) - {"core_avg"})
        res = {}
        for c in cands:
            have = [r for r in rows if c in r["vectors"]]
            _add_avg(have, members + [c], f"with:{c}")
            res[c] = {"alone_vs_core_average": _paired(market, have, c, "core_avg"),
                      "adding_to_core_average": _paired(market, have, f"with:{c}", "core_avg")}
        out["markets"][market] = {"core_members": members, "games": len(rows), "candidates": res}
    return out


def _deep(sport: str) -> dict:
    if not DEEP_EVIDENCE.is_file():
        return {"status": "unavailable"}
    r = json.loads(DEEP_EVIDENCE.read_text())
    out = {"study": r.get("study"), "rule": r.get("decision_rule"), "markets": {}}
    for key, m in r["markets"].items():
        sp, market = key.split(":")
        if sp != sport:
            continue
        out["markets"][market] = {n: {"games": x["games"], "alone_minus_best_prior_existing": x["log_loss_minus_best_prior_existing"],
                                      "minus_sklearn_counterpart": x.get("log_loss_minus_counterpart"),
                                      "counterpart": x.get("counterpart"), "qualifies": x.get("qualifies")}
                                  for n, x in m["deep"].items()}
    out["not_measured"] = "added inside an average or stack (no saved per-game deep forecasts on the shared walk)"
    return out


# --- Prospective (store) ------------------------------------------------------------------------------------------
def _prospective(conn, sport: str, market: str, as_of: str) -> dict:
    by_def: dict = {}
    for r in research_live.canonical_cohorts(conn, market, as_of=as_of):
        if research_live.is_expanded(r["definition"]) or research_live.state(r, as_of) != "settled":
            continue
        y = research_live._realised(market, r["line"], r, r["decision"])
        if y is None or y not in combine.sides(market):
            continue
        it = research_live.item(r, y)
        by_def.setdefault(r["definition"], []).append({**it, "cutoff_at": r["generated_at"]})
    out = {}
    for defn, rows in by_def.items():
        srcs = sorted({k for r in rows for k in r["vectors"]})
        quoted = [r for r in rows if r["bookmaker"] is not None]
        for r in quoted:
            r["vectors"]["bookmaker"] = tuple(r["bookmaker"])
        out[defn] = {"settled": len(rows), "quoted": len(quoted),
                     "sources": {s: {"games": len([r for r in rows if s in r["vectors"]]),
                                     "log_loss": (lambda h: sum(_ll(market, r, s) for r in h) / len(h) if h else None)(
                                         [r for r in rows if s in r["vectors"]])} for s in srcs},
                     "average_vs_primary": _paired(market, rows, "average", research_live.expected(sport, market)[0]),
                     "primary_vs_bookmaker_on_quoted": _paired(market, quoted, research_live.expected(sport, market)[0],
                                                               "bookmaker")}
    return out


# --- Inputs before each forecast (store) --------------------------------------------------------------------------
def _inputs(conn, sport: str, as_of: str) -> dict:
    from . import live
    since = (research_live._parse(as_of) - timedelta(days=RECENT_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    eng_types = tuple(live.engine(conn).eligible_types)
    cohorts = conn.execute("SELECT market, bookmaker, missing, definition FROM research_cohorts WHERE generated_at>=? AND "
                           "generated_at<=?", (since, as_of)).fetchall()
    per_market: dict = {}
    for c in cohorts:
        if research_live.is_expanded(c["definition"]):
            continue
        m = per_market.setdefault(c["market"], {"cohorts": 0, "with_quote": 0, "missing": {}})
        m["cohorts"] += 1
        m["with_quote"] += json.loads(c["bookmaker"]) is not None
        for name, why in json.loads(c["missing"]).items():
            key = f"{name}: {why.split(':')[0]}"
            m["missing"][key] = m["missing"].get(key, 0) + 1
    first = conn.execute("SELECT MIN(generated_at) FROM near_tipoff_forecasts").fetchone()[0]
    since_games = max(since, first) if first else as_of      # only games after two-hour recording began
    started = conn.execute(
        f"SELECT g.game_id FROM games g WHERE g.tipoff_at>=? AND g.tipoff_at<=? AND g.status NOT IN ('postponed','canceled') "
        f"AND g.season_type IN ({','.join('?' * len(eng_types))})", (since_games, as_of, *eng_types)).fetchall()
    forecast = {r[0] for r in conn.execute("SELECT DISTINCT game_id FROM near_tipoff_forecasts WHERE generated_at>=? "
                                           "AND generated_at<=?", (since, as_of))}
    jobs = conn.execute("SELECT job, status, COUNT(*) AS n FROM job_runs WHERE started_at>=? AND started_at<=? "
                        "GROUP BY job, status", (since, as_of)).fetchall()
    return {"window_days": RECENT_DAYS, "markets": per_market, "two_hour_recording_since": first,
            "eligible_games_started": len(started), "with_two_hour_forecast": sum(g[0] in forecast for g in started),
            "missed_games": sorted(g[0] for g in started if g[0] not in forecast),
            "job_runs": [dict(j) for j in jobs]}


# --- Structure ----------------------------------------------------------------------------------------------------
def _structure(conn, sport: str, as_of: str) -> dict:
    from . import agreement
    from .candidates import live as pool_live
    roster = pool_live.roster(conn, sport, as_of)
    out = {"winner": {"primary": agreement.NAMES[sport][0], "voters": list(agreement.NAMES[sport]),
                      "shadow_never_votes": list(agreement.SHADOW[sport]), "average_and_stack": list(research_live.CORE[sport])},
           "lines": {"primary": research_live.LINE_MEMBERS[sport][0], "voters": list(research_live.LINE_MEMBERS[sport]),
                     "shadow_never_votes": list(research_live.SHADOW_LINE_MEMBERS[sport]),
                     "average_and_stack": list(research_live.LINE_MEMBERS[sport])},
           "candidates": [{"name": c["name"], "markets": c["markets"], "training_status": c["training_status"],
                           **{k: c["coverage"][k] for k in ("fitted", "forecasting", "in_agreement_tracking", "selectable")}}
                          for c in roster["candidates"]],
           "members_now": roster.get("members", []),
           "primary_forecast_note": "The published win probability is the primary model alone; members, averages, stacks, "
                                    "candidates and selectors are research and never change it."}
    return out


# --- Report -------------------------------------------------------------------------------------------------------
def run(conn, sport: str, as_of: Optional[str] = None, log: Callable[[str], None] = print,
        out_dir: Optional[Path] = None) -> dict:
    as_of = as_of or utc_now()
    started = utc_now()
    tables, first, version, members, seasons = historical_start._history(conn, sport)
    report = {"kind": "decision and model-contribution audit (exploratory)", "sport": sport, "as_of": as_of,
              "generated_at": started, "research_definition": research_live.definition(conn), "dataset_version": version,
              "historical_seasons": list(seasons), "first_out_of_sample_season": first,
              "structure": _structure(conn, sport, as_of), "markets": {}}
    core = {}
    for market, table in tables.items():
        mem = members["winner"] if market == "winner" else members["lines"]
        core[market] = mem
        log(f"{sport} {market}: historical")
        h = _historical_market(market, table, mem, first, seasons)
        report["markets"][market] = {"historical": h, "prospective": _prospective(conn, sport, market, as_of)}
    log(f"{sport}: candidates and deep networks")
    report["candidates"] = _candidates(sport, core)
    report["deep"] = _deep(sport)
    report["inputs"] = _inputs(conn, sport, as_of)
    report["finished_at"] = utc_now()
    out_dir = Path(out_dir or config.data_dir() / "reports" / f"model-audit-{sport}")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=1, default=str))
    report["output_dir"] = str(out_dir)
    return report
