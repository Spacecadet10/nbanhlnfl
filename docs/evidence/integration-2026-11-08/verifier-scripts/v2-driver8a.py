import sys, os, json, time, subprocess, sqlite3, random, shutil
sys.path.insert(0, os.path.dirname(__file__))
S = os.path.dirname(os.path.abspath(__file__))
PY = '/Users/andry/Desktop/nba-predictor-sprint1/backend/.venv/bin/python'
import harness
def run_round(n, plans_spec, label):
    d = os.path.join(S, 'd8a', f'{label}-{n}'); shutil.rmtree(d, ignore_errors=True); os.makedirs(d)
    env = dict(os.environ, NBA_DATA_DIR=d, TELEGRAM_BOT_TOKEN='fixture-token', TELEGRAM_CHAT_ID='fixture-chat',
               PYTHONDONTWRITEBYTECODE='1', OMP_NUM_THREADS='4', PYTHONPATH='.')
    os.environ['NBA_DATA_DIR'] = d
    t, tg, config = harness.install(d)
    snap = harness.make_snapshot(t)
    random.shuffle(plans_spec)
    paths = []
    for i, spec in enumerate(plans_spec):
        plan = harness.make_plan(t, snap, os.path.join(d, f'snap{i}.json'), [harness.ticket(l, s) for l, s in spec])
        p = os.path.join(d, f'plan{i}.json'); open(p, 'w').write(json.dumps(plan)); paths.append(p)
    start = time.time() + 4.0
    procs = [subprocess.Popen([PY, os.path.join(S, 'worker8.py'), d, p, str(start), p + '.out'], env=env,
                              cwd='/Users/andry/Desktop/nba-predictor-sprint1/backend') for p in paths]
    for pr in procs: pr.wait()
    outs = [json.loads(open(p + '.out').read()) for p in paths]
    c = sqlite3.connect(os.path.join(d, '.telegram-delivery.sqlite')); c.row_factory = sqlite3.Row
    batches = [json.loads(r['payload']) for r in c.execute('SELECT payload FROM ticket_batches')]
    stakes = [tk['stake_usd'] for b in batches for tk in b['resolved']]
    sigs = [tk['signature'] for b in batches for tk in b['resolved']]
    deliveries = dict(c.execute("SELECT status, COUNT(*) FROM deliveries WHERE delivery_key LIKE 'ai-ticket-review-v3:%' GROUP BY status").fetchall())
    sent = [json.loads(l) for l in open(os.path.join(d, 'sent.jsonl'))] if os.path.exists(os.path.join(d, 'sent.jsonl')) else []
    ok = sum(stakes) <= 5 and stakes.count(3) <= 1 and stakes.count(1) <= 2 and len(sigs) == len(set(sigs)) \
         and sum(1 for s in sent if s['method'] == 'sendMessage') == len(batches)
    print(json.dumps({'round': f'{label}-{n}', 'ok': ok, 'stakes': stakes, 'batches': len(batches), 'deliveries': deliveries,
                      'sendMessage': sum(1 for s in sent if s['method'] == 'sendMessage'),
                      'exits': [(o['exit'], json.loads(o['stdout'].splitlines()[-1]).get('status'),
                                 json.loads(o['stdout'].splitlines()[-1]).get('reason', '')[:60]) for o in outs]}))
    return ok
mixed = [[([('nba',1),('nhl',2)],3)], [([('nba',3),('nhl',4)],3)], [([('nfl',1),('nfl',2)],1), ([('nfl',3),('nfl',4)],1)], [([('nhl',1),('nfl',5)],1)]]
same = [[([('nba',1),('nhl',2)],1)]] * 4
results = [run_round(n, [list(x) for x in mixed], 'mixed') for n in range(int(sys.argv[1]))]
results += [run_round(n, [list(x) for x in same], 'same') for n in range(int(sys.argv[2]))]
print('ALL_OK', all(results), 'rounds', len(results))
