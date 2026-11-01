import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { useApi } from '../api/useApi';
import { dateTime } from '../format';
import { SportToggle, useCarriedSearch, useSport } from './Selection';
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
  const [sport, setSport] = useSport();
  const carried = useCarriedSearch();
  const location = useLocation();
  const label = sport === 'nhl' ? 'NHL' : 'NBA';
  const meta = useApi((s) => api.meta(sport, s), [sport]);
  const mode = meta.state.status === 'success' ? meta.state.data.mode : null;
  const lastRefresh = meta.state.status === 'success' ? meta.state.data.last_refresh_at : null;
  const health = useApi((s) => api.health(s), []);
  const snapshot = health.state.status === 'success' ? health.state.data.data_snapshot_at : null;
  const section = SECTION.find(([re]) => re.test(location.pathname))?.[1] ?? 'Prediction Dashboard';
  const onGame = location.pathname.startsWith('/games/');
  const [lastGame, setLastGame] = useState<string | null>(() => {
    try { return sessionStorage.getItem(LAST_GAME); } catch { return null; }
  });
  useEffect(() => {
    if (!onGame) return;
    setLastGame(location.pathname);
    try { sessionStorage.setItem(LAST_GAME, location.pathname); } catch { /* the link then falls back to Game day */ }
  }, [onGame, location.pathname]);
  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  const choose = (t: Theme) => {
    setTheme(t);
    try { localStorage.setItem(THEME_KEY, t); } catch { /* Theme still works for this session. */ }
  };
  const next: Theme = theme === 'dark' ? 'light' : 'dark';
  return (
    <div className="app">
      <aside className="topbar">
        <div className="brand-block">
          <NavLink to={`/${carried}`} className="brand">
            <svg className="brand-mark" viewBox="0 0 40 40" aria-hidden="true">
              <path d="M4 33C14 31 27 21 36 6c-4 12-15 22-27 26 6-1 14-5 20-12C22 30 12 34 4 33z" fill="#A3E635" />
              <circle cx="31" cy="9" r="3.2" fill="#F8FAFC" />
            </svg>
            COURTSIDE<span className="brand-sub">{sport === 'nhl' ? 'HOCKEY' : 'BASKETBALL'} INTELLIGENCE</span>
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
          <span className="league-mark">{label}</span><strong>The game, in perspective.</strong>
          <span className="sidebar-status">Research preview</span>
        </div>
      </aside>
      <div className="workspace">
        <header className="workspace-header">
          <span className="breadcrumb">{label} <span className="header-divider">/</span> <strong>{section}</strong></span>
          <div className="header-tools">
            <SportToggle value={sport} onChange={setSport} />
            {mode === 'demo' && <span className="preview-pill is-demo"><span /> Demo workspace</span>}
            {mode === 'real' && (
              <details className="source-pop">
                <summary className="preview-pill" title="Where the data comes from"><span /> Real data</summary>
                <div className="source-pop-panel" role="note">
                  <strong>REAL {label} DATA</strong>
                  {sport === 'nhl'
                    ? <p>Schedule and results from the NHL's own stats API{lastRefresh ? `, last refreshed ${dateTime(lastRefresh)}` : ''}. Winner forecasts come from an NHL Elo model; puck line, totals and the regulation result from a goals model. No goalie, injury or line-up information.</p>
                    : <p>Schedule and results from ESPN's unofficial API{lastRefresh ? `, last refreshed ${dateTime(lastRefresh)}` : ''}. Forecasts come from an Elo model.</p>}
                  <p>Backtests are labelled Backtest; forecasts recorded before the game are labelled Live. Team logos are loaded from {sport === 'nhl' ? 'the NHL' : 'ESPN'}. No betting advice.</p>
                </div>
              </details>
            )}
            <button className="theme-toggle" type="button" aria-label={`Switch to ${next} theme`} title={`Switch to ${next} theme`}
              aria-pressed={theme === 'dark'} onClick={() => choose(next)}>
              <Icon name={theme === 'dark' ? 'moon' : 'sun'} size={20} />
            </button>
          </div>
        </header>
        {health.state.status === 'error' && <div className="demo-banner error-banner" role="alert"><strong>API NOT REACHABLE</strong><span>{health.state.error} Start the app with <code>sh scripts/dev.sh</code> from the repository folder.</span></div>}
        {snapshot && <div className="demo-banner" role="note"><strong>DEVELOPMENT COPY</strong><span>Results and games come from a copy of the live data taken {dateTime(snapshot)}; restart <code>scripts/dev.sh</code> to refresh it. Training status reads the live system directly.</span></div>}
        {mode === 'demo' && <div className="demo-banner" role="note"><strong>DEMO MODE</strong><span>Explore the app with synthetic games and predictions. These are not real NBA forecasts or results.</span></div>}
        <main className="content"><Outlet /></main>
        <footer className="app-footer"><span>COURTSIDE / {label}</span><span>Every probability deserves context.</span></footer>
      </div>
    </div>
  );
}
