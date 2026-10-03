"""Discovery tracking (MODEL_SELECTION §18): every evaluated group is classified from stored predictions (no copies per
threshold), historical and prospective separately, with the study's own confidence definitions and exact-line joins."""
import gzip
import json
import random

import numpy as np

from app import discovery_study as ds, discovery_tracking as dt
from test_pool_selection import CAND, add_candidate, add_cohort, add_game, settle, store  # noqa: F401

GROUP = {"group": f"avg|goals_glm+{CAND}", "method": "avg", "members": ["goals_glm", CAND]}


def write_tables(tmp_path, monkeypatch, rows, registry_rules=()):
    d = tmp_path / "reports" / "discovery-v1"
    d.mkdir(parents=True, exist_ok=True)
    with gzip.open(d / "groups-nhl-spread.json.gz", "wt") as fh:
        json.dump({"rows": rows}, fh)
    (d / "report.json").write_text(json.dumps({"markets": {"nhl:spread": {"prospective_registry": {"rules": list(registry_rules)}}}}))
    monkeypatch.setattr(dt, "discovery_dir", lambda: d)


def hrow(season, level, sel, w, l, matched=0.55):
    return {**GROUP, "season": season, "level": level, "eligible": 1000, "selected": sel, "coverage": sel / 1000, "wins": w,
            "losses": l, "pushes": sel - w - l, "voids": 0, "pending": 0, "win_rate": w / (w + l), "ci95": None,
            "mean_conf": 0.6, "status": "ok", "log_loss": 0.68, "brier": 0.48, "best_prior_single": CAND,
            "best_prior_single_matched": matched}


def test_tracking_uses_exactly_the_study_confidence_definitions():
    rng = random.Random(3)
    for three in (False, True):
        for _ in range(200):
            vecs = []
            for _m in range(3):
                a, b = rng.random(), rng.random() if three else 0.0
                c = rng.random()
                t = a + b + c
                vecs.append([a / t, b / t, c / t])
            rows = [{"game_id": "g", "season": 1, "cutoff_at": "2020-01-01T00:00:00Z", "tipoff_at": "2020-01-01T01:00:00Z",
                     "result_available_at": "x", "line": None, "realised": 0, "vectors": {k: v for k, v in zip("abc", vecs)}}]
            ar = ds.Arrays("reg3" if three else "spread", rows, ["a", "b", "c"])
            for meth in ("avg", "unanimous_min", "majority"):
                s1, c1, _, v1 = ds.side_conf(ar, {}, {"method": meth, "members": ["a", "b", "c"]})
                s2, c2, v2 = dt.side_conf(meth, vecs, three)
                assert (s1[0] if s1[0] >= 0 else None) == s2
                if s2 is not None:
                    assert abs(c1[0] - c2) < 1e-9 and (v1 is None or v1[0] == v2)


def test_historical_pools_seasons_marks_registered_and_sparse(tmp_path, monkeypatch):
    write_tables(tmp_path, monkeypatch, [hrow(2024, ">=0.60", 200, 120, 70), hrow(2025, ">=0.60", 100, 55, 40),
                                         hrow(2025, "[0.900,1.000)", 30, 20, 10)],
                 [{"group": GROUP["group"], "threshold": 0.6, "why": "best lift at ≥0.60"}])
    h = dt.historical("nhl", "spread")
    g = next(x for x in h["groups"] if x["level"] == ">=0.60")
    assert (g["selected"], g["wins"], g["losses"], g["pushes"]) == (300, 175, 110, 15) and g["registered"] == "best lift at ≥0.60"
    assert abs(g["win_rate"] - 175 / 285) < 1e-12 and g["status"] == "ok" and set(g["seasons"]) == {"2024", "2025"}
    band = next(x for x in h["groups"] if x["level"].startswith("[0.900"))
    assert band["status"] == "insufficient evidence" and band["registered"] is None
    assert dt.historical("nhl", "spread", models=["hockey_elo"])["groups"] == []


def test_prospective_classifies_stored_cohorts_at_their_exact_lines(store, tmp_path, monkeypatch):  # noqa: F811
    write_tables(tmp_path, monkeypatch, [hrow(2025, ">=0.60", 200, 120, 70)])
    # game 1: settled, home covers -1.5 (4-1); goals_glm and the candidate both favour home → avg 0.7
    add_game(store, "nhl:1", "2026-10-10T23:00:00Z")
    c1 = add_cohort(store, "nhl:1", "2026-10-10T23:00:00Z", "2026-10-10T22:00:00Z", members_p=(0.7, 0.0, 0.3))
    add_candidate(store, CAND, "nhl:1", "2026-10-10T23:00:00Z", "2026-10-10T22:01:00Z", (0.7, 0.0, 0.3), ref=f"research_cohorts:{c1}")
    settle(store, "nhl:1", "2026-10-10T23:00:00Z", 4, 1)
    # game 2: pending, different exact line; a candidate row for ANOTHER cohort must not join
    add_game(store, "nhl:2", "2026-10-11T23:00:00Z")
    c2 = add_cohort(store, "nhl:2", "2026-10-11T23:00:00Z", "2026-10-11T22:00:00Z", line=1.5, members_p=(0.8, 0.0, 0.2))
    add_candidate(store, CAND, "nhl:2", "2026-10-11T23:00:00Z", "2026-10-11T22:01:00Z", (0.8, 0.0, 0.2), line=1.5,
                  ref=f"research_cohorts:{c2}")
    add_game(store, "nhl:3", "2026-10-11T23:30:00Z")
    add_cohort(store, "nhl:3", "2026-10-11T23:30:00Z", "2026-10-11T22:30:00Z", members_p=(0.9, 0.0, 0.1))
    add_candidate(store, CAND, "nhl:3", "2026-10-11T23:30:00Z", "2026-10-11T22:31:00Z", (0.9, 0.0, 0.1), ref="research_cohorts:999")
    store.commit()
    p = dt.prospective(store, "nhl", "spread", "2026-10-12T12:00:00Z", level=">=0.60")
    (g,) = p["groups"]
    assert g["eligible"] == 2 and g["selected"] == 2 and g["wins"] == 1 and g["pending"] == 1     # game 3 has no joined candidate
    assert g["lines"] == {"-1.5": 1, "1.5": 1} and g["line_label"].startswith("across exact lines")
    assert g["status"] == "insufficient evidence"
    before = store.execute("SELECT count(*) FROM candidate_forecasts").fetchone()[0]
    dt.prospective(store, "nhl", "spread", "2026-10-12T12:00:00Z")
    assert store.execute("SELECT count(*) FROM candidate_forecasts").fetchone()[0] == before       # read-only: no copies
    early = dt.prospective(store, "nhl", "spread", "2026-10-10T22:30:00Z", level=">=0.60")["groups"][0]
    assert early["selected"] == 1 and early["wins"] == 0 and early["pending"] == 1                   # outcome not known then


def test_study_band_and_threshold_levels_match_tracking():
    assert [x[0] for x in dt.levels("reg3")][:8] == [f">={t:.2f}" for t in ds.REG3_T]
    assert dt.TWO_WAY_BANDS == ds.TWO_WAY_BANDS and dt.REG3_BANDS == ds.REG3_BANDS
    assert np.isclose(ds.REG3_BANDS[0][0], 1 / 3)


def test_frontend_types_match_the_tracking_rows(tmp_path, monkeypatch):
    from test_research_contract import fields
    write_tables(tmp_path, monkeypatch, [hrow(2025, ">=0.60", 200, 120, 70)])
    g = dt.historical("nhl", "spread")["groups"][0]
    optional = {"mean_conf", "log_loss", "brier", "best_prior_single_matched", "seasons", "lines", "line_label"}
    assert fields("DiscoveryGroup") - optional <= set(g)


def test_registered_only_counts_the_registered_groups(store, tmp_path, monkeypatch):  # noqa: F811
    write_tables(tmp_path, monkeypatch, [hrow(2025, ">=0.60", 200, 120, 70), hrow(2025, ">=0.65", 100, 60, 35)],
                 [{"group": GROUP["group"], "threshold": 0.65, "why": "top point lift"}])
    out = dt.report(store, "nhl", "spread", "2026-10-12T12:00:00Z", registered_only=True)
    assert out["historical"]["groups_matched"] == 1 and out["historical"]["groups"][0]["level"] == ">=0.65"
