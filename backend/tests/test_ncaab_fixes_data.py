"""Review fixes for the NCAAB data side: settlement statuses (S4), sticky Division I classification (N2), TheRundown UTC dates and
budget (S3) and the ticket snapshot with no NCAAB store (S2). Synthetic stores and saved ESPN responses; no network."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app import ncaab, rundown_odds, tickets
from app.sources import espn_ncaab
from ncaab_helpers import counts, dump, load, ncaab as ncaab_store  # noqa: F401  (fixture re-export)

BOARD = "scoreboard_2026-11-17.json"
T1, T2, T3 = "2026-11-17T12:00:00Z", "2026-11-17T18:00:00Z", "2026-11-18T05:00:00Z"


def board(status="STATUS_SCHEDULED", away_conference="12", scores=None, event=0):
    """The 2026-11-17 scoreboard with its first event given a status (and both conference ids unless one is dropped)."""
    payload = load(BOARD)
    ev = payload["events"][event]
    comp = ev["competitions"][0]
    comp["status"]["type"]["name"] = status
    for c in comp["competitors"]:
        c["team"]["conferenceId"] = "11" if c["homeAway"] == "home" else away_conference
        if away_conference is None and c["homeAway"] == "away":
            c["team"].pop("conferenceId")
        if scores:
            c["score"] = str(scores[c["homeAway"]])
    return payload, f"ncaab:{ev['id']}"


def ingest(conn, payload, at):
    return ncaab.ingest_day(conn, "20261117", fetch=lambda _d: dump(payload), retrieved_at=at)


# --- S4: no game was played, or not finished: the shared settlement voids postponed / canceled only ---------------------------------
@pytest.mark.parametrize("source, stored", [("STATUS_FORFEIT", "canceled"), ("STATUS_UNCONTESTED", "canceled"),
                                            ("STATUS_SUSPENDED", "postponed")])
def test_forfeit_uncontested_and_suspended_games_settle_void_and_a_later_final_updates_them(ncaab_store, source, stored):
    payload, gid = board(source)
    (parsed,) = [g for g in espn_ncaab.parse_scoreboard(payload) if g.game_id == gid]
    assert parsed.status == stored and parsed.home_score is None and parsed.source_status == source
    ingest(ncaab_store, payload, T1)
    assert ncaab_store.execute("SELECT status FROM games WHERE game_id=?", (gid,)).fetchone()[0] == stored
    from app import markets
    row = ncaab_store.execute("SELECT * FROM games WHERE game_id=?", (gid,)).fetchone()
    assert markets.settle("total", 140.5, status=row["status"], home=None, away=None, decision=None,
                          tipoff_at=row["tipoff_at"], forecast_tipoff_at=row["tipoff_at"]) == "void"
    once = counts(ncaab_store)
    ingest(ncaab_store, payload, T2)                                    # the same response again: nothing but the logged fetch and its quotes
    appended = ("raw_fetches", "market_quotes")                          # every retrieval is logged and quotes are append-only
    assert {k: v for k, v in counts(ncaab_store).items() if k not in appended} == {k: v for k, v in once.items() if k not in appended}
    final, _ = board("STATUS_FINAL", scores={"home": 80, "away": 70})
    ingest(ncaab_store, final, T3)                                      # ESPN completes it later: a normal final
    got = ncaab_store.execute("SELECT status, home_score, away_score FROM games WHERE game_id=?", (gid,)).fetchone()
    assert tuple(got) == ("final", 80, 70)


def test_other_unknown_statuses_still_map_to_other():
    payload, gid = board("STATUS_SOMETHING_NEW")
    assert {g.game_id: g.status for g in espn_ncaab.parse_scoreboard(payload)}[gid] == "other"


# --- N2: one scoreboard without a conferenceId never hides a game that was forecast -----------------------------------------------
def add_forecast(conn, gid):
    tip = conn.execute("SELECT tipoff_at FROM games WHERE game_id=?", (gid,)).fetchone()[0]
    conn.execute("INSERT INTO predictions (idempotency_key, game_id, run_type, model_version, dataset_version, feature_version, "
                 "cutoff_at, generated_at, home_win_prob) VALUES ('k', ?, 'live', 'm', 'd', 'f', ?, '2026-11-17T11:00:00Z', 0.6)",
                 (gid, tip))


def state(conn, gid):
    return tuple(conn.execute("SELECT g.season_type, c.both_d1, c.away_conference_id FROM games g JOIN ncaab_game_context c "
                              "ON c.game_id = g.game_id WHERE g.game_id = ?", (gid,)).fetchone())


def test_a_game_with_a_forecast_is_never_downgraded_by_a_scoreboard_missing_a_conference(ncaab_store):
    payload, gid = board()
    ingest(ncaab_store, payload, T1)
    assert state(ncaab_store, gid) == ("regular", 1, "12")
    add_forecast(ncaab_store, gid)
    missing, _ = board(away_conference=None)
    assert not [g for g in espn_ncaab.parse_scoreboard(missing) if g.game_id == gid][0].both_d1      # the parser alone would downgrade
    ingest(ncaab_store, missing, T2)
    assert state(ncaab_store, gid) == ("regular", 1, "12")
    ingest(ncaab_store, board()[0], T3)                                 # and the next good scoreboard changes nothing
    assert state(ncaab_store, gid) == ("regular", 1, "12")


def test_without_a_forecast_a_missing_conference_downgrades_only_a_team_season_known_to_be_non_d1(ncaab_store):
    payload, gid = board()
    ingest(ncaab_store, payload, T1)
    missing, _ = board(away_conference=None)
    ingest(ncaab_store, missing, T2)                                    # unknown team-season: the stored classification holds
    assert state(ncaab_store, gid) == ("regular", 1, "12")
    away = ncaab_store.execute("SELECT away_team_id FROM games WHERE game_id=?", (gid,)).fetchone()[0]
    ncaab_store.execute("UPDATE ncaab_team_seasons SET is_d1 = 0, conference_id = NULL WHERE team_id = ?", (away,))
    ncaab_store.commit()
    ingest(ncaab_store, missing, T3)                                    # known non-D-I and nothing forecast: downgraded as before
    assert state(ncaab_store, gid) == ("other", 0, None)


def test_a_game_first_seen_without_a_conference_is_other_as_before(ncaab_store):
    payload, gid = board(away_conference=None)
    ingest(ncaab_store, payload, T1)
    assert state(ncaab_store, gid) == ("other", 0, None)


# --- S3: TheRundown files events by UTC date; NCAAB needs both, inside a budget that leaves the other sports theirs ---------------------
class _Response:
    headers: dict = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b'{"events": []}'


def _fetch(tmp_path, monkeypatch, now, extra=(), prior=0):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("THERUNDOWN_API_KEY", "test-key-do-not-save")
    urls = []
    if prior:                                          # earlier runs of the day already in the request log
        directory = tmp_path / "odds-books"
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / "requests.jsonl").open("a") as f:
            for _ in range(prior):
                f.write(json.dumps({"day": now.date().isoformat(), "at": "x", "sport": "nba", "date": "x"}) + "\n")
    result = rundown_odds.fetch(now, opener=lambda r, timeout=0: urls.append(r.full_url) or _Response(), pause=lambda _: None,
                                extra=extra)
    return result, urls


NOW = datetime(2026, 11, 17, 20, 0, tzinfo=timezone.utc)


def test_ncaab_asks_for_both_utc_dates_and_the_default_fetch_is_unchanged(tmp_path, monkeypatch):
    result, urls = _fetch(tmp_path, monkeypatch, NOW)
    assert result["status"] == "ok" and len(urls) == 6 and not any("/sports/5/" in u for u in urls)
    default = [u.split("?")[0] for u in urls]
    result, urls = _fetch(tmp_path / "again", monkeypatch, NOW, extra=("ncaab",))
    assert [u.split("?")[0] for u in urls[:6]] == default                      # the existing sports' requests are the same ones
    assert [u.split("?")[0].rsplit("/", 2)[-2:] for u in urls[6:]] == [["events", "2026-11-17"], ["events", "2026-11-18"]]
    assert all("/sports/5/" in u for u in urls[6:])


def test_ncaab_requests_never_take_the_existing_sports_budget(tmp_path, monkeypatch):
    # Budget (review should-fix 1): the three sports use 7 runs x 6 = 42 of 48. NCAAB asks only in the afternoon ET window
    # (the 16:00 run, 2-7 h before the evening slate), two requests (both UTC dates): 44 a day, at least 4 always spare.
    assert rundown_odds.EXTRA_DAY_REQUESTS["ncaab"] == 2 and rundown_odds.EXTRA_RUN_WINDOW_ET["ncaab"] == (15, 18)
    assert 7 * 6 + rundown_odds.EXTRA_DAY_REQUESTS["ncaab"] <= rundown_odds.DAY_REQUESTS - 4
    assert rundown_odds.DAY_REQUESTS - rundown_odds.EXTRA_RESERVE == 38
    # 37 used so far: one NCAAB request still leaves 10 of the 48 (count 38), the second would not
    result, urls = _fetch(tmp_path, monkeypatch, NOW, extra=("ncaab",), prior=37 - 6)
    assert sum("/sports/5/" in u for u in urls) == 1 and len(urls) == 7
    # at 38 already used, NCAAB is not asked at all; the existing sports' six requests are not NCAAB's to refuse
    result, urls = _fetch(tmp_path / "full", monkeypatch, NOW, extra=("ncaab",), prior=38 - 6)
    assert not any("/sports/5/" in u for u in urls) and len(urls) == 6
    # a whole ET day of the seven scheduled runs: NCAAB only in the 16:00 run, the day total never above 46
    total, sport5, requests = tmp_path / "day", [], 0
    for hour, minute in ((10, 0), (13, 0), (16, 0), (18, 0), (19, 30), (21, 0), (22, 30)):
        at = datetime(2026, 11, 17, hour, minute, tzinfo=timezone.utc) + timedelta(hours=5)      # EST = UTC-5
        _, urls = _fetch(total, monkeypatch, at, extra=("ncaab",))
        sport5.append(sum("/sports/5/" in u for u in urls))
        requests += len(urls)
    assert sport5 == [0, 0, 2, 0, 0, 0, 0] and requests == 44
