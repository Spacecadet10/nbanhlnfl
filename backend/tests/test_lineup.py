"""Who is playing (app/lineup.py, owner request Nov 11): goalie and top-player facts for the card and ticket review.
Small in-memory tables; the report parser and player-box parser are replaced by fixtures (they have their own tests)."""
import sqlite3
from types import SimpleNamespace

import pytest

from app import availability as av, lineup

AT = '2026-11-11T22:00:00Z'


def store():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript('''
      CREATE TABLE teams (team_id TEXT, abbr TEXT, name TEXT);
      CREATE TABLE games (game_id TEXT, season INT, status TEXT, tipoff_at TEXT, home_team_id TEXT, away_team_id TEXT);
      CREATE TABLE goalie_snapshots (snapshot_id INTEGER PRIMARY KEY, game_id TEXT, side TEXT, retrieved_at TEXT,
                                     source_status TEXT, player_name TEXT);
      CREATE TABLE nhl_pbp_imports (game_id TEXT, fetch_id INT, raw_id INT, complete INT, available_at TEXT);
      CREATE TABLE raw_fetches (fetch_id INT, raw_id INT);
      CREATE TABLE injury_report_attempts (attempt_id INT, fetch_id INT, file_name TEXT, slot_at TEXT, completed_at TEXT,
                                           outcome TEXT);''')
    c.executemany('INSERT INTO teams VALUES (?,?,?)', [('espn:1', 'TOR', 'Toronto'), ('espn:2', 'BOS', 'Boston')])
    return c


def nhl_store(monkeypatch, home_starters, listing):
    """BOS @ TOR tonight; TOR's earlier starts by goalie name; tonight's listings {side: (status, name, retrieved_at)}."""
    c = store()
    starters = {}
    for i, name in enumerate(home_starters):
        gid = f'old{i}'
        c.execute("INSERT INTO games VALUES (?,2027,'final',?, 'espn:1','espn:2')", (gid, f'2026-10-{10 + i:02d}T23:00:00Z'))
        starters[gid] = {'home': name, 'away': 'jeremy swayman'}
    c.execute("INSERT INTO games VALUES ('g',2027,'scheduled','2026-11-12T00:00:00Z','espn:1','espn:2')")
    for side, (status, name, at) in listing.items():
        c.execute('INSERT INTO goalie_snapshots (game_id, side, retrieved_at, source_status, player_name) VALUES (?,?,?,?,?)',
                  ('g', side, at, status, name))
    monkeypatch.setattr(lineup, '_pbp_starters', lambda conn, ids: {g: s for g, s in starters.items() if g in ids})
    return c


def test_backup_and_unconfirmed_goalies(monkeypatch):
    c = nhl_store(monkeypatch, ['joseph woll'] * 6 + ['anthony stolarz'] * 2,
                  {'home': ('confirmed', 'Anthony Stolarz', '2026-11-11T21:00:00Z'),
                   'away': ('expected', 'Jeremy Swayman', '2026-11-11T21:00:00Z')})
    f = lineup.facts(c, 'nhl', ['g'], AT)['g']
    assert f['home'] == {'goalie': 'Anthony Stolarz', 'status': 'confirmed', 'listed_at': '2026-11-11T21:00:00Z',
                         'usual_no1': 'joseph woll', 'backup': True}
    assert f['away']['backup'] is False and f['away']['status'] == 'expected'
    assert lineup.card_line('nhl', f, 'BOS @ TOR') == 'Backup goalie starting: TOR · Goalie not confirmed: BOS'
    winner = {'market': 'winner', 'matchup': 'BOS @ TOR', 'pick_label': 'TOR win'}
    assert lineup.review_flags('nhl', winner, f) == ['backup goalie listed for TOR (Anthony Stolarz, confirmed)']
    assert lineup.review_flags('nhl', dict(winner, pick_label='BOS win'), f) == ['BOS goalie not confirmed (expected)']
    total = {'market': 'total', 'matchup': 'BOS @ TOR', 'pick_label': 'Over 6.5'}
    assert len(lineup.review_flags('nhl', total, f)) == 2          # a total depends on both goalies


def test_a_later_listing_is_invisible_and_no_listing_is_never_all_clear(monkeypatch):
    c = nhl_store(monkeypatch, ['joseph woll'] * 6, {'home': ('confirmed', 'Joseph Woll', '2026-11-11T23:00:00Z')})
    f = lineup.facts(c, 'nhl', ['g'], AT)['g']
    assert f['home']['status'] == 'no_listing' and f['home']['backup'] is None
    assert lineup.card_line('nhl', f, 'BOS @ TOR') == 'Goalies not confirmed'
    assert lineup.review_flags('nhl', {'market': 'winner', 'matchup': 'BOS @ TOR', 'pick_label': 'TOR win'}, f) == \
        ['TOR goalie not confirmed (no listing)']


@pytest.mark.parametrize('starts', [['joseph woll'] * 4, ['joseph woll'] * 3 + ['anthony stolarz'] * 3])
def test_no_clear_no1_says_nothing_about_a_backup(monkeypatch, starts):
    c = nhl_store(monkeypatch, starts, {'home': ('confirmed', 'Anthony Stolarz', '2026-11-11T21:00:00Z'),
                                        'away': ('confirmed', 'Jeremy Swayman', '2026-11-11T21:00:00Z')})
    f = lineup.facts(c, 'nhl', ['g'], AT)['g']
    assert f['home']['usual_no1'] is None and f['home']['backup'] is None
    assert lineup.card_line('nhl', f, 'BOS @ TOR') is None and lineup.review_flags(
        'nhl', {'market': 'winner', 'matchup': 'BOS @ TOR', 'pick_label': 'TOR win'}, f) == []


def nba_store(monkeypatch, entries, *, report=True, reconciled=True):
    c = store()
    c.execute("INSERT INTO games VALUES ('g',2027,'scheduled','2026-11-12T00:30:00Z','espn:1','espn:2')")
    if report:
        c.execute("INSERT INTO raw_fetches VALUES (5, 9)")
        c.execute("INSERT INTO injury_report_attempts VALUES (1,5,'Injury-Report_2026-11-11_04_45PM.pdf',"
                  "'2026-11-11T21:45:00Z','2026-11-11T21:46:01Z','stored')")
        c.execute("INSERT INTO raw_fetches VALUES (6, 10)")     # a later report is not yet received at AT
        c.execute("INSERT INTO injury_report_attempts VALUES (2,6,'Injury-Report_2026-11-11_05_00PM.pdf',"
                  "'2026-11-11T22:00:00Z','2026-11-11T22:01:00Z','stored')")
    parsed = SimpleNamespace(entries=entries, not_submitted=[], reconciled=reconciled)
    monkeypatch.setattr(av, 'parse_report', lambda pdf, name, teams: parsed if name.endswith('04_45PM.pdf') else 1 / 0)
    monkeypatch.setattr(lineup.raw_cache, 'load', lambda conn, raw_id: b'%PDF')
    A = av.Appearance
    apps = {'p1': [A(f'2026-10-{d}', 2027, '1', 36.0, True) for d in (20, 22, 24)],     # TOR top player
            'p2': [A(f'2026-10-{d}', 2027, '1', 12.0, True) for d in (20, 22, 24)],     # TOR bench
            'p3': [A(f'2026-10-{d}', 2027, '2', 30.0, True) for d in (20, 22, 24)]}     # BOS top player
    names = {'p1': 'Scottie Barnes', 'p2': 'Ja Bench', 'p3': 'Jaylen Brown'}
    monkeypatch.setattr(lineup, '_box', lambda season: (apps, names))
    return c


def entry(last, first, status, team='Toronto'):
    return {'game_date': '2026-11-11', 'team': team, 'last': last, 'first': first, 'status': status}


def test_top_player_out_or_questionable_on_the_latest_received_report(monkeypatch):
    c = nba_store(monkeypatch, [entry('Barnes', 'Scottie', 'Out'), entry('Bench', 'Ja', 'Out'),
                                entry('Brown', 'Jaylen', 'Questionable', 'Boston'), entry('Nobody', 'Al', 'Probable')])
    f = lineup.facts(c, 'nba', ['g'], AT)['g']
    assert f['report']['file'] == 'Injury-Report_2026-11-11_04_45PM.pdf'
    assert [p['player'] for p in f['home']['flagged']] == ['Scottie Barnes']      # the bench player is not a top player
    assert lineup.card_line('nba', f, 'BOS @ TOR') == 'Out: S. Barnes · Questionable: J. Brown'
    assert lineup.review_flags('nba', {'market': 'spread', 'matchup': 'BOS @ TOR', 'pick_label': 'TOR -3.5'}, f) == \
        ['TOR top player Scottie Barnes Out']


def test_an_unmatched_name_is_said_and_a_missing_report_is_unknown(monkeypatch):
    c = nba_store(monkeypatch, [entry('Barnes', 'Scotty', 'Out'), entry('Isaac', 'Jonathan', 'Out')])
    f = lineup.facts(c, 'nba', ['g'], AT)['g']
    assert lineup.card_line('nba', f, 'BOS @ TOR') is None
    # 'Scotty Barnes' may be the top player Scottie Barnes: said. 'Jonathan Isaac' has no game this season: not a top player.
    assert lineup.review_flags('nba', {'market': 'winner', 'matchup': 'BOS @ TOR', 'pick_label': 'TOR win'}, f) == \
        ['TOR report name not matched to a player: Scotty Barnes (Out)']
    f = lineup.facts(nba_store(monkeypatch, [], report=False), 'nba', ['g'], AT)['g']
    assert lineup.card_line('nba', f, 'BOS @ TOR') is None
    assert lineup.review_flags('nba', {'market': 'winner', 'matchup': 'BOS @ TOR', 'pick_label': 'TOR win'}, f) == \
        ['TOR injury report not readable (no_report)']


def test_errors_never_raise_and_other_sports_have_nothing():
    c = sqlite3.connect(':memory:')
    assert lineup.facts(c, 'nhl', ['g'], AT) == {'g': {'error': 'OperationalError'}}
    assert lineup.card_line('nhl', {'error': 'x'}, 'A @ B') is None
    assert lineup.review_flags('nhl', {'market': 'winner'}, {'error': 'x'}) == ['lineup facts unavailable']
    assert lineup.facts(c, 'nfl', ['g'], AT) == {} and lineup.review_flags('soccer', {}, None) == []
