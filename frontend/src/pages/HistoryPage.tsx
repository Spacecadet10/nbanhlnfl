import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import type { HistoryResponse, MarketId, Policy, PredictionRecord, Sport } from '../api/types';
import { useApi } from '../api/useApi';
import { RunBadge } from '../components/Badges';
import { ConfidenceFilter, confidenceLevels, levelLabel, MarketRecordLine, RecordLine, useConfidence } from '../components/ConfidenceFilter';
import { MarketHistoryTable, MarketVersionSelect } from '../components/MarketView';
import { ResearchHistoryTable } from '../components/Research';
import { MarketSelect, useCarriedSearch, useMarket, useSport } from '../components/Selection';
import { PolicyToggle, RunTypeToggle, usePolicy, useRunType } from '../components/RunTypeToggle';
import { AsyncView, Empty } from '../components/States';
import { canonicalFor, CanonicalHistory } from '../components/Canonical';
import { calendarDate, dateTime, FULL_DATE, pct } from '../format';

const PAGE = 500;
const MAX_ROWS = 5000;   // the API's largest limit

/** "Show more" for a table capped at `limit` rows; the record above it always covers every matching row. */
function MoreRows({ shown, total, onMore }: { shown: number; total: number; onMore: () => void }) {
  if (total <= shown) return null;
  return shown < MAX_ROWS
    ? <p><button type="button" className="link-btn" onClick={onMore}>Show {Math.min(PAGE, total - shown)} more ({shown} of {total} shown)</button></p>
    : <p className="small muted">Showing the latest {shown} of {total}; narrow the dates or team to see older rows.</p>;
}

export function HistoryPage() {
  const [params, setParams] = useSearchParams();
  const filters = {
    date_from: params.get('date_from') ?? undefined,
    date_to: params.get('date_to') ?? undefined,
    team: params.get('team') ?? undefined,
  };
  const [sport] = useSport();
  const [market, setMarket] = useMarket();
  const isWinner = market === 'winner';
  const [runChoice, setRun] = useRunType();
  const run = isWinner ? runChoice : 'live';   // market forecasts are live only
  const [level, setLevel] = useConfidence(sport, market);
  const [policyChoice, setPolicy] = usePolicy(canonicalFor(sport));
  const policy: Policy = run !== 'live' ? 'pregame'
    : policyChoice === 'canonical' && !canonicalFor(sport, market) ? 'near_tipoff' : policyChoice;
  const canonical = policy === 'canonical';
  const meta = useApi((s) => api.meta(sport, s), [sport]);
  const realMode = meta.state.status === 'success' && meta.state.data.mode === 'real';
  const [limit, setLimit] = useState(PAGE);
  useEffect(() => setLimit(PAGE), [sport, filters.date_from, filters.date_to, filters.team, run, level, policy, market]);
  // Other markets have their own history (MarketHistory): the winner list is fetched only when it is shown.
  const { state, retry } = useApi((s): Promise<HistoryResponse | null> => (isWinner && !canonical
    ? api.predictions(sport, { ...filters, run_type: run, min_confidence: level, policy, limit }, s) : Promise.resolve(null)),
    [sport, filters.date_from, filters.date_to, filters.team, run, level, policy, isWinner, limit, canonical]);

  const update = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    setParams(next);
  };
  const hasFilters = Boolean(filters.date_from || filters.date_to || filters.team);
  const carried = useCarriedSearch();
  const clearFilters = () => {
    const next = new URLSearchParams(carried.slice(1));
    if (run !== 'live') next.set('run', run);
    setParams(next);
  };

  return (
    <section>
      <div className="page-head">
        <h1>Prediction history</h1>
        {realMode && isWinner && <RunTypeToggle value={run} onChange={setRun} />}
      </div>
      {meta.state.status === 'success' && <MarketSelect value={market} markets={meta.state.data.markets} onChange={setMarket} />}
      {meta.state.status === 'success' && <p className="muted small">
        {!realMode ? 'Demo predictions from synthetic fixtures.'
          : canonical ? 'Canonical forecasts: one record per game, the same record Today, the game page and Performance show and grade. Missing forecasts are listed with their reason.'
          : run === 'live' && policy === 'near_tipoff' ? 'Near-tipoff forecasts, each made before tipoff from the freshest stored results (latest version per game). Kept separate from the pre-game archive. Elo does not know about injuries or line-ups.'
          : run === 'live' ? 'Pre-game archive: forecasts the discontinued daily job recorded before each game\'s cutoff (midnight Eastern on the game date), some several days before the game. Compare the recorded time with the game date. Latest version per game; outcomes are added after the game.'
            : 'Retrospective backtest predictions of the primary model, computed after the fact. These are not forecasts recorded before the games.'}
      </p>}
      {realMode && run === 'live' && <div className="policy-row"><PolicyToggle value={policy} onChange={setPolicy} canonical={canonicalFor(sport, market)} /></div>}
      {canonical ? <p className="small muted">The confidence filter applies to the legacy views and the game-day slate; the canonical history lists every game.</p>
        : <ConfidenceFilter value={level} onChange={setLevel} levels={confidenceLevels(sport, market)} />}
      <form className="filters" onSubmit={(e) => e.preventDefault()}>
        <label>From <input type="date" value={filters.date_from ?? ''} onChange={(e) => update('date_from', e.target.value)} /></label>
        <label>To <input type="date" value={filters.date_to ?? ''} onChange={(e) => update('date_to', e.target.value)} /></label>
        <label>Team
          <select value={filters.team ?? ''} onChange={(e) => update('team', e.target.value)} disabled={meta.state.status !== 'success'}>
            <option value="">All teams</option>
            {meta.state.status === 'success' && meta.state.data.teams.map((t) => <option key={t.abbr} value={t.abbr}>{t.name}</option>)}
          </select>
        </label>
        {hasFilters && <button type="button" className="link-btn" onClick={clearFilters}>Clear</button>}
      </form>
      {meta.state.status === 'error' && (
        <p className="warn small">
          Team list unavailable: {meta.state.error}{' '}
          <button type="button" className="link-btn" onClick={meta.retry}>Retry</button>
        </p>
      )}
      {canonical ? <CanonicalHistory sport={sport} filters={filters} limit={limit} />
      : !isWinner ? (
        <MarketHistory sport={sport} market={market} policy={policy} level={level} filters={filters} hasFilters={hasFilters}
          showAll={() => setLevel(null)} />
      ) : <AsyncView state={state} retry={retry}>
        {(data) => data == null ? null : data.predictions.length === 0
          ? <Empty>
              {level != null
                ? <>No {data.run_type === 'demo' ? 'demo' : data.policy === 'near_tipoff' ? 'near-tipoff' : data.run_type} picks at {levelLabel(level)} model confidence{hasFilters ? ' match these filters' : ''}.{' '}
                    <button type="button" className="link-btn" onClick={() => setLevel(null)}>Show all picks</button></>
                : hasFilters ? 'No predictions match these filters.'
                  : data.policy === 'near_tipoff' ? 'No two-hour forecasts recorded yet. The scheduled odds jobs record them within two hours before eligible games start.'
                  : data.run_type === 'live' ? 'No pre-game archive forecasts in this view. New forecasts use the Within 2 hours policy.'
                    : 'No predictions recorded.'}
            </Empty>
          : <>
              <RecordLine record={data.record} runType={data.run_type} scope={scopeLabel(filters)} parts={data.parts} />
              <p className="small muted">
                Model confidence is the forecast probability for the predicted winner; the observed win rate counts settled games only.
                {data.total > data.predictions.length && ` Showing the latest ${data.predictions.length} of ${data.total}; the record covers all ${data.total}.`}
              </p>
              <HistoryTable rows={data.predictions} />
              <MoreRows shown={data.predictions.length} total={data.total} onMore={() => setLimit((n) => Math.min(MAX_ROWS, n + PAGE))} />
            </>}
      </AsyncView>}
      <ResearchHistoryTable sport={sport} />
    </section>
  );
}

function MarketHistory({ sport, market, policy, level, filters, hasFilters, showAll }: {
  sport: Sport; market: MarketId; policy: Policy; level: number | null; hasFilters: boolean; showAll: () => void;
  filters: { date_from?: string; date_to?: string; team?: string };
}) {
  const [version, setVersion] = useState('');     // '' = the current sealed market model; otherwise an earlier version (archive)
  useEffect(() => setVersion(''), [sport, market, policy]);
  const [limit, setLimit] = useState(PAGE);
  useEffect(() => setLimit(PAGE), [sport, market, policy, level, filters.date_from, filters.date_to, filters.team, version]);
  const { state, retry } = useApi((s) => api.marketPredictions(sport, market, { ...filters, policy, min_confidence: level, model_version: version, limit }, s),
    [sport, market, policy, level, filters.date_from, filters.date_to, filters.team, version, limit]);
  return (
    <AsyncView state={state} retry={retry}>
      {(data) => <>
        <MarketVersionSelect versions={data.versions ?? []} value={version} onChange={setVersion} />
        {data.predictions.length === 0
        ? <Empty>
            {level != null
              ? <>No picks at {levelLabel(level)} model confidence{hasFilters ? ' match these filters' : ''}. <button type="button" className="link-btn" onClick={showAll}>Show all picks</button></>
              : hasFilters ? 'No forecasts match these filters.'
                : `No ${policy === 'near_tipoff' ? 'near-tipoff' : 'pre-game archive'} forecasts of this market have been recorded yet.`}
          </Empty>
        : <>
            <MarketRecordLine record={data.record} scope={scopeLabel(filters)} parts={data.parts} />
            <p className="small muted">
              Each row is the latest forecast per game, at the exact line it was made against. Model-only lines had no bookmaker line when the forecast was made.
              {data.total > data.predictions.length && ` Showing the latest ${data.predictions.length} of ${data.total}; the record covers all ${data.total}.`}
            </p>
            <MarketHistoryTable rows={data.predictions} />
            <MoreRows shown={data.predictions.length} total={data.total} onMore={() => setLimit((n) => Math.min(MAX_ROWS, n + PAGE))} />
          </>}
      </>}
    </AsyncView>
  );
}

/** The record covers every row matching the filters (not only the rows shown): name its date and team scope. */
function scopeLabel(f: { date_from?: string; date_to?: string; team?: string }): string {
  const dates = f.date_from && f.date_to ? `${calendarDate(f.date_from, FULL_DATE)} – ${calendarDate(f.date_to, FULL_DATE)}`
    : f.date_from ? `From ${calendarDate(f.date_from, FULL_DATE)}` : f.date_to ? `Through ${calendarDate(f.date_to, FULL_DATE)}` : 'All dates';
  return f.team ? `${dates} · ${f.team}` : dates;
}

function HistoryTable({ rows }: { rows: PredictionRecord[] }) {
  const carried = useCarriedSearch();   // keeps sport=nhl on the game link (the game is read from that sport's store)
  return (
    <table className="table history">
      <thead>
        <tr><th>Date</th><th>Game</th><th>Pick</th><th>Model confidence</th><th>Result</th><th>Outcome of pick</th><th>Recorded / computed</th><th>Model</th></tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.game_id}>
            <td data-label="Date">{calendarDate(r.game_date, FULL_DATE)}</td>
            <td data-label="Game"><Link to={`/games/${r.game_id}${carried}`}>{r.away.abbr} @ {r.home.abbr}</Link> <RunBadge runType={r.run_type} /></td>
            <td data-label="Pick"><strong>{r.pick === 'home' ? r.home.abbr : r.away.abbr}</strong> <span className="muted small">({r.pick})</span></td>
            <td data-label="Model confidence">{pct(r.confidence, 1)}</td>
            <td data-label="Result">{r.outcome ? `${r.away.abbr} ${r.outcome.away_score} – ${r.outcome.home_score} ${r.home.abbr}`
              : r.void ? <span className="muted" title="The start changed after the forecast, or the game was postponed or cancelled: not scored">Void</span>
              : r.unscorable ? <span className="warn" title="The game is final but has no usable result: not scored">Not scorable</span>
              : <span className="muted">Pending</span>}</td>
            <td data-label="Outcome of pick">
              {r.correct == null ? <span className="muted">—</span> : r.correct ? <span className="ok">Correct</span> : <span className="bad">Wrong</span>}
            </td>
            <td data-label="Recorded / computed">{dateTime(r.generated_at)}{r.lead_days != null && r.lead_days >= 2 && <span className="warn small"> · {r.lead_days} days before</span>}{r.period && r.period !== 'test' && <span className="muted small"> · {r.period} season</span>}</td>
            <td data-label="Model"><code>{r.model_version}</code></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
