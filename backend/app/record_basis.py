"""`record_basis`: which forecast an exact-percentage record counts, and what else exists for the same game and market
(card contract item 4, Nov 17 2026). Display only; nothing here changes a forecast, a grade or a record.

- The record counts the LATEST pregame forecast (coverage._select); earlier pregame forecasts of the same game and market are
  kept in game_forecasts and listed here for audit.
- The Telegram card is sent from the forecast the app had at send time; `published` is that forecast, read from the delivery
  ledger (app/published_picks.py, read-only, absent = None), with its own grade under the app's settlement rules.
NBA/NHL/NCAAB: providers/real.py. NFL: wrapper in app/nfl.py and telegram.run_once (nfl_live.py is sealed). Soccer: app/soccer_record.py.
"""
from typing import Optional

from . import coverage, published_picks


def _item(row, market: str) -> dict:
    p = (row['p_a'], row['p_mid'], row['p_b'])
    return {'forecast_id': row['forecast_id'], 'generated_at': row['generated_at'], 'side': coverage.pick_side(market, p),
            'line': row['line'], 'probability': row['confidence'], 'model_version': row['model_version']}


def pregame_forecasts(conn, game_id: str, market: str, as_of: Optional[str] = None) -> list:
    """Every forecast of the game and market made before its start (and by `as_of`), oldest first."""
    sql = ("SELECT * FROM game_forecasts WHERE game_id=? AND market=? AND status='forecast' AND generated_at < tipoff_at"
           + (" AND generated_at <= ?" if as_of else "") + " ORDER BY generated_at, forecast_id")
    return conn.execute(sql, (game_id, market) + ((as_of,) if as_of else ())).fetchall()


def published_block(conn, sport: str, game, market: str, published: Optional[dict], as_of: str) -> Optional[dict]:
    """The card's published forecast with its grade; None when the game and market were never published."""
    row = (published or {}).get((game['game_id'], market))
    if row is None:
        return None
    return {'forecast_id': published_picks.row_id(row['forecast_id']) if sport == 'soccer' else int(row['forecast_id']),
            'generated_at': row['generated_at'], 'side': row['side'], 'line': row['line'], 'probability': row['probability'],
            'model_version': row['model_version'], 'sent_at': row['sent_at'], 'message_id': row['message_id'],
            'grade': published_picks.grade(conn, sport, row, game, as_of)}


def build(conn, sport: str, game, market: str, rec: dict, published: Optional[dict], as_of: str) -> Optional[dict]:
    """record_basis for the canonical record `rec` of one game and market (coverage._canonical_one's record)."""
    if rec is None:
        return None
    rows = pregame_forecasts(conn, game['game_id'], market, as_of)
    items = [_item(r, market) for r in rows]
    earlier = [x for x in items if x['forecast_id'] != rec['forecast_id']]
    return {'forecast_id': rec['forecast_id'], 'generated_at': rec['generated_at'], 'model_version': rec['model_version'],
            'side': rec['side'], 'line': rec['line'], 'probability': rec['confidence'],
            'pregame_forecasts': max(len(items), 1), 'earlier': earlier,
            'published': published_block(conn, sport, game, market, published, as_of)}


def decorate_report(conn, sport: str, report: dict, published: Optional[dict] = None) -> None:
    """Adds line / side / model_version / record_basis to every pick of a ranked-picks report (in place), for sports whose
    picks are built outside providers/real.py (NFL, nfl_live.ranked_picks). Never raises: a pick keeps what it has."""
    try:
        published = published if published is not None else published_picks.load(sport)
        for pick in report['picks']:
            try:
                f = conn.execute('SELECT * FROM game_forecasts WHERE forecast_id=?', (pick['forecast_id'],)).fetchone()
                game = conn.execute('SELECT * FROM games WHERE game_id=?', (pick['game_id'],)).fetchone()
                if f is None or game is None:
                    continue
                p = (f['p_a'], f['p_mid'], f['p_b'])
                rec = {'forecast_id': f['forecast_id'], 'generated_at': f['generated_at'], 'model_version': f['model_version'],
                       'side': coverage.pick_side(f['market'], p), 'line': f['line'], 'confidence': f['confidence'],
                       'line_source': f['line_source'], 'market_quote_id': f['market_quote_id'], 'p': list(p)}
                pick.setdefault('line', rec['line'])
                pick.setdefault('side', rec['side'])
                pick.setdefault('model_version', rec['model_version'])
                pick.setdefault('record_basis', build(conn, sport, game, pick['market'], rec, published, report['as_of']))
                if pick.get('sportsbook') is None:
                    from .sportsbook import sportsbook_comparison
                    out = sportsbook_comparison(conn, sport, game, pick['market'], rec)
                    pick['sportsbook'] = None if out is None else out.model_dump(mode='json')
                history = pick.get('percentage_history')
                if isinstance(history, dict) and not (history.get('coverage') or {}).get('live_since'):
                    from . import threshold_overview as to
                    history['coverage'] = to.coverage_note(sport, to.live_since(conn))
            except Exception:
                continue
    except Exception:
        return
