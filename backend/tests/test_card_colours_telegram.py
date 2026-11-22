"""Card colouring on the Telegram card (text + PNG): each exact-% bucket is coloured by its OWN settled record
(threshold_overview.bucket_colour), never by a probability cutoff. Colour never removes a market or a recommendation; the
delivery key never moves. No network, no real bot: nothing is sent."""
import io

import pytest
from PIL import Image

from app import telegram as tg, telegram_card
from tests.test_card_block import card, pick


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('NBA_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(telegram_card, '_logo', lambda *_: None)
    return tmp_path


def with_record(p, wins, losses, pushes=0, voids=0):
    h = p['percentage_history']
    h.update(wins=wins, losses=losses, settled=wins + losses, pushes=pushes, voids=voids, small_sample=wins + losses < 30)
    return p


def text_for(wins, losses, **kw):
    return tg.message_batches(card('nba', [with_record(pick('nba'), wins, losses, **kw)]), '123')[0][1]


@pytest.mark.parametrize('wins,losses,mark', [(1, 0, '🟢'), (2, 0, '🟢'), (8, 1, '🟢'), (3, 1, '🟢'), (3, 2, None), (1, 1, None),
                                              (2, 1, None), (0, 0, None), (0, 2, 'red'), (1, 2, 'red')])
def test_block_colour_is_the_buckets_own_record(wins, losses, mark):
    text = text_for(wins, losses)
    if mark == '🟢':
        assert '<b>Model 57.1%</b> 🟢 · ' in text and '🔴' not in text.split('Colour key')[0]
    elif mark == 'red':
        assert '<b>Model 57.1%</b> 🔴 · ' in text and '🟢' not in text.split('Colour key')[0]
    else:
        assert '<b>Model 57.1%</b> · ' in text and '🟢' not in text and '🔴' not in text.split('Colour key')[0]


def test_record_line_wording_is_the_shared_one_with_pushes_voids_and_small_sample():
    text = text_for(8, 1, pushes=1)
    assert '<b>Model 57.1%</b> 🟢 · this exact % has gone 8–1 (89%) · 1 push · small sample (9 settled)' in text
    assert '<b>Model 57.1%</b> · no settled history at this exact %' in text_for(0, 0)
    assert '· 2 voids' in text_for(0, 0, voids=2) and '· 1 void' in text_for(3, 2, voids=1)
    big = text_for(40, 10)
    assert 'has gone 40–10 (80%)' in big and 'small sample' not in big.split('Colour key')[0]


def test_colour_is_the_same_at_every_probability_and_never_a_cutoff():
    low, high = pick('nba', probability=0.521), pick('nba', probability=0.931)
    low['percentage_history'].update(percentage='52.1')
    high['percentage_history'].update(percentage='93.1')
    with_record(low, 2, 0)
    with_record(high, 0, 2)
    text = tg.message_batches(card('nba', [low]), '123')[0][1]
    assert '<b>Model 52.1%</b> 🟢' in text                                 # a 52% bucket that has won is green
    text = tg.message_batches(card('nba', [high]), '123')[0][1]
    assert '<b>Model 93.1%</b> 🔴' in text                          # a 93% bucket that has lost is red


def test_legend_says_historical_record_not_profit_guarantee_and_never_claims_profit():
    text = text_for(2, 0)
    key = next(line for line in text.split('\n') if line.startswith('Colour key · '))
    assert 'at least one win' in key and '75%' in key and 'not a profit guarantee' in key
    assert 'proven' not in text.lower() and 'profitable' not in text.lower()
    assert 'Colour key' not in tg.message_batches(card('nba', [with_record(pick('nba'), 3, 2)]), '123')[0][1]   # nothing coloured


def test_recovered_and_recomputed_lines_have_their_own_colour_and_never_the_headline():
    p = with_record(pick('nba'), 3, 2)                                      # live 3-2: neutral
    p['percentage_history'].update(
        telegram={'source': 'telegram_cards', 'label': 'Recovered Telegram cards', 'period': {'from': '2026-11-02', 'to': '2026-11-14'},
                  'model_version_known': False, 'line_source': 'bookmaker', 'wins': 0, 'losses': 2, 'pushes': 0, 'voids': 1,
                  'colour': 'red', 'settled': 2, 'small_sample': True},
        history={'source': 'history_recomputed', 'line_source': 'bookmaker', 'wins': 4, 'losses': 0, 'pushes': 0, 'voids': 0,
                 'colour': 'green', 'settled': 4, 'small_sample': True})
    text = tg.message_batches(card('nba', [p]), '123')[0][1]
    assert '<b>Model 57.1%</b> · this exact % has gone 3–2' in text         # headline: live record only, neutral
    assert 'Recovered Telegram cards 11-02 to 11-14: this exact % has gone 0–2 (0%) · 1 void · small sample (2 settled) · red record · model version not recorded' in text
    assert 'Past seasons 2024-26, recomputed, model line: 4W–0L · Small sample. · green record' in text


def test_colour_never_removes_a_market_or_the_recommendation():
    losing = with_record(pick('nba'), 0, 3)                                 # red bucket, and the only (strong) pick
    text = tg.message_batches(card('nba', [losing]), '123')[0][1]
    assert '<b>Recommended pick</b>\n<b>ORL -8.5</b>' in text and '<b>Model 57.1%</b> 🔴' in text
    ctx = tg.message_batches(card('nba', [losing]), '123', context_selection=True)[0][1]
    assert '<b>Point spread</b>' in ctx


def test_delivery_key_ignores_colour_only_changes():
    a = tg.message_batches(card('nba', [with_record(pick('nba'), 3, 2)]), '123')[0][0]
    b = tg.message_batches(card('nba', [with_record(pick('nba'), 8, 2)]), '123')[0][0]
    assert a == b


def test_png_draws_green_and_red_badges_from_the_same_marks():
    for wins, losses, ink in ((2, 0, (0x16, 0x83, 0x2C)), (0, 2, (0xC5, 0x1D, 0x35))):
        img = Image.open(io.BytesIO(telegram_card.render_card(text_for(wins, losses)))).convert('RGB')
        colours = {img.getpixel((x, y)) for x in range(0, img.width, 3) for y in range(0, img.height, 3)}
        assert any(all(abs(a - b) < 12 for a, b in zip(c, ink)) for c in colours)
    neutral = Image.open(io.BytesIO(telegram_card.render_card(text_for(3, 2)))).convert('RGB')
    cs = {neutral.getpixel((x, y)) for x in range(0, neutral.width, 3) for y in range(0, neutral.height, 3)}
    assert not any(all(abs(a - b) < 6 for a, b in zip(c, (0xC5, 0x1D, 0x35))) for c in cs)


def test_recommended_panel_repeats_the_same_colour():
    p = with_record(pick('nba', market_prob=0.55), 2, 0)
    text = tg.message_batches(card('nba', [p]), '123')[0][1]
    assert 'App 57.1% 🟢 · Sportsbook 55.0%' in text
    with_record(p, 0, 2)
    text = tg.message_batches(card('nba', [p]), '123')[0][1]
    assert 'App 57.1% 🔴 · Sportsbook 55.0%' in text and '<b>Model 57.1%</b> 🔴' in text
    assert telegram_card.render_card(text).startswith(b'\x89PNG')


def test_withdrawal_parser_reads_red_and_green_blocks():
    for model in ('<b>Model 57.1%</b> 🔴 · 0W–0L (0 settled)', '<b>Model 62.0%</b> 🟢 · 1W–0L (1 settled)',
                  '<b>Model 62.0%</b> · 1W–0L (1 settled)'):
        text = '\n'.join(['<b>Point spread</b>', 'ORL -3.5 · bookmaker line', model])
        assert tg._blocks(text) and tg._blocks(text)[0][0] == 'Point spread' and tg._blocks(text)[0][2] in ('57.1', '62.0')


def test_compact_card_drops_the_legend_but_keeps_the_marks():
    p = with_record(pick('nba'), 2, 0)
    text = tg.format_batch(card('nba', [p]), '123', p['game_id'], [p], compact=True)[1]
    assert 'Colour key' not in text and '🟢' in text


def test_neutral_pill_is_grey_whatever_the_team_colour():
    """A red team (CHI, CGY, TOR...) must not make an uncoloured % pill look red: neutral is always the grey pill."""
    def render(league, matchup):
        p = with_record(pick(league.lower(), matchup=matchup), 3, 2)           # 3-2 = neutral
        text = tg.message_batches(card(league.lower(), [p]), '123')[0][1]
        return Image.open(io.BytesIO(telegram_card.render_card(text))).convert('RGB')
    fill, ink = (tuple(int(telegram_card.NEUTRAL_PILL[0][i:i + 2], 16) for i in (1, 3, 5)),
                 tuple(int(telegram_card.NEUTRAL_PILL[1][i:i + 2], 16) for i in (1, 3, 5)))
    for league, matchup in (('NHL', 'CGY @ TOR'), ('NHL', 'BOS @ ORL'), ('NHL', 'CHI @ TOR'), ('NBA', 'CHI @ MIA')):
        img = render(league, matchup)
        found = [(x, y) for y in range(300, img.height - 30, 2) for x in (851, 1000) if img.getpixel((x, y)) == fill]
        assert found, (matchup, 'no grey neutral pill')
        x, y = found[0]
        row = [img.getpixel((xx, y + dy)) for xx in range(860, 990, 3) for dy in range(0, 24, 2) if y + dy < img.height]
        assert any(all(abs(a - b) < 40 for a, b in zip(c, ink)) for c in row)           # the % digits are INK, not the team colour
        red = (0xC5, 0x1D, 0x35)
        assert not any(all(abs(a - b) < 12 for a, b in zip(img.getpixel((xx, y + 10)), red)) for xx in range(860, 990, 3))


def test_red_is_a_mark_and_a_pill_never_a_strike_through_and_small_sample_prints_once():
    text = text_for(0, 2)
    assert '<s>' not in text and '<b>Model 57.1%</b> 🔴' in text
    block = text.split('<b>Point spread</b>')[1].split('\n\n')[0]
    assert block.count('mall sample') == 1                                       # record_line's own suffix; no second note
    assert 'Small sample.' not in block and 'No settled matching examples.' not in text_for(0, 0)
    old = '\n'.join(['<b>Point spread</b>', 'ORL -3.5 · bookmaker line', '<b><s>Model 57.1%</s></b> 🔴 · 0W–2L (2 settled)'])
    assert tg._blocks(old)[0][2] == '57.1'                                         # an already-sent red message still parses
