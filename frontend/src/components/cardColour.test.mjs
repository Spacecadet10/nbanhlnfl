// Bucket-record colouring (run: npm run test:unit). Each exact-% bucket is coloured by its OWN settled record, never by a
// probability cutoff: green = >=1 win and win rate >= 75%; red = losses > wins; neutral otherwise. Pushes/voids are not in the
// denominator. The headline text mirrors the backend record_line; recovered/recomputed lines are the backend's own `line` strings.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { bucketColour, bucketColourOf, bucketHeadline, bucketSubLine, historyLine, telegramRecoveredLine } from './cardRecord.ts';

test('the rule on the owner examples', () => {
  for (const [w, l] of [[1, 0], [2, 0], [8, 1], [3, 1], [75, 25]]) assert.equal(bucketColour(w, l), 'green', `${w}-${l}`);
  for (const [w, l] of [[3, 2], [1, 1], [2, 1], [0, 0], [74, 26], [5, 5]]) assert.equal(bucketColour(w, l), 'neutral', `${w}-${l}`);
  for (const [w, l] of [[0, 2], [0, 1], [2, 3], [1, 5]]) assert.equal(bucketColour(w, l), 'red', `${w}-${l}`);
});

test('backend colour wins when present and valid; the TS rule agrees on the examples and is the fallback', () => {
  const ex = [[1, 0, 'green'], [2, 0, 'green'], [8, 1, 'green'], [3, 2, 'neutral'], [0, 2, 'red'], [0, 0, 'neutral']];
  for (const [wins, losses, colour] of ex) {
    assert.equal(bucketColourOf({ wins, losses, colour }), colour);
    assert.equal(bucketColourOf({ wins, losses }), colour);
  }
  assert.equal(bucketColourOf({ wins: 3, losses: 2, colour: 'green' }), 'green');   // the backend decision is used as sent
  assert.equal(bucketColourOf({ wins: 3, losses: 2, colour: 'bogus' }), 'neutral'); // unknown value -> TS rule
  assert.equal(bucketColourOf(null), 'neutral');
});

test('pushes and voids are shown but never in the denominator or the colour', () => {
  const r = { wins: 8, losses: 1, pushes: 1, voids: 0 };
  assert.equal(bucketHeadline('57.3', r), '57.3% · this exact % has gone 8–1 (89%) · 1 push · small sample (9 settled)');
  assert.equal(bucketHeadline('57.3', { wins: 8, losses: 1, pushes: 2, voids: 1 }),
    '57.3% · this exact % has gone 8–1 (89%) · 2 pushes · 1 void · small sample (9 settled)');
  assert.equal(bucketColour(1, 1), 'neutral');
  assert.equal(bucketColourOf({ wins: 3, losses: 1, pushes: 50, voids: 50 }), 'green');
});

test('small-sample suffix only under 30 settled; the rate rounds like the card', () => {
  assert.equal(bucketHeadline('60.1', { wins: 24, losses: 6 }), '60.1% · this exact % has gone 24–6 (80%)');
  assert.equal(bucketHeadline('60.1', { wins: 23, losses: 6 }), '60.1% · this exact % has gone 23–6 (79%) · small sample (29 settled)');
  assert.equal(bucketHeadline('60.1', { wins: 1, losses: 2, small_sample: true }), '60.1% · this exact % has gone 1–2 (33%) · small sample (3 settled)');
});

test('no settled history says so, and still lists pushes', () => {
  assert.equal(bucketHeadline('57.3', { wins: 0, losses: 0 }), '57.3% · no settled history at this exact %');
  assert.equal(bucketHeadline('57.3', { wins: 0, losses: 0, pushes: 1 }), '57.3% · no settled history at this exact % · 1 push');
});

test('57.3% and 57.4% stay distinct buckets in the printed text', () => {
  const a = (0.573 * 100).toFixed(1), b = (0.574 * 100).toFixed(1);
  assert.notEqual(bucketHeadline(a, { wins: 1, losses: 0 }), bucketHeadline(b, { wins: 1, losses: 0 }));
  assert.match(bucketHeadline(a, { wins: 1, losses: 0 }), /^57\.3% /);
  assert.match(bucketHeadline(b, { wins: 1, losses: 0 }), /^57\.4% /);
});

test('recovered and recomputed records are separate labelled lines with their own colour, never in the headline', () => {
  const tg = { wins: 2, losses: 0, pushes: 0, voids: 0, colour: 'green', label: 'Recovered Telegram cards', model_version_known: false };
  assert.equal(bucketSubLine('Recovered Telegram cards (model version unknown)', tg),
    'Recovered Telegram cards (model version unknown) · 2–0 (100%) · small sample (2 settled)');
  assert.equal(bucketSubLine('Recomputed past seasons', { wins: 0, losses: 0 }), 'Recomputed past seasons · no settled history at this exact %');
  assert.equal(bucketSubLine('Recomputed past seasons', { wins: 1, losses: 3, pushes: 1 }), 'Recomputed past seasons · 1–3 (25%) · 1 push · small sample (4 settled)');
  const live = { wins: 0, losses: 0 };
  assert.equal(bucketHeadline('57.3', live), '57.3% · no settled history at this exact %');   // recovered 2–0 does not leak in
  assert.equal(bucketColourOf(tg), 'green');
  assert.equal(bucketColourOf(live), 'neutral');
});

test('recovered Telegram fallback (older API, no line): Telegram formats, own colour, overlap note, no repeated %', () => {
  const t = { available: true, period: { from: '2026-11-02', to: '2026-11-16' }, wins: 2, losses: 0, pushes: 1, voids: 0, colour: 'green', overlap_live: 1 };
  assert.deepEqual(telegramRecoveredLine(t), { colour: 'green',
    text: 'Recovered Telegram cards 11-02 to 11-16 (model version not recorded) · 2–0 (100%) · 1 push · small sample (2 settled) · green record · 1 game also in the live record, not added' });
  assert.equal(telegramRecoveredLine({ ...t, overlap_live: 3, colour: 'neutral' }).text,
    'Recovered Telegram cards 11-02 to 11-16 (model version not recorded) · 2–0 (100%) · 1 push · small sample (2 settled) · 3 games also in the live record, not added');
  assert.equal(telegramRecoveredLine({ ...t, wins: 1, losses: 3, colour: undefined, overlap_live: 0 }).colour, 'red');
  assert.match(telegramRecoveredLine({ ...t, wins: 1, losses: 3, colour: undefined, overlap_live: 0 }).text, / · red record$/);
});

test('recovered fallback: unavailable and empty are never 0–0 and never fall back to the live record; counts may be null', () => {
  assert.deepEqual(telegramRecoveredLine(null), { colour: 'neutral', text: 'Recovered Telegram cards (model version not recorded) · recovered history unavailable' });
  assert.match(telegramRecoveredLine({ available: false, wins: null, losses: null, pushes: null, voids: null, settled: null }).text, /recovered history unavailable$/);
  const empty = telegramRecoveredLine({ available: true, period: { from: '2026-11-02', to: '2026-11-16' }, wins: 0, losses: 0, pushes: 0 });
  assert.deepEqual(empty, { colour: 'neutral', text: 'Recovered Telegram cards 11-02 to 11-16 (model version not recorded) · no recovered history at this exact %' });
  assert.doesNotMatch(empty.text, /0–0/);
});

test('backend-provided line strings are rendered verbatim, tinted by the record own colour; null line hides history', () => {
  const cases = [
    ['Recovered Telegram cards 11-02 to 11-16 (model version not recorded) · 8–1 (89%) · small sample (9 settled) · green record', 'green'],
    ['Recovered Telegram cards 11-02 to 11-16 (model version not recorded) · 3–2 (60%) · small sample (5 settled)', 'neutral'],
    ['Recovered Telegram cards 11-02 to 11-16 (model version not recorded) · 0–2 (0%) · small sample (2 settled) · red record', 'red'],
    ['Recovered Telegram cards: recovered history unavailable (file missing)', 'neutral'],
    ['No recovered Telegram history for NCAAB (recording began 2026-11-17)', 'neutral'],
  ];
  for (const [line, colour] of cases) {
    assert.deepEqual(telegramRecoveredLine({ line, colour, available: true, wins: 1, losses: 0 }), { text: line, colour });
  }
  assert.deepEqual(historyLine({ line: 'Recomputed past seasons · 4–1 (80%) · small sample (5 settled) · green record', colour: 'green', wins: 4, losses: 1 }),
    { text: 'Recomputed past seasons · 4–1 (80%) · small sample (5 settled) · green record', colour: 'green' });
  assert.equal(historyLine({ line: null, wins: 4, losses: 1 }), null);                       // Telegram hides it
  assert.equal(historyLine(null), null);
  assert.equal(historyLine({ wins: 1, losses: 3 }).text, 'Recomputed past seasons · 1–3 (25%) · small sample (4 settled) · red record');   // older API
});
