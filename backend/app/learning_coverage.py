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


def current_definitions(conn) -> dict:
    """The CURRENT weekly and daily adaptive definitions, computed on the (read-only) store as the trainer would."""
    from . import adaptive
    weekly = adaptive.definition(conn)
    with adaptive.cadence("daily"):
        daily = adaptive.definition(conn)
    return {"weekly": weekly, "daily": daily}


def _learning_row(conn, sport: str, market: str, trainer: dict, defs: dict) -> dict:
    """The stored states of an adaptive market, restricted to the current weekly and daily definitions; each unknown (not
    false) when a table or the definitions are unavailable."""
    def need(table):
        return None if learning_ops.has_table(conn, table) else _s("unknown", f"unknown: table missing ({table})")
    out = {}
    scheduled = _s("yes", f"{trainer['label']} at {', '.join(trainer['times'])} covers {sport}") if trainer["covers_sport"] else _s("no", trainer["reason"])
    if "error" in defs:
        u = _s("unknown", f"unknown: definition unavailable ({defs['error']})")
        return {"trained": u, "scheduled": scheduled, "recording_prospectively": u, "forecasting_with_fits": u, "evaluated": u}
    ids = sorted(set(defs.values()))
    marks = ",".join("?" * len(ids))
    miss = need("adaptive_fits")
    if miss:
        out["trained"] = miss
    else:
        trained = None
        for f in conn.execute(f"SELECT fit_id, definition, finished_at, detail FROM adaptive_fits WHERE sport=? AND kind='combiner' AND status IN {IN_FORCE} "
                              f"AND definition IN ({marks}) ORDER BY fit_id DESC", (sport, *ids)):
            try:
                m = json.loads(f["detail"]).get("markets", {}).get(market)
                has_weights = bool(m.get("weights"))
            except (ValueError, AttributeError):
                has_weights = False
            if has_weights:
                trained = f
                break
        out["trained"] = _s("yes", f"combiner fit #{trained['fit_id']} (current definition {trained['definition'][:16]}) finished {trained['finished_at']} has weights for this market") if trained else \
            _s("no", "no successful combiner fit of a current definition has weights for this market")
    out["scheduled"] = scheduled
    u = need("adaptive_forecasts") or miss
    if u:
        out["recording_prospectively"] = out["forecasting_with_fits"] = out["evaluated"] = u
        return out
    first = conn.execute(f"SELECT MIN(finished_at) FROM adaptive_fits WHERE sport=? AND kind='combiner' AND status IN {IN_FORCE} AND definition IN ({marks})",
                         (sport, *ids)).fetchone()[0]
    n_rec = 0 if first is None else conn.execute(f"SELECT COUNT(*) FROM adaptive_forecasts WHERE sport=? AND market=? AND definition IN ({marks}) "
                                                 "AND generated_at >= ?", (sport, market, *ids, first)).fetchone()[0]
    out["recording_prospectively"] = _s("yes", f"{n_rec} forecast rows (any status) of a current definition written after the first in-force fit") if n_rec else \
        _s("no", "no forecast rows of a current definition written after a fit finished")
    base = ("FROM adaptive_forecasts a JOIN adaptive_fits f ON f.fit_id=a.combiner_fit_id AND f.definition=a.definition AND f.kind='combiner' "
            f"AND f.sport=a.sport WHERE a.sport=? AND a.market=? AND a.status='forecast' AND f.status IN {IN_FORCE} "
            f"AND a.definition IN ({marks}) AND a.generated_at >= f.finished_at")
    args = (sport, market, *ids)
    n_fc = conn.execute(f"SELECT COUNT(*) {base}", args).fetchone()[0]
    out["forecasting_with_fits"] = _s("yes", f"{n_fc} rows with status 'forecast' carry a fit of the same current definition finished before they were generated") if n_fc else \
        _s("no", "no 'forecast' rows of a current definition carry a fit finished before generation (rows may be 'unavailable')")
    if not learning_ops.has_table(conn, "games"):
        out["evaluated"] = _s("unknown", "unknown: table missing (games)")
        return out
    n_ev = conn.execute(f"SELECT COUNT(*) {base} AND a.game_id IN (SELECT game_id FROM games WHERE status='final')", args).fetchone()[0] if n_fc else 0
    out["evaluated"] = _s("yes", f"{n_ev} settled forecasts carried a fit (a count, not a verdict)") if n_ev else \
        _s("no", "no settled forecast carried a fit")
    return out


def learning_challengers_error():
    from . import learning_challengers
    return learning_challengers.PolicyError


def _ncaab_states(data_dir: Path, market: str, store, challengers: dict) -> dict:
    """NCAAB challenger states (learning_challengers/ncaab_challengers): implemented, but not scheduled and never serving.
    `trained` is yes only when a refit cache with fitted values exists in the data directory being read; `recording` and
    `evaluated` count challenger rows (settled ones need the NCAAB store); the served forecast stays frozen."""
    no = lambda why: _s("no", why)
    if "error" in challengers:
        u = _s("unknown", f"unknown: challenger policy files unreadable ({challengers['error']})")
        return {"trained": u, "scheduled": no("no job is installed for NCAAB challengers"), "recording_prospectively": u,
                "forecasting_with_fits": no("served NCAAB forecasts are frozen"), "evaluated": u}
    names = challengers["markets"].get(market)
    if not names:
        gone = no("no NCAAB challenger covers this market (C1-C4 cover winner, spread, total, team totals)")
        return {"trained": gone, "scheduled": no("no job is installed for NCAAB challengers"), "recording_prospectively": gone,
                "forecasting_with_fits": no("served NCAAB forecasts are frozen"), "evaluated": gone}
    cache = Path(data_dir) / "research" / "challengers" / "ncaab"
    fitted = []
    for f in sorted(cache.glob("refit-*.json")):
        try:
            if any("sigmas" in v for v in json.loads(f.read_text())["fits"].values()):
                fitted.append(f.name)
        except (OSError, ValueError, KeyError, AttributeError):
            continue
    out = {"trained": _s("yes", f"{len(fitted)} weekly refit cache file(s) with fitted values; latest {fitted[-1]}") if fitted else
           no("no refit cache with fitted values in this data directory (challengers are fitted at record time and cached)"),
           "scheduled": no("no job is installed for NCAAB challengers: they are run by hand (learning_run)"),
           "forecasting_with_fits": no("served NCAAB forecasts are frozen; challengers never serve")}
    cc = learning_challengers_ro(data_dir)
    if cc is None:
        out["recording_prospectively"] = no("no challenger store in this data directory")
        out["evaluated"] = no("no challenger rows to settle")
        return out
    try:
        n = cc.execute("SELECT COUNT(*) FROM challenger_forecasts WHERE policy_id LIKE 'ncaab-%' AND market=?", (market,)).fetchone()[0]
        ids = [r[0] for r in cc.execute("SELECT DISTINCT game_id FROM challenger_forecasts WHERE policy_id LIKE 'ncaab-%' AND market=?", (market,))]
    except sqlite3.Error as exc:
        cc.close()
        u = _s("unknown", f"unknown: {exc}")
        out["recording_prospectively"] = out["evaluated"] = u
        return out
    cc.close()
    out["recording_prospectively"] = _s("yes", f"{n} challenger rows recorded for this market (separate store, before tip-off)") if n else \
        no("no challenger rows recorded for this market")
    scored, err = 0, None
    if ids and store is not None and learning_ops.has_table(store, "games"):
        scored, err = _scored_pairs(store, data_dir, "ncaab-", (market,))
    out["evaluated"] = _s("unknown", f"unknown: scoring failed ({err})") if err else \
        _s("yes", f"{scored} scored pairs (settled, not void, paired with the served forecast; a count, no claim until the preregistered n)") if scored else \
        no("no scored pair yet (settled, not void, with a served incumbent)")
    return out


def _scored_pairs(store, data_dir: Path, prefix: str, markets) -> tuple:
    """(scored pairs, error) for a sport's challenger rows: settled, not void, with an incumbent (learning_eval.evaluate)."""
    from . import learning_eval
    cc = learning_challengers_ro(data_dir)
    if cc is None or store is None:
        return 0, None
    try:
        groups = learning_eval.evaluate(store, cc)["groups"]
    except (sqlite3.Error, ValueError, KeyError) as exc:
        return 0, f"{type(exc).__name__}: {exc}"
    finally:
        cc.close()
    return sum(g["n_scored"] for g in groups if g["policy_id"].startswith(prefix) and g["market"] in markets), None


SPEC_STORE = {"soccer": ("soccer_matches", "match_id"), "nfl": ("games", "game_id")}
SPEC_MARKETS = {"soccer": {"1x2": ("soccer_1x2",), "double_chance": ("soccer_1x2",), "draw_no_bet": ("soccer_1x2",),
                           "goals_ou": ("soccer_goals_total",), "team_goals": ("soccer_goals_home", "soccer_goals_away"),
                           "btts": ("soccer_btts",)},
                "nfl": {m: (m,) for m in ("winner", "spread", "total", "team_total_home", "team_total_away")}}
SPEC_EVAL = {"double_chance": ("soccer_double_chance",), "draw_no_bet": ("soccer_1x2",)}     # derived from the 1X2 rows at read time
SPEC_SKIPPED = {"soccer:corners": "corners are skipped: no corner challenger exists",
                "soccer:corner_handicap": "corner handicap is skipped: no corner challenger exists",
                "soccer:asian_handicap": "Asian handicap is research only and blocked on the owner's preregistration answers: no challenger",
                "soccer:intl_1x2": "international is skipped: insufficient evidence"}


def _spec_states(sport: str, data_dir: Path, market: str, store) -> dict:
    """Soccer and NFL challenger states (soccer_challengers / nfl_challengers): implemented, never scheduled, never serving.
    Challengers are fitted in memory at record time, so nothing trained is stored. Rows are counted in the challenger store;
    settled ones need the sport store. Counts only, never a verdict."""
    no = lambda why: _s("no", why)
    stored = SPEC_MARKETS[sport].get(market)
    base = {"trained": no("fitted per cutoff in memory at record time; no fit is stored"),
            "scheduled": no("no job is installed for these challengers: they are run by hand (learning_run)"),
            "forecasting_with_fits": no(f"served {sport} forecasts are sealed or frozen; challengers never serve")}
    if not stored:
        gone = no(SPEC_SKIPPED.get(f"{sport}:{market}", f"no {sport} challenger covers this market"))
        return {**base, "recording_prospectively": gone, "evaluated": gone}
    cc = learning_challengers_ro(data_dir)
    if cc is None:
        return {**base, "recording_prospectively": no("no challenger store in this data directory"), "evaluated": no("no challenger rows to settle")}
    marks = ",".join("?" * len(stored))
    try:
        n = cc.execute(f"SELECT COUNT(*) FROM challenger_forecasts WHERE policy_id LIKE ? AND market IN ({marks})", (f"{sport}-%", *stored)).fetchone()[0]
        ids = [r[0] for r in cc.execute(f"SELECT DISTINCT game_id FROM challenger_forecasts WHERE policy_id LIKE ? AND market IN ({marks})", (f"{sport}-%", *stored))]
    except sqlite3.Error as exc:
        u = _s("unknown", f"unknown: {exc}")
        return {**base, "recording_prospectively": u, "evaluated": u}
    finally:
        cc.close()
    base["recording_prospectively"] = _s("yes", f"{n} challenger rows recorded for this market (separate store, before kickoff)") if n else \
        no("no challenger rows recorded for this market")
    table, col = SPEC_STORE[sport]
    scored, err = 0, None
    if ids and store is not None and learning_ops.has_table(store, table):
        scored, err = _scored_pairs(store, data_dir, f"{sport}-", SPEC_EVAL.get(market, stored))
    base["evaluated"] = _s("unknown", f"unknown: scoring failed ({err})") if err else \
        _s("yes", f"{scored} scored pairs (settled, not void, paired with the served forecast; a count, no claim until the preregistered n)") if scored else \
        no("no scored pair yet (settled, not void, with a served incumbent)")
    return base


def learning_challengers_ro(data_dir: Path):
    from . import learning_challengers
    return learning_challengers.open_ro(Path(data_dir) / "research" / "challengers.sqlite")


def _ncaab_challengers() -> dict:
    """{'markets': {market: [challenger names]}} from the NCAAB policy files."""
    from . import learning_challengers
    out: dict = {}
    for pol in learning_challengers.load_policies("ncaab"):
        for c in pol.challengers:
            for m in c.spec.extra["markets"]:
                out.setdefault(m, []).append(c.spec.name)
    return {"markets": out}


def coverage(data_dir: Optional[Path] = None, folder: Optional[Path] = None) -> dict:
    data_dir = Path(data_dir) if data_dir else config.data_dir()
    folder = Path(folder) if folder else learning_ops.agents_dir()
    ref = json.loads(REFERENCE.read_text())
    rows, stores, definitions = [], {}, {}
    for sport in ("nba", "nhl", "ncaab", "nfl", "soccer"):
        learning = sport in LEARNING_SPORTS
        trainer = learning_ops.trainer(sport, folder)
        conn, mode = learning_ops.open_ro(data_dir, sport) if learning else (None, "not read: no learning for this sport")
        ncaab_store = None
        if sport in ("ncaab", "nfl", "soccer"):
            ncaab_store, mode = learning_ops.open_ro(data_dir, sport)
        if sport == "ncaab":
            try:
                ch = _ncaab_challengers()
            except learning_challengers_error() as exc:
                ch = {"error": str(exc)}
        stores[sport] = mode
        defs = None
        if conn is not None:
            try:
                defs = current_definitions(conn)
            except Exception as exc:      # any failure: the states become unknown, never yes
                defs = {"error": str(exc) or type(exc).__name__}
            definitions[sport] = defs if "error" not in defs else f"unknown: definition unavailable ({defs['error']})"
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
                            states = _learning_row(conn, sport, market, trainer, defs)
                        except sqlite3.Error as exc:
                            states = {k: _s("unknown", f"unknown: {exc}") for k in ("trained", "scheduled", "recording_prospectively", "forecasting_with_fits", "evaluated")}
                elif sport == "ncaab":
                    states = _ncaab_states(data_dir, market, ncaab_store, ch)
                elif sport in SPEC_MARKETS:
                    states = _spec_states(sport, data_dir, market, ncaab_store)
                else:
                    no = _s("no", "learning is not implemented for this sport")
                    states = {"trained": no, "scheduled": no, "recording_prospectively": no, "forecasting_with_fits": no,
                              "evaluated": _s("no", "no fit exists, so no settled forecast carried one")}
                ev = ref["base_evaluation"].get(key)
                states["base_tested"] = _s(ev[0], f"base model only: {ev[1]}") if ev else _s("unknown", "base-model test not recorded here")
                rows.append({"sport": sport, "market": market, "implemented": implemented, "learning_implemented": learning or sport in ("ncaab", "nfl", "soccer"), **states,
                             "improved_prospectively": _s("not_established", "no preregistered prospective evaluation exists"),
                             "blocked_reason": [*ref["sports"].get(sport, []), *ref["markets"].get(key, [])]})
        finally:
            if conn is not None:
                conn.close()
            if ncaab_store is not None:
                ncaab_store.close()
    return {"rows": rows, "stores": stores, "definitions": definitions, "trainer": {s: learning_ops.trainer(s, folder)["covers_sport"] for s in LEARNING_SPORTS},
            "note": ("Separate states: a market can be trained and scheduled without forecasting with fits. Nothing here claims "
                     "improvement. Blocked reasons are curated facts as of " + ref["source"].split(";")[0]) + "."}
