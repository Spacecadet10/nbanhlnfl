import sys, json, shutil, io
sys.path.insert(0, __import__('os').path.dirname(__file__))
from harness import *
from datetime import datetime
from zoneinfo import ZoneInfo

SETS = {
    1: [ticket([('nba', 1), ('nhl', 2)], 3)],
    2: [ticket([('nba', 1), ('nhl', 2)], 3), ticket([('nba', 1), ('nfl', 3)], 1)],
    3: [ticket([('nba', 1), ('nhl', 2), ('nfl', 3)], 3), ticket([('nba', 1), ('nfl', 4)], 1), ticket([('nhl', 5), ('nfl', 5)], 1)],
}
for n, tks in SETS.items():
    print(f'\n=== ITEM 7 visual {n} ticket(s) ===')
    d = new_env(f'i7-{n}')
    plan, pf = at('2026-11-08T17:00:00Z', tks)
    code, out = run_main(['send', '--plan', str(pf)])
    print('exit', code, out)
    msg = [i for m, i in CALLS if m == 'sendMessage'][0]
    ph = [i for m, i in CALLS if m == 'sendPhoto'][0]
    img = rows('select * from ticket_images')[0]
    file_bytes = Path(img['path']).read_bytes()
    check(f'7.{n} PNG sent == archived file bytes == ticket_images.sha256', ph['photo'] == file_bytes and t.sha(ph['photo']) == img['sha256'] and ph['photo'][:4] == b'\x89PNG', (len(ph['photo']), img['sha256'][:12]))
    check(f'7.{n} caption == archived caption, <=1024', ph['caption'] == img['caption'] and len(ph['caption']) <= 1024, len(ph['caption']))
    check(f'7.{n} photo chat_id fixture', ph['chat_id'] == CHAT.encode())
    keys = [r['delivery_key'] for r in rows('select delivery_key from deliveries')]
    check(f'7.{n} text and image use different keys', len(set(keys)) == 2 and any(k.startswith('ticket-image:') for k in keys) and any(k.startswith('ai-ticket-review-v3:') for k in keys), keys)
    text = msg['json']['text']
    batch = json.loads(rows('select payload from ticket_batches')[0]['payload'])
    okmsg = text == batch['text'] and msg['json']['parse_mode'] == 'HTML'
    for num, tk in enumerate(batch['resolved'], 1):
        okmsg &= f'Ticket {num}:' in text and tk['ticket_id'] in text and f"${tk['stake_usd']:.2f}" in text
        for leg in tk['legs']:
            start = datetime.fromisoformat(leg['tipoff'].replace('Z', '+00:00')).astimezone(ZoneInfo('America/Toronto'))
            okmsg &= leg['matchup'] in text and leg['pick_label'] in text and f'{start:%b %d %I:%M %p %Z}' in text and leg['sport'].upper() in text
    check(f'7.{n} text has numbered tickets, every leg/matchup/side/Toronto time/ID/$stake', okmsg)
    check(f'7.{n} caption lists every ticket id', all(tk['ticket_id'] in ph['caption'] for tk in batch['resolved']))
    shutil.copy(img['path'], SCRATCH / f'card-{n}.png')
    (SCRATCH / f'text-{n}.txt').write_text(text + '\n----CAPTION----\n' + ph['caption'])
    # determinism of re-render (relevant to hash-mismatch defect)
    from app.telegram_card import render_ticket_card
    check(f'7.{n} re-render byte-identical (logos off)', render_ticket_card(batch['date'], batch['resolved']) == file_bytes)

print('\n=== PROBE: render_ticket_card raises after text sent ===')
new_env('probe-render')
import app.telegram_card as tc
real_render = tc.render_ticket_card
def boom(*a, **k): raise ValueError('Ticket card exceeds layout bounds.')
tc.render_ticket_card = boom
plan, pf = at('2026-11-08T17:00:00Z', SETS[2])
code, out = run_main(['send', '--plan', str(pf)])
print('exit', code, out, methods())
dl = rows('select delivery_key,status from deliveries'); ti = rows('select * from ticket_images')
print('deliveries', dl, 'ticket_images', ti)
CALLS.clear()
al = t.notify_alerts(make_snap('2026-11-08T17:02:00Z'), [])
print('notify_alerts out', al)
check('PROBE render-raise leaves alertable receipt (image delivery row or alert)', any(k['delivery_key'].startswith('ticket-image:') for k in dl) or al, (dl, al))
CALLS.clear()
plan, pf = at('2026-11-08T17:03:00Z', SETS[2])
code, out = run_main(['send', '--plan', str(pf)])
print('deterministic raise retry', code, out, methods())
tc.render_ticket_card = real_render
CALLS.clear()
plan, pf = at('2026-11-08T17:05:00Z', SETS[2])
code, out = run_main(['send', '--plan', str(pf)])
print('after transient error cleared', code, out, methods())
check('PROBE render transient -> later send recovers with photo only', json.loads(out)['status'] == 'sent' and [m for m, _ in sends()] == ['sendPhoto'], methods())

print('\n=== PROBE: PNG written, ticket_images insert lost (crash), later re-render differs ===')
d = new_env('probe-hash')
class Proxy:
    def __init__(self, c): self._c = c
    def execute(self, sql, *a):
        if sql.startswith('INSERT OR IGNORE INTO ticket_images'):
            raise SystemExit('simulated crash after PNG write, before ticket_images insert')
        return self._c.execute(sql, *a)
    def __getattr__(self, k): return getattr(self._c, k)
from contextlib import contextmanager
real_ledger = tg.ledger
@contextmanager
def crashy():
    with real_ledger() as c:
        yield Proxy(c)
tg.ledger = crashy
plan, pf = at('2026-11-08T17:00:00Z', SETS[2])
try:
    run_main(['send', '--plan', str(pf)])
except SystemExit as e:
    print('crash:', e)
tg.ledger = real_ledger
print('pngs', [p.name for p in (d / 'reports/tickets').glob('*.png')], 'ticket_images', rows('select * from ticket_images'), 'deliveries', rows('select delivery_key,status from deliveries'))
# Next run: team logo now available (cache/network), so render bytes differ from the orphan PNG.
from PIL import Image
tc._logo = lambda league, abbr: Image.new('RGBA', (60, 60), (200, 30, 30, 255))
CALLS.clear()
plan, pf = at('2026-11-08T17:03:00Z', SETS[2])
code, out = run_main(['send', '--plan', str(pf)])
print('second', code, out, methods(), rows('select sha256 from ticket_images'))
tc._logo = lambda *a: None
CALLS.clear()
plan, pf = at('2026-11-08T17:05:00Z', SETS[2])
code, out = run_main(['send', '--plan', str(pf)])
print('third (logos off again)', code, out, methods())
check('PROBE hash: image eventually deliverable after crash', 'sendPhoto' in methods(), (code, out))
CALLS.clear()
print('alerts after hash mismatch', t.notify_alerts(make_snap('2026-11-08T17:06:00Z'), []))

print('\n=== PROBE: retry after tipoff (snapshot/pregame window gone) ===')
new_env('probe-late')
SCRIPT['sendMessage'] = ['connect']
plan, pf = at('2026-11-08T17:58:00Z', SETS[1])
code, out = run_main(['send', '--plan', str(pf)]); print('first', code, out)
CALLS.clear()
plan, pf = at('2026-11-08T18:01:00Z', SETS[1])
code, out = run_main(['send', '--plan', str(pf)]); print('after tipoff', code, out, methods())
print('ledger', rows('select delivery_key,status,next_attempt_at from deliveries'), 'items', len(rows('select * from ticket_items')))
print('\nSUMMARY', sum(r for _, r in RESULTS), '/', len(RESULTS))
