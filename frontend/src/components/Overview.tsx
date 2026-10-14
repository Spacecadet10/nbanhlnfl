// Home-screen overview: results and training status for both sports at a glance. Reuses the existing scoring endpoints
// (/predictions record, /performance, /recovered-forecasts) and /training-run; nothing is computed or estimated here.
import { useEffect, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import type { ConfidenceRecord, Sport, TrainingRun, TrainingRunRecord } from '../api/types';
import { useApi, type ApiState } from '../api/useApi';
import { calendarDate, dateTime, FULL_DATE, pct } from '../format';
import { AsyncView, Loading } from './States';
import { canonicalFor, CanonicalRecordLine } from './Canonical';

const SPORTS: Sport[] = ['nba', 'nhl'];
const LABEL: Record<Sport, string> = { nba: 'NBA', nhl: 'NHL' };
const sportQuery = (s: Sport) => (s === 'nhl' ? '?sport=nhl' : '');
const range = (from: string | null, to: string | null) =>
  from && to ? (from === to ? calendarDate(from, FULL_DATE) : `${calendarDate(from, FULL_DATE)} – ${calendarDate(to, FULL_DATE)}`) : null;
const shortId = (s: string | null | undefined) => (s ? s.slice(0, 8) : '—');

export function Overview() {
  return (
    <section className="overview" aria-label="Overview">
      <div className="overview-head">
        <h1>Results so far</h1>
        <p className="muted small">Winner picks scored against final results. Only forecasts recorded before each game count toward the live record; reconstructed and historical results are listed separately.</p>
      </div>
      <div className="overview-grid">{SPORTS.map((s) => <SportResults key={s} sport={s} />)}</div>
      <div className="overview-head"><h2>Training</h2>
        <p className="muted small">Whether model training is running, what finished, and which models are making forecasts. Refreshes every minute.</p></div>
      <div className="overview-grid">{SPORTS.map((s) => <SportTraining key={s} sport={s} />)}</div>
    </section>
  );
}

function Line({ label, state, children }: { label: string; state: ApiState<unknown>; children: () => ReactNode }) {
  if (state.status === 'loading') return <li className="muted">{label}: loading…</li>;
  if (state.status === 'error') return <li className="warn">{label}: unavailable ({state.error})</li>;
  return <li>{children()}</li>;
}

const correctOf = (r: ConfidenceRecord) => `${r.wins} of ${r.settled} correct`;

function SportResults({ sport }: { sport: Sport }) {
  const meta = useApi((s) => api.meta(sport, s), [sport]);
  const live = useApi((s) => Promise.all([api.predictions(sport, { run_type: 'live', policy: 'near_tipoff', limit: 1 }, s),
    api.performance(sport, 'live', 'near_tipoff', s)]), [sport]);
  const archive = useApi((s) => Promise.all([api.predictions(sport, { run_type: 'live', policy: 'pregame', limit: 1 }, s),
    api.performance(sport, 'live', 'pregame', s)]), [sport]);
  const recovered = useApi((s) => api.recovered(sport, s), [sport]);
  const backtest = useApi((s) => api.performance(sport, 'backtest', 'pregame', s), [sport]);
  const demo = meta.state.status === 'success' && meta.state.data.mode === 'demo';
  return (
    <article className="card overview-card">
      <div className="overview-card-head">
        <h2>{LABEL[sport]}</h2>
        {demo ? <span className="badge badge-demo">Demo · synthetic</span> : <span className="badge badge-live">Live record</span>}
      </div>
      {!demo && canonicalFor(sport) && <><h3>Canonical forecast record</h3><CanonicalRecordLine sport={sport} /><h3>Legacy two-hour Elo ledger</h3></>}
      <AsyncView state={live.state} retry={live.retry}>{([hist, perf]) => {
        const r = hist.record;
        const dates = range(perf.date_from, perf.date_to);
        return <>
          {r.settled > 0
            ? <p className="overview-big"><strong>{pct(r.win_rate, 1)}</strong> <span>{correctOf(r)}</span></p>
            : <p className="overview-big"><strong>—</strong> <span>No settled {demo ? 'demo' : 'live'} forecasts yet</span></p>}
          <dl className="overview-stats">
            <div><dt>Settled</dt><dd>{r.settled}</dd></div>
            <div><dt>Correct</dt><dd>{r.wins}</dd></div>
            <div><dt>Accuracy</dt><dd>{pct(r.win_rate, 1)}</dd></div>
            <div><dt>Pending</dt><dd>{r.pending}</dd></div>
          </dl>
          <p className="small muted">
            {demo ? 'Synthetic demo forecasts, not real games.' : canonicalFor(sport) ? 'Legacy: the sealed Elo forecasts recorded in the two hours before each game; not the canonical record.' : 'Forecasts recorded in the two hours before each game (current policy).'}
            {' '}{dates ? <>Settled games: {dates}.</> : 'No settled games yet.'}
            {r.void > 0 && <> {r.void} void (game moved or postponed; never scored).</>}
            {r.settled > 0 && r.settled < 100 && <> Small sample ({r.settled} games): accuracy can move a lot with each result.</>}
          </p>
          {r.selected === 0 && !demo && meta.state.status === 'success' && (
            <p className="small notice">No live forecasts have been recorded for the {LABEL[sport]} yet. Forecasts are made only in
              the two hours before regular-season and playoff games (preseason is excluded). Next games on file: {calendarDate(meta.state.data.default_date, FULL_DATE)}.</p>
          )}
        </>;
      }}</AsyncView>
      {!demo && <ul className="overview-other small">
        <Line label="Older pre-game archive" state={archive.state}>{() => {
          if (archive.state.status !== 'success') return null;
          const [h, p] = archive.state.data;
          return h.record.settled === 0 ? <>Older pre-game archive: no settled forecasts.</>
            : <>Older pre-game archive (discontinued daily job, some made days ahead): {correctOf(h.record)} ({pct(h.record.win_rate, 1)}){range(p.date_from, p.date_to) ? `, ${range(p.date_from, p.date_to)}` : ''}.</>;
        }}</Line>
        <Line label="Reconstructed after the start" state={recovered.state}>{() => {
          if (recovered.state.status !== 'success') return null;
          const w = recovered.state.data.records.filter((x) => x.market === 'winner' && (x.outcome === 'win' || x.outcome === 'loss'));
          const wins = w.filter((x) => x.outcome === 'win').length;
          return w.length === 0 ? <>Reconstructed after the start (retrospective): none.</>
            : <>Reconstructed after the start (retrospective, not in the live record): {wins} of {w.length} winner picks correct.</>;
        }}</Line>
        <Line label="Historical backtest" state={backtest.state}>{() => {
          if (backtest.state.status !== 'success') return null;
          const b = backtest.state.data;
          return <>Historical backtest (retrospective, not live): {pct(b.accuracy, 1)} of {b.sample_size.toLocaleString()} games{range(b.date_from, b.date_to) ? `, ${range(b.date_from, b.date_to)}` : ''}.</>;
        }}</Line>
      </ul>}
      <p className="small overview-links">
        <Link to={`/history${sportQuery(sport)}`}>Every prediction →</Link>
        <Link to={`/performance${sportQuery(sport)}`}>Performance details →</Link>
      </p>
    </article>
  );
}

function runLine(r: TrainingRunRecord) {
  const failed = r.members_failed ? `, ${r.members_failed} failed` : '';
  const skipped = r.members_skipped ? `, ${r.members_skipped} skipped` : '';
  return `${dateTime(r.finished_at)} — ${r.status}: ${r.members_succeeded} models trained${failed}${skipped}`;
}

function SportTraining({ sport }: { sport: Sport }) {
  const { state, retry } = useApi((s) => api.trainingRun(sport, s), [sport]);
  useEffect(() => {
    const timer = window.setInterval(() => { if (document.visibilityState === 'visible') retry(); }, 60000);
    return () => window.clearInterval(timer);
  }, [retry]);
  return (
    <article className="card overview-card">
      <div className="overview-card-head"><h2>{LABEL[sport]} training</h2>
        <button type="button" className="link-btn" onClick={retry}>Refresh</button></div>
      {state.status === 'loading' ? <Loading label="Checking training status…" />
        : <AsyncView state={state} retry={retry}>{(t) => <TrainingBody t={t} />}</AsyncView>}
    </article>
  );
}

function TrainingBody({ t }: { t: TrainingRun }) {
  if (t.mode === 'demo') return <p className="small muted">{t.note}</p>;
  const cur = t.current_run;
  const codeDef = t.code_definition;
  const runDefs = cur ? Object.entries(cur.by_definition) : [];
  const serving = t.serving;
  return <>
    <p className="overview-state">
      {t.running === true && <><span className="dot dot-on" aria-hidden="true" /><strong>Training is running</strong></>}
      {t.running === false && <><span className="dot" aria-hidden="true" /><strong>Not running</strong></>}
      {t.running == null && <><span className="dot dot-unknown" aria-hidden="true" /><strong>Unknown</strong> <span className="small muted">{t.running_note}</span></>}
    </p>
    {cur && <ul className="small">
      <li>Started: {cur.started_at ? dateTime(cur.started_at) : 'start time not available'}</li>
      <li>Last progress: {cur.last_progress_at ? dateTime(cur.last_progress_at) : 'no model written yet'}</li>
      {runDefs.length === 0 ? <li>Models written in this run: 0{t.last_run ? ` (the last completed run trained ${t.last_run.members_succeeded})` : ''}</li>
        : runDefs.map(([d, a]) => <li key={d}>Models written in this run: {a.members_written}
          {d === codeDef && t.expected_members ? ` of ${t.expected_members}` : t.last_run ? ` (expected total not recorded for this version; the last completed run trained ${t.last_run.members_succeeded})` : ' (expected total not recorded for this version)'}
          {a.combiner_written ? '; combination written' : ''}</li>)}
    </ul>}
    <ul className="small">
      <li>Last completed run: {t.last_run ? runLine(t.last_run) : 'none recorded'}</li>
      {t.last_success && t.last_success.finished_at !== t.last_run?.finished_at && <li>Last successful run: {runLine(t.last_success)}</li>}
      <li className={t.last_failure ? 'warn' : undefined}>Last failure: {t.last_failure
        ? <>{dateTime(t.last_failure.at)} — {t.last_failure.name}: {t.last_failure.error ?? 'no reason recorded'}</> : 'none recorded'}</li>
      <li>Making forecasts now: {serving?.primary ? <>primary model, latest forecast {dateTime(serving.primary.last_forecast_at)}</> : 'no live primary forecast recorded yet'}
        {serving?.adaptive ? <>; research combination, latest forecast {dateTime(serving.adaptive.last_forecast_at)}</> : '; no research-combination forecast recorded yet'}.</li>
      {(t.not_serving ?? []).map((n) => <li key={n.definition}>Trained, not making forecasts: {n.members_written} models
        {n.state === 'written, not published' ? ' written to disk but not published' : ' published but not yet used for a live forecast'}
        {n.at ? ` (${dateTime(n.at)})` : ''}.</li>)}
    </ul>
    <p className="small muted">A finished training run does not mean better predictions; accuracy comes only from settled games (above).</p>
    <details className="small">
      <summary>Technical details</summary>
      <ul>
        <li>Version this development checkout would train: <code>{codeDef ?? 'unavailable'}</code>{t.expected_members ? ` (${t.expected_members} models)` : ''} — includes uncommitted edits; not necessarily what the trainer runs.</li>
        {(t.not_serving ?? []).map((n) => <li key={n.definition}>Not serving: <code>{n.definition}</code> — {n.state}</li>)}
        <li>Research combination in use: {(serving?.adaptive?.definitions ?? []).length > 0
          ? (serving?.adaptive?.definitions ?? []).map((d, i) => <span key={d.definition}>{i ? '; ' : ''}<code>{d.definition}</code> ({d.forecasts} forecasts{d.unavailable ? `, ${d.unavailable} unavailable` : ''})</span>)
          : <><code>{serving?.adaptive?.definition ?? '—'}</code>{serving?.adaptive ? ` (${serving.adaptive.forecasts} forecasts)` : ''}</>}</li>
        <li>Primary model version: <code className="wrap">{serving?.primary?.model_version ?? '—'}</code></li>
        {t.last_run && <li>Last run version <code>{shortId(t.last_run.definition)}</code>, period {t.last_run.period}, started {dateTime(t.last_run.started_at)}{t.last_run.error ? `; ${t.last_run.error}` : ''}</li>}
        {cur?.processes.map((p) => <li key={p.pid} className="wrap">Process {p.pid}: <code>{p.command ?? 'command unavailable'}</code></li>)}
        <li>{t.running_note}</li>
        <li className="wrap">Live data: <code>{t.live_data_dir}</code>; this app serves <code>{t.serving_data_dir}</code></li>
        <li>Checked {t.as_of ? dateTime(t.as_of) : '—'}. {t.note}</li>
      </ul>
    </details>
  </>;
}
