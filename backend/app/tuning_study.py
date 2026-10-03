"""Bounded chronological tuning `tuning-v1` (docs/MODEL_SELECTION.md §19). Exploratory.

For each season s, Optuna (TPE, fixed seed and trial count) tunes a learner on seasons < s−1 against season s−1, refits the
best setting on every season < s and predicts season s only. The learners are built as `candidates.learners.Fitted` objects
so the pool engine turns them into market vectors at each table row's own line (no change to any hashed pool file)."""
import gzip
import hashlib
import json
import math
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from . import config
from .candidates import engine, features, learners
from .candidates.registry import MIN_TRAIN, Spec
from .db import utc_now

STUDY = "tuning-v1"
SEED = 20261003
TRIALS = 25
CONFIGS = {
    "nba": [("hgb_tuned:nba_base", "hgb", "binary"), ("hgb_tuned:nba_base:normal", "hgb", "normal"),
            ("et_tuned:nba_base", "et", "binary"), ("et_tuned:nba_base:normal", "et", "normal")],
    "nhl": [("hgb_tuned:nhl_base", "hgb", "binary"), ("hgb_tuned:nhl_base:reg3", "hgb", "reg3"),
            ("hgb_tuned:nhl_base:goals", "hgb", "goals"), ("et_tuned:nhl_base", "et", "binary")],
}


# --- Search spaces and models ---------------------------------------------------------------------------------------------
def suggest(trial, family: str) -> dict:
    if family == "hgb":
        return {"learning_rate": trial.suggest_float("learning_rate", 0.02, 0.2, log=True),
                "max_iter": trial.suggest_int("max_iter", 100, 600),
                "max_leaf_nodes": trial.suggest_int("max_leaf_nodes", 8, 64),
                "min_samples_leaf": trial.suggest_int("min_samples_leaf", 20, 300),
                "l2_regularization": trial.suggest_float("l2_regularization", 0.001, 10.0, log=True)}
    return {"min_samples_leaf": trial.suggest_int("min_samples_leaf", 10, 300),
            "max_features": trial.suggest_float("max_features", 0.2, 1.0),
            "max_depth": trial.suggest_int("max_depth", 4, 30)}


def _model(family: str, head: str, p: dict, target: str = "class"):
    from sklearn.ensemble import (ExtraTreesClassifier, ExtraTreesRegressor, HistGradientBoostingClassifier,
                                  HistGradientBoostingRegressor)
    if family == "hgb":
        kw = dict(p, early_stopping=False, random_state=SEED)
        if target == "class":
            return HistGradientBoostingClassifier(**kw)
        return HistGradientBoostingRegressor(loss="poisson" if target == "poisson" else "squared_error", **kw)
    kw = dict(p, n_estimators=300, random_state=SEED, n_jobs=1)
    return ExtraTreesClassifier(**kw) if target == "class" else ExtraTreesRegressor(**kw)


def _arrays(rows, names):
    return np.array([[float(r.x[k]) for k in names] for r in rows], dtype=float)


def _reg3_class(reg):
    return 0 if reg[0] > reg[1] else 1 if reg[0] == reg[1] else 2


def fit(family: str, head: str, rows: list, names: tuple, p: dict) -> learners.Fitted:
    """A learners.Fitted in the head's format (engine.outputs/vector work unchanged)."""
    X = _arrays(rows, names)
    mu, sd = learners._scale(X)
    Z = (X - mu) / sd
    ys = [r.y for r in rows]
    if head == "binary":
        m = _model(family, head, p).fit(Z, np.array([int(y["home_won"]) for y in ys]))
        return learners.Fitted("binary", mu, sd, [m], {"training_rows": len(rows), "params": p})
    if head == "normal":
        mm = _model(family, head, p, "reg").fit(Z, np.array([y["margin"] for y in ys], dtype=float))
        mt = _model(family, head, p, "reg").fit(Z, np.array([y["total"] for y in ys], dtype=float))
        return learners.Fitted("normal", mu, sd, [[mm], [mt]], {"training_rows": len(rows), "params": p})
    keep = [i for i, y in enumerate(ys) if y["regulation"] is not None]
    reg = [ys[i]["regulation"] for i in keep]
    won = [ys[i]["home_won"] for i in keep]
    neutral = [bool(rows[i].x.get("neutral")) for i in keep]
    Zk = Z[keep]
    ot = learners._ot_share(reg, won, neutral)
    if head == "reg3":
        m = _model(family, head, p).fit(Zk, np.array([_reg3_class(r) for r in reg]))
        return learners.Fitted("reg3", mu, sd, m, {"training_rows": len(keep), "ot_home_share": ot, "params": p})
    mh = _model(family, head, p, "poisson").fit(Zk, np.array([r[0] for r in reg], dtype=float))
    ma = _model(family, head, p, "poisson").fit(Zk, np.array([r[1] for r in reg], dtype=float))
    lh, la = mh.predict(Zk), ma.predict(Zk)
    stats = []
    for (h, w), a, b in zip(reg, lh, la):
        if h > 15 or w > 15:
            continue
        ph, pa = learners._pois(a), learners._pois(b)
        stats.append((ph[h] * pa[w], sum(ph[k] * pa[k] for k in range(16)), h == w))
    weight = max(learners.DRAW_GRID, key=lambda w: (sum(math.log(max(1e-300, (1 - w) * pp + (w * pp / d if tie else 0.0)))
                                                        for pp, d, tie in stats), -w))
    return learners.Fitted("goals", mu, sd, (mh, ma), {"training_rows": len(keep), "ot_home_share": ot, "draw_weight": weight,
                                                       "params": p})


def score(head: str, model: learners.Fitted, rows: list, names: tuple) -> float:
    """Validation objective (lower is better)."""
    X = _arrays(rows, names)
    ys = [r.y for r in rows]
    if head == "binary":
        p = np.clip(model.predict(X), 1e-6, 1 - 1e-6)
        y = np.array([int(v["home_won"]) for v in ys])
        return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
    if head == "normal":
        out = model.predict(X)
        m = np.array([o[0] for o in out])
        t = np.array([o[2] for o in out])
        ym = np.array([v["margin"] for v in ys], dtype=float)
        yt = np.array([v["total"] for v in ys], dtype=float)
        return float(np.mean((m - ym) ** 2) / max(1e-9, ym.var()) + np.mean((t - yt) ** 2) / max(1e-9, yt.var()))
    keep = [i for i, v in enumerate(ys) if v["regulation"] is not None]
    Xk = X[keep]
    reg = [ys[i]["regulation"] for i in keep]
    if head == "reg3":
        P = np.array(model.predict(Xk))
        c = np.array([_reg3_class(r) for r in reg])
        return float(-np.mean(np.log(np.clip(P[np.arange(len(c)), c], 1e-9, 1))))
    out = model.predict(Xk)
    dev = 0.0
    for (lh, la), (h, a) in zip(out, reg):
        for lam, k in ((max(lh, 1e-6), h), (max(la, 1e-6), a)):
            dev += 2 * ((k * math.log(k / lam) if k > 0 else 0.0) - (k - lam))
    return dev / len(reg)


# --- Chronological tuning walk ----------------------------------------------------------------------------------------------
def _before(rows, season, cutoff):
    return engine.training_rows(rows, season, cutoff)


def tuned_walk(sport: str, name: str, family: str, head: str, rows: list, log: Callable = print) -> dict:
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    names = features.FEATURE_SETS[f"{sport}_base"]
    spec = Spec(name, sport, "logit_l2", head, f"{sport}_base", "candidate", "tuning-v1 (not a registry candidate)")
    rows = sorted(rows, key=lambda r: (r.cutoff_at, r.tipoff_at, r.game_id))
    seasons = sorted({r.season for r in rows})
    out, fits, residuals, trials_log = {}, [], [], {}
    for s in seasons:
        season_rows = [r for r in rows if r.season == s]
        train = _before(rows, s, season_rows[0].cutoff_at)
        if len(train) < MIN_TRAIN[sport]:
            continue
        val_season = max(r.season for r in train)
        inner_train = [r for r in train if r.season < val_season]
        inner_val = [r for r in train if r.season == val_season]
        t0 = time.time()
        if len(inner_train) >= MIN_TRAIN[sport] and inner_val:
            sampler = optuna.samplers.TPESampler(seed=SEED)
            study = optuna.create_study(direction="minimize", sampler=sampler)
            study.optimize(lambda tr: score(head, fit(family, head, inner_train, names, suggest(tr, family)), inner_val, names),
                           n_trials=TRIALS)
            best = dict(study.best_params)
            trials_log[str(s)] = {"validation_season": val_season, "inner_training_games": len(inner_train),
                                  "best": best, "best_value": study.best_value,
                                  "trials": [{"params": t.params, "value": t.value} for t in study.trials]}
        else:
            continue                       # no inner validation season yet: no tuned fit for this season
        model = fit(family, head, train, names, best)
        sd = engine.residual_sd(residuals, s) if head == "normal" else None
        fits.append({"season": s, "training_games": len(train), "results_through": max(r.result_available_at for r in train),
                     "params": best, "seconds": round(time.time() - t0, 1)})
        for r, o in zip(season_rows, engine.outputs(spec, model, season_rows, sd)):
            if head == "normal" and r.y is not None:
                residuals.append((s, r.y["margin"] - o["m"], r.y["total"] - o["t"]))
            if engine.ready(o):
                out[r.game_id] = o
        log(f"{name} {s}: {len(train)} training games, best {best}, {fits[-1]['seconds']} s")
    return {"name": name, "head": head, "outputs": out, "fits": fits, "trials": trials_log}


# --- Data ---------------------------------------------------------------------------------------------------------------------
def build_walk(conn, sport: str) -> list:
    """The pool-v1 point-in-time feature walk over every loadable season (base features only are used)."""
    from . import backtest
    if sport == "nba":
        _, cfg = backtest.load_frozen_decision(conn)
        rows, events = backtest.load_rows(conn, "test"), backtest.load_box_events(conn, "test")
        return list(features.walk(features.NbaRecorder(cfg), rows, events).values())
    from . import nhl_eval, nhl_shots, sports
    from .candidates import goalies
    from .models.hockey import GoalsConfig, HockeyEloConfig
    d = nhl_eval.load_decision(conn)
    rows = nhl_eval.load_rows(conn, "test")
    events = nhl_shots.load_events(conn, rows)
    ec, gc, key = HockeyEloConfig(**d["hockey_elo_config"]), GoalsConfig(**d["goals_config"]), sports.NHL.rating_key
    detail = goalies.load(conn)
    starters = goalies.actual_starters(conn, detail)
    rec = features.NhlRecorder(ec, gc, key, detail,
                               starters=lambda g: (starters.get(g.game_id, {}), {"goalie_source": "actual_starter"}))
    return list(features.walk(rec, rows, events).values())


def _one(args):
    sport, name, family, head, rows = args
    return tuned_walk(sport, name, family, head, rows, log=lambda m: print(m, flush=True))


HEAD_MARKETS = {"binary": ("winner",), "normal": ("spread", "total", "team_total_home", "team_total_away"),
                "reg3": ("winner", "reg3"), "goals": ("winner", "reg3", "spread", "total", "team_total_home", "team_total_away")}


def run(conn_for: Callable, out_dir: Optional[Path] = None, sports_: tuple = ("nhl", "nba"), log: Callable = print) -> dict:
    from . import discovery_study as ds
    started = utc_now()
    root = config.data_dir()
    out_dir = out_dir or root / "research" / "tuning"
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {"study": STUDY, "started_at": started, "trials": TRIALS, "seed": SEED, "sports": {}}
    for sport in sports_:
        t0 = time.time()
        walk = build_walk(conn_for(sport), sport)
        tv = features.table_version({r.game_id: r for r in walk})
        log(f"{sport}: feature walk {len(walk)} games, table {tv}, {time.time() - t0:.0f} s")
        jobs = [(sport, n, f, h, walk) for n, f, h in CONFIGS[sport]]
        with ProcessPoolExecutor(max_workers=len(jobs)) as ex:
            results = list(ex.map(_one, jobs))
        tables, _ = ds.load(root, sport)
        oof = {}
        for res in results:
            spec_head = res["head"]
            for market in HEAD_MARKETS[spec_head]:
                for row in tables.get(market, []):
                    o = res["outputs"].get(row["game_id"])
                    if o is None:
                        continue
                    v = engine.vector(o, market, row["line"])
                    if v is not None:
                        oof.setdefault(market, {}).setdefault(row["game_id"], {})[res["name"]] = [float(x) for x in v]
        payload = {"sport": sport, "study": STUDY, "feature_table": tv, "markets": oof,
                   "fits": {r["name"]: r["fits"] for r in results}}
        raw = gzip.compress(json.dumps(payload).encode())
        (out_dir / f"oof-{sport}.json.gz").write_bytes(raw)
        (out_dir / f"trials-{sport}.json").write_text(json.dumps({r["name"]: r["trials"] for r in results}, indent=1))
        report["sports"][sport] = {"feature_table": tv, "oof_sha256_24": hashlib.sha256(raw).hexdigest()[:24],
                                   "fits": {r["name"]: r["fits"] for r in results},
                                   "games_per_market": {m: len(v) for m, v in oof.items()},
                                   "seconds": round(time.time() - t0, 1)}
    report["finished_at"] = utc_now()
    (out_dir / "report.json").write_text(json.dumps(report, indent=1, default=float))
    report["output_dir"] = str(out_dir)
    return report


# --- Evaluation (alone and inside combinations) --------------------------------------------------------------------------
COUNTERPART = {"et_tuned:nba_base": "extra_trees:nba_base", "et_tuned:nba_base:normal": "extra_trees:nba_base:normal",
               "et_tuned:nhl_base": "extra_trees:nhl_base", "hgb_tuned:nba_base": "gbt_v1",
               "hgb_tuned:nba_base:normal": "gbt_points", "hgb_tuned:nhl_base": "hockey_gbt",
               "hgb_tuned:nhl_base:reg3": "multinomial_l2:nhl_base", "hgb_tuned:nhl_base:goals": "poisson_draw:nhl_base"}


def _week_boot(diffs: list, weeks: list, n: int = 2000) -> dict:
    import random
    by: dict = {}
    for d, w in zip(diffs, weeks):
        by.setdefault(w, []).append(d)
    ks = sorted(by)
    rng = random.Random(SEED)
    means = []
    for _ in range(n):
        tot = cnt = 0
        for _k in ks:
            b = by[ks[rng.randrange(len(ks))]]
            tot += sum(b)
            cnt += len(b)
        means.append(tot / cnt)
    means.sort()
    return {"mean": float(np.mean(diffs)), "ci95": [means[int(0.025 * n)], means[int(0.975 * n) - 1]], "games": len(diffs),
            "weeks": len(ks)}


def evaluate(out_dir: Optional[Path] = None, budget: Optional[int] = None, log: Callable = print) -> dict:
    from . import discovery_study as ds
    root = config.data_dir()
    tdir = root / "research" / "tuning"
    out_dir = out_dir or root / "reports" / "tuning-v1"
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {"study": STUDY, "started_at": utc_now(), "markets": {}}
    for sport in ("nhl", "nba"):
        tuned = json.loads(gzip.decompress((tdir / f"oof-{sport}.json.gz").read_bytes()))["markets"]
        tables, _ = ds.load(root, sport)
        for market, rows in tables.items():
            tv = tuned.get(market, {})
            names = sorted({n for v in tv.values() for n in v})
            if not names:
                continue
            aug = []
            for r in rows:
                if r["game_id"] in tv and all(n in tv[r["game_id"]] for n in names):
                    aug.append({**r, "vectors": {**r["vectors"], **{n: tuple(v) for n, v in tv[r["game_id"]].items()}}})
            base_members = ds.members_for(sport, rows)
            ar = ds.Arrays(market, aug, base_members + names)
            seasons = sorted(set(ar.season.tolist()))
            real = ar.realised
            ok = np.ones(ar.n, bool) if market == "reg3" else real != 1

            def ll(m):
                P = ar.P[m]
                return -np.log(np.clip(P[np.arange(ar.n), np.clip(real, 0, 2)], 1e-15, 1))
            losses = {m: ll(m) for m in base_members + names}
            alone = {}
            for n in names:
                diffs_best, diffs_cp, weeks, per = [], [], [], {}
                for s in seasons[1:]:
                    prior = (ar.season < s) & ok
                    cur = (ar.season == s) & ok
                    best = min(base_members, key=lambda m: (losses[m][prior].mean(), base_members.index(m)))
                    d = losses[n][cur] - losses[best][cur]
                    cp = COUNTERPART.get(n)
                    per[str(s)] = {"best_prior_existing": best, "minus_best": float(d.mean()),
                                   "minus_counterpart": float((losses[n][cur] - losses[cp][cur]).mean()) if cp in losses else None}
                    diffs_best += d.tolist()
                    weeks += ar.week[cur].tolist()
                    if cp in losses:
                        diffs_cp += (losses[n][cur] - losses[cp][cur]).tolist()
                alone[n] = {"log_loss_minus_best_prior_existing": _week_boot(diffs_best, weeks),
                            "counterpart": COUNTERPART.get(n),
                            "log_loss_minus_untuned_counterpart": _week_boot(diffs_cp, weeks) if diffs_cp else None,
                            "per_season": per}
            t0 = time.time()
            kw = {"budget": budget} if budget else {}
            base, _ = ds.analyse(sport, market, [{**r, "vectors": {k: v for k, v in r["vectors"].items() if k not in names}}
                                                 for r in aug], log=lambda *_: None, **kw)
            withm, tab = ds.analyse(sport, market, aug, log=lambda *_: None, **kw)
            pooled = {}
            for label, rep in (("without_tuned", base), ("with_tuned", withm)):
                agg = {}
                for f in rep["folds"]:
                    for x in f["assessed"]:
                        st = x["assess"]
                        nn = st["wins"] + st["losses"]
                        b = st["matched"]["best_prior_single"]
                        if nn and b is not None:
                            g = agg.setdefault(f"{x['threshold']:.2f}", [0, 0, 0.0, 0.0])
                            g[0] += st["wins"]
                            g[1] += nn
                            g[2] += b * nn
                            g[3] += (st["matched"]["all_member_average"] or 0) * nn
                pooled[label] = {t: {"win_rate": w / nn, "decided": nn, "best_prior_single": bs / nn, "all_member_average": av / nn}
                                 for t, (w, nn, bs, av) in sorted(agg.items())}
            # with / without each tuned member, for every frozen rule that contains one (same assess season, same threshold)
            route_b = []
            for f in withm["folds"]:
                a = f["assess_season"]
                for x in f["assessed"]:
                    meth, mem = x["group"].split("|")
                    members = mem.split("+")
                    for n in names:
                        if n not in members:
                            continue
                        rest = [m for m in members if m != n]
                        st = x["assess"]
                        row = {"assess_season": a, "rule": x["group"], "threshold": x["threshold"], "why": x["why"],
                               "with": {"decided": st["wins"] + st["losses"], "win_rate": st["win_rate"]}}
                        if rest and meth in ("avg", "unanimous_min", "majority", "single") and (meth != "majority" or len(rest) >= 3):
                            m2 = meth if len(rest) > 1 else "single"
                            side, conf, _, _ = ds.side_conf(ar, {}, {"method": m2, "members": rest})
                            out = ar.outcome(side)
                            sel = (side >= 0) & (conf + 1e-9 >= x["threshold"]) & (ar.season == a)
                            o = out[sel]
                            w, l = int((o == 1).sum()), int((o == 0).sum())
                            row["without"] = {"rule": f"{m2}|{'+'.join(rest)}", "decided": w + l, "win_rate": w / (w + l) if w + l else None}
                        else:
                            row["without"] = None
                        route_b.append(row)
            report["markets"][f"{sport}:{market}"] = {
                "tuned_members": names, "games": ar.n, "seasons": seasons, "alone": alone,
                "frozen_pooled": pooled, "qualified": {"without_tuned": [q for f in base["folds"] for q in f["qualified"]],
                                                       "with_tuned": [q for f in withm["folds"] for q in f["qualified"]]},
                "frozen_rules_with_tuned_member": route_b,
                "registry_with_tuned": withm["prospective_registry"], "configurations": withm["plan"]["configurations"]}
            with gzip.open(out_dir / f"groups-{sport}-{market}.json.gz", "wt") as fh:
                json.dump(tab, fh, default=float)
            log(f"{sport}:{market}: {ar.n} games, tuned {names}, {time.time() - t0:.0f} s")
    report["finished_at"] = utc_now()
    (out_dir / "evaluation.json").write_text(json.dumps(report, indent=1, default=float))
    report["output_dir"] = str(out_dir)
    return report
