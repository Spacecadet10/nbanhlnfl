"""The colour a pick had BEFORE its start, saved once (docs/PICK_COLOURS.md).

A pick's colour is the settled record of its exact-% bucket (threshold_overview.bucket_colour). That record changes with every
result, so a colour read today says nothing about what a card showed. This module keeps what was shown, in an append-only file
of its own, <data>/pick_snapshots.sqlite (never the forecast stores, never the delivery ledger):

- event 'seen': written by the Telegram run (telegram.run_once, about once a minute) for every pregame pick of its report, sent
  or not. The first row of a forecast is version 1 = its original colour; a later run writes another version only when the
  shown record, selection, recommendation or start changed. Refreshes write nothing.
- event 'sent': one row per (delivery, market), only after the delivery's status is 'sent'.
- basis 'live' = observed by this recorder; 'card_text' = read from a saved, confirmed Telegram card by `backfill`, only when the
  text itself proves it (see card_evidence). Nothing is ever derived from today's bucket record.

Counting (`classify`): one prediction per game and market, the forecast of record (the latest pregame forecast, as on every
other page) with ITS OWN original colour; if that forecast was never saved its colour is unknown, never an earlier forecast's.
The original side and line are graded, on read, with the app's own settlement. Nothing here changes a forecast, a colour rule,
a recommendation, a delivery key or a card, and a failure here never reaches the sender.
"""
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from html import unescape
from typing import Callable, Optional

from . import config, published_picks as pp, threshold_overview as to
from .coverage import et_date

RULE_LIVE_SINCE = "2026-11-22T17:15:41Z"     # the first card carrying bucket-record-v1's legend; earlier marks used another rule
PRICE_MAX_AGE = timedelta(minutes=20)        # the cards' own "odds unavailable" limit (telegram.STALE_ODDS)
FMT = "%Y-%m-%dT%H:%M:%SZ"
VIEWS = ("all", "recommended", "sent")
COLOURS = ("green", "red", "neutral", "no_record", "unknown")

SCHEMA = """
CREATE TABLE IF NOT EXISTS pick_snapshots (
  snapshot_id INTEGER PRIMARY KEY,
  event TEXT NOT NULL CHECK (event IN ('seen', 'sent')),
  sport TEXT NOT NULL, game_id TEXT NOT NULL, market TEXT NOT NULL,
  forecast_key TEXT NOT NULL,            -- the stored forecast row (soccer: the row behind the selection)
  forecast_id TEXT NOT NULL,             -- the pick's id as shown
  version_no INTEGER,                    -- seen only: 1 = the original
  delivery_key TEXT, message_id INTEGER, -- sent only
  observed_at TEXT NOT NULL, start_at TEXT NOT NULL, generated_at TEXT NOT NULL,
  matchup TEXT, pick_label TEXT, side TEXT NOT NULL, line REAL, line_source TEXT,
  probability REAL NOT NULL, percentage TEXT NOT NULL, model_version TEXT, source TEXT,
  colour TEXT CHECK (colour IN ('green', 'red', 'neutral')), colour_rule TEXT NOT NULL,
  wins INTEGER, losses INTEGER, pushes INTEGER, voids INTEGER,                      -- live record
  recovered_colour TEXT CHECK (recovered_colour IN ('green', 'red', 'neutral')), recovered_reason TEXT,
  recovered_wins INTEGER, recovered_losses INTEGER, recovered_pushes INTEGER, recovered_voids INTEGER, recovered_source TEXT,
  past_colour TEXT, past_wins INTEGER, past_losses INTEGER,                         -- recomputed past seasons (no view counts it)
  on_card INTEGER, recommended INTEGER,
  price_american INTEGER, price_provider TEXT, price_quote_id INTEGER, price_retrieved_at TEXT, price_reason TEXT,
  basis TEXT NOT NULL CHECK (basis IN ('live', 'card_text')), provenance TEXT, code_commit TEXT, recorded_at TEXT NOT NULL,
  CHECK (generated_at <= observed_at AND observed_at < start_at),
  CHECK ((event = 'seen') = (version_no IS NOT NULL)), CHECK ((event = 'sent') = (delivery_key IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS pick_snapshots_seen ON pick_snapshots(sport, game_id, market, forecast_key, version_no) WHERE event = 'seen';
CREATE UNIQUE INDEX IF NOT EXISTS pick_snapshots_sent ON pick_snapshots(delivery_key, market) WHERE event = 'sent';
CREATE TRIGGER IF NOT EXISTS pick_snapshots_no_update BEFORE UPDATE ON pick_snapshots
BEGIN SELECT RAISE(ABORT, 'pick_snapshots is append-only'); END;
CREATE TRIGGER IF NOT EXISTS pick_snapshots_no_delete BEFORE DELETE ON pick_snapshots
BEGIN SELECT RAISE(ABORT, 'pick_snapshots is append-only'); END;
CREATE TRIGGER IF NOT EXISTS pick_snapshots_no_replace BEFORE INSERT ON pick_snapshots
WHEN EXISTS (SELECT 1 FROM pick_snapshots WHERE snapshot_id = NEW.snapshot_id
   OR (NEW.event = 'seen' AND event = 'seen' AND sport = NEW.sport AND game_id = NEW.game_id AND market = NEW.market
       AND forecast_key = NEW.forecast_key AND version_no = NEW.version_no)
   OR (NEW.event = 'sent' AND event = 'sent' AND delivery_key = NEW.delivery_key AND market = NEW.market))
BEGIN SELECT RAISE(ABORT, 'pick_snapshots is append-only: conflicting insert rejected'); END;
"""

# What makes a later observation a new version. A price alone never does (it moves every few minutes).
VERSIONED = ("side", "line", "percentage", "start_at", "colour", "colour_rule", "wins", "losses", "pushes", "voids",
             "recovered_colour", "recovered_reason", "recovered_wins", "recovered_losses", "recovered_pushes", "recovered_voids",
             "past_colour", "past_wins", "past_losses", "on_card", "recommended")


# --- The file ----------------------------------------------------------------------------------------------------------
def path():
    return config.data_dir() / "pick_snapshots.sqlite"


def _connect(write: bool = False):
    if not write:
        conn = sqlite3.connect(f"file:{path()}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn
    from . import release
    if config.protected_data_dir(config.data_dir()) and not release.enabled():
        raise PermissionError("only the release environment records into a live data folder")
    path().parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path(), timeout=2, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def load(sport: str) -> list:
    """Every snapshot of the sport, oldest first; [] when the file does not exist yet. An unreadable file raises."""
    if not path().is_file():
        return []
    conn = _connect()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM pick_snapshots WHERE sport = ? ORDER BY observed_at, basis = 'card_text', "
                                              "event = 'sent', snapshot_id", (sport,))]
    finally:
        conn.close()


@lru_cache(maxsize=1)
def _commit() -> Optional[str]:
    from . import release
    try:
        return release._git(release.code_root(), "rev-parse", "HEAD").strip()
    except Exception:
        return None


# --- Recording ---------------------------------------------------------------------------------------------------------
def _dump(x):
    return x.model_dump(mode="json") if hasattr(x, "model_dump") else x


def _t(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def forecast_key(sport: str, pick_or_id) -> str:
    """The stored forecast row of a pick: its number for soccer selections (their id also names the side and line)."""
    if sport != "soccer":
        return str(pick_or_id["forecast_id"] if isinstance(pick_or_id, dict) else pick_or_id)
    raw = (pick_or_id.get("row_id") or pick_or_id["forecast_id"]) if isinstance(pick_or_id, dict) else pick_or_id
    rid = pp.row_id(raw)
    return str(raw if rid is None else rid)


def card_choices(report: dict, picks: list) -> dict:
    """{game_id: tickets.card_choice result} for the games in `picks`, in the card's own market order. A game whose choice cannot
    be read is absent. The one place the card rule is applied to a report: history, Telegram and the NFL page all use it."""
    from . import sports, tickets
    sport, out, games = report["sport"], {}, {}
    for p in picks:
        games.setdefault(p["game_id"], []).append(p)
    for gid, ps in games.items():
        order = (list(dict.fromkeys(p["market"] for p in ps)) if sport == "soccer" else list(sports.store(sport).markets))
        ps = sorted(ps, key=lambda p: order.index(p["market"]) if p["market"] in order else len(order))
        try:
            out[gid] = tickets.card_choice(sport, ps)
        except Exception:
            pass                               # not known: saved as NULL, never as "not recommended"
    return out


def _recommended(report: dict, picks: list) -> dict:
    """{game_id: (market, forecast_id) its card recommends, ('none',) without one} for the games that have a card now (the card's own
    rule and order). A game whose choice cannot be read is absent."""
    out = {}
    for gid, choice in card_choices(report, picks).items():
        chosen = choice["pick"]
        out[gid] = ("none",) if chosen is None else (chosen["market"], str(chosen["forecast_id"]))
    return out


def price_at(store, sport: str, pick: dict, side: str, line: Optional[float], at: str) -> dict:
    """`_price_at`, where any problem reading a quote leaves the pick unpriced and costs it nothing else."""
    try:
        return _price_at(store, sport, pick, side, line, at)
    except Exception:
        return {"price_american": None, "price_provider": None, "price_quote_id": None, "price_retrieved_at": None,
                "price_reason": "quotes_unreadable"}


def _price_at(store, sport: str, pick: dict, side: str, line: Optional[float], at: str) -> dict:
    """The price of exactly this side and line that could be taken at `at`: a stored sportsbook price, 'current', retrieved at or
    before `at` and at most 20 minutes earlier, before the start. Derived prices (DC/DNB from 1X2) are never prices."""
    out = {"price_american": None, "price_provider": None, "price_quote_id": None, "price_retrieved_at": None, "price_reason": None}
    if store is None:
        return {**out, "price_reason": "store_unavailable"}
    from . import odds, soccer_book, soccer_markets_live as sml
    gid, market, start = pick["game_id"], pick["market"], pick["tipoff"]
    if sport == "soccer":
        if market == "1x2_regulation":
            q = soccer_book.latest_1x2(store, gid, at, start)
            found = q and (q["price"][side], q["provider"], q["quote_id"], q["side_retrieved_at"][side])
        elif market == "goals_total":
            qs = [q for q in sml.quotes(store, gid, at) if q["market"] == "total_goals" and q["side"] == side and q["line"] == line]
            q = max(qs, key=lambda x: (x["retrieved_at"], x["quote_id"]), default=None)
            found = q and (q["american"], q["provider"], q["quote_id"], q["retrieved_at"])
        else:
            return {**out, "price_reason": "no_price_is_collected_for_this_market"}
    elif sport == "nba" and market == "winner":
        q = odds.winner_quote(store, sport, gid, at)
        found = q and q["price_label"] == "current" and (q["price_home"] if side == "a" else q["price_away"], q["provider"], q["quote_id"],
                                                         q["retrieved_at"])
    else:
        newest: dict = {}                  # each book's newest price by `at`: a line it has left is no longer on offer
        listed = False
        for q in store.execute("SELECT * FROM market_quotes WHERE game_id = ? AND market = ? AND price_label = 'current' AND retrieved_at <= ? "
                               f"AND retrieved_at < ? ORDER BY {odds._BOOK_ORDER}", (gid, market, at, start, None)):
            listed = listed or q["line"] == line
            newest.setdefault(odds.provider_key(q["provider"]), q)
        q = next((q for q in newest.values() if q["line"] == line), None)
        if q is None and listed:
            return {**out, "price_reason": "line_moved"}
        found = q is not None and ({"a": q["price_a"], "mid": q["price_mid"], "b": q["price_b"]}.get(side), q["provider"], q["quote_id"],
                                   q["retrieved_at"])
    if not found or found[0] is None or abs(found[0]) < 100 or not found[3] < start:
        return {**out, "price_reason": "no_quote_at_this_line"}
    if _t(at) - _t(found[3]) > PRICE_MAX_AGE:
        return {**out, "price_reason": "price_older_than_20_minutes"}
    return dict(zip(out, (int(found[0]), found[1], found[2], found[3], None)))


def _row(sport: str, pick: dict, store, at: str, on_card: bool, recommended: Optional[tuple]) -> Optional[dict]:
    f = pp.facts(store, sport, pick)
    if f is None:
        return None
    h = _dump(pick.get("percentage_history")) or {}
    tg, past = h.get("telegram") or {}, h.get("history") or {}
    has = bool(h)
    rec = bool(tg) and tg.get("available") is not False and not tg.get("no_history")      # no rows in this bucket is no record, not 0-0
    return {
        "sport": sport, "game_id": pick["game_id"], "market": pick["market"], "forecast_key": forecast_key(sport, pick),
        "forecast_id": f["forecast_id"], "observed_at": at, "start_at": pick["tipoff"], "generated_at": pick["generated_at"],
        "matchup": pick.get("matchup"), "pick_label": pick.get("pick_label"), "side": f["side"], "line": f["line"],
        "line_source": h.get("line_source") or (pick.get("quote_check") or {}).get("line_source"), "probability": f["probability"],
        "percentage": to.displayed_percentage(f["probability"]), "model_version": f["model_version"], "source": h.get("source"),
        "colour": to.bucket_colour(h["wins"], h["losses"]) if has else None, "colour_rule": to.COLOUR_RULE,
        "wins": h.get("wins"), "losses": h.get("losses"), "pushes": h.get("pushes", 0) if has else None, "voids": h.get("voids", 0) if has else None,
        "recovered_colour": to.bucket_colour(tg["wins"], tg["losses"]) if rec else None,
        "recovered_reason": None if rec else (tg.get("reason") or ("no_history" if tg.get("no_history") else "not_shown")), "recovered_source": tg.get("source") if rec else None,
        "recovered_wins": tg.get("wins") if rec else None, "recovered_losses": tg.get("losses") if rec else None,
        "recovered_pushes": tg.get("pushes") if rec else None, "recovered_voids": tg.get("voids") if rec else None,
        "past_colour": past.get("colour"), "past_wins": past.get("wins"), "past_losses": past.get("losses"),
        "on_card": int(on_card),
        "recommended": (None if on_card else 0) if recommended is None else int(recommended == (pick["market"], str(pick["forecast_id"]))),
        "basis": "live", "provenance": None}


def _insert(conn, row: dict) -> None:
    row = {**row, "recorded_at": datetime.now(timezone.utc).strftime(FMT)}
    conn.execute(f"INSERT INTO pick_snapshots ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))


def _latest(conn, r: dict):
    return conn.execute("SELECT * FROM pick_snapshots WHERE event = 'seen' AND sport = ? AND game_id = ? AND market = ? AND forecast_key = ? "
                        "ORDER BY version_no DESC LIMIT 1", (r["sport"], r["game_id"], r["market"], r["forecast_key"])).fetchone()


def _changed(last, r: dict) -> bool:
    """Is `r` a new version? An unreadable recommendation (None) alone is not a change."""
    return last is None or any(last[k] != r[k] for k in VERSIONED if not (k == "recommended" and r[k] is None))


def _open_store(sport: str):
    from . import db
    try:
        return db.connect_readonly(sport)
    except Exception:
        return None


def observe(sport: str, report: dict, now: datetime, store=None) -> int:
    """Save what `report` shows for every pick that has not started: the rows written, or -1 when it failed. Never raises and
    never changes the report. `store`: an open forecast store (prices, model facts), else one is opened read-only."""
    conn = opened = None
    try:
        conn = _connect(write=True)
        at = now.strftime(FMT)
        broken = 0

        def failed(p, exc):                        # one malformed pick never stops the others; the run is still reported as failed
            nonlocal broken
            broken += 1
            print(f"pick-snapshots recorder (cards unaffected): {sport} {p.get('game_id')} {p.get('market')}: {type(exc).__name__}",
                  file=sys.stderr, flush=True)

        picks = []
        for p in report["picks"]:
            try:
                if p["status"] == "scheduled" and p["grade"] == "pending" and _t(p["generated_at"]) <= _t(at) < _t(p["tipoff"]):
                    p["game_id"], p["market"], p["forecast_id"], p["probability"]
                    if not (p["generated_at"] <= at < p["tipoff"]):
                        raise ValueError("time format")
                    picks.append(p)
            except Exception as exc:
                failed(p, exc)
        if not picks:
            return -1 if broken else 0
        if store is None:
            store = opened = _open_store(sport)
        rep = {**report, "sport": sport, "picks": picks}
        cards = [p for gid in dict.fromkeys(p["game_id"] for p in picks) for p in pp.on_card(rep, gid, now)]
        named, shown = _recommended(rep, cards), {id(p) for p in cards}
        fresh = []
        for p in picks:
            try:
                r = _row(sport, p, store, at, id(p) in shown, named.get(p["game_id"]) if id(p) in shown else None)
                if r is not None and _changed(_latest(conn, r), r):
                    fresh.append((p, {**r, **price_at(store, sport, p, r["side"], r["line"], at)}))
            except sqlite3.OperationalError:       # our own file is locked: give up once, never wait again per pick
                raise
            except Exception as exc:
                failed(p, exc)
        if not fresh:
            return -1 if broken else 0
        commit = _commit()                         # git is asked before the write lock is taken, never under it
        written = 0
        conn.execute("BEGIN IMMEDIATE")
        try:
            for p, r in fresh:
                last = _latest(conn, r)                        # again, under the lock: two runs never write one version twice
                if not _changed(last, r):
                    continue
                try:
                    _insert(conn, {"event": "seen", "version_no": 1 if last is None else last["version_no"] + 1, "code_commit": commit, **r})
                    written += 1
                except (sqlite3.Error, ValueError, TypeError, OverflowError) as exc:
                    if not conn.in_transaction:     # the whole transaction is gone (disk, corruption): nothing to keep
                        raise
                    failed(p, exc)
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
        return -1 if broken else written
    except Exception as exc:
        print(f"pick-snapshots recorder (cards unaffected): {sport}: {type(exc).__name__}", file=sys.stderr, flush=True)
        return -1
    finally:
        for c in (conn, opened):
            if c is not None:
                c.close()


def record_sent(ledger, sport: str, key: str, game_id: str, report: dict, now: datetime) -> int:
    """One 'sent' row per market of a card whose delivery status is 'sent'. Same rules as observe; never raises."""
    conn = store = None
    try:
        d = ledger.execute("SELECT attempted_at, message_id FROM deliveries WHERE delivery_key = ? AND status = 'sent'", (key,)).fetchone()
        if d is None:
            return 0
        conn = _connect(write=True)
        store = _open_store(sport)
        rep = {**report, "sport": sport}
        cards = pp.on_card(rep, game_id, now)
        named = _recommended(rep, cards).get(game_id)
        commit, written = _commit(), 0
        for p in cards:
            at = d["attempted_at"]
            r = _row(sport, p, store, at, True, named) if at < p["tipoff"] else None
            if r is None or conn.execute("SELECT 1 FROM pick_snapshots WHERE event = 'sent' AND delivery_key = ? AND market = ?",
                                         (key, p["market"])).fetchone():
                continue
            _insert(conn, {"event": "sent", "delivery_key": key, "message_id": d["message_id"], "code_commit": commit, **r, **price_at(store, sport, p, r["side"], r["line"], at)})
            written += 1
        return written
    except Exception as exc:
        print(f"pick-snapshots recorder (card already sent): {sport}: {type(exc).__name__}", file=sys.stderr, flush=True)
        return -1
    finally:
        for c in (conn, store):
            if c is not None:
                c.close()


# --- Backfill: only what a saved, confirmed card proves ------------------------------------------------------------------
MODEL_LINE = re.compile(r"<b>Model ([\d.]+)%</b>(?: (🟢|🔴))?(?: · (.*))?$")
RECORD = re.compile(r"this exact % has gone (\d+)–(\d+) ")
EXTRA = {"pushes": re.compile(r" · (\d+) push(?:es)?\b"), "voids": re.compile(r" · (\d+) voids?\b")}


def _counts(text: str, empty: str) -> Optional[dict]:
    m = RECORD.search(text)
    if not m and not text.startswith(empty):
        return None
    out = {"wins": int(m.group(1)) if m else 0, "losses": int(m.group(2)) if m else 0}
    for k, rx in EXTRA.items():
        x = rx.search(text)
        out[k] = int(x.group(1)) if x else 0
    return out


def _recovered(block: list) -> dict:
    none = {"recovered_colour": None, "recovered_wins": None, "recovered_losses": None, "recovered_pushes": None, "recovered_voids": None,
            "recovered_source": None}
    line = next((x for x in block if x.startswith((to.TELEGRAM_LABEL, "No recovered Telegram history"))), None)
    if line is None:
        return {**none, "recovered_reason": "not_on_card"}
    if line.startswith("No recovered Telegram history"):
        return {**none, "recovered_reason": "no_rows_for_sport"}
    body = line.split(": ", 1)[1] if ": " in line else ""
    if body.startswith("recovered history unavailable"):
        return {**none, "recovered_reason": "file_missing" if "file missing" in body else "unreadable"}
    if body.startswith("no recovered history at this exact %"):
        return {**none, "recovered_reason": "no_history"}
    c = _counts(body, "no settled result at this exact %")
    if c is None:
        return {**none, "recovered_reason": "not_on_card"}
    colour = to.bucket_colour(c["wins"], c["losses"])
    word = next((w for w in ("green", "red") if f" · {w} record" in body), "neutral")
    if word != colour:
        return {**none, "recovered_reason": "mark_contradicts_record"}
    return {"recovered_colour": colour, "recovered_reason": None, "recovered_source": to.TELEGRAM_SOURCE,
            **{f"recovered_{k}": v for k, v in c.items()}}


def card_evidence(sport: str, pub: dict, delivery: dict, start: str) -> tuple:
    """(snapshot fields, None) when the saved text of a confirmed card proves the colour it showed for this published pick,
    else (None, why not). The printed record must be there and must give the printed mark under bucket-record-v1."""
    from .telegram import COLOUR_KEY, COLOUR_PREFIX, GREEN_MARK, RED_MARK
    from .telegram_history import market_title
    if delivery["status"] != "sent":
        return None, "delivery_not_confirmed"
    if not pub["sent_at"] < start or not delivery["attempted_at"] < start:
        return None, "sent_at_or_after_start"
    if delivery["attempted_at"] < RULE_LIVE_SINCE:
        return None, "before_colour_rule"
    text = delivery["text"]
    marked = any(f"%</b> {m}" in text for m in (GREEN_MARK, RED_MARK))
    key = next((unescape(x) for x in text.split("\n") if x.startswith(COLOUR_PREFIX)), None)
    if (marked and key != COLOUR_PREFIX + COLOUR_KEY) or (key is not None and key != COLOUR_PREFIX + COLOUR_KEY):
        return None, "another_colour_rule"
    lines, blocks = text.split("\n"), []
    for i in range(len(lines) - 2):
        m = MODEL_LINE.match(lines[i + 2])
        if m and unescape(re.sub(r"<[^>]+>", "", lines[i])) == market_title(sport, pub["market"]):
            end = next((j for j in range(i + 3, len(lines)) if not lines[j]), len(lines))
            blocks.append((unescape(re.sub(r"<[^>]+>", "", lines[i + 1])).split(" · ")[0], m, [unescape(x) for x in lines[i + 3:end]]))
    if len(blocks) != 1:
        return None, "market_not_on_card" if not blocks else "market_twice_on_card"
    label, m, rest = blocks[0]
    if m.group(1) != to.displayed_percentage(pub["probability"]):
        return None, "percentage_not_on_card"
    c = _counts(m.group(3) or "", "no settled history at this exact %")
    if c is None:
        return None, "no_record_on_card"
    colour = to.bucket_colour(c["wins"], c["losses"])
    if {GREEN_MARK: "green", RED_MARK: "red", None: "neutral"}[m.group(2)] != colour:
        return None, "mark_contradicts_record"
    rec = "<b>Recommended pick</b>"
    named = unescape(re.sub(r"<[^>]+>", "", lines[lines.index(rec) + 1])) if rec in lines else None
    return {"sport": sport, "game_id": pub["game_id"], "market": pub["market"], "forecast_key": forecast_key(sport, pub["forecast_id"]),
            "forecast_id": str(pub["forecast_id"]), "delivery_key": pub["delivery_key"], "message_id": delivery["message_id"],
            "observed_at": delivery["attempted_at"], "start_at": start, "generated_at": pub["generated_at"] or delivery["attempted_at"],
            "matchup": unescape(re.sub(r"<[^>]+>", "", lines[1])), "pick_label": label, "side": pub["side"], "line": pub["line"],
            "probability": pub["probability"], "percentage": m.group(1), "model_version": pub["model_version"],
            "colour": colour, "colour_rule": to.COLOUR_RULE, **c, **_recovered(rest), "on_card": 1, "recommended": int(named == label),
            "basis": "card_text",
            "provenance": json.dumps({"delivery_key": pub["delivery_key"], "message_id": delivery["message_id"],
                                      "published_basis": pub["basis"], "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                                      "model_line": m.group(0)}, ensure_ascii=False)}, None


def backfill(ledger, sport: str, starts: dict, apply: bool = False, store=None) -> dict:
    """Reads published_picks + deliveries; writes (with apply) one 'sent' row, basis 'card_text', per proven pick. Repeatable: an
    existing row of the delivery and market is kept. The price is the stored one that could be taken when the card went out (the
    quote tables are append-only with their retrieval times). {candidates, proven, written, already_saved, not_proven: {reason: n}}."""
    out, why = {"sport": sport, "candidates": 0, "proven": 0, "written": 0, "already_saved": 0}, Counter()
    pubs = [dict(r) for r in ledger.execute("SELECT * FROM published_picks WHERE sport = ? ORDER BY published_id", (sport,))]
    conn = _connect(write=True) if apply else None
    try:
        for pub in pubs:
            out["candidates"] += 1
            d = ledger.execute("SELECT delivery_key, text, status, attempted_at, message_id FROM deliveries WHERE delivery_key = ?",
                               (pub["delivery_key"],)).fetchone()
            start = starts.get(pub["game_id"])
            if pub["basis"] == pp.UNCONFIRMED_BASIS or d is None or start is None:
                why["delivery_not_confirmed" if pub["basis"] == pp.UNCONFIRMED_BASIS else "no_saved_card" if d is None else "start_unknown"] += 1
                continue
            row, reason = card_evidence(sport, pub, dict(d), start)
            if row is None:
                why[reason] += 1
                continue
            out["proven"] += 1
            if conn is None:
                continue
            if conn.execute("SELECT 1 FROM pick_snapshots WHERE event = 'sent' AND delivery_key = ? AND market = ?",
                            (pub["delivery_key"], pub["market"])).fetchone():
                out["already_saved"] += 1
                continue
            _insert(conn, {"event": "sent", "code_commit": _commit(), **row,
                           **price_at(store, sport, {"game_id": row["game_id"], "market": row["market"], "tipoff": start}, row["side"],
                                      row["line"], row["observed_at"])})
            out["written"] += 1
    finally:
        if conn is not None:
            conn.close()
    return {**out, "not_proven": dict(why)}


# --- Counting ------------------------------------------------------------------------------------------------------------
NO_RECORD = ("no_history", "no_rows_for_sport", "file_missing", "unreadable")      # the recovered file had nothing to say: not a neutral record


def original(snaps: list) -> dict:
    """{(game, market, forecast_key): its earliest saved row}; `snaps` as `load` orders them."""
    out: dict = {}
    for r in snaps:
        out.setdefault((r["game_id"], r["market"], r["forecast_key"]), r)
    return out


def tracking_start(snaps: list) -> Optional[str]:
    return min((r["observed_at"] for r in snaps if r["basis"] == "live" and r["event"] == "seen"), default=None)


def recovered_colour(snap: dict) -> str:
    return snap["recovered_colour"] or ("no_record" if snap["recovered_reason"] in NO_RECORD else "unknown")


def _support(s: dict) -> dict:
    return {**{k: s[k] for k in ("wins", "losses", "pushes", "voids", "recovered_wins", "recovered_losses", "recovered_pushes",
                                 "recovered_voids", "recovered_reason", "past_wins", "past_losses", "past_colour", "colour_rule", "observed_at",
                                 "basis", "event", "provenance", "price_american", "price_provider", "price_quote_id",
                                 "price_retrieved_at", "price_reason")}, "live_source": s["source"], "recovered_source": s["recovered_source"]}


def _version(s: dict) -> dict:
    return {k: s[k] for k in ("event", "version_no", "forecast_id", "generated_at", "observed_at", "pick_label", "side", "line", "percentage",
                              "colour", "wins", "losses", "recovered_colour", "recovered_wins", "recovered_losses", "recommended", "on_card", "basis")}


SUPERSEDED_NOTE = "superseded, not counted"


def classify(sport: str, items: list, snaps: list, published: list, regrade: Callable, starts: Optional[dict] = None,
             now: Optional[str] = None, label: Optional[Callable] = None) -> dict:
    """{'all', 'recommended', 'sent', 'excluded'}. `items`: the forecast of record per started game and market (one each).
    `regrade(row, item)` -> the grade of the saved side and line. `published`: the ledger's sends, unconfirmed ones
    included (they are only counted as excluded). `label(published row)` -> its pick label, for a send whose colour was not saved."""
    first, began = original(snaps), tracking_start(snaps)
    by_gm: dict = {}
    for r in snaps:
        by_gm.setdefault((r["game_id"], r["market"]), []).append(r)

    def unknown(item: dict, at: str, saved: bool, late: bool = False) -> dict:
        reason = ("saved_after_the_moved_start" if late else "before_colour_rule" if at < RULE_LIVE_SINCE else "later_forecast_not_saved" if saved else
                  "before_tracking" if began is None or at < began else "not_recorded")
        return {**item, "colour": "unknown", "recovered_colour": "unknown", "unknown_reason": reason, "support": None, "price": None,
                "recommended": None, "selection_differs": False}

    def known(item: dict, s: dict) -> dict:
        differs = s["side"] != item.get("side", s["side"]) or (s["line"] is not None and item.get("line") is not None
                                                              and abs(s["line"] - item["line"]) > 1e-9)
        return {**item, **{k: s[k] for k in ("forecast_id", "side", "line", "percentage", "model_version", "generated_at")},
                "pick": s["pick_label"] or item.get("pick"), "grade": regrade(s, item) or "unresolved",
                "colour": s["colour"] or "unknown", "recovered_colour": recovered_colour(s),
                "unknown_reason": None if s["colour"] else "no_exact_record_shown", "support": _support(s), "price": s["price_american"],
                "selection_differs": differs}

    out: dict = {"all": [], "recommended": [], "sent": [], "excluded": Counter()}
    by_item, named = {}, {}
    for it in items:
        gm = (it["game_id"], it["market"])
        by_item[gm] = it
        mine = by_gm.get(gm, [])
        before = [v for v in mine if v["observed_at"] < it["start_at"]]     # a start that moved earlier: later sights are not pregame evidence
        s = first.get((*gm, it["forecast_key"]))
        late = s is not None and s not in before
        row = known(it, s) if s is not None and not late else unknown(it, it["start_at"], bool(before), late)
        row["versions"] = [_version(v) for v in mine]
        row["recommendation_status"] = row["recommendation_note"] = None
        if s is not None and not late:         # the card's recommendation as it last stood before the start: at most one per game
            carded = [v for v in before if v["forecast_key"] == it["forecast_key"] and v["on_card"] and v["recommended"] is not None]
            row["recommended"] = bool(carded and carded[-1]["recommended"])
            if row["recommended"]:
                named.setdefault(it["game_id"], []).append((carded[-1]["observed_at"], len(out["all"]), row))
        out["all"].append(row)
    for options in named.values():             # a pick that left the card keeps its last state: the later recommendation stands
        keep = max(options, key=lambda o: o[:2])[2]
        for *_, row in options:
            row["recommended"] = row is keep
            row["recommendation_status"] = "counted" if row is keep else "superseded"
            row["recommendation_note"] = None if row is keep else SUPERSEDED_NOTE
    out["recommended"] = [r for r in out["all"] if r["recommended"]]

    sent_rows = {(r["delivery_key"], r["market"]): r for r in snaps if r["event"] == "sent"}
    last: dict = {}
    for p in sorted(published, key=lambda p: (p["sent_at"], p.get("published_id") or 0)):
        start = (starts or {}).get(p["game_id"])
        if p["basis"] == pp.UNCONFIRMED_BASIS:
            out["excluded"]["delivery_not_confirmed"] += 1
        elif start is None:
            out["excluded"]["start_unknown"] += 1
        elif not p["sent_at"] < start:
            out["excluded"]["sent_at_or_after_start"] += 1
        elif now is not None and start > now:
            out["excluded"]["not_started"] += 1
        else:
            if (p["game_id"], p["market"]) in last:
                out["excluded"]["earlier_cards"] += 1
            last[(p["game_id"], p["market"])] = (p, start)
    for gm, (p, start) in last.items():
        it = by_item.get(gm)
        base = {**(it or {}), "sport": sport, "game_id": gm[0], "market": gm[1], "forecast_id": str(p["forecast_id"]),
                "forecast_key": forecast_key(sport, p["forecast_id"]), "side": p["side"], "line": p["line"], "start_at": start,
                "percentage": to.displayed_percentage(p["probability"]), "model_version": p["model_version"],
                "generated_at": p["generated_at"], "pick": label(p) if label else None}
        base.setdefault("game_date", et_date(start))
        s = sent_rows.get((p["delivery_key"], p["market"]))
        row = known(base, s) if s is not None else {**unknown(base, p["sent_at"], False), "grade": regrade(p, it) or "unresolved"}
        row.update(delivery_key=p["delivery_key"], message_id=p["message_id"], sent_at=p["sent_at"], sent_basis=p["basis"],
                   recommended=bool(s["recommended"]) if s is not None else None, versions=[_version(v) for v in by_gm.get(gm, [])])
        out["sent"].append(row)
    pick_of = lambda r: (r["game_id"], r["market"], r["side"], r["line"])      # the recommendation itself, not its forecast version
    counted = {pick_of(r) for r in out["recommended"]}
    counted_games = {r["game_id"] for r in out["recommended"]}
    for row in out["sent"]:                    # a card's own result stays; only the match's one counted recommendation is in the record
        if row["recommended"]:
            row["recommendation_status"] = ("counted" if pick_of(row) in counted
                                            else "superseded" if row["game_id"] in counted_games else None)
        else:
            row["recommendation_status"] = None
        row["recommendation_note"] = SUPERSEDED_NOTE if row["recommendation_status"] == "superseded" else None
    out["excluded"] = dict(out["excluded"])
    return out


HALF = ("half_win", "half_loss")


def profit(grade: str, american: Optional[float]) -> Optional[float]:
    """Units won on a flat stake of 1 at the saved price; None while unsettled or unpriced."""
    if american is None or grade not in ("win", "loss", "push", "void", "cancelled", "canceled", *HALF):
        return None
    gain = american / 100 if american > 0 else 100 / -american
    return {"win": gain, "half_win": gain / 2, "loss": -1.0, "half_loss": -0.5}.get(grade, 0.0)


def tally(rows: list) -> dict:
    g = Counter(r["grade"] for r in rows)
    w, l = g["win"], g["loss"]
    void, half = g["void"] + g["cancelled"] + g["canceled"], sum(g[h] for h in HALF)
    priced = [r for r in rows if r.get("price") is not None]
    bets = [profit(r["grade"], r["price"]) for r in priced if r["grade"] in ("win", "loss", "push", *HALF)]
    return {"predictions": len(rows), "games": len({r["game_id"] for r in rows}), "win": w, "loss": l, "push": g["push"], "void": void,
            "half": half, "unsettled": len(rows) - w - l - g["push"] - void - half, "settled": w + l,
            "win_rate": w / (w + l) if w + l else None, "ci95": to.wilson(w, w + l), "small_sample": w + l < to.MIN_LIVE_GRADED,
            "priced": len(priced), "unpriced": len(rows) - len(priced), "priced_settled": len(bets),
            "profit_units": sum(bets) if bets else None, "roi": sum(bets) / len(bets) if bets else None}


def summarise(rows: list) -> dict:
    out = {"total": tally(rows)}
    for name, field in (("live", "colour"), ("recovered", "recovered_colour")):
        out[name] = {c: tally([r for r in rows if r[field] == c]) for c in COLOURS if name == "recovered" or c != "no_record"}
    out["cross"] = dict(Counter(f"{r['colour']}|{r['recovered_colour']}" for r in rows if r["support"]))
    parts = [t for name in ("live", "recovered") for t in out[name].values()] + [out["total"]]
    out["adds_up"] = (all(sum(t["predictions"] for t in out[n].values()) == len(rows) for n in ("live", "recovered"))
                      and all(t["predictions"] == t["win"] + t["loss"] + t["push"] + t["void"] + t["half"] + t["unsettled"]
                              == t["priced"] + t["unpriced"] for t in parts))
    return out


def main(argv: Optional[list] = None) -> int:
    """python -m app.pick_snapshots backfill [--apply]: what the saved cards prove (dry run unless --apply)."""
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] != ["backfill"] or set(argv[1:]) - {"--apply"}:
        print("usage: python -m app.pick_snapshots backfill [--apply]", file=sys.stderr)
        return 2
    from . import release
    from .pick_colours import SPORTS, starts
    apply = "--apply" in argv
    if apply and config.protected_data_dir(config.data_dir()) and (not release.enabled() or release._checkout(release.code_root().resolve())[1]):
        raise SystemExit("release guard: a backfill into the live data folder requires the clean release checkout")
    ledger = sqlite3.connect(f"file:{config.telegram_ledger_path()}?mode=ro", uri=True, timeout=5)
    ledger.row_factory = sqlite3.Row
    try:
        for sport in SPORTS:
            store = _open_store(sport)
            if store is None:
                print(json.dumps({"sport": sport, "status": "no_store"}))
                continue
            try:
                print(json.dumps(backfill(ledger, sport, starts(store, sport), apply=apply, store=store)))
            finally:
                store.close()
    finally:
        ledger.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
