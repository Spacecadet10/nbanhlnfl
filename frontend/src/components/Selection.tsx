import { useSearchParams } from 'react-router-dom';
import type { MarketId, MarketInfo, Sport } from '../api/types';

/** Sport and market live in the URL (?sport=nhl, ?market=total) so Today, History and Performance always show the
 * same selection. Absent means NBA and the winner market. An unknown value is never silently replaced by data of
 * another sport or market: it falls back only to these documented defaults. */
const SPORTS: { id: Sport; label: string }[] = [{ id: 'nba', label: 'NBA' }, { id: 'nhl', label: 'NHL' }];
const MARKETS: MarketId[] = ['winner', 'reg3', 'spread', 'total', 'team_total_home', 'team_total_away'];

export function useSport(): [Sport, (s: Sport) => void] {
  const [params, setParams] = useSearchParams();
  const sport: Sport = params.get('sport') === 'nhl' ? 'nhl' : 'nba';
  const set = (s: Sport) => {
    const next = new URLSearchParams(params);
    if (s === 'nba') next.delete('sport'); else next.set('sport', s);
    next.delete('market');   // markets differ by sport; the winner market exists for both
    next.delete('team');
    next.delete('date');
    next.delete('conf');     // confidence levels are sport- and market-specific
    setParams(next);
  };
  return [sport, set];
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
  for (const k of ['sport', 'market', 'policy', 'conf', 'details']) {
    const v = params.get(k);
    if (v) keep.set(k, v);
  }
  const q = keep.toString();
  return q ? `?${q}` : '';
}

export function SportToggle({ value, onChange }: { value: Sport; onChange: (s: Sport) => void }) {
  return (
    <div className="segmented sport-toggle" role="group" aria-label="Sport">
      {SPORTS.map((s) => (
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
