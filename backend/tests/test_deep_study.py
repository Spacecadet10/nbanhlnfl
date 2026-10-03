"""deep-v1 (MODEL_SELECTION §22): PyTorch and Keras networks fitted for season s on earlier seasons only; the latest
earlier season chooses the number of epochs. Synthetic rows; needs the `deep` extra (torch, tensorflow)."""
import random

import numpy as np
import pytest

from app import deep_study as dl
from app.candidates.features import Row
from test_tuning_study import rows


@pytest.fixture(autouse=True)
def small(monkeypatch):
    monkeypatch.setattr(dl, "MIN_TRAIN", {"nba": 200, "nhl": 200})
    monkeypatch.setattr(dl, "SEEDS", (1, 2))
    monkeypatch.setattr(dl, "MAX_EPOCHS", 12)
    monkeypatch.setattr(dl, "PATIENCE", 3)


def walk(lib, head="binary", rs=None, sport="nba", name="net"):
    return dl.deep_walk(sport, name, lib, head, rs or rows(), log=lambda *_: None)


@pytest.mark.parametrize("lib", ["torch", "keras"])
def test_a_network_never_sees_the_season_it_predicts_and_is_reproducible(lib):
    rs = rows()
    res = walk(lib, rs=rs)
    assert [f["season"] for f in res["fits"]] == [2020, 2021]            # 2019 has no earlier validation season
    assert [f["validation_season"] for f in res["fits"]] == [2019, 2020]
    assert all(1 <= f["epochs"] <= 12 for f in res["fits"])
    assert set(g.split("-")[0] for g in res["outputs"]) == {"g2020", "g2021"}
    assert all(0 < o["p"] < 1 for o in res["outputs"].values())
    again = walk(lib, rs=rs)
    assert all(again["outputs"][g]["p"] == pytest.approx(o["p"], abs=1e-6) for g, o in res["outputs"].items())   # fixed seeds
    flipped = [Row(r.game_id, r.season, r.tipoff_at, r.cutoff_at, r.x, r.result_available_at,
                   dict(r.y, home_won=not r.y["home_won"]) if r.season == 2021 else r.y) for r in rs]
    res2 = walk(lib, rs=flipped)
    assert res2["fits"][1]["epochs"] == res["fits"][1]["epochs"]         # 2021 outcomes reach neither its epochs nor its fit
    assert all(res2["outputs"][g]["p"] == pytest.approx(o["p"], abs=1e-6) for g, o in res["outputs"].items() if g.startswith("g2021"))


def test_the_torch_network_learns_a_real_signal():
    res = walk("torch")
    by = {r.game_id: r for r in rows()}
    ps = np.array([o["p"] for g, o in res["outputs"].items()])
    ys = np.array([by[g].y["home_won"] for g in res["outputs"]])
    assert ((ps >= 0.5) == ys).mean() > 0.58                              # the synthetic home edge is driven by elo_diff
    assert -np.mean(np.where(ys, np.log(ps), np.log(1 - ps))) < 0.69      # better than always saying 50%


def test_score_heads_produce_engine_compatible_outputs():
    res = walk("torch", "normal", rs=rows(seasons=(2018, 2019, 2020, 2021, 2022)), name="net:normal")
    o = next(iter(res["outputs"].values()))
    assert o["head"] == "normal" and o["sm"] > 0
    assert abs(sum(dl.engine.vector(o, "total", 210.5)) - 1) < 1e-9
    assert set(res["fits"][0]["epochs"]) == {"margin", "total"}


def test_goals_head_fits_poisson_rates_on_regulation_scores():
    rng = random.Random(4)
    rs = []
    for s in (2018, 2019, 2020, 2021):
        for i in range(260):
            x = {k: rng.gauss(0, 1) for k in dl.features.FEATURE_SETS["nhl_base"]}
            x["neutral"] = 0.0
            lam_h, lam_a = np.exp(1.0 + 0.2 * x["elo_diff"]), np.exp(0.9 - 0.2 * x["elo_diff"])
            h, a = int(np.random.default_rng(1000 * s + i).poisson(lam_h)), int(np.random.default_rng(7 + 1000 * s + i).poisson(lam_a))
            won = h > a if h != a else rng.random() < 0.5
            day = f"{s - 1}-11-{1 + i % 28:02d}"
            rs.append(Row(f"g{s}-{i}", s, f"{day}T23:00:00Z", f"{day}T04:00:00Z", x,
                          result_available_at=f"{s - 1}-12-31T00:00:00Z",
                          y={"home_won": won, "margin": (h - a) or (1 if won else -1), "total": h + a + (h == a),
                             "regulation": (h, a)}))
    res = walk("torch", "goals", rs=rs, sport="nhl", name="net:goals")
    o = next(iter(res["outputs"].values()))
    assert o["head"] == "goals" and o["lh"] > 0 and o["la"] > 0 and 0 <= o["w"] <= 1
    for market, line in (("winner", None), ("reg3", None), ("total", 5.5), ("spread", -1.5)):
        assert abs(sum(dl.engine.vector(o, market, line)) - 1) < 1e-6
    assert set(res["fits"][0]["epochs"]) == {"home", "away"}


def test_keras_is_registered_for_the_winner_head_only():
    with pytest.raises(ValueError):
        dl.KerasNet("reg", 1)
    assert {lib for cfg in dl.CONFIGS.values() for _, lib, _ in cfg} == {"torch", "keras"}
    assert all(len(cfg) <= 4 for cfg in dl.CONFIGS.values())
