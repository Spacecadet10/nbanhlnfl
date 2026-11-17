"""Shared helpers for the NCAAB data tests: real (trimmed) ESPN responses saved under tests/fixtures/ncaab/."""
import json
from pathlib import Path

import pytest

from app import db

FIXTURES = Path(__file__).parent / "fixtures" / "ncaab"


def raw(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def load(name: str) -> dict:
    return json.loads(raw(name))


def dump(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def event(payload: dict, event_id: str) -> dict:
    return next(e for e in payload["events"] if e["id"] == event_id)


@pytest.fixture
def ncaab(tmp_path, monkeypatch):
    """A migrated scratch NCAAB store (file ncaab.sqlite in a temporary data folder)."""
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    from app import config
    conn = db.connect(config.db_path("ncaab"))
    db.migrate(conn)
    yield conn
    conn.close()


def counts(conn, tables=("games", "teams", "team_game_stats", "result_revisions", "raw_responses", "raw_fetches",
                         "market_quotes", "ncaab_game_context", "ncaab_team_seasons")) -> dict:
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
