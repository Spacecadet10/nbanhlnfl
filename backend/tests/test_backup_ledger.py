"""Daily ledger backup (scripts/backup_ledger.py): a consistent copy, never overwritten, verified by restoring it into a
temporary folder and opening it with the app's own ledger code."""
import importlib.util
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('backup_ledger', ROOT / 'scripts' / 'backup_ledger.py')
bl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bl)
NOW = datetime(2026, 11, 11, 9, 30, tzinfo=timezone.utc)


def ledger(tmp_path):
    from app import telegram
    path = tmp_path / '.telegram-delivery.sqlite'
    with telegram.ledger(path) as conn:
        conn.execute("INSERT INTO deliveries (delivery_key,chat_id,text,status,attempted_at) VALUES ('k','1','t','sent','x')")
    return path


def test_backup_is_verified_by_a_restore_and_never_overwritten(tmp_path, monkeypatch):
    monkeypatch.delenv('NBA_RELEASE_GUARD', raising=False)
    path = ledger(tmp_path)
    made = bl.backup(path, tmp_path / 'b', NOW)
    assert made['expected']['deliveries'] == 1 and Path(made['backup']).stat().st_mode & 0o077 == 0
    check = bl.verify_restore(Path(made['backup']), made['expected'])
    assert check['status'] == 'verified' and check['sent_deliveries'] == 1 and check['problems'] == []
    with pytest.raises(SystemExit):
        bl.backup(path, tmp_path / 'b', NOW)


def test_a_backup_missing_rows_fails_the_restore_check(tmp_path, monkeypatch):
    monkeypatch.delenv('NBA_RELEASE_GUARD', raising=False)
    made = bl.backup(ledger(tmp_path), tmp_path / 'b', NOW)
    c = sqlite3.connect(made['backup'])
    c.execute("DELETE FROM deliveries"); c.commit(); c.close()
    check = bl.verify_restore(Path(made['backup']), made['expected'])
    assert check['status'] == 'FAILED' and check['problems'] == ['deliveries: 0 rows restored, 1 backed up']


def test_schedules_back_up_daily_and_keep_the_mac_awake_on_power():
    import plistlib
    b = plistlib.loads((ROOT / 'scripts/launchd/com.nba-predictor.ledger-snapshot.plist').read_bytes())
    assert '/Users/andry/Desktop/nba-predictor-sprint1/.telegram-delivery.sqlite' in b['ProgramArguments']
    assert len(b['StartCalendarInterval']) == 1 and b['RunAtLoad'] is False
    k = plistlib.loads((ROOT / 'scripts/launchd/com.nba-predictor.keep-awake.plist').read_bytes())
    assert k['ProgramArguments'] == ['/usr/bin/caffeinate', '-s'] and k['KeepAlive'] is True and k['RunAtLoad'] is True
