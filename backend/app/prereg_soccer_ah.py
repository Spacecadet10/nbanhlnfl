"""Research-only Asian handicap prices of the sealed goals model against stored DraftKings handicap quotes
(docs/preregistration/soccer-asian-handicap-research-2026-11-18.md). Section numbers below refer to that document.

Research only, never served: nothing here writes a store, a page, a card, a ticket or a message. The AH price is an exact
function of the stored goals joint grid (`soccer_market_forecasts.joint_json`) and `soccer_markets.grade_handicap`; no model is
fitted. Read-only on a COPY of the soccer store (`--data-dir`); the live data folder is refused and the report goes outside the
stores. Before the single look (800 decided paired matches or kickoff date 2027-05-31) only counts are produced; the look is
computed once and stored by the frozen document's sha256 in the looks folder (not in the report folder).

Written and tested on synthetic data only; independent review (section 10) comes before any counted outcome is read.

  python -m app.prereg_soccer_ah --data-dir COPY --out REPORTDIR
"""
import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import numpy as np

from . import odds, prereg_common as pc, soccer_markets as sm, soccer_markets_eval as ev
from .soccer_book import app_share

PREREG_FILE = "soccer-asian-handicap-research-2026-11-18.md"
PREREG_SHA256 = "a3e1fc3b60ffb9316432213fc66567bcc8b269548576ce299e88e06ec031b2b4"
ADDENDA_SHA256 = {"addendum-2026-11-18-soccer-evaluator-clarifications.md": "a85ad1313f59a9578e0043484ca54ef0ea82c1cc47c63b44786a777e2ffa8a53"}
CANDIDATE_SHA256 = "cfe78bf98a8e48215474de26d020fc5a0fdb45830957b38e24e47db88efbd432"
REPO_ROOT = pc.REPO_ROOT
T0 = "2026-11-18T14:25:37Z"              # committer timestamp of 1afd91b (the commit that first added the document)
COMPETITIONS = ("eng.1", "esp.1", "ger.1", "ita.1", "fra.1", "uefa.champions")
STOP_N, STOP_DATE, MIN_N_VERDICT, TYPE_MIN = 800, "2027-05-31", 300, 100
PROVIDER = "DraftKings"
PAST = timedelta(days=7)
WAITING = "waiting: an earlier match is neither final nor past 7 days"
VERDICTS = ("app better than DraftKings", "DraftKings better than the app", "not shown")


def check_document() -> None:
    pc.check_prereg(PREREG_FILE, PREREG_SHA256, REPO_ROOT)
    pc.check_addenda(ADDENDA_SHA256, REPO_ROOT)
    from . import soccer_markets_live
    if soccer_markets_live.CANDIDATES["goals"] != CANDIDATE_SHA256:
        raise pc.Refused("the served goals candidate differs from the pinned candidate sha256")


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- section 3: pricing and scoring ---------------------------------------------------------------------------------------
def handicap_probs(joint, line: float, side: str = "home") -> dict:
    """{win, half_win, push, half_loss, loss} of the side at its own `line` under the joint count grid."""
    out = {r: 0.0 for r in sm.RESULTS}
    for h, row in enumerate(joint):
        for a, p in enumerate(row):
            if p:
                out[sm.grade_handicap(h, a, line, side)] += float(p)
    total = sum(out.values())
    return {k: v / total for k, v in out.items()} if total > 0 else out


def score(result: str):
    """(weight, y): win/loss 1, half results 1/2, push 0 (scored as nothing)."""
    return {"win": (1.0, 1), "half_win": (0.5, 1), "push": (0.0, None), "half_loss": (0.5, 0), "loss": (1.0, 0)}[result]


def line_type(line: float) -> str:
    frac = round(abs(line) * 4) % 4
    return "whole" if frac == 0 else "half" if frac == 2 else "quarter"


def line_bin(line: float) -> str:
    """|line| bins 0.5 / 1.5 / >=2.5; a whole or quarter line goes in the bin of the nearest half line below it (a line under
    0.5 goes in the 0.5 bin)."""
    import math
    half = math.floor(abs(line) - 0.5 + 1e-9) + 0.5 if abs(line) >= 0.5 else 0.5
    return "0.5" if half < 1.0 else "1.5" if half < 2.0 else ">=2.5"


def power_devig(american_a: int, american_b: int):
    """Section 2 sensitivity (best-pick v4 section 6): q_i = r_i^k with k solved by bisection on [0.5, 5] to within 1e-12 of
    sum 1 (at most 200 iterations); None without a sign change or without convergence."""
    r = [odds.american_to_implied(american_a), odds.american_to_implied(american_b)]
    f = lambda k: r[0] ** k + r[1] ** k - 1       # noqa: E731
    lo, hi = 0.5, 5.0
    if f(lo) * f(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if abs(f(mid)) <= 1e-12:
            return r[0] ** mid, r[1] ** mid
        if f(lo) * f(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return None


# --- section 2: quotes -----------------------------------------------------------------------------------------------------------
def pair_at(conn: sqlite3.Connection, match_id: str, at: str, kickoff_at: str, provider: str = PROVIDER) -> dict:
    """The DraftKings handicap pair as of `at`: per side the latest 'current' row retrieved at or before `at` and before kickoff
    (the per-side rule of soccer_book.latest_1x2). paired / unpaired / no_quote."""
    rows = {}
    for side in ("home", "away"):
        rows[side] = conn.execute("SELECT * FROM soccer_odds_quotes WHERE match_id = ? AND market = 'handicap' AND side = ? AND "
                                  f"{odds.same_provider('provider')} AND price_kind = 'current' AND retrieved_at <= ? AND retrieved_at < ? "
                                  "ORDER BY quote_id DESC LIMIT 1", (match_id, side, provider, at, kickoff_at)).fetchone()
    if rows["home"] is None and rows["away"] is None:
        return {"status": "no_quote"}
    h, a = rows["home"], rows["away"]
    if h is None or a is None or not h["offered"] or not a["offered"]:
        return {"status": "unpaired"}
    lh, la = h["line"], a["line"]
    if lh is None or la is None or abs(lh + la) >= 1e-9 or abs(lh * 4 - round(lh * 4)) > 1e-9:
        return {"status": "unpaired"}
    return {"status": "paired", "line_home": lh, "american_home": h["american"], "american_away": a["american"],
            "decimal_home": h["decimal_odds"], "decimal_away": a["decimal_odds"], "quote_ids": [h["quote_id"], a["quote_id"]]}


# --- collection ---------------------------------------------------------------------------------------------------------------------
class Collected:
    def __init__(self):
        self.units = []
        self.counts = {"forecasts": 0, "no_goals_forecast": 0, "not_pinned_candidate": 0, "knockout_excluded": 0, "not_regular_season": 0, "forecast_before_t0": 0,
                       "after_stop_date": 0, "no_quote": 0, "unpaired": 0, "paired": {"half": 0, "whole": 0, "quarter": 0},
                       "decided_paired": 0, "push": 0, "void": 0, "unknown_ending": 0, "pending": 0, "past7": 0, "close_line_moved": 0,
                       "close_no_pair": 0}


def _state(m, now: datetime) -> str:
    if m["status"] in ("postponed", "canceled", "abandoned"):
        return "void"
    if m["status"] == "final" and (m["result_available_at"] or "9999") <= _ts(now):
        return "final" if m["decided_in"] == "regulation" else "unknown_ending"
    return "past7" if now >= _parse(m["kickoff_at"]) + PAST else "pending"


def collect(conn: sqlite3.Connection, *, now: datetime, outcomes: bool = True) -> Collected:
    """With `outcomes=False` (before the look) no result and no close is read: forecasts, pairs at t_d and line types only."""
    out, n = Collected(), None
    n = out.counts
    season = conn.execute("SELECT MAX(season) FROM soccer_matches").fetchone()[0]
    if season is None:
        return out
    main, rules = ev._main_types(conn, season), ev.stage_rules()
    q = ",".join("?" * len(COMPETITIONS))
    for m in conn.execute(f"SELECT * FROM soccer_matches WHERE competition_id IN ({q}) AND kickoff_at > ? ORDER BY kickoff_at, match_id",
                          (*COMPETITIONS, T0)).fetchall():
        if ev.local_date(m["kickoff_at"], m["competition_id"]) > STOP_DATE:
            n["after_stop_date"] += 1
            continue
        if not ev.eligible_stage(m["competition_id"], m["season"], m["season_type_id"], m["stage"], main, rules):
            n["knockout_excluded" if m["competition_id"].startswith("uefa.") else "not_regular_season"] += 1
            continue
        fcs = conn.execute("SELECT * FROM soccer_market_forecasts WHERE match_id = ? AND family = 'goals' ORDER BY version", (m["match_id"],)).fetchall()
        pinned = [f for f in fcs if f["candidate_sha256"] == CANDIDATE_SHA256]
        if not pinned:
            n["not_pinned_candidate" if fcs else "no_goals_forecast"] += 1
            continue
        f = pinned[-1]
        if f["generated_at"] < T0:
            n["forecast_before_t0"] += 1
            continue
        n["forecasts"] += 1
        td = pair_at(conn, m["match_id"], f["generated_at"], m["kickoff_at"])
        if td["status"] != "paired":
            n[td["status"]] += 1
            continue
        L = td["line_home"]
        n["paired"][line_type(L)] += 1
        if not outcomes:
            continue
        joint = json.loads(f["joint_json"])
        p_home = app_share(handicap_probs(joint, L))
        q_home = odds.devig_two_way(td["american_home"], td["american_away"])[0]
        pw = power_devig(td["american_home"], td["american_away"])
        state = _state(m, now)
        u = {"match_id": m["match_id"], "competition": m["competition_id"], "kickoff_at": m["kickoff_at"],
             "cluster": f"{m['competition_id']}|{ev.local_date(m['kickoff_at'], m['competition_id'])}", "p_home": p_home, "q_home": q_home,
             "q_home_power": pw[0] if pw else None, "line_home": L, "line_type": line_type(L), "w": None, "y": None,
             "result_home": None, "result_away": None, "decimal_home": td["decimal_home"], "decimal_away": td["decimal_away"],
             "close_status": "no_close", "q_close_home": None, "clv": None}
        if state == "final":
            rh = sm.grade_handicap(m["home_score"], m["away_score"], L, "home")
            u["result_home"], u["result_away"] = rh, sm.grade_handicap(m["home_score"], m["away_score"], -L, "away")
            w, y = score(rh)
            if w == 0:
                state = "push"
            else:
                u["w"], u["y"], state = w, y, "decided"
        n[{"decided": "decided_paired"}.get(state, state)] += 1
        u["state"] = state
        cl = pair_at(conn, m["match_id"], _ts(_parse(m["kickoff_at"]) - timedelta(seconds=1)), m["kickoff_at"])
        if cl["status"] != "paired":
            n["close_no_pair"] += 1
        elif abs(cl["line_home"] - L) > 1e-9:
            u["close_status"] = "line_moved"
            n["close_line_moved"] += 1
        else:
            qc = odds.devig_two_way(cl["american_home"], cl["american_away"])[0]
            u["close_status"], u["q_close_home"] = "ok", qc
            u["clv"] = (qc - q_home) if p_home >= q_home else (1 - qc) - (1 - q_home)
        out.units.append(u)
    return out


# --- section 5: metrics ----------------------------------------------------------------------------------------------------------------------
def _scored(units: list) -> list:
    return [x for x in units if x.get("w") and x.get("y") is not None]


def metrics(units: list) -> dict:
    us = _scored(units)
    W = sum(x["w"] for x in us)
    if not us:
        return {"n": 0, "weight": 0.0}
    ll = lambda p, y: pc.log_loss(p, y)      # noqa: E731
    out = {"n": len(us), "weight": W,
           "log_loss_app": sum(x["w"] * ll(x["p_home"], x["y"]) for x in us) / W,
           "log_loss_book": sum(x["w"] * ll(x["q_home"], x["y"]) for x in us) / W,
           "brier_app": sum(x["w"] * pc.brier(x["p_home"], x["y"]) for x in us) / W,
           "brier_book": sum(x["w"] * pc.brier(x["q_home"], x["y"]) for x in us) / W}
    out["delta_ll"] = out["log_loss_app"] - out["log_loss_book"]
    a, b = pc.logistic_fit([x["p_home"] for x in us], [x["y"] for x in us], [x["w"] for x in us])
    out["calibration"] = {"intercept": a, "slope": b}
    bins = [[] for _ in range(5)]
    for x in us:
        bins[min(4, int(x["p_home"] * 5 + 1e-12))].append(x)
    out["reliability"] = [{"n": len(g), "weight": sum(x["w"] for x in g),
                           "mean_p": sum(x["w"] * x["p_home"] for x in g) / sum(x["w"] for x in g) if g else None,
                           "mean_y": sum(x["w"] * x["y"] for x in g) / sum(x["w"] for x in g) if g else None} for g in bins]
    return out


def flat_stake(units: list) -> dict:
    """Descriptive only: 1 unit on the side with app_share - q >= 0 (home ties) at DraftKings' decimal price at t_d."""
    rets = []
    for x in units:
        if x.get("result_home") is None:
            continue
        home = x["p_home"] - x["q_home"] >= 0
        rets.append(sm.return_factor(x["result_home"] if home else x["result_away"], x["decimal_home"] if home else x["decimal_away"]))
    return {"n": len(rets), "mean_return": sum(rets) / len(rets) if rets else None}


def _cluster_arrays(units: list, value):
    clusters: dict = {}
    for x in units:
        clusters.setdefault(x["cluster"], []).append(x)
    keys = sorted(clusters)
    num = np.array([sum(value(x) * (x["w"] if x.get("w") else 1.0) for x in clusters[k]) for k in keys], float)
    den = np.array([sum(x["w"] if x.get("w") else 1.0 for x in clusters[k]) for k in keys], float)
    return keys, clusters, num, den


def slope_test(units: list, B: int = pc.BOOTSTRAP_B) -> dict:
    """S2: weighted calibration slope of the app against 1, two-sided cluster bootstrap p."""
    us = _scored(units)
    _, a = pc.logistic_fit([x["p_home"] for x in us], [x["y"] for x in us], [x["w"] for x in us])
    if a is None:
        return {"slope": None, "ci95": None, "p": 1.0}
    clusters: dict = {}
    for i, x in enumerate(us):
        clusters.setdefault(x["cluster"], []).append(i)
    groups = [np.array(clusters[k]) for k in sorted(clusters)]
    ps_, ys_, ws_ = (np.array([x[k] for x in us], float) for k in ("p_home", "y", "w"))
    idx = pc._resample(len(groups), B, pc.SEED)
    th = []
    for row in idx:
        rows = np.concatenate([groups[i] for i in row])
        s = pc.logistic_fit(ps_[rows], ys_[rows], ws_[rows])[1]
        th.append(1.0 if s is None else s)
    th = np.array(th)
    p = min(1.0, 2 * (1 + min(int((th <= 1).sum()), int((th >= 1).sum()))) / (B + 1))
    return {"slope": a, "ci95": list(pc.ci95(th)), "p": p}


# --- section 6-7: the look ---------------------------------------------------------------------------------------------------------------
def look(units: list, now: datetime, B: int = pc.BOOTSTRAP_B) -> dict:
    """The single look: counts only before 800 decided paired matches (w > 0) or the deadline; then the first 800 decided
    matches (every earlier match final or past 7 days), or all of them at the deadline (fewer than 300: no verdict)."""
    ordered = sorted(units, key=lambda x: (x["kickoff_at"], x["match_id"]))
    decided = [i for i, x in enumerate(ordered) if x["state"] == "decided"]
    deadline_passed = now.astimezone(ZoneInfo("America/New_York")).date().isoformat() > STOP_DATE
    if len(decided) >= STOP_N:
        end, chosen = decided[STOP_N - 1], decided[:STOP_N]
    elif deadline_passed:
        end, chosen = len(ordered) - 1, decided
    else:
        return {"status": f"no look yet (n of {STOP_N})", "n_decided_paired": len(decided), "n_required": STOP_N}
    if any(x["state"] == "pending" for x in ordered[:end + 1]):
        return {"status": WAITING, "n_decided_paired": len(decided), "n_required": STOP_N}
    sel = [ordered[i] for i in chosen]
    half = [x for x in sel if x["line_type"] == "half"]
    out = {"status": "look", "n": len(sel), "n_half_line": len(half), "metrics": metrics(half), "by_line_type": {}}
    for t in ("half", "whole", "quarter"):
        g = [x for x in sel if x["line_type"] == t]
        out["by_line_type"][t] = {"n": len(g), **metrics(g)} if t == "half" or len(g) >= TYPE_MIN else {"status": f"counts only (n of {TYPE_MIN})", "n": len(g)}
    if half:
        d = lambda x: pc.log_loss(x["p_home"], x["y"]) - pc.log_loss(x["q_home"], x["y"])      # noqa: E731
        _, _, num, den = _cluster_arrays(_scored(half), d)
        th = pc.boot_ratio(num, den, B)
        lo, hi = pc.ci95(th)
        delta = float(num.sum() / den.sum())
        out["primary"] = {"delta": delta, "ci95": [lo, hi], "clusters": int(len(num))}
        out["verdict"] = VERDICTS[0] if hi < 0 else VERDICTS[1] if lo > 0 else VERDICTS[2]
        clv = [x for x in half if x["clv"] is not None]
        if clv:
            _, _, cn, cd = _cluster_arrays(clv, lambda x: x["clv"])
            ct = pc.boot_ratio(cn, cd, B)
            s1 = {"theta": float(cn.sum() / cd.sum()), "p": pc.p_le_zero(ct), "n": len(clv)}
        else:
            s1 = {"theta": None, "p": 1.0, "n": 0}
        s2 = slope_test(half, B)
        out["secondary"] = {"S1": s1, "S2": s2}
        out["holm_secondary"] = pc.holm({"S1": s1["p"], "S2": s2["p"]})
        pw = [x for x in _scored(half) if x.get("q_home_power") is not None]
        W = sum(x["w"] for x in pw)
        out["sensitivity_power_devig"] = {"n": len(pw), "delta_ll": (sum(x["w"] * (pc.log_loss(x["p_home"], x["y"]) - pc.log_loss(x["q_home_power"], x["y"]))
                                                                          for x in pw) / W) if W else None}
        out["flat_stake"] = flat_stake(half)
        out["by_competition"] = {c: metrics([x for x in half if x["competition"] == c]) for c in sorted({x["competition"] for x in half})}
        shown = [x for x in sel if x["line_type"] == "half" or out["by_line_type"][x["line_type"]].get("status") is None]      # addendum 3
        out["by_line_bin"] = {b: metrics([x for x in shown if line_bin(x["line_home"]) == b]) for b in ("0.5", "1.5", ">=2.5")}
    if len(sel) < STOP_N:
        out["short_of_planned_n"] = f"n {len(sel)} of {STOP_N} (deadline {STOP_DATE})"
    if len(sel) < MIN_N_VERDICT:
        out["label"], out["verdict"] = f"underpowered (n of {STOP_N})", None
    return out


# --- entry point -------------------------------------------------------------------------------------------------------------------------
PRE_LOOK_COUNTS = ("forecasts", "no_goals_forecast", "not_pinned_candidate", "knockout_excluded", "not_regular_season", "forecast_before_t0",
                   "after_stop_date", "no_quote", "unpaired", "paired", "decided_paired")


def run(data_dir, out, *, now: Optional[datetime] = None, looks_dir=None) -> dict:
    check_document()
    dd = pc.guard_data_dir(data_dir)
    outdir = pc.guard_out(out, dd)
    now = now or datetime.now(timezone.utc)
    path = dd / "soccer.sqlite"
    if not path.is_file():
        raise pc.Refused(f"{path} not found")
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        col = collect(conn, now=now, outcomes=False)
        reachable = sum(col.counts["paired"].values()) >= STOP_N or now.astimezone(ZoneInfo("America/New_York")).date().isoformat() > STOP_DATE
        if reachable:
            col = collect(conn, now=now)
    finally:
        conn.close()
    if not reachable:                      # fewer than 800 paired matches: the stop cannot be reached, so no outcome needs reading
        counts = {k: col.counts[k] for k in PRE_LOOK_COUNTS if k != "decided_paired"}
        report = {"prereg_sha256": PREREG_SHA256, "candidate_sha256": CANDIDATE_SHA256, "evaluated_at": _ts(now),
                  "counts": {**counts, "decided_paired": None},
                  "look": {"status": f"no look yet (n of {STOP_N})", "n_decided_paired": None, "n_required": STOP_N}}
        pc.write_report(outdir, "soccer-ah-report.json", report)
        return report
    def compute() -> dict:
        r = look(col.units, now)
        return {**r, "counts_at_look": col.counts} if r["status"] == "look" else r
    lk = pc.look_once(looks_dir, PREREG_SHA256, "look", compute)
    # before the look only forecasts, paired quotes by line type and the stop count (addendum 5); the rest appears in the look
    counts = lk.get("counts_at_look") if lk["status"] == "look" else {k: col.counts[k] for k in PRE_LOOK_COUNTS}
    report = {"prereg_sha256": PREREG_SHA256, "candidate_sha256": CANDIDATE_SHA256, "evaluated_at": _ts(now), "counts": counts, "look": lk}
    pc.write_report(outdir, "soccer-ah-report.json", report)
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    try:
        print(json.dumps(run(a.data_dir, a.out), indent=2, sort_keys=True, default=str))
    except pc.Refused as exc:
        print(f"REFUSED: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
