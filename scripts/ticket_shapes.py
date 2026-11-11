#!/usr/bin/env python3
"""Paper replay of every ticket the app has sent, under the ticket-shape rules (app.tickets.SHAPE_FLOORS).

Read-only: opens the Telegram ledger and nothing else, sends nothing, changes nothing. For each sent ticket it shows how
it finished, what its safety version (the same ticket without its weakest leg) would have done at the stored prices, and
whether today's shape rules would have allowed it. One unit staked on each. Stored prices, not prices anyone got; this is
not betting profit. With few tickets the totals mean very little: the count is printed first.

Usage (from backend/):  python ../scripts/ticket_shapes.py [path to .telegram-delivery.sqlite]
"""
import json
import math
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app import tickets  # noqa: E402


def load(path):
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    grades = {}
    for row in conn.execute('SELECT ticket_id, as_of, payload FROM ticket_grades ORDER BY as_of, rowid'):
        grades[row['ticket_id']] = json.loads(row['payload'])       # the latest grade of each ticket wins
    seen, out = set(), []
    for row in conn.execute('SELECT payload FROM ticket_batches ORDER BY created_at, rowid'):
        for ticket in json.loads(row['payload']).get('resolved', []):
            if ticket['ticket_id'] in seen:
                continue
            seen.add(ticket['ticket_id'])
            out.append((ticket, grades.get(ticket['ticket_id'])))
    return out


def result(legs, graded):
    """('win'|'loss'|'pending'|'void', payout or None) for these legs, from the graded legs of the same ticket."""
    by_id = {(g['sport'], g['forecast_id']): g['grade'] for g in graded}
    states = [by_id.get((leg['sport'], leg['forecast_id']), 'pending') for leg in legs]
    if 'loss' in states:
        return 'loss', None
    if any(s not in ('win', 'push', 'void') for s in states):
        return 'pending', None
    live = [leg for leg, s in zip(legs, states) if s == 'win']
    if not live:
        return 'void', None
    prices = [tickets.decimal_odds((leg.get('quote_check') or {}).get('price_now')) for leg in live]
    return 'win', math.prod(prices) if all(prices) else None


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('NBA_TELEGRAM_LEDGER', '.telegram-delivery.sqlite')
    rows = load(path)
    print(f'{len(rows)} tickets sent in all. Stored prices; one unit on each; not betting profit.\n')
    totals = {'full': [0, 0, 0.0, 0], 'safety': [0, 0, 0.0, 0]}      # settled, won, returned, wins without a full price
    refused = lost_by_one = 0
    for ticket, grade in rows:
        legs = ticket['legs']
        shape = tickets.ticket_shape(legs)
        problems = tickets.shape_problems(legs)
        refused += bool(problems)
        graded = (grade or {}).get('legs', [])
        full = result(legs, graded)
        losses = sum(g['grade'] == 'loss' for g in graded)
        lost_by_one += full[0] == 'loss' and losses == 1
        rest = [leg for leg in legs if leg['pick_label'] != shape['weakest']]
        safety = result(rest, graded) if len(rest) >= tickets.MIN_LEGS else ('none', None)
        for name, (state, payout) in (('full', full), ('safety', safety)):
            if state in ('win', 'loss'):
                totals[name][0] += 1
                totals[name][1] += state == 'win'
                totals[name][2] += payout or 0
                totals[name][3] += state == 'win' and payout is None
        pay = lambda r: f"{r[0]}" + (f" x{r[1]:.2f}" if r[1] else '')
        print(f"{ticket['ticket_id']}  {len(legs)} legs  {pay(full):12}  safety: {pay(safety):12}  "
              f"{shape['weakest_line']}  |  {shape['all_land_line']}" + (f"  |  WOULD BE REFUSED: {problems[0]}" if problems else ''))
    print()
    for name, (settled, won, returned, unpriced) in totals.items():
        if settled:
            print(f"{name:7} settled {settled}, won {won}, returned {returned:.2f} units for {settled} staked"
                  + (f" ({unpriced} wins had no full stored price and count as 0)" if unpriced else ''))
    print(f"lost by exactly one leg: {lost_by_one}. Refused by today's shape rules: {refused} of {len(rows)}.")
    if totals['full'][0] < 100:
        print('Too few settled tickets to conclude anything about which shape is better.')


if __name__ == '__main__':
    main()
