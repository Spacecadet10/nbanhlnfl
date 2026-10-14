import { ModelAgreement } from '../components/ModelAgreement';
import { Overview } from '../components/Overview';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import type { CanonicalState, GameSummary, MarketId, Meta, Policy, ResearchSlate, SlateSelection } from '../api/types';
import { ResearchSlateLine } from '../components/Research';
import { useApi } from '../api/useApi';
import { ModeBadge, NearTipoffBadge, RunBadge, SourceList } from '../components/Badges';
import { ConfidenceFilter, confidenceLevels, levelLabel, RangeRecords, useConfidence } from '../components/ConfidenceFilter';
import { MarketCard } from '../components/MarketView';
import { DetailsToggle, MarketSelect, useCarriedSearch, useDetails, useMarket, useSport } from '../components/Selection';
import { PolicyToggle, usePolicy } from '../components/RunTypeToggle';
import { ProbabilityBar } from '../components/ProbabilityBar';
import { AsyncView, Empty } from '../components/States';
import { CanonicalBlock, canonicalFor, CanonicalRecordLine } from '../components/Canonical';
import { american, calendarDate, FULL_DATE, pct, shiftDate, tipoffTime, dateTime } from '../format';

export function TodayPage() {
  const [sport] = useSport();
  const meta = useApi((s) => api.meta(sport, s), [sport]);
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
  const sport = meta.sport;
  const [market, setMarket] = useMarket();
  const [policyChoice, setPolicy] = usePolicy(canonicalFor(sport));
  const policy: Policy = meta.mode !== 'real' ? 'pregame'
    : policyChoice === 'canonical' && !canonicalFor(sport, market) ? 'near_tipoff' : policyChoice;
  const [level, setLevel] = useConfidence(sport, market, policy);
  const { state, retry } = useApi((s) => api.games(sport, date, policy, level, market, s), [sport, date, policy, level, market]);
  const research = useApi((s) => api.researchSlate(sport, date, s), [sport, date]);

  return (
    <section>
      <Overview />
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
          <PolicyToggle value={policy} onChange={setPolicy} canonical={canonicalFor(sport, market)} />
          <p className="small muted">
            {policy === 'canonical'
              ? 'One canonical forecast per game, recorded within two hours of the start: the sealed baseline model (no adaptive ensemble is approved for this record; it stays research), otherwise the reason there is none. Preseason games have no validated model.'
              : policy === 'near_tipoff'
              ? 'Forecasts appear within two hours of game time. Picks, confidence filters and records use this timing policy.'
              : 'Showing the pre-game archive: forecasts the discontinued daily job recorded before midnight Eastern on the game date, some several days before the game. Picks, the confidence filter and the records use them.'}
          </p>
        </div>
      )}
      <MarketSelect value={market} markets={meta.markets} onChange={setMarket} />
      <ConfidenceFilter value={level} onChange={setLevel} levels={confidenceLevels(sport, market, policy)} />
      <DetailsToggle />
      {policy === 'canonical' ? <CanonicalRecordLine sport={sport} market={market} />
        : <RangeRecords sport={sport} market={market} level={level} realMode={meta.mode === 'real'} policy={policy} />}
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
              <>
                {research.state.status === 'error' && <p className="warn small">Research selections unavailable: {research.state.error}</p>}
                <FilteredSlate games={data.games} selection={data.selection} showAll={() => setLevel(null)} market={market} date={date}
                  research={research.state.status === 'success' ? research.state.data.games : undefined} />
              </>
            )}
          </>
        )}
      </AsyncView>
    </section>
  );
}

const POLICY_NAME: Record<Policy, string> = { pregame: 'pre-game archive', near_tipoff: 'near-tipoff', canonical: 'canonical' };
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
          : <><strong>{sel.selected}</strong> of {sel.games} {noun} selected at {levelLabel(level)} model confidence ({sel.with_forecast} with a {name} forecast)</>}
      </p>
      {selected.length === 0 ? (
        <Empty>
          No {name} picks at {levelLabel(level)} model confidence on this date.{' '}
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
  missing: 'missing (coverage gaps)', unvalidated: 'preseason, no validated model', upcoming: 'not forecast yet (recorded within two hours of the start)',
  not_recorded: 'before canonical recording began', not_forecastable: 'not a forecastable game type',
};

/** Canonical view: the confidence filter narrows the forecasts only. Games without a canonical forecast (missing,
 * unvalidated, not yet forecast) are always listed apart with their reason, and the counts name each group. */
function CanonicalSlate({ games, sel, showAll, market, date, research }: { games: GameSummary[]; sel: SlateSelection; showAll: () => void;
  market: MarketId; date: string; research?: ResearchSlate['games'] }) {
  const level = sel.min_confidence;
  // The slate market's canonical record (the API returns it in `canonical`); the winner's filter is decided by the API.
  const has = (g: GameSummary) => g.canonical?.state === 'forecast' && g.canonical.record != null;
  const meets = (g: GameSummary) => (market === 'winner' ? Boolean(g.slate_pick?.meets_confidence)
    : level == null || (g.canonical?.record?.confidence ?? 0) >= level - 1e-9);
  const forecasts = games.filter(has);
  const shown = level == null ? forecasts : forecasts.filter(meets);
  const without = games.filter((g) => !has(g));
  const below = sel.below_threshold ?? forecasts.length - shown.length;
  const groups = Object.entries(sel.without_forecast ?? {}).filter(([, n]) => n)
    .map(([state, n]) => `${n} ${WITHOUT_LABEL[state as Exclude<CanonicalState, 'forecast'>] ?? state}`).join(', ');
  const card = (g: GameSummary) => <GameCard key={g.game_id} game={g} policy="canonical" market={market} date={date} research={research?.[g.game_id]} />;
  return (
    <>
      <p className="small muted selection-count">
        {sel.games} {sel.games === 1 ? 'game' : 'games'}: <strong>{sel.with_forecast}</strong> with a canonical forecast
        {level != null && sel.with_forecast > 0 && <> ({sel.selected} at {levelLabel(level)} model confidence shown, {below} below it hidden)</>}
        {without.length > 0 && <>; <strong>{without.length}</strong> without one ({groups}), listed below whatever the confidence filter</>}.
      </p>
      {forecasts.length > 0 && (shown.length === 0 ? (
        <Empty>
          No canonical forecast reaches {levelLabel(level)} model confidence on this date ({below} below it).{' '}
          <button type="button" className="link-btn" onClick={showAll}>Show all {forecasts.length} forecasts</button>
        </Empty>
      ) : <ul className="game-grid">{shown.map(card)}</ul>)}
      {without.length > 0 && <>
        <h3>Without a canonical forecast</h3>
        <p className="small muted">Not predictions, so the confidence filter does not apply: each card gives the reason.</p>
        <ul className="game-grid">{without.map(card)}</ul>
      </>}
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
    return '—';
  };
  const other = market !== 'winner' ? null : near ? p : n;   // canonical view: the legacy two-hour forecast, mentioned only with details
  return (
    <li className="card game-card">
      <Link to={`/games/${g.game_id}${carried}`} className="card-link">
        <div className="game-card-head">
          <span className="game-time">{statusLabel(g, date)}{SEASON_LABEL[g.season_type] && ` · ${SEASON_LABEL[g.season_type]}`}</span>
          {g.mode === 'demo' ? <RunBadge runType="demo" /> : canonical ? null : near ? n && <NearTipoffBadge /> : p && <RunBadge runType={p.run_type} />}
        </div>
        <div className="team-line"><span className={`team-monogram team-${g.away.abbr}`}>{g.away.abbr}</span><div><strong>{g.away.name}</strong><span>Away</span></div><b>{side(false)}</b></div>
        <div className="team-line"><span className={`team-monogram team-${g.home.abbr}`}>{g.home.abbr}</span><div><strong>{g.home.name}</strong><span>Home</span></div><b>{side(true)}</b></div>
        {canonical ? <CanonicalBlock c={g.canonical} home={g.home} away={g.away} /> : pick ? (
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
            {near ? 'Pre-game archive forecast' : canonical ? 'Legacy two-hour forecast' : 'Near-tipoff forecast'} (not used by this view):{' '}
            {other.home_win_prob >= 0.5 ? g.home.abbr : g.away.abbr} {pct(Math.max(other.home_win_prob, 1 - other.home_win_prob), 1)}
          </p>
        )}
        {market === 'winner' && <ModelAgreement game={g} />}
        {details && market === 'winner' && <ResearchSlateLine entry={research} market="winner" />}
        <div className="card-bottom small muted">
          {market !== 'winner' ? <span>{g.canonical?.record?.line_source === 'bookmaker' ? "At the bookmaker's line when forecast" : ' '}</span>
            : g.market ? <span title="The latest bookmaker quote retrieved before the start; it may be newer than the forecast above">Bookmaker: {g.home.abbr} {pct(g.market.home_win_prob, 1)}{g.market.home_price != null && g.market.away_price != null && <> · odds {g.away.abbr} {american(g.market.away_price)} / {g.home.abbr} {american(g.market.home_price)}</>} <span className="muted small">(quote {dateTime(g.market.retrieved_at)})</span></span> : <span className="warn">No market odds</span>}<span className="matchup-link">Matchup ↗</span>
        </div>
      </Link>
    </li>
  );
}
