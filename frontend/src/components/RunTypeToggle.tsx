import { useSearchParams } from 'react-router-dom';
import type { Policy } from '../api/types';

export type ViewRunType = 'live' | 'backtest';

/** Live vs retrospective backtest, kept in the URL (?run=). The two are never mixed on one screen. */
export function useRunType(): [ViewRunType, (r: ViewRunType) => void] {
  const [params, setParams] = useSearchParams();
  const run: ViewRunType = params.get('run') === 'backtest' ? 'backtest' : 'live';
  const set = (r: ViewRunType) => {
    const next = new URLSearchParams(params);
    if (r === 'live') next.delete('run'); else next.set('run', r);
    setParams(next);
  };
  return [run, set];
}

export function RunTypeToggle({ value, onChange }: { value: ViewRunType; onChange: (r: ViewRunType) => void }) {
  return (
    <div className="segmented" role="group" aria-label="Forecast type">
      <button type="button" aria-pressed={value === 'live'} onClick={() => onChange('live')}>Live forecasts</button>
      <button type="button" aria-pressed={value === 'backtest'} onClick={() => onChange('backtest')}>Backtest (retrospective)</button>
    </div>
  );
}

/** Which live forecast: the canonical record (default where it exists: ?policy=canonical) or a legacy ledger — the
 * two-hour Elo ledger (?policy=near_tipoff) or the pre-game archive (?policy=pregame). Never mixed. Where the canonical
 * forecast is not available (NHL, other markets, so far) the two-hour ledger stays the default, as before. */
export function usePolicy(canonical = false): [Policy, (p: Policy) => void] {
  const [params, setParams] = useSearchParams();
  const raw = params.get('policy');
  const policy: Policy = raw === 'pregame' ? 'pregame' : raw === 'near_tipoff' ? 'near_tipoff'
    : canonical ? 'canonical' : 'near_tipoff';
  const set = (p: Policy) => {
    const next = new URLSearchParams(params);
    next.set('policy', p);
    setParams(next);
  };
  return [policy, set];
}

export function PolicyToggle({ value, onChange, canonical = false }: { value: Policy; onChange: (p: Policy) => void; canonical?: boolean }) {
  return (
    <div className="segmented" role="group" aria-label="Live forecast timing">
      {canonical && <button type="button" aria-pressed={value === 'canonical'} onClick={() => onChange('canonical')}>Canonical forecast</button>}
      <button type="button" aria-pressed={value === 'pregame'} onClick={() => onChange('pregame')}>{canonical ? 'Legacy: pre-game archive' : 'Pre-game archive'}</button>
      <button type="button" aria-pressed={value === 'near_tipoff'} onClick={() => onChange('near_tipoff')}>{canonical ? 'Legacy: within 2 hours' : 'Within 2 hours'}</button>
    </div>
  );
}
