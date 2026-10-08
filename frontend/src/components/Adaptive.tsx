// adaptive-v1 (docs/ADAPTIVE_SYSTEM.md): within-season refits of every learned member and a learned combination, separately
// versioned from the primary system. Research only: never the primary forecast, never betting advice.
import { api } from '../api/client';
import type { AdaptiveGame, AdaptiveMarket, AdaptiveReplayMarket, AdaptiveStatus, Side, Sport } from '../api/types';
import { useApi } from '../api/useApi';
import { modelName } from './Research';
import { AsyncView, Empty } from './States';
import { dateTime, num, pct } from '../format';

const MARKET: Record<string, string> = { winner: 'Winner', reg3: 'Regulation 3-way', spread: 'Spread / puck line', total: 'Total',
  team_total_home: 'Home team total', team_total_away: 'Away team total' };
const SIDES: Side[] = ['a', 'mid', 'b'];
const SOURCE: Record<string, string> = { primary: 'Primary (existing)', average: 'Core average (existing)', stack: 'Core season-start stacker (existing)',
  adaptive_core: 'Adaptive weights on the core members', adaptive: 'Adaptive combination (adaptive-v1)' };

/** A member's display name: the adaptive wrapper and candidate prefixes are explained once in the card text. */
export const adaptiveName = (n: string) => {
  const base = n.replace(/@adaptive$/, '').replace(/^cand:/, '').replace(/@market$/, '');
  const known = modelName(base);
  const label = known === base ? base : known;
  return n.endsWith('@adaptive') ? `${label} (adaptive refit)` : n.endsWith('@market') ? `${label} (market model)` : label;
};

export function AdaptiveGameCard({ sport, gameId }: { sport: Sport; gameId: string }) {
  const { state, retry } = useApi((s) => api.adaptiveGame(sport, gameId, s), [sport, gameId]);
  return <div className="card">
    <h2>Adaptive combination <span className="badge badge-research">adaptive-v1 · Research</span></h2>
    <AsyncView state={state} retry={retry}>{(a) => <AdaptiveGameBody a={a} />}</AsyncView>
  </div>;
}

function AdaptiveGameBody({ a }: { a: AdaptiveGame }) {
  if (a.mode === 'demo') return <Empty>{a.note}</Empty>;
  if (a.markets.length === 0) {
    return <Empty>No adaptive forecast for this game. Adaptive forecasts are recorded in the two hours before the scheduled start,
      from the artifacts prepared by the scheduled training job; they are separate from the primary forecast above.</Empty>;
  }
  return <>
    <p className="small muted">{a.note}</p>
    {a.markets.map((m) => <AdaptiveMarketView key={m.market} m={m} />)}
  </>;
}

function AdaptiveMarketView({ m }: { m: AdaptiveMarket }) {
  const sides = SIDES.filter((s) => s !== 'mid' || m.sides.mid);
  const sel = m.selection;
  return <div className="research-market">
    <h3>{MARKET[m.market] ?? m.market}{m.line != null && <> · line {m.line}</>} {m.line_label && <span className="muted small">({m.line_label})</span>}</h3>
    {m.status === 'forecast' && m.p
      ? <p className="research-verdict">{sides.map((s) => <span key={s}>{m.sides[s]} <strong>{pct(m.p![SIDES.indexOf(s)], 1)}</strong>{' '}</span>)}
        <span className="muted small">from {m.contributing.length} contributing models</span></p>
      : <p className="research-verdict"><strong>No adaptive probability.</strong> <span className="muted">{m.reason}</span></p>}
    <p className="small">{sel.status === 'pick'
      ? <><strong>Pick: {sel.label ?? sel.side}</strong> ({pct(sel.probability, 1)}) — {sel.reason}</>
      : <><strong>No pick.</strong> <span className="muted">{sel.reason}</span></>}</p>
    <p className="small muted">Odds: {m.bookmaker
      ? <>bookmaker {sides.map((s) => `${m.sides[s]} ${pct(m.bookmaker!.p[SIDES.indexOf(s)], 1)}`).join(' · ')} ({m.bookmaker.provider}, {dateTime(m.bookmaker.retrieved_at)}){m.edge && <>; model minus bookmaker {sides.map((s) => `${m.sides[s]} ${(m.edge![SIDES.indexOf(s)] * 100).toFixed(1)} pts`).join(' · ')}</>}</>
      : m.odds_status}. Comparison only; the probabilities are learned from outcomes, not from odds.</p>
    <p className="small muted">Training cutoff {m.training_cutoff ? dateTime(m.training_cutoff) : '—'} · weights updated {m.weights_updated_at ? dateTime(m.weights_updated_at) : '—'} ·
      forecast {dateTime(m.generated_at)} ({m.minutes_before_start} min before the start){m.start_changed && <span className="warn"> · start changed since: void</span>} · <code>{m.definition}</code></p>
    {m.members.length > 0 && <div className="table-wrap"><table className="table small">
      <thead><tr><th>Model</th>{sides.map((s) => <th key={s}>{m.sides[s]}</th>)}<th>Weight</th><th>Role</th></tr></thead>
      <tbody>{m.members.map((r) => <tr key={r.name}><th scope="row" className="wrap">{adaptiveName(r.name)}</th>
        {sides.map((s) => <td key={s}>{pct(r.p[SIDES.indexOf(s)], 1)}</td>)}
        <td>{r.weight == null ? '—' : num(r.weight, 2)}</td><td className="wrap muted">{r.role}</td></tr>)}</tbody>
    </table></div>}
    {Object.keys(m.missing).length > 0 && <p className="small muted">Missing: {Object.entries(m.missing).map(([k, v]) => `${adaptiveName(k)} — ${v}`).join('; ')}</p>}
  </div>;
}

export function AdaptiveStatusCard({ sport }: { sport: Sport }) {
  const { state, retry } = useApi((s) => api.adaptiveStatus(sport, s), [sport]);
  return <div className="card">
    <h2>Adaptive system <span className="badge badge-research">adaptive-v1 · Research</span></h2>
    <AsyncView state={state} retry={retry}>{(a) => <AdaptiveStatusBody a={a} />}</AsyncView>
  </div>;
}

function AdaptiveStatusBody({ a }: { a: AdaptiveStatus }) {
  if (a.mode === 'demo') return <Empty>{a.note}</Empty>;
  const c = a.combiner;
  return <>
    <p className="small muted">{a.note}</p>
    <p className="small">Definition <code>{a.definition ?? '—'}</code>{a.definition_error && <span className="bad"> {a.definition_error}</span>} ·
      {a.enabled ? ' enabled' : ' disabled (RESEARCH_SELECTION=0)'} · training due: {a.due.due ? <strong>yes</strong> : 'no'} — {a.due.reason}</p>
    <h3>Schedule</h3>
    <ul className="small">{Object.entries(a.schedule).map(([k, v]) => <li key={k}><strong>{k}</strong>: {v}</li>)}</ul>
    <h3>Combination in force</h3>
    {c == null ? <Empty>No combiner fit for this definition yet. Nothing adaptive is forecast until the scheduled training job
      (<code>adaptive-train</code>, run nightly by <code>daily</code>) has produced one.</Empty>
      : <>
        <p className="small">Fit #{c.fit_id} for period {c.period}; results through {c.training_cutoff ? dateTime(c.training_cutoff) : '—'};
          weights updated {dateTime(c.weights_updated_at)}.
          {c.status === 'partial' && <span className="warn"> Partial run ({c.partial_reason}): its usable markets apply, the others keep the
            latest earlier combination, and the next scheduled run retries.</span>}</p>
        <div className="table-wrap"><table className="table small">
          <thead><tr><th>Market</th><th>Members and weights</th><th>Training rows</th><th>L2</th><th>Excluded at training</th><th>Selector</th></tr></thead>
          <tbody>{Object.entries(c.markets).map(([mk, v]) => <tr key={mk}><th scope="row">{MARKET[mk] ?? mk}
            {c.market_fit_ids[mk] != null && c.market_fit_ids[mk] !== c.fit_id && <div className="muted">in force: earlier fit #{c.market_fit_ids[mk]}</div>}</th>
            <td className="wrap">{v.weights ? Object.entries(v.weights).map(([n, w]) => `${adaptiveName(n)} ${w.toFixed(2)}`).join(' · ') : <span className="muted">{v.reason ?? 'not trained'}</span>}</td>
            <td>{v.training ? <>{v.training.games}<div className="muted">{v.training.prospective} prospective · {v.training.walk_forward} walk-forward · seasons {v.training.seasons.join(', ')}</div></> : '—'}</td>
            <td>{v.l2 ?? '—'}</td>
            <td className="wrap muted">{v.excluded && Object.keys(v.excluded).length ? Object.entries(v.excluded).map(([n, r]) => `${adaptiveName(n)}: ${r}`).join('; ') : 'none'}</td>
            <td className="wrap muted">{c.selection[mk] ? `${c.selection[mk].status}: ${c.selection[mk].reason}` : '—'}</td></tr>)}</tbody>
        </table></div>
        <p className="small muted">Not evaluated as contributors: {Object.entries(c.excluded).map(([n, r]) => `${adaptiveName(n)} — ${r}`).join('; ')}.</p>
      </>}
    {a.last_failure && <p className="small bad">Latest combiner training failed at {dateTime(a.last_failure.at)} ({a.last_failure.period}): {a.last_failure.error}. The previous valid artifact stays in force.</p>}
    <h3>Member fits in force</h3>
    {a.members.length === 0 ? <p className="small muted">No member fit yet.</p>
      : <div className="table-wrap"><table className="table small">
        <thead><tr><th>Member</th><th>Period</th><th>Results through</th><th>Training games</th><th>Since</th><th>Latest failure</th></tr></thead>
        <tbody>{a.members.map((m) => <tr key={m.name}><th scope="row" className="wrap">{adaptiveName(m.name)}</th>
          <td>{m.fit_in_force?.period ?? '—'}</td><td>{m.fit_in_force?.training_cutoff ? dateTime(m.fit_in_force.training_cutoff) : '—'}</td>
          <td>{m.fit_in_force?.training_games ?? '—'}</td><td>{m.fit_in_force ? dateTime(m.fit_in_force.since) : <span className="warn">none</span>}</td>
          <td className={m.last_failure ? 'bad wrap' : 'muted'}>{m.last_failure ? `${dateTime(m.last_failure.at)}: ${m.last_failure.error}` : '—'}</td></tr>)}</tbody>
      </table></div>}
    <h3>Prospective record</h3>
    {Object.keys(a.prospective.markets).length === 0 ? <p className="small muted">No settled adaptive forecast yet ({a.prospective.pending} pending).</p>
      : <div className="table-wrap"><table className="table small">
        <thead><tr><th>Market</th><th>Settled</th><th>Record</th><th>Log loss</th><th>Brier</th><th>vs primary (same games)</th><th>vs bookmaker (same games, same line)</th></tr></thead>
        <tbody>{Object.entries(a.prospective.markets).map(([mk, v]) => <tr key={mk}><th scope="row">{MARKET[mk] ?? mk}</th><td>{v.settled}</td>
          <td>{v.record.wins}–{v.record.losses}{v.record.pushes ? `–${v.record.pushes}P` : ''}</td><td>{num(v.log_loss)}</td><td>{num(v.brier)}</td>
          <td>{v.primary.games ? `${num(v.primary.adaptive_log_loss)} vs ${num(v.primary.primary_log_loss)} (${v.primary.games})` : '—'}</td>
          <td>{v.bookmaker.games ? `${num(v.bookmaker.adaptive_log_loss)} vs ${num(v.bookmaker.bookmaker_log_loss)} (${v.bookmaker.games}, ${pct(v.bookmaker.coverage, 0)} quoted)` : 'no quotes at the forecast line'}</td></tr>)}</tbody>
      </table></div>}
    <p className="small muted">{a.prospective.note} Pending: {a.prospective.pending}.</p>
    <h3>Historical replay</h3>
    {a.replay ? <ReplayView r={a.replay} /> : <p className="small muted">No replay summary committed for this sport.</p>}
    <h3>Recent training attempts</h3>
    {a.attempts.length === 0 ? <p className="small muted">None.</p>
      : <div className="table-wrap"><table className="table small">
        <thead><tr><th>When</th><th>Kind</th><th>Name</th><th>Period</th><th>Status</th><th>Detail</th></tr></thead>
        <tbody>{a.attempts.slice(0, 15).map((t) => <tr key={t.fit_id}><td>{dateTime(t.finished_at)}</td><td>{t.kind}</td>
          <td className="wrap">{adaptiveName(t.name)}</td><td>{t.period}</td><td className={t.status === 'failed' ? 'bad' : t.status === 'skipped' ? 'warn' : 'ok'}>{t.status}</td>
          <td className="wrap muted">{t.status === 'failed' ? String(t.detail.error ?? '') : t.training_games != null ? `${t.training_games} games` : ''}</td></tr>)}</tbody>
      </table></div>}
  </>;
}

function ReplayView({ r }: { r: AdaptiveStatus['replay'] & object }) {
  return <>
    <p className="small muted">Chronological replay on development seasons {r.evaluated_seasons.join(', ')} (loaded {r.loaded_seasons[0]}–{r.loaded_seasons[r.loaded_seasons.length - 1]}; reserved test seasons never read),
      {' '}{r.members.length} adaptive members, run {dateTime(r.started_at)} in {Math.round(r.seconds / 60)} min. Identical games and lines; log loss is lower-is-better. Exploratory, not a live result.</p>
    {Object.entries(r.markets).map(([mk, m]) => <ReplayMarket key={mk} mk={mk} m={m} />)}
  </>;
}

function ReplayMarket({ mk, m }: { mk: string; m: AdaptiveReplayMarket }) {
  const srcs = Object.keys(m.sources);
  return <div className="research-market">
    <h4>{MARKET[mk] ?? mk} — {m.scored_games} scored games{m.pushes ? ` (+${m.pushes} pushes)` : ''} of {m.evaluated_games}</h4>
    <div className="table-wrap"><table className="table small">
      <thead><tr><th>Source</th><th>Coverage</th><th>Log loss</th><th>Brier</th><th>Accuracy</th><th>Adaptive − source (log loss, 95% block CI)</th></tr></thead>
      <tbody>{srcs.map((s) => { const v = m.sources[s]; const d = m.paired[`adaptive_minus_${s}`]; return <tr key={s}>
        <th scope="row" className="wrap">{SOURCE[s] ?? s}</th><td>{pct(m.coverage[s], 0)}</td><td>{v ? num(v.log_loss, 4) : '—'}</td><td>{v ? num(v.brier, 4) : '—'}</td>
        <td>{v ? pct(v.accuracy, 1) : '—'}</td>
        <td className={d ? (d.ci95[1] < 0 ? 'ok' : d.ci95[0] > 0 ? 'bad' : 'muted') : 'muted'}>{d ? `${d.mean >= 0 ? '+' : ''}${d.mean.toFixed(4)} [${d.ci95[0].toFixed(4)}, ${d.ci95[1].toFixed(4)}]` : '—'}</td></tr>; })}</tbody>
    </table></div>
    <p className="small muted">Last adaptive weights: {m.adaptive_weights_last ? Object.entries(m.adaptive_weights_last).map(([n, w]) => `${adaptiveName(n)} ${w.toFixed(2)}`).join(' · ') : '—'}
      {m.adaptive_excluded_last && Object.keys(m.adaptive_excluded_last).length > 0 && <>; excluded at the last fit: {Object.keys(m.adaptive_excluded_last).map(adaptiveName).join(', ')}</>}
      ; {m.adaptive_fit_count} combiner refits; L2 {m.adaptive_l2_last ?? '—'}.</p>
  </div>;
}
