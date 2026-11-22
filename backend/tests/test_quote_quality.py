"""Data-quality fixes before the Nov 18 research window (owner request, Nov 17 2026):
1. the Soccer scorecard prices a 1X2 pick exactly as the card does (latest offered row per side as of the forecast);
2. 'Draft Kings' and 'DraftKings' are one sportsbook in every lookup, saved names untouched, never mixed with other books;
3. a card says its sportsbook % is the price saved at forecast time, not at send time;
4. the scorecard's player-file guard looks at the file's content, not only its modified time."""
import hashlib
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from app import implied_lines, lineup, odds, soccer_book
from app import scorecard as sc
from app import telegram as tg

T = '2026-11-11T23:00:00Z'


# ---- 1. Soccer scorecard = card -------------------------------------------------------------------------------------------
def soccer_store():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript('''CREATE TABLE teams (team_id TEXT, name TEXT);
      CREATE TABLE soccer_matches (match_id TEXT, kickoff_at TEXT, home_team_id TEXT, away_team_id TEXT);
      CREATE TABLE soccer_live_forecasts (forecast_id INTEGER PRIMARY KEY, match_id TEXT, competition_id TEXT, market TEXT,
        version INT, model TEXT, p_home REAL, p_draw REAL, p_away REAL, cutoff_at TEXT, generated_at TEXT, inputs_through TEXT,
        source_retrieved_at TEXT);
      CREATE TABLE soccer_odds_quotes (quote_id INTEGER PRIMARY KEY, match_id TEXT, provider TEXT, market TEXT, side TEXT,
        price_kind TEXT, offered INT, line REAL, american INT, decimal_odds REAL, retrieved_at TEXT);
      INSERT INTO teams VALUES ('h','Albania'),('a','Finland');
      INSERT INTO soccer_matches VALUES ('m','2026-11-12T19:45:00Z','h','a');''')
    c.execute("INSERT INTO soccer_live_forecasts VALUES (1,'m','uefa.nations','1x2_regulation',1,'intl-elo',0.5,0.3,0.2,"
              "'2026-11-12T06:00:00Z',?,NULL,?)", (T, T))
    return c


def quote(c, side, d, at, provider='DraftKings', offered=1):
    c.execute("INSERT INTO soccer_odds_quotes (match_id, provider, market, side, price_kind, offered, american, decimal_odds, "
              "retrieved_at) VALUES ('m',?,'1x2',?,'current',?,?,?,?)", (provider, side, offered, int(100 * (d - 1)), d, at))


def test_soccer_scorecard_prices_the_pick_from_each_sides_latest_row_like_the_card():
    """The store saves only changed sides: at 22:30 home and draw moved, away did not. The old scorecard took the last
    retrieval holding all three sides (20:00) and was stale against the card on 10 of 53 live rows."""
    c = soccer_store()
    for side, d in zip(('home', 'draw', 'away'), (2.0, 3.2, 4.0)):
        quote(c, side, d, '2026-11-11T20:00:00Z')
    quote(c, 'home', 1.8, '2026-11-11T22:30:00Z')
    quote(c, 'draw', 3.5, '2026-11-11T22:30:00Z')
    for side, d in zip(('home', 'draw', 'away'), (1.2, 6.0, 12.0)):          # after the forecast: never used
        quote(c, side, d, '2026-11-11T23:30:00Z')
    k = sc.connect(Path(':memory:'), create=True)
    assert sc.sync(c, k, 'soccer', now='2026-11-11T23:40:00Z')['written'] == 1
    r = k.execute('SELECT * FROM scorecard').fetchone()
    inv = {'home': 1 / 1.8, 'draw': 1 / 3.5, 'away': 1 / 4.0}
    assert r['book_devig'] == pytest.approx(inv['home'] / sum(inv.values()))
    card = soccer_book.one_x_two(c, {'match_id': 'm', 'kickoff_at': '2026-11-12T19:45:00Z', 'generated_at': T,
                                     'most_likely': 'home', 'probabilities': {'home': .5, 'draw': .3, 'away': .2}})
    assert r['book_devig'] == pytest.approx(card['book_probability'])          # scorecard = card
    assert r['book_retrieved_at'] == '2026-11-11T22:30:00Z' and r['book_price'] == 80
    sides = json.loads(r['detail'])['book_side_retrieved_at']
    assert sides == {'home': '2026-11-11T22:30:00Z', 'draw': '2026-11-11T22:30:00Z', 'away': '2026-11-11T20:00:00Z'}
    assert r['open_retrieved_at'] == '2026-11-11T20:00:00Z' and r['move_pts'] is not None


def test_soccer_scorecard_has_no_quote_when_a_side_was_withdrawn_by_the_forecast():
    c = soccer_store()
    for side, d in zip(('home', 'draw', 'away'), (2.0, 3.2, 4.0)):
        quote(c, side, d, '2026-11-11T20:00:00Z')
    quote(c, 'away', 4.0, '2026-11-11T22:00:00Z', offered=0)                  # withdrawal row
    k = sc.connect(Path(':memory:'), create=True)
    sc.sync(c, k, 'soccer', now='2026-11-11T23:40:00Z')
    r = k.execute('SELECT * FROM scorecard').fetchone()
    assert r['book_state'] == 'missing' and r['book_devig'] is None


# ---- 2. 'Draft Kings' == 'DraftKings' -------------------------------------------------------------------------------------
def test_provider_key_joins_only_spellings_of_the_same_book():
    assert odds.provider_key('Draft Kings') == odds.provider_key('DraftKings') == odds.provider_key(' draftkings ')
    assert odds.provider_key('FanDuel') != odds.provider_key('DraftKings') and odds.provider_key(None) is None


def test_soccer_card_reads_both_spellings_as_one_book_and_keeps_the_saved_name():
    c = soccer_store()
    quote(c, 'home', 2.0, '2026-11-11T20:00:00Z')
    quote(c, 'draw', 3.2, '2026-11-11T20:00:00Z')
    quote(c, 'away', 4.0, '2026-11-11T20:00:00Z')
    quote(c, 'home', 1.8, '2026-11-11T22:30:00Z', provider='Draft Kings')      # the source's new spelling
    quote(c, 'draw', 3.0, '2026-11-11T22:30:00Z', provider='FanDuel')          # another book: never mixed in
    f = {'match_id': 'm', 'kickoff_at': '2026-11-12T19:45:00Z', 'generated_at': T, 'most_likely': 'home',
         'probabilities': {'home': .5, 'draw': .3, 'away': .2}}
    out = soccer_book.one_x_two(c, f)
    inv = {'home': 1 / 1.8, 'draw': 1 / 3.2, 'away': 1 / 4.0}
    assert out['status'] == 'exact' and out['book_probability'] == pytest.approx(inv['home'] / sum(inv.values()))
    assert c.execute("SELECT COUNT(*) FROM soccer_odds_quotes WHERE provider='Draft Kings'").fetchone()[0] == 1   # raw kept


def test_implied_team_total_reads_the_new_spelling():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.execute('CREATE TABLE market_quotes (quote_id INTEGER PRIMARY KEY, game_id TEXT, provider TEXT, market TEXT, '
              'price_label TEXT, line REAL, retrieved_at TEXT, fetch_id INT)')
    c.executemany('INSERT INTO market_quotes (game_id, provider, market, price_label, line, retrieved_at, fetch_id) '
                  'VALUES (?,?,?,?,?,?,?)', [('g', 'Draft Kings', 'spread', 'current', -4.0, T, 7),
                                             ('g', 'Draft Kings', 'total', 'current', 220.0, T, 7)])
    out = implied_lines.implied_team_total(c, 'g', 'team_total_home', T)
    assert out is not None and out['line'] == pytest.approx(112.0)


def test_winner_open_and_move_follow_the_book_across_its_two_spellings():
    """Opening quote saved as 'DraftKings', the forecast's quote as 'Draft Kings': the same book, so the move exists.
    A FanDuel quote earlier than both is never the opening price."""
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.execute('CREATE TABLE market_quotes (quote_id INTEGER PRIMARY KEY, game_id TEXT, provider TEXT, market TEXT, '
              'price_label TEXT, line REAL, price_a INT, price_mid INT, price_b INT, p_a_devig REAL, p_mid_devig REAL, '
              'p_b_devig REAL, retrieved_at TEXT, fetch_id INT)')
    c.executemany('INSERT INTO market_quotes (game_id, provider, market, price_label, price_a, price_b, p_a_devig, p_b_devig, '
                  'retrieved_at) VALUES (?,?,?,?,?,?,?,?,?)',
                  [('g', 'FanDuel', 'winner', 'current', -200, 170, 0.65, 0.35, '2026-11-11T08:00:00Z'),
                   ('g', 'DraftKings', 'winner', 'current', -150, 130, 0.58, 0.42, '2026-11-11T10:00:00Z'),
                   ('g', 'Draft Kings', 'winner', 'current', -170, 145, 0.61, 0.39, '2026-11-11T22:00:00Z')])
    first = sc._first_quote(c, 'nhl', 'g', 'winner', T, provider='Draft Kings')
    assert first['retrieved_at'] == '2026-11-11T10:00:00Z' and first['provider'] == 'DraftKings'
    assert odds.winner_quote(c, 'nhl', 'g', T, provider='DraftKings')['provider'] == 'Draft Kings'   # raw name returned


# ---- 3. The card says when its sportsbook % was priced -----------------------------------------------------------------
def test_card_labels_the_sportsbook_percentage_as_the_price_at_forecast_time():
    """On the last line, which the card image draws (review HIGH, Nov 17: a line below the divider never reached the PNG)."""
    import test_telegram as tt
    from PIL import Image
    import io
    from app import telegram_card
    d = tt.priced_card()
    key, text = tg.message_batches(d, '123')[0]
    label = 'Sportsbook % = no-vig price as of the forecast (02:30 PM EST)'
    assert text.splitlines()[-1].startswith('Updated ') and text.splitlines()[-1].endswith(label)
    assert Image.open(io.BytesIO(telegram_card.render_card(text))).size[0] == 1080
    d['picks'][0]['market_prob'] = None
    assert tg.message_batches(d, '123')[0][0] == key                                  # same delivery key: no resend
    assert 'as of the forecast' not in tg.message_batches(tt.report(), '123')[0][1]   # no sportsbook %: no label


def test_a_sportsbook_percentage_priced_near_send_time_is_not_labelled_as_forecast_time():
    import test_telegram as tt
    d = tt.priced_card()
    for p in d['picks']:
        p.update(card_market_prob=p.pop('market_prob'), card_comparison_basis='The Odds API pregame quote')
    assert 'as of the forecast' not in tg.message_batches(d, '123')[0][1]


# ---- 4. Player-file guard on content ------------------------------------------------------------------------------------
def _player_file(tmp_path, monkeypatch, body: bytes, mtime: str, attempts: list):
    from app import availability as av
    monkeypatch.setattr(av, 'research_dir', lambda: tmp_path)
    path = av.player_box_path(2027)
    path.parent.mkdir(parents=True)
    path.write_bytes(body)
    ts = datetime.fromisoformat(mtime.replace('Z', '+00:00')).timestamp()
    os.utime(path, (ts, ts))
    (tmp_path / 'attempts.jsonl').write_text(''.join(json.dumps(a) + '\n' for a in attempts))


def _attempt(body: bytes, at: str, name='player_box_2027.csv', outcome='stored'):
    return {'kind': 'player_box', 'name': name, 'completed_at': at, 'outcome': outcome,
            'sha256': 'sha256:' + hashlib.sha256(body).hexdigest()}


def test_a_player_file_rewritten_with_the_same_content_is_still_as_of_the_forecast(tmp_path, monkeypatch):
    body = b'athlete_id,game_id\n1,1\n'
    _player_file(tmp_path, monkeypatch, body, '2026-11-12T03:00:00Z',
                 [_attempt(body, '2026-11-11T09:00:00Z'), _attempt(body, '2026-11-12T03:00:00Z')])
    assert lineup._box_written_after(2027, T) is False


def test_a_player_file_with_new_rows_after_the_forecast_stays_unknown(tmp_path, monkeypatch):
    old, new = b'athlete_id,game_id\n1,1\n', b'athlete_id,game_id\n1,1\n1,2\n'
    _player_file(tmp_path, monkeypatch, new, '2026-11-12T03:00:00Z',
                 [_attempt(old, '2026-11-11T09:00:00Z'), _attempt(new, '2026-11-12T03:00:00Z')])
    assert lineup._box_written_after(2027, T) is True


def test_a_player_file_first_downloaded_after_the_forecast_stays_unknown(tmp_path, monkeypatch):
    body = b'athlete_id,game_id\n1,1\n'
    _player_file(tmp_path, monkeypatch, body, '2026-11-12T03:00:00Z',
                 [_attempt(body, '2026-11-12T03:00:00Z'), _attempt(body, '2026-11-11T09:00:00Z', outcome='failed')])
    assert lineup._box_written_after(2027, T) is True


def test_a_player_file_written_before_the_forecast_is_as_of_the_forecast(tmp_path, monkeypatch):
    body = b'athlete_id,game_id\n1,1\n'
    _player_file(tmp_path, monkeypatch, body, '2026-11-11T09:00:00Z', [])
    assert lineup._box_written_after(2027, T) is False


# ---- 5. one forecast-time quote rule (owner review, Nov 22): card, ticket and scorecard read the same quote -----------------
FT = '2026-11-20T18:00:00Z'      # forecast time
TIP = '2026-11-20T23:00:00Z'


def mq_store():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript('''CREATE TABLE market_quotes (quote_id INTEGER PRIMARY KEY, sport TEXT, game_id TEXT, provider TEXT,
        market TEXT, price_label TEXT, line REAL, price_a INT, price_mid INT, price_b INT, p_a_devig REAL, p_mid_devig REAL,
        p_b_devig REAL, retrieved_at TEXT);
      CREATE TABLE game_forecasts (forecast_id INTEGER PRIMARY KEY, game_id TEXT, market TEXT, line REAL, line_source TEXT,
        market_quote_id INT, generated_at TEXT, tipoff_at TEXT);
      INSERT INTO game_forecasts VALUES (1,'g','spread',-3.5,'bookmaker',NULL,'2026-11-20T18:00:00Z','2026-11-20T23:00:00Z');''')
    return c


def mq(c, provider, at, line=-3.5, pa=0.55, label='current', market='spread'):
    return c.execute("INSERT INTO market_quotes (sport, game_id, provider, market, price_label, line, price_a, price_b, "
                     "p_a_devig, p_b_devig, retrieved_at) VALUES ('nhl','g',?,?,?,?,-110,-110,?,?,?)",
                     (provider, market, label, line, pa, 1 - pa, at)).lastrowid


def all_consumers(c):
    """(card %, scorecard %, sportsbook-comparison quote id) for forecast 1 side a."""
    from app import sportsbook, tickets
    pick = {'forecast_id': 1, 'game_id': 'g', 'market': 'spread', 'percentage_history': {'side': 'a'}}
    card = tickets.card_market_prob(c, pick)
    g = {'game_id': 'g', 'tipoff_at': TIP}
    rec = {'market': 'spread', 'side': 'a', 'line': -3.5, 'line_source': 'bookmaker', 'market_quote_id': None,
           'generated_at': FT}
    (state, _), out = sc._book(c, 'nhl', g, rec)
    line_q, _ = sportsbook._line_quote(c, 'spread', g, rec)
    return card, out.get('book_devig'), None if line_q is None else line_q['quote_id']


def test_same_forecast_gets_the_same_quote_in_card_ticket_scorecard_and_sportsbook():
    c = mq_store()
    mq(c, 'DraftKings', '2026-11-20T10:00:00Z', pa=0.50)
    newest = mq(c, 'DraftKings', '2026-11-20T17:00:00Z', pa=0.58)
    mq(c, 'DraftKings', '2026-11-20T18:30:00Z', pa=0.70)            # after the forecast: excluded
    mq(c, 'DraftKings', '2026-11-20T17:30:00Z', pa=0.61, line=-4.5)  # another line: excluded
    card, scorecard, quote_id = all_consumers(c)
    assert card == pytest.approx(0.58) and scorecard == pytest.approx(0.58) and quote_id == newest


def test_forecast_quote_ties_break_deterministically_whatever_the_insert_order():
    for order in (('FanDuel', 'DraftKings', 'Zeta'), ('Zeta', 'DraftKings', 'FanDuel'), ('Zeta', 'FanDuel')):
        c = mq_store()
        for i, p in enumerate(order):
            mq(c, p, '2026-11-20T17:00:00Z', pa=0.40 + 0.05 * (p == 'DraftKings') + 0.01 * (p == 'Zeta'))
        want = 0.45 if 'DraftKings' in order else 0.40            # DraftKings first, else provider key order
        assert all_consumers(c)[0] == pytest.approx(want), order
        assert odds.forecast_quote(c, 'g', 'spread', -3.5, FT, TIP)['provider'] == ('DraftKings' if 'DraftKings' in order else 'FanDuel')


def test_forecast_quote_tie_prefers_the_quote_recorded_with_the_forecast():
    c = mq_store()
    mq(c, 'DraftKings', '2026-11-20T17:00:00Z', pa=0.50)
    own = mq(c, 'FanDuel', '2026-11-20T17:00:00Z', pa=0.60)
    assert odds.forecast_quote(c, 'g', 'spread', -3.5, FT, TIP, recorded_id=own)['quote_id'] == own
    assert odds.forecast_quote(c, 'g', 'spread', -3.5, FT, TIP)['provider'] == 'DraftKings'


def test_forecast_quote_none_when_only_after_forecast_after_start_or_other_line():
    c = mq_store()
    mq(c, 'DraftKings', '2026-11-20T18:00:01Z')
    mq(c, 'DraftKings', '2026-11-20T17:00:00Z', line=-2.5)
    assert odds.forecast_quote(c, 'g', 'spread', -3.5, FT, TIP) is None
    assert all_consumers(c)[:2] == (None, None)
    c2 = mq_store()
    mq(c2, 'DraftKings', '2026-11-20T23:30:00Z')                      # retrieved after the start: never a forecast price
    assert odds.forecast_quote(c2, 'g', 'spread', -3.5, '2026-11-21T00:00:00Z', TIP) is None


def test_send_time_quote_is_a_separate_named_check_and_may_see_a_later_quote():
    c = mq_store()
    first = mq(c, 'DraftKings', '2026-11-20T17:00:00Z', pa=0.58)
    later = mq(c, 'DraftKings', '2026-11-20T20:00:00Z', pa=0.62)
    assert odds.forecast_quote(c, 'g', 'spread', -3.5, FT, TIP)['quote_id'] == first
    assert odds.send_time_quote(c, 'g', 'spread', '2026-11-20T21:00:00Z', provider='draft kings')['quote_id'] == later
