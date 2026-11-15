// Soccer market wording (run: npm run test:unit). Plain JS so tsc ignores it; Node strips the types of the module.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { lineKind, outcomeText, returnText, selectionLabel, sourceText } from './soccerMarkets.ts';

test('every market names its side, line and team plainly', () => {
  assert.equal(selectionLabel('goals_total', 'over', 2.5, 'Arsenal', 'Chelsea'), 'Over 2.5 goals');
  assert.equal(selectionLabel('goals_home', 'under', 1.5, 'Arsenal', 'Chelsea'), 'Arsenal under 1.5 goals');
  assert.equal(selectionLabel('corners_away', 'over', 4, 'Arsenal', 'Chelsea'), 'Chelsea over 4 corners');
  assert.equal(selectionLabel('corners_handicap', 'home', -1.25, 'Arsenal', 'Chelsea'), 'Arsenal -1.25 corners');
  assert.equal(selectionLabel('corners_handicap', 'away', 1.25, 'Arsenal', 'Chelsea'), 'Chelsea +1.25 corners');
  assert.equal(selectionLabel('btts', 'no', null, 'Arsenal', 'Chelsea'), 'Both teams to score: no');
  assert.equal(selectionLabel('double_chance', 'draw_away', null, 'Arsenal', 'Chelsea'), 'draw or Chelsea');
  assert.equal(selectionLabel('draw_no_bet', 'home', null, 'Arsenal', 'Chelsea'), 'Arsenal (draw no bet)');
});

test('line kinds and split outcomes are shown, never hidden in one number', () => {
  assert.deepEqual([2.5, 3, 2.25, 2.75, -0.75, null].map(lineKind), ['half', 'whole', 'quarter', 'quarter', 'quarter', 'none']);
  assert.equal(outcomeText({ win: 0.4, half_win: 0, push: 0.3, half_loss: 0, loss: 0.3 }), '40.0% win · 30.0% push');
  assert.equal(outcomeText({ win: 0.4, half_win: 0.1, push: 0, half_loss: 0.2, loss: 0.3 }), '40.0% win · 10.0% half win · 20.0% half loss');
});

test('a bookmaker offer and a model line are labelled differently', () => {
  assert.equal(sourceText({ source: 'bookmaker', provider: 'DraftKings', american: -150, decimal: 1.6667 }), 'DraftKings -150 (1.67)');
  assert.equal(sourceText({ source: 'model' }), 'Model line');
  assert.equal(returnText(1.034), '+3.4%');
  assert.equal(returnText(0.95), '−5.0%');
  assert.equal(returnText(undefined), '—');
});
