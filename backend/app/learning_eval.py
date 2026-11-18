"""Read-time paired scoring of prospective challengers against the incumbent, for settled games only.

The incumbent is the primary member of the latest core research cohort at the challenger's cutoff, for the same game, market
and exact line. Differences are challenger minus incumbent (negative = the challenger did better). Nothing is written.

  python -m app.learning_eval --sport nba [--json]
"""
import argparse
import json
import sqlite3
import sys
from typing import Optional

from . import combine, config, learning_challengers as lc, markets

LABEL = ("PROSPECTIVE, NO CLAIM until the preregistered sample size is reached: these are paired differences on the games "
         "settled so far, with counts, not evidence that a challenger is better. Registration is not promotion.")
SIDE = {"a": 0, "mid": 1, "b": 2}


def _incumbent(store, row) -> Optional[list]:
    c = store.execute(
        "SELECT members, primary_name FROM research_cohorts WHERE game_id=? AND market=? AND line IS ? AND tipoff_at=? "
        "AND definition NOT LIKE '%:expanded-v1' AND generated_at<=? ORDER BY generated_at DESC, cohort_id DESC LIMIT 1",
        (row["game_id"], row["market"], row["line"], row["tipoff_at"], row["cutoff_at"])).fetchone()
    if c is None:
        return None
    for m in json.loads(c["members"]):
        if m.get("name") == c["primary_name"] and m.get("p"):
            return m["p"]
    return None


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def evaluate(store, cc) -> dict:
    prereg = {}
    for pol in cc.execute("SELECT policy_sha, content FROM challenger_policies"):
        for ch in json.loads(pol["content"]).get("challengers", []):
            prereg[(pol["policy_sha"], ch["spec"]["name"])] = ch.get("preregistered_n")
    groups: dict = {}
    seen: set = set()
    for r in cc.execute("SELECT * FROM challenger_forecasts ORDER BY policy_id, spec_name, market, game_id, cutoff_at, forecast_id"):
        key = (r["policy_id"], r["policy_sha"], r["spec_name"], r["spec_hash"], r["market"])
        once = (r["policy_sha"], r["spec_hash"], r["game_id"], r["market"])
        if once in seen:                 # a moving line records more rows: only the earliest counts, so n is games
            continue
        seen.add(once)
        g = groups.setdefault(key, {"n_scored": 0, "n_unsettled": 0, "n_void": 0, "n_unscorable": 0, "n_no_incumbent": 0,
                                    "ll_c": [], "ll_i": [], "br_c": [], "br_i": []})
        game = store.execute("SELECT status, home_score, away_score, tipoff_at FROM games WHERE game_id=?", (r["game_id"],)).fetchone()
        if game is None:
            g["n_unscorable"] += 1
            continue
        decision = store.execute("SELECT decision FROM game_decisions WHERE game_id=?", (r["game_id"],)).fetchone() \
            if r["market"] == "reg3" else None
        side = markets.settle(r["market"], r["line"], status=game["status"], home=game["home_score"], away=game["away_score"],
                              decision=decision[0] if decision else None, tipoff_at=game["tipoff_at"], forecast_tipoff_at=r["tipoff_at"])
        if side in ("pending",):
            g["n_unsettled"] += 1
            continue
        if side in ("void", "unscorable"):
            g["n_void" if side == "void" else "n_unscorable"] += 1
            continue
        inc = _incumbent(store, r)
        if inc is None:
            g["n_no_incumbent"] += 1
            continue
        k = SIDE[side]
        c = (r["p_a"], r["p_mid"], r["p_b"])
        g["n_scored"] += 1
        g["ll_c"].append(combine.log_loss(c, k))
        g["ll_i"].append(combine.log_loss(inc, k))
        g["br_c"].append(combine.brier(c, k))
        g["br_i"].append(combine.brier(inc, k))
    out = []
    for (pid, sha, name, h, market), g in groups.items():
        n, target = g["n_scored"], prereg.get((sha, name))
        mc, mi, bc, bi = _mean(g["ll_c"]), _mean(g["ll_i"]), _mean(g["br_c"]), _mean(g["br_i"])
        claim = ("no claim: no preregistered sample size is set" if target is None else
                 f"no claim: {n} of {target} preregistered games settled" if n < target else
                 "sample size reached: apply the preregistered decision rule, nothing is implied here")
        out.append({"policy_id": pid, "policy_sha": sha, "spec_name": name, "spec_hash": h, "market": market,
                    **{k: g[k] for k in ("n_scored", "n_unsettled", "n_void", "n_unscorable", "n_no_incumbent")},
                    "log_loss_challenger": mc, "log_loss_incumbent": mi, "log_loss_diff": None if mc is None else mc - mi,
                    "brier_challenger": bc, "brier_incumbent": bi, "brier_diff": None if bc is None else bc - bi,
                    "preregistered_n": target, "claim": claim})
    return {"label": LABEL, "groups": out}


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.learning_eval")
    p.add_argument("--sport", choices=lc.SPORTS, required=True)
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    src = config.data_dir() / f"{a.sport}.sqlite"
    cc = lc.open_ro(lc.store_path())
    if not src.is_file() or cc is None:
        out = {"label": LABEL, "groups": [], "note": "no challenger rows recorded yet" if cc is None else f"no {a.sport} store at {src}"}
    else:
        store = sqlite3.connect(f"{src.resolve().as_uri()}?mode=ro", uri=True)
        store.row_factory = sqlite3.Row
        out = evaluate(store, cc)
        out["groups"] = [g for g in out["groups"] if g["policy_id"].startswith(f"{a.sport}-")]
    if a.json:
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0
    print(out["label"])
    for g in out["groups"]:
        d = "n/a" if g["log_loss_diff"] is None else f"{g['log_loss_diff']:+.4f}"
        b = "n/a" if g["brier_diff"] is None else f"{g['brier_diff']:+.4f}"
        print(f"{g['policy_id']} {g['spec_name']} {g['market']}: scored {g['n_scored']} unsettled {g['n_unsettled']} void {g['n_void']} "
              f"no_incumbent {g['n_no_incumbent']} | log loss diff {d} brier diff {b} | {g['claim']}")
    if not out["groups"]:
        print(out.get("note", "no challenger rows recorded for this sport yet"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
