"""DRatings on paper (app/dratings_capture.py, owner request Nov 11): their numbers for the same game at the moment the
app records a forecast; light requests, append-only, never shown. Real saved pages (trimmed to the upcoming table)."""
import gzip
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app import dratings_capture as d

FIX = Path(__file__).parent / 'fixtures'
NBA = (FIX / 'dratings-nba-upcoming-2026-11-11-trimmed.html').read_bytes()
NHL = (FIX / 'dratings-nhl-upcoming-2026-11-11-trimmed.html').read_bytes()
NOW = datetime(2026, 11, 11, 22, 5, tzinfo=timezone.utc)


def test_real_pages_parse_with_the_right_columns():
    nba = d.upcoming_rows(NBA.decode())
    assert len(nba) == 10 and nba[0] == {'start': '2026-11-12T00:10:00Z', 'away': 'Cleveland Cavaliers',
                                         'home': 'New York Knicks', 'p_away': 0.489, 'p_home': 0.511,
                                         'proj_away': 114.8, 'proj_home': 115.2}
    nhl = d.upcoming_rows(NHL.decode())          # an extra Goalies column: found by header, not position
    assert len(nhl) == 6 and nhl[0]['p_home'] == 0.489 and nhl[0]['proj_away'] == 3.25


def store(rows):
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript('''CREATE TABLE teams (team_id TEXT, name TEXT);
      CREATE TABLE games (game_id TEXT, home_team_id TEXT, away_team_id TEXT);
      CREATE TABLE game_forecasts (forecast_id INT, game_id TEXT, market TEXT, status TEXT, generated_at TEXT,
                                   tipoff_at TEXT, p_a REAL);''')
    c.executemany('INSERT INTO teams VALUES (?,?)', [('t1', 'New York Knicks'), ('t2', 'Cleveland Cavaliers'),
                                                     ('t3', 'Toronto Raptors'), ('t4', 'Boston Celtics')])
    c.executemany('INSERT INTO games VALUES (?,?,?)', [('g1', 't1', 't2'), ('g2', 't3', 't4')])
    c.executemany('INSERT INTO game_forecasts VALUES (?,?,?,?,?,?,?)', rows)
    return c


def test_one_light_request_only_when_a_new_forecast_exists_and_rows_are_appended_once(tmp_path, monkeypatch):
    monkeypatch.setenv('NBA_DATA_DIR', str(tmp_path))
    c = store([(1, 'g1', 'winner', 'forecast', '2026-11-11T22:00:00Z', '2026-11-12T00:00:00Z', 0.55),
               (2, 'g1', 'spread', 'forecast', '2026-11-11T22:00:00Z', '2026-11-12T00:00:00Z', 0.5),       # not winner
               (3, 'g2', 'winner', 'forecast', '2026-11-11T21:00:00Z', '2026-11-12T00:00:00Z', 0.45),
               (4, 'g2', 'winner', 'forecast', '2026-11-11T23:00:00Z', '2026-11-12T00:00:00Z', 0.45)])    # future
    calls = []
    get = lambda url: calls.append(url) or (200, NBA)     # noqa: E731
    out = d.run({'nba': c}, NOW, get=get, pause=lambda s: None, clock=lambda: NOW)
    assert calls == [d.PAGES['nba']] and out['sports']['nba'] == {'pending': 2, 'captured': 2, 'matched': 2, 'dratings_rows': 10}
    rows = [json.loads(x) for x in (tmp_path / 'dratings' / 'captures.jsonl').read_text().splitlines()]
    one = next(r for r in rows if r['forecast_id'] == 1)
    assert (one['dr_p_home'], one['dr_proj_home'], one['app_p_home'], one['lag_seconds']) == (0.511, 115.2, 0.55, 300)
    assert one['dratings_start'] == '2026-11-12T00:10:00Z' and one['captured_at'] == '2026-11-11T22:05:00Z'
    page = tmp_path / 'dratings' / one['page']
    assert gzip.decompress(page.read_bytes()) == NBA
    again = d.run({'nba': c}, NOW, get=get, pause=lambda s: None, clock=lambda: NOW)
    assert len(calls) == 1 and again['sports']['nba'] == {'pending': 0}          # nothing new: no request
    assert len((tmp_path / 'dratings' / 'captures.jsonl').read_text().splitlines()) == 2


def test_an_unmatched_game_is_recorded_as_such_and_a_failed_request_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv('NBA_DATA_DIR', str(tmp_path))
    c = store([(1, 'g1', 'winner', 'forecast', '2026-11-11T22:00:00Z', '2026-11-11T20:00:00Z', 0.55)])
    c.execute("UPDATE game_forecasts SET tipoff_at='2026-11-12T03:00:00Z'")          # start differs from DRatings'
    assert d.run({'nba': c}, NOW, get=lambda u: (503, b''), pause=lambda s: None, clock=lambda: NOW)['sports']['nba']['error'] == 'HTTP 503'
    assert not (tmp_path / 'dratings' / 'captures.jsonl').exists()
    later = NOW.replace(minute=26)                                                   # after the 20-minute backoff
    d.run({'nba': c}, later, get=lambda u: (200, NBA), pause=lambda s: None, clock=lambda: later)
    (r,) = [json.loads(x) for x in (tmp_path / 'dratings' / 'captures.jsonl').read_text().splitlines()]
    assert r['matched'] is False and 'dr_p_home' not in r


def test_two_sports_are_spaced_and_names_are_normalised():
    assert d.norm('Montréal Canadiens') == 'montreal canadiens' and d.norm('Los Angeles Clippers') == 'la clippers'
    assert d.norm('St. Louis Blues') == 'st louis blues'


def test_an_empty_parse_records_nothing_and_a_cut_line_is_closed_first(tmp_path, monkeypatch):
    monkeypatch.setenv('NBA_DATA_DIR', str(tmp_path))
    c = store([(1, 'g1', 'winner', 'forecast', '2026-11-11T22:00:00Z', '2026-11-12T00:00:00Z', 0.55)])
    out = d.run({'nba': c}, NOW, get=lambda u: (200, b'<html>changed layout</html>'), pause=lambda s: None, clock=lambda: NOW)
    assert out['sports']['nba']['error'] == 'no upcoming rows parsed' and not (tmp_path / 'dratings' / 'captures.jsonl').exists()
    (tmp_path / 'dratings' / 'captures.jsonl').write_text('{"sport": "nba", "forec')          # a crash mid-write
    calls = []
    d.run({'nba': c}, NOW, get=lambda u: calls.append(u) or (200, NBA), pause=lambda s: None, clock=lambda: NOW)
    assert calls == []                                       # backing off 20 minutes after the unreadable page
    later = NOW.replace(minute=30)
    d.run({'nba': c}, later, get=lambda u: (200, NBA), pause=lambda s: None, clock=lambda: later)
    lines = (tmp_path / 'dratings' / 'captures.jsonl').read_text().splitlines()
    assert len(lines) == 2 and json.loads(lines[1])['forecast_id'] == 1


def test_the_job_runs_every_two_minutes():
    import plistlib
    plist = plistlib.loads((Path(__file__).resolve().parents[2] / 'scripts/launchd/com.nba-predictor.dratings-capture.plist').read_bytes())
    assert plist['StartInterval'] == 120 and plist['ProgramArguments'][-2:] == ['app.dratings_capture', 'run']
