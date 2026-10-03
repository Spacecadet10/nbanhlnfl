"""Prospective tracking of the registered combination rules (docs/MODEL_SELECTION.md §15). Research only.

Rules are frozen in models/combination_rules.json (sha256 seal). Each rule is evaluated on the stored two-hour cohorts — the
expanded cohort where the market has one, else the core cohort — using only the member probabilities stored there. One
canonical decision per game, market and rule: the latest cohort made before the start (retries never add games). Nothing here
writes, selects for the core selector, recommends or promotes.
"""
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import combine, nhl_eval, selector
from .selective_study import in_level, side_of, disagreement

REGISTRY_PATH = Path(__file__).parent / "models" / "combination_rules.json"
SIDE_NAMES = ("a", "mid", "b")


class RegistryError(ValueError):
    pass


def seal(reg: dict) -> dict:
    body = {k: v for k, v in reg.items() if k != "sha256"}
    return {**body, "sha256": hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()}


def load_registry(path: Optional[Path] = None) -> dict:
    p = path or REGISTRY_PATH
    if not p.is_file():
        raise RegistryError(f"combination rule registry missing: {p.name}")
    reg = json.loads(p.read_text())
    if seal(reg)["sha256"] != reg.get("sha256"):
        raise RegistryError("combination rule registry does not match its seal")
    return reg


def build_registry(reports: dict, effective_from: str) -> dict:
    """From the selective-v1 study reports: every registered rule per live market, flagged by the last fold's freeze."""
    from .selective_study import registry
    from . import agreement, research_live
    live_tables = {"nba:winner": "nba:winner", "nhl:winner": "nhl:winner"}
    markets = {}
    for sport in ("nba", "nhl"):
        rep = reports[sport]
        for key, a in rep["markets"].items():
            table, market = key.split(":")
            if table == "nba_evening":
                continue
            if market == "winner":
                members = list(agreement.NAMES[sport]) + list(agreement.SHADOW[sport])
            else:
                members = list(research_live.LINE_MEMBERS[sport]) + list(research_live.SHADOW_LINE_MEMBERS[sport])
            cohort = "expanded" if research_live.shadows(sport, market) else "core"
            rules = registry(table, market, members, True)
            last = a["folds"][-1]
            hist = {}
            for f in a["folds"]:
                for rid, x in f["assessed"].items():
                    hist.setdefault(rid, []).append({"assess_season": f["assess_season"], "why": x["why"], "picks": x["picks"],
                                                     "coverage": x["coverage"], "accuracy": x["accuracy"], "lift": x["lift"],
                                                     "selection_accuracy": x["selection"]["accuracy"]})
            studied = set(a["members"])
            out = []
            for r in rules:
                prospective_only = not set(r["members"]) <= studied
                sel = last["assessed"].get(r["id"])
                out.append({"id": r["id"], "kind": r["kind"], "members": r["members"], "t": r["t"], "level": r["level"],
                            "selected": None if sel is None else ("qualified" if r["id"] in last["qualified"] else "unqualified research"),
                            "why": None if sel is None else sel["why"], "prospective_only": prospective_only,
                            "historical": hist.get(r["id"], [])})
            markets[f"{sport}:{market}"] = {"sport": sport, "market": market, "cohort": cohort, "members": members,
                                            "comparator": last["best_single"], "study_report": rep.get("output_dir"),
                                            "frontier": a["frontier"]["bands"], "rules": out}
    return seal({"study": "selective-v1", "version": 1, "effective_from": effective_from, "markets": markets})


# --- Evaluation on stored cohorts -------------------------------------------------------------------------------------
def _cohorts(conn, market: str, expanded: bool) -> list:
    from .research_live import EXPANDED
    like = f"%:{EXPANDED}"
    op = "LIKE" if expanded else "NOT LIKE"
    return conn.execute(
        "SELECT c.*, g.status, g.home_score, g.away_score, g.result_available_at, g.tipoff_at AS game_tipoff_at, d.decision "
        "FROM research_cohorts c JOIN games g ON g.game_id=c.game_id LEFT JOIN game_decisions d ON d.game_id=c.game_id "
        f"WHERE c.market=? AND c.definition {op} ? AND c.cohort_id = (SELECT x.cohort_id FROM research_cohorts x "
        f"WHERE x.game_id=c.game_id AND x.market=c.market AND x.definition {op} ? AND x.generated_at < x.tipoff_at "
        "ORDER BY x.generated_at DESC, x.cohort_id DESC LIMIT 1) ORDER BY c.tipoff_at, c.game_id",
        (market, like, like)).fetchall()


def _favourites(conn) -> dict:
    """Primary winner probability per game from its canonical core winner cohort (the same pregame information)."""
    out = {}
    for r in _cohorts(conn, "winner", False):
        m = json.loads(r["members"])
        if m:
            p = m[0]["p"]
            out[r["game_id"]] = max(p[0], p[2])
    return out


def explain(rule: dict, vectors: dict, market: str, row: dict) -> str:
    if not in_level(row, rule["level"]):
        return f"condition '{rule['level']}' not met"
    if rule["kind"] != "stack" and any(m not in vectors for m in rule["members"]):
        return "member missing: " + ", ".join(m for m in rule["members"] if m not in vectors)
    if rule["kind"] == "stack" and "stack" not in vectors:
        return "no stacker probability in this cohort"
    if side_of(rule, vectors, market) is not None:
        return "pick"
    if rule["kind"] in ("unanimous_min", "unanimous_maxdis"):
        picks = {combine.pick(vectors[m], market) for m in rule["members"]}
        if len(picks) > 1:
            return "members disagree"
        return "agreement below the minimum member probability" if rule["kind"] == "unanimous_min" else "probabilities too far apart"
    if rule["kind"] == "majority":
        return "no strict majority"
    return "probability below threshold"


def studied_members(m: dict) -> list:
    """Members the frozen rules were studied with, in registry order: the members of every rule not flagged
    `prospective_only` (the sealed registry lists later members too, e.g. NBA availability_two_hour). Conditions
    (disagreement, favourite) and the stack's constituents use this set, as the study did (audit P3)."""
    studied = {x for r in m["rules"] if not r["prospective_only"] and r["kind"] != "stack" for x in r["members"]}
    return [x for x in m["members"] if x in studied] or list(m["members"])


def rule_statistic(rule: dict, vectors: dict, market: str, side: int) -> tuple[str, float]:
    """(label, value) the rule compared with its threshold for the picked side, for display (audit P4)."""
    kind = rule["kind"]
    if kind == "stack":
        return "stacker probability", vectors["stack"][side]
    vecs = [vectors[x] for x in rule["members"]]
    if kind == "single":
        return "probability", vecs[0][side]
    if kind == "avg":
        return "average probability", combine.average(vecs)[side]
    if kind == "unanimous_min":
        return "minimum member probability", min(v[side] for v in vecs)
    return "mean member probability", sum(v[side] for v in vecs) / len(vecs)


def _state(row) -> str:
    from .research_live import state
    return state(row, "9999")


def _realised(market, row):
    from .research_live import _realised as realised
    return realised(market, row["line"], row, row["decision"])


def report(conn, sport: str, market: str, show: str = "selected", reg: Optional[dict] = None) -> dict:
    reg = reg or load_registry()
    key = f"{sport}:{market}"
    m = reg["markets"].get(key)
    if m is None:
        return {"sport": sport, "market": market, "status": "no_registered_rules", "rules": []}
    cohorts = [r for r in _cohorts(conn, market, m["cohort"] == "expanded") if r["generated_at"] >= reg["effective_from"]]
    fav = _favourites(conn)
    studied = studied_members(m)
    items = []
    for r in cohorts:
        vectors = {x["name"]: x["p"] for x in json.loads(r["members"])}
        st = json.loads(r["stack"])
        if st and st.get("p"):
            vectors["stack"] = st["p"]
        p0 = vectors.get(studied[0])
        row = {"favourite": fav.get(r["game_id"], max(p0[0], p0[2]) if p0 and market == "winner" else 0.5),
               "disagreement": disagreement(vectors, studied, studied[0], market)
               if all(x in vectors for x in studied) else None}
        status = _state(r)
        realised = _realised(market, r) if status == "settled" else None
        items.append({"cohort": r, "vectors": vectors, "row": row, "status": status, "realised": realised,
                      "week": selector.week_of(r["tipoff_at"])})
    rules = [x for x in m["rules"] if show == "all" or x["selected"]]
    out = []
    for rule in rules:
        rr = {"kind": rule["kind"], "members": rule["members"], "t": rule["t"], "level": rule["level"]}
        eligible = [i for i in items if in_level(i["row"], rule["level"])]
        res, recent, weeks = {"win": 0, "loss": 0, "push": 0, "void": 0, "pending": 0, "unscorable": 0}, [], {}
        picked = []
        tail = {id(i) for i in items[-10:]}          # `recent`: the ten latest games (items are in start order)
        for i in items:
            if not in_level(i["row"], rule["level"]):
                if id(i) in tail:
                    recent.append({"game_id": i["cohort"]["game_id"], "decision": "abstain",
                                   "reason": explain(rr, i["vectors"], market, i["row"]), "generated_at": i["cohort"]["generated_at"]})
                continue
            s = side_of(rr, i["vectors"], market)
            if s is None:
                if id(i) in tail:
                    recent.append({"game_id": i["cohort"]["game_id"], "decision": "abstain",
                                   "reason": explain(rr, i["vectors"], market, i["row"]), "generated_at": i["cohort"]["generated_at"]})
                continue
            if i["status"] == "void":
                o = "void"
            elif i["status"] != "settled":
                o = "pending"
            elif i["realised"] is None:
                o = "unscorable"
            else:
                o = selector.outcome(market, s, i["realised"])
            res[o] += 1
            picked.append((i, s, o))
            if o in ("win", "loss", "push"):
                w = weeks.setdefault(i["week"], {"win": 0, "loss": 0, "push": 0})
                w[o] += 1
            if id(i) in tail:
                label, value = rule_statistic(rr, i["vectors"], market, s)
                recent.append({"game_id": i["cohort"]["game_id"], "decision": SIDE_NAMES[s], "probability": value,
                    "statistic": label, "outcome": o, "line": i["cohort"]["line"], "generated_at": i["cohort"]["generated_at"],
                    "minutes_before_start": round((datetime.strptime(i["cohort"]["tipoff_at"], "%Y-%m-%dT%H:%M:%SZ")
                                                   - datetime.strptime(i["cohort"]["generated_at"], "%Y-%m-%dT%H:%M:%SZ")).total_seconds() / 60, 1)})
        decided = res["win"] + res["loss"]
        settled = [i for i in eligible if i["status"] == "settled" and i["realised"] is not None]

        def matched(name):
            ranked = sorted((i for i in settled if name in i["vectors"]),
                            key=lambda i: (-max(i["vectors"][name][k] for k in combine.sides(market)), i["cohort"]["game_id"]))
            flags = []
            for i in ranked:
                s = combine.pick(i["vectors"][name], market)
                if s is None:
                    continue
                o = selector.outcome(market, s, i["realised"])
                if o != "push":
                    flags.append(int(o == "win"))
                if len(flags) >= decided:
                    break
            return {"model": name, "games": len(flags), "accuracy": sum(flags) / len(flags) if flags else None}
        cons = rule["members"] if rule["kind"] != "stack" else studied      # the live stacker's inputs (audit P5)
        out.append({**rule, "prospective": {
            "eligible_games": len(eligible), "picks": len(picked), "coverage": len(picked) / len(eligible) if eligible else None,
            **res, "accuracy": res["win"] / decided if decided else None, "ci95": nhl_eval._wilson(res["win"], decided),
            "strongest_single_matched": matched(m["comparator"]) if decided else None,
            "constituents_matched": [matched(c) for c in cons] if decided else [],
            "weeks": [{"week": k, **v} for k, v in sorted(weeks.items())], "recent": recent}})
    return {"sport": sport, "market": market, "kind": "prospective research (combination rules)", "study": reg["study"],
            "registry_sha256": reg["sha256"], "effective_from": reg["effective_from"], "cohort_source": m["cohort"],
            "members": m["members"], "studied_members": studied, "comparator": m["comparator"], "cohorts": len(items),
            "show": show,
            "registered_rules": len(m["rules"]), "selected_rules": sum(1 for x in m["rules"] if x["selected"]),
            "frontier_historical": m["frontier"], "rules": out,
            "cutoff_note": "Thresholds and conditions were frozen on night-before study tables; they are scored here on "
                           "two-hour cohorts, which carry later information, so prospective results are not a like-for-like "
                           "replication of the study (audit P6).",
            "note": "Historical numbers are exploratory (reused seasons). Prospective numbers count one canonical decision per "
                    "game, market and rule from cohorts recorded within two hours before the start. Research only: nothing here "
                    "is used by the selector, recommended or promoted."}
