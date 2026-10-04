import { ResearchSlateLine } from './Research';
import type { MarketId, ResearchSlateEntry } from '../api/types';
import { ModelAgreement } from './ModelAgreement';
import { Link } from 'react-router-dom';
import type { GameSummary, MarketForecast, MarketModelVersion, MarketPerformanceResponse, MarketRecord, Settlement } from '../api/types';
import { RunBadge, NearTipoffBadge } from './Badges';
import { MarketRecordLine } from './ConfidenceFilter';
import { useCarriedSearch, useDetails } from './Selection';
import { Empty } from './States';
import { american, calendarDate, dateTime, FULL_DATE, num, pct } from '../format';

const RESULT: Record<Settlement, { label: string; cls: string }> = {
  win: { label: 'Won', cls: 'ok' }, loss: { label: 'Lost', cls: 'bad' }, push: { label: 'Push', cls: 'muted' },
  void: { label: 'Void', cls: 'muted' }, pending: { label: 'Pending', cls: 'muted' }, unscorable: { label: 'Not scorable', cls: 'warn' },
};

export function ResultLabel({ result }: { result: Settlement }) {
  return <span className={RESULT[result].cls}>{RESULT[result].label}</span>;
}

/** The exact line a forecast was made against, and where the line came from. */
export function LineLabel({ f }: { f: MarketForecast }) {
  if (f.line == null) return <span className="muted">No line (3-way)</span>;
  const line = f.market === 'spread' && f.line > 0 ? `+${f.line}` : f.line;   // a sign only for handicaps, never totals
  return f.line_source === 'market'
    ? <span title="The bookmaker's line when the forecast was made">Line {line} · bookmaker</span>
    : f.earlier_line_rule
      ? <span className="warn" title="No bookmaker line had been retrieved, and the line was set by the rule used before Oct 2, 2026, which could place it far from an even split">Line {line} · model-only · earlier line rule</span>
      : <span className="warn" title="No bookmaker line had been retrieved when this forecast was made">Line {line} · model-only</span>;
}

export function SideBars({ f }: { f: MarketForecast }) {
  return (
    <ul className="side-bars" aria-label={`${f.title} probabilities`}>
      {f.sides.map((s) => (
        <li key={s.side} className={s.side === f.pick ? 'picked' : ''}>
          <span className="side-label">{s.label}</span>
          <span className="side-track" aria-hidden><span className="side-fill" style={{ width: `${s.probability * 100}%` }} /></span>
          <span className="side-value">{pct(s.probability, 1)}</span>
        </li>
      ))}
    </ul>
  );
}

function QuoteLine({ f }: { f: MarketForecast }) {
  if (!f.quote) return <span className="muted">No bookmaker comparison{f.line_source === 'model' ? ' (model-only line)' : ''}</span>;
  const q = f.quote;
  const a = f.sides.find((s) => s.side === 'a');
  const b = f.sides.find((s) => s.side === 'b');
  // A whole-number line can push: the two-way bookmaker pair has no push, the model's sides do (as Performance compares).
  const push = f.market === 'reg3' ? 0 : f.sides.find((s) => s.side === 'mid')?.probability ?? 0;
  return (
    <span>
      {q.bookmaker} at this line: {a?.label} {american(q.price_a)} · {b?.label} {american(q.price_b)}; without the margin {pct(q.prob_a, 1)} · {pct(q.prob_b, 1)}
      <span className="muted"> (retrieved {dateTime(q.retrieved_at)})</span>
      {push > 0 && a && b && <span className="muted"> · The bookmaker pair leaves out the push; on the same basis the model is {a.label} {pct(a.probability / (a.probability + b.probability), 1)} · {b.label} {pct(b.probability / (a.probability + b.probability), 1)}.</span>}
    </span>
  );
}

/** A slate card for one game's forecast of the selected market. */
export function MarketCard({ game: g, statusLabel, research, market }: { game: GameSummary; statusLabel: string; research?: ResearchSlateEntry; market: MarketId }) {
  const f = g.market_forecast;
  const carried = useCarriedSearch();
  const [details] = useDetails();
  return (
    <li className="card game-card">
      <Link to={`/games/${g.game_id}${carried}`} className="card-link">
        <div className="game-card-head">
          <span className="game-time">{statusLabel}</span>
          {f && (f.policy === 'near_tipoff' ? <NearTipoffBadge /> : <RunBadge runType="live" />)}
        </div>
        <p className="matchup-title"><strong>{g.away.abbr}</strong> @ <strong>{g.home.abbr}</strong>
          {g.outcome && <span className="muted"> · final {g.outcome.away_score}–{g.outcome.home_score}</span>}</p>
        {f ? (
          <>
            <div className="card-section-label">{f.title.toUpperCase()} · <LineLabel f={f} /></div>
            {f.current_version === false && <p className="warn small">Earlier model version (archive): shown for reference, not a pick of the current model and not in its counts or record.</p>}
            <SideBars f={f} />
            <p className="pick-line small">
              Pick: <strong>{f.pick_label}</strong><span className="muted"> · model confidence {pct(f.confidence, 1)}</span>
              {f.result !== 'pending' && <> · <ResultLabel result={f.result} /></>}
            </p>
            <p className="small"><QuoteLine f={f} /></p>
            <p className="small muted">Made {dateTime(f.generated_at)}</p>
            {f.limitations.length > 0 && <ul className="limitations small">{f.limitations.map((x) => <li key={x}>{x}</li>)}</ul>}
          </>
        ) : (
          <p className="forecast-note">No forecast of this market under this policy for this game.</p>
        )}
        <ModelAgreement game={g} />
        {details && <ResearchSlateLine entry={research} market={market} />}
      </Link>
    </li>
  );
}

/** Every market forecast of one game (game detail). */
export function MarketForecastsCard({ forecasts }: { forecasts: MarketForecast[] }) {
  if (forecasts.length === 0) {
    return <div className="card"><h2>Other markets</h2><p className="muted">No spread, total or team-total forecasts were recorded for this game.</p></div>;
  }
  const policies = Array.from(new Set(forecasts.map((f) => f.policy)));
  return (
    <div className="card">
      <h2>Other markets</h2>
      <p className="small muted">Each forecast is shown at the exact line it was made against. Later line moves do not change it.</p>
      {policies.map((p) => (
        <div key={p} className="market-block">
          <h3>{p === 'near_tipoff' ? 'Near-tipoff forecasts' : 'Pre-game archive forecasts'}</h3>
          {forecasts.filter((f) => f.policy === p).map((f) => (
            <div key={`${f.market}-${f.policy}-${f.model_version}`} className="market-row">
              <div className="market-row-head"><strong>{f.title}</strong> <LineLabel f={f} /> {f.result !== 'pending' && <ResultLabel result={f.result} />}
                {f.current_version === false && <span className="warn small"> · earlier model version (archive)</span>}</div>
              <SideBars f={f} />
              <p className="small"><QuoteLine f={f} /></p>
              <p className="small muted">Made {dateTime(f.generated_at)} · cutoff {dateTime(f.cutoff_at)} · <code>{f.model_version}</code></p>
              {f.limitations.length > 0 && <ul className="limitations small">{f.limitations.map((x) => <li key={x}>{x}</li>)}</ul>}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

/** Records cover one market-model version: the current sealed model by default, an earlier one as an archive view. */
export function MarketVersionSelect({ versions, value, onChange }: {
  versions: MarketModelVersion[]; value: string; onChange: (v: string) => void;
}) {
  const earlier = versions.filter((v) => !v.current);
  if (earlier.length === 0) return null;
  return (
    <p className="small">
      <label>Model version{' '}
        <select value={value} onChange={(e) => onChange(e.target.value)}>
          <option value="">Current sealed model{versions.some((v) => v.current) ? '' : ' (no forecasts yet)'}</option>
          {earlier.map((v) => <option key={v.model_version} value={v.model_version}>Earlier: {v.model_version} ({v.games} games)</option>)}
        </select>
      </label>{' '}
      <span className="muted">{value ? 'Archive: forecasts of an earlier market model version, kept separate from the current record.'
        : `${earlier.length} earlier version${earlier.length > 1 ? 's' : ''} stored; each has its own record.`}</span>
    </p>
  );
}

export function MarketHistoryTable({ rows }: { rows: MarketRecord[] }) {
  const carried = useCarriedSearch();
  return (
    <table className="table history">
      <thead>
        <tr><th>Date</th><th>Game</th><th>Line</th><th>Pick</th><th>Model confidence</th><th>Result</th><th>Outcome of pick</th><th>Recorded</th></tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={`${r.game_id}-${r.forecast.market}`}>
            <td data-label="Date">{calendarDate(r.game_date, FULL_DATE)}</td>
            <td data-label="Game"><Link to={`/games/${r.game_id}${carried}`}>{r.away.abbr} @ {r.home.abbr}</Link></td>
            <td data-label="Line"><LineLabel f={r.forecast} /></td>
            <td data-label="Pick"><strong>{r.forecast.pick_label}</strong></td>
            <td data-label="Model confidence">{pct(r.forecast.confidence, 1)}</td>
            <td data-label="Result">{r.outcome ? `${r.away.abbr} ${r.outcome.away_score} – ${r.outcome.home_score} ${r.home.abbr}` : <span className="muted">Pending</span>}</td>
            <td data-label="Outcome of pick"><ResultLabel result={r.forecast.result} /></td>
            <td data-label="Recorded">{dateTime(r.forecast.generated_at)}{r.lead_days != null && r.lead_days >= 2 && <span className="warn small"> · {r.lead_days} days before</span>}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function MarketReport({ perf: p }: { perf: MarketPerformanceResponse }) {
  return (
    <>
      <p className="muted">{p.title}</p>
      <p className="notice" role="note">{p.note}</p>
      {p.limitations.length > 0 && <ul className="limitations small">{p.limitations.map((x) => <li key={x}>{x}</li>)}</ul>}
      <MarketRecordLine record={p.record} scope="All dates" />
      {p.record.settled === 0 ? (
        <Empty>No forecasts of this market have been settled yet ({p.forecasts} recorded, {p.record.pending} pending, {p.record.voids} void, {p.record.unscorable} unscorable).</Empty>
      ) : (
        <div className="tiles">
          <div className="tile"><div className="tile-label">Settled</div><div className="tile-value">{p.record.settled}</div><div className="tile-hint">Wins, losses and pushes.</div></div>
          <div className="tile"><div className="tile-label">Brier score</div><div className="tile-value">{num(p.brier_score)}</div><div className="tile-hint">Summed over sides and push/draw (0–2 scale); lower is better.</div></div>
          <div className="tile"><div className="tile-label">Log loss</div><div className="tile-value">{num(p.log_loss)}</div><div className="tile-hint">Lower is better.</div></div>
        </div>
      )}
      <div className="card">
        <h2>Versus the bookmaker, same line only</h2>
        <p className="small muted">Line sources: {Object.entries(p.line_sources).map(([k, v]) => `${v} ${k === 'model' ? 'model-only' : 'bookmaker'}`).join(' · ') || 'none yet'}. Settled forecasts made at a quoted line: {p.market_matched}.</p>
        {p.market_matched === 0 ? <Empty>No settled forecasts at a bookmaker line yet.</Empty> : (
          <table className="table compare">
            <thead><tr><th /><th>Log loss</th></tr></thead>
            <tbody>
              <tr><th scope="row">Model</th><td>{num(p.model_log_loss_on_matched)}</td></tr>
              <tr><th scope="row">Bookmaker (de-vigged)</th><td>{num(p.market_log_loss_on_matched)}</td></tr>
            </tbody>
          </table>
        )}
      </div>
      {p.reference && (
        <div className="card">
          <h2>Historical test <RunBadge runType="backtest" /></h2>
          <p className="small muted">{p.reference.label}</p>
          {p.reference.model_note && <p className="warn small">{p.reference.model_note}</p>}
          <table className="table compare">
            <thead><tr><th>Line</th><th>Games</th><th>Log loss</th><th>League-average model</th><th>Pick accuracy</th></tr></thead>
            <tbody>
              {Object.entries(p.reference.by_line).map(([k, v]) => (
                <tr key={k}><th scope="row">{k}</th><td>{v.n}</td><td>{num(v.log_loss)}</td><td>{num(v.league_reference_log_loss)}</td><td>{pct(v.pick_accuracy_excluding_mid, 1)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
