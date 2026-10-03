"""Tracking of every evaluated discovery group (docs/MODEL_SELECTION.md §18): sport × market × model subset × agreement
rule × confidence threshold or band. Historical (exploratory, from the saved discovery-v1 per-group tables) and prospective
(computed at read time from the stored two-hour cohorts and candidate forecasts at their exact lines) are reported
separately. Nothing is copied per threshold: groups are classifications of stored predictions."""
import gzip
import json
import math
from pathlib import Path
from typing import Optional

from . import config, research_live

MIN_REPORT = 50
TWO_WAY_T = tuple(round(0.50 + 0.05 * i, 2) for i in range(9))
REG3_T = tuple(round(0.35 + 0.05 * i, 2) for i in range(8))
TWO_WAY_BANDS = tuple((round(0.50 + 0.05 * i, 2), round(0.55 + 0.05 * i, 2)) for i in range(8)) + ((0.90, 1.0001),)
REG3_BANDS = ((1 / 3, 0.35),) + tuple((round(0.35 + 0.05 * i, 2), round(0.40 + 0.05 * i, 2)) for i in range(7)) + ((0.70, 1.0001),)
NOT_LIVE = ("stack_l2", "stack_l1")       # trained historically only; not recorded prospectively


def discovery_dir() -> Path:
    return config.data_dir() / "reports" / "discovery-v1"


def levels(market: str) -> list:
    ts = REG3_T if market == "reg3" else TWO_WAY_T
    bs = REG3_BANDS if market == "reg3" else TWO_WAY_BANDS
    return [(f">={t:.2f}", t, 9.0) for t in ts] + [(f"[{lo:.3f},{hi:.3f})", lo, hi) for lo, hi in bs]


def _wilson(w: int, n: int, z: float = 1.959964):
    if n == 0:
        return None
    p = w / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [c - h, c + h]


def _match(row: dict, models: Optional[list], method: Optional[str], level: Optional[str]) -> bool:
    if method and row["method"] != method:
        return False
    if level and row["level"] != level:
        return False
    if models and not all(m in row["members"] for m in models):
        return False
    return True


def _load(sport: str, market: str) -> Optional[list]:
    p = discovery_dir() / f"groups-{sport}-{market}.json.gz"
    if not p.is_file():
        return None
    with gzip.open(p, "rt") as fh:
        return json.load(fh)["rows"]


def historical(sport: str, market: str, *, models=None, method=None, level=None, limit: int = 200) -> dict:
    rows = _load(sport, market)
    if rows is None:
        return {"status": "missing", "groups": [], "note": "No discovery-v1 per-group table for this market."}
    rows = [r for r in rows if _match(r, models, method, level)]
    pooled: dict = {}
    for r in rows:
        k = (r["group"], r["level"])
        p = pooled.setdefault(k, {"group": r["group"], "method": r["method"], "members": r["members"], "level": r["level"],
                                  "eligible": 0, "selected": 0, "wins": 0, "losses": 0, "pushes": 0, "voids": 0, "pending": 0,
                                  "matched_wins": 0.0, "matched_n": 0, "seasons": {}, "_conf": 0.0, "_ll": 0.0, "_br": 0.0,
                                  "_pn": 0})
        for f in ("eligible", "selected", "wins", "losses", "pushes"):
            p[f] += r[f]
        n = r["wins"] + r["losses"]
        p["_conf"] += (r["mean_conf"] or 0) * r["selected"]
        if r.get("log_loss") is not None:
            p["_ll"] += r["log_loss"] * n
            p["_br"] += r["brier"] * n
            p["_pn"] += n
        if r.get("best_prior_single_matched") is not None:
            p["matched_wins"] += r["best_prior_single_matched"] * n
            p["matched_n"] += n
        p["seasons"][str(r["season"])] = {"selected": r["selected"], "coverage": r["coverage"], "win_rate": r["win_rate"],
                                          "best_prior_single": r.get("best_prior_single"),
                                          "best_prior_single_matched": r.get("best_prior_single_matched"),
                                          **({"votes": r["votes"]} if "votes" in r else {})}
    out = []
    for p in pooled.values():
        n = p["wins"] + p["losses"]
        out.append({k: v for k, v in p.items() if not k.startswith("_") and k not in ("matched_wins", "matched_n")} | {
            "coverage": p["selected"] / p["eligible"] if p["eligible"] else 0.0, "win_rate": p["wins"] / n if n else None,
            "ci95": _wilson(p["wins"], n), "mean_conf": p["_conf"] / p["selected"] if p["selected"] else None,
            "log_loss": p["_ll"] / p["_pn"] if p["_pn"] else None, "brier": p["_br"] / p["_pn"] if p["_pn"] else None,
            "best_prior_single_matched": p["matched_wins"] / p["matched_n"] if p["matched_n"] else None,
            "status": "ok" if n >= MIN_REPORT else "insufficient evidence"})
    out.sort(key=lambda g: (-(g["wins"] + g["losses"]), g["group"], g["level"]))
    return {"status": "ok", "kind": "historical (exploratory: spent seasons; every group is shown, so the best rows are "
            "selected with hindsight)", "groups_matched": len(out), "groups": out[:limit],
            "line": None if market in ("winner", "reg3") else "pregame research line (the primary model's own line), "
                                                               "pooled across games' lines; not bookmaker lines"}


# --- Prospective (stored two-hour cohorts) -------------------------------------------------------------------------------
def _pick(v, three_way: bool):
    sides = (0, 1, 2) if three_way else (0, 2)
    best = max(v[i] for i in sides)
    top = [i for i in sides if abs(v[i] - best) <= 1e-12]
    return (top[0], best) if len(top) == 1 else (None, None)


def side_conf(method: str, vecs: list, three_way: bool):
    """Same definitions as discovery_study.side_conf: (side, confidence, supporting votes)."""
    if method == "single":
        s, c = _pick(vecs[0], three_way)
        return s, c, None
    if method == "avg":
        a = [sum(v[i] for v in vecs) / len(vecs) for i in range(3)]
        s, c = _pick(a, three_way)
        return s, c, None
    picks = [_pick(v, three_way)[0] for v in vecs]
    if method == "unanimous_min":
        if picks[0] is None or any(p != picks[0] for p in picks):
            return None, None, None
        return picks[0], min(v[picks[0]] for v in vecs), None
    if method == "majority":
        counts = {s: picks.count(s) for s in (0, 1, 2)}
        top = max(counts.values())
        lead = [s for s, c in counts.items() if c == top]
        if len(lead) != 1 or top * 2 <= len(vecs):
            return None, None, None
        s = lead[0]
        sup = [v[s] for v, p in zip(vecs, picks) if p == s]
        return s, sum(sup) / len(sup), top
    raise ValueError(method)


def _vectors(conn, cohort) -> dict:
    vec = {m["name"]: m["p"] for m in json.loads(cohort["members"]) if m.get("p")}
    for r in conn.execute("SELECT candidate, p_a, p_mid, p_b FROM candidate_forecasts WHERE game_id=? AND market=? AND "
                          "tipoff_at=? AND line IS ? AND line_ref=? AND generated_at<tipoff_at ORDER BY generated_at, forecast_id",
                          (cohort["game_id"], cohort["market"], cohort["tipoff_at"], cohort["line"],
                           f"research_cohorts:{cohort['cohort_id']}")):
        vec[r["candidate"]] = [r["p_a"], r["p_mid"], r["p_b"]]
    return vec


def prospective(conn, sport: str, market: str, as_of: str, *, models=None, method=None, level=None, limit: int = 200) -> dict:
    hist = _load(sport, market) or []
    defs = {}
    for r in hist:
        if _match(r, models, method, None):
            defs.setdefault(r["group"], (r["method"], r["members"]))
    three = market == "reg3"
    per_game = {}
    for c in research_live.canonical_cohorts(conn, market, None, as_of):
        if research_live.is_expanded(c["definition"]):
            continue
        if c["game_id"] not in per_game or c["generated_at"] > per_game[c["game_id"]]["generated_at"]:
            per_game[c["game_id"]] = c
    items = []
    for c in per_game.values():
        st = research_live.state(c, as_of)
        y = research_live._realised(market, c["line"], c, c["decision"]) if st == "settled" else None
        items.append((c, _vectors(conn, c), st, y))
    lv = [x for x in levels(market) if not level or x[0] == level]
    out = []
    for key, (meth, members) in defs.items():
        if any(m in NOT_LIVE for m in members):
            continue
        base = [(c, v, st, y) for c, v, st, y in items if all(m in v for m in members)]
        res = [(c, st, y, *side_conf(meth, [v[m] for m in members], three)) for c, v, st, y in base]
        for lab, lo, hi in lv:
            sel = [x for x in res if x[3] is not None and x[4] + 1e-9 >= lo and (hi >= 9 or x[4] < hi - 1e-12)]
            w = l = p = pend = void = 0
            lines: dict = {}
            for c, st, y, s, conf, votes in sel:
                lines[str(c["line"])] = lines.get(str(c["line"]), 0) + 1
                if st == "void":
                    void += 1
                elif st != "settled" or y is None:
                    pend += 1
                elif y == 1 and not three:
                    p += 1
                elif y == s:
                    w += 1
                else:
                    l += 1
            n = w + l
            out.append({"group": key, "method": meth, "members": members, "level": lab, "eligible": len(base),
                        "selected": len(sel), "coverage": len(sel) / len(base) if base else 0.0, "wins": w, "losses": l,
                        "pushes": p, "voids": void, "pending": pend, "win_rate": w / n if n else None, "ci95": _wilson(w, n),
                        "lines": lines, "line_label": None if three or market == "winner" else
                        ("exact line" if len(lines) <= 1 else "across exact lines (each cohort at its own exact line)"),
                        "status": "ok" if n >= MIN_REPORT else "insufficient evidence"})
    out.sort(key=lambda g: (-(g["selected"]), g["group"], g["level"]))
    return {"status": "ok", "kind": "prospective (two-hour cohorts, exact lines)", "as_of": as_of,
            "cohorts": len(items), "settled": sum(1 for x in items if x[2] == "settled"),
            "not_recorded_live": [k for k, (m, ms) in defs.items() if any(x in NOT_LIVE for x in ms)][:20],
            "groups_matched": len(out), "groups": out[:limit]}


def report(conn, sport: str, market: str, as_of: str, *, models=None, method=None, level=None, period="both", limit=200) -> dict:
    out = {"sport": sport, "market": market, "levels": [x[0] for x in levels(market)],
           "methods": ["single", "avg", "unanimous_min", "majority", "stack"],
           "confidence": {"single": "the model's probability of its pick", "avg": "equal-weight average probability of the pick",
                          "unanimous_min": "all members pick the same outcome; the lowest member probability of it",
                          "majority": "more than half pick the same outcome; mean probability of the supporting members",
                          "stack": "learned combiner's probability of its pick (historical only)"},
           "note": "Historical rows are exploratory (spent seasons). Groups with fewer than 50 decided picks are 'insufficient "
                   "evidence', not losers. Recommendations come only from the weekly selectors on earlier evidence."}
    rows = _load(sport, market)
    out["members"] = sorted({m for r in rows for m in r["members"]}) if rows else []
    if period in ("both", "historical"):
        out["historical"] = historical(sport, market, models=models, method=method, level=level, limit=limit)
    if period in ("both", "prospective"):
        out["prospective"] = prospective(conn, sport, market, as_of, models=models, method=method, level=level, limit=limit)
    return out
