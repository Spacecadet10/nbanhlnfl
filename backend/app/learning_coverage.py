"""Learning coverage matrix: every sport x every app-supported market, with separate states. Read-only.

States are computed from the stores and the installed trainer, never inferred from one another: a market can be trained
and scheduled and still not forecast with a fit. `improved_prospectively` is never established (no preregistered
prospective evaluation exists). Blocked reasons and base-model evaluations of sports without learning are curated facts in
reference/learning_blocked.json. Not hashed into any model definition."""
import json
import sqlite3
from pathlib import Path
from typing import Optional

from . import config, learning_ops, sports

REFERENCE = Path(__file__).parent / "reference" / "learning_blocked.json"
LEARNING_SPORTS = ("nba", "nhl")
SOCCER_MARKETS = ("1x2", "double_chance", "draw_no_bet", "goals_ou", "team_goals", "btts", "corners", "corner_handicap",
                  "asian_handicap", "intl_1x2")
NOT_IMPLEMENTED = {"asian_handicap": "research only: no forecast market"}
IN_FORCE = "('success','partial')"


def _s(state: str, detail: str = "") -> dict:
    return {"state": state, "detail": detail}


def _markets(sport: str) -> tuple:
    if sport == "soccer":
        return SOCCER_MARKETS
    return {"nba": sports.NBA, "nhl": sports.NHL, "ncaab": sports.NCAAB, "nfl": sports.NFL}[sport].markets


def _learning_row(conn, sport: str, market: str, trainer: dict) -> dict:
    """The five stored states of an adaptive market; each unknown (not false) when its table is missing."""
    def need(table):
        return None if learning_ops.has_table(conn, table) else _s("unknown", f"unknown: table missing ({table})")
    out = {}
    miss = need("adaptive_fits")
    if miss:
        out["trained"] = miss
    else:
        trained = None
        for f in conn.execute(f"SELECT fit_id, finished_at, detail FROM adaptive_fits WHERE sport=? AND kind='combiner' AND status IN {IN_FORCE} "
                              "ORDER BY fit_id DESC", (sport,)):
            try:
                m = json.loads(f["detail"]).get("markets", {}).get(market)
            except (ValueError, AttributeError):
                m = None
            if m and m.get("weights"):
                trained = f
                break
        out["trained"] = _s("yes", f"combiner fit #{trained['fit_id']} finished {trained['finished_at']} has weights for this market") if trained else \
            _s("no", "no successful combiner fit has weights for this market")
    out["scheduled"] = _s("yes", f"{trainer['label']} at {', '.join(trainer['times'])} covers {sport}") if trainer["covers_sport"] else \
        _s("no", trainer["reason"])
    u = need("adaptive_forecasts") or miss
    if u:
        out["recording_prospectively"] = out["forecasting_with_fits"] = out["evaluated"] = u
        return out
    first = conn.execute(f"SELECT MIN(finished_at) FROM adaptive_fits WHERE sport=? AND kind='combiner' AND status IN {IN_FORCE}", (sport,)).fetchone()[0]
    n_rec = 0 if first is None else conn.execute("SELECT COUNT(*) FROM adaptive_forecasts WHERE sport=? AND market=? AND generated_at >= ?",
                                                 (sport, market, first)).fetchone()[0]
    out["recording_prospectively"] = _s("yes", f"{n_rec} forecast rows (any status) written after the first in-force fit") if n_rec else \
        _s("no", "no forecast rows written after a fit finished")
    base = (f"FROM adaptive_forecasts a JOIN adaptive_fits f ON f.fit_id=a.combiner_fit_id WHERE a.sport=? AND a.market=? AND a.status='forecast' "
            f"AND f.status IN {IN_FORCE} AND a.generated_at >= f.finished_at")
    n_fc = conn.execute(f"SELECT COUNT(*) {base}", (sport, market)).fetchone()[0]
    out["forecasting_with_fits"] = _s("yes", f"{n_fc} rows with status 'forecast' carry a fit finished before they were generated") if n_fc else \
        _s("no", "no 'forecast' rows carry a fit finished before generation (rows may be 'unavailable')")
    if n_fc and learning_ops.has_table(conn, "games"):
        n_ev = conn.execute(f"SELECT COUNT(*) {base} AND a.game_id IN (SELECT game_id FROM games WHERE status='final')", (sport, market)).fetchone()[0]
    else:
        n_ev = 0
    out["evaluated"] = _s("yes", f"{n_ev} settled forecasts carried a fit (a count, not a verdict)") if n_ev else \
        _s("no", "no settled forecast carried a fit")
    return out


def coverage(data_dir: Optional[Path] = None, folder: Optional[Path] = None) -> dict:
    data_dir = Path(data_dir) if data_dir else config.data_dir()
    folder = Path(folder) if folder else learning_ops.agents_dir()
    ref = json.loads(REFERENCE.read_text())
    rows, stores = [], {}
    for sport in ("nba", "nhl", "ncaab", "nfl", "soccer"):
        learning = sport in LEARNING_SPORTS
        trainer = learning_ops.trainer(sport, folder)
        conn, mode = learning_ops.open_ro(data_dir, sport) if learning else (None, "not read: no learning for this sport")
        stores[sport] = mode
        try:
            for market in _markets(sport):
                key = f"{sport}:{market}"
                implemented = _s("no", NOT_IMPLEMENTED[market]) if sport == "soccer" and market in NOT_IMPLEMENTED else _s("yes", "app serves this market")
                if learning:
                    if conn is None:
                        unknown = _s("unknown", f"unknown: store {mode}")
                        states = {k: unknown for k in ("trained", "recording_prospectively", "forecasting_with_fits", "evaluated")}
                        states["scheduled"] = _s("yes", f"{trainer['label']} covers {sport}") if trainer["covers_sport"] else _s("no", trainer["reason"])
                    else:
                        try:
                            states = _learning_row(conn, sport, market, trainer)
                        except sqlite3.Error as exc:
                            states = {k: _s("unknown", f"unknown: {exc}") for k in ("trained", "scheduled", "recording_prospectively", "forecasting_with_fits", "evaluated")}
                else:
                    no = _s("no", "learning is not implemented for this sport")
                    ev = ref["base_evaluation"].get(key)
                    states = {"trained": no, "scheduled": no, "recording_prospectively": no, "forecasting_with_fits": no,
                              "evaluated": _s(ev[0], f"base model only: {ev[1]}") if ev else _s("unknown", "base-model evaluation not recorded here")}
                rows.append({"sport": sport, "market": market, "implemented": implemented, "learning_implemented": learning, **states,
                             "improved_prospectively": _s("not_established", "no preregistered prospective evaluation exists"),
                             "blocked_reason": [*ref["sports"].get(sport, []), *ref["markets"].get(key, [])]})
        finally:
            if conn is not None:
                conn.close()
    return {"rows": rows, "stores": stores, "trainer": {s: learning_ops.trainer(s, folder)["covers_sport"] for s in LEARNING_SPORTS},
            "note": ("Separate states: a market can be trained and scheduled without forecasting with fits. Nothing here claims "
                     "improvement. Blocked reasons are curated facts as of " + ref["source"].split(";")[0]) + "."}
