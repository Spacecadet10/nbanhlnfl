"""Development copy: only seasons <= 2022 (store rows AND raw files). Usage: build_open_season_store.py <full data dir> <new dev dir>"""
import sys, os, shutil, json
sys.path.insert(0, '.')
full, dev = sys.argv[1], sys.argv[2]
assert not os.path.exists(dev) and 'nba-predictor-research-data' in dev and os.environ['NBA_DATA_DIR'] == dev
os.makedirs(dev)
from app import db, soccer_history as h, soccer_eval as ev
c = db.connect(sport='soccer'); db.migrate(c); db.claim_store(c, 'soccer'); c.commit()
print('copied files', h.copy_provenance(c, os.path.join(full, 'soccer.sqlite'), max_season=ev.LAST_OPEN))
for (path,) in c.execute('SELECT path FROM raw_responses'):
    os.makedirs(os.path.dirname(os.path.join(dev, 'raw', path)), exist_ok=True)
    shutil.copy2(os.path.join(full, 'raw', path), os.path.join(dev, 'raw', path))
print(json.dumps(h.replay(c)))
print('max season rows', c.execute('select max(season) from soccer_hist_matches').fetchone()[0],
      'max season files', c.execute("select max(season) from soccer_source_files where kind='results'").fetchone()[0],
      'raw files', sum(len(f) for _, _, f in os.walk(os.path.join(dev, 'raw'))))
ev.check_dataset_pins(c)
print('open_data_digest dev ', ev.open_data_digest(c))
import sqlite3
f = sqlite3.connect(f"file:{os.path.join(full, 'soccer.sqlite')}?mode=ro", uri=True); f.row_factory = sqlite3.Row
print('open_data_digest full', ev.open_data_digest(f))
