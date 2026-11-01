"""Frozen paper rules (reference/paper_rules.json): no money, read-only. Each rule bets 1 unit on a canonical forecast's
pick (or the opposite side) when its model probability reaches the rule's threshold, at the bookmaker's price the
forecast could see (paper_trading._quote: the latest 'current' quote at the same line retrieved at or before the
forecast). Only canonical forecasts generated at or after the file's frozen_at count, regular season only.

The file is sealed: FROZEN_SHA256 pins its bytes (test_paper_rules), so a rule cannot change mid-season. The verdict is
the file's verdict_rule, applied only once the sport's regular season has no scheduled game left; every earlier look is
"interim" and never a verdict."""
import hashlib
import json
from pathlib import Path
from typing import Optional

from . import backtest, coverage
from .paper_trading import _decimal, _quote

PATH = Path(__file__).parent / "reference" / "paper_rules.json"
FROZEN_SHA256 = "81d689f5f6c905652815afa5c9804dda9a65a9f61516c5cdb37b22c87d79902c"


def load() -> dict:
    raw = PATH.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != FROZEN_SHA256:
        raise ValueError(f"paper_rules.json changed after it was frozen (sha256 {digest[:12]}…); a new rule needs a new file")
    return json.loads(raw)


def _flip(grade: str) -> str:
    return {"win": "loss", "loss": "win"}.get(grade, grade)


def bets(conn, rule: dict, spec: dict, now: str) -> tuple[list, dict]:
    """(bets, skipped counts) of one rule: one bet per qualifying game, settled from the canonical grade."""
    began = coverage.recording_began(conn, rule["sport"])
    games = conn.execute("SELECT * FROM games WHERE tipoff_at >= ? AND tipoff_at <= ? ORDER BY tipoff_at, game_id",
                         (spec["frozen_at"], now)).fetchall()
    games = [g for g in games if g["season_type"] in spec["season_types"]]
    canon = coverage.canonical(conn, games, rule["market"], now, began)
    out, skipped = [], {"below threshold": 0, "no forecast": 0, "made before the freeze": 0, "not at the bookmaker's line": 0,
                        "no bookmaker price": 0}
    for g in games:
        c = canon[g["game_id"]]
        if c["state"] != "forecast":
            skipped["no forecast"] += 1
            continue
        rec = c["record"]
        if rec["generated_at"] < spec["frozen_at"]:
            skipped["made before the freeze"] += 1
            continue
        if rec["confidence"] < rule["min_confidence"] - 1e-9:
            skipped["below threshold"] += 1
            continue
        if rule["market"] != "winner" and rec["line_source"] != "bookmaker":
            skipped["not at the bookmaker's line"] += 1
            continue
        side = rec["side"] if rule["side"] == "pick" else ("b" if rec["side"] == "a" else "a")
        q = _quote(conn, g["game_id"], rule["market"], rec["line"], rec["generated_at"])
        dec = None if q is None else _decimal(q[0] if side == "a" else q[1])
        if dec is None:
            skipped["no bookmaker price"] += 1
            continue
        grade = c["grade"] if rule["side"] == "pick" else _flip(c["grade"])
        profit = dec - 1 if grade == "win" else -1.0 if grade == "loss" else 0.0 if grade == "push" else None
        out.append({"game_id": g["game_id"], "tipoff": g["tipoff_at"], "side": side, "line": rec["line"],
                    "confidence": rec["confidence"], "price": dec, "grade": grade, "profit": profit})
    return out, skipped


def _season_over(conn, spec: dict) -> bool:
    left = conn.execute("SELECT COUNT(*) FROM games WHERE season_type = 'regular' AND status IN ('scheduled', 'in_progress') "
                        "AND tipoff_at >= ?", (spec["frozen_at"],)).fetchone()[0]
    played = conn.execute("SELECT COUNT(*) FROM games WHERE season_type = 'regular' AND tipoff_at >= ?",
                          (spec["frozen_at"],)).fetchone()[0]
    return played > 0 and left == 0


def verdict(settled: list, min_bets: int, final: bool) -> tuple[str, Optional[dict]]:
    """('interim' | 'pass' | 'fail' | 'inconclusive', bootstrap of the return per bet or None)."""
    boot = backtest.block_bootstrap([b["profit"] for b in settled], [b["tipoff"] for b in settled]) if len(settled) >= 2 else None
    if not final:
        return "interim", boot
    if len(settled) < min_bets:
        return "inconclusive", boot
    return ("pass" if boot["ci95"][0] > 0 else "fail"), boot


def report(conn, sport: str, now: str) -> dict:
    spec = load()
    rules = []
    final = _season_over(conn, spec)
    for rule in [r for r in spec["rules"] if r["sport"] == sport]:
        rows, skipped = bets(conn, rule, spec, now)
        settled = [b for b in rows if b["profit"] is not None]
        state, boot = verdict(settled, spec["min_bets"], final)
        rules.append({**rule, "bets": len(rows), "settled": len(settled),
                      "wins": sum(b["grade"] == "win" for b in settled), "losses": sum(b["grade"] == "loss" for b in settled),
                      "pushes": sum(b["grade"] == "push" for b in settled), "pending": len(rows) - len(settled),
                      "units": round(sum(b["profit"] for b in settled), 4) if settled else 0.0,
                      "return_per_bet": boot["mean"] if boot else None, "ci95": boot["ci95"] if boot else None,
                      "state": state, "skipped": skipped, "recent": rows[-10:][::-1]})
    return {"sport": sport, "as_of": now, "frozen_at": spec["frozen_at"], "sha256": FROZEN_SHA256[:16],
            "verdict_rule": spec["verdict_rule"], "min_bets": spec["min_bets"], "note": spec["note"], "rules": rules}
