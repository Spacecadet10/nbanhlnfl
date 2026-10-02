"""Test-wide guard: the NHL play-by-play adapter never reaches the network from tests (pass a fake `fetch` instead)."""
import pytest


@pytest.fixture(autouse=True)
def _no_nhl_pbp_network(monkeypatch):
    from app.sources import nhl_pbp

    def blocked(game_id, timeout=30):
        raise RuntimeError("network disabled in tests")
    monkeypatch.setattr(nhl_pbp, "fetch", blocked)
