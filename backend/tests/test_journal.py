"""Forecast journal (app/journal.py): an append-only JSONL copy of every forecast and odds-quote row, kept outside the
store, so the exact probabilities survive the loss of a store. Synthetic stores under tmp_path only."""
import json

import pytest

from app import backup, db, journal, ledger
from test_storage import T0, event, payload, run


def _forecast(**kw):
    base = dict(game_id="espn:1001", run_type="live", model_version="m1", dataset_version="d1", feature_version="f1",
                cutoff_at="2024-01-15T17:00:00Z", generated_at="2024-01-15T16:59:00Z", home_win_prob=0.6)
    return ledger.Forecast(**{**base, **kw})


@pytest.fixture
def store(tmp_path):
    data = tmp_path / "data"
    conn = db.connect(data / "nba.sqlite")
    db.migrate(conn)
    run(conn, payload(event()), T0)
    yield data, conn
    conn.close()


def _lines(path):
    return [json.loads(x) for x in path.read_text().splitlines()]


def test_every_forecast_row_is_journaled_with_its_exact_probability(store, tmp_path):
    data, conn = store
    ledger.record(conn, _forecast())
    ledger.record(conn, _forecast(model_version="m2", home_win_prob=0.625))
    out = journal.export(data, tmp_path / "journal")
    rows = _lines(tmp_path / "journal" / "nba" / "predictions.jsonl")
    assert [(r["row"]["model_version"], r["row"]["home_win_prob"], r["row"]["generated_at"]) for r in rows] == \
        [("m1", 0.6, "2024-01-15T16:59:00Z"), ("m2", 0.625, "2024-01-15T16:59:00Z")]
    assert out["sports"]["nba"]["predictions"] == 2


def test_rerunning_appends_only_new_rows_and_never_rewrites_old_lines(store, tmp_path):
    data, conn = store
    ledger.record(conn, _forecast())
    journal.export(data, tmp_path / "journal")
    path = tmp_path / "journal" / "nba" / "predictions.jsonl"
    first = path.read_bytes()
    assert journal.export(data, tmp_path / "journal")["sports"]["nba"]["predictions"] == 0   # nothing new: no duplicate
    ledger.record(conn, _forecast(model_version="m2", home_win_prob=0.7))
    assert journal.export(data, tmp_path / "journal")["sports"]["nba"]["predictions"] == 1
    after = path.read_bytes()
    assert after.startswith(first) and len(_lines(path)) == 2


def test_export_does_not_change_the_store(store, tmp_path):
    data, conn = store
    ledger.record(conn, _forecast())
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    before = backup.sha256(data / "nba.sqlite")
    journal.export(data, tmp_path / "journal")
    assert backup.sha256(data / "nba.sqlite") == before


def test_verify_reports_rows_the_store_has_lost_and_tampered_lines(store, tmp_path):
    data, conn = store
    ledger.record(conn, _forecast())
    ledger.record(conn, _forecast(model_version="m2", home_win_prob=0.7))
    jdir = tmp_path / "journal"
    journal.export(data, jdir)
    assert journal.verify(data, jdir)["ok"] is True
    # A rebuilt store that lost the second forecast: the journal still holds it, and verify says so.
    rebuilt = tmp_path / "rebuilt"
    c2 = db.connect(rebuilt / "nba.sqlite")
    db.migrate(c2)
    run(c2, payload(event()), T0)
    ledger.record(c2, _forecast())
    c2.close()
    lost = journal.verify(rebuilt, jdir)
    assert lost["ok"] is False and lost["sports"]["nba"]["predictions"]["journal_only"] == [2]
    # A journal line edited after the fact no longer matches its checksum.
    path = jdir / "nba" / "predictions.jsonl"
    path.write_text(path.read_text().replace("0.7", "0.9"))
    bad = journal.verify(data, jdir)
    assert bad["ok"] is False and bad["sports"]["nba"]["predictions"]["tampered"] == [2]


def test_the_journal_must_live_outside_the_data_folder(store):
    data, _ = store
    with pytest.raises(journal.JournalError):
        journal.export(data, data / "journal")


def test_a_row_written_between_the_export_and_the_check_is_not_reported_missing_and_is_journaled_next_time(store, tmp_path):
    # Nov 24 09:45Z and Nov 27 01:00Z: quotes inserted while the nightly backup ran made verify report them "missing" (a
    # false failure: export and verify read the live store at two different moments). Export and check now read ONE snapshot.
    data, conn = store
    ledger.record(conn, _forecast())
    late = lambda: ledger.record(conn, _forecast(model_version="late", home_win_prob=0.55))
    out = journal.export_and_verify(data, tmp_path / "journal", _after_snapshot=late)
    assert out["verify"]["ok"], out["verify"]
    assert out["export"]["sports"]["nba"]["predictions"] == 1
    again = journal.export_and_verify(data, tmp_path / "journal")
    assert again["verify"]["ok"] and again["export"]["sports"]["nba"]["predictions"] == 1      # the late row, next run


def test_the_one_snapshot_check_still_reports_a_changed_journal_line(store, tmp_path):
    data, conn = store
    ledger.record(conn, _forecast())
    journal.export_and_verify(data, tmp_path / "journal")
    p = tmp_path / "journal" / "nba" / "predictions.jsonl"
    p.write_text(p.read_text().replace("0.6", "0.7"))
    out = journal.export_and_verify(data, tmp_path / "journal")
    assert not out["verify"]["ok"] and out["verify"]["sports"]["nba"]["predictions"]["tampered"]
