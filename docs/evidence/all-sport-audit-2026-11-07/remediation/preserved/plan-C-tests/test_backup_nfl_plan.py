"""Proposed regression tests (plan C3). Must FAIL on snap3: backup covers only nba/nhl."""
import json
import pytest
from app import backup, db


def _nfl_store(path):
    conn = db.connect(path)
    db.migrate(conn)
    conn.execute("INSERT INTO store_identity (sport, claimed_at) VALUES ('nfl', '2026-11-01T00:00:00Z')")
    conn.execute("INSERT INTO teams (team_id, abbr, name, first_seen_at) VALUES ('nfl:1', 'BAL', 'Ravens', '2026-11-01T00:00:00Z')")
    return conn


def test_an_nfl_store_is_backed_up_verified_and_restorable(tmp_path):
    data, dest = tmp_path / "data", tmp_path / "bk"
    _nfl_store(data / "nfl.sqlite").close()
    out = backup.run(data, dest, stamp="s", sports=["nfl"])
    man = json.loads((dest / "s" / "manifest.json").read_text())
    assert out["status"] == "ok" and man["stores"]["nfl"]["identity"] == "nfl" and man["stores"]["nfl"]["rows"]["teams"] == 1
    restored = backup.restore(dest / "s", data, sports=["nfl"])
    assert restored["nfl"]["identity"] == "nfl"


def test_the_cli_accepts_nfl_and_soccer(tmp_path, monkeypatch):
    data = tmp_path / "data"
    _nfl_store(data / "nfl.sqlite").close()
    monkeypatch.setenv("NBA_DATA_DIR", str(data))
    backup.main(["--dest", str(tmp_path / "bk"), "--sport", "nfl"])


def test_a_default_backup_includes_every_existing_store_and_skips_absent_ones(tmp_path):
    data = tmp_path / "data"
    _nfl_store(data / "nfl.sqlite").close()
    out = backup.run(data, tmp_path / "bk", stamp="s")
    assert set(out["stores"]) == {"nfl"}
