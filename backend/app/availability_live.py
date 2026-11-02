"""Two-hour availability research candidate: real retrieval timestamps, no retrospective live forecasts.

The old 17:45 experiment stays unchanged. This variant trains at two-hour historical cutoffs, using a report only
if its documented historical publication assumption precedes that cutoff. Missing historical reports use the
registered missing flag. Current reports must have actually arrived and reconcile; unknown weighted players block
this candidate. Baselines continue independently. This is forward research, not an asserted improvement.
"""
import csv
import dataclasses
import hashlib
import io
import json
from collections import defaultdict

from . import availability as av, backtest, config, raw_cache
from .db import transaction, utc_now
from .sources import espn

SEED_SOURCE = 'nba_player_minutes_live'


def prepare(conn):
    """No evaluation: materialize already-researched report facts, with provenance, for live training."""
    box = {s: av.load_player_box(s) for s in av.SEASONS}
    facts, coverage = av.build_report_facts(conn, box)
    source = hashlib.sha256()
    for p in sorted((av.research_dir() / 'reports').glob('*')):
        source.update(p.name.encode()); source.update(p.read_bytes())
    for season in av.SEASONS:
        source.update(av.player_box_path(season).read_bytes())
    source.update(av.ALIAS_PATH.read_bytes())
    payload = dict(source_hash=source.hexdigest(), facts={d:dataclasses.asdict(f) for d,f in facts.items()})
    body = json.dumps(payload, sort_keys=True, default=lambda x: sorted(x)).encode()
    # Verify the cached artifact on every load. Preparation is explicit and never changes old ledger rows.
    payload['sha256'] = hashlib.sha256(body).hexdigest()
    path = av.research_dir() / 'forward-facts.json'
    path.write_text(json.dumps(payload, sort_keys=True, default=lambda x: sorted(x)))
    return {'facts':len(facts), 'coverage':coverage, 'path':str(path), 'source_hash':payload['source_hash']}


def seed_minutes(conn, season: int):
    """Explicit one-time free bulk download for live inputs only; never scored as an independent test."""
    import httpx
    current = av.season_of(utc_now()[:10])
    if season != current - 1:
        raise av.AvailabilityError('Live seed must be the immediately preceding season')
    name = f'player_box_{season}.csv'
    if conn.execute('SELECT 1 FROM raw_responses WHERE source=? AND request_key=?', (SEED_SOURCE,name)).fetchone():
        return {'status':'already_cached', 'season':season}
    release = httpx.get(av.PLAYER_BOX_RELEASE, timeout=45); release.raise_for_status()
    asset = next(a for a in release.json()['assets'] if a['name']==name)
    response = httpx.get(asset['browser_download_url'], timeout=120, follow_redirects=True)
    response.raise_for_status()
    received = utc_now()
    digest = 'sha256:'+hashlib.sha256(response.content).hexdigest()
    if digest != asset.get('digest'):
        raise av.AvailabilityError('Player-minutes file does not match the published digest')
    with transaction(conn):
        raw_id, fetch_id = raw_cache.store_fetch(conn, source=SEED_SOURCE, endpoint=asset['browser_download_url'],
            request_key=name, body=response.content, retrieved_at=received, importer_version='player-minutes-1', suffix='.csv')
    return {'status':'stored', 'raw_id':raw_id, 'fetch_id':fetch_id, 'season':season, 'sha256':digest}


def summary_players(data, game):
    """Complete player minutes from one final ESPN summary. Missing/malformed minutes are not zero minutes."""
    box = espn.parse_summary_box(data)
    if not box.completed or box.game_id != game['game_id']:
        raise av.AvailabilityError('Player summary is incomplete or belongs to another game')
    result = {}
    teams = {game['home_team_id'].split(':')[1], game['away_team_id'].split(':')[1]}
    for team in data.get('boxscore', {}).get('players', []):
        tid = str(team.get('team', {}).get('id', ''))
        if tid not in teams:
            continue
        players = {}
        for stats in team.get('statistics', []):
            labels = stats.get('labels', [])
            if 'MIN' not in labels:
                continue
            index = labels.index('MIN')
            for a in stats.get('athletes', []):
                pid = str(a.get('athlete', {}).get('id', ''))
                name = a.get('athlete', {}).get('displayName', '')
                if not pid or not name:
                    raise av.AvailabilityError('Player summary is missing an athlete identity')
                values = a.get('stats', [])
                inactive_dash = (a.get('active') is False and bool(a.get('reason')) and len(values) > index
                                 and str(values[index]).strip() in ('-', '--'))
                if a.get('didNotPlay') or inactive_dash:   # ESPN: unused player listed inactive with a reason
                    minutes = 0.
                else:
                    if len(values) <= index:
                        raise av.AvailabilityError('Missing player minutes')
                    value = str(values[index])
                    try:
                        parts = value.split(':')
                        minutes = float(parts[0]) + (float(parts[1])/60 if len(parts)==2 else 0)
                    except ValueError:
                        raise av.AvailabilityError('Invalid player minutes') from None
                    if not 0 <= minutes <= 100:
                        raise av.AvailabilityError('Out-of-range player minutes')
                players[pid] = (name, minutes)
        if not players or sum(m for _,m in players.values()) < 230:
            raise av.AvailabilityError('Incomplete team minutes')
        result[tid] = players
    if set(result) != teams:
        raise av.AvailabilityError('Both teams need player minutes')
    return result


def live_box(conn, now):
    seeds = conn.execute('SELECT f.*, r.request_key FROM raw_fetches f JOIN raw_responses r USING(raw_id) '
                         'WHERE r.source=? AND f.retrieved_at<=? ORDER BY f.retrieved_at DESC, f.fetch_id DESC',
                         (SEED_SOURCE,now)).fetchall()
    box, used = {}, []
    current_season = av.season_of(now[:10])
    for seed in seeds:
        season = int(seed['request_key'].split('_')[-1].split('.')[0])
        if season >= current_season:
            raise av.AvailabilityError('Live seed must precede the current season')
        if season in box:
            continue
        rows = csv.DictReader(io.StringIO(raw_cache.load(conn,seed['raw_id']).decode()))
        box[season] = av.parse_player_rows(rows, season)
        used.append(seed['fetch_id'])
    if not box:
        raise av.AvailabilityError('No prior-season player minutes cached; run availability-prepare --seed-season YEAR')
    latest_seed = max(box)
    snapshots = conn.execute('SELECT f.*, r.request_key FROM raw_fetches f JOIN raw_responses r USING(raw_id) '
        'WHERE r.source=? AND f.retrieved_at<=? ORDER BY f.retrieved_at DESC, f.fetch_id DESC',
        (espn.SUMMARY_SOURCE,now)).fetchall()
    seen, covered = set(), set()
    for raw in snapshots:
        gid = 'espn:'+raw['request_key'].split('=',1)[1]
        if gid in seen:
            continue
        game = conn.execute('SELECT * FROM games WHERE game_id=?', (gid,)).fetchone()
        if not game or game['season'] <= latest_seed or game['status']!='final' or not game['result_available_at'] or game['result_available_at']>now:
            continue
        parsed = summary_players(json.loads(raw_cache.load(conn,raw['raw_id'])), game)
        seen.add(gid); covered.add(gid); used.append(raw['fetch_id'])
        apps,names = box.setdefault(game['season'], (defaultdict(list),{}))
        day = av.datetime.fromisoformat(game['tipoff_at'].replace('Z','+00:00')).astimezone(av.ET).strftime('%Y-%m-%d')
        for tid,players in parsed.items():
            for pid,(name,minutes) in players.items():
                apps[pid].append(av.Appearance(day,game['season'],tid,minutes,minutes>0)); names[pid]=name
    # Refuse stale season initialization rather than silently treating everyone as zero minutes.
    year = av.season_of(av.datetime.fromisoformat(now.replace('Z','+00:00')).astimezone(av.ET).strftime('%Y-%m-%d'))
    if latest_seed < year-1:
        raise av.AvailabilityError('Prior-season minutes seed is stale')
    missing = [r[0] for r in conn.execute("SELECT game_id FROM games WHERE season>? AND status='final' "
        "AND season_type IN ('regular','play_in','postseason') AND result_available_at<=?", (latest_seed,now)) if r[0] not in covered]
    if missing:
        raise av.AvailabilityError(f'Player-minute coverage incomplete for {len(missing)} current-season games')
    return box, used


def current_facts(conn, now):
    row = conn.execute("SELECT a.*, f.raw_id FROM injury_report_attempts a JOIN raw_fetches f USING(fetch_id) "
        "WHERE a.outcome='stored' AND a.completed_at<=? ORDER BY a.slot_at DESC, a.completed_at DESC, a.attempt_id DESC LIMIT 1", (now,)).fetchone()
    if not row or (av.datetime.fromisoformat(now.replace('Z','+00:00')) - av.datetime.fromisoformat(row['completed_at'].replace('Z','+00:00'))).total_seconds()>3600:
        raise av.AvailabilityError('No injury report actually received in the last 60 minutes')
    team_names = {r['name']:r['team_id'].split(':')[1] for r in conn.execute('SELECT team_id,name FROM teams')}
    parsed = av.parse_report(raw_cache.load(conn,row['raw_id']),row['file_name'],list(team_names))
    clock = av.datetime.fromisoformat(now.replace('Z','+00:00'))
    if (not parsed.reconciled or parsed.header_published is None
            or not 0 <= (clock-parsed.header_published).total_seconds() <= 3600):
        raise av.AvailabilityError('Injury report does not reconcile or has a future publication time')
    box, used = live_box(conn,now)
    index, hist = av.PlayerIndex(box,av.load_aliases()), av.MinutesHistory(box)
    facts = {}
    for entry in parsed.entries + parsed.not_submitted:
        day = entry['game_date']
        facts.setdefault(day,av.ReportFacts(True,'live',row['completed_at']))
        team = team_names.get(entry['team'])
        if team is None:
            raise av.AvailabilityError('Unmatched report team')
        if not day:
            raise av.AvailabilityError('Report entry has no game date')
        facts[day].teams_on_report.add(team)
        if entry in parsed.not_submitted:
            facts[day].unsubmitted.add(team); continue
        weight = av.STATUS_WEIGHT[entry['status']]
        if not weight:
            continue
        pid,_ = index.match(entry['last'],entry['first'],team,av.season_of(day))
        if pid is None:
            raise av.AvailabilityError('Unresolved unavailable player; no silent zero-minute assumption')
        minutes,basis = hist.expected(pid,day)
        if basis=='no_history':
            raise av.AvailabilityError('Unavailable player has no trustworthy minute history')
        slot = facts[day].absent.setdefault(team,{'new':0.,'all':0.})
        slot['all'] += weight*minutes
        if hist.played_last_team_game(pid,team,day):
            slot['new'] += weight*minutes
    return facts, [row['fetch_id'], *used]


def build(conn, elo_cfg, now, current_ids):
    from .models.logistic import LogisticV2, V2_FEATURES, V2_SCALED
    path = av.research_dir() / 'forward-facts.json'
    payload = json.loads(path.read_text())
    seal = payload.pop('sha256')
    if hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()!=seal:
        raise av.AvailabilityError('Prepared historical facts failed their checksum')
    historical = {d:av.ReportFacts(**f) for d,f in payload['facts'].items()}
    current, fetches = current_facts(conn,now)
    class AvailabilityTwoHour(LogisticV2):
        name = 'availability_two_hour'
        feature_names = V2_FEATURES + av.AVAIL_FEATURES
        scaled_names = V2_SCALED + ('new_absent_diff','all_absent_diff')
        input_version = hashlib.sha256(json.dumps([seal,fetches],sort_keys=True).encode()).hexdigest()
        training_two_hour = True
        def _features(self,g):
            day = av.datetime.fromisoformat(g.tipoff_at.replace('Z','+00:00')).astimezone(av.ET).strftime('%Y-%m-%d')
            f = (current if g.game_id in current_ids else historical).get(day)
            cutoff = now if g.game_id in current_ids else (av.datetime.fromisoformat(g.tipoff_at.replace('Z','+00:00'))-av.timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%SZ')
            if g.game_id in current_ids and (f is None or any(t.split(':')[1] in f.unsubmitted for t in (g.home_team_id,g.away_team_id))):
                raise av.AvailabilityError('Current game injury report is missing or not submitted')
            usable = f is not None and f.published_at is not None and f.published_at <= cutoff
            return {**super()._features(g), **av.game_features({day:f} if usable else {},g,allow_early=True)}
    return AvailabilityTwoHour(elo_cfg)
