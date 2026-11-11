import sys, json, sqlite3, hashlib, os
from pathlib import Path
srcdir, bdir = Path(sys.argv[1]), Path(sys.argv[2])
man = json.load(open(bdir/'manifest.json'))
def ro(p): return sqlite3.connect(f"file:{p}?mode=ro&immutable=1", uri=True)
def dump(c):
    out={}
    for (t,) in c.execute("select name from sqlite_master where type='table' order by name"):
        out[t]=c.execute(f"select * from {t} order by 1,2").fetchall() if False else sorted(map(repr,c.execute(f"select * from {t}").fetchall()))
    return out
def schema(c): return sorted(c.execute("select type,name,sql from sqlite_master").fetchall(), key=repr)
files=sorted(p.name for p in bdir.iterdir())
print('files',files)
assert sorted(['manifest.json']+[m['file'] for m in man['stores'].values()])==files, 'manifest/file mismatch'
for sport,m in man['stores'].items():
    b=ro(bdir/m['file']); s=ro(srcdir/m['file'])
    h=hashlib.sha256((bdir/m['file']).read_bytes()).hexdigest()
    r=dict(sport=sport, sha_ok=h==m['sha256'], bytes_ok=(bdir/m['file']).stat().st_size==m['bytes'],
      integrity=b.execute('pragma integrity_check').fetchone()[0],
      fk_backup=b.execute('pragma foreign_key_check').fetchall(), fk_src=s.execute('pragma foreign_key_check').fetchall(),
      uv=(b.execute('pragma user_version').fetchone()[0], s.execute('pragma user_version').fetchone()[0]),
      schema_eq=schema(b)==schema(s), ident=(b.execute('select sport from store_identity').fetchall(), s.execute('select sport from store_identity').fetchall()),
      dump_eq=dump(b)==dump(s),
      counts_ok=all(b.execute(f'select count(*) from {t}').fetchone()[0]==n for t,n in m['rows'].items()))
    print(r)
    assert r['sha_ok'] and r['bytes_ok'] and r['integrity']=='ok' and not r['fk_backup'] and r['schema_eq'] and r['dump_eq'] and r['counts_ok'] and r['ident'][0]==r['ident'][1]
print('ALL OK')
