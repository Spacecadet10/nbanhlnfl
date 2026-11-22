// Bucket-record colouring (run: npm run test:unit). Each exact-% bucket is coloured by its OWN settled record, never by a
// probability cutoff: green = >=1 win and win rate >= 75%; red = losses > wins; neutral otherwise. Pushes/voids are not in the
// denominator. Wording matches the Telegram card (backend app/telegram.py).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { bucketColour, bucketColourOf, bucketHeadline, bucketSubLine, telegramRecoveredLine } from './cardRecord.ts';

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

test('recovered Telegram line: own date range, own colour, pushes and voids, matching the backend wording', () => {
  const t = { available: true, period: { from: '2026-11-02', to: '2026-11-16' }, wins: 2, losses: 0, pushes: 1, voids: 0, colour: 'green', model_version_known: false };
  assert.deepEqual(telegramRecoveredLine('57.3', t), { colour: 'green',
    text: 'Recovered Telegram cards Nov 2–16 (model version unknown) · 57.3% · this exact % has gone 2–0 (100%) · 1 push · small sample (2 settled)' });
  assert.equal(telegramRecoveredLine('57.3', { ...t, period: { from: '2026-11-30', to: '2026-12-02' }, pushes: 0, voids: 1 }).text,
    'Recovered Telegram cards Nov 30–Dec 2 (model version unknown) · 57.3% · this exact % has gone 2–0 (100%) · 1 void · small sample (2 settled)');
  assert.equal(telegramRecoveredLine('57.3', { ...t, wins: 1, losses: 3, colour: undefined }).colour, 'red');
});

test('recovered Telegram line: unavailable and empty are never shown as 0–0 and never fall back to live', () => {
  assert.deepEqual(telegramRecoveredLine('57.3', null), { colour: 'neutral', text: 'Recovered Telegram cards (model version unknown) · recovered history unavailable' });
  assert.equal(telegramRecoveredLine('57.3', undefined).text, 'Recovered Telegram cards (model version unknown) · recovered history unavailable');
  assert.match(telegramRecoveredLine('57.3', { available: false, wins: 2, losses: 0 }).text, /recovered history unavailable$/);
  const empty = telegramRecoveredLine('57.3', { available: true, period: { from: '2026-11-02', to: '2026-11-16' }, wins: 0, losses: 0, pushes: 0 });
  assert.deepEqual(empty, { colour: 'neutral', text: 'Recovered Telegram cards Nov 2–16 (model version unknown) · no recovered history at this exact %' });
  assert.doesNotMatch(empty.text, /0–0/);
});
