// What each sport has: NCAAB has no research views and is the only sport with a conference filter (run: npm run test:unit).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { conferenceFrom, conferenceOptions, hasConferenceFilter, hasResearch } from './sportCaps.ts';

test('research views exist for NBA and NHL, never for NCAAB', () => {
  assert.equal(hasResearch('nba'), true);
  assert.equal(hasResearch('nhl'), true);
  assert.equal(hasResearch('ncaab'), false);
});

test('only NCAAB has a conference filter', () => {
  assert.deepEqual(['nba', 'nhl', 'ncaab'].map(hasConferenceFilter), [false, false, true]);
});

test('a conference from the URL must be one the API listed, else all games', () => {
  assert.equal(conferenceFrom('ACC', ['ACC', 'Big 12']), 'ACC');
  assert.equal(conferenceFrom('Pac-12', ['ACC', 'Big 12']), null);
  assert.equal(conferenceFrom(null, ['ACC']), null);
  assert.equal(conferenceFrom('ACC', []), null);
});

test('conference options are sorted, trimmed and listed once', () => {
  assert.deepEqual(conferenceOptions(['SEC', ' ACC ', null, 'ACC', '', undefined, 'Big 12']), ['ACC', 'Big 12', 'SEC']);
});
