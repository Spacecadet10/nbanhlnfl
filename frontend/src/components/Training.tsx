// Training and update status per member (backend training_monitor). Reports only: nothing here refits or retunes.
import { api } from '../api/client';
import type { Sport, TrainingStatus } from '../api/types';
import { useApi } from '../api/useApi';
import { AsyncView, Empty } from './States';
import { modelName } from './Research';
import { dateTime } from '../format';

export function TrainingStatusCard({ sport }: { sport: Sport }) {
  const { state, retry } = useApi((s) => api.researchTraining(sport, s), [sport]);
  return <div className="card">
    <h2>Training and updates <span className="badge badge-research">Research</span></h2>
    <AsyncView state={state} retry={retry}>{(t) => <TrainingBody t={t} />}</AsyncView>
  </div>;
}

function TrainingBody({ t }: { t: TrainingStatus }) {
  if (t.mode === 'demo') return <Empty>{t.note}</Empty>;
  return <>
    <p className="small muted">{t.note}</p>
    <p className="small muted">Team inputs can update between weight refits. Research selection does not automatically replace the primary model.</p>
    <h3>Learned members (fit in force)</h3>
    {t.fits.length === 0 ? <Empty>No learned member has a registered fit in this store yet (fits are registered at their first two-hour forecast).</Empty>
      : <div className="table-wrap"><table className="table small">
        <thead><tr><th>Member</th><th>Schedule</th><th>Fit in force since</th><th>Training cutoff</th><th>New results since</th>
          <th>Next update</th><th>Versions</th></tr></thead>
        <tbody>{t.fits.map((f) => <tr key={f.member}><th scope="row" className="wrap">{modelName(f.member)}</th>
          <td className="wrap">{f.schedule}<div className="muted">{f.schedule_note}</div></td>
          <td>{dateTime(f.fit_in_force_since)}<div className="muted">{f.training_games} games, season {f.trained_season}</div></td>
          <td>{dateTime(f.results_available_through)}</td><td>{f.new_settled_results_since_fit}<div className="muted">{f.action}</div></td>
          <td className="wrap">{f.next_update.when ? dateTime(f.next_update.when) : <span className="muted">not scheduled yet</span>}<div className="muted">{f.next_update.rule}</div></td>
          <td className="wrap"><code>{f.versions.definition}</code> · params <code>{f.versions.params.slice(0, 10)}</code>
            <div className="muted">calibration: {f.versions.calibration}</div></td></tr>)}</tbody>
      </table></div>}
    <h3>Online members</h3>
    <ul className="small">{t.online.map((o) => <li key={o.member}><strong>{modelName(o.member)}</strong>: {o.schedule}.
      <div>Latest eligible result available: {o.latest_available_result ? dateTime(o.latest_available_result) : 'none'}.</div>
      <div>{o.consumption_verified ? <>Verified forecast: {dateTime(o.verified_at!)}; {o.results_used} results used,
        latest {o.last_update_input ? dateTime(o.last_update_input) : 'none'}.</>
        : 'Result consumption is not verified by per-model forecast provenance.'}</div>
      <div>Next state update: {o.next_update}.</div></li>)}</ul>
    <h3>Inputs</h3>
    <ul className="small">{Object.entries(t.inputs).map(([job, i]) => <li key={job} className={i.stale ? 'warn' : undefined}>
      {job}: last success {i.last_success ? dateTime(i.last_success) : 'never'}{i.stale ? ` — stale (older than ${i.stale_after_hours} h)` : ''}</li>)}</ul>
    <h3>Skipped and failed updates</h3>
    {t.updates.length === 0 ? <p className="small muted">None in the latest two-hour runs.</p>
      : <div className="table-wrap"><table className="table small">
        <thead><tr><th>When</th><th>Step</th><th>Member / game</th><th>Outcome</th><th>Reason</th></tr></thead>
        <tbody>{t.updates.slice(0, 30).map((u, i) => <tr key={`${u.run_id}-${i}`}><td>{dateTime(u.at)}</td><td>{u.step}</td>
          <td className="wrap">{u.member ? modelName(u.member) : '—'}</td><td className={u.outcome === 'failed' ? 'bad' : 'warn'}>{u.outcome}</td>
          <td className="wrap">{u.reason}</td></tr>)}</tbody>
      </table></div>}
    <h3>Monitoring</h3>
    {t.monitoring.alerts.length > 0 && <ul className="small">{t.monitoring.alerts.map((a) => <li key={`${a.market}-${a.member}`} className="bad">
      {a.market} · {modelName(a.member)}: {a.alert}</li>)}</ul>}
    {t.monitoring.members.length === 0 ? <p className="small muted">No settled two-hour research forecasts yet, so rolling loss cannot be monitored.</p>
      : <div className="table-wrap"><table className="table small">
        <thead><tr><th>Market</th><th>Member</th><th>Settled</th><th>Recent log loss</th><th>Previous</th><th>Missing rate</th><th>Status</th></tr></thead>
        <tbody>{t.monitoring.members.map((m) => <tr key={`${m.market}-${m.member}`}><td>{m.market}</td><th scope="row">{modelName(m.member)}</th>
          <td>{m.settled}</td><td>{m.recent_log_loss.toFixed(3)}</td><td>{m.previous_log_loss?.toFixed(3) ?? '—'}</td>
          <td>{(m.missing_rate * 100).toFixed(0)}%</td><td className="wrap muted">{m.status ?? (m.difference != null ? `Δ ${m.difference.toFixed(3)}` : '')}</td></tr>)}</tbody>
      </table></div>}
    <p className="small muted">{t.monitoring.note}</p>
  </>;
}
