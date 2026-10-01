import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import type { GameSummary, Meta } from '../api/types';
import { useApi } from '../api/useApi';
import { ModeBadge, RunBadge, SourceList } from '../components/Badges';
import { ConfidenceFilter, confidenceOf, levelLabel, meetsConfidence, RangeRecords, useConfidence } from '../components/ConfidenceFilter';
import { ProbabilityBar } from '../components/ProbabilityBar';
import { AsyncView, Empty } from '../components/States';
import { calendarDate, FULL_DATE, pct, shiftDate, time } from '../format';

export function TodayPage() {
  const meta = useApi((s) => api.meta(s), []);
  return (
    <AsyncView state={meta.state} retry={meta.retry}>
      {(m) => <Slate meta={m} />}
    </AsyncView>
  );
}

function Slate({ meta }: { meta: Meta }) {
  const [params, setParams] = useSearchParams();
  const date = params.get('date') ?? meta.default_date;
  const setDate = (d: string) => {
    if (!d) return;
    const next = new URLSearchParams(params);
    next.set('date', d);
    setParams(next);
  };
  const [level, setLevel] = useConfidence();
  const { state, retry } = useApi((s) => api.games(date, s), [date]);

  return (
    <section>
      <div className="slate-hero">
        <div><p className="eyebrow">THE DAILY SLATE</p><h1>A clearer view<br />of the game<span className="accent">.</span></h1><p className="hero-copy">Explore the matchups. Compare the probabilities.<br />Follow the numbers beyond the final score.</p></div>
        <svg className="court-art" viewBox="0 0 420 280" fill="none" aria-hidden="true"><rect x="30" y="20" width="360" height="240" rx="4"/><path d="M210 20v240"/><circle cx="210" cy="140" r="45"/><path d="M30 55h68v170H30m360-170h-68v170h68M30 85h45v110H30m360-110h-45v110h45"/><path d="M30 32c180 0 180 216 0 216M390 32c-180 0-180 216 0 216"/><circle cx="55" cy="140" r="9"/><circle cx="365" cy="140" r="9"/><circle className="court-dot" cx="265" cy="91" r="7"/><circle className="court-dot muted-dot" cx="152" cy="182" r="5"/></svg>
      </div>
      <div className="page-head">
        <h2 className="slate-title">Game day <ModeBadge mode={meta.mode} /></h2>
        <div className="date-picker">
          <button type="button" onClick={() => setDate(shiftDate(date, -1))} aria-label="Previous day">‹</button>
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} aria-label="Game date" />
          <button type="button" onClick={() => setDate(shiftDate(date, 1))} aria-label="Next day">›</button>
          {date !== meta.default_date && (
            <button type="button" className="link-btn" onClick={() => setDate(meta.default_date)}>{meta.mode === 'demo' ? 'Demo today' : 'Next games'}</button>
          )}
        </div>
      </div>
      <p className="muted">{calendarDate(date, { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' })}</p>
      <ConfidenceFilter value={level} onChange={setLevel} />
      <RangeRecords level={level} realMode={meta.mode === 'real'} />
      <AsyncView state={state} retry={retry}>
        {(data) => (
          <>
            <SourceList sources={data.sources} />
            {data.games.length === 0 ? (
              <Empty>
                No games on this date.{' '}
                {meta.available_dates.length > 0 && <>{meta.mode === 'demo' ? 'Demo data' : 'Stored data'} covers {calendarDate(meta.available_dates[0], FULL_DATE)} – {calendarDate(meta.available_dates[meta.available_dates.length - 1], FULL_DATE)}. </>}
                <button type="button" className="link-btn" onClick={() => setDate(meta.default_date)}>{meta.mode === 'demo' ? 'Go to demo today' : 'Go to the next games'}</button>
              </Empty>
            ) : (
              <FilteredSlate games={data.games} level={level} showAll={() => setLevel(null)} />
            )}
          </>
        )}
      </AsyncView>
    </section>
  );
}

/** Games on the date whose forecast meets the confidence level. Games without a forecast only appear under All. */
function FilteredSlate({ games, level, showAll }: { games: GameSummary[]; level: number | null; showAll: () => void }) {
  const withForecast = games.filter((g) => g.prediction);
  const selected = level == null ? games : withForecast.filter((g) => meetsConfidence(g.prediction!.home_win_prob, level));
  return (
    <>
      <p className="small muted selection-count">
        {level == null
          ? <>{games.length} {games.length === 1 ? 'game' : 'games'}, {withForecast.length} with a forecast</>
          : <><strong>{selected.length}</strong> of {games.length} {games.length === 1 ? 'game' : 'games'} selected at {levelLabel(level)} model confidence ({withForecast.length} with a forecast)</>}
      </p>
      {selected.length === 0 ? (
        <Empty>
          No picks at {levelLabel(level)} model confidence on this date.{' '}
          {withForecast.length === 0 ? 'None of these games has a forecast yet. ' : ''}
          <button type="button" className="link-btn" onClick={showAll}>Show all {games.length} {games.length === 1 ? 'game' : 'games'}</button>
        </Empty>
      ) : (
        <ul className="game-grid">
          {selected.map((g) => <GameCard key={g.game_id} game={g} />)}
        </ul>
      )}
    </>
  );
}

const SEASON_LABEL: Record<GameSummary['season_type'], string> = {
  preseason: 'PRESEASON', regular: '', play_in: 'PLAY-IN', postseason: 'PLAYOFFS', other: 'EXHIBITION',
};

function statusLabel(g: GameSummary) {
  if (g.outcome) return 'FINAL';
  if (g.status === 'postponed' || g.status === 'canceled') return g.status.toUpperCase();
  if (g.status === 'in_progress') return 'IN PROGRESS';
  return time(g.tipoff);
}

function GameCard({ game: g }: { game: GameSummary }) {
  const p = g.prediction;
  const side = (home: boolean) => {
    if (g.outcome) return home ? g.outcome.home_score : g.outcome.away_score;
    if (p) return pct(home ? p.home_win_prob : 1 - p.home_win_prob);
    return '—';
  };
  return (
    <li className="card game-card">
      <Link to={`/games/${g.game_id}`} className="card-link">
        <div className="game-card-head">
          <span className="game-time">{statusLabel(g)}{SEASON_LABEL[g.season_type] && ` · ${SEASON_LABEL[g.season_type]}`}</span>
          {g.mode === 'demo' ? <RunBadge runType="demo" /> : p && <RunBadge runType={p.run_type} />}
        </div>
        <div className="team-line"><span className={`team-monogram team-${g.away.abbr}`}>{g.away.abbr}</span><div><strong>{g.away.name}</strong><span>Away</span></div><b>{side(false)}</b></div>
        <div className="team-line"><span className={`team-monogram team-${g.home.abbr}`}>{g.home.abbr}</span><div><strong>{g.home.name}</strong><span>Home</span></div><b>{side(true)}</b></div>
        {p ? (
          <>
            <div className="card-section-label">{p.run_type === 'backtest' ? 'BACKTEST WIN PROBABILITY' : 'MODEL WIN PROBABILITY'}</div>
            <ProbabilityBar home={g.home} away={g.away} homeProb={p.home_win_prob} />
            <p className="pick-line small">
              Pick: <strong>{p.home_win_prob >= 0.5 ? g.home.abbr : g.away.abbr}</strong>
              <span className="muted"> · model confidence {pct(confidenceOf(p.home_win_prob), 1)}</span>
            </p>
          </>
        ) : (
          <p className="forecast-note">{g.forecast_note ?? 'No forecast available.'}</p>
        )}
        <div className="card-bottom small muted">
          {g.market ? <>Market: {g.home.abbr} {pct(g.market.home_win_prob)}</> : <span className="warn">No market odds</span>}<span className="matchup-link">Matchup ↗</span>
        </div>
      </Link>
    </li>
  );
}
