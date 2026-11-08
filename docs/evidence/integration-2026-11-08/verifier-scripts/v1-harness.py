"""Isolated harness: no network, temp NBA_DATA_DIR, MockTransport into real Telegram.call."""
import os, sys, socket, json, copy, tempfile, sqlite3, email
from pathlib import Path

SCRATCH = Path(__file__).resolve().parent
os.environ['TELEGRAM_BOT_TOKEN'] = 'fixture-token'
os.environ['TELEGRAM_CHAT_ID'] = 'fixture-chat'
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.dont_write_bytecode = True
_base = tempfile.mkdtemp(prefix='p1v1-', dir=str(SCRATCH))
os.environ['NBA_DATA_DIR'] = _base


def _nonet(*a, **k):
    raise RuntimeError('network disabled by harness')
socket.socket.connect = _nonet
socket.socket.connect_ex = _nonet
socket.create_connection = _nonet

import httpx
from app import tickets as t, telegram as tg, config, telegram_card

CHAT = 'fixture-chat'
CALLS = []          # (method, info)
SCRIPT = {}         # method -> list of actions
MSG = [0]


def _ok(result):
    return httpx.Response(200, json={'ok': True, 'result': result})


def handler(request):
    method = request.url.path.rsplit('/', 1)[1]
    ctype = request.headers.get('content-type', '')
    info = {'ctype': ctype}
    if ctype.startswith('application/json'):
        info['json'] = json.loads(request.content)
    elif ctype.startswith('multipart/form-data'):
        raw = b'Content-Type: ' + ctype.encode() + b'\r\n\r\n' + request.read()
        msg = email.message_from_bytes(raw)
        for part in msg.get_payload():
            name = part.get_param('name', header='content-disposition')
            info[name] = part.get_payload(decode=True)
        info['caption'] = info['caption'].decode()
    CALLS.append((method, info))
    queue = SCRIPT.get(method) or []
    action = queue.pop(0) if queue else 'ok'
    if action == 'connect':
        raise httpx.ConnectError('fixture connect', request=request)
    if action == 'readtimeout':
        raise httpx.ReadTimeout('fixture timeout', request=request)
    if action == 'badjson':
        return httpx.Response(200, content=b'<html>not json')
    if isinstance(action, tuple) and action[0] == 'status':
        return httpx.Response(action[1], json=action[2])
    if isinstance(action, tuple) and action[0] == 'raw':
        return httpx.Response(200, json=action[1])
    if isinstance(action, tuple) and action[0] == 'result':
        return _ok(action[1])
    if method == 'getMe':
        return _ok({'is_bot': True, 'username': 'prop51picksbot', 'id': 1})
    if method == 'getWebhookInfo':
        return _ok({'url': ''})
    if method == 'getUpdates':
        return _ok([])
    MSG[0] += 1
    return _ok({'message_id': MSG[0]})


MOCK = httpx.MockTransport(handler)
_orig_init = tg.Telegram.__init__
def _init(self, token, *, transport=None):
    _orig_init(self, token, transport=MOCK)
tg.Telegram.__init__ = _init
config.telegram_settings = lambda: {'TELEGRAM_BOT_TOKEN': 'fixture-token', 'TELEGRAM_CHAT_ID': CHAT}
telegram_card._logo = lambda *a: None

PICKS = [dict(forecast_id=i, game_id=f'g{i}', market='winner', matchup=f'AA{i} @ BB{i}'.replace('1','Q').replace('2','W').replace('3','E').replace('4','R').replace('5','T'),
              pick_label=f'BB{i} win', probability=.70 + i/100, status='scheduled', grade='pending',
              generated_at='2026-11-08T16:30:00Z', tipoff='2026-11-08T18:00:00Z') for i in range(1, 6)]
STATE = {}


def new_env(name):
    d = Path(tempfile.mkdtemp(prefix=name + '-', dir=str(SCRATCH)))
    os.environ['NBA_DATA_DIR'] = str(d)
    lp = Path(config.telegram_ledger_path()).resolve()
    assert str(lp).startswith(str(d.resolve())), lp
    assert 'nba-predictor-sprint1/.telegram' not in str(lp)
    t.SKILL = d / 'SKILL.md'; t.SKILL.write_text('fixture skill')
    CALLS.clear(); SCRIPT.clear()
    STATE['dir'] = d
    return d


def make_snap(as_of):
    snap = dict(as_of=as_of, date='2026-11-08', skill_sha256=t.sha(t.SKILL.read_bytes()),
                helper_sha256=t.sha(Path(t.__file__).read_bytes()),
                sports={s: dict(freshness=dict(source_state='fresh', failing=False),
                                report=dict(picks=copy.deepcopy(PICKS), without_forecast={})) for s in ('nba', 'nhl', 'nfl')})
    snap['sports']['soccer'] = {'error': 'forecasts_unavailable'}
    snap['alerts'] = []
    return snap


def ticket(legs, stake=1, label=None):
    return dict(label=label or ('Main' if stake == 3 else 'Variation'), stake_usd=stake,
                legs=[dict(sport=s, forecast_id=i, rationale='Fixture selection') for s, i in legs])


def at(as_of, tickets):
    """Simulate time: new snapshot file + plan bound to it; snapshot() returns same as_of."""
    d = STATE['dir']
    snap = make_snap(as_of)
    STATE['snap'] = snap
    t.snapshot = lambda: copy.deepcopy(STATE['snap'])
    path = d / ('snapshot-' + as_of.replace(':', '') + '.json')
    path.write_text(json.dumps(snap))
    review = dict(skill_sha256=snap['skill_sha256'], helper_sha256=snap['helper_sha256'], snapshot_sha256=t.sha(path.read_bytes()),
                  notes='Fixture review', combination_comparison='Compared combos',
                  markets_reviewed={s: {'winner': 'Reviewed'} for s in ('nba', 'nhl', 'nfl')},
                  forecasts_reviewed={f'{s}:{i}': dict(decision='candidate', reason='fixture') for s in ('nba', 'nhl', 'nfl') for i in range(1, 6)})
    plan = dict(snapshot_path=str(path), review=review, tickets=tickets)
    pf = d / ('plan-' + as_of.replace(':', '') + '.json')
    pf.write_text(json.dumps(plan))
    return plan, pf


def run_main(argv):
    import io, contextlib
    sys.argv = ['tickets'] + argv
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = t.main()
    return code, buf.getvalue().strip()


def db():
    c = sqlite3.connect(str(config.telegram_ledger_path()))
    c.row_factory = sqlite3.Row
    return c


def rows(sql, *a):
    c = db()
    try:
        return [dict(r) for r in c.execute(sql, a)]
    except sqlite3.OperationalError as e:
        return [{'error': str(e)}]
    finally:
        c.close()


def methods():
    return [m for m, _ in CALLS]


def sends():
    return [(m, i) for m, i in CALLS if m in ('sendMessage', 'sendPhoto')]


RESULTS = []
def check(name, cond, evidence=''):
    RESULTS.append((name, bool(cond)))
    print(('PASS ' if cond else 'FAIL ') + name + (' | ' + str(evidence) if evidence != '' else ''))
