// Regression check for soccer deep links (run: npm run test:unit). Plain JS so tsc ignores it; Node strips the types of
// the module under test. A match id names its competition (soccer:<competition>:<ESPN event id>), so the selection must
// follow the match even when the URL has no or a different ?competition / ?sport.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { competitionFrom, leagueFrom, soccerMatchCompetition } from './competition.ts';

const ucl = '/games/soccer%3Auefa.champions%3A733606';

test('a match path names its competition, whatever the query says', () => {
  assert.equal(soccerMatchCompetition(ucl), 'uefa.champions');
  assert.equal(soccerMatchCompetition('/games/soccer:uefa.europa:734499'), 'uefa.europa');
  assert.equal(competitionFrom(ucl, null), 'uefa.champions');            // ?competition missing
  assert.equal(competitionFrom(ucl, 'eng.1'), 'uefa.champions');         // ?competition contradicts the match
  assert.equal(leagueFrom(ucl, null), 'soccer');                         // ?sport missing
  assert.equal(leagueFrom(ucl, 'nba'), 'soccer');
});

test('outside a soccer match the query decides, with documented defaults only', () => {
  assert.equal(competitionFrom('/', 'fra.1'), 'fra.1');
  assert.equal(competitionFrom('/', null), 'eng.1');
  assert.equal(competitionFrom('/', 'mls'), 'eng.1');
  assert.equal(competitionFrom('/performance', 'uefa.europa'), 'uefa.europa');
  assert.equal(leagueFrom('/', 'nhl'), 'nhl');
  assert.equal(leagueFrom('/', 'soccer'), 'soccer');
  assert.equal(leagueFrom('/', null), 'nba');
  assert.equal(leagueFrom('/games/espn%3A401', 'nba'), 'nba');
});

test('malformed or unknown soccer ids never select a competition', () => {
  for (const p of ['/games/soccer%3Amls%3A1', '/games/soccer%3Aeng.1%3Aabc', '/games/soccer%3Aeng.1', '/games/%E0%A4%A', '/games/nfl%3A401']) {
    assert.equal(soccerMatchCompetition(p), null, p);
  }
  assert.equal(competitionFrom('/games/soccer%3Amls%3A1', 'ger.1'), 'ger.1');
});

test('openfootball history match ids name their competition too', () => {
  const of = '/games/of%3Auefa.europa%3A2022%3A0123456789abcdef';
  assert.equal(soccerMatchCompetition(of), 'uefa.europa');
  assert.equal(competitionFrom(of, 'eng.1'), 'uefa.europa');
  assert.equal(leagueFrom(of, 'nba'), 'soccer');
  assert.equal(soccerMatchCompetition('/games/of:mls:2022:0123456789abcdef'), null);
  assert.equal(soccerMatchCompetition('/games/of:eng.1:2022:XYZ'), null);
});

test('national-team competitions are selectable and their match links name them', () => {
  assert.equal(soccerMatchCompetition('/games/soccer%3Acaf.nations_qual%3A401900001'), 'caf.nations_qual');
  assert.equal(soccerMatchCompetition('/games/soccer:uefa.nations:401900002'), 'uefa.nations');
  assert.equal(soccerMatchCompetition('/games/soccer:fifa.worldq.conmebol:401900003'), 'fifa.worldq.conmebol');
  assert.equal(competitionFrom('/', 'fifa.friendly'), 'fifa.friendly');
  assert.equal(leagueFrom('/games/soccer:concacaf.nations.league:401900004', null), 'soccer');
});
