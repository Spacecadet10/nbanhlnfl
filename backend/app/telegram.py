"""Personal Telegram delivery. Reads canonical forecasts; writes only a separate delivery receipt ledger.

No training, ingestion, forecast changes, historical recommendations, or subscribers other than the paired private chat.
"""
import hashlib
import json
import os
import re
import secrets
import sqlite3
import sys
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

    CONNECT_RETRIES = (3, 7)   # seconds, kept short: a card's 'sending' claim waits meanwhile. Nov 10: 59 of 77 failed runs
                               # happened while the Mac was going to sleep

    def call(self, method, payload=None, *, photo=None, pause=time.sleep):
        """One Telegram request. A connection that could not be opened (ConnectError/ConnectTimeout: the request never
        reached Telegram, so nothing can have been sent) is retried after 3 and 7 seconds; anything else is not."""
        for wait in (*self.CONNECT_RETRIES, None):
            try:
                return self._call(method, payload, photo=photo)
            except DeliveryError as exc:
                if not getattr(exc, 'not_connected', False) or wait is None:
                    raise
                pause(wait)

    def _call(self, method, payload=None, *, photo=None):
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
            error = DeliveryError('Telegram connection unavailable.', retry_after=60)
            error.not_connected = True
            raise error from None
        except (httpx.HTTPError, ValueError):
            raise DeliveryError('Telegram response not confirmed; review delivery before retrying.', uncertain=True) from None
        if not isinstance(body, dict):
            raise DeliveryError('Telegram response not confirmed; review delivery before retrying.', uncertain=True)
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
    from . import release
    if release.enabled() and not path.is_file():
        raise ValueError(f'Telegram delivery ledger {path} does not exist; a release never creates an empty one.')
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
    try:   # Step F paper recorder's append-only table (app/best_pick_record.py); a problem here never blocks delivery
        from . import best_pick_record
        conn.executescript(best_pick_record.SCHEMA)
    except Exception:
        pass
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


PAST_SEASONS = '2024-26'      # the only seasons any history counts (points_history.HISTORY_SEASONS, winner_history clean seasons)
PAST_PREFIX = f'Past seasons {PAST_SEASONS}, recomputed, model line: '
PAST_PREFIX_NO_LINE = f'Past seasons {PAST_SEASONS}, recomputed: '    # winner (no line): app/winner_history.py


def message_batches(report, chat_id, lineup_lines=None, games_by_key=None, *, best_pick_pictures=None, context_selection=False):
    """One summary per game/version, using the same backend best-pick ranking and exact-percentage record as the UI."""
    now = datetime.fromisoformat(report['as_of'].replace('Z', '+00:00'))
    games = {}
    for pick in report['picks']:
        start = datetime.fromisoformat(pick['tipoff'].replace('Z', '+00:00'))
        generated = datetime.fromisoformat(pick['generated_at'].replace('Z', '+00:00'))
        if report['sport'] == 'soccer':
            from .tickets import eligible
            if not eligible(pick, now) or generated > now:
                continue
        elif (pick['status'] != 'scheduled' or pick['grade'] != 'pending' or not now < start <= now + timedelta(hours=2)
                or not start - timedelta(hours=2) <= generated <= now):
            continue
        games.setdefault(pick['game_id'], []).append(pick)
    batches = []
    for gid, picks in games.items():
        batch = format_batch(report, chat_id, gid, picks, lineup_line=(lineup_lines or {}).get(gid),
                             best_pick_picture=(best_pick_pictures or {}).get(gid), context_selection=context_selection)
        if batch:
            batches.append(batch)
            if games_by_key is not None:     # which game each card is (output only; the card itself is unchanged)
                games_by_key[batch[0]] = gid
    return batches


LINEUP_PREFIX = 'Lineup: '
STRONGEST_PREFIX = 'Strongest on this card (trial): '


def format_batch(report, chat_id, gid, picks, *, preview=False, lineup_line=None, best_pick_picture=None, context_selection=False):
    """One shared message layout. Preview retains actual past times/results and is prominently labelled."""
    from .threshold_overview import MIN_LIVE_GRADED, displayed_percentage
    now = datetime.fromisoformat(report['as_of'].replace('Z', '+00:00'))
    from . import sports
    order = ({market: i for i, market in enumerate(dict.fromkeys(p['market'] for p in picks))} if report['sport'] == 'soccer'
             else {market: i for i, market in enumerate(sports.store(report['sport']).markets)})
    picks = sorted(picks, key=lambda p: order.get(p['market'], len(order)))
    start = datetime.fromisoformat(picks[0]['tipoff'].replace('Z', '+00:00')).astimezone(TORONTO)
    strong = sorted((p for p in picks if p['strong']), key=lambda p: p['game_rank'])
    choice = None
    if context_selection:
        from .tickets import card_choice
        choice = card_choice(report['sport'], picks)
    chosen = choice['pick'] if choice else (strong[0] if strong else None)
    divider = '━━━━━━━━━━━━━━'
    lines = (['<b>TEST PREVIEW — PAST GAME, NOT A LIVE PICK</b>', ''] if preview else []) + [
        f"<b>PROP51 PICKS · {escape(report['sport'].upper())}</b>",
        f"<b>{escape(picks[0]['matchup'])}</b>",
        f"{'Started' if preview else 'Starts'} {start:%b %d, %I:%M %p %Z}", '', divider,
        '<b>Recommended pick</b>',
        '<b>' + escape(chosen['pick_label'] if chosen else ('No supported pick' if choice else 'No strong pick')) + '</b>']
    if choice:
        lines.append(escape(choice['reason']))
    elif strong:
        # Owner request Nov 9: say what the sportsbook's own price implies next to the model, and say so plainly when the
        # price asks for more than the model gives (a likely winner is not a value pick at any price).
        book = strong[0].get('market_prob')
        detail = escape(strong[0]['market_title']) + ' · Model ' + displayed_percentage(strong[0]['probability']) + '%'
        if book is not None:
            detail = ('App ' + displayed_percentage(strong[0]['probability']) + '% · Sportsbook ' + displayed_percentage(book) + '% · '
                      + price_tag(strong[0]['probability'], book).upper())
        lines.append(detail)
    if lineup_line:
        # Owner request Nov 11: who is playing, one plain line under the pick. Text only: the delivery key and the card's
        # identity are unchanged, so a later lineup change never resends a card.
        lines.append(LINEUP_PREFIX + escape(lineup_line))
    if not context_selection and not preview and best_pick_picture and best_pick_picture.get('rule_applies'):
        named_id = best_pick_picture.get('named_forecast_id')
        if named_id is None:
            lines.append(STRONGEST_PREFIX + 'No qualifying pick')
        else:
            label = next((p.get('pick_label') for p in picks if p.get('forecast_id') == named_id), None)
            if label:
                lines.append(STRONGEST_PREFIX + escape(label))
    lines += ['<i>' + ('App ranking for this past game.' if preview else ('Context comparison · provisional lean, not a ticket. Method context-card-v1.' if choice else 'Based on the app’s existing ranking.')) + '</i>',
              divider, '', '<b>Market breakdown</b>', '']
    versions = []
    for pick in picks:
        h = pick.get('percentage_history')
        if h is None:
            if report['sport'] == 'soccer':
                versions.append([pick['market'], pick['forecast_id'], pick['generated_at'], pick['cutoff_at']])
                quote = pick.get('quote_check') or {}
                provenance = 'bookmaker line' if quote.get('line_source') == 'bookmaker' else 'model selection'
                actual = pick.get('book_quote') or {}
                if actual.get('decimal') is not None:
                    provenance += f"; {actual['provider']} return {actual['decimal']:.2f}x"
                elif quote.get('price_now') is not None:
                    provenance += f"; stored price {quote['price_now']:+g}"
                else:
                    provenance += '; price unavailable'
                book = pick.get('card_market_prob')
                comparison = f"Sportsbook {displayed_percentage(book)}%" if book is not None else "Sportsbook probability unavailable"
                if 'card_app_prob' in pick:
                    comparison = f"App {displayed_percentage(pick['card_app_prob'])}% / " + comparison
                if pick.get('card_comparison_basis'):
                    comparison += f" ({pick['card_comparison_basis']})"
                lines += [f"<b>{escape(pick['market_title'])}</b>", f"{escape(pick['pick_label'])} · {escape(comparison)} · {escape(provenance)}",
                          f"<b>Model {displayed_percentage(pick['probability'])}%</b> · Exact-% history unavailable", '']
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
        book = pick.get('card_market_prob', pick.get('market_prob'))
        if book is not None:   # on the small grey line under the pick, so the record line keeps its length and never runs off the card
            basis = pick.get('card_comparison_basis')
            detail = f" ({escape(basis)})" if basis else f" · {price_tag(pick['probability'], book)}"
            source = (source[:-4] + ' · ' if source else ' · <i>') + f"sportsbook {displayed_percentage(book)}%{detail}</i>"
        elif report['sport'] in ('nhl', 'nfl'):
            source = (source[:-4] + ' · ' if source else ' · <i>') + 'sportsbook probability unavailable</i>'
        implied = pick.get('implied_line') or {}
        if implied.get('line') is not None:   # owner request Nov 11: labelled, never a price to bet at
            rough = ' (rough)' if report['sport'] == 'nhl' else ''   # NHL puck line is always 1.5 (review MEDIUM 2)
            source = (source[:-4] + ' · ' if source else ' · <i>') + f"implied from spread and total {implied['line']:g}{rough}</i>"
        # Owner request Nov 9: where this exact percentage stands among the market's ranked exact percentages, and a plain
        # AVOID when its own record is a losing one. Only percentages with three or more results are ranked.
        standing = '' if not h.get('rank') else f" · rank {h['rank']} of {h['ranked']}" + (' · AVOID' if losses > wins else '')
        lines += [f"<b>{escape(pick['market_title'])}</b>", f"{escape(pick['pick_label'])}{source}",
                  f"<b>Model {escape(h['percentage'])}%</b> · {wins}W–{losses}L ({settled} settled){standing}"]
        # Step 3 (Nov 11): the recomputed past seasons at the same exact percentage, side, model and line source, on a line of
        # its own and named as such, so the live record and its notes are never read as history. Shown only with settled
        # examples (spread and total picks at a bookmaker line never have any: past seasons have model lines only).
        past = h.get('history')
        if past and past['settled']:
            prefix = PAST_PREFIX_NO_LINE if past['line_source'] == 'none' else PAST_PREFIX
            # Review MEDIUM (Nov 11): the live line's own small-sample threshold and wording, so a short history is not read as proof.
            small = ' · Small sample.' if past['settled'] < MIN_LIVE_GRADED else ''
            lines.append(f"{prefix}{past['wins']}W–{past['losses']}L{small}")
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
    if len(text) > (12000 if report['sport'] == 'soccer' else 4000):
        raise DeliveryError('Pick summary exceeds Telegram message length; not sent.')
    versions.sort(key=lambda version: version[0])  # Preserve live deduplication across layout-only changes.
    key = hashlib.sha256(json.dumps(['preview-card-1' if preview else 'pregame', str(chat_id), report['sport'], gid, versions,
                                   strong[0]['market'] if strong else None], sort_keys=True).encode()).hexdigest()
    return key, text


def card_lineups(conn, sport, report, now):
    """{game_id: one plain lineup line} for the games whose card may go out now (start within two hours). A problem
    here never stops or changes a card: the line is simply left out."""
    try:
        from . import lineup
        soon = {p['game_id']: p['matchup'] for p in report['picks']
                if now < datetime.fromisoformat(p['tipoff'].replace('Z', '+00:00')) <= now + timedelta(hours=2)}
        found = lineup.facts(conn, sport, soon, now.strftime('%Y-%m-%dT%H:%M:%SZ'))
        return {gid: line for gid, m in soon.items() if (line := lineup.card_line(sport, found.get(gid), m))}
    except Exception:
        return {}


def card_implied_lines(conn, sport, report, now):
    """Owner request Nov 11: the team-total line implied from DraftKings' spread and total beside each team-total pick of a
    card that may go out now. Never stops a card."""
    try:
        from . import implied_lines
        stamp = now.strftime('%Y-%m-%dT%H:%M:%SZ')
        for p in report['picks']:
            if p.get('market') in ('team_total_home', 'team_total_away') and \
                    now < datetime.fromisoformat(p['tipoff'].replace('Z', '+00:00')) <= now + timedelta(hours=2):
                p['implied_line'] = implied_lines.implied_team_total(conn, p['game_id'], p['market'], stamp, sport)
    except Exception:
        pass


def best_pick_pictures(conn, sport, report, now):
    """Step F paper recorder (docs/preregistration/best-pick-2026-11-11-v4.md): what the preregistered rule would name for
    each card that may go out now, and the full picture behind it. Used for the trial card line and the paper record."""
    try:
        from . import best_pick_record
        return best_pick_record.card_pictures(conn, sport, report, now)
    except Exception as exc:
        print(f'best-pick recorder (card unaffected): pictures failed: {type(exc).__name__}', file=sys.stderr, flush=True)
        return None


def record_best_pick(conn, sport, key, game_id, pictures, as_of):
    """One append-only best_picks row for a card that was just sent; a missing picture is recorded as an error row.
    Never raises: a card already sent is never affected."""
    try:
        from . import best_pick_record
        return best_pick_record.record(conn, key, sport, game_id, (pictures or {}).get(game_id), as_of)
    except Exception as exc:
        print(f'best-pick recorder (card unaffected): record failed: {type(exc).__name__}', file=sys.stderr, flush=True)
        return False


def card_identity(text):
    """What makes a game card the same card for its reader: sport, matchup and start (its first three lines) and the
    recommended pick without its line number ('DET -10.5' and 'DET -9.5' are one pick; 'DET' and 'WSH' are not).
    None for a text that is not a live game card."""
    lines = text.split('\n')
    if len(lines) < 3 or not lines[0].startswith('<b>PROP51 PICKS · ') or '<b>Recommended pick</b>' not in lines:
        return None
    pick = re.sub(r'<[^>]+>', '', lines[lines.index('<b>Recommended pick</b>') + 1])
    return lines[0], lines[1], lines[2], re.sub(r'\s*[+-]?\d+(\.\d+)?$', '', pick.strip())


def price_tag(app_probability, book_probability):
    """Plain words for the gap between the app and the sportsbook's own (margin-free) probability, owner request Nov 9:
    'overpriced' when the sportsbook rates the pick at least three points higher than the app (the bet costs more than the
    app thinks it is worth), 'underpriced' when at least three points lower, else 'fair price'. A description of the two
    numbers, not a promise: the app has been wrong against the sportsbook often."""
    from decimal import Decimal
    gap = Decimal(str(app_probability)) - Decimal(str(book_probability))
    return 'underpriced' if gap >= Decimal('0.03') else 'overpriced' if gap <= Decimal('-0.03') else 'fair price'


def deliver_card(bot, conn, chat_id, key, text, now):
    """Owner request Nov 9: one card per game. A card whose game, start and recommended pick already reached the chat (or
    may have: sent, uncertain, sending) is not sent again when only a forecast version or a line changed; the delivery
    key is unchanged, so receipts written by the earlier release count too."""
    identity = card_identity(text)
    if identity and not conn.execute('SELECT 1 FROM deliveries WHERE delivery_key=?', (key,)).fetchone():
        since = (datetime.fromisoformat(now.replace('Z', '+00:00')) - timedelta(days=2)).strftime('%Y-%m-%dT%H:%M:%SZ')
        for row in conn.execute("SELECT text FROM deliveries WHERE chat_id=? AND attempted_at>=? AND status IN ('sent','uncertain','sending')",
                                (str(chat_id), since)):
            if (card_identity(row['text']) == identity or
                    (card_identity(row['text']) and card_identity(row['text'])[:3] == identity[:3]
                     and 'Context comparison · provisional lean' in text
                     and 'Context comparison · provisional lean' not in row['text'])):
                return 'already_sent_for_game'
    return deliver(bot, conn, chat_id, key, text, now)


def deliver(bot, conn, chat_id, key, text, now, *, photo=None, caption=None, send_text=None):
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
            message = bot.call('sendMessage', {'chat_id': str(chat_id), 'text': text if send_text is None else send_text, 'parse_mode': 'HTML'})
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
    from . import additional_odds, db, freshness
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
                lineups = card_lineups(source, sport, report, now)
                pictures = best_pick_pictures(source, sport, report, now)
                card_implied_lines(source, sport, report, now)
                from . import rundown_odds, tickets
                cached_odds = rundown_odds.read_cache(now) if sport == 'nhl' else None
                for pick in report['picks']:
                    pick['quote_check'] = tickets.quote_check(source, pick, report['as_of'])
                    if sport in ('nhl', 'nfl'):
                        pick['card_market_prob'] = tickets.card_market_prob(source, pick)
                        if cached_odds is not None and pick['card_market_prob'] is None and pick['market'] == 'reg3':
                            pick.update(rundown_odds.regulation_card_comparison(pick, cached_odds, now))
                    if pick['market'] in ('team_total_home', 'team_total_away'):
                        history = pick.get('percentage_history')
                        model_side = history.get('side') if isinstance(history, dict) else getattr(history, 'side', None)
                        side = {'a': 'over', 'b': 'under'}.get(model_side)
                        pick.update(additional_odds.comparison(sport, pick, report['as_of'], side=side,
                                                               line=pick['quote_check']['line_at_forecast']))
                tickets.attach_lineups(source, sport, report['picks'], report['as_of'])
                tickets.attach_facts(source, sport, report['picks'], report['as_of'])
            finally:
                source.close()
            games_by_key, outcomes = {}, Counter()
            batches = message_batches(report, settings['TELEGRAM_CHAT_ID'], lineup_lines=lineups, games_by_key=games_by_key,
                                      best_pick_pictures=pictures, context_selection=True)
            for key, text in batches:
                status = deliver_card(bot, conn, settings['TELEGRAM_CHAT_ID'], key, text, report['as_of'])
                outcomes[status] += 1
                if status == 'sent':
                    record_best_pick(conn, sport, key, games_by_key.get(key), pictures, report['as_of'])
            result['sports'][sport] = {'eligible_summaries': len(batches), **dict(outcomes)}
        except Exception as exc:
            # Do not echo arbitrary exception text (URLs/credentials). Other sport can continue.
            result['sports'][sport] = {'status': 'failed', 'error_type': type(exc).__name__}
    # Reuse the ticket soccer contract and its competition refusals; do not route soccer through RealProvider.
    from . import tickets
    if config.db_path('soccer').is_file():
        try:
            section = tickets.soccer_section(now)
            if 'error' in section or section.get('markets_error'):
                result['sports']['soccer'] = {'status': 'failed', 'reason': section.get('error') or section['markets_error']}
            else:
                report = dict(section['report'], sport='soccer', as_of=result['as_of'])
                report['picks'] = [
                    p for p in report['picks']
                    if p['competition'] not in section['refused'] or p['cutoff_at'] == p['tipoff']
                ]
                for pick in report['picks']:
                    pick.setdefault('strong', False)
                    pick.setdefault('game_rank', None)
                    if pick.get('card_market_prob') is None:
                        pick.update(additional_odds.comparison('soccer', pick, report['as_of'],
                                                               side=pick.get('side'), line=pick.get('line')))
                outcomes = Counter()
                batches = message_batches(report, settings['TELEGRAM_CHAT_ID'], context_selection=True)
                for key, text in batches:
                    outcomes[deliver_card(bot, conn, settings['TELEGRAM_CHAT_ID'], key, text, report['as_of'])] += 1
                result['sports']['soccer'] = dict(eligible_summaries=len(batches), **dict(outcomes), refused=section['refused'])
        except Exception as exc:
            result['sports']['soccer'] = {'status': 'failed', 'error_type': type(exc).__name__}
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
