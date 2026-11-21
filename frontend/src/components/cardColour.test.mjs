// Card colouring twin of backend app/card_threshold.py (run: npm run test:unit). Same Decimal cases as the backend tests.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { cardColour, cardTableStale, tenthsOf, torontoDay } from './sportsbook.ts';

const table = (lines, extra = {}) => ({
  enabled: true, date: '2026-11-21', no_colour_line_source: 'model',
  routes: { nba: { winner: 'W', spread: 'L', team_total_home: 'L' }, nhl: { reg3: 'R', winner: 'W' }, ncaab: { spread: 'L' },
    soccer: { double_chance: null, draw_no_bet: null, corners_total: null } },
  scopes: Object.entries(lines).map(([scope, t]) => ({ scope, class: scope.split(':').pop(), sport: scope.includes(':') ? scope.split(':')[0] : null,
    threshold: t, n: 250, state: t == null ? 'not established' : 'set', set_on: '2026-11-21',
    legend: t == null ? 'No threshold for this market yet' : `Green = ${t}% and above` })), ...extra,
});

test('tenths use the decimal digits, half-up, never float x 100', () => {
  assert.equal(tenthsOf(0.5494), 549);
  assert.equal(tenthsOf(0.5495), 550);
  assert.equal(tenthsOf(0.5725), 573);
  assert.equal(tenthsOf(0.5724), 572);
});

test('Decimal boundaries at T = 55.0 and T = 57.3', () => {
  assert.equal(cardColour(table({ W: '55.0' }), 'nba', 'winner', 0.5494).colour, 'red');
  assert.equal(cardColour(table({ W: '55.0' }), 'nba', 'winner', 0.5495).colour, 'green');
  assert.equal(cardColour(table({ W: '57.3' }), 'nba', 'winner', 0.5725).colour, 'green');
  assert.equal(cardColour(table({ W: '57.3' }), 'nba', 'winner', 0.5724).colour, 'red');
});

test('each class is compared with its own threshold; a cell overrides only its sport', () => {
  const t = table({ W: '52.0', R: '60.0', L: '55.0', 'ncaab:L': '60.0' });
  assert.equal(cardColour(t, 'nhl', 'reg3', 0.53).colour, 'red');
  assert.equal(cardColour(t, 'nba', 'winner', 0.53).colour, 'green');
  assert.equal(cardColour(t, 'ncaab', 'spread', 0.58, 'bookmaker').colour, 'red');
  assert.equal(cardColour(t, 'nba', 'spread', 0.58, 'bookmaker').colour, 'green');
});

test('unpriced markets and model lines get no colour; flag off gets nothing', () => {
  const t = table({ W: '55.0', L: '50.0', D: '60.0' });
  assert.deepEqual(cardColour(t, 'soccer', 'double_chance', 0.9), { colour: null, legend: null, scope: null });
  assert.equal(cardColour(t, 'soccer', 'corners_total', 0.9).colour, null);
  assert.equal(cardColour(t, 'nba', 'team_total_home', 0.6, 'model').colour, null);
  assert.equal(cardColour({ enabled: false }, 'nba', 'winner', 0.9).colour, null);
});

test('a class with no threshold is not coloured; its status legend is shown (colour release, Nov 21)', () => {
  assert.deepEqual(cardColour(table({ W: null }), 'nba', 'winner', 0.7), { colour: null, legend: 'No threshold for this market yet', scope: 'W' });
});

test('the stored lines are refetched after 10 minutes or on a new Toronto day', () => {
  const at = Date.parse('2026-11-21T20:00:00Z');                     // 3:00 PM in Toronto, Nov 21
  const t = { enabled: true, date: '2026-11-21' };
  assert.equal(torontoDay(at), '2026-11-21');
  assert.equal(cardTableStale(at, at + 60_000, t), false);
  assert.equal(cardTableStale(at, at + 11 * 60_000, t), true);
  assert.equal(cardTableStale(at, Date.parse('2026-11-22T05:01:00Z'), { ...t }, 24 * 3600_000), true);   // past Toronto midnight
  assert.equal(cardTableStale(at, at + 60_000, { enabled: false }), false);
});

// Colour release (Nov 21): the web reads green_from from the API (the backend's single decision), never the raw threshold.
const scopeTable = (scope) => ({ enabled: true, date: '2026-11-22', no_colour_line_source: 'model', routes: { nba: { winner: 'W' } }, scopes: [scope] });

test('a stale or unqualified line is not coloured but its status legend is shown', () => {
  const stale = { scope: 'W', class: 'W', sport: null, threshold: '55.0', green_from: null, qualifies: false, n: 342, state: 'not refreshed',
    set_on: '2026-11-20', legend: 'Threshold not refreshed today (last set Nov 20) · not coloured' };
  assert.deepEqual(cardColour(scopeTable(stale), 'nba', 'winner', 0.95),
    { colour: null, legend: 'Threshold not refreshed today (last set Nov 20) · not coloured', scope: 'W' });
});

test('a capped line greens from green_from, not the raw threshold', () => {
  const capped = { scope: 'W', class: 'W', sport: null, threshold: '56.0', green_from: '58.0', qualifies: true, n: 342, state: 'capped',
    set_on: '2026-11-22', legend: 'Green = 58.0% and above' };
  assert.equal(cardColour(scopeTable(capped), 'nba', 'winner', 0.5794).colour, 'red');
  assert.equal(cardColour(scopeTable(capped), 'nba', 'winner', 0.5795).colour, 'green');
});
