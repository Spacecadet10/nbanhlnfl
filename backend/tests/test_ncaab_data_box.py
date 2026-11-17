"""NCAAB box scores (ESPN game summaries), after-game odds, the D-I list cross-check, the model-facing loader and the CLI
(real, trimmed ESPN responses; no network)."""
import json

import pytest

from app import cli, ncaab
from app.sources import espn_ncaab
from ncaab_helpers import counts, dump, load, ncaab as ncaab_store, raw  # noqa: F401

REG_A, OT_GAME, NON_DI = "ncaab:401827625", "ncaab:401814522", "ncaab:401831990"
SB = "scoreboard_2026-01-17.json"
AT = "2026-01-18T12:00:00Z"
BOX_AT = "2026-01-18T13:00:00Z"


def seed_day(conn, name=SB, at=AT):
    return ncaab.ingest_day(conn, "20260117", fetch=lambda _d: raw(name), retrieved_at=at)


def summary_for(game_id):
    return lambda _g: raw(f"summary_{game_id.split(':')[1]}.json")


def test_summary_parse_has_the_four_possession_inputs():
    box = espn_ncaab.parse_summary(json.loads(raw("summary_401745972.json")))
    assert box.completed and box.game_id == "ncaab:401745972" and len(box.lines) == 2
    for line in box.lines:
        assert line.team_id.startswith("ncaab:")
        assert all(getattr(line, f) is not None for f in ("points", "fga", "fta", "oreb", "dreb", "tov"))
    assert {l.is_home for l in box.lines} == {0, 1}


def test_after_game_lines_are_parsed_with_home_handicap_and_provider():
    lines = espn_ncaab.parse_summary_lines(json.loads(raw("summary_401827625.json")))
    assert [(l.provider, l.spread_home, l.over_under) for l in lines] == [("DraftKings", 9.5, 165.5)]
    old = espn_ncaab.parse_summary_lines(json.loads(raw("summary_330120152.json")))
    assert {"consensus", "numberfire"} <= {l.provider for l in old}
    assert all(l.spread_home is not None or l.over_under is not None for l in old)


def test_ingest_summary_records_possessions_attempt_and_after_game_lines(ncaab_store):
    seed_day(ncaab_store)
    out = ncaab.ingest_summary(ncaab_store, REG_A, fetch=summary_for(REG_A), retrieved_at=BOX_AT)
    assert out["recorded"] == 2 and out["lines_recorded"] == 1
    obs = ncaab_store.execute("SELECT fga, fta, oreb, tov, availability_basis, available_at, observed_at FROM "
                              "team_possession_observations WHERE game_id=?", (REG_A,)).fetchall()
    assert len(obs) == 2 and all(None not in (o["fga"], o["fta"], o["oreb"], o["tov"]) for o in obs)
    assert all(o["availability_basis"] == "observed_retrieval" and o["available_at"] == BOX_AT for o in obs)
    assert ncaab_store.execute("SELECT outcome FROM box_fetch_attempts WHERE game_id=?", (REG_A,)).fetchone()[0] == "fetched"
    line = ncaab_store.execute("SELECT provider, spread_home, over_under, basis, retrieved_at FROM ncaab_after_game_lines").fetchone()
    assert tuple(line) == ("DraftKings", 9.5, 165.5, "retrieved_after_game", BOX_AT)


def test_historical_backfill_box_is_available_with_the_assumed_result_time(ncaab_store):
    seed_day(ncaab_store, at="2026-11-17T06:00:00Z")                  # result assumed available at tipoff + 4 h
    ncaab.ingest_summary(ncaab_store, REG_A, fetch=summary_for(REG_A), retrieved_at="2026-11-17T07:00:00Z")
    r = ncaab_store.execute("SELECT o.availability_basis, o.available_at, g.result_available_at FROM team_possession_observations o "
                            "JOIN games g USING (game_id) LIMIT 1").fetchone()
    assert r["availability_basis"] == "historical_backfill_assumed" and r["available_at"] == r["result_available_at"]


def test_pending_boxes_are_resumable_and_skip_non_d1_and_unfinished_games(ncaab_store):
    seed_day(ncaab_store)
    ncaab.ingest_day(ncaab_store, "20261117", fetch=lambda _d: raw("scoreboard_2026-11-17.json"), retrieved_at="2026-11-17T12:00:00Z")
    pending = ncaab.box_pending(ncaab_store)
    assert set(pending) == {"ncaab:401827625", "ncaab:401827626", "ncaab:401814522"}      # D-I finals only
    ncaab.ingest_summary(ncaab_store, REG_A, fetch=summary_for(REG_A), retrieved_at=BOX_AT)
    assert REG_A not in ncaab.box_pending(ncaab_store)
    assert ncaab.box_pending(ncaab_store, limit=1) == ncaab.box_pending(ncaab_store)[:1]


def test_fetch_boxes_records_failures_continues_and_stops_retrying_after_three(ncaab_store):
    seed_day(ncaab_store)
    seen = []

    def fetch(game_id):
        seen.append(game_id)
        if game_id != REG_A:
            raise RuntimeError("HTTP 500")
        return raw("summary_401827625.json")
    clock = iter(f"2026-01-18T13:00:{s:02d}Z" for s in range(60))
    out = ncaab.fetch_boxes(ncaab_store, fetch=fetch, sleep=lambda _s: None, clock=lambda: next(clock))
    assert (out["fetched"], out["failed"], out["requests"]) == (1, 2, 3) and len(seen) == 3        # one failure does not stop the rest
    for _ in range(4):
        ncaab.fetch_boxes(ncaab_store, fetch=fetch, sleep=lambda _s: None, clock=lambda: next(clock))
    for game in (OT_GAME, "ncaab:401827626"):
        failed = ncaab_store.execute("SELECT COUNT(*) FROM box_fetch_attempts WHERE game_id=? AND outcome='failed'", (game,)).fetchone()[0]
        assert failed == ncaab.MAX_BOX_ATTEMPTS
    assert ncaab.box_pending(ncaab_store) == [] and len(seen) == 1 + 2 * ncaab.MAX_BOX_ATTEMPTS
    assert ncaab_store.execute("SELECT COUNT(*) FROM team_possession_observations WHERE game_id=?", (REG_A,)).fetchone()[0] == 2


def test_incomplete_summary_records_nothing_is_retried_but_only_up_to_the_allowance(ncaab_store):
    seed_day(ncaab_store)
    payload = load("summary_401827625.json")
    payload["header"]["competitions"][0]["status"]["type"]["completed"] = False
    for i in range(ncaab.MAX_BOX_ATTEMPTS):
        assert REG_A in ncaab.box_pending(ncaab_store)
        out = ncaab.ingest_summary(ncaab_store, REG_A, fetch=lambda _g: dump(payload), retrieved_at=f"2026-01-18T13:0{i}:00Z")
        assert out["recorded"] == 0 and out["skipped_not_completed"] >= 1
    assert REG_A not in ncaab.box_pending(ncaab_store)
    assert ncaab_store.execute("SELECT COUNT(*) FROM team_possession_observations").fetchone()[0] == 0


# --- Division I cross-check ------------------------------------------------------------------------------------
def test_current_d1_list_is_stored_and_disagreements_are_reported_not_resolved(ncaab_store):
    seed_day(ncaab_store)
    s = ncaab.ingest_teams(ncaab_store, fetch=lambda: raw("teams_d1.json"), retrieved_at="2026-01-20T12:00:00Z")
    assert s["listed"] == 5 and s["season"] == 2026
    assert ncaab.ingest_teams(ncaab_store, fetch=lambda: raw("teams_d1.json"), retrieved_at="2026-01-21T12:00:00Z")["new"] == 0
    non_di_team = ncaab_store.execute("SELECT team_id FROM ncaab_team_seasons WHERE is_d1 = 0").fetchone()[0]
    ncaab_store.execute("INSERT INTO ncaab_d1_listings (season, team_id, retrieved_at, fetch_id, raw_id) "
                        "SELECT season, ?, retrieved_at, fetch_id, raw_id FROM ncaab_d1_listings LIMIT 1", (non_di_team,))
    d = ncaab.d1_disagreements(ncaab_store, 2026)
    assert [r["team_id"] for r in d["listed_but_non_d1_on_scoreboard"]] == [non_di_team]
    assert d["scoreboard_d1_not_listed"] and non_di_team not in {r["team_id"] for r in d["scoreboard_d1_not_listed"]}


# --- Model-facing loader -----------------------------------------------------------------------------------------
def test_load_rows_returns_only_eligible_final_games_known_by_the_cutoff(ncaab_store):
    seed_day(ncaab_store)
    ncaab.ingest_summary(ncaab_store, REG_A, fetch=summary_for(REG_A), retrieved_at=BOX_AT)
    rows = ncaab.load_rows(ncaab_store, cutoff="2026-02-01T00:00:00Z")
    assert {r["game_id"] for r in rows} == {REG_A, OT_GAME, "ncaab:401827626"} and NON_DI not in {r["game_id"] for r in rows}
    by = {r["game_id"]: r for r in rows}
    assert by[OT_GAME]["ot_periods"] == 1 and by[REG_A]["conference_competition"] == 1 and by[REG_A]["home_oreb"] is not None
    assert by[OT_GAME]["home_oreb"] is None                               # no box fetched for it: left missing, never guessed
    assert ncaab.load_rows(ncaab_store, cutoff="2026-01-18T00:00:00Z") == []     # results not yet available
    early = ncaab.load_rows(ncaab_store, cutoff="2026-01-18T12:30:00Z")
    assert early and all(r["home_oreb"] is None for r in early)           # the box arrived at 13:00, after this cutoff


def test_dataset_version_is_stable_and_follows_results(ncaab_store):
    seed_day(ncaab_store)
    v1 = ncaab.dataset_version(ncaab.load_rows(ncaab_store, cutoff="2026-02-01T00:00:00Z"))
    assert v1 == ncaab.dataset_version(ncaab.load_rows(ncaab_store, cutoff="2026-03-01T00:00:00Z")) and len(v1) == 16
    payload = json.loads(raw(SB))
    next(c for c in payload["events"][1]["competitions"][0]["competitors"] if c["homeAway"] == "home")["score"] = "1"
    ncaab.ingest_day(ncaab_store, "20260117", fetch=lambda _d: dump(payload), retrieved_at="2026-01-25T12:00:00Z")
    assert ncaab.dataset_version(ncaab.load_rows(ncaab_store, cutoff="2026-03-01T00:00:00Z")) != v1


# --- Coverage and CLI -------------------------------------------------------------------------------------------
def test_coverage_counts_per_season(ncaab_store):
    seed_day(ncaab_store)
    ncaab.ingest_summary(ncaab_store, REG_A, fetch=summary_for(REG_A), retrieved_at=BOX_AT)
    cov = ncaab.coverage(ncaab_store)
    s = cov["seasons"]["2026"]
    assert (s["games"], s["d1_eligible"], s["non_d1"], s["final"], s["ot"]) == (4, 3, 1, 4, 1)
    assert s["eligible_final"] == 3 and s["box_complete"] == 1 and s["box_pct"] == pytest.approx(100 / 3, abs=0.1)
    assert s["scoreboard_fga_fta"] == 3 and s["odds_games"] == 1 and s["neutral"] == 0 and s["postseason"] == 0
    assert cov["eligible_final_results"] == 3 and cov["requests"]["scoreboard_days"] == 1


def test_cli_registers_ncaab_and_reports_coverage(ncaab_store, capsys, tmp_path):
    seed_day(ncaab_store)
    ncaab_store.close()
    assert cli.main(["ncaab", "coverage"]) == 0
    assert json.loads(capsys.readouterr().out)["sport"] == "ncaab"
    with pytest.raises(SystemExit):
        cli.main(["ncaab", "ingest"])                                      # needs --from/--to
