"""Learner families for the candidate pool (docs/CANDIDATE_POOL.md §2). Every setting is declared here; none is searched.

Heads:
- `binary`: P(home wins) for the winner market.
- `normal`: NBA margin and total means with per-game or residual standard deviations (markets.MarginTotalNormal).
- `reg3`: NHL regulation home / draw / away (multinomial), winner via the training overtime share.
- `goals`: NHL regulation goal rates with a diagonally inflated joint score (markets.ScoreGrid; every NHL market).
"""
import math

SEED = 20261002
MIN_SD = 1.0
DRAW_GRID = tuple(round(0.005 * i, 3) for i in range(61))

SETTINGS = {
    "logit_l2": {"C": 1.0, "max_iter": 2000},
    "extra_trees": {"n_estimators": 300, "min_samples_leaf": 50, "max_features": 0.5, "random_state": SEED, "n_jobs": -1},
    "random_forest": {"n_estimators": 300, "min_samples_leaf": 50, "max_features": 0.5, "random_state": SEED, "n_jobs": -1},
    "mlp": {"hidden_layer_sizes": (16,), "alpha": 1.0, "max_iter": 400, "learning_rate_init": 0.001, "seeds": 5},
    "ridge": {"alpha": 10.0},
    "elastic_net": {"alpha": 0.05, "l1_ratio": 0.5, "max_iter": 5000},
    # Seeded base tree (the default base learner is unseeded, so `random_state` alone is not reproducible) and chronological
    # early stopping on the latest training season (the built-in split is random); predictions use the best iteration.
    "ngboost": {"n_estimators": 1000, "learning_rate": 0.02, "minibatch_frac": 1.0, "random_state": SEED,
                "base_max_depth": 3, "early_stopping_rounds": 50},
    "hetero_linear": {"l2": 1.0},
    "multinomial_l2": {"C": 1.0, "max_iter": 2000},
    "poisson_draw": {"alpha": 1.0, "max_iter": 1000},
}


def _np():
    import numpy as np
    return np


def _scale(X):
    np = _np()
    X = np.asarray(X, dtype=float)
    mu, sd = X.mean(axis=0), X.std(axis=0)
    sd[sd == 0] = 1.0
    return mu, sd


class Fitted:
    """A fitted learner (picklable): `predict(X)` returns one output per row in the head's format; `info` is serialisable."""

    def __init__(self, kind: str, mu, sd, objects, info: dict):
        self.kind, self.mu, self.sd, self.objects, self.info = kind, mu, sd, objects, info

    def _z(self, Xn):
        np = _np()
        return (np.asarray(Xn, dtype=float) - self.mu) / self.sd

    def predict(self, Xn):
        np = _np()
        Z = self._z(Xn)
        k, o = self.kind, self.objects
        if k == "binary":
            return np.mean([m.predict_proba(Z)[:, 1] for m in o], axis=0)
        if k == "normal_ngboost":
            out = []
            for m in o:
                d = m.pred_dist(Z)
                out.append((d.params["loc"], np.maximum(MIN_SD, d.params["scale"])))
            return list(zip(out[0][0], out[0][1], out[1][0], out[1][1]))
        if k == "normal_hetero":
            Z1 = np.hstack([np.ones((len(Z), 1)), Z])
            (bm, gm), (bt, gt) = o
            return [(a, max(MIN_SD, b), c, max(MIN_SD, e)) for a, b, c, e in
                    zip(Z1 @ bm, np.exp(Z1 @ gm), Z1 @ bt, np.exp(Z1 @ gt))]
        if k == "normal":
            pm = np.mean([m.predict(Z) for m in o[0]], axis=0)
            pt = np.mean([m.predict(Z) for m in o[1]], axis=0)
            return [(a, None, c, None) for a, c in zip(pm, pt)]
        if k == "reg3":
            m = o
            order = list(m.classes_)
            return [tuple(float(p[order.index(c)]) for c in (0, 1, 2)) for p in m.predict_proba(Z)]
        if k == "goals":
            mh, ma = o
            return list(zip(mh.predict(Z), ma.predict(Z)))
        raise KeyError(k)


# --- binary ---------------------------------------------------------------------------------------------------------
def _clf(family):
    s = dict(SETTINGS[family])
    if family == "logit_l2":
        from sklearn.linear_model import LogisticRegression
        return [LogisticRegression(**s)]
    if family == "extra_trees":
        from sklearn.ensemble import ExtraTreesClassifier
        return [ExtraTreesClassifier(**s)]
    if family == "random_forest":
        from sklearn.ensemble import RandomForestClassifier
        return [RandomForestClassifier(**s)]
    if family == "mlp":
        from sklearn.neural_network import MLPClassifier
        seeds = s.pop("seeds")
        return [MLPClassifier(**s, random_state=SEED + k) for k in range(seeds)]
    raise KeyError(family)


def fit_binary(family, X, y) -> Fitted:
    np = _np()
    mu, sd = _scale(X)
    Z = (np.asarray(X, dtype=float) - mu) / sd
    models = _clf(family)
    for m in models:
        m.fit(Z, np.asarray(y, dtype=int))
    return Fitted("binary", mu, sd, models, {"training_rows": len(y)})


# --- normal (NBA margin and total) ----------------------------------------------------------------------------------
def _reg(family):
    s = dict(SETTINGS[family])
    if family == "ridge":
        from sklearn.linear_model import Ridge
        return [Ridge(**s)]
    if family == "elastic_net":
        from sklearn.linear_model import ElasticNet
        return [ElasticNet(**s)]
    if family == "extra_trees":
        from sklearn.ensemble import ExtraTreesRegressor
        return [ExtraTreesRegressor(**s)]
    if family == "mlp":
        from sklearn.neural_network import MLPRegressor
        seeds = s.pop("seeds")
        return [MLPRegressor(**s, random_state=SEED + k) for k in range(seeds)]
    raise KeyError(family)


def _hetero_linear(Z, y, l2):
    """Normal regression with log σ linear in the features: maximum likelihood, L2 on the slopes (no new dependency)."""
    np = _np()
    from scipy.optimize import minimize
    n, k = Z.shape
    Z1 = np.hstack([np.ones((n, 1)), Z])
    beta0 = np.linalg.lstsq(Z1, y, rcond=None)[0]
    r = y - Z1 @ beta0
    theta0 = np.concatenate([beta0, [math.log(max(1e-6, r.std()))], np.zeros(k)])

    def nll(theta):
        b, g = theta[:k + 1], theta[k + 1:]
        mu, logs = Z1 @ b, Z1 @ g
        z = (y - mu) * np.exp(-logs)
        val = np.sum(logs + 0.5 * z * z) / n + 0.5 * l2 * (np.sum(b[1:] ** 2) + np.sum(g[1:] ** 2)) / n
        gm = -(z * np.exp(-logs)) / n
        gs = (1 - z * z) / n
        grad_b = Z1.T @ gm
        grad_g = Z1.T @ gs
        grad_b[1:] += l2 * b[1:] / n
        grad_g[1:] += l2 * g[1:] / n
        return val, np.concatenate([grad_b, grad_g])
    res = minimize(nll, theta0, jac=True, method="L-BFGS-B")
    return res.x[:k + 1], res.x[k + 1:]


def _ngboost(Z, y, seasons):
    np = _np()
    from ngboost import NGBRegressor
    from ngboost.distns import Normal
    from sklearn.tree import DecisionTreeRegressor
    s = dict(SETTINGS["ngboost"])
    base = DecisionTreeRegressor(max_depth=s.pop("base_max_depth"), criterion="squared_error", random_state=SEED)
    rounds = s.pop("early_stopping_rounds")
    seasons = np.asarray(seasons)
    last = seasons.max()
    fit_idx, val_idx = seasons < last, seasons == last
    if fit_idx.sum() < 200:          # one training season only: no chronological validation, fixed 200 rounds
        m = NGBRegressor(Dist=Normal, Base=base, verbose=False, **{**s, "n_estimators": 200}).fit(Z, y)
        return m, 200
    m = NGBRegressor(Dist=Normal, Base=base, verbose=False, **s)
    m.fit(Z[fit_idx], y[fit_idx], X_val=Z[val_idx], Y_val=y[val_idx], early_stopping_rounds=rounds)
    best = (m.best_val_loss_itr or len(m.base_models) - 1) + 1
    # Refit on all training seasons with the chosen number of rounds.
    m = NGBRegressor(Dist=Normal, Base=base, verbose=False, **{**s, "n_estimators": best}).fit(Z, y)
    return m, best


def fit_normal(family, X, margin, total, seasons=None) -> Fitted:
    """Returns rows of (margin mean, margin sd or None, total mean, total sd or None). None: use residual SDs."""
    np = _np()
    mu, sd = _scale(X)
    Z = (np.asarray(X, dtype=float) - mu) / sd
    ys = (np.asarray(margin, dtype=float), np.asarray(total, dtype=float))
    if family == "ngboost":
        fitted = [_ngboost(Z, y, seasons) for y in ys]
        return Fitted("normal_ngboost", mu, sd, [m for m, _ in fitted],
                      {"training_rows": len(margin), "rounds": [n for _, n in fitted]})
    if family == "hetero_linear":
        params = [_hetero_linear(Z, y, SETTINGS["hetero_linear"]["l2"]) for y in ys]
        return Fitted("normal_hetero", mu, sd, params, {"training_rows": len(margin)})
    pairs = []
    for y in ys:
        ms = _reg(family)
        for m in ms:
            m.fit(Z, y)
        pairs.append(ms)
    return Fitted("normal", mu, sd, pairs, {"training_rows": len(margin)})


# --- NHL regulation 3-way and goals ---------------------------------------------------------------------------------
def _ot_share(reg, home_won, neutral):
    ties = [hw for r, hw, n in zip(reg, home_won, neutral) if r is not None and r[0] == r[1] and not n]
    return (sum(ties) + 1) / (len(ties) + 2)


def fit_reg3(family, X, reg, home_won, neutral) -> Fitted:
    np = _np()
    from sklearn.linear_model import LogisticRegression
    keep = [i for i, r in enumerate(reg) if r is not None]
    Xa = np.asarray(X, dtype=float)[keep]
    cls = [0 if reg[i][0] > reg[i][1] else 1 if reg[i][0] == reg[i][1] else 2 for i in keep]
    mu, sd = _scale(Xa)
    m = LogisticRegression(**SETTINGS[family]).fit((Xa - mu) / sd, cls)
    return Fitted("reg3", mu, sd, m, {"training_rows": len(keep), "ot_home_share": _ot_share(reg, home_won, neutral)})


def _pois(lam, n=16):
    out, p = [], math.exp(-lam)
    for k in range(n):
        out.append(p)
        p *= lam / (k + 1)
    return out


def fit_goals(family, X, reg, home_won, neutral) -> Fitted:
    np = _np()
    from sklearn.linear_model import PoissonRegressor
    keep = [i for i, r in enumerate(reg) if r is not None]
    Xa = np.asarray(X, dtype=float)[keep]
    mu, sd = _scale(Xa)
    Z = (Xa - mu) / sd
    mh = PoissonRegressor(**SETTINGS[family]).fit(Z, [reg[i][0] for i in keep])
    ma = PoissonRegressor(**SETTINGS[family]).fit(Z, [reg[i][1] for i in keep])
    lh, la = mh.predict(Z), ma.predict(Z)
    stats = []
    for i, a, b in zip(keep, lh, la):
        h, w = reg[i]
        if h > 15 or w > 15:
            continue
        ph, pa = _pois(a), _pois(b)
        stats.append((ph[h] * pa[w], sum(ph[k] * pa[k] for k in range(16)), h == w))
    weight = max(DRAW_GRID, key=lambda w: (sum(math.log(max(1e-300, (1 - w) * p + (w * p / d if tie else 0.0)))
                                               for p, d, tie in stats), -w))
    return Fitted("goals", mu, sd, (mh, ma), {"training_rows": len(keep), "ot_home_share": _ot_share(reg, home_won, neutral),
                                              "draw_weight": weight})
