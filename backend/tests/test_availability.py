"""NBA availability research: parsing both layouts, timestamp rules, matching, minutes history and features (no network)."""
from datetime import datetime, timezone

import pytest

from app import availability as av
from app.models.base import PregameGame

TEAMS = ["Boston Celtics", "New Orleans Pelicans", "Los Angeles Lakers"]
H2019 = ("Game Date          Game Time          Matchup           Team                             Player Name"
         "                             Category                                 Reason                                  "
         "Current Status                           Previous Status")
H2024 = ("Game Date      Game Time     Matchup      Team                       Player Name                 Current Status"
         "     Reason")


def row(cols: dict, header: str) -> str:
    line = [" "] * (len(header) + 20)
    for label, text in cols.items():
        i = max(0, header.index(label) - 1)  # data starts a character left of the label, as in real files
        line[i:i + len(text)] = list(text)
    return "".join(line).rstrip()


def test_2019_layout_with_category_and_previous_status():
    lines = [H2019,
             row({"Game Date": "10/22/2019", "Matchup": "NOP@TOR", "Team": "New Orleans Pelicans",
                  "Player Name": "Cheatham, Zylan", "Category": "G League", "Reason": "Two-Way",
                  "Current Status": "Out", "Previous Status": "Questionable"}, H2019),
             row({"Player Name": "Gray, Josh", "Category": "Injury/Illness", "Current Status": "Doubtful"}, H2019),
             row({"Reason": "Hand"}, H2019),                                          # a wrapped reason line
             row({"Team": "Los Angeles Lakers", "Player Name": "Antetokounmpo, Kostas", "Current Status": "Available"}, H2019),
             row({"Team": "Boston Celtics", "Reason": "NOT YET SUBMITTED"}, H2019)]
    r = av.parse_layout([("Injury Report: 10/22/19 05:30 PM", lines)], "x", TEAMS)
    assert r.reconciled and r.loose_lines == 3
    assert [(e["team"], e["last"], e["first"], e["status"]) for e in r.entries] == [
        ("New Orleans Pelicans", "Cheatham", "Zylan", "Out"), ("New Orleans Pelicans", "Gray", "Josh", "Doubtful"),
        ("Los Angeles Lakers", "Antetokounmpo", "Kostas", "Available")]
    assert r.not_submitted == [{"game_date": "2019-10-22", "team": "Boston Celtics"}]
    assert r.header_published == datetime(2019, 10, 22, 21, 30, tzinfo=timezone.utc)   # 5:30 PM EDT


def test_later_layout_and_an_unparseable_name_fails_reconciliation():
    lines = [H2024,
             row({"Game Date": "01/10/2024", "Team": "Boston Celtics", "Player Name": "Holiday, Jrue",
                  "Current Status": "Questionable", "Reason": "Injury/Illness - Ankle"}, H2024),
             row({"Team": "Doe, John", "Current Status": "Out"}, H2024)]            # name outside its column
    r = av.parse_layout([("Injury Report: 01/10/24 05:30 PM", lines)], "x", TEAMS)
    assert [(e["last"], e["status"]) for e in r.entries] == [("Holiday", "Questionable")]
    assert r.loose_lines == 2 and not r.reconciled                         # excluded, never half-used
    assert r.header_published == datetime(2024, 1, 10, 22, 30, tzinfo=timezone.utc)   # 5:30 PM EST


def test_publication_time_rules():
    r = av.ParsedReport("x", datetime(2024, 1, 10, 22, 30, tzinfo=timezone.utc))
    assert av.publication_time(r, "Wed, 10 Jan 2024 22:30:20 GMT") == (r.header_published, "header")
    later, basis = av.publication_time(r, "Wed, 10 Jan 2024 22:40:00 GMT")
    assert basis == "flagged_last_modified_differs" and later == datetime(2024, 1, 10, 22, 40, tzinfo=timezone.utc)
    assert av.publication_time(r, None) == (None, "no_last_modified")
    assert av.publication_time(av.ParsedReport("x", None), "Wed, 10 Jan 2024 22:30:20 GMT") == (None, "no_header_time")


def _app(day, season, team, minutes):
    return av.Appearance(day, season, team, minutes, minutes > 0)


def test_matching_levels_and_unresolved():
    box = {2023: ({"1": [_app("2023-01-01", 2023, "2", 30)], "2": [_app("2023-01-01", 2023, "3", 20)],
                   "3": [_app("2023-01-01", 2023, "3", 10)]},
                  {"1": "Jrue Holiday", "2": "Kevin Knox II", "3": "Kevin Knox"})}
    idx = av.PlayerIndex(box, {av.norm_name("Nicolas Claxton"): "99"})
    assert idx.match("Holiday", "Jrue", "2", 2023) == ("1", "team_season")
    assert idx.match("Holiday", "Jrue", "9", 2023) == ("1", "league_unique")
    assert idx.match("Knox II", "Kevin", "3", 2023) == (None, "ambiguous")   # suffix removed: two candidates
    assert idx.match("Claxton", "Nicolas", "17", 2023) == ("99", "alias")
    assert idx.match("Nobody", "Some", "2", 2023) == (None, "unresolved")


def test_expected_minutes_and_new_absence_use_only_earlier_games():
    box = {2023: ({"1": [_app("2022-12-01", 2023, "2", 30), _app("2022-12-03", 2023, "2", 34), _app("2022-12-05", 2023, "2", 0),
                         _app("2022-12-07", 2023, "2", 38), _app("2022-12-09", 2023, "2", 50)],
                   "2": [_app("2022-12-01", 2023, "2", 10)]}, {}),
           2022: ({"2": [_app("2022-03-01", 2022, "2", 20), _app("2022-03-03", 2022, "2", 22)]}, {})}
    h = av.MinutesHistory(box)
    assert h.expected("1", "2022-12-09") == (34.0, "this_season")           # 30, 34, 38: the 12-09 game is not used
    assert h.expected("2", "2022-12-09") == (21.0, "previous_season")       # only 1 game this season
    assert h.expected("9", "2022-12-09") == (0.0, "no_history")
    assert h.played_last_team_game("1", "2", "2022-12-09") is True          # played 12-07
    assert h.played_last_team_game("1", "2", "2022-12-06") is False         # DNP on 12-05


def test_features_mark_missing_reports_and_unsubmitted_teams():
    g = PregameGame("espn:1", 2023, "regular", "2023-01-10T00:30:00Z", "espn:2", "espn:3", False)   # 7:30 PM ET 01-09
    ok = av.ReportFacts(True, "ok", "2023-01-09T22:30:00Z", absent={"2": {"new": 30.0, "all": 60.0}, "3": {"new": 0.0, "all": 12.0}})
    f = av.game_features({"2023-01-09": ok}, g)
    assert f == {"new_absent_diff": -30 / 48, "all_absent_diff": -48 / 48, "report_missing": 0.0}
    ok.unsubmitted.add("3")
    assert av.game_features({"2023-01-09": ok}, g)["report_missing"] == 1.0
    assert av.game_features({}, g) == {"new_absent_diff": 0.0, "all_absent_diff": 0.0, "report_missing": 1.0}


def test_evening_cutoff_and_season_guard():
    assert av.evening_cutoff("2023-01-10T00:30:00Z") == "2023-01-09T22:45:00Z"
    assert av.evening_cutoff("2023-01-09T20:00:00Z") is None                # 3 PM ET: excluded
    with pytest.raises(av.AvailabilityError):
        av.load_player_box(2025)                                            # spent test season
    assert av.report_name("2023-01-09") == "Injury-Report_2023-01-09_05PM.pdf"


def test_continuation_pages_without_a_header_keep_the_columns():
    first = [H2024, row({"Game Date": "01/10/2024", "Team": "Boston Celtics", "Player Name": "Holiday, Jrue",
                         "Current Status": "Out"}, H2024)]
    second = [row({"Player Name": "Horford, Al", "Current Status": "Questionable"}, H2024)]
    r = av.parse_layout([("Injury Report: 01/10/24 05:30 PM", first), ("", second)], "x", TEAMS)
    assert r.reconciled and [(e["team"], e["last"]) for e in r.entries] == [("Boston Celtics", "Holiday"),
                                                                            ("Boston Celtics", "Horford")]


def test_a_page_the_columns_cannot_read_fails_instead_of_passing_with_zero_entries():
    lines = ["Player   Name text without a status label", "Holiday, Jrue          Out"]
    r = av.parse_layout([("Injury Report: 01/10/24 05:30 PM", lines)], "x", TEAMS)
    assert r.entries == [] and r.loose_lines == 1 and not r.reconciled
