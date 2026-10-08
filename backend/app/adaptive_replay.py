"""Chronological replay of adaptive-v1 against the existing system (docs/ADAPTIVE_SYSTEM.md §6). Development data only:
NBA warm-up + validation seasons, NHL warm-up + development seasons; the reserved test seasons are never loaded.

Every adaptive member is walked with its registered schedule; the combiner is refitted at the first game of each ISO
week on rows whose outcome was available at that cutoff (last two completed seasons plus the current one). The existing
system (primary, core average, core season-start stacker) is scored on identical games and lines. Exploratory: no
ledger rows, no artifacts, no live change."""
import json
import time
from pathlib import Path
from typing import Callable, Optional

from . import adaptive, backtest, combination_research as cr, combine, config, live, nhl_eval, research_live
from .candidates import deep, features, study
from .db import utc_now

PERIOD = {"nba": "validation", "nhl": "development"}
EVALUATED = {"nba": backtest.PERIODS["validation"], "nhl": nhl_eval.PERIODS["development"]}
LOADABLE = {"nba": backtest.LOADABLE["validation"], "nhl": nhl_eval.LOADABLE["development"]}
EXISTING = {"primary": None, "average": None, "stack": None}


def _fingerprint(rows) -> str:
    import hashlib
    return hashlib.sha256("\n".join(sorted(r["game_id"] for r in rows)).encode()).hexdigest()[:16]


def adaptive_combiner_walk(market: str, rows: list, names: list, evaluated, key: str, log) -> list:
    """Weekly combiner refits over the evaluated seasons; adds `key` to the rows it covers. Returns the fit records."""
    rows = sorted(rows, key=lambda r: (r["cutoff_at"], r["game_id"]))
    periods: dict = {}
    for r in rows:
        if r["season"] in evaluated:
            periods.setdefault(adaptive.period_of(r["tipoff_at"], r["season"]), []).append(r)
    fits, model, fitted_fp = [], None, None
    for p, games in periods.items():
        cutoff = games[0]["cutoff_at"]
        season = games[0]["season"]
        window = adaptive._window({r["season"] for r in rows}, season)
        train = [r for r in rows if r["season"] in window and r["result_available_at"] <= cutoff]
        fp = _fingerprint(train)
        if model is None or fp != fitted_fp:
            res = adaptive.fit_combiner(train, names, market, cutoff, fallbacks=False)
            fitted_fp = fp
            if res.get("stack") is not None:
                assert all(r["result_available_at"] <= cutoff for r in train)
                model = res
                fits.append({"period": p, "cutoff": cutoff, "members": res["members"], "weights": res["weights"], "l2": res["l2"],
                             "training": res["training"], "excluded": res["excluded"]})
            else:
                fits.append({"period": p, "cutoff": cutoff, "fitted": False, "reason": res.get("reason")})
        if model is not None:
            for r in games:
                vec, _, _, reason = adaptive.combine_now(model, r["vectors"])
                if vec is not None:
                    r["vectors"][key] = vec
    log(f"{market} {key}: {sum(1 for f in fits if 'weights' in f)} combiner fits over {len(periods)} periods")
    return fits


def _compare(market: str, rows: list, sources: list, evaluated) -> dict:
    """Identical games (every source present, outcome known, evaluated seasons). Pushes are counted and left out of
    log loss / Brier (as the combiner's own objective)."""
    cls = combine._classes(market)
    ev = [r for r in rows if r["season"] in evaluated and r["realised"] is not None]
    common = [r for r in ev if all(s in r["vectors"] for s in sources)]
    scored = [r for r in common if r["realised"] in cls]
    out = {"evaluated_games": len(ev), "common_games": len(common), "scored_games": len(scored), "pushes": len(common) - len(scored),
           "coverage": {s: sum(1 for r in ev if s in r["vectors"]) / len(ev) if ev else None for s in sources}, "sources": {},
           "paired": {}}
    for s in sources:
        out["sources"][s] = cr._summary(market, scored, s) if scored else None
    base = sources[-1]
    for s in sources[:-1]:
        if scored:
            d = [combine.log_loss(r["vectors"][base], r["realised"]) - combine.log_loss(r["vectors"][s], r["realised"]) for r in scored]
            bb = backtest.block_bootstrap(d, [r["cutoff_at"] for r in scored])
            per = {}
            for season in evaluated:
                ds = [x for x, r in zip(d, scored) if r["season"] == season]
                per[str(season)] = sum(ds) / len(ds) if ds else None
            out["paired"][f"{base}_minus_{s}"] = {**bb, "per_season": per, "metric": "log loss (negative favours the adaptive source)"}
    return out


def run(conn, sport: str, out_dir: Optional[Path] = None, log: Callable[[str], None] = print,
        skip_deep: bool = False) -> dict:
    started = utc_now()
    t0 = time.time()
    evaluated = EVALUATED[sport]
    tables, first, existing, rows, events, recorder, dataset = study.existing_tables(conn, sport, PERIOD[sport])
    seasons = {r.game.season for r in rows}
    if seasons - set(LOADABLE[sport]):
        raise ValueError("adaptive replay read seasons outside the development data")
    log(f"{sport}: existing members on {len(rows)} games in {time.time() - t0:.0f} s")
    line_markets = [m for m in tables if m != "winner"]
    lines = {r["game_id"]: {} for r in tables["winner"]}
    for mk in line_markets:
        for r in tables[mk]:
            if r["line"] is not None:
                lines[r["game_id"]][mk] = r["line"]
    mems, excluded = adaptive.members(conn)
    eng = live.engine(conn)
    hist: dict = {}
    member_fits: dict = {}
    for m in [m for m in mems if m.kind == "walk"]:
        t1 = time.time()
        hist[m.name] = adaptive.walk_member(m, rows, adaptive._events_for(m.obj, eng, events), None, seasons, lines)
        w = m.obj
        member_fits[m.name] = {"refits_within_season": len(getattr(w, "refits", [])),
                               "skipped_no_new_results": getattr(w, "skipped_no_new_results", None),
                               "season_fits": getattr(getattr(w, "member", w), "fits", None), "seconds": round(time.time() - t1, 1)}
        log(f"{m.name}: {len(hist[m.name])} games, {member_fits[m.name]['refits_within_season']} within-season refits, "
            f"{member_fits[m.name]['seconds']} s")
    walk = features.walk(recorder, rows, events)
    hrows = list(walk.values())
    for m in [m for m in mems if m.kind == "candidate"]:
        spec = m.spec
        if (deep.is_deep(spec) and skip_deep) or not adaptive.can_fit(spec):
            excluded[m.name] = f"{adaptive.fit_dependency(spec)} not installed in this environment"
            continue
        t1 = time.time()
        store: dict = {}
        try:
            adaptive.scheduled_walk(spec, hrows, store, log=log)
        except Exception as exc:             # one candidate's failure is recorded; the replay goes on without it
            excluded[m.name] = f"walk failed: {type(exc).__name__}: {exc}"
            log(f"{m.name}: FAILED ({type(exc).__name__}: {exc})")
            continue
        firsts: dict = {}
        for r in hrows:                  # leakage: a fit never uses a result available after the period's first cutoff
            k = adaptive.period_of(r.tipoff_at, r.season)
            firsts[k] = min(firsts.get(k, r.cutoff_at), r.cutoff_at)
        for key_p, f in zip(store["periods"], store["fits"]):
            assert f["cutoff"] == firsts[key_p]
            if f["refit"]:
                assert f["results_through"] <= f["cutoff"], (spec.name, f)
        hist[m.name] = adaptive.candidate_vectors(spec, store["outputs"], m.markets, lines)
        member_fits[m.name] = {"fits": sum(1 for f in store["fits"] if f["refit"]), "periods": len(store["periods"]),
                               "kept": sum(1 for f in store["fits"] if not f["refit"]), "seconds": round(time.time() - t1, 1),
                               "reasons": sorted({f.get("reason") for f in store["fits"] if f.get("reason")})}
    report = {"kind": "adaptive-replay", "version": adaptive.VERSION, "sport": sport, "started_at": started, "dataset_version": dataset,
              "evaluated_seasons": list(evaluated), "loaded_seasons": sorted(seasons), "exploratory": True,
              "schedule": adaptive.SCHEDULE, "members": sorted(hist), "excluded": excluded, "member_fits": member_fits, "markets": {}}
    core = {"winner": list(research_live.CORE[sport]), **{mk: list(research_live.LINE_MEMBERS[sport]) for mk in line_markets}}
    for market, table in tables.items():
        rows_m = [dict(r, vectors=dict(r["vectors"]), tipoff_at=r["tipoff_at"]) for r in table]
        for r in rows_m:
            for name, h in hist.items():
                v = h.get(r["game_id"], {}).get(market)
                if v is not None:
                    r["vectors"][name] = v
        members_core = core[market]
        for r in rows_m:
            if all(n in r["vectors"] for n in members_core):
                r["vectors"]["average"] = combine.average([r["vectors"][n] for n in members_core])
        stack_fits = cr._stack_walk(market, rows_m, members_core, first, list(evaluated))
        primary = members_core[0]
        for r in rows_m:
            r["vectors"]["primary"] = r["vectors"][primary]
        names = [m.name for m in mems if market in m.markets and m.name in hist]
        fits_core = adaptive_combiner_walk(market, rows_m, members_core, evaluated, "adaptive_core", log)
        fits_full = adaptive_combiner_walk(market, rows_m, names, evaluated, "adaptive", log)
        singles = {n: cr._summary(market, [r for r in rows_m if r["season"] in evaluated and n in r["vectors"]
                                            and r["realised"] in combine._classes(market)], n)["log_loss"]
                   for n in names if any(n in r["vectors"] for r in rows_m)}
        report["markets"][market] = {
            "primary": primary, "core_members": members_core, "adaptive_members": names,
            "comparison": _compare(market, rows_m, ["primary", "average", "stack", "adaptive_core", "adaptive"], evaluated),
            "single_members_log_loss": singles, "existing_stack_fits": stack_fits,
            "adaptive_core_fits": fits_core[-3:], "adaptive_fits_last": fits_full[-3:],
            "adaptive_fit_count": sum(1 for f in fits_full if "weights" in f),
            "adaptive_weights_last": next((f["weights"] for f in reversed(fits_full) if "weights" in f), None),
            "adaptive_excluded_last": next((f["excluded"] for f in reversed(fits_full) if "weights" in f), None)}
        c = report["markets"][market]["comparison"]
        log(f"{market}: n={c['scored_games']} " + " ".join(f"{s}={v['log_loss']:.4f}" for s, v in c["sources"].items() if v))
    report["finished_at"] = utc_now()
    report["seconds"] = round(time.time() - t0)
    out_dir = out_dir or config.data_dir() / "reports" / f"adaptive-replay-{sport}-{started.replace(':', '')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=1, default=str))
    report["output_dir"] = str(out_dir)
    adaptive.EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    (adaptive.EVIDENCE_DIR / f"{sport}-replay.json").write_text(json.dumps(compact(report), indent=1, default=str) + "\n")
    log(f"wrote {out_dir / 'report.json'} and {adaptive.EVIDENCE_DIR / f'{sport}-replay.json'}")
    return report


def compact(report: dict) -> dict:
    """The committed summary read by the API: per market the comparison and the last weights; no per-game rows."""
    out = {k: report[k] for k in ("kind", "version", "sport", "started_at", "finished_at", "dataset_version", "evaluated_seasons",
                                  "loaded_seasons", "exploratory", "schedule", "members", "excluded", "seconds")}
    out["member_fits"] = {k: {kk: vv for kk, vv in v.items() if kk != "season_fits"} for k, v in report["member_fits"].items()}
    out["markets"] = {}
    for mk, v in report["markets"].items():
        c = v["comparison"]
        out["markets"][mk] = {"primary": v["primary"], "core_members": v["core_members"], "adaptive_members": v["adaptive_members"],
                              "evaluated_games": c["evaluated_games"], "scored_games": c["scored_games"], "pushes": c["pushes"],
                              "coverage": c["coverage"],
                              "sources": {s: ({k: x[k] for k in ("n", "accuracy", "accuracy_ci95", "log_loss", "brier", "calibration")}
                                              if x else None) for s, x in c["sources"].items()},
                              "paired": {k: {kk: x[kk] for kk in ("mean", "ci95", "per_season", "metric", "games")}
                                         for k, x in c["paired"].items()},
                              "single_members_log_loss": v["single_members_log_loss"], "adaptive_fit_count": v["adaptive_fit_count"],
                              "adaptive_weights_last": v["adaptive_weights_last"], "adaptive_excluded_last": v["adaptive_excluded_last"],
                              "adaptive_l2_last": (v["adaptive_fits_last"][-1].get("l2") if v["adaptive_fits_last"] else None)}
    return out
