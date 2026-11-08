"""Test-wide guards: the NHL play-by-play adapter never reaches the network from tests (pass a fake `fetch` instead), and
no test ever uses the checkout's own data folder (REPO/data holds the live stores)."""
import os

import pytest


@pytest.fixture(autouse=True)
def _no_nhl_pbp_network(monkeypatch):
    from app.sources import nhl_pbp

    def blocked(game_id, timeout=30):
        raise RuntimeError("network disabled in tests")
    monkeypatch.setattr(nhl_pbp, "fetch", blocked)


@pytest.fixture(autouse=True)
def _never_the_live_data_dir(monkeypatch, tmp_path_factory):
    """NBA_DATA_DIR is a fresh temporary folder unless it is already set to a folder that is not protected (e.g. a
    module-scoped fixture's store). Unset or protected (REPO/data, a main checkout's data folder) is replaced, so a test
    that forgets to set it can never read, write or migrate the live stores. Tests still set or unset it themselves."""
    from app import config
    current = os.environ.get("NBA_DATA_DIR")
    if not current or config.protected_path(current):
        monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path_factory.mktemp("nba-data-dir")))
