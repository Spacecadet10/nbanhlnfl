"""Read-only model diagnostics on non-test seasons (`python -m app.cli --sport <s> diagnostics`).

Exploratory only: no model, forecast, stored row or identity-hashed definition changes, and test seasons are never loaded
(NBA validation period, NHL development period). Every fit uses the shared point-in-time feature walk and the registry
reference learners (docs/CANDIDATE_POOL.md §3), so the numbers describe those learners, not the sealed live models.

- permutation importance: fit on seasons before the last loadable season, shuffle one input at a time on that season;
- learning curve: the same last season, trained on the 1, 2, … most recent earlier seasons;
- walk-forward errors: MAE / RMSE / median absolute error / bias / R² of points and goals, confusion matrix of winners;
- missing information (NHL): goalies with no earlier games carry a 0.0 rating; an IterativeImputer (BayesianRidge),
  fitted on training seasons only, is compared with that default;
- data checks: duplicate games; team and goalie names with encoding damage or several spellings.

Writes only report files under data/reports/diagnostics-<sport>/, except that on NHL `goalies.load` may refresh its own
parsed play-by-play cache under data/research/candidates/ (as every pool study does); that cache covers every season,
but only the walked (non-test) games are used.
"""
import html
import json
import math
import random
import statistics
import unicodedata
from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable, Optional

from . import backtest, config, metrics
from .candidates import engine, features, registry
from .candidates.features import FEATURE_SETS
from .models.hockey import regulation_nll

PERIOD = {"nba": "validation", "nhl": "development"}
SPECS = {"nba": ("logit_l2:nba_base", "ridge:nba_base"), "nhl": ("logit_l2:nhl_goalie", "poisson_draw:nhl_goalie")}
REPEATS = 5
SEED = 20261003
GOALIE_RATINGS = (("goalie_home", "goalie_exp_home"), ("goalie_away", "goalie_exp_away"))
MOJIBAKE = ("Ã", "Â", "â€", "�")


# --- inputs ---------------------------------------------------------------------------------------------------------
def feature_rows(conn, sport: str) -> list:
    """The shared feature walk over the period's seasons (same recorder set-up as the candidate pool study)."""
    if sport == "nba":
        _, cfg = backtest.load_frozen_decision(conn)
        period = PERIOD["nba"]
        rows, events = backtest.load_rows(conn, period), backtest.load_box_events(conn, period)
        recorder = features.NbaRecorder(cfg)
    else:
        from . import nhl_eval, nhl_shots, sports
        from .candidates import goalies
        from .models.hockey import GoalsConfig, HockeyEloConfig
        d = nhl_eval.load_decision(conn)
        rows = nhl_eval.load_rows(conn, PERIOD["nhl"])
        events = nhl_shots.load_events(conn, rows)
        detail = goalies.load(conn)
        starters = goalies.actual_starters(conn, detail)
        recorder = features.NhlRecorder(HockeyEloConfig(**d["hockey_elo_config"]), GoalsConfig(**d["goals_config"]),
                                        sports.NHL.rating_key, detail,
                                        starters=lambda g: (starters.get(g.game_id, {}), {"goalie_source": "actual_starter"}))
    walked = features.walk(recorder, rows, events)
    teams = {g.game.game_id: (g.game.home_team_id, g.game.away_team_id) for g in rows}
    for r in walked.values():
        r.meta["teams"] = teams[r.game_id]
    return sorted(walked.values(), key=lambda r: (r.cutoff_at, r.tipoff_at, r.game_id))


def holdout_split(rows: list, season: int) -> tuple[list, list]:
    """(training rows available before the season's first cutoff, scored rows of that season)."""
    hold = [r for r in rows if r.season == season and r.y is not None]
    return engine.training_rows(rows, season, min(r.cutoff_at for r in hold)), hold


# --- per-game losses ------------------------------------------------------------------------------------------------
def losses(spec, model, rows: list, X: Optional[list] = None) -> dict[str, list]:
    """Per-game loss for each scored quantity of the head (lower is better); X defaults to the rows' own inputs."""
    preds = model.predict(X if X is not None else engine._X(rows, FEATURE_SETS[spec.features]))
    if spec.head == "binary":
        return {"log_loss": [-math.log(min(1 - metrics.EPS, max(metrics.EPS, float(p) if r.y["home_won"] else 1 - float(p))))
                             for r, p in zip(rows, preds)]}
    if spec.head == "normal":
        return {"margin_abs_error": [abs(r.y["margin"] - float(p[0])) for r, p in zip(rows, preds)],
                "total_abs_error": [abs(r.y["total"] - float(p[2])) for r, p in zip(rows, preds)]}
    if spec.head == "goals":
        return {"regulation_nll": [regulation_nll(float(p[0]), float(p[1]), r.y["regulation"])
                                   for r, p in zip(rows, preds) if r.y["regulation"] is not None]}
    raise KeyError(spec.head)


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def paired(a: list, b: list) -> dict:
    """Mean of b − a per game with a normal-approximation 95% interval (a, b: per-game losses on the same games)."""
    d = [y - x for x, y in zip(a, b)]
    se = statistics.stdev(d) / math.sqrt(len(d)) if len(d) > 1 else None
    m = _mean(d)
    return {"mean": m, "interval": None if se is None else (m - 1.96 * se, m + 1.96 * se), "games": len(d)}


# --- 1. permutation importance --------------------------------------------------------------------------------------
def permutation_importance(spec, rows: list, season: int, repeats: int = REPEATS, seed: int = SEED) -> dict:
    """Loss increase on `season` when one input is shuffled across that season's games (model fitted on earlier
    seasons). ≤ 0 means the model does not use the input on later games. Correlated inputs share credit, so a low
    value for one of two similar inputs does not mean both can go."""
    train, hold = holdout_split(rows, season)
    model = engine.fit(spec, train)
    names = FEATURE_SETS[spec.features]
    X = engine._X(hold, names)
    base = {k: _mean(v) for k, v in losses(spec, model, hold, X).items()}
    rng = random.Random(seed)
    out = {}
    for j, name in enumerate(names):
        deltas = {k: [] for k in base}
        for _ in range(repeats):
            col = [x[j] for x in X]
            rng.shuffle(col)
            s = losses(spec, model, hold, [x[:j] + [c] + x[j + 1:] for x, c in zip(X, col)])
            for k in base:
                deltas[k].append(_mean(s[k]) - base[k])
        out[name] = {k: {"mean": _mean(v), "min": min(v), "max": max(v)} for k, v in deltas.items()}
    return {"model": spec.name, "season": season, "training_games": len(train), "scored_games": len(hold),
            "baseline": base, "repeats": repeats, "features": out}


# --- 2. learning curve ----------------------------------------------------------------------------------------------
def learning_curve(spec, rows: list, season: int) -> dict:
    """Loss on `season` when training on the k most recent earlier seasons, k = 1, 2, …; each step is compared
    game by game with the previous one (negative = adding the older season helped)."""
    train, hold = holdout_split(rows, season)
    earlier = sorted({r.season for r in train}, reverse=True)
    points, previous = [], None
    for k in range(1, len(earlier) + 1):
        keep = set(earlier[:k])
        subset = [r for r in train if r.season in keep]
        if len(subset) < registry.MIN_TRAIN[spec.sport]:
            continue
        per_game = losses(spec, engine.fit(spec, subset), hold)
        point = {"seasons": sorted(keep), "training_games": len(subset), "loss": {m: _mean(v) for m, v in per_game.items()}}
        if previous is not None:
            point["change_vs_previous"] = {m: paired(previous[m], v) for m, v in per_game.items()}
        points.append(point)
        previous = per_game
    return {"model": spec.name, "season": season, "scored_games": len(hold), "points": points}


# --- 3. walk-forward errors -----------------------------------------------------------------------------------------
def _expected_scores(o: dict) -> tuple[float, float]:
    if o["head"] == "normal":
        return (o["t"] + o["m"]) / 2, (o["t"] - o["m"]) / 2
    pmf = engine.distribution(o).pmf
    return sum(h * p for (h, _), p in pmf.items()), sum(a * p for (_, a), p in pmf.items())


def walk_forward(sport: str, rows: list) -> dict:
    """Season-start refits over the period (the pool engine's walk); errors on the period's own seasons only."""
    from . import nhl_eval
    scored = set((backtest.PERIODS if sport == "nba" else nhl_eval.PERIODS)[PERIOD[sport]])
    by_id = {r.game_id: r for r in rows}
    binary, points = (registry.BY_NAME[n] for n in SPECS[sport])
    out = {"seasons": sorted(scored), "winner": {"model": binary.name, "by_season": {}},
           "scores": {"model": points.name, "by_season": {}}, "games": []}
    win, _, _ = engine.walk(binary, rows)
    for s in sorted(scored):
        g = [gid for gid, o in win.items() if by_id[gid].season == s and by_id[gid].y is not None]
        probs, ys = [win[i]["p"] for i in g], [by_id[i].y["home_won"] for i in g]
        out["winner"]["by_season"][s] = {"games": len(g), "log_loss": metrics.log_loss(probs, ys),
                                         "accuracy": metrics.accuracy(probs, ys), "confusion": metrics.confusion(probs, ys)}
    dist, _, _ = engine.walk(points, rows)
    for s in sorted(scored):
        ids = [gid for gid, o in dist.items() if by_id[gid].season == s and by_id[gid].y is not None]
        # engine.walk leaves out games with no fit yet, or (normal head) no residual spread yet: counted, not hidden
        out["scores"].setdefault("games_without_output", {})[s] = sum(r.season == s and r.y is not None for r in rows) - len(ids)
        exp = [_expected_scores(dist[i]) for i in ids]
        ys = [by_id[i].y for i in ids]
        out["scores"]["by_season"][s] = {
            "total": metrics.regression_errors([h + a for h, a in exp], [y["total"] for y in ys]),
            "margin": metrics.regression_errors([h - a for h, a in exp], [y["margin"] for y in ys]),
            "home_score": metrics.regression_errors([h for h, _ in exp], [y["home"] for y in ys]),
            "away_score": metrics.regression_errors([a for _, a in exp], [y["away"] for y in ys])}
        for i, (h, a), y in zip(ids, exp, ys):
            r = by_id[i]
            out["games"].append({"game_id": i, "season": s, "tipoff_at": r.tipoff_at, "teams": r.meta["teams"],
                                 "total_error": y["total"] - (h + a), "margin_error": y["margin"] - (h - a)})
    return out


# --- 4. missing information -----------------------------------------------------------------------------------------
def missing_values(rows: list, names) -> dict:
    """Inputs that are None or NaN in the feature table (the learners would fail on them, so this should be empty)."""
    bad = {}
    for r in rows:
        for k in names:
            v = r.x.get(k)
            if v is None or (isinstance(v, float) and math.isnan(v)):
                bad[k] = bad.get(k, 0) + 1
    return bad


def _no_goalie_history(r) -> tuple[bool, bool]:
    return tuple(r.x[exp] == 0 for _, exp in GOALIE_RATINGS)


def _masked(rows: list, names) -> "np.ndarray":
    """Inputs with unknown goalie ratings as NaN. goalie_diff is masked too: it was computed with the 0.0 default, and
    left in place it lets the imputer recover that default exactly (rating = diff ± the other rating)."""
    import numpy as np
    X = np.asarray(engine._X(rows, names), dtype=float)
    for i, r in enumerate(rows):
        unknown = _no_goalie_history(r)
        for (rating, _), u in zip(GOALIE_RATINGS, unknown):
            if u:
                X[i, names.index(rating)] = np.nan
        if any(unknown):
            X[i, names.index("goalie_diff")] = np.nan
    return X


def _with_imputed(rows: list, imputer, names) -> list:
    filled = imputer.transform(_masked(rows, names))
    out = []
    for r, f in zip(rows, filled):
        x = dict(r.x)
        for (rating, _), unknown in zip(GOALIE_RATINGS, _no_goalie_history(r)):
            if unknown:
                x[rating] = float(f[names.index(rating)])
        x["goalie_diff"] = x["goalie_home"] - x["goalie_away"]
        out.append(replace(r, x=x))
    return out


def goalie_imputation(rows: list, season: int) -> dict:
    """A goalie with no earlier games is rated 0.0 (league average). Compared on `season`: that default vs an
    IterativeImputer (BayesianRidge) fitted on the training rows only, both with logit_l2:nhl_goalie."""
    from sklearn.experimental import enable_iterative_imputer  # noqa: F401  (activates IterativeImputer)
    from sklearn.impute import IterativeImputer
    from sklearn.linear_model import BayesianRidge
    spec = registry.BY_NAME["logit_l2:nhl_goalie"]
    names = list(FEATURE_SETS[spec.features])
    train, hold = holdout_split(rows, season)
    count = {s: {"games": 0, "with_unknown_goalie": 0} for s in sorted({r.season for r in rows})}
    for r in rows:
        count[r.season]["games"] += 1
        count[r.season]["with_unknown_goalie"] += any(_no_goalie_history(r))
    affected = [i for i, r in enumerate(hold) if any(_no_goalie_history(r))]
    result = {"model": spec.name, "season": season, "rows_by_season": count, "affected_scored_games": len(affected)}
    if not affected:
        return result | {"comparison": None, "reading": "no scored game has a goalie without earlier games"}
    imputer = IterativeImputer(estimator=BayesianRidge(), random_state=SEED, max_iter=10).fit(_masked(train, names))
    default = losses(spec, engine.fit(spec, train), hold)["log_loss"]
    imputed_hold = _with_imputed(hold, imputer, names)
    filled = losses(spec, engine.fit(spec, _with_imputed(train, imputer, names)), imputed_hold)["log_loss"]
    return result | {"comparison": {"all_scored_games": paired(default, filled),
                                    "affected_games": paired([default[i] for i in affected], [filled[i] for i in affected])},
                     "reading": "negative mean = imputation lowered log loss; an interval that includes 0 is no evidence"}


# --- 7. data checks -------------------------------------------------------------------------------------------------
def _damaged(name: str) -> Optional[str]:
    if any(m in name for m in MOJIBAKE):
        return "encoding damage"
    if name != unicodedata.normalize("NFC", name):
        return "not NFC-normalised"
    if name != name.strip() or "  " in name:
        return "extra spaces"
    return None


def data_checks(conn) -> dict:
    """Read-only integrity checks over the whole store (ids and names only; no score or forecast is evaluated). A final
    game without scores needs no check: the games table rejects it (migrations/0001_initial.sql)."""
    from .candidates.goalies import normalise
    eastern = backtest.EASTERN
    seen, duplicates = {}, []
    for gid, tip, h, a in conn.execute("SELECT game_id, tipoff_at, home_team_id, away_team_id FROM games "
                                       "WHERE season_type NOT IN ('preseason', 'other') AND status NOT IN ('postponed', 'canceled') "
                                       "ORDER BY tipoff_at, game_id"):
        day = datetime.strptime(tip, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(eastern).date().isoformat()
        key = (day, h, a)
        if key in seen:
            duplicates.append({"date": day, "home": h, "away": a, "game_ids": [seen[key], gid]})
        else:
            seen[key] = gid
    names = []
    for tid, name in conn.execute("SELECT team_id, name FROM teams"):
        if (why := _damaged(name)):
            names.append({"table": "teams", "id": tid, "name": name, "problem": why})
    spellings, ids = {}, {}
    for pid, name in conn.execute("SELECT DISTINCT espn_player_id, player_name FROM goalie_snapshots "
                                  "WHERE player_name IS NOT NULL"):
        if (why := _damaged(name)):
            names.append({"table": "goalie_snapshots", "id": pid, "name": name, "problem": why})
        if pid:
            spellings.setdefault(pid, set()).add(name)
        ids.setdefault(normalise(name), set()).add(pid)
    return {"duplicate_games": duplicates, "name_problems": names,
            "one_id_several_spellings": {k: sorted(v) for k, v in spellings.items() if len(v) > 1},
            "one_name_several_ids": {k: sorted(x for x in v if x) for k, v in ids.items() if len({x for x in v if x}) > 1}}


# --- 6. report ------------------------------------------------------------------------------------------------------
def _bars(title: str, items: list, fmt: str = "{:.2f}") -> str:
    """Horizontal bar chart (inline SVG) of (label, value) pairs; negative values drawn left of the axis."""
    items = [(label, v) for label, v in items if v is not None]
    if not items:
        return f"<h3>{html.escape(title)}</h3><p>No data.</p>"
    top = max(abs(v) for _, v in items) or 1.0
    row, label_w, half = 20, 170, 220
    neg = any(v < 0 for _, v in items)
    x0 = label_w + (half if neg else 0)
    parts = []
    for i, (label, v) in enumerate(items):
        y = i * row
        w = abs(v) / top * half
        x = x0 - w if v < 0 else x0
        parts.append(f'<text x="{label_w - 6}" y="{y + 14}" text-anchor="end">{html.escape(str(label))}</text>'
                     f'<rect x="{x:.1f}" y="{y + 3}" width="{max(w, 0.5):.1f}" height="{row - 6}" class="{"neg" if v < 0 else "pos"}"/>'
                     f'<text x="{(x + w + 4) if v >= 0 else (x - 4):.1f}" y="{y + 14}" text-anchor="{"start" if v >= 0 else "end"}">'
                     f'{fmt.format(v)}</text>')
    width = x0 + half + 70
    return (f'<h3>{html.escape(title)}</h3><svg viewBox="0 0 {width} {len(items) * row + 4}" role="img" '
            f'aria-label="{html.escape(title)}"><line x1="{x0}" x2="{x0}" y1="0" y2="{len(items) * row}" class="axis"/>'
            + "".join(parts) + "</svg>")


def _histogram(title: str, values: list, width: float) -> str:
    if not values:
        return _bars(title, [])
    lo = math.floor(min(values) / width) * width
    counts = {}
    for v in values:
        b = lo + math.floor((v - lo) / width) * width
        counts[b] = counts.get(b, 0) + 1
    return _bars(title, [(f"{b:+.0f} to {b + width:+.0f}", c) for b, c in sorted(counts.items())], "{:.0f}")


def render_html(report: dict, team_names: dict) -> str:
    sport = report["sport"].upper()
    games = report["walk_forward"]["games"]
    sections = [f"<h1>{sport} model diagnostics</h1><p class=note>{html.escape(report['scope'])}</p>"]
    for pi in report["permutation_importance"]:
        for metric in pi["baseline"]:
            items = sorted(((n, v[metric]["mean"]) for n, v in pi["features"].items()), key=lambda t: -t[1])
            sections.append(_bars(f"Permutation importance — {pi['model']}, season {pi['season']} ({metric} increase)",
                                  items, "{:+.4f}"))
    for lc in report["learning_curve"]:
        for metric in (lc["points"][0]["loss"] if lc["points"] else ()):
            items = [(f"{len(p['seasons'])} season(s), {p['training_games']} games", p["loss"][metric]) for p in lc["points"]]
            sections.append(_bars(f"Learning curve — {lc['model']}, season {lc['season']} ({metric}; lower is better)",
                                  items, "{:.4f}"))
    sections.append(_histogram("Total error (actual − expected), all scored games", [g["total_error"] for g in games], 5.0
                               if report["sport"] == "nba" else 1.0))
    sections.append(_histogram("Margin error (actual − expected), all scored games", [g["margin_error"] for g in games], 5.0
                               if report["sport"] == "nba" else 1.0))
    by_team = {}
    for g in games:
        for t in g["teams"]:
            by_team.setdefault(t, []).append(abs(g["total_error"]))
    sections.append(_bars("Mean absolute total error by team (games involving the team)",
                          sorted(((team_names.get(t, t), _mean(v)) for t, v in by_team.items()), key=lambda t: -t[1])))
    by_month = {}
    for g in games:
        by_month.setdefault(g["tipoff_at"][:7], []).append(g["total_error"])
    sections.append(_bars("Mean total error by month (positive = more scoring than expected)",
                          [(m, _mean(v)) for m, v in sorted(by_month.items())], "{:+.2f}"))
    out = report["outliers"]
    rows = "".join(f"<tr><td>{g['tipoff_at'][:10]}</td><td>{html.escape(team_names.get(g['teams'][1], g['teams'][1]))} at "
                   f"{html.escape(team_names.get(g['teams'][0], g['teams'][0]))}</td><td>{g['total_error']:+.1f}</td>"
                   f"<td>{g['margin_error']:+.1f}</td></tr>" for g in out["largest"])
    sections.append(f"<h3>Largest total errors</h3><p>{out['beyond_3_sd']} of {len(games)} games are more than 3 standard "
                    f"deviations from the mean total error.</p><table><tr><th>Date</th><th>Game</th><th>Total error</th>"
                    f"<th>Margin error</th></tr>{rows}</table>")
    sections.append("<h3>Numbers</h3><p>Every figure above, plus walk-forward errors, the confusion matrix, the "
                    "missing-information comparison and the data checks, is in <code>report.json</code> next to this file.</p>")
    return ("<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,"
            f"initial-scale=1'><title>{sport} diagnostics</title><style>"
            ":root{--bg:#fff;--fg:#1d1d1f;--muted:#666;--pos:#3b6fb6;--neg:#c0563f;--line:#ccc}"
            "@media (prefers-color-scheme:dark){:root{--bg:#161618;--fg:#eee;--muted:#aaa;--pos:#6f9be0;--neg:#e07f68;"
            "--line:#444}}body{background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif;max-width:900px;"
            "margin:0 auto;padding:16px}svg{width:100%;height:auto;font-size:11px}svg text{fill:var(--fg)}"
            ".pos{fill:var(--pos)}.neg{fill:var(--neg)}.axis{stroke:var(--line)}.note{color:var(--muted)}"
            "table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid var(--line);padding:3px 6px;"
            "text-align:left}</style></head><body>" + "".join(sections) + "</body></html>")


def run(conn, sport: str, log: Callable[[str], None] = print) -> dict:
    rows = feature_rows(conn, sport)
    log(f"{sport}: feature walk {len(rows)} games ({PERIOD[sport]} period; test seasons not loaded)")
    season = max(r.season for r in rows if r.y is not None)
    specs = [registry.BY_NAME[n] for n in SPECS[sport]]
    report = {"sport": sport, "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "scope": f"Exploratory diagnostics on the {PERIOD[sport]} period (seasons up to {season}); registry reference "
                       f"learners on the shared feature walk, not the sealed live models. Test seasons were not loaded.",
              "feature_table": features.table_version({r.game_id: r for r in rows}),
              "missing_values": {s.features: missing_values(rows, FEATURE_SETS[s.features]) for s in specs}}
    report["permutation_importance"] = [permutation_importance(s, rows, season) for s in specs]
    log("permutation importance done")
    report["learning_curve"] = [learning_curve(s, rows, season) for s in specs]
    log("learning curves done")
    report["walk_forward"] = walk_forward(sport, rows)
    errs = [g["total_error"] for g in report["walk_forward"]["games"]]
    mu, sd = (_mean(errs), statistics.pstdev(errs)) if errs else (0.0, 0.0)
    report["outliers"] = {"beyond_3_sd": sum(abs(e - mu) > 3 * sd for e in errs) if sd else 0,
                          "largest": sorted(report["walk_forward"]["games"], key=lambda g: -abs(g["total_error"]))[:15]}
    log("walk-forward errors done")
    report["goalie_imputation"] = goalie_imputation(rows, season) if sport == "nhl" else None
    report["data_checks"] = data_checks(conn)
    out = config.data_dir() / "reports" / f"diagnostics-{sport}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=1, default=str))
    team_names = {t: n for t, n in conn.execute("SELECT team_id, name FROM teams")}
    (out / "report.html").write_text(render_html(report, team_names))
    report["output_dir"] = str(out)
    return report
