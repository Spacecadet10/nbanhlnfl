"""Prospective soccer SPEC challengers: new specs on the served Poisson goals joint, not refits of the sealed models.

The served goals forecast (soccer_markets_live, sealed Poisson candidate) is never edited, refit or replaced. Each challenger
changes ONE thing that already exists in the codebase (soccer_markets_eval, protocol grid), point in time, and is priced for
the served soccer markets a goals joint gives (1X2 regulation, match goals, home and away goals, both teams to score; double
chance and draw no bet are exact functions of the 1X2 triple and are derived at read time, never stored):

* dixon_coles   the Dixon-Coles low-score correction (ev.dc_joint); ONE rho fitted by maximum likelihood on the labelled inputs
                of the cutoff's own window (results available by the cutoff only; in-sample for the cutoff's fit).
* half_life_days  time-decay half life of the fitting weights, a value from the protocol grid other than the served 365.
* dispersion    "league": negative-binomial goal counts (ev.nb_joint) with one k per competition fitted on the same window
                (pooled k when a competition has fewer than MIN_LEAGUE_OBS labelled inputs).

Not here: corners (skipped), Asian handicap (research only, blocked on the preregistration), international (insufficient
evidence). Quarter lines cannot be stored as win/push/loss, so a served quarter line is skipped and counted.

Point in time: the cutoff is the served one (06:00 UTC of the match's local date, ev.cutoff_for); inputs are labelled results
with available_at <= cutoff and an earlier match date. A run is refused, recording nothing, under the served completeness checks
(soccer_markets_live.check). A match is priced only when the served goals forecast exists, at the lines it serves (the model's
fair line plus bookmaker total-goals lines); rows go to the challenger store only and the sport store is opened read-only.
A row's cutoff_at is that 06:00 UTC information time (the run time is generated_at, the write time recorded_at).

The 1X2 row compares the goals-joint 1X2 with the served Elo 1X2: a different model family AND the spec change together, so a
1X2 difference cannot be attributed to the spec alone (the goals markets compare like with like against the served Poisson).

  python -m app.learning_challengers record --sport soccer [--dry-run] [--now T --store PATH]
"""
import bisect
import hashlib
import json
import math
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Optional

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.stats import poisson as _poisson
from scipy.special import gammaln

from . import learning_challengers as lc, soccer_live, soccer_markets as sm, soccer_markets_eval as ev, soccer_markets_live as sml
from .backtest import LeakageError
from .db import utc_now

FAMILY = "goals_spec"
EXTRA_KEYS = ("half_life_days", "dixon_coles", "dispersion")
DISPERSIONS = ("league",)
MIN_LEAGUE_OBS = 300
MARKET_OF = {"goals_total": "soccer_goals_total", "goals_home": "soccer_goals_home", "goals_away": "soccer_goals_away"}
RHO_BOUNDS = (-0.3, 0.3)
LOGK_BOUNDS = (math.log(0.5), math.log(10000))


@dataclass(frozen=True)
class Spec:
    name: str
    sport: str
    family: str
    head: str
    features: str
    role: str
    why: str
    refit: str = "cutoff"
    extra: Optional[dict] = None


def spec(raw: dict, sport: str = "soccer") -> Spec:
    if not isinstance(raw, dict) or set(raw) - set(Spec.__dataclass_fields__) or \
            not {"name", "sport", "family", "head", "features", "role", "why", "extra"} <= set(raw):
        raise lc.PolicyError(f"spec fields must be within {sorted(Spec.__dataclass_fields__)} and include extra")
    if raw["sport"] != sport:
        raise lc.PolicyError(f"spec {raw['name']}: sport {raw['sport']!r} is not the policy's sport {sport!r}")
    if raw["family"] != FAMILY:
        raise lc.PolicyError(f"spec {raw['name']}: family must be {FAMILY!r}")
    if raw.get("refit", "cutoff") != "cutoff":
        raise lc.PolicyError(f"spec {raw['name']}: only the per-cutoff refit is implemented")
    x = raw["extra"]
    if not isinstance(x, dict) or set(x) - set(EXTRA_KEYS):
        raise lc.PolicyError(f"spec {raw['name']}: extra may hold only {EXTRA_KEYS}")
    if not x:
        raise lc.PolicyError(f"spec {raw['name']}: extra needs at least one change from the served model")
    if "half_life_days" in x and x["half_life_days"] not in ev.GRID["goals"]["half_life_days"]:
        raise lc.PolicyError(f"spec {raw['name']}: half_life_days must be a protocol grid value {ev.GRID['goals']['half_life_days']}")
    if "dixon_coles" in x and x["dixon_coles"] is not True:
        raise lc.PolicyError(f"spec {raw['name']}: dixon_coles must be true")
    if "dispersion" in x and x["dispersion"] not in DISPERSIONS:
        raise lc.PolicyError(f"spec {raw['name']}: dispersion must be one of {DISPERSIONS}")
    return Spec(**raw)


# --- Point-in-time inputs and fits --------------------------------------------------------------------------------------
def day_inputs(data: ev.Data, day: str) -> list:
    """Labelled results available by the day's served cutoff with a match date before `day` (as ev.walk selects them)."""
    cutoff = ev.cutoff_for(day)
    lab, avail = ev._inputs_index(data)
    j = bisect.bisect_right(avail, cutoff)
    inputs = [o for o in lab[:j] if o.local_date < day]
    if any(o.available_at > cutoff for o in inputs):
        raise LeakageError(f"an input of {day} is available after its cutoff")
    return inputs


def _window(inputs: list, day: str) -> list:
    lo = date.fromisoformat(day).toordinal() - ev.WINDOW_DAYS
    return [o for o in inputs if date.fromisoformat(o.local_date).toordinal() >= lo]


def fit_rho(means: np.ndarray, ys: np.ndarray) -> float:
    """Maximum-likelihood Dixon-Coles rho over (mh, ma, h, a) rows: the cells (0,0),(0,1),(1,0),(1,1) of the Poisson joint
    scaled as ev.dc_joint does (per-match clipped rho, renormalised); one rho for every competition."""
    mh, ma, h, a = means[:, 0], means[:, 1], ys[:, 0], ys[:, 1]
    cap = ev.CAP["goals"]
    ph = [_poisson.pmf(i, mh) for i in (0, 1)]
    pa = [_poisson.pmf(i, ma) for i in (0, 1)]
    cell = np.array([float(ev.poisson_marginal(m, cap)[min(int(x), cap)]) * float(ev.poisson_marginal(n, cap)[min(int(y), cap)])
                     for m, n, x, y in zip(mh, ma, h, a)])
    lo = np.maximum(-1 / mh, -1 / ma)
    hi = np.minimum(1 / (mh * ma), 1.0)

    def loss(rho):
        r = np.minimum(np.maximum(rho, lo), hi)
        f = np.ones_like(mh)
        f = np.where((h == 0) & (a == 0), 1 - mh * ma * r, f)
        f = np.where((h == 0) & (a == 1), 1 + mh * r, f)
        f = np.where((h == 1) & (a == 0), 1 + ma * r, f)
        f = np.where((h == 1) & (a == 1), 1 - r, f)
        norm = 1 + ph[0] * pa[0] * (-mh * ma * r) + ph[0] * pa[1] * (mh * r) + ph[1] * pa[0] * (ma * r) + ph[1] * pa[1] * (-r)
        return float(np.mean(-np.log(np.maximum(ev.CLIP, cell * f / norm))))
    return float(minimize_scalar(loss, bounds=RHO_BOUNDS, method="bounded", options={"xatol": 1e-6}).x)


def fit_k(means: np.ndarray, ys: np.ndarray) -> float:
    """Maximum-likelihood negative-binomial k (Var = m + m^2/k) over both sides' counts, one k."""
    cap = ev.CAP["goals"]
    m = np.concatenate([means[:, 0], means[:, 1]])
    y = np.concatenate([ys[:, 0], ys[:, 1]])

    def loss(logk):
        k = math.exp(logk)
        logp = (gammaln(y + k) - gammaln(k) - gammaln(y + 1) + k * np.log(k / (k + m)) + y * np.log(m / (k + m)))
        p = np.exp(logp)
        tail = y >= cap
        if tail.any():
            p = p.copy()
            p[tail] = [max(0.0, float(ev.nb_marginal(mm, k, cap)[-1])) for mm in m[tail]]
        return float(np.mean(-np.log(np.maximum(ev.CLIP, p))))
    return float(math.exp(minimize_scalar(loss, bounds=LOGK_BOUNDS, method="bounded", options={"xatol": 1e-6}).x))


def joints(sp: Spec, sealed: dict, data: ev.Data, day: str, targets: list) -> dict:
    """{match id: {joint, mh, ma, rho?, k?}} of the spec's goals joints for `targets` dated `day`, from one cutoff fit."""
    cfg = ev.frozen_config(sealed)
    if "half_life_days" in sp.extra:
        cfg = replace(cfg, half_life_days=sp.extra["half_life_days"])
    cutoff = ev.cutoff_for(day)
    inputs = day_inputs(data, day)
    if any(o.mid in {t.mid for t in targets} for o in inputs):
        raise LeakageError(f"a match of {day} is among its own inputs")
    params = ev.fit_strengths("goals", cfg, data.members, inputs, cutoff, targets)
    cap = ev.CAP["goals"]
    out = {t.mid: dict(zip(("mh", "ma"), ev.means(params, t))) for t in targets}
    win = _window(inputs, day)
    train = np.array([ev.means(params, o) for o in win]) if win else np.zeros((0, 2))
    ys = np.array([(o.h, o.a) for o in win], float) if win else np.zeros((0, 2))
    rho, ks, pooled = None, {}, None
    if sp.extra.get("dixon_coles") and len(win) >= 2:
        rho = fit_rho(train, ys)
    if sp.extra.get("dispersion") == "league" and len(win) >= 2:
        pooled = fit_k(train, ys)
        for c in sorted({o.competition for o in win}):
            idx = [i for i, o in enumerate(win) if o.competition == c]
            ks[c] = fit_k(train[idx], ys[idx]) if len(idx) >= MIN_LEAGUE_OBS else pooled
    for t in targets:
        r = out[t.mid]
        if rho is not None:
            r["joint"], r["rho"] = ev.dc_joint(r["mh"], r["ma"], rho, cap), rho
        elif pooled is not None:
            k = ks.get(t.competition, pooled)
            r["joint"], r["k"] = ev.nb_joint(r["mh"], r["ma"], k, cap), k
        else:
            r["joint"] = ev.poisson_joint(r["mh"], r["ma"], cap)
    return out


# --- Pricing ------------------------------------------------------------------------------------------------------------
def x12(joint) -> tuple:
    j = np.asarray(joint)
    return float(np.tril(j, -1).sum()), float(np.trace(j)), float(np.triu(j, 1).sum())


def _rows_for(sp_joint, mid: str, lines: dict, with_1x2: bool) -> list:
    """(market, line, (a, push, b)) rows of one match from one joint: lines = {goals market: [lines]}."""
    j = np.asarray(sp_joint).tolist()
    out = []
    if with_1x2:
        out.append(("soccer_1x2", None, x12(j)))
    for m, ls in lines.items():
        for ln in ls:
            p = sm.probabilities(j, m, "over", ln)
            out.append((MARKET_OF[m], ln, (p["win"], p["push"], p["loss"])))
    p = sm.probabilities(j, "btts", "yes", None)
    out.append(("soccer_btts", None, (p["win"], 0.0, p["loss"])))
    return out


def served_goals(conn, mid: str, now: str) -> Optional[list]:
    r = conn.execute("SELECT joint_json FROM soccer_market_forecasts WHERE match_id=? AND family='goals' AND generated_at<=? "
                     "ORDER BY version DESC LIMIT 1", (mid, now)).fetchone()
    return None if r is None else json.loads(r[0])


def served_1x2(conn, mid: str, now: str) -> Optional[tuple]:
    r = conn.execute("SELECT p_home, p_draw, p_away FROM soccer_live_forecasts WHERE match_id=? AND generated_at<=? "
                     "ORDER BY version DESC LIMIT 1", (mid, now)).fetchone()
    return None if r is None else tuple(r)


def served_lines(conn, mid: str, joint, kickoff: str, now: str) -> dict:
    """The lines the app serves for the match: the model's fair line per goals market, plus the bookmaker total-goals lines."""
    out = {m: [sm.fair_line(joint, m)] for m in MARKET_OF}
    for q in sml.quotes(conn, mid, min(now, kickoff)):
        if q["market"] == "total_goals" and q["line"] not in out["goals_total"]:
            out["goals_total"].append(q["line"])
    return out


def record(conn, path: Path, *, policies: list, now: Optional[str] = None, day: Optional[str] = None, dry_run: bool = False) -> dict:
    """Challenger rows for scheduled matches dated `day` (default: the UTC date of `now`) that kick off after `now`."""
    now = now or utc_now()
    day = day or now[:10]
    out = {"status": "success", "dry_run": dry_run, "now": now, "day": day, "created": 0, "would_record": 0, "already_recorded": 0,
           "eligible_games": 0, "skipped": {}, "skipped_quarter_lines": 0, "errors": {},
           "policies": [{"policy_id": p.policy_id, "sha": p.sha} for p in policies]}
    try:
        lc.check_unique_names(policies)
        st = sml.state("goals")
        if not st["served"]:
            out["status"], out["errors"]["recorder"] = "failed", f"served goals forecast is not served: {st['reason']}"
            return out
        sealed = sml.sealed("goals")
        kick = {r[0]: r[1] for r in conn.execute("SELECT match_id, kickoff_at FROM soccer_matches WHERE season=?", (soccer_live.SEASON,))}
        targets = [t for t in sml.targets_on(conn, st["competitions"], day) if kick[t.mid] > now]
        out["eligible_games"] = len(targets)
        if not targets:
            return out
        data = ev.collect(conn, "goals", soccer_live.SEASON)
        cutoff = ev.cutoff_for(day)
        problems = [f"input competition {c}: {p}" for c in sml.INPUTS for p in sml.check(conn, "goals", c, now, cutoff, data)["problems"]]
        if problems:
            out["status"], out["errors"]["inputs"] = "failed", "; ".join(problems)
            return out
    except Exception as exc:
        out["status"], out["errors"]["recorder"] = "failed", f"{type(exc).__name__}: {exc}"
        return out
    priced = []
    for t in targets:
        inc = served_goals(conn, t.mid, now)
        if inc is None:
            out["skipped"][t.mid] = "no served goals forecast yet: nothing to pair with"
            continue
        lines = served_lines(conn, t.mid, inc, kick[t.mid], now)
        for m in lines:
            keep = [ln for ln in lines[m] if (ln * 2) % 1 == 0]
            out["skipped_quarter_lines"] += len(lines[m]) - len(keep)
            lines[m] = keep
        priced.append((t, lines, served_1x2(conn, t.mid, now) is not None))
    for t, _, has12 in priced:
        if not has12:
            out["skipped"][f"{t.mid}:1x2"] = "no served 1X2 forecast: 1X2 not priced"
    digest = hashlib.sha256(json.dumps(sorted((o.mid, o.available_at, o.h, o.a) for o in day_inputs(data, day))).encode()).hexdigest()[:16]
    for pol in policies:
        for c in pol.challengers:
            try:
                js = joints(c.spec, sealed, data, day, [p[0] for p in priced])
            except Exception as exc:
                out["errors"][c.spec.name] = f"{type(exc).__name__}: {exc}"
                continue
            definition = f"{sealed['sha256'][:8]}+spec={c.spec_hash[:8]}+cutoff={cutoff[:10]}-{digest[:8]}"
            rows = []
            for t, lines, has12 in priced:
                for market, line, p in _rows_for(js[t.mid]["joint"], t.mid, lines, has12):
                    pa, pm = round(p[0], 9), round(p[1], 9)
                    rows.append({"game_id": t.mid, "market": market, "line": line, "p": (pa, pm, round(1 - pa - pm, 9)),
                                 "generated_at": now, "tipoff_at": kick[t.mid]})
            lc.append_rows(path, pol, c, rows, definition=definition, dataset_version=digest, now=now, dry_run=dry_run, out=out, cutoff_at=cutoff)
    if out["status"] == "success" and (out["errors"] or out["skipped"] or out["skipped_quarter_lines"]):
        out["status"] = "partial"
    return out
