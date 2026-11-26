// How picks that were green, red or neutral BEFORE their start turned out (backend app/pick_colours.py). Colours are the saved
// ones; nothing here recomputes a colour or a grade. Every filter lives in the URL (the layout remounts the page on a change).
import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import { useDataRevision } from '../api/freshness';
import { useApi } from '../api/useApi';
import type { League, PcReport, PcRow, PcTally } from '../api/types';
import { american, calendarDate, dateTime, FULL_DATE, pct } from '../format';
import { AsyncView, Empty } from './States';
import { colourThenText, COLOUR_LABEL, recordOf, returnsText, sampleSuffix, supportText, tallyText, tallyWithSample, UNKNOWN_LABEL, VIEW_LABEL } from './pickColourText';
import { gradeLabel } from './recoveredHistory';

const SPORTS = ['nba', 'nhl', 'nfl', 'ncaab', 'soccer'];
const sportName = (s: string) => (s === 'all' ? 'All five sports' : s === 'soccer' ? 'Soccer' : s.toUpperCase());
const gradeClass = (g: string) => (g === 'win' ? 'ok' : g === 'loss' ? 'bad' : 'muted');
const PAGE = 100;

function Colour({ c }: { c: string }) {
  return <span className={`pick-colour pick-colour-${c}`}>{COLOUR_LABEL[c] ?? c}</span>;
}

function GroupRow({ label, t }: { label: React.ReactNode; t: PcTally }) {
  return <tr><th scope="row">{label}</th><td>{t.predictions} in {t.games} games</td><td>{tallyText(t)}{t.settled > 0 && <span className="small muted">{sampleSuffix(t.settled)}</span>}</td>
    <td>{t.ci95 ? `${pct(t.ci95[0], 0)}–${pct(t.ci95[1], 0)}` : '—'}</td><td>{returnsText(t)}</td></tr>;
}

function Detail({ r }: { r: PcRow }) {
  const s = r.support;
  return <details className="small"><summary>{supportText(r)}</summary>
    <ul>
      {s && <>
        <li>Saved {dateTime(s.observed_at)} under {s.colour_rule} · {s.basis === 'card_text' ? 'read from the saved Telegram card' : 'observed live'}{s.past_wins != null && <> · recomputed past seasons then: {recordOf(s.past_wins, s.past_losses)}</>}</li>
        <li>{s.price_american != null ? `Price then: ${american(s.price_american)} at ${s.price_provider}, stored ${s.price_retrieved_at ? dateTime(s.price_retrieved_at) : ''}` : `Unpriced: ${(s.price_reason ?? 'no price').replace(/_/g, ' ')}`}</li>
        {s.provenance && <li>Evidence: <code className="wrap">{s.provenance}</code></li>}
      </>}
      <li>Forecast #{r.forecast_id} · made {dateTime(r.generated_at)} · <code className="wrap">{r.model_version ?? 'model version not recorded'}</code></li>
      {r.sent_at && <li>Telegram card sent {dateTime(r.sent_at)} · message {r.message_id}</li>}
      {r.selection_differs && <li className="warn">The record’s current selection of this forecast differs; the saved side and line are the ones graded here.</li>}
      {r.versions.length > 0 && <li>Saved versions (all kept, one counted):<ul>{r.versions.map((v, i) => <li key={i}>
        {dateTime(v.observed_at)} · {v.event === 'sent' ? 'on a sent card' : `version ${v.version_no}`} · #{v.forecast_id}: {v.pick_label} {v.percentage}% · live {v.colour ? COLOUR_LABEL[v.colour] : 'no colour'} {recordOf(v.wins, v.losses)} · recovered {v.recovered_colour ? COLOUR_LABEL[v.recovered_colour] : 'none'}{v.recommended ? ' · recommended' : ''}</li>)}</ul></li>}
    </ul>
  </details>;
}

function Rows({ rows, record }: { rows: PcRow[]; record: string }) {
  if (rows.length === 0) return <Empty>No prediction matches these filters.</Empty>;
  return <div className="table-wrap"><table className="table history">
    <thead><tr><th>Date</th><th>Game</th><th>Market</th><th>Pick before the start</th><th>Colour then</th><th>Result</th><th>Grade</th><th>Support then</th></tr></thead>
    <tbody>{rows.map((r) => <tr key={`${r.sport}|${r.game_id}|${r.market}`}>
      <td data-label="Date">{calendarDate(r.game_date, FULL_DATE)}</td>
      <td data-label="Game"><span>{sportName(r.sport)} · {r.matchup ?? r.game_id}</span></td>
      <td data-label="Market">{r.market_title}</td>
      <td data-label="Pick"><span><strong>{r.pick ?? r.side}</strong> {r.percentage}%{r.recommended && <span className="badge"> Recommended</span>}</span></td>
      <td data-label="Colour then"><Colour c={record === 'live' ? r.colour : r.recovered_colour} />{colourThenText(r, record) && <><br /><span className="small muted">{colourThenText(r, record)}</span></>}</td>
      <td data-label="Result">{r.final_score ?? <span className="muted">No result yet</span>}</td>
      <td data-label="Grade"><span className={gradeClass(r.grade)}>{gradeLabel(r.grade)}</span></td>
      <td data-label="Support"><Detail r={r} /></td>
    </tr>)}</tbody>
  </table></div>;
}

function Reconciliation({ d }: { d: PcReport }) {
  const c = d.reconciliation;
  const lines: [string, React.ReactNode][] = [
    ['Started forecasts of record, one per game and market', `${c.live_forecasts.predictions}: ${tallyText(c.live_forecasts)}`],
    ['Colour saved or proven before the start', `${c.known_colour} (${c.observed_live} observed live, ${c.observed_card_text} proven by a saved card)`],
    ['Colour unknown', c.unknown_colour],
    ...Object.entries(c.unknown_reasons).map(([k, n]): [string, React.ReactNode] => [`Unknown: ${UNKNOWN_LABEL[k] ?? k}`, n]),
    ['Recovered Nov 2–16 cards without a live forecast (colour unknown)', `${c.recovered_only.predictions}: ${tallyText(c.recovered_only)}`],
    ['All predictions, each counted once', c.combined_predictions],
    ['Published picks in the Telegram ledger', c.sent_ledger_rows],
    ['Confirmed Telegram sends, last card before the start per game and market', `${c.sent}${c.sent_confirmed_by_export ? ` (${c.sent_confirmed_by_export} confirmed by the owner’s Telegram export)` : ''}`],
    ...Object.entries(c.sent_excluded).map(([k, n]): [string, React.ReactNode] => [`Sends not counted: ${k.replace(/_/g, ' ')}`, n]),
    ...Object.entries(c.sent_unknown_reasons).map(([k, n]): [string, React.ReactNode] => [`Sends with colour unknown: ${UNKNOWN_LABEL[k] ?? k}`, n]),
    ['Saved picks not started yet', c.upcoming_saved],
    ['Every group adds up', c.adds_up && c.sent_adds_up ? 'Yes' : 'NO'],
  ];
  return <details className="small"><summary>Reconciliation ({sportName(d.sport)}, every market and date; filters do not change it)</summary>
    <div className="table-wrap"><table className="table compare"><tbody>{lines.map(([k, v]) => <tr key={k}><th scope="row">{k}</th><td>{v}</td></tr>)}</tbody></table></div>
    <p className="small muted">{c.recovered_note}</p></details>;
}

function useFilters(league: League) {
  const [params, setParams] = useSearchParams();
  const set = (changes: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) if (v) next.set(k, v); else next.delete(k);
    setParams(next);
  };
  return { sport: params.get('pc_sport') ?? league, view: params.get('pc_view') ?? 'all', record: params.get('pc_record') ?? 'live', market: params.get('pc_market') ?? '',
    percentage: params.get('pc_pct') ?? '', colour: params.get('pc_colour') ?? '', from: params.get('date_from') ?? '', to: params.get('date_to') ?? '', set };
}

export function PickColours({ league }: { league: League }) {
  const f = useFilters(league);
  const rev = useDataRevision();
  const [shown, setShown] = useState(PAGE);
  const key = [f.sport, f.view, f.record, f.market, f.percentage, f.colour, f.from, f.to].join('|');
  useEffect(() => setShown(PAGE), [key]);
  const { state, retry } = useApi((s) => api.pickColours({ sport: f.sport, view: f.view, record: f.record, market: f.market, percentage: f.percentage, colour: f.colour,
    date_from: f.from, date_to: f.to, limit: String(Math.min(shown, 500)) }, s), [key, shown], rev);
  const d = state.status === 'success' ? state.data : undefined;
  const titles = new Map((d?.sports ?? []).flatMap((s) => s.markets.map((m) => [m.market, m.title] as const)));
  return (
    <div className="card" id="pick-colours">
      <h2>Green, red and neutral picks: colour before the start</h2>
      <form className="filters" onSubmit={(e) => e.preventDefault()}>
        <label>Sport <select value={f.sport} onChange={(e) => f.set({ pc_sport: e.target.value, pc_market: '' })}>{['all', ...SPORTS].map((s) => <option key={s} value={s}>{sportName(s)}</option>)}</select></label>
        <label>Picks <select value={f.view} onChange={(e) => f.set({ pc_view: e.target.value })}>{Object.entries(VIEW_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
        <label>Colour from <select value={f.record} onChange={(e) => f.set({ pc_record: e.target.value, pc_colour: '' })}><option value="live">Live record</option><option value="recovered">Recovered Nov 2–16 record</option></select></label>
        <label>Market <select value={f.market} onChange={(e) => f.set({ pc_market: e.target.value })}><option value="">All markets</option>
          {[...titles].map(([m, t]) => <option key={m} value={m}>{t}</option>)}{f.market && !titles.has(f.market) && <option value={f.market}>{f.market.replace(/_/g, ' ')}</option>}</select></label>
        <label>Exact % <select value={f.percentage} onChange={(e) => f.set({ pc_pct: e.target.value })}><option value="">Every %</option>
          {(d?.by_percentage ?? []).map((b) => <option key={b.percentage} value={b.percentage}>{b.percentage}%</option>)}{f.percentage && !d?.by_percentage.some((b) => b.percentage === f.percentage) && <option value={f.percentage}>{f.percentage}%</option>}</select></label>
        <label>From <input type="date" value={f.from} onChange={(e) => f.set({ date_from: e.target.value })} /></label>
        <label>To <input type="date" value={f.to} onChange={(e) => f.set({ date_to: e.target.value })} /></label>
      </form>
      <AsyncView state={state} retry={retry}>{(r) => <>
        <p className="small muted">{r.note}</p>
        <p className="small muted">{r.legend} {r.tracking_start ? `Colours saved live since ${dateTime(r.tracking_start)}.` : 'No colour has been saved live yet.'}
          {r.backfilled_from && ` Earlier colours back to ${dateTime(r.backfilled_from)} come only from saved Telegram cards that prove them.`}</p>
        {r.sports.filter((s) => s.status !== 'ok').map((s) => <p className="small warn" key={s.sport}>{sportName(s.sport)}: {s.status.replace(/_/g, ' ')} — not counted below.</p>)}
        <h3>{VIEW_LABEL[r.view]} · {r.record === 'live' ? 'live record' : 'recovered Nov 2–16 record'}</h3>
        <div className="table-wrap"><table className="table">
          <thead><tr><th>Colour before the start</th><th>Sample</th><th>Result</th><th>95% range</th><th>Flat stake, priced picks only</th></tr></thead>
          <tbody>{Object.entries(r.summary[r.record as 'live' | 'recovered']).map(([c, t]) => <GroupRow key={c} label={<Colour c={c} />} t={t} />)}
            <GroupRow label={<strong>Every pick</strong>} t={r.summary.total} /></tbody>
        </table></div>
        <p className="small muted">Win rate = wins / (wins + losses). Markets mix easy and hard picks and picks of one game move together, so compare colours within one market. {Object.entries(r.views).map(([v, t]) => `${VIEW_LABEL[v]}: ${t.predictions}`).join(' · ')}.</p>
        <details><summary><strong>By market</strong> · {r.by_market.length}</summary><div className="table-wrap"><table className="table">
          <thead><tr><th>Market</th><th>Green</th><th>Red</th><th>Neutral</th><th>Every pick</th></tr></thead>
          <tbody>{r.by_market.map((m) => <tr key={`${m.sport}|${m.market}`}><th scope="row">{r.sport === 'all' && `${sportName(m.sport)} · `}{m.title}</th><td>{tallyWithSample(m.groups.green)}</td><td>{tallyWithSample(m.groups.red)}</td><td>{tallyWithSample(m.groups.neutral)}</td><td>{tallyWithSample(m.all)}</td></tr>)}</tbody></table></div></details>
        <details><summary><strong>By exact %</strong> · {r.by_percentage.length}</summary><div className="table-wrap"><table className="table">
          <thead><tr><th>Exact %</th><th>Green</th><th>Red</th><th>Neutral</th><th>Every pick</th></tr></thead>
          <tbody>{r.by_percentage.map((b) => <tr key={b.percentage}><th scope="row">{b.percentage}%</th><td>{tallyWithSample(b.groups.green)}</td><td>{tallyWithSample(b.groups.red)}</td><td>{tallyWithSample(b.groups.neutral)}</td><td>{tallyWithSample(b.all)}</td></tr>)}</tbody></table></div>
          <p className="small muted">57.3% is not 57.4%. Most exact percentages hold one or two picks: too few to judge.</p></details>
        <h3>Individual picks · {r.rows_total}</h3>
        <label className="small">Show <select value={f.colour} onChange={(e) => f.set({ pc_colour: e.target.value })}><option value="">Every colour</option>
          {Object.keys(r.summary[r.record as 'live' | 'recovered']).map((c) => <option key={c} value={c}>{COLOUR_LABEL[c]}</option>)}</select></label>
        <Rows rows={r.rows} record={r.record} />
        {r.rows_total > r.rows.length && (r.rows.length < 500 ? <p><button type="button" className="link-btn" onClick={() => setShown((n) => n + PAGE)}>Show more ({r.rows.length} of {r.rows_total} shown)</button></p>
          : <p className="small muted">The first 500 of {r.rows_total} are shown; narrow the filters to see the others.</p>)}
        <Reconciliation d={r} />
      </>}</AsyncView>
    </div>
  );
}
