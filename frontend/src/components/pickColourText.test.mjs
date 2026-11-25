import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { recordOf, returnsText, supportText, tallyText } from './pickColourText.ts';

const t = (o = {}) => ({ predictions: 9, games: 8, win: 3, loss: 1, push: 1, void: 1, half: 2, unsettled: 1, settled: 4, win_rate: 0.75, priced: 2, unpriced: 7, priced_settled: 2, profit_units: -0.5, roi: -0.25, ...o });

test('every outcome is named and only wins and losses are in the rate', () => {
  assert.equal(tallyText(t()), '3–1 (75.0%) · 1 push · 1 void · 2 half results · 1 unsettled');
  assert.equal(tallyText(t({ predictions: 0 })), 'None');
  assert.equal(tallyText(t({ win: 0, loss: 0, win_rate: null, push: 0, void: 0, half: 0, unsettled: 2 })), '0–0 · 2 unsettled');
});

test('returns always state how many picks had a price', () => {
  assert.equal(returnsText(t()), '−0.50u on 2 (−25.0%) · 2 of 9 priced');
  assert.equal(returnsText(t({ profit_units: null, roi: null, priced: 0, priced_settled: 0 })), '0 of 9 priced · no settled priced pick');
});

test('the saved support shows both records apart, and a missing one is never 0–0', () => {
  const s = { wins: 3, losses: 1, pushes: 1, voids: 0, recovered_wins: null, recovered_losses: null, recovered_reason: 'no_rows_for_sport' };
  assert.equal(supportText({ support: s }), 'Live record then: 3–1 · 1 push · recovered record then: none (no rows for sport)');
  assert.equal(supportText({ support: null, unknown_reason: 'before_colour_rule' }), 'Colour unknown: before this colour rule went live');
  assert.equal(recordOf(null, null), 'no record');
});

test('all three performance pages show the saved colours, with its filters in the URL', () => {
  const read = (p) => readFileSync(join(import.meta.dirname, '..', p), 'utf8');
  for (const p of ['pages/PerformancePage.tsx', 'pages/NflPages.tsx', 'pages/SoccerPages.tsx']) assert.match(read(p), /<PickColours /);
  const c = read('components/PickColours.tsx');
  for (const k of ['pc_sport', 'pc_view', 'pc_record', 'pc_market', 'pc_pct', 'pc_colour', 'date_from', 'date_to']) assert.match(c, new RegExp(k));
});
