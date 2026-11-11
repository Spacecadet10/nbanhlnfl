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
             "open→now " + _field(r, "open", "no move (no own quote)" if move is None else f"{move:+.1f} pts"),
             "spread on winner " + _field(r, "spread", {1: "yes", 0: "no"}.get(r.get("spread_on_winner"), "missing")),
             _field(r, "rest", rest),
             "lineup flags " + _field(r, "lineup", str(flags)),
             "DRatings home " + (pct(dr) if dr is not None else "n/a" if r.get("market") != "winner"
                                 or r.get("sport") not in ("nba", "nhl") else "missing"),
             "card: " + _field(r, "card", _others(r))]
    return " | ".join(parts)


def _record(rows):
    w = sum(1 for r in rows if r["grade"] == "win")
    return w, len(rows) - w


def _line(label, a, b, name_a, name_b):
    """'label: name_a xW-yL (n=..) vs name_b ... ' with 'too few to tell' when either side has fewer than MIN_N."""
    (wa, la), (wb, lb) = _record(a), _record(b)
    na, nb = wa + la, wb + lb
    rate = lambda w, n: f"{100 * w / n:.1f}%" if n else "—"   # noqa: E731
    verdict = "too few to tell" if min(na, nb) < MIN_N else f"difference {100 * (wa / na - wb / nb):+.1f} pts (descriptive)"
    return (f"  {label}: {name_a} {wa}W-{la}L {rate(wa, na)} (n={na}) vs {name_b} {wb}W-{lb}L {rate(wb, nb)} (n={nb}) "
            f"-> {verdict}")


FACTORS = (
    ("sportsbook % above app %", lambda r: None if r.get("book_devig") is None or r.get("confidence") is None else r["book_devig"] > r["confidence"],
     "book above", "book at/below"),
    ("move since open", lambda r: None if r.get("move_pts") is None or r["move_pts"] == 0 else r["move_pts"] > 0,
     "toward pick", "away from pick"),
    ("spread pick on the team picked to win", lambda r: None if r.get("spread_on_winner") is None else bool(r["spread_on_winner"]),
     "same team", "other team"),
    ("either team on a back-to-back", lambda r: None if r.get("home_b2b") is None or r.get("away_b2b") is None
     else bool(r["home_b2b"] or r["away_b2b"]), "yes", "no"),
    ("lineup flags on the pick", lambda r: None if r.get("lineup_state") != "present" else (r.get("lineup_flag_count") or 0) > 0,
     "flagged", "no flags"),
)


def _split(rows, fn):
    a, b, unknown = [], [], 0
    for r in rows:
        v = fn(r)
        if v is None:
            unknown += 1
        else:
            (a if v else b).append(r)
    return a, b, unknown


def render(rows):
    """rows: graded canonical picks (dicts with the scorecard columns, 'grade' in win/loss)."""
    out = [f"Scorecard report — canonical picks graded win/loss: n={len(rows)}. Exact displayed % (one decimal), no ranges. "
           f"'too few to tell' below n={MIN_N} on either side. Descriptive only: not a finding until it holds on new games.", ""]
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
    for label, fn, ya, yb in FACTORS:
        a, b, unknown = _split(flat, fn)
        out.append(_line(label, a, b, ya, yb) + f" [not known: {unknown}]")
    out.append("\n3. Per-factor comparison, all graded canonical picks, by sport and market:")
    by = defaultdict(list)
    for r in rows:
        by[(r["sport"], r["market"])].append(r)
    for (sport, market), rs in sorted(by.items()):
        w, l = _record(rs)
        out.append(f"{sport.upper()} {market}: {w}W-{l}L (n={w + l})")
        for label, fn, ya, yb in FACTORS:
            a, b, unknown = _split(rs, fn)
            if not a and not b:
                out.append(f"  {label}: not known for any pick (n=0) [not known: {unknown}]")
                continue
            out.append(_line(label, a, b, ya, yb) + f" [not known: {unknown}]")
    return "\n".join(out) + "\n"


def _mean_book(rs, grade):
    xs = [r["book_devig"] for r in rs if r["grade"] == grade and r.get("book_devig") is not None]
    return sum(xs) / len(xs) if xs else None


def graded_rows(data_dir: Path, sport: str, as_of: str) -> list:
    """The scorecard rows of canonical picks graded win/loss as of `as_of` (both files opened read-only)."""
    from app import coverage, soccer_live
    card_path = data_dir / "scorecard" / f"{sport}.sqlite"
    store = data_dir / ("nba.sqlite" if sport == "nba" else f"{sport}.sqlite")
    if not card_path.is_file() or not store.is_file():
        return []
    card = sqlite3.connect(f"file:{card_path}?mode=ro", uri=True)
    card.row_factory = sqlite3.Row
    conn = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = {r["forecast_id"]: dict(r) for r in card.execute(
        "SELECT s.*, d.dr_p_home, d.captured_at AS dr_captured_at FROM scorecard s "
        "LEFT JOIN scorecard_dratings d USING (forecast_id)")}
    grades = {}
    if sport == "soccer":
        for f in conn.execute(soccer_live.LATEST_SQL):
            if f["forecast_id"] in rows and f["generated_at"] <= as_of:
                grades[f["forecast_id"]] = soccer_live.grade(conn, f["forecast_id"], rows[f["forecast_id"]]["side"], as_of)["grade"]
    else:
        ids = sorted({r["game_id"] for r in rows.values()})
        games = [g for i in range(0, len(ids), 500) for g in conn.execute(
            f"SELECT * FROM games WHERE game_id IN ({','.join('?' * len(ids[i:i + 500]))})", ids[i:i + 500])]
        games = [g for g in games if g["season_type"] in coverage.COMPETITIVE]
        for market in sorted({r["market"] for r in rows.values()}):
            for gid, c in coverage.canonical(conn, games, market, as_of).items():
                if c["state"] == "forecast":
                    grades[c["record"]["forecast_id"]] = c["grade"]
    return [{**rows[f], "grade": g} for f, g in grades.items() if g in ("win", "loss") and f in rows]


def main(argv=None) -> int:
    from app import config
    from app.db import utc_now
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sport", choices=SPORTS, action="append")
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    as_of = a.as_of or utc_now()
    rows = [r for s in (a.sport or SPORTS) for r in graded_rows(config.data_dir(), s, as_of)]
    text = f"as of {as_of}\n" + render(rows)
    if a.out:
        Path(a.out).write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
