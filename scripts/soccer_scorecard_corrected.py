"""Corrected Soccer 1X2 sportsbook columns for research, beside the original scorecard values (owner request, Nov 17 2026).

Scorecard rows are append-only. Rows written before the fix took the last retrieval holding all three 1X2 sides, which was
stale against the card when only some sides had moved since. This export recomputes each saved row with the card's rule
(app/soccer_book.latest_1x2: latest offered row of each side retrieved at or before the forecast, before kickoff) and
keeps the original values. It reads the stores read-only and writes only --out.

Usage: python scripts/soccer_scorecard_corrected.py --data-dir DIR --out FILE.json
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app import soccer_book  # noqa: E402


def ro(path: Path) -> sqlite3.Connection:
    try:
        c = sqlite3.connect(f'file:{path.resolve()}?mode=ro', uri=True)
        c.execute('SELECT 1 FROM sqlite_master')
    except sqlite3.OperationalError:          # a WAL store with no -shm file cannot open mode=ro; read the file as is
        c = sqlite3.connect(f'file:{path.resolve()}?immutable=1', uri=True)
    c.row_factory = sqlite3.Row
    return c


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--data-dir', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    data = Path(a.data_dir)
    store, card = ro(data / 'soccer.sqlite'), ro(data / 'scorecard' / 'soccer.sqlite')
    rows, changed = [], 0
    for r in card.execute("SELECT * FROM scorecard WHERE market LIKE '1x2%' ORDER BY forecast_id"):
        q = soccer_book.latest_1x2(store, r['game_id'], r['generated_at'], r['tipoff_at'])
        new = None if q is None else q['devig'][r['side']]
        later = q is not None and max(q['side_retrieved_at'].values()) > r['generated_at']
        if later:                                   # defensive: latest_1x2 already stops at the forecast time
            raise SystemExit(f"forecast {r['forecast_id']}: a quote after the forecast was selected; refusing")
        diff = None if new is None or r['book_devig'] is None else round(100 * (new - r['book_devig']), 2)
        changed += bool(diff)
        rows.append({'forecast_id': r['forecast_id'], 'match_id': r['game_id'], 'side': r['side'],
                     'generated_at': r['generated_at'], 'tipoff_at': r['tipoff_at'], 'confidence': r['confidence'],
                     'original': {'book_devig': r['book_devig'], 'book_price': r['book_price'],
                                  'book_retrieved_at': r['book_retrieved_at'], 'book_state': r['book_state']},
                     'corrected': None if q is None else {'book_devig': new, 'book_price': q['price'][r['side']],
                                                         'book_retrieved_at': q['retrieved_at'],
                                                         'side_retrieved_at': q['side_retrieved_at']},
                     'difference_pts': diff,
                     'corrected_note': None if q is not None else
                     ('no kickoff time on the row' if not r['tipoff_at'] else 'no complete offered 1X2 price by the forecast')})
    out = {'rule': 'soccer_book.latest_1x2 (latest offered row per side at or before the forecast, before kickoff)',
           'rows': rows, 'summary': {'rows': len(rows), 'original_quoted': sum(x['original']['book_devig'] is not None for x in rows),
                                     'corrected_quoted': sum(x['corrected'] is not None for x in rows), 'changed': changed}}
    Path(a.out).write_text(json.dumps(out, indent=1))
    print(json.dumps(out['summary']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
