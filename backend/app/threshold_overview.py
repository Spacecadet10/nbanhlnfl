"""Confidence thresholds per sport and market: does each threshold keep its word, and is any threshold better than taking
every pick? Read-only; nothing here changes a forecast, a pick or a model.

Two kinds of evidence, never mixed:

- **Live** (2026-27 onward): the canonical record of the market (one forecast per game, made within two hours of the
  start), cumulative per threshold of the market's reporting grid (coverage.threshold_grid). Each row: settled record,
  win rate with its Wilson 95% interval, the mean probability the model stated for those picks, and, on the games with
  the bookmaker quote the forecast could see, the market's de-vigged probability for the same picked side.
- **History**: the saved walk-forward out-of-sample forecasts of the market's canonical primary model
  (combination_thresholds, candidate pool), earlier seasons and later seasons apart. Only markets without a line (winner,
  regulation three-way): line markets were settled at the model's own line there (no historical bookmaker lines), so
  their history says nothing about picks made at the bookmaker's line.

Rule for "best threshold" (declared 2026-11-01, before this view computed any per-threshold verdict):
1. Only history can choose, and only its earlier seasons. Candidates: grid thresholds with at least MIN_HISTORY_PICKS
   settled earlier picks.
2. Best = the candidate with the highest Wilson 95% lower bound on its win rate (the most reliable win rate); ties go to
   the lower threshold (more picks).
3. "Better than all picks" (earlier): that lower bound exceeds the all-picks win rate of the same seasons.
4. "Held up" (later seasons, not used to choose): at least MIN_LATER_PICKS settled later picks at that threshold and its
   later Wilson lower bound exceeds the later all-picks win rate.
5. Live rows are never used to choose. A live row is judged only with at least MIN_LIVE_GRADED settled picks.
A higher win rate is not a better bet: a 75%+ pick is usually priced near 75% by the bookmaker. The live market column
is the comparison that bears on value; history has no bookmaker prices.
"""
import math
from typing import Callable, Optional

MIN_HISTORY_PICKS = 200
MIN_LATER_PICKS = 100
MIN_LIVE_GRADED = 30
STRONG_LOWER = 0.5                       # a pick is shown as a best pick only above this lower bound
# Markets whose picks have shown no skill against the bookmaker's line (decided 2026-11-01 from the bookmaker-line
# replays, 2021-24: NBA spread/total picks won 49-51% at the line and every model was worse than the closing line in
# NBA and NHL spread/total; team totals have no bookmaker history at all). Their estimates are shown for information,
# never as a best pick.
NO_PROVEN_SKILL = ("spread", "total", "team_total_home", "team_total_away")
SKILL_NOTE = ("No proven skill against the bookmaker's line: at the line, past picks in this market won about half the "
              "time. Shown for information, not as a pick.")
BREAK_EVEN_STANDARD = 110 / 210          # a -110 / -110 two-way line: the win rate needed to break even (52.4%)
HISTORY_MARKETS = ("winner", "reg3")
PRIMARY = {("nba", "winner"): "elo", ("nhl", "winner"): "hockey_elo", ("nhl", "reg3"): "goals_poisson"}
EPS = 1e-9

RULE = ("Best threshold = the threshold with the highest Wilson 95% lower bound on its win rate in the earlier historical "
        f"seasons, among thresholds with at least {MIN_HISTORY_PICKS} settled picks (ties: the lower threshold). It counts as "
        "better than all picks when that lower bound is above the all-picks win rate, and as held up when the later seasons "
        f"(not used to choose) give at least {MIN_LATER_PICKS} settled picks with a lower bound above their all-picks win "
        f"rate. Live results never choose; a live row is judged only from {MIN_LIVE_GRADED} settled picks. Line markets have no "
        "comparable history (settled at the model's own line), so no threshold is chosen for them.")


def wilson(w: int, n: int, z: float = 1.959964) -> Optional[list]:
    if n <= 0:
        return None
    p = w / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [max(0.0, c - h), min(1.0, c + h)]


def calibration_state(rate: Optional[float], ci: Optional[list], stated: Optional[float], n: int, min_n: int) -> str:
    """'too_few' below min_n settled picks; otherwise 'overconfident' when the interval lies below the stated probability,
    'underconfident' when it lies above, else 'holds'."""
    if n < min_n or rate is None or ci is None or stated is None:
        return "too_few"
    if ci[1] < stated:
        return "overconfident"
    if ci[0] > stated:
        return "underconfident"
    return "holds"


def _picked(market: str, vec_or_a, side: str) -> Optional[float]:
    """The market's probability for the picked side: two-way from P(a), three-way from the vector."""
    if vec_or_a is None:
        return None
    if market == "reg3":
        return vec_or_a[("a", "mid", "b").index(side)]
    return vec_or_a if side == "a" else 1 - vec_or_a


def live_rows(items: list, market: str, grid, quote_for: Callable) -> list:
    """Cumulative rows per threshold for the canonical cohort `items` [(game, canonical)]. quote_for(game, canonical) ->
    the market's de-vigged P(a) (three-way: vector) from a quote the forecast could see, or None."""
    graded = []
    for g, c in items:
        if c["state"] != "forecast":
            continue
        rec = c["record"]
        q = quote_for(g, c) if c["grade"] in ("win", "loss") else None
        graded.append((rec["confidence"], c["grade"], _picked(market, q, rec["side"])))
    out = []
    for t in grid:
        sel = [x for x in graded if x[0] >= t - EPS]
        dec = [x for x in sel if x[1] in ("win", "loss")]
        w = sum(1 for x in dec if x[1] == "win")
        n = len(dec)
        rate = w / n if n else None
        ci = wilson(w, n)
        stated = sum(x[0] for x in dec) / n if n else None
        matched = [x for x in dec if x[2] is not None]
        mw = sum(1 for x in matched if x[1] == "win")
        out.append({
            "threshold": t, "forecasts": len(sel), "graded": n, "win": w, "loss": n - w,
            "push": sum(1 for x in sel if x[1] == "push"),
            "unsettled": sum(1 for x in sel if x[1] not in ("win", "loss", "push")),
            "win_rate": rate, "ci95": ci, "stated": stated,
            "calibration": calibration_state(rate, ci, stated, n, MIN_LIVE_GRADED),
            "market_matched": len(matched),
            "market_win_rate": mw / len(matched) if matched else None,
            "market_implied": sum(x[2] for x in matched) / len(matched) if matched else None,
            "break_even": None if market in HISTORY_MARKETS else _versus(ci, n, BREAK_EVEN_STANDARD),
        })
    return out


def _versus(ci: Optional[list], n: int, ref: float) -> str:
    if n < MIN_LIVE_GRADED or ci is None:
        return "too_few"
    return "above" if ci[0] > ref else "below" if ci[1] < ref else "undecided"


def _hist_row(r: dict, grid) -> Optional[dict]:
    if not any(abs(r["lo"] - t) < EPS for t in grid):
        return None
    n = r["wins"] + r["losses"]
    return {"threshold": r["lo"], "graded": n, "win": r["wins"], "loss": r["losses"], "coverage": r["coverage"],
            "win_rate": r["win_rate"], "ci95": wilson(r["wins"], n), "stated": r["mean_p"],
            "calibration": calibration_state(r["win_rate"], wilson(r["wins"], n), r["mean_p"], n, MIN_HISTORY_PICKS)}


def choose(earlier: list, later: list) -> dict:
    """The declared rule (module docstring) on history rows [{threshold, graded, win_rate, ci95}], lowest threshold first
    (= all picks)."""
    cands = [r for r in earlier if r["graded"] >= MIN_HISTORY_PICKS and r["ci95"] is not None]
    if not earlier or not cands:
        return {"best": None, "better_than_all": False, "held_up": None,
                "reason": f"no threshold has {MIN_HISTORY_PICKS} settled earlier picks"}
    best = max(cands, key=lambda r: (round(r["ci95"][0], 12), -r["threshold"]))
    base = earlier[0]
    better = best["threshold"] > base["threshold"] + EPS and best["ci95"][0] > (base["win_rate"] or 0)
    lat = next((r for r in later if abs(r["threshold"] - best["threshold"]) < EPS), None)
    lbase = later[0] if later else None
    if lat is None or lat["graded"] < MIN_LATER_PICKS or lbase is None or lbase["win_rate"] is None:
        held = None
    else:
        held = lat["ci95"][0] > lbase["win_rate"]
    return {"best": best["threshold"], "better_than_all": better, "held_up": held, "reason": None}


def history(sport: str, market: str, grid) -> dict:
    """The canonical primary's saved out-of-sample ladder, earlier and later seasons, on the reporting grid."""
    if market not in HISTORY_MARKETS:
        return {"status": "not_comparable", "model": None, "earlier": None, "later": None,
                "reason": "History settled this market at the model's own line (no historical bookmaker lines), so it says "
                          "nothing about picks made at the bookmaker's line."}
    model = PRIMARY.get((sport, market))
    from . import combination_thresholds as ct
    try:
        t, models, _, _, state = ct._table(sport, market, "pool", ct._stamp(sport, "pool"))
    except Exception as exc:      # a missing or unreadable evidence file must not take the live view down with it
        return {"status": "unavailable", "model": model, "earlier": None, "later": None, "reason": str(exc)[:200]}
    if state or model not in models:
        return {"status": "unavailable", "model": model, "earlier": None, "later": None,
                "reason": state[1] if state else f"{model} has no saved forecasts"}
    ladder = ct.thresholds(t, [model])["sources"][0]
    out = {"status": "available", "model": model, "reason": None}
    for period in ("earlier", "later"):
        rows = [x for x in (_hist_row(r, grid) for r in ladder[period]["cumulative"]) if x is not None]
        span = ct._period_span(t, t.periods[period])
        out[period] = {"seasons": span["seasons"], "date_from": span["date_from"], "date_to": span["date_to"], "rows": rows}
    return out


def market_block(sport: str, market: str, title: str, items: list, grid, quote_for: Callable) -> dict:
    live = live_rows(items, market, grid, quote_for)
    hist = history(sport, market, grid)
    verdict = (choose(hist["earlier"]["rows"], hist["later"]["rows"]) if hist["status"] == "available"
               else {"best": None, "better_than_all": False, "held_up": None, "reason": hist["reason"]})
    return {"market": market, "title": title, "grid": list(grid), "live": live, "history": hist, "verdict": verdict,
            "skill_proven": market not in NO_PROVEN_SKILL, "skill_note": None if market not in NO_PROVEN_SKILL else SKILL_NOTE}


# --- Ranked picks of one date -----------------------------------------------------------------------------------------
RANK_RULE = ("Picks are ranked by the lowest win rate the past record supports for picks like them: the Wilson 95% lower "
             "bound of the past win rate of the same market's picks in the same probability band (from the threshold the "
             f"pick reaches to the next one), or, when that band has fewer than {MIN_LIVE_GRADED} settled picks, of all "
             "picks at the highest threshold it reaches that has enough. Winner and regulation three-way use "
             "the primary model's saved walk-forward history (past seasons); line markets use this season's canonical picks "
             "settled before the date (their history was settled at the model's own line). Ties: the higher model "
             "probability. Picks without enough past evidence are listed but not ranked. A rank is about how often picks "
             "like this have won, not about value: compare with the market's probability for the same pick. A pick is shown as a "
             "best pick only when that lower bound is above 50% (picks like it have won more often than not) and its market has "
             "shown skill against the bookmaker's line (spread, total and team-total estimates have not).")


def threshold_reached(conf: float, grid) -> Optional[float]:
    """The highest level of the grid the pick's probability reaches (None below the grid)."""
    hit = [t for t in grid if conf >= t - EPS]
    return max(hit) if hit else None


def evidence_ladder(sport: str, market: str, grid, prior: list) -> dict:
    """Cumulative evidence rows {threshold: (wins, settled)} for scoring picks: the primary's history (all saved seasons)
    where it is comparable, else this season's canonical picks in `prior` [(game, canonical)] (settled before the date)."""
    h = history(sport, market, grid)
    if h["status"] == "available":
        rows = {}
        for period in ("earlier", "later"):
            for r in h[period]["rows"]:
                w, n = rows.get(r["threshold"], (0, 0))
                rows[r["threshold"]] = (w + r["win"], n + r["graded"])
        seasons = (h["earlier"]["seasons"] or []) + (h["later"]["seasons"] or [])
        return {"source": "history", "model": h["model"], "seasons": seasons, "rows": rows}
    rows = {}
    for r in live_rows(prior, market, grid, lambda g, c: None):
        rows[r["threshold"]] = (r["win"], r["graded"])
    return {"source": "season", "model": None, "seasons": [], "rows": rows}


def pick_evidence(conf: float, ladder: dict, grid) -> Optional[dict]:
    """The most specific past evidence for a pick: its own band of the grid (from the threshold it reaches up to the next
    level) when that band has at least MIN_LIVE_GRADED settled picks; else the highest threshold it reaches whose
    cumulative row (that level or higher) has enough. {lo, hi (None = open top), win_rate, settled, lower, source} or None."""
    levels = sorted(grid)
    t = threshold_reached(conf, levels)
    if t is None:
        return None
    rows = ladder["rows"]
    nxt = next((x for x in levels if x > t + EPS), None)
    w, n = rows.get(t, (0, 0))
    if nxt is not None:
        w2, n2 = rows.get(nxt, (0, 0))
        w, n = w - w2, n - n2
    if n >= MIN_LIVE_GRADED:
        return {"lo": t, "hi": nxt, "win_rate": w / n, "settled": n, "lower": wilson(w, n)[0], "source": ladder["source"]}
    for lo in sorted((x for x in levels if x <= t + EPS), reverse=True):
        w, n = rows.get(lo, (0, 0))
        if n >= MIN_LIVE_GRADED:
            return {"lo": lo, "hi": None, "win_rate": w / n, "settled": n, "lower": wilson(w, n)[0], "source": ladder["source"]}
    return None


def rank(picks: list) -> list:
    """Adds day_rank and game_rank (1 = best) to picks with evidence; picks without evidence get None. Sorted: ranked first."""
    key = lambda p: (-p["evidence"]["lower"], -p["probability"])          # noqa: E731
    ranked = sorted([p for p in picks if p["evidence"]], key=key)
    unranked = sorted([p for p in picks if not p["evidence"]], key=lambda p: -p["probability"])
    for i, p in enumerate(ranked, 1):
        p["day_rank"] = i
    for p in picks:      # a "strong" pick: picks like it have won more than half the time, at 95% confidence, in a
        p["skill_proven"] = p["market"] not in NO_PROVEN_SKILL           # market with skill against the line
        p["strong"] = p["skill_proven"] and bool(p["evidence"]) and p["evidence"]["lower"] > STRONG_LOWER
    by_game: dict = {}
    for p in ranked:
        by_game.setdefault(p["game_id"], []).append(p)
    for game_picks in by_game.values():
        for i, p in enumerate(game_picks, 1):
            p["game_rank"] = i
            p["game_ranked"] = len(game_picks)
    for p in unranked:
        p["day_rank"] = p["game_rank"] = None
        p["game_ranked"] = len(by_game.get(p["game_id"], []))
    return ranked + unranked
