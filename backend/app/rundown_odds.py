"""Prices of the same pick at three sportsbooks (TheRundown), stored on disk and compared with the app's own quote.

Only fetch() uses the network. Reading never fetches, never changes a forecast, the app's stored quote, a ledger or
Telegram. The newest whole response for a sport and day replaces older ones (a price a book withdrew never comes back).
A price is only compared when the game, the side, the full-game period and the exact line all match the forecast.
Prices are facts as of the time shown; they are not predictions and not a value claim.
"""
import argparse
import fcntl
import json
import math
import os
import stat
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from . import config

BASE = 'https://therundown.io/api/v2'
SPORT_IDS = {'nba': 4, 'nhl': 6, 'nfl': 2}
BOOKS = {'19': 'DraftKings', '22': 'BetMGM', '23': 'FanDuel'}
MARKETS = {1: 'winner', 2: 'spread', 3: 'total'}
ALIASES = {'NYK': 'NY', 'GSW': 'GS', 'SAS': 'SA', 'NOP': 'NO', 'WAS': 'WSH', 'PHO': 'PHX',
           'TBL': 'TB', 'NJD': 'NJ', 'SJS': 'SJ', 'LAK': 'LA', 'MON': 'MTL', 'CLB': 'CBJ', 'NAS': 'NSH',
           'KCC': 'KC', 'GBP': 'GB', 'NEP': 'NE', 'NOS': 'NO', 'SFO': 'SF', 'TBB': 'TB', 'LVR': 'LV',
           'JAC': 'JAX', 'VEG': 'VGK'}
MAX_AGE = timedelta(hours=3)
DAY_REQUESTS, LOW_QUOTA = 40, 1000   # six requests a run (three sports, today and tomorrow), six scheduled runs a day


def stamp(now):
    return now.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def moment(value):
    d = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if d.tzinfo is None:
        raise ValueError('Timezone required')
    return d.astimezone(timezone.utc)


def canon(sport, value):
    value = str(value).upper()
    if sport == 'nba' and value == 'UTA':
        return 'UTAH'
    if sport == 'nhl' and value in ('UTAH_NHL', 'UTAH'):
        return 'UTA'
    return ALIASES.get(value, value)


def decimal(price):
    if isinstance(price, bool) or not isinstance(price, (float, int)) or not math.isfinite(price) or abs(price) < 100:
        return None
    return 1 + price / 100 if price > 0 else 1 + 100 / -price


def read_cache(now, directory=None):
    """The newest whole response per sport and day, if it is at most MAX_AGE old. Older responses are never merged in."""
    directory = Path(directory or config.data_dir() / 'odds-books')
    latest, errors = {}, []
    for path in sorted(directory.glob('*.json')):
        try:
            d = json.loads(path.read_text())
            at = moment(d['_fetched_at'])
            sport, day = d['_sport'], d['_date']
            if sport not in SPORT_IDS or not isinstance(d['events'], list) or at > now:
                raise ValueError('Invalid response metadata')
            if (sport, day) not in latest or at >= latest[sport, day][0]:
                latest[sport, day] = (at, d)
        except (ValueError, KeyError, OSError, TypeError):
            errors.append(path.name)
    return {'responses': [d for at, d in latest.values() if now - at <= MAX_AGE],
            'stale_responses': sum(now - at > MAX_AGE for at, d in latest.values()), 'invalid_files': errors}


def _side(pick):
    record = pick.get('percentage_history') or {}
    return record.get('side') if isinstance(record, dict) else getattr(record, 'side', None)


def comparison(sport, pick, cache, now):
    """{'status', 'checked_at', 'prices': {book: American price on the forecast's exact line}, 'best': {book, price},
    'other_lines': [{book, line, price}]}. 'prices' is empty unless status is 'available'."""
    q = pick.get('quote_check') or {}
    out = {'status': 'unavailable', 'source': 'TheRundown', 'checked_at': None, 'prices': {}, 'best': None, 'other_lines': []}
    if sport not in SPORT_IDS or pick.get('market') not in MARKETS.values() or q.get('line_source') == 'model':
        return dict(out, status='unsupported_market')
    if q.get('line_moved'):
        return dict(out, status='forecast_line_changed')
    side = _side(pick)
    if side not in ('a', 'b') or ' @ ' not in str(pick.get('matchup')):
        return dict(out, status='identity_unavailable')
    expected = q.get('line_at_forecast')
    if pick['market'] != 'winner' and expected is None:
        return dict(out, status='no_forecast_line')
    away, home = [canon(sport, x.strip()) for x in pick['matchup'].split(' @ ')]
    matches = []
    for response in cache['responses']:
        if response['_sport'] != sport:
            continue
        for event in response['events']:
            try:
                h = [t for t in event['teams'] if t.get('is_home')]
                a = [t for t in event['teams'] if t.get('is_away')]
                start = moment(event['event_date'])
                if (len(h) != 1 or len(a) != 1 or canon(sport, h[0]['abbreviation']) != home
                        or canon(sport, a[0]['abbreviation']) != away
                        or abs((start - moment(pick['tipoff'])).total_seconds()) > 300 or start <= now):
                    continue
                if (event.get('score') or {}).get('event_status') not in (None, 'STATUS_SCHEDULED'):
                    continue
                matches.append((response, event, h[0]['team_id'], a[0]['team_id']))
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
    if len({m[1].get('event_id') for m in matches}) != 1:
        return dict(out, status='ambiguous_event' if matches else 'no_current_event')
    response, event, home_id, away_id = max(matches, key=lambda m: m[0]['_fetched_at'])
    fetched = moment(response['_fetched_at'])
    out['checked_at'] = response['_fetched_at']
    exact, other = {}, []
    for market in event.get('markets') or []:
        try:
            if MARKETS.get(market.get('market_id')) != pick['market'] or market.get('period_id') != 0:
                continue
            for participant in market.get('participants') or []:
                name = str(participant.get('name') or '').lower()
                actual = ({'over': 'a', 'under': 'b'}.get(name) if pick['market'] == 'total' else
                          'a' if participant.get('id') == home_id else 'b' if participant.get('id') == away_id else None)
                if actual != side:
                    continue
                for line in participant.get('lines') or []:
                    if pick['market'] == 'winner':
                        value, same = None, True
                    else:
                        value = float(line['value'])
                        if not math.isfinite(value):
                            continue
                        if pick['market'] == 'spread' and side == 'b':
                            value = -value   # the app's spread line is always the HOME handicap
                        same = abs(value - expected) < 1e-9
                    for book, price in (line.get('prices') or {}).items():
                        if (book not in BOOKS or not isinstance(price, dict) or price.get('is_main_line') is not True
                                or price.get('closed_at') or decimal(price.get('price')) is None):
                            continue
                        updated = price.get('updated_at')
                        if updated and moment(updated) > fetched:
                            continue
                        if same:
                            exact.setdefault(BOOKS[book], []).append(int(price['price']))
                        else:
                            other.append(dict(book=BOOKS[book], line=value, price=int(price['price'])))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    # Two different prices from one book for the same side and line are ambiguous: that book is left out.
    out['prices'] = {book: found[0] for book, found in sorted(exact.items()) if len(set(found)) == 1}
    out['other_lines'] = sorted(other, key=lambda o: (o['book'], o['line']))
    if out['prices']:
        book = max(out['prices'], key=lambda b: decimal(out['prices'][b]))
        out['best'] = dict(book=book, price=out['prices'][book])
    out['status'] = 'available' if out['prices'] else 'no_exact_line'
    return out


def attach(sport, pick, cache, now):
    """pick['book_comparison']; the app's own stored quote (pick['quote_check']) is never changed. Never raises."""
    try:
        pick['book_comparison'] = comparison(sport, pick, cache, now)
    except Exception as exc:
        pick['book_comparison'] = {'status': 'error', 'error': type(exc).__name__, 'prices': {}, 'best': None, 'other_lines': []}


def credentials():
    key = os.environ.get('THERUNDOWN_API_KEY')
    if key:
        return key
    path = Path(os.environ.get('NBA_ODDS_CREDENTIALS', config.data_dir().parent / '.env.odds'))
    if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError('Odds credentials require a private regular file (chmod 600 .env.odds)')
    for line in path.read_text().splitlines():
        if line.startswith('THERUNDOWN_API_KEY='):
            key = line.split('=', 1)[1].strip()
            if key:
                return key
    raise ValueError('Odds API key unavailable')


def fetch(now=None, *, opener=urllib.request.urlopen, pause=time.sleep):
    """Today and tomorrow for three sports: six requests a run and at most DAY_REQUESTS a UTC day, one a second,
    no retries. Stops when the provider reports a low remaining allowance; the request caps hold when it reports none. Nothing printed or stored contains the
    key, a URL or an error message. Every response is saved whole, once, and never overwritten."""
    clock = (lambda: now) if now else (lambda: datetime.now(timezone.utc))
    started = clock()
    directory = config.data_dir() / 'odds-books'
    directory.mkdir(parents=True, exist_ok=True)
    key = credentials()
    with (directory / '.fetch.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return {'status': 'already_running', 'results': []}
        usage = directory / 'requests.jsonl'
        today = started.date().isoformat()
        count = sum(json.loads(x)['day'] == today for x in usage.read_text().splitlines() if x.strip()) if usage.exists() else 0
        results = []
        for sport, sid in SPORT_IDS.items():
            for offset in range(2):
                if count >= DAY_REQUESTS:
                    return {'status': 'request_cap', 'requests_today': count, 'results': results}
                day = (started + timedelta(days=offset)).date().isoformat()
                with usage.open('a') as f:
                    f.write(json.dumps({'day': today, 'at': stamp(clock()), 'sport': sport, 'date': day}) + '\n')
                count += 1
                url = f'{BASE}/sports/{sid}/events/{day}?market_ids=1,2,3&affiliate_ids=19,22,23&main_line=true&hide_closed=true'
                request = urllib.request.Request(url, headers={'X-TheRundown-Key': key, 'User-Agent': 'Courtside-odds'})
                try:
                    with opener(request, timeout=30) as response:
                        data = json.loads(response.read())
                        headers = {k: v for k, v in dict(response.headers).items() if k.lower().startswith('x-data')}
                    if not isinstance(data, dict) or not isinstance(data.get('events'), list):
                        raise ValueError('Response schema')
                    retrieved = clock()
                    data.update(_fetched_at=stamp(retrieved), _sport=sport, _date=day, _quota=headers)
                    dest = directory / f'{retrieved:%Y%m%dT%H%M%SZ}-{sport}-{day}-{uuid4().hex}.json'
                    tmp = dest.with_suffix('.tmp')   # a reader never sees half a file
                    tmp.write_text(json.dumps(data))
                    tmp.rename(dest)
                    results.append({'sport': sport, 'date': day, 'events': len(data['events']), 'quota': headers})
                    remaining = next((v for k, v in headers.items() if k.lower() == 'x-datapoints-remaining'), None)
                    if remaining is not None and int(remaining) < LOW_QUOTA:
                        return {'status': 'quota_low', 'results': results}
                except Exception as exc:
                    return {'status': 'failed', 'error': type(exc).__name__, 'results': results}
                pause(1.1)
        return {'status': 'ok', 'results': results}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['fetch', 'status'])
    args = parser.parse_args()
    if args.command == 'fetch':
        from . import release
        if config.protected_data_dir(config.data_dir()):
            # Only JSON files are written, no store: the checkout checks apply, the store-schema check does not.
            if not release.enabled() or release._checkout(release.code_root().resolve())[1]:
                raise SystemExit('release guard: price collection requires the clean release checkout')
        result = fetch()
        print(json.dumps(result))
        raise SystemExit(0 if result['status'] == 'ok' else 1)
    cache = read_cache(datetime.now(timezone.utc))
    print(json.dumps({'fresh_responses': len(cache['responses']), 'stale_responses': cache['stale_responses'],
                      'invalid_files': cache['invalid_files'],
                      'events': {f"{d['_sport']} {d['_date']}": len(d['events']) for d in cache['responses']}}))


if __name__ == '__main__':
    main()
