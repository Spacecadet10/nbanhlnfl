"""Shared guards and statistics of the three frozen evaluations (docs/preregistration/*-2026-11-18.md).

Pure helpers: no store is opened here. Every evaluator reads only a COPY of the stores (`--data-dir`), never the live data
folder or the live delivery ledger, writes its report outside the stores, and refuses to run when its frozen document's bytes
differ from the sha256 hard-coded in the evaluator.
"""
import hashlib
import json
import math
import os
import time
from pathlib import Path
from statistics import NormalDist

import numpy as np

LIVE_DATA = Path("/Users/andry/Desktop/nbanhlnfl/data")
LIVE_LEDGER = Path("/Users/andry/Desktop/nbanhlnfl/.telegram-delivery.sqlite")
REPO_ROOT = Path(__file__).resolve().parents[2]
SEED = 20261118
BOOTSTRAP_B = 10_000
_ND = NormalDist()


class Refused(RuntimeError):
    """The evaluator refuses to run (guard, hash, or a stop rule that is not reached)."""


def _inside(path: Path, parent: Path) -> bool:
    p, q = path.resolve(), parent.resolve()
    return p == q or q in p.parents


def guard_data_dir(path) -> Path:
    """The resolved copy folder, or Refused when it is (or lies inside) the live data folder or a protected one."""
    from . import config
    p = Path(path).expanduser().resolve()
    if _inside(p, LIVE_DATA) or config.protected_path(p):
        raise Refused(f"{p} is a live or protected data folder: evaluators read a COPY only")
    if not p.is_dir():
        raise Refused(f"{p} is not a folder")
    return p


def guard_file(path) -> Path:
    """A copy of a store file (the delivery ledger), refused when it is the live ledger or lies in the live data folder."""
    from . import config
    p = Path(path).expanduser().resolve()
    if p == LIVE_LEDGER.resolve() or _inside(p, LIVE_DATA) or config.protected_path(p.parent):
        raise Refused(f"{p} is a live store: evaluators read a COPY only")
    if not p.is_file():
        raise Refused(f"{p} is not a file")
    return p


def guard_out(out, data_dir) -> Path:
    """The report folder: never inside the copied stores, the live data folder or a protected folder."""
    from . import config
    o = Path(out).expanduser().resolve()
    if _inside(o, Path(data_dir)) or _inside(o, LIVE_DATA) or config.protected_path(o):
        raise Refused(f"{o} lies inside a data folder: reports are written outside the stores")
    o.mkdir(parents=True, exist_ok=True)
    return o


def check_prereg(name: str, sha256: str, root: Path = REPO_ROOT) -> None:
    """Refuses unless the committed document's bytes hash to `sha256`."""
    path = Path(root) / "docs" / "preregistration" / name
    try:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise Refused(f"frozen document {name} unreadable: {exc}") from None
    if actual != sha256:
        raise Refused(f"frozen document {name} has sha256 {actual}, expected {sha256}: refusing to run")


LOOKS_DIR = Path.home() / "nba-predictor-research-data" / "prereg-looks"


def check_addenda(addenda: dict, root: Path = REPO_ROOT) -> None:
    """Refuses unless every committed addendum file (name -> sha256) is byte-identical to its pin."""
    for name, sha in addenda.items():
        try:
            check_prereg(name, sha, root)
        except Refused as exc:
            raise Refused(f"addendum: {exc}") from None


def look_once(looks_dir, doc_sha256: str, key: str, compute, storable=lambda r: r.get("status") == "look", lock_timeout: float = 30.0) -> dict:
    """One look per (frozen document sha256, key): a stored look is returned verbatim and never recomputed, whatever the
    report folder (--out) of this run. Only a result `storable` says is the look is stored; counts-only reports rerun."""
    d = Path(looks_dir if looks_dir is not None else LOOKS_DIR).expanduser().resolve()
    if _inside(d, LIVE_DATA):
        raise Refused(f"{d} lies inside the live data folder: looks are stored outside the stores")
    path = d / f"{doc_sha256}-looks.json"
    d.mkdir(parents=True, exist_ok=True)
    lock, deadline = Path(f"{path}.lock"), time.monotonic() + lock_timeout
    while True:                                   # O_EXCL lock: a concurrent second writer waits, then refuses
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise Refused(f"look store {path} is locked by another run ({lock}): refusing") from None
            time.sleep(0.05)
    try:
        stored = json.loads(path.read_text()) if path.is_file() else {}
        if key in stored:
            return stored[key]
        result = compute()
        if storable(result):
            stored[key] = json.loads(json.dumps(result, default=str))
            tmp = Path(f"{path}.tmp")
            tmp.write_text(json.dumps(stored, indent=2, sort_keys=True) + "\n")
            os.replace(tmp, path)
        return result
    finally:
        os.unlink(lock)


def write_report(out: Path, name: str, report: dict) -> Path:
    path = Path(out) / name
    path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    return path


# --- statistics ---------------------------------------------------------------------------------------------------------
def norm_cdf(x: float) -> float:
    return _ND.cdf(x)


def norm_ppf(p: float) -> float:
    return _ND.inv_cdf(p)


def logit(p: float) -> float:
    return math.log(p / (1 - p))


def wilson(k: int, n: int, z: float = 1.959963984540054):
    """Wilson score interval (lo, hi) of k successes in n; (None, None) for n = 0."""
    if n <= 0:
        return None, None
    ph = k / n
    d = 1 + z * z / n
    centre = (ph + z * z / (2 * n)) / d
    half = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / d
    return centre - half, centre + half


def holm(pvalues: dict, alpha: float = 0.05) -> dict:
    """Holm step-down over the whole family: name -> {p, threshold, reject}. The i-th smallest p (i = 1..m) is rejected
    while p <= alpha / (m - i + 1); the first failure stops every later one."""
    m = len(pvalues)
    out, stopped = {}, False
    for i, (name, p) in enumerate(sorted(pvalues.items(), key=lambda kv: (kv[1], kv[0])), 1):
        thr = alpha / (m - i + 1)
        ok = (not stopped) and p <= thr
        stopped = stopped or not ok
        out[name] = {"p": p, "threshold": thr, "reject": ok}
    return out


def bonferroni(pvalues: dict, alpha: float = 0.05, m=None) -> dict:
    """name -> {p, threshold = alpha / m, reject}; m defaults to the number of p-values (pass the family size when
    some hypotheses could not be tested yet: every registered hypothesis is counted)."""
    m = m or len(pvalues)
    return {k: {"p": p, "threshold": alpha / m, "reject": p <= alpha / m} for k, p in pvalues.items()}


def _resample(n_clusters: int, B: int, seed: int) -> np.ndarray:
    """Cluster indices of every replicate: default_rng(seed).integers(0, D, size=(B, D)), replicate-major."""
    return np.random.default_rng(seed).integers(0, n_clusters, size=(B, n_clusters))


def boot_ratio(num, den, B: int = BOOTSTRAP_B, seed: int = SEED) -> np.ndarray:
    """Cluster bootstrap of a pooled mean: per-cluster numerator and denominator sums, clusters resampled with replacement;
    each replicate is sum(num*)/sum(den*). A replicate whose denominator is 0 is 0.0 (counted as not better)."""
    num, den = np.asarray(num, float), np.asarray(den, float)
    idx = _resample(len(num), B, seed)
    n, d = num[idx].sum(axis=1), den[idx].sum(axis=1)
    return np.where(d > 0, n / np.where(d > 0, d, 1), 0.0)


def boot_slope(n, sx, sy, sxy, sxx, B: int = BOOTSTRAP_B, seed: int = SEED) -> np.ndarray:
    """Cluster bootstrap of the pooled OLS slope of y on x from per-cluster sufficient statistics (count, sum x, sum y,
    sum xy, sum xx). A degenerate replicate (no x variance) is 0.0."""
    idx = _resample(len(n), B, seed)
    N, X, Y, XY, XX = (np.asarray(a, float)[idx].sum(axis=1) for a in (n, sx, sy, sxy, sxx))
    sxx_c = XX - X * X / np.where(N > 0, N, 1)
    sxy_c = XY - X * Y / np.where(N > 0, N, 1)
    return np.where(sxx_c > 1e-12, sxy_c / np.where(sxx_c > 1e-12, sxx_c, 1), 0.0)


def p_le_zero(thetas) -> float:
    """One-sided p = (1 + #{theta* <= 0}) / (B + 1)."""
    t = np.asarray(thetas)
    return float((1 + int((t <= 0).sum())) / (len(t) + 1))


def p_ge_zero(thetas) -> float:
    """One-sided p = (1 + #{theta* >= 0}) / (B + 1)."""
    t = np.asarray(thetas)
    return float((1 + int((t >= 0).sum())) / (len(t) + 1))


def ci95(thetas):
    lo, hi = np.percentile(np.asarray(thetas), [2.5, 97.5])
    return float(lo), float(hi)


def slope(xs, ys):
    """Pooled OLS slope of y on x; None without x variance."""
    x, y = np.asarray(xs, float), np.asarray(ys, float)
    sxx = float(((x - x.mean()) ** 2).sum()) if len(x) else 0.0
    return None if sxx <= 1e-12 else float(((x - x.mean()) * (y - y.mean())).sum() / sxx)


def clip_p(p: float, eps: float = 1e-6) -> float:
    return min(max(p, eps), 1 - eps)


def log_loss(p: float, y) -> float:
    p = clip_p(p)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def brier(p: float, y) -> float:
    return (p - y) ** 2


def logistic_fit(ps, ys, ws=None):
    """Weighted logistic regression of y on logit(p): (intercept, slope), or (None, None) without variance in logit(p) or when
    the fit diverges."""
    x = np.array([logit(clip_p(p)) for p in ps], float)
    y = np.array(ys, float)
    w = np.ones(len(x)) if ws is None else np.array(ws, float)
    if len(x) == 0 or w.sum() <= 0:
        return None, None
    xm = float((w * x).sum() / w.sum())
    if float((w * (x - xm) ** 2).sum()) < 1e-12:
        return None, None
    X = np.column_stack([np.ones(len(x)), x])
    beta = np.zeros(2)
    for _ in range(100):
        mu = 1 / (1 + np.exp(-X @ beta))
        try:
            step = np.linalg.solve(X.T @ (X * (w * mu * (1 - mu))[:, None]) + 1e-12 * np.eye(2), X.T @ (w * (y - mu)))
        except np.linalg.LinAlgError:
            return None, None
        beta = beta + step
        if np.max(np.abs(step)) < 1e-10:
            break
    if not np.all(np.isfinite(beta)) or np.max(np.abs(beta)) > 50:
        return None, None
    return float(beta[0]), float(beta[1])
