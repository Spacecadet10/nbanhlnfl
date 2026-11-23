"""Recovered Telegram predictions (Nov 2-16 2026) in Performance and Prediction History.

Source: <data>/research/telegram_history.json (scripts/build_telegram_history.py, from the owner's Telegram export). Each row is
what a card actually said before the start: game, market, side, line, percentage, with the result where one is known. Nothing
here is computed by a model: a row is never re-forecast, and a live or reconstructed number never replaces the card's number.

Counting rule: one prediction per (sport, game, market).
- A game and market the live ledger also holds is counted ONCE, by the live record (the latest pregame forecast, the record every
  page grades). The recovered row stays listed, not counted, with how it compares (`overlap.status`):
    same                  the live forecast of record has the same side, line and displayed percentage
    earlier_live_version  the card matches an earlier pregame forecast of the live ledger, replaced before the start
    different             the card differs from every live pregame forecast (both stay visible)
- Every other recovered row is counted by the recovered record (`overlap.status` none).
The export holds no prices, so every recovered row is unpriced; a row without a result is unsettled, never a loss. The live side
is the canonical cohort the pages already grade. Distinct from /api/recovered-forecasts (retrospective reconstructions).
"""
import json
from collections import Counter
from typing import Iterable, Optional

from . import threshold_overview as to

LABEL = "Recovered Telegram prediction — model version unknown"
NOTE = ("Predictions shown on the Telegram cards sent before each game, with their original percentages. The cards did not show "
        "the model version or a price: these rows are unpriced and carry no profit or loss. One prediction per game and market: "
        "where the live record holds the same game and market, the live forecast is the one counted and the card stays listed.")
OVERLAP_STATES = ("same", "earlier_live_version", "different")
SOCCER_MARKETS = ("1x2_regulation", "double_chance", "draw_no_bet")
SOCCER_TITLES = {"1x2_regulation": "Match result (90 min)"}
DECISIVE = ("win", "loss", "push")
VOID = ("void", "cancelled", "canceled")
_cache: dict = {}


# --- The imported file ------------------------------------------------------------------------------------------------
def load() -> tuple[Optional[dict], Optional[str]]:
    """(the imported file, None) or (None, why not), cached by path and modification time."""
    path = to.telegram_history_path()
    try:
        key = (str(path), path.stat().st_mtime_ns)
    except OSError:
        return None, "file_missing"
    if _cache.get("key") != key:
        try:
            body = json.loads(path.read_text())
            body["rows"][:0]                         # a file without rows is unreadable, not empty
        except (OSError, ValueError, KeyError, TypeError):
            return None, "unreadable"
        _cache.clear()
        _cache.update(key=key, body=body)
    return _cache["body"], None


# --- Live items: the live forecast of record per game and market, in one shape -----------------------------------------
def point_live_items(conn, sport: str, cohorts: Iterable) -> list:
    """`cohorts` [(market, [(game, canonical)])] -> live items. Started games with a forecast of record only."""
    from . import markets
    abbr = dict(conn.execute("SELECT team_id, abbr FROM teams").fetchall())
    versions: dict = {}
    for r in conn.execute("SELECT forecast_id, game_id, market, generated_at, p_a, p_mid, p_b, confidence, line FROM game_forecasts "
                          "WHERE status='forecast' AND generated_at < tipoff_at ORDER BY generated_at, forecast_id"):
        versions.setdefault((r["game_id"], r["market"]), []).append(r)
    from . import coverage
    out = []
    for market, cohort in cohorts:
        for g, c in cohort:
            rec = c["record"]
            if c["state"] != "forecast" or rec is None:
                continue
            home, away = abbr[g["home_team_id"]], abbr[g["away_team_id"]]
            labels = markets.side_labels(market, rec["line"], home, away)
            out.append({
                "game_id": g["game_id"], "market": market, "percentage": to.displayed_percentage(rec["confidence"]), "side": rec["side"],
                "home_line": rec["line"], "line_source": rec["line_source"], "grade": c["grade"], "start_at": g["tipoff_at"],
                "game_date": coverage.et_date(g["tipoff_at"]), "forecast_id": rec["forecast_id"], "generated_at": rec["generated_at"],
                "model_version": rec["model_version"], "pick": labels[("a", "mid", "b").index(rec["side"])],
                "matchup": f"{away} @ {home}", "home": home, "away": away,
                "final_score": (f"{away} {g['away_score']} - {g['home_score']} {home}"
                                if g["status"] == "final" and g["home_score"] is not None else None),
                "versions": [{"forecast_id": v["forecast_id"], "generated_at": v["generated_at"], "home_line": v["line"],
                              "side": coverage.pick_side(market, (v["p_a"], v["p_mid"], v["p_b"])),
                              "percentage": to.displayed_percentage(v["confidence"])}
                             for v in versions.get((g["game_id"], market), []) if v["forecast_id"] != rec["forecast_id"]]})
    return out


def provider_live_items(provider) -> list:
    """NBA / NHL / NCAAB: the canonical competitive cohort Performance grades."""
    from . import sports
    return point_live_items(provider.conn, provider.sport,
                            [(m, provider._canonical_cohort(m)[1]) for m in sports.get(provider.sport).markets])


def nfl_live_items(conn, views) -> list:
    """NFL: the cohort of the NFL record page (started games since activation)."""
    from . import sports
    began = views.active["activated_at"] if views.active else None
    return point_live_items(conn, "nfl", [(m, [(g, c) for g, c in views.cohort(m) if began and began <= g["tipoff_at"] <= views.now])
                                          for m in sports.NFL.markets])


def soccer_live_items(conn, as_of: str) -> list:
    """Soccer: the live pick the cards and the soccer record grade, for the three markets the recovered cards show: the 1X2 forecast of
    record and its double chance / draw no bet sides (the same picks as soccer_record.all_picks, without its goal and corner
    families, which read every quote and take seconds)."""
    from . import soccer_live, soccer_markets as sm, soccer_record as sr, tickets
    from .coverage import et_date
    if not soccer_live.has_tables(conn):
        return []
    matches = {r["match_id"]: r for r in conn.execute("SELECT match_id, competition_id, home_score, away_score FROM soccer_matches")}
    stored: dict = {}
    for r in conn.execute("SELECT * FROM soccer_live_forecasts ORDER BY match_id, version"):
        stored.setdefault(r["match_id"], []).append(r)
    score = {"home": (1, 0), "draw": (0, 0), "away": (0, 1)}      # these markets settle on the 90-minute outcome alone
    out = []
    for f in soccer_live.forecasts(conn, as_of=as_of):
        mid, kick = f["match_id"], f["kickoff_at"]
        if kick > as_of or not f["generated_at"] < kick:
            continue
        row = next(r for r in stored[mid] if r["forecast_id"] == f["forecast_id"])
        for market in SOCCER_MARKETS:
            if market == "1x2_regulation":
                side, prob = f["most_likely"], f["probabilities"][f["most_likely"]]
                pick = ("Draw" if side == "draw" else f"{f[side]} win") + " (90 min)"
                grade = "pending" if f["result"] is None else "win" if f["result"] == side else "loss"
            else:
                item = sr._derived_item(row, market)
                side, prob = item["side"], item["probability"]
                pick = tickets._soccer_label(market, side, None, f["home"], f["away"])
                grade = "pending" if f["result"] is None else sm.grade_1x2_market(score[f["result"]], market, side)
            out.append({
                "game_id": mid, "market": market, "percentage": to.displayed_percentage(prob), "side": side, "home_line": None,
                "line_source": "none" if market == "1x2_regulation" else "model", "grade": grade, "start_at": kick,
                "game_date": et_date(kick), "forecast_id": f["forecast_id"], "generated_at": f["generated_at"],
                "model_version": f"{row['model']}:{(row['candidate_sha256'] or '')[:12]}", "pick": pick,
                "matchup": f"{f['home']} vs {f['away']}", "home": f["home"], "away": f["away"], "competition_id": matches[mid]["competition_id"],
                "final_score": None if f["result"] is None else f"{f['away']} {matches[mid]['away_score']} - {matches[mid]['home_score']} {f['home']}",
                "versions": [{"forecast_id": i["forecast_id"], "generated_at": i["generated_at"], "home_line": None, "side": i["side"],
                              "percentage": to.displayed_percentage(i["probability"])}
                             for i in (sr._derived_item(v, market) for v in stored[mid]
                                       if v["forecast_id"] != f["forecast_id"] and v["generated_at"] < kick)]})
    return out


# --- Combine ----------------------------------------------------------------------------------------------------------
def _same(row: dict, other: dict) -> bool:
    a, b = row.get("home_line"), other.get("home_line")
    line = a is None and b is None or a is not None and b is not None and abs(a - b) < 1e-9
    return line and row["side"] == other["side"] and row["percentage"] == other["percentage"]


def _overlap(row: dict, live: Optional[dict]) -> dict:
    if live is None:
        return {"status": "none", "live": None, "version_id": None, "grade_conflict": False}
    version = None
    if _same(row, live):
        status = "same"
    else:
        version = next((v for v in live["versions"] if _same(row, v)), None)
        status = "earlier_live_version" if version else "different"
    shown = {k: live[k] for k in ("forecast_id", "generated_at", "model_version", "pick", "percentage", "side", "home_line", "grade")}
    # A different side or line may grade differently without any error; both grades stay shown.
    conflict = row["grade"] in DECISIVE and live["grade"] in DECISIVE and row["grade"] != live["grade"]
    return {"status": status, "live": shown, "version_id": version["forecast_id"] if version else None, "grade_conflict": conflict}


def tally(items: list) -> dict:
    """Counts of predictions. `settled` = wins + losses; pushes, voids and unsettled predictions are in no rate."""
    g = Counter(x["grade"] for x in items)
    w, n = g["win"], g["win"] + g["loss"]
    decided = [float(x["percentage"]) / 100 for x in items if x["grade"] in ("win", "loss")]
    void = sum(g[v] for v in VOID)
    return {"predictions": len(items), "win": w, "loss": g["loss"], "push": g["push"], "void": void, "settled": n,
            "unsettled": len(items) - n - g["push"] - void,
            "win_rate": w / n if n else None, "ci95": to.wilson(w, n), "stated": sum(decided) / n if n else None,
            "expected_wins": sum(decided) if n else None, "small_sample": n < to.MIN_LIVE_GRADED}


def cell_of(b: dict) -> dict:
    """The exact-percentage record of one bucket {recovered: [...], live: [...]}; `combined` counts each game once."""
    return {"recovered": tally(b["recovered"]), "live": tally(b["live"]),
            "combined": tally([r for r in b["recovered"] if r["counted"]] + b["live"])}


def _summary(rec: list, live: list) -> dict:
    overlap = Counter(r["overlap"]["status"] for r in rec)
    counted = [r for r in rec if r["counted"]]
    return {"live": tally(live),
            "recovered": {**tally(rec), "recovered_only": len(counted), "priced": 0, "unpriced": len(rec)},
            "combined": tally(counted + live),
            "overlap": {**{s: overlap[s] for s in OVERLAP_STATES}, "grade_conflicts": sum(r["overlap"]["grade_conflict"] for r in rec)}}


def market_title(sport: str, market: str) -> str:
    if sport == "soccer":
        from . import soccer_markets as sm
        return SOCCER_TITLES.get(market) or sm.TITLES[market]
    from . import markets
    return markets.spec(sport, market).title


def direction(sport: str, market: str, side: str) -> str:
    if sport == "soccer":
        from .soccer_record import DIRECTION
        return DIRECTION.get(market, {}).get(side, side)
    from . import markets
    return markets.SIDE_LABELS[market][("a", "mid", "b").index(side)]


def markets_of(sport: str) -> tuple:
    if sport == "soccer":
        return SOCCER_MARKETS
    from . import sports
    return tuple(sports.NFL.markets if sport == "nfl" else sports.get(sport).markets)


def combine(sport: str, rows: list, live: list, market: Optional[str] = None, date_from: Optional[str] = None,
            date_to: Optional[str] = None, team: Optional[str] = None, competition: Optional[str] = None) -> dict:
    """The recovered rows, annotated against the live items, with deduplicated totals. Overlap is judged on every live item before
    any filter, so a filter never turns a duplicate into a separate prediction."""
    by_key = {(x["game_id"], x["market"]): x for x in live}
    all_rows = []
    for r in rows:
        o = _overlap(r, by_key.get((r["game_id"], r["market"])))
        all_rows.append({**r, "origin": "recovered", "label": LABEL, "game_date": r.get("start_date_local") or r["start_at"][:10],
                         "market_title": market_title(sport, r["market"]), "direction": direction(sport, r["market"], r["side"]),
                         "model_version": None, "price": None, "priced": False, "overlap": o, "counted": o["status"] == "none"})

    def keep(x, use_market=True):
        return ((not use_market or market is None or x["market"] == market)
                and (date_from is None or x["game_date"] >= date_from) and (date_to is None or x["game_date"] <= date_to)
                and (team is None or team.lower() in ((x.get("home") or "").lower(), (x.get("away") or "").lower()))
                and (competition is None or x.get("competition_id") == competition))

    in_scope = [r for r in all_rows if keep(r, False)]
    live_scope = [x for x in live if keep(x, False)]
    shown = sorted((r for r in in_scope if keep(r)), key=lambda r: (r["start_at"], r["game_id"], r["market"]), reverse=True)
    live_shown = [x for x in live_scope if keep(x)]

    buckets: dict = {}
    for kind, items in (("recovered", shown), ("live", live_shown)):
        for x in items:
            b = buckets.setdefault((x["market"], x["percentage"], x["side"], x["line_source"]), {"recovered": [], "live": []})
            b[kind].append(x)
    exact = []
    for (m, pct, side, source), b in buckets.items():
        if b["recovered"]:
            exact.append({"market": m, "market_title": market_title(sport, m), "percentage": pct, "side": side,
                          "direction": direction(sport, m, side), "line_source": source,
                          "overlap": sum(not r["counted"] for r in b["recovered"]), **cell_of(b)})
    exact.sort(key=lambda b: (markets_of(sport).index(b["market"]), -float(b["percentage"]), b["side"], b["line_source"]))
    whole: dict = {}             # a row's own exact-percentage record is never narrowed by a date, team or competition filter
    for kind, items in (("recovered", all_rows), ("live", live)):
        for x in items:
            whole.setdefault((x["market"], x["percentage"], x["side"], x["line_source"]), {"recovered": [], "live": []})[kind].append(x)
    shown = [{**r, "bucket": cell_of(whole[(r["market"], r["percentage"], r["side"], r["line_source"])])} for r in shown]
    blocks = []
    for m in markets_of(sport):
        rec_m, live_m = [r for r in in_scope if r["market"] == m], [x for x in live_scope if x["market"] == m]
        if rec_m or live_m:
            blocks.append({"market": m, "title": market_title(sport, m), **_summary(rec_m, live_m)})
    return {"rows": shown, "summary": _summary(shown, live_shown), "markets": blocks, "exact_percentages": exact,
            "live_rows": sorted(({k: v for k, v in x.items() if k != "versions"} for x in live_shown),
                                key=lambda x: (x["start_at"], x["game_id"], x["market"]), reverse=True)}


def report(sport: str, live: list, teams: Optional[dict] = None, **filters) -> dict:
    """The endpoint body. `teams` {game_id: {home, away[, competition_id]}} from the store, for the team and competition filters."""
    body, why = load()
    rows = [] if body is None else [r for r in body["rows"] if r["sport"] == sport]
    reason = why or (None if rows else "no_rows_for_sport")
    base = {"sport": sport, "available": reason is None, "reason": reason, "label": LABEL, "note": NOTE,
            "period": None, "reconciliation": None, "not_predictions": [], "filters": filters}
    if reason:
        out = combine(sport, [], live, **filters)
        out["summary"] = {**out["summary"], "recovered": None}
        return {**base, **out}
    dates = sorted({r["start_date_local"] for r in rows if r.get("start_date_local")})
    out = combine(sport, [{**r, **(teams or {}).get(r["game_id"], {})} for r in rows], live, **filters)
    everything = combine(sport, rows, live)["summary"]
    recon = dict((body.get("reconciliation") or {}).get(sport) or {})
    recon.update(overlap=everything["overlap"], counted_recovered=everything["recovered"]["recovered_only"],
                 unsettled=everything["recovered"]["unsettled"], disagreements=sum(
                     1 for d in body.get("disagreements") or [] if d["sport"] == sport),
                 excluded_rows=[e for e in body.get("excluded") or [] if e["sport"] == sport])
    return {**base, "period": {"from": dates[0], "to": dates[-1]} if dates else None, "reconciliation": recon,
            "not_predictions": [x for x in body.get("not_predictions") or [] if x["sport"] == sport], **out}


def point_teams(conn) -> dict:
    return {r[0]: {"home": r[1], "away": r[2]} for r in conn.execute(
        "SELECT g.game_id, h.abbr, a.abbr FROM games g JOIN teams h ON h.team_id=g.home_team_id JOIN teams a ON a.team_id=g.away_team_id "
        "WHERE g.tipoff_at >= '2026-11-01'")}


def soccer_teams(conn) -> dict:
    return {r[0]: {"home": r[1], "away": r[2], "competition_id": r[3]} for r in conn.execute(
        "SELECT m.match_id, h.name, a.name, m.competition_id FROM soccer_matches m JOIN teams h ON h.team_id=m.home_team_id "
        "JOIN teams a ON a.team_id=m.away_team_id WHERE m.kickoff_at >= '2026-11-01'")}
