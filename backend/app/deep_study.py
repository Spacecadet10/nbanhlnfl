"""Deep-learning study `deep-v1` (docs/MODEL_SELECTION.md §22). Exploratory; selects nothing for live use.

PyTorch and TensorFlow/Keras networks on the pool-v1 point-in-time feature walk. For each season s a network is fitted on
rows whose results were available before the season's first cutoff; the latest training season is held out only to choose
the number of epochs (early stopping), then the network is refitted on all training rows for that many epochs and predicts
season s. The networks are wrapped as `candidates.learners.Fitted` objects, so the pool engine turns them into market
vectors exactly as for every other learner (no identity-hashed file is edited).

Needs the `deep` extra (torch, tensorflow); run on store copies (`deep-study`), then `deep-evaluate` (saved tables only)."""
import gzip
import hashlib
import json
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from . import config
from .candidates import engine, features, learners
from .candidates.registry import MIN_TRAIN, Spec
from .db import utc_now
from .tuning_study import HEAD_MARKETS, _arrays, _week_boot, build_walk, goals_fitted

STUDY = "deep-v1"
SEEDS = tuple(20261003 + i for i in range(5))
HIDDEN = (64, 32)
DROPOUT = 0.2
LEARNING_RATE = 1e-3
WEIGHT_DECAY = 1e-3
BATCH = 128
MAX_EPOCHS = 200
PATIENCE = 15
SETTINGS = {"hidden": HIDDEN, "dropout": DROPOUT, "learning_rate": LEARNING_RATE, "weight_decay": WEIGHT_DECAY,
            "batch": BATCH, "max_epochs": MAX_EPOCHS, "patience": PATIENCE, "seeds": len(SEEDS)}
# (name, library, head): at most 4 per sport, fixed in the registration
CONFIGS = {
    "nba": [("torch_mlp:nba_base", "torch", "binary"), ("keras_mlp:nba_base", "keras", "binary"),
            ("torch_mlp:nba_base:normal", "torch", "normal")],
    "nhl": [("torch_mlp:nhl_base", "torch", "binary"), ("keras_mlp:nhl_base", "keras", "binary"),
            ("torch_goals:nhl_base", "torch", "goals")],
}
COUNTERPART = {"torch_mlp:nba_base": "mlp:nba_base", "keras_mlp:nba_base": "mlp:nba_base",
               "torch_mlp:nba_base:normal": "gbt_points", "torch_mlp:nhl_base": "mlp:nhl_base",
               "keras_mlp:nhl_base": "mlp:nhl_base", "torch_goals:nhl_base": "poisson_draw:nhl_base"}


# --- Networks (scikit-learn style: fit / predict / predict_proba) -----------------------------------------------------------
class TorchNet:
    """A PyTorch MLP. target 'class' (sigmoid, binary cross-entropy), 'reg' (identity, MSE on a standardised target) or
    'poisson' (log link, Poisson loss). `fit(..., val=...)` stops early and records `epochs_`; `fit(..., epochs=n)` trains
    exactly n epochs. Deterministic for a seed (single-threaded CPU)."""
    classes_ = [0, 1]

    def __init__(self, target: str, seed: int):
        self.target, self.seed = target, seed
        self.epochs_ = None
        self.shift, self.scale = 0.0, 1.0

    def _build(self, n_in: int):
        import torch
        from torch import nn
        torch.manual_seed(self.seed)
        layers, prev = [], n_in
        for h in HIDDEN:
            layers += [nn.Linear(prev, h), nn.ReLU(), nn.Dropout(DROPOUT)]
            prev = h
        return nn.Sequential(*layers, nn.Linear(prev, 1))

    def _loss(self, out, y):
        import torch
        from torch.nn import functional as F
        if self.target == "class":
            return F.binary_cross_entropy_with_logits(out, y)
        if self.target == "poisson":
            return F.poisson_nll_loss(out, y, log_input=True)
        return F.mse_loss(out, y)

    def fit(self, Z, y, val: Optional[tuple] = None, epochs: Optional[int] = None):
        import torch
        torch.set_num_threads(1)
        Z = torch.tensor(np.asarray(Z, dtype=np.float32))
        y = np.asarray(y, dtype=np.float32)
        if self.target == "reg":
            self.shift, self.scale = float(y.mean()), float(y.std()) or 1.0
        t = torch.tensor((y - self.shift) / self.scale).reshape(-1, 1)
        self.net = self._build(Z.shape[1])
        opt = torch.optim.AdamW(self.net.parameters(), lr=LEARNING_RATE, weight_decay=WEIGHT_DECAY)
        gen = torch.Generator().manual_seed(self.seed)
        if val is not None:
            Zv = torch.tensor(np.asarray(val[0], dtype=np.float32))
            tv = torch.tensor((np.asarray(val[1], dtype=np.float32) - self.shift) / self.scale).reshape(-1, 1)
        best, best_epoch, best_state, limit = float("inf"), 0, None, epochs or MAX_EPOCHS
        for epoch in range(1, limit + 1):
            self.net.train()
            order = torch.randperm(len(Z), generator=gen)
            for i in range(0, len(Z), BATCH):
                idx = order[i:i + BATCH]
                opt.zero_grad()
                self._loss(self.net(Z[idx]), t[idx]).backward()
                opt.step()
            if val is None:
                continue
            self.net.eval()
            with torch.no_grad():
                v = float(self._loss(self.net(Zv), tv))
            if v < best - 1e-6:
                best, best_epoch = v, epoch
                best_state = {k: x.clone() for k, x in self.net.state_dict().items()}
            elif epoch - best_epoch >= PATIENCE:
                break
        if val is not None:
            self.net.load_state_dict(best_state)
            self.epochs_ = best_epoch
        else:
            self.epochs_ = limit
        self.net.eval()
        return self

    def _raw(self, Z):
        import torch
        with torch.no_grad():
            return self.net(torch.tensor(np.asarray(Z, dtype=np.float32))).reshape(-1).numpy().astype(float)

    def predict_proba(self, Z):
        p = 1.0 / (1.0 + np.exp(-self._raw(Z)))
        return np.column_stack([1 - p, p])

    def predict(self, Z):
        raw = self._raw(Z)
        return np.exp(raw) if self.target == "poisson" else raw * self.scale + self.shift


class KerasNet:
    """The same binary MLP in TensorFlow/Keras (AdamW, dropout), with the same fit contract as TorchNet."""
    classes_ = [0, 1]

    def __init__(self, target: str, seed: int):
        if target != "class":
            raise ValueError("deep-v1 registers the Keras network for the binary winner head only")
        self.target, self.seed = target, seed
        self.epochs_ = None

    def fit(self, Z, y, val: Optional[tuple] = None, epochs: Optional[int] = None):
        import keras
        keras.utils.set_random_seed(self.seed)
        Z, y = np.asarray(Z, dtype=np.float32), np.asarray(y, dtype=np.float32)
        layers = [keras.layers.Input(shape=(Z.shape[1],))]
        for h in HIDDEN:
            layers += [keras.layers.Dense(h, activation="relu"), keras.layers.Dropout(DROPOUT)]
        self.net = keras.Sequential(layers + [keras.layers.Dense(1, activation="sigmoid")])
        self.net.compile(optimizer=keras.optimizers.AdamW(learning_rate=LEARNING_RATE, weight_decay=WEIGHT_DECAY),
                         loss="binary_crossentropy")
        if val is not None:
            stop = keras.callbacks.EarlyStopping(monitor="val_loss", patience=PATIENCE, min_delta=1e-6, restore_best_weights=True)
            hist = self.net.fit(Z, y, validation_data=(np.asarray(val[0], dtype=np.float32), np.asarray(val[1], dtype=np.float32)),
                                epochs=MAX_EPOCHS, batch_size=BATCH, shuffle=True, verbose=0, callbacks=[stop])
            self.epochs_ = int(np.argmin(hist.history["val_loss"])) + 1
        else:
            self.net.fit(Z, y, epochs=epochs or MAX_EPOCHS, batch_size=BATCH, shuffle=True, verbose=0)
            self.epochs_ = epochs or MAX_EPOCHS
        return self

    def predict_proba(self, Z):
        p = self.net.predict(np.asarray(Z, dtype=np.float32), verbose=0).reshape(-1).astype(float)
        return np.column_stack([1 - p, p])


class Mean:
    """Average prediction of several fitted regressors (one object for the goals head's home or away rate)."""

    def __init__(self, nets: list):
        self.nets = nets

    def predict(self, Z):
        return np.mean([n.predict(Z) for n in self.nets], axis=0)


NETS = {"torch": TorchNet, "keras": KerasNet}


# --- One season's fit: epochs chosen on the latest earlier season, then refitted on every training row --------------------------
def _ensemble(lib: str, target: str, Z_inner, y_inner, Z_val, y_val, Z, y) -> tuple[list, int]:
    epochs = NETS[lib](target, SEEDS[0]).fit(Z_inner, y_inner, val=(Z_val, y_val)).epochs_
    return [NETS[lib](target, seed).fit(Z, y, epochs=epochs) for seed in SEEDS], epochs


def fit(lib: str, head: str, train: list, names: tuple) -> Optional[learners.Fitted]:
    """A learners.Fitted for one season, or None when no earlier validation season exists yet."""
    val_season = max(r.season for r in train)
    inner = [r for r in train if r.season < val_season]
    val = [r for r in train if r.season == val_season]
    if not inner or not val:
        return None
    X = _arrays(train, names)
    mu, sd = learners._scale(X)
    Z = (X - mu) / sd
    mu_i, sd_i = learners._scale(_arrays(inner, names))          # the epoch choice never sees the validation season's scale
    Zi, Zv = (_arrays(inner, names) - mu_i) / sd_i, (_arrays(val, names) - mu_i) / sd_i
    info = {"training_rows": len(train), "validation_season": val_season, "library": lib, "settings": SETTINGS}

    def target(rows, key):
        return np.array([float(key(r.y)) for r in rows])
    if head == "binary":
        won = lambda y: int(y["home_won"])
        nets, e = _ensemble(lib, "class", Zi, target(inner, won), Zv, target(val, won), Z, target(train, won))
        return learners.Fitted("binary", mu, sd, nets, {**info, "epochs": e})
    if head == "normal":
        out, epochs = [], {}
        for key in ("margin", "total"):
            k = lambda y, key=key: y[key]
            nets, e = _ensemble(lib, "reg", Zi, target(inner, k), Zv, target(val, k), Z, target(train, k))
            out.append(nets)
            epochs[key] = e
        return learners.Fitted("normal", mu, sd, out, {**info, "epochs": epochs})
    if head != "goals":
        raise ValueError(head)

    def regulation(rows, Zrows):
        keep = [i for i, r in enumerate(rows) if r.y["regulation"] is not None]
        return [rows[i] for i in keep], Zrows[keep]
    (ri, Zik), (rv, Zvk), (rt, Zk) = regulation(inner, Zi), regulation(val, Zv), regulation(train, Z)
    rates, epochs = [], {}
    for side, label in ((0, "home"), (1, "away")):
        k = lambda y, side=side: y["regulation"][side]
        nets, e = _ensemble(lib, "poisson", Zik, target(ri, k), Zvk, target(rv, k), Zk, target(rt, k))
        rates.append(Mean(nets))
        epochs[label] = e
    reg = [r.y["regulation"] for r in rt]
    ot = learners._ot_share(reg, [r.y["home_won"] for r in rt], [bool(r.x.get("neutral")) for r in rt])
    return goals_fitted(mu, sd, rates[0], rates[1], Zk, reg, ot, len(rt), {**info, "epochs": epochs})


def deep_walk(sport: str, name: str, lib: str, head: str, rows: list, log: Callable = print) -> dict:
    names = features.FEATURE_SETS[f"{sport}_base"]
    spec = Spec(name, sport, "logit_l2", head, f"{sport}_base", "candidate", "deep-v1 (not a registry candidate)")
    rows = sorted(rows, key=lambda r: (r.cutoff_at, r.tipoff_at, r.game_id))
    out, fits, residuals = {}, [], []
    for s in sorted({r.season for r in rows}):
        season_rows = [r for r in rows if r.season == s]
        train = engine.training_rows(rows, s, season_rows[0].cutoff_at)
        if len(train) < MIN_TRAIN[sport]:
            continue
        t0 = time.time()
        model = fit(lib, head, train, names)
        if model is None:
            continue
        sd = engine.residual_sd(residuals, s) if head == "normal" else None
        fits.append({"season": s, "training_games": len(train), "results_through": max(r.result_available_at for r in train),
                     "validation_season": model.info["validation_season"] if head != "goals" else model.info["params"]["validation_season"],
                     "epochs": model.info["epochs"] if head != "goals" else model.info["params"]["epochs"],
                     "seconds": round(time.time() - t0, 1)})
        for r, o in zip(season_rows, engine.outputs(spec, model, season_rows, sd)):
            if head == "normal" and r.y is not None:
                residuals.append((s, r.y["margin"] - o["m"], r.y["total"] - o["t"]))
            if engine.ready(o):
                out[r.game_id] = o
        log(f"{name} {s}: {len(train)} training games, epochs {fits[-1]['epochs']}, {fits[-1]['seconds']} s")
    return {"name": name, "head": head, "outputs": out, "fits": fits, "residuals": residuals}


def versions() -> dict:
    import keras
    import tensorflow
    import torch
    return {"torch": torch.__version__, "tensorflow": tensorflow.__version__, "keras": keras.__version__}


def run(conn_for: Callable, out_dir: Optional[Path] = None, sports_: tuple = ("nhl", "nba"), log: Callable = print) -> dict:
    """Fits every registered network on the walk of store copies and saves their out-of-sample market vectors."""
    from . import discovery_study as ds
    root = config.data_dir()
    out_dir = out_dir or root / "research" / "deep"
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {"study": STUDY, "started_at": utc_now(), "settings": SETTINGS, "seeds": list(SEEDS), "libraries": versions(),
              "sports": {}}
    for sport in sports_:
        t0 = time.time()
        walk = build_walk(conn_for(sport), sport)
        tv = features.table_version({r.game_id: r for r in walk})
        log(f"{sport}: feature walk {len(walk)} games, table {tv}, {time.time() - t0:.0f} s")
        results = [deep_walk(sport, n, lib, head, walk, log) for n, lib, head in CONFIGS[sport]]
        tables, _ = ds.load(root, sport)
        oof = {}
        for res in results:
            for market in HEAD_MARKETS[res["head"]]:
                for row in tables.get(market, []):
                    o = res["outputs"].get(row["game_id"])
                    v = None if o is None else engine.vector(o, market, row["line"])
                    if v is not None:
                        oof.setdefault(market, {}).setdefault(row["game_id"], {})[res["name"]] = [float(x) for x in v]
        raw = gzip.compress(json.dumps({"sport": sport, "study": STUDY, "feature_table": tv, "markets": oof,
                                        "fits": {r["name"]: r["fits"] for r in results}}).encode())
        (out_dir / f"oof-{sport}.json.gz").write_bytes(raw)
        report["sports"][sport] = {"feature_table": tv, "oof_sha256_24": hashlib.sha256(raw).hexdigest()[:24],
                                   "fits": {r["name"]: r["fits"] for r in results},
                                   "games_per_market": {m: len(v) for m, v in oof.items()}, "seconds": round(time.time() - t0, 1)}
    report["finished_at"] = utc_now()
    (out_dir / "report.json").write_text(json.dumps(report, indent=1, default=float))
    return {**report, "output_dir": str(out_dir)}


# --- Evaluation: each network alone, against the best earlier-chosen existing member and its scikit-learn counterpart -----------
def reserved_seasons(sport: str) -> set:
    from . import backtest, nhl_eval
    return set((nhl_eval if sport == "nhl" else backtest).PERIODS["test"])


def evaluate(out_dir: Optional[Path] = None, log: Callable = print) -> dict:
    from . import discovery_study as ds
    root = config.data_dir()
    ddir = root / "research" / "deep"
    out_dir = out_dir or root / "reports" / STUDY
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {"study": STUDY, "started_at": utc_now(), "markets": {},
              "decision_rule": "proposed for prospective recording only if the pooled log-loss difference against the best "
                               "earlier-chosen existing member has a 95% interval entirely below 0"}
    for sport in ("nhl", "nba"):
        path = ddir / f"oof-{sport}.json.gz"
        if not path.is_file():
            continue
        deep = json.loads(gzip.decompress(path.read_bytes()))["markets"]
        tables, _ = ds.load(root, sport)
        reserved = reserved_seasons(sport)
        for market, rows in tables.items():
            names = sorted({n for v in deep.get(market, {}).values() for n in v})
            if not names:
                continue
            dv = deep[market]
            aug = [{**r, "vectors": {**r["vectors"], **{n: tuple(v) for n, v in dv[r["game_id"]].items()}}}
                   for r in rows if r["game_id"] in dv and all(n in dv[r["game_id"]] for n in names)]
            base = ds.members_for(sport, rows)
            ar = ds.Arrays(market, aug, base + names)
            real = ar.realised
            ok = np.ones(ar.n, bool) if market == "reg3" else real != 1      # a push carries no information on the sides
            idx = np.clip(real, 0, 2)

            def scores(m, mask):
                P = ar.P[m][mask]
                r = idx[mask]
                ll = -np.log(np.clip(P[np.arange(len(r)), r], 1e-15, 1))
                onehot = np.eye(3)[r]
                side, _ = ds.Arrays.pick(ar, ar.P[m])
                picked = side[mask]
                decided = picked >= 0
                return ll, float(((P - onehot) ** 2).sum(axis=1).mean()), \
                    float((picked[decided] == r[decided]).mean()) if decided.any() else None
            seasons = sorted(set(ar.season.tolist()))
            losses = {m: -np.log(np.clip(ar.P[m][np.arange(ar.n), idx], 1e-15, 1)) for m in base + names}
            out = {}
            for n in names:
                d_best, d_cp, weeks, per = [], [], [], {}
                cp = COUNTERPART.get(n)
                for s in seasons[1:]:
                    prior, cur = (ar.season < s) & ok, (ar.season == s) & ok
                    if not prior.any() or not cur.any():
                        continue
                    best = min(base, key=lambda m: (losses[m][prior].mean(), base.index(m)))
                    ll, brier, acc = scores(n, cur)
                    ll_b, brier_b, acc_b = scores(best, cur)
                    per[str(s)] = {"games": int(cur.sum()), "reserved_test_season": s in reserved, "log_loss": float(ll.mean()),
                                   "brier": brier, "accuracy": acc, "best_prior_existing": best,
                                   "best_log_loss": float(ll_b.mean()), "best_brier": brier_b, "best_accuracy": acc_b,
                                   "minus_best": float((ll - ll_b).mean()),
                                   "minus_counterpart": float((ll - losses[cp][cur]).mean()) if cp in losses else None}
                    d_best += (ll - ll_b).tolist()
                    weeks += ar.week[cur].tolist()
                    if cp in losses:
                        d_cp += (ll - losses[cp][cur]).tolist()
                if not d_best:
                    continue
                vs_best = _week_boot(d_best, weeks)
                out[n] = {"games": len(d_best), "log_loss_minus_best_prior_existing": vs_best, "counterpart": cp,
                          "log_loss_minus_counterpart": _week_boot(d_cp, weeks) if d_cp else None, "per_season": per,
                          "qualifies": vs_best["ci95"][1] < 0}
                log(f"{sport} {market} {n}: vs best prior {vs_best['mean']:+.4f} {vs_best['ci95']}, qualifies {out[n]['qualifies']}")
            if out:
                report["markets"][f"{sport}:{market}"] = {"games": len(aug), "existing_members": base, "deep": out}
    report["finished_at"] = utc_now()
    (out_dir / "report.json").write_text(json.dumps(report, indent=1, default=float))
    return {**report, "output_dir": str(out_dir)}
