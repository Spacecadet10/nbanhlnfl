"""Which NCAAB games get a Telegram card: owner decision Nov 17, 2026 ("top 4 by gap, let's test it").

Once a day at 11:00 AM Eastern, every tracked NCAAB game tipping off at least two hours later that Eastern day is priced by
the shipped model as of that moment and compared with the latest saved sportsbook quote (no-vig) for the winner, spread and
total. A game's gap is the largest (app % - sportsbook %) of the model's own side in those markets. The four games with the
largest positive gap are saved, with every candidate and its numbers, append-only in the delivery ledger. Only those games
get a card, at the usual two-hour forecast time, from the official forecast.

UNTESTED: no NCAAB evidence shows that a large gap is an edge (DraftKings' 2026 lines beat the model); the saved candidates
let the selected games be compared later with the ones that were not sent. Nothing here changes a forecast, a grade or a record.
"""
import json
import sqlite3
from datetime import datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

RULE = "ncaab-top4-gap-v1"
TOP_N = 4
SELECT_AT = time(11, 0)                 # Eastern
LEAD = timedelta(hours=2)               # a game whose two-hour forecast window is already open cannot be selected
MARKETS = ("winner", "spread", "total")
MAX_QUOTE_AGE = timedelta(hours=6)      # an older saved quote is not a price as of the selection (review finding 2)
EASTERN = ZoneInfo("America/Toronto")

SCHEMA = '''
CREATE TABLE IF NOT EXISTS ncaab_card_selections (
  et_date TEXT PRIMARY KEY,              -- the Eastern day the selection covers
  rule TEXT NOT NULL,
  selected_at TEXT NOT NULL,             -- information time: model and quotes as of this moment, never later
  status TEXT NOT NULL CHECK (status IN ('ok', 'error')), error TEXT,
  model_version TEXT,
  selected TEXT NOT NULL,                -- JSON list of game ids, largest gap first
  candidates TEXT NOT NULL               -- JSON list: every game considered, its per-market numbers, or why it had none
);
CREATE TRIGGER IF NOT EXISTS ncaab_card_selections_no_update BEFORE UPDATE ON ncaab_card_selections
BEGIN SELECT RAISE(ABORT, 'ncaab_card_selections is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ncaab_card_selections_no_delete BEFORE DELETE ON ncaab_card_selections
BEGIN SELECT RAISE(ABORT, 'ncaab_card_selections is append-only'); END;
'''


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _market_entry(conn, dist, game_row, market: str, as_of: str) -> dict:
    from . import market_live, odds
    from .ncaab_app import LineNotModelled
    tip = game_row["tipoff_at"]
    if market == "winner":
        q = odds.winner_quote(conn, "ncaab", game_row["game_id"], as_of)
        if q is None or q["retrieved_at"] >= tip:
            return {"market": market, "reason": "no moneyline saved"}
        if _parse(q["retrieved_at"]) < _parse(as_of) - MAX_QUOTE_AGE:
            return {"market": market, "reason": "latest moneyline older than 6 hours"}
        pa, _, pb = dist.winner()
        book_a, line, qid, provider, at = q["p_home"], None, q["quote_id"], q["provider"], q["retrieved_at"]
    else:
        q = market_live.latest_quote(conn, game_row["game_id"], market, as_of)
        if q is None or q["retrieved_at"] >= tip:
            return {"market": market, "reason": "no quote saved"}
        if _parse(q["retrieved_at"]) < _parse(as_of) - MAX_QUOTE_AGE:
            return {"market": market, "reason": "latest quote older than 6 hours"}
        try:
            pa, _, pb = dist.market(market, q["line"])
        except LineNotModelled:
            return {"market": market, "reason": "the model cannot price the sportsbook's line"}
        book_a, line, qid, provider, at = q["p_a_devig"], q["line"], q["quote_id"], q["provider"], q["retrieved_at"]
    side = "a" if pa >= pb else "b"
    app, book = (pa, book_a) if side == "a" else (pb, 1 - book_a)
    return {"market": market, "side": side, "line": line, "app": round(app, 6), "book": round(book, 6),
            "gap": round((app - book) * 100, 1), "quote_id": qid, "provider": provider, "quote_retrieved_at": at}


def compute(source: sqlite3.Connection, now: datetime) -> dict:
    """The selection for the Eastern day of `now`, made with information available at `now` only (results through the model's
    own cutoff, quotes retrieved at or before `now`). Pure: writes nothing."""
    from . import live
    as_of = _ts(now)
    day = now.astimezone(EASTERN).date()
    eng = live.engine(source)
    if eng.sport != "ncaab":
        raise ValueError("The NCAAB card selection needs an NCAAB store")
    _, model = eng.primary(source)
    model.as_of = as_of                 # the model reads nothing newer than the selection time
    types = eng.eligible_types
    rows = source.execute(
        f"SELECT * FROM games WHERE status = 'scheduled' AND tipoff_at >= ? AND season_type IN ({', '.join('?' * len(types))}) "
        "ORDER BY tipoff_at, game_id", (_ts(now + LEAD), *types)).fetchall()
    candidates = []
    for g in rows:
        if _parse(g["tipoff_at"]).astimezone(EASTERN).date() != day:
            continue
        entry = {"game_id": g["game_id"], "tipoff_at": g["tipoff_at"]}
        try:
            dist = model.distribution(eng.pregame(g))
            entry["markets"] = [_market_entry(source, dist, g, m, as_of) for m in MARKETS]
        except Exception as exc:        # one game the model cannot price is reported, never a reason to card every game
            entry["error"] = f"{type(exc).__name__}: {exc}"[:300]
            entry["markets"] = []
        priced = [m for m in entry["markets"] if m.get("gap") is not None]
        best = max(priced, key=lambda m: (m["gap"], -MARKETS.index(m["market"])), default=None)
        entry["gap"] = best["gap"] if best else None
        entry["best_market"] = best["market"] if best else None
        candidates.append(entry)
    ranked = sorted((c for c in candidates if c["gap"] is not None and c["gap"] > 0),
                    key=lambda c: (-c["gap"], c["tipoff_at"], c["game_id"]))
    return {"et_date": day.isoformat(), "rule": RULE, "selected_at": as_of, "status": "ok", "error": None,
            "model_version": getattr(model, "version", None), "selected": [c["game_id"] for c in ranked[:TOP_N]],
            "candidates": candidates}


def _row(conn, day: str) -> Optional[dict]:
    r = conn.execute("SELECT * FROM ncaab_card_selections WHERE et_date = ?", (day,)).fetchone()
    if r is None:
        return None
    out = dict(r)
    out["selected"], out["candidates"] = json.loads(out["selected"]), json.loads(out["candidates"])
    return out


def ensure(ledger: sqlite3.Connection, source: sqlite3.Connection, now: datetime) -> Optional[dict]:
    """Today's saved selection; made and saved once, at the first call at or after 11:00 AM Eastern. None before then (no
    NCAAB card goes out). A failed selection is saved as an error with no games: that day gets no NCAAB cards, never all of them."""
    ledger.executescript(SCHEMA)
    local = now.astimezone(EASTERN)
    day = local.date().isoformat()
    saved = _row(ledger, day)
    if saved is not None or local.time() < SELECT_AT:
        return saved
    try:
        sel = compute(source, now)
    except Exception as exc:
        sel = {"et_date": day, "rule": RULE, "selected_at": _ts(now), "status": "error",
               "error": f"{type(exc).__name__}: {exc}"[:300], "model_version": None, "selected": [], "candidates": []}
    # INSERT OR IGNORE only (an OR REPLACE would delete a row without firing the delete trigger)
    ledger.execute("INSERT OR IGNORE INTO ncaab_card_selections (et_date, rule, selected_at, status, error, model_version, "
                   "selected, candidates) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   (sel["et_date"], sel["rule"], sel["selected_at"], sel["status"], sel["error"], sel["model_version"],
                    json.dumps(sel["selected"]), json.dumps(sel["candidates"], sort_keys=True)))
    return _row(ledger, day)


# --- NCAAB research adapter (all-sports research system) ---------------------------------------------------------------
# Lives here, not in ncaab_eval.py: ncaab_eval.py is hashed by ncaab_challengers.fit_code_version(), so editing it would change
# the live challenger fingerprint and definition. This module is hashed by no live fingerprint (tests/test_research_fingerprints.py).
import functools
import hashlib
import math
from pathlib import Path


def _research_imports() -> None:
    """The research section's heavy dependencies (numpy, the college models, the backtest walk, ncaab_eval) load on the first
    research call, not when telegram imports this module for the cards: importing ncaab_cards stays as cheap as before and
    cannot take part in an import cycle through app.models.college."""
    if "college" in globals():
        return
    import numpy
    from . import backtest as _bt, config as _cfg, markets as _mk, metrics as _mt, ncaab_eval
    from .db import has_table as _ht, store_sport as _ss, utc_now as _un
    from .models import college as _col
    from .models.base import PregameGame as _pg
    from .models.college import CollegeEff as _ce, CollegeEffConfig as _cec, CollegeElo as _el, CollegeEloConfig as _elc, \
        RunningPoints as _rp
    globals().update(np=numpy, backtest=_bt, config=_cfg, markets=_mk, metrics=_mt, _ev=ncaab_eval, has_table=_ht,
                     store_sport=_ss, utc_now=_un, college=_col, PregameGame=_pg, CollegeEff=_ce, CollegeEffConfig=_cec,
                     CollegeElo=_el, CollegeEloConfig=_elc, RunningPoints=_rp)


def _research(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        _research_imports()
        return fn(*args, **kwargs)
    return wrapper

# Research only: nothing here writes a store, changes a live model, a forecast, a card or an official record. Candidate names
# are new model identities ("ncaab-cand-*"); the incumbent is the sealed production decision, reproduced unchanged.
RESEARCH_MARKETS = ("winner", "spread", "total", "team_total_home", "team_total_away")   # = college.LINE_MARKETS (tested)
FAR = "9999-12-31T00:00:00Z"
MIN_FIT_ROWS = 300              # a candidate is fitted for a season only from at least this many earlier games, else it IS the incumbent
MIN_PHASE_ROWS = 200
EARLY_GAMES = 6                 # "early season": the fewer-played team has played fewer than this many games this season
HETERO_SHRINK = 300             # rows of weight the pooled sigma carries against a cell's own (phase x level) sigma
QUOTE_PROVIDER = "DraftKings"
CANDIDATE_KINDS = {"ncaab-cand-blend-v1": "blend", "ncaab-cand-phase-v1": "phase", "ncaab-cand-hetero-v1": "hetero"}
MODEL_SPECS = {
    "ncaab-incumbent": {"role": "incumbent", "version": "sealed-decision",
                        "description": "The production decision: winner (Platt-calibrated) and each line market from the model the "
                                       "sealed decision names (college_eff margin/team totals, running_points total), frozen sigmas."},
    "ncaab-baseline-coin": {"role": "baseline", "version": "coin-v1", "description": "Every side 50 % (no push mass)."},
    "ncaab-baseline-elo": {"role": "baseline", "version": "elo-runningpoints-v1",
                           "description": "Market-free baselines of the preregistered plan: college_elo winner and margin/12, "
                                          "running_points totals and team totals."},
    "ncaab-baseline-league": {"role": "baseline", "version": "league-v1",
                              "description": "Home advantage only (0 at neutral sites) for winner and margin; league-average total "
                                             "(and half of it per team); frozen sigmas."},
    "ncaab-cand-blend-v1": {"role": "candidate", "version": "blend-v1",
                            "description": "Margin and total means = OLS blend of college_eff and the Elo margin / running-points "
                                           "total, coefficients and residual sigma refitted each season on earlier seasons only."},
    "ncaab-cand-phase-v1": {"role": "candidate", "version": "phase-v1",
                            "description": "As blend, with separate coefficients and sigma for early-season games (a team has played "
                                           f"fewer than {EARLY_GAMES} games) and established ones."},
    "ncaab-cand-hetero-v1": {"role": "candidate", "version": "hetero-v1",
                             "description": "Blend means; sigma varies with games played (<4, <10, 10+) and predicted level (terciles of "
                                            "|margin| / total), shrunk to the pooled sigma, fitted on earlier seasons only."},
}


@_research
def research_periods() -> dict:
    p = _ev.PERIODS
    return {"warmup": tuple(p["warmup"]), "search": tuple(p["development"]), "validation": tuple(p["validation"]),
            "holdout": tuple(p["test"])}


@_research
def forward_from() -> int:
    return max(_ev.PERIODS["test"]) + 1


@_research
def research_adapter() -> dict:
    """What the research system consumes (docs/AUTORESEARCH.md): markets with settlement rules, periods, models, `walk` and
    `forecast_upcoming`. The input hash covers the sealed decision, the feature version and every model's spec."""
    try:
        sha = college.load_decision()["sha256"]
    except (college.FrozenDecisionError, OSError):
        sha = "no-sealed-decision"
    inputs = json.dumps({"feature": _ev.FEATURE_VERSION, "decision": sha, "models": MODEL_SPECS, "periods": research_periods()},
                        sort_keys=True)
    ot = "overtime counts (full-game score); neutral-site games keep the stored home/away; "
    return {
        "sport": "ncaab",
        "data_version": hashlib.sha256(inputs.encode()).hexdigest()[:16],
        "markets": {
            "winner": {"kind": "binary", "settlement": ot + "home wins if stored home score > away score (a tie voids)"},
            "spread": {"kind": "line", "settlement": ot + "home handicap: home covers (a) when home - away + line > 0, away (b) "
                                                      "when < 0, push on exactly 0 (whole-number lines only)"},
            "total": {"kind": "line", "settlement": ot + "over (a) when home + away > line, under (b) when <, push on equal"},
            "team_total_home": {"kind": "line", "settlement": ot + "home points over (a) / under (b) the line, push on equal"},
            "team_total_away": {"kind": "line", "settlement": ot + "away points over (a) / under (b) the line, push on equal"},
        },
        "unsupported": {},
        "caveats": {
            "line_source": "No historical NCAAB bookmaker line exists before 2026-11-17 (market_quotes) and the after-game DraftKings "
                           "lines cover only 2025-11-24 onward (the holdout). Search/validation records are therefore graded at "
                           "a market-free REFERENCE line (line_source 'reference_model': the Elo-margin / running-points line, "
                           "shared by every model so comparisons are paired) - never presented as a bookmaker result. A record "
                           "with a DraftKings quote retrieved at or before its cutoff uses that line (line_source 'bookmaker').",
            "team_total_home": "No bookmaker price for team totals is stored for any game: reference-line results only.",
            "team_total_away": "No bookmaker price for team totals is stored for any game: reference-line results only.",
            "validation_reuse": "2022-2024 already selected the production decision; candidate parameters are fitted on earlier "
                                "seasons only, but the validation look is a second look, not a fresh holdout.",
        },
        "periods": {k: list(v) for k, v in research_periods().items()},      # warmup = burn-in, never scored
        "models": {n: dict(s) for n, s in MODEL_SPECS.items()},
        "walk": research_walk,
        "forecast_upcoming": research_forecast_upcoming,
        "validate_data": validate_data,
    }


# Features: everything a model may know at a game's cutoff, from one chronological bundle of rating states.
class _Bundle:
    name = "ncaab-research-bundle"

    def __init__(self, decision: dict, box: dict):
        self.elo = CollegeElo(CollegeEloConfig(**decision["college_elo_config"]))
        self.eff = CollegeEff(CollegeEffConfig(**decision["college_eff_config"]), box)
        self.pts = RunningPoints()
        self.n: dict = {}
        self.season: Optional[int] = None
        self.total = [0.0, 0]

    def update(self, result) -> None:
        g = result.game
        self.elo.update(result)
        self.eff.update(result)
        self.pts.update(result)
        if g.season != self.season:
            self.n, self.season = {}, g.season
        for team in (g.home_team_id, g.away_team_id):
            self.n[team] = self.n.get(team, 0) + 1
        self.total[0] += result.home_score + result.away_score
        self.total[1] += 1

    def predict(self, game) -> float:
        return 0.5

    def config(self) -> dict:
        return {}

    def features(self, game) -> dict:
        mh, ma = self.eff.expected(game)
        ph, pa = self.pts.expected(game)
        same = game.season == self.season
        return {"elo_margin": self.elo.margin(game), "elo_p": self.elo.predict(game), "mu_h": mh, "mu_a": ma, "pts_h": ph,
                "pts_a": pa, "n_h": self.n.get(game.home_team_id, 0) if same else 0,
                "n_a": self.n.get(game.away_team_id, 0) if same else 0,
                "league_total": self.total[0] / self.total[1] if self.total[1] else 140.0, "ha": self.eff.ha,
                "neutral": bool(game.neutral_site)}


_BASE_CACHE: dict = {}


@_research
def clear_research_cache() -> None:
    _BASE_CACHE.clear()


@_research
def _base(conn: sqlite3.Connection, last: int) -> tuple[list, dict]:
    """Chronological per-game features (backtest.run: a result reaches the bundle only once available at the game's cutoff)
    for every eligible game of the warm-up through `last`, plus the sealed decision. Scores ride along for fitting EARLIER
    seasons and for settlement only."""
    if store_sport(conn) != "ncaab":
        raise _ev.NcaabEvaluationError("NCAAB research needs the NCAAB store")
    d = college.load_decision()
    rows, box = college.rows_at(conn, FAR, seasons=range(min(_ev.PERIODS["warmup"]), last + 1))
    key = (last, college.rows_version(rows), d["sha256"])
    if key not in _BASE_CACHE:
        scores = {r.game.game_id: (r.home_score, r.away_score) for r in rows}
        out = backtest.run(_Bundle(d, box), rows, extra=lambda m, g: m.features(g))
        for p in out:
            p["home_score"], p["away_score"] = scores[p["game_id"]]
        _BASE_CACHE.clear()
        _BASE_CACHE[key] = out
    return _BASE_CACHE[key], d


@_research
def _lstsq(X, y):
    return np.linalg.lstsq(X, y, rcond=None)[0]


@_research
def _xrow(market: str, f: dict) -> tuple:
    if market == "spread":
        return (1.0, f["mu_h"] - f["mu_a"], f["elo_margin"])
    return (1.0, f["mu_h"] + f["mu_a"], f["pts_h"] + f["pts_a"])


@_research
def _target(market: str, f: dict) -> float:
    return f["home_score"] - f["away_score"] if market == "spread" else f["home_score"] + f["away_score"]


@_research
def _phase(kind: str, f: dict) -> int:
    n = min(f["n_h"], f["n_a"])
    if kind == "hetero":
        return 0 if n < 4 else 1 if n < 10 else 2
    return 0 if kind == "blend" else int(n >= EARLY_GAMES)


class CandFit:
    """A candidate fitted on `train` (feature dicts WITH scores, all from seasons before the games it will price)."""

    def __init__(self, kind: str, train: list):
        self.kind = kind
        self.coef: dict = {}
        self.sd: dict = {}
        self.cuts: dict = {}
        ph = np.array([_phase(kind, f) for f in train])
        mean_pred = {}
        for market in ("spread", "total"):
            X = np.array([_xrow(market, f) for f in train])
            y = np.array([_target(market, f) for f in train], dtype=float)
            glob = _lstsq(X, y)
            coefs = {}
            for p in (0, 1, 2):
                mask = ph == p
                coefs[p] = _lstsq(X[mask], y[mask]) if kind == "phase" and mask.sum() >= MIN_PHASE_ROWS else glob
            self.coef[market] = coefs
            pred = np.array([X[i] @ coefs[ph[i]] for i in range(len(train))])
            mean_pred[market] = pred
            res = y - pred
            sg = _ev._rms(res)
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
            elif kind == "phase":
                self.sd[market] = {p: (_ev._rms(res[ph == p]) if (ph == p).sum() >= MIN_PHASE_ROWS else sg) for p in (0, 1, 2)}
            else:
                self.sd[market] = {p: sg for p in (0, 1, 2)}
        mh = (mean_pred["total"] + mean_pred["spread"]) / 2
        ma = (mean_pred["total"] - mean_pred["spread"]) / 2
        hs = np.array([f["home_score"] for f in train], dtype=float)
        as_ = np.array([f["away_score"] for f in train], dtype=float)
        sg_t = _ev._rms(np.concatenate([hs - mh, as_ - ma]))
        self.sd_team = {p: (_ev._rms(np.concatenate([(hs - mh)[ph == p], (as_ - ma)[ph == p]]))
                            if kind != "blend" and (ph == p).sum() >= MIN_PHASE_ROWS else sg_t) for p in (0, 1, 2)}

    def moments(self, f: dict) -> dict:
        p = _phase(self.kind, f)
        out, mean = {}, {}
        for market in ("spread", "total"):
            x = _xrow(market, f)
            mean[market] = float(sum(a * b for a, b in zip(x, self.coef[market][p])))
            if self.kind == "hetero":
                level = abs(mean[market]) if market == "spread" else mean[market]
                sd = self.sd[market][(p, int(np.digitize(level, self.cuts[market])))]
            else:
                sd = self.sd[market][p]
            out[market] = (mean[market], sd)
        out["team_total_home"] = ((mean["total"] + mean["spread"]) / 2, self.sd_team[p])
        out["team_total_away"] = ((mean["total"] - mean["spread"]) / 2, self.sd_team[p])
        out["winner"] = college._phi(out["spread"][0] / out["spread"][1])
        return out


class _FitBook:
    """Candidate fits by (kind, season), from base records of seasons strictly before it (and never after the last validation season)."""

    def __init__(self, base: list):
        self.base, self.cache = base, {}
        self.first = max(_ev.PERIODS["warmup"])
        self.last = max(_ev.PERIODS["validation"])

    def get(self, kind: str, season: int):
        key = (kind, season)
        if key not in self.cache:
            train = [f for f in self.base if self.first <= f["season"] < season and f["season"] <= self.last]
            self.cache[key] = (CandFit(kind, train), max(f["season"] for f in train)) if len(train) >= MIN_FIT_ROWS else (None, None)
        return self.cache[key]


@_research
def _incumbent(f: dict, d: dict) -> dict:
    sig = d["sigmas"]
    means = {"elo_margin": f["elo_margin"], "eff": (f["mu_h"], f["mu_a"]), "points": (f["pts_h"], f["pts_a"])}
    if d["winner_model"] == "college_elo":
        p = f["elo_p"]
    else:
        p = college._phi((f["mu_h"] - f["mu_a"]) / sig["eff_margin"])
    out = {"winner": float(college.platt(p, d.get("winner_calibration")))}
    for market in college.LINE_MARKETS:
        kind = d["markets"][market]["model"]
        if kind != "research":
            out[market] = college.market_moments(kind, means, sig, market)
    return out


@_research
def _baseline_moments(name: str, f: dict, d: dict) -> dict:
    sig = d["sigmas"]
    if name == "ncaab-baseline-coin":
        return {"winner": 0.5, **{m: "coin" for m in college.LINE_MARKETS}}
    ha = 0.0 if f["neutral"] else f["ha"]
    if name == "ncaab-baseline-elo":
        means = {"elo_margin": f["elo_margin"], "points": (f["pts_h"], f["pts_a"])}
        out = {"winner": f["elo_p"], "spread": college.market_moments("elo_margin", means, sig, "spread")}
        for m in ("total", "team_total_home", "team_total_away"):
            out[m] = college.market_moments("running_points", means, sig, m)
        return out
    half = f["league_total"] / 2
    return {"winner": college._phi(ha / sig["eff_margin"]), "spread": (ha, sig["eff_margin"]),
            "total": (f["league_total"], sig["points_total"]), "team_total_home": (half + ha / 2, sig["points_home"]),
            "team_total_away": (half - ha / 2, sig["points_away"])}


@_research
def _reference_lines(f: dict) -> dict:
    """The market-free reference line of each line market (shared by every model): the baselines' own half-point lines."""
    return {"spread": college.own_line("spread", f["elo_margin"]), "total": college.own_line("total", f["pts_h"] + f["pts_a"]),
            "team_total_home": college.own_line("team_total_home", f["pts_h"]),
            "team_total_away": college.own_line("team_total_away", f["pts_a"])}


@_research
def _moments(name: str, f: dict, d: dict, book: "_FitBook") -> tuple[dict, Optional[int]]:
    if name == "ncaab-incumbent":
        return _incumbent(f, d), None
    if name in CANDIDATE_KINDS:
        fit, through = book.get(CANDIDATE_KINDS[name], f["season"])
        if fit is None:
            return _incumbent(f, d), None
        return fit.moments(f), through
    return _baseline_moments(name, f, d), None


@_research
def _quotes(conn: sqlite3.Connection) -> dict:
    """(game_id, market) -> [(retrieved_at, quote dict)] of DraftKings 'current' spread/total quotes, oldest first."""
    if not has_table(conn, "market_quotes"):
        return {}
    out: dict = {}
    for r in conn.execute("SELECT game_id, market, line, price_a, price_b, p_a_devig, provider, retrieved_at FROM market_quotes "
                          "WHERE provider = ? AND price_label = 'current' AND market IN ('spread', 'total') AND line IS NOT NULL "
                          "ORDER BY retrieved_at, quote_id", (QUOTE_PROVIDER,)):
        out.setdefault((r[0], r[1]), []).append((r[7], {"provider": r[6], "retrieved_at": r[7], "price_a": r[3], "price_b": r[4],
                                                        "line": r[2], "p_a_devig": r[5], "executable": False,
                                                        "synthetic": False}))
    return out


@_research
def _quote_at(quotes: dict, game_id: str, market: str, at: str) -> Optional[dict]:
    best = None
    for t, q in quotes.get((game_id, market), ()):
        if t <= at:
            best = q
    return best


@_research
def _records(f: dict, name: str, d: dict, book: "_FitBook", quotes: dict, *, outcome: bool, cutoff: str) -> list[dict]:
    mm, through = _moments(name, f, d, book)
    ref = _reference_lines(f)
    base = {"game_id": f["game_id"], "season": f["season"], "start_at": f["tipoff_at"], "forecast_cutoff": cutoff, "model": name,
            "fit_through": through}
    out = []
    for market in RESEARCH_MARKETS:
        if market not in mm:
            continue
        hs, as_ = f.get("home_score"), f.get("away_score")
        if market == "winner":
            p = min(1 - 1e-9, max(1e-9, float(mm["winner"])))
            res = None if not outcome else ("void" if hs == as_ else "a" if hs > as_ else "b")
            out.append({**base, "market": "winner", "line": None, "line_source": None, "probs": {"a": p, "b": 1 - p},
                        "outcome": res, "point_pred": None, "point_actual": None, "quote": None})
            continue
        q = _quote_at(quotes, f["game_id"], market, cutoff) if market in ("spread", "total") else None
        line = float(q["line"]) if q else ref[market]
        if mm[market] == "coin":
            probs, mean, sd = (0.5, 0.0, 0.5), None, None
        else:
            mean, sd = mm[market]
            probs = college.market_probs(market, line, mean, sd)
        actual = None
        res = None
        if outcome:
            side = markets.settle(market, line, status="final", home=hs, away=as_)
            res = "push" if side == "mid" else side
            actual = {"spread": hs - as_, "total": hs + as_, "team_total_home": hs, "team_total_away": as_}[market]
        out.append({**base, "market": market, "line": line, "line_source": "bookmaker" if q else "reference_model",
                    "probs": {"a": probs[0], "mid": probs[1], "b": probs[2]}, "outcome": res, "point_pred": mean,
                    "point_actual": actual, "sd": sd, "quote": q})
    return out


@_research
def _check_model(name: str) -> None:
    if name not in MODEL_SPECS:
        raise _ev.NcaabEvaluationError(f"unknown research model {name!r}")


@_research
def research_walk(conn: sqlite3.Connection, seasons, model_name: str) -> list[dict]:
    """Chronological forecast records of `model_name` for the games of `seasons` (search, validation, warm-up or forward
    seasons; the holdout is refused). Each forecast uses only results available at its cutoff (backtest.run) and, for
    candidates, parameters fitted on strictly earlier seasons."""
    _check_model(model_name)
    seasons = tuple(sorted(set(seasons)))
    holdout = set(research_periods()["holdout"])
    if not seasons or set(seasons) & holdout:
        raise _ev.NcaabEvaluationError(f"seasons {sorted(set(seasons) & holdout) or 'none'}: the holdout is not opened by a walk")
    base, d = _base(conn, max(seasons))
    book = _FitBook(base)
    wanted = set(seasons)
    quotes = _quotes(conn)
    out = []
    for f in base:
        if f["season"] in wanted:
            out += _records(f, model_name, d, book, quotes, outcome=True, cutoff=f["cutoff_at"])
    return out


@_research
def research_forecast_upcoming(conn: sqlite3.Connection, now: str, model_name: str) -> list[dict]:
    """Records (no outcome) for every scheduled Division I game starting after `now`, from results available at `now` and
    candidate parameters fitted on seasons up to the last validation season only."""
    _check_model(model_name)
    if store_sport(conn) != "ncaab":
        raise _ev.NcaabEvaluationError("NCAAB research needs the NCAAB store")
    base, d = _base(conn, max(_ev.PERIODS["validation"]))
    book = _FitBook(base)
    rows, box = college.rows_at(conn, now)
    bundle = _Bundle(d, box)
    for r in sorted(rows, key=lambda r: (r.result_available_at, r.game.tipoff_at, r.game.game_id)):
        bundle.update(r.result)
    quotes = _quotes(conn)
    out = []
    q = ("SELECT g.* FROM games g JOIN ncaab_game_context c ON c.game_id = g.game_id WHERE g.status = 'scheduled' AND c.both_d1 = 1 "
         "AND g.season_type IN ('regular', 'postseason') AND g.tipoff_at > ? ORDER BY g.tipoff_at, g.game_id")
    for g in conn.execute(q, (now,)):
        game = PregameGame(g["game_id"], g["season"], g["season_type"], g["tipoff_at"], g["home_team_id"], g["away_team_id"],
                           bool(g["neutral_site"]), now)
        f = {**bundle.features(game), "game_id": g["game_id"], "season": g["season"], "tipoff_at": g["tipoff_at"]}
        out += _records(f, model_name, d, book, quotes, outcome=False, cutoff=now)
    return out


# --- Data validation --------------------------------------------------------------------------------------------------
@_research
def validate_data(conn: sqlite3.Connection, today: Optional[str] = None) -> dict:
    """Fixture identity, neutral-site orientation, finals with scores, missing inputs, and per-season coverage by market. Read-only."""
    if store_sport(conn) != "ncaab":
        raise _ev.NcaabEvaluationError("NCAAB validation needs the NCAAB store")
    today = today or utc_now()
    one = lambda sql, *a: conn.execute(sql, a).fetchone()[0]     # noqa: E731
    elig = ("FROM games g JOIN ncaab_game_context c ON c.game_id = g.game_id WHERE c.both_d1 = 1 "
            "AND g.season_type IN ('regular', 'postseason')")
    issues, notes = [], []
    scored_from = min(_ev.PERIODS["development"])
    fixtures = {"games": one("SELECT COUNT(*) FROM games"), "duplicate_game_ids": one("SELECT COUNT(*) - COUNT(DISTINCT game_id) FROM games"),
                "duplicate_games": one(f"SELECT COUNT(*) FROM (SELECT 1 {elig} GROUP BY g.tipoff_at, g.home_team_id, g.away_team_id "
                                       "HAVING COUNT(*) > 1)"),
                "duplicate_games_by_season": {str(r[0]): r[1] for r in conn.execute(
                    f"SELECT season, COUNT(*) FROM (SELECT g.season {elig} GROUP BY g.tipoff_at, g.home_team_id, g.away_team_id "
                    "HAVING COUNT(*) > 1) GROUP BY season")},
                "swapped_duplicates": one(f"SELECT COUNT(*) FROM games a JOIN games b ON a.home_team_id = b.away_team_id AND "
                                          "a.away_team_id = b.home_team_id AND substr(a.tipoff_at, 1, 10) = substr(b.tipoff_at, 1, 10) "
                                          "AND a.game_id < b.game_id AND a.season_type IN ('regular', 'postseason') "
                                          "AND b.season_type IN ('regular', 'postseason')"),
                "teams_missing": one("SELECT COUNT(*) FROM games g WHERE NOT EXISTS (SELECT 1 FROM teams t WHERE t.team_id = g.home_team_id) "
                                     "OR NOT EXISTS (SELECT 1 FROM teams t WHERE t.team_id = g.away_team_id)")}
    orient = {"neutral_games": one(f"SELECT COUNT(*) {elig} AND g.neutral_site = 1 AND g.status = 'final'"),
              "stats_points_mismatch": one(
                  "SELECT COUNT(DISTINCT g.game_id) FROM games g JOIN team_game_stats s ON s.game_id = g.game_id WHERE g.status = 'final' "
                  "AND ((s.is_home = 1 AND (s.team_id != g.home_team_id OR s.points != g.home_score)) OR "
                  "(s.is_home = 0 AND (s.team_id != g.away_team_id OR s.points != g.away_score)))"),
              "stats_points_mismatch_neutral": one(
                  "SELECT COUNT(DISTINCT g.game_id) FROM games g JOIN team_game_stats s ON s.game_id = g.game_id WHERE g.status = 'final' "
                  "AND g.neutral_site = 1 AND ((s.is_home = 1 AND (s.team_id != g.home_team_id OR s.points != g.home_score)) OR "
                  "(s.is_home = 0 AND (s.team_id != g.away_team_id OR s.points != g.away_score)))"),
              "finals_without_stats": one(f"SELECT COUNT(*) {elig} AND g.status = 'final' AND NOT EXISTS "
                                          "(SELECT 1 FROM team_game_stats s WHERE s.game_id = g.game_id)")}
    finals = {"missing_scores": one("SELECT COUNT(*) FROM games WHERE status = 'final' AND (home_score IS NULL OR away_score IS NULL)"),
              "tied": one(f"SELECT COUNT(*) {elig} AND g.status = 'final' AND g.home_score = g.away_score"),
              "tied_by_season": {str(r[0]): r[1] for r in conn.execute(
                  f"SELECT g.season, COUNT(*) {elig} AND g.status = 'final' AND g.home_score = g.away_score GROUP BY g.season")},
              "scheduled_past_start_over_2d": one(f"SELECT COUNT(*) {elig} AND g.status = 'scheduled' AND g.tipoff_at < "
                                                  "strftime('%Y-%m-%dT%H:%M:%SZ', ?, '-2 days')", today),
              "result_available_before_tipoff": one(f"SELECT COUNT(*) {elig} AND g.status = 'final' AND g.result_available_at <= g.tipoff_at")}
    quotes_by = {}
    if has_table(conn, "market_quotes"):
        for r in conn.execute("SELECT game_id, market, MIN(retrieved_at) FROM market_quotes WHERE provider = ? AND price_label = 'current' "
                              "GROUP BY game_id, market", (QUOTE_PROVIDER,)):
            quotes_by[(r[0], r[1])] = r[2]
    after = {r[0] for r in conn.execute("SELECT DISTINCT game_id FROM ncaab_after_game_lines")} if has_table(conn, "ncaab_after_game_lines") else set()
    cov: dict = {}
    for r in conn.execute(f"SELECT g.game_id, g.season, g.status, g.tipoff_at, g.neutral_site, "
                          "EXISTS (SELECT 1 FROM team_game_stats s WHERE s.game_id = g.game_id AND s.fga IS NOT NULL AND s.fta IS NOT NULL "
                          "GROUP BY s.game_id HAVING COUNT(*) = 2) " + elig):
        s = cov.setdefault(str(r[1]), {"games": 0, "finals": 0, "first": r[3], "last": r[3], "neutral": 0, "missing_fga_fta": 0,
                                       "after_game_line": 0, "quotes": {m: 0 for m in ("winner", "spread", "total")}})
        s["games"] += 1
        s["finals"] += r[2] == "final"
        s["first"], s["last"] = min(s["first"], r[3]), max(s["last"], r[3])
        s["neutral"] += r[4]
        s["missing_fga_fta"] += r[2] == "final" and not r[5]
        s["after_game_line"] += r[0] in after
        for m in s["quotes"]:
            s["quotes"][m] += (r[0], m) in quotes_by
    gaps = {}
    for season, s in cov.items():
        days = {r[0] for r in conn.execute(f"SELECT DISTINCT substr(g.tipoff_at, 1, 10) {elig} AND g.season = ?", (int(season),))}
        d0, d1 = datetime.fromisoformat(min(days)), datetime.fromisoformat(max(days))
        every = [(d0 + timedelta(days=i)).strftime("%Y-%m-%d") for i in range((d1 - d0).days + 1)]
        gaps[season] = [d for d in every if d not in days and d[5:7] in ("12", "01", "02")]
    dup_seasons = {int(k): v for k, v in fixtures["duplicate_games_by_season"].items()}
    tie_seasons = {int(k): v for k, v in finals["tied_by_season"].items()}
    if fixtures["duplicate_game_ids"] or any(k >= scored_from for k in dup_seasons) or fixtures["swapped_duplicates"] > 5:
        issues.append(f"duplicate fixtures: {fixtures}")
    elif dup_seasons:
        notes.append(f"{sum(dup_seasons.values())} duplicate fixtures (same tip-off and teams under two ids), seasons {sorted(dup_seasons)} "
                     "only: warm-up burn-in, never scored")
    if any(k >= scored_from for k in tie_seasons):
        issues.append(f"tied finals in scored seasons: {tie_seasons}")
    elif tie_seasons:
        notes.append(f"{sum(tie_seasons.values())} finals tied (0-0 placeholders), seasons {sorted(tie_seasons)} only: warm-up")
    if fixtures["swapped_duplicates"]:
        notes.append(f"{fixtures['swapped_duplicates']} same-UTC-day pairs with home/away swapped (rematches or relists; not removed)")
    if orient["stats_points_mismatch"]:
        issues.append(f"{orient['stats_points_mismatch']} finals whose team_game_stats home/away or points disagree with games")
    if finals["missing_scores"] or finals["result_available_before_tipoff"]:
        issues.append(f"finals without scores or available before tip-off: {finals}")
    if fixtures["teams_missing"]:
        issues.append(f"{fixtures['teams_missing']} games reference unknown teams")
    return {"sport": "ncaab", "checked_at": today, "ok": not issues, "issues": issues, "notes": notes, "fixtures": fixtures, "orientation": orient,
            "finals": finals, "coverage": dict(sorted(cov.items())), "empty_winter_days": gaps,
            "quote_note": "DraftKings quotes (market_quotes) start 2026-11-17; ncaab_after_game_lines are retrieved after the game "
                          "(benchmark only). Earlier seasons have no bookmaker line at all."}


# --- Evaluation of research models ------------------------------------------------------------------------------------
@_research
def _pairs(records: list[dict]) -> list[tuple]:
    """(p_a given a decisive result, 1 if a won) for records with a non-push, non-void outcome."""
    return [(r["probs"]["a"] / (r["probs"]["a"] + r["probs"]["b"]), r["outcome"] == "a") for r in records
            if r["outcome"] in ("a", "b")]


@_research
def score_records(records: list[dict]) -> dict:
    """n, Brier and log loss (pushes/voids excluded, a/(a+b) renormalised), Gaussian point log score and RMSE where the record has a
    point forecast, and the worst decile gap of predicted vs observed frequency."""
    pr = _pairs(records)
    if not pr:
        return {"n": 0}
    p = [x for x, _ in pr]
    y = [o for _, o in pr]
    out = {"n": len(pr), "pushes": sum(r["outcome"] == "push" for r in records), "brier": metrics.brier(p, y),
           "log_loss": metrics.log_loss(p, y)}
    pts = [r for r in records if r.get("point_pred") is not None and r.get("point_actual") is not None and r.get("sd")]
    if pts:
        out["gauss_nll"] = sum(0.5 * math.log(2 * math.pi * r["sd"] ** 2) + (r["point_actual"] - r["point_pred"]) ** 2 / (2 * r["sd"] ** 2)
                               for r in pts) / len(pts)
        out["rmse"] = math.sqrt(sum((r["point_actual"] - r["point_pred"]) ** 2 for r in pts) / len(pts))
    dec = _ev._deciles(p, [float(v) for v in y])
    out["max_decile_gap"] = max(abs(x["gap"]) for x in dec) if dec else None
    return out


@_research
def compare_records(a: list[dict], b: list[dict]) -> dict:
    """Mean per-game Brier difference a - b on the games both decided (negative favours a), with the shared week-block bootstrap."""
    pb = {r["game_id"]: r for r in b if r["outcome"] in ("a", "b")}
    vals, cuts = [], []
    for r in a:
        q = pb.get(r["game_id"])
        if r["outcome"] not in ("a", "b") or q is None:
            continue
        y = float(r["outcome"] == "a")
        pa = r["probs"]["a"] / (r["probs"]["a"] + r["probs"]["b"])
        pq = q["probs"]["a"] / (q["probs"]["a"] + q["probs"]["b"])
        vals.append((pa - y) ** 2 - (pq - y) ** 2)
        cuts.append(r["forecast_cutoff"])
    if not vals:
        return {"games": 0}
    boot = backtest.block_bootstrap(vals, cuts, samples=_ev.BOOTSTRAP_SAMPLES)
    return {"games": len(vals), "mean_brier_diff": boot["mean"], "ci95": boot["ci95"]}


@_research
def quote_benchmark(records: list[dict], incumbent: list[dict]) -> dict:
    """Records graded at a DraftKings line with a stored pre-cutoff quote: model Brier vs the no-vig price's Brier on the same games."""
    q = [r for r in records if r["quote"] and r["outcome"] in ("a", "b")]
    inc = {(r["game_id"], r["market"]): r for r in incumbent}
    if not q:
        return {"n": 0}
    y = [r["outcome"] == "a" for r in q]
    dk = [r["quote"]["p_a_devig"] for r in q]
    mod = [r["probs"]["a"] / (r["probs"]["a"] + r["probs"]["b"]) for r in q]
    ii = [inc[(r["game_id"], r["market"])] for r in q if (r["game_id"], r["market"]) in inc]
    out = {"n": len(q), "model_brier": metrics.brier(mod, y), "dk_novig_brier": metrics.brier(dk, y), "coin_brier": 0.25}
    if len(ii) == len(q):
        out["incumbent_brier"] = metrics.brier([r["probs"]["a"] / (r["probs"]["a"] + r["probs"]["b"]) for r in ii], y)
    return out


@_research
def evaluate_research(conn: sqlite3.Connection, periods=("search", "validation"), models=None) -> dict:
    """Every research model's score per market over the search and validation periods (never the holdout), the paired difference
    against the incumbent, and, for the forward seasons, DraftKings-quote benchmarks."""
    models = list(models or MODEL_SPECS)
    rp = research_periods()
    out: dict = {"periods": {p: list(rp[p]) for p in periods}, "line_source": "reference_model (market-free Elo-margin / "
                 "running-points half-point line, shared by every model)", "results": {}, "vs_incumbent": {}}
    recs = {m: research_walk(conn, [s for p in periods for s in rp[p]], m) for m in models}
    for p in periods:
        seasons = set(rp[p])
        for m in models:
            for market in RESEARCH_MARKETS:
                sel = [r for r in recs[m] if r["season"] in seasons and r["market"] == market]
                if sel:
                    out["results"].setdefault(p, {}).setdefault(market, {})[m] = score_records(sel)
                    if m != "ncaab-incumbent":
                        inc = [r for r in recs["ncaab-incumbent"] if r["season"] in seasons and r["market"] == market]
                        out["vs_incumbent"].setdefault(p, {}).setdefault(market, {})[m] = compare_records(sel, inc)
    return out


@_research
def evaluate_forward(conn: sqlite3.Connection, models=None) -> dict:
    """Forward seasons (after the holdout): games with a DraftKings quote retrieved at or before the forecast cutoff, graded at
    the quote's line against the no-vig price. Parameters of candidates stop at the last validation season."""
    models = list(models or MODEL_SPECS)
    seasons = [s for (s,) in conn.execute("SELECT DISTINCT season FROM games WHERE season >= ?", (forward_from(),))]
    out: dict = {"seasons": seasons, "quote_provider": QUOTE_PROVIDER, "results": {}}
    if not seasons:
        return out
    recs = {m: research_walk(conn, seasons, m) for m in models}
    for market in ("spread", "total"):
        inc = [r for r in recs["ncaab-incumbent"] if r["market"] == market]
        for m in models:
            sel = [r for r in recs[m] if r["market"] == market]
            out["results"].setdefault(market, {})[m] = quote_benchmark(sel, inc)
    return out


@_research
def research_main(argv=None) -> int:
    """`python -m app.ncaab_cards research --out-dir D`: data validation, search + validation walks and the forward quote
    benchmark as JSON files in D. Never the holdout; refuses the live data folder."""
    import argparse
    import sys
    ap = argparse.ArgumentParser(prog="python -m app.ncaab_cards research")
    ap.add_argument("stage", choices=("research",))
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    if config.protected_data_dir(config.data_dir()):
        raise SystemExit(f"Refusing to evaluate against the live data folder {config.data_dir()}; set NBA_DATA_DIR to a copy")
    conn = _ev._connect(config.db_path("ncaab"))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, body in (("validate_data", validate_data(conn)), ("research", evaluate_research(conn)),
                       ("forward", evaluate_forward(conn))):
        (args.out_dir / f"{name}.json").write_text(json.dumps(body, indent=1, default=str))
    print(json.dumps({"stage": "research", "output_dir": str(args.out_dir)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(research_main())
