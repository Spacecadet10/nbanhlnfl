import sys, json
sys.path.insert(0, __import__('os').path.dirname(__file__))
from harness import *

TICKETS = [ticket([('nba', 1), ('nhl', 2)], 3), ticket([('nba', 1), ('nfl', 3)], 1)]


def item1(kind):
    print(f'\n=== ITEM 1 text {kind} ===')
    d = new_env('i1-' + kind)
    SCRIPT['sendMessage'] = ['connect' if kind == 'connect' else ('status', 429, {'ok': False, 'error_code': 429, 'description': 'Too Many Requests', 'parameters': {'retry_after': 30}})]
    plan, pf = at('2026-11-08T17:00:00Z', TICKETS)
    code, out = run_main(['send', '--plan', str(pf)])
    dl = rows('select delivery_key,status,attempted_at,next_attempt_at,error from deliveries')
    print('T0 exit', code, out, dl, methods())
    check(f'1.{kind} first send status retry', json.loads(out)['status'] == 'retry', out)
    check(f'1.{kind} exit nonzero', code != 0, code)
    check(f'1.{kind} next_attempt_at=17:01', dl and dl[0]['next_attempt_at'] == '2026-11-08T17:01:00Z', dl)
    batch = rows('select payload from ticket_batches')[0]['payload']
    archived_text = json.loads(batch)['text']
    first_text = [i['json']['text'] for m, i in CALLS if m == 'sendMessage'][0]
    items0 = rows('select * from ticket_items')
    CALLS.clear()
    plan, pf = at('2026-11-08T17:00:30Z', TICKETS)
    code, out = run_main(['send', '--plan', str(pf)])
    print('T1 exit', code, out, methods())
    check(f'1.{kind} before next_attempt -> retry_pending', json.loads(out)['status'] == 'retry_pending' and 'sendMessage' not in methods(), (out, methods()))
    check(f'1.{kind} retry_pending exit nonzero', code != 0, code)
    CALLS.clear()
    plan, pf = at('2026-11-08T17:03:00Z', TICKETS)
    code, out = run_main(['send', '--plan', str(pf)])
    dl = rows('select delivery_key,status,attempted_at,next_attempt_at,message_id from deliveries order by delivery_key')
    print('T2 exit', code, out, methods(), dl)
    sm = [i['json']['text'] for m, i in CALLS if m == 'sendMessage']
    check(f'1.{kind} later send status sent exit0', json.loads(out)['status'] == 'sent' and code == 0, (code, out))
    check(f'1.{kind} resent text == archived original == first attempt', len(sm) == 1 and sm[0] == archived_text == first_text)
    tdl = [r for r in dl if r['delivery_key'].startswith('ai-ticket-review-v3:')][0]
    check(f'1.{kind} attempted_at is current attempt', tdl['attempted_at'] == '2026-11-08T17:03:00Z' and tdl['status'] == 'sent', tdl)
    items1 = rows('select * from ticket_items'); batches = rows('select count(*) n from ticket_batches')
    check(f'1.{kind} ticket_items unchanged, one per ticket, 1 batch', items0 == items1 and len(items1) == 2 and batches[0]['n'] == 1, (len(items1), batches))
    stakes = [x['stake_usd'] for x in json.loads(rows('select payload from ticket_batches')[0]['payload'])['resolved']]
    check(f'1.{kind} stakes unchanged', stakes == [3, 1], stakes)
    check(f'1.{kind} sendPhoto follows sendMessage', [m for m, _ in sends()] == ['sendMessage', 'sendPhoto'], methods())


def item2_text(kind):
    print(f'\n=== ITEM 2 text {kind} ===')
    new_env('i2-' + kind)
    SCRIPT['sendMessage'] = ['readtimeout' if kind == 'readtimeout' else ('status', 500, {'ok': False, 'description': 'Internal'})]
    plan, pf = at('2026-11-08T17:00:00Z', TICKETS)
    code, out = run_main(['send', '--plan', str(pf)])
    print('T0', code, out, rows('select status,error from deliveries'))
    check(f'2.{kind} first status uncertain, exit!=0', json.loads(out)['status'] == 'uncertain' and code != 0, out)
    for ts in ('2026-11-08T17:03:00Z', '2026-11-08T17:09:00Z'):
        CALLS.clear()
        plan, pf = at(ts, TICKETS)
        code, out = run_main(['send', '--plan', str(pf)])
        print(ts, code, out, methods())
        check(f'2.{kind} {ts} existing_uncertain, no sendMessage, exit!=0', json.loads(out)['status'] == 'existing_uncertain' and 'sendMessage' not in methods() and code != 0, out)


def item2_image(kind):
    print(f'\n=== ITEM 2 image {kind} ===')
    new_env('i2img-' + kind)
    SCRIPT['sendPhoto'] = ['readtimeout' if kind == 'uncertain' else 'connect']
    plan, pf = at('2026-11-08T17:00:00Z', TICKETS)
    code, out = run_main(['send', '--plan', str(pf)])
    first_photo = [i['photo'] for m, i in CALLS if m == 'sendPhoto'][0]
    print('T0', code, out, rows('select delivery_key,status,next_attempt_at from deliveries'))
    exp = 'partial_uncertain' if kind == 'uncertain' else 'partial_retry'
    check(f'2.img-{kind} first status {exp}, exit!=0', json.loads(out)['status'] == exp and code != 0, out)
    CALLS.clear()
    plan, pf = at('2026-11-08T17:03:00Z', TICKETS)
    code, out = run_main(['send', '--plan', str(pf)])
    print('T2', code, out, methods(), rows('select delivery_key,status,attempted_at from deliveries'))
    if kind == 'uncertain':
        check('2.img-uncertain later: neither sendMessage nor sendPhoto', not sends() and code != 0, (out, methods()))
    else:
        ph = [i['photo'] for m, i in CALLS if m == 'sendPhoto']
        check('2.img-connect later: ONLY photo resent, same bytes, exit0', [m for m, _ in sends()] == ['sendPhoto'] and ph[0] == first_photo and code == 0 and json.loads(out)['status'] == 'sent', (out, methods()))


item1('connect'); item1('429')
item2_text('readtimeout'); item2_text('500')
item2_image('uncertain'); item2_image('connect')
print('\nSUMMARY', sum(r for _, r in RESULTS), '/', len(RESULTS))
