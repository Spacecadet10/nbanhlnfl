// THE card section for one pick, the same on NBA, NHL, NCAAB, NFL and Soccer (Canonical, RankedPicks, GamePage, NflPages, SoccerPages,
// SoccerMarkets). It shows what the Telegram card shows (backend app/telegram.py market_block): the app %, the sportsbook no-vig %
// with its gap, provider, quote time and basis (or why there is none), the live exact-% record (other settled games, pushes), the
// recovered Telegram history and the record basis. Fields an older API does not send are left out. Wording: cardRecord.ts.
import type { ForecastPercentageHistory, RecordBasis, SportsbookComparison } from '../api/types';
import { dateTime, pct } from '../format';
import { liveRecordLine, recordBasisLines, recordLabel, telegramLine, thisGameLine } from './cardRecord';
import { dnbModelNote, quoteTime, sportsbookLine } from './sportsbook';

export interface PickEvidenceProps {
  history: ForecastPercentageHistory | null | undefined;
  grade: string | null;
  sportsbook?: SportsbookComparison | null;
  recordBasis?: RecordBasis | null;
  opposite?: string | null;
  startAt?: string;
  /** Pick, market, line and app % as a first line; leave out where the page already prints them next to this section. */
  header?: { pick: string; market: string; line?: number | null; probability: number } | null;
  /** Home and away names, to put a sent card's pick in words ("ORL -9.5"). */
  teams?: { home: string; away: string };
  /** The label before the sportsbook line when the pick is one of several outcomes (Soccer 1X2): "Home win". */
  bookLabel?: string;
}

/** Exact-percentage observations and an explicitly experimental opposite scenario; original forecasts stay unchanged. */
export function PickEvidence({ history: h, grade, sportsbook, recordBasis, opposite, startAt, header, bookLabel, teams }: PickEvidenceProps) {
  const book = sportsbook ? sportsbookLine(sportsbook) : null;
  const basis = recordBasisLines(recordBasis, quoteTime, { market: h?.market, teams });
  const dnbPct = (h?.market as string | undefined) === 'draw_no_bet' ? h?.percentage ?? null
    : sportsbook?.basis?.startsWith('conditional on no draw') && header ? (header.probability * 100).toFixed(1) : null;
  const dnbNote = dnbPct != null ? <>{dnbModelNote(dnbPct)}<br /></> : null;
  const headLine = header && <>
    <strong>{header.pick}</strong> · {header.market}{header.line != null && <> · line {header.line > 0 ? '+' : ''}{header.line}</>} · app {pct(header.probability, 1)}<br />
  </>;
  if (!h) return <span className="small percentage-record pick-evidence">{headLine}
    {dnbNote}
    {book && <>{bookLabel ? `${bookLabel}: ` : ''}{book}<br /></>}
    <span className="warn">Percentage history unavailable</span>
    {basis.map((b) => <span key={b}><br /><span className="muted">{b}</span></span>)}
  </span>;
  const excluded = Object.values(h.excluded ?? {}).reduce((a, b) => a + b, 0);
  const against = h.losses > h.wins; // Only OTHER games can trigger it, never this game's own loss.
  const pregame = grade === 'pending' && startAt != null && Date.parse(h.as_of) < Date.parse(startAt);
  const tg = telegramLine(h);
  const rate = h.wins + h.losses;
  return <span className="small percentage-record pick-evidence" title={`Same source: ${h.source}; model: ${h.model_version}. Current record, including results learned after older forecasts. The counts are OTHER settled games, as on the Telegram card; this game's own result is shown separately and never added. Lines vary within the same market and line source. Observed counts do not establish a future win rate.`}>
    {headLine}
    {dnbNote}
    {book && <>{bookLabel ? `${bookLabel}: ` : ''}{book}<br /></>}
    <strong>Observed: {recordLabel(h)}</strong><br />
    {h.sport.toUpperCase()} · {h.market_title} · {h.direction} · {h.percentage}% only
    {h.line_source !== 'none' && <> · {h.line_source === 'bookmaker' ? 'bookmaker lines' : 'model lines'}</>}
    <br />{liveRecordLine(h)}
    <br />{thisGameLine(grade)}
    {rate > 0 && <><br />{pct(h.wins / rate, 1)} won / {pct(h.losses / rate, 1)} lost
      {h.small_sample && <span className="muted"> · small sample</span>}</>}
    {excluded > 0 && <><br /><span className="muted">{excluded} non-decisive or unavailable results excluded</span></>}
    {against && <><br />{opposite ? <>
      <strong>{pregame ? 'Against model — experimental' : 'Opposite scenario — retrospective'}: {opposite}</strong>
      <br />Other matching model picks: {h.wins}–{h.losses} · {h.settled} settled
      <br /><span className="muted">{pregame ? 'Current comparison; not a validated betting rule.' : 'Comparison now; no pregame opposite call was recorded.'}</span>
      <br /><span className="muted">{h.line_source === 'model' ? 'Model line only; no bookmaker offer verified.' : 'Current opposite-side price unverified.'}</span>
    </> : <span className="muted">{h.market === 'reg3' ? 'Three-way market: no single opposite pick.' : 'Opposite side unavailable.'}</span>}</>}
    {h.history && h.history.settled > 0 && <><br />Past seasons 2024-26, recomputed{h.history.line_source === 'none' ? '' : h.history.line_source === 'bookmaker' ? ', bookmaker line' : ', model line'}: {h.history.wins}W–{h.history.losses}L{h.history.small_sample ? ' · Small sample.' : ''}
      <span className="muted"> · separate from the live record</span></>}
    {tg && <><br />{tg}<span className="muted"> · separate from the live record</span></>}
    {basis.map((b) => <span key={b}><br />{b}</span>)}
    <br /><span className="muted">Live record · same model · as of {dateTime(h.as_of)}</span>
  </span>;
}
