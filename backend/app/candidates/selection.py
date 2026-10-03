"""Candidate-pool selection (docs/CANDIDATE_POOL.md §7). Research only; nothing here changes a primary forecast, a core
cohort, a core (v1) decision or selection.

A sealed registry (`pool_rules.json`, frozen before any prospective pool data) names the extra selector sources per sport
and market. A candidate becomes a source by ONE of two routes, judged once on the stage-2 held-out evidence:
  standalone   — alone it beats the strongest existing member (log-loss difference, 95% upper bound below zero);
  combination  — inside an equal average it adds value: the same average with versus without it (week-block bootstrap,
                 95% upper bound below zero, chronological held-out seasons), and that average beats the strongest member.
A candidate that qualifies only through a combination is a source ONLY as that combination ("avg:..."), never alone.
Candidates with neither stay research-only (recorded and shown, never sources).

Weekly pool decisions run the unchanged selector arithmetic (`selector.decide`: minimum picks, coverage and weeks,
Bonferroni lower bound over every rule, hysteresis) on the core cohorts settled before the decision time, with each pool
source's vector joined from `candidate_forecasts` made for that exact cohort (same game, market, start, line and line
reference, pinned candidate identity, before the start). A missing vector is "source unavailable" — never a substitute.
Decisions and selections go to their own tables (migration 0020), so the core selector is untouched.
"""
import hashlib
import json
from datetime import timedelta
from pathlib import Path
from typing import Optional

from .. import combine, live as core_live, research_live, selector, sports
from ..db import transaction, utc_now
from . import live as pool_live, registry

REGISTRY_PATH = Path(__file__).with_name("pool_rules.json")
EVIDENCE = "docs/evidence/candidate-pool/{sport}-assess.json"
STANDALONE = "alone vs strongest member"


class RegistryError(ValueError):
    pass


# --- Sealed registry ----------------------------------------------------------------------------------------------
def seal(reg: dict) -> dict:
    body = {k: v for k, v in reg.items() if k != "sha256"}
    return {**body, "sha256": hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()}


def load_registry(path: Optional[Path] = None) -> dict:
    p = path or REGISTRY_PATH
    if not p.is_file():
        raise RegistryError(f"pool registry missing: {p.name}")
    reg = json.loads(p.read_text())
    if seal(reg)["sha256"] != reg.get("sha256"):
        raise RegistryError("pool registry does not match its seal")
    return reg


def _members(combination: str) -> list:
    """'avg:goals_draw+poisson_draw:nhl_base vs goals_draw' -> ['goals_draw', 'poisson_draw:nhl_base']."""
    left = combination.split(" vs ")[0]
    if not left.startswith("avg:"):
        raise RegistryError(f"only equal averages can be registered: {combination}")
    return left[4:].split("+")          # '+' never appears in a member or candidate name


def build_registry(effective_from: str, assess: Optional[dict] = None) -> dict:
    """Every route-qualified source per sport and market from the stage-2 (held-out) evidence. `assess`: sport -> report
    (default: the committed evidence files)."""
    sports_out, definitions = {}, {}
    for sport in ("nba", "nhl"):
        rep = (assess or {}).get(sport) or pool_live._evidence(sport)["assess"]
        if rep is None:
            raise RegistryError(f"no stage-2 evidence for {sport}")
        per_market = {}
        for market, am in sorted((rep.get("markets") or {}).items()):
            strongest = am.get("strongest_existing")
            combos = {(c["combination"].split(" (ablation")[0], c["combination"].split("remove ")[-1].rstrip(")")): (i, c)
                      for i, c in enumerate(am.get("combinations") or []) if "(ablation: remove " in c["combination"]}
            entries = {}
            for spec in registry.for_sport(sport):
                if spec.role == "reference" or market not in spec.markets:
                    continue
                gains = pool_live.held_out_gains(am, spec.name)
                if not gains:
                    continue
                definitions[spec.name] = registry.definition(spec)
                where = EVIDENCE.format(sport=sport)
                if STANDALONE in gains:
                    c = am["candidates"][spec.name]
                    entries[f"cand:{spec.name}"] = {
                        "source": f"cand:{spec.name}", "kind": "single", "members": [f"cand:{spec.name}"],
                        "candidates": [spec.name], "route": "standalone",
                        "evidence": {"file": where, "key": f"markets.{market}.candidates[{spec.name!r}].minus_strongest",
                                     "comparison": f"{spec.name} minus {strongest} (log loss)", "seasons": am.get("seasons"),
                                     **{k: c["minus_strongest"].get(k) for k in ("mean", "ci95", "games", "weeks")}}}
                for g in gains:
                    if g == STANDALONE:
                        continue
                    i, combo = combos[(g, spec.name)]
                    names = _members(g)
                    source = "avg:" + "+".join(f"cand:{n}" if n in registry.BY_NAME else n for n in names)
                    ent = entries.setdefault(source, {
                        "source": source, "kind": "average",
                        "members": [f"cand:{n}" if n in registry.BY_NAME else n for n in names],
                        "candidates": [n for n in names if n in registry.BY_NAME], "route": "combination",
                        "evidence": {"file": where, "key": f"markets.{market}.combinations[{i}].with_minus_without",
                                     "comparison": combo["combination"], "seasons": am.get("seasons"),
                                     "log_loss_with": combo["log_loss_with"],
                                     "strongest_log_loss": am["existing"][strongest]["log_loss"],
                                     **{k: combo["with_minus_without"].get(k) for k in ("mean", "ci95", "games", "weeks")}}})
                    ent.setdefault("qualified_by", [])
                    if spec.name not in ent["qualified_by"]:
                        ent["qualified_by"].append(spec.name)
            if entries:
                per_market[market] = sorted(entries.values(), key=lambda e: (e["kind"] != "single", e["source"]))
        sports_out[sport] = per_market
    for s in sports_out.values():
        for entries in s.values():
            for e in entries:
                for n in e["candidates"]:
                    definitions.setdefault(n, registry.definition(registry.BY_NAME[n]))
    return seal({
        "registry": "pool-v1", "effective_from": effective_from, "selector": selector.SELECTOR_NAME,
        "routes": {"standalone": "alone beats the strongest existing member: 95% upper bound of the log-loss difference < 0",
                   "combination": "the equal average with minus without the candidate: 95% upper bound < 0 (week-block "
                                  "bootstrap, held-out seasons), and the average beats the strongest member"},
        "rule": "Registry membership only makes a source selectable; selection needs the selector's own evidence "
                "thresholds on settled prospective cohorts made before the decision time. A candidate qualifying only "
                "inside a combination is never a source alone.",
        "definitions": dict(sorted(definitions.items())), "sports": sports_out})


def entries(reg: dict, sport: str, market: str) -> list:
    return list((reg.get("sports", {}).get(sport) or {}).get(market) or [])


def pool_sources(reg: dict, sport: str, market: str) -> list:
    return [e["source"] for e in entries(reg, sport, market)]


def all_sources(reg: dict, sport: str, market: str) -> list:
    """Core sources first (the core registry order and tie-break), then the pool's."""
    return research_live.sources(sport, market) + pool_sources(reg, sport, market)


def registry_id(reg: dict) -> str:
    return reg["sha256"][:16]


# --- Vectors for one core cohort ----------------------------------------------------------------------------------
def pool_vectors(conn, reg: dict, sport: str, cohort, as_of: str) -> tuple[dict, dict]:
    """(source -> vector, source -> forecast ids used) for one core cohort row. A candidate vector is joined only from a
    candidate forecast made for this exact cohort: same game, market and start; the same line (none for winner/reg3) and
    `line_ref` naming this cohort; the registry's pinned candidate identity; made at or after the cohort and before the
    start, and known at `as_of`. Anything else leaves the source absent (no fallback). Winner/reg3 forecasts name their
    cohort too, so a later cycle's cohort is never joined with an earlier, de-duplicated forecast (review finding 1)."""
    ents = entries(reg, sport, cohort["market"])
    if not ents:
        return {}, {}
    cand = {}
    for name in sorted({n for e in ents for n in e["candidates"]}):
        mv = f"{pool_live.PREFIX}:{name}:{reg['definitions'][name]}"
        sql = ("SELECT forecast_id, p_a, p_mid, p_b FROM candidate_forecasts WHERE game_id=? AND market=? AND candidate=? "
               "AND model_version=? AND tipoff_at=? AND generated_at<tipoff_at AND generated_at>=? AND generated_at<=?")
        params = [cohort["game_id"], cohort["market"], name, mv, cohort["tipoff_at"], cohort["generated_at"], as_of]
        # Every market: the forecast must name this cohort; line markets also the same line (winner/reg3: none).
        sql += " AND line IS ? AND line_ref=?"
        params += [cohort["line"], f"research_cohorts:{cohort['cohort_id']}"]
        r = conn.execute(sql + " ORDER BY generated_at DESC, forecast_id DESC LIMIT 1", params).fetchone()
        if r is not None:
            cand[f"cand:{name}"] = ((r["p_a"], r["p_mid"], r["p_b"]), r["forecast_id"])
    core = research_live.item(cohort)["vectors"]
    vectors, used = {}, {}
    for e in ents:
        parts = [cand[m][0] if m.startswith("cand:") and m in cand else None if m.startswith("cand:") else core.get(m)
                 for m in e["members"]]
        if any(p is None for p in parts):
            continue
        vectors[e["source"]] = tuple(parts[0]) if e["kind"] == "single" else tuple(combine.average(parts))
        used[e["source"]] = [cand[m][1] for m in e["members"] if m in cand]
    return vectors, used


def _item(conn, reg, sport, row, as_of, realised=None) -> tuple[dict, dict]:
    it = research_live.item(row, realised)
    vec, used = pool_vectors(conn, reg, sport, row, as_of)
    it["vectors"].update(vec)
    return it, used


def evidence(conn, reg: dict, sport: str, market: str, defn: str, cutoff: str) -> list:
    """As research_live.evidence (canonical core cohorts settled before `cutoff`), with pool vectors known at `cutoff`.
    Only cohorts made after the registry took effect count: the registry is frozen before the evidence it is judged on."""
    out = []
    for r in research_live.canonical_cohorts(conn, market, defn, as_of=cutoff):
        if r["generated_at"] < reg["effective_from"]:
            continue
        if r["tipoff_at"] >= cutoff or research_live.state(r, cutoff) != "settled":
            continue
        y = research_live._realised(market, r["line"], r, r["decision"])
        if y is not None:
            out.append(_item(conn, reg, sport, r, cutoff, y)[0])
    return out


# --- Weekly decisions ---------------------------------------------------------------------------------------------
def _source_stats(stats: list, sources: list) -> dict:
    """Best rule per pool source (by lower bound, then picks): what the roster shows as each source's evidence."""
    out = {}
    for s in stats:
        if s["source"] not in sources:
            continue
        cur = out.get(s["source"])
        key = (s["lower"] if s["lower"] is not None else float("-inf"), s["picks"])
        if cur is None or key > (cur["lower"] if cur["lower"] is not None else float("-inf"), cur["picks"]):
            out[s["source"]] = {k: s[k] for k in ("filter", "threshold", "picks", "wins", "losses", "pushes", "coverage",
                                                  "weeks", "lift", "lower", "eligible")}
    return out


def decide_due(conn, *, now: Optional[str] = None, write: bool = True, defn: Optional[str] = None,
               reg: Optional[dict] = None) -> dict:
    """This week's pool decision per market that has pool sources. Same schedule, evidence cutoff and idempotency as the
    core decisions; written to pool_decisions."""
    fixed = now is not None
    now = now or utc_now()
    reg = reg or load_registry()
    sport = core_live.engine(conn).sport
    sched = research_live.scheduled_time(now)
    until = research_live._fmt(research_live._parse(sched) + timedelta(days=7))
    defn = defn or research_live.definition(conn)
    version, rid = selector.version(), registry_id(reg)
    out = {"scheduled_for": sched, "selector_version": version, "definition": defn, "registry": rid, "written": [],
           "existing": []}
    if now < reg["effective_from"]:
        return {**out, "status": "not_yet_effective"}
    for market in sports.get(sport).markets:
        extra = pool_sources(reg, sport, market)
        if not extra:
            continue
        if conn.execute("SELECT 1 FROM pool_decisions WHERE sport=? AND market=? AND selector_version=? AND definition=? "
                        "AND registry=? AND scheduled_for=?", (sport, market, version, defn, rid, sched)).fetchone():
            out["existing"].append(market)
            continue
        prev = conn.execute("SELECT rule FROM pool_decisions WHERE sport=? AND market=? AND selector_version=? AND definition=? "
                            "AND registry=? AND scheduled_for<? ORDER BY scheduled_for DESC LIMIT 1",
                            (sport, market, version, defn, rid, sched)).fetchone()
        incumbent = json.loads(prev["rule"]) if prev else None
        items = evidence(conn, reg, sport, market, defn, sched)
        srcs = all_sources(reg, sport, market)
        d = selector.decide(items, market, srcs, incumbent)
        decided = now if fixed else utc_now()
        summary = {k: d[k] for k in ("settled_items", "rules", "z", "min_picks", "min_coverage", "min_weeks", "eligible_rules")}
        top = sorted([s for s in d["stats"] if s["lower"] is not None], key=lambda s: -s["lower"])[:10]
        ev = {**summary, "evidence_until": sched, "sources": srcs, "pool_sources": extra, "top_rules": top,
              "source_stats": _source_stats(d["stats"], extra), "settlement_version": research_live.settlement_version(),
              "weeks": sorted({i["week"] for i in items})}
        if write:
            conn.execute(
                "INSERT INTO pool_decisions (idempotency_key, sport, market, selector_version, definition, registry, "
                "scheduled_for, decided_at, effective_until, status, rule, lower_bound, reason, evidence) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (research_live._key("pool", sport, market, version, defn, rid, sched), sport, market, version, defn, rid,
                 sched, decided, until, d["status"], json.dumps(d["rule"]), d.get("lower"), d["reason"], selector.dumps(ev)))
            conn.commit()
        out["written"].append({"market": market, "status": d["status"], "reason": d["reason"], "rule": d["rule"]})
    return out


def decision_in_force(conn, sport: str, market: str, defn: str, rid: str, at: str):
    """As research_live.decision_in_force, for pool decisions of one registry."""
    return conn.execute("SELECT * FROM pool_decisions WHERE sport=? AND market=? AND selector_version=? AND definition=? "
                        "AND registry=? AND decided_at<=? AND ? < strftime('%Y-%m-%dT%H:%M:%SZ', effective_until, ?) "
                        "ORDER BY decided_at DESC, decision_id DESC LIMIT 1",
                        (sport, market, selector.version(), defn, rid, at, at, research_live.CARRY_OVER)).fetchone()


# --- Live selection (after the candidate forecasts of the same two-hour cycle) -------------------------------------
def select(conn, *, game_ids: list, now: Optional[str] = None, write: bool = True, defn: Optional[str] = None,
           reg: Optional[dict] = None) -> dict:
    """Pool selections for the latest core cohorts of `game_ids` under the pool decisions in force. Never raises into the
    cycle (the job guard catches); never writes core rows."""
    fixed = now is not None
    now = now or utc_now()
    reg = reg or load_registry()
    sport = core_live.engine(conn).sport
    defn = defn or research_live.definition(conn)
    rid = registry_id(reg)
    out = {"status": "success", "registry": rid, "definition": defn, "selections_created": 0, "selections": [],
           "recommendations": []}
    stamp = lambda: now if fixed else utc_now()
    wanted, latest = set(game_ids), {}
    for r in research_live.canonical_cohorts(conn, None, defn, as_of=now):
        # Only cohorts for the game's current start of a still-scheduled game (a rescheduled game's old cohort is void).
        if (r["game_id"] in wanted and pool_sources(reg, sport, r["market"]) and r["tipoff_at"] > now
                and r["status"] == "scheduled" and research_live.state(r, now) != "void"):
            latest.setdefault(r["game_id"], {})[r["market"]] = r
    for gid in game_ids:
        per_market = latest.get(gid, {})
        if not per_market:
            continue
        try:
            _select_game(conn, reg, sport, rid, gid, per_market, stamp, write, out)
        except Exception as exc:         # one game's failure never withholds the others' selections
            conn.rollback()
            out["status"] = "partial"
            out.setdefault("errors", {})[gid] = f"{type(exc).__name__}: {exc}"
    return out


def _select_game(conn, reg, sport, rid, gid, per_market, stamp, write, out) -> None:
    defn = next(iter(per_market.values()))["definition"]
    generated = stamp()
    drows = {m: decision_in_force(conn, sport, m, defn, rid, generated) for m in per_market}
    context = research_live._key(sorted((m, r["cohort_id"], None if drows[m] is None else drows[m]["decision_id"])
                                        for m, r in per_market.items()), "pool", rid)
    cands, rows = {}, {}
    for market, r in per_market.items():
        drow = drows[market]
        dec = None if drow is None else {"status": drow["status"], "rule": json.loads(drow["rule"]),
                                         "lower": drow["lower_bound"], "reason": drow["reason"]}
        it, used = _item(conn, reg, sport, r, generated)
        s = selector.apply(dec, it, market)
        cands[market] = (s, dec or {"lower": None})
        rows[market] = (r, drow, s, used)
    chosen, reasons = selector.choose_market(cands)
    if chosen:
        out["recommendations"].append({"game_id": gid, "market": chosen, "side": cands[chosen][0]["side"]})
    for market, (r, drow, s, used) in rows.items():
        if generated >= r["tipoff_at"]:
            continue
        rule = json.loads(drow["rule"]) if drow is not None and drow["status"] == "active" else None
        entry = {"game_id": gid, "market": market, "status": s["status"], "side": s["side"], "reason": s["reason"],
                 "source": rule["source"] if rule else None, "decision": None if drow is None else drow["status"]}
        out["selections"].append(entry)
        if not write:
            continue
        key = research_live._key("pool", r["cohort_id"], None if drow is None else drow["decision_id"], context,
                                 sorted((k, sorted(v)) for k, v in used.items()))   # a later candidate row → new row
        with transaction(conn):
            if conn.execute("SELECT 1 FROM pool_selections WHERE idempotency_key=?", (key,)).fetchone():
                continue
            conn.execute(
                "INSERT INTO pool_selections (idempotency_key, cohort_id, decision_id, sport, game_id, market, generated_at, "
                "tipoff_at, context, status, source, side, probability, threshold, reason, inputs, recommended, game_reason) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, r["cohort_id"], None if drow is None else drow["decision_id"], sport, gid, market, generated,
                 r["tipoff_at"], context, s["status"], rule["source"] if rule else None,
                 None if s["status"] != "pick" else s["side"], s["probability"], rule["threshold"] if rule else None,
                 s["reason"], json.dumps(used, sort_keys=True), int(market == chosen), reasons[market]))
        out["selections_created"] += 1


# --- Reports (prospective scoring of the registered pool sources) -------------------------------------------------
def report(conn, sport: str, as_of: Optional[str] = None, reg: Optional[dict] = None) -> dict:
    """Per market with pool sources: every pool source's prospective record under every selector rule (win / loss /
    push over settled canonical core cohorts; void and pending excluded), the decision history and coverage of the
    candidate vectors. Separate from the core rule report."""
    as_of = as_of or utc_now()
    reg = reg or load_registry()
    rid = registry_id(reg)
    # Every core definition with cohorts (not only those that already have a pool decision): a settled game is reported
    # before the first decision and after a definition change.
    defs = [r[0] for r in conn.execute("SELECT DISTINCT definition FROM research_cohorts WHERE definition NOT LIKE ? "
                                       "ORDER BY definition", (f"%:{research_live.EXPANDED}",))]
    markets_out = {}
    for market in sports.get(sport).markets:
        ents = entries(reg, sport, market)
        if not ents:
            continue
        extra = [e["source"] for e in ents]
        items, joined = [], {s: 0 for s in extra}
        # One cohort per game across core definitions (the latest made), so a definition change never counts a game twice.
        per_game = {}
        for defn in defs:
            for r in research_live.canonical_cohorts(conn, market, defn, as_of=as_of):
                if r["generated_at"] < reg["effective_from"] or research_live.state(r, as_of) != "settled":
                    continue
                cur = per_game.get(r["game_id"])
                if cur is None or (r["generated_at"], r["cohort_id"]) > (cur["generated_at"], cur["cohort_id"]):
                    per_game[r["game_id"]] = r
        for r in sorted(per_game.values(), key=lambda r: (r["tipoff_at"], r["game_id"])):
            y = research_live._realised(market, r["line"], r, r["decision"])
            if y is None:
                continue
            it = _item(conn, reg, sport, r, as_of, y)[0]
            for s in extra:
                joined[s] += s in it["vectors"]
            items.append(it)
        cohorts = len(items)
        stats = selector.evaluate(items, market, extra) if items else []
        decisions = [dict(d) for d in conn.execute(
            "SELECT scheduled_for, decided_at, effective_until, status, rule, lower_bound, reason, definition FROM pool_decisions "
            "WHERE sport=? AND market=? AND registry=? AND decided_at<=? ORDER BY scheduled_for DESC LIMIT 12",
            (sport, market, rid, as_of))]
        for d in decisions:
            d["rule"] = json.loads(d["rule"])
        markets_out[market] = {
            "sources": [{**e, "settled_with_vector": joined[e["source"]],
                         "best_rule": _source_stats(stats, [e["source"]]).get(e["source"])} for e in ents],
            "settled_cohorts": cohorts, "decisions": decisions}
    return {"sport": sport, "registry": rid, "effective_from": reg["effective_from"], "as_of": as_of,
            "routes": reg["routes"], "rule": reg["rule"], "markets": markets_out,
            "note": "Research only. Registry routes come from exploratory held-out seasons; only prospective settled "
                    "cohorts can show value, and the selector requires its own thresholds before any pick."}


def core_definition(conn) -> Optional[str]:
    """The current core definition, or None for a store without the core models (the roster then shows routes only)."""
    try:
        return research_live.definition(conn)
    except Exception:
        return None


def candidate_state(conn, reg: dict, sport: str, name: str, as_of: str, defn: Optional[str]) -> dict:
    """For the roster: the candidate's registry route per market and the pool decision in force there."""
    out = {}
    for market, ents in (reg.get("sports", {}).get(sport) or {}).items():
        mine = [e for e in ents if name in e["candidates"]]
        if not mine:
            continue
        drow = decision_in_force(conn, sport, market, defn, registry_id(reg), as_of) if defn and _has_pool(conn) else None
        rule = json.loads(drow["rule"]) if drow is not None and drow["status"] == "active" else None
        stats = json.loads(drow["evidence"]).get("source_stats", {}) if drow is not None else {}
        out[market] = {
            "sources": [{"source": e["source"], "route": e["route"], "evidence": e["evidence"],
                         "prospective": stats.get(e["source"])} for e in mine],
            "decision": None if drow is None else {"status": drow["status"], "rule": rule, "reason": drow["reason"],
                                                   "evidence_until": drow["scheduled_for"], "effective_until": drow["effective_until"]},
            "selected_now": bool(rule and rule["source"] in {e["source"] for e in mine})}
    return out


def _has_pool(conn) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pool_decisions'").fetchone() is not None
