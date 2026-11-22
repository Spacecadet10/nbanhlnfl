"""Prospective NFL weekly challengers: variants of parameters that ALREADY exist as configurable values in the NFL code.

The served NFL path (nfl_live, nfl_eval, coverage, markets, models/*) is never edited; this module only reads from it. Variants
(reference/learning_policies/nfl-challengers-v1.json) change ONE existing parameter of the sealed activation's learners:

* elo     winner Elo: k or home_advantage, a value from nfl_eval.GRID (carry and the margin multiplier stay as sealed).
* points  score model behind spread, total and the team totals: eta or carry, a value from nfl_eval.POINTS_GRID.

Recorded at the served cutoff (kickoff minus 120 minutes, state from results available by then, walked forward exactly as
nfl_live.record does) into the challenger store, at the line of the served forecast row (so every row pairs with an incumbent);
a game without its served forecast is skipped until it exists. The sport store is opened read-only and the served tables are never
written. At about 17 games a week, evidence this season stays far below any preregistered sample size: no claim.

  python -m app.learning_challengers record --sport nfl [--dry-run] [--now T --store PATH]
"""
import json
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

from . import learning_challengers as lc, nfl_eval, nfl_live
from .db import has_table, utc_now
from .models.elo import EloConfig
from .models.points import PointsConfig

FAMILY = "nfl_param"
ALLOWED = {"elo": ("k", "home_advantage"), "points": ("eta", "carry")}
POINT_MARKETS = ("spread", "total", "team_total_home", "team_total_away")
ORDER_VERSION = "nfl-live-v1"


@dataclass(frozen=True)
class Spec:
    name: str
    sport: str
    family: str
    head: str
    features: str
    role: str
    why: str
    refit: str = "week"
    extra: Optional[dict] = None


def spec(raw: dict, sport: str = "nfl") -> Spec:
    if not isinstance(raw, dict) or set(raw) - set(Spec.__dataclass_fields__) or \
            not {"name", "sport", "family", "head", "features", "role", "why", "extra"} <= set(raw):
        raise lc.PolicyError(f"spec fields must be within {sorted(Spec.__dataclass_fields__)} and include extra")
    if raw["sport"] != sport:
        raise lc.PolicyError(f"spec {raw['name']}: sport {raw['sport']!r} is not the policy's sport {sport!r}")
    if raw["family"] != FAMILY:
        raise lc.PolicyError(f"spec {raw['name']}: family must be {FAMILY!r}")
    x = raw["extra"]
    if not isinstance(x, dict) or set(x) - set(ALLOWED):
        raise lc.PolicyError(f"spec {raw['name']}: extra may hold only {tuple(ALLOWED)} (unknown extra)")
    if len(x) != 1:
        raise lc.PolicyError(f"spec {raw['name']}: extra must hold exactly one of {tuple(ALLOWED)}")
    head, params = next(iter(x.items()))
    grid = nfl_eval.GRID if head == "elo" else nfl_eval.POINTS_GRID
    if not isinstance(params, dict) or not params or set(params) - set(ALLOWED[head]):
        raise lc.PolicyError(f"spec {raw['name']}: {head} may vary only {ALLOWED[head]}")
    for k, v in params.items():
        if v not in grid[k]:
            raise lc.PolicyError(f"spec {raw['name']}: {head} {k}={v!r} is not in the existing grid {grid[k]}")
    return Spec(**raw)


def _model(sp: Spec, sealed: dict):
    head, params = next(iter(sp.extra.items()))
    if head == "elo":
        return nfl_eval.NflElo(replace(EloConfig(**sealed["config"]), **params))
    sm = sealed["line_markets"]["score_model"]
    return nfl_eval.NflPoints(replace(PointsConfig(eta=sm["eta"], carry=sm["carry"]), **params))


def challenger_access_log_path() -> Path:
    return nfl_eval.access_log_path().with_name("nfl_challenger_access.jsonl")


def research_access_log_path() -> Path:
    """Reserved-season reads by the research adapter's forward state: a log of their own, never the official NFL test-access
    log (nfl_eval.access_log_path), which must not change."""
    return nfl_eval.access_log_path().with_name("nfl_research_access.jsonl")


def record(conn, path: Path, *, policies: list, now: Optional[str] = None, dry_run: bool = False) -> dict:
    """Challenger rows for scheduled games past their served cutoff (kickoff - 120 min) and before kickoff."""
    now = now or utc_now()
    out = {"status": "success", "dry_run": dry_run, "now": now, "created": 0, "would_record": 0, "already_recorded": 0,
           "eligible_games": 0, "skipped": {}, "errors": {}, "policies": [{"policy_id": p.policy_id, "sha": p.sha} for p in policies]}
    try:
        lc.check_unique_names(policies)
        a = nfl_live.activation()
        if not a:
            out["status"], out["errors"]["recorder"] = "failed", "NFL live activation is missing or its definition changed"
            return out
        s = a["sealed"]
        games = [g for g in conn.execute("SELECT * FROM games WHERE status='scheduled' AND season_type IN ('regular','postseason') "
                                         "AND tipoff_at>? ORDER BY tipoff_at, game_id", (now,))
                 if nfl_eval.cutoff_for(g["tipoff_at"]) <= now]
        out["eligible_games"] = len(games)
        served = {}
        for g in games:
            rows = {r["market"]: r for r in conn.execute(
                "SELECT market, line FROM game_forecasts WHERE game_id=? AND tipoff_at=? AND status='forecast' AND order_version=? "
                "AND generated_at<=? ORDER BY generated_at, forecast_id",       # the latest per market wins (dict order)
                (g["game_id"], g["tipoff_at"], ORDER_VERSION, now))}
            if rows:
                served[g["game_id"]] = rows
            else:
                out["skipped"][g["game_id"]] = "no served forecast yet: nothing to pair with"
        due = [g for g in games if g["game_id"] in served]
        if not due:
            return out
        # The official test-access log (nfl_test_access.jsonl) keeps its audit meaning: the challenger recorder writes its own log,
        # once per (policy set, game set), not on every 15-minute run. A dry run logs to a throw-away file.
        purpose = ("prospective NFL challenger state initialization: " + ",".join(sorted(p.policy_id for p in policies))
                   + " games " + ",".join(sorted(g["game_id"] for g in due)))
        log = challenger_access_log_path()
        if dry_run or (log.is_file() and any(json.loads(x).get("purpose") == purpose for x in log.read_text().splitlines() if x)):
            log = Path(tempfile.mkdtemp(prefix="nfl-challenger-access-")) / "access.jsonl"
        results = nfl_eval.load_rows(conn, through=max(g["season"] for g in due), sealed=s, access_log=log, purpose=purpose)
        results = sorted(results, key=lambda r: (r.result_available_at, r.game.tipoff_at, r.game.game_id))
    except Exception as exc:
        out["status"], out["errors"]["recorder"] = "failed", f"{type(exc).__name__}: {exc}"
        return out
    for pol in policies:
        for c in pol.challengers:
            head = next(iter(c.spec.extra))
            if head == "elo" and s["model"] != "elo":
                out["skipped"][c.spec.name] = "the sealed winner model is not Elo"
                continue
            try:
                model, i, rows = _model(c.spec, s), 0, []
                for g in due:
                    cutoff = nfl_eval.cutoff_for(g["tipoff_at"])
                    while i < len(results) and results[i].result_available_at <= cutoff:
                        model.update(results[i].result)
                        i += 1
                    pg = nfl_live._game(g)
                    if head == "elo":
                        p = model.predict(pg)
                        if s.get("calibration", {}).get("accepted"):
                            p = nfl_eval.apply_frozen([{"p_home": p}], s["calibration"]["final"])[0]["p_home"]
                        todo = {"winner": (p, 0.0, 1 - p)}
                    else:
                        dist = model.distribution(pg)
                        todo = {m: dist.market(m, served[g["game_id"]][m]["line"]) for m in POINT_MARKETS if m in served[g["game_id"]]}
                    for market, probs in todo.items():
                        if market not in served[g["game_id"]]:
                            continue
                        pa, pm = round(probs[0], 9), round(probs[1], 9)
                        rows.append({"game_id": g["game_id"], "market": market, "line": served[g["game_id"]][market]["line"],
                                     "p": (pa, pm, round(1 - pa - pm, 9)), "generated_at": now, "tipoff_at": g["tipoff_at"]})
            except Exception as exc:
                out["errors"][c.spec.name] = f"{type(exc).__name__}: {exc}"
                continue
            definition = f"{a['definition']}+spec={c.spec_hash[:8]}"
            lc.append_rows(path, pol, c, rows, definition=definition, dataset_version=s.get("dataset_version", ""), now=now,
                           dry_run=dry_run, out=out)
    if out["status"] == "success" and (out["errors"] or out["skipped"]):
        out["status"] = "partial"
    return out


# --- Research adapter (all-sports research system) ---------------------------------------------------------------------
# Research only (lives here, not in nfl_eval.py, because nfl_eval.py is byte-pinned by the NFL activation definition,
# nfl_live.definition(): editing it would switch NFL live forecasting off). Nothing here writes a store, changes the live models, forecasts, cards or records. The incumbent is the sealed
# production choice reproduced unchanged; candidates are new identities ("nfl-cand-*"). Reserved seasons stay reserved:
# `research_walk` refuses 2022-2025 and reads the forward season only through the sealed, logged read nfl_live uses.
import hashlib
import math
import sqlite3

import numpy as np

from . import backtest, markets as _markets
from .models.base import PregameGame
from .nfl_eval import (DEVELOPMENT, LAST_OPEN, LINE_MARKETS, NFL_PRIOR_POINTS, TEST, VALIDATION, WARMUP, NflElo, NflHomeRate,
                       NflMarginTotal, NflPoints, TestSeasonsLocked, cutoff_for, load_rows)

RESEARCH_MARKETS = ("winner",) + LINE_MARKETS
INCUMBENT_ELO = EloConfig(k=20, home_advantage=50, carry=0.5, mov=True)   # sealed choice d0e51f91 (docs/NFL_PLAN.md)
INCUMBENT_POINTS = PointsConfig(eta=0.05, carry=0.75)                      # sealed score model nfl_points_normal
MIN_FIT_ROWS = 500              # a candidate is fitted for a season only from this many earlier games, else it IS the incumbent
MIN_PHASE_ROWS = 300
HETERO_SHRINK = 150
LEAGUE_WINDOW = 256             # the recent league total = mean total of the last 256 finals
ELO_POINTS = 25.0               # Elo points per point of margin (the usual NFL conversion; fixed, not tuned)
QUOTE_PROVIDERS = ("DraftKings", "Draft Kings")
CANDIDATE_KINDS = {"nfl-cand-blend-v1": "blend", "nfl-cand-hetero-v1": "hetero"}
MODEL_SPECS = {
    "nfl-incumbent": {"role": "incumbent", "version": "sealed-d0e51f91",
                      "description": "Production: NflElo (k20, home advantage 50, carry 0.5, MOV) for the winner; NflPoints "
                                     "(eta 0.05, carry 0.75) margin/total normals for every line market."},
    "nfl-baseline-coin": {"role": "baseline", "version": "coin-v1", "description": "Every side 50 % (no push mass)."},
    "nfl-baseline-homerate": {"role": "baseline", "version": "homerate-league-v1",
                              "description": "Winner = the running home win share (ties half); margin = running home edge, total = the "
                                             "recent league total; incumbent sigmas."},
    "nfl-baseline-elo": {"role": "baseline", "version": "elo-margin-v1",
                         "description": "Market-free Elo: margin = Elo difference / 25 (winner from it), total = recent league total; "
                                        "incumbent sigmas."},
    "nfl-cand-blend-v1": {"role": "candidate", "version": "blend-v1",
                          "description": "Margin = OLS blend of the NflPoints margin and the Elo margin; total = OLS blend of the "
                                         "NflPoints total and the recent league total; coefficients and residual sigmas refitted "
                                         "each season on earlier seasons only. Targets the totals (weakest by point accuracy)."},
    "nfl-cand-hetero-v1": {"role": "candidate", "version": "hetero-v1",
                           "description": "Blend means; sigma by games played (<4, <10, 10+) and predicted level (terciles), shrunk "
                                          "to the pooled sigma, fitted on earlier seasons only."},
}


def research_periods() -> dict:
    return {"warmup": WARMUP, "search": DEVELOPMENT, "validation": VALIDATION, "holdout": TEST}


def research_adapter() -> dict:
    inputs = json.dumps({"models": MODEL_SPECS, "elo": INCUMBENT_ELO.__dict__, "points": INCUMBENT_POINTS.__dict__,
                         "periods": {k: list(v) for k, v in research_periods().items()}, "min_fit": MIN_FIT_ROWS}, sort_keys=True)
    ot = "overtime counts; "
    return {
        "sport": "nfl",
        "data_version": hashlib.sha256(inputs.encode()).hexdigest()[:16],
        "markets": {
            "winner": {"kind": "binary", "settlement": ot + "home wins if home score > away score; a tie voids the winner market"},
            "spread": {"kind": "line", "settlement": ot + "home handicap: home covers (a) when home - away + line > 0, away (b) when "
                                                      "< 0, push on exactly 0; a tied final is a push only on a 0 line"},
            "total": {"kind": "line", "settlement": ot + "over (a) when home + away > line, under (b) when <, push on equal"},
            "team_total_home": {"kind": "line", "settlement": ot + "home points over (a) / under (b) the line, push on equal"},
            "team_total_away": {"kind": "line", "settlement": ot + "away points over (a) / under (b) the line, push on equal"},
        },
        "unsupported": {},
        "caveats": {
            "line_source": "No historical NFL bookmaker line is stored (market_quotes holds DraftKings from 2026-11-15 only; the ESPN "
                           "odds-history raw files used by the sealed test are not on this machine). Search/validation records are "
                           "graded at a market-free REFERENCE line (line_source 'reference_model': Elo-margin spread, recent league "
                           "total, team lines from both), shared by every model - never presented as a bookmaker result. A record "
                           "with a DraftKings quote at or before its cutoff uses that line (line_source 'bookmaker').",
            "team_total_home": "No NFL team-total prices exist at all: reference-line results only.",
            "team_total_away": "No NFL team-total prices exist at all: reference-line results only.",
            "holdout": "2022-2025 were consumed by the one-shot sealed test and stay out of every walk; 2026 is read only for "
                       "forward state through the sealed, logged read (nfl_live's protocol).",
            "orientation": "NFL team_game_stats is empty, so neutral-site home/away cannot be cross-checked against a second source.",
        },
        "periods": {k: list(v) for k, v in research_periods().items()},
        "models": {n: dict(s) for n, s in MODEL_SPECS.items()},
        "walk": research_walk,
        "forecast_upcoming": research_forecast_upcoming,
        "validate_data": validate_data,
    }


class _NflBundle:
    name = "nfl-research-bundle"

    def __init__(self):
        self.elo = NflElo(INCUMBENT_ELO)
        self.pts = NflPoints(INCUMBENT_POINTS)
        self.home = NflHomeRate()
        self.recent: list = []
        self.edge = [0.0, 0]
        self.n: dict = {}
        self.season = None

    def update(self, result) -> None:
        g = result.game
        self.elo.update(result)
        self.pts.update(result)
        self.home.update(result)
        if g.season != self.season:
            self.n, self.season = {}, g.season
        for t in (g.home_team_id, g.away_team_id):
            self.n[t] = self.n.get(t, 0) + 1
        self.recent = (self.recent + [result.home_score + result.away_score])[-LEAGUE_WINDOW:]
        if not g.neutral_site:
            self.edge[0] += result.home_score - result.away_score
            self.edge[1] += 1

    def predict(self, game) -> float:
        return 0.5

    def config(self) -> dict:
        return {}

    def features(self, game) -> dict:
        p_elo = self.elo.predict(game)
        diff = self.elo._diff(game)
        ph, pa = self.pts.expected(game)
        same = game.season == self.season
        return {"elo_p": p_elo, "elo_margin": diff / ELO_POINTS, "pts_m": ph - pa, "pts_t": ph + pa,
                "sd_m": math.sqrt(self.pts.var_margin), "sd_t": math.sqrt(self.pts.var_total),
                "league_total": sum(self.recent) / len(self.recent) if self.recent else 2 * NFL_PRIOR_POINTS,
                "home_p": self.home.predict(game),
                "edge": 0.0 if game.neutral_site or not self.edge[1] else self.edge[0] / self.edge[1],
                "n_h": self.n.get(game.home_team_id, 0) if same else 0, "n_a": self.n.get(game.away_team_id, 0) if same else 0}


_BASE_CACHE: dict = {}


def clear_research_cache() -> None:
    _BASE_CACHE.clear()


def _base(conn: sqlite3.Connection, last: int, sealed=None, access_log=None) -> list:
    """Chronological per-game features (backtest.run) of 2002 … `last`. Seasons past 2021 need the sealed, logged read."""
    rows = load_rows(conn, through=last, sealed=sealed, access_log=access_log, purpose="research forward state")
    key = (last, backtest.dataset_version(rows))
    if key not in _BASE_CACHE:
        scores = {r.game.game_id: (r.home_score, r.away_score) for r in rows}
        out = backtest.run(_NflBundle(), rows, extra=lambda m, g: m.features(g))
        for p in out:
            p["home_score"], p["away_score"] = scores[p["game_id"]]
        _BASE_CACHE.clear()
        _BASE_CACHE[key] = out
    return _BASE_CACHE[key]


def _xrow(market: str, f: dict) -> tuple:
    return (1.0, f["pts_m"], f["elo_margin"]) if market == "spread" else (1.0, f["pts_t"], f["league_total"])


def _target(market: str, f: dict) -> float:
    return f["home_score"] - f["away_score"] if market == "spread" else f["home_score"] + f["away_score"]


def _phase(kind: str, f: dict) -> int:
    n = min(f["n_h"], f["n_a"])
    return 0 if kind == "blend" else 0 if n < 4 else 1 if n < 10 else 2


def _rms_arr(v) -> float:
    return float(math.sqrt(float(np.mean(np.square(v))))) if len(v) else 0.0


class CandFit:
    """OLS blends of the incumbent's margin/total with a second predictor, plus residual sigmas, on earlier-season features."""

    def __init__(self, kind: str, train: list):
        self.kind, self.coef, self.sd, self.cuts = kind, {}, {}, {}
        ph = np.array([_phase(kind, f) for f in train])
        for market in ("spread", "total"):
            X = np.array([_xrow(market, f) for f in train])
            y = np.array([_target(market, f) for f in train], dtype=float)
            self.coef[market] = np.linalg.lstsq(X, y, rcond=None)[0]
            pred = X @ self.coef[market]
            res = y - pred
            sg = _rms_arr(res)
            if kind == "hetero":
                level = np.abs(pred) if market == "spread" else pred
                self.cuts[market] = [float(c) for c in np.quantile(level, [1 / 3, 2 / 3])]
                lv = np.digitize(level, self.cuts[market])
                cells = {}
                for p in (0, 1, 2):
                    for q in (0, 1, 2):
                        m = (ph == p) & (lv == q)
                        n = int(m.sum())
                        s2 = float(np.mean(res[m] ** 2)) if n else sg * sg
                        cells[(p, q)] = math.sqrt((n * s2 + HETERO_SHRINK * sg * sg) / (n + HETERO_SHRINK))
                self.sd[market] = cells
            else:
                self.sd[market] = sg

    def moments(self, f: dict) -> dict:
        p, out = _phase(self.kind, f), {}
        for market, key in (("spread", "m"), ("total", "t")):
            mean = float(np.dot(_xrow(market, f), self.coef[market]))
            if self.kind == "hetero":
                level = abs(mean) if market == "spread" else mean
                sd = self.sd[market][(p, int(np.digitize(level, self.cuts[market])))]
            else:
                sd = self.sd[market]
            out[key] = (mean, sd)
        return out


class _FitBook:
    def __init__(self, base: list):
        self.base, self.cache, self.first, self.last = base, {}, max(WARMUP), LAST_OPEN

    def get(self, kind: str, season: int):
        key = (kind, season)
        if key not in self.cache:
            train = [f for f in self.base if self.first <= f["season"] < season and f["season"] <= self.last]
            self.cache[key] = (CandFit(kind, train), max(f["season"] for f in train)) if len(train) >= MIN_FIT_ROWS else (None, None)
        return self.cache[key]


def _moments(name: str, f: dict, book: "_FitBook") -> tuple[dict, Optional[int]]:
    inc = {"winner": f["elo_p"], "m": (f["pts_m"], f["sd_m"]), "t": (f["pts_t"], f["sd_t"])}
    if name == "nfl-incumbent":
        return inc, None
    if name in CANDIDATE_KINDS:
        fit, through = book.get(CANDIDATE_KINDS[name], f["season"])
        return (inc, None) if fit is None else ({**fit.moments(f), "winner": None}, through)
    if name == "nfl-baseline-coin":
        return {"winner": 0.5, "coin": True}, None
    if name == "nfl-baseline-homerate":
        return {"winner": f["home_p"], "m": (f["edge"], f["sd_m"]), "t": (f["league_total"], f["sd_t"])}, None
    return {"winner": None, "m": (f["elo_margin"], f["sd_m"]), "t": (f["league_total"], f["sd_t"])}, None


def _reference_lines(f: dict) -> dict:
    """Market-free reference lines shared by every model: Elo-margin spread, recent league total, team lines from both."""
    m, t = f["elo_margin"], f["league_total"]
    own = lambda x: math.floor(x) + 0.5      # noqa: E731
    return {"spread": own(-m), "total": own(t), "team_total_home": own((t + m) / 2), "team_total_away": own((t - m) / 2)}


def _quotes(conn: sqlite3.Connection) -> dict:
    if not has_table(conn, "market_quotes"):
        return {}
    out: dict = {}
    for r in conn.execute("SELECT game_id, market, line, price_a, price_b, p_a_devig, provider, retrieved_at FROM market_quotes "
                          f"WHERE sport = 'nfl' AND provider IN ({', '.join('?' * len(QUOTE_PROVIDERS))}) AND price_label = 'current' "
                          "AND market IN ('spread', 'total') AND line IS NOT NULL ORDER BY retrieved_at, quote_id", QUOTE_PROVIDERS):
        out.setdefault((r[0], r[1]), []).append((r[7], {"provider": r[6], "retrieved_at": r[7], "price_a": r[3], "price_b": r[4],
                                                        "line": r[2], "p_a_devig": r[5], "executable": False, "synthetic": False}))
    return out


def _quote_at(quotes: dict, game_id: str, market: str, at: str) -> Optional[dict]:
    best = None
    for t, q in quotes.get((game_id, market), ()):
        if t <= at:
            best = q
    return best


def _records(f: dict, name: str, book: "_FitBook", quotes: dict, *, outcome: bool, cutoff: str) -> list[dict]:
    mm, through = _moments(name, f, book)
    ref = _reference_lines(f)
    dist = None if mm.get("coin") else NflMarginTotal(mm["m"][0], mm["m"][1], mm["t"][0], mm["t"][1])
    base = {"game_id": f["game_id"], "season": f["season"], "start_at": f["tipoff_at"], "forecast_cutoff": cutoff, "model": name,
            "fit_through": through}
    hs, as_ = f.get("home_score"), f.get("away_score")
    out = []
    for market in RESEARCH_MARKETS:
        if market == "winner":
            p = mm["winner"]
            if p is None:
                a, _, b = dist.market("spread", 0.0)
                p = a / (a + b)
            p = min(1 - 1e-9, max(1e-9, float(p)))
            res = None if not outcome else ("void" if hs == as_ else "a" if hs > as_ else "b")
            out.append({**base, "market": "winner", "line": None, "line_source": None, "probs": {"a": p, "b": 1 - p},
                        "outcome": res, "point_pred": None, "point_actual": None, "quote": None})
            continue
        q = _quote_at(quotes, f["game_id"], market, cutoff) if market in ("spread", "total") else None
        line = float(q["line"]) if q else ref[market]
        if dist is None:
            probs, mean, sd = (0.5, 0.0, 0.5), None, None
        else:
            probs = dist.market(market, line)
            mean, sd = {"spread": dist.m, "total": dist.t, "team_total_home": (dist.t + dist.m) / 2,
                        "team_total_away": (dist.t - dist.m) / 2}[market], \
                (math.sqrt(dist.st ** 2 + dist.sm ** 2) / 2 if market.startswith("team") else dist.sm if market == "spread" else dist.st)
        actual = res = None
        if outcome:
            side = _markets.settle(market, line, status="final", home=hs, away=as_)
            res = "push" if side == "mid" else side
            actual = {"spread": hs - as_, "total": hs + as_, "team_total_home": hs, "team_total_away": as_}[market]
        out.append({**base, "market": market, "line": line, "line_source": "bookmaker" if q else "reference_model",
                    "probs": {"a": probs[0], "mid": probs[1], "b": probs[2]}, "outcome": res, "point_pred": mean,
                    "point_actual": actual, "sd": sd, "quote": q})
    return out


def _check_model(name: str) -> None:
    if name not in MODEL_SPECS:
        raise ValueError(f"unknown research model {name!r}")


def research_walk(conn: sqlite3.Connection, seasons, model_name: str, *, sealed=None, access_log=None) -> list[dict]:
    """Chronological forecast records for the games of `seasons`. 2022-2025 are never walked; a season after them (the forward
    season) needs `sealed` and `access_log` (the read is logged before any row is read). Each forecast uses only results available
    at its cutoff and, for candidates, parameters fitted on strictly earlier seasons (never after 2021)."""
    _check_model(model_name)
    seasons = tuple(sorted(set(seasons)))
    if set(seasons) & set(TEST):
        raise TestSeasonsLocked(f"seasons {sorted(set(seasons) & set(TEST))} are the sealed holdout: a walk never opens them")
    base = _base(conn, max(seasons), sealed, access_log)
    book, quotes, wanted = _FitBook(base), _quotes(conn), set(seasons)
    out = []
    for f in base:
        if f["season"] in wanted:
            out += _records(f, model_name, book, quotes, outcome=True, cutoff=f["cutoff_at"])
    return out


def research_forecast_upcoming(conn: sqlite3.Connection, now: str, model_name: str, *, sealed=None, access_log=None) -> list[dict]:
    """Records (no outcome) for scheduled regular/postseason games starting after `now`, from results available at `now`; the
    candidate parameters stop at the last validation season. A forecast for a season after 2021 reads the reserved seasons'
    results as state only through the sealed, logged read."""
    _check_model(model_name)
    games = conn.execute("SELECT * FROM games WHERE status = 'scheduled' AND season_type IN ('regular', 'postseason') AND tipoff_at > ? "
                         "ORDER BY tipoff_at, game_id", (now,)).fetchall()
    through = max([LAST_OPEN] + [g["season"] for g in games])
    book = _FitBook(_base(conn, LAST_OPEN))
    rows = load_rows(conn, through=through, sealed=sealed, access_log=access_log, purpose="research forward state")
    bundle = _NflBundle()
    for r in sorted(rows, key=lambda r: (r.result_available_at, r.game.tipoff_at, r.game.game_id)):
        if r.result_available_at <= now:
            bundle.update(r.result)
    quotes, out = _quotes(conn), []
    for g in games:
        pg = PregameGame(g["game_id"], g["season"], g["season_type"], g["tipoff_at"], g["home_team_id"], g["away_team_id"],
                         bool(g["neutral_site"]), now)
        f = {**bundle.features(pg), "game_id": g["game_id"], "season": g["season"], "tipoff_at": g["tipoff_at"]}
        out += _records(f, model_name, book, quotes, outcome=False, cutoff=now)
    return out


def validate_data(conn: sqlite3.Connection, today: Optional[str] = None) -> dict:
    """Fixture identity, scores, orientation evidence, per-season coverage by market, date ranges and week gaps. Read-only."""
    today = today or utc_now()
    one = lambda sql, *a: conn.execute(sql, a).fetchone()[0]     # noqa: E731
    elig = "FROM games g WHERE g.season_type IN ('regular', 'postseason')"
    fixtures = {"games": one("SELECT COUNT(*) FROM games"), "duplicate_game_ids": one("SELECT COUNT(*) - COUNT(DISTINCT game_id) FROM games"),
                "duplicate_games": one(f"SELECT COUNT(*) FROM (SELECT 1 {elig} GROUP BY g.tipoff_at, g.home_team_id, g.away_team_id "
                                       "HAVING COUNT(*) > 1)"),
                "teams_missing": one("SELECT COUNT(*) FROM games g WHERE NOT EXISTS (SELECT 1 FROM teams t WHERE t.team_id = g.home_team_id) "
                                     "OR NOT EXISTS (SELECT 1 FROM teams t WHERE t.team_id = g.away_team_id)")}
    orient = {"neutral_games": one(f"SELECT COUNT(*) {elig} AND g.neutral_site = 1 AND g.status = 'final'"),
              "team_game_stats_rows": one("SELECT COUNT(*) FROM team_game_stats"),
              "stats_points_mismatch": one("SELECT COUNT(DISTINCT g.game_id) FROM games g JOIN team_game_stats s ON s.game_id = g.game_id "
                                           "WHERE g.status = 'final' AND ((s.is_home = 1 AND (s.team_id != g.home_team_id OR s.points != g.home_score)) "
                                           "OR (s.is_home = 0 AND (s.team_id != g.away_team_id OR s.points != g.away_score)))")}
    finals = {"missing_scores": one("SELECT COUNT(*) FROM games WHERE status = 'final' AND (home_score IS NULL OR away_score IS NULL)"),
              "tied": one(f"SELECT COUNT(*) {elig} AND g.status = 'final' AND g.home_score = g.away_score"),
              "no_result_time": one(f"SELECT COUNT(*) {elig} AND g.status = 'final' AND g.result_available_at IS NULL"),
              "available_before_kickoff": one(f"SELECT COUNT(*) {elig} AND g.status = 'final' AND g.result_available_at <= g.tipoff_at"),
              "scheduled_past_start_over_2d": one(f"SELECT COUNT(*) {elig} AND g.status = 'scheduled' AND g.tipoff_at < "
                                                  "strftime('%Y-%m-%dT%H:%M:%SZ', ?, '-2 days')", today)}
    qfirst = {}
    if has_table(conn, "market_quotes"):
        for r in conn.execute(f"SELECT game_id, market, MIN(retrieved_at) FROM market_quotes WHERE sport = 'nfl' AND provider IN "
                              f"({', '.join('?' * len(QUOTE_PROVIDERS))}) AND price_label = 'current' GROUP BY game_id, market", QUOTE_PROVIDERS):
            qfirst[(r[0], r[1])] = r[2]
    cov: dict = {}
    for r in conn.execute(f"SELECT g.game_id, g.season, g.status, g.tipoff_at, g.neutral_site {elig}"):
        s = cov.setdefault(str(r[1]), {"games": 0, "finals": 0, "first": r[3], "last": r[3], "neutral": 0,
                                       "quotes": {m: 0 for m in ("winner", "spread", "total")}})
        s["games"] += 1
        s["finals"] += r[2] == "final"
        s["first"], s["last"] = min(s["first"], r[3]), max(s["last"], r[3])
        s["neutral"] += r[4]
        for m in s["quotes"]:
            s["quotes"][m] += (r[0], m) in qfirst
    issues = []
    if fixtures["duplicate_game_ids"] or fixtures["duplicate_games"]:
        issues.append(f"duplicate fixtures: {fixtures}")
    if orient["stats_points_mismatch"]:
        issues.append(f"{orient['stats_points_mismatch']} finals whose team_game_stats disagree with games")
    if finals["missing_scores"] or finals["no_result_time"] or finals["available_before_kickoff"]:
        issues.append(f"finals without scores or result time, or available before kickoff: {finals}")
    if fixtures["teams_missing"]:
        issues.append(f"{fixtures['teams_missing']} games reference unknown teams")
    short = {k: v["games"] for k, v in cov.items() if v["finals"] and not (240 <= v["finals"] <= 300) and v["finals"] == v["games"]}
    return {"sport": "nfl", "checked_at": today, "ok": not issues, "issues": issues, "fixtures": fixtures, "orientation": orient,
            "finals": finals, "coverage": dict(sorted(cov.items())), "seasons_outside_240_300_games": short,
            "quote_note": "DraftKings quotes (market_quotes) start 2026-11-15; no earlier bookmaker line is stored."}


def evaluate_research(conn: sqlite3.Connection, periods=("search", "validation"), models=None) -> dict:
    """Score of every research model per market over the search and validation periods (never 2022-2025) and the paired
    difference against the incumbent. Record scoring is shared with the NCAAB adapter (sport-agnostic)."""
    from . import ncaab_cards as _rec
    models, rp = list(models or MODEL_SPECS), research_periods()
    out: dict = {"periods": {p: list(rp[p]) for p in periods}, "line_source": "reference_model (Elo-margin spread, recent league total, "
                 "team lines from both; shared by every model)", "results": {}, "vs_incumbent": {}}
    recs = {m: research_walk(conn, [s for p in periods for s in rp[p]], m) for m in models}
    for p in periods:
        seasons = set(rp[p])
        for m in models:
            for market in RESEARCH_MARKETS:
                sel = [r for r in recs[m] if r["season"] in seasons and r["market"] == market]
                if sel:
                    out["results"].setdefault(p, {}).setdefault(market, {})[m] = _rec.score_records(sel)
                    if m != "nfl-incumbent":
                        inc = [r for r in recs["nfl-incumbent"] if r["season"] in seasons and r["market"] == market]
                        out["vs_incumbent"].setdefault(p, {}).setdefault(market, {})[m] = _rec.compare_records(sel, inc)
    return out


def evaluate_forward(conn: sqlite3.Connection, sealed: dict, access_log: Path, models=None) -> dict:
    """The forward season's games with a DraftKings quote at or before the cutoff, at the quote's line vs the no-vig price.
    Needs the sealed, logged read of the reserved seasons (state only)."""
    from . import ncaab_cards as _rec
    seasons = [s for (s,) in conn.execute("SELECT DISTINCT season FROM games WHERE season > ?", (TEST[-1],))]
    out: dict = {"seasons": seasons, "results": {}}
    if not seasons:
        return out
    recs = {m: research_walk(conn, seasons, m, sealed=sealed, access_log=access_log) for m in (models or MODEL_SPECS)}
    for market in ("spread", "total"):
        inc = [r for r in recs["nfl-incumbent"] if r["market"] == market]
        for m, rs in recs.items():
            out["results"].setdefault(market, {})[m] = _rec.quote_benchmark([r for r in rs if r["market"] == market], inc)
    return out


def research_main(argv=None) -> int:
    """`python -m app.nfl_challengers research --out-dir D [--sealed PATH --access-log PATH]`: data validation and the search +
    validation walks as JSON in D. The forward benchmark runs only when a sealed choice (JSON file) and an access-log path are
    given; the read of the reserved seasons is then logged there. Never walks 2022-2025; refuses the live data folder."""
    import argparse
    import sys
    from . import config
    ap = argparse.ArgumentParser(prog="python -m app.nfl_challengers research")
    ap.add_argument("stage", choices=("research",))
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--sealed", type=Path, default=None)
    ap.add_argument("--access-log", type=Path, default=None)
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    if config.protected_data_dir(config.data_dir()):
        raise SystemExit(f"Refusing to evaluate against the live data folder {config.data_dir()}; set NBA_DATA_DIR to a copy")
    conn = sqlite3.connect(f"file:{config.db_path('nfl')}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    args.out_dir.mkdir(parents=True, exist_ok=True)
    bodies = [("validate_data", validate_data(conn)), ("research", evaluate_research(conn))]
    if args.sealed and args.access_log:
        bodies.append(("forward", evaluate_forward(conn, json.loads(args.sealed.read_text()), args.access_log)))
    for name, body in bodies:
        (args.out_dir / f"nfl_{name}.json").write_text(json.dumps(body, indent=1, default=str))
    print(json.dumps({"stage": "research", "output_dir": str(args.out_dir)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(research_main())
