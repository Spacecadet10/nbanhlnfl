"""NCAAB Telegram gate (owner brief Nov 21 2026, policy ncaab-colour-gate-v1): a card goes out only for a pick that is green
under the SAME decision the card displays (card_threshold.decide), on a supported, validated market, with a complete, matching,
fresh sportsbook quote and a positive expected value at the offered price. Zero qualifiers = zero cards. Synthetic store; the
HTTP client is forbidden and the bot is a stub, so nothing here can reach Telegram."""
from datetime import datetime, timezone

import httpx
import pytest

from app import card_threshold as ct, jobs, ncaab_gate, telegram as tg
from ncaab_support import GAME, NOW, add_quote, install_college, make_store, refreshed, seed

NOW_DT = datetime(2026, 11, 17, 22, 30, tzinfo=timezone.utc)
DAY = '2026-11-17'


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    from app import telegram_card

    def forbidden(*a, **k):
        raise AssertionError('a gate test tried to open an HTTP client')
    monkeypatch.setattr(httpx, 'Client', forbidden)
    monkeypatch.setattr(telegram_card, '_logo', lambda *_: None)
    monkeypatch.setenv('NBA_CARD_COLOURS', '1')
    monkeypatch.delenv('NBA_NCAAB_CARDS', raising=False)


@pytest.fixture
def store(tmp_path, monkeypatch):
    conn = make_store(tmp_path, monkeypatch)
    seed(conn)
    install_college(monkeypatch)
    add_quote(conn, GAME, 'winner', None, -150, 130, '2026-11-17T22:10:00Z')
    add_quote(conn, GAME, 'total', 141.5, -110, -110, '2026-11-17T22:05:00Z')
    assert jobs.predict_now(conn, now=NOW)['status'] == 'success'
    conn.commit()
    refreshed(conn, '2026-11-17T22:29:00Z')
    yield conn
    conn.close()


def lines(tmp_path, state='set', **scopes):
    conn = ct.connect(tmp_path / 'card_thresholds.sqlite')
    with conn:
        for scope, t in scopes.items():
            conn.execute("INSERT INTO card_thresholds (day, scope, class, sport, threshold, n, window, inputs_hash, rule_version, "
                         "state, reason, generated_at) VALUES (?, ?, ?, NULL, ?, 250, 400, 'h', 'card-threshold-v1', ?, ?, ?)",
                         (DAY, scope, scope, t, state, None if t else 'no candidate beat the price', DAY + 'T14:00:00Z'))
    conn.close()


class Bot:
    def __init__(self):
        self.sends = []

    def call(self, method, body, **kwargs):
        if method == 'getUpdates':
            return []
        assert method in ('sendMessage', 'sendPhoto')
        self.sends.append((method, body))
        return {'message_id': len(self.sends)}


def run(tmp_path, bot=None):
    bot = bot or Bot()
    with tg.ledger(tmp_path / 'receipts.sqlite') as conn:
        out = tg.run_once(bot, {'TELEGRAM_CHAT_ID': '123'}, conn, now=NOW_DT)
        rows = [dict(r) for r in conn.execute('SELECT * FROM ncaab_gate_decisions ORDER BY decision_id')]
    return out, bot, rows


def test_no_qualifying_threshold_means_zero_cards_and_every_decision_is_recorded(store, tmp_path):
    lines(tmp_path, W=None, L=None)                          # today's honest state: nothing qualifies
    out, bot, rows = run(tmp_path)
    assert out['sports']['ncaab']['status'] == 'gated' and out['sports']['ncaab']['qualifying'] == 0, out
    assert bot.sends == []
    assert rows and all(r['policy'] == ncaab_gate.POLICY for r in rows)
    verdicts = [v for r in rows for v in ncaab_gate.json.loads(r['verdicts'])]
    assert {v['market'] for v in verdicts} >= {'winner', 'spread', 'total'}
    assert not any(v['qualifies'] for v in verdicts)
    assert any('not green' in ' '.join(v['reasons']) for v in verdicts if v['market'] == 'winner')


def test_green_positive_ev_winner_is_sent_once_and_other_markets_are_context(store, tmp_path):
    lines(tmp_path, W='50.0', L='50.0')
    add_quote(store, GAME, 'winner', None, 300, 300, '2026-11-17T22:25:00Z')     # generous executable price on either side
    store.commit()
    out, bot, rows = run(tmp_path)
    assert out['sports']['ncaab'].get('sent') == 1 and out['sports']['ncaab']['qualifying'] == 1, out
    text = next(b['caption'] if 'caption' in b else b['text'] for _, b in bot.sends if 'text' in b or 'caption' in b)
    with tg.ledger(tmp_path / 'receipts.sqlite') as conn:
        card = conn.execute("SELECT text FROM deliveries WHERE status='sent'").fetchone()['text']
    assert 'win</b>' in card                                  # the recommended pick is the qualifying winner pick
    assert 'Qualifies (policy ncaab-colour-gate-v1)' in card and 'expected value +' in card
    assert 'Context only · not a recommendation' in card       # spread/total stay on the card, clearly marked
    again, bot2, _ = run(tmp_path)
    assert again['sports']['ncaab'].get('sent', 0) == 0 and bot2.sends == []        # deduplicated: never sent twice


def test_green_but_negative_expected_value_is_not_sent(store, tmp_path):
    lines(tmp_path, W='50.0')
    add_quote(store, GAME, 'winner', None, -10000, -10000, '2026-11-17T22:25:00Z')   # 1.01 decimal: green is not a price
    store.commit()
    out, bot, rows = run(tmp_path)
    assert bot.sends == [] and out['sports']['ncaab']['qualifying'] == 0
    winner = [v for r in rows for v in ncaab_gate.json.loads(r['verdicts']) if v['market'] == 'winner'][-1]
    assert winner['colour'] == 'green' and winner['ev'] < 0 and 'expected value not positive' in winner['reasons']


def test_stale_price_is_not_sent(store, tmp_path):
    lines(tmp_path, W='50.0')                                 # newest winner quote 22:10, the run at 23:30 is 80 min later
    later = datetime(2026, 11, 17, 23, 20, tzinfo=timezone.utc)
    with tg.ledger(tmp_path / 'receipts.sqlite') as conn:
        refreshed(store, '2026-11-17T23:19:00Z')
        out = tg.run_once(Bot(), {'TELEGRAM_CHAT_ID': '123'}, conn, now=later)
        rows = [dict(r) for r in conn.execute('SELECT * FROM ncaab_gate_decisions')]
    winner = [v for r in rows for v in ncaab_gate.json.loads(r['verdicts']) if v['market'] == 'winner'][-1]
    assert not winner['qualifies'] and any('older than' in x for x in winner['reasons'])
    assert out['sports']['ncaab'].get('sent', 0) == 0


def test_spread_and_total_are_blocked_by_failed_validation_even_when_green(store, tmp_path):
    lines(tmp_path, L='50.0')
    out, bot, rows = run(tmp_path)
    for v in (v for r in rows for v in ncaab_gate.json.loads(r['verdicts']) if v['market'] in ('spread', 'total')):
        assert not v['qualifies'] and any('failed validation' in x for x in v['reasons'])
    assert bot.sends == []


def test_stale_threshold_never_qualifies(store, tmp_path):
    conn = ct.connect(tmp_path / 'card_thresholds.sqlite')
    with conn:
        conn.execute("INSERT INTO card_thresholds (day, scope, class, sport, threshold, n, window, inputs_hash, rule_version, state, "
                     "generated_at) VALUES ('2026-11-16', 'W', 'W', NULL, '50.0', 250, 400, 'h', 'card-threshold-v1', 'set', "
                     "'2026-11-16T14:00:00Z')")
    conn.close()
    add_quote(store, GAME, 'winner', None, 300, 300, '2026-11-17T22:25:00Z')
    store.commit()
    out, bot, _ = run(tmp_path)
    assert bot.sends == [] and out['sports']['ncaab']['qualifying'] == 0


def test_colour_flag_off_fails_closed(store, tmp_path, monkeypatch):
    lines(tmp_path, W='50.0')
    add_quote(store, GAME, 'winner', None, 300, 300, '2026-11-17T22:25:00Z')
    store.commit()
    monkeypatch.setenv('NBA_CARD_COLOURS', '0')
    out, bot, _ = run(tmp_path)
    assert bot.sends == [] and out['sports']['ncaab']['qualifying'] == 0


def test_kill_switch_still_turns_ncaab_off(store, tmp_path, monkeypatch):
    monkeypatch.setenv('NBA_NCAAB_CARDS', '0')
    out, bot, _ = run(tmp_path)
    assert out['sports']['ncaab']['status'] == 'off' and bot.sends == []


def test_a_game_with_a_sent_card_stays_in_the_report_for_withdrawal(tmp_path):
    picks = [{'game_id': 'ncaab:9', 'market': 'winner', 'ncaab_gate': None}, {'game_id': 'ncaab:8', 'market': 'winner'}]
    verdicts = {id(picks[0]): {'qualifies': False}, id(picks[1]): {'qualifies': False}}
    kept = ncaab_gate.keep(picks, lambda p: verdicts[id(p)], sent_games={'ncaab:9'})
    assert [p['game_id'] for p in kept] == ['ncaab:9']        # it can become 'No supported pick' -> one 'Pick withdrawn' update


def test_expected_value_counts_pushes_as_refunds():
    # side a wins 50%, pushes 10%, loses 40% at even money (+100): EV = 0.5 * 1 - 0.4 = +0.10 per unit
    assert ncaab_gate.expected_value(0.5, 0.1, 0.4, 100) == pytest.approx(0.10)
    assert ncaab_gate.expected_value(0.5, 0.0, 0.5, -110) < 0


def test_decision_table_is_append_only(tmp_path):
    with tg.ledger(tmp_path / 'receipts.sqlite') as conn:
        conn.executescript(ncaab_gate.SCHEMA)
        conn.execute("INSERT INTO ncaab_gate_decisions (policy, game_id, decided_at, versions, verdicts, chosen, digest) "
                     "VALUES ('p', 'g', 't', '[]', '[]', NULL, 'd')")
        import sqlite3
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("UPDATE ncaab_gate_decisions SET chosen = 'x'")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute('DELETE FROM ncaab_gate_decisions')
