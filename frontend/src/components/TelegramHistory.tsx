// Recovered Telegram predictions (backend app/telegram_history.py) in Prediction History and Performance: the original game,
// market, side, line, percentage and result of each card, counted once together with the live record. The API decides what
// overlaps and what is counted; this file only shows it.
import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import type { League, TgExact, TgLive, TgReport, TgRow, TgSummary } from '../api/types';
import { useDataRevision } from '../api/freshness';
import { useApi } from '../api/useApi';
import { calendarDate, dateTime, FULL_DATE, pct } from '../format';
import { useCarriedSearch } from './Selection';
import { AsyncView, Empty } from './States';
import { addTallies, earlierText, gradeLabel, lineText, mergeByStart, overlapText, recordText, unavailableText, versionText } from './recoveredHistory';

const sportName = (s: string) => (s === 'soccer' ? 'Soccer' : s.toUpperCase());
const gradeClass = (g: string) => (g === 'win' ? 'ok' : g === 'loss' ? 'bad' : 'muted');
/** The card's own send time (Toronto wall clock in the export), shown without shifting it. */
const sentText = (local: string | null) => local == null ? 'at an unknown time' : `${calendarDate(local.slice(0, 10), FULL_DATE)}, ${local.slice(11, 16)} Toronto`;
const periodText = (r: TgReport) => (r.period ? `${calendarDate(r.period.from, FULL_DATE)} – ${calendarDate(r.period.to, FULL_DATE)}` : '');
const LINE: Record<string, string> = { bookmaker: 'bookmaker line', model: 'model line', none: '' };

function Grade({ grade }: { grade: string }) {
  return <span className={gradeClass(grade)}>{gradeLabel(grade)}</span>;
}

/** What the card said, as one block: pick, original percentage, line note, price state. */
function CardSaid({ row }: { row: TgRow }) {
  const line = lineText(row);
  return <>
    <strong>{row.pick}</strong> {row.percentage}%{row.recommended && <span className="badge"> Recommended pick</span>}
    <span className="small muted recovered-line">{line ? `${line} · ` : ''}Unpriced</span>
    <span className="small muted recovered-line">Exact {row.percentage}% {row.direction}, all dates, this game included: {row.bucket.combined.predictions ? recordText(row.bucket.combined) : 'no counted prediction (this game is counted under its live forecast)'}</span>
  </>;
}

/** When it was sent, how it relates to the live record, and the game's earlier cards. */
function CardRecord({ row }: { row: TgRow }) {
  const earlier = earlierText(row.earlier_versions);
  return <>
    Sent {sentText(row.sent_at_local)} · message {row.message_id}
    <span className={`small recovered-line ${row.overlap.status === 'different' || row.overlap.status === 'earlier_live_version' ? 'warn' : 'muted'}`}>{overlapText(row)}</span>
    {earlier && <details className="small"><summary>{earlier}</summary>
      <ul>{row.earlier_versions.map((v) => <li key={v.message_id}>{sentText(v.sent_at_local)}: {v.pick} {v.percentage}% ({versionText(v)}) · message {v.message_id}</li>)}</ul>
    </details>}
  </>;
}

const tag = (row: TgRow) => <span className="badge badge-recovered">{row.label}</span>;

/** One recovered row of the canonical history table (Date, Game, Forecast, Result, Grade, Record, Model). */
export function RecoveredHistoryRow({ row }: { row: TgRow }) {
  const carried = useCarriedSearch();
  return (
    <tr className="row-recovered">
      <td data-label="Date">{calendarDate(row.game_date, FULL_DATE)}</td>
      <td data-label="Game"><span><Link to={`/games/${encodeURIComponent(row.game_id)}${carried}`}>{row.matchup}</Link> {tag(row)}</span></td>
      <td data-label="Original prediction"><span><CardSaid row={row} /></span></td>
      <td data-label="Result">{row.final_score ?? <span className="muted">No result stored</span>}</td>
      <td data-label="Grade"><Grade grade={row.grade} /></td>
      <td data-label="Record"><span><CardRecord row={row} /></span></td>
      <td data-label="Model"><span className="muted">Unknown (not on the card)</span></td>
    </tr>
  );
}

/** Combined, live and recovered totals of one scope; the overlap says how many are in both. */
export function RecoveredTotals({ s }: { s: TgSummary }) {
  const r = s.recovered;
  const both = s.overlap.same + s.overlap.earlier_live_version + s.overlap.different;
  return (
    <p className="small">
      <strong>All predictions, each game and market once: {recordText(s.combined)}</strong> ({s.combined.predictions} predictions).
      {' '}Live record: {recordText(s.live)}. {r && r.predictions === 0 && <>No recovered Telegram prediction in this selection.</>}{r && r.predictions > 0 && <>Recovered Telegram: {recordText(r)} ({r.predictions} predictions, {r.recovered_only} counted here; {both} also in the live record and counted there
      {s.overlap.earlier_live_version + s.overlap.different > 0 && <>, of which {s.overlap.earlier_live_version + s.overlap.different} show a different version</>}).
      {' '}All {r.unpriced} recovered predictions are unpriced.</>}
    </p>
  );
}

/** Where every source row went. Never hidden: included, duplicate, unmatched and unsettled are all stated. */
function Reconciliation({ d }: { d: TgReport }) {
  const c = d.reconciliation;
  if (!c) return null;
  const rows: [string, number][] = [
    ['Rows on Telegram cards in the export', c.source_rows],
    ['Earlier cards of the same game and market (listed as versions, not counted)', c.earlier_versions],
    ['Sent after the start (not predictions, never counted)', c.sent_after_start],
    ['Last card before the start, one per game and market', c.one_per_game],
    ['Imported into this history', c.included],
    ['Excluded', c.excluded], ['Unmatched to a stored game or market', c.unmatched], ['Not accounted for', c.unaccounted],
    ['Duplicate of the live record: same prediction', c.overlap.same],
    ['Duplicate game and market, earlier live version', c.overlap.earlier_live_version],
    ['Duplicate game and market, different from every live version', c.overlap.different],
    ['Counted only in the recovered record', c.counted_recovered],
    ['Unsettled', c.unsettled], ['Result disagrees with the stored final score', c.disagreements],
  ];
  return (
    <details className="small">
      <summary>Reconciliation of the {sportName(d.sport)} source rows (every market)</summary>
      <div className="table-wrap"><table className="table compare"><tbody>{rows.map(([k, n]) => <tr key={k}><th scope="row">{k}</th><td>{n}</td></tr>)}</tbody></table></div>
      {d.not_predictions.length > 0 && <ul>{d.not_predictions.map((x) => <li key={`${x.message_id}-${x.market_key}`}>
        {x.matchup}: {x.pick} {x.percentage}% · message {x.message_id}, sent {sentText(x.sent_at_local)}{x.reason === 'sent_after_start' ? ` after the ${dateTime(x.start_at)} start` : ': no card of record for this game and market'}</li>)}</ul>}
    </details>
  );
}

function ExactTable({ rows }: { rows: TgExact[] }) {
  if (rows.length === 0) return <Empty>No recovered prediction in this selection.</Empty>;
  return (
    <div className="table-wrap"><table className="table">
      <thead><tr><th>Exact %</th><th>Pick side</th><th>Line</th><th>All, counted once</th><th>Live</th><th>Recovered</th></tr></thead>
      <tbody>{rows.map((b) => (
        <tr key={`${b.market}|${b.percentage}|${b.side}|${b.line_source}`}>
          <th scope="row">{b.percentage}%</th><td>{b.direction}</td><td>{LINE[b.line_source] ?? b.line_source}</td>
          <td>{recordText(b.combined)}</td><td>{recordText(b.live)}</td>
          <td>{recordText(b.recovered)}{b.overlap > 0 && <span className="small muted"> · {b.overlap} also live</span>}</td>
        </tr>))}</tbody>
    </table></div>
  );
}

/** Every filter lives in the URL: the layout remounts the page when the URL changes, so component state would be lost. */
function useFilters() {
  const [params, setParams] = useSearchParams();
  const set = (k: string, v: string) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v); else next.delete(k);
    setParams(next);
  };
  return { from: params.get('date_from') ?? undefined, to: params.get('date_to') ?? undefined, market: params.get('tg_market') ?? '',
    everywhere: params.get('tg_scope') !== 'one', set };
}

function Filters({ d, market, pickMarket, from, to, set, everywhere, competitionName }: {
  d?: TgReport; market: string; pickMarket: boolean; from?: string; to?: string; set: (k: string, v: string) => void;
  everywhere: boolean; competitionName?: string;
}) {
  const options = d?.markets ?? [];
  return (
    <form className="filters" onSubmit={(e) => e.preventDefault()}>
      <label>From <input type="date" value={from ?? ''} onChange={(e) => set('date_from', e.target.value)} /></label>
      <label>To <input type="date" value={to ?? ''} onChange={(e) => set('date_to', e.target.value)} /></label>
      {pickMarket && <label>Market
        <select value={market} onChange={(e) => set('tg_market', e.target.value)}>
          <option value="">All markets</option>
          {options.map((m) => <option key={m.market} value={m.market}>{m.title}</option>)}
          {market && !options.some((m) => m.market === market) && <option value={market}>{market.replace(/_/g, ' ')}</option>}
        </select></label>}
      {competitionName && <label>Competition
        <select value={everywhere ? 'all' : 'one'} onChange={(e) => set('tg_scope', e.target.value === 'one' ? 'one' : '')}>
          <option value="all">All competitions</option><option value="one">{competitionName}</option>
        </select></label>}
    </form>
  );
}

const ORIGIN_LIVE = 'Live forecast';

/** Live and recovered predictions in one dated list (NFL and Soccer History): every row says where it comes from. */
function LedgerTable({ rows, live, titles, page = 200 }: { rows: TgRow[]; live: TgLive[]; titles: Record<string, string>; page?: number }) {
  const carried = useCarriedSearch();
  const all = mergeByStart(live, (l) => l.start_at, rows);
  const [limit, setLimit] = useState(page);
  if (all.length === 0) return <Empty>No prediction matches these filters.</Empty>;
  return <>
    <div className="table-wrap"><table className="table history">
      <thead><tr><th>Date</th><th>Game</th><th>Market</th><th>Original prediction</th><th>Result</th><th>Grade</th><th>Record</th></tr></thead>
      <tbody>{all.slice(0, limit).map((x) => x.kind === 'recovered' ? (
        <tr className="row-recovered" key={`r|${x.row.game_id}|${x.row.market}`}>
          <td data-label="Date">{calendarDate(x.row.game_date, FULL_DATE)}</td>
          <td data-label="Game"><span><Link to={`/games/${encodeURIComponent(x.row.game_id)}${carried}`}>{x.row.matchup}</Link> {tag(x.row)}</span></td>
          <td data-label="Market">{x.row.market_title}</td>
          <td data-label="Original prediction"><span><CardSaid row={x.row} /></span></td>
          <td data-label="Result">{x.row.final_score ?? <span className="muted">No result stored</span>}</td>
          <td data-label="Grade"><Grade grade={x.row.grade} /></td>
          <td data-label="Record"><span><CardRecord row={x.row} /></span></td>
        </tr>) : (
        <tr key={`l|${x.row.game_id}|${x.row.market}`}>
          <td data-label="Date">{calendarDate(x.row.game_date, FULL_DATE)}</td>
          <td data-label="Game"><span><Link to={`/games/${encodeURIComponent(x.row.game_id)}${carried}`}>{x.row.matchup}</Link> <span className="badge">{ORIGIN_LIVE}</span></span></td>
          <td data-label="Market">{titles[x.row.market] ?? x.row.market}</td>
          <td data-label="Prediction"><span><strong>{x.row.pick}</strong> {x.row.percentage}%</span></td>
          <td data-label="Result">{x.row.final_score ?? <span className="muted">No result yet</span>}</td>
          <td data-label="Grade"><Grade grade={x.row.grade} /></td>
          <td data-label="Record"><span>#{x.row.forecast_id} · made {dateTime(x.row.generated_at)} · <code className="wrap">{x.row.model_version}</code></span></td>
        </tr>))}</tbody>
    </table></div>
    {all.length > limit && <p><button type="button" className="link-btn" onClick={() => setLimit((n) => n + 200)}>Show {Math.min(200, all.length - limit)} more ({limit} of {all.length} shown)</button></p>}
  </>;
}

/** Prediction History for the leagues whose own history has no date filter or market choice (NFL, Soccer). */
export function RecoveredLedger({ league, competition, competitionName }: { league: League; competition?: string; competitionName?: string }) {
  const { from, to, market, everywhere, set } = useFilters();
  const rev = useDataRevision();
  const comp = everywhere ? undefined : competition;
  const { state, retry } = useApi((s) => api.telegramHistory(league, { market, date_from: from, date_to: to, competition: comp, live_rows: 'true' }, s), [league, market, from, to, comp], rev);
  const d = state.status === 'success' ? state.data : undefined;
  return (
    <div className="card" id="all-predictions">
      <h2>{league === 'soccer' ? 'Live and recovered predictions: match result, double chance, draw no bet' : 'All recorded predictions: live and recovered'}</h2>
      <Filters d={d} market={market} pickMarket from={from} to={to} set={set} everywhere={everywhere} competitionName={competition && competitionName} />
      <AsyncView state={state} retry={retry}>{(r) => <>
        <p className="small muted">{r.available ? `${r.note} Recovered: ${periodText(r)}.` : unavailableText(r.reason, sportName(r.sport))}</p>
        <RecoveredTotals s={r.summary} />
        <LedgerTable key={`${market}|${from}|${to}|${comp}`} rows={r.rows} live={r.live_rows} titles={Object.fromEntries(r.markets.map((m) => [m.market, m.title]))} />
        <Reconciliation d={r} />
      </>}</AsyncView>
    </div>
  );
}

/** Aggregate performance of the live and recovered predictions, each game and market once, with the exact-percentage records. */
export function RecoveredPerformance({ league, market: fixed, competition, competitionName }: { league: League; market?: string; competition?: string; competitionName?: string }) {
  const { from, to, market: chosen, everywhere, set } = useFilters();
  const rev = useDataRevision();
  const market = fixed ?? chosen;
  const comp = everywhere ? undefined : competition;
  const { state, retry } = useApi((s) => api.telegramHistory(league, { market, date_from: from, date_to: to, competition: comp }, s), [league, market, from, to, comp], rev);
  const d = state.status === 'success' ? state.data : undefined;
  return (
    <div className="card" id="all-predictions">
      <h2>{league === 'soccer' ? 'Live and recovered Telegram: match result, double chance, draw no bet' : 'All predictions: live and recovered Telegram'}</h2>
      <Filters d={d} market={market} pickMarket={!fixed} from={from} to={to} set={set} everywhere={everywhere} competitionName={competition && competitionName} />
      <AsyncView state={state} retry={retry}>{(r) => <>
        <p className="small muted">{r.available ? `${r.note} Recovered: ${periodText(r)}.` : unavailableText(r.reason, sportName(r.sport))}</p>
        {r.available && <>
          <RecoveredTotals s={r.summary} />
          <div className="table-wrap"><table className="table">
            <thead><tr><th>Market</th><th>All, counted once</th><th>Live</th><th>Recovered</th><th>In both</th></tr></thead>
            <tbody>
              {r.markets.map((m) => <tr key={m.market} className={m.market === market ? 'row-best' : undefined}><th scope="row">{m.title}</th>
                <td>{recordText(m.combined)}</td><td>{recordText(m.live)}</td><td>{recordText(m.recovered)}</td>
                <td>{m.overlap.same + m.overlap.earlier_live_version + m.overlap.different}
                  {m.overlap.earlier_live_version + m.overlap.different > 0 && <span className="small warn"> · {m.overlap.earlier_live_version + m.overlap.different} different version</span>}</td></tr>)}
              {r.markets.length > 1 && <tr><th scope="row">Every market</th>
                <td><strong>{recordText(addTallies(r.markets.map((m) => m.combined)))}</strong></td>
                <td>{recordText(addTallies(r.markets.map((m) => m.live)))}</td>
                <td>{recordText(addTallies(r.markets.flatMap((m) => (m.recovered ? [m.recovered] : []))))}</td>
                <td>{r.markets.reduce((n, m) => n + m.overlap.same + m.overlap.earlier_live_version + m.overlap.different, 0)}</td></tr>}
            </tbody>
          </table></div>
          <p className="small muted">Win rate = wins / (wins + losses); pushes and unsettled predictions are in no rate.
            {r.summary.combined.stated != null && <> The settled predictions of this selection were stated at {pct(r.summary.combined.stated, 1)} on average.</>}
            {' '}Markets mix easy and hard picks, so the every-market rate says little about any one of them.</p>
          <details open={Boolean(market)}><summary><strong>Exact percentages{market ? `: ${r.markets.find((m) => m.market === market)?.title ?? ''}` : ''}</strong> · every percentage a recovered card showed</summary>
            {market ? <ExactTable rows={r.exact_percentages} /> : r.markets.map((m) => <div key={m.market}><h3>{m.title}</h3><ExactTable rows={r.exact_percentages.filter((b) => b.market === m.market)} /></div>)}
            <p className="small muted">57.3% is not 57.4%. Live records join every live model version; a game in both records is counted once in the first column.</p>
          </details>
          <details open><summary><strong>Individual recovered predictions</strong> · {r.rows.length}</summary>
            <LedgerTable key={`${market}|${from}|${to}|${comp}`} rows={r.rows} live={[]} titles={{}} page={25} /></details>
          <Reconciliation d={r} />
        </>}
      </>}</AsyncView>
    </div>
  );
}

/** The totals above a History table (NBA/NHL/NCAAB canonical view), or why there are none. */
export function RecoveredHistoryNote({ d }: { d: TgReport }) {
  return <div className="notice" role="note">
    {d.available ? <><RecoveredTotals s={d.summary} /><p className="small muted">{d.note} Recovered: {periodText(d)}; the rows below carry the label “{d.label}”.</p><Reconciliation d={d} /></>
      : <p className="small">{unavailableText(d.reason, sportName(d.sport))}</p>}
  </div>;
}
