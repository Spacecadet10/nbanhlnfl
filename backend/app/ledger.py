"""Append-only prediction ledger.

The idempotency key identifies one forecast: game, run type, model/dataset/feature versions and cutoff.
Retrying the same forecast returns the stored row; a different probability under the same key is a conflict.
A genuinely new forecast (new cutoff or version) gets a new key and a new row. The database rejects
UPDATE/DELETE on stored predictions and live forecasts made after cutoff/tipoff.
"""
import hashlib
import sqlite3
from dataclasses import dataclass
from typing import Optional

from .db import transaction

PROB_TOLERANCE = 1e-9


class LedgerConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class Forecast:
    game_id: str
    run_type: str
    model_version: str
    dataset_version: str
    feature_version: str
    cutoff_at: str
    generated_at: str
    home_win_prob: float

    @property
    def idempotency_key(self) -> str:
        parts = (self.game_id, self.run_type, self.model_version, self.dataset_version, self.feature_version,
                 self.cutoff_at)
        return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def record(conn: sqlite3.Connection, f: Forecast) -> tuple[int, bool]:
    """Stores a forecast; returns (prediction_id, created). Safe to retry."""
    with transaction(conn):
        existing = conn.execute("SELECT prediction_id, home_win_prob FROM predictions WHERE idempotency_key=?",
                                (f.idempotency_key,)).fetchone()
        if existing:
            if abs(existing["home_win_prob"] - f.home_win_prob) > PROB_TOLERANCE:
                raise LedgerConflict(
                    f"Forecast {f.idempotency_key[:12]} already stored with p={existing['home_win_prob']}; "
                    f"got p={f.home_win_prob}. Use a new model/feature version or cutoff for a new forecast.")
            return existing["prediction_id"], False
        cur = conn.execute(
            "INSERT INTO predictions (idempotency_key, game_id, run_type, model_version, dataset_version, "
            "feature_version, cutoff_at, generated_at, home_win_prob) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f.idempotency_key, f.game_id, f.run_type, f.model_version, f.dataset_version, f.feature_version,
             f.cutoff_at, f.generated_at, f.home_win_prob))
        return cur.lastrowid, True


@dataclass(frozen=True)
class MarketForecast:
    """One market forecast at an exact line (docs/MARKETS.md). The line is part of the identity: a forecast at another
    line is a different forecast and never replaces this one."""
    sport: str
    game_id: str
    market: str
    line: Optional[float]
    line_source: Optional[str]
    market_quote_id: Optional[int]
    policy: str
    run_type: str
    model_version: str
    dataset_version: str
    feature_version: str
    cutoff_at: str
    generated_at: str
    tipoff_at: str
    p_a: float
    p_mid: float
    p_b: float
    params: str = "{}"

    @property
    def idempotency_key(self) -> str:
        line = None if self.line is None else float(self.line) + 0.0   # -4 == -4.0 and -0.0 == 0.0: one forecast
        parts = (self.sport, self.game_id, self.market, repr(line), self.policy, self.run_type, self.model_version,
                 self.dataset_version, self.feature_version, self.cutoff_at)
        return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def record_market(conn: sqlite3.Connection, f: MarketForecast) -> tuple[int, bool]:
    """Stores a market forecast; returns (forecast_id, created). A retry returns the stored row; different
    probabilities under the same identity are a conflict, never an overwrite."""
    with transaction(conn):
        existing = conn.execute("SELECT forecast_id, p_a, p_mid, p_b FROM market_forecasts WHERE idempotency_key=?",
                                (f.idempotency_key,)).fetchone()
        if existing:
            if any(abs(existing[k] - getattr(f, k)) > PROB_TOLERANCE for k in ("p_a", "p_mid", "p_b")):
                raise LedgerConflict(f"Market forecast {f.idempotency_key[:12]} already stored with other probabilities")
            return existing["forecast_id"], False
        cols = ("idempotency_key", "sport", "game_id", "market", "line", "line_source", "market_quote_id", "policy",
                "run_type", "model_version", "dataset_version", "feature_version", "cutoff_at", "generated_at",
                "tipoff_at", "p_a", "p_mid", "p_b", "params")
        values = [f.idempotency_key] + [getattr(f, c) for c in cols[1:]]
        cur = conn.execute(f"INSERT INTO market_forecasts ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", values)
        return cur.lastrowid, True
