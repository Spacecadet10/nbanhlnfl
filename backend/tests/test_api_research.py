"""Research API contract: demo fixture (isolated, synthetic) and real-store empty states."""
from fastapi.testclient import TestClient

from app import main


def test_demo_research_endpoints_are_labelled_and_populated(monkeypatch):
    monkeypatch.setenv("NBA_API_MODE", "demo")
    monkeypatch.setattr(main.app, "dependency_overrides", {})
    c = TestClient(main.app)
    slate = c.get("/api/research/slate", params={"date": "2026-10-30"}).json()
    assert slate["mode"] == "demo" and len(slate["games"]) == 4
    assert {g["status"] for g in slate["games"].values()} <= {"pick", "no_pick", "none"}
    gid = next(iter(slate["games"]))
    detail = c.get(f"/api/research/game/{gid}").json()
    assert detail["mode"] == "demo" and "Demo" in detail["note"] and len(detail["markets"]) == 5
    win = next(m for m in detail["markets"] if m["market"] == "winner")
    assert win["minutes_before_start"] <= 120 and win["agreement"]["expected"] == 6
    perf = c.get("/api/research/performance", params={"market": "winner"}).json()
    assert perf["mode"] == "demo" and perf["groups"][0]["settled"] > 100 and perf["decisions"]
    assert c.get("/api/research/performance", params={"market": "reg3"}).status_code == 404
    retro = c.get("/api/research/retrospective").json()
    assert retro["kind"].startswith("retrospective") and "winner" in retro["markets"]
