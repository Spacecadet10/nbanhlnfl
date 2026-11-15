"""Frontend/API consistency for the research screens: every field the TypeScript types declare is present in the API JSON."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app import main

TYPES = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "api" / "types.ts").read_text()


def fields(interface: str) -> set:
    body = re.search(r"export interface " + interface + r"\b[^{]*\{(.*?)\n\}", TYPES, re.S).group(1)
    while re.search(r"\{[^{}]*\}", body):                 # drop nested object types: top-level fields only
        body = re.sub(r"\{[^{}]*\}", "", body)
    body = re.sub(r"<[^<>]*>", "", body)
    return {m for m in re.findall(r"(\w+)\??:", body) if m != "p"}


def test_research_api_matches_the_frontend_types(monkeypatch):
    monkeypatch.setenv("NBA_API_MODE", "demo")
    monkeypatch.setattr(main.app, "dependency_overrides", {})
    c = TestClient(main.app)
    game = c.get("/api/research/game/demo-20261030-LAL-DEN").json()
    top = {"game_id", "mode", "status", "markets", "recommendation", "note"}
    assert top <= set(game)
    market = game["markets"][0]
    want = {"market", "line", "line_source", "line_label", "bookmaker", "sides", "primary", "members", "missing", "average",
            "stack", "stack_note", "agreement", "definition", "generated_at", "information_at", "tipoff_at",
            "minutes_before_start", "start_changed", "selection"}
    assert want <= set(market) and want <= fields("ResearchMarket") | {"p"}
    sel = next(m["selection"] for m in game["markets"] if m["selection"])
    assert fields("ResearchSelection") <= set(sel)
    slate = c.get("/api/research/slate", params={"date": "2026-10-30"}).json()["games"]
    entry = next(e for e in slate.values() if e["status"] != "none")
    assert fields("ResearchSlateMarket") <= set(next(iter(entry["markets"].values())))
    rep = c.get("/api/research/performance", params={"market": "winner"}).json()
    g = rep["groups"][0]
    assert fields("ResearchGroup") <= set(g)
    assert fields("ResearchSource") - {"wins", "losses", "pushes", "decided", "accuracy", "ci95"} <= set(g["sources"]["elo"])
    hist = c.get("/api/research/history").json()
    assert fields("ResearchHistory") <= set(hist)
    retro = c.get("/api/research/retrospective").json()
    assert fields("Retrospective") - {"status"} <= set(retro)
