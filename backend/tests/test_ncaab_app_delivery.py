"""NCAAB in Telegram cards, the ticket snapshot (dry run) and the scorecard. Nothing here can reach Telegram: every sender is a
stub, and the HTTP client is replaced by one that fails the test if it is ever used (synthetic scratch store, no network)."""
import copy
import hashlib
from datetime import datetime, timezone

import httpx
import pytest

from app import db, jobs, scorecard, tickets as t, telegram as tg
from app.providers.real import RealProvider
from ncaab_support import GAME, NOW, add_quote, install_college, make_store, seed

NOW_DT = datetime(2026, 11, 17, 22, 30, tzinfo=timezone.utc)
DAY = datetime(2026, 11, 17).date()


@pytest.fixture(autouse=True)
def no_network_no_card_art(monkeypatch):
    from app import telegram_card

    def forbidden(*a, **k):
        raise AssertionError("an NCAAB test tried to open an HTTP client (Telegram must never be reached)")
    monkeypatch.setattr(httpx, "Client", forbidden)
    monkeypatch.setattr(telegram_card, "_logo", lambda *_: None)


@pytest.fixture
def store(tmp_path, monkeypatch):
    conn = make_store(tmp_path, monkeypatch)
    seed(conn)
    install_college(monkeypatch)
    add_quote(conn, GAME, "winner", None, -150, 130, "2026-11-17T22:10:00Z")
    add_quote(conn, GAME, "total", 141.5, -110, -110, "2026-11-17T22:05:00Z")
    out = jobs.predict_now(conn, now=NOW)
    assert out["status"] == "success", out
    conn.commit()
    yield conn
    conn.close()


def ncaab_report(conn):
    return RealProvider(conn, NOW).ranked_picks(DAY)


def test_ranked_report_carries_every_ncaab_market_and_no_rule_based_claim(store):
    report = ncaab_report(store)
    assert report["sport"] == "ncaab" and {p["market"] for p in report["picks"]} == {
        "winner", "spread", "total", "team_total_home", "team_total_away"}
    assert all(p["game_id"] == GAME and p["grade"] == "pending" for p in report["picks"])
    assert tg.best_pick_pictures(store, "ncaab", report, NOW_DT) is None                  # no NCAAB rule exists
    assert tg.record_best_pick(store, "ncaab", "k", GAME, {}, NOW) is False             # and nothing is recorded for one


def test_format_batch_and_dedup_key_follow_the_nba_rules(store):
    report = ncaab_report(store)
    for p in report["picks"]:      # percentage history comes from the live record of the exact percentage: none yet, as for a new NBA market
        p.setdefault("strong", False)
        p.setdefault("game_rank", None)
    batches = tg.message_batches(report, "123")
    assert len(batches) == 1
    key, text = batches[0]
    assert "PROP51 PICKS · NCAAB" in text and "DUKE" in text.upper() and "model line" in text.lower()
    nba = copy.deepcopy(report)
    nba["sport"] = "nba"
    for p in nba["picks"]:
        h = p.get("percentage_history")
        if h is not None:
            h = h.model_dump(mode="json") if hasattr(h, "model_dump") else dict(h)
            p["percentage_history"] = {**h, "sport": "nba"}
    other = tg.message_batches(nba, "123")
    assert other and other[0][0] != key                                                 # the sport is part of the key
    assert tg.message_batches(report, "456")[0][0] != key                               # so is the chat
    again = copy.deepcopy(report)
    for p in again["picks"]:
        p["forecast_id"] += 1000
    assert tg.message_batches(again, "123")[0][0] != key                                # a new forecast is a new card


def test_run_once_covers_ncaab_and_only_ever_calls_the_stub_sender(store, tmp_path, monkeypatch):
    monkeypatch.delenv("NBA_NCAAB_CARDS", raising=False)      # default since the colour release: the gate decides
    from ncaab_support import refreshed
    refreshed(store, "2026-11-17T22:29:00Z")                  # the source must be fresh for a card to go out (freshness policy)
    sends = []

    class Bot:
        def call(self, method, body, **kwargs):
            if method == "getUpdates":
                return []
            assert method in ("sendMessage", "sendPhoto")
            sends.append((method, body))
            return {"message_id": len(sends)}
    from app import ncaab_cards
    add_quote(store, GAME, "winner", None, -110, -110, "2026-11-17T15:00:00Z")    # seen by the 11 AM ET card selection
    with tg.ledger(tmp_path / "receipts.sqlite") as conn:
        assert ncaab_cards.ensure(conn, store, datetime(2026, 11, 17, 16, 0, tzinfo=timezone.utc))["selected"] == [GAME]
        first = tg.run_once(Bot(), {"TELEGRAM_CHAT_ID": "123"}, conn, now=NOW_DT)
        second = tg.run_once(Bot(), {"TELEGRAM_CHAT_ID": "123"}, conn, now=NOW_DT)
    assert "ncaab" in first["sports"] and first["sports"]["ncaab"].get("status") == "gated", first
    sent = first["sports"]["ncaab"].get("sent", 0)
    assert sent == 0 and first["sports"]["ncaab"]["qualifying"] == 0, first     # no threshold stored: nothing qualifies
    assert second["sports"]["ncaab"].get("sent", 0) == 0                                 # the same card is never sent twice
    assert len(sends) >= sent and all(m in ("sendMessage", "sendPhoto") for m, _ in sends)


def test_telegram_and_credentials_files_are_never_read_by_the_ncaab_format_path(store, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "telegram_settings", lambda *a, **k: pytest.fail("settings (tokens) were read"))
    assert tg.message_batches(ncaab_report(store), "123") is not None


def test_ticket_snapshot_dry_run_includes_ncaab_with_its_facts_and_no_lineup(store, monkeypatch, tmp_path):
    class Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW_DT if tz else NOW_DT.replace(tzinfo=None)
    skill = tmp_path / "SKILL.md"
    skill.write_text("fixture")
    monkeypatch.setattr(t, "SKILL", skill)
    monkeypatch.setattr(t, "datetime", Fixed)
    monkeypatch.setattr(t, "soccer_section", lambda now: {"freshness": {"source_state": "fresh", "failing": []},
                                                          "report": {"mode": "real", "picks": []}})
    snap = t.snapshot()
    section = snap["sports"]["ncaab"]
    assert "error" not in section, section
    picks = section["report"]["picks"]
    assert {p["market"] for p in picks} == {"winner", "spread", "total", "team_total_home", "team_total_away"}
    assert all("lineup" not in p and p["game_facts"].get("error") is None for p in picks)   # no injury or line-up facts exist
    assert all(p["quote_check"].get("error") is None for p in picks)
    assert section["missing_due_games"] == []
    assert t.lineup_flags("ncaab", picks) == []


def test_scorecard_enumerates_ncaab_and_reads_its_store(store, tmp_path):
    assert "ncaab" in scorecard.SPORTS and scorecard.path("ncaab", tmp_path).name == "ncaab.sqlite"
    rows = store.execute("SELECT * FROM game_forecasts WHERE game_id=? AND status='forecast'", (GAME,)).fetchall()
    assert len(rows) == 5
    picture = scorecard.game_picture(store, "ncaab", next(r for r in rows if r["market"] == "winner"))
    assert picture["sport"] == "ncaab" and picture["lineup_state"] == "not_applicable"
    assert picture["book_state"] == "present"                                           # the moneyline saved before the forecast
    assert picture["books3_state"] in ("missing", "not_applicable", "present")


def test_scorecard_state_of_a_model_line_has_no_book_for_that_market(store):
    row = next(r for r in store.execute("SELECT * FROM game_forecasts WHERE game_id=? AND market='spread'", (GAME,)))
    picture = scorecard.game_picture(store, "ncaab", row)
    assert picture["line_source"] == "model" and picture["book_state"] == "missing"


def test_ncaab_rundown_prices_are_requested_only_on_demand_and_within_a_daily_budget(tmp_path, monkeypatch):
    from app import rundown_odds as r
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("THERUNDOWN_API_KEY", "test-key-do-not-save")
    calls = []

    class Response:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"events": []}'

    def opener(request, timeout=0):
        calls.append(request.full_url)
        return Response()
    assert r.fetch(NOW_DT, opener=opener, pause=lambda _: None)["status"] == "ok" and len(calls) == 6      # default unchanged
    assert not any("/sports/5/" in u for u in calls)
    calls.clear()
    assert r.fetch(NOW_DT, opener=opener, pause=lambda _: None, extra=("ncaab",))["status"] == "ok"
    assert sum("/sports/5/" in u for u in calls) == 2 and len(calls) == 8                      # both UTC dates, two requests a run
    for _ in range(6):
        r.fetch(NOW_DT, opener=opener, pause=lambda _: None, extra=("ncaab",))
    assert sum("/sports/5/" in u for u in calls) == r.EXTRA_DAY_REQUESTS["ncaab"] == 2          # its own daily allowance, then none
    used = sum(1 for _ in (tmp_path / "odds-books" / "requests.jsonl").read_text().splitlines())
    assert used <= r.DAY_REQUESTS


def test_telegram_preview_formats_a_settled_ncaab_game_without_any_send(store, monkeypatch):
    store.execute("UPDATE games SET status='final', home_score=80, away_score=75, result_first_seen_at=?, result_available_at=?, "
                  "result_availability_basis='observed_retrieval' WHERE game_id=?", ("2026-11-18T05:00:00Z",) * 2 + (GAME,))
    store.commit()
    report = RealProvider(store, "2026-11-18T05:30:00Z").ranked_picks(DAY)
    picks = [p for p in report["picks"] if p["status"] == "final"]
    assert picks and {p["grade"] for p in picks} <= {"win", "loss"}
    batch = tg.format_batch(report, "123", GAME, picks, preview=True)
    assert batch is not None
    key, text = batch
    assert text.startswith("<b>TEST PREVIEW") and "PROP51 PICKS · NCAAB" in text
    assert tg.format_batch(report, "123", GAME, picks, preview=True)[0] == key                  # the preview key is stable


def test_the_cli_routes_telegram_preview_for_ncaab_to_the_same_command_as_the_nba(monkeypatch):
    from app import cli
    seen = {}
    monkeypatch.setattr(tg, "command", lambda args: seen.update(sport=args.sport, action=args.telegram_action, date=args.date) or 0)
    assert cli.main(["--sport", "ncaab", "telegram", "preview", "--date", "2026-11-16"]) == 0
    assert seen == {"sport": "ncaab", "action": "preview", "date": "2026-11-16"}


def test_ncaab_kill_switch_sends_nothing_and_selects_nothing(store, tmp_path, monkeypatch):
    """NBA_NCAAB_CARDS=0 turns every NCAAB card off before any read (the Nov 21 off switch, now a kill switch; by default the
    colour gate decides). Forecasts keep being recorded; only the cards stop."""
    from ncaab_support import refreshed
    refreshed(store, "2026-11-17T22:29:00Z")
    monkeypatch.setenv("NBA_NCAAB_CARDS", "0")
    sends = []

    class Bot:
        def call(self, method, body, **kwargs):
            if method == "getUpdates":
                return []
            sends.append((method, body))
            return {"message_id": len(sends)}
    with tg.ledger(tmp_path / "receipts.sqlite") as conn:
        out = tg.run_once(Bot(), {"TELEGRAM_CHAT_ID": "123"}, conn, now=NOW_DT)
        selections = conn.execute("SELECT COUNT(*) FROM ncaab_card_selections").fetchone()[0]
    assert out["sports"]["ncaab"]["status"] == "off", out
    assert "NBA_NCAAB_CARDS=0" in out["sports"]["ncaab"]["reason"]
    assert sends == [] and selections == 0
