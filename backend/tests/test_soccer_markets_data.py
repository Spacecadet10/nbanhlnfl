"""Corner counts and bookmaker prices from ESPN soccer scoreboards (migration 0040; app/soccer.py, app/sources/espn.py).

Payloads are REAL ESPN responses kept byte for byte (tests/fixtures/soccer_markets_real): the Premier League finals of
2026-11-08 and three retrievals (2026-11-09 00:20Z, 07:28Z, 09:28Z) of the Nations League scoreboard of 2026-11-13.
Where a test needs a case the real payloads do not contain (a corrected corner count, a withdrawn price), it edits a copy
of a real payload and says so."""
import copy
import json
from pathlib import Path

import pytest

from app import db, soccer
from app.sources import espn

REAL = Path(__file__).parent / "fixtures" / "soccer_markets_real"
EPL = (REAL / "eng1_20261108_finals.json").read_bytes()
NL = [(REAL / f"uefa_nations_20261113_at{t}z.json").read_bytes() for t in ("0020", "0728", "0928")]
NL_AT = ["2026-11-09T00:20:01Z", "2026-11-09T07:28:10Z", "2026-11-09T09:28:31Z"]


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    c = db.connect(sport="soccer")
    db.migrate(c)
    db.claim_store(c, "soccer")
    yield c
    c.close()


def ingest(c, competition, day, body, at):
    return soccer.ingest_day(c, competition, day, fetch=lambda *_: body, retrieved_at=at)


def test_corner_counts_are_parsed_from_the_real_scoreboard():
    rows = espn.parse_soccer_scoreboard(json.loads(EPL), "eng.1")
    got = {(r.home.name, r.away.name): (r.home_score, r.away_score, r.home_corners, r.away_corners) for r in rows}
    assert got[("Brighton & Hove Albion", "Brentford")] == (2, 0, 7, 3)
    assert got[("Manchester United", next(a for h, a in got if h == "Manchester United"))][2:] == (8, 1)
    assert all(v[2] is not None for v in got.values()) and len(got) == 5


def test_stats_are_append_only_and_never_available_before_the_result(store):
    ingest(store, "eng.1", "20261108", EPL, "2026-11-08T22:00:00Z")
    rows = store.execute("SELECT match_id, revision, home_corners, away_corners, observed_at, available_at FROM "
                         "soccer_match_stats ORDER BY match_id").fetchall()
    assert len(rows) == 5 and all(r["revision"] == 1 for r in rows)
    res = {r["match_id"]: r["result_available_at"] for r in store.execute("SELECT match_id, result_available_at FROM soccer_matches")}
    assert all(r["available_at"] >= res[r["match_id"]] for r in rows)
    ingest(store, "eng.1", "20261108", EPL, "2026-11-08T23:00:00Z")          # same counts: nothing new
    assert store.execute("SELECT COUNT(*) FROM soccer_match_stats").fetchone()[0] == 5
    edited = json.loads(EPL)                       # a corrected count (edited copy of the real payload)
    for s in edited["events"][0]["competitions"][0]["competitors"][0]["statistics"]:
        if s["name"] == "wonCorners":
            s["displayValue"] = str(int(s["displayValue"]) + 1)
    ingest(store, "eng.1", "20261108", json.dumps(edited).encode(), "2026-11-09T10:00:00Z")
    mid = f"soccer:eng.1:{edited['events'][0]['id']}"
    revs = store.execute("SELECT revision, available_at FROM soccer_match_stats WHERE match_id = ? ORDER BY revision", (mid,)).fetchall()
    assert [r["revision"] for r in revs] == [1, 2] and revs[1]["available_at"] == "2026-11-09T10:00:00Z"
    ingest(store, "eng.1", "20261108", EPL, "2026-11-09T09:00:00Z")          # an older retrieval changes nothing
    assert store.execute("SELECT COUNT(*) FROM soccer_match_stats WHERE match_id = ?", (mid,)).fetchone()[0] == 2
    with pytest.raises(Exception, match="append-only"):
        store.execute("UPDATE soccer_match_stats SET home_corners = 0")
    with pytest.raises(Exception, match="append-only"):
        store.execute("DELETE FROM soccer_match_stats")


def test_a_final_first_seen_long_after_kickoff_counts_from_kickoff_plus_four_hours(store):
    ingest(store, "eng.1", "20261108", EPL, "2026-11-20T12:00:00Z")
    r = store.execute("SELECT s.available_at, m.kickoff_at, m.result_available_at FROM soccer_match_stats s JOIN soccer_matches m "
                      "ON m.match_id = s.match_id WHERE m.home_team_id = 'soccer:331'").fetchone()
    assert r["available_at"] == r["result_available_at"] == "2026-11-08T18:00:00Z" and r["kickoff_at"] == "2026-11-08T14:00:00Z"


def test_real_draftkings_prices_are_stored_with_side_line_and_retrieval_time(store):
    ingest(store, "uefa.nations", "20261113", NL[0], NL_AT[0])
    q = {(r["market"], r["side"], r["price_kind"]): (r["line"], r["american"], round(r["decimal_odds"], 4), r["provider"])
         for r in store.execute("SELECT * FROM soccer_odds_quotes WHERE match_id = 'soccer:uefa.nations:401861162'")}
    assert q[("1x2", "home", "current")] == (None, 160, 2.6, "DraftKings")
    assert q[("1x2", "draw", "current")] == (None, 190, 2.9, "DraftKings")
    assert q[("1x2", "away", "current")] == (None, 180, 2.8, "DraftKings")
    assert q[("total_goals", "over", "current")] == (2.5, 145, 2.45, "DraftKings")
    assert q[("total_goals", "under", "current")] == (2.5, -190, round(1 + 100 / 190, 4), "DraftKings")
    assert q[("handicap", "home", "current")] == (-0.5, 150, 2.5, "DraftKings")
    assert q[("handicap", "away", "current")] == (0.5, -220, round(1 + 100 / 220, 4), "DraftKings")
    assert store.execute("SELECT COUNT(*) FROM soccer_odds_quotes").fetchone()[0] == 112


def test_only_changed_prices_are_appended_and_a_disappearing_price_is_recorded_withdrawn(store):
    for body, at in zip(NL, NL_AT):
        ingest(store, "uefa.nations", "20261113", body, at)
    changed = store.execute("SELECT market, side, american FROM soccer_odds_quotes WHERE retrieved_at = ? AND match_id = "
                            "'soccer:uefa.nations:401861161' ORDER BY market, side", (NL_AT[1],)).fetchall()
    assert [tuple(r) for r in changed] == [("1x2", "draw", 500), ("1x2", "home", -550), ("handicap", "away", 120),
                                           ("handicap", "home", -175)]
    assert store.execute("SELECT COUNT(*) FROM soccer_odds_quotes WHERE retrieved_at = ?", (NL_AT[1],)).fetchone()[0] == 4
    before = store.execute("SELECT COUNT(*) FROM soccer_odds_quotes").fetchone()[0]
    ingest(store, "uefa.nations", "20261113", NL[2], NL_AT[2])          # a replay of the same retrieval adds nothing
    assert store.execute("SELECT COUNT(*) FROM soccer_odds_quotes").fetchone()[0] == before
    gone = json.loads(NL[2])                     # the price block removed from one match (edited copy of the real payload)
    ev = next(e for e in gone["events"] if e["id"] == "401861162")
    ev["competitions"][0]["odds"] = []
    ingest(store, "uefa.nations", "20261113", json.dumps(gone).encode(), "2026-11-09T12:00:00Z")
    w = store.execute("SELECT offered, american, line FROM soccer_odds_quotes WHERE match_id = 'soccer:uefa.nations:401861162' "
                      "AND retrieved_at = '2026-11-09T12:00:00Z'").fetchall()
    assert len(w) == 14 and all(r["offered"] == 0 and r["american"] is None for r in w)


def test_no_price_is_recorded_at_or_after_kickoff(store):
    ingest(store, "uefa.nations", "20261113", NL[0], "2026-11-13T17:00:00Z")
    late = store.execute("SELECT COUNT(*) FROM soccer_odds_quotes WHERE match_id = 'soccer:uefa.nations:401861162'").fetchone()[0]
    assert late == 0
    with pytest.raises(Exception, match="before kickoff"):
        store.execute("INSERT INTO soccer_odds_quotes (match_id, provider, market, side, price_kind, offered, line, american, "
                      "decimal_odds, retrieved_at, raw_id, parser_version) VALUES ('soccer:uefa.nations:401861162', 'X', "
                      "'1x2', 'home', 'current', 1, NULL, 150, 2.5, '2026-11-13T18:00:00Z', 1, 't')")


def test_month_history_never_counts_as_a_fetched_day_or_a_fresh_retrieval(store):
    soccer.ingest_month(store, "eng.1", "202611", fetch=lambda *_: EPL, retrieved_at="2026-11-09T20:00:00Z")
    assert store.execute("SELECT COUNT(*) FROM soccer_match_stats").fetchone()[0] == 5
    assert soccer.fetched_dates(store, "eng.1") == [] and soccer.last_refresh_at(store, "eng.1") is None
    src = store.execute("SELECT source, request_key FROM raw_responses").fetchone()
    assert tuple(src) == ("espn_soccer_month", "competition=eng.1&dates=202611&limit=1000")
    assert soccer.replay(store)["responses"] == 1                     # replay re-reads month responses too
    out = soccer.ingest_months(store, ["eng.1"], "202610", "202611", sleep=0, fetch=lambda *_: EPL, log=lambda m: None)
    assert [r["month"] for r in out] == ["202610"]                     # an already retrieved month is skipped


def test_a_store_migrated_before_0040_imports_exactly_as_before(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    c = db.connect(sport="soccer")
    db.migrate(c, upto=39)
    db.claim_store(c, "soccer")
    out = ingest(c, "eng.1", "20261108", EPL, "2026-11-08T22:00:00Z")
    assert out["matches_inserted"] == 5 and "quotes" not in out
