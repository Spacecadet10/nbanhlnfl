"""Selection status per market (API /api/research/status): different markets show different selected rules, from the
decisions in force at the time asked; the API returns exactly what the module computes; later decisions never leak back."""
import json

from fastapi.testclient import TestClient

from app import research_live, selection_status, selector
from app.candidates import selection
from app.main import api_mode, app
from test_live_markets import sealed  # noqa: F401
from test_nhl_eval import nhl_store  # noqa: F401
from test_research_contract import fields

DECIDED, AS_OF, LATER = "2026-10-05T14:05:00Z", "2026-10-06T12:00:00Z", "2026-10-12T14:05:00Z"


def _ev(rule):
    return json.dumps({"settled_items": 400, "rules": 126, "eligible_rules": 3, "min_picks": 100, "min_coverage": 0.1,
                       "min_weeks": 3, "top_rules": [{**rule, "picks": 140, "wins": 101, "losses": 39, "pushes": 0,
                                                      "coverage": 0.35, "weeks": 6, "accuracy": 101 / 140, "lift": 0.05,
                                                      "lower": 0.004}]})


def decide(c, table, market, rule, defn, decided=DECIDED, until="2026-10-12T14:00:00Z", registry=None):
    sched = decided[:11] + "14:00:00Z"
    cols = "idempotency_key, sport, market, selector_version, definition, scheduled_for, decided_at, effective_until, status, " \
           "rule, lower_bound, reason, evidence"
    vals = [f"k-{table}-{market}-{decided}", "nhl", market, selector.version(), defn, sched, decided, until,
            "active" if rule else "insufficient_evidence", json.dumps(rule), 0.004 if rule else None, "test",
            _ev(rule) if rule else "{}"]
    if registry:
        cols += ", registry"
        vals.append(registry)
    c.execute(f"INSERT INTO {table} ({cols}) VALUES ({','.join('?' * len(vals))})", vals)
    c.commit()


def test_markets_select_different_combinations_and_the_api_shows_the_same_rule(sealed, monkeypatch):
    defn = research_live.definition(sealed)
    rid = selection.registry_id(selection.load_registry())
    win = {"source": "average", "filter": "unanimous", "threshold": 0.6}
    reg3_pool = {"source": "avg:goals_draw+cand:poisson_draw:nhl_base", "filter": "none", "threshold": 0.5}
    decide(sealed, "selector_decisions", "winner", win, defn)
    decide(sealed, "selector_decisions", "reg3", None, defn)
    decide(sealed, "pool_decisions", "reg3", reg3_pool, defn, registry=rid)
    decide(sealed, "selector_decisions", "winner", {"source": "stack", "filter": "none", "threshold": 0.7}, defn,
           decided=LATER, until="2026-10-19T14:00:00Z")                       # made later: never shown before it existed
    out = selection_status.status(sealed, AS_OF)
    m = {x["market"]: x for x in out["markets"]}
    assert m["winner"]["status"] == "selected" and m["winner"]["core"]["rule"]["source"] == "average"
    assert m["winner"]["core"]["rule"]["agreement"] == "all members agree" and m["winner"]["core"]["rule"]["threshold"] == 0.6
    assert m["winner"]["core"]["selected_at"] == DECIDED and m["winner"]["core"]["next_review"] == "2026-10-12T14:00:00Z"
    assert m["winner"]["core"]["evidence"]["record"]["picks"] == 140
    assert m["reg3"]["status"] == "selected" and m["reg3"]["core"]["status"] == "insufficient_evidence"
    assert m["reg3"]["pool"]["rule"]["members"] == ["goals_draw", "cand:poisson_draw:nhl_base"]
    assert m["winner"]["core"]["rule"]["source"] != m["reg3"]["pool"]["rule"]["source"]
    assert m["total"]["status"] == "research only" and m["total"]["pool"] is None      # no pool source registered there
    monkeypatch.setenv("NBA_API_MODE", "real")
    monkeypatch.delitem(app.dependency_overrides, api_mode, raising=False)
    from app.providers import real
    monkeypatch.setattr(real, "utc_now", lambda: AS_OF)
    api = TestClient(app).get("/api/research/status", params={"sport": "nhl"}).json()
    am = {x["market"]: x for x in api["markets"]}
    for k in ("winner", "reg3"):
        for sel in ("core", "pool"):
            assert (am[k][sel] or {}).get("rule") == (m[k][sel] or {}).get("rule")
    assert fields("SelectionStatusMarket") <= set(am["winner"]) and fields("SelectionStatus") <= set(api)
    assert fields("SelectionRuleView") - {"eligible_sources"} <= set(am["winner"]["core"])       # the frontend's declared fields
    later = {x["market"]: x for x in selection_status.status(sealed, "2026-10-13T00:00:00Z")["markets"]}
    assert later["winner"]["core"]["rule"]["source"] == "stack"
