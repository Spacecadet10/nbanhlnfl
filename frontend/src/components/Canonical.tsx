// The canonical forecast: one record per game and market (game_forecasts), shown and graded the same way on Today, Game,
// History and Performance. Each view names the record (#id) and model version so the pages can be compared.
import { NoSkillNote } from './Skill';
import { ForecastPercentageRecord } from './ThresholdOverview';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import type { CanonicalForecast, CanonicalGrade, CanonicalRecord, CanonicalState, CanonicalSummary, MarketId, Sport, Team } from '../api/types';
import { useApi } from '../api/useApi';
import { calendarDate, dateTime, FULL_DATE, num, pct } from '../format';
import { ProbabilityBar } from './ProbabilityBar';
import { MARKET } from './Research';
import { useCarriedSearch } from './Selection';
import { AsyncView, Empty } from './States';
import { Icon, MetricCard } from './ui';

/** Where the canonical forecast is shown: every sport and every market it defines (the API rejects a market a sport lacks). */
export const canonicalFor = (_sport: Sport, _market: MarketId = 'winner') => true;

const LINE_SOURCE: Record<CanonicalRecord['line_source'], string> = {
  bookmaker: "bookmaker's line", model: 'model line (no bookmaker line when forecast)', none: '',
};

export const SOURCE_LABEL: Record<CanonicalRecord['source'], string> = {
  adaptive: 'Adaptive ensemble (research model, not approved)',
  primary_recorded: 'Sealed baseline model',
  primary_direct: 'Sealed baseline model (computed for this record)',
};

export const STATE_LABEL: Record<Exclude<CanonicalState, 'forecast'>, string> = {
  missing: 'No forecast recorded',
  upcoming: 'Not forecast yet',
  unvalidated: 'No validated forecast',
  not_recorded: 'Before recording began',
  not_forecastable: 'Not forecast',
};

export const GRADE_LABEL: Record<CanonicalGrade, string> = {
  win: 'Correct', loss: 'Wrong', push: 'Push', pending: 'Pending', void: 'Void (tie, start changed, postponed or cancelled: not scored)',
  unscorable: 'Not scorable (final without a usable result)', unresolved: 'Unresolved (no final result 48 h after the start)',
};

export function GradeText({ grade }: { grade: CanonicalGrade | null }) {
  if (grade == null) return <span className="muted">—</span>;
  const cls = grade === 'win' ? 'ok' : grade === 'loss' ? 'bad' : grade === 'unresolved' || grade === 'unscorable' ? 'warn' : 'muted';
  return <span className={cls}>{GRADE_LABEL[grade]}</span>;
}

/** One game's canonical forecast, or the reason there is none. `detail` adds the provenance (Game page). */
export function CanonicalBlock({ c, home, away, detail = false, market }: {
  c: CanonicalForecast | null | undefined; home: Team; away: Team; detail?: boolean;
  /** De-vigged market probability for the home side, shown beside the model estimate. */
  market?: number | null;
}) {
  if (!c) return null;
  const r = c.record;
  if (c.state !== 'forecast' || !r) {
    return (
      <div className="canonical">
        <p className="forecast-note"><strong>{c.state === 'forecast' ? 'No forecast recorded' : STATE_LABEL[c.state]}.</strong>{' '}
          {c.state === 'upcoming' ? 'Forecasts are recorded within two hours of the start.' : c.reason}
          {c.attempts > 0 && c.state !== 'unvalidated' && <> ({c.attempts} recorded {c.attempts === 1 ? 'attempt' : 'attempts'}, none with a forecast.)</>}</p>
      </div>
    );
  }
  const winner = c.market === 'winner' && r.home_win_prob != null;
  const marketSide = winner && market != null ? (r.pick === 'home' ? market : 1 - market) : null;
  return (
    <div className="canonical">
      <div className="card-section-label">CURRENT FORECAST · {(c.percentage_history?.market_title ?? MARKET[c.market] ?? c.market).toUpperCase()} · RECORD #{r.forecast_id}</div>
      {winner && <ProbabilityBar home={home} away={away} homeProb={r.home_win_prob!} label="Canonical win probability" />}
      <p className="pick-line small">
        Model estimate: <strong>{winner ? (r.pick === 'home' ? home : away).abbr : r.pick_label}</strong>
        {!winner && r.line_source !== 'none' && <span className="muted"> · {LINE_SOURCE[r.line_source]}</span>}
        <span className="muted"> · {pct(r.confidence, 1)} model probability{marketSide != null && <> · market {pct(marketSide, 1)}</>} · made {Math.round(r.minutes_before_start)} min before the start</span>
        {' · '}<GradeText grade={c.grade} />
        {!winner && c.result_label && <span className="muted"> · result: {c.result_label}</span>}
      </p>
      <p><ForecastPercentageRecord history={c.percentage_history} grade={c.grade} opposite={r.opposite_pick_label} startAt={r.start_at} /></p>
      {c.percentage_history?.sport === "nfl" && c.market !== "winner"
        ? <p className="no-skill small">Informational NFL score forecast. This market has no proven skill against the bookmaker’s line and is excluded from recommended picks.</p>
        : <NoSkillNote market={c.market} />}
      <p className="small"><span className={r.source === 'adaptive' ? 'badge badge-research' : 'badge'}>{SOURCE_LABEL[r.source]}</span></p>
      {c.regraded && c.grade_history && (
        <p className="small warn">Regraded after a result correction: {c.grade_history.map((h) =>
          `${GRADE_LABEL[h.grade].split(' (')[0]} with ${away.abbr} ${h.away_score ?? '?'} – ${h.home_score ?? '?'} ${home.abbr}${h.decision && h.decision !== 'REG' ? ` (${h.decision})` : ''}`
          + ` (${h.change === 'decision_known' ? 'regulation decision first known' : h.basis === 'backfill' ? 'stored when correction tracking began' : 'seen'} ${dateTime(h.observed_at)})`).join(' → ')}. The forecast itself never changed.</p>
      )}
      {c.later_failure && (
        <p className="small warn">A later attempt at {dateTime(c.later_failure.generated_at)} (record #{c.later_failure.forecast_id}) recorded no
          forecast: {c.later_failure.reason}. This earlier forecast stays the canonical one.</p>
      )}
      {detail && (
        <dl className="facts">
          <dt>Record</dt><dd>#{r.forecast_id} · {c.attempts} {c.attempts === 1 ? 'row' : 'rows'} recorded for this game before the start</dd>
          <dt>Made</dt><dd>{dateTime(r.generated_at)}, {Math.round(r.minutes_before_start)} minutes before the start scheduled then ({dateTime(r.start_at)})</dd>
          <dt>Source</dt><dd>{SOURCE_LABEL[r.source]}{r.fallback_reason && <span className="muted"> — adaptive ensemble not used because: {r.fallback_reason}</span>}</dd>
          <dt>Model version</dt><dd><code className="wrap">{r.model_version}</code></dd>
          <dt>Inputs</dt><dd className="muted">{r.source === 'adaptive' ? 'See the adaptive combination card for the members used.'
            : 'Results only (the sealed baseline does not use injuries, rosters or player data).'}</dd>
          <dt>Grade</dt><dd><GradeText grade={c.grade} /></dd>
        </dl>
      )}
    </div>
  );
}

/** The canonical record over started regular-season, play-in and playoff games: missing forecasts are coverage gaps. */
export function CanonicalSummaryLine({ s }: { s: CanonicalSummary }) {
  if (s.games === 0) return <p className="small muted">No started regular-season or playoff game since canonical recording began.</p>;
  return (
    <p className="small">
      <strong>{s.forecast} of {s.games}</strong> started games have a forecast ({pct(s.coverage, 0)} coverage; {s.missing} without one, never counted as losses).
      {' '}Settled: <strong>{s.grades.win}–{s.grades.loss}</strong>{s.graded > 0 && <> ({pct(s.accuracy, 1)} correct)</>}
      {s.grades.push > 0 && <>, {s.grades.push} push</>}{s.grades.pending > 0 && <>, {s.grades.pending} pending</>}{s.grades.void > 0 && <>, {s.grades.void} void</>}
      {s.grades.unscorable > 0 && <>, {s.grades.unscorable} not scorable</>}{s.grades.unresolved > 0 && <>, {s.grades.unresolved} unresolved</>}.
      {(s.regraded ?? 0) > 0 && <span className="warn"> {s.regraded} {s.regraded === 1 ? 'forecast was' : 'forecasts were'} regraded after a result correction (each shows its earlier grade).</span>}
    </p>
  );
}

export function CanonicalHistory({ sport, market, filters, limit }: { sport: Sport; market: MarketId; filters: { date_from?: string; date_to?: string; team?: string }; limit: number }) {
  const carried = useCarriedSearch();
  const { state, retry } = useApi((s) => api.canonicalHistory(sport, market, { ...filters, limit }, s),
    [sport, market, filters.date_from, filters.date_to, filters.team, limit]);
  return (
    <AsyncView state={state} retry={retry}>{(h) => <>
      <CanonicalSummaryLine s={h.record} />
      <p className="small muted">
        One row per game since canonical recording began{h.recording_began_at ? ` (${dateTime(h.recording_began_at)})` : ''}: its canonical record
        and grade, or why there is none. Preseason games ({h.preseason_games}) have no validated model and are listed without a probability.
        Forecasts from before canonical recording are in the legacy views.
        {h.total > h.rows.length && ` Showing the latest ${h.rows.length} of ${h.total}; the record covers all ${h.total}.`}
      </p>
      {h.rows.length === 0 ? <Empty>{h.recording_began_at ? 'No records match these filters.' : 'Recording has not started in this data store.'}</Empty> : (
        <div className="table-wrap"><table className="table history">
          <thead><tr><th>Date</th><th>Game</th><th>Forecast</th><th>Result</th><th>Grade</th><th>Record</th><th>Model</th></tr></thead>
          <tbody>
            {h.rows.map((row) => {
              const c = row.canonical, r = c.record;
              return (
                <tr key={row.game_id}>
                  <td data-label="Date">{calendarDate(row.game_date, FULL_DATE)}</td>
                  <td data-label="Game"><Link to={`/games/${row.game_id}${carried}`}>{row.away.abbr} @ {row.home.abbr}</Link>
                    {row.season_type === 'preseason' && <span className="muted small"> · preseason</span>}</td>
                  <td data-label="Canonical forecast">{r && c.state === 'forecast'
                    ? <><strong>{c.market === 'winner' ? (r.pick === 'home' ? row.home.abbr : row.away.abbr) : r.pick_label}</strong> {pct(r.confidence, 1)}</>
                    : <span className="muted" title={c.reason ?? ''}>{c.state === 'forecast' ? 'No forecast recorded' : STATE_LABEL[c.state]}: {c.reason}</span>}</td>
                  <td data-label="Result">{row.outcome ? `${row.away.abbr} ${row.outcome.away_score} – ${row.outcome.home_score} ${row.home.abbr}` : <span className="muted">{row.status.replace('_', ' ')}</span>}</td>
                  <td data-label="Grade"><GradeText grade={c.grade} />{c.regraded && <span className="warn small"> · regraded</span>}</td>
                  <td data-label="Record">{r ? <>#{r.forecast_id} · {r.source === 'adaptive' ? 'adaptive (research)' : 'sealed baseline'} · {Math.round(r.minutes_before_start)} min before</> : <span className="muted">—</span>}
                    {c.later_failure && <span className="warn small"> · later attempt failed</span>}</td>
                  <td data-label="Model">{r ? <code className="wrap">{r.model_version}</code> : <span className="muted">—</span>}</td>
                </tr>
              );
            })}
          </tbody>
        </table></div>
      )}
    </>}</AsyncView>
  );
}

export function CanonicalReport({ sport, market }: { sport: Sport; market: MarketId }) {
  const { state, retry } = useApi((s) => api.canonicalPerformance(sport, market, s), [sport, market]);
  return (
    <AsyncView state={state} retry={retry}>{(p) => {
      const s = p.competitive;
      const winner = p.market === 'winner';
      const unmatched = Object.entries(p.market_comparison.unmatched ?? {});
      return <>
        <div className="metrics kpi">
          <MetricCard icon="target" tone="dark" label="Coverage" value={pct(s.coverage, 0)} hint={`${s.forecast} of ${s.games} started games`} />
          <MetricCard icon="trophy" tone="lime" label={<>{winner ? 'Picked Winner' : 'Correct Picks'}{s.graded > 0 && s.graded < 200 && <span className="sample-chip" title={`Only ${s.graded} settled games: these numbers are very noisy.`}>Small sample</span>}</>} value={pct(s.accuracy, 1)}
            hint={`Wins of ${s.graded} won or lost forecasts${s.grades.push > 0 ? `; ${s.grades.push} pushes apart` : ''}`} />
          <MetricCard icon="layers" tone="lavender" label="Brier Score" value={num(s.brier_score)}
            hint={<>Lower is better{p.market === 'reg3' ? '' : ' (0.25 = always 50%)'}.<br />Log loss {num(s.log_loss)}.</>} />
        </div>
        <div className="two-col">
        <div className="card">
          <h2>Versus the Market</h2>
          <p className="small muted">{winner ? 'Settled canonical forecasts with a bookmaker moneyline retrieved no later than the forecast'
            : "Settled canonical forecasts compared with the bookmaker quote the forecast used: same market, the forecast's exact line, retrieved before the forecast"}: {p.market_comparison.matched_games} of {s.graded} ({pct(p.market_comparison.coverage)}).</p>
          {unmatched.length > 0 && <ul className="small muted">{unmatched.map(([why, n]) => <li key={why}>{n} without a comparison: {why}</li>)}</ul>}
          {p.market_comparison.matched_games === 0 ? <Empty>No settled canonical forecast with a market quote.</Empty> : (<>
            <table className="table compare"><thead><tr><th /><th>Brier score</th></tr></thead><tbody>
              <tr className="row-model"><th scope="row">Canonical forecast</th><td><strong>{num(p.market_comparison.model_brier)}</strong></td></tr>
              <tr><th scope="row">Market</th><td>{num(p.market_comparison.market_brier)}</td></tr>
            </tbody></table>
            {p.market_comparison.model_brier != null && p.market_comparison.market_brier != null && (
              <p className="small versus-note">{p.market_comparison.market_brier < p.market_comparison.model_brier
                ? <>The market's Brier is lower by {num(p.market_comparison.model_brier - p.market_comparison.market_brier)} — the market is the better forecaster on these games. The model is a record, not a market edge.</>
                : <>The model's Brier is lower by {num(p.market_comparison.market_brier - p.market_comparison.model_brier)} on these {p.market_comparison.matched_games} games; that is not yet evidence of a betting edge.</>}</p>
            )}
          </>)}
        </div>
        <div className="card">
          <h2>Every Started Game</h2>
          <table className="table compare">
            <tbody>
              <tr><th scope="row">Started regular-season, play-in and playoff games</th><td>{s.games}</td></tr>
              <tr><th scope="row">With a canonical forecast</th><td>{s.forecast}</td></tr>
              <tr><th scope="row">Missing (coverage gaps)</th><td>{s.missing}</td></tr>
              {(['win', 'loss', 'push', 'pending', 'void', 'unscorable', 'unresolved'] as CanonicalGrade[]).map((g) => (
                <tr key={g}><th scope="row">Forecasts graded {GRADE_LABEL[g].split(' (')[0].toLowerCase()}</th><td>{s.grades[g]}</td></tr>
              ))}
            </tbody>
          </table>
          {s.missing_reasons.length > 0 && <>
            <h3>Why forecasts are missing</h3>
            <ul className="small">{s.missing_reasons.map((m) => <li key={m.reason}>{m.games} × {m.reason}</li>)}</ul>
          </>}
          <h3>Sources and model versions</h3>
          <ul className="small">
            {Object.entries(s.by_source).map(([k, n]) => <li key={k}>{SOURCE_LABEL[k as CanonicalRecord['source']] ?? k}: {n}</li>)}
            {s.model_versions.map((v) => <li key={v.model_version}><code className="wrap">{v.model_version}</code>: {v.forecasts}</li>)}
            {s.forecast === 0 && <li className="muted">No canonical forecasts yet.</li>}
          </ul>
        </div>
        </div>
        <details className="about-numbers about-record"><summary><Icon name="info" size={16} />About this record</summary>
          <p className="small" role="note">{p.note}</p>
          <p className="small">
            {p.recording_began_at ? <>Canonical recording began {dateTime(p.recording_began_at)}.</> : 'Canonical recording has not started in this data store.'}
            {p.date_from && p.date_to && <> Games: {calendarDate(p.date_from, FULL_DATE)} – {calendarDate(p.date_to, FULL_DATE)}.</>}
          </p>
          <CanonicalSummaryLine s={s} />
        </details>
        {s.graded > 0 && s.graded < 200 && <p className="warn small sample-warn">Small sample: {s.graded} settled games, so these numbers are very noisy.</p>}
        <div className="card">
          <h2>By confidence threshold</h2>
          <p className="small muted">Each row counts the forecasts whose model probability is at least that level (this market's grid: {p.market === 'reg3' ? '35–70%, three outcomes' : '50–90%'}). A filter of the record above: every game stays recorded and graded whatever its probability. A high band with a handful of picks is noise, not a threshold; judge bands by their interval, not their point estimate. <a href="#thresholds-by-market">Compare every market's thresholds and the best one</a>.</p>
          <div className="table-wrap"><table className="table"><thead><tr><th>Minimum</th><th>Forecasts</th><th>W–L{p.market === 'reg3' ? '' : '–P'}</th><th>Win rate</th><th>Pending</th><th>Void / unscorable</th></tr></thead>
            <tbody>{s.thresholds.map((t) => <tr key={t.threshold}><th scope="row">{pct(t.threshold)}+</th><td>{t.forecasts}</td>
              <td>{t.win}–{t.loss}{p.market === 'reg3' ? '' : `–${t.push}`}</td><td>{pct(t.win_rate, 1)}</td><td>{t.pending}</td><td>{t.void} / {t.unscorable + t.unresolved}</td></tr>)}</tbody>
          </table></div>
        </div>
        <div className="card">
          <h2>Calibration</h2>
          <p className="small muted">Won or lost canonical forecasts only. {winner ? 'When the forecast says X% for the home team, does it win about X% of the time?'
            : p.market === 'reg3' ? 'When the picked outcome is given X%, does it happen about X% of the time?'
            : 'When the forecast gives side A (home side / over) X% excluding a push, does it land about X% of the time?'}</p>
          <div className="table-wrap"><table className="table">
            <thead><tr><th>{winner ? 'Predicted home win' : p.market === 'reg3' ? 'Predicted pick' : 'Predicted side A'}</th><th>Games</th><th>Avg predicted</th><th>{winner ? 'Actual home wins' : 'Actual'}</th></tr></thead>
            <tbody>{p.calibration.map((b) => (
              <tr key={b.lower}><td>{pct(b.lower)}–{pct(b.upper)}</td><td>{b.count}</td><td>{pct(b.mean_predicted)}</td><td>{pct(b.observed_rate)}</td></tr>
            ))}</tbody>
          </table></div>
        </div>
        <div className="card">
          <h2>Preseason · reported apart</h2>
          <p className="small">{p.preseason.games} started preseason {p.preseason.games === 1 ? 'game' : 'games'} since canonical recording began; none has a canonical forecast.</p>
          <p className="small muted">{p.preseason.note} {p.preseason.experimental_rows > 0 && `${p.preseason.experimental_rows} experimental fallback rows exist for these games in the coverage ledger; they are not canonical forecasts and never enter this record.`}</p>
        </div>
      </>;
    }}</AsyncView>
  );
}

/** The canonical record line above the slate (unfiltered); with a level it also names that band's settled record. */
export function CanonicalRecordLine({ sport, market, level }: { sport: Sport; market: MarketId; level?: number | null }) {
  const { state, retry } = useApi((s) => api.canonicalPerformance(sport, market, s), [sport, market]);
  return <div className="small"><AsyncView state={state} retry={retry}>{(p) => {
    const s = p.competitive;
    const row = level == null ? null : s.thresholds.find((t) => Math.abs(t.threshold - level) < 1e-9);
    return <>
      <CanonicalSummaryLine s={s} />
      {p.market_comparison.matched_games > 0 && p.market_comparison.model_brier != null && p.market_comparison.market_brier != null && (
        <p className="small muted">
          Market benchmark: model Brier {num(p.market_comparison.model_brier)} vs market {num(p.market_comparison.market_brier)}
          {' '}over {p.market_comparison.matched_games} matched games
          {' '}({p.market_comparison.market_brier < p.market_comparison.model_brier ? 'the market is the better forecaster' : 'the model is ahead on this sample; not yet evidence of a betting edge'}).
        </p>
      )}
      {row && (row.forecasts > 0
        ? <p className="small muted">
            At {pct(row.threshold)} or higher: {row.win}–{row.loss}{market === 'winner' || market === 'reg3' ? '' : `–${row.push}`} on {row.graded} settled
            {row.win_rate != null && <> ({pct(row.win_rate, 1)})</>}.
            {row.graded > 0 && row.graded < 30 && <span className="warn"> Too few settled picks to judge this threshold.</span>}
          </p>
        : <p className="small muted">No settled forecasts at {pct(level)} or higher yet.</p>)}
    </>;
  }}</AsyncView></div>;
}
