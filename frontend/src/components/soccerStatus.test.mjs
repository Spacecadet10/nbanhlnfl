// Soccer page wording follows what the API reports (run: npm run test:unit). Live-state audit 2026-11-08: the page said
// "predictions are not available… no forecasts", "no validated model exists" and "Not refreshed automatically" beside 85
// recorded live forecasts and an hourly refresh. Four situations must read differently: forecasts recorded, matches still
// awaiting theirs, a competition with nothing stored, and a source that failed or went stale.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { dataSourceForecastText, daySummary, daySummaryText, emptyCompetition, forecastBanner, forecastKind, importHint, liveLoadProblem, matchForecastNote } from './soccerStatus.ts';

const when = (iso) => `<${iso}>`;
const forecast = { probabilities: { home: 0.4512, draw: 0.27, away: 0.2788 }, most_likely: 'home', result: null };
const NEVER = /not available|no forecasts|no validated model|not refreshed automatically/i;

test('recorded forecasts: the banner and the data note say they exist', () => {
  const b = forecastBanner('recorded', 'UEFA Nations League');
  assert.equal(b.kind, 'live');
  assert.match(b.title + ' ' + b.text, /Live soccer forecasts.*90-minute/);
  assert.doesNotMatch(b.title + ' ' + b.text, NEVER);
  assert.match(dataSourceForecastText('recorded'), /Live forecasts cover the 90-minute result, and goal and corner markets where tested/);
  assert.match(b.text, /goal and corner markets only where their own tested model covers/);
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
  assert.match(texts[1], /each match is forecast once, in the two hours before kickoff/);   // not the old 06:00 UTC deadline
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

// --- Consolidated review of 635a4ec -------------------------------------------------------------------------------
import { forecastStateOf } from './soccerStatus.ts';

test('a match past its forecast cutoff is not described as awaiting a forecast', () => {
  const cutoff = { passed: false, when: '<2026-11-15T06:00:00Z>' };
  assert.equal(matchForecastNote('recorded', 'scheduled', null, 'A', 'B', cutoff),
    'Awaiting forecast: recorded by <2026-11-15T06:00:00Z> at the latest, once the earlier results it depends on are stored.');
  assert.equal(matchForecastNote('recorded', 'scheduled', null, 'A', 'B', { ...cutoff, passed: true }),
    'No forecast: none was recorded before this match’s cutoff (<2026-11-15T06:00:00Z>), and none is made after it.');
  assert.doesNotMatch(matchForecastNote('awaiting', 'scheduled', null, 'A', 'B', { ...cutoff, passed: true }), /Awaiting/);
  assert.match(forecastBanner('awaiting', 'Premier League').text, /two hours before kickoff/);
  assert.doesNotMatch(forecastBanner('awaiting', 'Premier League').text, /06:00/);
});

test('an API older than this page (no forecast_state, no refresh, no forecast map) gets honest fallbacks, not a crash', () => {
  assert.equal(forecastStateOf(undefined, true), 'recorded');      // the old capability line already said forecasts exist
  assert.equal(forecastStateOf(undefined, false), 'unknown');
  assert.equal(forecastStateOf('awaiting', false), 'awaiting');
  const b = forecastBanner('unknown', 'Premier League');
  assert.match(b.title + ' ' + b.text, /not reported/);
  assert.doesNotMatch(b.title + ' ' + b.text, /No live soccer forecasts|not available/);
  assert.match(matchForecastNote('unknown', 'scheduled', undefined, 'A', 'B'), /not reported by this server/);
  assert.equal(importHint('Premier League', { check: null }, when, 'unknown'), 'Latest Premier League scoreboard request.');
  assert.equal(dataSourceForecastText('unknown'), 'Bookmaker prices: DraftKings as listed on ESPN (result, goals total, goal handicap) where available; no injury information is collected.');
});

test('stale data is only said to block forecasts for competitions that are forecast', () => {
  const stale = { check: null, refresh: { last_refresh_at: '2026-11-07T20:00:00Z', max_age_hours: 26, stale: true } };
  assert.match(importHint('Ligue 1', stale, when, 'awaiting'), /more than 26 h old, so new forecasts are refused/);
  assert.equal(importHint('UEFA Europa League', stale, when, 'out_of_scope'), 'Latest UEFA Europa League scoreboard request: more than 26 h old.');
  assert.doesNotMatch(importHint('UEFA Europa League', { ...stale, refresh: { ...stale.refresh, last_refresh_at: null } }, when, 'out_of_scope'), /forecast/);
});

// --- Day summary: what the two-hour forecast job actually saved for the selected date and competition ---------------------
const NOW = Date.parse('2026-11-16T20:00:00Z');
const iso = (h, m = 0) => `2026-11-${h >= 24 ? '17' : '16'}T${String(h % 24).padStart(2, '0')}:${String(m).padStart(2, '0')}:00Z`;
const match = (id, kickoff) => ({ match_id: id, kickoff });
// A two-hour forecast: generated 40 min before the kickoff it was made for (cutoff_at = that kickoff).
const twoHour = (kickoffAt, minutesBefore = 40) => ({ generated_at: new Date(Date.parse(kickoffAt) - minutesBefore * 60000).toISOString().replace('.000Z', 'Z'), cutoff_at: kickoffAt });
// The older daily forecast: made the evening before, cutoff 06:00 UTC of the match day.
const daily = { generated_at: '2026-11-15T19:05:00Z', cutoff_at: '2026-11-16T06:00:00Z' };

test('an all-timely elapsed day: every match has a forecast made within two hours of kickoff', () => {
  const ms = [match('a', iso(12)), match('b', iso(15))];
  const s = daySummary(ms, { a: twoHour(iso(12)), b: twoHour(iso(15), 110) }, NOW);
  assert.deepEqual([s.total, s.elapsed.count, s.elapsed.twoHour, s.elapsed.dailyOnly, s.elapsed.none, s.upcoming.count], [2, 2, 2, 0, 0, 0]);
  assert.deepEqual(s.moved, []);
  assert.match(daySummaryText(s, when), /2 matches.*2 kicked off.*2 with a forecast saved in the two hours before kickoff/);
});

test('an all-upcoming day: two-hour forecasts saved vs not yet, with when the first window opens', () => {
  const ms = [match('a', iso(21)), match('b', iso(22)), match('c', iso(23))];
  const s = daySummary(ms, { a: twoHour(iso(21)) }, NOW);
  assert.deepEqual([s.elapsed.count, s.upcoming.count, s.upcoming.twoHourSaved, s.upcoming.notYet], [0, 3, 1, 2]);
  assert.equal(s.upcoming.windowOpensAt, iso(20));                   // b kicks off 22:00, its window opens 20:00 (now), the earliest unsaved
  assert.match(daySummaryText(s, when), /3 upcoming.*1 with its two-hour forecast saved.*2 not yet.*first window opens <2026-11-16T20:00:00Z>/);
});

test('a moved kickoff is flagged only where the saved forecast says what the kickoff was', () => {
  const s = daySummary([match('m', iso(14)), match('n', iso(15))], { m: twoHour(iso(13)), n: daily }, NOW);
  assert.deepEqual(s.moved, [{ matchId: 'm', from: iso(13), to: iso(14), timelyForUpdated: true }]);   // cutoff_at = the kickoff then; 12:20 is inside both windows
  // 401927173: made 1h26m before the original 13:00 kickoff, 2h26m before the updated 14:00 one. The count follows the
  // updated kickoff; being on time for the original kickoff is shown separately.
  assert.equal(forecastKind(twoHour(iso(13), 86), iso(14)), 'original_kickoff');
  const s2 = daySummary([match('m', iso(14))], { m: twoHour(iso(13), 86) }, NOW);
  assert.deepEqual([s2.elapsed.twoHour, s2.elapsed.originalOnly, s2.elapsed.dailyOnly, s2.elapsed.none], [0, 1, 0, 0]);
  assert.match(daySummaryText(s2, when), /1 kicked off: 0 with a forecast saved in the two hours before kickoff, 1 on time only for its original kickoff\. Kickoff moved from <2026-11-16T13:00:00Z> to <2026-11-16T14:00:00Z> after the forecast: on time for the original kickoff, not the updated one\./);
  // a small move that keeps the forecast inside the updated window still counts, and the move is still named
  const s3 = daySummary([match('k', iso(13, 30))], { k: twoHour(iso(13)) }, NOW);
  assert.deepEqual([s3.elapsed.twoHour, s3.elapsed.originalOnly], [1, 0]);
  assert.match(daySummaryText(s3, when), /Kickoff moved from <2026-11-16T13:00:00Z> to <2026-11-16T13:30:00Z> after the forecast: on time for both\./);
  assert.equal(daySummary([match('n', iso(15))], { n: daily }, NOW).moved.length, 0);   // a daily row's 06:00 cutoff is not a kickoff
});

test('daily-only rows: elapsed matches with only the older daily forecast are named, not counted as two-hour', () => {
  const s = daySummary([match('a', iso(12)), match('b', iso(21)), match('c', iso(13))], { a: daily, b: daily, c: twoHour(iso(13)) }, NOW);
  assert.deepEqual([s.elapsed.twoHour, s.elapsed.dailyOnly, s.elapsed.none], [1, 1, 0]);
  assert.deepEqual([s.upcoming.twoHourSaved, s.upcoming.notYet, s.upcoming.dailyOnly], [0, 1, 1]);
  assert.match(daySummaryText(s, when), /1 only with the older daily forecast/);
  assert.equal(forecastKind(daily, iso(12)), 'daily');
  assert.equal(forecastKind(twoHour(iso(12)), iso(12)), 'two_hour');
  assert.equal(forecastKind(undefined, iso(12)), 'none');
  assert.match(matchForecastNote('recorded', 'final', { ...forecast, ...daily }, 'A', 'B', null, undefined, 'daily'), /older daily forecast, not a two-hour one/);
});

test('a mix with a miss: an elapsed match with no two-hour forecast is counted and shown as a miss', () => {
  const ms = [match('a', iso(12)), match('b', iso(13)), match('c', iso(14)), match('d', iso(23))];
  const s = daySummary(ms, { a: twoHour(iso(12)), b: twoHour(iso(13), 150), c: undefined }, NOW);   // b was generated 2.5 h before: too early
  assert.deepEqual([s.total, s.elapsed.count, s.elapsed.twoHour, s.elapsed.dailyOnly, s.elapsed.none, s.upcoming.notYet], [4, 3, 1, 1, 1, 1]);
  assert.match(daySummaryText(s, when), /1 with no forecast/);
  assert.equal(daySummaryText(daySummary([], {}, NOW), when), 'No matches on this date.');
});

test('the awaiting note describes the two-hour forecast, not a 06:00 UTC deadline', () => {
  const n = matchForecastNote('recorded', 'scheduled', null, 'A', 'B', null, { passed: false, when: '<2026-11-16T20:00:00Z>' });
  assert.match(n, /two-hour forecast.*<2026-11-16T20:00:00Z>/);
  assert.doesNotMatch(n, /06:00/);
  assert.match(matchForecastNote('recorded', 'scheduled', null, 'A', 'B', null, { passed: true, when: 'x' }), /No two-hour forecast was saved/);
});
