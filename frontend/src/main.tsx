import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Link, Route, Routes } from 'react-router-dom';
import { Layout } from './components/Layout';
import { Empty } from './components/States';
import { GamePage } from './pages/GamePage';
import { HistoryPage } from './pages/HistoryPage';
import { PerformancePage } from './pages/PerformancePage';
import { TodayPage } from './pages/TodayPage';
import './styles.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<TodayPage />} />
          <Route path="games/:gameId" element={<GamePage />} />
          <Route path="performance" element={<PerformancePage />} />
          <Route path="history" element={<HistoryPage />} />
          <Route path="*" element={<Empty>Page not found. <Link to="/">Go to today's games</Link></Empty>} />
        </Route>
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
