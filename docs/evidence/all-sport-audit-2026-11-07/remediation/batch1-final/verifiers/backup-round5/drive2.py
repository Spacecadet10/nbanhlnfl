import sys; sys.argv=['x','/private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/9adf6978-28aa-417d-9e17-e71bdb15143a/scratchpad/final/backup-verify/o4/20261108T062027Z','/private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/9adf6978-28aa-417d-9e17-e71bdb15143a/scratchpad/final/backup-verify/round5']
exec(open('/private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/9adf6978-28aa-417d-9e17-e71bdb15143a/scratchpad/final/backup-verify/round5/probe.py').read())
import os
# (a) _describe fails after the nfl install (post-rename error)
d,orig=setup('desc'); rd=backup._describe; n={'c':0}
def bad(p):
    if str(p).endswith('nfl.sqlite') and 'restoring' not in str(p) and not str(p).startswith(str(B)):
        raise RuntimeError('describe boom')
    return rd(p)
backup._describe=bad
try: backup.restore(B,d,sports=SPORTS)
except BaseException as e: print('describe fail ->',type(e).__name__)
backup._describe=rd; print('originals intact',state_ok(d,orig))
# (b) rollback failures
allok=True
for fail_at in range(1,9):
  for rb in (1,2,3,4,5):
    d,orig,res,c=run(f'rb_{fail_at}_{rb}',fail_at,OSError,fail_rollback_at=rb)
    if res[0]!='BackupError': 
        print(f'fail#{fail_at} rb#{rb}: {res[0]} (rollback rename #{rb} never reached)'); continue
    msg=res[1]
    before=sorted(p.name for p in d.iterdir())
    # nothing deleted: every original byte string must still exist somewhere in d
    blobs=[p.read_bytes() for p in d.iterdir() if not p.name.endswith('jobs.lock')]
    lost=[k for k,v in orig.items() if v not in blobs]
    named=all(str(p) in msg for p in d.iterdir() if '.replaced-' in p.name)
    # follow steps literally
    steps=re.findall(r'^\s+\d+\. move (\S+) to (\S+)$', msg, re.M)
    for a,b in steps:
        assert not os.path.lexists(b), ('would overwrite',b)
        os.rename(a,b)
    for x in d.glob('*.restoring'): x.unlink()   # the message says staged copies may be deleted afterwards
    ok,diff=state_ok(d,orig)
    staged_left=sorted(p.name for p in d.iterdir() if p.name.endswith('.restoring'))
    stage_before=[x for x in before if x.endswith('.restoring')]
    print(f'fail#{fail_at} rb#{rb}: BackupError lost_originals={lost} every_safety_file_named={named} manual_steps={len(steps)} -> originals_intact_after_steps={ok} diff={diff} staged_before={stage_before} staged_after_steps={staged_left}')
    allok&=ok and not lost and named
print('ALL MANUAL RECOVERIES OK' if allok else 'SOME FAILED')
