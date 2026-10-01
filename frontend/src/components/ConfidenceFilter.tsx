import { useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import type { ConfidenceRecord, RunType } from '../api/types';
import { useApi } from '../api/useApi';
import { pct } from '../format';
import { RunBadge } from './Badges';

/** Thresholds on the model's probability for its predicted winner, max(p_home, 1 - p_home). null = all picks. */
export const CONFIDENCE_LEVELS: (number | null)[] = [null, 0.6, 0.65, 0.7, 0.75, 0.8];
const DEFAULT_LEVEL = 0.7;

export const confidenceOf = (homeWinProb: number) => Math.max(homeWinProb, 1 - homeWinProb);
/** Same rule as the API: the threshold itself counts (1 - 0.3 is 70%). */
export const meetsConfidence = (homeWinProb: number, min: number | null) =>
  min == null || confidenceOf(homeWinProb) >= min - 1e-9;
export const levelLabel = (min: number | null) => (min == null ? 'All picks' : `${Math.round(min * 100)}%+`);

/** The filter lives in the URL (?conf=all|60|65|70|75|80); 70%+ when absent. */
export function useConfidence(): [number | null, (level: number | null) => void] {
  const [params, setParams] = useSearchParams();
  const raw = params.get('conf');
  const parsed = raw === 'all' ? null : CONFIDENCE_LEVELS.find((l) => l != null && String(Math.round(l * 100)) === raw);
  const level = raw == null || parsed === undefined ? DEFAULT_LEVEL : parsed;
  const set = (next: number | null) => {
    const p = new URLSearchParams(params);
    if (next === DEFAULT_LEVEL) p.delete('conf'); else p.set('conf', next == null ? 'all' : String(Math.round(next * 100)));
    setParams(p);
  };
  return [level, set];
}

export function ConfidenceFilter({ value, onChange }: { value: number | null; onChange: (level: number | null) => void }) {
  return (
    <div className="confidence-filter">
      <span className="confidence-filter-label" id="confidence-filter-label">Model confidence in the pick</span>
      <div className="segmented" role="group" aria-labelledby="confidence-filter-label">
        {CONFIDENCE_LEVELS.map((l) => (
          <button key={String(l)} type="button" aria-pressed={value === l} onClick={() => onChange(l)}>
            {l == null ? 'All' : `${Math.round(l * 100)}%+`}
          </button>
        ))}
      </div>
    </div>
  );
}

/** Observed record for one run type. Model confidence (a forecast) and observed win rate (results) are labelled apart. */
export function RecordLine({ record, runType }: { record: ConfidenceRecord; runType: RunType }) {
  return (
    <p className="record-line">
      <RunBadge runType={runType} />{' '}
      <span>
        <strong>{record.selected}</strong> {record.selected === 1 ? 'pick' : 'picks'}{' '}
        {record.min_confidence == null ? '(all confidence levels)' : `at ${levelLabel(record.min_confidence)}`}
      </span>
      <span className="record-sep" aria-hidden>·</span>
      {record.settled === 0
        ? <span className="muted">No settled games yet, so no observed win rate</span>
        : <>
            <span>Settled record <strong>{record.wins}–{record.losses}</strong> ({record.settled} settled)</span>
            <span className="record-sep" aria-hidden>·</span>
            <span>Observed win rate <strong>{pct(record.win_rate, 1)}</strong></span>
          </>}
    </p>
  );
}

/** Fetches only the record (limit 1) for one run type at the current confidence level. */
function FetchedRecord({ runType, level }: { runType: 'live' | 'backtest'; level: number | null }) {
  const { state } = useApi((s) => api.predictions({ run_type: runType, min_confidence: level, limit: 1 }, s), [runType, level]);
  if (state.status === 'loading') return <p className="record-line muted">Loading record…</p>;
  if (state.status === 'error') return <p className="record-line warn">Record unavailable: {state.error}</p>;
  return <RecordLine record={state.data.record} runType={state.data.run_type} />;
}

/** Live and backtest records side by side but never combined. Demo mode has a single demo record. */
export function RangeRecords({ level, realMode }: { level: number | null; realMode: boolean }) {
  return (
    <div className="range-records" aria-label="Observed record of picks in this confidence range">
      <FetchedRecord runType="live" level={level} />
      {realMode && <FetchedRecord runType="backtest" level={level} />}
      <p className="small muted">
        Model confidence is the forecast probability for the predicted winner. The observed win rate counts settled games only and is not a promise of future results.
      </p>
    </div>
  );
}
