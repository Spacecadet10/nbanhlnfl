"""Same exact percentage, won versus lost: what was different? (owner request, Nov 11 2026). READ-ONLY.

Reads the scorecard files (<data>/scorecard/<sport>.sqlite, app/scorecard.py) and the sport stores, both opened read-only.
Only canonical picks are reported (app/coverage.canonical: the latest forecast before the start of a competitive game;
soccer: the latest version of the 90-minute forecast), graded on read from the store as of --as-of (results are never
copied into the scorecard). Percentages are the exact displayed percentage to one decimal (threshold_overview
.displayed_percentage); nothing is grouped into ranges. Every count carries its n; a comparison where either side has fewer
than MIN_N picks says "too few to tell". Descriptive only: nothing here is a finding until it holds on new games.

  NBA_DATA_DIR=<copy> python scripts/scorecard_report.py [--sport nba ...] [--as-of 2026-11-11T22:00:00Z] [--out report.txt]
"""
import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

MIN_N = 30          # threshold_overview.MIN_LIVE_GRADED: a live row is judged only from 30 settled picks
SPORTS = ("nba", "nhl", "nfl", "soccer")


def pct(x):
    return "missing" if x is None else f"{100 * x:.1f}%"


def _field(r, group, text):
    state = r.get(f"{group}_state")
    if state == "not_applicable":
        return "n/a"
    if state == "missing":
        return "missing"
    return text


def _others(r):
    try:
        others = json.loads(r.get("card_others") or "[]")
    except ValueError:
        return "?"
    return ", ".join(f"{o.get('market', o.get('family'))} {o.get('pick_label', '')} {o.get('displayed_pct', '')}".strip()
                     for o in others) or "none"


def describe(r):
    """One occurrence's full picture in one line."""
    move = r.get("move_pts")
    rest = (f"rest H {r.get('home_rest_days')} / A {r.get('away_rest_days')}"
            + (" (back-to-back: " + ", ".join(s for s in ("home", "away") if r.get(f"{s}_b2b")) + ")"
               if r.get("home_b2b") or r.get("away_b2b") else ""))
    flags = r.get("lineup_flag_count")
    dr = r.get("dr_p_home")
    parts = [f"{r['grade'].upper():4s} {r.get('game_date')} {r.get('matchup')}: {r.get('pick_label')} (side {r.get('side')})",
             "book " + _field(r, "book", f"{pct(r.get('book_devig'))} at {r.get('book_price')}"),
             "move since open " + _field(r, "open", "not known (no quote at the pick's line)" if move is None
                                         else "no move" if move == 0 else f"{move:+.1f} pts"),
             "spread on winner " + _field(r, "spread", {1: "yes", 0: "no"}.get(r.get("spread_on_winner"), "missing")),
             _field(r, "rest", rest),
             "lineup flags " + _field(r, "lineup", str(flags)),
             "DRatings home " + (pct(dr) if dr is not None else "n/a" if r.get("market") != "winner"
                                 else "not collected" if r.get("sport") not in ("nba", "nhl") else "missing"),
             "card: " + _field(r, "card", _others(r))]
    return " | ".join(parts)


def _record(rows):
    w = sum(1 for r in rows if r["grade"] == "win")
    return w, len(rows) - w


def _rate(w, n):
    return f"{100 * w / n:.1f}%" if n else "—"


def _line(label, groups):
    """'label: name xW-yL rate (n=..) vs ...'; 'too few to tell' when any compared group has fewer than MIN_N."""
    parts, ns = [], []
    for name, rs in groups:
        w, l = _record(rs)
        ns.append(w + l)
        parts.append(f"{name} {w}W-{l}L {_rate(w, w + l)} (n={w + l})")
    verdict = "too few to tell" if min(ns) < MIN_N else "descriptive only"
    return f"  {label}: " + " vs ".join(parts) + f" -> {verdict}"


INJURY_WORDS = ("top player", "backup goalie")


def _lineup_kind(r):
    """'injury/backup' (a top player listed out/doubtful/questionable, or a backup goalie), 'unconfirmed/unknown' (only
    flags about what was not known: no report, not submitted, goalie not confirmed, No.1 unknown, unmatched names),
    'no flags', or None when the lineup facts were not available."""
    if r.get("lineup_state") != "present":
        return None
    try:
        flags = json.loads(r.get("lineup_flags") or "[]")
    except ValueError:
        return None
    if not flags:
        return "no flags"
    return "injury/backup" if any(w in f for f in flags for w in INJURY_WORDS) else "unconfirmed/unknown"


def _move_kind(r):
    m = r.get("move_pts")
    return None if m is None else "toward pick" if m > 0 else "away from pick" if m < 0 else "no move"


FACTORS = (
    ("sportsbook % above app %", lambda r: None if r.get("book_devig") is None or r.get("confidence") is None
     else "book above" if r["book_devig"] > r["confidence"] else "book at/below", ("book above", "book at/below")),
    ("move since open (same book, same line)", _move_kind, ("toward pick", "away from pick", "no move")),
    ("spread pick on the team picked to win", lambda r: None if r.get("spread_on_winner") is None
     else "same team" if r["spread_on_winner"] else "other team", ("same team", "other team")),
    ("either team on a back-to-back", lambda r: None if r.get("home_b2b") is None or r.get("away_b2b") is None
     else "yes" if (r["home_b2b"] or r["away_b2b"]) else "no", ("yes", "no")),
    ("lineup flags on the pick", _lineup_kind, ("injury/backup", "unconfirmed/unknown", "no flags")),
)


def _factor(label, fn, kinds, rows):
    split = {k: [] for k in kinds}
    unknown = 0
    for r in rows:
        k = fn(r)
        if k is None:
            unknown += 1
        else:
            split[k].append(r)
    if not any(split.values()):
        return f"  {label}: not known for any pick (n=0) [not known: {unknown}]"
    return _line(label, [(k, split[k]) for k in kinds]) + f" [not known: {unknown}]"


def render(rows, without_row=None):
    """rows: graded canonical picks (dicts with the scorecard columns, 'grade' in win/loss). without_row: {(sport,
    market): graded canonical picks that have no scorecard row} (counted, never guessed)."""
    out = [f"Scorecard report — canonical picks graded win/loss: n={len(rows)}. Exact displayed % (one decimal), no ranges. "
           f"'too few to tell' below n={MIN_N} in any compared group. Descriptive only: not a finding until it holds on new games."]
    missing = {k: v for k, v in (without_row or {}).items() if v}
    out.append("canonical picks without a scorecard row: " + (", ".join(f"{s} {m} {n}" for (s, m), n in sorted(missing.items()))
                                                              if missing else "none"))
    out.append("")
    groups = defaultdict(list)
    for r in rows:
        groups[(r["sport"], r["market"], r["displayed_pct"])].append(r)
    both = {k: v for k, v in groups.items() if {"win", "loss"} <= {r["grade"] for r in v}}
    out.append(f"1. Same exact percentage, won versus lost: {len(both)} percentages (of {len(groups)}) have both.")
    for (sport, market, p), rs in sorted(both.items(), key=lambda kv: (kv[0][0], kv[0][1], float(kv[0][2]))):
        w, l = _record(rs)
        out.append(f"\n{sport.upper()} {market} {p}%: won {w}, lost {l} (n={w + l})")
        for r in sorted(rs, key=lambda r: (r["grade"] != "win", r.get("game_date") or "")):
            out.append("  " + describe(r))
    paired = [rs for rs in both.values()]
    flat = [r for rs in paired for r in rs]
    out.append("\n2. What was different (only the percentages above, every occurrence; won vs lost):")
    higher = sum(1 for rs in paired if _mean_book(rs, "win") is not None and _mean_book(rs, "loss") is not None
                 and _mean_book(rs, "win") > _mean_book(rs, "loss"))
    comparable = sum(1 for rs in paired if _mean_book(rs, "win") is not None and _mean_book(rs, "loss") is not None)
    out.append(f"  winners had the higher sportsbook % in {higher} of {comparable} percentages with a sportsbook % on both "
               f"sides (n={comparable}{'; too few to tell' if comparable < MIN_N else ''})")
    for label, fn, kinds in FACTORS:
        out.append(_factor(label, fn, kinds, flat))
    out.append("\n3. Per-factor comparison, all graded canonical picks, by sport and market:")
    by = defaultdict(list)
    for r in rows:
        by[(r["sport"], r["market"])].append(r)
    for (sport, market), rs in sorted(by.items()):
        w, l = _record(rs)
        out.append(f"{sport.upper()} {market}: {w}W-{l}L (n={w + l})")
        for label, fn, kinds in FACTORS:
            out.append(_factor(label, fn, kinds, rs))
    return "\n".join(out) + "\n"


def _mean_book(rs, grade):
    xs = [r["book_devig"] for r in rs if r["grade"] == grade and r.get("book_devig") is not None]
    return sum(xs) / len(xs) if xs else None


def graded_rows(data_dir: Path, sport: str, as_of: str) -> tuple:
    """(scorecard rows of canonical picks graded win/loss as of `as_of`, {(sport, market): graded canonical picks with no
    scorecard row}). Both files are opened read-only."""
    from app import config, coverage, soccer_live, soccer_markets_live as sml
    card_path = data_dir / "scorecard" / f"{sport}.sqlite"
    store = Path(str(config.db_path(sport)).replace(str(config.data_dir()), str(data_dir), 1))
    if not store.is_file():
        return [], {}
    conn = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows, sel_rows = {}, defaultdict(list)
    if card_path.is_file():
        card = sqlite3.connect(f"file:{card_path}?mode=ro", uri=True)
        card.row_factory = sqlite3.Row
        rows = {r["forecast_id"]: dict(r) for r in card.execute(
            "SELECT s.*, d.dr_p_home, d.captured_at AS dr_captured_at FROM scorecard s "
            "LEFT JOIN scorecard_dratings d USING (forecast_id)")}
        if card.execute("SELECT 1 FROM sqlite_master WHERE name='scorecard_soccer_markets'").fetchone():
            for r in card.execute("SELECT * FROM scorecard_soccer_markets"):
                sel_rows[r["forecast_id"]].append(dict(r))
    out, without = [], defaultdict(int)

    def keep(row, grade, market):
        if grade not in ("win", "loss"):
            return
        if row is None:
            without[(sport, market)] += 1
        else:
            out.append({**row, "grade": grade})

    if sport == "soccer":
        if soccer_live.has_tables(conn):
            for f in conn.execute(soccer_live.LATEST_SQL):
                if f["generated_at"] > as_of:
                    continue
                p = (f["p_home"], f["p_draw"], f["p_away"])
                side = ("home", "draw", "away")[max(range(3), key=lambda k: p[k])]
                keep(rows.get(f["forecast_id"]), soccer_live.grade(conn, f["forecast_id"], side, as_of)["grade"], f["market"])
        if sml.has_tables(conn):
            for f in conn.execute(sml.LATEST):
                if f["generated_at"] > as_of:
                    continue
                if not sel_rows.get(f["forecast_id"]):
                    without[(sport, f["family"])] += 1
                for r in sel_rows.get(f["forecast_id"], []):
                    keep(r, sml.grade(conn, f["forecast_id"], r["market"], r["side"], r["line"], as_of)["grade"], r["market"])
        return out, dict(without)
    games = [g for g in conn.execute("SELECT * FROM games WHERE season_type IN (%s)" % ",".join("?" * len(coverage.COMPETITIVE)),
                                     coverage.COMPETITIVE)
             if g["tipoff_at"] <= as_of]
    began = coverage.recording_began(conn, sport)
    games = [g for g in games if began and g["tipoff_at"] > began]
    markets = [r[0] for r in conn.execute("SELECT DISTINCT market FROM game_forecasts")] if coverage._has_table(conn, "game_forecasts") else []
    for market in sorted(markets):
        for gid, c in coverage.canonical(conn, games, market, as_of, began).items():
            if c["state"] == "forecast":
                keep(rows.get(c["record"]["forecast_id"]), c["grade"], market)
    return out, dict(without)


def main(argv=None) -> int:
    from app import config
    from app.db import utc_now
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sport", choices=SPORTS, action="append")
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    as_of = a.as_of or utc_now()
    rows, without = [], {}
    for sport in a.sport or SPORTS:
        got, miss = graded_rows(config.data_dir(), sport, as_of)
        rows += got
        without.update(miss)
    text = f"as of {as_of}\n" + render(rows, without)
    if a.out:
        Path(a.out).write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
