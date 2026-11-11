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
            if s and s[0] == 0 and s[1] == 0:
                counts[f"zero_zero:{comp}:{season}"] += 1
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


def fit_strengths(family: str, cfg: Config, members: dict, inputs: list, cutoff: str, targets: list) -> dict:
    """Weighted ridge Poisson fit at one cutoff from `inputs` (labelled Obs available by the cutoff, earlier dates, within
    the window). Cold start from the centres: the result depends only on the inputs, the config and the targets."""
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

    def f(theta):
        mu, hh = theta[:ng], theta[ng:2 * ng]
        att, de = theta[2 * ng:2 * ng + nt], theta[2 * ng + nt:]
        eh = mu[g_] + hh[g_] + att[hi] - de[ai]
        ea = mu[g_] + att[ai] - de[hi]
        lh, la = np.exp(eh), np.exp(ea)
        nll = np.sum(w * (lh - hy * eh)) + np.sum(w * (la - ay * ea))
        nll += tau * (np.sum((att - pc) ** 2) + np.sum((de - pc) ** 2))
        rh, ra = w * (lh - hy), w * (la - ay)
        g_mu = np.bincount(g_, rh + ra, ng)
        g_h = np.bincount(g_, rh, ng)
        g_att = np.bincount(hi, rh, nt) + np.bincount(ai, ra, nt) + 2 * tau * (att - pc)
        g_de = -np.bincount(ai, rh, nt) - np.bincount(hi, ra, nt) + 2 * tau * (de - pc)
        return nll, np.concatenate([g_mu, g_h, g_att, g_de])
    x0 = np.concatenate([np.full(ng, mu0), np.zeros(ng), pc, pc])
    res = minimize(f, x0, jac=True, method="L-BFGS-B", options={"maxiter": 500, "ftol": 1e-9, "gtol": 1e-7})
    t = res.x
    return {"mu": {g: float(t[k]) for k, g in enumerate(groups)}, "h": {g: float(t[ng + k]) for k, g in enumerate(groups)},
            "att": {x: float(t[2 * ng + k]) for k, x in enumerate(teams)},
            "def": {x: float(t[2 * ng + nt + k]) for k, x in enumerate(teams)}, "centre": cen, "mu0": mu0}


def means(params: dict, o: Obs) -> tuple[float, float]:
    mu = params["mu"].get(o.competition, params["mu0"])
    hh = params["h"].get(o.competition, 0.0)
    ah = params["att"].get(o.home, params["centre"].get(o.home, 0.0))
    dh = params["def"].get(o.home, params["centre"].get(o.home, 0.0))
    aa = params["att"].get(o.away, params["centre"].get(o.away, 0.0))
    da = params["def"].get(o.away, params["centre"].get(o.away, 0.0))
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


def walk(data: Data, cfg: Config, seasons, targets: Optional[list] = None) -> list[dict]:
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
        params = fit_strengths(data.family, cfg, data.members, inputs, cutoff, tg[day])
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
    if any(e.get("stage") == "develop_completed" and e.get("family") == family for e in _entries(experiments_log)):
        raise RunAlreadyDone(f"the {family} development run is complete; it is never repeated")
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
