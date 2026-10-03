"""tuning-v1 (MODEL_SELECTION §19): season s is tuned on seasons < s−1 against s−1, refitted on seasons < s and predicts s only."""
import random

from app import tuning_study as ts
from app.candidates.features import Row


def rows(seasons=(2018, 2019, 2020, 2021), n=260, seed=2):
    rng = random.Random(seed)
    out = []
    for s in seasons:
        for i in range(n):
            elo = rng.gauss(0, 1)
            hw = rng.random() < 1 / (1 + 2.718 ** (-0.8 * elo))
            margin = int(round(6 * elo + rng.gauss(0, 10)))
            day = f"{s - 1}-11-{1 + i % 28:02d}"
            r = Row(f"g{s}-{i}", s, f"{day}T23:00:00Z", f"{day}T04:00:00Z",
                    {k: (elo if k == "elo_diff" else rng.gauss(0, 1)) for k in ts.features.FEATURE_SETS["nba_base"]},
                    result_available_at=f"{day}T03:00:00Z".replace(f"{day}T03", f"{day}T23:59"[:13]),
                    y={"home_won": hw if margin != 0 else hw, "margin": margin if margin else (1 if hw else -1),
                       "total": 210 + rng.randint(-20, 20), "regulation": None})
            r.result_available_at = f"{s - 1}-12-31T00:00:00Z" if i % 28 < 27 else f"{s - 1}-12-31T00:00:00Z"
            out.append(r)
    return out


def test_tuning_never_sees_the_season_it_predicts(monkeypatch):
    monkeypatch.setattr(ts, "TRIALS", 3)
    monkeypatch.setattr(ts, "MIN_TRAIN", {"nba": 200, "nhl": 200})
    rs = rows()
    res = ts.tuned_walk("nba", "hgb_tuned:nba_base", "hgb", "binary", rs, log=lambda *_: None)
    assert [f["season"] for f in res["fits"]] == [2020, 2021]           # 2019 has no inner validation season
    for s, t in res["trials"].items():
        assert t["validation_season"] == int(s) - 1 and len(t["trials"]) == 3
    assert set(g.split("-")[0] for g in res["outputs"]) == {"g2020", "g2021"}
    flipped = [Row(r.game_id, r.season, r.tipoff_at, r.cutoff_at, r.x, r.result_available_at,
                   dict(r.y, home_won=not r.y["home_won"]) if r.season == 2021 else r.y) for r in rs]
    res2 = ts.tuned_walk("nba", "hgb_tuned:nba_base", "hgb", "binary", flipped, log=lambda *_: None)
    assert res2["trials"]["2021"]["best"] == res["trials"]["2021"]["best"]       # 2021 outcomes never reach its tuning
    assert all(res2["outputs"][g] == res["outputs"][g] for g in res["outputs"] if g.startswith("g2021"))


def test_heads_produce_engine_compatible_outputs(monkeypatch):
    monkeypatch.setattr(ts, "TRIALS", 2)
    monkeypatch.setattr(ts, "MIN_TRAIN", {"nba": 200, "nhl": 200})
    res = ts.tuned_walk("nba", "et_tuned:nba_base:normal", "et", "normal", rows(seasons=(2018, 2019, 2020, 2021, 2022)),
                        log=lambda *_: None)
    o = next(iter(res["outputs"].values()))
    assert o["head"] == "normal" and o["sm"] > 0
    v = ts.engine.vector(o, "total", 210.5)
    assert abs(sum(v) - 1) < 1e-9
