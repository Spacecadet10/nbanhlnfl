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
from .models.efficiency import BoxEvent, BoxLine, EfficiencyMargin
from .models.elo import Elo, EloConfig
from .models.logistic import LogisticElo, LogisticV2

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
    return [Row(_pregame(r), cutoff_for(r["tipoff_at"]), r["home_score"], r["away_score"], r["result_available_at"])
            for r in conn.execute(query, params)]


def _pregame(r: sqlite3.Row) -> PregameGame:
    neutral = bool(r["neutral_site"]) or BUBBLE[0] <= r["tipoff_at"] < BUBBLE[1]
    return PregameGame(r["game_id"], r["season"], r["season_type"], r["tipoff_at"], r["home_team_id"],
                       r["away_team_id"], neutral)


def load_box_events(conn: sqlite3.Connection, period: str) -> list[BoxEvent]:
    """Every possession observation (both teams from the same retrieval) for the games `period` may load, with the same
    season isolation as load_rows. Delivered to models by `run` only once available at a cutoff."""
    seasons = None if period == "live" else LOADABLE[period]
    season_sql = "" if seasons is None else f"AND g.season IN ({', '.join('?' * len(seasons))})"
    query = f"""
        SELECT g.*, h.fetch_id, h.available_at AS box_available_at, h.observed_at AS box_observed_at,
               h.points AS h_points, h.fga AS h_fga, h.fta AS h_fta, h.oreb AS h_oreb, h.tov AS h_tov,
               a.points AS a_points, a.fga AS a_fga, a.fta AS a_fta, a.oreb AS a_oreb, a.tov AS a_tov
        FROM games g
        JOIN team_possession_observations h ON h.game_id = g.game_id AND h.team_id = g.home_team_id
        JOIN team_possession_observations a ON a.game_id = g.game_id AND a.team_id = g.away_team_id
                                           AND a.fetch_id = h.fetch_id
        WHERE g.status = 'final' AND g.season_type IN ({', '.join('?' * len(SEASON_TYPES))}) {season_sql}
        ORDER BY h.available_at, h.observed_at, h.fetch_id, g.game_id"""
    events = []
    for r in conn.execute(query, (*SEASON_TYPES, *(seasons or ()))):
        line = lambda p: BoxLine(r[f"{p}_points"], r[f"{p}_fga"], r[f"{p}_fta"], r[f"{p}_oreb"], r[f"{p}_tov"])  # noqa: E731
        events.append(BoxEvent(_pregame(r), r["box_available_at"], r["box_observed_at"],
                               r["fetch_id"], line("h"), line("a")))
    return events


def box_version(events: Iterable[BoxEvent]) -> str:
    """Fingerprint of the possession inputs a run used (recorded next to dataset_version)."""
    h = hashlib.sha256()
    for e in sorted(events, key=lambda e: (e.game.game_id, e.fetch_id)):
        h.update(repr((e.game.game_id, e.fetch_id, e.available_at, e.observed_at, e.home, e.away)).encode())
    return h.hexdigest()[:16]


def dataset_version(rows: Iterable[Row]) -> str:
    h = hashlib.sha256()
    for r in sorted(rows, key=lambda r: r.game.game_id):
        g = r.game
        h.update("|".join(map(str, (g.game_id, g.season, g.season_type, g.tipoff_at, g.home_team_id, g.away_team_id,
                                    g.neutral_site, r.home_score, r.away_score, r.result_available_at))).encode())
        h.update(b"\n")
    return h.hexdigest()[:16]


def run(model: Model, rows: list[Row], box_events: Optional[list[BoxEvent]] = None,
        deliver_until: Optional[str] = None, extra: Optional[Callable[[Model, PregameGame], dict]] = None) -> list[dict]:
    """Walk forward once over all rows; returns one prediction per row.

    With `box_events`, each possession observation is passed to `model.update_box` only once BOTH its own
    available_at and its game's result availability are at or before the current cutoff (effective availability =
    the later of the two), so efficiency inputs never precede the completed result. Models without update_box ignore
    them. A box score available at or before its own game's cutoff, or one with no matching row in `rows`, is a
    LeakageError.

    `deliver_until` (live forecasting only): after the walk, results and boxes whose (effective) availability is at
    or before that time are delivered too, leaving the model in its state as of that moment.
    `extra(model, game)`, if given, is called right after each prediction (same model state) and its fields are added
    to that game's output, e.g. a goals model's expected goals."""
    results = sorted(rows, key=lambda r: (r.result_available_at, r.game.tipoff_at, r.game.game_id))
    games = sorted(rows, key=lambda r: (r.cutoff_at, r.game.tipoff_at, r.game.game_id))
    by_id = {r.game.game_id: r for r in rows}
    boxes = []  # (effective availability, observed_at, fetch_id, game_id, event)
    for e in box_events or []:
        row = by_id.get(e.game.game_id)
        if row is None:
            raise LeakageError(f"{e.game.game_id}: box score without a matching eligible result")
        if e.available_at <= row.cutoff_at:
            raise LeakageError(f"{e.game.game_id}: box score available before its own cutoff")
        boxes.append((max(e.available_at, row.result_available_at), e.observed_at, e.fetch_id, e.game.game_id, e))
    boxes.sort(key=lambda b: b[:4])  # by effective availability, so a deferred event never blocks an eligible one
    deliver_box = getattr(model, "update_box", None)
    out, j, k = [], 0, 0
    for row in games:
        if row.result_available_at <= row.cutoff_at:
            raise LeakageError(f"{row.game.game_id}: result available before its own cutoff")
        while j < len(results) and results[j].result_available_at <= row.cutoff_at:
            model.update(results[j].result)
            j += 1
        while k < len(boxes) and boxes[k][0] <= row.cutoff_at:
            if deliver_box:
                deliver_box(boxes[k][4])
            k += 1
        p = model.predict(row.game)
        out.append({"game_id": row.game.game_id, "season": row.game.season, "season_type": row.game.season_type,
                    "tipoff_at": row.game.tipoff_at, "cutoff_at": row.cutoff_at,
                    "home_team_id": row.game.home_team_id, "away_team_id": row.game.away_team_id,
                    "neutral_site": row.game.neutral_site, "p_home": p,
                    "home_won": row.home_score > row.away_score})
        if extra is not None:
            out[-1].update(extra(model, row.game))
    if deliver_until is not None:
        while j < len(results) and results[j].result_available_at <= deliver_until:
            model.update(results[j].result)
            j += 1
        while k < len(boxes) and boxes[k][0] <= deliver_until:
            if deliver_box:
                deliver_box(boxes[k][4])
            k += 1
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


def paired_accuracy_bootstrap(a: list[dict], b: list[dict], seed: int = BOOTSTRAP_SEED,
                              samples: int = BOOTSTRAP_SAMPLES) -> dict:
    """Winner-accuracy difference (a - b) on the same games, with the counts of games only one model got right and a
    week-block bootstrap interval. Picks follow metrics.accuracy (home when p >= 0.5). Positive favours a."""
    pb = {p["game_id"]: p for p in b}
    blocks: dict[tuple, list[int]] = {}
    only_a = only_b = 0
    for p in a:
        q = pb[p["game_id"]]
        ra, rb = (p["p_home"] >= 0.5) == p["home_won"], (q["p_home"] >= 0.5) == q["home_won"]
        only_a, only_b = only_a + (ra and not rb), only_b + (rb and not ra)
        iso = datetime.strptime(p["cutoff_at"], "%Y-%m-%dT%H:%M:%SZ").isocalendar()
        blocks.setdefault((iso[0], iso[1]), []).append(int(ra) - int(rb))
    weeks = list(blocks.values())
    rng, stats = random.Random(seed), []
    for _ in range(samples):
        pick = [weeks[rng.randrange(len(weeks))] for _ in weeks]
        stats.append(sum(sum(w) for w in pick) / sum(len(w) for w in pick))
    stats.sort()
    return {"diff": (only_a - only_b) / len(a), "only_first_right": only_a, "only_second_right": only_b,
            "ci95": [stats[int(0.025 * samples)], stats[int(0.975 * samples) - 1]], "games": len(a),
            "weeks": len(weeks), "seed": seed, "samples": samples}


def block_bootstrap(values: list[float], cutoffs: list[str], seed: int = BOOTSTRAP_SEED,
                    samples: int = BOOTSTRAP_SAMPLES) -> dict:
    """Mean of per-game values (e.g. loss differences) with the same seeded ISO-week block bootstrap as
    paired_bootstrap, for any market or loss."""
    blocks: dict[tuple, list[float]] = {}
    for v, c in zip(values, cutoffs):
        iso = datetime.strptime(c, "%Y-%m-%dT%H:%M:%SZ").isocalendar()
        blocks.setdefault((iso[0], iso[1]), []).append(v)
    weeks = list(blocks.values())
    rng, stats = random.Random(seed), []
    for _ in range(samples):
        pick = [weeks[rng.randrange(len(weeks))] for _ in weeks]
        stats.append(sum(sum(w) for w in pick) / sum(len(w) for w in pick))
    stats.sort()
    return {"mean": sum(values) / len(values), "ci95": [stats[int(0.025 * samples)], stats[int(0.975 * samples) - 1]],
            "weeks": len(weeks), "games": len(values), "seed": seed, "samples": samples}


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


# Prospective-candidate screening (docs/EVALUATION_PLAN.md "Prospective candidates"). Development only: it decides
# which candidates enter the 2026-27 shadow season and is not evidence of improvement. It never touches the frozen
# decision, the ledger or the test seasons.
SCREEN_CANDIDATES = ("efficiency_margin", "logistic", "logistic_v2")
SCREEN_UPPER_BOUND = 0.001      # paired 95% upper bound on Brier(candidate − Elo) must be below this
SCREEN_CAP = 2
ENSEMBLE_MAX_CORRELATION = 0.95
SCREEN_FEATURE_VERSION = FEATURE_VERSION + "+possession-obs-v1"


def screening_models(elo_cfg: EloConfig) -> list:
    return [Constant(), HomeRate(), Elo(elo_cfg), EfficiencyMargin(), LogisticElo(elo_cfg), LogisticV2(elo_cfg)]


def _loss_correlation(a: list[dict], b: list[dict]) -> float:
    """Pearson correlation of per-game Brier losses on the same games."""
    pb = {p["game_id"]: p for p in b}
    xs = [(p["p_home"] - p["home_won"]) ** 2 for p in a]
    ys = [(pb[p["game_id"]]["p_home"] - p["home_won"]) ** 2 for p in a]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    var = (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5
    return cov / var if var else 1.0


def screen_candidates(preds: dict[str, list[dict]]) -> dict:
    """Preregistered screen: Brier point estimate below Elo's AND paired 95% upper bound on (candidate − Elo) below
    SCREEN_UPPER_BOUND; at most SCREEN_CAP go to shadow, by lowest validation Brier."""
    elo = summarize(preds["elo"])
    comparisons = {}
    for name in SCREEN_CANDIDATES:
        boot = paired_bootstrap(preds[name], preds["elo"])
        s = summarize(preds[name])
        comparisons[name] = {"brier": s["brier"], "log_loss": s["log_loss"], "accuracy": s["accuracy"],
                             "diff_vs_elo": boot["mean_diff"], "ci95": boot["ci95"],
                             "passed": s["brier"] < elo["brier"] and boot["ci95"][1] < SCREEN_UPPER_BOUND}
    passed = sorted((n for n, c in comparisons.items() if c["passed"]), key=lambda n: comparisons[n]["brier"])
    correlations = {f"{a}|{b}": _loss_correlation(preds[a], preds[b])
                    for a, b in itertools.combinations(SCREEN_CANDIDATES, 2)}
    eligible_pairs = [k for k, v in correlations.items()
                      if v < ENSEMBLE_MAX_CORRELATION and all(n in passed for n in k.split("|"))]
    return {"rule": f"Brier below Elo's and paired 95% upper bound of (candidate - Elo) Brier < {SCREEN_UPPER_BOUND}; "
                    f"at most {SCREEN_CAP} by lowest validation Brier",
            "elo": {"brier": elo["brier"], "log_loss": elo["log_loss"], "accuracy": elo["accuracy"]},
            "comparisons": comparisons, "passed": passed, "shadow_shortlist": passed[:SCREEN_CAP],
            "loss_correlations": correlations,
            "ensemble_precondition": {"rule": f"at least 2 passed and per-game Brier loss correlation < "
                                              f"{ENSEMBLE_MAX_CORRELATION}", "eligible_pairs": eligible_pairs,
                                      "met": bool(eligible_pairs)}}


def evaluate_screening(conn: sqlite3.Connection, *, decision_path: Optional[Path] = None,
                       out_dir: Optional[Path] = None, log: Callable[[str], None] = print) -> dict:
    """Runs the fixed candidate list on warm-up + validation seasons with the frozen Elo configuration (no tuning).
    Writes a report and per-game predictions; records nothing in the ledger and logs no test access (none occurs)."""
    _, elo_cfg = load_frozen_decision(conn, decision_path)  # reads validation-loadable rows only
    rows = load_rows(conn, "validation")
    events = load_box_events(conn, "validation")
    outside = {r.game.season for r in rows} | {e.game.season for e in events}
    if outside - set(LOADABLE["validation"]):
        raise ValueError(f"screening received non-validation seasons {sorted(outside - set(LOADABLE['validation']))}")
    version, started = dataset_version(rows), utc_now()
    models = screening_models(elo_cfg)
    preds = {m.name: in_period(run(m, rows, events), "validation") for m in models}
    report = _base_report("validation", started, version, models, preds)
    report.update({"kind": "screening", "feature_version": SCREEN_FEATURE_VERSION, "box_version": box_version(events),
                   "box_events": len(events), "elo_config": Elo(elo_cfg).config(),
                   "screening": screen_candidates(preds),
                   "fitted": {m.name: {"sigma": m.sigma, "first_fit_season": m.first_fit_season}
                              for m in models if isinstance(m, EfficiencyMargin)}})
    out_dir = out_dir or config.data_dir() / "reports" / f"screening-{started.replace(':', '')}"
    return _finish(conn, report, models, preds, version, started, False, out_dir, log)


def evaluate_research_gbt(conn: sqlite3.Connection, *, decision_path: Optional[Path] = None,
                          out_dir: Optional[Path] = None, log: Callable[[str], None] = print) -> dict:
    """Research round 2 (docs/EVALUATION_PLAN.md): one gradient-boosted tree candidate against Elo and logistic_v2
    on identical validation games. Warm-up + validation seasons only, frozen Elo configuration, no tuning, no ledger
    rows, no test access. Descriptive: nothing is selected, sealed or promoted here."""
    import sklearn
    from .models.gbt import GradientBoostedTrees   # research-only dependency, imported here and nowhere else
    _, elo_cfg = load_frozen_decision(conn, decision_path)   # reads validation-loadable rows only
    rows, events = load_rows(conn, "validation"), load_box_events(conn, "validation")
    outside = ({r.game.season for r in rows} | {e.game.season for e in events}) - set(LOADABLE["validation"])
    if outside:
        raise ValueError(f"research evaluation received non-validation seasons {sorted(outside)}")
    version, started = dataset_version(rows), utc_now()
    models = [Constant(), HomeRate(), Elo(elo_cfg), LogisticV2(elo_cfg), GradientBoostedTrees(elo_cfg)]
    preds = {m.name: in_period(run(m, rows, events), "validation") for m in models}
    gbt = models[-1]
    comparisons = {}
    for ref in ("elo", "logistic_v2"):
        brier = paired_bootstrap(preds[gbt.name], preds[ref])
        comparisons[ref] = {"brier_diff": {k: brier[k] for k in ("mean_diff", "ci95", "weeks", "games")},
                            "accuracy_diff": paired_accuracy_bootstrap(preds[gbt.name], preds[ref])}
    s, e = summarize(preds[gbt.name]), summarize(preds["elo"])
    report = _base_report("validation", started, version, models, preds)
    report.update({
        "kind": "research", "candidate": gbt.name, "feature_version": SCREEN_FEATURE_VERSION,
        "box_version": box_version(events), "box_events": len(events), "elo_config": Elo(elo_cfg).config(),
        "sklearn_version": sklearn.__version__, "candidate_config": gbt.config(), "fits": gbt.fits,
        "comparisons": comparisons,
        "step4_screen_against_elo": {
            "rule": f"Brier below Elo's and paired 95% upper bound of (candidate - Elo) Brier < {SCREEN_UPPER_BOUND}",
            "would_pass": s["brier"] < e["brier"] and comparisons["elo"]["brier_diff"]["ci95"][1] < SCREEN_UPPER_BOUND},
        "note": "Development research only. Validation seasons were already used to tune Elo and screen three "
                "candidates; this result cannot enter the sealed 2026-27 shadow experiment."})
    out_dir = out_dir or config.data_dir() / "reports" / f"research-{gbt.name}-{started.replace(':', '')}"
    return _finish(conn, report, models, preds, version, started, False, out_dir, log)


def evaluate_research_stacking(conn: sqlite3.Connection, *, decision_path: Optional[Path] = None,
                               out_dir: Optional[Path] = None, log: Callable[[str], None] = print) -> dict:
    """Research round 3 (docs/EVALUATION_PLAN.md): two learned stackers over the five walk-forward base models,
    against Elo, logistic_v2 and the equal average on identical validation games, plus accuracy at fixed coverage.
    Warm-up + validation seasons only, frozen Elo configuration, no ledger rows, no test access. Descriptive."""
    import sklearn
    from .models import stacking
    from .models.gbt import GradientBoostedTrees   # research-only dependency
    _, elo_cfg = load_frozen_decision(conn, decision_path)   # reads validation-loadable rows only
    rows, events = load_rows(conn, "validation"), load_box_events(conn, "validation")
    outside = ({r.game.season for r in rows} | {e.game.season for e in events}) - set(LOADABLE["validation"])
    if outside:
        raise ValueError(f"research evaluation received non-validation seasons {sorted(outside)}")
    version, started = dataset_version(rows), utc_now()
    bases = [Elo(elo_cfg), EfficiencyMargin(), LogisticElo(elo_cfg), LogisticV2(elo_cfg), GradientBoostedTrees(elo_cfg)]
    if tuple(m.name for m in bases) != stacking.BASE_MODELS:
        raise ValueError("base models do not match the registered list")
    base = {m.name: {p["game_id"]: p for p in run(m, rows, events)} for m in bases}
    first_oos = PERIODS["warmup"][1]
    not_ready = {m.name: m.first_fit_season for m in bases[1:] if m.first_fit_season != first_oos}
    if not_ready:
        raise ValueError(f"base models were not first fitted in season {first_oos}: {not_ready}")
    v2 = bases[3]
    context = {gid: f for _, f, _, gid in v2.examples}
    context.update({gid: f for gid, (_, f) in v2.pending.items()})
    available = {r.game.game_id: r.result_available_at for r in rows}
    table = []
    for gid, p in base["elo"].items():
        table.append({**{k: p[k] for k in p if k != "p_home"}, **{name: base[name][gid]["p_home"] for name in base},
                      "rest_diff": context[gid]["rest_diff"], "net_diff": context[gid]["net_diff"],
                      "result_available_at": available[gid]})
    seasons = PERIODS["validation"]
    stack_a, fits_a = stacking.walk_forward(table, stacking.features_logit, seasons, first_oos)
    stack_b, fits_b = stacking.walk_forward(table, stacking.features_conditional, seasons, first_oos)
    keep = ("game_id", "season", "season_type", "tipoff_at", "cutoff_at", "home_team_id", "away_team_id", "neutral_site",
            "home_won")
    average = [{**{k: r[k] for k in keep}, "p_home": sum(r[m] for m in stacking.BASE_MODELS) / len(stacking.BASE_MODELS)}
               for r in table if r["season"] in seasons]
    order = {r["game_id"]: i for i, r in enumerate(average)}
    preds = {name: in_period(list(base[name].values()), "validation") for name in stacking.BASE_MODELS}
    preds.update({"equal_average": average, "stack_logit": stack_a, "stack_conditional": stack_b})
    for name in preds:
        preds[name] = sorted(preds[name], key=lambda p: order[p["game_id"]])
        if len(preds[name]) != len(average):
            raise ValueError(f"{name} does not cover the same games")
    stackers, references = ("stack_logit", "stack_conditional"), ("logistic_v2", "elo", "equal_average")
    comparisons = {s: {ref: {"brier_diff": {k: v for k, v in paired_bootstrap(preds[s], preds[ref]).items()
                                            if k in ("mean_diff", "ci95", "weeks", "games")},
                             "accuracy_diff": paired_accuracy_bootstrap(preds[s], preds[ref])}
                       for ref in references} for s in stackers}
    elo_brier = summarize(preds["elo"])["brier"]
    methods = ("elo", "logistic_v2", "equal_average", *stackers)
    by_id = {name: {p["game_id"]: p for p in preds[name]} for name in preds}
    selective = {}
    for coverage in (0.5, 0.7, 0.85, 1.0):
        level = {m: stacking.accuracy_on(stacking.most_confident(preds[m], coverage)) for m in methods}
        chosen = [p["game_id"] for p in stacking.most_confident(preds["stack_conditional"], coverage)]
        level["base_models_on_stack_conditional_games"] = {
            b: stacking.accuracy_on([by_id[b][g] for g in chosen]) for b in stacking.BASE_MODELS}
        selective[str(coverage)] = level
    agree = [g for g in order if len({by_id[b][g]["p_home"] >= 0.5 for b in stacking.BASE_MODELS}) == 1]
    share = len(agree) / len(order)
    agreement = {"coverage": share, "all_five_agree": stacking.accuracy_on([by_id["logistic_v2"][g] for g in agree]),
                 "most_confident_at_same_coverage": {m: stacking.accuracy_on(stacking.most_confident(preds[m], share))
                                                     for m in methods}}
    report = {
        "kind": "research", "experiment": "stacking", "period": "validation", "seasons": seasons, "generated_at": started,
        "dataset_version": version, "feature_version": SCREEN_FEATURE_VERSION, "box_version": box_version(events),
        "elo_config": Elo(elo_cfg).config(), "sklearn_version": sklearn.__version__,
        "definition": {"base_models": stacking.BASE_MODELS, "clip": stacking.CLIP, "l2": stacking.L2,
                       "interactions": stacking.INTERACTIONS, "first_training_season": first_oos},
        "models": {name: {**summarize(preds[name]), "by_season": by_season(preds[name])} for name in preds},
        "fits": {"stack_logit": fits_a, "stack_conditional": fits_b},
        "comparisons": comparisons,
        "step4_screen_against_elo": {s: summarize(preds[s])["brier"] < elo_brier
                                     and comparisons[s]["elo"]["brier_diff"]["ci95"][1] < SCREEN_UPPER_BOUND for s in stackers},
        "selective_prediction": selective, "agreement_filter": agreement,
        "note": "Development research only. Rankings by confidence are made within the evaluated seasons. These seasons "
                "were already used to tune Elo and examine five candidates; nothing here can enter the sealed shadow experiment."}
    out_dir = out_dir or config.data_dir() / "reports" / f"research-stacking-{started.replace(':', '')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    report["output_dir"] = str(out_dir)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, default=str))
    with (out_dir / "predictions.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["model", "game_id", "season", "cutoff_at", "p_home", "home_won"])
        for name in preds:
            for p in preds[name]:
                w.writerow([name, p["game_id"], p["season"], p["cutoff_at"], f"{p['p_home']:.6f}", int(p["home_won"])])
    log(f"Report written to {out_dir}")
    return report


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
