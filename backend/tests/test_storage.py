"""Storage, ingestion idempotency, raw-cache replay, and append-only ledger tests (synthetic payloads, no network)."""
import gzip
import json
import sqlite3

import pytest

from app import coverage, db, ingest, ledger, odds, raw_cache
from app.sources import espn

T0 = "2024-01-15T12:00:00Z"   # retrieval before tipoff
T1 = "2024-01-16T02:00:00Z"   # retrieval after the game
LATER = "2024-03-01T00:00:00Z"


def event(eid="1001", date="2024-01-15T18:00Z", status="STATUS_SCHEDULED", home=("20", "PHI", "124"),
          away=("10", "HOU", "115"), season_type=2, odds_ml=None, stats=None):
    def comp(side, t):
        c = {"homeAway": side, "team": {"id": t[0], "abbreviation": t[1], "displayName": f"Team {t[1]}"},
             "score": t[2] if status == "STATUS_FINAL" else "0"}
        if status == "STATUS_FINAL":
            values = {"fieldGoalsAttempted": "83", "fieldGoalsMade": "40", "rebounds": "41", "avgRebounds": "41.0"}
            values.update(stats or {})
            c["statistics"] = [{"name": k, "displayValue": v} for k, v in values.items() if v is not None]
        return c
    competition = {"neutralSite": False, "status": {"type": {"name": status}},
                   "competitors": [comp("home", home), comp("away", away)]}
    if odds_ml:
        competition["odds"] = [{"provider": {"name": "Book"}, "moneyline": {
            "home": {"open": {"odds": odds_ml[0]}, "close": {"odds": odds_ml[0]}},
            "away": {"open": {"odds": odds_ml[1]}, "close": {"odds": odds_ml[1]}}}}]
    return {"id": eid, "date": date, "season": {"year": 2024, "type": season_type}, "competitions": [competition]}


def payload(*events) -> bytes:
    return json.dumps({"events": list(events)}, sort_keys=True).encode()


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    c = db.connect(tmp_path / "test.sqlite")
    db.migrate(c)
    yield c
    c.close()


def counts(c):
    return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("games", "teams", "team_game_stats", "odds_quotes", "raw_responses", "predictions")}


def run(c, body, at):
    return ingest.ingest_month(c, "202401", fetch=lambda _m: body, retrieved_at=at)


def test_migrations_are_idempotent(conn):
    assert db.migrate(conn) == []
    assert [r["name"] for r in conn.execute("SELECT name FROM schema_migrations")] == \
        ["0001_initial.sql", "0002_block_conflicting_inserts.sql", "0003_job_runs.sql",
         "0004_odds_job.sql", "0005_team_possession_stats.sql", "0006_possession_observations.sql",
         "0007_possession_fetch_events.sql", "0008_shadow_jobs.sql",
         "0009_box_fetch_attempts.sql", "0010_injury_report_attempts.sql", "0011_near_tipoff.sql",
         "0012_markets.sql", "0013_market_forecast_checks.sql",
         "0014_goalie_snapshots.sql", "0015_two_hour_forecasts.sql",
         "0016_research_selection.sql", "0017_nhl_shots.sql", "0018_model_fits.sql",
         "0019_candidate_forecasts.sql", "0020_pool_selection.sql"]


def test_repeated_ingestion_creates_no_duplicates(conn):
    body = payload(event(status="STATUS_FINAL"), event("1002", home=("5", "CLE", "99"), away=("2", "BOS", "101")))
    first = run(conn, body, T1)
    after_first = counts(conn)
    second = run(conn, body, LATER)
    assert first["games_inserted"] == 2
    assert second["games_inserted"] == 0 and second["games_updated"] == 0
    assert counts(conn) == after_first
    assert after_first["raw_responses"] == 1
    assert conn.execute("SELECT COUNT(*) FROM raw_fetches").fetchone()[0] == 2


def test_status_change_updates_game_without_duplicating(conn):
    run(conn, payload(event(odds_ml=("-125", "+105"))), T0)
    run(conn, payload(event(status="STATUS_FINAL")), T1)
    rows = conn.execute("SELECT * FROM games").fetchall()
    assert len(rows) == 1
    g = rows[0]
    assert (g["status"], g["home_score"], g["away_score"]) == ("final", 124, 115)
    assert g["tipoff_at"] == "2024-01-15T18:00:00Z"
    assert g["first_seen_at"] == T0 and g["result_first_seen_at"] == T1
    assert g["result_available_at"] == T1 and g["result_availability_basis"] == "observed_retrieval"
    assert counts(conn)["raw_responses"] == 2 and counts(conn)["team_game_stats"] == 2


def test_historical_result_uses_documented_availability_assumption(conn):
    run(conn, payload(event(status="STATUS_FINAL")), LATER)
    g = conn.execute("SELECT * FROM games").fetchone()
    assert g["result_availability_basis"] == "assumed_tipoff_plus_4h"
    assert g["result_available_at"] == "2024-01-15T22:00:00Z"
    assert g["result_first_seen_at"] == LATER


def test_final_result_never_regresses_and_older_data_never_overwrites(conn):
    run(conn, payload(event(status="STATUS_FINAL")), T1)
    run(conn, payload(event(status="STATUS_SCHEDULED")), LATER)
    assert conn.execute("SELECT status FROM games").fetchone()[0] == "final"


def test_replay_from_cache_rebuilds_identical_rows(conn, tmp_path):
    run(conn, payload(event(odds_ml=("-125", "+105"))), T0)
    run(conn, payload(event(status="STATUS_FINAL")), T1)
    snapshot = [dict(r) for r in conn.execute("SELECT * FROM games")]
    before = counts(conn)
    totals = ingest.replay(conn)
    assert totals["responses"] == 2 and totals["games_inserted"] == 0 and totals["games_updated"] == 0
    assert counts(conn) == before
    assert [dict(r) for r in conn.execute("SELECT * FROM games")] == snapshot

    fresh = db.connect(tmp_path / "fresh.sqlite")
    db.migrate(fresh)
    for r in conn.execute("SELECT * FROM raw_responses ORDER BY raw_id"):
        fresh.execute("INSERT INTO raw_responses VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", tuple(r))
    ingest.replay(fresh)
    assert [dict(r) for r in fresh.execute("SELECT * FROM games")] == snapshot


def test_tampered_cache_is_detected(conn, tmp_path):
    run(conn, payload(event()), T0)
    path = tmp_path / "raw" / conn.execute("SELECT path FROM raw_responses").fetchone()[0]
    path.write_bytes(gzip.compress(b'{"events": []}'))
    with pytest.raises(raw_cache.CacheIntegrityError):
        ingest.replay(conn)


def test_raw_responses_are_immutable(conn):
    run(conn, payload(event()), T0)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE raw_responses SET content_hash='x'")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("DELETE FROM raw_responses")


def test_database_constraints_reject_duplicates_and_bad_timestamps(conn):
    run(conn, payload(event()), T0)
    g = dict(conn.execute("SELECT * FROM games").fetchone())
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(f"INSERT INTO games ({', '.join(g)}) VALUES ({', '.join('?' * len(g))})", list(g.values()))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE games SET tipoff_at='2024-01-15 18:00' WHERE game_id=?", (g["game_id"],))
    with pytest.raises(sqlite3.IntegrityError):  # final requires scores and availability
        conn.execute("UPDATE games SET status='final' WHERE game_id=?", (g["game_id"],))


def test_odds_are_normalized_and_deduplicated(conn):
    body = payload(event(odds_ml=("-125", "+105")))
    run(conn, body, T0)
    run(conn, body, LATER)
    rows = conn.execute("SELECT * FROM odds_quotes ORDER BY price_label").fetchall()
    assert [r["price_label"] for r in rows] == ["current", "open"]
    assert all(r["quote_time"] is None and r["retrieved_at"] == T0 for r in rows)
    assert rows[0]["home_prob_devig"] == pytest.approx(0.5325, abs=1e-4)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM odds_quotes")


def test_devig_and_price_parsing():
    h, a = odds.devig_two_way(-125, 105)
    assert h + a == pytest.approx(1)
    assert h == pytest.approx((125 / 225) / (125 / 225 + 100 / 205))
    assert odds.devig_two_way(-110, -110) == pytest.approx((0.5, 0.5))
    assert odds.parse_american("EVEN") == 100
    assert odds.parse_american("OFF") is None and odds.parse_american("50") is None
    with pytest.raises(ValueError):
        odds.american_to_implied(50)


def test_all_star_style_games_are_not_regular_season():
    games = espn.parse_scoreboard(json.loads(payload(
        event(home=("111353", "LEB", "170"), away=("112151", "GIA", "150")),
        event("2", season_type=1, home=("20", "PHI", "1"), away=("66329", "REAL", "1")))))
    assert [g.season_type for g in games] == ["other", "preseason"]


def test_parser_skips_stats_for_unfinished_and_maps_types():
    games = espn.parse_scoreboard(json.loads(payload(
        event(status="STATUS_POSTPONED", season_type=3), event("2", status="STATUS_FINAL", season_type=5))))
    assert (games[0].status, games[0].season_type, games[0].home_score, games[0].home_stats) == \
        ("postponed", "postseason", None, {})
    assert games[1].season_type == "play_in" and games[1].home_stats["fga"] == 83


def _forecast(**kw):
    base = dict(game_id="espn:1001", run_type="live", model_version="m1", dataset_version="d1",
                feature_version="f1", cutoff_at="2024-01-15T17:00:00Z", generated_at="2024-01-15T16:59:00Z",
                home_win_prob=0.6)
    return ledger.Forecast(**{**base, **kw})


def test_ledger_retry_is_idempotent_and_conflicts_are_rejected(conn):
    run(conn, payload(event()), T0)
    pid, created = ledger.record(conn, _forecast())
    assert created
    assert ledger.record(conn, _forecast(generated_at="2024-01-15T16:59:30Z")) == (pid, False)  # retry
    with pytest.raises(ledger.LedgerConflict):
        ledger.record(conn, _forecast(home_win_prob=0.7))
    new_pid, created = ledger.record(conn, _forecast(model_version="m2", home_win_prob=0.7))  # new version
    assert created and new_pid != pid
    assert counts(conn)["predictions"] == 2


def test_stored_predictions_cannot_be_modified_or_deleted(conn):
    run(conn, payload(event()), T0)
    pid, _ = ledger.record(conn, _forecast())
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE predictions SET home_win_prob=0.99 WHERE prediction_id=?", (pid,))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM predictions WHERE prediction_id=?", (pid,))
    assert conn.execute("SELECT home_win_prob FROM predictions").fetchone()[0] == 0.6


def test_live_predictions_must_precede_cutoff_and_tipoff(conn):
    run(conn, payload(event()), T0)
    with pytest.raises(sqlite3.IntegrityError, match="before cutoff"):
        ledger.record(conn, _forecast(generated_at="2024-01-15T17:30:00Z"))
    with pytest.raises(sqlite3.IntegrityError, match="before cutoff"):
        ledger.record(conn, _forecast(cutoff_at="2024-01-15T19:00:00Z", generated_at="2024-01-15T18:30:00Z"))
    _, created = ledger.record(conn, _forecast(run_type="backtest", cutoff_at="2024-01-15T17:00:00Z",
                                               generated_at="2026-09-30T00:00:00Z"))
    assert created  # retrospective runs are allowed but labelled backtest
    with pytest.raises(sqlite3.IntegrityError):
        ledger.record(conn, _forecast(run_type="real"))


def test_coverage_report(conn):
    run(conn, payload(event(status="STATUS_FINAL"), event("1002", odds_ml=("-125", "+105"))), T1)
    rep = coverage.report(conn)
    s = rep["seasons"][0]
    assert (s["season"], s["regular"], s["final"], s["scheduled"], s["final_with_box_stats"], s["games_with_odds"]) \
        == (2024, 2, 1, 1, 1, 1)
    assert rep["totals"]["teams"] == 2


# --- Regression tests for docs/MILESTONE2_REVIEW.md ---------------------------------------------------------

def stat_row(c, team="espn:20"):
    return dict(c.execute("SELECT * FROM team_game_stats WHERE team_id=?", (team,)).fetchone())


def test_older_import_does_not_overwrite_newer_team_stats(conn):
    run(conn, payload(event(status="STATUS_FINAL", home=("20", "PHI", "130"))), "2024-01-17T02:00:00Z")
    run(conn, payload(event(status="STATUS_FINAL", home=("20", "PHI", "124"))), "2024-01-16T02:00:00Z")
    assert conn.execute("SELECT home_score FROM games").fetchone()[0] == 130
    assert stat_row(conn)["points"] == 130


def test_stats_only_change_is_applied_when_game_fields_are_unchanged(conn):
    run(conn, payload(event(status="STATUS_FINAL")), T1)
    updated = run(conn, payload(event(status="STATUS_FINAL", stats={"rebounds": "43"})), LATER)
    assert updated["games_updated"] == 0
    row = stat_row(conn)
    assert row["rebounds"] == 43
    assert conn.execute("SELECT first_retrieved_at FROM raw_responses WHERE raw_id=?",
                        (row["last_raw_id"],)).fetchone()[0] == LATER


def test_partial_newer_response_keeps_existing_fields(conn):
    run(conn, payload(event(status="STATUS_FINAL")), T1)
    run(conn, payload(event(status="STATUS_FINAL", stats={"fieldGoalsAttempted": None, "rebounds": "44"})), LATER)
    row = stat_row(conn)
    assert (row["fga"], row["fgm"], row["rebounds"]) == (83, 40, 44)


def test_out_of_order_replay_matches_in_order_state(conn):
    newer = payload(event(status="STATUS_FINAL", home=("20", "PHI", "130"), stats={"rebounds": "50"}))
    older = payload(event(status="STATUS_FINAL", home=("20", "PHI", "124")))
    run(conn, newer, "2024-01-17T02:00:00Z")
    run(conn, older, "2024-01-16T02:00:00Z")
    before = [dict(r) for r in conn.execute("SELECT * FROM team_game_stats ORDER BY team_id")]
    assert ingest.replay(conn)["games_updated"] == 0
    after = [dict(r) for r in conn.execute("SELECT * FROM team_game_stats ORDER BY team_id")]
    assert after == before and stat_row(conn)["points"] == 130 and stat_row(conn)["rebounds"] == 50


def _replace(c, table, row, **overrides):
    row = {**row, **overrides}
    c.execute(f"INSERT OR REPLACE INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
              list(row.values()))


def test_insert_or_replace_cannot_overwrite_append_only_rows(conn):
    run(conn, payload(event(odds_ml=("-125", "+105"))), T0)
    ledger.record(conn, _forecast())
    for table, key, changes in [
        ("predictions", "prediction_id", {"home_win_prob": 0.99}),
        ("raw_responses", "raw_id", {"byte_size": 1}),
        ("odds_quotes", "quote_id", {"home_prob_devig": 0.99}),
    ]:
        original = dict(conn.execute(f"SELECT * FROM {table} ORDER BY {key} LIMIT 1").fetchone())
        with pytest.raises(sqlite3.IntegrityError, match="conflicting insert"):  # same primary key
            _replace(conn, table, original, **changes)
        with pytest.raises(sqlite3.IntegrityError, match="conflicting insert"):  # unique key, new primary key
            _replace(conn, table, original, **{key: 999, **changes})
        with pytest.raises(sqlite3.IntegrityError, match="conflicting insert"):
            conn.execute(f"INSERT OR IGNORE INTO {table} ({', '.join(original)}) "
                         f"VALUES ({', '.join('?' * len(original))})", list(original.values()))
        assert dict(conn.execute(f"SELECT * FROM {table} WHERE {key}=?", (original[key],)).fetchone()) == original


def test_replace_blocked_even_without_recursive_triggers(conn):
    run(conn, payload(event()), T0)
    ledger.record(conn, _forecast())
    conn.execute("PRAGMA recursive_triggers = OFF")  # e.g. the sqlite3 CLI or another client
    original = dict(conn.execute("SELECT * FROM predictions").fetchone())
    with pytest.raises(sqlite3.IntegrityError, match="conflicting insert"):
        _replace(conn, "predictions", original, home_win_prob=0.99)
    assert conn.execute("SELECT home_win_prob FROM predictions").fetchone()[0] == 0.6


def test_migration_0002_preserves_existing_rows_and_retries_stay_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    c = db.connect(tmp_path / "old.sqlite")
    c.execute("PRAGMA recursive_triggers = OFF")
    assert db.migrate(c, upto=1) == ["0001_initial.sql"]
    run(c, payload(event(odds_ml=("-125", "+105"))), T0)
    pid, _ = ledger.record(c, _forecast())
    before = {t: [dict(r) for r in c.execute(f"SELECT * FROM {t}")] for t in ("predictions", "raw_responses", "odds_quotes")}
    assert db.migrate(c, upto=2) == ["0002_block_conflicting_inserts.sql"]
    assert {t: [dict(r) for r in c.execute(f"SELECT * FROM {t}")] for t in before} == before
    assert ledger.record(c, _forecast()) == (pid, False)
    assert run(c, payload(event(odds_ml=("-125", "+105"))), LATER)["odds_inserted"] == 0
    with pytest.raises(sqlite3.IntegrityError, match="conflicting insert"):
        _replace(c, "predictions", before["predictions"][0], home_win_prob=0.99)
    c.close()
