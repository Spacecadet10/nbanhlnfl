"""Plan B2/B4/M6 proposed regression tests (synthetic data only), run on snap3 from a scratch copy. Expected to FAIL."""
import json
import sys
sys.path.insert(0, "/private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/9adf6978-28aa-417d-9e17-e71bdb15143a/scratchpad/snap3/backend/tests")

import pytest  # noqa: E402

from app import nfl_eval, nhl_shots, threshold_overview as to  # noqa: E402
from test_nfl_eval import store, _test_store, _frozen, _log  # noqa: E402,F401
from test_nhl_eval import nhl_store  # noqa: E402,F401


def _row(t, n, w, lo):
    return {"threshold": t, "graded": n, "win": w, "loss": n - w, "win_rate": w / n, "ci95": [lo, 0.99], "stated": t}


def test_b2_held_up_on_reused_reserved_seasons_is_not_reported_as_confirmation(monkeypatch):
    earlier = [_row(0.5, 3000, 2000, 0.65), _row(0.8, 300, 270, 0.86)]
    later = [_row(0.5, 2000, 1300, 0.63), _row(0.8, 200, 180, 0.85)]
    hist = {"status": "available", "model": "elo", "reason": None,
            "earlier": {"seasons": [2022, 2023, 2024], "rows": earlier},
            "later": {"seasons": [2025, 2026], "rows": later}}
    monkeypatch.setattr(to, "history", lambda *a, **k: hist)
    block = to.market_block("nba", "winner", "Winner", [], (0.5, 0.8), lambda g, c: None)
    v = block["verdict"]
    assert v["best"] == 0.8
    assert v["held_up"] is None                                   # snap3: True (reused test seasons shown as confirmation)
    assert v["later_check"]["status"] == "reused_reserved_test"   # snap3: KeyError


def test_m6_a_second_seal_cannot_run_the_reserved_test(store, tmp_path):  # noqa: F811
    _test_store(store)
    log = tmp_path / "log" / "access.jsonl"
    nfl_eval.run_test(store, _frozen(store), access_log=log, out_dir=tmp_path / "out")
    other = _frozen(store, config={"k": 25, "home_advantage": 50, "carry": 0.5, "mov": True})   # a re-chosen model
    with pytest.raises(nfl_eval.TestRunAlreadyDone):              # snap3: runs again (per-seal guard)
        nfl_eval.run_test(store, other, access_log=log, out_dir=tmp_path / "out2")
    assert len(_log(log)) == 2


def test_b4_late_pbp_of_an_observed_final_is_available_only_from_its_retrieval(nhl_store, monkeypatch):  # noqa: F811
    from app.sources import nhl_pbp
    monkeypatch.setattr(nhl_pbp, "parse", lambda body: (True, []))
    g = dict(nhl_store.execute("SELECT * FROM games WHERE season=2026 AND status='final' ORDER BY tipoff_at LIMIT 1").fetchone())
    g["result_availability_basis"] = "observed_retrieval"            # as a live-era game first seen final by the live job
    g["result_available_at"] = g["tipoff_at"][:11] + "23:59:00Z"
    late = "2027-01-01T00:00:00Z"                                    # retrieved long after the 24 h window
    nhl_shots.import_game(nhl_store, g, b"{}", late)
    (avail,) = nhl_store.execute("SELECT available_at FROM nhl_pbp_imports WHERE game_id=?", (g["game_id"],)).fetchone()
    assert avail == late                                             # snap3: the result time (back-dated)
