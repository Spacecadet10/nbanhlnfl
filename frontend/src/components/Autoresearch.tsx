import { api } from '../api/client';
import { useApi } from '../api/useApi';
import type { AutoresearchSport, AutoresearchStatus } from '../api/types';
import { AsyncView } from './States';
import { dateTime, num } from '../format';

const MARKET: Record<string, string> = { winner: 'Winner', reg3: 'Regulation three-way', spread: 'Spread / puck line', total: 'Total', team_total_home: 'Home team total', team_total_away: 'Away team total' };
const signed = (x?: number | null) => (x == null ? '—' : `${x > 0 ? '+' : ''}${x.toFixed(5)}`);
const DECISION: Record<string, string> = { keep: 'Kept (exploratory)', reject: 'Rejected', discard: 'Rejected', crash: 'Crashed', timeout: 'Timed out', invalid: 'Invalid', interrupted: 'Interrupted' };

/** Sports autoresearch: an agent proposes train.py changes, the fixed evaluator fits and scores them on validation seasons of a
 *  read-only snapshot, and every decision is logged. Research only: nothing shown here is approved or used for a forecast. */
export function AutoresearchCard() {
  const { state, retry } = useApi(s => api.autoresearch(s), []);
  return <section className="card" aria-labelledby="autoresearch-h"><h2 id="autoresearch-h">Autoresearch (research only)</h2>
    <button onClick={retry}>Refresh status</button>
    <AsyncView state={state} retry={retry}>{d => <AutoresearchBody d={d} />}</AsyncView></section>;
}

function AutoresearchBody({ d }: { d: AutoresearchStatus }) {
  if (!d.configured) return <><p>{d.mode === 'demo' ? 'DEMO · ' : ''}Not available: {d.reason}</p><p className="small muted">{d.note}</p></>;
  const run = d.active_run;
  return <>
    <p className="notice">{d.note}</p>
    {d.stopped && <p role="alert">Research is stopped: {d.stopped}. No trial or agent run starts until a person clears it.</p>}
    <p>{run ? <>Agent run <code>{run.run_id}</code> {run.driver_alive ? 'is running' : 'has no live driver (trials refused until a person clears it)'} · started {run.started_at ? dateTime(run.started_at) : '—'} · deadline {run.deadline ? dateTime(new Date(run.deadline * 1000).toISOString()) : '—'} · at most {run.max_trials} trials</> : 'No agent run in progress.'}</p>
    <h3>Agent runs</h3>
    {(d.agent_runs ?? []).length === 0 ? <p>No agent run recorded yet.</p> : <div className="table-wrap"><table className="table"><thead><tr><th>Run</th><th>Started / ended</th><th>Budget</th><th>Ended because</th><th>Outcome</th><th>Trials</th><th>Cost</th></tr></thead><tbody>
      {d.agent_runs!.map(r => <tr key={r.run_id}><td><code>{r.run_id}</code></td><td>{r.started_at ? dateTime(r.started_at) : '—'} / {r.finished_at ? dateTime(r.finished_at) : 'not ended'}</td>
        <td>{r.budget ? `${r.budget.max_trials} trials, ${r.budget.max_minutes} min, $${r.budget.max_usd}` : '—'}</td><td>{r.end === 'abandoned' ? `abandoned: ${r.reason}` : r.stopped ?? '—'}</td>
        <td>{r.outcome ?? '—'}</td><td>{r.trials ?? '—'}</td><td>{r.cost_usd == null ? (r.end ? 'unknown' : '—') : `$${r.cost_usd.toFixed(2)}`}{r.corrections?.map((c, i) => <div key={i} className="small muted">Corrected: {c.reason}</div>)}</td></tr>)}
    </tbody></table></div>}
    {d.last_daily && <p>Last evaluation sweep: {d.last_daily.finished_at ? dateTime(d.last_daily.finished_at) : '—'} · {d.last_daily.outcome} · {d.last_daily.stopped}</p>}
    {Object.entries(d.sports ?? {}).map(([sport, s]) => <SportResearch key={sport} sport={sport} s={s} />)}
  </>;
}

function SportResearch({ sport, s }: { sport: string; s: AutoresearchSport }) {
  return <details className="card" open><summary><strong>{sport.toUpperCase()}</strong> · {s.trials} trials · {s.failures} failed · protocol <code>{s.protocol_id}</code></summary>
    <p>Scored validation seasons {s.scored_seasons?.join(', ') ?? '—'}; holdout seasons {s.holdout_seasons?.join(', ') ?? '—'} ({s.holdout.length ? s.holdout.map(h => h.event).join(', ') : 'never accessed under this protocol'}). Trial outcomes: {Object.entries(s.by_status).map(([k, v]) => `${DECISION[k] ?? k} ${v}`).join(' · ') || 'none yet'}.{s.running.length > 0 && ` Running now: ${s.running.join(', ')}.`}{s.unreadable_lines > 0 && ` ${s.unreadable_lines} unreadable log line(s).`}</p>
    <div className="table-wrap"><table className="table"><thead><tr><th>Market</th><th>Games</th><th>Bar: best existing model (log loss)</th><th>Current incumbent</th></tr></thead><tbody>
      {Object.entries(s.bar).map(([m, b]) => <tr key={m}><th>{MARKET[m] ?? m}</th><td>{s.cohort?.[m] ?? '—'}</td><td>{b.member} ({num(b.log_loss, 5)})</td>
        <td>{s.incumbents[m]?.source === 'kept trial' ? `${s.incumbents[m].label}, Δ ${signed(s.incumbents[m].delta_vs_previous)}` : 'existing model (nothing kept)'}</td></tr>)}
    </tbody></table></div>
    <h3>Recent trials</h3>
    {s.recent.length === 0 ? <p>No trial yet.</p> : <div className="table-wrap"><table className="table"><thead><tr><th>Trial / proposal</th><th>Result</th><th>Per market: decision, log-loss Δ vs incumbent [95% interval]</th><th>Resources</th></tr></thead><tbody>
      {s.recent.map(t => <tr key={t.trial_id}><td><code>{t.commit}</code> {t.description || '—'}<br /><span className="small muted">{t.finished_at ? dateTime(t.finished_at) : ''}{t.agent_run ? ` · agent run ${t.agent_run}` : ' · manual'}</span></td>
        <td>{DECISION[t.status] ?? t.status}{t.cached_from ? ` (same as ${t.cached_from})` : ''}{t.reason && t.status !== 'keep' && t.status !== 'discard' ? <><br /><span className="small">{t.reason}</span></> : null}</td>
        <td>{Object.keys(t.markets).length === 0 ? '—' : Object.entries(t.markets).map(([m, x]) => <div key={m}>{MARKET[m] ?? m}: {DECISION[x.decision] ?? x.decision}, {signed(x.delta)}{x.ci95 ? ` [${signed(x.ci95[0])}, ${signed(x.ci95[1])}]` : ''}{x.forecast != null && x.cohort != null && x.forecast < x.cohort ? ` · only ${x.forecast}/${x.cohort} games` : ''}</div>)}</td>
        <td>{t.resources ? `${t.resources.wall_s ?? '—'} s · ${t.resources.max_rss_gb ?? '—'} GB${t.resources.timed_out ? ' · timed out' : ''}` : '—'}</td></tr>)}
    </tbody></table></div>}
  </details>;
}
