"""Shadow-season report: frozen Elo versus the sealed challenger on live forecasts (docs/EVALUATION_PLAN.md).

Two modes:
- interim (default): operational only. Coverage, missing forecasts, job health and descriptive scores. The promotion
  rule is NOT evaluated and no interval against Elo is computed, so an interim look cannot drive a decision.
- final: refused unless the season is verified complete (season_completion: every team's full regular season, the
  whole play-in slate final, playoffs scheduled; never inferred from missing rows). Applies the sealed promotion
  rule once, logs the outcome, and returns the logged outcome on any later call.

Neither mode changes the frozen Elo decision, the sealed challenger or any forecast. Promotion itself is a separate,
reviewed step.
"""
import json
import sqlite3
from pathlib import Path
from typing import Optional

from . import backtest, config, live, metrics, shadow
from .db import utc_now
from .models.baselines import HomeRate
from .models.elo import Elo

DECISION_TYPES = ("regular", "play_in")
# Season format assumed when verifying completion. If the league's format differs, the final report refuses to run
# until this is amended (dated, in docs/EVALUATION_PLAN.md); it never guesses.
EXPECTED_TEAMS = 30
REGULAR_SEASON_GAMES_PER_TEAM = 82   # the NBA Cup final is stored as regular season, so two teams may have 83
PLAY_IN_GAMES = 6                    # two per conference, then one more per conference


class ShadowReportError(ValueError):
    pass


def season_of(rule: dict) -> int:
    """'2026-27' -> 2027 (season end year, as stored in games.season)."""
    return int(rule["season"][:2] + rule["season"][-2:])


def promotion_log_path() -> Path:
    return config.data_dir() / "eval" / "shadow_promotion.jsonl"


def apply_promotion_rule(n: int, ci_upper: float, candidate_log_loss: float, elo_log_loss: float,
                         minimum_games: int) -> dict:
    """The sealed rule: below the minimum sample → inconclusive; otherwise the candidate qualifies only if the
    interval's upper bound is below 0 and its log loss is not higher than Elo's."""
    if n < minimum_games:
        return {"outcome": "inconclusive", "elo_stays": True,
                "reason": f"{n} games in the decision set, fewer than the required {minimum_games}"}
    better, not_worse = ci_upper < 0, candidate_log_loss <= elo_log_loss
    if better and not_worse:
        return {"outcome": "candidate_qualifies", "elo_stays": False,
                "reason": "interval upper bound below 0 and log loss not higher than Elo's"}
    return {"outcome": "elo_stays", "elo_stays": True,
            "reason": "; ".join(r for r, bad in (("interval upper bound is not below 0", not better),
                                                 ("candidate log loss is higher than Elo's", not not_worse)) if bad)}


def season_completion(conn: sqlite3.Connection, season: int) -> dict:
    """Positive evidence that the regular season and the play-in are over. Completion is never inferred from the
    absence of pending rows: every check below must find what a finished season contains, otherwise `complete` is
    False and the final report is refused."""
    per_team: dict[str, int] = {}
    for r in conn.execute(
            "SELECT team_id, COUNT(*) AS n FROM (SELECT home_team_id AS team_id FROM games WHERE season = ? AND "
            "season_type = 'regular' AND status = 'final' UNION ALL SELECT away_team_id FROM games WHERE season = ? "
            "AND season_type = 'regular' AND status = 'final') GROUP BY team_id", (season, season)):
        per_team[r["team_id"]] = r["n"]
    short = sorted(t for t, n in per_team.items() if n < REGULAR_SEASON_GAMES_PER_TEAM)
    count = lambda where, *args: conn.execute(  # noqa: E731
        f"SELECT COUNT(*) FROM games WHERE season = ? AND {where}", (season, *args)).fetchone()[0]
    play_in_final = count("season_type = 'play_in' AND status = 'final'")
    last_play_in = conn.execute("SELECT MAX(tipoff_at) FROM games WHERE season = ? AND season_type = 'play_in' "
                                "AND status = 'final'", (season,)).fetchone()[0]
    checks = {
        "all_teams_have_a_full_regular_season": len(per_team) == EXPECTED_TEAMS and not short,
        "no_regular_or_play_in_game_pending": count("season_type IN ('regular', 'play_in') "
                                                    "AND status IN ('scheduled', 'in_progress')") == 0,
        "no_unresolved_play_in_postponement": count("season_type = 'play_in' "
                                                    "AND status NOT IN ('final', 'scheduled', 'in_progress')") == 0,
        "full_play_in_slate_is_final": play_in_final == PLAY_IN_GAMES,
        "playoffs_scheduled_after_the_play_in": bool(last_play_in) and count(
            "season_type = 'postseason' AND tipoff_at > ?", last_play_in) > 0,
    }
    return {"complete": all(checks.values()), "checks": checks,
            "expected": {"teams": EXPECTED_TEAMS, "regular_season_games_per_team": REGULAR_SEASON_GAMES_PER_TEAM,
                         "play_in_games": PLAY_IN_GAMES},
            "found": {"teams_with_regular_season_finals": len(per_team),
                      "teams_short_of_a_full_season": len(short) + max(0, EXPECTED_TEAMS - len(per_team)),
                      "play_in_games_final": play_in_final,
                      "postponed_or_canceled_regular_rows": count("season_type = 'regular' AND status IN "
                                                                  "('postponed', 'canceled')")}}


def _latest(conn: sqlite3.Connection, model_version: str, season: int) -> dict[str, sqlite3.Row]:
    """Latest pre-cutoff live forecast per game for one model, for the season's decision-set game types."""
    rows = conn.execute(
        f"SELECT l.*, g.status, g.season_type, g.tipoff_at, g.home_score, g.away_score FROM ({live.LATEST_LIVE_SQL}) l "
        f"JOIN games g ON g.game_id = l.game_id WHERE g.season = ? AND g.season_type IN (?, ?)",
        (model_version, season, *DECISION_TYPES)).fetchall()
    return {r["game_id"]: r for r in rows}


def _scores(pairs: list[tuple[float, bool]]) -> dict:
    probs, ys = [p for p, _ in pairs], [y for _, y in pairs]
    return {"n": len(pairs), "accuracy": metrics.accuracy(probs, ys), "brier": metrics.brier(probs, ys),
            "log_loss": metrics.log_loss(probs, ys)}


def _job_health(conn: sqlite3.Connection, since: str) -> dict:
    out = {}
    for job in ("refresh", "predict", "boxscores", "shadow"):
        rows = conn.execute("SELECT status, COUNT(*) AS n FROM job_runs WHERE job = ? AND started_at >= ? GROUP BY status",
                            (job, since)).fetchall()
        out[job] = {r["status"]: r["n"] for r in rows}
    stale = 0
    for r in conn.execute("SELECT detail FROM job_runs WHERE job = 'shadow' AND started_at >= ?", (since,)):
        stale += len(json.loads(r["detail"]).get("skipped_stale_inputs") or [])
    out["shadow_stale_skips"] = stale
    out["note"] = ("'predict' is the Elo forecast job. A failed 'shadow' run with stale skips means some shadow forecasts "
                   "were withheld that night; it does not mean the Elo job failed.")
    return out


def report(conn: sqlite3.Connection, *, final: bool = False, log_path: Optional[Path] = None) -> dict:
    decision, elo_cfg = shadow.load_shadow_decision(conn)
    rule = decision["promotion_rule"]
    season = season_of(rule)
    elo_mv, cand_mv = backtest.model_version(Elo(elo_cfg)), decision["model_version"]
    log_path = Path(log_path or promotion_log_path())

    if final and log_path.is_file():
        for line in log_path.read_text().splitlines():
            entry = json.loads(line)
            if entry.get("model_version") == cand_mv:
                return {**entry, "previously_applied": True}

    games = conn.execute("SELECT game_id, status, season_type, tipoff_at, home_score, away_score FROM games "
                         "WHERE season = ? AND season_type IN (?, ?)", (season, *DECISION_TYPES)).fetchall()
    finals = {g["game_id"]: g for g in games if g["status"] == "final"}
    unfinished = [g["game_id"] for g in games if g["status"] in ("scheduled", "in_progress")]
    play_in_final = sum(g["season_type"] == "play_in" and g["status"] == "final" for g in games)
    elo, cand = _latest(conn, elo_mv, season), _latest(conn, cand_mv, season)
    both = sorted(g for g in finals if g in elo and g in cand)
    coverage = {
        "final_games": len(finals), "with_elo_forecast": sum(g in elo for g in finals),
        "with_candidate_forecast": sum(g in cand for g in finals), "decision_set": len(both),
        "excluded_missing_candidate_only": sum(g in elo and g not in cand for g in finals),
        "excluded_missing_elo_only": sum(g not in elo and g in cand for g in finals),
        "excluded_missing_both": sum(g not in elo and g not in cand for g in finals),
        "unfinished_games": len(unfinished), "play_in_games_final": play_in_final,
    }
    completion = season_completion(conn, season)
    out = {"generated_at": utc_now(), "mode": "final" if final else "interim", "season": rule["season"],
           "elo_model_version": elo_mv, "candidate": decision["candidate"], "model_version": cand_mv,
           "shadow_decision_sha256": decision["sha256"], "coverage": coverage, "season_completion": completion}

    y = {g: finals[g]["home_score"] > finals[g]["away_score"] for g in both}
    preds = {name: [{"game_id": g, "p_home": src[g]["home_win_prob"], "home_won": y[g], "cutoff_at": src[g]["cutoff_at"]}
                    for g in both] for name, src in (("elo", elo), ("candidate", cand))}
    # Home-rate is not recorded live; it is recomputed with the backtest walk (results available at each cutoff only).
    home_rate = {p["game_id"]: p["p_home"] for p in backtest.run(HomeRate(), backtest.load_rows(conn, "live"))
                 if p["game_id"] in y} if both else {}
    out["scores"] = {
        "constant_0.5": _scores([(0.5, y[g]) for g in both]),
        "home_rate": {**_scores([(home_rate[g], y[g]) for g in both]), "note": "recomputed walk-forward, not recorded live"},
        "elo": _scores([(p["p_home"], p["home_won"]) for p in preds["elo"]]),
        "candidate": _scores([(p["p_home"], p["home_won"]) for p in preds["candidate"]]),
    }
    # Market: latest 'current' quote retrieved no later than the earlier of the two forecasts' generation times.
    matched = []
    for g in both:
        seen_by = min(elo[g]["generated_at"], cand[g]["generated_at"])
        q = conn.execute("SELECT home_prob_devig FROM odds_quotes WHERE game_id = ? AND price_label = 'current' "
                         "AND retrieved_at <= ? ORDER BY retrieved_at DESC, quote_id DESC LIMIT 1", (g, seen_by)).fetchone()
        if q:
            matched.append((g, q[0]))
    out["market"] = {
        "matched_games": len(matched), "coverage": len(matched) / len(both) if both else 0.0,
        "market": _scores([(p, y[g]) for g, p in matched]),
        "elo_on_matched": _scores([(elo[g]["home_win_prob"], y[g]) for g, _ in matched]),
        "candidate_on_matched": _scores([(cand[g]["home_win_prob"], y[g]) for g, _ in matched]),
        "note": "DraftKings via ESPN, proportional de-vig; reported only, never part of the promotion rule",
    }
    first_cutoff = min((elo[g]["cutoff_at"] for g in both), default=out["generated_at"])
    out["jobs"] = _job_health(conn, min(first_cutoff, decision["sealed_at"]))

    if not final:
        out["promotion"] = {"evaluated": False,
                            "reason": "interim report: operational only; the rule is applied once, after the last play-in game"}
        return out
    if not completion["complete"]:
        failed = [name for name, ok in completion["checks"].items() if not ok]
        raise ShadowReportError("The promotion rule is applied only after the last play-in game, and the season is not "
                                f"verified complete. Failed checks: {', '.join(failed)}. Found: {completion['found']}")
    boot = backtest.paired_bootstrap(preds["candidate"], preds["elo"]) if both else None
    verdict = apply_promotion_rule(len(both), boot["ci95"][1] if boot else float("inf"),
                                   out["scores"]["candidate"]["log_loss"] or float("inf"),
                                   out["scores"]["elo"]["log_loss"] or float("inf"), rule["minimum_games"])
    out["promotion"] = {"evaluated": True, "rule": rule, **verdict,
                        "brier_diff_candidate_minus_elo": boot,
                        "note": "This report changes nothing. Any promotion is a separate, reviewed step."}
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as f:
        f.write(json.dumps(out, default=str) + "\n")
    return {**out, "previously_applied": False}
