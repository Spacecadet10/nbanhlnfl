"""Early board (paper only, scripts/early_board.py): each game-market graded once from its first board, grades saved
write-once under the data folder, other sportsbooks read from the saved price files only."""
import importlib.util
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('early_board', ROOT / 'scripts' / 'early_board.py')
eb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eb)

NOW = datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc)


def entry(made_at, game='g1', market='total', line=220.5, side='a', edge=.03, price=-110):
    return dict(made_at=made_at, sport='nba', game_id=game, matchup='BOS @ NY', tipoff='2026-11-12T00:00:00Z', market=market,
                side=side, pick='Over 220.5', line=line, price=price, quote_id=1, quote_retrieved_at=made_at, model_prob=.55,
                break_even=.524, market_prob=.5, edge=edge, model_line=222.0, hours_ahead=20.0, model='m')


def store():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript("""CREATE TABLE games (game_id TEXT, tipoff_at TEXT, status TEXT, home_score INT, away_score INT);
        CREATE TABLE market_quotes (quote_id INTEGER PRIMARY KEY, game_id TEXT, market TEXT, price_label TEXT, line REAL,
                                    price_a INT, price_b INT, retrieved_at TEXT);""")
    c.execute("INSERT INTO games VALUES ('g1','2026-11-12T00:00:00Z','final',115,110)")   # 225 points: Over 220.5 wins
    c.execute("INSERT INTO market_quotes (game_id, market, price_label, line, price_a, price_b, retrieved_at) "
              "VALUES ('g1','total','current',222.5,-110,-110,'2026-11-11T23:50:00Z')")
    return c


def test_a_game_on_two_boards_is_graded_once_from_the_first(tmp_path):
    (tmp_path / 'board-2026-11-11-1100.jsonl').write_text(json.dumps(entry('2026-11-11T16:00:00Z', line=220.5)) + '\n')
    (tmp_path / 'board-2026-11-11-2330.jsonl').write_text(json.dumps(entry('2026-11-12T04:30:00Z', line=221.5)) + '\n\n')
    first = eb.first_entries(str(tmp_path))
    assert len(first) == 1 and first[0]['line'] == 220.5


def test_grade_is_saved_write_once_and_says_when_the_sample_is_too_small(tmp_path, monkeypatch):
    (tmp_path / 'board-2026-11-11-1100.jsonl').write_text(
        json.dumps(entry('2026-11-11T16:00:00Z')) + '\n' + json.dumps(entry('2026-11-11T16:00:00Z', game='gone')) + '\n')
    monkeypatch.setattr(eb, 'copy_store', lambda root, sport, dest: store())
    summary = eb.grade('unused', str(tmp_path), 'unused', NOW)
    assert summary['graded'] == 1 and summary['leans'] == 1           # the game missing from the store is skipped
    row = summary['by_sport_market']['nba total']
    assert row == dict(leans=1, settled=1, paper_return_units=0.91, line_toward=1, line_away=0, line_unchanged=0)
    assert summary['sample_note'].startswith('too small to judge')
    saved = list((tmp_path / 'grades').glob('grade-*.json'))
    assert len(saved) == 1 and json.loads(saved[0].read_text())['rows'][0]['unit_return'] == pytest.approx(100 / 110)
    with pytest.raises(FileExistsError):                               # never overwrites an earlier grade
        eb.grade('unused', str(tmp_path), 'unused', NOW)


def test_other_sportsbooks_come_from_the_saved_price_files_only():
    event = dict(event_id='e', event_date='2026-11-12T00:00:00Z', score=dict(event_status='STATUS_SCHEDULED'),
                 teams=[dict(team_id=1, abbreviation='NYK', is_home=True, is_away=False),
                        dict(team_id=2, abbreviation='BOS', is_away=True, is_home=False)],
                 markets=[dict(market_id=3, period_id=0, participants=[dict(id=9, name='Over', lines=[
                     dict(value='220.5', prices={'23': dict(price=-105, is_main_line=True)}),
                     dict(value='221.5', prices={'22': dict(price=-102, is_main_line=True)})])])])
    cache = {'responses': [dict(_fetched_at='2026-11-11T16:00:00Z', _sport='nba', _date='2026-11-12', events=[event])]}
    offers, at = eb.book_offers('nba', 'NY', 'BOS', '2026-11-12T00:00:00Z', 'total', 'a', 220.5,
                                datetime(2026, 11, 11, 16, 30, tzinfo=timezone.utc), cache)
    assert offers == {'FanDuel': (220.5, -105), 'BetMGM': (221.5, -102)} and at == '2026-11-11T16:00:00Z'
    assert not hasattr(eb, 'odds_books')


def test_boards_default_to_the_data_folder_and_the_schedule_is_paper_only():
    import plistlib
    src = (ROOT / 'scripts' / 'early_board.py').read_text()
    assert '"data", "early-board"' in src and '"docs", "evidence", "early-board"' not in src
    wrapper = (ROOT / 'scripts' / 'early_board.sh').read_text()
    commands = [l for l in wrapper.splitlines() if l.strip() and not l.lstrip().startswith('#')]
    assert not any('telegram' in l.lower() or 'release_tickets' in l or 'send' in l for l in commands)
    plist = plistlib.loads((ROOT / 'scripts/launchd/com.nba-predictor.early-board.plist').read_bytes())
    assert plist['ProgramArguments'] == ['/bin/sh', '/Users/andry/nba-predictor-delivery/scripts/early_board.sh']
    assert plist['RunAtLoad'] is False and len(plist['StartCalendarInterval']) == 2
