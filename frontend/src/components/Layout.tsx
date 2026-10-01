import { NavLink, Outlet } from 'react-router-dom';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { useApi } from '../api/useApi';
import { dateTime } from '../format';

const NAV = [
  { to: '/', label: 'Game day', icon: '◫', end: true },
  { to: '/performance', label: 'Performance', icon: '↗' },
  { to: '/history', label: 'Prediction history', icon: '◷' },
];

export function Layout() {
  const [theme, setTheme] = useState<'dark' | 'light'>(() => {
    try { return localStorage.getItem('courtside-theme') === 'light' ? 'light' : 'dark'; }
    catch { return 'dark'; }
  });
  const meta = useApi((s) => api.meta(s), []);
  const mode = meta.state.status === 'success' ? meta.state.data.mode : null;
  const lastRefresh = meta.state.status === 'success' ? meta.state.data.last_refresh_at : null;
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem('courtside-theme', theme); } catch { /* Theme still works for this session. */ }
  }, [theme]);
  return (
    <div className="app">
      <aside className="topbar">
        <NavLink to="/" className="brand"><span className="brand-mark" aria-hidden="true">◈</span> COURTSIDE<span className="brand-sub">BASKETBALL INTELLIGENCE</span></NavLink>
        <div className="nav-caption">WORKSPACE</div>
        <nav className="nav" aria-label="Main">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => (isActive ? 'active' : '')}>
              <span aria-hidden="true">{n.icon}</span>{n.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-note"><span className="league-mark">NBA</span><strong>The game, in perspective.</strong><p>Probabilities. Context. A record you can inspect.</p><span className="sidebar-status">Research preview</span></div>
      </aside>
      <div className="workspace">
        <header className="workspace-header">
          <span>NBA <span className="header-divider">/</span> Prediction dashboard</span>
          <div className="header-tools">
            {mode && <span className="preview-pill"><span /> {mode === 'demo' ? 'DEMO WORKSPACE' : 'REAL DATA'}</span>}
            <button className="theme-toggle" type="button" aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
              aria-pressed={theme === 'light'} onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>
              <span aria-hidden="true">{theme === 'dark' ? '☼' : '☾'}</span><span>{theme === 'dark' ? 'Light' : 'Dark'}</span>
            </button>
          </div>
        </header>
        {mode === 'demo' && <div className="demo-banner" role="note"><strong>DEMO MODE</strong><span>Explore the app with synthetic games and predictions. These are not real NBA forecasts or results.</span></div>}
        {mode === 'real' && (
          <div className="demo-banner data-banner" role="note">
            <strong>REAL NBA DATA</strong>
            <span>Schedule and results from ESPN's unofficial API{lastRefresh ? `, last refreshed ${dateTime(lastRefresh)}` : ''}. Forecasts come from an Elo model. Past seasons show retrospective backtests, labelled Backtest; forecasts recorded before tipoff are labelled Live. No betting advice.</span>
          </div>
        )}
        <main className="content"><Outlet /></main>
        <footer className="app-footer"><span>COURTSIDE / NBA</span><span>Every probability deserves context.</span></footer>
      </div>
    </div>
  );
}
