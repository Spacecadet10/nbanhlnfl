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


# =============================================================================================================================
# Research adapter (docs/AUTORESEARCH.md). Lives here, not in soccer_markets_eval.py: that file is SEALED (byte-identical to the
# plan commit, tests/test_ncaab_app_regression.py) and hashed by CODE_FILES. Everything below is research only: it reads the
# sealed module's functions and never changes them. `fit_strengths`, `means` and `walk` below are the sealed ones plus the
# research candidates' hierarchical option (`hier`, default off = the sealed behaviour).
# =============================================================================================================================
import bisect as _bisect  # noqa: E402
import sqlite3  # noqa: E402
from collections import Counter, defaultdict  # noqa: E402
from datetime import date, datetime, timedelta  # noqa: E402
from typing import Optional  # noqa: E402

from scipy.optimize import minimize  # noqa: E402

from . import backtest  # noqa: E402
from .soccer_markets_eval import (CAP, CLIP, COMPETITIONS, DEFAULT_MEAN, DEV_FIT, DEV_TUNE, LAST_OPEN, LEAGUES, PROSPECTIVE, TEST,  # noqa: E402
                                  VALIDATION, WARMUP, WINDOW_DAYS, Config, Data, Obs, TestSeasonsLocked, _append, _inputs_index,
                                  _league_of, _main_types, centre, collect, cutoff_for, data_digest, eligible_stage, load,
                                  local_date, nb_joint, poisson_joint, protocol_sha256, scored, stage_rules)
from .backtest import LeakageError  # noqa: E402
from .soccer_markets_eval import access_log_path as _official_access_log_path  # noqa: E402


def research_access_log_path() -> Path:
    """Reserved-season reads by the research adapter are logged HERE, never in the official soccer test-access logs."""
    return _official_access_log_path().with_name("research_access.jsonl")


HIER_RIDGE = 1.0     # research candidate only: ridge on the league-level offsets of `fit_strengths(hier=True)`


def fit_strengths(family: str, cfg: Config, members: dict, inputs: list, cutoff: str, targets: list, hier: bool = False) -> dict:
    """Weighted ridge Poisson fit at one cutoff from `inputs` (labelled Obs available by the cutoff, earlier dates, within
    the window). Cold start from the centres: the result depends only on the inputs, the config and the targets.
    `hier` (research candidates only, never the sealed models): each team's attack and defence are shrunk toward its own
    league's offset (learned mainly from the cross-league UEFA matches) instead of toward 0."""
    day = date.fromisoformat(cutoff[:10])
    lo = day.toordinal() - WINDOW_DAYS
    rows = [o for o in inputs if date.fromisoformat(o.local_date).toordinal() >= lo]
    season_of: dict = {}
    for o in sorted(rows, key=lambda o: (o.local_date, o.mid)):
        season_of[o.home] = season_of[o.away] = o.season
    for t in targets:
        season_of[t.home] = season_of[t.away] = t.season
    cen = {team: centre(members, team, s, cfg.D) for team, s in season_of.items()}
    mu0 = math.log(DEFAULT_MEAN[family])
    if not rows:
        return {"mu": {}, "h": {}, "att": {}, "def": {}, "centre": cen, "mu0": mu0}
    teams = sorted({o.home for o in rows} | {o.away for o in rows})
    groups = sorted({o.competition for o in rows})
    ti, gi = {t: k for k, t in enumerate(teams)}, {g: k for k, g in enumerate(groups)}
    hi = np.array([ti[o.home] for o in rows])
    ai = np.array([ti[o.away] for o in rows])
    g_ = np.array([gi[o.competition] for o in rows])
    hy = np.array([o.h for o in rows], float)
    ay = np.array([o.a for o in rows], float)
    age = day.toordinal() - np.array([date.fromisoformat(o.local_date).toordinal() for o in rows], float)
    w = np.exp(-math.log(2) * age / cfg.half_life_days)
    nt, ng = len(teams), len(groups)
    pc = np.array([cen.get(t, 0.0) for t in teams])
    tau = cfg.tau
    lg_names = [*LEAGUES, "other"]
    li = np.array([lg_names.index(_league_of(members, t, season_of[t]) or "other") for t in teams])
    nl = len(lg_names) if hier else 0

    def f(theta):
        mu, hh = theta[:ng], theta[ng:2 * ng]
        att, de = theta[2 * ng:2 * ng + nt], theta[2 * ng + nt:2 * ng + 2 * nt]
        da, dd = (theta[2 * ng + 2 * nt:2 * ng + 2 * nt + nl], theta[2 * ng + 2 * nt + nl:]) if hier else (0.0, 0.0)
        pa_, pd_ = (pc + da[li], pc + dd[li]) if hier else (pc, pc)
        eh = mu[g_] + hh[g_] + att[hi] - de[ai]
        ea = mu[g_] + att[ai] - de[hi]
        lh, la = np.exp(eh), np.exp(ea)
        nll = np.sum(w * (lh - hy * eh)) + np.sum(w * (la - ay * ea))
        nll += tau * (np.sum((att - pa_) ** 2) + np.sum((de - pd_) ** 2))
        rh, ra = w * (lh - hy), w * (la - ay)
        g_mu = np.bincount(g_, rh + ra, ng)
        g_h = np.bincount(g_, rh, ng)
        g_att = np.bincount(hi, rh, nt) + np.bincount(ai, ra, nt) + 2 * tau * (att - pa_)
        g_de = -np.bincount(ai, rh, nt) - np.bincount(hi, ra, nt) + 2 * tau * (de - pd_)
        parts = [g_mu, g_h, g_att, g_de]
        if hier:
            nll += HIER_RIDGE * (np.sum(da ** 2) + np.sum(dd ** 2))
            parts += [-2 * tau * np.bincount(li, att - pa_, nl) + 2 * HIER_RIDGE * da,
                      -2 * tau * np.bincount(li, de - pd_, nl) + 2 * HIER_RIDGE * dd]
        return nll, np.concatenate(parts)
    x0 = np.concatenate([np.full(ng, mu0), np.zeros(ng), pc, pc, np.zeros(2 * nl)])
    res = minimize(f, x0, jac=True, method="L-BFGS-B", options={"maxiter": 500, "ftol": 1e-9, "gtol": 1e-7})
    t = res.x
    off_a, off_d = {}, {}
    if hier:        # a team with no row in the window starts from its league's offset (promoted: centre + offset)
        da_, dd_ = t[2 * ng + 2 * nt:2 * ng + 2 * nt + nl], t[2 * ng + 2 * nt + nl:]
        for team, s_ in season_of.items():
            k = lg_names.index(_league_of(members, team, s_) or "other")
            off_a[team], off_d[team] = float(da_[k]), float(dd_[k])
    return {"mu": {g: float(t[k]) for k, g in enumerate(groups)}, "h": {g: float(t[ng + k]) for k, g in enumerate(groups)},
            "att": {x: float(t[2 * ng + k]) for k, x in enumerate(teams)},
            "def": {x: float(t[2 * ng + nt + k]) for k, x in enumerate(teams)}, "centre": cen, "mu0": mu0,
            **({"off_att": off_a, "off_def": off_d} if hier else {})}


def means(params: dict, o: Obs) -> tuple[float, float]:
    mu = params["mu"].get(o.competition, params["mu0"])
    hh = params["h"].get(o.competition, 0.0)
    oa, od = params.get("off_att", {}), params.get("off_def", {})
    ah = params["att"].get(o.home, params["centre"].get(o.home, 0.0) + oa.get(o.home, 0.0))
    dh = params["def"].get(o.home, params["centre"].get(o.home, 0.0) + od.get(o.home, 0.0))
    aa = params["att"].get(o.away, params["centre"].get(o.away, 0.0) + oa.get(o.away, 0.0))
    da = params["def"].get(o.away, params["centre"].get(o.away, 0.0) + od.get(o.away, 0.0))
    return math.exp(mu + hh + ah - da), math.exp(mu + aa - dh)


def walk(data: Data, cfg: Config, seasons, targets: Optional[list] = None, hier: bool = False) -> list[dict]:
    """Means for every scored match of `seasons` (or the given targets), each date from the inputs available at its
    06:00 UTC cutoff whose match date is earlier. Raises LeakageError if an input is not available by the cutoff or a
    target's own label is among its inputs."""
    tg = defaultdict(list)
    for o in (targets if targets is not None else scored(data, seasons)):
        tg[o.local_date].append(o)
    lab, avail = _inputs_index(data)
    out = []
    for day in sorted(tg):
        cutoff = cutoff_for(day)
        j = _bisect.bisect_right(avail, cutoff)
        inputs = [o for o in lab[:j] if o.local_date < day]
        if any(o.available_at > cutoff for o in inputs):
            raise LeakageError(f"an input of {day} is available after its cutoff")
        mids = {t.mid for t in tg[day]}
        if any(o.mid in mids for o in inputs):
            raise LeakageError(f"a match of {day} is among its own inputs")
        params = (fit_strengths(data.family, cfg, data.members, inputs, cutoff, tg[day], hier=True) if hier
                  else fit_strengths(data.family, cfg, data.members, inputs, cutoff, tg[day]))
        for t in tg[day]:
            mh, ma = means(params, t)
            out.append({"mid": t.mid, "competition": t.competition, "season": t.season, "cutoff_at": cutoff,
                        "mh": mh, "ma": ma, "h": t.h, "a": t.a})
    return out



# === Research adapter (docs/AUTORESEARCH.md contract; research only: nothing here writes a store, a forecast table or a
# live artifact, and no function below approves or promotes a model) ------------------------------------------------------
ADAPTER_VERSION = "soccer-research-adapter-v1"
RESEARCH_COMPETITIONS = COMPETITIONS            # deterministic processing order; the 1X2 model covers the same seven
LINES = {"goals_total": (1.5, 2.0, 2.5, 3.0, 3.5), "goals_home": (0.5, 1.5), "goals_away": (0.5, 1.5), "btts_yes": (None,),
         "corners_total": (8.5, 9.0, 9.5, 10.0, 10.5), "corners_home": (3.5, 4.5, 5.5), "corners_away": (2.5, 3.5, 4.5),
         "corners_handicap": (-1.5, -1.0, -0.5, 0.5, 1.0, 1.5)}
FAMILY_LINES = {"goals": ("goals_total", "goals_home", "goals_away", "btts_yes"),
                "corners": ("corners_total", "corners_home", "corners_away", "corners_handicap")}
_SETTLE = {"over": "a = the count is above the line, b = below, push = equal (whole lines only); 90 minutes plus stoppage time, "
                   "extra time and penalties never count; a postponed/cancelled/abandoned match is void",
           "yes": "a = both teams score, b = not; 90 minutes plus stoppage time",
           "handicap": "a = home count + line beats the away count, b = loses, push = equal (whole lines); away side = minus the line"}


def settle_count(market: str, line: Optional[float], h: int, a: int) -> str:
    """'a' / 'b' / 'push' of one count-market selection on the 90-minute counts (a = over / yes / home with the line)."""
    if market == "btts_yes":
        return "a" if h > 0 and a > 0 else "b"
    x = {"goals_total": h + a, "corners_total": h + a, "goals_home": h, "corners_home": h, "goals_away": a,
         "corners_away": a}.get(market)
    d = (h + line - a) if market == "corners_handicap" else (x - line)
    return "a" if d > 1e-9 else ("b" if d < -1e-9 else "push")


def line_probs(g: np.ndarray, market: str, line: Optional[float], cap: int) -> tuple[float, float, float]:
    """(P(a), P(b), P(push)) of one selection under a joint count grid."""
    h, a = np.meshgrid(np.arange(cap + 1), np.arange(cap + 1), indexing="ij")
    if market == "btts_yes":
        pa = float(g[(h > 0) & (a > 0)].sum())
        return pa, 1 - pa, 0.0
    x = {"goals_total": h + a, "corners_total": h + a, "goals_home": h, "corners_home": h, "goals_away": a,
         "corners_away": a}.get(market)
    d = (h + line - a) if market == "corners_handicap" else (x - line)
    return float(g[d > 1e-9].sum()), float(g[d < -1e-9].sum()), float(g[np.abs(d) <= 1e-9].sum())


def nb_tilt_joint(mh: float, ma: float, k_disp: float, gamma: float, cap: int) -> np.ndarray:
    """Negative-binomial marginals with a marginal-preserving negative dependence: exp(-gamma z_h z_a) tilt of the product
    (z = standardised count), margins restored by iterative proportional fitting. gamma 0 = `nb_joint`."""
    g = nb_joint(mh, ma, k_disp, cap)
    if gamma == 0:
        return g
    x = np.arange(cap + 1)
    rm, cm = g.sum(1), g.sum(0)
    zh = (x - (rm * x).sum()) / math.sqrt(max(1e-9, (rm * x * x).sum() - (rm * x).sum() ** 2))
    za = (x - (cm * x).sum()) / math.sqrt(max(1e-9, (cm * x * x).sum() - (cm * x).sum() ** 2))
    q = g * np.exp(-gamma * np.outer(zh, za))
    for _ in range(60):
        q *= (rm / q.sum(1))[:, None]
        q *= (cm / q.sum(0))[None, :]
    return q / q.sum()


def count_records(game_id: str, competition: str, season: int, start_at: str, cutoff: str, family: str, g: np.ndarray,
                  mh: float, ma: float, h: Optional[int], a: Optional[int]) -> list[dict]:
    """One record per market and line of the family, settled when the 90-minute counts (h, a) are given."""
    cap, out = CAP[family], []
    for market in FAMILY_LINES[family]:
        for line in LINES[market]:
            pa, pb, pp = line_probs(g, market, line, cap)
            pt = {"goals_total": (mh + ma, None if h is None else h + a), "corners_total": (mh + ma, None if h is None else h + a),
                  "goals_home": (mh, h), "corners_home": (mh, h), "goals_away": (ma, a), "corners_away": (ma, a),
                  "corners_handicap": (mh - ma, None if h is None else h - a)}.get(market, (None, None))
            rec = {"game_id": game_id, "competition": competition, "season": season, "start_at": start_at,
                   "forecast_cutoff": cutoff, "market": market, "line": line,
                   "probs": {"a": pa, "b": pb, **({"push": pp} if pp else {})},
                   "outcome": None if h is None else settle_count(market, line, h, a), "quote": None}
            if pt[0] is not None:
                rec["point_pred"], rec["point_actual"] = pt
            out.append(rec)
    return out


def one_x_two_records(game_id: str, season: int, start_at: str, cutoff: str, p: tuple, label: Optional[int],
                      competition: Optional[str] = None) -> list[dict]:
    """The 90-minute result, the three double chances and draw no bet (home side) from one 1X2 forecast. label 0 home win,
    1 draw, 2 away win. Double chance and draw no bet are exact functions of the 1X2 forecast, never separate models."""
    ph, pd, pa = (float(x) for x in p)
    base = {"game_id": game_id, "season": season, "start_at": start_at, "forecast_cutoff": cutoff, "line": None, "quote": None,
            **({"competition": competition} if competition else {})}
    yes = lambda ok: None if label is None else ("a" if ok else "b")
    return [{**base, "market": "result_1x2", "probs": {"a": ph, "mid": pd, "b": pa},
             "outcome": None if label is None else ("a", "mid", "b")[label]},
            {**base, "market": "double_chance_1x", "probs": {"a": ph + pd, "b": pa}, "outcome": yes(label in (0, 1))},
            {**base, "market": "double_chance_x2", "probs": {"a": pd + pa, "b": ph}, "outcome": yes(label in (1, 2))},
            {**base, "market": "double_chance_12", "probs": {"a": ph + pa, "b": pd}, "outcome": yes(label in (0, 2))},
            {**base, "market": "draw_no_bet_home", "probs": {"a": ph / (ph + pa), "b": pa / (ph + pa)},
             "outcome": None if label is None else ("push" if label == 1 else ("a" if label == 0 else "b"))}]


# --- Models -------------------------------------------------------------------------------------------------------------
def _sealed_file(family: str) -> dict:
    return json.loads(Path(__file__).with_name(f"soccer_markets_sealed_{family}.json").read_text())


def _model_table() -> dict:
    g, c = _sealed_file("goals"), _sealed_file("corners")
    gc, cc = Config(**g["config"]), Config(**c["config"])
    k = next(iter(c["extra"].values()))
    t = CANDIDATE_PARAMS
    return {
        "elo_sealed": dict(family="1x2", role="incumbent", kind="elo", version="sealed 7dbc668c",
                           description="draw-aware Elo, the sealed 90-minute 1X2 model (app/soccer_live.py); double chance and draw "
                                       "no bet are derived from it"),
        "frequency_1x2": dict(family="1x2", role="baseline", kind="freq1x2", version=ADAPTER_VERSION,
                              description="per competition result frequencies of the labels available at each cutoff (expanding, +1 smoothing)"),
        "goals_poisson_sealed": dict(family="goals", role="incumbent", kind="count", cfg=gc, joint="poisson", version="sealed " + g["sha256"][:8],
                                     description="sealed team-strength Poisson, half-life %d d, tau %g, D %g" % (gc.half_life_days, gc.tau, gc.D)),
        "goals_frequency": dict(family="goals", role="baseline", kind="freq_count", version=ADAPTER_VERSION,
                                description="per competition empirical home/away goal distributions of the labels available at each cutoff"),
        "goals_league_rate": dict(family="goals", role="baseline", kind="rate_count", version=ADAPTER_VERSION,
                                  description="plain Poisson from each competition's mean home/away goals so far (no team strengths)"),
        "goals_poisson_lowD": dict(family="goals", role="candidate", kind="count", cfg=Config(gc.half_life_days, t["goals_lowtau"]["tau"], t["goals_lowtau"]["D"]),
                                     joint="poisson", version=ADAPTER_VERSION,
                                     description="sealed goals Poisson with tau/D re-tuned below the preregistered grid's lower edge on development_tune (tau 3 stayed best, D 0.05)"),
        "goals_poisson_hier": dict(family="goals", role="candidate", kind="count", cfg=gc, joint="poisson", hier=True, version=ADAPTER_VERSION,
                                   description="sealed goals Poisson whose team ratings shrink toward their league's offset (learned from UEFA "
                                               "cross-league matches) instead of toward 0"),
        "corners_nb_sealed": dict(family="corners", role="incumbent", kind="count", cfg=cc, joint="nb", extra=k, version="sealed " + c["sha256"][:8],
                                  description="sealed negative-binomial corners model, k %.2f, half-life %d d, tau %g, D %g" % (k, cc.half_life_days, cc.tau, cc.D)),
        "corners_frequency": dict(family="corners", role="baseline", kind="freq_count", version=ADAPTER_VERSION,
                                  description="per competition empirical home/away corner distributions of the labels available at each cutoff"),
        "corners_league_rate": dict(family="corners", role="baseline", kind="rate_count", version=ADAPTER_VERSION,
                                    description="plain Poisson from each competition's mean home/away corners so far"),
        "corners_nb_tilt": dict(family="corners", role="candidate", kind="count", cfg=cc, joint="nb_tilt", extra=(k, t["corners_tilt"]["gamma"]),
                                version=ADAPTER_VERSION,
                                description="sealed corners NB with a marginal-preserving negative dependence between the two teams' counts "
                                            "(the sealed model's independent counts make the total too wide)"),
        "corners_nb_hier": dict(family="corners", role="candidate", kind="count", cfg=cc, joint="nb", extra=k, hier=True,
                                version=ADAPTER_VERSION, description="sealed corners NB with league-offset shrinkage of the team ratings"),
    }


# Candidate constants: chosen on development_tune / development_fit seasons only (the soccer agent's tuning run of 2026-11-22; its evidence folder is not in the repository).
CANDIDATE_PARAMS = {"goals_lowtau": {"tau": 3.0, "D": 0.05}, "corners_tilt": {"gamma": 0.2}}


def _joint(m: dict, mh: float, ma: float) -> np.ndarray:
    cap = CAP[m["family"]]
    j = m["joint"]
    if j == "poisson":
        return poisson_joint(mh, ma, cap)
    if j == "nb":
        return nb_joint(mh, ma, m["extra"], cap)
    return nb_tilt_joint(mh, ma, m["extra"][0], m["extra"][1], cap)


def research_adapter(conn: Optional[sqlite3.Connection] = None) -> dict:
    """The soccer research adapter (docs/AUTORESEARCH.md): markets, periods, models, `walk`, `forecast_upcoming`."""
    kinds = {"result_1x2": ("three_way", "90-minute result a = home win, mid = draw, b = away win; extra time and penalties never count"),
             "double_chance_1x": ("binary", "a = home win or draw, b = away win (90 minutes); derived from the 1X2 forecast: any price is SYNTHETIC"),
             "double_chance_x2": ("binary", "a = draw or away win, b = home win (90 minutes); derived: any price is SYNTHETIC"),
             "double_chance_12": ("binary", "a = no draw, b = draw (90 minutes); derived: any price is SYNTHETIC"),
             "draw_no_bet_home": ("binary", "a = home win, b = away win, push on a draw (stake returned); probs are conditional on no draw; derived: any price is SYNTHETIC"),
             "btts_yes": ("binary", _SETTLE["yes"])}
    for mk in ("goals_total", "corners_total"):
        kinds[mk] = ("line", _SETTLE["over"])
    for mk in ("goals_home", "goals_away", "corners_home", "corners_away"):
        kinds[mk] = ("line", _SETTLE["over"])
    kinds["corners_handicap"] = ("line", _SETTLE["handicap"])
    markets = {m: {"kind": k, "settlement": s} for m, (k, s) in kinds.items()}
    for m in LINES:
        if m in markets:
            markets[m]["lines"] = [x for x in LINES[m] if x is not None]
    markets["corners_total"]["walk_data"] = markets["corners_home"]["walk_data"] = markets["corners_away"]["walk_data"] = \
        markets["corners_handicap"]["walk_data"] = "ESPN corner counts; the store needs the month backfill (soccer history-months) for search/validation seasons"
    unsupported = {
        "goals_handicap": "ESPN lists a goal handicap price but the app forecasts no goal handicap (no market in soccer_markets.FAMILY_OF)",
        "asian_quarter_lines": "settlement is defined (win/half win/push/half loss/loss, soccer_markets.py) but the five outcome codes of the record cannot hold a half result, so lines are walked at half and whole steps only",
        "national_team_goals_corners": "national-team competitions carry the 1X2 forecast only (no goals/corners model, no corner counts in the ESPN national feeds)",
        "correct_score": "not forecast by the app", "first_half_markets": "not forecast: no half-time data in the stores",
        "bet365_double_chance_history": "Bet365 double chance quotes are saved as files from 2026-11-20 only; no history for search/validation",
    }
    models = {n: {k: v for k, v in m.items() if k in ("role", "description", "version")} | {"family": m["family"]}
              for n, m in _model_table().items()}
    periods = {"search": {"seasons": list(DEV_TUNE + DEV_FIT), "warmup": list(WARMUP), "meaning": "season = start year (2020 = 2020-21)"},
               "validation": {"seasons": list(VALIDATION)},
               "holdout": {"seasons": list(TEST), "locked_prospective": list(PROSPECTIVE),
                           "rule": "a walk over these seasons needs allow_holdout=True and is logged; the prospective season is never read"}}
    return {"sport": "soccer", "data_version": f"{ADAPTER_VERSION}:{protocol_sha256()[:12]}" + (f":{data_digest(conn, LAST_OPEN)[:12]}" if conn is not None else ""),
            "markets": markets, "unsupported": unsupported, "periods": periods, "models": models,
            "competitions": list(RESEARCH_COMPETITIONS), "push_scoring": "conditional", "walk": adapter_walk, "forecast_upcoming": forecast_upcoming,
            "validate_data": validate_data}


# --- Walk ----------------------------------------------------------------------------------------------------------------
def _check_seasons(seasons, allow_holdout: bool, log: dict) -> tuple:
    seasons = tuple(sorted(set(int(s) for s in seasons)))
    if any(s in PROSPECTIVE or s > TEST[-1] for s in seasons):
        raise TestSeasonsLocked(f"season(s) {[s for s in seasons if s > TEST[-1]]} are prospective and locked: no evaluation reads them")
    if any(s in TEST for s in seasons):
        if not allow_holdout:
            raise TestSeasonsLocked(f"seasons {[s for s in seasons if s in TEST]} are the holdout: pass allow_holdout=True (logged) to read them")
        _append(research_access_log_path(), {"purpose": "research adapter holdout read", "seasons": list(seasons), **log})
    return seasons


def _rwindow(items: list, key, competitions, max_work, resume, state):
    """Deterministic order (date, competition index, id); `competitions` filters; at most `max_work` cutoff dates per call after
    the resume token (the last completed date); `state` receives next / done / work."""
    order = {c: i for i, c in enumerate(RESEARCH_COMPETITIONS)}
    if competitions is not None:
        bad = sorted(set(competitions) - set(order))
        if bad:
            raise ValueError(f"unknown competition(s) {bad}; known: {list(order)}")
        items = [x for x in items if key(x)[1] in competitions]
    dates = sorted({key(x)[0] for x in items if resume is None or key(x)[0] > resume})
    take = dates if max_work is None else dates[:max_work]
    if state is not None:
        state.update(next=take[-1] if take else resume, done=len(take) == len(dates), work=len(take))
    keep = set(take)
    return sorted((x for x in items if key(x)[0] in keep), key=lambda x: (key(x)[0], order[key(x)[1]], key(x)[2]))


class _ExpandingFrequency1x2:
    """Per competition result frequencies of the labels fed so far (+1 smoothing): a leak-free baseline for any season."""
    name = "frequency_1x2"

    def __init__(self):
        self.c = defaultdict(lambda: [1.0, 1.0, 1.0])
        self.match_meta: dict = {}

    def observe(self, r):
        from .soccer_eval import label_of
        self.c[self.match_meta[r.mid][0]][label_of(r.hg, r.ag)] += 1

    def predict(self, matches, cutoff_at):
        return {m.mid: tuple(x / sum(self.c[m.competition]) for x in self.c[m.competition]) for m in matches}


def _walk_1x2(conn, seasons, m, competitions, max_work, resume, state, allow_holdout):
    from . import soccer_eval as se, soccer_live
    seasons = _check_seasons(seasons, allow_holdout, {"model": "1x2"})
    through = max(seasons)
    sealed = soccer_live.sealed_candidate()
    ds = se.load(conn, through=through, sealed=sealed if through > se.LAST_OPEN else None,
                 access_log=research_access_log_path() if through > se.LAST_OPEN else None, purpose="research adapter 1X2 walk")
    preds = se.forecast_frozen(sealed, ds, seasons) if m["kind"] == "elo" else se.walk(_ExpandingFrequency1x2(), ds, seasons)
    meta = ds.meta
    rows = _rwindow(preds, lambda p: (meta[p["mid"]][4], p["competition"], p["mid"]), competitions, max_work, resume, state)
    out = []
    for p in rows:
        day = meta[p["mid"]][4]
        out += one_x_two_records(p["mid"], p["season"], f"{day}T12:00:00", p["cutoff_at"], p["p"], p["label"], p["competition"])
    return out


def _baseline_joint(kind: str, family: str, lab: list, avail: list, cutoff: str, day: str, competition: str) -> np.ndarray:
    cap = CAP[family]
    j = _bisect.bisect_right(avail, cutoff)
    mine = [o for o in lab[:j] if o.local_date < day and o.competition == competition]
    if kind == "freq_count":
        h, a = np.full(cap + 1, 0.5), np.full(cap + 1, 0.5)
        for o in mine:
            h[min(o.h, cap)] += 1
            a[min(o.a, cap)] += 1
        return np.outer(h / h.sum(), a / a.sum())
    mh = (sum(o.h for o in mine) + DEFAULT_MEAN[family]) / (len(mine) + 1)
    ma = (sum(o.a for o in mine) + DEFAULT_MEAN[family]) / (len(mine) + 1)
    return poisson_joint(mh, ma, cap)


def _research_data(conn, family: str, seasons: tuple, allow_holdout: bool) -> Data:
    through = max(seasons)
    if through <= LAST_OPEN:
        return load(conn, family, through=through)
    return collect(conn, family, through)           # holdout: gate and log were handled by _check_seasons


def _kickoffs(conn) -> dict:
    return {r[0]: r[1] for r in conn.execute("SELECT match_id, kickoff_at FROM soccer_matches")}


def _count_walk(conn, seasons, m, competitions, max_work, resume, state, allow_holdout):
    family = m["family"]
    seasons = _check_seasons(seasons, allow_holdout, {"model": family})
    data = _research_data(conn, family, seasons, allow_holdout)
    chosen = _rwindow(scored(data, seasons), lambda o: (o.local_date, o.competition, o.mid), competitions, max_work, resume, state)
    if not chosen:
        return []
    kick = _kickoffs(conn)
    out = []
    if m["kind"] == "count":
        rows = walk(data, m["cfg"], (), targets=chosen, hier=bool(m.get("hier")))
        for r in rows:
            g = _joint(m, r["mh"], r["ma"])
            out += count_records(r["mid"], r["competition"], r["season"], kick[r["mid"]], r["cutoff_at"], family, g,
                                 r["mh"], r["ma"], r["h"], r["a"])
    else:
        lab, avail = _inputs_index(data)
        for o in chosen:
            cutoff = cutoff_for(o.local_date)
            g = _baseline_joint(m["kind"], family, lab, avail, cutoff, o.local_date, o.competition)
            mh, ma = float((g.sum(1) * np.arange(g.shape[0])).sum()), float((g.sum(0) * np.arange(g.shape[0])).sum())
            out += count_records(o.mid, o.competition, o.season, kick[o.mid], cutoff, family, g, mh, ma, o.h, o.a)
    attach_quotes(conn, out)
    return sorted(out, key=lambda r: (r["start_at"], r["game_id"], r["market"], r["line"] if r["line"] is not None else 0))


def adapter_walk(conn: sqlite3.Connection, seasons, model_name: str, competitions=None, max_work: Optional[int] = None,
                 resume: Optional[str] = None, state: Optional[dict] = None, allow_holdout: bool = False) -> list[dict]:
    """Chronological forecast records of `model_name` for every scored match of `seasons`, each from the inputs available at
    its 06:00 UTC cutoff of the match date (never a same-day or later label). `competitions` filters the targets (the ratings
    still use every input); `max_work` bounds the cutoff dates processed in this call and `resume` (the previous call's
    `state['next']`) continues after them, in the fixed order (date, competition, match id). `state` receives next, done, work.
    Holdout seasons need allow_holdout=True (logged); the prospective season is never read."""
    table = _model_table()
    if model_name not in table:
        raise ValueError(f"unknown model {model_name!r}; known: {sorted(table)}")
    m = {**table[model_name]}
    walker = _walk_1x2 if m["family"] == "1x2" else _count_walk
    return walker(conn, seasons, m, competitions, max_work, resume, state, allow_holdout)


# --- Executable bookmaker prices stored at or before the forecast (never invented) ------------------------------------------
def attach_quotes(conn, records: list[dict]) -> None:
    """Fills `quote` in place for match ids that have stored ESPN (DraftKings) total-goals prices at the record's own line,
    retrieved at or before the forecast cutoff and before kickoff. Executable, not synthetic. Nothing else is priced."""
    if not records or not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'soccer_odds_quotes'").fetchone():
        return
    mids = {r["game_id"] for r in records if r["market"] == "goals_total"}
    if not mids:
        return
    q = ",".join("?" * len(mids))
    rows = conn.execute(f"SELECT quote_id, match_id, provider, side, line, decimal_odds, retrieved_at FROM soccer_odds_quotes WHERE "
                        f"market = 'total_goals' AND price_kind = 'current' AND offered = 1 AND match_id IN ({q}) ORDER BY quote_id",
                        tuple(mids)).fetchall()
    by = defaultdict(list)
    for r in rows:
        by[r["match_id"]].append(r)
    for rec in records:
        if rec["market"] != "goals_total" or rec["game_id"] not in by:
            continue
        latest = {}
        for r in by[rec["game_id"]]:
            if r["line"] == rec["line"] and r["retrieved_at"] <= rec["forecast_cutoff"] and r["retrieved_at"] < rec["start_at"]:
                latest[(r["provider"].lower().replace(" ", ""), r["side"])] = r
        for prov in {k[0] for k in latest}:
            o, u = latest.get((prov, "over")), latest.get((prov, "under"))
            if o and u:
                rec["quote"] = {"provider": o["provider"], "retrieved_at": max(o["retrieved_at"], u["retrieved_at"]),
                                "price_a": o["decimal_odds"], "price_b": u["decimal_odds"], "price_mid": None, "line": rec["line"],
                                "executable": True, "synthetic": False}
                break


def _derived_quote(book: dict, market: str) -> Optional[dict]:
    """A double chance / draw no bet price built from the three 1X2 prices of one book: SYNTHETIC, never executable."""
    ih, idr, ia = (1 / book[s] for s in ("home", "draw", "away"))
    p = {"double_chance_1x": (ih + idr, None), "double_chance_x2": (idr + ia, None), "double_chance_12": (ih + ia, None),
         "draw_no_bet_home": (ih / (ih + ia), ia / (ih + ia))}[market]
    return {"provider": book["provider"] + " (derived)", "retrieved_at": book["retrieved_at"],
            "price_a": round(1 / p[0], 4), "price_b": round(1 / p[1], 4) if p[1] else None, "price_mid": None, "line": None,
            "executable": False, "synthetic": True}


def _stored_1x2(conn, match_id: str, as_of: str, kickoff: str) -> Optional[dict]:
    rows = conn.execute("SELECT q.* FROM soccer_odds_quotes q WHERE q.match_id = ? AND q.market = '1x2' AND q.price_kind = 'current' "
                        "AND q.retrieved_at <= ? AND q.retrieved_at < ? AND q.quote_id = (SELECT MAX(p.quote_id) FROM "
                        "soccer_odds_quotes p WHERE p.match_id = q.match_id AND lower(replace(p.provider, ' ', '')) = "
                        "lower(replace(q.provider, ' ', '')) AND p.market = q.market AND p.side = q.side AND "
                        "p.price_kind = q.price_kind AND p.retrieved_at <= ? AND p.retrieved_at < ?)",
                        (match_id, as_of, kickoff, as_of, kickoff)).fetchall()
    by = {r["side"]: r for r in rows if r["offered"] and r["decimal_odds"]}
    if set(by) != {"home", "draw", "away"}:
        return None
    return {"provider": by["home"]["provider"], "retrieved_at": max(r["retrieved_at"] for r in by.values()),
            **{s: by[s]["decimal_odds"] for s in by}}


# --- Forward test --------------------------------------------------------------------------------------------------------
def forecast_upcoming(conn: sqlite3.Connection, now: str, model_name: str, horizon_days: int = 7, competitions=None) -> list[dict]:
    """Records (outcome None) for scheduled matches kicking off after `now` within `horizon_days`, from information available
    at `now` only (forecast_cutoff = now). 1X2: the incumbent returns the stored live forecasts of soccer_live_forecasts
    (baselines have no stored forecast and are walk-only); count models are recomputed from the current inputs exactly as the
    live run does. Executable prices appear only when stored at or before `now`: ESPN DraftKings 1X2 / total goals, Bet365
    double chance; a double chance / draw no bet price derived from 1X2 prices is flagged synthetic."""
    table = _model_table()
    if model_name not in table:
        raise ValueError(f"unknown model {model_name!r}; known: {sorted(table)}")
    m = table[model_name]
    end = (datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ") + timedelta(days=horizon_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    comps = tuple(competitions or RESEARCH_COMPETITIONS)
    q = ",".join("?" * len(comps))
    sched = {r["match_id"]: r for r in conn.execute(
        f"SELECT match_id, competition_id, season, kickoff_at, home_team_id, away_team_id FROM soccer_matches WHERE status = 'scheduled' "
        f"AND kickoff_at > ? AND kickoff_at <= ? AND competition_id IN ({q})", (now, end, *comps))}
    out = []
    if m["family"] == "1x2":
        if m["kind"] != "elo":
            raise ValueError("a 1X2 baseline has no stored forecast: it is evaluated through walk only")
        for r in conn.execute("SELECT f.* FROM soccer_live_forecasts f WHERE f.version = (SELECT MAX(version) FROM soccer_live_forecasts g "
                              "WHERE g.match_id = f.match_id) AND f.generated_at <= ?", (now,)):
            s = sched.get(r["match_id"])
            if s is None:
                continue
            recs = one_x_two_records(r["match_id"], s["season"], s["kickoff_at"], min(r["generated_at"], now), (r["p_home"], r["p_draw"], r["p_away"]),
                                     None, s["competition_id"])
            book = _stored_1x2(conn, r["match_id"], now, s["kickoff_at"])
            for rec in recs:
                if book and rec["market"] == "result_1x2":
                    rec["quote"] = {"provider": book["provider"], "retrieved_at": book["retrieved_at"], "price_a": book["home"],
                                    "price_b": book["away"], "price_mid": book["draw"], "line": None, "executable": True, "synthetic": False}
                elif book:
                    rec["quote"] = _derived_quote(book, rec["market"])
                if rec["market"] in ("double_chance_1x", "double_chance_x2"):
                    b365 = _bet365(s, now)
                    side = "home_draw" if rec["market"] == "double_chance_1x" else "draw_away"
                    if b365 and side in b365["prices"]:
                        rec["quote"] = {"provider": "Bet365", "retrieved_at": b365["retrieved_at"], "price_a": b365["prices"][side]["decimal"],
                                        "price_b": None, "price_mid": None, "line": None, "executable": True, "synthetic": False}
            out += recs
        return sorted(out, key=lambda r: (r["start_at"], r["game_id"], r["market"]))
    family = m["family"]
    season = max((s["season"] for s in sched.values()), default=None)
    if season is None:
        return []
    data = collect(conn, family, season)
    lab, avail = _inputs_index(data)
    for mid, s in sorted(sched.items(), key=lambda kv: (kv[1]["kickoff_at"], kv[0])):
        if s["home_team_id"] == s["away_team_id"]:
            continue
        day = local_date(s["kickoff_at"], s["competition_id"])
        t = Obs(mid, s["competition_id"], s["season"], day, s["home_team_id"], s["away_team_id"], None, None, None)
        cutoff = min(cutoff_for(day), now)
        if m["kind"] == "count":
            r = walk(data, m["cfg"], (), targets=[t], hier=bool(m.get("hier")))[0]
            g, mh, ma = _joint(m, r["mh"], r["ma"]), r["mh"], r["ma"]
        else:
            g = _baseline_joint(m["kind"], family, lab, avail, cutoff, day, s["competition_id"])
            mh, ma = float((g.sum(1) * np.arange(g.shape[0])).sum()), float((g.sum(0) * np.arange(g.shape[0])).sum())
        out += count_records(mid, s["competition_id"], s["season"], s["kickoff_at"], cutoff, family, g, mh, ma, None, None)
    attach_quotes(conn, out)
    return out


def _bet365(s, now: str) -> Optional[dict]:
    from . import bet365_dc
    return bet365_dc.latest(s["match_id"], s["home_team_id"], s["away_team_id"], now)


# --- Data validation and readiness (report output) -------------------------------------------------------------------------
LEAGUE_FINALS = {"eng.1": 380, "esp.1": 380, "ita.1": 380, "ger.1": 306, "fra.1": 306}


def validate_data(conn: sqlite3.Connection, now: Optional[str] = None) -> dict:
    """Read-only checks of the soccer store for the research adapter: fixture identity, scores and corners of finals, coverage
    by competition, season and market, date ranges, gaps and the inputs a goals/corners forecast needs, with counts."""
    now = now or utc_now()
    rules, main = stage_rules(), _main_types(conn, 9999)
    rows = conn.execute("SELECT match_id, competition_id, season, season_type_id, stage, kickoff_at, home_team_id, away_team_id, status, "
                        "decided_in, home_score, away_score FROM soccer_matches ORDER BY kickoff_at, match_id").fetchall()
    corners = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT s.match_id, s.home_corners, s.away_corners FROM soccer_match_stats s WHERE s.revision = "
        "(SELECT MAX(revision) FROM soccer_match_stats t WHERE t.match_id = s.match_id)")}
    cov, rng = defaultdict(lambda: defaultdict(lambda: Counter())), {}
    pairs = defaultdict(list)
    bad_score = 0
    for r in rows:
        c, season = r["competition_id"], str(r["season"])
        if c not in RESEARCH_COMPETITIONS:
            continue
        elig = eligible_stage(c, r["season"], r["season_type_id"], r["stage"], main, rules)
        d = cov[c][season]
        d["matches"] += 1
        lo, hi = rng.get(c, (r["kickoff_at"], r["kickoff_at"]))
        rng[c] = (min(lo, r["kickoff_at"]), max(hi, r["kickoff_at"]))
        pairs[(c, r["season"], r["home_team_id"], r["away_team_id"], r["kickoff_at"][:10])].append(r["match_id"])
        if r["status"] != "final":
            d["not_final"] += 1
            continue
        d["finals"] += 1
        if r["home_score"] is None or r["away_score"] is None:
            bad_score += 1
        if not elig or r["decided_in"] != "regulation":
            d["not_usable"] += 1
            continue
        d["goals_labelled"] += 1
        hc = corners.get(r["match_id"])
        if hc is None:
            d["corners_missing"] += 1
        elif hc == (0, 0):
            d["corners_zero_zero"] += 1
        else:
            d["corners_labelled"] += 1
    dup = [{"competition": k[0], "season": k[1], "home": k[2], "away": k[3], "date": k[4], "match_ids": v}
           for k, v in sorted(pairs.items()) if len(v) > 1]
    keys = ("matches", "finals", "not_final", "not_usable", "goals_labelled", "corners_labelled", "corners_missing", "corners_zero_zero")
    coverage = {c: {s: {k: v.get(k, 0) for k in keys} for s, v in sorted(by.items())} for c, by in sorted(cov.items())}
    for c, by in coverage.items():
        want = LEAGUE_FINALS.get(c)
        for s, v in by.items():
            v["complete"] = want is None or v.get("finals", 0) + v.get("not_final", 0) >= want
            v["corner_share"] = round(v.get("corners_labelled", 0) / v["goals_labelled"], 3) if v.get("goals_labelled") else None
    today = now[:10]
    ready = {}
    for c in RESEARCH_COMPETITIONS:
        by = coverage.get(c, {})
        cur = max((int(s) for s in by), default=None)
        item = {}
        for fam, key in (("goals", "goals_labelled"), ("corners", "corners_labelled")):
            prev = by.get(str(cur - 1), {}).get(key, 0) if cur else 0
            now_n = by.get(str(cur), {}).get(key, 0) if cur else 0
            search_val = sum(v.get(key, 0) for s, v in by.items() if int(s) in (*DEV_TUNE, *DEV_FIT, *VALIDATION))
            need = 300 if c in LEAGUES else 1
            why = []
            if prev < need:
                why.append(f"previous season has {prev} labelled matches (< {need})")
            if search_val == 0:
                why.append("no labelled match in the search/validation seasons: nothing to develop or validate on")
            if fam == "corners" and by.get(str(cur), {}).get("corners_zero_zero", 0):
                why.append(f"{by[str(cur)]['corners_zero_zero']} current-season 0-0 corner counts (not delivered)")
            item[fam] = {"current_season": cur, "previous_season_labelled": prev, "current_season_labelled": now_n,
                         "search_validation_labelled": search_val, "forecastable": prev >= need, "evaluable": search_val > 0,
                         "problems": why}
        upcoming = conn.execute("SELECT COUNT(*) FROM soccer_matches WHERE competition_id = ? AND status = 'scheduled' AND kickoff_at > ? "
                                "AND kickoff_at <= date(?, '+7 days')", (c, now, today)).fetchone()[0]
        past_open = conn.execute("SELECT COUNT(*) FROM soccer_matches WHERE competition_id = ? AND status IN ('scheduled', 'in_progress') "
                                 "AND kickoff_at < ?", (c, now)).fetchone()[0]
        item["upcoming_7d"], item["kicked_off_unsettled"] = upcoming, past_open
        ready[c] = item
    quotes = {}
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'soccer_odds_quotes'").fetchone():
        quotes = {f"{r[0]}:{r[1]}": r[2] for r in conn.execute("SELECT market, provider, COUNT(DISTINCT match_id) FROM soccer_odds_quotes "
                                                              "WHERE offered = 1 GROUP BY 1, 2")}
    return {"as_of": now, "matches": len(rows), "fixtures": {"duplicate_pairings": dup, "finals_without_score": bad_score,
            "identity": "match_id = soccer:<competition>:<ESPN event id> (CHECK); one pairing per competition, season, date and ordered team pair"},
            "date_range": {c: {"first": a, "last": b} for c, (a, b) in sorted(rng.items())}, "coverage": coverage,
            "readiness": ready, "bookmaker_quotes_matches_by_market_provider": quotes,
            "search_validation_note": "goals/corners seasons before the first ESPN season in the store are absent unless "
                                      "`soccer history-months` was run into this store"}


# --- Reproducible comparison of adapter models (search / validation seasons) ----------------------------------------------
def _per_game(recs: list[dict]) -> dict:
    """Per game: mean log loss and Brier over its settled, non-push records, scoring a vs b conditional on no push (the
    stake-returned share carries no information); a three-way result scores its three probabilities."""
    acc = defaultdict(lambda: [0.0, 0.0, 0])
    for r in recs:
        if r["outcome"] in (None, "push", "void"):
            continue
        p = r["probs"]
        tot = sum(p.get(k, 0.0) for k in ("a", "mid", "b"))
        q = {k: min(1 - CLIP, max(CLIP, p[k] / tot)) for k in ("a", "mid", "b") if k in p}
        o = r["outcome"]
        x = acc[r["game_id"]]
        x[0] += -math.log(q[o])
        x[1] += sum((q[k] - (k == o)) ** 2 for k in q)
        x[2] += 1
    return {g: (v[0] / v[2], v[1] / v[2]) for g, v in acc.items()}


def research_compare(conn: sqlite3.Connection, names: list, seasons, incumbent: str, baselines=(), competitions=None,
                     bootstrap: bool = True) -> dict:
    """Walks each model over `seasons` (search/validation only; the holdout stays locked) and compares per game the mean market
    log loss and Brier: overall, per market line and per competition, each paired against `incumbent` (block bootstrap by ISO
    week; a negative difference favours the model) and against each baseline. Records with a stored executable bookmaker
    price are scored against the book's de-vigged probability in `bookmaker` (n 0 when no price was stored)."""
    walks = {n: adapter_walk(conn, seasons, n, competitions=competitions) for n in dict.fromkeys([incumbent, *baselines, *names])}
    per = {n: _per_game(r) for n, r in walks.items()}
    cut = {r["game_id"]: r["forecast_cutoff"] for r in walks[incumbent]}
    comp = {r["game_id"]: r.get("competition") for r in walks[incumbent]}

    def paired(n, ref, key, games):
        d = [per[n][g][key] - per[ref][g][key] for g in games]
        if not d:
            return {"n": 0}
        return backtest.block_bootstrap(d, [cut[g] for g in games]) if bootstrap else {"mean": sum(d) / len(d), "games": len(d)}
    games = sorted(set.intersection(*(set(per[n]) for n in per)) & set(cut))
    out = {"seasons": list(seasons), "incumbent": incumbent, "games": len(games), "models": {}}
    for n in per:
        ll = [per[n][g][0] for g in games]
        br = [per[n][g][1] for g in games]
        entry = {"mean_log_loss": sum(ll) / len(ll) if ll else None, "mean_brier": sum(br) / len(br) if br else None}
        if n != incumbent:
            entry["log_loss_vs_incumbent"] = paired(n, incumbent, 0, games)
            entry["brier_vs_incumbent"] = paired(n, incumbent, 1, games)
        for b in baselines:
            if b != n:
                entry[f"log_loss_vs_{b}"] = paired(n, b, 0, games)
        by_comp = defaultdict(list)
        for g in games:
            by_comp[comp.get(g)].append(g)
        entry["by_competition"] = {c: {"n": len(v), "mean_log_loss": sum(per[n][g][0] for g in v) / len(v),
                                       **({"log_loss_vs_incumbent": paired(n, incumbent, 0, v)["mean"]} if n != incumbent else {})}
                                   for c, v in sorted(by_comp.items(), key=lambda kv: str(kv[0]))}
        by_mk = defaultdict(list)
        for r in walks[n]:
            if r["outcome"] in ("a", "b", "mid"):
                p = r["probs"]
                tot = sum(p.get(k, 0.0) for k in ("a", "mid", "b"))
                by_mk[(r["market"], r["line"])].append((p["a"] / tot, r["outcome"] == "a"))
        entry["by_market"] = {f"{m}@{ln}": {"n": len(v), "brier_a": sum((p - y) ** 2 for p, y in v) / len(v),
                                            "mean_pred": sum(p for p, _ in v) / len(v), "observed": sum(y for _, y in v) / len(v)}
                              for (m, ln), v in sorted(by_mk.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0))}
        priced = [r for r in walks[n] if r["quote"] and r["outcome"] in ("a", "b")]
        bk = []
        for r in priced:
            ia, ib = 1 / r["quote"]["price_a"], 1 / r["quote"]["price_b"]
            bk.append((r["probs"]["a"] / (r["probs"]["a"] + r["probs"]["b"]), ia / (ia + ib), r["outcome"] == "a"))
        entry["bookmaker"] = {"n": len(bk)} if not bk else {
            "n": len(bk), "model_brier": sum((p - y) ** 2 for p, _, y in bk) / len(bk),
            "book_brier": sum((q - y) ** 2 for _, q, y in bk) / len(bk)}
        out["models"][n] = entry
    return out
