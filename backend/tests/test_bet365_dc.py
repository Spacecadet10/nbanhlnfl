"""Bet365 double chance from ESPN (app/bet365_dc.py) and prices derived from stored prices (app/implied_lines.py).
Owner request Nov 11: a sportsbook quote shown with its time; implied/derived numbers labelled and never a price to bet."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app import bet365_dc, implied_lines

REAL = json.loads((Path(__file__).parent / 'fixtures/espn-core-odds-eng1-401879233-trimmed.json').read_text())
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def test_the_real_feed_gives_labelled_double_chance_for_the_right_teams():
    prices = bet365_dc.parse(REAL, '382', '370')          # Man City (home) v Fulham, as ESPN lists them
    assert prices == {'home_draw': {'fractional': '1/9', 'decimal': 1.1111},
                      'draw_away': {'fractional': '7/4', 'decimal': 2.75},
                      'home_away': {'fractional': '2/11', 'decimal': 1.1818}}
    assert bet365_dc.parse(REAL, '370', '382') == {}       # home/away swapped: never read the wrong way round
    assert bet365_dc.decimal('15/4') == 4.75 and bet365_dc.decimal('x') is None and bet365_dc.decimal(None) is None


def store():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.execute('CREATE TABLE soccer_matches (match_id TEXT, competition_id TEXT, kickoff_at TEXT, status TEXT, '
              'home_team_id TEXT, away_team_id TEXT)')
    c.executemany('INSERT INTO soccer_matches VALUES (?,?,?,?,?,?)', [
        ('soccer:eng.1:401879233', 'eng.1', '2026-11-21T12:30:00Z', 'scheduled', 'soccer:382', 'soccer:370'),
        ('soccer:eng.1:2', 'eng.1', '2026-11-25T12:30:00Z', 'scheduled', 'soccer:1', 'soccer:2'),      # too far ahead
        ('soccer:uefa.nations:3', 'uefa.nations', '2026-11-21T12:30:00Z', 'scheduled', 'soccer:5', 'soccer:6')])
    return c


def test_fetch_is_light_saves_whole_responses_once_and_reads_back_with_the_time(tmp_path, monkeypatch):
    monkeypatch.setenv('NBA_DATA_DIR', str(tmp_path))
    calls, pauses = [], []
    get = lambda url: calls.append(url) or (200, json.dumps(REAL).encode())    # noqa: E731
    out = bet365_dc.fetch(store(), NOW, get=get, pause=pauses.append)
    assert calls == ['https://sports.core.api.espn.com/v2/sports/soccer/leagues/eng.1/events/401879233/competitions/401879233/odds']
    assert out['saved'] == 1 and out['with_quote'] == 1 and pauses == []
    again = bet365_dc.fetch(store(), NOW, get=get, pause=pauses.append)        # within REFRESH: not asked again
    assert len(calls) == 1 and again['skipped_recent'] == 1
    q = bet365_dc.latest('soccer:eng.1:401879233', 'soccer:382', 'soccer:370', '2026-11-20T13:00:00Z')
    assert q['provider'] == 'Bet365' and q['retrieved_at'] == '2026-11-20T12:00:00Z' and q['prices']['draw_away']['decimal'] == 2.75
    assert bet365_dc.latest('soccer:eng.1:401879233', 'soccer:382', 'soccer:370', '2026-11-20T11:59:59Z') is None
    later = datetime(2026, 11, 20, 14, 1, tzinfo=timezone.utc)
    bet365_dc.fetch(store(), later, get=lambda url: (500, b''), pause=pauses.append)
    assert len(list((tmp_path / 'bet365-dc').rglob('*.json'))) == 1                # a failed request saves nothing
    assert bet365_dc.latest('soccer:eng.1:401879233', 'soccer:382', 'soccer:370', '2026-11-20T15:00:00Z')['retrieved_at'] \
        == '2026-11-20T12:00:00Z'


def test_fetch_never_exceeds_the_per_run_cap(tmp_path, monkeypatch):
    monkeypatch.setenv('NBA_DATA_DIR', str(tmp_path))
    c = store()
    for i in range(40):
        c.execute("INSERT INTO soccer_matches VALUES (?, 'ger.1', '2026-11-21T14:30:00Z', 'scheduled', 'soccer:1', 'soccer:2')",
                  (f'soccer:ger.1:9{i:03d}',))
    calls = []
    out = bet365_dc.fetch(c, NOW, get=lambda url: calls.append(url) or (200, b'{"items": []}'), pause=lambda s: None)
    assert len(calls) == bet365_dc.MAX_PER_RUN == out['requests'] and out['deferred'] == 41 - bet365_dc.MAX_PER_RUN


def test_derived_double_chance_and_draw_no_bet_match_the_research_figures():
    """docs/evidence/free-price-sources-2026-11-10 section 6: Fulham @ Man City, DC 1.183 / 2.904 / 1.234, DNB 1.236 / 5.235."""
    def q(side, dec, at='2026-11-11T02:32:38Z'):
        return {'market': '1x2', 'provider': 'DraftKings', 'side': side, 'decimal_odds': dec, 'retrieved_at': at}
    # DraftKings decimal prices that give the stored fair probabilities (any proportional set does)
    p = {'home': 0.7, 'draw': 0.1452, 'away': 0.1336}
    quotes = [q(k, 1 / v / 1.05) for k, v in p.items()]
    d = implied_lines.derived_dc_dnb(quotes)
    assert d['label'] == 'derived from match result prices' and 'Not a price you can bet at' in d['note']
    assert d['double_chance']['home_draw'] == round(1 / ((p['home'] + p['draw']) / sum(p.values())), 3)
    assert implied_lines.derived_dc_dnb(quotes[:2]) is None                                # all three sides needed
    assert implied_lines.derived_dc_dnb(quotes[:2] + [q('away', 7.0, '2026-11-11T03:00:00Z')]) is None   # one retrieval


def test_implied_team_totals_use_one_response_and_say_what_they_are():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.execute('CREATE TABLE market_quotes (game_id TEXT, provider TEXT, market TEXT, price_label TEXT, line REAL, '
              'retrieved_at TEXT, fetch_id INT)')
    c.executemany('INSERT INTO market_quotes VALUES (?,?,?,?,?,?,?)', [
        ('g', 'DraftKings', 'spread', 'current', -4.5, '2026-10-20T21:10:28Z', 1),
        ('g', 'DraftKings', 'total', 'current', 222.5, '2026-10-20T21:10:28Z', 1),
        ('g', 'DraftKings', 'spread', 'current', -6.5, '2026-10-20T22:00:00Z', 2)])      # no total in fetch 2
    home = implied_lines.implied_team_total(c, 'g', 'team_total_home', '2026-10-20T23:00:00Z', 'nba')
    away = implied_lines.implied_team_total(c, 'g', 'team_total_away', '2026-10-20T23:00:00Z', 'nba')
    assert (home['line'], away['line']) == (113.5, 109.0) and home['label'] == 'implied from spread and total'
    assert 'Not a price you can bet at' in home['note'] and 'puck line' not in home['note']
    assert 'puck line' in implied_lines.implied_team_total(c, 'g', 'team_total_home', '2026-10-20T23:00:00Z', 'nhl')['note']
    assert implied_lines.implied_team_total(c, 'g', 'team_total_home', '2026-10-20T21:00:00Z') is None
    assert implied_lines.implied_team_total(c, 'g', 'total', '2026-10-20T23:00:00Z') is None
