"""Historical starting policy (historical-start-v1). Research only, separately labelled; never the primary forecast, never
a core or pool selector decision, and never counted in their records.

The live selector starts with no evidence and needs prospective settled cohorts (selector.MIN_PICKS, MIN_WEEKS) before it
can pick anything. This policy gives a starting rule from history instead:
  1. Walk-forward (chronological, out-of-sample) forecasts of the SAME member models and configurations the live research
     cohorts use (agreement.models, research_live.line_models), on the already-used historical seasons (NBA validation,
     NHL development) — never the reserved test seasons. Line markets use the primary's model-only line (no historical
     bookmaker lines exist).
  2. Sources: each member alone, plus the equal average and the stacker wherever they can be formed. A game without a
     combination is still an item for the single members (selector: "source unavailable" for that combination only).
  3. Judged on the selector's "historical" basis: the outcome base rate of the picked side at the same line, never odds
     and never a flat 50%. Agreement filters are offered only when the historical voters equal the live voters.
  4. A weekly chronological check (each week decided on earlier games only, `combination_research._simulate`) shows how
     the procedure would have done; then one final decision on all historical games is frozen with the research
     definition, selector version and dataset version. "No rule qualified" is an explicit, valid outcome.
The frozen file is written once (refuses to overwrite) and applied at read time only to research cohorts of the same
definition generated after it was frozen. Its live record is reported apart from the live selector, and the bookmaker
comparison of its picks is reported apart from its win/loss record. The seasons were already used to choose the models:
the chronological check is exploratory, not held-out evidence.
"""
import json
import os
from pathlib import Path
from typing import Callable, Optional

from . import backtest, combine, combination_research as cr, config, research_live, selector, sports
from .db import utc_now

POLICY = "historical-start-v2"     # v2: stacker refit as live; unsupported lines not judged (v1 files are kept)
LABEL = ("Historical starting policy: chosen on earlier seasons' walk-forward forecasts against the outcome base rate "
         "(no historical odds). Not live evidence and not a held-out test.")


def policy_dir() -> Path:
    return config.data_dir() / "research" / "historical-start"


def path(sport: str, definition: str) -> Path:
    return policy_dir() / f"{sport}-{definition}-{selector.version().split(':')[1]}-{POLICY}.json"


def _history(conn, sport: str):
    """(tables, first OOS season per member, dataset version, member names per kind, evaluated seasons), using the live
    research members' own classes and configurations."""
    from . import agreement, nhl_eval
    winner_models = [m for m in agreement.models(conn) if m.name in research_live.CORE[sport]]
    line_models = [m for m in research_live.line_models(conn) if m.name in research_live.LINE_MEMBERS[sport]]
    markets_ = tuple(m for m in sports.get(sport).markets if m != "winner")
    if sport == "nba":
        rows, events = backtest.load_rows(conn, "validation"), backtest.load_box_events(conn, "validation")
        allowed, warmup, seasons, decision_of = backtest.LOADABLE["validation"], backtest.PERIODS["warmup"], cr.NBA_EVAL, None
        version = backtest.dataset_version(rows)
    else:
        rows, events = nhl_eval.load_rows(conn, "development"), None
        allowed, warmup, seasons = nhl_eval.LOADABLE["development"], nhl_eval.PERIODS["warmup"], cr.NHL_EVAL
        decision_of, version = (lambda r: r.decision), nhl_eval.dataset_version(rows)
    if {r.game.season for r in rows} - set(allowed):
        raise ValueError("historical start read seasons outside the already-used historical period")
    tables, first = cr._tables(rows, winner_models, line_models, markets_, events, decision_of)
    for m in winner_models + line_models:
        if first.get(m.name) is None:          # rating models are out of sample from the first season after warm-up
            first[m.name] = min(warmup)
    return tables, first, version, {"winner": [m.name for m in winner_models], "lines": [m.name for m in line_models]}, seasons


def _sources(sport: str, market: str, members: list) -> tuple[list, tuple, dict]:
    """(sources, agreement filters, excluded -> reason): only sources whose live meaning equals the historical one."""
    live_sources = research_live.sources(sport, market)
    combo_members = list(research_live.CORE[sport] if market == "winner" else research_live.LINE_MEMBERS[sport])
    excluded = {}
    sources = [m for m in members if m in live_sources]
    for s in live_sources:
        if s in ("average", "stack"):
            if set(members) == set(combo_members):
                sources.append(s)
            else:
                excluded[s] = "live combination uses other members than the history"
        elif s not in members:
            excluded[s] = "no historical walk-forward forecasts for this member"
    voters = list(research_live.expected(sport, market))
    filters = selector.FILTERS if set(members) == set(voters) else ("none",)
    if filters == ("none",):
        excluded["majority/unanimous filters"] = (f"live agreement counts {len(voters)} voters, history has {len(members)}: "
                                                  "agreement filters would mean something else")
    return sources, filters, excluded


def _live_stack_walk(market: str, settled: list, complete: list, members: list, seasons) -> list:
    """The stacker exactly as the live cohorts fit it (research_live._winner_stack / _line_stacks): at each season start,
    on the members' walk-forward forecasts of the last STACK_SEASONS completed seasons, at least STACK_MIN_TRAIN games
    with every member present; otherwise no stack that season. Adds 'stack' to the target season's complete rows."""
    fits = []
    for s in seasons:
        train_seasons = sorted({r["season"] for r in settled if r["season"] < s})[-research_live.STACK_SEASONS:]
        train = [r for r in complete if r["season"] in train_seasons]
        target = [r for r in complete if r["season"] == s]
        if len(train) < research_live.STACK_MIN_TRAIN or not target:
            fits.append({"season": s, "fitted": False, "training_games": len(train), "training_seasons": train_seasons})
            continue
        if any(r["result_available_at"] > min(t["cutoff_at"] for t in target) for r in train):
            raise ValueError("stacker training result not available before its season")
        model = combine.fit_stack([([r["vectors"][m] for m in members], r["realised"]) for r in train], market)
        for r in target:
            r["vectors"]["stack"] = combine.stack(model, [r["vectors"][m] for m in members])
        fits.append({"season": s, "fitted": True, "training_games": model["n"], "training_seasons": train_seasons})
    return fits


def _market(market: str, table: list, members: list, first: dict, seasons, sport: str) -> dict:
    sources, filters, excluded = _sources(sport, market, members)
    settled = [r for r in table if r["realised"] is not None]
    for r in settled:
        if all(m in r["vectors"] for m in members):
            r["vectors"]["average"] = combine.average([r["vectors"][m] for m in members])
    complete = [r for r in settled if all(m in r["vectors"] for m in members)]
    fits = _live_stack_walk(market, settled, complete, members, list(seasons)) if "stack" in sources else []
    rows = [r for r in settled if r["season"] in seasons]
    if not rows:
        return {"status": "insufficient_evidence", "rule": None, "reason": "no settled historical games", "sources": sources,
                "filters": list(filters), "excluded": excluded, "games": 0}
    sim, picks = cr._simulate(market, rows, sources, members, None, filters)
    decided = [x for x in picks if x["result"] != "push"]
    items = cr.history_items(market, rows, members)
    incumbent = next((d["rule"] for d in reversed(sim["decisions"]) if d["rule"]), None)
    d = selector.decide(items, market, sources, incumbent, "historical", filters)
    top = sorted([s for s in d["stats"] if s["lower"] is not None], key=lambda s: -s["lower"])[:5]
    return {"status": d["status"], "rule": d["rule"], "lower": d.get("lower"), "lift": d.get("lift"), "reason": d["reason"],
            "sources": sources, "filters": list(filters), "excluded": excluded, "games": len(rows),
            "seasons": sorted({r["season"] for r in rows}), "from": rows[0]["tipoff_at"][:10], "until": rows[-1]["tipoff_at"][:10],
            "stack_fits": fits, "stack_rule": (f"season start, last {research_live.STACK_SEASONS} completed seasons, "
                                               f"≥ {research_live.STACK_MIN_TRAIN} games (as the live cohorts)"),
            "rules_compared": d["rules"], "eligible_rules": d["eligible_rules"],
            "top_rules": [{k: s[k] for k in ("source", "filter", "threshold", "picks", "coverage", "weeks", "accuracy",
                                              "lift", "lower", "eligible")} for s in top],
            "chronological_check": {**{k: v for k, v in sim["summary"].items()
                                       if k not in ("primary_confidence_same_count", "best_single_confidence_same_count")},
                                    # Calibration of the picks: a high win rate on confident favourites is expected, and
                                    # beats the side's base rate without any skill beyond the model's own probability.
                                    "mean_probability": sum(x["p"] for x in decided) / len(decided) if decided else None,
                                    "mean_reference": (lambda j: sum(j) / len(j) if j else None)(
                                        [x["reference"] for x in decided if x["reference"] is not None])}}


def build(conn, sport: str, log: Callable[[str], None] = print) -> dict:
    """Builds and freezes the policy for the store's current research definition. Refuses to replace a frozen one."""
    defn = research_live.definition(conn)
    out = path(sport, defn)
    if out.exists():
        raise ValueError(f"{out.name} exists: a historical starting policy is frozen once per definition and selector")
    started = utc_now()
    tables, first, version, members, seasons = _history(conn, sport)
    doc = {"policy": POLICY, "label": LABEL, "sport": sport, "definition": defn, "selector_version": selector.version(),
           "dataset_version": version, "evaluated_seasons": list(seasons), "reference": cr.HISTORICAL_REFERENCE,
           "members": members, "first_out_of_sample_season": first, "markets": {}}
    for market, table in tables.items():
        mem = members["winner"] if market == "winner" else members["lines"]
        doc["markets"][market] = _market(market, table, mem, first, seasons, sport)
        m = doc["markets"][market]
        log(f"{sport} {market}: {m['games']} historical games, {m['status']}: {m['reason']}")
    doc["frozen_at"] = utc_now()
    doc["started_at"] = started
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1, default=str))
    os.link(tmp, out)              # fails if another build froze one meanwhile: never overwritten
    tmp.unlink()
    doc["path"] = str(out)
    return doc


def load(sport: str, definition: Optional[str] = None) -> Optional[dict]:
    """The frozen policy for `definition` under the current selector, else the newest frozen one for the sport."""
    if definition:
        p = path(sport, definition)
        if p.is_file():
            return json.loads(p.read_text())
    found = [json.loads(p.read_text()) for p in sorted(policy_dir().glob(f"{sport}-*.json"))]
    return max(found, key=lambda d: d["frozen_at"]) if found else None


def _decision(m: dict) -> dict:
    return {"status": m["status"], "rule": m["rule"], "lower": m.get("lower"), "reason": m["reason"]}


def live_record(conn, doc: dict, market: str, as_of: str) -> dict:
    """The frozen rule applied to research cohorts of the policy's definition made after it was frozen. Read-time only:
    cohorts are append-only and made before the start, so nothing is backdated. Win/loss record and the bookmaker's
    view of the same picks are reported separately."""
    m = doc["markets"].get(market)
    if m is None:
        return {"cohorts": 0, "picks": 0}
    dec = _decision(m)
    wins = losses = pushes = pending = void = unscorable = before = after = 0
    implied, recent = [], []
    for r in research_live.canonical_cohorts(conn, market, doc["definition"], as_of=as_of):
        if r["generated_at"] < doc["frozen_at"]:
            before += 1
            continue
        after += 1
        it = research_live.item(r)
        s = selector.apply(dec, it, market)
        if s["status"] != "pick":
            continue
        side = combine.SIDES.index(s["side"])
        st = research_live.state(r, as_of)
        res = None
        if st == "void":
            void += 1
        elif st == "pending":
            pending += 1
        else:
            y = research_live._realised(market, r["line"], r, r["decision"])
            if y is None:
                unscorable += 1
            else:
                res = selector.outcome(market, side, y)
                wins += res == "win"
                losses += res == "loss"
                pushes += res == "push"
                if it["bookmaker"] is not None and res != "push":
                    implied.append((selector.reference(it, side, {}, market, "bookmaker"), res == "win"))
        recent.append({"game_id": r["game_id"], "tipoff_at": r["tipoff_at"], "line": r["line"], "side": s["side"],
                       "probability": s["probability"], "result": res or st})
    return {"cohorts_after_freeze": after, "picks": len(recent), "wins": wins, "losses": losses,
            "pushes": pushes, "pending": pending, "void": void, "unscorable": unscorable,
            "accuracy": wins / (wins + losses) if wins + losses else None, "cohorts_before_freeze": before,
            "bookmaker": {"quoted_decided": len(implied), "quoted_wins": sum(w for _, w in implied),
                          "mean_implied": sum(p for p, _ in implied) / len(implied) if implied else None,
                          "note": "The bookmaker's de-vigged probability of the same picks, on quoted decided games only. "
                                  "Shown beside the record, never mixed into it; no profit claim."},
            "recent": recent[-20:][::-1]}


def report(conn, sport: str, market: str, as_of: Optional[str] = None) -> dict:
    as_of = as_of or utc_now()
    current = research_live.definition(conn)
    doc = load(sport, current)
    base = {"sport": sport, "market": market, "policy": POLICY, "label": LABEL, "current_definition": current}
    if doc is None:
        return {**base, "status": "not_built",
                "note": "No historical starting policy has been frozen for this sport. Build: "
                        f"python -m app.cli --sport {sport} historical-start"}
    m = doc["markets"].get(market)
    return {**base, "status": "frozen", "frozen_policy": doc["policy"], "matches_current_policy": doc["policy"] == POLICY,
            "definition": doc["definition"], "matches_current_definition": doc["definition"] == current,
            "frozen_at": doc["frozen_at"], "selector_version": doc["selector_version"],
            "matches_current_selector": doc["selector_version"] == selector.version(),
            "dataset_version": doc["dataset_version"], "evaluated_seasons": doc["evaluated_seasons"],
            "reference": doc["reference"], "market": market, "decision": m,
            "live": live_record(conn, doc, market, as_of) if m else None,
            "note": ("Applied only to two-hour research cohorts of the definition it was frozen with, made after it was "
                     "frozen. " + ("" if doc["definition"] == current else
                                   "The current research definition differs (model code changed): this policy describes the "
                                   "earlier definition and no current cohort is counted; build a new one for the current "
                                   "definition."))}
