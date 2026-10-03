"""Live research cohorts, weekly selector decisions and research selections (docs/MODEL_SELECTION.md §6-7).

Shadow only: nothing here changes a primary forecast, and a failure here never blocks one. Every row is written inside
[scheduled start − 2 h, start) against the game's current start (database triggers). Each cohort records the exact line and
quote the primary used in the same run, every member's stored forecast and probability vector, missing members with reasons,
the equal average and the stacker (both only for complete cohorts) and the agreement counts.
"""
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Optional

from . import agreement, backtest, combine, live, market_live, markets, near_tipoff, selector, sports
from .db import transaction, utc_now
from .ledger import MarketForecast, record_market

PREFIX = "research-v3"
STACK_SEASONS = 2
STACK_MIN_TRAIN = 300
LINE_MEMBERS = {"nba": ("points_normal", "ridge_points", "gbt_points"), "nhl": ("goals_poisson", "goals_draw", "goals_glm")}
# Separately versioned shadow members (docs/MODEL_SELECTION.md §9): recorded and scored as sources, never part of the
# average, the stacker or agreement, so the core cohort definition is unchanged.
SHADOW_LINE_MEMBERS = {"nba": ("ridge_points@weekly", "gbt_points@weekly"), "nhl": ()}
# Separate, versioned expanded-combination experiment (docs/MODEL_SELECTION.md §14): every member incl. shadows. Recording only:
# never read by the selector, selections or recommendations; the core cohort and definition are unchanged.
EXPANDED = "expanded-v1"


def expanded_definition(defn: str) -> str:
    return f"{defn}:{EXPANDED}"


def is_expanded(defn: str) -> bool:
    return defn.endswith(f":{EXPANDED}")


def shadows(sport: str, market: str) -> tuple:
    return agreement.SHADOW[sport] if market == "winner" else SHADOW_LINE_MEMBERS[sport]
CORE = {"nba": agreement.NAMES["nba"][:5], "nhl": agreement.NAMES["nhl"]}
FMT = "%Y-%m-%dT%H:%M:%SZ"
SIDE = {"a": 0, "mid": 1, "b": 2}


def enabled() -> bool:
    """Off switch for research cohorts, decisions and selections (primary forecasts are unaffected)."""
    return os.environ.get("RESEARCH_SELECTION", "1") != "0"


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, FMT).replace(tzinfo=timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.strftime(FMT)


def line_models(conn) -> list:
    """Primary market model first, then the registered candidates (docs/MODEL_SELECTION.md §2)."""
    from .models.score_candidates import GbtPoints, GoalsDraw, GoalsGlm, RidgePoints
    primary, _ = market_live.market_model(conn)
    eng = live.engine(conn)
    if eng.sport == "nhl":
        from . import nhl_eval
        from .models.hockey import GoalsConfig, HockeyEloConfig
        d = nhl_eval.load_decision(conn)
        key = sports.NHL.rating_key
        return [primary, GoalsDraw(GoalsConfig(**d["goals_config"]), key), GoalsGlm(HockeyEloConfig(**d["hockey_elo_config"]), key)]
    from .retrain_cadence import Cadenced
    _, cfg = backtest.load_frozen_decision(conn)
    return [primary, RidgePoints(cfg), GbtPoints(cfg), Cadenced(RidgePoints(cfg), "weekly"), Cadenced(GbtPoints(cfg), "weekly")]


def definition(conn) -> str:
    """Research identity: winner members (agreement digest), line members, combination and selection code, library version."""
    h = hashlib.sha256(agreement.definition_hash(agreement.models(conn)).encode())
    root = Path(__file__).parent
    for name in ("combine.py", "research_live.py", "markets.py", "market_live.py", "models/score_candidates.py",
                 "retrain_cadence.py", "fit_register.py"):
        h.update(name.encode())
        h.update((root / name).read_bytes())
    h.update(json.dumps([(m.name, m.config()) for m in line_models(conn)], sort_keys=True, default=str).encode())
    h.update(pkg_version("scikit-learn").encode())
    return h.hexdigest()[:16]


def sources(sport: str, market: str) -> list[str]:
    members = (list(agreement.NAMES[sport] + agreement.SHADOW[sport]) if market == "winner"
               else list(LINE_MEMBERS[sport] + SHADOW_LINE_MEMBERS[sport]))
    return members + ["average", "stack"]


def expected(sport: str, market: str) -> tuple:
    return agreement.NAMES[sport] if market == "winner" else LINE_MEMBERS[sport]


def _key(*parts) -> str:
    return hashlib.sha256("\x1f".join(json.dumps(p, sort_keys=True, default=str) for p in parts).encode()).hexdigest()


def _vec(v) -> tuple:
    pa, pm = round(v[0], 9), round(v[1], 9)
    return pa, pm, round(1 - pa - pm, 9)


# --- Recording -------------------------------------------------------------------------------------------------------
def record(conn, *, game_ids: list, winner_run: Optional[dict], history: Optional[dict], now: Optional[str] = None,
           write: bool = True) -> dict:
    """Cohorts (winner + every other supported market) for `game_ids`, then selections under the decisions in force."""
    fixed = now is not None
    now = now or utc_now()
    eng = live.engine(conn)
    sport = eng.sport
    out = {"sport": sport, "status": "success", "cohorts_created": 0, "cohorts_existing": 0, "members_created": 0,
           "selections_created": 0, "skipped": {}, "errors": {}, "recommendations": []}
    if not game_ids:
        out["status"] = "skipped"
        return out
    try:
        defn = definition(conn)
    except Exception as exc:     # visible; never touches primary rows
        return {**out, "status": "failed", "error": f"definition unavailable: {type(exc).__name__}: {exc}"}
    out["definition"] = defn
    games = {g["game_id"]: g for g in conn.execute(
        f"SELECT * FROM games WHERE game_id IN ({','.join('?' * len(game_ids))})", list(game_ids))}
    cohorts = {gid: {} for gid in game_ids}          # game -> market -> (cohort_id, cohort dict)
    stamp = lambda: now if fixed else utc_now()

    def skip(market, gid, reason):
        out["skipped"].setdefault(market, []).append({"game_id": gid, "reason": reason})

    # Winner cohorts from the agreement step of this same run.
    winner_stack, reason = _winner_stack(conn, sport, history, games, now)
    winner_stack_x, reason_x = (_winner_stack(conn, sport, history, games, now, list(CORE[sport]) + list(agreement.SHADOW[sport]))
                                if agreement.SHADOW[sport] else (None, None))
    for gid in game_ids:
        fc = [f for f in (winner_run or {}).get("forecasts", []) if f["game_id"] == gid]
        if not fc:
            skip("winner", gid, "no research winner forecast in this run")
            continue
        members = [{"name": f["model"], "ref": {"table": "predictions", "id": f.get("prediction_id")},
                    "p": [f["home_win_prob"], 0.0, 1 - f["home_win_prob"]]} for f in fc]
        missing = {}
        for name in expected(sport, "winner") + agreement.SHADOW[sport]:
            if name in {m["name"] for m in members}:
                continue
            err = (winner_run or {}).get("errors", {}).get(name)
            missing[name] = f"error: {err}" if err else "skipped: stale or unavailable input, or not fitted"
        book = _winner_quote(conn, sport, gid, now)
        stack = None
        if winner_stack is not None and all(n in {m["name"] for m in members} for n in CORE[sport]):
            vecs = [next(m["p"] for m in members if m["name"] == n) for n in CORE[sport]]
            stack = {"p": list(combine.stack(winner_stack, vecs)), "weights": winner_stack["weights"],
                     "training_games": winner_stack["n"], "members": list(CORE[sport])}
        c = _cohort(sport, games[gid], "winner", None, None, None, book, defn, CORE[sport][0], now, stamp(), members, missing,
                    CORE[sport], stack, reason)
        _store(conn, c, cohorts, out, write)
        if agreement.SHADOW[sport]:
            names_x = list(CORE[sport]) + list(agreement.SHADOW[sport])
            stack_x = None
            if winner_stack_x is not None and all(n in {m["name"] for m in members} for n in names_x):
                vecs = [next(m["p"] for m in members if m["name"] == n) for n in names_x]
                stack_x = {"p": list(combine.stack(winner_stack_x, vecs)), "weights": winner_stack_x["weights"],
                           "training_games": winner_stack_x["n"], "members": names_x}
            cx = _cohort(sport, games[gid], "winner", None, None, None, book, expanded_definition(defn), CORE[sport][0], now,
                         stamp(), members, missing, names_x, stack_x, reason_x,
                         voters=list(expected(sport, "winner")) + list(agreement.SHADOW[sport]))
            _store_expanded(conn, cx, out, write)

    # Line and 3-way markets: the same line, line source and quote as the primary market forecast in this run.
    try:
        models = line_models(conn)
        current, stacks, reasons, data = _line_state(conn, eng, models, games, now, defn)
        known = data["known"]
        if write:
            from . import fit_register
            for name, ident in data["identities"].items():
                try:     # a record only; never blocks the cohorts
                    fit_register.record(conn, sport, name, defn, ident, now)
                except Exception as exc:
                    out.setdefault("fit_register_errors", {})[name] = f"{type(exc).__name__}: {exc}"
        out["line_state"] = data["source"]
    except Exception as exc:
        out["errors"]["line_members"] = f"{type(exc).__name__}: {exc}"
        models, current, stacks, reasons, data, known = [], {}, {}, {}, {}, {}
    if models:
        _, base = market_live.market_model(conn)
        primary_mv = market_live.model_version(base, "near_tipoff")
        for gid in game_ids:
            g = games[gid]
            for market in [m for m in sports.get(sport).markets if m != "winner"]:
                prim = conn.execute(
                    "SELECT * FROM market_forecasts WHERE game_id=? AND market=? AND policy='near_tipoff' AND run_type='live' "
                    "AND model_version=? AND tipoff_at=? AND generated_at>=? ORDER BY generated_at DESC, forecast_id DESC LIMIT 1",
                    (gid, market, primary_mv, g["tipoff_at"], _fmt(_parse(g["tipoff_at"]) - near_tipoff.WINDOW))).fetchone()
                if prim is None:
                    skip(market, gid, "no primary two-hour market forecast: no line to compare at")
                    continue
                line, source, quote = prim["line"], prim["line_source"], prim["market_quote_id"]
                members = [{"name": models[0].name, "ref": {"table": "market_forecasts", "id": prim["forecast_id"]},
                            "p": [prim["p_a"], prim["p_mid"], prim["p_b"]]}]
                missing = {}
                still = conn.execute("SELECT status, tipoff_at FROM games WHERE game_id=?", (gid,)).fetchone()
                if still is None or still["status"] != "scheduled" or still["tipoff_at"] != g["tipoff_at"]:
                    skip(market, gid, "game no longer scheduled at its forecast start: no member forecasts written")
                    continue
                for m in models[1:]:
                    d = current.get(m.name, {}).get(gid)
                    if isinstance(d, str):
                        missing[m.name] = d
                        continue
                    if d is None:    # the game started or left the schedule before this member's state was computed
                        missing[m.name] = "no member state: the game was no longer scheduled before the start"
                        continue
                    try:
                        vec = _vec(d.market(market, line))
                        mv = f"{PREFIX}:{m.name}:{defn}-window=2h-policy=near_tipoff"
                        generated = stamp()
                        if generated >= g["tipoff_at"]:
                            missing[m.name] = "calculation finished after the scheduled start"
                            continue
                        prev = conn.execute("SELECT line FROM market_forecasts WHERE game_id=? AND market=? AND model_version=? "
                                            "ORDER BY generated_at DESC, forecast_id DESC LIMIT 1", (gid, market, mv)).fetchone()
                        f = MarketForecast(sport, gid, market, line, source, quote, "near_tipoff", "live", mv,
                                           data["digest"], prim["feature_version"], g["tipoff_at"], generated,
                                           g["tipoff_at"], *vec, json.dumps(_params(d, data["fits"].get(m.name, {}).get(gid), known, now),
                                                      sort_keys=True),
                                           generated if prev is not None and prev["line"] != line else "")
                        fid = None
                        if write:
                            fid, new = record_market(conn, f)
                            out["members_created"] += new
                        members.append({"name": m.name, "ref": {"table": "market_forecasts", "id": fid}, "p": list(vec)})
                    except Exception as exc:    # incl. a ledger conflict: reported, never overwritten or substituted
                        missing[m.name] = f"error: {type(exc).__name__}: {exc}"
                        out["errors"].setdefault(m.name, f"{type(exc).__name__}: {exc}")
                book = None
                if quote is not None:
                    qr = conn.execute("SELECT * FROM market_quotes WHERE quote_id=?", (quote,)).fetchone()
                    book = {"p": [qr["p_a_devig"], qr["p_mid_devig"] or 0.0, qr["p_b_devig"]], "provider": qr["provider"],
                            "retrieved_at": qr["retrieved_at"], "quote_table": "market_quotes", "quote_id": quote}
                st = stacks.get(market)
                stack = None
                core = [m for m in members if m["name"] in LINE_MEMBERS[sport]]
                if st is not None and not any(n in LINE_MEMBERS[sport] for n in missing):
                    stack = {"p": list(combine.stack(st, [m["p"] for m in core])), "weights": st["weights"],
                             "training_games": st["n"], "members": [m["name"] for m in core]}
                c = _cohort(sport, g, market, line, source, quote, book, defn, models[0].name, now, stamp(), members, missing,
                            LINE_MEMBERS[sport], stack, reasons.get(market))
                _store(conn, c, cohorts, out, write)
                if SHADOW_LINE_MEMBERS[sport]:
                    names_x = list(LINE_MEMBERS[sport]) + list(SHADOW_LINE_MEMBERS[sport])
                    stx = data.get("stacks_x", {}).get(market)
                    stack_x = None
                    if stx is not None and not missing:
                        stack_x = {"p": list(combine.stack(stx, [m["p"] for m in members])), "weights": stx["weights"],
                                   "training_games": stx["n"], "members": [m["name"] for m in members]}
                    cx = _cohort(sport, g, market, line, source, quote, book, expanded_definition(defn), models[0].name, now,
                                 stamp(), members, missing, names_x, stack_x, data.get("reasons_x", {}).get(market),
                                 voters=names_x)
                    _store_expanded(conn, cx, out, write)
    if write:
        _select(conn, sport, defn, cohorts, out, stamp)
    if out["errors"] or any(v for v in out["skipped"].values()):
        out["status"] = "partial"
    return out


def _params(dist, info: Optional[dict], known, now: str) -> dict:
    """Distribution parameters, plus the fit identity of a cadence member: the fit in force and the results it used, up to the
    availability time of the last of them (`known`: game id -> result availability)."""
    if info is None:
        return dict(dist.params)
    through = known.get(info["last_result"]) if info.get("last_result") else None
    return {**dist.params, "fit": {**info, "results_available_through": through}}


def _winner_quote(conn, sport: str, game_id: str, at: str) -> Optional[dict]:
    """Latest current moneyline retrieved at or before `at`, with the table and id of the stored quote."""
    if sport == "nba":
        q = conn.execute("SELECT quote_id, home_prob_devig AS p, retrieved_at, provider FROM odds_quotes WHERE game_id=? "
                         "AND price_label='current' AND retrieved_at<=? ORDER BY retrieved_at DESC, quote_id DESC LIMIT 1",
                         (game_id, at)).fetchone()
        table = "odds_quotes"
    else:
        q = conn.execute("SELECT quote_id, p_a_devig AS p, retrieved_at, provider FROM market_quotes WHERE game_id=? "
                         "AND market='winner' AND price_label='current' AND retrieved_at<=? "
                         "ORDER BY retrieved_at DESC, quote_id DESC LIMIT 1", (game_id, at)).fetchone()
        table = "market_quotes"
    if q is None:
        return None
    return {"p": [q["p"], 0.0, 1 - q["p"]], "provider": q["provider"], "retrieved_at": q["retrieved_at"],
            "quote_table": table, "quote_id": q["quote_id"]}


def prior_best(sport: str, market: str) -> Optional[str]:
    """Strongest single member chosen on EARLIER data: lowest log loss in the recorded retrospective study (development
    seasons, all before any live game). Never chosen on the live results it is compared with. None if unavailable."""
    from .combination_research import compact
    m = compact(sport).get("markets", {}).get(market)
    return m["best_single_by_log_loss"] if m else None


def settlement_version() -> str:
    """Identity of the rules that turn a stored cohort into win / loss / push / void / unscorable: markets.settle, the
    research void rule and the pick outcome rule. Recorded with every decision and report."""
    import inspect
    src = inspect.getsource(markets.settle) + inspect.getsource(state) + inspect.getsource(selector.outcome)
    return "settle-" + hashlib.sha256(src.encode()).hexdigest()[:12]


def _cohort(sport, g, market, line, source, quote, book, defn, primary, info_at, generated, members, missing, exp, stack,
            stack_reason, voters=None) -> dict:
    vecs = {m["name"]: m["p"] for m in members}
    complete = all(n in vecs for n in exp)
    avg = list(combine.average([vecs[n] for n in exp])) if complete else None
    exp_all = list(voters if voters is not None else expected(sport, market))
    agree = combine.agreement({n: v for n, v in vecs.items() if n in exp_all}, exp_all, market)   # shadows never vote
    if stack is None:
        stack_note = "incomplete cohort" if not complete else (stack_reason or "no stacker")
    return {"sport": sport, "game_id": g["game_id"], "market": market, "line": line, "line_source": source,
            "market_quote_id": quote, "bookmaker": book, "definition": defn, "primary_name": primary,
            "information_at": info_at, "generated_at": generated, "tipoff_at": g["tipoff_at"], "members": members,
            "missing": missing, "average": avg,
            "stack": stack if stack is not None else {"p": None, "reason": stack_note}, "agreement": agree}


def _store(conn, c, cohorts, out, write):
    if c["generated_at"] >= c["tipoff_at"]:
        out["skipped"].setdefault(c["market"], []).append({"game_id": c["game_id"], "reason": "finished after the start"})
        return
    gone = {"game_id": c["game_id"], "reason": "game no longer scheduled at its forecast start when the cohort was written"}
    key = _key(c["sport"], c["game_id"], c["market"], c["line"], c["line_source"], c["market_quote_id"], c["definition"],
               c["primary_name"], [(m["name"], m["ref"], m["p"]) for m in c["members"]], c["missing"])
    if not write:
        cohorts[c["game_id"]][c["market"]] = (None, c)
        return
    try:
        with transaction(conn):
            row = conn.execute("SELECT cohort_id FROM research_cohorts WHERE idempotency_key=?", (key,)).fetchone()
            if row:
                out["cohorts_existing"] += 1
                cohorts[c["game_id"]][c["market"]] = (row[0], c)
                return
            # Checked in the same transaction as the insert: a game that started, was rescheduled or left the schedule
            # during this cycle gets no cohort and a skip with a reason, never an error that withholds other games.
            cur_g = conn.execute("SELECT status, tipoff_at FROM games WHERE game_id=?", (c["game_id"],)).fetchone()
            if cur_g is None or cur_g["status"] != "scheduled" or cur_g["tipoff_at"] != c["tipoff_at"]:
                out["skipped"].setdefault(c["market"], []).append(gone)
                return
            cur = conn.execute(
                "INSERT INTO research_cohorts (idempotency_key, sport, game_id, market, line, line_source, market_quote_id, bookmaker, "
                "definition, primary_name, information_at, generated_at, tipoff_at, members, missing, average, stack, agreement) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, c["sport"], c["game_id"], c["market"], c["line"], c["line_source"], c["market_quote_id"],
                 json.dumps(c["bookmaker"]), c["definition"], c["primary_name"], c["information_at"], c["generated_at"],
                 c["tipoff_at"], json.dumps(c["members"]), json.dumps(c["missing"]), json.dumps(c["average"]),
                 json.dumps(c["stack"]), json.dumps(c["agreement"])))
    except sqlite3.IntegrityError as exc:
        if "two hours before the current scheduled start" not in str(exc):
            raise
        out["skipped"].setdefault(c["market"], []).append(gone)       # refused by the trigger: same skip, never an abort
        return
    out["cohorts_created"] += 1
    cohorts[c["game_id"]][c["market"]] = (cur.lastrowid, c)


def _store_expanded(conn, c, out, write):
    """Expanded-experiment cohort: stored like a core cohort but counted separately and never passed to the selector."""
    sink = {"skipped": out["skipped"], "cohorts_created": 0, "cohorts_existing": 0}
    _store(conn, c, {c["game_id"]: {}}, sink, write)
    out["expanded_created"] = out.get("expanded_created", 0) + sink["cohorts_created"]
    out["expanded_existing"] = out.get("expanded_existing", 0) + sink["cohorts_existing"]


def _train_seasons(rows, games) -> list:
    current = min(g["season"] for g in games.values())
    return sorted({r.game.season for r in rows if r.game.season < current})[-STACK_SEASONS:]


def _winner_stack(conn, sport, history, games, now, names=None):
    """Stacker over the core winner members (or `names`), fitted on their walk-forward (out-of-fold) predictions of the last
    two completed seasons, as collected from this run's agreement step. None with a reason when not possible."""
    names = list(names or CORE[sport])
    if not history or any(n not in history for n in names):
        return None, "member walk-forward history unavailable"
    rows = live.available_rows(conn, now)
    seasons = _train_seasons(rows, games)
    real = {r.game.game_id: (0 if r.home_score > r.away_score else 2) for r in rows}
    by = {n: {p["game_id"]: p for p in history[n]} for n in names}
    train = []
    for gid, p in by[names[0]].items():
        if p["season"] in seasons and gid in real and all(gid in by[n] for n in names):
            train.append(([(by[n][gid]["p_home"], 0.0, 1 - by[n][gid]["p_home"]) for n in names], real[gid]))
    if len(train) < STACK_MIN_TRAIN:
        return None, f"only {len(train)} out-of-fold training games"
    return combine.fit_stack(train, "winner"), None


def _rebuild_defaultdict(kind, arg, items):
    from collections import defaultdict, deque
    from functools import partial
    factory = partial(deque, maxlen=arg) if kind == "deque" else partial(_constant, arg)
    return defaultdict(factory, items)


def _constant(value):
    return value


def _dumps(obj) -> bytes:
    """Pickle that also stores the models' defaultdicts, whose factories are lambdas returning a constant or a bounded deque;
    each is rebuilt with an equivalent picklable factory. Anything else unpicklable raises (the cache is then skipped)."""
    import io
    import pickle
    from collections import defaultdict, deque

    class P(pickle.Pickler):
        def reducer_override(self, o):
            if type(o) is defaultdict and o.default_factory is not None:
                sample = o.default_factory()
                if isinstance(sample, deque) and not sample:
                    return _rebuild_defaultdict, ("deque", sample.maxlen, dict(o))
                if isinstance(sample, (int, float)):
                    return _rebuild_defaultdict, ("const", sample, dict(o))
                raise pickle.PicklingError(f"unsupported defaultdict factory {o.default_factory!r}")
            return NotImplemented
    buf = io.BytesIO()
    P(buf, protocol=pickle.HIGHEST_PROTOCOL).dump(obj)
    return buf.getvalue()


def _cache_path(sport: str) -> Path:
    from . import config
    return config.data_dir() / "research" / f"line-state-{sport}.pkl"


def _line_state(conn, eng, models, games, now, defn):
    """Walk-forward of every line member over all results available now; current distributions for the eligible games and
    one stacker per market trained on the last two completed seasons at the primary's model-only line.

    The fitted state after the walk (before any scheduled game is touched) and the stackers depend only on the delivered
    results, box observations, training seasons and the research definition, so they are cached on disk under that key and
    reused by later cycles of the same evening. Returns (current, stacks, reasons, data) where data["digest"] identifies the
    inputs and is part of each member forecast's identity."""
    import pickle
    rows = live.available_rows(conn, now)
    known = {r.game.game_id: r for r in rows}
    events = [] if eng.sport == "nhl" else [e for e in backtest.load_box_events(conn, "live")
              if e.game.game_id in known and max(e.available_at, known[e.game.game_id].result_available_at) <= now]
    seasons = set(_train_seasons(rows, games))
    digest = hashlib.sha256(json.dumps([eng.dataset_version(rows), backtest.box_version(events), sorted(seasons), defn])
                            .encode()).hexdigest()[:24]
    path = _cache_path(eng.sport)
    state, source = None, "computed"
    try:
        if path.is_file():
            cached = pickle.loads(path.read_bytes())
            if cached.get("digest") == digest:
                state, source = cached, "cache"
    except Exception:
        state = None
    if state is None:
        state = _fit_line_state(models, rows, events, now, seasons, eng)
        state["digest"] = digest
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(_dumps(state))
            tmp.replace(path)
        except Exception:
            pass                         # caching is an optimisation only
    fitted = pickle.loads(state["models"]) if state.get("models") else {}
    horizon = max(g["tipoff_at"] for g in games.values())
    schedule = [g for g in conn.execute(
        f"SELECT * FROM games WHERE status='scheduled' AND tipoff_at > ? AND tipoff_at <= ? "
        f"AND season_type IN ({','.join('?' for _ in eng.eligible_types)}) ORDER BY tipoff_at, game_id",
        (now, horizon, *eng.eligible_types)) if g["game_id"] not in known]
    current, fits, idents = {}, {}, {}
    for m in models:
        if m.name in state["errors"]:
            current[m.name] = {gid: state["errors"][m.name] for gid in games}
            continue
        mm, cur = fitted[m.name], {}
        try:
            for g in schedule:              # every earlier scheduled game, in order, so rest features match the schedule
                d = mm.distribution(eng.pregame(g))
                if g["game_id"] in games:
                    cur[g["game_id"]] = d if d is not None else "not_ready: no out-of-sample fit yet"
                    if hasattr(mm, "fit_info"):
                        fits.setdefault(m.name, {})[g["game_id"]] = mm.fit_info()
        except Exception as exc:
            cur = {gid: f"error: {type(exc).__name__}: {exc}" for gid in games}
        current[m.name] = cur
        try:
            from . import fit_register
            idents[m.name] = fit_register.identity(mm, rows, now)
        except Exception:
            idents[m.name] = None          # identity is a record, never a reason to withhold a forecast
    return current, state["stacks"], state["reasons"], {"digest": digest, "source": source, "known": {g: r.result_available_at for g, r in known.items()},
                                                   "fits": fits, "identities": idents,
                                                   "stacks_x": state.get("stacks_x", {}), "reasons_x": state.get("reasons_x", {})}


def _fit_line_state(models, rows, events, now, seasons, eng) -> dict:
    hist, errors, fitted = {}, {}, {}
    for m in models:
        try:
            out = backtest.run(m, rows, events, deliver_until=now,
                               extra=lambda mm, g: {"dist": mm.distribution(g)} if g.season in seasons else {})
            hist[m.name] = {p["game_id"]: p.get("dist") for p in out}
            fitted[m.name] = m
        except Exception as exc:
            errors[m.name] = f"error: {type(exc).__name__}: {exc}"
    line_markets = [mk for mk in sports.get(eng.sport).markets if mk != "winner"]
    core = [m.name for m in models if m.name not in SHADOW_LINE_MEMBERS[eng.sport]]   # core stackers: core members only
    stacks, reasons = _line_stacks(core, hist, rows, seasons, line_markets, errors)
    out = {"models": _dumps(fitted), "errors": errors, "stacks": stacks, "reasons": reasons}
    if SHADOW_LINE_MEMBERS[eng.sport]:          # expanded experiment: every member, in model order
        out["stacks_x"], out["reasons_x"] = _line_stacks([m.name for m in models], hist, rows, seasons, line_markets, errors)
    return out


def _line_stacks(names, hist, rows, seasons, line_markets, errors):
    stacks, reasons = {}, {}
    if errors:
        reasons = {mk: "member walk-forward failed" for mk in line_markets}
    else:
        for market in line_markets:
            train = []
            for r in rows:
                if r.game.season not in seasons:
                    continue
                ds = [hist[n].get(r.game.game_id) for n in names]
                if any(d is None for d in ds):
                    continue
                line = None if market == "reg3" else markets.model_line(ds[0], market)
                s = markets.settle(market, line, status="final", home=r.home_score, away=r.away_score,
                                   decision=getattr(r, "decision", None))
                if s not in SIDE:
                    continue
                train.append(([d.market(market, line) for d in ds], SIDE[s]))
            if len(train) < STACK_MIN_TRAIN:
                reasons[market] = f"only {len(train)} out-of-fold training games"
                continue
            stacks[market] = combine.fit_stack(train, market)
    return stacks, reasons


# --- Decisions -------------------------------------------------------------------------------------------------------
def scheduled_time(now: str) -> str:
    t = _parse(now)
    monday = (t - timedelta(days=t.weekday())).replace(hour=selector.DECISION_HOUR_UTC, minute=0, second=0, microsecond=0)
    if monday > t:
        monday -= timedelta(days=7)
    return _fmt(monday)


def _realised(market, line, g, decision):
    s = markets.settle(market, line, status=g["status"], home=g["home_score"], away=g["away_score"], decision=decision)
    return SIDE.get(s)


def canonical_cohorts(conn, market: Optional[str] = None, definition_: Optional[str] = None, as_of: str = "9999") -> list:
    """One cohort per game, market and definition: the latest made before the start it was made for, as known at `as_of`."""
    sql = ("SELECT c.*, g.status, g.home_score, g.away_score, g.result_available_at, g.tipoff_at AS game_tipoff_at, "
           "d.decision FROM research_cohorts c JOIN games g ON g.game_id=c.game_id LEFT JOIN game_decisions d ON d.game_id=c.game_id "
           "WHERE c.cohort_id = (SELECT x.cohort_id FROM research_cohorts x WHERE x.game_id=c.game_id AND x.market=c.market "
           "AND x.definition=c.definition AND x.generated_at < x.tipoff_at AND x.generated_at <= ? "
           "ORDER BY x.generated_at DESC, x.cohort_id DESC LIMIT 1)")
    params = [as_of]
    if market:
        sql += " AND c.market=?"
        params.append(market)
    if definition_:
        sql += " AND c.definition=?"
        params.append(definition_)
    return conn.execute(sql + " ORDER BY c.tipoff_at, c.game_id", params).fetchall()


def state(row, as_of: str) -> str:
    """'settled', 'pending' or 'void' as known at `as_of`. Any change of the start time voids a cohort (the same rule as
    markets.settle: the two-hour forecast was made for a different start)."""
    if row["status"] in ("postponed", "canceled") or row["game_tipoff_at"] != row["tipoff_at"]:
        return "void"
    if row["status"] != "final" or not row["result_available_at"] or row["result_available_at"] > as_of:
        return "pending"
    return "settled"


def item(row, realised=None) -> dict:
    members = json.loads(row["members"])
    vectors = {m["name"]: tuple(m["p"]) for m in members}
    avg, stack = json.loads(row["average"]), json.loads(row["stack"])
    if avg:
        vectors["average"] = tuple(avg)
    if stack and stack.get("p"):
        vectors["stack"] = tuple(stack["p"])
    a = json.loads(row["agreement"])
    book = json.loads(row["bookmaker"])
    return {"game_id": row["game_id"], "week": selector.week_of(row["tipoff_at"]), "tipoff_at": row["tipoff_at"],
            "vectors": vectors, "agreement": {"majority_side": a["side"] if a["majority"] else None,
                                              "unanimous_side": a["side"] if a["unanimous"] else None},
            "bookmaker": tuple(book["p"]) if book else None, "realised": realised}


def evidence(conn, market: str, defn: str, cutoff: str) -> list:
    out = []
    for r in canonical_cohorts(conn, market, defn, as_of=cutoff):
        if r["tipoff_at"] >= cutoff or state(r, cutoff) != "settled":
            continue
        y = _realised(market, r["line"], r, r["decision"])
        if y is not None:
            out.append(item(r, y))
    return out


def decide_due(conn, *, now: Optional[str] = None, write: bool = True, defn: Optional[str] = None) -> dict:
    """Writes this week's decision for every market of the store's sport once its scheduled time has passed. Idempotent:
    a decision that exists is never recomputed; a missed week is not written retroactively."""
    fixed = now is not None
    now = now or utc_now()
    sport = live.engine(conn).sport
    sched = scheduled_time(now)
    until = _fmt(_parse(sched) + timedelta(days=7))
    defn = defn or definition(conn)
    version = selector.version()
    out = {"scheduled_for": sched, "selector_version": version, "definition": defn, "written": [], "existing": []}
    for market in sports.get(sport).markets:
        if conn.execute("SELECT 1 FROM selector_decisions WHERE sport=? AND market=? AND selector_version=? AND definition=? "
                        "AND scheduled_for=?", (sport, market, version, defn, sched)).fetchone():
            out["existing"].append(market)
            continue
        prev = conn.execute("SELECT rule FROM selector_decisions WHERE sport=? AND market=? AND selector_version=? AND definition=? "
                            "AND scheduled_for<? ORDER BY scheduled_for DESC LIMIT 1", (sport, market, version, defn, sched)).fetchone()
        incumbent = json.loads(prev["rule"]) if prev else None
        items = evidence(conn, market, defn, sched)
        d = selector.decide(items, market, sources(sport, market), incumbent)
        decided = now if fixed else utc_now()
        summary = {k: d[k] for k in ("settled_items", "rules", "z", "min_picks", "min_coverage", "min_weeks", "eligible_rules")}
        top = sorted([s for s in d["stats"] if s["lower"] is not None], key=lambda s: -s["lower"])[:10]
        ev = {**summary, "evidence_until": sched, "sources": sources(sport, market), "top_rules": top,
              "settlement_version": settlement_version(),
              "weeks": sorted({i["week"] for i in items})}
        if write:
            conn.execute(
                "INSERT INTO selector_decisions (idempotency_key, sport, market, selector_version, definition, scheduled_for, "
                "decided_at, effective_until, status, rule, lower_bound, reason, evidence) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_key(sport, market, version, defn, sched), sport, market, version, defn, sched, decided, until, d["status"],
                 json.dumps(d["rule"]), d.get("lower"), d["reason"], selector.dumps(ev)))
            conn.commit()
        out["written"].append({"market": market, "status": d["status"], "reason": d["reason"]})
    return out


CARRY_OVER = "+7 days"


def decision_in_force(conn, sport: str, market: str, defn: str, at: str):
    """The latest decision written at or before `at`. Between a scheduled Monday time and the write of the new decision the
    previous one stays in force (never the new one retroactively); a decision is carried at most one week past its interval,
    after which, with no newer decision written, there is none."""
    return conn.execute("SELECT * FROM selector_decisions WHERE sport=? AND market=? AND selector_version=? AND definition=? "
                        "AND decided_at<=? AND ? < strftime('%Y-%m-%dT%H:%M:%SZ', effective_until, ?) "
                        "ORDER BY decided_at DESC, decision_id DESC LIMIT 1",
                        (sport, market, selector.version(), defn, at, at, CARRY_OVER)).fetchone()


def _select(conn, sport, defn, cohorts, out, stamp):
    for gid, per_market in cohorts.items():
        if not per_market:
            continue
        generated = stamp()
        drows = {market: decision_in_force(conn, sport, market, defn, generated) for market in per_market}
        # One context = the cohorts AND decisions compared in this run, so a run with any change writes a complete new set.
        context = _key(sorted((m, cid, None if drows[m] is None else drows[m]["decision_id"])
                              for m, (cid, _) in per_market.items()))
        candidates, rows = {}, {}
        for market, (cid, c) in per_market.items():
            drow = drows[market]
            dec = None if drow is None else {"status": drow["status"], "rule": json.loads(drow["rule"]),
                                             "lower": drow["lower_bound"], "reason": drow["reason"]}
            it = item({"members": json.dumps(c["members"]), "average": json.dumps(c["average"]), "stack": json.dumps(c["stack"]),
                       "agreement": json.dumps(c["agreement"]), "bookmaker": json.dumps(c["bookmaker"]),
                       "game_id": gid, "tipoff_at": c["tipoff_at"]})
            s = selector.apply(dec, it, market)
            candidates[market] = (s, dec or {"lower": None})
            rows[market] = (cid, c, drow, s)
        chosen, reasons = selector.choose_market(candidates)
        if chosen:
            out["recommendations"].append({"game_id": gid, "market": chosen, "side": candidates[chosen][0]["side"]})
        for market, (cid, c, drow, s) in rows.items():
            if generated >= c["tipoff_at"]:
                continue
            key = _key(cid, None if drow is None else drow["decision_id"], context)
            try:
                with transaction(conn):
                    if conn.execute("SELECT 1 FROM research_selections WHERE idempotency_key=?", (key,)).fetchone():
                        continue
                    conn.execute(
                        "INSERT INTO research_selections (idempotency_key, cohort_id, decision_id, sport, game_id, market, generated_at, "
                        "tipoff_at, context, status, side, probability, threshold, reason, recommended, game_reason) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (key, cid, None if drow is None else drow["decision_id"], sport, gid, market, generated, c["tipoff_at"],
                         context, s["status"], None if s["status"] != "pick" else s["side"], s["probability"],
                         None if drow is None or drow["status"] != "active" else json.loads(drow["rule"])["threshold"],
                         s["reason"], int(market == chosen), reasons[market]))
            except sqlite3.IntegrityError as exc:     # refused by the guard (e.g. the start changed mid-cycle): a skip
                out["skipped"].setdefault(market, []).append({"game_id": gid, "reason": f"selection refused: {exc}"})
                continue
            out["selections_created"] += 1


# --- Reading (API) -----------------------------------------------------------------------------------------------------
def _minutes(tipoff: str, at: str) -> float:
    return round((_parse(tipoff) - _parse(at)).total_seconds() / 60, 1)


def _context_selections(conn, game_id: str, as_of: str) -> dict:
    """market -> selection row of the game's latest selection run (context) made at or before `as_of`."""
    rows = conn.execute(
        "SELECT s.*, d.selector_version, d.scheduled_for, d.decided_at, d.effective_until, d.status AS decision_status, "
        "d.rule, d.lower_bound, d.reason AS decision_reason FROM research_selections s "
        "LEFT JOIN selector_decisions d ON d.decision_id=s.decision_id WHERE s.game_id=? AND s.generated_at<=? "
        "AND s.context = (SELECT x.context FROM research_selections x WHERE x.game_id=s.game_id AND x.generated_at<=? "
        "ORDER BY x.generated_at DESC, x.selection_id DESC LIMIT 1)", (game_id, as_of, as_of)).fetchall()
    return {r["market"]: r for r in rows}


def game_detail(conn, game_id: str, as_of: Optional[str] = None) -> dict:
    """Latest research cohort and selection per market for one game, as known at `as_of`. Research only."""
    as_of = as_of or utc_now()
    g = conn.execute("SELECT * FROM games WHERE game_id=?", (game_id,)).fetchone()
    if g is None:
        return {"game_id": game_id, "markets": [], "status": "unknown_game"}
    abbr = {t["team_id"]: t["abbr"] for t in conn.execute("SELECT team_id, abbr FROM teams WHERE team_id IN (?, ?)",
                                                            (g["home_team_id"], g["away_team_id"]))}
    sels = _context_selections(conn, game_id, as_of)
    out = []
    for r in conn.execute(
            "SELECT c.* FROM research_cohorts c WHERE c.game_id=? AND c.generated_at<=? AND c.cohort_id = (SELECT x.cohort_id "
            "FROM research_cohorts x WHERE x.game_id=c.game_id AND x.market=c.market AND x.generated_at<=? "
            "AND x.definition NOT LIKE ? ORDER BY x.generated_at DESC, x.cohort_id DESC LIMIT 1) ORDER BY c.market",
            (game_id, as_of, as_of, f"%:{EXPANDED}")):
        s = sels.get(r["market"])
        if s is not None and s["cohort_id"] != r["cohort_id"]:
            s = None          # a newer cohort was recorded but not (yet) evaluated by the selector
        stack = json.loads(r["stack"])
        book = json.loads(r["bookmaker"])
        selection = None
        if s is not None:
            selection = {"status": s["status"], "side": s["side"], "probability": s["probability"], "threshold": s["threshold"],
                         "reason": s["reason"], "recommended": bool(s["recommended"]), "game_reason": s["game_reason"],
                         "generated_at": s["generated_at"],
                         "decision": None if s["decision_id"] is None else {
                             "status": s["decision_status"], "rule": json.loads(s["rule"]), "lower_bound": s["lower_bound"],
                             "reason": s["decision_reason"], "selector_version": s["selector_version"],
                             "evidence_until": s["scheduled_for"], "effective_from": s["decided_at"],
                             "effective_until": s["effective_until"]}}
        labels = markets.side_labels(r["market"], r["line"], abbr[g["home_team_id"]], abbr[g["away_team_id"]])
        out.append({"market": r["market"], "line": r["line"], "line_source": r["line_source"],
                    "line_label": "bookmaker line" if r["line_source"] == "market" else
                    "model-only line (no bookmaker line retrieved)" if r["line_source"] == "model" else None,
                    "bookmaker": book, "sides": {"a": labels[0], "mid": labels[1] or None, "b": labels[2]},
                    "primary": r["primary_name"], "members": json.loads(r["members"]), "missing": json.loads(r["missing"]),
                    "average": json.loads(r["average"]), "stack": stack.get("p"),
                    "stack_note": stack.get("reason") if not stack.get("p") else f"trained on {stack['training_games']} out-of-fold games",
                    "agreement": json.loads(r["agreement"]), "definition": r["definition"], "generated_at": r["generated_at"],
                    "information_at": r["information_at"], "tipoff_at": r["tipoff_at"],
                    "minutes_before_start": _minutes(r["tipoff_at"], r["generated_at"]),
                    "start_changed": g["tipoff_at"] != r["tipoff_at"], "selection": selection})
    rec = next((m for m in out if m["selection"] and m["selection"]["recommended"]), None)
    expanded = [{"market": r["market"], "line": r["line"], "members": json.loads(r["members"]), "missing": json.loads(r["missing"]),
                 "average": json.loads(r["average"]), "stack": json.loads(r["stack"]).get("p"),
                 "agreement": json.loads(r["agreement"]), "definition": r["definition"], "generated_at": r["generated_at"],
                 "minutes_before_start": _minutes(r["tipoff_at"], r["generated_at"])}
                for r in conn.execute(
                    "SELECT c.* FROM research_cohorts c WHERE c.game_id=? AND c.generated_at<=? AND c.cohort_id = (SELECT x.cohort_id "
                    "FROM research_cohorts x WHERE x.game_id=c.game_id AND x.market=c.market AND x.generated_at<=? AND x.definition LIKE ? "
                    "ORDER BY x.generated_at DESC, x.cohort_id DESC LIMIT 1) ORDER BY c.market",
                    (game_id, as_of, as_of, f"%:{EXPANDED}"))]
    return {"game_id": game_id, "kind": "research", "markets": out, "expanded": expanded,
            "expanded_note": f"{EXPANDED}: separate recording-only experiment with every member incl. shadows; never used for "
                             "selections or recommendations.",
            "recommendation": None if rec is None else {"market": rec["market"], "side": rec["selection"]["side"],
                                                         "label": rec["sides"][rec["selection"]["side"]], "line": rec["line"],
                                                         "probability": rec["selection"]["probability"]},
            "status": "no_research_forecast" if not out else "recorded",
            "note": "Research/shadow recommendations. The primary forecast above is unchanged. No betting advice."}


def slate(conn, game_ids: list, as_of: Optional[str] = None) -> dict:
    """Per game: the current research recommendation, 'no_pick', or 'none' when no research cohort exists; plus, per market,
    its own research status with the exact line, missing-input reasons and timing."""
    out = {}
    for gid in game_ids:
        d = game_detail(conn, gid, as_of)
        per = {}
        for m in d["markets"]:
            sel = m["selection"]
            per[m["market"]] = {
                "status": "not_evaluated" if sel is None else sel["status"],
                "pick": m["sides"][sel["side"]] if sel and sel["side"] else None,
                "probability": sel["probability"] if sel else None, "threshold": sel["threshold"] if sel else None,
                "recommended": bool(sel and sel["recommended"]),
                "reason": "recorded but not yet evaluated by the selector" if sel is None else sel["reason"],
                "line": m["line"], "line_source": m["line_source"], "line_label": m["line_label"],
                "missing": m["missing"], "generated_at": m["generated_at"],
                "minutes_before_start": m["minutes_before_start"], "start_changed": m["start_changed"]}
        if not d["markets"]:
            out[gid] = {"status": "none", "markets": {}}
        elif d["recommendation"]:
            out[gid] = {"status": "pick", **d["recommendation"], "markets": per}
        else:
            out[gid] = {"status": "no_pick", "reasons": {k: v["reason"] for k, v in per.items()}, "markets": per}
    return out


def _record(results: list) -> dict:
    w, l, p = results.count("win"), results.count("loss"), results.count("push")
    from .nhl_eval import _wilson
    return {"wins": w, "losses": l, "pushes": p, "decided": w + l, "accuracy": w / (w + l) if w + l else None,
            "ci95": _wilson(w, w + l)}


def report(conn, market: str, as_of: Optional[str] = None) -> dict:
    """Prospective research record for one market: every source, thresholds, agreement filters, the selector and its
    game-level recommendations, by definition. Descriptive; retries never add games (one canonical cohort per game)."""
    as_of = as_of or utc_now()
    sport = live.engine(conn).sport
    rows = canonical_cohorts(conn, market, as_of=as_of)
    by_def: dict = {}
    for r in rows:
        by_def.setdefault(r["definition"], []).append(r)
    groups = []
    for defn, rs in by_def.items():
        settled, pending, void, unscorable = [], 0, 0, 0
        for r in rs:
            st = state(r, as_of)
            if st == "void":
                void += 1
            elif st == "pending":
                pending += 1
            else:
                y = _realised(market, r["line"], r, r["decision"])
                if y is None:          # final, but data needed to settle is missing (e.g. NHL decision type): never guessed
                    unscorable += 1
                else:
                    settled.append(item(r, y) | {"line_source": r["line_source"], "tipoff_at": r["tipoff_at"]})
        srcs = sources(sport, market)
        if is_expanded(defn):     # the registered expanded subsets (docs/MODEL_SELECTION.md §14) as probability sources
            from .expanded_search import nba_subsets, nhl_subsets
            members_x = list(expected(sport, market)) + list(shadows(sport, market))
            subsets = (nhl_subsets(members_x) if sport == "nhl" else nba_subsets(members_x))[0] if shadows(sport, market) else []
            for i in settled:
                for sub in subsets:
                    if all(n in i["vectors"] for n in sub):
                        i["vectors"]["avg:" + "+".join(sub)] = list(combine.average([i["vectors"][n] for n in sub]))
            srcs = srcs + ["avg:" + "+".join(sub) for sub in subsets]
        per_source = {}
        for s in srcs:
            have = [i for i in settled if s in i["vectors"]]
            res = [selector.outcome(market, side, i["realised"]) for i in have
                   if (side := combine.pick(i["vectors"][s], market)) is not None]
            vecs, real = [i["vectors"][s] for i in have], [i["realised"] for i in have]
            from . import metrics
            from .combination_research import _summary as _calib
            per_source[s] = {"cohorts": len(have), "coverage": len(have) / len(settled) if settled else None, **_record(res),
                             "ties": len(have) - len(res),
                             "calibration": _calib(market, have, s)["calibration"] if have else [],
                             "log_loss": metrics.log_loss_multi(vecs, real), "brier": metrics.brier_multi(vecs, real),
                             "thresholds": [{"threshold": t, **_record([selector.outcome(market, side, i["realised"]) for i in have
                                                                        if (side := combine.pick(i["vectors"][s], market)) is not None
                                                                        and i["vectors"][s][side] + 1e-9 >= t])}
                                            for t in selector.thresholds(market)]}
        primary = srcs[0]
        best = prior_best(sport, market)

        def top_record(src, count):
            have = [i for i in settled if src in i["vectors"]]
            top = sorted(have, key=lambda i: -max(i["vectors"][src][k] for k in combine.sides(market)))[:count]
            return _record([selector.outcome(market, combine.pick(i["vectors"][src], market), i["realised"])
                            for i in top if combine.pick(i["vectors"][src], market) is not None])
        agree = {}
        for kind in ("majority", "unanimous"):
            chosen = [i for i in settled if i["agreement"][f"{kind}_side"]]
            res = [selector.outcome(market, SIDE[i["agreement"][f"{kind}_side"]], i["realised"]) for i in chosen]
            agree[kind] = {**_record(res), "cohorts": len(chosen), "coverage": len(chosen) / len(settled) if settled else None,
                           "primary_confidence_same_count": top_record(primary, len(chosen)),
                           "average_confidence_same_count": top_record("average", len(chosen)),
                           "best_single": best, "best_single_basis": "lowest log loss in the retrospective study (earlier seasons)",
                           "best_single_confidence_same_count": top_record(best, len(chosen)) if best else None}
        weeks: dict = {}
        for i in settled:
            w = weeks.setdefault(i["week"], {"week": i["week"], "cohorts": 0, "primary": [], "average": []})
            w["cohorts"] += 1
            for s in ("primary", "average"):
                key = primary if s == "primary" else "average"
                if key in i["vectors"] and (side := combine.pick(i["vectors"][key], market)) is not None:
                    w[s].append(selector.outcome(market, side, i["realised"]))
        periods = [{"week": w["week"], "cohorts": w["cohorts"], "primary": _record(w["primary"]), "average": _record(w["average"])}
                   for w in sorted(weeks.values(), key=lambda x: x["week"])]
        book = [i for i in settled if i["bookmaker"] and primary in i["vectors"]]
        from . import metrics
        bookmaker = {"cohorts": len(book),
                     "primary_log_loss": metrics.log_loss_multi([i["vectors"][primary] for i in book], [i["realised"] for i in book]),
                     "bookmaker_log_loss": metrics.log_loss_multi([i["bookmaker"] for i in book], [i["realised"] for i in book]),
                     "note": "Same games, same exact line, quote retrieved before the forecast. De-vigged prices; no profit claim."}
        groups.append({"definition": defn, "cohorts": len(rs), "settled": len(settled), "pending": pending, "void": void,
                       "unscorable": unscorable,
                       "line_sources": {k: sum(r["line_source"] == k for r in rs) for k in ("market", "model")},
                       "sources": per_source, "agreement": agree, "periods": periods, "bookmaker": bookmaker,
                       "experiment": EXPANDED if is_expanded(defn) else "core", "recording_only": is_expanded(defn),
                       "selector": None if is_expanded(defn) else _selector_record(conn, market, defn, as_of, primary)})
    # Decisions written before settlement versions were recorded keep None ("unknown"); nothing is backfilled.
    decisions = [dict(d) | {"rule": json.loads(d["rule"]), "evidence": json.loads(d["evidence"]),
                            "settlement_version": json.loads(d["evidence"]).get("settlement_version")} for d in conn.execute(
        "SELECT * FROM selector_decisions WHERE market=? AND decided_at<=? ORDER BY scheduled_for DESC, decision_id DESC LIMIT 12",
        (market, as_of))]
    return {"sport": sport, "market": market, "as_of": as_of, "kind": "prospective research", "groups": groups,
            "decisions": decisions, "selector_version": selector.version(), "settlement_version": settlement_version(),
            "note": "Two-hour forward research only, separate from primary records and from the retrospective study. One canonical "
                    "cohort per game and definition. Intervals are Wilson 95%, not adjusted for game dependence or for "
                    "looking at many rows; the selector's own rule uses a Bonferroni-adjusted bound."}


def _selector_record(conn, market, defn, as_of, primary):
    """Canonical selection per game (latest before the start) and the game-level recommendations."""
    rows = conn.execute(
        "SELECT s.*, c.line, c.tipoff_at AS c_tipoff, c.members, g.status AS g_status, g.home_score, g.away_score, "
        "g.result_available_at, g.tipoff_at AS game_tipoff_at, d.decision FROM research_selections s "
        "JOIN research_cohorts c ON c.cohort_id=s.cohort_id JOIN games g ON g.game_id=s.game_id "
        "LEFT JOIN game_decisions d ON d.game_id=s.game_id WHERE s.market=? AND c.definition=? AND s.generated_at<=? "
        "AND s.selection_id=(SELECT x.selection_id FROM research_selections x JOIN research_cohorts cx ON cx.cohort_id=x.cohort_id "
        "WHERE x.game_id=s.game_id AND x.market=s.market AND cx.definition=? AND x.generated_at<=? "
        "AND x.generated_at < x.tipoff_at ORDER BY x.generated_at DESC, x.selection_id DESC LIMIT 1)",
        (market, defn, as_of, defn, as_of)).fetchall()
    picks, rec, statuses = [], [], {"pick": 0, "no_pick": 0}
    pending = void = 0
    for r in rows:
        statuses[r["status"]] += 1
        if r["status"] != "pick":
            continue
        fake = {"status": r["g_status"], "game_tipoff_at": r["game_tipoff_at"], "tipoff_at": r["tipoff_at"],
                "result_available_at": r["result_available_at"]}
        st = state(fake, as_of)
        if st != "settled":
            pending += st == "pending"
            void += st == "void"
            continue
        y = _realised(market, r["line"], {"status": r["g_status"], "home_score": r["home_score"], "away_score": r["away_score"]},
                      r["decision"])
        res = selector.outcome(market, SIDE[r["side"]], y) if y is not None else None
        if res:
            picks.append(res)
            if r["recommended"]:
                rec.append(res)
    return {"selections": statuses, "picks": _record(picks), "pending": pending, "void": void,
            "recommended_for_game": _record(rec)}


def history(conn, as_of: Optional[str] = None, limit: int = 200) -> dict:
    """Game-level research recommendations: per game, the selections of its latest run before the start. Includes games
    where the selector recommended nothing, so the record is never only the successful picks."""
    as_of = as_of or utc_now()
    latest = conn.execute(
        "SELECT s.*, c.line, c.line_source, g.status AS g_status, g.home_score, g.away_score, g.result_available_at, "
        "g.tipoff_at AS game_tipoff_at, d.decision, h.abbr AS home, a.abbr AS away, sd.rule, sd.selector_version "
        "FROM research_selections s JOIN research_cohorts c ON c.cohort_id=s.cohort_id JOIN games g ON g.game_id=s.game_id "
        "JOIN teams h ON h.team_id=g.home_team_id JOIN teams a ON a.team_id=g.away_team_id "
        "LEFT JOIN game_decisions d ON d.game_id=s.game_id LEFT JOIN selector_decisions sd ON sd.decision_id=s.decision_id "
        "WHERE s.generated_at<=? AND s.generated_at < s.tipoff_at AND s.context = (SELECT x.context FROM research_selections x "
        "WHERE x.game_id=s.game_id AND x.generated_at<=? AND x.generated_at < x.tipoff_at "
        "ORDER BY x.generated_at DESC, x.selection_id DESC LIMIT 1) ORDER BY s.tipoff_at DESC, s.game_id", (as_of, as_of)).fetchall()
    games: dict = {}
    for r in latest:
        games.setdefault(r["game_id"], []).append(r)
    rows, no_pick, results = [], 0, []
    for gid, rs in games.items():
        rec = next((r for r in rs if r["recommended"]), None)
        if rec is None:
            no_pick += 1
            continue
        st = state({"status": rec["g_status"], "game_tipoff_at": rec["game_tipoff_at"], "tipoff_at": rec["tipoff_at"],
                    "result_available_at": rec["result_available_at"]}, as_of)
        result = st
        if st == "settled":
            y = _realised(rec["market"], rec["line"], {"status": rec["g_status"], "home_score": rec["home_score"],
                                                       "away_score": rec["away_score"]}, rec["decision"])
            result = "unscorable" if y is None else selector.outcome(rec["market"], SIDE[rec["side"]], y)
        results.append(result)
        labels = markets.side_labels(rec["market"], rec["line"], rec["home"], rec["away"])
        rows.append({"game_id": gid, "tipoff_at": rec["tipoff_at"], "home": rec["home"], "away": rec["away"],
                     "market": rec["market"], "line": rec["line"], "line_source": rec["line_source"],
                     "pick": labels[SIDE[rec["side"]]] or rec["side"], "probability": rec["probability"],
                     "threshold": rec["threshold"], "rule": json.loads(rec["rule"]) if rec["rule"] else None,
                     "selector_version": rec["selector_version"], "generated_at": rec["generated_at"],
                     "minutes_before_start": _minutes(rec["tipoff_at"], rec["generated_at"]), "result": result})
    return {"kind": "research", "settlement_version": settlement_version(),
            "games_with_selection_run": len(games), "games_without_pick": no_pick,
            "record": {**_record([r for r in results if r in ("win", "loss", "push")]),
                       "pending": results.count("pending"), "void": results.count("void"), "unscorable": results.count("unscorable")},
            "rows": rows[:limit], "total": len(rows)}
