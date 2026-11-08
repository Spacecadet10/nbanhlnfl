// Pure selection rules for soccer deep links (no imports besides types, so Node can test it directly:
// src/components/competition.test.mjs). A soccer match id names its competition (soccer:<competition>:<ESPN event id>,
// backend app/soccer.py), so on a match page the league and competition follow the match, not the query string.
import type { League, SoccerCompetitionId } from '../api/types';

export const COMPETITION_IDS: readonly SoccerCompetitionId[] =
  ['eng.1', 'esp.1', 'ger.1', 'ita.1', 'fra.1', 'uefa.champions', 'uefa.europa',
   // national teams (backend app/intl_live.py)
   'uefa.nations', 'concacaf.nations.league', 'fifa.friendly', 'caf.nations_qual', 'uefa.euroq', 'afc.cupq', 'fifa.worldq.uefa', 'fifa.worldq.conmebol', 'fifa.worldq.concacaf', 'fifa.worldq.afc', 'fifa.worldq.caf', 'fifa.worldq.ofc', 'uefa.euro', 'conmebol.america', 'caf.nations', 'afc.asian.cup', 'concacaf.gold', 'fifa.world'];

const isCompetition = (v: string | null): v is SoccerCompetitionId => !!v && (COMPETITION_IDS as readonly string[]).includes(v);

/** The competition named by a soccer match path (/games/soccer:<competition>:<digits> or /games/of:<competition>:…), else null. */
export function soccerMatchCompetition(pathname: string): SoccerCompetitionId | null {
  if (!pathname.startsWith('/games/')) return null;
  let id: string;
  try { id = decodeURIComponent(pathname.slice('/games/'.length)); } catch { return null; }
  // ESPN ids (soccer:<competition>:<event id>) and openfootball history ids (of:<competition>:<season>:<16 hex>)
  const m = /^soccer:([a-z0-9._]+):(\d+)$/.exec(id) ?? /^of:([a-z0-9.]+):\d{4}:[0-9a-f]{16}$/.exec(id);
  return m && isCompetition(m[1]) ? m[1] : null;
}

/** The selected competition: the match's own on a soccer match page; otherwise ?competition, defaulting to eng.1. */
export function competitionFrom(pathname: string, raw: string | null): SoccerCompetitionId {
  return soccerMatchCompetition(pathname) ?? (isCompetition(raw) ? raw : 'eng.1');
}

/** The selected league: soccer on a soccer match page; otherwise ?sport (nhl|nfl|soccer), defaulting to nba. */
export function leagueFrom(pathname: string, raw: string | null): League {
  if (soccerMatchCompetition(pathname)) return 'soccer';
  return raw === 'nhl' || raw === 'nfl' || raw === 'soccer' ? raw : 'nba';
}
