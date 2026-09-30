import { Link, useParams } from 'react-router-dom';
import { api } from '../api/client';
import type { GameDetail, TeamStats } from '../api/types';
import { useApi } from '../api/useApi';
import { ModeBadge, RunBadge } from '../components/Badges';
import { ProbabilityBar } from '../components/ProbabilityBar';
import { AsyncView } from '../components/States';
import { calendarDate, dateTime, pct } from '../format';

export function GamePage() {
  const { gameId = '' } = useParams();
  const { state, retry } = useApi((s) => api.game(gameId, s), [gameId]);
  return (
    <section>
      {state.status === 'error' && <Link to="/" className="back">‹ Today's games</Link>}
      <AsyncView state={state} retry={retry}>{(g) => <Detail game={g} />}</AsyncView>
    </section>
  );
}

const avg = (v: number | null) => (v == null ? '—' : v.toFixed(1));

const STAT_ROWS: { label: string; help?: string; value: (s: TeamStats) => string }[] = [
  { label: 'Record', help: 'Regular-season wins and losses known at the stats cutoff.', value: (s) => `${s.wins}-${s.losses}` },
  { label: 'Last 10', value: (s) => (s.last10_games ? `${s.last10_wins}-${s.last10_games - s.last10_wins}` : '—') },
  { label: 'Points scored / game', value: (s) => avg(s.points_for_avg) },
  { label: 'Points allowed / game', value: (s) => avg(s.points_against_avg) },
  { label: 'Rest days', help: 'Days since the previous scheduled game this season.', value: (s) => (s.rest_days == null ? '—' : s.rest_days.toFixed(1)) },
];

function Detail({ game: g }: { game: GameDetail }) {
  return (
    <>
      <Link to={`/?date=${g.game_date}`} className="back">‹ Games on {calendarDate(g.game_date)}</Link>
      <div className="page-head">
        <h1>{g.away.name} <span className="muted">@</span> {g.home.name} <ModeBadge mode={g.mode} /></h1>
      </div>
      <p className="muted">
        {g.outcome
          ? <>Final: {g.away.abbr} {g.outcome.away_score} – {g.outcome.home_score} {g.home.abbr}</>
          : <>Tipoff {dateTime(g.tipoff)}</>}
      </p>

      <div className="card">
        <h2>Win probability {g.prediction && <RunBadge runType={g.prediction.run_type} />}</h2>
        {g.prediction
          ? <ProbabilityBar home={g.home} away={g.away} homeProb={g.prediction.home_win_prob} />
          : <p className="forecast-note">{g.forecast_note ?? 'No forecast available.'}</p>}
        <table className="table compare">
          <thead><tr><th /><th>{g.away.abbr}</th><th>{g.home.abbr}</th></tr></thead>
          <tbody>
            <tr>
              <th scope="row">Model</th>
              {g.prediction
                ? <><td>{pct(1 - g.prediction.home_win_prob)}</td><td>{pct(g.prediction.home_win_prob)}</td></>
                : <td colSpan={2} className="muted">No forecast</td>}
            </tr>
            <tr>
              <th scope="row">Market <Help label="Market">Bookmaker odds converted to probabilities with the margin removed (de-vigged).</Help></th>
              {g.market
                ? <><td>{pct(1 - g.market.home_win_prob)}</td><td>{pct(g.market.home_win_prob)}</td></>
                : <td colSpan={2} className="warn">No market odds for this game</td>}
            </tr>
          </tbody>
        </table>
        <dl className="facts">
          {g.prediction && <PredictionFacts prediction={g.prediction} />}
          {g.market && (
            <>
              <dt>Market quote</dt>
              <dd>{g.market.bookmaker} · {g.market.price_label} line · {g.market.quote_time ? `quoted ${dateTime(g.market.quote_time)}` : `retrieved ${dateTime(g.market.retrieved_at)} (no quote time published)`}</dd>
            </>
          )}
          <dt>Projected score</dt>
          <dd className="muted">{g.projected_score_available ? '—' : 'Not shown until a validated score model exists.'}</dd>
        </dl>
      </div>

      <div className="card">
        <h2>Team comparison</h2>
        <p className="small muted">This season, using only results known by {dateTime(g.stats_as_of)}.</p>
        <table className="table compare">
          <thead><tr><th /><th>{g.away.abbr}</th><th>{g.home.abbr}</th></tr></thead>
          <tbody>
            {STAT_ROWS.map((row) => (
              <tr key={row.label}>
                <th scope="row">{row.label}{row.help && <Help label={row.label}>{row.help}</Help>}</th>
                <td>{row.value(g.away_stats)}</td>
                <td>{row.value(g.home_stats)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function PredictionFacts({ prediction: p }: { prediction: NonNullable<GameDetail['prediction']> }) {
  if (p.run_type === 'backtest') {
    return (
      <>
        <dt>Prediction type</dt><dd>Retrospective backtest, computed {dateTime(p.generated_at)}. Not a forecast recorded before the game.</dd>
        <dt>Information cutoff</dt><dd>{dateTime(p.cutoff_at)}</dd>
        <dt>Model version</dt><dd><code>{p.model_version}</code></dd>
      </>
    );
  }
  return (
    <>
      <dt>Prediction made</dt><dd>{dateTime(p.generated_at)} (before the {dateTime(p.cutoff_at)} cutoff)</dd>
      <dt>Model version</dt><dd><code>{p.model_version}</code></dd>
    </>
  );
}

function Help({ label, children }: { label: string; children: string }) {
  return (
    <details className="help">
      <summary aria-label={`About ${label}`}>?</summary>
      <span className="help-text">{children}</span>
    </details>
  );
}
