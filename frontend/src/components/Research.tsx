// Research / shadow model selection (docs/MODEL_SELECTION.md). Never the primary forecast; never betting advice.
import { api } from '../api/client';
import type {
  CalibrationBin, MarketId, ResearchGame, ResearchMarket, ResearchRecord, ResearchReport, ResearchSlateEntry, Retrospective, RetroMarket, Side, Sport, Vector,
} from '../api/types';
import { useApi } from '../api/useApi';
import { AsyncView, Empty } from './States';
import { dateTime, num, pct } from '../format';

const NAMES: Record<string, string> = {
  elo: 'Elo', logistic: 'Logistic', logistic_v2: 'Efficiency logistic', efficiency_margin: 'Efficiency', gbt_v1: 'Boosted trees',
  availability_two_hour: 'Injury-aware logistic', hockey_elo: 'Elo', goals_poisson: 'Goals model', hockey_logistic: 'Logistic',
  hockey_gbt: 'Boosted trees', points_normal: 'Points model', ridge_points: 'Ridge points', gbt_points: 'Boosted points',
  goals_draw: 'Goals + draw adjustment', goals_glm: 'Goals regression', average: 'Equal average', stack: 'Learned stacker',
};
export const modelName = (n: string) => NAMES[n] ?? n;
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
  </>;
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
        <p className="small muted">Definition <code>{g.definition.slice(0, 10)}</code> · {g.cohorts} games · {g.settled} settled · {g.pending} pending · {g.void} void{g.unscorable > 0 && ` · ${g.unscorable} unscorable`}
          {' · lines: '}{g.line_sources.market} bookmaker, {g.line_sources.model} model-only</p>
        <h3>Individual models and combinations</h3>
        <div className="table-wrap"><table className="table">
          <thead><tr><th>Source</th><th>Games</th><th>Record</th><th>Accuracy (95%)</th><th>Log loss</th><th>Brier</th></tr></thead>
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
        <h3>Selector (shadow)</h3>
        <p className="small">Selections {g.selector.selections.pick} picks / {g.selector.selections.no_pick} no-picks · settled picks {record(g.selector.picks)}
          {' '}({pct(g.selector.picks.accuracy, 1)}, {interval(g.selector.picks.ci95)}) · recommended for the game {record(g.selector.recommended_for_game)}
          {' · '}{g.selector.pending} pending · {g.selector.void} void</p>
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
      <thead><tr><th>Method</th><th>Games</th><th>Accuracy</th><th>Log loss</th><th>Brier</th></tr></thead>
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
