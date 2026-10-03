"""Player-quality-weighted absences `avail_q` (docs/MODEL_SELECTION.md §19, features-v1). Exploratory research.

`avail_v1` (availability.py) weights each player listed out/doubtful/questionable by expected minutes only. Here the same
listing is also weighted by the player's recent production per minute: Hollinger game score per minute over his last 20
played games strictly before the report date (this season when he has ≥ 5, else including the previous season), centred on
the previous season's league mean per minute. A positive value means the absent player produced more than an average
player per minute. Only the report (published before the 17:45 ET cutoff, as `avail_v1`) and earlier box scores are used.

The report-parsing loop mirrors availability.build_report_facts on purpose: that file is part of the hashed research
definition (agreement.definition_hash), and editing it would restart every live research record for both sports.
"""
import csv
import json
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from . import availability as av
from . import backtest, config
from .db import utc_now

WINDOW, MIN_THIS_SEASON = 20, 5
Q_FEATURES = ("new_absent_q_diff", "all_absent_q_diff")
STUDY = "features-v1"


def game_score(r: dict) -> float:
    """Hollinger game score from one box-score row (missing fields count as 0)."""
    def f(k):
        try:
            v = r.get(k)
            return 0.0 if v in (None, "", "NA") else float(v)
        except ValueError:
            return 0.0
    return (f("points") + 0.4 * f("field_goals_made") - 0.7 * f("field_goals_attempted")
            - 0.4 * (f("free_throws_attempted") - f("free_throws_made")) + 0.7 * f("offensive_rebounds")
            + 0.3 * f("defensive_rebounds") + f("steals") + 0.7 * f("assists") + 0.7 * f("blocks") - 0.4 * f("fouls")
            - f("turnovers"))


class QualityHistory:
    """Per player: (ET date, season, minutes, game score) of played games; league per-minute mean per season."""

    def __init__(self, seasons: list[int], loader: Optional[Callable[[int], list]] = None):
        self.apps: dict[str, list[tuple]] = defaultdict(list)
        totals: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0])
        for season in seasons:
            for r in (loader(season) if loader else _rows(season)):
                pid = r.get("athlete_id")
                if not pid or pid == "NA" or str(r.get("did_not_play", "")).lower() == "true":
                    continue
                try:
                    minutes = float(r.get("minutes") or 0)
                except ValueError:
                    continue
                if minutes <= 0:
                    continue
                tip = datetime.fromisoformat(r["game_date_time"].replace("Z", "+00:00")) if r.get("game_date_time") \
                    else datetime.fromisoformat(r["game_date"])
                day = (tip.astimezone(av.ET) if tip.tzinfo else tip).strftime("%Y-%m-%d")
                gs = game_score(r)
                self.apps[pid].append((day, season, minutes, gs))
                totals[season][0] += gs
                totals[season][1] += minutes
        for v in self.apps.values():
            v.sort()
        self.league = {s: (t[0] / t[1] if t[1] else None) for s, t in totals.items()}

    def quality(self, pid: str, day: str, season: int) -> tuple[float, str]:
        """Game score per minute over the last 20 played games before `day`, minus the previous season's league mean."""
        ref = self.league.get(season - 1)
        if ref is None:
            return 0.0, "no_reference_season"
        before = [a for a in self.apps.get(pid, []) if a[0] < day]
        this = [a for a in before if a[1] == season]
        use = this[-WINDOW:] if len(this) >= MIN_THIS_SEASON else [a for a in before if a[1] >= season - 1][-WINDOW:]
        mins = sum(a[2] for a in use)
        if mins <= 0:
            return 0.0, "no_history"
        return sum(a[3] for a in use) / mins - ref, "ok"


def _rows(season: int) -> list:
    with av.player_box_path(season).open(newline="") as f:
        return list(csv.DictReader(f))


def build_quality_facts(conn: sqlite3.Connection, box: dict, quality: QualityHistory) -> tuple[dict, dict]:
    """{ET date: ReportFacts} where each absent slot also carries quality-weighted lost production per 48 minutes."""
    team_names = {r["name"]: r["team_id"].split(":", 1)[1] for r in conn.execute(
        "SELECT team_id, name FROM teams WHERE CAST(substr(team_id, 6) AS INTEGER) BETWEEN 1 AND 30")}
    idx = av.PlayerIndex(box, av.load_aliases())
    hist = av.MinutesHistory(box)
    cov: dict = defaultdict(int)
    facts = {}
    rdir = av.research_dir() / "reports"
    seasons = av.date_seasons(conn)
    for day in sorted(seasons):
        name = av.report_name(day)
        path = rdir / name
        if not path.exists():
            facts[day] = av.ReportFacts(False, "missing", None)
            continue
        meta = json.loads((rdir / (name + ".json")).read_text())
        try:
            parsed = av.parse_report(path.read_bytes(), name, list(team_names))
        except Exception as exc:
            facts[day] = av.ReportFacts(False, f"parse_error:{type(exc).__name__}", None)
            continue
        pub, _basis = av.publication_time(parsed, meta.get("last_modified"))
        cutoff = datetime.fromisoformat(f"{day}T{av.CUTOFF_HOUR:02d}:{av.CUTOFF_MINUTE:02d}:00").replace(tzinfo=av.ET)
        if pub is None or not parsed.reconciled or pub > cutoff:
            facts[day] = av.ReportFacts(False, "excluded", None)
            continue
        f = av.ReportFacts(True, "ok", pub.strftime("%Y-%m-%dT%H:%M:%SZ"))
        for ns in parsed.not_submitted:
            if ns["game_date"] == day and ns["team"] in team_names:
                f.unsubmitted.add(team_names[ns["team"]])
        season = seasons[day]
        for e in parsed.entries:
            if e["game_date"] != day or e["team"] not in team_names:
                continue
            team = team_names[e["team"]]
            w = av.STATUS_WEIGHT[e["status"]]
            if w == 0:
                continue
            pid, _level = idx.match(e["last"], e["first"], team, season)
            slot = f.absent.setdefault(team, {"new": 0.0, "all": 0.0, "new_q": 0.0, "all_q": 0.0})
            if pid is None:
                cov["unresolved"] += 1
                continue
            minutes, _ = hist.expected(pid, day, season)
            q, basis = quality.quality(pid, day, season)
            cov[f"quality_{basis}"] += 1
            slot["all"] += w * minutes
            slot["all_q"] += w * minutes * q
            if hist.played_last_team_game(pid, team, day):
                slot["new"] += w * minutes
                slot["new_q"] += w * minutes * q
        facts[day] = f
        cov["usable"] += 1
    return facts, dict(cov)


def q_features(facts: dict, g) -> dict:
    base = av.game_features(facts, g)
    if base["report_missing"]:
        return {**base, "new_absent_q_diff": 0.0, "all_absent_q_diff": 0.0}
    day = datetime.strptime(g.tipoff_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=av.timezone.utc).astimezone(av.ET).strftime("%Y-%m-%d")
    f = facts[day]
    h, a = g.home_team_id.split(":", 1)[1], g.away_team_id.split(":", 1)[1]
    zero = {"new_q": 0.0, "all_q": 0.0}
    hh, aa = f.absent.get(h, zero), f.absent.get(a, zero)
    return {**base, "new_absent_q_diff": (aa.get("new_q", 0.0) - hh.get("new_q", 0.0)) / 48,
            "all_absent_q_diff": (aa.get("all_q", 0.0) - hh.get("all_q", 0.0)) / 48}


def avail_q(elo_cfg, facts: dict, *, minutes_terms: bool):
    from .models.logistic import V2_FEATURES, V2_SCALED, LogisticV2
    extra = (av.AVAIL_FEATURES + Q_FEATURES) if minutes_terms else (Q_FEATURES + ("report_missing",))
    scaled = (("new_absent_diff", "all_absent_diff") if minutes_terms else ()) + Q_FEATURES

    class AvailQ(LogisticV2):
        name = "avail_q_v1" if minutes_terms else "avail_q_only"
        feature_names = V2_FEATURES + extra
        scaled_names = V2_SCALED + scaled

        def _features(self, g):
            x = q_features(facts, g)
            return {**super()._features(g), **{k: x[k] for k in extra if k in x}}
    return AvailQ(elo_cfg)


def _log(entry: dict) -> None:
    p = config.data_dir() / "research" / "features-v1" / "experiments.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps({"logged_at": utc_now(), **entry}, default=str) + "\n")


def evaluate(conn: sqlite3.Connection, out_dir: Optional[Path] = None, log: Callable[[str], None] = print) -> dict:
    """§19 NBA evaluation: avail_q_v1 and avail_q_only against avail_v1 and logistic_v2 on the avail_v1 games."""
    import dataclasses
    import math
    from statistics import NormalDist
    from .models.elo import Elo
    from .models.logistic import LogisticV2
    from .nhl_eval import _score
    _, elo_cfg = backtest.load_frozen_decision(conn)
    box = {s: av.load_player_box(s) for s in av.SEASONS}
    quality = QualityHistory(list(av.SEASONS))
    facts, coverage = build_quality_facts(conn, box, quality)
    rows = backtest.load_rows(conn, "validation")
    if any(r.game.season not in av.SEASONS for r in rows):
        raise av.AvailabilityError("must not read seasons outside the availability research seasons")
    rows = [dataclasses.replace(r, cutoff_at=av.evening_cutoff(r.game.tipoff_at) or r.cutoff_at) for r in rows]
    scored = {r.game.game_id for r in rows if r.game.season in av.SCORED and av.evening_cutoff(r.game.tipoff_at)}
    events = backtest.load_box_events(conn, "validation")
    models = {"elo": Elo(elo_cfg), "logistic_v2": LogisticV2(elo_cfg), "avail_v1": av.avail_v1(elo_cfg, facts),
              "avail_q_v1": avail_q(elo_cfg, facts, minutes_terms=True), "avail_q_only": avail_q(elo_cfg, facts, minutes_terms=False)}
    by = {}
    for n, m in models.items():
        try:
            out = backtest.run(m, rows, events)
            by[n] = {p["game_id"]: p for p in out if p["game_id"] in scored}
            if n.startswith("avail_q"):
                _log({"study": STUDY, "config": n, "outcome": "success", "games": len(by[n])})
        except Exception as exc:
            _log({"study": STUDY, "config": n, "outcome": "failed", "error": f"{type(exc).__name__}: {exc}"})
            raise
    ids = sorted(by["elo"], key=lambda g: (by["elo"][g]["cutoff_at"], g))

    def ll(p, y):
        p = min(1 - 1e-15, max(1e-15, p))
        return -math.log(p if y else 1 - p)

    def br(p, y):
        return (p - float(y)) ** 2
    z = NormalDist().inv_cdf(1 - 0.05 / 2 / 4)        # Bonferroni over 4 comparisons
    comps = {}
    for a in ("avail_q_v1", "avail_q_only"):
        for b in ("avail_v1", "logistic_v2"):
            for metric, fn in (("log_loss", ll), ("brier", br)):
                diffs = [fn(by[a][g]["p_home"], by[a][g]["home_won"]) - fn(by[b][g]["p_home"], by[b][g]["home_won"]) for g in ids]
                bs = backtest.block_bootstrap(diffs, [by[a][g]["cutoff_at"] for g in ids])
                sd = (bs["ci95"][1] - bs["ci95"][0]) / (2 * 1.959964)
                comps[f"{a}_minus_{b}_{metric}"] = {**bs, "bonferroni_interval": [bs["mean"] - z * sd, bs["mean"] + z * sd]}
    games = [{"game_id": g, "cutoff_at": by["elo"][g]["cutoff_at"], "home_won": by["elo"][g]["home_won"],
              **{f"p_{n}": by[n][g]["p_home"] for n in models}} for g in ids]
    for x in games:
        for n in ("avail_v1", "avail_q_v1", "avail_q_only"):
            x[f"p_avg_elo_{n}"] = (x["p_elo"] + x[f"p_{n}"]) / 2

    def acc_top(key, share=0.25):
        k = int(round(share * len(games)))
        top = sorted(games, key=lambda x: (-max(x[key], 1 - x[key]), x["game_id"]))[:k]
        return {"games": k, "accuracy": sum((x[key] >= 0.5) == bool(x["home_won"]) for x in top) / k if k else None}
    incremental = {n: {**_score(games, f"p_avg_elo_{n}"), "top25": acc_top(f"p_avg_elo_{n}")} for n in ("avail_v1", "avail_q_v1", "avail_q_only")}
    report = {"study": STUDY, "part": "NBA avail_q", "exploratory": True, "generated_at": utc_now(),
              "cutoff": "17:45 ET game day; report publication times assumed (header / Last-Modified)",
              "scored_games": len(ids), "seasons": list(av.SCORED), "coverage": coverage,
              "models": {n: {**_score([{"p_home": by[n][g]["p_home"], "home_won": by[n][g]["home_won"]} for g in ids], "p_home"),
                             "top25": acc_top(f"p_{n}"), "version": backtest.model_version(models[n])} for n in models},
              "comparisons": comps, "incremental_average_with_elo": incremental,
              "weights_last_fit": {n: dict(zip(("intercept",) + models[n].feature_names, [round(w, 4) for w in models[n].weights]))
                                   for n in ("avail_v1", "avail_q_v1", "avail_q_only")}}
    report["reading"] = {n: ("adds information beyond avail_v1" if comps[f"{n}_minus_avail_v1_log_loss"]["bonferroni_interval"][1] < 0
                             else "no evidence of information beyond avail_v1") for n in ("avail_q_v1", "avail_q_only")}
    out_dir = Path(out_dir or config.data_dir() / "reports" / f"features-v1-nba-{report['generated_at'].replace(':', '')}")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=1, default=str))
    report["output_dir"] = str(out_dir)
    log(f"wrote {out_dir / 'report.json'}")
    return report
