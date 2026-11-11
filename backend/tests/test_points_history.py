"""Recomputed past-season points-model history (app/points_history.py): the live computation exactly, NBA only and only
parameter-clean seasons, append-only, and a record kept apart from the live one on the card (synthetic stores, no network)."""
import copy
import sqlite3

import pytest

from app import market_live, points_history as ph, telegram as tg, threshold_overview as to
from app.providers.real import RealProvider
from test_live import store, T_GAME  # noqa: F401  (fixture)
from test_live_markets import sealed  # noqa: F401  (fixture)
from test_nhl_eval import nhl_store  # noqa: F401  (fixture)
from test_telegram import isolated_card_assets, report  # noqa: F401  (fixture)

AS_OF = "2026-11-11T00:00:00Z"


def built(conn, tmp_path):
    hist = ph.connect(tmp_path / "history" / "points_nba.sqlite", create=True)
    out = ph.build(conn, hist, seasons=(2025, 2026), computed_at=AS_OF)
    return hist, out


def test_recompute_matches_the_live_near_tipoff_writer(store):
    t = "2026-10-21T22:00:00Z"                                  # 90 minutes before T_GAME
    live_out = market_live.forecast(store, now=t, policy="near_tipoff", game_ids=["espn:g1"], write=False)
    by = {f["market"]: f for f in live_out["forecasts"]}
    g = store.execute("SELECT * FROM games WHERE game_id='espn:g1'").fetchone()
    rep = ph.Replayer(store)
    for market in ph.MARKETS:
        r = ph.compute(store, rep, g, market, t)
        assert r["model_version"] == live_out["model_version"] and r["dataset_version"] == live_out["dataset_version"]
        assert r["line"] == by[market]["line"] and list(r["p"]) == by[market]["p"]
        assert {"bookmaker": "market"}.get(r["line_source"], r["line_source"]) == by[market]["line_source"]


def test_build_appends_only_clean_nba_seasons_once_and_refuses_the_rest(store, tmp_path):
    hist = ph.connect(tmp_path / "history" / "points_nba.sqlite", create=True)
    with pytest.raises(ph.HistoryRefused):
        ph.build(store, hist, seasons=(2024,), computed_at=AS_OF)
    hist, out = built(store, tmp_path)
    finals = store.execute("SELECT COUNT(*) FROM games WHERE status='final' AND season IN (2025, 2026) "
                           "AND season_type IN ('regular','play_in','postseason')").fetchone()[0]
    assert finals > 0 and out["created"] == finals * len(ph.MARKETS)
    rows = hist.execute("SELECT * FROM points_history").fetchall()
    assert {r["line_source"] for r in rows} == {"model"} and {r["source"] for r in rows} == {ph.SOURCE}
    for r in rows:
        assert r["forecast_at"] == ph.forecast_time(r["tipoff_at"]) < r["tipoff_at"]
        available = store.execute("SELECT COUNT(*) FROM games WHERE status='final' AND result_available_at <= ? "
                                  "AND season_type IN ('regular','play_in','postseason')", (r["forecast_at"],)).fetchone()[0]
        assert r["results_used"] == available         # nothing after the forecast time
    again = ph.build(store, hist, seasons=(2025, 2026), computed_at="2026-11-12T00:00:00Z")
    assert again["created"] == 0 and again["already_recorded"] == out["created"]
    with pytest.raises(sqlite3.DatabaseError):
        hist.execute("UPDATE points_history SET p_a = 0.5")
    with pytest.raises(sqlite3.DatabaseError):
        hist.execute("DELETE FROM points_history")


def test_nhl_history_is_withheld(sealed, tmp_path):
    """Review HIGH-1: the NHL empty-net adjustment was added after the one-time test on these seasons: in-sample."""
    hist = ph.connect(tmp_path / "history" / "points_nhl.sqlite", create=True)
    with pytest.raises(ph.HistoryRefused, match="withheld"):
        ph.build(sealed, hist, seasons=(2025, 2026), computed_at=AS_OF)
    with pytest.raises(SystemExit, match="withheld"):
        ph.main(["--sport", "nhl", "--dry-run"])
    assert ph.history_index(sealed, hist, "total", AS_OF) is None
    provider = RealProvider.__new__(RealProvider)
    provider.sport, provider.conn, provider.now = "nhl", sealed, AS_OF
    assert provider._points_history_index("total", "regular") is None


def test_history_is_a_separate_record_on_the_same_exact_key(store, tmp_path):
    hist, _ = built(store, tmp_path)
    index = ph.history_index(store, hist, "total", AS_OF)
    key, games = max(index.items(), key=lambda kv: len(kv[1]))
    pct, side, source, version, line_source = key
    assert source == to.HISTORY_SOURCE and line_source == "model"
    rec = {"confidence": float(pct) / 100, "side": side, "source": "primary_recorded", "model_version": version,
           "line_source": "model"}
    wins = sum(1 for gr in games.values() if gr == "win")
    losses = sum(1 for gr in games.values() if gr == "loss")
    out = to.percentage_record("nba", "total", rec, "live-game", {}, AS_OF, history_index=index)
    assert (out["wins"], out["losses"], out["settled"]) == (0, 0, 0)          # the live record is untouched
    assert out["history"] == {"source": to.HISTORY_SOURCE, "line_source": "model", "wins": wins, "losses": losses,
                              "settled": wins + losses, "small_sample": wins + losses < to.MIN_LIVE_GRADED} and wins + losses > 0
    bookmaker = to.percentage_record("nba", "total", {**rec, "line_source": "bookmaker"}, "live-game", {}, AS_OF,
                                     history_index=index)
    assert bookmaker["history"]["settled"] == 0                                # a model line never stands in for a bookmaker line
    assert to.percentage_record("nba", "total", rec, "live-game", {}, AS_OF)["history"] is None
    assert ph.history_index(store, None, "total", AS_OF) is None
    assert ph.history_index(store, hist, "reg3", AS_OF) is None


def _provider(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(ph, "path", lambda sport, data_dir=None: tmp_path / "history" / f"points_{sport}.sqlite")
    p = RealProvider.__new__(RealProvider)
    p.sport, p.conn, p.now = "nba", conn, AS_OF
    return p


def test_provider_history_only_for_competitive_games_and_never_breaks_on_a_bad_file(store, tmp_path, monkeypatch):
    p = _provider(store, tmp_path, monkeypatch)
    assert p._points_history_index("total", "regular") is None                  # missing file
    hist, _ = built(store, tmp_path)
    hist.close()
    p = _provider(store, tmp_path, monkeypatch)
    assert p._points_history_index("total", "regular")                          # present
    assert p._points_history_index("total", "preseason") is None                # review LOW-5: preseason never gets history
    bad = tmp_path / "history" / "points_nba.sqlite"
    bad.write_bytes(b"not a database" * 100)                                     # corrupt file
    assert _provider(store, tmp_path, monkeypatch)._points_history_index("total", "regular") is None
    bad.unlink()
    wrong = sqlite3.connect(bad)
    wrong.execute("CREATE TABLE points_history (history_id INTEGER PRIMARY KEY, sport TEXT, market TEXT)")
    wrong.execute("INSERT INTO points_history (sport, market) VALUES ('nba', 'total')")
    wrong.commit()
    wrong.close()                                                                # wrong schema
    assert _provider(store, tmp_path, monkeypatch)._points_history_index("total", "regular") is None


def test_card_names_past_seasons_on_their_own_line_and_fits():
    d = report()
    d["picks"][0]["percentage_history"]["history"] = {"source": "history_recomputed", "line_source": "model", "wins": 41,
                                                      "losses": 37, "settled": 78}
    _, text = tg.message_batches(d, "123")[0]
    lines = text.split("\n")
    record = lines.index("<b>Model 51.8%</b> · 0W–2L (2 settled)")              # the live record line, unchanged
    assert "Past seasons 2024-26, recomputed, model line: 41W–37L" in lines[record:]
    assert "past seasons" not in lines[record].lower()
    plain = copy.deepcopy(report())
    assert "Past seasons" not in tg.message_batches(plain, "123")[0][1]
    none = copy.deepcopy(report())       # a bookmaker-line pick: history exists but never at that key
    none["picks"][0]["percentage_history"]["history"] = {"source": "history_recomputed", "line_source": "bookmaker", "wins": 0,
                                                         "losses": 0, "settled": 0}
    assert "Past seasons" not in tg.message_batches(none, "123")[0][1]
    # adding history changes no card identity: a card already sent is not sent again
    assert tg.message_batches(d, "123")[0][0] == tg.message_batches(plain, "123")[0][0]
    from PIL import Image, ImageDraw
    from app import telegram_card
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    for longest in ("12W–10L (22 settled) · rank 12 of 120 · AVOID · Small sample.",
                    "Past seasons 2024-26, recomputed, model line: 1234W–1199L",
                    "Past seasons 2024-26, recomputed, model line: 29W–0L · Small sample."):
        text_out, font = telegram_card.fit_record(draw, longest)
        assert text_out == longest and font.size >= 17 and draw.textlength(text_out, font=font) <= telegram_card.RECORD_WIDTH
    assert telegram_card.render_card(text)                                      # the picture still renders
