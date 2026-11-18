"""Soccer live exact-percentage record, sportsbook comparison and record_basis on the card contract (app/soccer_record.py).
Real sealed 1X2 model on synthetic ESPN payloads (tests/test_soccer_live.py fixture); results are written by hand."""
from datetime import datetime, timedelta, timezone

import pytest

from app import soccer_live, soccer_record as sr, threshold_overview as to
from test_soccer_live import NOW, TOMORROW, live  # noqa: F401  (fixture)
from test_soccer_markets_live import _put_quote

MATCH = "soccer:eng.1:3"


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def stamp():
    return _iso(datetime.now(timezone.utc) + timedelta(seconds=5))


@pytest.fixture
def forecast(live):
    assert soccer_live.run(live, day=TOMORROW.isoformat())["status"] == "ok"
    return live


def settle(c, home, away, available=None):
    c.execute("UPDATE soccer_matches SET status='final', decided_in='regulation', home_score=?, away_score=?, "
              "result_available_at=? WHERE match_id=?", (home, away, available or _iso(NOW - timedelta(minutes=1)), MATCH))


def pick_of(c, market, as_of=None):
    return next(p for p in sr.all_picks(c, as_of or stamp()) if p["market"] == market and p["game_id"] == MATCH)


def clone(pick, game_id="soccer:eng.1:other"):
    """The same exact bucket on another match (rec_of reads the stored forecast row of the original, so same version)."""
    return {**pick, "game_id": game_id}


def test_a_draw_no_bet_draw_is_a_push_counted_apart_and_never_a_win_or_loss(forecast):
    settle(forecast, 1, 1)
    as_of = stamp()
    dnb, one = pick_of(forecast, "draw_no_bet", as_of), pick_of(forecast, "1x2_regulation", as_of)
    assert dnb["grade"] == "push" and one["grade"] in ("win", "loss")
    index = sr.build_index(forecast, as_of)
    assert list(index["draw_no_bet"].values())[0][MATCH] == "push"
    r = sr.percentage_history(forecast, clone(dnb), index, as_of, None)
    assert (r["wins"], r["losses"], r["pushes"], r["settled"]) == (0, 0, 1, 0) and r["excluded"]["push"] == 1
    assert r["sport"] == "soccer" and r["market"] == "draw_no_bet" and r["source"] == "soccer_live" and r["line_source"] == "model"
    own = sr.percentage_history(forecast, dnb, index, as_of, None)             # the match is never in its own record
    assert (own["wins"], own["losses"], own["pushes"]) == (0, 0, 0)


def test_the_probability_stays_the_stored_win_chance_for_draw_no_bet(forecast):
    dnb = pick_of(forecast, "draw_no_bet")
    probs = dnb["outcome_probabilities"]
    assert dnb["probability"] == probs["win"] and probs["push"] > 0
    assert to.displayed_percentage(dnb["probability"]) == sr.percentage_history(forecast, dnb, {}, stamp(), None)["percentage"]


def test_a_result_counts_only_once_available(forecast):
    settle(forecast, 2, 0, available=_iso(NOW + timedelta(hours=3)))
    as_of = stamp()
    one = pick_of(forecast, "1x2_regulation", as_of)
    assert one["grade"] == "pending"
    r = sr.percentage_history(forecast, clone(one), sr.build_index(forecast, as_of), as_of, None)
    assert (r["wins"], r["losses"], r["settled"]) == (0, 0, 0) and r["excluded"]["pending"] >= 1
    settle(forecast, 2, 0)
    later = stamp()
    one = pick_of(forecast, "1x2_regulation", later)
    r = sr.percentage_history(forecast, clone(one), sr.build_index(forecast, later), later, None)
    assert r["settled"] == 1 and (r["wins"] + r["losses"]) == 1


def test_a_model_change_restarts_the_soccer_record(forecast, monkeypatch):
    settle(forecast, 2, 0)
    as_of = stamp()
    one = pick_of(forecast, "1x2_regulation", as_of)
    index = sr.build_index(forecast, as_of)
    assert sr.percentage_history(forecast, clone(one), index, as_of, None)["settled"] == 1
    real = sr.model_version
    monkeypatch.setattr(sr, "model_version", lambda c, p: real(c, p) + "-v2" if p["game_id"].endswith("other") else real(c, p))
    assert sr.percentage_history(forecast, clone(one), index, as_of, None)["settled"] == 0


def test_soccer_exact_buckets_57_3_and_57_4_are_separate_in_live_and_telegram(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    to.telegram_history_path().parent.mkdir(parents=True, exist_ok=True)
    to.telegram_history_path().write_text(json.dumps({"period": {"from": "2026-11-14", "to": "2026-11-14"}, "rows": [
        dict(sport="soccer", market="1x2_regulation", percentage="57.3", side="home", line_source="none", game_id="t1", grade="win"),
        dict(sport="soccer", market="1x2_regulation", percentage="57.4", side="home", line_source="none", game_id="t2", grade="loss")]}))
    rec = {"confidence": .5731, "side": "home", "source": "soccer_live", "model_version": "elo:abc", "line_source": "none",
           "market_title": "Match result (90 min)", "direction": "home win"}
    index = {to._percentage_key({**rec, "confidence": .5732}): {"m1": "win"}, to._percentage_key({**rec, "confidence": .5741}): {"m2": "loss"}}
    r = to.percentage_record("soccer", "1x2_regulation", rec, "self", index, "2026-11-18T00:00:00Z")
    assert (r["percentage"], r["wins"], r["losses"]) == ("57.3", 1, 0) and (r["telegram"]["wins"], r["telegram"]["losses"]) == (1, 0)


def test_draw_no_bet_sportsbook_is_conditional_on_no_draw_for_both_app_and_book(forecast):
    early = _iso(datetime.now(timezone.utc) - timedelta(minutes=3))
    for side, a in (("home", -110), ("draw", 260), ("away", 280)):
        _put_quote(forecast, MATCH, "1x2", side, a, None, early)
    dnb = pick_of(forecast, "draw_no_bet")
    sb = sr.sportsbook(forecast, dnb)
    inv = {"home": 1 / (1 + 100 / 110), "draw": 1 / 3.6, "away": 1 / 3.8}
    total = sum(inv.values())
    side = dnb["side"]
    assert sb["status"] == "exact" and sb["basis"].startswith("conditional on no draw")
    assert sb["book_probability"] == pytest.approx((inv[side] / total) / ((inv["home"] + inv["away"]) / total))
    p = dnb["outcome_probabilities"]
    assert sb["app_probability"] == pytest.approx(p["win"] / (p["win"] + p["loss"])) and sb["app_probability"] > dnb["probability"]
    dc = sr.sportsbook(forecast, pick_of(forecast, "double_chance"))
    assert dc["status"] == "exact" and dc["basis"] == "derived from 1X2 prices"


def test_a_quote_saved_after_the_forecast_is_never_used(forecast):
    late = _iso(datetime.now(timezone.utc) + timedelta(minutes=30))
    for side, a in (("home", -110), ("draw", 260), ("away", 280)):
        _put_quote(forecast, MATCH, "1x2", side, a, None, late)
    sb = sr.sportsbook(forecast, pick_of(forecast, "double_chance"))
    assert sb["status"] == "no_quote" and sb["book_probability"] is None and sb["reason"]


def test_attach_gives_every_soccer_pick_the_contract_fields_and_keeps_the_earlier_forecasts(forecast):
    picks = [p for p in sr.all_picks(forecast, stamp()) if p["game_id"] == MATCH]
    sr.attach(forecast, picks, stamp(), published={})
    assert {p["market"] for p in picks} == {"1x2_regulation", "double_chance", "draw_no_bet"}
    for p in picks:
        h, b = p["percentage_history"], p["record_basis"]
        assert h["sport"] == "soccer" and h["coverage"]["live_since"] and "pushes" in h
        assert b["pregame_forecasts"] == 1 and b["earlier"] == [] and b["published"] is None and b["probability"] == p["probability"]
        assert p["sportsbook"]["status"] in ("exact", "no_quote", "unsupported") and p["model_version"].startswith(("elo:", "intl-elo:"))
        assert "line" in p


def test_markets_selections_the_page_reads_carry_the_contract_fields_of_their_pick(forecast):
    """D1: the web page renders derived.selections / families[].selections, not matches[].picks."""
    from app import soccer_markets_live as sml
    as_of = stamp()
    matches = sml.match_markets(forecast, competition="eng.1", since="", as_of=as_of)
    sr.attach_to_markets(forecast, matches, as_of, "")
    m = next(x for x in matches if x["match_id"] == MATCH)
    assert m["picks"]
    sels = m["derived"]["selections"] + [s for f in m["families"].values() for s in f["selections"]]
    for p in m["picks"]:
        hit = [s for s in sels if (s["market"], s["side"], s["line"]) == (p["market"], p["side"], p["line"])]
        assert hit, p["market"]
        for s in hit:
            assert s["percentage_history"]["sport"] == "soccer" and s["record_basis"]["probability"] == p["probability"]
            assert s["pick_label"] == p["pick_label"] and s["sportsbook"]["status"] in ("exact", "no_quote", "unsupported")


def test_dnb_conditional_helper_is_the_one_used_and_bucket_key_stays_the_stored_90_minute_percentage(forecast):
    dnb = pick_of(forecast, "draw_no_bet")
    probs = dnb["outcome_probabilities"]
    assert sr.sm.conditional_on_no_draw(probs) == pytest.approx(probs["win"] / (probs["win"] + probs["loss"]))
    before = sr.percentage_history(forecast, clone(dnb), {}, stamp(), None)
    sr.sportsbook(forecast, dnb)                       # computing the comparison changes neither the pick nor its bucket
    assert dnb["probability"] == probs["win"]
    assert sr.percentage_history(forecast, clone(dnb), {}, stamp(), None)["percentage"] == before["percentage"] == to.displayed_percentage(probs["win"])
