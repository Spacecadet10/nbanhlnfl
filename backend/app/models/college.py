"""NCAA men's Division I basketball models (docs/NCAAB_PLAN.md "Evaluation plan", amendment 2026-11-17).

* `CollegeElo` (`ncaab-college_elo`): its own Elo (no NBA value reused); home advantage only off neutral sites; margin
  = Elo difference / 25 (the preregistered spread baseline).
* `CollegeEff` (`ncaab-college_eff`): adjusted offensive/defensive efficiency (points per 100 possessions) and tempo per
  team, opponent- and venue-adjusted online; possessions = FGA + 0.475*FTA from the scoreboard (amendment; a game
  without FGA/FTA updates from points only with tempo fixed). Early in a season each rating is the carried prior blended
  with this season's evidence by games played (prior weight w0 games).
* `RunningPoints` (`ncaab-running_points`): opponent-unadjusted running points scored/allowed (total and team-total baseline).

Every model's `predict`/`expected` is pure (no state change), so a live slate shares one rating state and the same inputs
always give the same output. Market probabilities come from normal distributions of margin, total and team points, read
on integer scores (a whole-number line can push; half-point lines cannot).

`predict(conn, game_id, *, cutoff, lines=None)` is the interface `app/ncaab_app.py` calls. It uses the sealed decision
(`ncaab_frozen_decision.json`, sha256 self-check) and reads only results with result_available_at <= cutoff.
"""
import hashlib
import json
import math
import sqlite3
from collections import OrderedDict, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from .base import PregameGame, Result

PREFIX = "ncaab-"
INITIAL = 1500.0
ELO_POINTS_PER_POINT = 25.0       # preregistered spread baseline: Elo difference / 25 = home margin in points
FTA_WEIGHT = 0.475                # possessions = FGA + 0.475 * FTA (amendment 2026-11-17)
LEAGUE_RATE = 0.002               # EMA weight of each game in the league efficiency/tempo means (fixed, not tuned)
START_EFFICIENCY = 100.0          # league starting values; washed out in the warm-up seasons
START_TEMPO = 68.0
LINE_MARKETS = ("spread", "total", "team_total_home", "team_total_away")
COMMITTED_DECISION_PATH = Path(__file__).parent / "ncaab_frozen_decision.json"
DECISION_PATH = COMMITTED_DECISION_PATH
ELIGIBLE_TYPES = ("regular", "postseason")
VALIDATION_SEASONS = (2022, 2023, 2024)
VALIDATION_COMPLETE_AT = "2024-07-01T00:00:00Z"   # after this cutoff every validation result is in the store


class FrozenDecisionError(ValueError):
    """The sealed NCAAB decision is missing, modified or does not match the store's validation data."""


class NotForecastable(ValueError):
    """The game is not an eligible Division I game, or the cutoff is not before its start."""


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def version(name: str, cfg: Optional[dict]) -> str:
    return name + ("" if not cfg else "-" + "-".join(f"{k}={v}" for k, v in sorted(cfg.items())))


# --- Elo -----------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class CollegeEloConfig:
    k: float = 30.0
    home_advantage: float = 75.0
    carry: float = 1.0      # share of (rating - 1500) kept at a new season
    mov: bool = False       # margin-of-victory multiplier ((|m| + 3)^0.8 / (7.5 + 0.006 * winner Elo edge))


class CollegeElo:
    name = PREFIX + "college_elo"

    def __init__(self, cfg: CollegeEloConfig = CollegeEloConfig()):
        self.cfg = cfg
        self.ratings: dict[str, float] = {}
        self.season: Optional[int] = None

    def _roll(self, season: int) -> None:
        if self.season is not None and season > self.season:
            for t, r in self.ratings.items():
                self.ratings[t] = INITIAL + self.cfg.carry * (r - INITIAL)
        if self.season is None or season > self.season:
            self.season = season

    def rating(self, team: str, season: int) -> float:
        r = self.ratings.get(team, INITIAL)
        if self.season is not None and season > self.season:
            r = INITIAL + self.cfg.carry * (r - INITIAL)
        return r

    def diff(self, game: PregameGame) -> float:
        h = 0.0 if game.neutral_site else self.cfg.home_advantage
        return self.rating(game.home_team_id, game.season) + h - self.rating(game.away_team_id, game.season)

    def predict(self, game: PregameGame) -> float:
        return 1 / (1 + 10 ** (-self.diff(game) / 400))

    def margin(self, game: PregameGame) -> float:
        return self.diff(game) / ELO_POINTS_PER_POINT

    def update(self, result: Result) -> None:
        g = result.game
        self._roll(g.season)
        diff = self.diff(g)
        expected = 1 / (1 + 10 ** (-diff / 400))
        mult = 1.0
        if self.cfg.mov:
            margin = abs(result.home_score - result.away_score)
            mult = ((margin + 3) ** 0.8) / (7.5 + 0.006 * (diff if result.home_won else -diff))
        delta = self.cfg.k * mult * ((1.0 if result.home_won else 0.0) - expected)
        self.ratings[g.home_team_id] = self.ratings.get(g.home_team_id, INITIAL) + delta
        self.ratings[g.away_team_id] = self.ratings.get(g.away_team_id, INITIAL) - delta

    def config(self) -> dict:
        return asdict(self.cfg)


# --- Efficiency ----------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class CollegeEffConfig:
    eta: float = 0.04       # in-season step (after the first 1/eta games the evidence is an EMA with this weight)
    carry: float = 0.7      # share of the end-of-season rating kept as next season's prior (shrink to the D-I mean)
    w0: int = 8             # prior weight in games


def possessions(box) -> Optional[float]:
    """Game possessions from the scoreboard: mean of both teams' FGA + 0.475*FTA, or None without both sides' FGA/FTA."""
    if not box:
        return None
    (hfga, hfta), (afga, afta) = box
    if None in (hfga, hfta, afga, afta):
        return None
    return ((hfga + FTA_WEIGHT * hfta) + (afga + FTA_WEIGHT * afta)) / 2


class CollegeEff:
    """Ratings relative to the D-I mean: [offence, defence (points allowed above average), tempo] per team.
    Expected home points = (E + off_h + def_a) * poss / 100 + HA/2, away = (E + off_a + def_h) * poss / 100 - HA/2,
    poss = T + tempo_h + tempo_a; HA (points) = running mean of residual margins of non-neutral games, 0 on neutral sites."""
    name = PREFIX + "college_eff"

    def __init__(self, cfg: CollegeEffConfig = CollegeEffConfig(), box: Optional[dict] = None):
        self.cfg = cfg
        self.box = box if box is not None else {}
        self.prior: dict[str, list] = {}
        self.cur: dict[str, list] = {}
        self.n: dict[str, int] = defaultdict(int)
        self.season: Optional[int] = None
        self.eff, self.tempo = START_EFFICIENCY, START_TEMPO
        self.ha, self.ha_n = 0.0, 0

    def _blend(self, team: str) -> list:
        prior = self.prior.get(team, [0.0, 0.0, 0.0])
        n = self.n.get(team, 0)
        if n == 0:
            return list(prior)
        cur = self.cur[team]
        w0 = self.cfg.w0
        return [(w0 * p + n * c) / (w0 + n) for p, c in zip(prior, cur)]

    def rating(self, team: str, season: int) -> list:
        r = self._blend(team)
        if self.season is not None and season > self.season:
            r = [self.cfg.carry * x for x in r]
        return r

    def _roll(self, season: int) -> None:
        if self.season is not None and season > self.season:
            teams = set(self.prior) | set(self.cur)
            new = {t: [self.cfg.carry * x for x in self._blend(t)] for t in teams}
            self.prior, self.cur, self.n = new, {}, defaultdict(int)
        if self.season is None or season > self.season:
            self.season = season

    def _neutral_means(self, game: PregameGame) -> tuple[float, float, float]:
        oh, dh, th = self.rating(game.home_team_id, game.season)
        oa, da, ta = self.rating(game.away_team_id, game.season)
        poss = max(40.0, self.tempo + th + ta)
        return (self.eff + oh + da) * poss / 100, (self.eff + oa + dh) * poss / 100, poss

    def expected(self, game: PregameGame) -> tuple[float, float]:
        mh, ma, _ = self._neutral_means(game)
        h = 0.0 if game.neutral_site else self.ha
        return mh + h / 2, ma - h / 2

    def predict(self, game: PregameGame, sigma_margin: float = 11.0) -> float:
        mh, ma = self.expected(game)
        return _phi((mh - ma) / sigma_margin)

    def update(self, result: Result) -> None:
        g = result.game
        self._roll(g.season)
        hs, as_ = result.home_score, result.away_score
        mh, ma, poss_pred = self._neutral_means(g)
        h = 0.0 if g.neutral_site else self.ha
        poss_obs = possessions(self.box.get(g.game_id))
        poss = poss_obs if poss_obs is not None else poss_pred
        oh, dh, th = self.rating(g.home_team_id, g.season)
        oa, da, ta = self.rating(g.away_team_id, g.season)
        eff_h = 100 * (hs - h / 2) / poss        # venue removed
        eff_a = 100 * (as_ + h / 2) / poss
        ev_h = [eff_h - self.eff - da, eff_a - self.eff - oa, None]
        ev_a = [eff_a - self.eff - dh, eff_h - self.eff - oh, None]
        if poss_obs is not None:
            ev_h[2] = poss_obs - self.tempo - ta
            ev_a[2] = poss_obs - self.tempo - th
        for team, ev in ((g.home_team_id, ev_h), (g.away_team_id, ev_a)):
            n = self.n.get(team, 0)
            step = max(self.cfg.eta, 1.0 / (n + 1))
            # this season's evidence starts at the prior; the first game's step is 1, then a running mean until 1/eta games
            cur = list(self.prior.get(team, [0.0, 0.0, 0.0])) if n == 0 else self.cur[team]
            for i in range(3):
                if ev[i] is not None:
                    cur[i] += step * (ev[i] - cur[i])
            self.cur[team] = cur
            self.n[team] = n + 1
        if not g.neutral_site:
            self.ha_n += 1
            self.ha += ((hs - as_) - (mh - ma) - self.ha) / self.ha_n
        self.eff += LEAGUE_RATE * ((eff_h + eff_a) / 2 - self.eff)
        if poss_obs is not None:
            self.tempo += LEAGUE_RATE * (poss_obs - self.tempo)

    def config(self) -> dict:
        return asdict(self.cfg)


# --- Running points baseline ----------------------------------------------------------------------------------------
class RunningPoints:
    """Expected home points = (home's points scored per game + away's points allowed per game) / 2 (and the reverse),
    from this season's results; before a team's first game, its previous season's means; with no history, the league mean."""
    name = PREFIX + "running_points"

    def __init__(self):
        self.cur: dict[str, list] = {}      # team -> [scored, allowed, games] this season
        self.last: dict[str, tuple] = {}    # team -> (scored mean, allowed mean) last season it played
        self.season: Optional[int] = None
        self.league = [0.0, 0]

    def _roll(self, season: int) -> None:
        if self.season is not None and season > self.season:
            for t, (s, a, n) in self.cur.items():
                self.last[t] = (s / n, a / n)
            self.cur = {}
        if self.season is None or season > self.season:
            self.season = season

    def _means(self, team: str, season: int) -> tuple[float, float]:
        league = self.league[0] / self.league[1] if self.league[1] else 70.0
        if self.season is not None and season > self.season:
            if team in self.cur:
                s, a, n = self.cur[team]
                return s / n, a / n
            return self.last.get(team, (league, league))
        if team in self.cur:
            s, a, n = self.cur[team]
            return s / n, a / n
        return self.last.get(team, (league, league))

    def expected(self, game: PregameGame) -> tuple[float, float]:
        hs, ha = self._means(game.home_team_id, game.season)
        as_, aa = self._means(game.away_team_id, game.season)
        return (hs + aa) / 2, (as_ + ha) / 2

    def predict(self, game: PregameGame) -> float:
        mh, ma = self.expected(game)
        return _phi((mh - ma) / 11.0)

    def update(self, result: Result) -> None:
        g = result.game
        self._roll(g.season)
        for team, s, a in ((g.home_team_id, result.home_score, result.away_score),
                           (g.away_team_id, result.away_score, result.home_score)):
            c = self.cur.setdefault(team, [0.0, 0.0, 0])
            c[0] += s
            c[1] += a
            c[2] += 1
            self.league[0] += s
            self.league[1] += 1

    def config(self) -> dict:
        return {}


# --- Market probabilities --------------------------------------------------------------------------------------------
def _threshold(market: str, line: float) -> float:
    """The value of the market's quantity above which side 'a' (home covers / over) wins: margin > -line for spreads."""
    return -line if market == "spread" else line


def market_probs(market: str, line: float, mean: float, sd: float) -> tuple[float, float, float]:
    """(a, push, b) for a normal quantity (home margin, total or team points) read on integer scores. A whole-number line
    pushes with the probability of that exact score; a spread of 0 cannot push (no ties in basketball)."""
    c = _threshold(market, float(line))
    if c == int(c):
        hi = _phi((c + 0.5 - mean) / sd)
        lo = _phi((c - 0.5 - mean) / sd)
        if market == "spread" and c == 0:
            pa, pm = 1 - _phi((0 - mean) / sd), 0.0
        else:
            pa, pm = 1 - hi, hi - lo
    else:
        pa, pm = 1 - _phi((math.floor(c) + 0.5 - mean) / sd), 0.0
    eps = 1e-9
    pa = min(1 - eps, max(eps, pa))
    pb = max(eps, 1 - pa - pm)
    total = pa + pm + pb
    return pa / total, pm / total, pb / total


def own_line(market: str, mean: float) -> float:
    """The model's own half-point line nearest the median (spread: the home handicap, minus the margin line)."""
    mid = math.floor(mean) + 0.5
    return -mid if market == "spread" else mid


def market_moments(kind: str, means: dict, sigmas: dict, market: str) -> tuple[float, float]:
    """(mean, sd) of a market's quantity for a market model: 'college_eff' (joint margin/total normal), 'elo_margin'
    (spread) or 'running_points' (totals)."""
    if kind == "college_eff":
        mh, ma = means["eff"]
        sm, st, rho = sigmas["eff_margin"], sigmas["eff_total"], sigmas["eff_rho"]
        if market == "spread":
            return mh - ma, sm
        if market == "total":
            return mh + ma, st
        sign = 1 if market == "team_total_home" else -1
        return (mh if sign == 1 else ma), math.sqrt(max(1e-9, st * st + sm * sm + 2 * sign * rho * st * sm)) / 2
    if kind == "elo_margin" and market == "spread":
        return means["elo_margin"], sigmas["elo_margin"]
    if kind == "running_points" and market != "spread":
        ph, pa = means["points"]
        return {"total": (ph + pa, sigmas["points_total"]), "team_total_home": (ph, sigmas["points_home"]),
                "team_total_away": (pa, sigmas["points_away"])}[market]
    raise ValueError(f"{kind} does not price {market}")


# --- Sealed decision ---------------------------------------------------------------------------------------------------
def seal(decision: dict) -> dict:
    body = {k: v for k, v in decision.items() if k != "sha256"}
    return {**body, "sha256": hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()}


def winner_version(d: dict) -> str:
    if d["winner_model"] == "college_elo":
        v = version(CollegeElo.name, d["college_elo_config"])
    elif d["winner_model"] == "college_eff":
        v = version(CollegeEff.name, d["college_eff_config"])
    else:
        raise FrozenDecisionError(f"Unknown winner model {d['winner_model']!r}")
    return v + ("+platt" if d.get("winner_calibration") else "")


def market_version(d: dict, kind: str) -> str:
    if kind == "college_eff":
        return version(CollegeEff.name, d["college_eff_config"])
    if kind == "elo_margin":
        return version(CollegeElo.name, d["college_elo_config"]) + "+margin"
    if kind == "running_points":
        return RunningPoints.name
    raise FrozenDecisionError(f"Unknown market model {kind!r}")


REQUIRED = ("sport", "model_version", "winner_model", "college_elo_config", "college_eff_config", "sigmas", "markets",
            "validation_dataset_version", "validation_seasons", "selected_on", "data_cutoff", "feature_version",
            "training_command", "frozen_at", "validation_report")


def load_decision(path: Optional[Path] = None) -> dict:
    """The sealed decision; refuses a missing, malformed or modified file (sha256 of the canonical content)."""
    path = Path(path or DECISION_PATH)
    if not path.is_file():
        raise FrozenDecisionError(f"No sealed NCAAB decision at {path}")
    try:
        d = json.loads(path.read_text())
    except ValueError as exc:
        raise FrozenDecisionError(f"Malformed NCAAB decision {path}: {exc}") from exc
    if d.get("sha256") != seal(d)["sha256"]:
        raise FrozenDecisionError(f"{path} was modified after sealing (digest mismatch)")
    missing = [k for k in REQUIRED if k not in d]
    if missing:
        raise FrozenDecisionError(f"NCAAB decision {path} is missing {missing}")
    if d["sport"] != "ncaab" or d["selected_on"] != "validation":
        raise FrozenDecisionError("The NCAAB decision must be an NCAAB validation decision")
    if set(d["college_elo_config"]) != set(CollegeEloConfig.__dataclass_fields__) or \
            set(d["college_eff_config"]) != set(CollegeEffConfig.__dataclass_fields__):
        raise FrozenDecisionError("The NCAAB decision must list every model parameter")
    if d["model_version"] != winner_version(d):
        raise FrozenDecisionError("The NCAAB decision's model_version does not match its winner model")
    if set(d["markets"]) != set(LINE_MARKETS):
        raise FrozenDecisionError(f"The NCAAB decision must state every line market {LINE_MARKETS}")
    return d


def platt(p: float, cal: Optional[list]) -> float:
    if not cal:
        return p
    a, b = cal
    p = min(1 - 1e-12, max(1e-12, p))
    z = a * math.log(p / (1 - p)) + b
    return 1 / (1 + math.exp(-z))


# --- Store access ------------------------------------------------------------------------------------------------------
def box_stats(conn: sqlite3.Connection) -> dict:
    """game_id -> ((home FGA, FTA), (away FGA, FTA)) from the scoreboard (team_game_stats; same retrieval as the final
    score). Used only by `CollegeEff.update`, i.e. once the game's result is available at the cutoff."""
    sides: dict = {}
    for r in conn.execute("SELECT game_id, is_home, fga, fta FROM team_game_stats"):
        sides.setdefault(r[0], [None, None])[0 if r[1] else 1] = (r[2], r[3])
    return {g: tuple(s) for g, s in sides.items() if None not in s and None not in (*s[0], *s[1])}


def rows_at(conn: sqlite3.Connection, cutoff: str, seasons=None) -> tuple[list, dict]:
    """Eligible Division I finals with result_available_at <= cutoff (app.ncaab.load_rows) as backtest rows, and the
    scoreboard FGA/FTA of exactly those games."""
    from .. import backtest, ncaab
    raw = ncaab.load_rows(conn, cutoff=cutoff, seasons=seasons)
    rows = [backtest.Row(PregameGame(r["game_id"], r["season"], r["season_type"], r["tipoff_at"], r["home_team_id"],
                                     r["away_team_id"], bool(r["neutral_site"])), backtest.cutoff_for(r["tipoff_at"]),
                         r["home_score"], r["away_score"], r["result_available_at"]) for r in raw]
    ids = {r.game.game_id for r in rows}
    box = {g: b for g, b in box_stats(conn).items() if g in ids}
    return rows, box


def rows_version(rows: list) -> str:
    """app.ncaab.dataset_version of backtest rows (the same fields, so it equals the loader's hash of those games)."""
    from .. import ncaab
    return ncaab.dataset_version(
        {"game_id": r.game.game_id, "season": r.game.season, "season_type": r.game.season_type, "tipoff_at": r.game.tipoff_at,
         "home_team_id": r.game.home_team_id, "away_team_id": r.game.away_team_id, "neutral_site": int(r.game.neutral_site),
         "home_score": r.home_score, "away_score": r.away_score, "result_available_at": r.result_available_at} for r in rows)


@dataclass
class State:
    decision: dict
    elo: CollegeElo
    eff: CollegeEff
    points: RunningPoints
    results: int


_CACHE: "OrderedDict[tuple, State]" = OrderedDict()
CACHE_SIZE = 4


def clear_cache() -> None:
    _CACHE.clear()


def _store_key(conn: sqlite3.Connection) -> tuple:
    db = conn.execute("PRAGMA database_list").fetchone()[2]
    games = tuple(conn.execute("SELECT COUNT(*), MAX(updated_at), MAX(result_available_at) FROM games").fetchone())
    stats = tuple(conn.execute("SELECT COUNT(*), MAX(last_raw_id) FROM team_game_stats").fetchone())
    ctx = tuple(conn.execute("SELECT COUNT(*), MAX(updated_at) FROM ncaab_game_context").fetchone())
    return db, games, stats, ctx


def state(conn: sqlite3.Connection, cutoff: str) -> State:
    """The rating state after every result available at `cutoff`, computed once per (store state, cutoff, decision)."""
    d = load_decision()
    key = (_store_key(conn), cutoff, d["sha256"])
    if key in _CACHE:
        _CACHE.move_to_end(key)
        return _CACHE[key]
    rows, box = rows_at(conn, cutoff)
    if cutoff >= VALIDATION_COMPLETE_AT:
        seasons = set(d["validation_seasons"])
        current = rows_version([r for r in rows if r.game.season in seasons])
        if current != d["validation_dataset_version"]:
            raise FrozenDecisionError(f"NCAAB validation data changed since sealing "
                                      f"({d['validation_dataset_version']} -> {current})")
    elo = CollegeElo(CollegeEloConfig(**d["college_elo_config"]))
    eff = CollegeEff(CollegeEffConfig(**d["college_eff_config"]), box)
    pts = RunningPoints()
    for r in sorted(rows, key=lambda r: (r.result_available_at, r.game.tipoff_at, r.game.game_id)):
        res = r.result
        elo.update(res)
        eff.update(res)
        pts.update(res)
    st = State(d, elo, eff, pts, len(rows))
    _CACHE[key] = st
    while len(_CACHE) > CACHE_SIZE:
        _CACHE.popitem(last=False)
    return st


def _pregame(conn: sqlite3.Connection, game_id: str, cutoff: str) -> PregameGame:
    g = conn.execute("SELECT g.*, c.both_d1 FROM games g LEFT JOIN ncaab_game_context c ON c.game_id = g.game_id "
                     "WHERE g.game_id = ?", (game_id,)).fetchone()
    if g is None:
        raise NotForecastable(f"No game {game_id}")
    if g["season_type"] not in ELIGIBLE_TYPES or g["both_d1"] != 1:
        raise NotForecastable(f"{game_id} is not an eligible Division I game (non-D-I games are never forecast)")
    if cutoff > g["tipoff_at"]:
        raise NotForecastable(f"{game_id}: cutoff {cutoff} is after the start {g['tipoff_at']}")
    return PregameGame(g["game_id"], g["season"], g["season_type"], g["tipoff_at"], g["home_team_id"], g["away_team_id"],
                       bool(g["neutral_site"]), cutoff)


def predict(conn: sqlite3.Connection, game_id: str, *, cutoff: str, lines: Optional[dict] = None) -> dict:
    """{model_version, home_win_prob, markets: {market: {line, probs (a, push, b), mean, sd, model_version}}} for one
    eligible game from results available at `cutoff`. Markets at the model's own half-point line unless `lines` gives one.
    The spread line is the home handicap (home covers when home - away + line > 0). Research markets are omitted."""
    game = _pregame(conn, game_id, cutoff)
    st = state(conn, cutoff)
    d = st.decision
    sig = d["sigmas"]
    means = {"elo_margin": st.elo.margin(game), "eff": st.eff.expected(game), "points": st.points.expected(game)}
    if d["winner_model"] == "college_elo":
        p = st.elo.predict(game)
    else:
        mh, ma = means["eff"]
        p = _phi((mh - ma) / sig["eff_margin"])
    p = float(platt(p, d.get("winner_calibration")))
    out = {"model_version": d["model_version"], "home_win_prob": p, "markets": {}}
    for market in LINE_MARKETS:
        kind = d["markets"][market]["model"]
        if kind == "research":
            continue
        mean, sd = market_moments(kind, means, sig, market)
        line = float((lines or {}).get(market, own_line(market, mean)))
        out["markets"][market] = {"line": line, "probs": market_probs(market, line, mean, sd), "mean": mean, "sd": sd,
                                  "model_version": market_version(d, kind)}
    return out
