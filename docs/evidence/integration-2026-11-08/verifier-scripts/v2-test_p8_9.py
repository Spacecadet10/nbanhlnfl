"""Items 8b, 8c, 9 (scratch). Network blocked; fake bot; settings never read from .env.telegram."""
import sys, os, json, copy
sys.path.insert(0, os.path.dirname(__file__))
import harness  # blocks sockets first
import pytest
from pathlib import Path


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('NBA_DATA_DIR', str(tmp_path))
    from app import tickets as t, telegram as tg, config, telegram_card
    for mod, name in ((t, 'SKILL'), (t, 'snapshot'), (tg, 'Telegram'), (config, 'telegram_settings'), (telegram_card, '_logo')):
        monkeypatch.setattr(mod, name, getattr(mod, name))     # restored after the test
    harness.install(tmp_path)
    state = {'snap': harness.make_snapshot(t)}
    monkeypatch.setattr(t, 'snapshot', lambda: copy.deepcopy(state['snap']))
    def plan(tickets, snap=None):
        return harness.make_plan(t, snap or state['snap'], tmp_path / f'snap-{len(list(tmp_path.glob("snap-*")))}.json', tickets)
    def sent():
        p = tmp_path / 'sent.jsonl'
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []
    return t, tg, state, plan, sent, tmp_path


T = harness.ticket


def _legacy_v2_key(t, result, chat='fixture-chat', day='2026-11-08'):
    identity = sorted((p['sport'], p['forecast_id']) for x in result['resolved'] for p in x['legs'])
    return f'ai-ticket-review-v2:{day}:{chat}:' + t.sha(json.dumps(identity).encode())


def _archive(path, result, name):
    d = path / 'reports/tickets'; d.mkdir(parents=True, exist_ok=True); (d / name).write_text(json.dumps(result))


def test_8b_legacy_reservation_with_archive_counts(env):
    t, tg, state, plan, sent, path = env
    legacy = t.check(plan([T([('nba', 1), ('nhl', 2)], 3), T([('nba', 1), ('nfl', 3)], 1)]))
    _archive(path, legacy, '2026-11-08-legacyA.json')
    with tg.ledger() as c:
        c.execute('CREATE TABLE ticket_budget_reservations (batch_key TEXT PRIMARY KEY, chat_id TEXT, day TEXT)')
        c.execute('INSERT INTO ticket_budget_reservations VALUES (?,?,?)', (_legacy_v2_key(t, legacy), 'fixture-chat', '2026-11-08'))
    with pytest.raises(ValueError, match='Daily'):
        t.send(t.check(plan([T([('nba', 4), ('nhl', 5)], 3)])))           # second $3 refused
    assert t.send(t.check(plan([T([('nhl', 4), ('nfl', 5)], 1)]))) == 'sent'   # $4 -> $5
    with pytest.raises(ValueError, match='Daily'):
        t.send(t.check(plan([T([('nba', 5), ('nfl', 4)], 1)])))           # third $1 refused
    with pytest.raises(ValueError, match='Duplicate|Daily'):
        t.send(t.check(plan([T([('nba', 1), ('nhl', 2)], 1)])))


def test_8b_legacy_v2_delivery_with_archive_counts(env):
    t, tg, state, plan, sent, path = env
    legacy = t.check(plan([T([('nba', 1), ('nhl', 2)], 1), T([('nba', 1), ('nfl', 3)], 1)]))
    _archive(path, legacy, '2026-11-08-legacyB.json')
    with tg.ledger() as c:
        c.execute('insert into deliveries(delivery_key,chat_id,text,status,attempted_at) values(?,?,?,?,?)',
                  (_legacy_v2_key(t, legacy), 'fixture-chat', legacy['text'], 'sent', harness.NOW))
    with pytest.raises(ValueError, match='Daily'):
        t.send(t.check(plan([T([('nhl', 4), ('nfl', 5)], 1)])))           # two $1 already
    assert t.send(t.check(plan([T([('nhl', 3), ('nfl', 4)], 3)]))) == 'sent'


def test_8b_legacy_reservation_without_archive_blocks(env):
    t, tg, state, plan, sent, path = env
    with tg.ledger() as c:
        c.execute('CREATE TABLE ticket_budget_reservations (batch_key TEXT PRIMARY KEY, chat_id TEXT, day TEXT)')
        c.execute('INSERT INTO ticket_budget_reservations VALUES (?,?,?)', ('ai-ticket-review-v2:2026-11-08:fixture-chat:deadbeef', 'fixture-chat', '2026-11-08'))
    with pytest.raises(ValueError, match='Unresolved legacy'):
        t.send(t.check(plan([T([('nhl', 4), ('nfl', 5)], 1)])))
    assert sent() == []


def test_8c_crash_after_reservation_before_claim(env, monkeypatch):
    t, tg, state, plan, sent, path = env
    real = tg.deliver
    calls = []
    def crash(*a, **k):
        calls.append(a[3])
        if len(calls) == 1:
            raise RuntimeError('injected crash after batch commit, before claim')
        return real(*a, **k)
    monkeypatch.setattr(tg, 'deliver', crash)
    tickets = [T([('nba', 1), ('nhl', 2)], 3), T([('nba', 1), ('nfl', 3)], 1)]
    with pytest.raises(RuntimeError):
        t.send(t.check(plan(tickets)))
    with tg.ledger() as c:
        assert c.execute('select count(*) from ticket_batches').fetchone()[0] == 1
        assert c.execute('select count(*) from ticket_items').fetchone()[0] == 2
        assert c.execute("select count(*) from deliveries where delivery_key like 'ai-ticket-review-v3:%'").fetchone()[0] == 0
    # A different plan meanwhile is refused (the reservation is retained)
    with pytest.raises(ValueError, match='Daily'):
        t.send(t.check(plan([T([('nhl', 4), ('nfl', 5)], 3)])))
    # Fresh snapshot (later as_of) + fresh plan with the same tickets: delivered once
    state['snap'] = harness.make_snapshot(t, as_of='2026-11-08T17:04:00Z')
    assert t.send(t.check(plan(tickets, state['snap']))) == 'sent'
    assert t.send(t.check(plan(tickets, state['snap']))) == 'existing_sent'
    with tg.ledger() as c:
        assert c.execute('select count(*) from ticket_batches').fetchone()[0] == 1
        assert c.execute('select count(*) from ticket_items').fetchone()[0] == 2
    assert [s['method'] for s in sent()] == ['sendMessage', 'sendPhoto']
    print('8c deliveries', [s['method'] for s in sent()])


def test_8c_crash_alert_visibility(env, monkeypatch):
    """Does notify_alerts surface a reservation that crashed before its delivery claim?"""
    t, tg, state, plan, sent, path = env
    monkeypatch.setattr(tg, 'deliver', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('crash')))
    with pytest.raises(RuntimeError):
        t.send(t.check(plan([T([('nba', 1), ('nhl', 2)], 3)])))
    monkeypatch.undo  # keep crash deliver: count attempted alerts via a recording deliver instead
    alerts = []
    monkeypatch.setattr(tg, 'deliver', lambda bot, conn, chat, key, text, now, **k: alerts.append(key) or 'sent')
    snap = dict(state['snap']); snap['alerts'] = []
    t.notify_alerts(snap, [])
    print('alerts for crashed reservation:', alerts)
    assert alerts, 'reservation retained with no delivery row is never alerted'


def test_9_complete_slate_after_budget_exhausted(env, monkeypatch, capsys):
    t, tg, state, plan, sent, path = env
    assert t.send(t.check(plan([T([('nba', 1), ('nhl', 2)], 3), T([('nba', 1), ('nfl', 3)], 1), T([('nba', 4), ('nhl', 5)], 1)]))) == 'sent'
    empty = plan([])
    del empty['review']['forecasts_reviewed']['nfl:5']
    with pytest.raises(ValueError, match='Every available'):
        t.check(empty)
    empty = plan([]); empty['review']['combination_comparison'] = ' '
    with pytest.raises(ValueError, match='Compare combinations'):
        t.check(empty)
    empty = plan([])
    result = t.check(empty)
    assert result['coverage'] == {'required': 15, 'accounted': 15}, result['coverage']
    reviews = [json.loads(p.read_text()) for p in (path / 'reports/tickets/reviews').glob('*.json')]
    assert any(r['resolved'] == [] and r['coverage'] == {'required': 15, 'accounted': 15} for r in reviews)
    assert t.send(result) == 'no_ticket'
    p = plan([T([('nhl', 3), ('nfl', 4)], 1)])
    assert t.check(p)['resolved']
    f = path / 'plan9.json'; f.write_text(json.dumps(p))
    monkeypatch.setattr(sys, 'argv', ['tickets', 'send', '--plan', str(f)])
    capsys.readouterr()
    code = t.main()
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    print('9 main exit', code, out)
    assert code == 1 and out['status'] == 'failed' and 'Daily' in out['reason']
    f2 = path / 'plan9b.json'; f2.write_text(json.dumps(plan([])))
    monkeypatch.setattr(sys, 'argv', ['tickets', 'send', '--plan', str(f2)])
    assert t.main() == 0
    assert [s['method'] for s in sent()] == ['sendMessage', 'sendPhoto']
