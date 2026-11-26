"""Nightly backup (scripts/backup_nightly.py): journal export, a verified store backup with the journal inside it, and a
mirror to a second place. Synthetic stores under tmp_path only."""
import importlib.util
import json
import tarfile
from pathlib import Path

from app import backup, db, ledger
from test_storage import T0, event, payload, run

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('backup_nightly', ROOT / 'scripts' / 'backup_nightly.py')
nb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nb)


def _store(data):
    conn = db.connect(data / "nba.sqlite")
    db.migrate(conn)
    run(conn, payload(event()), T0)
    ledger.record(conn, ledger.Forecast("espn:1001", "live", "m1", "d1", "f1", "2024-01-15T17:00:00Z",
                                        "2024-01-15T16:59:00Z", 0.6))
    conn.close()


def test_a_nightly_run_backs_up_the_stores_and_journal_and_mirrors_them(tmp_path):
    data, root, offsite = tmp_path / "data", tmp_path / "backups", tmp_path / "offsite"
    _store(data)
    offsite.mkdir()
    out = nb.nightly(data, root, mirror=offsite, stamp="20261116T043000Z")
    assert out["status"] == "ok" and out["journal_verify_ok"] is True
    local = root / "stores" / "20261116T043000Z"
    for folder in (local, offsite / "20261116T043000Z"):
        man = json.loads((folder / "manifest.json").read_text())
        assert backup.sha256(folder / "nba.sqlite") == man["stores"]["nba"]["sha256"]
        with tarfile.open(folder / "journal.tar.gz") as t:
            line = json.loads(t.extractfile("./nba/predictions.jsonl").read().decode().splitlines()[0])
        assert line["row"]["home_win_prob"] == 0.6
    assert (root / "journal" / "nba" / "predictions.jsonl").is_file()


def test_without_a_mirror_the_run_still_backs_up_but_reports_it_is_not_off_machine(tmp_path):
    data, root = tmp_path / "data", tmp_path / "backups"
    _store(data)
    out = nb.nightly(data, root, mirror=None, stamp="20261116T043000Z")
    assert out["status"] == "no_mirror" and (root / "stores" / "20261116T043000Z" / "manifest.json").is_file()


def test_the_snapshot_also_holds_the_ledger_recovered_history_rules_artifacts_and_config_but_never_a_secret(tmp_path):
    import sqlite3
    data, root = tmp_path / "data", tmp_path / "backups"
    _store(data)
    ledger_path = tmp_path / ".telegram-delivery.sqlite"
    c = sqlite3.connect(ledger_path)
    c.execute("CREATE TABLE deliveries (delivery_key TEXT, text TEXT)")
    c.execute("INSERT INTO deliveries VALUES ('k', 'card')")
    c.commit(); c.close()
    repo, agents = tmp_path / "repo", tmp_path / "agents"
    files = {repo / "docs" / "telegram_record.json": "{}", repo / "docs" / "preregistration" / "p.md": "rule",
             data / "research" / "line-state-nba.pkl": "model", data / "reports" / "weekly_report_review.json": "{}",
             agents / "com.nba-predictor.x.plist": "<plist/>", agents / "com.google.other.plist": "<plist/>",
             repo / ".env.telegram": "SECRET-TOKEN", data / "research" / ".env.odds": "SECRET-KEY"}
    for p, text in files.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    out = nb.nightly(data, root, mirror=None, stamp="20261116T043000Z", ledger=ledger_path, repo_root=repo, launch_agents=agents)
    folder = root / "stores" / "20261116T043000Z"
    copy = sqlite3.connect(folder / "ledger.sqlite")
    assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert copy.execute("SELECT text FROM deliveries").fetchone()[0] == "card"
    copy.close()
    assert out["extras"]["ledger"]["sha256"] == backup.sha256(folder / "ledger.sqlite")
    with tarfile.open(folder / "files.tar.gz") as t:
        names = set(t.getnames())
        assert t.extractfile("repo/docs/telegram_record.json").read() == b"{}"
    assert {"repo/docs/preregistration/p.md", "data/research/line-state-nba.pkl", "data/reports/weekly_report_review.json",
            "launch_agents/com.nba-predictor.x.plist"} <= names
    assert not any(".env" in n or "google" in n for n in names)


def test_the_snapshot_also_holds_adaptive_pickles_scorecard_dbs_and_thresholds_db_but_no_locks_or_secrets(tmp_path):
    import sqlite3
    data, root = tmp_path / "data", tmp_path / "backups"
    _store(data)
    for rel, text in {"research/adaptive/nba/history-ab.pkl": "pk", "research/adaptive/nba/history-ab.pkl.sha256": "eb3102a6cb586765d01fad324523ec0bc67b9efd6a2d9589c135adfedf7922cc",
                      "research/adaptive/nhl/combiner/c-1.pkl": "cb", "research/adaptive/nba/train.lock": "",
                      "research/adaptive/nba/history-ab.pkl.lock": "", "research/adaptive/.env.x": "SECRET"}.items():
        p = data / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    (data / "scorecard").mkdir()
    for name in ("scorecard/nhl.sqlite", "card_thresholds.sqlite", "pick_snapshots.sqlite"):
        c = sqlite3.connect(data / name)
        c.execute("CREATE TABLE scorecard (k TEXT)")
        c.executemany("INSERT INTO scorecard VALUES (?)", [("a",), ("b",)])
        c.commit(); c.close()
    out = nb.nightly(data, root, mirror=None, stamp="20261116T043000Z", ledger=tmp_path / "none.sqlite", repo_root=tmp_path / "r")
    folder = root / "stores" / "20261116T043000Z"
    with tarfile.open(folder / "files.tar.gz") as t:
        names = set(t.getnames())
    assert not any("research/adaptive" in n for n in names)                         # pickles are in the pool, not the tarball
    assert {"adaptive/nba/history-ab.pkl", "adaptive/nba/history-ab.pkl.sha256", "adaptive/nhl/combiner/c-1.pkl"} == \
        set(json.loads((folder / "pool-manifest.json").read_text())["files"])
    assert not any(p.name.endswith(".lock") or ".env" in p.name for p in (root / "pool").rglob("*"))
    assert not any(n.endswith(".lock") or ".env" in n for n in names)
    dbs = out["extras"]["databases"]
    assert set(dbs) == {"scorecard/nhl.sqlite", "card_thresholds.sqlite", "pick_snapshots.sqlite"}
    for rel, info in dbs.items():
        copy = sqlite3.connect(folder / "databases" / rel)
        assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok" == info["integrity"]
        assert info["rows"] == {"scorecard": 2} and info["sha256"] == backup.sha256(folder / "databases" / rel)
        copy.close()


# ---- retention: report-only by default, deletes only with apply ------------------------------------------------------------
from datetime import datetime, timedelta, timezone  # noqa: E402

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)


def fake_backups(root, ages_days, broken=()):
    """One fake backup folder per age (days before NOW, fractions allowed): a store file plus a manifest holding its sha256."""
    names = {}
    for age in ages_days:
        name = (NOW - timedelta(days=age)).strftime("%Y%m%dT%H%M%SZ")
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "nba.sqlite").write_bytes(f"store-{name}".encode())
        sha = backup.sha256(folder / "nba.sqlite")
        (folder / "manifest.json").write_text(json.dumps({"stores": {"nba": {"file": "nba.sqlite", "sha256": sha}}}))
        if age in broken:
            (folder / "nba.sqlite").write_bytes(b"damaged")
        names[age] = name
    return names


def test_prune_keeps_14_days_then_newest_per_iso_week_for_8_weeks_then_newest_per_month_for_12_months(tmp_path):
    ages = [0.1, 1, 5, 13.4,            # last 14 days: all kept
            15, 16, 20, 27,             # weeks of Nov 2-8 (ages 14-20) and Oct 26-Nov 1 (21-27): newest per ISO week
            50, 53,                     # same week, 7 weeks back: newest kept
            100, 105, 110,              # beyond 8 weeks: newest per month only
            200, 400]                   # 400 days: older than 12 months
    names = fake_backups(tmp_path, ages)
    plan = nb.prune(tmp_path, now=NOW)
    kept = set(plan["keep"])
    assert plan["mode"] == "report-only" and plan["deleted"] == [] and all((tmp_path / n).is_dir() for n in names.values())
    for age in (0.1, 1, 5, 13.4):
        assert names[age] in kept
    # ages 15, 16, 20 = Nov 7, 6, 2: one ISO week (Nov 2-8), newest kept; 27 = Oct 26 starts the week before
    assert names[15] in kept and names[16] not in kept and names[20] not in kept and names[27] in kept
    # 50 and 53 = Oct 3 and Sep 30: one ISO week, but also two months: newest of each month kept
    assert names[50] in kept and names[53] in kept
    # beyond 8 weeks only the newest of each month survives (100/105/110 = Aug 14, Aug 9, Aug 4)
    assert names[100] in kept and names[105] not in kept and names[110] not in kept
    assert names[200] in kept                                    # May 6: its month's only (and newest) backup
    assert names[400] not in kept and names[400] in plan["would_delete"]


def test_prune_report_only_deletes_nothing_and_apply_deletes_exactly_the_reported_folders(tmp_path):
    names = fake_backups(tmp_path, [0.1, 15, 16, 400])
    report = nb.prune(tmp_path, now=NOW)
    assert report["would_delete"] and all((tmp_path / n).is_dir() for n in names.values())
    done = nb.prune(tmp_path, now=NOW, apply=True)
    assert sorted(done["deleted"]) == sorted(report["would_delete"]) and done["mode"] == "apply"
    assert {p.name for p in tmp_path.iterdir()} == set(report["keep"])


def test_prune_never_deletes_the_newest_or_the_only_copy_in_a_bucket_even_if_it_fails_verification(tmp_path):
    names = fake_backups(tmp_path, [400, 0.1, 15, 16], broken=(0.1, 16))
    plan = nb.prune(tmp_path, now=NOW)
    keep = set(plan["keep"])
    assert names[0.1] in keep                                   # the newest, though damaged
    assert names[15] in keep                                    # newest verified of its week
    assert names[16] in plan["would_delete"]                    # damaged and not the only copy: deletable
    # a damaged folder alone in its week/month is the only copy: kept
    only = fake_backups(tmp_path / "b", [0.1, 30], broken=(30,))
    assert only[30] in nb.prune(tmp_path / "b", now=NOW)["keep"]
    # and the newest VERIFIED backup is always kept
    assert names[15] in keep


def test_prune_falls_back_to_an_older_verified_backup_when_the_newest_of_a_week_is_damaged(tmp_path):
    names = fake_backups(tmp_path, [0.1, 15, 16], broken=(15,))  # 15 and 16 days: same ISO week
    plan = nb.prune(tmp_path, now=NOW)
    assert names[16] in plan["keep"] and names[15] in plan["would_delete"]


def test_prune_ignores_folders_that_are_not_backup_stamps(tmp_path):
    fake_backups(tmp_path, [0.1])
    (tmp_path / "notes").mkdir()
    (tmp_path / ".20261101T000000Z.partial").mkdir()
    plan = nb.prune(tmp_path, now=NOW, apply=True)
    assert (tmp_path / "notes").is_dir() and (tmp_path / ".20261101T000000Z.partial").is_dir() and plan["deleted"] == []


def test_nightly_reports_the_prune_plan_but_deletes_only_with_the_flag(tmp_path):
    data, root = tmp_path / "data", tmp_path / "backups"
    _store(data)
    old = root / "stores" / "20250101T000000Z"
    old.mkdir(parents=True)
    (old / "manifest.json").write_text(json.dumps({"stores": {}}))
    out = nb.nightly(data, root, mirror=None, stamp="20261116T043000Z")
    assert out["prune"]["mode"] == "report-only" and "20250101T000000Z" in out["prune"]["would_delete"] and old.is_dir()
    out = nb.nightly(data, root, mirror=None, stamp="20261117T043000Z", prune_apply=True)
    assert out["prune"]["mode"] == "apply" and not old.exists()


# Off-machine copy (owner, Nov 22): an external drive protects a backup only when it is connected and the copy succeeds.
# A missing drive or a failed copy keeps the local backup, returns status mirror_failed with the reason, and raises a
# warning the owner sees (macOS notification + a WARNING line in the job log); a copy is checked file by file.

def test_a_missing_drive_keeps_the_local_backup_and_raises_a_visible_warning(tmp_path):
    data, root = tmp_path / "data", tmp_path / "backups"
    _store(data)
    seen = []
    out = nb.nightly(data, root, mirror=tmp_path / "Volumes" / "Backup" / "nba", stamp="20261116T043000Z", notify=seen.append)
    assert out["status"] == "mirror_failed" and out["mirror"] is None
    assert "does not exist" in out["mirror_error"]
    assert (root / "stores" / "20261116T043000Z" / "manifest.json").is_file()
    assert not (tmp_path / "Volumes").exists()                         # never created on the local disk
    assert len(seen) == 1 and "NOT copied off this Mac" in seen[0] and out["warning"] == seen[0]


def test_no_mirror_configured_also_warns(tmp_path):
    data, root = tmp_path / "data", tmp_path / "backups"
    _store(data)
    seen = []
    out = nb.nightly(data, root, mirror=None, stamp="20261116T043000Z", notify=seen.append)
    assert out["status"] == "no_mirror" and len(seen) == 1 and "NOT copied off this Mac" in seen[0]


def test_a_successful_copy_matches_every_file_restores_from_the_drive_and_does_not_warn(tmp_path):
    data, root, drive = tmp_path / "data", tmp_path / "backups", tmp_path / "drive"
    _store(data)
    drive.mkdir()
    seen = []
    out = nb.nightly(data, root, mirror=drive, stamp="20261116T043000Z", notify=seen.append)
    assert out["status"] == "ok" and not seen and "warning" not in out
    src, dst = root / "stores" / "20261116T043000Z", drive / "20261116T043000Z"
    names = sorted(p.relative_to(src) for p in src.rglob("*") if p.is_file())
    assert names == sorted(p.relative_to(dst) for p in dst.rglob("*") if p.is_file())
    assert all(backup.sha256(src / n) == backup.sha256(dst / n) for n in names)
    assert out["mirror_verified_files"] == len(names)
    target = tmp_path / "restored"
    target.mkdir()
    backup.restore(dst, target, sports=["nba"])
    conn = db.connect(target / "nba.sqlite")
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("SELECT home_win_prob FROM predictions").fetchone()[0] == 0.6


def test_a_corrupted_copy_is_reported_as_failed(tmp_path, monkeypatch):
    data, root, drive = tmp_path / "data", tmp_path / "backups", tmp_path / "drive"
    _store(data)
    drive.mkdir()
    real = nb.backup.mirror

    def bad_mirror(folder, mroot):
        res = real(folder, mroot)
        (Path(res["dir"]) / "journal.tar.gz").write_bytes(b"damaged")
        return res
    monkeypatch.setattr(nb.backup, "mirror", bad_mirror)
    seen = []
    out = nb.nightly(data, root, mirror=drive, stamp="20261116T043000Z", notify=seen.append)
    assert out["status"] == "mirror_failed" and "journal.tar.gz" in out["mirror_error"] and len(seen) == 1


# ---- shared pool for the immutable adaptive pickles; stale partial mirror copy ---------------------------------------------
import hashlib  # noqa: E402


def adaptive(data, files):
    for rel, blob in files.items():
        p = data / "research" / "adaptive" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(blob)
        (p.parent / (p.name + ".sha256")).write_text(hashlib.sha256(blob).hexdigest() + "\n")


def pool_files(base):
    return {str(p.relative_to(base)) for p in base.rglob("*") if p.is_file()}


def test_pickles_go_to_a_shared_pool_once_and_not_into_every_nights_tarball(tmp_path):
    data, root = tmp_path / "data", tmp_path / "backups"
    _store(data)
    adaptive(data, {"nba/history-a.pkl": b"A" * 1000, "nhl/combiner/c-1.pkl": b"C" * 500})
    one = nb.nightly(data, root, mirror=None, stamp="20261116T043000Z", repo_root=tmp_path / "r", notify=lambda m: None)
    assert one["pool"]["copied"] == 4 and one["pool"]["conflicts"] == []          # 2 pickles + 2 sidecars
    two = nb.nightly(data, root, mirror=None, stamp="20261117T043000Z", repo_root=tmp_path / "r", notify=lambda m: None)
    assert two["pool"]["copied"] == 0 and two["pool"]["present"] == 4
    adaptive(data, {"nba/history-b.pkl": b"B" * 10})                              # a new pickle: only it is copied
    three = nb.nightly(data, root, mirror=None, stamp="20261118T043000Z", repo_root=tmp_path / "r", notify=lambda m: None)
    assert three["pool"]["copied"] == 2
    assert (root / "pool" / "adaptive" / "nba" / "history-a.pkl").read_bytes() == b"A" * 1000
    for stamp in ("20261116T043000Z", "20261118T043000Z"):
        with tarfile.open(root / "stores" / stamp / "files.tar.gz") as t:
            assert not any("research/adaptive" in n for n in t.getnames())
    man = json.loads((root / "stores" / "20261118T043000Z" / "pool-manifest.json").read_text())
    assert man["files"]["adaptive/nba/history-b.pkl"] == hashlib.sha256(b"B" * 10).hexdigest()
    assert len(man["files"]) == 6


def test_a_changed_pickle_under_the_same_name_is_reported_and_never_overwrites_the_pool(tmp_path):
    data, root, warned = tmp_path / "data", tmp_path / "backups", []
    _store(data)
    adaptive(data, {"nba/history-a.pkl": b"A" * 100})
    nb.nightly(data, root, mirror=None, stamp="20261116T043000Z", repo_root=tmp_path / "r", notify=warned.append)
    adaptive(data, {"nba/history-a.pkl": b"X" * 100})
    out = nb.nightly(data, root, mirror=None, stamp="20261117T043000Z", repo_root=tmp_path / "r", notify=warned.append)
    assert out["pool"]["conflicts"] == ["adaptive/nba/history-a.pkl", "adaptive/nba/history-a.pkl.sha256"]
    assert (root / "pool" / "adaptive" / "nba" / "history-a.pkl").read_bytes() == b"A" * 100
    assert any("history-a.pkl" in w for w in warned)
    man = json.loads((root / "stores" / "20261117T043000Z" / "pool-manifest.json").read_text())
    assert "adaptive/nba/history-a.pkl" not in man["files"] or man["files"]["adaptive/nba/history-a.pkl"] == hashlib.sha256(b"A" * 100).hexdigest()


def test_a_pickle_that_does_not_match_its_sidecar_is_not_pooled(tmp_path):
    data, root = tmp_path / "data", tmp_path / "backups"
    _store(data)
    adaptive(data, {"nba/history-a.pkl": b"A" * 100})
    (data / "research/adaptive/nba/history-a.pkl.sha256").write_text("0" * 64 + "\n")
    out = nb.nightly(data, root, mirror=None, stamp="20261116T043000Z", repo_root=tmp_path / "r", notify=lambda m: None)
    assert out["pool"]["conflicts"] == ["adaptive/nba/history-a.pkl"] and not (root / "pool/adaptive/nba/history-a.pkl").exists()


def test_the_mirror_pool_receives_only_new_files_and_a_restore_collects_the_referenced_set_verified(tmp_path):
    data, root, offsite = tmp_path / "data", tmp_path / "backups", tmp_path / "offsite"
    _store(data)
    offsite.mkdir()
    adaptive(data, {"nba/history-a.pkl": b"A" * 1000})
    one = nb.nightly(data, root, mirror=offsite, stamp="20261116T043000Z", repo_root=tmp_path / "r", notify=lambda m: None)
    assert one["status"] == "ok" and one["pool_mirrored"]["copied"] == 2
    two = nb.nightly(data, root, mirror=offsite, stamp="20261117T043000Z", repo_root=tmp_path / "r", notify=lambda m: None)
    assert two["pool_mirrored"]["copied"] == 0
    adaptive(data, {"nba/history-b.pkl": b"B" * 20})
    three = nb.nightly(data, root, mirror=offsite, stamp="20261118T043000Z", repo_root=tmp_path / "r", notify=lambda m: None)
    assert three["pool_mirrored"]["copied"] == 2
    assert pool_files(offsite / "pool") == pool_files(root / "pool")
    # restore from the off-machine copy: the pool of that night's manifest, hashes verified
    got = nb.collect_pool(offsite / "20261117T043000Z", offsite / "pool", tmp_path / "restored")
    assert got == 2 and (tmp_path / "restored/adaptive/nba/history-a.pkl").read_bytes() == b"A" * 1000
    (offsite / "pool/adaptive/nba/history-a.pkl").write_bytes(b"damaged")
    import pytest
    with pytest.raises(backup.BackupError):
        nb.collect_pool(offsite / "20261117T043000Z", offsite / "pool", tmp_path / "restored2")


def test_a_stale_partial_mirror_copy_is_removed_but_completed_and_newer_folders_are_not(tmp_path):
    data, root, offsite = tmp_path / "data", tmp_path / "backups", tmp_path / "offsite"
    _store(data)
    offsite.mkdir()
    for name in (".20261116T043000Z.partial", ".20261101T000000Z.partial"):          # same stamp, older stamp
        (offsite / name).mkdir()
        (offsite / name / "half").write_text("x")
    (offsite / ".20261201T000000Z.partial").mkdir()                                  # newer than tonight: not ours to judge
    (offsite / "20261101T000000Z").mkdir()                                           # a completed folder
    out = nb.nightly(data, root, mirror=offsite, stamp="20261116T043000Z", repo_root=tmp_path / "r", notify=lambda m: None)
    assert out["status"] == "ok" and (offsite / "20261116T043000Z" / "manifest.json").is_file()
    assert not (offsite / ".20261116T043000Z.partial").exists() and not (offsite / ".20261101T000000Z.partial").exists()
    assert (offsite / ".20261201T000000Z.partial").is_dir() and (offsite / "20261101T000000Z").is_dir()


def test_a_failed_pool_copy_keeps_the_backup_and_warns_instead_of_crashing(tmp_path, monkeypatch):
    data, root = tmp_path / "data", tmp_path / "backups"
    _store(data)

    def broken(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(nb, "_pool", broken)
    seen = []
    out = nb.nightly(data, root, mirror=None, stamp="20261116T043000Z", notify=seen.append)
    assert "disk full" in out["pool"]["error"] and (root / "stores" / "20261116T043000Z" / "manifest.json").is_file()
    assert any("model pickles were NOT backed up" in m for m in seen)


# ---- the forward-testing evidence (research/forward-candidates.sqlite) is in every night's backup -----------------------------
def _forward_db(path, rows=5, table="rf_forecasts"):
    import sqlite3
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, v TEXT)")
    c.execute("CREATE TABLE rf_grades (id INTEGER PRIMARY KEY, v TEXT)")
    c.executemany(f"INSERT INTO {table} (v) VALUES (?)", [(f"r{i}",) for i in range(rows)])
    c.commit(); c.close()


def test_the_forward_candidate_store_and_the_challenger_store_are_copied_each_night_with_a_databases_manifest(tmp_path):
    import sqlite3
    data, root = tmp_path / "data", tmp_path / "backups"
    _store(data)
    _forward_db(data / "research" / "forward-candidates.sqlite", rows=5)
    _forward_db(data / "research" / "challengers.sqlite", rows=2, table="forecasts")
    out = nb.nightly(data, root, mirror=None, stamp="20261116T043000Z", ledger=tmp_path / "none.sqlite", repo_root=tmp_path / "r")
    folder = root / "stores" / "20261116T043000Z"
    dbs = out["extras"]["databases"]
    assert {"research/forward-candidates.sqlite", "research/challengers.sqlite"} <= set(dbs)
    fwd = dbs["research/forward-candidates.sqlite"]
    assert fwd["integrity"] == "ok" and fwd["rows"] == {"rf_forecasts": 5, "rf_grades": 0}
    assert fwd["sha256"] == backup.sha256(folder / "databases" / "research" / "forward-candidates.sqlite")
    # the same facts are written into the night's folder, so a restore needs nothing but the folder
    man = json.loads((folder / backup.DATABASES_MANIFEST).read_text())
    assert man["databases"] == dbs
    # the source is untouched and still writable
    c = sqlite3.connect(data / "research" / "forward-candidates.sqlite")
    c.execute("INSERT INTO rf_forecasts (v) VALUES ('later')"); c.commit(); c.close()


def test_a_missing_forward_store_is_not_an_error_and_the_manifest_lists_only_what_exists(tmp_path):
    data, root = tmp_path / "data", tmp_path / "backups"
    _store(data)
    out = nb.nightly(data, root, mirror=None, stamp="20261116T043000Z", ledger=tmp_path / "none.sqlite", repo_root=tmp_path / "r")
    assert "databases" not in out["extras"]
    assert not (root / "stores" / "20261116T043000Z" / backup.DATABASES_MANIFEST).exists()


def test_the_databases_manifest_travels_with_the_mirror_and_is_checked_there(tmp_path):
    data, root, offsite = tmp_path / "data", tmp_path / "backups", tmp_path / "offsite"
    _store(data)
    offsite.mkdir()
    _forward_db(data / "research" / "forward-candidates.sqlite")
    out = nb.nightly(data, root, mirror=offsite, stamp="20261116T043000Z", ledger=tmp_path / "none.sqlite", repo_root=tmp_path / "r")
    assert out["status"] == "ok"
    copy = offsite / "20261116T043000Z"
    assert (copy / backup.DATABASES_MANIFEST).is_file() and (copy / "databases" / "research" / "forward-candidates.sqlite").is_file()
    restored = backup.restore_databases(copy, tmp_path / "scratch", ["research/forward-candidates.sqlite"])
    assert restored["research/forward-candidates.sqlite"]["rows"]["rf_forecasts"] == 5
