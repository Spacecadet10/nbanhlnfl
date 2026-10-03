"""Store-backed requests are served one at a time, and waiting for that turn must never use up the worker threads: with
the turn taken inside a worker thread, more concurrent requests than threads deadlocked the whole API (2026-10-03: 60
requests against a store copy, every one timed out and /api/health stopped answering). Synthetic stores only."""
import threading

import anyio
from fastapi.testclient import TestClient

from app.main import app
from test_api_markets import api  # noqa: F401  (real routing on a synthetic NHL store; get_provider is not overridden)
from test_live_markets import sealed  # noqa: F401
from test_nhl_eval import nhl_store  # noqa: F401

WORKERS = 3          # the deadlock appears once concurrent requests exceed the worker threads (40 by default)
REQUESTS = 8


def _shrink_worker_pool() -> None:
    anyio.to_thread.current_default_thread_limiter().total_tokens = WORKERS


def test_more_concurrent_store_requests_than_worker_threads_all_complete(api):
    with TestClient(app) as client:          # one event loop and one worker pool for every request, as under uvicorn
        client.portal.call(_shrink_worker_pool)
        codes, errors = [], []

        def hit():
            try:
                codes.append(client.get("/api/meta", params={"sport": "nhl"}).status_code)
            except Exception as exc:          # recorded, so a failure is reported instead of a hung thread
                errors.append(repr(exc))

        threads = [threading.Thread(target=hit, daemon=True) for _ in range(REQUESTS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert not errors
        assert codes == [200] * REQUESTS, f"{len(codes)} of {REQUESTS} requests completed"
        assert client.get("/api/health").status_code == 200      # the API still answers afterwards


def test_a_request_abandoned_while_queued_is_not_served_and_frees_the_turn(api):
    """Serving abandoned requests one at a time made every later request wait (20 hung-up clients: > 10 s)."""
    import asyncio

    import pytest
    from fastapi import HTTPException

    from app import main

    class Gone:                       # a client that hung up while its request waited for the turn
        async def is_disconnected(self):
            return True

    async def run():
        gen = main.get_provider(Gone(), "real", "nhl")
        with pytest.raises(HTTPException) as caught:
            await gen.__anext__()
        assert caught.value.status_code == 499
    asyncio.run(run())
    assert main._STORE_TURN.acquire(blocking=False)      # the turn was released
    main._STORE_TURN.release()


def test_a_store_error_inside_an_endpoint_is_still_a_503_and_frees_the_turn(api, monkeypatch):
    import sqlite3
    from app.providers.real import RealProvider

    def broken(self):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(RealProvider, "sources", broken)
    with TestClient(app) as client:
        r = client.get("/api/sources", params={"sport": "nhl"})
        assert r.status_code == 503 and "Data store error" in r.json()["detail"]
        assert client.get("/api/meta", params={"sport": "nhl"}).status_code == 200    # the turn was released
