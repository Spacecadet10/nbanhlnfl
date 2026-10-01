"""FastAPI entry point. One API contract, two providers selected by NBA_API_MODE:
`real` (default: SQLite store + prediction ledger) or `demo` (synthetic fixtures, always labelled Demo)."""
import os
import sqlite3
from datetime import date
from typing import Iterator, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from . import config, db
from .providers.demo import DemoProvider
from .providers.real import RealProvider
from .schemas import GameDetail, GamesResponse, Health, HistoryResponse, Meta, PerformanceResponse, SourceStatus

API_VERSION = "0.2.0"

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


def get_provider(mode: str = Depends(api_mode)) -> Iterator:
    if mode == "demo":
        yield DemoProvider()
        return
    if not config.db_path().is_file():
        raise HTTPException(status_code=503, detail="No data store found. Run `python -m app.cli migrate` and "
                                                    "`ingest` from backend/, or start the API with NBA_API_MODE=demo.")
    conn = db.connect(check_same_thread=False)
    try:
        if conn.execute("SELECT name FROM sqlite_master WHERE name='job_runs'").fetchone() is None:
            raise HTTPException(status_code=503, detail="Data store needs migrations: run `python -m app.cli migrate`.")
        yield RealProvider(conn)
    except sqlite3.Error as exc:
        raise HTTPException(status_code=503, detail=f"Data store error: {exc}") from exc
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
def games(date: date = Query(description="Game date (US Eastern), YYYY-MM-DD"), p=Depends(get_provider)):
    return p.games(date)


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
                limit: int = Query(500, ge=1, le=5000), p=Depends(get_provider)):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must be on or before date_to")
    return p.predictions(run_type, date_from, date_to, team, limit, min_confidence)


@app.get("/api/performance", response_model=PerformanceResponse)
def performance(run_type: str = Query("live", pattern="^(live|backtest)$"), p=Depends(get_provider)):
    return p.performance(run_type)
