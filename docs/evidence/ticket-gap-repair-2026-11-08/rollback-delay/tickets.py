"""Reviewed ticket delivery through Courtside's existing private sender."""
import argparse
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

SKILL = Path.home() / '.codex/skills/courtside-ticket-review/SKILL.md'
TORONTO = ZoneInfo('America/Toronto')
MAX_TICKETS, MIN_LEGS, MAX_LEGS = 6, 2, 6   # owner request Nov 8: a varied menu, not one fixed shape
CARD_TICKETS = 3                             # the PNG shows the first three; the text lists every option
BET_TYPES = {'winner': 'Moneyline', 'spread': 'Spread', 'total': 'Over/Under', 'team_total_home': 'Team total',
             'team_total_away': 'Team total', 'reg3': '60-min result'}


def bet_type(leg):
    if leg.get('sport') == 'nhl' and leg.get('market') == 'spread':
        return 'Puck line'
    return BET_TYPES.get(leg.get('market'), leg.get('market'))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def snapshot():
    from app import db, freshness
    from app.providers.real import RealProvider
    from app.nfl_live import Views, activation
    now = datetime.now(timezone.utc)
    stamp = now.strftime('%Y-%m-%dT%H:%M:%SZ')
    day = now.astimezone(TORONTO).date()
    result = dict(as_of=stamp, date=str(day), skill_sha256=sha(SKILL.read_bytes()),
                  helper_sha256=sha(Path(__file__).read_bytes()), sports={})
    for sport in ('nba', 'nhl', 'nfl'):
        try:
            if sport == 'nfl' and not activation():
                result['sports'][sport] = nfl_inactive_detail(stamp)
                continue
            conn = db.connect_readonly(sport)
            try:
                conn.execute('BEGIN')
                allowed = (sport, None) if sport == 'nba' else (sport,)
                if db.store_sport(conn) not in allowed:
                    raise ValueError('Store identity mismatch')
                state = freshness.summary(conn, sport, now)
                provider = Views(conn, stamp) if sport == 'nfl' else RealProvider(conn, stamp)
                report = provider.ranked_picks(day)
                if report['mode'] != 'real':
                    raise ValueError('Real forecasts required')
                due = conn.execute("SELECT game_id FROM games WHERE status='scheduled' AND season_type IN ('regular','play_in','postseason') AND tipoff_at>? AND tipoff_at<=?",
                                   (stamp, (now+timedelta(minutes=110)).strftime('%Y-%m-%dT%H:%M:%SZ'))).fetchall()
                covered = {p['game_id'] for p in report['picks'] if p['grade']=='pending'}
                result['sports'][sport] = {'freshness': state, 'report': report,
                    'missing_due_games': [g['game_id'] for g in due if g['game_id'] not in covered]}
            finally:
                conn.close()
        except Exception as exc:
            result['sports'][sport] = {'error': type(exc).__name__}
    result['sports']['soccer'] = {'error': 'forecasts_unavailable', 'reason': 'No approved soccer forecast provider is connected to this ticket workflow; this checkout exposes schedules/results.'}
    result['alerts'] = health_alerts(result)
    from fastapi.encoders import jsonable_encoder
    return jsonable_encoder(result)


def _parse(stamp):
    return datetime.fromisoformat(stamp.replace('Z', '+00:00'))


def _stamp(moment=None):
    return (moment or datetime.now(timezone.utc)).strftime('%Y-%m-%dT%H:%M:%SZ')


def _nfl_store():
    from app import db
    return db.connect_readonly('nfl')


def nfl_inactive_detail(stamp):
    """Why NFL is not served, and how many recorded NFL forecasts that excludes. Never a silent drop."""
    from app import nfl_live
    detail = {'error': 'inactive'}
    try:
        stored = json.loads(nfl_live.activation_path().read_text())
        detail['reason'] = ('definition_mismatch' if stored.get('definition') != nfl_live.definition()
                            else 'activation_invalid')
    except FileNotFoundError:
        detail['reason'] = 'missing_activation'
    except (OSError, ValueError, TypeError, AttributeError):
        detail['reason'] = 'activation_unreadable'
    try:
        conn = _nfl_store()
        try:
            rows = conn.execute("SELECT game_id FROM game_forecasts WHERE status='forecast' AND tipoff_at>? AND tipoff_at<=? "
                                "AND generated_at<=?", (stamp, _stamp(_parse(stamp) + timedelta(hours=2)), stamp)).fetchall()
        finally:
            conn.close()
        detail.update(unserved_forecasts=len(rows), unserved_games=len({r[0] for r in rows}))
    except Exception as exc:
        detail['count_error'] = type(exc).__name__
    return detail


def eligible(pick, now):
    start = datetime.fromisoformat(pick['tipoff'].replace('Z', '+00:00'))
    made = datetime.fromisoformat(pick['generated_at'].replace('Z', '+00:00'))
    return (pick['status'] == 'scheduled' and pick['grade'] == 'pending'
            and now < start <= now + timedelta(hours=2)
            and start - timedelta(hours=2) <= made <= now)


def _describe(sport, pick):
    return {'key': f"{sport}:{pick['forecast_id']}", 'sport': sport, 'game_id': pick['game_id'],
            'matchup': pick.get('matchup'), 'market': pick.get('market'), 'pick_label': pick.get('pick_label'),
            'probability': pick.get('probability'), 'tipoff': pick['tipoff'], 'generated_at': pick['generated_at']}


def retryable_snapshots(day):
    """Snapshots whose latest recorded attempt failed in a retryable way (refused/connection/429): not yet reviewed."""
    from app import telegram
    latest = {}
    with telegram.ledger() as conn:
        schema(conn)
        for row in conn.execute('SELECT snapshot_sha256,outcome,payload FROM ticket_reviews WHERE day=? ORDER BY review_id', (day,)):
            if row['snapshot_sha256']:
                latest[row['snapshot_sha256']] = row['outcome'] == 'operational_failure' and json.loads(row['payload']).get('retryable')
    return {digest for digest, retry in latest.items() if retry}


def reviewed_history(day):
    """Forecast keys and games accounted for by the day's validated review archives (written only by a passing check).

    A review whose dispatch then failed retryably does not count, so the trigger fires again; uncertain/paused do count.
    """
    from app import config
    keys, games = set(), set()
    retry = retryable_snapshots(day)
    for path in sorted((config.data_dir() / 'reports/tickets/reviews').glob(day + '-*.json')):
        try:
            data = json.loads(path.read_text())
            accounted = data['plan']['review'].get('forecasts_reviewed') or {}
            sections = data['snapshot']['sports']
            if data['plan']['review'].get('snapshot_sha256') in retry:
                continue
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
        keys.update(accounted)
        for sport, section in sections.items():
            for pick in (section.get('report') or {}).get('picks', []):
                if f"{sport}:{pick.get('forecast_id')}" in accounted:
                    games.add((sport, pick['game_id']))
    return keys, games


def prior_ticket_games(day):
    """Games already used by the day's tickets; the shared-game rule only allows that exact forecast again."""
    from app import config, telegram
    chat = str(config.telegram_settings().get('TELEGRAM_CHAT_ID', ''))
    with telegram.ledger() as conn:
        schema(conn)
        tickets = existing_tickets(conn, chat, day)
    used = {}
    for ticket in tickets:
        for leg in ticket['legs']:
            entry = used.setdefault((leg['sport'], leg['game_id']), {'forecast_id': leg['forecast_id'],
                                    'pick_label': leg.get('pick_label'), 'matchup': leg.get('matchup'), 'tickets': []})
            entry['tickets'].append(ticket['ticket_id'])
    return used


def review_state(snap):
    """Deterministic review trigger: due when a fresh, combinable forecast has not been accounted for by any review today.

    Selection stays with the reviewer; this only says WHEN a review is needed and what is excluded and why.
    """
    if 'as_of' not in snap or 'date' not in snap:
        raise ValueError('Snapshot lacks as_of/date')
    now = _parse(snap['as_of'])
    ticketable, blocked, unavailable, started = [], {}, {}, []
    for sport, section in snap['sports'].items():
        if section.get('error'):
            unavailable[sport] = dict(section)
            continue
        picks = section['report']['picks']
        current = [p for p in picks if eligible(p, now)]
        started += [(sport, p) for p in picks if _parse(p['tipoff']) <= now
                    and _parse(p['tipoff']) - timedelta(hours=2) <= _parse(p['generated_at']) <= _parse(p['tipoff'])]
        if section['freshness']['source_state'] != 'fresh' or section['freshness']['failing']:
            if current:
                blocked[sport] = {'issue': 'source_unavailable_or_stale', 'forecasts': len(current),
                                  'games': len({p['game_id'] for p in current})}
            continue
        ticketable += [(sport, p) for p in current]
    reviewed, reviewed_games, used = set(), set(), {}
    state = {'as_of': snap['as_of'], 'date': snap['date']}
    if ticketable or started:
        reviewed, reviewed_games = reviewed_history(snap['date'])
    if ticketable:
        try:
            used = prior_ticket_games(snap['date'])
        except Exception as exc:
            state['locked_games_error'] = type(exc).__name__
    locked = []
    for (sport, game), entry in used.items():
        here = [p for s, p in ticketable if (s, p['game_id']) == (sport, game)]
        if here:
            locked.append({'sport': sport, 'game_id': game, 'matchup': entry['matchup'], 'ticket_forecast_id': entry['forecast_id'],
                           'ticket_pick': entry['pick_label'], 'tickets': entry['tickets'],
                           'blocked_forecasts': [f"{sport}:{p['forecast_id']}" for p in here if p['forecast_id'] != entry['forecast_id']]})
    blocked_keys = {k for x in locked for k in x['blocked_forecasts']}
    unreviewed = [dict(_describe(s, p), blocked_by_prior_ticket=f"{s}:{p['forecast_id']}" in blocked_keys)
                  for s, p in ticketable if f"{s}:{p['forecast_id']}" not in reviewed]
    games = {(s, p['game_id']) for s, p in ticketable}
    new = [u for u in unreviewed if not u['blocked_by_prior_ticket']]
    if not ticketable:
        due, reason = False, 'no_eligible_forecasts'
    elif not new:
        due, reason = False, 'no_new_forecasts'
    elif len(games) < 2:
        due, reason = False, 'single_game_window'   # a ticket needs two distinct games
    else:
        due, reason = True, 'new_forecasts'
    missed = {}
    for sport, pick in started:
        if (sport, pick['game_id']) not in reviewed_games:
            entry = missed.setdefault((sport, pick['game_id']), {'sport': sport, 'game_id': pick['game_id'],
                                      'matchup': pick.get('matchup'), 'tipoff': pick['tipoff'], 'forecasts': 0})
            entry['forecasts'] += 1
    state.update(due=due, reason=reason, ticketable_forecasts=len(ticketable), ticketable_games=len(games),
                 reviewed_today=len(reviewed), unreviewed=unreviewed,
                 oldest_unreviewed_at=min((u['generated_at'] for u in new), default=None),
                 blocked=blocked, unavailable=unavailable, locked_games=locked,
                 missed_before_start=sorted(missed.values(), key=lambda m: (m['tipoff'], m['sport'], m['game_id'])))
    return state


def check(plan):
    raw = Path(plan['snapshot_path']).read_bytes()
    old = json.loads(raw)
    fresh = snapshot()
    now = datetime.fromisoformat(fresh['as_of'].replace('Z', '+00:00'))
    age = now - datetime.fromisoformat(old['as_of'].replace('Z', '+00:00'))
    if old['date'] != fresh['date'] or not timedelta(0) <= age <= timedelta(minutes=10):
        raise ValueError('Snapshot expired; take a new snapshot and review again')
    review = plan['review']
    for field in ('skill_sha256', 'helper_sha256'):
        if review[field] != fresh[field] or old[field] != fresh[field]:
            raise ValueError('Skill or helper changed; review again')
    if review['snapshot_sha256'] != sha(raw) or not review['notes'].strip():
        raise ValueError('Missing or mismatched review')
    if not isinstance(review['markets_reviewed'], dict):
        raise ValueError('Record the review of each supported market')
    for sport, section in old['sports'].items():
        if 'report' in section:
            markets = {p['market'] for p in section['report']['picks']}
            covered = review['markets_reviewed'].get(sport, {})
            if not markets <= covered.keys() or any(not covered[m].strip() for m in markets):
                raise ValueError('Every available market needs a review note')
    accounted = review.get('forecasts_reviewed', {})
    required = set()
    for snap in (old, fresh):
        at = datetime.fromisoformat(snap['as_of'].replace('Z', '+00:00'))
        for sport, section in snap['sports'].items():
            for pick in section.get('report', {}).get('picks', []):
                if eligible(pick, at):
                    required.add(f"{sport}:{pick['forecast_id']}")
    if not isinstance(accounted, dict) or any(
        key not in accounted or not isinstance(accounted[key], dict) or
        accounted[key].get('decision') not in ('candidate', 'excluded') or
        not str(accounted[key].get('reason', '')).strip() for key in required):
        raise ValueError('Every available pregame forecast needs a candidate/excluded decision and reason; re-review newly published picks')
    if not review.get('combination_comparison', '').strip():
        raise ValueError('Compare combinations even when no new stakes can be allocated')
    if not isinstance(plan['tickets'], list) or len(plan['tickets']) > MAX_TICKETS:
        raise ValueError('At most six ticket options per review')
    seen, combinations, resolved = {}, set(), []
    for ticket in plan['tickets']:
        if not ticket['label'].strip() or not MIN_LEGS <= len(ticket['legs']) <= MAX_LEGS:
            raise ValueError('A ticket needs a label and two to six legs')
        stake = ticket.get('stake_usd')
        if stake is not None and (isinstance(stake, bool) or not isinstance(stake, (int, float))
                                  or not math.isfinite(stake) or stake <= 0):
            raise ValueError('An optional dollar stake must be a positive finite amount')
        legs, ticket_games = [], set()
        for leg in ticket['legs']:
            sport = leg['sport']
            decision = accounted.get(f"{sport}:{leg['forecast_id']}", {})
            if decision.get('decision') != 'candidate':
                raise ValueError('Selected leg must have been reviewed as a candidate')
            sections = [old['sports'][sport], fresh['sports'][sport]]
            picks = []
            for section in sections:
                if 'error' in section or section['freshness']['source_state'] != 'fresh' or section['freshness']['failing']:
                    raise ValueError('Selected source unavailable or stale')
                pick = next((p for p in section['report']['picks'] if p.get('forecast_id') == leg['forecast_id']), None)
                if pick is None:
                    raise ValueError('Selected forecast missing')
                picks.append(pick)
            before, pick = picks
            if not eligible(pick, now):
                raise ValueError('Forecast is settled or outside its pregame window')
            for field in ('game_id', 'market', 'pick_label', 'probability', 'tipoff', 'generated_at'):
                if before[field] != pick[field]:
                    raise ValueError('Forecast changed; review again')
            key = (sport, pick['game_id'])
            if key in ticket_games or not leg['rationale'].strip():
                raise ValueError('Repeated game within ticket or missing rationale')
            identity = (sport, pick['forecast_id'])
            if key in seen and seen[key] != identity:
                raise ValueError('Variations must preserve the same forecast for a shared game')
            ticket_games.add(key)
            seen[key] = identity
            legs.append({**pick, 'sport': sport, 'rationale': leg['rationale']})
        combination = tuple(sorted((p['sport'], p['forecast_id']) for p in legs))
        if combination in combinations:
            raise ValueError('Duplicate ticket combination')
        combinations.add(combination)
        signature = sha(json.dumps(combination).encode())
        resolved.append({'ticket_id': old['date'] + '-' + signature[:12], 'signature': signature,
                         'label': ticket['label'], 'legs': legs, 'stake_usd': ticket.get('stake_usd')})
    text = ticket_text(fresh['date'], resolved)
    result = dict(snapshot=old, plan=plan, resolved=resolved, text=text, as_of=fresh['as_of'], date=fresh['date'],
                  coverage={'required':len(required), 'accounted':len(required & set(accounted)),
                            'unavailable_sports':{sport:section['error'] for sport,section in fresh['sports'].items()
                                                  if sport in ('nba','nhl','nfl') and section.get('error')},
                            'complete_supported_sports':not any(section.get('error') for sport,section in fresh['sports'].items()
                                                                 if sport in ('nba','nhl','nfl'))})
    from app import config
    directory = config.data_dir() / 'reports/tickets/reviews'
    directory.mkdir(parents=True, exist_ok=True)
    content = json.dumps(result, sort_keys=True, indent=2).encode()
    archive = directory / (fresh['date'] + '-' + sha(content) + '.json')
    if not archive.exists():
        with archive.open('xb') as handle:
            handle.write(content)
    return result


def ticket_text(day, tickets, prior_exposure=None):
    lines = [f'<b>PROP51 · COMBINED PARLAY TICKETS · {day}</b>',
             'Multi-game combinations from app forecasts + AI selection. Not a single-game card.']
    if tickets:   # one-glance menu: size and model estimate of every option
        lines.append(f"{len(tickets)} option{'s' if len(tickets) > 1 else ''}: " + ' · '.join(
            f"#{n} {len(x['legs'])} legs {math.prod(p['probability'] for p in x['legs']):.0%}" for n, x in enumerate(tickets, 1)))
    for number, ticket in enumerate(tickets, 1):
        kinds = ' + '.join(dict.fromkeys(bet_type(p) for p in ticket['legs']))
        lines.extend(['', f"<b>Ticket {number}: {escape(ticket['label'])}</b>",
                      f"{len(ticket['legs'])} legs · {escape(kinds)}",
                      f"ID {ticket['ticket_id']}" + (f" / Suggested stake ${ticket['stake_usd']:.2f}" if ticket.get('stake_usd') is not None else ''),
                      'Combine ALL these legs in ONE parlay:'])
        for leg_number, p in enumerate(ticket['legs'], 1):
            start = datetime.fromisoformat(p['tipoff'].replace('Z', '+00:00')).astimezone(TORONTO)
            lines.append(f"{leg_number}. {p['sport'].upper()} / {escape(p['matchup'])} / {escape(bet_type(p))}: "
                         f"<b>{escape(p['pick_label'])}</b> / {start:%b %d %I:%M %p %Z} / {p['probability']:.1%}")
        lines.append(f"Model estimate {math.prod(p['probability'] for p in ticket['legs']):.1%} (independence assumption).")
    exposure = dict(prior_exposure or {})
    for t in tickets:
        for p in t['legs']:
            key = f"{p['sport']}:{p['forecast_id']}"
            old = exposure.get(key, {'dollars': 0, 'tickets': []})
            exposure[key] = {'dollars': old['dollars'] + (t.get('stake_usd') or 0), 'tickets': old['tickets'] + [t['ticket_id']]}
    # Only this batch's legs; earlier batches keep their own original disclosure.
    current = {f"{p['sport']}:{p['forecast_id']}": p for t in tickets for p in t['legs']}
    shared = [f"{p['sport'].upper()} {escape(p['pick_label'])} ({escape(p['matchup'])}): shared across "
              f"{len(exposure[key]['tickets'])} tickets ({', '.join(exposure[key]['tickets'])})"
              for key, p in current.items() if len(exposure[key]['tickets']) > 1]
    if shared:
        lines += ['', '<b>Shared core exposure</b>', *shared, 'One losing core pick can lose multiple tickets.']
    lines += ['', 'Prices unverified. Confirm exact sportsbook lines. Model estimates are not guaranteed win rates.']
    if not tickets:
        lines.append('No tickets selected.')
    text = '\n'.join(lines)
    if len(text) > 3900:
        raise ValueError('Ticket message too long for one Telegram message; send fewer or shorter options')
    return text


def selection_identity(tickets):
    # Descriptive history/as_of can change between check and send; original selections cannot.
    fields = ('sport','forecast_id','game_id','market','pick_label','probability','tipoff','generated_at')
    return [(t['label'], t.get('stake_usd'), [tuple(p.get(k) for k in fields) for p in t['legs']]) for t in tickets]


def send(result):
    """Reserve atomically, persist exact text, then use existing single-message deduplicated delivery."""
    from app import config, telegram
    if not result['resolved']:
        return 'no_ticket'
    # Revalidate at dispatch; never accept a cached check result as authorization to send later.
    checked = check(result['plan'])
    if selection_identity(checked['resolved']) != selection_identity(result['resolved']):
        raise ValueError('Forecasts changed before dispatch')
    result = checked
    attempt_at = checked['as_of']
    settings = config.telegram_settings()
    if not settings.get('TELEGRAM_CHAT_ID'):
        raise ValueError('Pair the existing Telegram bot first')
    bot = telegram.Telegram(settings.get('TELEGRAM_BOT_TOKEN'))
    bot.identity()
    with telegram.ledger() as conn:
        if not telegram.controls(bot, settings, conn):
            return 'paused'
        schema(conn)
        chat = str(settings['TELEGRAM_CHAT_ID'])
        # Full combination structure, not a flattened multiset; stable across retries and ordering.
        identity = sorted(t['signature'] for t in result['resolved'])
        key = 'ai-ticket-review-v3:' + result['date'] + ':' + chat + ':' + sha(json.dumps(identity).encode())
        prior = conn.execute('SELECT * FROM ticket_batches WHERE batch_key=?', (key,)).fetchone()
        if prior:
            delivery = conn.execute('SELECT * FROM deliveries WHERE delivery_key=?', (key,)).fetchone()
            archived = json.loads(prior['payload'])
            if selection_identity(archived['resolved']) != selection_identity(result['resolved']):
                raise ValueError('Retry must preserve the original tickets and stakes')
            if delivery and delivery['status'] not in ('retry','sent'):
                return 'existing_' + delivery['status']
            if delivery and delivery['status']=='retry' and delivery['next_attempt_at'] > attempt_at:
                return 'retry_pending'
            result = archived
        else:
            conn.execute('BEGIN IMMEDIATE')
            try:
                prior_tickets = existing_tickets(conn, chat, result['date'])
                tickets = result['resolved']
                all_tickets = prior_tickets + tickets
                signatures = [t['signature'] for t in all_tickets]
                if len(signatures) != len(set(signatures)):
                    raise ValueError('Duplicate ticket combination across batches')
                identities, exposure = {}, {}
                for t in prior_tickets:
                    for p in t['legs']:
                        k = f"{p['sport']}:{p['forecast_id']}"
                        e = exposure.setdefault(k, {'dollars': 0, 'tickets': []})
                        e['dollars'] += t.get('stake_usd') or 0; e['tickets'].append(t['ticket_id'])
                for t in all_tickets:
                    for p in t['legs']:
                        game = (p['sport'], p['game_id'])
                        fid = p['forecast_id']
                        if game in identities and identities[game] != fid:
                            raise ValueError('Shared games must preserve the identical forecast')
                        identities[game] = fid
                result['text'] = ticket_text(result['date'], tickets, exposure)
                raw = json.dumps(result, sort_keys=True, indent=2)
                conn.execute('INSERT INTO ticket_batches VALUES (?,?,?,?,?)', (key, chat, result['date'], result['as_of'], raw))
                for t in tickets:
                    conn.execute('INSERT INTO ticket_items VALUES (?,?,?,?,?)',
                                 (chat, result['date'], t['signature'], t['ticket_id'], key))
                conn.execute('COMMIT')
            except Exception:
                conn.execute('ROLLBACK'); raise
        directory = config.data_dir() / 'reports' / 'tickets'
        directory.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(result, sort_keys=True, indent=2).encode()
        archive = directory / (result['date'] + '-' + sha(raw) + '.json')
        if not archive.exists():
            with archive.open('xb') as handle:
                handle.write(raw)
        # Keep original content/time as evidence; retries use the CURRENT attempt time.
        previous = conn.execute('SELECT status FROM deliveries WHERE delivery_key=?',(key,)).fetchone()
        status = telegram.deliver(bot, conn, chat, key, result['text'], attempt_at)
        confirmed = conn.execute('SELECT status FROM deliveries WHERE delivery_key=?',(key,)).fetchone()
        if not confirmed or confirmed['status'] != 'sent':
            return status
        image_status = deliver_visual(bot, conn, chat, key, result, directory, attempt_at)
        if image_status not in ('sent','already_recorded'):
            return 'partial_' + image_status
        return 'existing_sent' if previous and previous['status']=='sent' and image_status=='already_recorded' else 'sent'


def deliver_visual(bot, conn, chat, key, result, directory, attempt_at):
    from app import telegram
    from app.telegram_card import render_ticket_card
    artifact = conn.execute('SELECT * FROM ticket_images WHERE batch_key=?',(key,)).fetchone()
    if artifact:
        image_path = Path(artifact['path'])
        photo = image_path.read_bytes()
        if sha(photo) != artifact['sha256']:
            raise ValueError('Archived ticket image hash mismatch')
        caption = artifact['caption']
    else:
        photo = render_ticket_card(result['date'], result['resolved'])
        image_path = directory / (result['date'] + '-' + sha(key.encode()) + '.png')
        if not image_path.exists():
            with image_path.open('xb') as handle:
                handle.write(photo)
        caption = 'PROP51 / COMBINED PARLAY TICKETS / ' + result['date'] + '\n' + '\n'.join(
            f"Ticket {i}: {t['ticket_id']} / {len(t['legs'])} legs" + (f" / ${t['stake_usd']:.2f}" if t.get('stake_usd') is not None else '') for i,t in enumerate(result['resolved'],1))
        caption += '\nEach numbered ticket combines all its listed legs. See preceding text for full details.'
        extra = len(result['resolved']) - CARD_TICKETS
        if extra > 0:
            caption += f"\nImage shows tickets 1-{CARD_TICKETS}; +{extra} more option{'s' if extra > 1 else ''} in the text above."
        if len(caption)>1024:
            raise ValueError('Ticket image caption too long')
        conn.execute('INSERT OR IGNORE INTO ticket_images VALUES (?,?,?,?)',(key,str(image_path),sha(photo),caption))
        artifact = conn.execute('SELECT * FROM ticket_images WHERE batch_key=?',(key,)).fetchone()
        photo = Path(artifact['path']).read_bytes(); caption = artifact['caption']
        if sha(photo) != artifact['sha256']:
            raise ValueError('Archived ticket image hash mismatch')
    image_key = 'ticket-image:' + sha(key.encode())
    status = telegram.deliver(bot,conn,chat,image_key,result['text'],attempt_at,photo=photo,caption=caption)
    row = conn.execute('SELECT status FROM deliveries WHERE delivery_key=?',(image_key,)).fetchone()
    if row and row['status'] not in ('sent',):
        return row['status']
    return status


def schema(conn):
    conn.executescript('''
      CREATE TABLE IF NOT EXISTS ticket_batches (
        batch_key TEXT PRIMARY KEY, chat_id TEXT NOT NULL, day TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS ticket_images (batch_key TEXT PRIMARY KEY, path TEXT NOT NULL, sha256 TEXT NOT NULL, caption TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS ticket_items (
        chat_id TEXT NOT NULL, day TEXT NOT NULL, signature TEXT NOT NULL, ticket_id TEXT NOT NULL,
        batch_key TEXT NOT NULL REFERENCES ticket_batches(batch_key), PRIMARY KEY(chat_id,day,signature));
      CREATE TABLE IF NOT EXISTS ticket_grades (
        ticket_id TEXT NOT NULL, evidence_hash TEXT NOT NULL, as_of TEXT NOT NULL, payload TEXT NOT NULL,
        PRIMARY KEY(ticket_id,evidence_hash));
      CREATE TABLE IF NOT EXISTS personal_tickets (
        slip_id TEXT PRIMARY KEY, recorded_at TEXT NOT NULL, payload TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS ticket_reviews (
        review_id INTEGER PRIMARY KEY AUTOINCREMENT, recorded_at TEXT NOT NULL, day TEXT NOT NULL,
        stage TEXT NOT NULL CHECK(stage IN ('check','send')),
        outcome TEXT NOT NULL CHECK(outcome IN ('ticket_sent','no_ticket','operational_failure','paused')),
        detail TEXT NOT NULL, snapshot_sha256 TEXT, snapshot_as_of TEXT, payload TEXT NOT NULL);
      CREATE TRIGGER IF NOT EXISTS ticket_reviews_no_update BEFORE UPDATE ON ticket_reviews
        BEGIN SELECT RAISE(ABORT,'ticket review outcomes are append-only'); END;
      CREATE TRIGGER IF NOT EXISTS ticket_reviews_no_delete BEFORE DELETE ON ticket_reviews
        BEGIN SELECT RAISE(ABORT,'ticket review outcomes are append-only'); END;
      CREATE TRIGGER IF NOT EXISTS ticket_batches_no_update BEFORE UPDATE ON ticket_batches
        BEGIN SELECT RAISE(ABORT,'ticket batches are immutable'); END;
      CREATE TRIGGER IF NOT EXISTS ticket_batches_no_delete BEFORE DELETE ON ticket_batches
        BEGIN SELECT RAISE(ABORT,'ticket batches are immutable'); END;
      CREATE TRIGGER IF NOT EXISTS ticket_grades_no_update BEFORE UPDATE ON ticket_grades
        BEGIN SELECT RAISE(ABORT,'ticket grades are append-only'); END;
      CREATE TRIGGER IF NOT EXISTS ticket_grades_no_delete BEFORE DELETE ON ticket_grades
        BEGIN SELECT RAISE(ABORT,'ticket grades are append-only'); END;
    ''')


def normalized_ticket(t, day):
    out = dict(t)
    combination = tuple(sorted((p['sport'], p['forecast_id']) for p in t['legs']))
    out['signature'] = sha(json.dumps(combination).encode())
    out['ticket_id'] = day + '-' + out['signature'][:12]
    return out


def existing_tickets(conn, chat, day):
    """Legacy reservations count too; recover exact combinations from original archives/receipts."""
    from app import config
    out = []
    for row in conn.execute('SELECT payload FROM ticket_batches WHERE chat_id=? AND day=?', (chat, day)):
        out.extend(json.loads(row['payload'])['resolved'])
    rows = conn.execute("SELECT delivery_key,text FROM deliveries WHERE chat_id=? AND delivery_key LIKE ?",
                        (chat, 'ai-ticket-review-v%:' + day + ':%')).fetchall()
    legacy = {r['delivery_key'] for r in rows if not r['delivery_key'].startswith('ai-ticket-review-v3:')}
    # Include reservations even if a process crashed before its delivery claim.
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ticket_budget_reservations'").fetchone():
        legacy.update(r[0] for r in conn.execute('SELECT batch_key FROM ticket_budget_reservations WHERE chat_id=? AND day=?', (chat,day)))
    if legacy:
        for path in sorted((config.data_dir() / 'reports/tickets').glob(day + '-*.json')):
            data = json.loads(path.read_text())
            tickets = data.get('resolved', [])
            if not tickets:
                continue
            identity = sorted((p['sport'],p['forecast_id']) for t in tickets for p in t['legs'])
            key = 'ai-ticket-review-v2:' + day + ':' + chat + ':' + sha(json.dumps(identity).encode())
            if key in legacy:
                out.extend(normalized_ticket(t,day) for t in tickets); legacy.remove(key)
        if legacy:
            raise ValueError('Unresolved legacy ticket reservation; inspect archive before allocating more')
    return out


def health_alerts(snap):
    alerts = []
    for sport, section in snap['sports'].items():
        if section.get('error'):
            if section['error'] != 'forecasts_unavailable':
                alerts.append({'sport': sport, 'issue': section['error'],
                               **{k: section[k] for k in ('reason', 'unserved_forecasts', 'unserved_games') if k in section}})
            continue
        state = section['freshness']
        if state['source_state'] != 'fresh' or state['failing']:
            alerts.append({'sport': sport, 'issue': 'source_unavailable_or_stale'})
        if section.get('missing_due_games'):
            alerts.append({'sport': sport, 'issue': 'missing_due_forecasts', 'games': section['missing_due_games']})
    return alerts


def grade_leg(leg, as_of):
    """Resolve the immutable forecast ID, not today's replacement canonical pick."""
    from app import db, coverage
    conn = db.connect_readonly(leg['sport'])
    try:
        row = conn.execute('SELECT * FROM game_forecasts WHERE forecast_id=?', (leg['forecast_id'],)).fetchone()
        if not row or row['game_id'] != leg['game_id'] or row['market'] != leg['market']:
            raise ValueError('Archived forecast identity mismatch')
        game = conn.execute('SELECT * FROM games WHERE game_id=?', (row['game_id'],)).fetchone()
        if not game:
            raise ValueError('Archived game missing')
        g = dict(game)
        if g['status'] == 'final' and (not g['result_available_at'] or g['result_available_at'] > as_of):
            g.update(status='in_progress',home_score=None,away_score=None)
        decision = None
        if row['market'] == 'reg3':
            d = conn.execute('SELECT decision FROM game_decisions WHERE game_id=?', (row['game_id'],)).fetchone()
            decision = d['decision'] if d else None
        grade = coverage.grade(row['market'], row['line'], (row['p_a'],row['p_mid'],row['p_b']),g,as_of,row['tipoff_at'],decision)
        return {'sport':leg['sport'], 'forecast_id':row['forecast_id'], 'game_id':row['game_id'],
                'pick_label':leg['pick_label'], 'market':row['market'], 'line':row['line'], 'grade':grade,
                'home_score':g['home_score'], 'away_score':g['away_score'], 'result_available_at':g['result_available_at']}
    finally:
        conn.close()


def ticket_grade(legs):
    grades = [p['grade'] for p in legs]
    if 'loss' in grades:
        return 'loss'
    if any(g in ('pending','unscorable','unresolved','unavailable') for g in grades):
        return 'pending'
    if all(g in ('push','void') for g in grades):
        return 'void'
    return 'win'


def settle(*, now=None, notify=False):
    """Append grade changes, even when there are no new picks or the budget is exhausted."""
    from app import telegram, config
    now = now or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    report = {'as_of':now, 'tickets':[], 'errors':[]}
    settings = config.telegram_settings()
    chat = str(settings.get('TELEGRAM_CHAT_ID', ''))
    with telegram.ledger() as conn:
        schema(conn)
        days = {r[0] for r in conn.execute('SELECT day FROM ticket_batches WHERE chat_id=?', (chat,))}
        days.update(r[0].split(':')[1] for r in conn.execute("SELECT delivery_key FROM deliveries WHERE chat_id=? AND delivery_key LIKE 'ai-ticket-review-v%:%'", (chat,)))
        notices = []
        report['notification_errors'] = []
        for day in sorted(days):
            try:
                tickets = existing_tickets(conn,chat,day)
            except Exception as exc:
                report['errors'].append({'day':day,'error':type(exc).__name__}); continue
            for t in tickets:
                legs = []
                for p in t['legs']:
                    try:
                        legs.append(grade_leg(p,now))
                    except Exception as exc:
                        legs.append({'sport':p['sport'],'forecast_id':p['forecast_id'],'grade':'unavailable', 'error':type(exc).__name__})
                if any(p['grade']=='unavailable' for p in legs):
                    report['errors'].append({'ticket_id':t['ticket_id'],'error':'settlement_unavailable'})
                result = {'ticket_id':t['ticket_id'], 'label':t['label'], 'grade':ticket_grade(legs), 'legs':legs,
                          'kind':'suggested_ticket', 'profit':None}
                raw = json.dumps(result,sort_keys=True)
                digest = sha(raw.encode())
                conn.execute('INSERT OR IGNORE INTO ticket_grades VALUES (?,?,?,?)', (t['ticket_id'],digest,now,raw))
                report['tickets'].append(result)
                # Only confirmed sent batches receive a settlement notification.
                batch = conn.execute('SELECT batch_key FROM ticket_items WHERE ticket_id=? AND chat_id=?', (t['ticket_id'],chat)).fetchone()
                confirmed = batch and conn.execute("SELECT 1 FROM deliveries WHERE delivery_key=? AND status='sent'",(batch['batch_key'],)).fetchone()
                if confirmed and result['grade'] != 'pending':
                    text = f"PARLAY RESULT / {t['ticket_id']} / {result['grade'].upper()}\n" + '\n'.join(f"{p.get('pick_label',p['forecast_id'])}: {p['grade']}" for p in legs)
                    notices.append((result,'ticket-result:'+t['ticket_id']+':'+digest,text))
        # All grading is committed before ANY external call. Notification failure cannot suppress outcomes.
        if notify and notices:
            try:
                bot = telegram.Telegram(settings.get('TELEGRAM_BOT_TOKEN')); bot.identity()
                allowed = telegram.controls(bot,settings,conn)
                for result,key,text in notices:
                    if allowed:
                        try:
                            result['notification'] = telegram.deliver(bot,conn,chat,key,text,now)
                        except telegram.DeliveryError as exc:
                            report['notification_errors'].append({'ticket_id':result['ticket_id'],'error':type(exc).__name__})
            except (telegram.DeliveryError, ValueError) as exc:
                report['notification_errors'].append({'error':type(exc).__name__})
    return report


def record_personal(path):
    """Explicit user-provided slip, distinct from suggested stakes and strategy performance."""
    from app import telegram
    value = json.loads(Path(path).read_text())
    if (not value.get('slip_id') or not value.get('source') or not isinstance(value.get('legs'),list) or
        not value['legs'] or any(not isinstance(p,dict) or not p.get('sport') or not p.get('pick_label') for p in value['legs'])):
        raise ValueError('Personal slip requires slip_id, source and exact legs')
    stake, returned = value.get('stake_usd'), value.get('return_usd')
    if not isinstance(stake,(int,float)) or not math.isfinite(stake) or stake < 0:
        raise ValueError('Actual nonnegative dollar stake required')
    if returned is not None and (not isinstance(returned,(int,float)) or not math.isfinite(returned) or returned < 0):
        raise ValueError('Actual nonnegative return required')
    value['kind'] = 'user_placed'
    value['profit_usd'] = None if returned is None else round(returned-stake,2)
    raw = json.dumps(value,sort_keys=True)
    with telegram.ledger() as conn:
        schema(conn)
        prior = conn.execute('SELECT payload FROM personal_tickets WHERE slip_id=?',(value['slip_id'],)).fetchone()
        if prior and prior['payload'] != raw:
            raise ValueError('Personal slip already recorded with different content; use a correction ID')
        conn.execute('INSERT OR IGNORE INTO personal_tickets VALUES (?,?,?)',(value['slip_id'],datetime.now(timezone.utc).isoformat(),raw))
    return value


def notify_alerts(snap, settlement_errors):
    from app import config, telegram
    issues = snap.get('alerts', []) + [{'sport':'tickets','issue':'settlement_unavailable'} for _ in settlement_errors]
    out = []
    settings = config.telegram_settings()
    bot = telegram.Telegram(settings.get('TELEGRAM_BOT_TOKEN')); bot.identity()
    with telegram.ledger() as conn:
        if not telegram.controls(bot,settings,conn):
            return ['paused']
        for issue in issues:
            # One alert per issue/sport/day, no repeated hourly noise.
            key = 'ticket-health:' + snap['date'] + ':' + issue['sport'] + ':' + issue['issue']
            text = 'PROP51 / REVIEW BLOCKER / ' + issue['sport'].upper() + '\n' + issue['issue'] + ''.join(
                f"\n{field}: {issue[field]}" for field in ('reason', 'unserved_forecasts', 'unserved_games') if field in issue)
            out.append(telegram.deliver(bot,conn,settings['TELEGRAM_CHAT_ID'],key,text,snap['as_of']))
        start = datetime.fromisoformat(snap['date']).replace(tzinfo=TORONTO)
        end = start + timedelta(days=1)
        rows = conn.execute("SELECT delivery_key,status FROM deliveries WHERE chat_id=? "
                            "AND status IN ('uncertain','sending','failed','retry') AND attempted_at>=? AND attempted_at<? "
                            "AND (delivery_key LIKE 'ai-ticket-review-v%:%' OR delivery_key LIKE 'ticket-image:%')",
                            (str(settings['TELEGRAM_CHAT_ID']),start.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                             end.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))).fetchall()
        for row in rows:
            # Each affected ticket/status has its own key: an earlier false alert cannot mask later failures.
            digest = sha((row['delivery_key'] + ':' + row['status']).encode())
            out.append(telegram.deliver(bot,conn,settings['TELEGRAM_CHAT_ID'],'ticket-health:'+snap['date']+':delivery:'+digest,
                f"PROP51 / TICKET DELIVERY REVIEW REQUIRED / {row['status']}. Reservation retained; inspect original receipt.",snap['as_of']))
    return out


def preview_payload(path, *, now=None):
    """Render an explicit historical format test from original forecast identities; never reserve stakes."""
    from app import config
    from app.telegram_card import render_ticket_card
    now = now or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    data = json.loads(Path(path).read_text())
    past = data['resolved']
    if not past or len(past)>3:
        raise ValueError('Preview requires one to three archived tickets')
    tickets = []
    for source in past:
        ticket = normalized_ticket(source,data['date'])
        ticket['ticket_id'] = 'TEST-' + ticket['ticket_id']
        legs = []
        for original in ticket['legs']:
            if original['tipoff'] >= now:
                raise ValueError('Format tests require past games, not future recommendations')
            result = grade_leg(original,now)
            if result['grade'] in ('pending','unavailable','unresolved','unscorable'):
                raise ValueError('Historical preview requires settled original forecasts')
            legs.append({**original, 'result': result['grade'].upper() + f" / Final {result['home_score']}-{result['away_score']} (home-away)"})
        ticket['legs'] = legs
        ticket['result'] = 'TEST / RESULTS ALREADY KNOWN / ' + ticket_grade([{'grade':p['result'].split(' / ')[0].lower()} for p in legs]).upper()
        tickets.append(ticket)
    caption = 'TEST PREVIEW / PAST GAMES / NOT A LIVE TICKET. Example stakes only; no allocation or bet placed.'
    photo = render_ticket_card(data['date'],tickets,preview=True)
    return photo,caption


def send_preview(path):
    from app import config, telegram
    photo,caption = preview_payload(path)
    key = 'ticket-preview:' + sha(photo)
    directory = config.data_dir() / 'reports/tickets/previews'
    directory.mkdir(parents=True,exist_ok=True)
    artifact = directory / (sha(photo) + '.png')
    if not artifact.exists():
        with artifact.open('xb') as handle:
            handle.write(photo)
    meta = directory / (sha(photo) + '.json')
    if not meta.exists():
        with meta.open('x') as handle:
            json.dump({'kind':'historical_format_test','caption':caption,'image_sha256':sha(photo),'source':str(path)},handle,indent=2)
    settings = config.telegram_settings()
    bot = telegram.Telegram(settings.get('TELEGRAM_BOT_TOKEN')); bot.identity()
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    with telegram.ledger() as conn:
        if not telegram.controls(bot,settings,conn):
            return 'paused'
        return telegram.deliver(bot,conn,settings['TELEGRAM_CHAT_ID'],key,caption,now,photo=photo,caption=caption)


SEND_OUTCOMES = {'sent': 'ticket_sent', 'existing_sent': 'ticket_sent', 'no_ticket': 'no_ticket', 'paused': 'paused'}
RETRYABLE = {'retry', 'retry_pending'}   # uncertain/failed/partial need receipt inspection and are never re-triggered


def record_review(stage, outcome, detail, *, plan=None, result=None, retryable=False):
    """Append what one review attempt produced: a ticket, no ticket (insufficient evidence), a pause or an operational failure."""
    from app import telegram
    review = plan.get('review') if isinstance(plan, dict) and isinstance(plan.get('review'), dict) else {}
    accounted = review.get('forecasts_reviewed') if isinstance(review.get('forecasts_reviewed'), dict) else {}
    payload = {'snapshot_path': str(plan.get('snapshot_path')) if isinstance(plan, dict) else None,
               'forecasts_accounted': len(accounted),
               'candidates': sum(1 for v in accounted.values() if isinstance(v, dict) and v.get('decision') == 'candidate'),
               'retryable': bool(retryable)}
    if result:
        payload.update(coverage=result['coverage'], ticket_ids=[t['ticket_id'] for t in result['resolved']],
                       alerts=result['snapshot'].get('alerts', []))
    day = result['date'] if result else datetime.now(timezone.utc).astimezone(TORONTO).date().isoformat()
    with telegram.ledger() as conn:
        schema(conn)
        cursor = conn.execute('INSERT INTO ticket_reviews (recorded_at,day,stage,outcome,detail,snapshot_sha256,snapshot_as_of,payload) '
                              'VALUES (?,?,?,?,?,?,?,?)',
                              (_stamp(), day, stage, outcome, str(detail)[:1000], review.get('snapshot_sha256'),
                               result['snapshot']['as_of'] if result else None, json.dumps(payload, sort_keys=True)))
        return cursor.lastrowid


def _no_ticket_detail(plan):
    review = plan['review']
    return 'insufficient evidence for a justified combination: ' + str(
        review.get('outcome_summary') or review.get('combination_comparison', '')).strip()[:600]


def _safe_record(*args, **kwargs):
    try:
        return record_review(*args, **kwargs)
    except Exception as exc:      # recording must never change delivery; report the gap instead
        return 'not_recorded:' + type(exc).__name__


def review_outcomes(day):
    from app import telegram
    with telegram.ledger() as conn:
        schema(conn)
        return [dict(r) | {'payload': json.loads(r['payload'])} for r in
                conn.execute('SELECT * FROM ticket_reviews WHERE day=? ORDER BY review_id', (day,))]


def ticket_deliveries(day):
    """Combined-ticket text and image receipts for the day (game cards are a separate sender and are excluded)."""
    from app import telegram
    out = []
    with telegram.ledger() as conn:
        schema(conn)
        for batch in conn.execute('SELECT batch_key FROM ticket_batches WHERE day=? ORDER BY created_at', (day,)).fetchall():
            for kind, key in (('ticket_text', batch['batch_key']), ('ticket_image', 'ticket-image:' + sha(batch['batch_key'].encode()))):
                row = conn.execute('SELECT status,attempted_at,message_id,next_attempt_at FROM deliveries WHERE delivery_key=?', (key,)).fetchone()
                out.append({'kind': kind, 'batch_key': batch['batch_key'], **(dict(row) if row else {'status': 'not_attempted'})})
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('snapshot', 'check', 'send', 'settle', 'preflight', 'status', 'personal', 'preview'))
    parser.add_argument('--notify', action='store_true', help='Send result/failure notifications to the existing paired chat')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--brief', action='store_true', help='preflight: print only the review trigger, alerts and settlement summary')
    args = parser.parse_args()
    if args.action in ('check', 'send'):
        return review_command(args)
    try:
        if args.action in ('settle', 'preflight'):
            report = settle(notify=args.notify)
            if args.action == 'preflight':
                snap = snapshot()
                try:
                    report['review'] = review_state(snap)
                except Exception as exc:
                    report['review'] = {'due': None, 'reason': 'review_state_unavailable', 'error': type(exc).__name__}
                report['snapshot'] = snap
                if args.notify:
                    report['alerts'] = notify_alerts(report['snapshot'], report['errors'])
                if args.brief:   # the trigger check runs often; the full snapshot is taken separately when a review is due
                    report = {'as_of': report.get('as_of'), 'errors': report['errors'],
                              'notification_errors': report.get('notification_errors', []), 'alerts': report.get('alerts', []),
                              'snapshot_alerts': snap.get('alerts', []), 'review': report['review'],
                              'settlement': [{k: t.get(k) for k in ('ticket_id', 'grade', 'notification')} for t in report.get('tickets', [])]}
            print(json.dumps(report))
        elif args.action == 'status':
            snap = snapshot()
            print(json.dumps({'as_of': snap['as_of'], 'date': snap['date'], 'review': review_state(snap),
                              'outcomes': review_outcomes(snap['date']), 'ticket_deliveries': ticket_deliveries(snap['date'])}))
        elif args.action == 'preview':
            status = send_preview(args.plan)
            print(json.dumps({'status':status}))
            return 0 if status in ('sent','already_recorded') else 1
        elif args.action == 'personal':
            print(json.dumps(record_personal(args.plan)))
        elif args.action == 'snapshot':
            raw = json.dumps(snapshot(), sort_keys=True, indent=2).encode()
            with args.output.open('xb') as handle:
                handle.write(raw)
            print(json.dumps({'snapshot_path': str(args.output), 'snapshot_sha256': sha(raw)}))
    except Exception as exc:
        print(json.dumps(_failure(exc)))
        return 1
    return 0


def _failure(exc):
    return {'status': 'failed', 'error_type': type(exc).__name__,
            'reason': str(exc) if isinstance(exc, ValueError) else 'Inspect local inputs or backend compatibility'}


def review_command(args):
    """check/send; every attempt appends its outcome to ticket_reviews without changing what is validated or sent."""
    plan = None
    try:
        plan = json.loads(args.plan.read_text())
        result = check(plan)
    except Exception as exc:
        failure = dict(_failure(exc), review_outcome='operational_failure')
        failure['recorded'] = _safe_record('check', 'operational_failure', 'validation refused: ' + failure['reason'], plan=plan)
        print(json.dumps(failure))
        return 1
    if args.action == 'check':
        print(result['text'])
        if result['resolved']:
            print(json.dumps({'review_outcome': 'validated_not_sent', 'coverage': result['coverage']}))
        else:
            recorded = _safe_record('check', 'no_ticket', _no_ticket_detail(plan), plan=plan, result=result)
            print(json.dumps({'review_outcome': 'no_ticket', 'recorded': recorded, 'coverage': result['coverage']}))
        return 0
    try:
        status = send(result)
    except Exception as exc:
        failure = dict(_failure(exc), review_outcome='operational_failure')
        failure['recorded'] = _safe_record('send', 'operational_failure', 'send refused: ' + failure['reason'], plan=plan,
                                           result=result, retryable=True)   # refused before any claim; a fresh review may retry
        print(json.dumps(failure))
        return 1
    outcome = SEND_OUTCOMES.get(status, 'operational_failure')
    detail = (_no_ticket_detail(plan) if outcome == 'no_ticket' else
              status + ''.join(': ' + ', '.join(t['ticket_id'] for t in result['resolved']) for _ in [0] if result['resolved']))
    recorded = _safe_record('send', outcome, detail, plan=plan, result=result, retryable=status in RETRYABLE)
    print(json.dumps({'status': status, 'review_outcome': outcome, 'recorded': recorded}))
    return 0 if status in ('sent', 'existing_sent', 'no_ticket') else 1


if __name__ == '__main__':
    raise SystemExit(main())
