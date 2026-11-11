#!/usr/bin/env python3
"""Value audit: do the stored forecasts and the AI ticket selection add measurable value?

Read-only. It copies the live stores with the SQLite backup API (sources opened mode=ro) into a snapshot directory and
analyses only the copies. It trains nothing, tunes nothing, writes nothing to data/ and sends nothing.

    PYTHONPATH=backend backend/.venv/bin/python scripts/value_audit.py \
        --as-of 2026-11-09T23:45:00Z --out docs/VALUE_AUDIT.md

Evidence classes are kept apart everywhere:
  BACKTEST       predictions.run_type='backtest' (probabilities computed after the games, walk-forward by the app's claim)
  LIVE           game_forecasts rows recorded before the start (the canonical ledger the app and the tickets use), and
                 adaptive_forecasts rows recorded before the start (research, never canonical)
  RECONSTRUCTED  recovered_forecasts (rebuilt after the start from inputs available at the cutoff; historical, not live)
Settlement reuses app.markets.settle and app.coverage.pick_side. Prices are the stored DraftKings quotes; "return" is a
hypothetical one-unit flat stake at the stored price, never an actual bet, stake or profit.
"""
import argparse, glob, hashlib, json, math, os, re, sqlite3, sys, tempfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import numpy as np

from app import markets
from app.coverage import pick_side

COMPETITIVE = ("regular", "play_in", "postseason")
SPORTS = ("nba", "nhl", "nfl")
TWO_WAY = ("winner", "spread", "total", "team_total_home", "team_total_away")
# Reporting conventions fixed before looking at results (not tuned): below these counts a section says so explicitly.
MIN_CALIBRATION_N = 100      # graded forecasts needed before a calibration or accuracy statement is called supported
MIN_SELECTION_N = 50         # settled selected legs needed before a selected-vs-excluded statement is called supported
MIN_RETURN_N = 200           # priced, settled picks needed before a return statement is called supported
CONF_BINS = (0.5, 0.55, 0.6, 0.65, 0.7, 0.8, 1.0000001)
FMT = "%Y-%m-%dT%H:%M:%SZ"


def ts(s): return datetime.strptime(s, FMT).replace(tzinfo=timezone.utc)
def sha(b): return hashlib.sha256(b).hexdigest()
def dec(american): return 1 + (american / 100 if american > 0 else 100 / -american)
def implied(american): return 1 / dec(american)


def wilson(w, n, z=1.959964):
    if not n:
        return None
    p = w / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def cboot(vals, clusters, B, rng):
    """Mean of vals and a percentile 95% interval from resampling whole clusters. (mean, lo, hi, n, clusters)."""
    vals = np.asarray(vals, float)
    if len(vals) == 0:
        return None
    _, inv = np.unique(np.asarray(clusters, dtype=object).astype(str), return_inverse=True)
    s = np.bincount(inv, weights=vals)
    c = np.bincount(inv).astype(float)
    k = len(s)
    if k < 2:
        return (float(vals.mean()), None, None, len(vals), k)
    lo_hi = []
    for start in range(0, B, 250):                      # chunks keep memory small for the large backtests
        idx = rng.integers(0, k, size=(min(250, B - start), k))
        lo_hi.append(s[idx].sum(1) / c[idx].sum(1))
    m = np.concatenate(lo_hi)
    return (float(vals.mean()), float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5)), len(vals), k)


# ---- formatting ------------------------------------------------------------------------------------------------
def pct(x, d=1): return "—" if x is None else f"{100 * x:.{d}f}%"
def num(x, d=4): return "—" if x is None else f"{x:.{d}f}"
def sgn(x, d=4): return "—" if x is None else f"{x:+.{d}f}"
def ci(lo, hi, f=sgn, d=4): return "—" if lo is None else f"[{f(lo, d)}, {f(hi, d)}]"
def pci(c): return "—" if c is None else f"[{pct(c[0])}, {pct(c[1])}]"


def table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def verdict(lo, hi, n, need, better_is_negative=True, words=("better", "worse", "not distinguishable")):
    """Reading of a difference from its 95% interval and the sample size this report asks for."""
    if lo is None:
        return "cannot be assessed"
    if lo <= 0 <= hi:
        return words[2] + ("" if n >= need else f" (n={n}, below the {need} asked for)")
    good = (hi < 0) if better_is_negative else (lo > 0)
    word = words[0] if good else words[1]
    return word + ("" if n >= need else f" in this sample, but n={n} is below the {need} asked for: not a supported conclusion")


RET_WORDS = ("above zero", "below zero", "not distinguishable from zero")
MODEL_WORDS = ("model better", "model worse", "not distinguishable")


# ---- stores ------------------------------------------------------------------------------------------------------
def snapshot_stores(root, ledger, dest):
    os.makedirs(os.path.join(dest, "data"), exist_ok=True)
    made = {}
    for name, src in [(s, os.path.join(root, "data", s + ".sqlite")) for s in SPORTS + ("soccer",)] + [("ledger", ledger)]:
        if not os.path.exists(src):
            made[name] = None
            continue
        out = os.path.join(dest, "data", name + ".sqlite")
        if os.path.exists(out):
            os.remove(out)
        s = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=60)
        d = sqlite3.connect(out)
        s.backup(d)
        d.close()
        s.close()
        made[name] = out
    return made


def ro(path):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


class Store:
    def __init__(self, sport, path, as_of):
        self.sport, self.as_of, self.c = sport, as_of, ro(path)
        self.games = {r["game_id"]: dict(r) for r in self.c.execute("SELECT * FROM games")}
        self.decisions = dict(self.c.execute("SELECT game_id, decision FROM game_decisions").fetchall())
        self._q = {}

    def game(self, gid):
        g = dict(self.games[gid])
        if g["status"] == "final" and (not g["result_available_at"] or g["result_available_at"] > self.as_of):
            g.update(status="in_progress", home_score=None, away_score=None)       # result not yet known at as_of
        return g

    def grade(self, f):
        """(grade, realised side) of one stored forecast row against the result known at as_of."""
        g = self.game(f["game_id"])
        st = markets.settle(f["market"], f["line"], status=g["status"], home=g["home_score"], away=g["away_score"],
                            decision=self.decisions.get(f["game_id"]), tipoff_at=g["tipoff_at"], forecast_tipoff_at=f["tipoff_at"])
        if st in ("pending", "void", "unscorable"):
            return st, None
        if st == "mid" and f["market"] != "reg3":
            return "push", st
        return ("win" if pick_side(f["market"], (f["p_a"], f["p_mid"], f["p_b"])) == st else "loss"), st

    def quotes(self, gid, market):
        key = (gid, market)
        if key not in self._q:
            self._q[key] = [dict(r) for r in self.c.execute(
                "SELECT quote_id, line, price_a, price_mid, price_b, p_a_devig, p_mid_devig, p_b_devig, retrieved_at FROM market_quotes "
                "WHERE game_id=? AND market=? AND price_label='current' AND retrieved_at<=? ORDER BY retrieved_at, quote_id",
                (gid, market, self.as_of))]
        return self._q[key]

    def quote_at(self, gid, market, line, at, quote_id=None):
        """The stored bookmaker quote usable at time `at` for this market at exactly this line, or (None, why).
        With quote_id (the quote a forecast used) only that row is accepted. Otherwise: the latest quote retrieved by `at`;
        if its line differs from the forecast's line the forecast is unpriced (the line moved)."""
        tip = self.games[gid]["tipoff_at"]
        if market in ("team_total_home", "team_total_away", "reg3"):
            return None, "no bookmaker quotes are collected for this market"
        if quote_id is not None:
            r = self.c.execute("SELECT * FROM market_quotes WHERE quote_id=?", (quote_id,)).fetchone()
            if r is None or r["game_id"] != gid or r["market"] != market:
                return None, "quote is not of this game and market"
            q = dict(r)
        else:
            qs = [q for q in self.quotes(gid, market) if q["retrieved_at"] <= at]
            if not qs:
                return None, "no quote retrieved by then"
            q = qs[-1]
        if (q["line"] is None) != (line is None) or (line is not None and abs(q["line"] - line) > 1e-9):
            return None, "latest quote is at a different line"
        if q["retrieved_at"] > at or q["retrieved_at"] >= tip:
            return None, "quote retrieved after the reference time or the start"
        if q["price_a"] is None or q["price_b"] is None:
            return None, "quote has no two-way prices"
        return q, None


def devig(q):
    a, b = implied(q["price_a"]), implied(q["price_b"])
    return a / (a + b), b / (a + b), a + b - 1


# ---- BACKTEST ----------------------------------------------------------------------------------------------------
def backtest(st, B, rng):
    rows = st.c.execute("SELECT p.model_version m, p.game_id, p.home_win_prob p, p.generated_at, g.tipoff_at, g.season, g.season_type, "
                        "g.home_score h, g.away_score a FROM predictions p JOIN games g USING(game_id) "
                        "WHERE p.run_type='backtest' AND g.status='final' AND g.home_score IS NOT NULL AND g.home_score<>g.away_score").fetchall()
    by = defaultdict(dict)
    for r in rows:
        by[r["m"]][r["game_id"]] = r
    out = {"models": [], "after_games": all(r["generated_at"] > r["tipoff_at"] for r in rows) if rows else None}
    base_home = next((m for m in by if m.startswith("home_rate")), None)
    base_const = next((m for m in by if m.startswith("constant")), None)

    def arr(m, ids):
        p = np.array([by[m][i]["p"] for i in ids])
        y = np.array([by[m][i]["h"] > by[m][i]["a"] for i in ids], float)
        return p, y
    for m in sorted(by):
        ids = sorted(by[m])
        p, y = arr(m, ids)
        pc = np.clip(p, 1e-15, 1 - 1e-15)
        rec = {"model": m, "n": len(ids), "first": min(by[m][i]["tipoff_at"] for i in ids)[:10], "last": max(by[m][i]["tipoff_at"] for i in ids)[:10],
               "accuracy": float(((p >= 0.5) == (y == 1)).mean()), "brier": float(((p - y) ** 2).mean()),
               "log_loss": float(-(y * np.log(pc) + (1 - y) * np.log(1 - pc)).mean()), "home_rate": float(y.mean())}
        for name, b in (("vs_home_rate", base_home), ("vs_constant", base_const)):
            if b and b != m:
                common = [i for i in ids if i in by[b]]
                pm, ym = arr(m, common)
                pb, _ = arr(b, common)
                d = (pm - ym) ** 2 - (pb - ym) ** 2
                rec[name] = cboot(d, [by[m][i]["tipoff_at"][:10] for i in common], B, rng)
        rec["by_season"] = []
        for season in sorted({by[m][i]["season"] for i in ids}):
            sid = [i for i in ids if by[m][i]["season"] == season]
            ps, ys = arr(m, sid)
            rec["by_season"].append((season, len(sid), float(((ps >= .5) == (ys == 1)).mean()), float(((ps - ys) ** 2).mean())))
        conf = np.maximum(p, 1 - p)
        hit = ((p >= 0.5) == (y == 1)).astype(float)
        rec["calibration"] = [(lo, hi, int(((conf >= lo) & (conf < hi)).sum()),
                               float(conf[(conf >= lo) & (conf < hi)].mean()) if ((conf >= lo) & (conf < hi)).any() else None,
                               float(hit[(conf >= lo) & (conf < hi)].mean()) if ((conf >= lo) & (conf < hi)).any() else None)
                              for lo, hi in zip(CONF_BINS, CONF_BINS[1:])]
        out["models"].append(rec)
    return out


# ---- LIVE canonical ------------------------------------------------------------------------------------------------
def canonical(st):
    """One record per started competitive game and market: the latest 'forecast' row made before the start for the game's
    current start (else for an earlier start, which grades void) - the app's own selection rule (coverage._select)."""
    rows = defaultdict(list)
    for r in st.c.execute("SELECT * FROM game_forecasts WHERE status='forecast' AND generated_at<tipoff_at AND generated_at<=? "
                          "ORDER BY generated_at, forecast_id", (st.as_of,)):
        rows[(r["game_id"], r["market"])].append(dict(r))
    out = []
    for (gid, market), fs in rows.items():
        g = st.games.get(gid)
        if g is None or g["season_type"] not in COMPETITIVE or g["tipoff_at"] > st.as_of:
            continue
        cur = [f for f in fs if f["tipoff_at"] == g["tipoff_at"]]
        out.append(enrich(st, (cur or fs)[-1], len(fs)))
    return out


def enrich(st, f, attempts=1, price_at=None, use_forecast_quote=True):
    """Adds grade, pick side, scoring probability, and the bookmaker quote usable at `price_at` (default: forecast time)."""
    g = st.games[f["game_id"]]
    grade, side_real = st.grade(f)
    p = (f["p_a"], f["p_mid"], f["p_b"])
    side = pick_side(f["market"], p)
    two = f["market"] != "reg3"
    pa = (p[0] / (p[0] + p[2])) if two and p[0] + p[2] > 0 else None
    conf = max(pa, 1 - pa) if two else max(p)
    rec = dict(sport=st.sport, market=f["market"], game_id=f["game_id"], forecast_id=f["forecast_id"], date=g["tipoff_at"][:10],
               tipoff=g["tipoff_at"], generated_at=f["generated_at"], minutes_before=(ts(f["tipoff_at"]) - ts(f["generated_at"])).total_seconds() / 60,
               p=p, pa=pa, side=side, conf=conf, line=f["line"], line_source=f.get("line_source"), source=f.get("source"),
               model_version=f.get("model_version"), grade=grade, side_real=side_real, attempts=attempts,
               scored=grade in ("win", "loss"), hit=(grade == "win") if grade in ("win", "loss") else None,
               ya=(side_real == "a") if grade in ("win", "loss") else None, mkt_pa=None, mkt_pick=None, price=None, overround=None, why_unpriced=None)
    at = price_at or f["generated_at"]
    qid = f.get("market_quote_id") if (use_forecast_quote and price_at is None) else None
    if two and (f["market"] == "winner" or f.get("line_source") in ("bookmaker", "market")):
        q, why = st.quote_at(f["game_id"], f["market"], f["line"], at, qid)
    else:
        q, why = None, ("no bookmaker quotes are collected for this market" if f["market"] in ("reg3", "team_total_home", "team_total_away")
                        else "model line (no bookmaker line at forecast time)")
    if q:
        a, b, over = devig(q)
        rec.update(mkt_pa=a, mkt_pick=a if side == "a" else b, price=q["price_a"] if side == "a" else q["price_b"], overround=over,
                   quote_age_min=(ts(at) - ts(q["retrieved_at"])).total_seconds() / 60,
                   devig_matches_store=q["p_a_devig"] is None or abs(q["p_a_devig"] - a) < 1e-4)
    else:
        rec["why_unpriced"] = why
    return rec


def unit_return(r):
    if r["price"] is None or r["grade"] not in ("win", "loss", "push"):
        return None
    return {"win": dec(r["price"]) - 1, "loss": -1.0, "push": 0.0}[r["grade"]]


def current_season(st):
    """The season of the games that have canonical forecasts (the live season)."""
    return st.c.execute("SELECT MAX(g.season) FROM game_forecasts f JOIN games g USING(game_id)").fetchone()[0]


def coverage_gap(st, canon):
    """Started competitive games since canonical recording began, and how many have a canonical winner forecast."""
    began = st.c.execute("SELECT MIN(tipoff_at) FROM game_forecasts f WHERE status='forecast'").fetchone()[0]
    ids = [r[0] for r in st.c.execute("SELECT game_id FROM games WHERE tipoff_at>=? AND tipoff_at<=? AND status='final' AND season_type IN ('regular','play_in','postseason')",
                                      (began, st.as_of))] if began else []
    have = {r["game_id"] for r in canon if r["market"] == "winner"}
    return len(ids), sum(1 for i in ids if i in have)


def home_baseline(st):
    """Ex-ante constant: the home win share of the two seasons before the current one (regular season)."""
    cur = current_season(st)
    r = st.c.execute("SELECT SUM(home_score>away_score), COUNT(*) FROM games WHERE status='final' AND season_type='regular' AND season IN (?,?) "
                     "AND home_score<>away_score", (cur - 1, cur - 2)).fetchone()
    return (r[0] / r[1], r[1], (cur - 2, cur - 1)) if r[1] else (None, 0, None)


def reg3_baseline(st):
    cur = current_season(st)
    r = st.c.execute("SELECT SUM(d.decision='REG' AND g.home_score>g.away_score), SUM(d.decision<>'REG'), SUM(d.decision='REG' AND g.home_score<g.away_score), COUNT(*) "
                     "FROM games g JOIN game_decisions d USING(game_id) WHERE g.status='final' AND g.season_type='regular' AND g.season IN (?,?)",
                     (cur - 1, cur - 2)).fetchone()
    return (r[0] / r[3], r[1] / r[3], r[2] / r[3]) if r[3] else None


def score_block(recs, B, rng, home_p=None, reg3_p=None):
    """Accuracy, calibration-in-the-large and Brier comparisons for records of one market (or pooled two-way markets)."""
    s = [r for r in recs if r["scored"]]
    n = len(s)
    out = {"records": len(recs), "graded": n, "grades": dict(Counter(r["grade"] for r in recs)), "games": len({(r["sport"], r["game_id"]) for r in s})}
    if not n:
        return out
    cl = [r["sport"] + r["game_id"] for r in s]
    hits = sum(r["hit"] for r in s)
    out.update(wins=hits, accuracy=hits / n, accuracy_ci=wilson(hits, n), mean_conf=float(np.mean([r["conf"] for r in s])))
    two = [r for r in s if r["market"] != "reg3"]
    if len(two) == n:
        exp = sum(r["conf"] for r in s)
        var = sum(r["conf"] * (1 - r["conf"]) for r in s)
        out.update(expected_wins=exp, z=(hits - exp) / math.sqrt(var) if var > 0 else None)
        pa = np.array([r["pa"] for r in s]); y = np.array([r["ya"] for r in s], float)
        pc = np.clip(pa, 1e-15, 1 - 1e-15)
        out.update(brier=float(((pa - y) ** 2).mean()), log_loss=float(-(y * np.log(pc) + (1 - y) * np.log(1 - pc)).mean()),
                   vs_coin=cboot((pa - y) ** 2 - 0.25, cl, B, rng))
        if home_p is not None and all(r["market"] == "winner" for r in s):
            out["vs_home_rate"] = cboot((pa - y) ** 2 - (home_p - y) ** 2, cl, B, rng)
        m = [r for r in s if r["mkt_pa"] is not None]
        out["matched"] = len(m)
        if m:
            pm = np.array([r["pa"] for r in m]); qm = np.array([r["mkt_pa"] for r in m]); ym = np.array([r["ya"] for r in m], float)
            out.update(brier_model_matched=float(((pm - ym) ** 2).mean()), brier_market=float(((qm - ym) ** 2).mean()),
                       vs_market=cboot((pm - ym) ** 2 - (qm - ym) ** 2, [r["sport"] + r["game_id"] for r in m], B, rng),
                       mean_overround=float(np.mean([r["overround"] for r in m])),
                       market_pick_agrees=float(np.mean([(r["mkt_pick"] >= 0.5) for r in m])),
                       market_exp_wins=float(sum(r["mkt_pick"] for r in m)), market_wins=int(sum(r["hit"] for r in m)))
    else:
        idx = {"a": 0, "mid": 1, "b": 2}
        v = np.array([r["p"] for r in s]); real = np.array([idx[r["side_real"]] for r in s])
        onehot = np.eye(3)[real]
        b = ((v - onehot) ** 2).sum(1)
        out.update(brier=float(b.mean()), log_loss=float(-np.log(np.clip(v[np.arange(n), real], 1e-15, 1)).mean()),
                   vs_uniform=cboot(b - ((1 / 3 - onehot) ** 2).sum(1), cl, B, rng), matched=0)
        if reg3_p:
            out["vs_prior_rates"] = cboot(b - ((np.array(reg3_p) - onehot) ** 2).sum(1), cl, B, rng)
    return out


def returns_block(recs, B, rng):
    pr = [(r, unit_return(r)) for r in recs]
    pr = [(r, u) for r, u in pr if u is not None]
    out = {"priced": len(pr), "unpriced": Counter(r["why_unpriced"] for r in recs if r["price"] is None and r["grade"] in ("win", "loss", "push"))}
    if not pr:
        return out
    cl = [r["sport"] + r["game_id"] for r, _ in pr]
    out["roi"] = cboot([u for _, u in pr], cl, B, rng)
    out["mean_overround"] = float(np.mean([r["overround"] for r, _ in pr]))
    out["theoretical_hold"] = float(np.mean([r["overround"] / (1 + r["overround"]) for r, _ in pr]))
    edge = [(r, u) for r, u in pr if r["conf"] > implied(r["price"])]
    out["edge_n"] = len(edge)
    out["roi_edge"] = cboot([u for _, u in edge], [r["sport"] + r["game_id"] for r, _ in edge], B, rng) if edge else None
    noedge = [(r, u) for r, u in pr if r["conf"] <= implied(r["price"])]
    out["roi_noedge"] = cboot([u for _, u in noedge], [r["sport"] + r["game_id"] for r, _ in noedge], B, rng) if noedge else None
    return out


def calibration_bins(recs):
    s = [r for r in recs if r["scored"] and r["market"] != "reg3"]
    rows = []
    for lo, hi in zip(CONF_BINS, CONF_BINS[1:]):
        b = [r for r in s if lo <= r["conf"] < hi]
        w = sum(r["hit"] for r in b)
        rows.append((lo, min(hi, 1.0), len(b), np.mean([r["conf"] for r in b]) if b else None, w / len(b) if b else None, wilson(w, len(b))))
    return rows


# ---- LIVE adaptive (research) and RECONSTRUCTED --------------------------------------------------------------------
def adaptive(st, canon, B, rng):
    sched = {r[0]: r[1] for r in st.c.execute("SELECT definition, MIN(schedule) FROM adaptive_fits GROUP BY definition")}
    rows = defaultdict(list)
    for r in st.c.execute("SELECT * FROM adaptive_forecasts WHERE status='forecast' AND generated_at<tipoff_at AND generated_at<=? "
                          "ORDER BY generated_at, forecast_id", (st.as_of,)):
        rows[(r["game_id"], r["market"])].append(dict(r))
    cidx = {(r["game_id"], r["market"]): r for r in canon}
    out = {}
    for schedule in ("weekly", "daily"):
        recs = []
        for (gid, market), fs in rows.items():
            g = st.games.get(gid)
            fs = [f for f in fs if sched.get(f["definition"]) == schedule and g and f["tipoff_at"] == g["tipoff_at"]]
            if not fs or g["season_type"] not in COMPETITIVE or g["tipoff_at"] > st.as_of:
                continue
            f = fs[-1]
            f["line_source"] = "bookmaker" if f.get("market_quote_id") else f.get("line_source")
            recs.append(enrich(st, f))
        for market in sorted({r["market"] for r in recs}):
            mine = [r for r in recs if r["market"] == market and r["scored"]]
            pairs = [(r, cidx.get((r["game_id"], market))) for r in mine]
            pairs = [(a, c) for a, c in pairs if c and c["scored"] and a["line"] == c["line"]]
            d = None
            if pairs and market != "reg3":
                d = cboot([(a["pa"] - a["ya"]) ** 2 - (c["pa"] - c["ya"]) ** 2 for a, c in pairs], [a["game_id"] for a, _ in pairs], B, rng)
            elif pairs:
                idx = {"a": 0, "mid": 1, "b": 2}
                def b3(r): return sum((r["p"][k] - (k == idx[r["side_real"]])) ** 2 for k in range(3))
                d = cboot([b3(a) - b3(c) for a, c in pairs], [a["game_id"] for a, _ in pairs], B, rng)
            out[(schedule, market)] = {"graded": len(mine), "wins": sum(r["hit"] for r in mine), "paired": len(pairs), "diff": d,
                                       "canon_wins_paired": sum(c["hit"] for _, c in pairs), "adaptive_wins_paired": sum(a["hit"] for a, _ in pairs)}
    return out


def reconstructed(st):
    out = Counter()
    for r in st.c.execute("SELECT * FROM recovered_forecasts WHERE reconstructed_at<=?", (st.as_of,)):
        g = st.games.get(r["game_id"])
        if not g:
            continue
        f = dict(r)
        grade, _ = st.grade(f)
        out[(r["market"], grade, r["reconstructed_at"] > r["tipoff_at"])] += 1
    return out


# ---- SELECTION -----------------------------------------------------------------------------------------------------
OVERRIDE = re.compile(r"user[- ]requested|requested by (the )?(user|owner)|owner[- ]requested|-user\\.json", re.I)


def user_override(plan):
    """True when the archived plan itself says the user asked for the tickets (review notes, comparison text or the
    snapshot file name). Text-based and approximate: an instruction given in chat that left no trace here is invisible."""
    review = plan.get("review", {})
    text = " ".join(str(review.get(k, "")) for k in ("notes", "combination_comparison")) + " " + str(plan.get("snapshot_path", ""))
    return bool(OVERRIDE.search(text))


def eligible(pick, now):
    start, made = ts(pick["tipoff"]), ts(pick["generated_at"])
    return (pick["status"] == "scheduled" and pick["grade"] == "pending" and now < start <= now + timedelta(hours=2)
            and start - timedelta(hours=2) <= made <= now)


def load_reviews(tickets_dir, as_of):
    """Unique review events from the archives (the same review is archived several times: deduplicated by snapshot time
    and plan content)."""
    events, files, bad = {}, 0, 0
    for path in sorted(glob.glob(os.path.join(tickets_dir, "*.json")) + glob.glob(os.path.join(tickets_dir, "reviews", "*.json"))):
        try:
            d = json.load(open(path))
            snap, plan = d["snapshot"], d["plan"]
            at = snap["as_of"]
        except (ValueError, KeyError, TypeError):
            bad += 1
            continue
        if at > as_of:
            continue
        files += 1
        key = (at, sha(json.dumps(plan, sort_keys=True).encode()))
        if key in events:
            events[key]["copies"] += 1
            continue
        fr = plan.get("review", {}).get("forecasts_reviewed")
        events[key] = dict(as_of=at, picks=[(sp, p) for sp, sec in snap.get("sports", {}).items() for p in (sec.get("report") or {}).get("picks", [])
                                             if sp in SPORTS and eligible(p, ts(at))],
                           decisions=fr if isinstance(fr, dict) else None, locks=set((d.get("locks") or {}).keys()),
                           tickets=len(plan.get("tickets") or []), copies=1, resolved=d.get("resolved") or [],
                           version=((snap.get("skill_sha256") or "unknown")[:8], (snap.get("helper_sha256") or "unknown")[:8]),
                           override=user_override(plan))
    return list(events.values()), files, bad


def load_tickets(ledger, events, as_of):
    """Sent tickets: every ticket the ledger has graded, with its latest grade evidence by as_of; the send time is the
    snapshot time of the latest review whose resolved tickets contain the same legs (an exact combination is sent once;
    an earlier review with the same legs is an attempt that was refused and repeated)."""
    c = ro(ledger)
    latest = {}
    for r in c.execute("SELECT ticket_id, as_of, payload FROM ticket_grades WHERE as_of<=? ORDER BY as_of", (as_of,)):
        latest[r["ticket_id"]] = json.loads(r["payload"])
    sent = {}
    for e in sorted(events, key=lambda e: e["as_of"]):
        for t in e["resolved"]:
            sent[tuple(sorted((p["sport"], p["forecast_id"]) for p in t["legs"]))] = e     # a refused attempt is followed by the real send
    out = []
    for tid, p in sorted(latest.items()):
        legs = [(l["sport"], l["forecast_id"]) for l in p["legs"]]
        out.append(dict(ticket_id=tid, label=p.get("label"), legs=legs, stored_grade=p["grade"], stored_legs={(l["sport"], l["forecast_id"]): l["grade"] for l in p["legs"]},
                        sent_at=(sent.get(tuple(sorted(legs))) or {}).get("as_of"),
                        version=(sent.get(tuple(sorted(legs))) or {}).get("version"), override=bool((sent.get(tuple(sorted(legs))) or {}).get("override"))))
    deliveries = c.execute("SELECT COUNT(*), SUM(status='sent') FROM deliveries WHERE delivery_key LIKE 'ai-ticket-review-v%' AND attempted_at<=?", (as_of,)).fetchone()
    personal = c.execute("SELECT COUNT(*) FROM personal_tickets").fetchone()[0]
    return out, tuple(deliveries), personal


def selection(stores, events, tickets, B, rng):
    pool = {}
    for e in sorted(events, key=lambda e: e["as_of"]):
        for sp, p in e["picks"]:
            k = (sp, p["forecast_id"])
            x = pool.setdefault(k, dict(first_review=e["as_of"], decisions=[], locked=False, moved=False, strong=bool(p.get("strong")),
                                        app_rank=p.get("game_rank"), reviews=0, legacy=False))
            x["reviews"] += 1
            dkey = f"{sp}:{p['forecast_id']}"
            if e["decisions"] is None:
                x["legacy"] = True
            elif dkey in e["decisions"]:
                dd = e["decisions"][dkey]
                x["decisions"].append(dd.get("decision"))
                reason = str(dd.get("reason", "")).lower()
                x["moved"] |= ("line" in reason and ("changed" in reason or "moved" in reason))
            x["locked"] |= dkey in e["locks"]
    sel_time, mult = {}, Counter()
    for t in tickets:
        for k in t["legs"]:
            mult[k] += 1
            if t["sent_at"]:
                sel_time[k] = min(sel_time.get(k, t["sent_at"]), t["sent_at"])
    recs, missing_from_pool = [], []
    for k in sorted(set(pool) | set(mult)):
        sp, fid = k
        st = stores[sp]
        f = st.c.execute("SELECT * FROM game_forecasts WHERE forecast_id=?", (fid,)).fetchone()
        if f is None:
            continue
        x = pool.get(k)
        if x is None:
            missing_from_pool.append(k)
            x = dict(first_review=sel_time.get(k), decisions=[], locked=False, moved=False, strong=None, app_rank=None, reviews=0, legacy=True)
        at = sel_time.get(k) or x["first_review"]
        r = enrich(st, dict(f), price_at=at) if at else enrich(st, dict(f))
        cls = ("selected" if k in mult else "candidate, not selected" if "candidate" in x["decisions"]
               else "excluded" if x["decisions"] else "reviewed, no per-forecast decision (before Nov 8 rule)")
        sub = None
        if cls == "excluded":
            sub = "excluded: game locked to another leg" if x["locked"] else "excluded: line moved (reason text)" if x["moved"] else "excluded: judgement"
        r.update(cls=cls, sub=sub, selected=k in mult, tickets=mult.get(k, 0), reference_time=at, strong=x["strong"], app_rank=x["app_rank"],
                 reviews=x["reviews"], review_minutes_before=(ts(r["tipoff"]) - ts(at)).total_seconds() / 60 if at else None)
        recs.append(r)
    return recs, missing_from_pool


_GROUP_CACHE = {}


def group_stats(rs, B, rng):
    key = frozenset((r["sport"], r["forecast_id"]) for r in rs)
    if key not in _GROUP_CACHE:
        _GROUP_CACHE[key] = _group_stats(rs, B, rng)
    return _GROUP_CACHE[key]


def _group_stats(rs, B, rng):
    s = [r for r in rs if r["scored"]]
    n = len(s)
    out = dict(n=len(rs), games=len({(r["sport"], r["game_id"]) for r in rs}), graded=n, wins=sum(r["hit"] for r in s),
               other=dict(Counter(r["grade"] for r in rs if not r["scored"])))
    if n:
        cl = [r["sport"] + r["game_id"] for r in s]
        out.update(rate=out["wins"] / n, ci=wilson(out["wins"], n), mean_conf=float(np.mean([r["conf"] for r in s])),
                   resid_model=cboot([r["hit"] - r["conf"] for r in s], cl, B, rng))
        m = [r for r in s if r["mkt_pick"] is not None]
        out.update(priced=len(m), mean_mkt=float(np.mean([r["mkt_pick"] for r in m])) if m else None,
                   resid_market=cboot([r["hit"] - r["mkt_pick"] for r in m], [r["sport"] + r["game_id"] for r in m], B, rng) if m else None)
        u = [(r, unit_return(r)) for r in rs]
        u = [(r, v) for r, v in u if v is not None]
        out.update(ret_n=len(u), roi=cboot([v for _, v in u], [r["sport"] + r["game_id"] for r, _ in u], B, rng) if u else None)
    return out


def band(c): return "50-60" if c < .6 else "60-70" if c < .7 else "70-80" if c < .8 else "80+"


def stratified_diff(recs, B, rng):
    """Selected minus not-selected win rate inside strata of sport x market x model-probability band, weighted by the
    number of selected legs in the stratum; interval from resampling games; permutation p-value inside strata."""
    s = [r for r in recs if r["scored"]]

    def est(rows):
        strata = defaultdict(lambda: [[], []])
        for r in rows:
            strata[(r["sport"], r["market"], band(r["conf"]))][0 if r["selected"] else 1].append(r["hit"])
        num = den = 0
        used = 0
        for _key in sorted(strata):
            a, b = strata[_key]
            if a and b:
                num += len(a) * (np.mean(a) - np.mean(b)); den += len(a); used += len(a)
        return (num / den if den else None), used
    point, used = est(s)
    if point is None:
        return None
    games = sorted({r["sport"] + r["game_id"] for r in s})
    byg = defaultdict(list)
    for r in s:
        byg[r["sport"] + r["game_id"]].append(r)
    boots = []
    for _ in range(B):
        rows = [r for g in rng.choice(games, len(games)) for r in byg[g]]
        e, _u = est(rows)
        if e is not None:
            boots.append(e)
    strata = defaultdict(list)
    for r in s:
        strata[(r["sport"], r["market"], band(r["conf"]))].append(r)
    perm = []
    for _ in range(B):
        rows = []
        for _key in sorted(strata):
            members = strata[_key]
            flags = rng.permutation([m["selected"] for m in members])
            rows += [dict(m, selected=bool(f)) for m, f in zip(members, flags)]
        e, _u = est(rows)
        perm.append(e if e is not None else 0)
    p = float(np.mean(np.abs(perm) >= abs(point) - 1e-12))
    return dict(diff=point, lo=float(np.percentile(boots, 2.5)), hi=float(np.percentile(boots, 97.5)), selected_used=used,
                selected_total=sum(r["selected"] for r in s), perm_p=p)


def ticket_results(tickets, recs, rng, sims=200000):
    idx = {(r["sport"], r["forecast_id"]): r for r in recs}
    rows = []
    for t in tickets:
        legs = [idx[k] for k in t["legs"] if k in idx]
        grades = [l["grade"] for l in legs]
        grade = ("loss" if "loss" in grades else "pending" if any(g in ("pending", "unscorable") for g in grades) or len(legs) < len(t["legs"])
                 else "void" if all(g in ("push", "void") for g in grades) else "win")
        priced = all(l["price"] is not None for l in legs)
        payout = float(np.prod([dec(l["price"]) for l in legs if l["grade"] not in ("push", "void")])) if priced else None
        rows.append(dict(t, legs_r=legs, grade=grade, agrees=grade == t["stored_grade"], priced=priced, payout=payout,
                         p_model=float(np.prod([l["conf"] for l in legs])), p_market=float(np.prod([l["mkt_pick"] for l in legs])) if priced else None,
                         ret=None if payout is None or grade == "pending" else payout - 1 if grade == "win" else -1.0 if grade == "loss" else 0.0))
    settled = [r for r in rows if r["grade"] in ("win", "loss")]
    out = dict(rows=rows, settled=len(settled), wins=sum(r["grade"] == "win" for r in settled))
    legs = sorted({k for r in settled for k in r["legs"]})
    out["distinct_legs"], out["distinct_games"] = len(legs), len({(idx[k]["sport"], idx[k]["game_id"]) for k in legs})
    out["leg_slots"] = sum(len(r["legs"]) for r in settled)
    out["max_multiplicity"] = max(Counter(k for r in settled for k in r["legs"]).values()) if settled else 0
    # Overlap-aware reference distribution: legs are independent across games, each wins with the stated probability;
    # a ticket wins when all its legs win. Shared legs make tickets win or lose together, as they really do.
    for name, key in (("model", "conf"), ("market", "mkt_pick")):
        use = [r for r in settled if all(idx[k][key] is not None for k in r["legs"])]
        if not use:
            out["sim_" + name] = None
            continue
        ls = sorted({k for r in use for k in r["legs"]})
        pos = {k: i for i, k in enumerate(ls)}
        probs = np.array([idx[k][key] for k in ls])
        draws = rng.random((sims, len(ls))) < probs
        wins = np.zeros(sims)
        ret = np.zeros(sims)
        for r in use:
            w = draws[:, [pos[k] for k in r["legs"]]].all(1)
            wins += w
            if r["payout"] is not None:
                ret += np.where(w, r["payout"] - 1, -1.0)
        obs = sum(r["grade"] == "win" for r in use)
        res = dict(tickets=len(use), observed_wins=obs, expected_wins=float(wins.mean()), p_at_least=float((wins >= obs).mean()), p_at_most=float((wins <= obs).mean()))
        if all(r["payout"] is not None for r in use):
            obs_ret = sum(r["ret"] for r in use)
            res.update(observed_return=obs_ret, expected_return=float(ret.mean()), p_return_at_least=float((ret >= obs_ret - 1e-9).mean()),
                       return_interval=(float(np.percentile(ret, 2.5)), float(np.percentile(ret, 97.5))))
        out["sim_" + name] = res
    return out


# ---- report ----------------------------------------------------------------------------------------------------------
def binom_two_sided(k, n):
    """Exact two-sided sign-test p-value of k successes in n against 50%."""
    if n == 0:
        return None
    hi = max(k, n - k)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(hi, n + 1)) / 2 ** n)


def closing_section(w, path, as_of):
    """Section 5b: was the price at pick time better or worse than the last stored price before the start? Facts from the
    append-only records of scripts/closing_value.py (rows recorded after as_of are ignored). A statement about direction
    needs MIN_SELECTION_N picks whose price changed (better + worse), the same convention as section 5."""
    w("## 5b. Closing price check: pick-time price against the last price before the start")
    w("")
    rows, damaged, seen = [], 0, set()
    if os.path.exists(path):
        for x in open(path):
            if not x.strip():
                continue
            try:
                r = json.loads(x)
            except ValueError:      # a line cut off by a crash: counted, never read
                damaged += 1
                continue
            # one row per key (the collector never writes a key twice; this guards the report anyway), and a ticket
            # forecast used on several tickets counts once: the sign test assumes independent picks
            if not isinstance(r, dict) or not r.get("key") or not r.get("sport"):
                damaged += 1
                continue
            ident = (r["sport"], r["forecast_id"]) if r.get("kind") == "ticket_leg" and r.get("forecast_id") is not None else r["key"]
            if r.get("recorded_at", "") <= as_of and ident not in seen:
                seen.add(ident)
                rows.append(r)
    if not rows:
        w(f"No records yet (`data/closing-value/records.jsonl` is written daily by `scripts/closing_value.py` once games have started).")
        w("")
        return
    w("Each ticket leg and each game card's recommended pick, once its game has started: the stored DraftKings price (and line) "
      "when the pick was made against the last stored DraftKings price before the start. **Better** = a better line, or the same line "
      "paying more; **worse** = the opposite; **same** = identical. Ticket legs use the price printed on the ticket (older tickets: the "
      "stored quote when they were sent); cards use the stored quote when the card was sent. Facts about prices only: not a profit "
      f"or skill claim. A resent card counts once (its first send); a forecast used on several tickets counts once. "
      f"A direction statement needs {MIN_SELECTION_N} picks whose price changed; each row is tested on its own (no correction "
      "for testing several rows), so one p below 0.05 among many rows can be chance.")
    w("")
    head = ["Kind", "Sport", "Market", "Recorded", "Better", "Same", "Worse", "Sign test p (better vs worse)", "Market prob. change at close (mean)", "Reading"]
    out = []
    groups = defaultdict(list)
    for r in rows:
        if r["status"] == "recorded":
            groups[(r["kind"], r["sport"], r["market"])].append(r)
    for (kind, sport, market), rs in sorted(groups.items()):
        c = Counter(r["draftkings"] for r in rs)
        moved = c["better"] + c["worse"]
        p = binom_two_sided(c["better"], moved)
        mean = sum(r["close_market_prob"] - r["pick_market_prob"] for r in rs) / len(rs)
        reading = (f"too small ({moved} of {MIN_SELECTION_N} changed prices)" if moved < MIN_SELECTION_N else
                   "more often better" if p < 0.05 and c["better"] > c["worse"] else
                   "more often worse" if p < 0.05 else "not distinguishable")
        out.append((kind.replace("_", " "), sport.upper(), market, len(rs), c["better"], c["same"], c["worse"], num(p, 3), sgn(mean, 4), reading))
    w(table(head, out))
    w("")
    if damaged:
        w(f"{damaged} damaged line(s) in the records file were skipped.")
        w("")
    other = Counter((r["kind"], r["status"]) for r in rows if r["status"] != "recorded")
    if other:
        w("Not compared: " + "; ".join(f"{n} {k.replace('_', ' ')} ({s.replace('_', ' ')})" for (k, s), n in sorted(other.items())) + ".")
        w("")
    books = Counter()
    for r in rows:
        for book, verdict in (r.get("books") or {}).items():
            books[(book, verdict)] += 1
    if books:
        w("Same comparison at the three sportsbooks (TheRundown prices saved within three hours before pick time and before the start, same line): "
          + "; ".join(f"{b}: better {books[(b, 'better')]}, same {books[(b, 'same')]}, worse {books[(b, 'worse')]}"
                      for b in sorted({b for b, _ in books})) + ".")
    else:
        w("Three-sportsbook comparison: no pick yet has saved TheRundown prices at both moments (collection began Nov 10, 2026).")
    w("")
    w("A positive mean market-probability change means the sportsbook rated the picked side more likely at the close than at pick time.")
    w("")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--ledger", default=None, help="default: <root>/.telegram-delivery.sqlite")
    ap.add_argument("--snapshot-dir", default=None, help="where the read-only copies go (default: a new temporary directory)")
    ap.add_argument("--reuse-snapshot", action="store_true", help="analyse existing copies in --snapshot-dir instead of copying again")
    ap.add_argument("--as-of", default=None, help="ignore everything recorded after this UTC time (default: now)")
    ap.add_argument("--out", default=None, help="Markdown report path (default: stdout)")
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20261109)
    a = ap.parse_args()
    as_of = a.as_of or datetime.now(timezone.utc).strftime(FMT)
    rng = np.random.default_rng(a.seed)
    B = a.boot
    snap = a.snapshot_dir or tempfile.mkdtemp(prefix="value-audit-")
    ledger_src = a.ledger or os.path.join(a.root, ".telegram-delivery.sqlite")
    if a.reuse_snapshot:
        paths = {n: os.path.join(snap, "data", n + ".sqlite") for n in SPORTS + ("soccer", "ledger")}
        paths = {n: p if os.path.exists(p) else None for n, p in paths.items()}
    else:
        paths = snapshot_stores(a.root, ledger_src, snap)
    tickets_dir = os.path.join(snap, "data", "reports", "tickets") if a.reuse_snapshot and os.path.isdir(os.path.join(snap, "data", "reports", "tickets")) \
        else os.path.join(a.root, "data", "reports", "tickets")
    stores = {s: Store(s, paths[s], as_of) for s in SPORTS if paths.get(s)}
    L = []
    w = L.append

    w("# Value audit: forecasts and AI ticket selection")
    w("")
    w(f"Generated by `scripts/value_audit.py` · evidence as of **{as_of}** · bootstrap {B} resamples, seed {a.seed}.")
    w("")
    w("Read-only audit of stored records. Nothing was trained, tuned, deployed or sent, and no rule was fitted to these results. "
      "The stores were copied with the SQLite backup API and only the copies were read. "
      "\"Return\" always means a hypothetical one-unit flat stake at the stored DraftKings price; no actual bet, stake or profit is recorded anywhere "
      "(`personal_tickets` is empty), so **actual betting profit is unknown and is not inferred**.")
    w("")
    w("Conventions fixed before looking at results: intervals are 95% (Wilson for rates; bootstrap over games, or over dates for backtests, for differences). "
      f"A calibration or accuracy statement needs {MIN_CALIBRATION_N} graded forecasts, a return statement {MIN_RETURN_N} priced picks, "
      f"a selected-versus-excluded statement {MIN_SELECTION_N} settled selected legs. Below that the report says the sample cannot support a conclusion, whatever the point estimate.")
    w("")

    # ---------- inventory
    w("## 1. What evidence exists")
    w("")
    inv = []
    canon = {}
    for sp, st in stores.items():
        canon[sp] = canonical(st)
        bt = st.c.execute("SELECT COUNT(*), COUNT(DISTINCT game_id), MIN(cutoff_at), MAX(cutoff_at) FROM predictions WHERE run_type='backtest'").fetchone()
        q = st.c.execute("SELECT COUNT(*), COUNT(DISTINCT game_id), MIN(retrieved_at) FROM market_quotes WHERE retrieved_at<=?", (as_of,)).fetchone()
        rc = st.c.execute("SELECT COUNT(*), COUNT(DISTINCT game_id) FROM recovered_forecasts").fetchone()
        ad = st.c.execute("SELECT COUNT(*), COUNT(DISTINCT game_id) FROM adaptive_forecasts WHERE status='forecast' AND generated_at<=?", (as_of,)).fetchone()
        cs = canon[sp]
        inv.append((sp.upper(), f"{bt[0]} rows / {bt[1]} games" + (f" ({bt[2][:10]} → {bt[3][:10]})" if bt[0] else ""),
                    f"{len(cs)} game-markets / {len({r['game_id'] for r in cs})} games" + (f" ({min(r['date'] for r in cs)} → {max(r['date'] for r in cs)})" if cs else ""),
                    f"{ad[0]} rows / {ad[1]} games", f"{rc[0]} rows / {rc[1]} games", f"{q[0]} quotes / {q[1]} games" + (f" (from {q[2][:10]})" if q[0] else "")))
    w(table(["Sport", "BACKTEST winner predictions", "LIVE canonical forecasts (started games)", "LIVE adaptive (research)", "RECONSTRUCTED", "Bookmaker quotes"], inv))
    w("")
    if paths.get("soccer"):
        sc = ro(paths["soccer"])
        n = sc.execute("SELECT COUNT(*), MIN(cutoff_at) FROM soccer_live_forecasts WHERE generated_at<=?", (as_of,)).fetchone()
        done = sc.execute("SELECT COUNT(*) FROM soccer_live_forecasts f JOIN soccer_matches m USING(match_id) WHERE m.status='final' AND f.generated_at<=?", (as_of,)).fetchone()[0]
        w(f"Soccer: {n[0]} live forecasts stored, earliest kickoff cutoff {n[1]}; {done} have a final result. **No soccer evidence can be evaluated yet.**")
        w("")
    w("- Bookmaker prices are stored only from the start of October 2026 (this season) onward, for winner, spread and total. Team totals and the NHL regulation three-way market have **no stored price**, "
      "so neither a sportsbook comparison nor a return can be computed for them.")
    w("- Backtest seasons therefore have **no sportsbook comparison at all**: backtests are compared only with simple baselines.")
    w("- Live canonical forecasts are made inside the two hours before the start; nothing here speaks to earlier prices.")
    w("")

    # ---------- backtest
    w("## 2. BACKTEST evidence (winner only; no prices exist for these seasons)")
    w("")
    w("Probabilities stored with `run_type='backtest'`. They were computed after the games; whether each used only earlier results is the app's claim "
      "(walk-forward) and was **not re-verified here**. Baselines stored alongside: a constant 50% and the running home-win rate. "
      "Δ Brier is model minus baseline (negative = model better), interval from resampling dates.")
    w("")
    bt_main = {}
    for sp, st in stores.items():
        bt = backtest(st, B, rng)
        if not bt["models"]:
            w(f"**{sp.upper()}**: no backtest predictions stored.")
            w("")
            continue
        w(f"**{sp.upper()}** — all rows generated after their game: {bt['after_games']}.")
        w("")
        rows = []
        for m in bt["models"]:
            vh, vc = m.get("vs_home_rate"), m.get("vs_constant")
            rows.append((m["model"][:58], m["n"], f"{m['first']} → {m['last']}", pct(m["accuracy"]), num(m["brier"]), num(m["log_loss"]),
                         "—" if not vc else f"{sgn(vc[0])} {ci(vc[1], vc[2])}", "—" if not vh else f"{sgn(vh[0])} {ci(vh[1], vh[2])}"))
        w(table(["Model", "Games", "Period", "Accuracy", "Brier", "Log loss", "Δ Brier vs 50%", "Δ Brier vs home rate"], rows))
        w("")
        main_m = next((m for m in bt["models"] if "elo" in m["model"] and "platt" not in m["model"] and "logistic" not in m["model"]), None)
        bt_main[sp] = main_m
        if main_m:
            w(f"Primary model (`{main_m['model'][:40]}`) by season: " +
              "; ".join(f"{s}: {pct(acc)} of {n} (Brier {num(b)})" for s, n, acc, b in main_m["by_season"]) + ".")
            w("")
            w(table(["Pick probability", "Games", "Mean stated", "Won", "Gap"],
                    [(f"{pct(lo, 0)}–{pct(min(hi, 1), 0)}", n, pct(mc), pct(hr), "—" if hr is None else f"{100 * (hr - mc):+.1f} pts")
                     for lo, hi, n, mc, hr in main_m["calibration"]]))
            w("")

    # ---------- live canonical
    w("## 3. LIVE evidence: canonical forecasts recorded before the start")
    w("")
    w("One forecast per started game and market: the latest row recorded before the start (the app's own rule). Settled with the app's `markets.settle`. "
      "Line markets are scored on P(side | no push); pushes and voids are in no rate. "
      "Sportsbook probability = the quote the forecast used (line markets, same line, retrieved no later than the forecast) or the latest moneyline retrieved "
      "no later than the forecast (winner), with the margin removed proportionally.")
    w("")
    all_canon = [r for sp in canon for r in canon[sp]]
    mism = sum(1 for r in all_canon if r.get("devig_matches_store") is False)
    w(f"Stored de-vigged probabilities were recomputed from the stored prices: {mism} mismatches in {sum(1 for r in all_canon if r['mkt_pa'] is not None)} quotes used.")
    w("")
    acc_rows, mk_rows, ret_rows = [], [], []
    live_summary = {}
    cov_lines = []
    for sp, st in list(stores.items()) + [("all", None)]:
        if st is not None:
            hp = home_baseline(st)
            r3 = reg3_baseline(st) if sp == "nhl" else None
            tot, have = coverage_gap(st, canon[sp])
            cov_lines.append(f"{sp.upper()}: {have} of {tot} finished competitive games since recording began have a canonical forecast ({tot - have} missing)")
            mkts = [m for m in ("winner", "spread", "total", "team_total_home", "team_total_away", "reg3") if any(r["market"] == m for r in canon[sp])]
            pool = canon[sp]
        else:
            hp, r3, mkts, pool = (None, 0, None), None, ["winner", "spread", "total"], all_canon
        for m in mkts + ["two-way pooled"]:
            recs = [r for r in pool if (r["market"] == m if m != "two-way pooled" else r["market"] != "reg3")]
            b = score_block(recs, B, rng, home_p=hp[0] if m == "winner" else None, reg3_p=r3 if m == "reg3" else None)
            live_summary[(sp, m)] = b
            if not b["graded"]:
                acc_rows.append((sp.upper(), m, b["records"], 0, "—", "—", "—", "—", "—", "—", str(b["grades"])))
                continue
            base = b.get("vs_coin") or b.get("vs_uniform")
            extra = b.get("vs_home_rate") or b.get("vs_prior_rates")
            others = {k: v for k, v in b["grades"].items() if k not in ("win", "loss")}
            acc_rows.append((sp.upper(), m, b["records"], b["graded"], f"{pct(b['accuracy'])} {pci(b['accuracy_ci'])}", pct(b["mean_conf"]),
                             "—" if b.get("z") is None else f"{b['z']:+.2f}", num(b["brier"]),
                             f"{sgn(base[0])} {ci(base[1], base[2])}", "—" if not extra else f"{sgn(extra[0])} {ci(extra[1], extra[2])}",
                             ", ".join(f"{k} {v}" for k, v in sorted(others.items())) or "—"))
            if b.get("matched"):
                v = b["vs_market"]
                mk_rows.append((sp.upper(), m, b["matched"], b["graded"] - b["matched"], num(b["brier_model_matched"]), num(b["brier_market"]),
                                f"{sgn(v[0])} {ci(v[1], v[2])}", verdict(v[1], v[2], b["matched"], MIN_CALIBRATION_N, words=MODEL_WORDS),
                                pct(b["mean_overround"], 2)))
            elif m != "two-way pooled":
                mk_rows.append((sp.upper(), m, 0, b["graded"], "—", "—", "—", "no stored price: cannot be assessed", "—"))
            rb = returns_block(recs, B, rng)
            live_summary[(sp, m, "ret")] = rb
            if rb["priced"]:
                r0, e, ne = rb["roi"], rb["roi_edge"], rb["roi_noedge"]
                ret_rows.append((sp.upper(), m, rb["priced"], f"{pct(r0[0])} {ci(r0[1], r0[2], pct, 1)}", pct(-rb["theoretical_hold"]),
                                 rb["edge_n"], "—" if not e else f"{pct(e[0])} {ci(e[1], e[2], pct, 1)}",
                                 "—" if not ne else f"{pct(ne[0])} {ci(ne[1], ne[2], pct, 1)}", verdict(r0[1], r0[2], rb["priced"], MIN_RETURN_N, False, RET_WORDS)))
            elif m != "two-way pooled":
                ret_rows.append((sp.upper(), m, 0, "—", "—", 0, "—", "—", "no stored price: not calculable"))
        if st is not None and hp[0]:
            w(f"{sp.upper()} home-rate baseline: {pct(hp[0])} (regular seasons {hp[2][0]}–{hp[2][1]}, {hp[1]} games, fixed before this season)."
              + (f" Regulation three-way prior rates: home {pct(r3[0])}, draw {pct(r3[1])}, away {pct(r3[2])}." if r3 else ""))
    w("")
    w("Coverage (a missing forecast is a gap, not a loss; missed games are outside every rate below): " + "; ".join(cov_lines) + ".")
    w("")
    w("### 3a. Accuracy and calibration")
    w("")
    w(table(["Sport", "Market", "Forecasts", "Graded", "Pick won (95% CI)", "Mean stated", "z (won − expected)", "Brier", "Δ Brier vs 50% / uniform",
             "Δ Brier vs home rate / prior rates", "Not graded"], acc_rows))
    w("")
    w("z compares picks won with the number the stated probabilities imply; |z| > 2 indicates miscalibration in the large. "
      "The pooled rows count several markets of one game (they share one score distribution): their intervals resample games, but their z treats picks as independent and is overstated. "
      "ALL rows pool the sports. Many rows are tested at once, so an isolated interval that just excludes zero is weak evidence.")
    w("")
    w("Calibration by stated pick probability, two-way markets pooled:")
    w("")
    for sp in stores:
        bins = calibration_bins(canon[sp])
        w(f"**{sp.upper()}**")
        w("")
        w(table(["Stated", "Picks", "Mean stated", "Won (95% CI)", "Gap"],
                [(f"{pct(lo, 0)}–{pct(hi, 0)}", n, pct(mc), "—" if hr is None else f"{pct(hr)} {pci(c)}", "—" if hr is None else f"{100 * (hr - mc):+.1f} pts")
                 for lo, hi, n, mc, hr, c in bins]))
        w("")
    w("### 3b. Against the sportsbook's margin-free probability (same games, same line, same time)")
    w("")
    ages = [r["quote_age_min"] for r in all_canon if r.get("quote_age_min") is not None]
    if ages:
        w(f"Age of the quote when the forecast was recorded: median {np.median(ages):.0f} minutes, 95th percentile {np.percentile(ages, 95):.0f}, maximum {max(ages):.0f}.")
        w("")
    w(table(["Sport", "Market", "Matched", "Unmatched", "Brier model", "Brier sportsbook", "Δ Brier model − sportsbook (95% CI)", "Reading", "Mean margin"], mk_rows))
    w("")
    w("### 3c. Hypothetical flat-stake return at the stored price (one unit on every canonical pick)")
    w("")
    w("\"Model > price\" = picks whose stated probability exceeds the break-even probability of the stored price (threshold zero, not tuned). "
      "The margin column is what a bettor with no information would expect to lose at these prices.")
    w("")
    w(table(["Sport", "Market", "Priced picks", "Return per unit (95% CI)", "No-information expectation", "Model > price: n", "Model > price: return",
             "Model ≤ price: return", "Reading (vs zero)"], ret_rows))
    w("")
    unp = Counter()
    for sp in canon:
        for r in canon[sp]:
            if r["price"] is None and r["grade"] in ("win", "loss", "push"):
                unp[(sp.upper(), r["market"], r["why_unpriced"])] += 1
    w("Settled canonical picks with no usable price: " + "; ".join(f"{s} {m}: {n} ({why})" for (s, m, why), n in sorted(unp.items())) + ".")
    w("")

    # ---------- adaptive + reconstructed
    w("### 3d. LIVE research forecasts (adaptive ensemble; never canonical, never in tickets)")
    w("")
    rows = []
    for sp, st in stores.items():
        for (schedule, market), v in sorted(adaptive(st, canon[sp], B, rng).items()):
            d = v["diff"]
            rows.append((sp.upper(), schedule, market, v["graded"], v["wins"], v["paired"], f"{v['adaptive_wins_paired']} vs {v['canon_wins_paired']}",
                         "—" if not d else f"{sgn(d[0])} {ci(d[1], d[2])}", "—" if not d else verdict(d[1], d[2], v["paired"], MIN_CALIBRATION_N)))
    w(table(["Sport", "Schedule", "Market", "Graded", "Won", "Paired with canonical (same line)", "Wins: adaptive vs canonical", "Δ Brier adaptive − canonical", "Reading"], rows) if rows else "None stored.")
    w("")
    w("## 4. RECONSTRUCTED evidence (historical, not live)")
    w("")
    rows = []
    for sp, st in stores.items():
        rc = reconstructed(st)
        for market in sorted({k[0] for k in rc}):
            g = Counter()
            for (m, grade, after), n in rc.items():
                if m == market:
                    g[grade] += n
            rows.append((sp.upper(), market, sum(g.values()), g["win"], g["loss"], ", ".join(f"{k} {v}" for k, v in g.items() if k not in ("win", "loss")) or "—"))
        if rc:
            w(f"{sp.upper()}: {sum(n for (m, g, after), n in rc.items() if after)} of {sum(rc.values())} rows were rebuilt after the game started.")
    w("")
    w(table(["Sport", "Market", "Rows", "Won", "Lost", "Other"], rows) if rows else "None stored.")
    w("")
    w("These were rebuilt after the fact for games the live cycle missed. They are listed for completeness and are **not** pooled with live evidence; the counts are too small for any rate.")
    w("")

    # ---------- selection
    w("## 5. AI ticket selection: do selected picks outperform excluded picks?")
    w("")
    if not paths.get("ledger"):
        w("Ledger not found: section not computed.")
    else:
        events, files, bad = load_reviews(tickets_dir, as_of)
        tickets, deliveries, personal = load_tickets(paths["ledger"], events, as_of)
        recs, missing = selection(stores, events, tickets, B, rng)
        with_dec = [e for e in events if e["decisions"] is not None]
        w(f"Sources: {files} review archives with a snapshot at or before the as-of time → {len(events)} distinct reviews ({sum(e['copies'] - 1 for e in events)} duplicate copies; "
          f"{bad} other JSON files in the folder, such as settlements and format tests, carry no review), "
          f"{events and min(e['as_of'] for e in events)} → {events and max(e['as_of'] for e in events)}; {len(with_dec)} reviews carry a per-forecast candidate/excluded decision (rule introduced 8 Nov), "
          f"{len(events) - len(with_dec)} earlier ones only record what was selected. Ledger: {len(tickets)} graded tickets, {deliveries[1]} of {deliveries[0]} ticket deliveries marked sent; "
          f"{personal} user-placed slips recorded.")
        w("")
        w("Unit of analysis: one immutable forecast ID, counted once however many reviews or tickets it appears in. Pool = forecasts that were eligible "
          "(scheduled, inside the two-hour window) in at least one archived review. Each is graded on its own stored line. "
          "Price and sportsbook probability are taken from the stored quote at the time the ticket was sent (selected) or first reviewed (others); "
          "a pick is unpriced when the latest quote by then was at a different line.")
        w("")
        if missing:
            w(f"Note: {len(missing)} selected legs were not found as eligible picks in any archived snapshot ({', '.join(f'{s}:{i}' for s, i in missing)}); they are graded from the forecast ledger.")
            w("")
        chk = [(t["ticket_id"], k, g) for t in tickets for k, g in t["stored_legs"].items()]
        idx = {(r["sport"], r["forecast_id"]): r for r in recs}
        agree = sum(1 for _, k, g in chk if k in idx and (idx[k]["grade"] == g or (g in ("pending", "unresolved") and idx[k]["grade"] in ("pending",))))
        diff = [(tid, k, g, idx[k]["grade"]) for tid, k, g in chk if k in idx and idx[k]["grade"] != g]
        w(f"Cross-check of this audit's grading against the ledger's stored leg grades: {agree} of {len(chk)} agree"
          + ("." if not diff else "; differences (ledger → audit, the audit uses results known at the as-of time): " + ", ".join(f"{k[0]}:{k[1]} {g}→{h}" for _, k, g, h in diff[:12]) + "."))
        w("")
        classes = ["selected", "candidate, not selected", "excluded", "reviewed, no per-forecast decision (before Nov 8 rule)"]
        rows = []

        def row(label, rs):
            g = group_stats(rs, B, rng)
            if not g["graded"]:
                return (label, g["n"], g["games"], 0, "—", "—", "—", "—", "—", "—", ", ".join(f"{k} {v}" for k, v in g["other"].items()) or "—")
            rm, rk, roi = g["resid_model"], g["resid_market"], g["roi"]
            return (label, g["n"], g["games"], g["graded"], f"{g['wins']}/{g['graded']} = {pct(g['rate'])} {pci(g['ci'])}", pct(g["mean_conf"]),
                    f"{100 * rm[0]:+.1f} pts {ci(100 * rm[1] if rm[1] is not None else None, 100 * rm[2] if rm[2] is not None else None, sgn, 1)}",
                    "—" if not rk else f"{pct(g['mean_mkt'])} (n={g['priced']})",
                    "—" if not rk else f"{100 * rk[0]:+.1f} pts {ci(100 * rk[1] if rk[1] is not None else None, 100 * rk[2] if rk[2] is not None else None, sgn, 1)}",
                    "—" if not roi else f"{pct(roi[0])} {ci(roi[1], roi[2], pct, 1)} (n={g['ret_n']})",
                    ", ".join(f"{k} {v}" for k, v in g["other"].items()) or "—")
        head = ["Group", "Forecasts", "Games", "Graded", "Won (95% CI)", "Mean model prob.", "Won − model prob.", "Mean sportsbook prob.", "Won − sportsbook prob.",
                "Flat return at stored price", "Not graded"]
        for c in classes:
            rows.append(row(c, [r for r in recs if r["cls"] == c]))
        for sub in ("excluded: judgement", "excluded: line moved (reason text)", "excluded: game locked to another leg"):
            rows.append(row("↳ " + sub, [r for r in recs if r["sub"] == sub]))
        rows.append(row("all not selected", [r for r in recs if not r["selected"]]))
        w("### 5a. Outcomes by review decision")
        w("")
        w(table(head, rows))
        w("")
        w("\"Excluded: game locked\" and \"line moved\" are mechanical exclusions, not judgements about quality; the line-moved flag is read from the free-text reason and is approximate.")
        w("")
        w("### 5b. Same sport and market")
        w("")
        rows = []
        for sp in SPORTS:
            for m in ("winner", "spread", "total", "team_total_home", "team_total_away", "reg3"):
                a_ = [r for r in recs if r["sport"] == sp and r["market"] == m and r["selected"] and r["scored"]]
                b_ = [r for r in recs if r["sport"] == sp and r["market"] == m and not r["selected"] and r["scored"]]
                if a_ or b_:
                    rows.append((sp.upper(), m, f"{sum(r['hit'] for r in a_)}/{len(a_)}" if a_ else "0", pct(np.mean([r["conf"] for r in a_])) if a_ else "—",
                                 f"{sum(r['hit'] for r in b_)}/{len(b_)}" if b_ else "0", pct(np.mean([r["conf"] for r in b_])) if b_ else "—"))
        w(table(["Sport", "Market", "Selected won", "Selected mean model prob.", "Not selected won", "Not selected mean model prob."], rows))
        w("")
        sd = stratified_diff(recs, B, rng)
        n_sel = sum(1 for r in recs if r["selected"] and r["scored"])
        w("### 5c. Adjusted comparison")
        w("")
        if sd is None:
            w("No stratum (sport × market × model-probability band) contains both a settled selected and a settled not-selected forecast: **no adjusted comparison is possible.**")
        else:
            w(f"Within strata of sport × market × model-probability band (50–60, 60–70, 70–80, 80+), weighted by selected legs: selected minus not-selected win rate = "
              f"**{100 * sd['diff']:+.1f} points**, 95% interval [{100 * sd['lo']:+.1f}, {100 * sd['hi']:+.1f}] (games resampled), permutation p = {sd['perm_p']:.2f} "
              f"(labels shuffled inside strata; ignores same-game correlation, so it is if anything too small). "
              f"{sd['selected_used']} of {sd['selected_total']} settled selected legs had a comparison forecast in their stratum.")
            w("")
            w(f"Reading: {verdict(sd['lo'], sd['hi'], n_sel, MIN_SELECTION_N, False, ('selected better', 'selected worse', 'not distinguishable'))}.")
        w("")
        sel = [r for r in recs if r["selected"]]
        non = [r for r in recs if not r["selected"]]

        def med(rs, k):
            v = [r[k] for r in rs if r[k] is not None]
            return "—" if not v else f"{np.median(v):.0f}"
        w(f"Timing: selected legs were referenced a median {med(sel, 'review_minutes_before')} minutes before the start (forecast made {med(sel, 'minutes_before')} minutes before); "
          f"not-selected forecasts were first reviewed a median {med(non, 'review_minutes_before')} minutes before (forecast made {med(non, 'minutes_before')} minutes before). "
          "All forecasts sit inside the same two-hour window, so timing cannot be separated from the other factors at this sample size.")
        w("")
        w("### 5d. Simple selection baselines on the same reviewed pool")
        w("")
        best = {}
        for r in recs:
            k = (r["sport"], r["game_id"])
            if k not in best or r["conf"] > best[k]["conf"]:
                best[k] = r
        rows = [row("AI-selected legs", sel), row("every reviewed forecast", recs), row("every reviewed winner pick", [r for r in recs if r["market"] == "winner"]),
                row("app flag `strong` (as archived)", [r for r in recs if r["strong"]]),
                row("highest model probability in each game", list(best.values())),
                row("sportsbook favourite, winner market (priced)", [r for r in recs if r["market"] == "winner" and r["mkt_pick"] is not None and r["mkt_pick"] >= 0.5])]
        w(table(head, rows))
        w("")
        gs = group_stats(sel, B, rng)
        beat = []
        for label, rs in (("every reviewed winner pick", [r for r in recs if r["market"] == "winner"]),
                          ("the sportsbook favourite on the winner market", [r for r in recs if r["market"] == "winner" and r["mkt_pick"] is not None and r["mkt_pick"] >= 0.5]),
                          ("the app's `strong` flag", [r for r in recs if r["strong"]])):
            g = group_stats(rs, B, rng)
            if g.get("resid_market") and gs.get("resid_market") and g["resid_market"][0] >= gs["resid_market"][0]:
                beat.append(f"{label} ({100 * g['resid_market'][0]:+.1f} pts)")
        if beat and gs.get("resid_market"):
            w(f"Relative to the sportsbook's probability the selected legs won {100 * gs['resid_market'][0]:+.1f} points more than priced in, but so did rules that use no review at all: "
              + "; ".join(beat) + ". Favourites won more often than priced in these few days, so the selected legs' record cannot be attributed to the review.")
            w("")
        w("### 5e. Tickets (parlays) and overlapping exposure")
        w("")
        tr = ticket_results(tickets, recs, rng)
        rows = []
        for t in tr["rows"]:
            rows.append((t["ticket_id"], (t["sent_at"] or "—")[5:16].replace("T", " "), " + ".join(f"{l['sport']}:{l['forecast_id']} {l['market']} ({pct(l['conf'], 0)})" for l in t["legs_r"]),
                         pct(t["p_model"]), pct(t["p_market"]) if t["p_market"] is not None else "unpriced leg", f"×{t['payout']:.2f}" if t["payout"] else "—",
                         t["grade"] + ("" if t["agrees"] else f" (ledger: {t['stored_grade']})"), "—" if t["ret"] is None else f"{t['ret']:+.2f}"))
        w(table(["Ticket", "Sent (UTC)", "Legs (model prob.)", "Model product", "Sportsbook product", "Payout at stored prices", "Grade", "Unit return"], rows))
        w("")
        pend = sum(1 for t in tr["rows"] if t["grade"] == "pending")
        w(f"{len(tr['rows'])} tickets sent: {tr['wins']} won, {tr['settled'] - tr['wins']} lost, {pend} pending. The {tr['settled']} settled tickets use {tr['leg_slots']} leg slots but only "
          f"**{tr['distinct_legs']} distinct forecasts in {tr['distinct_games']} games**; one forecast appears in up to {tr['max_multiplicity']} tickets. "
          "Tickets that share a leg win and lose together, so the ticket count overstates the independent evidence.")
        w("")
        for name, label in (("model", "the model's own probabilities"), ("market", "the sportsbook's margin-free probabilities")):
            s = tr.get("sim_" + name)
            if not s:
                w(f"Reference under {label}: not computable (a leg has no stored price).")
                continue
            txt = (f"Reference under {label} (legs independent across games, shared legs shared; {s['tickets']} tickets): expected {s['expected_wins']:.1f} winning tickets, observed {s['observed_wins']}; "
                   f"chance of at least that many = {s['p_at_least']:.2f}, of at most that many = {s['p_at_most']:.2f}.")
            if "observed_return" in s:
                txt += (f" Hypothetical one unit per ticket at stored prices: observed {s['observed_return']:+.2f} units, expected {s['expected_return']:+.2f}, "
                        f"95% of outcomes between {s['return_interval'][0]:+.1f} and {s['return_interval'][1]:+.1f}; chance of at least the observed = {s['p_return_at_least']:.2f}.")
            w(txt)
            w("")
        w("### 5f. By selection-rule version")
        w("")
        w("A version is the pair (review-instructions hash, helper-code hash) stored in each review's snapshot. Tickets are attributed to the version of the review that sent them, legs to the first ticket that used them. "
          "Batches whose own archive says the user asked for them are shown apart: there the selection was not the rule's alone. "
          "That flag is read from the archived text and is approximate; identical hashes do not guarantee an identical rule when instructions given in chat changed, and such changes leave no trace here.")
        w("")
        tix = {t["ticket_id"]: t for t in tr["rows"]}
        first_use = {}
        for t in sorted(tr["rows"], key=lambda t: (t["sent_at"] or "", t["ticket_id"])):
            for k in t["legs"]:
                first_use.setdefault(k, t)
        groups = {}
        for e in sorted(events, key=lambda e: e["as_of"]):
            g = groups.setdefault((e["version"], e["override"]), dict(first=e["as_of"], last=e["as_of"], reviews=0))
            g["last"] = e["as_of"]
            g["reviews"] += 1
        for t in tr["rows"]:
            if t["version"] is None:
                groups.setdefault((("unknown", "unknown"), False), dict(first="—", last="—", reviews=0))
        rows = []
        for (ver, ovr), g in sorted(groups.items(), key=lambda kv: kv[1]["first"]):
            ts_ = [t for t in tr["rows"] if (t["version"] or ("unknown", "unknown")) == ver and t["override"] == ovr]
            legs = [idx[k] for k, t in sorted(first_use.items()) if (t["version"] or ("unknown", "unknown")) == ver and t["override"] == ovr and k in idx]
            sl = [l for l in legs if l["scored"]]
            pm = [l["mkt_pick"] for l in sl if l["mkt_pick"] is not None]
            rows.append((ver[0], ver[1], "yes" if ovr else "no", g["first"][5:16].replace("T", " "), g["last"][5:16].replace("T", " "), g["reviews"], len(ts_),
                         f"{sum(t['grade'] == 'win' for t in ts_)} / {sum(t['grade'] == 'loss' for t in ts_)} / {sum(t['grade'] == 'pending' for t in ts_)}",
                         len(legs), f"{sum(l['hit'] for l in sl)}/{len(sl)}" if sl else "—",
                         pct(np.mean([l["conf"] for l in sl])) if sl else "—", pct(np.mean(pm)) if pm else "—"))
        w(table(["Instructions", "Helper", "User-requested", "First review (UTC)", "Last review (UTC)", "Reviews", "Tickets sent", "Tickets won / lost / pending",
                 "Legs first selected", "Settled legs won", "Mean model prob.", "Mean sportsbook prob."], rows))
        w("")
        reused = sum(1 for k, t in first_use.items() if any((o["version"], o["override"]) != (t["version"], t["override"]) for o in tr["rows"] if k in o["legs"]))
        nver = len({v for (v, _o) in groups})
        w(f"{nver} distinct versions in {len({e['as_of'][:10] for e in events})} days; the largest has "
          f"{max((sum(1 for k, t in first_use.items() if (t['version'], t['override']) == key and k in idx and idx[k]['scored']) for key in groups), default=0)} settled selected legs. "
          f"{reused} selected legs were reused by a ticket sent under a different version or override status. "
          "Forecasts that were not selected were often reviewed under several versions and are not attributed to one. "
          "**No per-version comparison is possible at these counts**; the table exists so that a later frozen rule can be separated from these earlier, changing ones.")
        w("")
        w("### 5g. Selection bias and missing data in this section")
        w("")
        w(f"- Settled selected legs: **{n_sel}** (needs {MIN_SELECTION_N}). Everything above is from {len({r['date'] for r in recs})} calendar days.")
        w("- The reviewer saw each pick's model probability, its backtest win-rate record and the current price, and preferred high-probability winner picks. "
          "Selected and excluded picks therefore differ in sport, market and probability by construction; raw win rates are not comparable, which is why 5a also shows outcomes relative to the model and sportsbook probabilities.")
        w("- Many exclusions are mechanical (game locked by an earlier ticket, line moved, no price). They are not evidence about judgement.")
        w("- Reviews exist only for the hours the workflow ran; forecasts never reviewed are not in the pool. Before 8 Nov there is no per-forecast decision, only the selected legs.")
        w("- Team totals and the NHL regulation market have no stored price, so their excluded picks have no sportsbook comparison and no return.")
        w("- The review instructions and helper code changed repeatedly during these days (5f); this is not one fixed selection rule.")
        w("- No stake, placed bet or payout is recorded. Ticket returns are hypothetical at the stored price at send time; the sportsbook's actual parlay price can differ.")
        w("")

    # ---------- closing price check (scripts/closing_value.py, append-only records)
    closing_section(w, os.path.join(a.root, "data", "closing-value", "records.jsonl"), as_of)

    # ---------- conclusions (rule-based, from the numbers above)
    w("## 6. What the evidence supports")
    w("")
    w("Each line is produced by the fixed conventions at the top; none is a judgement added afterwards.")
    w("")
    w("**Forecast accuracy and calibration (live)**")
    w("")
    for sp in list(stores) + ["all"]:
        for m in ("winner", "spread", "total", "two-way pooled"):
            b = live_summary.get((sp, m))
            if not b or not b.get("graded") or (sp != "all" and m == "two-way pooled"):
                continue
            parts = [f"{sp.upper()} {m}, {b['graded']} graded: picks won {pct(b['accuracy'])} against {pct(b['mean_conf'])} stated"]
            if m != "two-way pooled" and b.get("z") is not None and abs(b["z"]) > 2:
                parts.append(("stated probabilities too high" if b["z"] < 0 else "stated probabilities too low") + f" (z {b['z']:+.2f})"
                             + ("" if b["graded"] >= MIN_CALIBRATION_N else f", n below {MIN_CALIBRATION_N}: not a supported conclusion"))
            base = b.get("vs_coin")
            if base:
                parts.append("vs a coin flip: " + verdict(base[1], base[2], b["graded"], MIN_CALIBRATION_N, words=MODEL_WORDS))
            v = b.get("vs_market")
            if v:
                parts.append(f"vs the sportsbook's margin-free probability: {verdict(v[1], v[2], b['matched'], MIN_CALIBRATION_N, words=MODEL_WORDS)} (Δ Brier {sgn(v[0])})")
            w("- " + "; ".join(parts) + ".")
    for sp in stores:
        bt_m = bt_main.get(sp)
        b = live_summary.get((sp, "winner"))
        if bt_m and b and b.get("graded"):
            inside = b["accuracy_ci"][0] <= bt_m["accuracy"] <= b["accuracy_ci"][1]
            w(f"- {sp.upper()} winner, backtest vs live: backtest accuracy {pct(bt_m['accuracy'])} ({bt_m['n']} games) is {'inside' if inside else 'outside'} the live interval {pci(b['accuracy_ci'])}.")
    w("")
    w("**Price-based value (live, hypothetical flat stake at stored prices)**")
    w("")
    for sp in list(stores) + ["all"]:
        for m in ("winner", "spread", "total", "two-way pooled"):
            rb = live_summary.get((sp, m, "ret"))
            if not rb or not rb.get("roi") or (sp != "all" and m == "two-way pooled"):
                continue
            r0, e = rb["roi"], rb["roi_edge"]
            txt = f"- {sp.upper()} {m}, {rb['priced']} priced picks: {pct(r0[0])} {ci(r0[1], r0[2], pct, 1)} per unit, {verdict(r0[1], r0[2], rb['priced'], MIN_RETURN_N, False, RET_WORDS)}"
            if e:
                txt += f"; picks where the model exceeds the price ({rb['edge_n']}): {pct(e[0])} {ci(e[1], e[2], pct, 1)}, {verdict(e[1], e[2], rb['edge_n'], MIN_RETURN_N, False, RET_WORDS)}"
            w(txt + ".")
    w("")
    w("**Cannot be assessed from stored data**")
    w("")
    w("- Team totals (all sports) and the NHL regulation three-way market: no stored price, so no sportsbook comparison and no return.")
    w("- Any backtest against the sportsbook: no prices exist for past seasons.")
    w("- Soccer: no settled forecast. NFL: one week of games. Reconstructed forecasts: too few for any rate.")
    w("- Actual betting profit: no placed bet, stake or payout is recorded.")
    if paths.get("ledger"):
        w("")
        w("**AI ticket selection**")
        w("")
        w(f"- {n_sel} settled selected legs over {len({r['date'] for r in recs})} days, shared across overlapping tickets. "
          + ("**The sample cannot support a conclusion** about whether the review adds value over the forecasts it chooses from, in either direction."
             if n_sel < MIN_SELECTION_N else "See 5c for the adjusted comparison."))
        if sd is not None:
            w(f"- Adjusted selected-minus-not-selected difference {100 * sd['diff']:+.1f} points, interval [{100 * sd['lo']:+.1f}, {100 * sd['hi']:+.1f}]: an interval this wide is compatible with the review helping, hurting or doing nothing.")
        w("- The archive now records a candidate/excluded decision for every eligible forecast, so the comparison becomes informative as settled selected legs accumulate; re-run this script unchanged.")
    w("")
    w("## 7. Reproduce")
    w("")
    w("```sh")
    w(f"PYTHONPATH=backend backend/.venv/bin/python scripts/value_audit.py --as-of {as_of} --out docs/VALUE_AUDIT.md")
    w("```")
    w("")
    w("The script copies the stores read-only into a temporary directory, ignores every row recorded after `--as-of` "
      "(forecasts, quotes, results, reviews, grades) and uses a fixed bootstrap seed, so the same `--as-of` reproduces these numbers while the ledgers stay append-only. "
      "Requires numpy.")
    text = "\n".join(L) + "\n"
    if a.out:
        with open(a.out, "w") as fh:
            fh.write(text)
        print(f"wrote {a.out} ({len(text)} bytes); snapshot in {snap}")
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
