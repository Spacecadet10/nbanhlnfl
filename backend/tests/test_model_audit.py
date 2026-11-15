"""Decision and model-contribution audit helpers (app/model_audit.py). Synthetic data only."""
import random
from datetime import datetime, timedelta, timezone

import pytest

from app import model_audit as ma

FMT = "%Y-%m-%dT%H:%M:%SZ"


def _rows(n=600, seed=1):
    rnd, t0, out = random.Random(seed), datetime(2020, 1, 6, tzinfo=timezone.utc), []
    for i in range(n):
        p = rnd.uniform(0.3, 0.7)
        y = 0 if rnd.random() < p else 2
        noisy = min(0.95, max(0.05, p + rnd.gauss(0, 0.15)))
        out.append({"game_id": f"g{i}", "cutoff_at": (t0 + timedelta(hours=7 * i)).strftime(FMT), "realised": y,
                    "vectors": {"good": (p, 0.0, 1 - p), "same": (p, 0.0, 1 - p), "noisy": (noisy, 0.0, 1 - noisy),
                                "coin": (0.5, 0.0, 0.5)}})
    return out


def test_paired_comparison_finds_the_better_source_and_the_games_needed():
    rows = _rows()
    p = ma._paired("winner", rows, "good", "noisy")
    assert p["verdict"] == "a better" and p["mean"] < 0 and p["games_to_detect"] > 0
    assert ma._paired("winner", rows, "good", "same")["verdict"] == "no detectable difference"
    assert ma._paired("winner", rows[:10], "good", "noisy") is None          # too few games to say anything


def test_overlap_uses_forecasts_not_the_shared_outcome():
    rows = _rows()
    o = ma._residual_corr("winner", rows, ["good", "same", "noisy"])
    assert o["pairs"]["good|same"]["forecast_corr"] == pytest.approx(1.0)
    assert o["pairs"]["good|same"]["pick_disagreement"] == 0
    assert o["pairs"]["good|noisy"]["forecast_corr"] < 0.9
    assert "effective_members" not in o and o["pick_disagreement_range"][0] == 0


def test_two_way_scoring_removes_the_push_and_matched_coverage_ranks_by_confidence():
    assert ma._vec("spread", (0.45, 0.10, 0.45)) == pytest.approx((0.5, 0.0, 0.5))
    assert ma._vec("reg3", (0.4, 0.3, 0.3)) == (0.4, 0.3, 0.3)
    rows = _rows()
    s = ma._summary("winner", rows, "good")
    assert s["games"] == 600 and s["ece"] is not None and 0 <= s["ece"] < 0.1
    cov = ma._coverage("winner", rows, "good")
    assert cov["0.10"]["games"] == 60 and cov["1.00"]["games"] == 600
    assert cov["0.10"]["accuracy"] >= cov["1.00"]["accuracy"]
    # An exact 50/50 forecast is an abstention: not counted as a loss.
    flat = [dict(r, vectors={"coin": (0.5, 0.0, 0.5)}) for r in rows]
    c = ma._coverage("winner", flat, "coin")["1.00"]
    assert c["abstentions"] == 600 and c["picks"] == 0 and c["accuracy"] is None
