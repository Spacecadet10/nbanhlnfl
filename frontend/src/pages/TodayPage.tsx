import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import type { GameSummary, Meta, Policy, SlateSelection } from '../api/types';
import { useApi } from '../api/useApi';
import { ModeBadge, NearTipoffBadge, RunBadge, SourceList } from '../components/Badges';
import { ConfidenceFilter, levelLabel, RangeRecords, useConfidence } from '../components/ConfidenceFilter';
import { PolicyToggle, usePolicy } from '../components/RunTypeToggle';
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
  const [policyChoice, setPolicy] = usePolicy();
  const policy: Policy = meta.mode === 'real' ? policyChoice : 'pregame';
  const { state, retry } = useApi((s) => api.games(date, policy, level, s), [date, policy, level]);

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
      {meta.mode === 'real' && (
        <div className="policy-row">
          <PolicyToggle value={policy} onChange={setPolicy} />
          <p className="small muted">
            {policy === 'near_tipoff'
              ? 'Showing near-tipoff forecasts. Picks, the confidence filter and the record below all use them.'
              : 'Showing night-before forecasts. Picks, the confidence filter and the record below all use them.'}
          </p>
        </div>
      )}
      <ConfidenceFilter value={level} onChange={setLevel} />
      <RangeRecords level={level} realMode={meta.mode === 'real'} policy={policy} />
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
              <FilteredSlate games={data.games} selection={data.selection} showAll={() => setLevel(null)} />
            )}
          </>
        )}
      </AsyncView>
    </section>
  );
}

const POLICY_NAME: Record<Policy, string> = { pregame: 'night-before', near_tipoff: 'near-tipoff' };

/** The API decides which games are selected: only the chosen policy's forecast counts, never the other one. */
function FilteredSlate({ games, selection: sel, showAll }: { games: GameSummary[]; selection: SlateSelection; showAll: () => void }) {
  const level = sel.min_confidence;
  const selected = level == null ? games : games.filter((g) => g.slate_pick?.meets_confidence);
  const noun = games.length === 1 ? 'game' : 'games';
  return (
    <>
      <p className="small muted selection-count">
        {level == null
          ? <>{sel.games} {noun}, {sel.with_forecast} with a {POLICY_NAME[sel.policy]} forecast</>
          : <><strong>{sel.selected}</strong> of {sel.games} {noun} selected at {levelLabel(level)} model confidence ({sel.with_forecast} with a {POLICY_NAME[sel.policy]} forecast)</>}
      </p>
      {selected.length === 0 ? (
        <Empty>
          No {POLICY_NAME[sel.policy]} picks at {levelLabel(level)} model confidence on this date.{' '}
          {sel.with_forecast === 0 ? `None of these games has a ${POLICY_NAME[sel.policy]} forecast yet. ` : ''}
          <button type="button" className="link-btn" onClick={showAll}>Show all {sel.games} {noun}</button>
        </Empty>
      ) : (
        <ul className="game-grid">
          {selected.map((g) => <GameCard key={g.game_id} game={g} policy={sel.policy} />)}
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

function GameCard({ game: g, policy }: { game: GameSummary; policy: Policy }) {
  const p = g.prediction;
  const n = g.near_tipoff;
  const near = policy === 'near_tipoff';
  const pick = g.slate_pick;   // the selected policy's pick; the other forecast is only mentioned below
  const side = (home: boolean) => {
    if (g.outcome) return home ? g.outcome.home_score : g.outcome.away_score;
    if (pick) return pct(home ? pick.home_win_prob : 1 - pick.home_win_prob);
    return '—';
  };
  const other = near ? p : n;
  return (
    <li className="card game-card">
      <Link to={`/games/${g.game_id}`} className="card-link">
        <div className="game-card-head">
          <span className="game-time">{statusLabel(g)}{SEASON_LABEL[g.season_type] && ` · ${SEASON_LABEL[g.season_type]}`}</span>
          {g.mode === 'demo' ? <RunBadge runType="demo" /> : near ? n && <NearTipoffBadge /> : p && <RunBadge runType={p.run_type} />}
        </div>
        <div className="team-line"><span className={`team-monogram team-${g.away.abbr}`}>{g.away.abbr}</span><div><strong>{g.away.name}</strong><span>Away</span></div><b>{side(false)}</b></div>
        <div className="team-line"><span className={`team-monogram team-${g.home.abbr}`}>{g.home.abbr}</span><div><strong>{g.home.name}</strong><span>Home</span></div><b>{side(true)}</b></div>
        {pick ? (
          <>
            <div className="card-section-label">
              {near ? 'NEAR-TIPOFF WIN PROBABILITY' : p?.run_type === 'backtest' ? 'BACKTEST WIN PROBABILITY' : 'MODEL WIN PROBABILITY'}
            </div>
            <ProbabilityBar home={g.home} away={g.away} homeProb={pick.home_win_prob} />
            <p className="pick-line small">
              Pick: <strong>{pick.pick === 'home' ? g.home.abbr : g.away.abbr}</strong>
              <span className="muted"> · model confidence {pct(pick.confidence, 1)}</span>
              {near && n && <span className="muted"> · made {Math.round(n.minutes_before_tipoff)} min before tipoff</span>}
              {near && n?.correct != null && (n.correct ? <span className="ok"> · Correct</span> : <span className="bad"> · Wrong</span>)}
            </p>
          </>
        ) : (
          <p className="forecast-note">{near ? 'No near-tipoff forecast was recorded for this game.' : g.forecast_note ?? 'No forecast available.'}</p>
        )}
        {other && (
          <p className="other-forecast small muted">
            {near ? 'Night-before forecast' : 'Near-tipoff forecast'} (not used by this view):{' '}
            {other.home_win_prob >= 0.5 ? g.home.abbr : g.away.abbr} {pct(Math.max(other.home_win_prob, 1 - other.home_win_prob), 1)}
          </p>
        )}
        <div className="card-bottom small muted">
          {g.market ? <>Market: {g.home.abbr} {pct(g.market.home_win_prob)}</> : <span className="warn">No market odds</span>}<span className="matchup-link">Matchup ↗</span>
        </div>
      </Link>
    </li>
  );
}
