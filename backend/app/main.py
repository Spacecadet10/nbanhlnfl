"""FastAPI entry point. One API contract, two providers selected by NBA_API_MODE:
`real` (default: SQLite store + prediction ledger) or `demo` (synthetic fixtures, always labelled Demo)."""
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date
from typing import AsyncIterator, Iterator, Optional

import anyio
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

from . import config, db, sports
from .backtest import FrozenDecisionError
from .errors import InvalidRequest
from .nhl_eval import NhlEvaluationError
from .providers.demo import DemoProvider
from .providers.real import RealProvider
from .schemas import (CanonicalHistoryResponse, CanonicalPerformanceResponse, DataFreshness, GameDetail, GamesResponse, Health, HistoryResponse, MarketHistoryResponse, MarketPerformanceResponse,
                      Meta, NflGameDetail, NflGamesResponse, NflMeta, PerformanceResponse, SoccerMatchDetail, SoccerMatchesResponse,
                      SoccerPerformance, SoccerResearchHistory, SoccerResearchMatchDetail, SoccerResearchMatches, SoccerResearchMeta,
                      SoccerMeta, SourceStatus)

API_VERSION = "0.3.0"
SPORT = Query("nba", pattern=f"^({'|'.join(sports.SPORT_IDS)})$", description="Which sport's data store to read. Never falls back to another.")
MARKET = Query("winner", pattern="^(winner|reg3|spread|total|team_total_home|team_total_away)$")

app = FastAPI(title="NBA Predictor API", version=API_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(","),
    allow_methods=["GET"],
    allow_headers=["*"],
)


def api_mode() -> str:
    mode = os.environ.get("NBA_API_MODE", "real")
    if mode not in ("real", "demo"):
        raise HTTPException(status_code=500, detail=f"NBA_API_MODE must be 'real' or 'demo', not {mode!r}")
    return mode


# One store-backed request at a time. Each request is a few milliseconds of small SQLite reads; run concurrently in the
# thread pool they contend so badly that 13 requests took ~9 s instead of ~0.25 s (measured on a store copy, 2026-10-03:
# the time is in the reads themselves, not in opening, the journal pragma or closing). Serialising them is the fix.
# Waiting for the turn must never occupy the shared worker pool (40 threads): a waiter blocked inside a pool thread is a
# thread the request holding the turn cannot get for its endpoint, so a burst larger than the pool deadlocked the whole API
# (2026-10-03). The wait therefore runs on a thread of its own capacity limiter; the turn is one process-wide lock, so it
# holds across event loops too.
_STORE_TURN = threading.Lock()


async def get_provider(request: Request, mode: str = Depends(api_mode), sport: str = SPORT) -> AsyncIterator:
    held = {"turn": False}

    def take_turn() -> None:
        _STORE_TURN.acquire()
        held["turn"] = True          # set in the thread: a request cancelled after acquiring still releases in `finally`

    try:
        await anyio.to_thread.run_sync(take_turn, limiter=anyio.CapacityLimiter(1))
        if await request.is_disconnected():
            # The client gave up while queued: serving it anyway would make every later request wait for abandoned work.
            raise HTTPException(status_code=499, detail="Client closed the request before it was served")
        cm = contextmanager(_provider)(mode, sport)
        provider = await run_in_threadpool(cm.__enter__)          # opens the store off the event loop
        try:
            yield provider
        except BaseException as exc:                              # e.g. a store error inside the endpoint -> 503
            if not await run_in_threadpool(cm.__exit__, type(exc), exc, exc.__traceback__):
                raise
        else:
            await run_in_threadpool(cm.__exit__, None, None, None)
    finally:
        if held["turn"]:
            _STORE_TURN.release()


def _provider(mode: str, sport: str) -> Iterator:
    if mode == "demo":
        if sport != "nba":
            raise HTTPException(status_code=404, detail=f"Demo data exists only for the NBA; there is no {sport.upper()} "
                                                        "demo. Start the API in real mode to see NHL data.")
        yield DemoProvider()
        return
    if not config.db_path(sport).is_file():
        raise HTTPException(status_code=503, detail=f"No {sport.upper()} data store found. Run `python -m app.cli "
                                                    f"--sport {sport} migrate` and its ingest command from backend/"
                                                    + (", or start the API with NBA_API_MODE=demo." if sport == "nba" else "."))
    conn = db.connect(check_same_thread=False, sport=sport)
    try:
        if conn.execute("SELECT name FROM sqlite_master WHERE name='job_runs'").fetchone() is None:
            raise HTTPException(status_code=503, detail="Data store needs migrations: run `python -m app.cli migrate`.")
        stored = db.store_sport(conn)
        if stored not in (sport, None) or (stored is None and sport != "nba"):
            raise HTTPException(status_code=503, detail=f"The {sport.upper()} store holds {stored or 'unlabelled'} data.")
        yield RealProvider(conn)
    except sqlite3.Error as exc:
        raise HTTPException(status_code=503, detail=f"Data store error: {exc}") from exc
    except (FrozenDecisionError, NhlEvaluationError) as exc:   # missing, modified or out-of-date sealed model decision
        raise HTTPException(status_code=503, detail=f"Sealed model decision unusable: {exc}") from exc
    finally:
        conn.close()


@app.get("/api/health", response_model=Health)
def health(mode: str = Depends(api_mode)):
    stamp = config.data_dir() / "SNAPSHOT_AT"
    try:
        snapshot = stamp.read_text().strip() or None if stamp.is_file() else None
    except OSError:
        snapshot = None
    return Health(status="ok", mode=mode, api_version=API_VERSION, data_snapshot_at=snapshot)


LEAGUE = Query("nba", pattern="^(nba|nhl|nfl)$", description="Which sport's store; NFL is data-only (no forecasts)")
PUBLISH_STATUS = "PUBLISH_STATUS.json"     # written by `app.cli preview-publish` next to the copies it publishes


def snapshot_state(sport: str) -> Optional[dict]:
    """None when the API serves the stores in place. A served copy (SNAPSHOT_AT present) of `sport` is 'auto_snapshot'
    only while a publisher keeps republishing it (its status file names an interval, its last attempt is recent) AND it
    has published this sport; otherwise 'frozen_snapshot': a copy that will not update automatically. Publication time
    and errors are this sport's own: another sport's publication says nothing about this copy. A sport the publisher never
    republished keeps the startup copy time (`initial_snapshot_at`, else SNAPSHOT_AT when no publisher ever ran)."""
    import json as _json
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    folder = config.data_dir()
    stamp = folder / "SNAPSHOT_AT"
    if not stamp.is_file():
        return None
    try:
        copied = stamp.read_text().strip() or None
    except OSError:
        copied = None
    try:
        status = _json.loads((folder / PUBLISH_STATUS).read_text())
    except (OSError, ValueError):
        return {"mode": "frozen_snapshot", "published_at": copied}
    own = (status.get("sports") or {}).get(sport) or {}
    published = own.get("published_at") or status.get("initial_snapshot_at") or copied
    state = {"mode": "frozen_snapshot", "published_at": published,
             "last_error": own.get("error") if own.get("status") == "failed" else None}
    interval, attempt = status.get("interval_seconds"), status.get("last_attempt_at")
    try:
        alive = bool(interval and attempt) and _dt.now(_tz.utc) - _dt.strptime(attempt, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=_tz.utc) <= _td(seconds=3 * int(interval) + 60)
    except (TypeError, ValueError):
        alive = False
    if alive and own.get("published_at"):
        state.update(mode="auto_snapshot", interval_seconds=int(interval), next_publish_at=status.get("next_attempt_at"))
    return state


@app.get("/api/freshness", response_model=DataFreshness)
def freshness_status(sport: str = LEAGUE, mode: str = Depends(api_mode)):
    """Cheap read for the browser's 30-second check: never contacts a source, never writes, works for all three sports
    (NFL through its data-only store, never through the served-sport path)."""
    from datetime import datetime as _dt, timezone as _tz
    from . import freshness
    now = _dt.now(_tz.utc)
    if mode == "demo":
        return {"sport": sport, "as_of": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "mode": "demo", "revision": "demo",
                "source_state": "unknown", "forecasts_supported": sport != "nfl"}
    try:
        conn = db.connect_readonly(sport)
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail=f"No {sport.upper()} data store found.") from None
    try:
        stored = db.store_sport(conn)
        if stored not in (sport, None) or (stored is None and sport != "nba"):
            raise HTTPException(status_code=503, detail=f"The {sport.upper()} store holds {stored or 'unlabelled'} data.")
        return freshness.summary(conn, sport, now, snapshot=snapshot_state(sport))
    except sqlite3.Error as exc:
        raise HTTPException(status_code=503, detail=f"Data store error: {exc}") from exc
    finally:
        conn.close()


# --- NFL: data-only (docs/NFL_PLAN.md). These reads open the NFL store query-only and never go through get_provider,
# live.engine or any served path, so NFL stays outside SPORT_IDS and no forecast code can see an NFL store.
@contextmanager
def _nfl_store(mode: str) -> Iterator[sqlite3.Connection]:
    from .nfl import StoreMismatch
    if mode == "demo":
        raise HTTPException(status_code=404, detail="Demo data exists only for the NBA; there is no NFL demo. Start the "
                                                    "API in real mode to see the stored NFL schedule and results.")
    try:
        conn = db.connect_readonly("nfl")
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="No NFL data store found. Run `python -m app.cli nfl migrate` and "
                                                    "`python -m app.cli nfl ingest --from YYYYMM --to YYYYMM` from backend/.") from None
    try:
        conn.execute("BEGIN")
        yield conn
    except StoreMismatch as exc:
        raise HTTPException(status_code=503, detail=f"The NFL store is unusable: {exc}") from exc
    except sqlite3.Error as exc:
        raise HTTPException(status_code=503, detail=f"NFL data store error: {exc}") from exc
    finally:
        conn.close()


@app.get("/api/nfl/meta", response_model=NflMeta)
def nfl_meta(mode: str = Depends(api_mode)):
    from . import nfl
    from datetime import datetime as _dt
    with _nfl_store(mode) as conn:
        return {"mode": "real", **nfl.meta(conn, _dt.now(nfl.EASTERN).date())}


@app.get("/api/nfl/games", response_model=NflGamesResponse, response_model_exclude_unset=True)
def nfl_games(date: date = Query(description="Kickoff date (US Eastern), YYYY-MM-DD"), mode: str = Depends(api_mode)):
    from . import nfl
    with _nfl_store(mode) as conn:
        return nfl.games_on(conn, date)


@app.get("/api/nfl/record")
def nfl_record(mode: str = Depends(api_mode)):
    from .nfl_live import Views
    with _nfl_store(mode) as conn:
        return Views(conn).report()


@app.get("/api/nfl/games/{game_id}", response_model=NflGameDetail, response_model_exclude_unset=True)
def nfl_game(game_id: str, mode: str = Depends(api_mode)):
    from . import nfl
    with _nfl_store(mode) as conn:
        detail = nfl.game_detail(conn, game_id) if game_id.startswith(sports.NFL.id_prefix) else None
    if detail is None:
        raise HTTPException(status_code=404, detail=f"No NFL game {game_id!r} in the store.")
    return detail


# --- European soccer: data-only (app/soccer.py). Like the NFL reads: query-only connection to the soccer store, never
# get_provider or any forecast path; soccer is not in SPORT_IDS. Every read names one competition.
from .sources.espn import SOCCER_COMPETITIONS as _SOCCER_COMPETITIONS   # noqa: E402
COMPETITION = Query("eng.1", pattern="^(" + "|".join(c.replace(".", "\\.") for c in _SOCCER_COMPETITIONS) + ")$",
                    description="ESPN competition slug; one competition per request, never mixed")


@contextmanager
def _soccer_store(mode: str) -> Iterator[sqlite3.Connection]:
    from .db import StoreMismatch
    if mode == "demo":
        raise HTTPException(status_code=404, detail="There is no soccer demo. Start the API in real mode to see the stored "
                                                    "soccer fixtures and results.")
    try:
        conn = db.connect_readonly("soccer")
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="No soccer data store found. A development store is created from backend/ "
                            "with `NBA_DATA_DIR=<scratch or development data directory> .venv/bin/python -m app.cli soccer "
                            "migrate` and `... soccer ingest --from YYYYMMDD --to YYYYMMDD` (never the live data/ folder). That "
                            "ingest collects from ESPN, which the S1 source review found not clearly permitted for this "
                            "project (docs/SOCCER_PLAN.md): a user decision.") from None
    try:
        conn.execute("BEGIN")
        yield conn
    except StoreMismatch as exc:
        raise HTTPException(status_code=503, detail=f"The soccer store is unusable: {exc}") from exc
    except sqlite3.Error as exc:
        raise HTTPException(status_code=503, detail=f"Soccer data store error: {exc}") from exc
    finally:
        conn.close()


@app.get("/api/soccer/meta", response_model=SoccerMeta)
def soccer_meta(competition: str = COMPETITION, mode: str = Depends(api_mode)):
    from . import soccer
    from datetime import datetime as _dt
    with _soccer_store(mode) as conn:
        return {"mode": "real", **soccer.meta(conn, competition, _dt.now(soccer.EASTERN).date())}


@app.get("/api/soccer/matches", response_model=SoccerMatchesResponse)
def soccer_matches(date: date = Query(description="Kickoff date (US Eastern), YYYY-MM-DD"), competition: str = COMPETITION,
                   mode: str = Depends(api_mode)):
    from . import soccer
    with _soccer_store(mode) as conn:
        return soccer.matches_on(conn, competition, date)


@app.get("/api/soccer/matches/{match_id}", response_model=SoccerMatchDetail)
def soccer_match(match_id: str, mode: str = Depends(api_mode)):
    from . import soccer
    with _soccer_store(mode) as conn:
        detail = soccer.match_detail(conn, match_id) if match_id.startswith(sports.SOCCER.id_prefix) else None
    if detail is None:
        raise HTTPException(status_code=404, detail=f"No soccer match {match_id!r} in the store.")
    return detail


@app.get("/api/soccer/live")
def soccer_live_forecasts(competition: str = COMPETITION, mode: str = Depends(api_mode)):
    """Live 90-minute forecasts of the sealed Elo (app/soccer_live.py) for one competition: upcoming matches and those
    kicked off in the last 2 days, each with its exact probabilities, generation/cutoff times, input freshness and, once
    known, its 90-minute result. Separate from the research reads below; never computed on request."""
    from . import soccer_live
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    now = _dt.now(_tz.utc)
    since = (now - _td(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with _soccer_store(mode) as conn:
        rows = [f for f in soccer_live.forecasts(conn, competition=competition, as_of=now.strftime("%Y-%m-%dT%H:%M:%SZ"))
                if f["kickoff_at"] >= since]
        check = soccer_live.latest_checks(conn).get(competition)
        from . import soccer
        refresh = soccer.refresh_state(conn, competition, now)
    from . import intl_live
    in_scope = competition in soccer_live.LAUNCH or competition in intl_live.LAUNCH
    return {"competition": competition, "in_scope": in_scope, "label": soccer_live.LABEL,
            "settlement_rule": soccer_live.SETTLEMENT_RULE, "market": soccer_live.MARKET,
            "scope_note": None if in_scope else ("No live forecast: this competition did not pass its held-out test "
                                                 "(too few test matches)."),
            "model": ({"name": "international Elo (national teams)", "candidate_sha256": intl_live.CANDIDATE_SHA256}
                      if competition in intl_live.COMPETITIONS else
                      {"name": "draw-aware Elo (uncalibrated)", "candidate_sha256": soccer_live.CANDIDATE_SHA256}),
            "check": check, "refresh": refresh, "forecasts": rows}


@app.get("/api/soccer/live/record")
def soccer_live_record(competition: Optional[str] = None, mode: str = Depends(api_mode)):
    """Every recorded live soccer forecast version (all competitions, or one), newest kickoff first, with probabilities,
    the model's pick, kickoff, generation/cutoff times and the eventual 90-minute result; plus a summary and the
    limitations. Nothing is filtered for performance; never computed on request."""
    from . import soccer
    with _soccer_store(mode) as conn:
        return soccer.forecast_record(conn, competition)


from datetime import date as _dt_date   # noqa: E402  (calendar check of the research date parameter)


# Soccer research reads (app/soccer_forecasts.py): openfootball history and the stored research forecasts. Only stored
# rows are returned; a store without them gives an empty list or a 503 that says so, never a computed fallback.
def _research(conn: sqlite3.Connection) -> None:
    from . import soccer_forecasts
    if not soccer_forecasts.available(conn):
        raise HTTPException(status_code=503, detail="This soccer store has no research history or forecast ledger (migrations "
                            "0036/0037). Build one on a copy: `NBA_DATA_DIR=<scratch> .venv/bin/python -m app.cli soccer "
                            "history-download` then `soccer eval-develop` and `soccer eval-record` (never the live data/ folder).")


@app.get("/api/soccer/research/meta", response_model=SoccerResearchMeta)
def soccer_research_meta(competition: str = COMPETITION, mode: str = Depends(api_mode)):
    from . import soccer_forecasts
    with _soccer_store(mode) as conn:
        _research(conn)
        return soccer_forecasts.research_meta(conn, competition)


@app.get("/api/soccer/research/matches", response_model=SoccerResearchMatches)
def soccer_research_matches(date: str = Query(pattern="^[0-9]{4}-[0-9]{2}-[0-9]{2}$", description="Listed local match date"),
                            competition: str = COMPETITION, mode: str = Depends(api_mode)):
    from . import soccer_forecasts
    try:
        _dt_date.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{date!r} is not a calendar date (YYYY-MM-DD)") from None
    with _soccer_store(mode) as conn:
        _research(conn)
        return soccer_forecasts.research_matches(conn, competition, date)


@app.get("/api/soccer/research/matches/{match_id}", response_model=SoccerResearchMatchDetail)
def soccer_research_match(match_id: str, mode: str = Depends(api_mode)):
    from . import soccer_forecasts
    with _soccer_store(mode) as conn:
        _research(conn)
        detail = soccer_forecasts.research_match(conn, match_id) if match_id.startswith("of:") else None
    if detail is None:
        raise HTTPException(status_code=404, detail=f"No soccer history match {match_id!r} in the store.")
    return detail


@app.get("/api/soccer/research/performance", response_model=SoccerPerformance)
def soccer_research_performance(competition: str = COMPETITION, mode: str = Depends(api_mode)):
    from . import soccer_forecasts
    with _soccer_store(mode) as conn:
        _research(conn)
        return soccer_forecasts.performance(conn, competition)


@app.get("/api/soccer/research/history", response_model=SoccerResearchHistory)
def soccer_research_history(competition: str = COMPETITION, season: Optional[int] = Query(None, ge=1990, le=2100),
                            model: Optional[str] = Query(None, pattern="^(frequency|elo|poisson)$"),
                            limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                            mode: str = Depends(api_mode)):
    from . import soccer_forecasts
    with _soccer_store(mode) as conn:
        _research(conn)
        return soccer_forecasts.research_history(conn, competition, season, model, limit, offset)


@app.get("/api/meta", response_model=Meta)
def meta(p=Depends(get_provider)):
    return p.meta()


@app.get("/api/sources", response_model=list[SourceStatus])
def sources(p=Depends(get_provider)):
    return p.sources()


@app.get("/api/games", response_model=GamesResponse)
def games(date: date = Query(description="Game date (US Eastern), YYYY-MM-DD"),
          policy: str = Query("pregame", pattern="^(pregame|near_tipoff|canonical)$",
                              description="Which forecast the slate's picks and confidence filter use: canonical (one "
                                          "record per game and market) or a legacy ledger."),
          min_confidence: Optional[float] = Query(None, ge=0, le=1), market: str = MARKET, p=Depends(get_provider)):
    if market == "winner" and min_confidence is not None and min_confidence < 0.5:
        raise HTTPException(status_code=422, detail="min_confidence for the winner market is at least 0.5")
    if market != "winner" and policy == "canonical":
        _canonical_market(p, market, min_confidence)
        return p.games(date, policy, min_confidence, market)
    if market != "winner":
        _market_supported(p, market)
        return p.games(date, policy, min_confidence, market)
    return p.games(date, policy, min_confidence)


def _market_supported(p, market: str) -> None:
    if getattr(p, "mode", None) == "demo":
        raise HTTPException(status_code=404, detail="Market forecasts are not part of the demo data.")
    info = {m.id: m for m in p.market_infos()}
    if market not in info:
        raise HTTPException(status_code=404, detail=f"The {market} market is not defined for {p.sport.upper()}.")
    if not info[market].available:
        raise HTTPException(status_code=404, detail=f"No {market} forecasts are recorded for {p.sport.upper()}: "
                                                    "no sealed market model.")


@app.get("/api/games/{game_id}", response_model=GameDetail)
def game(game_id: str, p=Depends(get_provider)):
    g = p.game(game_id)
    if g is None:
        raise HTTPException(status_code=404, detail=f"Game {game_id} not found")
    return g


def _canonical_provider(p):
    if getattr(p, "mode", None) != "real":
        raise HTTPException(status_code=404, detail="Canonical forecasts are not part of the demo data.")
    return p


def _canonical_market(p, market: str, min_confidence: Optional[float] = None) -> None:
    """The market must be one the sport defines; a two-way market's confidence is at least 0.5 (its pick is the likelier
    side). The three-way market's confidence can be lower (its grid starts at 35%)."""
    _canonical_provider(p)
    if market not in sports.get(p.sport).markets:
        raise HTTPException(status_code=404, detail=f"The {market} market is not defined for {p.sport.upper()}.")
    if market != "reg3" and min_confidence is not None and min_confidence < 0.5:
        raise HTTPException(status_code=422, detail=f"min_confidence for the {market} market is at least 0.5")


@app.get("/api/canonical/forecasts", response_model=CanonicalHistoryResponse)
def canonical_forecasts(market: str = MARKET, date_from: Optional[date] = None, date_to: Optional[date] = None,
                        team: Optional[str] = None, limit: int = Query(500, ge=1, le=5000), p=Depends(get_provider)):
    """One row per game since canonical recording began: its canonical forecast and grade, or why there is none."""
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must be on or before date_to")
    _canonical_market(p, market)
    return p.canonical_history(date_from, date_to, team, limit, market)


@app.get("/api/canonical/performance", response_model=CanonicalPerformanceResponse)
def canonical_performance(market: str = MARKET, p=Depends(get_provider)):
    _canonical_market(p, market)
    return p.canonical_performance(market)


@app.get("/api/canonical/thresholds")
def canonical_thresholds(p=Depends(get_provider)):
    """Every market of the sport, per confidence threshold: the live canonical record (with the bookmaker's probability
    for the same picks), the primary model's comparable history, and the declared best-threshold verdict. Read-only."""
    _canonical_provider(p)
    return p.threshold_overview()


@app.get("/api/canonical/ranked")
def canonical_ranked(date_: date = Query(..., alias="date", description="Eastern game date."), p=Depends(get_provider)):
    """Every canonical pick of the date in every market of the sport, ranked by the past evidence for picks like it
    (best first), with the threshold each pick reaches. Read-only; not betting advice."""
    _canonical_provider(p)
    return p.ranked_picks(date_)


@app.get("/api/paper-rules")
def paper_rules_report(p=Depends(get_provider)):
    """The frozen paper rules of the sport (app/reference/paper_rules.json; no money): bets since the freeze, record,
    return per bet and the verdict state (interim until the regular season is over). Read-only."""
    _canonical_provider(p)
    from . import paper_rules
    return paper_rules.report(p.conn, p.sport, p.now)


@app.get("/api/predictions", response_model=HistoryResponse)
def predictions(run_type: str = Query("live", pattern="^(live|backtest)$"), date_from: Optional[date] = None,
                date_to: Optional[date] = None, team: Optional[str] = None,
                min_confidence: Optional[float] = Query(
                    None, ge=0.5, le=1, description="Keep only picks whose model probability for the predicted winner, "
                                                    "max(p_home, 1 - p_home), is at least this value."),
                policy: str = Query("pregame", pattern="^(pregame|near_tipoff)$",
                                    description="Live forecasts only: the pre-game archive (pregame) or near-tipoff."),
                limit: int = Query(500, ge=1, le=5000), p=Depends(get_provider)):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must be on or before date_to")
    _check_policy(run_type, policy)
    return p.predictions(run_type, date_from, date_to, team, limit, min_confidence, policy)


def _check_policy(run_type: str, policy: str) -> None:
    if policy == "near_tipoff" and run_type != "live":
        raise HTTPException(status_code=422, detail="near_tipoff forecasts exist only as live forecasts")


@app.get("/api/market-predictions", response_model=MarketHistoryResponse)
def market_predictions(market: str = MARKET, policy: str = Query("pregame", pattern="^(pregame|near_tipoff)$"),
                       date_from: Optional[date] = None, date_to: Optional[date] = None, team: Optional[str] = None,
                       min_confidence: Optional[float] = Query(None, ge=0, le=1),
                       limit: int = Query(500, ge=1, le=5000),
                       model_version: Optional[str] = Query(None, description="An earlier market model version (archive); "
                                                                                  "default: the current sealed model."),
                       p=Depends(get_provider)):
    if market == "winner":
        raise HTTPException(status_code=422, detail="Use /api/predictions for the winner market")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must be on or before date_to")
    _market_supported(p, market)
    return p.market_predictions(market, policy, date_from, date_to, team, limit, min_confidence, model_version)


@app.get("/api/market-performance", response_model=MarketPerformanceResponse)
def market_performance(market: str = MARKET, policy: str = Query("pregame", pattern="^(pregame|near_tipoff)$"),
                       model_version: Optional[str] = None, p=Depends(get_provider)):
    if market == "winner":
        raise HTTPException(status_code=422, detail="Use /api/performance for the winner market")
    _market_supported(p, market)
    return p.market_performance(market, policy, model_version)


@app.get("/api/performance", response_model=PerformanceResponse)
def performance(run_type: str = Query("live", pattern="^(live|backtest)$"),
                policy: str = Query("pregame", pattern="^(pregame|near_tipoff)$"), p=Depends(get_provider)):
    _check_policy(run_type, policy)
    return p.performance(run_type, policy)


@app.get("/api/agreement-performance")
def agreement_performance(policy: str = Query("near_tipoff", pattern="^(pregame|near_tipoff)$"), p=Depends(get_provider)):
    if p.mode != "real":
        return {"groups": [], "partial_games": 0, "note": "Research agreement is unavailable in demo mode."}
    from . import agreement
    return agreement.performance(p.conn, p.now, policy)


@app.get("/api/recovered-forecasts")
def recovered_forecasts(p=Depends(get_provider)):
    from . import recovery
    if p.mode != "real":
        return {"note": recovery.NOTE, "groups": [], "records": [], "status": "demo",
                "adaptive": {"note": recovery.ADAPTIVE_NOTE, "groups": [], "records": [], "unreconstructable": [], "status": "demo"}}
    return {**recovery.report(p.conn, p.now), "adaptive": recovery.adaptive_report(p.conn, p.now)}


@app.get("/api/thresholds")
def thresholds(p=Depends(get_provider)):
    if p.mode != "real":
        return {"groups": [], "all_stored_rows": {"winner_rows": 0, "market_rows": 0}, "note": "No forward threshold research in demo mode."}
    from . import tracking
    return tracking.report(p.conn, p.now)


@app.get("/api/coverage")
def coverage_report(p=Depends(get_provider), date_from: Optional[date] = None, date_to: Optional[date] = None):
    """Every-game coverage (docs/COVERAGE.md) for Eastern dates [date_from, date_to] (default: the last 7 days and today):
    scheduled games against the source schedule, forecasts per market (main or fallback), graded, pending, void,
    unresolved and missing with reasons, the unfiltered record and threshold views. Read-only."""
    from datetime import timedelta
    from . import coverage
    today = date.fromisoformat(coverage.et_date(p.now))
    date_to = date_to or today
    date_from = date_from or (date_to - timedelta(days=7))
    if date_from > date_to or (date_to - date_from).days > coverage.MAX_REPORT_DAYS:
        raise HTTPException(status_code=422, detail=f"date_from must not be after date_to and the range at most "
                                                    f"{coverage.MAX_REPORT_DAYS} days")
    if p.mode != "real":
        return {"status": "demo", "note": "Coverage requires the real store; demo mode records no forecasts."}
    start, end = coverage.et_bounds(date_from.isoformat(), date_to.isoformat())
    return {"status": "available", "date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
            **coverage.games_report(p.conn, start=start, end=end, as_of=p.now)}


# --- Research: model agreement, combination and selection (docs/MODEL_SELECTION.md). Research/shadow only. ------------------
def _research_store(p) -> None:
    if not db.has_table(p.conn, "research_cohorts"):
        raise HTTPException(status_code=503, detail="Research tables missing: run `python -m app.cli migrate` for this sport.")


@app.get("/api/research/game/{game_id}")
def research_game(game_id: str, p=Depends(get_provider)):
    if p.mode != "real":
        from . import research_demo
        return research_demo.game_detail(game_id)
    _research_store(p)
    from . import research_live
    return {**research_live.game_detail(p.conn, game_id, p.now), "mode": "real"}


@app.get("/api/research/slate")
def research_slate(date: date = Query(description="Game date (US Eastern), YYYY-MM-DD"), p=Depends(get_provider)):
    ids = [g.game_id for g in p.games(date).games]
    if p.mode != "real":
        from . import research_demo
        return {"mode": "demo", "games": research_demo.slate(ids)}
    _research_store(p)
    from . import research_live
    return {"mode": "real", "games": research_live.slate(p.conn, ids, p.now)}


@app.get("/api/research/performance")
def research_performance(market: str = MARKET, p=Depends(get_provider)):
    if p.mode != "real":
        if market not in sports.NBA.markets:
            raise HTTPException(status_code=404, detail=f"The {market} market is not defined for the demo sport.")
        from . import research_demo
        return research_demo.report(market)
    _market_supported(p, market)
    _research_store(p)
    from . import research_live
    return {**research_live.report(p.conn, market, p.now), "mode": "real"}


@app.get("/api/research/rules")
def research_rules(market: str = MARKET, show: str = Query("selected", pattern="^(selected|all)$"),
                   definition: Optional[str] = Query(None, max_length=64, pattern="^[0-9a-f]+$",
                                                     description="A research definition; default: the newest with cohorts."),
                   p=Depends(get_provider)):
    """Registered combination rules (docs/MODEL_SELECTION.md §15): historical exploration and prospective record."""
    if p.mode != "real":
        return {"mode": "demo", "sport": "nba", "market": market, "status": "demo", "rules": [],
                "note": "Combination-rule tracking uses recorded research cohorts; none exist in demo mode."}
    _market_supported(p, market)
    _research_store(p)
    from . import rule_tracking
    try:
        out = rule_tracking.report(p.conn, p.sport, market, show, definition=definition)
    except rule_tracking.RegistryError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {**out, "mode": "real"}


@app.get("/api/research/tracking")
def research_tracking(market: str = MARKET, evidence: str = Query("prospective", pattern="^(prospective|historical)$"),
                      kind: Optional[str] = Query(None, pattern="^(single|unanimous|avg|majority|stack|stack_pool)$"),
                      member: Optional[str] = Query(None, max_length=400, description="Comma-separated: groups containing all"),
                      group: Optional[str] = Query(None, max_length=600), version: Optional[str] = Query(None, max_length=64),
                      line: Optional[float] = None, date_from: Optional[date] = None, date_to: Optional[date] = None,
                      p=Depends(get_provider)):
    """Persistent agreement tracking tracking-v2 (docs/MODEL_SELECTION.md §18): every registered group by threshold and band,
    prospective or historical (exploratory) evidence, exploratory leaderboard; `exact_rules` = the registered exact rules'
    prospective record (exact-rules-v1; CLI-only registration). Research only."""
    if p.mode != "real":
        return {"mode": "demo", "sport": "nba", "market": market, "evidence": evidence, "status": "demo", "groups": [],
                "leaderboard": [], "note": "Agreement tracking reads the real stores; none in demo mode.",
                "exact_rules": {"study": "exact-rules-v1", "status": "demo", "games": 0, "rules": [],
                                "note": "Exact-rule tracking reads the real stores; none in demo mode."}}
    _market_supported(p, market)
    from . import agreement_tracking as at, selection_status
    try:
        out = at.report(p.conn, p.sport, market, evidence=evidence, kind=kind, member=member, group_id=group,
                        date_from=date_from, date_to=date_to, version=version, line=line)
    except at.RegistryError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except InvalidRequest as exc:          # e.g. a version prefix matching several research definitions
        raise HTTPException(status_code=422, detail=str(exc))
    rec = None
    if db.has_table(p.conn, "research_cohorts"):
        rec = next((m for m in selection_status.status(p.conn, p.now).get("markets", []) if m.get("market") == market), None)
    return {**out, "recommendation": rec, "mode": "real"}


@app.get("/api/research/tracking/check")
def research_tracking_check(market: str = MARKET, p=Depends(get_provider)):
    """Historical, exploratory: the configuration ranked first on earlier seasons, evaluated on the next season."""
    if p.mode != "real":
        return {"mode": "demo", "status": "demo", "folds": []}
    _market_supported(p, market)
    from . import agreement_tracking as at
    return {**at.precomputed_check(p.sport, market), "mode": "real"}


@app.get("/api/research/tracking/game/{game_id}")
def research_tracking_game(game_id: str, p=Depends(get_provider)):
    if p.mode != "real":
        return {"mode": "demo", "game_id": game_id, "status": "demo", "markets": []}
    from . import agreement_tracking as at
    return {**at.game_observations(p.conn, game_id), "mode": "real"}


@app.get("/api/research/training")
def research_training(p=Depends(get_provider)):
    """Training and update status per member: fit in force, training cutoff, new results since, next scheduled update,
    versions, skipped/failed updates with reasons, input freshness and rolling-loss monitoring. Never refits anything."""
    if p.mode != "real":
        return {"mode": "demo", "fits": [], "online": [], "updates": [], "inputs": {}, "monitoring": {"members": [], "alerts": []},
                "note": "Training status reads the real stores; none in demo mode."}
    from . import training_monitor
    return {**training_monitor.status(p.conn, p.now), "mode": "real"}


@app.get("/api/training-run")
def training_run(p=Depends(get_provider)):
    """Adaptive training state for one sport: whether a process holds the training lock, artifacts written by a running
    run, the last run, last success and last failure, and which model versions made the latest live forecasts. Reads the
    live store and training folder (config.live_data_dir) without locks or writes; nothing is estimated."""
    if p.mode != "real":
        return {"mode": "demo", "sport": "nba", "note": "Training status reads the real stores; none in demo mode."}
    from . import adaptive, training_monitor
    try:
        defn, expected = adaptive.definition(p.conn), len(adaptive.members(p.conn)[0])
    except Exception:      # an unusable definition is reported as unknown, never guessed
        defn, expected = None, None
    live = config.live_data_dir()
    path = live / sports.get(p.sport).db_file
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            out = training_monitor.run_status(conn, live / "research" / "adaptive" / p.sport, p.sport, p.now,
                                              code_definition=defn, expected_members=expected)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise HTTPException(status_code=503, detail=f"Live store unreadable for training status ({path}): {exc}") from exc
    return {**out, "live_data_dir": str(live), "serving_data_dir": str(config.data_dir()), "mode": "real"}


@app.get("/api/research/adaptive")
def research_adaptive(p=Depends(get_provider)):
    """adaptive-v1 (docs/ADAPTIVE_SYSTEM.md): fits in force, combination weights, exclusions, schedule, due state, recent
    training attempts, prospective record and the replay summary. Read-only; separate from the primary system."""
    if p.mode != "real":
        return {"mode": "demo", "version": "adaptive-v1", "sport": "nba", "combiner": None, "members": [], "attempts": [],
                "prospective": {"markets": {}, "pending": 0}, "replay": None, "due": {"due": False, "reason": "demo mode"},
                "schedule": {}, "enabled": False, "note": "The adaptive system reads the real stores; none in demo mode."}
    from . import adaptive
    if not db.has_table(p.conn, "adaptive_fits"):
        raise HTTPException(status_code=503, detail="Adaptive tables missing: run `python -m app.cli migrate` for this sport.")
    return {**adaptive.status(p.conn, p.now), "mode": "real"}


@app.get("/api/research/adaptive/game/{game_id}")
def research_adaptive_game(game_id: str, p=Depends(get_provider)):
    """Latest adaptive-v1 forecast per market for one game: probability, contributing members and weights, training
    cutoff, weight-update time, odds availability, pick or abstention with its reason."""
    if p.mode != "real":
        return {"mode": "demo", "game_id": game_id, "version": "adaptive-v1", "markets": [], "status": "no_adaptive_forecast",
                "note": "The adaptive system reads the real stores; none in demo mode."}
    from . import adaptive
    if not db.has_table(p.conn, "adaptive_forecasts"):
        raise HTTPException(status_code=503, detail="Adaptive tables missing: run `python -m app.cli migrate` for this sport.")
    return {**adaptive.game_detail(p.conn, game_id, p.now), "mode": "real"}


@app.get("/api/research/historical-start")
def research_historical_start(market: str = MARKET, p=Depends(get_provider)):
    """Separately labelled historical starting policy (app/historical_start.py): the frozen rule chosen on walk-forward
    history against the outcome base rate, and its record on later two-hour research cohorts. Never the primary forecast
    and never part of the live selector's record."""
    if p.mode != "real":
        return {"mode": "demo", "sport": "nba", "market": market, "status": "demo",
                "note": "The historical starting policy is built from the real stores; none in demo mode."}
    _market_supported(p, market)
    _research_store(p)
    from . import historical_start
    return {**historical_start.report(p.conn, p.sport, market, p.now), "mode": "real"}


@app.get("/api/research/candidates")
def research_candidates(p=Depends(get_provider)):
    """Candidate-pool roster (docs/CANDIDATE_POOL.md): training status, markets, latest fit, evaluation, prospective sample,
    combination participation and selection eligibility. Research only."""
    if p.mode != "real":
        return {"mode": "demo", "sport": "nba", "candidates": [], "family_audit": [],
                "note": "The candidate roster reads the real stores; none in demo mode."}
    from .candidates import live as pool_live
    return {**pool_live.roster(p.conn, p.sport, p.now), "mode": "real"}


@app.get("/api/research/pool")
def research_pool(p=Depends(get_provider)):
    """Candidate-pool selection (docs/CANDIDATE_POOL.md §7): the sealed registry's sources per market, their prospective
    record and the pool decisions. Separate from the core selector. Research only."""
    if p.mode != "real":
        return {"mode": "demo", "sport": "nba", "markets": {}, "note": "The pool selector reads the real stores; none in demo mode."}
    _research_store(p)
    from .candidates import selection
    try:
        out = selection.report(p.conn, p.sport, p.now)
    except selection.RegistryError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {**out, "mode": "real"}


@app.get("/api/research/status")
def research_status(p=Depends(get_provider)):
    """Per market: the selected model or exact subset, agreement requirement, threshold, versions, selection date, evidence
    cutoff, next review, evidence and status, from the core and pool decisions in force (app/selection_status.py)."""
    if p.mode != "real":
        return {"mode": "demo", "sport": "nba", "markets": [],
                "note": "Selection status reads the real stores' decisions; none in demo mode."}
    _research_store(p)
    from . import selection_status
    return {**selection_status.status(p.conn, p.now), "mode": "real"}


@app.get("/api/research/retrospective")
def research_retrospective(p=Depends(get_provider)):
    if p.mode != "real":      # the recorded study uses real results: never shown under the Demo label
        return {"mode": "demo", "sport": "nba", "status": "unavailable", "kind": "retrospective study (withheld in demo)",
                "generated_at": "", "evaluated_seasons": [], "design": "", "markets": {},
                "note": "The retrospective study uses real results; it is not part of the demo data."}
    from . import combination_research
    return {**combination_research.compact(p.sport), "mode": p.mode}


@app.get("/api/research/combinations")
def research_combinations(market: str = MARKET, base: str = Query("", max_length=1000), sport: str = SPORT,
                          mode: str = Depends(api_mode)):
    """Combinations of the saved out-of-sample forecasts (combination_research.stored). Reads saved tables, not the store,
    so it takes no store turn; a first, uncached call computes for several seconds."""
    if mode != "real":       # real historical results: never shown under the Demo label
        return {"mode": "demo", "sport": sport, "market": market, "status": "unavailable", "models": [],
                "note": "The combination analysis uses real historical results; it is not part of the demo data."}
    from . import combination_research
    try:
        return {**combination_research.stored(sport, market, [m for m in base.split(",") if m] or None), "mode": "real"}
    except InvalidRequest as exc:
        raise HTTPException(status_code=422, detail=str(exc))


SOURCE = Query("pool", pattern="^(pool|adaptive:[0-9a-f]{16})$",
               description="'pool' (candidate-pool saved tables) or 'adaptive:<definition>' (exported adaptive candidates)")


@app.get("/api/research/thresholds")
def research_thresholds(market: str = MARKET, sport: str = SPORT, source: str = SOURCE,
                        group: str = Query("", max_length=2000), base: str = Query("", max_length=2000),
                        rule: str = Query("", max_length=2000), mode: str = Depends(api_mode)):
    """Confidence thresholds, agreement x confidence, a model added at each threshold, and one exact rule
    (combination_thresholds.explore) on the saved out-of-sample forecasts. Reads saved tables only; nothing is fitted."""
    if mode != "real":
        return {"mode": "demo", "sport": sport, "market": market, "status": "unavailable", "models": [],
                "note": "The threshold explorer uses real historical results; it is not part of the demo data."}
    from . import combination_thresholds as ct
    split = lambda s: [m for m in s.split(",") if m] or None     # noqa: E731
    try:
        return {**ct.explore(sport, market, source, split(group), split(base), rule or None), "mode": "real"}
    except InvalidRequest as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/api/research/thresholds/ledger")
def research_threshold_ledger(market: str = MARKET, sport: str = SPORT, source: str = SOURCE,
                              rule: str = Query(..., min_length=3, max_length=2000),
                              period: str = Query("all", pattern="^(earlier|later|all)$"),
                              format: str = Query("json", pattern="^(json|csv)$"), mode: str = Depends(api_mode)):
    """Every qualifying game of one exact rule (combination_thresholds.ledger): teams, each member's pick and probability, the
    shared pick, actual outcome and result, and the best single model's pick on the same game. CSV for download."""
    if mode != "real":
        return {"mode": "demo", "sport": sport, "market": market, "status": "unavailable", "rows": [],
                "note": "The ledger uses real historical results; it is not part of the demo data."}
    from fastapi.responses import Response
    from . import combination_thresholds as ct
    try:
        led = ct.ledger(sport, market, source, rule, period)
    except InvalidRequest as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if format == "csv":
        name = f"ledger-{sport}-{market}-{period}.csv"
        return Response(ct.ledger_csv(led), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})
    return {**led, "mode": "real"}


@app.get("/api/research/subsets")
def research_subsets(market: str = MARKET, sport: str = SPORT, source: str = SOURCE, mode: str = Depends(api_mode)):
    """Every pair and triple of saved models with per-member thresholds: searched on the earlier period, the top rules
    frozen and scored on the later period (combination_thresholds.subsets). Exploratory; nothing is fitted or promoted."""
    if mode != "real":
        return {"mode": "demo", "sport": sport, "market": market, "status": "unavailable", "models": [],
                "note": "The subset search uses real historical results; it is not part of the demo data."}
    from . import combination_thresholds as ct
    try:
        return {**ct.subsets(sport, market, source), "mode": "real"}
    except InvalidRequest as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/api/research/history")
def research_history(limit: int = Query(200, ge=1, le=2000), p=Depends(get_provider)):
    if p.mode != "real":
        from . import research_demo
        return research_demo.history()
    _research_store(p)
    from . import research_live
    return {**research_live.history(p.conn, p.now, limit), "mode": "real"}


@app.get("/api/research/tracking/joint")
def research_tracking_joint(market: str = MARKET, evidence: str = Query("historical", pattern="^(historical|prospective)$"),
                            members: str = Query("", max_length=1000),
                            confidence: str = Query("supporters_mean", pattern="^(supporters_mean|all_mean|supporters_min)$"),
                            agreement_min: float = Query(0.5, ge=0, le=1), agreement_max: Optional[float] = Query(None, ge=0, le=1),
                            confidence_min: float = Query(0.5, ge=0, le=1), confidence_max: Optional[float] = Query(None, ge=0, le=1),
                            version: Optional[str] = Query(None, max_length=150), line: Optional[float] = None,
                            date_from: Optional[date] = None, date_to: Optional[date] = None, evaluate: bool = False,
                            p=Depends(get_provider)):
    from . import agreement_tracking as at
    if p.mode != "real":
        return {"status": "demo", "options": [], "by_definition": [], "recent": [], "note": "Joint research requires real recorded forecasts."}
    _market_supported(p, market)
    if ((agreement_max is not None and agreement_max <= agreement_min) or
            (confidence_max is not None and confidence_max <= confidence_min) or
            (date_from and date_to and date_from > date_to)):
        raise HTTPException(status_code=422, detail="Upper bounds must exceed lower bounds; dates must be ordered")
    try:
        return at.joint_report(p.conn, p.sport, market, evidence=evidence, members=[m for m in members.split(",") if m],
                               confidence=confidence, agreement_min=agreement_min, agreement_max=agreement_max,
                               confidence_min=confidence_min, confidence_max=confidence_max, version=version, line=line,
                               date_from=date_from, date_to=date_to, evaluate=evaluate)
    except InvalidRequest as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/api/research/daily-agreement")
def research_daily_agreement(p=Depends(get_provider)):
    if not hasattr(p, "conn"):
        return {"mode": "demo", "markets": [], "note": "Daily learning requires real stored results; no synthetic performance is presented."}
    from . import daily_agreement
    return {**daily_agreement.report(p.conn, p.now), "mode": "real"}


@app.get("/api/research/daily-agreement/evidence")
def daily_agreement_evidence(market: str = MARKET, rule: str = Query(..., max_length=4096),
                             period: str = Query("later", pattern="^(earlier|later)$"),
                             offset: int = Query(0, ge=0), p=Depends(get_provider)):
    if not hasattr(p, "conn"):
        raise HTTPException(status_code=400, detail="Daily evidence requires real stored results")
    from . import daily_agreement
    try:
        return daily_agreement.evidence(p.conn, p.now, market, rule, period, offset)
    except InvalidRequest as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/research/autoresearch")
def research_autoresearch(mode: str = Depends(api_mode)):
    """Sports autoresearch (docs/AUTORESEARCH.md): current protocol and bar per sport, incumbents, recent trials with per-market
    decisions and failures, holdout accesses, agent runs, and whether research is running or stopped. Reads the experiment
    directory (NBA_AUTORESEARCH_DIR) only; research, never a forecast source."""
    from . import research_status
    if mode != "real":
        return {"mode": "demo", "configured": False, "note": research_status.NOTE,
                "reason": "Autoresearch status reads the real experiment directory; none in demo mode."}
    return {**research_status.status(), "mode": "real"}


@app.get("/api/research/candidates/game/{game_id}")
def research_candidates_game(game_id: str, as_of: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"),
                             p=Depends(get_provider)):
    """Every candidate model's recorded pregame forecasts for one game (candidate_forecasts), neural ones included: each model
    version separately, with line, line source, fit and input provenance. Research only: never canonical, never a vote."""
    from . import candidates_view
    if p.mode != "real":
        return {"mode": "demo", "game_id": game_id, "forecasts": [], "without_forecast": [], "note": candidates_view.NOTE}
    out = candidates_view.game(p.conn, p.sport, game_id, as_of or p.now)
    if out is None:
        raise HTTPException(status_code=404, detail=f"Unknown game {game_id}")
    return {**out, "mode": "real"}
