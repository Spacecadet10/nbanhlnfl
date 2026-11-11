"""Ticket lifecycle uses synthetic forecasts, temporary ledgers and a mocked Telegram transport."""
import copy
import json
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
                  generated_at='2026-11-08T16:30:00Z',tipoff='2026-11-08T18:00:00Z') for i in range(1,6)]
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


def ticket(legs,stake=1):
    return dict(label='Main' if stake==3 else 'Variation',stake_usd=stake,
                legs=[dict(sport=s,forecast_id=i,rationale='Fixture selection') for s,i in legs])


def test_mixed_ticket_sent_as_numbered_text_exact_archive_and_dedup(setup):
    plan,snap,sent,path=setup;plan['tickets']=[ticket([('nba',1),('nhl',2)],3)]
    checked=t.check(plan);assert t.send(checked)=='sent';assert t.send(checked)=='existing_sent'
    assert len(sent)==2 and [m for m,p in sent]==['sendMessage','sendPhoto']
    text=sent[0][1]['text']
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
    assert 'Shared core exposure' in sent[-2][1]['text'] and 'shared across 2 tickets' in sent[-2][1]['text']
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


def test_primary_coverage_published_before_slow_research(monkeypatch, tmp_path):
    from app import jobs
    calls=[]
    from app import availability
    monkeypatch.setattr(availability,'research_dir',lambda:tmp_path)
    monkeypatch.setattr(jobs,'sport',lambda c:'nba')
    monkeypatch.setattr(jobs,'refresh',lambda *a,**kw:{'status':'success'})
    monkeypatch.setattr(jobs,'odds_snapshot',lambda *a,**kw:{'status':'success'})
    monkeypatch.setattr(jobs,'boxscores',lambda *a,**kw:{'status':'success'})
    monkeypatch.setattr(jobs.near_tipoff,'predict_now',lambda *a,**kw:{'refused':[], 'forecasts':[{'game_id':'fixture'}]})
    monkeypatch.setattr(jobs,'market_forecasts',lambda *a,**kw:{'status':'success','created':4})
    monkeypatch.setattr(jobs.coverage,'record',lambda *a,**kw:calls.append('coverage') or {'rows':[], 'created':5})
    monkeypatch.setattr(jobs.research_live,'enabled',lambda:False)
    def research(*a,**kw):
        assert calls==['coverage'];calls.append('research');return {'status':'success'}
    monkeypatch.setattr(jobs,'research_forecasts',research)
    result=jobs.predict_now(None,dry_run=True,now=NOW)
    assert calls==['coverage','research','coverage']
    assert result['early_coverage']['created']==5


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
    assert len(sent)==3 and '\n' in sent[2][1]['text'] and '\\n' not in sent[2][1]['text']
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
    t.notify_alerts(snap,[]);t.notify_alerts(snap,[]);assert len(sent)==1
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
    assert len(sent)==2


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


def test_fallback_explanation_only_does_not_create_new_forecast_identity():
    from app import coverage
    row=dict(order_version='v',game_id='g',market='winner',tipoff_at=NOW,status='forecast',source='primary_recorded',
             source_table='p',source_id=1,model_version='v',line=None,line_source='none',p_a=.75,p_mid=0,p_b=.25,
             fallback_reason='research pending',reason=None)
    changed={**row,'fallback_reason':'research unavailable'}
    assert coverage._content_key(row)==coverage._content_key(changed)
    assert coverage._content_key(row)!=coverage._content_key({**row,'p_a':.7,'p_b':.3})


def test_production_sends_visual_ticket_after_text_with_archived_png(setup,monkeypatch):
    from app import telegram_card
    monkeypatch.setattr(telegram_card,'_logo',lambda *a:None)
    plan,snap,sent,path=setup;plan['tickets']=[ticket([('nba',1),('nhl',2)],3)]
    assert t.send(t.check(plan))=='sent'
    assert [m for m,p in sent]==['sendMessage','sendPhoto']
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
    assert t.send(t.check(plan))=='partial_uncertain'
    assert t.send(t.check(plan))=='partial_uncertain'
    assert len(photos)==1 and len(sent)==1


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
    text=sent[-2][1]['text']
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
    assert 'Shared core exposure' not in sent[-2][1]['text'] and 'nba:1' not in sent[-2][1]['text']
    plan['tickets']=[ticket([('nba',1),('nhl',3)])];assert t.send(t.check(plan))=='sent'
    text=sent[-2][1]['text'];assert 'B1 win' in text and 'shared across 3 tickets' in text and 'nba:1' not in text


def test_ticket_messages_are_labelled_as_combined_parlays_not_game_cards(setup):
    plan,snap,sent,path=setup;plan['tickets']=[ticket([('nba',1),('nhl',2)])]
    assert t.send(t.check(plan))=='sent'
    assert 'COMBINED PARLAY TICKET' in sent[0][1]['text'] and 'not a single-game card' in sent[0][1]['text'].lower()
    assert 'COMBINED PARLAY TICKET' in sent[1][1]['caption']


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
    text=sent[0][1]['text']
    assert '4 options:' in text and '#1 2 legs' in text and '#4 5 legs' in text
    assert 'Moneyline' in text and 'Puck line' in text and 'Over/Under' in text and 'Team total' in text and 'Spread' in text
    assert 'Ticket 4: Bigger five-leg' in text and len(text)<=3900
    assert 'Ticket 4' in sent[1][1]['caption'] and '+1 more' in sent[1][1]['caption']


@pytest.mark.parametrize('tickets,message',[
    ([[('nba',i),('nhl',i)] for i in range(1,6)]+[[('nba',1),('nfl',1)],[('nba',2),('nfl',2)]],'six'),
    ([[('nba',1),('nhl',1),('nfl',1),('nba',2),('nhl',2),('nfl',2),('nba',3)]],'two to six'),
    ([[('nba',1)]],'two to six')])
def test_menu_caps_are_six_options_of_two_to_six_legs(setup,tickets,message):
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
