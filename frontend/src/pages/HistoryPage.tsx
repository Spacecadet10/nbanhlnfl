import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import type { PredictionRecord } from '../api/types';
import { useApi } from '../api/useApi';
import { RunBadge } from '../components/Badges';
import { ConfidenceFilter, levelLabel, RecordLine, useConfidence } from '../components/ConfidenceFilter';
import { PolicyToggle, RunTypeToggle, usePolicy, useRunType } from '../components/RunTypeToggle';
import { AsyncView, Empty } from '../components/States';
import { calendarDate, dateTime, FULL_DATE, pct } from '../format';

export function HistoryPage() {
  const [params, setParams] = useSearchParams();
  const filters = {
    date_from: params.get('date_from') ?? undefined,
    date_to: params.get('date_to') ?? undefined,
    team: params.get('team') ?? undefined,
  };
  const [run, setRun] = useRunType();
  const [level, setLevel] = useConfidence();
  const [policyChoice, setPolicy] = usePolicy();
  const policy = run === 'live' ? policyChoice : 'pregame';
  const meta = useApi((s) => api.meta(s), []);
  const realMode = meta.state.status === 'success' && meta.state.data.mode === 'real';
  const { state, retry } = useApi((s) => api.predictions({ ...filters, run_type: run, min_confidence: level, policy }, s),
    [filters.date_from, filters.date_to, filters.team, run, level, policy]);

  const update = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    setParams(next);
  };
  const hasFilters = Boolean(filters.date_from || filters.date_to || filters.team);
  const clearFilters = () => {
    const next = new URLSearchParams();
    if (run !== 'live') next.set('run', run);
    if (params.get('conf')) next.set('conf', params.get('conf') as string);
    if (params.get('policy')) next.set('policy', params.get('policy') as string);
    setParams(next);
  };

  return (
    <section>
      <div className="page-head">
        <h1>Prediction history</h1>
        {realMode && <RunTypeToggle value={run} onChange={setRun} />}
      </div>
      <p className="muted small">
        {!realMode ? 'Demo predictions from synthetic fixtures.'
          : run === 'live' && policy === 'near_tipoff' ? 'Near-tipoff forecasts, each made before tipoff from the freshest stored results (latest version per game). Kept separate from the night-before record. Elo does not know about injuries or line-ups.'
          : run === 'live' ? 'Live forecasts, each recorded before its cutoff (latest version per game). Outcomes are added after the game.'
            : 'Retrospective backtest predictions of the primary model, computed after the fact. These are not forecasts recorded before the games.'}
      </p>
      {realMode && run === 'live' && <div className="policy-row"><PolicyToggle value={policy} onChange={setPolicy} /></div>}
      <ConfidenceFilter value={level} onChange={setLevel} />
      <form className="filters" onSubmit={(e) => e.preventDefault()}>
        <label>From <input type="date" value={filters.date_from ?? ''} onChange={(e) => update('date_from', e.target.value)} /></label>
        <label>To <input type="date" value={filters.date_to ?? ''} onChange={(e) => update('date_to', e.target.value)} /></label>
        <label>Team
          <select value={filters.team ?? ''} onChange={(e) => update('team', e.target.value)} disabled={meta.state.status !== 'success'}>
            <option value="">All teams</option>
            {meta.state.status === 'success' && meta.state.data.teams.map((t) => <option key={t.abbr} value={t.abbr}>{t.name}</option>)}
          </select>
        </label>
        {hasFilters && <button type="button" className="link-btn" onClick={clearFilters}>Clear</button>}
      </form>
      {meta.state.status === 'error' && (
        <p className="warn small">
          Team list unavailable: {meta.state.error}{' '}
          <button type="button" className="link-btn" onClick={meta.retry}>Retry</button>
        </p>
      )}
      <AsyncView state={state} retry={retry}>
        {(data) => data.predictions.length === 0
          ? <Empty>
              {level != null
                ? <>No {data.run_type === 'demo' ? 'demo' : data.policy === 'near_tipoff' ? 'near-tipoff' : data.run_type} picks at {levelLabel(level)} model confidence{hasFilters ? ' match these filters' : ''}.{' '}
                    <button type="button" className="link-btn" onClick={() => setLevel(null)}>Show all picks</button></>
                : hasFilters ? 'No predictions match these filters.'
                  : data.policy === 'near_tipoff' ? 'No near-tipoff forecasts recorded yet. They are made with the predict-now command for games starting within 24 hours.'
                  : data.run_type === 'live' ? 'No live forecasts recorded yet. The daily job records them within 7 days of each regular-season or playoff game.'
                    : 'No predictions recorded.'}
            </Empty>
          : <>
              <RecordLine record={data.record} runType={data.run_type} />
              <p className="small muted">
                Model confidence is the forecast probability for the predicted winner; the observed win rate counts settled games only.
                {data.total > data.predictions.length && ` Showing the latest ${data.predictions.length} of ${data.total}; the record covers all ${data.total}.`}
              </p>
              <HistoryTable rows={data.predictions} />
            </>}
      </AsyncView>
    </section>
  );
}

function HistoryTable({ rows }: { rows: PredictionRecord[] }) {
  return (
    <table className="table history">
      <thead>
        <tr><th>Date</th><th>Game</th><th>Pick</th><th>Model confidence</th><th>Result</th><th>Outcome of pick</th><th>Recorded / computed</th><th>Model</th></tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.game_id}>
            <td data-label="Date">{calendarDate(r.game_date, FULL_DATE)}</td>
            <td data-label="Game"><Link to={`/games/${r.game_id}`}>{r.away.abbr} @ {r.home.abbr}</Link> <RunBadge runType={r.run_type} /></td>
            <td data-label="Pick"><strong>{r.pick === 'home' ? r.home.abbr : r.away.abbr}</strong> <span className="muted small">({r.pick})</span></td>
            <td data-label="Model confidence">{pct(r.confidence, 1)}</td>
            <td data-label="Result">{r.outcome ? `${r.away.abbr} ${r.outcome.away_score} – ${r.outcome.home_score} ${r.home.abbr}` : <span className="muted">Pending</span>}</td>
            <td data-label="Outcome of pick">
              {r.correct == null ? <span className="muted">—</span> : r.correct ? <span className="ok">Correct</span> : <span className="bad">Wrong</span>}
            </td>
            <td data-label="Recorded / computed">{dateTime(r.generated_at)}</td>
            <td data-label="Model"><code>{r.model_version}</code></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
