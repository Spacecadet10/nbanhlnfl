"""NFL prospective cutoff, append-only retries, settlement and cross-sport isolation."""
import json
from datetime import date

import pytest

from app import backtest, db, ingest, nfl, nfl_eval, nfl_live, sports, freshness
from app.schemas import NflGameDetail
from app.telegram import format_batch, message_batches
from test_nfl import nfl_event, nfl_payload

NOW = '2026-11-02T23:00:00Z'
START = '2026-11-03T00:15:00Z'


@pytest.fixture
def live_store(tmp_path, monkeypatch):
    monkeypatch.setenv('NBA_DATA_DIR', str(tmp_path))
    c = db.connect(sport='nfl')
    db.migrate(c)
    ingest.ingest_nfl_month(c, '202611', fetch=lambda _: nfl_payload(nfl_event(
        eid='live', date=START, year=2026, status='STATUS_SCHEDULED')), retrieved_at='2026-11-02T21:00:00Z')
    s = nfl_eval.seal_choice({'model': 'elo', 'config': {'k':20,'home_advantage':50,'carry':.5,'mov':True},
        'dataset_version': backtest.dataset_version(nfl_eval.load_rows(c, through=2021)), 'calibration': {'accepted':False},
        'line_markets': {'score_model': {'eta':.05,'carry':.75}, 'markets': {'winner': {'best_threshold':.75}}}})
    r = {'choice_sha256':s['sha256'], 'winner': {'confirmed':True,'frozen_threshold':{
        'threshold':.75,'holds':True,'graded':157,'won':126,'win_rate':126/157,'ci95':[.73,.86]}}}
    log = tmp_path/'test_access.jsonl'
    log.write_text(json.dumps({'choice_sha256':s['sha256'],'purpose':nfl_eval._RUN_DONE})+'\n')
    monkeypatch.setattr(db, 'utc_now', lambda: '2026-11-02T22:00:00Z')
    nfl_live.activate(c,s,r,log)
    monkeypatch.setattr(freshness,'summary',lambda *_a,**_kw: {'source_state':'fresh','failing':False})
    yield c
    c.close()


def test_two_hour_window_actual_timestamp_retry_and_five_markets(live_store):
    c = live_store
    assert nfl_live.record(c,now='2026-11-02T22:14:59Z')['recorded']==0
    assert all(x['state']=='upcoming' for x in nfl_live.Views(c,'2026-11-02T22:14:59Z').forecasts('nfl:live'))
    assert nfl_live.record(c,now=NOW)['recorded']==5
    assert nfl_live.record(c,now='2026-11-02T23:01:00Z')['recorded']==0
    rows = list(c.execute('SELECT * FROM game_forecasts'))
    assert {r['market'] for r in rows} == set(sports.NFL.markets)
    assert all(r['generated_at']==NOW and r['sport']=='nfl' for r in rows)
    assert all(r['line_source']=='model' for r in rows if r['market']!='winner')
    assert nfl_live.record(c,now=START)['recorded']==0
    assert c.execute('SELECT COUNT(*) FROM market_forecasts').fetchone()[0]==0


def test_incomplete_batch_resumes_without_duplicating_existing_record(live_store,monkeypatch):
    original=coverage_insert=nfl_live.coverage._insert
    calls=[]
    def fail(conn,row):
        if calls:
            raise RuntimeError('interrupted')
        calls.append(row['market'])
        return coverage_insert(conn,row)
    monkeypatch.setattr(nfl_live.coverage,'_insert',fail)
    with pytest.raises(RuntimeError):
        nfl_live.record(live_store,now=NOW)
    monkeypatch.setattr(nfl_live.coverage,'_insert',original)
    assert nfl_live.record(live_store,now='2026-11-02T23:01:00Z')['recorded']==4
    assert live_store.execute('SELECT COUNT(*) FROM game_forecasts').fetchone()[0]==5


def test_results_after_fixed_cutoff_cannot_change_probability(live_store):
    c=live_store
    # At 22:30 this final is known at generation, but AFTER this game's fixed 22:15 cutoff.
    ingest.ingest_nfl_month(c,'202611',fetch=lambda _:nfl_payload(nfl_event(eid='prior',year=2026,
        date='2026-11-02T18:00:00Z',home=('16','MIN','Minnesota Vikings','70'))),retrieved_at='2026-11-02T22:30:00Z')
    nfl_live.record(c,now=NOW)
    p=c.execute("SELECT p_a FROM game_forecasts WHERE market='winner'").fetchone()[0]
    m=nfl_eval.NflElo(nfl_eval.EloConfig(k=20,home_advantage=50,carry=.5,mov=True))
    g=c.execute("SELECT * FROM games WHERE game_id='nfl:live'").fetchone()
    assert p==m.predict(nfl_live._game(g))


def test_active_views_history_and_telegram_match_exact_source(live_store):
    nfl_live.record(live_store,now=NOW)
    v=nfl_live.Views(live_store,NOW)
    forecasts=v.forecasts('nfl:live')
    assert len(forecasts)==5
    for c in forecasts:
        h=c['percentage_history']
        assert h['sport']=='nfl' and h['settled']==0 and h['wins']==h['losses']==0
    game=nfl.game_detail(live_store,'nfl:live')
    assert len(NflGameDetail(**game).game.forecasts)==5
    report=v.ranked_picks(date(2026,11,2))
    assert len(report['picks'])==5 and not any(p['strong'] for p in report['picks'])
    batches=message_batches(report,'123')
    assert len(batches)==1 and 'PROP51 PICKS' in batches[0][1] and 'NFL' in batches[0][1]
    assert all(m['summary']['graded']==0 for m in v.report()['markets'])


def test_nfl_tied_winner_void_and_line_pushes(live_store):
    nfl_live.record(live_store,now=NOW)
    live_store.execute("UPDATE games SET status='final',home_score=20,away_score=20,result_available_at=? WHERE game_id='nfl:live'",('2026-11-03T04:00:00Z',))
    before=nfl_live.Views(live_store,'2026-11-03T03:59:59Z').forecasts('nfl:live')
    assert all(c['grade']=='pending' for c in before)
    after=nfl_live.Views(live_store,'2026-11-03T04:00:00Z').forecasts('nfl:live')
    assert next(c for c in after if c['market']=='winner')['grade']=='void'
    assert nfl_live.markets.settle('total',40,status='final',home=20,away=20)=='mid'
    assert nfl_live.markets.settle('spread',0,status='final',home=20,away=20)=='mid'


def test_stale_source_inactive_and_definition_changes_refuse(live_store,monkeypatch):
    monkeypatch.setattr(freshness,'summary',lambda *_a,**_kw:{'source_state':'stale','failing':False})
    assert nfl_live.record(live_store,now=NOW)['status']=='blocked'
    monkeypatch.setattr(nfl_live,'definition',lambda:'changed')
    assert nfl_live.activation() is None
    assert nfl_live.record(live_store,now=NOW)['status']=='inactive'


def test_line_market_never_recommended_even_at_high_confidence(live_store):
    nfl_live.record(live_store,now=NOW)
    v=nfl_live.Views(live_store,NOW)
    cs=v.forecasts('nfl:live')
    for c in cs:
        c['record']['confidence']=.99
    assert v.recommended([c for c in cs if c['market']!='winner']) is None
    assert v.recommended(cs)==cs[0]['record']['pick_label']
    v.active['test_report']['winner']['confirmed']=False
    assert v.recommended(cs) is None


def test_wrong_store_still_rejected(live_store):
    other=db.connect(':memory:')
    db.migrate(other)
    db.claim_store(other,'nba')
    with pytest.raises(db.StoreMismatch):
        nfl_live.record(other,now=NOW)
    other.close()


def test_quote_migration_preserves_existing_references_and_guards(tmp_path,monkeypatch):
    import sqlite3
    from test_storage import event,payload
    monkeypatch.setenv('NBA_DATA_DIR',str(tmp_path))
    c=db.connect(tmp_path/'legacy.sqlite')
    db.migrate(c,upto=32)
    ingest.ingest_month(c,'202401',fetch=lambda _:payload(event()),retrieved_at='2024-01-15T12:00:00Z')
    c.execute("INSERT INTO market_quotes(sport,game_id,provider,market,price_label,line,price_a,price_b,p_a_devig,p_b_devig,retrieved_at,fetch_id,raw_id) VALUES('nba','espn:1001','Book','spread','current',-1.5,-110,-110,.5,.5,'2024-01-15T12:00:00Z',1,1)")
    c.execute("INSERT INTO market_forecasts(idempotency_key,sport,game_id,market,line,line_source,market_quote_id,policy,run_type,model_version,dataset_version,feature_version,cutoff_at,generated_at,tipoff_at,p_a,p_mid,p_b) VALUES('legacy','nba','espn:1001','spread',-1.5,'market',1,'pregame','demo','test','test','test','2024-01-15T18:00:00Z','2024-01-15T12:00:00Z','2024-01-15T18:00:00Z',.5,0,.5)")
    old=tuple(c.execute('SELECT * FROM market_quotes').fetchone())
    assert db.migrate(c)==['0033_nfl_market_quotes.sql','0034_soccer_store.sql','0035_result_integrity.sql']
    assert tuple(c.execute('SELECT * FROM market_quotes').fetchone())==old
    assert c.execute('SELECT market_quote_id FROM market_forecasts').fetchone()[0]==1
    assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
    assert c.execute('PRAGMA foreign_keys').fetchone()[0]==1
    assert db.migrate(c)==[]
    for sql in ('DELETE FROM market_quotes','UPDATE market_quotes SET line=2'):
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(sql)
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO market_quotes SELECT 2,'nfl',game_id,provider,market,price_label,line,price_a,price_mid,price_b,p_a_devig,p_mid_devig,p_b_devig,quote_time,retrieved_at,fetch_id,raw_id FROM market_quotes")
    c.close()


def test_nfl_price_ingestion_uses_exact_event_and_is_idempotent(live_store):
    from test_storage import event
    ev=nfl_event(eid='live',date=START,year=2026,status='STATUS_SCHEDULED')
    ev['competitions'][0]['odds']=event(odds_ml=(-150,130))['competitions'][0]['odds']
    body=nfl_payload(ev)
    out=ingest.ingest_nfl_month(live_store,'202611',fetch=lambda _:body,retrieved_at='2026-11-02T22:10:00Z')
    assert out['lines']['inserted']>0 and out['lines']['unmatched']==0
    fetch_id=out['lines']['fetch_id']
    with db.transaction(live_store):
        retry=ingest.import_lines_fetch(live_store,fetch_id,'nfl')
    assert retry['inserted']==0
    assert all(r['sport']=='nfl' and r['game_id']=='nfl:live' for r in live_store.execute('SELECT * FROM market_quotes'))
