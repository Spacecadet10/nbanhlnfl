"""NCAAB data: ESPN scoreboard parsing, Division I classification, ingestion idempotency, result corrections, store
identity (real, trimmed ESPN responses under tests/fixtures/ncaab; no network)."""
import json

import pytest

from app import db, ingest, ncaab, sports
from app.db import StoreMismatch
from app.sources import espn, espn_ncaab
from ncaab_helpers import counts, dump, event, load, ncaab as ncaab_store, raw  # noqa: F401  (fixture re-export)

OT_GAME, REG_A, NON_DI = "401814522", "401827625", "401831990"
AT = "2026-01-18T12:00:00Z"            # a retrieval within a day of the January games
LATE = "2026-11-17T06:00:00Z"          # a historical backfill of the same games, ten months later


def games(name):
    return {g.game_id.split(":")[1]: g for g in espn_ncaab.parse_scoreboard(json.loads(raw(name)))}


# --- Parsing ---------------------------------------------------------------------------------------------------
def test_request_asks_for_division_i_group_and_all_games():
    key, params = espn_ncaab.day_request("20260117")
    assert params == {"dates": "20260117", "groups": "50", "limit": "400"}
    assert key == "dates=20260117&groups=50&limit=400"
    with pytest.raises(ValueError):
        espn_ncaab.day_request("2026-01-17")


def test_regular_season_game_ids_scores_and_conference_game():
    g = games("scoreboard_2026-01-17.json")[REG_A]
    assert g.game_id == "ncaab:401827625" and g.home.team_id.startswith("ncaab:") and g.away.team_id.startswith("ncaab:")
    assert (g.season, g.season_type, g.status) == (2026, "regular", "final")
    assert g.home_score is not None and g.away_score is not None
    assert g.conference_competition is True and g.both_d1 and g.ot_periods == 0
    assert g.home_conference_id and g.away_conference_id
    assert g.home_stats["points"] == g.home_score and g.home_stats["fga"] and g.home_stats["fta"]


def test_overtime_period_count_comes_from_linescores():
    g = games("scoreboard_2026-01-17.json")[OT_GAME]
    assert g.ot_periods == 1 and g.status == "final"
    assert g.home_score != g.away_score


def test_non_division_i_opponent_is_season_type_other_and_never_eligible():
    g = games("scoreboard_2026-01-17.json")[NON_DI]
    assert not g.both_d1 and g.season_type == "other"
    assert g.home_conference_id is None or g.away_conference_id is None


def test_tournament_game_is_neutral_postseason_with_note_and_not_a_conference_game():
    for g in games("scoreboard_2025-03-20.json").values():
        assert g.neutral_site is True and g.season_type == "postseason" and g.season == 2025
        assert g.tournament_note.startswith("Men's Basketball Championship") and g.conference_competition is False
        assert g.both_d1 and g.status == "final" and g.ot_periods in (0, 1, 2)


def test_postponed_and_canceled_games_have_no_result_and_are_not_final():
    by_status = {g.status: g for g in games("scoreboard_2021-01-09.json").values()}
    assert {"postponed", "canceled", "final"} <= set(by_status)
    for status in ("postponed", "canceled"):
        assert by_status[status].home_score is None and by_status[status].away_score is None
        assert by_status[status].ot_periods is None and not by_status[status].home_stats


def test_placeholder_tbd_teams_are_skipped_not_stored():
    """March 2020 lists canceled tournament games against 'TBD' teams with ids -1 / -2 (checked 2026-11-17)."""
    payload = json.loads(raw("scoreboard_2021-01-09.json"))
    away = next(c for c in payload["events"][0]["competitions"][0]["competitors"] if c["homeAway"] == "away")
    away["team"].update(id="-2", displayName="TBD TBD")
    parsed = espn_ncaab.parse_scoreboard(payload)
    assert len(parsed) == len(payload["events"]) - 1 and all(g.home.team_id[6:].isdigit() and g.away.team_id[6:].isdigit() for g in parsed)


def test_upcoming_games_carry_neutral_flag_tournament_note_and_no_scores():
    gs = games("scoreboard_2026-11-17.json")
    assert all(g.status == "scheduled" and g.home_score is None and g.season == 2027 for g in gs.values())
    assert any(g.neutral_site and g.tournament_note for g in gs.values())
    assert any(not g.both_d1 and g.season_type == "other" for g in gs.values())


def test_draftkings_open_and_current_lines_are_parsed_for_scheduled_games():
    quotes, skipped = espn.parse_lines(json.loads(raw("scoreboard_2026-11-17.json")))
    assert quotes and {q.provider for q in quotes} == {"DraftKings"}
    assert {q.market for q in quotes} >= {"winner", "spread", "total"} and {q.price_label for q in quotes} == {"open", "current"}


def test_team_list_parse():
    teams = espn_ncaab.parse_teams(json.loads(raw("teams_d1.json")))
    assert len(teams) == 5 and all(t.team_id.startswith("ncaab:") and t.name for t in teams)


def test_registry_serves_ncaab_with_its_own_store():
    assert sports.store("ncaab") is sports.NCAAB and sports.get("ncaab") is sports.NCAAB and "ncaab" not in sports.DATA_ONLY
    assert sports.NCAAB.eligible_season_types == ("regular", "postseason") and sports.NCAAB.id_prefix == "ncaab:"


# --- Ingestion -------------------------------------------------------------------------------------------------
def day(conn, name, at, **kw):
    return ncaab.ingest_day(conn, name.split("_")[1].split(".")[0].replace("-", ""), fetch=lambda _d: raw(name),
                            retrieved_at=at, **kw)


def test_ingest_day_stores_games_context_teams_stats_and_classification(ncaab_store):
    s = day(ncaab_store, "scoreboard_2026-01-17.json", AT)
    assert s["games_inserted"] == 4 and s["d1_games"] == 3 and s["non_d1_games"] == 1
    c = ncaab_store
    assert c.execute("SELECT season_type, status FROM games WHERE game_id=?", (f"ncaab:{NON_DI}",)).fetchone()[:] == ("other", "final")
    assert c.execute("SELECT ot_periods, both_d1, conference_competition FROM ncaab_game_context WHERE game_id=?",
                     (f"ncaab:{OT_GAME}",)).fetchone()[:] == (1, 1, 1)
    assert c.execute("SELECT both_d1 FROM ncaab_game_context WHERE game_id=?", (f"ncaab:{NON_DI}",)).fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM team_game_stats WHERE fga IS NOT NULL AND fta IS NOT NULL").fetchone()[0] == 8
    assert c.execute("SELECT sport FROM store_identity").fetchone()[0] == "ncaab"
    assert c.execute("SELECT result_availability_basis FROM games WHERE game_id=?", (f"ncaab:{REG_A}",)).fetchone()[0] == "observed_retrieval"
    # team-season membership: the Division I flag follows the conference id
    di = c.execute("SELECT is_d1, conference_id FROM ncaab_team_seasons WHERE season=2026 AND team_id IN "
                   "(SELECT home_team_id FROM games WHERE game_id=?)", (f"ncaab:{REG_A}",)).fetchone()
    assert di[0] == 1 and di[1]
    assert c.execute("SELECT COUNT(*) FROM ncaab_team_seasons WHERE is_d1 = 0").fetchone()[0] == 1


def test_historical_backfill_assumes_tipoff_plus_four_hours(ncaab_store):
    day(ncaab_store, "scoreboard_2026-01-17.json", LATE)
    row = ncaab_store.execute("SELECT tipoff_at, result_available_at, result_availability_basis FROM games WHERE game_id=?",
                              (f"ncaab:{REG_A}",)).fetchone()
    assert row["result_availability_basis"] == "assumed_tipoff_plus_4h"
    assert row["result_available_at"] > row["tipoff_at"] and row["result_available_at"][:10] <= "2026-01-18"


def test_postponed_and_canceled_are_stored_without_results(ncaab_store):
    s = day(ncaab_store, "scoreboard_2021-01-09.json", LATE)
    statuses = dict(ncaab_store.execute("SELECT status, COUNT(*) FROM games GROUP BY 1").fetchall())
    assert statuses == {"final": 1, "postponed": 1, "canceled": 1} and s["games_inserted"] == 3
    assert ncaab_store.execute("SELECT COUNT(*) FROM games WHERE status != 'final' AND (home_score IS NOT NULL OR away_score IS NOT NULL)"
                               ).fetchone()[0] == 0


def test_rerun_adds_no_games_changes_no_finals_and_records_the_fetch(ncaab_store):
    day(ncaab_store, "scoreboard_2026-01-17.json", AT)
    first = counts(ncaab_store)
    snapshot = ncaab_store.execute("SELECT * FROM games ORDER BY game_id").fetchall()
    second = day(ncaab_store, "scoreboard_2026-01-17.json", "2026-01-19T12:00:00Z")
    assert second["games_inserted"] == 0 and second["games_updated"] == 0 and second["games_seen"] == 4
    after = counts(ncaab_store)
    assert after["raw_fetches"] == first["raw_fetches"] + 1                      # the retrieval is logged ...
    assert {k: v for k, v in after.items() if k != "raw_fetches"} == {k: v for k, v in first.items() if k != "raw_fetches"}
    cols = [c[1] for c in ncaab_store.execute("PRAGMA table_info(games)") if c[1] not in ("last_seen_at", "last_seen_fetch_id")]
    assert [tuple(r[c] for c in cols) for r in ncaab_store.execute("SELECT * FROM games ORDER BY game_id")] == \
           [tuple(r[c] for c in cols) for r in snapshot]                           # ... and nothing else changed


def test_changed_response_for_a_final_is_a_correction_with_a_new_revision(ncaab_store):
    day(ncaab_store, "scoreboard_2026-01-17.json", AT)
    payload = json.loads(raw("scoreboard_2026-01-17.json"))
    comp = event(payload, REG_A)["competitions"][0]
    home = next(c for c in comp["competitors"] if c["homeAway"] == "home")
    original = int(home["score"])
    home["score"] = str(original + 1)
    s = ncaab.ingest_day(ncaab_store, "20260117", fetch=lambda _d: dump(payload), retrieved_at="2026-01-20T12:00:00Z")
    assert s["games_updated"] == 1 and s["games_inserted"] == 0
    revs = ncaab_store.execute("SELECT revision, home_score, basis FROM result_revisions WHERE game_id=? ORDER BY revision",
                               (f"ncaab:{REG_A}",)).fetchall()
    assert [(r[0], r[1]) for r in revs] == [(1, original), (2, original + 1)] and revs[1][2] == "observed"
    g = ncaab_store.execute("SELECT home_score, result_available_at FROM games WHERE game_id=?", (f"ncaab:{REG_A}",)).fetchone()
    assert g["home_score"] == original + 1 and g["result_available_at"] == "2026-01-20T12:00:00Z"
    assert ncaab_store.execute("SELECT COUNT(*) FROM raw_responses").fetchone()[0] == 2     # both responses kept


def test_an_older_retrieval_never_overwrites_a_newer_one(ncaab_store):
    payload = json.loads(raw("scoreboard_2026-01-17.json"))
    event(payload, REG_A)["competitions"][0]["competitors"][0]["score"] = "1"
    ncaab.ingest_day(ncaab_store, "20260117", fetch=lambda _d: raw("scoreboard_2026-01-17.json"), retrieved_at="2026-01-20T12:00:00Z")
    s = ncaab.ingest_day(ncaab_store, "20260117", fetch=lambda _d: dump(payload), retrieved_at=AT)
    assert s["games_updated"] == 0 and s["games_inserted"] == 0


def test_scheduled_day_appends_dk_quotes_per_retrieval_and_context(ncaab_store):
    first = day(ncaab_store, "scoreboard_2026-11-17.json", "2026-11-17T12:00:00Z")
    assert first["lines"]["inserted"] > 0 and first["lines"]["unmatched"] == 0
    n = ncaab_store.execute("SELECT COUNT(*) FROM market_quotes").fetchone()[0]
    assert ncaab_store.execute("SELECT DISTINCT sport, provider FROM market_quotes").fetchall()[0][:] == ("ncaab", "DraftKings")
    day(ncaab_store, "scoreboard_2026-11-17.json", "2026-11-17T13:00:00Z")
    assert ncaab_store.execute("SELECT COUNT(*) FROM market_quotes").fetchone()[0] == 2 * n    # append-only, one set per retrieval
    assert ncaab_store.execute("SELECT COUNT(*) FROM games WHERE status='scheduled'").fetchone()[0] == 4
    note = ncaab_store.execute("SELECT tournament_note FROM ncaab_game_context WHERE tournament_note IS NOT NULL").fetchone()
    assert note and note[0]


def test_context_and_team_season_uniqueness_is_enforced_by_the_database(ncaab_store):
    day(ncaab_store, "scoreboard_2026-01-17.json", AT)
    with pytest.raises(Exception):
        ncaab_store.execute("INSERT INTO ncaab_team_seasons (season, team_id, conference_id, is_d1, first_seen_at, last_seen_at) "
                            "SELECT season, team_id, conference_id, is_d1, first_seen_at, last_seen_at FROM ncaab_team_seasons LIMIT 1")
    with pytest.raises(Exception):
        ncaab_store.execute("INSERT INTO ncaab_game_context SELECT * FROM ncaab_game_context LIMIT 1")


# --- Identity --------------------------------------------------------------------------------------------------
def test_an_ncaab_ingest_is_refused_by_other_sports_stores(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    nba = db.connect(tmp_path / "nba.sqlite")
    db.migrate(nba)
    db.claim_store(nba, "nba")
    with pytest.raises(StoreMismatch):
        ncaab.ingest_day(nba, "20260117", fetch=lambda _d: raw("scoreboard_2026-01-17.json"), retrieved_at=AT)
    assert nba.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 0


def test_nba_ingest_is_refused_by_an_ncaab_store(ncaab_store):
    day(ncaab_store, "scoreboard_2026-01-17.json", AT)
    with pytest.raises(StoreMismatch):
        ingest.ingest_day(ncaab_store, "20260117", fetch=lambda _d: raw("scoreboard_2026-01-17.json"), retrieved_at=AT)
    with pytest.raises(StoreMismatch):
        db.claim_store(ncaab_store, "nba")


def test_replay_of_the_stores_own_log_adds_nothing(ncaab_store):
    day(ncaab_store, "scoreboard_2026-11-17.json", "2026-11-17T12:00:00Z")
    day(ncaab_store, "scoreboard_2026-01-17.json", AT)
    before = counts(ncaab_store)
    out = ncaab.replay(ncaab_store)
    assert out["fetches"] == 2 and out["games_inserted"] == 0 and out["games_updated"] == 0
    assert counts(ncaab_store) == before


def test_only_the_registered_ncaab_store_name_holds_the_ncaab_schema(tmp_path):
    other = db.connect(tmp_path / "scratch.sqlite")
    db.migrate(other)
    assert not db.has_table(other, "ncaab_game_context")           # not an ncaab store: 0042 is never applied
