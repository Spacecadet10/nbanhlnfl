import { useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import type { ConfidenceRecord, MarketId, MarketRecordSummary, Policy, RunType, Sport } from '../api/types';
import { useApi } from '../api/useApi';
import { pct } from '../format';
import { RunBadge } from './Badges';

/** Thresholds on the model's probability for its pick (winner: max(p_home, 1 - p_home); other markets: the picked
 * side). Levels and defaults are per sport and market: the NBA winner's 70% default was chosen for that market only and
 * is never applied to the NHL or to other markets, which default to all picks. null = all picks. */
export const CONFIDENCE_LEVELS: (number | null)[] = [null, 0.6, 0.65, 0.7, 0.75, 0.8];
const MARKET_LEVELS: (number | null)[] = [null, 0.55, 0.6, 0.65, 0.7];

export function confidenceLevels(sport: Sport, market: MarketId): (number | null)[] {
  return sport === 'nba' && market === 'winner' ? CONFIDENCE_LEVELS : MARKET_LEVELS;
}

export function defaultLevel(sport: Sport, market: MarketId): number | null {
  return sport === 'nba' && market === 'winner' ? 0.7 : null;
}

export const levelLabel = (min: number | null) => (min == null ? 'All picks' : `${Math.round(min * 100)}%+`);

/** The filter lives in the URL (?conf=all|55|60|…); the sport's and market's default when absent or not offered. */
export function useConfidence(sport: Sport = 'nba', market: MarketId = 'winner'): [number | null, (level: number | null) => void] {
  const [params, setParams] = useSearchParams();
  const levels = confidenceLevels(sport, market);
  const fallback = defaultLevel(sport, market);
  const raw = params.get('conf');
  const parsed = raw === 'all' ? null : levels.find((l) => l != null && String(Math.round(l * 100)) === raw);
  const level = raw == null || parsed === undefined ? fallback : parsed;
  const set = (next: number | null) => {
    const p = new URLSearchParams(params);
    if (next === fallback) p.delete('conf'); else p.set('conf', next == null ? 'all' : String(Math.round(next * 100)));
    setParams(p);
  };
  return [level, set];
}

export function ConfidenceFilter({ value, onChange, levels = CONFIDENCE_LEVELS }: {
  value: number | null; onChange: (level: number | null) => void; levels?: (number | null)[];
}) {
  return (
    <div className="confidence-filter">
      <span className="confidence-filter-label" id="confidence-filter-label">Model confidence in the pick</span>
      <div className="segmented" role="group" aria-labelledby="confidence-filter-label">
        {levels.map((l) => (
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

/** Record of a non-winner market's picks: pushes and voids are shown, never counted as wins or losses. */
export function MarketRecordLine({ record }: { record: MarketRecordSummary }) {
  return (
    <p className="record-line">
      <RunBadge runType="live" />{' '}
      <span><strong>{record.selected}</strong> {record.selected === 1 ? 'pick' : 'picks'} {record.min_confidence == null ? '(all confidence levels)' : `at ${levelLabel(record.min_confidence)}`}</span>
      <span className="record-sep" aria-hidden>·</span>
      {record.wins + record.losses + record.pushes === 0
        ? <span className="muted">No settled games yet, so no observed win rate</span>
        : <>
            <span>Settled <strong>{record.wins}–{record.losses}–{record.pushes}</strong> (W–L–push)</span>
            <span className="record-sep" aria-hidden>·</span>
            <span>Win rate excl. pushes <strong>{pct(record.win_rate, 1)}</strong></span>
          </>}
      {record.voids > 0 && <><span className="record-sep" aria-hidden>·</span><span className="muted">{record.voids} void</span></>}
    </p>
  );
}

/** Fetches only the record (limit 1) for one run type and forecast policy at the current confidence level. */
function FetchedRecord({ sport, runType, level, policy }: { sport: Sport; runType: 'live' | 'backtest'; level: number | null; policy: Policy }) {
  const { state } = useApi((s) => api.predictions(sport, { run_type: runType, min_confidence: level, limit: 1, policy }, s),
    [sport, runType, level, policy]);
  if (state.status === 'loading') return <p className="record-line muted">Loading record…</p>;
  if (state.status === 'error') return <p className="record-line warn">Record unavailable: {state.error}</p>;
  return <RecordLine record={state.data.record} runType={state.data.run_type} />;
}

/** Records for the selected forecast policy. Live and backtest stay separate; backtests exist only for the
 * night-before policy. Demo mode has a single demo record. */
function FetchedMarketRecord({ sport, market, level, policy }: { sport: Sport; market: MarketId; level: number | null; policy: Policy }) {
  const { state } = useApi((s) => api.marketPredictions(sport, market, { min_confidence: level, limit: 1, policy }, s),
    [sport, market, level, policy]);
  if (state.status === 'loading') return <p className="record-line muted">Loading record…</p>;
  if (state.status === 'error') return <p className="record-line warn">Record unavailable: {state.error}</p>;
  return <MarketRecordLine record={state.data.record} />;
}

export function RangeRecords({ sport = 'nba', market = 'winner', level, realMode, policy }: {
  sport?: Sport; market?: MarketId; level: number | null; realMode: boolean; policy: Policy;
}) {
  if (market !== 'winner') {
    return (
      <div className="range-records" aria-label="Observed record of picks in this confidence range">
        <FetchedMarketRecord sport={sport} market={market} level={level} policy={policy} />
        <p className="small muted">Model confidence is the forecast probability for the picked side. Pushes and voided games are not counted as wins or losses.</p>
      </div>
    );
  }
  return (
    <div className="range-records" aria-label="Observed record of picks in this confidence range">
      <FetchedRecord sport={sport} runType="live" level={level} policy={policy} />
      {realMode && policy === 'pregame' && <FetchedRecord sport={sport} runType="backtest" level={level} policy="pregame" />}
      <p className="small muted">
        Model confidence is the forecast probability for the predicted winner. The observed win rate counts settled games only and is not a promise of future results.
      </p>
    </div>
  );
}
