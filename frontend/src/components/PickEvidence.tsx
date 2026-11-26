// THE card section for one pick, the same on NBA, NHL, NCAAB, NFL and Soccer (Canonical, RankedPicks, GamePage, NflPages, SoccerPages,
// SoccerMarkets). It shows what the Telegram card shows (backend app/telegram.py market_block): the app %, the sportsbook no-vig %
// with its gap, provider, quote time and basis (or why there is none), the live exact-% record (other settled games, pushes), the
// recovered Telegram history and the record basis. Fields an older API does not send are left out. Wording: cardRecord.ts.
import type { ForecastPercentageHistory, RecordBasis, SportsbookComparison } from '../api/types';
import { dateTime, pct } from '../format';
import { bucketColourOf, bucketHeadline, colourReason, COLOUR_CUE, historyLine, recordBasisLines, SMALL_SAMPLE_NOTE, telegramRecoveredLine, thisGameLine, type BucketColour, type BucketRecord } from './cardRecord';
import { dnbModelNote, quoteTime, sportsbookLine } from './sportsbook';

/** A model % coloured by its own exact-% bucket record (green with a check, red with a triangle: neither depends on the colour alone);
 *  neutral = plain. The tooltip and the screen-reader text carry the supporting record (W–L, rate, n settled, small sample).
 *  Colour only describes the history of this bucket; it never hides or reorders a pick. */
export function ColouredValue({ text, colour, record }: { text: string; colour: BucketColour; record?: BucketRecord | null }) {
  if (colour === 'neutral') return <>{text}</>;
  const where = colourReason(colour, record);
  return <span className={`model-pct model-pct-${colour}`} title={where} aria-label={`${text}, ${where}`}>{text}{COLOUR_CUE[colour]}</span>;
}

/** The model % next to a card section, coloured identically to the card section itself. */
export function ModelPct({ probability, history }: { probability: number | null | undefined; history?: ForecastPercentageHistory | null }) {
  return <ColouredValue text={pct(probability, 1)} colour={history ? bucketColourOf(history) : 'neutral'} record={history} />;
}

const LEGEND: Record<'green' | 'red', string> = {
  green: `Green ✓: this exact % has won at least 75% of its settled picks so far — a historical record, not a profit guarantee. ${SMALL_SAMPLE_NOTE}`,
  red: `Red ▼: this exact % has lost more than it has won so far. ${SMALL_SAMPLE_NOTE}`,
};

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
  const colour = h ? bucketColourOf(h) : 'neutral';
  const key = colour === 'neutral' ? null : <><br /><span className="card-colour-key">{LEGEND[colour]}</span></>;
  const bookText = sportsbook ? sportsbookLine(sportsbook) : null;
  // The same model % repeated as "App 50.8% · …" in the sportsbook line is coloured the same (never a conditional DNB %).
  const shown = header ? pct(header.probability, 1) : h ? `${h.percentage}%` : null;
  const app = bookText && shown && colour !== 'neutral' && bookText.startsWith(`App ${shown} · `) ? shown : null;
  const book = app && bookText ? <>App <ColouredValue text={app} colour={colour} record={h} />{bookText.slice(`App ${app}`.length)}</> : bookText;
  const basis = recordBasisLines(recordBasis, quoteTime, { market: h?.market, teams });
  const dnbPct = (h?.market as string | undefined) === 'draw_no_bet' ? h?.percentage ?? null
    : sportsbook?.basis?.startsWith('conditional on no draw') && header ? (header.probability * 100).toFixed(1) : null;
  const dnbNote = dnbPct != null ? <>{dnbModelNote(dnbPct)}<br /></> : null;
  const headLine = header && <>
    <strong>{header.pick}</strong> · {header.market}{header.line != null && <> · line {header.line > 0 ? '+' : ''}{header.line}</>} · app <ColouredValue text={pct(header.probability, 1)} colour={colour} record={h} /><br />
  </>;
  if (!h) return <span className="small percentage-record pick-evidence">{headLine}
    {dnbNote}
    {book && <>{bookLabel ? `${bookLabel}: ` : ''}{book}<br /></>}
    <span className="warn">Percentage history unavailable</span>
    {basis.map((b) => <span key={b}><br /><span className="muted">{b}</span></span>)}
    {key}
  </span>;
  const excluded = Object.values(h.excluded ?? {}).reduce((a, b) => a + b, 0);
  const against = h.losses > h.wins; // Only OTHER games can trigger it, never this game's own loss.
  const pregame = grade === 'pending' && startAt != null && Date.parse(h.as_of) < Date.parse(startAt);
  const hl = historyLine(h.history), tl = telegramRecoveredLine(h.telegram);
  return <span className="small percentage-record pick-evidence" title={`Same source: ${h.source}; model: ${h.model_version}. Current record, including results learned after older forecasts. The counts are OTHER settled games, as on the Telegram card; this game's own result is shown separately and never added. Lines vary within the same market and line source. Observed counts do not establish a future win rate.`}>
    {headLine}
    {dnbNote}
    {book && <>{bookLabel ? `${bookLabel}: ` : ''}{book}<br /></>}
    <span className={`bucket-${colour}`}><strong>{bucketHeadline(h.percentage, h)}</strong></span><br />
    {h.sport.toUpperCase()} · {h.market_title} · {h.direction} · <ColouredValue text={`${h.percentage}%`} colour={colour} record={h} /> only
    {h.line_source !== 'none' && <> · {h.line_source === 'bookmaker' ? 'bookmaker lines' : 'model lines'}</>}
    <br />{thisGameLine(grade)}
    {excluded > 0 && <><br /><span className="muted">{excluded} non-decisive or unavailable results excluded</span></>}
    {against && <><br />{opposite ? <>
      <strong>{pregame ? 'Against model — experimental' : 'Opposite scenario — retrospective'}: {opposite}</strong>
      <br />Other matching model picks: {h.wins}–{h.losses} · {h.settled} settled
      <br /><span className="muted">{pregame ? 'Current comparison; not a validated betting rule.' : 'Comparison now; no pregame opposite call was recorded.'}</span>
      <br /><span className="muted">{h.line_source === 'model' ? 'Model line only; no bookmaker offer verified.' : 'Current opposite-side price unverified.'}</span>
    </> : <span className="muted">{h.market === 'reg3' ? 'Three-way market: no single opposite pick.' : 'Opposite side unavailable.'}</span>}</>}
    {hl && <><br /><span className={`bucket-${hl.colour}`}>{hl.text}</span></>}
    <br /><span className={`bucket-${tl.colour}`}>{tl.text}</span>
    {basis.map((b) => <span key={b}><br />{b}</span>)}
    <br /><span className="muted">Live record · same model · as of {dateTime(h.as_of)}</span>
    {key}
  </span>;
}
