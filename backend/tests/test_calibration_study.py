"""calibration-v1 (app/calibration_study.py): fits use earlier seasons only; the fits recover known miscalibration."""
import copy
import random
from datetime import datetime, timedelta, timezone

import pytest

from app import calibration_study as cs

FMT = "%Y-%m-%dT%H:%M:%SZ"


def _table(seed=4):
    rnd, rows = random.Random(seed), []
    for season in (2017, 2018, 2019, 2020):
        t0 = datetime(season, 10, 1, tzinfo=timezone.utc)
        for i in range(500):
            p = rnd.uniform(0.3, 0.7)
            y = 0 if rnd.random() < p else 2
            over = 0.5 + 1.8 * (p - 0.5)                       # member "a" is overconfident
            tip = t0 + timedelta(hours=9 * i)
            rows.append({"game_id": f"{season}-{i}", "season": season, "line": 5.5, "realised": y,
                         "cutoff_at": tip.strftime(FMT), "tipoff_at": tip.strftime(FMT),
                         "result_available_at": (tip + timedelta(hours=3)).strftime(FMT),
                         "vectors": {"a": (over, 0.0, 1 - over), "b": (p, 0.0, 1 - p)}})
    return rows


def test_shrink_and_platt_recover_known_overconfidence():
    rnd = random.Random(2)
    pairs = []
    for _ in range(3000):
        p = rnd.uniform(0.3, 0.7)
        q = 0.5 + 1.8 * (p - 0.5)
        pairs.append(((q, 0.0, 1 - q), 0 if rnd.random() < p else 2))
    assert cs._fit("shrink", "total", pairs) == pytest.approx(1 / 1.8, abs=0.06)
    intercept, slope = cs._fit("platt", "total", pairs)
    assert abs(intercept) < 0.1 and 0.4 < slope < 0.7


def test_each_season_is_calibrated_on_earlier_seasons_only():
    base = _table()
    out = cs._market("total", copy.deepcopy(base), ["a", "b"], {"a": 2017, "b": 2017}, [2018, 2019, 2020])
    flipped = copy.deepcopy(base)
    for r in flipped:
        if r["season"] == 2020:
            r["realised"] = 2 - r["realised"]                 # outcomes of the evaluated season changed
    again = cs._market("total", flipped, ["a", "b"], {"a": 2017, "b": 2017}, [2018, 2019, 2020])
    assert out["fits"]["a"]["platt"]["2020"] == again["fits"]["a"]["platt"]["2020"]
    assert out["fits"]["a"]["shrink"]["2020"] == again["fits"]["a"]["shrink"]["2020"]
    assert out["fits"]["a"]["shrink"]["2019"]["params"] < 0.75          # the overconfident member is shrunk
    assert out["member_effects"]["a"]["shrink"]["ci95"][1] < 0          # and that helps it on later games
