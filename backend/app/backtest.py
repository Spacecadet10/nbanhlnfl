"""Chronological walk-forward backtester. The rules are fixed in docs/EVALUATION_PLAN.md.

For every game, results whose availability time is at or before the game's cutoff are fed to the model first,
then the model predicts from `PregameGame` alone. Model state carries through every season; only games in the
requested period are scored.
"""
import csv
import hashlib
import itertools
import json
import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional
from zoneinfo import ZoneInfo

from . import config, metrics
from .db import utc_now
from .ledger import Forecast, record
from .models.base import Model, PregameGame, Result
from .models.baselines import Constant, HomeRate
from .models.calibration import PlattCalibrated
from .models.elo import Elo, EloConfig
from .models.logistic import LogisticElo

EASTERN = ZoneInfo("America/New_York")
SEASON_TYPES = ("regular", "play_in", "postseason")
PERIODS = {"warmup": (2020, 2021), "validation": (2022, 2023, 2024), "test": (2025, 2026)}
# Seasons each evaluation may load at all. Validation never reads reserved test-season rows.
LOADABLE = {"validation": PERIODS["warmup"] + PERIODS["validation"],
            "test": PERIODS["warmup"] + PERIODS["validation"] + PERIODS["test"]}
BUBBLE = ("2020-07-30T00:00:00Z", "2020-10-13T00:00:00Z")  # Orlando bubble: treated as neutral site
FEATURE_VERSION = "pregame-ids-v1+prior-day-et-cutoff"
ELO_GRID = {"k": (10, 15, 20, 25, 30), "home_advantage": (25, 35, 50, 75, 100), "carry": (0.5, 0.75, 1.0), "mov": (False, True)}
BOOTSTRAP_SEED = 20260930
BOOTSTRAP_SAMPLES = 2000


class LeakageError(RuntimeError):
    pass


@dataclass(frozen=True)
class Row:
    game: PregameGame
    cutoff_at: str
    home_score: int
    away_score: int
    result_available_at: str

    @property
    def result(self) -> Result:
        return Result(self.game, self.home_score, self.away_score)


def cutoff_for(tipoff_at: str) -> str:
    """00:00 America/New_York on the game's Eastern calendar date, as UTC."""
    tip = datetime.strptime(tipoff_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(EASTERN)
    midnight = tip.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_rows(conn: sqlite3.Connection, period: str) -> list[Row]:
    """Loads only the seasons `period` may use (LOADABLE). Filtering happens in SQL, so rows from other seasons
    are never read, hashed, predicted, or fed to a model."""
    if period == "live":  # after the final test: live forecasting may use every season
        query = (f"SELECT * FROM games WHERE status = 'final' AND season_type IN ({', '.join('?' * len(SEASON_TYPES))}) "
                 "ORDER BY tipoff_at, game_id")
        params: tuple = SEASON_TYPES
    else:
        seasons = LOADABLE[period]
        query = (f"SELECT * FROM games WHERE status = 'final' AND season_type IN ({', '.join('?' * len(SEASON_TYPES))}) "
                 f"AND season IN ({', '.join('?' * len(seasons))}) ORDER BY tipoff_at, game_id")
        params = (*SEASON_TYPES, *seasons)
    rows = []
    for r in conn.execute(query, params):
        neutral = bool(r["neutral_site"]) or BUBBLE[0] <= r["tipoff_at"] < BUBBLE[1]
        game = PregameGame(r["game_id"], r["season"], r["season_type"], r["tipoff_at"], r["home_team_id"],
                           r["away_team_id"], neutral)
        rows.append(Row(game, cutoff_for(r["tipoff_at"]), r["home_score"], r["away_score"], r["result_available_at"]))
    return rows


def dataset_version(rows: Iterable[Row]) -> str:
    h = hashlib.sha256()
    for r in sorted(rows, key=lambda r: r.game.game_id):
        g = r.game
        h.update("|".join(map(str, (g.game_id, g.season, g.season_type, g.tipoff_at, g.home_team_id, g.away_team_id,
                                    g.neutral_site, r.home_score, r.away_score, r.result_available_at))).encode())
        h.update(b"\n")
    return h.hexdigest()[:16]


def run(model: Model, rows: list[Row]) -> list[dict]:
    """Walk forward once over all rows; returns one prediction per row."""
    results = sorted(rows, key=lambda r: (r.result_available_at, r.game.tipoff_at, r.game.game_id))
    games = sorted(rows, key=lambda r: (r.cutoff_at, r.game.tipoff_at, r.game.game_id))
    out, j = [], 0
    for row in games:
        if row.result_available_at <= row.cutoff_at:
            raise LeakageError(f"{row.game.game_id}: result available before its own cutoff")
        while j < len(results) and results[j].result_available_at <= row.cutoff_at:
            model.update(results[j].result)
            j += 1
        p = model.predict(row.game)
        out.append({"game_id": row.game.game_id, "season": row.game.season, "season_type": row.game.season_type,
                    "tipoff_at": row.game.tipoff_at, "cutoff_at": row.cutoff_at,
                    "home_team_id": row.game.home_team_id, "away_team_id": row.game.away_team_id,
                    "neutral_site": row.game.neutral_site, "p_home": p,
                    "home_won": row.home_score > row.away_score})
    return out


def in_period(preds: list[dict], period: str) -> list[dict]:
    seasons = PERIODS[period]
    return [p for p in preds if p["season"] in seasons]


def summarize(preds: list[dict]) -> dict:
    probs = [p["p_home"] for p in preds]
    ys = [p["home_won"] for p in preds]
    return {
        "n": len(preds),
        "first_game": min((p["tipoff_at"][:10] for p in preds), default=None),
        "last_game": max((p["tipoff_at"][:10] for p in preds), default=None),
        "accuracy": metrics.accuracy(probs, ys), "brier": metrics.brier(probs, ys),
        "log_loss": metrics.log_loss(probs, ys),
        "calibration": [b.model_dump() for b in metrics.calibration(probs, ys)],
    }


def by_season(preds: list[dict]) -> dict:
    out = {}
    for season, group in itertools.groupby(sorted(preds, key=lambda p: p["season"]), key=lambda p: p["season"]):
        s = summarize(list(group))
        out[season] = {k: s[k] for k in ("n", "accuracy", "brier", "log_loss")}
    return out


def paired_bootstrap(a: list[dict], b: list[dict], seed: int = BOOTSTRAP_SEED, samples: int = BOOTSTRAP_SAMPLES) -> dict:
    """Mean per-game Brier difference (a - b), with a week-block bootstrap interval. Negative favours a."""
    pb = {p["game_id"]: p for p in b}
    blocks: dict[tuple, list[float]] = {}
    for p in a:
        q = pb[p["game_id"]]
        y = float(p["home_won"])
        d = (p["p_home"] - y) ** 2 - (q["p_home"] - y) ** 2
        iso = datetime.strptime(p["cutoff_at"], "%Y-%m-%dT%H:%M:%SZ").isocalendar()
        blocks.setdefault((iso[0], iso[1]), []).append(d)
    weeks = list(blocks.values())
    all_d = [d for w in weeks for d in w]
    rng = random.Random(seed)
    stats = []
    for _ in range(samples):
        pick = [weeks[rng.randrange(len(weeks))] for _ in weeks]
        n = sum(len(w) for w in pick)
        stats.append(sum(sum(w) for w in pick) / n)
    stats.sort()
    return {"mean_diff": sum(all_d) / len(all_d), "ci95": [stats[int(0.025 * samples)], stats[int(0.975 * samples) - 1]],
            "weeks": len(weeks), "games": len(all_d), "seed": seed, "samples": samples}


def elo_grid() -> list[EloConfig]:
    keys = list(ELO_GRID)
    return [EloConfig(**dict(zip(keys, values))) for values in itertools.product(*ELO_GRID.values())]


def _tiebreak(cfg: EloConfig) -> tuple:
    return (cfg.mov, -cfg.carry, cfg.k, cfg.home_advantage)


def tune_elo(rows: list[Row]) -> tuple[EloConfig, list[dict]]:
    """Grid search scored on validation seasons only. Refuses rows outside the validation-loadable seasons."""
    outside = {r.game.season for r in rows} - set(LOADABLE["validation"])
    if outside:
        raise ValueError(f"tune_elo received rows from non-validation seasons {sorted(outside)}")
    trials = []
    for cfg in elo_grid():
        s = summarize(in_period(run(Elo(cfg), rows), "validation"))
        trials.append({"config": Elo(cfg).config(), "brier": s["brier"], "log_loss": s["log_loss"], "_cfg": cfg})
    best = min(trials, key=lambda t: (round(t["brier"], 6), _tiebreak(t["_cfg"])))
    for t in trials:
        t.pop("_cfg")
    return EloConfig(**best["config"]), trials


CANDIDATES = ("elo_platt", "logistic", "logistic_platt")


def select_candidate(preds: dict[str, list[dict]]) -> dict:
    """Preregistered rule (docs/EVALUATION_PLAN.md): a candidate replaces Elo only if the upper 95% bound of its
    paired Brier difference versus Elo is below 0; among eligible candidates, the lowest Brier wins; otherwise Elo."""
    comparisons = {}
    for name in CANDIDATES:
        boot = paired_bootstrap(preds[name], preds["elo"])
        brier = summarize(preds[name])["brier"]
        comparisons[name] = {"brier": brier, "diff_vs_elo": boot["mean_diff"], "ci95": boot["ci95"],
                             "eligible": boot["ci95"][1] < 0}
    eligible = [n for n, c in comparisons.items() if c["eligible"]]
    selected = min(eligible, key=lambda n: comparisons[n]["brier"]) if eligible else "elo"
    return {"rule": "replace Elo only if upper 95% bound of (candidate - Elo) Brier < 0; then lowest Brier",
            "elo_brier": summarize(preds["elo"])["brier"], "comparisons": comparisons, "selected": selected}


def model_version(model: Model) -> str:
    cfg = model.config()
    return model.name + ("" if not cfg else "-" + "-".join(f"{k}={v}" for k, v in sorted(cfg.items())))


def log_test_access(entry: dict, path: Optional[Path] = None) -> None:
    path = path or config.data_dir() / "eval" / "test_access.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(entry) + "\n")


FROZEN_DECISION_PATH = Path(__file__).parent / "models" / "frozen_decision.json"
SUPPORTED_PRIMARY = "elo"


class FrozenDecisionError(ValueError):
    pass


def candidate_models(elo_cfg: EloConfig) -> list:
    return [Constant(), HomeRate(), Elo(elo_cfg), PlattCalibrated(Elo(elo_cfg)), LogisticElo(elo_cfg),
            PlattCalibrated(LogisticElo(elo_cfg))]


def seal(decision: dict) -> dict:
    """Adds a SHA-256 digest of the decision's canonical content, so accidental edits are detected on load."""
    body = {k: v for k, v in decision.items() if k != "sha256"}
    return {**body, "sha256": hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()}


def decision_from_validation_report(report: dict, report_path: str) -> dict:
    """Builds the frozen decision from a validation report; nothing here reads test-season data."""
    if report.get("period") != "validation" or "candidate_selection" not in report:
        raise FrozenDecisionError("A frozen decision can only come from a validation report with candidate selection")
    sel = report["candidate_selection"]
    elo_cfg = report["elo_tuning"]["selected"] if "elo_tuning" in report else None
    if elo_cfg is None:
        raise FrozenDecisionError("Validation report has no tuned Elo configuration")
    return seal({
        "primary_model": sel["selected"], "elo_config": elo_cfg, "selected_on": "validation",
        "validation_report": report_path, "validation_dataset_version": report["dataset_version"],
        "validation_seasons": list(report["seasons"]), "selection_rule": sel["rule"],
        "validation_candidate_comparisons": sel["comparisons"], "frozen_at": report["generated_at"],
    })


def load_frozen_decision(conn: sqlite3.Connection, path: Optional[Path] = None) -> tuple[dict, EloConfig]:
    """Loads and checks the frozen validation decision. Reads only validation-loadable rows (to confirm the
    fingerprint); must be called before any test-season row is read."""
    path = Path(path or FROZEN_DECISION_PATH)
    if not path.is_file():
        raise FrozenDecisionError(f"No frozen validation decision at {path}")
    try:
        decision = json.loads(path.read_text())
        if decision.get("sha256") != seal(decision)["sha256"]:
            raise FrozenDecisionError(f"Frozen decision {path} was modified after sealing (digest mismatch)")
        if set(decision["elo_config"]) != set(EloConfig.__dataclass_fields__):
            raise FrozenDecisionError("Frozen elo_config must list every Elo parameter explicitly")
        cfg = EloConfig(**decision["elo_config"])
        required = ("primary_model", "selected_on", "validation_dataset_version", "validation_seasons")
        missing = [k for k in required if k not in decision]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise FrozenDecisionError(f"Malformed frozen decision {path}: {exc}") from exc
    if missing:
        raise FrozenDecisionError(f"Frozen decision missing {missing}")
    if decision["selected_on"] != "validation":
        raise FrozenDecisionError("Frozen decision must come from validation")
    if decision["primary_model"] != SUPPORTED_PRIMARY:
        raise FrozenDecisionError(f"Primary model {decision['primary_model']!r} is not the validation-selected Elo")
    if list(decision["validation_seasons"]) != list(PERIODS["validation"]):
        raise FrozenDecisionError("Frozen decision was made on different validation seasons")
    current = dataset_version(load_rows(conn, "validation"))
    if current != decision["validation_dataset_version"]:
        raise FrozenDecisionError(f"Validation data changed since the decision was frozen "
                                  f"({decision['validation_dataset_version']} → {current})")
    return decision, cfg


def evaluate(conn: sqlite3.Connection, period: str, elo_cfg: Optional[EloConfig] = None, *,
             decision_path: Optional[Path] = None, record_predictions: bool = False, out_dir: Optional[Path] = None,
             log: Callable[[str], None] = print) -> dict:
    if period == "validation":
        return _evaluate_validation(conn, elo_cfg, record_predictions=record_predictions, out_dir=out_dir, log=log)
    if period == "test":
        if elo_cfg is not None:
            raise FrozenDecisionError("The test period takes its configuration only from the frozen decision")
        return _evaluate_test(conn, decision_path, record_predictions=record_predictions, out_dir=out_dir, log=log)
    raise ValueError("period must be 'validation' or 'test'")


def _evaluate_validation(conn, elo_cfg, *, record_predictions, out_dir, log) -> dict:
    rows = load_rows(conn, "validation")
    version = dataset_version(rows)
    trials = None
    if elo_cfg is None:
        elo_cfg, trials = tune_elo(rows)
    started = utc_now()
    models = candidate_models(elo_cfg)
    preds = {m.name: in_period(run(m, rows), "validation") for m in models}
    report = _base_report("validation", started, version, models, preds)
    report["candidate_selection"] = select_candidate(preds)
    if trials is not None:
        report["elo_tuning"] = {"grid": ELO_GRID, "criterion": "validation Brier", "selected": Elo(elo_cfg).config(),
                                "trials": sorted(trials, key=lambda t: t["brier"])}
    return _finish(conn, report, models, preds, version, started, record_predictions, out_dir, log)


def _evaluate_test(conn, decision_path, *, record_predictions, out_dir, log) -> dict:
    # 1) Check the frozen validation decision (reads validation rows only). 2) Log the authorised access.
    # 3) Only then load test rows. Selection is never rerun here; Elo is primary regardless of test scores.
    decision, elo_cfg = load_frozen_decision(conn, decision_path)
    started = utc_now()
    log_test_access({"at": started, "decision": str(decision_path or FROZEN_DECISION_PATH),
                     "validation_dataset_version": decision["validation_dataset_version"],
                     "elo_config": Elo(elo_cfg).config()})
    rows = load_rows(conn, "test")
    version = dataset_version(rows)
    models = candidate_models(elo_cfg)
    preds = {m.name: in_period(run(m, rows), "test") for m in models}
    report = _base_report("test", started, version, models, preds)
    report["primary_model"] = {"name": SUPPORTED_PRIMARY, "model_version": model_version(Elo(elo_cfg)),
                               "config": Elo(elo_cfg).config(), "decision": decision}
    report["descriptive_comparisons"] = {
        "note": "Descriptive only. Not a selection decision; the primary model was fixed on validation.",
        "brier_diff_vs_primary": {name: {k: v for k, v in paired_bootstrap(preds[name], preds["elo"]).items()
                                         if k in ("mean_diff", "ci95", "weeks", "games")}
                                  for name in CANDIDATES},
    }
    return _finish(conn, report, models, preds, version, started, record_predictions, out_dir, log)


def _base_report(period, started, version, models, preds) -> dict:
    return {
        "period": period, "seasons": PERIODS[period], "generated_at": started, "dataset_version": version,
        "feature_version": FEATURE_VERSION, "cutoff_rule": "00:00 America/New_York on game date; results with "
        "result_available_at <= cutoff (historical availability assumed tipoff+4h)",
        "models": {m.name: {"model_version": model_version(m), **summarize(preds[m.name]),
                            "by_season": by_season(preds[m.name])} for m in models},
        "elo_vs_home_rate_brier": paired_bootstrap(preds["elo"], preds["home_rate"]),
        "market_comparison": {"matched_games": 0, "coverage": 0.0,
                              "note": "No historical odds stored; market comparison not possible."},
    }


def _finish(conn, report, models, preds, version, started, record_predictions, out_dir, log) -> dict:
    period = report["period"]
    if record_predictions:
        created = 0
        for m in models:
            for p in preds[m.name]:
                _, new = record(conn, Forecast(p["game_id"], "backtest", model_version(m), version, FEATURE_VERSION,
                                               p["cutoff_at"], started, round(p["p_home"], 9)))
                created += new
        report["ledger_rows_created"] = created
    out_dir = out_dir or config.data_dir() / "reports" / f"{period}-{started.replace(':', '')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    report["output_dir"] = str(out_dir)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, default=str))
    with (out_dir / "predictions.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["model_version", "game_id", "season", "season_type", "tipoff_at", "cutoff_at", "home_team_id",
                    "away_team_id", "neutral_site", "p_home", "home_won"])
        for m in models:
            for p in preds[m.name]:
                w.writerow([model_version(m), p["game_id"], p["season"], p["season_type"], p["tipoff_at"],
                            p["cutoff_at"], p["home_team_id"], p["away_team_id"], int(p["neutral_site"]),
                            f"{p['p_home']:.6f}", int(p["home_won"])])
    log(f"Report written to {out_dir}")
    return report
