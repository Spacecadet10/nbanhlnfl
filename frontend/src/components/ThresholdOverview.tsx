import { api } from '../api/client';
import type { CalibrationState, Sport, ThresholdHistoryPeriod, ThresholdHistoryRow, ThresholdLiveRow, ThresholdMarketBlock } from '../api/types';
import { useApi } from '../api/useApi';
import { pct } from '../format';
import { AsyncView } from './States';

const near = (a: number | null | undefined, b: number | null | undefined) => a != null && b != null && Math.abs(a - b) < 1e-9;
const season = (end: number) => `${end - 1}–${String(end).slice(2)}`;
const seasons = (p: ThresholdHistoryPeriod | null) =>
  !p || p.seasons.length === 0 ? '—' : p.seasons.length === 1 ? season(p.seasons[0]) : `${season(p.seasons[0])} to ${season(p.seasons[p.seasons.length - 1])}`;
const ci = (c: [number, number] | null) => (c ? `${pct(c[0])}–${pct(c[1])}` : '');

const CALIBRATION: Record<CalibrationState, { text: string; cls: string }> = {
  holds: { text: 'wins as often as stated', cls: 'muted' },
  overconfident: { text: 'wins less than stated', cls: 'warn' },
  underconfident: { text: 'wins more than stated', cls: 'ok' },
  too_few: { text: 'too few to judge', cls: 'muted' },
};

function Rate({ r }: { r: { graded: number; win_rate: number | null; ci95: [number, number] | null } }) {
  if (r.graded === 0) return <span className="muted">—</span>;
  return <>{pct(r.win_rate, 1)} <span className="small muted">({ci(r.ci95)})</span></>;
}

function Calib({ state }: { state: CalibrationState }) {
  const c = CALIBRATION[state];
  return <span className={`small ${c.cls}`}>{c.text}</span>;
}

/** One sentence per market: the declared best threshold and whether it held, or why none is chosen. */
function Verdict({ m }: { m: ThresholdMarketBlock }) {
  const v = m.verdict;
  if (v.best == null) {
    return <span className="muted">No threshold chosen: {m.history.status === 'not_comparable' ? 'no comparable history' : v.reason}</span>;
  }
  const later = m.history.later?.rows.find((r) => near(r.threshold, v.best));
  return <>
    <strong>{pct(v.best)}+</strong>{' '}
    {v.better_than_all ? 'beats all picks' : 'no better than all picks'} in {seasons(m.history.earlier)}
    {'; '}
    {v.held_up == null ? <span className="muted">too few later picks to check</span>
      : v.held_up ? <span className="ok">held up in {seasons(m.history.later)}</span>
      : <span className="warn">did not hold up in {seasons(m.history.later)}</span>}
    {later && later.graded > 0 && <span className="small muted"> ({pct(later.win_rate, 1)} on {later.graded})</span>}
  </>;
}

function LiveCell({ r, lineMarket, breakEven }: { r: ThresholdLiveRow | undefined; lineMarket: boolean; breakEven: number }) {
  if (!r || r.graded === 0) return <span className="muted">none settled</span>;
  return <>
    {r.win}–{r.loss}{r.push > 0 ? `–${r.push}` : ''} · <Rate r={r} />
    <br /><span className="small muted">stated {pct(r.stated, 1)}</span> · <Calib state={r.calibration} />
    {r.market_matched > 0 && r.market_implied != null && <>
      <br /><span className="small muted">market gave the same picks {pct(r.market_implied, 1)}{r.market_matched < r.graded ? ` (${r.market_matched} with a quote)` : ''}</span>
    </>}
    {lineMarket && r.break_even === 'below' && <><br /><span className="small warn">below the {pct(breakEven, 1)} break-even</span></>}
    {lineMarket && r.break_even === 'above' && <><br /><span className="small ok">above the {pct(breakEven, 1)} break-even</span></>}
  </>;
}

function HistCell({ r }: { r: ThresholdHistoryRow | undefined }) {
  if (!r || r.graded === 0) return <span className="muted">—</span>;
  return <>{r.graded} picks · <Rate r={r} /><br /><span className="small muted">stated {pct(r.stated, 1)}</span> · <Calib state={r.calibration} /></>;
}

function MarketLadder({ m, breakEven }: { m: ThresholdMarketBlock; breakEven: number }) {
  const lineMarket = m.market !== 'winner' && m.market !== 'reg3';
  const hist = m.history.status === 'available';
  const rows = m.grid.filter((t) => {   // hide the empty top of the grid: no live and no historical pick there
    const l = m.live.find((r) => near(r.threshold, t));
    const e = m.history.earlier?.rows.find((r) => near(r.threshold, t));
    const la = m.history.later?.rows.find((r) => near(r.threshold, t));
    return (l?.forecasts ?? 0) > 0 || (e?.graded ?? 0) > 0 || (la?.graded ?? 0) > 0;
  });
  const hidden = m.grid.length - rows.length;
  return (
    <details className="threshold-market">
      <summary><strong>{m.title}</strong> — every threshold</summary>
      <div className="table-wrap"><table className="table">
        <thead><tr>
          <th>Minimum</th>
          <th>Live (this season, canonical)</th>
          {hist && <th>History {seasons(m.history.earlier)}<br /><span className="small muted">chooses the best</span></th>}
          {hist && <th>History {seasons(m.history.later)}<br /><span className="small muted">checks it</span></th>}
        </tr></thead>
        <tbody>{rows.map((t) => {
          const best = near(t, m.verdict.best);
          return <tr key={t} className={best ? 'row-best' : undefined}>
            <th scope="row">{pct(t)}+{best && <> <span className="badge badge-best">best</span></>}</th>
            <td><LiveCell r={m.live.find((r) => near(r.threshold, t))} lineMarket={lineMarket} breakEven={breakEven} /></td>
            {hist && <td><HistCell r={m.history.earlier?.rows.find((r) => near(r.threshold, t))} /></td>}
            {hist && <td><HistCell r={m.history.later?.rows.find((r) => near(r.threshold, t))} /></td>}
          </tr>;
        })}</tbody>
      </table></div>
      <p className="small muted">
        Each row counts picks whose model probability is at least that level (cumulative). Intervals are Wilson 95%.
        {hidden > 0 && ` ${hidden} higher grid level${hidden > 1 ? 's' : ''} with no picks hidden.`}
        {hist && m.history.model && ` History: ${m.history.model}, saved walk-forward forecasts made the night before (live forecasts are made within two hours of the start).`}
        {!hist && m.history.reason && ` ${m.history.reason}`}
        {lineMarket && ` Break-even ${pct(breakEven, 1)} assumes standard −110 prices on both sides.`}
      </p>
    </details>
  );
}

/** Every market of the sport on one card: which confidence threshold is best by the declared rule, whether it held on
 * later seasons, how each threshold does live, and whether the model's stated probability is kept. Read-only. */
export function ThresholdOverviewCard({ sport }: { sport: Sport }) {
  const { state, retry } = useApi((s) => api.canonicalThresholds(sport, s), [sport]);
  return (
    <div className="card" id="thresholds-by-market">
      <h2>Confidence thresholds by market</h2>
      <AsyncView state={state} retry={retry}>{(d) => <>
        <p className="small muted">
          Every {sport.toUpperCase()} market has its own threshold grid. A threshold only filters which picks you look at; it never changes a forecast.
          "Best" is chosen on earlier seasons and checked on later ones; this season's games never choose. A higher win rate is not a better bet:
          favourites are priced like favourites, so compare the live win rate with what the market gave the same picks.
        </p>
        <div className="table-wrap"><table className="table">
          <thead><tr><th>Market</th><th>Best threshold (history)</th><th>Live, all picks</th><th>Live at the best threshold</th></tr></thead>
          <tbody>{d.markets.map((m) => {
            const all = m.live[0];
            const atBest = m.verdict.best == null ? undefined : m.live.find((r) => near(r.threshold, m.verdict.best));
            const lineMarket = m.market !== 'winner' && m.market !== 'reg3';
            return <tr key={m.market}>
              <th scope="row">{m.title}{!m.skill_proven && <><br /><span className="no-skill small">No proven skill against the line</span></>}</th>
              <td><Verdict m={m} /></td>
              <td><LiveCell r={all} lineMarket={lineMarket} breakEven={d.break_even_standard} /></td>
              <td>{m.verdict.best == null ? <span className="muted">—</span>
                : <LiveCell r={atBest} lineMarket={lineMarket} breakEven={d.break_even_standard} />}</td>
            </tr>;
          })}</tbody>
        </table></div>
        {d.markets.map((m) => <MarketLadder key={m.market} m={m} breakEven={d.break_even_standard} />)}
        <p className="small muted">Rule: {d.rule}</p>
      </>}</AsyncView>
    </div>
  );
}
