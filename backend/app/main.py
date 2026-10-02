"""FastAPI entry point. One API contract, two providers selected by NBA_API_MODE:
`real` (default: SQLite store + prediction ledger) or `demo` (synthetic fixtures, always labelled Demo)."""
import os
import sqlite3
from datetime import date
from typing import Iterator, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from . import config, db, sports
from .backtest import FrozenDecisionError
from .nhl_eval import NhlEvaluationError
from .providers.demo import DemoProvider
from .providers.real import RealProvider
from .schemas import (GameDetail, GamesResponse, Health, HistoryResponse, MarketHistoryResponse, MarketPerformanceResponse,
                      Meta, PerformanceResponse, SourceStatus)

API_VERSION = "0.3.0"
SPORT = Query("nba", pattern="^(nba|nhl)$", description="Which sport's data store to read. Never falls back to another.")
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


def get_provider(mode: str = Depends(api_mode), sport: str = SPORT) -> Iterator:
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
    return Health(status="ok", mode=mode, api_version=API_VERSION)


@app.get("/api/meta", response_model=Meta)
def meta(p=Depends(get_provider)):
    return p.meta()


@app.get("/api/sources", response_model=list[SourceStatus])
def sources(p=Depends(get_provider)):
    return p.sources()


@app.get("/api/games", response_model=GamesResponse)
def games(date: date = Query(description="Game date (US Eastern), YYYY-MM-DD"),
          policy: str = Query("pregame", pattern="^(pregame|near_tipoff)$",
                              description="Which forecast the slate's picks and confidence filter use."),
          min_confidence: Optional[float] = Query(None, ge=0, le=1), market: str = MARKET, p=Depends(get_provider)):
    if market == "winner" and min_confidence is not None and min_confidence < 0.5:
        raise HTTPException(status_code=422, detail="min_confidence for the winner market is at least 0.5")
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


@app.get("/api/predictions", response_model=HistoryResponse)
def predictions(run_type: str = Query("live", pattern="^(live|backtest)$"), date_from: Optional[date] = None,
                date_to: Optional[date] = None, team: Optional[str] = None,
                min_confidence: Optional[float] = Query(
                    None, ge=0.5, le=1, description="Keep only picks whose model probability for the predicted winner, "
                                                    "max(p_home, 1 - p_home), is at least this value."),
                policy: str = Query("pregame", pattern="^(pregame|near_tipoff)$",
                                    description="Live forecasts only: night-before (pregame) or near-tipoff."),
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
                       limit: int = Query(500, ge=1, le=5000), p=Depends(get_provider)):
    if market == "winner":
        raise HTTPException(status_code=422, detail="Use /api/predictions for the winner market")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must be on or before date_to")
    _market_supported(p, market)
    return p.market_predictions(market, policy, date_from, date_to, team, limit, min_confidence)


@app.get("/api/market-performance", response_model=MarketPerformanceResponse)
def market_performance(market: str = MARKET, policy: str = Query("pregame", pattern="^(pregame|near_tipoff)$"),
                       p=Depends(get_provider)):
    if market == "winner":
        raise HTTPException(status_code=422, detail="Use /api/performance for the winner market")
    _market_supported(p, market)
    return p.market_performance(market, policy)


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


@app.get("/api/thresholds")
def thresholds(p=Depends(get_provider)):
    if p.mode != "real":
        return {"groups": [], "all_stored_rows": {"winner_rows": 0, "market_rows": 0}, "note": "No forward threshold research in demo mode."}
    from . import tracking
    return tracking.report(p.conn, p.now)


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
def research_rules(market: str = MARKET, show: str = Query("selected", pattern="^(selected|all)$"), p=Depends(get_provider)):
    """Registered combination rules (docs/MODEL_SELECTION.md §15): historical exploration and prospective record."""
    if p.mode != "real":
        return {"mode": "demo", "sport": "nba", "market": market, "status": "demo", "rules": [],
                "note": "Combination-rule tracking uses recorded research cohorts; none exist in demo mode."}
    _market_supported(p, market)
    _research_store(p)
    from . import rule_tracking
    try:
        out = rule_tracking.report(p.conn, p.sport, market, show)
    except rule_tracking.RegistryError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {**out, "mode": "real"}


@app.get("/api/research/candidates")
def research_candidates(p=Depends(get_provider)):
    """Candidate-pool roster (docs/CANDIDATE_POOL.md): training status, markets, latest fit, evaluation, prospective sample,
    combination participation and selection eligibility. Research only."""
    if p.mode != "real":
        return {"mode": "demo", "sport": "nba", "candidates": [], "family_audit": [],
                "note": "The candidate roster reads the real stores; none in demo mode."}
    from .candidates import live as pool_live
    return {**pool_live.roster(p.conn, p.sport, p.now), "mode": "real"}


@app.get("/api/research/retrospective")
def research_retrospective(p=Depends(get_provider)):
    from . import combination_research
    return {**combination_research.compact(p.sport), "mode": p.mode}


@app.get("/api/research/history")
def research_history(limit: int = Query(200, ge=1, le=2000), p=Depends(get_provider)):
    if p.mode != "real":
        from . import research_demo
        return research_demo.history()
    _research_store(p)
    from . import research_live
    return {**research_live.history(p.conn, p.now, limit), "mode": "real"}
