"""Adaptive rule selection, per sport and market (docs/MODEL_SELECTION.md §6). Research / shadow only.

The pure part (`rules`, `evaluate`, `decide`, `apply`) is shared by the retrospective simulation and the live selector, so
both use the same arithmetic. An *item* is one settled or unsettled game-market cohort:
  {"game_id", "week", "vectors": {source: (p_a, p_mid, p_b)}, "agreement": {"majority_side", "unanimous_side"},
   "bookmaker": (p_a, p_mid, p_b) or None, "realised": 0/1/2 or None}
Only items whose result was available before the decision's evidence cutoff may be passed to `evaluate`/`decide`.
"""
import hashlib
import json
import math
from pathlib import Path
from statistics import NormalDist
from typing import Optional, Sequence

from . import combine

SELECTOR_NAME = "selector-v1"
THRESHOLDS = {"default": (0.50, 0.55, 0.60, 0.65, 0.70, 0.75), "reg3": (0.40, 0.45, 0.50, 0.55, 0.60)}
FILTERS = ("none", "majority", "unanimous")
MIN_PICKS = 100
MIN_COVERAGE = 0.10
MIN_WEEKS = 3
FAMILY_ALPHA = 0.05
HYSTERESIS = 0.01
DECISION_WEEKDAY = 0          # Monday
DECISION_HOUR_UTC = 14


def version() -> str:
    """Selector identity: this file's code and its registered constants."""
    return f"{SELECTOR_NAME}:{hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]}"


def thresholds(market: str) -> tuple[float, ...]:
    return THRESHOLDS.get(market, THRESHOLDS["default"])


def rules(market: str, sources: Sequence[str]) -> list[dict]:
    """Registry order is the final tie-break: primary first, then members, average, stack."""
    return [{"source": s, "filter": f, "threshold": t} for s in sources for f in FILTERS for t in thresholds(market)]


def z_for(n_rules: int) -> float:
    return NormalDist().inv_cdf(1 - FAMILY_ALPHA / max(1, n_rules))


def passes(rule: dict, item: dict, market: str) -> tuple[bool, str, Optional[int], Optional[float]]:
    """(passes, reason, picked index, probability of the pick) for one item under one rule. Uses no outcome."""
    vec = item["vectors"].get(rule["source"])
    if vec is None:
        return False, f"source {rule['source']} unavailable", None, None
    side = combine.pick(vec, market)
    if side is None:
        return False, "source probabilities tie: no pick", None, None
    p = vec[side]
    if rule["filter"] != "none":
        agreed = item["agreement"].get(f"{rule['filter']}_side")
        if agreed is None:
            return False, f"no {rule['filter']} agreement", side, p
        if combine.SIDES.index(agreed) != side:
            return False, f"{rule['filter']} agreement is on the other side", side, p
    if p + 1e-9 < rule["threshold"]:
        return False, f"probability {p:.3f} below threshold {rule['threshold']:.2f}", side, p
    return True, "passes", side, p


def outcome(market: str, side: int, realised: int) -> str:
    if realised == side:
        return "win"
    if realised == 1 and market not in combine.THREE_WAY:
        return "push"
    return "loss"


def reference_rates(items: Sequence[dict], market: str) -> dict:
    """Frequency of each outcome class over all settled items (two-way markets: among non-push results)."""
    allowed = combine.sides(market)
    settled = [i["realised"] for i in items if i["realised"] in allowed]
    n = len(settled)
    return {k: (sum(r == k for r in settled) / n if n else None) for k in allowed}


def reference(item: dict, side: int, rates: dict, market: str) -> Optional[float]:
    book = item.get("bookmaker")
    if book is not None:
        allowed = combine.sides(market)
        return book[side] / sum(book[k] for k in allowed)
    return rates.get(side)


def evaluate(items: Sequence[dict], market: str, sources: Sequence[str]) -> list[dict]:
    """Per registered rule: picks, record, coverage, lift over the reference and its Bonferroni lower bound."""
    reg = rules(market, sources)
    z = z_for(len(reg))
    rates = reference_rates(items, market)
    total = len(items)
    # Each source's pick and probability once per item (identical to `passes`, which is used for single items).
    cache = {}
    for s in sources:
        col = []
        for item in items:
            vec = item["vectors"].get(s)
            side = None if vec is None else combine.pick(vec, market)
            col.append((side, None if side is None else vec[side]))
        cache[s] = col
    out = []
    for rule in reg:
        wins = losses = pushes = 0
        excess, sq, weeks = 0.0, 0.0, set()
        col = cache[rule["source"]]
        key = None if rule["filter"] == "none" else f"{rule['filter']}_side"
        for idx, item in enumerate(items):
            side, p = col[idx]
            if side is None or p + 1e-9 < rule["threshold"]:
                continue
            if key is not None:
                agreed = item["agreement"].get(key)
                if agreed is None or combine.SIDES.index(agreed) != side:
                    continue
            res = outcome(market, side, item["realised"])
            if res == "push":
                pushes += 1
                continue
            ref = reference(item, side, rates, market)
            if ref is None:
                continue
            y = 1.0 if res == "win" else 0.0
            wins += res == "win"
            losses += res == "loss"
            excess += y - ref
            sq += (y - ref) ** 2
            weeks.add(item["week"])
        n = wins + losses
        lift = excess / n if n else None
        se = math.sqrt(max(0.0, sq / n - lift ** 2) / n) if n > 1 else None
        lower = lift - z * se if se is not None else None
        coverage = (n + pushes) / total if total else None
        eligible = n >= MIN_PICKS and (coverage or 0) >= MIN_COVERAGE and len(weeks) >= MIN_WEEKS
        out.append({**rule, "picks": n, "wins": wins, "losses": losses, "pushes": pushes, "coverage": coverage,
                    "weeks": len(weeks), "accuracy": wins / n if n else None, "lift": lift, "se": se, "lower": lower,
                    "z": z, "eligible": eligible})
    return out


def _key(rule: dict) -> tuple:
    return rule["source"], rule["filter"], rule["threshold"]


def decide(items: Sequence[dict], market: str, sources: Sequence[str], incumbent: Optional[dict] = None) -> dict:
    """The rule in force for the next interval, or abstain / insufficient evidence. Deterministic for given items."""
    stats = evaluate(items, market, sources)
    settled = len(items)
    passing = [s for s in stats if s["eligible"] and s["lower"] is not None and s["lower"] > 0]
    order = {_key(s): i for i, s in enumerate(stats)}
    base = {"settled_items": settled, "rules": len(stats), "z": stats[0]["z"] if stats else None,
            "min_picks": MIN_PICKS, "min_coverage": MIN_COVERAGE, "min_weeks": MIN_WEEKS,
            "eligible_rules": sum(s["eligible"] for s in stats)}
    if not any(s["eligible"] for s in stats):
        return {**base, "status": "insufficient_evidence", "rule": None,
                "reason": f"no rule has {MIN_PICKS}+ settled picks, {MIN_COVERAGE:.0%}+ coverage and {MIN_WEEKS}+ weeks "
                          f"({settled} settled cohorts)", "stats": stats}
    if not passing:
        return {**base, "status": "abstain", "rule": None,
                "reason": "no eligible rule's adjusted lower bound of lift over the reference is above 0", "stats": stats}
    best = max(passing, key=lambda s: (s["lower"], s["coverage"], -order[_key(s)]))
    if incumbent is not None:
        inc = next((s for s in passing if _key(s) == _key(incumbent)), None)
        if inc is not None and best["lower"] < inc["lower"] + HYSTERESIS:
            best = inc
    rule = {k: best[k] for k in ("source", "filter", "threshold")}
    return {**base, "status": "active", "rule": rule, "lower": best["lower"], "lift": best["lift"],
            "reason": f"{rule['source']} / {rule['filter']} / {rule['threshold']:.2f}: lift {best['lift']:+.3f}, "
                      f"lower bound {best['lower']:+.3f} on {best['picks']} picks", "stats": stats}


def apply(decision: Optional[dict], item: dict, market: str) -> dict:
    """Selection for one unsettled item under the decision in force."""
    if decision is None:
        return {"status": "no_pick", "reason": "no selector decision in force yet", "side": None, "probability": None}
    if decision["status"] != "active":
        return {"status": "no_pick", "reason": f"selector {decision['status']}: {decision['reason']}", "side": None,
                "probability": None}
    ok, reason, side, p = passes(decision["rule"], item, market)
    if not ok:
        return {"status": "no_pick", "reason": reason, "side": None, "probability": p}
    return {"status": "pick", "reason": f"passes {decision['rule']['source']} / {decision['rule']['filter']} at "
                                        f"{decision['rule']['threshold']:.2f}", "side": combine.SIDES[side], "probability": p}


def choose_market(candidates: dict) -> tuple[Optional[str], dict]:
    """candidates: market -> (selection, decision). One recommendation per game: the passing market whose decision has the
    highest lower bound; every other market gets its reason."""
    passing = {m: d["lower"] for m, (s, d) in candidates.items() if s["status"] == "pick"}
    chosen = max(passing, key=lambda m: (passing[m], -sorted(candidates).index(m))) if passing else None
    reasons = {}
    for m, (s, d) in candidates.items():
        if m == chosen:
            reasons[m] = f"recommended: highest evidence lower bound ({passing[m]:+.3f})"
        elif s["status"] == "pick":
            reasons[m] = f"passes, but outranked by {chosen} ({passing[m]:+.3f} < {passing[chosen]:+.3f})"
        else:
            reasons[m] = s["reason"]
    return chosen, reasons


def week_of(ts: str) -> str:
    from datetime import datetime
    y, w, _ = datetime.strptime(ts[:10], "%Y-%m-%d").isocalendar()
    return f"{y}-W{w:02d}"


def dumps(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))
