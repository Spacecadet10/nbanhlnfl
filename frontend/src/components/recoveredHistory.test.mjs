// Recovered Telegram predictions in History and Performance: wording, merging and counting helpers (no DOM).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { addTallies, earlierText, gradeLabel, lineText, mergeByStart, overlapText, recordText, unavailableText } from './recoveredHistory.ts';

const live = { forecast_id: 7, pick: 'CLE win', percentage: '57.0', grade: 'win' };

test('an overlap says which record counts the game, and a different version keeps both numbers visible', () => {
  assert.equal(overlapText({ overlap: { status: 'none' } }), 'Counted in the recovered record');
  assert.equal(overlapText({ overlap: { status: 'same', live } }), 'Same as live forecast #7: counted once, in the live record');
  assert.equal(overlapText({ overlap: { status: 'earlier_live_version', live, version_id: 3 } }),
    'Matches live version #3, replaced before the start by #7 (CLE win 57.0%, Correct): the live forecast is the one counted');
  assert.equal(overlapText({ overlap: { status: 'different', live } }),
    'Differs from live forecast #7 (CLE win 57.0%, Correct): both shown, the live forecast is the one counted');
});

test('an unsettled recovered prediction is never shown as a loss', () => {
  assert.equal(gradeLabel('unsettled'), 'Unsettled (no result)');
  assert.equal(gradeLabel('loss'), 'Wrong');
  assert.equal(gradeLabel('win'), 'Correct');
  assert.equal(gradeLabel('push'), 'Push');
});

test('a record prints wins, losses, pushes and unsettled apart; nothing is 0-0 when there is no prediction', () => {
  const t = { predictions: 5, win: 2, loss: 1, push: 1, void: 0, unsettled: 1, settled: 3, win_rate: 2 / 3 };
  assert.equal(recordText(t), '2–1 (66.7%) · 1 push · 1 unsettled');
  assert.equal(recordText({ ...t, predictions: 0, win: 0, loss: 0, push: 0, unsettled: 0, settled: 0, win_rate: null }), '—');
  assert.equal(recordText({ ...t, win: 0, loss: 0, push: 0, unsettled: 5, settled: 0, win_rate: null }), '5 unsettled');
  assert.equal(recordText(null), '—');
});

test('market totals add counts and recompute the rate from them', () => {
  const t = addTallies([{ predictions: 2, win: 2, loss: 0, push: 0, void: 0, unsettled: 0, settled: 2, win_rate: 1 },
    { predictions: 3, win: 0, loss: 2, push: 0, void: 0, unsettled: 1, settled: 2, win_rate: 0 }]);
  assert.deepEqual([t.predictions, t.win, t.loss, t.unsettled, t.settled, t.win_rate], [5, 2, 2, 1, 4, 0.5]);
});

test('live and recovered rows are merged newest first; the recovered version of a game follows its live row', () => {
  const rows = mergeByStart([{ id: 'L2', start: '2026-11-16T00:00:00Z' }, { id: 'L1', start: '2026-11-15T18:00:00Z' }], (r) => r.start,
    [{ game: 'R2', start_at: '2026-11-16T00:00:00Z' }, { game: 'R0', start_at: '2026-11-02T00:00:00Z' }]);
  assert.deepEqual(rows.map((r) => (r.kind === 'live' ? r.row.id : r.row.game)), ['L2', 'R2', 'L1', 'R0']);
});

test('the line and the line source are the card\'s own words; earlier cards say whether they differed', () => {
  assert.equal(lineText({ card_line_text: 'Model line' }), 'Model line');
  assert.equal(lineText({ card_line_text: null }), null);
  assert.equal(earlierText([]), null);
  assert.equal(earlierText(undefined), null);
  assert.equal(earlierText([{ differs: false }, { differs: false }]), '2 earlier cards, same prediction');
  assert.equal(earlierText([{ differs: true }, { differs: false }]), '2 earlier cards, 1 different');
  assert.equal(earlierText([{ differs: true }]), '1 earlier card, 1 different');
});

test('a sport without recovered rows says so', () => {
  assert.equal(unavailableText('no_rows_for_sport', 'NCAAB'), 'No recovered Telegram predictions for NCAAB: the source holds none.');
  assert.match(unavailableText('file_missing', 'NBA'), /not imported/);
  assert.match(unavailableText('unreadable', 'NBA'), /could not be read/);
  assert.match(unavailableText('old_format', 'NBA'), /rebuilt/);
});

test('both pages of every sport use the recovered history components', () => {
  const read = (f) => readFileSync(join(import.meta.dirname, '..', f), 'utf8');
  assert.match(read('pages/HistoryPage.tsx'), /recovered=/);
  assert.match(read('pages/PerformancePage.tsx'), /<RecoveredPerformance /);
  for (const f of ['pages/NflPages.tsx', 'pages/SoccerPages.tsx']) {
    assert.match(read(f), /<RecoveredPerformance /);
    assert.match(read(f), /<RecoveredLedger /);
  }
  assert.match(read('components/TelegramHistory.tsx'), /row\.label/);            // the required label, from the API
  assert.match(read('components/TelegramHistory.tsx'), /Unpriced/);
});

test('NCAAB pages never claim recovered predictions, and the filters that share the page with a date live in the URL', () => {
  const read = (f) => readFileSync(join(import.meta.dirname, '..', f), 'utf8');
  assert.match(read('components/Canonical.tsx'), /recovered\?\.available && /);
  assert.match(read('pages/HistoryPage.tsx'), /hasResearch\(sport\) && !canonical/);
  assert.match(read('pages/PerformancePage.tsx'), /if \(!hasResearch\(sport\)\) return null;.*no recovered/s);
  // Layout remounts the page on every URL change, so a market or competition kept in component state would be lost with a date.
  const src = read('components/TelegramHistory.tsx');
  assert.match(src, /params\.get\('tg_market'\)/);
  assert.match(src, /params\.get\('tg_scope'\)/);
});
