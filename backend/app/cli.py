"""Explicit data commands. Run from backend/: `.venv/bin/python -m app.cli <command>`."""
import argparse
import json

from . import backtest, config, coverage, db, ingest, jobs
from .models.elo import EloConfig


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
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="Create/upgrade the SQLite schema")
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
    rf = sub.add_parser("refresh", help="Fetch current ESPN months (retries; at most once per 30 min unless --force)")
    rf.add_argument("--force", action="store_true")
    sub.add_parser("predict", help="Record live Elo forecasts for eligible games whose cutoff is within 7 days")
    sub.add_parser("score", help="Score the latest live forecasts against stored results (read-only)")
    dl = sub.add_parser("daily", help="refresh, then predict, then score")
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
    bt = sub.add_parser("backtest", help="Walk-forward evaluation (docs/EVALUATION_PLAN.md)")
    bt.add_argument("--period", choices=("validation", "test"), required=True)
    bt.add_argument("--elo-config", help='Validation only: evaluate a fixed Elo JSON instead of running the grid.')
    bt.add_argument("--decision", help="Test only: frozen validation decision JSON (default "
                    "backend/app/models/frozen_decision.json). Checked before any test row is read.")
    bt.add_argument("--confirm-final-test", action="store_true",
                    help="Required for --period test; the access is logged to data/eval/test_access.jsonl")
    bt.add_argument("--record", action="store_true", help="Also append predictions to the ledger as run_type=backtest")
    args = p.parse_args(argv)

    conn = db.connect()
    applied = db.migrate(conn)
    if args.cmd == "migrate":
        print(json.dumps({"db": str(config.db_path()), "applied": applied}))
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
        result = jobs.shadow_forecasts(conn) if args.record else shadow.predict(conn)
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
    elif args.cmd == "freeze-decision":
        from pathlib import Path
        report_path = Path(args.report).resolve()
        shown = report_path.relative_to(config.REPO_ROOT) if report_path.is_relative_to(config.REPO_ROOT) else report_path
        decision = backtest.decision_from_validation_report(json.loads(report_path.read_text()), str(shown))
        backtest.FROZEN_DECISION_PATH.write_text(json.dumps(decision, indent=2) + "\n")
        print(json.dumps({"written": str(backtest.FROZEN_DECISION_PATH), "primary_model": decision["primary_model"],
                          "elo_config": decision["elo_config"]}))
    elif args.cmd in ("refresh", "odds", "predict", "score", "daily", "scheduler"):
        if args.cmd == "refresh":
            result = jobs.refresh(conn, force=args.force)
        elif args.cmd == "odds":
            result = jobs.odds_snapshot(conn, force=args.force)
        elif args.cmd == "predict":
            result = jobs.predict(conn)
        elif args.cmd == "score":
            result = jobs.score(conn)
        elif args.cmd == "daily":
            result = jobs.daily(conn, force_refresh=args.force_refresh)
        else:
            result = jobs.scheduler(conn, interval_minutes=args.interval_minutes, max_runs=args.max_runs, job=args.job)
        print(json.dumps(result, indent=2, default=str))
        return 1 if _any_failed(result) else 0
    elif args.cmd == "coverage":
        print(json.dumps(coverage.report(conn), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
