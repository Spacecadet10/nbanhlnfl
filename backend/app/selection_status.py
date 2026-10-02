"""One row per market: which rule (model or exact subset, agreement requirement, threshold) the selectors have in force, with
its version, selection date, evidence cutoff, next review, evidence and status. Read-only; uses only decisions written at or
before `as_of` (the decisions themselves used only evidence settled before their own cutoff)."""
import json
from typing import Optional

from . import research_live, selector, sports
from .db import utc_now

FILTER_LABEL = {"none": "no agreement requirement", "majority": "majority of members agree", "unanimous": "all members agree"}


def _rule_view(row, kind: str) -> dict:
    """A decision row (core or pool) as shown: status, rule and its own evidence."""
    if row is None:
        return {"selector": kind, "status": "no_decision", "label": "no decision in force", "rule": None}
    rule = json.loads(row["rule"]) if row["rule"] else None
    ev = json.loads(row["evidence"]) if row["evidence"] else {}
    stats = None
    if rule is not None:
        stats = next((s for s in ev.get("top_rules", []) if (s.get("source"), s.get("filter"), s.get("threshold"))
                      == (rule["source"], rule["filter"], rule["threshold"])), None)
    label = {"active": "selected", "abstain": "no rule beats the reference (abstain)",
             "insufficient_evidence": "insufficient evidence"}.get(row["status"], row["status"])
    return {"selector": kind, "status": row["status"], "label": label, "decision_id": row["decision_id"],
            "selector_version": row["selector_version"], "definition": row["definition"],
            "registry": row["registry"] if "registry" in row.keys() else None,
            "selected_at": row["decided_at"], "evidence_until": row["scheduled_for"], "next_review": row["effective_until"],
            "reason": row["reason"], "lower_bound": row["lower_bound"],
            "rule": None if rule is None else {**rule, "members": rule["source"].removeprefix("avg:").split("+")
                                               if rule["source"].startswith("avg:") else [rule["source"]],
                                               "agreement": FILTER_LABEL.get(rule["filter"], rule["filter"])},
            "evidence": {"settled_cohorts": ev.get("settled_items"), "rules_compared": ev.get("rules"),
                         "eligible_rules": ev.get("eligible_rules"), "min_picks": ev.get("min_picks"),
                         "min_coverage": ev.get("min_coverage"), "min_weeks": ev.get("min_weeks"),
                         "record": None if stats is None else {k: stats.get(k) for k in
                                                                ("picks", "wins", "losses", "pushes", "coverage", "weeks",
                                                                 "accuracy", "lift", "lower")}}}


def _latest_cohort(conn, market: str, defn: str, as_of: str):
    return conn.execute("SELECT generated_at, members, missing FROM research_cohorts WHERE market=? AND definition=? "
                        "AND generated_at<=? ORDER BY generated_at DESC, cohort_id DESC LIMIT 1", (market, defn, as_of)).fetchone()


def status(conn, as_of: Optional[str] = None) -> dict:
    as_of = as_of or utc_now()
    sport = research_live.live.engine(conn).sport
    defn = research_live.definition(conn)
    reg = rid = None
    try:
        from .candidates import selection
        reg = selection.load_registry()
        rid = selection.registry_id(reg)
    except Exception as exc:          # a missing or broken registry is shown, never hidden
        reg_error = f"{type(exc).__name__}: {exc}"
    else:
        reg_error = None
    markets = []
    for market in sports.get(sport).markets:
        core = _rule_view(research_live.decision_in_force(conn, sport, market, defn, as_of), "core")
        pool = None
        if reg is not None and selection.pool_sources(reg, sport, market):
            pool = _rule_view(selection.decision_in_force(conn, sport, market, defn, rid, as_of), "pool")
            pool["eligible_sources"] = [{"source": e["source"], "route": e["route"], "members": e["members"]}
                                        for e in selection.entries(reg, sport, market)]
        coh = _latest_cohort(conn, market, defn, as_of)
        recording = {"latest_cohort_at": None, "missing": {}} if coh is None else {
            "latest_cohort_at": coh["generated_at"], "members": json.loads(coh["members"]) if coh["members"] else None,
            "missing": json.loads(coh["missing"]) if coh["missing"] else {}}
        selected = [v for v in (core, pool) if v is not None and v["status"] == "active"]
        overall = "selected" if selected else ("eligible, insufficient evidence" if any(
            v is not None and v["status"] == "insufficient_evidence" for v in (core, pool)) else
            "abstain" if any(v is not None and v["status"] == "abstain" for v in (core, pool)) else "research only")
        markets.append({"market": market, "status": overall, "core": core, "pool": pool,
                        "research_sources": research_live.sources(sport, market), "recording": recording})
    return {"sport": sport, "as_of": as_of, "definition": defn, "selector_version": selector.version(),
            "pool_registry": rid, "pool_registry_error": reg_error, "markets": markets,
            "note": "Rules come only from weekly decisions made on prospective evidence settled before each decision's cutoff. "
                    "Research forecasts keep recording when no rule is selected; requirements are never lowered to force one."}
