import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { bandNote, gridNote, onStandardGrid, standardRows } from './ConfidenceFilter';
import type { MarketId, Sport, SubsetSearch, ThRecord, ThRule, ThSourceInfo, ThresholdExplorer as Explorer } from '../api/types';
import { RuleLedgerTable } from './RuleLedger';
import { useApi } from '../api/useApi';
import { count, pct } from '../format';
import { CoverageTable, modelName, NotAvailable } from './Research';
import { AsyncView, Empty } from './States';

/** Thresholds, agreement x confidence, adding a model, and exact model subsets on the saved out-of-sample forecasts
 * (GET /api/research/thresholds, /api/research/subsets). Exploratory research; loads only when opened. */
export function ThresholdExplorerCard({ sport, market }: { sport: Sport; market: MarketId }) {
  const [open, setOpen] = useState(false);
  return <details className="card" id="thresholds" onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
    <summary><strong>Confidence thresholds &amp; model subsets · saved forecasts</strong> <span className="badge badge-backtest">Backtest</span> <span className="muted small">exploratory</span></summary>
    {open && <ExplorerLoader sport={sport} market={market} />}
  </details>;
}

type Period = 'earlier' | 'later';
type View = 'thresholds' | 'agreement' | 'add' | 'subsets' | 'rule';
const VIEWS: [View, string][] = [['thresholds', 'Thresholds'], ['agreement', 'Agreement × confidence'], ['add', 'Add a model'],
  ['subsets', 'Exact subsets'], ['rule', 'Check a rule']];

function ExplorerLoader({ sport, market }: { sport: Sport; market: MarketId }) {
  const [source, setSource] = useState('pool');
  const [group, setGroup] = useState<string[]>([]);
  const [base, setBase] = useState<string[]>([]);
  const [rule, setRule] = useState('');
  const [origin, setOrigin] = useState<'frozen' | 'manual'>('manual');   // a rule opened from a frozen list, or chosen by hand
  const [view, setView] = useState<View>('thresholds');
  const [period, setPeriod] = useState<Period>('later');
  useEffect(() => { setGroup([]); setBase([]); setRule(''); }, [sport, market, source]);
  const { state, retry } = useApi((s) => api.researchThresholds(sport, market, { source, group, base, rule }, s),
    [sport, market, source, group.join(','), base.join(','), rule]);
  // The source picker lives outside the loading/empty/error states, so an empty or failed source can always be left.
  const [known, setKnown] = useState<ThSourceInfo[]>([]);
  useEffect(() => { if (state.status === 'success' && state.data.sources?.length) setKnown(state.data.sources); }, [state]);
  useEffect(() => { setKnown([]); setSource('pool'); }, [sport]);   // saved sources are per sport
  const options = known.some((s) => s.key === source) ? known : [...known, { key: source, label: source, files: [] }];
  return <>
    <div className="explorer-controls small">
      <label>Saved forecast source <select value={source} onChange={(e) => setSource(e.target.value)}>
        {options.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}</select></label>
      {source !== 'pool' && <button type="button" className="link-btn" onClick={() => setSource('pool')}>Back to the candidate pool</button>}
    </div>
    <div className="explorer-controls small">
      <div className="segmented" role="group" aria-label="View">
        {VIEWS.map(([v, label]) => <button key={v} type="button" aria-pressed={view === v} onClick={() => setView(v)}>{label}</button>)}
      </div>
      <div className="segmented" role="group" aria-label="Period">
        {(['earlier', 'later'] as Period[]).map((p) => <button key={p} type="button" aria-pressed={period === p} onClick={() => setPeriod(p)}>
          {p === 'earlier' ? 'Earlier (search)' : 'Later (frozen check)'}</button>)}
      </div>
    </div>
    <AsyncView state={state} retry={retry}>{(r) => r.status !== 'available'
      ? <NotAvailable status={r.status} reason={r.reason ?? r.note} excluded={r.excluded} split={r.split}
          fallback={r.status === 'no_stored_forecasts' ? 'no saved out-of-sample forecasts for this market and source' : 'not enough saved forecasts for this market and source'} />
      : <>
        <Facts r={r} source={source} />
        {view === 'thresholds' && <ThresholdsView r={r} period={period} onGroup={setGroup} />}
        {view === 'agreement' && <AgreementView r={r} period={period} onGroup={setGroup} />}
        {view === 'add' && <AddView r={r} period={period} onBase={setBase} />}
        {view === 'subsets' && <SubsetsView sport={sport} market={market} source={source}
          onCheck={(x) => { setRule(x.members.map((m, i) => `${m}@${x.thresholds[i]}`).join(',')); setOrigin('frozen'); setView('rule'); }} />}
        {view === 'rule' && <RuleView r={r} period={period} origin={origin} sport={sport} market={market} source={source}
          onRule={(x) => { setRule(x); setOrigin('manual'); }} />}
      </>}
    </AsyncView>
  </>;
}

// --- Formatting -----------------------------------------------------------------------------------------------------
const p1 = (x: number | null | undefined) => pct(x, 1);
const ci = (c: [number, number] | null) => (c ? `${pct(c[0], 1)}–${pct(c[1], 1)}` : '—');
const gap = (g: number | null) => (g == null ? '—' : `${g >= 0 ? '+' : ''}${(g * 100).toFixed(1)} pts`);
const wlp = (r: ThRecord) => `${r.wins}–${r.losses}${r.pushes ? `–${r.pushes}P` : ''}`;
const thr = (x: number) => (Math.abs(x - 1 / 3) < 1e-9 ? 'any pick' : `${Math.round(x * 100)}%+`);
/** A member's threshold inside a rule: "Elo ≥ 80%" ("Elo: any pick" at the 3-way floor). */
const at = (m: string, x: number) => (Math.abs(x - 1 / 3) < 1e-9 ? `${modelName(m)}: any pick` : `${modelName(m)} ≥ ${Math.round(x * 100)}%`);
const names = (ms: string[]) => ms.map(modelName).join(' + ');
const small = (r: ThRecord, min = 30) => r.wins + r.losses > 0 && r.wins + r.losses < min;

function RecordCells({ r }: { r: ThRecord }) {
  return <>
    <td>{r.picks} <span className="muted">of {r.eligible} ({p1(r.coverage)})</span></td>
    <td>{wlp(r)}{r.pending ? <span className="muted"> · {r.pending} pending</span> : null}</td>
    <td>{p1(r.win_rate)} <span className="muted">[{ci(r.win_rate_ci95)}]</span>{small(r) && <span className="warn"> small sample</span>}</td>
    <td>{p1(r.mean_p)}</td><td>{gap(r.calibration_gap)}</td>
  </>;
}
const RECORD_HEAD = <><th>Picks of eligible (coverage)</th><th>W–L–P</th><th>Win rate [95%]</th><th>Mean probability</th><th title="Win rate minus mean probability: positive = the picks won more often than predicted">Calibration gap</th></>;
const short = (r: ThRecord) => `${wlp(r)} · ${p1(r.win_rate)} [${ci(r.win_rate_ci95)}] · ${r.picks} picks (${p1(r.coverage)})`;

function Facts({ r, source }: { r: Explorer; source: string }) {
  const per = r.periods!;
  return <>
    <p className="notice small" role="note">{r.note}</p>
    <dl className="facts small">
      <dt>Source</dt><dd>{(r.sources ?? []).find((s) => s.key === source)?.label ?? source}
        <span className="muted"> {(r.sources ?? []).find((s) => s.key === source)?.files.map((f) => `${f.file} ${f.sha256}`).join(', ')}</span></dd>
      <dt>Evidence</dt><dd>{r.kind}{r.source_detail?.exported_at ? ` · exported ${r.source_detail.exported_at}` : ''}</dd>
      <dt>Earlier period</dt><dd>{per.earlier.date_from} to {per.earlier.date_to} (seasons {per.earlier.seasons.join(', ')}; {per.earlier.games} games) — rules are searched here</dd>
      <dt>Later period</dt><dd>{per.later.date_from} to {per.later.date_to} (seasons {per.later.seasons.join(', ')}; {per.later.games} games) — frozen rules, no retuning
        {r.later_reserved_test_seasons?.length ? `; includes the spent reserved-test seasons ${r.later_reserved_test_seasons.join(', ')} (already explored)` : ''}</dd>
      <dt>Models with saved history</dt><dd>{r.models.map(modelName).join(', ')}</dd>
      {(r.not_stored ?? []).length > 0 && <><dt>No saved history</dt><dd>{r.not_stored!.map((n) => modelName(n.model)).join(', ')} — {r.not_stored![0].reason}; not analysed here</dd></>}
      {(r.excluded ?? []).length > 0 && <><dt>Excluded</dt><dd>{r.excluded!.map((e) => `${modelName(e.model)} (${e.reason})`).join('; ')}</dd></>}
      <dt>Win rate</dt><dd>{r.win_rate_denominator}; pending (unsettled) games: {r.pending_games ?? 0}</dd>
      <dt>Probability</dt><dd>{r.thresholds?.probability}</dd>
    </dl>
    <CoverageTable r={r} scope={`Applies to all ${r.models.length} compared models together. It is not any one rule's sample: a threshold, average or rule needs only its own members, so each row below counts its own games ("of eligible" = games where all of its members have a saved forecast, pending included; the win rate uses its settled picks only).`} />
  </>;
}

function GroupPicker({ models, value, label, min, onApply }: { models: string[]; value: string[]; label: string; min: number;
  onApply: (g: string[]) => void }) {
  const [chosen, setChosen] = useState<string[]>(value);
  useEffect(() => setChosen(value), [value.join(',')]);   // eslint-disable-line react-hooks/exhaustive-deps
  const valid = chosen.length >= min;
  return <fieldset className="base-picker small">
    <legend>{label}</legend>
    {models.map((m) => <label key={m}><input type="checkbox" checked={chosen.includes(m)}
      onChange={(e) => setChosen(e.target.checked ? [...chosen, m] : chosen.filter((x) => x !== m))} /> {modelName(m)}</label>)}
    <button type="button" disabled={!valid} onClick={() => onApply(chosen)}>Apply</button>
    {!valid && <span className="muted"> choose at least {min}</span>}
  </fieldset>;
}

// --- 1. Thresholds ----------------------------------------------------------------------------------------------------
function ThresholdsView({ r, period, onGroup }: { r: Explorer; period: Period; onGroup: (g: string[]) => void }) {
  const sources = r.thresholds!.sources;
  const [key, setKey] = useState(sources.find((s) => s.kind === 'average')?.key ?? sources[0].key);
  const [bands, setBands] = useState(false);
  const src = sources.find((s) => s.key === key) ?? sources[0];
  const all = bands ? src[period].bands : src[period].cumulative;
  const rows = standardRows(all, r.market, bands);
  return <>
    <GroupPicker models={r.models} value={r.group ?? []} label="Group (its average is a row below; also the agreement group)" min={1} onApply={onGroup} />
    <div className="explorer-controls small">
      <label>Model or combination <select value={src.key} onChange={(e) => setKey(e.target.value)}>
        {sources.map((s) => <option key={s.key} value={s.key}>{s.kind === 'average' ? `Equal average of ${s.members.length} models` : modelName(s.key)}</option>)}
      </select></label>
      <div className="segmented" role="group" aria-label="Threshold kind">
        <button type="button" aria-pressed={!bands} onClick={() => setBands(false)}>Cumulative (65%+)</button>
        <button type="button" aria-pressed={bands} onClick={() => setBands(true)}>Bands (65–70%)</button>
      </div>
    </div>
    {src.kind === 'average' && <p className="small muted">Thresholds on the averaged probability of {names(src.members)} — not a per-model rule.</p>}
    <p className="small muted">{gridNote(all.map((x) => x.lo), r.market, all.length - rows.length)}{bands && bandNote(all, r.market)}</p>
    <div className="table-wrap"><table className="table small">
      <thead><tr><th>Threshold</th>{RECORD_HEAD}<th>Picks dated</th></tr></thead>
      <tbody>{rows.map((x) => <tr key={x.rule}><th scope="row">{x.rule}</th><RecordCells r={x} /><td className="muted">{x.first ? `${x.first} – ${x.last}` : '—'}</td></tr>)}</tbody>
    </table></div>
  </>;
}

// --- 2. Agreement x confidence ------------------------------------------------------------------------------------------
function AgreementView({ r, period, onGroup }: { r: Explorer; period: Period; onGroup: (g: string[]) => void }) {
  const standard = r.thresholds!.levels.filter((l) => onStandardGrid(l, r.market));
  const levels = standard.length ? standard : r.thresholds!.levels;      // no standard level computed: show what exists
  const [level, setLevel] = useState(levels[0]);
  const a = r.agreement;
  return <>
    <GroupPicker models={r.models} value={r.group ?? []} label="Agreement group" min={2} onApply={onGroup} />
    {!a ? <Empty>Choose at least two models.</Empty> : <>
      <p className="small muted">{a.rule}. Games where a group model has no saved forecast are excluded ({a.excluded_games_missing_member}), never counted as agreement.
        &ldquo;8 of 10 agree&rdquo; is a vote count; &ldquo;average 80%+&rdquo; is the combined probability — they are separate columns.</p>
      <label className="small">Average's probability <select value={level} onChange={(e) => setLevel(Number(e.target.value))}>
        {levels.map((l) => <option key={l} value={l}>{thr(l)}</option>)}</select></label>
      <h3>Average {thr(level)} × share of the group agreeing</h3>
      <div className="table-wrap"><table className="table small">
        <thead><tr><th>Rule</th>{RECORD_HEAD}</tr></thead>
        <tbody>{a.cross[period].filter((x) => x.level === level).map((x) => <tr key={x.rule}><th scope="row">{x.rule}</th><RecordCells r={x} /></tr>)}</tbody>
      </table></div>
      <h3>Exact vote count (average {thr(level)})</h3>
      <div className="table-wrap"><table className="table small">
        <thead><tr><th>Agreeing</th><th>Kind</th>{RECORD_HEAD}</tr></thead>
        <tbody>{a.by_count[period].filter((x) => x.level === level && x.picks > 0).map((x) => <tr key={x.agreeing}>
          <th scope="row">{x.agreeing} of {x.of}</th><td>{x.kind}</td><RecordCells r={x} /></tr>)}</tbody>
      </table></div>
    </>}
  </>;
}

// --- 3. Add a model ---------------------------------------------------------------------------------------------------
function AddView({ r, period, onBase }: { r: Explorer; period: Period; onBase: (b: string[]) => void }) {
  const add = r.add_model!;
  const [added, setAdded] = useState(add.rows[0]?.added ?? '');
  const row = add.rows.find((x) => x.added === added) ?? add.rows[0];
  return <>
    <GroupPicker models={r.models} value={add.base} label="Starting group" min={1} onApply={onBase} />
    {!row ? <Empty>Nothing left to add.</Empty> : <>
      <label className="small">Add <select value={row.added} onChange={(e) => setAdded(e.target.value)}>
        {add.rows.map((x) => <option key={x.added} value={x.added}>{modelName(x.added)}</option>)}</select></label>
      <p className="small muted">Equal average of {names(add.base)} against the same plus {modelName(row.added)}, on identical games, at each
        threshold of the averaged probability. Retained = both pick the same side; new = only the larger group qualifies; dropped = only the
        starting group qualifies. A higher win rate from dropping hard games is fewer picks, not better predictions.</p>
      <div className="table-wrap"><table className="table small">
        <thead><tr><th>Threshold</th><th>Starting group</th><th>With {modelName(row.added)}</th><th>Win rate change</th><th>Retained</th><th>Switched side</th><th>New picks</th><th>Dropped picks</th></tr></thead>
        <tbody>{row[period].filter((x) => onStandardGrid(x.level, r.market)).map((x) => <tr key={x.rule}><th scope="row">{x.rule}</th><td>{short(x.base)}</td><td>{short(x.with)}</td>
          <td>{x.base.win_rate != null && x.with.win_rate != null ? gap(x.with.win_rate - x.base.win_rate) : '—'}</td>
          <td>{x.retained.picks}</td><td>{x.switched_base.picks ? `${x.switched_base.picks}: was ${wlp(x.switched_base)}, now ${wlp(x.switched_with)}` : 0}</td>
          <td>{x.new.picks ? `${x.new.picks} (${wlp(x.new)})` : 0}</td><td>{x.dropped.picks ? `${x.dropped.picks} (${wlp(x.dropped)})` : 0}</td></tr>)}</tbody>
      </table></div>
    </>}
  </>;
}

// --- 4. Exact subsets ---------------------------------------------------------------------------------------------------
function SubsetsView({ sport, market, source, onCheck }: { sport: Sport; market: MarketId; source: string;
  onCheck: (x: ThRule) => void }) {
  const { state, retry } = useApi((s) => api.researchSubsets(sport, market, source, s), [sport, market, source]);
  return <AsyncView state={state} retry={retry}>{(s) => s.status !== 'available'
    ? <NotAvailable status={s.status} reason={s.reason ?? s.note} excluded={s.excluded} split={s.split}
        fallback="not enough saved models for a subset search" /> : <SubsetTables s={s} onCheck={onCheck} />}</AsyncView>;
}

function SubsetTables({ s, onCheck }: { s: SubsetSearch; onCheck: (x: ThRule) => void }) {
  const [list, setList] = useState<'frozen' | 'frozen_lift'>('frozen');
  const rules = s[list] ?? [];
  const beats = s.later_beats_matched ?? { reliable: 0, lift: 0 };
  const ov = s.later_overlap;
  const sets = (k: 'reliable' | 'lift') => (ov ? ` (${count(ov[k].beats_matched.distinct_later_pick_sets, 'distinct later pick set')})` : '');
  const cur = ov?.[list === 'frozen' ? 'reliable' : 'lift'];
  const src = (s.sources ?? []).find((x) => x.key === s.source);
  const missing = [...(s.not_stored ?? []).map((n) => `${modelName(n.model)} (no saved history)`),
    ...(s.excluded ?? []).map((e) => `${modelName(e.model)} (${e.reason})`)];
  return <>
    <h3>Search limits</h3>
    <dl className="facts small">
      <dt>Archive</dt><dd>{src?.label ?? s.source} {src?.files.map((f) => `${f.file} ${f.sha256}`).join(', ')}</dd>
      <dt>Models searched</dt><dd>{s.models.map(modelName).join(', ')}</dd>
      <dt>Not searched</dt><dd>{missing.join('; ') || 'none'}</dd>
      <dt>Rules</dt><dd>exact subsets of size {(s.sizes ?? []).join(' and ')} ({Object.entries(s.per_size ?? {}).map(([k, v]) =>
        `${v.subsets} ${k === '2' ? 'pairs' : k === '3' ? 'triples' : `size-${k}`}`).join(', ')}); per-member thresholds {(s.grid ?? []).map(thr).join(', ')};
        {' '}{s.rules_searched?.toLocaleString()} rules searched; {s.rules_with_min_picks?.toLocaleString()} had ≥{s.min_decided} decided earlier picks</dd>
      <dt>Selection</dt><dd>on the earlier period only ({s.periods?.earlier.date_from} to {s.periods?.earlier.date_to}); top {s.frozen_top} of each list frozen,
        then scored on {s.periods?.later.date_from} to {s.periods?.later.date_to} without retuning; rules with identical earlier picks collapsed
        ({(s.duplicates_collapsed?.reliable ?? 0) + (s.duplicates_collapsed?.lift ?? 0)})</dd>
      <dt>Best single model</dt><dd>{s.best_single ? modelName(s.best_single) : '—'} (lowest earlier log loss)</dd>
    </dl>
    <p className="small">A game qualifies when every model in the subset picks the same side and each reaches its own threshold. Models outside the subset do not vote.</p>
    <p className="notice small" role="note">{s.note} In this search, the later win rate was above the best single model's same number of most
      confident later picks for <strong>{beats.reliable} of {s.frozen?.length ?? 0}</strong> rules{sets('reliable')} in the first list and
      <strong> {beats.lift} of {s.frozen_lift?.length ?? 0}</strong>{sets('lift')} in the second. These count frozen RULES, not games won, and say nothing about
      models, sizes or thresholds outside this search. Several rules can make exactly the same later picks: they are one later result
      counted more than once, not independent confirmations that a combination is better.</p>
    <div className="segmented small" role="group" aria-label="Ranking">
      <button type="button" aria-pressed={list === 'frozen'} onClick={() => setList('frozen')}>Highest earlier win rate</button>
      <button type="button" aria-pressed={list === 'frozen_lift'} onClick={() => setList('frozen_lift')}>Strongest earlier improvement</button>
    </div>
    <p className="small muted">{list === 'frozen' ? 'Ranked by the 95% lower bound of the earlier win rate (so a high rate on few picks does not top the list).'
      : 'Ranked by the earlier win rate minus the best single model\'s win rate on the same number of its most confident earlier picks.'}</p>
    {cur && <p className="small" role="note"><strong>Later-period overlap (descriptive):</strong> these {count(cur.rules, 'rule')} make {count(cur.distinct_later_pick_sets, 'distinct later pick set')}
      {cur.shared.length ? ` — ${cur.shared.map((g) => `set ${g.set}: rules ${g.rules.join(', ')} (${count(g.picks, 'pick')})`).join('; ')}` : ' — no two share their later picks'}.
      {' '}{s.later_overlap_definition}</p>}
    <p className="small muted">Two separate comparisons with the best single model (never merged): <strong>same games</strong> = its own pick on exactly
      the rule's games (does the rule's choice of games explain the record?); <strong>same number</strong> = its own most confident picks, as many as
      the rule made, usually on other games (does agreement add anything beyond picking confident games?).</p>
    <div className="table-wrap"><table className="table small">
      <thead><tr><th>#</th><th title="Rules with the same number make exactly the same later picks (same games and sides)">Later pick set</th><th>Exact models and per-member thresholds</th><th>Earlier: picks (coverage) · W–L · win rate [95%]</th><th>Later: picks (coverage) · W–L · win rate [95%]</th>
        <th>Later: best single on the SAME games</th><th>Later: best single, same number of its most confident picks (other games possible)</th><th /></tr></thead>
      <tbody>{rules.map((x, i) => {
        const later = x.later.record, a = x.later.same_games_best_single, b = x.later.coverage_matched_best_single;
        const shared = cur?.shared.find((g) => g.set === x.later_pick_set);
        return <tr key={x.rule}><td>{i + 1}</td><td>{x.later_pick_set ?? '—'}{shared && <span className="muted"> (shared by {shared.rules.length})</span>}</td><th scope="row" className="wrap">{x.members.map((mm, i) => at(mm, x.thresholds[i])).join(' & ')}</th>
          <td>{x.earlier.record.picks} ({p1(x.earlier.record.coverage)}) · {wlp(x.earlier.record)} · {p1(x.earlier.record.win_rate)} [{ci(x.earlier.record.win_rate_ci95)}]</td>
          <td>{later.picks} ({p1(later.coverage)}) · {wlp(later)} · {p1(later.win_rate)} [{ci(later.win_rate_ci95)}]{x.later.too_few && <span className="warn"> too few to judge</span>}</td>
          <td>{a ? `${wlp(a)} · ${p1(a.win_rate)}${a.same_side === later.picks ? ' (same side on every game)' : ` (same side on ${a.same_side})`}` : '—'}</td>
          <td>{b ? `${wlp(b)} · ${p1(b.win_rate)} [${ci(b.win_rate_ci95)}]` : '—'}</td>
          <td><button type="button" className="link-btn" onClick={() => onCheck(x)}>Details &amp; games</button></td></tr>;
      })}</tbody>
    </table></div>
    <p className="small muted">Each 95% interval describes one record on its own; overlapping intervals mean the difference is not established.
      Rules sharing a later pick set made the same later picks, so their win–loss–push outcomes and win rate are identical by construction;
      their mean probability and calibration gap can still differ, because those come from each rule's own members.</p>
  </>;
}

// --- 5. One exact rule -------------------------------------------------------------------------------------------------
function RuleView({ r, period, origin, sport, market, source, onRule }: { r: Explorer; period: Period; origin: 'frozen' | 'manual';
  sport: Sport; market: MarketId; source: string; onRule: (rule: string) => void }) {
  const levels = r.thresholds!.levels.filter((l) => l < 0.9 + 1e-9);
  const init = Object.fromEntries((r.rule?.members ?? []).map((m, i) => [m, r.rule!.thresholds[i]]));
  const [ths, setThs] = useState<Record<string, number | ''>>(init);
  useEffect(() => setThs(init), [r.rule?.rule]);   // eslint-disable-line react-hooks/exhaustive-deps
  const chosen = Object.entries(ths).filter(([, v]) => v !== '') as [string, number][];
  const x = r.rule;
  const spec = x ? x.members.map((m, i) => `${m}@${x.thresholds[i]}`).join(',') : '';
  return <>
    <fieldset className="base-picker small">
      <legend>Per-member thresholds (blank = not in the rule)</legend>
      {r.models.map((m) => <label key={m}>{modelName(m)} <select value={ths[m] ?? ''} onChange={(e) => setThs({ ...ths, [m]: e.target.value === '' ? '' : Number(e.target.value) })}>
        <option value="">—</option>{levels.map((l) => <option key={l} value={l}>{thr(l)}</option>)}</select></label>)}
      <button type="button" disabled={!chosen.length} onClick={() => onRule(chosen.map(([m, v]) => `${m}@${v}`).join(','))}>Check rule</button>
    </fieldset>
    {!x ? <Empty>Choose thresholds for one or more models, or pick a rule from Exact subsets.</Empty> : <>
      <p className={origin === 'frozen' ? 'notice small' : 'warn small'} role="note">{origin === 'frozen'
        ? 'Frozen rule: chosen on the earlier period only, then scored on the later period without retuning. Still exploratory (these seasons were used before).'
        : 'Exploratory: a rule chosen by hand, not a frozen validation. If it was chosen after looking at later results, its later record is not evidence.'}</p>
      <RuleDetail x={x} period={period} />
      <RuleLedgerTable sport={sport} market={market} source={source} rule={spec} period={period} />
    </>}
  </>;
}

function RuleDetail({ x, period }: { x: ThRule; period: Period }) {
  const d = x[period];
  const a = d.same_games_best_single, b = d.coverage_matched_best_single;
  return <>
    <h3>{x.members.map((mm, i) => at(mm, x.thresholds[i])).join(' & ')} · {period} period</h3>
    {x.excluded_games_missing_member > 0 && <p className="small warn">{count(x.excluded_games_missing_member, 'game')} excluded: a member had no saved forecast.</p>}
    <div className="table-wrap"><table className="table small">
      <thead><tr><th /><th>Picks of eligible (coverage)</th><th>W–L–P</th><th>Win rate [95%]</th><th>Mean probability</th><th>Calibration gap</th></tr></thead>
      <tbody>
        <tr><th scope="row">This rule</th><RecordCells r={d.record} /></tr>
        {a && <tr><th scope="row">(a) {modelName(a.model)} on the SAME games{a.member ? ' (a member: the same picks and outcomes)' : ` — same side on ${a.same_side} of ${d.record.picks}`}</th><RecordCells r={a} /></tr>}
        {b && <tr><th scope="row">(b) {modelName(b.model)}: its {b.picks_matched} most confident picks (≥ {p1(b.min_probability)}; other games possible)</th><RecordCells r={b} /></tr>}
      </tbody>
    </table></div>
    <p className="small muted">(a) and (b) answer different questions and are not combined: (a) the same games, the single model's own pick; (b) the same
      number of picks, the single model's most confident games. Neither turns this one rule into evidence that combinations are better in general.</p>
    <h3>Constituent models</h3>
    <p className="small muted">On the rule's own games every member picks the same side, so their win–loss records there are identical to the rule's.
      What differs is their probability for that side, and the extra picks each makes on its own at its threshold.</p>
    <div className="table-wrap"><table className="table small">
      <thead><tr><th>Model</th><th>Mean probability on the rule's games</th><th>Its own picks at its threshold</th><th>Its extra picks (not in the rule)</th></tr></thead>
      <tbody>{d.alone.map((m) => <tr key={m.model}><th scope="row">{at(m.model, m.threshold)}</th><td>{p1(d.member_mean_p[m.model])}</td>
        <td>{short(m.own)}</td><td>{short(m.excluded)}</td></tr>)}</tbody>
    </table></div>
    <h3>By season ({period} period)</h3>
    <div className="table-wrap"><table className="table small">
      <thead><tr><th>Season</th>{RECORD_HEAD}</tr></thead>
      <tbody>{d.by_season.map((s) => <tr key={s.season}><th scope="row">{s.season}</th><RecordCells r={s} /></tr>)}</tbody>
    </table></div>
    {d.too_few && <p className="small warn">Too few decided picks to judge.</p>}
  </>;
}
