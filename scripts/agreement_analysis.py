"""Descriptive analysis: how often the winner is right when Elo, efficiency_margin, logistic and logistic_v2 all pick
the same side. Reads saved development (validation-season) screening predictions only; computes nothing on the spent
test seasons, changes no model or rule, and is not part of model promotion.

Usage: python3 scripts/agreement_analysis.py data/reports/screening-<timestamp>/predictions.csv [--json out.json]
       backend/.venv/bin/python scripts/agreement_analysis.py <predictions.csv> --combinations [--json out.json]
       python3 scripts/agreement_analysis.py data/reports/research-stacking-<timestamp>/predictions.csv --five
       python3 scripts/agreement_analysis.py data/reports/screening-<timestamp>/predictions.csv --ranges

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
            label = r.get("model") or r["model_version"]   # research CSVs name the model directly
            name = next((m for m in MODELS if label == m or label.startswith(m + "-")), None)
            if name is None:
                continue
            g = games[r["game_id"]]
            g[name] = float(r["p_home"])
            g.update(season=int(r["season"]), cutoff=r["cutoff_at"], home_won=r["home_won"] == "1",
                     season_type=r.get("season_type"), tipoff=r.get("tipoff_at"))
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


def vote_splits(games, confidence_model="logistic_v2"):
    """Majority-vote splits for any number of voting models, and agreement combined with one model's confidence.
    Fixed before computing: splits by size of the majority; then, at 50% coverage, the most confident games of
    `confidence_model` among all games versus among the unanimous games only."""
    def week(g):
        iso = datetime.strptime(g["cutoff"], "%Y-%m-%dT%H:%M:%SZ").isocalendar()
        return (iso[0], iso[1])
    n, splits = len(games), defaultdict(list)
    for g in games.values():
        home = sum(pick(g[m]) is True for m in MODELS)
        away = sum(pick(g[m]) is False for m in MODELS)
        if home == away:
            splits["tie_or_abstention"].append((week(g), None))
            continue
        majority_home = home > away
        splits[f"{max(home, away)}-{min(home, away)}"].append((week(g), majority_home == g["home_won"]))
    out = {"models": list(MODELS), "games": n, "splits": {}}
    for key in sorted(splits, reverse=True):
        rows = [r for r in splits[key] if r[1] is not None]
        out["splits"][key] = {**rate(rows), "coverage": len(splits[key]) / n}
    half = round(0.5 * n)
    conf = lambda g: abs(g[confidence_model] - 0.5)  # noqa: E731
    ranked = sorted(games.items(), key=lambda kv: (-conf(kv[1]), kv[0]))
    unanimous = [(k, g) for k, g in ranked if len({pick(g[m]) for m in MODELS}) == 1 and pick(g[MODELS[0]]) is not None]
    top_all, top_unanimous = ranked[:half], unanimous[:half]
    right = lambda rows: [(week(g), (g[confidence_model] > 0.5) == g["home_won"]) for _, g in rows]  # noqa: E731
    out["at_50_percent_coverage"] = {
        f"most_confident_by_{confidence_model}": rate(right(top_all)),
        f"unanimous_then_most_confident_by_{confidence_model}": rate(right(top_unanimous)),
        "games_in_both_selections": len({k for k, _ in top_all} & {k for k, _ in top_unanimous})}
    return out


BANDS = ((0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70), (0.70, 0.75), (0.75, 0.80), (0.80, 1.01))
THRESHOLDS = (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80)
PHASES = {10: "Oct-Nov", 11: "Oct-Nov", 12: "Dec", 1: "Jan", 2: "Feb", 3: "Mar-Apr", 4: "Mar-Apr"}


def confidence_ranges(games, models=("elo", "logistic_v2")):
    """Win rate by ranges fixed before computing: bands of the pick's stated probability, 'at least X' thresholds,
    home versus away picks, and part of the season. Descriptive; no threshold is chosen from these results."""
    from zoneinfo import ZoneInfo
    eastern = ZoneInfo("America/New_York")

    def week(g):
        iso = datetime.strptime(g["cutoff"], "%Y-%m-%dT%H:%M:%SZ").isocalendar()
        return (iso[0], iso[1])

    def phase(g):
        if g["season_type"] != "regular":
            return "Play-in and playoffs"
        month = datetime.strptime(g["tipoff"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=ZoneInfo("UTC")).astimezone(eastern).month
        return PHASES.get(month, "other")
    n, out = len(games), {}
    for m in models:
        rows = [(g, max(g[m], 1 - g[m]), (g[m] >= 0.5) == g["home_won"]) for g in games.values()]

        def cut(selected):
            r = rate([(week(g), won) for g, _, won in selected])
            r["share_of_games"] = len(selected) / n
            r["mean_stated_probability"] = sum(c for _, c, _ in selected) / len(selected) if selected else None
            return r
        out[m] = {
            "bands": {f"{lo:.2f}-{min(hi, 1.0):.2f}": cut([x for x in rows if lo <= x[1] < hi]) for lo, hi in BANDS},
            "at_least": {f">={t:.2f}": cut([x for x in rows if x[1] >= t]) for t in THRESHOLDS},
            "pick_side": {"home_pick": cut([x for x in rows if x[0][m] >= 0.5]),
                          "away_pick": cut([x for x in rows if x[0][m] < 0.5])},
            "season_phase": {ph: cut([x for x in rows if phase(x[0]) == ph])
                             for ph in ("Oct-Nov", "Dec", "Jan", "Feb", "Mar-Apr", "Play-in and playoffs")},
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

    def paired_accuracy(preds, ref):
        """Accuracy difference on the same games (preds - ref): discordant picks and a week-block bootstrap interval."""
        blocks, only_a, only_b = defaultdict(list), 0, 0
        for p, q in zip(preds, ref):
            a = (p["p_home"] >= 0.5) == p["home_won"]   # same convention as app.metrics.accuracy
            b = (q["p_home"] >= 0.5) == q["home_won"]
            only_a, only_b = only_a + (a and not b), only_b + (b and not a)
            iso = datetime.strptime(p["cutoff_at"], "%Y-%m-%dT%H:%M:%SZ").isocalendar()
            blocks[(iso[0], iso[1])].append(int(a) - int(b))
        weeks, rng, stats = list(blocks.values()), random.Random(SEED), []
        for _ in range(SAMPLES):
            sample = [weeks[rng.randrange(len(weeks))] for _ in weeks]
            stats.append(sum(map(sum, sample)) / sum(map(len, sample)))
        stats.sort()
        return {"diff": (only_a - only_b) / len(preds), "only_this_right": only_a, "only_reference_right": only_b,
                "ci95": [stats[int(0.025 * SAMPLES)], stats[int(0.975 * SAMPLES) - 1]]}

    def scored(preds):
        probs, ys = [p["p_home"] for p in preds], [p["home_won"] for p in preds]
        out = {"accuracy": metrics.accuracy(probs, ys), "brier": metrics.brier(probs, ys),
               "log_loss": metrics.log_loss(probs, ys)}
        for ref in ("elo", "logistic_v2"):
            boot = backtest.paired_bootstrap(preds, singles[ref])
            out[f"brier_minus_{ref}"] = {"mean_diff": boot["mean_diff"], "ci95": boot["ci95"]}
            out[f"accuracy_minus_{ref}"] = paired_accuracy(preds, singles[ref])
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
    if "--five" in sys.argv:   # the five base models of research round 3 (its predictions.csv)
        MODELS = ("elo", "efficiency_margin", "logistic", "logistic_v2", "gbt_v1")
    games = load(sys.argv[1])
    result = analyse(games)
    result["vote_splits"] = vote_splits(games)
    if "--ranges" in sys.argv:
        result["confidence_ranges"] = confidence_ranges(games)
    if "--combinations" in sys.argv:
        result["combinations"] = combinations(games)
    result["source"] = sys.argv[1]
    text = json.dumps(result, indent=2)
    if "--json" in sys.argv:
        with open(sys.argv[sys.argv.index("--json") + 1], "w") as f:
            f.write(text + "\n")
    print(text)
