"""Store backup before a release migration (app/backup.py): a consistent online copy of each sport's store, verified, with
a manifest that a restore is checked against. Synthetic stores under tmp_path only."""
import json
import sqlite3

import pytest

from app import backup, db


def _store(path, sport, rows=3):
    conn = db.connect(path)
    db.migrate(conn, upto=28)
    conn.execute("INSERT INTO store_identity (sport, claimed_at) VALUES (?, '2026-10-01T00:00:00Z')", (sport,))
    for i in range(rows):
        conn.execute("INSERT INTO teams (team_id, abbr, name, first_seen_at) VALUES (?, ?, ?, '2026-10-01T00:00:00Z')",
                     (f"{sport}:{i}", f"T{i}", f"Team {i}"))
    return conn


def test_a_backup_is_a_verified_consistent_copy_and_a_restore_is_checked_against_its_manifest(tmp_path):
    data, dest = tmp_path / "data", tmp_path / "backups"
    live = {s: _store(data / f"{s}.sqlite", s) for s in ("nba", "nhl")}
    out = backup.run(data, dest, stamp="20261014T140000Z")
    man = json.loads((dest / "20261014T140000Z" / "manifest.json").read_text())
    assert out["status"] == "ok" and set(man["stores"]) == {"nba", "nhl"}
    for sport, m in man["stores"].items():
        assert m["integrity"] == "ok" and m["schema_version"] == 28 and m["rows"]["teams"] == 3
        copy = sqlite3.connect(dest / "20261014T140000Z" / f"{sport}.sqlite")
        assert copy.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == 3
        assert backup.sha256(dest / "20261014T140000Z" / f"{sport}.sqlite") == m["sha256"]
    # The source stores are unchanged and still writable (no lock left behind).
    live["nba"].execute("INSERT INTO teams (team_id, abbr, name, first_seen_at) VALUES ('nba:9', 'T9', 'Team 9', '2026-10-14T00:00:00Z')")
    # A migrated store is rolled back by restoring the backup; the restore is refused if a file does not match the manifest.
    db.migrate(live["nhl"])
    assert live["nhl"].execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] > 28
    for c in live.values():
        c.close()
    restored = backup.restore(dest / "20261014T140000Z", data, sports=["nhl"])
    assert restored["nhl"]["schema_version"] == 28 and restored["nhl"]["sha256"] == man["stores"]["nhl"]["sha256"]
    (dest / "20261014T140000Z" / "nba.sqlite").write_bytes(b"corrupt")
    with pytest.raises(backup.BackupError, match="does not match"):
        backup.restore(dest / "20261014T140000Z", data, sports=["nba"])


def test_a_backup_refuses_an_existing_destination_and_a_missing_store(tmp_path):
    data, dest = tmp_path / "data", tmp_path / "backups"
    _store(data / "nba.sqlite", "nba").close()
    backup.run(data, dest, stamp="s1", sports=["nba"])
    with pytest.raises(backup.BackupError, match="exists"):
        backup.run(data, dest, stamp="s1", sports=["nba"])
    with pytest.raises(backup.BackupError, match="No nhl store"):
        backup.run(data, dest, stamp="s2", sports=["nhl"])


def test_a_restore_refuses_a_manifest_naming_another_store_or_path_and_a_held_lock(tmp_path):
    data, dest = tmp_path / "data", tmp_path / "backups"
    for s in ("nba", "nhl"):
        _store(data / f"{s}.sqlite", s).close()
    backup.run(data, dest, stamp="s")
    folder = dest / "s"
    man = json.loads((folder / "manifest.json").read_text())
    for bad in ("nhl.sqlite", "../nba.sqlite"):
        (folder / "manifest.json").write_text(json.dumps({**man, "stores": {**man["stores"], "nba": {**man["stores"]["nba"], "file": bad}}}))
        with pytest.raises(backup.BackupError, match="not the nba store"):
            backup.restore(folder, data, sports=["nba"])
    (folder / "manifest.json").write_text(json.dumps(man))
    import fcntl
    import os
    fd = os.open(data / ".nhl-jobs.lock", os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)                              # a scheduled job holding the NHL store
    try:
        with pytest.raises(backup.BackupError, match="holds the nhl store lock"):
            backup.restore(folder, data, sports=["nhl"])
        before = backup.sha256(data / "nba.sqlite")                 # all sports are checked before any is swapped
        with pytest.raises(backup.BackupError, match="holds the nhl store lock"):
            backup.restore(folder, data, sports=["nba", "nhl"])
        assert backup.sha256(data / "nba.sqlite") == before and not list(data.glob("*.replaced-*"))
    finally:
        os.close(fd)
    (folder / "manifest.json").write_text("{not json")
    with pytest.raises(backup.BackupError, match="manifest"):
        backup.restore(folder, data, sports=["nhl"])


def test_a_backup_includes_committed_rows_still_in_the_wal_and_leaves_no_sidecar_files(tmp_path):
    data, dest = tmp_path / "data", tmp_path / "backups"
    conn = _store(data / "nba.sqlite", "nba")
    conn.execute("PRAGMA wal_autocheckpoint = 0")
    for i in range(20):
        conn.execute("INSERT INTO teams (team_id, abbr, name, first_seen_at) VALUES (?, 'X', 'X', '2026-10-01T00:00:00Z')", (f"w{i}",))
    assert (data / "nba.sqlite-wal").stat().st_size > 0
    out = backup.run(data, dest, stamp="w", sports=["nba"])
    assert out["stores"]["nba"]["rows"]["teams"] == 23
    assert sorted(p.name for p in (dest / "w").iterdir()) == ["manifest.json", "nba.sqlite"]
    conn.close()
