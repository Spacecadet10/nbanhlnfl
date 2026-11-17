"""Additional sportsbook markets from The Odds API, isolated from forecast inputs."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import re
import stat
import unicodedata
from uuid import uuid4

import httpx

from . import config, db, soccer, soccer_markets

HOST = 'https://api.the-odds-api.com/v4'
SPORT_KEYS = {'nba': 'basketball_nba', 'nhl': 'icehockey_nhl', 'nfl': 'americanfootball_nfl'}
COMPETITION_KEYS = {
    'eng.1': 'soccer_epl', 'esp.1': 'soccer_spain_la_liga', 'ger.1': 'soccer_germany_bundesliga',
    'ita.1': 'soccer_italy_serie_a', 'fra.1': 'soccer_france_ligue_one',
    'uefa.champions': 'soccer_uefa_champs_league', 'uefa.europa': 'soccer_uefa_europa_league',
    'uefa.nations': 'soccer_uefa_nations_league',
}
MARKETS = {
    'team_total_home': 'team_totals', 'team_total_away': 'team_totals',
    'goals_home': 'team_totals', 'goals_away': 'team_totals', 'btts': 'btts',
    'corners_total': 'alternate_totals_corners', 'corners_handicap': 'alternate_spreads_corners',
    'corners_home': 'alternate_team_totals_corners', 'corners_away': 'alternate_team_totals_corners',
}
MAX_AGE = timedelta(minutes=90)
DAILY_CREDITS = 12


def _now():
    return datetime.now(timezone.utc)


def _time(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


def _stamp(time):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ')


def _team(text):
    ascii_name = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]', '', ascii_name.lower())


def _credentials():
    if os.environ.get('THE_ODDS_API_KEY'):
        return os.environ['THE_ODDS_API_KEY']
    file = config.data_dir().parent / '.env.the-odds-api'
    if file.is_symlink() or not file.is_file() or stat.S_IMODE(file.stat().st_mode) & 0o077:
        return None
    for line in file.read_text().splitlines():
        if line.startswith('THE_ODDS_API_KEY='):
            return line.partition('=')[2].strip()
    return None


def _cache_path(sport, game_id):
    if sport not in (*SPORT_KEYS, 'soccer') or not re.fullmatch(r'[a-zA-Z0-9_.:-]+', game_id):
        raise ValueError('Invalid event identity')
    return config.data_dir() / 'additional-odds' / sport / f'{game_id}.json'


def _read(sport, game_id):
    try:
        return json.loads(_cache_path(sport, game_id).read_text())
    except (ValueError, OSError):
        return None


def _games(now):
    start, end = _stamp(now), _stamp(now + timedelta(hours=3))
    for sport in (*SPORT_KEYS, 'soccer'):
        if not config.db_path(sport).exists():
            continue
        with closing(db.connect_readonly(sport)) as conn:
            if sport == 'soccer':
                sql = soccer.MATCH_SQL + " WHERE m.status='scheduled' AND m.kickoff_at>? AND m.kickoff_at<=?"
                for row in conn.execute(sql, (start, end)):
                    key = COMPETITION_KEYS.get(row['competition_id'])
                    if key:
                        yield sport, key, row['match_id'], row['kickoff_at'], row['home_name'], row['away_name']
            else:
                sql = ("SELECT g.game_id,g.tipoff_at,h.name,a.name FROM games g "
                       "JOIN teams h ON h.team_id=g.home_team_id JOIN teams a ON a.team_id=g.away_team_id "
                       "WHERE g.status='scheduled' AND g.tipoff_at>? AND g.tipoff_at<=?")
                for row in conn.execute(sql, (start, end)):
                    yield sport, SPORT_KEYS[sport], *row


def _matches(event, kickoff, home, away):
    try:
        return (_team(event['home_team']) == _team(home) and _team(event['away_team']) == _team(away)
                and abs(_time(event['commence_time']) - _time(kickoff)) <= timedelta(minutes=5))
    except (TypeError, ValueError, KeyError, AttributeError):
        return False


def collect(*, limit=2):
    key = _credentials()
    if not key:
        return {'status': 'missing_key', 'saved': 0, 'failed': 0}
    directory = config.data_dir() / 'additional-odds'
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / '.fetch.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'already_running', 'saved': 0, 'failed': 0}
        return _collect(key, limit, directory / "budget.json")


def _collect(key, limit, budget_path):
    now = _now()
    day = now.date().isoformat()
    budget = {"day": day, "used": 0, "quota_low": False}
    if budget_path.exists():
        previous_budget = json.loads(budget_path.read_text())
        if previous_budget["day"] == day:
            budget = previous_budget
    if budget["quota_low"] or budget["used"] >= DAILY_CREDITS:
        return {"status": "budget_reached", "saved": 0, "failed": 0, "credits_today": budget["used"]}

    def save_budget():
        temporary = budget_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(budget))
        temporary.replace(budget_path)

    calendars = {}
    saved = failed = requests = 0
    with httpx.Client(base_url=HOST, headers={'x-api-key': key}, timeout=12) as client:
        for sport, league, game_id, start, home, away in sorted(_games(now), key=lambda game: game[3]):
            previous = _read(sport, game_id)
            if previous and previous.get('kickoff') == start and _time(previous['retrieved_at']) > now - MAX_AGE:
                continue
            try:
                if league not in calendars:
                    response = client.get(f'/sports/{league}/events')
                    response.raise_for_status()
                    calendars[league] = response.json()
                matches = [e for e in calendars[league] if _matches(e, start, home, away)]
                if len(matches) != 1:
                    continue
                event_id = matches[0]['id']
                requested = sorted(set(MARKETS.values())) if sport == 'soccer' else ['team_totals']
                reserved = len(requested)  # one credit per returned market, requesting a single region
                if budget["used"] + reserved > DAILY_CREDITS:
                    continue
                budget["used"] += reserved
                save_budget()
                requests += 1
                response = client.get(f'/sports/{league}/events/{event_id}/odds',
                                      params={'regions': 'uk' if sport == 'soccer' else 'us',
                                              'markets': ','.join(requested), 'oddsFormat': 'decimal'})
                response.raise_for_status()
                actual = int(response.headers.get('x-requests-last', reserved))
                budget["used"] += max(0, actual) - reserved
                budget["quota_low"] = int(response.headers.get('x-requests-remaining', '1000')) < 6
                save_budget()
                event = response.json()
                retrieved = _now()
                if event.get('id') != event_id or not _matches(event, start, home, away):
                    raise ValueError('Event identity changed')
                if retrieved < _time(start):
                    path = _cache_path(sport, game_id)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    temp = path.with_suffix(f'.{uuid4().hex}.tmp')
                    temp.write_text(json.dumps({'kickoff': start, 'retrieved_at': _stamp(retrieved), 'event': event}))
                    temp.replace(path)
                    saved += 1
                if budget["quota_low"]:
                    break
            except httpx.HTTPStatusError as exc:
                failed += 1
                if exc.response.status_code in (401, 403, 429):
                    break
                if league not in calendars:
                    calendars[league] = []
            except (httpx.RequestError, ValueError, KeyError, TypeError):
                failed += 1
                if league not in calendars:
                    calendars[league] = []
            if requests >= limit:
                break
    return {'status': 'failed' if failed else 'ok', 'saved': saved, 'failed': failed, 'credits_today': budget['used']}


def _pair(event, market, side, line, moment):
    key = MARKETS[market]
    team = event['home_team'] if market.endswith('_home') else event['away_team'] if market.endswith('_away') else None
    names = ('yes', 'no') if market == 'btts' else ('over', 'under')
    points = (line, line)
    if market == 'corners_handicap':
        names = (event[f'{side}_team'].casefold(), event[f'{"away" if side == "home" else "home"}_team'].casefold())
        points = (line, -line)
        side = names[0]
    for bookmaker in event.get('bookmakers') or []:
        for offer in bookmaker.get('markets') or []:
            if offer.get('key') != key or not offer.get('last_update'):
                continue
            if not timedelta() <= moment - _time(offer['last_update']) <= MAX_AGE:
                continue
            odds = {}
            for name, point in zip(names, points):
                values = {o['price'] for o in offer.get('outcomes') or []
                          if str(o.get('name', '')).casefold() == name and o.get('point') == point
                          and (team is None or _team(o.get('description') or '') == _team(team))
                          and isinstance(o.get('price'), (int, float))
                          and math.isfinite(o['price']) and o['price'] > 1}
                if len(values) == 1:
                    odds[name] = 1 / values.pop()
            if set(odds) == set(names) and side in odds:
                return odds[side] / sum(odds.values()), bookmaker.get('title') or 'The Odds API'
    return None


def comparison(sport, pick, as_of, *, side, line):
    """Return a display-only comparison for a complete, current quote at the exact forecast line."""
    market = pick['market']
    kickoff = pick['tipoff']
    if sport not in (*SPORT_KEYS, 'soccer'):   # NCAAB (and any unknown sport): none of the shared 500 free credits a month is spent on it
        return {}
    if market not in MARKETS or not side or as_of >= kickoff or pick['status'] != 'scheduled':
        return {}
    cached = _read(sport, pick['game_id'])
    if not cached or cached.get('kickoff') != kickoff:
        return {}
    try:
        now = _time(as_of)
        if not timedelta() <= now - _time(cached['retrieved_at']) <= MAX_AGE:
            return {}
        pair = _pair(cached['event'], market, side, line, now)
        if pair is None:
            return {}
        book, provider = pair
        result = {'card_market_prob': book, 'card_comparison_basis': f'{provider} pregame quote'}
        if sport == 'soccer' and market != 'btts':
            probs = pick['outcome_probabilities']
            win = soccer_markets.win_share(probs)
            loss = probs['loss'] + probs['half_loss'] / 2
            if win + loss == 0:
                return {}
            if probs['push'] or probs['half_win'] or probs['half_loss']:
                result.update(card_app_prob=win / (win + loss),
                              card_comparison_basis=f'{provider}, adjusted for settlement')
        elif sport != 'soccer' and (line is None or line % 1 != .5):
            return {}
        return result
    except (TypeError, ValueError, KeyError, OverflowError):
        return {}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('fetch',))
    parser.add_argument('--limit', type=int, choices=range(1, 13), default=2)
    args = parser.parse_args()
    result = collect(limit=args.limit)
    print(json.dumps(result))
    raise SystemExit(0 if result['status'] in ('ok', 'already_running', 'budget_reached') else 1)
