"""DRatings on paper: their numbers for the same game at the moment the app records a forecast (owner request, Nov 11 2026).

A fair test needs both forecasts made at the same moment (docs/evidence/dratings-map-2026-11-11: DRatings' published
numbers are updated about a minute before the start, the app's about 90 minutes before). Every run (launchd, every
10 minutes) looks for canonical NBA/NHL winner forecasts recorded since the last capture; only when there is one does it
fetch that sport's public DRatings predictions page (one request per sport, 2 s apart, public page, robots allows it).
The page is saved whole, write-once (`<data>/dratings/pages/`), and one row per forecast is appended to
`<data>/dratings/captures.jsonl` (append-only; a forecast is captured once). Nothing here is shown in the app or on
Telegram, and nothing reads it except scripts/value_audit.py. DRatings' content is copyrighted with no reuse licence:
measuring only.
"""
import fcntl
import gzip
import hashlib
import html
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from . import config

PAGES = {'nba': 'https://www.dratings.com/predictor/nba-basketball-predictions/',
         'nhl': 'https://www.dratings.com/predictor/nhl-hockey-predictions/'}
USER_AGENT = 'personal research (non-commercial)'
LOOKBACK = timedelta(hours=3)           # forecasts recorded this recently and not yet captured are captured now
START_SLACK = timedelta(minutes=30)     # DRatings lists the start a few minutes after the schedule (NBA +10, NHL +7)
ALIAS = {'los angeles clippers': 'la clippers', 'utah hockey club': 'utah mammoth'}
FMT = '%Y-%m-%dT%H:%M:%SZ'


def folder() -> Path:
    return config.data_dir() / 'dratings'


def norm(name: str) -> str:
    n = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode().lower().replace('.', '').strip()
    return ALIAS.get(n, n)


def _cells(tr: str) -> list[str]:
    return [re.sub(r'\s+', ' ', html.unescape(re.sub('<[^>]+>', ' ', c))).strip()
            for c in re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)]


def upcoming_rows(page: str) -> list[dict]:
    """Rows of the page's upcoming-games table: start (UTC), away/home team, win % each, projected points/goals each.
    Columns are found by their header (NHL pages have an extra Goalies column)."""
    out = []
    for tb in re.findall(r'<table.*?</table>', page, re.S):
        heads = [re.sub(r'\s+', ' ', html.unescape(re.sub('<[^>]+>', ' ', h))).strip()
                 for h in re.findall(r'<th[^>]*>(.*?)</th>', tb, re.S)]
        heads = [re.split(r' (We use|Log loss|Using|Our)', h)[0] for h in heads]
        if 'Best ML' not in heads or 'Win' not in heads or not ({'Points', 'Goals'} & set(heads)):
            continue
        win, proj_col = heads.index('Win'), heads.index('Points' if 'Points' in heads else 'Goals')
        for tr in re.findall(r'<tr.*?</tr>', re.sub(r'<thead.*?</thead>', '', tb, flags=re.S), re.S):
            start = re.findall(r'datetime="([^"]+)"', tr)
            teams = [html.unescape(x).strip() for x in re.findall(r'href="/teams/[^"]+">([^<]+)</a>', tr)]
            c = _cells(tr)
            if not start or len(teams) != 2 or len(c) <= max(win, proj_col):
                continue
            pct = [float(x) / 100 for x in re.findall(r'([\d.]+)%', c[win])]
            proj = [float(x) for x in re.findall(r'\d+(?:\.\d+)?', c[proj_col])]
            if len(pct) != 2:
                continue
            out.append({'start': start[0], 'away': teams[0], 'home': teams[1], 'p_away': pct[0], 'p_home': pct[1],
                        'proj_away': proj[0] if len(proj) == 2 else None, 'proj_home': proj[1] if len(proj) == 2 else None})
    return out


def captured_ids() -> set:
    path = folder() / 'captures.jsonl'
    out = set()
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                r = json.loads(line)
                out.add((r['sport'], r['forecast_id']))
            except (ValueError, KeyError, TypeError):
                continue
    return out


def pending(conn, sport: str, now: datetime, done: set) -> list[sqlite3.Row]:
    """Canonical winner forecasts recorded in the last LOOKBACK for games not started, not yet captured."""
    rows = conn.execute(
        "SELECT f.forecast_id, f.game_id, f.generated_at, f.tipoff_at, f.p_a, h.name AS home, a.name AS away "
        "FROM game_forecasts f JOIN games g ON g.game_id = f.game_id JOIN teams h ON h.team_id = g.home_team_id "
        "JOIN teams a ON a.team_id = g.away_team_id WHERE f.market = 'winner' AND f.status = 'forecast' "
        "AND f.generated_at > ? AND f.generated_at <= ? AND f.tipoff_at > ? ORDER BY f.generated_at, f.forecast_id",
        ((now - LOOKBACK).strftime(FMT), now.strftime(FMT), now.strftime(FMT))).fetchall()
    return [r for r in rows if (sport, r['forecast_id']) not in done]


def match(row, dr_rows: list[dict]) -> Optional[dict]:
    """The DRatings row with the same away/home teams starting within START_SLACK after the scheduled start."""
    tip = datetime.strptime(row['tipoff_at'], FMT).replace(tzinfo=timezone.utc)
    hits = [d for d in dr_rows if norm(d['away']) == norm(row['away']) and norm(d['home']) == norm(row['home'])
            and timedelta(0) <= datetime.fromisoformat(d['start'].replace('Z', '+00:00')) - tip <= START_SLACK]
    return hits[0] if len(hits) == 1 else None


def _get(url: str) -> tuple[int, bytes]:
    import httpx
    resp = httpx.get(url, headers={'User-Agent': USER_AGENT}, timeout=30, follow_redirects=True)
    return resp.status_code, resp.content


def run(stores: dict, now: Optional[datetime] = None, *, get: Callable[[str], tuple[int, bytes]] = _get,
        pause: Callable[[float], None] = time.sleep, clock: Optional[Callable[[], datetime]] = None) -> dict:
    """One pass. `stores` = {sport: read-only connection}. Returns counts per sport."""
    clock = clock or (lambda: datetime.now(timezone.utc))
    now = now or clock()
    folder().mkdir(parents=True, exist_ok=True)
    out = {'as_of': now.strftime(FMT), 'sports': {}}
    with (folder() / '.capture.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return {**out, 'status': 'already_running'}
        done, requests = captured_ids(), 0
        for sport, conn in stores.items():
            todo = pending(conn, sport, now, done)
            if not todo:
                out['sports'][sport] = {'pending': 0}
                continue
            if requests:
                pause(2.0)
            requests += 1
            try:
                status, body = get(PAGES[sport])
            except Exception as exc:
                out['sports'][sport] = {'pending': len(todo), 'error': type(exc).__name__}
                continue
            got = clock()
            if status != 200:
                out['sports'][sport] = {'pending': len(todo), 'error': f'HTTP {status}'}
                continue
            digest = hashlib.sha256(body).hexdigest()
            pages = folder() / 'pages' / sport
            pages.mkdir(parents=True, exist_ok=True)
            dest = pages / f'{got:%Y%m%dT%H%M%SZ}-{digest[:12]}.html.gz'
            if not dest.exists():
                tmp = dest.with_suffix('.tmp')
                tmp.write_bytes(gzip.compress(body))
                tmp.rename(dest)
            rows = upcoming_rows(body.decode('utf-8', 'replace'))
            matched = 0
            with (folder() / 'captures.jsonl').open('a') as f:
                for r in todo:
                    d = match(r, rows)
                    matched += d is not None
                    rec = {'sport': sport, 'forecast_id': r['forecast_id'], 'game_id': r['game_id'],
                           'forecast_generated_at': r['generated_at'], 'tipoff_at': r['tipoff_at'],
                           'captured_at': got.strftime(FMT),
                           'lag_seconds': int((got - datetime.strptime(r['generated_at'], FMT).replace(tzinfo=timezone.utc)).total_seconds()),
                           'app_p_home': r['p_a'], 'page': f'pages/{sport}/{dest.name}', 'page_sha256': digest,
                           'matched': d is not None}
                    if d:
                        rec.update(dratings_start=d['start'], dr_p_home=d['p_home'], dr_p_away=d['p_away'],
                                   dr_proj_home=d['proj_home'], dr_proj_away=d['proj_away'])
                    f.write(json.dumps(rec, sort_keys=True) + '\n')
                f.flush()
                os.fsync(f.fileno())
            out['sports'][sport] = {'pending': len(todo), 'captured': len(todo), 'matched': matched, 'dratings_rows': len(rows)}
        out['requests'] = requests
    return {**out, 'status': 'ok'}


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv != ['run']:
        print('usage: python -m app.dratings_capture run', file=sys.stderr)
        return 2
    from . import db, release
    if config.protected_data_dir(config.data_dir()):
        # Only files are written, never a store: the checkout checks apply, the store-schema check does not.
        if not release.enabled() or release._checkout(release.code_root().resolve())[1]:
            raise SystemExit('release guard: the DRatings capture requires the clean release checkout')
    stores = {}
    try:
        for sport in ('nba', 'nhl'):
            if config.db_path(sport).is_file():
                stores[sport] = db.connect_readonly(sport)
        result = run(stores)
    finally:
        for c in stores.values():
            c.close()
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
