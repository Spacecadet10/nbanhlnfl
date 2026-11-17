// The shared card section's wording (run: npm run test:unit). Same facts as the Telegram card (backend telegram.market_block).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { liveRecordLine, recordBasisLines, recordLabel, telegramLine, thisGameLine } from './cardRecord.ts';
import { quoteTime, sportsbookLine } from './sportsbook.ts';

const h = { sport: 'nba', market: 'spread', wins: 3, losses: 1, settled: 4, pushes: 0 };
const clock = () => '2:30 PM';

test('live record counts OTHER games, names pushes and says when nothing has settled', () => {
  assert.equal(liveRecordLine(h), 'Other settled games: 3 wins · 1 loss · 4 settled');
  assert.equal(liveRecordLine({ ...h, wins: 1, losses: 0, settled: 1, pushes: 2 }), 'Other settled games: 1 win · 0 losses · 2 pushes · 1 settled');
  assert.equal(liveRecordLine({ ...h, wins: 0, losses: 0, settled: 0 }), 'No settled results at this exact % yet');
});

test('the label leans on the other games only, never on this game', () => {
  assert.equal(recordLabel(h), 'More often right');
  assert.equal(recordLabel({ ...h, wins: 2, losses: 2 }), 'Evenly split');
  assert.equal(recordLabel({ ...h, wins: 0, losses: 1 }), 'First example lost');
  assert.equal(recordLabel({ ...h, wins: 0, losses: 0 }), 'No settled matches');
});

test('this game is stated separately and is never in the counts', () => {
  assert.match(thisGameLine('win'), /won \(not in the counts above\)/);
  assert.match(thisGameLine('loss'), /lost \(not in the counts above\)/);
  assert.match(thisGameLine('push'), /push/);
  assert.match(thisGameLine('half_win'), /half win/);
  assert.match(thisGameLine('pending'), /isn't counted until it is settled/);
  assert.match(thisGameLine(null), /isn't counted/);
});

test('recovered Telegram history is labelled and says the model version is not recorded', () => {
  const t = { source: 'telegram_cards', label: 'Recovered Telegram cards', period: { from: '2026-11-02', to: '2026-11-14' },
    wins: 3, losses: 2, pushes: 1, settled: 6, line_source: 'bookmaker' };
  assert.equal(telegramLine({ ...h, telegram: t }),
    'Recovered Telegram cards 11-02 to 11-14: 3W–2L · 1 push (6 settled) · model version not recorded');
  assert.match(telegramLine({ ...h, telegram: { ...t, wins: 0, losses: 0, pushes: 0, settled: 0 } }), /none settled at this exact %/);
});

test('no Telegram history: per-sport sentence, NCAAB says when recording began, an older API adds nothing', () => {
  const cov = { live_since: '2026-11-14T00:00:00Z', telegram_period: null, history_note: '' };
  assert.equal(telegramLine({ ...h, telegram: null, coverage: cov }), 'No recovered Telegram history for NBA');
  assert.equal(telegramLine({ ...h, sport: 'ncaab', telegram: null, coverage: cov }),
    'No recovered Telegram history for NCAAB (recording began 2026-11-14)');
  assert.equal(telegramLine({ ...h, sport: 'soccer', coverage: cov }), 'No recovered Telegram history for SOCCER');
  assert.equal(telegramLine(h), null);
});

test('record basis: latest of several pregame forecasts, and a card that was sent with another forecast', () => {
  const rb = { forecast_id: 7, generated_at: '2026-11-02T19:30:00Z', pregame_forecasts: 3,
    published: { forecast_id: 5, side: 'a', line: -9.5, probability: 0.52, grade: 'win' } };
  assert.deepEqual(recordBasisLines(rb, clock), [
    'Record uses the latest pregame forecast (#7, 2:30 PM); 2 earlier kept for audit',
    'Telegram card sent forecast #5 (a -9.5 52.0%): win']);
  assert.deepEqual(recordBasisLines({ ...rb, pregame_forecasts: 1, published: { forecast_id: 7 } }, clock), []);
  assert.deepEqual(recordBasisLines(null, clock), []);
});

test('sportsbook line: backend reason, quote time and conditional basis', () => {
  const book = { status: 'exact', app_probability: 0.6, book_probability: 0.5, gap_pp: null, provider: 'DraftKings', price: '-150/+120',
    line: null, quote_id: 1, retrieved_at: '2026-11-02T19:29:30Z', reason: null, basis: 'conditional on no push' };
  assert.equal(quoteTime('2026-11-02T19:29:30Z'), '2:29 PM');
  assert.equal(sportsbookLine(book), 'App 60.0% · Sportsbook 50.0% no-vig (DraftKings -150/+120) · +10.0 pts · quote 2:29 PM · conditional on no push');
  const none = { ...book, status: 'no_quote', book_probability: null, reason: 'No quote saved before the forecast' };
  assert.equal(sportsbookLine(none), 'No quote saved before the forecast');
});
