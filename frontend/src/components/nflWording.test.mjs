// Wording guard for the NFL screens (run: npm run test:unit). Reads the .tsx sources as text. NFL live forecasts, odds
// and a prospective record exist once the store's activation is valid (backend nfl.capabilities()), so the UI must not
// say the NFL is "data only" or has "no NFL model" (audit M11 / NFL-C F2). What it says instead follows the API's
// `forecasts` capability, and names only what exists: no accuracy, edge or performance claim.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
const layout = read('./Layout.tsx');
const nfl = read('../pages/NflPages.tsx');

test('no NFL screen calls the NFL data only or says there is no NFL model', () => {
  assert.doesNotMatch(layout, /no NFL model/i);
  assert.doesNotMatch(nfl, /no NFL model/i);
  assert.doesNotMatch(nfl, /NFL is data only/i);   // Daily Learning subtitle and the Game day notice
});

test('the Real-data note describes NFL forecasts when they are active, and says when they are not', () => {
  // Order-sensitive: the active text is the true branch of `nflForecasts ?`, the inactive text its false branch.
  assert.match(layout, /\{nflForecasts\s*\?\s*'Live forecasts are recorded within two hours of kickoff for regular-season and playoff games: [^']*'\s*:\s*'NFL live forecasts are not active for this data store[^']*'/,
    'the NFL note must show the active text only when the forecasts capability is available');
  assert.match(layout, /the winner from an NFL Elo model; spread, total and team totals from a points model/);
  // nfl_live.record: a quote counts only if retrieved in [cutoff - 2 h, cutoff], cutoff = kickoff - 120 min.
  assert.match(layout, /against a bookmaker line collected 2–4 hours before kickoff when there is one, otherwise a model line\. No injury or weather information\./);
});

test('NFL pages without content name what is missing and do not deny recorded forecasts', () => {
  // Order-sensitive: the active subtitle is the true branch of `forecastsActive ?`.
  assert.match(nfl, /sub=\{forecastsActive\s*\?\s*'Not available for the NFL\. NFL live forecasts and their record are on Game Day, Prediction History and Performance\.'\s*:\s*'Not available: NFL live forecasts are not active for this data store\.'\}/);
  // "No recorded NFL forecasts to classify" is only true while forecasts are not active.
  assert.match(nfl, /\{!forecastsActive && <NflThresholdRecord capabilities=\{meta\.capabilities\} \/>\}/);
  assert.match(nfl, /NFL live forecasts are not active:<\/strong> schedule and results only/);
});

test('the new NFL wording makes no accuracy, edge or performance claim', () => {
  const said = [...layout.matchAll(/nflForecasts\s*\?[\s\S]*?: sport === 'nhl'/g), ...nfl.matchAll(/sub=\{forecastsActive[^}]*\}/g)]
    .map((m) => m[0]).join('\n');
  assert.ok(said.length > 0, 'new wording not found');
  assert.doesNotMatch(said, /accura|edge|profit|beat|reliable|proven|model performance/i);
});
