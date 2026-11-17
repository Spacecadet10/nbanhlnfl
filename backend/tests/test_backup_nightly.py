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
