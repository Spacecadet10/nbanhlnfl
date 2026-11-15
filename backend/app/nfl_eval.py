"""NFL winner evaluation, exactly as preregistered in docs/NFL_PLAN.md ("NFL evaluation plan"). Development 2005–2016
tunes Elo; validation 2017–2021 chooses among constant 0.5, the running home rate and the tuned Elo, then tests one Platt
calibrator; 2022–2025 are reserved: `load_rows` refuses them unless a sealed choice is supplied, and logs every access.

Reuses the shared walk-forward (`backtest.run`: a result reaches a model only when available at or before the game's
cutoff), the shared paired week-block bootstrap and metrics. NFL specifics live here: the two-hour cutoff, ties (half a
win, void for scoring), the NFL margin multiplier, and the pinned Platt protocol. Reads only a store; writes nothing to it.
"""
import hashlib
import itertools
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

from . import backtest, metrics
from .db import utc_now
from .models.base import PregameGame, Result
from .models.baselines import Constant, HomeRate
from .models.elo import Elo, EloConfig
from .models.logistic import fit_logistic, sigmoid

WARMUP = (2002, 2003, 2004)
DEVELOPMENT = tuple(range(2005, 2017))
VALIDATION = tuple(range(2017, 2022))
TEST = (2022, 2023, 2024, 2025)
FIRST_SCORED = DEVELOPMENT[0]
LAST_OPEN = VALIDATION[-1]                 # seasons after this are reserved for the single logged test run
SEASON_TYPES = ("regular", "postseason")   # preseason and 'other' (Pro Bowl, misdated rows) are never eligible
CUTOFF = timedelta(minutes=120)
GRID = {"k": (10, 15, 20, 25, 30), "home_advantage": (0, 25, 50, 75), "carry": (1.0, 0.75, 0.67, 0.5), "mov": (False, True)}
PLATT_L2, PLATT_CLIP, PLATT_MIN_PAIRS = 1e-3, 1e-6, 50
RELIABILITY_BINS = 10


class TestSeasonsLocked(PermissionError):
    """A reserved test season was requested without a valid sealed choice."""


class TestRunAlreadyDone(PermissionError):
    """The single reserved test run was already started for this seal, or its output already exists."""


# --- Data -----------------------------------------------------------------------------------------------------------
def cutoff_for(kickoff_at: str) -> str:
    """Kickoff − 120 min (UTC), the same in history and in serving."""
    t = datetime.strptime(kickoff_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) - CUTOFF
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def seal_choice(choice: dict) -> dict:
    body = {k: v for k, v in choice.items() if k != "sha256"}
    return {**body, "sha256": hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()}


def _valid_seal(choice: Optional[dict]) -> bool:
    return bool(choice) and seal_choice(choice).get("sha256") == choice.get("sha256")


def _log_access(access_log: Path, sealed: dict, through: int, purpose: str) -> None:
    access_log.parent.mkdir(parents=True, exist_ok=True)
    with access_log.open("a") as f:
        f.write(json.dumps({"at": utc_now(), "through": through, "choice_sha256": sealed["sha256"],
                            "purpose": purpose}) + "\n")


def load_rows(conn: sqlite3.Connection, *, through: int, sealed: Optional[dict] = None,
              access_log: Optional[Path] = None, purpose: str = "") -> list[backtest.Row]:
    """Final regular-season and postseason games of seasons 2002 … `through`. Seasons after 2021 are refused unless
    `sealed` is a choice whose digest matches (then the access is appended to `access_log` before anything is read).
    Filtering is in SQL: rows of later seasons are never read."""
    if through > LAST_OPEN:
        if not _valid_seal(sealed):
            raise TestSeasonsLocked(f"seasons {LAST_OPEN + 1}–{through} are reserved: a sealed choice is required")
        if access_log is None:
            raise TestSeasonsLocked("a test-season read must be logged: pass access_log")
        _log_access(access_log, sealed, through, purpose)
    q = (f"SELECT * FROM games WHERE status = 'final' AND season_type IN ({', '.join('?' * len(SEASON_TYPES))}) "
         "AND season BETWEEN ? AND ? AND home_score IS NOT NULL AND result_available_at IS NOT NULL "
         "ORDER BY tipoff_at, game_id")
    rows = []
    for r in conn.execute(q, (*SEASON_TYPES, WARMUP[0], through)):
        g = PregameGame(r["game_id"], r["season"], r["season_type"], r["tipoff_at"], r["home_team_id"],
                        r["away_team_id"], bool(r["neutral_site"]))
        rows.append(backtest.Row(g, cutoff_for(r["tipoff_at"]), r["home_score"], r["away_score"], r["result_available_at"]))
    return rows


def walk(model, rows: list[backtest.Row]) -> list[dict]:
    """One walk-forward pass (backtest.run) plus the tie flag of each game (ties are void for scoring)."""
    ties = {r.game.game_id for r in rows if r.home_score == r.away_score}
    return [{**p, "tie": p["game_id"] in ties} for p in backtest.run(model, rows)]


def scorable(preds: Iterable[dict], seasons: Optional[Iterable[int]] = None) -> tuple[list[dict], list[dict]]:
    keep = set(seasons) if seasons is not None else None
    sel = [p for p in preds if keep is None or p["season"] in keep]
    return [p for p in sel if not p["tie"]], [p for p in sel if p["tie"]]


# --- Candidates -----------------------------------------------------------------------------------------------------
def margin_multiplier(margin: int, winner_diff: float) -> float:
    """ln(max(|margin|, 1) + 1) × 2.2 / (0.001 × winner_diff + 2.2); a tie counts as margin 1, winner_diff 0."""
    if margin == 0:
        return math.log(2)
    return math.log(max(abs(margin), 1) + 1) * 2.2 / (0.001 * winner_diff + 2.2)


class NflElo(Elo):
    """Elo with the NFL rules: a tie is half a win; the NFL margin multiplier; no home advantage at neutral sites."""
    name = "nfl_elo"

    def update(self, result: Result) -> None:
        g = result.game
        self._roll_season(g.season)
        diff = self._diff(g)
        expected = 1 / (1 + 10 ** (-diff / 400))
        margin = result.home_score - result.away_score
        actual = 0.5 if margin == 0 else (1.0 if margin > 0 else 0.0)
        mult = margin_multiplier(margin, 0.0 if margin == 0 else (diff if margin > 0 else -diff)) if self.cfg.mov else 1.0
        delta = self.cfg.k * mult * (actual - expected)
        self.ratings[g.home_team_id] += delta
        self.ratings[g.away_team_id] -= delta


class NflHomeRate(HomeRate):
    """Laplace-smoothed home win share of non-neutral results so far, a tie counting as half; 0.5 at neutral sites."""
    name = "nfl_home_rate"

    def __init__(self):
        super().__init__()
        self.home_wins = 0.0

    def update(self, result: Result) -> None:
        if result.game.neutral_site:
            return
        self.games += 1
        self.home_wins += 0.5 if result.home_score == result.away_score else float(result.home_won)


def elo_grid() -> list[EloConfig]:
    return [EloConfig(**dict(zip(GRID, v))) for v in itertools.product(*GRID.values())]


def _tiebreak(cfg: EloConfig) -> tuple:
    return (cfg.mov, -cfg.carry, cfg.k, cfg.home_advantage)    # multiplier off, carry 1.0 first, smallest K, smallest H


def best_config(trials: list[tuple[EloConfig, float]]) -> EloConfig:
    return min(trials, key=lambda t: (round(t[1], 6), _tiebreak(t[0])))[0]


def summary(preds: list[dict]) -> dict:
    probs, ys = [p["p_home"] for p in preds], [p["home_won"] for p in preds]
    return {"n": len(preds), "brier": metrics.brier(probs, ys), "log_loss": metrics.log_loss(probs, ys),
            "accuracy": metrics.accuracy(probs, ys),
            "reliability": [b.model_dump() for b in metrics.calibration(probs, ys, bins=RELIABILITY_BINS)]}


def tune(rows: list[backtest.Row]) -> tuple[EloConfig, list[dict]]:
    """Development Brier (non-tie games of 2005–2016) for every grid point; refuses later seasons."""
    late = {r.game.season for r in rows} - set(WARMUP + DEVELOPMENT)
    if late:
        raise ValueError(f"tuning received seasons after development: {sorted(late)}")
    trials = []
    for cfg in elo_grid():
        scored, _ = scorable(walk(NflElo(cfg), rows), DEVELOPMENT)
        trials.append((cfg, metrics.brier([p["p_home"] for p in scored], [p["home_won"] for p in scored])))
    return best_config(trials), [{**cfg.__dict__, "brier": b} for cfg, b in trials]


def _beats(a: list[dict], b: list[dict]) -> dict:
    """a vs b on the same games: paired week-block bootstrap of per-game Brier (a − b); upper bound < 0 favours a."""
    boot = backtest.paired_bootstrap(a, b)
    return {"mean_diff": boot["mean_diff"], "ci95": boot["ci95"], "weeks": boot["weeks"], "games": boot["games"],
            "beats": boot["ci95"][1] < 0}


def choose(validation: dict[str, list[dict]]) -> dict:
    """Elo if lowest validation Brier AND it beats the home rate; else the home rate if it beats 0.5; else 0.5."""
    s = {m: summary(p)["brier"] for m, p in validation.items()}
    elo_vs_home = _beats(validation["elo"], validation["home_rate"])
    home_vs_const = _beats(validation["home_rate"], validation["constant"])
    lowest = min(s, key=s.get)
    if lowest == "elo" and elo_vs_home["beats"]:
        model = "elo"
    elif home_vs_const["beats"]:
        model = "home_rate"
    else:
        model = "constant"
    return {"model": model, "validation_brier": s, "elo_vs_home_rate": elo_vs_home, "home_rate_vs_constant": home_vs_const}


# --- Platt scaling (pinned in the plan) -----------------------------------------------------------------------------
def _logit(p: float) -> float:
    p = min(1 - PLATT_CLIP, max(PLATT_CLIP, p))
    return math.log(p / (1 - p))


def fit_platt(pairs: list[tuple[float, bool]]) -> tuple[Optional[tuple[float, float]], str]:
    """(a, b) of σ(a·logit(p) + b), or (None, reason) for the raw fallback."""
    if len(pairs) < PLATT_MIN_PAIRS:
        return None, f"fallback: {len(pairs)} pairs (< {PLATT_MIN_PAIRS})"
    try:
        b, a = fit_logistic([[_logit(p)] for p, _ in pairs], [y for _, y in pairs], PLATT_L2)
    except Exception as exc:                        # noqa: BLE001 — any fitting failure means raw probabilities
        return None, f"fallback: fit failed ({type(exc).__name__}: {exc})"
    if not (math.isfinite(a) and math.isfinite(b)) or a <= 0:
        return None, f"fallback: unusable coefficients (a={a}, b={b})"
    return (a, b), "fitted"


def _apply(p: float, a_b: Optional[tuple[float, float]]) -> float:
    return p if a_b is None else sigmoid(a_b[0] * _logit(p) + a_b[1])


def platt_walk(preds: list[dict]) -> tuple[list[dict], dict]:
    """Calibrated copies of walk-forward predictions: for each season s (from 2005), the map is fitted once on non-tie
    predictions of seasons 2005 … s−1; warm-up seasons are passed through raw and never used for fitting."""
    out, notes = [], {}
    for season in sorted({p["season"] for p in preds}):
        group = [p for p in preds if p["season"] == season]
        if season < FIRST_SCORED:
            out.extend(group)
            continue
        pairs = [(p["p_home"], p["home_won"]) for p in preds if FIRST_SCORED <= p["season"] < season and not p["tie"]]
        a_b, note = fit_platt(pairs)
        notes[season] = note if a_b is None else f"fitted on {FIRST_SCORED}-{season - 1} ({len(pairs)} pairs)"
        out.extend({**p, "p_home": _apply(p["p_home"], a_b), "p_raw": p["p_home"]} for p in group)
    return out, notes


def accept_calibration(calibrated: list[dict], raw: list[dict]) -> dict:
    """Kept only if calibrated Brier AND log loss are lower on the same games AND the bootstrap 95 % upper bound of the
    per-game Brier difference (calibrated − raw) is below 0. The interval is for Brier only; log loss is a point check."""
    c, r = summary(calibrated), summary(raw)
    boot = _beats(calibrated, raw)
    reasons = []
    if not c["brier"] < r["brier"]:
        reasons.append("Brier not lower")
    if not c["log_loss"] < r["log_loss"]:
        reasons.append("log loss not lower")
    if not boot["beats"]:
        reasons.append("Brier interval does not exclude 0")
    return {"accepted": not reasons, "reason": "; ".join(reasons) or "all three conditions met",
            "brier_calibrated": c["brier"], "brier_raw": r["brier"], "log_loss_calibrated": c["log_loss"],
            "log_loss_raw": r["log_loss"], "brier_diff": boot["mean_diff"], "brier_ci95": boot["ci95"]}


def final_calibrator(preds: list[dict]) -> dict:
    """The calibrator sealed with the choice: fitted once on all non-tie walk-forward predictions of 2005–2021."""
    late = sorted({p["season"] for p in preds if p["season"] > LAST_OPEN})
    if late:
        raise ValueError(f"the final calibrator may not see reserved seasons {late}")
    pairs = [(p["p_home"], p["home_won"]) for p in preds if FIRST_SCORED <= p["season"] and not p["tie"]]
    a_b, note = fit_platt(pairs)
    return {"a": a_b[0] if a_b else None, "b": a_b[1] if a_b else None, "note": note, "pairs": len(pairs),
            "seasons": [FIRST_SCORED, LAST_OPEN], "l2": PLATT_L2, "clip": PLATT_CLIP}


def apply_frozen(preds: list[dict], cal: dict) -> list[dict]:
    """Applies sealed coefficients unchanged (no refit during the test run)."""
    a_b = (cal["a"], cal["b"]) if cal.get("a") is not None else None
    return [{**p, "p_home": _apply(p["p_home"], a_b), "p_raw": p["p_home"]} for p in preds]


# --- Development + validation run (never reads 2022–2025) -------------------------------------------------------------
def evaluate_open(conn: sqlite3.Connection) -> dict:
    """Development tuning, validation choice and the calibration test; returns the report and the choice to seal."""
    dev_rows = [r for r in load_rows(conn, through=DEVELOPMENT[-1])]
    elo_cfg, trials = tune(dev_rows)
    rows = load_rows(conn, through=LAST_OPEN)
    full = {"constant": walk(Constant(), rows), "home_rate": walk(NflHomeRate(), rows), "elo": walk(NflElo(elo_cfg), rows)}
    val = {m: scorable(p, VALIDATION)[0] for m, p in full.items()}
    _, val_ties = scorable(full["constant"], VALIDATION)
    choice = choose(val)
    report = {
        "plan": "docs/NFL_PLAN.md (NFL evaluation plan, amended 2026-11-01)", "generated_at": utc_now(),
        "dataset_version": backtest.dataset_version(rows),
        "seasons": {"warmup": list(WARMUP), "development": [DEVELOPMENT[0], DEVELOPMENT[-1]],
                    "validation": [VALIDATION[0], VALIDATION[-1]], "reserved_not_read": list(TEST)},
        "games": {"development_scored": len(scorable(full["constant"], DEVELOPMENT)[0]),
                  "development_ties": len(scorable(full["constant"], DEVELOPMENT)[1]),
                  "validation_scored": len(val["constant"]), "validation_ties": len(val_ties)},
        "development": {"selected": elo_cfg.__dict__, "grid_size": len(trials),
                        "top5": sorted(trials, key=lambda t: t["brier"])[:5]},
        "validation": {m: summary(p) for m, p in val.items()},
        "validation_by_season": {m: {s: summary(scorable(p, [s])[0])["brier"] for s in VALIDATION} for m, p in full.items()},
        "choice": choice,
    }
    calibration = {"accepted": False, "reason": "not applicable to the constant 0.5"}
    final, cal_full = None, None
    if choice["model"] != "constant":
        raw_full = full[choice["model"]]
        cal_full, notes = platt_walk(raw_full)
        cal_val = scorable(cal_full, VALIDATION)[0]
        calibration = {**accept_calibration(cal_val, val[choice["model"]]), "fits": notes,
                       "validation_reliability_calibrated": summary(cal_val)["reliability"]}
        if calibration["accepted"]:
            final = final_calibrator(raw_full)
    report["calibration"] = calibration
    sealed_preds = sealed_version(full[choice["model"]], cal_full if choice["model"] != "constant" else None,
                                  accepted=calibration["accepted"])
    report["line_markets"], line_seal = evaluate_lines(conn, rows, sealed_preds)
    config = elo_cfg.__dict__ if choice["model"] == "elo" else ({"smoothing": "laplace", "ties": "half"}
                                                                if choice["model"] == "home_rate" else {"p": 0.5})
    sealed = seal_choice({"sport": "nfl", "market": "winner", "model": choice["model"], "config": config,
                          "cutoff": "kickoff - 120 min", "calibration": {"accepted": calibration["accepted"],
                                                                        "final": final},
                          "line_markets": line_seal,
                          "dataset_version": report["dataset_version"], "chosen_at": report["generated_at"],
                          "plan": report["plan"]})
    return {"report": report, "sealed_choice": sealed}


# --- Line markets (docs/NFL_PLAN.md, "NFL line markets and thresholds") ------------------------------------------------
from . import markets as _markets                                  # noqa: E402
from . import threshold_overview as _to                            # noqa: E402
from .models import points as _points                              # noqa: E402
from .models.points import PointsConfig                            # noqa: E402,F401  (re-exported for callers/tests)

LINE_MARKETS = _markets.LINE_MARKETS
NFL_PRIOR_POINTS, NFL_PRIOR_SD_MARGIN, NFL_PRIOR_SD_TOTAL = 22.0, 13.5, 13.5
POINTS_GRID = {"eta": (0.01, 0.02, 0.05, 0.1), "carry": (1.0, 0.75, 0.5)}
THRESHOLDS = tuple(round(0.5 + 0.05 * i, 2) for i in range(9))      # 50 %+ … 90 %+


class NflMarginTotal(_markets.MarginTotalNormal):
    """As the NBA distribution, but a zero margin is possible (NFL ties), so no mass is removed from M = 0."""

    def __init__(self, margin: float, sd_margin: float, total: float, sd_total: float):
        super().__init__(margin, sd_margin, total, sd_total)
        self._p0 = 0.0

    def _p_margin_above(self, x: float) -> float:
        k = math.floor(x) + 1
        return 1 - self._cdf_m(k - 0.5)

    def _p_margin_eq(self, k: float) -> float:
        if k != int(k):
            return 0.0
        return self._cdf_m(k + 0.5) - self._cdf_m(k - 0.5)


class NflPoints(_points.PointsNormal):
    """`points_normal` with the NFL priors fixed in the plan and the NFL margin distribution."""

    def __init__(self, cfg: PointsConfig = PointsConfig()):
        super().__init__(cfg)
        self.home_mean = self.away_mean = NFL_PRIOR_POINTS
        self.var_margin, self.var_total = NFL_PRIOR_SD_MARGIN ** 2, NFL_PRIOR_SD_TOTAL ** 2

    @property
    def name(self) -> str:
        return "nfl_points_normal"

    def distribution(self, game: PregameGame) -> NflMarginTotal:
        ph, pa = self.expected(game)
        return NflMarginTotal(ph - pa, math.sqrt(self.var_margin), ph + pa, math.sqrt(self.var_total))


def points_grid() -> list[PointsConfig]:
    return [PointsConfig(**dict(zip(POINTS_GRID, v))) for v in itertools.product(*POINTS_GRID.values())]


def best_points(trials: list[tuple[PointsConfig, float]]) -> PointsConfig:
    return min(trials, key=lambda t: (round(t[1], 4), -t[0].carry, t[0].eta))[0]


def _params(model, game) -> dict:
    d = model.distribution(game)
    return {"dist": {"margin": d.m, "sd_margin": d.sm, "total": d.t, "sd_total": d.st}}


def points_walk(cfg: PointsConfig, rows: list[backtest.Row]) -> list[dict]:
    scores = {r.game.game_id: (r.home_score, r.away_score) for r in rows}
    out = []
    for p in backtest.run(NflPoints(cfg), rows, extra=_params):
        hs, as_ = scores[p["game_id"]]
        out.append({**p, "home_score": hs, "away_score": as_})
    return out


def tune_points(rows: list[backtest.Row]) -> tuple[PointsConfig, list[dict]]:
    """Development criterion: MSE(margin) + MSE(total) on 2005–2016; refuses later seasons."""
    late = {r.game.season for r in rows} - set(WARMUP + DEVELOPMENT)
    if late:
        raise ValueError(f"tuning received seasons after development: {sorted(late)}")
    trials = []
    for cfg in points_grid():
        preds = [p for p in points_walk(cfg, rows) if p["season"] in DEVELOPMENT]
        mse_m = sum((p["home_score"] - p["away_score"] - p["dist"]["margin"]) ** 2 for p in preds) / len(preds)
        mse_t = sum((p["home_score"] + p["away_score"] - p["dist"]["total"]) ** 2 for p in preds) / len(preds)
        trials.append((cfg, mse_m + mse_t))
    return best_points(trials), [{"eta": c.eta, "carry": c.carry, "criterion": v} for c, v in trials]


def _half(x: float) -> float:
    """Nearest 0.5; a value exactly between (x.25 / x.75) goes to the x.5 line, which cannot push."""
    q = x * 4
    if abs(q - round(q)) < 1e-9 and int(round(q)) % 2 == 1:      # quarter point: halfway between two 0.5 steps
        lo, hi = math.floor(x * 2) / 2, math.ceil(x * 2) / 2
        return lo if lo != int(lo) else hi
    return math.floor(x * 2 + 0.5) / 2


def implied_team_lines(spread: float, total: float) -> tuple[float, float]:
    """Implied home/away team lines from the home spread S and total T: ((T − S)/2, (T + S)/2), to the nearest 0.5."""
    return _half((total - spread) / 2), _half((total + spread) / 2)


def line_events(conn: sqlite3.Connection, through: int = LAST_OPEN, *, sealed: Optional[dict] = None,
                access_log: Optional[Path] = None) -> list[str]:
    """ESPN event ids whose lines may be collected: validation seasons 2017 … `through` (≤ 2021); or, for the sealed test
    run only, the reserved seasons 2022 … `through`, which needs a valid seal and is logged before the ids are read
    (odds are collected for them, never results)."""
    if through > LAST_OPEN:
        if not _valid_seal(sealed) or access_log is None:
            raise TestSeasonsLocked("lines of the reserved seasons are collected only for the sealed test run (seal + log)")
        _log_access(access_log, sealed, through, "collect reserved lines (odds only)")
        first = TEST[0]
    else:
        first = VALIDATION[0]
    q = (f"SELECT game_id FROM games WHERE status = 'final' AND season_type IN ({', '.join('?' * len(SEASON_TYPES))}) "
         "AND season BETWEEN ? AND ? ORDER BY tipoff_at DESC")
    return [r[0].split(":", 1)[1] for r in conn.execute(q, (*SEASON_TYPES, first, through))]


def consensus_lines(sport_dir_files: dict) -> dict:
    """game id (nfl:<event>) -> {"spread": home line, "total": line} from stored raw odds (consensus close)."""
    from .sources import espn_odds_history as eoh
    out = {}
    for event, path in sport_dir_files.items():
        rows = eoh.parse(path.read_bytes(), "nfl")
        s, t = eoh.consensus(rows, "close", "spread"), eoh.consensus(rows, "close", "total")
        out[f"nfl:{event}"] = {"spread": s["line"] if s else None, "total": t["line"] if t else None,
                               "books_spread": s["books"] if s else 0, "books_total": t["books"] if t else 0}
    return out


def grade_market(market: str, params: dict, meta: dict, lines: dict) -> tuple[list[dict], dict]:
    """Model probability of side a at the consensus line vs the realised side; pushes and games without a line counted."""
    graded, counts = [], {"scored": 0, "push": 0, "no_line": 0}
    for gid, prm in params.items():
        ln = lines.get(gid) or {}
        sp, tot = ln.get("spread"), ln.get("total")
        if market == "spread":
            line = sp
        elif market == "total":
            line = tot
        else:
            line = None if sp is None or tot is None else implied_team_lines(sp, tot)[0 if market == "team_total_home" else 1]
        if line is None:
            counts["no_line"] += 1
            continue
        m = meta[gid]
        side = _markets.settle(market, line, status="final", home=m["home_score"], away=m["away_score"])
        if side == "mid":
            counts["push"] += 1
            continue
        a, mid, b = NflMarginTotal(**prm).market(market, line)
        graded.append({"game_id": gid, "season": m["season"], "cutoff_at": m["cutoff_at"], "line": line,
                       "p_home": a / (a + b), "home_won": side == "a", "tie": False})
        counts["scored"] += 1
    return graded, counts


def skill_verdict(graded: list[dict]) -> dict:
    """Proven only if model Brier < 0.5's AND the 95 % upper bound of (model − 0.5) per-game Brier is below 0."""
    if not graded:
        return {"proven": False, "reason": "no scored games"}
    coin = [{**p, "p_home": 0.5} for p in graded]
    boot = _beats(graded, coin)
    s = summary(graded)
    proven = s["brier"] < 0.25 and boot["beats"]
    return {"proven": proven, "scope": "retrospective closing-line test",
            "note": ("probability skill vs a coin flip at the consensus closing line, judged after the fact: the close is "
                     "not known two hours before kickoff, so this is not evidence that these picks were available then, "
                     "and a lower Brier score is not a profitable edge (no prices, vig or line movement are tested)"),
            "brier": s["brier"], "brier_coin": 0.25, "log_loss": s["log_loss"],
            "brier_diff": boot["mean_diff"], "ci95": boot["ci95"], "games": len(graded), "reliability": s["reliability"]}


IMPLIED_MARKETS = ("team_total_home", "team_total_away")      # no quoted NFL team-total lines: implied, never a best pick


def threshold_rows(graded: list[dict], thresholds: Iterable[float] = THRESHOLDS) -> list[dict]:
    """Per cumulative band of the picked side: settled picks, wins, win rate, Wilson 95 %, stated mean, calibration."""
    rows = []
    for th in thresholds:
        picks = [p for p in graded if max(p["p_home"], 1 - p["p_home"]) >= th - 1e-12]
        wins = sum((p["p_home"] >= 0.5) == p["home_won"] for p in picks)
        n = len(picks)
        stated = (sum(max(p["p_home"], 1 - p["p_home"]) for p in picks) / n) if n else None
        ci = _to.wilson(wins, n)
        rows.append({"threshold": th, "graded": n, "won": wins, "win_rate": wins / n if n else None, "ci95": ci,
                     "stated": stated, "calibration": _to.calibration_state(wins / n if n else None, ci, stated, n,
                                                                           _to.MIN_HISTORY_PICKS)})
    return rows


def threshold_table(graded: list[dict], *, line_market: bool, proven: bool, market: Optional[str] = None) -> dict:
    """Cumulative bands on the picked side (probability ≥ 0.5), Wilson intervals and the app's best-threshold rule."""
    rows = threshold_rows(graded)
    out = {"rows": rows, "break_even": _to.BREAK_EVEN_STANDARD if line_market else None,
           # Line markets are graded at the closing line, unknown at kickoff − 120 min: whatever this table shows, it cannot
           # unlock a best pick; that needs a prospective record graded at the line available when each forecast was made.
           "best_pick_allowed": not line_market,
           "best_pick_note": ("closing-line retrospective only: a best pick needs a prospective record at forecast-time "
                              "lines") if line_market else None}
    if market in IMPLIED_MARKETS:
        return {**out, "best": None, "strong": False, "reason": "implied team line (no quoted NFL team totals): never a best pick"}
    if line_market and not proven:
        return {**out, "best": None, "strong": False, "reason": "no proven skill against the line: never a best pick"}
    ch = _to.choose(rows, [])
    best = next((r for r in rows if ch["best"] is not None and abs(r["threshold"] - ch["best"]) < 1e-9), None)
    return {**out, "best": ch["best"], "strong": bool(best and best["ci95"] and best["ci95"][0] > _to.STRONG_LOWER),
            "reason": ch["reason"]}


def sealed_version(raw: list[dict], calibrated: Optional[list[dict]], *, accepted: bool) -> list[dict]:
    """The winner probabilities that are being sealed: calibrated when the calibrator was accepted, else raw."""
    return calibrated if accepted and calibrated is not None else raw


def evaluate_lines(conn: sqlite3.Connection, rows: list[backtest.Row], winner_preds: list[dict]) -> tuple[dict, dict]:
    """Score-model tuning (development), per-market skill verdicts and thresholds on validation at the consensus closing
    line, plus the winner threshold table from the chosen winner model. Lines must have been collected (validation only)."""
    from .sources import espn_odds_history as eoh
    have = eoh.stored("nfl")["files"]
    if not have:
        msg = "no NFL lines collected: run `python -m app.cli nfl collect-lines` (validation seasons only)"
        return {"status": "not_run", "reason": msg}, {"status": "not_run"}
    dev_rows = [r for r in rows if r.game.season <= DEVELOPMENT[-1]]
    cfg, trials = tune_points(dev_rows)
    walked = points_walk(cfg, rows)
    val = [p for p in walked if p["season"] in VALIDATION]
    params = {p["game_id"]: p["dist"] for p in val}
    meta = {p["game_id"]: {"season": p["season"], "cutoff_at": p["cutoff_at"], "home_score": p["home_score"],
                           "away_score": p["away_score"]} for p in val}
    lines = consensus_lines(have)
    report = {"status": "run", "score_model": {"name": "nfl_points_normal", "selected": {"eta": cfg.eta, "carry": cfg.carry},
                                               "trials": trials},
              "lines": {"events_with_raw_odds": len(have),
                        "validation_games_with_spread": sum(1 for g in params if (lines.get(g) or {}).get("spread") is not None),
                        "validation_games_with_total": sum(1 for g in params if (lines.get(g) or {}).get("total") is not None),
                        "validation_games": len(params)},
              "markets": {}}
    seal = {"score_model": {"name": "nfl_points_normal", "eta": cfg.eta, "carry": cfg.carry,
                            "priors": {"points": NFL_PRIOR_POINTS, "sd_margin": NFL_PRIOR_SD_MARGIN,
                                       "sd_total": NFL_PRIOR_SD_TOTAL}},
            "line": "consensus close (ESPN core odds), never a model input", "markets": {}}
    for market in LINE_MARKETS:
        graded, counts = grade_market(market, params, meta, lines)
        verdict = skill_verdict(graded)
        table = threshold_table(graded, line_market=True, proven=verdict["proven"], market=market)
        report["markets"][market] = {"counts": counts, "skill": verdict, "thresholds": table,
                                     "implied_line": market.startswith("team_total")}
        seal["markets"][market] = {"closing_line_skill": verdict["proven"], "scope": verdict.get("scope"),
                                   "best_threshold": table["best"], "strong": table["strong"],
                                   "best_pick_allowed": False, "implied_line": market in IMPLIED_MARKETS}
    win_val = scorable(winner_preds, VALIDATION)[0]
    wt = threshold_table(win_val, line_market=False, proven=True)
    report["markets"]["winner"] = {"thresholds": wt}
    seal["markets"]["winner"] = {"best_threshold": wt["best"], "strong": wt["strong"]}
    return report, seal


# --- The single reserved test run (docs/NFL_PLAN.md, "Reserved test run protocol") ------------------------------------
TEST_REPORT = "nfl_test_report.json"


def access_log_path() -> Path:
    from . import config
    return config.data_dir() / "research" / "nfl_eval" / "nfl_test_access.jsonl"
_RUN_START, _RUN_DONE = "test run: start", "test run: complete"


def _check_frozen(conn: sqlite3.Connection, sealed: Optional[dict]) -> None:
    """Valid digest, a frozen winner threshold, and the same 2002–2021 data the choice was sealed on. Reads open seasons only."""
    if not _valid_seal(sealed):
        raise TestSeasonsLocked("the sealed choice is missing or its digest does not match")
    winner = ((sealed.get("line_markets") or {}).get("markets") or {}).get("winner") or {}
    if winner.get("best_threshold") is None:
        raise TestSeasonsLocked("the sealed choice has no frozen winner threshold")
    if backtest.dataset_version(load_rows(conn, through=LAST_OPEN)) != sealed.get("dataset_version"):
        raise TestSeasonsLocked("the store's 2002–2021 data differ from the data the choice was sealed on")


def _winner_model(sealed: dict):
    if sealed["model"] == "elo":
        return NflElo(EloConfig(**sealed["config"]))
    return NflHomeRate() if sealed["model"] == "home_rate" else Constant()


def evaluate_test(conn: sqlite3.Connection, sealed: dict, *, access_log: Path) -> dict:
    """Frozen models on the reserved seasons: nothing is tuned, chosen or fitted. Call through `run_test`."""
    rows = load_rows(conn, through=TEST[-1], sealed=sealed, access_log=access_log, purpose=_RUN_START)
    full = {"constant": walk(Constant(), rows), "home_rate": walk(NflHomeRate(), rows)}
    chosen_raw = walk(_winner_model(sealed), rows)
    if sealed["model"] == "elo":
        full["elo"] = chosen_raw
    final = (sealed.get("calibration") or {}).get("final")
    chosen = apply_frozen(chosen_raw, final) if (sealed.get("calibration") or {}).get("accepted") and final else chosen_raw
    test = {m: scorable(p, TEST)[0] for m, p in full.items()}
    chosen_test, ties = scorable(chosen, TEST)
    vs_home = _beats(chosen_test, test["home_rate"]) if sealed["model"] != "home_rate" else None
    vs_const = _beats(chosen_test, test["constant"]) if sealed["model"] != "constant" else None
    frozen = sealed["line_markets"]["markets"]["winner"]["best_threshold"]
    band = threshold_rows(chosen_test, [frozen])[0]
    winner = {
        "model": sealed["model"], "config": sealed["config"],
        "probabilities": "calibrated (frozen)" if chosen is not chosen_raw else "raw",
        "games": {"scored": len(chosen_test), "ties": len(ties)},
        "summary": {**{m: summary(p) for m, p in test.items()}, "chosen": summary(chosen_test)},
        "by_season": {m: {s: summary(scorable(p, [s])[0])["brier"] for s in TEST if scorable(p, [s])[0]}
                      for m, p in full.items()},
        "chosen_vs_home_rate": vs_home, "chosen_vs_constant": vs_const,
        "confirmed": bool(vs_home and vs_home["beats"]) if sealed["model"] != "home_rate" else bool(vs_const and vs_const["beats"]),
        "frozen_threshold": {**band, "holds": bool(band["ci95"] and band["ci95"][0] > _to.STRONG_LOWER),
                             "rule": "holds = Wilson lower bound > 50 %; the 200-pick minimum is a selection rule"},
        "thresholds_reported_only": {"rows": threshold_rows(chosen_test),
                                     "note": "reported only: no band is promoted, re-chosen or re-labelled from the test"},
    }
    return {"plan": "docs/NFL_PLAN.md (Reserved test run protocol, 2026-11-01)", "generated_at": utc_now(),
            "choice_sha256": sealed["sha256"], "dataset_version_open": sealed["dataset_version"],
            "dataset_version_all": backtest.dataset_version(rows),
            "seasons": {"test": list(TEST)}, "winner": winner, "line_markets": _test_lines(rows, sealed)}


def _test_lines(rows: list[backtest.Row], sealed: dict) -> dict:
    """The frozen score model graded at the reserved seasons' consensus close (retrospective; never unlocks a best pick)."""
    from .sources import espn_odds_history as eoh
    sm = sealed["line_markets"].get("score_model") or {}
    if "eta" not in sm:
        return {"status": "not_run", "reason": "no frozen score model in the sealed choice"}
    val = [p for p in points_walk(PointsConfig(eta=sm["eta"], carry=sm["carry"]), rows) if p["season"] in TEST]
    test_events = {p["game_id"].split(":", 1)[1] for p in val}
    files = {e: f for e, f in eoh.stored("nfl")["files"].items() if e in test_events}
    if not files:
        return {"status": "not_run", "reason": "no reserved-season lines collected (`nfl collect-lines --test`)"}
    params = {p["game_id"]: p["dist"] for p in val}
    meta = {p["game_id"]: {"season": p["season"], "cutoff_at": p["cutoff_at"], "home_score": p["home_score"],
                           "away_score": p["away_score"]} for p in val}
    lines = consensus_lines(files)
    out = {"status": "run", "score_model": {"eta": sm["eta"], "carry": sm["carry"]},
           "lines": {"test_games": len(params), "events_with_raw_odds": len(files)},
           "note": "per-market intervals, no family-wise correction: exploratory; best_pick_allowed stays false",
           "markets": {}}
    for market in LINE_MARKETS:
        graded, counts = grade_market(market, params, meta, lines)
        out["markets"][market] = {"counts": counts, "skill": skill_verdict(graded),
                                  "thresholds_reported_only": threshold_rows(graded), "best_pick_allowed": False,
                                  "implied_line": market in IMPLIED_MARKETS}
    return out


def _already_run(access_log: Path, sha: str) -> bool:
    if not access_log.is_file():
        return False
    for line in access_log.read_text().splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("choice_sha256") == sha and str(e.get("purpose", "")).startswith("test run"):
            return True
    return False


def run_test(conn: sqlite3.Connection, sealed: dict, *, access_log: Path, out_dir: Path) -> dict:
    """The single logged test run: refuses a bad seal, changed data, a previous run of this seal or an existing output;
    logs the start before any reserved row is read and the completion after the report is written."""
    if out_dir.exists():
        raise TestRunAlreadyDone(f"output {out_dir} already exists")
    _check_frozen(conn, sealed)
    if _already_run(access_log, sealed["sha256"]):
        raise TestRunAlreadyDone("the reserved test run was already started for this sealed choice")
    report = evaluate_test(conn, sealed, access_log=access_log)
    out_dir.mkdir(parents=True)
    (out_dir / TEST_REPORT).write_text(json.dumps(report, indent=1, default=str))
    (out_dir / "nfl_sealed_choice_used.json").write_text(json.dumps(sealed, indent=1))
    _log_access(access_log, sealed, TEST[-1], _RUN_DONE)
    return report
