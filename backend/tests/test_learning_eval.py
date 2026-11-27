"""Read-time paired scoring of challengers against the incumbent (app/learning_eval.py). Expected values are computed by hand."""
import json
import math
import sqlite3

import pytest

from app import learning_challengers as lc, learning_eval as le
from test_candidate_pool import GID, TIPOFF, upcoming  # noqa: F401  (fixtures)
from test_live_markets import sealed  # noqa: F401
from test_nhl_eval import nhl_store  # noqa: F401

CUT = "2026-10-12T22:00:00Z"


def challenger_row(conn, market, p, line=None, name="spec:a", policy="nhl-challengers-v9", sha="s" * 64, h="h1", cutoff=CUT, game=GID):
    conn.execute(
        "INSERT INTO challenger_forecasts (policy_id, policy_sha, spec_name, spec_json, spec_hash, definition, game_id, market, line, "
        "p_a, p_mid, p_b, cutoff_at, tipoff_at, generated_at, dataset_version, model_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (policy, sha, name, "{}", h, "d", game, market, line, *p, cutoff, TIPOFF, cutoff, "ds", f"challenger:{name}"))


def cohort(store, market, p, line=None, name="inc", at="2026-10-12T21:00:00Z", tipoff=TIPOFF, key=None):
    store.execute("PRAGMA foreign_keys = OFF")
    store.execute("DROP TRIGGER IF EXISTS research_cohorts_guard")
    store.execute(
        "INSERT INTO research_cohorts (idempotency_key, sport, game_id, market, line, line_source, bookmaker, definition, primary_name, "
        "information_at, generated_at, tipoff_at, members, agreement) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (key or f"{market}{line}{at}{name}", "nhl", GID, market, line, None if line is None else "model", "null", "d", name, at, at,
         tipoff, json.dumps([{"name": name, "ref": {}, "p": p}]), "{}"))


def finalise(store, home, away, decision=None):
    store.execute("PRAGMA foreign_keys = OFF")
    store.execute("UPDATE games SET status='final', home_score=?, away_score=?, result_available_at='2026-10-13T03:00:00Z', "
                  "result_availability_basis='observed_retrieval' WHERE game_id=?", (home, away, GID))
    if decision:
        store.execute("INSERT OR REPLACE INTO game_decisions (game_id, decision, periods, last_raw_id) VALUES (?,?,3,"
                      "(SELECT raw_id FROM raw_responses LIMIT 1))", (GID, decision))


@pytest.fixture
def env(upcoming, tmp_path):
    cc = lc.open_store(tmp_path / "challengers.sqlite")
    return upcoming, cc


def group(out, market, name="spec:a"):
    return [g for g in out["groups"] if g["market"] == market and g["spec_name"] == name][0]


def test_winner_pair_log_loss_and_brier_differences(env):
    store, cc = env
    challenger_row(cc, "winner", (0.7, 0.0, 0.3))
    cohort(store, "winner", [0.6, 0.0, 0.4])
    finalise(store, 3, 2)                                            # home win: side a
    out = le.evaluate(store, cc)
    g = group(out, "winner")
    assert g["n_scored"] == 1 and g["n_unsettled"] == 0 and g["n_no_incumbent"] == 0
    assert g["log_loss_challenger"] == pytest.approx(-math.log(0.7)) and g["log_loss_incumbent"] == pytest.approx(-math.log(0.6))
    assert g["log_loss_diff"] == pytest.approx(math.log(0.6 / 0.7))                    # negative: challenger better
    assert g["brier_diff"] == pytest.approx(0.18 - 0.32)
    assert "prospective" in out["label"].lower() and "no claim" in out["label"].lower() and "preregistered" in out["label"].lower()
    assert g["claim"].startswith("no claim")


def test_unsettled_games_and_missing_incumbents_are_counted_not_dropped(env):
    store, cc = env
    challenger_row(cc, "winner", (0.7, 0.0, 0.3))
    cohort(store, "winner", [0.6, 0.0, 0.4])
    g = group(le.evaluate(store, cc), "winner")                         # game still scheduled
    assert g["n_scored"] == 0 and g["n_unsettled"] == 1 and g["log_loss_diff"] is None
    finalise(store, 3, 2)
    challenger_row(cc, "total", (0.5, 0.1, 0.4), line=6.5)               # no incumbent cohort for the total
    g = group(le.evaluate(store, cc), "total")
    assert g["n_scored"] == 0 and g["n_no_incumbent"] == 1


def test_line_markets_are_paired_only_at_the_same_line_and_push_is_scored(env):
    store, cc = env
    finalise(store, 3, 2)                                                    # total 5 == line: push (side mid)
    challenger_row(cc, "total", (0.45, 0.10, 0.45), line=5.0)
    cohort(store, "total", [0.5, 0.0, 0.5], line=6.5, key="other-line")     # a different line is never paired
    g = group(le.evaluate(store, cc), "total")
    assert g["n_no_incumbent"] == 1
    cohort(store, "total", [0.40, 0.2, 0.40], line=5.0)
    g = group(le.evaluate(store, cc), "total")
    assert g["n_scored"] == 1 and g["log_loss_diff"] == pytest.approx(-math.log(0.10) + math.log(0.2))


def test_incumbent_is_the_latest_cohort_at_or_before_the_challenger_cutoff(env):
    store, cc = env
    challenger_row(cc, "winner", (0.7, 0.0, 0.3))
    cohort(store, "winner", [0.55, 0.0, 0.45], at="2026-10-12T20:00:00Z")
    cohort(store, "winner", [0.60, 0.0, 0.40], at="2026-10-12T21:30:00Z")
    cohort(store, "winner", [0.99, 0.0, 0.01], at="2026-10-12T22:30:00Z")        # after the cutoff: never used
    finalise(store, 3, 2)
    assert group(le.evaluate(store, cc), "winner")["log_loss_incumbent"] == pytest.approx(-math.log(0.6))


def test_a_start_time_change_voids_and_reg3_uses_the_stored_decision(env):
    store, cc = env
    challenger_row(cc, "reg3", (0.5, 0.3, 0.2))
    cohort(store, "reg3", [0.4, 0.4, 0.2])
    finalise(store, 3, 2, decision="OT")                                          # not regulation: side mid
    g = group(le.evaluate(store, cc), "reg3")
    assert g["n_scored"] == 1 and g["log_loss_diff"] == pytest.approx(-math.log(0.3) + math.log(0.4))
    store.execute("PRAGMA foreign_keys = OFF")
    store.execute("UPDATE games SET tipoff_at='2026-10-13T01:00:00Z' WHERE game_id=?", (GID,))
    g = group(le.evaluate(store, cc), "reg3")
    assert g["n_scored"] == 0 and g["n_void"] == 1


def test_empty_store_reports_no_rows_with_the_label(env):
    store, cc = env
    out = le.evaluate(store, cc)
    assert out["groups"] == [] and "no claim" in out["label"].lower()


def test_a_moving_line_scores_the_earliest_row_once_so_n_counts_games(env):
    store, cc = env
    finalise(store, 3, 2)
    challenger_row(cc, "total", (0.45, 0.10, 0.45), line=5.0, cutoff="2026-10-12T21:30:00Z")
    challenger_row(cc, "total", (0.30, 0.10, 0.60), line=5.5, cutoff="2026-10-12T22:00:00Z")      # later row, different line
    cohort(store, "total", [0.4, 0.2, 0.4], line=5.0)
    cohort(store, "total", [0.4, 0.2, 0.4], line=5.5, key="l55", at="2026-10-12T21:45:00Z")
    g = group(le.evaluate(store, cc), "total")
    assert g["n_scored"] == 1 and g["log_loss_challenger"] == pytest.approx(-math.log(0.10))     # the earliest row only


# ---- pairing with the published forecast (NCAAB/NFL/Soccer; autoresearch.FORWARD_PAIRING, on information times) -------------
def _row(cutoff="2026-11-20T18:10:00Z", tipoff="2026-11-20T20:00:00Z", line=140.5):
    return {"cutoff_at": cutoff, "tipoff_at": tipoff, "line": line}


def _run(info, made=None, line=140.5, p=0.6):
    return (info, made or info, line, [p, 0.0, 1 - p])


def test_pairing_takes_the_first_published_run_whose_information_is_at_or_after_the_challengers_within_ten_minutes():
    runs = [_run("2026-11-20T18:00:00Z", p=0.1), _run("2026-11-20T18:15:00Z", p=0.2), _run("2026-11-20T18:40:00Z", p=0.3)]
    assert le.pair(_row(), runs) == ([0.2, 0.0, 0.8], None)
    assert le.pair(_row("2026-11-20T18:15:00Z"), runs)[1] is None                    # the same instant is allowed (never later)


def test_a_published_row_written_late_still_pairs_by_its_information_time():
    assert le.pair(_row("2026-11-20T18:00:00Z"), [_run("2026-11-20T18:00:00Z", made="2026-11-20T18:50:00Z")])[1] is None


@pytest.mark.parametrize("runs,why", [
    ([], "no_published_row"),
    ([_run("2026-11-20T18:12:00Z", line=141.5)], "line"),                              # exact line only
    ([_run("2026-11-20T18:00:00Z")], "candidate_later"),                               # the challenger knows more than the published run
    ([_run("2026-11-20T18:21:00Z")], "gap"),                                           # more than 10 minutes after the challenger
    ([_run("2026-11-20T18:12:00Z", made="2026-11-20T19:05:00Z")], "published_after_as_of"),   # made only after start - 60 min
])
def test_unpaired_rows_say_why(runs, why):
    assert le.pair(_row(), runs) == (None, why)


def test_a_challenger_after_the_fixed_instant_is_unpaired():
    assert le.pair(_row("2026-11-20T19:10:00Z"), [_run("2026-11-20T19:12:00Z")]) == (None, "as_of_late")


def test_a_pregame_soccer_1x2_row_that_stores_the_kickoff_as_cutoff_uses_the_real_06z_cutoff():
    store = sqlite3.connect(":memory:")
    store.execute("CREATE TABLE soccer_live_forecasts (match_id, version, generated_at, p_home, p_draw, p_away, cutoff_at)")
    kick = "2026-11-25T20:00:00Z"
    store.execute("INSERT INTO soccer_live_forecasts VALUES ('m', 1, '2026-11-25T18:25:00Z', 0.5, 0.3, 0.2, ?)", (kick,))   # kickoff stored as cutoff
    row = {"policy_id": "soccer-challengers-v1", "market": "soccer_1x2", "game_id": "m", "tipoff_at": kick, "line": None,
           "cutoff_at": "2026-11-25T06:00:00Z"}
    assert le._published_soccer(store, row) == [("2026-11-25T06:00:00Z", "2026-11-25T18:25:00Z", None, [0.5, 0.3, 0.2])]
    assert le.pair(row, le._published_soccer(store, row)) == ([0.5, 0.3, 0.2], None)
