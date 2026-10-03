import { ModelAgreement } from '../components/ModelAgreement';
import { Link, useParams } from 'react-router-dom';
import { api } from '../api/client';
import type { GameDetail, TeamStats } from '../api/types';
import { useApi } from '../api/useApi';
import { ModeBadge, NearTipoffBadge, RunBadge } from '../components/Badges';
import { ProbabilityBar } from '../components/ProbabilityBar';
import { AsyncView } from '../components/States';
import { calendarDate, dateTime, pct } from '../format';
import { MarketForecastsCard } from '../components/MarketView';
import { useCarriedSearch, useSport } from '../components/Selection';
import { ResearchGameCard } from '../components/Research';
import { TrackingGameCard } from '../components/Tracking';

export function GamePage() {
  const { gameId = '' } = useParams();
  const [sport] = useSport();
  const carried = useCarriedSearch();
  const { state, retry } = useApi((s) => api.game(sport, gameId, s), [sport, gameId]);
  return (
    <section>
      {state.status === 'error' && <Link to={`/${carried}`} className="back">‹ Today's games</Link>}
      <AsyncView state={state} retry={retry}>{(g) => <Detail game={g} />}</AsyncView>
    </section>
  );
}

const avg = (v: number | null) => (v == null ? '—' : v.toFixed(1));

const statRows = (unit: string): { label: string; help?: string; value: (s: TeamStats) => string }[] => [
  { label: 'Record', help: 'Regular-season wins and losses known at the stats cutoff (NHL: overtime and shootout results count as wins or losses).', value: (s) => `${s.wins}-${s.losses}` },
  { label: 'Last 10', value: (s) => (s.last10_games ? `${s.last10_wins}-${s.last10_games - s.last10_wins}` : '—') },
  { label: `${unit} scored / game`, value: (s) => avg(s.points_for_avg) },
  { label: `${unit} allowed / game`, value: (s) => avg(s.points_against_avg) },
  { label: 'Rest days', help: 'Days since the previous scheduled game this season.', value: (s) => (s.rest_days == null ? '—' : s.rest_days.toFixed(1)) },
];

function Detail({ game: g }: { game: GameDetail }) {
  const [sport] = useSport();
  const carried = useCarriedSearch();
  const back = new URLSearchParams(carried.slice(1));
  back.set('date', g.game_date);
  return (
    <>
      <Link to={`/?${back}`} className="back">‹ Games on {calendarDate(g.game_date)}</Link>
      <div className="page-head">
        <h1>{g.away.name} <span className="muted">@</span> {g.home.name} <ModeBadge mode={g.mode} /></h1>
      </div>
      <p className="muted">
        {g.outcome
          ? <>Final: {g.away.abbr} {g.outcome.away_score} – {g.outcome.home_score} {g.home.abbr}</>
          : <>Tipoff {dateTime(g.tipoff)}</>}
      </p>

      {g.mode === 'real' && (g.near_tipoff ? <NearTipoffCard game={g} /> : <div className="card">
        <h2>Within 2 hours · Win probability</h2><p className="forecast-note">No two-hour forecast recorded. New forecasts are made only during the two hours before the scheduled start, when fresh inputs are available.</p>
      </div>)}
      <div className="card"><ModelAgreement game={g} detail /></div>
      <ResearchGameCard sport={sport} gameId={g.game_id} />
      <TrackingGameCard sport={sport} gameId={g.game_id} />
      <div className="card">
        <h2>{g.mode === 'real' ? 'Archived night-before probability' : 'Win probability'} {g.prediction && <RunBadge runType={g.prediction.run_type} />}</h2>
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


      {sport === 'nhl' && g.mode === 'real' && (
        <div className="card">
          <h2>Starting goalies (ESPN listing)</h2>
          <p className="small muted">As listed by ESPN when we last checked before now. "Expected" is not "confirmed". Not used by any model yet; collected so it can be tested later.</p>
          {g.goalies.length === 0 ? <p className="muted">No goalie listing has been retrieved for this game.</p> : (
            <dl className="facts">
              {g.goalies.map((x) => (
                <div key={x.side}>
                  <dt>{x.side === 'home' ? g.home.abbr : g.away.abbr}</dt>
                  <dd>{x.source_status === 'none_listed' ? <span className="muted">No goalie listed</span>
                    : <><strong>{x.player_name ?? 'Unnamed'}</strong> · {x.source_status_label ?? x.source_status}</>}
                    <span className="muted"> (retrieved {dateTime(x.retrieved_at)})</span></dd>
                </div>
              ))}
            </dl>
          )}
        </div>
      )}

      {g.mode === 'real' && <MarketForecastsCard forecasts={g.market_forecasts} />}

      <div className="card">
        <h2>Team comparison</h2>
        <p className="small muted">This season, using only results known by {dateTime(g.stats_as_of)}.</p>
        <table className="table compare">
          <thead><tr><th /><th>{g.away.abbr}</th><th>{g.home.abbr}</th></tr></thead>
          <tbody>
            {statRows(sport === 'nhl' ? 'Goals' : 'Points').map((row) => (
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

function NearTipoffCard({ game: g }: { game: GameDetail }) {
  const n = g.near_tipoff!;
  return (
    <div className="card">
      <h2>Within 2 hours · Win probability <NearTipoffBadge /></h2>
      <p className="small muted">
        The current forecast policy uses the freshest stored results within two hours of the scheduled start. {n.limitation}
      </p>
      <ProbabilityBar home={g.home} away={g.away} homeProb={n.home_win_prob} label="Near-tipoff win probability" />
      <dl className="facts">
        <dt>Pick</dt>
        <dd>
          <strong>{n.pick === 'home' ? g.home.abbr : g.away.abbr}</strong> · model confidence {pct(n.confidence, 1)}
          {n.correct != null && (n.correct ? <span className="ok"> · Correct</span> : <span className="bad"> · Wrong</span>)}
        </dd>
        <dt>Made</dt><dd>{dateTime(n.generated_at)}, {Math.round(n.minutes_before_tipoff)} minutes before the scheduled tipoff</dd>
        <dt>Data freshness</dt>
        <dd>
          {n.results_refreshed_at ? `Schedule and results refreshed ${dateTime(n.results_refreshed_at)}` : 'Refresh time not recorded'}
          {n.results_used != null && ` · ${n.results_used} results used`}
          {n.latest_result_available_at && ` · newest result from ${dateTime(n.latest_result_available_at)}`}
        </dd>
        <dt>Market at that time</dt>
        <dd>
          {n.market_home_prob != null && n.market_retrieved_at
            ? `${g.home.abbr} ${pct(n.market_home_prob)} (quote retrieved ${dateTime(n.market_retrieved_at)})`
            : <span className="warn">No market quote was available when this forecast was made</span>}
        </dd>
        <dt>Model version</dt><dd><code>{n.model_version}</code></dd>
      </dl>
    </div>
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
