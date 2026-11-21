"""Card colouring on the Telegram card (text + PNG): flag off = byte-identical card, flag on = marker, struck-through red,
colour key. The delivery key never moves (no resend). No network, no real bot: nothing is sent."""
import io
import sqlite3
from decimal import Decimal

import pytest
from PIL import Image

from app import card_threshold as ct, telegram as tg, telegram_card
from tests.test_card_block import card, pick

DAY = '2026-11-02'


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('NBA_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(telegram_card, '_logo', lambda *_: None)
    return tmp_path


def seed_lines(tmp_path, **lines):
    """A threshold store holding `lines` ({scope: T}) for DAY, written as the daily job would (state 'set')."""
    conn = ct.connect(tmp_path / 'card_thresholds.sqlite')
    with conn:
        for scope, t in lines.items():
            cls, sport = (scope.split(':')[1], scope.split(':')[0]) if ':' in scope else (scope, None)
            conn.execute("INSERT INTO card_thresholds (day, scope, class, sport, threshold, n, window, inputs_hash, rule_version, "
                         "state, generated_at) VALUES (?, ?, ?, ?, ?, 250, 400, 'h', 'card-threshold-v1', 'set', ?)",
                         (DAY, scope, cls, sport, t, DAY + 'T15:00:00Z'))
    conn.close()


def two_markets():
    winner = pick('nba', forecast_id=2, market='winner', market_title='Winner', pick_label='ORL win', probability=0.62,
                  percentage_history={'sport': 'nba', 'market': 'winner', 'model_version': 'm', 'source': 'primary_recorded',
                                      'percentage': '62.0', 'wins': 1, 'losses': 0, 'settled': 1, 'small_sample': True,
                                      'line_source': 'none'})
    spread = pick('nba')                                     # 57.1% on a bookmaker line
    return [winner, spread]


def test_flag_off_card_is_byte_identical_and_same_key(isolated, monkeypatch):
    seed_lines(isolated, W='55.0', L='60.0')
    monkeypatch.delenv('NBA_CARD_COLOURS', raising=False)
    off_key, off_text = tg.message_batches(card('nba', two_markets()), '123')[0][:2]
    assert '🟢' not in off_text and '🔴' not in off_text and 'Colour key' not in off_text and '<s>' not in off_text
    monkeypatch.setenv('NBA_CARD_COLOURS', '1')
    on_key, on_text = tg.message_batches(card('nba', two_markets()), '123')[0][:2]
    assert on_key == off_key                                  # same delivery key: a coloured card never resends
    assert on_text != off_text


def test_flag_on_colours_each_market_against_its_own_class(isolated, monkeypatch):
    seed_lines(isolated, W='55.0', L='60.0')
    monkeypatch.setenv('NBA_CARD_COLOURS', '1')
    text = tg.message_batches(card('nba', two_markets()), '123')[0][1]
    assert '<b>Model 62.0%</b> 🟢' in text                    # winner 62.0 >= W 55.0
    assert '<b><s>Model 57.1%</s></b> 🔴' in text             # spread 57.1 < L 60.0: struck through, still shown
    assert 'Colour key · Winner markets: Green = 55.0% and above · this class has beaten the price from here (n=250 priced, set Nov 2)' in text
    assert 'Colour key · Line markets: Green = 60.0% and above' in text
    png = telegram_card.render_card(text)                     # the preview renders; nothing is sent
    assert Image.open(io.BytesIO(png)).size[0] == 1080


def test_flag_on_without_any_threshold_colours_nothing(isolated, monkeypatch):
    monkeypatch.setenv('NBA_CARD_COLOURS', '1')
    text = tg.message_batches(card('nba', two_markets()), '123')[0][1]
    assert '🟢' not in text and '🔴' not in text
    assert 'Colour key · Winner markets: No threshold for this market yet' in text
    assert telegram_card.render_card(text).startswith(b'\x89PNG')


def test_model_line_unpriced_market_gets_no_colour(isolated, monkeypatch):
    seed_lines(isolated, W='55.0', L='50.0')
    monkeypatch.setenv('NBA_CARD_COLOURS', '1')
    tt = pick('nba', forecast_id=3, market='team_total_home', market_title='Home team points', pick_label='ORL over 110.5',
              probability=0.51, percentage_history={'sport': 'nba', 'market': 'team_total_home', 'model_version': 'm',
                                                    'source': 'primary_recorded', 'percentage': '51.0', 'wins': 0, 'losses': 0,
                                                    'settled': 0, 'small_sample': True, 'line_source': 'model'})
    text = tg.message_batches(card('nba', [tt]), '123')[0][1]
    assert '<b>Model 51.0%</b> ·' in text and '🟢' not in text and '🔴' not in text and 'Colour key' not in text


def test_the_png_draws_the_red_badge_struck_through(isolated, monkeypatch):
    seed_lines(isolated, W='55.0', L='60.0')
    monkeypatch.setenv('NBA_CARD_COLOURS', '1')
    text = tg.message_batches(card('nba', two_markets()), '123')[0][1]
    img = Image.open(io.BytesIO(telegram_card.render_card(text))).convert('RGB')
    colours = {img.getpixel((x, y)) for x in range(0, img.width, 3) for y in range(0, img.height, 3)}
    near = lambda c, ref: all(abs(a - b) < 12 for a, b in zip(c, ref))
    assert any(near(c, (0x16, 0x83, 0x2C)) for c in colours)          # green ink
    assert any(near(c, (0xC5, 0x1D, 0x35)) for c in colours)          # red ink (badge text and strike line)


def test_recommended_panel_repeats_the_same_colour(isolated, monkeypatch):
    seed_lines(isolated, W='55.0', L='60.0')
    monkeypatch.setenv('NBA_CARD_COLOURS', '1')
    picks = two_markets()
    picks[0].update(strong=True, game_rank=1, market_prob=0.55)       # the winner (62.0%, green) is the recommended pick
    picks[1].update(strong=False, game_rank=2)
    text = tg.message_batches(card('nba', picks), '123')[0][1]
    assert 'App 62.0% 🟢 · Sportsbook 55.0%' in text
    picks[0]['probability'] = 0.54                                     # below W 55.0: red, struck, in both places
    picks[0]['percentage_history']['percentage'] = '54.0'
    text = tg.message_batches(card('nba', picks), '123')[0][1]
    assert 'App <s>54.0%</s> 🔴 · Sportsbook 55.0%' in text and '<b><s>Model 54.0%</s></b> 🔴' in text
    assert telegram_card.render_card(text).startswith(b'\x89PNG')


def test_recommended_panel_unchanged_with_flag_off(isolated, monkeypatch):
    monkeypatch.delenv('NBA_CARD_COLOURS', raising=False)
    picks = two_markets()
    picks[0].update(strong=True, game_rank=1, market_prob=0.55)
    picks[1].update(strong=False, game_rank=2)
    text = tg.message_batches(card('nba', picks), '123')[0][1]
    assert 'App 62.0% · Sportsbook 55.0%' in text
