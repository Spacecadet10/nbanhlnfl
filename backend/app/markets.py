"""Market definitions, score distributions and settlement (docs/MARKETS.md).

Every market probability for a game comes from one score distribution, so the markets of one forecast cannot contradict
each other. Sides: `a` (home / over), `mid` (push or regulation draw), `b` (away / under).
"""
import math
from dataclasses import dataclass
from typing import Optional

LINE_MARKETS = ("spread", "total", "team_total_home", "team_total_away")
MID_LABEL = {"reg3": "draw", "spread": "push", "total": "push", "team_total_home": "push", "team_total_away": "push"}
SIDE_LABELS = {"winner": ("home", None, "away"), "reg3": ("home", "draw", "away"), "spread": ("home", "push", "away"),
               "total": ("over", "push", "under"), "team_total_home": ("over", "push", "under"),
               "team_total_away": ("over", "push", "under")}
VOID_IF_MOVED_HOURS = 12
SUM_TOLERANCE = 1e-6


@dataclass(frozen=True)
class MarketSpec:
    sport: str
    market: str
    title: str
    overtime: str          # what the settlement score includes
    line_step: Optional[float]


SPEC = {(s.sport, s.market): s for s in (
    MarketSpec("nba", "winner", "Winner", "overtime counts", None),
    MarketSpec("nba", "spread", "Point spread", "overtime counts", 0.5),
    MarketSpec("nba", "total", "Game total points", "overtime counts", 0.5),
    MarketSpec("nba", "team_total_home", "Home team points", "overtime counts", 0.5),
    MarketSpec("nba", "team_total_away", "Away team points", "overtime counts", 0.5),
    MarketSpec("nhl", "winner", "Winner (incl. OT and shootout)", "overtime and shootout count", None),
    MarketSpec("nhl", "reg3", "Regulation result (3-way)", "regulation only; OT/SO games are a draw", None),
    MarketSpec("nhl", "spread", "Puck line", "overtime and shootout count (shootout winner +1 goal)", 0.5),
    MarketSpec("nhl", "total", "Game total goals", "overtime and shootout count (shootout winner +1 goal)", 0.5),
    MarketSpec("nhl", "team_total_home", "Home team goals", "overtime and shootout count", 0.5),
    MarketSpec("nhl", "team_total_away", "Away team goals", "overtime and shootout count", 0.5),
)}


def spec(sport: str, market: str) -> MarketSpec:
    try:
        return SPEC[(sport, market)]
    except KeyError:
        raise ValueError(f"Market {market!r} is not defined for {sport}") from None


# --- Settlement -------------------------------------------------------------------------------------------------
def _parse(ts: str):
    from datetime import datetime, timezone
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def settle(market: str, line: Optional[float], *, status: str, home: Optional[int], away: Optional[int],
           decision: Optional[str] = None, tipoff_at: Optional[str] = None, forecast_tipoff_at: Optional[str] = None) -> str:
    """Realised side 'a' / 'mid' / 'b', or 'pending' / 'void' / 'unscorable' (docs/MARKETS.md)."""
    if status in ("postponed", "canceled"):
        return "void"
    if tipoff_at and forecast_tipoff_at and \
            abs((_parse(tipoff_at) - _parse(forecast_tipoff_at)).total_seconds()) > VOID_IF_MOVED_HOURS * 3600:
        return "void"
    if status != "final":
        return "pending"
    if home is None or away is None:
        return "unscorable"
    if market == "winner":
        return "a" if home > away else "b"
    if market == "reg3":
        if decision is None:
            return "unscorable"
        if decision != "REG":
            return "mid"
        return "a" if home > away else "b"
    if line is None:
        return "unscorable"
    value = {"spread": home - away + line, "total": home + away - line,
             "team_total_home": home - line, "team_total_away": away - line}[market]
    return "a" if value > 0 else "b" if value < 0 else "mid"


# --- Distributions ----------------------------------------------------------------------------------------------
def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _clean(pa: float, pmid: float, pb: float) -> tuple[float, float, float]:
    """Clips rounding noise and renormalises; raises if the inputs are not a probability vector."""
    vals = [max(0.0, v) for v in (pa, pmid, pb)]
    total = sum(vals)
    if not math.isfinite(total) or abs(total - 1) > 1e-6:
        raise ValueError(f"Probabilities do not sum to 1: {pa}, {pmid}, {pb}")
    return tuple(v / total for v in vals)  # type: ignore[return-value]


class ScoreGrid:
    """Joint pmf of final (home, away) scores on a finite grid, with optional regulation detail (NHL)."""

    def __init__(self, pmf: dict[tuple[int, int], float], reg3: Optional[tuple[float, float, float]] = None,
                 params: Optional[dict] = None):
        total = sum(pmf.values())
        self.pmf = {k: v / total for k, v in pmf.items() if v > 0}
        self._reg3 = reg3
        self.params = params or {}

    def _sum(self, pred) -> float:
        return sum(p for (h, a), p in self.pmf.items() if pred(h, a))

    def winner(self) -> tuple[float, float, float]:
        tie = self._sum(lambda h, a: h == a)
        if tie > 1e-9:
            raise ValueError("A final-score distribution cannot contain ties")
        pa = self._sum(lambda h, a: h > a)
        return _clean(pa, 0.0, 1 - pa)

    def reg3(self) -> tuple[float, float, float]:
        if self._reg3 is None:
            raise ValueError("This distribution has no regulation detail")
        return _clean(*self._reg3)

    def _line(self, value) -> tuple[float, float, float]:
        pa = self._sum(lambda h, a: value(h, a) > 0)
        pmid = self._sum(lambda h, a: value(h, a) == 0)
        return _clean(pa, pmid, 1 - pa - pmid)

    def market(self, market: str, line: Optional[float] = None) -> tuple[float, float, float]:
        if market == "winner":
            return self.winner()
        if market == "reg3":
            return self.reg3()
        fn = {"spread": lambda h, a: h - a + line, "total": lambda h, a: h + a - line,
              "team_total_home": lambda h, a: h - line, "team_total_away": lambda h, a: a - line}[market]
        return self._line(fn)

    def median(self, market: str) -> float:
        """Median of the market's quantity (home margin for spreads; reversed sign gives the home handicap)."""
        q = {"spread": lambda h, a: h - a, "total": lambda h, a: h + a,
             "team_total_home": lambda h, a: h, "team_total_away": lambda h, a: a}[market]
        acc = 0.0
        for v, p in sorted(((q(h, a), p) for (h, a), p in self.pmf.items())):
            acc += p
            if acc >= 0.5:
                return float(v)
        return float("nan")


def poisson_grid(lam_home: float, lam_away: float, ot_home_share: float, max_goals: int = 15) -> ScoreGrid:
    """NHL: independent Poisson regulation goals; a regulation tie k-k becomes k+1-k (home wins OT/SO) with probability
    `ot_home_share`, else k-k+1. The shootout winner's credited goal is part of the final score, as settled."""
    def pois(lam):
        out, p = [], math.exp(-lam)
        for k in range(max_goals + 1):
            out.append(p)
            p *= lam / (k + 1)
        return out
    ph, pa = pois(lam_home), pois(lam_away)
    pmf: dict[tuple[int, int], float] = {}
    reg = [0.0, 0.0, 0.0]
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            p = ph[h] * pa[a]
            if h == a:
                reg[1] += p
                pmf[(h + 1, a)] = pmf.get((h + 1, a), 0.0) + p * ot_home_share
                pmf[(h, a + 1)] = pmf.get((h, a + 1), 0.0) + p * (1 - ot_home_share)
            else:
                reg[0 if h > a else 2] += p
                pmf[(h, a)] = pmf.get((h, a), 0.0) + p
    s = sum(reg)
    return ScoreGrid(pmf, reg3=(reg[0] / s, reg[1] / s, reg[2] / s),
                     params={"lambda_home": round(lam_home, 6), "lambda_away": round(lam_away, 6),
                             "ot_home_share": round(ot_home_share, 6)})


class MarginTotalNormal:
    """NBA: final margin M and total T as independent discretised Normals (±0.5 continuity correction). A zero margin is
    impossible in a final score (overtime), so its mass is removed from M and the rest renormalised. Team totals use
    H = (T + M) / 2 and A = (T − M) / 2, Normal with sd = sqrt(sd_T² + sd_M²) / 2."""

    def __init__(self, margin: float, sd_margin: float, total: float, sd_total: float):
        if sd_margin <= 0 or sd_total <= 0:
            raise ValueError("Standard deviations must be positive")
        self.m, self.sm, self.t, self.st = margin, sd_margin, total, sd_total
        self.params = {"margin": round(margin, 4), "sd_margin": round(sd_margin, 4), "total": round(total, 4),
                       "sd_total": round(sd_total, 4)}
        self._p0 = self._cdf_m(0.5) - self._cdf_m(-0.5)

    def _cdf_m(self, x: float) -> float:
        return _norm_cdf((x - self.m) / self.sm)

    def _p_margin_above(self, x: float) -> float:
        """P(M > x) over integer margins without zero, x on a half/whole point grid."""
        k = math.floor(x) + 1                       # smallest integer strictly above x
        above = 1 - self._cdf_m(k - 0.5)
        if k <= 0:
            above -= self._p0
        return above / (1 - self._p0)

    def _p_margin_eq(self, k: float) -> float:
        if k != int(k) or k == 0:
            return 0.0
        return (self._cdf_m(k + 0.5) - self._cdf_m(k - 0.5)) / (1 - self._p0)

    @staticmethod
    def _discrete(mean: float, sd: float, line: float) -> tuple[float, float, float]:
        if line == int(line):
            above = 1 - _norm_cdf((line + 0.5 - mean) / sd)
            eq = _norm_cdf((line + 0.5 - mean) / sd) - _norm_cdf((line - 0.5 - mean) / sd)
        else:
            above, eq = 1 - _norm_cdf((line - mean) / sd), 0.0
        return _clean(above, eq, 1 - above - eq)

    def winner(self) -> tuple[float, float, float]:
        pa = self._p_margin_above(0)
        return _clean(pa, 0.0, 1 - pa)

    def market(self, market: str, line: Optional[float] = None) -> tuple[float, float, float]:
        if market == "winner":
            return self.winner()
        if market == "spread":
            # home covers when M + line > 0, i.e. M > -line; push when M == -line
            pa, pmid = self._p_margin_above(-line), self._p_margin_eq(-line)
            return _clean(pa, pmid, 1 - pa - pmid)
        if market == "total":
            return self._discrete(self.t, self.st, line)
        sd = math.sqrt(self.st ** 2 + self.sm ** 2) / 2
        mean = (self.t + self.m) / 2 if market == "team_total_home" else (self.t - self.m) / 2
        if market in ("team_total_home", "team_total_away"):
            return self._discrete(mean, sd, line)
        raise ValueError(f"Market {market!r} is not available from this NBA distribution")

    def median(self, market: str) -> float:
        return {"spread": self.m, "total": self.t, "team_total_home": (self.t + self.m) / 2,
                "team_total_away": (self.t - self.m) / 2}[market]


def model_line(dist, market: str) -> float:
    """A model-only line: the half-point line (x.5, so it can never push) at which the model's two sides are closest to
    50/50. Changed 2026-10-02: the earlier rule (median rounded down + 0.5) put low-scoring team totals at 3.5 goals with
    'under' near 70%, so the pick reflected where the line was placed, not the game (docs/MARKETS.md).
    Spread lines are the home handicap."""
    med = dist.median(market)
    centre = math.floor(-med if market == "spread" else med) + 0.5
    candidates = [centre + k for k in range(-3, 4)]
    return min(candidates, key=lambda line: (abs(dist.market(market, line)[0] - 0.5), abs(line - centre)))


def check_consistency(dist, market: str, lines: list[float]) -> None:
    """Monotonicity across lines: P(over L) non-increasing; P(home covers L) non-decreasing; sums equal 1."""
    prev = None
    for line in sorted(lines):
        pa, pmid, pb = dist.market(market, line)
        if abs(pa + pmid + pb - 1) > SUM_TOLERANCE:
            raise ValueError(f"{market} {line}: probabilities do not sum to 1")
        if prev is not None:
            if market == "spread" and pa < prev - 1e-12:
                raise ValueError(f"spread: P(home covers) fell as the line rose to {line}")
            if market != "spread" and pa > prev + 1e-12:
                raise ValueError(f"{market}: P(over) rose as the line rose to {line}")
        prev = pa


# --- What the app says next to each market (docs/NHL_BASELINE_REPORT.md, docs/EVALUATION_PLAN.md NBA research) ----------
LIMITATIONS = {
    "nhl": ["Goalie listings are collected, but this model does not yet use goalies, injuries or line-ups."],
    "nba": ["No injury or line-up information is used."],
    ("nhl", "reg3"): "Regulation draws were under-predicted in the one-time test (about 16% predicted, 23% observed).",
    ("nhl", "spread"): "Two-goal wins were under-predicted in testing (empty-net goals are not modelled).",
    ("nhl", "total"): "Totals showed no skill over a league-average model in the one-time test.",
    ("nhl", "team_total_home"): "Team totals were not tested separately.",
    ("nhl", "team_total_away"): "Team totals were not tested separately.",
    ("nba", "spread"): "Development research only (2023-24 holdout); no independent test yet.",
    ("nba", "total"): "Development research only (2023-24 holdout); no independent test yet.",
    ("nba", "team_total_home"): "Development research only; team totals were not tested separately.",
    ("nba", "team_total_away"): "Development research only; team totals were not tested separately.",
}
MODEL_LINE_NOTE = "Model-only line: no bookmaker line had been retrieved when this forecast was made."


def limitations(sport: str, market: str, line_source: Optional[str]) -> list[str]:
    out = list(LIMITATIONS.get(sport, []))
    if (sport, market) in LIMITATIONS:
        out.append(LIMITATIONS[(sport, market)])
    if line_source == "model":
        out.append(MODEL_LINE_NOTE)
    return out


def side_labels(market: str, line: Optional[float], home: str, away: str) -> tuple[str, str, str]:
    """Plain labels for sides a / mid / b, e.g. ('BOS -1.5', 'Push', 'CHI +1.5')."""
    def num(x: float) -> str:
        return f"{x:+g}" if x else "0"
    if market == "winner":
        return f"{home} win", "", f"{away} win"
    if market == "reg3":
        return f"{home} in regulation", "Tied after regulation", f"{away} in regulation"
    if market == "spread":
        return f"{home} {num(line)}", "Push", f"{away} {num(-line)}"
    if market == "total":
        return f"Over {line:g}", "Push", f"Under {line:g}"
    team = home if market == "team_total_home" else away
    return f"{team} over {line:g}", "Push", f"{team} under {line:g}"
