"""/api/research/candidates/game/{id}: every candidate model's recorded forecasts for one game (research only)."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import db, main

GID, START = "nhl:2026020001", "2026-10-10T23:00:00Z"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_DATA_DIR", str(tmp_path))
    c = db.connect(tmp_path / "nhl.sqlite", check_same_thread=False)
    db.migrate(c)
    db.claim_store(c, "nhl")
    c.execute("INSERT INTO raw_responses (raw_id, source, endpoint, request_key, content_hash, byte_size, path, "
              "importer_version, first_retrieved_at) VALUES (1,'test','test','k','h',0,'p','t','2026-10-01T00:00:00Z')")
    for t, a in (("1", "BOS"), ("2", "TOR")):
        c.execute("INSERT INTO teams VALUES (?,?,?,?)", (f"nhl:{t}", a, f"Team {a}", "2026-10-01T00:00:00Z"))
    c.execute("INSERT INTO games (game_id, season, season_type, tipoff_at, home_team_id, away_team_id, neutral_site, status, "
              "first_seen_at, last_raw_id, updated_at) VALUES (?,2027,'regular',?,'nhl:1','nhl:2',0,'scheduled',?,1,?)",
              (GID, START, "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z"))
    c.commit()
    # The real provider for this store (its NHL sealed-decision check needs a full store; this view does not use it).
    monkeypatch.setattr(main.app, "dependency_overrides", {main.get_provider: lambda: SimpleNamespace(
        mode="real", conn=c, sport="nhl", now="2026-10-12T00:00:00Z")})
    yield c
    c.close()


def add(c, name, market, version, generated, p, line=None, source="none"):
    c.execute("INSERT INTO candidate_forecasts (game_id, candidate, model_version, market, line, line_source, line_ref, p_a, "
              "p_mid, p_b, params, inputs, fit_id, artifact_sha256, training_cutoff, dataset_version, tipoff_at, generated_at, "
              "idempotency_key) VALUES (?,?,?,?,?,?,NULL,?,?,?,'{}','{\"features\": \"ft-1\"}',NULL,?,"
              "'2026-10-09T00:00:00Z','ds-1',(SELECT tipoff_at FROM games WHERE game_id=?),?,?)",
              (GID, name, f"research-cand:{name}:{version}", market, line, source, p[0], p[1], p[2], f"sha-{version}", GID,
               generated, f"{name}:{market}:{version}:{generated}"))
    c.commit()


def get(params=None):
    return TestClient(main.app).get(f"/api/research/candidates/game/{GID}", params={"sport": "nhl", **(params or {})})


def test_each_model_version_is_shown_separately_with_its_latest_pregame_row(store):
    add(store, "torch_mlp:nhl_base", "winner", "v1", "2026-10-10T21:30:00Z", (0.60, 0.0, 0.40))
    add(store, "torch_mlp:nhl_base", "winner", "v2", "2026-10-10T22:00:00Z", (0.55, 0.0, 0.45))
    add(store, "torch_mlp:nhl_base", "winner", "v2", "2026-10-10T22:30:00Z", (0.57, 0.0, 0.43))      # newer row, same version
    add(store, "poisson_draw:nhl_base", "spread", "v1", "2026-10-10T22:30:00Z", (0.35, 0.0, 0.65), line=-1.5,
        source="core_cohort:model")
    r = get()
    assert r.status_code == 200
    d = r.json()
    assert d["mode"] == "real" and "Research only" in d["note"]
    rows = {(f["candidate"], f["market"], f["model_version"]): f for f in d["forecasts"]}
    assert set(rows) == {("torch_mlp:nhl_base", "winner", "research-cand:torch_mlp:nhl_base:v1"),
                         ("torch_mlp:nhl_base", "winner", "research-cand:torch_mlp:nhl_base:v2"),
                         ("poisson_draw:nhl_base", "spread", "research-cand:poisson_draw:nhl_base:v1")}
    v2 = rows[("torch_mlp:nhl_base", "winner", "research-cand:torch_mlp:nhl_base:v2")]
    assert v2["generated_at"] == "2026-10-10T22:30:00Z" and v2["sides"][0] == {"label": "BOS win", "p": 0.57}
    assert v2["neural"] is True and v2["family"] == "torch_mlp" and len(v2["sides"]) == 2
    assert v2["research"] is True and v2["canonical"] is False and v2["start_moved"] is False
    sp = rows[("poisson_draw:nhl_base", "spread", "research-cand:poisson_draw:nhl_base:v1")]
    assert sp["line"] == -1.5 and sp["line_source"] == "core_cohort:model" and sp["neural"] is False
    assert [s["label"] for s in sp["sides"]] == ["BOS -1.5", "Push", "TOR +1.5"]
    missing = {m["candidate"]: m for m in d["without_forecast"]}
    assert missing["mlp:nhl_base"]["neural"] is True                 # scikit-learn MLP is a neural network too
    assert missing["logit_l2:nhl_base"]["neural"] is False
    assert "keras_mlp:nhl_base" in missing and "torch_mlp:nhl_base" not in missing
    assert all(m["reason"] == "no pregame forecast recorded for this game" for m in d["without_forecast"])


def test_only_rows_before_the_current_start_are_shown_and_a_moved_start_is_flagged(store):
    add(store, "torch_mlp:nhl_base", "winner", "v1", "2026-10-10T22:30:00Z", (0.6, 0.0, 0.4))
    store.execute("UPDATE games SET tipoff_at='2026-10-11T00:00:00Z' WHERE game_id=?", (GID,))       # start moved later
    store.commit()
    [f] = get().json()["forecasts"]
    assert f["start_moved"] is True and f["start_at_forecast"] == START
    store.execute("UPDATE games SET tipoff_at='2026-10-10T22:00:00Z' WHERE game_id=?", (GID,))       # moved earlier
    store.commit()
    assert get().json()["forecasts"] == []                      # made after the game's current start: not pregame


def test_as_of_hides_later_rows(store):
    add(store, "torch_mlp:nhl_base", "winner", "v1", "2026-10-10T21:30:00Z", (0.6, 0.0, 0.4))
    add(store, "torch_mlp:nhl_base", "winner", "v1", "2026-10-10T22:30:00Z", (0.5, 0.0, 0.5))
    [f] = get({"as_of": "2026-10-10T22:00:00Z"}).json()["forecasts"]
    assert f["generated_at"] == "2026-10-10T21:30:00Z"


def test_unknown_game_and_demo_mode(store, monkeypatch):
    c = TestClient(main.app)
    assert c.get("/api/research/candidates/game/nhl:nope", params={"sport": "nhl"}).status_code == 404
    assert c.get(f"/api/research/candidates/game/{GID}", params={"sport": "nhl", "as_of": "yesterday"}).status_code == 422
    monkeypatch.setenv("NBA_API_MODE", "demo")
    monkeypatch.setattr(main.app, "dependency_overrides", {})
    d = TestClient(main.app).get("/api/research/candidates/game/x").json()
    assert d["mode"] == "demo" and d["forecasts"] == []


def test_a_row_made_after_its_own_start_is_hidden_even_if_the_game_later_moved(store):
    # Review finding 6: the stored-start filter. The insert trigger normally prevents such a row; dropped here to prove the
    # view does not rely on it alone.
    store.execute("DROP TRIGGER candidate_forecasts_two_hour_window")
    add(store, "torch_mlp:nhl_base", "winner", "v1", "2026-10-10T23:30:00Z", (0.6, 0.0, 0.4))   # 30 min after its start
    store.execute("UPDATE games SET tipoff_at='2026-10-11T02:00:00Z' WHERE game_id=?", (GID,))   # game moved later
    store.commit()
    assert get().json()["forecasts"] == []
