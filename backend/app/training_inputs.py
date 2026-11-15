"""Training inputs: collect, then check, before a scheduled refit (docs/ADAPTIVE_SYSTEM.md §14).

The nightly order (scripts/launchd/com.nba-predictor.adaptive-train.plist): `training-inputs` collects what the members read
and checks it; `adaptive-train` (weekly, then `--cadence daily`) refuses unless the check passes; the two-hour cycle then
forecasts with the fits in force; the nightly jobs settle; the status panel reports.

Why the check gates training. Some inputs retire a fitted member when they arrive after its fit:
- NHL player boxes: the NHL injury member's guard digests the whole box folder (nhl_player_ratings input_mismatch), so a box
  added after a fit retires the member until the next fit. Boxes are therefore collected here, last, only once every other
  gate has passed (a run that defers training adds none), right before training, and nowhere in the two-hour cycle. They
  never gate training: once one has arrived the member is retired until the next fit anyway, and training is how it comes
  back (without a missing box only that team's games lack it). A box collection that fails marks the run failed (visible)
  without stopping training. With NHL_PLAYER_BOX_REFRESH unset no box is collected and the member stays missing (reported).
- NHL shots: imported within 24 h of the result they count as observed (available at retrieval); later they are dated back
  (historical_backfill) and retire the xG member until the next fit. Recent finals are imported first (newest first), so an
  old backlog cannot starve them.
- Results: training on a store whose results refresh is stale would freeze an incomplete state.
Only recent finals (results available within RECENT) gate shots, and a game still missing after GIVE_UP collection runs stops
gating (reported as given up): a game whose source never publishes cannot stop every later refit. Older gaps are reported.
Manual `training-inputs` runs count toward GIVE_UP too. A recent game whose shot import failed may be fetched once more by
the backlog step in the same run (harmless: only complete imports are read).
NBA inputs (team boxes, the season player-box file, injury reports) do not retire fits when they arrive late (player_ratings'
guard covers only consumed rows): reported, not gating.
A run is logged as job 'training_inputs' in job_runs ('failed' when not ready); a deferred `adaptive-train` adds a failed row
too, so either is visible in the store and on the System status panel."""
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import config, jobs

REFRESH_HOURS = 3                 # the results refresh must be this recent when training starts
RECENT = timedelta(days=3)        # finals whose inputs gate training (results available within this window)
GIVE_UP = 3                       # collection runs after which a still-missing game stops gating
FMT = "%Y-%m-%dT%H:%M:%SZ"


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, FMT).replace(tzinfo=timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(FMT)


def _now() -> str:
    return _fmt(datetime.now(timezone.utc))


def _nhl_season(conn) -> Optional[int]:
    row = conn.execute("SELECT MAX(season) FROM games WHERE status='final' AND season_type IN ('regular','postseason')").fetchone()
    return row[0] if row else None


def _failures_path(sport: str):
    return config.data_dir() / "research" / "training_inputs" / f"missing-runs-{sport}.json"


def _failures(sport: str) -> dict:
    """{input: {game_id: collection runs after which it was still missing}}."""
    try:
        return json.loads(_failures_path(sport).read_text())
    except (OSError, ValueError):
        return {}


def _gate(missing: list, recent: set, failures: dict) -> tuple[list, list, int]:
    """(gating, given up, older): recent missing games gate until they were missing after GIVE_UP collection runs."""
    gating = [g for g in missing if g in recent and failures.get(g, 0) < GIVE_UP]
    given_up = [g for g in missing if g in recent and failures.get(g, 0) >= GIVE_UP]
    return gating, given_up, sum(g not in recent for g in missing)


def _recent(conn, now: str) -> set:
    since = _fmt(_parse(now) - RECENT)
    return {r[0] for r in conn.execute("SELECT game_id FROM games WHERE status='final' AND result_available_at > ? "
                                       "AND result_available_at <= ?", (since, now))}


def _missing_shots(conn, now: str) -> list:
    from . import nhl_shots
    season = _nhl_season(conn)
    return [r["game_id"] for r in nhl_shots.pending(conn, [season] if season else None) if r["result_available_at"] <= now]


def _missing_boxes(conn, now: str) -> list:
    from .sources import nhl_injuries, nhl_pbp
    season = _nhl_season(conn)
    if season is None:
        return []
    folder = nhl_injuries.root() / "boxes"
    return [r[0] for r in conn.execute("SELECT game_id FROM games WHERE status='final' AND season=? AND season_type IN "
                                       "('regular','postseason') AND result_available_at <= ? ORDER BY tipoff_at", (season, now))
            if not (folder / f"{nhl_pbp.api_id(r[0])}.json").exists()]


def check(conn, now: Optional[str] = None) -> dict:
    """Read-only. {'ready', 'blocking': {input: detail}, 'report': {input: detail}}."""
    now = now or _now()
    blocking, report = {}, {}
    last = conn.execute("SELECT MAX(finished_at) FROM job_runs WHERE job='refresh' AND status='success' AND finished_at <= ?",
                        (now,)).fetchone()[0]
    report["results"] = {"last_refresh": last}
    if last is None or _parse(last) < _parse(now) - timedelta(hours=REFRESH_HOURS):
        blocking["results"] = f"no successful results refresh in the last {REFRESH_HOURS} h (last: {last})"
    sport = jobs.sport(conn)
    recent = _recent(conn, now)
    failures = _failures(sport)
    if sport == "nhl":
        gate, gave_up, older = _gate(_missing_shots(conn, now), recent, failures.get("nhl_shots", {}))
        report["nhl_shots"] = {"recent_missing": len(gate), "given_up": gave_up, "older_missing": older}
        if gate:
            blocking["nhl_shots"] = {"missing": len(gate), "first": gate[:5]}
        boxes = _missing_boxes(conn, now)
        report["nhl_player_boxes"] = {"recent_missing": sum(g in recent for g in boxes), "first": [g for g in boxes if g in recent][:5],
                                      "older_missing": sum(g not in recent for g in boxes), "season": _nhl_season(conn),
                                      "note": "reported, never gating (collected last, right before training)"}
        if not config.nhl_player_box_refresh_enabled():
            report["nhl_player_boxes"]["note"] = ("not collected: NHL_PLAYER_BOX_REFRESH is not set to 1, so the NHL injury "
                                                  "member stays missing (stale inputs)")
    else:
        since = _fmt(_parse(now) - RECENT)
        missing_box = [r[0] for r in conn.execute(
            "SELECT game_id FROM games g WHERE status='final' AND result_available_at > ? AND result_available_at <= ? AND NOT "
            "EXISTS (SELECT 1 FROM team_game_stats s WHERE s.game_id = g.game_id)", (since, now))]
        report["nba_team_boxes"] = {"recent_missing": len(missing_box), "first": missing_box[:5]}
        from . import availability
        files = sorted((availability.research_dir() / "player_box").glob("player_box_*.csv"))
        report["nba_player_box"] = {"newest_file": files[-1].name if files else None}
    return {"ready": not blocking, "as_of": now, "blocking": blocking, "report": report}


def collect_recent_shots(conn, now: Optional[str] = None) -> dict:
    """NHL: shots of recent finals first, newest first, each isolated (then jobs.nhl_pbp works off any older backlog)."""
    from . import nhl_shots
    from .db import utc_now
    from .sources import nhl_pbp
    now = now or _now()
    since = _fmt(_parse(now) - RECENT)
    season = _nhl_season(conn)
    todo = sorted((g for g in nhl_shots.pending(conn, [season] if season else None)
                   if since < g["result_available_at"] <= now), key=lambda g: g["result_available_at"], reverse=True)
    out = {"pending": len(todo), "imported": 0, "incomplete": 0, "errors": {}}
    for g in todo:
        try:
            r = nhl_shots.import_game(conn, g, nhl_pbp.fetch(g["game_id"]), utc_now())
            out["imported"] += 1
            out["incomplete"] += not r["complete"]
        except Exception as exc:        # reported; the game stays pending for the next run
            out["errors"][g["game_id"]] = f"{type(exc).__name__}: {exc}"
    out["status"] = "failed" if out["errors"] and not out["imported"] else "success" if out["imported"] else "skipped"
    return out


def _step(steps: dict, name: str, fn) -> None:
    try:
        steps[name] = fn()
    except Exception as exc:
        steps[name] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}


def collect(conn, now: Optional[str] = None) -> dict:
    """The collectors the members read except the NHL boxes (collect_boxes), each isolated: a failure is reported, the
    others still run."""
    steps: dict = {}
    _step(steps, "refresh", lambda: jobs.refresh(conn))
    if jobs.sport(conn) == "nhl":
        _step(steps, "nhl_recent_shots", lambda: collect_recent_shots(conn, now))
        _step(steps, "nhl_shots", lambda: jobs.nhl_pbp(conn))
    else:
        _step(steps, "nba_team_boxes", lambda: jobs.boxscores(conn))
        _step(steps, "nba_player_box", lambda: jobs.player_box_refresh(conn))
    return steps


QUARANTINE_DAYS = 7               # box files this recent are re-read for completeness before each collection
IN_FLIGHT_SECONDS = 600           # files modified this recently may still be open by another collector: left alone


def _quarantine_unreadable_boxes(folder) -> list:
    """collect_bulk_boxes writes each box file and each raw bulk response in place (not atomically; that module is part of
    the NHL definition and is not edited): a run killed mid-write leaves a truncated file that counts as present and makes
    the member's loader raise (JSON that does not parse; a bulk-*.raw whose bytes no longer match the sha256 in its name).
    Such files modified in the last QUARANTINE_DAYS are renamed aside (`.corrupt-<time>`, kept), so they are fetched again.
    Files modified in the last IN_FLIGHT_SECONDS are left alone (`nhl-player-inputs` may be writing them). Game files that
    name a quarantined raw are set aside too (they would count as collected while their raw is gone). Only the bulk
    format is checked: the per-game raw format is written by `collect_boxes(limit=...)`, which this job never calls."""
    import hashlib
    import time
    out, now = [], time.time()
    stamp = _now().replace(":", "")
    for p in sorted([*folder.glob("[0-9]*.json"), *folder.glob("bulk-*.raw")]):
        age = now - p.stat().st_mtime
        if age > QUARANTINE_DAYS * 86400 or age < IN_FLIGHT_SECONDS:
            continue
        body = p.read_bytes()
        if p.suffix == ".raw":
            ok = hashlib.sha256(body).hexdigest() == p.stem.rsplit("-", 1)[-1]
        else:
            try:
                json.loads(body)
                ok = True
            except ValueError:
                ok = False
        if not ok:
            p.rename(p.with_name(f"{p.name}.corrupt-{stamp}"))
            out.append(p.name)
    # A game file naming a quarantined raw would count as collected while its raw is gone: aside too, so it is refetched.
    gone = {n for n in out if n.endswith(".raw")}
    if gone:
        for p in sorted(folder.glob("[0-9]*.json")):
            try:
                refs = set(json.loads(p.read_bytes()).get("raw_files") or ())
            except (OSError, ValueError):
                continue
            if refs & gone:
                p.rename(p.with_name(f"{p.name}.corrupt-{stamp}"))
                out.append(p.name)
    return out


def collect_boxes(conn) -> dict:
    if not config.nhl_player_box_refresh_enabled():
        return {"status": "disabled", "reason": "NHL_PLAYER_BOX_REFRESH is not set to 1; no request was made"}
    from . import nhl_player_ratings
    from .sources import nhl_injuries
    season = _nhl_season(conn)
    if season is None:
        return {"status": "skipped", "reason": "no NHL final in the store"}
    folder = nhl_injuries.root() / "boxes"
    quarantined = _quarantine_unreadable_boxes(folder) if folder.is_dir() else []
    out = nhl_player_ratings.collect_bulk_boxes(conn, season, season, delay=1., log=lambda _m: None)
    return {"status": "failed" if out["failed"] else "success", **out, "errors": out["errors"][:10], "quarantined": quarantined}


def _count_missing(conn, now: str) -> None:
    """After a collection run (shots were requested): one more run for every recent game still missing shots (GIVE_UP)."""
    sport = jobs.sport(conn)
    if sport != "nhl":
        return
    recent = _recent(conn, now)
    failures = _failures(sport)
    counts = failures.get("nhl_shots", {})
    failures = {"nhl_shots": {g: counts.get(g, 0) + 1 for g in _missing_shots(conn, now) if g in recent}}   # others drop out
    path = _failures_path(sport)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(failures, indent=1, sort_keys=True))
    tmp.replace(path)


def record_deferral(conn, by: str, c: dict) -> int:
    """A training run deferred by the check leaves a failed 'training_inputs' row (visible like a failed collection)."""
    run_id = jobs._start(conn, "training_inputs")
    return jobs._finish(conn, run_id, "failed", 0, {"deferred_by": by, "check": json.loads(json.dumps(c, default=str))})["run_id"]


def run(conn, now: Optional[str] = None, collect: bool = True) -> dict:
    """Collect (unless `collect` is False), pre-check, collect NHL boxes only if the pre-check passes, then check; logged as
    job 'training_inputs' ('failed' when not ready)."""
    given, now = now, now or _now()
    run_id = jobs._start(conn, "training_inputs")
    # Without an explicit time, recent shots and the checks read the clock after the run's own refresh, which finishes
    # (and settles finals) after the run started.
    steps = globals()["collect"](conn, given) if collect else {}
    if given is None:
        now = _now()
    try:
        pre = check(conn, now)
        if collect and jobs.sport(conn) == "nhl":
            if pre["ready"]:
                _step(steps, "nhl_player_boxes", lambda: collect_boxes(conn))
            else:
                steps["nhl_player_boxes"] = {"status": "deferred", "reason": "other training inputs are not ready, so no "
                                             "box is added (a box added after a fit retires the NHL injury member)"}
        if collect:
            _count_missing(conn, now)
        c = check(conn, now)
    except Exception as exc:
        c = {"ready": False, "blocking": {"check": f"{type(exc).__name__}: {exc}"}, "report": {}}
    failed = sorted(k for k, v in steps.items() if isinstance(v, dict) and v.get("status") == "failed")
    detail = {"steps": {k: (v.get("status") if isinstance(v, dict) else v) for k, v in steps.items()}, "failed_steps": failed,
              "check": c}
    # 'failed' when training must wait OR a collector failed (visible on the panel); `ready` alone decides training.
    status = "success" if c["ready"] and not failed else "failed"
    out = jobs._finish(conn, run_id, status, 1, json.loads(json.dumps(detail, default=str)))
    return {**out, "steps": steps, "check": c}
