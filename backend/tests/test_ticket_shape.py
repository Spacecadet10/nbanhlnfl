"""Ticket shape: the weakest leg decides a long ticket (owner, Nov 11: his own tickets kept losing by exactly one leg)."""
import copy

import pytest

from app import tickets as t


def leg(label, probability, price=None, sport='nba', market='winner', game=None):
    return dict(sport=sport, market=market, matchup='AAA @ BBB', pick_label=label, probability=probability,
                game_id=game or label, tipoff='2026-11-12T00:00:00Z',
                quote_check=dict(provider='DraftKings', price_now=price, line_source='bookmaker') if price is not None else {})


def test_weakest_leg_is_the_lowest_percentage_and_the_chance_is_in_plain_words():
    legs = [leg('DET win', .878, -500), leg('DAL win', .672, -192), leg('MIN win', .869, -700)]
    s = t.ticket_shape(legs)
    assert s['weakest'] == 'DAL win' and s['weakest_line'] == 'Weakest leg: DAL win (67%)' and not s['price_asks_a_lot']
    assert abs(s['all_land'] - .878 * .672 * .869) < 1e-9 and s['all_land_line'] == 'All legs land about 1 time in 2'
    assert t.land_words(.33) == 'about 1 time in 3' and t.land_words(.21) == 'about 1 time in 5'
    assert t.land_words(.64) == 'about 6 times in 10' and t.land_words(.99) == 'about 9 times in 10'
    assert t.land_words(.55) == 'about 6 times in 10' and t.land_words(.54) == 'about 1 time in 2' and t.land_words(.72) == 'about 7 times in 10'
    assert t.land_words(.0) == 'about 1 time in 1000'


def test_a_leg_whose_price_asks_a_lot_counts_as_the_weakest_even_when_another_percentage_is_lower():
    # Nov 9, CLE @ CHA: the price needed 74%, the app said 55%; that leg lost the owner's five-leg ticket.
    cle, low = leg('CLE win', .552, -290), leg('LAL win', .506, 121)
    s = t.ticket_shape([leg('DET win', .878, -500), cle, low])
    assert s['weakest'] == 'CLE win' and s['weakest_line'] == 'Weakest leg: CLE win (55%) · price asks a lot'
    assert s['price_asks_a_lot'] == ['CLE win'] and s['lowest_probability'] == .506
    assert t.leg_assessment('nba', cle)['price_asks_a_lot'] is True
    assert t.leg_assessment('nba', low)['price_asks_a_lot'] is False                 # the price asks for less than the app says
    assert t.leg_assessment('nba', leg('X win', .60))['price_asks_a_lot'] is False   # no price: nothing to compare
    exactly = leg('Y win', .60, -300)                                                # needs 75%: exactly 15 points more
    assert t.leg_assessment('nba', exactly)['price_asks_a_lot'] is True


@pytest.mark.parametrize('count,probability,allowed', [
    (2, .51, True), (3, .51, True), (4, .75, True), (4, .749, False), (5, .80, True), (5, .79, False),
    (6, .80, True), (7, .80, True), (7, .78, False)])
def test_ticket_length_must_match_its_weakest_leg(count, probability, allowed):
    legs = [leg(f'T{i} win', .90) for i in range(count - 1)] + [leg('Weak win', probability)]
    problems = t.shape_problems(legs)
    assert (problems == []) is allowed
    if not allowed:
        assert 'Weak win' in problems[0] and f'{count}-leg ticket' in problems[0] and 'two or three legs' in problems[0]


def test_at_most_one_nhl_plus_one_and_a_half_leg_per_ticket():
    dog = lambda name: leg(f'{name} +1.5', .71, -260, sport='nhl', market='spread')
    assert t.shape_problems([dog('CBJ'), leg('DET win', .88)]) == []
    two = t.shape_problems([dog('CBJ'), dog('OTT'), leg('DET win', .88)])
    assert len(two) == 1 and 'CBJ +1.5, OTT +1.5' in two[0]
    favourite = leg('WPG -1.5', .40, 180, sport='nhl', market='spread')
    nba = leg('NO +11.5', .60, -110, sport='nba', market='spread')
    assert t.shape_problems([favourite, dict(favourite, pick_label='TB -1.5'), nba]) == []


def test_the_picture_shows_both_shape_lines_and_never_fails_because_of_them(monkeypatch):
    from app import telegram_card as card
    monkeypatch.setattr(card, '_logo', lambda *a: None)
    seen = []
    real = card.ImageDraw.ImageDraw.text
    monkeypatch.setattr(card.ImageDraw.ImageDraw, 'text', lambda self, xy, text, **kw: (seen.append(text), real(self, xy, text, **kw))[1])
    legs = [leg('DET win', .878, -500), leg('CLE win', .552, -290)]
    png = card.render_ticket_card('2026-11-11', [dict(label='Pair', ticket_id='T1', legs=legs)])
    drawn = ' '.join(seen)
    assert png[:4] == b'\x89PNG' and 'All legs land about 1 time in 2' in drawn
    assert 'Weakest leg: CLE win (55%)' in drawn and 'price asks a lot' in drawn
    # A picture too tall with the extra lines is drawn without them, exactly as before this change.
    long = ' '.join(['Pelicans'] * 8)

    def tall(wrapped):      # 21 legs; the first `wrapped` carry a pick name long enough to wrap (small height steps)
        return [dict(label='Layout', ticket_id=f'T{k}', legs=[leg((long if k * 7 + i < wrapped else 'NO') + f' {k}{i}', .85) for i in range(7)])
                for k in range(3)]

    def drawn_or_none(tickets, shape_lines):
        try:
            return card._ticket_card('2026-11-11', tickets, preview=False, number_from=1, total=None, book_lines=False, shape_lines=shape_lines)
        except ValueError:
            return None
    for wrapped in range(22):
        tickets = tall(wrapped)
        with_lines, without = drawn_or_none(tickets, True), drawn_or_none(tickets, False)
        if with_lines is None and without is not None:
            assert card.render_ticket_card('2026-11-11', tickets) == without
            return
    raise AssertionError('no layout found where only the shape lines overflow')


def test_check_refuses_a_long_ticket_with_a_weak_leg_and_accepts_it_short(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    monkeypatch.setenv('NBA_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(t, 'SKILL', tmp_path / 'SKILL.md'); t.SKILL.write_text('fixture')
    now = '2026-11-08T17:00:00Z'
    picks = [dict(forecast_id=i, game_id=f'g{i}', market='winner', matchup=f'A{i} @ B{i}', pick_label=f'B{i} win',
                  probability=.90 if i < 5 else .70, status='scheduled', grade='pending',
                  generated_at='2026-11-08T16:30:00Z', tipoff='2026-11-08T18:00:00Z', lineup_flags=[]) for i in range(1, 6)]
    snap = dict(as_of=now, date='2026-11-08', skill_sha256=t.sha(t.SKILL.read_bytes()), helper_sha256=t.sha(Path(t.__file__).read_bytes()),
                sports={s: dict(freshness=dict(source_state='fresh', failing=False), report=dict(picks=copy.deepcopy(picks), without_forecast={}))
                        for s in ('nba', 'nhl', 'nfl')})
    snap['sports']['soccer'] = {'error': 'forecasts_unavailable'}
    monkeypatch.setattr(t, 'snapshot', lambda: copy.deepcopy(snap))
    path = tmp_path / 'snapshot.json'; path.write_text(json.dumps(snap))
    review = dict(skill_sha256=snap['skill_sha256'], helper_sha256=snap['helper_sha256'], snapshot_sha256=t.sha(path.read_bytes()),
                  notes='Fixture review', combination_comparison='Compared',
                  markets_reviewed={s: {'winner': 'Reviewed'} for s in ('nba', 'nhl', 'nfl')},
                  forecasts_reviewed={f'{s}:{i}': dict(decision='candidate', reason='Compared market, side and evidence')
                                      for s in ('nba', 'nhl', 'nfl') for i in range(1, 6)})
    def plan(ids):
        return dict(snapshot_path=str(path), review=review, tickets=[dict(
            label='Shape', stake_usd=1, payout_assessment='Fixture payout judgment',
            legs=[dict(sport='nba', forecast_id=i, rationale='Fixture reason') for i in ids])])
    with pytest.raises(ValueError, match=r'Ticket shape \(Shape\): a 5-leg ticket needs every leg at 80% or better; too weak: B5 win \(70%\)'):
        t.check(plan([1, 2, 3, 4, 5]))
    ok = t.check(plan([1, 2, 5]))
    assert ok['resolved'][0]['shape']['weakest'] == 'B5 win' and ok['resolved'][0]['shape']['legs'] == 3
    assert t.check(plan([1, 2, 3, 4]))['resolved'][0]['shape']['weakest_probability'] == .90
