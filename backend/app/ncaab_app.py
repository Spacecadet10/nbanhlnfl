"""NCAAB serving adapter: what the shared NBA/NHL production paths need from a college-basketball store.

The shared paths (live.engine, near_tipoff, market_live, coverage, the API provider) are reused unchanged for NCAAB;
this module is the one place that knows the NCAAB specifics (docs/NCAAB_PLAN.md):

* the serving `live.Engine` (eligible games, results, dataset version, pregame view, primary model);
* `CollegeModel`, an adapter over the model agent's `app/models/college.py`
  (`college.predict(conn, game_id, *, cutoff) -> {model_version, home_win_prob, markets: {market: {line, probs}}}`), written
  so that the shared code can call `.update`, `.predict` and `.distribution` as it does for Elo and the NBA points model;
* the data adapter (`app/ncaab.py`, the data agent's module) for the day refresh and the conference context tables;
* explicit refusals: NCAAB has no agreement members, no pick rule and no player-level inputs.

Nothing here edits a sealed module. The adapter holds no state about results: `college.predict` reads the store at
`cutoff`, so a result that became available after `cutoff` can never reach a forecast.
"""
import hashlib
import inspect
import json
import sqlite3
from pathlib import Path
from typing import Callable, Optional

from . import backtest, config, sports
from .db import has_table
from .models.base import PregameGame

SPORT = "ncaab"
# Seasons by ending year (docs/NCAAB_PLAN.md "Evaluation plan"). Used for labels only; no period gates a live forecast.
PERIODS = {"warmup": (2013, 2014), "development": tuple(range(2015, 2022)), "validation": (2022, 2023, 2024),
           "test": (2025, 2026)}
FEATURE_VERSION = "ncaab-pregame-ids-v1+prior-day-et-cutoff"
DECISION_PATH = Path(__file__).parent / "models" / "ncaab_frozen_decision.json"
UNSEALED_VERSION = "ncaab-college-unavailable"
MARKET_SUFFIX = "-markets"
LIMITATION = ("The NCAAB model uses results only; it does not know about injuries, rest, travel or line-ups. "
              "Research only: no pick or threshold rule is claimed for NCAAB.")
SCORING_NOTE = "Non-Division I opponents are never forecast, graded or counted."


class CollegeModelUnavailable(RuntimeError):
    """The NCAAB model (app/models/college.py and its frozen decision) is not installed or cannot predict."""


class NcaabDataUnavailable(RuntimeError):
    """The NCAAB data module (app/ncaab.py) does not provide what a job needs."""


class LineNotModelled(ValueError):
    """The model returned probabilities only for its own line and cannot price this other one."""


# --- the model ------------------------------------------------------------------------------------------------------
def _college():
    try:
        from .models import college
    except ImportError as exc:   # the model agent's file is not part of this checkout
        raise CollegeModelUnavailable(f"No NCAAB model installed: {exc}") from exc
    return college


def _load_decision() -> tuple[dict, Optional[str]]:
    """(decision, None) when the sealed decision loads through the model's own loader (sha256 of the canonical content, every
    field present), else ({}, the reason)."""
    try:
        college = _college()
        return college.load_decision(DECISION_PATH), None
    except CollegeModelUnavailable as exc:
        return {}, str(exc)
    except (AttributeError, OSError, ValueError, KeyError) as exc:      # FrozenDecisionError is a ValueError
        return {}, f"No valid frozen NCAAB decision ({DECISION_PATH.name}): {exc}"


def decision() -> dict:
    """The sealed NCAAB decision (`app/models/ncaab_frozen_decision.json`) when it is valid, else {} (nothing is forecast)."""
    return _load_decision()[0]


def decision_issue() -> Optional[str]:
    """Why NCAAB has no usable decision, or None."""
    return None if decision() else (_load_decision()[1] or "No valid frozen NCAAB decision")


def card_issue() -> Optional[str]:
    """Why NCAAB Telegram cards must not be produced (the way NFL's activation gate works), or None: a store must exist in
    this data directory and the sealed decision must be valid."""
    if not config.db_path(SPORT).is_file():
        return "No NCAAB store in this data directory"
    return decision_issue()


def base_version() -> str:
    """Model id of the winner forecasts: the frozen decision's id, else the model module's, else a labelled placeholder."""
    d = decision()
    for key in ("model_version", "winner_model_version", "winner_model"):
        if isinstance(d.get(key), str) and d[key]:
            return d[key] if d[key].startswith("ncaab-") else f"ncaab-{d[key]}"
    try:
        v = getattr(_college(), "MODEL_VERSION", None)
    except CollegeModelUnavailable:
        v = None
    return v if isinstance(v, str) and v else UNSEALED_VERSION


def _probs(raw) -> tuple[float, float, float]:
    """(home or over, push, away or under) from the model's `probs`: a dict (a / mid / b or home / push / away or
    over / push / under), a pair (no push) or a triple."""
    if isinstance(raw, dict):
        keys = next((k for k in (("a", "mid", "b"), ("home", "push", "away"), ("over", "push", "under"),
                                 ("home", "mid", "away")) if k[0] in raw and k[2] in raw), None)
        if keys is None:
            raise ValueError(f"NCAAB model probabilities have no recognised sides: {sorted(raw)}")
        raw = (raw[keys[0]], raw.get(keys[1], 0.0), raw[keys[2]])
    raw = tuple(float(x) for x in raw)
    if len(raw) == 2:
        raw = (raw[0], 0.0, raw[1])
    pa, pm, pb = raw
    total = pa + pm + pb
    if abs(total - 1) > 1e-6 or min(raw) < 0:
        raise ValueError(f"NCAAB model probabilities do not sum to 1: {raw}")
    return pa / total, pm / total, pb / total


class CollegeDist:
    """What market_live and coverage read from a score distribution: `.market(market, line)`, `.params`. The model returns
    each market at its own line; another line is priced only if the model can be asked for it (`predict(..., lines=)`)."""

    def __init__(self, pred: dict, reprice: Optional[Callable[[dict], dict]] = None):
        self.pred = pred
        self._reprice = reprice
        self._by_line: dict = {}
        markets = pred.get("markets") or {}
        self.params = {"model_version": pred.get("model_version"),
                       "model_lines": {m: v.get("line") for m, v in markets.items()},
                       "market_models": {m: v.get("model_version") for m, v in markets.items() if v.get("model_version")}}

    def winner(self) -> tuple[float, float, float]:
        p = float(self.pred["home_win_prob"])
        return p, 0.0, 1.0 - p

    def model_line(self, market: str) -> float:
        spec = (self.pred.get("markets") or {}).get(market)
        if not spec or spec.get("line") is None:
            raise LineNotModelled(f"The NCAAB model gave no {market} line")
        return float(spec["line"])

    def median(self, market: str) -> float:
        """The middle of the market's quantity (what `markets.model_line` centres on): the home margin for the spread, the
        total or the team's points. Normal, so the mean. A model that gives no mean: its own line (negated for the spread)."""
        spec = (self.pred.get("markets") or {}).get(market) or {}
        if spec.get("mean") is not None:
            return float(spec["mean"])
        own = self.model_line(market)
        return -own if market == "spread" else own

    def _normal(self, market: str, line: float) -> Optional[tuple[float, float, float]]:
        """The model's own normal (mean, sd) priced at any line, when the model gave them and can price a line."""
        spec = (self.pred.get("markets") or {}).get(market) or {}
        price = getattr(_college(), "market_probs", None)
        if price is None or spec.get("mean") is None or not spec.get("sd"):
            return None
        return _probs(price(market, float(line), float(spec["mean"]), float(spec["sd"])))

    def market(self, market: str, line: Optional[float] = None) -> tuple[float, float, float]:
        if market == "winner":
            return self.winner()
        own = self.model_line(market)
        if line is None or abs(float(line) - own) < 1e-9:
            return _probs(self.pred["markets"][market]["probs"])
        key = (market, float(line))
        if key not in self._by_line and (priced := self._normal(market, line)) is not None:
            self._by_line[key] = priced
        if key not in self._by_line:
            if self._reprice is None:
                raise LineNotModelled(f"The NCAAB model prices {market} only at its own line {own:g}, not {line:g}")
            other = self._reprice({market: float(line)})
            spec = (other.get("markets") or {}).get(market)
            if not spec or spec.get("line") is None or abs(float(spec["line"]) - float(line)) > 1e-9:
                raise LineNotModelled(f"The NCAAB model could not price {market} at {line:g}")
            self._by_line[key] = _probs(spec["probs"])
        return self._by_line[key]


class CollegeModel:
    """The primary NCAAB model as the shared code sees a model: `name`, `config()`, `update(result)`, `predict(game)` and
    `distribution(game)`. `as_of` is the information time of the current calculation (the two-hour cycle sets it to its
    clock; unset, the night-before cutoff of the game's Eastern date). The model keeps no state, so `update` does nothing and a
    deep copy is itself."""
    name = "ncaab-college"      # replaced by the frozen decision's winner version on every instance (see __init__)

    def __init__(self, conn: sqlite3.Connection, version: Optional[str] = None):
        self.conn = conn
        self.version = version or base_version()
        self.name = self.version    # `backtest.model_version(model)` = name + config(): the stored winner id carries the model
        self.as_of: Optional[str] = None
        self._cache: dict = {}

    def config(self) -> dict:
        return {}

    def __deepcopy__(self, memo):
        return self

    def update(self, result) -> None:
        return None

    def _cutoff(self, game: PregameGame) -> str:
        return self.as_of or game.cutoff_at or backtest.cutoff_for(game.tipoff_at)

    def _predict(self, game: PregameGame, lines: Optional[dict] = None) -> dict:
        college = _college()
        cutoff = self._cutoff(game)
        key = (game.game_id, cutoff, tuple(sorted((lines or {}).items())))
        if key not in self._cache:
            kw = {"cutoff": cutoff}
            if lines:
                if "lines" not in inspect.signature(college.predict).parameters:
                    raise LineNotModelled("The NCAAB model cannot price a given line")
                kw["lines"] = lines
            pred = college.predict(self.conn, game.game_id, **kw)
            p = pred.get("home_win_prob")
            if not isinstance(p, float) or not 0.0 < p < 1.0:
                raise CollegeModelUnavailable(f"NCAAB model returned no usable home_win_prob for {game.game_id}: {p!r}")
            self._cache[key] = pred
        return self._cache[key]

    def predict(self, game: PregameGame) -> float:
        return self._predict(game)["home_win_prob"]

    def distribution(self, game: PregameGame) -> CollegeDist:
        return CollegeDist(self._predict(game), lambda lines: self._predict(game, lines))


def primary(conn: sqlite3.Connection) -> tuple[dict, CollegeModel]:
    """The winner model under the sealed decision; without a valid decision nothing is forecast (a stand-in id is never stored)."""
    d = decision()
    if not d:
        raise CollegeModelUnavailable(decision_issue() or "No valid frozen NCAAB decision")
    return d, CollegeModel(conn)


def market_model(conn: sqlite3.Connection):
    """(model, base version) of the NCAAB market forecasts, or MarketModelUnavailable when the model is not installed or
    not frozen (a sport without a sealed model is skipped with the reason, never given a made-up forecast)."""
    from .market_live import MarketModelUnavailable
    try:
        _college()
    except CollegeModelUnavailable as exc:
        raise MarketModelUnavailable(str(exc)) from exc
    d = decision()
    if not d:
        raise MarketModelUnavailable(decision_issue() or "No valid frozen NCAAB model decision")
    return CollegeModel(conn), base_version() + MARKET_SUFFIX + _market_digest(d)


def _market_digest(d: dict) -> str:
    """'-<8 hex>' of the model that ships for each line market, so the market id changes when the assignment does (a stored
    forecast is never re-stated with other probabilities under the same id); '' when the decision names none."""
    shipped = {m: v.get("model_version") or v.get("model") for m, v in (d.get("markets") or {}).items() if isinstance(v, dict)}
    if not any(shipped.values()):
        return ""
    return "-" + hashlib.sha256(json.dumps(shipped, sort_keys=True).encode()).hexdigest()[:8]


def market_line(model: CollegeModel, dist: CollegeDist, quote, market: str) -> tuple[float, Optional[object]]:
    """(line, quote used): the bookmaker's latest line when the model can price it, else the model's own line (labelled
    model-only by the caller)."""
    if quote is not None:
        try:
            dist.market(market, quote["line"])
            return float(quote["line"]), quote
        except LineNotModelled:
            pass
    return dist.model_line(market), None


# --- the serving engine ---------------------------------------------------------------------------------------------
def pregame(r: sqlite3.Row) -> PregameGame:
    return PregameGame(r["game_id"], r["season"], r["season_type"], r["tipoff_at"], r["home_team_id"], r["away_team_id"],
                       bool(r["neutral_site"]))


LATEST = "9999-12-31T00:00:00Z"


def load_rows(conn: sqlite3.Connection) -> list[backtest.Row]:
    """Every eligible Division I final (the data module's `ncaab.load_rows`, the rows the Coverage section hashes)."""
    return [backtest.Row(PregameGame(r["game_id"], r["season"], r["season_type"], r["tipoff_at"], r["home_team_id"],
                                     r["away_team_id"], bool(r["neutral_site"])), backtest.cutoff_for(r["tipoff_at"]),
                         r["home_score"], r["away_score"], r["result_available_at"])
            for r in _data().load_rows(conn, cutoff=LATEST)]


def dataset_version(rows: list) -> str:
    """The data module's `ncaab.dataset_version` of backtest rows (the same fields and hash, so forecasts record the hash the
    Coverage section shows)."""
    return _data().dataset_version(
        {"game_id": r.game.game_id, "season": r.game.season, "season_type": r.game.season_type, "tipoff_at": r.game.tipoff_at,
         "home_team_id": r.game.home_team_id, "away_team_id": r.game.away_team_id, "neutral_site": int(r.game.neutral_site),
         "home_score": r.home_score, "away_score": r.away_score, "result_available_at": r.result_available_at} for r in rows)


def engine():
    from .live import Engine
    return Engine(SPORT, sports.NCAAB.eligible_season_types, FEATURE_VERSION, load_rows, dataset_version,
                  pregame, primary)


# --- explicit refusals ----------------------------------------------------------------------------------------------
def no_agreement(policy: str) -> dict:
    """The agreement field of a card: NCAAB has no research members, so it is 'unavailable' with nothing missing."""
    return dict(policy=policy, status="unavailable", expected=0, available=0, agreeing=0, pick=None, home_votes=0,
                away_votes=0, abstentions=0, models=[], missing=[], generated_at=None, cutoff_at=None)


# --- the data module (the data agent's app/ncaab.py) ----------------------------------------------------------------
def _data():
    try:
        from . import ncaab
    except ImportError as exc:
        raise NcaabDataUnavailable(f"The NCAAB data module is not installed: {exc}") from exc
    return ncaab


def ingest_day(conn: sqlite3.Connection, day: str, *, fetch: Optional[Callable] = None) -> dict:
    """One ESPN D-I day scoreboard (YYYYMMDD) into the store: games, results, status and the DraftKings odds block
    (saved append-only with its retrieval time). Expected from the data module: `ncaab.ingest_day(conn, yyyymmdd, fetch=)`."""
    data = _data()
    if not hasattr(data, "ingest_day"):
        raise NcaabDataUnavailable("app/ncaab.py has no ingest_day(conn, yyyymmdd, fetch=)")
    return data.ingest_day(conn, day, fetch=fetch)


def fetch_day() -> Callable:
    """The scoreboard fetcher for `ingest_day`: `app.sources.espn_ncaab.fetch_day(yyyymmdd) -> bytes`."""
    try:
        from .sources import espn_ncaab
    except ImportError as exc:
        raise NcaabDataUnavailable(f"The NCAAB ESPN source is not installed: {exc}") from exc
    return espn_ncaab.fetch_day


# --- conference context (0042, written by the data module) -----------------------------------------------------------
_CONF_FILE = Path(__file__).parent / "reference" / "ncaab_conferences.json"


def conference_names() -> dict[str, str]:
    """ESPN conference id -> short display name (app/reference/ncaab_conferences.json, ESPN's current labels)."""
    return {cid: c["short"] for cid, c in json.loads(_CONF_FILE.read_text())["conferences"].items()}


def conferences(conn: sqlite3.Connection, season: int) -> dict[str, str]:
    """team_id -> conference short name for a season's Division I teams, from `ncaab_team_seasons`. An id without a
    recorded name is shown as its id, never dropped. {} when the store has no context table."""
    if not has_table(conn, "ncaab_team_seasons"):
        return {}
    names = conference_names()
    return {r[0]: names.get(r[1], f"Conference {r[1]}") for r in conn.execute(
        "SELECT team_id, conference_id FROM ncaab_team_seasons WHERE season = ? AND is_d1 = 1", (season,))}
