import { ModelAgreement } from '../components/ModelAgreement';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import type { CanonicalGrade, GameDetail, TeamStats } from '../api/types';
import { useApi } from '../api/useApi';
import { ModeBadge, NearTipoffBadge, RunBadge } from '../components/Badges';
import { ProbabilityBar } from '../components/ProbabilityBar';
import { AsyncView } from '../components/States';
import { american, calendarDate, dateTime, FULL_DATE, pct } from '../format';
import { MarketForecastsCard } from '../components/MarketView';
import { DetailsToggle, useCarriedSearch, useDetails, useSport } from '../components/Selection';
import { MARKET, ResearchGameCard } from '../components/Research';
import { AdaptiveGameCard } from '../components/Adaptive';
import { TrackingGameCard } from '../components/Tracking';
import { CandidateForecasts } from '../components/CandidateForecasts';
import { CanonicalBlock, canonicalFor, GRADE_LABEL, STATE_LABEL } from '../components/Canonical';
import { noProvenSkill } from '../components/Skill';
import { TeamLogo } from '../components/TeamLogo';
import { EmptyForecastState, Icon, MetricCard, PageTitle } from '../components/ui';
import { GameRankedPicks, useGamePicks } from '../components/RankedPicks';

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

type Tab = 'forecast' | 'markets' | 'teams' | 'research';

function Detail({ game: g }: { game: GameDetail }) {
  const [sport] = useSport();
  const carried = useCarriedSearch();
  const [details] = useDetails();
  // The open section lives in the URL (?tab=), so it survives a reload and can be linked; the route is unchanged.
  const [params, setParams] = useSearchParams();
  const tab: Tab = (['forecast', 'markets', 'teams', 'research'] as Tab[]).find((t) => t === params.get('tab')) ?? 'forecast';
  const setTab = (t: Tab) => {
    const next = new URLSearchParams(params);
    if (t === 'forecast') next.delete('tab'); else next.set('tab', t);
    setParams(next);
    window.scrollTo(0, 0);
  };
  const back = new URLSearchParams(carried.slice(1));
  back.set('date', g.game_date);
  const real = g.mode === 'real';
  const others = (g.canonical_markets ?? []).filter((c) => c.market !== 'winner');
  const picks = useGamePicks(sport, g.game_date, g.game_id, real);
  const SUB: Record<Exclude<Tab, 'forecast'>, { title: string; icon: string; sub: string }> = {
    markets: { title: 'Markets', icon: 'scale', sub: 'The bookmaker line and every market forecast for this game.' },
    teams: { title: 'Team Comparison', icon: 'bars', sub: 'Both teams side by side, from results known before the game.' },
    research: { title: 'Research Details', icon: 'flask', sub: 'Legacy forecasts, model agreement and research models. None of it is the current forecast.' },
  };
  const more = (
    <nav className="more-links" aria-label="More for this game">
      <span className="more-label">More for this game</span>
      {(['markets', 'teams', 'research'] as const).map((t) => (
        <button key={t} type="button" onClick={() => setTab(t)}><Icon name={SUB[t].icon} size={18} />{SUB[t].title}<Icon name="chevron" size={16} /></button>
      ))}
    </nav>
  );
  if (tab !== 'forecast') {
    const page = SUB[tab];
    return (
      <div className={`sub-page sub-${tab}`}>
        <nav className="crumbs" aria-label="Breadcrumb">
          <button type="button" className="link-btn" onClick={() => setTab('forecast')}>Prediction Dashboard</button><span>/</span><span>{page.title}</span>
        </nav>
        <div className="sub-title"><h1>{page.title}{g.mode === 'demo' && <ModeBadge mode={g.mode} />}</h1></div>
        <MatchupStrip game={g} sport={sport} />
        {tab === 'markets' && <>
          <MarketOddsCard game={g} />
          {real && <MarketForecastsCard forecasts={g.market_forecasts} />}
        </>}
        {tab === 'teams' && <>
          <TeamComparison game={g} unit={sport === 'nhl' ? 'Goals' : 'Points'} />
          {sport === 'nhl' && real && <GoaliesCard game={g} />}
        </>}
        {tab === 'research' && <>
          {real && <ArchiveCard game={g} />}
          {real && (g.near_tipoff ? <NearTipoffCard game={g} legacy={canonicalFor(sport)} /> : <div className="card">
            <h2>Legacy – Within 2 Hours (Elo Ledger)</h2>
            <EmptyForecastState>No two-hour forecast recorded. New forecasts are made only during the two hours before the scheduled start, when fresh inputs are available.</EmptyForecastState>
          </div>)}
          <div className="card"><ModelAgreement game={g} detail /></div>
          <DetailsToggle />
          {details && <>
            <ResearchGameCard sport={sport} gameId={g.game_id} />
            <AdaptiveGameCard sport={sport} gameId={g.game_id} />
            <TrackingGameCard sport={sport} gameId={g.game_id} />
            <CandidateForecasts sport={sport} gameId={g.game_id} />
          </>}
        </>}
      </div>
    );
  }
  return (
    <>
      <PageTitle icon="dashboard" title="Prediction Dashboard" badge={g.mode === 'demo' ? <ModeBadge mode={g.mode} /> : undefined}
        sub="Forecasts, probabilities and market context."
        right={<Link to={`/?${back}`} className="date-chip">{calendarDate(g.game_date, FULL_DATE)}<Icon name="chevronDown" size={18} /></Link>} />
      <GameHero game={g} sport={sport} />
      {real && <ForecastMetrics game={g} picks={picks} />}
      {!real && <ArchiveCard game={g} />}
      {more}
      {real && <GameRankedPicks sport={sport} date={g.game_date} gameId={g.game_id} compact />}
      {real && <ForecastTable game={g} />}
      {real && canonicalFor(sport) && (
        <details className="card legacy-card card-soft">
          <summary><Icon name="chevron" size={16} />Forecast record details <span className="legacy-tag">Model, source, timing</span></summary>
          <h2>Canonical forecast · Win probability</h2>
          <p className="small muted">The one forecast record for this game that Today, History and Performance also show and grade.</p>
          <CanonicalBlock c={g.canonical} home={g.home} away={g.away} detail />
          {others.length > 0 && <>
            <h2 className="section-gap">Canonical forecast · Other markets</h2>
            <p className="small muted">One record per market for this game, the same records Today, History and Performance show and grade for that market.
              Each is graded at its own line.</p>
            {others.map((c) => (
              <section key={c.market} className="canonical-market" aria-label={`Canonical ${MARKET[c.market] ?? c.market} forecast`}>
                <h3>{MARKET[c.market] ?? c.market}</h3>
                <CanonicalBlock c={c} home={g.home} away={g.away} detail={details} />
              </section>
            ))}
          </>}
        </details>
      )}
    </>
  );
}

/** The matchup at a glance, as in the reference: both logos, the league, the matchup and the start (or the final score).
 * Probabilities live in the Win probability card below; records in Team comparison. */
function GameHero({ game: g, sport }: { game: GameDetail; sport: string }) {
  return (
    <section className="hero" aria-label="Matchup">
      <TeamLogo team={g.away} size={104} />
      <div className="hero-center">
        <span className="league-chip">{sport.toUpperCase()}</span>
        <h2 className="hero-matchup">{g.away.name} <span>@</span> {g.home.name}</h2>
        <p className="hero-time">{g.outcome
          ? <>Final: {g.away.abbr} {g.outcome.away_score} – {g.outcome.home_score} {g.home.abbr}</>
          : <>Tipoff: {dateTime(g.tipoff)}{g.status !== 'scheduled' && ` · ${g.status.replace('_', ' ')}`}</>}</p>
      </div>
      <TeamLogo team={g.home} size={104} />
    </section>
  );
}

/** The compact matchup header of the Research details page. */
function MatchupStrip({ game: g, sport }: { game: GameDetail; sport: string }) {
  return (
    <section className="matchup-strip" aria-label="Matchup">
      <span className="page-title-icon"><Icon name={sport === 'nhl' ? 'puck' : 'ball'} size={24} /></span>
      <div className="matchup-strip-text">
        <span className="league-chip">{sport.toUpperCase()}</span>
        <strong>{g.away.name} @ {g.home.name}</strong>
        <span className="muted">{g.outcome ? `Final: ${g.away.abbr} ${g.outcome.away_score} – ${g.outcome.home_score} ${g.home.abbr}` : `Tipoff: ${dateTime(g.tipoff)}`}</span>
      </div>
      <div className="matchup-strip-logos"><TeamLogo team={g.away} size={56} /><span>@</span><TeamLogo team={g.home} size={56} /></div>
    </section>
  );
}

const MARKET_ICON: Record<string, string> = { winner: 'bars', reg3: 'scale', spread: 'plusminus', total: 'layers', team_total_home: 'home', team_total_away: 'airplane' };

/** The forecast at a glance: the best pick, then one card per market (current pick and model probability), grey with the
 * reason when there is no forecast. */
function ForecastMetrics({ game: g, picks }: { game: GameDetail; picks: ReturnType<typeof useGamePicks> }) {
  const markets = g.canonical_markets ?? [];
  const best = picks.picks?.find((x) => x.strong) ?? null;
  return (
    <div className="metrics forecast-metrics">
      {best
        ? <MetricCard icon="targetArrow" label="Best Pick" tone="accent" value={best.pick_label}
            hint={<>{best.market_title}{best.threshold != null && <> · <span className="badge badge-threshold">{pct(best.threshold)}+</span></>}
              {best.evidence && <> · picks like this won {pct(best.evidence.win_rate, 0)}</>}{best.grade && best.grade !== 'pending' && <> · {GRADE_LABEL[best.grade as CanonicalGrade]?.split(' (')[0] ?? best.grade}</>}</>} />
        : <MetricCard icon="targetArrow" label="Best Pick" tone="plain"
            value={<span className="metric-na">{picks.picks == null ? (picks.error ? 'Unavailable' : '…') : picks.picks.length === 0 ? 'Not available yet' : 'No strong pick'}</span>}
            hint={picks.picks && picks.picks.length > 0 ? 'No pick here is of a kind that has won more often than not.' : 'Picks appear about two hours before the game starts.'} />}
      {markets.map((c) => {
        const r = c.state === 'forecast' ? c.record : null;
        const title = c.market === 'winner' ? 'Win Probability' : MARKET[c.market] ?? c.market;
        const icon = MARKET_ICON[c.market];
        const marketHint = c.market === 'winner' && g.market ? `Market: ${g.home.abbr} ${pct(g.market.home_win_prob, 1)}` : null;
        if (!r) {
          return <MetricCard key={c.market} icon={icon} label={title} tone="plain" value={<span className="metric-na">—</span>}
            hint={<>{c.state === 'forecast' ? 'No forecast recorded' : STATE_LABEL[c.state]}{marketHint && <><br />{marketHint}</>}</>} />;
        }
        const graded = c.grade && c.grade !== 'pending' ? GRADE_LABEL[c.grade].split(' (')[0] : null;
        if (c.market === 'winner' && r.home_win_prob != null) {
          const home = r.home_win_prob, away = 1 - home;
          const pick = r.pick === 'home' ? g.home.abbr : g.away.abbr;
          return <MetricCard key={c.market} icon={icon} label={title} tone="plain"
            value={<>{pick} <span className="metric-strong">{pct(r.confidence, 1)}</span></>}
            hint={<>
              <span className="mini-bar" role="img" aria-label={`${g.away.abbr} ${pct(away, 1)}, ${g.home.abbr} ${pct(home, 1)}`}>
                <span className={away > home ? 'is-fav' : ''} style={{ width: `${away * 100}%` }} /><span className={home >= away ? 'is-fav' : ''} style={{ width: `${home * 100}%` }} />
              </span>
              {[`${r.pick === 'home' ? g.away.abbr : g.home.abbr} ${pct(r.pick === 'home' ? away : home, 1)}`, marketHint, graded].filter(Boolean).join(' · ')}
            </>} />;
        }
        const pick = c.market === 'winner' ? (r.pick === 'home' ? g.home.abbr : g.away.abbr) : r.pick_label;
        return <MetricCard key={c.market} icon={icon} label={title} tone="plain" value={pick ?? '—'}
          hint={<>{pct(r.confidence, 1)} model probability{graded ? ` · ${graded}` : ''}
            {noProvenSkill(c.market) && <> <span className="skill-chip" title="No proven skill against the bookmaker's line: at the line, past picks in this market won about half the time. Shown for information, not as a pick.">No proven skill</span></>}</>} />;
      })}
    </div>
  );
}

/** The canonical forecast of every market in one table: Market | Forecast | Status. */
function ForecastTable({ game: g }: { game: GameDetail }) {
  const markets = g.canonical_markets ?? [];
  if (markets.length === 0) return null;
  return (
    <div className="card">
      <h2>Canonical Forecast</h2>
      <div className="table-wrap"><table className="table forecast-table">
        <thead><tr><th>Market</th><th>Forecast</th><th>Status</th></tr></thead>
        <tbody>{markets.map((c) => {
          const r = c.state === 'forecast' ? c.record : null;
          const pick = !r ? null : c.market === 'winner' ? (r.pick === 'home' ? g.home.abbr : g.away.abbr) : r.pick_label;
          const cls = c.grade === 'win' ? 'ok' : c.grade === 'loss' ? 'bad' : '';
          return <tr key={c.market}>
            <td>{MARKET[c.market] ?? c.market}</td>
            <td>{r ? <><strong>{pick}</strong> <span className="muted">· {pct(r.confidence, 1)}</span></> : <span className="muted">{c.state === 'forecast' ? 'No forecast recorded' : STATE_LABEL[c.state]}</span>}</td>
            <td>{c.grade ? <span className={`status-chip ${cls}`}>{GRADE_LABEL[c.grade].split(' (')[0]}</span> : <span className="muted">—</span>}</td>
          </tr>;
        })}</tbody>
      </table></div>
    </div>
  );
}

/** What the bookmaker says: the de-vigged moneyline probability and the American odds, with the quote's provenance. */
function MarketOddsCard({ game: g }: { game: GameDetail }) {
  const m = g.market;
  return (
    <div className="card">
      <h2>Bookmaker Moneyline</h2>
      {!m ? <EmptyForecastState>No market odds for this game.</EmptyForecastState> : <>
        <div className="table-wrap"><table className="table compare">
          <thead><tr><th>Team</th><th>Market probability <Help label="Market">Bookmaker odds converted to probabilities with the margin removed (de-vigged).</Help></th><th>Odds <Help label="Odds">The bookmaker's moneyline as quoted (American odds), before the margin is removed.</Help></th></tr></thead>
          <tbody>
            <tr><td><span className="team-cell"><TeamLogo team={g.away} size={28} />{g.away.name}</span></td><td>{pct(1 - m.home_win_prob, 1)}</td><td>{m.away_price != null ? american(m.away_price) : '—'}</td></tr>
            <tr><td><span className="team-cell"><TeamLogo team={g.home} size={28} />{g.home.name}</span></td><td>{pct(m.home_win_prob, 1)}</td><td>{m.home_price != null ? american(m.home_price) : '—'}</td></tr>
          </tbody>
        </table></div>
        <p className="small muted">{m.bookmaker} · {m.price_label} line · {m.quote_time ? `quoted ${dateTime(m.quote_time)}` : `retrieved ${dateTime(m.retrieved_at)} (no quote time published)`}</p>
      </>}
    </div>
  );
}

/** Side by side; a value is highlighted only where higher (or lower) is plainly better. Rest days are not judged. */
function TeamComparison({ game: g, unit }: { game: GameDetail; unit: string }) {
  const lead = (a: number | null, h: number | null, higher = true): 'away' | 'home' | null => {
    if (a == null || h == null || a === h) return null;
    return (a > h) === higher ? 'away' : 'home';
  };
  const winPct = (s: TeamStats) => (s.wins + s.losses ? s.wins / (s.wins + s.losses) : null);
  const l10 = (s: TeamStats) => (s.last10_games ? s.last10_wins / s.last10_games : null);
  const leads: Record<string, 'away' | 'home' | null> = {
    Record: lead(winPct(g.away_stats), winPct(g.home_stats)),
    'Last 10': lead(l10(g.away_stats), l10(g.home_stats)),
    [`${unit} scored / game`]: lead(g.away_stats.points_for_avg, g.home_stats.points_for_avg),
    [`${unit} allowed / game`]: lead(g.away_stats.points_against_avg, g.home_stats.points_against_avg, false),
  };
  return (
    <div className="card">
      <h2>Team Comparison</h2>
      <p className="small muted">This season, using only results known by {dateTime(g.stats_as_of)}. Highlighted: the better value where higher or lower is clearly better.</p>
      <div className="table-wrap"><table className="table compare">
        <thead><tr><th>Metric</th><th><span className="team-cell"><TeamLogo team={g.away} size={24} />{g.away.abbr}</span></th><th><span className="team-cell"><TeamLogo team={g.home} size={24} />{g.home.abbr}</span></th></tr></thead>
        <tbody>
          {statRows(unit).map((row) => (
            <tr key={row.label}>
              <th scope="row">{row.label}{row.help && <Help label={row.label}>{row.help}</Help>}</th>
              <td className={leads[row.label] === 'away' ? 'lead' : undefined}>{row.value(g.away_stats)}</td>
              <td className={leads[row.label] === 'home' ? 'lead' : undefined}>{row.value(g.home_stats)}</td>
            </tr>
          ))}
        </tbody>
      </table></div>
    </div>
  );
}

function GoaliesCard({ game: g }: { game: GameDetail }) {
  return (
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
  );
}

/** The pre-game archive probability (the main forecast in demo mode; legacy in real mode), with the market rows. */
function ArchiveCard({ game: g, bare = false }: { game: GameDetail; bare?: boolean }) {
  const [sport] = useSport();
  const body = <>
    {!bare && <h2>{g.mode !== 'real' ? 'Win probability' : canonicalFor(sport) ? 'Legacy – Pre-Game Archive Probability' : 'Pre-game archive probability'} {g.prediction && <RunBadge runType={g.prediction.run_type} />}</h2>}
    {bare && g.prediction && <p><RunBadge runType={g.prediction.run_type} /></p>}
    {g.prediction
      ? <ProbabilityBar home={g.home} away={g.away} homeProb={g.prediction.home_win_prob} />
      : <EmptyForecastState>{g.forecast_note ?? 'No forecast available.'}</EmptyForecastState>}
    <table className="table compare">
      <thead><tr><th /><th>{g.away.abbr}</th><th>{g.home.abbr}</th></tr></thead>
      <tbody>
        <tr>
          <th scope="row">Model</th>
          {g.prediction
            ? <><td>{pct(1 - g.prediction.home_win_prob, 1)}</td><td>{pct(g.prediction.home_win_prob, 1)}</td></>
            : <><td className="muted">No forecast</td><td className="muted">—</td></>}
        </tr>
        <tr>
          <th scope="row">Market <Help label="Market">Bookmaker odds converted to probabilities with the margin removed (de-vigged).</Help></th>
          {g.market
            ? <><td>{pct(1 - g.market.home_win_prob, 1)}</td><td>{pct(g.market.home_win_prob, 1)}</td></>
            : <td colSpan={2} className="warn">No market odds for this game</td>}
        </tr>
        {g.market?.home_price != null && g.market.away_price != null && (
          <tr>
            <th scope="row">Odds <Help label="Odds">The bookmaker's moneyline as quoted (American odds), before the margin is removed.</Help></th>
            <td>{american(g.market.away_price)}</td><td>{american(g.market.home_price)}</td>
          </tr>
        )}
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
      <dd className="muted">{g.projected_score_available ? 'Available in the market forecasts.' : 'Not shown until a validated score model exists.'}</dd>
    </dl>
  </>;
  return bare ? body : <div className="card">{body}</div>;
}

function NearTipoffCard({ game: g, legacy = false }: { game: GameDetail; legacy?: boolean }) {
  const n = g.near_tipoff!;
  return (
    <div className="card">
      <h2>{legacy ? 'Legacy – Within 2 Hours (Elo Ledger)' : 'Within 2 Hours · Win Probability'} <NearTipoffBadge /></h2>
      <p className="small muted">
        The current forecast policy uses the freshest stored results within two hours of the scheduled start. {n.limitation}
      </p>
      <ProbabilityBar home={g.home} away={g.away} homeProb={n.home_win_prob} label="Near-tipoff win probability" />
      <dl className="facts">
        <dt>Pick</dt>
        <dd>
          <strong>{n.pick === 'home' ? g.home.abbr : g.away.abbr}</strong> · model confidence {pct(n.confidence, 1)}
          {n.correct != null && (n.correct ? <span className="ok"> · Correct</span> : <span className="bad"> · Wrong</span>)}
          {n.void && <span className="muted"> · Void (start changed, postponed or cancelled: not scored)</span>}
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
            ? `${g.home.abbr} ${pct(n.market_home_prob, 1)} (quote retrieved ${dateTime(n.market_retrieved_at)})`
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
      <dt>Prediction made</dt><dd>{dateTime(p.generated_at)} (before the {dateTime(p.cutoff_at)} cutoff).{p.run_type === 'live' && ' Pre-game archive forecasts come from the discontinued daily job and may have been made several days before the game.'}</dd>
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
