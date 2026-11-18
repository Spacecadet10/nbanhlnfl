"""Owner decision Nov 17: a sent pick that becomes unsupported gets at most ONE 'Pick withdrawn' update linked to the original
card, never a second full card. Fake bot and a tmp ledger only; nothing is ever sent."""
import sqlite3

import pytest

from app import published_picks as pp, telegram as tg
from test_telegram import Bot, strong_report

T0, T1, T2 = '2026-11-02T19:40:00Z', '2026-11-02T20:00:00Z', '2026-11-02T20:30:00Z'


def supported(forecast_id=1, label='DET -10.5', probability=0.5181):
    d = strong_report(label, forecast_id)
    d['picks'][0]['probability'] = probability
    d['picks'][0]['percentage_history']['percentage'] = f'{probability * 100:.1f}'
    d['picks'][0].update(line=-10.5, side='a')
    return d


def unsupported(forecast_id=2, label='WSH +9.5', as_of=T1):
    d = supported(forecast_id, label, 0.5333)
    d['as_of'] = as_of
    d['picks'][0]['quote_check'] = {'line_moved': True}
    d['picks'][0]['line'] = 9.5
    return d


def card(report):
    key, text = tg.message_batches(report, '123', context_selection=True)[0]
    return key, text


def send(bot, conn, report, now, *, context=True):
    key, text = card(report)
    ctx = {'sport': 'nba', 'game_id': 'g1', 'report': report} if context else None
    return tg.deliver_card(bot, conn, '123', key, text, now, context=ctx)


def sent_original(bot, conn, **kw):
    key, text = card(supported(**kw))
    assert tg.deliver_card(bot, conn, '123', key, text, T0) == 'sent'
    tg.record_published(conn, 'nba', key, 'g1', supported(**kw), __import__('datetime').datetime(2026, 11, 2, 19, 40, tzinfo=__import__('datetime').timezone.utc))
    return key


def test_the_unsupported_card_really_says_no_supported_pick():
    assert tg.card_identity(card(unsupported())[1])[3] == 'No supported pick'
    assert tg.card_identity(card(supported())[1])[3] == 'DET'


def test_a_withdrawal_replaces_the_second_card_and_replies_to_the_original(tmp_path):
    bot = Bot()
    with tg.ledger(tmp_path / 'l.sqlite') as conn:
        okey = sent_original(bot, conn)
        assert send(bot, conn, unsupported(), T1) == 'withdrawn'
        assert len(bot.sends) == 2
        body = bot.sends[1]
        assert body['parse_mode'] == 'HTML'
        assert body['reply_parameters'] == {'message_id': 1, 'allow_sending_without_reply': True}
        lines = body['text'].split('\n')
        assert lines[0] == '<b>PROP51 UPDATE · NBA</b>' and lines[1] == '<b>BOS @ ORL</b>' and lines[2].startswith('Starts ')
        assert '<b>Pick withdrawn</b>' in lines
        assert any(l.startswith('Original pick: <b>DET -10.5</b> · Point spread · Model 51.8% · sent Nov 02, ') for l in lines)
        assert 'Now: no supported pick — The original line changed or could not be checked.' in lines
        assert 'What changed: Point spread' in lines
        assert 'Original forecast: #1 · DET -10.5 · line -10.5 · 51.8%' in lines
        assert 'Current forecast: #2 · WSH +9.5 · line 9.5 · 53.3%' in lines
        assert lines[-1].startswith('<i>The original card and both forecasts are kept.')
        assert tg.card_identity(body['text']) is None
        # no second full card, a delivery row for the update only, the original and its published pick untouched
        assert conn.execute("SELECT COUNT(*) FROM deliveries WHERE text LIKE '%PROP51 PICKS%'").fetchone()[0] == 1
        row = conn.execute('SELECT * FROM deliveries WHERE delivery_key LIKE ?', ('pick-withdrawn-v1:%',)).fetchone()
        assert row['status'] == 'sent' and row['message_id'] == 2
        assert conn.execute('SELECT COUNT(*), MIN(forecast_id) FROM published_picks WHERE delivery_key=?', (okey,)).fetchone()[:] == (1, '1')
        audit = dict(conn.execute('SELECT * FROM pick_withdrawals').fetchone())
        assert (audit['original_delivery_key'], audit['sport'], audit['game_id'], audit['market'], audit['original_forecast_id'],
                audit['new_forecast_id'], audit['status'], audit['message_id']) == (okey, 'nba', 'g1', 'spread', '1', '2', 'sent', 2)
        assert 'line changed' in audit['reason'] and audit['withdrawal_key'] == row['delivery_key']
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("UPDATE pick_withdrawals SET status='x'")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute('DELETE FROM pick_withdrawals')


def test_reruns_and_later_forecasts_send_nothing_more(tmp_path):
    bot = Bot()
    with tg.ledger(tmp_path / 'l.sqlite') as conn:
        sent_original(bot, conn)
        assert send(bot, conn, unsupported(), T1) == 'withdrawn'
        assert send(bot, conn, unsupported(), T1) == 'already_recorded'
        assert send(bot, conn, unsupported(3, 'WSH +8.5', T2), T2) == 'already_recorded'
        assert send(bot, conn, unsupported(), '2026-11-03T20:00:00Z') == 'already_recorded'
        assert len(bot.sends) == 2
        assert conn.execute('SELECT COUNT(*) FROM pick_withdrawals').fetchone()[0] == 1


@pytest.mark.parametrize('claimed', ['sending', 'uncertain', 'failed'])
def test_a_crashed_or_uncertain_withdrawal_is_never_sent_again(tmp_path, claimed):
    bot = Bot()
    with tg.ledger(tmp_path / 'l.sqlite') as conn:
        sent_original(bot, conn)
        wkey = 'pick-withdrawn-v1:' + __import__('hashlib').sha256(card(supported())[0].encode()).hexdigest()
        conn.execute("INSERT INTO deliveries(delivery_key,chat_id,text,status,attempted_at) VALUES (?,?,?,?,?)", (wkey, '123', 'x', claimed, T1))
        assert send(bot, conn, unsupported(), T2) == 'already_recorded'
        assert len(bot.sends) == 1


def test_an_uncertain_withdrawal_ack_is_recorded_and_not_retried(tmp_path):
    bot = Bot()
    with tg.ledger(tmp_path / 'l.sqlite') as conn:
        sent_original(bot, conn)
        bot.error = tg.DeliveryError('ack unknown', uncertain=True)
        assert send(bot, conn, unsupported(), T1) == 'uncertain'
        bot.error = None
        assert send(bot, conn, unsupported(), T2) == 'already_recorded'
        assert len(bot.sends) == 2
        assert conn.execute('SELECT status FROM pick_withdrawals').fetchone()[0] == 'uncertain'


def test_an_original_without_a_message_id_names_its_send_time_instead_of_replying(tmp_path):
    bot = Bot()
    with tg.ledger(tmp_path / 'l.sqlite') as conn:
        sent_original(bot, conn)
        conn.execute("UPDATE deliveries SET status='uncertain', message_id=NULL")
        assert send(bot, conn, unsupported(), T1) == 'withdrawn'
        body = bot.sends[1]
        assert 'reply_parameters' not in body and 'reply_to_message_id' not in body
        assert any(l.startswith('Original card sent Nov 02, ') and l.endswith('its message link is unavailable.') for l in body['text'].split('\n'))


def test_an_earlier_no_supported_card_means_no_withdrawal_on_top(tmp_path):
    bot = Bot()
    with tg.ledger(tmp_path / 'l.sqlite') as conn:
        sent_original(bot, conn)
        key, text = card(unsupported())      # the historical pattern: the second full card already went out after the first
        assert tg.deliver(bot, conn, '123', key, text, T1) == 'sent'
        assert send(bot, conn, unsupported(3, 'WSH +8.5', T2), T2) == 'already_sent_for_game'
        assert len(bot.sends) == 2 and conn.execute('SELECT COUNT(*) FROM pick_withdrawals').fetchone()[0] == 0


def test_a_game_whose_first_card_had_no_supported_pick_has_nothing_to_withdraw(tmp_path):
    bot = Bot()
    with tg.ledger(tmp_path / 'l.sqlite') as conn:
        assert send(bot, conn, unsupported(1), T0) == 'sent'
        assert send(bot, conn, unsupported(2), T1) == 'already_sent_for_game'
        assert len(bot.sends) == 1


def test_a_pick_that_is_supported_again_follows_the_existing_rules(tmp_path):
    bot = Bot()
    with tg.ledger(tmp_path / 'l.sqlite') as conn:
        sent_original(bot, conn)
        assert send(bot, conn, unsupported(), T1) == 'withdrawn'
        assert send(bot, conn, supported(4), T2) == 'already_sent_for_game'     # same pick: already sent
        assert send(bot, conn, supported(5, 'WSH +10.5'), T2) == 'sent'          # a different pick: a new card


def test_ordinary_cards_keep_their_keys_and_identity():
    text = card(supported())[1]
    assert tg.card_identity(text)[3] == 'DET'
    key = tg.message_batches(strong_report(), '123')[0][0]
    assert key == tg.message_batches(strong_report(), '123', context_selection=False)[0][0]


def test_without_a_context_the_withdrawal_still_comes_from_the_card_text(tmp_path):
    bot = Bot()
    with tg.ledger(tmp_path / 'l.sqlite') as conn:
        sent_original(bot, conn)
        assert send(bot, conn, unsupported(), T1, context=False) == 'withdrawn'
        assert 'Now: no supported pick' in bot.sends[1]['text'] and 'What changed: Point spread' in bot.sends[1]['text']


def test_an_audit_failure_never_stops_the_update(tmp_path, monkeypatch):
    bot = Bot()
    with tg.ledger(tmp_path / 'l.sqlite') as conn:
        sent_original(bot, conn)
        monkeypatch.setattr(pp, 'record_withdrawal', lambda *a, **k: 1 / 0)
        assert send(bot, conn, unsupported(), T1) == 'withdrawn' and len(bot.sends) == 2


def test_message_id_gap_rows_are_kept_but_are_not_confirmed_published_picks(tmp_path):
    path = tmp_path / 'l.sqlite'
    bot = Bot()
    with tg.ledger(path) as conn:
        sent_original(bot, conn)
        conn.execute("INSERT INTO published_picks (delivery_key,sport,game_id,market,forecast_id,side,line,probability,sent_at,basis) "
                     "VALUES ('k2','nba','g2','spread','7','a',-3.5,0.6,?,'uncertain_resolved_message_id_gap')", (T0,))
    loaded = pp.load('nba', path)
    assert ('g1', 'spread') in loaded and ('g2', 'spread') not in loaded
    with sqlite3.connect(path) as raw:
        assert raw.execute("SELECT COUNT(*) FROM published_picks WHERE basis='uncertain_resolved_message_id_gap'").fetchone()[0] == 1
