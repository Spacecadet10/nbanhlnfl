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
MAX_TICKETS, MIN_LEGS, MAX_LEGS = 6, 2, 7   # owner request Nov 8: a varied menu; Nov 9: long-shot tickets up to seven legs
# Owner request Nov 9: these two states clear by themselves within minutes and their alert text meant nothing to the
# reader. They stay in the preflight report, the snapshot and the trigger observations; they are no longer sent to the chat.
QUIET_HEALTH = ('source_unavailable_or_stale', 'missing_due_forecasts')
CARD_TICKETS = 3
PROVIDER_SHORT = {'DraftKings': 'DK'}
SNAPSHOT_TTL = timedelta(minutes=10)   # check() refuses older snapshots; a start older than this can never complete                             # the PNG shows the first three; the text lists every option
BET_TYPES = {'winner': 'Moneyline', 'spread': 'Spread', 'total': 'Over/Under', 'team_total_home': 'Team total',
             'team_total_away': 'Team total', 'reg3': '60-min result', '1x2_regulation': 'Match result (90 min)'}


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
                delay = refresh_delay(conn, state)
                provider = Views(conn, stamp) if sport == 'nfl' else RealProvider(conn, stamp)
                report = provider.ranked_picks(day)
                if report['mode'] != 'real':
                    raise ValueError('Real forecasts required')
                for pick in report['picks']:
                    try:
                        pick['quote_check'] = quote_check(conn, pick, stamp)
                    except Exception as exc:   # a quote lookup problem is reported on the pick, never hides the forecast
                        pick['quote_check'] = {'error': type(exc).__name__}
                attach_assessments(sport, report['picks'])
                due = conn.execute("SELECT game_id FROM games WHERE status='scheduled' AND season_type IN ('regular','play_in','postseason') AND tipoff_at>? AND tipoff_at<=?",
                                   (stamp, (now+timedelta(minutes=110)).strftime('%Y-%m-%dT%H:%M:%SZ'))).fetchall()
                covered = {p['game_id'] for p in report['picks'] if p['grade']=='pending'}
                result['sports'][sport] = {'freshness': state, 'report': report,
                    'missing_due_games': [g['game_id'] for g in due if g['game_id'] not in covered]}
                if delay:
                    result['sports'][sport]['refresh_delay'] = delay
            finally:
                conn.close()
        except Exception as exc:
            result['sports'][sport] = {'error': type(exc).__name__}
    try:
        result['sports']['soccer'] = soccer_section(now)
    except Exception as exc:
        result['sports']['soccer'] = {'error': type(exc).__name__}
    attach_assessments('soccer', (result['sports']['soccer'].get('report') or {}).get('picks', []))
    result['alerts'] = health_alerts(result)
    from fastapi.encoders import jsonable_encoder
    return jsonable_encoder(result)


def _parse(stamp):
    return datetime.fromisoformat(stamp.replace('Z', '+00:00'))


def refresh_delay(conn, state):
    """A stale, non-failing source whose own store job started at/after the last successful refresh and is still running:
    the tick refreshes, then runs the (sometimes 30+ min) forecast job under the store lock, so no refresh can run meanwhile
    (docs/TICKETS.md, lock coupling). Nothing failed; the data is only as old as that refresh. Old crashed 'running' rows
    predate the last success and never count."""
    if state.get('source_state') != 'stale' or state.get('failing') or not state.get('source_fetched_at'):
        return None
    row = conn.execute("SELECT job, started_at FROM job_runs WHERE status='running' AND started_at>=? "
                       "ORDER BY started_at DESC LIMIT 1", (state['source_fetched_at'],)).fetchone()
    return dict(job=row['job'], started_at=row['started_at'], source_fetched_at=state['source_fetched_at']) if row else None


def source_usable(section, pick=None):
    """Fresh, or delayed only by its running forecast job (refresh_delay). A delayed source serves only games whose
    last status refresh happened inside their own two-hour pregame window; legs disclose that refresh time."""
    if 'error' in section or section['freshness']['failing']:
        return False
    if section['freshness']['source_state'] == 'fresh':
        return True
    delay = section.get('refresh_delay')
    if section['freshness']['source_state'] != 'stale' or not delay:
        return False
    return pick is None or _parse(pick['tipoff']) - timedelta(hours=2) <= _parse(delay['source_fetched_at'])


def quote_check(conn, pick, stamp):
    """The forecast's line against the latest stored sportsbook quote (market_quotes 'current', retrieved by `stamp`).
    A bookmaker line that moved since the forecast means its probability belongs to a line no longer offered. Prices are
    reported, never judged: value depends on the exact price the user gets. Model-line markets have no stored quote."""
    row = conn.execute('SELECT line, line_source, market_quote_id FROM game_forecasts WHERE forecast_id=?',
                       (pick['forecast_id'],)).fetchone()
    history = pick.get('percentage_history')   # a dict, or the provider's ForecastPercentageRecord before JSON encoding
    side = history.get('side') if isinstance(history, dict) else getattr(history, 'side', None)
    price = lambda q: None if q is None else {'a': q['price_a'], 'b': q['price_b'], 'mid': q['price_mid']}.get(side)
    first = conn.execute('SELECT provider, price_a, price_mid, price_b FROM market_quotes WHERE quote_id=?',
                         (row['market_quote_id'],)).fetchone() if row and row['market_quote_id'] else None
    sql = ("SELECT provider, line, price_a, price_mid, price_b, retrieved_at FROM market_quotes WHERE game_id=? AND market=? "
           "AND price_label='current' AND retrieved_at<=?" + (' AND provider=?' if first else '') +
           ' ORDER BY retrieved_at DESC, quote_id DESC LIMIT 1')
    now = conn.execute(sql, (pick['game_id'], pick['market'], stamp) + ((first['provider'],) if first else ())).fetchone()
    line_at = row['line'] if row else None
    moved = bool(row and row['line_source'] == 'bookmaker' and now is not None and now['line'] is not None
                 and line_at is not None and abs(now['line'] - line_at) > 1e-9)
    return dict(provider=now['provider'] if now else None, retrieved_at=now['retrieved_at'] if now else None,
                line_source=row['line_source'] if row else None, line_at_forecast=line_at,
                line_now=now['line'] if now else None, price_now=price(now), price_at_forecast=price(first), line_moved=moved)


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


def decimal_odds(american):
    """Decimal payout per 1 staked (stake included) for an American price. None when no price is stored or the stored
    value is not a possible American price (0 or between -100 and +100): no payout is computed from it (review F3)."""
    if american is None or -100 < american < 100:
        return None
    return 1 + american / 100 if american > 0 else 1 + 100 / -american


def _field(obj, key, default=None):
    """Picks carry percentage_history as a dict, or as the provider's record object before JSON encoding."""
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def leg_assessment(sport, pick):
    """What the reviewer weighs together for one market, side by side; facts only, no verdict and no threshold:
    the app probability and recommendation, the exact stored price and its break-even probability, the record of similar
    past picks with its 95% interval (the app's own Wilson interval, threshold_overview.wilson) and the evidence quality
    the app states. A negative model_gap is a warning that the price asks for more than the app estimates, not proof of a
    loss; a positive one is not a value claim (the estimate may be wrong; a past group does not fix today's probability)."""
    from app import markets, threshold_overview
    quote = pick.get('quote_check') or {}
    evidence = pick.get('evidence') or {}
    history = pick.get('percentage_history') or {}
    decimal = decimal_odds(quote.get('price_now'))
    break_even = None if decimal is None else 1 / decimal
    record = None
    if evidence.get('settled'):
        n = evidence['settled']
        record = dict(win_rate=evidence['win_rate'], settled=n,
                      interval=threshold_overview.wilson(round(evidence['win_rate'] * n), n), source=evidence.get('source'))
    return dict(probability=pick['probability'], price=quote.get('price_now'), decimal=decimal, break_even=break_even,
                model_gap=None if break_even is None else pick['probability'] - break_even, record=record,
                record_minus_break_even=None if record is None or break_even is None else
                [record['interval'][0] - break_even, record['interval'][1] - break_even],
                exact=f"{_field(history, 'wins', 0) or 0}-{_field(history, 'losses', 0) or 0} ({_field(history, 'settled', 0) or 0} settled)",
                app_rank=pick.get('game_rank'), skill_proven=pick.get('skill_proven'),
                line_source=quote.get('line_source') or _field(history, 'line_source'), app_note=markets.LIMITATIONS.get((sport, pick['market'])))


def attach_assessments(sport, picks):
    """Every snapshot pick carries its assessment (review F2: soccer too); a problem is reported on the pick, never hides it."""
    for pick in picks:
        try:
            pick['assessment'] = leg_assessment(sport, pick)
        except Exception as exc:
            pick['assessment'] = {'error': type(exc).__name__}


def ticket_assessment(legs):
    """The whole ticket: payout at the stored prices (decimal prices multiplied, as a parlay pays for distinct games), its
    break-even probability and the model estimate (independence assumption), plus each leg's own record of similar picks.
    No ticket-level range is computed from those records (review, Nov 8): multiplying historical intervals does not give
    today's ticket a win-probability interval. Lists legs priced above the model, unpriced legs and legs without a record."""
    rows = [(f"{leg['sport'].upper()} {leg['pick_label']}", leg.get('assessment') or leg_assessment(leg['sport'], leg)) for leg in legs]
    payout = math.prod(a['decimal'] for _, a in rows) if all(a['decimal'] for _, a in rows) else None
    estimate = math.prod(a['probability'] for _, a in rows)
    break_even = None if payout is None else 1 / payout
    return dict(legs=len(rows), payout=payout, break_even=break_even, model_estimate=estimate,
                model_minus_break_even=None if payout is None else estimate - break_even,
                leg_records=[dict(label=label, record=a['record']) for label, a in rows],
                priced_above_model=[label for label, a in rows if a['model_gap'] is not None and a['model_gap'] < 0],
                unpriced=[label for label, a in rows if a['decimal'] is None],
                no_record=[label for label, a in rows if a['record'] is None])


def assess(plan):
    """Read-only comparison of candidate tickets against the plan's snapshot (no fresh snapshot, archive, ledger or send)."""
    snap = json.loads(Path(plan['snapshot_path']).read_text())
    out = []
    for ticket in plan.get('tickets') or []:
        legs = []
        for leg in ticket['legs']:
            pick = next((p for p in (snap['sports'].get(leg['sport'], {}).get('report') or {}).get('picks', [])
                         if p.get('forecast_id') == leg['forecast_id']), None)
            if pick is None:
                raise ValueError('Selected forecast missing')
            legs.append(dict(pick, sport=leg['sport'], assessment=leg_assessment(leg['sport'], pick)))
        out.append({'label': ticket.get('label'), 'ticket': ticket_assessment(legs),
                    'legs': [dict(label=f"{l['sport'].upper()} {l['pick_label']}", market=l['market'], **l['assessment']) for l in legs]})
    return {'snapshot_as_of': snap['as_of'], 'tickets': out}


def soccer_section(now):
    """Live soccer picks (app/soccer_live.py): the sealed Elo's 90-minute home/draw/away forecast per match, made at or
    before 06:00 UTC on match day. Each pick is the model's most likely outcome with its exact probability; all three
    probabilities travel with it for review. Competitions refused by the completeness check have no picks and alert."""
    from app import config, db, soccer_live
    if not config.db_path('soccer').is_file():
        return {'error': 'forecasts_unavailable', 'reason': 'No live soccer store in this data directory.'}
    stamp = now.strftime('%Y-%m-%dT%H:%M:%SZ')
    conn = db.connect_readonly('soccer')
    try:
        if not soccer_live.has_tables(conn):
            return {'error': 'forecasts_unavailable', 'reason': 'Live soccer forecast tables are not migrated.'}
        today = now.date().isoformat()
        checks, after_cutoff = soccer_live.checks_for(conn, today), stamp > today + 'T06:00:00Z'
        refused = {}
        from app import intl_live
        for c in (*soccer_live.LAUNCH, *intl_live.LAUNCH):
            chk = checks.get(c)
            if chk is not None and not chk['ok']:
                refused[c] = chk['problems']
            elif chk is None and after_cutoff:          # before today's cutoff the run may still come
                refused[c] = ["no live forecast run for today's matches before the 06:00 UTC cutoff"]
        names = {'home': None, 'draw': 'Draw', 'away': None}
        picks = []
        for f in soccer_live.forecasts(conn, as_of=stamp):
            out, p = f['most_likely'], f['probabilities']
            label = (f['home'] + ' win' if out == 'home' else f['away'] + ' win' if out == 'away' else names['draw']) + ' (90 min)'
            grade = 'pending' if f['result'] is None else ('win' if f['result'] == out else 'loss')
            picks.append(dict(forecast_id=f['forecast_id'], game_id=f['match_id'], market='1x2_regulation',
                              market_title='Match result (90 min)', matchup=f"{f['home']} vs {f['away']}", pick_label=label,
                              outcome=out, probability=p[out], probabilities=p, status=f['status'], grade=grade,
                              generated_at=f['generated_at'], tipoff=f['kickoff_at'], cutoff_at=f['cutoff_at'],
                              competition=f['competition'], source_retrieved_at=f['source_retrieved_at'],
                              settlement_rule=f['settlement_rule']))
        live = [c for c in (*soccer_live.LAUNCH, *intl_live.LAUNCH) if c not in refused]
        return {'freshness': {'source_state': 'fresh' if live else 'stale', 'failing': [] if live else sorted(refused),
                              'competitions_live': live}, 'report': {'mode': 'real', 'picks': picks},
                'missing_due_games': [], 'refused': refused}
    finally:
        conn.close()


def eligible(pick, now):
    start = datetime.fromisoformat(pick['tipoff'].replace('Z', '+00:00'))
    made = datetime.fromisoformat(pick['generated_at'].replace('Z', '+00:00'))
    if pick.get('market') == '1x2_regulation':
        # Soccer is forecast at its evaluated point in time (06:00 UTC on match day or earlier), not in the last 2 hours;
        # the ticket window itself (start within 2 h of the check) is the same as for every sport.
        cutoff = datetime.fromisoformat(pick['cutoff_at'].replace('Z', '+00:00'))
        return (pick['status'] == 'scheduled' and pick['grade'] == 'pending'
                and now < start <= now + timedelta(hours=2) and made <= cutoff <= start)
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
        for row in conn.execute('SELECT snapshot_sha256,outcome,detail,payload FROM ticket_reviews WHERE day=? ORDER BY review_id', (day,)):
            if row['snapshot_sha256']:   # a refusal that says "review again" (code/skill changed, snapshot expired) is retryable
                latest[row['snapshot_sha256']] = row['outcome'] == 'operational_failure' and bool(
                    json.loads(row['payload']).get('retryable') or 'review again' in row['detail'])
    return {digest for digest, retry in latest.items() if retry}


def reviewed_history(day):
    """Forecast keys and games accounted for by the day's validated review archives (written only by a passing check).

    A review whose dispatch then failed retryably does not count, so the trigger fires again; uncertain/paused do count.
    A forecast whose source was unusable (stale/failing) in that review's snapshot could not be a leg there, so it is not
    counted either: once its source is usable again it triggers a review of its own.
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
        unusable = set()
        for sport, section in sections.items():
            for pick in (section.get('report') or {}).get('picks', []):
                key = f"{sport}:{pick.get('forecast_id')}"
                if key not in accounted:
                    continue
                try:
                    usable = source_usable(section, pick)
                except (KeyError, TypeError, ValueError, AttributeError):
                    usable = False
                if usable:
                    games.add((sport, pick['game_id']))
                else:
                    unusable.add(key)
        keys.update(set(accounted) - unusable)
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


def lock_facts(day, snaps):
    """Forecasts that cannot be dispatched because their game is already in a sent ticket with a different forecast: a
    fact the helper knows from the ledger, recorded per forecast so the reviewer writes reasons only for judgments."""
    used = prior_ticket_games(day)
    locks = {}
    for snap in snaps:
        now = _parse(snap['as_of'])
        for sport, section in snap['sports'].items():
            for pick in (section.get('report') or {}).get('picks', []):
                entry = used.get((sport, pick['game_id']))
                if entry and entry['forecast_id'] != pick['forecast_id'] and eligible(pick, now):
                    locks[f"{sport}:{pick['forecast_id']}"] = {
                        'matchup': pick.get('matchup'), 'market': pick.get('market'), 'pick_label': pick.get('pick_label'),
                        'ticket_forecast_id': entry['forecast_id'], 'ticket_pick': entry['pick_label'],
                        'tickets': sorted(set(entry['tickets']))}
    return locks


def _lock_reason(key, lock):
    sport = key.split(':')[0]
    return (f"Locked: {lock['matchup']} is already in reserved or sent ticket(s) {', '.join(lock['tickets'])} with forecast "
            f"{sport}:{lock['ticket_forecast_id']} ({lock['ticket_pick']}); only that forecast may be used again, so "
            f"{lock['pick_label']} ({lock['market']}) cannot be a leg.")


def _was_reviewable(pick, day):
    """A started game's forecast that a review could have used before the start. NBA/NHL/NFL forecasts are made in the two
    hours before the start. A soccer forecast is made at its cutoff, days earlier, so its own rule applies (as in
    `eligible`); the soccer list holds every recorded match, so only this Toronto day's matches that were actually
    played count."""
    tip, made = _parse(pick['tipoff']), _parse(pick['generated_at'])
    if pick.get('market') == '1x2_regulation':
        return (made <= _parse(pick['cutoff_at']) <= tip and tip.astimezone(TORONTO).date().isoformat() == day
                and pick.get('status') not in ('postponed', 'canceled', 'abandoned'))
    return tip - timedelta(hours=2) <= made <= tip


def _review_window(pick):
    """(from, to) during which this forecast was eligible for a ticket (see `eligible`), or None if it never was."""
    if pick.get('status') in ('postponed', 'canceled', 'abandoned'):   # never played: never a leg
        return None
    tip, made = _parse(pick['tipoff']), _parse(pick['generated_at'])
    opens = tip - timedelta(hours=2)
    if pick.get('market') == '1x2_regulation':
        return (opens, tip) if made <= _parse(pick['cutoff_at']) <= tip else None
    return (max(opens, made), tip) if opens <= made <= tip else None


def review_state(snap):
    """Deterministic review trigger: due when a fresh, combinable forecast has not been accounted for by any review today.

    Selection stays with the reviewer; this only says WHEN a review is needed and what is excluded and why.
    """
    if 'as_of' not in snap or 'date' not in snap:
        raise ValueError('Snapshot lacks as_of/date')
    now = _parse(snap['as_of'])
    ticketable, blocked, unavailable, started, delayed, moved = [], {}, {}, [], {}, []
    windows = {}   # (sport, game) -> when its forecasts were eligible: tells whether a lone game stayed alone
    for sport, section in snap['sports'].items():
        if section.get('error'):
            unavailable[sport] = dict(section)
            continue
        picks = section['report']['picks']
        current = [p for p in picks if eligible(p, now)]
        for p in picks:
            w = _review_window(p)
            if w:
                a, b = windows.get((sport, p['game_id']), w)
                windows[(sport, p['game_id'])] = (min(a, w[0]), max(b, w[1]))
        started += [(sport, p) for p in picks if _parse(p['tipoff']) <= now and _was_reviewable(p, snap['date'])]
        usable = [p for p in current if source_usable(section, p)]
        refused = [p for p in current if not source_usable(section, p)]
        if refused:
            blocked[sport] = {'issue': 'source_unavailable_or_stale', 'forecasts': len(refused),
                              'games': len({p['game_id'] for p in refused}),
                              'game_ids': sorted({p['game_id'] for p in refused})}
        if usable and section['freshness']['source_state'] != 'fresh':
            delayed[sport] = dict(section['refresh_delay'], forecasts=len(usable))
        for p in usable:
            q = p.get('quote_check') or {}
            if q.get('line_moved'):   # its probability is for a line the sportsbook no longer shows
                moved.append(dict(_describe(sport, p), line_at_forecast=q['line_at_forecast'], line_now=q['line_now']))
            else:
                ticketable.append((sport, p))
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
                                      'matchup': pick.get('matchup'), 'tipoff': pick['tipoff'], 'forecasts': 0,
                                      'first_forecast_at': pick['generated_at'],
                                      'review_window_from': (_parse(pick['tipoff']) - timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%SZ')})
            entry['forecasts'] += 1
            entry['first_forecast_at'] = min(entry['first_forecast_at'], pick['generated_at'])
    if missed:   # why: what the trigger recorded and which reviews failed while the game was still pending
        for key, entry in missed.items():
            entry['_other_windows'] = [w for other, w in windows.items() if other != key]
        try:
            explain_missed(snap['date'], missed.values())
        except Exception as exc:
            state['missed_explanation_error'] = type(exc).__name__
        for entry in missed.values():
            entry.pop('_other_windows', None)
    # Pending games this run does not send to review, each with its reason (recorded by preflight: record_observations).
    # A refused sport hides its games, so it is recorded for the sport unless it reports that it excludes no forecast now.
    waiting = [{'sport': sport, 'game_id': None, 'issue': section['error'], 'detail': str(section.get('reason') or '')}
               for sport, section in unavailable.items()
               if section['error'] != 'forecasts_unavailable' and section.get('unserved_forecasts') != 0]
    waiting += [{'sport': sport, 'game_id': game, 'issue': entry['issue'], 'detail': ''}
                for sport, entry in blocked.items() for game in entry['game_ids']]
    waiting += [{'sport': sport, 'game_id': game, 'issue': 'line_changed', 'detail': ''}
                for sport, game in sorted({(m['sport'], m['game_id']) for m in moved} - games)]
    if not due:   # a game with new forecasts that the trigger does not open a review for (a ticket needs two games)
        waiting += [{'sport': sport, 'game_id': game, 'issue': reason, 'detail': ''}
                    for sport, game in sorted({(u['sport'], u['game_id']) for u in new})]
    state.update(due=due, reason=reason, ticketable_forecasts=len(ticketable), ticketable_games=len(games),
                 reviewed_today=len(reviewed), unreviewed=unreviewed,
                 oldest_unreviewed_at=min((u['generated_at'] for u in new), default=None),
                 blocked=blocked, unavailable=unavailable, locked_games=locked,
                 missed_before_start=sorted(missed.values(), key=lambda m: (m['tipoff'], m['sport'], m['game_id'])))
    if waiting:   # pending games this run does not send to review, each with its reason (preflight records them)
        state['not_reviewed_now'] = waiting
    if delayed:   # stale only because the store's forecast job still holds the lock; legs disclose the refresh time
        state['delayed'] = delayed
    if moved:     # bookmaker line moved since the forecast: excluded until the app forecasts the new line
        state['line_changed'] = moved
    return state


REVIEW_DUE = 'review_due'


def record_observations(state, snap=None):
    """Append one record of this trigger run (ticket_trigger_runs), also when it saw nothing to record, and under it what
    it saw for each pending game: what stood between the game and a review (review_state's not_reviewed_now: a refused
    sport or source, a moved line, a single-game window), or that a review of it was due (REVIEW_DUE). A clean run
    leaves its run record, so a game that later starts unreviewed carries what the trigger saw at the checks on both
    sides of its start, including that a check saw no blocker. A run whose review state could not be evaluated is
    recorded with `due` NULL (its time comes from `snap`). Never selects or sends."""
    from app import telegram
    as_of, day = state.get('as_of') or snap['as_of'], state.get('date') or snap['date']
    rows = [(as_of, day, w['sport'], w['game_id'], w['issue'], w['detail'][:300]) for w in state.get('not_reviewed_now', [])]
    if state.get('due') is True:
        rows += [(as_of, day, sport, game, REVIEW_DUE, '')
                 for sport, game in sorted({(u['sport'], u['game_id']) for u in state['unreviewed']
                                            if not u['blocked_by_prior_ticket']})]
    with telegram.ledger() as conn:
        schema(conn)
        if 'run_id' not in {r[1] for r in conn.execute('PRAGMA table_info(ticket_trigger_observations)')}:
            raise ValueError('ticket_trigger_observations has no run_id column: it was created by an unreleased build; inspect the ledger')
        conn.execute('BEGIN IMMEDIATE')   # the run and its observations are stored together or not at all
        try:
            run = conn.execute('INSERT INTO ticket_trigger_runs (recorded_at,day,due,reason,observations) VALUES (?,?,?,?,?)',
                               (as_of, day, None if state.get('due') is None else int(state['due']), str(state.get('reason')),
                                len(rows))).lastrowid
            conn.executemany('INSERT INTO ticket_trigger_observations (run_id,recorded_at,day,sport,game_id,issue,detail) '
                             'VALUES (?,?,?,?,?,?,?)', [(run, *row) for row in rows])
            conn.execute('COMMIT')
        except BaseException:
            conn.execute('ROLLBACK')
            raise
    return len(rows)


def explain_missed(day, entries):
    """Attach to each game that started unreviewed what the trigger recorded, and the reviews that failed, in its review
    window: the two hours before its start, in which its forecasts were eligible (`review_window_from`).

    Two different facts are reported. `blocker_observed`: a blocker was recorded at some check in the window.
    `explained`: the ENTIRE window is accounted for, which needs more than one sighting:
    - a review failed after the trigger last said one was due; or
    - the last check before the start recorded a blocker for the game AND it is shown to have lasted to the start: the
      FIRST check at or after the start recorded the same blocker for that sport, or, for a single-game window, no
      other game became eligible between that check and the start.
    Every trigger run is recorded (ticket_trigger_runs), clean ones too, so "the last check before" and "the first check
    after" are the actual runs, not the last and next sightings: a blocker that had cleared at the last check before the
    start, or that was gone at the first check after it and came back later, leaves `explained` false, and
    `explanation` says which check and how many minutes are unaccounted for. No time threshold is used anywhere."""
    from app import telegram
    with telegram.ledger() as conn:
        schema(conn)
        runs = [dict(r) for r in conn.execute('SELECT * FROM ticket_trigger_runs WHERE day=? ORDER BY recorded_at, run_id', (day,))]
        seen = [dict(r) for r in conn.execute('SELECT * FROM ticket_trigger_observations WHERE day=? ORDER BY recorded_at, observation_id', (day,))]
        failures = [dict(r) for r in conn.execute("SELECT review_id,recorded_at,stage,outcome,detail FROM ticket_reviews "
                                                  "WHERE day=? AND outcome='operational_failure' ORDER BY review_id", (day,))]
    for entry in entries:
        lo, hi = _parse(entry['review_window_from']), _parse(entry['tipoff'])
        mine = [o for o in seen if o['sport'] == entry['sport'] and o['game_id'] in (None, entry['game_id'])
                and lo <= _parse(o['recorded_at']) < hi]
        blockers, due = {}, [o['recorded_at'] for o in mine if o['issue'] == REVIEW_DUE]
        for o in mine:
            if o['issue'] != REVIEW_DUE:
                item = blockers.setdefault((o['issue'], o['detail']), {'issue': o['issue'], 'detail': o['detail'],
                                           'first_seen_at': o['recorded_at'], 'last_seen_at': o['recorded_at'], 'observations': 0})
                item['last_seen_at'] = o['recorded_at']
                item['observations'] += 1
        failed = [dict(f, detail=f['detail'][:200]) for f in failures if lo <= _parse(f['recorded_at']) <= hi]
        before = [r for r in runs if lo <= _parse(r['recorded_at']) < hi]             # the checks in the window
        check = before[-1] if before else None                                         # the last check before the start
        after = next((r for r in runs if _parse(r['recorded_at']) >= hi), None)        # the first check at or after it
        last = mine[-1] if mine else None                                              # the last thing recorded for this game
        ago = lambda stamp: int((hi - _parse(stamp)).total_seconds() // 60)
        minutes = ago(last['recorded_at']) if last else None
        unknown = lambda run: run['due'] is None                                       # that run could not evaluate the state
        failed_after_due = bool(failed and (not due or max(f['recorded_at'] for f in failed) >= due[-1]))
        if failed_after_due:
            explained, why = True, 'A review failed after the trigger last reported one due.'
        elif check is None:
            explained, why = False, 'Nothing was recorded for this game in its review window: no trigger run saw it before its start.'
        elif last is None and _parse(check['recorded_at']) < _parse(entry['first_forecast_at']):
            # Nothing was recorded for the game and its current forecast (after the start: the latest version made before
            # it, often a re-issue minutes before) is younger than the last check, which could not have seen it.
            explained = False
            why = (f"No trigger run saw this game before its start: its current forecast was made {ago(entry['first_forecast_at'])} "
                   f"minutes before the start, after the last check ({ago(check['recorded_at'])} minutes before it).")
        elif last is None or last['run_id'] != check['run_id']:   # the last check before the start recorded nothing for it
            explained = False
            why = (f"The last check before the start, {ago(check['recorded_at'])} minutes before it, "
                   + ('could not evaluate the review state' if unknown(check) else
                      'recorded neither a blocker nor a due review for this game')
                   + (f"; '{last['issue']}' had been recorded {minutes} minutes before the start." if last else '.'))
        elif last['issue'] == REVIEW_DUE:
            explained, why = False, f"A review was due at the last check, {minutes} minutes before the start, and none finished."
        elif last['issue'] == 'single_game_window':
            at = _parse(last['recorded_at'])
            joined = any(a < hi and b > at for a, b in entry.get('_other_windows', []))
            explained = not joined
            why = ('Single-game window: no other game became eligible between the last check and the start.' if explained else
                   f"Single-game window at the last check, {minutes} minutes before the start, but another game became "
                   f"eligible before the start and no review ran.")
        else:
            open_end = f"so whether a review was possible in those {minutes} minutes is not established."
            lasted = after is not None and last['issue'] != 'line_changed' and any(   # another game's moved line says nothing
                o['run_id'] == after['run_id'] and o['sport'] == entry['sport'] and o['issue'] == last['issue'] for o in seen)
            explained = lasted
            if lasted:
                why = f"Blocker '{last['issue']}' was seen at the last check before the start and again at the first check at or after it."
            elif after is None:
                why = (f"Blocker '{last['issue']}' was last seen {minutes} minutes before the start and no check at or after the "
                       f"start is recorded yet, {open_end}")
            elif unknown(after):
                why = (f"Blocker '{last['issue']}' was last seen {minutes} minutes before the start and the first check at or "
                       f"after it ({after['recorded_at']}) could not evaluate the review state, {open_end}")
            elif not any(o['run_id'] == after['run_id'] and o['sport'] == entry['sport'] for o in seen):
                why = (f"Blocker '{last['issue']}' was last seen {minutes} minutes before the start and the first check at or "
                       f"after it ({after['recorded_at']}) recorded nothing for {entry['sport']}, {open_end}")
            else:
                why = (f"Blocker '{last['issue']}' was last seen {minutes} minutes before the start and was not seen at the "
                       f"first check at or after it ({after['recorded_at']}), {open_end}")
        entry['recorded_blockers'] = list(blockers.values())
        entry['review_due_seen'] = {'first_seen_at': due[0], 'last_seen_at': due[-1], 'observations': len(due)} if due else None
        entry['last_observation'] = {'issue': last['issue'], 'at': last['recorded_at'], 'minutes_before_start': minutes} if last else None
        entry['trigger_runs'] = {'in_window': len(before), 'last_before_start_at': check['recorded_at'] if check else None,
                                 'first_after_start_at': after['recorded_at'] if after else None}
        entry['failed_reviews'] = failed
        entry['blocker_observed'] = bool(blockers)
        entry['explained'] = explained
        entry['explanation'] = why


def _text(value, what):
    """A free-text plan field: non-text (e.g. a list of notes) is a plain validation refusal the reviewer can retry, never an
    AttributeError recorded as a backend failure (review 9, 2026-11-09T01:58Z)."""
    if not isinstance(value, str):
        raise ValueError(f'{what} must be text, not {type(value).__name__}; review again')
    return value.strip()


def check(plan):
    raw = Path(plan['snapshot_path']).read_bytes()
    old = json.loads(raw)
    fresh = snapshot()
    now = datetime.fromisoformat(fresh['as_of'].replace('Z', '+00:00'))
    age = now - datetime.fromisoformat(old['as_of'].replace('Z', '+00:00'))
    if old['date'] != fresh['date'] or not timedelta(0) <= age <= SNAPSHOT_TTL:
        raise ValueError('Snapshot expired; take a new snapshot and review again')
    review = plan['review']
    for field in ('skill_sha256', 'helper_sha256'):
        if review[field] != fresh[field] or old[field] != fresh[field]:
            raise ValueError('Skill or helper changed; review again')
    if review['snapshot_sha256'] != sha(raw) or not _text(review['notes'], 'review.notes'):
        raise ValueError('Missing or mismatched review')
    if not isinstance(review['markets_reviewed'], dict):
        raise ValueError('Record the review of each supported market')
    for sport, section in old['sports'].items():
        if 'report' in section:
            markets = {p['market'] for p in section['report']['picks']}
            covered = review['markets_reviewed'].get(sport, {})
            if not markets <= covered.keys() or any(not _text(covered[m], f'markets_reviewed.{sport}.{m}') for m in markets):
                raise ValueError('Every available market needs a review note')
    accounted = review.get('forecasts_reviewed', {})
    required = set()
    for snap in (old, fresh):
        at = datetime.fromisoformat(snap['as_of'].replace('Z', '+00:00'))
        for sport, section in snap['sports'].items():
            for pick in section.get('report', {}).get('picks', []):
                if eligible(pick, at):
                    required.add(f"{sport}:{pick['forecast_id']}")
    # A lock is a ledger fact, not a judgment: the helper records it for each locked forecast the reviewer left out, and
    # archives every lock beside the review. Reviewer-written entries are kept as written; text is never compared.
    locks, lock_error = {}, None
    try:
        locks = {key: lock for key, lock in lock_facts(old['date'], (old, fresh)).items() if key in required}
    except Exception as exc:   # without the ledger nothing is filled in: every forecast then needs the reviewer's entry
        lock_error = type(exc).__name__
    if isinstance(accounted, dict):
        for key, lock in locks.items():
            if key not in accounted:
                accounted[key] = {'decision': 'excluded', 'reason': _lock_reason(key, lock), 'recorded_by': 'helper'}
        review['forecasts_reviewed'] = accounted
    if not isinstance(accounted, dict) or any(
        key not in accounted or not isinstance(accounted[key], dict) or
        accounted[key].get('decision') not in ('candidate', 'excluded') or
        not str(accounted[key].get('reason', '')).strip() for key in required):
        raise ValueError('Every available pregame forecast needs a candidate/excluded decision and reason; re-review newly published picks')
    if not _text(review.get('combination_comparison', ''), 'review.combination_comparison'):
        raise ValueError('Compare combinations even when no new stakes can be allocated')
    if not isinstance(plan['tickets'], list) or len(plan['tickets']) > MAX_TICKETS:
        raise ValueError('At most six ticket options per review')
    seen, combinations, resolved = {}, set(), []
    for ticket in plan['tickets']:
        if not _text(ticket['label'], 'ticket label') or not MIN_LEGS <= len(ticket['legs']) <= MAX_LEGS:
            raise ValueError('A ticket needs a label and two to seven legs')
        if not isinstance(ticket.get('payout_assessment'), str) or not ticket['payout_assessment'].strip():
            raise ValueError('Each ticket needs a payout_assessment: judge its payout at the stored prices against its uncertainty; review again')
        stake = ticket.get('stake_usd')
        if stake is not None and (isinstance(stake, bool) or not isinstance(stake, (int, float))
                                  or not math.isfinite(stake) or stake <= 0):
            raise ValueError('An optional dollar stake must be a positive finite amount')
        legs, ticket_games = [], set()
        for leg in ticket['legs']:
            sport = leg['sport']
            decision = accounted.get(f"{sport}:{leg['forecast_id']}", {})
            if f"{sport}:{leg['forecast_id']}" in locks:   # send would refuse it without a retry; say so here instead
                lock = locks[f"{sport}:{leg['forecast_id']}"]
                raise ValueError(f"Selected forecast {sport}:{leg['forecast_id']} is locked: {lock['matchup']} is already in "
                                 f"ticket(s) {', '.join(lock['tickets'])} with forecast {sport}:{lock['ticket_forecast_id']}, and "
                                 f"shared games must preserve the identical forecast; review again")
            if decision.get('decision') != 'candidate':
                raise ValueError('Selected leg must have been reviewed as a candidate')
            sections = [old['sports'][sport], fresh['sports'][sport]]
            picks = []
            for section in sections:
                if not source_usable(section):
                    raise ValueError('Selected source unavailable or stale')
                pick = next((p for p in section['report']['picks'] if p.get('forecast_id') == leg['forecast_id']), None)
                if pick is None:
                    raise ValueError('Selected forecast missing')
                if not source_usable(section, pick):
                    raise ValueError('Selected source unavailable or stale for this game')
                picks.append(pick)
            refreshed = None if sections[1]['freshness']['source_state'] == 'fresh' else sections[1]['refresh_delay']['source_fetched_at']
            before, pick = picks
            if not eligible(pick, now):
                raise ValueError('Forecast is settled or outside its pregame window')
            for field in ('game_id', 'market', 'pick_label', 'probability', 'tipoff', 'generated_at'):
                if before[field] != pick[field]:
                    raise ValueError('Forecast changed; review again')
            quote = pick.get('quote_check') or {}
            if quote.get('line_moved'):
                raise ValueError(f"Line changed since forecast ({pick['matchup']} {pick['market']}: {quote['line_at_forecast']:g} "
                                 f"-> {quote['line_now']:g}); review again")
            key = (sport, pick['game_id'])
            if key in ticket_games or not _text(leg['rationale'], 'leg rationale'):
                raise ValueError('Repeated game within ticket or missing rationale')
            identity = (sport, pick['forecast_id'])
            if key in seen and seen[key] != identity:
                raise ValueError('Variations must preserve the same forecast for a shared game')
            ticket_games.add(key)
            seen[key] = identity
            legs.append({**pick, 'sport': sport, 'rationale': leg['rationale'], 'assessment': leg_assessment(sport, pick),
                         **({'status_refreshed_at': refreshed} if refreshed else {})})
        combination = tuple(sorted((p['sport'], p['forecast_id']) for p in legs))
        if combination in combinations:
            raise ValueError('Duplicate ticket combination')
        combinations.add(combination)
        signature = sha(json.dumps(combination).encode())
        resolved.append({'ticket_id': old['date'] + '-' + signature[:12], 'signature': signature,
                         'label': ticket['label'], 'legs': legs, 'stake_usd': ticket.get('stake_usd'),
                         'payout_assessment': ticket['payout_assessment'].strip(), 'assessment': ticket_assessment(legs)})
    text = ticket_text(fresh['date'], resolved)
    result = dict(snapshot=old, plan=plan, resolved=resolved, text=text, as_of=fresh['as_of'], date=fresh['date'],
                  locks=locks, **({'locks_error': lock_error} if lock_error else {}),
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
    write_once(archive, content)
    return result


def ticket_text(day, tickets, prior_exposure=None):
    lines = [f'<b>PROP51 · COMBINED PARLAY TICKETS · {day}</b>',
             'Multi-game combinations from app forecasts + AI selection. Not a single-game card.']
    if tickets:   # one-glance menu: size and model estimate of every option
        lines.append(f"{len(tickets)} option{'s' if len(tickets) > 1 else ''}: " + ' · '.join(
            f"#{n} {len(x['legs'])} legs {math.prod(p['probability'] for p in x['legs']):.0%}" +
            (lambda a: f" pays ×{a['payout']:.2f}" if a['payout'] else '')(x.get('assessment') or ticket_assessment(x['legs']))
            for n, x in enumerate(tickets, 1)))
    for number, ticket in enumerate(tickets, 1):
        kinds = ' + '.join(dict.fromkeys(bet_type(p) for p in ticket['legs']))
        lines.extend(['', f"<b>Ticket {number}: {escape(ticket['label'])}</b>",
                      f"{len(ticket['legs'])} legs · {escape(kinds)}",
                      f"ID {ticket['ticket_id']}" + (f" / Suggested stake ${ticket['stake_usd']:.2f}" if ticket.get('stake_usd') is not None else ''),
                      'Combine ALL these legs in ONE parlay:'])
        for leg_number, p in enumerate(ticket['legs'], 1):
            start = datetime.fromisoformat(p['tipoff'].replace('Z', '+00:00')).astimezone(TORONTO)
            q = p.get('quote_check') or {}
            price = ''
            if q.get('price_now') is not None:
                price = f" / {PROVIDER_SHORT.get(q['provider'], q['provider'])} {q['price_now']:+d}" + (
                    f" (was {q['price_at_forecast']:+d})" if q.get('price_at_forecast') not in (None, q['price_now']) else '')
            lines.append(f"{leg_number}. {p['sport'].upper()} / {escape(p['matchup'])} / {escape(bet_type(p))}: "
                         f"<b>{escape(p['pick_label'])}</b> / {start:%b %d %I:%M %p %Z} / {p['probability']:.1%}{escape(price)}")
        a = ticket.get('assessment') or ticket_assessment(ticket['legs'])   # the whole ticket: payout against uncertainty
        per_leg = [p.get('assessment') or leg_assessment(p['sport'], p) for p in ticket['legs']]
        def which(test):   # leg numbers, never labels again (review F1: keeps long menus inside one message)
            numbers = [n for n, x in enumerate(per_leg, 1) if test(x)]
            return '' if not numbers else 'all legs' if len(numbers) == len(per_leg) else (
                f"leg {numbers[0]}" if len(numbers) == 1 else 'legs ' + ', '.join(map(str, numbers)))
        if a['payout']:
            lines.append(f"Pays about ×{a['payout']:.2f} at stored prices; break-even {a['break_even']:.1%}; "
                         f"model estimate {a['model_estimate']:.1%} (independence assumption).")
        else:
            lines.append(f"Payout unknown (no stored price: {which(lambda x: x['decimal'] is None)}); "
                         f"model estimate {a['model_estimate']:.1%} (independence assumption).")
        if a['no_record']:
            lines.append(f"No record of similar picks: {which(lambda x: x['record'] is None)}.")
        if a['priced_above_model']:
            lines.append(f"Sportsbook implies a higher chance than the model: "
                         f"{which(lambda x: x['model_gap'] is not None and x['model_gap'] < 0)}.")
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
    delays = {}
    for p in (p for t in tickets for p in t['legs'] if p.get('status_refreshed_at')):
        delays[p['sport']] = min(delays.get(p['sport'], p['status_refreshed_at']), p['status_refreshed_at'])
    for sport, stamp in sorted(delays.items()):
        lines += ['', f"{sport.upper()} game status last refreshed {_parse(stamp).astimezone(TORONTO):%I:%M %p %Z} "
                      f"(app forecast job still running). Confirm each game is still on before betting."]
    if any(p['sport'] == 'soccer' for t in tickets for p in t['legs']):
        lines += ['', 'Soccer legs settle on the 90-minute result (home/draw/away incl. stoppage time); extra time and penalties do not count.']
    legs = list({f"{p['sport']}:{p['forecast_id']}": p for t in tickets for p in t['legs']}.values())
    model = [p for p in legs if (p.get('quote_check') or {}).get('line_source') == 'model']
    if model:
        lines += [''] + [f"{p['sport'].upper()} {escape(p['pick_label'])}: the app's own line; no sportsbook line stored." for p in model]
    quoted = [p['quote_check'] for p in legs if (p.get('quote_check') or {}).get('price_now') is not None]
    if quoted:
        oldest = min(quoted, key=lambda q: q['retrieved_at'])
        lines += ['', f"Prices: latest stored {escape(oldest['provider'])} quotes, "
                      f"{_parse(oldest['retrieved_at']).astimezone(TORONTO):%I:%M %p %Z}; confirm before betting. "
                      'Model estimates are not guaranteed win rates.']
    else:
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
    captions = ticket_captions(result)   # refuses before any reservation if a selection could not be shown in full
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
            captions = ticket_captions(result)
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
        write_once(archive, raw)
        # Keep original content/time as evidence; retries use the CURRENT attempt time.
        previous = conn.execute('SELECT status FROM deliveries WHERE delivery_key=?',(key,)).fetchone()
        image_key = 'ticket-image:' + sha(key.encode())
        pictured = conn.execute('SELECT 1 FROM ticket_images WHERE batch_key=?',(key + '#p1',)).fetchone() is not None
        if pictured or not (previous and previous['status'] == 'sent'):
            # Owner request Nov 9: a ticket batch arrives as pictures only, no long text message. Every ticket is on a
            # picture in full (ticket_pages); the first picture is sent under the batch key (the delivery every later step
            # reads) and the image receipt is recorded as that same Telegram message, so nothing is sent twice. Further
            # pictures have their own receipts. The exact text stays in the ledger and the archive.
            pictures = ticket_pictures(conn, key, result, directory)   # an archived picture that fails its hash raises: nothing is sent
            if pictures is None:
                # The renderer is unavailable. The ticket still arrives, as the short caption lines (never the long text),
                # and the failed image receipt raises the delivery alert, as a failed image always did.
                short = '\n\n'.join(escape(c) for c in captions)
                status = telegram.deliver(bot, conn, chat, key, result['text'], attempt_at, send_text=short)
                confirmed = conn.execute('SELECT status FROM deliveries WHERE delivery_key=?',(key,)).fetchone()
                if not confirmed or confirmed['status'] != 'sent':
                    return status
                conn.execute('INSERT OR IGNORE INTO deliveries (delivery_key,chat_id,text,status,attempted_at,error) VALUES (?,?,?,?,?,?)',
                             (image_key, str(chat), result['text'], 'failed', attempt_at, 'ticket image not rendered'))
                return 'partial_failed'
            statuses = [telegram.deliver(bot, conn, chat, key, result['text'], attempt_at, photo=pictures[0][0], caption=pictures[0][1])]
            confirmed = conn.execute('SELECT status,message_id FROM deliveries WHERE delivery_key=?',(key,)).fetchone()
            if not confirmed or confirmed['status'] != 'sent':
                return statuses[0]
            conn.execute('INSERT OR IGNORE INTO deliveries (delivery_key,chat_id,text,status,attempted_at,message_id) VALUES (?,?,?,?,?,?)',
                         (image_key, str(chat), result['text'], 'sent', attempt_at, confirmed['message_id']))
            for number, (photo, caption) in enumerate(pictures[1:], 2):
                page_key = f'{image_key}:p{number}'
                statuses.append(telegram.deliver(bot, conn, chat, page_key, result['text'], attempt_at, photo=photo, caption=caption))
                row = conn.execute('SELECT status FROM deliveries WHERE delivery_key=?',(page_key,)).fetchone()
                if not row or row['status'] != 'sent':
                    return 'partial_' + (row['status'] if row else statuses[-1])   # later pictures wait: order is kept
            return 'sent' if 'sent' in statuses else 'existing_sent'
        # A batch whose text the earlier release already sent keeps that release's text-then-image path.
        status = telegram.deliver(bot, conn, chat, key, result['text'], attempt_at)
        confirmed = conn.execute('SELECT status FROM deliveries WHERE delivery_key=?',(key,)).fetchone()
        if not confirmed or confirmed['status'] != 'sent':
            return status
        image_status = deliver_visual(bot, conn, chat, key, result, directory, attempt_at)
        if image_status not in ('sent','already_recorded'):
            return 'partial_' + image_status
        return 'existing_sent' if previous and previous['status']=='sent' and image_status=='already_recorded' else 'sent'


PAGE_LEGS = 9   # a picture holds at most CARD_TICKETS tickets and about this many legs, so it stays readable on a phone


def ticket_pages(tickets):
    """Tickets split over pictures, in order; every ticket is on exactly one picture, whole."""
    pages = []
    for t in tickets:
        if pages and len(pages[-1]) < CARD_TICKETS and sum(len(x['legs']) for x in pages[-1]) + len(t['legs']) <= PAGE_LEGS:
            pages[-1].append(t)
        else:
            pages.append([t])
    return pages


def ticket_captions(result):
    """One caption per picture: its tickets' legs with the stored price and what each pays at those prices. Telegram
    captions hold 1,024 characters. A caption that is too long first drops the ticket labels, then the prices; if the legs
    themselves still do not fit it raises: a selection is never cut off."""
    tickets = result['resolved']
    pages = ticket_pages(tickets)
    quoted = [p['quote_check']['retrieved_at'] for t in tickets for p in t['legs'] if (p.get('quote_check') or {}).get('price_now') is not None]
    footer = 'Each numbered ticket is ONE parlay with all its legs.' + (
        ' Prices: stored DraftKings quotes, ' + _parse(max(quoted)).astimezone(TORONTO).strftime('%I:%M %p %Z') + '; confirm before betting.' if quoted else '')
    out, number = [], 0
    for index, page in enumerate(pages, 1):
        first = number + 1

        def build(labels, prices):
            lines = ['PROP51 · COMBINED PARLAY TICKETS · ' + result['date'] + (f' · picture {index} of {len(pages)}' if len(pages) > 1 else '')]
            for n, t in enumerate(page, first):
                legs = ' + '.join(p['pick_label'] + (f" ({p['quote_check']['price_now']:+d})" if prices and (p.get('quote_check') or {}).get('price_now') is not None else '')
                                  for p in t['legs'])
                try:
                    payout = (t.get('assessment') or ticket_assessment(t['legs']))['payout']
                except Exception:
                    payout = None
                lines.append(f"#{n}" + (f" {t['label']}" if labels else '') + f": {legs}" + (f" — pays about ×{payout:.2f}" if payout else '')
                             + (f" — ${t['stake_usd']:.2f}" if t.get('stake_usd') is not None else ''))
            return '\n'.join(lines + [footer])
        caption = next((c for c in (build(True, True), build(False, True), build(False, False)) if len(c) <= 1024), None)
        if caption is None:
            raise ValueError('Ticket selections do not fit a picture caption; shorten the pick labels or split the batch')
        out.append(caption)
        number += len(page)
    return out


def ticket_pictures(conn, key, result, directory):
    """[(PNG bytes, caption)] for a batch, one per ticket_pages page, archived once in ticket_images (keys '<batch>#p<n>')
    and reused unchanged on every retry. None when the renderer cannot draw (nothing is archived). An archived picture
    whose bytes no longer match their recorded hash raises."""
    from app.telegram_card import render_ticket_card
    tickets = result['resolved']
    pages, captions = ticket_pages(tickets), ticket_captions(result)
    keys = [f'{key}#p{n}' for n in range(1, len(pages) + 1)]

    def rows():
        return [conn.execute('SELECT * FROM ticket_images WHERE batch_key=?',(k,)).fetchone() for k in keys]
    if not all(rows()):
        try:
            photos, number = [], 1
            for page in pages:
                photos.append(render_ticket_card(result['date'], page, number_from=number, total=len(tickets)))
                number += len(page)
        except Exception:
            return None
        for k, photo, caption in zip(keys, photos, captions):
            # Named by content: a file left by a crash before its record can never be mistaken for a later render.
            image_path = directory / (result['date'] + '-' + sha(k.encode())[:16] + '-' + sha(photo) + '.png')
            write_once(image_path, photo)
            conn.execute('INSERT OR IGNORE INTO ticket_images VALUES (?,?,?,?)',(k,str(image_path),sha(photo),caption))
    out = []
    for row in rows():
        photo = Path(row['path']).read_bytes()
        if sha(photo) != row['sha256']:
            raise ValueError('Archived ticket image hash mismatch')
        out.append((photo, row['caption']))
    return out


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
        try:
            photo = render_ticket_card(result['date'], result['resolved'])
        except Exception as exc:
            # The text is already delivered: leave a failed image receipt so the Toronto-day alert reports it.
            conn.execute('INSERT OR IGNORE INTO deliveries (delivery_key,chat_id,text,status,attempted_at,error) VALUES (?,?,?,?,?,?)',
                         ('ticket-image:' + sha(key.encode()), str(chat), result['text'], 'failed', attempt_at,
                          'ticket image not rendered: ' + type(exc).__name__))
            return 'failed'
        # Named by content: a file left by a crash before its record can never be mistaken for a later render.
        image_path = directory / (result['date'] + '-' + sha(key.encode())[:16] + '-' + sha(photo) + '.png')
        write_once(image_path, photo)
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


def write_once(path, content):
    """Content-addressed archive: an identical file written first by a concurrent process is the same evidence."""
    if path.exists():
        return
    try:
        with path.open('xb') as handle:
            handle.write(content)
    except FileExistsError:
        if path.read_bytes() != content:
            raise


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
      CREATE TABLE IF NOT EXISTS ticket_review_starts (
        snapshot_sha256 TEXT PRIMARY KEY, day TEXT NOT NULL, started_at TEXT NOT NULL, earliest_tipoff TEXT);
      CREATE TABLE IF NOT EXISTS ticket_trigger_runs (
        run_id INTEGER PRIMARY KEY AUTOINCREMENT, recorded_at TEXT NOT NULL, day TEXT NOT NULL, due INTEGER,
        reason TEXT NOT NULL, observations INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS ticket_trigger_observations (
        observation_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL REFERENCES ticket_trigger_runs(run_id),
        recorded_at TEXT NOT NULL, day TEXT NOT NULL, sport TEXT NOT NULL, game_id TEXT, issue TEXT NOT NULL, detail TEXT NOT NULL);
      CREATE TRIGGER IF NOT EXISTS ticket_trigger_runs_no_update BEFORE UPDATE ON ticket_trigger_runs
        BEGIN SELECT RAISE(ABORT,'ticket trigger runs are append-only'); END;
      CREATE TRIGGER IF NOT EXISTS ticket_trigger_runs_no_delete BEFORE DELETE ON ticket_trigger_runs
        BEGIN SELECT RAISE(ABORT,'ticket trigger runs are append-only'); END;
      CREATE TRIGGER IF NOT EXISTS ticket_trigger_observations_no_update BEFORE UPDATE ON ticket_trigger_observations
        BEGIN SELECT RAISE(ABORT,'ticket trigger observations are append-only'); END;
      CREATE TRIGGER IF NOT EXISTS ticket_trigger_observations_no_delete BEFORE DELETE ON ticket_trigger_observations
        BEGIN SELECT RAISE(ABORT,'ticket trigger observations are append-only'); END;
      CREATE TRIGGER IF NOT EXISTS ticket_review_starts_no_update BEFORE UPDATE ON ticket_review_starts
        BEGIN SELECT RAISE(ABORT,'ticket review starts are append-only'); END;
      CREATE TRIGGER IF NOT EXISTS ticket_review_starts_no_delete BEFORE DELETE ON ticket_review_starts
        BEGIN SELECT RAISE(ABORT,'ticket review starts are append-only'); END;
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
        if section.get('refused'):
            alerts.append({'sport': sport, 'issue': 'inputs_refused', 'competitions': sorted(section['refused'])})
        if section.get('missing_due_games'):
            alerts.append({'sport': sport, 'issue': 'missing_due_forecasts', 'games': section['missing_due_games']})
    return alerts


def grade_leg(leg, as_of):
    """Resolve the immutable forecast ID, not today's replacement canonical pick."""
    from app import db, coverage
    if leg['sport'] == 'soccer':
        from app import soccer_live
        conn = db.connect_readonly('soccer')
        try:
            g = soccer_live.grade(conn, leg['forecast_id'], leg['outcome'], as_of)
        finally:
            conn.close()
        if g['match_id'] != leg['game_id']:
            raise ValueError('Archived forecast identity mismatch')
        return {'sport': 'soccer', 'forecast_id': leg['forecast_id'], 'game_id': leg['game_id'], 'pick_label': leg['pick_label'],
                'market': '1x2_regulation', 'line': None, 'grade': g['grade'], 'home_score': g['home_score'],
                'away_score': g['away_score'], 'result_available_at': g['result_available_at']}
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


CORRECTED_LINE = 'Corrected result: it replaces the result sent before.'
SETTLED_GRADES = ('win', 'loss', 'push', 'void')


REACHED = ('sent', 'uncertain', 'sending')   # a notice the user has or may have; one Telegram rejected or deferred is neither


def _result_receipts(conn, chat, ticket_id):
    """A ticket's result-notice receipts, newest first."""
    return conn.execute("SELECT delivery_key,text,status,attempted_at FROM deliveries WHERE chat_id=? AND delivery_key LIKE ? "
                        "ORDER BY attempted_at DESC, rowid DESC", (chat, 'ticket-result:' + ticket_id + ':%')).fetchall()


def undelivered_result(conn, chat, result, key):
    """The entry for a result notice that is owed now and that Telegram rejected, or None. A rejected notice is final: it
    is never sent again and its receipt is never changed, so without this it would drop out of sight while the user
    keeps the result sent before (a WIN whose LOSS correction was rejected). It is listed by every settlement until a
    notice that reaches the user says the current result, or the result returns to what they have."""
    rows = _result_receipts(conn, chat, result['ticket_id'])
    owed = next((r for r in rows if r['delivery_key'] == key), None)
    if not owed or owed['status'] != 'failed':
        return None
    last = next((r for r in rows if r['status'] in REACHED), None)
    return {'ticket_id': result['ticket_id'], 'grade': result['grade'], 'status': 'failed', 'rejected_at': owed['attempted_at'],
            'delivery_key': key, 'correction': owed['text'].endswith(CORRECTED_LINE),
            'last_notice': {'grade': last['text'].split('\n')[0].rsplit(' / ', 1)[-1], 'status': last['status'],
                            'at': last['attempted_at']} if last else None}


def result_notice(conn, chat, ticket_id, grade, legs, digest):
    """(delivery key, text) of the result notice a ticket is owed now, or None. Every reading is archived in ticket_grades;
    what the user is TOLD is decided here, against the last notice that reached them for this ticket (sent or
    unconfirmed; one Telegram rejected or deferred did not reach them):
    - nothing announced yet: the first settled result (a loss as soon as one leg loses, a win or void once all are in);
    - a genuine correction: a leg announced as settled (win/loss/push/void) now has another settled grade. Announced
      with the correction line, also when it reopens the ticket (PENDING);
    - one final recap: every leg is settled now and the announced notice still had an open leg;
    - otherwise nothing: an unchanged losing ticket is not announced again when another leg settles, and a leg that
      reads as open for a while (or a corrected score with the same grades) changes nothing the user was told.
    When nothing new is owed the announced notice's own key is returned, so delivery reports it as already recorded.
    A deferred notice with exactly the owed text is retried under its own key. A REJECTED notice with exactly the owed
    text, newer than the last notice that reached the user, keeps its own key too: the text is not attempted again
    under a new key when only the reading's payload changed (a score, a result time), it stays listed as undelivered
    (`undelivered_result`) and its single alert key stays the same."""
    lines = [(str(p.get('pick_label', p['forecast_id'])), p['grade']) for p in legs]
    text = f"PARLAY RESULT / {ticket_id} / {grade.upper()}\n" + '\n'.join(f"{label}: {g}" for label, g in lines)
    said = lambda row: row['text'].split('\n' + CORRECTED_LINE)[0]
    rows = _result_receipts(conn, chat, ticket_id)
    announced = next((r for r in rows if r['status'] in REACHED), None)
    settled_now = all(g in SETTLED_GRADES for _, g in lines)
    if not announced:
        owed = text if grade != 'pending' else None
    else:
        earlier = [line.rsplit(': ', 1)[1] for line in said(announced).split('\n')[1:] if ': ' in line]
        comparable = len(earlier) == len(lines)
        corrected = comparable and any(old in SETTLED_GRADES and new in SETTLED_GRADES and old != new
                                       for old, (_, new) in zip(earlier, lines))
        was_final = comparable and all(old in SETTLED_GRADES for old in earlier)
        if corrected:
            owed = text + '\n' + CORRECTED_LINE
        elif said(announced) != text and settled_now and grade != 'pending' and not was_final:
            owed = text
        else:
            owed = None
    if owed is None:
        return (announced['delivery_key'], announced['text']) if announced else None
    same = next((r for r in rows if r['text'] == owed and (r['status'] == 'retry' or (
        r['status'] == 'failed' and (announced is None or r['attempted_at'] >= announced['attempted_at'])))), None)
    return (same['delivery_key'], same['text']) if same else ('ticket-result:' + ticket_id + ':' + digest, owed)


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
                # Append-only evidence: a row is added whenever the result differs from the ticket's latest stored result,
                # also when it returns to an earlier one (that row names the result it supersedes, so its evidence hash
                # is new). First and unchanged results keep the original hashing. A result with a leg that could not
                # be graded ('unavailable') is a failed reading: stored as before, skipped when looking for the latest
                # result. What is ANNOUNCED is decided separately (result_notice).
                raw = json.dumps(result,sort_keys=True)
                digest = sha(raw.encode())
                if any(p['grade']=='unavailable' for p in legs):
                    conn.execute('INSERT OR IGNORE INTO ticket_grades VALUES (?,?,?,?)', (t['ticket_id'],digest,now,raw))
                else:
                    latest = None
                    for row in conn.execute('SELECT evidence_hash,payload FROM ticket_grades WHERE ticket_id=? ORDER BY as_of DESC, rowid DESC', (t['ticket_id'],)):
                        stored = json.loads(row['payload'])
                        if not any(p.get('grade')=='unavailable' for p in stored.get('legs', [])):
                            latest = (row['evidence_hash'], json.dumps({k:v for k,v in stored.items() if k!='supersedes'},sort_keys=True))
                            break
                    if latest and latest[1] == raw:
                        digest = latest[0]
                    else:
                        if conn.execute('SELECT 1 FROM ticket_grades WHERE ticket_id=? AND evidence_hash=?', (t['ticket_id'],digest)).fetchone():
                            raw = json.dumps({**result, 'supersedes': latest[0]},sort_keys=True)
                            digest = sha(raw.encode())
                        conn.execute('INSERT OR IGNORE INTO ticket_grades VALUES (?,?,?,?)', (t['ticket_id'],digest,now,raw))
                report['tickets'].append(result)
                # Only confirmed sent batches receive a settlement notification.
                batch = conn.execute('SELECT batch_key FROM ticket_items WHERE ticket_id=? AND chat_id=?', (t['ticket_id'],chat)).fetchone()
                confirmed = batch and conn.execute("SELECT 1 FROM deliveries WHERE delivery_key=? AND status='sent'",(batch['batch_key'],)).fetchone()
                # A result with a leg that could not be read is stored above but never announced.
                if confirmed and not any(p['grade']=='unavailable' for p in legs):
                    owed = result_notice(conn, chat, t['ticket_id'], result['grade'], legs, digest)
                    if owed:
                        notices.append((result, *owed))
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
                            continue
                        if result['notification'] not in ('sent','already_recorded'):
                            report['notification_errors'].append({'ticket_id':result['ticket_id'],'status':result['notification']})
            except Exception as exc:  # grades are committed; no notification problem (even a malformed reply) may abort settlement
                report['notification_errors'].append({'error':type(exc).__name__})
        # Owed result notices Telegram rejected: listed by every run (notices on or off) until the user has the current
        # result. Only the run in which the rejection happens reports it under notification_errors, as before.
        report['undelivered_results'] = []
        try:
            report['undelivered_results'] = [u for result,key,text in notices for u in [undelivered_result(conn,chat,result,key)] if u]
        except Exception as exc:  # the list is informational; a read problem here must not end the run after grades are committed
            report['undelivered_error'] = type(exc).__name__
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


def notify_alerts(snap, settlement_errors, extra=(), undelivered=()):
    from app import config, telegram
    issues = snap.get('alerts', []) + [{'sport':'tickets','issue':'settlement_unavailable'} for _ in settlement_errors] + list(extra)
    out = []
    settings = config.telegram_settings()
    bot = telegram.Telegram(settings.get('TELEGRAM_BOT_TOKEN')); bot.identity()
    with telegram.ledger() as conn:
        if not telegram.controls(bot,settings,conn):
            return ['paused']
        for issue in issues:
            if issue['issue'] in QUIET_HEALTH:
                out.append('not_sent_quiet')
                continue
            # One alert per issue/sport/day, no repeated hourly noise.
            key = 'ticket-health:' + snap['date'] + ':' + issue['sport'] + ':' + issue['issue'] + (':' + issue['dedup'] if issue.get('dedup') else '')
            text = 'PROP51 / REVIEW BLOCKER / ' + issue['sport'].upper() + '\n' + issue['issue'] + ''.join(
                f"\n{field}: {issue[field]}" for field in ('reason', 'unserved_forecasts', 'unserved_games', 'detail') if field in issue)
            out.append(telegram.deliver(bot,conn,settings['TELEGRAM_CHAT_ID'],key,text,snap['as_of']))
        for u in undelivered:
            # One alert per rejected result notice (its key has no date): the notice itself is never sent again.
            last = u['last_notice']
            out.append(telegram.deliver(bot,conn,settings['TELEGRAM_CHAT_ID'],'ticket-health:result-notice:'+sha(u['delivery_key'].encode())[:16],
                f"PROP51 / RESULT NOTICE NOT DELIVERED / {u['ticket_id']}\nTelegram rejected the {u['grade'].upper()} result notice"
                f"{' (a correction)' if u['correction'] else ''} at {u['rejected_at']}. "
                + (f"The last result notice on record is {last['grade']} ({last['status']})." if last else 'No result notice was delivered before.')
                + ' It is not sent again automatically and this alert is not repeated, also if it is owed again later; inspect the receipt.',snap['as_of']))
        start = datetime.fromisoformat(snap['date']).replace(tzinfo=TORONTO)
        end = start + timedelta(days=1)
        rows = conn.execute("SELECT delivery_key,status FROM deliveries WHERE chat_id=? "
                            "AND status IN ('uncertain','sending','failed','retry') AND attempted_at>=? AND attempted_at<? "
                            "AND (delivery_key LIKE 'ai-ticket-review-v%:%' OR delivery_key LIKE 'ticket-image:%')",
                            (str(settings['TELEGRAM_CHAT_ID']),start.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                             end.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))).fetchall()
        # A reservation committed without any delivery claim (a crash in between) keeps its stake but sent nothing.
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ticket_batches'").fetchone():
            rows += [{'delivery_key': r['batch_key'], 'status': 'unclaimed'} for r in conn.execute(
                'SELECT batch_key FROM ticket_batches b WHERE chat_id=? AND day=? AND NOT EXISTS '
                '(SELECT 1 FROM deliveries d WHERE d.delivery_key=b.batch_key)',
                (str(settings['TELEGRAM_CHAT_ID']), snap['date']))]
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


def _snapshot_day(plan):
    """The slate date of the snapshot a plan reviewed, or None when the plan or its snapshot cannot be read."""
    try:
        day = json.loads(Path(plan['snapshot_path']).read_text())['date']
        return day if isinstance(day, str) and len(day) == 10 else None
    except Exception:
        return None


def record_review(stage, outcome, detail, *, plan=None, result=None, retryable=False, snapshot_sha256=None, day=None):
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
    # A refused review belongs to the day of the snapshot it reviewed (the trigger looks failures up by that day), not to
    # the wall-clock day: around Toronto midnight, and in tests with a fixed slate date, the two differ.
    day = result['date'] if result else day or _snapshot_day(plan) or datetime.now(timezone.utc).astimezone(TORONTO).date().isoformat()
    with telegram.ledger() as conn:
        schema(conn)
        cursor = conn.execute('INSERT INTO ticket_reviews (recorded_at,day,stage,outcome,detail,snapshot_sha256,snapshot_as_of,payload) '
                              'VALUES (?,?,?,?,?,?,?,?)',
                              (_stamp(), day, stage, outcome, str(detail)[:1000], review.get('snapshot_sha256') or snapshot_sha256,
                               result['snapshot']['as_of'] if result else None, json.dumps(payload, sort_keys=True)))
        return cursor.lastrowid


def record_start(digest, snap):
    """A reviewer's snapshot (the `snapshot` action) starts a review; preflight later checks that it finished."""
    from app import telegram
    now = _parse(snap['as_of'])
    tips = [p['tipoff'] for s in snap['sports'].values() for p in (s.get('report') or {}).get('picks', []) if eligible(p, now)]
    with telegram.ledger() as conn:
        schema(conn)
        conn.execute('INSERT OR IGNORE INTO ticket_review_starts VALUES (?,?,?,?)', (digest, snap['date'], snap['as_of'], min(tips, default=None)))
    return True


def record_interrupted(snap):
    """Reviews started today that can no longer finish (snapshot past SNAPSHOT_TTL) without any check/send outcome or
    archive: each is recorded once as a retryable operational failure, so the trigger stays due and the gap is visible."""
    from app import config, telegram
    now, day = _parse(snap['as_of']), snap['date']
    archived = set()
    for path in (config.data_dir() / 'reports/tickets/reviews').glob(day + '-*.json'):
        try:
            archived.add(json.loads(path.read_text())['plan']['review'].get('snapshot_sha256'))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    with telegram.ledger() as conn:
        schema(conn)
        done = {r[0] for r in conn.execute('SELECT snapshot_sha256 FROM ticket_reviews WHERE day=? AND snapshot_sha256 IS NOT NULL', (day,))}
        starts = [dict(r) for r in conn.execute('SELECT * FROM ticket_review_starts WHERE day=? ORDER BY started_at', (day,))]
    gone = [s for s in starts if s['snapshot_sha256'] not in done | archived and now - _parse(s['started_at']) > SNAPSHOT_TTL]
    for s in gone:
        record_review('check', 'operational_failure', f"review interrupted: snapshot {s['started_at']} was never checked or sent "
                      f"before it expired; earliest game {s['earliest_tipoff']}", retryable=True,
                      snapshot_sha256=s['snapshot_sha256'], day=day)
    return [{k: s[k] for k in ('snapshot_sha256', 'started_at', 'earliest_tipoff')} for s in gone]


def interruption_alerts(gone):
    fmt = lambda stamp: f"{_parse(stamp).astimezone(TORONTO):%I:%M %p %Z}" if stamp else 'none pending'
    return [dict(sport='tickets', issue='review_interrupted', dedup=g['snapshot_sha256'][:12],
                 detail=f"review snapshot {fmt(g['started_at'])} never checked or sent; earliest game {fmt(g['earliest_tipoff'])}")
            for g in gone]


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
            for row in conn.execute("SELECT status,attempted_at,message_id,next_attempt_at FROM deliveries WHERE delivery_key LIKE ? ORDER BY delivery_key",
                                    ('ticket-image:' + sha(batch['batch_key'].encode()) + ':p%',)).fetchall():
                out.append({'kind': 'ticket_image_page', 'batch_key': batch['batch_key'], **dict(row)})
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('snapshot', 'check', 'send', 'settle', 'preflight', 'status', 'personal', 'preview', 'assess'))
    parser.add_argument('--notify', action='store_true', help='Send result/failure notifications to the existing paired chat')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--brief', action='store_true', help='preflight: print only the review trigger, alerts and settlement summary')
    args = parser.parse_args()
    from app import release
    if release.enabled():  # guarded release: refuse before any snapshot, ledger or Telegram access
        info = release.delivery_check()
        if info['problems']:
            print(json.dumps({'status': 'failed', 'reason': 'release guard', **info}))
            return 1
    if args.action in ('check', 'send'):
        return review_command(args)
    try:
        if args.action in ('settle', 'preflight'):
            report = settle(notify=args.notify)
            if args.action == 'preflight':
                try:
                    snap = snapshot()
                except Exception:   # best effort: leave a run record with the clock's time, so the next run does not stand in for this one
                    moment = datetime.now(timezone.utc)
                    try:
                        record_observations({'due': None, 'reason': 'snapshot_unavailable'},
                                            {'as_of': _stamp(moment), 'date': moment.astimezone(TORONTO).date().isoformat()})
                    except Exception:
                        pass
                    raise
                try:
                    report['review'] = review_state(snap)
                except Exception as exc:
                    report['review'] = {'due': None, 'reason': 'review_state_unavailable', 'error': type(exc).__name__}
                try:
                    record_observations(report['review'], snap)
                except Exception as exc:   # recording never blocks settlement, alerts or the trigger
                    report['review']['observations_error'] = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                try:
                    gone = record_interrupted(snap)
                except Exception as exc:
                    gone, report['review']['interrupted_error'] = [], type(exc).__name__
                if gone:   # a started review that never reached check/send: resumed by this run (still due) and alerted once
                    report['review']['interrupted'] = gone
                report['snapshot'] = snap
                if args.notify:
                    try:
                        report['alerts'] = notify_alerts(report['snapshot'], report['errors'], interruption_alerts(gone),
                                                         report.get('undelivered_results', ()))
                    except Exception as exc:  # keep the committed settlement report visible when alerts cannot go out
                        report.update(status='failed', alerts_error=type(exc).__name__)
                        print(json.dumps(report))
                        return 1
                if args.brief:   # the trigger check runs often; the full snapshot is taken separately when a review is due
                    report = {'as_of': report.get('as_of'), 'errors': report['errors'],
                              'notification_errors': report.get('notification_errors', []), 'alerts': report.get('alerts', []),
                              'snapshot_alerts': snap.get('alerts', []), 'review': report['review'],
                              'settlement': [{k: t.get(k) for k in ('ticket_id', 'grade', 'notification')} for t in report.get('tickets', [])],
                              **({'undelivered_results': report['undelivered_results']} if report.get('undelivered_results') else {}),
                              **({'undelivered_error': report['undelivered_error']} if report.get('undelivered_error') else {})}
            print(json.dumps(report))
        elif args.action == 'status':
            snap = snapshot()
            print(json.dumps({'as_of': snap['as_of'], 'date': snap['date'], 'review': review_state(snap),
                              'outcomes': review_outcomes(snap['date']), 'ticket_deliveries': ticket_deliveries(snap['date'])}))
        elif args.action == 'preview':
            status = send_preview(args.plan)
            print(json.dumps({'status':status}))
            return 0 if status in ('sent','already_recorded') else 1
        elif args.action == 'assess':   # compare candidate tickets before check: payout, break-even, estimate, records
            print(json.dumps(assess(json.loads(args.plan.read_text())), default=str))
        elif args.action == 'personal':
            print(json.dumps(record_personal(args.plan)))
        elif args.action == 'snapshot':
            snap = snapshot()
            raw = json.dumps(snap, sort_keys=True, indent=2).encode()
            with args.output.open('xb') as handle:
                handle.write(raw)
            try:
                started = record_start(sha(raw), snap)
            except Exception as exc:   # never blocks the review; preflight just cannot see this start
                started = 'not_recorded:' + type(exc).__name__
            print(json.dumps({'snapshot_path': str(args.output), 'snapshot_sha256': sha(raw), 'review_start_recorded': started}))
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
        failure['recorded'] = _safe_record('check', 'operational_failure', 'validation refused: ' + failure['reason'], plan=plan,
                                           retryable='review again' in failure['reason'])
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
