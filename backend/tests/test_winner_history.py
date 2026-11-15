"""Recomputed past-season winner history (app/winner_history.py): the live near-tipoff computation exactly, tuned seasons
flagged and never counted, append-only, and a record kept apart from the live one (synthetic stores, no network)."""
import copy
import sqlite3

import pytest

from app import near_tipoff, telegram as tg, threshold_overview as to, winner_history as wh
from app.points_history import HistoryRefused
from app.providers.real import RealProvider
from test_live import store, T_GAME  # noqa: F401  (fixture)
from test_live_markets import sealed  # noqa: F401  (fixture)
from test_nhl_eval import nhl_store  # noqa: F401  (fixture)
from test_telegram import isolated_card_assets, report  # noqa: F401  (fixture)

AS_OF = "2026-11-11T00:00:00Z"
CLEAN = {"nba": (2025, 2026), "nhl": (2025, 2026)}


def built(conn, tmp_path, sport="nba"):
    hist = wh.connect(tmp_path / "history" / f"winner_{sport}.sqlite", create=True)
    return hist, wh.build(conn, hist, seasons=wh.HISTORY_SEASONS[sport], computed_at=AS_OF)


def test_recompute_matches_the_live_near_tipoff_writer(store, monkeypatch):
    t = "2026-10-21T22:00:00Z"                                  # 90 minutes before T_GAME
    monkeypatch.setattr(near_tipoff, "results_refreshed_at", lambda conn, now: now)
    live_out = near_tipoff.predict_now(store, now=t, write=False)
    f = next(x for x in live_out["forecasts"] if x["game_id"] == "espn:g1")
    g = store.execute("SELECT * FROM games WHERE game_id='espn:g1'").fetchone()
    r = wh.compute(wh.Replayer(store), g, t)
    assert r["home_win_prob"] == f["home_win_prob"] and r["model_version"] == live_out["model_version"]
    assert r["dataset_version"] == live_out["dataset_version"] and r["results_used"] == live_out["results_used"]


def test_nhl_recompute_matches_the_live_near_tipoff_writer(sealed, monkeypatch):
    from test_live_markets import add_upcoming
    add_upcoming(sealed, start="2026-10-11T19:00:00")
    g = sealed.execute("SELECT * FROM games WHERE status='scheduled'").fetchone()
    t = wh.forecast_time(g["tipoff_at"])
    monkeypatch.setattr(near_tipoff, "results_refreshed_at", lambda conn, now: now)
    live_out = near_tipoff.predict_now(sealed, now=t, write=False)
    f = next(x for x in live_out["forecasts"] if x["game_id"] == g["game_id"])
    r = wh.compute(wh.Replayer(sealed), g, t)
    assert r["home_win_prob"] == f["home_win_prob"] and r["model_version"] == live_out["model_version"]
    assert "hockey_elo" in r["model_version"] and r["model_version"].endswith("-window=2h-policy=near_tipoff")


def test_build_appends_once_flags_tuned_seasons_and_uses_only_available_results(store, tmp_path):
    hist = wh.connect(tmp_path / "history" / "winner_nba.sqlite", create=True)
    with pytest.raises(HistoryRefused):
        wh.build(store, hist, seasons=(2021,), computed_at=AS_OF)
    hist, out = built(store, tmp_path)
    finals = store.execute("SELECT COUNT(*) FROM games WHERE status='final' AND season BETWEEN 2022 AND 2026 "
                           "AND season_type IN ('regular','play_in','postseason')").fetchone()[0]
    assert finals > 0 and out["created"] + out["skipped_missing_result"] == finals
    rows = hist.execute("SELECT * FROM winner_history").fetchall()
    assert {r["season"] for r in rows if r["season_role"] == "tuned"} == {2022, 2023, 2024}
    assert {r["season"] for r in rows if r["season_role"] == "clean"} == {2025, 2026}
    for r in rows:
        assert r["forecast_at"] == wh.forecast_time(r["tipoff_at"]) < r["tipoff_at"]
        available = store.execute("SELECT COUNT(*) FROM games WHERE status='final' AND result_available_at <= ? "
                                  "AND season_type IN ('regular','play_in','postseason')", (r["forecast_at"],)).fetchone()[0]
        assert r["results_used"] == available         # nothing after the forecast time
        assert r["model_version"] == near_tipoff.current_version(store)
    again = wh.build(store, hist, seasons=wh.HISTORY_SEASONS["nba"], computed_at="2026-11-12T00:00:00Z")
    assert again["created"] == 0 and again["already_recorded"] == out["created"]
    with pytest.raises(sqlite3.DatabaseError):
        hist.execute("UPDATE winner_history SET home_win_prob = 0.5")
    with pytest.raises(sqlite3.DatabaseError):
        hist.execute("DELETE FROM winner_history")


def test_a_team_with_an_earlier_result_not_yet_available_gets_no_history_row(store, tmp_path):
    """The live missing_result guard: an earlier game of either team in the last week without an available result."""
    g = store.execute("SELECT * FROM games WHERE status='final' AND season=2025 ORDER BY tipoff_at LIMIT 1 OFFSET 5").fetchone()
    t = wh.forecast_time(g["tipoff_at"])
    other = store.execute("SELECT game_id FROM games WHERE status='final' AND season=2025 AND game_id != ? AND "
                          "(home_team_id=? OR away_team_id=?) AND tipoff_at < ? ORDER BY tipoff_at DESC LIMIT 1",
                          (g["game_id"], g["home_team_id"], g["home_team_id"], g["tipoff_at"])).fetchone()[0]
    assert not wh.unavailable_earlier(store, g, t)
    earlier = near_tipoff._fmt(near_tipoff._parse(t) - near_tipoff.WINDOW)
    later = near_tipoff._fmt(near_tipoff._parse(t) + near_tipoff.WINDOW)
    store.execute("UPDATE games SET tipoff_at=?, result_available_at=? WHERE game_id=?", (earlier, later, other))
    assert wh.unavailable_earlier(store, g, t) == [other]
    hist, out = built(store, tmp_path)
    assert out["skipped_missing_result"] >= 1
    assert not hist.execute("SELECT 1 FROM winner_history WHERE game_id=?", (g["game_id"],)).fetchone()


def test_tuned_seasons_never_count_toward_history_rank_or_avoid(store, tmp_path):
    hist, _ = built(store, tmp_path)
    index = wh.history_index(store, hist, "winner", AS_OF)
    counted = {gid for games in index.values() for gid in games}
    seasons = {r[0] for r in store.execute(f"SELECT DISTINCT season FROM games WHERE game_id IN "
                                           f"({','.join('?' * len(counted))})", sorted(counted))}
    assert counted and seasons <= set(CLEAN["nba"])                         # only the clean test seasons
    tuned = {r["game_id"] for r in hist.execute("SELECT game_id FROM winner_history WHERE season_role='tuned'")}
    assert tuned and not tuned & counted
    # A tuned row mislabelled 'clean' still never counts (the season list decides, not the row).
    raw = sqlite3.connect(tmp_path / "history" / "winner_nba.sqlite")
    r = dict(hist.execute("SELECT * FROM winner_history WHERE season_role='tuned' LIMIT 1").fetchone())
    r.update(history_id=None, season_role="clean", idempotency_key="mislabelled")
    raw.execute(f"INSERT INTO winner_history ({', '.join(r)}) VALUES ({', '.join('?' * len(r))})", list(r.values()))
    raw.commit()
    assert r["game_id"] not in {gid for games in wh.history_index(store, hist, "winner", AS_OF).values() for gid in games}
    # Rank and AVOID read the live index only: a losing history leaves the live rank, counts and AVOID untouched.
    key, games = max(index.items(), key=lambda kv: len(kv[1]))
    pct, side, _, version, _ = key
    rec = {"confidence": float(pct) / 100, "side": side, "source": "primary_recorded", "model_version": version,
           "line_source": "none"}
    live_key = to._percentage_key(rec)
    live_index = {live_key: {"x1": "win", "x2": "win", "x3": "win"}}
    losing = {key: {f"h{i}": "loss" for i in range(50)}}
    with_hist = to.percentage_record("nba", "winner", rec, "live-game", live_index, AS_OF, history_index=losing)
    without = to.percentage_record("nba", "winner", rec, "live-game", live_index, AS_OF)
    for k in ("wins", "losses", "settled", "rank", "ranked", "state"):
        assert with_hist[k] == without[k]
    assert with_hist["history"]["losses"] == 50 and with_hist["rank"] == 1
    d = report()
    d["picks"][0].update(market="winner", market_title="Winner", pick_label="ORL win", opposite_pick_label="BOS win")
    d["picks"][0]["percentage_history"].update(market="winner", line_source="none", wins=3, losses=0, settled=3, rank=1,
                                               ranked=1, small_sample=True,
                                               history={"source": "history_recomputed", "line_source": "none", "wins": 0,
                                                        "losses": 50, "settled": 50})
    _, text = tg.message_batches(d, "123")[0]
    assert "AVOID" not in text and "Against model" not in text


def test_nhl_winner_history_builds_clean_seasons_only_counted(sealed, tmp_path):
    hist, out = built(sealed, tmp_path, "nhl")
    assert out["season_roles"]["tuned"] > 0 and out["season_roles"]["clean"] > 0
    index = wh.history_index(sealed, hist, "winner", AS_OF)
    counted = {gid for games in index.values() for gid in games}
    seasons = {r[0] for r in sealed.execute(f"SELECT DISTINCT season FROM games WHERE game_id IN "
                                            f"({','.join('?' * len(counted))})", sorted(counted))}
    assert seasons <= set(CLEAN["nhl"]) and 2024 not in seasons
    assert wh.history_index(sealed, hist, "total", AS_OF) is None
    assert wh.history_index(sealed, None, "winner", AS_OF) is None


def _provider(conn, tmp_path, monkeypatch, sport="nba"):
    monkeypatch.setattr(wh, "path", lambda s, data_dir=None: tmp_path / "history" / f"winner_{s}.sqlite")
    p = RealProvider.__new__(RealProvider)
    p.sport, p.conn, p.now = sport, conn, AS_OF
    return p


def test_provider_gives_winner_history_for_competitive_games_only_and_survives_a_bad_file(store, tmp_path, monkeypatch):
    p = _provider(store, tmp_path, monkeypatch)
    assert p._points_history_index("winner", "regular") is None                 # missing file
    hist, _ = built(store, tmp_path)
    hist.close()
    p = _provider(store, tmp_path, monkeypatch)
    assert p._points_history_index("winner", "regular")                         # present
    assert p._points_history_index("winner", "preseason") is None               # preseason never gets history
    bad = tmp_path / "history" / "winner_nba.sqlite"
    bad.write_bytes(b"not a database" * 100)
    assert _provider(store, tmp_path, monkeypatch)._points_history_index("winner", "regular") is None


def test_winner_card_line_is_named_without_model_line_fits_and_keeps_identity():
    d = report()
    d["picks"][0].update(market="winner", market_title="Winner", pick_label="ORL win", opposite_pick_label="BOS win")
    d["picks"][0]["percentage_history"].update(market="winner", line_source="none")
    plain = copy.deepcopy(d)
    d["picks"][0]["percentage_history"]["history"] = {"source": "history_recomputed", "line_source": "none", "wins": 61,
                                                      "losses": 44, "settled": 105}
    _, text = tg.message_batches(d, "123")[0]
    lines = text.split("\n")
    record = lines.index("<b>Model 51.8%</b> · 0W–2L (2 settled)")
    assert "Past seasons 2024-26, recomputed: 61W–44L" in lines[record:]
    assert "model line: 61W" not in text
    assert tg.message_batches(d, "123")[0][0] == tg.message_batches(plain, "123")[0][0]   # no resend
    from PIL import Image, ImageDraw
    from app import telegram_card
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    for longest in ("Past seasons 2024-26, recomputed: 1234W–1199L", "Past seasons 2024-26, recomputed: 29W–0L · Small sample."):
        text_out, font = telegram_card.fit_record(draw, longest)
        assert text_out == longest and draw.textlength(text_out, font=font) <= telegram_card.RECORD_WIDTH
    assert telegram_card.render_card(text)


def test_past_seasons_line_names_its_seasons_and_flags_a_small_sample_like_the_live_line():
    """Review MEDIUM: a short history gets the live line's own small-sample wording and threshold (MIN_LIVE_GRADED)."""
    from app import points_history as ph
    clean = sorted(s for s in wh.HISTORY_SEASONS["nba"] if s not in wh.TUNED_SEASONS["nba"])
    for seasons in (clean, sorted(s for s in wh.HISTORY_SEASONS["nhl"] if s not in wh.TUNED_SEASONS["nhl"]),
                    list(ph.HISTORY_SEASONS["nba"])):
        assert f"{seasons[0] - 1}-{str(seasons[-1])[2:]}" == tg.PAST_SEASONS
    for line_source, prefix in (("none", "Past seasons 2024-26, recomputed: "),
                                ("model", "Past seasons 2024-26, recomputed, model line: ")):
        for wins, losses, small in ((1, 0, True), (to.MIN_LIVE_GRADED - 1, 0, True), (to.MIN_LIVE_GRADED, 0, False)):
            d = report()
            d["picks"][0]["percentage_history"].update(line_source=line_source)
            plain = copy.deepcopy(d)
            d["picks"][0]["percentage_history"]["history"] = {"source": "history_recomputed", "line_source": line_source,
                                                              "wins": wins, "losses": losses, "settled": wins + losses}
            key, text = tg.message_batches(d, "123")[0]
            want = f"{prefix}{wins}W–{losses}L" + (" · Small sample." if small else "")
            assert want in text.split("\n")
            assert key == tg.message_batches(plain, "123")[0][0]
            assert tg.card_identity(text) == tg.card_identity(tg.message_batches(plain, "123")[0][1])
            from app import telegram_card
            assert telegram_card.render_card(text)
