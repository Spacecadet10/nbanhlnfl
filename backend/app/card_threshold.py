"""Card colouring by a per-class daily threshold (owner brief, Nov 21 2026). Display only: no forecast, pick, price, settlement,
delivery key or card identity changes here, and nothing here is read by a model.

Each market a card renders belongs to one class (CLASS_OF): W single outcome (winners, Soccer 1X2), L two-way line (spreads,
totals, team totals, draw no bet, goal totals), R NHL regulation 3-way, D Soccer double chance; None = never coloured. A class
has one threshold T a day, computed only from its own priced picks that settled before that day (`fit`, the brief's rule), and
frozen append-only in `card_thresholds` (`run`, `seed`); a past day is never recomputed. A sport x class cell overrides its class
only with 200+ priced picks in its own trailing window. The card colours a pick's model % green at or above T and red (struck
through) below, compared in Decimal tenths of a percent (`tenths`, `colour_for`): never a float comparison, never float x 100.

Priced = the stored DraftKings price the pick could see: for NBA/NHL/NFL/NCAAB the canonical forecast (coverage.canonical) at
the latest 'current' quote at the same line retrieved at or before the forecast (paper_trading._quote, the price paper P/L uses);
for Soccer the 1X2 / goal-total quote stored at or before the forecast. Soccer double chance and draw no bet have only derived
no-vig prices and NHL regulation 3-way has no quotes: those picks are unpriced, so classes D and R are never established."""
import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Callable, Optional
from zoneinfo import ZoneInfo

RULE_VERSION = 'card-threshold-v1'
SEED_VERSION = 'seed'
TORONTO = ZoneInfo('America/Toronto')
WINDOW = 400                    # trailing priced picks of a class (or of a cell)
CELL_MIN = 200                  # a sport x class cell overrides its class only with this many in its own window
MIN_N = 150                     # fewer priced picks in the window: keep yesterday's T (and never seed)
MIN_CANDIDATE = 100             # a candidate T needs this many picks at or above it
PRIOR = Decimal(20)             # shrinkage weight toward 0.5, the same for hit rate and break-even
EDGE = Decimal('0.01')          # hit_s must exceed be_s by at least this
CLAMP = Decimal('1.0')          # most T moves in a day
LOW, HIGH = Decimal('50.0'), Decimal('90.0')
HALF = Decimal('0.5')
CLASSES = ('W', 'L', 'R', 'D')
SPORTS = ('nba', 'nhl', 'nfl', 'ncaab', 'soccer')

_US = ('nba', 'nhl', 'nfl', 'ncaab')
CLASS_OF = {
    **{(s, 'winner'): 'W' for s in _US}, ('soccer', '1x2_regulation'): 'W',
    **{(s, m): 'L' for s in _US for m in ('spread', 'total', 'team_total_home', 'team_total_away')},
    ('soccer', 'draw_no_bet'): 'L', ('soccer', 'goals_total'): 'L', ('soccer', 'goals_home'): 'L', ('soccer', 'goals_away'): 'L',
    ('nhl', 'reg3'): 'R', ('soccer', 'double_chance'): 'D',
    ('soccer', 'btts'): None, ('soccer', 'corners_total'): None, ('soccer', 'corners_home'): None,
    ('soccer', 'corners_away'): None, ('soccer', 'corners_handicap'): None,
}
# No stored sportsbook price exists for these markets (only a derived no-vig price, which never counts): never coloured.
NO_STORED_PRICE = {('soccer', 'double_chance'), ('soccer', 'draw_no_bet')}

SCHEMA = '''
CREATE TABLE IF NOT EXISTS card_thresholds (
  day TEXT NOT NULL,                     -- the Toronto day this threshold colours cards on
  scope TEXT NOT NULL,                   -- a class ('W') or a sport x class cell ('ncaab:L')
  class TEXT NOT NULL CHECK (class IN ('W', 'L', 'R', 'D')),
  sport TEXT,
  threshold TEXT,                        -- Decimal tenths of a percent, e.g. '55.0'; NULL when not established
  requested TEXT,                        -- what the rule asked for before the daily clamp (capped rows)
  n INTEGER NOT NULL,                    -- priced picks in the window
  window INTEGER NOT NULL,
  since TEXT, until TEXT,                -- first and last settled day in the window
  carried_from TEXT,                     -- held / capped: the day the carried line was set
  reason TEXT,
  inputs_hash TEXT NOT NULL,
  rule_version TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('set', 'seed', 'held', 'capped', 'not established')),
  generated_at TEXT NOT NULL,
  PRIMARY KEY (day, scope)
);
CREATE TRIGGER IF NOT EXISTS card_thresholds_no_update BEFORE UPDATE ON card_thresholds
BEGIN SELECT RAISE(ABORT, 'card_thresholds is append-only'); END;
CREATE TRIGGER IF NOT EXISTS card_thresholds_no_delete BEFORE DELETE ON card_thresholds
BEGIN SELECT RAISE(ABORT, 'card_thresholds is append-only'); END;
'''


# --- Decimal decision ------------------------------------------------------------------------------------------------------
def tenths(p) -> Decimal:
    """A stored probability as a percentage in tenths: Decimal(str(p)) rounded half-up to 0.001, times 100 (exact)."""
    return (Decimal(str(p)).quantize(Decimal('0.001'), rounding=ROUND_HALF_UP) * 100).quantize(Decimal('0.1'))


def colour_for(pct: Decimal, threshold: Decimal) -> str:
    return 'green' if pct >= threshold else 'red'


def class_of(sport: str, market: str) -> Optional[str]:
    return CLASS_OF.get((sport, market))


# --- Data shapes -----------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Pick:
    key: str
    sport: str
    cls: str
    pct: Decimal                # displayed percentage, tenths
    result: str                 # 'win' / 'loss' / 'push'
    implied: Decimal            # 1 / decimal odds of the stored price
    settled_at: str             # when the result was available (UTC ISO)


@dataclass(frozen=True)
class Line:
    cls: str
    sport: Optional[str]
    threshold: Optional[Decimal]
    n: int
    state: str                  # set / seed / held / capped / not refreshed / not established
    day: str                    # the day the stored row was written (for 'not refreshed': the day it was set)
    since: Optional[str]
    carried_from: Optional[str]
    seed_to: Optional[str] = None
    requested: Optional[Decimal] = None
    reason: Optional[str] = None


@dataclass
class Table:
    lines: dict                 # scope -> Line
    day: str

    def line_for(self, sport: str, cls: str) -> Optional[Line]:
        return self.lines.get(f'{sport}:{cls}') or self.lines.get(cls)


@dataclass(frozen=True)
class Fit:
    threshold: Optional[Decimal]
    requested: Optional[Decimal]
    n: int
    window: int
    state: str
    reason: Optional[str]
    inputs_hash: str
    since: Optional[str]
    until: Optional[str]


# --- The rule --------------------------------------------------------------------------------------------------------------
def settled_before(picks: list, cutoff: str) -> list:
    """Only picks whose result was available strictly before `cutoff` (no look-ahead)."""
    return [p for p in picks if p.settled_at < cutoff]


def _window(picks: list) -> list:
    priced = sorted((p for p in picks if p.result in ('win', 'loss')), key=lambda p: (p.settled_at, p.key))
    return priced[-WINDOW:]


def _hash(window: list) -> str:
    rows = [[p.key, p.result, str(p.pct), str(p.implied)] for p in window]
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def fit(picks: list, prev: Optional[Decimal], *, min_n: int = MIN_N, clamp: bool = True) -> Fit:
    """The brief's rule on one class's (or cell's) priced picks, already limited to those settled before the day.
    Lowest T in 50.0..90.0 (0.1 steps, >= 100 picks at or above it) whose shrunk hit rate beats the shrunk break-even by 0.01;
    none, or fewer than `min_n` picks: keep `prev`. `clamp`: move at most 1.0 from `prev` (not for a seed)."""
    window = _window(picks)
    n = len(window)
    since = window[0].settled_at[:10] if window else None
    until = window[-1].settled_at[:10] if window else None
    h = _hash(window)

    def keep(reason):
        return Fit(prev, None, n, WINDOW, 'held' if prev is not None else 'not established', reason, h, since, until)
    if n < min_n:
        return keep(f'fewer than {min_n} priced picks')
    requested = None
    for step in range(int(LOW * 10), int(HIGH * 10) + 1):
        t = Decimal(step).scaleb(-1)
        sub = [p for p in window if p.pct >= t]
        if len(sub) < MIN_CANDIDATE:
            break                   # higher candidates hold fewer picks still
        wins = sum(1 for p in sub if p.result == 'win')
        hit_s = (Decimal(wins) + PRIOR * HALF) / (len(sub) + PRIOR)
        be_s = (sum((p.implied for p in sub), Decimal(0)) + PRIOR * HALF) / (len(sub) + PRIOR)
        if hit_s - be_s >= EDGE:
            requested = t
            break
    if requested is None:
        return keep('no candidate beat the price')
    if clamp and prev is not None and abs(requested - prev) > CLAMP:
        moved = prev + CLAMP if requested > prev else prev - CLAMP
        return Fit(min(max(moved, LOW), HIGH), requested, n, WINDOW, 'capped', None, h, since, until)
    return Fit(requested, requested, n, WINDOW, 'set', None, h, since, until)


# --- Storage ---------------------------------------------------------------------------------------------------------------
def migrate(conn) -> None:
    conn.executescript(SCHEMA)


def connect(path, *, readonly: bool = False):
    if readonly:
        conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    else:
        conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    if not readonly:
        migrate(conn)
    return conn


def _scopes(by_class: dict) -> list:
    """[(scope, class, sport, picks)]: the four classes, plus every sport x class cell with picks."""
    out = [(c, c, None, list(by_class.get(c, []))) for c in CLASSES]
    for c in CLASSES:
        for s in SPORTS:
            cell = [p for p in by_class.get(c, []) if p.sport == s]
            if cell:
                out.append((f'{s}:{c}', c, s, cell))
    return out


def _prev(conn, scope: str, day: str):
    return conn.execute('SELECT * FROM card_thresholds WHERE scope = ? AND day < ? ORDER BY day DESC LIMIT 1',
                        (scope, day)).fetchone()


def _insert(conn, day, scope, cls, sport, f: Fit, version, carried_from, generated_at):
    conn.execute('INSERT OR IGNORE INTO card_thresholds (day, scope, class, sport, threshold, requested, n, window, since, '
                 'until, carried_from, reason, inputs_hash, rule_version, state, generated_at) '
                 'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                 (day, scope, cls, sport, None if f.threshold is None else str(f.threshold),
                  None if f.requested is None else str(f.requested), f.n, f.window, f.since, f.until, carried_from, f.reason,
                  f.inputs_hash, version, f.state, generated_at))


def _cell_qualifies(f: Fit, sport) -> bool:
    return sport is None or f.n >= CELL_MIN


def seed(conn, day: str, by_class: dict, *, generated_at: str) -> None:
    """Day 0: one row per class from the backtest window (no daily clamp; 150 picks minimum, else 'not established'), plus
    each sport x class cell that has 200 priced picks. Refused once any threshold exists."""
    migrate(conn)
    if conn.execute('SELECT 1 FROM card_thresholds LIMIT 1').fetchone():
        raise ValueError('card thresholds already exist; a seed is only day 0')
    with conn:
        for scope, cls, sport, picks in _scopes(by_class):
            f = fit(picks, None, min_n=MIN_N if sport is None else CELL_MIN, clamp=False)
            if not _cell_qualifies(f, sport) or (sport is not None and f.threshold is None):
                continue
            f = Fit(f.threshold, f.requested, f.n, f.window, 'seed' if f.threshold is not None else 'not established',
                    f.reason, f.inputs_hash, f.since, f.until)
            _insert(conn, day, scope, cls, sport, f, SEED_VERSION, None, generated_at)


def run(conn, day: str, load: Callable[[], dict], *, generated_at: str) -> None:
    """The daily job for `day`: refit every class (and qualifying cell) on picks settled before the day, clamp to 1.0 from
    the previous line, write once. A day already written keeps its rows; a day older than the newest row is refused (never
    backfilled). `load()` failing writes nothing, so cards read 'not refreshed'."""
    migrate(conn)
    newest = conn.execute('SELECT MAX(day) FROM card_thresholds').fetchone()[0]
    if newest is not None and day < newest:
        raise ValueError(f'refusing to backfill {day}: thresholds exist up to {newest}')
    if conn.execute('SELECT 1 FROM card_thresholds WHERE day = ? LIMIT 1', (day,)).fetchone():
        return                                  # frozen: the day keeps the T it first used
    by_class = load()
    with conn:
        for scope, cls, sport, picks in _scopes(by_class):
            prev = _prev(conn, scope, day)
            prev_t = Decimal(prev['threshold']) if prev is not None and prev['threshold'] is not None else None
            f = fit(picks, prev_t, min_n=MIN_N if sport is None else CELL_MIN, clamp=True)
            if sport is not None and (f.n < CELL_MIN or f.threshold is None):
                continue                        # the cell inherits its class
            carried = None
            if f.state in ('held', 'capped') and prev is not None:
                carried = prev['carried_from'] if prev['state'] == 'held' and prev['carried_from'] else prev['day']
            _insert(conn, day, scope, cls, sport, f, RULE_VERSION, carried, generated_at)
        for cls in CLASSES:                     # a class with no picks at all still records that it was not established
            if not conn.execute('SELECT 1 FROM card_thresholds WHERE day = ? AND scope = ?', (day, cls)).fetchone():
                _insert(conn, day, cls, cls, None, fit([], None), RULE_VERSION, None, generated_at)


def _line(row, day: str) -> Line:
    t = None if row['threshold'] is None else Decimal(row['threshold'])
    state = row['state'] if row['day'] == day or t is None else 'not refreshed'
    return Line(row['class'], row['sport'], t, row['n'], state, row['day'], row['since'], row['carried_from'],
                seed_to=row['until'], requested=None if row['requested'] is None else Decimal(row['requested']),
                reason=row['reason'])


def current(conn, day: str) -> Table:
    """The stored lines in force on `day`: each scope's newest row on or before the day. A class with no row is 'not
    established'; a row from an earlier day is 'not refreshed' (the job did not run or failed). Never computes T."""
    lines = {c: Line(c, None, None, 0, 'not established', day, None, None) for c in CLASSES}
    try:
        rows = conn.execute('SELECT t.* FROM card_thresholds t JOIN (SELECT scope, MAX(day) d FROM card_thresholds '
                            'WHERE day <= ? GROUP BY scope) l ON l.scope = t.scope AND l.d = t.day', (day,)).fetchall()
    except sqlite3.OperationalError:            # no store yet
        rows = []
    newest_class_day = {r['scope']: r['day'] for r in rows if r['sport'] is None}
    for r in rows:
        if r['sport'] is not None and r['day'] != newest_class_day.get(r['class']):
            continue                            # a cell that no longer qualified: its class's line applies
        lines[r['scope']] = _line(r, day)
    return Table(lines, day)


# --- Wording ---------------------------------------------------------------------------------------------------------------
def _md(day: Optional[str]) -> str:
    d = date.fromisoformat(day[:10])
    return f"{d.strftime('%b')} {d.day}"


def _span(a: Optional[str], b: Optional[str]) -> str:
    if not a or not b:
        return 'the stored window'
    da, db = date.fromisoformat(a[:10]), date.fromisoformat(b[:10])
    return f'{_md(a)}-{db.day}' if (da.year, da.month) == (db.year, db.month) else f'{_md(a)}-{_md(b)}'


def legend(line: Optional[Line], day: str) -> str:
    """The one-line legend; only 'set' and 'seed' say the class has beaten the price."""
    if line is None or line.threshold is None:
        return 'No threshold for this market yet'
    green = f'Green = {line.threshold}% and above'
    if line.state == 'set':
        return f'{green} · this class has beaten the price from here (n={line.n} priced, set {_md(line.day)})'
    if line.state == 'seed':
        return f'{green} · seeded from {_span(line.since, line.seed_to)} (n={line.n} priced)'
    if line.state == 'held':
        carried = _md(line.carried_from or line.day)
        if line.reason == 'no candidate beat the price':
            return f'{green} · line carried from {carried}, no line beat the price today (n={line.n} priced)'
        return f'{green} · line carried from {carried}, not re-fitted today (n={line.n} priced)'
    if line.state == 'capped':
        sign = '-' if line.requested is not None and line.requested < line.threshold else '+'
        return f'{green} · line capped at {sign}1.0 pt today, carried from {_md(line.carried_from or line.day)} (n={line.n} priced)'
    if line.state == 'not refreshed':
        yesterday = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
        which = "yesterday's line" if line.day == yesterday else 'the last line'
        return f'Threshold not refreshed today · using {which} (set {_md(line.day)})'
    return 'No threshold for this market yet'


def decide(sport: str, market: str, p, line_source: Optional[str], table: Table) -> dict:
    """{'colour': 'green'|'red'|None, 'class', 'legend', 'state', 'threshold'} for one pick. No colour (and no legend) when
    the market has no class or no stored price; no colour with the 'not established' legend when the class has no T."""
    cls = class_of(sport, market)
    if cls is None or (sport, market) in NO_STORED_PRICE or line_source == 'model':
        return {'colour': None, 'class': cls, 'legend': None, 'state': None, 'threshold': None}
    line = table.line_for(sport, cls)
    scope = f'{sport}:{cls}' if f'{sport}:{cls}' in table.lines else cls
    text = legend(line, table.day)
    if line is None or line.threshold is None:
        return {'colour': None, 'class': cls, 'scope': scope, 'legend': text, 'state': 'not established', 'threshold': None}
    return {'colour': colour_for(tenths(p), line.threshold), 'class': cls, 'scope': scope, 'legend': text, 'state': line.state,
            'threshold': str(line.threshold)}


def load_table(path, day: str) -> Table:
    """The lines in force on `day` from the store at `path` (read-only); every class 'not established' without a store."""
    from pathlib import Path
    if not Path(path).exists():
        return Table({c: Line(c, None, None, 0, 'not established', day, None, None) for c in CLASSES}, day)
    conn = connect(path, readonly=True)
    try:
        return current(conn, day)
    finally:
        conn.close()


# --- Days ------------------------------------------------------------------------------------------------------------------
def today(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(TORONTO).date().isoformat()


def day_start(day: str) -> str:
    """Toronto midnight of `day` in UTC ISO: picks must have settled strictly before it."""
    local = datetime.combine(date.fromisoformat(day), time(0), TORONTO)
    return local.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


# --- Priced picks from the stores ------------------------------------------------------------------------------------------
def _decimal_odds(american) -> Optional[Decimal]:
    if american is None:
        return None
    a = Decimal(str(american))
    if abs(a) < 100:
        return None
    return 1 + (a / 100 if a > 0 else Decimal(100) / -a)


def _us_picks(sport: str, cutoff: str) -> list:
    from . import config, coverage
    from .paper_trading import _quote
    path = config.db_path(sport)
    if not path.exists():
        return []
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    try:
        games = conn.execute("SELECT * FROM games WHERE status = 'final' AND result_available_at < ?", (cutoff,)).fetchall()
        out = []
        for market in [m for (s, m), c in CLASS_OF.items() if s == sport and c is not None]:
            for gid, c in coverage.canonical(conn, games, market, cutoff).items():
                rec = c.get('record')
                if c['state'] != 'forecast' or rec is None or c['grade'] not in ('win', 'loss', 'push'):
                    continue
                if market != 'winner' and rec['line_source'] != 'bookmaker':
                    continue                    # a model line has no stored price
                q = _quote(conn, gid, market, rec['line'], rec['generated_at'])
                side = rec['side']
                if q is None or side not in ('a', 'b'):
                    continue
                dec = _decimal_odds(q[0] if side == 'a' else q[1])
                if dec is None:
                    continue
                g = next(x for x in games if x['game_id'] == gid)
                out.append(Pick(f'{sport}:{gid}:{market}', sport, CLASS_OF[(sport, market)], tenths(rec['confidence']),
                                c['grade'], Decimal(1) / dec, g['result_available_at']))
        return out
    finally:
        conn.close()


def _soccer_picks(cutoff: str) -> list:
    from . import config, soccer_record
    path = config.db_path('soccer')
    if not path.exists():
        return []
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    try:
        latest: dict = {}
        for x in soccer_record.all_picks(conn, cutoff):
            if x['market'] not in ('1x2_regulation', 'goals_total') or not x['generated_at'] < x['tipoff']:
                continue
            k = (x['game_id'], x['market'])
            if k not in latest or x['generated_at'] > latest[k]['generated_at']:
                latest[k] = x
        out = []
        for x in latest.values():
            if x['grade'] not in ('win', 'loss', 'push'):
                continue
            match_id = x['game_id']
            m = conn.execute('SELECT result_available_at FROM soccer_matches WHERE match_id = ?', (match_id,)).fetchone()
            if m is None or not m['result_available_at'] or m['result_available_at'] >= cutoff:
                continue
            if x['market'] == '1x2_regulation':
                q = conn.execute("SELECT decimal_odds FROM soccer_odds_quotes WHERE match_id = ? AND market = '1x2' AND side = ? "
                                 "AND offered = 1 AND price_kind = 'current' AND retrieved_at <= ? "
                                 "ORDER BY retrieved_at DESC LIMIT 1", (match_id, x['outcome'], x['generated_at'])).fetchone()
            else:
                qc = x.get('quote_check') or {}
                if qc.get('line_source') != 'bookmaker':
                    continue
                q = conn.execute("SELECT decimal_odds FROM soccer_odds_quotes WHERE match_id = ? AND market = 'total_goals' "
                                 "AND side = ? AND line = ? AND offered = 1 AND price_kind = 'current' AND retrieved_at <= ? "
                                 "ORDER BY retrieved_at DESC LIMIT 1", (match_id, x['side'], x['line'], x['generated_at'])).fetchone()
            if q is None or q[0] is None:
                continue
            out.append(Pick(f"soccer:{match_id}:{x['market']}", 'soccer', CLASS_OF[('soccer', x['market'])],
                            tenths(x['probability']), x['grade'], Decimal(1) / Decimal(str(q[0])), m['result_available_at']))
        return out
    finally:
        conn.close()


def priced_picks(cutoff: str) -> dict:
    """{class: [Pick]} of every priced pick settled before `cutoff`, from the stores under NBA_DATA_DIR (read-only)."""
    by_class: dict = {c: [] for c in CLASSES}
    for p in [*(q for s in _US for q in _us_picks(s, cutoff)), *_soccer_picks(cutoff)]:
        by_class[p.cls].append(p)
    return by_class


def counts(by_class: dict) -> dict:
    return {c: {'priced': sum(1 for p in ps if p.result in ('win', 'loss')), 'pushes': sum(1 for p in ps if p.result == 'push'),
                'by_sport': {s: sum(1 for p in ps if p.sport == s and p.result in ('win', 'loss')) for s in SPORTS}}
            for c, ps in by_class.items()}


def routes() -> dict:
    """{sport: {market: class or None}} for the web twin (the frontend never decides which class a market is)."""
    out: dict = {}
    for (s, m), c in CLASS_OF.items():
        out.setdefault(s, {})[m] = None if (s, m) in NO_STORED_PRICE else c
    return out


def payload(conn, day: str, enabled: bool) -> dict:
    """GET /api/card-thresholds: the stored lines in force today, with their legends. Never computes T."""
    if not enabled:
        return {'enabled': False}
    table = current(conn, day) if conn is not None else Table(
        {c: Line(c, None, None, 0, 'not established', day, None, None) for c in CLASSES}, day)
    return {'enabled': True, 'date': day, 'routes': routes(), 'no_colour_line_source': 'model',
            'scopes': [{'scope': scope, 'class': ln.cls, 'sport': ln.sport,
                        'threshold': None if ln.threshold is None else str(ln.threshold), 'n': ln.n, 'state': ln.state,
                        'set_on': ln.day, 'legend': legend(ln, day)} for scope, ln in sorted(table.lines.items())]}


# --- Backtest (read-only description of history) ---------------------------------------------------------------------------
BACKTEST_LABEL = ('Description of history, not an improvement claim: the thresholds are fitted on the same few weeks they are '
                  'scored on (in-sample), and three weeks cannot validate an adaptive cut.')


def backtest(end_day: str, *, start: Optional[str] = None) -> dict:
    """Replays the daily rule day by day on the stores (read-only; an in-memory threshold table): day 0 seeds, later days
    run with the clamp. Each day's picks (priced, settled during that day) are coloured with that day's lines: how many are
    crossed out, and 1-unit P/L of every pick vs green only. Each day reads only picks settled before its own start."""
    days, picks_by_day = [], {}
    first = start
    if first is None:
        everything = priced_picks(day_start((date.fromisoformat(end_day) + timedelta(days=1)).isoformat()))
        settled = [p.settled_at for ps in everything.values() for p in ps]
        if not settled:
            return {'label': BACKTEST_LABEL, 'days': [], 'note': 'no priced, settled pick in the stores'}
        first = datetime.fromisoformat(min(settled).replace('Z', '+00:00')).astimezone(TORONTO).date().isoformat()
    d = date.fromisoformat(first)
    while d <= date.fromisoformat(end_day):
        days.append(d.isoformat())
        d += timedelta(days=1)
    for day in days + [(date.fromisoformat(days[-1]) + timedelta(days=1)).isoformat()]:
        picks_by_day[day] = priced_picks(day_start(day))
    mem = sqlite3.connect(':memory:')
    mem.row_factory = sqlite3.Row
    migrate(mem)
    out, totals = [], {c: {'shown': 0, 'crossed': 0, 'pl_all': Decimal(0), 'pl_green': Decimal(0)} for c in CLASSES}
    for i, day in enumerate(days):
        known = picks_by_day[day]
        if i == 0:
            seed(mem, day, known, generated_at=day_start(day))
        else:
            run(mem, day, lambda: known, generated_at=day_start(day))
        table = current(mem, day)
        before = {p.key for ps in known.values() for p in ps}
        row = {'day': day, 'classes': {}}
        for c in CLASSES:
            line = table.lines[c]
            shown = [p for p in picks_by_day[days[i + 1] if i + 1 < len(days) else
                                            (date.fromisoformat(day) + timedelta(days=1)).isoformat()][c]
                     if p.key not in before and p.result in ('win', 'loss')]
            def pl(ps):
                return sum(((Decimal(1) / p.implied - 1) if p.result == 'win' else Decimal(-1) for p in ps), Decimal(0))
            green = [p for p in shown
                     if (table.line_for(p.sport, c) or line).threshold is not None
                     and p.pct >= (table.line_for(p.sport, c) or line).threshold]
            crossed = [p for p in shown if (table.line_for(p.sport, c) or line).threshold is not None and p not in green]
            row['classes'][c] = {'threshold': None if line.threshold is None else str(line.threshold), 'state': line.state,
                                 'n_window': line.n, 'priced_known': sum(1 for p in known[c] if p.result in ('win', 'loss')),
                                 'shown': len(shown), 'crossed_out': len(crossed),
                                 'pl_all': str(pl(shown).quantize(Decimal('0.01'))),
                                 'pl_green_only': str(pl(green).quantize(Decimal('0.01'))) if line.threshold is not None else None,
                                 'cells': {k: str(v.threshold) for k, v in table.lines.items()
                                           if v.sport is not None and v.cls == c}}
            t = totals[c]
            t['shown'] += len(shown); t['crossed'] += len(crossed); t['pl_all'] += pl(shown)
            t['pl_green'] += pl(green) if line.threshold is not None else pl(shown)
        out.append(row)
    return {'label': BACKTEST_LABEL, 'first_day': days[0], 'last_day': days[-1],
            'note': 'The stores hold forecasts from Nov 15 (NBA/NHL/NFL), Nov 16 (Soccer) and Nov 17 (NCAAB); the Telegram cards '
                    'of Nov 2-14 have no stored price and cannot enter this rule.',
            'priced_counts_end': counts(picks_by_day[days[-1]]),
            'days': out,
            'totals': {c: {'shown': t['shown'], 'crossed_out': t['crossed'], 'pl_all': str(t['pl_all'].quantize(Decimal('0.01'))),
                           'pl_green_only_or_all_when_no_line': str(t['pl_green'].quantize(Decimal('0.01')))}
                       for c, t in totals.items()}}
