import { useEffect } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import type { League, MarketId, MarketInfo, SoccerCompetitionId, Sport } from '../api/types';
import { COMPETITION_IDS, competitionFrom, leagueFrom, soccerMatchCompetition } from './competition';

/** Sport and market live in the URL (?sport=nhl, ?market=total) so Today, History and Performance always show the
 * same selection. Absent means NBA and the winner market. An unknown value is never silently replaced by data of
 * another sport or market: it falls back only to these documented defaults. */
const LEAGUES: { id: League; label: string }[] = [{ id: 'nba', label: 'NBA' }, { id: 'nhl', label: 'NHL' }, { id: 'nfl', label: 'NFL' },
  { id: 'soccer', label: 'Soccer' }];
/** Soccer competitions (?competition=…, ESPN slugs). Absent means the Premier League; an unknown value never shows another
 * competition's data under its name: it falls back only to this documented default. On a soccer match page the match's
 * own competition wins (competition.ts). */
const COMPETITION_LABELS: Record<SoccerCompetitionId, string> = {
  'eng.1': 'Premier League', 'esp.1': 'La Liga', 'ger.1': 'Bundesliga', 'ita.1': 'Serie A', 'fra.1': 'Ligue 1',
  'uefa.champions': 'Champions League', 'uefa.europa': 'Europa League',
};
export const COMPETITIONS = COMPETITION_IDS.map((id) => ({ id, label: COMPETITION_LABELS[id] }));
const MARKETS: MarketId[] = ['winner', 'reg3', 'spread', 'total', 'team_total_home', 'team_total_away'];

/** The league shown (?sport=nba|nhl|nfl|soccer). NFL and soccer are data-only: the layout shows their own screens and
 * every forecast screen keeps using `useSport`, which never returns 'nfl' or 'soccer'. */
export function useLeague(): [League, (l: League) => void] {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const league = leagueFrom(location.pathname, params.get('sport'));
  const set = (s: League) => {
    const next = new URLSearchParams(params);
    if (s === 'nba') next.delete('sport'); else next.set('sport', s);
    next.delete('market');   // markets differ by sport; the winner market exists for both
    next.delete('team');
    next.delete('date');
    next.delete('conf');     // confidence levels are sport- and market-specific
    next.delete('competition');
    // Game identifiers belong to one league. Choose a game in the new league before opening its dashboard.
    if (s !== league && location.pathname.startsWith('/games/')) navigate({ pathname: '/', search: next.toString() });
    else setParams(next);
  };
  return [league, set];
}

/** The served sport of the forecast screens: NBA or NHL only (NFL and soccer selections are handled by the layout). */
export function useSport(): [Sport, (s: Sport) => void] {
  const [league, set] = useLeague();
  return [league === 'nhl' ? 'nhl' : 'nba', set];
}

/** The soccer competition shown. Changing it clears the date and leaves an open match (match ids belong to one competition). */
export function useCompetition(): [SoccerCompetitionId, (c: SoccerCompetitionId) => void] {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const competition = competitionFrom(location.pathname, params.get('competition'));
  const set = (c: SoccerCompetitionId) => {
    const next = new URLSearchParams(params);
    if (c === 'eng.1') next.delete('competition'); else next.set('competition', c);
    next.delete('date');
    if (c !== competition && location.pathname.startsWith('/games/')) navigate({ pathname: '/', search: next.toString() });
    else setParams(next);
  };
  return [competition, set];
}

/** On a soccer match page, writes the match's league and competition into the URL (replacing the history entry) when the
 * query is missing or contradicts them, so links carried from the page (back to matches, other matches) stay consistent. */
export function useSoccerMatchUrlSync(): void {
  const [params, setParams] = useSearchParams();
  const { pathname } = useLocation();
  const match = soccerMatchCompetition(pathname);
  const want = match === 'eng.1' ? null : match;
  const stale = match !== null && (params.get('sport') !== 'soccer' || params.get('competition') !== want);
  useEffect(() => {
    if (!stale || match === null) return;
    const next = new URLSearchParams(params);
    next.set('sport', 'soccer');
    if (want) next.set('competition', want); else next.delete('competition');
    for (const k of ['market', 'conf', 'team', 'date']) next.delete(k);   // NBA/NHL selections do not apply to soccer
    setParams(next, { replace: true });
  }, [stale, match, want, params, setParams]);
}

export function useMarket(): [MarketId, (m: MarketId) => void] {
  const [params, setParams] = useSearchParams();
  const raw = params.get('market') as MarketId | null;
  const market: MarketId = raw && MARKETS.includes(raw) ? raw : 'winner';
  const set = (m: MarketId) => {
    const next = new URLSearchParams(params);
    if (m === 'winner') next.delete('market'); else next.set('market', m);
    next.delete('conf');
    setParams(next);
  };
  return [market, set];
}

/** Research detail (other timing policy, research lines, research cards) is hidden unless ?details=1, so the default
 * view answers: the pick, how sure, whether the models agree, and the bookmaker. Model agreement is always shown. */
export function useDetails(): [boolean, (on: boolean) => void] {
  const [params, setParams] = useSearchParams();
  const on = params.get('details') === '1';
  const set = (v: boolean) => {
    const next = new URLSearchParams(params);
    if (v) next.set('details', '1'); else next.delete('details');
    setParams(next, { replace: true });
  };
  return [on, set];
}

export function DetailsToggle() {
  const [on, set] = useDetails();
  return (
    <label className="details-toggle small">
      <input type="checkbox" checked={on} onChange={(e) => set(e.target.checked)} /> Show research details
    </label>
  );
}

/** The query string to keep when moving between screens: the selection, not per-screen filters. */
export function useCarriedSearch(): string {
  const [params] = useSearchParams();
  const keep = new URLSearchParams();
  for (const k of ['sport', 'competition', 'market', 'policy', 'conf', 'details']) {
    const v = params.get(k);
    if (v) keep.set(k, v);
  }
  const q = keep.toString();
  return q ? `?${q}` : '';
}

export function SportToggle({ value, onChange }: { value: League; onChange: (s: League) => void }) {
  return (
    <div className="segmented sport-toggle" role="group" aria-label="League">
      {LEAGUES.map((s) => (
        <button key={s.id} type="button" aria-pressed={value === s.id} onClick={() => onChange(s.id)}>{s.label}</button>
      ))}
    </div>
  );
}

export function MarketSelect({ value, markets, onChange }: { value: MarketId; markets: MarketInfo[]; onChange: (m: MarketId) => void }) {
  const current = markets.find((m) => m.id === value);
  return (
    <div className="market-select">
      <label>
        <span className="confidence-filter-label">Market</span>
        <select value={value} onChange={(e) => onChange(e.target.value as MarketId)}>
          {markets.map((m) => (
            <option key={m.id} value={m.id} disabled={!m.available}>{m.title}{m.available ? '' : ' (not available)'}</option>
          ))}
          {!current && <option value={value} disabled>{value} (not defined for this sport)</option>}
        </select>
      </label>
      {current && <p className="small muted market-note">{current.note}</p>}
      {!current && <p className="small warn">This market is not defined for this sport.</p>}
    </div>
  );
}

export function CompetitionSelect({ value, onChange }: { value: SoccerCompetitionId; onChange: (c: SoccerCompetitionId) => void }) {
  return (
    <label className="competition-select">
      <select value={value} onChange={(e) => onChange(e.target.value as SoccerCompetitionId)} aria-label="Competition">
        {COMPETITIONS.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
      </select>
    </label>
  );
}
