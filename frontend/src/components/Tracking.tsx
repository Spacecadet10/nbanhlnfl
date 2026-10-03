// Persistent agreement tracking tracking-v2 (docs/MODEL_SELECTION.md §18). Research only; never betting advice.
import { useMemo, useState } from 'react';
import { api } from '../api/client';
import type {
  MarketId, Sport, TrackingCell, TrackingCheck, TrackingEvidence, TrackingGame, TrackingGroup, TrackingKind, TrackingReport,
} from '../api/types';
import { useApi } from '../api/useApi';
import { AsyncView, Empty } from './States';
import { modelName } from './Research';
import { dateTime, pct } from '../format';

const KIND: Record<TrackingKind, string> = {
  single: 'alone', unanimous: 'all agree', avg: 'probability average', majority: 'strict majority',
  stack: 'learned stacker', stack_pool: 'pool stacker',
};
const SIDE: Record<string, string> = { a: 'home / over', mid: 'draw / push', b: 'away / under' };
const sideName = (market: string, side: string) => {
  if (market === 'winner' || market === 'reg3') return ({ a: 'home', mid: 'regulation draw', b: 'away' } as Record<string, string>)[side];
  if (market === 'spread') return ({ a: 'home covers', mid: 'push', b: 'away covers' } as Record<string, string>)[side];
  return ({ a: 'over', mid: 'push', b: 'under' } as Record<string, string>)[side];
};
const STATUS: Record<string, string> = { pick: 'pick', disagree: 'members disagree', tie: 'tie', no_majority: 'no majority', incomplete: 'incomplete' };
const ci = (c: [number, number] | null) => (c ? `${pct(c[0], 0)}–${pct(c[1], 0)}` : '');
const rec = (c: TrackingCell) => `${c.wins}–${c.losses}${c.pushes ? `–${c.pushes}P` : ''}${c.voids ? ` · ${c.voids} void` : ''}`;

export const groupLabel = (g: { kind: TrackingKind; members: string[] }) => {
  if (g.kind === 'stack_pool') return `Pool stacker (${g.members.length} inputs)`;
  const names = g.members.map(modelName).join(' + ');
  return g.kind === 'single' ? names : `${names} · ${KIND[g.kind]}`;
};

type View = { type: 'threshold'; value: number | null } | { type: 'band'; index: number };
type Sort = 'evidence' | 'win_rate' | 'coverage';

/** Every registered model subset × rule × confidence threshold/band for one market, prospective or historical. */
export function AgreementTracking({ sport, market }: { sport: Sport; market: MarketId }) {
  const [evidence, setEvidence] = useState<TrackingEvidence>('prospective');
  const [kind, setKind] = useState<TrackingKind | ''>('');
  const [member, setMember] = useState('');
  const [line, setLine] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [version, setVersion] = useState('');
  const [view, setView] = useState<View>({ type: 'threshold', value: null });
  const [sort, setSort] = useState<Sort>('evidence');
  const [all, setAll] = useState(false);
  const { state, retry } = useApi((s) => api.researchTracking(sport, market, {
    evidence, kind, member: member || undefined, line: line || undefined, date_from: from || undefined, date_to: to || undefined,
    version: version.trim() || undefined,
  }, s), [sport, market, evidence, kind, member, line, from, to, version]);
  return <div className="card tracking">
    <h2>Agreement tracking <span className="badge badge-research">Research</span></h2>
    <p className="small muted">Every registered model, pair, triple and larger subset with each agreement rule, by confidence
      threshold and band. Research records continue even when no recommendation is made. Sparse groups stay visible as
      “insufficient evidence”.</p>
    <div className="segmented small" role="group" aria-label="Evidence">
      <button type="button" aria-pressed={evidence === 'prospective'} onClick={() => setEvidence('prospective')}>Prospective (recorded live)</button>
      <button type="button" aria-pressed={evidence === 'historical'} onClick={() => setEvidence('historical')}>Historical (exploratory)</button>
    </div>
    <AsyncView state={state} retry={retry}>{(r) => <TrackingBody r={r} sport={sport} market={market} f={{ kind, setKind, member, setMember,
      line, setLine, from, setFrom, to, setTo, version, setVersion, view, setView, sort, setSort, all, setAll }} />}</AsyncView>
  </div>;
}

interface Filters {
  kind: TrackingKind | ''; setKind: (k: TrackingKind | '') => void; member: string; setMember: (m: string) => void;
  line: string; setLine: (l: string) => void; from: string; setFrom: (d: string) => void; to: string; setTo: (d: string) => void;
  version: string; setVersion: (v: string) => void; view: View; setView: (v: View) => void; sort: Sort; setSort: (s: Sort) => void;
  all: boolean; setAll: (a: boolean) => void;
}

function cellOf(g: TrackingGroup, view: View): TrackingCell {
  if (view.type === 'band') return g.bands[view.index] ?? g.all_picks;
  return view.value == null ? g.all_picks : g.thresholds.find((c) => c.threshold === view.value) ?? g.all_picks;
}

function TrackingBody({ r, sport, market, f }: { r: TrackingReport; sport: Sport; market: MarketId; f: Filters }) {
  const members = useMemo(() => Array.from(new Set(r.groups.flatMap((g) => g.members))).sort(), [r.groups]);
  if (r.mode === 'demo') return <Empty>{r.note}</Empty>;
  if (r.status === 'historical_table_outdated') return <Empty><span className="warn">The saved historical table was built under an earlier calculation and is not used. Rebuild it with <code>tracking-historical</code>.</span></Empty>;
  const rows = [...r.groups].map((g) => ({ g, c: cellOf(g, f.view) })).sort((a, b) =>
    f.sort === 'evidence' ? b.c.decided - a.c.decided || (b.c.win_rate ?? -1) - (a.c.win_rate ?? -1)
      : f.sort === 'win_rate' ? (b.c.win_rate ?? -1) - (a.c.win_rate ?? -1) || b.c.decided - a.c.decided
        : (b.c.coverage ?? -1) - (a.c.coverage ?? -1));
  const shown = f.all ? rows : rows.slice(0, 25);
  const viewValue = f.view.type === 'band' ? `b${f.view.index}` : f.view.value == null ? 'all' : `t${f.view.value}`;
  const rcm = r.recommendation;
  return <>
    <div className="filters tracking-filters">
      <label>Rule<select value={f.kind} onChange={(e) => f.setKind(e.target.value as TrackingKind | '')}>
        <option value="">every rule</option>
        {(Object.keys(KIND) as TrackingKind[]).map((k) => <option key={k} value={k}>{KIND[k]}</option>)}
      </select></label>
      <label>Includes model<select value={f.member} onChange={(e) => f.setMember(e.target.value)}>
        <option value="">any model</option>
        {members.map((m) => <option key={m} value={m}>{modelName(m)}</option>)}
      </select></label>
      <label>Confidence<select value={viewValue} onChange={(e) => {
        const v = e.target.value;
        f.setView(v === 'all' ? { type: 'threshold', value: null } : v.startsWith('t') ? { type: 'threshold', value: Number(v.slice(1)) }
          : { type: 'band', index: Number(v.slice(1)) });
      }}>
        <option value="all">every pick</option>
        <optgroup label="At least (cumulative)">{(r.thresholds ?? []).map((t) => <option key={t} value={`t${t}`}>≥ {pct(t, 0)}{r.sealed_thresholds && !r.sealed_thresholds.includes(t) ? ' (descriptive)' : ''}</option>)}</optgroup>
        <optgroup label="Band (non-overlapping)">{(r.bands ?? []).map((b, i) => <option key={b.label} value={`b${i}`}>{b.label}</option>)}</optgroup>
      </select></label>
      {market !== 'winner' && market !== 'reg3' && <label>Exact line<select value={f.line} onChange={(e) => f.setLine(e.target.value)}>
        <option value="">every line</option>
        {(r.lines ?? []).map((l) => <option key={l} value={String(l)}>{l}</option>)}
      </select></label>}
      <label>From<input type="date" value={f.from} onChange={(e) => f.setFrom(e.target.value)} /></label>
      <label>To<input type="date" value={f.to} onChange={(e) => f.setTo(e.target.value)} /></label>
      {r.evidence === 'prospective' && <label>Version<input type="text" value={f.version} placeholder="definition or member signature"
        onChange={(e) => f.setVersion(e.target.value)} /></label>}
      <label>Sort<select value={f.sort} onChange={(e) => f.setSort(e.target.value as Sort)}>
        <option value="evidence">most evidence</option><option value="win_rate">win rate</option><option value="coverage">coverage</option>
      </select></label>
    </div>
    <p className="small"><strong>{r.evidence === 'historical' ? 'Historical, exploratory' : 'Prospective'}</strong>: {r.games ?? 0} games ·
      {' '}{r.registered_groups} registered groups, {r.configurations_searched} rule configurations ({r.skipped_allocation} more held back by the
      allocation cap) · anchor {r.anchor && modelName(r.anchor)} · registry <code>{r.registry_sha256?.slice(0, 10)}</code>
      {r.evidence === 'prospective' && r.effective_from && <> · recording since {dateTime(r.effective_from)}</>}</p>
    <p className={r.evidence === 'historical' ? 'small warn' : 'small muted'}>{r.note}</p>
    <div className="tracking-reco small">
      <strong>Active recommendation:</strong>{' '}
      {rcm?.core?.rule || rcm?.pool?.rule
        ? <span className="ok">{modelName((rcm.pool?.rule ?? rcm.core.rule)!.source)} — chosen by the weekly selector on earlier evidence</span>
        : <span className="muted">none for this market ({rcm?.status ?? 'no decision'}). The leaderboard below is exploratory.</span>}
    </div>
    <h3>Exploratory leaderboard</h3>
    <p className="small muted">{r.leaderboard_note}</p>
    {r.leaderboard.length === 0
      ? <Empty>No configuration has {r.min_evidence ?? 100}+ decided picks and 5%+ coverage yet: insufficient evidence everywhere.</Empty>
      : <div className="table-wrap"><table className="table small">
        <thead><tr><th>Group</th><th>At least</th><th>Picks (coverage)</th><th>Win rate (95%)</th><th>Anchor, same count</th><th>Difference</th><th>Adjusted lower bound</th></tr></thead>
        <tbody>{r.leaderboard.slice(0, 10).map((l) => <tr key={`${l.id}|${l.threshold}`}>
          <th scope="row" className="wrap">{groupLabel(l)}</th><td>{l.threshold == null ? 'every pick' : pct(l.threshold, 0)}</td>
          <td>{l.decided} ({pct(l.coverage, 0)})</td><td>{pct(l.win_rate, 1)} <span className="muted">{ci(l.ci95)}</span></td>
          <td>{pct(l.anchor_win_rate, 1)} <span className="muted">({modelName(l.anchor)})</span></td>
          <td>{(l.lift * 100).toFixed(1)} pts</td><td className={l.lower_adjusted > 0 && r.evidence === 'prospective' ? 'ok' : 'muted'}>{(l.lower_adjusted * 100).toFixed(1)} pts</td></tr>)}</tbody>
      </table></div>}
    <h3>All groups</h3>
    {r.groups.length === 0 ? <Empty>No group matches these filters.</Empty> : <div className="table-wrap"><table className="table small tracking-table">
      <thead><tr><th>Group</th><th>Eligible</th><th>Selected (coverage)</th><th>Pending</th><th>Record</th><th>Win rate (95%)</th>
        <th>Mean confidence</th><th>Brier / log loss</th><th>Anchor, same count{f.view.type === 'band' ? ' (thresholds only)' : ''}</th><th>Evidence</th></tr></thead>
      <tbody>{shown.map(({ g, c }) => <tr key={g.id}>
        <th scope="row" className="wrap">{groupLabel(g)}<div className="muted">{g.cohort === 'expanded' ? 'expanded cohort' : 'core cohort'} · {g.priority}</div>
          {Object.keys(g.missing_reasons).length > 0 && <div className="warn">incomplete {g.status_counts.incomplete ?? 0}×: {Object.keys(g.missing_reasons).map(modelName).join(', ')}</div>}
        </th>
        <td>{g.eligible}<div className="muted">{Object.entries(g.status_counts).filter(([k]) => k !== 'pick').map(([k, n]) => `${n} ${STATUS[k] ?? k}`).join(' · ')}</div></td>
        <td>{c.selected} ({pct(c.coverage, 0)})</td><td>{c.pending}</td><td>{rec(c)}</td>
        <td>{pct(c.win_rate, 1)} <span className="muted">{ci(c.ci95)}</span></td><td>{pct(c.mean_confidence, 1)}</td>
        <td>{c.brier == null ? <span className="muted">n/a</span> : `${c.brier.toFixed(3)} / ${c.log_loss?.toFixed(3)}`}</td>
        <td>{c.matched?.[0] ? `${pct(c.matched[0].win_rate, 1)} (${c.matched[0].games})` : '—'}</td>
        <td className={c.evidence === 'sufficient' ? 'ok' : 'muted'}>{c.evidence}</td></tr>)}</tbody>
    </table></div>}
    {rows.length > 25 && <button type="button" className="link-button" onClick={() => f.setAll(!f.all)}>
      {f.all ? 'Show the first 25' : `Show all ${rows.length} groups`}</button>}
    <details className="small"><summary>How confidence is defined per rule</summary>
      <ul>{Object.entries(r.kinds ?? {}).map(([k, v]) => <li key={k}><strong>{KIND[k as TrackingKind] ?? k}</strong>: {v}</li>)}</ul>
      <p className="muted">Brier and log loss are shown only for rules that output a probability (single, average, stackers); the
        minimum of agreeing members and the majority confidence are thresholds, not forecasts.</p>
    </details>
    {r.evidence === 'historical' && <ChronologicalCheck sport={sport} market={market} />}
  </>;
}

function ChronologicalCheck({ sport, market }: { sport: Sport; market: MarketId }) {
  const [open, setOpen] = useState(false);
  return <details className="small" onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
    <summary>Chosen on earlier seasons, checked on the next (exploratory)</summary>
    {open && <CheckBody sport={sport} market={market} />}
  </details>;
}

function CheckBody({ sport, market }: { sport: Sport; market: MarketId }) {
  const { state, retry } = useApi((s) => api.researchTrackingCheck(sport, market, s), [sport, market]);
  return <AsyncView state={state} retry={retry}>{(c: TrackingCheck) => c.folds.length === 0 ? <Empty>{c.status === 'historical_table_outdated'
    ? <span className="warn">The saved historical table was built under an earlier calculation and is not used. Rebuild it with <code>tracking-historical</code>.</span>
    : 'No historical table.'}</Empty> : <>
    <div className="table-wrap"><table className="table small">
      <thead><tr><th>Season</th><th>First on earlier seasons</th><th>Earlier</th><th>Next season</th><th>Anchor, same count</th></tr></thead>
      <tbody>{c.folds.map((x) => <tr key={x.season}><th scope="row">{x.season}</th>
        {x.chosen && x.earlier && x.later ? <><td className="wrap">{x.chosen} · ≥ {pct(x.threshold ?? null, 0)}</td>
          <td>{pct(x.earlier.win_rate, 1)} on {x.earlier.decided}</td>
          <td>{pct(x.later.win_rate, 1)} on {x.later.decided} <span className="muted">{ci(x.later.ci95)}</span></td>
          <td>{x.later.matched[0] ? pct(x.later.matched[0].win_rate, 1) : '—'}</td></>
          : <td colSpan={4} className="muted">{x.reason}</td>}</tr>)}</tbody>
    </table></div><p className="muted">{c.note}</p></>}</AsyncView>;
}

/** Game page: how the registered groups classified this game from forecasts stored before the start. */
export function TrackingGameCard({ sport, gameId }: { sport: Sport; gameId: string }) {
  const { state, retry } = useApi((s) => api.researchTrackingGame(sport, gameId, s), [sport, gameId]);
  return <div className="card">
    <h2>Agreement tracking <span className="badge badge-research">Research</span></h2>
    <AsyncView state={state} retry={retry}>{(t: TrackingGame) => t.mode === 'demo' || t.markets.length === 0
      ? <Empty>{t.mode === 'demo' ? 'Agreement tracking reads the real stores; none in demo mode.'
        : 'No tracking observations for this game (they are recorded during the two hours before the start).'}</Empty>
      : <>{t.markets.map((m) => <details key={m.market} className="tracking-game">
        <summary><strong>{m.market}</strong>{m.line != null && ` ${m.line}`} — {m.groups} groups:{' '}
          {Object.entries(m.counts).map(([k, n]) => `${n} ${sideName(m.market, k) ?? STATUS[k] ?? k}`).join(' · ')}</summary>
        <p className="small muted">Recorded {dateTime(m.recorded_at)} · definition {m.definition.slice(0, 16)}</p>
        {m.picks.length > 0 && <details className="small"><summary>Classifications by group (research; not picks, regardless of evidence)</summary>
          <div className="table-wrap"><table className="table small">
          <thead><tr><th>Group (highest rule statistic first)</th><th>Side</th><th>Rule statistic</th></tr></thead>
          <tbody>{m.picks.map((p) => <tr key={p.group}><th scope="row" className="wrap">{p.group}</th><td>{sideName(m.market, p.side) ?? SIDE[p.side] ?? p.side}</td>
            <td>{pct(p.statistic, 1)}{p.votes && <span className="muted"> ({p.votes.a ?? 0}–{p.votes.b ?? 0}{p.votes.mid ? `–${p.votes.mid}` : ''} votes)</span>}</td></tr>)}</tbody>
        </table></div></details>}
        {m.incomplete.length > 0 && <p className="small warn">Incomplete: {m.incomplete.map((x) => `${x.group} (${Object.keys(x.missing).join(', ')})`).join('; ')}</p>}
      </details>)}<p className="small muted">{t.note}</p></>}</AsyncView>
  </div>;
}
