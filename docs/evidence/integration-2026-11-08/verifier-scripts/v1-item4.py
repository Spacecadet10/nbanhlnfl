import sys, json, hashlib
sys.path.insert(0, __import__('os').path.dirname(__file__))
from harness import *


def seed(key, status, stamp):
    c = db()
    c.execute('insert into deliveries(delivery_key,chat_id,text,status,attempted_at) values(?,?,?,?,?) '
              'on conflict(delivery_key) do update set status=excluded.status, attempted_at=excluded.attempted_at', (key, CHAT, 'x', status, stamp))
    c.commit(); c.close()


def alerts():
    return [i['json']['text'] for m, i in CALLS if m == 'sendMessage' and 'TICKET DELIVERY REVIEW' in i['json']['text']]


def digest_of(key, status):
    return hashlib.sha256((key + ':' + status).encode()).hexdigest()


def alert_keys():
    return [r['delivery_key'] for r in rows("select delivery_key from deliveries where delivery_key like 'ticket-health:%:delivery:%' order by attempted_at, delivery_key")]


print('=== ITEM 4 alerts ===')
new_env('i4')
snap = make_snap('2026-11-08T17:00:00Z')
with tg.ledger() as c:
    pass  # create schema
game_key = hashlib.sha256(b'fixture game card').hexdigest()
v3_prev = 'ai-ticket-review-v3:2026-11-07:' + CHAT + ':' + 'b' * 64
v3_retry = 'ai-ticket-review-v3:2026-11-08:' + CHAT + ':' + 'c' * 64
img_failed = 'ticket-image:' + 'd' * 64
result_failed = 'ticket-result:2026-11-08-abc:' + 'e' * 64
seed(game_key, 'uncertain', '2026-11-08T16:00:00Z')            # (a)
seed(v3_prev, 'uncertain', '2026-11-08T04:30:00Z')             # (b) = Nov 7 23:30 EST
seed(v3_retry, 'retry', '2026-11-08T15:00:00Z')                # (c)
seed(img_failed, 'failed', '2026-11-08T16:30:00Z')             # (d)
seed(result_failed, 'failed', '2026-11-08T16:40:00Z')          # extra: failed settlement notice
out = t.notify_alerts(snap, [])
keys = alert_keys()
print('out', out, 'alert keys', keys)
expected = {'ticket-health:2026-11-08:delivery:' + digest_of(v3_retry, 'retry'), 'ticket-health:2026-11-08:delivery:' + digest_of(img_failed, 'failed')}
check('4.only (c) and (d) alert', set(keys) == expected and len(alerts()) == 2, keys)
check('4.(b) previous Toronto day excluded', 'ticket-health:2026-11-08:delivery:' + digest_of(v3_prev, 'uncertain') not in keys)
check('4.failed ticket-result notice NOT alerted (observation)', 'ticket-health:2026-11-08:delivery:' + digest_of(result_failed, 'failed') not in keys)
CALLS.clear()
out = t.notify_alerts(snap, [])
check('4.re-run dedups (no new alerts)', len(alerts()) == 0, out)
CALLS.clear()
later = 'ai-ticket-review-v3:2026-11-08:' + CHAT + ':' + 'f' * 64
seed(later, 'failed', '2026-11-08T16:50:00Z')
out = t.notify_alerts(snap, [])
check('4.later different ticket failure -> NEW alert', len(alerts()) == 1 and 'ticket-health:2026-11-08:delivery:' + digest_of(later, 'failed') in alert_keys(), out)
CALLS.clear()
seed(v3_retry, 'uncertain', '2026-11-08T16:55:00Z')
out = t.notify_alerts(snap, [])
check('4.(c) status retry->uncertain -> NEW alert', len(alerts()) == 1 and 'ticket-health:2026-11-08:delivery:' + digest_of(v3_retry, 'uncertain') in alert_keys(), out)
print('alert texts sample', alerts())

# Same key, same status repeated failure later (e.g. retry -> sending -> retry again) on the same day
CALLS.clear()
seed(img_failed, 'retry', '2026-11-08T17:10:00Z'); t.notify_alerts(snap, []); n1 = len(alerts())
CALLS.clear()
seed(img_failed, 'sending', '2026-11-08T17:20:00Z'); seed(img_failed, 'retry', '2026-11-08T17:30:00Z'); t.notify_alerts(snap, []); n2 = len(alerts())
print('img retry first alert', n1, 'same status recurring later alert', n2, '(observation: same key+status recurrence is deduped)')

print('\n=== ITEM 4 DST boundaries ===')
for day, inside, outside in [
        ('2026-03-08', ['2026-03-08T05:00:00Z', '2026-03-09T03:59:59Z'], ['2026-03-08T04:59:59Z', '2026-03-09T04:00:00Z']),
        ('2026-11-01', ['2026-11-01T04:00:00Z', '2026-11-02T04:59:59Z'], ['2026-11-01T03:59:59Z', '2026-11-02T05:00:00Z'])]:
    new_env('i4dst-' + day)
    snap = make_snap(day + 'T17:00:00Z'); snap['date'] = day
    with tg.ledger() as c:
        pass
    mapping = {}
    for i, stamp in enumerate(inside + outside):
        k = 'ai-ticket-review-v3:' + day + ':' + CHAT + ':' + str(i) * 64
        seed(k, 'failed', stamp); mapping[k] = stamp
    t.notify_alerts(snap, [])
    got = {k for k in mapping if 'ticket-health:' + day + ':delivery:' + digest_of(k, 'failed') in alert_keys()}
    got_stamps = sorted(mapping[k] for k in got)
    check(f'4.DST {day} window correct', got_stamps == sorted(inside), got_stamps)
print('\nSUMMARY', sum(r for _, r in RESULTS), '/', len(RESULTS))
