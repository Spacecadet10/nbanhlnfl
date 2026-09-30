import { useSearchParams } from 'react-router-dom';

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
