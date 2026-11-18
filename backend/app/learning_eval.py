"""Read-time paired scoring of prospective challengers against the incumbent, for settled games only.

The incumbent is the primary member of the latest core research cohort at the challenger's cutoff, for the same game, market
and exact line. Differences are challenger minus incumbent (negative = the challenger did better). Nothing is written.

  python -m app.learning_eval --sport nba [--json]
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Optional

from . import combine, config, learning_challengers as lc, markets, soccer_markets as sm

LABEL = ("PROSPECTIVE, NO CLAIM until the preregistered sample size is reached: these are paired differences on the games "
         "settled so far, with counts, not evidence that a challenger is better. Registration is not promotion.")
SIDE = {"a": 0, "mid": 1, "b": 2}


def _served_ncaab(store, row) -> Optional[list]:
    """NCAAB incumbent: the served near-tipoff forecast of the same game, market and exact line, the latest generated at or
    before the challenger's cutoff (the winner: the stored near-tipoff win probability)."""
    if row["market"] == "winner":
        r = store.execute(
            "SELECT p.home_win_prob FROM predictions p JOIN near_tipoff_forecasts n ON n.prediction_id = p.prediction_id "
            "WHERE p.game_id=? AND p.run_type='live' AND n.tipoff_at=? AND p.generated_at<=? "
            "ORDER BY p.generated_at DESC, p.prediction_id DESC LIMIT 1", (row["game_id"], row["tipoff_at"], row["cutoff_at"])).fetchone()
        return None if r is None else [r[0], 0.0, 1 - r[0]]
    r = store.execute(
        "SELECT p_a, p_mid, p_b FROM market_forecasts WHERE game_id=? AND market=? AND line IS ? AND policy='near_tipoff' "
        "AND run_type='live' AND tipoff_at=? AND generated_at<=? ORDER BY generated_at DESC, forecast_id DESC LIMIT 1",
        (row["game_id"], row["market"], row["line"], row["tipoff_at"], row["cutoff_at"])).fetchone()
    return None if r is None else list(r)


def _served_nfl(store, row) -> Optional[list]:
    """NFL incumbent: the served live forecast of the same game, market and exact line made at or before the challenger's row."""
    r = store.execute(
        "SELECT p_a, p_mid, p_b FROM game_forecasts WHERE game_id=? AND market=? AND line IS ? AND order_version='nfl-live-v1' "
        "AND status='forecast' AND tipoff_at=? AND generated_at<=? ORDER BY generated_at DESC, forecast_id DESC LIMIT 1",
        (row["game_id"], row["market"], row["line"], row["tipoff_at"], row["cutoff_at"])).fetchone()
    return None if r is None else list(r)


def _served_soccer(store, row) -> Optional[list]:
    """Soccer incumbent: the served 1X2 (Elo) triple, or the served Poisson goals joint priced at the challenger's own line."""
    if row["market"] == "soccer_1x2":
        r = store.execute("SELECT p_home, p_draw, p_away FROM soccer_live_forecasts WHERE match_id=? AND generated_at<=? "
                          "ORDER BY version DESC LIMIT 1", (row["game_id"], row["cutoff_at"])).fetchone()
        return None if r is None else list(r)
    r = store.execute("SELECT joint_json FROM soccer_market_forecasts WHERE match_id=? AND family='goals' AND generated_at<=? "
                      "ORDER BY version DESC LIMIT 1", (row["game_id"], row["cutoff_at"])).fetchone()
    if r is None:
        return None
    joint = json.loads(r[0])
    if row["market"] == "soccer_btts":
        p = sm.probabilities(joint, "btts", "yes", None)
        return [p["win"], 0.0, p["loss"]]
    p = sm.probabilities(joint, row["market"][len("soccer_"):], "over", row["line"])
    return [p["win"], p["push"], p["loss"]]


def _soccer_side(store, row) -> str:
    """The settled side ('a' first-named outcome, 'mid' push/draw, 'b'), or pending / void / unscorable."""
    m = store.execute("SELECT status, decided_in, home_score, away_score, result_available_at FROM soccer_matches WHERE match_id=?",
                      (row["game_id"],)).fetchone()
    if m is None:
        return "unscorable"
    if m["status"] in ("postponed", "canceled", "abandoned"):
        return "void"
    if m["status"] != "final" or not m["result_available_at"]:
        return "pending"
    if m["decided_in"] != "regulation":
        return "unscorable"
    h, a = m["home_score"], m["away_score"]
    if row["market"] == "soccer_1x2":
        return "a" if h > a else "mid" if h == a else "b"
    if row["market"] == "soccer_btts":
        return "a" if h > 0 and a > 0 else "b"
    count = {"soccer_goals_total": h + a, "soccer_goals_home": h, "soccer_goals_away": a}[row["market"]]
    return "a" if count > row["line"] else "mid" if count == row["line"] else "b"


DC_SIDES = ((0, 1), (1, 2), (0, 2))      # home or draw, draw or away, home or away


def _dc(p, k: int) -> tuple:
    """(log loss, brier) of the three double-chance selections of a 1X2 triple p, averaged: one unit per match."""
    ll = br = 0.0
    for s in DC_SIDES:
        q = sum(p[i] for i in s)
        o = 0 if k in s else 2
        ll += combine.log_loss((q, 0.0, 1 - q), o) / 3
        br += combine.brier((q, 0.0, 1 - q), o) / 3
    return ll, br


def _incumbent(store, row) -> Optional[list]:
    if row["policy_id"].startswith("ncaab-"):
        return _served_ncaab(store, row)
    if row["policy_id"].startswith("nfl-"):
        return _served_nfl(store, row)
    if row["policy_id"].startswith("soccer-"):
        return _served_soccer(store, row)
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
        if r["policy_id"].startswith("soccer-"):
            side = _soccer_side(store, r)
        else:
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
        if r["market"] == "soccer_1x2":          # double chance: an exact function of the 1X2 triple, scored here and never stored
            d = groups.setdefault((r["policy_id"], r["policy_sha"], r["spec_name"], r["spec_hash"], "soccer_double_chance"),
                                  {"n_scored": 0, "n_unsettled": 0, "n_void": 0, "n_unscorable": 0, "n_no_incumbent": 0,
                                   "ll_c": [], "ll_i": [], "br_c": [], "br_i": []})
            (lc_, bc_), (li_, bi_) = _dc(c, k), _dc(inc, k)
            d["n_scored"] += 1
            d["ll_c"].append(lc_), d["ll_i"].append(li_), d["br_c"].append(bc_), d["br_i"].append(bi_)
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
    p.add_argument("--store", help="challenger store path (a rehearsal copy); the default is <data dir>/research/challengers.sqlite")
    a = p.parse_args(argv)
    src = config.data_dir() / f"{a.sport}.sqlite"
    cc = lc.open_ro(Path(a.store) if a.store else lc.store_path())
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
