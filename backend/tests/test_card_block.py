"""The one per-market Telegram block, the same for NBA, NHL, NFL, NCAAB and Soccer (app/telegram.market_block).

Text-only additions: the delivery key and card_identity must not move, so no card is sent twice. No network, no real bot."""
import copy
import os
import sqlite3
from pathlib import Path

import pytest

from app import telegram as tg, telegram_card

NOW = '2026-11-02T20:00:00Z'
TELEGRAM = {'source': 'telegram_cards', 'label': 'Recovered Telegram cards', 'period': {'from': '2026-11-02', 'to': '2026-11-14'},
            'model_version_known': False, 'line_source': 'bookmaker', 'wins': 3, 'losses': 2, 'pushes': 1, 'settled': 6}
SPORTSBOOK = {'status': 'exact', 'app_probability': 0.571, 'book_probability': 0.5043, 'gap_pp': 6.7, 'provider': 'DraftKings',
              'price': '-110/-110', 'line': -8.5, 'quote_id': 9, 'retrieved_at': '2026-11-02T19:29:30Z', 'reason': None, 'basis': None}
COVERAGE = {'live_since': '2026-11-14T00:00:00Z', 'telegram_period': None, 'history_note': 'x'}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('NBA_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(telegram_card, '_logo', lambda *_: None)


def pick(sport='nba', **kw):
    p = {'game_id': 'g1', 'forecast_id': 1, 'market': 'spread', 'market_title': 'Point spread', 'matchup': 'BOS @ ORL',
         'tipoff': '2026-11-02T21:00:00Z', 'generated_at': '2026-11-02T19:30:00Z', 'status': 'scheduled', 'grade': 'pending',
         'pick_label': 'ORL -8.5', 'opposite_pick_label': 'BOS +8.5', 'probability': 0.5708, 'strong': True, 'game_rank': 1,
         'percentage_history': {'sport': sport, 'market': 'spread', 'model_version': 'points-test', 'source': 'primary_recorded',
                                'percentage': '57.1', 'wins': 3, 'losses': 1, 'settled': 4, 'small_sample': True,
                                'line_source': 'bookmaker'}}
    p.update(kw)
    return p


def card(sport='nba', picks=None, **kw):
    return {'sport': sport, 'as_of': NOW, 'picks': picks or [pick(sport)], **kw}


def history(p, **kw):
    p['percentage_history'].update(kw)
    return p


def text_of(d, **kw):
    return tg.message_batches(d, '123', **kw)[0][1]


@pytest.mark.parametrize('sport', ['nba', 'nhl', 'nfl', 'ncaab'])
def test_every_sport_shows_the_same_items(sport):
    p = history(pick(sport, sportsbook=SPORTSBOOK, market_prob=0.5043,
                     record_basis={'forecast_id': 7, 'generated_at': '2026-11-02T19:30:00Z', 'pregame_forecasts': 3,
                                   'published': {'forecast_id': 5, 'side': 'a', 'line': -9.5, 'probability': 0.52, 'grade': 'win'}}),
                pushes=2, telegram=TELEGRAM)
    text = text_of(card(sport, [p]))
    assert '<b>Model 57.1%</b> · 3W–1L · 2 push (4 settled)' in text
    assert ('Sportsbook quote: DraftKings -110/-110 at -8.5 · no-vig 50.4% · gap +6.7 pts · underpriced · quote 02:29 PM EST') in text
    assert 'Recovered Telegram cards 11-02 to 11-14: 3W–2L · 1 push (6 settled) · model version not recorded' in text
    assert 'Record uses the latest pregame forecast (#7, 02:30 PM EST); 2 earlier kept for audit' in text
    assert 'Telegram card sent forecast #5 (ORL -9.5, 52.0%): won' in text and '(a -9.5' not in text
    assert text.count('Sportsbook quote:') == 1            # one sportsbook line per market
    assert 'sportsbook 50.4%' not in text and 'bookmaker line at forecast · sportsbook' not in text
    assert 'Counts are other settled games, not this game.' in text
    assert telegram_card.render_card(text).startswith(b'\x89PNG')


def test_no_quote_says_why_and_never_shows_a_number():
    sb = {**SPORTSBOOK, 'status': 'no_quote', 'book_probability': None, 'gap_pp': None, 'provider': None, 'price': None,
          'reason': 'No sportsbook quote saved at this line'}
    text = text_of(card('nba', [history(pick(sportsbook=sb), telegram=TELEGRAM)]))
    assert 'Sportsbook: No sportsbook quote saved at this line' in text
    assert 'Sportsbook quote:' not in text and 'sportsbook probability unavailable' not in text


def test_model_line_has_its_own_reason():
    sb = {**SPORTSBOOK, 'status': 'model_line', 'book_probability': None, 'reason': 'Model line: no sportsbook line to compare'}
    text = text_of(card('nhl', [history(pick('nhl', sportsbook=sb), line_source='model')]))
    assert 'Sportsbook: Model line: no sportsbook line to compare' in text


def test_no_settled_history_and_repeated_percentage_wording():
    tg0 = {**TELEGRAM, 'wins': 0, 'losses': 0, 'pushes': 0, 'settled': 0}
    text = text_of(card('nba', [history(pick(), wins=0, losses=0, settled=0, telegram=tg0)]))
    assert '0W–0L (0 settled)' in text and 'No settled matching examples.' in text
    assert 'none settled at this exact %' in text
    rep = text_of(card('nba', [history(pick(), wins=5, losses=5, settled=10, small_sample=False, telegram=TELEGRAM)]))
    assert '5W–5L (10 settled)' in rep


def test_ncaab_has_no_recovered_telegram_history_and_says_when_recording_began():
    text = text_of(card('ncaab', [history(pick('ncaab'), telegram=None, coverage=COVERAGE)]))
    assert 'No recovered Telegram history for NCAAB (recording began 2026-11-14)' in text
    nba = text_of(card('nba', [history(pick('nba'), telegram=None, coverage=COVERAGE)]))
    assert 'No recovered Telegram history for NBA' in nba and 'recording began' not in nba


def test_an_older_api_without_the_new_fields_adds_no_lines_and_invents_nothing():
    text = text_of(card('nba'))
    for word in ('Recovered Telegram', 'No recovered', 'Sportsbook quote', 'Record uses', ' push'):
        assert word not in text


def test_soccer_dnb_basis_and_pushes_use_the_same_block():
    sb = {**SPORTSBOOK, 'basis': 'conditional on no push', 'line': None, 'price': '-150/+120'}
    p = history(pick('soccer', market='draw_no_bet', market_title='Draw no bet (90 min)', pick_label='Senegal DNB',
                     sportsbook=sb, cutoff_at='2026-11-02T20:30:00Z', forecast_id='1x2:1:dnb:home', probability=0.6),
                market='draw_no_bet', percentage='60.0', pushes=4, telegram={**TELEGRAM, 'line_source': 'none'}, line_source='none')
    p['strong'] = False
    text = text_of(card('soccer', [p]), context_selection=True)
    assert '3W–1L · 4 push (4 settled)' in text
    assert 'DraftKings -150/+120 · no-vig 50.4% · gap +6.7 pts · underpriced · quote 02:29 PM EST · conditional on no push' in text
    assert telegram_card.render_card(text).startswith(b'\x89PNG')


def test_soccer_without_history_keeps_its_legacy_lines_and_gains_the_quote_detail():
    p = dict(game_id='s1', forecast_id='1x2:1:double_chance:home_draw', market='double_chance', market_title='Double chance (90 min)',
             matchup='Senegal vs Belarus', pick_label='Senegal or draw (90 min)', probability=.92, status='scheduled', grade='pending',
             generated_at='2026-11-02T05:00:00Z', cutoff_at='2026-11-02T06:00:00Z', tipoff='2026-11-02T21:00:00Z',
             competition='friendly', strong=False, game_rank=None, sportsbook={**SPORTSBOOK, 'basis': 'conditional on no draw'})
    text = text_of(card('soccer', [p]), context_selection=True)
    assert 'Exact-% history unavailable' in text and 'conditional on no draw' in text
    assert 'sportsbook probability unavailable' not in text      # header and block read the same source
    assert 'sportsbook 50.4%' in text.split('Market breakdown')[0]


def test_the_header_and_the_block_agree_on_the_sportsbook_source():
    p = history(pick('soccer', market='draw_no_bet', market_title='Draw no bet (90 min)', pick_label='Senegal DNB', sportsbook=SPORTSBOOK,
                     cutoff_at='2026-11-02T20:30:00Z', forecast_id='1x2:1:dnb:home', probability=0.6),
                market='draw_no_bet', percentage='60.0', line_source='none')
    p['strong'] = False
    text = text_of(card('soccer', [p]), context_selection=True)
    head, block = text.split('Market breakdown')
    assert 'sportsbook 50.4%' in head and 'sportsbook probability unavailable' not in head and 'no-vig 50.4%' in block


def test_published_pick_is_written_in_words_for_every_market_shape():
    rb = lambda side, line: {'forecast_id': 7, 'generated_at': '2026-11-02T19:30:00Z', 'pregame_forecasts': 1,
                             'published': {'forecast_id': 5, 'side': side, 'line': line, 'probability': 0.52, 'grade': 'loss'}}
    cases = [('total', 'b', 215.5, 'Under 215.5'), ('total', 'a', 215.5, 'Over 215.5'), ('spread', 'b', 9.5, 'BOS +9.5'),
             ('winner', 'a', None, 'ORL'), ('reg3', 'mid', None, 'Draw'), ('team_total_home', 'a', 109.5, 'ORL over 109.5')]
    for market, side, line, words in cases:
        lines = tg.record_basis_lines(rb(side, line), market, 'BOS @ ORL')
        assert lines == [f'Telegram card sent forecast #5 ({words}, 52.0%): lost'], (market, lines)


def test_a_long_card_drops_the_optional_detail_before_it_is_refused():
    long_basis = {'forecast_id': 7, 'generated_at': '2026-11-02T19:30:00Z', 'pregame_forecasts': 2}
    p = history(pick(sportsbook={**SPORTSBOOK, 'basis': 'x ' * 300}, record_basis=long_basis), telegram=TELEGRAM)
    d = card('nba', [copy.deepcopy(p) for _ in range(5)])
    for i, q in enumerate(d['picks']):
        q['market'] = q['percentage_history']['market'] = ('spread', 'total', 'winner', 'team_total_home', 'team_total_away')[i]
    text = text_of(d)
    assert len(text) <= 4000 and 'Sportsbook quote:' not in text and 'Record uses' in text


# --- the delivery key and card_identity do not move -------------------------------------------------------------------------
GOLDEN_FILE = Path(__file__).with_name('card_block_golden.json')


def golden_cases():
    base = card('nba')
    full = card('nba', [history(pick(sportsbook=SPORTSBOOK, market_prob=0.5043, record_basis={'forecast_id': 7, 'pregame_forecasts': 2}),
                                pushes=1, telegram=TELEGRAM)])
    return {'nba_plain': base, 'nba_full': full,
            'nhl_full': card('nhl', [history(pick('nhl', sportsbook=SPORTSBOOK), telegram=TELEGRAM)]),
            'ncaab_full': card('ncaab', [history(pick('ncaab'), telegram=None, coverage=COVERAGE)]),
            'nfl_full': card('nfl', [history(pick('nfl', sportsbook=SPORTSBOOK), pushes=2)])}


def test_delivery_key_and_card_identity_equal_the_values_of_the_previous_layout():
    """GOLDEN holds the key and identity the layout before this change produced for the same reports (captured with the old
    telegram.py): adding lines after the pick must not change either."""
    import json
    golden = json.loads(GOLDEN_FILE.read_text())
    for name, report in golden_cases().items():
        key, text = tg.message_batches(report, '123')[0]
        assert key == golden[name]['key'], name
        assert list(tg.card_identity(text)) == golden[name]['identity'], name


def test_ledger_texts_keep_their_identity_when_the_new_lines_are_added():
    """For real sent cards (a copy of the delivery ledger; set COURTSIDE_LEDGER_COPY): the first three lines and the line after
    '<b>Recommended pick</b>' are what card_identity reads, and the new block lines are only ever added after the pick."""
    path = os.environ.get('COURTSIDE_LEDGER_COPY')
    if not path or not Path(path).is_file():
        pytest.skip('COURTSIDE_LEDGER_COPY not set')
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    rows = [r[0] for r in conn.execute("SELECT text FROM deliveries WHERE text LIKE '%Recommended pick%' ORDER BY rowid DESC LIMIT 400")]
    assert rows
    for old in rows:
        lines = old.split('\n')
        at = lines.index('<b>Market breakdown</b>') + 2
        new = '\n'.join(lines[:at] + ['Sportsbook quote: x', 'Recovered Telegram cards 11-02 to 11-14: 1W–0L (1 settled) · model version not recorded']
                       + lines[at:])
        assert tg.card_identity(new) == tg.card_identity(old)
