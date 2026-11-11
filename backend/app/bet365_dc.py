"""Bet365 double chance from ESPN's odds feed (owner request, Nov 11 2026; docs/evidence/free-price-sources-2026-11-10).

ESPN's public core odds endpoint for a soccer match lists Bet365's pre-match double chance (home or draw / draw or away /
home or away) with labels, as fractional prices and without a time. It is a sportsbook QUOTE: shown with the time we
retrieved it, never changed, never mixed into a model probability. Seen for EPL, Bundesliga, Serie A, Ligue 1 and the
Champions League (one match each); other competitions are asked too and simply have no quote when Bet365 is absent.

Light requests: one GET per scheduled match kicking off within WINDOW, at most once per REFRESH per match, at most
MAX_PER_RUN per run, one a second (the hourly soccer cycle calls `fetch`). Every response is saved whole, once, under
`<data>/bet365-dc/` and never overwritten; reading picks the newest file retrieved at or before the moment asked.
"""
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path
from typing import Callable, Optional
from uuid import uuid4

from . import config

COMPETITIONS = ('eng.1', 'esp.1', 'ger.1', 'ita.1', 'fra.1', 'uefa.champions', 'uefa.europa')
URL = 'https://sports.core.api.espn.com/v2/sports/soccer/leagues/{c}/events/{e}/competitions/{e}/odds'
WINDOW = timedelta(hours=48)
REFRESH = timedelta(hours=2)
MAX_PER_RUN = 24
PROVIDER = 'Bet365'
LABEL = 'Bet365 quote'
KEYS = {'home_draw': 'preMatchDoubleChanceHomeOrDraw', 'draw_away': 'preMatchDoubleChanceDrawOrAway',
        'home_away': 'preMatchDoubleChanceHomeOrAway'}
FMT = '%Y-%m-%dT%H:%M:%SZ'


def folder() -> Path:
    return config.data_dir() / 'bet365-dc'


def _safe(match_id: str) -> str:
    return re.sub(r'[^A-Za-z0-9._-]', '_', match_id)


def _files(match_id: str) -> list[tuple[str, Path]]:
    """(retrieved_at, path) of every saved response for the match, oldest first."""
    d = folder() / _safe(match_id)
    out = []
    for p in d.glob('*.json') if d.is_dir() else []:
        m = re.match(r'(\d{8}T\d{6}Z)-', p.name)
        if m:
            out.append((datetime.strptime(m.group(1), '%Y%m%dT%H%M%SZ').strftime(FMT), p))
    return sorted(out)


def decimal(fractional) -> Optional[float]:
    """'15/4' -> 4.75; anything else (missing, malformed, not above evens-zero) -> None."""
    try:
        value = 1 + Fraction(str(fractional).strip())
    except (ValueError, ZeroDivisionError):
        return None
    return round(float(value), 4) if value > 1 else None


def parse(body: dict, home_espn: str, away_espn: str) -> dict:
    """{side: {'fractional', 'decimal'}} of Bet365's double chance, only when the feed's home/away teams are the
    match's; {} otherwise. Sides are the app's: home_draw, draw_away, home_away."""
    for item in body.get('items') or []:
        provider = (item.get('provider') or {}).get('name') or ''
        odds = item.get('bettingOdds') or {}
        if provider.replace(' ', '').lower() != 'bet365' or not odds:
            continue
        refs = [str((odds.get(k) or {}).get('$ref') or '') for k in ('homeTeam', 'awayTeam')]
        ids = [re.search(r'/teams/(\d+)', r) for r in refs]
        if not all(ids) or [i.group(1) for i in ids] != [str(home_espn), str(away_espn)]:
            return {}
        out = {}
        for side, key in KEYS.items():
            value = ((odds.get('teamOdds') or {}).get(key) or {}).get('value')
            dec = decimal(value)
            if dec is not None:
                out[side] = {'fractional': str(value), 'decimal': dec}
        return out
    return {}


def latest(match_id: str, home_team_id: str, away_team_id: str, as_of: str) -> Optional[dict]:
    """Bet365's double chance for the match from the newest response retrieved at or before `as_of`, or None."""
    for retrieved_at, path in reversed(_files(match_id)):
        if retrieved_at > as_of:
            continue
        try:
            saved = json.loads(path.read_text())
            prices = parse(saved['body'], home_team_id.split(':')[-1], away_team_id.split(':')[-1])
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None
        if not prices:
            return None
        return {'provider': PROVIDER, 'label': LABEL, 'retrieved_at': retrieved_at, 'prices': prices,
                'note': 'Sportsbook quote at the time shown; ESPN gives no time of its own.'}
    return None


def _get(url: str) -> tuple[int, bytes]:
    import httpx
    from .sources.espn import HEADERS
    resp = httpx.get(url, headers=HEADERS, timeout=20)
    return resp.status_code, resp.content


def fetch(conn, now: Optional[datetime] = None, *, get: Callable[[str], tuple[int, bytes]] = _get,
          pause: Callable[[float], None] = time.sleep) -> dict:
    """One light pass over upcoming club matches (see module docstring). Never raises for one match."""
    now = now or datetime.now(timezone.utc)
    stamp = now.strftime(FMT)
    marks = ','.join('?' * len(COMPETITIONS))
    rows = conn.execute(f"SELECT match_id, competition_id, kickoff_at FROM soccer_matches WHERE status='scheduled' AND "
                        f"competition_id IN ({marks}) AND kickoff_at > ? AND kickoff_at <= ? ORDER BY kickoff_at, match_id",
                        (*COMPETITIONS, stamp, (now + WINDOW).strftime(FMT))).fetchall()
    out = {'as_of': stamp, 'matches': len(rows), 'requests': 0, 'saved': 0, 'with_quote': 0, 'failed': 0, 'skipped_recent': 0}
    for r in rows:
        files = _files(r['match_id'])
        if files and files[-1][0] > (now - REFRESH).strftime(FMT):
            out['skipped_recent'] += 1
            continue
        if out['requests'] >= MAX_PER_RUN:
            out['deferred'] = out.get('deferred', 0) + 1
            continue
        if out['requests']:
            pause(1.1)
        event = r['match_id'].rsplit(':', 1)[-1]
        url = URL.format(c=r['competition_id'], e=event)
        out['requests'] += 1
        try:
            status, content = get(url)
            body = json.loads(content) if status == 200 else None
        except Exception:
            out['failed'] += 1
            continue
        if not isinstance(body, dict):
            out['failed'] += 1
            continue
        retrieved = datetime.now(timezone.utc) if get is _get else now
        d = folder() / _safe(r['match_id'])
        d.mkdir(parents=True, exist_ok=True)
        dest = d / f"{retrieved:%Y%m%dT%H%M%SZ}-{uuid4().hex}.json"
        tmp = dest.with_suffix('.tmp')
        tmp.write_text(json.dumps({'retrieved_at': retrieved.strftime(FMT), 'match_id': r['match_id'], 'url': url,
                                   'body': body}))
        tmp.rename(dest)
        out['saved'] += 1
        if any(((i.get('provider') or {}).get('name') or '').replace(' ', '').lower() == 'bet365'
               for i in body.get('items') or []):
            out['with_quote'] += 1
    return out


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv != ['fetch']:
        print('usage: python -m app.bet365_dc fetch', file=sys.stderr)
        return 2
    from . import db, release
    if release.enabled():
        info = release.check('soccer')
        if info['problems']:
            print(json.dumps({'status': 'refused', 'reason': 'release guard', 'problems': info['problems']}))
            return 1
    conn = db.connect_readonly('soccer')
    try:
        print(json.dumps(fetch(conn)))
    finally:
        conn.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
