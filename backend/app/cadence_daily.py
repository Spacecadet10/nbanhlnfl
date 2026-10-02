"""Daily-refit comparison `daily-v1` (docs/MODEL_SELECTION.md §17): the §9 members refitted at the first forecast of each
Eastern calendar date on which newly settled results have arrived since the previous fit, against the season-start and
weekly cadences. A separate module so the hashed live cadence code (retrain_cadence.py) is unchanged. Exploratory."""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from . import backtest, config
from . import retrain_cadence as rc
from .db import utc_now

STUDY = "daily-v1"


class Daily(rc.Cadenced):
    """As rc.Cadenced, with a daily period and no refit unless new results were delivered since the last fit."""

    def __init__(self, member):
        super().__init__(member, "weekly")      # validated base; the period is overridden below
        self.cadence, self.name = "daily", f"{member.name}@daily"
        self.fitted_on: Optional[int] = None
        self.skipped_no_new_results = 0

    def _maybe_refit(self, g) -> None:
        if self.first_season is None:
            self.first_season = g.season
        if getattr(self.member, "season", None) is not None and g.season > self.member.season:
            self._log("season_start", g)
            self.fitted_on = len(self.delivered)
        if g.season == self.first_season:
            return
        d = datetime.strptime(g.tipoff_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(backtest.EASTERN).date()
        key = (g.season, d.isoformat())
        if key == self.key:
            return
        self.key = key
        if getattr(self.member, "season", None) != g.season:
            return
        if self.fitted_on == len(self.delivered):
            self.skipped_no_new_results += 1
            return
        before = {a: getattr(self.member, a, None) for a in rc._FIRST_ATTRS}
        self.member._refit(g.season + 1)
        for a, v in before.items():
            if v is None and getattr(self.member, a, None) is not None:
                setattr(self.member, a, g.season)
        self.fitted_on = len(self.delivered)
        self.refits.append({"cutoff_at": backtest.cutoff_for(g.tipoff_at), "season": g.season,
                            "training_games": len(self.member.examples)})
        self._log("daily", g)


def run(conn, sport: str, out_dir: Optional[Path] = None, log: Callable[[str], None] = print) -> dict:
    started = utc_now()
    rows, events, factories, primary, scorer, evaluated, version = (rc.nba_setup if sport == "nba" else rc.nhl_setup)(conn)
    report = {"kind": "daily-cadence", "study": STUDY, "sport": sport, "started_at": started, "dataset_version": version,
              "evaluated_seasons": list(evaluated), "exploratory": True, "members": {}}
    if events is not None:
        report["box_version"] = backtest.box_version(events)
    for name, (factory, _) in factories.items():
        walks, info = {}, {}
        for label, make in (("season", lambda: rc.Cadenced(factory(), "season")),
                            ("weekly", lambda: rc.Cadenced(factory(), "weekly")), ("daily", lambda: Daily(factory()))):
            t0 = datetime.now()
            w = make()
            walks[label] = rc._walk(w, rows, events, scorer)
            info[label] = {"refits_within_season": len(w.refits), "seconds": round((datetime.now() - t0).total_seconds(), 1),
                           "skipped_no_new_results": getattr(w, "skipped_no_new_results", None)}
            log(f"{sport} {name}@{label}: {len(walks[label])} games, {len(w.refits)} refits, {info[label]['seconds']} s")
        vs_season = rc.compare({"season": walks["season"], "daily": walks["daily"]}, primary[name], evaluated)
        vs_weekly = rc.compare({"season": walks["weekly"], "daily": walks["daily"]}, primary[name], evaluated)
        report["members"][name] = {"walks": info, "primary_metric": primary[name], "games": vs_season["games"],
                                   "levels": {**vs_season["levels"], "weekly": vs_weekly["levels"]["season"]},
                                   "daily_minus_season": vs_season["daily"], "daily_minus_weekly": vs_weekly["daily"]}
    report["finished_at"] = utc_now()
    out_dir = out_dir or config.data_dir() / "reports" / f"daily-cadence-{sport}-{started.replace(':', '')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, default=str))
    report["output_dir"] = str(out_dir)
    log(f"wrote {out_dir / 'report.json'}")
    return report
