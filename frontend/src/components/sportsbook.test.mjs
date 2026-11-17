// The app-versus-sportsbook line (run: npm run test:unit). A real -110/-110 quote is 50.0%, not a missing number.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { formatGap, gapPoints, sportsbookLine } from './sportsbook.ts';

const exact = { status: 'exact', app_probability: 0.769, book_probability: 0.5, gap_pp: null, provider: 'DraftKings',
  price: '-110/-110', line: 223.5, quote_id: 6312, retrieved_at: null, reason: null };

test('an even -110/-110 quote shows 50.0% and the gap from the shown numbers', () => {
  assert.equal(sportsbookLine(exact), 'App 76.9% · Sportsbook 50.0% no-vig (DraftKings -110/-110 at 223.5) · +26.9 pts');
});

test('the gap is signed and uses the one-decimal values on screen', () => {
  assert.equal(gapPoints(0.594, 0.4913), 10.3);
  assert.equal(gapPoints(0.40, 0.4567), -5.7);
  assert.equal(formatGap(-5.7), '−5.7 pts');
  assert.equal(formatGap(0), '±0.0 pts');
});

test('missing comparisons say why and never show a number', () => {
  for (const [status, text] of [['no_quote', /No sportsbook quote saved at this line/], ['model_line', /Model line/],
    ['unsupported', /No sportsbook quotes are collected/]]) {
    const s = sportsbookLine({ ...exact, status, book_probability: null });
    assert.match(s, text);
    assert.doesNotMatch(s, /%/);
  }
});
