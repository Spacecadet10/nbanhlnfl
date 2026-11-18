import { test } from 'node:test';
import assert from 'node:assert/strict';
import { cellLabel, marketLabel, COLUMNS, COLUMN_NOTE } from './learningCoverage.ts';

test('states keep their own words and an unknown or missing cell is never shown as Yes or No', () => {
  assert.equal(cellLabel({ state: 'yes', detail: '' }), 'Yes');
  assert.equal(cellLabel({ state: 'no', detail: '' }), 'No');
  assert.equal(cellLabel({ state: 'unknown', detail: 'unknown: table missing' }), 'Unknown');
  assert.equal(cellLabel({ state: 'not_established', detail: '' }), 'Not established');
  assert.equal(cellLabel(undefined), 'Unknown');
});
test('forecasting with fits and improved prospectively are separate columns', () => {
  const keys = COLUMNS.map(c => c[0]);
  assert.ok(keys.includes('forecasting_with_fits') && keys.includes('improved_prospectively') && keys.includes('recording_prospectively'));
});
test('market labels', () => {
  assert.equal(marketLabel('goals_ou'), 'goals O/U');
  assert.equal(marketLabel('intl_1x2'), 'intl 1X2');
  assert.equal(marketLabel('team_total_home'), 'team total home');
});
test('scheduled is worded as installed (plist) with the caveat inline, and the base-model test is its own column', () => {
  const by = Object.fromEntries(COLUMNS.map(c => [c[0], c[1]]));
  assert.equal(by.scheduled, 'Installed (plist)');
  assert.match(COLUMN_NOTE.scheduled, /not proof it is loaded/);
  assert.equal(by.base_tested, 'Base model test');
  assert.equal(by.evaluated, 'Settled with fit');
  assert.ok(!COLUMNS.some(c => c[1] === 'Evaluated' || c[1] === 'Scheduled'));
});
