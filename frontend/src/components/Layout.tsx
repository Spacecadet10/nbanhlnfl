import { Link, NavLink, Outlet, useLocation } from 'react-router-dom';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { League } from '../api/types';
import { FreshnessContext, useFreshnessPoll } from '../api/freshness';
import { useApi } from '../api/useApi';
import { dateTime } from '../format';
import { NflScreens } from '../pages/NflPages';
import { SoccerScreens } from '../pages/SoccerPages';
import { FreshnessBar } from './Freshness';
import { gameRoute } from './gameRoute';
import { Empty, PageErrorBoundary } from './States';
import { CompetitionSelect, SportToggle, useCarriedSearch, useCompetition, useLeague, useSoccerMatchUrlSync } from './Selection';
import { Icon } from './ui';

const NAV = [
  { to: '/daily-learning', label: 'Daily Learning', icon: 'learning' },
  { to: '/', label: 'Game Day', icon: 'gameday', end: true },
  { to: '/performance', label: 'Performance', icon: 'performance' },
  { to: '/history', label: 'Prediction History', icon: 'history' },
];

const SECTION: [RegExp, string][] = [
  [/^\/games\//, 'Prediction Dashboard'], [/^\/daily-learning/, 'Daily Learning'], [/^\/performance/, 'Performance'],
  [/^\/history/, 'Prediction History'], [/^\/$/, 'Game Day'],
];

/** The last game page opened in this tab, so "Prediction dashboard" can return to it (Game day until one is opened). */
const LAST_GAME = 'courtside-last-game';

type Theme = 'light' | 'dark';
const THEME_KEY = 'courtside-theme-choice';

export function Layout() {
  // Light is the primary theme; dark stays available. Only an explicit choice is stored (THEME_KEY): the old key was written
  // on every visit with the old dark default, so it says nothing about what a user chose. index.html applies it before paint.
  const [theme, setTheme] = useState<Theme>(() => {
    try { return localStorage.getItem(THEME_KEY) === 'dark' ? 'dark' : 'light'; }
    catch { return 'light'; }
  });
  // `sport` is the selected league: NBA and NHL are served; NFL and soccer are data-only with their own screens.
  const [sport, setSport] = useLeague();
  const [competition, setCompetition] = useCompetition();
  useSoccerMatchUrlSync();
  const carried = useCarriedSearch();
  const location = useLocation();
  const label = sport.toUpperCase();
  const fresh = useFreshnessPoll(sport);
  const meta = useApi((s) => (sport === 'nfl' ? api.nflMeta(s) : sport === 'soccer' ? api.soccerMeta(competition, s) : api.meta(sport, s))
    .then((m) => ({ mode: m.mode, last_refresh_at: m.last_refresh_at,
      forecasts: 'capabilities' in m && m.capabilities.some((c) => c.id === 'forecasts' && c.available) })), [sport, competition], fresh.changes);
  const mode = meta.state.status === 'success' ? meta.state.data.mode : null;
  const lastRefresh = meta.state.status === 'success' ? meta.state.data.last_refresh_at : null;
  const nflForecasts = sport === 'nfl' && meta.state.status === 'success' && meta.state.data.forecasts;
  const health = useApi((s) => api.health(s), []);
  const snapshot = health.state.status === 'success' ? health.state.data.data_snapshot_at : null;
  const section = SECTION.find(([re]) => re.test(location.pathname))?.[1] ?? 'Prediction Dashboard';
  const route = gameRoute(location.pathname);
  const onGame = route.kind !== 'none';
  const [lastGames, setLastGames] = useState<Record<League, string | null>>(() => {
    try {
      return { nba: sessionStorage.getItem(`${LAST_GAME}-nba`), nhl: sessionStorage.getItem(`${LAST_GAME}-nhl`),
        nfl: sessionStorage.getItem(`${LAST_GAME}-nfl`), soccer: sessionStorage.getItem(`${LAST_GAME}-soccer`) };
    } catch { return { nba: null, nhl: null, nfl: null, soccer: null }; }
  });
  const lastGame = lastGames[sport];
  useEffect(() => {
    if (route.kind !== 'game') return;   // a malformed link is never remembered as the last game
    setLastGames((previous) => ({ ...previous, [sport]: location.pathname }));
    try { sessionStorage.setItem(`${LAST_GAME}-${sport}`, location.pathname); } catch { /* the link then falls back to Game day */ }
  }, [route.kind, location.pathname, sport]);
  useEffect(() => { window.scrollTo(0, 0); }, [location.pathname, sport]);
  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  const choose = (t: Theme) => {
    setTheme(t);
    try { localStorage.setItem(THEME_KEY, t); } catch { /* Theme still works for this session. */ }
  };
  const next: Theme = theme === 'dark' ? 'light' : 'dark';
  return (
    <div className={`app sport-${sport}`}>
      <aside className="topbar">
        <div className="brand-block">
          <NavLink to={`/${carried}`} className="brand">
            <svg className="brand-mark" viewBox="0 0 40 40" aria-hidden="true">
              <ellipse cx="21" cy="18" rx="19" ry="8" transform="rotate(-46 21 18)" fill="none" stroke="#F8FAFC" strokeWidth="1.2" />
              <path d="M3 35C15 34 28 19 35 8l-1 10C26 29 13 38 3 35Z" fill="#A3E635" />
              <circle cx="29" cy="12" r="5" fill="#C084FC" stroke="#1E1B4B" />
              <path d="M25 9l8 6m-8-1l7-5" fill="none" stroke="#1E1B4B" strokeWidth=".9" />
            </svg>
            COURTSIDE<span className="brand-sub">{sport === 'nhl' ? 'HOCKEY' : sport === 'nfl' ? 'FOOTBALL' : sport === 'soccer' ? 'SOCCER' : 'BASKETBALL'} INTELLIGENCE</span>
          </NavLink>
        </div>
        <nav className="nav" aria-label="Main">
          <span className="nav-caption"><Icon name="workspace" />Workspace</span>
          {NAV.slice(0, 3).map((n) => (
            <NavLink key={n.to} to={`${n.to}${carried}`} end={n.end} className={({ isActive }) => (isActive ? 'active' : '')}>
              <Icon name={n.icon} />{n.label}
            </NavLink>
          ))}
          <NavLink to={onGame ? `${location.pathname}${carried}` : lastGame ? `${lastGame}${carried}` : `/${carried}`}
            className={() => (onGame ? 'active' : '')} aria-current={onGame ? 'page' : undefined} title={lastGame || onGame ? 'The game you opened last' : 'Open a game from Game day'}>
            <Icon name="dashboard" />Prediction Dashboard
          </NavLink>
          {NAV.slice(3).map((n) => (
            <NavLink key={n.to} to={`${n.to}${carried}`} className={({ isActive }) => (isActive ? 'active' : '')}>
              <Icon name={n.icon} />{n.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-note">
          <div className="sidebar-note-copy"><span className="league-mark">{label}</span><strong>The game, in perspective.</strong></div>
        </div>
      </aside>
      <div className="workspace">
        <header className="workspace-header">
          <span className="breadcrumb">{label} <span className="header-divider">/</span> <strong>{section}</strong></span>
          <div className={`header-tools${sport === 'soccer' ? ' has-competition' : ''}`}>
            <SportToggle value={sport} onChange={setSport} />
            {sport === 'soccer' && <CompetitionSelect value={competition} onChange={setCompetition} />}
            {mode === 'demo' && <span className="preview-pill is-demo"><span /> Demo workspace</span>}
            {mode === 'real' && (
              <details className="source-pop">
                <summary className="preview-pill" title="Where the data comes from"><span /> Real data</summary>
                <div className="source-pop-panel" role="note">
                  <strong>REAL {label} DATA</strong>
                  {sport === 'soccer'
                    ? <p>Results history from openfootball (CC0) where this data folder has it, with stored research backtest forecasts (retrospective, never live); otherwise fixtures and results from ESPN's public soccer scoreboards{lastRefresh ? ` (this competition last imported ${dateTime(lastRefresh)})` : ''}. No live soccer forecast, odds or injury information exists.</p>
                    : sport === 'nfl'
                    ? <p>Schedule and results from ESPN's public NFL scoreboard{lastRefresh ? `, last imported ${dateTime(lastRefresh)}` : ''}. {nflForecasts
                      ? 'Live forecasts are recorded within two hours of kickoff for regular-season and playoff games: the winner from an NFL Elo model; spread, total and team totals from a points model, against a bookmaker line collected 2–4 hours before kickoff when there is one, otherwise a model line. No injury or weather information.'
                      : 'NFL live forecasts are not active for this data store, so no NFL forecast, odds or record is shown. No injury information.'}</p>
                    : sport === 'nhl'
                    ? <p>Schedule and results from the NHL's own stats API{lastRefresh ? `, last refreshed ${dateTime(lastRefresh)}` : ''}. Winner forecasts come from an NHL Elo model; puck line, totals and the regulation result from a goals model. No goalie, injury or line-up information.</p>
                    : <p>Schedule and results from ESPN's unofficial API{lastRefresh ? `, last refreshed ${dateTime(lastRefresh)}` : ''}. Forecasts come from an Elo model.</p>}
                  <p>{sport === 'nfl' || sport === 'soccer' ? '' : 'Backtests are labelled Backtest; forecasts recorded before the game are labelled Live. '}Team logos are loaded from {sport === 'nhl' ? 'the NHL' : 'ESPN'}. No betting advice.</p>
                </div>
              </details>
            )}
            <button className="theme-toggle" type="button" aria-label={`Switch to ${next} theme`} title={`Switch to ${next} theme`}
              aria-pressed={theme === 'dark'} onClick={() => choose(next)}>
              <Icon name={theme === 'dark' ? 'moon' : 'sun'} size={20} />
            </button>
          </div>
        </header>
        {health.state.status === 'error' && <div className="demo-banner error-banner" role="alert"><strong>API NOT REACHABLE</strong><span>{health.state.error} Start the app with <code>sh scripts/dev.sh</code> from the repository folder.</span><button type="button" onClick={() => window.location.reload()}>Reconnect</button></div>}
        {snapshot && (fresh.data?.mode === 'auto_snapshot'
          ? <div className="demo-banner" role="note"><strong>DEVELOPMENT COPY</strong><span>Games, results and forecasts come from a copy of the live data, republished automatically when it changes (checked every {Math.round((fresh.data.publish_interval_seconds ?? 60))} s; last copy {dateTime(fresh.data.published_at ?? snapshot)}). Training status reads the live system directly.</span></div>
          : fresh.data?.mode === 'frozen_snapshot'
          ? <div className="demo-banner" role="note"><strong>DEVELOPMENT COPY</strong><span>Results and games come from a copy of the live data taken {dateTime(snapshot)}. It will not update automatically; restart <code>scripts/dev.sh</code> to refresh it. Training status reads the live system directly.</span></div>
          : <div className="demo-banner" role="note"><strong>DEVELOPMENT COPY</strong><span>Results and games come from a copy of the live data (last copied {dateTime(snapshot)}). Training status reads the live system directly.</span></div>)}
        {mode === 'demo' && <div className="demo-banner" role="note"><strong>DEMO MODE</strong><span>Explore the app with synthetic games and predictions. These are not real NBA forecasts or results.</span></div>}
        <FreshnessBar check={fresh} />
        <FreshnessContext.Provider value={fresh}>
          <main className="content">
            <PageErrorBoundary key={location.pathname + location.search}
              fallback={<Empty>This page could not be displayed. <Link to={`/${carried}`}>Go to today's games</Link></Empty>}>
              {route.kind === 'malformed'
                ? <Empty>This game link is damaged or incomplete, so no game can be opened. <Link to={`/${carried}`}>Go to today's games</Link></Empty>
                : sport === 'nfl' ? <NflScreens /> : sport === 'soccer' ? <SoccerScreens /> : <Outlet />}
            </PageErrorBoundary>
          </main>
        </FreshnessContext.Provider>
        <footer className="app-footer"><span>COURTSIDE / {label}</span><span>Every probability deserves context.</span></footer>
      </div>
    </div>
  );
}
