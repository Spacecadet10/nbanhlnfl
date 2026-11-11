"""Who is playing: plain facts for the game card and the ticket review (owner request, Nov 11 2026).

Display and review only: no model reads this and no forecast changes. Built only from data the app already collects,
all retrieved at or before `at` (the card's or snapshot's time):
- NHL: the latest ESPN goalie listing per team (`goalie_snapshots`: confirmed / expected / none listed / no listing), and
  whether the listed goalie is the team's usual No.1: the goalie who started the most of the team's earlier games this
  season (first goalie in net in the stored play-by-play), with at least MIN_NO1_GAMES known starts and no tie. Without a
  clear No.1 nothing is said about a backup.
- NBA: top players (up to TOP_PLAYERS per team, averaging TOP_MINUTES+ minutes in at least MIN_APPEARANCES games played
  this season before the game's date, from the stored player box file) listed Out, Doubtful or Questionable on the latest
  stored official injury report for the game's date.
These are the definitions of the Nov 11 lineup research (docs/evidence/lineup-info-2026-11-11). Unknown stays unknown:
a missing report or listing is reported as such, never as "all clear".
"""
import csv
import gzip
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from typing import Optional

from . import config, raw_cache

MIN_NO1_GAMES = 5
TOP_MINUTES = 24.0
MIN_APPEARANCES = 3
TOP_PLAYERS = 3
FLAGGED = ('Out', 'Doubtful', 'Questionable')
CARD_LINE_CHARS = 58          # one line in the pick panel at 26 px (the renderer also measures and shortens)


def _side_abbrs(matchup: str) -> dict:
    away, _, home = (matchup or '').partition(' @ ')
    return {'away': away.strip(), 'home': home.strip()}


# --- NHL ---------------------------------------------------------------------------------------------------------------
def _norm(name: Optional[str]) -> Optional[str]:
    from .candidates.goalies import normalise
    return normalise(name)


def _pbp_starters(conn, game_ids: set) -> dict:
    """game_id -> {'home': normalised name, 'away': ...} for the first complete play-by-play import of each game. Reads the
    research goalie cache when present (never writes it) and parses only what it lacks."""
    if not game_ids:
        return {}
    from .candidates.goalies import parse, _cache_path
    cache = {}
    try:
        with gzip.open(_cache_path(), 'rt') as fh:
            cache = json.load(fh)
    except (OSError, ValueError, EOFError):
        cache = {}
    out = {}
    marks = ','.join('?' * len(game_ids))
    for r in conn.execute(f"SELECT game_id, fetch_id, raw_id FROM nhl_pbp_imports WHERE complete=1 AND game_id IN ({marks}) "
                          "ORDER BY available_at, fetch_id", tuple(game_ids)):
        if r['game_id'] in out:
            continue
        det = cache.get(str(r['fetch_id']))
        if det is None:
            try:
                d = parse(json.loads(raw_cache.load(conn, r['raw_id'])))
            except Exception:
                continue
            det = {'starter': d['starter'], 'names': {str(k): v for k, v in d['names'].items()}}
        names = det.get('names', {})
        out[r['game_id']] = {side: names.get(str(gid)) for side, gid in det.get('starter', {}).items()}
    return out


def usual_no1(conn, team_id: str, season: int, before: str, starters: dict) -> Optional[str]:
    """Normalised name of the team's No.1 this season before `before`, or None (too few known starts, or a tie)."""
    counts = Counter()
    for g in conn.execute("SELECT game_id, home_team_id FROM games WHERE season=? AND status='final' AND tipoff_at<? "
                          "AND (home_team_id=? OR away_team_id=?)", (season, before, team_id, team_id)):
        name = starters.get(g['game_id'], {}).get('home' if g['home_team_id'] == team_id else 'away')
        if name:
            counts[name] += 1
    if sum(counts.values()) < MIN_NO1_GAMES:
        return None
    top = counts.most_common(2)
    if len(top) > 1 and top[0][1] == top[1][1]:
        return None
    return top[0][0]


def nhl_facts(conn, games: list, at: str) -> dict:
    """game_id -> {'home': {...}, 'away': {...}} goalie facts (see module docstring)."""
    if not games:
        return {}
    teams = {(g['home_team_id'], g['season']) for g in games} | {(g['away_team_id'], g['season']) for g in games}
    earlier = set()
    for team, season in teams:
        earlier |= {r[0] for r in conn.execute("SELECT game_id FROM games WHERE season=? AND status='final' AND tipoff_at<? "
                                               "AND (home_team_id=? OR away_team_id=?)", (season, at, team, team))}
    starters = _pbp_starters(conn, earlier)
    out = {}
    for g in games:
        sides = {}
        for side in ('home', 'away'):
            r = conn.execute("SELECT source_status, player_name, retrieved_at FROM goalie_snapshots WHERE game_id=? AND side=? "
                             "AND retrieved_at<=? ORDER BY retrieved_at DESC, snapshot_id DESC LIMIT 1",
                             (g['game_id'], side, at)).fetchone()
            status = 'no_listing' if r is None else ('unknown' if r['source_status'] == 'none_listed' else r['source_status'])
            name = None if r is None or r['source_status'] == 'none_listed' else r['player_name']
            no1 = usual_no1(conn, g[f'{side}_team_id'], g['season'], min(at, g['tipoff_at']), starters)
            backup = None if (name is None or no1 is None) else _norm(name) != no1
            sides[side] = {'goalie': name, 'status': status, 'listed_at': r['retrieved_at'] if r else None,
                           'usual_no1': no1, 'backup': backup}
        out[g['game_id']] = sides
    return out


# --- NBA ---------------------------------------------------------------------------------------------------------------
def _latest_report(conn, at: str):
    return conn.execute("SELECT a.file_name, a.completed_at, f.raw_id FROM injury_report_attempts a JOIN raw_fetches f "
                        "USING(fetch_id) WHERE a.outcome='stored' AND a.completed_at<=? "
                        "ORDER BY a.slot_at DESC, a.completed_at DESC, a.attempt_id DESC LIMIT 1", (at,)).fetchone()


def _box(season: int):
    from . import availability as av
    path = av.player_box_path(season)
    with path.open(newline='') as f:
        return av.parse_player_rows(csv.DictReader(f), season)


def top_players(apps: dict, names: dict, team: str, day: str, season: int) -> list:
    """[(athlete id, name, average minutes)] of the team's top players before `day` (ET date)."""
    rows = []
    for pid, games in apps.items():
        before = [a for a in games if a.date < day and a.season == season]
        if not before or before[-1].team_id != team:
            continue
        played = [a.minutes for a in before if a.played and a.team_id == team]
        if len(played) >= MIN_APPEARANCES and sum(played) / len(played) >= TOP_MINUTES:
            rows.append((pid, names.get(pid, ''), sum(played) / len(played)))
    return sorted(rows, key=lambda r: -r[2])[:TOP_PLAYERS]


def nba_facts(conn, games: list, at: str) -> dict:
    """game_id -> {'report': {...}, 'home': {...}, 'away': {...}} top-player injury facts (see module docstring)."""
    from . import availability as av
    if not games:
        return {}
    report = _latest_report(conn, at)
    parsed, problem = None, None
    if report is None:
        problem = 'no_report'
    else:
        team_names = [r['name'] for r in conn.execute('SELECT name FROM teams')]
        try:
            parsed = av.parse_report(raw_cache.load(conn, report['raw_id']), report['file_name'], team_names)
            if not parsed.reconciled:
                problem = 'report_unreadable'
        except Exception:
            problem = 'report_unreadable'
    by_name = {r['name']: r['team_id'] for r in conn.execute('SELECT team_id, name FROM teams')}
    boxes, out = {}, {}
    for g in games:
        tip = datetime.fromisoformat(g['tipoff_at'].replace('Z', '+00:00')).astimezone(av.ET)
        day, season = tip.strftime('%Y-%m-%d'), av.season_of(tip.strftime('%Y-%m-%d'))
        info = {'file': report['file_name'] if report else None, 'received_at': report['completed_at'] if report else None,
                'problem': problem}
        entries = [e for e in (parsed.entries if parsed and not problem else []) if e['game_date'] == day]
        listed_teams = {by_name.get(e['team']) for e in (parsed.entries + parsed.not_submitted if parsed and not problem else [])
                        if e['game_date'] == day}
        if season not in boxes:
            try:
                boxes[season] = _box(season)
            except (OSError, ValueError):
                boxes[season] = None
        box = boxes[season]
        result = {'report': info}
        for side in ('home', 'away'):
            team_id = g[f'{side}_team_id']
            if problem or box is None:
                result[side] = {'top_players': None, 'flagged': None,
                                'problem': problem or 'no_player_minutes'}
                continue
            team = team_id.split(':', 1)[1]
            apps, names = box
            top = top_players(apps, names, team, day, season)
            index = av.PlayerIndex({season: box}, {})
            flagged, unmatched = [], []
            for e in entries:
                if by_name.get(e['team']) != team_id or e['status'] not in FLAGGED:
                    continue
                pid, _ = index.match(e['last'], e['first'], team, season, before=day)
                if pid is None:
                    # A top player has 3+ games this season, so the index normally resolves him; a name it cannot resolve
                    # is said (never treated as "not top") only when it shares a surname with one of the team's top players.
                    if any(av.norm_name(t[1].split(' ', 1)[-1]) == av.norm_name(e['last']) for t in top):
                        unmatched.append(f"{e['first']} {e['last']} ({e['status']})")
                    continue
                hit = next((t for t in top if t[0] == pid), None)
                if hit:
                    flagged.append({'player': hit[1], 'status': e['status'], 'avg_minutes': round(hit[2], 1)})
            result[side] = {'top_players': [t[1] for t in top], 'flagged': flagged, 'unmatched_names': unmatched,
                            'on_report': team_id in listed_teams}
        out[g['game_id']] = result
    return out


# --- Shared ------------------------------------------------------------------------------------------------------------
def facts(conn, sport: str, game_ids, at: str) -> dict:
    """game_id -> facts for NBA/NHL games; {} for other sports. Never raises: a problem is returned as {'error': ...}."""
    if sport not in ('nba', 'nhl'):
        return {}
    ids = sorted(set(game_ids))
    if not ids:
        return {}
    try:
        marks = ','.join('?' * len(ids))
        games = list(conn.execute(f'SELECT * FROM games WHERE game_id IN ({marks})', ids))
        return (nhl_facts if sport == 'nhl' else nba_facts)(conn, games, at)
    except Exception as exc:
        return {gid: {'error': type(exc).__name__} for gid in ids}


def _short(name: str) -> str:
    parts = (name or '').split()
    return name if len(parts) < 2 else f'{parts[0][0]}. {" ".join(parts[1:])}'


def card_line(sport: str, fact: Optional[dict], matchup: str) -> Optional[str]:
    """One short plain line for the card, or None when there is nothing to say."""
    if not fact or 'error' in fact:
        return None
    abbr = _side_abbrs(matchup)
    parts = []
    if sport == 'nhl':
        for status, words in (('confirmed', 'Backup goalie starting: '), ('expected', 'Backup goalie expected: ')):
            backup = [abbr[s] for s in ('away', 'home') if fact[s]['backup'] and fact[s]['status'] == status]
            if backup:
                parts.append(words + ', '.join(backup))
        open_ = [abbr[s] for s in ('away', 'home') if fact[s]['status'] != 'confirmed' and not fact[s]['backup']]
        if len(open_) == 2:
            parts.append('Goalies not confirmed')
        elif open_:
            parts.append('Goalie not confirmed: ' + open_[0])
    else:
        if fact['report'].get('problem') == 'no_report':
            return None
        by_status = defaultdict(list)
        for s in ('away', 'home'):
            for f in fact[s].get('flagged') or []:
                by_status[f['status']].append(_short(f['player']))
        parts = [f'{status}: ' + ', '.join(by_status[status]) for status in FLAGGED if by_status[status]]
    return ' · '.join(parts) or None


def picked_sides(pick: dict) -> tuple:
    """The sides a pick depends on: the named team for winner / spread / regulation picks; both teams for totals, team
    totals (a team's goals depend on the other side's goalie) and a regulation draw."""
    abbr = _side_abbrs(pick.get('matchup', ''))
    label = (pick.get('pick_label') or '').split(' ')[0]
    if pick.get('market') in ('winner', 'spread', 'reg3'):
        for side in ('home', 'away'):
            if abbr[side] and label == abbr[side]:
                return (side,)
    return ('home', 'away')


def review_flags(sport: str, pick: dict, fact: Optional[dict]) -> list:
    """Plain reasons the ticket review must leave this pick off unless it states why it still stands."""
    if sport not in ('nba', 'nhl'):
        return []
    if not fact or 'error' in fact:
        return ['lineup facts unavailable']
    abbr, flags = _side_abbrs(pick.get('matchup', '')), []
    for side in picked_sides(pick):
        s = fact[side]
        if sport == 'nhl':
            if s['backup']:
                flags.append(f"backup goalie listed for {abbr[side]} ({s['goalie']}, {s['status']})")
            if s['status'] != 'confirmed':
                flags.append(f"{abbr[side]} goalie not confirmed ({s['status'].replace('_', ' ')})")
        else:
            if s.get('flagged') is None:
                flags.append(f"{abbr[side]} injury report not readable ({s.get('problem')})")
            for f in s.get('flagged') or []:
                flags.append(f"{abbr[side]} top player {f['player']} {f['status']}")
            for name in s.get('unmatched_names') or []:
                flags.append(f"{abbr[side]} report name not matched to a player: {name}")
    return flags
