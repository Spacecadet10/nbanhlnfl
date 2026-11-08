import { Link, useLocation, useSearchParams } from 'react-router-dom';
import { CanonicalBlock, CanonicalSummaryLine, GradeText } from '../components/Canonical';
import { ForecastPercentageRecord } from '../components/ThresholdOverview';
import { pct } from '../format';
import { api } from '../api/client';
import type { NflCapability, NflGame, NflMeta } from '../api/types';
import { useApi } from '../api/useApi';
import { useDataRevision } from '../api/freshness';
import { useCarriedSearch } from '../components/Selection';
import { AsyncView, Empty } from '../components/States';
import { gameRoute } from '../components/gameRoute';
import { TeamLogo } from '../components/TeamLogo';
import { EmptyForecastState, Icon, MetricCard, PageTitle } from '../components/ui';
import { calendarDate, dateTime, FULL_DATE, tipoffTime } from '../format';

// NFL is data-only (docs/NFL_PLAN.md): schedule, results and when each was learned, from the NFL store through /api/nfl/*.
// Nothing here shows or derives a forecast, odds, injury or metric; every such section states that it is not available
// and why (the reasons come from the API). The layout renders these screens in place of the NBA/NHL routes.

const SEASON_TYPE: Record<NflGame['season_type'], string> = {
  preseason: 'PRESEASON', regular: '', play_in: '', postseason: 'PLAYOFFS', other: 'EXHIBITION',
};

const seasonLabel = (season: number) => `${season}–${String(season + 1).slice(2)} season`;

function statusLabel(g: NflGame, date: string) {
  if (g.status === 'final') return g.winner === 'tie' ? 'FINAL · TIE' : 'FINAL';
  if (g.status === 'postponed' || g.status === 'canceled') return g.status.toUpperCase();
  if (g.status === 'in_progress') return 'IN PROGRESS';
  return tipoffTime(g.kickoff, date);
}

function learned(g: NflGame) {
  if (!g.result_available_at) return null;
  return g.result_availability_basis === 'assumed_tipoff_plus_4h'
    ? `Result imported later; counted as known 4 h after kickoff (${dateTime(g.result_available_at)})`
    : `Result first seen ${dateTime(g.result_available_at)}`;
}

/** Routes the NFL selection: Game day, a matchup, or the honest "not available" screens for everything else. */
export function NflScreens() {
  const { pathname } = useLocation();
  const rev = useDataRevision();
  const meta = useApi((s) => api.nflMeta(s), [], rev);
  return (
    <AsyncView state={meta.state} retry={meta.retry}>
      {(m) => {
        const game = gameRoute(pathname);
        if (game.kind === 'game') return <NflMatchup id={game.id} />;
        if (game.kind === 'malformed') return <Empty>This game link is damaged or incomplete. <Link to="/?sport=nfl">Go to NFL games</Link></Empty>;
        if (pathname === '/') return <NflGameDay meta={m} />;
        const page = pathname.startsWith('/performance') ? 'performance' : pathname.startsWith('/history') ? 'history'
          : pathname.startsWith('/daily-learning') ? 'research' : null;
        return page && page !== 'research' && m.capabilities.some(c => c.id === 'forecasts' && c.available) ? <NflRecords page={page} /> : page ? <NflUnavailable meta={m} page={page} /> : <Empty>Page not found. <Link to="/?sport=nfl">Go to NFL games</Link></Empty>;
      }}
    </AsyncView>
  );
}

function NflGameDay({ meta }: { meta: NflMeta }) {
  const [params, setParams] = useSearchParams();
  const date = params.get('date') ?? meta.default_date;
  const setDate = (d: string | null) => {
    if (!d) return;
    const next = new URLSearchParams(params);
    next.set('date', d);
    setParams(next);
  };
  return (
    <section>
      <PageTitle icon="football" plain title="NFL Games" sub={meta.capabilities.some(c => c.id === "forecasts" && c.available) ? "Live forecasts within two hours of kickoff · results update automatically." : "NFL schedule and results."}
        right={date && <GameDateControl date={date} dates={meta.game_dates} defaultDate={meta.default_date} onChange={setDate} />} />
      <ForecastUnavailable capabilities={meta.capabilities} />
      {date ? <NflSlate date={date} onChange={setDate} /> : <Empty>No NFL games are stored yet.</Empty>}
    </section>
  );
}

/** Steps between dates that have stored games (NFL and soccer play on a few days a week), plus a free date picker. */
export function GameDateControl({ date, dates, defaultDate, onChange }: {
  date: string; dates: string[]; defaultDate: string | null; onChange: (d: string | null) => void;
}) {
  const earlier = dates.filter((d) => d < date);
  const later = dates.filter((d) => d > date);
  return (
    <div className="date-control">
      <button type="button" className="date-step prev" disabled={!earlier.length} onClick={() => onChange(earlier[earlier.length - 1] ?? null)}
        aria-label="Previous game day"><Icon name="chevron" size={16} /></button>
      <button type="button" className="date-step" disabled={!later.length} onClick={() => onChange(later[0] ?? null)}
        aria-label="Next game day"><Icon name="chevron" size={16} /></button>
      <label className="date-button">
        <span>{calendarDate(date, FULL_DATE)}</span><Icon name="chevronDown" size={18} />
        <input type="date" value={date} onChange={(e) => onChange(e.target.value || null)} aria-label="Game date" />
      </label>
      {defaultDate && date !== defaultDate && (
        <button type="button" className="link-btn" onClick={() => onChange(defaultDate)}>Next games</button>
      )}
    </div>
  );
}

/** The capabilities that concern a single game. */
const GAME_LEVEL = ['forecasts', 'odds', 'injuries'];

function ForecastUnavailable({ capabilities }: { capabilities: NflCapability[] }) {
  if (capabilities.some(c => c.id === "forecasts" && c.available)) return <p className="notice">NFL live predictions active. Winner recommendations use the frozen 75% threshold. Spread and total markets are informational; injuries and weather are not included.</p>;
  return (
    <details className="notice nfl-notice">
      <summary><strong>NFL live forecasts are not active:</strong> schedule and results only, with no forecasts, odds or injuries. <span className="link-btn">Why</span></summary>
      <ul className="nfl-capabilities small">
        {capabilities.map((c) => <li key={c.id}><strong>{c.label}:</strong> {c.available ? "active" : "not available"}. <span className="muted">{c.reason}</span></li>)}
      </ul>
    </details>
  );
}

function NflThresholdRecord({ capabilities }: { capabilities: NflCapability[] }) {
  const forecast = capabilities.find((c) => c.id === 'forecasts');
  return <div className="card">
    <h2>Confidence thresholds by market</h2>
    <EmptyForecastState>No recorded NFL forecasts to classify by market and threshold.</EmptyForecastState>
    <p className="small muted">{forecast?.reason} Schedule results alone cannot provide prediction win rates.</p>
  </div>;
}

function NflSlate({ date, onChange }: { date: string; onChange: (d: string | null) => void }) {
  const rev = useDataRevision();
  const { state, retry } = useApi((s) => api.nflGames(date, s), [date], rev);
  return (
    <AsyncView state={state} retry={retry}>
      {(d) => {
        const finals = d.games.filter((g) => g.status === 'final').length;
        return (
          <>
            <div className="metrics today-metrics">
              <MetricCard icon="calendar" label="Games on this date" tone="lime" value={d.games.length}
                hint={d.games.length ? `${finals} final, ${d.games.length - finals} not final.` : 'No NFL game is scheduled on this date.'} />
              <MetricCard icon="layers" label="Games with forecasts" tone="dark" value={d.games.filter(g => g.forecasts?.some(c => c.record)).length} hint="Recorded within two hours of kickoff; older games are never filled in." />
              <MetricCard icon="history" label="Schedule refreshed" tone="lavender"
                value={d.last_refresh_at ? dateTime(d.last_refresh_at) : '—'} hint="Last ESPN NFL scoreboard import." />
            </div>
            {d.games.length === 0 ? (
              <Empty>
                No NFL games on {calendarDate(date, FULL_DATE)}.{' '}
                {d.prev_date && <button type="button" className="link-btn" onClick={() => onChange(d.prev_date)}>Previous game day ({calendarDate(d.prev_date)})</button>}
                {d.prev_date && d.next_date && ' · '}
                {d.next_date && <button type="button" className="link-btn" onClick={() => onChange(d.next_date)}>Next game day ({calendarDate(d.next_date)})</button>}
              </Empty>
            ) : <ul className="game-grid">{d.games.map((g) => <NflGameCard key={g.game_id} game={g} date={date} />)}</ul>}
          </>
        );
      }}
    </AsyncView>
  );
}

function NflGameCard({ game: g, date }: { game: NflGame; date: string }) {
  const carried = useCarriedSearch();
  const tags = [SEASON_TYPE[g.season_type], g.neutral_site ? 'NEUTRAL SITE' : ''].filter(Boolean);
  const score = (home: boolean) => (g.status === 'final' ? (home ? g.home_score : g.away_score) : null);
  const won = (home: boolean) => g.winner === (home ? 'home' : 'away');
  return (
    <li className="card game-card">
      <Link to={`/games/${encodeURIComponent(g.game_id)}${carried}`} className="card-link">
        <div className="game-card-head">
          <span className="game-time">{statusLabel(g, date)}{tags.map((t) => ` · ${t}`).join('')}</span>
        </div>
        <div className={`team-line${won(false) ? ' is-winner' : ''}`}><TeamLogo team={g.away} sport="nfl" size={44} /><div><strong>{g.away.name}</strong><span>{g.neutral_site ? 'Listed away' : 'Away'}</span></div><b>{score(false)}</b></div>
        <div className={`team-line${won(true) ? ' is-winner' : ''}`}><TeamLogo team={g.home} sport="nfl" size={44} /><div><strong>{g.home.name}</strong><span>{g.neutral_site ? 'Listed home' : 'Home'}</span></div><b>{score(true)}</b></div>
        <NflForecasts game={g} />
        <div className="card-bottom small muted">
          <span>{learned(g) ?? (g.status === 'scheduled' ? `Kickoff ${dateTime(g.kickoff)}`
            : g.status === 'in_progress' ? 'Live scores are not collected; the final score follows the game.'
            : `Last updated ${dateTime(g.updated_at)}`)}</span>
          <span className="matchup-link">Matchup <Icon name="arrow" size={14} /></span>
        </div>
      </Link>
    </li>
  );
}

function NflMatchup({ id }: { id: string }) {
  const carried = useCarriedSearch();
  const rev = useDataRevision();
  const { state, retry } = useApi((s) => api.nflGame(id, s), [id], rev);
  return (
    <section>
      <Link to={`/${carried}`} className="back">‹ NFL games</Link>
      <AsyncView state={state} retry={retry}>
        {(d) => {
          const g = d.game;
          const title = g.status === 'final'
            ? <>Final{g.winner === 'tie' ? ' (tie)' : ''}: {g.away.abbr} {g.away_score} – {g.home_score} {g.home.abbr}</>
            : <>Kickoff: {dateTime(g.kickoff)}{g.status !== 'scheduled' && ` · ${g.status.replace('_', ' ')}`}</>;
          return (
            <>
              <section className="hero" aria-label="Matchup">
                <TeamLogo team={g.away} sport="nfl" size={104} />
                <div className="hero-center">
                  <span className="league-chip">NFL</span>
                  <h2 className="hero-matchup">{g.away.name} <span>{g.neutral_site ? 'vs' : '@'}</span> {g.home.name}</h2>
                  <p className="hero-time">{title}</p>
                </div>
                <TeamLogo team={g.home} sport="nfl" size={104} />
              </section>
              <div className="nfl-detail-grid">
                <div className="card">
                  <h2>Game facts</h2>
                  <dl className="facts">
                    <dt>Season</dt><dd>{seasonLabel(g.season)}{SEASON_TYPE[g.season_type] ? ` · ${SEASON_TYPE[g.season_type].toLowerCase()}` : ' · regular season'}</dd>
                    <dt>Kickoff</dt><dd>{dateTime(g.kickoff)}</dd>
                    <dt>Status</dt><dd>{g.status === 'final' ? (g.winner === 'tie' ? 'Final, tie' : `Final, ${g.winner === 'home' ? g.home.name : g.away.name} won`) : g.status.replace('_', ' ')}</dd>
                    <dt>Site</dt><dd>{g.neutral_site ? 'Neutral site (no home field)' : `${g.home.name} home game`}</dd>
                    <dt>First listed</dt><dd>{dateTime(g.first_seen_at)}</dd>
                    <dt>Last updated</dt><dd>{dateTime(g.updated_at)}</dd>
                    {g.result_available_at && <><dt>Result known</dt><dd>{learned(g)}</dd></>}
                  </dl>
                  <p className="small muted">Source: ESPN's public NFL scoreboard. Team names are each franchise's current name, also for older games.</p>
                </div>
                <div className="card">
                  <h2>Forecast</h2>
                  <NflForecasts game={g} detail />
                  <ul className="nfl-capabilities small">
                    {d.capabilities.filter((c) => GAME_LEVEL.includes(c.id)).map((c) => <li key={c.id}><strong>{c.label}:</strong> {c.available ? "active" : "not available"}. <span className="muted">{c.reason}</span></li>)}
                  </ul>
                </div>
              </div>
              {!g.forecasts && <NflThresholdRecord capabilities={d.capabilities} />}
              <div className="card">
                <h2>Earlier meetings</h2>
                <p className="small muted">Final regular-season and playoff results between these teams before this kickoff, newest first.</p>
                <ResultTable games={d.head_to_head} empty="No earlier meeting is stored." />
              </div>
              <div className="nfl-detail-grid">
                {([[g.away, d.away_recent], [g.home, d.home_recent]] as const).map(([team, games]) => (
                  <div className="card" key={team.team_id}>
                    <h2><span className="team-cell"><TeamLogo team={team} sport="nfl" size={28} />{team.name}: last results</span></h2>
                    <p className="small muted">Before this kickoff (up to five).</p>
                    <ResultTable games={games} team={team.team_id} empty="No earlier result is stored." />
                  </div>
                ))}
              </div>
            </>
          );
        }}
      </AsyncView>
    </section>
  );
}

/** Final results as a table; with `team`, each row also says W/L/T for that team. */
function ResultTable({ games, team, empty }: { games: NflGame[]; team?: string; empty: string }) {
  const carried = useCarriedSearch();
  if (!games.length) return <p className="muted">{empty}</p>;
  const outcome = (g: NflGame) => {
    if (!team || !g.winner) return null;
    if (g.winner === 'tie') return <span className="muted">T</span>;
    return (g.winner === 'home') === (g.home.team_id === team) ? <span className="ok">W</span> : <span className="bad">L</span>;
  };
  return (
    <div className="table-wrap"><table className="table compare">
      <thead><tr><th>Date</th><th>Game</th><th>Score (away–home)</th>{team && <th>Result</th>}</tr></thead>
      <tbody>
        {games.map((g) => (
          <tr key={g.game_id}>
            <td>{new Date(g.kickoff).toLocaleDateString([], FULL_DATE)}</td>
            <td><Link to={`/games/${encodeURIComponent(g.game_id)}${carried}`}>{g.away.abbr} {g.neutral_site ? 'vs' : '@'} {g.home.abbr}</Link>
              {SEASON_TYPE[g.season_type] && <span className="muted small"> · {SEASON_TYPE[g.season_type].toLowerCase()}</span>}</td>
            <td>{g.away_score}–{g.home_score}</td>
            {team && <td>{outcome(g)}</td>}
          </tr>
        ))}
      </tbody>
    </table></div>
  );
}

const PAGES = {
  performance: { icon: 'performance', title: 'NFL Performance', capability: 'performance' },
  history: { icon: 'history', title: 'NFL Prediction History', capability: 'history' },
  research: { icon: 'learning', title: 'NFL Daily Learning', capability: 'research' },
} as const;

/** A page with nothing to show for the NFL (Daily Learning always; Performance and History while live forecasts are not
 *  active): say so, and show what is stored. */
function NflUnavailable({ meta, page }: { meta: NflMeta; page: keyof typeof PAGES }) {
  const carried = useCarriedSearch();
  const p = PAGES[page];
  const cap = meta.capabilities.find((c) => c.id === p.capability);
  const first = meta.seasons[0];
  const last = meta.seasons[meta.seasons.length - 1];
  const forecastsActive = meta.capabilities.some((c) => c.id === 'forecasts' && c.available);
  return (
    <section>
      <PageTitle icon={p.icon} title={p.title} sub={forecastsActive
        ? 'Not available for the NFL. NFL live forecasts and their record are on Game Day, Prediction History and Performance.'
        : 'Not available: NFL live forecasts are not active for this data store.'} />
      <Empty>
        <strong>{cap?.label ?? p.title}: not available.</strong> {cap?.reason}{' '}
        <Link to={`/${carried}`}>See the NFL schedule and results</Link>.
      </Empty>
      {!forecastsActive && <NflThresholdRecord capabilities={meta.capabilities} />}
      <div className="metrics today-metrics">
        <MetricCard icon="calendar" label="Seasons stored" tone="lime" value={meta.seasons.length || '—'}
          hint={first != null ? `${seasonLabel(first)} to ${seasonLabel(last)} (regular season and playoffs).` : 'No season stored yet.'} />
        <MetricCard icon="layers" label="Games stored" tone="dark" value={meta.games_stored.toLocaleString()}
          hint="Every listed game, including preseason and the Pro Bowl." />
        <MetricCard icon="history" label="Schedule refreshed" tone="lavender" value={meta.last_refresh_at ? dateTime(meta.last_refresh_at) : '—'}
          hint="Last ESPN NFL scoreboard import." />
      </div>
    </section>
  );
}

function NflForecasts({ game: g, detail = false }: { game: NflGame; detail?: boolean }) {
  if (!g.forecasts) return <p className="forecast-note">NFL live forecasts have not been activated.</p>;
  if (!g.forecasts.some(c => c.record)) {
    const first = g.forecasts[0];
    return <p className="forecast-note">{first?.state === 'upcoming'
      ? `Forecast window opens ${dateTime(new Date(new Date(g.kickoff).getTime()-2*60*60*1000).toISOString())}.`
      : first?.reason ?? 'No forecast was recorded for this game.'}</p>;
  }
  return <>
    <div className="notice"><strong>Recommended pick: {g.recommended_pick ?? 'No strong pick'}</strong>
      <p className="small">Winner requires at least 75% model probability under the frozen NFL rule. Other markets are informational.</p></div>
    {g.forecasts.map(c => <CanonicalBlock key={c.market} c={c} home={g.home} away={g.away} detail={detail} />)}
  </>;
}

function NflRecords({ page }: { page: 'performance' | 'history' }) {
  const rev = useDataRevision();
  const { state, retry } = useApi(s => api.nflRecord(s), [], rev);
  const carried = useCarriedSearch();
  return <section><PageTitle icon={page === 'history' ? 'history' : 'performance'} title={page === 'history' ? 'NFL Prediction History' : 'NFL Performance'}
    sub="Prospective forecasts only. Exact percentages stay separate by market, side, model and line source." />
    <AsyncView state={state} retry={retry}>{r => <>
      <p className="notice">Live recording {r.recording_began_at ? `began ${dateTime(r.recording_began_at)}` : 'has no picks yet. The first forecast appears within two hours of kickoff.'}</p>
      {r.historical_test && <details className="card"><summary>Separate historical test · frozen winner 75%+</summary>
        <p>{r.historical_test.won} wins, {r.historical_test.graded-r.historical_test.won} losses ({r.historical_test.graded} picks; {pct(r.historical_test.win_rate,1)}).
        95% interval {pct(r.historical_test.ci95[0],1)}–{pct(r.historical_test.ci95[1],1)}. These games are excluded from the live records below.</p></details>}
      {r.markets.map(m => <div className="card" key={m.market}><h2>{m.title}</h2><CanonicalSummaryLine s={m.summary} />
        {!m.rows.length ? <p className="muted">No started NFL forecast in this market yet.</p> :
          <div className="table-wrap"><table className="table"><thead><tr><th>Game</th><th>Recorded pick</th><th>Result</th><th>Exact percentage record</th></tr></thead><tbody>
            {m.rows.map(row => <tr key={row.game_id}><td><Link to={`/games/${encodeURIComponent(row.game_id)}${carried}`}>{dateTime(row.tipoff)} · {row.game_id}</Link></td>
              <td>{row.forecast.record?.pick_label} · {pct(row.forecast.record?.confidence,1)}</td><td><GradeText grade={row.forecast.grade} /></td>
              <td><ForecastPercentageRecord history={row.forecast.percentage_history} grade={row.forecast.grade} opposite={row.forecast.record?.opposite_pick_label} startAt={row.forecast.record?.start_at} /></td></tr>)}
          </tbody></table></div>}
      </div>)}
    </>}</AsyncView></section>;
}
