import { api } from '../api/client';
import { useApi } from '../api/useApi';
import { AsyncView } from './States';
import { dateTime } from '../format';
import { COLUMNS, cellLabel, marketLabel } from './learningCoverage';

/** Operational facts from /api/learning/ops: what is installed and what the stored fit and job rows say. */
export function OperationsBlock({ sport }: { sport: string }) {
  const { state, retry } = useApi(s => api.learningOps(sport, s), [sport]);
  return <div className="card ops-block"><h3>Operations · {sport.toUpperCase()}</h3>
    <AsyncView state={state} retry={retry}>{d => d.mode === 'demo' ? <p>DEMO · {d.note}</p> : <>
      <p>{d.trainer?.installed
        ? <>Trainer <code>{d.trainer.label}</code> is installed (plist found; not proof it is loaded) · runs at {d.trainer.times.join(' and ')} local · {d.trainer.covers_sport ? `covers ${sport.toUpperCase()}` : d.trainer.reason}. <span className="small muted">{d.trainer.program_summary}</span></>
        : <>Trainer not scheduled: {d.trainer?.reason}</>}</p>
      <p>Daily recording configuration in <code>{d.recording?.job_label}</code>: {d.recording?.configured === null ? `not known (${d.recording.note})` : d.recording?.configured ? `${d.recording.env_var}=1 (configured)` : 'not set'}. <span className="small muted">{d.recording?.note}</span></p>
      {d.store && d.store !== 'read-only' && <p className="small muted">Store read: {d.store}</p>}
      {(d.fits ?? []).length === 0 ? <p>No adaptive fits recorded for {sport.toUpperCase()}.</p> : <div className="table-wrap"><table className="table"><thead><tr><th>Definition</th><th>Schedule</th><th>Latest combiner fit</th><th>Members (latest period)</th></tr></thead><tbody>{d.fits!.map(f => <tr key={f.definition}>
        <th><code>{f.definition.slice(0, 16)}</code></th><td>{f.combiner?.schedule ?? '—'}</td>
        <td>{f.combiner ? `#${f.combiner.fit_id} · ${f.combiner.status} · ${dateTime(f.combiner.finished_at)}` : 'none'}</td>
        <td>{Object.entries(f.members).map(([k, v]) => `${v} ${k}`).join(', ')}{f.skipped_reasons.length > 0 && <><br /><span className="small muted">Skipped: {f.skipped_reasons.join('; ')}</span></>}{f.failed_reasons.length > 0 && <><br /><span className="small muted">Failed: {f.failed_reasons.join('; ')}</span></>}</td>
      </tr>)}</tbody></table></div>}
      {d.deep_members === 'unverified' && <p className="notice">Deep members: unverified. {d.deep_members_note}</p>}
      <details><summary>Recent training-input runs ({d.job_runs?.length ?? 0})</summary>{(d.job_runs ?? []).map((r, i) => <p key={i}>{r.job} · {r.status} · {r.finished_at ? dateTime(r.finished_at) : 'running'}{r.deferred_by && <> · deferred <code>{r.deferred_by}</code></>}</p>)}<p className="small muted">{d.adaptive_training_job_note}</p></details>
      <p className="small muted">{d.note}</p>
    </>}</AsyncView></div>;
}

/** Sport x market matrix from /api/learning/coverage; every state is its own column. Scrolls inside its own container. */
export function LearningCoverage() {
  const { state, retry } = useApi(s => api.learningCoverage(s), []);
  return <section className="card coverage-matrix" id="learning-coverage"><h2>Learning coverage</h2>
    <AsyncView state={state} retry={retry}>{d => d.mode === 'demo' ? <p>DEMO · {(d as { note?: string }).note}</p> : <>
      <p className="small muted">{d.note}</p>
      <div className="table-wrap"><table className="table"><thead><tr><th>Sport</th><th>Market</th>{COLUMNS.map(c => <th key={c[0]}>{c[1]}</th>)}<th>Blocked / limitation</th></tr></thead><tbody>{d.rows.map(r => <tr key={`${r.sport}:${r.market}`}>
        <th>{r.sport.toUpperCase()}</th><td>{marketLabel(r.market)}</td>
        {COLUMNS.map(c => <td key={c[0]} title={r[c[0]]?.detail}>{cellLabel(r[c[0]])}</td>)}
        <td>{r.blocked_reason.length ? r.blocked_reason.join(' · ') : '—'}</td>
      </tr>)}</tbody></table></div>
    </>}</AsyncView></section>;
}
