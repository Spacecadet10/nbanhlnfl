from app import additional_odds as odds


def quote(monkeypatch, market, outcomes, *, home='Home', away='Away'):
    now = '2026-11-15T23:00:00Z'
    kickoff = '2026-11-16T00:00:00Z'
    snapshot = {
        'kickoff': kickoff, 'retrieved_at': now,
        'event': {'home_team': home, 'away_team': away, 'bookmakers': [{
            'title': 'Example book', 'markets': [{'key': market, 'last_update': now, 'outcomes': outcomes}]
        }]},
    }
    monkeypatch.setattr(odds, '_read', lambda *_: snapshot)
    return now, kickoff


def test_team_totals_require_exact_team_and_line(monkeypatch):
    now, kickoff = quote(monkeypatch, 'team_totals', [
        {'name': 'Over', 'description': 'Home', 'point': 23.5, 'price': 2},
        {'name': 'Under', 'description': 'Home', 'point': 23.5, 'price': 2},
        {'name': 'Under', 'description': 'Away', 'point': 21.5, 'price': 1.8},
    ])
    pick = {'game_id': 'game', 'market': 'team_total_home', 'status': 'scheduled', 'tipoff': kickoff}
    assert odds.comparison('nfl', pick, now, side='over', line=23.5)['card_market_prob'] == .5
    assert odds.comparison('nfl', pick, now, side='over', line=21.5) == {}
    assert odds.comparison('nfl', {**pick, 'market': 'team_total_away'}, now, side='under', line=21.5) == {}
    assert odds.comparison('nfl', pick, kickoff, side='over', line=23.5) == {}


def test_btts_uses_complete_yes_no_market(monkeypatch):
    now, kickoff = quote(monkeypatch, 'btts', [{'name': 'Yes', 'price': 2}, {'name': 'No', 'price': 2}])
    pick = {'game_id': 'match', 'market': 'btts', 'status': 'scheduled', 'tipoff': kickoff}
    assert odds.comparison('soccer', pick, now, side='yes', line=None)['card_market_prob'] == .5


def test_corner_handicap_requires_opposite_signed_line(monkeypatch):
    now, kickoff = quote(monkeypatch, 'alternate_spreads_corners', [
        {'name': 'Home', 'point': -1.5, 'price': 2}, {'name': 'Away', 'point': 1.5, 'price': 2},
    ])
    pick = {'game_id': 'match', 'market': 'corners_handicap', 'status': 'scheduled', 'tipoff': kickoff,
            'outcome_probabilities': {'win': .6, 'loss': .4, 'half_win': 0, 'half_loss': 0, 'push': 0}}
    assert odds.comparison('soccer', pick, now, side='away', line=1.5)['card_market_prob'] == .5
    assert odds.comparison('soccer', pick, now, side='away', line=-1.5) == {}
