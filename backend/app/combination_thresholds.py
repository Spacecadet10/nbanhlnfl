"""Confidence thresholds and exact-subset agreement on SAVED out-of-sample forecasts (extends combination_research.stored).

Same saved tables, earlier/later split, pick and settlement rules as `combination_research.stored`. Nothing is fitted: a
model's probability is read as saved, a combination is the equal-weight average, a rule is a count. Three views:

- thresholds: each model and the group's average, cumulative ("65%+") and non-overlapping bands ("65–70%");
- agreement x confidence: the average's probability crossed with the share of the group picking the average's side;
- exact subsets: every pair and triple of the compared models; a game qualifies when all members of the subset pick the
  same side and each member's probability for it reaches that member's own threshold. Models outside the subset do not
  vote. Rules are searched on the earlier period, frozen, then scored on the later period without retuning.

Confidence = probability of the picked side among the market's allowed sides (a push's probability is left out of a
two-way line market; in the 3-way market the draw is an outcome, so a pick can sit below 50%). Win rate = wins / (wins +
losses): pushes and unsettled games are counted separately and never in the denominator.
"""
import gzip
import hashlib
import json
import warnings
from functools import lru_cache
from itertools import combinations
from pathlib import Path
from typing import Optional

import numpy as np

from . import combination_research as cr
from .errors import InvalidRequest
from . import combine, config, markets, nhl_eval, selector, sports
from .db import utc_now

TWO_WAY_LEVELS = (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90)
THREE_WAY_LEVELS = (1 / 3, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90)
TWO_WAY_GRID = (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80)       # per-member thresholds searched for a subset rule
THREE_WAY_GRID = (1 / 3, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65)
AGREEMENT_LEVELS = (None, 0.5, 0.6, 0.75, 0.9, 1.0)              # share of the group on the average's side (None = any)
SUBSET_SIZES = (2, 3)
MIN_RULE_DECIDED = 100       # decided earlier picks before a searched rule can be frozen
FROZEN_TOP = 20              # rules frozen per search (by the earlier Wilson lower bound)
MIN_LATER_DECIDED = 50       # below this a frozen rule's later record is reported as too few picks to judge
EPS = 1e-12
ADAPTIVE_NOTE = ("Out-of-sample forecasts of the adaptive candidates (weekly-refit walk, night-before cutoff), saved in the "
                 "adaptive history store and exported without refitting. These seasons were already used for training "
                 "and model choices: all results are exploratory, never an untouched test. No historical bookmaker odds: "
                 "nothing here measures profit. Research only: nothing here changes the live forecasts or selects a model.")
SUBSET_NOTE = ("A rule's record is an observed record on these games, not proof it will hold. Many rules were searched; "
               "the best earlier records are partly luck, which is why only the later period (frozen rules, no "
               "retuning) is evidence — and it is exploratory too.")


def _levels(market):
    return THREE_WAY_LEVELS if market in combine.THREE_WAY else TWO_WAY_LEVELS


def _grid(market):
    return THREE_WAY_GRID if market in combine.THREE_WAY else TWO_WAY_GRID


def _pct(x):
    return f"{x * 100:.1f}".rstrip("0").rstrip(".")


# --- Sources: the candidate pool's saved tables, or an exported adaptive candidate history ----------------------------
def adaptive_dir(sport: str) -> Path:
    return config.data_dir() / "research" / "adaptive" / sport


def sources(sport: str) -> list:
    """Every saved source for the sport: the pool tables and each exported adaptive definition (newest first)."""
    out = [{"key": "pool", "label": "Candidate pool walk (saved out-of-sample tables)", "files": cr._source_identity(sport)}]
    for p in sorted(adaptive_dir(sport).glob("oof-*.json.gz"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            head = json.loads(gzip.decompress(p.read_bytes())).get("head", {})
        except (OSError, ValueError):
            continue
        out.append({"key": f"adaptive:{head.get('definition')}", "label": f"Adaptive candidates, definition "
                    f"{head.get('definition')}" + (" (in force at export)" if head.get("in_force") else " (older, history)"),
                    "files": [{"file": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()[:16]}],
                    "in_force": head.get("in_force"), "exported_at": head.get("exported_at")})
    return out


def _load(sport: str, market: str, source: str) -> tuple[list, list, list, dict]:
    """(rows, compared models, excluded models with reasons, source detail)."""
    if source == "pool":
        rows, models, excluded = cr._table(sport, market)
        return rows, models, excluded, {"kind": "pool", "not_stored": []}
    if not source.startswith("adaptive:"):
        raise InvalidRequest("source must be 'pool' or 'adaptive:<definition>'")
    p = adaptive_dir(sport) / f"oof-{source.split(':', 1)[1]}.json.gz"
    if not p.is_file():
        raise InvalidRequest(f"no exported adaptive history for {source} (run combination-export-adaptive)")
    d = json.loads(gzip.decompress(p.read_bytes()))
    rows = d["tables"].get(market, [])
    models, excluded = cr.earlier_roster(rows, complete=False)     # earlier presence only; partial coverage is allowed here
    return rows, models, excluded, {"kind": "adaptive", **d["head"]}


# --- Arrays -----------------------------------------------------------------------------------------------------------
class Table:
    """Saved rows as arrays: P[game, model, 3] (NaN = no saved forecast), O[game, side] outcome codes."""

    def __init__(self, market: str, rows: list, models: list, later_seasons: Optional[list] = None):
        self.market, self.models = market, list(models)
        rows = sorted(rows, key=lambda r: (r["tipoff_at"], r["game_id"]))
        self.rows = rows
        g, m = len(rows), len(models)
        self.P = np.full((g, m, 3), np.nan)
        for i, r in enumerate(rows):
            for j, name in enumerate(models):
                v = r["vectors"].get(name)
                if v is not None:
                    values = np.asarray(v, dtype=float)
                    if (values.shape != (3,) or not np.isfinite(values).all()
                            or (values < 0).any() or (values > 1).any()
                            or not np.isclose(values.sum(), 1., rtol=0., atol=1e-6)):
                        raise ValueError(f"invalid saved probability for {name} in {r['game_id']}")
                    self.P[i, j] = values
        self.present = ~np.isnan(self.P[:, :, 0])
        self.settled = np.array([r["realised"] is not None for r in rows], dtype=bool)
        # outcome code of picking side s: 1 win, 0 loss, 2 push, -1 unsettled
        self.O = np.full((g, 3), -1, dtype=np.int8)
        code = {"win": 1, "loss": 0, "push": 2}
        for i, r in enumerate(rows):
            if r["realised"] is not None:
                for s in combine.sides(market):
                    self.O[i, s] = code[selector.outcome(market, s, r["realised"])]
        self.season = np.array([r["season"] for r in rows])
        self.tipoff = np.array([r["tipoff_at"] for r in rows])
        later = later_seasons if later_seasons is not None else cr.later_seasons(rows)
        if later is None:          # never treat every game as one period: the caller reports insufficient evidence
            raise InvalidRequest(f"no earlier/later split: needs more than {cr.LATER_SEASONS} seasons")
        self.periods = {"earlier": ~np.isin(self.season, later), "later": np.isin(self.season, later)}
        self.sides = np.array(combine.sides(market))
        self.present_avg = {}
        self.side, self.conf = {}, {}
        for j, name in enumerate(models):
            self.side[name], self.conf[name] = self.pick(self.P[:, j])

    def pick(self, V):
        """(side, confidence) per game; side −1 when no forecast or the allowed sides tie exactly (an abstention)."""
        A = V[:, self.sides]
        ok = ~np.isnan(A).any(axis=1)
        A0 = np.where(ok[:, None], A, -1.0)
        best = A0.max(axis=1)
        ties = (np.abs(A0 - best[:, None]) <= EPS).sum(axis=1) > 1
        side = np.where(ok & ~ties, self.sides[A0.argmax(axis=1)], -1)
        tot = np.where(ok, np.nansum(A, axis=1), 1.0)
        conf = np.where(side >= 0, best / np.where(tot > 0, tot, 1.0), np.nan)
        return side, conf

    def average(self, members: list):
        """(side, confidence, eligible) of the equal-weight average; eligible = every member has a saved forecast."""
        key = "avg:" + "+".join(members)
        if key not in self.side:
            idx = [self.models.index(m) for m in members]
            elig = self.present[:, idx].all(axis=1)
            with warnings.catch_warnings():          # games without every member are NaN on purpose
                warnings.simplefilter("ignore", RuntimeWarning)
                V = np.where(elig[:, None], np.nanmean(self.P[:, idx], axis=1), np.nan)
            V = V / np.where(elig, V.sum(axis=1), 1.0)[:, None]
            self.side[key], self.conf[key] = self.pick(V)
            self.present_avg[key] = elig
        return key, self.present_avg[key]

    def eligible(self, members: list):
        return self.present[:, [self.models.index(m) for m in members]].all(axis=1)


def _period_span(t: Table, mask):
    tips = t.tipoff[mask]
    return {"seasons": sorted(set(t.season[mask].tolist())), "games": int(mask.sum()),
            "date_from": min(tips)[:10] if len(tips) else None, "date_to": max(tips)[:10] if len(tips) else None}


def record(t: Table, sel, side, eligible, prob=None) -> dict:
    """One row: eligible games, qualifying picks, coverage, W/L/P, pending, win rate [Wilson 95%], mean probability of
    the picked side over decided picks, calibration gap (win rate − mean probability)."""
    sel = sel & eligible & (side >= 0)
    idx = np.nonzero(sel)[0]
    codes = t.O[idx, side[idx]] if len(idx) else np.array([], dtype=np.int8)
    w, l, p = int((codes == 1).sum()), int((codes == 0).sum()), int((codes == 2).sum())
    pending = int((codes == -1).sum())
    decided = (codes == 1) | (codes == 0)
    probs = prob[idx][decided] if prob is not None and len(idx) else np.array([])
    rate = w / (w + l) if w + l else None
    mean_p = float(probs.mean()) if len(probs) else None
    elig = int(eligible.sum())
    tips = t.tipoff[idx]
    return {"eligible": elig, "picks": int(len(idx)), "coverage": len(idx) / elig if elig else None, "wins": w, "losses": l,
            "pushes": p, "pending": pending, "win_rate": rate, "win_rate_ci95": nhl_eval._wilson(w, w + l),
            "mean_p": mean_p, "calibration_gap": rate - mean_p if rate is not None and mean_p is not None else None,
            "first": min(tips)[:10] if len(tips) else None, "last": max(tips)[:10] if len(tips) else None}


def _ladder(t: Table, key: str, eligible, period, levels) -> dict:
    side, conf = t.side[key], t.conf[key]
    el = eligible & period
    bounds = list(levels) + [1.0]
    cumulative = [{"rule": f"{_pct(lo)}%+", "lo": lo, "hi": None, **record(t, conf >= lo - EPS, side, el, conf)} for lo in levels]
    # The top band is closed at 100%: a certain pick (confidence 1.0) belongs to it, so the bands partition the picks.
    bands = [{"rule": f"{_pct(lo)}–{_pct(hi)}%", "lo": lo, "hi": hi,
              **record(t, (conf >= lo - EPS) & ((conf < hi - EPS) if hi < 1.0 else True), side, el, conf)}
             for lo, hi in zip(bounds[:-1], bounds[1:])]
    return {"cumulative": cumulative, "bands": bands}


# --- 1. Thresholds per model and for the group's average ---------------------------------------------------------------
def thresholds(t: Table, group: list) -> dict:
    levels = _levels(t.market)
    out = []
    for name in group + (["avg"] if len(group) > 1 else []):
        if name == "avg":
            key, elig = t.average(group)
            members, kind = group, "average"
        else:
            key, elig, members, kind = name, t.present[:, t.models.index(name)], [name], "model"
        out.append({"key": key, "kind": kind, "members": members,
                    **{pn: _ladder(t, key, elig, pm, levels) for pn, pm in t.periods.items()}})
    return {"levels": list(levels), "sources": out,
            "probability": "the probability of the picked side among the market's allowed sides"}


# --- 2. Agreement x confidence for the group's average ------------------------------------------------------------------
def agreement(t: Table, group: list) -> dict:
    """Share of the group picking the AVERAGE's side, crossed with the average's probability. A model that abstains (exact
    tie) counts in the denominator and not as agreeing; a game where any group member has no saved forecast is excluded,
    so a missing model never turns 8 of 10 into 8 of 8."""
    key, elig = t.average(group)
    side, conf = t.side[key], t.conf[key]
    agree = np.zeros(len(t.rows), dtype=int)
    for m in group:
        agree += (t.side[m] == side) & (side >= 0)
    n = len(group)
    share = agree / n
    levels = _levels(t.market)
    cross, counts = {}, {}
    for pn, pm in t.periods.items():
        el = elig & pm
        cross[pn] = [{"level": lo, "agreement_min": a, "rule": f"average {_pct(lo)}%+ · " +
                      ("any agreement" if a is None else "all agree (unanimous)" if a == 1.0 else f"≥{_pct(a)}% agree"),
                      **record(t, (conf >= lo - EPS) & (share >= (a or 0) - EPS), side, el, conf)}
                     for lo in levels for a in AGREEMENT_LEVELS]
        counts[pn] = [{"level": lo, "agreeing": k, "of": n, "kind": "unanimous" if k == n else "partial",
                       **record(t, (conf >= lo - EPS) & (agree == k), side, el, conf)}
                      for lo in levels for k in range(n, 0, -1)]
    return {"members": group, "cross": cross, "by_count": counts,
            "excluded_games_missing_member": int((~elig).sum()),
            "rule": "agreement = models in the group picking the average's side / models in the group; an exact 50/50 model "
                    "abstains (in the denominator, not agreeing); unanimous = every group model agrees"}


# --- 3. Adding a model at each threshold --------------------------------------------------------------------------------
def add_model(t: Table, base: list, candidates: list) -> dict:
    """avg(base) against avg(base + C) at each cumulative level, on games where every model of base + C is saved:
    retained (both qualify, same side), switched (both qualify, other side), new (only base + C qualifies), dropped (only
    base qualifies). Separates better predictions from simply picking fewer or different games."""
    kb, _ = t.average(base)
    levels = _levels(t.market)
    out = []
    for c in candidates:
        kc, elig = t.average(base + [c])
        sb, cb, sc, cc = t.side[kb], t.conf[kb], t.side[kc], t.conf[kc]
        per = {}
        for pn, pm in t.periods.items():
            el = elig & pm
            rows = []
            for lo in levels:
                qb, qc = (sb >= 0) & (cb >= lo - EPS), (sc >= 0) & (cc >= lo - EPS)
                both = qb & qc
                rows.append({"level": lo, "rule": f"{_pct(lo)}%+", "base": record(t, qb, sb, el, cb),
                             "with": record(t, qc, sc, el, cc), "retained": record(t, both & (sb == sc), sb, el, cb),
                             "switched_base": record(t, both & (sb != sc), sb, el, cb),
                             "switched_with": record(t, both & (sb != sc), sc, el, cc),
                             "new": record(t, qc & ~qb, sc, el, cc), "dropped": record(t, qb & ~qc, sb, el, cb)})
            per[pn] = rows
        out.append({"added": c, "members": base + [c], **per})
    return {"base": base, "rows": out}


# --- 4. Exact subsets with per-member thresholds ------------------------------------------------------------------------
def _rule_label(members, ths):
    return " & ".join(f"{m} ≥{_pct(x)}%" for m, x in zip(members, ths))


def _qualifying(t: Table, members: list, ths: list):
    """(eligible, shared side, qualifying) masks: every member saved, all on the same side, each at its own threshold."""
    elig = t.eligible(members)
    s0 = t.side[members[0]]
    qual = elig & (s0 >= 0)
    for m in members[1:]:
        qual &= t.side[m] == s0
    for m, x in zip(members, ths):
        qual &= t.conf[m] >= x - EPS
    return elig, s0, qual


def _earlier_signature(t: Table, members: list, ths: list) -> bytes:
    """The exact earlier picks of a rule (game positions and picked sides). Only earlier data: later outcomes or later
    forecast presence can never change which rules are frozen or collapsed."""
    _, s0, qual = _qualifying(t, members, ths)
    idx = np.nonzero(qual & t.periods["earlier"])[0]
    return idx.tobytes() + b"|" + s0[idx].astype(np.int8).tobytes()


def evaluate_rule(t: Table, members: list, ths: list, best_single: Optional[str] = None) -> dict:
    """One exact rule in both periods: its record, each member's probability for the shared side, each member alone at its
    own threshold (retained = the rule's picks, excluded = the member's other picks), and the same number of the best single
    model's most confident picks (coverage-matched: does agreement add anything beyond picking confident games?)."""
    elig, s0, qual = _qualifying(t, members, ths)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        probs = np.nanmean(np.stack([t.conf[m] for m in members]), axis=0)     # mean member probability of the shared side
    out = {"members": members, "thresholds": list(ths), "rule": _rule_label(members, ths),
           "excluded_games_missing_member": int((~elig).sum())}
    for pn, pm in t.periods.items():
        el = elig & pm
        rec = record(t, qual, s0, el, probs)
        idx = np.nonzero(qual & el)[0]
        member_p = {m: (float(np.nanmean(t.conf[m][idx])) if len(idx) else None) for m in members}
        alone = []
        for m, x in zip(members, ths):
            own = (t.side[m] >= 0) & (t.conf[m] >= x - EPS)
            alone.append({"model": m, "threshold": x, "own": record(t, own, t.side[m], el, t.conf[m]),
                          "excluded": record(t, own & ~qual, t.side[m], el, t.conf[m])})
        matched = same = None
        if best_single:
            bs, bc = t.side[best_single], t.conf[best_single]
            # (a) the best single model on EXACTLY the rule's games, with its own pick there (identical if it is a member)
            on = el & t.present[:, t.models.index(best_single)]
            same = {"model": best_single, "member": best_single in members,
                    "same_side": int((qual & on & (bs == s0)).sum()), "abstained": int((qual & on & (bs < 0)).sum()),
                    **record(t, qual, bs, on, bc)}
            # (b) the best single model's same number of most confident picks: possibly DIFFERENT games
            el_b = t.present[:, t.models.index(best_single)] & pm & (bs >= 0) & t.settled
            n = rec["wins"] + rec["losses"] + rec["pushes"]
            order = np.nonzero(el_b)[0]
            top = order[np.argsort(-bc[order], kind="stable")[:n]]
            sel = np.zeros(len(t.rows), dtype=bool)
            sel[top] = True
            matched = {"model": best_single, "picks_matched": n, **record(t, sel, bs, el_b, bc),
                       "min_probability": float(bc[top].min()) if len(top) else None}
        seasons = sorted(set(t.season[pm].tolist()))
        by_season = [{"season": int(sn), **record(t, qual, s0, el & (t.season == sn), probs)} for sn in seasons]
        out[pn] = {"record": rec, "member_mean_p": member_p, "alone": alone, "same_games_best_single": same,
                   "coverage_matched_best_single": matched, "by_season": by_season,
                   "too_few": rec["wins"] + rec["losses"] < MIN_LATER_DECIDED}
    return out


LATER_OVERLAP_DEFINITION = (
    "Descriptive later-period overlap: frozen rules whose later picks are exactly the same games (game IDs) with the same "
    "picked sides share one pick set. Rules in one set made the same picks, so their later win–loss–push outcomes, pending "
    "picks and win rate are identical; their mean probability and calibration gap can differ, because those come from each "
    "rule's own members. Matching win–loss totals on different games are different sets. Rules sharing a set are one later "
    "result, not independent confirmations. Counted only after the rules were frozen on the earlier period; it never "
    "removes, replaces, reorders or chooses a rule.")


def _count(n: int, word: str) -> str:
    """'1 rule' / '2 rules' for the generated summaries."""
    return f"{n} {word}{'' if n == 1 else 's'}"


def later_pick_sets(t: Table, rules: list) -> dict:
    """Group already-frozen rules by their exact later picks (game ID and picked side, pending picks included). Descriptive
    only: the caller's list and order are left as they are."""
    later, ids, set_of_rule, picks = t.periods["later"], {}, [], {}
    for f in rules:
        _, s0, qual = _qualifying(t, list(f["members"]), list(f["thresholds"]))
        key = tuple((t.rows[i]["game_id"], int(s0[i])) for i in np.nonzero(qual & later)[0])
        sid = ids.setdefault(key, len(ids) + 1)
        set_of_rule.append(sid)
        picks[sid] = len(key)
    shared = [{"set": sid, "rules": [i + 1 for i, s in enumerate(set_of_rule) if s == sid], "picks": picks[sid]}
              for sid in sorted(picks) if set_of_rule.count(sid) > 1]
    return {"rules": len(rules), "distinct_later_pick_sets": len(ids), "set_of_rule": set_of_rule, "shared": shared}


MEMBER_OVERLAP_NOTE = (
    "Descriptive earlier-period pick overlap of member pairs: the share of games on which both members had a side and picked "
    "the same one. Members built on shared inputs (one a superset of the other, e.g. players_injuries_v1 extends logistic_v2) "
    "agree largely by construction, so their unanimity is not independent evidence; the coverage-matched best-single gate "
    "guards against this only partly. Counted on the earlier (search) period only; it never changes the search or the choice.")


def member_overlap(t: Table, models: list) -> dict:
    """Pairwise share of same-side picks among `models` on the earlier period (games where both picked a side)."""
    early = t.periods["earlier"]
    pairs = []
    for i, a in enumerate(models):
        for b in models[i + 1:]:
            both = early & (t.side[a] >= 0) & (t.side[b] >= 0)
            n, same = int(both.sum()), int((both & (t.side[a] == t.side[b])).sum())
            pairs.append({"a": a, "b": b, "games": n, "same_side": same, "share": same / n if n else None})
    pairs.sort(key=lambda p: (-(p["share"] if p["share"] is not None else -1.0), p["a"], p["b"]))
    return {"period": "earlier", "pairs": pairs, "note": MEMBER_OVERLAP_NOTE}


def _beats_matched(f: dict) -> bool:
    """Later win rate above the best single model's same number of most confident later picks (descriptive)."""
    m = f["later"]["coverage_matched_best_single"]
    return (f["later"]["record"]["win_rate"] is not None and bool(m) and m["win_rate"] is not None
            and f["later"]["record"]["win_rate"] > m["win_rate"])


def search_large_subsets(t: Table, *, budget_per_size: int = 512, beam_width: int = 8,
                         min_picks: int = MIN_RULE_DECIDED, seed: int = 17) -> dict:
    if not 16 <= budget_per_size <= 10000 or not 1 <= beam_width <= 50 or min_picks < 1:
        raise InvalidRequest("invalid bounded-search budget, beam width or minimum picks")
    early = t.periods["earlier"] & t.settled
    models = sorted(m for m in t.models if (early & t.present[:, t.models.index(m)]).any())
    grid = tuple(_levels(t.market))
    rng = np.random.default_rng(seed)
    evaluated, frozen, previous = {}, [], []
    singles = []
    for m in models:
        r = record(t, t.side[m] >= 0, t.side[m], early & t.present[:, t.models.index(m)])
        singles.append((-((r["win_rate_ci95"] or [0])[0]), m))
    ranked = [m for _, m in sorted(singles)]
    best = ranked[0] if ranked else None
    for size in range(2, len(models) + 1):
        seen, candidates = set(), []

        def consider(pairs):
            key = tuple(sorted(pairs))
            if key in seen or len(seen) >= budget_per_size:
                return
            seen.add(key)
            ms, ts = map(list, zip(*key))
            elig, side, qual = _qualifying(t, ms, ts)
            r = record(t, qual, side, elig & early)
            decided = r["wins"] + r["losses"]
            score = (decided >= min_picks, (r["win_rate_ci95"] or [0])[0], decided)
            candidates.append((score, key))

        for threshold in grid:
            consider((m, threshold) for m in ranked[:size])
        for _, key in previous:
            for m in ranked:
                if m not in dict(key):
                    consider((*key, (m, grid[0])))
                    if len(seen) >= budget_per_size // 2:
                        break
            if len(seen) >= budget_per_size // 2:
                break
        for _ in range(min(beam_width, len(models))):
            group = rng.choice(models, size=size, replace=False).tolist()
            consider((m, grid[0]) for m in group)
        while len(seen) < budget_per_size:
            leaders = sorted(candidates, key=lambda x: (tuple(-float(v) for v in x[0]), x[1]))[:beam_width]
            before = len(seen)
            for _, key in leaders:
                for j, (m, threshold) in enumerate(key):
                    for value in grid:
                        if value != threshold:
                            consider((*key[:j], (m, value), *key[j + 1:]))
                    for other in ranked:
                        if other not in dict(key):
                            consider((*key[:j], (other, threshold), *key[j + 1:]))
            if before == len(seen):
                break
        previous = sorted(candidates, key=lambda x: (tuple(-float(v) for v in x[0]), x[1]))[:beam_width]
        evaluated[str(size)] = len(seen)
        for score, key in previous:
            if score[0]:
                frozen.append(key)
    rules = [evaluate_rule(t, list(dict(key)), list(dict(key).values()), best) for key in frozen]
    return {"rules": rules, "best_single": best, "search": {
        "method": "deterministic size-stratified beam search with coordinate threshold/member mutations",
        "exhaustive": False, "sizes": list(range(2, len(models) + 1)), "models": models,
        "thresholds": list(grid), "budget_per_size": budget_per_size, "beam_width": beam_width,
        "min_earlier_decided": min_picks, "seed": seed, "evaluated_by_size": evaluated,
        "evaluated": sum(evaluated.values()), "selection_data": "earlier period only",
        "note": "Larger subsets are searched at every size, not exhaustively. Rankings are exploratory, not proof of superiority."}}


def _cum_counts(H):
    for ax in range(H.ndim):
        H = np.flip(np.cumsum(np.flip(H, ax), axis=ax), ax)
    return H


def search_subsets(t: Table, models: list, best_single: Optional[str]) -> dict:
    """Every pair and triple; per member, every threshold in the grid (unequal thresholds allowed). Counted on the earlier
    period only; the top FROZEN_TOP rules by the earlier Wilson lower bound (≥ MIN_RULE_DECIDED decided picks) are frozen
    and then scored on the later period."""
    grid = np.array(_grid(t.market))
    L = len(grid)
    early = t.periods["earlier"]
    cands, searched, per_size = [], 0, {}
    # Coverage-matched baseline on the earlier period: wins among the best single model's n most confident decided picks.
    base_cum = np.zeros(1)
    if best_single:
        bs, bc = t.side[best_single], t.conf[best_single]
        ok = np.nonzero(early & t.settled & (bs >= 0) & t.present[:, t.models.index(best_single)])[0]
        codes = t.O[ok, bs[ok]]
        ok, codes = ok[codes != 2], codes[codes != 2]
        order = np.argsort(-bc[ok], kind="stable")
        base_cum = np.concatenate([[0], np.cumsum(codes[order] == 1)])
    for k in SUBSET_SIZES:
        n_sub = 0
        for members in combinations(models, k):
            n_sub += 1
            elig = t.eligible(list(members)) & early & t.settled
            s0 = t.side[members[0]]
            same = elig & (s0 >= 0)
            for m in members[1:]:
                same &= t.side[m] == s0
            idx = np.nonzero(same)[0]
            searched += L ** k
            if not len(idx):
                continue
            lv = [np.clip(np.searchsorted(grid, t.conf[m][idx] + EPS, side="right") - 1, 0, L - 1) for m in members]
            flat = np.ravel_multi_index(lv, (L,) * k)
            codes = t.O[idx, s0[idx]]
            W = np.bincount(flat[codes == 1], minlength=L ** k).reshape((L,) * k)
            Lo = np.bincount(flat[codes == 0], minlength=L ** k).reshape((L,) * k)
            W, Lo = _cum_counts(W), _cum_counts(Lo)
            n = W + Lo
            ok = n >= MIN_RULE_DECIDED
            if not ok.any():
                continue
            lb = _wilson_lb(W[ok], n[ok])
            nb = np.minimum(n[ok], len(base_cum) - 1)
            lift = W[ok] / n[ok] - base_cum[nb] / np.maximum(nb, 1)
            for pos, b, w, nn, li in zip(np.argwhere(ok), lb, W[ok], n[ok], lift):
                cands.append((float(b), int(nn), members, tuple(float(grid[i]) for i in pos), int(w), float(li)))
        per_size[k] = {"subsets": n_sub, "rules": n_sub * L ** k}
    eligible_rules = len(cands)
    lists = {}
    collapsed = {}
    for name, key in (("reliable", lambda c: (-c[0], -c[1], len(c[2]), c[2], c[3])),
                      ("lift", lambda c: (-c[5], -c[1], len(c[2]), c[2], c[3]))):
        frozen, seen, dup = [], set(), 0
        chosen = []
        for lb, n, members, ths, w, li in sorted(cands, key=key):      # chosen from earlier data only
            if len(chosen) == FROZEN_TOP:
                break
            sig = _earlier_signature(t, list(members), list(ths))
            if sig in seen:          # the same earlier picks as a rule already listed (e.g. a member at a non-binding threshold)
                dup += 1
                continue
            seen.add(sig)
            chosen.append((lb, members, ths, li))
        for lb, members, ths, li in chosen:                          # only now are the frozen rules scored on later games
            frozen.append({**evaluate_rule(t, list(members), list(ths), best_single), "earlier_wilson_lower": lb,
                           "earlier_lift": li})
        lists[name], collapsed[name] = frozen, dup
    later_beats = {k: sum(1 for f in v if _beats_matched(f)) for k, v in lists.items()}
    # Descriptive overlap of the frozen rules' later picks, computed after freezing; the lists are not changed by it.
    overlap = {}
    for k, v in lists.items():
        ov = later_pick_sets(t, v)
        for f, sid in zip(v, ov["set_of_rule"]):
            f["later_pick_set"] = sid
        won = {sid for f, sid in zip(v, ov["set_of_rule"]) if _beats_matched(f)}
        overlap[k] = {**ov, "beats_matched": {"rules": later_beats[k], "distinct_later_pick_sets": len(won)}}
    return {"grid": grid.tolist(), "sizes": list(SUBSET_SIZES), "per_size": {str(k): v for k, v in per_size.items()},
            "rules_searched": searched, "rules_with_min_picks": eligible_rules, "min_decided": MIN_RULE_DECIDED,
            "frozen": lists["reliable"], "frozen_lift": lists["lift"], "frozen_top": FROZEN_TOP,
            "later_beats_matched": later_beats, "duplicates_collapsed": collapsed,
            "later_overlap": overlap, "later_overlap_definition": LATER_OVERLAP_DEFINITION,
            "ranked_by": {"frozen": "earlier-period Wilson 95% lower bound of the win rate",
                          "frozen_lift": "earlier-period win rate minus the best single model's win rate on the same number "
                                         "of its most confident picks (does agreement add anything beyond confidence?)"},
            "best_single": best_single, "note": SUBSET_NOTE}


def _wilson_lb(w, n, z=1.959964):
    w, n = np.asarray(w, float), np.asarray(n, float)
    p = w / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h


def _best_single(t: Table, models: list) -> Optional[str]:
    """The model with the lowest earlier-period log loss (the same choice combination_research.stored makes)."""
    early = t.periods["earlier"] & t.settled
    best, val = None, None
    push = np.array([r["realised"] is None or (r["realised"] == 1 and t.market not in combine.THREE_WAY) for r in t.rows])
    for j, m in enumerate(models):
        el = early & t.present[:, j] & ~push
        if not el.any():
            continue
        y = np.array([t.rows[i]["realised"] for i in np.nonzero(el)[0]])
        p = t.P[el, j][np.arange(len(y)), y]
        ll = float(-np.log(np.maximum(p, 1e-15)).mean())
        if val is None or ll < val:
            best, val = m, ll
    return best


# --- Assembly ------------------------------------------------------------------------------------------------------------
def _parse_rule(spec: str, models: list) -> tuple[list, list]:
    members, ths = [], []
    for part in [p for p in spec.split(",") if p]:
        name, _, x = part.rpartition("@")
        if name not in models or name in members:
            raise InvalidRequest(f"rule members must be distinct models from: {', '.join(models)}")
        try:
            v = float(x)
        except ValueError:
            raise InvalidRequest("rule thresholds must be numbers, e.g. logistic_v2@0.65") from None
        if not 0 < v < 1:
            raise InvalidRequest("rule thresholds must be between 0 and 1")
        members.append(name)
        ths.append(v)
    if not members:
        raise InvalidRequest("rule needs at least one member, e.g. a@0.6,b@0.7")
    return members, ths


@lru_cache(maxsize=16)
def _table(sport, market, source, stamp):
    """(Table or None, compared models, excluded, source detail, (status, reason) or None)."""
    rows, models, excluded, detail = _load(sport, market, source)
    state = cr.evidence_state(rows, models, 1)
    if state:
        return None, models, excluded, detail, state
    return Table(market, rows, models, later_seasons=cr.later_seasons(rows)), models, excluded, detail, None


def _not_available(sport, market, source, models, excluded, state) -> dict:
    return {"sport": sport, "market": market, "source": source, "sources": sources(sport), "status": state[0],
            "reason": state[1], "models": models, "excluded": excluded}


def _stamp(sport, source):
    return tuple(f.get("sha256") for s in sources(sport) if s["key"] == source for f in s["files"])


def _head(sport, market, source, t, models, excluded, detail):
    reserved = cr._reserved(sport) if detail["kind"] == "pool" else set()
    later = _period_span(t, t.periods["later"])
    return {"sport": sport, "market": market, "source": source, "sources": sources(sport), "source_detail": detail,
            "kind": "historical out-of-sample (exploratory)", "models": models, "excluded": excluded,
            "not_stored": detail.get("not_stored", []),
            "periods": {"all": _period_span(t, np.ones(len(t.rows), dtype=bool)),
                        "earlier": _period_span(t, t.periods["earlier"]), "later": later},
            "later_reserved_test_seasons": sorted(set(later["seasons"]) & reserved),
            "pending_games": int((~t.settled).sum()),
            # Games of each period and each compared model's missing forecasts there. A game without every member of a
            # rule or average is excluded from it (and from its comparisons); no forecast is filled in.
            "coverage": {pn: cr.coverage([r for r, inp in zip(t.rows, pm) if inp], models) for pn, pm in t.periods.items()},
            "coverage_definition": cr.COVERAGE_DEFINITION, "split": cr.split(t.rows),
            "note": (cr.STORED_NOTE if detail["kind"] == "pool" else ADAPTIVE_NOTE)
                    + ("" if market in ("winner", "reg3") else " " + cr.LINE_NOTE),
            "win_rate_denominator": "wins + losses (pushes and unsettled games excluded and shown separately)"}


def explore(sport: str, market: str, source: str = "pool", group: Optional[list] = None, base: Optional[list] = None,
            rule: Optional[str] = None) -> dict:
    """Thresholds, agreement x confidence, add-a-model and (optionally) one exact rule. Backend-calculated, no store access."""
    stamp = _stamp(sport, source)
    t, models, excluded, detail, state = _table(sport, market, source, stamp)
    if state:
        return _not_available(sport, market, source, models, excluded, state)
    group = list(group or models)
    if any(m not in models for m in group) or len(set(group)) != len(group) or not group:
        raise InvalidRequest(f"group must be distinct models from: {', '.join(models)}")
    best = _best_single(t, models)
    if base:
        if any(m not in models for m in base) or len(set(base)) != len(base) or not 1 <= len(base) < len(models):
            raise InvalidRequest(f"base must be 1 to {len(models) - 1} distinct models from: {', '.join(models)}")
    else:
        base = [best] if best else models[:1]
    out = {**_head(sport, market, source, t, models, excluded, detail), "status": "available", "group": group,
           "best_single": best, "thresholds": thresholds(t, group),
           "agreement": agreement(t, group) if len(group) > 1 else None,
           "add_model": add_model(t, list(base), [m for m in models if m not in base])}
    if rule:
        members, ths = _parse_rule(rule, models)
        out["rule"] = evaluate_rule(t, members, ths, best)
    return out


@lru_cache(maxsize=16)
def _search(sport, market, source, stamp):
    t, models, _, _, _ = _table(sport, market, source, stamp)
    return search_subsets(t, models, _best_single(t, models))


def subsets(sport: str, market: str, source: str = "pool") -> dict:
    stamp = _stamp(sport, source)
    t, models, excluded, detail, state = _table(sport, market, source, stamp)
    state = state or (("insufficient_evidence", f"only {len(models)} eligible model; a subset needs at least 2")
                      if len(models) < 2 else None)
    if state:
        return _not_available(sport, market, source, models, excluded, state)
    return {**_head(sport, market, source, t, models, excluded, detail), "status": "available", **_search(sport, market, source, stamp)}


# --- Per-game ledger of one rule -----------------------------------------------------------------------------------------
def _games(sport: str, ids: list) -> dict:
    """Teams and scores of these games, read from the store read-only (no lock, no write)."""
    import sqlite3
    path = config.db_path(sport)
    if not path.is_file() or not ids:
        return {}
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        q = (f"SELECT g.game_id, g.home_score, g.away_score, coalesce(h.abbr, g.home_team_id), coalesce(a.abbr, g.away_team_id) "
             f"FROM games g LEFT JOIN teams h ON h.team_id = g.home_team_id LEFT JOIN teams a ON a.team_id = g.away_team_id "
             f"WHERE g.game_id IN ({','.join('?' * len(ids))})")
        return {r[0]: {"home_score": r[1], "away_score": r[2], "home": r[3], "away": r[4]} for r in conn.execute(q, ids)}
    finally:
        conn.close()


def _eastern_date(tipoff_at: str) -> str:
    from datetime import datetime, timezone
    from .backtest import EASTERN
    return datetime.strptime(tipoff_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(EASTERN).strftime("%Y-%m-%d")


def ledger(sport: str, market: str, source: str, rule: str, period: str = "all") -> dict:
    """Every qualifying game of one rule: date, teams, each member's pick and probability, the shared pick, the line, the
    actual outcome and win/loss/push, and the best earlier-selected single model's pick on the same game."""
    if period not in ("earlier", "later", "all"):
        raise InvalidRequest("period must be earlier, later or all")
    stamp = _stamp(sport, source)
    t, models, _, detail, state = _table(sport, market, source, stamp)
    if state:
        raise InvalidRequest(f"{sport} {market} in {source}: {state[1]}")
    members, ths = _parse_rule(rule, models)
    best = _best_single(t, models)
    elig, s0, qual = _qualifying(t, members, ths)
    pm = np.ones(len(t.rows), dtype=bool) if period == "all" else t.periods[period]
    idx = np.nonzero(qual & elig & pm)[0]
    labels = markets.SIDE_LABELS[market]
    info = _games(sport, [t.rows[i]["game_id"] for i in idx])
    code = {1: "win", 0: "loss", 2: "push", -1: "pending"}
    rows = []
    for i in idx:
        r, side = t.rows[i], int(s0[i])
        g = info.get(r["game_id"], {})
        bs = int(t.side[best][i]) if best else -1
        rows.append({"date": _eastern_date(r["tipoff_at"]), "tipoff_at": r["tipoff_at"], "game_id": r["game_id"], "season": int(r["season"]),
                     "period": "later" if t.periods["later"][i] else "earlier", "home": g.get("home"), "away": g.get("away"),
                     "market": market, "line": r.get("line"), "shared_pick": labels[side],
                     "members": [{"model": m, "threshold": x, "pick": labels[int(t.side[m][i])], "p": float(t.conf[m][i])}
                                 for m, x in zip(members, ths)],
                     "home_score": g.get("home_score"), "away_score": g.get("away_score"),
                     "actual": None if r["realised"] is None else labels[r["realised"]],
                     "result": code[int(t.O[i, side])], "best_single": best,
                     "best_single_pick": labels[bs] if bs >= 0 else None,
                     "best_single_p": float(t.conf[best][i]) if bs >= 0 else None,
                     "best_single_result": code[int(t.O[i, bs])] if bs >= 0 else None})
    return {"sport": sport, "market": market, "source": source, "source_detail": detail, "rule": _rule_label(members, ths),
            "members": members, "thresholds": ths, "period": period, "best_single": best, "games": len(rows),
            "exploratory": True, "rows": rows,
            # The rule members' earlier-period pick overlap: members that agree by construction are not independent votes.
            "member_overlap": member_overlap(t, members)}


def ledger_csv(led: dict) -> str:
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    head = ["date (US Eastern)", "game_id", "season", "period", "home", "away", "market", "line"]
    for m in led["members"]:
        head += [f"{m} pick", f"{m} probability"]
    head += ["shared_pick", "home_score", "away_score", "actual", "result", f"best single ({led['best_single']}) pick",
             "best single probability", "best single result"]
    w.writerow(head)
    for r in led["rows"]:
        row = [r["date"], r["game_id"], r["season"], r["period"], r["home"], r["away"], r["market"], r["line"]]
        for m in r["members"]:
            row += [m["pick"], f"{m['p']:.4f}"]
        row += [r["shared_pick"], r["home_score"], r["away_score"], r["actual"], r["result"], r["best_single_pick"],
                "" if r["best_single_p"] is None else f"{r['best_single_p']:.4f}", r["best_single_result"]]
        w.writerow(row)
    return buf.getvalue()


# --- Export of the adaptive candidate histories (research environment: the files hold torch/keras objects) -------------
def export_adaptive(conn, sport: str, log=None) -> dict:
    """Writes research/adaptive/<sport>/oof-<definition>.json.gz for every candidate history store: each candidate's saved
    out-of-sample output as winner (and 3-way) vectors on settled games. Reads; refits nothing. Line markets are not
    exported: the walk-forward model lines they need are not saved. Walk members have no saved history (re-walked each
    training run) and are listed as not stored."""
    from . import adaptive, live
    from .candidates import engine
    log = log or (lambda _m: None)
    now = utc_now()
    rows = {r.game.game_id: r for r in live.available_rows(conn, now)}
    # In force per training schedule: the weekly and the daily definitions each have their own latest usable combiner.
    by_schedule = {}
    for r in conn.execute("SELECT schedule, definition FROM adaptive_fits WHERE sport = ? AND kind = 'combiner' AND status IN "
                          "('success', 'partial') ORDER BY finished_at DESC, fit_id DESC", (sport,)):
        by_schedule.setdefault(r[0], r[1])
    in_force = set(by_schedule.values())
    out = {}
    for hp in sorted(adaptive_dir(sport).glob("history-*.pkl")):
        defn = hp.stem.split("-", 1)[1]
        try:
            hist = adaptive._load(hp)
        except Exception as exc:          # e.g. torch/keras missing outside the research environment
            out[defn] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            log(f"{defn}: {out[defn]['error']}")
            continue
        walk = [r[0] for r in conn.execute("SELECT DISTINCT name FROM adaptive_fits WHERE sport = ? AND definition = ? AND "
                                           "kind = 'member' AND name NOT LIKE 'cand:%' ORDER BY name", (sport, defn))]
        tables, pending = {}, 0
        markets = [m for m in ("winner", "reg3") if m in sports.get(sport).markets]
        for spec, store in sorted(hist["candidates"].items()):
            name = f"cand:{spec}@adaptive"
            for gid, o in store.get("outputs", {}).items():
                r = rows.get(gid)
                if r is None:
                    pending += 1
                    continue
                for mk in markets:
                    v = engine.vector(o, mk, None)
                    if v is None:
                        continue
                    t = tables.setdefault(mk, {})
                    row = t.setdefault(gid, {"game_id": gid, "season": r.game.season, "cutoff_at": r.cutoff_at,
                                             "tipoff_at": r.game.tipoff_at, "result_available_at": r.result_available_at,
                                             "line": None, "vectors": {},
                                             "realised": cr._realised(mk, None, r.home_score, r.away_score,
                                                                      getattr(r, "decision", None))})
                    row["vectors"][name] = [round(float(x), 9) for x in v]
        head = {"kind": "adaptive-candidate-history", "sport": sport, "definition": defn, "in_force": defn in in_force,
                "exported_at": now, "history_file": hp.name, "history_sha256": hashlib.sha256(hp.read_bytes()).hexdigest()[:16],
                "outputs_without_settled_game": pending,
                "not_stored": [{"model": w, "reason": "walk member: re-walked at each training run, history not saved"}
                               for w in walk]}
        data = {"head": head, "tables": {mk: sorted(v.values(), key=lambda r: (r["tipoff_at"], r["game_id"]))
                                         for mk, v in tables.items()}}
        dest = adaptive_dir(sport) / f"oof-{defn}.json.gz"
        tmp = dest.with_suffix(".tmp")
        tmp.write_bytes(gzip.compress(json.dumps(data).encode()))
        tmp.replace(dest)
        out[defn] = {"status": "success", "file": str(dest), "in_force": defn in in_force,
                     "games": {mk: len(v) for mk, v in data["tables"].items()}, "not_stored": len(walk)}
        log(f"{defn}: {out[defn]['games']}")
    _table.cache_clear()
    _search.cache_clear()
    return {"sport": sport, "exported": out}


# --- Report ---------------------------------------------------------------------------------------------------------------
def report(sport: str, market: str, source: str = "pool", out_dir: Optional[Path] = None) -> dict:
    """Reproducible report from the saved forecasts: the full explorer payload and the subset search, as JSON, plus a
    short Markdown summary."""
    ex, sub = explore(sport, market, source), subsets(sport, market, source)
    out_dir = out_dir or (config.data_dir() / "reports" / f"combination-thresholds-{sport}-{market}-{source.replace(':', '_')}")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "explore.json").write_text(json.dumps(ex, indent=1, default=str))
    (out_dir / "subsets.json").write_text(json.dumps(sub, indent=1, default=str))
    (out_dir / "summary.md").write_text(_summary_md(ex, sub))
    return {"output_dir": str(out_dir), "status": ex.get("status"), "rules_searched": sub.get("rules_searched"),
            "frozen": len(sub.get("frozen", []))}


def _fmt_rec(r):
    if not r:
        return "—"
    ci = r["win_rate_ci95"]
    rate = f"{r['win_rate'] * 100:.1f}% [{ci[0] * 100:.1f}, {ci[1] * 100:.1f}]" if r["win_rate"] is not None else "—"
    gap = f"{r['calibration_gap'] * 100:+.1f}" if r["calibration_gap"] is not None else "—"
    cov = f"{r['coverage'] * 100:.1f}%" if r["coverage"] is not None else "—"
    return f"{r['picks']} picks ({cov}) {r['wins']}–{r['losses']}–{r['pushes']}P, {rate}, gap {gap} pts"


def _summary_md(ex, sub):
    if ex.get("status") != "available":
        return (f"# Thresholds — {ex['sport']} {ex['market']} ({ex['source']})\n\nStatus: {ex.get('status')}"
                + (f" — {ex['reason']}" if ex.get("reason") else "") + "\n")
    p = ex["periods"]
    lines = [f"# Thresholds and exact subsets — {ex['sport'].upper()} {ex['market']} ({ex['source']})", "",
             f"Kind: {ex['kind']}. Earlier {p['earlier']['date_from']}–{p['earlier']['date_to']} ({p['earlier']['games']} games); "
             f"later {p['later']['date_from']}–{p['later']['date_to']} ({p['later']['games']} games)."
             + (f" Later includes spent reserved-test seasons {ex['later_reserved_test_seasons']}." if ex["later_reserved_test_seasons"] else ""),
             "", ex["note"], "", f"Win rate denominator: {ex['win_rate_denominator']}.", "",
             f"Models compared: {', '.join(ex['models'])}.",
             "Not stored: " + (", ".join(n["model"] for n in ex["not_stored"]) or "none") + ".",
             "Excluded: " + ("; ".join(f"{e['model']} ({e['reason']})" for e in ex["excluded"]) or "none") + ".",
             *(f"Missing {pn} forecasts (those games are excluded from any rule or average needing the model): "
               + (", ".join(f"{m} {n} of {ex['coverage'][pn]['settled']} settled" for m, n in ex["coverage"][pn]["missing"].items())
                  or "none") + "." for pn in ("earlier", "later")), "",
             "## Cumulative thresholds (later period)", "", "| Source | Threshold | Record |", "|---|---|---|"]
    for s in ex["thresholds"]["sources"]:
        for r in s["later"]["cumulative"]:
            if r["picks"]:
                lines.append(f"| {s['key']} | {r['rule']} | {_fmt_rec(r)} |")
    if sub.get("status") == "available":
        p_ = sub["periods"]
        missing = [n["model"] for n in sub.get("not_stored", [])] + [e["model"] for e in sub.get("excluded", [])]
        lines += ["", "## Exact subsets (chosen on the earlier period, frozen, then scored on the later period)", "",
                  "### Search limits", "",
                  f"- Source: {sub['source']} ({', '.join(f['file'] + ' ' + f['sha256'] for s_ in sub['sources'] if s_['key'] == sub['source'] for f in s_['files'])}).",
                  f"- Models searched ({len(sub['models'])}): {', '.join(sub['models'])}.",
                  f"- Not searched: {', '.join(missing) or 'none'}.",
                  f"- Subset sizes {sub['sizes']}; per-member thresholds {[round(x, 3) for x in sub['grid']]}; "
                  f"{', '.join(str(v['subsets']) + ' subsets of size ' + k for k, v in sub['per_size'].items())}; "
                  f"{sub['rules_searched']} rules searched; {sub['rules_with_min_picks']} had ≥{sub['min_decided']} decided earlier picks.",
                  f"- Earlier (search) {p_['earlier']['date_from']}–{p_['earlier']['date_to']}; later (frozen check) "
                  f"{p_['later']['date_from']}–{p_['later']['date_to']}. Best single model chosen on the earlier period: {sub['best_single']}.",
                  f"- {sub['note']}", "",
                  "### What the counts mean", "",
                  "Each list holds 20 frozen rules. \"X of 20\" counts RULES whose later win rate was above the comparator "
                  "(the best single model's same number of most confident later picks, possibly different games). It is not a "
                  "count of games won, and it is about this search only (these models, sizes, grid and minimum).", "",
                  sub["later_overlap_definition"], ""]
        for key, short_ in (("frozen", "reliable"), ("frozen_lift", "lift")):
            title = ("Highest earlier win rate (ranked by its 95% lower bound)" if key == "frozen"
                     else "Strongest earlier improvement over the best single model at the same number of picks")
            ov = sub["later_overlap"][short_]
            lines += [f"### {title}", "",
                      f"Later win rate above the comparator: {ov['beats_matched']['rules']} of {_count(ov['rules'], 'rule')}, "
                      f"{_count(ov['beats_matched']['distinct_later_pick_sets'], 'distinct later pick set')}. The {_count(ov['rules'], 'rule')} "
                      f"make {_count(ov['distinct_later_pick_sets'], 'distinct later pick set')}"
                      + ("; shared: " + "; ".join(f"set {g['set']} = rules {', '.join(map(str, g['rules']))} ({_count(g['picks'], 'pick')})"
                                                  for g in ov["shared"]) if ov["shared"] else "") + ".", "",
                      "| # | Later pick set | Rule | Earlier | Later | Later: best single on the SAME games | Later: best single, same number of its most confident picks |",
                      "|---|---|---|---|---|---|---|"]
            for i, f in enumerate(sub[key], 1):
                lines.append(f"| {i} | {f['later_pick_set']} | {f['rule']} | {_fmt_rec(f['earlier']['record'])} | {_fmt_rec(f['later']['record'])} | "
                             f"{_fmt_rec(f['later']['same_games_best_single'])} | {_fmt_rec(f['later']['coverage_matched_best_single'])} |")
            lines.append("")
    return "\n".join(lines) + "\n"
