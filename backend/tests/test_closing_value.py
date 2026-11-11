"""Closing price check (scripts/closing_value.py): pick-time number against the last stored price before the start,
from the bettor's side; one append-only row per pick, recorded only after the start."""
import importlib.util
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('closing_value', ROOT / 'scripts' / 'closing_value.py')
cv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cv)


def test_better_and_worse_from_the_bettors_side():
    # spread lines are the HOME handicap: home bettor wants it higher, away bettor lower
    assert cv.compare('spread', 'a', -3.5, -110, -4.5, -110) == 'better'    # home -3.5 beats home -4.5
    assert cv.compare('spread', 'b', -3.5, -110, -4.5, -110) == 'worse'     # away +3.5 is worse than away +4.5
    assert cv.compare('total', 'a', 220.5, -110, 222.5, -110) == 'better'   # over 220.5 beats over 222.5
    assert cv.compare('total', 'b', 220.5, -110, 222.5, -110) == 'worse'
    assert cv.compare('winner', 'a', None, 150, None, 140) == 'better'
    assert cv.compare('winner', 'a', None, -1040, None, -970) == 'worse'
    assert cv.compare('spread', 'a', -3.5, -105, -3.5, -105) == 'same'


def test_card_labels_parse_to_the_stored_market_side_and_home_line():
    assert cv.parse_label('Over 226.5', 'POR', 'OKC') == ('total', 'a', 226.5)
    assert cv.parse_label('OKC win', 'POR', 'OKC') == ('winner', 'b', None)
    assert cv.parse_label('OKC -1.5', 'POR', 'OKC') == ('spread', 'b', 1.5)
    assert cv.parse_label('POR +1.5', 'POR', 'OKC') == ('spread', 'a', 1.5)
    assert cv.parse_label('OKC over 112.5', 'POR', 'OKC') is None          # team totals have no stored price
    text = '<b>PROP51 PICKS · NBA</b>\n<b>OKC @ POR</b>\nStarts\n\n━\n<b>Recommended pick</b>\n<b>OKC win</b>\n'
    assert cv.card_pick(text) == ('nba', 'OKC', 'POR', 'OKC win')
    assert cv.card_pick('PROP51 · COMBINED PARLAY TICKETS') is None


def stores(tmp_path):
    (tmp_path / 'data').mkdir()
    s = sqlite3.connect(tmp_path / 'data' / 'nba.sqlite')
    s.executescript("""CREATE TABLE teams (team_id TEXT, abbr TEXT);
        CREATE TABLE games (game_id TEXT, home_team_id TEXT, away_team_id TEXT, tipoff_at TEXT, status TEXT);
        CREATE TABLE game_forecasts (forecast_id INT, game_id TEXT, p_a REAL, p_mid REAL, p_b REAL, line REAL);
        CREATE TABLE market_quotes (quote_id INTEGER PRIMARY KEY, game_id TEXT, market TEXT, price_label TEXT, line REAL,
                                    price_a INT, price_b INT, retrieved_at TEXT);
        INSERT INTO teams VALUES ('h','POR'),('a','OKC');
        INSERT INTO games VALUES ('g1','h','a','2026-11-11T04:00:00Z','final');
        INSERT INTO game_forecasts VALUES (7,'g1',.4,0,.6,NULL);
        INSERT INTO market_quotes (game_id,market,price_label,line,price_a,price_b,retrieved_at) VALUES
          ('g1','winner','current',NULL,120,-140,'2026-11-11T02:00:00Z'),
          ('g1','winner','current',NULL,130,-150,'2026-11-11T03:55:00Z'),
          ('g1','winner','current',NULL,200,-250,'2026-11-11T04:30:00Z');""")
    s.commit(); s.close()
    led = sqlite3.connect(tmp_path / 'ledger.sqlite')
    led.executescript("""CREATE TABLE ticket_batches (batch_key TEXT, created_at TEXT, payload TEXT);
        CREATE TABLE deliveries (delivery_key TEXT, text TEXT, status TEXT, attempted_at TEXT);""")
    leg = dict(sport='nba', forecast_id=7, market='winner', percentage_history={'side': 'b'},
               quote_check=dict(price_now=-140, retrieved_at='2026-11-11T02:00:00Z'))
    led.execute("INSERT INTO ticket_batches VALUES ('b1','2026-11-11T02:05:00Z',?)",
                (json.dumps(dict(resolved=[dict(ticket_id='T1', legs=[leg])])),))
    card = '<b>PROP51 PICKS · NBA</b>\n<b>OKC @ POR</b>\nStarts\n<b>Recommended pick</b>\n<b>OKC win</b>'
    led.execute("INSERT INTO deliveries VALUES (?,?,?,?)", ('x' * 64, card, 'sent', '2026-11-11T02:01:00Z'))
    led.execute("INSERT INTO deliveries VALUES (?,?,?,?)", ('y' * 64, card, 'sent', '2026-11-11T03:56:00Z'))   # resent card
    led.execute("INSERT INTO deliveries VALUES (?,?,?,?)", ('z' * 64, card.replace('OKC win', 'No strong pick'), 'sent',
                                                            '2026-11-11T02:02:00Z'))
    led.commit(); led.close()


def test_rows_are_recorded_after_the_start_once_against_the_last_price_before_it(tmp_path, monkeypatch):
    stores(tmp_path)
    run = lambda now, done: cv.collect(str(tmp_path), str(tmp_path / 'ledger.sqlite'), str(tmp_path / 'copy'), now, done)
    (tmp_path / 'copy').mkdir()
    assert run(datetime(2026, 11, 11, 3, 0, tzinfo=timezone.utc), set()) == []          # not started: nothing yet
    for f in (tmp_path / 'copy').iterdir():
        f.unlink()
    done = set()
    rows = run(datetime(2026, 11, 11, 5, 0, tzinfo=timezone.utc), done)
    leg, card = sorted(rows, key=lambda r: r['kind'], reverse=True)
    assert leg['kind'] == 'ticket_leg' and leg['pick_price'] == -140 and leg['close_price'] == -150   # 04:30 quote is after the start
    assert leg['draftkings'] == 'better' and leg['close_minutes_before_start'] == 5.0
    assert card['kind'] == 'card_pick' and card['pick_price'] == -140 and card['draftkings'] == 'better'   # FIRST send
    assert len(rows) == 2 and card['key'] == 'card:nba:g1:OKC win'          # resend and no-pick card add nothing
    for f in (tmp_path / 'copy').iterdir():
        f.unlink()
    assert run(datetime(2026, 11, 11, 6, 0, tzinfo=timezone.utc), done) == []            # never recorded twice


def test_daily_schedule_runs_the_release_copy_against_the_live_root():
    import plistlib
    plist = plistlib.loads((ROOT / 'scripts/launchd/com.nba-predictor.closing-value.plist').read_bytes())
    assert plist['ProgramArguments'][1:] == ['/Users/andry/nba-predictor-delivery/scripts/closing_value.py', '--root',
                                             '/Users/andry/Desktop/nba-predictor-sprint1']
    assert plist['RunAtLoad'] is False and len(plist['StartCalendarInterval']) == 1


def test_a_postponed_game_is_not_recorded_until_it_is_played(tmp_path):
    stores(tmp_path)
    s = sqlite3.connect(tmp_path / 'data' / 'nba.sqlite')
    s.execute("UPDATE games SET status='postponed'")
    s.commit(); s.close()
    (tmp_path / 'copy').mkdir()
    assert cv.collect(str(tmp_path), str(tmp_path / 'ledger.sqlite'), str(tmp_path / 'copy'),
                      datetime(2026, 11, 11, 5, 0, tzinfo=timezone.utc), set()) == []


def test_a_line_cut_off_by_a_crash_does_not_stop_later_runs(tmp_path):
    path = tmp_path / 'records.jsonl'
    path.write_text(json.dumps(dict(key='a')) + '\n{"key": "b", "sta')
    assert cv.read_keys(path) == ({'a'}, 1)
