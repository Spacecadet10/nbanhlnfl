// Soccer page wording follows what the API reports (run: npm run test:unit). Live-state audit 2026-11-08: the page said
// "predictions are not available… no forecasts", "no validated model exists" and "Not refreshed automatically" beside 85
// recorded live forecasts and an hourly refresh. Four situations must read differently: forecasts recorded, matches still
// awaiting theirs, a competition with nothing stored, and a source that failed or went stale.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { dataSourceForecastText, emptyCompetition, forecastBanner, importHint, liveLoadProblem, matchForecastNote } from './soccerStatus.ts';

const when = (iso) => `<${iso}>`;
const forecast = { probabilities: { home: 0.4512, draw: 0.27, away: 0.2788 }, most_likely: 'home', result: null };
const NEVER = /not available|no forecasts|no validated model|not refreshed automatically/i;

test('recorded forecasts: the banner and the data note say they exist', () => {
  const b = forecastBanner('recorded', 'UEFA Nations League');
  assert.equal(b.kind, 'live');
  assert.match(b.title + ' ' + b.text, /Live soccer forecasts.*90-minute/);
  assert.doesNotMatch(b.title + ' ' + b.text, NEVER);
  assert.match(dataSourceForecastText('recorded'), /Live forecasts cover the 90-minute result only/);
  assert.doesNotMatch(dataSourceForecastText('recorded'), /No live soccer forecast/);
});

test('a forecasted match shows its three probabilities, and its result once known', () => {
  assert.equal(matchForecastNote('recorded', 'scheduled', forecast, 'Türkiye', 'Belgium'),
    '90-minute forecast: Türkiye win 45.1% · Draw 27.0% · Belgium win 27.9%.');
  assert.equal(matchForecastNote('recorded', 'final', { ...forecast, result: 'draw' }, 'Türkiye', 'Belgium'),
    '90-minute forecast: Türkiye win 45.1% · Draw 27.0% · Belgium win 27.9%. 90-minute result: Draw.');
});

test('an unforecasted match says whether its forecast is still to come', () => {
  assert.match(matchForecastNote('awaiting', 'scheduled', null, 'A', 'B'), /^Awaiting forecast/);
  assert.match(matchForecastNote('recorded', 'scheduled', undefined, 'A', 'B'), /^Awaiting forecast/);
  assert.equal(matchForecastNote('recorded', 'final', null, 'A', 'B'), 'No forecast was recorded for this match.');
  assert.match(matchForecastNote('out_of_scope', 'scheduled', null, 'A', 'B'), /outside the live model/);
  assert.match(matchForecastNote('no_live_store', 'scheduled', null, 'A', 'B'), /this data store records none/);
  for (const state of ['recorded', 'awaiting', 'out_of_scope', 'no_live_store']) {
    assert.doesNotMatch(matchForecastNote(state, 'scheduled', null, 'A', 'B'), /no validated model/i);
  }
});

test('matches awaiting forecasts, out-of-scope competitions and stores without forecasts read differently', () => {
  const texts = ['recorded', 'awaiting', 'out_of_scope', 'no_live_store'].map((s) => {
    const b = forecastBanner(s, 'Premier League'); return b.title + ' ' + b.text;
  });
  assert.equal(new Set(texts).size, 4);
  assert.match(texts[1], /No Premier League forecast is recorded yet/);
  assert.match(texts[1], /each match is forecast before it starts/);
  assert.match(texts[2], /did not pass its held-out test/);
  assert.match(texts[3], /data store/);
});

test('"Last imported" describes the refresh that actually happened', () => {
  const live = { check: { checked_at: '2026-11-09T01:30:54Z' }, refresh: { last_refresh_at: '2026-11-09T02:25:17Z', max_age_hours: 26, stale: false } };
  assert.equal(importHint('Premier League', live, when),
    'Latest Premier League scoreboard request. The live soccer job last checked this competition <2026-11-09T01:30:54Z>.');
  assert.doesNotMatch(importHint('Premier League', live, when), NEVER);
  const stale = { check: live.check, refresh: { last_refresh_at: '2026-11-07T20:00:00Z', max_age_hours: 26, stale: true } };
  assert.match(importHint('Premier League', stale, when), /more than 26 h old.*new forecasts are refused/);
  assert.equal(importHint('Premier League', { check: null, refresh: live.refresh }, when),
    'Latest Premier League scoreboard request. No live soccer job has checked this competition in this data store.');
  assert.equal(importHint('Premier League', null, when), 'Latest Premier League scoreboard request.');
});

test('an empty competition and a failed source are not described as "no predictions"', () => {
  assert.equal(emptyCompetition('UEFA Euro qualifying', 41, '2026-11-09T02:26:53Z', when),
    'No UEFA Euro qualifying matches are stored: ESPN listed none on the 41 days requested so far (latest request <2026-11-09T02:26:53Z>).');
  assert.match(emptyCompetition('UEFA Euro qualifying', 0, null, when), /no day has been requested/);
  assert.match(liveLoadProblem('503 Service Unavailable'), /could not be loaded \(503 Service Unavailable\).*not affected/);
});
