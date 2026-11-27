"""The research status page reports the prospective challengers (NCAAB, NFL, Soccer) honestly: waiting until rows exist, then
recording; never passed. Pairing and scores are read at evaluation time (app.learning_eval), not here."""
from app import autoresearch, forward_status, learning_challengers as lc

SPORTS = ("ncaab", "nfl", "soccer")


def test_no_store_means_waiting_for_every_sport(tmp_path):
    st = forward_status.challenger_statuses(tmp_path / "challengers.sqlite")
    assert set(st) == set(SPORTS) and all(v["status"] == "waiting" and v["rows"] == 0 for v in st.values())


def test_rows_mean_recording_never_passed_and_only_for_their_sport(tmp_path):
    path = tmp_path / "challengers.sqlite"
    conn = lc.open_store(path)
    conn.execute("INSERT INTO challenger_forecasts (policy_id, policy_sha, spec_name, spec_json, spec_hash, definition, game_id, market, line, "
                 "p_a, p_mid, p_b, cutoff_at, tipoff_at, generated_at, dataset_version, model_version, recorded_at) VALUES "
                 "('nfl-challengers-v1','s','n','{}','h','d','nfl:1','winner',NULL,0.6,0,0.4,'2026-11-29T16:00:00Z','2026-11-29T18:00:00Z',"
                 "'2026-11-29T16:00:00Z','v','m','2026-11-29T16:01:00Z')")
    conn.commit()
    conn.close()
    st = forward_status.challenger_statuses(path)
    assert st["nfl"]["status"] == "recording" and st["nfl"]["rows"] == 1 and st["nfl"]["games"] == 1
    assert st["ncaab"]["status"] == "waiting" and st["soccer"]["status"] == "waiting"
    assert all(v["status"] != "passed" and "no claim" in v["note"].lower() for v in st.values())


def test_the_published_bar_start_stays_unestablished():
    assert autoresearch.FORWARD_PAIRING["published_bar_from"] is None
