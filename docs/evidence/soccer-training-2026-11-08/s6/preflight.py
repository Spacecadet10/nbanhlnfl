"""Read-only preflight of the one-shot reserved test (does NOT call run_test, writes no log, reads no reserved outcome).
Usage (NBA_DATA_DIR = full store): preflight.py <sealed_candidate.json> <planned out dir>"""
import sys, json, sqlite3, os
from pathlib import Path
sys.path.insert(0, '.')
from app import config, soccer_eval as ev
sealed = json.loads(Path(sys.argv[1]).read_text())
out = Path(sys.argv[2])
conn = sqlite3.connect(f"file:{config.db_path('soccer')}?mode=ro", uri=True); conn.row_factory = sqlite3.Row
res = {}
def step(name, fn):
    try: fn(); res[name] = 'pass'
    except Exception as e: res[name] = f'FAIL {type(e).__name__}: {e}'
step('seal valid', lambda: None if ev._valid_seal(sealed) else (_ for _ in ()).throw(ValueError('invalid')))
step('output folder absent', lambda: None if not out.exists() else (_ for _ in ()).throw(ValueError(str(out))))
step('no earlier test-run start', lambda: None if not ev._already_run(ev.access_log_path(), None) else (_ for _ in ()).throw(ValueError('started')))
starts, seals = ev._entries(ev.experiments_log_path(), 'start'), ev._entries(ev.experiments_log_path(), 'sealed')
step('exactly one development start for this candidate', lambda: None if len(starts) == 1 and starts[0]['run'] == sealed['development_run'] else (_ for _ in ()).throw(ValueError(len(starts))))
step('this candidate is the only seal', lambda: None if len(seals) == 1 and seals[0]['candidate_sha256'] == sealed['sha256'] else (_ for _ in ()).throw(ValueError(len(seals))))
step('code, protocol and open data unchanged', lambda: ev.check_frozen(conn, sealed))
step('dataset pins', lambda: ev.check_dataset_pins(conn))
step('reserved seasons present (counts only)', lambda: ev.check_reserved_present(conn))
print(json.dumps({"store": str(config.db_path('soccer')), "candidate_sha256": sealed['sha256'], "access_log": str(ev.access_log_path()),
                  "access_log_exists": ev.access_log_path().exists(), "checks": res}, indent=1))
