"""Evaluation of the NCAAB top-4 card selection policy (docs/preregistration/ncaab-top4-policy-eval-2026-11-18.md).

Implements that frozen document and nothing else; section numbers below refer to it. Read-only on COPIES of the delivery
ledger and the NCAAB store (`--ledger`, `--data-dir`); the live data folder and live ledger are refused. The report goes
outside the stores. Two modes:

  status  counts only (qualifying days, units, whether a same-book close exists): no result, CLV, excess, hit rate or
          slope is computed or shown (section 7: no interim looks). Never reads a score.
  final   the one look, refused until the stop point (60th qualifying day, or ET date after 2027-03-15, or a rule change)
          and until every game of the last counted day is final or past 7 days.

Written and tested on synthetic data only; independent review (section 10) comes before any counted result is read.

  python -m app.prereg_ncaab_top4 --data-dir COPY --ledger COPY/.telegram-delivery.sqlite --out REPORTDIR [--mode status|final]
"""
import argparse
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

import numpy as np

from . import markets, ncaab_cards, odds, prereg_common as pc

PREREG_FILE = "ncaab-top4-policy-eval-2026-11-18.md"
PREREG_SHA256 = "55b8734a28eba1f56804810516fbf96ee1f0df6c2b7ce7c4dd1e6baa22bb9f3b"
REPO_ROOT = pc.REPO_ROOT
T0 = "2026-11-18T14:29:50Z"              # committer timestamp of b0c9eaf (git log --diff-filter=A on the document)
RULE = ncaab_cards.RULE
STOP_DAYS, STOP_DATE = 60, "2027-03-15"
P1_REPS = 1000
WINDOW = (time(11, 0, 0), time(11, 15, 0))
STALE = timedelta(minutes=60)
PAST = timedelta(days=7)
SIGMA_SPREAD, SIGMA_TOTAL = 11.319247889236534, 17.762583342156923
POLICIES = ("P0", "P1", "P2", "P3", "P4", "P5", "P6", "P7")
BINS = ("<=0", "0-5", "5-10", "10-20", ">=20")
STATE_KEYS = ("push", "void", "unscorable", "past7", "pending")


def check_document() -> None:
    """Refuses unless the frozen document's bytes and the frozen sigmas are as registered (section 3)."""
    pc.check_prereg(PREREG_FILE, PREREG_SHA256, REPO_ROOT)
    path = REPO_ROOT / "backend" / "app" / "models" / "ncaab_frozen_decision.json"
    sig = json.loads(path.read_text())["sigmas"]
    if sig["eff_margin"] != SIGMA_SPREAD or sig["points_total"] != SIGMA_TOTAL:
        raise pc.Refused("the frozen sigmas of ncaab_frozen_decision.json differ from the preregistered constants")


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- section 1: qualifying days ------------------------------------------------------------------------------------------
def load_rows(ledger: sqlite3.Connection) -> list:
    out = []
    for r in ledger.execute("SELECT * FROM ncaab_card_selections ORDER BY et_date"):
        d = dict(r)
        d["selected"], d["candidates"] = json.loads(d["selected"]), json.loads(d["candidates"])
        out.append(d)
    return out


def _sorted_priced(candidates: list) -> list:
    return sorted((c for c in candidates if c.get("gap") is not None), key=lambda c: (c["tipoff_at"], c["game_id"]))


def replay_selected(candidates: list) -> list:
    """The rule's own selection from the stored candidates: game gap > 0, top 4 by (gap desc, tip-off, game id)."""
    ranked = sorted((c for c in candidates if c.get("gap") is not None and c["gap"] > 0),
                    key=lambda c: (-c["gap"], c["tipoff_at"], c["game_id"]))
    return [c["game_id"] for c in ranked[:ncaab_cards.TOP_N]]


def prepare_day(row: dict) -> dict:
    return {**row, "priced": _sorted_priced(row["candidates"])}


@dataclass
class Classification:
    qualifying: list = field(default_factory=list)       # every qualifying day up to the stop date, in et_date order
    counted: list = field(default_factory=list)          # the days analysed: the first STOP_DAYS qualifying ones
    excluded: dict = field(default_factory=dict)
    stop_reached: bool = False
    stop_reason: Optional[str] = None


def classify(rows: list, *, asof: datetime, t0: str = T0, stop_date: str = STOP_DATE, stop_days: int = STOP_DAYS) -> Classification:
    """Section 1 and 7. Non-qualifying days are counted by reason and never analysed; nothing is imputed or re-run."""
    cl = Classification()
    bad = cl.excluded

    def bump(reason):
        bad[reason] = bad.get(reason, 0) + 1
    rule_changed, seen = False, set()
    for r in sorted(rows, key=lambda r: r["et_date"]):
        seen.add(r["et_date"])
        if rule_changed:
            bump("rule changed (counting ended)")
        elif r["selected_at"] < t0:
            bump("before T0")
        elif r["rule"] != RULE:
            rule_changed = True
            bump("rule changed (counting ended)")
        elif r["et_date"] > stop_date:
            bump(f"after {stop_date}")
        elif r["status"] != "ok":
            bump("selection error")
        else:
            local = _parse(r["selected_at"]).astimezone(ncaab_cards.EASTERN)
            if local.date().isoformat() != r["et_date"] or not WINDOW[0] <= local.time() <= WINDOW[1]:
                bump("selection outside 11:00-11:15 ET")
            elif not _sorted_priced(r["candidates"]):
                bump("no priced candidate")
            elif replay_selected(r["candidates"]) != r["selected"]:
                bump("P0 replay mismatch")
            else:
                cl.qualifying.append(prepare_day(r))
    first = _parse(t0).astimezone(ncaab_cards.EASTERN).date()
    last = min(asof.astimezone(ncaab_cards.EASTERN).date() - timedelta(days=1), date.fromisoformat(stop_date))
    d = first
    while d <= last:
        if d.isoformat() not in seen:
            bump("no selection row")
        d += timedelta(days=1)
    cl.counted = cl.qualifying[:stop_days]
    if len(cl.qualifying) > stop_days:
        bad["after 60th qualifying day"] = len(cl.qualifying) - stop_days
    if len(cl.qualifying) >= stop_days:
        cl.stop_reached, cl.stop_reason = True, f"{stop_days} qualifying days"
    elif rule_changed:
        cl.stop_reached, cl.stop_reason = True, "rule changed"
    elif asof.astimezone(ncaab_cards.EASTERN).date().isoformat() > stop_date:
        cl.stop_reached, cl.stop_reason = True, stop_date
    return cl


# --- section 2: policies -------------------------------------------------------------------------------------------------
def _unit(day: dict, c: dict, e: dict) -> dict:
    return {"et_date": day["et_date"], "game_id": c["game_id"], "tipoff_at": c["tipoff_at"], "market": e["market"], "side": e["side"],
            "line": e["line"], "q_sel": e["book"], "quote_id": e["quote_id"], "provider": e["provider"],
            "quote_retrieved_at": e["quote_retrieved_at"], "gap": e["gap"], "game_gap": c["gap"], "app": e["app"],
            "model_version": day["model_version"]}


def _best(c: dict) -> dict:
    return next(m for m in c["markets"] if m["market"] == c["best_market"])


def _tie(c: dict):
    return (c["tipoff_at"], c["game_id"])


def _priced_entries(c: dict) -> list:
    return [m for m in c["markets"] if m.get("gap") is not None]


def choose(policy: str, day: dict) -> list:
    """Units of one day under a policy (P1 is drawn by random4_draws). Ties: earlier tip-off, then lower game id."""
    priced, n = day["priced"], ncaab_cards.TOP_N
    if policy == "P0":
        by_id = {c["game_id"]: c for c in day["candidates"]}
        return [_unit(day, by_id[g], _best(by_id[g])) for g in day["selected"]]
    if policy == "P7":
        return []
    if policy == "P3":
        return [_unit(day, c, _best(c)) for c in priced]
    if policy == "P2":
        def top(c):                       # largest app over the priced entries; tie: market order
            return max(_priced_entries(c), key=lambda m: (m["app"], -ncaab_cards.MARKETS.index(m["market"])))
        picks = sorted(((c, top(c)) for c in priced), key=lambda ce: (-ce[1]["app"], *_tie(ce[0])))
        return [_unit(day, c, e) for c, e in picks[:n]]
    if policy == "P4":
        return [_unit(day, c, _best(c)) for c in sorted(priced, key=lambda c: (c["gap"], *_tie(c)))[:n]]
    if policy == "P5":
        wins = [(c, m) for c in priced for m in _priced_entries(c) if m["market"] == "winner" and m["gap"] > 0]
        return [_unit(day, c, m) for c, m in sorted(wins, key=lambda cm: (-cm[1]["gap"], *_tie(cm[0])))[:n]]
    if policy == "P6":
        capped = [c for c in priced if 0 < c["gap"] < 20.0]
        return [_unit(day, c, _best(c)) for c in sorted(capped, key=lambda c: (-c["gap"], *_tie(c)))[:n]]
    raise ValueError(policy)


def random4_draws(days: list, reps: int = P1_REPS) -> list:
    """P1: for each replicate (outer loop), each day in et_date order: rng.choice(n, min(4, n), replace=False) over the day's
    priced candidates sorted by (tip-off, game id); one default_rng(20261118) stream."""
    rng = np.random.default_rng(pc.SEED)
    return [[rng.choice(len(d["priced"]), min(ncaab_cards.TOP_N, len(d["priced"])), replace=False) for d in days] for _ in range(reps)]


def chosen_random(day: dict, idx) -> list:
    return [_unit(day, day["priced"][int(i)], _best(day["priced"][int(i)])) for i in idx]


# --- section 3: close and CLV ---------------------------------------------------------------------------------------------
def convert(market: str, p_close: float, line_close: float, line_sel: float, side: str) -> float:
    """Closing no-vig probability of the selected side at the selection line (normal conversion, frozen sigmas)."""
    if abs(line_close - line_sel) < 1e-9:
        pa = p_close
    elif market == "spread":
        mu = SIGMA_SPREAD * pc.norm_ppf(p_close) - line_close
        pa = pc.norm_cdf((mu + line_sel) / SIGMA_SPREAD)
    elif market == "total":
        mu = line_close + SIGMA_TOTAL * pc.norm_ppf(p_close)
        pa = pc.norm_cdf((mu - line_sel) / SIGMA_TOTAL)
    else:
        raise ValueError(market)
    return pa if side == "a" else 1 - pa


def points_clv(market: str, side: str, line_sel: Optional[float], line_close: Optional[float]) -> Optional[float]:
    if market not in ("spread", "total") or line_sel is None or line_close is None:
        return None
    if market == "spread":
        return line_sel - line_close if side == "a" else line_close - line_sel
    return line_close - line_sel if side == "a" else line_sel - line_close


def close_for(conn: sqlite3.Connection, u: dict) -> dict:
    """The same-sportsbook close of a unit (section 3): latest row before tip-off, no cross-book fallback."""
    tip = _parse(u["tipoff_at"])
    at = _ts(tip - timedelta(seconds=1))
    if u["market"] == "winner":
        q = odds.winner_quote(conn, "ncaab", u["game_id"], at, provider=u["provider"])
        if q is not None:
            q = {"quote_id": q["quote_id"], "retrieved_at": q["retrieved_at"], "line": None, "p_a": q["p_home"]}
    else:
        r = conn.execute("SELECT quote_id, retrieved_at, line, p_a_devig FROM market_quotes WHERE game_id = ? AND market = ? "
                         f"AND price_label = 'current' AND retrieved_at <= ? AND {odds.same_provider()} "
                         "ORDER BY retrieved_at DESC, quote_id DESC LIMIT 1", (u["game_id"], u["market"], at, u["provider"])).fetchone()
        q = None if r is None else {"quote_id": r[0], "retrieved_at": r[1], "line": r[2], "p_a": r[3]}
    if q is None:
        return {"status": "no_close"}
    converted = u["market"] != "winner" and abs(q["line"] - u["line"]) > 1e-9
    q_close = convert(u["market"], q["p_a"], q["line"], u["line"], u["side"]) if u["market"] != "winner" else (q["p_a"] if u["side"] == "a" else 1 - q["p_a"])
    stale = _parse(q["retrieved_at"]) < tip - STALE
    return {"status": "stale" if stale else "ok", "q_close": q_close, "converted": converted, "line": q["line"],
            "quote_id": q["quote_id"], "retrieved_at": q["retrieved_at"]}


# --- section 4: settlement and excess --------------------------------------------------------------------------------------
def settle_unit(conn: sqlite3.Connection, u: dict, now: datetime) -> dict:
    g = conn.execute("SELECT status, home_score, away_score, tipoff_at FROM games WHERE game_id = ?", (u["game_id"],)).fetchone()
    if g is None:
        return {"state": "unscorable"}
    res = markets.settle(u["market"], u["line"], status=g[0], home=g[1], away=g[2], tipoff_at=g[3], forecast_tipoff_at=u["tipoff_at"])
    if res == "pending":
        return {"state": "past7" if now >= _parse(u["tipoff_at"]) + PAST else "pending"}
    if res == "mid":
        return {"state": "push"}
    if res in ("void", "unscorable"):
        return {"state": res}
    return {"state": "decided", "won": res == u["side"]}


def values(u: dict, close: dict, settle: Optional[dict] = None) -> dict:
    """CLV, points CLV and excess of one unit. Pushes, voids, unscorable and past-7-day units have neither CLV nor excess."""
    state = "unsettled" if settle is None else settle["state"]
    counted = state in ("unsettled", "decided")
    ok = counted and close["status"] == "ok"
    has_close = counted and close["status"] in ("ok", "stale")
    won = settle.get("won") if settle else None
    same = has_close and not close["converted"]
    return {"et_date": u["et_date"], "game_id": u["game_id"], "market": u["market"], "model_version": u["model_version"], "gap": u["gap"],
            "q_sel": u["q_sel"], "state": state, "close": close["status"], "won": won,
            "clv": close["q_close"] - u["q_sel"] if ok else None,
            "clv_incl_stale": close["q_close"] - u["q_sel"] if has_close else None,
            "clv_same_line": close["q_close"] - u["q_sel"] if ok and same else None,
            "points_clv": points_clv(u["market"], u["side"], u["line"], close["line"]) if ok else None,
            "converted": bool(close.get("converted")) if has_close else False,
            "excess": (1.0 if won else 0.0) - u["q_sel"] if state == "decided" else None}


# --- section 5: metrics ----------------------------------------------------------------------------------------------------
def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def summarize(vs: list) -> dict:
    dec = [v for v in vs if v["state"] == "decided"]
    clv = [v["clv"] for v in vs if v["clv"] is not None]
    wins = sum(1 for v in dec if v["won"])
    lo, hi = pc.wilson(wins, len(dec))
    sums: dict = {}
    for v in vs:
        s = sums.setdefault(v["et_date"], {"clv": 0.0, "excess": 0.0})
        s["clv"] += v["clv"] or 0.0
        s["excess"] += v["excess"] or 0.0
    pts = [v["points_clv"] for v in vs if v["points_clv"] is not None]
    pct = lambda x: None if x is None else x * 100      # noqa: E731
    return {"n_picks": len(vs), "n_days": len({v["et_date"] for v in vs}), "n_decided": len(dec),
            **{k: sum(1 for v in vs if v["state"] == k) for k in STATE_KEYS},
            "no_close": sum(1 for v in vs if v["close"] == "no_close"), "stale_close": sum(1 for v in vs if v["close"] == "stale"),
            "line_converted_close": sum(1 for v in vs if v.get("converted")),
            "close_coverage": (sum(1 for v in vs if v["close"] == "ok") / len(vs)) if vs else None,
            "n_clv": len(clv), "mean_clv_pp": pct(_mean(clv)), "n_points_clv": len(pts), "mean_points_clv": _mean(pts),
            "mean_excess_pp": pct(_mean([v["excess"] for v in dec])), "hit_rate": wins / len(dec) if dec else None,
            "hit_rate_wilson95": [lo, hi], "mean_q_sel": _mean([v["q_sel"] for v in dec]), "day_sums": sums}


def bin_of(gap: float) -> str:
    if gap <= 0:
        return BINS[0]
    return BINS[1] if gap < 5 else BINS[2] if gap < 10 else BINS[3] if gap < 20 else BINS[4]


# --- section 6: hypotheses -------------------------------------------------------------------------------------------------
def day_arrays(per_day: list):
    """Per-day (sum, count) of the non-missing values: the bootstrap's cluster statistics."""
    nums = [sum(x for x in d if x is not None) for d in per_day]
    dens = [sum(1 for x in d if x is not None) for d in per_day]
    return np.array(nums, float), np.array(dens, float)


def test_mean(num, den) -> dict:
    """H: pooled mean > 0, day-cluster bootstrap (B = 10,000, default_rng(20261118)); one-sided p = (1 + #{theta* <= 0}) / (B + 1)."""
    theta = float(num.sum() / den.sum()) if den.sum() > 0 else None
    th = pc.boot_ratio(num, den)
    return {"theta": theta, "ci95": list(pc.ci95(th)), "p": pc.p_le_zero(th), "n_days": int(len(num)), "n": int(den.sum())}


test_mean.__test__ = False       # a function named test_* is not a pytest test


def h3_p(theta0: float, replicate_means) -> float:
    reps = list(replicate_means)
    return (1 + sum(1 for r in reps if r >= theta0)) / (1 + len(reps))


def _slope_test(per_day_xy: list) -> dict:
    stats = [(len(d), sum(x for x, _ in d), sum(y for _, y in d), sum(x * y for x, y in d), sum(x * x for x, _ in d)) for d in per_day_xy]
    arr = [np.array(c, float) for c in zip(*stats)] if stats else [np.zeros(0)] * 5
    pairs = [p for d in per_day_xy for p in d]
    th = pc.boot_slope(*arr)
    return {"theta": pc.slope([x for x, _ in pairs], [y for _, y in pairs]), "ci95": list(pc.ci95(th)), "p": pc.p_le_zero(th), "n": len(pairs)}


# --- section 2B: operational audit -------------------------------------------------------------------------------------------
_SIDE = {"a": "a", "home": "a", "over": "a", "b": "b", "away": "b", "under": "b"}


def audit(ledger: sqlite3.Connection, days: list) -> dict:
    """Descriptive counts of P0 games against structured sent-card records (published_picks linked to a 'sent' delivery);
    never the message text, never a CLV, excess or win figure."""
    out = {"p0_games": 0, "delivered_matches_selection": 0, "delivered_differs_from_selection": 0,
           "no_structured_delivery_record": 0, "unconfirmed_or_backfilled_basis": 0}
    for day in days:
        for u in choose("P0", day):
            out["p0_games"] += 1
            try:
                rows = ledger.execute("SELECT p.market, p.side, p.line, p.basis FROM published_picks p JOIN deliveries d "
                                      "ON d.delivery_key = p.delivery_key WHERE p.sport = 'ncaab' AND p.game_id = ? AND d.status = 'sent'",
                                      (u["game_id"],)).fetchall()
            except sqlite3.Error:
                rows = []
            live = [r for r in rows if r[3] == "live"]
            if not rows:
                out["no_structured_delivery_record"] += 1
            elif not live:
                out["unconfirmed_or_backfilled_basis"] += 1
            elif any(r[0] == u["market"] and _SIDE.get(r[1]) == u["side"] and (r[2] == u["line"] or
                     (r[2] is not None and u["line"] is not None and abs(r[2] - u["line"]) < 1e-9)) for r in live):
                out["delivered_matches_selection"] += 1
            else:
                out["delivered_differs_from_selection"] += 1
    return out


# --- the analysis (the one look) -------------------------------------------------------------------------------------------------
def _key(u: dict):
    return (u["game_id"], u["market"], u["side"], u["line"], u["quote_id"])


def analyze(days: list, store: sqlite3.Connection, ledger: Optional[sqlite3.Connection], *, now: datetime, excluded: Optional[dict] = None) -> dict:
    cache: dict = {}

    def val(u):
        k = _key(u)
        if k not in cache:
            cache[k] = values(u, close_for(store, u), settle_unit(store, u, now))
            cache[k]["_unit"] = u
        return {**cache[k], "et_date": u["et_date"]}
    units = {p: [[val(u) for u in choose(p, d)] for d in days] for p in ("P0", "P2", "P3", "P4", "P5", "P6", "P7")}
    summaries = {p: summarize([v for day in vs for v in day]) for p, vs in units.items()}
    # P1: 1,000 draws; pooled CLV / excess per replicate
    draws = random4_draws(days)
    p1_clv, p1_exc, p1_n = [], [], []
    for rep in draws:
        vs = [val(u) for d, idx in zip(days, rep) for u in chosen_random(d, idx)]
        p1_clv.append(_mean([v["clv"] for v in vs]) or 0.0)
        p1_exc.append(_mean([v["excess"] for v in vs]) or 0.0)
        p1_n.append(len(vs))
    summaries["P1"] = {"replicates": len(draws), "mean_picks": float(np.mean(p1_n)), "mean_clv_pp_of_replicates": float(np.mean(p1_clv)) * 100,
                       "mean_excess_pp_of_replicates": float(np.mean(p1_exc)) * 100,
                       "clv_pp_p5_p95": [float(x) * 100 for x in np.percentile(p1_clv, [5, 95])],
                       "excess_pp_p5_p95": [float(x) * 100 for x in np.percentile(p1_exc, [5, 95])]}
    # primary and secondary families
    p0 = units["P0"]
    h = {"H1": test_mean(*day_arrays([[v["clv"] for v in d] for d in p0])),
         "H2": test_mean(*day_arrays([[v["excess"] for v in d] for d in p0]))}
    clv0, exc0 = h["H1"]["theta"], h["H2"]["theta"]
    h["H3"] = {"theta": None if clv0 is None else clv0 - float(np.mean(p1_clv)), "p": h3_p(clv0 if clv0 is not None else float("inf"), p1_clv),
               "excess_version_p_not_in_family": h3_p(exc0 if exc0 is not None else float("inf"), p1_exc)}
    h["H4"] = _slope_test([[(v["gap"], v["clv"]) for v in d if v["clv"] is not None] for d in units["P3"]])
    h["H5"] = _slope_test([[(v["gap"], v["excess"]) for v in d if v["excess"] is not None] for d in units["P3"]])
    primary, secondary = pc.holm({k: h[k]["p"] for k in ("H1", "H2")}), pc.holm({k: h[k]["p"] for k in ("H3", "H4", "H5")})
    add_value = primary["H1"]["reject"] and secondary["H3"]["reject"]
    cov = summaries["P0"]["close_coverage"]
    flat = {p: [v for day in vs for v in day] for p, vs in units.items()}
    markets_split = {p: {m: summarize([v for v in vs if v["market"] == m]) for m in ("winner", "spread", "total")} for p, vs in flat.items()}
    versions = sorted({v["model_version"] for v in flat["P3"]}, key=str)
    diff = lambda a, b: {"clv_pp": _diff(summaries[a]["mean_clv_pp"], summaries[b]["mean_clv_pp"]),      # noqa: E731
                         "excess_pp": _diff(summaries[a]["mean_excess_pp"], summaries[b]["mean_excess_pp"])}
    bins = {b: summarize([v for v in flat["P3"] if bin_of(v["gap"]) == b]) for b in BINS}
    q_bad = q_missing = 0
    for k, c in cache.items():
        u = c["_unit"]
        r = store.execute("SELECT p_a_devig FROM market_quotes WHERE quote_id = ?", (u["quote_id"],)).fetchone()
        if r is None:
            q_missing += 1
        elif abs((r[0] if u["side"] == "a" else 1 - r[0]) - u["q_sel"]) > 1e-6:
            q_bad += 1
    return {
        "prereg_sha256": PREREG_SHA256, "evaluated_at": _ts(now), "n_counted_days": len(days),
        "excluded_days": excluded or {}, "policies": summaries, "markets": markets_split,
        "model_versions": {mv: summarize([v for v in flat["P0"] if v["model_version"] == mv]) for mv in versions},
        "close_coverage_p0": cov,
        "limitations": [] if cov is None or cov >= 0.8 else [f"close coverage {cov:.1%} of P0 picks is below 80%: limitation of the CLV result"],
        "hypotheses": h, "holm_primary": primary, "holm_secondary": secondary,
        "verdicts": {
            "policy_shown_to_add_value": add_value,
            "h2_alone": primary["H2"]["reject"] and not primary["H1"]["reject"],
            "h2_note": None if primary["H2"]["reject"] else "not shown (underpowered for excess: MDE about 7.7 points)",
            "clv_shown_false": bool(h["H1"]["ci95"][1] < 0) if h["H1"]["theta"] is not None else False,
            "gap_premise_shown_false": bool(h["H4"]["ci95"][1] < 0) if h["H4"]["theta"] is not None else False,
            "short_of_planned_n": None if len(days) >= STOP_DAYS else f"short of planned n ({len(days)} of {STOP_DAYS} days)"},
        "descriptive": {"P0_minus": {p: diff("P0", p) for p in ("P2", "P4", "P5", "P6")},
                        "P0_minus_P1": {"clv_pp": _diff(summaries["P0"]["mean_clv_pp"], summaries["P1"]["mean_clv_pp_of_replicates"]),
                                        "excess_pp": _diff(summaries["P0"]["mean_excess_pp"], summaries["P1"]["mean_excess_pp_of_replicates"])},
                        "gap_bins_P3": bins,
                        "sensitivity_P0": {"stale_closes_included_clv_pp": _pp(_mean([v["clv_incl_stale"] for v in flat["P0"]])),
                                           "same_line_closes_only_clv_pp": _pp(_mean([v["clv_same_line"] for v in flat["P0"]])),
                                           "mean_points_clv": summaries["P0"]["mean_points_clv"]}},
        "integrity": {"q_sel_mismatch": q_bad, "q_sel_quote_missing": q_missing},
        "audit_2b": audit(ledger, days) if ledger is not None else None}


def _diff(a, b):
    return None if a is None or b is None else a - b


def _pp(x):
    return None if x is None else x * 100


# --- status mode and entry point ---------------------------------------------------------------------------------------------
def _readonly(path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def status(cl: Classification, store: sqlite3.Connection) -> dict:
    """Counts only (section 7): days, candidates, selected, and whether a same-book close exists. No score is read."""
    p0 = [u for d in cl.counted for u in choose("P0", d)]
    closes = [close_for(store, u)["status"] for u in p0]
    return {"prereg_sha256": PREREG_SHA256, "counted_days": len(cl.counted), "qualifying_days": len(cl.qualifying), "planned_days": STOP_DAYS,
            "non_qualifying_days_by_reason": cl.excluded, "priced_candidates": sum(len(d["priced"]) for d in cl.counted),
            "p0_units": len(p0), "p0_close_available": closes.count("ok"), "p0_close_stale": closes.count("stale"),
            "p0_close_missing": closes.count("no_close"), "stop_reached": cl.stop_reached, "stop_reason": cl.stop_reason}


def run(ledger_path, data_dir, out, *, now: Optional[datetime] = None, mode: str = "status") -> dict:
    check_document()
    dd = pc.guard_data_dir(data_dir)
    ledger_file = pc.guard_file(ledger_path)
    outdir = pc.guard_out(out, dd)
    now = now or datetime.now(timezone.utc)
    store_file = dd / "ncaab.sqlite"
    if not store_file.is_file():
        raise pc.Refused(f"{store_file} not found")
    ledger, store = _readonly(ledger_file), _readonly(store_file)
    try:
        cl = classify(load_rows(ledger), asof=now)
        if mode == "status":
            report = status(cl, store)
            pc.write_report(outdir, "ncaab-top4-status.json", report)
            return report
        if mode != "final":
            raise ValueError(mode)
        if not cl.stop_reached:
            raise pc.Refused(f"the stop point is not reached ({len(cl.qualifying)} of {STOP_DAYS} qualifying days, date limit {STOP_DATE}): no interim look")
        if cl.counted:
            pending = [u for u in choose("P3", cl.counted[-1]) if settle_unit(store, u, now)["state"] == "pending"]
            if pending:
                raise pc.Refused(f"{len(pending)} game(s) of the last counted day are neither final nor past 7 days")
        report = analyze(cl.counted, store, ledger, now=now, excluded=cl.excluded)
        report["stop_reason"] = cl.stop_reason
        pc.write_report(outdir, "ncaab-top4-report.json", report)
        return report
    finally:
        ledger.close()
        store.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--mode", choices=("status", "final"), default="status")
    a = ap.parse_args(argv)
    try:
        print(json.dumps(run(a.ledger, a.data_dir, a.out, mode=a.mode), indent=2, sort_keys=True, default=str))
    except pc.Refused as exc:
        print(f"REFUSED: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
