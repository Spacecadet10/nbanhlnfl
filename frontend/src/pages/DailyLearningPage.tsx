import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import { useApi } from '../api/useApi';
import type { DailyReport, MarketId, Sport, ThRecord, ThRow } from '../api/types';
import { MemberOverlapNote } from '../components/RuleLedger';
import { AsyncView } from '../components/States';
import { pct, dateTime } from '../format';

// The ledger outcome is the API's counter key; show it as a word.
const OUTCOME: Record<string, string> = { wins: 'Win', losses: 'Loss', pushes: 'Push', pending: 'Pending', void: 'Void', unscorable: 'Unscorable', no_pick: 'No pick' };
const LABELS: Record<string, string> = { winner: 'Winner', reg3: 'Regulation three-way', spread: 'Spread / puck line', total: 'Total', team_total_home: 'Home team total', team_total_away: 'Away team total' };
export function DailyLearningPage() {
  return <section><div className="page-head"><h1>Daily learning · All 11 markets</h1></div>
    <p>Discover unanimous model groups from 2 members through the eligible roster, with separate thresholds per member. Historical research and frozen prospective decisions are shown separately.</p>
    {(['nba', 'nhl'] as Sport[]).map(s => <SportLearning key={s} sport={s} />)}
  </section>;
}
function SportLearning({ sport }: { sport: Sport }) {
  const { state, retry } = useApi(s => api.dailyAgreement(sport, s), [sport]);
  return <section className="card"><h2>{sport.toUpperCase()}</h2><button onClick={retry}>Refresh status</button>
    <AsyncView state={state} retry={retry}>{d => d.mode === 'demo' ? <p>DEMO · {d.note}</p> : <>
      <p>{d.schedule}</p><p className="small muted">{d.note}</p>
      <p>Definition: <code>{d.definition}</code> · Read at {dateTime(d.as_of)} · Daily recording configuration: {d.recording_enabled ? 'enabled (see actual records below)' : 'off'}</p>
      <p>{d.fit ? <>Fit #{d.fit.fit_id} · {d.fit.status} · Published {dateTime(d.fit.finished_at)} · Results through {dateTime(d.fit.training_cutoff)}</> : 'No daily fit published for this code definition.'}</p>
      {d.error && <p role="alert">{d.error}</p>}
      <div className="table-wrap"><table className="table"><thead><tr><th>Market</th><th>Usable model fits</th><th>Historical rows</th><th>Recorded games</th><th>Frozen rule</th></tr></thead><tbody>{d.markets.map(m => <tr key={m.market}>
        <th><a href={`#daily-${sport}-${m.market}`}>{LABELS[m.market]}</a></th><td>{m.models.filter(x => x.supported && x.fit_id).length}</td><td>{m.history_games}</td><td>{m.prospective.eligible}</td><td>{m.decision?.status ?? 'Awaiting training'}</td>
      </tr>)}</tbody></table></div>
      {d.markets.map(m => <MarketLearning key={m.market} sport={sport} m={m} />)}
      <details><summary>Recent training attempts and failures</summary>{d.attempts.map(a => <p key={a.fit_id}>#{a.fit_id} {a.name} · {a.status} · {dateTime(a.finished_at)}<br /><code>{a.detail}</code></p>)}</details>
    </>}</AsyncView></section>;
}
function RecordTable({ rows }: { rows: (ThRecord & { rule: string })[] }) {
  return <div className="table-wrap"><table className="table"><thead><tr><th>Rule</th><th>Eligible</th><th>Picks</th><th>Coverage</th><th>W–L–Push</th><th>Pending</th><th>Win rate / 95% interval</th><th>Mean confidence / calibration gap</th><th>Pick dates</th></tr></thead><tbody>{rows.map((r, i) => <tr key={i}>
    <th>{r.rule}</th><td>{r.eligible}</td><td>{r.picks}</td><td>{pct(r.coverage, 1)}</td><td>{r.wins}–{r.losses}–{r.pushes}</td><td>{r.pending}</td><td>{pct(r.win_rate, 1)} / {r.win_rate_ci95?.map(x => pct(x, 1)).join('–') ?? '—'}</td><td>{pct(r.mean_p, 1)} / {pct(r.calibration_gap, 1)}</td><td>{r.first ?? '—'} – {r.last ?? '—'}</td>
  </tr>)}</tbody></table></div>;
}
function MarketLearning({ sport, m }: { sport: Sport; m: DailyReport['markets'][number] }) {
  const [period, setPeriod] = useState<'earlier' | 'later'>('later');
  const [bands, setBands] = useState(false);
  const [inspect, setInspect] = useState('');
  const [custom, setCustom] = useState('');
  const d = m.decision, p = m.prospective;
  return <details className="card" id={`daily-${sport}-${m.market}`}><summary><strong>{LABELS[m.market]}</strong> · {d?.status ?? 'Awaiting daily training'}</summary>
    <p>Supported confidence levels: {m.levels.map(x => pct(x, 1)).join(', ')}. Cumulative thresholds include all higher confidence; bands are disjoint.</p>
    {m.reason && <p className="notice">{m.reason}</p>}
    <h3>Frozen upcoming-game rule</h3><p>{d?.rule?.rule ?? 'No eligible selected rule'} · {d?.reason ?? 'Train and publish daily fits to reassess.'}</p>
    {d?.rule && <MemberOverlapNote overlap={d.member_overlap} members={d.rule.members} showNote />}
    {d && <p>Frozen with training cutoff {dateTime(d.cutoff)}. Reassessment can retain or replace tomorrow’s rule; past forecasts stay unchanged.</p>}
    {m.incumbent_error && <p role="alert">Yesterday’s rule could not be carried forward: {m.incumbent_error}</p>}
    <h3>Actually recorded live</h3><p>{p.wins}–{p.losses} · {pct(p.win_rate, 1)} (95% interval {p.win_rate_ci95?.map(x => pct(x, 1)).join('–') ?? '—'}) · {p.picks} picks / {p.eligible} recorded games · {p.pushes} pushes · {p.pending} pending · {p.void} void · {p.unscorable ?? 0} unscorable · {p.no_pick} no-pick decisions.</p>
    <details><summary>Live game evidence ({m.ledger.length})</summary>{m.ledger.length === 0 ? <p>No daily forecasts recorded.</p> : <div className="table-wrap"><table className="table"><thead><tr><th>Game</th><th>Recorded / fit</th><th>Line</th><th>Rule / reason</th><th>Outcome</th></tr></thead><tbody>{m.ledger.map(r => <tr key={r.game_id}><td><Link to={`/games/${encodeURIComponent(r.game_id)}?sport=${sport}`}>{r.game_id}</Link></td><td>{dateTime(r.generated_at)} / {r.fit_id == null ? 'no daily fit' : `#${r.fit_id}`}</td><td>{r.line ?? '—'}</td><td>{r.selection.rule?.rule} {r.selection.reason}</td><td>{OUTCOME[r.outcome] ?? r.outcome}</td></tr>)}</tbody></table></div>}</details>
    <h3>Model support and daily fit coverage</h3><div className="table-wrap"><table className="table"><thead><tr><th>Model</th><th>Fit</th><th>Historical predictions</th><th>Limitation</th></tr></thead><tbody>{m.models.map(x => <tr key={x.name}><td>{x.name}</td><td>{x.fit_id ? `#${x.fit_id}` : 'Unavailable'}</td><td>{x.historical_games}</td><td>{x.reason ?? (x.fit_id ? 'See attempts for training status' : 'Awaiting successful daily fit')}</td></tr>)}</tbody></table></div>
    <h3>Historical selection evidence · exploratory</h3>
    <form onSubmit={e => { e.preventDefault(); setInspect(custom); }}><label>Inspect exact subset (model@threshold,model@threshold)<input value={custom} onChange={e => setCustom(e.target.value)} placeholder="member@0.60,other@0.70" required /></label><button>Inspect saved evidence</button></form>
    {inspect && <Evidence key={`${inspect}-${period}`} sport={sport} market={m.market} rule={inspect} period={period} />}

    <p>Inputs: {m.source_counts.walk_forward} reconstructed chronological rows; {m.source_counts.prospective} settled recorded rows. Market fit #{m.fit_id ?? '—'}. These are selection inputs; the frozen-rule live record is shown separately above.</p>
    <p>The earlier third searches and freezes candidate rules. The later third both picks the winner and shows its record, so the chosen rule’s later numbers are optimistically biased (a winner’s-curse) and the 95% interval is nominal, not an untouched validation. The “Actually recorded live” section above is the only unbiased evidence. Line markets use the same line for every member in each game. Historical voids are not prospective voids.</p>
    <label>Evidence period <select value={period} onChange={e => setPeriod(e.target.value as 'earlier' | 'later')}><option value="earlier">Earlier search</option><option value="later">Later (selection — biased)</option></select></label>{' '}
    <label><input type="checkbox" checked={bands} onChange={e => setBands(e.target.checked)} /> Confidence bands</label>
    {!m.thresholds && <p>No daily historical predictions available. Zero coverage is not completed evaluation.</p>}
    {m.thresholds?.sources.map(s => <details key={s.key}><summary>{s.key} · {s.kind === 'average' ? 'Equal average (not unanimous agreement)' : 'Individual model'}</summary><RecordTable rows={s[period][bands ? 'bands' : 'cumulative'] as ThRow[]} />{s.kind === 'model' && <button onClick={() => setInspect(`${s.key}@${m.levels[0]}`)}>Inspect games / custom threshold</button>}</details>)}
    {d?.search && <><p>{d.search.method}. Sizes explored: {d.search.sizes.join(', ')}. {d.search.evaluated} rules evaluated; at most {d.search.budget_per_size} per size. Stops at budget or when beam mutations produce no new rules. {d.search.note}</p><p>Best earlier single-model baseline: {d.best_single}. Searched member thresholds: {d.search.thresholds.map(x => pct(x, 1)).join(', ')}.</p>
      <details><summary>Search coverage by subset size</summary><div className="table-wrap"><table className="table"><thead><tr><th>Size</th><th>Rules evaluated</th></tr></thead><tbody>{Object.entries(d.search.evaluated_by_size).map(([size, count]) => <tr key={size}><td>{size}</td><td>{count}</td></tr>)}</tbody></table></div></details>
      {d.rules.map((r, i) => <details key={i}><summary>{r.members.length} members · {r.rule}</summary><MemberOverlapNote overlap={d.member_overlap} members={r.members} /><button onClick={() => setInspect(r.members.map((n, j) => `${n}@${r.thresholds[j]}`).join(','))}>Inspect games and full unanimous threshold ladder</button><RecordTable rows={[{...r[period].record, rule: 'Unanimous subset'}, ...r[period].alone.map(a => ({...a.own, rule: `${a.model} alone @ ${pct(a.threshold, 1)}`})), ...(r[period].coverage_matched_best_single ? [{...r[period].coverage_matched_best_single!, rule: 'Strong single · matched pick count'}] : []), ...(r[period].same_games_best_single ? [{...r[period].same_games_best_single!, rule: 'Strong single · same games'}] : [])]} /><p>Season stability: {r[period].by_season.map(s => `${s.season}: ${s.wins}–${s.losses} (${s.picks} picks)`).join('; ')}</p></details>)}
      {d.rules.length === 0 && <p>No searched rules reached the minimum sample size.</p>}
    </>}
  </details>;
}

function Evidence({ sport, market, rule, period }: { sport: Sport; market: MarketId; rule: string; period: string }) {
  const [offset, setOffset] = useState(0);
  const { state, retry } = useApi(s => api.dailyEvidence(sport, market, rule, period, offset, s), [sport, market, rule, period, offset]);
  return <div className="card"><h3>Game evidence · {rule}</h3><AsyncView state={state} retry={retry}>{d => <>
    <p>{d.note} Fit #{d.fit_id} · {d.total} qualifying games.</p>
    <details><summary>All cumulative thresholds</summary><RecordTable rows={d.ladder.cumulative} /></details>
    <details><summary>All confidence bands</summary><RecordTable rows={d.ladder.bands} /></details>
    <div className="table-wrap"><table className="table"><thead><tr><th>Game / date</th><th>Line</th><th>Source</th><th>Member confidence</th><th>Result</th></tr></thead><tbody>{d.games.map(g => <tr key={g.game_id}><td><Link to={`/games/${encodeURIComponent(g.game_id)}?sport=${sport}`}>{g.game_id}</Link><br />{dateTime(g.tipoff_at)}</td><td>{g.line ?? '—'}</td><td>{g.source}</td><td>{Object.entries(g.probabilities).map(([n, p]) => `${n}: ${pct(p, 1)}`).join('; ')}</td><td>{g.result}</td></tr>)}</tbody></table></div>
    {d.total === 0 && <p>No qualifying games at these thresholds.</p>}
    <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 100))}>Previous 100</button>{' '}<button disabled={offset + 100 >= d.total} onClick={() => setOffset(offset + 100)}>Next 100</button>
  </>}</AsyncView></div>;
}
