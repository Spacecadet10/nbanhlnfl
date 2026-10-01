"""Possession inputs from the sportsdataverse team box release (synthetic CSV, no network).

Includes the Codex step 2 review reproductions: late data must not be backdated (P1), and an older file must not
overwrite newer values, including after replay (P2)."""
import hashlib
import sqlite3

import pytest

from app import coverage, db, ingest
from app.sources import sdv_team_box
from test_storage import T1, event, payload, run

HEADER = ("game_id,season,season_type,team_id,team_home_away,team_score,field_goals_attempted,"
          "free_throws_attempted,offensive_rebounds,defensive_rebounds,total_turnovers\n")
LATE_RESULT = "2024-03-01T00:00:00Z"   # result imported long after the 2024-01-15 game → assumed_tipoff_plus_4h


def csv_body(home_tov: int = 12, away_tov: int = 14, home_fga: int = 83) -> bytes:
    return (HEADER + f"1001,2024,2,20,home,124,{home_fga},20,10,33,{home_tov}\n"
                     f"1001,2024,2,10,away,115,83,25,9,30,{away_tov}\n").encode()


def make_store(tmp_path, monkeypatch, name="s", result_at=T1):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    c = db.connect(tmp_path / f"{name}.sqlite")
    db.migrate(c)
    run(c, payload(event(status="STATUS_FINAL"), event("1002", date="2024-01-20T18:00Z")), result_at)
    return c


def load(c, body, at, backfill=False):
    return ingest.ingest_team_box(c, 2024, fetch=lambda _s: body, retrieved_at=at, historical_backfill=backfill)


def observations(c):
    return [dict(r) for r in c.execute("SELECT * FROM team_possession_observations ORDER BY game_id, team_id, observed_at")]


def latest(c):
    return {r["team_id"]: (r["tov"], r["observed_at"]) for r in c.execute(ingest.POSSESSION_LATEST_SQL)}


def as_of(c, t):
    return {k[1]: (r["tov"], r["available_at"]) for k, r in ingest.possession_as_of(c, t).items()}


@pytest.fixture
def live_store(tmp_path, monkeypatch):
    """Game 1001's result was observed live (01-16 02:00Z, within 24 h of tipoff); game 1002 is still scheduled."""
    c = make_store(tmp_path, monkeypatch)
    yield c
    c.close()


def test_p1_late_stats_for_a_live_game_are_never_backdated(live_store):
    # Codex P1: stats first retrieved 01-18 12:00 were backdated to the 01-16 02:00 result availability.
    out = load(live_store, csv_body(), "2024-01-18T12:00:00Z", backfill=True)  # even when backfill is requested
    assert out["recorded"] == 2 and out["assumed_availability"] == 0
    assert all((o["available_at"], o["availability_basis"]) == ("2024-01-18T12:00:00Z", "observed_retrieval")
               for o in observations(live_store))
    assert as_of(live_store, "2024-01-17T00:00:00Z") == {}  # not known before its retrieval


def test_historical_assumption_needs_explicit_backfill_and_a_retrospective_result(tmp_path, monkeypatch):
    c = make_store(tmp_path, monkeypatch, result_at=LATE_RESULT)
    result_at = c.execute("SELECT result_available_at FROM games WHERE game_id='espn:1001'").fetchone()[0]
    assert result_at == "2024-01-15T22:00:00Z"  # assumed tipoff + 4 h
    load(c, csv_body(), "2026-09-30T12:00:00Z")  # default policy: observed at retrieval
    assert {o["availability_basis"] for o in observations(c)} == {"observed_retrieval"}
    out = load(c, csv_body(home_tov=13), "2026-10-01T12:00:00Z", backfill=True)
    assert out["assumed_availability"] == 2
    backfilled = [o for o in observations(c) if o["availability_basis"] == "historical_backfill_assumed"]
    assert len(backfilled) == 2 and all(o["available_at"] == result_at for o in backfilled)
    c.close()


def test_p2_older_file_never_overrides_newer_and_replay_is_order_independent(tmp_path, monkeypatch):
    # Codex P2 sequence: A (day 1: 12/14), B (day 3: 12/15), then the older C (day 2: 13/14) imported last.
    files = {"A": (csv_body(12, 14), "2024-01-17T12:00:00Z"), "B": (csv_body(12, 15), "2024-01-19T12:00:00Z"),
             "C": (csv_body(13, 14), "2024-01-18T12:00:00Z")}
    first = make_store(tmp_path / "one", monkeypatch)
    for k in "ABC":
        load(first, *files[k])
    assert latest(first) == {"espn:10": (15, "2024-01-19T12:00:00Z"), "espn:20": (12, "2024-01-19T12:00:00Z")}
    # As known on day 2.5, C's correction (home 13) was the latest; on day 1.5 only A existed.
    assert as_of(first, "2024-01-18T18:00:00Z") == {"espn:10": (14, "2024-01-18T12:00:00Z"),
                                                    "espn:20": (13, "2024-01-18T12:00:00Z")}
    assert as_of(first, "2024-01-17T18:00:00Z")["espn:20"][0] == 12
    before = observations(first)
    ingest.replay(first)  # idempotent: nothing appended, nothing changed
    assert observations(first) == before
    # A second store importing the same files in chronological order holds the same observations.
    second = make_store(tmp_path / "two", monkeypatch)
    for k in "ACB":
        load(second, *files[k])
    strip = lambda rows: [{k: v for k, v in r.items() if k not in ("raw_id", "fetch_id")} for r in rows]  # noqa: E731
    assert strip(observations(second)) == strip(before) and latest(second) == latest(first)
    first.close(), second.close()


def test_observations_are_append_only(live_store):
    load(live_store, csv_body(), "2024-01-18T12:00:00Z")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        live_store.execute("UPDATE team_possession_observations SET tov = 0")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        live_store.execute("DELETE FROM team_possession_observations")


def test_identical_redownload_is_a_new_observation_and_scoreboard_is_untouched(live_store):
    board = [dict(r) for r in live_store.execute("SELECT * FROM team_game_stats ORDER BY team_id")]
    load(live_store, csv_body(), "2024-01-18T12:00:00Z")
    again = load(live_store, csv_body(), "2024-01-20T12:00:00Z")  # same bytes → same raw file, new retrieval event
    assert again["recorded"] == 2 and again["changed_vs_previous"] == 0
    assert live_store.execute("SELECT COUNT(DISTINCT raw_id) FROM team_possession_observations").fetchone()[0] == 1
    assert latest(live_store)["espn:20"] == (12, "2024-01-20T12:00:00Z")
    assert ingest.rebuild_team_box(live_store)["recorded"] == 0  # replay is idempotent
    assert [dict(r) for r in live_store.execute("SELECT * FROM team_game_stats ORDER BY team_id")] == board


def test_rereview_p1_correction_back_to_an_earlier_value_is_observed(live_store):
    # Codex re-review P1: A (day 17: TOV 12), B (day 18: 13), then A's identical bytes again (day 19: 12).
    load(live_store, csv_body(home_tov=12), "2024-01-17T12:00:00Z")
    load(live_store, csv_body(home_tov=13), "2024-01-18T12:00:00Z")
    out = load(live_store, csv_body(home_tov=12), "2024-01-19T12:00:00Z")
    assert out["recorded"] == 2 and out["changed_vs_previous"] == 1
    assert as_of(live_store, "2024-01-18T18:00:00Z")["espn:20"][0] == 13
    assert as_of(live_store, "2024-01-19T18:00:00Z")["espn:20"][0] == 12
    assert latest(live_store)["espn:20"] == (12, "2024-01-19T12:00:00Z")
    before = observations(live_store)
    ingest.replay(live_store)
    assert observations(live_store) == before


def test_rereview_p2_insert_or_replace_is_rejected_from_a_default_connection(live_store, tmp_path):
    load(live_store, csv_body(home_tov=13), "2024-01-18T12:00:00Z")
    external = sqlite3.connect(tmp_path / "s.sqlite")  # plain connection: recursive_triggers off (SQLite default)
    try:
        assert external.execute("PRAGMA recursive_triggers").fetchone()[0] == 0
        row = external.execute("SELECT game_id, team_id, fetch_id, raw_id, is_home, observed_at, available_at, "
                               "availability_basis FROM team_possession_observations WHERE team_id='espn:20'").fetchone()
        with pytest.raises(sqlite3.IntegrityError, match="conflicting insert rejected"):
            external.execute("INSERT OR REPLACE INTO team_possession_observations (game_id, team_id, fetch_id, raw_id, "
                             "is_home, observed_at, available_at, availability_basis, tov) VALUES (?,?,?,?,?,?,?,?,99)", row)
        fetch = external.execute("SELECT fetch_id, raw_id, retrieved_at FROM raw_fetches LIMIT 1").fetchone()
        with pytest.raises(sqlite3.IntegrityError, match="conflicting insert rejected"):
            external.execute("INSERT OR REPLACE INTO raw_fetches (fetch_id, raw_id, retrieved_at) VALUES (?,?,?)", fetch)
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            external.execute("UPDATE raw_fetches SET retrieved_at = '2030-01-01T00:00:00Z'")
    finally:
        external.close()
    assert latest(live_store)["espn:20"][0] == 13


def test_skips_are_counted_and_differences_reported(live_store):
    body = (HEADER + "1001,2024,2,20,away,124,83,20,10,33,12\n"     # PHI is home in our store
                     "1001,2024,2,10,away,115,80,25,9,30,14\n"      # FGA differs from the scoreboard's 83
                     "1002,2024,2,5,home,0,,,,,\n"                  # not final
                     "9999,2024,2,1,home,100,80,20,10,30,10\n"      # unknown game
                     "1003,2024,2,130579,home,180,90,20,5,40,8\n").encode()  # All-Star team
    out = load(live_store, body, "2024-01-18T12:00:00Z")
    assert (out["skipped_side_mismatch"], out["recorded"], out["scoreboard_mismatch"]) == (1, 1, 1)
    assert (out["skipped_not_final"], out["skipped_unknown_game"], out["skipped_non_franchise"]) == (1, 1, 1)


def test_coverage_uses_latest_complete_observations(live_store):
    load(live_store, (HEADER + "1001,2024,2,20,home,124,83,20,10,33,12\n1001,2024,2,10,away,115,83,25,9,30,\n").encode(),
         "2024-01-18T12:00:00Z")
    season = next(s for s in coverage.report(live_store)["seasons"] if s["season"] == 2024)
    assert (season["final_competitive"], season["final_competitive_with_possession_stats"]) == (1, 0)
    load(live_store, csv_body(), "2024-01-19T12:00:00Z")
    season = next(s for s in coverage.report(live_store)["seasons"] if s["season"] == 2024)
    assert season["final_competitive_with_possession_stats"] == 1


def test_policy_is_recorded_in_the_request_key():
    assert sdv_team_box.policy_of(sdv_team_box.request_key(2025)) == "observed"
    assert sdv_team_box.policy_of(sdv_team_box.request_key(2025, "historical_backfill")) == "historical_backfill"
    assert sdv_team_box.policy_of("file=team_box_2025.csv") == "historical_backfill"  # the 2026-09-30 legacy imports
    with pytest.raises(ValueError):
        sdv_team_box.request_key(2025, "guess")


def test_download_must_match_published_digest():
    body = b"game_id\n1\n"
    sdv_team_box.verify(body, "sha256:" + hashlib.sha256(body).hexdigest())
    with pytest.raises(sdv_team_box.DigestMismatch):
        sdv_team_box.verify(body, "sha256:" + "0" * 64)
    with pytest.raises(sdv_team_box.DigestMismatch):
        sdv_team_box.verify(body, None)
