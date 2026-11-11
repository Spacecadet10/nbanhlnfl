"""Recomputed past-season points-model history (app/points_history.py): the live computation exactly, only parameter-clean
seasons, append-only, and a record kept apart from the live one on the card (synthetic stores, no network)."""
import copy
import sqlite3

import pytest

from app import market_live, points_history as ph, telegram as tg, threshold_overview as to
from test_live_markets import add_upcoming, quote, sealed  # noqa: F401  (fixtures)
from test_nhl_eval import nhl_store  # noqa: F401  (fixture)
from test_telegram import isolated_card_assets, report  # noqa: F401  (fixture)


def test_recompute_matches_the_live_near_tipoff_writer(sealed):
    add_upcoming(sealed, start="2026-10-11T19:00:00")       # 23:00Z
    quote(sealed, "2026-10-11T20:00:00Z")
    t = "2026-10-11T22:00:00Z"
    live_out = market_live.forecast(sealed, now=t, policy="near_tipoff", game_ids=["nhl:2026020001"], write=False)
    by = {f["market"]: f for f in live_out["forecasts"]}
    g = sealed.execute("SELECT * FROM games WHERE game_id='nhl:2026020001'").fetchone()
    rep = ph.Replayer(sealed)
    for market in ph.MARKETS:
        r = ph.compute(sealed, rep, g, market, t)
        assert r["model_version"] == live_out["model_version"]
        assert r["dataset_version"] == live_out["dataset_version"]
        assert r["line"] == by[market]["line"]
        assert {"bookmaker": "market"}.get(r["line_source"], r["line_source"]) == by[market]["line_source"]
        assert list(r["p"]) == by[market]["p"]
    assert ph.compute(sealed, ph.Replayer(sealed), g, "spread", t)["line_source"] == "bookmaker"


def test_build_appends_only_clean_seasons_once_and_refuses_the_rest(sealed, tmp_path):
    hist = ph.connect(tmp_path / "history" / "points_nhl.sqlite", create=True)
    with pytest.raises(ph.HistoryRefused):
        ph.build(sealed, hist, seasons=(2024,), computed_at="2026-11-11T00:00:00Z")
    out = ph.build(sealed, hist, seasons=(2026,), computed_at="2026-11-11T00:00:00Z")
    finals = sealed.execute("SELECT COUNT(*) FROM games WHERE status='final' AND season=2026 "
                            "AND season_type IN ('regular','play_in','postseason')").fetchone()[0]
    assert finals > 0 and out["created"] == finals * len(ph.MARKETS)
    rows = hist.execute("SELECT * FROM points_history").fetchall()
    assert {r["line_source"] for r in rows} == {"model"} and {r["source"] for r in rows} == {ph.SOURCE}
    for r in rows[:50]:
        assert r["forecast_at"] == ph.forecast_time(r["tipoff_at"]) < r["tipoff_at"]
        available = sealed.execute("SELECT COUNT(*) FROM games WHERE status='final' AND result_available_at <= ? "
                                   "AND season_type IN ('regular','play_in','postseason')", (r["forecast_at"],)).fetchone()[0]
        assert r["results_used"] == available         # nothing after the forecast time
    again = ph.build(sealed, hist, seasons=(2026,), computed_at="2026-11-12T00:00:00Z")
    assert again["created"] == 0 and again["already_recorded"] == out["created"]
    with pytest.raises(sqlite3.DatabaseError):
        hist.execute("UPDATE points_history SET p_a = 0.5")
    with pytest.raises(sqlite3.DatabaseError):
        hist.execute("DELETE FROM points_history")


def test_history_is_a_separate_record_on_the_same_exact_key(sealed, tmp_path):
    hist = ph.connect(tmp_path / "history" / "points_nhl.sqlite", create=True)
    ph.build(sealed, hist, seasons=(2026,), computed_at="2026-11-11T00:00:00Z")
    index = ph.history_index(sealed, hist, "total", "2026-11-11T00:00:00Z")
    key, games = max(index.items(), key=lambda kv: len(kv[1]))
    pct, side, source, version, line_source = key
    assert source == to.HISTORY_SOURCE and line_source == "model"
    rec = {"confidence": float(pct) / 100, "side": side, "source": "primary_recorded", "model_version": version,
           "line_source": "model"}
    wins = sum(1 for gr in games.values() if gr == "win")
    losses = sum(1 for gr in games.values() if gr == "loss")
    out = to.percentage_record("nhl", "total", rec, "live-game", {}, "2026-11-11T00:00:00Z", history_index=index)
    assert (out["wins"], out["losses"], out["settled"]) == (0, 0, 0)          # the live record is untouched
    assert out["history"] == {"source": to.HISTORY_SOURCE, "line_source": "model", "wins": wins, "losses": losses,
                              "settled": wins + losses} and wins + losses > 0
    bookmaker = to.percentage_record("nhl", "total", {**rec, "line_source": "bookmaker"}, "live-game", {}, "2026-11-11T00:00:00Z",
                                     history_index=index)
    assert bookmaker["history"]["settled"] == 0                                # a model line never stands in for a bookmaker line
    assert to.percentage_record("nhl", "total", rec, "live-game", {}, "2026-11-11T00:00:00Z")["history"] is None
    assert ph.history_index(sealed, None, "total", "2026-11-11T00:00:00Z") is None
    assert ph.history_index(sealed, hist, "reg3", "2026-11-11T00:00:00Z") is None


def test_card_record_line_shows_live_and_past_seasons_apart_and_fits():
    d = report()
    d["picks"][0]["percentage_history"]["history"] = {"source": "history_recomputed", "line_source": "model", "wins": 41,
                                                      "losses": 37, "settled": 78}
    _, text = tg.message_batches(d, "123")[0]
    assert "<b>Model 51.8%</b> · 0W–2L (2 settled) · past seasons 41W–37L" in text
    plain = copy.deepcopy(report())
    assert "past seasons" not in tg.message_batches(plain, "123")[0][1]
    none = copy.deepcopy(report())       # a bookmaker-line pick: history exists but never at that key
    none["picks"][0]["percentage_history"]["history"] = {"source": "history_recomputed", "line_source": "bookmaker", "wins": 0,
                                                         "losses": 0, "settled": 0}
    assert "past seasons" not in tg.message_batches(none, "123")[0][1]
    # adding history changes no card identity: a card already sent is not sent again
    assert tg.message_batches(d, "123")[0][0] == tg.message_batches(plain, "123")[0][0]
    from PIL import Image, ImageDraw
    from app import telegram_card
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    longest = "12W–10L (22 settled) · rank 12 of 120 · AVOID · past seasons 1234W–1199L · Small sample."
    text_out, font = telegram_card.fit_record(draw, longest)
    assert text_out == longest and draw.textlength(text_out, font=font) <= telegram_card.RECORD_WIDTH
    assert telegram_card.render_card(text)                                      # the picture still renders
