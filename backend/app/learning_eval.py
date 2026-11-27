"""Read-time paired scoring of prospective challengers against the incumbent, for settled games only.

The incumbent is the primary member of the latest core research cohort at the challenger's cutoff, for the same game, market
and exact line. Differences are challenger minus incumbent (negative = the challenger did better). Nothing is written.

  python -m app.learning_eval --sport nba [--json]
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from . import combine, config, learning_challengers as lc, markets, soccer_markets as sm

LABEL = ("PROSPECTIVE, NO CLAIM until the preregistered sample size is reached: these are paired differences on the games "
         "settled so far, with counts, not evidence that a challenger is better. Registration is not promotion.")
SIDE = {"a": 0, "mid": 1, "b": 2}


MINUTE = 60


def _ts(at) -> Optional[datetime]:
    try:
        return datetime.strptime(str(at), lc.FMT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _published_ledger(store, row) -> list:
    """NCAAB/NFL: every published run (game_forecasts, the canonical ledger the cards and Telegram show, status 'forecast') of the
    same game, market and current start: [(generated_at, line, p)], oldest first."""
    return [(r[0], r[1], [r[2], r[3], r[4]]) for r in store.execute(
        "SELECT generated_at, line, p_a, p_mid, p_b FROM game_forecasts WHERE game_id=? AND market=? AND status='forecast' AND tipoff_at=? "
        "ORDER BY generated_at, forecast_id", (row["game_id"], row["market"], row["tipoff_at"]))]


def _published_soccer(store, row) -> list:
    """Soccer: every published run of the same match: the served 1X2 (Elo) triple, or the served Poisson goals joint priced at the
    challenger's own line: [(generated_at, line, p)], oldest first."""
    if row["market"] == "soccer_1x2":
        return [(r[0], None, [r[1], r[2], r[3]]) for r in store.execute(
            "SELECT generated_at, p_home, p_draw, p_away FROM soccer_live_forecasts WHERE match_id=? ORDER BY generated_at, version",
            (row["game_id"],))]
    out = []
    for gen, joint_json in store.execute("SELECT generated_at, joint_json FROM soccer_market_forecasts WHERE match_id=? AND family='goals' "
                                         "ORDER BY generated_at, version", (row["game_id"],)):
        joint = json.loads(joint_json)
        if row["market"] == "soccer_btts":
            p = sm.probabilities(joint, "btts", "yes", None)
            out.append((gen, None, [p["win"], 0.0, p["loss"]]))
        else:
            p = sm.probabilities(joint, row["market"][len("soccer_"):], "over", row["line"])
            out.append((gen, row["line"], [p["win"], p["push"], p["loss"]]))
    return out


def _published(store, row) -> list:
    return _published_soccer(store, row) if row["policy_id"].startswith("soccer-") else _published_ledger(store, row)


def pair(row, runs: list) -> tuple:
    """(published p, None) or (None, reason): the pairing rule of autoresearch.FORWARD_PAIRING. The challenger's information time is
    its generated_at; the published run it is paired with is made at or after it and at most max_gap_minutes later (a challenger
    never knows more than the forecast it is compared with), at the same exact line, and by the fixed as-of instant
    (start - as_of_minutes). Reasons: no_published_row | line | as_of_late (the challenger itself is later than the instant) |
    published_after_as_of | candidate_later (every eligible published run is before the challenger) | gap (the first one at or after
    it is more than max_gap_minutes later)."""
    from .autoresearch import FORWARD_PAIRING
    if not runs:
        return None, "no_published_row"
    same = [r for r in runs if r[1] == row["line"]]
    if not same:
        return None, "line"
    start, t = _ts(row["tipoff_at"]), _ts(row["generated_at"])
    if start is None or t is None:
        return None, "no_published_row"
    as_of = start - timedelta(minutes=FORWARD_PAIRING["as_of_minutes"])
    if t > as_of:
        return None, "as_of_late"
    eligible = [(_ts(r[0]), r) for r in same if _ts(r[0]) is not None and _ts(r[0]) <= as_of]
    if not eligible:
        return None, "published_after_as_of"
    gap = timedelta(minutes=FORWARD_PAIRING["max_gap_minutes"])
    later = sorted((g, r) for g, r in eligible if g >= t)
    if not later:
        return None, "candidate_later"
    if later[0][0] - t > gap:
        return None, "gap"
    return later[0][1][2], None


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


def _dnb(p, k: int) -> tuple:
    """(log loss, brier) of the two draw-no-bet selections (home, away) of a 1X2 triple p given no draw (k is 0 or 2), averaged:
    each side's win probability is its share of the non-draw mass (conditional on no draw)."""
    ll = br = 0.0
    for side, i in (("home", 0), ("away", 2)):
        q = p[i] / (p[0] + p[2])
        o = 0 if k == i else 2
        ll += combine.log_loss((q, 0.0, 1 - q), o) / 2
        br += combine.brier((q, 0.0, 1 - q), o) / 2
    return ll, br


def _incumbent(store, row) -> tuple:
    """(incumbent p, None) or (None, why it is unpaired). NCAAB/NFL/Soccer: the published run (see `pair`). Others: the primary
    member of the latest core research cohort at the challenger's cutoff."""
    if row["policy_id"].startswith(("ncaab-", "nfl-", "soccer-")):
        return pair(row, _published(store, row))
    c = store.execute(
        "SELECT members, primary_name FROM research_cohorts WHERE game_id=? AND market=? AND line IS ? AND tipoff_at=? "
        "AND definition NOT LIKE '%:expanded-v1' AND generated_at<=? ORDER BY generated_at DESC, cohort_id DESC LIMIT 1",
        (row["game_id"], row["market"], row["line"], row["tipoff_at"], row["cutoff_at"])).fetchone()
    if c is None:
        return None, "no_incumbent"
    for m in json.loads(c["members"]):
        if m.get("name") == c["primary_name"] and m.get("p"):
            return m["p"], None
    return None, "no_incumbent"


def _group() -> dict:
    return {"n_scored": 0, "n_unsettled": 0, "n_void": 0, "n_unscorable": 0, "n_no_incumbent": 0, "n_paired": 0, "unpaired": {},
            "ll_c": [], "ll_i": [], "br_c": [], "br_i": []}


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def evaluate(store, cc, prefix: Optional[str] = None) -> dict:
    """Scores the challenger rows whose policy id starts with `prefix` (default all) against `store`'s served forecasts."""
    prereg = {}
    for pol in cc.execute("SELECT policy_sha, content FROM challenger_policies"):
        for ch in json.loads(pol["content"]).get("challengers", []):
            prereg[(pol["policy_sha"], ch["spec"]["name"])] = ch.get("preregistered_n")
    groups: dict = {}
    seen: set = set()
    for r in cc.execute("SELECT * FROM challenger_forecasts WHERE policy_id LIKE ? ESCAPE '\\' "
                        "ORDER BY policy_id, spec_name, market, game_id, cutoff_at, forecast_id", (f"{prefix or ''}%",)):
        key = (r["policy_id"], r["policy_sha"], r["spec_name"], r["spec_hash"], r["market"])
        once = (r["policy_sha"], r["spec_hash"], r["game_id"], r["market"])
        if once in seen:                 # a moving line records more rows: only the earliest counts, so n is games
            continue
        seen.add(once)
        g = groups.setdefault(key, _group())
        inc, why = _incumbent(store, r)          # every first row is paired or labelled unpaired with a reason, settled or not
        if inc is None:
            g["unpaired"][why] = g["unpaired"].get(why, 0) + 1
        else:
            g["n_paired"] += 1
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
        if r["market"] == "soccer_1x2":          # double chance and draw no bet: exact functions of the 1X2 triple, scored here, never stored
            def derived(name):
                return groups.setdefault((r["policy_id"], r["policy_sha"], r["spec_name"], r["spec_hash"], name), _group())
            d = derived("soccer_double_chance")
            (lc_, bc_), (li_, bi_) = _dc(c, k), _dc(inc, k)
            d["n_scored"] += 1
            d["n_paired"] += 1
            d["ll_c"].append(lc_), d["ll_i"].append(li_), d["br_c"].append(bc_), d["br_i"].append(bi_)
            n = derived("soccer_draw_no_bet")
            if k == 1:                                  # a draw pushes: void, excluded
                n["n_void"] += 1
            else:
                (lc_, bc_), (li_, bi_) = _dnb(c, k), _dnb(inc, k)
                n["n_scored"] += 1
                n["n_paired"] += 1
                n["ll_c"].append(lc_), n["ll_i"].append(li_), n["br_c"].append(bc_), n["br_i"].append(bi_)
    out = []
    for (pid, sha, name, h, market), g in groups.items():
        n, target = g["n_scored"], prereg.get((sha, name))
        mc, mi, bc, bi = _mean(g["ll_c"]), _mean(g["ll_i"]), _mean(g["br_c"]), _mean(g["br_i"])
        claim = ("no claim: no preregistered sample size is set" if target is None else
                 f"no claim: {n} of {target} preregistered games settled" if n < target else
                 "sample size reached: apply the preregistered decision rule, nothing is implied here")
        out.append({"policy_id": pid, "policy_sha": sha, "spec_name": name, "spec_hash": h, "market": market,
                    **{k: g[k] for k in ("n_scored", "n_unsettled", "n_void", "n_unscorable", "n_no_incumbent", "n_paired", "unpaired")},
                    "log_loss_challenger": mc, "log_loss_incumbent": mi, "log_loss_diff": None if mc is None else mc - mi,
                    "brier_challenger": bc, "brier_incumbent": bi, "brier_diff": None if bc is None else bc - bi,
                    "preregistered_n": target, "claim": claim})
    pairing = {"paired": 0, "unpaired": {}}      # first rows per (policy, spec, game, market): paired with the published run, or why not
    for g in out:
        if g["market"] in lc.MARKETS:
            pairing["paired"] += g["n_paired"]
            for why, n in g["unpaired"].items():
                pairing["unpaired"][why] = pairing["unpaired"].get(why, 0) + n
    return {"label": LABEL, "groups": out, "pairing": pairing}


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
        out = evaluate(store, cc, f"{a.sport}-")
    if a.json:
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0
    print(out["label"])
    for g in out["groups"]:
        d = "n/a" if g["log_loss_diff"] is None else f"{g['log_loss_diff']:+.4f}"
        b = "n/a" if g["brier_diff"] is None else f"{g['brier_diff']:+.4f}"
        print(f"{g['policy_id']} {g['spec_name']} {g['market']}: scored {g['n_scored']} unsettled {g['n_unsettled']} void {g['n_void']} "
              f"no_incumbent {g['n_no_incumbent']} | log loss diff {d} brier diff {b} | {g['claim']}")
    if out["groups"]:
        pr = out["pairing"]
        print(f"{a.sport} pairing with the published forecast (rows, not games): paired {pr['paired']}, unpaired {sum(pr['unpaired'].values())} "
              f"{json.dumps(pr['unpaired'], sort_keys=True)}")
    if not out["groups"]:
        print(out.get("note", "no challenger rows recorded for this sport yet"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
