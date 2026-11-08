"""Soccer 1X2 forecast ledger (migration 0037) and the research reads behind /api/soccer/research/*.

Writes: `record_run` + `record` (append-only; a retry with the same idempotency key is not stored twice, a genuinely new
forecast becomes the next version) and `record_development`, which stores the backtest forecasts a development run
wrote (soccer_eval.develop). Reads: matches of a date with their research forecasts, one match with every forecast
version and result revision, a performance summary computed from the stored rows, and a history list.

Every row read here belongs to a run whose run_type and label travel with it: backtest rows are 'Research backtest',
retrospective and never Live. Only the regulation 1X2 market exists. No probability is computed or invented here: the
API shows stored rows or says there are none.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Optional

from . import metrics, sports
from .db import StoreMismatch, has_table, store_sport, transaction, utc_now

MARKET = "1x2_regulation"
LABELS = {"backtest": "Research backtest", "reserved_test": "Reserved test (one-shot)",
          "prospective": "Prospective research forecast"}          # one fixed label per run type (migration 0037)
BACKTEST_LABEL = LABELS["backtest"]
PERIODS = ("development_tune", "development_calibration", "validation")
OUTCOMES = ("home", "draw", "away")


def _require_soccer(conn: sqlite3.Connection) -> None:
    if store_sport(conn) != sports.SOCCER.id:
        raise StoreMismatch("not a soccer store")


def idempotency_key(run_id: str, hist_match_id: str, cutoff_at: str, inputs_digest: str) -> str:
    return hashlib.sha256("\x1f".join((run_id, hist_match_id, MARKET, cutoff_at, inputs_digest)).encode()).hexdigest()


def record_run(conn: sqlite3.Connection, *, run_id: str, run_type: str, period: str, model: str, model_version: str,
               config: dict, calibrated: bool, protocol_sha256: str, data_digest: str, label: str, source_run: str,
               candidate_sha256: Optional[str] = None, created_at: Optional[str] = None) -> bool:
    """Inserts the run once; True when new. An existing run_id with different content is refused (append-only)."""
    _require_soccer(conn)
    row = (run_id, run_type, period, model, model_version, json.dumps(config, sort_keys=True), int(calibrated),
           protocol_sha256, data_digest, candidate_sha256, source_run, label)
    old = conn.execute("SELECT run_id, run_type, period, model, model_version, config_json, calibrated, protocol_sha256, "
                       "data_digest, candidate_sha256, source_run, label FROM soccer_forecast_runs WHERE run_id = ?",
                       (run_id,)).fetchone()
    if old is not None:
        if tuple(old) != row:
            raise ValueError(f"run {run_id} already exists with different content")
        return False
    conn.execute("INSERT INTO soccer_forecast_runs (run_id, run_type, period, model, model_version, config_json, calibrated,"
                 " protocol_sha256, data_digest, candidate_sha256, source_run, label, created_at) VALUES "
                 "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (*row, created_at or utc_now()))
    return True


def record(conn: sqlite3.Connection, run_id: str, forecasts: list[dict], *, computed_at: str, inputs_digest: str) -> dict:
    """Appends forecasts {mid, cutoff_at, p} of one run, generated now (never a caller-chosen time). Same key again with
    the same numbers: skipped (a retry); with other numbers: refused. Same match with a new key: the next version."""
    _require_soccer(conn)
    generated_at = utc_now()
    stats = {"inserted": 0, "retries": 0, "new_versions": 0}
    for f in forecasts:
        key = idempotency_key(run_id, f["mid"], f["cutoff_at"], inputs_digest)
        old = conn.execute("SELECT p_home, p_draw, p_away FROM soccer_forecasts WHERE idempotency_key = ?", (key,)).fetchone()
        if old:
            if tuple(old) != tuple(float(x) for x in f["p"]):
                raise ValueError(f"same idempotency key, different probabilities for {f['mid']} in {run_id}")
            stats["retries"] += 1
            continue
        version = conn.execute("SELECT IFNULL(MAX(version), 0) + 1 FROM soccer_forecasts WHERE run_id = ? AND "
                               "hist_match_id = ? AND market = ?", (run_id, f["mid"], MARKET)).fetchone()[0]
        ph, pd, pa = f["p"]
        conn.execute("INSERT INTO soccer_forecasts (run_id, hist_match_id, market, version, idempotency_key, inputs_digest,"
                     " p_home, p_draw, p_away, cutoff_at, computed_at, generated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (run_id, f["mid"], MARKET, version, key, inputs_digest, ph, pd, pa, f["cutoff_at"], computed_at,
                      generated_at))
        stats["inserted"] += 1
        stats["new_versions"] += version > 1
    return stats


def record_development(conn: sqlite3.Connection, out_dir: Path) -> dict:
    """Stores the backtest forecasts of a development run (report + forecasts.jsonl in `out_dir`), one run per model and
    period. Reserved-test and prospective forecasts are never produced by a development run."""
    report = json.loads((out_dir / "development_validation_report.json").read_text())
    sealed = json.loads((out_dir / "sealed_candidate.json").read_text())
    if sealed.get("development_run") != report.get("run"):
        raise ValueError("the sealed candidate does not belong to this development run")
    from . import soccer_eval
    if soccer_eval.open_data_digest(conn) != report.get("open_data_digest"):
        raise ValueError("this store's open data differ from the data the development run used")
    version = hashlib.sha256(json.dumps(report["code"], sort_keys=True).encode()).hexdigest()
    by_run: dict[tuple, list] = defaultdict(list)
    period_of = {}
    for s in soccer_eval.DEV_TUNE:
        period_of[s] = "development_tune"
    for s in soccer_eval.DEV_CAL:
        period_of[s] = "development_calibration"
    for s in soccer_eval.VALIDATION:
        period_of[s] = "validation"
    for line in (out_dir / "forecasts.jsonl").read_text().splitlines():
        f = json.loads(line)
        period = period_of.get(f["season"])
        if period is None:
            raise ValueError(f"forecast of season {f['season']} is outside the development and validation periods")
        by_run[(f["model"], period)].append(f)
    want = report["counts"]["scored"]
    for (model, period), fs in by_run.items():
        if len(fs) != want[period]:
            raise ValueError(f"{model} {period}: {len(fs)} forecasts in the folder, the report scored {want[period]}")
    configs = {"frequency": {"frequencies": report["frequency"]["frequencies"]},
               "elo": {**report["elo"]["selected_config"], "draw_model": report["elo"]["draw_model"]},
               "poisson": report["poisson"]["selected_config"]}
    totals = {}
    with transaction(conn):
        for (model, period), fs in sorted(by_run.items()):
            run_id = f"soccer-{report['run']}-{model}-{period}"
            record_run(conn, run_id=run_id, run_type="backtest", period=period, model=model, model_version=version,
                       config=configs[model], calibrated=False, protocol_sha256=report["protocol_sha256"],
                       data_digest=report["open_data_digest"], label=BACKTEST_LABEL, source_run=report["run"],
                       candidate_sha256=sealed["sha256"] if model == sealed["model"] else None,
                       created_at=report["finished_at"])
            totals[run_id] = record(conn, run_id, fs, computed_at=report["finished_at"],
                                    inputs_digest=report["open_data_digest"])
    return totals


# --- Reads (API) -----------------------------------------------------------------------------------------------------
def available(conn: sqlite3.Connection) -> bool:
    return has_table(conn, "soccer_forecasts") and has_table(conn, "soccer_hist_matches")


def _side(h: Optional[int], a: Optional[int]) -> Optional[str]:
    if h is None or a is None:
        return None
    return "home" if h > a else ("draw" if h == a else "away")


GRADING_TEXT = {"gradable": None, "not_final": "No result in the source yet", "cancelled": "Cancelled",
                "awarded": "Result awarded off the pitch: not graded",
                "extra_time_noted": "Went to extra time: no graded 90-minute result",
                "deciding_match_unverified": "Deciding knockout match: 90-minute result not independently verified, not graded"}


def _club(cid: str) -> dict:
    country, name = cid.split(":", 1)
    return {"club_id": cid, "name": name, "country": country}


def _match(r) -> dict:
    return {"match_id": r["hist_match_id"], "competition_id": r["competition_id"], "season": r["season"],
            "round": r["round"], "stage": r["stage"], "local_date": r["local_date"], "local_time": r["local_time"],
            "local_time_listed": None if r["local_time_listed"] is None else bool(r["local_time_listed"]),
            "home": _club(r["home_club_id"]), "away": _club(r["away_club_id"]), "status": r["status"],
            "ft_home": r["ft_home"], "ft_away": r["ft_away"], "ht_home": r["ht_home"], "ht_away": r["ht_away"],
            "aet_home": r["aet_home"], "aet_away": r["aet_away"], "pen_home": r["pen_home"], "pen_away": r["pen_away"],
            "regulation_result": _side(r["ft_home"], r["ft_away"]) if r["grading"] == "gradable" else None,
            "grading": r["grading"], "grading_note": GRADING_TEXT[r["grading"]],
            "result_available_at": r["result_available_at"], "result_availability_basis": r["result_availability_basis"]}


def _forecast(r) -> dict:
    return {"forecast_id": r["forecast_id"], "run_id": r["run_id"], "run_type": r["run_type"], "period": r["period"],
            "model": r["model"], "label": r["label"], "calibrated": bool(r["calibrated"]), "market": r["market"],
            "version": r["version"], "p_home": r["p_home"], "p_draw": r["p_draw"], "p_away": r["p_away"],
            "cutoff_at": r["cutoff_at"], "computed_at": r["computed_at"], "generated_at": r["generated_at"],
            "published_at": r["published_at"], "is_selected_candidate": r["candidate_sha256"] is not None}


FORECAST_SQL = ("SELECT f.*, r.run_type, r.period, r.model, r.label, r.calibrated, r.candidate_sha256 FROM soccer_forecasts f "
                "JOIN soccer_forecast_runs r ON r.run_id = f.run_id AND r.run_type = 'backtest' AND r.source_run = :src ")


def _source(conn) -> Optional[str]:
    """The development run whose backtest rows the research screens show: the one recorded last (insertion order, not a
    writer-supplied time) among runs that hold forecasts. Rows of other development runs, reserved-test and prospective
    runs are never mixed into these reads (review S6 M2/M4, re-review N1)."""
    r = conn.execute("SELECT r.source_run FROM soccer_forecast_runs r WHERE r.run_type = 'backtest' AND EXISTS (SELECT 1 "
                     "FROM soccer_forecasts f WHERE f.run_id = r.run_id) ORDER BY r.rowid DESC LIMIT 1").fetchone()
    return r[0] if r else None


PROTOCOL_FILE = Path(__file__).with_name("soccer_protocol.json")


def _last_open() -> int:
    """Seasons after the protocol's last open season are reserved: no research read returns them (review S6 M5). Read
    straight from the protocol file, without loading the evaluation module (re-review N2: no numpy/scipy import and no
    protocol digest check on the read path; the digest guards evaluation, not this cap)."""
    return int(json.loads(PROTOCOL_FILE.read_text())["periods"]["last_open_season"])


def _latest_forecasts(conn, mids: list[str]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    if not mids:
        return out
    marks = ", ".join(f":m{i}" for i in range(len(mids)))
    q = (FORECAST_SQL + f"WHERE f.hist_match_id IN ({marks}) AND f.version = (SELECT MAX(version) FROM "
         "soccer_forecasts g WHERE g.run_id = f.run_id AND g.hist_match_id = f.hist_match_id) ORDER BY r.model, f.run_id")
    for r in conn.execute(q, {"src": _source(conn), **{f"m{i}": m for i, m in enumerate(mids)}}):
        out[r["hist_match_id"]].append(_forecast(r))
    return out


def research_meta(conn: sqlite3.Connection, competition: str) -> dict:
    runs = [dict(r) for r in conn.execute(
        "SELECT r.run_id, r.run_type, r.period, r.model, r.label, r.calibrated, r.protocol_sha256, r.created_at, "
        "r.candidate_sha256 IS NOT NULL AS is_selected_candidate, COUNT(m.hist_match_id) AS forecasts, "
        "MIN(m.local_date) AS first_date, MAX(m.local_date) AS last_date FROM soccer_forecast_runs r "
        "LEFT JOIN soccer_forecasts f ON f.run_id = r.run_id LEFT JOIN soccer_hist_matches m ON m.hist_match_id = f.hist_match_id "
        "AND m.competition_id = ? AND m.season <= ? WHERE r.run_type = 'backtest' AND r.source_run = ? "
        "GROUP BY r.run_id ORDER BY r.period, r.model", (competition, _last_open(), _source(conn)))]
    for r in runs:
        r["is_selected_candidate"] = bool(r["is_selected_candidate"])
        r["calibrated"] = bool(r["calibrated"])
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT m.local_date FROM soccer_forecasts f JOIN soccer_forecast_runs r ON r.run_id = f.run_id JOIN "
        "soccer_hist_matches m ON m.hist_match_id = f.hist_match_id WHERE m.competition_id = ? AND m.season <= ? AND "
        "r.run_type = 'backtest' AND r.source_run = ? ORDER BY 1", (competition, _last_open(), _source(conn)))]
    cov = conn.execute("SELECT MIN(season), MAX(season), COUNT(*) FROM soccer_hist_matches WHERE competition_id = ? AND "
                       "season <= ?", (competition, _last_open())).fetchone()
    return {"competition_id": competition, "runs": runs, "forecast_dates": dates,
            "history": {"first_season": cov[0], "last_season": cov[1], "matches": cov[2]},
            "label": BACKTEST_LABEL, "market": MARKET,
            "notice": "Retrospective research forecasts of past matches (development and validation periods), computed long "
                      "after the matches. Not live predictions, not betting advice. The reserved test seasons are locked."}


def research_matches(conn: sqlite3.Connection, competition: str, local_date: str) -> dict:
    rows = conn.execute("SELECT * FROM soccer_hist_matches WHERE competition_id = ? AND local_date = ? AND season <= ? "
                        "ORDER BY local_time IS NULL, local_time, hist_match_id",
                        (competition, local_date, _last_open())).fetchall()
    fc = _latest_forecasts(conn, [r["hist_match_id"] for r in rows])
    return {"competition_id": competition, "date": local_date,
            "matches": [{**_match(r), "forecasts": fc.get(r["hist_match_id"], [])} for r in rows]}


def research_match(conn: sqlite3.Connection, match_id: str) -> Optional[dict]:
    r = conn.execute("SELECT * FROM soccer_hist_matches WHERE hist_match_id = ? AND season <= ?",
                     (match_id, _last_open())).fetchone()
    if r is None:
        return None
    forecasts = [_forecast(x) for x in conn.execute(FORECAST_SQL + "WHERE f.hist_match_id = :mid ORDER BY r.model, f.run_id, "
                                                                   "f.version", {"src": _source(conn), "mid": match_id})]
    revisions = [dict(x) for x in conn.execute(
        "SELECT revision, local_date, local_time, status, ft_home, ft_away, aet_home, aet_away, pen_home, pen_away, deciding,"
        " in_file, result_available_at, result_availability_basis, observed_at FROM soccer_hist_revisions WHERE "
        "hist_match_id = ? ORDER BY revision", (match_id,))]
    src = conn.execute("SELECT f.repo, f.commit_sha, f.path, f.retrieved_at FROM soccer_source_files f WHERE f.fetch_id = ?",
                       (r["last_seen_fetch_id"],)).fetchone()
    return {"match": _match(r), "forecasts": forecasts, "revisions": revisions, "source": dict(src) if src else None}


def performance(conn: sqlite3.Connection, competition: Optional[str] = None) -> dict:
    """Metrics of the stored research forecasts against the graded 90-minute results, computed from the rows (latest
    version per run and match). Only gradable matches count."""
    q = (FORECAST_SQL.replace("SELECT f.*", "SELECT f.*, m.ft_home, m.ft_away, m.competition_id, m.season") +
         "JOIN soccer_hist_matches m ON m.hist_match_id = f.hist_match_id WHERE m.grading = 'gradable' AND m.season <= :last "
         "AND f.version = (SELECT MAX(version) FROM soccer_forecasts g WHERE g.run_id = f.run_id AND "
         "g.hist_match_id = f.hist_match_id)" + (" AND m.competition_id = :comp" if competition else ""))
    groups = defaultdict(list)
    meta = {}
    for r in conn.execute(q, {"src": _source(conn), "last": _last_open(), "comp": competition}):
        key = (r["period"], r["model"])
        y = 0 if r["ft_home"] > r["ft_away"] else (1 if r["ft_home"] == r["ft_away"] else 2)
        groups[key].append(((r["p_home"], r["p_draw"], r["p_away"]), y))
        meta[key] = {"run_type": r["run_type"], "label": r["label"], "is_selected_candidate": r["candidate_sha256"] is not None,
                     "calibrated": bool(r["calibrated"])}
    rows = []
    for (period, model), items in sorted(groups.items(), key=lambda kv: (PERIODS.index(kv[0][0]), kv[0][1])):
        vecs, ys = [v for v, _ in items], [y for _, y in items]
        rel = []
        for k, name in enumerate(OUTCOMES):
            bins = defaultdict(list)
            for v, y in items:
                bins[min(9, int(v[k] * 10))].append((v[k], y == k))
            rel.append({"outcome": name, "bins": [{"lower": b / 10, "upper": (b + 1) / 10, "n": len(bins[b]),
                                                   "mean_predicted": sum(p for p, _ in bins[b]) / len(bins[b]),
                                                   "observed": sum(o for _, o in bins[b]) / len(bins[b])}
                                                  for b in sorted(bins)]})
        rows.append({"period": period, "model": model, **meta[(period, model)], "n": len(items),
                     "log_loss": metrics.log_loss_multi(vecs, ys), "brier": metrics.brier_multi(vecs, ys),
                     "mean_predicted": [sum(v[k] for v in vecs) / len(vecs) for k in range(3)],
                     "observed": [sum(y == k for y in ys) / len(ys) for k in range(3)], "reliability": rel})
    return {"competition_id": competition, "label": BACKTEST_LABEL, "rows": rows,
            "notice": "Computed from the stored research backtest forecasts and graded 90-minute results. Validation decided "
                      "the model choice once; these are not out-of-sample test results and not a betting edge."}


def research_history(conn: sqlite3.Connection, competition: str, season: Optional[int], model: Optional[str],
                     limit: int = 200, offset: int = 0) -> dict:
    where, args = ["m.competition_id = :comp", "m.season <= :last"], {"comp": competition, "last": _last_open(),
                                                                        "src": _source(conn)}
    if season is not None:
        where.append("m.season = :season")
        args["season"] = season
    if model:
        where.append("r.model = :model")
        args["model"] = model
    base = (FORECAST_SQL.replace("SELECT f.*", "SELECT f.*, m.*") + "JOIN soccer_hist_matches m ON m.hist_match_id = "
            "f.hist_match_id WHERE " + " AND ".join(where) + " AND f.version = (SELECT MAX(version) FROM soccer_forecasts g "
            "WHERE g.run_id = f.run_id AND g.hist_match_id = f.hist_match_id)")
    total = conn.execute(f"SELECT COUNT(*) FROM ({base})", args).fetchone()[0]
    rows = conn.execute(base + " ORDER BY m.local_date DESC, m.hist_match_id, r.model LIMIT :limit OFFSET :offset",
                        {**args, "limit": limit, "offset": offset}).fetchall()
    seasons = [r[0] for r in conn.execute(
        "SELECT DISTINCT m.season FROM soccer_forecasts f JOIN soccer_forecast_runs r ON r.run_id = f.run_id JOIN "
        "soccer_hist_matches m ON m.hist_match_id = f.hist_match_id WHERE m.competition_id = ? AND m.season <= ? AND "
        "r.run_type = 'backtest' AND r.source_run = ? ORDER BY 1 DESC", (competition, _last_open(), _source(conn)))]
    return {"competition_id": competition, "season": season, "model": model, "total": total, "limit": limit,
            "offset": offset, "seasons": seasons, "label": BACKTEST_LABEL,
            "items": [{"match": _match(r), "forecast": _forecast(r)} for r in rows]}
