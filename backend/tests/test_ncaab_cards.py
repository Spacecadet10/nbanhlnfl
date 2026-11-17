"""NCAAB Telegram card selection (app/ncaab_cards.py): the day's top 4 app-vs-sportsbook gaps chosen at 11:00 AM ET.
Synthetic scratch store and a stub sender only: nothing reaches Telegram or the network."""
import json
import sqlite3
from datetime import datetime, timezone

import httpx
import pytest

from app import jobs, ncaab_cards as nc, telegram as tg, tickets as t
from ncaab_support import NOW, add_game, add_quote, install_college, make_store, refreshed, seed

SELECT = datetime(2026, 11, 17, 16, 0, tzinfo=timezone.utc)        # 11:00 AM EST
BEFORE = datetime(2026, 11, 17, 15, 59, tzinfo=timezone.utc)
NOW_DT = datetime(2026, 11, 17, 22, 30, tzinfo=timezone.utc)
QUOTED = "2026-11-17T15:00:00Z"
# Fake model (tests/ncaab_support.py): home win probability 0.55 + 0.03 per earlier home-team win -> team 1 and team 4: 0.58,
# teams 2 and 3: 0.55. Expected gaps below are app % on the model's side minus the no-vig moneyline % for that side.
EXPECTED = ["ncaab:407", "ncaab:401", "ncaab:406", "ncaab:404"]     # +11.2, +8.0, +6.1, +5.0; 405 is -6.3 (never selected)


@pytest.fixture(autouse=True)
def no_network_no_card_art(monkeypatch):
    from app import telegram_card

    def forbidden(*a, **k):
        raise AssertionError("an NCAAB test tried to open an HTTP client")
    monkeypatch.setattr(httpx, "Client", forbidden)
    monkeypatch.setattr(telegram_card, "_logo", lambda *_: None)


@pytest.fixture
def store(tmp_path, monkeypatch):
    conn = make_store(tmp_path, monkeypatch)
    seed(conn)                                                      # 401 (1 v 2, 23:30Z), 402 non-D-I, 403 tomorrow
    fake = install_college(monkeypatch)
    add_game(conn, "404", "2026-11-18T00:00:00Z", "2", "3")
    add_game(conn, "405", "2026-11-18T00:30:00Z", "4", "1")
    add_game(conn, "406", "2026-11-18T01:00:00Z", "3", "4")
    add_game(conn, "407", "2026-11-18T01:30:00Z", "2", "1")
    add_game(conn, "408", "2026-11-17T17:00:00Z", "4", "2")         # tips within two hours of 11 AM: not selectable
    add_game(conn, "409", "2026-11-18T06:00:00Z", "1", "3")         # 1 AM ET Nov 18: another Eastern day
    add_game(conn, "410", "2026-11-18T02:00:00Z", "2", "4")         # quoted only after 11 AM
    for game, a, b in (("401", -110, -110), ("404", -110, -110), ("405", -200, 170), ("406", -105, -115),
                       ("407", 120, -140), ("408", 300, -400), ("409", 300, -400), ("402", 300, -400)):
        add_quote(conn, f"ncaab:{game}", "winner", None, a, b, QUOTED)
    add_quote(conn, "ncaab:410", "winner", None, 300, -400, "2026-11-17T16:30:00Z")
    add_quote(conn, "ncaab:405", "winner", None, 400, -500, "2026-11-17T16:30:00Z")   # after 11 AM: never seen by the selection
    yield conn, fake
    conn.close()


def test_selection_is_the_top_four_positive_gaps_known_at_11_am(store):
    store, fake = store
    sel = nc.compute(store, SELECT)
    assert sel["status"] == "ok" and sel["et_date"] == "2026-11-17" and sel["selected_at"] == "2026-11-17T16:00:00Z"
    assert sel["selected"] == EXPECTED
    by = {c["game_id"]: c for c in sel["candidates"]}
    assert set(by) == {"ncaab:401", "ncaab:404", "ncaab:405", "ncaab:406", "ncaab:407", "ncaab:410"}   # 402, 403, 408, 409 out
    assert [by[g]["gap"] for g in EXPECTED] == [11.2, 8.0, 6.1, 5.0]
    assert by["ncaab:405"]["gap"] == -6.3 and by["ncaab:410"]["gap"] is None                 # the later quotes are invisible
    w = next(m for m in by["ncaab:401"]["markets"] if m["market"] == "winner")
    assert w["side"] == "a" and w["quote_retrieved_at"] == QUOTED and w["app"] == 0.58 and w["book"] == 0.5
    assert {m["reason"] for m in by["ncaab:401"]["markets"] if m["market"] != "winner"} == {"no quote saved"}
    assert fake.calls and {cutoff for _, cutoff, _ in fake.calls} == {"2026-11-17T16:00:00Z"}   # model as of 11 AM


def test_spread_and_total_quotes_count_and_the_largest_market_gap_is_the_games_gap(store):
    store, _ = store
    add_quote(store, "ncaab:405", "total", 141.5, 130, -150, QUOTED)    # model 52% over vs no-vig over 42.0%: +10.0
    gap = next(c for c in nc.compute(store, SELECT)["candidates"] if c["game_id"] == "ncaab:405")
    assert gap["best_market"] == "total" and gap["gap"] == 10.0                    # beats its winner gap of -6.3
    total = next(m for m in gap["markets"] if m["market"] == "total")
    assert total["side"] == "a" and total["line"] == 141.5


def test_a_quote_older_than_six_hours_is_not_a_price_at_the_selection(store):
    store, _ = store
    add_quote(store, "ncaab:410", "winner", None, -110, -110, "2026-11-17T09:59:00Z")   # 6 h 1 min before 11 AM ET
    by = {c["game_id"]: c for c in nc.compute(store, SELECT)["candidates"]}
    assert by["ncaab:410"]["gap"] is None
    assert by["ncaab:410"]["markets"][0]["reason"] == "latest moneyline older than 6 hours"


def test_saved_once_per_eastern_day_append_only_and_nothing_before_11_am(store, tmp_path):
    store, _ = store
    with tg.ledger(tmp_path / "receipts.sqlite") as ledger:
        assert nc.ensure(ledger, store, BEFORE) is None
        assert ledger.execute("SELECT COUNT(*) FROM ncaab_card_selections").fetchone()[0] == 0
        first = nc.ensure(ledger, store, SELECT)
        add_quote(store, "ncaab:404", "winner", None, 300, -400, "2026-11-17T16:10:00Z")
        later = nc.ensure(ledger, store, datetime(2026, 11, 17, 18, 0, tzinfo=timezone.utc))
        assert first["selected"] == later["selected"] == EXPECTED and later["selected_at"] == "2026-11-17T16:00:00Z"
        with pytest.raises(sqlite3.IntegrityError):
            ledger.execute("UPDATE ncaab_card_selections SET selected='[]'")
        with pytest.raises(sqlite3.IntegrityError):
            ledger.execute("DELETE FROM ncaab_card_selections")
        assert json.loads(ledger.execute("SELECT candidates FROM ncaab_card_selections").fetchone()[0])


def test_a_failed_selection_is_saved_as_an_error_and_cards_no_game(store, tmp_path, monkeypatch):
    store, _ = store
    monkeypatch.setattr(nc, "compute", lambda *a: (_ for _ in ()).throw(RuntimeError("model down")))
    with tg.ledger(tmp_path / "receipts.sqlite") as ledger:
        sel = nc.ensure(ledger, store, SELECT)
    assert sel["status"] == "error" and "model down" in sel["error"] and sel["selected"] == []
    assert nc.annotate([{"game_id": "ncaab:401", "market": "winner"}], sel) == []
    assert nc.annotate([{"game_id": "ncaab:401", "market": "winner"}], None) == []


def test_card_choice_recommends_the_selected_market_and_says_the_rule_is_untested():
    sel = {"rule": nc.RULE, "rank": 2, "of": 4, "selected_at": "2026-11-17T16:00:00Z", "market": "total", "side": "b", "gap": 8.0}
    picks = [dict(forecast_id=i, market=m, pick_label=label, probability=p, market_prob=b, quote_check={},
                  percentage_history=None, ncaab_selection=sel)
             for i, (m, label, p, b) in enumerate((("winner", "DUKE win", 0.8, 0.75), ("total", "Under 141.5", 0.56, 0.5)))]
    choice = t.card_choice("ncaab", picks)
    assert choice["pick"]["market"] == "total"
    assert "#2 of today's 4 largest app-vs-sportsbook gaps (+8.0 points then); untested rule" in choice["reason"]
    assert "11:00 AM ET" in choice["reason"]
    picks[1]["percentage_history"] = {"side": "a"}                          # the official forecast now leans over: flagged
    flipped = t.card_choice("ncaab", picks)
    assert flipped["pick"]["market"] == "total" and "side in this market changed since the selection" in flipped["reason"]
    picks[1]["percentage_history"] = None
    for p in picks:
        del p["ncaab_selection"]
    assert t.card_choice("ncaab", picks)["pick"]["market"] == "winner"      # without a selection: unchanged behaviour


def test_run_once_sends_cards_only_for_selected_games_and_never_twice(store, tmp_path):
    store, _ = store
    sends = []

    class Bot:
        def call(self, method, body, **kwargs):
            if method == "getUpdates":
                return []
            assert method in ("sendMessage", "sendPhoto")
            sends.append((method, body))
            return {"message_id": len(sends)}
    with tg.ledger(tmp_path / "receipts.sqlite") as ledger:
        assert nc.ensure(ledger, store, SELECT)["selected"] == EXPECTED
        store.execute("UPDATE games SET status='final', home_score=60, away_score=50, result_first_seen_at=?, result_available_at=?, "
                      "result_availability_basis='observed_retrieval' WHERE game_id='ncaab:408'", ("2026-11-17T19:30:00Z",) * 2)
        out = jobs.predict_now(store, now=NOW)                      # two-hour forecasts for 401, 404, 405
        assert out["status"] == "success", out
        store.commit()
        refreshed(store, "2026-11-17T22:29:00Z")
        first = tg.run_once(Bot(), {"TELEGRAM_CHAT_ID": "123"}, ledger, now=NOW_DT)
        second = tg.run_once(Bot(), {"TELEGRAM_CHAT_ID": "123"}, ledger, now=NOW_DT)
        texts = [r["text"] for r in ledger.execute("SELECT text FROM deliveries WHERE status='sent'")]
    got = first["sports"]["ncaab"]
    assert got["eligible_summaries"] == 2 and got["sent"] == 2 and got["selection"] == {"status": "ok", "selected": 4}, first
    assert second["sports"]["ncaab"].get("sent", 0) == 0
    assert len(texts) == 2 and all("untested rule" in x for x in texts)
    assert sorted(x.split("\n")[1] for x in texts) == ["<b>KU @ UNC</b>", "<b>UNC @ DUKE</b>"]      # 401 and 404; 405 is not


def test_run_once_sends_no_ncaab_card_before_the_11_am_selection(store, tmp_path):
    store, _ = store
    class Bot:
        def call(self, method, body, **kwargs):
            if method == "getUpdates":
                return []
            raise AssertionError("no card may be sent before the selection")
    out = jobs.predict_now(store, now="2026-11-17T15:30:00Z")       # 408's two-hour forecast (17:00Z)
    store.commit()
    refreshed(store, "2026-11-17T15:55:00Z")
    with tg.ledger(tmp_path / "receipts.sqlite") as ledger:
        res = tg.run_once(Bot(), {"TELEGRAM_CHAT_ID": "123"}, ledger, now=BEFORE)
    assert res["sports"]["ncaab"].get("sent", 0) == 0 and res["sports"]["ncaab"].get("selection") is None, res
