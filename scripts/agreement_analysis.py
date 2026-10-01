"""Descriptive analysis: how often the winner is right when Elo, efficiency_margin, logistic and logistic_v2 all pick
the same side. Reads saved development (validation-season) screening predictions only; computes nothing on the spent
test seasons, changes no model or rule, and is not part of model promotion.

Usage: python3 scripts/agreement_analysis.py data/reports/screening-<timestamp>/predictions.csv [--json out.json]
       backend/.venv/bin/python scripts/agreement_analysis.py <predictions.csv> --combinations [--json out.json]

--combinations adds a fixed set, defined here before anything was computed:
- pairwise agreement: for each of the 6 model pairs, the games where both pick the same team;
- majority vote: the team picked by at least 3 of the 4 models (2-2 splits have no majority and are reported apart);
- averaged probabilities: the equal-weight mean of all four models and of each pair, scored like a model and
  compared with Elo and with logistic_v2 by the project's paired week-block bootstrap (backtest.paired_bootstrap).
No weights are fitted and nothing is selected; with 14 intervals some will look notable by chance.
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


def combinations(games):
    """Pairwise agreement, majority vote and averaged probabilities (see the module docstring)."""
    import itertools
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    from app import backtest, metrics   # the project's own scoring and bootstrap, not a copy

    def week(g):
        iso = datetime.strptime(g["cutoff"], "%Y-%m-%dT%H:%M:%SZ").isocalendar()
        return (iso[0], iso[1])
    all_games = list(games.items())
    n = len(all_games)

    pairwise = {}
    for a, b in itertools.combinations(MODELS, 2):
        same = [g for _, g in all_games if pick(g[a]) is not None and pick(g[a]) == pick(g[b])]
        diff = [g for _, g in all_games if pick(g[a]) is not None and pick(g[b]) is not None and pick(g[a]) != pick(g[b])]
        pairwise[f"{a}|{b}"] = {
            "agree": {**rate([(week(g), pick(g[a]) == g["home_won"]) for g in same]), "coverage": len(same) / n},
            "disagree": {"games": len(diff), a: model_scores(diff, a), b: model_scores(diff, b)}}

    splits = {"4-0": [], "3-1": [], "2-2": []}
    dissent = {m: {"games": 0, "dissenter_right": 0} for m in MODELS}
    for _, g in all_games:
        home_votes = sum(pick(g[m]) is True for m in MODELS)
        key = {4: "4-0", 0: "4-0", 3: "3-1", 1: "3-1", 2: "2-2"}[home_votes]
        g["majority_home"] = None if key == "2-2" else home_votes >= 3
        splits[key].append(g)
        if key == "3-1":
            odd = next(m for m in MODELS if pick(g[m]) != g["majority_home"])
            dissent[odd]["games"] += 1
            dissent[odd]["dissenter_right"] += pick(g[odd]) == g["home_won"]
    with_majority = splits["4-0"] + splits["3-1"]
    majority = {
        "majority_exists": {**rate([(week(g), g["majority_home"] == g["home_won"]) for g in with_majority]),
                            "coverage": len(with_majority) / n},
        "split_4_0": {**rate([(week(g), g["majority_home"] == g["home_won"]) for g in splits["4-0"]]),
                      "coverage": len(splits["4-0"]) / n},
        "split_3_1": {**rate([(week(g), g["majority_home"] == g["home_won"]) for g in splits["3-1"]]),
                      "coverage": len(splits["3-1"]) / n, "dissenting_model": dissent},
        "split_2_2_no_majority": {"games": len(splits["2-2"]), "coverage": len(splits["2-2"]) / n,
                                  "home_win_rate": (sum(g["home_won"] for g in splits["2-2"]) / len(splits["2-2"])
                                                    if splits["2-2"] else None)},
    }

    def as_preds(prob):
        return [{"game_id": gid, "p_home": prob(g), "home_won": g["home_won"], "cutoff_at": g["cutoff"]}
                for gid, g in all_games]
    singles = {m: as_preds(lambda g, m=m: g[m]) for m in MODELS}

    def scored(preds):
        probs, ys = [p["p_home"] for p in preds], [p["home_won"] for p in preds]
        out = {"accuracy": metrics.accuracy(probs, ys), "brier": metrics.brier(probs, ys),
               "log_loss": metrics.log_loss(probs, ys)}
        for ref in ("elo", "logistic_v2"):
            boot = backtest.paired_bootstrap(preds, singles[ref])
            out[f"brier_minus_{ref}"] = {"mean_diff": boot["mean_diff"], "ci95": boot["ci95"]}
        return out
    groups = [MODELS] + list(itertools.combinations(MODELS, 2))
    averages = {"mean(" + ",".join(grp) + ")": scored(as_preds(lambda g, grp=grp: sum(g[m] for m in grp) / len(grp)))
                for grp in groups}
    return {"pairwise_agreement": pairwise, "majority_vote": majority,
            "single_models": {m: scored(singles[m]) for m in MODELS}, "averaged_probabilities": averages,
            "bootstrap": "paired per-game Brier difference, ISO-week blocks, seed 20260930, 2000 resamples"}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    games = load(sys.argv[1])
    result = analyse(games)
    if "--combinations" in sys.argv:
        result["combinations"] = combinations(games)
    result["source"] = sys.argv[1]
    text = json.dumps(result, indent=2)
    if "--json" in sys.argv:
        with open(sys.argv[sys.argv.index("--json") + 1], "w") as f:
            f.write(text + "\n")
    print(text)
