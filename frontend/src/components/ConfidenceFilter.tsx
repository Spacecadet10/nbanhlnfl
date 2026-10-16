import { useState } from 'react';
import { AsyncView, Empty } from './States';
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
// Three outcomes split the probability: a 3-way pick is rarely above 55%, so its levels start lower (as the research grid).
const THREE_WAY_LEVELS: (number | null)[] = [null, 0.4, 0.45, 0.5, 0.55, 0.6];

/** The canonical record's reporting grid (approved 2026-10-14): two-way markets 50–90%, the regulation three-way market
 * 35–70%, the same grids the canonical Performance report uses. */
const CANONICAL_LEVELS: (number | null)[] = [null, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9];
const CANONICAL_THREE_WAY_LEVELS: (number | null)[] = [null, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7];

/** The standard reporting grid of a market (50–90%; regulation three-way 35–70%). Research views that compute their own
 * grids show only these rows (decision 2026-10-14: filter at display; model code and stored numbers unchanged). */
export function standardGrid(market: MarketId): number[] {
  return (market === 'reg3' ? CANONICAL_THREE_WAY_LEVELS : CANONICAL_LEVELS).filter((x): x is number => x != null);
}
export const onStandardGrid = (value: number, market: MarketId) => standardGrid(market).some((t) => Math.abs(t - value) < 1e-6);
/** Rows of a computed ladder on the standard grid. A band (lo–hi) is kept only when both ends are grid levels (hi may be
 * the 100% top): a band such as 70–75% for the three-way market is not a standard band and is hidden, never shown as one. */
export function standardRows<T extends { lo: number; hi: number | null }>(rows: T[], market: MarketId, bands: boolean): T[] {
  return rows.filter((x) => onStandardGrid(x.lo, market)
    && (!bands || x.hi == null || x.hi >= 0.999999 || onStandardGrid(x.hi, market)));
}
/** The note for a band ladder: which picks the shown bands no longer cover (above the grid's highest computed band). */
export function bandNote<T extends { lo: number; hi: number | null }>(rows: T[], market: MarketId): string {
  const shown = standardRows(rows, market, true);
  const top = shown.length ? Math.max(...shown.map((x) => (x.hi == null || x.hi >= 0.999999 ? 1 : x.hi))) : null;
  return top != null && top < 0.999999
    ? ` Bands stop at ${Math.round(top * 100)}%: picks above it are in the cumulative ${Math.round(top * 100)}%+ row, not in a band.`
    : '';
}
/** A short note on what the standard-grid filter hid and which standard rows the view did not compute. */
export function gridNote(values: number[], market: MarketId, hiddenRows?: number): string {
  const grid = standardGrid(market);
  const hidden = hiddenRows ?? values.filter((v) => !onStandardGrid(v, market)).length;
  const missing = grid.filter((t) => !values.some((v) => Math.abs(v - t) < 1e-6));
  return `Standard grid ${Math.round(grid[0] * 100)}–${Math.round(grid[grid.length - 1] * 100)}% only`
    + (hidden ? ` (${hidden} other row${hidden > 1 ? 's' : ''} hidden)` : '')
    + (missing.length ? `; not computed by this view: ${missing.map((t) => `${Math.round(t * 100)}%`).join(', ')}` : '') + '.';
}

export function confidenceLevels(sport: Sport, market: MarketId, policy?: Policy): (number | null)[] {
  if (policy === 'canonical') return market === 'reg3' ? CANONICAL_THREE_WAY_LEVELS : CANONICAL_LEVELS;
  if (market === 'reg3') return THREE_WAY_LEVELS;
  return sport === 'nba' && market === 'winner' ? CONFIDENCE_LEVELS : MARKET_LEVELS;
}

export function defaultLevel(sport: Sport, market: MarketId): number | null {
  return sport === 'nba' && market === 'winner' ? 0.7 : null;
}

export const levelLabel = (min: number | null) => (min == null ? 'All picks' : `${Math.round(min * 100)}%+`);

/** The filter lives in the URL (?conf=all|55|60|…); the sport's and market's default when absent or not offered by the
 * market and policy shown (changing the market clears it; a level offered by both grids stays when the policy changes). */
export function useConfidence(sport: Sport = 'nba', market: MarketId = 'winner', policy?: Policy): [number | null, (level: number | null) => void] {
  const [params, setParams] = useSearchParams();
  const levels = confidenceLevels(sport, market, policy);
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

/** Every selected pick is settled, pending (no final result yet), void (moved, postponed or cancelled) or unscorable
 * (final but without a usable result). The counts always add up to the picks shown. */
function Unsettled({ pending, voids, unscorable }: { pending: number; voids: number; unscorable: number }) {
  return (
    <>
      <span className="record-sep" aria-hidden>·</span>
      <span>{pending} pending</span>
      <span className="record-sep" aria-hidden>·</span>
      <span>{voids} void</span>
      <span className="record-sep" aria-hidden>·</span>
      <span>{unscorable} unscorable</span>
    </>
  );
}

/** Which games a record covers; records are never per-day slates unless they say so. */
function Scope({ scope }: { scope: string }) {
  return <><strong className="record-scope">{scope}</strong><span className="record-sep" aria-hidden>·</span></>;
}

/** Observed record for one run type. Model confidence (a forecast) and observed win rate (results) are labelled apart. */
export function RecordLine({ record, runType, scope, parts }: { record: ConfidenceRecord; runType: RunType; scope: string;
  parts?: { key: string; label: string; record: ConfidenceRecord }[] }) {
  return (<>
    <p className="record-line">
      <RunBadge runType={runType} />{' '}
      <Scope scope={scope} />
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
      <Unsettled pending={record.pending} voids={record.void} unscorable={record.unscorable} />
    </p>
    {parts && parts.length > 0 && <ul className="record-parts small" aria-label="The same record, split; the parts add up to the line above">
      {parts.map((p) => <li key={p.key}><strong>{p.label}:</strong> {p.record.selected} {p.record.selected === 1 ? 'pick' : 'picks'}
        {p.record.settled > 0 ? <> · {p.record.wins}–{p.record.losses} ({pct(p.record.win_rate, 1)})</> : <> · none settled</>}
        {' '}· {p.record.pending} pending · {p.record.void} void</li>)}
    </ul>}
  </>);
}

/** Record of a non-winner market's picks: pushes and voids are shown, never counted as wins or losses. */
export function MarketRecordLine({ record, scope, parts }: { record: MarketRecordSummary; scope: string;
  parts?: { key: string; label: string; record: MarketRecordSummary }[] }) {
  return (<>
    <p className="record-line">
      <RunBadge runType="live" />{' '}
      <Scope scope={scope} />
      <span><strong>{record.selected}</strong> {record.selected === 1 ? 'pick' : 'picks'} {record.min_confidence == null ? '(all confidence levels)' : `at ${levelLabel(record.min_confidence)}`}</span>
      <span className="record-sep" aria-hidden>·</span>
      {record.wins + record.losses + record.pushes === 0
        ? <span className="muted">No settled games yet, so no observed win rate</span>
        : <>
            <span>Settled <strong>{record.wins}–{record.losses}–{record.pushes}</strong> (W–L–push)</span>
            <span className="record-sep" aria-hidden>·</span>
            <span>Win rate excl. pushes <strong>{pct(record.win_rate, 1)}</strong></span>
          </>}
      <Unsettled pending={record.pending} voids={record.voids} unscorable={record.unscorable} />
    </p>
    {(record.quoted_decided ?? 0) > 0 && <p className="small muted">On the {record.quoted_decided} decided picks made at a bookmaker line the picks won {record.quoted_wins} ({pct((record.quoted_wins ?? 0) / (record.quoted_decided ?? 1), 1)}); the bookmaker's own de-vigged probability for those sides averaged {pct(record.mean_implied ?? null, 1)}. A win rate near that figure is what the prices already implied.</p>}
    {parts && parts.length > 0 && <ul className="record-parts small" aria-label="The same record, split by lead time; the parts add up to the line above">
      {parts.map((p) => <li key={p.key}><strong>{p.label}:</strong> {p.record.selected} {p.record.selected === 1 ? 'pick' : 'picks'}
        {p.record.settled > 0 ? <> · {p.record.wins}–{p.record.losses}–{p.record.pushes} ({pct(p.record.win_rate, 1)})</> : <> · none settled</>}
        {' '}· {p.record.pending} pending · {p.record.voids} void</li>)}
    </ul>}
  </>);
}

/** Fetches only the record (limit 1) for one run type and forecast policy at the current confidence level. */
function FetchedRecord({ sport, runType, level, policy }: { sport: Sport; runType: 'live' | 'backtest'; level: number | null; policy: Policy }) {
  const { state } = useApi((s) => api.predictions(sport, { run_type: runType, min_confidence: level, limit: 1, policy }, s),
    [sport, runType, level, policy]);
  if (state.status === 'loading') return <p className="record-line muted">Loading record…</p>;
  if (state.status === 'error') return <p className="record-line warn">Record unavailable: {state.error}</p>;
  return <RecordLine record={state.data.record} runType={state.data.run_type} scope="All dates" parts={state.data.parts} />;
}

/** Records for the selected forecast policy. Live and backtest stay separate; backtests exist only for the
 * pre-game archive policy. Demo mode has a single demo record. */
function FetchedMarketRecord({ sport, market, level, policy }: { sport: Sport; market: MarketId; level: number | null; policy: Policy }) {
  const { state } = useApi((s) => api.marketPredictions(sport, market, { min_confidence: level, limit: 1, policy }, s),
    [sport, market, level, policy]);
  if (state.status === 'loading') return <p className="record-line muted">Loading record…</p>;
  if (state.status === 'error') return <p className="record-line warn">Record unavailable: {state.error}</p>;
  return <MarketRecordLine record={state.data.record} scope="All dates" parts={state.data.parts} />;
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


export function ThresholdResearch({ sport, market }: { sport: Sport; market: MarketId }) {
  const { state, retry } = useApi(s => api.thresholds(sport, s), [sport]);
  const [selected, setSelected] = useState('');
  return <div className="card"><h2>Results by confidence threshold · Two-hour record</h2>
    <p className="small muted">Each row includes picks at or above that confidence: 60%+ also includes 65%, 70% and higher. Win rates exclude pending games and pushes. Small samples do not establish a reliable threshold.</p>
    <AsyncView state={state} retry={retry}>{p => {
      const groups = p.groups.filter(g => g.market === market);
      const key = (g: typeof groups[number]) => `${g.model_version}|${g.line_source}`;
      const group = groups.find(g => key(g) === selected) ?? groups[0];
      return <>
        <p className="small muted">Every forecast version is retained: {p.all_stored_rows.winner_rows.toLocaleString()} winner rows and {p.all_stored_rows.market_rows.toLocaleString()} market rows across this sport's archive.</p>
        {!group ? <Empty>No two-hour forecasts recorded for this market yet.</Empty> : <>
          <label className="threshold-model">Model and line source <select value={key(group)} onChange={e => setSelected(e.target.value)}>
            {groups.map(g => <option key={key(g)} value={key(g)}>{g.model_version} · {g.line_source}</option>)}
          </select></label>
          <div className="table-wrap"><table className="table"><thead><tr><th>Minimum</th><th>W–L</th><th>Win rate</th><th>95% range</th><th>Coverage</th><th>Pending</th><th>Push / void / unscorable</th></tr></thead>
            <tbody>{group.thresholds.map(r => <tr key={r.threshold}><th scope="row">{pct(r.threshold)}+</th>
              <td>{r.win}–{r.loss}</td><td>{pct(r.accuracy, 1)}</td>
              <td>{r.interval ? `${pct(r.interval[0], 1)}–${pct(r.interval[1], 1)}` : '—'}</td>
              <td>{pct(r.coverage, 1)}</td><td>{r.pending}</td><td>{r.push} / {r.void} / {r.unscorable}</td>
            </tr>)}</tbody></table></div>
        </>}
        <p className="small muted">{p.note}</p>
      </>;
    }}</AsyncView>
  </div>;
}


export function RecoveredThresholds({ sport, market }: { sport: Sport; market: MarketId }) {
  const { state, retry } = useApi(s => api.recovered(sport, s), [sport]);
  return <div className="card"><h2>Missed games · Reconstructed, not live</h2>
    <AsyncView state={state} retry={retry}>{p => <>
      <p className="notice">{p.note}</p>
      {p.groups.filter(g => g.market === market).length === 0
        ? <Empty>{p.status === 'schema not migrated' ? 'Recovery is not activated in this store.' : 'No reconstructed picks for this market yet.'}</Empty>
        : p.groups.filter(g => g.market === market).map(g => <div key={`${g.model_version}|${g.line_source}`}>
          <p className="small wrap">{g.model_version} · {g.line_source} lines</p>
          <div className="table-wrap"><table className="table"><thead><tr><th>Minimum</th><th>W–L</th><th>Win rate</th><th>Pending</th><th>Push / void / unscorable</th></tr></thead>
            <tbody>{g.thresholds.map(r => <tr key={r.threshold}><th>{pct(r.threshold)}+</th><td>{r.win}–{r.loss}</td><td>{pct(r.accuracy, 1)}</td><td>{r.pending}</td><td>{r.push} / {r.void} / {r.unscorable}</td></tr>)}</tbody>
          </table></div>
        </div>)}
      <details><summary>Recovered game records</summary><ul className="small">{p.records.filter(r => r.market === market).slice(0, 50).map(r => <li key={r.recovery_id}>
        {r.game_id} · {r.tipoff_at} · {r.line == null ? 'no line' : `line ${r.line}`} · {r.outcome}
      </li>)}</ul></details>
    </>}</AsyncView>
  </div>;
}
