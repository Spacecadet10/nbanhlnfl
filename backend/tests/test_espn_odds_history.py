import json
import sqlite3
from types import SimpleNamespace

import pytest

from app.sources import espn_odds_history as oh


def _price(american):
    return {"american": american}


def _book(pid, name, *, close_ml=("-238", "+195"), open_ml=("-185", "+150"), spread=("-6", "-108", "-112"),
          total=("221", "-112", "-108")):
    side = lambda ml, ps, pr: {"moneyLine": _price(ml), "pointSpread": {"american": ps}, "spread": _price(pr)}  # noqa: E731
    return {"provider": {"id": pid, "name": name},
            "homeTeamOdds": {"close": side(close_ml[0], spread[0], spread[1]), "open": side(open_ml[0], spread[0], spread[1])},
            "awayTeamOdds": {"close": side(close_ml[1], "+" + spread[0].lstrip("-"), spread[2]),
                             "open": side(open_ml[1], "+" + spread[0].lstrip("-"), spread[2])},
            "close": {"total": {"american": total[0]}, "over": _price(total[1]), "under": _price(total[2])},
            "open": {"total": {"american": total[0]}, "over": _price(total[1]), "under": _price(total[2])}}


def body(*books):
    return json.dumps({"count": len(books), "items": list(books)}).encode()


def test_parse_devigs_valid_lines_and_flags_stale_live_and_model_providers():
    rows = oh.parse(body(_book("40", "DraftKings"), _book("1001", "Bet365", close_ml=("-180", "+375")),
                         _book("59", "ESPN Bet - Live Odds"), _book("1002", "accuscore")))
    dk = {(r["phase"], r["market"]): r for r in rows if r["provider"] == "DraftKings"}
    ml = dk[("close", "moneyline")]
    pa, pb = 1 + 100 / 238, 1 + 195 / 100
    assert ml["price_a"] == pytest.approx(pa) and ml["p_a"] == pytest.approx((1 / pa) / (1 / pa + 1 / pb))
    assert dk[("close", "spread")]["line"] == -6 and dk[("close", "total")]["line"] == 221
    bet365 = next(r for r in rows if r["provider"] == "Bet365" and r["phase"] == "close" and r["market"] == "moneyline")
    assert bet365["p_a"] is None and "sum to 0.8" in bet365["invalid"]          # 1/1.556 + 1/4.75 = 0.853
    assert all(r["invalid"].startswith("not a pregame bookmaker") for r in rows if r["provider"] in
               ("ESPN Bet - Live Odds", "accuscore"))


def test_consensus_uses_the_median_of_valid_books_at_the_most_common_line():
    rows = oh.parse(body(_book("1", "A"), _book("2", "B", close_ml=("-250", "+205")), _book("3", "C", close_ml=("-180", "+375")),
                         _book("4", "D", spread=("-6.5", "-110", "-110"))))
    c = oh.consensus(rows, "close", "moneyline")
    assert c["books"] == 3      # C is invalid; A, B and D are valid (D's moneyline equals A's)
    s = oh.consensus(rows, "close", "spread")
    assert s["line"] == -6 and s["books"] == 3
    assert oh.consensus(rows, "close", "nothing") is None


def test_collect_stores_raw_once_records_every_attempt_and_resumes(tmp_path, monkeypatch):
    monkeypatch.setattr(oh.config, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(oh.time, "sleep", lambda s: None)
    replies = {"1": (200, body(_book("40", "DraftKings"))), "2": (404, b""), "3": (500, b""), "4": (200, b"not json")}
    calls = []

    def get(url):
        event = url.split("/events/")[1].split("/")[0]
        calls.append(event)
        code, content = replies[event]
        return SimpleNamespace(status_code=code, content=content)

    first = oh.collect("nba", ["1", "2", "3", "4"], get=get)
    assert first == {"stored": 1, "not_available": 1, "failed": 2, "already": 0}
    files = list(oh.root("nba").glob("1-*.json"))
    assert len(files) == 1 and files[0].read_bytes() == replies["1"][1]
    assert not list(oh.root("nba").glob("4-*"))                      # an unparseable body is never stored
    calls.clear()
    second = oh.collect("nba", ["1", "2", "3", "4"], get=get)
    assert calls == ["3", "4"] and second["already"] == 2              # stored and 404 are not asked again; failures are
    attempts = [json.loads(x) for x in (oh.root("nba") / "attempts.jsonl").read_text().splitlines()]
    assert len(attempts) == 6 and attempts[0]["sha256"]


def test_nhl_events_are_matched_by_us_date_and_both_teams(tmp_path, monkeypatch):
    monkeypatch.setattr(oh.config, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(oh.time, "sleep", lambda s: None)
    conn = sqlite3.connect(":memory:")
    conn.executescript("CREATE TABLE teams (team_id, name); CREATE TABLE games (game_id, tipoff_at, home_team_id, away_team_id, status);"
                       "INSERT INTO teams VALUES ('nhl:7','Buffalo Sabres'),('nhl:28','San Jose Sharks'),('nhl:8','Montréal Canadiens');"
                       "INSERT INTO games VALUES ('nhl:A','2024-01-15T17:00:00Z','nhl:7','nhl:28','final'),"
                       "('nhl:B','2024-01-16T03:30:00Z','nhl:28','nhl:8','final')")
    boards = {"20240115": [("Buffalo Sabres", "San Jose Sharks", "901"), ("San Jose Sharks", "Montreal Canadiens", "902")]}

    def get(url, params=None):
        events = [{"id": i, "competitions": [{"competitors": [{"homeAway": "home", "team": {"displayName": h}},
                                                             {"homeAway": "away", "team": {"displayName": a}}]}]}
                  for h, a, i in boards.get(params["dates"], [])]
        return SimpleNamespace(status_code=200, content=json.dumps({"events": events}).encode())

    out = oh.nhl_event_map(conn, get=get)
    assert out == {"mapped": 2, "unmatched": 0, "failed_days": 0}
    assert json.loads((oh.root("nhl") / "event-map.json").read_text()) == {"nhl:A": "901", "nhl:B": "902"}


def test_an_older_record_without_open_or_close_uses_current_as_the_labelled_close():
    b = _book("40", "DraftKings")
    for side in ("homeTeamOdds", "awayTeamOdds"):
        b[side]["current"] = b[side].pop("close"); b[side].pop("open")
    b["current"] = b.pop("close"); b.pop("open")
    rows = oh.parse(body(b))
    assert {(r["phase"], r["source"]) for r in rows} == {("close", "current")}
    assert oh.consensus(rows, "close", "moneyline")["books"] == 1


def test_request_urls_are_the_documented_endpoints():
    assert oh.ODDS_URL.format(league=oh.CORE["nba"], id="401585099") == \
        "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/events/401585099/competitions/401585099/odds"
    assert oh.ODDS_URL.format(league=oh.CORE["nhl"], id="1") == \
        "https://sports.core.api.espn.com/v2/sports/hockey/leagues/nhl/events/1/competitions/1/odds"
    assert oh.SCOREBOARD_URL.format(league=oh.LEAGUE["nhl"]) == "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard"


def test_a_scoreboard_timeout_skips_the_date_and_the_next_run_maps_it(tmp_path, monkeypatch):
    monkeypatch.setattr(oh.config, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(oh.time, "sleep", lambda s: None)
    conn = sqlite3.connect(":memory:")
    conn.executescript("CREATE TABLE teams (team_id, name); CREATE TABLE games (game_id, tipoff_at, home_team_id, away_team_id, status);"
                       "INSERT INTO teams VALUES ('nhl:7','Buffalo Sabres'),('nhl:28','San Jose Sharks');"
                       "INSERT INTO games VALUES ('nhl:A','2024-01-15T17:00:00Z','nhl:7','nhl:28','final')")
    def timeout(url, params=None):
        raise TimeoutError("handshake timed out")
    assert oh.nhl_event_map(conn, get=timeout, log=lambda m: None) == {"mapped": 0, "unmatched": 0, "failed_days": 1}
    board = {"events": [{"id": "901", "competitions": [{"competitors": [
        {"homeAway": "home", "team": {"displayName": "Buffalo Sabres"}},
        {"homeAway": "away", "team": {"displayName": "San Jose Sharks"}}]}]}]}
    ok = lambda url, params=None: SimpleNamespace(status_code=200, content=json.dumps(board).encode())  # noqa: E731
    assert oh.nhl_event_map(conn, get=ok)["mapped"] == 1


# --- Review fixes (2026-10-16) -----------------------------------------------------------------------------------------
def test_a_price_in_the_line_field_or_mismatched_handicaps_are_invalid():
    rows = oh.parse(body(_book("58", "ESPN BET", spread=("-110", "-110", "-110"), total=("-110", "-110", "-110"))))
    spread = [r for r in rows if r["market"] == "spread"]; total = [r for r in rows if r["market"] == "total"]
    assert spread and all(r["invalid"] for r in spread) and all(r["invalid"] for r in total)
    b = _book("40", "DraftKings")
    b["awayTeamOdds"]["close"]["pointSpread"] = {"american": "+5"}             # home -6, away +5
    assert all(r["invalid"] for r in oh.parse(body(b)) if r["market"] == "spread" and r["phase"] == "close")


def test_pickem_is_a_zero_line():
    b = _book("40", "DraftKings")
    for side in ("homeTeamOdds", "awayTeamOdds"):
        b[side]["close"]["pointSpread"] = {"american": "PK"}
    r = next(r for r in oh.parse(body(b)) if r["market"] == "spread" and r["phase"] == "close")
    assert r["line"] == 0 and r["invalid"] is None


def test_aggregates_and_rating_sites_are_not_books():
    rows = oh.parse(body(_book("1004", "consensus"), _book("1002", "teamrankings"), _book("1005", "betegy")))
    assert rows and all(r["invalid"].startswith("not a pregame bookmaker") for r in rows)


def test_a_book_far_from_the_others_is_an_outlier_and_left_out():
    books = [_book(str(i), f"B{i}") for i in range(3)] + [_book("9", "Swapped", close_ml=("+285", "-350"))]
    rows = oh.consensus_rows(oh.parse(body(*books)))
    swapped = next(r for r in rows if r["provider"] == "Swapped" and r["phase"] == "close" and r["market"] == "moneyline")
    assert swapped["invalid"].startswith("outlier")
    c = oh.consensus(rows, "close", "moneyline")
    assert c["books"] == 3 and c["best_price_b"] < 3.0


def test_tied_line_counts_pick_the_line_nearest_the_median_line():
    books = [_book("1", "A", spread=("-6", "-110", "-110")), _book("2", "B", spread=("-6", "-110", "-110")),
             _book("3", "C", spread=("-7", "-110", "-110")), _book("4", "D", spread=("-7", "-110", "-110")),
             _book("5", "E", spread=("-6.5", "-110", "-110"))]
    assert oh.consensus(oh.parse(body(*books)), "close", "spread")["line"] in (-6, -7)
    books.append(_book("6", "F", spread=("-6", "-110", "-110")))
    assert oh.consensus(oh.parse(body(*books)), "close", "spread")["line"] == -6


def test_same_day_games_of_the_same_teams_are_matched_by_start_time(tmp_path, monkeypatch):
    monkeypatch.setattr(oh.config, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(oh.time, "sleep", lambda s: None)
    conn = sqlite3.connect(":memory:")
    conn.executescript("CREATE TABLE teams (team_id, name); CREATE TABLE games (game_id, tipoff_at, home_team_id, away_team_id, status);"
                       "INSERT INTO teams VALUES ('nhl:18','Nashville Predators'),('nhl:13','Florida Panthers');"
                       "INSERT INTO games VALUES ('nhl:A','2017-09-22T16:00:00Z','nhl:18','nhl:13','final'),"
                       "('nhl:B','2017-09-22T23:00:00Z','nhl:18','nhl:13','final')")
    ev = lambda i, d: {"id": i, "date": d, "competitions": [{"competitors": [  # noqa: E731
        {"homeAway": "home", "team": {"displayName": "Nashville Predators"}},
        {"homeAway": "away", "team": {"displayName": "Florida Panthers"}}]}]}
    board = {"events": [ev("1", "2017-09-22T16:00Z"), ev("2", "2017-09-22T23:00Z")]}
    get = lambda url, params=None: SimpleNamespace(status_code=200, content=json.dumps(board).encode())  # noqa: E731
    oh.nhl_event_map(conn, get=get)
    assert json.loads((oh.root("nhl") / "event-map.json").read_text()) == {"nhl:A": "1", "nhl:B": "2"}


def test_a_torn_last_attempt_line_does_not_swallow_the_next_record(tmp_path, monkeypatch):
    monkeypatch.setattr(oh.config, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(oh.time, "sleep", lambda s: None)
    oh.root("nba").mkdir(parents=True)
    (oh.root("nba") / "attempts.jsonl").write_text('{"event": "0", "outc')          # interrupted write
    get = lambda url: SimpleNamespace(status_code=404, content=b"")  # noqa: E731
    oh.collect("nba", ["7"], get=get)
    assert "7" in oh.stored("nba")["not_available"]
