"""Recovered Telegram predictions in Performance and Prediction History (app/telegram_history.py).
Core rules on hand-written rows; API tests on the synthetic NBA / NFL / soccer stores. No network, no live data."""
import json

import pytest

from app import coverage, nfl_live, near_tipoff, soccer_live, soccer_record, telegram_history as th, threshold_overview as to
from test_canonical import AFTER, G1, record_now, winner_rows
from test_live import T_GAME, client, event, ingest_events, store  # noqa: F401  (fixtures)
from test_near_tipoff import NOW, ready  # noqa: F401  (fixture)
from test_nfl_live import NOW as NFL_NOW, live_store  # noqa: F401  (fixture)
from test_soccer_live import TOMORROW, live  # noqa: F401  (fixture)


def rec(gid="g1", market="winner", pct="60.0", side="a", grade="win", line=None, home_line=None, line_source="none", day="2026-11-05", **kw):
    return {"sport": "nba", "game_id": gid, "market": market, "percentage": pct, "side": side, "grade": grade, "line": line,
            "home_line": home_line, "line_source": line_source, "start_date_local": day, "start_at": day + "T23:00:00Z",
            "matchup": "OKC @ CLE", "pick": "CLE win", "message_id": 1, "sent_at_local": day + "T17:00:00", "grade_source": "telegram_export",
            "final_score": "OKC 90 - 100 CLE", "recommended": False, "card_line_text": None, "price": None, "earlier_versions": [], **kw}


def liv(gid="g1", market="winner", pct="60.0", side="a", grade="win", home_line=None, line_source="none", day="2026-11-05", versions=(), **kw):
    return {"game_id": gid, "market": market, "percentage": pct, "side": side, "grade": grade, "home_line": home_line,
            "line_source": line_source, "game_date": day, "start_at": day + "T23:00:00Z", "forecast_id": 7, "generated_at": day + "T22:00:00Z",
            "model_version": "m1", "pick": "CLE win", "matchup": "OKC @ CLE", "home": "CLE", "away": "OKC", "versions": list(versions), **kw}


def counts(x):
    return (x["predictions"], x["win"], x["loss"], x["push"], x["unsettled"])


def test_an_overlapping_prediction_is_counted_once_and_a_different_version_stays_visible():
    rows = [rec("only"), rec("same"), rec("diff", pct="62.0", side="b", grade="loss")]
    live = [liv("same"), liv("diff", pct="57.0"), liv("new", grade="loss", day="2026-11-20")]
    out = th.combine("nba", rows, live)
    got = {r["game_id"]: (r["overlap"]["status"], r["counted"]) for r in out["rows"]}
    assert got == {"only": ("none", True), "same": ("same", False), "diff": ("different", False)}
    diff = next(r for r in out["rows"] if r["game_id"] == "diff")
    assert diff["overlap"]["live"]["percentage"] == "57.0" and diff["overlap"]["live"]["forecast_id"] == 7
    assert diff["overlap"]["grade_conflict"] is True and diff["percentage"] == "62.0"      # the original, never today's number
    s = out["summary"]
    assert counts(s["recovered"]) == (3, 2, 1, 0, 0)
    assert counts(s["live"]) == (3, 2, 1, 0, 0)
    assert counts(s["combined"]) == (4, 3, 1, 0, 0)              # only + same + diff(live) + new: each game and market once
    assert s["overlap"] == {"same": 1, "earlier_live_version": 0, "different": 1, "grade_conflicts": 1}
    assert s["recovered"]["recovered_only"] == 1


def test_the_same_side_and_percentage_at_another_line_is_a_different_version():
    rows = [rec(market="spread", line=-4.5, home_line=4.5, side="b", line_source="bookmaker")]
    assert th.combine("nba", rows, [liv(market="spread", home_line=4.5, side="b")])["rows"][0]["overlap"]["status"] == "same"
    assert th.combine("nba", rows, [liv(market="spread", home_line=5.5, side="b")])["rows"][0]["overlap"]["status"] == "different"


def test_a_card_that_matches_an_earlier_live_version_is_named_so():
    older = {"forecast_id": 3, "generated_at": "2026-11-05T21:30:00Z", "percentage": "60.0", "side": "a", "home_line": None}
    r = th.combine("nba", [rec()], [liv(pct="58.0", versions=[older])])["rows"][0]
    assert (r["overlap"]["status"], r["overlap"]["version_id"], r["counted"]) == ("earlier_live_version", 3, False)


def test_missing_outcomes_stay_unsettled_and_missing_prices_stay_unpriced():
    out = th.combine("nba", [rec("a", grade="unsettled", grade_source=None, final_score=None), rec("b", grade="push"), rec("c", grade="loss")], [])
    s = out["summary"]["combined"]
    assert counts(s) == (3, 0, 1, 1, 1) and s["settled"] == 1 and s["win_rate"] == 0.0
    assert out["summary"]["recovered"]["unpriced"] == 3 and out["summary"]["recovered"]["priced"] == 0
    assert all(r["price"] is None and r["model_version"] is None for r in out["rows"])
    assert all(r["label"] == "Recovered Telegram prediction — model version unknown" for r in out["rows"])


def test_exact_percentages_are_never_rounded_together_and_count_each_game_once():
    rows = [rec("a", pct="57.3"), rec("b", pct="57.4", grade="loss"), rec("same", pct="57.3")]
    out = th.combine("nba", rows, [liv("same", pct="57.3"), liv("n", pct="57.3", grade="loss", day="2026-11-20")])
    got = {b["percentage"]: b for b in out["exact_percentages"]}
    assert set(got) == {"57.3", "57.4"}
    assert [(got["57.3"][k]["win"], got["57.3"][k]["loss"]) for k in ("recovered", "live", "combined")] == [(2, 0), (1, 1), (2, 1)]
    assert got["57.3"]["overlap"] == 1
    assert (got["57.4"]["combined"]["win"], got["57.4"]["combined"]["loss"], got["57.4"]["live"]["predictions"]) == (0, 1, 0)
    r = next(x for x in out["rows"] if x["game_id"] == "a")
    assert (r["bucket"]["combined"]["win"], r["bucket"]["combined"]["loss"]) == (2, 1)
    later = th.combine("nba", rows, [liv("n", pct="57.3", grade="loss", day="2026-11-20")], date_to="2026-11-10")
    r = next(x for x in later["rows"] if x["game_id"] == "a")      # a date filter never narrows the row's own exact-% record
    assert (r["bucket"]["combined"]["win"], r["bucket"]["combined"]["loss"]) == (2, 1)
    assert [b["live"]["predictions"] for b in later["exact_percentages"] if b["percentage"] == "57.3"] == [0]


def test_date_team_market_and_competition_filters_apply_to_rows_and_totals():
    rows = [rec("a", day="2026-11-02", home="CLE", away="OKC"), rec("b", day="2026-11-09", home="BOS", away="NY", matchup="NY @ BOS"),
            rec("c", market="total", line_source="bookmaker", line=220.5, home_line=220.5, day="2026-11-02", home="CLE", away="OKC")]
    live = [liv("l", day="2026-11-20")]
    f = lambda **kw: th.combine("nba", rows, live, **kw)          # noqa: E731
    assert [r["game_id"] for r in f(market="winner")["rows"]] == ["b", "a"]                        # newest first
    assert [r["game_id"] for r in f(market="winner", date_from="2026-11-02", date_to="2026-11-08")["rows"]] == ["a"]
    assert f(date_to="2026-11-08")["summary"]["live"]["predictions"] == 0
    assert [r["game_id"] for r in f(market="winner", team="bos")["rows"]] == ["b"]
    one = f(market="winner")
    assert {m["market"]: m["recovered"]["predictions"] for m in one["markets"]} == {"winner": 2, "total": 1}    # every market stays listed
    assert one["summary"]["recovered"]["predictions"] == 2 and f()["summary"]["recovered"]["predictions"] == 3


def _file(tmp_path, rows, **extra):
    path = tmp_path / "research" / "telegram_history.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"source": "telegram_cards", "period": {"from": "2026-11-02", "to": "2026-11-16"}, "rows": rows,
                                "reconciliation": {"nba": {"source_rows": 5, "unaccounted": 0}}, "not_predictions": [], **extra}))


@pytest.fixture
def data(tmp_path, monkeypatch):
    path = tmp_path / "tg"
    monkeypatch.setattr(to, "telegram_history_path", lambda: path / "research" / "telegram_history.json")
    return path


def test_the_nba_endpoint_shows_the_original_row_and_counts_the_live_forecast_once(ready, client, data):
    record_now(ready)
    [row] = winner_rows(ready)
    ingest_events(ready, [event("g1", T_GAME, "1", "2", "STATUS_FINAL", 110, 100)], "2026-10-22T03:00:00Z")
    client.now = AFTER
    side = "a" if row["p_a"] >= 0.5 else "b"
    pct = to.displayed_percentage(row["confidence"])
    _file(data, [rec(G1, pct=pct, side=side, grade="win" if side == "a" else "loss", day="2026-10-21"),
                 rec("espn:old", pct="71.5", day="2026-10-20")])
    body = client.get("/api/telegram-history", params={"market": "winner"}).json()
    assert body["available"] is True and body["sport"] == "nba"
    assert [(r["game_id"], r["percentage"], r["overlap"]["status"], r["counted"]) for r in body["rows"]] == [
        (G1, pct, "same", False), ("espn:old", "71.5", "none", True)]
    assert body["summary"]["combined"]["predictions"] == 2 and body["summary"]["live"]["predictions"] == 1
    assert body["rows"][0]["overlap"]["live"]["forecast_id"] == row["forecast_id"]
    assert body["reconciliation"]["source_rows"] == 5
    assert "110" in body["live_rows"][0]["final_score"] and "100" in body["live_rows"][0]["final_score"]
    old = client.get("/api/telegram-history", params={"market": "winner", "date_to": "2026-10-20"}).json()
    assert [r["game_id"] for r in old["rows"]] == ["espn:old"]
    assert client.get("/api/telegram-history", params={"market": "nope"}).status_code == 404
    assert client.get("/api/telegram-history", params={"date_from": "2026-11-02", "date_to": "2026-11-01"}).status_code == 422
    # the live record itself is unchanged
    assert client.get("/api/canonical/performance").json()["competitive"]["forecast"] == 1


def test_a_missing_or_unreadable_file_and_a_sport_without_rows_say_so_never_0_0(ready, client, data):
    client.now = AFTER
    body = client.get("/api/telegram-history").json()
    assert (body["available"], body["reason"], body["rows"]) == (False, "file_missing", []) and body["summary"]["recovered"] is None
    _file(data, [dict(rec(), sport="nhl")])
    assert client.get("/api/telegram-history").json()["reason"] == "no_rows_for_sport"
    (data / "research" / "telegram_history.json").write_text("{bad")
    assert client.get("/api/telegram-history").json()["reason"] == "unreadable"


def test_the_nfl_endpoint_combines_with_the_nfl_cohort(live_store, data, monkeypatch):
    from fastapi.testclient import TestClient
    from app import db, main
    assert nfl_live.record(live_store, now=NFL_NOW)["recorded"] == 5
    live_store.commit()
    monkeypatch.setattr(main.app, "dependency_overrides", {})
    monkeypatch.setenv("NBA_API_MODE", "real")
    monkeypatch.setattr(db, "utc_now", lambda: "2026-11-03T04:00:00Z")
    _file(data, [dict(rec("nfl:live", pct="99.9", grade="loss", day="2026-11-02"), sport="nfl"), dict(rec("nfl:old", day="2026-11-01"), sport="nfl")])
    body = TestClient(main.app).get("/api/nfl/telegram-history", params={"market": "winner"}).json()
    assert {r["game_id"]: (r["overlap"]["status"], r["counted"]) for r in body["rows"]} == {
        "nfl:live": ("different", False), "nfl:old": ("none", True)}
    assert body["summary"]["live"]["predictions"] == 1 and body["summary"]["live"]["unsettled"] == 1
    assert body["summary"]["combined"]["predictions"] == 2


def test_soccer_live_items_are_one_per_match_and_market_and_only_the_match_s_own_market_overlaps(live):
    assert soccer_live.run(live, day=TOMORROW.isoformat())["status"] == "ok"
    picks = soccer_record.all_picks(live, "2999-01-01T00:00:00Z")
    one = next(p for p in picks if p["market"] == "1x2_regulation")
    items = th.soccer_live_items(live, "2999-01-01T00:00:00Z")
    assert len({(i["game_id"], i["market"]) for i in items}) == len(items)
    want = {(p["game_id"], p["market"], soccer_record.pick_side(p), to.displayed_percentage(p["probability"]), p["grade"], p["pick_label"],
             soccer_record.line_source(p), soccer_record.model_version(live, p)) for p in picks if p["market"] in th.SOCCER_MARKETS}
    assert want and {(i["game_id"], i["market"], i["side"], i["percentage"], i["grade"], i["pick"], i["line_source"], i["model_version"])
                     for i in items} == want            # the same picks the cards and the soccer record grade
    r = dict(rec(one["game_id"], market="1x2_regulation", pct=to.displayed_percentage(one["probability"]), side=one["outcome"]), sport="soccer")
    out = th.combine("soccer", [r], items, market="1x2_regulation")
    assert out["rows"][0]["overlap"]["status"] == "same"
    assert out["rows"][0]["market_title"] == "Match result (90 min)"


def test_unsettled_rows_never_enter_the_card_bucket_records(tmp_path, monkeypatch):
    path = tmp_path / "telegram_history.json"
    monkeypatch.setattr(to, "telegram_history_path", lambda: path)
    path.write_text(json.dumps({"period": None, "rows": [dict(rec("u", grade="unsettled"), market="winner", percentage="60.0")]}))
    r = to.telegram_record("nba", "winner", {"confidence": .6, "side": "a", "line_source": "none"}, "self")
    assert (r["wins"], r["losses"], r["no_history"]) == (0, 0, True)
