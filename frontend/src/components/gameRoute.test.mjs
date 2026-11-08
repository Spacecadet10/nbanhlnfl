// Regression check for game links (run: npm run test:unit). A malformed percent-encoding such as /games/%E0%A4%A made
// decodeURIComponent throw during render and blanked the whole app for every league; one shared parser decides instead.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { gameRoute } from './gameRoute.ts';

test('malformed or empty game links are reported, never thrown', () => {
  for (const path of ['/games/%E0%A4%A', '/games/%', '/games/%ZZ', '/games/', '/games/%20', '/games/a/b']) {
    assert.deepEqual(gameRoute(path), { kind: 'malformed' }, path);
  }
});

test('valid game links decode once, for every league id format', () => {
  assert.deepEqual(gameRoute('/games/espn%3A401585123'), { kind: 'game', id: 'espn:401585123' });
  assert.deepEqual(gameRoute('/games/nfl:401772'), { kind: 'game', id: 'nfl:401772' });
  assert.deepEqual(gameRoute('/games/soccer%3Auefa.champions%3A733606'), { kind: 'game', id: 'soccer:uefa.champions:733606' });
  assert.deepEqual(gameRoute('/games/of%3Aeng.1%3A2021%3A0123456789abcdef'), { kind: 'game', id: 'of:eng.1:2021:0123456789abcdef' });
});

test('other pages are not game links', () => {
  for (const path of ['/', '/history', '/performance', '/daily-learning', '/gamesx']) {
    assert.deepEqual(gameRoute(path), { kind: 'none' }, path);
  }
});
