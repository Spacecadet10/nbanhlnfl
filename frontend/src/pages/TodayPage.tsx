import { useState } from 'react';
import { ModelAgreement } from '../components/ModelAgreement';
import { Overview } from '../components/Overview';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import type { CanonicalState, GameSummary, MarketId, Meta, Policy, ResearchSlate, SlateSelection, Sport } from '../api/types';
import { ResearchSlateLine } from '../components/Research';
import { useApi } from '../api/useApi';
import { useDataRevision } from '../api/freshness';
import { ModeBadge, NearTipoffBadge, RunBadge, SourceList } from '../components/Badges';
import { ConfidenceFilter, confidenceLevels, levelLabel, RangeRecords, useConfidence } from '../components/ConfidenceFilter';
import { MarketCard } from '../components/MarketView';
import { DetailsToggle, MarketSelect, useCarriedSearch, useDetails, useMarket, useSport } from '../components/Selection';
import { PolicyToggle, usePolicy } from '../components/RunTypeToggle';
import { ProbabilityBar } from '../components/ProbabilityBar';
import { AsyncView, Empty } from '../components/States';
import { CanonicalBlock, canonicalFor, CanonicalRecordLine } from '../components/Canonical';
import { RankedPicksCard } from '../components/RankedPicks';
import { NoSkillNote } from '../components/Skill';
import { TeamLogo } from '../components/TeamLogo';
import { Icon, MetricCard, PageTitle } from '../components/ui';
import { american, calendarDate, FULL_DATE, pct, shiftDate, tipoffTime, dateTime } from '../format';

export function TodayPage() {
  const [sport] = useSport();
  const rev = useDataRevision();
  const meta = useApi((s) => api.meta(sport, s), [sport], rev);
  return (
    <AsyncView state={meta.state} retry={meta.retry}>
      {(m) => <Slate meta={m} />}
    </AsyncView>
  );
}

const POLICY_NOTE: Record<Policy, string> = {
  canonical: 'One forecast per game, recorded within two hours of the start. This is the current record.',
  near_tipoff: 'Forecasts recorded within two hours of tip-off.',
  pregame: 'Older forecasts recorded before midnight Eastern on the game date (a discontinued daily job); shown for reference.',
};

function Slate({ meta }: { meta: Meta }) {
  const [params, setParams] = useSearchParams();
  const date = params.get('date') ?? meta.default_date;
  const setDate = (d: string) => {
    if (!d) return;
    const next = new URLSearchParams(params);
    next.set('date', d);
    setParams(next);
  };
  const sport = meta.sport;
  const [market, setMarket] = useMarket();
  const [policyChoice, setPolicy] = usePolicy(canonicalFor(sport));
  const policy: Policy = meta.mode !== 'real' ? 'pregame'
    : policyChoice === 'canonical' && !canonicalFor(sport, market) ? 'near_tipoff' : policyChoice;
  const [level, setLevel] = useConfidence(sport, market, policy);
  const rev = useDataRevision();
  const { state, retry } = useApi((s) => api.games(sport, date, policy, level, market, s), [sport, date, policy, level, market], rev);
  const research = useApi((s) => api.researchSlate(sport, date, s), [sport, date], rev);
  const [showOverview, setShowOverview] = useState(false);

  return (
    <section>
      <PageTitle icon={sport === 'nhl' ? 'puck' : 'ball'} plain title="Today's Games" badge={meta.mode === 'demo' ? <ModeBadge mode={meta.mode} /> : undefined}
        sub="Live forecasts and best picks."
        right={
          <div className="date-control">
            <button type="button" className="date-step prev" onClick={() => setDate(shiftDate(date, -1))} aria-label="Previous day"><Icon name="chevron" size={16} /></button>
            <button type="button" className="date-step" onClick={() => setDate(shiftDate(date, 1))} aria-label="Next day"><Icon name="chevron" size={16} /></button>
            <label className="date-button">
              <span>{calendarDate(date, FULL_DATE)}</span><Icon name="chevronDown" size={18} />
              <input type="date" value={date} onChange={(e) => setDate(e.target.value)} aria-label="Game date" />
            </label>
            {date !== meta.default_date && (
              <button type="button" className="link-btn" onClick={() => setDate(meta.default_date)}>{meta.mode === 'demo' ? 'Demo today' : 'Next games'}</button>
            )}
          </div>
        } />
      {meta.mode === 'real' && (
        <div className="policy-row">
          <PolicyToggle value={policy} onChange={setPolicy} canonical={canonicalFor(sport, market)} />
        </div>
      )}
      <div className="today-controls">
        <MarketSelect value={market} markets={meta.markets} onChange={setMarket} />
        <ConfidenceFilter value={level} onChange={setLevel} levels={confidenceLevels(sport, market, policy)} />
      </div>
      <NoSkillNote market={market} />
      <AsyncView state={state} retry={retry}>
        {(data) => (
          <>
            {data.games.length === 0 ? (
              <Empty>
                No games on this date.{' '}
                {meta.available_dates.length > 0 && <>{meta.mode === 'demo' ? 'Demo data' : 'Stored data'} covers {calendarDate(meta.available_dates[0], FULL_DATE)} – {calendarDate(meta.available_dates[meta.available_dates.length - 1], FULL_DATE)}. </>}
                <button type="button" className="link-btn" onClick={() => setDate(meta.default_date)}>{meta.mode === 'demo' ? 'Go to demo today' : 'Go to the next games'}</button>
              </Empty>
            ) : (
              <>
                {research.state.status === 'error' && <p className="warn small">Research selections unavailable: {research.state.error}</p>}
                <FilteredSlate games={data.games} selection={data.selection} showAll={() => setLevel(null)} market={market} date={date}
                  research={research.state.status === 'success' ? research.state.data.games : undefined} />
              </>
            )}
            {meta.mode === 'real' && <RankedPicksCard sport={sport} date={date} />}
            {meta.mode === 'real' && policy === 'canonical' && <TodaySummary sport={sport} market={market} date={date} />}
            <details className="about-numbers">
              <summary><Icon name="info" size={16} />About these numbers</summary>
              {meta.markets.find((m) => m.id === market)?.note && <p className="small">{meta.markets.find((m) => m.id === market)?.note}</p>}
              <p className="small">{POLICY_NOTE[meta.mode === 'real' ? policy : 'pregame']}</p>
              <p className="small">Model probability is the model's estimate for its side — not a guarantee. Each card also shows the market's
                de-vigged estimate; the market is the benchmark. The record counts settled games only. Not betting advice.</p>
              {policy === 'canonical' ? <CanonicalRecordLine sport={sport} market={market} level={level} />
                : <RangeRecords sport={sport} market={market} level={level} realMode={meta.mode === 'real'} policy={policy} />}
            </details>
            <DetailsToggle />
            {data.sources.length > 0 && (
              <details className="today-more">
                <summary><strong>Data sources</strong> <span className="muted small">what feeds these numbers and how fresh it is</span></summary>
                <SourceList sources={data.sources} />
              </details>
            )}
          </>
        )}
      </AsyncView>
      <details className="card today-more" onToggle={(e) => { if ((e.target as HTMLDetailsElement).open) setShowOverview(true); }}>
        <summary><strong>How we're doing</strong> <span className="muted small">full record by sport, training status, backtests and research</span></summary>
        {showOverview && <Overview />}
      </details>
    </section>
  );
}

const POLICY_NAME: Record<Policy, string> = { pregame: 'pre-game archive', near_tipoff: 'near-tipoff', canonical: 'current forecast' };
/** Demo games carry one synthetic forecast each: never call it an archive or a timing policy. */
const policyName = (policy: Policy, demo: boolean) => (demo ? 'demo' : POLICY_NAME[policy]);

/** The API decides which games are selected: only the chosen policy's forecast counts, never the other one. */
function FilteredSlate({ games, selection: sel, showAll, market, date, research }: { games: GameSummary[]; selection: SlateSelection; showAll: () => void;
  market: MarketId; date: string; research?: ResearchSlate['games'] }) {
  const level = sel.min_confidence;
  const isWinner = market === 'winner';
  if (sel.policy === 'canonical') return <CanonicalSlate games={games} sel={sel} showAll={showAll} market={market} date={date} research={research} />;
  const selected = level == null ? games
    : games.filter((g) => (isWinner ? g.slate_pick?.meets_confidence : g.market_forecast?.meets_confidence));
  const noun = games.length === 1 ? 'game' : 'games';
  const name = policyName(sel.policy, games.some((g) => g.mode === 'demo'));
  return (
    <>
      <p className="small muted selection-count">
        {level == null
          ? <>{sel.games} {noun}, {sel.with_forecast} with a {name} forecast</>
          : <><strong>{sel.selected}</strong> of {sel.games} {noun} selected at {levelLabel(level)} model probability ({sel.with_forecast} with a {name} forecast)</>}
      </p>
      {selected.length === 0 ? (
        <Empty>
          No {name} picks at {levelLabel(level)} model probability on this date.{' '}
          {sel.with_forecast === 0 ? `None of these games has a ${name} forecast yet. ` : ''}
          <button type="button" className="link-btn" onClick={showAll}>Show all {sel.games} {noun}</button>
        </Empty>
      ) : (
        <ul className="game-grid">
          {selected.map((g) => (isWinner
            ? <GameCard key={g.game_id} game={g} policy={sel.policy} date={date} research={research?.[g.game_id]} />
            : <MarketCard key={g.game_id} game={g} statusLabel={statusLabel(g, date)} research={research?.[g.game_id]} market={market} />))}
        </ul>
      )}
    </>
  );
}

const WITHOUT_LABEL: Record<Exclude<CanonicalState, 'forecast'>, string> = {
  missing: 'no forecast recorded', unvalidated: 'preseason, no validated model', upcoming: 'recorded within two hours of the start',
  not_recorded: 'before recording began', not_forecastable: 'not a forecastable game type',
};

/** Canonical view: the confidence filter narrows the forecasts only. Games without a forecast (missing,
 * unvalidated, not yet forecast) are always listed apart with their reason, and the counts name each group. */
function CanonicalSlate({ games, sel, showAll, market, date, research }: { games: GameSummary[]; sel: SlateSelection; showAll: () => void;
  market: MarketId; date: string; research?: ResearchSlate['games'] }) {
  const level = sel.min_confidence;
  // The slate market's record; the winner's filter is decided by the API.
  const has = (g: GameSummary) => g.canonical?.state === 'forecast' && g.canonical.record != null;
  const meets = (g: GameSummary) => (market === 'winner' ? Boolean(g.slate_pick?.meets_confidence)
    : level == null || (g.canonical?.record?.confidence ?? 0) >= level - 1e-9);
  const forecasts = games.filter(has);
  const shown = level == null ? forecasts : forecasts.filter(meets);
  const without = games.filter((g) => !has(g));
  const below = sel.below_threshold ?? forecasts.length - shown.length;
  const groups = Object.entries(sel.without_forecast ?? {}).filter(([, n]) => n)
    .map(([state]) => WITHOUT_LABEL[state as Exclude<CanonicalState, 'forecast'>] ?? state).join(', ');
  const card = (g: GameSummary) => <GameCard key={g.game_id} game={g} policy="canonical" market={market} date={date} research={research?.[g.game_id]} />;
  return (
    <>
      {forecasts.length > 0 && (shown.length === 0 ? (
        <Empty>
          No forecast at {levelLabel(level)} model probability on this date ({below} lower).{' '}
          <button type="button" className="link-btn" onClick={showAll}>Show all {forecasts.length} forecasts</button>
        </Empty>
      ) : <ul className="game-grid">{shown.map(card)}</ul>)}
      {without.length > 0 && <>
        {forecasts.length > 0 && <h3 className="slate-group">Not forecast yet <span className="muted small">— not predictions, so the filter does not apply; each card gives the reason</span></h3>}
        <ul className="game-grid">{without.map(card)}</ul>
      </>}
      <p className="small muted selection-count">
        {sel.games} {sel.games === 1 ? 'game' : 'games'}: <strong>{sel.with_forecast}</strong> with a forecast
        {level != null && sel.with_forecast > 0 && <> ({sel.selected} at {levelLabel(level)} model probability shown, {below} lower)</>}
        {without.length > 0 && <>; <strong>{without.length}</strong> without a forecast ({groups}), listed below whatever the filter</>}.
      </p>
    </>
  );
}

const SEASON_LABEL: Record<GameSummary['season_type'], string> = {
  preseason: 'PRESEASON', regular: '', play_in: 'PLAY-IN', postseason: 'PLAYOFFS', other: 'EXHIBITION',
};

function statusLabel(g: GameSummary, date: string) {
  if (g.outcome) return 'FINAL';
  if (g.status === 'postponed' || g.status === 'canceled') return g.status.toUpperCase();
  if (g.status === 'in_progress') return 'IN PROGRESS';
  return tipoffTime(g.tipoff, date);
}

function GameCard({ game: g, policy, market = 'winner', date, research }: { game: GameSummary; policy: Policy; market?: MarketId; date: string;
  research?: ResearchSlate['games'][string] }) {
  const carried = useCarriedSearch();
  const [details] = useDetails();
  const p = g.prediction;
  const n = g.near_tipoff;
  const near = policy === 'near_tipoff';
  const canonical = policy === 'canonical';
  const pick = g.slate_pick;   // the selected policy's pick; the other forecast is only mentioned below
  const side = (home: boolean) => {
    if (g.outcome) return home ? g.outcome.home_score : g.outcome.away_score;
    if (pick) return pct(home ? pick.home_win_prob : 1 - pick.home_win_prob, 1);
    return null;   // no forecast yet: the card's note says why
  };
  const other = market !== 'winner' ? null : near ? p : n;   // canonical view: the legacy two-hour forecast, mentioned only with details
  const marketSide = pick && g.market ? (pick.pick === 'home' ? g.market.home_win_prob : 1 - g.market.home_win_prob) : null;
  return (
    <li className="card game-card">
      <Link to={`/games/${g.game_id}${carried}`} className="card-link">
        <div className="game-card-head">
          <span className="game-time">{statusLabel(g, date)}{SEASON_LABEL[g.season_type] && ` · ${SEASON_LABEL[g.season_type]}`}</span>
          {g.mode === 'demo' ? <RunBadge runType="demo" /> : canonical ? null : near ? n && <NearTipoffBadge /> : p && <RunBadge runType={p.run_type} />}
        </div>
        <div className="team-line"><TeamLogo team={g.away} size={44} /><div><strong>{g.away.name}</strong><span>Away</span></div><b>{side(false)}</b></div>
        <div className="team-line"><TeamLogo team={g.home} size={44} /><div><strong>{g.home.name}</strong><span>Home</span></div><b>{side(true)}</b></div>
        {canonical ? <CanonicalBlock c={g.canonical} home={g.home} away={g.away} market={g.market?.home_win_prob ?? null} /> : pick ? (
          <>
            <div className="card-section-label">
              {near ? 'NEAR-TIPOFF PROBABILITY' : p?.run_type === 'backtest' ? 'BACKTEST PROBABILITY' : 'MODEL PROBABILITY'}
            </div>
            <ProbabilityBar home={g.home} away={g.away} homeProb={pick.home_win_prob} />
            <p className="pick-line small">
              Model estimate: <strong>{pick.pick === 'home' ? g.home.abbr : g.away.abbr}</strong>
              <span className="muted"> · {pct(pick.confidence, 1)} model probability</span>
              {marketSide != null && <span className="muted"> · market {pct(marketSide, 1)}</span>}
              {near && n && <span className="muted"> · made {Math.round(n.minutes_before_tipoff)} min before tipoff</span>}
              {near && n?.correct != null && (n.correct ? <span className="ok"> · Correct</span> : <span className="bad"> · Wrong</span>)}
              {near && n?.void && <span className="muted"> · Void</span>}
            </p>
          </>
        ) : (
          <p className="forecast-note">{near ? (g.status === 'scheduled' && new Date(g.tipoff).getTime() > Date.now()
            ? 'No forecast recorded yet. Forecasts are made within two hours of game time.'
            : 'No two-hour forecast was recorded for this game.') : g.forecast_note ?? 'No forecast available.'}</p>
        )}
        {details && other && (
          <p className="other-forecast small muted">
            {near ? 'Pre-game archive forecast' : canonical ? 'Older two-hour forecast' : 'Near-tipoff forecast'} (not used by this view):{' '}
            {other.home_win_prob >= 0.5 ? g.home.abbr : g.away.abbr} {pct(Math.max(other.home_win_prob, 1 - other.home_win_prob), 1)}
          </p>
        )}
        {details && market === 'winner' && <ModelAgreement game={g} />}
        {details && market === 'winner' && <ResearchSlateLine entry={research} market="winner" />}
        <div className="card-bottom small muted">
          {market !== 'winner' ? <span>{g.canonical?.record?.line_source === 'bookmaker' ? "At the bookmaker's line when forecast" : ' '}</span>
            : g.market ? <span className="book-lines" title="The latest bookmaker quote retrieved before the start; it may be newer than the forecast above">
                <span>Bookmaker: <strong>{g.home.abbr} {pct(g.market.home_win_prob, 1)}</strong></span>
                {g.market.home_price != null && g.market.away_price != null && <span>Odds: <strong>{g.away.abbr} {american(g.market.away_price)} / {g.home.abbr} {american(g.market.home_price)}</strong></span>}
                <span className="muted quote-time">Quote {dateTime(g.market.retrieved_at)}</span>
              </span> : <span className="warn">No market odds</span>}<span className="matchup-link">Matchup <Icon name="arrow" size={14} /></span>
        </div>
      </Link>
    </li>
  );
}

/** The compact analytics row at the top of Game day: this date's best picks and the selected market's season record
 * (coverage and settled accuracy of the canonical forecasts). Values come from the API as they are; nothing is filled in. */
function TodaySummary({ sport, market, date }: { sport: Sport; market: MarketId; date: string }) {
  const rev = useDataRevision();
  const perf = useApi((s) => api.canonicalPerformance(sport, market, s), [sport, market], rev);
  const ranked = useApi((s) => api.canonicalRanked(sport, date, s), [sport, date], rev);
  const p = perf.state.status === 'success' ? perf.state.data : null;
  const strong = ranked.state.status === 'success' ? ranked.state.data.picks.filter((x) => x.strong).length : null;
  const s = p?.competitive;
  return (
    <div className="metrics today-metrics">
      <MetricCard icon="target" label="Best picks" tone="lime"
        value={strong == null ? '—' : strong}
        hint={strong == null ? (ranked.state.status === 'error' ? 'Unavailable' : 'Loading…')
          : strong === 0 ? 'No strong pick on this date yet.' : `Strong ${strong === 1 ? 'pick' : 'picks'} on this date, listed below.`} />
      <MetricCard icon="layers" label="Forecast coverage" tone="dark"
        value={s?.coverage != null ? pct(s.coverage, 0) : '—'}
        hint={s ? `${s.forecast} of ${s.games} started games have a forecast this season.` : perf.state.status === 'error' ? 'Unavailable' : 'Loading…'} />
      <MetricCard icon="trophy" label="Settled accuracy" tone="lavender"
        value={s?.accuracy != null ? pct(s.accuracy, 1) : '—'}
        hint={s ? (s.graded > 0 ? `${s.grades.win}–${s.grades.loss} on settled games this season.` : 'No settled games yet.') : perf.state.status === 'error' ? 'Unavailable' : 'Loading…'} />
    </div>
  );
}
