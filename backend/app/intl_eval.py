"""International (national-team) soccer: data, frozen protocol, models, development run, seal and one-shot test.

Protocol: docs/INTERNATIONAL_PROTOCOL.md (v1, sha256 dfd3f400…, written before this module). Data: martj42/
international_results (CC0) at a pinned commit, cached with sha256. Target: the 90-minute home/draw/away result.
Reuses metrics.log_loss_multi / brier_multi and backtest.block_bootstrap / seal. The reserved years (2022-2025) are read
only through a valid sealed candidate with an access log written first; 2026+ is never evaluated here.
"""
from __future__ import annotations

import bisect
import csv
import hashlib
import io
import json
import math
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from . import backtest, config, metrics

PROTOCOL_SHA256 = "1c5aee54648795fa3137ac4547ae23394478eb80a3803377d2f50904484608ee"
REPO = "martj42/international_results"
FILES = ("results.csv", "goalscorers.csv", "shootouts.csv")
WARMUP_END, DEV_TUNE, DEV_CAL, VALIDATION, TEST = 2009, range(2010, 2016), range(2016, 2018), range(2018, 2022), range(2022, 2026)
LAST_OPEN = 2021
FREQ_YEARS = range(1990, 2010)
GROUPS = {
    "world_cup": ("FIFA World Cup",),
    "continental_finals": ("UEFA Euro", "Copa América", "African Cup of Nations", "AFC Asian Cup", "Gold Cup", "OFC Nations Cup"),
    "wc_qualification": ("FIFA World Cup qualification",),
    "continental_qualification": ("UEFA Euro qualification", "African Cup of Nations qualification",
                                  "AFC Asian Cup qualification", "Gold Cup qualification", "OFC Nations Cup qualification"),
    "nations_league": ("UEFA Nations League", "CONCACAF Nations League"),
    "friendly": ("Friendly",),
}
GROUP_OF = {t: g for g, ts in GROUPS.items() for t in ts}
WEIGHT = {"world_cup": 60, "continental_finals": 50, "wc_qualification": 40, "continental_qualification": 40,
          "nations_league": 40, "friendly": 20, None: 30}
GRID = [(k, h, mov) for k in (10, 15, 20) for h in (50, 75, 100) for mov in (False, True)]   # tie-break order
SEED, RESAMPLES = 20260930, 2000
RUN_START, RUN_DONE = "test run: start", "test run: complete"


class Locked(PermissionError):
    pass


class FrozenMismatch(RuntimeError):
    pass


@dataclass(frozen=True)
class Match:
    mid: str
    day: str           # match date (local, as listed)
    home: str
    away: str
    neutral: bool
    tournament: str
    group: Optional[str]
    hs: Optional[int]
    as_: Optional[int]
    label: Optional[int]        # 0 home, 1 draw, 2 away after 90 minutes; None = not a usable result
    exclusion: Optional[str]


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def cutoff_for(day: str) -> str:
    return f"{day}T06:00:00Z"


def available_at(day: str) -> str:
    return date.fromordinal(date.fromisoformat(day).toordinal() + 1).isoformat() + "T06:00:00Z"


def is_final_tournament(t: str) -> bool:
    return not any(s in t for s in ("qualification", "Friendly", "Nations League"))


def parse(files: dict[str, bytes]) -> list[Match]:
    """Matches with their 90-minute label and exclusion reason, by the protocol's label rules."""
    rd = lambda n: list(csv.DictReader(io.StringIO(files[n].decode("utf-8"))))
    key = lambda r: (r["date"], r["home_team"], r["away_team"])
    minutes = defaultdict(list)
    for g in rd("goalscorers.csv"):
        minutes[key(g)].append(None if g["minute"] in ("", "NA") else float(g["minute"]))
    shootouts = {key(s) for s in rd("shootouts.csv")}
    results = rd("results.csv")
    count = defaultdict(int)
    for r in results:
        count[key(r)] += 1
    out, nth = [], defaultdict(int)
    for r in results:
        k = key(r)
        nth[k] += 1
        mid = "intl:" + sha(json.dumps([*k, nth[k]]).encode())[:16]
        t = r["tournament"]
        if count[k] > 1:                       # protocol amendment 1: minutes/shootouts cannot be attributed
            out.append(Match(mid, r["date"], r["home_team"], r["away_team"], r["neutral"].upper() == "TRUE", t,
                             GROUP_OF.get(t), None, None, None, "duplicate listing: goal minutes/shootout cannot be attributed"))
            continue
        neutral = r["neutral"].upper() == "TRUE"
        if r["home_score"] in ("", "NA") or r["away_score"] in ("", "NA"):
            out.append(Match(mid, r["date"], r["home_team"], r["away_team"], neutral, t, GROUP_OF.get(t), None, None, None,
                             "no score"))
            continue
        hs, as_ = int(r["home_score"]), int(r["away_score"])
        m = minutes.get(k, [])
        known = [x for x in m if x is not None]
        label, why = None, None
        if k in shootouts or any(x >= 106 for x in known):
            label = 1                                        # extra time was played: level after 90 minutes
        elif any(90 < x < 106 for x in known):
            why = "goal at 91-105 only: stoppage or extra time unknown"
        elif is_final_tournament(t) and (len(known) != hs + as_ or len(m) != len(known)):
            why = "final-tournament match with incomplete goal minutes: extra time not ruled out"
        else:
            label = 0 if hs > as_ else (1 if hs == as_ else 2)
        out.append(Match(mid, r["date"], r["home_team"], r["away_team"], neutral, t, GROUP_OF.get(t), hs, as_, label, why))
    return sorted(out, key=lambda m: (m.day, m.mid))


def year(m: Match) -> int:
    return int(m.day[:4])


def scored(ms: list[Match], years) -> list[Match]:
    ys = set(years)
    return [m for m in ms if m.label is not None and m.group is not None and year(m) in ys]


# --- Models ---------------------------------------------------------------------------------------------------------------
class Frequency:
    name = "frequency"

    def __init__(self, shares: dict):
        self.shares = shares

    def observe(self, m: Match) -> None:
        pass

    def predict(self, m: Match) -> tuple:
        return tuple(self.shares["neutral" if m.neutral else "home"])


def fit_frequency(ms: list[Match]) -> dict:
    out = {}
    for venue in ("home", "neutral"):
        n = [0, 0, 0]
        for m in scored(ms, FREQ_YEARS):
            if m.neutral == (venue == "neutral"):
                n[m.label] += 1
        out[venue] = [x / sum(n) for x in n]
    return out


def _sigmoid(z):
    return 1 / (1 + math.exp(-z))


def draw_probs(dm: dict, x: float) -> tuple:
    """Ordered logit on x = dr/400: P(away) = σ(c1 − s·x), P(away or draw) = σ(c2 − s·x)."""
    s, c1, c2 = dm["slope"], dm["c1"], dm["c2"]
    pa = _sigmoid(c1 - s * x)
    pad = _sigmoid(c2 - s * x)
    p = (1 - pad, pad - pa, pa)
    eps = 1e-6
    p = tuple(max(eps, v) for v in p)
    t = sum(p)
    return tuple(v / t for v in p)


class Elo:
    name = "elo"

    def __init__(self, k: float, h: float, mov: bool, dm: Optional[dict] = None):
        self.k, self.h, self.mov, self.dm = k, h, mov, dm
        self.r: dict[str, float] = {}

    def x(self, m: Match) -> float:
        return (self.r.get(m.home, 1500.0) + (0 if m.neutral else self.h) - self.r.get(m.away, 1500.0)) / 400

    def observe(self, m: Match) -> None:
        x = self.x(m)
        e = 1 / (1 + 10 ** (-x))
        a = 1.0 if m.hs > m.as_ else (0.5 if m.hs == m.as_ else 0.0)
        if m.label == 1 and m.hs != m.as_:                   # extra time: the 90-minute result was a draw
            a = 0.5
        d = abs(m.hs - m.as_) if m.label != 1 else 0
        g = 1.0 if not self.mov or d <= 1 else (1.5 if d == 2 else (11 + d) / 8)
        delta = self.k * WEIGHT[m.group] / 40 * g * (a - e)
        self.r[m.home] = self.r.get(m.home, 1500.0) + delta
        self.r[m.away] = self.r.get(m.away, 1500.0) - delta

    def predict(self, m: Match) -> tuple:
        return draw_probs(self.dm, self.x(m)) if self.dm else (1 / 3, 1 / 3, 1 / 3)


def walk(model, ms: list[Match], years, *, upto: Optional[str] = None) -> list[dict]:
    """Date order; before forecasting date D the model has observed every usable result of dates < D (available the day
    after); same-day results never. Returns one record per scored match of `years`."""
    ys, out = set(years), []
    targets = defaultdict(list)
    for m in ms:
        if m.label is not None and m.group is not None and year(m) in ys and (upto is None or m.day <= upto):
            targets[m.day].append(m)
    inputs = [m for m in ms if m.label is not None]
    days = [m.day for m in inputs]
    i = 0
    for d in sorted(targets):
        j = bisect.bisect_left(days, d)                      # strictly earlier dates only
        for m in inputs[i:j]:
            if available_at(m.day) > cutoff_for(d):
                raise backtest.LeakageError(f"{m.mid} not available before {d}")
            model.observe(m)
        i = j
        for m in targets[d]:
            p = model.predict(m)
            out.append({"mid": m.mid, "group": m.group, "year": year(m), "day": d, "cutoff_at": cutoff_for(d),
                        "p": tuple(float(v) for v in p), "label": m.label})
    return out


def fit_draw_model(rows: list[tuple[float, int]]) -> dict:
    import numpy as np
    from scipy.optimize import minimize
    x = np.array([r[0] for r in rows])
    y = np.array([r[1] for r in rows])

    def nll(t):
        s, c1, w = t
        c2 = c1 + math.exp(w)
        pa = 1 / (1 + np.exp(-(c1 - s * x)))
        pad = 1 / (1 + np.exp(-(c2 - s * x)))
        p = np.stack([1 - pad, pad - pa, pa], axis=1).clip(1e-6, None)
        p = p / p.sum(axis=1, keepdims=True)
        return float(-np.log(p[np.arange(len(y)), y]).mean())
    r = minimize(nll, (2.0, -0.5, 0.0), method="Nelder-Mead", options={"xatol": 1e-8, "fatol": 1e-10, "maxiter": 6000})
    s, c1, w = r.x
    return {"slope": float(s), "c1": float(c1), "c2": float(c1 + math.exp(w))}


def _elo_x_rows(ms, cfg, years) -> list[tuple[float, int]]:
    model, rows = Elo(*cfg), []
    model.predict = lambda m: (rows.append((model.x(m), m.label)) or (1 / 3, 1 / 3, 1 / 3))
    walk(model, ms, years)
    return rows


def summary(preds: list[dict], boot: bool = True) -> dict:
    if not preds:
        return {"n": 0}
    v, y = [p["p"] for p in preds], [p["label"] for p in preds]
    out = {"n": len(preds), "log_loss": metrics.log_loss_multi(v, y), "brier": metrics.brier_multi(v, y),
           "accuracy_most_likely": sum(max(range(3), key=lambda k: a[k]) == b for a, b in zip(v, y)) / len(y),
           "mean_predicted": [sum(a[k] for a in v) / len(v) for k in range(3)],
           "observed": [sum(b == k for b in y) / len(y) for k in range(3)], "ece": [ece(preds, k) for k in range(3)]}
    if boot:
        b = backtest.block_bootstrap([loss(p) for p in preds], [p["cutoff_at"] for p in preds])
        out["log_loss_ci95"] = b["ci95"]
    return out


def loss(p: dict) -> float:
    return -math.log(min(1 - 1e-15, max(1e-15, p["p"][p["label"]])))


def ece(preds, k, bins=10) -> float:
    g = defaultdict(list)
    for p in preds:
        g[min(bins - 1, int(p["p"][k] * bins))].append((p["p"][k], p["label"] == k))
    return sum(len(x) / len(preds) * abs(sum(q for q, _ in x) / len(x) - sum(o for _, o in x) / len(x)) for x in g.values())


def paired(a: list[dict], b: list[dict]) -> dict:
    pb = {p["mid"]: p for p in b}
    d = [loss(p) - loss(pb[p["mid"]]) for p in a]
    return backtest.block_bootstrap(d, [p["cutoff_at"] for p in a])


def breakdown(preds: list[dict], base: Optional[list[dict]] = None) -> dict:
    by_g, by_y = defaultdict(list), defaultdict(list)
    for p in preds:
        by_g[p["group"]].append(p)
        by_y[p["year"]].append(p)
    out = {"overall": summary(preds), "by_group": {g: summary(v) for g, v in sorted(by_g.items())},
           "by_year": {y: summary(v, boot=False) for y, v in sorted(by_y.items())}}
    if base is not None:
        bb = defaultdict(list)
        for p in base:
            bb[p["group"]].append(p)
        out["paired_vs_frequency"] = {"overall": paired(preds, base),
                                      "by_group": {g: paired(v, bb[g]) for g, v in sorted(by_g.items())}}
    return out


# --- Development, seal, test ---------------------------------------------------------------------------------------------
def load(cache: Path, *, through: int, sealed: Optional[dict] = None, access_log: Optional[Path] = None,
         purpose: str = "") -> tuple[list[Match], dict]:
    """Matches of years <= `through` from the cached pinned files (sha256-checked against the manifest). Years after
    LAST_OPEN need a valid seal and an access log written first; years after 2025 are never evaluated."""
    if through > TEST[-1]:
        raise Locked("years after 2025 are prospective: never evaluated")
    if through > LAST_OPEN:
        if not valid_seal(sealed) or access_log is None:
            raise Locked("reserved years need a valid sealed candidate and an access log")
        log(access_log, {"candidate_sha256": sealed["sha256"], "through": through, "purpose": purpose})
    manifest = json.loads((cache / "manifest.json").read_text())
    files = {}
    for n in FILES:
        b = (cache / n).read_bytes()
        if sha(b) != manifest["files"][n]:
            raise FrozenMismatch(f"{n} differs from its manifest")
        files[n] = b
    return [m for m in parse(files) if year(m) <= through], manifest


def log(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps({"at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **entry}) + "\n")


def code_digest() -> str:
    return sha(Path(__file__).read_bytes())


def valid_seal(c: Optional[dict]) -> bool:
    return bool(c) and backtest.seal(c)["sha256"] == c.get("sha256")


def develop(cache: Path) -> dict:
    """Tune on 2010-2015, choose on 2018-2021 by the protocol; returns the report and the sealed candidate."""
    ms, manifest = load(cache, through=LAST_OPEN)
    freq = fit_frequency(ms)
    rows = []
    for cfg in GRID:
        dm = fit_draw_model(_elo_x_rows(ms, cfg, DEV_TUNE))
        preds = walk(Elo(*cfg, dm), ms, DEV_TUNE)
        rows.append((summary(preds, boot=False)["log_loss"], cfg, dm))
    best = min(rows, key=lambda r: (r[0], GRID.index(r[1])))
    ll, cfg, dm = best
    elo_val = walk(Elo(*cfg, dm), ms, VALIDATION)
    freq_val = walk(Frequency(freq), ms, VALIDATION)
    cmp = paired(elo_val, freq_val)
    years_better = sum(1 for y in VALIDATION if
                       sum(loss(p) for p in elo_val if p["year"] == y) < sum(loss(p) for p in freq_val if p["year"] == y))
    chosen = "elo" if (cmp["mean"] < 0 and cmp["ci95"][1] < 0 and years_better >= 3) else "frequency"
    candidate = {"model": chosen, "config": {"k": cfg[0], "h": cfg[1], "mov": cfg[2]} if chosen == "elo" else None,
                 "draw_model": dm if chosen == "elo" else None, "frequency": freq, "protocol_sha256": PROTOCOL_SHA256,
                 "data": manifest, "code_sha256": code_digest(),
                 "sealed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    candidate = backtest.seal(candidate)
    report = {"grid": [{"k": c[0], "h": c[1], "mov": c[2], "dev_tune_log_loss": l} for l, c, _ in rows],
              "selected": {"k": cfg[0], "h": cfg[1], "mov": cfg[2], "dev_tune_log_loss": ll, "draw_model": dm},
              "on_grid_edge": [n for n, v, lo, hi in (("k", cfg[0], 10, 20), ("h", cfg[1], 50, 100)) if v in (lo, hi)],
              "validation": {"elo": breakdown(elo_val), "frequency": breakdown(freq_val), "paired": cmp,
                             "years_elo_better": years_better},
              "development_calibration": {"elo": breakdown(walk(Elo(*cfg, dm), ms, DEV_CAL), None)},
              "choice": chosen, "frequency": freq}
    return {"report": report, "sealed": candidate}


def model_of(sealed: dict):
    if sealed["model"] == "elo":
        c = sealed["config"]
        return Elo(c["k"], c["h"], c["mov"], sealed["draw_model"])
    return Frequency(sealed["frequency"])


def run_test(cache: Path, sealed: dict, *, access_log: Path, out_dir: Path) -> dict:
    """The single logged reserved-test run (2022-2025)."""
    if not valid_seal(sealed):
        raise Locked("invalid sealed candidate")
    if out_dir.exists():
        raise Locked(f"{out_dir} exists")
    if access_log.exists() and any(json.loads(l).get("purpose") == RUN_START for l in access_log.read_text().splitlines()):
        raise Locked("the reserved test already ran from this access log")
    if sealed["code_sha256"] != code_digest() or sealed["protocol_sha256"] != PROTOCOL_SHA256:
        raise FrozenMismatch("code or protocol changed since the seal")
    log(access_log, {"candidate_sha256": sealed["sha256"], "through": TEST[-1], "purpose": RUN_START})
    ms, manifest = load(cache, through=TEST[-1], sealed=sealed, access_log=access_log, purpose="test run: read reserved years")
    if manifest != sealed["data"]:
        raise FrozenMismatch("data manifest changed since the seal")
    preds = walk(model_of(sealed), ms, TEST)
    base = walk(Frequency(sealed["frequency"]), ms, TEST)
    report = {"candidate_sha256": sealed["sha256"], "model": sealed["model"], "years": list(TEST),
              "test": breakdown(preds, base)}
    out_dir.mkdir(parents=True)
    (out_dir / "test_report.json").write_text(json.dumps(report, indent=1, default=str))
    log(access_log, {"candidate_sha256": sealed["sha256"], "through": TEST[-1], "purpose": RUN_DONE})
    return report


def read_test(report: dict) -> dict:
    """The protocol's fixed reading (R1-R3)."""
    t = report["test"]
    po = t["paired_vs_frequency"]["overall"]
    r1 = po["mean"] < 0 and po["ci95"][1] < 0
    r2 = all(e <= 0.05 for e in t["overall"]["ece"])
    scope = []
    for g, pg in t["paired_vs_frequency"]["by_group"].items():
        if r1 and r2 and pg["mean"] < 0 and pg["ci95"][1] < 0 and t["by_group"][g]["n"] >= 300:
            scope.append(g)
    return {"R1": r1, "R2": r2, "launch_scope": scope, "verdict": ("SUPPORTS LAUNCH (model quality only) for " +
            ", ".join(scope)) if scope else "DOES NOT SUPPORT LAUNCH"}


def cache_dir() -> Path:
    return config.data_dir() / "research" / "international"


def download(commit: Optional[str] = None, *, get=None) -> Path:
    """Downloads the three CSV files at `commit` (default: the repository's current HEAD, resolved and recorded) into
    cache_dir()/<commit>/ with a sha256 manifest. Never overwrites an existing pinned folder."""
    import httpx
    get = get or (lambda url: httpx.get(url, timeout=60, follow_redirects=True).raise_for_status().content)
    if commit is None:
        commit = json.loads(get(f"https://api.github.com/repos/{REPO}/commits/master"))["sha"]
    target = cache_dir() / commit
    if (target / "manifest.json").exists():
        return target
    target.mkdir(parents=True, exist_ok=True)
    files = {}
    for n in FILES:
        b = get(f"https://raw.githubusercontent.com/{REPO}/{commit}/{n}")
        (target / n).write_bytes(b)
        files[n] = sha(b)
    (target / "manifest.json").write_text(json.dumps({"repo": REPO, "commit": commit, "files": files,
                                                      "retrieved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
                                                     indent=1))
    return target


def access_log_path() -> Path:
    return cache_dir() / "access.jsonl"


def main(argv=None) -> int:
    """python -m app.intl_eval download [--commit SHA] | develop --cache DIR --out DIR | test --cache DIR --sealed FILE --out DIR"""
    import argparse
    p = argparse.ArgumentParser(prog="python -m app.intl_eval")
    p.add_argument("action", choices=("download", "develop", "test"))
    p.add_argument("--commit")
    p.add_argument("--cache", type=Path)
    p.add_argument("--sealed", type=Path)
    p.add_argument("--out", type=Path)
    a = p.parse_args(argv)
    if a.action == "download":
        print(json.dumps({"cache": str(download(a.commit))}))
        return 0
    if a.action == "develop":
        if a.out.exists():
            p.error(f"{a.out} exists")
        res = develop(a.cache)
        a.out.mkdir(parents=True)
        (a.out / "development_report.json").write_text(json.dumps(res["report"], indent=1, default=str))
        (a.out / "sealed_candidate.json").write_text(json.dumps(res["sealed"], indent=1))
        print(json.dumps({"choice": res["report"]["choice"], "selected": res["report"]["selected"],
                          "validation_paired": res["report"]["validation"]["paired"],
                          "years_elo_better": res["report"]["validation"]["years_elo_better"],
                          "sealed_sha256": res["sealed"]["sha256"]}, indent=1, default=str))
        return 0
    rep = run_test(a.cache, json.loads(a.sealed.read_text()), access_log=access_log_path(), out_dir=a.out)
    print(json.dumps(read_test(rep), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
