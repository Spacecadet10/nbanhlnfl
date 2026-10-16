import { useState } from 'react';
import { api } from '../api/client';
import type { CoverageRecord, CoverageReport, MarketId, Sport } from '../api/types';
import { useApi } from '../api/useApi';
import { MARKET } from './Research';
import { AsyncView, Empty } from './States';
import { dateTime, pct } from '../format';

const SOURCE: Record<string, string> = {
  adaptive: 'Adaptive ensemble (only if approved; none is)', primary_recorded: 'Sealed primary, recorded',
  primary_direct: 'Sealed primary, computed for coverage', primary_night_before: 'Night-before primary (older ledger)',
};
const STATE: Record<string, string> = {
  win: 'Won', loss: 'Lost', push: 'Push', pending: 'Pending', void: 'Void', unscorable: 'Unscorable', unresolved: 'Unresolved',
  missing: 'Missing', upcoming: 'Not started', unvalidated: 'No validated model',
};

function Rec({ r }: { r: CoverageRecord }) {
  return <>{r.win}–{r.loss}{r.push ? `–${r.push}` : ''} · {pct(r.win_rate, 1)} · {r.pending} pending · {r.void} void
    {r.unresolved ? ` · ${r.unresolved} unresolved` : ''}{r.unscorable ? ` · ${r.unscorable} unscorable` : ''}</>;
}

/** Every scheduled game and market of the chosen Eastern dates: what was forecast (and by which source), how it was graded,
 * and what is missing with the reason. The record is unfiltered; the threshold rows only filter it. */
export function CoverageCard({ sport, market }: { sport: Sport; market: MarketId }) {
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const { state, retry } = useApi((s) => api.coverage(sport, from || undefined, to || undefined, s), [sport, from, to]);
  return (
    <div className="card coverage">
      <h2>Every-game coverage</h2>
      <p className="small muted">Every scheduled game, including preseason, should have a forecast for every market before it starts, or a
        visible reason. Preseason rows are experimental / unvalidated: no model is validated on preseason games. Confidence never decides
        what is recorded or graded; the threshold rows below only filter the full record.</p>
      <form className="filters" onSubmit={(e) => e.preventDefault()}>
        <label>From <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
        <label>To <input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label>
      </form>
      <AsyncView state={state} retry={retry}>{(c) => <CoverageBody c={c} market={market} />}</AsyncView>
    </div>
  );
}

function CoverageBody({ c, market }: { c: CoverageReport; market: MarketId }) {
  if (c.status !== 'available' || !c.schedule || !c.record || !c.by_market) return <Empty>{c.note ?? 'No coverage report available.'}</Empty>;
  const s = c.schedule;
  const src = c.source_schedule;
  const m = c.by_market[market];
  const rec = c.record_by_market?.[market];
  if (!rec) return <Empty>No coverage record for this market in this report.</Empty>;
  const types = Object.entries(s.by_season_type).map(([k, v]) => `${v} ${k.replace('_', '-')}`).join(', ');
  return <>
    <p className="small muted">{c.date_from} to {c.date_to} (Eastern dates) · fallback order {c.order_version}: {(c.order ?? []).map((o) => SOURCE[o] ?? o).join(' → ')}</p>
    <ul className="small">
      <li>Scheduled: <strong>{s.games}</strong> games{types ? ` (${types})` : ''}; {s.games_with_any_forecast} with a forecast, {s.games_fully_forecast} for every market{s.games_unvalidated ? `; ${s.games_unvalidated} preseason without a validated model (no canonical forecast)` : ''}{s.upcoming_games ? `; ${s.upcoming_games} not started yet` : ''}{s.not_forecastable_type ? `; ${s.not_forecastable_type} exhibition games not forecast` : ''}.</li>
      <li>Source schedule: {src?.available
        ? <>{src.source_games} games listed by the source; {src.missing_from_store?.length ?? 0} missing from this store; {src.field_differences?.length ?? 0} with a different start, type or status.</>
        : <span className="warn">{src?.reason ?? 'not checked'}</span>}</li>
      {s.corrected_results > 0 && <li>{s.corrected_results} final results were corrected after first being seen (graded with the corrected result, once).</li>}
      {c.recording_began_at
        ? <li>Every-game recording began {dateTime(c.recording_began_at)}; earlier games show the pregame forecast the existing ledgers hold.</li>
        : <li>Every-game recording has not started in this store; games show the pregame forecast the existing ledgers hold.</li>}
    </ul>
    {m && <p className="small"><strong>{MARKET[market] ?? market}</strong>: {m.forecast} of {m.cells} forecast ({m.main} adaptive, {m.fallback} sealed primary), {m.missing} missing{m.upcoming ? `, ${m.upcoming} not started` : ''}{m.unvalidated ? `, ${m.unvalidated} preseason without a validated model` : ''} · {m.graded} graded, {m.pending} pending, {m.void} void{m.unresolved ? `, ${m.unresolved} unresolved` : ''}{m.unscorable ? `, ${m.unscorable} unscorable` : ''}.</p>}

    <h3>{MARKET[market] ?? market} forecasts, unfiltered</h3>
    <div className="table-wrap"><table className="table"><thead><tr><th>Source</th><th>Forecasts</th><th>Record (W–L–P · win rate)</th></tr></thead>
      <tbody>
        <tr><th scope="row">All (regular season and playoffs)</th><td>{rec.all.forecasts}</td><td><Rec r={rec.all} /></td></tr>
        <tr><th scope="row">Adaptive ensemble (research, not approved)</th><td>{rec.main.forecasts}</td><td><Rec r={rec.main} /></td></tr>
        <tr><th scope="row">Sealed primary</th><td>{rec.fallback.forecasts}</td><td><Rec r={rec.fallback} /></td></tr>
        {c.preseason_record && <tr><th scope="row">Preseason · {c.preseason_record.label ?? 'Experimental / unvalidated'} · all markets combined (not in the record above)</th><td>{c.preseason_record.forecasts}</td><td><Rec r={c.preseason_record} /></td></tr>}
      </tbody></table></div>

    <h3>Same record by confidence threshold</h3>
    <p className="small muted">{MARKET[market] ?? market} only, on its own grid ({market === 'reg3' ? '35–70%' : '50–90%'}): filters that market's forecasts in the record above; markets are never pooled. Fallback forecasts are counted apart from the ensemble and never train it.</p>
    <div className="table-wrap"><table className="table"><thead><tr><th>Minimum</th><th>All</th><th>Adaptive</th><th>Sealed primary</th></tr></thead>
      <tbody>{(c.thresholds_by_market?.[market] ?? []).map((t) => <tr key={t.threshold}><th scope="row">{pct(t.threshold)}+</th>
        <td>{t.all.win}–{t.all.loss} · {pct(t.all.win_rate, 1)} ({t.all.forecasts})</td>
        <td>{t.main.win}–{t.main.loss} · {pct(t.main.win_rate, 1)}</td><td>{t.fallback.win}–{t.fallback.loss} · {pct(t.fallback.win_rate, 1)}</td></tr>)}</tbody>
    </table></div>

    <h3>Sources</h3>
    <ul className="small">{(['competitive', 'preseason'] as const).map((b) => <li key={b}>{b === 'competitive' ? 'Regular season and playoffs' : 'Preseason (experimental / unvalidated)'}: {
      Object.entries(c.sources?.[b] ?? {}).map(([k, v]) => `${SOURCE[k] ?? k} ${v}`).join(' · ') || 'none'}</li>)}</ul>

    <h3>Missing forecasts</h3>
    {(c.missing_reasons ?? []).length === 0 ? <p className="small">None: every game and market in this range has a forecast.</p>
      : <ul className="small">{(c.missing_reasons ?? []).map((r) => <li key={r.reason}><strong>{r.cells}</strong> game-markets: {r.reason}</li>)}</ul>}

    <details><summary>Games ({(c.games ?? []).length})</summary>
      <div className="table-wrap"><table className="table"><thead><tr><th>Start</th><th>Game</th><th>Type</th><th>{MARKET[market] ?? market}</th></tr></thead>
        <tbody>{(c.games ?? []).map((g) => { const cell = g.markets.find((x) => x.market === market); return <tr key={g.game_id}>
          <td>{dateTime(g.tipoff_at)}</td><td className="wrap">{g.game_id}</td><td>{g.season_type}</td>
          <td className="wrap">{!cell ? '—' : cell.status === 'missing'
            ? <span className="warn">Missing: {cell.reason}{cell.retrospective_available ? ' (a retrospective reconstruction exists; not a pregame forecast)' : ''}</span>
            : cell.status === 'unvalidated'
            ? <><span className="warn">No validated forecast: {cell.reason}</span>{cell.experimental && <span className="muted"> · {cell.experimental.label} row #{cell.experimental.forecast_id}: {pct(cell.experimental.confidence, 1)} · {STATE[cell.experimental.grade] ?? cell.experimental.grade} (not a canonical forecast; in no accepted record)</span>}</>
            : <>{cell.label && <strong className="warn">{cell.label} · </strong>}{cell.forecast_id != null && `#${cell.forecast_id} · `}{STATE[cell.status] ?? cell.status} · {SOURCE[cell.source ?? ''] ?? cell.source} · {pct(cell.confidence, 1)}{cell.line != null ? ` · line ${cell.line} (${cell.line_source === 'bookmaker' ? 'bookmaker' : 'model line'})` : ''}{cell.fallback_reason ? <span className="muted"> · why fallback: {cell.fallback_reason}</span> : null}{cell.later_failure && <span className="warn"> · a later attempt (#{cell.later_failure.forecast_id}, {dateTime(cell.later_failure.generated_at)}) recorded no forecast: {cell.later_failure.reason}; this earlier forecast stays the one graded</span>}</>}</td>
        </tr>; })}</tbody></table></div>
    </details>

    <details><summary>Grading of every stored forecast row, by ledger and model</summary>
      <p className="small muted">All rows in each ledger, not only the latest per game; no threshold. Retrospective ledgers were made after the start and are never live forecasts.</p>
      <div className="table-wrap"><table className="table"><thead><tr><th>Ledger</th><th>Model</th><th>Rows (games)</th><th>Graded</th><th>Record</th></tr></thead>
        <tbody>{(c.ledgers ?? []).filter((l) => l.market === market).map((l) => <tr key={`${l.ledger}|${l.model}`}>
          <td>{l.ledger}{l.retrospective ? ' (retrospective)' : ''}</td><td className="wrap"><code>{l.model}</code></td><td>{l.forecasts} ({l.games})</td>
          <td>{l.graded}</td><td><Rec r={l} /></td></tr>)}</tbody></table></div>
    </details>
    {(c.notes ?? []).map((n) => <p key={n} className="small muted">{n}</p>)}
  </>;
}
