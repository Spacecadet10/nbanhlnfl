"""Adaptive prediction system `adaptive-v1` for both sports (docs/ADAPTIVE_SYSTEM.md).

Separately versioned research layer. Within-season refits of every learned member (core walk members and registry
candidates, the neural networks included) on a registered weekly schedule, a learned combination refitted on the same
schedule from out-of-sample predictions (historical walk-forward rows plus settled prospective forecasts), optional
contemporaneous odds, and an append-only forecast ledger. Nothing here changes a primary forecast, a research cohort, a
candidate forecast or a selector decision, and no file hashed into an existing identity is edited: this module wraps
`retrain_cadence.Cadenced`, `combine.fit_stack`, the candidate engine and the selector, as `cadence_daily` wraps the
cadence code.

Training (`train`) runs off the forecast path (nightly job or CLI) and saves verified artifacts atomically; the two-hour
cycle (`record`) only loads them, delivers results that arrived after each artifact's cutoff to the frozen state, and
predicts. A failed refit keeps the previous valid artifact in force and is logged in `adaptive_fits`.
"""
import copy
import hashlib
import json
import math
import pickle
from dataclasses import replace
from datetime import timedelta
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Callable, Optional

from . import agreement, backtest, combination_research, combine, config, ingest, live, markets, near_tipoff, player_ratings, nhl_player_ratings, research_live, selector, sports
from . import retrain_cadence as rc
from .candidates import deep, engine, features, registry
from .candidates import live as pool_live
from .db import transaction, utc_now
from .models.efficiency import EfficiencyMargin

VERSION = "adaptive-v1"
PERIOD = "weekly"
MIN_DEEP_VALIDATION = 100            # current-season results before a deep network refits within the season
WINDOW_SEASONS = research_live.STACK_SEASONS      # completed seasons in the combiner's training window (+ the current one)
MIN_TRAIN = combination_research.STACK_MIN_TRAIN  # common out-of-sample rows a combination needs
MIN_COVERAGE_SHARE = 0.5             # a member may not cut the common training set below this share of the best-covered one
L2_GRID = (combine.STACK_L2, 10.0, 100.0, 1000.0)
VALIDATION_SHARE = 0.25              # newest share of the training rows (time ordered) used to choose the L2 penalty
SCHEDULE = {"period": "ISO week of the game's Eastern date",
            "member": "first forecast of a new period, only when eligible results were delivered since the member's last fit",
            "deep": f"as member, and within a season only once {MIN_DEEP_VALIDATION} current-season results exist",
            "combiner": "first forecast of a new period, only when newly settled results exist; L2 chosen on the newest "
                        f"{VALIDATION_SHARE:.0%} of the training rows from {list(L2_GRID)}",
            "live_trigger": "jobs.daily (nightly) when the next eligible game's period differs from the fit in force and new "
                            "eligible results exist"}
ONLINE = {"nba": ("elo", "points_normal", "efficiency_margin"), "nhl": ("hockey_elo", "goals_poisson")}
LEARNED = {"nba": ("logistic", "logistic_v2", "gbt_v1", "ridge_points", "gbt_points"),
           "nhl": ("hockey_logistic", "hockey_gbt", "hockey_logistic_xg", "goals_draw", "goals_glm")}
FORECAST_IN_STATE = (EfficiencyMargin,)   # online members whose forecast is part of their state (the margin estimate
                                          # kept for its residual); pinned against every online member by a test
BOX_DEPENDENT = {"nba": ("logistic_v2", "efficiency_margin", "gbt_v1", player_ratings.ADAPTIVE_MEMBER, player_ratings.SCORE_MEMBER), "nhl": ("hockey_logistic_xg",)}
EXCLUDED_FEATURES = {"nhl_goalie": "historical rows use the actual starting goalie (known only after the start): no "
                                   "point-in-time out-of-sample history to learn a weight from"}
SIDE = research_live.SIDE
FMT = "%Y-%m-%dT%H:%M:%SZ"


def enabled() -> bool:
    return research_live.enabled()


def _parse(ts):
    return research_live._parse(ts)


def _fmt(dt):
    return research_live._fmt(dt)


def _key(*parts) -> str:
    return hashlib.sha256("\x1f".join(json.dumps(p, sort_keys=True, default=str) for p in parts).encode()).hexdigest()


_CADENCE = ContextVar("adaptive_cadence", default=PERIOD)


@contextmanager
def cadence(value: str):
    if value not in ("weekly", "daily"):
        raise ValueError("cadence must be weekly or daily")
    token = _CADENCE.set(value)
    try:
        yield
    finally:
        _CADENCE.reset(token)


def period_of(tipoff_at: str, season: int) -> str:
    if _CADENCE.get() == "daily":
        return f"{season}:{_parse(tipoff_at).astimezone(backtest.EASTERN).date().isoformat()}"
    y, w = rc.period_key(PERIOD, tipoff_at)
    return f"{season}:{y}-W{w:02d}"


def definition(conn) -> str:
    """adaptive-v1 identity: this code, the research definition (members, combination code), the agreement digest and
    every candidate definition. Any change gives a new identity; earlier adaptive rows keep theirs."""
    sport = live.engine(conn).sport
    h = hashlib.sha256(VERSION.encode())
    h.update(research_live.definition(conn).encode())
    h.update(agreement.definition_hash(agreement.models(conn)).encode())
    for spec in registry.for_sport(sport):
        h.update(registry.definition(spec).encode())
    h.update(Path(__file__).read_bytes())
    if sport == "nba":      # the player-rating/injury member's code (availability.py is already in the agreement digest)
        h.update(Path(__file__).with_name("player_ratings.py").read_bytes())
    if sport == "nhl":
        h.update(Path(__file__).with_name("nhl_player_ratings.py").read_bytes())
        h.update(Path(__file__).with_name("sources").joinpath("nhl_injuries.py").read_bytes())
    if _CADENCE.get() != PERIOD:
        h.update(_CADENCE.get().encode())
        h.update(Path(__file__).with_name("daily_agreement.py").read_bytes())
        h.update(Path(__file__).with_name("combination_thresholds.py").read_bytes())
    return h.hexdigest()[:16]


def forecast_cutoff(tipoff_at: str) -> str:
    """Historical adaptive forecasts share one information time, two hours before the game."""
    return _fmt(_parse(tipoff_at) - timedelta(hours=2))


def available_rows(conn, now):
    return [replace(r, cutoff_at=forecast_cutoff(r.game.tipoff_at),
                    game=replace(r.game, cutoff_at=forecast_cutoff(r.game.tipoff_at)))
            for r in live.available_rows(conn, now)]


def model_version(defn: str, member: str = "combiner") -> str:
    return f"{VERSION}:{member}:{defn}"


# --- Members ---------------------------------------------------------------------------------------------------------
class Adaptive(rc.Cadenced):
    """A learned member refitted at the first forecast of each ISO week, only when new results were delivered since its
    last fit (cadence_daily.Daily's rule). `frozen` stops every refit (the forecast path); the member's own season-start
    refit inside predict() is then prevented by the caller's season guard."""

    def __init__(self, member):
        super().__init__(member, PERIOD)
        self.cadence = _CADENCE.get()
        self.name = f"{member.name}@adaptive"
        self.fitted_on: Optional[int] = None
        self.frozen = False
        self.skipped_no_new_results = 0

    def _maybe_refit(self, g) -> None:
        if self.frozen:
            return
        if self.first_season is None:
            self.first_season = g.season
        if getattr(self.member, "season", None) is not None and g.season > self.member.season:
            self._log("season_start", g)
            self.fitted_on = len(self.delivered)
        if g.season == self.first_season:
            return
        key = ((g.season, _parse(g.tipoff_at).astimezone(backtest.EASTERN).date().isoformat())
               if self.cadence == "daily" else (g.season, *rc.period_key(PERIOD, g.tipoff_at)))
        if key == self.key:
            return
        self.key = key
        if getattr(self.member, "season", None) != g.season:
            return
        if self.fitted_on == len(self.delivered):
            self.skipped_no_new_results += 1
            return
        before = {a: getattr(self.member, a, None) for a in rc._FIRST_ATTRS}
        self.member._refit(g.season + 1)
        for a, v in before.items():
            if v is None and getattr(self.member, a, None) is not None:
                setattr(self.member, a, g.season)
        self.fitted_on = len(self.delivered)
        self.refits.append({"cutoff_at": g.cutoff_at or forecast_cutoff(g.tipoff_at), "season": g.season,
                            "training_games": len(getattr(self.member, "examples", []))})
        self._log(self.cadence, g)

    def _log(self, kind, g):
        super()._log(kind, g)
        self.last_fit["trigger_cutoff"] = g.cutoff_at or forecast_cutoff(g.tipoff_at)

    def config(self) -> dict:
        return {**self.member.config(), "cadence": f"adaptive-{self.cadence}"}


class Member:
    """One contributor: a walk model (online or Adaptive-wrapped) or a registry candidate."""

    def __init__(self, name, kind, obj=None, spec=None, markets_=None, learned=False, line_primary=False):
        self.name, self.kind, self.obj, self.spec, self.learned = name, kind, obj, spec, learned
        self.markets, self.line_primary = tuple(markets_), line_primary


LINE_PRIMARY = {"nba": "points_normal", "nhl": "goals_poisson@market"}   # the member whose model line the history uses


def members(conn) -> tuple[list, dict]:
    """(members, excluded with reasons) for this store's sport. Walk members come from the agreement and line-model
    builders (fresh instances); learned ones are wrapped as Adaptive; candidates from the registry."""
    eng = live.engine(conn)
    sport = eng.sport
    all_markets = sports.get(sport).markets
    lines = tuple(m for m in all_markets if m != "winner")
    out, excluded = [], {}
    built = agreement.models(conn)
    for m in built:
        if m.name in LEARNED[sport]:
            out.append(Member(f"{m.name}@adaptive", "walk", Adaptive(m), markets_=("winner",), learned=True))
            excluded[m.name] = "season-start version: superseded by its adaptive (within-season refit) version"
        elif m.name in ONLINE[sport]:
            out.append(Member(m.name, "walk", m, markets_=("winner",)))
        elif m.name == "availability_two_hour":
            excluded[m.name] = "forward-only inputs (injury listings): no out-of-sample history to learn a weight from"
        else:
            excluded[m.name] = "not registered as an adaptive member (adaptive.LEARNED / ONLINE)"
    if sport == "nba":
        # logistic_v2 + injury report + player ratings (player_ratings.PlayersInjuriesV1), every input gated by the row's
        # cutoff. It needs the availability research cache of this data directory; without one it is excluded, visibly.
        # NHL has a separate input pipeline; never apply NBA per-48 player ratings to hockey.
        reason = player_ratings.adaptive_inputs_missing()
        if reason:
            excluded[player_ratings.ADAPTIVE_MEMBER] = reason
            excluded[player_ratings.SCORE_MEMBER] = reason
        else:
            inner = player_ratings.adaptive_member(built[0].cfg, player_ratings.team_names(conn))
            out.append(Member(f"{inner.name}@adaptive", "walk", Adaptive(inner), markets_=("winner",), learned=True))
            scores = player_ratings.PlayersInjuriesPoints(built[0].cfg, player_ratings.team_names(conn))
            out.append(Member(f"{scores.name}@adaptive", "walk", Adaptive(scores), markets_=all_markets, learned=True))
    if sport == "nhl":
        reason = nhl_player_ratings.missing_inputs()
        if reason:
            excluded[nhl_player_ratings.MEMBER] = reason
        else:
            inner = nhl_player_ratings.NhlPlayersInjuries(built[0].cfg, sports.get(sport).rating_key)
            out.append(Member(f"{inner.name}@adaptive", "walk", Adaptive(inner), markets_=all_markets, learned=True))
    names = {m.name for m in out}
    for i, m in enumerate(research_live.line_models(conn)):
        if isinstance(m, rc.Cadenced):
            excluded[m.name] = "existing weekly shadow: superseded by the adaptive version of the same model"
            continue
        if m.name in LEARNED[sport]:
            out.append(Member(f"{m.name}@adaptive", "walk", Adaptive(m), markets_=lines, learned=True))
            excluded.setdefault(m.name, "season-start version: superseded by its adaptive (within-season refit) version")
        elif i and hasattr(m, "_refit"):
            excluded[m.name] = "not registered as an adaptive member (adaptive.LEARNED / ONLINE)"
        elif i == 0:      # the sealed market model: line markets at its model line; the NHL one differs from the plain
            clash = m.name in names                                     # winner member of the same name (empty net)
            out.append(Member(f"{m.name}@market" if clash else m.name, "walk", m, markets_=lines if clash else all_markets,
                              line_primary=True))
        else:
            out.append(Member(m.name, "walk", m, markets_=all_markets))
    for spec in registry.for_sport(sport):
        if spec.features in EXCLUDED_FEATURES:
            excluded[f"cand:{spec.name}"] = EXCLUDED_FEATURES[spec.features]
        else:       # a missing fitting library keeps an existing fit or skips the member visibly (train), never excludes it here
            out.append(Member(f"cand:{spec.name}@adaptive", "candidate", spec=spec, markets_=spec.markets, learned=True))
    return out, excluded


def _vec(v) -> tuple:
    values = tuple(float(p) for p in v)
    if (len(values) != 3 or any(not math.isfinite(p) or not 0 <= p <= 1 for p in values)
            or not math.isclose(sum(values), 1., rel_tol=0., abs_tol=1e-6)):
        raise ValueError("invalid probability vector: expected three finite probabilities summing to one")
    total = sum(values)
    pa = round(values[0] / total, 9)
    pm = min(round(values[1] / total, 9), round(1 - pa, 9))
    return pa, pm, round(1 - pa - pm, 9)


def _dist_vectors(d, p, market_list, lines: dict, invalid: Optional[list] = None) -> dict:
    """market -> vector of a walk member's forecast (winner from its probability, line markets from its distribution).
    With `invalid` (the training walk), an invalid vector is left out for that market and listed instead of failing the
    member's whole history; without it (serving), it raises as before."""
    out = {}
    for mk in market_list:
        try:
            if mk == "winner":
                if p is not None:
                    out[mk] = _vec((p, 0.0, 1 - p))
            elif d is not None and (mk == "reg3" or mk in lines):
                out[mk] = _vec(d.market(mk, None if mk == "reg3" else lines[mk]))
        except ValueError as exc:
            if invalid is None:
                raise
            invalid.append((mk, str(exc)))
    return out


def walk_member(member: Member, rows, events, now: Optional[str], window: set, lines: dict, line_primary: bool = False,
                invalid: Optional[list] = None) -> dict:
    """Walk-forward of one walk member over `rows` (results delivered until `now`). Returns per game in `window` the
    member's vectors at the given lines (or, for the line primary, the model lines it sets)."""
    m = member.obj
    has_dist = hasattr(getattr(m, "member", m), "distribution")

    def extra(model, g):
        if g.season not in window:
            return {}
        d = model.distribution(g) if has_dist else None
        if line_primary and d is not None:
            lines[g.game_id] = {mk: markets.model_line(d, mk) for mk in member.markets if mk not in ("winner", "reg3")}
        return {"dist": d}
    out = backtest.run(m, rows, events, deliver_until=now, extra=extra)
    hist = {}
    for p in out:
        if p["season"] in window:
            bad: list = []
            hist[p["game_id"]] = ({} if has_dist and p.get("dist") is None else
                                  _dist_vectors(p.get("dist"), p["p_home"], member.markets, lines.get(p["game_id"], {}),
                                                bad if invalid is not None else None))
            if invalid is not None:
                invalid += [(p["game_id"], mk, err) for mk, err in bad]
    return hist


# --- Candidate scheduled walk -----------------------------------------------------------------------------------------
def _fingerprint(rows) -> str:
    return pool_live._results_fingerprint(rows)


def _sd(residuals: list, cutoff: str) -> Optional[tuple]:
    res = [r for r in residuals if r[0] <= cutoff]
    if len(res) < engine.MIN_RESIDUALS:
        return None
    import math
    from .candidates import learners
    return (max(learners.MIN_SD, math.sqrt(sum(r[1] ** 2 for r in res) / len(res))),
            max(learners.MIN_SD, math.sqrt(sum(r[2] ** 2 for r in res) / len(res))))


def fit_dependency(spec) -> str:
    if deep.is_deep(spec):
        lib = deep.LIBRARY_PACKAGE[deep.FAMILIES[spec.family]]
        return f"{lib}=={deep.PINNED[lib]}"
    return f"optional dependency {spec.extra}"


def can_fit(spec) -> bool:
    return deep.libraries_present(spec) if deep.is_deep(spec) else registry.available(spec)


def live_fit(spec, store: dict, labelled: list, period: str, season: int, cutoff: str) -> tuple[dict, dict]:
    """The fit prepared for the NEXT period from every labelled row available now, on a COPY of the history store: the
    checkpointed history keeps each period's own fit (a resumed walk must reproduce the uninterrupted one), the live
    artifact carries the prepared one. Returns (fit record, prepared store)."""
    prepared = dict(store)
    rec = _period_fit(spec, prepared, labelled, period, season, cutoff)
    return rec, prepared


def _period_fit(spec, store: dict, train: list, key: str, season: int, cutoff: str) -> dict:
    """Refit decision for one period: a new fit when new labelled results exist since the stored fit (deep: and the
    current season has enough results); otherwise the stored fit stays. Returns the fit record."""
    rec = {"period": key, "cutoff": cutoff, "training_games": len(train), "refit": False}
    if len(train) < registry.MIN_TRAIN[spec.sport]:
        rec["reason"] = f"too few labelled results ({len(train)} < {registry.MIN_TRAIN[spec.sport]})"
        return rec
    fp = _fingerprint(train)
    if store.get("model") is not None and fp == store.get("fitted_fp"):
        rec["reason"] = "no new results since the last fit"
        return rec
    if not can_fit(spec):
        if store.get("model") is None:
            raise deep.DependencyMissing(f"{spec.name}: {fit_dependency(spec)} is not installed here and no fit exists yet")
        rec["reason"] = f"kept: {fit_dependency(spec)} is not installed here (the stored fit is applied; refit where it is)"
        rec["kept_for_dependency"] = True
        return rec
    in_season = sum(1 for r in train if r.season == season)
    if _CADENCE.get() != "daily" and deep.is_deep(spec) and store.get("model") is not None and 0 < in_season < MIN_DEEP_VALIDATION:
        rec["reason"] = f"deep network keeps its fit until {MIN_DEEP_VALIDATION} current-season results ({in_season} so far)"
        return rec
    if deep.is_deep(spec) and len({r.season for r in train}) < 2:
        rec["reason"] = "no earlier season to choose the number of epochs"      # deep.fit raises on this; kept explicit
        return rec
    model = deep.fit(spec, train) if deep.is_deep(spec) else engine.fit(spec, train)
    if model is None:
        rec["reason"] = "no earlier season to choose the number of epochs"
        return rec
    store.update({"model": model, "fitted_fp": fp, "fit_cutoff": cutoff, "fit_period": key, "fit_games": len(train),
                  "results_through": max(r.result_available_at for r in train), "fitted_at": utc_now(),
                  "info": {k: v for k, v in model.info.items() if k != "training_rows"}})
    rec.update({"refit": True, "results_fingerprint": fp, "results_through": store["results_through"]})
    return rec


def scheduled_walk(spec, rows: list, store: dict, checkpoint: Optional[Callable] = None, log=None,
                   from_season: Optional[int] = None) -> dict:
    """Extends `store` (the out-of-sample history of one candidate) over every period of `rows` not yet covered.
    Each period's games are predicted with the fit in force at the period's first cutoff; the fit uses only rows whose
    result was available at that cutoff. Deterministic, so an interrupted run resumes from the last checkpoint.
    Periods before `from_season` are passed over without a fit (their predictions are not needed; a fit still sees
    every earlier labelled row)."""
    rows = sorted(rows, key=lambda r: (r.cutoff_at, r.tipoff_at, r.game_id))
    store.setdefault("outputs", {})
    store.setdefault("residuals", [])
    store.setdefault("fits", [])
    store.setdefault("periods", [])
    store.setdefault("seen", set())
    done = set(store["periods"])
    periods: dict = {}
    for r in rows:
        periods.setdefault(period_of(r.tipoff_at, r.season), []).append(r)
    labelled = [r for r in rows if r.y is not None and r.result_available_at is not None]
    n = 0

    def predict(games, cutoff):
        sd = _sd(store["residuals"], cutoff) if spec.head == "normal" else None
        for r, o in zip(games, engine.outputs(spec, store["model"], games, sd)):
            if spec.head == "normal" and r.y is not None:
                store["residuals"].append((r.result_available_at, r.y["margin"] - o["m"], r.y["total"] - o["t"]))
            if engine.ready(o):
                store["outputs"][r.game_id] = o
            store["seen"].add(r.game_id)
    for key, games in periods.items():
        if key in done:
            # Only the newest covered period can still grow (its later games arrive after a checkpoint); its fit in
            # force is the stored model. A game that turns up late in an older period is left unpredicted.
            late = [r for r in games if r.game_id not in store["seen"]]
            if late and key == store["periods"][-1] and store.get("model") is not None:
                predict(late, games[0].cutoff_at)
            continue
        if from_season is not None and games[0].season < from_season:
            store["periods"].append(key)
            store["seen"].update(r.game_id for r in games)
            continue
        cutoff = games[0].cutoff_at
        train = [r for r in labelled if r.result_available_at <= cutoff]
        rec = _period_fit(spec, store, train, key, games[0].season, cutoff)
        store["fits"].append(rec)
        if store.get("model") is not None:
            predict(games, cutoff)
        else:
            store["seen"].update(r.game_id for r in games)
        store["periods"].append(key)
        n += 1
        if checkpoint and n % 20 == 0:
            checkpoint()
    if log:
        log(f"{spec.name}: {len(store['outputs'])} out-of-sample games, {sum(f['refit'] for f in store['fits'])} fits")
    return store


def candidate_vectors(spec, outputs: dict, market_list, lines: dict) -> dict:
    """game -> market -> vector at the given lines, from a candidate's stored outputs."""
    hist = {}
    for gid, o in outputs.items():
        if not pool_live.usable_goals(o):          # degenerate goal rates: no vector, as at serving
            continue
        v = {}
        for mk in market_list:
            if mk in ("winner", "reg3"):
                vec = engine.vector(o, mk, None)
            elif mk in lines.get(gid, {}):
                vec = engine.vector(o, mk, lines[gid][mk])
            else:
                vec = None
            if vec is not None:
                v[mk] = _vec(vec)
        hist[gid] = v
    return hist


# --- Combiner --------------------------------------------------------------------------------------------------------
def _loss(stack, rows, names) -> float:
    return sum(combine.log_loss(combine.stack(stack, [r["vectors"][m] for m in names]), r["realised"]) for r in rows) / len(rows)


def fit_combiner(rows: list, candidates: list, market: str, cutoff: str, fallbacks: bool = True, groups=()) -> dict:
    """The combination for one market from out-of-sample rows ({vectors, realised, cutoff_at, result_available_at}).
    Only rows whose outcome was available at `cutoff` are used. Members are admitted greedily by coverage while the
    common training set stays large; the L2 penalty is chosen on the newest VALIDATION_SHARE of the rows; weights are
    unconstrained (a member may get a weight near or below zero). Leave-one-out fallbacks cover one missing member;
    `groups` (member names that abstain together, e.g. the members guarded by one shared input cache) each get one more
    fallback trained without the whole group, when at least two of its members are in the combination."""
    cls = combine._classes(market)
    usable = [r for r in rows if r["result_available_at"] <= cutoff and r["realised"] in cls]
    late = [r for r in rows if r["result_available_at"] > cutoff]
    out = {"market": market, "cutoff": cutoff, "rows_available": len(usable), "rows_after_cutoff": len(late),
           "excluded": {}, "members": [], "stack": None, "fallbacks": {}, "group_fallbacks": []}
    coverage = {m: sum(1 for r in usable if m in r["vectors"]) for m in candidates}
    order = sorted(candidates, key=lambda m: (-coverage[m], candidates.index(m)))
    best = coverage[order[0]] if order else 0
    need = max(MIN_TRAIN, int(MIN_COVERAGE_SHARE * best))
    chosen, common = [], usable
    for m in order:
        c = [r for r in common if m in r["vectors"]]
        if len(c) >= need:
            chosen.append(m)
            common = c
        else:
            out["excluded"][m] = f"would cut the common out-of-sample training set to {len(c)} rows (< {need}); covers {coverage[m]}"
    if len(chosen) < 2 or len(common) < MIN_TRAIN:
        out["reason"] = f"only {len(chosen)} members with {len(common)} common out-of-sample rows (need 2 and {MIN_TRAIN})"
        return out
    common.sort(key=lambda r: (r["cutoff_at"], r["game_id"]))
    split = max(1, int(len(common) * (1 - VALIDATION_SHARE)))
    early, late_rows = common[:split], common[split:]
    choice = {}
    for l2 in L2_GRID:
        model = combine.fit_stack([([r["vectors"][m] for m in chosen], r["realised"]) for r in early], market, l2)
        choice[l2] = _loss(model, late_rows, chosen) if late_rows else None
    l2 = min(L2_GRID, key=lambda v: (choice[v] if choice[v] is not None else 0.0, -v))
    model = combine.fit_stack([([r["vectors"][m] for m in chosen], r["realised"]) for r in common], market, l2)
    out.update({"members": chosen, "stack": model, "l2": l2, "l2_validation": {str(k): v for k, v in choice.items()},
                "training": {"games": model["n"], "prospective": sum(1 for r in common if r.get("source") == "prospective"),
                             "walk_forward": sum(1 for r in common if r.get("source") != "prospective"),
                             "seasons": sorted({r["season"] for r in common}), "results_through": max(r["result_available_at"] for r in common),
                             "first_cutoff": common[0]["cutoff_at"], "last_cutoff": common[-1]["cutoff_at"]},
                "weights": dict(zip(chosen, model["weights"]))})
    for m in chosen if fallbacks else []:
        others = [x for x in chosen if x != m]
        if len(others) >= 2:
            out["fallbacks"][m] = {"members": others,
                                   "stack": combine.fit_stack([([r["vectors"][x] for x in others], r["realised"]) for r in common], market, l2)}
    for group in groups if fallbacks else []:
        gone = [x for x in chosen if x in group]          # one member of the group alone is covered by its leave-one-out stack
        others = [x for x in chosen if x not in group]
        if len(gone) >= 2 and len(others) >= 2:
            out["group_fallbacks"].append({"missing": gone, "members": others,
                                           "stack": combine.fit_stack([([r["vectors"][x] for x in others], r["realised"]) for r in common], market, l2)})
    return out


def combine_now(cm: Optional[dict], have: dict) -> tuple:
    """(vector, stack used, members used, reason) for one game from the combiner of its market and the member vectors
    available. One missing member uses the registered leave-one-out stack; exactly a registered group missing together
    uses that group's stack; any other missing subset abstains. Never substitutes."""
    if cm is None or cm.get("stack") is None:
        return None, None, [], "no combination trained for this market" + (f": {cm['reason']}" if cm and cm.get("reason") else "")
    missing = [m for m in cm["members"] if m not in have]
    if not missing:
        names, stack = cm["members"], cm["stack"]
    elif len(missing) == 1 and missing[0] in cm["fallbacks"]:
        names, stack = cm["fallbacks"][missing[0]]["members"], cm["fallbacks"][missing[0]]["stack"]
    else:
        group = next((f for f in cm.get("group_fallbacks", ()) if set(f["missing"]) == set(missing)), None)
        if group is None:
            return None, None, [], f"missing members {', '.join(missing)}: no combination trained for this subset"
        names, stack = group["members"], group["stack"]
    return _vec(combine.stack(stack, [have[m] for m in names])), stack, list(names), None


# --- Artifacts -------------------------------------------------------------------------------------------------------
def _rebuild(kind, arg, items):
    from collections import defaultdict, deque
    from functools import partial
    factory = {"deque": partial(deque, maxlen=arg), "const": partial(research_live._constant, arg),
               "list": list, "dict": dict, "set": set}[kind]
    return defaultdict(factory, items)


def _dumps(obj) -> bytes:
    """As research_live._dumps, also covering defaultdicts of lists / dicts / sets (the xG inputs)."""
    import io
    from collections import defaultdict, deque

    class P(pickle.Pickler):
        def reducer_override(self, o):
            if type(o) is defaultdict and o.default_factory is not None:
                sample = o.default_factory()
                if isinstance(sample, deque) and not sample:
                    return _rebuild, ("deque", sample.maxlen, dict(o))
                if isinstance(sample, (int, float)):
                    return _rebuild, ("const", sample, dict(o))
                for kind, t in (("list", list), ("dict", dict), ("set", set)):
                    if type(sample) is t and not sample:
                        return _rebuild, (kind, None, dict(o))
                raise pickle.PicklingError(f"unsupported defaultdict factory {o.default_factory!r}")
            return NotImplemented
    buf = io.BytesIO()
    P(buf, protocol=pickle.HIGHEST_PROTOCOL).dump(obj)
    return buf.getvalue()


def _defines(obj, name: str) -> bool:
    """Whether the object's own classes (not `object`, which has a default __getstate__ since Python 3.11) define `name`."""
    return any(name in vars(c) for c in type(obj).__mro__ if c is not object)


def _state(obj) -> dict:
    """The instance state that enters an artifact: the member's own __getstate__ when it defines one (that is how a
    member keeps its external inputs, e.g. the shared player cache, out of the artifact), else its instance dictionary.
    A member that defines __getstate__ must return a dict; we never drop back to vars(obj) for it, or the external
    cache that __getstate__ deliberately excluded would slip back into the artifact."""
    if _defines(obj, "__getstate__"):
        state = obj.__getstate__()
        if not isinstance(state, dict):
            raise TypeError(f"{type(obj).__name__}.__getstate__ must return a dict for artifact state, "
                            f"got {type(state).__name__}")
        return dict(state)
    return dict(vars(obj))


def freeze(w) -> bytes:
    """The state of a walk member (wrapper and inner model) as instance dictionaries: some NHL members are classes
    defined inside builder functions, so the objects themselves cannot be pickled, their state can."""
    inner = getattr(w, "member", None)
    if inner is None:
        return _dumps({"wrapped": False, "inner": _state(w)})
    return _dumps({"wrapped": True, "inner": _state(inner), "wrapper": {k: v for k, v in vars(w).items() if k != "member"}})


def thaw(fresh, blob: bytes):
    """Loads frozen state into a freshly built member of the same kind (from `members(conn)`), through the member's own
    __setstate__ when it defines one (the counterpart of `_state`)."""
    state = pickle.loads(blob)
    inner = getattr(fresh, "member", fresh)
    if _defines(inner, "__setstate__"):
        inner.__setstate__(state["inner"])
    else:
        inner.__dict__.update(state["inner"])
    if state["wrapped"]:
        fresh.__dict__.update(state["wrapper"])
    return fresh


def root(sport: str) -> Path:
    return config.data_dir() / "research" / "adaptive" / sport


def _write_bytes(path: Path, data: bytes) -> None:
    """Data then its digest, each through a unique temporary file and an atomic rename (as candidates.live._write)."""
    import os
    import time
    tag = f".{os.getpid()}.{time.monotonic_ns()}.tmp"
    tmp = path.with_name(path.name + tag)
    tmp.write_bytes(data)
    tmp.replace(path)
    side = pool_live._sidecar(path)
    stmp = side.with_name(side.name + tag)
    stmp.write_text(hashlib.sha256(data).hexdigest() + "\n")
    stmp.replace(side)


def _save(base: Path, obj: dict) -> tuple[Path, str]:
    """Immutable, content-addressed artifact: the file name carries the digest of its bytes, so a retry or a forced refit
    of the same period never overwrites an earlier artifact (an identical fit maps to the same file). Returns (path, sha)."""
    data = _dumps(obj)
    sha = hashlib.sha256(data).hexdigest()
    path = base.with_name(f"{base.stem}-{sha[:12]}.pkl")
    path.parent.mkdir(parents=True, exist_ok=True)
    with pool_live._locked(path):
        if not (path.exists() and pool_live._sidecar(path).exists()
                and hashlib.sha256(path.read_bytes()).hexdigest() == sha):
            _write_bytes(path, data)
    return path, sha


class ArtifactMismatch(RuntimeError):
    """The file at a fit row's path does not have the digest that row recorded: never loaded, never substituted."""


def _load(path: Path, expected_sha: Optional[str] = None) -> dict:
    """Verifies the bytes against the digest recorded on the fit row (and the sidecar) BEFORE unpickling."""
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    if expected_sha is not None and sha != expected_sha:
        raise ArtifactMismatch(f"artifact {path.name}: digest {sha[:12]} differs from the fit row's {expected_sha[:12]}")
    if sha != pool_live._sidecar(path).read_text().strip():
        raise pool_live.ArtifactIntegrityError(f"artifact {path.name}: digest mismatch")
    return pickle.loads(data)


def load_fit(row) -> dict:
    return _load(Path(row["artifact_path"]), row["artifact_sha256"])


def _history_path(sport: str, defn: str) -> Path:
    return root(sport) / f"history-{defn}.pkl"


def save_history(sport: str, defn: str, history: dict) -> None:
    """The candidate history checkpoint (each period's own fit and outputs): a mutable, digest-checked checkpoint that no
    fit row refers to; the prepared live fits are separate immutable artifacts."""
    p = _history_path(sport, defn)
    p.parent.mkdir(parents=True, exist_ok=True)
    with pool_live._locked(p):
        _write_bytes(p, _dumps(history))


def load_history(sport: str, defn: str) -> dict:
    p = _history_path(sport, defn)
    if p.exists() and pool_live._sidecar(p).exists():
        try:
            return _load(p)
        except Exception:
            return {"candidates": {}}
    return {"candidates": {}}


# --- Store reads -----------------------------------------------------------------------------------------------------
def _has(conn, table: str) -> bool:
    return pool_live._has(conn, table)


IN_FORCE = ("success", "partial")     # fit rows whose artifact may be used (a partial combiner has usable markets)


FIT_ORDER = {     # which fit is "latest at `at`": published by then (live), or trained only on results through then (replay)
    "finished": "finished_at<=? ORDER BY finished_at DESC, fit_id DESC",
    "training": "training_cutoff IS NOT NULL AND training_cutoff<=? ORDER BY training_cutoff DESC, finished_at DESC, fit_id DESC",
}


def latest_fit(conn, sport: str, kind: str, name: str, defn: str, at: str = "9999", status=None, basis: str = "finished"):
    """The latest fit row of a member or the combiner at `at`: by default any usable one (success or partial).
    basis='training' (retrospective replay only): the latest fit whose training results end at or before `at`."""
    if not _has(conn, "adaptive_fits"):
        return None
    statuses = IN_FORCE if status is None else (status,)
    return conn.execute(f"SELECT * FROM adaptive_fits WHERE sport=? AND kind=? AND name=? AND definition=? AND status IN "
                        f"({','.join('?' * len(statuses))}) AND {FIT_ORDER[basis]} LIMIT 1",
                        (sport, kind, name, defn, *statuses, at)).fetchone()


def _insert_fit(conn, sport, kind, name, defn, period, status, started, detail: dict, *, cutoff=None, games=None, fp=None,
                path=None, sha=None, model_fit_id=None) -> int:
    key = _key(sport, kind, name, defn, period, fp, sha)
    with transaction(conn):
        row = conn.execute(f"SELECT fit_id FROM adaptive_fits WHERE idempotency_key=? AND status IN ({','.join('?' * len(IN_FORCE))})",
                           (key, *IN_FORCE)).fetchone()
        if row and status in IN_FORCE:          # the same usable fit again (a retry): one row
            return row[0]
        return conn.execute(
            "INSERT INTO adaptive_fits (sport, kind, name, definition, period, schedule, status, training_cutoff, training_games, "
            "results_fingerprint, artifact_path, artifact_sha256, model_fit_id, detail, started_at, finished_at, idempotency_key) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sport, kind, name, defn, period, _CADENCE.get(), status, cutoff, games, fp, path, sha, model_fit_id,
             json.dumps(detail, sort_keys=True, default=str), started, utc_now(), key)).lastrowid


def next_game(conn, now: str):
    eng = live.engine(conn)
    types = eng.eligible_types
    return conn.execute(f"SELECT * FROM games WHERE status='scheduled' AND tipoff_at > ? AND season_type IN "
                        f"({','.join('?' * len(types))}) ORDER BY tipoff_at, game_id LIMIT 1", (now, *types)).fetchone()


def due(conn, now: Optional[str] = None) -> dict:
    """Whether a training run is due: the next eligible game's period differs from the combiner fit in force, or new
    eligible results exist since its training cutoff. Read-only."""
    now = now or utc_now()
    sport = live.engine(conn).sport
    try:
        defn = definition(conn)
    except Exception as exc:
        return {"due": False, "reason": f"definition unavailable: {type(exc).__name__}: {exc}"}
    nxt = next_game(conn, now)
    if nxt is None:
        return {"due": False, "reason": "no upcoming eligible game", "definition": defn}
    period = period_of(now if _CADENCE.get() == "daily" else nxt["tipoff_at"], nxt["season"])
    fit = latest_fit(conn, sport, "combiner", "combiner", defn, now)
    if fit is None:
        return {"due": True, "reason": "no combiner fit for this definition yet", "period": period, "definition": defn}
    if fit["period"] == period:
        if fit["status"] == "partial":        # a member failed or a market has no usable combination: retried every run
            return {"due": True, "reason": f"previous run for {period} was partial: {json.loads(fit['detail']).get('partial_reason')}",
                    "period": period, "definition": defn}
        refittable = kept_for_dependency_here(conn, sport, defn, period, now)
        if refittable:
            return {"due": True, "reason": f"this environment can refit members kept for a missing library: {', '.join(refittable)}",
                    "period": period, "definition": defn}
        changed = inputs_changed_since_fit(conn, sport, defn, now)
        if changed:
            return {"due": True, "reason": "results or box/shot data the fit in force consumed have changed since it was trained "
                                           f"(a corrected score or decision, a late result or backfill dated before its cutoff, "
                                           f"or a previous-season result after a fit that started the new season): "
                                           f"{', '.join(changed)} cannot forecast until a retrain", "period": period, "definition": defn}
        return {"due": False, "reason": f"fit in force already prepared for {period} ({_CADENCE.get()} schedule)", "period": period, "definition": defn}
    rows = available_rows(conn, now)
    newest = max((r.result_available_at for r in rows), default=None)
    if newest and fit["training_cutoff"] and newest <= fit["training_cutoff"]:
        return {"due": False, "reason": f"new period {period}, but no eligible result since {fit['training_cutoff']}", "period": period,
                "definition": defn}
    return {"due": True, "reason": f"new period {period} (fit in force: {fit['period']})", "period": period, "definition": defn}


def inputs_changed_since_fit(conn, sport, defn, now) -> list:
    """Walk members in force (latest usable fit of the definition) whose consumed results or box/shot observations, as the
    store shows them now at the fit's cutoff, differ from what the frozen state consumed (`_results_changed` /
    `_events_changed` make them missing at serving), or whose external inputs fail their own `input_mismatch` guard.
    Fits recorded before the digests were kept are not checked for results/events."""
    fits = conn.execute("SELECT f.* FROM adaptive_fits f WHERE f.sport=? AND f.kind='member' AND f.definition=? "
                        "AND f.status='success' AND f.finished_at<=? AND f.fit_id=(SELECT MAX(x.fit_id) FROM adaptive_fits x "
                        "WHERE x.sport=f.sport AND x.kind='member' AND x.name=f.name AND x.definition=f.definition "
                        "AND x.status='success' AND x.finished_at<=?) ORDER BY f.name", (sport, defn, now, now)).fetchall()
    details = {r["name"]: json.loads(r["detail"]) for r in fits}
    consumed = {k: d.get("consumed") for k, d in details.items() if d.get("consumed")}
    # Members with external inputs (player box / injury cache): their own serving guard, `input_mismatch`, against the
    # artifact in force — a mismatch (e.g. box rows appearing for a result the fit was offered without them) blocks the
    # member at serving exactly as a changed result does, so it must schedule the retrain too.
    external = [r for r in fits if details[r["name"]].get("inputs")]
    changed = []
    if external:
        mems = {m.name: m for m in members(conn)[0]}
        for r in external:
            m = mems.get(r["name"])
            if m is None:                  # excluded here (e.g. no cache): nothing it could forecast with, nothing to retrain
                continue
            try:
                art = load_fit(r)
                w = thaw(m.obj, art["state"])
                check = getattr(getattr(w, "member", w), "input_mismatch", None)
                if check is not None and check(art.get("inputs") or {}):
                    changed.append(r["name"])
            except Exception as exc:
                changed.append(f"{r['name']} (fit in force unreadable: {type(exc).__name__})")
    if not consumed:
        return sorted(changed)
    eng = live.engine(conn)
    rows = available_rows(conn, now)
    known = {r.game.game_id: r for r in rows}
    empty = _events_digest([], known, now)
    events = None
    results: dict = {}
    boxes: dict = {}
    for name, c in consumed.items():
        if name in changed:
            continue
        cut = c["cutoff"]
        if cut not in results:
            results[cut] = eng.dataset_version([r for r in rows if r.result_available_at <= cut])
        bad = results[cut] != c["delivered_digest"]
        if not bad and c.get("events_digest") not in (None, empty):
            if events is None:
                events = _events(conn, eng, rows, known, now)
            if cut not in boxes:
                boxes[cut] = _events_digest(events, known, cut)
            bad = boxes[cut] != c["events_digest"]
        if not bad and c.get("season") is not None:              # `_season_changed` (fits recorded since it exist)
            bad = bool(_earlier_seasons_after(rows, cut, c["season"]))
        if bad:
            changed.append(name)
    return sorted(changed)


def kept_for_dependency_here(conn, sport, defn, period, now) -> list:
    """Members whose fit for `period` was kept only because their library was missing, and whose library this
    environment has (the research environment refits what the release environment could only apply)."""
    out = []
    for r in conn.execute("SELECT f.name, f.detail FROM adaptive_fits f WHERE f.sport=? AND f.kind='member' AND f.definition=? "
                          "AND f.period=? AND f.status='success' AND f.finished_at<=? AND f.fit_id=(SELECT MAX(x.fit_id) FROM adaptive_fits x "
                          "WHERE x.sport=f.sport AND x.kind='member' AND x.name=f.name AND x.definition=f.definition AND x.period=f.period "
                          "AND x.status='success' AND x.finished_at<=?) ORDER BY f.fit_id", (sport, defn, period, now, now)):
        d = json.loads(r["detail"])
        if not (d.get("fit") or {}).get("kept_for_dependency"):
            continue
        spec = registry.BY_NAME.get(r["name"].removeprefix("cand:").removesuffix("@adaptive"))
        if spec is not None and can_fit(spec):
            out.append(r["name"])
    return out


class _Sink:
    """Where a training run's ledger writes go: straight into `conn`, or (deferred) into a pending list that `publish`
    applies to the live store later, under a brief lock, after re-verifying every artifact."""

    def __init__(self, conn, deferred: bool):
        self.conn, self.deferred, self.pending = conn, deferred, []

    def fit(self, sport, kind, name, defn, period, status, started, detail, *, cutoff=None, games=None, fp=None, path=None,
            sha=None, ident=None, now=None):
        rec = {"sport": sport, "kind": kind, "name": name, "defn": defn, "period": period, "status": status, "started": started,
               "detail": detail, "cutoff": cutoff, "games": games, "fp": fp, "path": path, "sha": sha, "ident": ident, "now": now}
        if self.deferred:
            self.pending.append(rec)
            return None
        return _apply(self.conn, rec)


def _apply(conn, rec: dict):
    """One pending record into the live ledgers: the model_fits row (identity) first, then the adaptive_fits row."""
    mf = None
    if rec.get("ident") is not None:
        from . import fit_register
        mf = fit_register.record(conn, rec["sport"], f"adaptive:{rec['name']}", rec["defn"], rec["ident"], rec["now"])
    return _insert_fit(conn, rec["sport"], rec["kind"], rec["name"], rec["defn"], rec["period"], rec["status"], rec["started"],
                       rec["detail"], cutoff=rec["cutoff"], games=rec["games"], fp=rec["fp"], path=rec["path"], sha=rec["sha"],
                       model_fit_id=mf)


def publish(conn, out: dict, log=None) -> dict:
    """Applies a deferred training run's pending records to the live store (caller holds the store lock). Every usable
    artifact is verified against the digest its record carries before the row is written; a record whose artifact does
    not verify is written as 'failed' with the reason. Refuses when the live definition no longer matches the run's."""
    log = log or (lambda _m: None)
    pending = out.get("pending") or []
    res = {"published": 0, "failed": [], "definition": out.get("definition")}
    try:
        live_defn = definition(conn)
    except Exception as exc:
        res["error"] = f"definition unavailable: {type(exc).__name__}: {exc}"
        return res
    if live_defn != out.get("definition"):
        res["error"] = f"definition changed during training ({out.get('definition')} -> {live_defn}): nothing published"
        return res
    # The whole batch is verified first: a usable member whose artifact does not verify is published as 'failed', and
    # the combiner of the same run is then published as 'partial' (never 'success'), so the week stays due for a retry.
    checked, broken = [], []
    for rec in pending:
        if rec["status"] in IN_FORCE and rec.get("path"):
            try:
                _load(Path(rec["path"]), rec["sha"])
            except Exception as exc:
                res["failed"].append({"name": rec["name"], "error": f"{type(exc).__name__}: {exc}"})
                broken.append(rec["name"])
                rec = {**rec, "status": "failed", "detail": {"error": f"artifact did not verify at publication: {exc}"},
                       "path": None, "sha": None, "ident": None}
        checked.append(rec)
    # Members first; the combiner last, so a member whose record could not be written (not only one whose artifact did
    # not verify) also downgrades the combiner to 'partial' and the week stays due.
    unwritten = []
    for rec in [r for r in checked if r["kind"] != "combiner"]:
        try:
            _apply(conn, rec)
            res["published"] += 1
        except Exception as exc:
            conn.rollback()
            res["failed"].append({"name": rec["name"], "error": f"{type(exc).__name__}: {exc}"})
            if rec["status"] in IN_FORCE:
                unwritten.append(rec["name"])
    for rec in [r for r in checked if r["kind"] == "combiner"]:
        problems = [n for n in broken if n != "combiner"] + unwritten
        if rec["status"] in IN_FORCE and problems:
            reason = "member record(s) failed at publication: " + ", ".join(sorted(set(problems)))
            detail = {**rec["detail"], "partial_reason": "; ".join(x for x in (rec["detail"].get("partial_reason"), reason) if x)}
            rec = {**rec, "status": "partial", "detail": detail}
        try:
            fid = _apply(conn, rec)
            res["published"] += 1
            res["combiner_status"] = rec["status"]
            if rec["status"] in IN_FORCE:
                res["combiner_fit_id"] = fid
        except Exception as exc:
            conn.rollback()
            res["failed"].append({"name": rec["name"], "error": f"{type(exc).__name__}: {exc}"})
            res["combiner_status"] = "failed"
    log(f"published {res['published']} fit records ({len(res['failed'])} failed)")
    return res


class training_lock:
    """One training run per sport at a time: an advisory lock separate from the store's job lock, so the two-hour odds
    and forecast cycles are never blocked by a refit."""

    def __init__(self, sport: str):
        self.path = root(sport) / "train.lock"
        self.fd = None

    def __enter__(self) -> bool:
        import fcntl
        import os
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        self.fd = fd
        return True

    def __exit__(self, *_):
        import os
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        return False


def snapshot(conn, sport: str) -> Path:
    """A consistent copy of the store (SQLite online backup) for training to read while the live store keeps serving the
    cycles. Older snapshots of the sport are removed; the copy is deleted once the run has been published."""
    import sqlite3
    import os
    folder = root(sport) / "snapshots"
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.sqlite*"):     # leftovers of an interrupted run; the caller holds the training lock
        old.unlink(missing_ok=True)
    path = folder / f"{utc_now().replace(':', '')}-{os.getpid()}.sqlite"
    dst = sqlite3.connect(path)
    conn.backup(dst)
    dst.close()
    return path


def open_snapshot(path: Path):
    import sqlite3
    c = sqlite3.connect(path, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA query_only = ON")
    return c


LOCK_WAIT_SECONDS = 120     # the brief live-lock phases wait out a short job, never a full training run


def run_scheduled(sport: str, *, now: Optional[str] = None, force: bool = False, log=None,
                  lock_wait: float = LOCK_WAIT_SECONDS) -> dict:
    """The scheduled training command: (1) under the store lock, briefly: due check and a consistent snapshot;
    (2) under the sport's own training lock: the full run from the snapshot, artifacts to disk, no ledger writes;
    (3) under the store lock, briefly: verify and publish the fit records. Forecast cycles run throughout (2)."""
    from . import db
    log = log or (lambda _m: None)
    out = {"sport": sport, "status": "skipped"}
    # The training lock is taken first and held through the snapshot, the training, the publication and the cleanup, so
    # two trainers of one sport can never share, delete or republish each other's snapshot or records.
    with training_lock(sport) as held:
        if not held:
            return {**out, "reason": "another training run holds the training lock"}
        with db.store_lock(sport, wait_seconds=lock_wait) as held_store:
            if not held_store:
                return {**out, "reason": "store busy: another job holds the job lock"}
            conn = db.connect(sport=sport)
            try:
                if not _has(conn, "adaptive_fits"):
                    return {**out, "status": "failed", "reason": "store not migrated to 0024 (adaptive_fits missing)"}
                # The training cutoff is taken under the store lock, before the snapshot: a job that commits after the lock
                # is released retrieves its results later than this, so every result at or before the cutoff is in the
                # snapshot (a cutoff stamped later made the frozen state miss them and the digest guard abstain).
                now = now or utc_now()
                d = due(conn, now)
                out["due"] = d
                if not d["due"] and not force:
                    return {**out, "reason": d["reason"]}
                snap = snapshot(conn, sport)
            finally:
                conn.close()
        log(f"snapshot {snap.name}; training from it under the training lock")
        sconn = open_snapshot(snap)
        try:
            run = train(sconn, now=now, write=True, force=True, log=log, publish=False)
        finally:
            sconn.close()
        with db.store_lock(sport, wait_seconds=lock_wait) as held_store:
            if not held_store:
                return {**out, "status": "failed", "reason": "store busy at publication: fit records not published (artifacts kept; "
                                                            "the next run republishes)", "training": _summary(run)}
            conn = db.connect(sport=sport)
            try:
                pub = publish(conn, run, log)
            finally:
                conn.close()
        for leftover in snap.parent.glob(snap.name + "*"):      # the copy and its -wal / -shm files
            leftover.unlink(missing_ok=True)
    status = run["status"] if not pub.get("error") and not pub["failed"] else "failed"
    if pub.get("combiner_status") == "partial" and status == "success":
        status = "partial"
    return {**out, "status": status, "training": _summary(run), "publication": pub, "snapshot": snap.name}


def _summary(run: dict) -> dict:
    return {k: v for k, v in run.items() if k not in ("members", "combiner", "pending")} | {
        "members": {k: v.get("status") for k, v in run.get("members", {}).items()},
        "combiner": {k: v for k, v in run.get("combiner", {}).items() if k in ("status", "partial_reason", "error", "artifact")}}


# --- Training --------------------------------------------------------------------------------------------------------
def _window(seasons, season: int) -> set:
    """The combiner's training seasons: the last WINDOW_SEASONS completed ones and the current one."""
    completed = sorted({s for s in seasons if s < season})[-WINDOW_SEASONS:]
    return set(completed) | {season}


def _realised(sport, market, line, r) -> Optional[int]:
    return combination_research._realised(market, line, r.home_score, r.away_score, getattr(r, "decision", None))


def _events(conn, eng, rows, known, now):
    return pool_live._events(conn, eng, rows, known, now)


def _events_for(member_obj, eng, events):
    """NHL shot events reach only the xG member (as agreement.predict); NBA box events reach every member that consumes
    them (`update_box`); the others never see them, so a box backfill cannot change what they were fitted on."""
    inner = getattr(member_obj, "member", member_obj)
    if eng.sport == "nhl":
        return events if hasattr(inner, "xg") else []
    return events if hasattr(inner, "update_box") else []


def train(conn, *, now: Optional[str] = None, write: bool = True, force: bool = False, log=None, publish: bool = True) -> dict:
    """Prepares the artifacts for the period of the next eligible game: every member's fit (when new results exist), the
    out-of-sample history, the combiner per market with its selection decision. Restartable and idempotent. With
    `publish=False` the ledger rows are returned in `pending` (for `publish()` under the live lock) instead of written."""
    log = log or (lambda _m: None)
    sink = _Sink(conn, deferred=not publish)
    now = now or utc_now()
    started = utc_now()
    eng = live.engine(conn)
    sport = eng.sport
    out = {"sport": sport, "as_of": now, "status": "success", "members": {}, "combiner": {}, "errors": {}}
    d = due(conn, now)
    out.update({k: v for k, v in d.items() if k != "due"})
    if not d["due"] and not force:
        out["status"] = "skipped"
        return out
    if "definition" not in d:
        out["status"] = "failed"
        out["error"] = d["reason"]
        return out
    defn, period = d["definition"], d.get("period")
    nxt = next_game(conn, now)
    if nxt is None:
        out["status"] = "skipped"
        out["reason"] = "no upcoming eligible game"
        return out
    season = nxt["season"]
    rows = available_rows(conn, now)
    known = {r.game.game_id: r for r in rows}
    events = _events(conn, eng, rows, known, now)
    window = _window({r.game.season for r in rows}, season)
    in_flight = _in_flight(conn, eng, rows, now)
    mems, excluded = members(conn)
    out["excluded"] = excluded
    lines: dict = {}
    hist: dict = {}            # member -> game -> market -> vector (window seasons only)
    base = root(sport)
    # 1. Walk members: history, then the fit for the next period; the frozen state is the artifact.
    walkers = sorted([m for m in mems if m.kind == "walk"], key=lambda m: not m.line_primary)   # the line primary sets the lines
    for m in walkers:
        t0 = utc_now()
        try:
            invalid: list = []
            prepare = getattr(getattr(m.obj, "member", m.obj), "prepare_training_inputs", None)
            if prepare:              # external inputs the serving path reads (live injury reports) are read for training too
                prepare(conn, now)
            cut = frozen_at(m.obj, rows, in_flight, now)
            unwalked = copy.deepcopy(m.obj) if cut != now else None
            hist[m.name] = walk_member(m, rows, _events_for(m.obj, eng, events), now, window, lines, line_primary=m.line_primary,
                                       invalid=invalid)
            # an online member is frozen before the games in flight (walked again to that time; history above is unchanged)
            w = m.obj if unwalked is None else _walk_until(unwalked, rows, _events_for(m.obj, eng, events), known, cut)
            _training_forecast(w, replace(eng.pregame(nxt), cutoff_at=now))
            info = w.fit_info() if isinstance(w, Adaptive) else None
            ident = None
            if isinstance(w, Adaptive):
                from . import fit_register
                ident = fit_register.identity(w, rows, now)
            fp = (info or {}).get("results_fingerprint") or eng.dataset_version(rows)
            # Members with external inputs (the player box / injury-report cache) report what they actually read, so a
            # missing or partial cache is visible in the fit record rather than silently degrading the features.
            inputs = getattr(getattr(w, "member", w), "input_status", None)
            inputs = inputs() if inputs else None
            art = {"kind": "walk", "name": m.name, "state": freeze(w), "period": period, "cutoff": cut,
                   "results_available_through": max((r.result_available_at for r in rows), default=now),
                   "results_fingerprint": fp, "fit_info": info, "markets": list(m.markets), "fitted_at": utc_now(),
                   # exactly the results the frozen state consumed, and box/shot data
                   "delivered_digest": eng.dataset_version([r for r in rows if r.result_available_at <= cut]),
                   "events_digest": _events_digest(_events_for(m.obj, eng, events), known, cut),
                   "definition": defn, "training_games": (info or {}).get("delivered_results", len(rows)), "inputs": inputs}
            path = base / "members" / m.name.replace(":", "__") / f"{defn}-{period.replace(':', '_')}-{fp}.pkl"
            sha = None
            mf = None
            if write:
                path, sha = _save(path, art)
                sink.fit(sport, "member", m.name, defn, period, "success", t0,
                         {"fit_info": info, "refits_within_season": len(getattr(w, "refits", [])), "learned": m.learned,
                          "consumed": {"cutoff": art["cutoff"], "delivered_digest": art["delivered_digest"],
                                       "events_digest": art["events_digest"],
                                       "season": _season_fit(w)},
                          **({"inputs": inputs} if inputs is not None else {})},
                         cutoff=art["results_available_through"], games=art["training_games"], fp=fp, path=str(path), sha=sha,
                         ident=ident, now=now)
            out["members"][m.name] = {"status": "success", "fit": info, "artifact": str(path) if write else None,
                                      "history_games": len(hist[m.name]), **({"frozen_at": cut} if cut != now else {}), **({"inputs": inputs} if inputs is not None else {}),
                                      **({"invalid_vectors": {"count": len(invalid), "first": invalid[:5]}} if invalid else {})}
            log(f"{m.name}: walked {len(hist[m.name])} window games; fit {info and info.get('kind')}")
        except Exception as exc:
            out["errors"][m.name] = f"{type(exc).__name__}: {exc}"
            out["members"][m.name] = {"status": "failed", "error": out["errors"][m.name]}
            if write:
                conn.rollback()
                sink.fit(sport, "member", m.name, defn, period, "failed", t0, {"error": out["errors"][m.name]})
    # 2. Candidates on the shared feature walk: scheduled out-of-sample history, then the fit for the next period.
    cands = [m for m in mems if m.kind == "candidate"]
    history = load_history(sport, defn) if write else {"candidates": {}}
    checkpoint = (lambda: save_history(sport, defn, history)) if write else (lambda: None)
    if cands:
        try:
            recorder = pool_live._recorder(conn, eng, set(), now)
            walk = features.walk(recorder, rows, events, deliver_until=now)
            hrows = sorted(walk.values(), key=lambda r: (r.cutoff_at, r.tipoff_at, r.game_id))
            labelled = [r for r in hrows if r.y is not None and r.result_available_at is not None and r.result_available_at <= now]
        except Exception as exc:
            out["errors"]["feature_walk"] = f"{type(exc).__name__}: {exc}"
            hrows, labelled = [], []
    for m in cands:
        t0 = utc_now()
        spec = m.spec
        try:
            if not hrows:
                raise RuntimeError("feature walk unavailable")
            store = history["candidates"].setdefault(spec.name, {})
            scheduled_walk(spec, hrows, store, checkpoint, log, from_season=min(window) - 1)
            checkpoint()                                   # the history holds each period's own fit, never the live one
            rec, prepared = live_fit(spec, store, labelled, period, season, now)
            if prepared.get("model") is None:
                raise RuntimeError(rec.get("reason") or "no fit")
            sd = _sd(prepared["residuals"], now) if spec.head == "normal" else None
            fp = prepared["fitted_fp"]
            # Information time of every component: the model's own fit and, separately, the residual spread, which is
            # recomputed at every training run (a kept model's spread can include results after the model's fit).
            components = {"model": prepared["fit_cutoff"],
                          "sd": max((r[0] for r in prepared["residuals"] if r[0] <= now), default=None) if sd else None}
            art = {"kind": "candidate", "name": m.name, "spec": spec.name, "model": prepared["model"], "sd": sd, "period": period,
                   "cutoff": prepared["fit_cutoff"], "components": components,
                   "results_available_through": prepared["results_through"],
                   "results_fingerprint": fp, "training_games": prepared["fit_games"], "fitted_at": prepared["fitted_at"],
                   "info": prepared["info"], "definition": defn, "fit_period": prepared["fit_period"], "refit_now": rec["refit"]}
            hist[m.name] = candidate_vectors(spec, {g: o for g, o in store["outputs"].items() if g in lines or walk[g].season in window},
                                             m.markets, lines)
            path = base / "members" / m.name.replace(":", "__") / f"{defn}-{period.replace(':', '_')}-{fp}.pkl"
            sha = mf = None
            if write:
                path, sha = _save(path, art)
                ident = {"schedule": _CADENCE.get(), "fit_kind": f"adaptive-{_CADENCE.get()}" if rec["refit"] else "kept", "trained_season": season,
                         "training_games": prepared["fit_games"], "results_fingerprint": fp,
                         "results_available_through": prepared["results_through"], "params_digest": sha[:24]}
                sink.fit(sport, "member", m.name, defn, period, "success", t0,
                         {"fit": rec, "fit_period": prepared["fit_period"], "info": prepared["info"], "learned": True},
                         cutoff=prepared["results_through"], games=prepared["fit_games"], fp=fp, path=str(path), sha=sha,
                         ident=ident, now=now)
            out["members"][m.name] = {"status": "success", "fit": rec, "artifact": str(path) if write else None,
                                      "history_games": len(hist[m.name])}
        except deep.DependencyMissing as exc:       # fitted elsewhere: skipped with the reason, never an error
            out["members"][m.name] = {"status": "skipped", "reason": str(exc)}
            if write:
                sink.fit(sport, "member", m.name, defn, period, "skipped", t0, {"reason": str(exc)})
        except Exception as exc:
            out["errors"][m.name] = f"{type(exc).__name__}: {exc}"
            out["members"][m.name] = {"status": "failed", "error": out["errors"][m.name]}
            if write:
                conn.rollback()
                sink.fit(sport, "member", m.name, defn, period, "failed", t0, {"error": out["errors"][m.name]})
    # 3. Combiner per market from the window rows (walk-forward) with settled prospective forecasts overlaid.
    t0 = utc_now()
    try:
        table = training_rows(conn, sport, defn, rows, window, lines, hist, now)
        prev = latest_fit(conn, sport, "combiner", "combiner", defn, now)
        prev_detail = json.loads(prev["detail"]) if prev else {}
        comb = {"version": VERSION, "definition": defn, "period": period, "cutoff": now, "markets": {}, "selection": {},
                "members_available": sorted(hist), "excluded": excluded, "fitted_at": utc_now(),
                "results_available_through": max((r.result_available_at for r in rows), default=now)}
        if _CADENCE.get() == "daily":
            comb["agreement_history"] = {mk: [{**r, "tipoff_at": known[r["game_id"]].game.tipoff_at}
                                                for r in rs] for mk, rs in table.items()}
            comb["cadence"] = "daily"
            from . import daily_agreement
            old_daily = {}
            if prev:        # the incumbent rule lives in the previous ARTIFACT; an unreadable one costs the incumbent, never the run
                try:
                    old_daily = load_fit(prev).get("daily_agreement", {})
                except Exception as exc:
                    comb["daily_incumbent_error"] = f"previous combiner fit {prev['fit_id']} unreadable, no incumbent rule carried: {type(exc).__name__}: {exc}"
                    log(comb["daily_incumbent_error"])
            comb["daily_agreement"] = {}
        market_errors = {}
        for market in sports.get(sport).markets:
            # One market's failure leaves that market without a combination in this fit (it keeps the combination of the
            # earlier fit, and the 'partial' fit is retried); the other markets are still fitted.
            if _CADENCE.get() == "daily":     # its own guard: a failed rule assessment never costs the market its combination
                try:
                    comb["daily_agreement"][market] = daily_agreement.assess(
                        market, comb["agreement_history"].get(market, []), now, old_daily.get(market, {}).get("rule"))
                except Exception as exc:
                    err = f"{type(exc).__name__}: {exc}"
                    market_errors[f"daily rule {market}"] = err
                    log(f"daily rule {market}: {err}")
                    if market in old_daily:      # the earlier frozen decision stays in force, as for a failed fit
                        comb["daily_agreement"][market] = {**old_daily[market], "carried_after_failure": err}
            try:
                cands_m = [m.name for m in mems if market in m.markets and m.name in hist]
                # Members guarded by `stale_inputs` read the data directory's shared player cache and are marked missing
                # together when it is stale or changed (B1/B2 guards in `_record`): the market also needs a stack trained
                # without all of them, else every forecast of the market would abstain with them.
                shared = [m.name for m in mems if m.name in cands_m
                          and getattr(getattr(m.obj, "member", m.obj), "stale_inputs", None) is not None]
                comb["markets"][market] = fit_combiner(table.get(market, []), cands_m, market, now,
                                                       groups=[shared] if len(shared) > 1 else [])
                incumbent = (prev_detail.get("selection") or {}).get(market, {}).get("rule")
                comb["selection"][market] = _decide(conn, sport, defn, market, now, incumbent)
            except Exception as exc:
                market_errors[market] = f"{type(exc).__name__}: {exc}"
                log(f"combiner {market}: {market_errors[market]}")
                comb["markets"][market] = {"market": market, "cutoff": now, "excluded": {}, "members": [], "stack": None,
                                           "fallbacks": {}, "reason": f"combiner fit failed: {market_errors[market]}"}
                comb["selection"][market] = {"status": "failed", "rule": None, "reason": comb["markets"][market]["reason"]}
        fp = _key(sorted((mk, v.get("training", {}).get("results_through"), v.get("members")) for mk, v in comb["markets"].items()))[:16]
        path = base / "combiner" / f"{defn}-{period.replace(':', '_')}-{fp}.pkl"
        sha = None
        # 'success' only when every market with at least two available members got a combination and no member refit
        # failed; otherwise 'partial' (its usable markets are applied, earlier fits cover the rest, and the next run retries).
        available = {mk: {n for r in table.get(mk, []) for n in r["vectors"]} for mk in comb["markets"]}
        no_stack = [mk for mk, v in comb["markets"].items()
                    if v.get("stack") is None and len(available[mk]) >= 2 and mk not in market_errors]
        failed = sorted(k for k, v in out["members"].items() if v.get("status") == "failed")
        partial_reason = "; ".join([f"{mk} assessment failed: {e}" if mk.startswith("daily rule") else f"combiner fit failed for {mk}: {e}"
                                    for mk, e in market_errors.items()]
                                   + ([f"no combination for {', '.join(no_stack)}"] if no_stack else [])
                                   + ([f"member refit failed: {', '.join(failed)}"] if failed else [])) or None
        status = "partial" if partial_reason else "success"
        out["errors"].update({f"combiner:{mk}": e for mk, e in market_errors.items()})
        detail = {"markets": {mk: {k: v.get(k) for k in ("members", "excluded", "weights", "l2", "l2_validation", "training", "reason")}
                              for mk, v in comb["markets"].items()},
                  "selection": {mk: {k: v.get(k) for k in ("status", "rule", "reason", "settled_items", "judged_items")}
                                for mk, v in comb["selection"].items()},
                  "excluded": excluded, "members_available": comb["members_available"], "partial_reason": partial_reason}
        if write:
            path, sha = _save(path, comb)
            fid = sink.fit(sport, "combiner", "combiner", defn, period, status, t0, detail,
                           cutoff=comb["results_available_through"], games=sum(v.get("training", {}).get("games") or 0 for v in comb["markets"].values()),
                           fp=fp, path=str(path), sha=sha)
            out["combiner"]["fit_id"] = fid
        out["combiner"].update({"status": status, "artifact": str(path) if write else None, **detail})
    except Exception as exc:
        out["errors"]["combiner"] = f"{type(exc).__name__}: {exc}"
        out["combiner"] = {"status": "failed", "error": out["errors"]["combiner"]}
        if write:
            conn.rollback()
            sink.fit(sport, "combiner", "combiner", defn, period, "failed", t0, {"error": out["errors"]["combiner"]})
    if out["errors"] or out["combiner"].get("status") == "partial":
        out["status"] = "failed" if out["combiner"].get("status") == "failed" else "partial"
    if sink.deferred:
        out["pending"] = sink.pending
    out["finished_at"] = utc_now()
    out["started_at"] = started
    return out


def training_rows(conn, sport, defn, rows, window, lines, hist, now) -> dict:
    """market -> out-of-sample rows for the combiner: the walk-forward reconstruction of every window game at the
    primary's model line, replaced by the settled prospective adaptive forecast of the same game and market when one
    exists (its recorded line, its recorded member probabilities; made before the outcome was available)."""
    table: dict = {}
    for r in rows:
        g = r.game
        if g.season not in window or r.result_available_at > now:
            continue
        for market in sports.get(sport).markets:
            line = None if market in ("winner", "reg3") else lines.get(g.game_id, {}).get(market)
            if market not in ("winner", "reg3") and line is None:
                continue
            y = _realised(sport, market, line, r)
            if y is None:
                continue
            vecs = {name: h[g.game_id][market] for name, h in hist.items() if g.game_id in h and market in h[g.game_id]}
            table.setdefault(market, []).append({"game_id": g.game_id, "season": g.season, "cutoff_at": r.cutoff_at,
                                                 "result_available_at": r.result_available_at, "line": line, "realised": y,
                                                 "vectors": vecs, "source": "walk_forward"})
    for item in prospective_rows(conn, sport, defn, now):
        t = table.get(item["market"])
        if t is None:
            continue
        for i, r in enumerate(t):
            if r["game_id"] == item["game_id"]:
                t[i] = {**r, "line": item["line"], "realised": item["realised"], "vectors": item["vectors"], "source": "prospective"}
                break
    return table


def prospective_rows(conn, sport, defn, now) -> list:
    """Settled canonical adaptive forecasts (latest before the start, made before the outcome was available, start
    unchanged), with every recorded member probability: the prospective part of the next combiner fit."""
    if not _has(conn, "adaptive_forecasts"):
        return []
    out = []
    for r in conn.execute(
            "SELECT a.*, g.status AS g_status, g.home_score, g.away_score, g.result_available_at, g.tipoff_at AS game_tipoff_at, "
            "d.decision FROM adaptive_forecasts a JOIN games g ON g.game_id=a.game_id LEFT JOIN game_decisions d ON d.game_id=a.game_id "
            "WHERE a.sport=? AND a.definition=? AND a.status='forecast' AND a.forecast_id=(SELECT x.forecast_id FROM adaptive_forecasts x "
            "WHERE x.game_id=a.game_id AND x.market=a.market AND x.definition=a.definition AND x.status='forecast' "
            "AND x.generated_at < x.tipoff_at AND x.generated_at<=? ORDER BY x.generated_at DESC, x.forecast_id DESC LIMIT 1)",
            (sport, defn, now)):
        if r["g_status"] != "final" or r["game_tipoff_at"] != r["tipoff_at"] or not r["result_available_at"] \
                or r["result_available_at"] > now or r["generated_at"] >= r["result_available_at"]:
            continue
        y = combination_research._realised(r["market"], r["line"], r["home_score"], r["away_score"], r["decision"])
        if y is None:
            continue
        book = json.loads(r["bookmaker"]) if r["bookmaker"] else None
        out.append({"game_id": r["game_id"], "market": r["market"], "line": r["line"], "realised": y,
                    "tipoff_at": r["tipoff_at"], "result_available_at": r["result_available_at"],
                    "vectors": {m["name"]: tuple(m["p"]) for m in json.loads(r["members"]) if m.get("p")},
                    "adaptive": (r["p_a"], r["p_mid"], r["p_b"]), "bookmaker": tuple(book["p"]) if book else None,
                    "week": selector.week_of(r["tipoff_at"])})
    return out


def _decide(conn, sport, defn, market, now, incumbent) -> dict:
    """The selector's weekly rule for the adaptive source from settled prospective adaptive forecasts only (same
    thresholds, minimum picks, coverage and weeks as the research selector; filter 'none' only)."""
    items = [{"game_id": i["game_id"], "week": i["week"], "tipoff_at": i["tipoff_at"], "line": i["line"],
              "vectors": {"adaptive": i["adaptive"]}, "agreement": {}, "bookmaker": i["bookmaker"], "realised": i["realised"]}
             for i in prospective_rows(conn, sport, defn, now) if i["market"] == market and i["tipoff_at"] < now]
    d = selector.decide(items, market, ["adaptive"], incumbent, filters=("none",))
    return {k: d[k] for k in d if k != "stats"} | {"evidence_until": now, "selector_version": selector.version()}


# --- Forecast path ----------------------------------------------------------------------------------------------------
def _book(conn, quote_id, line, until=None) -> Optional[dict]:
    """The de-vigged stored quote at exactly this line (and, in a replay, retrieved at or before `until`)."""
    if quote_id is None:
        return None
    qr = conn.execute("SELECT * FROM market_quotes WHERE quote_id=?", (quote_id,)).fetchone()
    if qr is None or qr["line"] != line or (until is not None and qr["retrieved_at"] > until):
        return None
    return {"p": [qr["p_a_devig"], qr["p_mid_devig"] or 0.0, qr["p_b_devig"]], "provider": qr["provider"],
            "retrieved_at": qr["retrieved_at"], "quote_table": "market_quotes", "quote_id": quote_id}


def _primary_line(conn, g, market, sport, now, replay: bool = False):
    """The exact line, source, quote and reference of the primary two-hour market forecast (as research cohorts).
    replay=True: only a primary forecast made at or before `now` (the simulated cutoff) counts; failing that, the line of
    the primary reconstruction (recovered_forecasts) with its quote only if that was retrieved at or before `now`."""
    from . import market_live
    _, base = market_live.market_model(conn)
    mv = market_live.model_version(base, "near_tipoff")
    prim = conn.execute(
        "SELECT * FROM market_forecasts WHERE game_id=? AND market=? AND policy='near_tipoff' AND run_type='live' "
        "AND model_version=? AND tipoff_at=? AND generated_at>=? AND generated_at<=? ORDER BY generated_at DESC, forecast_id DESC LIMIT 1",
        (g["game_id"], market, mv, g["tipoff_at"], _fmt(_parse(g["tipoff_at"]) - near_tipoff.WINDOW),
         now if replay else "9999")).fetchone()
    if prim is not None:
        book = _book(conn, prim["market_quote_id"], prim["line"], now if replay else None)
        return {"line": prim["line"], "source": prim["line_source"], "quote": prim["market_quote_id"] if book or not replay else None,
                "ref": f"market_forecasts:{prim['forecast_id']}", "book": book}
    if not replay or not _has(conn, "recovered_forecasts"):
        return None
    rec = conn.execute("SELECT * FROM recovered_forecasts WHERE game_id=? AND market=? AND tipoff_at=? AND cutoff_at<=?",
                       (g["game_id"], market, g["tipoff_at"], now)).fetchone()
    if rec is None:
        return None
    book = _book(conn, rec["quote_id"], rec["line"], now)
    return {"line": rec["line"], "source": rec["line_source"], "quote": rec["quote_id"] if book else None,
            "ref": f"recovered_forecasts:{rec['recovery_id']}", "book": book}


REPLAY_LOOKBACK = 6     # fits per member/combiner examined in a replay (newest training first)


def _replay_fits(conn, sport, kind, name, defn, at, limit=REPLAY_LOOKBACK) -> list:
    """Replay only: usable fits trained on results through `at`, newest training first."""
    if not _has(conn, "adaptive_fits"):
        return []
    return conn.execute(f"SELECT * FROM adaptive_fits WHERE sport=? AND kind=? AND name=? AND definition=? AND status IN "
                        f"({','.join('?' * len(IN_FORCE))}) AND {FIT_ORDER['training']} LIMIT ?",
                        (sport, kind, name, defn, *IN_FORCE, at, limit)).fetchall()


class ReplayUnavailable(RuntimeError):
    """Replay only: an input could not be read (artifact file, digest, evaluation). Not evidence about the game: the
    caller records nothing and retries; it never becomes an 'unreconstructable' row."""


def information_at(art: dict) -> Optional[str]:
    """The latest information used by any component of an artifact: its training snapshot (`cutoff`) and every entry of
    `components` (a candidate's model fit and its residual spread). None when it cannot be established: a candidate
    artifact with a spread but no recorded spread time (written before component times were kept)."""
    comps = art.get("components") or {}
    if art.get("kind") == "candidate" and art.get("sd") is not None and comps.get("sd") is None:
        return None
    times = [t for t in (art.get("cutoff"), *comps.values()) if t]
    return max(times) if art.get("cutoff") else None


def _informed_by(art: dict, at: str) -> bool:
    """Replay only: every component of the artifact was informed at or before `at`."""
    t = information_at(art)
    return t is not None and t <= at


def _load_member_artifacts(conn, sport, defn, mems, now, replay: bool = False,
                           skipped: Optional[dict] = None) -> tuple[dict, dict]:
    """Live: each member's latest published fit. Replay: the newest fit trained on, and informed by, data at or before
    `now`; a newer fit is passed over only for that established reason (recorded in `skipped`). An artifact that cannot
    be read raises ReplayUnavailable rather than falling back to an older fit or counting the member as missing."""
    arts, missing = {}, {}
    for m in mems:
        if replay:
            rows = _replay_fits(conn, sport, "member", m.name, defn, now)
            if not rows:
                missing[m.name] = "no fit for this definition trained only on results before this game's cutoff"
                continue
            problems = []
            for row in rows:
                try:
                    art = load_fit(row)
                except Exception as exc:
                    raise ReplayUnavailable(f"{m.name} fit {row['fit_id']}: artifact unavailable: {type(exc).__name__}: {exc}") from exc
                if not _informed_by(art, now):
                    problems.append(f"fit {row['fit_id']}: information time {information_at(art)} is after this game's cutoff")
                    continue
                arts[m.name] = (row, art)
                break
            else:
                missing[m.name] = "; ".join(problems)
            if skipped is not None and problems:
                skipped[m.name] = problems
            continue
        row = latest_fit(conn, sport, "member", m.name, defn, now)
        if row is None:
            missing[m.name] = "no adaptive fit for this definition yet (adaptive-train has not produced one)"
            continue
        try:
            arts[m.name] = (row, load_fit(row))
        except Exception as exc:
            missing[m.name] = f"artifact unavailable: {type(exc).__name__}: {exc}"
    return arts, missing


def _results_changed(eng, art: dict, rows, digests: dict) -> Optional[str]:
    """None when the results available at the artifact's cutoff are still exactly the ones its frozen state consumed.
    A score corrected later (its availability moves past the cutoff) or a final ingested late with an assumed
    availability before the cutoff cannot be delivered to the frozen state in walk order: the member is missing, with
    this reason, until the next fit. Artifacts without the digest predate this check."""
    recorded = art.get("delivered_digest")
    if recorded is None:
        return None
    cutoff = art["cutoff"]
    if cutoff not in digests:
        digests[cutoff] = eng.dataset_version([r for r in rows if r.result_available_at <= cutoff])
    if digests[cutoff] != recorded:
        return (f"results changed since the fit: the results available at its cutoff {cutoff} differ from the ones it "
                "consumed (a corrected score or a late-ingested result); retrain before this member can forecast")
    return None


def _events_digest(events, known, cutoff) -> str:
    """Fingerprint of the box/shot observations a walk delivered by `cutoff` (effective availability: the later of the
    observation's and its game result's). Observations are append-only per fetch, so the fetch identifies the content."""
    h = hashlib.sha256()
    for e in sorted(events, key=lambda e: (e.game.game_id, e.fetch_id)):
        if max(e.available_at, known[e.game.game_id].result_available_at) <= cutoff:
            h.update(f"{e.game.game_id}|{e.fetch_id}|{e.available_at}\n".encode())
    return h.hexdigest()[:16]


def _events_changed(art: dict, events, known, digests: dict) -> Optional[str]:
    """None when the box/shot observations dated at or before the artifact's cutoff are exactly the ones its frozen state
    consumed. A backfill imported later but dated before the cutoff (historical backfills take the result's time) cannot
    be delivered in walk order (`_advance` skips it as already delivered): the member is missing until the next fit.
    Artifacts without the digest predate this check."""
    recorded = art.get("events_digest")
    if recorded is None:
        return None
    key = ("events", art["cutoff"], len(events))      # within one cycle a member gets either every event or none
    if key not in digests:
        digests[key] = _events_digest(events, known, art["cutoff"])
    if digests[key] != recorded:
        return (f"box/shot inputs changed since the fit: observations dated at or before its cutoff {art['cutoff']} differ "
                "from the ones it consumed (a backfill imported after the fit); retrain before this member can forecast")
    return None


def _earlier_seasons_after(rows, cutoff: str, season: int) -> list:
    return sorted({r.game.season for r in rows if r.result_available_at > cutoff and r.game.season < season})


def _training_forecast(w, game) -> None:
    """The training-time forecast of the next game, for learned members only: it makes the period's scheduled refit, the
    fit the artifact freezes. Online members are frozen as walked: their season start (carry) runs in walk order when
    serving delivers the results still to come (`_advance`), as in a fresh walk, never ahead of a pending result."""
    if not isinstance(w, Adaptive):
        return
    pending = getattr(w.member, "pending", None)
    cached = game.game_id in (pending or {})
    w.predict(game)
    if not cached:   # features cached at the training cutoff must not be served after newer results are delivered
        (getattr(w.member, "pending", None) or {}).pop(game.game_id, None)
    w.frozen = True


def _in_flight(conn, eng, rows, now) -> list:
    """Forecast cutoffs of the eligible games in flight at `now`: their cutoff has passed, their result is not available
    yet (scheduled, in progress, or final with a later availability). Postponed and canceled games never become rows.
    A game still scheduled or in progress more than `ingest.OBSERVED_WINDOW` after its start is stuck (a feed that never
    updated it, a game suspended for days), not in flight: counting it would freeze the online members before it for as
    long as it stays so (every serve replaying everything since). The trade-off: if it later turns final with an
    assumed availability before the fit, every walk member is missing until the next fit (`_results_changed`, as for a
    game marked postponed at training); with one after it, serving replays it exactly except for members whose forecast
    is part of their state or a season start (`_landed_in_flight`: missing until the next fit). Finals with a later
    availability stay in flight."""
    known = {r.game.game_id for r in rows}
    types = eng.eligible_types
    stale = _fmt(_parse(now) - ingest.OBSERVED_WINDOW)
    out = []
    for g in conn.execute(f"SELECT game_id, tipoff_at, status, result_available_at FROM games WHERE tipoff_at IS NOT NULL "
                          f"AND status IN ('scheduled', 'in_progress', 'final') AND season_type IN ({','.join('?' * len(types))})",
                          types):
        if g["game_id"] in known or (g["status"] == "final" and not (g["result_available_at"] or "") > now):
            continue
        if g["status"] != "final" and g["tipoff_at"] < stale:
            continue
        cutoff = forecast_cutoff(g["tipoff_at"])
        if cutoff <= now:
            out.append(cutoff)
    return out


def frozen_at(w, rows, in_flight, now) -> str:
    """The cutoff of a walk member's frozen state. Learned members: `now` (their fit is the period's fit). Online members:
    the latest time at or before `now` at which no game was in flight (its forecast cutoff passed, its result not yet
    available; `in_flight`: those still without a result). Frozen later, a game in flight would be forecast at serving at
    its own cutoff with a state that already holds results and box scores of after it (a fresh walk forecasts it before
    them), and its forecast is part of the state (e.g. efficiency_margin's residuals, the next season's sigma); frozen
    there, serving replays everything after it in walk order (`_advance`)."""
    if isinstance(w, Adaptive):
        return now
    spans = [(c, None) for c in in_flight] + [(r.cutoff_at, r.result_available_at) for r in rows if r.result_available_at > r.cutoff_at]
    at = now
    while True:
        open_ = [c for c, end in spans if c <= at and (end is None or end > at)]
        if not open_:
            return at
        at = _fmt(_parse(min(open_)) - timedelta(seconds=1))


def _walk_until(w, rows, events, known, at):
    """`w` walked over the results available at `at` and the box/shot data available then (effective time)."""
    head = [r for r in rows if r.result_available_at <= at]
    ids = {r.game.game_id for r in head}
    backtest.run(w, head, [e for e in events if e.game.game_id in ids
                           and max(e.available_at, known[e.game.game_id].result_available_at) <= at], deliver_until=at)
    return w


def _season_fit(w) -> Optional[int]:
    """The season a learned member's frozen fit is for; None for online members (they start seasons in walk order)."""
    return getattr(w.member, "season", None) if isinstance(w, Adaptive) else None


def _season_changed(w, art: dict, rows) -> Optional[str]:
    """None unless a result of an earlier season became available after the artifact's cutoff while a learned member's
    frozen fit is for a later season (the training-time forecast of the next game made its season-start refit). A fresh
    walk delivers that result before the season starts, the frozen fit cannot: the member is missing until the next fit
    (`inputs_changed_since_fit` makes that fit due)."""
    season = _season_fit(w)
    late = _earlier_seasons_after(rows, art["cutoff"], season) if season is not None else []
    if late:
        return (f"results of season {late[-1]} arrived after the fit had started season {season}; retrain before this member "
                "can forecast")
    return None


def _landed_in_flight(w, art: dict, rows) -> Optional[str]:
    """None unless an online member's state was frozen while a game it did not wait for was in flight (stuck past the
    `_in_flight` bound, or not in the store at the fit), that game's result has arrived since, and replaying it differs
    from a fresh walk. `_advance` forecasts it at its cutoff with the state of the fit cutoff; that differs only when the
    forecast is part of the state (`FORECAST_IN_STATE`) or when it starts its season (every online member does so at its
    first forecast of a season) ahead of an earlier season's result delivered between its cutoff and the fit. Otherwise
    the replay is exact and the member keeps serving; else it is missing until the next fit (frozen before the game)."""
    if isinstance(w, Adaptive):
        return None
    cut = art["cutoff"]
    for g in (r for r in rows if r.cutoff_at <= cut < r.result_available_at):
        if isinstance(w, FORECAST_IN_STATE) or any(r.game.season < g.game.season and g.cutoff_at < r.result_available_at <= cut
                                                   for r in rows):
            return (f"{g.game.game_id} was in flight at the fit cutoff and has a result now; retrain before this member "
                    "can forecast")
    return None


def _frozen_state_unusable(eng, w, art: dict, rows, events, known, digests: dict) -> Optional[str]:
    """Why a thawed walk member cannot be brought up to date by `_advance` (None when it can)."""
    return (_results_changed(eng, art, rows, digests) or _events_changed(art, events, known, digests)
            or _season_changed(w, art, rows) or _landed_in_flight(w, art, rows))


def _advance(w, art: dict, rows, events, known, now):
    """Delivers results and box/shot events that became available after the artifact's cutoff to the frozen state."""
    cutoff = art["cutoff"]
    season_fit = _season_fit(w)
    # Results of a season a learned member has no fit for are not delivered: predicting them would trigger its own
    # season-start refit in the forecast path (the scheduled training job makes that fit; until then the member is missing).
    new_rows = [r for r in rows if r.result_available_at > cutoff and (season_fit is None or r.game.season <= season_fit)]
    ids = {r.game.game_id for r in new_rows}
    # A box of a game the state already has the result of is interleaved with the newer results and forecasts by its
    # effective time, as a walk over every row delivers it (delivered ahead of them, a forecast whose cutoff precedes it
    # would use it).
    late, fresh = [], []
    for e in events:
        eff = max(e.available_at, known[e.game.game_id].result_available_at)
        if eff <= cutoff:
            continue
        if e.game.game_id in ids:
            fresh.append(e)
        else:
            late.append((eff, e))
    if new_rows or late:
        held = {e.game.game_id for _, e in late}
        backtest.run(_Delivered(w, held), new_rows + [known[g] for g in sorted(held)], fresh + [e for _, e in late],
                     deliver_until=now)
    return len(new_rows)


class _Delivered:
    """`w` as seen by a walk that also lists games whose results `w` already has (`held`): their results and forecasts
    are not passed on, so their box/shot lists join the walk's delivery queue by effective time and reach `w` exactly
    when a walk over every row would deliver them (backtest.run itself is unchanged: it is part of the research
    definition)."""

    def __init__(self, w, held):
        self.w, self.held = w, held

    def predict(self, game):
        return None if game.game_id in self.held else self.w.predict(game)

    def update(self, result):
        if result.game.game_id not in self.held:
            self.w.update(result)

    def __getattr__(self, name):          # update_box (when `w` has it), anything else the walk asks for
        return getattr(self.w, name)


def record(conn, *, game_ids: Optional[list] = None, now: Optional[str] = None, write: bool = True, replay=None) -> dict:
    """Adaptive forecasts for eligible games within two hours of the start (the exact line of the primary two-hour
    market forecast; the winner market needs none). No fit happens here. Never raises into the cycle.

    replay=<games row> (retrospective recovery only; read-only): the same evaluation for that one past game as of
    `now` (its simulated pregame cutoff), applying only fits trained on and informed by data at or before `now`, with
    lines and quotes known by then. Nothing is written; the caller stores the result in its own ledger."""
    if replay is not None and (write or now is None or now >= replay["tipoff_at"]):
        raise ValueError("a replay is read-only and needs a cutoff before the game's start")
    out = {"status": "success", "created": 0, "already_recorded": 0, "unavailable": 0, "skipped": {}, "errors": {}, "forecasts": []}
    try:
        _record(conn, game_ids, now, write, out, replay)
    except Exception as exc:
        out["status"] = "failed"
        out["error"] = f"{type(exc).__name__}: {exc}"
        if write:
            conn.rollback()
    if out["status"] == "success" and (out["errors"] or out["skipped"]):
        out["status"] = "partial"
    return out


def _record(conn, game_ids, now, write, out, replay=None) -> None:
    fixed = now is not None
    rp = replay is not None
    now = now or utc_now()
    eng = live.engine(conn)
    sport = eng.sport
    out["sport"] = sport
    defn = definition(conn)
    out["definition"] = defn
    horizon = _fmt(_parse(now) + near_tipoff.WINDOW)
    types = eng.eligible_types
    # A replay predicts past games: any game that was pending at `now` belongs to the schedule, whatever its status now.
    pending = "status IN ('scheduled', 'in_progress', 'final')" if rp else "status='scheduled'"
    eligible = [replay] if rp else [g for g in conn.execute(
        f"SELECT * FROM games WHERE status='scheduled' AND tipoff_at > ? AND tipoff_at <= ? AND season_type IN "
        f"({','.join('?' * len(types))}) ORDER BY tipoff_at, game_id", (now, horizon, *types))
        if game_ids is None or g["game_id"] in game_ids]
    out["eligible_games"] = len(eligible)
    if not eligible:
        return
    ids = {g["game_id"] for g in eligible}
    per_market, comb_reason = combiners_in_force(conn, sport, defn, now, replay=rp)
    mems, excluded = members(conn)
    passed_over: dict = {}
    arts, missing_all = _load_member_artifacts(conn, sport, defn, mems, now, replay=rp, skipped=passed_over)
    rows = available_rows(conn, now)
    if rp:
        out["results_used"] = len(rows)
        out["latest_result_at"] = max((r.result_available_at for r in rows), default=None)
    known = {r.game.game_id: r for r in rows}
    events = _events(conn, eng, rows, known, now)
    schedule = [g for g in conn.execute(
        f"SELECT * FROM games WHERE {pending} AND tipoff_at > ? AND tipoff_at <= ? AND season_type IN "
        f"({','.join('?' * len(types))}) ORDER BY tipoff_at, game_id", (now, max(g["tipoff_at"] for g in eligible), *types))
        if g["game_id"] not in known]
    # Lines and quotes per game and market, from the primary two-hour forecasts of this cycle.
    lines: dict = {}
    for g in eligible:
        for market in sports.get(sport).markets:
            if market == "winner":
                lines[(g["game_id"], market)] = {"line": None, "source": "none", "quote": None, "ref": None,
                                                 "book": research_live._winner_quote(conn, sport, g["game_id"], now)}
            elif market == "reg3":
                lines[(g["game_id"], market)] = {"line": None, "source": "none", "quote": None, "ref": None, "book": None}
                pl = _primary_line(conn, g, market, sport, now, replay=rp)
                if pl is not None:
                    lines[(g["game_id"], market)].update({"quote": pl["quote"], "ref": pl["ref"], "book": pl["book"]})
            else:
                pl = _primary_line(conn, g, market, sport, now, replay=rp)
                if pl is None:
                    out["skipped"].setdefault(market, {})[g["game_id"]] = "no primary two-hour market forecast: no line to combine at"
                    continue
                lines[(g["game_id"], market)] = pl
    # Member forecasts for every eligible game and market.
    have: dict = {}            # (game, market) -> {member: vec}
    missing: dict = {}         # (game) -> {member: reason}
    for g in eligible:
        missing[g["game_id"]] = dict(missing_all)
    # Candidates: the shared feature walk with pregame listings for the eligible games, plus staleness checks.
    cands = [m for m in mems if m.kind == "candidate" and m.name in arts]
    stale: dict = {}
    crow: dict = {}
    recent: dict = {}          # (season, team) -> final game ids, newest first (staleness checks)
    for r in sorted(rows, key=lambda r: r.game.tipoff_at, reverse=True):
        for team in (r.game.home_team_id, r.game.away_team_id):
            recent.setdefault((r.game.season, team), []).append(r.game.game_id)
    if cands or any(m.name.split("@")[0] in BOX_DEPENDENT[sport] for m in mems):
        try:
            rec = pool_live._recorder(conn, eng, ids, now)
            features.walk(rec, rows, events, deliver_until=now)
            for g in schedule:
                game = replace(eng.pregame(g), cutoff_at=now)
                rec.predict(game)
                if game.game_id in ids:
                    row = rec.rows[game.game_id]
                    row.cutoff_at = now
                    crow[game.game_id] = (game, row)
            for gid, (game, _) in crow.items():
                stale[gid] = pool_live._stale(eng, rec, game, recent)
        except Exception as exc:
            out["errors"]["feature_walk"] = f"{type(exc).__name__}: {exc}"
            for gid in ids:
                stale[gid] = f"feature walk failed: {type(exc).__name__}: {exc}"
            for m in cands:
                for gid in ids:
                    missing[gid][m.name] = f"feature walk failed: {type(exc).__name__}: {exc}"
            cands = []
    for m in cands:
        row, art = arts[m.name]
        for gid, (game, frow) in crow.items():
            if stale.get(gid):
                missing[gid][m.name] = stale[gid]
                continue
            try:
                o = engine.outputs(m.spec, art["model"], [frow], art["sd"])[0]
                if not engine.ready(o):
                    missing[gid][m.name] = "not ready: no out-of-sample residual spread yet"
                    continue
                if not pool_live.usable_goals(o):
                    missing[gid][m.name] = pool_live.DEGENERATE_GOALS
                    continue
                for market in m.markets:
                    ln = lines.get((gid, market))
                    if ln is None:
                        continue
                    v = engine.vector(o, market, ln["line"])
                    if v is not None:
                        have.setdefault((gid, market), {})[m.name] = _vec(v)
            except Exception as exc:
                for market in m.markets:
                    have.get((gid, market), {}).pop(m.name, None)
                missing[gid][m.name] = f"error: {type(exc).__name__}: {exc}"
                if rp:      # an evaluation failure is not evidence about the game: the replay is retried, never recorded
                    out["errors"][m.name] = missing[gid][m.name]
    # Walk members: deliver what arrived after the artifact's cutoff, then predict the schedule in order.
    digests: dict = {}
    for m in [m for m in mems if m.kind == "walk" and m.name in arts]:
        row, art = arts[m.name]
        try:
            w = thaw(m.obj, art["state"])
            if isinstance(w, Adaptive):
                w.frozen = True
            inner = getattr(w, "member", w)
            # Members with external inputs (the player box / injury-report cache): the inputs this process resolves must be
            # the ones the artifact was fitted on, else the member is missing with the reason until a retrain.
            check_inputs = getattr(inner, "input_mismatch", None)
            mismatch = check_inputs(art.get("inputs") or {}) if check_inputs else None
            if mismatch:
                for gid in ids:
                    missing[gid][m.name] = mismatch
                continue
            member_events = _events_for(w, eng, events)
            changed = _frozen_state_unusable(eng, w, art, rows, member_events, known, digests)
            if changed:
                for gid in ids:
                    missing[gid][m.name] = changed
                continue
            prepare_inputs = getattr(inner, "prepare_inputs", None)
            if prepare_inputs:
                prepare_inputs(conn, now)
            _advance(w, art, rows, member_events, known, now)
            check_stale = getattr(inner, "stale_inputs", None)
            for g in schedule:
                game = replace(eng.pregame(g), cutoff_at=now)
                season_fit = _season_fit(w)
                if season_fit is not None and game.season > season_fit:
                    if game.game_id in ids:
                        missing[game.game_id][m.name] = f"awaiting the scheduled season fit for {game.season} (fit in force: {season_fit})"
                    continue
                if game.game_id in ids and m.name.split("@")[0] in BOX_DEPENDENT[sport] and stale.get(game.game_id):
                    missing[game.game_id][m.name] = stale[game.game_id]
                    continue
                if game.game_id in ids and check_stale is not None:        # the member's own inputs (player box) must be current
                    reason = check_stale(game, recent, pool_live.STALE_BOX_GAMES)
                    if reason:
                        missing[game.game_id][m.name] = reason
                        continue
                p = w.predict(game)
                d = w.distribution(game) if hasattr(inner, "distribution") else None
                if game.game_id in ids:
                    if d is None and m.markets != ("winner",):
                        missing[game.game_id][m.name] = "not ready: no usable score distribution"
                        continue
                    for market in m.markets:
                        ln = lines.get((game.game_id, market))
                        if ln is None:
                            continue
                        if market == "winner":
                            have.setdefault((game.game_id, market), {})[m.name] = _vec((p, 0.0, 1 - p))
                        elif d is not None:
                            have.setdefault((game.game_id, market), {})[m.name] = _vec(d.market(market, ln["line"]))
        except Exception as exc:
            out["errors"][m.name] = f"{type(exc).__name__}: {exc}"
            for gid in ids:
                for market in m.markets:
                    have.get((gid, market), {}).pop(m.name, None)
                missing[gid][m.name] = f"error: {type(exc).__name__}: {exc}"
    # Combine, select, record.
    for g in eligible:
        gid = g["game_id"]
        for market in sports.get(sport).markets:
            ln = lines.get((gid, market))
            if ln is None:
                continue
            vecs = have.get((gid, market), {})
            cfit, comb = per_market.get(market, (None, None))
            cm = comb["markets"].get(market) if comb else None
            supports = {m.name: m.markets for m in mems}
            miss = {k: v for k, v in missing[gid].items() if market in supports.get(k, ()) and k not in vecs}
            book = ln.get("book")
            odds = "available" if book else ("unavailable: no bookmaker quote retrieved before this forecast at this line"
                                             if market != "winner" else "unavailable: no bookmaker moneyline retrieved before this forecast")
            # One market's failure is that market's error (nothing recorded for it this cycle); the others still record.
            try:
                if comb is None:
                    p, stack, names, reason = None, None, [], comb_reason
                else:
                    p, stack, names, reason = combine_now(cm, vecs)
                decision = comb["selection"].get(market) if comb else None
                item = {"vectors": {"adaptive": p} if p else {}, "agreement": {}, "bookmaker": tuple(book["p"]) if book else None, "line": ln["line"]}
                sel = selector.apply(decision, item, market) if p else {"status": "no_pick", "reason": reason, "side": None, "probability": None}
                if _CADENCE.get() == "daily":
                    from . import daily_agreement
                    sel["daily_agreement"] = {**daily_agreement.apply(
                        comb.get("daily_agreement", {}).get(market) if comb else None, vecs, market),
                        # the fit whose rule assessment applied (the newest one, also when the combination is older)
                        "rule_fit_id": (comb.get("daily_agreement_fit_id") or (cfit["fit_id"] if cfit else None)) if comb else None}
            except Exception as exc:
                out["errors"][market] = f"{gid}: {type(exc).__name__}: {exc}"
                continue
            weights = dict(zip(names, stack["weights"])) if stack else {}
            mem_rows = [{"name": n, "p": list(v), "weight": weights.get(n),
                         "fit_id": arts[n][0]["fit_id"] if n in arts else None,
                         "role": "in the combination" if n in names else "recorded, not in the combination"
                         + (f" ({cm['excluded'][n]})" if cm and n in cm.get("excluded", {}) else "")} for n, v in sorted(vecs.items())]
            generated = now if fixed else utc_now()
            if generated >= g["tipoff_at"]:
                out["skipped"].setdefault(market, {})[gid] = "calculation finished at or after the start"
                continue
            key = _key(defn, gid, market, ln["line"], ln["ref"], g["tipoff_at"], cfit["fit_id"] if cfit else None,
                       [(r["name"], r["p"], r["fit_id"]) for r in mem_rows], miss, reason)
            item_out = {"game_id": gid, "market": market, "line": ln["line"], "p": list(p) if p else None, "status": "forecast" if p else "unavailable",
                        "reason": reason, "members": names, "missing": miss, "selection": sel, "odds_status": odds, "generated_at": generated}
            if rp:      # provenance for the retrospective ledger: every artifact applied, with its digest and data times
                for r in mem_rows:
                    if r["name"] in arts:
                        frow, fart = arts[r["name"]]
                        r.update({"artifact_sha256": frow["artifact_sha256"], "training_cutoff": frow["training_cutoff"],
                                  "information_at": information_at(fart), "components": fart.get("components"),
                                  "finished_at": frow["finished_at"], "passed_over": passed_over.get(r["name"], [])})
                item_out.update({"line_source": ln["source"], "line_ref": ln["ref"], "quote_id": ln["quote"], "book": book,
                                 "member_rows": mem_rows, "combiner": dict(cfit) if cfit else None,
                                 "combiner_information_at": information_at(comb) if comb else None,
                                 "weights": {"members": names, "weights": stack["weights"], "bias": stack["bias"],
                                             "l2": stack.get("l2")} if stack else None})
            out["forecasts"].append(item_out)
            if not write:
                continue
            with transaction(conn):
                if conn.execute("SELECT 1 FROM adaptive_forecasts WHERE idempotency_key=?", (key,)).fetchone():
                    out["already_recorded"] += 1
                    continue
                cur = conn.execute("SELECT status, tipoff_at FROM games WHERE game_id=?", (gid,)).fetchone()
                if cur is None or cur["status"] != "scheduled" or cur["tipoff_at"] != g["tipoff_at"]:
                    out["skipped"].setdefault(market, {})[gid] = "game no longer scheduled at its forecast start"
                    continue
                conn.execute(
                    "INSERT INTO adaptive_forecasts (game_id, sport, market, line, line_source, line_ref, market_quote_id, bookmaker, "
                    "odds_status, model_version, definition, combiner_fit_id, status, reason, p_a, p_mid, p_b, members, missing, weights, "
                    "selection, training_cutoff, weights_updated_at, information_at, generated_at, tipoff_at, idempotency_key) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (gid, sport, market, ln["line"], ln["source"], ln["ref"], ln["quote"], json.dumps(book) if book else None, odds,
                     model_version(defn), defn, cfit["fit_id"] if cfit else None, "forecast" if p else "unavailable", reason,
                     p[0] if p else None, p[1] if p else None, p[2] if p else None, json.dumps(mem_rows), json.dumps(miss),
                     json.dumps({"members": names, "weights": stack["weights"], "bias": stack["bias"], "l2": stack.get("l2")} if stack else None),
                     json.dumps(sel), (cm or {}).get("training", {}).get("results_through") if cm else None,
                     cfit["finished_at"] if cfit else None, now, generated, g["tipoff_at"], key))
            out["created" if p else "unavailable"] += 1


COMBINER_LOOKBACK = 6


def combiners_in_force(conn, sport, defn, now, replay: bool = False) -> tuple[dict, Optional[str]]:
    """market -> (fit row, loaded combiner artifact) to apply now: the latest usable combiner (success or partial) for
    every market it has a combination for; a market it lacks keeps the combination of the most recent earlier usable
    fit that has one (within COMBINER_LOOKBACK fits); older fits are loaded only while some market still lacks one.
    Returns ({}, reason) when nothing usable exists. Each artifact is
    verified against its own row's digest before use. replay=True (retrospective only): fits are ordered by training
    cutoff and only those trained on results, and informed, at or before `now` qualify, whenever they were published."""
    if not _has(conn, "adaptive_fits"):
        return {}, "schema not migrated"
    rows = (_replay_fits(conn, sport, "combiner", "combiner", defn, now, COMBINER_LOOKBACK) if replay else
            conn.execute(f"SELECT * FROM adaptive_fits WHERE sport=? AND kind='combiner' AND definition=? AND status IN "
                         f"({','.join('?' * len(IN_FORCE))}) AND finished_at<=? ORDER BY finished_at DESC, fit_id DESC LIMIT ?",
                         (sport, defn, *IN_FORCE, now, COMBINER_LOOKBACK)).fetchall())
    if not rows:
        return {}, ("no combiner fit for this definition trained only on results before this game's cutoff" if replay
                    else "no combiner fit for this definition yet (adaptive-train has not produced one)")
    out, loaded, problems = {}, [], []
    required = set(sports.get(sport).markets)
    for row in rows:
        if required <= out.keys():      # every market already has a combination: older fits are not needed, not loaded
            break
        try:
            comb = load_fit(row)
        except Exception as exc:
            if replay:      # unreadable is not evidence: retry rather than fall back or record 'unreconstructable'
                raise ReplayUnavailable(f"combiner fit {row['fit_id']}: artifact unavailable: {type(exc).__name__}: {exc}") from exc
            problems.append(f"fit {row['fit_id']}: {type(exc).__name__}: {exc}")
            continue
        if replay and not _informed_by(comb, now):
            problems.append(f"fit {row['fit_id']}: information time {information_at(comb)} is after this game's cutoff")
            continue
        loaded.append((row, comb))
        for market, cm in comb["markets"].items():
            if market not in out and cm.get("stack") is not None:
                out[market] = (row, comb)
    if not loaded:
        return {}, "combiner artifact unavailable: " + "; ".join(problems)
    for market, cm in loaded[0][1]["markets"].items():     # no usable combination anywhere: the latest fit's reason applies
        out.setdefault(market, loaded[0])
    # The daily agreement rule does not depend on the stack: a market whose combination comes from an earlier fit still
    # applies the NEWEST fit's assessment of its rule (a decision carried after a failed fit is that earlier rule anyway).
    newest_row, newest = loaded[0]
    for market, (row, comb) in list(out.items()):
        decision = (newest.get("daily_agreement") or {}).get(market)
        if comb is not newest and decision is not None:
            out[market] = (row, {**comb, "daily_agreement": {**(comb.get("daily_agreement") or {}), market: decision},
                                 "daily_agreement_fit_id": newest_row["fit_id"]})
    return out, None


# --- Reads (API) -------------------------------------------------------------------------------------------------------
def game_detail(conn, game_id: str, as_of: Optional[str] = None) -> dict:
    """Latest adaptive forecast per market for one game, as known at `as_of`."""
    as_of = as_of or utc_now()
    if not _has(conn, "adaptive_forecasts"):
        return {"game_id": game_id, "version": VERSION, "markets": [], "status": "no_adaptive_forecast"}
    g = conn.execute("SELECT * FROM games WHERE game_id=?", (game_id,)).fetchone()
    if g is None:
        return {"game_id": game_id, "version": VERSION, "markets": [], "status": "unknown_game"}
    abbr = {t["team_id"]: t["abbr"] for t in conn.execute("SELECT team_id, abbr FROM teams WHERE team_id IN (?, ?)",
                                                            (g["home_team_id"], g["away_team_id"]))}
    # One cadence per view: daily rows (recorded by the same two-hour job with NBA_DAILY_AGREEMENT=1, same generated_at,
    # higher forecast_id) always carry `selection.daily_agreement`; weekly rows never do. Both the outer filter and the
    # latest-per-market choice are scoped, so a daily row can never win the weekly view or the reverse.
    scope = "json_type({}.selection, '$.daily_agreement') IS " + ("NOT NULL" if _CADENCE.get() == "daily" else "NULL")
    out = []
    for r in conn.execute(
            f"SELECT a.* FROM adaptive_forecasts a WHERE a.game_id=? AND a.generated_at<=? AND {scope.format('a')} "
            f"AND a.forecast_id=(SELECT x.forecast_id FROM adaptive_forecasts x WHERE x.game_id=a.game_id AND x.market=a.market "
            f"AND x.generated_at<=? AND {scope.format('x')} ORDER BY x.generated_at DESC, x.forecast_id DESC LIMIT 1) "
            "ORDER BY a.market", (game_id, as_of, as_of)):
        labels = markets.side_labels(r["market"], r["line"], abbr.get(g["home_team_id"], "home"), abbr.get(g["away_team_id"], "away"))
        sel = json.loads(r["selection"])
        mems_ = json.loads(r["members"])
        out.append({"market": r["market"], "line": r["line"], "line_source": r["line_source"],
                    "line_label": "bookmaker line" if r["line_source"] == "market" else
                    "model-only line (no bookmaker line retrieved)" if r["line_source"] == "model" else None,
                    "sides": {"a": labels[0], "mid": labels[1] or None, "b": labels[2]}, "status": r["status"], "reason": r["reason"],
                    "p": None if r["p_a"] is None else [r["p_a"], r["p_mid"], r["p_b"]],
                    "members": mems_, "missing": json.loads(r["missing"]), "weights": json.loads(r["weights"]),
                    "contributing": [m["name"] for m in mems_ if m["role"] == "in the combination"],
                    "bookmaker": json.loads(r["bookmaker"]) if r["bookmaker"] else None, "odds_status": r["odds_status"],
                    "edge": None if r["p_a"] is None or not r["bookmaker"] else
                    [round(p - b, 4) for p, b in zip((r["p_a"], r["p_mid"], r["p_b"]), json.loads(r["bookmaker"])["p"])],
                    "selection": {**sel, "label": labels[SIDE[sel["side"]]] if sel.get("side") else None},
                    "training_cutoff": r["training_cutoff"], "weights_updated_at": r["weights_updated_at"],
                    "combiner_fit_id": r["combiner_fit_id"], "definition": r["definition"], "model_version": r["model_version"],
                    "generated_at": r["generated_at"], "information_at": r["information_at"], "tipoff_at": r["tipoff_at"],
                    "minutes_before_start": research_live._minutes(r["tipoff_at"], r["generated_at"]),
                    "start_changed": g["tipoff_at"] != r["tipoff_at"]})
    return {"game_id": game_id, "version": VERSION, "kind": "adaptive research", "markets": out,
            "status": "recorded" if out else "no_adaptive_forecast",
            "note": f"{VERSION}: separately versioned adaptive combination (within-season refits, learned weights). Research only; "
                    "the primary forecast is unchanged. Picks follow the research selector's unchanged evidence thresholds."}


def status(conn, as_of: Optional[str] = None) -> dict:
    """Fits in force, weights, exclusions, schedule, due state, recent attempts and the prospective record."""
    as_of = as_of or utc_now()
    sport = live.engine(conn).sport
    try:
        defn = definition(conn)
        defn_error = None
    except Exception as exc:
        defn, defn_error = None, f"{type(exc).__name__}: {exc}"
    out = {"version": VERSION, "sport": sport, "as_of": as_of, "definition": defn, "definition_error": defn_error,
           "schedule": SCHEDULE, "enabled": enabled(), "combiner": None, "members": [], "attempts": [], "prospective": {},
           "replay": replay_summary(sport),
           "note": "Research only. Within-season refits and learned combination weights, separately versioned from the primary "
                   "system; nothing here replaces the primary forecast. No profit claim; odds comparisons are shown only where a "
                   "quote at the same line was retrieved before the forecast."}
    if defn is None or not _has(conn, "adaptive_fits"):
        out["due"] = {"due": False, "reason": defn_error or "schema not migrated"}
        return out
    try:
        out["due"] = due(conn, as_of)
    except Exception as exc:
        out["due"] = {"due": False, "reason": f"{type(exc).__name__}: {exc}"}
    c = latest_fit(conn, sport, "combiner", "combiner", defn, as_of)
    fail = latest_fit(conn, sport, "combiner", "combiner", defn, as_of, status="failed")
    if c is not None:
        d = json.loads(c["detail"])
        per_market, _ = combiners_in_force(conn, sport, defn, as_of)
        out["combiner"] = {"fit_id": c["fit_id"], "period": c["period"], "status": c["status"], "partial_reason": d.get("partial_reason"),
                           "training_cutoff": c["training_cutoff"], "weights_updated_at": c["finished_at"],
                           "artifact_sha256": c["artifact_sha256"], "markets": d["markets"], "selection": d["selection"],
                           "excluded": d["excluded"], "members_available": d["members_available"],
                           "market_fit_ids": {mk: r["fit_id"] for mk, (r, _) in per_market.items()}}
    out["last_failure"] = None if fail is None or (c is not None and fail["finished_at"] < c["finished_at"]) else \
        {"at": fail["finished_at"], "period": fail["period"], "error": json.loads(fail["detail"]).get("error")}
    names = {r["name"] for r in conn.execute("SELECT DISTINCT name FROM adaptive_fits WHERE sport=? AND kind='member' AND definition=?",
                                             (sport, defn))}
    for name in sorted(names):
        ok = latest_fit(conn, sport, "member", name, defn, as_of)
        bad = latest_fit(conn, sport, "member", name, defn, as_of, status="failed")
        out["members"].append({"name": name, "fit_in_force": None if ok is None else {
            "fit_id": ok["fit_id"], "period": ok["period"], "training_cutoff": ok["training_cutoff"],
            "training_games": ok["training_games"], "since": ok["finished_at"], "detail": json.loads(ok["detail"])},
            "last_failure": None if bad is None or (ok is not None and bad["finished_at"] < ok["finished_at"]) else
            {"at": bad["finished_at"], "error": json.loads(bad["detail"]).get("error")}})
    out["attempts"] = [dict(r) | {"detail": json.loads(r["detail"])} for r in conn.execute(
        "SELECT fit_id, kind, name, period, status, training_cutoff, training_games, started_at, finished_at, detail FROM adaptive_fits "
        "WHERE sport=? AND definition=? AND finished_at<=? ORDER BY fit_id DESC LIMIT 40", (sport, defn, as_of))]
    out["prospective"] = prospective_record(conn, sport, defn, as_of)
    return out


def prospective_record(conn, sport, defn, as_of) -> dict:
    """Settled adaptive forecasts per market: the adaptive combination, the primary member on the same games, and the
    bookmaker where a quote at the same line was retrieved before the forecast. Descriptive; small samples."""
    from . import metrics
    primary = {"winner": ONLINE[sport][0]}
    rows = prospective_rows(conn, sport, defn, as_of)
    per: dict = {}
    for i in rows:
        per.setdefault(i["market"], []).append(i)
    out = {}
    for market, items in per.items():
        prim = primary.get(market, LINE_PRIMARY[sport])
        vec = [i["adaptive"] for i in items]
        real = [i["realised"] for i in items]
        res = [selector.outcome(market, s, y) for v, y in zip(vec, real) if (s := combine.pick(v, market)) is not None]
        same = [i for i in items if prim in i["vectors"]]
        book = [i for i in items if i["bookmaker"]]
        out[market] = {"settled": len(items), "log_loss": metrics.log_loss_multi(vec, real), "brier": metrics.brier_multi(vec, real),
                       "record": research_live._record(res),
                       "primary": {"name": prim, "games": len(same),
                                   "adaptive_log_loss": metrics.log_loss_multi([i["adaptive"] for i in same], [i["realised"] for i in same]),
                                   "primary_log_loss": metrics.log_loss_multi([i["vectors"][prim] for i in same], [i["realised"] for i in same])},
                       "bookmaker": {"games": len(book), "coverage": len(book) / len(items) if items else None,
                                     "adaptive_log_loss": metrics.log_loss_multi([i["adaptive"] for i in book], [i["realised"] for i in book]),
                                     "bookmaker_log_loss": metrics.log_loss_multi([i["bookmaker"] for i in book], [i["realised"] for i in book])}}
    pending = 0
    if _has(conn, "adaptive_forecasts"):
        pending = conn.execute("SELECT COUNT(DISTINCT game_id || market) FROM adaptive_forecasts a WHERE sport=? AND definition=? "
                               "AND status='forecast' AND generated_at<=? AND (SELECT status FROM games WHERE game_id=a.game_id) "
                               "IN ('scheduled', 'in_progress')", (sport, defn, as_of)).fetchone()[0]
    return {"markets": out, "pending": pending,
            "note": "Prospective two-hour adaptive forecasts only (this definition). Log loss on identical games; no profit claim."}


EVIDENCE_DIR = Path(__file__).resolve().parents[2] / "docs" / "evidence" / "adaptive"


def replay_summary(sport: str) -> Optional[dict]:
    p = EVIDENCE_DIR / f"{sport}-replay.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except ValueError:
        return None
