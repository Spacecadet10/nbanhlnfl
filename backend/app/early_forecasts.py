"""Early-forecast test, NHL winner (docs/evidence/early-forecast-test/PREREGISTRATION.md). Research only: rows go to
`early_forecasts` (migration 0032), never to a primary, research, adaptive or selection table, and are paper-traded at the
first quote retrieved after them (app/paper_trading.py). Nothing here changes a canonical forecast.

Arms (fixed in the pre-registration):
- 'open': `hockey_logistic_xg` as the research cycle computes it (agreement.predict, season-start refits), made once per
  game at the first run that finds a bookmaker moneyline for it (lines are fetched up to 36 h before the start).
- 'news': `nhl_players_injuries@adaptive` (the only NHL member that reads injury reports), served from its fit in force
  exactly as the adaptive cycle serves it (adaptive.record, read-only replay evaluation at `now`), for every quoted game of
  a team whose rows changed between the two latest live injury captures, once per capture, only while that capture is
  fresh (NEWS_WINDOW).

No information retrieved after `now` is used: results, shots and quotes by retrieval time, captures by capture time, fits
published by then. This file is not part of any model or adaptive definition, so editing it retires no fit."""
import json
import math
import random
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import adaptive, agreement, backtest, config, db, live, near_tipoff
from . import nhl_player_ratings as npr
from .db import utc_now

OPEN_MEMBER = "hockey_logistic_xg"
NEWS_MEMBER = "nhl_players_injuries@adaptive"
HORIZON = timedelta(hours=36)            # jobs.ODDS_WINDOW: no line is fetched earlier
NEWS_WINDOW = timedelta(minutes=45)      # a capture older than this is not news any more (captures every 30 min)
# Pre-registered analysis (PREREGISTRATION.md): fixed before the first early forecast exists.
THRESHOLD = 0.06
MIN_BETS = 150
END = "2027-05-01T00:00:00Z"             # games starting before this time (the 2026-27 regular season)
BOOTSTRAP = 10000
SEED = 20261016
FMT = "%Y-%m-%dT%H:%M:%SZ"
COLUMNS = ("sport", "arm", "member", "model_version", "game_id", "market", "status", "reason", "p_a", "p_b", "information_at",
           "trigger_detail", "provenance", "tipoff_at", "generated_at", "idempotency_key")


def _parse(s: str) -> datetime:
    return datetime.strptime(s, FMT).replace(tzinfo=timezone.utc)


def _fmt(d: datetime) -> str:
    return d.strftime(FMT)


# --- Arm 'open': the research xG member at any time before the start ------------------------------------------------
def xg_forecasts(conn, now: str, game_ids: list) -> dict:
    """{game id: (P(home win), None) or (None, reason)} for scheduled games, as agreement.predict computes the shadow member
    for games within two hours, at any earlier `now`: the same walk over the results and shots available at `now`, the same
    schedule order, rounding and abstentions (test_early_forecasts pins it to agreement.predict). agreement.py is part of
    the research and adaptive definitions, so its two-hour window is not widened there."""
    eng = live.engine(conn)
    if eng.sport != "nhl":
        raise ValueError("the early-forecast test is NHL only")
    wanted = {r["game_id"]: r for r in conn.execute(
        f"SELECT * FROM games WHERE game_id IN ({','.join('?' * len(game_ids))})", list(game_ids))}
    out = {gid: (None, "not a scheduled game after this time") for gid in game_ids}
    eligible = {gid for gid, g in wanted.items() if g["status"] == "scheduled" and g["tipoff_at"] > now}
    if not eligible:
        return out
    horizon = max(wanted[gid]["tipoff_at"] for gid in eligible)
    rows = live.available_rows(conn, now)
    known = {r.game.game_id: r for r in rows}
    schedule = conn.execute(
        f"SELECT * FROM games WHERE status NOT IN ('postponed','canceled') AND tipoff_at <= ? "
        f"AND season_type IN ({','.join('?' for _ in eng.eligible_types)}) ORDER BY tipoff_at, game_id",
        (horizon, *eng.eligible_types)).fetchall()
    schedule = [g for g in schedule if g["game_id"] not in known]
    from . import nhl_shots
    shots = [e for e in nhl_shots.load_events(conn, rows)
             if max(e.available_at, known[e.game.game_id].result_available_at) <= now]
    model = next(m for m in agreement.models(conn) if m.name == OPEN_MEMBER)
    from dataclasses import replace
    training = [replace(r, cutoff_at=live._ts(live._parse(r.game.tipoff_at) - near_tipoff.WINDOW))
                for r in rows] if getattr(model, "training_two_hour", False) else rows
    backtest.run(model, training, shots, deliver_until=now)
    recent: dict = {}
    for r in sorted(rows, key=lambda r: r.game.tipoff_at, reverse=True):
        for team in (r.game.home_team_id, r.game.away_team_id):
            recent.setdefault((r.game.season, team), []).append(r.game.game_id)
    for g in schedule:
        game = eng.pregame(g)
        p = round(model.predict(game), 9)
        if game.game_id not in eligible:
            continue
        xg = model.xg
        if xg.valued == 0 or any(gid not in xg.lines for team in (game.home_team_id, game.away_team_id)
                                 for gid in recent.get((game.season, team), [])[:agreement.STALE_SHOT_GAMES]):
            out[game.game_id] = (None, f"stale shot data: a team's last {agreement.STALE_SHOT_GAMES} finals lack imported shots")
        elif hasattr(model, "oos_from_season") and model.oos_from_season() is None:
            out[game.game_id] = (None, "no out-of-sample season yet")
        else:
            out[game.game_id] = (p, None)
    return out


# --- Arm 'news': the injury member, served as the adaptive cycle serves it --------------------------------------------
def adaptive_member(conn, now: str, game, member: str) -> dict:
    """{'p': winner vector or None, 'reason', 'definition', 'provenance'} of one adaptive member for `game` at `now`, through
    adaptive.record's read-only evaluation of a single game (its `replay` path: any game whose start is after `now`, fits
    trained on results through `now`, nothing written). test_early_forecasts pins it to the vector the live cycle records.
    A member adaptive.members excludes (e.g. no player/injury cache) is unavailable with that reason, without an evaluation."""
    mems, excluded = adaptive.members(conn)
    if member not in {m.name for m in mems}:
        why = excluded.get(member) or excluded.get(member.split("@")[0]) or "not an adaptive member"
        return {"p": None, "reason": f"excluded: {why}", "definition": adaptive.definition(conn), "provenance": {}}
    out = adaptive.record(conn, now=now, replay=game, write=False)
    res = {"p": None, "reason": None, "definition": out.get("definition"), "provenance": {}}
    f = next((x for x in out.get("forecasts", []) if x["game_id"] == game["game_id"] and x["market"] == "winner"), None)
    row = next((r for r in (f or {}).get("member_rows", []) if r["name"] == member), None)
    if row is None:
        res["reason"] = ((f or {}).get("missing", {}).get(member) or out.get("errors", {}).get(member) or out.get("error")
                         or (out.get("skipped", {}).get("winner") or {}).get(game["game_id"]) or "member not evaluated")
        return res
    res["provenance"] = {k: row.get(k) for k in ("fit_id", "artifact_sha256", "training_cutoff", "information_at", "finished_at")}
    if not row.get("finished_at") or row["finished_at"] > now:     # chosen by its training data; must also exist by `now`
        res["reason"] = f"the fit applied (fit {row.get('fit_id')}) was published after this time ({row.get('finished_at')})"
        return res
    res["p"] = list(row["p"])
    return res


def captures(now: str) -> list:
    """Live NHL injury captures (nhl_injuries.capture_live metadata) taken at or before `now`, oldest first. Unreadable
    files are skipped (the injury member verifies the raw pages itself)."""
    d = config.data_dir() / "research" / "nhl_players" / "injuries"
    out = []
    for p in sorted(d.glob("live-*.json")) if d.is_dir() else ():
        try:
            meta = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(meta, dict) and meta.get("captured_at") and meta["captured_at"] <= now:
            out.append(meta)
    return sorted(out, key=lambda m: m["captured_at"])


def _per_team(capture: dict) -> dict:
    teams: dict = defaultdict(set)
    for t in capture.get("teams", ()):
        teams.setdefault(npr.team_key(t), set())
    for r in capture.get("rows", ()):
        teams[npr.team_key(r.get("team"))].add((npr.norm(r.get("player") or ""), (r.get("status") or "").strip().lower(),
                                                r.get("weight")))
    return teams


def changed_teams(previous: dict, latest: dict) -> set:
    """Team keys (nhl_player_ratings.team_key) whose listed players, statuses or weights differ between two captures."""
    a, b = _per_team(previous), _per_team(latest)
    return {t for t in set(a) | set(b) if t is not None and a.get(t, set()) != b.get(t, set())}


# --- One run --------------------------------------------------------------------------------------------------------
def _existing(conn) -> set:
    if not db.has_table(conn, "early_forecasts"):
        return set()
    return {r[0] for r in conn.execute("SELECT idempotency_key FROM early_forecasts")}


def _row(arm, member, version, g, now, generated, p, reason, trigger, provenance, key) -> dict:
    return {"sport": "nhl", "arm": arm, "member": member, "model_version": version or "unknown", "game_id": g["game_id"],
            "market": "winner", "status": "forecast" if p is not None else "unavailable",
            "reason": None if p is not None else (reason or "no forecast"),
            "p_a": p[0] if p is not None else None, "p_b": p[1] if p is not None else None, "information_at": now,
            "trigger_detail": json.dumps(trigger, sort_keys=True), "provenance": json.dumps(provenance, sort_keys=True, default=str),
            "tipoff_at": g["tipoff_at"], "generated_at": generated, "idempotency_key": key}


def plan(conn, now: str, *, fixed_clock: bool = True) -> dict:
    """The rows one run would append at `now` (read-only; `conn` may be a query-only connection)."""
    eng = live.engine(conn)
    if eng.sport != "nhl":
        raise ValueError("the early-forecast test is NHL only")
    done = _existing(conn)
    horizon = _fmt(_parse(now) + HORIZON)
    games = conn.execute(
        f"SELECT * FROM games WHERE status='scheduled' AND tipoff_at > ? AND tipoff_at <= ? AND season_type IN "
        f"({','.join('?' * len(eng.eligible_types))}) ORDER BY tipoff_at, game_id", (now, horizon, *eng.eligible_types)).fetchall()
    first = {r["game_id"]: (r["retrieved_at"], r["quote_id"]) for r in conn.execute(
        "SELECT game_id, retrieved_at, quote_id FROM market_quotes q WHERE market='winner' AND price_label='current' "
        "AND p_a_devig IS NOT NULL AND retrieved_at <= ? AND quote_id = (SELECT x.quote_id FROM market_quotes x "
        "WHERE x.game_id=q.game_id AND x.market='winner' AND x.price_label='current' AND x.p_a_devig IS NOT NULL "
        "AND x.retrieved_at <= ? ORDER BY x.retrieved_at, x.quote_id LIMIT 1)", (now, now))}
    quoted = [g for g in games if g["game_id"] in first]
    finish = (lambda: now) if fixed_clock else utc_now
    out = {"now": now, "open": {"candidates": [], "rows": []}, "news": {"changed_teams": [], "rows": [], "reason": None}}

    # Arm 'open': once per game, at the first run that finds its line.
    todo = [g for g in quoted if f"open|{g['game_id']}" not in done]
    out["open"]["candidates"] = [g["game_id"] for g in todo]
    if todo:
        version = f"research:{agreement.definition_hash(agreement.models(conn))}"
        try:
            xs = xg_forecasts(conn, now, [g["game_id"] for g in todo])
        except Exception as exc:
            xs = {g["game_id"]: (None, f"error: {type(exc).__name__}: {exc}") for g in todo}
        generated = finish()
        for g in todo:
            p, reason = xs[g["game_id"]]
            out["open"]["rows"].append(_row("open", OPEN_MEMBER, version, g, now, generated,
                                            None if p is None else (p, 1 - p), reason,
                                            {"first_quote_at": first[g["game_id"]][0], "quote_id": first[g["game_id"]][1]},
                                            {"results_through": now}, f"open|{g['game_id']}"))

    # Arm 'news': games of teams whose report changed in the latest capture, while it is fresh.
    caps = captures(now)
    news = out["news"]
    if len(caps) < 2:
        news["reason"] = "fewer than two live injury captures"
        return out
    latest, previous = caps[-1], caps[-2]
    if _parse(latest["captured_at"]) < _parse(now) - NEWS_WINDOW:
        news["reason"] = f"the latest capture ({latest['captured_at']}) is older than {int(NEWS_WINDOW.total_seconds() // 60)} min"
        return out
    changed = changed_teams(previous, latest)
    news["changed_teams"] = sorted(changed)
    keys = {r["team_id"]: npr.team_key(r["name"]) for r in conn.execute("SELECT team_id, name FROM teams")}
    hit = [g for g in quoted if {keys.get(g["home_team_id"]), keys.get(g["away_team_id"])} & changed
           and f"news|{g['game_id']}|{latest['captured_at']}" not in done]
    for g in hit:
        try:
            m = adaptive_member(conn, now, g, NEWS_MEMBER)
        except Exception as exc:
            m = {"p": None, "reason": f"error: {type(exc).__name__}: {exc}", "definition": None, "provenance": {}}
        v = m["p"]
        p = None if v is None else (v[0], v[2]) if len(v) == 3 else (v[0], 1 - v[0])
        news["rows"].append(_row("news", NEWS_MEMBER, m["definition"] and f"adaptive:{m['definition']}", g, now, finish(), p,
                                 m["reason"], {"capture_at": latest["captured_at"], "previous_capture_at": previous["captured_at"],
                                               "changed_teams": sorted(changed & {keys.get(g["home_team_id"]),
                                                                                  keys.get(g["away_team_id"])})},
                                 m["provenance"], f"news|{g['game_id']}|{latest['captured_at']}"))
    return out


def store_row(conn, row: dict) -> bool:
    """Appends one row; False when its idempotency key is already stored. The store refuses rows made at or after the
    start, or for a game that is not scheduled at that start (migration 0032)."""
    cur = conn.execute(f"INSERT INTO early_forecasts ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))}) "
                       "ON CONFLICT(idempotency_key) DO NOTHING", [row[c] for c in COLUMNS])
    return cur.rowcount == 1


def write_rows(conn, planned: dict) -> dict:
    """Stores a plan's rows in one transaction; a row the store refuses (the game started or moved meanwhile) is reported."""
    counts = {"open": {"created": 0, "refused": {}}, "news": {"created": 0, "refused": {}}}
    with db.transaction(conn):
        for arm in ("open", "news"):
            for r in planned[arm]["rows"]:
                try:
                    counts[arm]["created"] += store_row(conn, r)
                except Exception as exc:
                    counts[arm]["refused"][r["idempotency_key"]] = f"{type(exc).__name__}: {exc}"
    return counts


def run(conn, *, now: Optional[str] = None, write: bool = True, capture: bool = True) -> dict:
    """One run on one connection (tests, dry runs): an injury capture first (when enabled and writing), then plan, then
    append. The scheduled command plans on a query-only connection and appends under the store lock (cli.py)."""
    fixed = now is not None
    out_capture = None
    if capture and write:
        from . import jobs
        out_capture = jobs.nhl_injury_capture()
    now = now or utc_now()
    out = plan(conn, now, fixed_clock=fixed)
    out["capture"] = out_capture
    for arm in ("open", "news"):
        out[arm]["created"] = 0
    if write:
        counts = write_rows(conn, out)
        for arm in ("open", "news"):
            out[arm].update(counts[arm])
    return out



def coverage(conn) -> dict:
    """Per arm (reported, never decisive): rows by status, unavailable rows by reason, model versions in force, and hours
    from the trigger (first quote / capture) to the forecast."""
    out: dict = {}
    if not db.has_table(conn, "early_forecasts"):
        return out
    for r in conn.execute("SELECT arm, status, reason, model_version, trigger_detail, generated_at FROM early_forecasts"):
        a = out.setdefault(r["arm"], {"unavailable_reasons": {}, "model_versions": {}, "_hours": []})
        if r["status"] == "unavailable":
            why = (r["reason"] or "")[:120]
            a["unavailable_reasons"][why] = a["unavailable_reasons"].get(why, 0) + 1
        a["model_versions"][r["model_version"]] = a["model_versions"].get(r["model_version"], 0) + 1
        t = json.loads(r["trigger_detail"]).get("first_quote_at" if r["arm"] == "open" else "capture_at")
        if t:
            a["_hours"].append((_parse(r["generated_at"]) - _parse(t)).total_seconds() / 3600)
    for arm, a in out.items():
        h = sorted(a.pop("_hours"))
        key = "hours_from_first_quote" if arm == "open" else "hours_from_capture"
        a[key] = {"median": round(h[len(h) // 2] if len(h) % 2 else (h[len(h) // 2 - 1] + h[len(h) // 2]) / 2, 2),
                  "max": round(h[-1], 2)} if h else None
    return out


# --- Pre-registered analysis ----------------------------------------------------------------------------------------
def _week(tipoff: str) -> tuple:
    return _parse(tipoff).isocalendar()[:2]


def verdict(bets: list) -> dict:
    """The pre-registered decision for one arm: mean excess CLV (de-vigged close of the bet side minus the implied
    probability of the price taken) with a week-block bootstrap 95% interval (BOOTSTRAP resamples, SEED). 'pass' when the
    lower bound is above 0 with at least MIN_BETS bets, 'fail' otherwise, 'inconclusive' with fewer bets."""
    usable = [b for b in bets if b.get("excess_clv") is not None]
    weeks: dict = defaultdict(list)
    for b in usable:
        weeks[_week(b["tipoff_at"])].append(b["excess_clv"])
    keys = sorted(weeks)
    for k in keys:
        weeks[k].sort()
    n = len(usable)
    settled = [b["profit"] for b in bets if b.get("profit") is not None]
    out = {"bets": n, "weeks": len(keys), "excess_clv_mean": math.fsum(x for k in keys for x in weeks[k]) / n if n else None,
           "excess_clv_ci": None, "roi": math.fsum(settled) / len(settled) if settled else None, "settled": len(settled)}
    if len(keys) >= 2:
        rng = random.Random(SEED)
        means = []
        for _ in range(BOOTSTRAP):
            vals = [x for k in (rng.choice(keys) for _ in keys) for x in weeks[k]]
            means.append(math.fsum(vals) / len(vals))
        means.sort()
        out["excess_clv_ci"] = [means[int(0.025 * BOOTSTRAP)], means[int(0.975 * BOOTSTRAP) - 1]]
    if n < MIN_BETS or out["excess_clv_ci"] is None:
        out["verdict"] = "inconclusive"
    else:
        out["verdict"] = "pass" if out["excess_clv_ci"][0] > 0 else "fail"
    return out


def evaluate(conn, *, end: str = END) -> dict:
    """Per arm: the pre-registered verdict over paper-traded early bets at THRESHOLD on games starting before `end`."""
    from . import paper_trading
    by: dict = defaultdict(list)
    for b in paper_trading.bets(conn, thresholds=(THRESHOLD,)):
        if b["source"].startswith("early:") and b["tipoff_at"] < end:
            by[b["source"]].append(b)
    return {source: verdict(rows) for source, rows in sorted(by.items())}
