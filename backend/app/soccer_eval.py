"""Soccer 1X2 evaluation, exactly as preregistered in app/soccer_protocol.json (pinned by PROTOCOL_SHA256; written before
any model was fitted; readable copy in docs/evidence/soccer-training-2026-11-08/PROTOCOL.md).

Data: openfootball history (app/soccer_history.py). Candidates: training-only outcome frequencies, a draw-aware Elo (one
rating scale across competitions, ordered-logit draw model) and an independent-Poisson attack/defence goals model.
Development seasons tune each model, validation chooses among them (simpler model kept unless clearly worse) and tests one
calibrator; reserved test seasons are refused unless a sealed candidate is supplied, and every such read is logged.

Point in time: every forecast for a match date uses the state at 06:00 UTC that day; an input is a result revision whose
availability is at or before that cutoff and that is usable for 90-minute grading (final, no extra-time note, not a UEFA
deciding match). The walk-forward engine refuses to feed a later revision and refuses to forecast a match whose own
result was fed. Reuses metrics.log_loss_multi / brier_multi and backtest.block_bootstrap / seal / LeakageError.
Reads a store only; writes reports, logs and the sealed candidate to files.
"""
from __future__ import annotations

import bisect
import hashlib
import itertools
import json
import math
import sqlite3
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
from scipy.optimize import minimize
from scipy.stats import poisson as _poisson_dist

from . import backtest, metrics, soccer_clubs
from .backtest import LeakageError
from .db import utc_now

PROTOCOL_PATH = Path(__file__).with_name("soccer_protocol.json")
PROTOCOL_SHA256 = "f37946397ce971a28dd2d1603fad570273e9b7eb50a67583fbe9830ac32f70e9"


class ProtocolChanged(RuntimeError):
    """The protocol file differs from the sealed digest."""


class TestSeasonsLocked(PermissionError):
    """A reserved season was requested without a valid sealed candidate (or without an access log)."""


class TestRunAlreadyDone(PermissionError):
    """The one-shot test run already started for this seal, or its output folder exists."""


class FrozenMismatch(RuntimeError):
    """Code, protocol or open data differ from what the sealed candidate was built from."""


def load_protocol() -> dict:
    raw = PROTOCOL_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PROTOCOL_SHA256:
        raise ProtocolChanged(f"{PROTOCOL_PATH.name} does not match the sealed protocol digest")
    return json.loads(raw)


_P = load_protocol()
PERIODS = _P["periods"]
WARMUP, DEV_TUNE, DEV_CAL = tuple(PERIODS["warmup"]), tuple(PERIODS["development_tune"]), tuple(PERIODS["development_calibration"])
VALIDATION, TEST = tuple(PERIODS["validation"]), tuple(PERIODS["reserved_test"])
LAST_OPEN = PERIODS["last_open_season"]
FIRST_SEASON = PERIODS["league_first_season_not_scored"]
LEAGUES = tuple(soccer_clubs.LEAGUE_COUNTRY)
SHARED = {"uefa.europa": "uefa.champions"}          # UEL has no development data: it uses UCL's competition parameters
RUN_START, RUN_DONE = "test run: start", "test run: complete"
CLIP = 1e-15
ELO_GRID, POISSON_GRID = _P["candidates"]["elo"]["grid"], _P["candidates"]["poisson"]["grid"]
CAL = _P["calibration"]
RELIABILITY_BINS = 10


def group(competition: str) -> str:
    return SHARED.get(competition, competition)


# --- Data ------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Match:
    """A match to forecast and score: its latest revision is gradable; label 0 home win, 1 draw, 2 away win."""
    mid: str
    competition: str
    season: int
    local_date: str
    home: str
    away: str
    stage: str
    label: Optional[int]


@dataclass(frozen=True)
class Revision:
    """One revision of a match result, usable as an input from `available_at` if `usable`."""
    mid: str
    revision: int
    available_at: str
    usable: bool
    hg: Optional[int]
    ag: Optional[int]


@dataclass
class Dataset:
    matches: list
    revisions: list
    members: dict                         # (league, season) -> frozenset of club ids, from the schedule
    through: int
    meta: Optional[dict] = None           # mid -> (competition, season, home, away, local_date)

    def __post_init__(self):
        self.matches = sorted(self.matches, key=lambda m: (m.local_date, m.mid))
        self.revisions = sorted(self.revisions, key=lambda r: (r.available_at, r.mid, r.revision))
        if self.meta is None:
            self.meta = {m.mid: (m.competition, m.season, m.home, m.away, m.local_date) for m in self.matches}
        self.club_league = {(c, s): lg for (lg, s), cs in self.members.items() for c in cs}


def cutoff_for(local_date: str) -> str:
    return f"{local_date}T06:00:00Z"


def label_of(hg: int, ag: int) -> int:
    return 0 if hg > ag else (1 if hg == ag else 2)


def seal_candidate(candidate: dict) -> dict:
    return backtest.seal(candidate)


def _valid_seal(c: Optional[dict]) -> bool:
    return bool(c) and backtest.seal(c).get("sha256") == c.get("sha256")


def _log_access(access_log: Path, sealed: dict, through: int, purpose: str) -> None:
    access_log.parent.mkdir(parents=True, exist_ok=True)
    with access_log.open("a") as f:
        f.write(json.dumps({"at": utc_now(), "through": through, "candidate_sha256": sealed["sha256"],
                            "purpose": purpose}) + "\n")


def eval_log_dir() -> Path:
    """One folder for the soccer evaluation logs, shared by every data folder (a development copy and the full store
    write to the same logs, so one development run and one test run are counted across all of them). Override with
    SOCCER_EVAL_LOG_DIR; never inside the live data/ folder."""
    import os
    return Path(os.environ.get("SOCCER_EVAL_LOG_DIR", Path.home() / "nba-predictor-research-data" / "soccer-eval-logs"))


def access_log_path() -> Path:
    return eval_log_dir() / "soccer_test_access.jsonl"


def experiments_log_path() -> Path:
    return eval_log_dir() / "soccer_experiments.jsonl"


def load(conn: sqlite3.Connection, *, through: int, sealed: Optional[dict] = None, access_log: Optional[Path] = None,
         purpose: str = "") -> Dataset:
    """Matches, revisions and league membership of seasons <= `through` (filtered in SQL: later seasons are never read).
    Seasons after LAST_OPEN need a valid sealed candidate and an access log, written before anything is read."""
    if through > TEST[-1]:
        raise TestSeasonsLocked(f"seasons after {TEST[-1]} are prospective and locked: no evaluation reads them")
    if through > LAST_OPEN:
        if not _valid_seal(sealed):
            raise TestSeasonsLocked(f"seasons {LAST_OPEN + 1}-{through} are reserved: a valid sealed candidate is required")
        if access_log is None:
            raise TestSeasonsLocked("a reserved-season read must be logged: pass access_log")
        _log_access(access_log, sealed, through, purpose)
    matches, meta, members = [], {}, defaultdict(set)
    for r in conn.execute("SELECT hist_match_id, competition_id, season, local_date, home_club_id, away_club_id, stage, "
                          "grading, ft_home, ft_away, in_latest_file FROM soccer_hist_matches WHERE season <= ? "
                          "ORDER BY local_date, hist_match_id", (through,)):
        meta[r[0]] = (r[1], r[2], r[4], r[5], r[3])
        if r[1] in LEAGUES and r[10]:
            members[(r[1], r[2])].update({r[4], r[5]})
        if r[7] == "gradable" and r[10]:
            matches.append(Match(r[0], r[1], r[2], r[3], r[4], r[5], r[6], label_of(r[8], r[9])))
    revisions = []
    for r in conn.execute("SELECT v.hist_match_id, v.revision, v.result_available_at, v.status, v.aet_home, v.pen_home, "
                          "v.deciding, v.in_file, v.ft_home, v.ft_away FROM soccer_hist_revisions v JOIN soccer_hist_matches m "
                          "ON m.hist_match_id = v.hist_match_id WHERE m.season <= ?", (through,)):
        if r[2] is None:
            continue                                   # not a result (scheduled / missing / cancelled)
        usable = r[3] == "final" and r[4] is None and r[5] is None and r[6] == 0 and r[7] == 1
        revisions.append(Revision(r[0], r[1], r[2], usable, r[8], r[9]))
    return Dataset(matches, revisions, {k: frozenset(v) for k, v in members.items()}, through, meta)


def scored(ds: Dataset, seasons) -> list[Match]:
    """Matches scored in `seasons`: gradable, not a warm-up season, not a league's first covered season."""
    keep = set(seasons) - set(WARMUP)
    return [m for m in ds.matches if m.season in keep and m.label is not None
            and not (m.competition in FIRST_SEASON and m.season == FIRST_SEASON[m.competition])]


# --- Walk-forward engine -----------------------------------------------------------------------------------------------
def feed(model, revisions: list, *, cutoff_at: str, start: int = 0) -> None:
    """Feeds revisions[start:] to the model; every one must be available by the cutoff (else LeakageError). Unusable
    revisions are passed to `withdraw` (as-of models drop the match) when the model has it, never to `observe`."""
    for r in revisions[start:]:
        if r.available_at > cutoff_at:
            raise LeakageError(f"revision {r.mid}#{r.revision} available {r.available_at} is after the cutoff {cutoff_at}")
        if r.usable:
            model.observe(r)
        elif hasattr(model, "withdraw"):
            model.withdraw(r)


def walk(model, ds: Dataset, seasons) -> list[dict]:
    """Forecasts every scored match of `seasons` in date order; before each date the model has seen exactly the revisions
    available by that date's cutoff. Returns one record per match: mid, competition, season, cutoff_at, p, label."""
    targets = defaultdict(list)
    for m in scored(ds, seasons):
        targets[m.local_date].append(m)
    if hasattr(model, "match_meta"):
        model.match_meta = ds.meta
    revs, avail = ds.revisions, [r.available_at for r in ds.revisions]
    i, fed_mids, out = 0, set(), []
    for day in sorted(targets):
        cutoff = cutoff_for(day)
        j = bisect.bisect_right(avail, cutoff)
        feed(model, revs[i:j], cutoff_at=cutoff)
        fed_mids.update(r.mid for r in revs[i:j])
        i = j
        ms = targets[day]
        leaked = [m.mid for m in ms if m.mid in fed_mids]
        if leaked:
            raise LeakageError(f"{len(leaked)} match(es) forecast after their own result was fed: {leaked[:3]}")
        probs = model.predict([replace(m, label=None) for m in ms], cutoff)       # never the label it forecasts
        for m in ms:
            p = probs[m.mid]
            if len(p) != 3 or abs(sum(p) - 1) > 1e-9 or min(p) <= 0:
                raise ValueError(f"{getattr(model, 'name', model)} gave an invalid forecast for {m.mid}: {p}")
            out.append({"mid": m.mid, "competition": m.competition, "season": m.season, "cutoff_at": cutoff,
                        "p": tuple(float(x) for x in p), "label": m.label})
    return out


# --- Frequency baseline ------------------------------------------------------------------------------------------------
FREQ_SEASONS = tuple(range(2010, 2020))


def fit_frequencies(ds: Dataset) -> dict:
    counts = defaultdict(lambda: [0, 0, 0])
    for m in ds.matches:
        if m.season in FREQ_SEASONS and m.label is not None:
            counts[m.competition][m.label] += 1
    out = {c: tuple(x / sum(v) for x in v) for c, v in counts.items()}
    if "uefa.champions" in out:
        out["uefa.europa"] = out["uefa.champions"]
    return out


class FrequencyModel:
    name = "frequency"

    def __init__(self, freqs: dict):
        self.freqs = freqs

    def observe(self, r):
        pass

    def predict(self, matches, cutoff_at):
        return {m.mid: self.freqs[m.competition] for m in matches}


# --- Draw-aware Elo --------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class EloConfig:
    K: float
    H: float
    C: float
    P: float
    F: float
    mov: bool


def elo_grid() -> list[EloConfig]:
    keys = ("K", "H", "C", "P", "F", "mov")
    return [EloConfig(**dict(zip(keys, v))) for v in itertools.product(*(ELO_GRID[k] for k in keys))]


def elo_tiebreak(cfg: EloConfig) -> tuple:
    return (cfg.K, cfg.H, -cfg.C, cfg.P, cfg.F, cfg.mov)


def goal_multiplier(gd: int) -> float:
    gd = abs(gd)
    return 1.0 if gd <= 1 else (1.5 if gd == 2 else (11 + gd) / 8)


def _sigmoid(z):
    return 1 / (1 + np.exp(-z))


def draw_probs(dm: dict, competition: str, x: float) -> tuple[float, float, float]:
    a, d = dm["cuts"][group(competition)]
    b = dm["b"]
    p_away = 1 / (1 + math.exp(-(a - b * x)))
    p_not_home = 1 / (1 + math.exp(-(a + d - b * x)))
    return (1 - p_not_home, p_not_home - p_away, p_away)


def fit_draw_model(rows: list[dict]) -> dict:
    """Ordered logit on x, one slope, cut points per competition group; development_tune rows only."""
    bad = sorted({r["season"] for r in rows} - set(DEV_TUNE))
    if bad:
        raise LeakageError(f"the draw model is fitted on development_tune seasons only; got {bad}")
    groups = sorted({group(r["competition"]) for r in rows})
    gi = np.array([groups.index(group(r["competition"])) for r in rows])
    x = np.array([r["x"] for r in rows], float)
    y = np.array([r["label"] for r in rows])
    n = len(groups)

    def nll(theta):
        b, a, logd = theta[0], theta[1:1 + n][gi], theta[1 + n:][gi]
        d = np.exp(logd)
        pa = _sigmoid(a - b * x)
        pn = _sigmoid(a + d - b * x)
        p = np.where(y == 2, pa, np.where(y == 1, pn - pa, 1 - pn))
        return -np.sum(np.log(np.clip(p, CLIP, 1)))
    theta0 = np.concatenate([[1.0], np.full(n, -0.8), np.full(n, np.log(0.9))])
    res = minimize(nll, theta0, method="L-BFGS-B", options={"maxiter": 500})
    t = res.x
    return {"b": float(t[0]), "cuts": {g: (float(t[1 + k]), float(np.exp(t[1 + n + k]))) for k, g in enumerate(groups)},
            "n": len(rows), "converged": bool(res.success)}


class EloModel:
    name = "elo"

    def __init__(self, cfg: EloConfig, members: dict, draw_model: Optional[dict]):
        self.cfg, self.members, self.dm = cfg, members, draw_model
        self.club_league = {(c, s): lg for (lg, s), cs in members.items() for c in cs}
        self.ratings: dict[str, float] = {}
        self.touched: set = set()
        self.foreign_season: dict[str, int] = {}
        self.applied: set = set()
        self.match_meta: dict = {}
        self.last_x: dict[str, float] = {}

    def touch(self, league: str, season: int) -> None:
        if (league, season) in self.touched:
            return
        self.touched.add((league, season))
        prev, cur = self.members.get((league, season - 1)), self.members.get((league, season), frozenset())
        if not prev:
            for c in cur:
                self.ratings[c] = 1500.0
            return
        m = sum(self.ratings.get(c, 1500.0) for c in prev) / len(prev)
        for c in cur:
            self.ratings[c] = m + self.cfg.C * (self.ratings.get(c, 1500.0) - m) if c in prev else m - self.cfg.P

    def touch_club(self, club: str, competition: str, season: int) -> None:
        if competition in LEAGUES:
            self.touch(competition, season)
            return
        league = self.club_league.get((club, season))
        if league is not None:
            self.touch(league, season)
            return
        if club not in self.ratings:
            self.ratings[club] = 1500.0 - self.cfg.F
            self.foreign_season[club] = season
            return
        if self.foreign_season.get(club) != season:
            pool = [r for c, r in self.ratings.items() if (c, season) not in self.club_league]
            mf = sum(pool) / len(pool)
            self.ratings[club] = mf + self.cfg.C * (self.ratings[club] - mf)
            self.foreign_season[club] = season

    def _x(self, home: str, away: str) -> float:
        return (self.ratings[home] + self.cfg.H - self.ratings[away]) / 400

    def observe(self, r: Revision) -> None:
        if r.mid in self.applied:                    # a later correction never rewrites an earlier update
            return
        self.applied.add(r.mid)
        comp, season, home, away = self.match_meta[r.mid][:4]
        self.touch_club(home, comp, season)
        self.touch_club(away, comp, season)
        x = self._x(home, away)
        expected = 1 / (1 + 10 ** (-x))
        actual = 1.0 if r.hg > r.ag else (0.5 if r.hg == r.ag else 0.0)
        mult = goal_multiplier(r.hg - r.ag) if self.cfg.mov else 1.0
        delta = self.cfg.K * mult * (actual - expected)
        self.ratings[home] += delta
        self.ratings[away] -= delta

    def predict(self, matches, cutoff_at):
        out = {}
        for m in matches:
            self.touch_club(m.home, m.competition, m.season)
            self.touch_club(m.away, m.competition, m.season)
            x = self._x(m.home, m.away)
            self.last_x[m.mid] = x
            out[m.mid] = draw_probs(self.dm, m.competition, x) if self.dm else (1 / 3, 1 / 3, 1 / 3)
        return out


# --- Poisson goals model -----------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class PoissonConfig:
    half_life_days: float
    tau: float
    D: float


def poisson_grid() -> list[PoissonConfig]:
    keys = ("half_life_days", "tau", "D")
    return [PoissonConfig(**dict(zip(keys, v))) for v in itertools.product(*(POISSON_GRID[k] for k in keys))]


def poisson_tiebreak(cfg: PoissonConfig) -> tuple:
    return (-cfg.half_life_days, -cfg.tau, cfg.D)


_GOALS = np.arange(11)


def poisson_probs(lh: float, la: float) -> tuple[float, float, float]:
    ph, pa = _poisson_dist.pmf(_GOALS, lh), _poisson_dist.pmf(_GOALS, la)
    grid = np.outer(ph, pa)
    home, draw, away = np.tril(grid, -1).sum(), np.trace(grid), np.triu(grid, 1).sum()
    s = home + draw + away
    return (float(home / s), float(draw / s), float(away / s))


WINDOW_DAYS = 730


def _season_of(day: date) -> int:
    return day.year if day.month >= 7 else day.year - 1


class PoissonModel:
    name = "poisson"

    def __init__(self, cfg: PoissonConfig, members: dict):
        self.cfg, self.members = cfg, members
        self.snap: dict[str, tuple] = {}               # mid -> (date ordinal, group, home, away, hg, ag): as-of results
        self.match_meta: dict = {}
        self.fit_cutoff: Optional[str] = None
        self.params: dict = {"att": {}, "def": {}, "mu": {}, "h": {}}
        self.fits = 0
        self.centres: dict[str, tuple[float, float]] = {}

    def observe(self, r: Revision) -> None:
        comp, season, home, away, day = self.match_meta[r.mid]
        self.snap[r.mid] = (date.fromisoformat(day).toordinal(), group(comp), home, away, r.hg, r.ag, season)

    def withdraw(self, r: Revision) -> None:
        self.snap.pop(r.mid, None)

    def prior_centres(self, club_season: dict[str, int], prev: dict) -> dict[str, tuple[float, float]]:
        """Ridge centre per club: 0, except a club in a league-season it was not in the previous covered season of that
        league (promoted): mean (att, def) of the league-season's other members present in the previous cutoff's fit
        (promoted ones included), minus D. A newly covered league (no previous season) or no member in the previous
        fit: 0. `club_season`: the season of the club's forecast match, else of its latest input row."""
        out = {}
        for c, season in club_season.items():
            centre = (0.0, 0.0)
            for lg in LEAGUES:
                cur, before = self.members.get((lg, season)), self.members.get((lg, season - 1))
                if cur and before and c in cur and c not in before:
                    others = [o for o in cur if o != c and o in prev["att"]]
                    if others:
                        centre = (float(np.mean([prev["att"][o] for o in others])) - self.cfg.D,
                                  float(np.mean([prev["def"][o] for o in others])) - self.cfg.D)
                    break
            out[c] = centre
        return out

    def fit(self, cutoff_at: str, matches=()) -> None:
        day = date.fromisoformat(cutoff_at[:10])
        lo = day.toordinal() - WINDOW_DAYS
        rows = [v for v in self.snap.values() if v[0] >= lo]
        self.fit_cutoff = cutoff_at
        club_season: dict[str, int] = {}
        for v in sorted(rows):                                  # latest input row's season ...
            club_season[v[2]] = club_season[v[3]] = v[6]
        for m in matches:                                       # ... unless the club is forecast at this cutoff
            club_season[m.home] = club_season[m.away] = m.season
        prev = self.params
        self.centres = self.prior_centres(club_season, prev)
        if not rows:
            return
        clubs = sorted({v[2] for v in rows} | {v[3] for v in rows})
        groups = sorted({v[1] for v in rows})
        ci, gi = {c: k for k, c in enumerate(clubs)}, {g: k for k, g in enumerate(groups)}
        h_idx = np.array([ci[v[2]] for v in rows])
        a_idx = np.array([ci[v[3]] for v in rows])
        g_idx = np.array([gi[v[1]] for v in rows])
        hg = np.array([v[4] for v in rows], float)
        ag = np.array([v[5] for v in rows], float)
        w = np.exp(-math.log(2) * (day.toordinal() - np.array([v[0] for v in rows], float)) / self.cfg.half_life_days)
        n_c, n_g = len(clubs), len(groups)
        pa = np.array([self.centres[c][0] for c in clubs])
        pd = np.array([self.centres[c][1] for c in clubs])
        tau = self.cfg.tau
        old = self.params
        x0 = np.concatenate([[old["mu"].get(g, 0.25) for g in groups], [old["h"].get(g, 0.25) for g in groups],
                             [old["att"].get(c, pa[k]) for k, c in enumerate(clubs)],
                             [old["def"].get(c, pd[k]) for k, c in enumerate(clubs)]])

        def f(theta):
            mu, h = theta[:n_g], theta[n_g:2 * n_g]
            att, de = theta[2 * n_g:2 * n_g + n_c], theta[2 * n_g + n_c:]
            eh = mu[g_idx] + h[g_idx] + att[h_idx] - de[a_idx]
            ea = mu[g_idx] + att[a_idx] - de[h_idx]
            lh, la = np.exp(eh), np.exp(ea)
            nll = np.sum(w * (lh - hg * eh)) + np.sum(w * (la - ag * ea))
            nll += tau * (np.sum((att - pa) ** 2) + np.sum((de - pd) ** 2))
            rh, ra = w * (lh - hg), w * (la - ag)        # d nll / d eh, d nll / d ea
            g_mu = np.bincount(g_idx, rh + ra, n_g)
            g_h = np.bincount(g_idx, rh, n_g)
            g_att = np.bincount(h_idx, rh, n_c) + np.bincount(a_idx, ra, n_c) + 2 * tau * (att - pa)
            g_de = -np.bincount(a_idx, rh, n_c) - np.bincount(h_idx, ra, n_c) + 2 * tau * (de - pd)
            return nll, np.concatenate([g_mu, g_h, g_att, g_de])
        res = minimize(f, x0, jac=True, method="L-BFGS-B", options={"maxiter": 200, "ftol": 1e-8, "gtol": 1e-6})
        t = res.x
        self.params = {"mu": {g: float(t[k]) for k, g in enumerate(groups)},
                       "h": {g: float(t[n_g + k]) for k, g in enumerate(groups)},
                       "att": {c: float(t[2 * n_g + k]) for k, c in enumerate(clubs)},
                       "def": {c: float(t[2 * n_g + n_c + k]) for k, c in enumerate(clubs)}}
        self.fits += 1

    def _strength(self, club: str, season: int) -> tuple[float, float]:
        if club in self.params["att"]:
            return self.params["att"][club], self.params["def"][club]
        return self.centres.get(club, (0.0, 0.0))             # no input in the window: its prior centre

    def predict(self, matches, cutoff_at):
        if cutoff_at != self.fit_cutoff:
            self.fit(cutoff_at, matches)
        out = {}
        for m in matches:
            g = group(m.competition)
            mu, h = self.params["mu"].get(g, 0.25), self.params["h"].get(g, 0.25)
            ah, dh = self._strength(m.home, m.season)
            aa, da = self._strength(m.away, m.season)
            out[m.mid] = poisson_probs(math.exp(mu + h + ah - da), math.exp(mu + aa - dh))
        return out


# --- Calibration -------------------------------------------------------------------------------------------------------
def fit_calibration(preds: list[dict]) -> dict:
    bad = sorted({p["season"] for p in preds} - set(DEV_CAL))
    if bad:
        raise LeakageError(f"the calibrator is fitted on development_calibration seasons only; got {bad}")
    if len(preds) < CAL["minimum_matches"]:
        raise ValueError(f"calibration needs at least {CAL['minimum_matches']} matches, got {len(preds)}")
    lp = np.log(np.clip(np.array([p["p"] for p in preds]), CLIP, 1))
    y = np.array([p["label"] for p in preds])

    def nll(t):
        s, cd, ca = t
        z = s * lp + np.array([0.0, cd, ca])
        z -= z.max(axis=1, keepdims=True)
        logq = z - np.log(np.exp(z).sum(axis=1, keepdims=True))
        return -logq[np.arange(len(y)), y].sum() + 1e-3 * ((s - 1) ** 2 + cd ** 2 + ca ** 2)
    res = minimize(nll, np.array([1.0, 0.0, 0.0]), method="L-BFGS-B")
    return {"s": float(res.x[0]), "c_draw": float(res.x[1]), "c_away": float(res.x[2]), "n": len(preds),
            "converged": bool(res.success)}


def apply_calibration(cal: dict, p: tuple) -> tuple[float, float, float]:
    z = [cal["s"] * math.log(max(CLIP, q)) + c for q, c in zip(p, (0.0, cal["c_draw"], cal["c_away"]))]
    mz = max(z)
    e = [math.exp(v - mz) for v in z]
    q = [min(1 - 1e-6, max(1e-6, v / sum(e))) for v in e]
    return tuple(v / sum(q) for v in q)


# --- Metrics, choice and acceptance ----------------------------------------------------------------------------------
def match_loss(p: dict) -> float:
    return -math.log(min(1 - CLIP, max(CLIP, p["p"][p["label"]])))


def ece(preds: list[dict], outcome: int, bins: int = RELIABILITY_BINS) -> float:
    groups = defaultdict(list)
    for p in preds:
        q = p["p"][outcome]
        groups[min(bins - 1, int(q * bins))].append((q, p["label"] == outcome))
    n = len(preds)
    return sum(len(g) / n * abs(sum(q for q, _ in g) / len(g) - sum(y for _, y in g) / len(g)) for g in groups.values())


def reliability(preds: list[dict], outcome: int, bins: int = RELIABILITY_BINS) -> list[dict]:
    out = []
    for b in range(bins):
        g = [(p["p"][outcome], p["label"] == outcome) for p in preds if min(bins - 1, int(p["p"][outcome] * bins)) == b]
        out.append({"bin": [b / bins, (b + 1) / bins], "n": len(g),
                    "mean_predicted": sum(q for q, _ in g) / len(g) if g else None,
                    "observed": sum(y for _, y in g) / len(g) if g else None})
    return out


def summary(preds: list[dict], bootstrap: bool = True) -> dict:
    if not preds:
        return {"n": 0}
    vecs, labels = [p["p"] for p in preds], [p["label"] for p in preds]
    out = {"n": len(preds), "log_loss": metrics.log_loss_multi(vecs, labels), "brier": metrics.brier_multi(vecs, labels),
           "accuracy_most_likely": sum(max(range(3), key=lambda k: v[k]) == y for v, y in zip(vecs, labels)) / len(preds),
           "mean_predicted": [sum(v[k] for v in vecs) / len(vecs) for k in range(3)],
           "observed": [sum(y == k for y in labels) / len(labels) for k in range(3)],
           "ece": [ece(preds, k) for k in range(3)]}
    if bootstrap:
        b = backtest.block_bootstrap([match_loss(p) for p in preds], [p["cutoff_at"] for p in preds])
        out["log_loss_ci95"], out["weeks"] = b["ci95"], b["weeks"]
    return out


def breakdown(preds: list[dict]) -> dict:
    by_comp, by_season = defaultdict(list), defaultdict(list)
    for p in preds:
        by_comp[p["competition"]].append(p)
        by_season[p["season"]].append(p)
    return {"overall": summary(preds), "by_competition": {c: summary(v) for c, v in sorted(by_comp.items())},
            "by_season": {s: summary(v, bootstrap=False) for s, v in sorted(by_season.items())}}


def paired(a: list[dict], b: list[dict]) -> dict:
    """Per-match log-loss difference a - b on the same matches (negative favours a), week-block bootstrap."""
    pb = {p["mid"]: p for p in b}
    if set(pb) != {p["mid"] for p in a}:
        raise ValueError("paired comparison needs forecasts of the same matches")
    d = [match_loss(p) - match_loss(pb[p["mid"]]) for p in a]
    return backtest.block_bootstrap(d, [p["cutoff_at"] for p in a])


def per_season_diff(a: list[dict], b: list[dict]) -> dict:
    """Mean per-match log-loss difference a - b in each validation season (negative favours a)."""
    pb = {p["mid"]: p for p in b}
    out = defaultdict(list)
    for p in a:
        out[p["season"]].append(match_loss(p) - match_loss(pb[p["mid"]]))
    return {s: sum(v) / len(v) for s, v in sorted(out.items())}


def _robust(a: list[dict], b: list[dict]) -> tuple[dict, dict, bool]:
    """The preregistered 'clearly better' test (amendment 1): pooled mean below 0 with the 95 % interval below 0, AND a
    lower mean in at least 2 of the 3 validation seasons (one atypical season such as 2020-21 cannot decide alone)."""
    cmp, seasons = paired(a, b), per_season_diff(a, b)
    wins = sum(1 for s in VALIDATION if seasons.get(s, 0.0) < 0)
    return cmp, seasons, bool(cmp["mean"] < 0 and cmp["ci95"][1] < 0 and wins >= 2)


def choose_model(preds: dict) -> dict:
    current, steps = "frequency", []
    for cand in ("elo", "poisson"):
        cmp, seasons, ok = _robust(preds[cand], preds[current])
        steps.append({"candidate": cand, "against": current, "mean_diff": cmp["mean"], "ci95": cmp["ci95"],
                      "per_season_diff": seasons, "weeks": cmp["weeks"], "matches": cmp["games"], "accepted": ok})
        if ok:
            current = cand
    return {"selected": current, "steps": steps}


def accept_calibration(uncalibrated: list[dict], calibrated: list[dict]) -> dict:
    cmp, seasons, better = _robust(calibrated, uncalibrated)
    e_u, e_c = [ece(uncalibrated, k) for k in range(3)], [ece(calibrated, k) for k in range(3)]
    no_worse = all(c <= u + 0.005 for u, c in zip(e_u, e_c))
    return {"keep": bool(better and no_worse), "mean_diff": cmp["mean"], "ci95": cmp["ci95"], "per_season_diff": seasons,
            "ece_uncalibrated": e_u,
            "ece_calibrated": e_c, "log_loss_better": better, "reliability_not_worse": no_worse}


# --- Development and validation (no reserved season is read) ------------------------------------------------------
def _log_experiment(path: Optional[Path], entry: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps({"at": utc_now(), **entry}, default=str) + "\n")


def _elo_dev(ds: Dataset, cfg: EloConfig) -> tuple[float, dict, list]:
    m = EloModel(cfg, ds.members, None)
    preds = walk(m, ds, DEV_TUNE)
    rows = [{**p, "x": m.last_x[p["mid"]]} for p in preds]
    dm = fit_draw_model(rows)
    final = [{**p, "p": draw_probs(dm, p["competition"], r["x"])} for p, r in zip(preds, rows)]
    return metrics.log_loss_multi([p["p"] for p in final], [p["label"] for p in final]), dm, final


def _poisson(ds: Dataset, cfg: PoissonConfig, seasons) -> list:
    return walk(PoissonModel(cfg, ds.members), ds, seasons)


def open_data_digest(conn: sqlite3.Connection) -> str:
    """sha256 over the open (season <= LAST_OPEN) match and revision rows the evaluation reads, the content hash of every
    open results file, and the cached commit/tree listings (which pin the reserved files by blob id). Equal on a
    development copy that holds only open seasons and on the full store."""
    h = hashlib.sha256()
    for r in conn.execute("SELECT hist_match_id, competition_id, season, local_date, home_club_id, away_club_id, stage, "
                          "grading, ft_home, ft_away, in_latest_file FROM soccer_hist_matches WHERE season <= ? "
                          "ORDER BY hist_match_id", (LAST_OPEN,)):
        h.update(json.dumps(list(r)).encode())
    for r in conn.execute("SELECT v.hist_match_id, v.revision, v.result_available_at, v.status, v.aet_home, v.pen_home, "
                          "v.deciding, v.in_file, v.ft_home, v.ft_away FROM soccer_hist_revisions v JOIN soccer_hist_matches m "
                          "ON m.hist_match_id = v.hist_match_id WHERE m.season <= ? ORDER BY v.revision_id", (LAST_OPEN,)):
        h.update(json.dumps(list(r)).encode())
    for r in conn.execute("SELECT f.kind, f.repo, f.commit_sha, f.path, r.content_hash FROM soccer_source_files f JOIN "
                          "raw_responses r ON r.raw_id = f.raw_id WHERE f.kind <> 'results' OR f.season <= ? "
                          "ORDER BY f.kind, f.repo, f.path, f.fetch_id", (LAST_OPEN,)):
        h.update(json.dumps(list(r)).encode())          # tree listings pin reserved files by blob id, never by content
    return h.hexdigest()


CODE_FILES = ("soccer_eval.py", "soccer_history.py", "soccer_clubs.py", "soccer_protocol.json", "backtest.py", "metrics.py")


def code_digest() -> dict:
    here = Path(__file__).parent
    return {f: hashlib.sha256((here / f).read_bytes()).hexdigest() for f in CODE_FILES}


def environment() -> dict:
    import platform
    import scipy
    return {"python": platform.python_version(), "numpy": np.__version__, "scipy": scipy.__version__}


def check_dataset_pins(conn: sqlite3.Connection) -> None:
    """The store must hold exactly the preregistered data version: results files only from the pinned commits, rows
    imported by the pinned importer and resolved with the pinned club map (review S3 addendum)."""
    pins, problems = _P["dataset"], []
    for repo, sha in conn.execute("SELECT DISTINCT repo, commit_sha FROM soccer_source_files WHERE kind = 'results'"):
        if pins["source_pins"].get(repo) != sha:
            problems.append(f"{repo} file at {sha[:12]} is not the pinned commit")
    for col, want in (("importer_version", pins["importer_version"]), ("map_version", pins["club_map_version"])):
        other = conn.execute(f"SELECT DISTINCT {col} FROM soccer_hist_matches WHERE {col} <> ?", (want,)).fetchall()
        if other:
            problems.append(f"{col} {[r[0] for r in other]} differs from the pinned {want}")
    if problems:
        raise FrozenMismatch("; ".join(problems))


def select_config(rows: list[tuple]) -> tuple:
    """(log loss, tie-break key, config, ...) rows -> the preregistered choice: lowest log loss; rows within 1e-9 of it
    are ties, decided by the smallest tie-break key."""
    best = min(r[0] for r in rows)
    return min((r for r in rows if r[0] <= best + 1e-9), key=lambda r: r[1])


def _exclusions(conn: sqlite3.Connection) -> dict:
    """Counts of the protocol's excluded categories over open seasons (no outcome is read)."""
    out = {g: n for g, n in conn.execute("SELECT grading, COUNT(*) FROM soccer_hist_matches WHERE season <= ? AND "
                                         "in_latest_file = 1 GROUP BY grading", (LAST_OPEN,))}
    out["rejected_at_import"] = {f"{c} {s} {r}": n for c, s, r, n in conn.execute(
        "SELECT competition_id, season, reason, COUNT(*) FROM soccer_hist_rejections WHERE season <= ? GROUP BY 1, 2, 3",
        (LAST_OPEN,))}
    out["gradable_in_warmup"] = conn.execute("SELECT COUNT(*) FROM soccer_hist_matches WHERE grading = 'gradable' AND "
                                             f"season IN ({', '.join('?' * len(WARMUP))})", WARMUP).fetchone()[0]
    out["gradable_in_league_first_seasons"] = sum(conn.execute(
        "SELECT COUNT(*) FROM soccer_hist_matches WHERE grading = 'gradable' AND competition_id = ? AND season = ?",
        (c, s)).fetchone()[0] for c, s in FIRST_SEASON.items() if s not in WARMUP)
    return out


def develop(conn: sqlite3.Connection, *, out_dir: Path, experiments_log: Path, progress=print) -> dict:
    """Development (tune), development (calibration) and validation, exactly as preregistered; returns the report and
    writes it, the per-match forecasts and the sealed candidate to `out_dir`. Never reads a reserved season. The
    experiments log is mandatory: its start entry (code, protocol, data, environment) is written before anything is
    loaded, so every development run leaves a trace; run_test accepts only a candidate that is the log's only seal."""
    started = utc_now()
    check_dataset_pins(conn)
    data_digest = open_data_digest(conn)
    run_id = hashlib.sha256(f"{started}{data_digest}".encode()).hexdigest()[:12]
    log = lambda e: _log_experiment(experiments_log, {"run": run_id, **e})          # noqa: E731
    log({"stage": "start", "code": code_digest(), "protocol_sha256": PROTOCOL_SHA256, "open_data_digest": data_digest,
         "environment": environment()})
    ds = load(conn, through=LAST_OPEN)
    freqs = fit_frequencies(ds)
    # 1. Elo grid on development_tune.
    t0, elo_rows = time.time(), []
    for k, cfg in enumerate(elo_grid()):
        ll, dm, _ = _elo_dev(ds, cfg)
        elo_rows.append((ll, elo_tiebreak(cfg), cfg, dm))
        log({"stage": "elo dev_tune", "config": asdict(cfg), "log_loss": ll, "draw_model": dm})
        if progress and k % 50 == 0:
            progress(f"elo {k + 1}/{len(elo_grid())} {time.time() - t0:.0f}s")
    elo_ll, _, elo_cfg, elo_dm = select_config(elo_rows)
    # 2. Poisson grid on development_tune.
    pois_rows = []
    for cfg in poisson_grid():
        t1 = time.time()
        pr = _poisson(ds, cfg, DEV_TUNE)
        ll = metrics.log_loss_multi([p["p"] for p in pr], [p["label"] for p in pr])
        pois_rows.append((ll, poisson_tiebreak(cfg), cfg))
        log({"stage": "poisson dev_tune", "config": asdict(cfg), "log_loss": ll})
        if progress:
            progress(f"poisson {asdict(cfg)} ll={ll:.5f} {time.time() - t1:.0f}s")
    pois_ll, _, pois_cfg = select_config(pois_rows)
    # 3. Selected configurations: forecasts for development_tune, development_calibration and validation.
    later = (*DEV_CAL, *VALIDATION)
    _, _, elo_tune = _elo_dev(ds, elo_cfg)
    elo_later = walk(EloModel(elo_cfg, ds.members, elo_dm), ds, later)
    pois_tune = _poisson(ds, pois_cfg, DEV_TUNE)
    pois_later = _poisson(ds, pois_cfg, later)
    freq_model = FrequencyModel(freqs)
    freq_tune, freq_later = walk(freq_model, ds, DEV_TUNE), walk(freq_model, ds, later)
    split = lambda ps, seasons: [p for p in ps if p["season"] in seasons]                 # noqa: E731
    val = {"frequency": split(freq_later, VALIDATION), "elo": split(elo_later, VALIDATION),
           "poisson": split(pois_later, VALIDATION)}
    cal_set = {"frequency": split(freq_later, DEV_CAL), "elo": split(elo_later, DEV_CAL), "poisson": split(pois_later, DEV_CAL)}
    # 4. Model choice on validation (preregistered sequential rule).
    choice = choose_model(val)
    selected = choice["selected"]
    log({"stage": "validation choice", **choice})
    # 5. Calibration of the selected model (fitted on development_calibration, judged on validation).
    calibration = {"attempted": selected != "frequency" and len(cal_set[selected]) >= CAL["minimum_matches"],
                   "calibration_set_matches": len(cal_set[selected])}
    if selected == "frequency":
        calibration["reason"] = "not attempted for the frequency baseline"
    elif not calibration["attempted"]:
        calibration["reason"] = f"fewer than {CAL['minimum_matches']} calibration matches: uncalibrated"
    if calibration["attempted"]:
        cal = fit_calibration(cal_set[selected])
        calibrated = [{**p, "p": apply_calibration(cal, p["p"])} for p in val[selected]]
        verdict = accept_calibration(val[selected], calibrated)
        calibration.update({"params": cal, **verdict, "validation_calibrated": breakdown(calibrated)})
        log({"stage": "calibration", "params": cal, **verdict})
    no_uel20 = lambda ps: [p for p in ps if not (p["competition"] == "uefa.europa" and p["season"] == 2020)]  # noqa: E731
    report = {
        "protocol_sha256": PROTOCOL_SHA256, "started_at": started, "finished_at": utc_now(), "run": run_id,
        "open_data_digest": data_digest, "code": code_digest(), "environment": environment(), "through_season": LAST_OPEN,
        "exclusions_open_seasons": _exclusions(conn),
        "notes": ["elo development_tune log loss is in-sample for its draw model (one slope + cut points fitted on the "
                  "same matches): comparable across configurations, optimistic in level",
                  "frequency development figures are in-sample (frequencies fitted on 2010-2019)",
                  "grid-edge selections are reported only; the grids are never extended",
                  "sequential rule: a more complex model is compared with the current choice, not with every simpler model",
                  "ISO-week block intervals ignore dependence across weeks and seasons (anti-conservative)"],
        "counts": {"matches_loaded": len(ds.matches), "revisions_loaded": len(ds.revisions),
                   "scored": {"development_tune": len(freq_tune), "development_calibration": len(cal_set["frequency"]),
                              "validation": len(val["frequency"])}},
        "frequency": {"frequencies": freqs, "development_tune_in_sample": breakdown(freq_tune),
                      "development_calibration": breakdown(cal_set["frequency"]), "validation": breakdown(val["frequency"])},
        "elo": {"selected_config": asdict(elo_cfg), "development_tune_log_loss": elo_ll, "draw_model": elo_dm,
                "grid_size": len(elo_rows), "grid_best5": [{"config": asdict(r[2]), "log_loss": r[0]} for r in
                                                           sorted(elo_rows, key=lambda r: (r[0], r[1]))[:5]],
                "selected_on_grid_edge": _edge(asdict(elo_cfg), ELO_GRID),
                "development_tune": breakdown(elo_tune), "development_calibration": breakdown(cal_set["elo"]),
                "validation": breakdown(val["elo"])},
        "poisson": {"selected_config": asdict(pois_cfg), "development_tune_log_loss": pois_ll,
                    "grid": [{"config": asdict(r[2]), "log_loss": r[0]} for r in sorted(pois_rows, key=lambda r: (r[0], r[1]))],
                    "selected_on_grid_edge": _edge(asdict(pois_cfg), POISSON_GRID),
                    "development_tune": breakdown(pois_tune), "development_calibration": breakdown(cal_set["poisson"]),
                    "validation": breakdown(val["poisson"])},
        "validation_choice": choice, "calibration": calibration,
        "reliability_validation": {mdl: {k: reliability(ps, i) for i, k in enumerate(("home", "draw", "away"))}
                                   for mdl, ps in val.items()},
        "validation_without_uel_2020": {mdl: summary(no_uel20(ps)) for mdl, ps in val.items()},
    }
    candidate = {"model": selected, "protocol_sha256": PROTOCOL_SHA256, "open_data_digest": report["open_data_digest"],
                 "code": report["code"], "source_pins": _P["dataset"]["source_pins"],
                 "config": (asdict(elo_cfg) if selected == "elo" else asdict(pois_cfg) if selected == "poisson" else {}),
                 "draw_model": elo_dm if selected == "elo" else None,
                 "frequencies": freqs if selected == "frequency" else None,
                 "calibration": calibration.get("params") if calibration.get("keep") else None,
                 "environment": report["environment"], "development_run": run_id, "sealed_at": utc_now()}
    sealed = seal_candidate(candidate)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "development_validation_report.json").write_text(json.dumps(report, indent=1, default=str))
    (out_dir / "sealed_candidate.json").write_text(json.dumps(sealed, indent=1))
    forecasts = {"frequency": freq_tune + freq_later, "elo": elo_tune + elo_later, "poisson": pois_tune + pois_later}
    with (out_dir / "forecasts.jsonl").open("w") as f:
        for model, ps in forecasts.items():
            for p in ps:
                f.write(json.dumps({"model": model, **p}) + "\n")
    log({"stage": "sealed", "candidate_sha256": sealed["sha256"], "selected": selected})
    return {"report": report, "sealed": sealed}


def _edge(cfg: dict, grid: dict) -> list[str]:
    return [k for k, v in cfg.items() if k in grid and len(grid[k]) > 1 and not isinstance(v, bool)
            and v in (min(grid[k]), max(grid[k]))]


# --- Frozen model for forecasts and the one-shot test runner (prepared; not executed in this task) ------------------
def frozen_model(sealed: dict, ds: Dataset):
    if sealed["model"] == "frequency":
        return FrequencyModel(sealed["frequencies"])
    if sealed["model"] == "elo":
        return EloModel(EloConfig(**sealed["config"]), ds.members, sealed["draw_model"])
    return PoissonModel(PoissonConfig(**sealed["config"]), ds.members)


def forecast_frozen(sealed: dict, ds: Dataset, seasons) -> list[dict]:
    preds = walk(frozen_model(sealed, ds), ds, seasons)
    if sealed.get("calibration"):
        preds = [{**p, "p": apply_calibration(sealed["calibration"], p["p"])} for p in preds]
    return preds


def _already_run(access_log: Path, sha: str) -> bool:
    if not access_log.exists():
        return False
    return any(json.loads(x).get("purpose") == RUN_START and (sha is None or json.loads(x).get("candidate_sha256") == sha)
               for x in access_log.read_text().splitlines() if x.strip())


def check_frozen(conn: sqlite3.Connection, sealed: dict) -> None:
    problems = []
    if sealed.get("protocol_sha256") != PROTOCOL_SHA256:
        problems.append("protocol differs")
    if sealed.get("code") != code_digest():
        problems.append("code differs")
    if sealed.get("open_data_digest") != open_data_digest(conn):
        problems.append("open data differ")
    if problems:
        raise FrozenMismatch("; ".join(problems))


def _entries(experiments_log: Path, stage: str) -> list[dict]:
    if not experiments_log.exists():
        return []
    return [e for e in (json.loads(x) for x in experiments_log.read_text().splitlines() if x.strip())
            if e.get("stage") == stage]


def check_reserved_present(conn: sqlite3.Connection) -> None:
    """Every reserved season must hold scored-eligible matches: a run against a development copy (no reserved rows)
    would otherwise pass every check and report nothing. Counts only (no outcome is read)."""
    empty = [s for s in TEST if not conn.execute("SELECT 1 FROM soccer_hist_matches WHERE season = ? AND grading = "
                                                 "'gradable' LIMIT 1", (s,)).fetchone()]
    if empty:
        raise FrozenMismatch(f"reserved season(s) {empty} have no scored match in this store: not the full store")


def run_test(conn: sqlite3.Connection, sealed: dict, *, access_log: Path, experiments_log: Path, out_dir: Path) -> dict:
    """The single logged reserved-test run. Refuses an invalid seal, an existing output, a seal already run, changed
    code/protocol/open data; logs the start before any reserved row is read and the completion after writing."""
    if not _valid_seal(sealed):
        raise TestSeasonsLocked("the sealed candidate is invalid")
    if out_dir.exists():
        raise TestRunAlreadyDone(f"output {out_dir} already exists")
    if _already_run(access_log, None):
        raise TestRunAlreadyDone("a reserved test run was already started from this access log (one run, whatever the seal)")
    starts, seals = _entries(experiments_log, "start"), _entries(experiments_log, "sealed")
    if len(starts) != 1 or starts[0].get("run") != sealed.get("development_run"):
        raise FrozenMismatch(f"the experiments log has {len(starts)} development start(s); exactly one, belonging to this "
                             "candidate, is required (any other run may have seen validation results)")
    if not any(e.get("candidate_sha256") == sealed["sha256"] and e.get("run") == sealed.get("development_run") for e in seals):
        raise FrozenMismatch("this candidate was not recorded as sealed by a development run in the experiments log")
    if len(seals) != 1:
        raise FrozenMismatch(f"the experiments log has more than one sealed candidate ({len(seals)}): development was re-run; "
                             "the user must decide which, with the history disclosed")
    check_frozen(conn, sealed)
    check_dataset_pins(conn)
    check_reserved_present(conn)
    _log_access(access_log, sealed, TEST[-1], RUN_START)
    ds = load(conn, through=TEST[-1], sealed=sealed, access_log=access_log, purpose="test run: read reserved seasons")
    preds = forecast_frozen(sealed, ds, TEST)
    report = {"candidate_sha256": sealed["sha256"], "model": sealed["model"], "seasons": list(TEST),
              "test": breakdown(preds), "finished_at": utc_now()}
    out_dir.mkdir(parents=True)
    (out_dir / "test_report.json").write_text(json.dumps(report, indent=1, default=str))
    (out_dir / "sealed_candidate_used.json").write_text(json.dumps(sealed, indent=1))
    _log_access(access_log, sealed, TEST[-1], RUN_DONE)
    return report
