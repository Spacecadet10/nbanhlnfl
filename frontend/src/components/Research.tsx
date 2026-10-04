// Research / shadow model selection (docs/MODEL_SELECTION.md). Never the primary forecast; never betting advice.
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type {
  CalibrationBin, Candidate, CoreMemberCoverage, HistoricalStart, HistoricalStartRule, CandidatePoolState, CandidateRoster, PoolReport, SelectionRuleView, SelectionStatus, MarketId, RegisteredRule, ResearchExpanded, RuleReport, ResearchGame, ResearchMarket, ResearchRecord, ResearchReport, ResearchSlateEntry, Retrospective, RetroMarket, Side, Sport, Vector,
} from '../api/types';
import { useApi } from '../api/useApi';
import { AsyncView, Empty } from './States';
import { dateTime, num, pct } from '../format';

const NAMES: Record<string, string> = {
  elo: 'Elo', logistic: 'Logistic', logistic_v2: 'Efficiency logistic', efficiency_margin: 'Efficiency', gbt_v1: 'Boosted trees',
  availability_two_hour: 'Injury-aware logistic', hockey_elo: 'Elo', goals_poisson: 'Goals model', hockey_logistic: 'Logistic',
  hockey_gbt: 'Boosted trees', points_normal: 'Points model', ridge_points: 'Ridge points', gbt_points: 'Boosted points',
  goals_draw: 'Goals + draw adjustment', goals_glm: 'Goals regression', average: 'Equal average', stack: 'Learned stacker',
  'ridge_points@weekly': 'Ridge points (weekly refit, shadow)', 'gbt_points@weekly': 'Boosted points (weekly refit, shadow)',
  hockey_logistic_xg: 'Logistic + shot quality (shadow)',
};
export const modelName = (n: string) => n.startsWith('avg:')
  ? `Average: ${n.slice(4).split('+').map((m) => NAMES[m] ?? m).join(' + ')}` : NAMES[n] ?? n;
const MARKET: Record<string, string> = { winner: 'Winner', reg3: 'Regulation 3-way', spread: 'Spread / puck line', total: 'Total',
  team_total_home: 'Home team total', team_total_away: 'Away team total' };
const FILTER: Record<string, string> = { none: 'no agreement filter', majority: 'strict majority agrees', unanimous: 'all models agree' };
const STATUS: Record<string, string> = { active: 'Active rule', abstain: 'Abstaining', insufficient_evidence: 'Insufficient evidence' };
const SIDES: Side[] = ['a', 'mid', 'b'];

const sideIdx = (s: Side) => SIDES.indexOf(s);
const record = (r: ResearchRecord) => `${r.wins}–${r.losses}${r.pushes ? `–${r.pushes}P` : ''}`;
const interval = (ci: [number, number] | null) => (ci ? `${pct(ci[0], 0)}–${pct(ci[1], 0)}` : '—');

/** Research line for a Today card: the selected market's own research status, its exact line, missing inputs and timing,
 * plus the game-level recommendation when that is a different market. */
export function ResearchSlateLine({ entry, market }: { entry: ResearchSlateEntry | undefined; market: MarketId }) {
  if (!entry || entry.status === 'none') return null;
  const m = entry.markets[market];
  const missing = m ? Object.entries(m.missing) : [];
  return <div className="research-line small">
    <span className="badge badge-research">Research</span>{' '}
    {!m ? <span className="muted">{MARKET[market]}: no research cohort recorded for this market.</span> : <>
      <strong>{MARKET[market]}</strong>{m.line != null && <> {m.line}</>}
      {m.line_source && <span className="muted"> ({m.line_source === 'market' ? 'bookmaker line' : 'model-only line'})</span>}
      {': '}{m.status === 'pick'
        ? <><strong>{m.pick}</strong> <span className="muted">({pct(m.probability, 1)}{m.threshold != null && `, threshold ${pct(m.threshold)}`})</span>
            {m.recommended && <span className="ok"> · game recommendation</span>}</>
        : <span className="muted">no pick · {m.reason}</span>}
      <span className="muted"> · made {Math.round(m.minutes_before_start)} min before start</span>
      {m.start_changed && <span className="warn"> · start time changed: void</span>}
      {missing.length > 0 && <div className="warn">Missing: {missing.map(([n, why]) => `${modelName(n)} (${why})`).join('; ')}</div>}
    </>}
    {entry.status === 'pick' && entry.market !== market && <div className="muted">Game recommendation: {entry.label} ({MARKET[entry.market]})</div>}
  </div>;
}

function Calibration({ bins }: { bins: CalibrationBin[] | undefined }) {
  if (!bins || bins.length === 0) return <span className="muted">—</span>;
  return <>{bins.map((b, i) => <span key={i} className="calib-bin">{pct(b.mean_p, 0)}→{pct(b.win_rate, 0)} <span className="muted">({b.n})</span>{i < bins.length - 1 ? ' · ' : ''}</span>)}</>;
}

export function ResearchGameCard({ sport, gameId }: { sport: Sport; gameId: string }) {
  const { state, retry } = useApi((s) => api.researchGame(sport, gameId, s), [sport, gameId]);
  return <div className="card">
    <h2>Model selection <span className="badge badge-research">Research</span></h2>
    <AsyncView state={state} retry={retry}>{(r) => <ResearchGameBody r={r} />}</AsyncView>
  </div>;
}

function ResearchGameBody({ r }: { r: ResearchGame }) {
  if (r.markets.length === 0) {
    return <Empty>No research forecast for this game. Research cohorts are recorded only in the two hours before the scheduled
      start, alongside the primary forecast.</Empty>;
  }
  const rec = r.recommendation;
  return <>
    <p className="small muted">{r.note}</p>
    <p className="research-verdict">{rec
      ? <>Selected market: <strong>{MARKET[rec.market]} · {rec.label}</strong> <span className="muted">({pct(rec.probability, 1)} from the rule's source)</span></>
      : <><strong>No pick.</strong> <span className="muted">No market passed its frozen selection rule for this game.</span></>}</p>
    {r.markets.map((m) => <ResearchMarketView key={m.market} m={m} />)}
    {r.expanded && r.expanded.length > 0 && <>
      <h3>Expanded experiment <span className="badge badge-research">Recording only</span></h3>
      <p className="small muted">{r.expanded_note}</p>
      {r.expanded.map((x) => <ExpandedView key={x.market} x={x} core={r.markets.find((m) => m.market === x.market)} />)}
    </>}
  </>;
}

function ExpandedView({ x, core }: { x: ResearchExpanded; core: ResearchMarket | undefined }) {
  const labels = core?.sides ?? { a: 'Home / over', mid: null, b: 'Away / under' };
  const sides = SIDES.filter((s) => s !== 'mid' || labels.mid);
  const missing = Object.entries(x.missing);
  return <details className="research-market">
    <summary><strong>{MARKET[x.market]}</strong>{x.line != null && <> · line {x.line}</>} · all {x.agreement.expected} models incl. new ones
      {' · '}{x.agreement.side ? <>{x.agreement.agreeing} of {x.agreement.expected} pick {labels[x.agreement.side]}{x.agreement.unanimous ? ' (unanimous)' : ''}</> : 'split'}</summary>
    <div className="table-wrap"><table className="table research-table">
      <thead><tr><th>Model</th>{sides.map((s) => <th key={s}>{labels[s]}</th>)}</tr></thead>
      <tbody>
        {x.members.map((m) => <tr key={m.name}><th scope="row">{modelName(m.name)}</th>{sides.map((s) => <td key={s}>{pct(m.p[sideIdx(s)], 1)}</td>)}</tr>)}
        {missing.map(([n, why]) => <tr key={n} className="muted"><th scope="row">{modelName(n)}</th><td colSpan={sides.length} className="warn small wrap">Missing: {why}</td></tr>)}
        <Combined label="Equal average (all models)" v={x.average} sides={sides} note="needs every model" />
        <Combined label="Learned stacker (all models)" v={x.stack} sides={sides} note="not fitted for this cohort" />
      </tbody>
    </table></div>
    <p className="small muted">Made {dateTime(x.generated_at)}, {Math.round(x.minutes_before_start)} min before the start · <code>{x.definition.slice(-24)}</code></p>
  </details>;
}

function ResearchMarketView({ m }: { m: ResearchMarket }) {
  const sel = m.selection;
  const d = sel?.decision;
  const sides = SIDES.filter((s) => s !== 'mid' || m.sides.mid);
  const missing = Object.entries(m.missing);
  return <details className="research-market" open={sel?.recommended}>
    <summary>
      <strong>{MARKET[m.market]}</strong>{m.line != null && <> · line {m.line > 0 && m.market === 'spread' ? '+' : ''}{m.line}</>}
      {' · '}{sel?.status === 'pick' ? <span className="ok">{sel.recommended ? 'Recommended' : 'Passes'}: {m.sides[sel.side!]}</span>
        : <span className="muted">No pick</span>}
      {missing.length > 0 && <span className="warn"> · {missing.length} model(s) missing</span>}
    </summary>
    {m.start_changed && <p className="warn small">The scheduled start changed after this was recorded: it is void and not counted.</p>}
    <p className="small">{!sel ? 'Not evaluated by the selector.' : sel.status === 'pick' ? 'Passes the rule in force (below).' : sel.reason}{sel && !sel.recommended && sel.status === 'pick' && <span className="muted"> · {sel.game_reason}</span>}</p>
    <div className="table-wrap"><table className="table research-table">
      <thead><tr><th>Model</th>{sides.map((s) => <th key={s}>{m.sides[s]}</th>)}</tr></thead>
      <tbody>
        {m.members.map((x) => <tr key={x.name}><th scope="row">{modelName(x.name)}{x.name === m.primary && <span className="muted"> (primary)</span>}</th>
          {sides.map((s) => <td key={s}>{pct(x.p[sideIdx(s)], 1)}</td>)}</tr>)}
        {missing.map(([n, why]) => <tr key={n} className="muted"><th scope="row">{modelName(n)}</th><td colSpan={sides.length} className="warn small wrap">Missing: {why}</td></tr>)}
        <Combined label="Equal average" v={m.average} sides={sides} note="needs every model" />
        <Combined label="Learned stacker" v={m.stack} sides={sides} note={m.stack_note} />
        {m.bookmaker && <Combined label={`Bookmaker (${m.bookmaker.provider}, de-vigged)`} v={m.bookmaker.p} sides={sides} note="" />}
      </tbody>
    </table></div>
    <p className="small">Agreement (picks, not a probability): {m.agreement.side
      ? <><strong>{m.agreement.agreeing} of {m.agreement.expected}</strong> pick {m.sides[m.agreement.side]}{m.agreement.unanimous ? ' · unanimous' : m.agreement.majority ? ' · strict majority' : ' · no strict majority'}</>
      : 'models are split'}{!m.agreement.complete && <span className="warn"> · incomplete cohort ({m.agreement.available}/{m.agreement.expected})</span>}
      {m.agreement.abstentions > 0 && <span className="muted"> · {m.agreement.abstentions} tie(s) abstain</span>}</p>
    <dl className="facts small">
      <dt>Line</dt><dd>{m.line == null ? 'No line (winner / 3-way)' : <>{m.line} · {m.line_label}</>}</dd>
      <dt>Made</dt><dd>{dateTime(m.generated_at)}, {Math.round(m.minutes_before_start)} min before the scheduled start</dd>
      <dt>Rule in force</dt><dd>{!d ? 'No selector decision yet' : <>{STATUS[d.status]}{d.rule && <>: {modelName(d.rule.source)}, {FILTER[d.rule.filter]}, threshold {pct(d.rule.threshold)}</>}</>}</dd>
    </dl>
    <details className="audit small"><summary>Audit details</summary>
      <dl className="facts">
        {d && <><dt>Decision</dt><dd>{d.reason}</dd>
          <dt>Effective</dt><dd>{dateTime(d.effective_from)} until {dateTime(d.effective_until)} · evidence up to {dateTime(d.evidence_until)}</dd>
          <dt>Selector</dt><dd><code>{d.selector_version}</code></dd></>}
        <dt>Research definition</dt><dd><code>{m.definition}</code></dd>
        {m.bookmaker && <><dt>Bookmaker quote</dt><dd>{m.bookmaker.provider}, retrieved {dateTime(m.bookmaker.retrieved_at)} ·
          {m.bookmaker.quote_id != null ? <> <code>{m.bookmaker.quote_table}#{m.bookmaker.quote_id}</code></> : ' quote id unknown (not recorded for this row)'}</dd></>}
        <dt>Information time</dt><dd>{dateTime(m.information_at)}</dd>
        {m.agreement.pairs.length > 0 && <><dt>Pairs</dt><dd>{m.agreement.pairs.map((p) => `${modelName(p.members[0])} + ${modelName(p.members[1])}: ${p.agree ? 'agree' : 'differ'}`).join(' · ')}</dd></>}
      </dl>
    </details>
  </details>;
}

function Combined({ label, v, sides, note }: { label: string; v: Vector | null; sides: Side[]; note: string }) {
  return <tr className="research-combined"><th scope="row">{label}</th>
    {v ? sides.map((s) => <td key={s}>{pct(v[sideIdx(s)], 1)}</td>) : <td colSpan={sides.length} className="muted small">Not available{note ? `: ${note}` : ''}</td>}</tr>;
}

const KIND: Record<string, string> = { single: 'alone', unanimous_min: 'all agree, each at least', avg: 'average at least',
  unanimous_maxdis: 'all agree, probabilities within', majority: 'strict majority', stack: 'learned stacker at least' };
const LEVEL: Record<string, string> = { all: 'all games', close: 'close games (favourite < 60%)', clear: 'clear favourite (≥ 60%)',
  low: 'models within 5 points', high: 'models more than 5 points apart' };
export const combinationLabel = (r: Pick<RegisteredRule, 'kind' | 'members' | 't' | 'level'>) =>
  `${r.members.map(modelName).join(' + ')} · ${KIND[r.kind] ?? r.kind}${r.t != null ? ` ${pct(r.t, 0)}` : ''} · ${LEVEL[r.level] ?? r.level}`;

/** Registered combination rules for one sport and market: historical exploration next to the prospective record. */
export function CombinationRules({ sport, market }: { sport: Sport; market: MarketId }) {
  const [show, setShow] = useState<'selected' | 'all'>('selected');
  const [definition, setDefinition] = useState('');     // '' = the newest research version with recorded games
  useEffect(() => setDefinition(''), [sport, market]);
  const { state, retry } = useApi((s) => api.researchRules(sport, market, show, s, definition), [sport, market, show, definition]);
  return <div className="card">
    <h2>Combination rules <span className="badge badge-research">Research</span></h2>
    <div className="segmented small" role="group" aria-label="Rules shown">
      <button type="button" aria-pressed={show === 'selected'} onClick={() => setShow('selected')}>Selected</button>
      <button type="button" aria-pressed={show === 'all'} onClick={() => setShow('all')}>All registered</button>
    </div>
    <AsyncView state={state} retry={retry}>{(r) => <>
      {(r.definitions?.length ?? 0) > 0 && <label className="small">Research version{' '}
        <select value={r.definition ?? ''} onChange={(e) => setDefinition(e.target.value)}>
          {r.definitions!.map((d) => <option key={d.definition} value={d.definition}>{d.definition} · {d.games} {d.games === 1 ? 'game' : 'games'} · last {dateTime(d.last_at)}</option>)}
        </select>
        <span className="muted"> One record per research version: results under different model code are never pooled.</span>
      </label>}
      <RulesBody r={r} />
    </>}</AsyncView>
  </div>;
}

function RulesBody({ r }: { r: RuleReport }) {
  if (r.status === 'no_registered_rules' || r.mode === 'demo') return <Empty>{r.note ?? 'No combination rules are registered for this market.'}</Empty>;
  return <>
    <p className="small muted">{r.note}</p>
    <p className="small">{r.registered_rules} registered rules ({r.selected_rules} selected by the chronological procedure) · members {r.members?.map(modelName).join(', ')}
      {' · '}cohorts: {r.cohort_source === 'expanded' ? 'expanded experiment' : 'core experiment'} · {r.cohorts} games recorded since {r.effective_from && dateTime(r.effective_from)}
      {' · '}comparator: {r.comparator && modelName(r.comparator)} · registry <code>{r.registry_sha256?.slice(0, 10)}</code></p>
    <h3>Historical exploration <span className="badge">Backtest · reused seasons</span></h3>
    <div className="table-wrap"><table className="table small">
      <thead><tr><th>Coverage band</th><th>Best rule (hindsight)</th><th>Picks</th><th>Accuracy</th><th>Strongest single, same count</th><th>By season</th></tr></thead>
      <tbody>{(r.frontier_historical ?? []).map((b) => <tr key={b.band[0]}><th scope="row">{pct(b.band[0], 0)}–{pct(Math.min(1, b.band[1]), 0)}</th>
        {b.rule ? <><td className="wrap">{combinationLabel(parseRule(b.rule))}</td><td>{b.picks} ({pct(b.coverage ?? null, 0)})</td><td>{pct(b.accuracy ?? null, 1)}</td>
          <td>{pct(b.comparator_accuracy ?? null, 1)} <span className="muted">({b.comparator && modelName(b.comparator)})</span></td>
          <td className="wrap">{Object.entries(b.per_season ?? {}).map(([s, x]) => `${s}: ${pct(x.accuracy, 0)} on ${x.picks}`).join(' · ')}</td></>
          : <td colSpan={5} className="muted">no rule with 100+ picks in this band</td>}</tr>)}</tbody>
    </table></div>
    <p className="small muted">Chosen with hindsight across all reused seasons — descriptive only. Selection for tracking used earlier seasons only.</p>
    <h3>Prospective record <span className="badge badge-live">Live</span></h3>
    {r.rules.length === 0 ? <Empty>No rules to show.</Empty> : <div className="table-wrap"><table className="table small">
      <thead><tr><th>Rule</th><th>Status</th><th>Later-season check (backtest)</th><th>Picks / coverage</th><th>Record</th><th>Accuracy (95%)</th>
        <th>Strongest single, same count</th><th>Best member, same count</th><th>Pending</th></tr></thead>
      <tbody>{r.rules.map((x) => <RuleRow key={x.id} x={x} />)}</tbody>
    </table></div>}
  </>;
}

function parseRule(id: string) {
  const [kind, members, t, level] = id.split('|');
  return { kind, members: members.split('+'), t: t === 'None' ? null : Number(t), level };
}

function RuleRow({ x }: { x: RegisteredRule }) {
  const p = x.prospective;
  const bestMember = p.constituents_matched.filter((c) => c.accuracy != null).sort((a, b) => (b.accuracy ?? 0) - (a.accuracy ?? 0))[0];
  return <tr><th scope="row" className="wrap">{combinationLabel(x)}{x.prospective_only && <span className="muted"> (prospective only)</span>}</th>
    <td>{x.selected ? <span className={x.selected === 'qualified' ? 'ok' : 'warn'}>{x.selected}</span> : <span className="muted">tracked</span>}{x.why && <div className="muted">{x.why}</div>}</td>
    <td className="wrap">{x.historical.length ? x.historical.map((h) => `${h.assess_season}: ${pct(h.accuracy, 0)} on ${h.picks}`).join(' · ') : <span className="muted">—</span>}</td>
    <td>{p.picks} / {pct(p.coverage, 0)}</td><td>{p.win}–{p.loss}{p.push ? `–${p.push}P` : ''}</td>
    <td>{pct(p.accuracy, 1)} <span className="muted">{interval(p.ci95)}</span></td>
    <td>{p.strongest_single_matched ? pct(p.strongest_single_matched.accuracy, 1) : '—'}</td>
    <td>{bestMember ? <>{pct(bestMember.accuracy, 1)} <span className="muted">({modelName(bestMember.model)})</span></> : '—'}</td>
    <td>{p.pending}{p.void ? ` · ${p.void} void` : ''}</td></tr>;
}

const llDiff = (v: unknown): string => {
  const x = typeof v === 'number' ? v : (v && typeof v === 'object' && 'mean' in v ? (v as { mean: number }).mean : null);
  return x == null ? '—' : `${x >= 0 ? '+' : ''}${x.toFixed(4)}`;
};

/** Candidate pool: every registered candidate with its status, evidence and whether it may be selected. */
export function CandidateRosterCard({ sport, market }: { sport: Sport; market: MarketId }) {
  const { state, retry } = useApi((s) => api.researchCandidates(sport, s), [sport]);
  return <div className="card">
    <h2>Candidate pool <span className="badge badge-research">Research</span></h2>
    <AsyncView state={state} retry={retry}>{(r) => <RosterBody r={r} market={market} />}</AsyncView>
  </div>;
}

function RosterBody({ r, market }: { r: CandidateRoster; market: MarketId }) {
  if (r.candidates.length === 0) return <Empty>{r.note}</Empty>;
  const rows = [...r.candidates].sort((a, b) => Number(b.markets.includes(market)) - Number(a.markets.includes(market)));
  return <>
    <h3>Models in use</h3>
    <p className="small muted">{r.coverage_note}</p>
    <div className="table-wrap"><table className="table small">
      <thead><tr><th>Model</th><th>Fitted</th><th>Forecasting</th><th>Agreement</th><th>Selectable</th></tr></thead>
      <tbody>
        {(r.members ?? []).map((m) => <MemberCoverageRow key={`${m.markets}-${m.name}`} m={m} />)}
        {rows.filter((c) => c.coverage).map((c) => { const v = c.coverage!; return <tr key={`cand-${c.name}`}>
          <th scope="row" className="wrap">{c.name}<div className="muted">candidate · {c.markets.map((x) => MARKET[x] ?? x).join(', ')}</div></th>
          <td>{v.fitted ? 'yes' : <span className="muted">{v.earlier_fit ? 'earlier version only' : 'no'}</span>}</td>
          <td>{v.latest_forecast_at ? <>{v.forecasting ? 'yes' : 'not recently'}<div className="muted">{dateTime(v.latest_forecast_at)}</div></> : <span className="muted">no</span>}
            {v.earlier_forecast_at && <div className="muted">earlier version: {dateTime(v.earlier_forecast_at)}</div>}</td>
          <td className="wrap">{v.in_agreement_tracking.length ? <>tracked ({v.in_agreement_tracking.map((x) => MARKET[x] ?? x).join(', ')}), never votes</> : <span className="muted">not tracked</span>}</td>
          <td className="wrap">{v.selectable.alone.length ? `alone: ${v.selectable.alone.map((x) => MARKET[x] ?? x).join(', ')}` : ''}
            {v.selectable.inside.length ? <div>inside: {v.selectable.inside.join(', ')}</div> : null}
            {!v.selectable.alone.length && !v.selectable.inside.length && <span className="muted">no</span>}</td></tr>; })}
      </tbody>
    </table></div>
    <h3>Candidates</h3>
    <p className="small muted">{r.note} Log-loss differences are against the strongest existing member (negative = better).</p>
    <div className="table-wrap"><table className="table small">
      <thead><tr><th>Candidate</th><th>Markets</th><th>Training</th><th>Latest fit</th><th>{MARKET[market]}: screen (backtest)</th>
        <th title="Judged on seasons that include the reserved final-test seasons, reused after the one-time test: exploratory, not held-out">{MARKET[market]}: reused test seasons (backtest)</th><th>Prospective games</th><th>{MARKET[market]}: pool sources</th><th>Selection</th></tr></thead>
      <tbody>{rows.map((c) => <CandidateRow key={c.name} c={c} market={market} />)}</tbody>
    </table></div>
  </>;
}

function CandidateRow({ c, market }: { c: Candidate; market: MarketId }) {
  const e = c.evaluation[market];
  return <tr className={c.markets.includes(market) ? '' : 'muted'}>
    <th scope="row" className="wrap">{c.name}<div className="muted">{c.family} · {c.role}</div></th>
    <td className="wrap">{c.markets.map((m) => MARKET[m] ?? m).join(', ')}</td>
    <td className="wrap">{c.training_status}</td>
    <td className="wrap">{c.latest_fit ? <>{dateTime(c.latest_fit.first_used_at)} · {c.latest_fit.training_games} games</> : '—'}</td>
    <td>{e ? <>{llDiff(e.vs_strongest)}{e.shortlisted && <span className="ok"> · shortlisted</span>}</> : <span className="muted">—</span>}</td>
    <td>{e?.assess ? <>{llDiff(e.assess.vs_strongest)}{e.assess.confirmed_gains.length > 0 && <span className="ok"> · gain confirmed</span>}</> : <span className="muted">—</span>}</td>
    <td>{c.prospective.games} <span className="muted">({c.prospective.settled} settled)</span></td>
    <td className="wrap"><PoolSources st={c.pool?.[market]} /></td>
    <td className="wrap">{c.selection}{c.pool?.[market]?.decision && <div className="muted">
      {MARKET[market]} pool decision: {decisionLabel(c.pool[market].decision!)}</div>}</td></tr>;
}

function MemberCoverageRow({ m }: { m: CoreMemberCoverage }) {
  return <tr>
    <th scope="row" className="wrap">{modelName(m.name)}<div className="muted">{m.role} · {m.markets}</div></th>
    <td className="wrap">{m.fit_register ? <>{dateTime(m.fit_register.first_used_at)} · {m.fit_register.training_games} games</> : <span className="muted">no fit row for the current version (rating or online model, or not fitted yet)</span>}
      {m.earlier_fit && <div className="muted">earlier version: {dateTime(m.earlier_fit.first_used_at)}</div>}</td>
    <td>{m.latest_forecast_at ? <>yes<div className="muted">{dateTime(m.latest_forecast_at)}</div></> : <span className="muted">none in 7 days (current version)</span>}
      {m.earlier_forecast_at && <div className="muted">earlier version: {dateTime(m.earlier_forecast_at)}</div>}</td>
    <td>{m.votes ? 'votes' : 'recorded, never votes'}</td>
    <td>{m.selectable ? 'yes (core selector source)' : 'no'}</td></tr>;
}

const ruleText = (r: HistoricalStartRule) => `${modelName(r.source)}${r.filter === 'none' ? '' : ` + ${r.filter} agreement`} at ${pct(r.threshold, 0)}+`;

/** Separately labelled historical starting policy: a frozen rule chosen on walk-forward history (outcome base rate, no odds)
 *  and its record on later two-hour research cohorts. Never the live selector's record, never a bookmaker comparison. */
export function HistoricalStartCard({ sport, market }: { sport: Sport; market: MarketId }) {
  const { state, retry } = useApi((s) => api.researchHistoricalStart(sport, market, s), [sport, market]);
  return <div className="card">
    <h2>Historical starting policy <span className="badge badge-research">Research · historical</span></h2>
    <AsyncView state={state} retry={retry}>{(r) => <HistoricalStartBody r={r} />}</AsyncView>
  </div>;
}

function HistoricalStartBody({ r }: { r: HistoricalStart }) {
  if (r.status !== 'frozen' || !r.decision) return <Empty>{r.note}</Empty>;
  const d = r.decision, c = d.chronological_check, l = r.live;
  return <>
    <p className="small muted">{r.label}</p>
    {r.matches_current_policy === false && <p className="warn small">Frozen under an earlier policy version ({r.frozen_policy}); build the current version.</p>}
    {r.matches_current_definition === false && <p className="warn small">Built for an earlier research definition: current forecasts use changed model code, so none are counted here.</p>}
    <p><strong>{d.status === 'active' && d.rule ? ruleText(d.rule) : d.status === 'abstain' ? 'No rule qualified' : 'Not enough history'}</strong></p>
    <p className="small muted">{d.reason}. {d.games} games, {d.from} to {d.until} ({d.rules_compared} rules compared, {d.eligible_rules} with enough picks). Reference: {r.reference}.</p>
    {Object.keys(d.excluded).length > 0 && <p className="small muted">Not offered: {Object.entries(d.excluded).map(([k, v]) => `${modelName(k)} (${v})`).join('; ')}.</p>}
    {c && <p className="small">Chronological check (each week chosen on earlier games only; seasons already used to choose the models, so exploratory):{' '}
      {c.picks} picks, {c.wins}–{c.losses}{c.pushes ? `–${c.pushes}` : ''} ({pct(c.accuracy, 1)}), lift over the base rate {c.lift == null ? '—' : `${(c.lift * 100).toFixed(1)} pts`}, active {c.active_weeks} of {c.weeks} weeks.
      {c.mean_probability != null && <> The picks' average model probability was {pct(c.mean_probability, 1)} and their base rate {pct(c.mean_reference ?? null, 1)}: winning near the model's own probability is calibration, not an edge over a bookmaker.</>}</p>}
    <h3>Since frozen <span className="muted small">({r.frozen_at && dateTime(r.frozen_at)})</span></h3>
    {!l || l.picks === 0 ? <p className="small muted">No picks yet on two-hour research cohorts made after the policy was frozen ({l?.cohorts_after_freeze ?? 0} cohorts).</p> : <>
      <p>{l.picks} picks · {l.wins}–{l.losses}{l.pushes ? `–${l.pushes}` : ''} · {l.pending} pending · {l.void} void{l.accuracy != null && <> · {pct(l.accuracy, 1)}</>}</p>
      <p className="small muted">Bookmaker view of the same picks (separate from the record): {l.bookmaker.quoted_decided} quoted and decided{l.bookmaker.mean_implied != null && <>, implied {pct(l.bookmaker.mean_implied, 1)} on average, {l.bookmaker.quoted_wins} won</>}.</p>
    </>}
  </>;
}

const ROUTE: Record<string, string> = { standalone: 'standalone route', combination: 'combination route' };

function decisionLabel(d: { status: string; rule: { source: string; filter: string; threshold: number } | null; reason: string }): string {
  return d.status === 'active' && d.rule ? `${d.rule.source} / ${d.rule.filter} / ${d.rule.threshold.toFixed(2)}`
    : d.status === 'insufficient_evidence' ? 'insufficient evidence' : d.status;
}

/** A candidate's selector sources in one market: the sealed route and, once decided, the prospective record. */
function PoolSources({ st }: { st: CandidatePoolState | undefined }) {
  if (!st || st.sources.length === 0) return <span className="muted">not a selector source</span>;
  return <>{st.sources.map((x) => <div key={x.source}>{x.source} <span className="muted">({ROUTE[x.route] ?? x.route}
    {x.prospective ? `; ${x.prospective.wins}-${x.prospective.losses} prospective` : ''})</span></div>)}</>;
}

/** Candidate-pool selector: sealed sources per market (standalone or in-combination value on reused test seasons), their
 *  prospective record on settled cohorts, and the weekly pool decisions. Separate from the core selector. */
export function PoolSelectionCard({ sport, market }: { sport: Sport; market: MarketId }) {
  const { state, retry } = useApi((s) => api.researchPool(sport, s), [sport]);
  return <div className="card">
    <h2>Candidate-pool selector <span className="badge badge-research">Research</span></h2>
    <AsyncView state={state} retry={retry}>{(r) => <PoolBody r={r} market={market} />}</AsyncView>
  </div>;
}

function PoolBody({ r, market }: { r: PoolReport; market: MarketId }) {
  const m = r.markets[market];
  if (!m) return <Empty>{r.mode === 'demo' ? r.note : `No candidate-pool sources registered for ${MARKET[market] ?? market}.`}</Empty>;
  const d = m.decisions[0];
  return <>
    <p className="small muted">Registry {r.registry} (effective {r.effective_from ? dateTime(r.effective_from) : '—'}). {r.rule}</p>
    {!!r.suspended?.length && <p className="small warn">Suspended sources (recorded, never selectable):{' '}
      {r.suspended.map((s) => `${s.market}: ${s.source}`).join('; ')}. {r.suspended[0].reason}</p>}
    <p className="small">{d ? <>Pool decision for the week from {dateTime(d.scheduled_for)}: <strong>{decisionLabel(d)}</strong>
      {d.status !== 'active' && <span className="muted"> — {d.reason}</span>}</> : <span className="muted">No pool decision written yet.</span>}
      {' '}<span className="muted">· {m.settled_cohorts} settled cohorts</span></p>
    <div className="table-wrap"><table className="table small">
      <thead><tr><th>Source</th><th>Route</th><th>Held-out evidence (backtest)</th><th>Settled with vector</th><th>Best rule (prospective)</th></tr></thead>
      <tbody>{m.sources.map((x) => <tr key={x.source}>
        <th scope="row" className="wrap">{x.source}</th>
        <td>{ROUTE[x.route] ?? x.route}</td>
        <td className="wrap">{llDiff(x.evidence.mean)} {x.evidence.ci95 && <span className="muted">[{x.evidence.ci95.map((v) => v.toFixed(4)).join(', ')}]</span>}
          <div className="muted">{x.evidence.comparison}</div></td>
        <td>{x.settled_with_vector}</td>
        <td className="wrap">{x.best_rule ? <>{x.best_rule.wins}-{x.best_rule.losses} at {x.best_rule.threshold.toFixed(2)} ({x.best_rule.filter})
          {x.best_rule.lower != null && <span className="muted"> · lower bound {x.best_rule.lower.toFixed(3)}</span>}</> : <span className="muted">—</span>}</td>
      </tr>)}</tbody>
    </table></div>
    <p className="small muted">{r.note}</p>
  </>;
}

export function ResearchPerformance({ sport, market }: { sport: Sport; market: MarketId }) {
  const { state, retry } = useApi((s) => api.researchPerformance(sport, market, s), [sport, market]);
  return <div className="card">
    <h2>Model selection · Forward research <span className="badge badge-research">Research</span></h2>
    <AsyncView state={state} retry={retry}>{(r) => <ReportBody r={r} />}</AsyncView>
  </div>;
}

function ReportBody({ r }: { r: ResearchReport }) {
  return <>
    <p className="small muted">{r.note}</p>
    {r.groups.length === 0 && <Empty>No research cohorts recorded for this market yet. They are recorded within two hours of each eligible game.</Empty>}
    {r.groups.map((g) => {
      const primary = Object.keys(g.sources)[0];
      return <div key={g.definition}>
        <h3>{g.recording_only ? <>Expanded experiment ({g.experiment}) <span className="badge badge-research">Recording only</span></> : 'Core experiment'}</h3>
        {g.recording_only && <p className="small warn">Every model including the new ones. Recorded and scored for research; never used for selections, recommendations or promotion.</p>}
        <p className="small muted">Definition <code>{g.definition.slice(0, 10)}</code> · {g.cohorts} games · {g.settled} settled · {g.pending} pending · {g.void} void{g.unscorable > 0 && ` · ${g.unscorable} unscorable`}
          {' · lines: '}{g.line_sources.market} bookmaker, {g.line_sources.model} model-only</p>
        <h3>Individual models and combinations</h3>
        <div className="table-wrap"><table className="table">
          <thead><tr><th>Source</th><th>Games</th><th>Record</th><th>Accuracy (95%)</th><th>Log loss</th><th title="Summed over all outcomes, so it runs from 0 to 2. For a two-way result it is twice the 0–1 Brier score on the main Performance tiles (always 50% = 0.5 here, 0.25 there).">Brier (0–2 scale)</th></tr></thead>
          <tbody>{Object.entries(g.sources).map(([n, s]) => <tr key={n}><th scope="row">{modelName(n)}{n === primary && <span className="muted"> (primary)</span>}</th>
            <td>{s.cohorts}</td><td>{record(s)}{s.ties > 0 && <span className="muted small"> · {s.ties} exact 50% (no pick)</span>}</td><td>{pct(s.accuracy, 1)} <span className="muted small">{interval(s.ci95)}</span></td>
            <td>{num(s.log_loss)}</td><td>{num(s.brier)}</td></tr>)}</tbody>
        </table></div>
        <h3>Agreement versus confidence at the same coverage</h3>
        <div className="table-wrap"><table className="table">
          <thead><tr><th>Rule</th><th>Coverage</th><th>Record</th><th>Accuracy</th><th>Primary's most confident</th><th>Average's most confident</th>
            <th>Best single model's most confident</th></tr></thead>
          <tbody>{(['majority', 'unanimous'] as const).map((k) => { const a = g.agreement[k]; return <tr key={k}>
            <th scope="row">{k === 'majority' ? 'Strict majority' : 'Unanimous'}</th><td>{pct(a.coverage, 0)}</td><td>{record(a)}</td>
            <td>{pct(a.accuracy, 1)}</td><td>{pct(a.primary_confidence_same_count.accuracy, 1)}</td>
            <td>{pct(a.average_confidence_same_count.accuracy, 1)}</td>
            <td>{a.best_single && a.best_single_confidence_same_count ? <>{pct(a.best_single_confidence_same_count.accuracy, 1)} <span className="muted small">({modelName(a.best_single)})</span></> : <span className="muted">unavailable</span>}</td></tr>; })}</tbody>
        </table></div>
        <p className="small muted">Same number of games, ranked within these games (descriptive). The best single model is chosen on earlier seasons (retrospective study), never on these results.</p>
        <details className="small"><summary>Reliability (predicted probability of the pick → observed win rate, games per bin)</summary>
          <div className="table-wrap"><table className="table"><thead><tr><th>Source</th><th>Bins</th></tr></thead>
            <tbody>{Object.entries(g.sources).map(([n, s]) => <tr key={n}><th scope="row">{modelName(n)}</th><td className="wrap"><Calibration bins={s.calibration} /></td></tr>)}</tbody>
          </table></div><p className="muted">A table only: no calibration is fitted to these results.</p></details>
        {g.selector && <><h3>Selector (shadow)</h3>
        <p className="small">Selections {g.selector.selections.pick} picks / {g.selector.selections.no_pick} no-picks · settled picks {record(g.selector.picks)}
          {' '}({pct(g.selector.picks.accuracy, 1)}, {interval(g.selector.picks.ci95)}) · recommended for the game {record(g.selector.recommended_for_game)}
          {' · '}{g.selector.pending} pending · {g.selector.void} void</p></>}
        <details className="small"><summary>Confidence thresholds (descriptive, not tuned)</summary>
          <div className="table-wrap"><table className="table">
            <thead><tr><th>Source</th>{g.sources[primary].thresholds.map((t) => <th key={t.threshold}>≥{pct(t.threshold)}</th>)}</tr></thead>
            <tbody>{Object.entries(g.sources).map(([n, s]) => <tr key={n}><th scope="row">{modelName(n)}</th>
              {s.thresholds.map((t) => <td key={t.threshold}>{record(t)}{t.decided > 0 && <span className="muted"> {pct(t.accuracy, 0)}</span>}</td>)}</tr>)}</tbody>
          </table></div></details>
        <details className="small"><summary>By week</summary>
          <div className="table-wrap"><table className="table"><thead><tr><th>Week</th><th>Games</th><th>Primary</th><th>Average</th></tr></thead>
            <tbody>{g.periods.map((p) => <tr key={p.week}><th scope="row">{p.week}</th><td>{p.cohorts}</td><td>{record(p.primary)}</td><td>{record(p.average)}</td></tr>)}</tbody>
          </table></div></details>
        <p className="small muted">Bookmaker comparison: {g.bookmaker.cohorts === 0 ? 'no settled games with a bookmaker quote at the same line.'
          : `${g.bookmaker.cohorts} games · primary log loss ${num(g.bookmaker.primary_log_loss)} vs bookmaker ${num(g.bookmaker.bookmaker_log_loss)}. ${g.bookmaker.note}`}</p>
      </div>;
    })}
    <h3>Weekly decisions</h3>
    {r.decisions.length === 0 ? <p className="small muted">No decision yet. Decisions are made every Monday 14:00 UTC from results known by then.</p>
      : <div className="table-wrap"><table className="table small"><thead><tr><th>Evidence to</th><th>Status</th><th>Rule</th><th>Lower bound</th><th>Effective</th><th>Settlement rules</th></tr></thead>
        <tbody>{r.decisions.map((d) => <tr key={d.decision_id}><th scope="row">{dateTime(d.scheduled_for)}</th><td>{STATUS[d.status]}</td>
          <td>{d.rule ? `${modelName(d.rule.source)} · ${FILTER[d.rule.filter]} · ≥${pct(d.rule.threshold)}` : <span className="muted">{d.reason}</span>}</td>
          <td>{d.lower_bound == null ? '—' : `${d.lower_bound >= 0 ? '+' : ''}${num(d.lower_bound)}`}</td>
          <td>{dateTime(d.decided_at)} – {dateTime(d.effective_until)}</td>
          <td>{d.settlement_version ? <code>{d.settlement_version}</code> : <span className="muted">unknown (not recorded then)</span>}</td></tr>)}</tbody></table></div>}
  </>;
}

export function RetrospectiveStudy({ sport, market }: { sport: Sport; market: MarketId }) {
  const { state, retry } = useApi((s) => api.researchRetrospective(sport, s), [sport]);
  return <details className="card retrospective">
    <summary><strong>Retrospective study (exploratory)</strong> <span className="badge badge-backtest">Backtest</span></summary>
    <AsyncView state={state} retry={retry}>{(r) => <RetroBody r={r} market={market} />}</AsyncView>
  </details>;
}

const diff = (d: { diff?: number; mean?: number; ci95: [number, number] } | null | undefined, digits = 3) => {
  if (!d) return '—';
  const v = d.diff ?? d.mean ?? 0;
  return `${v >= 0 ? '+' : ''}${v.toFixed(digits)} [${d.ci95[0].toFixed(digits)}, ${d.ci95[1].toFixed(digits)}]`;
};

/** 'pair:elo+logistic' → 'Pair: Elo + Logistic'; 'majority+average>=0.6' → 'Strict majority, average ≥ 60%'. */
function ruleLabel(rule: string) {
  if (rule.startsWith('pair:')) return `Pair: ${rule.slice(5).split('+').map(modelName).join(' + ')}`;
  const [kind, cond] = rule.split('+average>=');
  const base = kind === 'majority' ? 'Strict majority' : 'Unanimous';
  return cond ? `${base}, average ≥ ${pct(Number(cond))}` : base;
}

function RetroBody({ r, market }: { r: Retrospective; market: MarketId }) {
  const m: RetroMarket | undefined = r.markets[market];
  if (r.status === 'unavailable' || !m) return <Empty>No retrospective study for this sport and market.</Empty>;
  return <>
    <p className="notice small" role="note">{r.note} Seasons {m.seasons.join(', ')}; computed {dateTime(r.generated_at)}. Not live performance.</p>
    <div className="table-wrap"><table className="table small">
      <thead><tr><th>Method</th><th>Games</th><th>Accuracy</th><th>Log loss</th><th title="Summed over all outcomes, so it runs from 0 to 2. For a two-way result it is twice the 0–1 Brier score on the main Performance tiles (always 50% = 0.5 here, 0.25 there).">Brier (0–2 scale)</th></tr></thead>
      <tbody>{Object.entries(m.methods).map(([n, s]) => <tr key={n}><th scope="row">{modelName(n)}{n === m.primary && <span className="muted"> (primary)</span>}</th>
        <td>{s.n}</td><td>{pct(s.accuracy, 1)}</td><td>{num(s.log_loss, 4)}</td><td>{num(s.brier, 4)}</td></tr>)}
        {m.stack.methods?.stack && <tr><th scope="row">Learned stacker <span className="muted">(seasons {m.stack.seasons.join(', ')})</span></th>
          <td>{m.stack.methods.stack.n}</td><td>{pct(m.stack.methods.stack.accuracy, 1)}</td><td>{num(m.stack.methods.stack.log_loss, 4)}</td><td>—</td></tr>}</tbody>
    </table></div>
    <p className="small">Paired log-loss differences (negative = better, week-block 95%):</p>
    <ul className="small">{Object.entries(m.log_loss_diff).map(([k, v]) => <li key={k}>{k.split('-').map(modelName).join(' − ')}: {diff(v, 4)}</li>)}</ul>
    <div className="table-wrap"><table className="table small">
      <thead><tr><th>Agreement rule</th><th>Coverage</th><th>Accuracy</th><th>vs primary's confidence</th><th>vs average's confidence</th><th>vs best single (chosen on earlier seasons)</th></tr></thead>
      <tbody>{m.agreement.map((a) => <tr key={a.rule}><th scope="row">{ruleLabel(a.rule)}</th>
        <td>{pct(a.coverage, 0)}</td><td>{pct(a.accuracy, 1)}</td><td>{diff(a.vs_primary_confidence)}</td><td>{diff(a.vs_average_confidence)}</td>
        <td>{diff(a.vs_best_prior_confidence)}{a.best_prior_games != null && <span className="muted small"> ({a.best_prior_games} games)</span>}</td></tr>)}</tbody>
    </table></div>
    {m.best_single_prior && <p className="small muted">Best single model per season, chosen on earlier seasons only: {Object.entries(m.best_single_prior)
      .map(([season, b]) => `${season}: ${b.model ? modelName(b.model) : 'none (no earlier out-of-sample season)'}`).join(' · ')}.</p>}
    <details className="small"><summary>Reliability (predicted probability of the pick → observed win rate)</summary>
      <div className="table-wrap"><table className="table"><tbody>{Object.entries(m.methods).map(([n, s]) => <tr key={n}><th scope="row">{modelName(n)}</th>
        <td className="wrap"><Calibration bins={s.calibration} /></td></tr>)}</tbody></table></div></details>
    <p className="small">Simulated weekly selector: {m.selector_simulation.picks} picks ({pct(m.selector_simulation.coverage, 0)} coverage),
      accuracy {pct(m.selector_simulation.accuracy, 1)}, active {m.selector_simulation.active_weeks}/{m.selector_simulation.weeks} weeks;
      primary's most confident games, same count (ranked within the same span, in hindsight; descriptive): {pct(m.selector_simulation.primary_confidence_same_count?.accuracy, 1)}
      {' '}({diff(m.selector_simulation.primary_confidence_same_count?.diff)}); best single model chosen on earlier seasons, same count:
      {' '}{pct(m.selector_simulation.best_single_confidence_same_count?.accuracy, 1)} ({diff(m.selector_simulation.best_single_confidence_same_count?.diff)}
      {m.selector_simulation.best_single_confidence_same_count && `, ${m.selector_simulation.best_single_confidence_same_count.games} games`}).</p>
  </>;
}

export function ResearchHistoryTable({ sport }: { sport: Sport }) {
  const { state, retry } = useApi((s) => api.researchHistory(sport, s), [sport]);
  return <details className="card">
    <summary><strong>Research recommendations</strong> <span className="badge badge-research">Research</span></summary>
    <AsyncView state={state} retry={retry}>{(h) => h.games_with_selection_run === 0
      ? <Empty>No research selections recorded yet.</Empty>
      : <>
        <p className="small">{h.games_with_selection_run} games evaluated · {h.games_without_pick} with no pick · record {record(h.record)}
          {' '}({pct(h.record.accuracy, 1)}, {interval(h.record.ci95)}) · {h.record.pending} pending · {h.record.void} void</p>
        <div className="table-wrap"><table className="table small">
          <thead><tr><th>Game</th><th>Market</th><th>Pick</th><th>Rule</th><th>Made</th><th>Result</th></tr></thead>
          <tbody>{h.rows.map((x) => <tr key={x.game_id}><th scope="row">{x.away} @ {x.home}<br /><span className="muted">{dateTime(x.tipoff_at)}</span></th>
            <td>{MARKET[x.market]}{x.line_source === 'model' && <span className="muted"> (model line)</span>}</td><td>{x.pick} <span className="muted">{pct(x.probability, 0)}</span></td>
            <td>{x.rule ? `${modelName(x.rule.source)}, ≥${pct(x.rule.threshold)}` : '—'}</td><td>{Math.round(x.minutes_before_start)} min before</td>
            <td className={x.result === 'win' ? 'ok' : x.result === 'loss' ? 'bad' : 'muted'}>{x.result}</td></tr>)}</tbody>
        </table></div>
      </>}</AsyncView>
  </details>;
}

/** One row per market: the rule the selectors have in force (exact model or subset, agreement requirement, threshold),
 * its version, selection date, evidence cutoff, next review and evidence, or why none is selected. */
export function SelectionStatusCard({ sport, market }: { sport: Sport; market: MarketId }) {
  const { state, retry } = useApi((s) => api.researchStatus(sport, s), [sport]);
  return <div className="card">
    <h2>Selection status by market <span className="badge badge-research">Research</span></h2>
    <AsyncView state={state} retry={retry}>{(r) => <StatusBody r={r} market={market} />}</AsyncView>
  </div>;
}

function ruleCell(v: SelectionRuleView | null) {
  if (!v) return <span className="muted">no pool sources</span>;
  if (!v.rule) return <><strong>{v.label}</strong>{v.reason && <div className="muted">{v.reason}</div>}</>;
  const rec = v.evidence?.record;
  return <><strong>{modelName(v.rule.source)}</strong>
    <div>{v.rule.agreement}; confidence ≥ {pct(v.rule.threshold, 0)}</div>
    {rec && <div className="muted">{rec.wins}–{rec.losses}{rec.pushes ? `–${rec.pushes}P` : ''} on {rec.picks} picks
      ({pct(rec.coverage, 0)} coverage, {rec.weeks} weeks); lift lower bound {rec.lower.toFixed(3)}</div>}</>;
}

function reviewCell(v: SelectionRuleView | null) {
  if (!v || v.status === 'no_decision') return <span className="muted">—</span>;
  return <span className="small">{v.rule ? 'selected' : 'decided'} {v.selected_at ? dateTime(v.selected_at) : '—'}<br />
    evidence to {v.evidence_until ? dateTime(v.evidence_until) : '—'}<br />next review {v.next_review ? dateTime(v.next_review) : '—'}<br />
    <span className="muted">{v.selector_version}{v.registry ? ` · registry ${v.registry}` : ''} · {v.evidence?.settled_cohorts ?? 0} settled</span></span>;
}

function StatusBody({ r, market }: { r: SelectionStatus; market: MarketId }) {
  if (r.mode === 'demo' || !r.markets.length) return <Empty>{r.note}</Empty>;
  return <>
    <p className="small muted">Research definition {r.definition} · {r.selector_version}{r.pool_registry ? ` · pool registry ${r.pool_registry}` : ''}
      {r.pool_registry_error && <span className="warn"> · pool registry unavailable: {r.pool_registry_error}</span>}</p>
    <div className="table-wrap"><table className="table small">
      <thead><tr><th>Market</th><th>Status</th><th>Core selector</th><th>Candidate-pool selector</th><th>Decision</th><th>Recording</th></tr></thead>
      <tbody>{r.markets.map((m) => <tr key={m.market} className={m.market === market ? 'row-selected' : undefined}>
        <th scope="row">{MARKET[m.market] ?? m.market}</th>
        <td>{m.status}</td>
        <td className="wrap">{ruleCell(m.core)}</td>
        <td className="wrap">{ruleCell(m.pool)}{m.pool?.eligible_sources && <div className="muted">{m.pool.eligible_sources.length} eligible sources</div>}</td>
        <td>{reviewCell(m.pool?.rule ? m.pool : m.core)}</td>
        <td className="wrap">{m.recording.latest_cohort_at ? <>last {dateTime(m.recording.latest_cohort_at)}</> : <span className="muted">none yet</span>}
          {Object.entries(m.recording.missing).map(([k, why]) => <div key={k} className="muted">{modelName(k)} missing: {why}</div>)}</td>
      </tr>)}</tbody>
    </table></div>
    <p className="small muted">{r.note}</p>
  </>;
}
