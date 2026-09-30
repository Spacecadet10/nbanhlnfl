import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import type { PredictionRecord } from '../api/types';
import { useApi } from '../api/useApi';
import { RunBadge } from '../components/Badges';
import { RunTypeToggle, useRunType } from '../components/RunTypeToggle';
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
  const meta = useApi((s) => api.meta(s), []);
  const realMode = meta.state.status === 'success' && meta.state.data.mode === 'real';
  const { state, retry } = useApi((s) => api.predictions({ ...filters, run_type: run }, s),
    [filters.date_from, filters.date_to, filters.team, run]);

  const update = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    setParams(next);
  };
  const hasFilters = Boolean(filters.date_from || filters.date_to || filters.team);
  const clearFilters = () => setParams(run === 'live' ? {} : { run });

  return (
    <section>
      <div className="page-head">
        <h1>Prediction history</h1>
        {realMode && <RunTypeToggle value={run} onChange={setRun} />}
      </div>
      <p className="muted small">
        {!realMode ? 'Demo predictions from synthetic fixtures.'
          : run === 'live' ? 'Live forecasts, each recorded before its cutoff (latest version per game). Outcomes are added after the game.'
            : 'Retrospective backtest predictions of the primary model, computed after the fact. These are not forecasts recorded before the games.'}
      </p>
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
          ? <Empty>{hasFilters ? 'No predictions match these filters.'
              : data.run_type === 'live' ? 'No live forecasts recorded yet. The daily job records them within 7 days of each regular-season or playoff game.'
                : 'No predictions recorded.'}</Empty>
          : <>
              <p className="small muted">
                {data.total} predictions{data.total > data.predictions.length && ` (showing the latest ${data.predictions.length})`} <RunBadge runType={data.run_type} />
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
        <tr><th>Date</th><th>Game</th><th>Home win prob.</th><th>Result</th><th>Pick</th><th>Recorded / computed</th><th>Model</th></tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.game_id}>
            <td data-label="Date">{calendarDate(r.game_date, FULL_DATE)}</td>
            <td data-label="Game"><Link to={`/games/${r.game_id}`}>{r.away.abbr} @ {r.home.abbr}</Link> <RunBadge runType={r.run_type} /></td>
            <td data-label="Home win prob.">{pct(r.home_win_prob)}</td>
            <td data-label="Result">{r.outcome ? `${r.away.abbr} ${r.outcome.away_score} – ${r.outcome.home_score} ${r.home.abbr}` : <span className="muted">Pending</span>}</td>
            <td data-label="Pick">
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
