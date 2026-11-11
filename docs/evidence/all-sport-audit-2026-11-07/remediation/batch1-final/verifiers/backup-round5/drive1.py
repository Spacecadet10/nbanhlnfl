import sys; sys.argv=['x','/private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/9adf6978-28aa-417d-9e17-e71bdb15143a/scratchpad/final/backup-verify/o4/20261108T062027Z','/private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/9adf6978-28aa-417d-9e17-e71bdb15143a/scratchpad/final/backup-verify/round5']
exec(open('/private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/9adf6978-28aa-417d-9e17-e71bdb15143a/scratchpad/final/backup-verify/round5/probe.py').read())
# baseline: count renames in a successful restore
d,orig,res,c=run('base',fail_at=10**9); print('baseline',res[0],'renames',c['n'])
bad=0
for k in range(1,c['n']+1):
    for exc in (OSError, KeyboardInterrupt):
        d,orig,res,c2=run(f'k{k}_{exc.__name__}',k,exc)
        ok,diff=state_ok(d,orig); bad+= (not ok)
        print(f'fail rename#{k:2d} {exc.__name__:17s} raised={res[0]:14s} originals_intact={ok} leftover_diff={diff}')
d,orig,res,c2=run('clash',10**9,clash=True); ok,diff=state_ok(d,orig)
print('clash BackupError: raised=',res[0],res[1][:90],'| originals_intact=',ok,diff); bad+=(not ok)
print('FAILURES',bad)
