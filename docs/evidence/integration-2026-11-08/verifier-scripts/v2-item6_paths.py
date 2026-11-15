import socket, os, sys
def _blocked(*a, **k): raise RuntimeError('network blocked')
socket.socket.connect = _blocked; socket.create_connection = _blocked
from pathlib import Path
from app import config
S = sys.argv[1]
REPO = '/Users/andry/Desktop/nba-predictor-sprint1'
orig_root = config.REPO_ROOT
def show(label, env, root=None):
    if env is None: os.environ.pop('NBA_DATA_DIR', None)
    else: os.environ['NBA_DATA_DIR'] = env
    config.REPO_ROOT = Path(root) if root else orig_root
    led = config.telegram_ledger_path()
    cred = config.REPO_ROOT / '.env.telegram'   # path computed only; file is NOT opened
    prod = Path(REPO) / '.telegram-delivery.sqlite'
    print(f"{label}\n  NBA_DATA_DIR={env!r} REPO_ROOT={config.REPO_ROOT}\n  data_dir={config.data_dir()}\n  ledger={led}  (== production ledger: {led == prod}; resolves into repo data/: {str(led.resolve()).startswith(REPO + '/data')})\n  credentials file read by telegram_settings={cred}")
show('A unset', None)
show('B = repo data', REPO + '/data')
show('C = repo data + trailing slash', REPO + '/data/')
show('D = symlink -> repo data', S + '/d6/link_to_repo_data')
show('E = temp copy', S + '/d6/copy')
show('F release checkout REPO_ROOT=/Users/andry/nba-predictor-live, NBA_DATA_DIR=sprint1 data (launchd value)', REPO + '/data', '/Users/andry/nba-predictor-live')
show('G release checkout, NBA_DATA_DIR unset', None, '/Users/andry/nba-predictor-live')
show('H dev copy (temp NBA_DATA_DIR) in this checkout', S + '/d6/copy')
