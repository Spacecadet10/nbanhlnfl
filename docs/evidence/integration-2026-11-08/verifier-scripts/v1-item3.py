import sys, json, traceback
sys.path.insert(0, __import__('os').path.dirname(__file__))
from harness import *

TICKETS = [ticket([('nba', 1), ('nhl', 2)], 3), ticket([('nba', 1), ('nfl', 3)], 1)]
GRADES = {('nba', 1): 'win', ('nhl', 2): 'win', ('nfl', 3): 'loss'}
t.grade_leg = lambda leg, now: dict(sport=leg['sport'], forecast_id=leg['forecast_id'], pick_label=leg['pick_label'],
                                    grade=GRADES[(leg['sport'], leg['forecast_id'])])
NOW = '2026-11-08T22:00:00Z'


def setup(name):
    new_env(name)
    plan, pf = at('2026-11-08T17:00:00Z', TICKETS)
    code, out = run_main(['send', '--plan', str(pf)])
    assert json.loads(out)['status'] == 'sent', out
    CALLS.clear(); SCRIPT.clear()


def grades():
    return rows('select ticket_id, json_extract(payload,"$.grade") g from ticket_grades order by ticket_id')


SCEN = {
    'a_getMe_connect': {'getMe': ['connect']},
    'b1_getUpdates_connect': {'getUpdates': ['connect']},
    'b2_getUpdates_badjson': {'getUpdates': ['badjson']},
    'b3_getUpdates_missing_update_id': {'getUpdates': [('result', [{'message': {'chat': {'type': 'private', 'id': CHAT}, 'text': '/stop'}}])]},
    'b4_getUpdates_result_dict': {'getUpdates': [('result', {'unexpected': 1})]},
    'b5_getUpdates_body_list': {'getUpdates': [('raw', [1, 2])]},
    'b6_getUpdates_private_no_chat_id': {'getUpdates': [('result', [{'update_id': 5, 'message': {'chat': {'type': 'private'}}}])]},
    'b7_getMe_result_list': {'getMe': [('result', [])]},
    'c1_sendMessage_400': {'sendMessage': [('status', 400, {'ok': False, 'description': 'Bad Request'})] * 2},
    'c2_sendMessage_connect': {'sendMessage': ['connect'] * 2},
}
for name, script in SCEN.items():
    print(f'\n=== ITEM 3 settle {name} ===')
    setup('i3-' + name)
    SCRIPT.update({k: list(v) for k, v in script.items()})
    try:
        rep = t.settle(now=NOW, notify=True)
        raised = None
        print('report.notification_errors', rep['notification_errors'], 'per-ticket notification', [x.get('notification') for x in rep['tickets']])
    except Exception as e:
        raised = type(e).__name__ + ': ' + str(e)[:80]
        print('RAISED', raised)
    g = grades()
    print('methods', methods(), 'grades(new conn)', g)
    print('result deliveries', rows("select delivery_key,status,next_attempt_at from deliveries where delivery_key like 'ticket-result:%'"))
    check(f'3.{name} grades committed (2 tickets)', len(g) == 2 and {x['g'] for x in g} == {'win', 'loss'}, g)
    check(f'3.{name} settle did not raise', raised is None, raised)

print('\n=== ITEM 3 preflight --notify, network down (getMe connect for every call) ===')
setup('i3-preflight')
SCRIPT['getMe'] = ['connect'] * 10
code, out = run_main(['preflight', '--notify'])
print('exit', code, 'stdout', out[:400]); g = grades(); print('grades', g, 'methods', methods())
check('3.preflight exit nonzero', code != 0, code)
check('3.preflight grades persisted', len(g) == 2, g)
check('3.preflight settlement report printed', 'tickets' in out, out[:200])

print('\n=== ITEM 3 preflight --notify, malformed getUpdates (KeyError) ===')
setup('i3-preflight-keyerror')
SCRIPT['getUpdates'] = [('result', [{'message': {'chat': {'type': 'private', 'id': CHAT}, 'text': '/stop'}}])]
code, out = run_main(['preflight', '--notify'])
print('exit', code, 'stdout', out[:400]); g = grades(); print('grades', g)
check('3.preflight-keyerror grades persisted', len(g) == 2, g)
check('3.preflight-keyerror report printed', 'tickets' in out, out[:200])

print('\n=== ITEM 3 retry of failed notification on next settle (c2 connect then ok) ===')
setup('i3-renotify')
SCRIPT['sendMessage'] = ['connect', 'connect']
r1 = t.settle(now=NOW, notify=True)
CALLS.clear()
r2 = t.settle(now='2026-11-08T22:05:00Z', notify=True)
print('second settle notifications', [x.get('notification') for x in r2['tickets']], [m for m, _ in sends()])
check('3.renotify later settle re-sends retry notices', [m for m, _ in sends()] == ['sendMessage', 'sendMessage'], methods())
print('\nSUMMARY', sum(r for _, r in RESULTS), '/', len(RESULTS))
