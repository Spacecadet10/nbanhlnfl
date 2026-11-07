import { api } from '../api/client';
import type { CalibrationState, ForecastPercentageHistory, Sport, ThresholdHistoryPeriod, ThresholdHistoryRow, ThresholdLiveRow, ThresholdMarketBlock } from '../api/types';
import { useApi } from '../api/useApi';
import { dateTime, pct } from '../format';
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

/** Exact-percentage observations and an explicitly experimental opposite scenario; original forecasts stay unchanged. */
export function ForecastPercentageRecord({ history: h, grade, opposite, startAt }: {
  history: ForecastPercentageHistory | null | undefined; grade: string | null; opposite?: string | null; startAt?: string;
}) {
  if (!h) return <span className="small warn">Percentage history unavailable</span>;
  // The API supplies OTHER matches. A settled card also shows its own separately supplied canonical result in the total.
  const ownWin = grade === 'win' ? 1 : 0, ownLoss = grade === 'loss' ? 1 : 0;
  const includesThisGame = ownWin + ownLoss > 0;
  const wins = h.wins + ownWin, losses = h.losses + ownLoss, settled = wins + losses;
  const label = settled === 0 ? 'No settled matches' : settled === 1 ? `First example ${wins ? 'won' : 'lost'}`
    : wins === losses ? 'Evenly split' : wins > losses ? 'More often right' : 'More often wrong';
  const excluded = Object.values(h.excluded).reduce((a, b) => a + b, 0);
  const against = h.losses > h.wins; // Only OTHER games can trigger it, never this game's own loss.
  const pregame = grade === 'pending' && startAt != null && Date.parse(h.as_of) < Date.parse(startAt);
  return <span className="small percentage-record" title={`Same source: ${h.source}; model: ${h.model_version}. Current record, including results learned after older forecasts. The total includes this game's result only once it has won or lost; the other-example count always excludes it. Lines vary within the same market and line source. Observed counts do not establish a future win rate.`}>
    <strong>Observed: {label}</strong><br />
    {h.sport.toUpperCase()} · {h.market_title} · {h.direction} · {h.percentage}% only
    {h.line_source !== 'none' && <> · {h.line_source === 'bookmaker' ? 'bookmaker lines' : 'model lines'}</>}
    <br />{wins} {wins === 1 ? 'win' : 'wins'} · {losses} {losses === 1 ? 'loss' : 'losses'} · {settled} settled {settled === 1 ? 'pick' : 'picks'}
    <br />{includesThisGame ? "Includes this game's result" : "This game isn't counted"} · {h.settled} other settled {h.settled === 1 ? 'example' : 'examples'}
    {settled > 0 && <><br />{pct(wins / settled, 1)} won / {pct(losses / settled, 1)} lost
      {h.small_sample && <span className="muted"> · small sample</span>}</>}
    {excluded > 0 && <><br /><span className="muted">{excluded} non-decisive or unavailable results excluded</span></>}
    {against && <><br />{opposite ? <>
      <strong>{pregame ? 'Against model — experimental' : 'Opposite scenario — retrospective'}: {opposite}</strong>
      <br />Other matching model picks: {h.wins}–{h.losses} · {h.settled} settled
      <br /><span className="muted">{pregame ? 'Current comparison; not a validated betting rule.' : 'Comparison now; no pregame opposite call was recorded.'}</span>
      <br /><span className="muted">{h.line_source === 'model' ? 'Model line only; no bookmaker offer verified.' : 'Current opposite-side price unverified.'}</span>
    </> : <span className="muted">{h.market === 'reg3' ? 'Three-way market: no single opposite pick.' : 'Opposite side unavailable.'}</span>}</>}
    <br /><span className="muted">Live record · same model · as of {dateTime(h.as_of)}</span>
  </span>;
}
