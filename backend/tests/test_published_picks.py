"""app/published_picks.py (what each sent Telegram card published) and app/record_basis.py (which forecast the record counts).
Synthetic data; a fake bot only (nothing is ever sent)."""
import sqlite3
from datetime import datetime, timezone

import pytest

from app import published_picks as pp, record_basis, telegram as tg
from test_telegram import Bot, NOW, soccer_report as _soccer_report


def soccer_report():
    r = _soccer_report()
    r["picks"][0].update(side="home_draw", row_id=1)      # the real double-chance pick carries its side and stored row
    return r

GAME = {"game_id": "g1", "status": "final", "home_score": 112, "away_score": 113, "tipoff_at": "2026-11-02T21:00:00Z"}
DDL = """CREATE TABLE game_forecasts (forecast_id INTEGER PRIMARY KEY, game_id TEXT, market TEXT, status TEXT, generated_at TEXT,
  tipoff_at TEXT, model_version TEXT, line REAL, p_a REAL, p_mid REAL, p_b REAL, confidence REAL);
CREATE TABLE game_decisions (game_id TEXT, decision TEXT);"""


def store(rows):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(DDL)
    db.executemany("INSERT INTO game_forecasts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return db


def total_row(fid, gen, line, p_over, version="m1"):
    # total 225 = 112 + 113: Over 224.5 wins, Under 225.5 wins
    over = p_over >= 0.5
    return (fid, "g1", "total", "forecast", gen, "2026-11-02T21:00:00Z", version, line, p_over, 0.0, 1 - p_over,
            max(p_over, 1 - p_over))


def test_the_record_counts_the_latest_forecast_but_the_card_published_an_earlier_one_and_each_is_graded_on_its_own():
    db = store([total_row(1, "2026-11-02T19:10:00Z", 224.5, 0.58), total_row(2, "2026-11-02T19:40:00Z", 225.5, 0.41)])
    latest = {"forecast_id": 2, "generated_at": "2026-11-02T19:40:00Z", "model_version": "m1", "side": "b", "line": 225.5,
              "confidence": 0.59}
    published = {("g1", "total"): dict(game_id="g1", forecast_id="1", market="total", side="a", line=224.5, probability=0.58, model_version="m1",
                                       generated_at="2026-11-02T19:10:00Z", sent_at="2026-11-02T19:11:00Z", message_id=7)}
    b = record_basis.build(db, "nba", GAME, "total", latest, published, "2026-11-03T12:00:00Z")
    assert (b["forecast_id"], b["side"], b["line"], b["pregame_forecasts"]) == (2, "b", 225.5, 2)
    assert [e["forecast_id"] for e in b["earlier"]] == [1] and b["earlier"][0]["side"] == "a" and b["earlier"][0]["line"] == 224.5
    assert b["published"]["forecast_id"] == 1 and b["published"]["message_id"] == 7 and b["published"]["side"] == "a"
    assert b["published"]["grade"] == "win"                    # Over 224.5 on 225 total points; the record's Under 225.5 also wins
    published[("g1", "total")]["line"] = 226.5                  # (the ledger line is informational; grading uses the stored forecast)
    assert record_basis.build(db, "nba", GAME, "total", latest, published, "2026-11-03T12:00:00Z")["published"]["grade"] == "win"


def test_a_replaced_forecast_that_flipped_side_grades_differently_for_the_card_and_the_record():
    db = store([total_row(1, "2026-11-02T19:10:00Z", 225.5, 0.60), total_row(2, "2026-11-02T19:40:00Z", 225.5, 0.40)])
    latest = {"forecast_id": 2, "generated_at": "2026-11-02T19:40:00Z", "model_version": "m1", "side": "b", "line": 225.5,
              "confidence": 0.60}
    published = {("g1", "total"): dict(game_id="g1", forecast_id="1", market="total", side="a", line=225.5, probability=0.60, model_version="m1",
                                       generated_at="2026-11-02T19:10:00Z", sent_at="x", message_id=1)}
    b = record_basis.build(db, "nba", GAME, "total", latest, published, "2026-11-03T12:00:00Z")
    from app import coverage
    assert b["published"]["grade"] == "loss"                    # Over 225.5 on 225 points
    assert coverage.grade("total", 225.5, (0.40, 0.0, 0.60), GAME, "2026-11-03T12:00:00Z", "2026-11-02T21:00:00Z") == "win"   # the record's pick


def test_no_published_row_means_no_published_block_and_a_single_forecast_has_no_earlier():
    db = store([total_row(1, "2026-11-02T19:10:00Z", 225.5, 0.60)])
    rec = {"forecast_id": 1, "generated_at": "2026-11-02T19:10:00Z", "model_version": "m1", "side": "a", "line": 225.5, "confidence": 0.6}
    b = record_basis.build(db, "nba", GAME, "total", rec, {}, "2026-11-03T12:00:00Z")
    assert b["published"] is None and b["earlier"] == [] and b["pregame_forecasts"] == 1


def test_a_missing_ledger_is_tolerated(tmp_path):
    assert pp.load("nba", tmp_path / "nope.sqlite") == {}


# --- The ledger table -------------------------------------------------------------------------------------------------
def ledger(tmp_path):
    return tg.ledger(tmp_path / "receipts.sqlite")


def sent_card(conn, key="k1", status="sent"):
    conn.execute("INSERT INTO deliveries (delivery_key, chat_id, text, status, attempted_at, message_id) VALUES (?,?,?,?,?,?)",
                 (key, "123", "text", status, "2026-11-02T19:11:00Z", 42))


def test_the_published_picks_table_is_created_with_the_ledger_and_is_append_only(tmp_path):
    with ledger(tmp_path) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='published_picks'").fetchone()
        conn.execute("INSERT INTO published_picks (delivery_key, sport, game_id, market, forecast_id, side, probability, sent_at) "
                     "VALUES ('k','nba','g','total','1','a',.6,'t')")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("UPDATE published_picks SET side='b'")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("DELETE FROM published_picks")


def test_soccer_job_records_the_published_pick_after_the_send_only(tmp_path, monkeypatch):
    from app import db, tickets
    monkeypatch.setattr(tg, "controls", lambda *a: True)
    monkeypatch.setattr(db, "connect_readonly", lambda *a: (_ for _ in ()).throw(FileNotFoundError()))
    (tmp_path / "soccer.sqlite").touch()
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    section = {"report": soccer_report(), "refused": {}}
    monkeypatch.setattr(tickets, "soccer_section", lambda now: section)
    now = datetime.fromisoformat(NOW.replace("Z", "+00:00"))
    bot = Bot()
    with ledger(tmp_path) as conn:
        first = tg.run_once(bot, {"TELEGRAM_CHAT_ID": "123"}, conn, now=now)
        assert first["sports"]["soccer"]["sent"] == 1
        rows = conn.execute("SELECT * FROM published_picks").fetchall()
        assert rows and {r["sport"] for r in rows} == {"soccer"} and all(r["message_id"] == 1 and r["basis"] == "live" for r in rows)
        key = conn.execute("SELECT delivery_key FROM deliveries").fetchone()[0]
        assert {r["delivery_key"] for r in rows} == {key}
        tg.run_once(bot, {"TELEGRAM_CHAT_ID": "123"}, conn, now=now)           # already recorded: nothing more is written
        assert conn.execute("SELECT COUNT(*) FROM published_picks").fetchone()[0] == len(rows)
    assert len(bot.sends) == 1


def test_a_failed_send_records_nothing_and_a_recorder_error_never_reaches_the_sender(tmp_path):
    report = soccer_report()
    now = datetime.fromisoformat(NOW.replace("Z", "+00:00"))
    gid = report["picks"][0]["game_id"]
    with ledger(tmp_path) as conn:
        sent_card(conn, "k-failed", status="failed")
        assert pp.record(conn, "soccer", "k-failed", gid, report, now) == 0           # only a 'sent' card is published
        assert pp.record(conn, "soccer", "k-failed", gid, {"picks": None}, now) == 0    # a broken report never raises
        assert conn.execute("SELECT COUNT(*) FROM published_picks").fetchone()[0] == 0


def test_repeating_a_record_is_ignored_by_the_unique_key(tmp_path):
    report = soccer_report()
    now = datetime.fromisoformat(NOW.replace("Z", "+00:00"))
    gid = report["picks"][0]["game_id"]
    with ledger(tmp_path) as conn:
        sent_card(conn, "k1")
        first = pp.record(conn, "soccer", "k1", gid, report, now)
        assert first >= 1 and pp.record(conn, "soccer", "k1", gid, report, now) == 0
