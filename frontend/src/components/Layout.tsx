import { NavLink, Outlet } from 'react-router-dom';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { useApi } from '../api/useApi';
import { dateTime } from '../format';
import { SportToggle, useCarriedSearch, useSport } from './Selection';

const NAV = [
  { to: '/daily-learning', label: 'Daily learning', icon: '↻' },
  { to: '/', label: 'Game day', icon: '◫', end: true },
  { to: '/performance', label: 'Performance', icon: '↗' },
  { to: '/history', label: 'Prediction history', icon: '◷' },
];

export function Layout() {
  const [theme, setTheme] = useState<'dark' | 'light'>(() => {
    try { return localStorage.getItem('courtside-theme') === 'light' ? 'light' : 'dark'; }
    catch { return 'dark'; }
  });
  const [sport, setSport] = useSport();
  const carried = useCarriedSearch();
  const label = sport === 'nhl' ? 'NHL' : 'NBA';
  const meta = useApi((s) => api.meta(sport, s), [sport]);
  const mode = meta.state.status === 'success' ? meta.state.data.mode : null;
  const lastRefresh = meta.state.status === 'success' ? meta.state.data.last_refresh_at : null;
  const health = useApi((s) => api.health(s), []);
  const snapshot = health.state.status === 'success' ? health.state.data.data_snapshot_at : null;
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem('courtside-theme', theme); } catch { /* Theme still works for this session. */ }
  }, [theme]);
  return (
    <div className="app">
      <aside className="topbar">
        <NavLink to={`/${carried}`} className="brand"><span className="brand-mark" aria-hidden="true">◈</span> COURTSIDE<span className="brand-sub">{sport === 'nhl' ? 'HOCKEY' : 'BASKETBALL'} INTELLIGENCE</span></NavLink>
        <div className="nav-caption">WORKSPACE</div>
        <nav className="nav" aria-label="Main">
          {NAV.map((n) => (
            <NavLink key={n.to} to={`${n.to}${carried}`} end={n.end} className={({ isActive }) => (isActive ? 'active' : '')}>
              <span aria-hidden="true">{n.icon}</span>{n.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-note"><span className="league-mark">{label}</span><strong>The game, in perspective.</strong><p>Probabilities. Context. A record you can inspect.</p><span className="sidebar-status">Research preview</span></div>
      </aside>
      <div className="workspace">
        <header className="workspace-header">
          <span>{label} <span className="header-divider">/</span> Prediction dashboard</span>
          <div className="header-tools">
            <SportToggle value={sport} onChange={setSport} />
            {mode && <span className="preview-pill"><span /> {mode === 'demo' ? 'DEMO WORKSPACE' : 'REAL DATA'}</span>}
            <button className="theme-toggle" type="button" aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
              aria-pressed={theme === 'light'} onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>
              <span aria-hidden="true">{theme === 'dark' ? '☼' : '☾'}</span><span>{theme === 'dark' ? 'Light' : 'Dark'}</span>
            </button>
          </div>
        </header>
        {health.state.status === 'error' && <div className="demo-banner error-banner" role="alert"><strong>API NOT REACHABLE</strong><span>{health.state.error} Start the app with <code>sh scripts/dev.sh</code> from the repository folder.</span></div>}
        {snapshot && <div className="demo-banner" role="note"><strong>DEVELOPMENT COPY</strong><span>Results and games come from a copy of the live data taken {dateTime(snapshot)}; restart <code>scripts/dev.sh</code> to refresh it. Training status reads the live system directly.</span></div>}
        {mode === 'demo' && <div className="demo-banner" role="note"><strong>DEMO MODE</strong><span>Explore the app with synthetic games and predictions. These are not real NBA forecasts or results.</span></div>}
        {mode === 'real' && sport === 'nhl' && (
          <div className="demo-banner data-banner" role="note">
            <strong>REAL NHL DATA</strong>
            <span>Schedule and results from the NHL's own stats API{lastRefresh ? `, last refreshed ${dateTime(lastRefresh)}` : ''}. Winner forecasts come from an NHL Elo model; puck line, totals and the regulation result from a goals model. No goalie, injury or line-up information. Backtests are labelled Backtest; forecasts recorded before the game are labelled Live. No betting advice.</span>
          </div>
        )}
        {mode === 'real' && sport === 'nba' && (
          <div className="demo-banner data-banner" role="note">
            <strong>REAL NBA DATA</strong>
            <span>Schedule and results from ESPN's unofficial API{lastRefresh ? `, last refreshed ${dateTime(lastRefresh)}` : ''}. Forecasts come from an Elo model. Past seasons show retrospective backtests, labelled Backtest; forecasts recorded before tipoff are labelled Live. No betting advice.</span>
          </div>
        )}
        <main className="content"><Outlet /></main>
        <footer className="app-footer"><span>COURTSIDE / {label}</span><span>Every probability deserves context.</span></footer>
      </div>
    </div>
  );
}
