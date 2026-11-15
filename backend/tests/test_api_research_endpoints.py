"""HTTP contract of the research endpoints that had no HTTP-level test: demo labelling, real-mode responses on the synthetic
NBA and NHL stores (with recorded research data on the NHL one), and parameter validation. Research only; no network."""
import pytest
from fastapi.testclient import TestClient

from app import db, main
from app.providers.real import RealProvider
from test_live import client as nba_client, store  # noqa: F401  (fixtures: synthetic NBA store, real provider)
from test_live_markets import sealed  # noqa: F401  (fixture)
from test_nhl_eval import nhl_store  # noqa: F401  (fixture)
from test_research_live import GID, game, run  # noqa: F401  (fixture: synthetic NHL store)

ENDPOINTS = ["/api/agreement-performance", "/api/recovered-forecasts", "/api/thresholds", "/api/research/rules",
             "/api/research/tracking/check", "/api/research/adaptive", "/api/research/adaptive/game/{gid}",
             "/api/research/historical-start", "/api/research/candidates", "/api/research/pool"]
CYCLE = "2026-10-12T21:30:00Z"            # 90 minutes before the synthetic NHL game


@pytest.fixture
def nhl_client(game, monkeypatch):
    path = game.execute("PRAGMA database_list").fetchone()["file"]

    def provider():
        conn = db.connect(path, check_same_thread=False)
        try:
            yield RealProvider(conn, now=c.now)
        finally:
            conn.close()
    monkeypatch.setitem(main.app.dependency_overrides, main.get_provider, provider)
    c = TestClient(main.app, raise_server_exceptions=False)
    c.now = CYCLE
    return game, c


@pytest.mark.parametrize("path", ENDPOINTS)
def test_demo_mode_is_labelled_and_never_shows_real_or_invented_research(path, monkeypatch):
    monkeypatch.setenv("NBA_API_MODE", "demo")
    monkeypatch.setattr(main.app, "dependency_overrides", {})
    r = TestClient(main.app).get(path.format(gid="g1"), params={"sport": "nba"})
    assert r.status_code == 200, r.text
    d = r.json()
    text = str(d).lower()
    assert d.get("mode") == "demo" or "demo" in text, d
    for k in ("groups", "rules", "candidates", "members", "attempts", "folds", "markets"):
        if isinstance(d.get(k), (list, dict)):
            assert not d[k], f"demo {path} returned research rows under {k}"


@pytest.mark.parametrize("path", ENDPOINTS)
def test_nba_real_mode_answers_every_research_endpoint(path, nba_client):  # noqa: F811
    r = nba_client.get(path.format(gid="nba:none"), params={"sport": "nba"})
    assert r.status_code in (200, 404, 503), r.text
    if r.status_code != 200:                      # an explicit, named reason; never a bare server error
        assert r.json()["detail"]
    else:
        assert r.json().get("mode", "real") == "real"


@pytest.mark.parametrize("path", ENDPOINTS)
def test_nhl_real_mode_with_recorded_research_answers_every_endpoint(path, nhl_client):
    conn, c = nhl_client
    run(conn, CYCLE)
    conn.commit()
    r = c.get(path.format(gid=GID), params={"sport": "nhl"})
    assert r.status_code in (200, 404, 503), r.text
    if r.status_code != 200:
        assert r.json()["detail"]
    else:
        assert r.json().get("mode", "real") == "real"


def test_parameter_validation(nhl_client, monkeypatch):
    _, c = nhl_client
    assert c.get("/api/research/rules", params={"sport": "nhl", "market": "moneyline"}).status_code == 422
    assert c.get("/api/research/thresholds/ledger", params={"sport": "nhl"}).status_code == 422      # rule is required
    monkeypatch.setattr(main.app, "dependency_overrides", {})           # the sport is checked by the real provider dependency
    monkeypatch.setenv("NBA_API_MODE", "demo")
    for path in ENDPOINTS:
        assert TestClient(main.app).get(path.format(gid="g1"), params={"sport": "mlb"}).status_code == 422, path
