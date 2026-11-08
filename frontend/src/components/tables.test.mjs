// Layout guard for the history tables (run: npm run test:unit). Reads the .tsx sources as text, like competition.test.mjs
// stays free of a DOM. A `.table.history` collapses to cards only at <= 580 px (styles.css); between that and the desktop
// width its seven or eight columns are wider than the page, so each one must sit directly inside a `.table-wrap` scroll
// container, or the whole page scrolls sideways (audit M14: page scrollWidth 694 / 710 at 600 px).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = fileURLToPath(new URL('..', import.meta.url));

function tsxFiles(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? tsxFiles(join(dir, e.name)) : e.name.endsWith('.tsx') ? [join(dir, e.name)] : []);
}

/** Every <table> whose className names the `history` class, with what directly surrounds it. */
function historyTables() {
  const found = [];
  for (const file of tsxFiles(SRC)) {
    const text = readFileSync(file, 'utf8');
    const open = /<table\b[^>]*className="([^"]*)"[^>]*>/g;
    for (let m; (m = open.exec(text));) {
      if (!m[1].split(/\s+/).includes('history')) continue;
      const line = text.slice(0, m.index).split('\n').length;
      const close = text.indexOf('</table>', m.index);
      found.push({
        where: `${relative(SRC, file)}:${line}`,
        before: text.slice(0, m.index).trimEnd(),
        after: close < 0 ? '' : text.slice(close + '</table>'.length).trimStart(),
      });
    }
  }
  return found;
}

test('the history tables are found (the guard is not vacuous)', () => {
  // Canonical history, legacy history and market history (Canonical.tsx, HistoryPage.tsx, MarketView.tsx).
  assert.ok(historyTables().length >= 3, `expected at least 3 history tables, found ${historyTables().length}`);
});

test('.table-wrap is a horizontal scroll container', () => {
  const css = readFileSync(join(SRC, 'styles.css'), 'utf8');
  const rule = css.match(/(?:^|\n)\.table-wrap\s*\{([^}]*)\}/);
  assert.ok(rule, '.table-wrap rule not found in styles.css');
  assert.match(rule[1], /overflow-x:\s*auto/);
});

test('every history table sits directly inside a .table-wrap scroll container', () => {
  const bad = historyTables()
    .filter((t) => !t.before.endsWith('<div className="table-wrap">') || !t.after.startsWith('</div>'))
    .map((t) => t.where);
  assert.deepEqual(bad, [], `history tables without a directly enclosing <div className="table-wrap">: ${bad.join(', ')}`);
});
