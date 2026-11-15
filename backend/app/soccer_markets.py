"""Soccer goal, corner, double-chance and draw-no-bet markets: exact settlement and probabilities (pure functions, no I/O).

Every probability comes from ONE stored forecast:
- goal and corner markets from that family's joint grid of the 90-minute counts (`joint[h][a]`, the last cell of each axis
  = that count or more; app/soccer_markets_eval.py builds it, soccer_market_forecasts stores it);
- double chance and draw no bet from the tested 90-minute 1X2 forecast (p_home, p_draw, p_away) only.
A corner market never accepts a 1X2 forecast, and no market mixes two forecasts.

Settlement (docs/SOCCER_MARKETS_PLAN.md; protocol `settlement`): 90 minutes plus stoppage time. A half line wins or loses;
a whole line pushes on equality; a quarter line splits the stake over the two neighbouring half-steps (x.25 = x.0 and x.5,
x.75 = x.5 and x+1.0), so a leg can win, half win, push, half lose or lose. A handicap adds the side's own line to its
count and compares with the other side's. Draw no bet pushes on a draw. Per unit stake at decimal odds d a leg returns
d, (d + 1) / 2, 1, 1/2 or 0 (void: 1).
"""
from __future__ import annotations

from typing import Optional, Sequence

RESULTS = ("win", "half_win", "push", "half_loss", "loss")
FAMILY_OF = {"goals_total": "goals", "goals_home": "goals", "goals_away": "goals", "btts": "goals",
             "corners_total": "corners", "corners_home": "corners", "corners_away": "corners", "corners_handicap": "corners",
             "double_chance": "1x2", "draw_no_bet": "1x2"}
COUNT_MARKETS = tuple(m for m, f in FAMILY_OF.items() if f in ("goals", "corners"))
SIDES = {"goals_total": ("over", "under"), "goals_home": ("over", "under"), "goals_away": ("over", "under"),
         "btts": ("yes", "no"), "corners_total": ("over", "under"), "corners_home": ("over", "under"),
         "corners_away": ("over", "under"), "corners_handicap": ("home", "away"),
         "double_chance": ("home_draw", "draw_away", "home_away"), "draw_no_bet": ("home", "away")}
LINED = {"goals_total", "goals_home", "goals_away", "corners_total", "corners_home", "corners_away", "corners_handicap"}
TITLES = {"goals_total": "Match goals", "goals_home": "Home team goals", "goals_away": "Away team goals",
          "btts": "Both teams to score", "corners_total": "Match corners", "corners_home": "Home team corners",
          "corners_away": "Away team corners", "corners_handicap": "Corner handicap", "double_chance": "Double chance",
          "draw_no_bet": "Draw no bet"}
# Lines searched for a model's fair line (quarter steps); the handicap range is the home side's line.
FAIR_RANGE = {"goals_total": (0.5, 6.5), "goals_home": (0.5, 4.5), "goals_away": (0.5, 4.5),
              "corners_total": (4.5, 16.5), "corners_home": (0.5, 10.5), "corners_away": (0.5, 10.5),
              "corners_handicap": (-6.5, 6.5)}


def _check_line(line: float) -> None:
    if line is None or abs(line * 4 - round(line * 4)) > 1e-9:
        raise ValueError(f"a line must be a multiple of 0.25, got {line!r}")


def _single(margin: float, line: float) -> str:
    """Result of a whole or half line given margin = side count + line - other (or count - line for over)."""
    if margin > 1e-9:
        return "win"
    if margin < -1e-9:
        return "loss"
    return "push"                           # only reachable on a whole line


def _combine(a: str, b: str) -> str:
    pair = {a, b}
    if pair == {"win"}:
        return "win"
    if pair == {"loss"}:
        return "loss"
    if pair == {"push"}:
        return "push"
    if pair == {"win", "push"}:
        return "half_win"
    if pair == {"loss", "push"}:
        return "half_loss"
    raise ValueError(f"neighbouring half-steps cannot settle as {a} and {b}")


def _grade_margin(base: float, line: float) -> str:
    """`base` = the side's margin before the line (count for over, -count for under, own - other for a handicap)."""
    _check_line(line)
    frac = (line * 4) % 2
    if abs(frac - 1) < 1e-9:                # quarter line: split over line - 0.25 and line + 0.25
        return _combine(_single(base + line - 0.25, line - 0.25), _single(base + line + 0.25, line + 0.25))
    return _single(base + line, line)


def grade_total(count: int, line: float, side: str) -> str:
    """Over/under `line` for a count (match total or one team's count)."""
    if side == "over":
        return _grade_over(count, line)
    if side == "under":
        return _grade_under(count, line)
    raise ValueError(f"side must be over or under, got {side!r}")


def _grade_over(count: int, line: float) -> str:
    _check_line(line)
    return _flip_line(lambda ln: _single(count - ln, ln), line)


def _grade_under(count: int, line: float) -> str:
    _check_line(line)
    return _flip_line(lambda ln: _single(ln - count, ln), line)


def _flip_line(single, line: float) -> str:
    if abs((line * 4) % 2 - 1) < 1e-9:
        return _combine(single(line - 0.25), single(line + 0.25))
    return single(line)


def grade_handicap(home: int, away: int, line: float, side: str) -> str:
    """The side's count plus its own `line` against the other side's count."""
    if side == "home":
        return _grade_margin(home - away, line)
    if side == "away":
        return _grade_margin(away - home, line)
    raise ValueError(f"side must be home or away, got {side!r}")


def grade_market(market: str, side: str, line: Optional[float], counts: Sequence[int]) -> str:
    """Settles a goal or corner market on the family's 90-minute counts (home, away)."""
    if market not in COUNT_MARKETS:
        raise ValueError(f"{market!r} is not a goal or corner market")
    if side not in SIDES[market]:
        raise ValueError(f"{market} has no side {side!r}")
    h, a = int(counts[0]), int(counts[1])
    kind = market.split("_", 1)[1] if market != "btts" else "btts"
    if kind == "btts":
        both = h > 0 and a > 0
        return "win" if both == (side == "yes") else "loss"
    if kind == "handicap":
        return grade_handicap(h, a, line, side)
    count = {"total": h + a, "home": h, "away": a}[kind]
    return grade_total(count, line, side)


def grade_1x2_market(score: Sequence[int], market: str, side: str) -> str:
    """Double chance / draw no bet on the 90-minute score."""
    h, a = int(score[0]), int(score[1])
    outcome = "home" if h > a else ("draw" if h == a else "away")
    if market == "double_chance":
        if side not in SIDES[market]:
            raise ValueError(f"double_chance has no side {side!r}")
        return "win" if outcome in side.split("_") else "loss"
    if market == "draw_no_bet":
        if side not in SIDES[market]:
            raise ValueError(f"draw_no_bet has no side {side!r}")
        return "push" if outcome == "draw" else ("win" if outcome == side else "loss")
    raise ValueError(f"{market!r} is not a 1X2-derived market")


def return_factor(grade: str, decimal: float) -> float:
    """Return per unit stake at decimal odds `decimal`."""
    return {"win": decimal, "half_win": (decimal + 1) / 2, "push": 1.0, "void": 1.0, "half_loss": 0.5,
            "loss": 0.0}[grade]


def _empty() -> dict:
    return {r: 0.0 for r in RESULTS}


def probabilities(joint, market: str, side: str, line: Optional[float]) -> dict:
    """{win, half_win, push, half_loss, loss} probabilities of one selection under a joint count grid."""
    if market not in COUNT_MARKETS:
        raise ValueError(f"{market!r} is not a goal or corner market (a 1X2 forecast is never used here)")
    if not joint or not hasattr(joint[0], "__len__"):
        raise ValueError("a goal or corner market needs a joint count grid")
    if market in LINED:
        _check_line(line)
    out = _empty()
    for h, row in enumerate(joint):
        for a, p in enumerate(row):
            if p:
                out[grade_market(market, side, line, (h, a))] += float(p)
    total = sum(out.values())
    return {k: v / total for k, v in out.items()} if total > 0 else out


def probabilities_1x2(p: Sequence[float], market: str, side: str) -> dict:
    """Double chance / draw no bet from (p_home, p_draw, p_away)."""
    if market not in ("double_chance", "draw_no_bet"):
        raise ValueError(f"{market!r} cannot be derived from a 1X2 forecast")
    ph, pd, pa = (float(x) for x in p)
    out = _empty()
    for score, q in (((1, 0), ph), ((0, 0), pd), ((0, 1), pa)):
        out[grade_1x2_market(score, market, side)] += q
    return out


def win_share(probs: dict) -> float:
    """Probability-weighted share of the stake that wins (a half win counts half)."""
    return probs["win"] + probs["half_win"] / 2


def fair_decimal(probs: dict) -> Optional[float]:
    """Decimal odds at which the selection's expected return is exactly the stake; None if it cannot win."""
    gain = probs["win"] + probs["half_win"] / 2
    lose = probs["loss"] + probs["half_loss"] / 2
    if gain <= 0:
        return None
    return 1 + lose / gain


def expected_return(probs: dict, decimal: float) -> float:
    """Expected return per unit stake at decimal odds (1.0 = break-even)."""
    return sum(probs[r] * return_factor(r, decimal) for r in RESULTS)


def fair_line(joint, market: str) -> float:
    """The quarter-step line (home line for the handicap) where the two sides' fair odds are closest; ties: lower line."""
    lo, hi = FAIR_RANGE[market]
    sides = SIDES[market]
    best, best_gap = None, None
    steps = int(round((hi - lo) * 4))
    for i in range(steps + 1):
        ln = lo + i * 0.25
        other = -ln if market == "corners_handicap" else ln
        d1 = fair_decimal(probabilities(joint, market, sides[0], ln))
        d2 = fair_decimal(probabilities(joint, market, sides[1], other))
        if d1 is None or d2 is None:
            continue
        gap = abs(d1 - d2)
        if best_gap is None or gap < best_gap - 1e-12:
            best, best_gap = ln, gap
    return best
