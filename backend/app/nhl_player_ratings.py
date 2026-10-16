"""NHL skater availability member: delivered box scores plus timestamped injury snapshots.

Skater rating = regularized cumulative plus/minus per 60 minutes (1200-minute zero prior).
This is a hockey-specific starting feature, not an adjusted on-ice/xG rating or proven edge.
Goalie injuries are explicit counts; starting goalie identity is never inferred from the result.
"""
import hashlib
import json
import re
import time
import unicodedata
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

from . import raw_cache
from .db import utc_now
from .models.score_candidates import GoalsGlm
from .sources import nhl_injuries, nhl_pbp

MEMBER = "nhl_players_injuries"
FEATURES = ("player_home", "player_away", "absent_home", "absent_away", "goalie_absent_home",
            "goalie_absent_away", "report_missing", "snapshot_age_hours", "unresolved")
MAX_AGE_HOURS = 24.
PRIOR_MINUTES = 1200.


def norm(value):
    return re.sub('[^a-z]', '', unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode().lower())


# ESPN injury snapshots use the team displayName; NHL-API boxes use placeName + ' ' + commonName.
# norm() already folds accents ("Montréal"->"montreal"), punctuation ("St. Louis") and spacing; this map
# collapses the remaining franchise-move spellings so a team listed by one source still matches its box
# from the other. Relocated names never co-occur (a snapshot is time-gated to within MAX_AGE_HOURS of the
# game), so folding them to one key cannot merge two distinct teams.
TEAM_ALIASES = {
    'arizonacoyotes': 'utahnhl', 'phoenixcoyotes': 'utahnhl', 'utah': 'utahnhl',
    'utahhockeyclub': 'utahnhl', 'utahmammoth': 'utahnhl',
}


def split_name(value):
    """(first name as written, normalized surname); the first whitespace-separated token is the first name."""
    parts = (value or '').split(None, 1)
    return (parts[0], norm(parts[1])) if len(parts) == 2 else (value or '', '')


# Same player, written differently by ESPN and the NHL API, where neither first name is a prefix of the other. Each pair
# was observed in the 2016-2026 archive; the last group are transliterations (Yevgeni/Evgeni follows Yegor/Egor).
NICKNAMES = {frozenset(p) for p in (
    ('joe', 'joseph'), ('mike', 'michael'), ('jake', 'jacob'), ('nick', 'nicholas'), ('matt', 'mathew'),
    ('freddy', 'frederick'), ('sammy', 'samuel'), ('tommy', 'thomas'), ('vinnie', 'vincent'), ('quinn', 'quintin'),
    ('tony', 'anthony'), ('jj', 'janis'),
    ('alexei', 'alexey'), ('maxim', 'maksim'), ('artyom', 'artem'), ('yegor', 'egor'), ('yevgeni', 'evgeni'),
    ('yevgeny', 'evgeny'))}


def _initials(written):
    parts = [p for p in re.split(r'[\s.\-]+', written) if p]
    return ''.join(norm(p)[:1] for p in parts) if len(parts) > 1 else None


def first_names_compatible(a, b):
    """Two written first names of the same person: equal, one a prefix of the other with 3+ letters (Matt/Matthew,
    Alex/Alexander, Josh/Joshua), the initials of a multi-part name (P.O/Pierre-Olivier, JJ/John-Jason), or a listed
    nickname or transliteration. A shared initial alone is not enough (Josh/Jordan, Jeremie/Emile, Carter/Matt): the
    reported player may not be in any delivered box yet, and a teammate must not take his absence."""
    x, y = sorted((norm(a), norm(b)), key=len)
    if not x:
        return False
    return (x == y or (len(x) >= 3 and y.startswith(x)) or frozenset((x, y)) in NICKNAMES
            or (len(x) >= 2 and x in (_initials(a), _initials(b))))


def team_key(name):
    """Normalized cross-source team identity for matching, or None when the team is unknown."""
    if not name:
        return None
    key = norm(name)
    return TEAM_ALIASES.get(key, key)


def validate_player_teams(teams):
    seen = set()
    if len(teams) != 2:
        raise ValueError('incomplete player box: expected two teams')
    for players in teams.values():
        for player in players:
            if player['id'] in seen:
                raise ValueError('duplicate player in box')
            seen.add(player['id'])
        if sum(not p['goalie'] for p in players) < 15 or not any(p['goalie'] for p in players):
            raise ValueError('incomplete player box')


# A sanity bound, not a game length: the longest NHL games ran ~176 minutes (1936), and five-overtime playoff games put
# goalies above 150 (nhl:2019030121, 150.4). Anything above this is a corrupt time on ice.
MAX_TOI_MINUTES = 200


def box_players(payload, roster):
    if payload.get('gameState') not in ('OFF', 'FINAL'):
        raise ValueError('unfinished player box')
    names = {str(p['playerId']): p['firstName']['default'] + ' ' + p['lastName']['default']
             for p in roster.get('rosterSpots', [])}
    teams, team_names = {}, {}
    for side in ('homeTeam', 'awayTeam'):
        team = payload[side]
        tid = 'nhl:' + str(team['id'])
        team_names[tid] = (team['placeName']['default'] + ' ' + team['commonName']['default']).strip()
        players = []
        for group in ('forwards', 'defense', 'goalies'):
            for p in payload['playerByGameStats'][side][group]:
                pid = str(p['playerId'])
                if pid not in names:
                    raise ValueError(f'missing full name for NHL player {pid}')
                mm, ss = p['toi'].split(':')
                if int(mm) < 0 or not 0 <= int(ss) < 60:
                    raise ValueError('invalid time on ice')
                minutes = int(mm) + int(ss) / 60
                if minutes < 0 or minutes > MAX_TOI_MINUTES:
                    raise ValueError('invalid time on ice')
                players.append({'id': pid, 'name': names[pid], 'minutes': minutes,
                                'plus_minus': int(p.get('plusMinus', 0)), 'goalie': group == 'goalies'})
        if not any(p['minutes'] > 0 and not p['goalie'] for p in players):
            raise ValueError('missing skater time on ice')
        teams[tid] = players
    validate_player_teams(teams)
    return teams, team_names


def collect_boxes(conn, start, end, *, limit=None, delay=2., directory=None):
    import httpx
    if limit is None:
        return collect_bulk_boxes(conn, start, end, delay=delay, directory=directory)
    directory = Path(directory or nhl_injuries.root() / 'boxes')
    directory.mkdir(parents=True, exist_ok=True)
    rows = conn.execute("SELECT game_id FROM games WHERE status='final' AND season BETWEEN ? AND ? "
                        "AND season_type='regular' ORDER BY tipoff_at", (start, end)).fetchall()
    if limit is not None:
        rows = rows[:limit]
    out = {'stored': 0, 'cached': 0, 'failed': 0, 'errors': []}
    with httpx.Client(timeout=45) as client:
        for row in rows:
            gid = row['game_id']; ident = nhl_pbp.api_id(gid)
            target = directory / f'{ident}.json'
            if target.exists():
                out['cached'] += 1
                continue
            try:
                time.sleep(max(1., delay))
                url = f'https://api-web.nhle.com/v1/gamecenter/{ident}/boxscore'
                response = client.get(url); response.raise_for_status()
                cached = conn.execute("SELECT raw_id FROM raw_responses WHERE source=? AND request_key LIKE ? "
                                      "ORDER BY raw_id LIMIT 1", (nhl_pbp.SOURCE, f'game={ident}&%')).fetchone()
                if cached:
                    pbp = raw_cache.load(conn, cached['raw_id'])
                else:
                    time.sleep(max(1., delay))
                    rp = client.get(nhl_pbp.ENDPOINT.format(gid=ident)); rp.raise_for_status(); pbp = rp.content
                teams, names = box_players(response.json(), json.loads(pbp))
                raw_hashes = []
                for kind, body in (('box', response.content), ('pbp', pbp)):
                    sha = hashlib.sha256(body).hexdigest(); raw_hashes.append(sha)
                    path = directory / f'{ident}-{kind}-{sha}.raw'
                    if not path.exists():
                        with path.open('xb') as f: f.write(body)
                meta = {'game_id': gid, 'teams': teams, 'names': names, 'raw_hashes': raw_hashes,
                        'retrieved_at': utc_now(), 'endpoint': url,
                        'availability_basis': 'historical box delivered only with the stored result; publication time assumed'}
                with target.open('x') as f: json.dump(meta, f)
                out['stored'] += 1
            except Exception as exc:
                out['failed'] += 1; out['errors'].append({'game_id': gid, 'error': str(exc)})
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (403, 429): break
    return out


def bulk_players(game, skaters, goalies):
    """Normalize one game's official per-game stats; reject incomplete teams instead of healthy defaults."""
    import math
    teams = {game['home_team_id']: [], game['away_team_id']: []}
    for goalie, rows in ((False, skaters), (True, goalies)):
        for p in rows:
            if p['gameId'] != int(game['game_id'].split(':')[1]):
                continue
            if p['homeRoad'] not in ('H', 'R'):
                raise ValueError('unknown home/road team')
            tid = game['home_team_id'] if p['homeRoad'] == 'H' else game['away_team_id']
            mins = float(p['timeOnIce'] if goalie else p['timeOnIcePerGame']) / 60
            if not math.isfinite(mins) or not 0 <= mins <= MAX_TOI_MINUTES:
                raise ValueError('invalid bulk time on ice')
            teams[tid].append({'id': str(p['playerId']), 'name': p['goalieFullName'] if goalie else p['skaterFullName'],
                              'minutes': mins, 'plus_minus': 0 if goalie else int(p['plusMinus']), 'goalie': goalie})
    validate_player_teams(teams)
    return teams


def collect_bulk_boxes(conn, start, end, *, delay=2., directory=None, log=print):
    """128-game batches stay below the stats API's 10,000-row cap. No fitting or DB mutation."""
    import httpx
    directory = Path(directory or nhl_injuries.root() / 'boxes')
    directory.mkdir(parents=True, exist_ok=True)
    games = [dict(g) for g in conn.execute("SELECT game_id,season,home_team_id,away_team_id FROM games "
             "WHERE status='final' AND season BETWEEN ? AND ? AND season_type IN ('regular','postseason') "
             "ORDER BY game_id", (start, end))]
    names = {r['team_id']: r['name'] for r in conn.execute('SELECT team_id,name FROM teams')}
    pending = [g for g in games if not (directory / (nhl_pbp.api_id(g['game_id']) + '.json')).exists()]
    out = {'stored': 0, 'cached': len(games) - len(pending), 'failed': 0, 'errors': []}
    with httpx.Client(timeout=60) as client:
        for offset in range(0, len(pending), 128):
            batch = pending[offset:offset+128]
            first, last = [int(nhl_pbp.api_id(g['game_id'])) for g in (batch[0], batch[-1])]
            # Gaps between pending games can span a whole cached season. Filter exact IDs to bound the response.
            expression = 'gameId in (' + ','.join(nhl_pbp.api_id(g['game_id']) for g in batch) + ')'
            data, raw_files, digests, endpoints = {}, [], [], []
            try:
                for group in ('skater', 'goalie'):
                    params = {'isAggregate': 'false', 'isGame': 'true', 'start': 0, 'limit': -1,
                              'cayenneExp': expression}
                    time.sleep(max(1., delay))
                    resp = client.get(f'https://api.nhle.com/stats/rest/en/{group}/summary', params=params)
                    resp.raise_for_status()
                    payload = resp.json()
                    if len(payload['data']) != payload['total'] or payload['total'] >= 10000:
                        raise ValueError('truncated NHL bulk response')
                    data[group] = payload['data']
                    sha = hashlib.sha256(resp.content).hexdigest()
                    name = f'bulk-{group}-{first}-{last}-{sha}.raw'
                    path = directory / name
                    if not path.exists():
                        with path.open('xb') as f: f.write(resp.content)
                    raw_files.append(name); digests.append(sha); endpoints.append(str(resp.url))
                for game in batch:
                    try:
                        teams = bulk_players(game, data['skater'], data['goalie'])
                        meta = {'game_id': game['game_id'], 'home_team_id': game['home_team_id'],
                                'away_team_id': game['away_team_id'], 'teams': teams, 'names': {t: names[t] for t in teams},
                                'raw_files': raw_files, 'raw_hashes': digests, 'retrieved_at': utc_now(),
                                'endpoints': endpoints, 'availability_basis': 'historical per-game stats delivered with stored result; publication time assumed'}
                        path = directory / (nhl_pbp.api_id(game['game_id']) + '.json')
                        with path.open('x') as f: json.dump(meta, f)
                        out['stored'] += 1
                    except Exception as exc:
                        out['failed'] += 1
                        out['errors'].append({'game_id': game['game_id'], 'error': str(exc)})
                        log(json.dumps(out['errors'][-1]))
            except Exception as exc:
                out['failed'] += 1
                out['errors'].append({'first': first, 'last': last, 'error': str(exc)})
                log(json.dumps(out['errors'][-1]))
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (403, 429):
                    break
            log(json.dumps({k: v for k, v in out.items() if k != 'errors'}))
    (directory / 'last-bulk-run.json').write_text(json.dumps(out, indent=2))
    return out


class Inputs:
    def __init__(self, directory=None):
        self.root = Path(directory or nhl_injuries.root())
        self.boxes = {}; digest = hashlib.sha256()
        raw_payloads = {}
        verified_raw = {}  # Bulk responses are shared by many games; hash each file once per load.
        for p in sorted((self.root / 'boxes').glob('[0-9]*.json')):
            body = p.read_bytes(); data = json.loads(body)
            hashes = data.get('raw_hashes', [])
            if len(hashes) != 2:
                raise ValueError('NHL player box needs two original source hashes')
            files = data.get('raw_files')
            if files is None:  # Original individual box + play-by-play cache format.
                ident = nhl_pbp.api_id(data['game_id'])
                files = [f'{ident}-{kind}-{sha}.raw' for kind, sha in zip(('box', 'pbp'), hashes)]
            if len(files) != len(hashes):
                raise ValueError('NHL player box raw file/hash count mismatch')
            for name, expected in zip(files, hashes):
                if Path(name).name != name:
                    raise ValueError('NHL player box raw file must be a local filename')
                if name not in verified_raw:
                    raw_bytes = (p.parent / name).read_bytes()
                    verified_raw[name] = hashlib.sha256(raw_bytes).hexdigest()
                if verified_raw[name] != expected:
                    raise ValueError('NHL player box raw hash mismatch')
                if name not in raw_payloads:
                    raw_payloads[name] = json.loads(raw_bytes)
            if p.stem != nhl_pbp.api_id(data['game_id']):
                raise ValueError('normalized player box game ID disagrees with filename')
            source, roster = [raw_payloads[name] for name in files]
            if data.get('raw_files') is not None:
                # Both collectors serialize teams in home/away order; older bulk caches lack explicit side IDs.
                tids = list(data['teams'])
                if len(tids) != 2:
                    raise ValueError('normalized player box needs two teams')
                game = {'game_id': data['game_id'], 'home_team_id': data.get('home_team_id', tids[0]),
                        'away_team_id': data.get('away_team_id', tids[1])}
                expected_teams = bulk_players(game, source['data'], roster['data'])
            else:
                if str(source.get('id')) != nhl_pbp.api_id(data['game_id']):
                    raise ValueError('normalized player box game ID disagrees with source')
                expected_teams, expected_names = box_players(source, roster)
                game = {'home_team_id': 'nhl:' + str(source['homeTeam']['id']),
                        'away_team_id': 'nhl:' + str(source['awayTeam']['id'])}
                if data['names'] != expected_names:
                    raise ValueError('normalized player box team names disagree with source')
            if data['teams'] != expected_teams:
                raise ValueError('normalized player box disagrees with raw source')
            for side in ('home_team_id', 'away_team_id'):
                if side in data and data[side] != game[side]:
                    raise ValueError('normalized player box sides disagree with source')
                data[side] = game[side]
            self.boxes[data['game_id']] = data
            digest.update(body)
        self.digest = digest.hexdigest()
        self.snapshots = []
        directory = self.root / 'injuries'
        self.skipped_live = 0
        for p in sorted([*directory.glob('[0-9]*.json'), *directory.glob('live-[0-9]*.json')]):
            try:
                item = json.loads(p.read_text())
            except ValueError:
                if p.name.startswith('live-'):      # a torn live write (pre-atomic or a crash): skipped and counted
                    self.skipped_live += 1
                    continue
                raise
            raw = (p.parent / item['raw_file']).read_bytes()
            if hashlib.sha256(raw).hexdigest() != item['sha256']:
                raise ValueError('NHL injury raw hash mismatch')
            if item.get('source') == 'live':        # live capture: stamped at retrieval, from the live page only
                if not nhl_injuries.live_source(item['response_url']) or item['captured_at'] != item['retrieved_at']:
                    raise ValueError('NHL live injury capture source or timestamp mismatch')
            elif nhl_injuries.actual_capture(item['response_url'], item['requested_capture']) != item['captured_at']:
                raise ValueError('NHL injury timestamp mismatch')
            layout, rows, teams = nhl_injuries.parse(raw)
            if (item.get('layout'), item.get('rows'), item.get('teams')) != (layout, rows, teams):
                raise ValueError('NHL parsed injury metadata disagrees with raw source; rebuild capture metadata')
            self.snapshots.append(item)
        self.snapshots.sort(key=lambda s: s['captured_at'])

    def snapshot(self, cutoff):
        import bisect
        pos = bisect.bisect_right([s['captured_at'] for s in self.snapshots], cutoff) - 1
        if pos < 0: return None, MAX_AGE_HOURS
        item = self.snapshots[pos]
        age = (datetime.fromisoformat(cutoff.replace('Z', '+00:00')) -
               datetime.fromisoformat(item['captured_at'].replace('Z', '+00:00'))).total_seconds() / 3600
        return (item if age <= MAX_AGE_HOURS else None), min(age, MAX_AGE_HOURS)


def missing_inputs():
    root = nhl_injuries.root()
    if not any((root / 'boxes').glob('[0-9]*.json')) or not any([*(root / 'injuries').glob('[0-9]*.json'),
                                                                     *(root / 'injuries').glob('live-[0-9]*.json')]):
        return f'needs NHL player boxes and timestamp-verified injury archives under {root}; run nhl-player-inputs'
    return None


class NhlPlayersInjuries(GoalsGlm):
    name = MEMBER

    def __init__(self, elo_cfg, rating_key=None, inputs=None):
        super().__init__(elo_cfg, rating_key)
        self._inputs = inputs
        self.player_stats = {}
        self.recent_players = defaultdict(lambda: deque(maxlen=5))
        self.team_names = {}
        # Identity lasts beyond the short expected-lineup window. Updated from delivered results only.
        self.player_history = {}
        self.name_index = defaultdict(set)
        self.surname_index = defaultdict(set)      # normalized surname -> player ids (for first-name variants)
        self.first_names = defaultdict(set)        # player id -> first names as written in delivered boxes
        self.delivered = set()
        self.feature_cutoffs = {}

    def __getstate__(self):
        return {k: v for k, v in vars(self).items() if k != '_inputs'}

    def __setstate__(self, state):
        vars(self).update(state); self._inputs = None

    @property
    def inputs(self):
        if self._inputs is None: self._inputs = Inputs()
        return self._inputs

    def player_features(self, g):
        if not g.cutoff_at or g.cutoff_at >= g.tipoff_at:
            raise ValueError('NHL availability requires an explicit pregame cutoff')
        snap, age = self.inputs.snapshot(g.cutoff_at)
        features = dict.fromkeys(FEATURES, 0.)
        features['snapshot_age_hours'] = age
        snap_team_keys = {team_key(t) for t in snap['teams']} if snap is not None else set()
        # A player the pre-cutoff report uniquely places on a different team (traded while injured, before any
        # delivered box shows the move) is no longer part of his former team's expected lineup.
        moved = set()
        for row in (snap['rows'] if snap is not None else ()):
            rkey = team_key(row['team'])
            ids = self.name_index.get(norm(row['player']), set())
            ids = {p for p in ids if team_key(self.team_names.get(self.player_history[p]['team'])) == rkey} or ids
            if len(ids) == 1:
                pid = next(iter(ids))
                if team_key(self.team_names.get(self.player_history[pid]['team'])) != rkey:
                    moved.add(pid)
        for side, tid in (('home', g.home_team_id), ('away', g.away_team_id)):
            appearances = defaultdict(list)
            for _, _, box in self.recent_players[tid]:
                for player in box:
                    history = self.player_history.get(player['id'])
                    if player['minutes'] > 0 and history and history['team'] == tid and player['id'] not in moved:
                        appearances[player['id']].append(player)
            roster = {pid: rows[-1] for pid, rows in appearances.items()}
            tkey = team_key(self.team_names.get(tid))
            listed = snap is not None and tkey is not None and tkey in snap_team_keys
            if not listed or not roster: features['report_missing'] = 1.
            weights = {}
            if listed:
                for row in snap['rows']:
                    if team_key(row['team']) != tkey: continue
                    ids = self.name_index.get(norm(row['player']), set())
                    # Prefer identities last observed on this team; a unique global identity can be
                    # assigned by the pre-cutoff report itself (e.g. traded while injured).
                    local = {pid for pid in ids if self.player_history[pid]['team'] == tid}
                    ids = local or ids or self.variant_ids(row['player'], tid)
                    if len(ids) != 1 or row['weight'] is None:
                        features['unresolved'] += 1.; features['report_missing'] = 1.; continue
                    pid = next(iter(ids))
                    weights[pid] = max(weights.get(pid, 0.), row['weight'])
            # Injury burden includes long absences, even when the player has left the expected lineup.
            # Identity evidence and expected minutes are deliberately separate.
            for pid, weight in weights.items():
                history = self.player_history[pid]
                if history['goalie']:
                    features[f'goalie_absent_{side}'] += weight
                elif weight:
                    minutes = [m for _, _, season, m in history['minutes'] if season in (g.season, g.season - 1)]
                    if not minutes:
                        features['unresolved'] += 1.; features['report_missing'] = 1.
                        continue
                    features[f'absent_{side}'] += sum(minutes) / len(minutes) / 60 * weight
            for pid, player in roster.items():
                weight = weights.get(pid, 0.)
                if player['goalie']:
                    continue
                mins = sum(p['minutes'] for p in appearances[pid]) / len(appearances[pid])
                pm, played = self.player_stats.get(pid, (0., 0.))
                rating = 60 * pm / (PRIOR_MINUTES + played)
                features[f'player_{side}'] += mins / 60 * rating * (1 - weight)
        return features

    def _prepare(self, g):
        if self.feature_cutoffs.get(g.game_id) != g.cutoff_at:
            self.pending.pop(g.game_id, None)
            self.feature_cutoffs[g.game_id] = g.cutoff_at
        return super()._prepare(g)

    def _vector(self, g):
        f = self.player_features(g)
        return super()._vector(g) + [f[n] for n in FEATURES]

    def update(self, r):
        box = self.inputs.boxes.get(r.game.game_id)
        if box is not None:
            if (set(box['teams']) != {r.game.home_team_id, r.game.away_team_id}
                    or any(side in box and box[side] != getattr(r.game, side)
                           for side in ('home_team_id', 'away_team_id'))):
                raise ValueError('NHL player box teams disagree with delivered game')
        super().update(r)
        if not box: return
        for tid, players in box['teams'].items():
            recent = list(self.recent_players[tid])
            entry = (r.game.tipoff_at, r.game.game_id, players)
            if not recent or entry[:2] >= recent[-1][:2]:
                self.team_names[tid] = box['names'][tid]
            self.recent_players[tid] = deque(sorted([*recent, entry], key=lambda x: x[:2])[-5:], maxlen=5)
            for p in players:
                pid = p['id']
                self.name_index[norm(p['name'])].add(pid)
                first, last = split_name(p['name'])
                if last:
                    self.surname_index[last].add(pid); self.first_names[pid].add(first)
                history = self.player_history.setdefault(pid, {'team': tid, 'goalie': p['goalie'],
                                                               'minutes': deque(maxlen=10), 'last_tipoff': ''})
                if r.game.tipoff_at >= history['last_tipoff']:
                    history.update(team=tid, goalie=p['goalie'], last_tipoff=r.game.tipoff_at)
                if p['minutes'] > 0:
                    appearance = (r.game.tipoff_at, r.game.game_id, r.game.season, p['minutes'])
                    history['minutes'] = deque(sorted([*history['minutes'], appearance],
                                                       key=lambda x: x[:2])[-10:], maxlen=10)
                if not p['goalie'] and p['minutes'] > 0:
                    stats = self.player_stats.setdefault(p['id'], [0., 0.])
                    stats[0] += p['plus_minus']; stats[1] += p['minutes']
        self.delivered.add(r.game.game_id)

    def variant_ids(self, name, tid):
        """Players last observed on `tid` whose surname equals the reported one and whose first name is compatible (see
        first_names_compatible). Used only when the reported name matches no delivered player exactly; a caller treats any
        result other than exactly one id as unresolved, so two candidates (e.g. brothers with one initial) stay unknown."""
        first, last = split_name(name)
        return {pid for pid in self.surname_index.get(last, ()) if self.player_history[pid]['team'] == tid
                and any(first_names_compatible(first, f) for f in self.first_names[pid])} if last else set()

    def input_status(self):
        return {'box_digest': self.inputs.digest, 'box_games': len(self.inputs.boxes),
                'snapshots': len(self.inputs.snapshots), 'delivered_results': len(self.delivered)}

    def input_mismatch(self, recorded):
        if recorded.get('box_digest') != self.inputs.digest:
            return 'NHL player box inputs changed since fit; retrain'
        return None

    def stale_inputs(self, g, recent, n):
        if any(not self.inputs.boxes.get(gid, {}).get('teams', {}).get(t) for t in (g.home_team_id, g.away_team_id)
               for gid in recent.get((g.season, t), [])[:n]):
            return 'stale inputs: missing recent NHL player boxes'
        f = self.player_features(g)
        if f['report_missing']:
            return 'missing/stale NHL injury report, unknown team or unresolved player'
        return None

    def config(self):
        return {**super().config(), 'player_features': FEATURES, 'rating': 'regularized plus-minus per60',
                'prior_minutes': PRIOR_MINUTES, 'max_snapshot_age_hours': MAX_AGE_HOURS,
                'identity': 'all delivered player names; most-recent observed team; unique report-resolved transfer; '
                            'unmatched reported names: unique same-surname player last seen on the listed team '
                            'with a nickname-compatible first name',
                'absence_minutes': 'last 10 by game time among delivered appearances in current or prior season',
                'roster': 'last 5 by game time among delivered team boxes excluding observed transfers'}
