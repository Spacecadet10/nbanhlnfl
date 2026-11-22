"""Read-only status of the sports autoresearch (app/autoresearch.py, app/research_agent.py) for the API.

Reads the experiment directory named by NBA_AUTORESEARCH_DIR (never a store, never the snapshot): per sport the current
protocol (bar, cohort), incumbents, recent trials with their per-market decisions and failures, holdout accesses, the agent
runs and whether research is stopped or running. It runs nothing and changes nothing. Everything it shows is research:
nothing here is approved, promoted or used for a forecast.
"""
import json
import os
from pathlib import Path
from typing import Optional

NOTE = ("Research only. Trials search the validation seasons of a read-only data snapshot; a 'keep' means a candidate beat "
        "the incumbent there (exploratory, repeated search), not that it is approved or used. Forecasts never come from "
        "here; promotion stays a separate, manual decision on the forward record.")
FAILED = ("crash", "timeout", "invalid", "interrupted")


def experiment_dir() -> Optional[Path]:
    v = os.environ.get("NBA_AUTORESEARCH_DIR")
    return Path(v) if v else None


def _jsonl(p: Path) -> list:
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:              # a torn last line while a writer appends: skipped, said so in the count
            out.append({"event": "unreadable"})
    return out


def _read(p: Path) -> Optional[dict]:
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def proc_token(pid) -> Optional[str]:
    """Start time of a live process: (pid, token) identifies it even after the pid is reused."""
    import subprocess
    try:
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(int(pid))], capture_output=True, text=True).stdout.strip()
    except (TypeError, ValueError):
        return None
    return out or None


def _alive(pid, token) -> bool:
    return token is not None and proc_token(pid) == token


def _trial(r: dict) -> dict:
    return {"trial_id": r.get("trial_id"), "commit": (r.get("commit") or "")[:7], "status": r.get("status"),
            "cached_from": r.get("cached_from"), "description": r.get("description"), "reason": r.get("reason"),
            "finished_at": r.get("finished_at"), "agent_run": r.get("agent_run"),
            "resources": r.get("resources"),
            "markets": {m: {"decision": s.get("decision"), "incumbent": s.get("incumbent"),
                            "forecast": s.get("forecast"), "cohort": s.get("cohort"),
                            "delta": (s.get("vs_incumbent") or {}).get("mean"),
                            "ci95": (s.get("vs_incumbent") or {}).get("ci95")}
                        for m, s in (r.get("markets") or {}).items()}}


def sport_status(root: Path, meta: dict, recent: int) -> dict:
    recs = _jsonl(root / "trials.jsonl")
    done = [r for r in recs if r.get("event") == "finished"]
    started = {r["trial_id"] for r in recs if r.get("event") == "started"}
    by_status = {}
    for r in done:
        k = r.get("status", "?") + (" (cached)" if r.get("cached_from") else "")
        by_status[k] = by_status.get(k, 0) + 1
    incumbents = {m: {"source": "existing member", "label": s["member"], "log_loss": s.get("log_loss")}
                  for m, s in (meta.get("start_incumbent") or {}).items()}
    for r in done:                                # the last kept trial per market (same replay as the evaluator)
        if r.get("status") == "keep" and not r.get("cached_from"):
            for m, s in (r.get("markets") or {}).items():
                if s.get("decision") == "keep":
                    incumbents[m] = {"source": "kept trial", "label": f"trial {r['trial_id']} ({r['commit'][:7]})",
                                     "delta_vs_previous": (s.get("vs_incumbent") or {}).get("mean")}
    holdout = _jsonl(root / "holdout_access.jsonl")
    return {"protocol_id": meta.get("protocol_id"), "registered_at": meta.get("registered_at"),
            "scored_seasons": (meta.get("protocol") or {}).get("scored_seasons", {}).get(meta.get("sport")),
            "holdout_seasons": (meta.get("protocol") or {}).get("holdout_seasons", {}).get(meta.get("sport")),
            "cohort": meta.get("cohort"), "dataset_version": meta.get("dataset_version"),
            "bar": {m: {"member": s["member"], "log_loss": s.get("log_loss")} for m, s in (meta.get("start_incumbent") or {}).items()},
            "incumbents": incumbents, "trials": len(done), "by_status": by_status,
            "failures": sum(r.get("status") in FAILED for r in done),
            "running": sorted(started - {r.get("trial_id") for r in done}),
            "unreadable_lines": sum(r.get("event") == "unreadable" for r in recs),
            "recent": [_trial(r) for r in done[-recent:]][::-1],
            "holdout": [{"event": h.get("event"), "at": h.get("at")} for h in holdout]}


def status(recent: int = 12) -> dict:
    root = experiment_dir()
    if root is None:
        return {"configured": False, "note": NOTE,
                "reason": "No research directory configured (NBA_AUTORESEARCH_DIR is not set for this server)."}
    if not root.is_dir():
        return {"configured": False, "note": NOTE, "reason": f"Research directory {root} does not exist."}
    sports = {}
    for meta_path in root.glob("*/*/protocol.json"):
        meta = _read(meta_path)
        if not meta:
            continue
        sport = meta.get("sport") or meta_path.parent.name
        if sport not in sports or meta.get("registered_at", "") > sports[sport][1].get("registered_at", ""):
            sports[sport] = (meta_path.parent, meta)
    agent = root / "agent"
    events = _jsonl(agent / "runs.jsonl")
    runs = {}
    for e in events:
        if "run_id" in e:
            r = runs.setdefault(e["run_id"], {"run_id": e["run_id"]})
            if e.get("event") == "correction":     # an appended correction of an earlier record (the log is append-only)
                r |= e.get("fields") or {}
                r.setdefault("corrections", []).append({"at": e.get("at"), "reason": e.get("reason")})
            elif e.get("event") == "started":
                r |= {k: e.get(k) for k in ("branch", "budget", "sports", "agent", "model")} | {"started_at": e.get("at")}
            else:
                r |= {"finished_at": e.get("at"), "end": e.get("event"),
                      **{k: e.get(k) for k in ("stopped", "outcome", "trials", "cost_usd", "reason") if k in e}}
    active = _read(agent / "active.json")
    stop = agent / "STOP"
    daily = sorted((root / "reports").glob("daily-*.json"))
    last_daily = _read(daily[-1]) if daily else None
    return {"configured": True, "note": NOTE,
            "stopped": stop.read_text().strip() if stop.is_file() else None,
            "active_run": None if not active else {
                "run_id": active.get("run_id"), "branch": active.get("branch"), "started_at": active.get("started_at"),
                "deadline": active.get("deadline"), "max_trials": active.get("max_trials"),
                "driver_alive": _alive(active.get("driver_pid"), active.get("driver_token"))},
            "agent_runs": sorted(runs.values(), key=lambda r: r.get("started_at") or "", reverse=True)[:10],
            "last_daily": last_daily and {k: last_daily.get(k) for k in ("started_at", "finished_at", "outcome", "stopped", "refused")},
            "sports": {s: sport_status(p, m, recent) for s, (p, m) in sorted(sports.items())},
            "v3": v3_status(root)}


# --- Protocol v3 (five sports): per-sport state written by autoresearch.run_sport_v3 -----------------------------------------
V3_NOTE = ("Protocol v3 evaluates research candidates for NBA, NHL, NFL, Soccer and NCAAB on chronological search and validation "
           "seasons, against the best existing model, simple baselines and (where valid quotes exist) sportsbook no-vig "
           "probabilities. Exploratory and repeated looks are labelled; the holdout is read once; the forward test is a separate "
           "store. Nothing here changes a live model, forecast or card; a promotion recommendation is advice, never an action.")


def daily_state(path: Optional[Path] = None) -> Optional[list]:
    """The wrapper's per-sport status file (sport, last_attempt_utc, last_outcome, last_success_utc, last_reason), read only."""
    p = path or Path(os.environ.get("NBA_AUTORESEARCH_HOME") or Path.home() / "autoresearch-sports") / "daily-state.tsv"
    try:
        lines = p.read_text(errors="replace").splitlines()
    except OSError:
        return None
    keys = ("sport", "last_attempt_utc", "last_outcome", "last_success_utc", "last_reason")
    return [dict(zip(keys, ln.split("\t"))) for ln in lines if ln.strip() and not ln.startswith(("sport\t", "#"))]


def v3_status(root: Path) -> Optional[dict]:
    """Latest state per sport (state.json of the newest protocol directory) and the latest daily5 report. Reads files only."""
    latest = {}
    for p in sorted((root / "v3").glob("*/*/state.json")):
        st = _read(p)
        if st and st.get("sport") and (st["sport"] not in latest or st.get("at", "") > latest[st["sport"]].get("at", "")):
            latest[st["sport"]] = st
    reports = sorted((root / "reports").glob("daily5-*.json"))
    last = _read(reports[-1]) if reports else None
    if not latest and not last:
        return None
    keep = ("protocol_id", "at", "last_attempt", "last_success_at", "outcome", "reason", "candidates", "evaluated_markets", "blockers",
            "queue", "next_run", "periods", "periods_note", "unsupported", "kept", "evaluation", "data_version", "models", "markets", "caveats")
    sports = {}
    for s, st in sorted(latest.items()):
        sports[s] = {k: st.get(k) for k in keep} | {"candidate_count": len(st.get("candidates") or [])}
        sports[s]["models"] = {n: {"role": m.get("role"), "version": m.get("version")} for n, m in (st.get("models") or {}).items()}
        sports[s]["markets"] = {m: {"kind": v.get("kind"), "settlement": v.get("settlement"), "line_basis": v.get("line_basis")}
                                for m, v in (st.get("markets") or {}).items()}
    return {"protocol": "sports-research-v3", "note": V3_NOTE, "sports": sports, "wrapper_state": daily_state(),
            "last_daily": last and {k: last.get(k) for k in ("started_at", "finished_at", "outcomes", "budget_per_sport")}}
