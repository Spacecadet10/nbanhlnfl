import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import type { SoccerForecast, SoccerHistMatch, SoccerModel, SoccerPerformanceRow, SoccerResearchMeta } from '../api/types';
import { useApi } from '../api/useApi';
import { RunBadge } from '../components/Badges';
import { useCarriedSearch, useCompetition } from '../components/Selection';
import { AsyncView, Empty } from '../components/States';
import { Icon, MetricCard, PageTitle } from '../components/ui';
import { calendarDate, dateTime, FULL_DATE } from '../format';
import { GameDateControl } from './NflPages';

// Soccer research screens (backend app/soccer_forecasts.py): openfootball (CC0) history with the stored, append-only
// regulation 1X2 research forecasts. Every probability shown is a stored row; every row is a retrospective backtest
// (computed long after the match) and is labelled so — never Live, never a pick or betting advice. Qualification, extra
// time and shoot-outs are facts of the match, not forecast outcomes.

const MODEL_LABEL: Record<SoccerModel, string> = { frequency: 'Outcome frequencies', elo: 'Draw-aware Elo', poisson: 'Poisson goals' };
const PERIOD_LABEL: Record<string, string> = {
  development_tune: 'Development (tuning)', development_calibration: 'Development (calibration)', validation: 'Validation',
  reserved_test: 'Reserved test', prospective: 'Prospective',
};
const COMPETITION_LABEL: Record<string, string> = {
  'eng.1': 'Premier League', 'esp.1': 'La Liga', 'ger.1': 'Bundesliga', 'ita.1': 'Serie A', 'fra.1': 'Ligue 1',
  'uefa.champions': 'UEFA Champions League', 'uefa.europa': 'UEFA Europa League',
};
const NO_FORECAST = 'No research forecast is stored for this match: it is not graded on the 90-minute result, or its season is a warm-up, a league\'s first covered season or outside the backtested periods (the reserved test seasons are locked).';
const pct = (p: number) => `${Math.round(p * 1000) / 10}%`;
const seasonLabel = (season: number) => `${season}–${String(season + 1).slice(2)}`;

export function ResearchNotice({ meta }: { meta: Pick<SoccerResearchMeta, 'notice'> }) {
  return (
    <p className="notice research-notice"><RunBadge runType="backtest" /> <strong>Research backtest.</strong> {meta.notice}</p>
  );
}

/** The row's own run type and label: research backtest rows get the Backtest badge; any other run type shows its label. */
function RowBadge({ f }: { f: SoccerForecast }) {
  return f.run_type === 'backtest' ? <RunBadge runType="backtest" /> : <span className="badge">{f.label}</span>;
}

/** Home / draw / away as one bar; widths are the stored probabilities. */
export function ThreeWayBar({ f, home, away }: { f: SoccerForecast; home: string; away: string }) {
  const parts = [['home', f.p_home, home], ['draw', f.p_draw, 'Draw'], ['away', f.p_away, away]] as const;
  return (
    <div className="threeway" role="img" aria-label={`${home} ${pct(f.p_home)}, draw ${pct(f.p_draw)}, ${away} ${pct(f.p_away)}`}>
      <div className="threeway-bar">
        {parts.map(([k, p]) => <span key={k} className={`threeway-${k}`} style={{ width: `${p * 100}%` }} />)}
      </div>
      <div className="threeway-legend small">
        {parts.map(([k, p, label]) => <span key={k}><i className={`dot threeway-${k}`} />{label} <b>{pct(p)}</b></span>)}
      </div>
    </div>
  );
}

function score(m: SoccerHistMatch) {
  if (m.ft_home == null) return null;
  const extra = m.aet_home != null ? ` · ${m.aet_home}–${m.aet_away} aet` : '';
  const pens = m.pen_home != null ? ` · pens ${m.pen_home}–${m.pen_away}` : '';
  return `${m.ft_home}–${m.ft_away}${extra}${pens}`;
}

function resultText(m: SoccerHistMatch) {
  if (m.regulation_result === 'draw') return 'Draw after 90 minutes';
  if (m.regulation_result) return `${m.regulation_result === 'home' ? m.home.name : m.away.name} won after 90 minutes`;
  return m.grading_note ?? 'Not graded';
}

function timeText(m: SoccerHistMatch) {
  if (!m.local_time) return 'time not listed';
  return `${m.local_time} local${m.local_time_listed === false ? ' (time as listed for the line above)' : ''}`;
}

function ForecastMeta({ f }: { f: SoccerForecast }) {
  return (
    <span className="small muted">
      <RowBadge f={f} /> {f.label} · {MODEL_LABEL[f.model]}{f.is_selected_candidate ? ' · selected candidate' : ''}
      {f.calibrated ? ' · calibrated' : ''} · {PERIOD_LABEL[f.period] ?? f.period} · information cutoff {dateTime(f.cutoff_at)}
      {' '}· computed {dateTime(f.computed_at)}{f.version > 1 ? ` · version ${f.version}` : ''}
      {' '}· {f.published_at ? `published ${dateTime(f.published_at)}` : 'never published as live'}
    </span>
  );
}

// --- Game day -------------------------------------------------------------------------------------------------------
export function ResearchGameDay({ meta }: { meta: SoccerResearchMeta }) {
  const [params, setParams] = useSearchParams();
  const dates = meta.forecast_dates;
  const asked = params.get('date');
  const date = asked && dates.includes(asked) ? asked : dates[dates.length - 1] ?? null;
  const setDate = (d: string | null) => {
    if (!d) return;
    const next = new URLSearchParams(params);
    next.set('date', d);
    setParams(next);
  };
  return (
    <section>
      <PageTitle icon="ball" plain title="Soccer research forecasts" badge={<RunBadge runType="backtest" />}
        sub="openfootball (CC0) results; dates as listed (local, zone not stated). Retrospective research only."
        right={date && <GameDateControl date={date} dates={dates} defaultDate={dates[dates.length - 1] ?? null} onChange={setDate} />} />
      <ResearchNotice meta={meta} />
      {asked && asked !== date && date && <p className="notice small">No research forecast is stored for {COMPETITION_LABEL[meta.competition_id]} on {asked}; showing {calendarDate(date, FULL_DATE)}, the latest date with one.</p>}
      {date ? <ResearchSlate competition={meta.competition_id} date={date} />
        : <Empty>No research forecast is stored for this competition. Forecasts exist only for development and validation
          seasons with a model run recorded (`soccer eval-record`).</Empty>}
    </section>
  );
}

function ResearchSlate({ competition, date }: { competition: SoccerResearchMeta['competition_id']; date: string }) {
  const { state, retry } = useApi((s) => api.soccerResearchMatches(competition, date, s), [competition, date]);
  return (
    <AsyncView state={state} retry={retry}>
      {(d) => {
        const graded = d.matches.filter((m) => m.grading === 'gradable').length;
        return (
          <>
            <div className="metrics today-metrics">
              <MetricCard icon="calendar" label="Matches on this date" tone="lime" value={d.matches.length}
                hint={`${calendarDate(date, FULL_DATE)}; ${graded} graded on the 90-minute result.`} />
              <MetricCard icon="layers" label="Research forecasts" tone="dark" value={d.matches.reduce((n, m) => n + m.forecasts.length, 0)}
                hint="Stored backtest rows (one per model), computed after the fact." />
            </div>
            {d.matches.length === 0 ? <Empty>No match is listed on {calendarDate(date, FULL_DATE)}.</Empty>
              : <ul className="game-grid">{d.matches.map((m) => <ResearchCard key={m.match_id} m={m} />)}</ul>}
          </>
        );
      }}
    </AsyncView>
  );
}

function ResearchCard({ m }: { m: SoccerHistMatch & { forecasts: SoccerForecast[] } }) {
  const carried = useCarriedSearch();
  const chosen = m.forecasts.find((f) => f.is_selected_candidate) ?? m.forecasts[0];
  return (
    <li className="card game-card">
      <Link to={`/games/${encodeURIComponent(m.match_id)}${carried}`} className="card-link">
        <div className="game-card-head"><span className="game-time">{m.round} · {timeText(m)}</span></div>
        {(['home', 'away'] as const).map((side) => (
          <div key={side} className={`team-line${m.regulation_result === side ? ' is-winner' : ''}`}>
            <div><strong>{m[side].name}</strong><span>{side === 'home' ? 'Home' : 'Away'}</span></div>
            <b>{m.ft_home != null ? (side === 'home' ? m.ft_home : m.ft_away) : null}</b>
          </div>
        ))}
        <p className="small">{resultText(m)}</p>
        {chosen ? <><ThreeWayBar f={chosen} home={m.home.name} away={m.away.name} /><ForecastMeta f={chosen} /></>
          : <p className="forecast-note">{m.grading_note ? `No research forecast: ${m.grading_note}.` : NO_FORECAST}</p>}
        <div className="card-bottom small muted">
          <span>{m.forecasts.length > 1 ? `${m.forecasts.length} model forecasts stored` : ''}</span>
          <span className="matchup-link">Match <Icon name="arrow" size={14} /></span>
        </div>
      </Link>
    </li>
  );
}

// --- Match page -------------------------------------------------------------------------------------------------------
export function ResearchMatchPage({ id }: { id: string }) {
  const carried = useCarriedSearch();
  const { state, retry } = useApi((s) => api.soccerResearchMatch(id, s), [id]);
  return (
    <section>
      <Link to={`/${carried}`} className="back">‹ Soccer research forecasts</Link>
      <AsyncView state={state} retry={retry}>
        {(d) => {
          const m = d.match;
          return (
            <>
              <section className="hero" aria-label="Match">
                <div className="hero-center">
                  <span className="league-chip">{COMPETITION_LABEL[m.competition_id] ?? m.competition_id} · {seasonLabel(m.season)} · {m.round}</span>
                  <h2 className="hero-matchup">{m.home.name} <span>v</span> {m.away.name}</h2>
                  <p className="hero-time">{calendarDate(m.local_date, FULL_DATE)} · {timeText(m)}{score(m) ? ` · ${score(m)}` : ''}</p>
                </div>
              </section>
              <ResearchNotice meta={{ notice: 'Retrospective research forecasts computed long after this match, from results available at the information cutoff. Not a live prediction, not betting advice.' }} />
              <div className="nfl-detail-grid">
                <div className="card">
                  <h2>Result</h2>
                  <dl className="facts">
                    <dt>90-minute score</dt><dd>{m.ft_home != null ? `${m.ft_home}–${m.ft_away}` : <span className="muted">Not available ({m.status.replace('_', ' ')})</span>}</dd>
                    {m.ht_home != null && <><dt>Half time</dt><dd>{m.ht_home}–{m.ht_away}</dd></>}
                    {m.aet_home != null && <><dt>After extra time</dt><dd>{m.aet_home}–{m.aet_away}</dd></>}
                    {m.pen_home != null && <><dt>Shoot-out</dt><dd>{m.pen_home}–{m.pen_away} <span className="muted">(decides the tie, not the 90-minute result)</span></dd></>}
                    <dt>Graded result</dt><dd>{resultText(m)}</dd>
                    <dt>Result counted as known</dt>
                    <dd>{m.result_available_at ? `${dateTime(m.result_available_at)} (${m.result_availability_basis === 'assumed_next_day_0600Z'
                      ? 'assumed: the source publishes no time' : m.result_availability_basis === 'observed_correction'
                      ? 'a correction, from the retrieval that delivered it' : 'from the retrieval that first listed it'})` : <span className="muted">—</span>}</dd>
                  </dl>
                </div>
                <div className="card">
                  <h2>Source</h2>
                  {d.source ? <dl className="facts">
                    <dt>Data</dt><dd>openfootball/{d.source.repo} (CC0)</dd>
                    <dt>Commit</dt><dd><code>{d.source.commit_sha.slice(0, 12)}</code></dd>
                    <dt>File</dt><dd><code>{d.source.path}</code></dd>
                    <dt>Retrieved</dt><dd>{dateTime(d.source.retrieved_at)}</dd>
                    <dt>Versions seen</dt><dd>{d.revisions.length}</dd>
                  </dl> : <p className="muted">No source record.</p>}
                </div>
              </div>
              <div className="card">
                <h2>Research forecasts (home / draw / away after 90 minutes)</h2>
                {d.forecasts.length === 0
                  ? <p className="muted">{m.grading_note ? `None stored: ${m.grading_note}.` : NO_FORECAST}</p>
                  : <ul className="plain-list">{d.forecasts.map((f) => (
                    <li key={f.forecast_id} className="forecast-row">
                      <ThreeWayBar f={f} home={m.home.name} away={m.away.name} />
                      <ForecastMeta f={f} />
                    </li>))}</ul>}
              </div>
            </>
          );
        }}
      </AsyncView>
    </section>
  );
}

// --- Performance ------------------------------------------------------------------------------------------------------
export function ResearchPerformance({ meta }: { meta: SoccerResearchMeta }) {
  const { state, retry } = useApi((s) => api.soccerResearchPerformance(meta.competition_id, s), [meta.competition_id]);
  return (
    <section>
      <PageTitle icon="performance" title="Soccer research performance" badge={<RunBadge runType="backtest" />}
        sub="Log loss (lower is better) and multiclass Brier of the stored research forecasts against graded 90-minute results." />
      <AsyncView state={state} retry={retry}>
        {(d) => d.rows.length === 0 ? <Empty>No graded research forecast is stored for this competition.</Empty> : (
          <>
            <ResearchNotice meta={d} />
            <div className="table-wrap"><table className="table compare">
              <thead><tr><th>Period</th><th>Model</th><th>Matches</th><th>Log loss</th><th>Brier</th><th>Predicted H/D/A</th><th>Observed H/D/A</th></tr></thead>
              <tbody>{d.rows.map((r) => (
                <tr key={`${r.period}-${r.model}`}>
                  <td>{PERIOD_LABEL[r.period] ?? r.period}</td>
                  <td>{MODEL_LABEL[r.model]}{r.is_selected_candidate ? <span className="small muted"> · selected</span> : ''}</td>
                  <td>{r.n.toLocaleString()}</td><td>{r.log_loss.toFixed(4)}</td><td>{r.brier.toFixed(4)}</td>
                  <td>{r.mean_predicted.map(pct).join(' / ')}</td><td>{r.observed.map(pct).join(' / ')}</td>
                </tr>))}</tbody>
            </table></div>
            <p className="small muted">Development (tuning) figures are in-sample for the frequencies and for Elo's draw model, both fitted on those seasons; validation figures are out of sample. Validation chose the model once (preregistered rule); these are not test results and not a betting edge.</p>
            <Reliability row={d.rows.find((r) => r.period === 'validation' && r.is_selected_candidate)
              ?? d.rows.find((r) => r.period === 'validation')} />
          </>
        )}
      </AsyncView>
    </section>
  );
}

function Reliability({ row }: { row?: SoccerPerformanceRow }) {
  if (!row) return null;
  return (
    <div className="card">
      <h2>Reliability on validation: {MODEL_LABEL[row.model]}</h2>
      <p className="small muted">Matches grouped by forecast probability (10 bins per outcome): mean forecast against how often it happened. Small bins are noisy.</p>
      <div className="reliability-grid">
        {row.reliability.map((o) => (
          <div className="table-wrap" key={o.outcome}><table className="table compare">
            <thead><tr><th>{o.outcome === 'home' ? 'Home win' : o.outcome === 'draw' ? 'Draw' : 'Away win'}</th><th>n</th><th>Forecast</th><th>Happened</th></tr></thead>
            <tbody>{o.bins.map((b) => (
              <tr key={b.lower}><td>{Math.round(b.lower * 100)}–{Math.round(b.upper * 100)}%</td><td>{b.n}</td>
                <td>{pct(b.mean_predicted)}</td><td>{pct(b.observed)}</td></tr>))}</tbody>
          </table></div>
        ))}
      </div>
    </div>
  );
}

// --- History ----------------------------------------------------------------------------------------------------------
export function ResearchHistory({ meta }: { meta: SoccerResearchMeta }) {
  const [params, setParams] = useSearchParams();
  const carried = useCarriedSearch();
  const [competition] = useCompetition();
  const rawSeason = Number(params.get('season'));
  const season = params.get('season') && Number.isInteger(rawSeason) ? rawSeason : null;   // malformed: all seasons
  const rawModel = params.get('model');
  const model = rawModel && rawModel in MODEL_LABEL ? rawModel : null;
  const offset = Math.max(0, Math.floor(Number(params.get('offset') ?? 0)) || 0);
  const set = (k: string, v: string | null) => {
    const next = new URLSearchParams(params);
    if (v == null || v === '') next.delete(k); else next.set(k, v);
    if (k !== 'offset') next.delete('offset');
    setParams(next);
  };
  const { state, retry } = useApi((s) => api.soccerResearchHistory(competition, season, model, offset, s),
    [competition, season, model, offset]);
  return (
    <section>
      <PageTitle icon="history" title="Soccer research forecast history" badge={<RunBadge runType="backtest" />}
        sub="Stored research forecasts, newest match first. Retrospective; not a record of live predictions." />
      <ResearchNotice meta={meta} />
      <AsyncView state={state} retry={retry}>
        {(d) => (
          <>
            <div className="filters">
              <label>Season <select value={season ?? ''} onChange={(e) => set('season', e.target.value || null)}>
                <option value="">All</option>{d.seasons.map((s) => <option key={s} value={s}>{seasonLabel(s)}</option>)}
              </select></label>
              <label>Model <select value={model ?? ''} onChange={(e) => set('model', e.target.value || null)}>
                <option value="">All</option>{(Object.keys(MODEL_LABEL) as SoccerModel[]).map((k) => <option key={k} value={k}>{MODEL_LABEL[k]}</option>)}
              </select></label>
              <span className="small muted">{d.total.toLocaleString()} forecasts</span>
            </div>
            {d.items.length === 0 ? <Empty>No research forecast matches these filters.</Empty> : (
              <div className="table-wrap"><table className="table compare">
                <thead><tr><th>Date</th><th>Match</th><th>Model</th><th>Home</th><th>Draw</th><th>Away</th><th>90-minute result</th></tr></thead>
                <tbody>{d.items.map(({ match: m, forecast: f }) => (
                  <tr key={f.forecast_id}>
                    <td>{m.local_date}</td>
                    <td><Link to={`/games/${encodeURIComponent(m.match_id)}${carried}`}>{m.home.name} v {m.away.name}</Link></td>
                    <td>{MODEL_LABEL[f.model]}</td><td>{pct(f.p_home)}</td><td>{pct(f.p_draw)}</td><td>{pct(f.p_away)}</td>
                    <td>{m.grading === 'gradable' ? `${m.ft_home}–${m.ft_away}` : <span className="muted">{m.grading_note}</span>}</td>
                  </tr>))}</tbody>
              </table></div>)}
            <div className="pager">
              <button type="button" disabled={offset === 0} onClick={() => set('offset', String(Math.max(0, offset - d.limit)))}>Newer</button>
              <span className="small muted">{d.total ? (offset < d.total ? `${offset + 1}–${Math.min(offset + d.limit, d.total)} of ${d.total}` : `past the last of ${d.total}`) : ''}</span>
              <button type="button" disabled={offset + d.limit >= d.total} onClick={() => set('offset', String(offset + d.limit))}>Older</button>
            </div>
          </>
        )}
      </AsyncView>
    </section>
  );
}
