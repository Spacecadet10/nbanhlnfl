"""scripts/build_telegram_history.py: recovered Telegram cards -> <data>/research/telegram_history.json (Nov 17 rules).
Synthetic stores built by hand (only the columns the builder reads); no network, no live data."""
import importlib.util
import sqlite3
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_telegram_history.py"
spec = importlib.util.spec_from_file_location("build_telegram_history", SCRIPT)
bth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bth)

POINT_DDL = """
CREATE TABLE teams (team_id TEXT PRIMARY KEY, abbr TEXT, name TEXT);
CREATE TABLE games (game_id TEXT PRIMARY KEY, tipoff_at TEXT, status TEXT, home_score INT, away_score INT, home_team_id TEXT, away_team_id TEXT);
CREATE TABLE game_forecasts (forecast_id INTEGER PRIMARY KEY, game_id TEXT, market TEXT, status TEXT, generated_at TEXT, tipoff_at TEXT);
CREATE TABLE game_decisions (game_id TEXT, decision TEXT);
"""
SOCCER_DDL = """
CREATE TABLE teams (team_id TEXT PRIMARY KEY, abbr TEXT, name TEXT);
CREATE TABLE soccer_matches (match_id TEXT PRIMARY KEY, kickoff_at TEXT, status TEXT, decided_in TEXT, home_score INT, away_score INT,
                             home_team_id TEXT, away_team_id TEXT);
CREATE TABLE soccer_live_forecasts (forecast_id INTEGER PRIMARY KEY, match_id TEXT, generated_at TEXT);
"""


def make(tmp_path, games=(), forecasts=(), matches=(), soccer_forecasts=()):
    for sport in ("nba", "nhl", "nfl"):
        db = sqlite3.connect(tmp_path / f"{sport}.sqlite")
        db.executescript(POINT_DDL)
        db.executemany("INSERT INTO teams VALUES (?,?,?)", [("h", "CLE", "Cavaliers"), ("a", "OKC", "Thunder")])
        if sport == "nba":
            db.executemany("INSERT INTO games VALUES (?,?,?,?,?,'h','a')", games)
            db.executemany("INSERT INTO game_forecasts (game_id, market, status, generated_at, tipoff_at) VALUES (?,?,?,?,?)", forecasts)
        db.commit()
        db.close()
    db = sqlite3.connect(tmp_path / "soccer.sqlite")
    db.executescript(SOCCER_DDL)
    db.executemany("INSERT INTO teams VALUES (?,?,?)", [("th", "TAH", "Tahiti"), ("tc", "COK", "Cook Islands")])
    db.executemany("INSERT INTO soccer_matches VALUES (?,?,?,?,?,?,'th','tc')", matches)
    db.executemany("INSERT INTO soccer_live_forecasts (match_id, generated_at) VALUES (?,?)", soccer_forecasts)
    db.commit()
    db.close()


def row(sport="NBA", gid="espn:1", key="winner", sel="OKC win", pct=54, result="win", line_source="", **kw):
    return dict(message_id=1, sent_at_local="2026-11-02T17:00:00", sport=sport, game_id=gid, matchup="OKC @ CLE", starts="x",
                final_score=None, market=key.upper(), market_key=key, selection=sel, percent=pct, line_source=line_source,
                recommended=False, result=result, **kw)


GAME = ("espn:1", "2026-11-02T00:00:00Z", "final", 110, 117)      # home CLE 110, away OKC 117 -> OKC (away) won


def build(tmp_path, rows):
    return bth.build({"per_game_rows": rows}, tmp_path)


def test_displayed_percentage_is_kept_exactly_57_3_is_not_57_4(tmp_path):
    make(tmp_path, games=[GAME])
    out = build(tmp_path, [row(pct=57.3), row(key="total", sel="Under 229.5", pct=57.4, line_source="Bookmaker line at forecast")])
    assert {(r["market"], r["percentage"]) for r in out["rows"]} == {("winner", "57.3"), ("total", "57.4")}
    assert bth.build({"per_game_rows": [row(pct=54)]}, tmp_path)["rows"][0]["percentage"] == "54.0"


def test_a_game_the_live_record_also_counts_stays_in_the_recovered_record_flagged_and_counted(tmp_path):
    """Nov 22: the recovered record is shown apart from the live one and never summed with it, so a game both sources hold is
    counted once in EACH displayed record. It is no longer dropped; it is flagged and reported."""
    make(tmp_path, games=[GAME], forecasts=[("espn:1", "winner", "forecast", "2026-11-01T22:30:00Z", "2026-11-02T00:00:00Z")])
    out = build(tmp_path, [row(), row(key="total", sel="Under 229.5", pct=57.4, line_source="Bookmaker line at forecast")])
    assert {(r["market"], r["also_in_live"]) for r in out["rows"]} == {("winner", True), ("total", False)}
    assert out["excluded"] == []
    nba = out["summary"]["nba"]
    assert nba["included"] == 2 and nba["overlap_with_live"] == 1 and nba["excluded"] == 0
    assert "never summed" in out["overlap_rule"] and "counted once" in out["overlap_rule"]


def test_the_period_and_dates_come_from_the_rows_in_toronto_time_and_every_date_is_reported(tmp_path):
    games = [("espn:1", "2026-11-03T00:00:00Z", "final", 110, 117), ("espn:2", "2026-11-09T18:00:00Z", "final", 100, 90)]
    make(tmp_path, games=games)
    rows = [row(), {**row(gid="espn:2", sel="CLE win"), "sent_at_local": "2026-11-09T11:00:00"},
            {**row(gid="espn:9", key="total", sel="Over 200.5", line_source="Model line"), "sent_at_local": "2026-11-09T12:00:00"}]           # espn:9 is not in the store
    out = build(tmp_path, rows)
    nba = out["summary"]["nba"]
    assert nba["period"] == {"from": "2026-11-02", "to": "2026-11-09"}          # 00:00Z Nov 3 is 7 PM Nov 2 in Toronto
    assert nba["dates"] == ["2026-11-02", "2026-11-09"] and out["period"] == nba["period"]
    day = {d["date"]: d for d in out["by_sport_date"]["nba"]}
    assert (day["2026-11-02"]["source_rows"], day["2026-11-02"]["imported"]) == (1, 1)
    assert day["2026-11-09"]["source_rows"] == 2 and day["2026-11-09"]["unmatched"] == 1
    assert day["2026-11-09"]["excluded_by_reason"] == {"game_not_in_store": 1}


def test_earlier_cards_of_a_game_are_reported_as_superseded_never_counted(tmp_path):
    make(tmp_path, games=[GAME])
    last = {**row(), "message_id": 2}
    early = {**row(), "message_id": 1, "percent": 50}
    record = {"per_game_rows": [last], "all_card_rows": [early, last]}
    out = bth.build(record, tmp_path)
    assert len(out["rows"]) == 1 and out["rows"][0]["message_id"] == 2
    assert out["by_sport_date"]["nba"][0]["superseded_earlier_cards"] == 1


def test_a_missing_forecast_row_does_not_exclude_the_game(tmp_path):
    make(tmp_path, games=[GAME], forecasts=[("espn:1", "winner", "missing", "2026-11-01T22:30:00Z", "2026-11-02T00:00:00Z")])
    assert len(build(tmp_path, [row()])["rows"]) == 1


def test_an_ungraded_row_is_graded_from_the_store_final_and_says_so(tmp_path):
    make(tmp_path, games=[GAME])
    r = build(tmp_path, [row(result=None, sel="OKC win"), row(key="spread", sel="OKC +4.5", result=None, pct=60,
                                                              line_source="Bookmaker line at forecast")])["rows"]
    assert {(x["market"], x["grade"], x["grade_source"]) for x in r} == {("winner", "win", "store_final"), ("spread", "win", "store_final")}


def test_an_ungraded_row_with_no_final_is_excluded_not_guessed(tmp_path):
    make(tmp_path, games=[("espn:1", "2026-11-02T00:00:00Z", "scheduled", None, None)])
    out = build(tmp_path, [row(result=None)])
    assert out["rows"] == [] and out["excluded"][0]["reason"] == "no_final_result_in_store"


def test_a_spread_push_is_a_push_and_the_exported_grade_is_kept_when_the_store_disagrees(tmp_path):
    make(tmp_path, games=[("espn:1", "2026-11-02T00:00:00Z", "final", 110, 117)])
    r = build(tmp_path, [row(key="spread", sel="OKC -7", result=None, pct=60, line_source="Bookmaker line at forecast")])["rows"]
    assert r[0]["grade"] == "push"                                          # away team OKC won by 7, line -7 (home +7)
    out = build(tmp_path, [row(result="loss")])                             # exported 'loss' but the store says OKC won
    assert out["rows"][0]["grade"] == "loss" and out["rows"][0]["grade_source"] == "telegram_export"
    assert out["disagreements"] == [{"sport": "nba", "game_id": "espn:1", "market": "winner", "exported": "loss", "store": "win"}]


def test_duplicate_source_rows_are_an_error_not_a_silent_drop(tmp_path):
    make(tmp_path, games=[GAME])
    with pytest.raises(AssertionError, match="duplicate"):
        build(tmp_path, [row(), row()])


@pytest.mark.parametrize("selection,market,expected", [
    ("Tahiti win (90 min)", "1x2_regulation", "home"), ("Cook Islands win (90 min)", "1x2_regulation", "away"),
    ("Draw (90 min)", "1x2_regulation", "draw"), ("Tahiti or draw (90 min)", "double_chance", "home_draw"),
    ("Draw or Cook Islands (90 min)", "double_chance", "draw_away"), ("Cook Islands or Tahiti (90 min)", "double_chance", "home_away"),
    ("Tahiti (draw no bet, 90 min)", "draw_no_bet", "home"), ("Cook Islands (draw no bet, 90 min)", "draw_no_bet", "away")])
def test_soccer_sides_use_the_apps_soccer_keys(selection, market, expected):
    assert bth.soccer_side(market, selection, "Tahiti", "Cook Islands") == expected


def soccer_row(key, sel, pct, result="win", gid="soccer:fifa.friendly:1"):
    return dict(message_id=2, sport="SOCCER", game_id=gid, matchup="Tahiti vs Cook Islands", starts="x", final_score=None,
                market=key.upper(), market_key=key, selection=sel, percent=pct, line_source="", recommended=False, result=result)


def test_soccer_rows_are_imported_with_app_markets_and_a_dnb_draw_is_a_push(tmp_path):
    make(tmp_path, matches=[("soccer:fifa.friendly:1", "2026-11-14T05:30:00Z", "final", "regulation", 1, 1)])
    out = build(tmp_path, [soccer_row("match result", "Tahiti win (90 min)", 45.5, result=None),
                           soccer_row("double chance", "Tahiti or draw (90 min)", 72, result=None),
                           soccer_row("draw no bet", "Tahiti (draw no bet, 90 min)", 45.5, result=None)])
    got = {r["market"]: (r["side"], r["grade"], r["percentage"], r["line_source"]) for r in out["rows"]}
    assert got == {"1x2_regulation": ("home", "loss", "45.5", "none"), "double_chance": ("home_draw", "win", "72.0", "model"),
                   "draw_no_bet": ("home", "push", "45.5", "model")}
    assert out["summary"]["soccer"]["included"] == 3 and out["summary"]["soccer"]["period"] == {"from": "2026-11-14", "to": "2026-11-14"}


def test_a_soccer_match_with_a_live_forecast_keeps_every_market_flagged(tmp_path):
    make(tmp_path, matches=[("soccer:fifa.friendly:1", "2026-11-16T05:30:00Z", "final", "regulation", 2, 0),
                            ("soccer:fifa.friendly:2", "2026-11-14T05:30:00Z", "final", "regulation", 2, 0)],
         soccer_forecasts=[("soccer:fifa.friendly:1", "2026-11-15T19:00:00Z")])
    out = build(tmp_path, [soccer_row("match result", "Tahiti win (90 min)", 70), soccer_row("double chance", "Tahiti or draw (90 min)", 90),
                           soccer_row("match result", "Tahiti win (90 min)", 70, gid="soccer:fifa.friendly:2")])
    assert len(out["rows"]) == 3 and out["excluded"] == []
    assert {r["game_id"]: r["also_in_live"] for r in out["rows"]} == {"soccer:fifa.friendly:1": True, "soccer:fifa.friendly:2": False}
    assert out["summary"]["soccer"]["period"] == {"from": "2026-11-14", "to": "2026-11-16"}
    assert out["summary"]["soccer"]["overlap_with_live"] == 2
