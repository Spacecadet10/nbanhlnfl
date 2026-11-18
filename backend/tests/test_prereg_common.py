"""Shared guards and statistics of the frozen evaluations: hand-computed expected values, synthetic data only."""
import hashlib

import numpy as np
import pytest

from app import prereg_common as pc


def test_wilson_five_of_ten():
    lo, hi = pc.wilson(5, 10)
    assert lo == pytest.approx(0.2366, abs=5e-4) and hi == pytest.approx(0.7634, abs=5e-4)
    assert pc.wilson(0, 0) == (None, None)


def test_holm_stops_at_first_failure():
    out = pc.holm({"a": 0.01, "b": 0.04, "c": 0.03}, 0.05)
    assert out["a"]["reject"] and out["a"]["threshold"] == pytest.approx(0.05 / 3)
    assert not out["c"]["reject"] and out["c"]["threshold"] == pytest.approx(0.025)
    assert not out["b"]["reject"]              # after the first failure nothing else is rejected
    two = pc.holm({"x": 0.02, "y": 0.04}, 0.05)
    assert two["x"]["reject"] and two["y"]["reject"] and two["y"]["threshold"] == 0.05


def test_bonferroni_family_size_counts_untested_hypotheses():
    out = pc.bonferroni({"h": 0.006}, 0.05, m=8)
    assert out["h"]["threshold"] == pytest.approx(0.00625) and out["h"]["reject"]
    assert not pc.bonferroni({"h": 0.007}, 0.05, m=8)["h"]["reject"]
    assert pc.bonferroni({"a": 0.02, "b": 0.03}, 0.05)["b"]["reject"] is False   # 0.05/2 = 0.025


def test_one_sided_p_and_ci():
    t = np.array([-1.0, 0.0, 1.0, 2.0])
    assert pc.p_le_zero(t) == pytest.approx(3 / 5)      # -1 and 0 are <= 0
    assert pc.p_ge_zero(t) == pytest.approx(4 / 5)      # 0, 1, 2 are >= 0
    lo, hi = pc.ci95(np.arange(101.0))
    assert (lo, hi) == (pytest.approx(2.5), pytest.approx(97.5))


def test_boot_ratio_single_cluster_is_constant_and_zero_denominator_is_zero():
    t = pc.boot_ratio([6.0], [3.0], B=50)
    assert set(t.round(12)) == {2.0}
    assert set(pc.boot_ratio([0.0], [0.0], B=5)) == {0.0}


def test_boot_ratio_determinism_and_draw_order():
    num, den = [1.0, 3.0, 8.0], [1.0, 1.0, 2.0]
    a, b = pc.boot_ratio(num, den, B=200), pc.boot_ratio(num, den, B=200)
    assert (a == b).all()
    assert not (a == pc.boot_ratio(num, den, B=200, seed=1)).all()
    rng = np.random.default_rng(20261118)      # replicate-major draws of D cluster indices each
    for r in range(5):
        idx = rng.integers(0, 3, size=3)
        assert a[r] == pytest.approx(sum(num[i] for i in idx) / sum(den[i] for i in idx))


def test_boot_slope_matches_ols_on_one_cluster():
    x, y = [0.0, 1.0, 2.0], [0.0, 2.0, 4.0]
    t = pc.boot_slope([3], [sum(x)], [sum(y)], [sum(a * b for a, b in zip(x, y))], [sum(a * a for a in x)], B=10)
    assert set(t.round(12)) == {2.0}
    assert pc.slope(x, y) == pytest.approx(2.0) and pc.slope([1, 1], [0, 1]) is None
    flat = pc.boot_slope([2], [2.0], [1.0], [1.0], [2.0], B=3)       # x = (1, 1): no variance
    assert set(flat) == {0.0}


def test_log_loss_clips():
    assert pc.log_loss(0.8, 1) == pytest.approx(0.22314355131)
    assert pc.log_loss(0.0, 1) == pytest.approx(13.815510558, rel=1e-6)
    assert pc.brier(0.8, 1) == pytest.approx(0.04)


def test_guards(tmp_path):
    with pytest.raises(pc.Refused):
        pc.guard_data_dir(pc.LIVE_DATA)
    with pytest.raises(pc.Refused):
        pc.guard_data_dir(pc.LIVE_DATA / "research")
    assert pc.guard_data_dir(tmp_path) == tmp_path.resolve()
    with pytest.raises(pc.Refused):
        pc.guard_file(pc.LIVE_LEDGER)
    f = tmp_path / "l.sqlite"
    f.write_bytes(b"x")
    assert pc.guard_file(f) == f.resolve()
    with pytest.raises(pc.Refused):
        pc.guard_out(tmp_path / "inside", tmp_path)
    assert pc.guard_out(tmp_path.parent / (tmp_path.name + "-out"), tmp_path).is_dir()


def test_check_prereg(tmp_path):
    d = tmp_path / "docs" / "preregistration"
    d.mkdir(parents=True)
    (d / "x.md").write_bytes(b"frozen")
    sha = hashlib.sha256(b"frozen").hexdigest()
    pc.check_prereg("x.md", sha, tmp_path)
    (d / "x.md").write_bytes(b"frozen!")
    with pytest.raises(pc.Refused):
        pc.check_prereg("x.md", sha, tmp_path)
    with pytest.raises(pc.Refused):
        pc.check_prereg("missing.md", sha, tmp_path)


def test_logistic_fit_weighted_and_degenerate():
    ps = [0.2] * 10 + [0.8] * 10
    ys = [1] * 2 + [0] * 8 + [1] * 8 + [0] * 2
    a, b = pc.logistic_fit(ps, ys)
    assert a == pytest.approx(0.0, abs=1e-6) and b == pytest.approx(1.0, abs=1e-6)
    # a weight constant within each group leaves the saturated fit unchanged
    a2, b2 = pc.logistic_fit(ps, ys, [0.5] * 10 + [1.0] * 10)
    assert b2 == pytest.approx(1.0, abs=1e-6)
    assert pc.logistic_fit([0.6, 0.6], [1, 0]) == (None, None) and pc.logistic_fit([], []) == (None, None)


def test_look_once_is_keyed_to_document_sha_and_key_not_to_a_report_folder(tmp_path):
    looks = tmp_path / "looks"
    first = pc.look_once(looks, "a" * 64, "m", lambda: {"status": "look", "v": 1})
    again = pc.look_once(looks, "a" * 64, "m", lambda: pytest.fail("a stored look is never recomputed"))
    assert again == first == {"status": "look", "v": 1}
    other = pc.look_once(looks, "b" * 64, "m", lambda: {"status": "look", "v": 2})      # another document: its own look
    assert other["v"] == 2
    counts = pc.look_once(looks, "a" * 64, "n", lambda: {"status": "waiting"})           # counts-only results are not stored
    assert counts == {"status": "waiting"} and pc.look_once(looks, "a" * 64, "n", lambda: {"status": "look", "v": 3})["v"] == 3


def test_look_store_refuses_the_live_data_folder():
    with pytest.raises(pc.Refused):
        pc.look_once(pc.LIVE_DATA / "x", "a" * 64, "m", lambda: {"status": "look"})


def test_check_addenda_refuses_changed_bytes(tmp_path):
    d = tmp_path / "docs" / "preregistration"
    d.mkdir(parents=True)
    (d / "a.md").write_bytes(b"x")
    pc.check_addenda({"a.md": hashlib.sha256(b"x").hexdigest()}, tmp_path)
    with pytest.raises(pc.Refused, match="addendum"):
        pc.check_addenda({"a.md": "0" * 64}, tmp_path)


def test_boot_slope_mixed_sign_matches_an_independent_loop():
    # four clusters of (x, y) points with a mixed-sign slope; B = 20 draws of default_rng(20261118).integers(0, 4, (20, 4))
    clusters = [[(1.0, 0.2), (2.0, 0.1)], [(1.0, 0.0), (3.0, 0.4)], [(2.0, 0.5), (4.0, 0.3)], [(1.0, 0.3), (2.0, 0.1)]]
    st = [(len(c), sum(x for x, _ in c), sum(y for _, y in c), sum(x * y for x, y in c), sum(x * x for x, _ in c)) for c in clusters]
    th = pc.boot_slope(*[np.array(a, float) for a in zip(*st)], B=20)
    idx = np.random.default_rng(pc.SEED).integers(0, 4, size=(20, 4))
    expect = []
    for rep in idx:
        pts = [pt for i in rep for pt in clusters[i]]
        xs, ys = np.array([a for a, _ in pts]), np.array([b for _, b in pts])
        expect.append(0.0 if xs.var() < 1e-12 else float(np.polyfit(xs, ys, 1)[0]))
    assert th == pytest.approx(expect, abs=1e-12)
    assert any(t > 0 for t in th) and any(t < 0 for t in th)
