"""Migration 0042 (NCAAB): applied to the NCAAB store only; NBA, NHL, NFL and Soccer stores migrate as before, with their
existing rows and counts unchanged."""
import pytest

from app import config, db
from ncaab_helpers import FIXTURES  # noqa: F401

NCAAB_TABLES = ("ncaab_team_seasons", "ncaab_game_context", "ncaab_d1_listings", "ncaab_after_game_lines")
TIPOFF = "2024-01-15T18:00:00Z"


def seed(conn):
    """One team pair, one final game and one quote, written at the schema before 0033 (every sport has these tables)."""
    for tid in ("1", "2"):
        conn.execute("INSERT INTO teams (team_id, abbr, name, first_seen_at) VALUES (?, 'T', 'Team', ?)", (f"x:{tid}", TIPOFF))
    conn.execute("INSERT INTO raw_responses (source, endpoint, request_key, content_hash, byte_size, path, importer_version,"
                 " first_retrieved_at) VALUES ('s', 'e', 'k', 'h', 1, 'p', 'v', ?)", (TIPOFF,))
    conn.execute("INSERT INTO games (game_id, season, season_type, tipoff_at, home_team_id, away_team_id, neutral_site, status,"
                 " home_score, away_score, first_seen_at, result_first_seen_at, result_available_at, result_availability_basis,"
                 " last_raw_id, updated_at) VALUES ('x:g', 2024, 'regular', ?, 'x:1', 'x:2', 0, 'final', 80, 70, ?, ?, ?,"
                 " 'observed_retrieval', 1, ?)", (TIPOFF, TIPOFF, TIPOFF, TIPOFF, TIPOFF))
    conn.execute("INSERT INTO raw_fetches (raw_id, retrieved_at) VALUES (1, ?)", (TIPOFF,))


def snapshot(conn):
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                                          "AND name != 'schema_migrations' ORDER BY name")]
    return {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t}")] for t in tables}


@pytest.mark.parametrize("sport,expected_last", [("nba", 40), ("nhl", 40), ("nfl", 40), ("soccer", 41)])
def test_other_sports_stores_never_receive_0042_and_keep_their_rows(tmp_path, monkeypatch, sport, expected_last):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    conn = db.connect(config.db_path(sport))
    db.migrate(conn, upto=34)
    seed(conn)
    db.claim_store(conn, sport)
    before = {t: rows for t, rows in snapshot(conn).items() if t in ("teams", "games", "raw_responses", "raw_fetches", "store_identity")}
    applied = db.migrate(conn)
    assert applied[-1].startswith(f"00{expected_last}") and "0042_ncaab.sql" not in applied
    assert not any(db.has_table(conn, t) for t in NCAAB_TABLES)
    after = snapshot(conn)
    for table, rows in before.items():
        # later migrations only append columns to games; every stored value is unchanged
        assert [r[:len(rows[0])] for r in after[table]] == rows, table
    with pytest.raises(Exception):
        conn.execute("INSERT INTO market_quotes (sport, game_id, provider, market, price_label, line, price_a, price_b, p_a_devig,"
                     " p_b_devig, retrieved_at, fetch_id, raw_id) VALUES ('ncaab', 'x:g', 'b', 'spread', 'open', 1, -110, -110,"
                     " .5, .5, ?, 1, 1)", (TIPOFF,))
    assert db.migrate(conn) == []                                   # idempotent
    assert max(r[0] for r in conn.execute("SELECT version FROM schema_migrations")) == expected_last


def test_ncaab_store_migrates_from_an_older_schema_keeping_rows_and_gaining_the_ncaab_tables(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    conn = db.connect(config.db_path("ncaab"))
    db.migrate(conn, upto=40)
    seed(conn)
    conn.execute("DROP TRIGGER market_quotes_store_sport")           # the store is unclaimed; the migration recreates it
    conn.execute("INSERT INTO market_quotes (sport, game_id, provider, market, price_label, line, price_a, price_b, p_a_devig,"
                 " p_b_devig, retrieved_at, fetch_id, raw_id) VALUES ('nba', 'x:g', 'b', 'spread', 'open', 1, -110, -110,"
                 " .5, .5, ?, 1, 1)", (TIPOFF,))
    quote = tuple(conn.execute("SELECT * FROM market_quotes").fetchone())
    before = {t: rows for t, rows in snapshot(conn).items() if t in ("teams", "games", "raw_responses", "raw_fetches")}
    applied = db.migrate(conn)
    assert applied == ["0042_ncaab.sql"] and all(db.has_table(conn, t) for t in NCAAB_TABLES)
    assert tuple(conn.execute("SELECT * FROM market_quotes").fetchone()) == quote
    assert {t: rows for t, rows in snapshot(conn).items() if t in before} == before
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == [] and conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert db.claim_store(conn, "ncaab") == "ncaab"


def test_ncaab_store_accepts_ncaab_quotes_only_for_its_own_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    conn = db.connect(config.db_path("ncaab"))
    db.migrate(conn)
    seed(conn)
    db.claim_store(conn, "ncaab")
    sql = ("INSERT INTO market_quotes (sport, game_id, provider, market, price_label, line, price_a, price_b, p_a_devig,"
           " p_b_devig, retrieved_at, fetch_id, raw_id) VALUES (?, 'x:g', 'b', 'spread', 'open', 1, -110, -110, .5, .5, ?, 1, 1)")
    conn.execute(sql, ("ncaab", TIPOFF))
    with pytest.raises(Exception):
        conn.execute(sql, ("nba", TIPOFF))
    with pytest.raises(Exception):                                  # append-only
        conn.execute("DELETE FROM market_quotes")


def test_ncaab_tables_refuse_rows_in_a_store_that_is_not_ncaab(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    conn = db.connect(config.db_path("ncaab"))
    db.migrate(conn)                                                # unclaimed
    seed(conn)
    with pytest.raises(Exception):
        conn.execute("INSERT INTO ncaab_team_seasons VALUES (2024, 'x:1', '8', 1, ?, ?)", (TIPOFF, TIPOFF))
