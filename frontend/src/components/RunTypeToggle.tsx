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

/** Which live forecast: the two-hour one (default) or the night-before archive (?policy=pregame). Never mixed. */
export function usePolicy(): [Policy, (p: Policy) => void] {
  const [params, setParams] = useSearchParams();
  const policy: Policy = params.get('policy') === 'pregame' ? 'pregame' : 'near_tipoff';
  const set = (p: Policy) => {
    const next = new URLSearchParams(params);
    next.set('policy', p);
    setParams(next);
  };
  return [policy, set];
}

export function PolicyToggle({ value, onChange }: { value: Policy; onChange: (p: Policy) => void }) {
  return (
    <div className="segmented" role="group" aria-label="Live forecast timing">
      <button type="button" aria-pressed={value === 'pregame'} onClick={() => onChange('pregame')}>Night-before archive</button>
      <button type="button" aria-pressed={value === 'near_tipoff'} onClick={() => onChange('near_tipoff')}>Within 2 hours</button>
    </div>
  );
}
