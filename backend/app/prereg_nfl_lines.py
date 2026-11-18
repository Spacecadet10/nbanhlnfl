"""Prospective NFL line-market evaluation and weekly challengers (docs/preregistration/nfl-line-eval-2026-11-18.md).

Implements that frozen document; section numbers below refer to it. Read-only on a COPY of the NFL store and of the research
challenger store (`--data-dir`); the live data folder is refused and the report goes outside the stores. Below n = 600 decided
bookmaker-line games per market only counts and coverage are produced, never a skill metric value (section 6). The look of
a market is computed once and stored (`nfl-looks.json` in the report folder); a second run returns the stored look.

Written and tested on synthetic data only; independent review (section 9) comes before any outcome is read.

  python -m app.prereg_nfl_lines --data-dir COPY --out REPORTDIR
"""
import argparse
import json
import math
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import numpy as np

from . import coverage, nfl_eval, odds, prereg_common as pc

PREREG_FILE = "nfl-line-eval-2026-11-18.md"
PREREG_SHA256 = "5ea6fef126ca9d4b8555f689d255a590f08261aac758a0e11101294d419e6e6c"
POLICY_PATH = pc.REPO_ROOT / "backend" / "app" / "reference" / "learning_policies" / "nfl-challengers-v1.json"
POLICY_SHA256 = "9503bca10a1120b3fd687f62105fd84467ea7ebf33269c79f3ebf5e15841cede"
REPO_ROOT = pc.REPO_ROOT
T0 = "2026-11-18T14:25:37Z"              # committer timestamp of 1afd91b (the commit that first added the document)
N_MIN = 600
DEADLINE = "2029-02-28"
PRIMARY_ALPHA, PRIMARY_M = 0.05, 2       # Bonferroni over {spread, total}: reject at p <= 0.025
CHALLENGER_ALPHA = 0.05 / 8
ORDER_VERSION = "nfl-live-v1"
MARKETS = ("winner", "spread", "total", "team_total_home", "team_total_away")
BOOK_MARKETS = ("winner", "spread", "total")
LINE_MARKETS = ("spread", "total")
PAST = timedelta(days=7)
STALE = timedelta(minutes=60)
NY = ZoneInfo("America/New_York")
INSUFFICIENT = "insufficient evidence (n of 600)"


def check_document() -> None:
    """Refuses unless the frozen document and the registered challenger policy file are byte-identical to the registered hashes."""
    pc.check_prereg(PREREG_FILE, PREREG_SHA256, REPO_ROOT)
    import hashlib
    try:
        sha = hashlib.sha256(Path(POLICY_PATH).read_bytes()).hexdigest()
    except OSError as exc:
        raise pc.Refused(f"challenger policy file unreadable: {exc}") from None
    if sha != POLICY_SHA256:
        raise pc.Refused(f"challenger policy file has sha256 {sha}, expected {POLICY_SHA256}: refusing to run")


def load_policy() -> dict:
    check_document()
    raw = json.loads(Path(POLICY_PATH).read_text())
    hyps = []
    for c in raw["challengers"]:
        name, head = c["spec"]["name"], c["spec"]["head"]
        hyps.extend((name, m) for m in (["winner"] if head == "winner" else ["spread", "total"]))
    return {"sha256": POLICY_SHA256, "policy": raw, "hypotheses": hyps}


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def week_key(season: int, tipoff_at: str) -> str:
    """Cluster = season + NFL week. The store has no week column: a week is the Tuesday-anchored Eastern week of the kickoff
    (Thursday night to Monday night share the Tuesday before)."""
    d = _parse(tipoff_at).astimezone(NY).date()
    return f"{season}|{d - timedelta(days=(d.weekday() - 1) % 7)}"


# --- section 1-2: the counted forecasts -------------------------------------------------------------------------------------
class Collected:
    def __init__(self):
        self.units, self.counts, self.games_counted = [], {}, 0


def _blank() -> dict:
    return {k: 0 for k in ("forecasts", "upcoming", "no_canonical_forecast", "other_order_version", "decided", "push", "void",
                           "unscorable", "past7", "pending", "bookmaker_line", "model_line", "decided_bookmaker_line",
                           "open_label_t120", "generated_after_cutoff", "quote_line_mismatch")} | {"open_label_providers": {}}


def collect(store: sqlite3.Connection, *, now: datetime) -> Collected:
    now_s = _ts(now)
    games = [dict(g) for g in store.execute("SELECT * FROM games WHERE season_type IN ('regular','postseason') ORDER BY tipoff_at, game_id")]
    counted = [g for g in games if nfl_eval.cutoff_for(g["tipoff_at"]) > T0]       # tip-off after T0 + 120 min
    for g in counted:                       # a result counts only once it was available (as nfl_live.Views does)
        if g["status"] == "final" and (g.get("result_available_at") or "") > now_s:
            g.update(status="in_progress", home_score=None, away_score=None)
    out = Collected()
    out.games_counted = len(counted)
    for market in MARKETS:
        n = out.counts[market] = _blank()
        cohort = coverage.canonical(store, counted, market, now_s) if counted else {}
        for g in counted:
            c = cohort[g["game_id"]]
            if c["state"] == "upcoming":
                n["upcoming"] += 1
                continue
            if c["state"] != "forecast":
                n["no_canonical_forecast"] += 1
                continue
            rec = c["record"]
            ov = store.execute("SELECT order_version FROM game_forecasts WHERE forecast_id = ?", (rec["forecast_id"],)).fetchone()[0]
            if ov != ORDER_VERSION:
                n["other_order_version"] += 1
                continue
            n["forecasts"] += 1
            pa, _, pb = rec["p"]
            cutoff = nfl_eval.cutoff_for(rec["start_at"])
            line, source, qid, q_book, label, provider = rec["line"], rec["line_source"], rec["market_quote_id"], None, None, None
            if market == "winner":
                wq = odds.winner_quote(store, "nfl", g["game_id"], cutoff)
                if wq is not None and wq["retrieved_at"] >= _ts(_parse(cutoff) - timedelta(hours=2)):
                    source, qid, q_book, label, provider = "bookmaker", wq["quote_id"], wq["p_home"], wq["price_label"], wq["provider"]
                else:
                    source = "model"
            elif source == "bookmaker" and qid is not None:
                qr = store.execute("SELECT p_a_devig, price_label, provider, line FROM market_quotes WHERE quote_id = ?", (qid,)).fetchone()
                q_book, label, provider = qr[0], qr[1], qr[2]
                if abs(qr[3] - line) > 1e-9:
                    n["quote_line_mismatch"] += 1
            if source == "bookmaker":
                n["bookmaker_line"] += 1
                if label == "open":
                    n["open_label_t120"] += 1
                    n["open_label_providers"][provider] = n["open_label_providers"].get(provider, 0) + 1
            else:
                n["model_line"] += 1
            n["generated_after_cutoff"] += rec["generated_at"] > cutoff
            side = c["result_side"]
            if side in ("a", "b"):
                state = "decided"
            elif side == "mid":
                state = "push"
            elif c["grade"] in ("void", "unscorable"):
                state = c["grade"]
            else:
                state = "past7" if now >= _parse(g["tipoff_at"]) + PAST else "pending"
            n[state] += 1
            if state == "decided" and source == "bookmaker":
                n["decided_bookmaker_line"] += 1
            out.units.append({"game_id": g["game_id"], "market": market, "week": week_key(g["season"], g["tipoff_at"]),
                              "tipoff_at": g["tipoff_at"], "cutoff_at": cutoff, "generated_at": rec["generated_at"], "p": pa / (pa + pb),
                              "line": line, "line_source": source, "quote_id": qid, "q_book": q_book, "quote_label": label,
                              "quote_provider": provider, "state": state, "y": (1 if side == "a" else 0) if state == "decided" else None})
    return out


# --- section 4: metrics -------------------------------------------------------------------------------------------------------
def _calibration(ps, ys) -> dict:
    x = np.array([pc.logit(pc.clip_p(p)) for p in ps])
    y = np.array(ys, float)
    if len(x) == 0 or float(((x - x.mean()) ** 2).sum()) < 1e-12:
        return {"intercept": None, "slope": None}
    beta = np.zeros(2)
    X = np.column_stack([np.ones(len(x)), x])
    for _ in range(100):
        mu = 1 / (1 + np.exp(-X @ beta))
        W = mu * (1 - mu)
        try:
            step = np.linalg.solve(X.T @ (X * W[:, None]) + 1e-12 * np.eye(2), X.T @ (y - mu))
        except np.linalg.LinAlgError:
            return {"intercept": None, "slope": None}
        beta = beta + step
        if np.max(np.abs(step)) < 1e-10:
            break
    ok = np.all(np.isfinite(beta)) and np.max(np.abs(beta)) < 50
    return {"intercept": float(beta[0]), "slope": float(beta[1])} if ok else {"intercept": None, "slope": None}


def metrics(units: list, book: bool = True) -> dict:
    """Section 4.1 / 4.3 metrics of decided units (keys p, y, q_book)."""
    n = len(units)
    ps, ys = [u["p"] for u in units], [u["y"] for u in units]
    out = {"n": n, "log_loss_app": sum(pc.log_loss(p, y) for p, y in zip(ps, ys)) / n, "brier_app": sum(pc.brier(p, y) for p, y in zip(ps, ys)) / n}
    side_a = [p >= 0.5 for p in ps]
    y_side = [y if a else 1 - y for a, y in zip(side_a, ys)]
    out["hit_rate"] = sum(y_side) / n
    out["hit_rate_wilson95"] = list(pc.wilson(sum(y_side), n))
    if book:
        qs = [u["q_book"] for u in units]
        out["log_loss_book"] = sum(pc.log_loss(q, y) for q, y in zip(qs, ys)) / n
        out["brier_book"] = sum(pc.brier(q, y) for q, y in zip(qs, ys)) / n
        out["delta_ll"] = out["log_loss_app"] - out["log_loss_book"]
        q_side = [q if a else 1 - q for a, q in zip(side_a, qs)]
        out["mean_q_side"] = sum(q_side) / n
        out["excess"] = out["hit_rate"] - out["mean_q_side"]
    out["calibration"] = _calibration(ps, ys)
    bins = [[] for _ in range(5)]
    for p, y in zip(ps, ys):
        bins[min(4, int(p * 5 + 1e-12))].append((p, y))
    out["reliability"] = [{"n": len(b), "mean_p": sum(p for p, _ in b) / len(b) if b else None,
                           "mean_y": sum(y for _, y in b) / len(b) if b else None} for b in bins]
    return out


def points_clv(market: str, side: str, line_forecast: Optional[float], line_close: Optional[float]) -> Optional[float]:
    if market not in LINE_MARKETS or line_forecast is None or line_close is None:
        return None
    if market == "spread":
        return line_forecast - line_close if side == "a" else line_close - line_forecast
    return line_close - line_forecast if side == "a" else line_forecast - line_close


def close_for(store: sqlite3.Connection, u: dict) -> dict:
    """Section 4.1 close: the T-120 quote's sportsbook, same market, 'current', before kick-off; stale beyond 60 minutes."""
    tip = _parse(u["tipoff_at"])
    r = store.execute("SELECT quote_id, retrieved_at, line, p_a_devig FROM market_quotes WHERE sport = 'nfl' AND game_id = ? AND market = ? "
                      f"AND price_label = 'current' AND retrieved_at <= ? AND {odds.same_provider()} "
                      "ORDER BY retrieved_at DESC, quote_id DESC LIMIT 1",
                      (u["game_id"], u["market"], _ts(tip - timedelta(seconds=1)), u["quote_provider"])).fetchone()
    if r is None:
        return {"status": "no_close"}
    if _parse(r[1]) < tip - STALE:
        return {"status": "stale"}
    side = "a" if u["p"] >= 0.5 else "b"
    same = (u["line"] is None and r[2] is None) or (u["line"] is not None and r[2] is not None and abs(u["line"] - r[2]) < 1e-9)
    if same:
        qc = r[3] if side == "a" else 1 - r[3]
        qs = u["q_book"] if side == "a" else 1 - u["q_book"]
        return {"status": "ok", "clv": qc - qs, "points_clv": None, "quote_id": r[0]}
    return {"status": "ok", "clv": None, "points_clv": points_clv(u["market"], side, u["line"], r[2]), "quote_id": r[0]}


# --- section 5-6: gate, look, hypotheses -------------------------------------------------------------------------------------------
def _cluster_test(deltas_by_week: dict, threshold: float) -> dict:
    keys = sorted(deltas_by_week)
    num = np.array([sum(deltas_by_week[k]) for k in keys], float)
    den = np.array([len(deltas_by_week[k]) for k in keys], float)
    theta = float(num.sum() / den.sum())
    th = pc.boot_ratio(num, den)
    p = pc.p_ge_zero(th)
    lo, hi = pc.ci95(th)
    return {"theta": theta, "ci95": [lo, hi], "p": p, "threshold": threshold, "reject": p <= threshold, "clusters": len(keys)}


def _by_week(units, delta) -> dict:
    out: dict = {}
    for u in units:
        out.setdefault(u["week"], []).append(delta(u))
    return out


def _delta_book(u):
    return pc.log_loss(u["p"], u["y"]) - pc.log_loss(u["q_book"], u["y"])


def look_market(units: list, market: str, now: datetime, store: Optional[sqlite3.Connection] = None) -> dict:
    """The single look of a market (section 6): counts only below n = 600 decided bookmaker-line games (team totals: decided
    forecasts); at 600, the first 600 decided games, once every earlier game is final or past 7 days."""
    book = market in BOOK_MARKETS
    elig = sorted((u for u in units if u["market"] == market and (not book or (u["line_source"] == "bookmaker" and u["q_book"] is not None))),
                  key=lambda u: (u["tipoff_at"], u["game_id"]))
    decided = [i for i, u in enumerate(elig) if u["state"] == "decided"]
    key = "n_decided_bookmaker_line" if book else "n_decided_forecasts"
    if len(decided) < N_MIN:
        closed = now.astimezone(timezone.utc).date().isoformat() > DEADLINE
        return {"market": market, "status": f"insufficient evidence (closed at deadline {DEADLINE})" if closed else INSUFFICIENT,
                key: len(decided), "n_required": N_MIN}
    last = decided[N_MIN - 1]
    if any(u["state"] == "pending" for u in elig[:last + 1]):
        return {"market": market, "status": "waiting: an earlier game is neither final nor past 7 days", key: len(decided), "n_required": N_MIN}
    look = [elig[i] for i in decided[:N_MIN]]
    out = {"market": market, "status": "look", "n": N_MIN, "metrics": metrics(look, book=book)}
    if store is not None and book:
        closes = [close_for(store, u) for u in look]
        clv = [c["clv"] for c in closes if c.get("clv") is not None]
        pts = [c["points_clv"] for c in closes if c.get("points_clv") is not None]
        out["clv"] = {"ok": sum(c["status"] == "ok" for c in closes), "stale": sum(c["status"] == "stale" for c in closes),
                      "no_close": sum(c["status"] == "no_close" for c in closes), "mean_clv": sum(clv) / len(clv) if clv else None,
                      "n_clv": len(clv), "mean_points_clv": sum(pts) / len(pts) if pts else None, "n_points_clv": len(pts)}
    if market in LINE_MARKETS:
        out["test"] = _cluster_test(_by_week(look, _delta_book), PRIMARY_ALPHA / PRIMARY_M)
        t = out["test"]
        out["verdict"] = (f"app beats the T-120 book on {market}" if t["reject"] else
                          f"book beats app on {market}" if t["ci95"][0] > 0 else "not shown")
        kept = [u for u in look if u["quote_label"] != "open"]
        if len(kept) < len(look) and kept:
            s = _cluster_test(_by_week(kept, _delta_book), PRIMARY_ALPHA / PRIMARY_M)
            out["sensitivity_without_open_label"] = {"n": len(kept), "delta_ll": s["theta"], "p": s["p"]}
        else:
            out["sensitivity_without_open_label"] = {"n": len(kept), "delta_ll": out["test"]["theta"], "p": out["test"]["p"]}
    return out


def look_once(out: Path, key: str, compute) -> dict:
    """One look per key: a stored look is returned verbatim, never recomputed."""
    path = Path(out) / "nfl-looks.json"
    stored = json.loads(path.read_text()) if path.is_file() else {}
    if key in stored:
        return stored[key]
    result = compute()
    if result.get("status") == "look":                    # only an actual look is stored; counts-only reports are rerunnable
        stored[key] = result
        path.write_text(json.dumps(stored, indent=2, sort_keys=True, default=str) + "\n")
    return result


# --- section 3: challengers -------------------------------------------------------------------------------------------------------------
def challenger_units(conn: sqlite3.Connection, incumbent: list, pol: dict, name: str, market: str) -> dict:
    """Paired decided games of one challenger and market from the research store, at the incumbent's line."""
    first = conn.execute("SELECT MIN(generated_at) FROM challenger_forecasts WHERE policy_sha = ?", (pol["sha256"],)).fetchone()[0]
    start = max(T0, first) if first else None
    rows = conn.execute("SELECT * FROM challenger_forecasts WHERE policy_sha = ? AND spec_name = ? AND market = ? ORDER BY forecast_id",
                        (pol["sha256"], name, market)).fetchall()
    by_game: dict = {}
    for r in rows:
        by_game.setdefault(r["game_id"], []).append(r)
    counts = {"rows": len(rows), "incumbent_decided_after_start": 0, "missing_rows": 0, "late_rows": 0}
    pairs, pending = [], []
    for u in sorted((u for u in incumbent if u["market"] == market), key=lambda u: (u["tipoff_at"], u["game_id"])):
        if start is None or nfl_eval.cutoff_for(u["tipoff_at"]) < start:
            continue
        if u["state"] == "pending":
            pending.append(u["tipoff_at"])
        if u["state"] != "decided":
            continue
        counts["incumbent_decided_after_start"] += 1
        cand = [r for r in by_game.get(u["game_id"], []) if (r["line"] is None and u["line"] is None) or
                (r["line"] is not None and u["line"] is not None and abs(r["line"] - u["line"]) < 1e-9)]
        good = [r for r in cand if r["generated_at"] < r["tipoff_at"]]
        counts["late_rows"] += len(cand) - len(good)
        if not good:
            counts["missing_rows"] += 1
            continue
        r = good[0]
        pairs.append({"game_id": u["game_id"], "week": u["week"], "tipoff_at": u["tipoff_at"], "p": u["p"], "y": u["y"], "q_book": u["q_book"],
                      "p_ch": r["p_a"] / (r["p_a"] + r["p_b"])})
    return {"start": start, "pairs": pairs, "counts": counts, "pending_tips": pending}


def look_challenger(pairs: list, name: str, market: str, now: datetime, pending_tips=()) -> dict:
    if len(pairs) < N_MIN:
        return {"challenger": name, "market": market, "status": INSUFFICIENT, "n_paired_decided": len(pairs), "n_required": N_MIN}
    look = pairs[:N_MIN]
    last = look[-1].get("tipoff_at")
    if last and any(t <= last for t in pending_tips):
        return {"challenger": name, "market": market, "status": "waiting: an earlier game is neither final nor past 7 days",
                "n_paired_decided": len(pairs), "n_required": N_MIN}
    d = lambda u: pc.log_loss(u["p_ch"], u["y"]) - pc.log_loss(u["p"], u["y"])      # noqa: E731
    test = _cluster_test(_by_week(look, d), CHALLENGER_ALPHA)
    book = [u for u in look if u.get("q_book") is not None]
    return {"challenger": name, "market": market, "status": "look", "n": N_MIN, "test": test,
            "metrics": {"log_loss_challenger": sum(pc.log_loss(u["p_ch"], u["y"]) for u in look) / N_MIN,
                        "log_loss_incumbent": sum(pc.log_loss(u["p"], u["y"]) for u in look) / N_MIN,
                        "n_book_line": len(book),
                        "delta_ll_vs_book": (sum(pc.log_loss(u["p_ch"], u["y"]) - pc.log_loss(u["q_book"], u["y"]) for u in book) / len(book)) if book else None},
            "verdict": f"{name} beats the incumbent on {market} (promotion proposal only)" if test["reject"] else "not shown"}


# --- entry point -------------------------------------------------------------------------------------------------------------------------
def _readonly(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def run(data_dir, out, *, now: Optional[datetime] = None) -> dict:
    check_document()
    dd = pc.guard_data_dir(data_dir)
    outdir = pc.guard_out(out, dd)
    now = now or datetime.now(timezone.utc)
    store_file = dd / "nfl.sqlite"
    if not store_file.is_file():
        raise pc.Refused(f"{store_file} not found")
    store = _readonly(store_file)
    try:
        col = collect(store, now=now)
        markets = {m: look_once(outdir, m, lambda m=m: look_market(col.units, m, now, store)) for m in MARKETS}
        pol = load_policy()
        ch_file = dd / "research" / "challengers.sqlite"
        ch = _readonly(ch_file) if ch_file.is_file() else None
        challengers = {}
        for name, market in pol["hypotheses"]:
            if ch is None:
                challengers[f"{name}:{market}"] = {"challenger": name, "market": market, "status": INSUFFICIENT, "n_paired_decided": 0, "n_required": N_MIN}
                continue
            cu = challenger_units(ch, col.units, pol, name, market)
            r = look_once(outdir, f"{name}:{market}", lambda: look_challenger(cu["pairs"], name, market, now, cu["pending_tips"]))
            challengers[f"{name}:{market}"] = {**r, "counts": cu["counts"], "start": cu["start"]}
        if ch is not None:
            ch.close()
        tested = {m: markets[m]["test"]["p"] for m in LINE_MARKETS if "test" in markets[m]}
        report = {"prereg_sha256": PREREG_SHA256, "policy_sha256": POLICY_SHA256, "evaluated_at": _ts(now), "games_counted": col.games_counted,
                  "counts": col.counts, "markets": markets, "primary_family_bonferroni": pc.bonferroni(tested, PRIMARY_ALPHA, m=PRIMARY_M) if tested else {},
                  "challengers": challengers, "challenger_family_size": len(pol["hypotheses"])}
        pc.write_report(outdir, "nfl-line-eval-report.json", report)
        return report
    finally:
        store.close()


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
