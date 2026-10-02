"""Agreement, averaging and learned stacking over probability vectors (docs/MODEL_SELECTION.md §4).

A vector is (p_a, p_mid, p_b): home/over/home-covers, push or regulation draw, away/under/away-covers. Pure functions only.
Agreement is about picks; averaging and stacking produce probabilities. Nothing here reads outcomes except `fit_stack`,
whose callers must pass only chronologically out-of-fold member forecasts.
"""
import math
from itertools import combinations
from typing import Optional, Sequence

THREE_WAY = ("reg3",)
STACK_L2 = 1.0
STACK_FLOOR = 1e-6
SIDES = ("a", "mid", "b")


def sides(market: str) -> tuple[int, ...]:
    """Indices a pick may take: the middle is a pick only in a genuine 3-way market (a regulation draw)."""
    return (0, 1, 2) if market in THREE_WAY else (0, 2)


def pick(vec: Sequence[float], market: str) -> Optional[int]:
    """Index of the most probable allowed side, or None when two allowed sides tie exactly (an abstention)."""
    allowed = sides(market)
    best = max(vec[i] for i in allowed)
    top = [i for i in allowed if abs(vec[i] - best) <= 1e-12]
    return top[0] if len(top) == 1 else None


def average(vecs: Sequence[Sequence[float]]) -> tuple[float, float, float]:
    k = len(vecs)
    out = [sum(v[i] for v in vecs) / k for i in range(3)]
    s = sum(out)
    return tuple(x / s for x in out)  # type: ignore[return-value]


def agreement(members: dict, expected: Sequence[str], market: str) -> dict:
    """Votes of the members present. Majority = more than half of ALL expected members pick the same side; unanimous = every
    expected member present and agreeing. A missing member is not a vote; an exact tie is an abstention."""
    picks = {name: pick(vec, market) for name, vec in members.items()}
    counts = {i: sum(p == i for p in picks.values()) for i in sides(market)}
    top = max(counts.values()) if counts else 0
    leaders = [i for i, c in counts.items() if c == top and c > 0]
    side = leaders[0] if len(leaders) == 1 else None
    complete = all(n in members for n in expected)
    pairs = [{"members": [x, y], "agree": picks[x] is not None and picks[x] == picks[y]}
             for x, y in combinations([n for n in expected if n in members], 2)]
    return {"picks": {n: (None if p is None else SIDES[p]) for n, p in picks.items()},
            "votes": {SIDES[i]: c for i, c in counts.items()}, "abstentions": sum(p is None for p in picks.values()),
            "expected": len(expected), "available": len(members), "complete": complete,
            "side": None if side is None else SIDES[side], "agreeing": top,
            "majority": side is not None and top > len(expected) / 2,
            "unanimous": complete and side is not None and top == len(expected), "pairs": pairs}


# --- Learned stacker: regularised log-linear pooling ----------------------------------------------------------------
def _classes(market: str) -> tuple[int, ...]:
    return (0, 1, 2) if market in THREE_WAY else (0, 2)


def _features(vecs: Sequence[Sequence[float]], market: str) -> list[list[float]]:
    """Per class, log of each member's probability (two-way: conditional on no push)."""
    cls = _classes(market)
    out = []
    for c in cls:
        row = []
        for v in vecs:
            denom = sum(v[j] for j in cls)
            row.append(math.log(max(STACK_FLOOR, v[c] / denom)))
        out.append(row)
    return out


def fit_stack(rows: Sequence[tuple], market: str, l2: float = STACK_L2) -> dict:
    """rows: (member vectors in a fixed order, realised index 0/1/2). Pushes (index 1 in a line market) carry no information
    about the sides and are skipped. Minimises mean log loss + l2/n·(Σ(w − 1/k)² + Σb²); last class bias fixed at 0."""
    import numpy as np
    from scipy.optimize import minimize
    cls = _classes(market)
    data = [(np.asarray(_features(v, market)), cls.index(y)) for v, y in rows if y in cls]
    if not data:
        raise ValueError("no training rows")
    k = data[0][0].shape[1]
    nb = len(cls) - 1
    n = len(data)
    feats = np.stack([d[0] for d in data])          # n × classes × k
    ys = np.asarray([d[1] for d in data])

    def loss(theta):
        w, b = theta[:k], np.concatenate([theta[k:], [0.0]])
        s = feats @ w + b                            # n × classes
        s -= s.max(axis=1, keepdims=True)
        lse = np.log(np.exp(s).sum(axis=1))
        p = np.exp(s - lse[:, None])
        nll = -(s[np.arange(n), ys] - lse).mean()
        onehot = np.zeros_like(p)
        onehot[np.arange(n), ys] = 1
        g_s = (p - onehot) / n                        # n × classes
        g_w = np.einsum("nc,nck->k", g_s, feats) + 2 * l2 / n * (w - 1 / k)
        g_b = g_s.sum(axis=0)[:nb] + 2 * l2 / n * theta[k:]
        pen = l2 / n * (((w - 1 / k) ** 2).sum() + (theta[k:] ** 2).sum())
        return nll + pen, np.concatenate([g_w, g_b])

    theta0 = np.concatenate([np.full(k, 1 / k), np.zeros(nb)])
    res = minimize(loss, theta0, jac=True, method="L-BFGS-B", options={"maxiter": 500})
    return {"market": market, "weights": [float(x) for x in res.x[:k]], "bias": [float(x) for x in res.x[k:]] + [0.0],
            "n": n, "l2": l2, "converged": bool(res.success)}


def stack(model: dict, vecs: Sequence[Sequence[float]]) -> tuple[float, float, float]:
    market = model["market"]
    cls = _classes(market)
    f = _features(vecs, market)
    s = [model["bias"][i] + sum(w * x for w, x in zip(model["weights"], f[i])) for i in range(len(cls))]
    m = max(s)
    e = [math.exp(x - m) for x in s]
    z = sum(e)
    q = [x / z for x in e]
    if market in THREE_WAY:
        return q[0], q[1], q[2]
    mid = sum(v[1] for v in vecs) / len(vecs)      # push probability kept from the members
    return (1 - mid) * q[0], mid, (1 - mid) * q[1]


def log_loss(vec: Sequence[float], realised: int) -> float:
    return -math.log(max(1e-15, vec[realised]))


def brier(vec: Sequence[float], realised: int) -> float:
    return sum((vec[i] - (1.0 if i == realised else 0.0)) ** 2 for i in range(3))
