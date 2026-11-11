"""Ticket lifecycle uses synthetic forecasts, temporary ledgers and a mocked Telegram transport."""
import copy
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest

from app import tickets as t, telegram as tg, config

NOW = '2026-11-08T17:00:00Z'


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('NBA_DATA_DIR',str(tmp_path))
    from app import telegram_card
    monkeypatch.setattr(telegram_card,'_logo',lambda *a:None)
    monkeypatch.setattr(t,'SKILL',tmp_path/'SKILL.md');t.SKILL.write_text('fixture')
    picks = [dict(forecast_id=i,game_id=f'g{i}',market='winner',matchup=f'A{i} @ B{i}',
                  pick_label=f'B{i} win',probability=.75,status='scheduled',grade='pending',
                  generated_at='2026-11-08T16:30:00Z',tipoff='2026-11-08T18:00:00Z',lineup_flags=[]) for i in range(1,6)]
    snap = dict(as_of=NOW,date='2026-11-08',skill_sha256=t.sha(t.SKILL.read_bytes()),
                helper_sha256=t.sha(Path(t.__file__).read_bytes()),sports={s:dict(freshness=dict(source_state='fresh',failing=False),
                report=dict(picks=copy.deepcopy(picks),without_forecast={})) for s in ('nba','nhl','nfl')})
    snap['sports']['soccer']={'error':'forecasts_unavailable'}
    monkeypatch.setattr(t,'snapshot',lambda:copy.deepcopy(snap))
    path=tmp_path/'snapshot.json';path.write_text(json.dumps(snap))
    review=dict(skill_sha256=snap['skill_sha256'],helper_sha256=snap['helper_sha256'],snapshot_sha256=t.sha(path.read_bytes()),
                notes='Fixture review',combination_comparison='Compare NBA/NHL/NFL combinations together',
                markets_reviewed={s:{'winner':'Reviewed all fixture winners'} for s in ('nba','nhl','nfl')})
    review['forecasts_reviewed']={f'{sport}:{i}':dict(decision='candidate',reason='Compared fixture market, side and evidence') for sport in ('nba','nhl','nfl') for i in range(1,6)}
    plan=dict(snapshot_path=str(path),review=review,tickets=[])
    ledger=tg.ledger
    monkeypatch.setattr(tg,'ledger',lambda:ledger(tmp_path/'ledger.sqlite'))
    monkeypatch.setattr(config,'telegram_settings',lambda:dict(TELEGRAM_CHAT_ID='fixture',TELEGRAM_BOT_TOKEN='fixture'))
    monkeypatch.setattr(tg,'controls',lambda *a:True)
    sent=[]
    class Bot:
        def __init__(self,*a):pass
        def identity(self):return 'fixture'
        def call(self,method,payload,**kw):sent.append((method,payload));return dict(message_id=len(sent))
    monkeypatch.setattr(tg,'Telegram',Bot)
    return plan,snap,sent,tmp_path


def stored_texts():
    """The exact ticket texts kept in the ledger, oldest first. Since Nov 9 a batch is delivered as one picture with a
    short caption; the full text is stored and archived, no longer sent as its own message."""
    with tg.ledger() as c:
        return [r[0] for r in c.execute("select text from deliveries where delivery_key like 'ai-ticket-review-v3:%' order by rowid")]


def ticket(legs,stake=1):
    return dict(label='Main' if stake==3 else 'Variation',stake_usd=stake,payout_assessment='Fixture payout judgment',
                legs=[dict(sport=s,forecast_id=i,rationale='Fixture selection') for s,i in legs])


def test_mixed_ticket_sent_as_numbered_text_exact_archive_and_dedup(setup):
    plan,snap,sent,path=setup;plan['tickets']=[ticket([('nba',1),('nhl',2)],3)]
    checked=t.check(plan);assert t.send(checked)=='sent';assert t.send(checked)=='existing_sent'
    assert [m for m,p in sent]==['sendPhoto']   # one picture; no separate text message, nothing sent twice
    caption=sent[0][1]['caption']
    assert len(caption)<=1024 and 'B1 win' in caption and 'B2 win' in caption and '$3.00' in caption and 'ONE parlay' in caption
    text=stored_texts()[0]
    with tg.ledger() as c:
        receipts=c.execute("select status,message_id from deliveries where delivery_key like 'ai-ticket-review-v3:%' or delivery_key like 'ticket-image:%' order by rowid").fetchall()
    assert [tuple(r) for r in receipts]==[('sent',1),('sent',1)]   # text and image receipts name the same Telegram message
    assert 'Ticket 1:' in text and 'Combine ALL' in text and 'NBA' in text and 'NHL' in text and '$3.00' in text
    archive=json.loads(next((path/'reports/tickets').glob('*.json')).read_text())
    assert archive['text']==text
    with tg.ledger() as c:
        assert c.execute('select count(*) from ticket_items').fetchone()[0]==1
        with pytest.raises(Exception,match='immutable'):c.execute("update ticket_batches set day='other'")


def test_cross_batch_shared_core_disclosed_without_daily_staking_cap(setup):
    plan,snap,sent,path=setup
    plan['tickets']=[ticket([('nba',1),('nhl',2)],3)];assert t.send(t.check(plan))=='sent'
    plan['tickets']=[ticket([('nba',1),('nfl',3)])];assert t.send(t.check(plan))=='sent'
    assert 'Shared core exposure' in stored_texts()[-1] and 'shared across 2 tickets' in stored_texts()[-1]
    plan['tickets']=[ticket([('nba',1),('nhl',2)])]
    with pytest.raises(ValueError,match='preserve'):t.send(t.check(plan))
    plan['tickets']=[ticket([('nba',4),('nfl',5)],3)]
    assert t.send(t.check(plan))=='sent'
    plan['tickets']=[ticket([('nhl',4),('nfl',5)])];assert t.send(t.check(plan))=='sent'
    plan['tickets']=[ticket([('nhl',1),('nfl',2)])]
    assert t.send(t.check(plan))=='sent'


def test_same_game_changed_forecast_across_batches_refused(setup):
    plan,snap,sent,path=setup
    plan['tickets']=[ticket([('nba',1),('nhl',2)],3)];assert t.send(t.check(plan))=='sent'
    snap['sports']['nba']['report']['picks'][3]['game_id']='g1'
    path.joinpath('snapshot.json').write_text(json.dumps(snap));plan['review']['snapshot_sha256']=t.sha(path.joinpath('snapshot.json').read_bytes())
    plan['tickets']=[ticket([('nba',4),('nhl',5)])]
    with pytest.raises(ValueError,match='identical forecast'):t.send(t.check(plan))


@pytest.mark.parametrize('problem', ['stale','started','market','comparison','same_game'])
def test_guards(setup,problem):
    plan,snap,sent,path=setup;plan['tickets']=[ticket([('nba',1),('nhl',2)],3)]
    if problem=='stale':snap['sports']['nhl']['freshness']['source_state']='stale'
    if problem=='started':snap['sports']['nba']['report']['picks'][0]['tipoff']=NOW
    if problem=='market':plan['review']['markets_reviewed']={}
    if problem=='comparison':plan['review']['combination_comparison']=''
    if problem=='same_game':plan['tickets']=[ticket([('nba',1),('nba',1)],3)]
    with pytest.raises(ValueError):t.check(plan)
    assert sent==[]


def test_uncertain_keeps_original_record_and_never_resends(setup,monkeypatch):
    plan,snap,sent,path=setup;plan['tickets']=[ticket([('nba',1),('nhl',2)],3)]
    def fail(*a,**kw):raise tg.DeliveryError('uncertain',uncertain=True)
    monkeypatch.setattr(tg.Telegram,'call',fail)
    result=t.check(plan);assert t.send(result)=='uncertain';assert t.send(result)=='existing_uncertain'
    assert sent==[]
    with tg.ledger() as c:
        assert c.execute("select status from deliveries").fetchone()[0]=='uncertain'


def test_settlement_preserves_loss_all_legs_and_appends_correction_once(setup,monkeypatch):
    plan,snap,sent,path=setup;plan['tickets']=[ticket([('nba',1),('nhl',2)],3)];t.send(t.check(plan))
    grades={1:'loss',2:'pending'}
    monkeypatch.setattr(t,'grade_leg',lambda leg,now:dict(forecast_id=leg['forecast_id'],grade=grades[leg['forecast_id']]))
    first=t.settle(now=NOW);assert first['tickets'][0]['grade']=='loss';assert len(first['tickets'][0]['legs'])==2
    t.settle(now=NOW)
    with tg.ledger() as c:assert c.execute('select count(*) from ticket_grades').fetchone()[0]==1
    grades.update({1:'win',2:'win'});assert t.settle(now=NOW)['tickets'][0]['grade']=='win'
    with tg.ledger() as c:assert c.execute('select count(*) from ticket_grades').fetchone()[0]==2


def test_legacy_records_preserved_without_blocking_new_combinations(setup):
    plan,snap,sent,path=setup;plan['tickets']=[ticket([('nba',1),('nhl',2)],3)];result=t.check(plan)
    identity=sorted((p['sport'],p['forecast_id']) for x in result['resolved'] for p in x['legs'])
    key='ai-ticket-review-v2:2026-11-08:fixture:'+t.sha(json.dumps(identity).encode())
    archive=path/'reports/tickets';archive.mkdir(parents=True,exist_ok=True);(archive/'2026-11-08-legacy.json').write_text(json.dumps(result))
    with tg.ledger() as c:c.execute('insert into deliveries(delivery_key,chat_id,text,status,attempted_at) values(?,?,?,?,?)',(key,'fixture',result['text'],'uncertain',NOW))
    plan['tickets']=[ticket([('nba',3),('nhl',4)],3)]
    assert t.send(t.check(plan))=='sent'


def test_personal_slip_is_separate_idempotent_and_profit_uses_actual_return(setup):
    plan,snap,sent,path=setup;slip=path/'slip.json'
    slip.write_text(json.dumps(dict(slip_id='user-1',source='user screenshot',stake_usd=5,return_usd=18.07,legs=[dict(sport='nba',pick_label='ATL win')])) )
    assert t.record_personal(slip)['profit_usd']==13.07;t.record_personal(slip)
    with tg.ledger() as c:
        assert c.execute('select count(*) from personal_tickets').fetchone()[0]==1
        assert c.execute('select count(*) from ticket_batches').fetchone()[0]==0


def test_soccer_gap_not_fabricated_and_source_alerts(setup):
    plan,snap,sent,path=setup;snap['sports']['nhl']['freshness']['source_state']='stale'
    assert t.health_alerts(snap)==[dict(sport='nhl',issue='source_unavailable_or_stale')]


def test_dispatch_rechecks_freshness(setup):
    plan,snap,sent,path=setup;plan['tickets']=[ticket([('nba',1),('nhl',2)],3)];result=t.check(plan)
    snap['sports']['nhl']['freshness']['source_state']='stale'
    with pytest.raises(ValueError,match='stale'):t.send(result)
    assert sent==[]


def test_pending_push_void_and_uncertainty_ticket_settlement():
    assert t.ticket_grade([{'grade':'win'},{'grade':'push'}])=='win'
    assert t.ticket_grade([{'grade':'void'},{'grade':'push'}])=='void'
    assert t.ticket_grade([{'grade':'win'},{'grade':'unresolved'}])=='pending'
    assert t.ticket_grade([{'grade':'loss'},{'grade':'unavailable'}])=='loss'


@pytest.mark.parametrize('status,available,expected',[('final','2026-11-08T19:00:00Z','loss'),('final','2026-11-09T19:00:00Z','pending'),('canceled',None,'void')])
def test_grade_immutable_forecast_at_original_spread(monkeypatch,status,available,expected):
    import sqlite3
    from app import db
    c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
    c.executescript('''CREATE TABLE game_forecasts(forecast_id,game_id,market,line,p_a,p_mid,p_b,tipoff_at);
    CREATE TABLE games(game_id,status,home_score,away_score,tipoff_at,result_available_at);
    INSERT INTO game_forecasts VALUES(1,'g','spread',-3.5,.75,0,.25,'2026-11-08T18:00:00Z');
    INSERT INTO game_forecasts VALUES(2,'g','spread',1.5,.75,0,.25,'2026-11-08T18:00:00Z');''')
    c.execute('INSERT INTO games VALUES(?,?,?,?,?,?)',('g',status,102,100,'2026-11-08T18:00:00Z',available))
    monkeypatch.setattr(db,'connect_readonly',lambda sport:c)
    result=t.grade_leg(dict(sport='nba',forecast_id=1,game_id='g',market='spread',pick_label='HOME -3.5'),'2026-11-08T19:30:00Z')
    assert result['grade']==expected and result['line']==-3.5


def test_updated_history_timestamp_does_not_change_reviewed_selection(setup):
    plan,snap,sent,path=setup;plan['tickets']=[ticket([('nba',1),('nhl',2)],3)];result=t.check(plan)
    snap['as_of']='2026-11-08T17:00:05Z'
    snap['sports']['nba']['report']['picks'][0]['percentage_history']={'as_of':snap['as_of']}
    assert t.send(result)=='sent'
    assert t.send(t.check(plan))=='existing_sent'


def test_settlement_notifications_deduplicated_and_leg_failure_visible(setup,monkeypatch):
    plan,snap,sent,path=setup;plan['tickets']=[ticket([('nba',1),('nhl',2)],3)];t.send(t.check(plan))
    monkeypatch.setattr(t,'grade_leg',lambda p,now:dict(forecast_id=p['forecast_id'],pick_label=p['pick_label'],grade='win'))
    t.settle(now=NOW,notify=True);t.settle(now=NOW,notify=True)
    assert len(sent)==2 and '\n' in sent[1][1]['text'] and '\\n' not in sent[1][1]['text']
    def unavailable(*a):raise FileNotFoundError('store missing')
    monkeypatch.setattr(t,'grade_leg',unavailable)
    report=t.settle(now=NOW);assert report['errors'] and report['tickets'][0]['grade']=='pending'


def test_preflight_settlement_always_precedes_empty_slate(monkeypatch,capsys):
    import sys
    calls=[]
    monkeypatch.setattr(t,'settle',lambda **kw:calls.append('settle') or {'errors':[]})
    monkeypatch.setattr(t,'snapshot',lambda:calls.append('snapshot') or {'sports':{}})
    monkeypatch.setattr(sys,'argv',['tickets','preflight'])
    assert t.main()==0 and calls==['settle','snapshot']


def test_health_alerts_are_deduplicated_and_delivery_only_failures_alert(setup):
    plan,snap,sent,path=setup
    snap['alerts']=[dict(sport='nhl',issue='source_unavailable_or_stale')]
    # Owner request Nov 9: stale-source and missing-forecast states are no longer sent to the chat.
    assert t.notify_alerts(snap,[])==['not_sent_quiet'] and t.notify_alerts(snap,[])==['not_sent_quiet'] and sent==[]
    snap['alerts']=[dict(sport='nhl',issue='Store identity mismatch')]
    t.notify_alerts(snap,[]);t.notify_alerts(snap,[]);assert len(sent)==1   # any other issue: one alert, deduplicated
    with tg.ledger() as c:
        c.execute('insert into deliveries(delivery_key,chat_id,text,status,attempted_at) values(?,?,?,?,?)',('ai-ticket-review-v3:uncertain-fixture','fixture','fixture','uncertain',NOW))
    snap['alerts']=[];t.notify_alerts(snap,[]);assert len(sent)==2


def test_shared_card_does_not_claim_cross_ticket_distinctness(setup,monkeypatch):
    from PIL import ImageDraw
    from app import telegram_card
    plan,snap,sent,path=setup;plan['tickets']=[ticket([('nba',1),('nhl',2)],3),ticket([('nba',1),('nfl',3)])]
    texts=[];original=ImageDraw.ImageDraw.text
    def capture(self,xy,text,*a,**kw):texts.append(text);return original(self,xy,text,*a,**kw)
    monkeypatch.setattr(ImageDraw.ImageDraw,'text',capture);monkeypatch.setattr(telegram_card,'_logo',lambda *a:None)
    png=telegram_card.render_ticket_card(snap['date'],t.check(plan)['resolved'])
    assert png.startswith(b'\x89PNG')
    assert any('Shared core picks' in v for v in texts)
    assert not any('Distinct games across tickets' in v for v in texts)


def test_prior_tickets_still_require_full_review_and_archive_no_ticket_plan(setup):
    plan,snap,sent,path=setup
    plan['tickets']=[ticket([('nba',1),('nhl',2)],3),ticket([('nba',1),('nfl',3)]),ticket([('nba',4),('nhl',5)])]
    assert t.send(t.check(plan))=='sent'
    plan['tickets']=[]
    del plan['review']['forecasts_reviewed']['nhl:4']
    with pytest.raises(ValueError,match='Every available'):t.check(plan)
    plan['review']['forecasts_reviewed']['nhl:4']=dict(decision='excluded',reason='Reviewed later alternative; no new ticket justified')
    result=t.check(plan);assert t.send(result)=='no_ticket'
    assert any(not json.loads(p.read_text())['resolved'] for p in (path/'reports/tickets/reviews').glob('*.json'))
    assert len(sent)==1


def test_newly_published_forecast_requires_re_review(setup):
    plan,snap,sent,path=setup
    new=copy.deepcopy(snap['sports']['nba']['report']['picks'][0]);new.update(forecast_id=99,game_id='new-game')
    snap['sports']['nba']['report']['picks'].append(new)
    with pytest.raises(ValueError,match='newly published'):t.check(plan)


@pytest.mark.parametrize('retry_after',[60,120])
def test_retry_uses_current_attempt_time_and_preserves_payload(setup,monkeypatch,retry_after):
    plan,snap,sent,path=setup;plan['tickets']=[ticket([('nba',1),('nhl',2)],3)]
    original=tg.Telegram.call; attempts=[]
    def flaky(self,method,payload,**kw):
        attempts.append(payload)
        if len(attempts)==1:raise tg.DeliveryError('connect/429',retry_after=retry_after)
        return original(self,method,payload,**kw)
    monkeypatch.setattr(tg.Telegram,'call',flaky)
    assert t.send(t.check(plan))=='retry'
    snap['as_of']='2026-11-08T17:03:00Z'
    assert t.send(t.check(plan))=='sent'
    assert attempts[0]==attempts[1]


def test_settlement_completes_before_notification_network_failure(setup,monkeypatch):
    plan,snap,sent,path=setup;plan['tickets']=[ticket([('nba',1),('nhl',2)],3)];t.send(t.check(plan))
    monkeypatch.setattr(t,'grade_leg',lambda p,now:dict(forecast_id=p['forecast_id'],pick_label=p['pick_label'],grade='win'))
    def offline(self):raise tg.DeliveryError('offline',retry_after=60)
    monkeypatch.setattr(tg.Telegram,'identity',offline)
    report=t.settle(now=NOW,notify=True)
    assert report['tickets'][0]['grade']=='win' and report['notification_errors']
    with tg.ledger() as c:assert c.execute('select count(*) from ticket_grades').fetchone()[0]==1


def test_delivery_alert_scope_toronto_day_and_retry_status(setup):
    plan,snap,sent,path=setup;snap['alerts']=[]
    with tg.ledger() as c:
        for key,stamp,status in [('game-card','2026-11-08T17:00:00Z','failed'),('ai-ticket-review-v3:old','2026-11-08T02:00:00Z','uncertain')]:
            c.execute('insert into deliveries(delivery_key,chat_id,text,status,attempted_at) values(?,?,?,?,?)',(key,'fixture','test',status,stamp))
    assert t.notify_alerts(snap,[])==[]
    with tg.ledger() as c:c.execute('insert into deliveries(delivery_key,chat_id,text,status,attempted_at) values(?,?,?,?,?)',('ai-ticket-review-v3:new','fixture','test','retry',NOW))
    t.notify_alerts(snap,[]);assert len(sent)==1
    with tg.ledger() as c:c.execute('insert into deliveries(delivery_key,chat_id,text,status,attempted_at) values(?,?,?,?,?)',('ai-ticket-review-v3:later','fixture','test','failed',NOW))
    t.notify_alerts(snap,[]);assert len(sent)==2


def test_ledger_follows_isolated_data_directory(tmp_path,monkeypatch):
    monkeypatch.setenv('NBA_DATA_DIR',str(tmp_path))
    with tg.ledger() as c:
        assert Path(c.execute('pragma database_list').fetchone()[2]).parent==tmp_path


def test_production_sends_one_picture_with_archived_png(setup,monkeypatch):
    from app import telegram_card
    monkeypatch.setattr(telegram_card,'_logo',lambda *a:None)
    plan,snap,sent,path=setup;plan['tickets']=[ticket([('nba',1),('nhl',2)],3)]
    assert t.send(t.check(plan))=='sent'
    assert [m for m,p in sent]==['sendPhoto']
    assert len(sent[-1][1]['caption'])<=1024
    assert list((path/'reports/tickets').glob('*.png'))


def test_visual_uncertain_does_not_resend_text_or_image(setup,monkeypatch):
    plan,snap,sent,path=setup;plan['tickets']=[ticket([('nba',1),('nhl',2)],3)]
    original=tg.Telegram.call;photos=[]
    def flaky(self,method,payload,**kw):
        if method=='sendPhoto':
            photos.append(kw['photo']);raise tg.DeliveryError('uncertain image',uncertain=True)
        return original(self,method,payload,**kw)
    monkeypatch.setattr(tg.Telegram,'call',flaky)
    assert t.send(t.check(plan))=='uncertain'
    assert t.send(t.check(plan))=='existing_uncertain'
    assert len(photos)==1 and sent==[]   # the one picture is never sent a second time, and no text is sent in its place


def test_historical_visual_preview_is_labelled_and_never_reserves(setup,monkeypatch):
    plan,snap,sent,path=setup
    value=dict(date='2026-11-07',resolved=[dict(label='Mixed NBA + NHL',stake_usd=3,legs=[
        {**snap['sports'][s]['report']['picks'][i-1],'sport':s,'tipoff':'2026-11-07T18:00:00Z'} for s,i in [('nba',1),('nhl',2)]])])
    file=path/'past.json';file.write_text(json.dumps(value))
    monkeypatch.setattr(t,'grade_leg',lambda *a:dict(grade='win',home_score=100,away_score=90))
    photo,caption=t.preview_payload(file,now=NOW);assert photo.startswith(b'\x89PNG') and 'NOT A LIVE TICKET' in caption
    assert t.send_preview(file)=='sent'
    assert len(sent)==1 and sent[0][0]=='sendPhoto'
    with tg.ledger() as c:assert not c.execute("select 1 from sqlite_master where name='ticket_batches'").fetchone()
    value['resolved'][0]['legs'][0]['tipoff']='2026-11-08T18:00:00Z';file.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='past games'):t.preview_payload(file,now=NOW)


def test_cli_retry_status_is_a_failure_exit(setup,monkeypatch,capsys):
    import sys
    plan,snap,sent,path=setup;plan['tickets']=[ticket([('nba',1),('nhl',2)],3)]
    file=path/'plan.json';file.write_text(json.dumps(plan))
    monkeypatch.setattr(sys,'argv',['tickets','send','--plan',str(file)])
    monkeypatch.setattr(t,'send',lambda result:'retry')
    assert t.main()==1
    assert json.loads(capsys.readouterr().out)['status']=='retry'


def test_available_forecast_count_never_claims_all_sports_when_nfl_inactive(setup):
    plan,snap,sent,path=setup
    snap['sports']['nfl']={'error':'inactive'}
    result=t.check(plan)
    assert result['coverage']['complete_supported_sports'] is False
    assert result['coverage']['unavailable_sports']=={'nfl':'inactive'}


def test_tickets_without_stakes_send_text_and_visual_after_prior_allocations(setup):
    plan,snap,sent,path=setup
    plan['tickets']=[ticket([('nba',1),('nhl',2)],10)]
    assert t.send(t.check(plan))=='sent'
    new=ticket([('nba',3),('nhl',4)])
    del new['stake_usd']
    plan['tickets']=[new]
    assert t.send(t.check(plan))=='sent'
    text=stored_texts()[-1]
    assert 'Combine ALL' in text and 'Suggested stake' not in text and 'Daily maximum' not in text
    assert 'stake' not in sent[-1][1]['caption'].lower()
    with tg.ledger() as c:
        rows=c.execute('select payload from ticket_batches').fetchall()
        assert len(rows)==2
        assert json.loads(rows[-1][0])['resolved'][0]['stake_usd'] is None


# --- Card/ticket gap repair (Nov 8): prompt review trigger, explicit outcomes, explicit exclusions ---

def plan_file(plan,path,name='plan.json'):
    file=path/name;file.write_text(json.dumps(plan));return file


def run_cli(monkeypatch,capsys,*args):
    import sys
    monkeypatch.setattr(sys,'argv',['tickets',*args])
    code=t.main();return code,capsys.readouterr().out


def test_review_due_when_new_forecasts_span_two_games_and_clears_after_review(setup):
    plan,snap,sent,path=setup
    state=t.review_state(snap)
    assert state['due'] is True and state['reason']=='new_forecasts'
    assert len(state['unreviewed'])==15 and state['ticketable_games']==15
    assert t.check(plan)['resolved']==[]          # complete tickets=[] review archived
    state=t.review_state(snap)
    assert state['due'] is False and state['reason']=='no_new_forecasts' and state['unreviewed']==[]
    new=copy.deepcopy(snap['sports']['nba']['report']['picks'][0]);new.update(forecast_id=99,game_id='new-game')
    snap['sports']['nba']['report']['picks'].append(new)
    state=t.review_state(snap)
    assert state['due'] is True and [u['key'] for u in state['unreviewed']]==['nba:99']


def test_single_game_or_stale_only_forecasts_do_not_trigger_a_review_but_are_explained(setup):
    plan,snap,sent,path=setup
    for sport in ('nhl','nfl'):snap['sports'][sport]['report']['picks']=[]
    snap['sports']['nba']['report']['picks']=[p for p in snap['sports']['nba']['report']['picks'] if p['game_id']=='g1']
    state=t.review_state(snap)
    assert state['due'] is False and state['reason']=='single_game_window' and [u['key'] for u in state['unreviewed']]==['nba:1']
    snap['sports']['nhl']['report']['picks']=[dict(snap['sports']['nba']['report']['picks'][0],forecast_id=7,game_id='h7')]
    snap['sports']['nhl']['freshness']['source_state']='stale'
    state=t.review_state(snap)
    assert state['due'] is False and state['blocked']['nhl']['issue']=='source_unavailable_or_stale' and state['blocked']['nhl']['forecasts']==1


def test_inactive_sport_and_games_started_without_review_are_reported(setup):
    plan,snap,sent,path=setup
    snap['sports']['nfl']={'error':'inactive','reason':'definition_mismatch','unserved_forecasts':5,'unserved_games':1}
    snap['sports']['nhl']['report']['picks'][0].update(status='in_progress',tipoff='2026-11-08T16:50:00Z',generated_at='2026-11-08T15:00:00Z')
    state=t.review_state(snap)
    assert state['unavailable']['nfl']['reason']=='definition_mismatch' and state['unavailable']['nfl']['unserved_forecasts']==5
    assert [m['game_id'] for m in state['missed_before_start'] if m['sport']=='nhl']==['g1']


def test_prior_ticket_game_lock_is_visible_before_selection(setup):
    plan,snap,sent,path=setup;plan['tickets']=[ticket([('nba',1),('nhl',2)])]
    assert t.send(t.check(plan))=='sent'
    snap['sports']['nba']['report']['picks'][3]['game_id']='g1'      # newer nba version of a game already used
    locked={(x['sport'],x['game_id']):x for x in t.review_state(snap)['locked_games']}
    assert locked[('nba','g1')]['ticket_forecast_id']==1 and locked[('nba','g1')]['blocked_forecasts']==['nba:4']


def test_preflight_prints_review_trigger_state(setup,monkeypatch,capsys):
    plan,snap,sent,path=setup
    monkeypatch.setattr(t,'settle',lambda **kw:{'errors':[],'tickets':[]})
    code,out=run_cli(monkeypatch,capsys,'preflight')
    report=json.loads(out);assert code==0 and report['review']['due'] is True and report['review']['reason']=='new_forecasts'


def test_each_review_records_ticket_no_ticket_or_operational_failure(setup,monkeypatch,capsys):
    plan,snap,sent,path=setup
    code,out=run_cli(monkeypatch,capsys,'check','--plan',str(plan_file(plan,path)))
    assert code==0 and 'no_ticket' in out
    bad=copy.deepcopy(plan);bad['review']['combination_comparison']=''
    code,out=run_cli(monkeypatch,capsys,'check','--plan',str(plan_file(bad,path,'bad.json')))
    assert code==1
    plan['tickets']=[ticket([('nba',1),('nhl',2)])]
    code,out=run_cli(monkeypatch,capsys,'send','--plan',str(plan_file(plan,path,'send.json')))
    assert code==0 and json.loads(out)['status']=='sent'
    monkeypatch.setattr(tg,'controls',lambda *a:False)
    plan['tickets']=[ticket([('nba',3),('nhl',4)])]
    code,out=run_cli(monkeypatch,capsys,'send','--plan',str(plan_file(plan,path,'paused.json')))
    assert code==1
    with tg.ledger() as c:
        rows=c.execute('select stage,outcome,detail,payload from ticket_reviews order by review_id').fetchall()
        assert [(r['stage'],r['outcome']) for r in rows]==[('check','no_ticket'),('check','operational_failure'),
                                                          ('send','ticket_sent'),('send','paused')]
        assert 'Compare' in rows[1]['detail'] or 'Compare' in rows[1]['detail'].title()
        assert json.loads(rows[0]['payload'])['coverage']['required']==15
        assert json.loads(rows[2]['payload'])['ticket_ids']
        with pytest.raises(Exception,match='append-only'):c.execute("update ticket_reviews set outcome='no_ticket'")
        with pytest.raises(Exception,match='append-only'):c.execute('delete from ticket_reviews')


def test_status_is_read_only_and_shows_outcomes_and_receipts(setup,monkeypatch,capsys):
    plan,snap,sent,path=setup;plan['tickets']=[ticket([('nba',1),('nhl',2)])]
    run_cli(monkeypatch,capsys,'send','--plan',str(plan_file(plan,path)))
    def no_network(*a,**kw):raise AssertionError('status must not call Telegram')
    monkeypatch.setattr(tg,'Telegram',no_network)
    code,out=run_cli(monkeypatch,capsys,'status')
    report=json.loads(out)
    assert code==0 and report['outcomes'][-1]['outcome']=='ticket_sent'
    assert {r['kind'] for r in report['ticket_deliveries']}=={'ticket_text','ticket_image'}
    assert report['review']['due'] is False and report['review']['reason']=='no_new_forecasts'
    assert [x['game_id'] for x in report['review']['locked_games']]==['g1','g2']


def test_shared_exposure_lists_only_this_batch_with_readable_labels(setup):
    plan,snap,sent,path=setup
    plan['tickets']=[ticket([('nba',1),('nhl',2)]),ticket([('nba',1),('nfl',3)])];assert t.send(t.check(plan))=='sent'
    plan['tickets']=[ticket([('nba',4),('nhl',5)])];assert t.send(t.check(plan))=='sent'
    assert 'Shared core exposure' not in stored_texts()[-1] and 'nba:1' not in stored_texts()[-1]
    plan['tickets']=[ticket([('nba',1),('nhl',3)])];assert t.send(t.check(plan))=='sent'
    text=stored_texts()[-1];assert 'B1 win' in text and 'shared across 3 tickets' in text and 'nba:1' not in text


def test_ticket_messages_are_labelled_as_combined_parlays_not_game_cards(setup):
    plan,snap,sent,path=setup;plan['tickets']=[ticket([('nba',1),('nhl',2)])]
    assert t.send(t.check(plan))=='sent'
    assert 'COMBINED PARLAY TICKET' in stored_texts()[0] and 'not a single-game card' in stored_texts()[0].lower()
    assert 'COMBINED PARLAY TICKET' in sent[0][1]['caption'] and len(sent)==1


def test_nfl_inactive_detail_names_cause_and_counts_unserved_forecasts(tmp_path,monkeypatch):
    import sqlite3
    from app import nfl_live
    monkeypatch.setenv('NBA_DATA_DIR',str(tmp_path))
    c=sqlite3.connect(tmp_path/'nfl.sqlite')
    c.executescript("""CREATE TABLE game_forecasts(forecast_id INTEGER PRIMARY KEY,game_id,status,tipoff_at,generated_at);
      INSERT INTO game_forecasts VALUES(1,'n1','forecast','2026-11-08T18:00:00Z','2026-11-08T16:00:00Z');
      INSERT INTO game_forecasts VALUES(2,'n1','forecast','2026-11-08T18:00:00Z','2026-11-08T16:00:00Z');
      INSERT INTO game_forecasts VALUES(3,'n2','forecast','2026-11-08T23:00:00Z','2026-11-08T16:00:00Z');""");c.commit();c.close()
    monkeypatch.setattr(t,'_nfl_store',lambda:sqlite3.connect(f"file:{tmp_path/'nfl.sqlite'}?mode=ro",uri=True))
    detail=t.nfl_inactive_detail(NOW)
    assert detail=={'error':'inactive','reason':'missing_activation','unserved_forecasts':2,'unserved_games':1}
    nfl_live.activation_path().parent.mkdir(parents=True);nfl_live.activation_path().write_text(json.dumps({'definition':'old','activated_at':NOW}))
    assert t.nfl_inactive_detail(NOW)['reason']=='definition_mismatch'


@pytest.mark.parametrize('failure,retrigger',[('refused',True),('retry',True),('uncertain',False),('paused',False)])
def test_retryable_dispatch_failure_re_triggers_review_but_uncertain_and_paused_do_not(setup,monkeypatch,capsys,failure,retrigger):
    plan,snap,sent,path=setup;plan['tickets']=[ticket([('nba',1),('nhl',2)])]
    def dispatch(result):
        if failure=='refused':raise ValueError('Selected source unavailable or stale')
        return failure
    monkeypatch.setattr(t,'send',dispatch)
    code,out=run_cli(monkeypatch,capsys,'send','--plan',str(plan_file(plan,path)))
    assert code==1 and list((path/'reports/tickets/reviews').glob('*.json'))   # validation passed and archived
    state=t.review_state(snap)
    assert state['due'] is retrigger and state['reason']==('new_forecasts' if retrigger else 'no_new_forecasts')


def test_brief_preflight_is_compact_but_keeps_trigger_settlement_and_alerts(setup,monkeypatch,capsys):
    plan,snap,sent,path=setup
    snap['alerts']=[dict(sport='nhl',issue='source_unavailable_or_stale')]
    monkeypatch.setattr(t,'settle',lambda **kw:{'as_of':NOW,'errors':[],'notification_errors':[],
                                                 'tickets':[dict(ticket_id='T1',grade='win',legs=[{'x':1}]*3,notification='sent')]})
    code,out=run_cli(monkeypatch,capsys,'preflight','--brief')
    report=json.loads(out)
    assert code==0 and 'snapshot' not in report and report['review']['due'] is True
    assert report['settlement']==[dict(ticket_id='T1',grade='win',notification='sent')]
    assert report['snapshot_alerts']==snap['alerts'] and len(out)<len(json.dumps(snap))


# --- Varied, intuitive menu (owner request Nov 8): up to 6 options of 2-6 legs, bet type on every leg ---

def mixed_markets(snap):
    kinds=['winner','spread','total','team_total_home','winner']
    for sport in ('nba','nhl','nfl'):
        for p,kind in zip(snap['sports'][sport]['report']['picks'],kinds):
            p['market']=kind
    return snap


def test_varied_menu_sends_options_of_different_sizes_with_bet_types(setup):
    plan,snap,sent,path=setup;mixed_markets(snap)
    path.joinpath('snapshot.json').write_text(json.dumps(snap));plan['review']['snapshot_sha256']=t.sha(path.joinpath('snapshot.json').read_bytes())
    plan['review']['markets_reviewed']={s:{m:'Reviewed' for m in ('winner','spread','total','team_total_home')} for s in ('nba','nhl','nfl')}
    plan['tickets']=[ticket([('nba',1),('nhl',2)]),ticket([('nba',2),('nhl',3),('nfl',4)]),
                     ticket([('nba',3),('nhl',4),('nfl',5),('nba',5)]),ticket([('nhl',1),('nfl',1),('nfl',2),('nba',4),('nhl',5)])]
    for i,(x,label) in enumerate(zip(plan['tickets'],['Safer pair','Mixed markets','Four-leg mix','Bigger five-leg'])):
        x['label']=label;del x['stake_usd']
    assert t.send(t.check(plan))=='sent'
    text=stored_texts()[0]
    assert '4 options:' in text and '#1 2 legs' in text and '#4 5 legs' in text
    assert 'Moneyline' in text and 'Puck line' in text and 'Over/Under' in text and 'Team total' in text and 'Spread' in text
    assert 'Ticket 4: Bigger five-leg' in text and len(text)<=3900
    assert [m for m,p in sent]==['sendPhoto','sendPhoto']   # 2+3+4 legs on the first picture, the five-leg ticket on its own
    first,second=sent[0][1]['caption'],sent[1][1]['caption']
    assert 'picture 1 of 2' in first and '#3 Four-leg mix' in first and '#4' not in first
    assert 'picture 2 of 2' in second and '#4 Bigger five-leg' in second and second.count(' + ')==4 and max(len(first),len(second))<=1024


@pytest.mark.parametrize('tickets,message',[
    ([[('nba',i),('nhl',i)] for i in range(1,6)]+[[('nba',1),('nfl',1)],[('nba',2),('nfl',2)]],'six'),
    ([[('nba',1),('nhl',1),('nfl',1),('nba',2),('nhl',2),('nfl',2),('nba',3),('nhl',3)]],'two to seven'),
    ([[('nba',1)]],'two to seven')])
def test_menu_caps_are_six_options_of_two_to_seven_legs(setup,tickets,message):
    plan,snap,sent,path=setup
    plan['tickets']=[ticket(legs) for legs in tickets]
    with pytest.raises(ValueError,match=message):t.check(plan)
    assert sent==[]


def test_six_option_menu_within_caps_is_accepted(setup):
    plan,snap,sent,path=setup
    plan['tickets']=[ticket([('nba',i),('nhl',i)]) for i in range(1,6)]+[ticket([('nba',1),('nhl',2),('nfl',3),('nba',4),('nhl',5),('nfl',1)])]
    result=t.check(plan);assert len(result['resolved'])==6 and '6 options:' in result['text']


def test_ticket_card_renders_six_leg_tickets_and_points_to_extra_options(setup,monkeypatch):
    from PIL import ImageDraw
    from app import telegram_card
    plan,snap,sent,path=setup
    plan['tickets']=[ticket([('nba',1),('nhl',1),('nfl',1),('nba',2),('nhl',2),('nfl',2)]) for _ in range(1)]+\
                    [ticket([('nba',i),('nhl',i)]) for i in range(3,6)]
    texts=[];original=ImageDraw.ImageDraw.text
    def capture(self,xy,text,*a,**kw):texts.append(text);return original(self,xy,text,*a,**kw)
    monkeypatch.setattr(ImageDraw.ImageDraw,'text',capture)
    png=telegram_card.render_ticket_card(snap['date'],t.check(plan)['resolved'])
    assert png.startswith(b'\x89PNG') and any('+1 more option' in v for v in texts)
    assert any('Moneyline' in v for v in texts)


# Nov 8 evening: the release tick refreshes, then runs a 5-48 min forecast job under the store lock; meanwhile the source
# turns "stale" although nothing failed, and every NBA/NHL leg was refused (7 PM NBA games never reached a review).
def delayed(snap, sport, fetched='2026-11-08T16:40:00Z'):
    snap['sports'][sport]['freshness'].update(source_state='stale', failing=False, source_fetched_at=fetched)
    snap['sports'][sport]['refresh_delay'] = dict(job='near_tipoff', started_at=fetched, source_fetched_at=fetched)


def test_source_stale_only_because_its_forecast_job_is_running_still_serves_tickets_with_disclosure(setup):
    plan,snap,sent,path=setup;delayed(snap,'nba');sp=Path(plan['snapshot_path']);sp.write_text(json.dumps(snap))
    plan['review']['snapshot_sha256']=t.sha(sp.read_bytes())
    state=t.review_state(snap)
    assert 'nba' not in state['blocked'] and state['delayed']['nba']['job']=='near_tipoff' and state['delayed']['nba']['forecasts']==5
    plan['tickets']=[dict(label='Mixed',payout_assessment='Fixture payout judgment',legs=[dict(sport='nba',forecast_id=1,rationale='r'),dict(sport='nhl',forecast_id=2,rationale='r')])]
    checked=t.check(plan)
    assert 'NBA game status last refreshed' in checked['text'] and 'forecast job still running' in checked['text']
    assert t.send(checked)=='sent' and 'NBA game status last refreshed' in stored_texts()[0]


@pytest.mark.parametrize('problem',['failing','no_running_job','outside_window'])
def test_delayed_source_is_refused_when_failing_unexplained_or_older_than_the_games_window(setup,problem):
    plan,snap,sent,path=setup;delayed(snap,'nba')
    if problem=='failing':snap['sports']['nba']['freshness']['failing']=True
    if problem=='no_running_job':del snap['sports']['nba']['refresh_delay']
    if problem=='outside_window':delayed(snap,'nba',fetched='2026-11-08T15:30:00Z')   # games start 18:00: refreshed before their 2-h window
    sp=Path(plan['snapshot_path']);sp.write_text(json.dumps(snap));plan['review']['snapshot_sha256']=t.sha(sp.read_bytes())
    assert t.review_state(snap)['blocked']['nba']['issue']=='source_unavailable_or_stale'
    plan['tickets']=[dict(label='Mixed',payout_assessment='Fixture payout judgment',legs=[dict(sport='nba',forecast_id=1,rationale='r'),dict(sport='nhl',forecast_id=2,rationale='r')])]
    with pytest.raises(ValueError,match='stale'):t.check(plan)
    assert sent==[]


def test_refresh_delay_detects_only_a_job_running_since_the_last_successful_refresh():
    import sqlite3
    conn=sqlite3.connect(':memory:');conn.row_factory=sqlite3.Row
    conn.execute('CREATE TABLE job_runs (run_id INTEGER PRIMARY KEY, job TEXT, started_at TEXT, finished_at TEXT, status TEXT, detail TEXT)')
    conn.execute("INSERT INTO job_runs (job,started_at,status) VALUES ('near_tipoff','2026-11-04T22:27:50Z','running')")   # crashed long ago
    stale=dict(source_state='stale',failing=False,source_fetched_at='2026-11-08T22:50:10Z')
    assert t.refresh_delay(conn,stale) is None
    conn.execute("INSERT INTO job_runs (job,started_at,status) VALUES ('near_tipoff','2026-11-08T22:50:11Z','running')")
    assert t.refresh_delay(conn,stale)==dict(job='near_tipoff',started_at='2026-11-08T22:50:11Z',source_fetched_at='2026-11-08T22:50:10Z')
    assert t.refresh_delay(conn,dict(stale,failing=True)) is None and t.refresh_delay(conn,dict(stale,source_state='fresh')) is None


def test_forecast_excluded_while_its_source_was_stale_is_re_reviewed_once_usable(setup):
    plan,snap,sent,path=setup;snap['sports']['nba']['freshness']['source_state']='stale'
    sp=Path(plan['snapshot_path']);sp.write_text(json.dumps(snap));plan['review']['snapshot_sha256']=t.sha(sp.read_bytes())
    assert t.check(plan)['resolved']==[]                  # complete review; NBA accounted but could not be a leg
    state=t.review_state(snap)
    assert state['due'] is False and state['reason']=='no_new_forecasts' and state['blocked']['nba']['forecasts']==5
    snap['sports']['nba']['freshness']['source_state']='fresh'
    state=t.review_state(snap)
    assert state['due'] is True and sorted(u['key'] for u in state['unreviewed'])==[f'nba:{i}' for i in range(1,6)]


def test_refusal_saying_review_again_re_triggers_the_review(setup,monkeypatch,capsys):
    plan,snap,sent,path=setup;plan['tickets']=[ticket([('nba',1),('nhl',2)])]
    file=plan_file(plan,path);assert t.check(plan)['resolved']          # validated and archived
    assert t.review_state(snap)['due'] is False
    snap['skill_sha256']='skill-updated-between-check-and-send'   # what the next fresh snapshot reports
    code,out=run_cli(monkeypatch,capsys,'send','--plan',str(file))
    assert code==1 and 'review again' in out and sent==[]
    assert t.review_state(snap)['due'] is True


# Integration review (Nov 8): delivery defects reproduced with the real telegram.controls / Telegram.call mapping.
REAL_CONTROLS = tg.controls


def test_orphan_png_from_a_crash_before_its_record_never_blocks_the_image(setup,monkeypatch):
    from app import telegram_card
    from PIL import Image
    plan,snap,sent,path=setup;plan['tickets']=[ticket([('nba',1),('nhl',2)],3)];real=tg.ledger
    class Crashing:
        def __init__(self,conn):self.conn=conn
        def execute(self,query,*a):
            if query.startswith('INSERT OR IGNORE INTO ticket_images'):raise KeyboardInterrupt
            return self.conn.execute(query,*a)
        def __getattr__(self,name):return getattr(self.conn,name)
    @contextmanager
    def crashy():
        with real() as conn:yield Crashing(conn)
    monkeypatch.setattr(tg,'ledger',crashy)
    with pytest.raises(KeyboardInterrupt):t.send(t.check(plan))
    monkeypatch.setattr(tg,'ledger',real)
    monkeypatch.setattr(telegram_card,'_logo',lambda *a:Image.new('RGBA',(60,60),'red'))   # the re-render differs
    assert t.send(t.check(plan))=='sent' and [m for m,p in sent]==['sendPhoto']
    with tg.ledger() as c:
        row=c.execute('select * from ticket_images').fetchone()
    assert t.sha(Path(row['path']).read_bytes())==row['sha256']


def test_render_failure_after_text_leaves_an_image_receipt_and_alerts(setup,monkeypatch):
    from app import telegram_card
    plan,snap,sent,path=setup;plan['tickets']=[ticket([('nba',1),('nhl',2)],3)];snap['alerts']=[]
    def broken(*a,**k):raise ValueError('Ticket card too long')
    monkeypatch.setattr(telegram_card,'render_ticket_card',broken)
    assert t.send(t.check(plan))=='partial_failed' and [m for m,p in sent]==['sendMessage']
    t.notify_alerts(snap,[])
    assert len(sent)==2 and 'TICKET DELIVERY REVIEW REQUIRED' in sent[-1][1]['text']


def test_malformed_telegram_update_cannot_abort_settlement(setup,monkeypatch):
    plan,snap,sent,path=setup;plan['tickets']=[ticket([('nba',1),('nhl',2)],3)];t.send(t.check(plan))
    monkeypatch.setattr(t,'grade_leg',lambda p,now:dict(forecast_id=p['forecast_id'],pick_label=p['pick_label'],grade='win'))
    monkeypatch.setattr(tg,'controls',REAL_CONTROLS)
    original=tg.Telegram.call
    def malformed(self,method,payload,**kw):
        if method=='getUpdates':return [{'message':{'chat':{'type':'private','id':'fixture'},'text':'/stop'}}]
        return original(self,method,payload,**kw)
    monkeypatch.setattr(tg.Telegram,'call',malformed)
    report=t.settle(now=NOW,notify=True)
    assert report['tickets'][0]['grade']=='win' and report['notification_errors']
    with tg.ledger() as c:assert c.execute('select count(*) from ticket_grades').fetchone()[0]==1


def test_non_object_telegram_body_is_an_unconfirmed_delivery():
    import httpx
    bot=tg.Telegram('fixture',transport=httpx.MockTransport(lambda request:httpx.Response(200,json=[1,2])))
    with pytest.raises(tg.DeliveryError) as caught:bot.call('getMe')
    assert caught.value.uncertain


def test_failed_settlement_notice_is_reported(setup,monkeypatch):
    plan,snap,sent,path=setup;plan['tickets']=[ticket([('nba',1),('nhl',2)],3)];t.send(t.check(plan))
    monkeypatch.setattr(t,'grade_leg',lambda p,now:dict(forecast_id=p['forecast_id'],pick_label=p['pick_label'],grade='win'))
    def rejected(self,method,payload,**kw):raise tg.DeliveryError('Telegram rejected sendMessage (HTTP 400).')
    monkeypatch.setattr(tg.Telegram,'call',rejected)
    report=t.settle(now=NOW,notify=True)
    assert report['notification_errors']==[{'ticket_id':report['tickets'][0]['ticket_id'],'status':'failed'}]


def test_offline_preflight_still_prints_the_settlement_report(setup,monkeypatch,capsys):
    import sys
    plan,snap,sent,path=setup;plan['tickets']=[ticket([('nba',1),('nhl',2)],3)];t.send(t.check(plan))
    monkeypatch.setattr(t,'grade_leg',lambda p,now:dict(forecast_id=p['forecast_id'],pick_label=p['pick_label'],grade='win'))
    def offline(self):raise tg.DeliveryError('offline',retry_after=60)
    monkeypatch.setattr(tg.Telegram,'identity',offline)
    monkeypatch.setattr(sys,'argv',['tickets','preflight','--notify'])
    assert t.main()==1
    out=json.loads(capsys.readouterr().out)
    assert out['tickets'][0]['grade']=='win' and out['status']=='failed' and out['alerts_error']=='DeliveryError: offline'   # the cause, not only the class (Nov 10)


def test_unclaimed_reservation_from_a_crash_is_alerted(setup,monkeypatch):
    plan,snap,sent,path=setup;plan['tickets']=[ticket([('nba',1),('nhl',2)],3)];snap['alerts']=[]
    original=tg.deliver
    def crash(*a,**kw):raise KeyboardInterrupt       # after the reservation commit, before the delivery claim
    monkeypatch.setattr(tg,'deliver',crash)
    with pytest.raises(KeyboardInterrupt):t.send(t.check(plan))
    monkeypatch.setattr(tg,'deliver',original)
    assert sent==[]
    t.notify_alerts(snap,[]);t.notify_alerts(snap,[])
    assert len(sent)==1 and 'TICKET DELIVERY REVIEW REQUIRED' in sent[0][1]['text'] and 'unclaimed' in sent[0][1]['text']


def test_identical_concurrent_review_archive_is_not_an_error(setup,monkeypatch):
    plan,snap,sent,path=setup;t.check(plan)
    monkeypatch.setattr(Path,'exists',lambda self:False)       # another process archived the identical review first
    assert t.check(plan)['coverage']['required']==15


# Nov 8 owner request (forwarded review): check changed lines/prices; interrupted reviews must resume or record a failure.
def quote_store(line_now=-12.5, price_b=-112, quotes=True):
    import sqlite3
    conn=sqlite3.connect(':memory:');conn.row_factory=sqlite3.Row
    conn.execute('CREATE TABLE game_forecasts (forecast_id INTEGER PRIMARY KEY, line REAL, line_source TEXT, market_quote_id INTEGER)')
    conn.execute('CREATE TABLE market_quotes (quote_id INTEGER PRIMARY KEY, game_id TEXT, provider TEXT, market TEXT, price_label TEXT, '
                 'line REAL, price_a INTEGER, price_mid INTEGER, price_b INTEGER, retrieved_at TEXT)')
    conn.executemany('INSERT INTO game_forecasts VALUES (?,?,?,?)',[(1,-12.5,'bookmaker',10),(2,120.5,'model',None),(3,None,'none',11)])
    if quotes:
        conn.executemany('INSERT INTO market_quotes VALUES (?,?,?,?,?,?,?,?,?,?)',[
            (10,'g1','DraftKings','spread','current',-12.5,-108,None,-110,'2026-11-08T16:00:00Z'),
            (11,'g1','DraftKings','winner','current',None,-650,None,480,'2026-11-08T16:00:00Z'),
            (12,'g1','DraftKings','spread','current',line_now,-105,None,price_b,'2026-11-08T16:55:00Z'),
            (13,'g1','DraftKings','winner','current',None,-700,None,504,'2026-11-08T16:55:00Z'),
            (14,'g1','DraftKings','spread','current',-14.5,-110,None,-110,'2026-11-08T17:30:00Z')])   # after as_of: never used
    return conn


def qpick(fid,market,side):
    return dict(forecast_id=fid,game_id='g1',market=market,percentage_history=dict(side=side))


def test_quote_check_reports_current_price_and_a_moved_bookmaker_line():
    same=t.quote_check(quote_store(),qpick(1,'spread','b'),NOW)
    assert same==dict(provider='DraftKings',retrieved_at='2026-11-08T16:55:00Z',line_source='bookmaker',line_at_forecast=-12.5,
                      line_now=-12.5,price_now=-112,price_at_forecast=-110,line_moved=False)
    assert t.quote_check(quote_store(line_now=-11.5),qpick(1,'spread','b'),NOW)['line_moved'] is True
    winner=t.quote_check(quote_store(),qpick(3,'winner','a'),NOW)
    assert winner['price_now']==-700 and winner['price_at_forecast']==-650 and winner['line_moved'] is False
    model=t.quote_check(quote_store(),qpick(2,'team_total_home','a'),NOW)
    assert model==dict(provider=None,retrieved_at=None,line_source='model',line_at_forecast=120.5,line_now=None,price_now=None,
                       price_at_forecast=None,line_moved=False)


def test_moved_line_is_excluded_from_the_trigger_and_refused_at_check(setup):
    plan,snap,sent,path=setup
    snap['sports']['nhl']['report']['picks'][1]['quote_check']=dict(provider='DraftKings',retrieved_at=NOW,line_source='bookmaker',
        line_at_forecast=-1.5,line_now=-2.5,price_now=150,price_at_forecast=120,line_moved=True)
    sp=Path(plan['snapshot_path']);sp.write_text(json.dumps(snap));plan['review']['snapshot_sha256']=t.sha(sp.read_bytes())
    state=t.review_state(snap)
    assert [x['key'] for x in state['line_changed']]==['nhl:2'] and state['ticketable_forecasts']==14
    plan['tickets']=[ticket([('nba',1),('nhl',2)])]
    with pytest.raises(ValueError,match='Line changed since forecast.*review again'):t.check(plan)
    assert sent==[]


def test_ticket_text_shows_latest_stored_price_and_model_line_note(setup):
    plan,snap,sent,path=setup
    snap['sports']['nba']['report']['picks'][0]['quote_check']=dict(provider='DraftKings',retrieved_at='2026-11-08T16:55:00Z',
        line_source='none',line_at_forecast=None,line_now=None,price_now=-700,price_at_forecast=-650,line_moved=False)
    snap['sports']['nhl']['report']['picks'][1].update(market='team_total_home',pick_label='B2 over 2.5',quote_check=dict(
        provider=None,retrieved_at=None,line_source='model',line_at_forecast=2.5,line_now=None,price_now=None,price_at_forecast=None,line_moved=False))
    sp=Path(plan['snapshot_path']);sp.write_text(json.dumps(snap));plan['review']['snapshot_sha256']=t.sha(sp.read_bytes())
    plan['review']['markets_reviewed']['nhl']['team_total_home']='Reviewed'
    plan['tickets']=[ticket([('nba',1),('nhl',2)])]
    text=t.check(plan)['text']
    assert 'B1 win</b> / Nov 08 01:00 PM EST / 75.0% / DK -700 (was -650)' in text
    assert 'Prices: latest stored DraftKings quotes, 11:55 AM EST' in text and 'confirm before betting' in text
    assert "NHL B2 over 2.5: the app's own line; no sportsbook line stored" in text


def test_interrupted_review_is_recorded_as_a_retryable_failure_and_alerted_once(setup,monkeypatch,capsys):
    plan,snap,sent,path=setup
    code,out=run_cli(monkeypatch,capsys,'snapshot','--output',str(path/'review-snapshot.json'))
    digest=json.loads(out)['snapshot_sha256']
    monkeypatch.setattr(t,'settle',lambda **kw:{'as_of':NOW,'errors':[],'notification_errors':[],'tickets':[]})
    code,out=run_cli(monkeypatch,capsys,'preflight','--brief')       # 0 min later: the review may still be running
    assert 'interrupted' not in json.loads(out)['review']
    snap['as_of']='2026-11-08T17:11:00Z'                              # past the 10-min snapshot expiry, no check/send
    code,out=run_cli(monkeypatch,capsys,'preflight','--brief')
    gone=json.loads(out)['review']['interrupted']
    assert [g['snapshot_sha256'] for g in gone]==[digest] and gone[0]['earliest_tipoff']=='2026-11-08T18:00:00Z'
    rows=t.review_outcomes('2026-11-08')
    assert [(r['outcome'],r['payload']['retryable']) for r in rows]==[('operational_failure',True)] and 'interrupted' in rows[0]['detail']
    assert digest in t.retryable_snapshots('2026-11-08')
    code,out=run_cli(monkeypatch,capsys,'preflight','--brief')
    assert 'interrupted' not in json.loads(out)['review'] and len(t.review_outcomes('2026-11-08'))==1   # recorded once
    assert [a for a in t.interruption_alerts(gone)]==[dict(sport='tickets',issue='review_interrupted',dedup=digest[:12],
                                                          detail='review snapshot 12:00 PM EST never checked or sent; earliest game 01:00 PM EST')]


def test_quote_check_reads_the_side_from_the_providers_record_object():
    from types import SimpleNamespace
    pick=dict(forecast_id=3,game_id='g1',market='winner',percentage_history=SimpleNamespace(side='b'))
    assert t.quote_check(quote_store(),pick,NOW)['price_now']==504


# Selection revision (owner + reviewer, Nov 8 evening; see selection audit): weigh probability, recommendation, record,
# evidence quality and exact price together, and judge every complete ticket's payout and uncertainty, not only its
# multiplied hit probability. Facts only: no new thresholds, no value claims, no forced variety/length; zero tickets allowed.
def priced(snap, sport, i, price, win_rate=None, settled=0, line_source='none'):
    pick = snap['sports'][sport]['report']['picks'][i]
    pick['quote_check'] = dict(provider='DraftKings', retrieved_at='2026-11-08T16:55:00Z', line_source=line_source,
                               line_at_forecast=None, line_now=None, price_now=price, price_at_forecast=None, line_moved=False)
    pick['evidence'] = dict(lo=.75, hi=.8, win_rate=win_rate, settled=settled, lower=None, source='history') if settled else None
    return pick


def test_decimal_odds_and_break_even():
    assert t.decimal_odds(-110) == pytest.approx(1.909091, abs=1e-6) and t.decimal_odds(103) == pytest.approx(2.03)
    assert t.decimal_odds(-700) == pytest.approx(8 / 7) and t.decimal_odds(None) is None


def test_leg_assessment_puts_probability_price_record_and_evidence_quality_side_by_side(setup):
    from app import threshold_overview
    plan, snap, sent, path = setup
    pick = priced(snap, 'nba', 0, -790, win_rate=.796, settled=607)
    pick.update(game_rank=1, skill_proven=True, percentage_history=dict(side='a', wins=0, losses=0, settled=0))
    a = t.leg_assessment('nba', pick)
    lo, hi = threshold_overview.wilson(round(.796 * 607), 607)
    assert a['break_even'] == pytest.approx(790 / 890) and a['model_gap'] == pytest.approx(.75 - 790 / 890)
    assert a['record'] == dict(win_rate=.796, settled=607, interval=[lo, hi], source='history')
    assert a['record_minus_break_even'] == [pytest.approx(lo - 790 / 890), pytest.approx(hi - 790 / 890)]
    assert a['app_rank'] == 1 and a['skill_proven'] is True and a['line_source'] == 'none' and a['exact'] == '0-0 (0 settled)'
    bare = t.leg_assessment('nhl', dict(snap['sports']['nhl']['report']['picks'][1], market='team_total_home'))
    assert bare['price'] is None and bare['break_even'] is None and bare['record'] is None
    assert bare['app_note'] == 'Team totals were not tested separately.'


def test_ticket_assessment_reports_payout_break_even_estimate_and_uncertainty(setup):
    plan, snap, sent, path = setup
    legs = [dict(priced(snap, 'nba', 0, -790, .796, 607), sport='nba'), dict(priced(snap, 'nhl', 1, 103, .6, 100), sport='nhl')]
    for leg in legs:
        leg['assessment'] = t.leg_assessment(leg['sport'], leg)
    a = t.ticket_assessment(legs)
    payout = (1 + 100 / 790) * 2.03
    assert a['payout'] == pytest.approx(payout) and a['break_even'] == pytest.approx(1 / payout)
    assert a['model_estimate'] == pytest.approx(.75 * .75) and a['model_minus_break_even'] == pytest.approx(.75 * .75 - 1 / payout)
    assert 'record_range' not in a and 'record_vs_break_even' not in a    # no ticket-level historical range (review, Nov 8)
    assert [r['record']['settled'] for r in a['leg_records']] == [607, 100]
    assert a['priced_above_model'] == ['NBA B1 win'] and a['unpriced'] == [] and a['no_record'] == []
    legs[1]['quote_check'] = None; legs[1]['evidence'] = None; legs[1]['assessment'] = t.leg_assessment('nhl', legs[1])
    a = t.ticket_assessment(legs)
    assert a['payout'] is None and a['break_even'] is None and a['leg_records'][1]['record'] is None
    assert a['unpriced'] == ['NHL B2 win'] and a['no_record'] == ['NHL B2 win']


def test_check_requires_a_payout_judgment_per_ticket_and_the_text_shows_payout_and_break_even(setup):
    plan, snap, sent, path = setup
    priced(snap, 'nba', 0, -790, .796, 607); priced(snap, 'nhl', 1, 103, .6, 100)
    sp = Path(plan['snapshot_path']); sp.write_text(json.dumps(snap)); plan['review']['snapshot_sha256'] = t.sha(sp.read_bytes())
    plan['tickets'] = [dict(label='Pair', legs=[dict(sport='nba', forecast_id=1, rationale='r'), dict(sport='nhl', forecast_id=2, rationale='r')])]
    with pytest.raises(ValueError, match='payout_assessment.*review again'):
        t.check(plan)
    plan['tickets'][0]['payout_assessment'] = 'Favorite adds little payout for its risk; kept because both legs have long records.'
    result = t.check(plan)
    text = result['text']
    assert result['resolved'][0]['assessment']['payout'] == pytest.approx((1 + 100 / 790) * 2.03)
    assert result['resolved'][0]['payout_assessment'].startswith('Favorite adds little payout')
    assert f'Pays about ×2.29 at stored prices; break-even 43.7%; model estimate {.5625:.1%} (independence assumption).' in text
    assert 'intervals multiplied' not in text and 'Similar past picks' not in text and 'entirely' not in text and 'straddles' not in text
    assert 'Sportsbook implies a higher chance than the model: leg 1.' in text
    assert 'value' not in text.lower() and 'edge' not in text.lower()


def test_unpriced_tickets_say_the_payout_is_unknown(setup):
    plan, snap, sent, path = setup
    plan['tickets'] = [dict(ticket([('nba', 1), ('nhl', 2)]), payout_assessment='No stored prices; judged on records only.')]
    text = t.check(plan)['text']
    assert f'Payout unknown (no stored price: all legs); model estimate {.5625:.1%} (independence assumption).' in text


def test_assess_action_compares_candidate_tickets_without_archiving_or_sending(setup, monkeypatch, capsys):
    plan, snap, sent, path = setup
    priced(snap, 'nba', 0, -790, .796, 607); priced(snap, 'nhl', 1, 103, .6, 100)
    sp = Path(plan['snapshot_path']); sp.write_text(json.dumps(snap)); plan['review']['snapshot_sha256'] = t.sha(sp.read_bytes())
    plan['tickets'] = [ticket([('nba', 1), ('nhl', 2)]), ticket([('nhl', 2), ('nfl', 3)])]
    code, out = run_cli(monkeypatch, capsys, 'assess', '--plan', str(plan_file(plan, path)))
    report = json.loads(out)
    assert code == 0 and [r['ticket']['payout'] is not None for r in report['tickets']] == [True, False]
    assert report['tickets'][0]['legs'][0]['label'] == 'NBA B1 win' and report['tickets'][0]['legs'][0]['model_gap'] < 0
    assert not (path / 'reports/tickets/reviews').exists() and sent == []



def test_no_ticket_level_historical_range_or_verdict_but_leg_records_stay_visible(setup):
    plan, snap, sent, path = setup
    legs = [dict(priced(snap, 'nba', 0, -790, .796, 607), sport='nba'), dict(priced(snap, 'nba', 1, -700, .882, 186), sport='nba')]
    a = t.ticket_assessment([dict(l, assessment=t.leg_assessment('nba', l)) for l in legs])
    assert set(a) == {'legs', 'payout', 'break_even', 'model_estimate', 'model_minus_break_even', 'leg_records',
                      'priced_above_model', 'unpriced', 'no_record', 'book_payouts', 'best_book'}
    assert a['leg_records'] == [dict(label='NBA B1 win', record=t.leg_assessment('nba', legs[0])['record']),
                                dict(label='NBA B2 win', record=t.leg_assessment('nba', legs[1])['record'])]
    text = t.ticket_text('2026-11-08', [dict(ticket_id='T1', label='Pair', legs=legs, assessment=a)])
    assert 'Pays about ×1.29' in text and 'intervals' not in text and 'entirely' not in text


def test_no_record_untested_markets_and_large_sportsbook_gaps_are_never_automatic_exclusions(setup):
    # Owner, Nov 8: the demo's cautions are case-by-case judgment, not bans. The helper only reports them.
    plan, snap, sent, path = setup
    nfl = priced(snap, 'nfl', 0, 190)                        # no NFL record, model 75% vs break-even 34%: a 41-point gap
    nhl = priced(snap, 'nhl', 1, -110); nhl.update(market='total', pick_label='Over 6.5')   # app note: totals showed no skill
    sp = Path(plan['snapshot_path']); sp.write_text(json.dumps(snap)); plan['review']['snapshot_sha256'] = t.sha(sp.read_bytes())
    plan['review']['markets_reviewed']['nhl']['total'] = 'Reviewed'
    state = t.review_state(snap)
    assert state['ticketable_forecasts'] == 15 and not state['blocked']
    plan['tickets'] = [dict(label='Model-led pair', payout_assessment='Both legs rest on the model alone; weighed case by case.',
                            legs=[dict(sport='nfl', forecast_id=1, rationale='r'), dict(sport='nhl', forecast_id=2, rationale='r')])]
    result = t.check(plan)
    assert len(result['resolved']) == 1 and 'No record of similar picks: all legs.' in result['text']
    assert t.leg_assessment('nhl', nhl)['app_note'] == 'Totals showed no skill over a league-average model in the one-time test.'



# f7 review fixes (F1-F3): long menus still fit, soccer picks carry an assessment, impossible prices are no price.
LONG = ['Wolverhampton Wanderers', 'Brighton and Hove Albion', 'Borussia Monchengladbach', 'Paris Saint-Germain',
        'Nottingham Forest', 'Atletico Madrid', 'Bayer Leverkusen', 'Manchester United', 'Real Sociedad', 'Newcastle United']


@pytest.mark.parametrize('shape', [(6, 2), (5, 3), (4, 4)])
def test_unpriced_no_record_menus_with_long_names_still_fit_one_message(setup, shape):
    plan, snap, sent, path = setup
    for sport in ('nba', 'nhl', 'nfl'):
        for i, p in enumerate(snap['sports'][sport]['report']['picks']):
            home, away = LONG[i % len(LONG)], LONG[(i + 3) % len(LONG)]
            p.update(matchup=f'{away} @ {home}', pick_label=f'{home} win')
    sp = Path(plan['snapshot_path']); sp.write_text(json.dumps(snap)); plan['review']['snapshot_sha256'] = t.sha(sp.read_bytes())
    count, size = shape
    games = [(s, i) for i in range(1, 6) for s in ('nba', 'nhl', 'nfl')]
    plan['tickets'] = [dict(label=f'Option {n}', payout_assessment='Judged', legs=[dict(sport=s, forecast_id=i, rationale='r')
                       for s, i in games[n * size:(n + 1) * size]]) for n in range(count)]
    text = t.check(plan)['text']                                  # raises "too long" if the menu no longer fits
    assert len(text) <= 3900 and text.count('No record of similar picks: all legs.') == count
    assert 'Wolverhampton Wanderers win,' not in text            # per-ticket lines cite leg numbers, never repeat labels


def test_decimal_odds_treats_impossible_american_prices_as_no_price():
    assert [t.decimal_odds(x) for x in (0, 50, -99, 99)] == [None] * 4
    assert t.decimal_odds(100) == 2.0 and t.decimal_odds(-100) == 2.0
    pick = dict(forecast_id=1, game_id='g', market='winner', probability=.6, quote_check=dict(price_now=0))
    assert t.leg_assessment('nba', pick)['price'] is None or t.leg_assessment('nba', pick)['break_even'] is None


def test_soccer_picks_carry_an_assessment_in_the_snapshot(monkeypatch, tmp_path):
    monkeypatch.setenv('NBA_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(t, 'SKILL', tmp_path / 'SKILL.md'); t.SKILL.write_text('fixture')
    soccer = dict(forecast_id=7, game_id='m1', market='1x2_regulation', outcome='home', pick_label='ARS win', probability=.55,
                  tipoff='2026-11-08T18:00:00Z', generated_at='2026-11-08T06:00:00Z', status='scheduled', grade='pending')
    broken = dict(forecast_id=8, game_id='m2', market='1x2_regulation', pick_label='broken')        # no probability
    monkeypatch.setattr(t, 'soccer_section', lambda now: {'freshness': {'source_state': 'fresh', 'failing': []},
                                                          'report': {'mode': 'real', 'picks': [soccer, broken]}})
    snap = t.snapshot()
    a, b = [p['assessment'] for p in snap['sports']['soccer']['report']['picks']]
    assert a['probability'] == .55 and a['price'] is None and a['record'] is None and b == {'error': 'KeyError'}


@pytest.mark.parametrize('bad', [{'why': 'pays well'}, ['pays', 'well'], 5])
def test_a_non_text_payout_assessment_is_a_clear_retryable_refusal(setup, bad):
    """First live run of revision 4 (Nov 9 01:58Z, review 9): a structured payout_assessment crashed check() with an
    AttributeError, recorded as a non-retryable 'backend compatibility' failure. It must be a plain validation refusal."""
    plan,snap,sent,path=setup;plan['tickets']=[ticket([('nba',1),('nhl',2)],None)]
    plan['tickets'][0]['payout_assessment']=bad
    with pytest.raises(ValueError, match='review again'):
        t.check(plan)


@pytest.mark.parametrize('where', ['notes', 'combination_comparison', 'market_note', 'label', 'rationale'])
def test_non_text_review_fields_are_clear_retryable_refusals(setup, where):
    """Revision 4's first live run (review 9, 2026-11-09T01:58Z): review.notes arrived as a LIST and check() crashed on
    .strip() with an AttributeError, recorded as a non-retryable 'backend compatibility' failure. Every free-text plan
    field must refuse a non-text value with a plain '…; review again' validation error."""
    plan,snap,sent,path=setup;plan['tickets']=[ticket([('nba',1),('nhl',2)],None)]
    bad=['Read the skill.', 'Compared markets.']
    if where=='notes': plan['review']['notes']=bad
    if where=='combination_comparison': plan['review']['combination_comparison']=bad
    if where=='market_note': plan['review']['markets_reviewed']['nba']['winner']=bad
    if where=='label': plan['tickets'][0]['label']=bad
    if where=='rationale': plan['tickets'][0]['legs'][0]['rationale']=bad
    with pytest.raises(ValueError, match='review again'):
        t.check(plan)


# --- Audit 2026-11-08: helper-recorded locks, recorded blockers for games that start unreviewed, grade corrections ---------

def relock(plan, snap):
    """Rewrite the reviewed snapshot file after the fixture slate changed."""
    file = Path(plan['snapshot_path']); file.write_text(json.dumps(snap))
    plan['review']['snapshot_sha256'] = t.sha(file.read_bytes())


def test_lock_blocked_forecast_is_recorded_by_the_helper_without_reviewer_text(setup):
    plan,snap,sent,path=setup;plan['tickets']=[ticket([('nba',1),('nhl',2)])]
    assert t.send(t.check(plan))=='sent'
    snap['sports']['nba']['report']['picks'][3]['game_id']='g1'      # nba:4 is another forecast of ticketed game g1
    relock(plan,snap);plan['tickets']=[]
    del plan['review']['forecasts_reviewed']['nba:4']                # the reviewer writes nothing for the locked forecast
    result=t.check(plan)
    entry=result['plan']['review']['forecasts_reviewed']['nba:4']
    assert entry['decision']=='excluded' and entry['recorded_by']=='helper'
    assert 'A4 @ B4' in entry['reason'] and 'nba:1' in entry['reason'] and 'B1 win' in entry['reason']   # its own game and lock
    lock=result['locks']['nba:4']
    assert lock['ticket_forecast_id']==1 and lock['ticket_pick']=='B1 win' and len(lock['tickets'])==1
    assert 'nba:1' not in result['locks']                            # the ticket's own forecast may recur: not locked
    assert 'nba:4' not in [u['key'] for u in t.review_state(snap)['unreviewed']]   # accounted: no re-trigger
    del plan['review']['forecasts_reviewed']['nba:5']                # not locked: still needs the reviewer's decision
    with pytest.raises(ValueError,match='candidate/excluded'):t.check(plan)


def test_reviewer_text_for_a_locked_forecast_is_kept_beside_the_helper_fact(setup):
    plan,snap,sent,path=setup;plan['tickets']=[ticket([('nba',1),('nhl',2)])]
    assert t.send(t.check(plan))=='sent'
    snap['sports']['nba']['report']['picks'][3]['game_id']='g1'
    relock(plan,snap);plan['tickets']=[]
    plan['review']['forecasts_reviewed']['nba:4']=dict(decision='excluded',reason='Own note on the newer version')
    result=t.check(plan)
    assert result['plan']['review']['forecasts_reviewed']['nba:4']==dict(decision='excluded',reason='Own note on the newer version')
    assert result['locks']['nba:4']['ticket_forecast_id']==1


def test_shared_reason_text_across_forecasts_is_not_refused(setup):
    plan,snap,sent,path=setup
    assert len({v['reason'] for v in plan['review']['forecasts_reviewed'].values()})==1   # one explanation for all 15
    assert t.check(plan)['coverage']['accounted']==15


def started(snap,at='2026-11-08T18:05:00Z'):
    snap['as_of']=at
    for section in snap['sports'].values():
        for p in (section.get('report') or {}).get('picks',[]):p['status']='in_progress'


def test_game_blocked_until_kickoff_is_reported_with_its_recorded_blocker(setup,monkeypatch,capsys):
    plan,snap,sent,path=setup
    monkeypatch.setattr(t,'settle',lambda **kw:{'errors':[],'tickets':[]})
    healthy=copy.deepcopy(snap['sports']['nfl'])
    snap['sports']['nfl']={'error':'inactive','reason':'definition_mismatch','unserved_forecasts':5,'unserved_games':5}
    code,out=run_cli(monkeypatch,capsys,'preflight','--brief')      # 17:00: forecasts exist, the sport is refused
    assert code==0 and 'nfl' in json.loads(out)['review']['unavailable']
    snap['as_of']='2026-11-08T17:15:00Z'
    assert run_cli(monkeypatch,capsys,'preflight','--brief')[0]==0  # still refused at the next 15-minute run
    refused=snap['sports']['nfl'];started(snap,'2026-11-08T18:02:00Z');snap['sports']['nfl']=refused
    assert run_cli(monkeypatch,capsys,'preflight','--brief')[0]==0  # still refused at the first run after kickoff
    snap['sports']['nfl']=healthy;started(snap)                     # readable again only later
    missed={(m['sport'],m['game_id']):m for m in t.review_state(snap)['missed_before_start']}
    entry=missed[('nfl','g1')]
    assert entry['blocker_observed'] is True and entry['explained'] is True   # blocked at the checks on both sides of the start
    assert entry['recorded_blockers']==[dict(issue='inactive',detail='definition_mismatch',first_seen_at=NOW,
                                             last_seen_at='2026-11-08T17:15:00Z',observations=2)]
    # NBA was reviewable (a review was due) and no review ran: nothing explains that miss
    assert missed[('nba','g1')]['explained'] is False and missed[('nba','g1')]['recorded_blockers']==[]
    with tg.ledger() as c:
        with pytest.raises(Exception,match='append-only'):c.execute("update ticket_trigger_observations set issue='x'")
        with pytest.raises(Exception,match='append-only'):c.execute('delete from ticket_trigger_observations')


def test_blocked_sport_that_recovers_before_kickoff_triggers_a_review_and_is_not_missed(setup,monkeypatch,capsys):
    plan,snap,sent,path=setup
    monkeypatch.setattr(t,'settle',lambda **kw:{'errors':[],'tickets':[]})
    healthy=copy.deepcopy(snap['sports']['nfl'])
    for sport in ('nba','nhl'):snap['sports'][sport]['report']['picks']=[]
    snap['sports']['nfl']={'error':'inactive','reason':'definition_mismatch','unserved_forecasts':5,'unserved_games':5}
    code,out=run_cli(monkeypatch,capsys,'preflight','--brief')
    assert json.loads(out)['review']['due'] is False
    snap['sports']['nfl']=healthy;snap['as_of']='2026-11-08T17:05:00Z'   # recovered 55 minutes before kickoff
    state=t.review_state(snap)
    assert state['due'] is True and state['reason']=='new_forecasts' and len(state['unreviewed'])==5
    relock(plan,snap)
    plan['review']['forecasts_reviewed']={k:v for k,v in plan['review']['forecasts_reviewed'].items() if k.startswith('nfl:')}
    assert t.check(plan)['coverage']=={'required':5,'accounted':5,'unavailable_sports':{},'complete_supported_sports':True}
    started(snap)
    assert t.review_state(snap)['missed_before_start']==[]


def test_single_game_window_that_starts_unreviewed_is_explained_by_the_trigger_record(setup,monkeypatch,capsys):
    plan,snap,sent,path=setup
    monkeypatch.setattr(t,'settle',lambda **kw:{'errors':[],'tickets':[]})
    for sport in ('nhl','nfl'):snap['sports'][sport]['report']['picks']=[]
    snap['sports']['nba']['report']['picks']=[p for p in snap['sports']['nba']['report']['picks'] if p['game_id']=='g1']
    code,out=run_cli(monkeypatch,capsys,'preflight','--brief')
    assert json.loads(out)['review']['reason']=='single_game_window'
    code,out=run_cli(monkeypatch,capsys,'status')                   # status stays read-only: records nothing
    with tg.ledger() as c:assert c.execute('select count(*) from ticket_trigger_observations').fetchone()[0]==1
    idle=copy.deepcopy(snap);idle['sports']['nfl']={'error':'inactive','reason':'definition_mismatch','unserved_forecasts':0,'unserved_games':0}
    assert [w['issue'] for w in t.review_state(idle)['not_reviewed_now']]==['single_game_window']   # an idle refused sport blocks nothing
    started(snap)
    [entry]=t.review_state(snap)['missed_before_start']
    assert entry['explained'] is True and [b['issue'] for b in entry['recorded_blockers']]==['single_game_window']


def test_failed_review_before_kickoff_is_attached_to_the_missed_game(setup,monkeypatch,capsys):
    plan,snap,sent,path=setup
    monkeypatch.setattr(t,'_stamp',lambda moment=None:'2026-11-08T17:01:00Z')   # the refusal is recorded inside the pregame window
    bad=copy.deepcopy(plan);bad['review']['combination_comparison']=''
    assert run_cli(monkeypatch,capsys,'check','--plan',str(plan_file(bad,path,'bad.json')))[0]==1
    started(snap)
    entry=t.review_state(snap)['missed_before_start'][0]
    assert entry['explained'] is True and entry['failed_reviews'][0]['outcome']=='operational_failure'
    assert 'Compare' in entry['failed_reviews'][0]['detail']


def settled_ticket(setup,monkeypatch,grades):
    plan,snap,sent,path=setup;plan['tickets']=[ticket([('nba',1),('nhl',2)])];assert t.send(t.check(plan))=='sent'
    monkeypatch.setattr(t,'grade_leg',lambda p,now:dict(forecast_id=p['forecast_id'],pick_label=p['pick_label'],
                                                         grade=grades[p['forecast_id']]))
    return sent


def test_correction_back_to_an_earlier_result_is_appended_and_notified(setup,monkeypatch):
    grades={1:'loss',2:'win'};sent=settled_ticket(setup,monkeypatch,grades);before=len(sent)
    t.settle(now='2026-11-08T21:00:00Z',notify=True)
    grades[1]='win';t.settle(now='2026-11-08T21:30:00Z',notify=True)           # correction: the loss becomes a win
    with tg.ledger() as c:first_two=[tuple(r) for r in c.execute('select * from ticket_grades order by rowid')]
    grades[1]='loss';report=t.settle(now='2026-11-08T22:00:00Z',notify=True)   # corrected back to the first result
    assert report['tickets'][0]['grade']=='loss'
    t.settle(now='2026-11-08T22:30:00Z',notify=True)                           # unchanged: nothing appended or re-sent
    with tg.ledger() as c:
        rows=[tuple(r) for r in c.execute('select * from ticket_grades order by rowid')]
        assert rows[:2]==first_two and len(rows)==3                           # earlier rows untouched, the reversal appended
        assert [json.loads(r[3])['grade'] for r in rows]==['loss','win','loss'] and [r[2] for r in rows][2]=='2026-11-08T22:00:00Z'
        assert json.loads(rows[2][3])['supersedes']==rows[1][1]
        with pytest.raises(Exception,match='append-only'):c.execute("update ticket_grades set as_of='x'")
        with pytest.raises(Exception,match='append-only'):c.execute('delete from ticket_grades')
    notices=[p['text'].split('\n')[0].rsplit(' / ',1)[1] for m,p in sent[before:]]
    assert notices==['LOSS','WIN','LOSS']
    assert ['Corrected result' in p['text'] for m,p in sent[before:]]==[False,True,True]   # every reversal of an announced leg


def test_leg_that_cannot_be_graded_for_a_while_is_never_announced_and_never_repeats_a_notice(setup,monkeypatch):
    grades={1:'loss',2:'win'};sent=settled_ticket(setup,monkeypatch,grades);before=len(sent)
    t.settle(now='2026-11-08T21:00:00Z',notify=True)
    grades[2]='unavailable';report=t.settle(now='2026-11-08T21:01:00Z',notify=True)   # e.g. the store could not be read
    assert report['errors']==[{'ticket_id':report['tickets'][0]['ticket_id'],'error':'settlement_unavailable'}]
    grades[2]='win';t.settle(now='2026-11-08T21:02:00Z',notify=True)                 # readable again, same result as before
    with tg.ledger() as c:
        rows=[json.loads(r[0]) for r in c.execute('select payload from ticket_grades order by rowid')]
    assert len(rows)==2 and not any('supersedes' in r for r in rows)                 # the failed reading is kept, no correction
    texts=[p['text'] for m,p in sent[before:]]
    assert len(texts)==1 and 'B2 win: win' in texts[0] and 'unavailable' not in texts[0]   # announced once; the failed reading never


@pytest.mark.parametrize('grades,ticket_result,lines',[
    ({1:'win',2:'push'},'WIN',['B1 win: win','B2 win: push']),
    ({1:'void',2:'push'},'VOID',['B1 win: void','B2 win: push']),
    ({1:'loss',2:'void'},'LOSS',['B1 win: loss','B2 win: void']),
    ({1:'push',2:'pending'},None,None)])
def test_push_and_void_legs_settle_the_ticket_and_are_shown_in_the_result_notice(setup,monkeypatch,grades,ticket_result,lines):
    sent=settled_ticket(setup,monkeypatch,grades);before=len(sent)
    report=t.settle(now='2026-11-08T21:00:00Z',notify=True)
    if ticket_result is None:
        assert report['tickets'][0]['grade']=='pending' and len(sent)==before   # nothing is announced while a leg is open
        return
    assert report['tickets'][0]['grade']==ticket_result.lower()
    assert sent[-1][1]['text'].split('\n')==[f"PARLAY RESULT / {report['tickets'][0]['ticket_id']} / {ticket_result}",*lines]


@pytest.mark.parametrize('market,line,home,away,status,tipoff,expected',[
    ('spread',-2,102,100,'final','2026-11-08T18:00:00Z','push'),            # home wins by exactly the line
    ('total',202,102,100,'final','2026-11-08T18:00:00Z','push'),
    ('team_total_home',102,102,100,'final','2026-11-08T18:00:00Z','push'),
    ('team_total_away',100.5,102,100,'final','2026-11-08T18:00:00Z','loss'),  # a half line cannot push
    ('winner',None,100,100,'final','2026-11-08T18:00:00Z','void'),          # a tie has no winner
    ('spread',-2,None,None,'postponed','2026-11-08T18:00:00Z','void'),
    ('spread',-2,102,100,'final','2026-11-08T19:00:00Z','void')])           # start moved after the forecast
def test_push_and_void_are_graded_from_the_original_forecast_line(monkeypatch,market,line,home,away,status,tipoff,expected):
    import sqlite3
    from app import db
    c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
    c.executescript('''CREATE TABLE game_forecasts(forecast_id,game_id,market,line,p_a,p_mid,p_b,tipoff_at);
    CREATE TABLE games(game_id,status,home_score,away_score,tipoff_at,result_available_at);''')
    c.execute('INSERT INTO game_forecasts VALUES(1,?,?,?,.75,0,.25,?)',('g',market,line,'2026-11-08T18:00:00Z'))
    c.execute('INSERT INTO games VALUES(?,?,?,?,?,?)',('g',status,home,away,tipoff,'2026-11-08T20:30:00Z' if status=='final' else None))
    monkeypatch.setattr(db,'connect_readonly',lambda sport:c)
    result=t.grade_leg(dict(sport='nba',forecast_id=1,game_id='g',market=market,pick_label='fixture'),'2026-11-08T21:00:00Z')
    assert result['grade']==expected and result['line']==line


# --- Independent review of 635a4ec (session 43, 2026-11-08): notices follow what was last ANNOUNCED; `explained` follows
# --- the last thing the trigger saw before the start.
def notice_texts(sent, before):
    return [p['text'] for m, p in sent[before:] if m == 'sendMessage']


def test_result_that_goes_pending_and_returns_is_not_announced_again(setup, monkeypatch):
    grades = {1: 'win', 2: 'win'}; sent = settled_ticket(setup, monkeypatch, grades); before = len(sent)
    t.settle(now='2026-11-08T21:00:00Z', notify=True)
    grades[2] = 'unresolved'; t.settle(now='2026-11-08T21:15:00Z', notify=True)   # ticket pending: stored, never announced
    grades[2] = 'win'; t.settle(now='2026-11-08T21:30:00Z', notify=True)          # the result already sent
    assert len(notice_texts(sent, before)) == 1
    with tg.ledger() as c: assert c.execute('select count(*) from ticket_grades').fetchone()[0] == 3   # every reading is kept


def test_score_only_change_with_the_same_grades_is_stored_but_not_announced(setup, monkeypatch):
    plan, snap, sent, path = setup; plan['tickets'] = [ticket([('nba', 1), ('nhl', 2)])]; assert t.send(t.check(plan)) == 'sent'
    score = {2: (4, 3)}
    monkeypatch.setattr(t, 'grade_leg', lambda p, now: dict(forecast_id=p['forecast_id'], pick_label=p['pick_label'],
                        grade='loss' if p['forecast_id'] == 1 else 'win', home_score=score.get(p['forecast_id'], (3, 4))[0],
                        away_score=score.get(p['forecast_id'], (3, 4))[1]))
    before = len(sent)
    t.settle(now='2026-11-08T21:00:00Z', notify=True)
    score[2] = (5, 3); t.settle(now='2026-11-08T21:15:00Z', notify=True)   # score corrected, every grade the same
    score[2] = (4, 3); t.settle(now='2026-11-08T21:30:00Z', notify=True)   # and corrected back
    assert len(notice_texts(sent, before)) == 1
    with tg.ledger() as c: assert c.execute('select count(*) from ticket_grades').fetchone()[0] == 3


def test_leg_settling_later_is_an_update_and_a_reversed_leg_is_a_correction(setup, monkeypatch):
    grades = {1: 'loss', 2: 'pending'}; sent = settled_ticket(setup, monkeypatch, grades); before = len(sent)
    t.settle(now='2026-11-08T21:00:00Z', notify=True)
    grades[2] = 'win'; t.settle(now='2026-11-08T21:15:00Z', notify=True)    # the open leg settles: an update
    grades[1] = 'win'; t.settle(now='2026-11-08T21:30:00Z', notify=True)    # an announced loss becomes a win: a correction
    texts = notice_texts(sent, before)
    assert [x.split('\n')[0].rsplit(' / ', 1)[1] for x in texts] == ['LOSS', 'LOSS', 'WIN']
    assert ['Corrected result' in x for x in texts] == [False, False, True]


def test_unreadable_leg_is_stored_but_never_announced(setup, monkeypatch):
    plan, snap, sent, path = setup; plan['tickets'] = [ticket([('nba', 1), ('nhl', 2)])]; assert t.send(t.check(plan)) == 'sent'
    state = {2: 'pending'}
    def grade(p, now):
        if state.get(p['forecast_id']) == 'RAISE':
            raise OSError('store briefly unreadable')
        return dict(forecast_id=p['forecast_id'], pick_label=p['pick_label'], grade=state.get(p['forecast_id'], 'loss'))
    monkeypatch.setattr(t, 'grade_leg', grade); before = len(sent)
    t.settle(now='2026-11-08T21:00:00Z', notify=True)                         # LOSS announced, one leg still open
    state[2] = 'RAISE'; report = t.settle(now='2026-11-08T21:15:00Z', notify=True)
    assert report['errors'] == [{'ticket_id': report['tickets'][0]['ticket_id'], 'error': 'settlement_unavailable'}]
    state[2] = 'pending'; t.settle(now='2026-11-08T21:30:00Z', notify=True)
    texts = notice_texts(sent, before)
    assert len(texts) == 1 and 'unavailable' not in texts[0]
    with tg.ledger() as c: assert c.execute('select count(*) from ticket_grades').fetchone()[0] == 2   # the failed reading is kept


def test_blocker_that_cleared_does_not_explain_a_review_that_was_due_and_never_ran(setup, monkeypatch, capsys):
    plan, snap, sent, path = setup
    monkeypatch.setattr(t, 'settle', lambda **kw: {'errors': [], 'tickets': []})
    healthy = copy.deepcopy(snap['sports']['nfl'])
    snap['sports']['nfl'] = {'error': 'inactive', 'reason': 'definition_mismatch', 'unserved_forecasts': 5, 'unserved_games': 5}
    run_cli(monkeypatch, capsys, 'preflight', '--brief')                     # 17:00 NFL refused
    snap['sports']['nfl'] = healthy
    for at in ('2026-11-08T17:15:00Z', '2026-11-08T17:30:00Z', '2026-11-08T17:45:00Z'):   # recovered: due each run, nobody reviews
        snap['as_of'] = at
        code, out = run_cli(monkeypatch, capsys, 'preflight', '--brief')
        assert json.loads(out)['review']['due'] is True
    started(snap)
    entry = {(e['sport'], e['game_id']): e for e in t.review_state(snap)['missed_before_start']}[('nfl', 'g1')]
    assert entry['explained'] is False                                       # the blocker was gone 45 minutes before the start
    assert entry['recorded_blockers'][0]['last_seen_at'] == NOW              # still shown, as history
    assert entry['review_due_seen'] == dict(first_seen_at='2026-11-08T17:15:00Z', last_seen_at='2026-11-08T17:45:00Z', observations=3)
    assert entry['last_observation'] == dict(issue='review_due', at='2026-11-08T17:45:00Z', minutes_before_start=15)


def test_review_that_failed_after_the_trigger_was_due_explains_the_miss(setup, monkeypatch, capsys):
    plan, snap, sent, path = setup
    monkeypatch.setattr(t, 'settle', lambda **kw: {'errors': [], 'tickets': []})
    run_cli(monkeypatch, capsys, 'preflight', '--brief')                     # 17:00 due
    monkeypatch.setattr(t, '_stamp', lambda moment=None: '2026-11-08T17:05:00Z')
    bad = copy.deepcopy(plan); bad['review']['combination_comparison'] = ''
    assert run_cli(monkeypatch, capsys, 'check', '--plan', str(plan_file(bad, path, 'bad.json')))[0] == 1
    started(snap)
    entry = t.review_state(snap)['missed_before_start'][0]
    assert entry['explained'] is True and entry['failed_reviews'][0]['recorded_at'] == '2026-11-08T17:05:00Z'
    assert entry['last_observation']['issue'] == 'review_due'


def test_rejected_notice_is_not_what_was_announced_and_a_deferred_one_is_retried_once(setup, monkeypatch):
    grades = {1: 'loss', 2: 'win'}; sent = settled_ticket(setup, monkeypatch, grades); before = len(sent)
    t.settle(now='2026-11-08T21:00:00Z', notify=True)                          # LOSS reaches the user
    delivered = tg.Telegram.call
    def rejected(self, method, payload, **kw): raise tg.DeliveryError('Telegram rejected sendMessage (HTTP 400).')
    monkeypatch.setattr(tg.Telegram, 'call', rejected)
    grades[1] = 'win'; report = t.settle(now='2026-11-08T21:15:00Z', notify=True)   # WIN is rejected: never delivered
    assert report['notification_errors']
    monkeypatch.setattr(tg.Telegram, 'call', delivered)
    grades[1] = 'loss'; t.settle(now='2026-11-08T21:30:00Z', notify=True)      # back to what the user last received
    assert len(notice_texts(sent, before)) == 1
    def busy(self, method, payload, **kw): raise tg.DeliveryError('429', retry_after=60)
    monkeypatch.setattr(tg.Telegram, 'call', busy)
    grades[2] = 'push'; t.settle(now='2026-11-08T21:45:00Z', notify=True)      # a real change, deferred by Telegram
    monkeypatch.setattr(tg.Telegram, 'call', delivered)
    t.settle(now='2026-11-08T21:45:30Z', notify=True)                          # before the retry time: nothing
    assert len(notice_texts(sent, before)) == 1
    t.settle(now='2026-11-08T21:47:00Z', notify=True); t.settle(now='2026-11-08T21:48:00Z', notify=True)
    texts = notice_texts(sent, before)
    assert len(texts) == 2 and texts[1].endswith('B2 win: push\n' + t.CORRECTED_LINE)   # sent once, after the retry time


# --- Consolidated review of 635a4ec (three reviewers) and the owner's notification rule, 2026-11-08 -----------------------
# Rule: every reading is archived; the user is told the first result, genuine grade corrections, and one final recap
# when every leg is settled. An unchanged losing ticket is not announced again each time another leg settles, and an
# unreadable leg never produces a result notice.
def three_leg_ticket(setup, monkeypatch, grades, raises=None):
    plan, snap, sent, path = setup; plan['tickets'] = [ticket([('nba', 1), ('nhl', 2), ('nfl', 3)])]
    assert t.send(t.check(plan)) == 'sent'
    def grade(p, now):
        if raises and raises.get(p['forecast_id']):
            raise raises[p['forecast_id']]
        return dict(forecast_id=p['forecast_id'], pick_label=p['pick_label'], grade=grades[p['forecast_id']])
    monkeypatch.setattr(t, 'grade_leg', grade)
    return sent


def grade_rows():
    with tg.ledger() as c:
        return [json.loads(r[0]) for r in c.execute('select payload from ticket_grades order by as_of, rowid')]


def test_lost_ticket_is_announced_once_then_only_recapped_when_every_leg_is_settled(setup, monkeypatch):
    grades = {1: 'loss', 2: 'pending', 3: 'pending'}; sent = three_leg_ticket(setup, monkeypatch, grades); before = len(sent)
    t.settle(now='2026-11-08T21:00:00Z', notify=True)                       # the initial loss
    grades[2] = 'win'; t.settle(now='2026-11-08T22:00:00Z', notify=True)    # another leg settles: archived, not announced
    assert len(notice_texts(sent, before)) == 1
    grades[3] = 'push'; t.settle(now='2026-11-08T23:00:00Z', notify=True)   # the last leg settles: one recap
    t.settle(now='2026-11-08T23:15:00Z', notify=True)                       # unchanged: nothing
    texts = notice_texts(sent, before)
    assert [x.split('\n')[1:] for x in texts] == [['B1 win: loss', 'B2 win: pending', 'B3 win: pending'],
                                                  ['B1 win: loss', 'B2 win: win', 'B3 win: push']]
    assert not any(t.CORRECTED_LINE in x for x in texts)                    # an update is not a correction
    assert [[p['grade'] for p in r['legs']] for r in grade_rows()] == [['loss', 'pending', 'pending'],
                                                                      ['loss', 'win', 'pending'], ['loss', 'win', 'push']]


def test_announced_loss_that_is_corrected_while_other_legs_are_open_is_announced_as_a_correction(setup, monkeypatch):
    grades = {1: 'loss', 2: 'pending', 3: 'pending'}; sent = three_leg_ticket(setup, monkeypatch, grades); before = len(sent)
    t.settle(now='2026-11-08T21:00:00Z', notify=True)
    grades[1] = 'win'; t.settle(now='2026-11-08T21:30:00Z', notify=True)    # the announced loss was wrong: ticket is open again
    grades[2] = 'win'; t.settle(now='2026-11-08T22:00:00Z', notify=True)    # a leg settles: archived only
    grades[3] = 'win'; t.settle(now='2026-11-08T23:00:00Z', notify=True)    # all settled: the final result
    texts = notice_texts(sent, before)
    assert [x.split('\n')[0].rsplit(' / ', 1)[1] for x in texts] == ['LOSS', 'PENDING', 'WIN']
    assert [t.CORRECTED_LINE in x for x in texts] == [False, True, False]


def test_unreadable_legs_of_any_kind_never_produce_a_result_notice(setup, monkeypatch):
    grades = {1: 'loss', 2: 'pending', 3: 'pending'}; raises = {}
    sent = three_leg_ticket(setup, monkeypatch, grades, raises); before = len(sent)
    t.settle(now='2026-11-08T21:00:00Z', notify=True)
    for minute, error in enumerate((OSError('disk'), ValueError('identity'), KeyError('row'), None), start=1):
        raises[2] = error; raises[3] = error if minute % 2 else None
        t.settle(now=f'2026-11-08T21:{minute:02d}:00Z', notify=True)
    assert len(notice_texts(sent, before)) == 1
    assert len(grade_rows()) >= 4                                           # each distinct failed reading is still archived


def test_correction_computed_while_notices_were_off_is_still_labelled_when_it_is_sent(setup, monkeypatch):
    grades = {1: 'loss', 2: 'win'}; sent = settled_ticket(setup, monkeypatch, grades); before = len(sent)
    t.settle(now='2026-11-08T21:00:00Z', notify=True)
    grades[1] = 'win'; t.settle(now='2026-11-08T21:30:00Z')                 # stored by a run without --notify
    t.settle(now='2026-11-08T21:45:00Z', notify=True)
    texts = notice_texts(sent, before)
    assert len(texts) == 2 and texts[1].split('\n')[0].endswith('/ WIN') and texts[1].endswith(t.CORRECTED_LINE)


def soccer_pick(i, tipoff, **over):
    return dict(dict(forecast_id=i, game_id=f'soccer:eng.1:{i}', market='1x2_regulation', matchup=f'Home{i} vs Away{i}',
                     pick_label=f'Home{i} win (90 min)', outcome='home', probability=.5, status='scheduled', grade='pending',
                     generated_at='2026-11-06T03:00:00Z', cutoff_at='2026-11-08T06:00:00Z', tipoff=tipoff, competition='eng.1'), **over)


def test_soccer_match_that_starts_unreviewed_is_reported_and_explained_like_any_game(setup, monkeypatch, capsys):
    plan, snap, sent, path = setup
    monkeypatch.setattr(t, 'settle', lambda **kw: {'errors': [], 'tickets': []})
    for sport in ('nba', 'nhl', 'nfl'): snap['sports'][sport]['report']['picks'] = []
    snap['sports']['soccer'] = dict(freshness=dict(source_state='fresh', failing=[], competitions_live=['eng.1']), refused={},
                                    missing_due_games=[], report=dict(mode='real', picks=[
                                        soccer_pick(1, '2026-11-08T18:00:00Z'),
                                        soccer_pick(9, '2026-11-07T15:00:00Z', status='final', grade='win'),         # yesterday's match
                                        soccer_pick(8, '2026-11-08T18:00:00Z', status='postponed')]))                # never started
    code, out = run_cli(monkeypatch, capsys, 'preflight', '--brief')         # 17:00: one eligible match, a single-game window
    assert json.loads(out)['review']['reason'] == 'single_game_window'
    snap['as_of'] = '2026-11-08T18:05:00Z'
    snap['sports']['soccer']['report']['picks'][0]['status'] = 'in_progress'
    missed = t.review_state(snap)['missed_before_start']
    assert [(m['sport'], m['game_id']) for m in missed] == [('soccer', 'soccer:eng.1:1')]
    assert missed[0]['explained'] is True and missed[0]['recorded_blockers'][0]['issue'] == 'single_game_window'
    assert missed[0]['review_window_from'] == '2026-11-08T16:00:00Z'


def test_observation_made_before_the_review_window_opened_does_not_explain_a_miss(setup, monkeypatch, capsys):
    plan, snap, sent, path = setup
    monkeypatch.setattr(t, 'settle', lambda **kw: {'errors': [], 'tickets': []})
    healthy = copy.deepcopy(snap['sports']['nfl'])
    snap['as_of'] = '2026-11-08T15:30:00Z'                                   # 2.5 h before kickoff: outside the window
    for sport in ('nba', 'nhl'): snap['sports'][sport]['report']['picks'] = []
    snap['sports']['nfl'] = {'error': 'inactive', 'reason': 'definition_mismatch', 'unserved_forecasts': 5, 'unserved_games': 5}
    run_cli(monkeypatch, capsys, 'preflight', '--brief')
    snap['sports']['nfl'] = healthy; started(snap)
    entry = t.review_state(snap)['missed_before_start'][0]
    assert entry['recorded_blockers'] == [] and entry['last_observation'] is None and entry['explained'] is False


def test_locked_forecast_selected_as_a_leg_is_refused_at_check_with_a_retryable_reason(setup):
    plan, snap, sent, path = setup; plan['tickets'] = [ticket([('nba', 1), ('nhl', 2)])]
    assert t.send(t.check(plan)) == 'sent'
    snap['sports']['nba']['report']['picks'][3]['game_id'] = 'g1'            # nba:4 is another forecast of ticketed game g1
    relock(plan, snap); plan['tickets'] = [ticket([('nba', 4), ('nfl', 5)])]
    with pytest.raises(ValueError, match=r'nba:4 is locked.*review again'):
        t.check(plan)
    plan['tickets'] = []
    del plan['review']['forecasts_reviewed']['nba:4']
    reason = t.check(plan)['plan']['review']['forecasts_reviewed']['nba:4']['reason']
    assert 'reserved or sent ticket(s)' in reason                           # a reservation locks too, delivered or not


# --- Owner, 2026-11-08: "a blocker was observed" is not "the entire missed window is explained" (HOU @ LAC) ---------------
def test_blocker_seen_before_the_start_but_gone_at_the_next_check_does_not_explain_the_whole_window(setup, monkeypatch, capsys):
    plan, snap, sent, path = setup
    monkeypatch.setattr(t, 'settle', lambda **kw: {'errors': [], 'tickets': []})
    healthy = copy.deepcopy(snap['sports']['nfl'])
    for sport in ('nba', 'nhl'): snap['sports'][sport]['report']['picks'] = []
    snap['sports']['nfl'] = {'error': 'inactive', 'reason': 'definition_mismatch', 'unserved_forecasts': 5, 'unserved_games': 5}
    run_cli(monkeypatch, capsys, 'preflight', '--brief')                     # 17:00: the last check before the 18:00 start
    snap['sports']['nfl'] = healthy; started(snap, '2026-11-08T18:01:30Z')   # first check after the start: readable again
    run_cli(monkeypatch, capsys, 'preflight', '--brief')
    entry = t.review_state(snap)['missed_before_start'][0]
    assert entry['blocker_observed'] is True and entry['explained'] is False
    assert entry['last_observation'] == dict(issue='inactive', at=NOW, minutes_before_start=60)
    assert entry['explanation'] == ("Blocker 'inactive' was last seen 60 minutes before the start and the first check at or after it "
                                    "(2026-11-08T18:01:30Z) recorded nothing for nfl, so whether a review "
                                    "was possible in those 60 minutes is not established.")   # every run is recorded: the check is named


def test_single_game_window_explains_the_whole_window_only_while_no_other_game_became_eligible(setup, monkeypatch, capsys):
    plan, snap, sent, path = setup
    monkeypatch.setattr(t, 'settle', lambda **kw: {'errors': [], 'tickets': []})
    for sport in ('nhl', 'nfl'): snap['sports'][sport]['report']['picks'] = []
    lone = [p for p in snap['sports']['nba']['report']['picks'] if p['game_id'] == 'g1']
    snap['sports']['nba']['report']['picks'] = lone
    run_cli(monkeypatch, capsys, 'preflight', '--brief')                     # 17:00: one game, a single-game window
    later = dict(lone[0], forecast_id=77, game_id='h77', generated_at='2026-11-08T17:30:00Z', tipoff='2026-11-08T19:30:00Z')
    snap['sports']['nhl']['report']['picks'] = [later]                       # a second game became eligible at 17:30
    snap['as_of'] = '2026-11-08T18:05:00Z'; lone[0]['status'] = 'in_progress'
    entry = t.review_state(snap)['missed_before_start'][0]
    assert entry['blocker_observed'] is True and entry['explained'] is False
    assert 'another game became eligible' in entry['explanation']
    snap['sports']['nhl']['report']['picks'] = []                            # without it the lone window lasted to the start
    entry = t.review_state(snap)['missed_before_start'][0]
    assert entry['explained'] is True and entry['explanation'].startswith('Single-game window')


def test_refused_review_is_filed_under_the_day_of_its_snapshot_whatever_the_calendar_says(setup, monkeypatch, capsys):
    # Found when the suite ran past midnight: the refusal was filed under the wall-clock day, so on any other day than
    # the fixture's the trigger could not find it (the release has this too).
    plan, snap, sent, path = setup
    bad = copy.deepcopy(plan); bad['review']['combination_comparison'] = ''
    assert run_cli(monkeypatch, capsys, 'check', '--plan', str(plan_file(bad, path, 'bad.json')))[0] == 1
    with tg.ledger() as c:
        assert [r[0] for r in c.execute('select day from ticket_reviews')] == [snap['date']]
    assert t._snapshot_day({'snapshot_path': str(path / 'missing.json')}) is None and t._snapshot_day(None) is None


# --- Follow-up to the re-review of 3f85fc5 (owner, 2026-11-09) ------------------------------------------------------------
# 1. Every trigger run leaves a record, clean runs included: `explained` is decided from the checks on both sides of the
#    start, so a blocker that comes back later cannot explain an earlier miss.
# 2. A result notice Telegram rejected stays visible as an undelivered result on every run until a notice that reaches
#    the user makes it moot. It is never sent again, and its receipt is never changed.
REFUSED = {'error': 'inactive', 'reason': 'definition_mismatch', 'unserved_forecasts': 5, 'unserved_games': 5}


def trigger_runs():
    with tg.ledger() as c:
        return [dict(r) for r in c.execute('select * from ticket_trigger_runs order by run_id')]


def nfl_only(snap, monkeypatch):
    monkeypatch.setattr(t, 'settle', lambda **kw: {'errors': [], 'tickets': []})
    for sport in ('nba', 'nhl'): snap['sports'][sport]['report']['picks'] = []
    return copy.deepcopy(snap['sports']['nfl'])


def preflight_at(snap, monkeypatch, capsys, at, nfl):
    snap['as_of'] = at; snap['sports']['nfl'] = nfl
    code, out = run_cli(monkeypatch, capsys, 'preflight', '--brief')
    assert code == 0
    return json.loads(out)


def nfl_missed(snap, healthy, at='2026-11-08T19:35:00Z'):
    started(snap, at); snap['sports']['nfl'] = healthy
    for p in healthy['report']['picks']: p['status'] = 'in_progress'
    return {(m['sport'], m['game_id']): m for m in t.review_state(snap)['missed_before_start']}[('nfl', 'g1')]


def test_every_trigger_run_is_recorded_including_clean_ones_and_status_records_none(setup, monkeypatch, capsys):
    plan, snap, sent, path = setup
    monkeypatch.setattr(t, 'settle', lambda **kw: {'errors': [], 'tickets': []})
    for sport in ('nba', 'nhl', 'nfl'): snap['sports'][sport]['report']['picks'] = []
    out = json.loads(run_cli(monkeypatch, capsys, 'preflight', '--brief')[1])          # nothing pending: a clean run
    assert out['review']['reason'] == 'no_eligible_forecasts' and 'not_reviewed_now' not in out['review']
    assert run_cli(monkeypatch, capsys, 'status')[0] == 0                              # read-only: records nothing
    snap['as_of'] = '2026-11-08T17:15:00Z'
    run_cli(monkeypatch, capsys, 'preflight', '--brief')
    runs = trigger_runs()
    assert [(r['recorded_at'], r['day'], r['due'], r['reason'], r['observations']) for r in runs] == [
        (NOW, '2026-11-08', 0, 'no_eligible_forecasts', 0), ('2026-11-08T17:15:00Z', '2026-11-08', 0, 'no_eligible_forecasts', 0)]
    with tg.ledger() as c:
        assert c.execute('select count(*) from ticket_trigger_observations').fetchone()[0] == 0
        with pytest.raises(Exception, match='append-only'): c.execute("update ticket_trigger_runs set reason='x'")
        with pytest.raises(Exception, match='append-only'): c.execute('delete from ticket_trigger_runs')


def test_observations_belong_to_their_run(setup, monkeypatch, capsys):
    plan, snap, sent, path = setup
    healthy = nfl_only(snap, monkeypatch)
    preflight_at(snap, monkeypatch, capsys, NOW, dict(REFUSED))
    preflight_at(snap, monkeypatch, capsys, '2026-11-08T17:15:00Z', healthy)           # readable: a review is due
    runs = trigger_runs()
    assert [(r['due'], r['reason'], r['observations']) for r in runs] == [(0, 'no_eligible_forecasts', 1), (1, 'new_forecasts', 5)]
    with tg.ledger() as c:
        rows = [tuple(r) for r in c.execute('select run_id,issue,count(*) from ticket_trigger_observations group by run_id,issue order by run_id')]
    assert rows == [(runs[0]['run_id'], 'inactive', 1), (runs[1]['run_id'], 'review_due', 5)]


def test_run_whose_review_state_could_not_be_evaluated_is_recorded_as_such(setup, monkeypatch, capsys):
    plan, snap, sent, path = setup
    monkeypatch.setattr(t, 'settle', lambda **kw: {'errors': [], 'tickets': []})
    def broken(snap): raise RuntimeError('fixture')
    monkeypatch.setattr(t, 'review_state', broken)
    out = json.loads(run_cli(monkeypatch, capsys, 'preflight', '--brief')[1])
    assert out['review'] == {'due': None, 'reason': 'review_state_unavailable', 'error': 'RuntimeError'}   # output unchanged
    assert [(r['recorded_at'], r['due'], r['reason'], r['observations']) for r in trigger_runs()] == [(NOW, None, 'review_state_unavailable', 0)]


def test_blocker_that_returns_later_does_not_explain_a_miss_when_the_first_check_after_the_start_was_clean(setup, monkeypatch, capsys):
    # The re-review's reproduction: game at 18:00; refused at 17:45, readable at 18:45, refused again at 19:30.
    plan, snap, sent, path = setup
    healthy = nfl_only(snap, monkeypatch)
    preflight_at(snap, monkeypatch, capsys, '2026-11-08T17:45:00Z', dict(REFUSED))
    started(snap); preflight_at(snap, monkeypatch, capsys, '2026-11-08T18:45:00Z', copy.deepcopy(healthy))   # first check after the start: clean
    preflight_at(snap, monkeypatch, capsys, '2026-11-08T19:30:00Z', dict(REFUSED))                           # the blocker is back
    entry = nfl_missed(snap, healthy)
    assert entry['blocker_observed'] is True and entry['explained'] is False
    assert entry['explanation'] == ("Blocker 'inactive' was last seen 15 minutes before the start and the first check at or after it "
                                    "(2026-11-08T18:45:00Z) recorded nothing for nfl, so whether a review "
                                    "was possible in those 15 minutes is not established.")
    assert entry['trigger_runs'] == dict(in_window=1, last_before_start_at='2026-11-08T17:45:00Z', first_after_start_at='2026-11-08T18:45:00Z')


def test_blocker_seen_at_the_checks_on_both_sides_of_the_start_explains_the_window(setup, monkeypatch, capsys):
    plan, snap, sent, path = setup
    healthy = nfl_only(snap, monkeypatch)
    preflight_at(snap, monkeypatch, capsys, '2026-11-08T17:45:00Z', dict(REFUSED))
    started(snap); preflight_at(snap, monkeypatch, capsys, '2026-11-08T18:05:00Z', dict(REFUSED))            # first check after the start
    preflight_at(snap, monkeypatch, capsys, '2026-11-08T18:45:00Z', copy.deepcopy(healthy))                  # clean only later
    entry = nfl_missed(snap, healthy)
    assert entry['explained'] is True
    assert entry['explanation'] == "Blocker 'inactive' was seen at the last check before the start and again at the first check at or after it."


def test_clean_check_between_a_blocker_and_the_start_leaves_the_miss_unexplained(setup, monkeypatch, capsys):
    plan, snap, sent, path = setup
    healthy = nfl_only(snap, monkeypatch)
    preflight_at(snap, monkeypatch, capsys, NOW, dict(REFUSED))                                              # 17:00 refused
    preflight_at(snap, monkeypatch, capsys, '2026-11-08T17:30:00Z', dict(REFUSED, unserved_forecasts=0, unserved_games=0))   # excludes nothing: no record for the game
    started(snap); preflight_at(snap, monkeypatch, capsys, '2026-11-08T18:02:00Z', dict(REFUSED))            # refused again after the start
    entry = nfl_missed(snap, healthy)
    assert entry['blocker_observed'] is True and entry['explained'] is False
    assert entry['explanation'] == ("The last check before the start, 30 minutes before it, recorded neither a blocker nor a due "
                                    "review for this game; 'inactive' had been recorded 60 minutes before the start.")
    assert entry['last_observation'] == dict(issue='inactive', at=NOW, minutes_before_start=60)


def test_first_check_after_the_start_that_could_not_evaluate_the_state_does_not_explain_the_miss(setup, monkeypatch, capsys):
    plan, snap, sent, path = setup
    healthy = nfl_only(snap, monkeypatch)
    preflight_at(snap, monkeypatch, capsys, '2026-11-08T17:45:00Z', dict(REFUSED))
    evaluate = t.review_state
    def broken(snap): raise RuntimeError('fixture')
    monkeypatch.setattr(t, 'review_state', broken)
    started(snap); preflight_at(snap, monkeypatch, capsys, '2026-11-08T18:05:00Z', dict(REFUSED))            # state unknown at this check
    monkeypatch.setattr(t, 'review_state', evaluate)
    preflight_at(snap, monkeypatch, capsys, '2026-11-08T18:20:00Z', dict(REFUSED))
    entry = nfl_missed(snap, healthy)
    assert entry['explained'] is False and 'could not evaluate the review state' in entry['explanation']


def test_no_check_recorded_after_the_start_yet_does_not_explain_the_miss(setup, monkeypatch, capsys):
    plan, snap, sent, path = setup
    healthy = nfl_only(snap, monkeypatch)
    preflight_at(snap, monkeypatch, capsys, '2026-11-08T17:45:00Z', dict(REFUSED))
    entry = nfl_missed(snap, healthy, '2026-11-08T18:05:00Z')
    assert entry['explained'] is False and 'no check at or after the start is recorded yet' in entry['explanation']
    assert entry['trigger_runs']['first_after_start_at'] is None


def result_receipts():
    with tg.ledger() as c:
        return [tuple(r) for r in c.execute("select delivery_key,text,status,attempted_at,message_id,error from deliveries "
                                            "where delivery_key like 'ticket-result:%' order by attempted_at,rowid")]


def reject_all(monkeypatch):
    delivered = tg.Telegram.call
    def rejected(self, method, payload, **kw): raise tg.DeliveryError('Telegram rejected sendMessage (HTTP 400).')
    monkeypatch.setattr(tg.Telegram, 'call', rejected)
    return delivered


def test_rejected_correction_stays_visible_on_every_run_and_is_never_sent_again(setup, monkeypatch):
    grades = {1: 'win', 2: 'win'}; sent = settled_ticket(setup, monkeypatch, grades); before = len(sent)
    t.settle(now='2026-11-08T21:00:00Z', notify=True)                          # WIN reaches the user
    delivered = reject_all(monkeypatch)
    grades[1] = 'loss'; report = t.settle(now='2026-11-08T21:15:00Z', notify=True)   # the LOSS correction is rejected
    ticket_id = report['tickets'][0]['ticket_id']
    assert report['notification_errors'] == [{'ticket_id': ticket_id, 'status': 'failed'}]   # the failure itself, as before
    [owed] = report['undelivered_results']
    assert {k: owed[k] for k in ('ticket_id', 'grade', 'status', 'rejected_at', 'correction')} == dict(
        ticket_id=ticket_id, grade='loss', status='failed', rejected_at='2026-11-08T21:15:00Z', correction=True)
    assert owed['last_notice'] == dict(grade='WIN', status='sent', at='2026-11-08T21:00:00Z')
    receipts = result_receipts()
    assert [r[2] for r in receipts] == ['sent', 'failed'] and receipts[1][0] == owed['delivery_key']
    monkeypatch.setattr(tg.Telegram, 'call', delivered)                        # Telegram works again
    for now, notify in (('2026-11-08T21:30:00Z', True), ('2026-11-08T21:45:00Z', False), ('2026-11-09T09:00:00Z', True)):
        later = t.settle(now=now, notify=notify)
        assert later['undelivered_results'] == [owed]                          # still owed: listed on every run, notices on or off
        assert later['notification_errors'] == []                              # not a new failure: the trigger stays quiet
    assert len(notice_texts(sent, before)) == 1                                # never sent again
    assert result_receipts() == receipts                                       # both receipts exactly as they were


def test_rejected_notice_is_no_longer_listed_once_the_user_has_the_current_result(setup, monkeypatch):
    grades = {1: 'win', 2: 'win'}; sent = settled_ticket(setup, monkeypatch, grades); before = len(sent)
    t.settle(now='2026-11-08T21:00:00Z', notify=True)
    delivered = reject_all(monkeypatch)
    grades[1] = 'loss'; assert t.settle(now='2026-11-08T21:15:00Z', notify=True)['undelivered_results']
    failed = [r for r in result_receipts() if r[2] == 'failed']
    monkeypatch.setattr(tg.Telegram, 'call', delivered)
    grades[1] = 'win'; report = t.settle(now='2026-11-08T21:30:00Z', notify=True)   # back to the WIN the user has: nothing owed
    assert report['undelivered_results'] == [] and len(notice_texts(sent, before)) == 1
    grades[1] = 'loss'; report = t.settle(now='2026-11-08T21:45:00Z', notify=True)  # the same rejected text is owed again: not attempted again
    assert len(notice_texts(sent, before)) == 1 and [u['rejected_at'] for u in report['undelivered_results']] == ['2026-11-08T21:15:00Z']
    grades[2] = 'push'; report = t.settle(now='2026-11-08T22:00:00Z', notify=True)  # a different text: a new notice, delivered
    texts = notice_texts(sent, before)
    assert len(texts) == 2 and texts[1].split('\n')[0].endswith('/ LOSS') and texts[1].endswith(t.CORRECTED_LINE)
    assert report['undelivered_results'] == []                                 # the user now has the current result
    assert [r for r in result_receipts() if r[2] == 'failed'] == failed        # the rejected receipt is kept as it was


def test_rejected_first_result_is_listed_with_nothing_delivered_before(setup, monkeypatch):
    grades = {1: 'loss', 2: 'win'}; sent = settled_ticket(setup, monkeypatch, grades)
    reject_all(monkeypatch)
    t.settle(now='2026-11-08T21:00:00Z', notify=True)
    [owed] = t.settle(now='2026-11-08T21:15:00Z', notify=True)['undelivered_results']
    assert owed['grade'] == 'loss' and owed['last_notice'] is None and owed['correction'] is False


def test_unconfirmed_result_notice_is_still_treated_as_announced_and_is_not_listed_as_rejected(setup, monkeypatch):
    grades = {1: 'loss', 2: 'win'}; sent = settled_ticket(setup, monkeypatch, grades); before = len(sent)
    delivered = tg.Telegram.call
    def unconfirmed(self, method, payload, **kw): raise tg.DeliveryError('timeout', uncertain=True)
    monkeypatch.setattr(tg.Telegram, 'call', unconfirmed)
    report = t.settle(now='2026-11-08T21:00:00Z', notify=True)
    assert report['notification_errors'] == [{'ticket_id': report['tickets'][0]['ticket_id'], 'status': 'uncertain'}]
    monkeypatch.setattr(tg.Telegram, 'call', delivered)
    later = t.settle(now='2026-11-08T21:15:00Z', notify=True)
    assert later['undelivered_results'] == [] and notice_texts(sent, before) == []   # never blindly re-sent
    assert [r[2] for r in result_receipts()] == ['uncertain']


def test_preflight_alerts_once_for_a_rejected_result_notice_and_lists_it_in_the_brief(setup, monkeypatch, capsys):
    grades = {1: 'win', 2: 'win'}; sent = settled_ticket(setup, monkeypatch, grades)
    plan, snap, _, path = setup; snap['alerts'] = []
    t.settle(now='2026-11-08T21:00:00Z', notify=True)
    delivered = tg.Telegram.call
    def rejects_results(self, method, payload, **kw):
        if 'PARLAY RESULT' in str(payload.get('text', '')): raise tg.DeliveryError('Telegram rejected sendMessage (HTTP 400).')
        return delivered(self, method, payload, **kw)
    monkeypatch.setattr(tg.Telegram, 'call', rejects_results)
    grades[1] = 'loss'; before = len(sent)
    out = json.loads(run_cli(monkeypatch, capsys, 'preflight', '--brief', '--notify')[1])
    ticket_id = out['settlement'][0]['ticket_id']
    assert [u['ticket_id'] for u in out['undelivered_results']] == [ticket_id]
    alerts = [p['text'] for m, p in sent[before:] if 'RESULT NOTICE NOT DELIVERED' in p.get('text', '')]
    assert len(alerts) == 1 and ticket_id in alerts[0] and 'LOSS' in alerts[0] and 'WIN' in alerts[0]
    assert 'not sent again automatically' in alerts[0]
    snap['as_of'] = '2026-11-09T14:00:00Z'; snap['date'] = '2026-11-09'       # the next day: still listed, not alerted again
    out = json.loads(run_cli(monkeypatch, capsys, 'preflight', '--brief', '--notify')[1])
    assert [u['ticket_id'] for u in out['undelivered_results']] == [ticket_id] and out['notification_errors'] == []
    assert len([1 for m, p in sent[before:] if 'RESULT NOTICE NOT DELIVERED' in p.get('text', '')]) == 1
    grades[1] = 'win'                                                          # nothing owed any more: gone from the brief
    out = json.loads(run_cli(monkeypatch, capsys, 'preflight', '--brief', '--notify')[1])
    assert 'undelivered_results' not in out


# --- Delta review of acd984a (sessions 43 and cb, 2026-11-09): one MEDIUM and the LOW findings ---------------------------
def test_rejected_notice_is_not_sent_again_when_only_the_payload_changes(setup, monkeypatch):
    # cb MEDIUM / 43 LOW 3: a score-only re-grade changes the evidence hash, so the identical rejected text got a new key.
    plan, snap, sent, path = setup; plan['tickets'] = [ticket([('nba', 1), ('nhl', 2)])]; assert t.send(t.check(plan)) == 'sent'
    grades, score = {1: 'win', 2: 'win'}, {2: (3, 4)}
    monkeypatch.setattr(t, 'grade_leg', lambda p, now: dict(forecast_id=p['forecast_id'], pick_label=p['pick_label'],
                        grade=grades[p['forecast_id']], home_score=score.get(p['forecast_id'], (1, 0))[0], away_score=score.get(p['forecast_id'], (1, 0))[1]))
    before = len(sent); snap['alerts'] = []
    results = lambda: [x for x in notice_texts(sent, before) if x.startswith('PARLAY RESULT')]
    t.settle(now='2026-11-08T21:00:00Z', notify=True)                          # WIN reaches the user
    delivered = reject_all(monkeypatch)
    grades[1] = 'loss'; t.settle(now='2026-11-08T21:15:00Z', notify=True)      # LOSS correction rejected
    monkeypatch.setattr(tg.Telegram, 'call', delivered)
    score[2] = (5, 4); report = t.settle(now='2026-11-08T21:30:00Z', notify=True)   # a score corrected, no grade changes
    assert len(results()) == 1                                # the rejected text is not sent under a new key
    [owed] = report['undelivered_results']
    assert owed['rejected_at'] == '2026-11-08T21:15:00Z' and len([r for r in result_receipts() if r[2] == 'failed']) == 1
    t.notify_alerts(snap, [], (), report['undelivered_results']); t.notify_alerts(snap, [], (), report['undelivered_results'])
    assert len([1 for m, p in sent[before:] if 'RESULT NOTICE NOT DELIVERED' in p.get('text', '')]) == 1   # one alert, one key
    grades[1] = 'win'; assert t.settle(now='2026-11-08T21:45:00Z', notify=True)['undelivered_results'] == []   # back to what the user has
    grades[1] = 'loss'; report = t.settle(now='2026-11-08T22:00:00Z', notify=True)   # the same rejected correction again: still not sent
    assert len(results()) == 1 and [u['rejected_at'] for u in report['undelivered_results']] == ['2026-11-08T21:15:00Z']
    grades[2] = 'push'; t.settle(now='2026-11-08T22:15:00Z', notify=True)      # a different text: a new notice, delivered
    assert len(results()) == 2 and results()[1].endswith('B2 win: push\n' + t.CORRECTED_LINE)


def test_a_rejected_text_sent_before_the_last_delivered_notice_does_not_block_a_later_identical_one(setup, monkeypatch):
    grades = {1: 'win', 2: 'win'}; sent = settled_ticket(setup, monkeypatch, grades); before = len(sent)
    reject_all(monkeypatch)
    t.settle(now='2026-11-08T21:00:00Z', notify=True)                          # first WIN rejected
    monkeypatch.setattr(tg.Telegram, 'call', lambda self, m, p, **kw: (sent.append((m, p)), dict(message_id=len(sent)))[1])
    grades[1] = 'loss'; t.settle(now='2026-11-08T21:15:00Z', notify=True)      # LOSS delivered (a new text)
    grades[1] = 'win'; t.settle(now='2026-11-08T21:30:00Z', notify=True)       # WIN again: a correction of the delivered LOSS
    texts = notice_texts(sent, before)
    assert [x.split('\n')[0].rsplit(' / ', 1)[1] for x in texts] == ['LOSS', 'WIN'] and texts[1].endswith(t.CORRECTED_LINE)


def test_a_check_exactly_at_the_start_belongs_to_the_checks_after_it(setup, monkeypatch, capsys):
    # 43 LOW 1 / cb LOW 2: a run timed at kickoff counted as the last check before AND the first check after.
    plan, snap, sent, path = setup
    healthy = nfl_only(snap, monkeypatch)
    preflight_at(snap, monkeypatch, capsys, NOW, copy.deepcopy(healthy))                  # 17:00 and 17:45: review due, none ran
    preflight_at(snap, monkeypatch, capsys, '2026-11-08T17:45:00Z', copy.deepcopy(healthy))
    started(snap, '2026-11-08T18:00:00Z'); preflight_at(snap, monkeypatch, capsys, '2026-11-08T18:00:00Z', dict(REFUSED))   # refused exactly at the start
    entry = nfl_missed(snap, healthy, '2026-11-08T18:10:00Z')
    assert entry['explained'] is False and entry['last_observation']['issue'] == 'review_due'
    assert entry['trigger_runs'] == dict(in_window=2, last_before_start_at='2026-11-08T17:45:00Z', first_after_start_at='2026-11-08T18:00:00Z')


def test_listing_undelivered_results_can_fail_without_costing_the_run(setup, monkeypatch, capsys):
    # 43 LOW 2 / cb LOW 4: the listing step ran outside the guard that protects settlement from notification problems.
    grades = {1: 'win', 2: 'win'}; settled_ticket(setup, monkeypatch, grades)
    plan, snap, sent, path = setup; snap['alerts'] = []
    def broken(*a, **kw): raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(t, 'undelivered_result', broken)
    report = t.settle(now='2026-11-08T21:00:00Z', notify=True)
    assert report['tickets'][0]['grade'] == 'win' and report['undelivered_results'] == [] and report['undelivered_error'] == 'OperationalError'
    code, out = run_cli(monkeypatch, capsys, 'preflight', '--brief', '--notify')
    assert code == 0 and json.loads(out)['review']['due'] is not None and len(trigger_runs()) == 1   # the run is still recorded
    assert json.loads(out)['undelivered_error'] == 'OperationalError'                                 # and the brief says so


def test_an_observations_table_from_an_unreleased_build_is_refused_loudly(setup, monkeypatch, capsys):
    # cb LOW 3: CREATE TABLE IF NOT EXISTS leaves a run_id-less table in place; recording would fail on every run.
    plan, snap, sent, path = setup
    monkeypatch.setattr(t, 'settle', lambda **kw: {'errors': [], 'tickets': []})
    with tg.ledger() as c:
        c.execute('CREATE TABLE ticket_trigger_observations (observation_id INTEGER PRIMARY KEY AUTOINCREMENT, recorded_at TEXT NOT NULL, '
                  'day TEXT NOT NULL, sport TEXT NOT NULL, game_id TEXT, issue TEXT NOT NULL, detail TEXT NOT NULL)')
    code, out = run_cli(monkeypatch, capsys, 'preflight', '--brief')
    review = json.loads(out)['review']
    assert code == 0 and review['due'] is True and 'run_id' in review['observations_error'] and 'unreleased' in review['observations_error']
    assert trigger_runs() == []


def test_forecast_that_arrived_after_the_last_check_is_said_so(setup, monkeypatch, capsys):
    # 43 LOW 4: the last check could not have seen a forecast made after it.
    plan, snap, sent, path = setup
    healthy = nfl_only(snap, monkeypatch)
    for p in healthy['report']['picks']: p['generated_at'] = '2026-11-08T17:58:00Z'
    preflight_at(snap, monkeypatch, capsys, '2026-11-08T17:51:00Z', {'error': 'forecasts_unavailable'})   # no forecast yet
    entry = nfl_missed(snap, healthy, '2026-11-08T18:05:00Z')
    assert entry['explained'] is False and entry['last_observation'] is None
    assert entry['explanation'] == ('No trigger run saw this game before its start: its current forecast was made 2 minutes before the start, '
                                    'after the last check (9 minutes before it).')


def test_last_pending_game_of_its_sport_says_the_check_after_the_start_recorded_nothing_for_the_sport(setup, monkeypatch, capsys):
    # cb LOW 1: once no game of the sport is pending, a check records nothing for it; "was not seen" read as "had cleared".
    plan, snap, sent, path = setup
    healthy = nfl_only(snap, monkeypatch)
    preflight_at(snap, monkeypatch, capsys, '2026-11-08T17:45:00Z', dict(REFUSED))
    started(snap); preflight_at(snap, monkeypatch, capsys, '2026-11-08T18:05:00Z', dict(REFUSED, unserved_forecasts=0, unserved_games=0))
    entry = nfl_missed(snap, healthy)
    assert entry['explained'] is False
    assert entry['explanation'] == ("Blocker 'inactive' was last seen 15 minutes before the start and the first check at or after it "
                                    "(2026-11-08T18:05:00Z) recorded nothing for nfl, so whether a review was "
                                    "possible in those 15 minutes is not established.")


def test_preflight_whose_snapshot_fails_still_leaves_a_run_record(setup, monkeypatch, capsys):
    # a9 LOW 2: an unrecorded preflight let the next run stand in as "the first check after the start".
    plan, snap, sent, path = setup
    monkeypatch.setattr(t, 'settle', lambda **kw: {'errors': [], 'tickets': []})
    def broken(): raise OSError('store unreadable')
    monkeypatch.setattr(t, 'snapshot', broken)
    code, out = run_cli(monkeypatch, capsys, 'preflight', '--brief')
    assert code == 1 and json.loads(out)['status'] == 'failed'
    [run] = trigger_runs()
    assert run['due'] is None and run['reason'] == 'snapshot_unavailable' and run['observations'] == 0 and run['recorded_at'].endswith('Z')


# --- Recheck of 98f8f96 (a9 MEDIUM): the current forecast is often a re-issue made minutes before the start ------------------
def reissued(healthy, at='2026-11-08T17:55:00Z'):
    late = copy.deepcopy(healthy)
    for p in late['report']['picks']: p['generated_at'] = at
    return late


def test_a_forecast_reissued_before_the_start_does_not_hide_that_a_review_was_due_and_never_ran(setup, monkeypatch, capsys):
    plan, snap, sent, path = setup
    healthy = nfl_only(snap, monkeypatch)
    preflight_at(snap, monkeypatch, capsys, NOW, copy.deepcopy(healthy))                 # 17:00 and 17:45: review due, none ran
    preflight_at(snap, monkeypatch, capsys, '2026-11-08T17:45:00Z', copy.deepcopy(healthy))
    entry = nfl_missed(snap, reissued(healthy), '2026-11-08T18:05:00Z')                 # after the start the current forecast is the 17:55 re-issue
    assert entry['first_forecast_at'] == '2026-11-08T17:55:00Z' and entry['explained'] is False
    assert entry['explanation'] == 'A review was due at the last check, 15 minutes before the start, and none finished.'


def test_a_forecast_reissued_before_the_start_keeps_a_blocker_seen_on_both_sides_explained(setup, monkeypatch, capsys):
    plan, snap, sent, path = setup
    healthy = nfl_only(snap, monkeypatch)
    for at in (NOW, '2026-11-08T17:45:00Z'): preflight_at(snap, monkeypatch, capsys, at, dict(REFUSED))
    started(snap); preflight_at(snap, monkeypatch, capsys, '2026-11-08T18:05:00Z', dict(REFUSED))
    entry = nfl_missed(snap, reissued(healthy))
    assert entry['explained'] is True and entry['explanation'].startswith("Blocker 'inactive' was seen at the last check before the start and again")


def test_rejected_notice_alert_says_it_is_not_repeated(setup, monkeypatch):
    # cb LOW: a notice that drops off the list and is owed again later is not alerted again (one alert per rejected receipt).
    grades = {1: 'win', 2: 'win'}; sent = settled_ticket(setup, monkeypatch, grades); plan, snap, _, path = setup; snap['alerts'] = []
    t.settle(now='2026-11-08T21:00:00Z', notify=True)
    delivered = reject_all(monkeypatch)
    grades[1] = 'loss'; report = t.settle(now='2026-11-08T21:15:00Z', notify=True)
    monkeypatch.setattr(tg.Telegram, 'call', delivered)
    t.notify_alerts(snap, [], (), report['undelivered_results'])
    [alert] = [p['text'] for m, p in sent if 'RESULT NOTICE NOT DELIVERED' in p.get('text', '')]
    assert 'this alert is not repeated' in alert


def test_seven_leg_long_shot_is_accepted_rendered_and_sent_as_one_picture(setup,monkeypatch):
    """Owner request Nov 9: a long-shot ticket of up to seven legs."""
    from app import telegram_card
    monkeypatch.setattr(telegram_card,'_logo',lambda *a:None)
    plan,snap,sent,path=setup
    seven=ticket([('nba',1),('nhl',1),('nfl',1),('nba',2),('nhl',2),('nfl',2),('nba',3)]);seven['label']='Long shot'
    plan['tickets']=[ticket([('nba',4),('nhl',4)]),seven]
    assert t.send(t.check(plan))=='sent' and [m for m,p in sent]==['sendPhoto']
    assert '#2 Long shot' in sent[0][1]['caption'] and sent[0][1]['caption'].count(' + ')==7 and '7 legs' in stored_texts()[0]


def test_picture_that_cannot_be_rendered_sends_the_short_lines_never_the_long_text(setup,monkeypatch):
    from app import telegram_card
    def broken(*a,**k):raise RuntimeError('no renderer')
    monkeypatch.setattr(telegram_card,'render_ticket_card',broken)
    plan,snap,sent,path=setup;plan['tickets']=[ticket([('nba',1),('nhl',2)])]
    assert t.send(t.check(plan))=='partial_failed'
    assert [m for m,p in sent]==['sendMessage']   # the ticket still arrives
    assert 'B1 win + B2 win' in sent[0][1]['text'] and 'Combine ALL' not in sent[0][1]['text'] and len(sent[0][1]['text'])<400
    assert 'Combine ALL' in stored_texts()[0]     # the ledger keeps the exact full text
    with tg.ledger() as c:
        assert c.execute("select status from deliveries where delivery_key like 'ticket-image:%'").fetchone()[0]=='failed'
    assert t.send(t.check(plan))=='partial_failed' and len(sent)==1   # never a second message for the same batch


def test_batch_whose_text_the_earlier_release_sent_keeps_that_release_path_for_its_image(setup,monkeypatch):
    from app import telegram_card
    monkeypatch.setattr(telegram_card,'_logo',lambda *a:None)
    plan,snap,sent,path=setup;plan['tickets']=[ticket([('nba',1),('nhl',2)])]
    assert t.send(t.check(plan))=='sent'
    with tg.ledger() as c:   # the earlier release's state after its text and before its image: no picture archived, no image receipt
        c.execute("delete from deliveries where delivery_key like 'ticket-image:%'");c.execute('delete from ticket_images')
    assert t.send(t.check(plan))=='sent' and [m for m,p in sent]==['sendPhoto','sendPhoto']
    assert 'See preceding text' in sent[1][1]['caption']
    assert t.send(t.check(plan))=='existing_sent' and len(sent)==2


def seven(i):
    games=[(s,n) for n in range(1,6) for s in ('nba','nhl','nfl')]
    return [games[(i+k)%15] for k in range(7)]


def long_shots(plan,count):
    plan['tickets']=[ticket(seven(i)) for i in range(count)]
    for n,x in enumerate(plan['tickets'],1):x['label']=f'Long shot {n}';del x['stake_usd']


def test_largest_seven_leg_menu_delivers_every_selection_on_pictures_and_a_larger_one_is_refused_whole(setup,monkeypatch):
    """Boundary (review, Nov 9): the stored ticket text must fit one Telegram message, so `check` refuses a menu that is
    too large before anything is reserved or sent; the largest menu it accepts must reach the chat complete."""
    from PIL import ImageDraw
    from app import telegram_card
    monkeypatch.setattr(telegram_card,'_logo',lambda *a:None)
    drawn=[];original=ImageDraw.ImageDraw.text
    def capture(self,xy,text,*a,**kw):drawn.append(text);return original(self,xy,text,*a,**kw)
    monkeypatch.setattr(ImageDraw.ImageDraw,'text',capture)
    plan,snap,sent,path=setup
    accepted=[]
    for count in range(1,t.MAX_TICKETS+1):
        long_shots(plan,count)
        try:t.check(plan);accepted.append(count)
        except ValueError as exc:
            assert 'too long for one Telegram message' in str(exc)
    count=max(accepted);assert count>=3 and accepted==list(range(1,count+1))
    if count<t.MAX_TICKETS:
        long_shots(plan,count+1)
        with pytest.raises(ValueError,match='too long'):t.send({'plan':plan,'resolved':[{'signature':'x'}]})
        assert sent==[]
    long_shots(plan,count);checked=t.check(plan)
    assert t.send(checked)=='sent' and [m for m,p in sent]==['sendPhoto']*count
    for n,(resolved,(m,p)) in enumerate(zip(checked['resolved'],sent),1):
        caption=p['caption']
        assert len(caption)<=1024 and f'picture {n} of {count}' in caption and not caption.endswith('...')
        line=next(l for l in caption.split('\n') if l.startswith(f'#{n}'))
        assert line.count(' + ')==6 and all(leg['pick_label'] in line for leg in resolved['legs'])
        assert any(resolved['ticket_id'] in v for v in drawn) and any(v.startswith(f'Ticket {n}:') for v in drawn)
    assert sum(v.startswith('7. ') for v in drawn)==count      # the seventh leg of every ticket is drawn
    with tg.ledger() as c:
        pages=c.execute("select status from deliveries where delivery_key like 'ticket-image:%' order by delivery_key").fetchall()
        assert [r[0] for r in pages]==['sent']*count and c.execute('select count(*) from ticket_images').fetchone()[0]==count
    assert t.send(t.check(plan))=='existing_sent' and len(sent)==count
    assert [d['kind'] for d in t.ticket_deliveries(snap['date'])]==['ticket_text','ticket_image']+['ticket_image_page']*(count-1)


def test_selections_that_cannot_be_shown_in_full_are_refused_before_any_reservation(setup):
    plan,snap,sent,path=setup
    for sport in ('nba','nhl','nfl'):
        for p in snap['sports'][sport]['report']['picks']:p['pick_label']=p['pick_label']+' '+'x'*170
    monkeypatch_snapshot=Path(plan['snapshot_path']);monkeypatch_snapshot.write_text(json.dumps(snap))
    plan['review']['snapshot_sha256']=t.sha(monkeypatch_snapshot.read_bytes())
    plan['tickets']=[ticket(seven(0))]
    checked=t.check(plan)
    with pytest.raises(ValueError,match='do not fit'):t.send(checked)
    with tg.ledger() as c:
        t.schema(c);assert c.execute('select count(*) from ticket_batches').fetchone()[0]==0
    assert sent==[]


def test_a_later_picture_that_may_not_have_arrived_is_never_resent_and_earlier_ones_are_not_repeated(setup,monkeypatch):
    from app import telegram_card
    monkeypatch.setattr(telegram_card,'_logo',lambda *a:None)
    plan,snap,sent,path=setup;plan['tickets']=[ticket(seven(0)),ticket(seven(3))]
    original=tg.Telegram.call;calls=[]
    def flaky(self,method,payload,**kw):
        calls.append(method)
        if len(calls)==2:raise tg.DeliveryError('uncertain image',uncertain=True)
        return original(self,method,payload,**kw)
    monkeypatch.setattr(tg.Telegram,'call',flaky)
    assert t.send(t.check(plan))=='partial_uncertain'
    assert t.send(t.check(plan))=='partial_uncertain' and calls==['sendPhoto','sendPhoto']


def test_archived_picture_that_fails_its_hash_sends_nothing(setup,monkeypatch):
    from app import telegram_card
    monkeypatch.setattr(telegram_card,'_logo',lambda *a:None)
    plan,snap,sent,path=setup;plan['tickets']=[ticket([('nba',1),('nhl',2)])]
    original=tg.Telegram.call
    def busy(self,method,payload,**kw):raise tg.DeliveryError('Telegram rejected sendPhoto (HTTP 429).',retry_after=1)
    monkeypatch.setattr(tg.Telegram,'call',busy)
    assert t.send(t.check(plan))=='retry'
    with tg.ledger() as c:
        png=Path(c.execute('select path from ticket_images').fetchone()[0])
        c.execute("update deliveries set next_attempt_at='2000-01-01T00:00:00Z'")
    png.chmod(0o600);png.write_bytes(png.read_bytes()+b'tampered')
    monkeypatch.setattr(tg.Telegram,'call',original)
    with pytest.raises(ValueError,match='hash mismatch'):t.send(t.check(plan))
    assert sent==[]


def test_reviewer_sees_each_picks_exact_percentage_rank_and_the_ranked_list(setup,monkeypatch):
    """Owner request Nov 9: the AI reads the ranked exact percentages when it builds tickets."""
    plan,snap,sent,path=setup
    pick=dict(snap['sports']['nba']['report']['picks'][0],percentage_history=dict(wins=4,losses=1,settled=5,rank=2,ranked=9,line_source='none'))
    assert t.leg_assessment('nba',pick)['exact_rank']=='2 of 9' and t.leg_assessment('nba',pick)['exact']=='4-1 (5 settled)'
    assert t.leg_assessment('nba',snap['sports']['nba']['report']['picks'][1])['exact_rank'] is None
    class Provider:
        indexes={m:{('87.8','a','primary_direct','m','none'):{'g1':'win','g2':'win','g3':'loss'}} for m in ('winner','spread','total','team_total_home','team_total_away')}
    ranking=t.percentage_rankings('nfl',Provider(),NOW)
    assert ranking['winner']==[dict(percentage='87.8',side='a',source='primary_direct',model_version='m',line_source='none',wins=2,losses=1,settled=3,win_rate=2/3,rank=1)]
    class Broken:
        indexes=None
    assert t.percentage_rankings('nfl',Broken(),NOW)=={'error':'TypeError'}     # a ranking problem never fails the snapshot


def test_nfl_percentage_rankings_populates_lazy_market_indexes():
    class LazyProvider:
        indexes = {}
        def cohort(self, market):
            self.indexes[market] = {('60.0', 'a', 'primary_direct', 'm', 'none'): {'g1': 'win', 'g2': 'win', 'g3': 'loss'}}
    ranking = t.percentage_rankings('nfl', LazyProvider(), NOW)
    assert set(ranking) == {'winner', 'spread', 'total', 'team_total_home', 'team_total_away'}
    assert ranking['total'][0]['settled'] == 3


def _flag(snap, sport, fid, flags):
    for p in snap['sports'][sport]['report']['picks']:
        if p['forecast_id'] == fid:
            p['lineup_flags'] = flags


def test_a_pick_with_a_lineup_flag_needs_a_stated_reason(setup, monkeypatch):
    """Owner request Nov 11: an unconfirmed/backup goalie or a top player out/questionable on the picked side keeps a pick
    off tickets unless the reviewer states why it still stands. The flag may first appear in the fresh snapshot."""
    plan, snap, sent, path = setup
    fresh = copy.deepcopy(snap)
    _flag(fresh, 'nhl', 2, ['B2 goalie not confirmed (expected)'])
    monkeypatch.setattr(t, 'snapshot', lambda: copy.deepcopy(fresh))
    plan['tickets'] = [ticket([('nba', 1), ('nhl', 2)], 3)]
    with pytest.raises(ValueError, match=r'A2 @ B2 B2 win: B2 goalie not confirmed \(expected\)\. Leave this pick off'):
        t.check(plan)
    plan['tickets'][0]['legs'][1]['lineup_reason'] = '   '
    with pytest.raises(ValueError, match='lineup_reason'):
        t.check(plan)
    plan['tickets'][0]['legs'][1]['lineup_reason'] = 'Both listed goalies are the usual starters; price unchanged since'
    leg = t.check(plan)['resolved'][0]['legs'][1]
    assert leg['lineup_reason'].startswith('Both listed') and leg['lineup_flags'] == ['B2 goalie not confirmed (expected)']
    assert 'lineup_reason' not in t.check(plan)['resolved'][0]['legs'][0]
    assert sent == []


def test_a_snapshot_pick_without_lineup_facts_is_flagged_not_waved_through():
    assert t.lineup_flags('nhl', [{}]) == ['lineup facts missing from the snapshot']
    assert t.lineup_flags('nfl', [{}]) == [] and t.lineup_flags('nba', [{'lineup_flags': []}]) == []


def test_snapshot_attaches_lineup_facts_and_flags_and_survives_a_failure(monkeypatch):
    from app import lineup
    picks = [dict(game_id='g', market='winner', matchup='BOS @ TOR', pick_label='TOR win')]
    monkeypatch.setattr(lineup, 'facts', lambda conn, sport, ids, at: {'g': {'home': {'backup': True, 'goalie': 'X',
                        'status': 'confirmed'}, 'away': {'backup': False, 'status': 'confirmed'}}})
    t.attach_lineups(None, 'nhl', picks, NOW)
    assert picks[0]['lineup_flags'] == ['backup goalie listed for TOR (X, confirmed)'] and picks[0]['lineup']['home']['backup']
    monkeypatch.setattr(lineup, 'facts', lambda *a: 1 / 0)
    t.attach_lineups(None, 'nhl', picks, NOW)
    assert picks[0]['lineup'] == {'error': 'ZeroDivisionError'} and picks[0]['lineup_flags'] == ['lineup facts unavailable']
    soccer = [dict(game_id='s')]
    t.attach_lineups(None, 'soccer', soccer, NOW)
    assert soccer == [dict(game_id='s')]
