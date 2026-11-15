"""Point-in-time team efficiency features (accuracy plan step 3; definition fixed in docs/EVALUATION_PLAN.md).

Inputs arrive only through `update_box`, which the backtester calls for a box-score observation once its
`available_at` is at or before the current forecast cutoff (backtest.run with box_events). Nothing here reads the
database, a game's own result, or anything after the cutoff.

Per game: possessions = FGA − OREB + TOV + 0.44·FTA per team, averaged over both teams. Offensive rating =
100·points / possessions, defensive rating = 100·opponent points / possessions, pace = possessions (overtime
games are not normalised; minutes are not in the source).
Per team at a reference time: exponentially time-decayed means (half-life HALF_LIFE_DAYS) over the current season's
delivered games. They are adjusted for opponent strength and home court by ITERATIONS rounds, then shrunk toward the
team's previous-season rating (as known now) with a weight of PRIOR_GAMES games. Teams with no previous season use the league average.
The home edge is estimated from the same games, shrunk toward HCA_PRIOR with a weight of HCA_PRIOR_GAMES home games.
"""
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .base import PregameGame, Result

HALF_LIFE_DAYS = 30.0
PRIOR_GAMES = 10.0
ITERATIONS = 20
SIGMA0 = 12.0             # margin SD in points before the first fit (warm-up only; research range 11.6–12.8)
MIN_SIGMA_EXAMPLES = 50
HCA_PRIOR = 1.25          # home edge per team per 100 possessions (≈2.5 total), a small-sample anchor, not tuned
HCA_PRIOR_GAMES = 100.0   # weight of HCA_PRIOR in home games; otherwise a few games attribute team strength to home court
DEFAULT = {"ortg": 110.0, "drtg": 110.0, "pace": 100.0}   # used only before any game has been delivered


@dataclass(frozen=True)
class BoxLine:
    points: Optional[int]
    fga: Optional[int]
    fta: Optional[int]
    oreb: Optional[int]
    tov: Optional[int]

    def complete(self) -> bool:
        return None not in (self.points, self.fga, self.fta, self.oreb, self.tov)

    def possessions(self) -> float:
        return self.fga - self.oreb + self.tov + 0.44 * self.fta


@dataclass(frozen=True)
class BoxEvent:
    """One retrieval of a game's two team lines (team_possession_observations paired by fetch)."""
    game: PregameGame
    available_at: str
    observed_at: str
    fetch_id: int
    home: BoxLine
    away: BoxLine


@dataclass(frozen=True)
class TeamRating:
    ortg: float
    drtg: float
    pace: float
    games: int     # delivered current-season games (unweighted count)

    @property
    def net(self) -> float:
        return self.ortg - self.drtg


def _t(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


class EfficiencyRatings:
    def __init__(self, half_life_days: float = HALF_LIFE_DAYS, prior_games: float = PRIOR_GAMES,
                 iterations: int = ITERATIONS):
        self.half_life_days, self.prior_games, self.iterations = half_life_days, prior_games, iterations
        self.lines: dict[str, BoxEvent] = {}        # latest complete delivered observation per game
        self.skipped_incomplete = 0
        self.season_version: dict[int, int] = {}  # accepted events per season (cache keys)
        self._cache: dict[tuple, tuple[dict[str, TeamRating], float]] = {}
        self._prior_cache: dict[tuple, dict[str, TeamRating]] = {}

    def update_box(self, e: BoxEvent) -> None:
        """Keeps, per game, the most recently observed COMPLETE event among those delivered (last-complete policy).

        An incomplete observation (a missing field for either team) is counted and otherwise ignored: it neither
        replaces the active values nor advances the freshness watermark, which is the (observed_at, fetch_id) of
        complete observations only. A gap in a newer file is treated as missing data, not as evidence that the
        earlier complete values were wrong. The outcome depends only on the set of delivered events, not their order.
        This differs from ingest.POSSESSION_AS_OF_SQL, which returns the latest observation whether complete or not."""
        if not (e.home.complete() and e.away.complete()):
            self.skipped_incomplete += 1
            return
        cur = self.lines.get(e.game.game_id)
        if cur is not None and (cur.observed_at, cur.fetch_id) >= (e.observed_at, e.fetch_id):
            return
        self.lines[e.game.game_id] = e
        self.season_version[e.game.season] = self.season_version.get(e.game.season, 0) + 1

    def ratings(self, season: int, reference: str) -> tuple[dict[str, TeamRating], float]:
        """(team → rating, home-court edge in points per 100 possessions) for `season` as known now."""
        key = (season, reference, self.season_version.get(season, 0), self.season_version.get(season - 1, 0))
        if key not in self._cache:
            self._cache = {key: self._compute(season, reference, self.prior(season))}  # keep only the latest
        return self._cache[key]

    def prior(self, season: int) -> Optional[dict[str, TeamRating]]:
        """Previous season's ratings as of its own last delivered game (not decayed to today), or None."""
        prev = [e for e in self.lines.values() if e.game.season == season - 1]
        if not prev:
            return None
        key = (season - 1, self.season_version.get(season - 1, 0))
        if key not in self._prior_cache:
            end = max(e.game.tipoff_at for e in prev)
            self._prior_cache[key] = self._compute(season - 1, end, prior=None)[0]
        return self._prior_cache[key]

    def _compute(self, season: int, reference: str, prior: Optional[dict[str, TeamRating]]
                 ) -> tuple[dict[str, TeamRating], float]:
        ref = _t(reference)
        obs = []  # (team, opp, is_home, neutral, ortg, drtg, poss, weight)
        for e in self.lines.values():
            if e.game.season != season:
                continue
            poss = (e.home.possessions() + e.away.possessions()) / 2
            if poss <= 0:
                continue
            w = 0.5 ** (max(0.0, (ref - _t(e.game.tipoff_at)).total_seconds() / 86400) / self.half_life_days)
            h_o, a_o = 100 * e.home.points / poss, 100 * e.away.points / poss
            obs.append((e.game.home_team_id, e.game.away_team_id, 1, e.game.neutral_site, h_o, a_o, poss, w))
            obs.append((e.game.away_team_id, e.game.home_team_id, 0, e.game.neutral_site, a_o, h_o, poss, w))
        teams = {o[0] for o in obs} | set(prior or {})
        if not obs:
            base = prior or {}
            return {t: TeamRating(r.ortg, r.drtg, r.pace, 0) for t, r in base.items()}, HCA_PRIOR
        wsum = sum(o[7] for o in obs)
        lg_o = sum(o[4] * o[7] for o in obs) / wsum
        lg_p = sum(o[6] * o[7] for o in obs) / wsum
        home = [o for o in obs if o[2] == 1 and not o[3]]
        hw = sum(o[7] for o in home)
        # Home edge per team per 100 possessions, shrunk toward HCA_PRIOR so that team strength in small samples
        # (e.g. one team always at home) is not absorbed as home court.
        hca = (sum((o[4] - o[5]) / 2 * o[7] for o in home) + HCA_PRIOR_GAMES * HCA_PRIOR) / (hw + HCA_PRIOR_GAMES)
        pri = {t: (prior[t] if prior and t in prior else TeamRating(lg_o, lg_o, lg_p, 0)) for t in teams}
        o_r = {t: pri[t].ortg for t in teams}
        d_r = {t: pri[t].drtg for t in teams}
        p_r = {t: pri[t].pace for t in teams}
        counts = {t: 0 for t in teams}
        for o in obs:
            counts[o[0]] += 1
        for _ in range(self.iterations):
            acc = {t: [0.0, 0.0, 0.0, 0.0] for t in teams}  # sum_w·o, sum_w·d, sum_w·p, sum_w
            for team, opp, is_home, neutral, ortg, drtg, poss, w in obs:
                edge = 0.0 if neutral else (hca if is_home else -hca)
                a = acc[team]
                a[0] += w * (ortg - edge - (d_r[opp] - lg_o))
                a[1] += w * (drtg + edge - (o_r[opp] - lg_o))
                a[2] += w * (poss - (p_r[opp] - lg_p))
                a[3] += w
            k = self.prior_games
            o_r = {t: (a[0] + k * pri[t].ortg) / (a[3] + k) for t, a in acc.items()}
            d_r = {t: (a[1] + k * pri[t].drtg) / (a[3] + k) for t, a in acc.items()}
            p_r = {t: (a[2] + k * pri[t].pace) / (a[3] + k) for t, a in acc.items()}
        return {t: TeamRating(o_r[t], d_r[t], p_r[t], counts[t]) for t in teams}, hca

    def features(self, g: PregameGame) -> dict:
        """Home-minus-away features for a pregame game, using the game's tipoff date as the decay reference."""
        ratings, hca = self.ratings(g.season, g.tipoff_at[:10] + "T00:00:00Z")
        fallback = TeamRating(DEFAULT["ortg"], DEFAULT["drtg"], DEFAULT["pace"], 0)
        h, a = ratings.get(g.home_team_id, fallback), ratings.get(g.away_team_id, fallback)
        pace = (h.pace + a.pace) / 2
        edge = 0.0 if g.neutral_site else 2 * hca
        return {
            "net_home": h.net, "net_away": a.net, "net_diff": h.net - a.net,
            "pace": pace, "home_edge_per100": edge,
            # Expected home margin in points: rating gap plus home edge, scaled to the expected possessions.
            "margin_est": (h.net - a.net + edge) * pace / 100,
            "games_home": h.games, "games_away": a.games,
        }


def is_finite(features: dict) -> bool:
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in features.values())


class EfficiencyMargin:
    """Candidate (a), docs/EVALUATION_PLAN.md: P(home win) = Φ(margin_est / σ).

    σ is refit at each season start as the root-mean-square of (actual home margin − margin_est) over games from
    earlier seasons only, with margin_est as captured at predict time. Before the first fit σ = SIGMA0."""
    name = "efficiency_margin"

    def __init__(self, sigma0: float = SIGMA0):
        self.eff = EfficiencyRatings()
        self.sigma0, self.sigma = sigma0, sigma0
        self.pending: dict[str, tuple[int, float]] = {}
        self.residuals: list[tuple[int, float]] = []   # (season, actual margin − predicted margin)
        self.season: Optional[int] = None
        self.first_fit_season: Optional[int] = None

    def _refit(self, season: int) -> None:
        res = [r for s, r in self.residuals if s < season]
        if len(res) < MIN_SIGMA_EXAMPLES:
            return
        self.sigma = math.sqrt(sum(r * r for r in res) / len(res))
        if self.first_fit_season is None:
            self.first_fit_season = season

    def predict(self, g: PregameGame) -> float:
        if self.season is None or g.season > self.season:
            self.season = g.season
            self._refit(g.season)
        margin = self.eff.features(g)["margin_est"]
        self.pending[g.game_id] = (g.season, margin)
        return 0.5 * (1 + math.erf(margin / (self.sigma * math.sqrt(2))))

    def update(self, r: Result) -> None:
        season, margin = self.pending.pop(r.game.game_id)
        self.residuals.append((season, (r.home_score - r.away_score) - margin))

    def update_box(self, e: BoxEvent) -> None:
        self.eff.update_box(e)

    def config(self) -> dict:
        return {"sigma0": self.sigma0, "half_life_days": self.eff.half_life_days, "prior_games": self.eff.prior_games,
                "iterations": self.eff.iterations, "hca_prior": HCA_PRIOR, "hca_prior_games": HCA_PRIOR_GAMES}
