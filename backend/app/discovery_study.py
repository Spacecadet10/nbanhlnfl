"""Historical training and combination discovery `discovery-v1` (docs/MODEL_SELECTION.md §18). Exploratory.

Reuses the chained pool-v1 chronological out-of-sample tables (every member refitted at each season start on earlier
seasons), trains two learned combiners per market at every season start on earlier seasons only, searches subsets ×
agreement rules × cumulative thresholds, chooses rules on earlier seasons and evaluates them once on the next season, and
keeps a per-group table (thresholds and non-overlapping bands, per season) for the Performance tracking view."""
import gzip
import hashlib
import json
import math
import random
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from itertools import combinations
from pathlib import Path
from statistics import NormalDist
from typing import Callable, Optional

import numpy as np

from . import config
from .db import utc_now

STUDY = "discovery-v1"
SEED = 20261003
BUDGET = 6000
MIN_PICKS, MIN_COVERAGE, ALPHA, MIN_REPORT = 100, 0.05, 0.05, 50
TWO_WAY_T = tuple(round(0.50 + 0.05 * i, 2) for i in range(9))          # 0.50 … 0.90
REG3_T = tuple(round(0.35 + 0.05 * i, 2) for i in range(8))             # 0.35 … 0.70
TWO_WAY_BANDS = tuple((round(0.50 + 0.05 * i, 2), round(0.55 + 0.05 * i, 2)) for i in range(8)) + ((0.90, 1.0001),)
REG3_BANDS = ((1 / 3, 0.35),) + tuple((round(0.35 + 0.05 * i, 2), round(0.40 + 0.05 * i, 2)) for i in range(7)) + ((0.70, 1.0001),)
EXCLUDED = ("logit_l2:nba_base", "ridge:nba_base")           # pool references re-expressing existing members
PRIMARY = {"nba": {"winner": "elo"}, "nhl": {"winner": "hockey_elo"}}
LINE_PRIMARY = {"nba": "points_normal", "nhl": "goals_poisson"}
EXISTING = {"nba": ("elo", "logistic", "logistic_v2", "efficiency_margin", "gbt_v1", "points_normal", "ridge_points",
                    "gbt_points", "ridge_points@weekly", "gbt_points@weekly"),
            "nhl": ("hockey_elo", "goals_poisson", "hockey_logistic", "hockey_gbt", "hockey_logistic_xg", "goals_draw",
                    "goals_glm")}
# Rules evaluated before for the market (§11/§14/§15/pool stage 2/§16): the multiple-testing count covers the whole search.
PRIOR_RULES = {("nba", "winner"): 1655 + 895 + 1170 + 40 + 497 + 366, ("nba", "line"): 1655 + 27 + 555 + 56 + 499,
               ("nhl", "winner"): 925 + 36 + 895 + 40 + 497, ("nhl", "reg3"): 345 + 255 + 42 + 366,
               ("nhl", "line"): 405 + 195 + 40 + 182}
STACKERS = {"stack_l2": {"l1_ratio": 0.0, "C": 0.1}, "stack_l1": {"l1_ratio": 1.0, "C": 0.05}}   # pure L2 / pure L1


def kind(market: str) -> str:
    return "winner" if market == "winner" else "reg3" if market == "reg3" else "line"


def thresholds(market: str) -> tuple:
    return REG3_T if market == "reg3" else TWO_WAY_T


def bands(market: str) -> tuple:
    return REG3_BANDS if market == "reg3" else TWO_WAY_BANDS


def primary(sport: str, market: str) -> str:
    return PRIMARY[sport].get(market, LINE_PRIMARY[sport])


# --- Data ---------------------------------------------------------------------------------------------------------------
def load(root: Path, sport: str) -> tuple[dict, dict]:
    """market -> rows (chained screen + assess), and the input fingerprints."""
    tables, prints = {}, {}
    for stage in ("screen", "assess"):
        raw = (root / "research" / "candidates" / f"oof-{sport}-{stage}.json.gz").read_bytes()
        prints[f"oof-{sport}-{stage}"] = hashlib.sha256(raw).hexdigest()[:24]
        d = json.loads(gzip.decompress(raw))
        for market, rows in d["tables"].items():
            tables.setdefault(market, []).extend(rows)
    for market, rows in tables.items():
        ids = [r["game_id"] for r in rows]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{sport}:{market}: a game appears in both stages")
        rows.sort(key=lambda r: (r["cutoff_at"], r["game_id"]))
    return tables, prints


def members_for(sport: str, rows: list) -> list:
    keys = set.intersection(*(set(r["vectors"]) for r in rows))
    keep = [k for k in keys if k not in EXCLUDED and not k.endswith("_goalie") and ":nhl_goalie" not in k]
    existing = [m for m in EXISTING[sport] if m in keep]
    return existing + sorted(k for k in keep if k not in existing)


# --- Arrays -------------------------------------------------------------------------------------------------------------
class Arrays:
    """Member probability matrices (n, 3), outcomes and seasons for one market."""

    def __init__(self, market: str, rows: list, members: list):
        self.market, self.members = market, list(members)
        self.n = len(rows)
        self.season = np.array([r["season"] for r in rows])
        self.realised = np.array([r["realised"] for r in rows])
        self.cutoff = [r["cutoff_at"] for r in rows]
        self.available = [r["result_available_at"] for r in rows]
        self.week = np.array([int(datetime.strptime(r["tipoff_at"][:10], "%Y-%m-%d").strftime("%G%V")) for r in rows])
        self.P = {m: np.array([r["vectors"][m] for r in rows], dtype=float) for m in members}
        self.allowed = (0, 1, 2) if market == "reg3" else (0, 2)

    def pick(self, V: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Picked side (-1 = abstain on an exact tie) and its probability, per row."""
        A = V[:, list(self.allowed)]
        best = A.max(axis=1)
        ties = (np.abs(A - best[:, None]) <= 1e-12).sum(axis=1) > 1
        side = np.array(self.allowed)[A.argmax(axis=1)]
        side = np.where(ties | np.isnan(best), -1, side)
        return side, np.where(side >= 0, best, np.nan)

    def outcome(self, side: np.ndarray) -> np.ndarray:
        """1 win, 0 loss, 2 push (two-way line at the exact line), -1 no pick."""
        push = (self.realised == 1) & (self.market != "reg3")
        out = np.where(side == self.realised, 1, 0)
        out = np.where(push, 2, out)
        return np.where(side < 0, -1, out)


def train_stackers(ar: Arrays, seasons: list) -> dict:
    """Season-start fits on earlier seasons' out-of-sample member predictions only; returns name -> (n, 3) matrix (NaN
    where no fit) and the fit log."""
    from sklearn.linear_model import LogisticRegression
    eps = 1e-6
    sides = list(ar.allowed)
    X = np.concatenate([np.log(np.clip(ar.P[m][:, sides], eps, 1)) for m in ar.members], axis=1)
    out, log = {}, {}
    for name, cfg in STACKERS.items():
        M = np.full((ar.n, 3), np.nan)
        fits = []
        for s in seasons[1:]:
            target = ar.season == s
            train = (ar.season < s) & np.isin(ar.realised, sides)
            start = min(c for c, t in zip(ar.cutoff, target) if t)
            if any(a > start for a, t in zip(ar.available, train) if t):
                raise ValueError("stacker training result not available before its season")
            y = ar.realised[train]
            model = LogisticRegression(l1_ratio=cfg["l1_ratio"], C=cfg["C"], solver="saga", max_iter=5000, tol=1e-4,
                                       random_state=SEED)
            model.fit(X[train], y)
            proba = model.predict_proba(X[target])
            for j, cls in enumerate(model.classes_):
                M[target, int(cls)] = proba[:, j]
            M[target] = np.nan_to_num(M[target], nan=0.0)
            coef = np.abs(model.coef_).sum(axis=0).reshape(len(ar.members), len(sides)).sum(axis=1)
            fits.append({"season": s, "training_games": int(train.sum()), "training_seasons": sorted(set(ar.season[train].tolist())),
                         "members_used": [m for m, c in zip(ar.members, coef) if c > 1e-9], "n_iter": int(np.max(model.n_iter_))})
        out[name], log[name] = M, fits
    return out, log


# --- Rules --------------------------------------------------------------------------------------------------------------
def family(members: list, candidates: set, stackers: list, market: str, budget: int = BUDGET) -> tuple[list, dict]:
    ts = thresholds(market)
    rng = random.Random(SEED)
    blocks = [("single", [[m] for m in members + stackers], ("single",))]
    blocks.append(("pair", [list(p) for p in combinations(members, 2)], ("avg", "unanimous_min")))
    tri = [list(t) for t in combinations(members, 3)]
    mixed = [t for t in tri if any(m in candidates for m in t) and any(m not in candidates for m in t)]
    rest = [t for t in tri if t not in mixed]
    rng.shuffle(mixed)
    rng.shuffle(rest)
    blocks.append(("triple", mixed + rest, ("avg", "unanimous_min", "majority")))
    larger = []
    for size in range(4, len(members) + 1):
        g = [list(c) for c in combinations(members, size)]
        rng.shuffle(g)
        larger += g
    blocks.append(("larger", larger, ("avg", "majority")))
    groups, evaluated, omitted, configs = [], {}, {}, 0
    for name, subsets, methods in blocks:
        for sub in subsets:
            cost = len(methods) * len(ts)
            if configs + cost > budget:
                omitted.setdefault(name, []).append("+".join(sub))
                continue
            configs += cost
            evaluated.setdefault(name, []).append("+".join(sub))
            for meth in methods:
                groups.append({"method": "stack" if sub[0] in stackers else meth, "members": sub})
    return groups, {"evaluated": evaluated, "omitted": omitted, "configurations": configs}


def side_conf(ar: Arrays, extra: dict, g: dict) -> tuple[np.ndarray, np.ndarray, Optional[np.ndarray], Optional[np.ndarray]]:
    """Per row: picked side, confidence (per the method's definition), the group's probability vector when it has one,
    and the supporting votes (majority)."""
    mats = [extra[m] if m in extra else ar.P[m] for m in g["members"]]
    meth = g["method"]
    if meth in ("single", "stack"):
        side, conf = ar.pick(mats[0])
        return side, conf, mats[0], None
    if meth == "avg":
        V = np.mean(mats, axis=0)
        side, conf = ar.pick(V)
        return side, conf, V, None
    picks = [ar.pick(M)[0] for M in mats]
    S = np.stack(picks, axis=1)
    rows = np.arange(ar.n)
    if meth == "unanimous_min":
        same = (S == S[:, :1]).all(axis=1) & (S[:, 0] >= 0)
        side = np.where(same, S[:, 0], -1)
        probs = np.stack([M[rows, np.clip(side, 0, 2)] for M in mats], axis=1)
        return side, np.where(same, probs.min(axis=1), np.nan), None, None
    if meth == "majority":
        k = len(mats)
        counts = np.stack([(S == s).sum(axis=1) for s in (0, 1, 2)], axis=1)
        top = counts.max(axis=1)
        leader = counts.argmax(axis=1)
        unique = (counts == top[:, None]).sum(axis=1) == 1
        ok = unique & (top * 2 > k)
        side = np.where(ok, leader, -1)
        probs = np.stack([M[rows, np.clip(side, 0, 2)] for M in mats], axis=1)
        support = S == side[:, None]
        conf = np.where(ok, (probs * support).sum(axis=1) / np.maximum(1, support.sum(axis=1)), np.nan)
        return side, conf, None, np.where(ok, top, 0)
    raise ValueError(meth)


def gid(g: dict) -> str:
    return f"{g['method']}|{'+'.join(g['members'])}"


# --- Scoring --------------------------------------------------------------------------------------------------------------
def _wilson(w: int, n: int, z: float = 1.959964):
    if n == 0:
        return None
    p = w / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [c - h, c + h]


def _curve(conf: np.ndarray, out: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Cumulative (wins, decided) of a single's picks in descending confidence within `mask` (pushes not decided)."""
    idx = np.where(mask & (out >= 0))[0]
    order = idx[np.argsort(-conf[idx], kind="stable")]
    o = out[order]
    return np.stack([np.cumsum(o == 1), np.cumsum(o != 2)], axis=1)


def _matched(curve: np.ndarray, picks: int) -> tuple[Optional[float], int]:
    if picks <= 0 or len(curve) == 0:
        return None, 0
    w, n = curve[min(picks, len(curve)) - 1]
    return (w / n if n else None), int(n)


def stats(out: np.ndarray, conf: np.ndarray, sel: np.ndarray, eligible: int, V=None, realised=None,
          three_way: bool = False) -> dict:
    o = out[sel]
    w, l, p = int((o == 1).sum()), int((o == 0).sum()), int((o == 2).sum())
    n = w + l
    s = {"eligible": eligible, "selected": int(sel.sum()), "coverage": (int(sel.sum()) / eligible) if eligible else 0.0,
         "wins": w, "losses": l, "pushes": p, "voids": 0, "pending": 0, "win_rate": (w / n) if n else None,
         "ci95": _wilson(w, n), "mean_conf": float(np.nanmean(conf[sel])) if sel.any() else None,
         "status": "ok" if n >= MIN_REPORT else "insufficient evidence"}
    if V is not None and sel.any():
        dec = sel if three_way else sel & (realised != 1)        # a two-way push has no log loss; a reg3 draw does
        if dec.any():
            r = realised[dec]
            pv = np.clip(V[dec, r], 1e-15, 1)
            onehot = np.zeros_like(V[dec])
            onehot[np.arange(len(r)), r] = 1
            s["log_loss"] = float(-np.log(pv).mean())
            s["brier"] = float(((V[dec] - onehot) ** 2).sum(axis=1).mean())
    return s


# --- Market analysis --------------------------------------------------------------------------------------------------------
def analyse(sport: str, market: str, rows: list, budget: int = BUDGET, log: Callable = print) -> tuple[dict, dict]:
    members = members_for(sport, rows)
    ar = Arrays(market, rows, members)
    seasons = sorted(set(ar.season.tolist()))
    extra, fit_log = train_stackers(ar, seasons)
    cands = {m for m in members if m not in EXISTING[sport]}
    groups, plan = family(members, cands, list(extra), market, budget)
    ts = thresholds(market)
    prim = primary(sport, market)
    k = kind(market)
    prior = PRIOR_RULES[(sport, k)]
    z = NormalDist().inv_cdf(1 - ALPHA / (plan["configurations"] + prior))
    # per-group arrays
    computed = {}
    for g in groups:
        side, conf, V, votes = side_conf(ar, extra, g)
        out = ar.outcome(side)
        computed[gid(g)] = (side, conf, V, votes, out)
    singles = {m: computed[f"single|{m}"] for m in members}
    avg_all = side_conf(ar, extra, {"method": "avg", "members": members})
    avg_all_out = ar.outcome(avg_all[0])

    def logloss(m, mask):
        r = ar.realised[mask & (ar.realised != 1 if market != "reg3" else mask)]
        P = ar.P[m][mask & (ar.realised != 1 if market != "reg3" else mask)]
        return float(-np.log(np.clip(P[np.arange(len(r)), r], 1e-15, 1)).mean())

    folds = []
    for a in seasons[1:]:
        selm, assm = ar.season < a, ar.season == a
        best = min(members, key=lambda m: (logloss(m, selm), members.index(m)))
        cur_sel = {m: _curve(singles[m][1], singles[m][4], selm) for m in members}
        cur_ass = {m: _curve(singles[m][1], singles[m][4], assm) for m in members}
        avg_ass = _curve(avg_all[1], avg_all_out, assm)
        n_sel, n_ass = int(selm.sum()), int(assm.sum())
        scored = []
        for g in groups:
            side, conf, V, votes, out = computed[gid(g)]
            for t in ts:
                pickm = (side >= 0) & (conf + 1e-9 >= t)
                s = pickm & selm
                o = out[s]
                w, l = int((o == 1).sum()), int((o == 0).sum())
                n = w + l
                if n == 0:
                    scored.append((g, t, 0, None, None, None, int(s.sum())))
                    continue
                acc = w / n
                cmp_acc, cmp_n = _matched(cur_sel[best], int(s.sum()))
                lift = se = lower = None
                if cmp_acc is not None:
                    lift = acc - cmp_acc
                    se = math.sqrt(acc * (1 - acc) / n + cmp_acc * (1 - cmp_acc) / max(1, cmp_n)) or 1e-9
                    lower = lift - z * se
                scored.append((g, t, n, acc, lift, lower, int(s.sum())))
        big = [x for x in scored if x[2] >= MIN_PICKS and x[6] / n_sel >= MIN_COVERAGE and x[3] is not None]
        frozen = {}
        for g, t, n, acc, lift, lower, picks in big:
            if lower is not None and lower > 0:
                cons = [m for m in g["members"] if m in members]
                ok = True
                for m in cons:
                    ca, _ = _matched(cur_sel[m], picks)
                    ok &= ca is not None and acc > ca
                if ok:
                    frozen.setdefault((gid(g), t), "qualified")
        if big:
            frozen.setdefault((gid(max(big, key=lambda x: (x[3], x[2]))[0]), max(big, key=lambda x: (x[3], x[2]))[1]),
                              "highest accuracy (≥100 picks, ≥5%)")
            lifted = [x for x in big if x[4] is not None]
            if lifted:
                b = max(lifted, key=lambda x: (x[4], x[2]))
                frozen.setdefault((gid(b[0]), b[1]), "top point lift")
        for t in ts:
            at = [x for x in scored if x[1] == t and x[2] >= MIN_PICKS and x[4] is not None]
            if at:
                b = max(at, key=lambda x: (x[4], x[2]))
                frozen.setdefault((gid(b[0]), t), f"best lift at ≥{t:.2f}")
        assessed = []
        by = {gid(g): g for g in groups}
        sel_by = {(gid(x[0]), x[1]): x for x in scored}
        for (key, t), why in frozen.items():
            g = by[key]
            side, conf, V, votes, out = computed[key]
            pickm = (side >= 0) & (conf + 1e-9 >= t) & assm
            st = stats(out, conf, pickm, n_ass, V, ar.realised, market == "reg3")
            cons = [m for m in g["members"] if m in members]
            strongest = min(cons, key=lambda m: (logloss(m, selm), members.index(m))) if cons else None
            st["matched"] = {"best_prior_single": _matched(cur_ass[best], st["selected"])[0],
                             "primary": _matched(cur_ass[prim], st["selected"])[0] if prim in cur_ass else None,
                             "strongest_constituent": _matched(cur_ass[strongest], st["selected"])[0] if strongest else None,
                             "all_member_average": _matched(avg_ass, st["selected"])[0]}
            x = sel_by[(key, t)]
            assessed.append({"group": key, "threshold": t, "why": why, "selection": {"decided": x[2], "win_rate": x[3],
                             "lift": x[4], "lower": x[5], "picks": x[6]}, "assess": st})
        folds.append({"select_seasons": [s for s in seasons if s < a], "assess_season": a, "best_single": best,
                      "configurations": len(scored), "bonferroni_count": plan["configurations"] + prior, "z": z,
                      "qualified": [f"{k_}@{t_}" for (k_, t_), w_ in frozen.items() if w_ == "qualified"],
                      "assessed": assessed})
    # Per-group tracking table: every evaluated group × threshold and band, per season (descriptive).
    best_by_season = {f["assess_season"]: f["best_single"] for f in folds}
    table = []
    for g in groups:
        key = gid(g)
        side, conf, V, votes, out = computed[key]
        for s in seasons:
            m_s = ar.season == s
            elig = int(m_s.sum())
            ref = best_by_season.get(s)
            curve = _curve(singles[ref][1], singles[ref][4], m_s) if ref else None
            for lab, lo, hi in [(f">={t:.2f}", t, 9.0) for t in ts] + [(f"[{lo:.3f},{hi:.3f})", lo, hi) for lo, hi in bands(market)]:
                pickm = (side >= 0) & (conf + 1e-9 >= lo) & (conf < hi - 1e-12 if hi < 9 else True) & m_s
                st = stats(out, conf, pickm, elig, V, ar.realised, market == "reg3")
                if st["selected"] == 0:
                    continue
                st["best_prior_single"] = ref
                st["best_prior_single_matched"] = _matched(curve, st["selected"])[0] if curve is not None else None
                if votes is not None:
                    st["votes"] = {str(v): int(((votes == v) & pickm).sum()) for v in sorted(set(votes[pickm].tolist()))}
                    st["members_total"] = len(g["members"])
                table.append({"group": key, "method": g["method"], "members": g["members"], "season": s, "level": lab, **st})
    report = {"sport": sport, "market": market, "kind": k, "members": members, "candidates": sorted(cands),
              "primary": prim, "games": ar.n, "seasons": seasons, "plan": plan, "stackers": fit_log,
              "thresholds": list(ts), "bands": [list(b) for b in bands(market)], "folds": folds,
              "line": None if market in ("winner", "reg3") else "pregame research line (the primary model's own line)"}
    log(f"{sport}:{market}: {ar.n} games, {len(members)} members, {plan['configurations']} configurations, "
        f"qualified per fold {[len(f['qualified']) for f in folds]}, {len(table)} group rows")
    return report, {"sport": sport, "market": market, "rows": table}


def _one(args):
    root, sport, market, budget = args
    tables, _ = load(Path(root), sport)
    return analyse(sport, market, tables[market], budget)


def run(out_dir: Optional[Path] = None, log: Callable[[str], None] = print, budget: int = BUDGET,
        markets: Optional[list] = None) -> dict:
    started = utc_now()
    root = config.data_dir()
    out_dir = out_dir or root / "reports" / f"discovery-{started.replace(':', '')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs, prints = [], {}
    for sport in ("nhl", "nba"):
        tables, p = load(root, sport)
        prints.update(p)
        for market in tables:
            if markets is None or f"{sport}:{market}" in markets:
                jobs.append((str(root), sport, market, budget))
    report = {"kind": "discovery", "study": STUDY, "started_at": started, "exploratory": True, "inputs": prints,
              "budget": budget, "markets": {}}
    groups = {}
    with ProcessPoolExecutor(max_workers=min(6, len(jobs))) as ex:
        for rep, tab in ex.map(_one, jobs):
            key = f"{rep['sport']}:{rep['market']}"
            report["markets"][key] = rep
            groups[key] = tab
            log(f"{key}: done")
    report["finished_at"] = utc_now()
    (out_dir / "report.json").write_text(json.dumps(report, indent=1, default=float))
    for key, tab in groups.items():
        with gzip.open(out_dir / f"groups-{key.replace(':', '-')}.json.gz", "wt") as fh:
            json.dump(tab, fh, default=float)
    report["output_dir"] = str(out_dir)
    log(f"wrote {out_dir}")
    return report
