#!/usr/bin/env python3
"""Report of the preregistered best-pick rule (docs/preregistration/best-pick-2026-11-11-v4.md). Read-only: opens the
Telegram ledger and the sport stores read-only, changes nothing, sends nothing.

Counted card (v4 section 1): the earliest sent card of its game; recorded live by a recorder whose stored prereg_sha256 is
the v4 file's; sent at or after T0 for a game starting after T0; recorded at or after the recorder's independent review
(--reviewed-at, the time written in HANDOFF.md) by a reviewed recorder version (--reviewed-recorder, its recorder_sha256;
rows of any other version are excluded whole). Without --reviewed-at nothing counts.

No interim looks (v4 section 9): until a sport's first 100 decided named picks are complete, only counts are printed
(cards recorded, counted, named, decided). Then Tests A and B and the full-rate condition run once, exactly as v4
section 7. Win counts only: no profit claim follows.

Usage (from backend/, NBA_DATA_DIR set to the data folder):
  python ../scripts/best_picks.py LEDGER --reviewed-at 2026-11-12T03:00:00Z --reviewed-recorder <sha256> [--now ...]
"""
import argparse
import json
import math
import sqlite3
import sys
from typing import Optional
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app import best_pick_record as bpr  # noqa: E402

N_EVAL = 100
LIMIT = timedelta(days=7)
DEADLINE = '2027-06-30T23:59:00Z'
SEED = 20260930
PERMUTATIONS = 100_000
ALPHA = 0.05
SPORTS = ('nba', 'nhl')


def _t(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace('Z', '+00:00'))


# ---- statistics (v4 section 7) -------------------------------------------------------------------------------------------
def poisson_binomial_sf(w: int, qs: list) -> float:
    """P(X >= w), X = sum of independent Bernoulli(q_i), exact by convolution."""
    dist = [1.0]
    for q in qs:
        nxt = [0.0] * (len(dist) + 1)
        for k, p in enumerate(dist):
            nxt[k] += p * (1 - q)
            nxt[k + 1] += p * q
        dist = nxt
    return min(1.0, sum(dist[w:]))


def permutation_p(named_only: list, base_only: list, *, permutations: int = PERMUTATIONS, seed: int = SEED) -> tuple:
    """(D, p) for D = hit rate named-only minus baseline-only; one-sided, pooled values named-only first then baseline-only
    (each in send order), each reassignment one rng.permutation of the pooled array, first n_named_only = named group;
    p = (1 + #{D_perm >= D}) / (1 + permutations). Compared in integers (n2*s1 - n1*s2), so no rounding decides a tie."""
    import numpy as np
    n1, n2 = len(named_only), len(base_only)
    s1, s2 = sum(named_only), sum(base_only)
    stat = n2 * s1 - n1 * s2
    pooled = np.array(list(named_only) + list(base_only), dtype=np.int64)
    total = int(pooled.sum())
    rng = np.random.default_rng(seed)
    hits = 0
    for _ in range(permutations):
        a = int(rng.permutation(pooled)[:n1].sum())
        if n2 * a - n1 * (total - a) >= stat:
            hits += 1
    return Fraction(s1, n1) - Fraction(s2, n2), (1 + hits) / (1 + permutations)


def wilson(w: int, n: int, z: float = 1.959963984540054) -> tuple:
    if not n:
        return (None, None)
    p = w / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


# ---- grading (v4 section 6) ----------------------------------------------------------------------------------------------
def grade(conn, forecast_id: int, now: datetime) -> tuple:
    """(state, time) of a recorded forecast by the app's own settlement (markets.settle with that forecast's line):
    'win' / 'loss' / 'push' / 'void' / 'unscorable' with the time its result was first seen, 'pending' (not final, within
    7 days of its tipoff_at), or 'past_limit' (no final grade 7 days after tipoff_at) with that limit time."""
    from app import coverage, markets
    f = conn.execute('SELECT * FROM game_forecasts WHERE forecast_id=?', (forecast_id,)).fetchone()
    if f is None:
        return 'unscorable', None
    g = conn.execute('SELECT * FROM games WHERE game_id=?', (f['game_id'],)).fetchone()
    decision = None
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='game_decisions'").fetchone():
        r = conn.execute('SELECT decision FROM game_decisions WHERE game_id=?', (f['game_id'],)).fetchone()
        decision = r and r[0]
    real = markets.settle(f['market'], f['line'], status=g['status'], home=g['home_score'], away=g['away_score'],
                          decision=decision, tipoff_at=g['tipoff_at'], forecast_tipoff_at=f['tipoff_at'])
    limit = _t(f['tipoff_at']) + LIMIT
    if real == 'pending':
        return ('past_limit', limit) if now > limit else ('pending', None)
    keys = g.keys()
    seen = next((g[k] for k in ('result_first_seen_at', 'result_available_at') if k in keys and g[k]), None)
    seen_t = _t(seen) if seen else None
    if g['status'] == 'final' and seen_t is not None and seen_t > limit:
        return 'past_limit', limit
    if real in ('void', 'unscorable'):
        return real, seen_t
    side = coverage.pick_side(f['market'], (f['p_a'], f['p_mid'], f['p_b']))
    if real == 'mid' and f['market'] != 'reg3':
        return 'push', seen_t
    return ('win' if real == side else 'loss'), seen_t


def _review_gap(entries: list, row) -> Optional[str]:
    """Why no entry covers the row (None when one does)."""
    same = [e for e in entries if not e.get('code_commit') or e['code_commit'] == row['code_commit']]
    if not same:
        return 'code revision not reviewed'
    return None if any(row['recorded_at'] >= e['reviewed_at'] for e in same) else 'recorded before the independent review'


# ---- which cards count (v4 section 1) ------------------------------------------------------------------------------------
def counted_cards(ledger, sport: str, reviewed_at, reviewed) -> dict:
    """Every sent game card of the sport since T0, grouped by game, the earliest one deciding whether the game counts.
    `reviewed`: recorder sha256 values (all reviewed at `reviewed_at`), or {sha256: [{reviewed_at, code_commit}, ...]}, one
    entry per (recorder, code commit) with its own review time (code_commit None = any commit): a row counts when an entry
    of its recorder version names its code commit (or any) and was written at or before its recorded_at."""
    entries = reviewed if isinstance(reviewed, dict) else {
        sha: [{'reviewed_at': reviewed_at, 'code_commit': None}] for sha in reviewed}
    from app import telegram as tg
    rows = {r['delivery_key']: dict(r) for r in ledger.execute('SELECT * FROM best_picks WHERE sport=?', (sport,))} \
        if ledger.execute("SELECT 1 FROM sqlite_master WHERE name='best_picks'").fetchone() else {}
    head = f'<b>PROP51 PICKS · {sport.upper()}</b>'
    every = []          # every sent card, before T0 too: a game whose earliest card predates T0 does not count (review L1)
    for d in ledger.execute("SELECT delivery_key, text, attempted_at FROM deliveries WHERE status='sent' "
                            "ORDER BY attempted_at, delivery_key"):
        ident = tg.card_identity(d['text'])
        if ident and ident[0] == head:
            every.append((d['delivery_key'], d['attempted_at'], ident[:3]))
    sent = [s for s in every if s[1] >= bpr.T0]
    game_of_ident = {}
    for key, _, ident in every:
        if key in rows:
            game_of_ident.setdefault(ident, rows[key]['game_id'])
    games = {}
    for key, at, ident in every:
        gid = rows[key]['game_id'] if key in rows else game_of_ident.get(ident, ('unrecorded',) + ident)
        games.setdefault(gid, []).append((at, key))
    out = {'sent': len(sent), 'rows': len(rows), 'error_rows': sum(r['status'] != 'ok' for r in rows.values()),
           'rows_without_sent_card': len(set(rows) - {k for k, _, _ in every}),
           'sent_without_row': sum(k not in rows for k, _, _ in sent),
           'sent_without_row_after_review': sum(k not in rows and reviewed_at is not None and at >= reviewed_at
                                                for k, at, _ in sent),
           'excluded': {}, 'cards': []}
    for gid, cards in games.items():
        at, key = min(cards)
        r = rows.get(key)
        why = ('earliest card sent before T0' if at < bpr.T0 else
               'earliest card has no row' if r is None else
               'error row' if r['status'] != 'ok' else
               'preregistration hash differs' if r['prereg_sha256'] != bpr.PREREG_SHA256 else
               'no review time given' if reviewed_at is None else
               'recorded before the independent review' if r['recorded_at'] < reviewed_at else
               'recorder version not reviewed' if r['recorder_sha256'] not in entries else
               gap if (gap := _review_gap(entries[r['recorder_sha256']], r)) else
               'sent before T0' if r['recorded_at'] < bpr.T0 else
               'game starts before T0' if not (r['tipoff_at'] and _t(r['tipoff_at']) > _t(bpr.T0)) else None)
        if why:
            out['excluded'][why] = out['excluded'].get(why, 0) + 1
        else:
            out['cards'].append(r)
    out['cards'].sort(key=lambda r: (r['recorded_at'], r['delivery_key']))
    return out


def _pick(card: dict, fid) -> dict:
    return next(c for c in json.loads(card['picture'])['picks'] if c['forecast_id'] == fid)


def evaluate(store, cards: list, now: datetime) -> dict:
    """v4 section 7 for one sport. Before the evaluation set is complete: counts only."""
    named, out = [], {'cards_counted': len(cards), 'named': 0, 'named_decided': 0, 'baseline_decided': 0,
                      'excluded_lineup': sum(c['excluded_lineup'] or 0 for c in cards),
                      'excluded_avoid': sum(c['excluded_avoid'] or 0 for c in cards)}
    for i, c in enumerate(cards):
        if c['named_forecast_id'] is not None:
            named.append((i, c))
    out['named'] = len(named)
    grades = {}

    def g(fid):
        if fid not in grades:
            grades[fid] = grade(store, fid, now)
        return grades[fid]
    decided, cut, done_at, complete = [], None, None, False
    for i, c in named:
        state, when = g(c['named_forecast_id'])
        if state == 'pending':
            break
        if when is not None:
            done_at = when if done_at is None else max(done_at, when)
        if state in ('win', 'loss'):
            decided.append((i, c))
            if len(decided) == N_EVAL:
                cut, complete = i, True
                break
    out['named_decided'] = sum(g(c['named_forecast_id'])[0] in ('win', 'loss') for _, c in named)
    out['baseline_decided'] = sum(g(c['baseline_forecast_id'])[0] in ('win', 'loss') for c in cards
                                  if c['baseline_forecast_id'] is not None)
    deadline = _t(DEADLINE)
    if not complete or (done_at is not None and done_at > deadline):
        out['state'] = 'too few games: rule dropped' if (now > deadline or complete) else 'counting'
        return out                                                  # no wins, rates or excess before completion
    last = cards[cut]['recorded_at']
    base_cards = [c for c in cards if c['recorded_at'] <= last and c['baseline_forecast_id'] is not None]
    nset = []
    for _, c in decided:
        p = _pick(c, c['named_forecast_id'])
        nset.append({'fid': p['forecast_id'], 'win': int(g(p['forecast_id'])[0] == 'win'), 'q': p['book_raw'],
                     'qp': (p.get('quote') or {}).get('power')})
    bset, base_states = [], {}
    for c in base_cards:
        p = _pick(c, c['baseline_forecast_id'])
        state, when = g(p['forecast_id'])
        if done_at is not None and state != 'pending' and (when is None or when > done_at):
            state = 'pending_at_completion'   # review M1: graded as of the completion time, never with later results
        base_states[state] = base_states.get(state, 0) + 1
        if state in ('win', 'loss'):
            bset.append({'fid': p['forecast_id'], 'win': int(state == 'win'), 'q': p['book_raw'],
                         'qp': (p.get('quote') or {}).get('power')})
    named_states = {}
    for i, c in named:
        if i <= cut:
            s = g(c['named_forecast_id'])[0]
            named_states[s] = named_states.get(s, 0) + 1
    w = sum(x['win'] for x in nset)
    powered = [x for x in nset if x['qp'] is not None]
    p_prop = poisson_binomial_sf(w, [x['q'] for x in nset])
    p_pow = poisson_binomial_sf(sum(x['win'] for x in powered), [x['qp'] for x in powered]) if powered else None
    test_a = p_prop < ALPHA and p_pow is not None and p_pow < ALPHA
    shared = {x['fid'] for x in nset} & {x['fid'] for x in bset}
    n_only = [x['win'] for x in nset if x['fid'] not in shared]
    b_only = [x['win'] for x in bset if x['fid'] not in shared]
    if n_only and b_only:
        d, p_b = permutation_p(n_only, b_only)
        test_b = p_b < ALPHA
    else:
        d, p_b, test_b = None, None, False
    full_n = Fraction(w, len(nset))
    full_b = Fraction(sum(x['win'] for x in bset), len(bset)) if bset else None
    full_ok = full_b is not None and full_n >= full_b

    def excess(group, key):
        xs = [x['win'] - x[key] for x in group if x[key] is not None]
        return sum(xs) / len(xs) if xs else None
    ex = {k: {'named': excess(nset, k), 'baseline': excess(bset, k)} for k in ('q', 'qp')}
    for v in ex.values():
        v['difference'] = None if None in (v['named'], v['baseline']) else v['named'] - v['baseline']
    out.update(state='KEEP (may be proposed; still needs review and the owner\'s go)' if test_a and test_b and full_ok else 'DROP',
               W=w, n=len(nset), wilson95=wilson(w, len(nset)),
               mean_expectation_proportional=sum(x['q'] for x in nset) / len(nset),
               mean_expectation_power=sum(x['qp'] for x in powered) / len(powered) if powered else None,
               test_a={'p_proportional': p_prop, 'p_power': p_pow, 'pass': test_a,
                       'picks_without_power_expectation': len(nset) - len(powered)},
               test_b={'D': None if d is None else float(d), 'p': p_b, 'pass': test_b, 'named_only': len(n_only),
                       'baseline_only': len(b_only), 'shared': len(shared)},
               full_hit_rate={'named': float(full_n), 'baseline': None if full_b is None else float(full_b),
                              'named_at_least_baseline': full_ok, 'named_n': len(nset), 'baseline_n': len(bset)},
               excess_reported_only={'proportional': ex['q'], 'power': ex['qp']},
               named_states_through_cut=named_states, baseline_states=base_states,
               baseline_without_power=sum(x['qp'] is None for x in bset), cards_through_cut=len(base_cards))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('ledger')
    ap.add_argument('--reviewed-at', help="UTC time the recorder's independent review was written in HANDOFF.md")
    ap.add_argument('--reviewed-recorder', action='append', default=[], help='recorder_sha256 of a reviewed recorder')
    ap.add_argument('--now', help='evaluation time (default: now, UTC)')
    ap.add_argument('--store', action='append', default=[], help='sport=path (default: the data folder\'s store)')
    a = ap.parse_args(argv)
    now = _t(a.now) if a.now else datetime.now(timezone.utc)
    ledger = sqlite3.connect(f'file:{a.ledger}?mode=ro', uri=True)
    ledger.row_factory = sqlite3.Row
    paths = dict(s.split('=', 1) for s in a.store)
    print(f'Preregistration v4 sha256 {bpr.PREREG_SHA256[:12]}…, T0 {bpr.T0}. Evaluated {now:%Y-%m-%dT%H:%M:%SZ}.')
    if a.reviewed_at is None:
        print('No independent review time given (--reviewed-at): no card counts yet.')
    for sport in SPORTS + ('nfl',):
        info = counted_cards(ledger, sport, a.reviewed_at, set(a.reviewed_recorder))
        print(f"\n{sport.upper()}: game cards sent since T0 {info['sent']}; rows {info['rows']} (error rows "
              f"{info['error_rows']}); cards sent without a row {info['sent_without_row']} (after the review "
              f"{info['sent_without_row_after_review']}); rows without a sent card {info['rows_without_sent_card']}.")
        if sport == 'nfl':
            print('NFL: record only, no pick is named (v4 section 8).')
            continue
        print(f"  Games not counted: {json.dumps(info['excluded'], sort_keys=True)}")
        if not info['cards']:
            print('  No counted card.')
            continue
        from app import config
        store = sqlite3.connect(f"file:{paths.get(sport) or config.db_path(sport)}?mode=ro", uri=True)
        store.row_factory = sqlite3.Row
        out = evaluate(store, info['cards'], now)
        print('  ' + json.dumps(out, sort_keys=True, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
