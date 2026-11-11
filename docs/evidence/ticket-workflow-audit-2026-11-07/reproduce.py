import importlib.util, sys, json, tempfile, copy
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, '/Users/andry/Desktop/nba-predictor-sprint1/backend')
spec=importlib.util.spec_from_file_location('tickets','/Users/andry/.codex/skills/courtside-ticket-review/scripts/tickets.py')
h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
from app import telegram, telegram_card, config
from PIL import ImageDraw
root=Path(tempfile.mkdtemp(prefix='ticket-audit-'))
now='2026-11-07T22:00:00Z'
picks=[dict(forecast_id=i,game_id=str(i),market='winner',matchup=f'A{i} @ B{i}',pick_label=f'A{i} wins',probability=.7,status='scheduled',grade='pending',generated_at='2026-11-07T21:30:00Z',tipoff='2026-11-07T23:00:00Z') for i in range(1,7)]
snapshot=dict(as_of=now,date='2026-11-07',skill_sha256=h.sha(h.SKILL.read_bytes()),helper_sha256=h.sha(Path(h.__file__).read_bytes()),sports={'nba':dict(freshness={'source_state':'fresh','failing':False},report={'picks':picks})})
p=root/'snapshot.json';p.write_text(json.dumps(snapshot))
def ticket(ids,stake=1):return dict(label='Main' if stake==3 else 'Variation',stake_usd=stake,legs=[dict(sport='nba',forecast_id=i,rationale='Audit fixture') for i in ids])
plan=dict(snapshot_path=str(p),review=dict(skill_sha256=snapshot['skill_sha256'],helper_sha256=snapshot['helper_sha256'],snapshot_sha256=h.sha(p.read_bytes()),markets_reviewed={'nba':{'winner':'All fixture winners reviewed'}},notes='Audit fixture only'),tickets=[ticket([1,2],3),ticket([1,3])])
checks=[]
def reject(label,fn):
    try:fn()
    except ValueError as e: checks.append(label+': rejected '+str(e))
    else:raise AssertionError(label+' accepted')
with patch.object(h,'snapshot',return_value=snapshot):
    result=h.check(plan);assert 'Shared picks' in result['text'];checks.append('Shared core accepted; archive warning present')
    for label,ts in [('duplicate',[ticket([1,2]),ticket([2,1])]),('same game',[ticket([1,1])]),('batch cap',[ticket([1,2],3),ticket([3,4],3)])]:
        bad=copy.deepcopy(plan);bad['tickets']=ts;reject(label,lambda:h.check(bad))
    bad=copy.deepcopy(plan);bad['review']['markets_reviewed']={};reject('missing market assessment',lambda:h.check(bad))
    snapshot['sports']['nba']['freshness']['source_state']='stale';reject('stale',lambda:h.check(plan));snapshot['sports']['nba']['freshness']['source_state']='fresh'
    snapshot['as_of']='2026-11-07T23:00:00Z';assert not h.eligible(picks[0],h.datetime.fromisoformat(snapshot['as_of']));snapshot['as_of']=now
# Capture actual renderer text; no logo requests.
texts=[];orig=ImageDraw.ImageDraw.text
def capture(self,xy,text,*args,**kw):texts.append(str(text));return orig(self,xy,text,*args,**kw)
with patch.object(telegram_card,'_logo',return_value=None),patch.object(ImageDraw.ImageDraw,'text',capture):
    photo=telegram_card.render_ticket_card(result['date'],result['resolved'])
(root/'shared-card.png').write_bytes(photo)
assert any('Distinct games across tickets' in t for t in texts)
assert not any('Shared picks' in t for t in texts)
checks.append('DEFECT reproduced: shared-core card falsely says Distinct games across tickets; warning absent')
ledger=telegram.ledger;sent=[]
class Bot:
    def __init__(self,*args):pass
    def identity(self):return {}
    def call(self,method,payload,**kwargs):sent.append((method,payload,kwargs));return {'message_id':123}
with patch.object(config,'telegram_settings',return_value={'TELEGRAM_CHAT_ID':'fixture','TELEGRAM_BOT_TOKEN':'fixture'}),patch.object(config,'data_dir',return_value=root),patch.object(telegram,'Telegram',Bot),patch.object(telegram,'controls',return_value=True),patch.object(telegram,'ledger',lambda:ledger(root/'receipts.sqlite')),patch.object(telegram_card,'render_ticket_card',return_value=photo),patch.object(h,'snapshot',return_value=snapshot):
    assert h.send(result)=='sent';assert h.send(result)=='existing_sent';assert len(sent)==1
    assert sent[0][0]=='sendPhoto' and 'Shared' not in sent[0][1]['caption']
    bad=copy.deepcopy(plan);bad['tickets']=[ticket([4,5],3)];reject('daily cap',lambda:h.send(h.check(bad)))
    bad['tickets']=[ticket([1,4])];reject('prior batch game reuse',lambda:h.send(h.check(bad)))
    checks.append('Mock send, receipt, duplicate prevention, daily budget and earlier-batch reservation verified')
archives=list((root/'reports/tickets').glob('*'))
assert all(a.suffix=='.json' for a in archives)
checks.append('Only JSON is archived by sender; sent PNG/caption not persisted as artifacts')
print('\n'.join(checks));print('Evidence:',root)
