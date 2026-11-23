import { api } from '../api/client';
import { useApi } from '../api/useApi';
import type { AutoresearchOutcome, AutoresearchSport, AutoresearchStatus, AutoresearchV3, AutoresearchV3Sport } from '../api/types';
import { AsyncView } from './States';
import { dateTime, num } from '../format';

const MARKET: Record<string, string> = { winner: 'Winner', reg3: 'Regulation three-way', spread: 'Spread / puck line', total: 'Total', team_total_home: 'Home team total', team_total_away: 'Away team total' };
const signed = (x?: number | null) => (x == null ? '—' : `${x > 0 ? '+' : ''}${x.toFixed(5)}`);
const OUTCOME: Record<AutoresearchOutcome, string> = { evaluated: 'Evaluated', no_candidates: 'No candidates (nothing was searched)', refused: 'Refused', failed: 'Failed', skipped: 'Skipped (budget or time)' };
const SPORT_NAME: Record<string, string> = { nba: 'NBA', nhl: 'NHL', nfl: 'NFL', soccer: 'Soccer', ncaab: 'NCAAB' };
const DECISION: Record<string, string> = { needs_human_review: 'Needs human review (reference line)', suspect_invalid: 'Suspect: forecasts at probability 0 or 1 (never kept)', keep: 'Kept (exploratory)', reject: 'Rejected', discard: 'Rejected', crash: 'Crashed', timeout: 'Timed out', invalid: 'Invalid', interrupted: 'Interrupted' };

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
    {d.audit?.error && <p role="alert">The audit file is unusable, so no research record may be used: {d.audit.error}</p>}
    {d.audit && d.audit.entries.length > 0 && <details className="card" open><summary><strong>Research audit</strong> · {d.audit.entries.length} {d.audit.entries.length === 1 ? 'entry' : 'entries'} (append-only)</summary>
      <p className="small muted">An invalid record stays stored but is never used: not as the model to beat, not as a reused result, not in a holdout or forward test. An unverified one waits for a recomputation under the intended code.</p>
      <div className="table-wrap"><table className="table"><thead><tr><th>When</th><th>Status</th><th>Record</th><th>Why</th></tr></thead><tbody>
        {d.audit.entries.map((e, i) => <tr key={i}><td>{dateTime(e.at)}</td><td>{e.status}</td><td>{SPORT_NAME[e.sport] ?? e.sport} <code>{e.protocol_id}</code>{e.trial_id ? <> trial <code>{e.trial_id}</code></> : ' (every record)'}</td><td>{e.reason}</td></tr>)}
      </tbody></table></div></details>}
    <p>{run ? <>Agent run <code>{run.run_id}</code> {run.driver_alive ? 'is running' : 'has no live driver (trials refused until a person clears it)'} · started {run.started_at ? dateTime(run.started_at) : '—'} · deadline {run.deadline ? dateTime(new Date(run.deadline * 1000).toISOString()) : '—'} · at most {run.max_trials} trials</> : 'No agent run in progress.'}</p>
    <h3>Agent runs</h3>
    {(d.agent_runs ?? []).length === 0 ? <p>No agent run recorded yet.</p> : <div className="table-wrap"><table className="table"><thead><tr><th>Run</th><th>Started / ended</th><th>Budget</th><th>Ended because</th><th>Outcome</th><th>Trials</th><th>Cost</th></tr></thead><tbody>
      {d.agent_runs!.map(r => <tr key={r.run_id}><td><code>{r.run_id}</code></td><td>{r.started_at ? dateTime(r.started_at) : '—'} / {r.finished_at ? dateTime(r.finished_at) : 'not ended'}</td>
        <td>{r.budget ? `${r.budget.max_trials} trials, ${r.budget.max_minutes} min, $${r.budget.max_usd}` : '—'}</td><td>{r.end === 'abandoned' ? `abandoned: ${r.reason}` : r.stopped ?? '—'}</td>
        <td>{r.outcome ?? '—'}</td><td>{r.trials ?? '—'}</td><td>{r.cost_usd == null ? (r.end ? 'unknown' : '—') : `$${r.cost_usd.toFixed(2)}`}{r.corrections?.map((c, i) => <div key={i} className="small muted">Corrected: {c.reason}</div>)}</td></tr>)}
    </tbody></table></div>}
    {d.last_daily && <p>Last evaluation sweep: {d.last_daily.finished_at ? dateTime(d.last_daily.finished_at) : '—'} · {d.last_daily.outcome} · {d.last_daily.stopped}</p>}
    {d.v3 && <V3Research v={d.v3} />}
    {Object.entries(d.sports ?? {}).map(([sport, s]) => <SportResearch key={sport} sport={sport} s={s} />)}
  </>;
}

/** Five-sport research (protocol v3): per sport the explicit outcome of the last attempt, the last successful evaluation,
 *  candidates, evaluated markets, blockers and the next run. Research only: a keep is exploratory, never a promotion. */
function V3Research({ v }: { v: AutoresearchV3 }) {
  return <section aria-labelledby="ar-v3-h"><h3 id="ar-v3-h">All sports ({v.protocol})</h3>
    <p className="notice">{v.note}</p>
    {v.last_daily?.combined && <p className="small">Per sport, protocol v3 and the v2 agent queue: {Object.entries(v.last_daily.combined).map(([s, c]) => `${SPORT_NAME[s] ?? s}: ${OUTCOME[c.outcome] ?? c.outcome} (v3 ${c.v3.outcome ?? '—'}${c.v2.outcome ? `, v2 ${c.v2.outcome}` : ''})`).join(' · ')}</p>}
    {v.wrapper_state && v.wrapper_state.length > 0 && <p className="small muted">Scheduled wrapper: {v.wrapper_state.map(w => `${SPORT_NAME[w.sport] ?? w.sport} ${w.last_outcome} at ${w.last_attempt_utc}`).join(' · ')}</p>}
    {v.last_daily && <p>Last daily run: {v.last_daily.finished_at ? dateTime(v.last_daily.finished_at) : '—'} · {Object.entries(v.last_daily.outcomes ?? {}).map(([s, o]) => `${SPORT_NAME[s] ?? s}: ${OUTCOME[o] ?? o}`).join(' · ')}</p>}
    <div className="table-wrap"><table className="table"><thead><tr><th>Sport</th><th>Last attempt</th><th>Last successful evaluation</th><th>Candidates</th><th>Evaluated markets</th><th>Blockers</th><th>Next run</th></tr></thead><tbody>
      {Object.entries(v.sports).map(([sport, s]) => <tr key={sport}><th>{SPORT_NAME[sport] ?? sport}{s.audit ? <div className="small" role="alert">{s.audit}</div> : null}</th>
        <td>{s.last_attempt ? <>{OUTCOME[s.last_attempt.outcome] ?? s.last_attempt.outcome}<br /><span className="small muted">{dateTime(s.last_attempt.at)}{s.last_attempt.reason ? ` · ${s.last_attempt.reason}` : ''}</span></> : '—'}</td>
        <td>{s.last_success_at ? dateTime(s.last_success_at) : 'never'}</td>
        <td>{s.candidate_count}{s.queue ? <span className="small muted"> · queue {s.queue.done}/{s.queue.total}{s.queue.remaining ? `, ${s.queue.remaining} waiting` : ''}</span> : null}</td>
        <td>{s.evaluated_markets.length ? s.evaluated_markets.map(m => MARKET[m] ?? m).join(', ') : 'none'}</td>
        <td>{s.blockers.length ? <ul className="small">{s.blockers.map((b, i) => <li key={i}>{b}</li>)}</ul> : 'none'}</td>
        <td>{s.next_run ?? '—'}{s.data_cutoff?.last_final ? <div className="small muted">data through {dateTime(s.data_cutoff.last_final)}</div> : null}</td></tr>)}
    </tbody></table></div>
    {v.forward && !('error' in v.forward) && <><h4>Forward test (games still ahead; research store, never official)</h4>
      <div className="table-wrap"><table className="table"><thead><tr><th>Sport</th><th>Candidate recording</th><th>Grading</th><th>Candidate forecasts</th><th>Current-model forecasts</th><th>Graded games</th><th>Graded items with both a candidate and the current model (raw stored rows; the comparison uses the pairing rule)</th><th>Last record / grade</th></tr></thead><tbody>
        {Object.entries(v.forward).map(([sport, f]) => <tr key={sport}><th>{SPORT_NAME[sport] ?? sport}</th>
          <td>{f.label ?? f.status ?? '—'}{f.suspect_models && Object.keys(f.suspect_models).length > 0 ? <div className="small" role="alert">Suspect (forecasts at probability 0 or 1, never recommended until diagnosed): {Object.entries(f.suspect_models).map(([m, n]) => `${m} (${n})`).join(', ')}</div> : null}</td>
          <td>{f.grading ?? '—'}</td>
          <td>{f.candidate_forecasts ? `${f.candidate_forecasts.rows} (${f.candidate_forecasts.models} models, ${f.candidate_forecasts.games} games)` : 'none'}</td>
          <td>{f.incumbent_forecasts ? `${f.incumbent_forecasts.rows} (${f.incumbent_forecasts.models} models, ${f.incumbent_forecasts.games} games)` : 'none'}</td>
          <td>{f.graded_games ?? 0}</td><td>{f.paired_graded_items ?? 0}</td>
          <td className="small muted">{f.last_record ? dateTime(f.last_record.at) : '—'} / {f.last_grade ? dateTime(f.last_grade.at) : '—'}</td></tr>)}
      </tbody></table></div></>}
    {Object.entries(v.sports).filter(([, s]) => s.evaluation).map(([sport, s]) => <V3Detail key={sport} sport={sport} s={s} />)}
  </section>;
}

function V3Detail({ sport, s }: { sport: string; s: AutoresearchV3Sport }) {
  const rows = Object.entries(s.evaluation ?? {});
  return <details className="card"><summary><strong>{SPORT_NAME[sport] ?? sport}</strong> · {s.kept?.length ? `kept (exploratory): ${s.kept.join(', ')}` : 'no candidate kept'} · seasons search {s.periods?.search.join(', ')} / validation {s.periods?.validation.join(', ')} / holdout {s.periods?.holdout.join(', ')} (read once, never by daily runs)</summary>
    {s.periods_note && <p className="small muted">{s.periods_note}</p>}
    {s.caveats && <ul className="small muted">{(Array.isArray(s.caveats) ? s.caveats.map((c, i) => [String(i), c] as [string, string]) : Object.entries(s.caveats)).map(([k, c]) => <li key={k}>{Array.isArray(s.caveats) ? c : `${k}: ${c}`}</li>)}</ul>}
    {s.suspect && s.suspect.length > 0 && <p role="alert">Suspect, never kept or recommended until diagnosed (forecasts state certainty, probability 0 or 1): {s.suspect.join(', ')}.</p>}
    {s.needs_human_review && s.needs_human_review.length > 0 && <p role="note">Needs human review (graded at a reference line owned by a compared model, never tracked forward automatically): {s.needs_human_review.join(', ')}.</p>}
    {s.data_validation && !s.data_validation.ok && <p role="alert">Data validation: {s.data_validation.issues.join('; ')}</p>}
    <div className="table-wrap"><table className="table"><thead><tr><th>Market</th><th>Best existing model (log loss, ECE)</th><th>Games</th><th>Baselines (log loss)</th><th>Sportsbook</th><th>Candidates: decision, validation Δ [95%]</th></tr></thead><tbody>
      {rows.map(([m, e]) => <tr key={m}><th>{MARKET[m] ?? m}{s.markets?.[m]?.settlement ? <div className="small muted">{s.markets[m].settlement}</div> : null}{e.reference_line_note ? <div className="small" role="note">{e.reference_line_note}</div> : null}{e.by_line_source ? <div className="small muted">{Object.entries(e.by_line_source).map(([src, v]) => `${src}: n ${v.n}, log loss ${num(v.log_loss, 4)}`).join(' · ')}</div> : null}</th>
        {e.status !== 'evaluated' ? <td colSpan={5}>{e.status}: {e.reason}</td> : <>
          <td>{e.incumbent} ({num(e.log_loss, 5)}, {num(e.ece, 4)})</td><td>{e.n}</td>
          <td>{Object.entries(e.baselines ?? {}).map(([b, x]) => `${b.replace('baseline:', '')} ${num(x, 5)}`).join(' · ')}</td>
          <td>{e.vs_book === 'ok' ? 'compared' : 'unavailable (no valid quote history)'}</td>
          <td>{Object.keys(e.candidates ?? {}).length === 0 ? 'no candidates' : Object.entries(e.candidates ?? {}).map(([n, c]) => <div key={n}>{n}: {DECISION[c.decision] ?? c.decision}, {signed(c.validation_delta)}{c.validation_ci95 ? ` [${signed(c.validation_ci95[0])}, ${signed(c.validation_ci95[1])}]` : ''}</div>)}</td></>}
      </tr>)}
    </tbody></table></div>
    {s.unsupported && Object.keys(s.unsupported).length > 0 && <p className="small">Not evaluable yet: {Object.entries(s.unsupported).map(([m, why]) => `${m} (${why})`).join('; ')}.</p>}
  </details>;
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
        <td>{t.audit ? <div role="alert">{t.audit}</div> : null}{DECISION[t.status] ?? t.status}{t.cached_from ? ` (same as ${t.cached_from})` : ''}{t.reason && t.status !== 'keep' && t.status !== 'discard' ? <><br /><span className="small">{t.reason}</span></> : null}</td>
        <td>{Object.keys(t.markets).length === 0 ? '—' : Object.entries(t.markets).map(([m, x]) => <div key={m}>{MARKET[m] ?? m}: {DECISION[x.decision] ?? x.decision}, {signed(x.delta)}{x.ci95 ? ` [${signed(x.ci95[0])}, ${signed(x.ci95[1])}]` : ''}{x.forecast != null && x.cohort != null && x.forecast < x.cohort ? ` · only ${x.forecast}/${x.cohort} games` : ''}</div>)}</td>
        <td>{t.resources ? `${t.resources.wall_s ?? '—'} s · ${t.resources.max_rss_gb ?? '—'} GB${t.resources.timed_out ? ' · timed out' : ''}` : '—'}</td></tr>)}
    </tbody></table></div>}
  </details>;
}
