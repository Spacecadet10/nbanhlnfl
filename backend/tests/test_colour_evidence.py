"""Display-only evidence beside the colour (owner: 'Keep my existing colour rules. Show supporting W/L and sample size, and label
small samples clearly.'). The colour rule, MIN_LIVE_GRADED, the pick choice and every delivery key are unchanged; the card
gains: the recommended pick's own exact-% record in the panel (G1), a separate sample note beside the colour key (G3), and
past seasons worded like the live record (G4). No network, nothing is sent."""
import io

import pytest
from PIL import Image

from app import pick_snapshots as ps, telegram as tg, telegram_card, threshold_overview as to
from tests.test_card_block import card, pick


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('NBA_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(telegram_card, '_logo', lambda *_: None)


def winner(wins, losses, pushes=0, voids=0, prob=0.7508):
    p = pick('nba', market='winner', market_title='Winner', pick_label='ORL win', probability=prob, market_prob=0.73)
    p['percentage_history'].update(market='winner', percentage=f'{prob * 100:.1f}', wins=wins, losses=losses, settled=wins + losses,
                                   pushes=pushes, voids=voids, small_sample=wins + losses < 30)
    return p


def text_of(p, **kw):
    return tg.message_batches(card('nba', [p]), '123', **kw)[0][1]


def panel(text):
    """The lines between 'Recommended pick' and the closing divider of the panel."""
    lines = text.split('\n')
    start = lines.index('<b>Recommended pick</b>')
    return lines[start:lines.index('━━━━━━━━━━━━━━', start)]


def evidence_line(text):
    return next((x for x in panel(text) if x.startswith(tg.EVIDENCE_PREFIX)), None)


# --- G1: the recommended pick's own record in the panel -------------------------------------------------------------------
@pytest.mark.parametrize('wins,losses,pushes,want', [
    (8, 1, 1, 'Pick record · 75.1% 🟢 · this exact % has gone 8–1 (89%) · 1 push · small sample (9 settled)'),
    (0, 2, 0, 'Pick record · 75.1% 🔴 · this exact % has gone 0–2 (0%) · small sample (2 settled)'),
    (3, 2, 0, 'Pick record · 75.1% · this exact % has gone 3–2 (60%) · small sample (5 settled)'),
    (0, 0, 0, 'Pick record · 75.1% · no settled history at this exact %'),
    (0, 0, 2, 'Pick record · 75.1% · no settled history at this exact % · 2 pushes'),
    (29, 0, 0, 'Pick record · 75.1% 🟢 · this exact % has gone 29–0 (100%) · small sample (29 settled)'),
    (30, 0, 0, 'Pick record · 75.1% 🟢 · this exact % has gone 30–0 (100%) · 30 settled'),
    (40, 10, 1, 'Pick record · 75.1% 🟢 · this exact % has gone 40–10 (80%) · 1 push · 50 settled'),
    (10, 40, 0, 'Pick record · 75.1% 🔴 · this exact % has gone 10–40 (20%) · 50 settled'),
])
def test_recommended_panel_shows_colour_record_and_sample_size(wins, losses, pushes, want):
    text = text_of(winner(wins, losses, pushes), context_selection=True)
    assert evidence_line(text) == want
    lines = panel(text)
    assert lines[1] == '<b>ORL win</b>' and lines[2].startswith('App 75.1% / sportsbook 73.0%')     # pick line and reason untouched
    assert lines.index(want) == 3                                                               # the line right after the reason


def test_the_evidence_is_the_same_record_the_market_block_prints():
    text = text_of(winner(8, 1, 1), context_selection=True)
    assert '<b>Model 75.1%</b> 🟢 · this exact % has gone 8–1 (89%) · 1 push · small sample (9 settled)' in text
    block = next(x for x in text.split('\n') if x.startswith('<b>Model 75.1%</b>'))
    assert evidence_line(text).split(' · ', 2)[2] == block.split(' · ', 1)[1]          # the same words and counts, not a second computation


def test_preview_counts_include_the_game_result_exactly_as_the_block_does():
    p = winner(2, 0)
    p['grade'] = 'loss'
    text = tg.format_batch(card('nba', [p]), '123', p['game_id'], [p], preview=True, context_selection=True)[1]
    assert evidence_line(text) == 'Pick record · 75.1% · this exact % has gone 2–1 (67%) · small sample (3 settled)'
    assert '<b>Model 75.1%</b> · this exact % has gone 2–1 (67%)' in text


def test_legacy_strong_panel_gets_the_same_line_and_the_old_detail_is_unchanged():
    text = text_of(winner(8, 1))
    assert 'App 75.1% 🟢 · Sportsbook 73.0%' in panel(text)[2]
    assert evidence_line(text) == 'Pick record · 75.1% 🟢 · this exact % has gone 8–1 (89%) · small sample (9 settled)'


def test_no_supported_pick_has_no_evidence_line():
    p = winner(8, 1)
    p['percentage_history'].update(wins=1, losses=3, percentage='90.0')
    p['probability'] = 0.9               # app far above the sportsbook: a warning, no support -> 'No supported pick'
    p['market_prob'] = 0.5
    text = text_of(p, context_selection=True)
    assert '<b>No supported pick</b>' in text and tg.EVIDENCE_PREFIX not in text


def test_a_pick_without_exact_history_says_so():
    p = pick('soccer', market='btts', market_title='Both teams to score', pick_label='Yes', matchup='AAA vs BBB')
    del p['percentage_history']
    p['strong'] = True
    line = tg.pick_evidence_line(p)
    assert line == 'Pick record · exact-% history unavailable'


@pytest.mark.parametrize('wins,losses', [(8, 1), (0, 2), (3, 2), (0, 0), (40, 10)])
def test_parsers_read_the_new_card_exactly_like_the_old_one(wins, losses):
    text = text_of(winner(wins, losses), context_selection=True)
    old = '\n'.join(x for x in text.split('\n') if not x.startswith((tg.EVIDENCE_PREFIX, tg.SAMPLE_NOTE_PREFIX)))
    assert old != text and tg.card_identity(text) == tg.card_identity(old)
    assert tg.card_identity(text)[3] == 'ORL win'
    pub = dict(delivery_key='k', sport='nba', game_id='g1', market='winner', forecast_id='1', side='a', line=None, probability=0.7508,
               model_version='m', generated_at='2026-11-02T19:30:00Z', sent_at='2026-11-25T20:00:00Z', message_id=5, basis='live')
    at = '2026-11-25T20:00:00Z'
    got = [ps.card_evidence('nba', pub, {'delivery_key': 'k', 'text': t, 'status': 'sent', 'attempted_at': at, 'message_id': 5}, '2026-11-25T21:00:00Z')
           for t in (text, old)]
    assert got[0][1] is None and got[1][1] is None
    new, was = got[0][0], got[1][0]
    assert {k: v for k, v in new.items() if k != 'provenance'} == {k: v for k, v in was.items() if k != 'provenance'}
    assert (new['wins'], new['losses'], new['recommended']) == (wins, losses, 1)


def test_withdrawal_reads_the_reason_of_the_new_card_not_the_evidence():
    p = winner(8, 1)
    text = text_of(p, context_selection=True)
    lines = text.split('\n')
    assert lines[lines.index('<b>Recommended pick</b>') + 2].startswith('App 75.1%')      # the reason stays at +2


def test_delivery_key_ignores_the_evidence_and_the_note():
    keys = {tg.message_batches(card('nba', [winner(w, l)]), '123', context_selection=ctx)[0][0] for w, l in ((8, 1), (3, 2), (40, 10)) for ctx in (True,)}
    assert len(keys) == 1


# --- G3: the sample note beside the colour key ---------------------------------------------------------------------------
def test_colour_key_constant_is_untouched():
    assert tg.COLOUR_KEY == ("green = at least one win at this exact % and 75% or more of settled results won; red = more losses than wins; "
                             "no colour otherwise. Pushes and voids are not counted. Historical record at this exact %, not a profit guarantee.")


def test_sample_note_follows_the_key_only_when_a_colour_is_shown():
    text = text_of(winner(8, 1), context_selection=True)
    lines = text.split('\n')
    key = next(i for i, x in enumerate(lines) if x.startswith(tg.COLOUR_PREFIX))
    assert lines[key] == tg.COLOUR_PREFIX + tg.COLOUR_KEY
    assert lines[key + 1] == 'Sample note · Colours from fewer than 30 settled results are small samples.'
    assert tg.SAMPLE_NOTE_PREFIX not in text_of(winner(3, 2), context_selection=True)


def test_compact_card_keeps_the_marks_the_evidence_and_the_sample_note_but_not_the_key():
    p = winner(8, 1)
    text = tg.format_batch(card('nba', [p]), '123', p['game_id'], [p], compact=True, context_selection=True)[1]
    assert 'Colour key' not in text and '🟢' in text
    assert 'Sample note · Colours from fewer than 30 settled results are small samples.' in text
    assert evidence_line(text) is not None


# --- the picture draws every new line ---------------------------------------------------------------------------------------
def _ink_rows(png, ink, box, tol=14):
    img = Image.open(io.BytesIO(png)).convert('RGB')
    return {y for y in range(box[1], min(box[3], img.height)) for x in range(box[0], box[2], 2)
            if all(abs(a - b) < tol for a, b in zip(img.getpixel((x, y)), ink))}


def test_png_draws_the_evidence_line_in_the_panel_and_the_sample_note():
    with_line = text_of(winner(8, 1), context_selection=True)
    without = '\n'.join(x for x in with_line.split('\n') if not x.startswith((tg.EVIDENCE_PREFIX, tg.SAMPLE_NOTE_PREFIX)))
    a, b = (Image.open(io.BytesIO(telegram_card.render_card(t))) for t in (with_line, without))
    assert a.height > b.height                                                    # both new lines take room in the picture
    green_on_dark = (0x86, 0xEF, 0xAC)                                              # the panel's light green for the % token
    assert _ink_rows(telegram_card.render_card(with_line), green_on_dark, (80, 400, 1000, 760))
    red = text_of(winner(0, 2), context_selection=True)
    assert _ink_rows(telegram_card.render_card(red), (0xFD, 0xA4, 0xAF), (80, 400, 1000, 760))
    assert not _ink_rows(telegram_card.render_card(text_of(winner(3, 2), context_selection=True)), green_on_dark, (80, 400, 1000, 760))


def test_png_renders_a_long_evidence_line_and_a_wrapping_reason():
    p = winner(1234, 1199, pushes=12)
    p['pick_label'] = 'Orlando Magic to win in regulation or overtime'
    text = text_of(p, context_selection=True)
    assert telegram_card.render_card(text).startswith(b'\x89PNG')


# --- G4: past seasons worded like the live record ---------------------------------------------------------------------------
@pytest.mark.parametrize('line_source,prefix', [('none', 'Past seasons 2024-26, recomputed: '), ('model', 'Past seasons 2024-26, recomputed, model line: ')])
@pytest.mark.parametrize('wins,losses,tail', [(4, 0, '4–0 (100%) · small sample (4 settled)'), (1, 2, '1–2 (33%) · small sample (3 settled)'),
                                              (29, 0, '29–0 (100%) · small sample (29 settled)'), (30, 0, '30–0 (100%) · 30 settled'),
                                              (41, 37, '41–37 (53%) · 78 settled')])
def test_past_seasons_line_has_record_rate_and_sample_size(line_source, prefix, wins, losses, tail):
    h = {'line_source': line_source, 'wins': wins, 'losses': losses, 'settled': wins + losses}
    assert to.history_line(h) == prefix + tail
    assert to.history_line({**h, 'colour': 'green'}) == prefix + tail + ' · green record'
    assert 'Small sample.' not in to.history_line(h) and 'W–' not in to.history_line(h)
    assert to.history_line({'line_source': line_source, 'wins': 0, 'losses': 0, 'settled': 0}) is None


def test_evidence_follows_the_lineup_line_which_stays_right_under_the_pick():
    d = card('nba', [winner(8, 1)])
    text = tg.message_batches(d, '123', lineup_lines={'g1': 'Out: L. James'}, context_selection=True)[0][1]
    lines = panel(text)
    assert lines[3] == 'Lineup: Out: L. James' and lines[4].startswith(tg.EVIDENCE_PREFIX) and lines[5].startswith('<i>Context comparison')


def test_png_wraps_a_past_seasons_line_too_long_for_one_row_instead_of_cutting_it():
    """record + rate + small sample + colour word is wider than one row at 17 px: the picture wraps it, so nothing is cut."""
    def text(settled_wins, colour):
        p = winner(8, 1)
        p['percentage_history']['history'] = {'source': 'history_recomputed', 'line_source': 'model', 'wins': settled_wins, 'losses': 0,
                                              'settled': settled_wins, 'colour': colour}
        return text_of(p, context_selection=True)
    long_text, short_text = text(29, 'green'), text(41, 'neutral')
    past = 'Past seasons 2024-26, recomputed, model line: 29–0 (100%) · small sample (29 settled) · green record'
    assert past in long_text.split('\n')
    from PIL import ImageDraw
    draw = ImageDraw.Draw(Image.new('RGB', (10, 10)))
    assert telegram_card.fit_record(draw, past)[0] != past                            # one row would cut it ...
    assert all(draw.textlength(r, font=telegram_card._font(18)) <= 800 for r in telegram_card._wrap_rows(draw, past, 18, 800))
    tall, flat = (Image.open(io.BytesIO(telegram_card.render_card(t))) for t in (long_text, short_text))
    assert tall.height > flat.height                                                   # ... so it takes a second row
