"""NHL walk-forward evaluation under the preregistered plan (docs/EVALUATION_PLAN.md, "NHL evaluation plan").

Stages: development (each candidate's grid, scored on development seasons only), validation (tuned candidates compared
once; the choice is sealed in models/nhl_frozen_decision.json and the stage refuses to run again), test (once, logged).
The walk-forward engine, leakage checks and bootstraps are the NBA ones (backtest.run); the rows come from the NHL store.
"""
import csv
import hashlib
import itertools
import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional

from . import backtest, config, markets, metrics, sports
from .db import utc_now
from .models.baselines import Constant, HomeRate
from .models.base import PregameGame
from .models.hockey import GoalsConfig, GoalsPoisson, HockeyElo, HockeyEloConfig, HockeyResult, regulation_nll

SPORT = sports.NHL
PERIODS = {"warmup": (2016, 2017), "development": (2018, 2019, 2020, 2021), "validation": (2022, 2023, 2024),
           "test": (2025, 2026)}
LOADABLE = {"development": PERIODS["warmup"] + PERIODS["development"],
            "validation": PERIODS["warmup"] + PERIODS["development"] + PERIODS["validation"],
            "test": PERIODS["warmup"] + PERIODS["development"] + PERIODS["validation"] + PERIODS["test"]}
FEATURE_VERSION = "nhl-pregame-ids-v1+prior-day-et-cutoff"
ELO_GRID = {"k": (2, 3, 4, 6, 8, 10, 12), "home_advantage": (0, 15, 30, 45, 60), "carry": (0.5, 0.75, 1.0), "mov": (False, True)}
GOALS_GRID = {"eta": (0.001, 0.002, 0.003, 0.005, 0.01, 0.02, 0.04), "carry": (0.5, 0.75, 1.0)}
STANDARD_LINES = {"spread": (-1.5, 1.5), "total": (5.5, 6.5)}
DECISION_PATH = Path(__file__).parent / "models" / "nhl_frozen_decision.json"


class NhlEvaluationError(RuntimeError):
    pass


@dataclass(frozen=True)
class HockeyRow(backtest.Row):
    decision: Optional[str] = None

    @property
    def result(self) -> HockeyResult:
        return HockeyResult(self.game, self.home_score, self.away_score, self.decision)


def load_rows(conn: sqlite3.Connection, period: str) -> list[HockeyRow]:
    """Final regular-season and playoff games of the seasons `period` may load (filtered in SQL), or every season for
    'live'. Requires the NHL store."""
    from .db import store_sport
    if store_sport(conn) != "nhl":
        raise NhlEvaluationError("NHL evaluation needs the NHL store")
    types = SPORT.eligible_season_types
    query = (f"SELECT g.*, d.decision FROM games g LEFT JOIN game_decisions d USING (game_id) "
             f"WHERE g.status = 'final' AND g.season_type IN ({', '.join('?' * len(types))})")
    params: tuple = types
    if period != "live":
        seasons = LOADABLE[period]
        query += f" AND g.season IN ({', '.join('?' * len(seasons))})"
        params += seasons
    rows = []
    for r in conn.execute(query + " ORDER BY g.tipoff_at, g.game_id", params):
        game = PregameGame(r["game_id"], r["season"], r["season_type"], r["tipoff_at"], r["home_team_id"],
                           r["away_team_id"], sports.is_neutral(SPORT, bool(r["neutral_site"]), r["tipoff_at"]))
        rows.append(HockeyRow(game, backtest.cutoff_for(r["tipoff_at"]), r["home_score"], r["away_score"],
                              r["result_available_at"], r["decision"]))
    return rows


def dataset_version(rows: list[HockeyRow]) -> str:
    h = hashlib.sha256(backtest.dataset_version(rows).encode())
    for r in sorted(rows, key=lambda r: r.game.game_id):
        h.update(f"{r.game.game_id}|{r.decision}\n".encode())
    return h.hexdigest()[:16]


def in_period(preds: list[dict], period: str) -> list[dict]:
    return [p for p in preds if p["season"] in PERIODS[period]]


def _goals_extra(model, game) -> dict:
    lh, la = model.rates(game)
    return {"lambda_home": lh, "lambda_away": la, "ot_home_share": model.ot_home_share() if not game.neutral_site else 0.5}


def candidate(name: str, cfg=None):
    key = SPORT.rating_key
    if name == "constant":
        return Constant()
    if name == "home_rate":
        return HomeRate()
    if name == "hockey_elo":
        return HockeyElo(cfg or HockeyEloConfig(), key)
    if name == "goals_poisson":
        return GoalsPoisson(cfg or GoalsConfig(), key)
    if name == "league_poisson":
        return GoalsPoisson(GoalsConfig(eta=0.0, carry=1.0), key)
    raise ValueError(name)


def model_version(model) -> str:
    return backtest.model_version(model)


# --- Development: per-candidate grids ---------------------------------------------------------------------------
def tune(rows: list[HockeyRow]) -> dict:
    if any(r.game.season not in LOADABLE["development"] for r in rows):
        raise NhlEvaluationError("Tuning may only read warm-up and development seasons")
    by_id = {r.game.game_id: r for r in rows}
    elo_trials = []
    for values in itertools.product(*ELO_GRID.values()):
        cfg = HockeyEloConfig(**dict(zip(ELO_GRID, values)))
        preds = in_period(backtest.run(candidate("hockey_elo", cfg), rows), "development")
        elo_trials.append({"config": asdict(cfg), "brier": metrics.brier([p["p_home"] for p in preds],
                                                                        [p["home_won"] for p in preds]), "n": len(preds)})
    # ties → MOV off, carry 1.0, smallest K (as preregistered); exact float ties only
    elo_best = min(elo_trials, key=lambda t: (t["brier"], t["config"]["mov"], -t["config"]["carry"], t["config"]["k"]))
    goals_trials = []
    for values in itertools.product(*GOALS_GRID.values()):
        cfg = GoalsConfig(**dict(zip(GOALS_GRID, values)))
        preds = in_period(backtest.run(candidate("goals_poisson", cfg), rows, extra=_goals_extra), "development")
        nlls = [regulation_nll(p["lambda_home"], p["lambda_away"], by_id[p["game_id"]].result.regulation)
                for p in preds if by_id[p["game_id"]].result.regulation is not None]
        goals_trials.append({"config": asdict(cfg), "regulation_nll": sum(nlls) / len(nlls), "n": len(nlls)})
    goals_best = min(goals_trials, key=lambda t: (t["regulation_nll"], t["config"]["eta"], -t["config"]["carry"]))
    return {"elo": {"grid": ELO_GRID, "criterion": "development Brier", "selected": elo_best["config"],
                    "trials": sorted(elo_trials, key=lambda t: t["brier"])[:20], "trial_count": len(elo_trials)},
            "goals": {"grid": GOALS_GRID, "criterion": "development regulation-score NLL", "selected": goals_best["config"],
                      "trials": sorted(goals_trials, key=lambda t: t["regulation_nll"])}}


# --- Scoring helpers --------------------------------------------------------------------------------------------
def _realised(market: str, line, row: HockeyRow) -> Optional[int]:
    side = markets.settle(market, line, status="final", home=row.home_score, away=row.away_score, decision=row.decision)
    return {"a": 0, "mid": 1, "b": 2}.get(side)


def market_scores(preds: list[dict], rows_by_id: dict) -> dict[str, list[dict]]:
    """Per-game market vectors and realised outcomes from a goals model's predictions (with lambdas)."""
    out: dict[str, list[dict]] = {}
    for p in preds:
        row = rows_by_id[p["game_id"]]
        grid = markets.poisson_grid(p["lambda_home"], p["lambda_away"], p["ot_home_share"])
        specs = [("reg3", None)] + [(m, line) for m, lines in STANDARD_LINES.items() for line in lines]
        for market, line in specs:
            real = _realised(market, line, row)
            if real is None:
                continue
            key = market if line is None else f"{market} {line:+g}" if market == "spread" else f"{market} {line:g}"
            out.setdefault(key, []).append({"game_id": p["game_id"], "cutoff_at": p["cutoff_at"],
                                            "vector": grid.market(market, line), "realised": real})
    return out


def summarize_market(items: list[dict]) -> dict:
    vecs, real = [i["vector"] for i in items], [i["realised"] for i in items]
    counts = [sum(r == k for r in real) for k in range(3)]
    mean_pred = [sum(v[k] for v in vecs) / len(vecs) for k in range(3)]
    picks = [max((0, 2), key=lambda k: v[k]) for v in vecs]
    decided = [(pk, r) for pk, r in zip(picks, real) if r != 1]
    return {"n": len(items), "brier": metrics.brier_multi(vecs, real), "log_loss": metrics.log_loss_multi(vecs, real),
            "observed_a_mid_b": counts, "mean_predicted_a_mid_b": [round(x, 4) for x in mean_pred],
            "pick_accuracy_excluding_mid": (sum(pk == r for pk, r in decided) / len(decided)) if decided else None}


def _paired_market(a: list[dict], b: list[dict]) -> dict:
    pb = {i["game_id"]: i for i in b}
    diffs, cuts = [], []
    for i in a:
        j = pb[i["game_id"]]
        diffs.append(metrics.log_loss_multi([i["vector"]], [i["realised"]]) - metrics.log_loss_multi([j["vector"]], [j["realised"]]))
        cuts.append(i["cutoff_at"])
    return backtest.block_bootstrap(diffs, cuts)


def _winner_summary(preds: list[dict]) -> dict:
    s = backtest.summarize(preds)
    s["by_season"] = backtest.by_season(preds)
    return s


# --- Validation (once) -------------------------------------------------------------------------------------------
def seal(decision: dict) -> dict:
    return backtest.seal(decision)


def evaluate_validation(conn: sqlite3.Connection, *, decision_path: Optional[Path] = None, out_dir: Optional[Path] = None,
                        log: Callable[[str], None] = print) -> dict:
    decision_path = Path(decision_path or DECISION_PATH)
    if decision_path.exists():
        raise NhlEvaluationError(f"{decision_path} exists: the validation stage has already been run and sealed")
    started = utc_now()
    dev_rows = load_rows(conn, "development")
    tuning = tune(dev_rows)
    rows = load_rows(conn, "validation")
    version = dataset_version(rows)
    elo_cfg, goals_cfg = HockeyEloConfig(**tuning["elo"]["selected"]), GoalsConfig(**tuning["goals"]["selected"])
    models = {"constant": candidate("constant"), "home_rate": candidate("home_rate"),
              "hockey_elo": candidate("hockey_elo", elo_cfg), "goals_poisson": candidate("goals_poisson", goals_cfg),
              "league_poisson": candidate("league_poisson")}
    preds = {name: in_period(backtest.run(m, rows, extra=_goals_extra if isinstance(m, GoalsPoisson) else None), "validation")
             for name, m in models.items()}
    by_id = {r.game.game_id: r for r in rows}
    goals_vs_elo = backtest.paired_bootstrap(preds["goals_poisson"], preds["hockey_elo"])
    winner = "goals_poisson" if goals_vs_elo["ci95"][1] < 0 else "hockey_elo"
    goal_markets = market_scores(preds["goals_poisson"], by_id)
    league_markets = market_scores(preds["league_poisson"], by_id)
    report = {
        "sport": "nhl", "stage": "validation", "generated_at": started, "seasons": PERIODS["validation"],
        "development_seasons": PERIODS["development"], "dataset_version": version,
        "development_dataset_version": dataset_version(dev_rows), "feature_version": FEATURE_VERSION,
        "cutoff_rule": "00:00 America/New_York on game date; results available at or before the cutoff "
                       "(historical availability assumed tipoff+4h)",
        "tuning": tuning,
        "winner_models": {n: {"model_version": model_version(m), **_winner_summary(preds[n])} for n, m in models.items()},
        "goals_vs_elo_brier": goals_vs_elo, "elo_vs_home_rate_brier": backtest.paired_bootstrap(preds["hockey_elo"], preds["home_rate"]),
        "winner_selection": {"rule": "goals_poisson only if the paired 95% upper bound of (goals - Elo) Brier < 0; "
                                     "otherwise hockey_elo", "selected": winner},
        "markets": {k: {"goals_poisson": summarize_market(goal_markets[k]),
                        "league_poisson": summarize_market(league_markets[k]),
                        "goals_minus_league_log_loss": _paired_market(goal_markets[k], league_markets[k])}
                    for k in goal_markets},
        "market_comparison": {"matched_games": 0, "note": "No historical NHL odds exist in the store; no market comparison."},
    }
    decision = seal({"sport": "nhl", "winner_model": winner, "hockey_elo_config": asdict(elo_cfg),
                     "goals_config": asdict(goals_cfg), "market_model": "goals_poisson",
                     "validation_dataset_version": version, "validation_seasons": list(PERIODS["validation"]),
                     "selected_on": "validation", "frozen_at": started, "feature_version": FEATURE_VERSION})
    out_dir = _write(report, preds, models, out_dir, f"nhl-validation-{started.replace(':', '')}", log)
    decision["validation_report"] = str(Path(out_dir).relative_to(config.REPO_ROOT)) \
        if Path(out_dir).is_relative_to(config.REPO_ROOT) else str(out_dir)
    decision = seal({k: v for k, v in decision.items() if k != "sha256"})
    Path(decision_path).write_text(json.dumps(decision, indent=2) + "\n")
    report["decision"] = decision
    return report


_CHECKED: dict = {}


def _decisions_digest(conn: sqlite3.Connection) -> str:
    """Every stored decision value: a corrected decision (OT -> SO) changes no game row but must invalidate the cache."""
    h = hashlib.sha256()
    for r in conn.execute("SELECT game_id, decision, periods FROM game_decisions ORDER BY game_id"):
        h.update(f"{r[0]}|{r[1]}|{r[2]}\n".encode())
    return h.hexdigest()


def load_decision(conn: Optional[sqlite3.Connection] = None, path: Optional[Path] = None, check_data: bool = True) -> dict:
    path = Path(path or DECISION_PATH)
    if not path.is_file():
        raise NhlEvaluationError(f"No sealed NHL decision at {path}; run the validation stage first")
    d = json.loads(path.read_text())
    if d.get("sha256") != seal(d)["sha256"]:
        raise NhlEvaluationError(f"{path} was modified after sealing")
    if check_data and conn is not None:
        # The check reloads every validation game; it is cached per store state (file, game count, latest update), so a
        # correction ingested into the store is checked again on the next call.
        state = (str(path), d["sha256"], conn.execute("PRAGMA database_list").fetchone()["file"],
                 *conn.execute("SELECT COUNT(*), MAX(updated_at) FROM games").fetchone(),
                 _decisions_digest(conn))
        current = _CHECKED.get(state) or dataset_version(load_rows(conn, "validation"))
        _CHECKED.clear()
        _CHECKED[state] = current
        if current != d["validation_dataset_version"]:
            raise NhlEvaluationError(f"NHL validation data changed since sealing ({d['validation_dataset_version']} -> {current})")
    return d


def models_from_decision(d: dict) -> dict:
    return {"hockey_elo": candidate("hockey_elo", HockeyEloConfig(**d["hockey_elo_config"])),
            "goals_poisson": candidate("goals_poisson", GoalsConfig(**d["goals_config"]))}


# --- Test (once, logged) -------------------------------------------------------------------------------------------
def test_access_path() -> Path:
    return config.data_dir() / "eval" / "test_access_nhl.jsonl"


def evaluate_test(conn: sqlite3.Connection, *, decision_path: Optional[Path] = None, out_dir: Optional[Path] = None,
                  log: Callable[[str], None] = print) -> dict:
    decision = load_decision(conn, decision_path)
    access = test_access_path()
    if access.exists() and access.read_text().strip():
        raise NhlEvaluationError(f"The NHL test period was already accessed ({access}); it is not rerun")
    started = utc_now()
    access.parent.mkdir(parents=True, exist_ok=True)
    with access.open("a") as f:
        f.write(json.dumps({"at": started, "decision_sha256": decision["sha256"],
                            "validation_dataset_version": decision["validation_dataset_version"]}) + "\n")
    rows = load_rows(conn, "test")
    version = dataset_version(rows)
    chosen = models_from_decision(decision)
    models = {"constant": candidate("constant"), "home_rate": candidate("home_rate"), **chosen,
              "league_poisson": candidate("league_poisson")}
    preds = {name: in_period(backtest.run(m, rows, extra=_goals_extra if isinstance(m, GoalsPoisson) else None), "test")
             for name, m in models.items()}
    by_id = {r.game.game_id: r for r in rows}
    goal_markets, league_markets = market_scores(preds["goals_poisson"], by_id), market_scores(preds["league_poisson"], by_id)
    report = {
        "sport": "nhl", "stage": "test", "generated_at": started, "seasons": PERIODS["test"], "dataset_version": version,
        "feature_version": FEATURE_VERSION, "decision": decision,
        "winner_models": {n: {"model_version": model_version(m), **_winner_summary(preds[n])} for n, m in models.items()},
        "primary_winner_model": decision["winner_model"],
        "descriptive": {"goals_minus_elo_brier": backtest.paired_bootstrap(preds["goals_poisson"], preds["hockey_elo"]),
                        "elo_minus_home_rate_brier": backtest.paired_bootstrap(preds["hockey_elo"], preds["home_rate"]),
                        "note": "Descriptive only; the choice was sealed on validation."},
        "markets": {k: {"goals_poisson": summarize_market(goal_markets[k]),
                        "league_poisson": summarize_market(league_markets[k]),
                        "goals_minus_league_log_loss": _paired_market(goal_markets[k], league_markets[k])}
                    for k in goal_markets},
        "market_comparison": {"matched_games": 0, "note": "No historical NHL odds exist in the store; no market comparison."},
    }
    _write(report, preds, models, out_dir, f"nhl-test-{started.replace(':', '')}", log)
    return report


def _write(report, preds, models, out_dir, name, log) -> Path:
    out_dir = Path(out_dir or config.data_dir() / "reports" / name)
    out_dir.mkdir(parents=True, exist_ok=True)
    report["output_dir"] = str(out_dir)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, default=str))
    with (out_dir / "predictions.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["model_version", "game_id", "season", "season_type", "cutoff_at", "p_home", "home_won",
                    "lambda_home", "lambda_away"])
        for n, m in models.items():
            for p in preds[n]:
                w.writerow([model_version(m), p["game_id"], p["season"], p["season_type"], p["cutoff_at"],
                            f"{p['p_home']:.6f}", int(p["home_won"]), p.get("lambda_home", ""), p.get("lambda_away", "")])
    log(f"Report written to {out_dir}")
    return out_dir


def record_test_report(conn: sqlite3.Connection, report_dir: Path) -> dict:
    """Appends the one-time test's per-game winner predictions (from its saved predictions.csv) to the ledger as
    run_type 'backtest', so the app can show them. Nothing is re-evaluated; retries are idempotent."""
    from .ledger import Forecast, record
    report = json.loads((Path(report_dir) / "report.json").read_text())
    if report.get("stage") != "test" or report.get("sport") != "nhl":
        raise NhlEvaluationError("Only the NHL test report can be recorded")
    keep = {m["model_version"] for n, m in report["winner_models"].items() if n in ("hockey_elo", "home_rate", "constant")}
    created = existing = 0
    with (Path(report_dir) / "predictions.csv").open() as f:
        for row in csv.DictReader(f):
            if row["model_version"] not in keep:
                continue
            _, new = record(conn, Forecast(row["game_id"], "backtest", row["model_version"], report["dataset_version"],
                                                    report["feature_version"], row["cutoff_at"], report["generated_at"],
                                                    float(row["p_home"])))
            created += new
            existing += not new
    return {"created": created, "already_recorded": existing, "models": sorted(keep)}


# --- Agreement research (EVALUATION_PLAN "NHL agreement research"; development seasons only) ----------------------
AGREEMENT_THRESHOLDS = (0.55, 0.60, 0.65)


def _wilson(wins: int, n: int, z: float = 1.959964) -> Optional[list]:
    if n == 0:
        return None
    p = wins / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return [c - h, c + h]


def _week(cutoff: str) -> tuple:
    from datetime import datetime
    iso = datetime.strptime(cutoff, "%Y-%m-%dT%H:%M:%SZ").isocalendar()
    return iso[0], iso[1]


def _subset_diff_bootstrap(a: list[dict], b: list[dict], key_a: str, key_b: str, seed: int = backtest.BOOTSTRAP_SEED,
                           samples: int = backtest.BOOTSTRAP_SAMPLES) -> dict:
    """Accuracy(a) − accuracy(b) for two subsets of possibly different games, resampling the same ISO weeks for both."""
    import random
    weeks: dict[tuple, list] = {}
    for rows, key, slot in ((a, key_a, 0), (b, key_b, 1)):
        for r in rows:
            w = weeks.setdefault(_week(r["cutoff_at"]), [[0, 0], [0, 0]])
            w[slot][0] += r[key]
            w[slot][1] += 1
    blocks = list(weeks.values())
    rng, stats = random.Random(seed), []
    for _ in range(samples):
        pick = [blocks[rng.randrange(len(blocks))] for _ in blocks]
        ca, na = sum(x[0][0] for x in pick), sum(x[0][1] for x in pick)
        cb, nb = sum(x[1][0] for x in pick), sum(x[1][1] for x in pick)
        if na and nb:
            stats.append(ca / na - cb / nb)
    stats.sort()
    diff = sum(r[key_a] for r in a) / len(a) - sum(r[key_b] for r in b) / len(b)
    return {"diff": diff, "ci95": [stats[int(0.025 * len(stats))], stats[int(0.975 * len(stats)) - 1]],
            "weeks": len(blocks), "seed": seed, "samples": samples}


def _score(rows: list[dict], pkey: str) -> dict:
    probs, ys = [r[pkey] for r in rows], [r["home_won"] for r in rows]
    wins = sum((p >= 0.5) == y for p, y in zip(probs, ys))
    return {"n": len(rows), "wins": wins, "losses": len(rows) - wins, "accuracy": wins / len(rows) if rows else None,
            "accuracy_ci95": _wilson(wins, len(rows)), "brier": metrics.brier(probs, ys),
            "log_loss": metrics.log_loss(probs, ys)}


def agreement_research(conn: sqlite3.Connection, decision_path: Optional[Path] = None, out_dir: Optional[Path] = None,
                       log: Callable[[str], None] = print) -> dict:
    d = load_decision(conn, decision_path)   # sealed configurations; the decision itself is not changed
    rows = load_rows(conn, "development")
    if any(r.game.season not in LOADABLE["development"] for r in rows):
        raise NhlEvaluationError("Agreement research reads development seasons only")
    models = models_from_decision(d)
    elo = {p["game_id"]: p for p in in_period(backtest.run(models["hockey_elo"], rows), "development")}
    goals = {p["game_id"]: p for p in in_period(backtest.run(models["goals_poisson"], rows), "development")}
    games = []
    for gid, e in sorted(elo.items(), key=lambda x: (x[1]["cutoff_at"], x[0])):
        pe, pg = e["p_home"], goals[gid]["p_home"]
        pa = (pe + pg) / 2
        y = e["home_won"]
        games.append({"game_id": gid, "cutoff_at": e["cutoff_at"], "season": e["season"], "home_won": y,
                      "p_elo": pe, "p_goals": pg, "p_avg": pa,
                      "c_elo": max(pe, 1 - pe), "c_goals": max(pg, 1 - pg), "c_avg": max(pa, 1 - pa),
                      "ok_elo": int((pe >= 0.5) == y), "ok_goals": int((pg >= 0.5) == y), "ok_avg": int((pa >= 0.5) == y),
                      "agree": pe != 0.5 and pg != 0.5 and (pe > 0.5) == (pg > 0.5)})
    n = len(games)

    def as_pred(rows, key):
        return [{"game_id": g["game_id"], "cutoff_at": g["cutoff_at"], "p_home": g[key], "home_won": g["home_won"]} for g in rows]
    report = {"sport": "nhl", "stage": "development agreement research", "generated_at": utc_now(),
              "seasons": PERIODS["development"], "dataset_version": dataset_version(rows), "decision_sha256": d["sha256"],
              "games": n, "abstentions": sum(g["p_elo"] == 0.5 or g["p_goals"] == 0.5 for g in games),
              "caveat": "Configurations were tuned on these seasons; absolute figures are optimistic.",
              "all_games": {k: _score(games, f"p_{k}") for k in ("elo", "goals", "avg")},
              "average_vs_elo_brier": backtest.paired_bootstrap(as_pred(games, "p_avg"), as_pred(games, "p_elo")),
              "average_vs_goals_brier": backtest.paired_bootstrap(as_pred(games, "p_avg"), as_pred(games, "p_goals")),
              "goals_vs_elo_brier": backtest.paired_bootstrap(as_pred(games, "p_goals"), as_pred(games, "p_elo")),
              "average_vs_elo_accuracy": backtest.paired_accuracy_bootstrap(as_pred(games, "p_avg"), as_pred(games, "p_elo"))}
    disagree = [g for g in games if not g["agree"]]
    report["disagreement"] = {"n": len(disagree), "coverage": len(disagree) / n,
                              "elo": _score(disagree, "p_elo"), "goals": _score(disagree, "p_goals"),
                              "avg": _score(disagree, "p_avg"),
                              "home_win_rate": sum(g["home_won"] for g in disagree) / len(disagree) if disagree else None}
    subsets = {"unanimous": [g for g in games if g["agree"]]}
    for t in AGREEMENT_THRESHOLDS:
        subsets[f"unanimous_avg_conf_{int(t * 100)}"] = [g for g in games if g["agree"] and g["c_avg"] >= t - 1e-12]
    out = {}
    for name, sub in subsets.items():
        k = len(sub)
        entry = {"n": k, "coverage": k / n,
                 "selected_probability": "p_avg (agreement selects games; it does not make a probability)",
                 "method": {**_score(sub, "p_avg")},
                 "base_models_on_this_subset": {"elo": _score(sub, "p_elo"), "goals": _score(sub, "p_goals")},
                 "matched_coverage_filters": {}}
        for key in ("elo", "goals", "avg"):
            top = sorted(games, key=lambda g: (-g[f"c_{key}"], g["game_id"]))[:k]
            entry["matched_coverage_filters"][key] = {
                **{x: v for x, v in _score(top, f"p_{key}").items()},
                "subset_minus_filter_accuracy": _subset_diff_bootstrap(sub, top, "ok_avg", f"ok_{key}") if k else None}
        out[name] = entry
    report["subsets"] = out
    primary = {name: e["matched_coverage_filters"]["elo"]["subset_minus_filter_accuracy"] for name, e in out.items()}
    promising = [name for name, b in primary.items() if b and b["ci95"][0] > 0]
    avg_better = report["average_vs_elo_brier"]["ci95"][1] < 0 and report["average_vs_goals_brier"]["ci95"][1] < 0
    report["decision"] = {"rule": "EVALUATION_PLAN 'NHL agreement research'",
                          "average_brier_better_than_both": avg_better, "promising_agreement_subsets": promising,
                          "promising": bool(promising or avg_better)}
    out_dir = Path(out_dir or config.data_dir() / "reports" / f"nhl-agreement-{report['generated_at'].replace(':', '')}")
    out_dir.mkdir(parents=True, exist_ok=True)
    report["output_dir"] = str(out_dir)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, default=str))
    log(f"Report written to {out_dir}")
    return report
