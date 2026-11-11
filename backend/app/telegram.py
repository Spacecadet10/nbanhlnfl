"""Personal Telegram delivery. Reads canonical forecasts; writes only a separate delivery receipt ledger.

No training, ingestion, forecast changes, historical recommendations, or subscribers other than the paired private chat.
"""
import hashlib
import json
import os
import secrets
import sqlite3
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from . import config

TORONTO = ZoneInfo('America/Toronto')


class DeliveryError(Exception):
    def __init__(self, message, *, retry_after=None, uncertain=False):
        super().__init__(message)
        self.retry_after, self.uncertain = retry_after, uncertain


class Telegram:
    def __init__(self, token, *, transport=None):
        if not token:
            raise ValueError('Telegram bot token is not configured.')
        self._token = token
        self._transport = transport

    def call(self, method, payload=None, *, photo=None):
        # Never propagate HTTP exceptions: their URL contains the token.
        try:
            with httpx.Client(timeout=15, follow_redirects=False, trust_env=False, transport=self._transport) as client:
                url = f'https://api.telegram.org/bot{self._token}/{method}'
                if photo is None:
                    response = client.post(url, json=payload or {})
                else:
                    response = client.post(url, data=payload or {}, files={'photo': ('prop51-card.png', photo, 'image/png')})
                body = response.json()
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise DeliveryError('Telegram connection unavailable.', retry_after=60) from None
        except (httpx.HTTPError, ValueError):
            raise DeliveryError('Telegram response not confirmed; review delivery before retrying.', uncertain=True) from None
        if response.status_code >= 500:
            raise DeliveryError('Telegram server response not confirmed.', uncertain=True)
        if not body.get('ok'):
            retry = body.get('parameters', {}).get('retry_after') if response.status_code == 429 else None
            raise DeliveryError(f'Telegram rejected {method} (HTTP {response.status_code}).', retry_after=retry)
        return body['result']

    def identity(self):
        me = self.call('getMe')
        if not me.get('is_bot') or me.get('username', '').lower() != 'prop51picksbot':
            raise DeliveryError('The token does not belong to @Prop51picksbot.')
        if self.call('getWebhookInfo').get('url'):
            raise DeliveryError('This bot has an active webhook; it was not changed.')
        return me['username']


@contextmanager
def ledger(path=None):
    path = Path(path or config.telegram_ledger_path())
    if path.is_symlink():
        raise ValueError('Telegram delivery ledger must not be a symlink.')
    fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(fd)
    conn = sqlite3.connect(path, timeout=5, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS deliveries (
          delivery_key TEXT PRIMARY KEY, chat_id TEXT NOT NULL, text TEXT NOT NULL,
          status TEXT NOT NULL CHECK(status IN ('sending','sent','retry','failed','uncertain')),
          attempted_at TEXT NOT NULL, next_attempt_at TEXT, message_id INTEGER, error TEXT);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    ''')
    try:
        yield conn
    finally:
        conn.close()


def _get(conn, key, default=''):
    row = conn.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return row[0] if row else default


def _set(conn, key, value):
    conn.execute('INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(value)))


def pairing_link(settings):
    code = settings.get('TELEGRAM_PAIR_CODE') or secrets.token_urlsafe(24)
    if not settings.get('TELEGRAM_PAIR_CODE'):
        config.save_telegram_setting('TELEGRAM_PAIR_CODE', code)
    return 'https://t.me/Prop51picksbot?start=' + code


def connect(bot, settings):
    """Only a private /start carrying this session's pairing code can choose the destination."""
    code = settings.get('TELEGRAM_PAIR_CODE')
    if not code:
        return {'connected': False, 'pairing_link': pairing_link(settings)}
    updates = bot.call('getUpdates', {'timeout': 0, 'limit': 100, 'allowed_updates': ['message']})
    chats = {str(m['chat']['id']) for u in updates for m in [u.get('message', {})]
             if m.get('chat', {}).get('type') == 'private' and not m.get('from', {}).get('is_bot')
             and m.get('text') == '/start ' + code}
    current = settings.get('TELEGRAM_CHAT_ID')
    if current:
        return {'connected': True}
    if len(chats) > 1:
        raise DeliveryError('Multiple chats used the pairing link; destination needs review.')
    if not chats:
        return {'connected': False, 'pairing_link': pairing_link(settings)}
    config.save_telegram_setting('TELEGRAM_CHAT_ID', chats.pop())
    return {'connected': True}


def controls(bot, settings, conn):
    updates = bot.call('getUpdates', {'offset': int(_get(conn, 'offset', '0')), 'timeout': 0,
                                     'limit': 100, 'allowed_updates': ['message']})
    for update in updates:
        message = update.get('message', {})
        if (message.get('chat', {}).get('type') == 'private'
                and str(message['chat']['id']) == settings['TELEGRAM_CHAT_ID']):
            command = message.get('text', '').split(' ', 1)[0].lower()
            if command in ('/start', '/stop'):
                _set(conn, 'enabled', '1' if command == '/start' else '0')
        _set(conn, 'offset', update['update_id'] + 1)
    return _get(conn, 'enabled', '1') == '1'


def message_batches(report, chat_id):
    """One summary per game/version, using the same backend best-pick ranking and exact-percentage record as the UI."""
    now = datetime.fromisoformat(report['as_of'].replace('Z', '+00:00'))
    games = {}
    for pick in report['picks']:
        start = datetime.fromisoformat(pick['tipoff'].replace('Z', '+00:00'))
        generated = datetime.fromisoformat(pick['generated_at'].replace('Z', '+00:00'))
        if (pick['status'] != 'scheduled' or pick['grade'] != 'pending' or not now < start <= now + timedelta(hours=2)
                or not start - timedelta(hours=2) <= generated <= now):
            continue
        games.setdefault(pick['game_id'], []).append(pick)
    batches = []
    for gid, picks in games.items():
        batch = format_batch(report, chat_id, gid, picks)
        if batch:
            batches.append(batch)
    return batches


def format_batch(report, chat_id, gid, picks, *, preview=False):
    """One shared message layout. Preview retains actual past times/results and is prominently labelled."""
    from .threshold_overview import displayed_percentage
    now = datetime.fromisoformat(report['as_of'].replace('Z', '+00:00'))
    from . import sports
    order = {market: i for i, market in enumerate(sports.store(report['sport']).markets)}
    picks = sorted(picks, key=lambda p: order.get(p['market'], len(order)))
    start = datetime.fromisoformat(picks[0]['tipoff'].replace('Z', '+00:00')).astimezone(TORONTO)
    strong = sorted((p for p in picks if p['strong']), key=lambda p: p['game_rank'])
    divider = '━━━━━━━━━━━━━━'
    lines = (['<b>TEST PREVIEW — PAST GAME, NOT A LIVE PICK</b>', ''] if preview else []) + [
        f"<b>PROP51 PICKS · {escape(report['sport'].upper())}</b>",
        f"<b>{escape(picks[0]['matchup'])}</b>",
        f"{'Started' if preview else 'Starts'} {start:%b %d, %I:%M %p %Z}", '', divider,
        '<b>Recommended pick</b>',
        '<b>' + escape(strong[0]['pick_label'] if strong else 'No strong pick') + '</b>']
    if strong:
        lines.append(escape(strong[0]['market_title']) + ' · Model ' + displayed_percentage(strong[0]['probability']) + '%')
    lines += ['<i>' + ('App ranking for this past game.' if preview else 'Based on the app’s existing ranking.') + '</i>',
              divider, '', '<b>Market breakdown</b>', '']
    versions = []
    for pick in picks:
        h = pick.get('percentage_history')
        if h is None:
            continue   # absent history must not be manufactured as a zero record
        if hasattr(h, 'model_dump'):
            h = h.model_dump(mode='json')
        if (h['sport'] != report['sport'] or h['market'] != pick['market']
                or h['percentage'] != displayed_percentage(pick['probability'])):
            raise DeliveryError('Pick market/probability does not match its history; not sent.')
        versions.append([pick['market'], pick['forecast_id'], pick['generated_at'], h['model_version'], h['source'],
                         h['losses'] > h['wins'], pick['strong']])
        wins = h['wins'] + int(preview and pick['grade'] == 'win')
        losses = h['losses'] + int(preview and pick['grade'] == 'loss')
        settled = wins + losses
        source = {'model': ' · <i>model line</i>', 'bookmaker': ' · <i>bookmaker line at forecast</i>', 'none': ''}[h['line_source']]
        lines += [f"<b>{escape(pick['market_title'])}</b>", f"{escape(pick['pick_label'])}{source}",
                  f"<b>Model {escape(h['percentage'])}%</b> · {wins}W–{losses}L ({settled} settled)"]
        if preview:
            result = {'win': 'Won', 'loss': 'Lost', 'push': 'Push'}.get(pick['grade'], pick['grade'])
            lines.append('Result: ' + escape(result))
        if h['losses'] > h['wins'] and pick.get('opposite_pick_label'):
            lines.append('<b>' + ('Opposite scenario — retrospective: ' if preview else 'Against model — EXPERIMENTAL: ') + escape(pick['opposite_pick_label']) + '</b>')
            lines.append('<i>Current comparison only; not a validated betting rule. ' +
                         ('Model line; no bookmaker offer verified.' if h['line_source'] == 'model'
                          else 'Current opposite-side price unverified.') + '</i>')
        if h['small_sample']:
            lines.append('<i>' + ('Small sample.' if settled else 'No settled matching examples.') + '</i>')
        lines.append('')
    if not versions:
        return None
    lines += [divider, '<i>Records match the sport, market, direction, percentage and model.</i>']
    if preview:
        lines.append('<i>Records include this game’s result. This is a formatting example.</i>')
    lines.append('Updated ' + now.astimezone(TORONTO).strftime('%I:%M %p %Z'))
    text = '\n'.join(lines)
    if len(text) > 4000:
        raise DeliveryError('Pick summary exceeds Telegram message length; not sent.')
    versions.sort(key=lambda version: version[0])  # Preserve live deduplication across layout-only changes.
    key = hashlib.sha256(json.dumps(['preview-card-1' if preview else 'pregame', str(chat_id), report['sport'], gid, versions,
                                   strong[0]['market'] if strong else None], sort_keys=True).encode()).hexdigest()
    return key, text


def deliver(bot, conn, chat_id, key, text, now, *, photo=None, caption=None):
    """Unique claim before send; ambiguous responses/crashes are never automatically sent a second time."""
    if photo is None and '<b>Recommended pick</b>' in text:
        from .telegram_card import render_card
        photo = render_card(text)  # Render before claiming; a missing renderer must not strand a delivery claim.
    conn.execute('BEGIN IMMEDIATE')
    row = conn.execute('SELECT * FROM deliveries WHERE delivery_key=?', (key,)).fetchone()
    if row and (row['status'] != 'retry' or row['next_attempt_at'] > now):
        conn.execute('COMMIT')
        return 'already_recorded'
    conn.execute('INSERT INTO deliveries (delivery_key,chat_id,text,status,attempted_at) VALUES (?,?,?,?,?) '
                 'ON CONFLICT(delivery_key) DO UPDATE SET status=excluded.status,attempted_at=excluded.attempted_at,text=excluded.text',
                 (key, str(chat_id), text, 'sending', now))
    conn.execute('COMMIT')
    try:
        if photo is None:
            message = bot.call('sendMessage', {'chat_id': str(chat_id), 'text': text, 'parse_mode': 'HTML'})
        else:
            if caption is None:
                league = text.split('PROP51 PICKS · ', 1)[1].split('<', 1)[0]
                caption = ('TEST PREVIEW — design example, not a new live pick.\n' if 'TEST PREVIEW' in text else '') + f'PROP51 GAME CARD · {league} · single game, not a parlay ticket\nRecommended pick and market breakdown for this one game. Combined parlay tickets arrive separately.'
            message = bot.call('sendPhoto', {'chat_id': str(chat_id), 'caption': caption}, photo=photo)
    except DeliveryError as exc:
        status = 'uncertain' if exc.uncertain else 'retry' if exc.retry_after is not None else 'failed'
        retry = (datetime.fromisoformat(now.replace('Z', '+00:00')) + timedelta(seconds=max(60, exc.retry_after))).strftime('%Y-%m-%dT%H:%M:%SZ') if status == 'retry' else None
        conn.execute('UPDATE deliveries SET status=?,next_attempt_at=?,error=? WHERE delivery_key=?', (status, retry, str(exc), key))
        return status
    conn.execute("UPDATE deliveries SET status='sent',message_id=?,error=NULL WHERE delivery_key=?", (message['message_id'], key))
    return 'sent'


def run_once(bot, settings, conn, *, now=None):
    from . import db, freshness
    from .providers.real import RealProvider
    now = now or datetime.now(timezone.utc)
    if not controls(bot, settings, conn):
        return {'status': 'paused'}
    result = {'status': 'ok', 'as_of': now.strftime('%Y-%m-%dT%H:%M:%SZ'), 'sports': {}}
    for sport in ('nba', 'nhl', 'nfl'):
        try:
            if sport == 'nfl':
                from .nfl_live import activation
                if not activation():
                    continue
            source = db.connect_readonly(sport)
            try:
                source.execute('BEGIN')  # one consistent read snapshot; never a forecast job lock
                stored = db.store_sport(source)
                if stored not in (sport, None) or (stored is None and sport != 'nba'):
                    raise ValueError('Forecast store identity does not match the requested sport.')
                state = freshness.summary(source, sport, now)
                if state['source_state'] != 'fresh' or state['failing']:
                    result['sports'][sport] = {'status': 'source_not_fresh'}
                    continue
                if sport == 'nfl':
                    from .nfl_live import Views
                    report = Views(source, now.strftime('%Y-%m-%dT%H:%M:%SZ')).ranked_picks(now.astimezone(TORONTO).date())
                else:
                    report = RealProvider(source, now.strftime('%Y-%m-%dT%H:%M:%SZ')).ranked_picks(now.astimezone(TORONTO).date())
            finally:
                source.close()
            batches = message_batches(report, settings['TELEGRAM_CHAT_ID'])
            result['sports'][sport] = {'eligible_summaries': len(batches), **dict(Counter(
                deliver(bot, conn, settings['TELEGRAM_CHAT_ID'], key, text, report['as_of']) for key, text in batches))}
        except Exception as exc:
            # Do not echo arbitrary exception text (URLs/credentials). Other sport can continue.
            result['sports'][sport] = {'status': 'failed', 'error_type': type(exc).__name__}
    if any(s.get('status') == 'failed' or any(s.get(k, 0) for k in ('failed', 'uncertain', 'retry'))
           for s in result['sports'].values()):
        result['status'] = 'delivery_issue'
    return result


def command(args):
    settings = config.telegram_settings()
    bot = Telegram(settings.get('TELEGRAM_BOT_TOKEN'))
    username = bot.identity()
    if args.telegram_action == 'status':
        with ledger() as conn:
            print(json.dumps({'bot': username, 'paired': bool(settings.get('TELEGRAM_CHAT_ID')),
                              'enabled': _get(conn, 'enabled', '1') == '1',
                              'deliveries': dict(conn.execute('SELECT status,COUNT(*) FROM deliveries GROUP BY status'))}))
        return 0
    paired = connect(bot, settings)
    if not paired['connected']:
        print(json.dumps(paired))
        return 2
    settings = config.telegram_settings()
    if args.telegram_action == 'connect':
        print(json.dumps({'bot': username, 'paired': True}))
        return 0
    with ledger() as conn:
        if args.telegram_action == 'preview':
            from datetime import date
            from . import db
            from .providers.real import RealProvider
            if not controls(bot, settings, conn):
                print(json.dumps({'preview': 'paused'}))
                return 1
            now = datetime.now(timezone.utc)
            day = date.fromisoformat(args.date) if args.date else now.astimezone(TORONTO).date() - timedelta(days=1)
            if day >= now.astimezone(TORONTO).date():
                raise ValueError('Preview must use a past date; live delivery keeps its separate pregame-only guard.')
            source = db.connect_readonly(args.sport)
            try:
                source.execute('BEGIN')
                report = RealProvider(source, now.strftime('%Y-%m-%dT%H:%M:%SZ')).ranked_picks(day)
            finally:
                source.close()
            picks = [p for p in report['picks'] if p['status'] == 'final' and (not args.game_id or p['game_id'] == args.game_id)]
            if not picks:
                raise ValueError('No recorded final game matches that preview selection.')
            gid = picks[0]['game_id']
            batch = format_batch(report, settings['TELEGRAM_CHAT_ID'], gid, [p for p in picks if p['game_id'] == gid], preview=True)
            if not batch:
                raise ValueError('That game has no comparable percentage history.')
            key, text = batch
            status = deliver(bot, conn, settings['TELEGRAM_CHAT_ID'], key, text, report['as_of'])
            print(json.dumps({'preview': status}))
            return 0 if status in ('sent', 'already_recorded') else 1
        if args.telegram_action == 'test':
            if not controls(bot, settings, conn):
                print(json.dumps({'test': 'paused'}))
                return 1
            key = hashlib.sha256(('connection-test:' + settings['TELEGRAM_CHAT_ID']).encode()).hexdigest()
            status = deliver(bot, conn, settings['TELEGRAM_CHAT_ID'], key,
                             'Prop51 is connected. Pregame pick summaries will come from the app’s recorded forecasts. '
                             'Experimental against-model signals will be labelled. Send /stop to pause or /start to resume.',
                             datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
            print(json.dumps({'test': status}))
            return 0 if status in ('sent', 'already_recorded') else 1
        while True:
            try:
                result = run_once(bot, settings, conn)
                print(json.dumps(result), flush=True)
                if args.once:
                    return 0 if result['status'] in ('ok', 'paused') else 1
            except DeliveryError as exc:
                print(json.dumps({'status': 'failed', 'error': str(exc)}), flush=True)
                if args.once:
                    return 1
            time.sleep(args.interval)
