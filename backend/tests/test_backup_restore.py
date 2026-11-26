"""Store backup before a release migration (app/backup.py): a consistent online copy of each sport's store, verified, with
a manifest that a restore is checked against. Synthetic stores under tmp_path only."""
import fcntl
import json
import os
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


# Every store sport, served or data-only (NFL is a live data-only store; soccer has no live store yet).

def _data_store(path, sport, upto=None):
    conn = db.connect(path)
    db.migrate(conn, upto=upto)
    conn.execute("INSERT INTO store_identity (sport, claimed_at) VALUES (?, '2026-11-01T00:00:00Z')", (sport,))
    conn.execute("INSERT INTO teams (team_id, abbr, name, first_seen_at) VALUES (?, 'T1', 'Team 1', '2026-11-01T00:00:00Z')",
                 (f"{sport}:1",))
    return conn


def test_an_nfl_store_is_backed_up_verified_and_restorable(tmp_path):
    data, dest = tmp_path / "data", tmp_path / "backups"
    live = _data_store(data / "nfl.sqlite", "nfl", upto=34)
    out = backup.run(data, dest, stamp="pre35", sports=["nfl"])
    man = json.loads((dest / "pre35" / "manifest.json").read_text())
    assert out["status"] == "ok" and set(man["stores"]) == {"nfl"}
    m = man["stores"]["nfl"]
    assert m["file"] == "nfl.sqlite" and m["identity"] == "nfl" and m["integrity"] == "ok" and m["schema_version"] == 34
    assert m["rows"]["teams"] == 1 and {"market_quotes", "raw_responses", "raw_fetches"} <= set(m["rows"])
    assert backup.sha256(dest / "pre35" / "nfl.sqlite") == m["sha256"]
    assert sorted(p.name for p in (dest / "pre35").iterdir()) == ["manifest.json", "nfl.sqlite"]
    # The release migration (0035 rebuilds tables) is rolled back by restoring the backup, under the NFL job lock.
    db.migrate(live)
    live.execute("INSERT INTO teams (team_id, abbr, name, first_seen_at) VALUES ('nfl:2', 'T2', 'Team 2', '2026-11-07T00:00:00Z')")
    assert live.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] > 34
    live.close()
    fd = os.open(data / ".nfl-jobs.lock", os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)                              # an NFL job holding the store
    try:
        with pytest.raises(backup.BackupError, match="holds the nfl store lock"):
            backup.restore(dest / "pre35", data, sports=["nfl"])
    finally:
        os.close(fd)
    restored = backup.restore(dest / "pre35", data, sports=["nfl"])
    assert restored["nfl"]["identity"] == "nfl" and restored["nfl"]["schema_version"] == 34
    assert restored["nfl"]["rows"]["teams"] == 1 and restored["nfl"]["sha256"] == m["sha256"]
    assert backup.sha256(data / "nfl.sqlite") == m["sha256"] and list(data.glob("nfl.sqlite.replaced-*"))
    # A backup of another sport's store is never restored as the NFL store.
    _store(data / "nba.sqlite", "nba").close()
    backup.run(data, dest, stamp="nba", sports=["nba"])
    other = json.loads((dest / "nba" / "manifest.json").read_text())
    (dest / "nba" / "manifest.json").write_text(json.dumps({**other, "stores": {"nfl": {**other["stores"]["nba"], "file": "nfl.sqlite"}}}))
    (dest / "nba" / "nba.sqlite").rename(dest / "nba" / "nfl.sqlite")
    with pytest.raises(backup.BackupError, match="not the nfl store"):
        backup.restore(dest / "nba", data, sports=["nfl"])


def test_the_cli_backs_up_and_restores_nfl_and_soccer(tmp_path, monkeypatch, capsys):
    data, dest = tmp_path / "data", tmp_path / "backups"
    for s in ("nfl", "soccer"):
        _data_store(data / f"{s}.sqlite", s).close()
    monkeypatch.setenv("NBA_DATA_DIR", str(data))
    backup.main(["--dest", str(dest), "--sport", "nfl", "--sport", "soccer"])
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "ok" and set(out["stores"]) == {"nfl", "soccer"}
    for s in ("nfl", "soccer"):
        assert out["stores"][s]["identity"] == s and out["stores"][s]["integrity"] == "ok" and out["stores"][s]["rows"]["teams"] == 1
    assert out["stores"]["soccer"]["rows"]["soccer_matches"] == 0
    folder = dest / out["dir"].rsplit("/", 1)[-1]
    assert sorted(p.name for p in folder.iterdir()) == ["manifest.json", "nfl.sqlite", "soccer.sqlite"]
    backup.main(["--restore", str(folder), "--sport", "nfl", "--sport", "soccer"])
    restored = json.loads(capsys.readouterr().out)
    assert {s: restored[s]["identity"] for s in restored} == {"nfl": "nfl", "soccer": "soccer"}
    assert all(restored[s]["sha256"] == out["stores"][s]["sha256"] for s in restored)


def test_a_default_backup_includes_every_existing_store_and_skips_absent_ones(tmp_path):
    data, dest = tmp_path / "data", tmp_path / "backups"
    data.mkdir()
    with pytest.raises(backup.BackupError, match="No store"):
        backup.run(data, dest, stamp="none")
    assert not (dest / "none").exists()
    for s in ("nfl", "soccer"):
        _data_store(data / f"{s}.sqlite", s).close()
    out = backup.run(data, dest, stamp="s")
    man = json.loads((dest / "s" / "manifest.json").read_text())
    assert set(out["stores"]) == set(man["stores"]) == {"nfl", "soccer"} and out["skipped"] == ["nba", "nhl", "ncaab"]
    assert sorted(p.name for p in (dest / "s").iterdir()) == ["manifest.json", "nfl.sqlite", "soccer.sqlite"]


def test_a_default_backup_with_the_nba_nhl_and_nfl_stores_present_backs_up_all_three(tmp_path):
    data, dest = tmp_path / "data", tmp_path / "backups"
    for s in ("nba", "nhl"):
        _store(data / f"{s}.sqlite", s).close()
    _data_store(data / "nfl.sqlite", "nfl").close()
    out = backup.run(data, dest, stamp="live")
    man = json.loads((dest / "live" / "manifest.json").read_text())
    assert set(man["stores"]) == {"nba", "nhl", "nfl"} and out["skipped"] == ["ncaab", "soccer"]
    assert {s: (m["file"], m["identity"], m["integrity"]) for s, m in man["stores"].items()} == {
        "nba": ("nba.sqlite", "nba", "ok"), "nhl": ("nhl.sqlite", "nhl", "ok"), "nfl": ("nfl.sqlite", "nfl", "ok")}
    assert sorted(p.name for p in (dest / "live").iterdir()) == ["manifest.json", "nba.sqlite", "nfl.sqlite", "nhl.sqlite"]
    # The NBA/NHL entries keep the manifest's existing fields.
    assert set(man["stores"]["nba"]) == {"file", "source", "bytes", "sha256", "integrity", "schema_version", "identity", "rows"}


def test_a_default_backup_warns_on_stderr_for_each_store_it_skips(tmp_path, capsys):
    data, dest = tmp_path / "data", tmp_path / "backups"
    _store(data / "nhl.sqlite", "nhl").close()
    _data_store(data / "nfl.sqlite", "nfl").close()
    out = backup.run(data, dest, stamp="s")
    assert set(out["stores"]) == {"nhl", "nfl"} and out["skipped"] == ["nba", "ncaab", "soccer"]
    err = capsys.readouterr().err.splitlines()
    assert err == [f"WARNING: no nba store at {data / 'nba.sqlite'}; not backed up",
                   f"WARNING: no ncaab store at {data / 'ncaab.sqlite'}; not backed up",
                   f"WARNING: no soccer store at {data / 'soccer.sqlite'}; not backed up"]
    backup.run(data, dest, stamp="named", sports=["nhl", "nfl"])           # named sports: nothing skipped, no warning
    assert capsys.readouterr().err == ""


def test_a_named_missing_store_is_refused_before_any_folder_is_made(tmp_path):
    data, dest = tmp_path / "data", tmp_path / "backups"
    _data_store(data / "nfl.sqlite", "nfl").close()
    for named in (["soccer"], ["nfl", "soccer"]):
        with pytest.raises(backup.BackupError, match="No soccer store"):
            backup.run(data, dest, stamp="x", sports=named)
        assert not (dest / "x").exists()


def test_a_restore_refuses_a_data_only_store_without_its_identity(tmp_path):
    data, dest = tmp_path / "data", tmp_path / "backups"
    _data_store(data / "nfl.sqlite", "nfl").close()
    backup.run(data, dest, stamp="s", sports=["nfl"])
    copy = sqlite3.connect(dest / "s" / "nfl.sqlite")
    copy.execute("DROP TRIGGER store_identity_no_delete")
    copy.execute("DELETE FROM store_identity")
    copy.commit()
    copy.close()
    man = json.loads((dest / "s" / "manifest.json").read_text())        # the manifest matches the altered file,
    man["stores"]["nfl"]["sha256"] = backup.sha256(dest / "s" / "nfl.sqlite")   # so only the identity check can refuse
    (dest / "s" / "manifest.json").write_text(json.dumps(man))
    before = backup.sha256(data / "nfl.sqlite")
    with pytest.raises(backup.BackupError, match="not the nfl store"):
        backup.restore(dest / "s", data, sports=["nfl"])
    assert backup.sha256(data / "nfl.sqlite") == before and not list(data.glob("*.replaced-*"))


def test_a_held_soccer_job_lock_refuses_a_soccer_restore(tmp_path):
    data, dest = tmp_path / "data", tmp_path / "backups"
    _data_store(data / "soccer.sqlite", "soccer").close()
    backup.run(data, dest, stamp="s", sports=["soccer"])
    fd = os.open(data / ".soccer-jobs.lock", os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        with pytest.raises(backup.BackupError, match="holds the soccer store lock"):
            backup.restore(dest / "s", data, sports=["soccer"])
    finally:
        os.close(fd)
    assert backup.restore(dest / "s", data, sports=["soccer"])["soccer"]["identity"] == "soccer"


def test_a_sport_named_twice_is_backed_up_and_restored_once(tmp_path, monkeypatch, capsys):
    data, dest = tmp_path / "data", tmp_path / "backups"
    _store(data / "nhl.sqlite", "nhl").close()
    monkeypatch.setenv("NBA_DATA_DIR", str(data))
    backup.main(["--dest", str(dest), "--sport", "nhl", "--sport", "nhl"])
    out = json.loads(capsys.readouterr().out)
    assert set(out["stores"]) == {"nhl"}
    backup.main(["--restore", out["dir"], "--sport", "nhl", "--sport", "nhl"])   # not refused by its own lock
    restored = json.loads(capsys.readouterr().out)
    assert set(restored) == {"nhl"} and restored["nhl"]["sha256"] == out["stores"]["nhl"]["sha256"]
    assert len(list(data.glob("nhl.sqlite.replaced-*"))) == 1


def test_two_restores_in_the_same_second_keep_both_safety_copies(tmp_path, monkeypatch):
    """The replaced store is never overwritten: a second restore within the same clock second keeps the first safety copy."""
    from datetime import datetime as real_datetime, timezone

    class Frozen(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime(2026, 11, 8, 6, 4, 29, 123456, tzinfo=timezone.utc)
    data, dest = tmp_path / "data", tmp_path / "backups"
    _store(data / "nhl.sqlite", "nhl").close()
    backup.run(data, dest, stamp="b", sports=["nhl"])
    monkeypatch.setattr(backup, "datetime", Frozen)
    replaced = []
    for mark in ("first", "second"):
        conn = db.connect(data / "nhl.sqlite")
        conn.execute("INSERT INTO teams (team_id, abbr, name, first_seen_at) VALUES (?, 'M', ?, '2026-11-08T00:00:00Z')",
                     (f"nhl:{mark}", mark))
        conn.close()
        replaced.append(backup.sha256(data / "nhl.sqlite"))
        backup.restore(dest / "b", data, sports=["nhl"])
    copies = sorted(data.glob("nhl.sqlite.replaced-*"))
    assert len(copies) == 2 and sorted(backup.sha256(p) for p in copies) == sorted(replaced), copies
    assert replaced[0] != replaced[1]


# A failure during the swap (step 3) is rolled back: every renamed file goes back, in reverse order, across all sports.
def _swap_setup(tmp_path):
    """nba + nfl backed up, then live files (main, -wal, -shm) with distinct bytes; restore never opens them."""
    data, dest = tmp_path / "data", tmp_path / "backups"
    _store(data / "nba.sqlite", "nba").close()
    _data_store(data / "nfl.sqlite", "nfl").close()
    backup.run(data, dest, stamp="b", sports=["nba", "nfl"])
    originals = {}
    for sport in ("nba", "nfl"):
        for part in ("", "-wal", "-shm"):
            p = data / f"{sport}.sqlite{part}"
            p.write_bytes(f"original {sport}{part}".encode())
            originals[p.name] = p.read_bytes()
    return data, dest / "b", originals


def _inject(monkeypatch, fail):
    """Path.rename raising OSError(5) when fail(source name, target name); every rename is recorded."""
    from pathlib import Path
    real, calls = Path.rename, []

    def rename(self, target):
        calls.append((Path(self).name, Path(target).name))
        if fail(Path(self).name, Path(target).name):
            raise OSError(5, "injected")
        return real(self, target)
    monkeypatch.setattr(Path, "rename", rename)
    return calls


def _forward(sport, stage):
    store = f"{sport}.sqlite"
    if stage == "install":
        return lambda a, b: (a, b) == (f"{store}.restoring", store)
    src = store + {"main": "", "wal": "-wal", "shm": "-shm"}[stage]
    return lambda a, b: a == src and b.startswith(f"{src}.replaced-")


def _files(data):
    return {p.name: p.read_bytes() for p in data.iterdir() if p.is_file() and not p.name.endswith("-jobs.lock")}


@pytest.mark.parametrize("stage", ["main", "wal", "shm", "install"])
@pytest.mark.parametrize("sport", ["nba", "nfl"])     # nfl: a later sport, after nba was fully swapped
def test_a_failed_swap_is_rolled_back_for_every_sport(tmp_path, monkeypatch, sport, stage):
    data, folder, originals = _swap_setup(tmp_path)
    calls = _inject(monkeypatch, _forward(sport, stage))
    synced, real_sync = [], backup._fsync_dir
    monkeypatch.setattr(backup, "_fsync_dir", lambda d: (synced.append(sorted(_files(data))), real_sync(d)))
    with pytest.raises(OSError, match="injected"):
        backup.restore(folder, data, sports=["nba", "nfl"])
    assert synced                                                 # the folder is synced after the rollback
    if sport == "nfl":
        assert ("nba.sqlite.restoring", "nba.sqlite") in calls    # the earlier sport was fully swapped first
    assert _files(data) == originals                              # originals back, -wal/-shm too; staged copies removed


def test_a_failed_rollback_deletes_nothing_and_names_the_manual_recovery(tmp_path, monkeypatch):
    import re
    data, folder, originals = _swap_setup(tmp_path)
    install_nfl = _forward("nfl", "install")
    _inject(monkeypatch, lambda a, b: install_nfl(a, b) or (a.startswith("nba.sqlite.replaced-") and b == "nba.sqlite"))
    with pytest.raises(backup.BackupError) as err:
        backup.restore(folder, data, sports=["nba", "nfl"])
    msg = str(err.value)
    assert isinstance(err.value.__cause__, OSError) and "injected" in str(err.value.__cause__)
    files = _files(data)
    restored = {s: (folder / f"{s}.sqlite").read_bytes() for s in ("nba", "nfl")}
    for s in ("nba", "nfl"):                                      # nothing deleted: both staged copies are kept
        assert files[f"{s}.sqlite.restoring"] == restored[s]
    assert sorted(originals.values()) == sorted(v for k, v in files.items() if not k.endswith(".restoring"))
    (replaced,) = data.glob("nba.sqlite.replaced-*")              # the rename back that failed: still a safety copy
    for s in ("nba", "nfl"):
        assert str(data / f"{s}.sqlite") in msg and str(data / f"{s}.sqlite.restoring") in msg
    assert str(replaced) in msg and f"move {replaced} to {data / 'nba.sqlite'}" in msg
    # The listed steps, done by hand in order, put every original back.
    steps = re.findall(r"^\s*\d+\. move (\S+) to (\S+)$", msg, flags=re.M)
    assert steps
    monkeypatch.undo()
    for a, b in steps:
        assert not os.path.lexists(b), (a, b)
        os.rename(a, b)
    assert {k: v for k, v in _files(data).items() if not k.endswith(".restoring")} == originals


def test_a_backup_is_mirrored_to_a_second_place_and_each_copy_is_checked_against_the_manifest(tmp_path):
    data, dest, mirror = tmp_path / "data", tmp_path / "backups", tmp_path / "offsite"
    _store(data / "nba.sqlite", "nba").close()
    mirror.mkdir()
    (tmp_path / "offsite2").mkdir()
    src = backup.run(data, dest, stamp="20261116T040000Z")["dir"]
    out = backup.mirror(src, mirror)
    copy = mirror / "20261116T040000Z"
    assert out["status"] == "ok" and (copy / "manifest.json").read_text() == (dest / "20261116T040000Z" / "manifest.json").read_text()
    assert backup.sha256(copy / "nba.sqlite") == json.loads((copy / "manifest.json").read_text())["stores"]["nba"]["sha256"]
    with pytest.raises(backup.BackupError):          # an existing mirror copy is never overwritten
        backup.mirror(src, mirror)
    # A corrupted source file is refused before anything is copied.
    (dest / "20261116T040000Z" / "nba.sqlite").write_bytes(b"not a store")
    with pytest.raises(backup.BackupError):
        backup.mirror(src, tmp_path / "offsite2")
    assert not (tmp_path / "offsite2" / "20261116T040000Z").exists()


def test_a_mirror_folder_that_does_not_exist_is_refused_not_created(tmp_path):
    # An unplugged disk or a missing cloud folder must fail loudly, not quietly create a "mirror" on the same disk.
    data, dest = tmp_path / "data", tmp_path / "backups"
    _store(data / "nba.sqlite", "nba").close()
    src = backup.run(data, dest, stamp="20261116T040000Z")["dir"]
    with pytest.raises(backup.BackupError):
        backup.mirror(src, tmp_path / "Volumes" / "Unplugged")
    assert not (tmp_path / "Volumes").exists()


# ---- research snapshot (five-sport-snapshot-v1) ----------------------------------------------------------------------------
def _games_store(path, sport, table, col, rows):
    c = sqlite3.connect(path)
    c.execute(f"CREATE TABLE {table} ({'match_id' if table == 'soccer_matches' else 'game_id'} TEXT, {col} TEXT, status TEXT, season INT)")
    c.executemany(f"INSERT INTO {table} VALUES (?,?,?,?)", rows)
    c.commit()
    return c


def test_snapshot_is_a_read_only_verified_five_sport_copy_with_coverage_and_a_checkable_identity(tmp_path):
    data, dest = tmp_path / "data", tmp_path / "snaps"
    data.mkdir()
    keep = []
    for sport, table, col in (("nba", "games", "tipoff_at"), ("nhl", "games", "tipoff_at"), ("ncaab", "games", "tipoff_at"),
                              ("nfl", "games", "tipoff_at"), ("soccer", "soccer_matches", "kickoff_at")):
        keep.append(_games_store(data / f"{sport}.sqlite", sport, table, col,
                                 [("a", "2026-10-01T00:00:00Z", "final", 2027), ("b", "2026-11-30T00:00:00Z", "scheduled", 2027)]))
    (data / "research" / "candidates").mkdir(parents=True)
    (data / "research" / "candidates" / "goalies-v1.json.gz").write_bytes(b"goalies")
    keep[0].execute("INSERT INTO games VALUES ('c', '2026-11-02T00:00:00Z', 'final', 2027)")      # a live writer is not blocked and is captured
    keep[0].commit()
    ident = backup.snapshot(data, dest, "snap-1")
    snap = dest / "snap-1"
    assert set(ident["stores"]) == {"nba", "nhl", "ncaab", "nfl", "soccer"} and ident["snapshot_version"] == backup.SNAPSHOT_VERSION
    cov = ident["stores"]["nba"]["coverage"]
    assert (cov["rows"], cov["first"], cov["last"], cov["last_final"], cov["seasons"]) == (3, "2026-10-01T00:00:00Z", "2026-11-30T00:00:00Z", "2026-11-02T00:00:00Z", [2027])
    assert ident["stores"]["soccer"]["coverage"]["time_column"] == "kickoff_at" and ident["stores"]["nfl"]["integrity_check"] == "ok"
    assert ident["files"]["research/candidates/goalies-v1.json.gz"]["bytes"] == 7
    assert json.loads((dest / "IDENTITY-snap-1.json").read_text()) == json.loads((snap / "IDENTITY.json").read_text())
    assert backup.verify_snapshot(snap) == {"ok": True, "problems": [], "name": "snap-1", "snapshot_version": backup.SNAPSHOT_VERSION}
    assert not any(os.access(p, os.W_OK) for p in [snap, *snap.rglob("*")])
    from app import autoresearch                                # the pinned evaluator accepts the read-only five-sport snapshot as identity
    os.environ["NBA_DATA_DIR"] = str(snap)
    try:
        assert autoresearch.snapshot_identity("ncaab")["sha256"] == ident["files"]["ncaab.sqlite"]["sha256"]
    finally:
        del os.environ["NBA_DATA_DIR"]
    with pytest.raises(backup.BackupError, match="already exists"):
        backup.snapshot(data, dest, "snap-1")
    os.chmod(snap, 0o755)
    os.chmod(snap / "nba.sqlite", 0o644)
    (snap / "nba.sqlite").write_bytes(b"tampered")
    res = backup.verify_snapshot(snap)
    assert not res["ok"] and any("sha256 differs: nba.sqlite" in p for p in res["problems"]) and any(p.startswith("writable") for p in res["problems"])


def test_snapshot_refuses_a_missing_store_and_leaves_nothing_behind(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    _games_store(data / "nba.sqlite", "nba", "games", "tipoff_at", [])
    with pytest.raises(backup.BackupError, match="no store for nhl"):
        backup.snapshot(data, tmp_path / "snaps", "s", sports=["nba", "nhl"])
    assert not (tmp_path / "snaps" / "s").exists()


def _soccer_live_store(path):
    conn = db.connect(path)
    db.migrate(conn)
    conn.close()


def test_snapshot_soccer_backfill_is_resumable_bounded_counted_and_never_touches_the_live_store(tmp_path):
    data, dest = tmp_path / "data", tmp_path / "snaps"
    data.mkdir()
    _soccer_live_store(data / "soccer.sqlite")
    live_before = backup.sha256(data / "soccer.sqlite")
    asked = []

    def fetch(comp, month, timeout=60):
        asked.append((comp, month))
        return json.dumps({"leagues": [{"slug": comp}], "events": []}).encode()
    cfg = {"from": "201008", "to": "201010", "competitions": ["eng.1", "esp.1"], "sleep": 0, "max_requests": 4}
    first = backup.snapshot(data, dest, "s", sports=["soccer"], soccer_backfill=cfg, soccer_fetch=fetch)
    assert first["status"] == "partial" and first["requested"] == 4 and first["pending"] == 2 and not (dest / "s").exists()
    assert (dest / "s.partial").is_dir() and not (dest / "IDENTITY-s.json").exists()
    ident = backup.snapshot(data, dest, "s", sports=["soccer"], soccer_backfill={**cfg, "max_requests": 4}, soccer_fetch=fetch)
    assert len(asked) == 6 and len(set(asked)) == 6                    # the resumed run asked only for the missing months
    assert ident["soccer_backfill"]["requested"] == 6 and ident["soccer_backfill"]["failed"] == [] and ident["soccer_backfill"]["runs"] == 2
    assert ident["soccer_backfill"]["competitions"] == ["eng.1", "esp.1"] and not (dest / "s.partial").exists()
    assert backup.sha256(data / "soccer.sqlite") == live_before   # live store file unchanged (a reader may add -wal/-shm)
    assert not (data / "raw").exists()                                                                 # raw cache went to the stage
    assert backup.verify_snapshot(dest / "s")["ok"], backup.verify_snapshot(dest / "s")
    conn = sqlite3.connect(f"file:{dest / 's' / 'soccer.sqlite'}?mode=ro&immutable=1", uri=True)
    assert conn.execute("SELECT COUNT(*) FROM raw_responses WHERE source LIKE '%month%'").fetchone()[0] == 6


def test_snapshot_backfill_failures_keep_the_stage_and_are_not_finalised(tmp_path):
    data, dest = tmp_path / "data", tmp_path / "snaps"
    data.mkdir()
    _soccer_live_store(data / "soccer.sqlite")

    def bad(comp, month, timeout=60):
        raise OSError("espn down")
    r = backup.snapshot(data, dest, "s", sports=["soccer"], soccer_fetch=bad,
                        soccer_backfill={"from": "201008", "to": "201008", "competitions": ["eng.1"], "sleep": 0})
    assert r["status"] == "partial" and r["failed"] == ["eng.1:201008"] and (dest / "s.partial").is_dir() and not (dest / "s").exists()


# ---- restoring a database that sits beside the stores (research/forward-candidates.sqlite, scorecards, ...) -------------------
def _night_with_forward(tmp_path, rows=4):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("backup_nightly", Path(__file__).resolve().parents[2] / "scripts" / "backup_nightly.py")
    nb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(nb)
    data, root = tmp_path / "data", tmp_path / "backups"
    _store(data / "nba.sqlite", "nba").close()
    fwd = data / "research" / "forward-candidates.sqlite"
    fwd.parent.mkdir(parents=True)
    c = sqlite3.connect(fwd)
    c.execute("CREATE TABLE rf_forecasts (id INTEGER PRIMARY KEY, v TEXT)")
    c.execute("CREATE TABLE rf_grades (id INTEGER PRIMARY KEY, v TEXT)")
    c.executemany("INSERT INTO rf_forecasts (v) VALUES (?)", [(f"r{i}",) for i in range(rows)])
    c.execute("INSERT INTO rf_grades (v) VALUES ('g')")
    c.commit(); c.close()
    nb.nightly(data, root, mirror=None, stamp="20261116T043000Z", ledger=tmp_path / "none.sqlite", repo_root=tmp_path / "r")
    return data, root / "stores" / "20261116T043000Z"


def test_a_restore_puts_the_forward_store_back_verified_and_keeps_the_replaced_file(tmp_path):
    data, night = _night_with_forward(tmp_path)
    live = data / "research" / "forward-candidates.sqlite"
    live.write_bytes(b"damaged")                                  # what a restore is for
    out = backup.restore_databases(night, data, ["research/forward-candidates.sqlite"])
    r = out["research/forward-candidates.sqlite"]
    assert r["integrity"] == "ok" and r["rows"] == {"rf_forecasts": 4, "rf_grades": 1}
    assert r["sha256"] == backup.sha256(live)
    aside = [p for p in live.parent.iterdir() if ".replaced-" in p.name]
    assert len(aside) == 1 and aside[0].read_bytes() == b"damaged"
    assert sqlite3.connect(live).execute("SELECT COUNT(*) FROM rf_forecasts").fetchone()[0] == 4


def test_a_restore_into_an_empty_folder_creates_the_file_and_a_tampered_copy_is_refused_before_anything_changes(tmp_path):
    data, night = _night_with_forward(tmp_path)
    scratch = tmp_path / "scratch"
    out = backup.restore_databases(night, scratch, ["research/forward-candidates.sqlite"])
    assert (scratch / "research" / "forward-candidates.sqlite").is_file() and out["research/forward-candidates.sqlite"]["rows"]["rf_forecasts"] == 4
    copy = night / "databases" / "research" / "forward-candidates.sqlite"
    c = sqlite3.connect(copy)
    c.execute("INSERT INTO rf_forecasts (v) VALUES ('extra')"); c.commit(); c.close()   # file no longer matches its sha256
    other = tmp_path / "other"
    with pytest.raises(backup.BackupError, match="does not match"):
        backup.restore_databases(night, other, ["research/forward-candidates.sqlite"])
    assert not other.exists() or not any(other.rglob("*.sqlite*"))


def test_a_restore_refuses_a_row_count_that_differs_from_the_manifest_and_a_night_that_never_held_the_database(tmp_path):
    data, night = _night_with_forward(tmp_path)
    mpath = night / backup.DATABASES_MANIFEST
    man = json.loads(mpath.read_text())
    man["databases"]["research/forward-candidates.sqlite"]["rows"]["rf_forecasts"] = 99
    mpath.write_text(json.dumps(man))
    with pytest.raises(backup.BackupError, match="rows"):
        backup.restore_databases(night, tmp_path / "scratch", ["research/forward-candidates.sqlite"])
    with pytest.raises(backup.BackupError, match="no research/challengers.sqlite"):
        backup.restore_databases(night, tmp_path / "scratch", ["research/challengers.sqlite"])
    with pytest.raises(backup.BackupError, match="not a path inside"):
        backup.restore_databases(night, tmp_path / "scratch", ["../escape.sqlite"])


def test_the_cli_restores_a_database_with_database_and_needs_no_sport(tmp_path, monkeypatch, capsys):
    data, night = _night_with_forward(tmp_path)
    scratch = tmp_path / "scratch"
    monkeypatch.setenv("NBA_DATA_DIR", str(scratch))
    backup.main(["--restore", str(night), "--database", "research/forward-candidates.sqlite"])
    out = json.loads(capsys.readouterr().out)
    assert out["databases"]["research/forward-candidates.sqlite"]["rows"]["rf_forecasts"] == 4 and out["stores"] == {}
    assert (scratch / "research" / "forward-candidates.sqlite").is_file()
