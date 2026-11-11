"""Personal delivery: no external network, real secret or live-store writes."""
import copy
from datetime import datetime, timezone

import httpx
import pytest

from app import config, telegram as tg


@pytest.fixture(autouse=True)
def isolated_card_assets(tmp_path, monkeypatch):
    from app import telegram_card
    monkeypatch.setenv('NBA_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(telegram_card, '_logo', lambda *_: None)


NOW = '2026-11-02T20:00:00Z'


def report():
    return {'sport':'nba', 'as_of':NOW, 'picks':[
        {'game_id':'g1', 'forecast_id':1, 'market':'team_total_home', 'market_title':'Home team points',
         'matchup':'BOS @ ORL', 'tipoff':'2026-11-02T21:00:00Z', 'generated_at':'2026-11-02T19:30:00Z',
         'status':'scheduled', 'grade':'pending', 'pick_label':'ORL over 109.5', 'opposite_pick_label':'ORL under 109.5',
         'probability':0.5181,
         'strong':False, 'game_rank':None,
         'percentage_history':{'sport':'nba', 'market':'team_total_home', 'model_version':'points-test', 'source':'primary_recorded', 'percentage':'51.8',
                               'wins':0, 'losses':2, 'settled':2, 'small_sample':True, 'line_source':'model'}}]}


class Bot:
    def __init__(self, updates=None, error=None):
        self.updates, self.error, self.sends = updates or [], error, []

    def call(self, method, body, **kwargs):
        if method == 'getUpdates':
            return self.updates
        assert method in ('sendMessage', 'sendPhoto')
        self.sends.append(body)
        if self.error:
            raise self.error
        return {'message_id':len(self.sends)}


def test_summary_is_pregame_only_names_opposite_and_preserves_model_line_warning():
    d = report()
    _, text = tg.message_batches(d, '123')[0]
    assert 'No strong pick' in text and 'ORL over 109.5' in text
    assert '<b>Model 51.8%</b>' in text and '0W–2L (2 settled)' in text
    assert 'Against model — EXPERIMENTAL: ORL under 109.5' in text
    assert 'no bookmaker offer verified' in text
    for changes in ({'grade':'loss'}, {'status':'final'}, {'tipoff':NOW},
                    {'tipoff':'2026-11-03T01:00:00Z'}, {'generated_at':'2026-11-02T20:01:00Z'}):
        changed = copy.deepcopy(d)
        changed['picks'][0].update(changes)
        assert tg.message_batches(changed, '123') == []


def test_forecast_ids_create_versions_but_observed_count_changes_alone_do_not_spam():
    d = report()
    first = tg.message_batches(d, '123')[0][0]
    d['picks'][0]['percentage_history']['losses'] += 1
    d['picks'][0]['percentage_history']['settled'] += 1
    assert tg.message_batches(d, '123')[0][0] == first
    d['picks'][0]['forecast_id'] = 2
    assert tg.message_batches(d, '123')[0][0] != first
    assert tg.message_batches(d, '456')[0][0] != tg.message_batches(d, '123')[0][0]


def test_missing_history_never_becomes_a_fake_zero_record_and_three_way_has_no_guessed_opposite():
    d = report()
    d['picks'][0]['percentage_history'] = None
    assert tg.message_batches(d, '123') == []
    d = report()
    d['picks'][0].update(market='reg3', market_title='Regulation result', opposite_pick_label=None)
    d['picks'][0]['percentage_history']['market'] = 'reg3'
    assert 'Against model' not in tg.message_batches(d, '123')[0][1]


@pytest.mark.parametrize('field,value', [('sport','nhl'),('market','total'),('percentage','52.0')])
def test_market_sport_or_percentage_mismatch_never_sends(field, value):
    d = report()
    d['picks'][0]['percentage_history'][field] = value
    with pytest.raises(tg.DeliveryError, match='does not match'):
        tg.message_batches(d, '123')


@pytest.mark.parametrize('source_state,failing', [('stale',False),('fresh',True)])
def test_stale_or_failed_sources_do_not_construct_or_send_picks(tmp_path, monkeypatch, source_state, failing):
    from app import db, freshness
    from app.providers import real
    class Source:
        def __init__(self, sport): self.sport = sport
        def execute(self, sql): assert sql == 'BEGIN'
        def close(self): pass
    monkeypatch.setattr(db, 'connect_readonly', Source)
    monkeypatch.setattr(db, 'store_sport', lambda s:s.sport)
    monkeypatch.setattr(freshness, 'summary', lambda *_:{'source_state':source_state,'failing':failing})
    monkeypatch.setattr(real, 'RealProvider', lambda *_:pytest.fail('unhealthy source must not create picks'))
    bot = Bot()
    with tg.ledger(tmp_path / 'receipts.sqlite') as conn:
        result = tg.run_once(bot, {'TELEGRAM_CHAT_ID':'123'}, conn, now=datetime(2026,11,2,20,tzinfo=timezone.utc))
    assert result['sports'] == {'nba':{'status':'source_not_fresh'},'nhl':{'status':'source_not_fresh'}}
    assert not bot.sends


def test_explicit_preview_keeps_actual_final_result_and_time_without_enabling_live_delivery():
    d = report()
    d['picks'][0].update(status='final',grade='loss')
    assert tg.message_batches(d, '123') == []
    key, text = tg.format_batch(d, '123', 'g1', d['picks'], preview=True)
    assert text.startswith('<b>TEST PREVIEW — PAST GAME, NOT A LIVE PICK</b>')
    assert 'Started Nov 02' in text and 'Result: Lost' in text
    assert '0W–3L (3 settled)' in text and 'Records include this game’s result' in text
    assert 'Opposite scenario — retrospective' in text and 'Against model — EXPERIMENTAL' not in text
    assert d['picks'][0]['grade'] == 'loss' and d['picks'][0]['tipoff'] == '2026-11-02T21:00:00Z'


def test_rich_message_escapes_source_text_and_keeps_recommendation_separate_from_experimental_signal():
    from html.parser import HTMLParser
    d = report()
    d['picks'][0].update(matchup='BOS & <ORL>', pick_label='<script>bad</script> & over 109.5',
                         strong=True, game_rank=1)
    _, text = tg.message_batches(d, '123')[0]
    assert '<b>Recommended pick</b>' in text
    assert 'BOS &amp; &lt;ORL&gt;' in text
    assert '&lt;script&gt;bad&lt;/script&gt; &amp; over 109.5' in text and '<script>' not in text
    assert 'Against model — EXPERIMENTAL' in text
    class Tags(HTMLParser):
        def __init__(self): super().__init__(); self.stack = []
        def handle_starttag(self, tag, attrs):
            assert tag in ('b','i') and not attrs
            self.stack.append(tag)
        def handle_endtag(self, tag): assert self.stack.pop() == tag
    parser = Tags()
    parser.feed(text)
    assert not parser.stack


def test_pick_summary_is_delivered_as_a_real_png_card_without_icons(tmp_path):
    import io
    from PIL import Image
    d = report()
    before = copy.deepcopy(d)
    key, text = tg.message_batches(d, '123')[0]
    assert not any(ord(c) >= 0x1F000 or c in '✅❌➖⭐' for c in text)
    class PhotoBot:
        def __init__(self): self.calls = 0
        def call(self, method, body, **kwargs):
            self.calls += 1
            assert method == 'sendPhoto' and 'text' not in body
            assert 'Recommended pick' in body['caption']
            image = Image.open(io.BytesIO(kwargs['photo']))
            assert image.format == 'PNG' and image.width == 1080
            assert image.width + image.height < 10000
            return {'message_id':123}
    bot = PhotoBot()
    with tg.ledger(tmp_path / 'receipts.sqlite') as conn:
        assert tg.deliver(bot, conn, '123', key, text, NOW) == 'sent'
        assert tg.deliver(bot, conn, '123', key, text, NOW) == 'already_recorded'
    assert bot.calls == 1 and d == before


def test_card_render_failure_does_not_claim_or_attempt_delivery(tmp_path, monkeypatch):
    from app import telegram_card
    def fail(_): raise ValueError('render unavailable')
    monkeypatch.setattr(telegram_card, 'render_card', fail)
    key, text = tg.message_batches(report(), '123')[0]
    bot = Bot()
    with tg.ledger(tmp_path / 'receipts.sqlite') as conn:
        with pytest.raises(ValueError, match='render unavailable'):
            tg.deliver(bot, conn, '123', key, text, NOW)
        assert conn.execute('SELECT COUNT(*) FROM deliveries').fetchone()[0] == 0
    assert not bot.sends


def test_photo_transport_uploads_binary_multipart_not_a_public_image_url():
    def handler(request):
        assert request.url.path.endswith('/sendPhoto')
        assert request.headers['content-type'].startswith('multipart/form-data;')
        body = request.read()
        assert b'name="photo"' in body and b'image/png' in body and b'png-test-bytes' in body
        return httpx.Response(200,json={'ok':True,'result':{'message_id':123}})
    bot = tg.Telegram('test-secret',transport=httpx.MockTransport(handler))
    assert bot.call('sendPhoto', {'chat_id':'123','caption':'Recommended pick'},photo=b'png-test-bytes')['message_id'] == 123


def test_database_uniqueness_survives_a_second_connection_and_allows_new_versions(tmp_path):
    path = tmp_path / 'receipts.sqlite'
    bot = Bot()
    with tg.ledger(path) as conn:
        assert tg.deliver(bot, conn, '123', 'same', 'test', NOW) == 'sent'
    with tg.ledger(path) as conn:
        assert tg.deliver(bot, conn, '123', 'same', 'test', NOW) == 'already_recorded'
        assert tg.deliver(bot, conn, '123', 'new-version', 'new', NOW) == 'sent'
        assert conn.execute('SELECT COUNT(*) FROM deliveries').fetchone()[0] == 2
    assert len(bot.sends) == 2
    assert all(message['parse_mode'] == 'HTML' for message in bot.sends)
    assert path.stat().st_mode & 0o077 == 0


def test_ambiguous_ack_or_crashed_claim_is_not_automatically_resent(tmp_path):
    bot = Bot(error=tg.DeliveryError('ack unknown', uncertain=True))
    with tg.ledger(tmp_path / 'receipts.sqlite') as conn:
        assert tg.deliver(bot, conn, '123', 'a', 'test', NOW) == 'uncertain'
        assert tg.deliver(bot, conn, '123', 'a', 'test', NOW) == 'already_recorded'
        conn.execute("INSERT INTO deliveries(delivery_key,chat_id,text,status,attempted_at) VALUES ('crashed','123','x','sending',?)", (NOW,))
        assert tg.deliver(bot, conn, '123', 'crashed', 'x', NOW) == 'already_recorded'
    assert len(bot.sends) == 1


def test_confirmed_connection_failure_retries_only_after_backoff(tmp_path):
    bot = Bot(error=tg.DeliveryError('not connected', retry_after=60))
    with tg.ledger(tmp_path / 'receipts.sqlite') as conn:
        assert tg.deliver(bot, conn, '123', 'a', 'test', NOW) == 'retry'
        assert tg.deliver(bot, conn, '123', 'a', 'test', '2026-11-02T20:00:59Z') == 'already_recorded'
        bot.error = None
        assert tg.deliver(bot, conn, '123', 'a', 'test', '2026-11-02T20:01:00Z') == 'sent'
    assert len(bot.sends) == 2


def test_credentials_are_private_and_can_be_extended_without_echoing_token(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'REPO_ROOT', tmp_path)
    path = tmp_path / '.env.telegram'
    path.write_text('TELEGRAM_BOT_TOKEN=test-secret\n')
    path.chmod(0o600)
    config.save_telegram_setting('TELEGRAM_CHAT_ID', '123')
    assert config.telegram_settings()['TELEGRAM_BOT_TOKEN'] == 'test-secret'
    assert config.telegram_settings()['TELEGRAM_CHAT_ID'] == '123'
    assert path.stat().st_mode & 0o077 == 0
    path.chmod(0o644)
    with pytest.raises(ValueError, match='private'):
        config.telegram_settings()


def test_pairing_rejects_generic_start_wrong_code_and_groups(monkeypatch):
    saved = {}
    monkeypatch.setattr(config, 'save_telegram_setting', saved.__setitem__)
    def update(chat, text, kind='private'):
        return {'message':{'chat':{'id':chat,'type':kind}, 'text':text,'from':{'is_bot':False}}}
    settings = {'TELEGRAM_PAIR_CODE':'private-code'}
    bot = Bot([update(999, '/start'), update(999, '/start other'), update(999, '/start private-code','group')])
    assert tg.connect(bot, settings)['connected'] is False and saved == {}
    bot.updates.append(update(123, '/start private-code'))
    assert tg.connect(bot, settings)['connected'] is True
    assert saved == {'TELEGRAM_CHAT_ID':'123'}
    bot.updates.append(update(456, '/start private-code'))
    with pytest.raises(tg.DeliveryError, match='Multiple'):
        tg.connect(bot, settings)


def test_stop_only_accepts_the_paired_private_chat_and_does_not_read_stores(tmp_path, monkeypatch):
    from app import db
    monkeypatch.setattr(db, 'connect_readonly', lambda *_: pytest.fail('paused worker must not read forecasts'))
    updates = [
        {'update_id':1,'message':{'chat':{'id':123,'type':'private'},'text':'/stop'}},
        {'update_id':2,'message':{'chat':{'id':999,'type':'private'},'text':'/start'}}]
    with tg.ledger(tmp_path / 'receipts.sqlite') as conn:
        assert tg.run_once(Bot(updates), {'TELEGRAM_CHAT_ID':'123'}, conn) == {'status':'paused'}
        assert tg._get(conn, 'offset') == '3'


def test_http_failures_are_redacted_and_rate_limits_are_safe_to_retry():
    token = 'test-secret'
    def timeout(request):
        raise httpx.ReadTimeout('URL contains '+token, request=request)
    bot = tg.Telegram(token, transport=httpx.MockTransport(timeout))
    with pytest.raises(tg.DeliveryError) as caught:
        bot.call('sendMessage', {})
    assert token not in str(caught.value) and caught.value.uncertain is True
    bot = tg.Telegram(token, transport=httpx.MockTransport(lambda r:httpx.Response(429, json={'ok':False,'parameters':{'retry_after':90}})))
    with pytest.raises(tg.DeliveryError) as caught:
        bot.call('sendMessage', {})
    assert caught.value.retry_after == 90 and token not in str(caught.value)


def test_active_webhook_is_not_deleted():
    called = []
    def handler(r):
        method = r.url.path.rsplit('/',1)[-1]
        called.append(method)
        result = {'is_bot':True,'username':'Prop51picksbot'} if method=='getMe' else {'url':'https://existing.example/webhook'}
        return httpx.Response(200,json={'ok':True,'result':result})
    with pytest.raises(tg.DeliveryError, match='webhook'):
        tg.Telegram('test-secret',transport=httpx.MockTransport(handler)).identity()
    assert called == ['getMe','getWebhookInfo']


def test_cli_telegram_does_not_open_or_migrate_a_forecast_store(monkeypatch):
    from app import cli, db
    monkeypatch.setattr(tg, 'command', lambda args:0 if args.telegram_action=='status' else 1)
    monkeypatch.setattr(db, 'connect', lambda *_a,**_k:pytest.fail('not a forecast job'))
    assert cli.main(['telegram','status']) == 0
    assert cli.main(['telegram','run','--once']) == 1


def test_published_branding_is_complete_and_sport_specific():
    from app.telegram_card import _team_branding, _team_palette
    teams=_team_branding()
    assert {s:len(v) for s,v in teams.items()}=={'NBA':30,'NHL':32,'NFL':32}
    assert _team_palette('NHL','ANA')[0]=='#FC4C02'
    assert _team_palette('NHL','VAN')[0]=='#003E7E'
    assert _team_palette('NBA','BOS')!=_team_palette('NHL','BOS')
    assert _team_palette('NBA','GSW')==_team_palette('NBA','GS')
    assert _team_palette('NHL','LAK')==_team_palette('NHL','LA')
    assert _team_palette('NHL','UTA')==_team_palette('NHL','UTAH')
    assert _team_palette('NFL','UNKNOWN')==('#09283F','#8495A2')
    for values in teams.values():
        for team in values.values():
            assert all(len(team[k])==7 and int(team[k][1:],16)>=0 for k in ('primary','secondary'))


def test_game_card_caption_says_single_game_not_a_parlay_ticket(tmp_path):
    key, text = tg.message_batches(report(), '123')[0]
    class PhotoBot:
        def call(self, method, body, **kwargs):
            self.caption = body['caption']
            return {'message_id': 1}
    bot = PhotoBot()
    with tg.ledger(tmp_path / 'receipts.sqlite') as conn:
        assert tg.deliver(bot, conn, '123', key, text, NOW) == 'sent'
    assert 'GAME CARD' in bot.caption and 'not a parlay ticket' in bot.caption.lower() and 'Recommended pick' in bot.caption


def strong_report(label='DET -10.5', forecast_id=1, tipoff='2026-11-02T21:00:00Z'):
    d = report()
    d['picks'][0].update(market='spread', market_title='Point spread', pick_label=label, opposite_pick_label='WSH +10.5', strong=True,
                         game_rank=1, forecast_id=forecast_id, tipoff=tipoff)
    d['picks'][0]['percentage_history'].update(market='spread', line_source='bookmaker', wins=3, losses=1, settled=4)
    return d


def card_sends(tmp_path, reports, *, error=None, first_status=None):
    """Statuses of delivering each report's single card in turn through the one-card-per-game guard."""
    bot, out = Bot(), []
    with tg.ledger(tmp_path / 'cards.sqlite') as conn:
        for i, d in enumerate(reports):
            key, text = tg.message_batches(d, '123')[0]
            out.append(tg.deliver_card(bot, conn, '123', key, text, NOW))
            if i == 0 and first_status:
                conn.execute('UPDATE deliveries SET status=?', (first_status,))
    return out, bot


def test_one_card_per_game_a_new_forecast_version_or_a_moved_line_is_not_sent_again(tmp_path):
    """Owner request Nov 9. The delivery key is the earlier release's, so its receipts are matched at cutover too."""
    out, bot = card_sends(tmp_path, [strong_report(), strong_report(forecast_id=2), strong_report('DET -9.5', forecast_id=3)])
    assert out == ['sent', 'already_sent_for_game', 'already_sent_for_game'] and len(bot.sends) == 1
    assert tg.message_batches(strong_report(), '123')[0][0] != tg.message_batches(strong_report(forecast_id=2), '123')[0][0]


def test_a_new_recommended_side_or_a_new_start_sends_the_game_again(tmp_path):
    out, bot = card_sends(tmp_path, [strong_report(), strong_report('WSH +10.5', forecast_id=2),
                                     strong_report(forecast_id=3, tipoff='2026-11-02T21:30:00Z'), strong_report()])
    assert out == ['sent', 'sent', 'sent', 'already_recorded'] and len(bot.sends) == 3


@pytest.mark.parametrize('status,expected', [('uncertain', 'already_sent_for_game'), ('sending', 'already_sent_for_game'), ('failed', 'sent')])
def test_a_card_that_may_have_arrived_blocks_the_game_and_a_rejected_one_does_not(tmp_path, status, expected):
    out, bot = card_sends(tmp_path, [strong_report(), strong_report(forecast_id=2)], first_status=status)
    assert out == ['sent', expected]


def test_card_identity_ignores_the_line_only_and_is_none_for_other_messages():
    text = tg.message_batches(strong_report(), '123')[0][1]
    assert tg.card_identity(text)[3] == 'DET' and tg.card_identity('PROP51 / REVIEW BLOCKER / NBA') is None
    assert tg.card_identity(tg.message_batches(strong_report('Over 233.5'), '123')[0][1])[3] == 'Over'
    assert tg.card_identity(tg.message_batches(report(), '123')[0][1])[3] == 'No strong pick'


def priced_card(history=None):
    d = strong_report('CLE win')
    d['picks'][0].update(matchup='CLE @ CHA', market='winner', market_title='Winner', probability=0.552, market_prob=0.7404, opposite_pick_label='CHA win')
    d['picks'][0]['percentage_history'].update(market='winner', percentage='55.2', line_source='none', **(history or {}))
    spread = copy.deepcopy(strong_report('CHA +8.5')['picks'][0])
    spread.update(matchup='CLE @ CHA', strong=False, game_rank=2, probability=0.5708, market_prob=0.5043, forecast_id=2)
    spread['percentage_history'].update(percentage='57.1', **(history or {}))
    total = copy.deepcopy(spread)
    total.update(market='total', market_title='Game total points', pick_label='Over 215.5', probability=0.52, market_prob=0.51, forecast_id=3)
    total['percentage_history'].update(market='total', percentage='52.0')
    d['picks'] += [spread, total]
    return d


def test_card_says_in_plain_words_how_the_app_and_the_sportsbook_compare():
    """Owner request Nov 9 (CLE @ CHA: 'CLE win' was recommended at 55% while the sportsbook had it at 74%; the card never
    said so). Simple words: overpriced / underpriced / fair price."""
    key, text = tg.message_batches(priced_card(), '123')[0]
    assert 'App 55.2% · Sportsbook 74.0% · OVERPRICED' in text
    assert 'CLE win · <i>sportsbook 74.0% · overpriced</i>' in text
    assert 'CHA +8.5 · <i>bookmaker line at forecast · sportsbook 50.4% · underpriced</i>' in text
    assert 'Over 215.5 · <i>bookmaker line at forecast · sportsbook 51.0% · fair price</i>' in text
    assert '<b>Model 57.1%</b> · 3W–1L (4 settled)' in text          # the record line is exactly as before
    assert tg.card_identity(text)[3] == 'CLE win'
    assert (tg.price_tag(.60, .57), tg.price_tag(.60, .571), tg.price_tag(.60, .63), tg.price_tag(.60, .629)) == ('underpriced', 'fair price', 'overpriced', 'fair price')


def test_priced_card_renders_and_no_line_is_longer_than_the_card():
    """Found on a real card (CLE @ CHA, Nov 9): added sportsbook text pushed the record line off the right edge."""
    from PIL import Image, ImageDraw
    from app import telegram_card
    text = tg.message_batches(priced_card(dict(wins=0, losses=0, settled=0, small_sample=True)), '123')[0][1]
    assert telegram_card.render_card(text).startswith(b'\x89PNG')
    probe = ImageDraw.Draw(Image.new('RGB', (10, 10)))
    assert 200 + probe.textlength('0W–0L (0 settled) · No settled matching examples.', font=telegram_card._font(22)) <= 1000
    assert 200 + probe.textlength('Bookmaker line at forecast · sportsbook 50.4% · underpriced', font=telegram_card._font(20)) <= 1000
    assert 95 + probe.textlength('App 55.2% · Sportsbook 74.0% · OVERPRICED', font=telegram_card._font(27)) <= 930


def test_card_without_a_stored_price_is_unchanged():
    text = tg.message_batches(report(), '123')[0][1]            # team total: no sportsbook price is stored
    assert 'ortsbook' not in text and '<b>Model 51.8%</b> · 0W–2L (2 settled)' in text and 'ORL over 109.5 · <i>model line</i>' in text
    d = strong_report(); d['picks'][0]['market_prob'] = 0.48
    assert tg.message_batches(d, '123')[0][0] == tg.message_batches(strong_report(), '123')[0][0]   # same delivery key: no resend


def test_exact_percentages_are_ranked_by_record_once_they_have_three_results():
    """Owner request Nov 9: 'we track every percentage to see the strongest ones'. Exact percentages only, never a range."""
    from app import threshold_overview as to
    key = lambda pct, side='a': (pct, side, 'primary_recorded', 'm', 'none')
    index = {key('87.8'): {'g1': 'win', 'g2': 'win', 'g3': 'win'},                    # 3-0
             key('87.9'): {'g4': 'win', 'g5': 'loss', 'g6': 'win', 'g7': 'win'},      # 3-1: a different percentage, its own record
             key('51.4'): {'g8': 'loss', 'g9': 'loss', 'g10': 'loss'},                # 0-3
             key('60.0'): {'g11': 'win', 'g12': 'win'},                               # two results: tracked, not ranked yet
             key('87.8', 'b'): {'g13': 'win', 'g14': 'push', 'g15': 'not_yet_available'}}   # other direction, one settled
    ranking = to.percentage_ranking(index)
    assert [(r['percentage'], r['side'], r['wins'], r['losses'], r['rank']) for r in ranking] == [
        ('87.8', 'a', 3, 0, 1), ('87.9', 'a', 3, 1, 2), ('51.4', 'a', 0, 3, 3)]
    rec = {'confidence': 0.879, 'side': 'a', 'source': 'primary_recorded', 'model_version': 'm', 'line_source': 'none'}
    mine = to.percentage_record('nba', 'winner', rec, 'new-game', index, NOW)
    assert (mine['wins'], mine['losses'], mine['rank'], mine['ranked']) == (3, 1, 2, 3)
    assert to.percentage_record('nba', 'winner', rec, 'g5', index, NOW)['rank'] == 1      # its own game never counts toward its record
    assert to.percentage_record('nba', 'winner', dict(rec, confidence=0.60), 'new-game', index, NOW)['rank'] is None
    from app.schemas import ForecastPercentageRecord
    assert ForecastPercentageRecord(**mine).rank == 2


def test_card_shows_the_rank_of_a_ranked_percentage_and_avoid_for_a_losing_one():
    d = strong_report()
    d['picks'][0]['percentage_history'].update(wins=5, losses=1, settled=6, rank=2, ranked=41)
    assert '<b>Model 51.8%</b> · 5W–1L (6 settled) · rank 2 of 41\n' in tg.message_batches(d, '123')[0][1]
    d['picks'][0]['percentage_history'].update(wins=0, losses=3, settled=3, rank=41, ranked=41)
    text = tg.message_batches(d, '123')[0][1]
    assert '0W–3L (3 settled) · rank 41 of 41 · AVOID' in text
    from PIL import Image, ImageDraw
    from app import telegram_card
    assert telegram_card.render_card(text).startswith(b'\x89PNG')
    probe = ImageDraw.Draw(Image.new('RGB', (10, 10)))
    assert 200 + probe.textlength('10W–13L (23 settled) · rank 141 of 141 · AVOID · Small sample.', font=telegram_card._font(22)) <= 1000
    plain = tg.message_batches(strong_report(), '123')[0][1]            # not ranked: the line is exactly as before
    assert '<b>Model 51.8%</b> · 3W–1L (4 settled)\n' in plain and ' · rank ' not in plain


def test_price_tag_includes_three_point_boundaries_despite_binary_rounding():
    assert tg.price_tag(.58, .55) == 'underpriced'
    assert tg.price_tag(.55, .58) == 'overpriced'
    assert tg.price_tag(.57999, .55) == 'fair price'


def test_ranking_failure_does_not_hide_the_existing_percentage_record(monkeypatch):
    from app import threshold_overview as to
    rec = dict(confidence=.60, side='a', source='primary_recorded', model_version='m', line_source='none')
    index = {to._percentage_key(rec): {'g1':'win', 'g2':'win', 'g3':'loss'}}
    def broken(*args):
        raise ValueError('ranking unavailable')
    monkeypatch.setattr(to, 'percentage_ranking', broken)
    history = to.percentage_record('nba', 'winner', rec, 'new-game', index, NOW)
    assert (history['wins'], history['losses'], history['rank'], history['ranked']) == (2, 1, None, 0)


def test_a_connection_that_never_opened_is_retried_and_nothing_else_is():
    """Nov 10: intermittent DeliveryError in the alert path; 59 of 77 failed runs coincided with the Mac going to sleep.
    A ConnectError means the request never reached Telegram, so a short retry cannot send twice."""
    import httpx
    from app.telegram import DeliveryError, Telegram
    calls, waits = [], []

    def transport(fail_with, failures):
        def handler(request):
            calls.append(request)
            if len(calls) <= failures:
                raise fail_with('down', request=request)
            return httpx.Response(200, json={'ok': True, 'result': {'id': 1}})
        return httpx.MockTransport(handler)

    bot = Telegram('t', transport=transport(httpx.ConnectError, 2))
    assert bot.call('getMe', pause=waits.append) == {'id': 1} and len(calls) == 3 and waits == [3, 7]
    calls.clear(); waits.clear()
    bot = Telegram('t', transport=transport(httpx.ConnectError, 3))
    with pytest.raises(DeliveryError, match='connection unavailable'):
        bot.call('getMe', pause=waits.append)
    assert len(calls) == 3
    calls.clear(); waits.clear()
    bot = Telegram('t', transport=transport(httpx.ReadTimeout, 1))     # may have reached Telegram: never retried
    with pytest.raises(DeliveryError, match='not confirmed'):
        bot.call('sendMessage', {'text': 'x'}, pause=waits.append)
    assert len(calls) == 1 and waits == []


def test_lineup_line_sits_under_the_pick_and_never_changes_the_card_key_or_identity(tmp_path):
    """Owner request Nov 11: who is playing, one plain line under the pick; a late lineup change never resends the card."""
    d = strong_report()
    key, plain = tg.message_batches(d, '123')[0]
    key2, text = tg.message_batches(d, '123', lineup_lines={'g1': 'Out: L. James'})[0]
    lines = text.split('\n')
    pick = lines.index('<b>Recommended pick</b>')
    assert 'Lineup: Out: L. James' in lines[pick + 2:pick + 4] and 'Lineup:' not in plain
    assert key2 == key and tg.card_identity(text) == tg.card_identity(plain)
    assert tg.message_batches(d, '123', lineup_lines={'other': 'x'})[0][1] == plain
    bot = Bot()
    with tg.ledger(tmp_path / 'cards.sqlite') as conn:
        assert tg.deliver_card(bot, conn, '123', key, plain, NOW) == 'sent'
        later = tg.message_batches(strong_report(forecast_id=2), '123', lineup_lines={'g1': 'Backup goalie starting: TOR'})[0]
        assert tg.deliver_card(bot, conn, '123', *later, NOW) == 'already_sent_for_game'
        assert tg.deliver_card(bot, conn, '123', key2, text, NOW) == 'already_recorded'
    assert len(bot.sends) == 1


@pytest.mark.parametrize('line', ['Goalies not confirmed', 'Out: S. Barnes, R. Barrett, I. Quickley · Doubtful: J. Brown, '
                                  'D. White, J. Tatum · Questionable: K. Porzingis, A. Simons, P. Pritchard'])
def test_lineup_line_renders_on_one_line_inside_the_card(line):
    from PIL import Image, ImageDraw
    from app import telegram_card
    text = tg.message_batches(priced_card(), '123', lineup_lines={'g1': line})[0][1]
    assert telegram_card.render_card(text).startswith(b'\x89PNG')
    probe = ImageDraw.Draw(Image.new('RGB', (10, 10)))
    shown = telegram_card.fit_line(probe, line, telegram_card._font(26, True), 860)
    assert 95 + probe.textlength(shown, font=telegram_card._font(26, True)) <= 1000
    assert shown == line or shown.endswith('…')
    without = telegram_card.render_card(tg.message_batches(priced_card(), '123')[0][1])
    from io import BytesIO
    assert Image.open(BytesIO(telegram_card.render_card(text))).height > Image.open(BytesIO(without)).height


def test_a_lineup_problem_never_stops_a_card(monkeypatch):
    from app import lineup
    monkeypatch.setattr(lineup, 'facts', lambda *a: 1 / 0)
    now = datetime(2026, 11, 2, 20, 0, tzinfo=timezone.utc)
    assert tg.card_lineups(None, 'nba', strong_report(), now) == {}
    monkeypatch.setattr(lineup, 'facts', lambda conn, sport, ids, at: {g: 'x' for g in ids})
    monkeypatch.setattr(lineup, 'card_line', lambda sport, fact, matchup: f'{sport} {matchup}')
    assert tg.card_lineups(None, 'nba', strong_report(), now) == {'g1': 'nba BOS @ ORL'}


def test_a_long_wrapped_pick_pushes_the_lineup_line_down_instead_of_overlapping():
    """Review LOW 10: a 48 px pick label that wraps to two lines must not run into the lineup line."""
    from io import BytesIO
    from PIL import Image
    from app import telegram_card
    short = tg.message_batches(priced_card(), '123', lineup_lines={'g1': 'Goalies not confirmed'})[0][1]
    long = short.replace('<b>CLE win</b>', '<b>Cleveland Cavaliers to win in regulation time tonight</b>', 1)
    heights = [Image.open(BytesIO(telegram_card.render_card(t))).height for t in (short, long)]
    assert heights[1] > heights[0]
