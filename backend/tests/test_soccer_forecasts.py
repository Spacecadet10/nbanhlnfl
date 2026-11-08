"""Soccer 1X2 forecast ledger (migration 0037, app/soccer_forecasts.py) and /api/soccer/research/*.

What these tests pin down: forecasts are append-only; a retry (same run, match, cutoff, inputs) is stored once and a
genuinely new forecast becomes the next version; probabilities sum to one; a research/backtest row is never published
and never has a cutoff after its match date; a prospective row is never generated after its cutoff; only the regulation
1X2 market exists; the API returns only stored rows with their run type and label, and says plainly when none exist."""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db, soccer_forecasts as sf, soccer_history as h
from tests.test_soccer_history import EPL, SHA_A, T1, fetch_league, league, rows

EPL22 = [{**x, "date": x["date"].replace("2023-", "2022-")} for x in EPL]
RUN = dict(run_type="backtest", period="validation", model="elo", model_version="v1", config={"K": 20}, calibrated=False,
           protocol_sha256="p" * 64, data_digest="d" * 64, label=sf.BACKTEST_LABEL, source_run="dev1")


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    c = db.connect(sport="soccer")
    db.migrate(c)
    db.claim_store(c, "soccer")
    c.commit()
    fetch_league(c, SHA_A, league(EPL22), T1, path="2022-23/en.1.json")   # an open season (reserved ones are never read)
    c.commit()
    yield c
    c.close()


def mid(c, home):
    return rows(c)[next(k for k in rows(c) if k[0] == home)]["hist_match_id"]


def fc(match_id, p=(0.5, 0.3, 0.2), cutoff="2022-08-12T06:00:00Z"):
    return {"mid": match_id, "cutoff_at": cutoff, "p": p}


def test_a_retry_is_stored_once_and_new_inputs_make_a_new_version(store):
    m = mid(store, "Arsenal FC")
    sf.record_run(store, run_id="r1", **RUN)
    assert sf.record(store, "r1", [fc(m)], computed_at=T1, inputs_digest="a") == {"inserted": 1, "retries": 0, "new_versions": 0}
    assert sf.record(store, "r1", [fc(m)], computed_at=T1, inputs_digest="a")["retries"] == 1
    assert sf.record(store, "r1", [fc(m, (0.4, 0.3, 0.3))], computed_at=T1, inputs_digest="b")["new_versions"] == 1
    assert [r[0] for r in store.execute("SELECT version FROM soccer_forecasts ORDER BY version")] == [1, 2]
    assert sf.record_run(store, run_id="r1", **RUN) is False                     # same run again: no-op
    with pytest.raises(ValueError):
        sf.record_run(store, run_id="r1", **{**RUN, "model": "poisson"})       # same id, other content


def test_the_ledger_is_append_only_and_checks_its_rows(store):
    m = mid(store, "Arsenal FC")
    sf.record_run(store, run_id="r1", **RUN)
    sf.record(store, "r1", [fc(m)], computed_at=T1, inputs_digest="a")
    for sql in ("UPDATE soccer_forecasts SET p_home = 0.6", "DELETE FROM soccer_forecasts",
                "UPDATE soccer_forecast_runs SET label = 'Live'", "DELETE FROM soccer_forecast_runs"):
        with pytest.raises(sqlite3.IntegrityError):
            store.execute(sql)
    for bad in [fc(m, (0.5, 0.3, 0.3)), fc(m, (1.0, 0.0, 0.0))]:               # not summing to 1; a zero
        with pytest.raises(sqlite3.IntegrityError):
            sf.record(store, "r1", [bad], computed_at=T1, inputs_digest="x" + str(bad["p"]))


def test_a_research_row_is_never_published_or_cut_off_after_its_match(store):
    m = mid(store, "Arsenal FC")                                                # match date 2022-08-12
    sf.record_run(store, run_id="r1", **RUN)
    with pytest.raises(sqlite3.IntegrityError, match="cutoff"):
        sf.record(store, "r1", [fc(m, cutoff="2022-08-13T06:00:00Z")], computed_at=T1, inputs_digest="a")
    with pytest.raises(sqlite3.IntegrityError, match="never published"):
        store.execute("INSERT INTO soccer_forecasts (run_id, hist_match_id, market, version, idempotency_key, inputs_digest, "
                      "p_home, p_draw, p_away, cutoff_at, computed_at, generated_at, published_at) VALUES ('r1', ?, "
                      "'1x2_regulation', 1, 'k', 'a', 0.5, 0.3, 0.2, '2022-08-12T06:00:00Z', ?, ?, ?)", (m, T1, T1, T1))
    for run_type, period, label in (("backtest", "validation", "Live"), ("backtest", "validation", "Live backtest"),
                                    ("reserved_test", "reserved_test", "Live"), ("prospective", "prospective", "Live")):
        with pytest.raises(sqlite3.IntegrityError):                            # each run type has its one fixed label
            sf.record_run(store, run_id=f"bad-{run_type}-{label}", **{**RUN, "run_type": run_type, "period": period,
                                                                       "label": label})


def test_a_prospective_row_is_never_generated_after_its_cutoff(store):
    m = mid(store, "Arsenal FC")
    sf.record_run(store, run_id="p1", **{**RUN, "run_type": "prospective", "period": "prospective", "label": sf.LABELS["prospective"]})
    with pytest.raises(sqlite3.IntegrityError, match="prospective"):     # generated now (2026), cutoff in 2023
        sf.record(store, "p1", [fc(m)], computed_at=T1, inputs_digest="a")


def test_only_the_regulation_1x2_market_exists(store):
    m = mid(store, "Arsenal FC")
    sf.record_run(store, run_id="r1", **RUN)
    with pytest.raises(sqlite3.IntegrityError):
        store.execute("INSERT INTO soccer_forecasts (run_id, hist_match_id, market, version, idempotency_key, inputs_digest, "
                      "p_home, p_draw, p_away, cutoff_at, computed_at, generated_at) VALUES ('r1', ?, 'totals_2.5', 1, 'k',"
                      " 'a', 0.5, 0.3, 0.2, '2022-08-12T06:00:00Z', ?, ?)", (m, T1, T1))


def test_research_api_returns_stored_rows_with_their_labels(store, monkeypatch):
    from app import main
    monkeypatch.setattr(main.app, "dependency_overrides", {})          # review S6 M1: test_api sets demo for the session
    m = mid(store, "Arsenal FC")
    sf.record_run(store, run_id="r1", **RUN, candidate_sha256="c" * 64)
    sf.record(store, "r1", [fc(m)], computed_at=T1, inputs_digest="a")
    store.commit()
    monkeypatch.setenv("NBA_API_MODE", "real")
    client = TestClient(main.app)
    meta = client.get("/api/soccer/research/meta", params={"competition": "eng.1", "mode": "real"})
    assert meta.status_code == 200, meta.text
    assert meta.json()["forecast_dates"] == ["2022-08-12"] and meta.json()["runs"][0]["run_type"] == "backtest"
    day = client.get("/api/soccer/research/matches", params={"competition": "eng.1", "date": "2022-08-12", "mode": "real"}).json()
    ars = next(x for x in day["matches"] if x["home"]["name"] == "Arsenal FC")
    (f,) = ars["forecasts"]
    assert (f["label"], f["run_type"], f["published_at"], f["is_selected_candidate"]) == (sf.BACKTEST_LABEL, "backtest", None, True)
    assert abs(f["p_home"] + f["p_draw"] + f["p_away"] - 1) < 1e-9 and ars["regulation_result"] == "home"
    other = next(x for x in day["matches"] if x["home"]["name"] != "Arsenal FC")
    assert other["forecasts"] == []                                            # no stored row: nothing computed
    detail = client.get(f"/api/soccer/research/matches/{m}", params={"mode": "real"}).json()
    assert detail["source"]["commit_sha"] == SHA_A and len(detail["revisions"]) == 1
    perf = client.get("/api/soccer/research/performance", params={"competition": "eng.1", "mode": "real"}).json()
    assert perf["rows"][0]["n"] == 1 and perf["rows"][0]["label"] == sf.BACKTEST_LABEL
    hist = client.get("/api/soccer/research/history", params={"competition": "eng.1", "mode": "real"}).json()
    assert hist["total"] == 1 and hist["items"][0]["forecast"]["model"] == "elo"
    assert client.get("/api/soccer/research/matches/of:eng.1:2023:0000000000000000", params={"mode": "real"}).status_code == 404


def test_research_api_says_so_when_the_store_has_no_research_tables(tmp_path, monkeypatch):
    from app import main
    monkeypatch.setattr(main.app, "dependency_overrides", {})
    monkeypatch.setenv("NBA_API_MODE", "real")
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    c = db.connect(sport="soccer")
    db.migrate(c, upto=35)
    db.claim_store(c, "soccer")
    c.commit()
    c.close()
    r = TestClient(main.app).get("/api/soccer/research/meta", params={"competition": "eng.1", "mode": "real"})
    assert r.status_code == 503 and "0036/0037" in r.json()["detail"]


def test_research_meta_counts_only_the_selected_competitions_forecasts(store):
    fetch_league(store, SHA_A, league(EPL22, name="x"), "2026-11-08T13:00:00Z", path="2022-23/es.1.json")
    eng = mid(store, "Arsenal FC")
    esp = next(r["hist_match_id"] for r in store.execute("SELECT * FROM soccer_hist_matches WHERE competition_id = 'esp.1'"))
    sf.record_run(store, run_id="r1", **RUN)
    sf.record(store, "r1", [fc(eng), fc(esp)], computed_at=T1, inputs_digest="a")
    (run,) = sf.research_meta(store, "eng.1")["runs"]
    assert run["forecasts"] == 1 and run["first_date"] == "2022-08-12"



# --- Review S6 (REQUEST CHANGES) follow-up -----------------------------------------------------------------------------
def test_timestamps_must_be_utc_iso_and_generation_is_never_backdated(store):
    m = mid(store, "Arsenal FC")
    sf.record_run(store, run_id="r1", **RUN)
    with pytest.raises(sqlite3.IntegrityError):
        sf.record(store, "r1", [fc(m)], computed_at="2020-01-01", inputs_digest="a")
    with pytest.raises(sqlite3.IntegrityError, match="generated"):
        store.execute("INSERT INTO soccer_forecasts (run_id, hist_match_id, market, version, idempotency_key, inputs_digest, "
                      "p_home, p_draw, p_away, cutoff_at, computed_at, generated_at) VALUES ('r1', ?, '1x2_regulation', 1, "
                      "'k', 'a', 0.5, 0.3, 0.2, '2022-08-12T06:00:00Z', ?, ?)", (m, T1, "2024-01-01T00:00:00Z"))


def test_a_retry_with_the_same_key_but_other_numbers_is_refused(store):
    m = mid(store, "Arsenal FC")
    sf.record_run(store, run_id="r1", **RUN)
    sf.record(store, "r1", [fc(m)], computed_at=T1, inputs_digest="a")
    with pytest.raises(ValueError, match="same idempotency key"):
        sf.record(store, "r1", [fc(m, (0.4, 0.4, 0.2))], computed_at=T1, inputs_digest="a")


def test_research_reads_show_backtest_rows_of_the_latest_development_run_only(store):
    m = mid(store, "Arsenal FC")
    sf.record_run(store, run_id="old", **{**RUN, "source_run": "dev1"}, created_at="2026-11-08T10:00:00Z")
    sf.record(store, "old", [fc(m, (0.6, 0.2, 0.2))], computed_at=T1, inputs_digest="a")
    sf.record_run(store, run_id="new", **{**RUN, "source_run": "dev2"}, created_at="2026-11-08T11:00:00Z")
    sf.record(store, "new", [fc(m)], computed_at=T1, inputs_digest="b")
    sf.record_run(store, run_id="t1", **{**RUN, "run_type": "reserved_test", "period": "reserved_test",
                                         "label": sf.LABELS["reserved_test"], "source_run": "dev2"})
    sf.record(store, "t1", [fc(m, (0.7, 0.2, 0.1))], computed_at=T1, inputs_digest="c")
    perf = sf.performance(store, "eng.1")                                   # no ValueError for a reserved_test row
    assert [(r["period"], r["model"], r["n"]) for r in perf["rows"]] == [("validation", "elo", 1)]
    day = sf.research_matches(store, "eng.1", "2022-08-12")
    ars = next(x for x in day["matches"] if x["home"]["name"] == "Arsenal FC")
    assert [f["run_id"] for f in ars["forecasts"]] == ["new"]
    assert [r["run_id"] for r in sf.research_meta(store, "eng.1")["runs"]] == ["new"]
    assert {i["forecast"]["run_id"] for i in sf.research_history(store, "eng.1", None, None)["items"]} == {"new"}
    assert {f["run_id"] for f in sf.research_match(store, m)["forecasts"]} == {"new"}


def test_research_reads_never_serve_a_reserved_season(store):
    body = league([m_ for m_ in [{"round": "Matchday 1", "date": "2023-09-02", "time": "15:00", "team1": "Arsenal FC",
                                  "team2": "Burnley FC", "score": {"ft": [2, 0]}}]])
    fetch_league(store, "e" * 40, body, "2026-11-08T13:00:00Z", path="2023-24/en.1.json")   # season 2023 (reserved)
    resv = next(r["hist_match_id"] for r in store.execute("SELECT * FROM soccer_hist_matches WHERE season = 2023"))
    assert sf.research_match(store, resv) is None
    assert sf.research_matches(store, "eng.1", "2023-09-02")["matches"] == []
    assert sf.research_meta(store, "eng.1")["history"]["last_season"] <= 2022


def test_recording_a_development_folder_checks_it_belongs_to_its_seal_and_store(store, tmp_path):
    out = tmp_path / "dev"
    out.mkdir()
    (out / "development_validation_report.json").write_text(json.dumps({"run": "dev1", "open_data_digest": "x"}))
    (out / "sealed_candidate.json").write_text(json.dumps({"development_run": "other", "sha256": "s", "model": "elo"}))
    (out / "forecasts.jsonl").write_text("")
    with pytest.raises(ValueError, match="development run"):
        sf.record_development(store, out)


def test_research_api_refuses_an_impossible_date(store, monkeypatch):
    from app import main
    monkeypatch.setattr(main.app, "dependency_overrides", {})
    monkeypatch.setenv("NBA_API_MODE", "real")
    r = TestClient(main.app).get("/api/soccer/research/matches", params={"competition": "eng.1", "date": "2022-13-45"})
    assert r.status_code == 422


# --- S6 re-review LOW follow-up -----------------------------------------------------------------------------------------
def test_the_shown_run_is_the_last_one_recorded_with_forecasts_not_the_latest_claimed_time(store):
    m = mid(store, "Arsenal FC")
    sf.record_run(store, run_id="real", **RUN)
    sf.record(store, "real", [fc(m)], computed_at=T1, inputs_digest="a")
    sf.record_run(store, run_id="empty", **{**RUN, "source_run": "ghost"}, created_at="2099-01-01T00:00:00Z")
    assert [r["run_id"] for r in sf.research_meta(store, "eng.1")["runs"]] == ["real"]


def test_the_research_season_cap_comes_from_the_protocol_file_without_loading_the_evaluation_module():
    from app import soccer_eval
    assert sf._last_open() == soccer_eval.LAST_OPEN == 2022
    assert "soccer_eval" not in sf._last_open.__code__.co_names


@pytest.mark.parametrize("broken", ["digest", "count"])
def test_recording_refuses_a_folder_whose_data_or_counts_do_not_match(store, tmp_path, broken):
    from app import soccer_eval
    out = tmp_path / "dev"
    out.mkdir()
    m = mid(store, "Arsenal FC")
    digest = soccer_eval.open_data_digest(store)
    report = {"run": "dev1", "open_data_digest": "other" if broken == "digest" else digest, "finished_at": T1,
              "code": {}, "protocol_sha256": "p", "counts": {"scored": {"validation": 2 if broken == "count" else 1}},
              "frequency": {"frequencies": {}}, "elo": {"selected_config": {}, "draw_model": None},
              "poisson": {"selected_config": {}}}
    (out / "development_validation_report.json").write_text(json.dumps(report))
    (out / "sealed_candidate.json").write_text(json.dumps({"development_run": "dev1", "sha256": "s", "model": "elo"}))
    (out / "forecasts.jsonl").write_text(json.dumps({"model": "elo", "mid": m, "season": 2020, "cutoff_at": "2022-08-12T06:00:00Z",
                                                     "p": [0.5, 0.3, 0.2]}) + "\n")
    with pytest.raises(ValueError, match="open data" if broken == "digest" else "forecasts in the folder"):
        sf.record_development(store, out)
    assert store.execute("SELECT COUNT(*) FROM soccer_forecast_runs").fetchone()[0] == 0
