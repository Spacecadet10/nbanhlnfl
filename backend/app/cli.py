"""Explicit data commands. Run from backend/: `.venv/bin/python -m app.cli <command>`."""
import argparse
import json
import os

from . import backtest, config, coverage, db, ingest, jobs, release, sports
from .models.elo import EloConfig


# Commands that work for every sport; everything else is NBA-only until generalised.
SPORT_COMMANDS = {"nhl-pbp-import", "nhl-xg-research", "migrate", "replay", "coverage", "ingest-nhl", "nhl-validate", "nhl-test", "nhl-record-test", "nhl-agreement", "nhl-learned", "nhl-agreement-explore", "rebuild-goalies", "refresh", "odds", "predict",
                  "score", "daily", "scheduler", "predict-now", "score-near-tipoff", "markets", "score-markets", "agreement", "thresholds",
                  "combination-study", "selector-decide", "research-report", "retrain-cadence", "catboost-challenger", "combination-search", "expanded-search", "selective-study", "rules-registry",
                  "daily-cadence", "features-nhl", "tracking-report", "tracking-record", "pool-screen", "pool-assess", "pool-record", "pool-roster", "pool-report",
                  "release-check", "diagnostics", "historical-start"}
NHL_ONLY = {"nhl-pbp-import", "nhl-xg-research", "ingest-nhl", "nhl-validate", "nhl-test", "nhl-record-test", "nhl-agreement", "nhl-learned", "nhl-agreement-explore", "rebuild-goalies"}


# Commands that only read the store: they run on a query-only connection without the job lock or a migration, so a long
# report can never make a scheduled two-hour cycle wait, skip or miss a game. (`score` is not here: it records a job run.)
READ_ONLY_COMMANDS = {"score-markets", "score-near-tipoff", "thresholds", "research-report", "pool-report", "pool-roster",
                      "tracking-report", "coverage", "release-check", "diagnostics", "historical-start"}
LOCK_WAIT_SECONDS = 45 * 60     # longer than any two-hour cycle (NHL ~13 minutes); a job still waiting then fails visibly
ODDS_LOCK_WAIT_SECONDS = 120    # the periodic cycle waits out a short job (daily, injuries), not another full cycle


def _record_lock_busy(sport: str, reason: str):
    """job_runs row for an odds cycle skipped because the store lock was busy; None if it could not be written (no store
    yet, or the running job holds the database). Never raises and never creates or migrates a store."""
    import sqlite3
    if not config.db_path(sport).is_file():
        return None
    try:
        conn = db.connect(sport=sport)
        try:
            return jobs.record_lock_busy(conn, "odds", reason) if db.has_table(conn, "job_runs") else None
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def _any_failed(result) -> bool:
    """True if any job result (nested dicts/lists) has status 'failed'."""
    if isinstance(result, list):
        return any(_any_failed(r) for r in result)
    if isinstance(result, dict):
        return result.get("status") == "failed" or any(_any_failed(v) for v in result.values()
                                                       if isinstance(v, (dict, list)))
    return False


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__)
    p.add_argument("--sport", choices=sports.SPORT_IDS, default="nba",
                   help="Which sport's store to use (default nba). Each sport has its own SQLite file.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="Create/upgrade the SQLite schema")
    nh = sub.add_parser("ingest-nhl", help="NHL only: import NHL stats season game lists (one request per season) and the team list")
    nh.add_argument("--from-season", type=int, required=True, help="First season end year, e.g. 2016 = 2015-16")
    nh.add_argument("--to-season", type=int, required=True)
    nh.add_argument("--sleep", type=float, default=1.5, help="Seconds between requests")
    nm = sub.add_parser("nba-market-research", help="NBA development research for spread/total/team-total distributions "
                                                     "(seasons 2019-20 … 2023-24 only); --seal records the chosen model")
    nm.add_argument("--seal", action="store_true")
    mk = sub.add_parser("markets", help="Record market forecasts (spread/puck line, totals, team totals, NHL 3-way) for "
                                        "games starting within two hours")
    mk.add_argument("--dry-run", action="store_true")
    sub.add_parser("score-markets", help="Settle and score the latest market forecasts (read-only)")
    sub.add_parser("nhl-validate", help="NHL only: development grids, then the one-time validation comparison; seals "
                                        "backend/app/models/nhl_frozen_decision.json (refuses to run twice)")
    nt = sub.add_parser("nhl-test", help="NHL only: the one-time final test of the sealed decision (logged; refuses to rerun)")
    nt.add_argument("--confirm-final-test", action="store_true", required=True)
    sub.add_parser("nhl-learned", help="NHL only: development screening of the logistic and gradient-boosted candidates vs Elo")
    na = sub.add_parser("nba-availability", help="NBA availability research (avail_v1): download data, or evaluate once "
                                                 "(development seasons 2019-24 only; needs the dev extra)")
    ap = sub.add_parser("availability-prepare", help="Prepare checksum-sealed historical facts and optional prior-season live minutes; no evaluation")
    ap.add_argument("--seed-season", type=int)
    na.add_argument("step", choices=("download", "evaluate"))
    sub.add_parser("rebuild-goalies", help="NHL only: import goalie listings from every cached ESPN NHL scoreboard (idempotent)")
    sub.add_parser("nhl-agreement-explore", help="NHL only: descriptive agreement map across the four models (development seasons)")
    sub.add_parser("nhl-agreement", help="NHL only: development-season agreement research (Elo vs goals model; no live change)")
    rt = sub.add_parser("nhl-record-test", help="NHL only: append the saved one-time test predictions to the ledger as backtest rows")
    rt.add_argument("--report", required=True, help="data/reports/nhl-test-*/ directory")
    ing = sub.add_parser("ingest", help="Fetch ESPN scoreboard months into the raw cache and import them")
    ing.add_argument("--from", dest="start", required=True, help="First month, YYYYMM")
    ing.add_argument("--to", dest="end", required=True, help="Last month, YYYYMM")
    ing.add_argument("--sleep", type=float, default=1.0, help="Seconds between requests")
    tb = sub.add_parser("import-team-box", help="Import possession inputs (TOV/OREB/DREB/FGA/FTA) from the digest-verified "
                                                "sportsdataverse ESPN team box release, one CSV per season")
    tb.add_argument("--from-season", type=int, required=True, help="First season end year, e.g. 2020 = 2019-20")
    tb.add_argument("--to-season", type=int, required=True)
    tb.add_argument("--historical-backfill", action="store_true",
                    help="Assume availability with the result, only for games whose results were imported retrospectively "
                         "(tipoff + 4 h). Without it, stats are usable only from their retrieval time.")
    sub.add_parser("replay", help="Re-import every cached response without network access")
    sub.add_parser("coverage", help="Print stored data coverage as JSON")
    sub.add_parser("release-check", help="Read-only: the release commit, schema, research definition and every reason the "
                                         "scheduled jobs would refuse to run from this checkout (docs: HANDOFF, operations)")
    rf = sub.add_parser("refresh", help="Fetch current ESPN months (retries; at most once per 30 min unless --force)")
    rf.add_argument("--force", action="store_true")
    sub.add_parser("predict", help="Refresh and record supported forecasts only within two hours of tipoff")
    sub.add_parser("thresholds", help="Read-only two-hour threshold records for every stored market and model")
    cs = sub.add_parser("combination-study", help="One-time retrospective combination/selection study (docs/MODEL_SELECTION.md §5)")
    cs.add_argument("--out", help="Report directory (default data/reports/combination-<sport>-<time>)")
    rc = sub.add_parser("retrain-cadence", help="Exploratory retraining-cadence comparison (docs/MODEL_SELECTION.md §9)")
    rc.add_argument("--out", help="Report directory (default data/reports/retrain-cadence-<sport>-<time>)")
    rc.add_argument("--member", action="append", help="Limit to these members (repeatable)")
    cb = sub.add_parser("catboost-challenger", help="Exploratory bounded CatBoost challenger (docs/MODEL_SELECTION.md §10)")
    cb.add_argument("--out", help="Report directory (default data/reports/catboost-<sport>-<time>)")
    cz = sub.add_parser("combination-search", help="Exploratory predefined combination search (docs/MODEL_SELECTION.md §11)")
    cz.add_argument("--out", help="Report directory (default data/reports/combination-search-<sport>-<time>)")
    pi = sub.add_parser("nhl-pbp-import", help="Import NHL play-by-play shots for final games (api-web.nhle.com)")
    pi.add_argument("--season", type=int, action="append", help="Limit to these seasons (end year; repeatable)")
    pi.add_argument("--limit", type=int)
    xr = sub.add_parser("nhl-xg-research", help="Exploratory xG candidate on development seasons (MODEL_SELECTION §12)")
    xr.add_argument("--out")
    ex = sub.add_parser("expanded-search", help="Expanded-combination experiment, retrospective part (MODEL_SELECTION §14)")
    ex.add_argument("--out")
    tsp = sub.add_parser("tuning-study", help="Bounded chronological Optuna tuning of HGB / ExtraTrees learners on both stores "
                                              "(MODEL_SELECTION §21); run on store copies")
    tsp.add_argument("--out")
    tev = sub.add_parser("tuning-evaluate", help="Evaluate tuning-v1 predictions alone and inside combinations (saved tables "
                                                 "only; opens no store)")
    tev.add_argument("--out")
    dsp = sub.add_parser("discovery-study", help="Historical training and combination discovery from saved chronological "
                                                 "tables; opens no store (MODEL_SELECTION §20)")
    dsp.add_argument("--out")
    gfp = sub.add_parser("gapfill-study", help="Gap-filling combination study from saved out-of-sample tables, both sports; "
                                               "opens no store (MODEL_SELECTION §16)")
    gfp.add_argument("--out")
    dc = sub.add_parser("daily-cadence", help="Daily vs weekly vs season-start refits of the §9 members (MODEL_SELECTION §17)")
    dc.add_argument("--out")
    sv = sub.add_parser("selective-study", help="Market-specific selective-combination study (MODEL_SELECTION §15)")
    sv.add_argument("--out")
    rg = sub.add_parser("rules-registry", help="Freeze the selective-v1 rule registry from the two study reports (MODEL_SELECTION §15)")
    rg.add_argument("--nba", required=True, help="NBA selective-study report.json")
    rg.add_argument("--nhl", required=True, help="NHL selective-study report.json")
    rg.add_argument("--effective-from", required=True)
    sub.add_parser("features-nhl", help="features-v1 NHL: shot volume and special-teams finishing (MODEL_SELECTION §19; exploratory)")
    sub.add_parser("diagnostics", help="Read-only exploratory diagnostics on non-test seasons: permutation importance, "
                                       "learning curves, score errors, goalie imputation (NHL), data checks; writes "
                                       "data/reports/diagnostics-<sport>/")
    sub.add_parser("historical-start", help="Freeze the separately labelled historical starting policy for the current "
                                            "research definition (walk-forward history, outcome base-rate reference; reads "
                                            "the store only, writes data/research/historical-start/; refuses to overwrite)")
    sub.add_parser("features-nba", help="features-v1 NBA: player-quality-weighted absences (MODEL_SELECTION §19; exploratory)")
    # Persistent agreement tracking tracking-v2 (MODEL_SELECTION §18)
    tg = sub.add_parser("tracking-registry", help="Seal the tracking-v2 group registry (refuses to replace an existing one)")
    tg.add_argument("--effective-from", required=True)
    sub.add_parser("tracking-historical", help="Classify every tracking-v2 group on the saved out-of-sample tables (exploratory; no store)")
    tr = sub.add_parser("tracking-report", help="Read-only tracking-v2 report for one market")
    tr.add_argument("--market", required=True)
    tr.add_argument("--evidence", choices=("prospective", "historical"), default="prospective")
    tr.add_argument("--check", action="store_true", help="Historical: chronological selection check (earlier → later seasons)")
    tc = sub.add_parser("tracking-record", help="Record tracking-v2 observations for games with pregame cohorts (normally the two-hour cycle)")
    tc.add_argument("--dry-run", action="store_true")
    # Candidate pool (docs/CANDIDATE_POOL.md)
    ps = sub.add_parser("pool-screen", help="Candidate pool stage 1: screen every registry candidate on development seasons")
    ps.add_argument("--dry-run", action="store_true", help="Score only a warm-up season; writes nothing")
    sub.add_parser("pool-assess", help="Candidate pool stage 2: assess the frozen shortlist once on later seasons (logged)")
    pr = sub.add_parser("pool-record", help="Two-hour candidate forecasts for winner/reg3 markets (lines come from the core cohort)")
    pr.add_argument("--dry-run", action="store_true")
    sub.add_parser("pool-roster", help="Read-only candidate roster")
    pr = sub.add_parser("pool-rules", help="Freeze the candidate-pool selection registry from the stage-2 evidence (CANDIDATE_POOL §7)")
    pr.add_argument("--effective-from", required=True)
    sub.add_parser("pool-report", help="Read-only prospective record of the pool registry's sources and pool decisions")
    sub.add_parser("selector-decide", help="Write this week's selector decisions if due (normally run by the odds job)")
    rr = sub.add_parser("research-report", help="Read-only prospective research record for one market")
    rr.add_argument("--market", default="winner")
    ag = sub.add_parser("agreement", help="Record prospective research-model winner comparisons; primary models unchanged")
    ag.add_argument("--dry-run", action="store_true")
    sub.add_parser("score", help="Score the latest live forecasts against stored results (records a job run; takes the job lock)")
    dl = sub.add_parser("daily", help="Nightly data maintenance and scoring; forecasts run through periodic odds jobs")
    dl.add_argument("--force-refresh", action="store_true")
    od = sub.add_parser("odds", help="Snapshot pregame odds for games tipping off within 36 h "
                                     "(ESPN day scoreboards; at most once per 10 min unless --force)")
    od.add_argument("--force", action="store_true")
    sc = sub.add_parser("scheduler", help="Run `daily` (or `odds`) every N minutes, a bounded number of times")
    sc.add_argument("--job", choices=("daily", "odds"), default="daily")
    sc.add_argument("--interval-minutes", type=float, default=360)
    sc.add_argument("--max-runs", type=int, required=True)
    fz = sub.add_parser("freeze-decision", help="Write the frozen decision from an existing validation report.json")
    fz.add_argument("--report", required=True, help="Path to a validation report.json")
    sub.add_parser("screen-candidates", help="Screen the preregistered shadow candidates on validation seasons with the "
                                             "frozen Elo config (no tuning, no ledger rows, no test access)")
    sub.add_parser("boxscores", help="Possession refresh: ESPN game summaries for recent finals lacking a complete box "
                                     "(max 30 requests per run; every retrieval timestamped)")
    ss = sub.add_parser("seal-shadow", help="Seal the shadow challenger's definition and promotion rule from a screening report")
    ss.add_argument("--report", required=True, help="Path to a screening report.json that shortlists the challenger")
    sp = sub.add_parser("shadow-predict", help="Shadow forecasts of the sealed challenger; a dry run unless --record")
    sp.add_argument("--record", action="store_true", help="Write the forecasts to the ledger (run_type live, own model_version)")
    sr = sub.add_parser("shadow-report", help="Shadow season report: coverage, job health and descriptive scores. "
                                              "--final applies the sealed promotion rule once, after the last play-in game")
    sr.add_argument("--final", action="store_true")
    sub.add_parser("research-gbt", help="Research round 2: one gradient-boosted tree candidate vs Elo and logistic_v2 on "
                                        "validation seasons (needs the dev extra; no ledger rows, no test access)")
    ij = sub.add_parser("injuries", help="Collect raw NBA injury-report PDFs for game windows (pilot; needs "
                                         "NBA_INJURY_COLLECTION=1; at most 8 attempts per run)")
    ij.add_argument("--dry-run", action="store_true", help="List the reports that would be requested; no request is made")
    sub.add_parser("research-stacking", help="Research round 3: two learned stackers vs Elo, logistic_v2 and the equal "
                                             "average on validation seasons (needs the dev extra; no ledger rows)")
    pn = sub.add_parser("predict-now", help="Near-tipoff forecasts: refresh data, then forecast games tipping off within "
                                            "two hours with the frozen Elo, or refuse them with a reason")
    pn.add_argument("--dry-run", action="store_true", help="Refresh and show the forecasts without storing them")
    sub.add_parser("score-near-tipoff", help="Score near-tipoff forecasts against results (read-only; separate record)")
    bt = sub.add_parser("backtest", help="Walk-forward evaluation (docs/EVALUATION_PLAN.md)")
    bt.add_argument("--period", choices=("validation", "test"), required=True)
    bt.add_argument("--elo-config", help='Validation only: evaluate a fixed Elo JSON instead of running the grid.')
    bt.add_argument("--decision", help="Test only: frozen validation decision JSON (default "
                    "backend/app/models/frozen_decision.json). Checked before any test row is read.")
    bt.add_argument("--confirm-final-test", action="store_true",
                    help="Required for --period test; the access is logged to data/eval/test_access.jsonl")
    bt.add_argument("--record", action="store_true", help="Also append predictions to the ledger as run_type=backtest")
    args = p.parse_args(argv)
    if args.cmd == "tuning-evaluate":
        from pathlib import Path
        from . import tuning_study
        report = tuning_study.evaluate(out_dir=Path(args.out) if args.out else None)
        print(json.dumps({"output_dir": report["output_dir"], "markets": list(report["markets"])}))
        return 0
    if args.cmd == "tuning-study":       # opens (and migrates) both stores: run on copies
        from pathlib import Path
        import importlib.util
        if importlib.util.find_spec("optuna") is None:      # refuse before any store is opened or migrated
            p.error("tuning-study needs the research extra (optuna==5.0.0); install it in a research venv, not the live one")
        from . import tuning_study

        def conn_for(sport):
            c = db.connect(sport=sport)
            db.migrate(c)
            db.claim_store(c, sport)
            return c
        report = tuning_study.run(conn_for, out_dir=Path(args.out) if args.out else None)
        print(json.dumps({"output_dir": report["output_dir"], "games": {s: v["games_per_market"] for s, v in report["sports"].items()}}))
        return 0
    if args.cmd == "discovery-study":    # reads saved tables only; no store is opened or migrated
        from pathlib import Path
        from . import discovery_study
        report = discovery_study.run(out_dir=Path(args.out) if args.out else None)
        print(json.dumps({"output_dir": report["output_dir"], "qualified": {m: [len(f["qualified"]) for f in a["folds"]]
                                                                            for m, a in report["markets"].items()}}))
        return 0
    if args.cmd == "gapfill-study":      # reads saved tables only; no store is opened or migrated
        from pathlib import Path
        from . import gapfill_study
        report = gapfill_study.run(out_dir=Path(args.out) if args.out else None)
        print(json.dumps({"output_dir": report["output_dir"], "qualified": {m: [len(f["qualified"]) for f in a.get("folds", [])]
                                                                            for m, a in report["markets"].items()}}))
        return 0

    if args.cmd in ("tracking-registry", "tracking-historical"):     # no store is opened or migrated
        from . import agreement_tracking as at
        if args.cmd == "tracking-registry":
            if at.REGISTRY_PATH.exists():
                p.error(f"{at.REGISTRY_PATH.name} exists and is sealed; a new registry needs a new study version")
            reg = at.build_registry(args.effective_from)
            at.REGISTRY_PATH.write_text(json.dumps(reg, indent=1, sort_keys=True) + "\n")
            print(json.dumps({"sha256": reg["sha256"], "markets": {k: {"groups": len(m["groups"]), "configurations":
                              m["configurations"], "skipped": len(m["skipped"])} for k, m in reg["markets"].items()}}, indent=1))
        else:
            print(json.dumps({"path": str(at.build_historical(args.sport))}))
        return 0

    if args.sport != "nba" and args.cmd not in SPORT_COMMANDS:
        p.error(f"`{args.cmd}` is NBA-only; it is not available with --sport {args.sport}")
    if args.sport == "nba" and args.cmd in NHL_ONLY:
        p.error(f"`{args.cmd}` needs --sport nhl")
    if release.enabled() and args.cmd in release.GUARDED:
        # Pinned release (app/release.py): refuse before any request, lock or migration if this is not a clean release
        # checkout using the shared data folder at the schema it ships. The refusal is a failed job run, never silent.
        info = release.check(args.sport)
        if info["problems"]:
            print(json.dumps({"status": "failed", "reason": "release guard", **info,
                              "run_id": release.record_refusal(args.sport, args.cmd, info)}))
            return 1
        os.environ[release.COMMIT_ENV] = info["commit"]          # recorded in every job run of this command
    if args.cmd in READ_ONLY_COMMANDS:
        try:
            conn = db.connect_readonly(args.sport)
        except FileNotFoundError as exc:
            p.error(f"{exc}; run `migrate` and the ingest command first")
        if not db.has_table(conn, "job_runs") or db.store_sport(conn) not in (args.sport, None):
            p.error(f"the {args.sport} store is not migrated or holds another sport's data")
        return _run(args, p, conn, [])
    # One job per store at a time: every command below opens (and migrates) the sport's store. The periodic odds cycle
    # waits out a short job but does not queue behind another full cycle (that cycle is doing the same work; the next
    # interval follows); other commands wait, then fail visibly. `scheduler` holds the lock per iteration, not while asleep.
    with db.store_lock(args.sport, wait_seconds=ODDS_LOCK_WAIT_SECONDS if args.cmd == "odds" else LOCK_WAIT_SECONDS) as held:
        if not held:
            busy = {"status": "skipped" if args.cmd == "odds" else "failed",
                    "reason": f"another job is running on the {args.sport} store"}
            if args.cmd == "odds":       # leave a trace: a cycle that never ran is otherwise invisible
                busy["run_id"] = _record_lock_busy(args.sport, busy["reason"])
            print(json.dumps(busy))
            return 0 if args.cmd == "odds" else 1
        conn = db.connect(sport=args.sport)
        applied = db.migrate(conn)
        db.claim_store(conn, args.sport)
        if args.cmd != "scheduler":
            return _run(args, p, conn, applied)
    return _run(args, p, conn, applied)


def _run(args, p, conn, applied) -> int:
    if args.cmd == "migrate":
        print(json.dumps({"db": str(config.db_path(args.sport)), "sport": args.sport, "applied": applied}))
    elif args.cmd == "ingest-nhl":
        import time
        results, failed = [], []
        for i, season in enumerate([None, *range(args.from_season, args.to_season + 1)]):
            if i and args.sleep:
                time.sleep(args.sleep)
            try:
                results.append(ingest.ingest_nhl_teams(conn) if season is None else ingest.ingest_nhl_season(conn, season))
            except Exception as exc:  # report and continue; nothing partial is committed for that request
                failed.append({"season": season or "teams", "error": f"{type(exc).__name__}: {str(exc).splitlines()[0]}"})
        print(json.dumps({"imports": results, "failed": failed}, indent=2))
        return 1 if failed else 0
    elif args.cmd == "nba-market-research":
        from . import nba_markets
        report = nba_markets.research(conn)
        out = {k: report[k] for k in ("dataset_version", "selected", "holdout", "holdout_nll_diff_vs_league", "output_dir")}
        out["top_trials"] = report["trials"][:4]
        if args.seal:
            out["sealed"] = nba_markets.seal(report)
        print(json.dumps(out, indent=2, default=str))
    elif args.cmd == "markets":
        result = jobs.predict_now(conn, dry_run=args.dry_run)
        print(json.dumps(result, indent=2, default=str))
        return 1 if _any_failed(result) else 0
    elif args.cmd == "score-markets":
        from . import market_live
        print(json.dumps({p: market_live.score(conn, policy=p) for p in market_live.POLICIES}, indent=2, default=str))
    elif args.cmd == "availability-prepare":
        from . import availability_live
        out = {"facts": availability_live.prepare(conn)}
        if args.seed_season:
            out["minutes"] = availability_live.seed_minutes(conn, args.seed_season)
        print(json.dumps(out, indent=2, default=str))
    elif args.cmd == "nba-availability":
        from . import availability
        if args.step == "download":
            out = {"player_box": [availability.download_player_box(y) for y in availability.SEASONS
                                  if not availability.player_box_path(y).exists()],
                   "reports": availability.download_reports(conn, log=print)}
            print(json.dumps(out, indent=2))
        else:
            r = availability.evaluate(conn)
            print(json.dumps({k: r[k] for k in ("scored_games", "excluded_early_tipoffs", "report_coverage",
                                                "avail_minus_logistic_v2_brier", "avail_minus_elo_brier", "decision",
                                                "output_dir")}, indent=2, default=str))
    elif args.cmd == "rebuild-goalies":
        print(json.dumps(ingest.rebuild_goalies(conn)))
    elif args.cmd == "nhl-agreement-explore":
        from . import nhl_eval
        r = nhl_eval.agreement_exploration(conn)
        print(json.dumps({"output_dir": r["output_dir"], "games": r["games"]}))
    elif args.cmd == "nhl-learned":
        from . import nhl_eval
        r = nhl_eval.learned_research(conn)
        print(json.dumps({k: r[k] for k in ("dataset_version", "learner_first_fit_season", "all_games", "vs_elo_brier",
                                            "unanimous_4", "decision", "output_dir")}, indent=2, default=str))
    elif args.cmd == "nhl-agreement":
        from . import nhl_eval
        r = nhl_eval.agreement_research(conn)
        print(json.dumps({k: r[k] for k in ("dataset_version", "games", "abstentions", "all_games", "average_vs_elo_brier",
                                            "average_vs_goals_brier", "disagreement", "decision", "output_dir")}, indent=2, default=str))
    elif args.cmd == "nhl-record-test":
        from pathlib import Path
        from . import nhl_eval
        print(json.dumps(nhl_eval.record_test_report(conn, Path(args.report))))
    elif args.cmd in ("nhl-validate", "nhl-test"):
        from . import nhl_eval
        try:
            report = nhl_eval.evaluate_validation(conn) if args.cmd == "nhl-validate" else nhl_eval.evaluate_test(conn)
        except nhl_eval.NhlEvaluationError as exc:
            print(json.dumps({"error": str(exc)}))
            return 1
        brief = {k: report[k] for k in ("stage", "seasons", "dataset_version", "output_dir")}
        brief["winner_models"] = {n: {k: m[k] for k in ("model_version", "n", "accuracy", "brier", "log_loss")}
                                  for n, m in report["winner_models"].items()}
        brief["markets"] = {k: {n: {x: v[n][x] for x in ("n", "brier", "log_loss")} for n in ("goals_poisson", "league_poisson")}
                            | {"goals_minus_league_log_loss": {x: v["goals_minus_league_log_loss"][x] for x in ("mean", "ci95")}}
                            for k, v in report["markets"].items()}
        for key in ("winner_selection", "goals_vs_elo_brier", "primary_winner_model", "descriptive"):
            if key in report:
                brief[key] = report[key]
        print(json.dumps(brief, indent=2, default=str))
    elif args.cmd == "ingest":
        results = ingest.ingest_range(conn, args.start, args.end, sleep=args.sleep)
        failed = [r for r in results if "error" in r]
        print(json.dumps({"months": len(results), "failed_months": [r["month"] for r in failed]}))
        return 1 if failed else 0
    elif args.cmd == "import-team-box":
        results, failed = [], []
        for season in range(args.from_season, args.to_season + 1):
            try:
                results.append(ingest.ingest_team_box(conn, season, historical_backfill=args.historical_backfill))
            except Exception as exc:  # report and continue; nothing partial is committed for that season
                failed.append({"season": season, "error": f"{type(exc).__name__}: {str(exc).splitlines()[0]}"})
        print(json.dumps({"imports": results, "failed": failed}, indent=2))
        return 1 if failed else 0
    elif args.cmd == "replay":
        print(json.dumps(ingest.replay(conn)))
    elif args.cmd == "backtest":
        if args.period == "test":
            if not args.confirm_final_test or args.elo_config:
                p.error("--period test requires --confirm-final-test and takes no --elo-config "
                        "(configuration comes from the frozen validation decision)")
            report = backtest.evaluate(conn, "test", decision_path=args.decision, record_predictions=args.record)
        else:
            cfg = EloConfig(**json.loads(args.elo_config)) if args.elo_config else None
            report = backtest.evaluate(conn, "validation", cfg, record_predictions=args.record)
        keys = ("period", "dataset_version", "elo_vs_home_rate_brier", "candidate_selection", "primary_model",
                "descriptive_comparisons", "output_dir")
        brief = {k: report[k] for k in keys if k in report}
        brief["models"] = {name: {k: m[k] for k in ("model_version", "n", "accuracy", "brier", "log_loss")}
                           for name, m in report["models"].items()}
        if "ledger_rows_created" in report:
            brief["ledger_rows_created"] = report["ledger_rows_created"]
        print(json.dumps(brief, indent=2))
    elif args.cmd == "screen-candidates":
        report = backtest.evaluate_screening(conn)
        print(json.dumps({"dataset_version": report["dataset_version"], "box_version": report["box_version"],
                          "screening": report["screening"], "fitted": report["fitted"],
                          "output_dir": report["output_dir"]}, indent=2))
    elif args.cmd == "boxscores":
        result = jobs.boxscores(conn)
        print(json.dumps(result, indent=2, default=str))
        return 1 if _any_failed(result) else 0
    elif args.cmd == "seal-shadow":
        from pathlib import Path
        from . import shadow
        if shadow.SHADOW_DECISION_PATH.exists():
            p.error(f"{shadow.SHADOW_DECISION_PATH} already exists; a sealed challenger is never overwritten")
        report_path = Path(args.report).resolve()
        shown = report_path.relative_to(config.REPO_ROOT) if report_path.is_relative_to(config.REPO_ROOT) else report_path
        decision = shadow.build_decision(conn, json.loads(report_path.read_text()), str(shown))
        shadow.SHADOW_DECISION_PATH.write_text(json.dumps(decision, indent=2) + "\n")
        print(json.dumps({"written": str(shadow.SHADOW_DECISION_PATH), "model_version": decision["model_version"],
                          "sha256": decision["sha256"]}))
    elif args.cmd == "shadow-predict":
        from . import shadow
        result = {"status": "skipped", "reason": "Night-before shadow recording discontinued under the two-hour policy; existing rows remain unchanged."} if args.record else shadow.predict(conn)
        print(json.dumps(result, indent=2, default=str))
        return 1 if _any_failed(result) else 0
    elif args.cmd == "shadow-report":
        from . import shadow_report
        try:
            print(json.dumps(shadow_report.report(conn, final=args.final), indent=2, default=str))
        except shadow_report.ShadowReportError as exc:
            print(json.dumps({"error": str(exc)}))
            return 1
    elif args.cmd == "research-gbt":
        report = backtest.evaluate_research_gbt(conn)
        keys = ("dataset_version", "box_version", "sklearn_version", "candidate_config", "fits", "comparisons",
                "step4_screen_against_elo", "output_dir")
        brief = {k: report[k] for k in keys}
        brief["models"] = {n: {k: m[k] for k in ("n", "accuracy", "brier", "log_loss")}
                           for n, m in report["models"].items()}
        print(json.dumps(brief, indent=2))
    elif args.cmd == "injuries":
        result = jobs.injury_reports(conn, dry_run=True) if args.dry_run else jobs.injury_reports(conn)
        print(json.dumps(result, indent=2, default=str))
        return 1 if _any_failed(result) else 0
    elif args.cmd == "research-stacking":
        report = backtest.evaluate_research_stacking(conn)
        print(json.dumps({k: report[k] for k in ("dataset_version", "box_version", "definition", "fits", "comparisons",
                                                 "step4_screen_against_elo", "selective_prediction", "agreement_filter",
                                                 "output_dir")}
                         | {"models": {n: {k: m[k] for k in ("n", "accuracy", "brier", "log_loss")}
                                       for n, m in report["models"].items()}}, indent=2))
    elif args.cmd == "predict-now":
        result = jobs.predict_now(conn, dry_run=args.dry_run)
        print(json.dumps(result, indent=2, default=str))
        return 1 if _any_failed(result) else 0
    elif args.cmd == "score-near-tipoff":
        from . import near_tipoff
        print(json.dumps(near_tipoff.score(conn), indent=2, default=str))
    elif args.cmd == "freeze-decision":
        from pathlib import Path
        report_path = Path(args.report).resolve()
        shown = report_path.relative_to(config.REPO_ROOT) if report_path.is_relative_to(config.REPO_ROOT) else report_path
        decision = backtest.decision_from_validation_report(json.loads(report_path.read_text()), str(shown))
        backtest.FROZEN_DECISION_PATH.write_text(json.dumps(decision, indent=2) + "\n")
        print(json.dumps({"written": str(backtest.FROZEN_DECISION_PATH), "primary_model": decision["primary_model"],
                          "elo_config": decision["elo_config"]}))
    elif args.cmd == "combination-study":
        from pathlib import Path
        from . import combination_research
        report = combination_research.run(conn, args.sport, out_dir=Path(args.out) if args.out else None)
        print(json.dumps({"output_dir": report["output_dir"], "markets": list(report["markets"])}))
    elif args.cmd == "retrain-cadence":
        from pathlib import Path
        from . import retrain_cadence
        report = retrain_cadence.run(conn, args.sport, out_dir=Path(args.out) if args.out else None, members=args.member)
        print(json.dumps({"output_dir": report["output_dir"], "members": list(report["members"])}))
    elif args.cmd == "catboost-challenger":
        from pathlib import Path
        from . import catboost_challenger
        report = catboost_challenger.run(conn, args.sport, out_dir=Path(args.out) if args.out else None)
        print(json.dumps({"output_dir": report["output_dir"], "qualifies": {t["task"]: t["qualifies"] for t in report["tasks"]}}))
    elif args.cmd == "combination-search":
        from pathlib import Path
        from . import combination_search
        report = combination_search.run(conn, args.sport, out_dir=Path(args.out) if args.out else None)
        print(json.dumps({"output_dir": report["output_dir"], "deploy": {m: a["deploy"] for m, a in report["markets"].items()}}))
    elif args.cmd == "nhl-pbp-import":
        from . import nhl_shots
        result = nhl_shots.import_pending(conn, args.season, args.limit)
        print(json.dumps({k: (v if k != "errors" else dict(list(v.items())[:20])) for k, v in result.items()}, indent=2))
        return 1 if result["errors"] and not result["imported"] else 0
    elif args.cmd == "nhl-xg-research":
        from pathlib import Path
        from . import nhl_eval
        report = nhl_eval.xg_research(conn, out_dir=Path(args.out) if args.out else None)
        print(json.dumps({"output_dir": report["output_dir"], "qualifies_for_shadow": report["qualifies_for_shadow"]}))
    elif args.cmd == "expanded-search":
        from pathlib import Path
        from . import expanded_search
        report = expanded_search.run(conn, args.sport, out_dir=Path(args.out) if args.out else None)
        print(json.dumps({"output_dir": report["output_dir"], "deploy_candidates": {m: a["deploy_candidates"] for m, a in report["markets"].items()}}))
    elif args.cmd == "daily-cadence":
        from pathlib import Path
        from . import cadence_daily
        report = cadence_daily.run(conn, args.sport, out_dir=Path(args.out) if args.out else None)
        print(json.dumps({"output_dir": report["output_dir"]}))
    elif args.cmd == "selective-study":
        from pathlib import Path
        from . import selective_study
        report = selective_study.run(conn, args.sport, out_dir=Path(args.out) if args.out else None)
        print(json.dumps({"output_dir": report["output_dir"], "qualified": {m: [len(f["qualified"]) for f in a["folds"]]
                                                                            for m, a in report["markets"].items()}}))
    elif args.cmd == "features-nhl":
        from .candidates import features_extra
        r = features_extra.evaluate(conn)
        print(json.dumps({k: r[k] for k in ("walk_games", "reading", "output_dir")} | {"results": {k: {p: {"mean": x["mean"],
              "games": x["games"], "bonferroni": x["bonferroni_interval"]} for p, x in v.items()} for k, v in r["results"].items()}},
              indent=1, default=str))
    elif args.cmd == "features-nba":
        from . import availability_quality
        r = availability_quality.evaluate(conn)
        print(json.dumps({k: r[k] for k in ("scored_games", "reading", "output_dir")} | {"comparisons": {k: {"mean": v["mean"],
              "bonferroni": v["bonferroni_interval"]} for k, v in r["comparisons"].items()}}, indent=1, default=str))
    elif args.cmd == "historical-start":
        from . import historical_start
        doc = historical_start.build(conn, args.sport)
        print(json.dumps({"path": doc["path"], "definition": doc["definition"], "frozen_at": doc["frozen_at"],
                          "markets": {m: {k: v.get(k) for k in ("status", "rule", "lower", "games", "reason")}
                                      for m, v in doc["markets"].items()}}, indent=1, default=str))
    elif args.cmd == "diagnostics":
        from . import diagnostics
        r = diagnostics.run(conn, args.sport)
        print(json.dumps({"output_dir": r["output_dir"], "permutation_importance": [{"model": pi["model"], "baseline": pi["baseline"],
              "features": {k: {m: round(x["mean"], 5) for m, x in v.items()} for k, v in pi["features"].items()}}
              for pi in r["permutation_importance"]], "learning_curve": [{"model": c["model"], "points": [
              {"seasons": q["seasons"], "loss": q["loss"]} for q in c["points"]]} for c in r["learning_curve"]],
              "goalie_imputation": r["goalie_imputation"] and r["goalie_imputation"]["comparison"],
              "data_checks": {k: len(v) for k, v in r["data_checks"].items()}}, indent=1, default=str))
    elif args.cmd == "tracking-report":
        from . import agreement_tracking as at
        if args.check:
            print(json.dumps(at.chronological_check(args.sport, args.market), indent=1, default=str))
        else:
            r = at.report(conn, args.sport, args.market, evidence=args.evidence)
            print(json.dumps({k: r[k] for k in r if k not in ("groups", "kinds")} | {"groups": [
                {"id": g["id"], "eligible": g["eligible"], "all_picks": {k: g["all_picks"][k] for k in ("decided", "win_rate")},
                 "status_counts": g["status_counts"]} for g in r["groups"]]}, indent=1, default=str))
    elif args.cmd == "tracking-record":
        from . import agreement_tracking as at
        ids = [r[0] for r in conn.execute("SELECT DISTINCT game_id FROM research_cohorts")]
        print(json.dumps(at.record(conn, game_ids=ids, write=not args.dry_run), indent=1))
    elif args.cmd == "rules-registry":
        from pathlib import Path
        from . import rule_tracking
        reports = {k: {**json.loads(Path(v).read_text()), "output_dir": str(Path(v).parent)}
                   for k, v in (("nba", args.nba), ("nhl", args.nhl))}
        reg = rule_tracking.build_registry(reports, args.effective_from)
        rule_tracking.REGISTRY_PATH.write_text(json.dumps(reg, indent=1, sort_keys=True) + "\n")
        print(json.dumps({"path": str(rule_tracking.REGISTRY_PATH), "sha256": reg["sha256"],
                          "markets": {k: {"rules": len(m["rules"]), "selected": sum(1 for r in m["rules"] if r["selected"])}
                                      for k, m in reg["markets"].items()}}))
    elif args.cmd == "pool-rules":
        from .candidates import selection
        reg = selection.build_registry(args.effective_from)
        selection.REGISTRY_PATH.write_text(json.dumps(reg, indent=1, sort_keys=True) + "\n")
        print(json.dumps({"path": str(selection.REGISTRY_PATH), "sha256": reg["sha256"],
                          "sources": {f"{sp}:{m}": [(e["source"], e["route"]) for e in es]
                                      for sp, ms in reg["sports"].items() for m, es in ms.items()}}, indent=1))
    elif args.cmd == "pool-report":
        from .candidates import selection
        print(json.dumps(selection.report(conn, args.sport), indent=1, default=str))
    elif args.cmd in ("pool-screen", "pool-assess", "pool-record", "pool-roster"):
        from .candidates import live as pool_live, study as pool_study
        if args.cmd == "pool-screen":
            r = pool_study.run_screen(conn, args.sport, dry=args.dry_run)
            print(json.dumps({m: {"games": v.get("games"), "strongest": v.get("strongest_existing"),
                                  "shortlist": v.get("shortlist")} for m, v in r["markets"].items()}))
        elif args.cmd == "pool-assess":
            r = pool_study.run_assess(conn, args.sport)
            print(json.dumps({m: {"games": v.get("games")} for m, v in r["markets"].items()}))
        elif args.cmd == "pool-record":
            r = pool_live.record(conn, write=not args.dry_run)
            print(json.dumps({k: v for k, v in r.items() if k != "forecasts"}, default=str))
        else:
            print(json.dumps(pool_live.roster(conn, args.sport), indent=1, default=str))
    elif args.cmd == "selector-decide":
        result = jobs.selector_decisions(conn)
        print(json.dumps(result, indent=2))
        return 1 if result["status"] == "failed" else 0
    elif args.cmd == "research-report":
        from . import research_live
        print(json.dumps(research_live.report(conn, args.market), indent=2, default=str))
    elif args.cmd == "thresholds":
        from . import tracking
        print(json.dumps(tracking.report(conn), indent=2))
    elif args.cmd == "agreement":
        from . import agreement
        result = jobs.predict_now(conn, dry_run=args.dry_run)
        print(json.dumps(result, indent=2))
        return 1 if result["status"] == "failed" else 0
    elif args.cmd in ("refresh", "odds", "predict", "score", "daily", "scheduler"):
        if args.cmd == "refresh":
            result = jobs.refresh(conn, force=args.force)
        elif args.cmd == "odds":
            snap = jobs.odds_snapshot(conn, force=args.force, props=False)
            result = {"odds": snap, "forecasts": jobs.forecast_due(conn)}
            snap["props"] = jobs.collect_props(conn, snap)       # after the time-critical forecasts, never before them
        elif args.cmd == "predict":
            result = jobs.predict_now(conn)
        elif args.cmd == "score":
            result = jobs.score(conn)
        elif args.cmd == "daily":
            result = jobs.daily(conn, force_refresh=args.force_refresh)
        else:
            result = jobs.scheduler(conn, interval_minutes=args.interval_minutes, max_runs=args.max_runs, job=args.job,
                                    lock=lambda: db.store_lock(args.sport, wait_seconds=ODDS_LOCK_WAIT_SECONDS))
        print(json.dumps(result, indent=2, default=str))
        return 1 if _any_failed(result) else 0
    elif args.cmd == "coverage":
        print(json.dumps(coverage.report(conn), indent=2))
    elif args.cmd == "release-check":
        from . import research_live, selector
        info = release.check(args.sport)
        print(json.dumps({**info, "app": str(release.code_root() / "backend" / "app"),
                          "research_definition": research_live.definition(conn), "selector": selector.version(),
                          "guard_enabled": release.enabled()}, indent=1))
        return 1 if info["problems"] else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
