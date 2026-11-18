"""The picks a Telegram card actually published (Nov 17 2026, owner decision pending on whether cards should follow
re-forecasts).

A card is sent from the forecast the app had at send time, while the app's record grades the LATEST pregame forecast of
each game and market. This module keeps what each sent card said, in an append-only table of the delivery ledger:

    published_picks(delivery_key, sport, game_id, market, forecast_id, side, line, probability, model_version,
                    generated_at, sent_at, message_id, basis)

- Written only AFTER a card's status is 'sent' (telegram.run_once), never before, and a failure here never affects
  sending (record() catches everything). One row per (delivery_key, market); repeats are ignored.
- `basis` is 'live' for rows written at send time and 'backfill_text_match' for rows reconstructed from the ledger text
  by scripts/backfill_published_picks.py (never run against the live ledger without the owner's say-so).
- Grading reuses the app's own rules: coverage.grade for NBA/NHL/NFL/NCAAB, the Soccer grade functions for Soccer.
Nothing here changes a forecast, a delivery key or a card.
"""
import sqlite3
import sys
from datetime import datetime, timedelta
from typing import Optional

SCHEMA = '''
CREATE TABLE IF NOT EXISTS published_picks (
  published_id INTEGER PRIMARY KEY,
  delivery_key TEXT NOT NULL,
  sport        TEXT NOT NULL,
  game_id      TEXT NOT NULL,
  market       TEXT NOT NULL,
  forecast_id  TEXT NOT NULL,
  side         TEXT NOT NULL,
  line         REAL,
  probability  REAL NOT NULL,
  model_version TEXT,
  generated_at TEXT,
  sent_at      TEXT NOT NULL,
  message_id   INTEGER,
  basis        TEXT NOT NULL DEFAULT 'live',
  UNIQUE (delivery_key, market)
);
CREATE INDEX IF NOT EXISTS published_picks_game ON published_picks(sport, game_id, market);
CREATE TRIGGER IF NOT EXISTS published_picks_no_update BEFORE UPDATE ON published_picks
BEGIN SELECT RAISE(ABORT, 'published_picks is append-only'); END;
CREATE TRIGGER IF NOT EXISTS published_picks_no_delete BEFORE DELETE ON published_picks
BEGIN SELECT RAISE(ABORT, 'published_picks is append-only'); END;
'''

COLUMNS = ('delivery_key', 'sport', 'game_id', 'market', 'forecast_id', 'side', 'line', 'probability', 'model_version',
           'generated_at', 'sent_at', 'message_id', 'basis')


def row_id(forecast_id) -> Optional[int]:
    """The stored forecast row behind a pick's forecast id: 12 -> 12, '1x2:12:double_chance:home_draw' -> 12."""
    try:
        return int(str(forecast_id).split(':')[1]) if ':' in str(forecast_id) else int(forecast_id)
    except (ValueError, IndexError):
        return None


def on_card(report: dict, game_id: str, now: datetime) -> list:
    """The picks of `game_id` that telegram.message_batches puts on the card at `now` (same eligibility)."""
    out = []
    for pick in report['picks']:
        if pick['game_id'] != game_id:
            continue
        start = datetime.fromisoformat(pick['tipoff'].replace('Z', '+00:00'))
        generated = datetime.fromisoformat(pick['generated_at'].replace('Z', '+00:00'))
        if report['sport'] == 'soccer':
            from .tickets import eligible
            if not eligible(pick, now) or generated > now:
                continue
        elif (pick['status'] != 'scheduled' or pick['grade'] != 'pending' or not now < start <= now + timedelta(hours=2)
              or not start - timedelta(hours=2) <= generated <= now):
            continue
        out.append(pick)
    return out


def _field(obj, key):
    return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)


def facts(store, sport: str, pick: dict) -> Optional[dict]:
    """The published forecast of one pick on a card: forecast_id, side, line, probability, model_version, generated_at.
    Prefers what the pick carries; fills the rest from the stored forecast row (read-only). None when unknown."""
    history = pick.get('percentage_history')
    side = pick.get('side') if sport != 'soccer' or pick['market'] != '1x2_regulation' else None
    side = side or pick.get('outcome') or _field(history, 'side')
    line, model_version = pick.get('line'), pick.get('model_version') or _field(history, 'model_version')
    if sport == 'soccer':
        row = None
        rid = row_id(pick.get('row_id') or pick['forecast_id'])
        table, cols = (('soccer_market_forecasts', 'candidate_sha256, model') if pick['market'] not in ('1x2_regulation', 'double_chance', 'draw_no_bet')
                       else ('soccer_live_forecasts', 'candidate_sha256, model'))
        if store is not None and rid is not None:
            try:
                row = store.execute(f'SELECT {cols} FROM {table} WHERE forecast_id=?', (rid,)).fetchone()
            except sqlite3.Error:
                row = None
        if model_version is None and row is not None:
            model_version = f"{row[1]}:{(row[0] or '')[:12]}"
    else:
        row = None
        if store is not None:
            try:
                row = store.execute('SELECT line, model_version FROM game_forecasts WHERE forecast_id=?', (pick['forecast_id'],)).fetchone()
            except sqlite3.Error:
                row = None
        if row is not None:
            line = row[0] if line is None else line
            model_version = model_version or row[1]
    if side is None:
        return None
    return {'forecast_id': str(pick['forecast_id']), 'side': str(side), 'line': line, 'probability': float(pick['probability']),
            'model_version': model_version, 'generated_at': pick.get('generated_at')}


def ensure(conn) -> None:
    conn.executescript(SCHEMA)


def record(conn, sport: str, key: str, game_id: str, report: dict, now: datetime) -> int:
    """Append the card's picks to published_picks after a successful send. Returns the rows written; never raises."""
    try:
        from . import db
        ensure(conn)
        sent = conn.execute('SELECT message_id, attempted_at FROM deliveries WHERE delivery_key=? AND status=\'sent\'', (key,)).fetchone()
        if sent is None:
            return 0
        store = None
        try:
            store = db.connect_readonly(sport)
        except Exception:
            store = None
        written = 0
        try:
            for pick in on_card(report, game_id, now):
                f = facts(store, sport, pick)
                if f is None:
                    continue
                cur = conn.execute(
                    'INSERT OR IGNORE INTO published_picks (delivery_key, sport, game_id, market, forecast_id, side, line, probability, '
                    'model_version, generated_at, sent_at, message_id, basis) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (key, sport, game_id, pick['market'], f['forecast_id'], f['side'], f['line'], f['probability'],
                     f['model_version'], f['generated_at'], sent['attempted_at'], sent['message_id'], 'live'))
                written += cur.rowcount
        finally:
            if store is not None:
                store.close()
        return written
    except Exception as exc:       # a card already sent is never affected
        print(f'published-picks recorder (card unaffected): record failed: {type(exc).__name__}', file=sys.stderr, flush=True)
        return 0


def load(sport: str, path=None) -> dict:
    """{(game_id, market): the latest published row as a dict} from the delivery ledger, opened read-only. {} when the
    ledger or the table is absent or unreadable."""
    try:
        from . import config
        p = path or config.telegram_ledger_path()
        conn = sqlite3.connect(f'file:{p}?mode=ro', uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute('SELECT * FROM published_picks WHERE sport=? ORDER BY published_id', (sport,)).fetchall()
        finally:
            conn.close()
    except Exception:
        return {}
    return {(r['game_id'], r['market']): dict(r) for r in rows}


def grade(store, sport: str, row: dict, game, as_of: str) -> Optional[str]:
    """win / loss / push / pending / void / unscorable for a published pick, by the app's own settlement of the forecast the
    card published (not the record's later forecast). `game` = the games row (point sports). None when it cannot be read."""
    try:
        if sport == 'soccer':
            from . import soccer_live, soccer_markets_live as sml
            rid = row_id(row['forecast_id'])
            if row['market'] == '1x2_regulation':
                return soccer_live.grade(store, rid, row['side'], as_of)['grade']
            if row['market'] in sml.DERIVED:
                return sml.grade_derived(store, rid, row['market'], row['side'], as_of)['grade']
            return sml.grade(store, rid, row['market'], row['side'], row['line'], as_of)['grade']
        from . import coverage
        f = store.execute('SELECT * FROM game_forecasts WHERE forecast_id=?', (int(row['forecast_id']),)).fetchone()
        if f is None:
            return None
        p = (f['p_a'], f['p_mid'], f['p_b'])
        try:
            decision = (store.execute('SELECT decision FROM game_decisions WHERE game_id=?', (row['game_id'],)).fetchone() or [None])[0]
        except sqlite3.Error:
            decision = None
        return coverage.grade(row['market'], f['line'], p, game, as_of, f['tipoff_at'], decision)
    except Exception:
        return None
