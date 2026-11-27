"""Per-sport forward-test status for the five sports, with the reason, the baseline path and the candidate path (read-only).

One of: `no candidate` (nothing registered to compare) | `waiting` (registered, but no eligible graded pair yet, or the prospective
start is not established / not reached) | `invalid comparison` (rows exist but none is eligible: timing, line, no information
time, or a re-implemented bar) | `failed` (the record/grade job failed or is stale, the store is missing or unreadable) |
`recording valid evidence` (eligible pairs accumulate after the prospective start).

The baseline and candidate descriptions below were read from the code named in each entry and checked against the stored forward
rows (models and roles) on 2026-11-26; they are labels for people, not inputs to any criterion. The counts come from the
evaluator's own pairing (autoresearch.forward_pair_counts); this module only classifies them. It runs nothing and writes nothing.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

FIVE = ("nba", "nhl", "ncaab", "nfl", "soccer")
STALE_AFTER = timedelta(hours=3)          # the recorder runs every 30 minutes and the grader hourly
JOB_ERRORS = ("failed", "refused", "skipped")
BAR_BASIS_NOTE = ("The bar is the published forecast as of 60 minutes before the start (the forward as-of rule: the latest published "
                  "run made by then), not necessarily the last forecast published or sent on the cards before the start.")

_PUBLISHED = {"published": True,
              "label": BAR_BASIS_NOTE + " The forecast actually published (game_forecasts, the canonical selection the web cards and Telegram show), "
                       "recorded read-only as model 'published' with its market, line, line source and information time (a/b fixed by the market definition); the "
                       "research cohort members (elo, points_normal, ...) are recorded as labelled context incumbents only",
              "code": "autoresearch._study_upcoming -> candidates_view.upcoming_published"}
BASELINE = {
    "nba": _PUBLISHED, "nhl": _PUBLISHED,
    "ncaab": {"published": False,
              "label": "a re-implementation of the sealed NCAAB decision (model ncaab-incumbent) computed at the recorder's own "
                       "clock (forecast cutoff = the record time) from current data; it is not the published forecast and is "
                       "never counted as it",
              "code": "ncaab_cards.research_forecast_upcoming (cutoff=now)"},
    "nfl": {"published": False,
            "label": "the NFL incumbent (model nfl-incumbent) recomputed at record time from the results available then; not the "
                     "stored published forecast",
            "code": "nfl_challengers.research_forecast_upcoming"},
    "soccer": {"published": "1X2 only",
               "label": "1X2, double chance and draw no bet: the stored published forecast (soccer_live_forecasts, model "
                        "elo_sealed); goals, BTTS and corners: recomputed at record time (goals_poisson_sealed, corners_nb_sealed), "
                        "not the stored published rows",
               "code": "soccer_challengers.forecast_upcoming"}}
CANDIDATE = {
    "nba": {"label": "the candidate pool (app.candidates.registry), recorded by the cycle's pool step into candidate_forecasts and "
                     "read by the forward recorder; a row counts only with an information time (inputs.information_at) equal to the "
                     "published cycle's; rows without one are labelled invalid (pre-repair)"},
    "nhl": {"label": "the candidate pool (app.candidates.registry), recorded by the cycle's pool step into candidate_forecasts and "
                     "read by the forward recorder; a row counts only with an information time (inputs.information_at) equal to the "
                     "published cycle's; rows without one are labelled invalid (pre-repair)"},
    "ncaab": {"label": "none registered for forward tracking (research keeps are not registered; challengers are run by hand and "
                       "are not recorded in the forward store)"},
    "nfl": {"label": "none registered for forward tracking; only the recomputed incumbent is recorded"},
    "soccer": {"label": "no 1X2 candidate exists (nothing to compare against the stored 1X2 forecast), and none is registered for "
                        "goals, BTTS or corners"}}


def _parse(at) -> Optional[datetime]:
    try:
        return datetime.strptime(str(at), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _breakdown(c: dict) -> str:
    names = (("unpaired_timing", "unpaired by timing"), ("unpaired_line", "unpaired by line (number or kind of line)"),
             ("missing", "missing (no candidate forecast)"),
             ("as_of_late", "forecast only after the fixed instant"),
             ("invalid_no_information_time", "invalid: no information time (pre-repair rows included)"),
             ("no_published_bar", "no published forecast to compare with"))
    return ", ".join(f"{c[k]} {label}" for k, label in names if c.get(k)) or "none"


def _job_problem(v: dict, now: datetime) -> Optional[str]:
    for kind in ("record", "grade"):
        run = v.get(f"last_{kind}")
        if run is None:
            return f"the {kind} job has never run for this sport"
        if run["outcome"] in JOB_ERRORS:
            return f"the last {kind} job {run['outcome']}: {(run.get('detail') or '').strip()[:200]}"
        at = _parse(run["at"])
        if at is not None and now - at > STALE_AFTER:
            return f"the {kind} job is stale: last run {run['at']}"
    if v.get("status") in ("failed", "candidates_failed"):
        return f"the last record run stated forward_status={v['status']}"
    return None


def _one(sport: str, summary: Optional[dict], now: datetime) -> dict:
    if summary is None:
        return {"status": "failed", "reason": "the forward store (research/forward-candidates.sqlite) is missing: nothing is recorded"}
    if "error" in summary:
        return {"status": "failed", "reason": f"the forward store is unreadable: {summary['error']}"}
    v = summary.get(sport) or {}
    if (problem := _job_problem(v, now)):
        return {"status": "failed", "reason": problem}
    rows = (v.get("candidate_forecasts") or {}).get("rows", 0)
    if not rows and v.get("status") not in ("candidates_waiting", "candidates_recording"):
        return {"status": "no candidate", "reason": v.get("label") or "no candidate is registered for this sport"}
    if not rows:
        return {"status": "waiting", "reason": "candidates are registered; none has forecast a game that is still ahead yet"}
    pairing = v.get("pairing")
    if not isinstance(pairing, dict) or "error" in pairing:
        return {"status": "failed", "reason": f"the pairing counts could not be read: {(pairing or {}).get('error', 'none')}"}
    tot = pairing["totals"]
    if BASELINE[sport]["published"] is not True:
        return {"status": "invalid comparison", "pairing": pairing,
                "reason": "candidate rows exist, but the incumbent is a re-implementation computed at the recorder's clock, not the "
                          "published forecast: no pair is valid evidence (" + BASELINE[sport]["label"] + ")"}
    start, valid_all = pairing["prospective_start"], tot["all"]["valid_pairs"]
    if start is None:
        if valid_all:
            return {"status": "waiting", "pairing": pairing,
                    "reason": f"{valid_all} valid pair(s) exist, but the prospective start of the repaired comparison is not "
                              "established: nothing is confirmatory yet"}
        return {"status": "invalid comparison", "pairing": pairing,
                "reason": f"candidate rows exist but none is an eligible pair ({_breakdown(tot['all'])}); the prospective start "
                          "is not established"}
    conf = tot["confirmatory"]
    if conf["valid_pairs"]:
        return {"status": "recording valid evidence", "pairing": pairing,
                "reason": f"{conf['valid_pairs']} valid pair(s) for games starting at or after {start} "
                          f"({conf['valid_pairs_graded']} graded); not eligible: {_breakdown({k: v for k, v in conf.items() if k != 'valid_pairs'})}"}
    if any(conf.values()):
        return {"status": "invalid comparison", "pairing": pairing,
                "reason": f"candidate rows exist for games starting at or after {start}, but none is an eligible pair "
                          f"({_breakdown(conf)})"}
    return {"status": "waiting", "pairing": pairing,
            "reason": f"no candidate forecast for a game starting at or after {start} yet; {valid_all} valid pair(s) are all for "
                      "games before it (exploratory, never counted)"}


def sport_statuses(summary: Optional[dict], now: Optional[str] = None) -> dict:
    """{sport: {"status", "reason", "baseline", "candidate", "pairing"?}} for all five sports from `forward_summary`'s result
    (None = no store). `now` (UTC, tests) defaults to the clock."""
    moment = _parse(now) if now else datetime.now(timezone.utc)
    return {s: _one(s, summary, moment) | {"baseline": BASELINE[s], "candidate": CANDIDATE[s]} for s in FIVE}


CHALLENGER_SPORTS = ("ncaab", "nfl", "soccer")
CHALLENGER_NOTE = ("PROSPECTIVE, no claim: challengers are recorded by the research-only learning job and compared with the published "
                   "forecast at read time (python -m app.learning_eval --sport <sport>: paired, unpaired with a reason, scored). "
                   "Registration or recording is not promotion; this status is never 'passed'.")


def challenger_statuses(path=None) -> dict:
    """{sport: {"status": waiting | recording | failed, "rows", "games", "last_recorded_at", "note"}} for the sports whose challengers
    are recorded (research/challengers.sqlite, read-only). `waiting`: no row yet (store missing included); `recording`: rows exist;
    `failed`: the store is unreadable. Nothing here pairs or scores, and nothing is ever `passed`."""
    from . import learning_challengers as lc
    out = {s: {"status": "waiting", "rows": 0, "games": 0, "last_recorded_at": None, "note": CHALLENGER_NOTE} for s in CHALLENGER_SPORTS}
    try:
        conn = lc.open_ro(path or lc.store_path())
        if conn is None:
            return out
        try:
            for s in CHALLENGER_SPORTS:
                n, games, last = conn.execute("SELECT count(*), count(DISTINCT game_id), max(recorded_at) FROM challenger_forecasts "
                                              "WHERE policy_id LIKE ? ESCAPE '\\'", (f"{s}-%",)).fetchone()
                if n:
                    out[s] |= {"status": "recording", "rows": n, "games": games, "last_recorded_at": last}
        finally:
            conn.close()
    except Exception as exc:               # a status page never raises
        return {s: v | {"status": "failed", "reason": f"the challenger store is unreadable: {type(exc).__name__}: {exc}"} for s, v in out.items()}
    return out
