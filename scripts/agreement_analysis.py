"""Descriptive analysis: how often the winner is right when Elo, efficiency_margin, logistic and logistic_v2 all pick
the same side. Reads saved development (validation-season) screening predictions only; computes nothing on the spent
test seasons, changes no model or rule, and is not part of model promotion.

Usage: python3 scripts/agreement_analysis.py data/reports/screening-<timestamp>/predictions.csv [--json out.json]
"""
import csv
import json
import math
import random
import sys
from collections import defaultdict
from datetime import datetime

MODELS = ("elo", "efficiency_margin", "logistic", "logistic_v2")   # voting models; baselines never vote
SEED, SAMPLES = 20260930, 2000
HALF = 5e-7   # the CSV stores 6 decimals: a probability within this of 0.5 is an abstention


def load(path):
    games = defaultdict(dict)
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            name = next((m for m in MODELS if r["model_version"].startswith(m + "-")), None)
            if name is None:
                continue
            g = games[r["game_id"]]
            g[name] = float(r["p_home"])
            g.update(season=int(r["season"]), cutoff=r["cutoff_at"], home_won=r["home_won"] == "1")
    complete = {k: g for k, g in games.items() if all(m in g for m in MODELS)}
    if len(complete) != len(games):
        raise SystemExit(f"{len(games) - len(complete)} games lack a prediction from one of {MODELS}")
    return complete


def pick(p):
    return None if abs(p - 0.5) < HALF else p > 0.5


def wilson(wins, n, z=1.959964):
    if n == 0:
        return [None, None]
    p, d = wins / n, 1 + z * z / n
    c, h = (p + z * z / (2 * n)) / d, z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [c - h, c + h]


def week_bootstrap(rows):
    """95% interval for the win rate, resampling ISO weeks of the cutoff (games in a week are not independent)."""
    blocks = defaultdict(list)
    for week, won in rows:
        blocks[week].append(won)
    weeks = list(blocks.values())
    if not weeks:
        return [None, None]
    rng, stats = random.Random(SEED), []
    for _ in range(SAMPLES):
        sample = [weeks[rng.randrange(len(weeks))] for _ in weeks]
        stats.append(sum(map(sum, sample)) / sum(map(len, sample)))
    stats.sort()
    return [stats[int(0.025 * SAMPLES)], stats[int(0.975 * SAMPLES) - 1]]


def rate(rows):
    wins, n = sum(w for _, w in rows), len(rows)
    return {"games": n, "wins": wins, "win_rate": wins / n if n else None, "wilson95": wilson(wins, n),
            "week_block_bootstrap95": week_bootstrap(rows)}


def model_scores(games, model):
    n = len(games)
    if not n:
        return {"games": 0}
    right = sum((g[model] > 0.5) == g["home_won"] for g in games if pick(g[model]) is not None)
    decided = sum(pick(g[model]) is not None for g in games)
    brier = sum((g[model] - g["home_won"]) ** 2 for g in games) / n
    return {"games": n, "picks": decided, "wins": right, "accuracy": right / decided if decided else None, "brier": brier}


def analyse(games):
    def week(g):
        iso = datetime.strptime(g["cutoff"], "%Y-%m-%dT%H:%M:%SZ").isocalendar()
        return (iso[0], iso[1])
    groups = {"unanimous": [], "disagreement": [], "abstention": []}
    for g in games.values():
        picks = [pick(g[m]) for m in MODELS]
        key = "abstention" if None in picks else "unanimous" if len(set(picks)) == 1 else "disagreement"
        g["pick_home"] = picks[0] if key == "unanimous" else None
        groups[key].append(g)
    uni = groups["unanimous"]
    won = [(week(g), g["pick_home"] == g["home_won"]) for g in uni]
    out = {
        "models": list(MODELS), "games": len(games),
        "seasons": sorted({g["season"] for g in games.values()}),
        "coverage": {k: {"games": len(v), "share": len(v) / len(games)} for k, v in groups.items()},
        "unanimous": {**rate(won),
                      "home_picks": sum(g["pick_home"] for g in uni), "away_picks": sum(not g["pick_home"] for g in uni),
                      "mean_predicted_probability_of_pick": sum(
                          sum(g[m] if g["pick_home"] else 1 - g[m] for m in MODELS) / len(MODELS) for g in uni) / len(uni),
                      "by_season": {s: rate([(week(g), g["pick_home"] == g["home_won"]) for g in uni if g["season"] == s])
                                    for s in sorted({g["season"] for g in uni})}},
        "each_model_on_unanimous_games": {m: model_scores(uni, m) for m in MODELS},
        "each_model_on_disagreement_games": {m: model_scores(groups["disagreement"], m) for m in MODELS},
        "each_model_on_all_games": {m: model_scores(list(games.values()), m) for m in MODELS},
        "disagreement_home_win_rate": (sum(g["home_won"] for g in groups["disagreement"]) / len(groups["disagreement"])
                                       if groups["disagreement"] else None),
    }
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    result = analyse(load(sys.argv[1]))
    result["source"] = sys.argv[1]
    text = json.dumps(result, indent=2)
    if "--json" in sys.argv:
        with open(sys.argv[sys.argv.index("--json") + 1], "w") as f:
            f.write(text + "\n")
    print(text)
