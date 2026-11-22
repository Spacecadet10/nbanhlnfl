"""Preregistered evaluation of the soccer goal and corner count models (protocol: soccer_markets_protocol.json; plan:
docs/SOCCER_MARKETS_PLAN.md). Data: ESPN results (soccer_matches) and corner counts (soccer_match_stats) of the seven club
competitions, 2010-11 onwards, one research store. Nothing here reads the 1X2 evaluation files or edits them.

Rules enforced in code:
- seasons after the last open season are read only with a valid sealed candidate of the family, and the read is logged
  BEFORE it happens; prospective seasons are never read (`load`);
- every forecast of date D uses only labels available by D 06:00 UTC from matches of earlier dates (`walk`, LeakageError);
- one development run and one reserved-test run per family (experiments / access logs outside the repository);
- the frozen model refits from its inputs at every cutoff with no state carried between cutoffs, so the live forecast of a
  date is the evaluated forecast of that date by construction (app/soccer_markets_live.py calls `forecast_targets`).
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import os
import sqlite3
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import numpy as np
from scipy.optimize import minimize, minimize_scalar
from scipy.special import gammaln
from scipy.stats import poisson as _poisson

from . import backtest
from .backtest import LeakageError
from .db import utc_now

PROTOCOL_PATH = Path(__file__).with_name("soccer_markets_protocol.json")
P = json.loads(PROTOCOL_PATH.read_text())
FAMILIES = ("goals", "corners")
COMPETITIONS = tuple(P["dataset"]["competitions"])
LEAGUES = ("eng.1", "esp.1", "ger.1", "ita.1", "fra.1")
PER = P["periods"]
WARMUP, DEV_TUNE, DEV_FIT = tuple(PER["warmup"]), tuple(PER["development_tune"]), tuple(PER["development_fit"])
VALIDATION, TEST, PROSPECTIVE = tuple(PER["validation"]), tuple(PER["reserved_test"]), tuple(PER["prospective_locked"])
LAST_OPEN = PER["last_open_season"]
FREQ_SEASONS = tuple(range(2010, 2020))
CAP = {"goals": 10, "corners": 25}
DEFAULT_MEAN = {"goals": 1.35, "corners": 5.0}
WINDOW_DAYS = 730
CLIP = 1e-15
BINS = 10
ZONES = {"eng.1": "Europe/London", "esp.1": "Europe/Madrid", "ger.1": "Europe/Berlin", "ita.1": "Europe/Rome",
         "fra.1": "Europe/Paris", "uefa.champions": "Europe/Paris", "uefa.europa": "Europe/Paris"}
GRID = P["candidates"]["poisson"]["grid"]
MARKET_LINES = P["metrics"]["markets_reported"]
FAMILY_MARKETS = {"goals": ("goals_total_over", "goals_home_over", "goals_away_over", "btts_yes"),
                  "corners": ("corners_total_over", "corners_home_over", "corners_away_over", "corners_handicap_home")}
# The files whose bytes decide an evaluation result (the settlement/display module app/soccer_markets.py is not one: the
# evaluation scores half-line events with its own masks, which tests/test_soccer_markets_eval.py checks against it).
CODE_FILES = ("soccer_markets_eval.py", "soccer_markets_protocol.json", "backtest.py")


class TestSeasonsLocked(PermissionError):
    pass


class RunAlreadyDone(PermissionError):
    pass


class FrozenMismatch(RuntimeError):
    pass


def protocol_sha256() -> str:
    return hashlib.sha256(PROTOCOL_PATH.read_bytes()).hexdigest()


def code_digest() -> dict:
    here = Path(__file__).parent
    return {f: hashlib.sha256((here / f).read_bytes()).hexdigest() for f in CODE_FILES}


def log_dir() -> Path:
    """Shared by every data folder (a development copy and a full store count as the same run); never inside data/."""
    return Path(os.environ.get("SOCCER_MARKETS_LOG_DIR", Path.home() / "nba-predictor-research-data" / "soccer-markets-eval-logs"))


def experiments_log_path() -> Path:
    return log_dir() / "experiments.jsonl"


def access_log_path() -> Path:
    return log_dir() / "test_access.jsonl"


def _append(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps({"at": utc_now(), **entry}, sort_keys=True) + "\n")


def _entries(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def valid_seal(c: Optional[dict]) -> bool:
    return bool(c) and backtest.seal(c).get("sha256") == c.get("sha256") and c.get("family") in FAMILIES


# --- Eligibility (protocol `eligibility`, amendment 1) -----------------------------------------------------------------
def stage_rules() -> dict:
    for a in P.get("amendments", []):
        if "stage_rules" in a:
            return a["stage_rules"]
    raise RuntimeError("the protocol's stage rules (amendment 1) are not written yet: nothing can be loaded")


def local_date(kickoff_at: str, competition: str) -> str:
    t = datetime.strptime(kickoff_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return t.astimezone(ZoneInfo(ZONES[competition])).date().isoformat()


def cutoff_for(day: str) -> str:
    return f"{day}T06:00:00Z"


@dataclass(frozen=True)
class Obs:
    """A usable match (eligible stage, final in regulation) or a scheduled target; label = the family's counts."""
    mid: str
    competition: str
    season: int
    local_date: str
    home: str
    away: str
    available_at: Optional[str]
    h: Optional[int]
    a: Optional[int]


@dataclass
class Data:
    family: str
    obs: list                     # usable matches of seasons <= through (labelled or not)
    members: dict                 # (league, season) -> frozenset of team ids (regular-season schedule, any status)
    through: int
    counts: dict


def _main_types(conn: sqlite3.Connection, through: int) -> dict:
    """(league, season) -> the season type with the most matches (the regular season), from the schedule only."""
    out, best = {}, {}
    for c, s, t, n in conn.execute("SELECT competition_id, season, season_type_id, COUNT(*) FROM soccer_matches "
                                   "WHERE season <= ? GROUP BY 1, 2, 3", (through,)):
        if c in LEAGUES and (best.get((c, s), -1) < n or (best.get((c, s)) == n and t < out[(c, s)])):
            out[(c, s)], best[(c, s)] = t, n
    return out


def eligible_stage(competition: str, season: int, season_type: int, stage: Optional[str], main: dict, rules: dict) -> bool:
    if competition in LEAGUES:
        return main.get((competition, season)) == season_type
    return (stage or "") in rules["uefa_group_stages"]


def load(conn: sqlite3.Connection, family: str, *, through: int, sealed: Optional[dict] = None,
         access_log: Optional[Path] = None, purpose: str = "") -> Data:
    """Usable matches and league membership of seasons <= `through` (later seasons are never read: filtered in SQL).
    Seasons after LAST_OPEN need a valid sealed candidate of this family and an access-log entry written first."""
    if family not in FAMILIES:
        raise ValueError(family)
    if through > TEST[-1]:
        raise TestSeasonsLocked(f"seasons after {TEST[-1]} are prospective and locked: no evaluation reads them")
    if through > LAST_OPEN:
        if not valid_seal(sealed) or sealed["family"] != family:
            raise TestSeasonsLocked(f"seasons {LAST_OPEN + 1}-{through} are reserved: a valid sealed {family} candidate is required")
        if access_log is None:
            raise TestSeasonsLocked("a reserved-season read must be logged: pass access_log")
        _append(access_log, {"family": family, "through": through, "candidate_sha256": sealed["sha256"], "purpose": purpose})
    return collect(conn, family, through)


def collect(conn: sqlite3.Connection, family: str, through: int) -> Data:
    """The rows behind `load`, without its gate: only `load` (evaluation) and app/soccer_markets_live.py (live inputs,
    which logs its own read) call it."""
    rules = stage_rules()
    main = _main_types(conn, through)
    q = ",".join("?" * len(COMPETITIONS))
    members = defaultdict(set)
    obs, counts = [], Counter()
    stats = {}
    if family == "corners":
        for mid, hc, ac, av in conn.execute(
                "SELECT s.match_id, s.home_corners, s.away_corners, s.available_at FROM soccer_match_stats s "
                "JOIN soccer_matches m ON m.match_id = s.match_id WHERE m.season <= ? AND s.revision = "
                "(SELECT MAX(revision) FROM soccer_match_stats t WHERE t.match_id = s.match_id)", (through,)):
            stats[mid] = (hc, ac, av)
    for r in conn.execute(f"SELECT match_id, competition_id, season, season_type_id, stage, kickoff_at, home_team_id, "
                          f"away_team_id, status, decided_in, home_score, away_score, result_available_at FROM soccer_matches "
                          f"WHERE season <= ? AND competition_id IN ({q}) ORDER BY kickoff_at, match_id",
                          (through, *COMPETITIONS)):
        mid, comp, season, stype, stage, kick, home, away, status, decided, hs, as_, rav = r
        if not eligible_stage(comp, season, stype, stage, main, rules):
            continue
        if comp in LEAGUES:
            members[(comp, season)].update({home, away})
        if status != "final" or decided != "regulation" or home == away:
            continue
        day = local_date(kick, comp)
        if family == "goals":
            label = (hs, as_, rav)
        else:
            s = stats.get(mid)
            label = (s[0], s[1], s[2]) if s else (None, None, None)
            if s and s[0] == 0 and s[1] == 0:            # amendment 2: a both-zero count is "not delivered"
                counts[f"zero_zero:{comp}:{season}"] += 1
                label = (None, None, None)
        if label[0] is None:
            counts[f"unlabelled:{comp}:{season}"] += 1
        counts[f"usable:{comp}:{season}"] += 1
        obs.append(Obs(mid, comp, season, day, home, away, label[2] if label[0] is not None else None, label[0], label[1]))
    return Data(family, obs, {k: frozenset(v) for k, v in members.items()}, through, dict(counts))


def corner_season_ok(data: Data) -> dict:
    """(competition, season) -> whether >= 90 % of its usable matches have a corner label (protocol)."""
    out = {}
    for k, n in data.counts.items():
        if k.startswith("usable:"):
            _, c, s = k.split(":")
            miss = data.counts.get(f"unlabelled:{c}:{s}", 0)
            out[(c, int(s))] = (n - miss) / n >= 0.9 if n else False
    return out


def scored(data: Data, seasons) -> list[Obs]:
    keep = set(seasons) - set(WARMUP)
    ok = corner_season_ok(data) if data.family == "corners" else None
    return [o for o in data.obs if o.season in keep and o.h is not None and (ok is None or ok.get((o.competition, o.season)))]


# --- Count models ------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Config:
    half_life_days: float
    tau: float
    D: float


def grid_configs(family: str) -> list[Config]:
    g = GRID[family]
    return [Config(h, t, d) for h in g["half_life_days"] for t in g["tau"] for d in g["D"]]


def tiebreak(cfg: Config) -> tuple:
    return (-cfg.half_life_days, -cfg.tau, cfg.D)


def _league_of(members: dict, team: str, season: int) -> Optional[str]:
    for lg in LEAGUES:
        if team in members.get((lg, season), ()):
            return lg
    return None


def centre(members: dict, team: str, season: int, D: float) -> float:
    """0, or -D for a team promoted into its league-season (member now, not in the covered previous season)."""
    lg = _league_of(members, team, season)
    if lg is None:
        return 0.0
    before = members.get((lg, season - 1))
    return -D if before and team not in before else 0.0


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


# --- Joint grids -------------------------------------------------------------------------------------------------------
def _fold(pmf: np.ndarray, sf_last: float) -> np.ndarray:
    out = pmf.copy()
    out[-1] = sf_last
    return out


def poisson_marginal(m: float, cap: int) -> np.ndarray:
    k = np.arange(cap + 1)
    return _fold(_poisson.pmf(k, m), float(_poisson.sf(cap - 1, m)))


def nb_marginal(m: float, k_disp: float, cap: int) -> np.ndarray:
    y = np.arange(cap + 1)
    logp = (gammaln(y + k_disp) - gammaln(k_disp) - gammaln(y + 1) + k_disp * np.log(k_disp / (k_disp + m))
            + y * np.log(m / (k_disp + m)))
    pmf = np.exp(logp)
    pmf[-1] = max(0.0, 1.0 - pmf[:-1].sum())
    return pmf


def poisson_joint(mh: float, ma: float, cap: int) -> np.ndarray:
    return np.outer(poisson_marginal(mh, cap), poisson_marginal(ma, cap))


def dc_joint(mh: float, ma: float, rho: float, cap: int) -> np.ndarray:
    g = poisson_joint(mh, ma, cap)
    r = min(max(rho, max(-1 / mh, -1 / ma)), min(1 / (mh * ma), 1.0))
    g[0, 0] *= 1 - mh * ma * r
    g[0, 1] *= 1 + mh * r
    g[1, 0] *= 1 + ma * r
    g[1, 1] *= 1 - r
    return g / g.sum()


def nb_joint(mh: float, ma: float, k_disp: float, cap: int) -> np.ndarray:
    return np.outer(nb_marginal(mh, k_disp, cap), nb_marginal(ma, k_disp, cap))


def joint_of(model: str, family: str, mh: float, ma: float, extra: Optional[float] = None) -> np.ndarray:
    cap = CAP[family]
    if model == "poisson":
        return poisson_joint(mh, ma, cap)
    if model == "dixon_coles":
        return dc_joint(mh, ma, extra, cap)
    if model == "negative_binomial":
        return nb_joint(mh, ma, extra, cap)
    raise ValueError(model)


def fit_frequency(data: Data) -> dict:
    """Per competition: home and away count marginals over usable labelled 2010-2019 matches, +0.5 per cell."""
    cap = CAP[data.family]
    acc = defaultdict(lambda: [np.full(cap + 1, 0.5), np.full(cap + 1, 0.5)])
    for o in data.obs:
        if o.season in FREQ_SEASONS and o.h is not None:
            acc[o.competition][0][min(o.h, cap)] += 1
            acc[o.competition][1][min(o.a, cap)] += 1
    return {c: [list(v[0] / v[0].sum()), list(v[1] / v[1].sum())] for c, v in acc.items()}


def frequency_joint(freq: dict, competition: str) -> np.ndarray:
    h, a = freq[competition]
    return np.outer(np.array(h), np.array(a))


# --- Walk-forward ------------------------------------------------------------------------------------------------------
def _inputs_index(data: Data) -> tuple[list, list]:
    lab = sorted((o for o in data.obs if o.h is not None), key=lambda o: (o.available_at, o.mid))
    return lab, [o.available_at for o in lab]


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
        j = bisect.bisect_right(avail, cutoff)
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


def _walk_job(args):
    data, cfg, seasons = args
    return cfg, walk(data, cfg, seasons)


# --- Metrics -----------------------------------------------------------------------------------------------------------
def nll(joint: np.ndarray, h: int, a: int, cap: int) -> float:
    return -math.log(max(CLIP, float(joint[min(h, cap), min(a, cap)])))


def event_masks(family: str) -> dict:
    """(market, line) -> boolean cap+1 x cap+1 mask of the cells where the 'over / yes / home' side wins (half lines)."""
    cap = CAP[family]
    h, a = np.meshgrid(np.arange(cap + 1), np.arange(cap + 1), indexing="ij")
    out = {}
    for m in FAMILY_MARKETS[family]:
        for line in MARKET_LINES[m]:
            if m.endswith("total_over"):
                mask = h + a > line
            elif m.endswith("home_over"):
                mask = h > line
            elif m.endswith("away_over"):
                mask = a > line
            elif m == "btts_yes":
                mask = (h > 0) & (a > 0)
            elif m == "corners_handicap_home":
                mask = h + line > a
            else:
                raise ValueError(m)
            out[(m, line)] = mask
    return out


def totals_bins(family: str) -> list[tuple[str, int, int]]:
    if family == "goals":
        return [(str(k), k, k) for k in range(6)] + [("6+", 6, 10 ** 6)]
    return [("<=6", 0, 6)] + [(str(k), k, k) for k in range(7, 15)] + [("15+", 15, 10 ** 6)]


def score(preds: list[dict], family: str, joint_fn) -> list[dict]:
    """Adds per-match joint log loss, event probabilities and outcomes, and the total-count distribution."""
    cap = CAP[family]
    masks = event_masks(family)
    tbins = totals_bins(family)
    hh, aa = np.meshgrid(np.arange(cap + 1), np.arange(cap + 1), indexing="ij")
    tot = hh + aa
    out = []
    for p in preds:
        g = joint_fn(p)
        ev = {f"{m}@{ln}": float(g[mask].sum()) for (m, ln), mask in masks.items()}
        y = {f"{m}@{ln}": bool(mask[min(p["h"], cap), min(p["a"], cap)]) for (m, ln), mask in masks.items()}
        tdist = {lab: float(g[(tot >= lo) & (tot <= hi)].sum()) for lab, lo, hi in tbins}
        t = p["h"] + p["a"]
        out.append({**p, "nll": nll(g, p["h"], p["a"], cap), "ev": ev, "y": y, "tdist": tdist,
                    "tobs": next(lab for lab, lo, hi in tbins if lo <= t <= hi)})
    return out


def _binary(ps: list[float], ys: list[bool]) -> dict:
    n = len(ps)
    brier = sum((p - y) ** 2 for p, y in zip(ps, ys)) / n
    ll = -sum(math.log(min(1 - CLIP, max(CLIP, p if y else 1 - p))) for p, y in zip(ps, ys)) / n
    bins = defaultdict(list)
    for p, y in zip(ps, ys):
        bins[min(BINS - 1, int(p * BINS))].append((p, y))
    ece = sum(len(v) / n * abs(sum(p for p, _ in v) / len(v) - sum(y for _, y in v) / len(v)) for v in bins.values())
    rel = []
    for b in range(BINS):
        v = bins.get(b, [])
        rel.append({"bin": [b / BINS, (b + 1) / BINS], "n": len(v),
                    "mean_predicted": sum(p for p, _ in v) / len(v) if v else None,
                    "observed": sum(y for _, y in v) / len(v) if v else None})
    return {"n": n, "brier": brier, "log_loss": ll, "mean_predicted": sum(ps) / n, "observed": sum(ys) / n, "ece": ece,
            "reliability": rel}


def summary(rows: list[dict], base: Optional[list[dict]] = None, bootstrap: bool = True) -> dict:
    """Joint log loss (+ interval), every market line, the total distribution; paired against `base` when given."""
    if not rows:
        return {"n": 0}
    out = {"n": len(rows), "joint_log_loss": sum(r["nll"] for r in rows) / len(rows)}
    if bootstrap:
        b = backtest.block_bootstrap([r["nll"] for r in rows], [r["cutoff_at"] for r in rows])
        out["joint_log_loss_ci95"] = b["ci95"]
    bm = {r["mid"]: r for r in base} if base is not None else None
    if bm is not None:
        d = [r["nll"] - bm[r["mid"]]["nll"] for r in rows]
        out["vs_frequency"] = backtest.block_bootstrap(d, [r["cutoff_at"] for r in rows]) if bootstrap else {"mean": sum(d) / len(d)}
    markets = {}
    for key in rows[0]["ev"]:
        m = _binary([r["ev"][key] for r in rows], [r["y"][key] for r in rows])
        if bm is not None:
            bb = _binary([bm[r["mid"]]["ev"][key] for r in rows], [r["y"][key] for r in rows])
            m["frequency_brier"], m["frequency_ece"] = bb["brier"], bb["ece"]
            if bootstrap:
                dd = [(r["ev"][key] - r["y"][key]) ** 2 - (bm[r["mid"]]["ev"][key] - r["y"][key]) ** 2 for r in rows]
                m["brier_vs_frequency"] = backtest.block_bootstrap(dd, [r["cutoff_at"] for r in rows])
        markets[key] = m
    out["markets"] = markets
    labs = list(rows[0]["tdist"])
    out["totals"] = {lab: {"mean_predicted": sum(r["tdist"][lab] for r in rows) / len(rows),
                           "observed": sum(r["tobs"] == lab for r in rows) / len(rows)} for lab in labs}
    out["mean_count"] = {"home_predicted": sum(r.get("mh", 0) for r in rows) / len(rows),
                         "home_observed": sum(r["h"] for r in rows) / len(rows),
                         "away_predicted": sum(r.get("ma", 0) for r in rows) / len(rows),
                         "away_observed": sum(r["a"] for r in rows) / len(rows)}
    return out


def breakdown(rows: list[dict], base: Optional[list[dict]] = None) -> dict:
    by_c, by_s = defaultdict(list), defaultdict(list)
    for r in rows:
        by_c[r["competition"]].append(r)
        by_s[r["season"]].append(r)
    bm = {r["mid"]: r for r in base} if base is not None else None
    sub = (lambda v: [bm[r["mid"]] for r in v]) if bm is not None else (lambda v: None)
    return {"overall": summary(rows, base),
            "by_competition": {c: summary(v, sub(v)) for c, v in sorted(by_c.items())},
            "by_season": {str(s): summary(v, sub(v), bootstrap=False) for s, v in sorted(by_s.items())}}


def paired(a: list[dict], b: list[dict]) -> dict:
    bm = {r["mid"]: r for r in b}
    if set(bm) != {r["mid"] for r in a}:
        raise ValueError("paired comparison needs the same matches")
    return backtest.block_bootstrap([r["nll"] - bm[r["mid"]]["nll"] for r in a], [r["cutoff_at"] for r in a])


# --- Second-stage fits on development_fit ------------------------------------------------------------------------------
def fit_rho(preds: list[dict]) -> dict:
    bad = sorted({p["season"] for p in preds} - set(DEV_FIT))
    if bad:
        raise LeakageError(f"rho is fitted on development_fit seasons only; got {bad}")
    cap = CAP["goals"]

    def loss(rho):
        return sum(nll(dc_joint(p["mh"], p["ma"], rho, cap), p["h"], p["a"], cap) for p in preds) / len(preds)
    r = minimize_scalar(loss, bounds=(-0.3, 0.3), method="bounded", options={"xatol": 1e-6})
    return {"rho": float(r.x), "n": len(preds), "mean_log_loss": float(r.fun), "at_bound": abs(abs(r.x) - 0.3) < 1e-4}


def fit_dispersion(preds: list[dict]) -> dict:
    bad = sorted({p["season"] for p in preds} - set(DEV_FIT))
    if bad:
        raise LeakageError(f"the dispersion is fitted on development_fit seasons only; got {bad}")
    cap = CAP["corners"]

    def loss(logk):
        k = math.exp(logk)
        return sum(nll(nb_joint(p["mh"], p["ma"], k, cap), p["h"], p["a"], cap) for p in preds) / len(preds)
    lo, hi = math.log(0.5), math.log(10000)
    r = minimize_scalar(loss, bounds=(lo, hi), method="bounded", options={"xatol": 1e-6})
    return {"k": float(math.exp(r.x)), "n": len(preds), "mean_log_loss": float(r.fun),
            "at_bound": min(abs(r.x - lo), abs(r.x - hi)) < 1e-4}


SECOND = {"goals": ("dixon_coles", fit_rho, "rho"), "corners": ("negative_binomial", fit_dispersion, "k")}


def select_config(rows: list[tuple[float, Config]]) -> Config:
    """Lowest development_tune log loss; within 1e-9 of it: larger half-life, then larger tau, then smaller D."""
    best = min(ll for ll, _ in rows)
    return sorted((cfg for ll, cfg in rows if ll - best <= 1e-9), key=tiebreak)[0]


def choose(second: str, se: list[dict], po: list[dict], fq: list[dict]) -> tuple[dict, str, dict, bool]:
    """Validation choice: the second-stage model only if its paired interval against poisson lies below 0; the chosen
    model goes on only if its paired interval against frequency lies below 0."""
    vs_poisson = paired(se, po)
    chosen = second if vs_poisson["ci95"][1] < 0 else "poisson"
    vs_base = paired(se if chosen == second else po, fq)
    return vs_poisson, chosen, vs_base, vs_base["ci95"][1] < 0


# --- Development (once per family) -------------------------------------------------------------------------------------
def data_digest(conn: sqlite3.Connection, through: int) -> str:
    h = hashlib.sha256()
    for r in conn.execute("SELECT match_id, status, decided_in, home_score, away_score, result_available_at FROM "
                          "soccer_matches WHERE season <= ? ORDER BY match_id", (through,)):
        h.update(json.dumps(list(r)).encode())
    for r in conn.execute("SELECT s.match_id, s.revision, s.home_corners, s.away_corners, s.available_at FROM soccer_match_stats s "
                          "JOIN soccer_matches m ON m.match_id = s.match_id WHERE m.season <= ? ORDER BY 1, 2", (through,)):
        h.update(json.dumps(list(r)).encode())
    return h.hexdigest()


def _check_protocol_logged(log: Path) -> None:
    hs = [e["protocol_sha256"] for e in _entries(log) if e.get("stage") in ("protocol_written", "protocol_amended")]
    if not hs or hs[-1] != protocol_sha256():
        raise FrozenMismatch("the protocol file differs from the last logged version: log the amendment first")


def develop(conn: sqlite3.Connection, family: str, *, out_dir: Path, experiments_log: Path, workers: int = 6,
            progress=print) -> dict:
    """Development (grid on development_tune, second-stage fit on development_fit) and the validation choice of one
    family; writes the report and, when the chosen model beats the baseline on validation, the sealed candidate."""
    _check_protocol_logged(experiments_log)
    log = _entries(experiments_log)
    retired = {e["candidate_sha256"] for e in log if e.get("stage") == "candidate_retired"}
    if any(e.get("stage") == "develop_completed" and e.get("family") == family and e.get("candidate_sha256") not in retired
           for e in log):
        raise RunAlreadyDone(f"the {family} development run is complete; it is never repeated (unless its candidate is "
                             f"retired by a logged, user-approved protocol amendment)")
    _append(experiments_log, {"stage": "develop_started", "family": family, "protocol_sha256": protocol_sha256(),
                              "code": code_digest()})
    out_dir.mkdir(parents=True, exist_ok=True)
    data = load(conn, family, through=LAST_OPEN)
    freq = fit_frequency(data)
    cap = CAP[family]
    report = {"family": family, "protocol_sha256": protocol_sha256(), "code": code_digest(),
              "data_digest": data_digest(conn, LAST_OPEN), "counts": data.counts, "frequency": freq}
    if family == "corners":
        report["corner_seasons_scored"] = {f"{c}:{s}": ok for (c, s), ok in sorted(corner_season_ok(data).items())}
    # 1. grid on development_tune
    configs = grid_configs(family)
    dev_seasons = (*DEV_TUNE, *DEV_FIT)
    progress(f"{family}: walking {len(configs)} configurations over {dev_seasons}")
    with ProcessPoolExecutor(max_workers=workers) as ex:
        walked = dict(ex.map(_walk_job, [(data, c, dev_seasons) for c in configs]))
    rows = []
    for cfg in configs:
        tune = [p for p in walked[cfg] if p["season"] in DEV_TUNE]
        ll = sum(nll(poisson_joint(p["mh"], p["ma"], cap), p["h"], p["a"], cap) for p in tune) / len(tune)
        rows.append((ll, tiebreak(cfg), cfg, len(tune)))
    rows.sort(key=lambda r: (r[0], r[1]))
    selected = select_config([(ll, cfg) for ll, _, cfg, _ in rows])
    report["development_tune"] = {"grid": [{"config": asdict(c), "log_loss": ll, "n": n} for ll, _, c, n in rows],
                                  "selected": asdict(selected),
                                  "on_grid_edge": [k for k, v in asdict(selected).items()
                                                   if v in (min(GRID[family][k]), max(GRID[family][k])) and len(GRID[family][k]) > 1]}
    # 2. second-stage fit on development_fit
    second, fitter, key = SECOND[family]
    fit_rows = [p for p in walked[selected] if p["season"] in DEV_FIT]
    report["development_fit"] = {second: fitter(fit_rows)}
    extra = report["development_fit"][second][key]
    progress(f"{family}: selected {asdict(selected)}; {key} = {extra:.4f}")
    # 3. validation
    val = walk(data, selected, VALIDATION)
    fq = score(val, family, lambda p: frequency_joint(freq, p["competition"]))
    po = score(val, family, lambda p: poisson_joint(p["mh"], p["ma"], cap))
    se = score(val, family, lambda p: joint_of(second, family, p["mh"], p["ma"], extra))
    second_vs_poisson, chosen, vs_base, passed = choose(second, se, po, fq)
    report["validation"] = {"n": len(val), "frequency": breakdown(fq), "poisson": breakdown(po, fq),
                            second: breakdown(se, fq),
                            "choice": {f"{second}_minus_poisson": second_vs_poisson, "chosen": chosen,
                                       "chosen_minus_frequency": vs_base, "beats_baseline": passed}}
    sealed = None
    if passed:
        sealed = backtest.seal({"family": family, "model": chosen, "config": asdict(selected),
                                "extra": {key: extra} if chosen == second else {}, "frequency": freq,
                                "protocol_sha256": protocol_sha256(), "code": code_digest(),
                                "data_digest": report["data_digest"], "developed_at": utc_now()})
        (out_dir / f"sealed_{family}.json").write_text(json.dumps(sealed, indent=1, sort_keys=True))
    (out_dir / f"development_validation_{family}.json").write_text(json.dumps(report, indent=1, sort_keys=True, default=str))
    _write_rows(out_dir / f"validation_rows_{family}.json", val)
    _append(experiments_log, {"stage": "develop_completed", "family": family, "chosen": chosen, "beats_baseline": passed,
                              "candidate_sha256": sealed["sha256"] if sealed else None})
    return {"report": report, "sealed": sealed}


def _write_rows(path: Path, rows: list[dict]) -> None:
    """Per-match evidence (id, competition, season, cutoff, the model's two means, the observed counts), enough to recompute
    every reported metric and the reading with the sealed model's grid."""
    keys = ("mid", "competition", "season", "cutoff_at", "mh", "ma", "h", "a")
    path.write_text(json.dumps([[r[k] for k in keys] for r in rows]))


# --- Frozen model (test and live) --------------------------------------------------------------------------------------
def frozen_config(sealed: dict) -> Config:
    return Config(**sealed["config"])


def frozen_joint(sealed: dict, mh: float, ma: float) -> np.ndarray:
    extra = next(iter(sealed["extra"].values()), None) if sealed["extra"] else None
    return joint_of(sealed["model"], sealed["family"], mh, ma, extra)


def check_frozen(sealed: dict) -> None:
    if not valid_seal(sealed):
        raise FrozenMismatch("the sealed candidate's digest does not match its content")
    if sealed["code"] != code_digest() or sealed["protocol_sha256"] != protocol_sha256():
        raise FrozenMismatch("the evaluation code or protocol changed after sealing")


def forecast_targets(sealed: dict, data: Data, targets: list[Obs]) -> list[dict]:
    """The frozen model's forecasts of `targets` (one date or several), each from its own cutoff's inputs."""
    rows = walk(data, frozen_config(sealed), (), targets=targets)
    for r in rows:
        r["joint"] = frozen_joint(sealed, r["mh"], r["ma"])
    return rows


# --- Reserved test (once per family) -----------------------------------------------------------------------------------
def run_test(conn: sqlite3.Connection, sealed: dict, *, access_log: Path, experiments_log: Path, out_dir: Path) -> dict:
    check_frozen(sealed)
    family = sealed["family"]
    if out_dir.exists():
        raise FileExistsError(f"{out_dir} exists: the test writes a new folder")
    if any(e.get("family") == family and e.get("purpose", "").startswith("reserved test") for e in _entries(access_log)):
        raise RunAlreadyDone(f"the {family} reserved test has been run; it is never repeated")
    if any(e.get("stage") == "candidate_retired" and e.get("candidate_sha256") == sealed["sha256"]
           for e in _entries(experiments_log)):
        raise RunAlreadyDone(f"candidate {sealed['sha256'][:8]} is retired; it is never tested")
    data = load(conn, family, through=TEST[-1], sealed=sealed, access_log=access_log,
                purpose=f"reserved test {family} (one-shot)")
    out_dir.mkdir(parents=True)
    cap = CAP[family]
    rows = walk(data, frozen_config(sealed), TEST)
    fq = score(rows, family, lambda p: frequency_joint(sealed["frequency"], p["competition"]))
    md = score(rows, family, lambda p: frozen_joint(sealed, p["mh"], p["ma"]))
    rep = {"family": family, "candidate_sha256": sealed["sha256"], "model": sealed["model"], "counts": data.counts,
           "test": breakdown(md, fq), "frequency": breakdown(fq)}
    if family == "corners":
        rep["corner_seasons_scored"] = {f"{c}:{s}": ok for (c, s), ok in sorted(corner_season_ok(data).items()) if s in TEST}
    rep["reading"] = read_test(rep)
    (out_dir / "test_report.json").write_text(json.dumps(rep, indent=1, sort_keys=True))
    (out_dir / "reading.json").write_text(json.dumps(rep["reading"], indent=1, sort_keys=True))
    (out_dir / "sealed_candidate_used.json").write_text(json.dumps(sealed, indent=1, sort_keys=True))
    _write_rows(out_dir / "test_rows.json", rows)
    _append(experiments_log, {"stage": "test_completed", "family": family, "candidate_sha256": sealed["sha256"],
                              "reading": rep["reading"]["verdict"]})
    return rep


def read_test(rep: dict) -> dict:
    """Protocol `reserved_test_reading` R1-R4, applied mechanically (unrounded floats; a tie fails '<')."""
    from . import soccer_live
    t = rep["test"]
    r1 = t["overall"]["vs_frequency"]["ci95"][1] < 0
    by_market = defaultdict(list)
    for key, m in t["overall"]["markets"].items():
        by_market[key.split("@")[0]].append((key, m["ece"]))
    r2 = {mk: all(e <= 0.05 for _, e in v) for mk, v in by_market.items()}
    r3 = {c: (v["n"] >= 300 and v["vs_frequency"]["ci95"][1] < 0) for c, v in t["by_competition"].items()}
    r4 = [k for k, m in t["overall"]["markets"].items() if abs(m["mean_predicted"] - m["observed"]) > 0.03]
    comps = sorted(c for c, ok in r3.items() if ok and c in soccer_live.LAUNCH) if r1 else []
    markets = sorted(m for m, ok in r2.items() if ok) if r1 else []
    return {"R1_family_skill": r1, "R2_market_calibration": r2, "R3_competition_scope": r3,
            "R4_disclosed_lines": r4, "served_competitions": comps if markets else [],
            "served_markets": markets if comps else [],
            "verdict": "serve" if (r1 and comps and markets) else "do_not_serve"}


# --- Double chance and draw no bet: the frozen 1X2 Elo on its open validation seasons (protocol `not_modelled_here`) ----
def derived_validation(conn: sqlite3.Connection) -> dict:
    """Double-chance and draw-no-bet metrics of the sealed 1X2 Elo (app/soccer_live.sealed_candidate) on validation
    2020-2022: openfootball seasons <= 2022 only (no reserved season is read; the 1X2 test is not rerun). Double chance =
    1 - P(one outcome); draw no bet = P(home) / (P(home) + P(away)), scored on non-drawn matches (a draw is a push). Per
    competition and overall: n, Brier, log loss, ECE, mean predicted vs observed, and the same for the 2010-2019
    frequency baseline of the 1X2 protocol."""
    from . import soccer_eval, soccer_live
    sealed = soccer_live.sealed_candidate()
    ds = soccer_eval.load(conn, through=soccer_eval.LAST_OPEN)
    preds = soccer_eval.forecast_frozen(sealed, ds, soccer_eval.VALIDATION)
    freq = soccer_eval.fit_frequencies(ds)
    rows = defaultdict(lambda: defaultdict(lambda: ([], [], [])))
    for p in preds:
        ph, pd, pa = p["p"]
        fh, fd, fa = freq[p["competition"]]
        y = p["label"]
        events = {"double_chance home_draw": (ph + pd, fh + fd, y != 2), "double_chance draw_away": (pd + pa, fd + fa, y != 0),
                  "double_chance home_away": (ph + pa, fh + fa, y != 1)}
        if y != 1:
            events["draw_no_bet home"] = (ph / (ph + pa), fh / (fh + fa), y == 0)
        for k, (pm, pf, yy) in events.items():
            for c in (p["competition"], "overall"):
                rows[k][c][0].append(pm)
                rows[k][c][1].append(pf)
                rows[k][c][2].append(yy)
    out = {}
    for k, by in rows.items():
        out[k] = {c: {**_binary(m, ys), "frequency": {kk: v for kk, v in _binary(f, ys).items() if kk != "reliability"}}
                  for c, (m, f, ys) in sorted(by.items())}
    return {"model": "sealed 1X2 Elo " + sealed["sha256"][:8], "seasons": list(soccer_eval.VALIDATION),
            "n_matches": len(preds), "note": "validation seasons were used to choose the 1X2 model (Elo over Poisson); these are "
            "not held-out numbers. The held-out 1X2 test (2023-26) covers double chance exactly through its per-outcome "
            "calibration (1X = not away, X2 = not home, 12 = not draw).", "markets": out}


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
        "goals_poisson_lowtau": dict(family="goals", role="candidate", kind="count", cfg=Config(gc.half_life_days, t["goals_lowtau"]["tau"], t["goals_lowtau"]["D"]),
                                     joint="poisson", version=ADAPTER_VERSION,
                                     description="sealed goals Poisson with tau/D below the preregistered grid's lower edge (both were on the edge)"),
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


# Candidate constants: chosen on development_tune / development_fit seasons only (docs/evidence/soccer-research-2026-11-22).
CANDIDATE_PARAMS = {"goals_lowtau": {"tau": 1.5, "D": 0.05}, "corners_tilt": {"gamma": 0.3}}


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
            "competitions": list(RESEARCH_COMPETITIONS), "walk": adapter_walk, "forecast_upcoming": forecast_upcoming,
            "validate_data": validate_data}


# --- Walk ----------------------------------------------------------------------------------------------------------------
def _check_seasons(seasons, allow_holdout: bool, log: dict) -> tuple:
    seasons = tuple(sorted(set(int(s) for s in seasons)))
    if any(s in PROSPECTIVE or s > TEST[-1] for s in seasons):
        raise TestSeasonsLocked(f"season(s) {[s for s in seasons if s > TEST[-1]]} are prospective and locked: no evaluation reads them")
    if any(s in TEST for s in seasons):
        if not allow_holdout:
            raise TestSeasonsLocked(f"seasons {[s for s in seasons if s in TEST]} are the holdout: pass allow_holdout=True (logged) to read them")
        _append(access_log_path(), {"purpose": "research adapter holdout read", "seasons": list(seasons), **log})
    return seasons


def _window(items: list, key, competitions, max_work, resume, state):
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
                 access_log=se.access_log_path() if through > se.LAST_OPEN else None, purpose="research adapter 1X2 walk")
    preds = se.forecast_frozen(sealed, ds, seasons) if m["kind"] == "elo" else se.walk(_ExpandingFrequency1x2(), ds, seasons)
    meta = ds.meta
    rows = _window(preds, lambda p: (meta[p["mid"]][4], p["competition"], p["mid"]), competitions, max_work, resume, state)
    out = []
    for p in rows:
        day = meta[p["mid"]][4]
        out += one_x_two_records(p["mid"], p["season"], f"{day}T12:00:00", p["cutoff_at"], p["p"], p["label"], p["competition"])
    return out


def _baseline_joint(kind: str, family: str, lab: list, avail: list, cutoff: str, day: str, competition: str) -> np.ndarray:
    cap = CAP[family]
    j = bisect.bisect_right(avail, cutoff)
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
    chosen = _window(scored(data, seasons), lambda o: (o.local_date, o.competition, o.mid), competitions, max_work, resume, state)
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
            recs = one_x_two_records(r["match_id"], s["season"], s["kickoff_at"], r["generated_at"], (r["p_home"], r["p_draw"], r["p_away"]),
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
