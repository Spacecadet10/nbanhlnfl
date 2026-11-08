import app, shutil, os, re, sys, hashlib
assert app.__file__.startswith('/Users/andry/Desktop/nba-predictor-batch1/backend/app/'), app.__file__
from pathlib import Path
from app import backup
B = next(Path(sys.argv[1]).iterdir()) if False else Path(sys.argv[1])
R = Path(sys.argv[2])
H = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
SPORTS = ['nba', 'nfl']
def setup(name):
    d = R/name; shutil.rmtree(d, ignore_errors=True); d.mkdir()
    for s in SPORTS:
        (d/f'{s}.sqlite').write_bytes(b'ORIG-MAIN-'+s.encode()*100)
        (d/f'{s}.sqlite-wal').write_bytes(b'ORIG-WAL-'+s.encode())
        (d/f'{s}.sqlite-shm').write_bytes(b'ORIG-SHM-'+s.encode())
    return d, {p.name: p.read_bytes() for p in d.iterdir()}
def state_ok(d, orig):
    now = {p.name: p.read_bytes() for p in d.iterdir() if not p.name.endswith('-jobs.lock')}
    return now == orig, sorted(set(now) ^ set(orig))
real_rename = Path.rename
def run(name, fail_at, exc=OSError, fail_rollback_at=None, clash=False):
    d, orig = setup(name)
    calls = {'n': 0, 'failed': False, 'after': 0}
    def ren(self, t):
        calls['n'] += 1
        if calls['n'] == fail_at and not calls['failed']:
            calls['failed'] = True; raise exc(5, 'injected') if exc is OSError else exc()
        if calls['failed']:
            calls['after'] += 1
            if fail_rollback_at and calls['after'] == fail_rollback_at: raise OSError(5, 'injected rollback')
        return real_rename(self, t)
    Path.rename = ren
    lx = os.path.lexists; seen = {}
    if clash:
        def lex(p):
            p = str(p)
            if 'nfl.sqlite-shm.replaced-' in p:
                seen[p] = seen.get(p, 0)+1
                return seen[p] > 1
            return lx(p)
        backup.os.path.lexists = lex
    try:
        try: backup.restore(B, d, sports=SPORTS); res = ('NO-ERROR', '')
        except BaseException as e: res = (type(e).__name__, str(e))
    finally:
        Path.rename = real_rename
        if clash: backup.os.path.lexists = lx
    return d, orig, res, calls
