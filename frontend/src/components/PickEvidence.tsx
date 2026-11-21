// THE card section for one pick, the same on NBA, NHL, NCAAB, NFL and Soccer (Canonical, RankedPicks, GamePage, NflPages, SoccerPages,
// SoccerMarkets). It shows what the Telegram card shows (backend app/telegram.py market_block): the app %, the sportsbook no-vig %
// with its gap, provider, quote time and basis (or why there is none), the live exact-% record (other settled games, pushes), the
// recovered Telegram history and the record basis. Fields an older API does not send are left out. Wording: cardRecord.ts.
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { CardThresholds, ForecastPercentageHistory, RecordBasis, SportsbookComparison } from '../api/types';
import { dateTime, pct } from '../format';
import { liveRecordLine, recordBasisLines, recordLabel, telegramLine, thisGameLine } from './cardRecord';
import { type CardColourDecision, cardColour, cardTableStale, dnbModelNote, quoteTime, sportsbookLine } from './sportsbook';

// Card colouring (GET /api/card-thresholds): one shared copy for every card on the page, refetched when it is older than
// 10 minutes or from another Toronto day. The thresholds are stored lines, never computed here. A failed fetch is said on
// the card ('error'), never shown as a plain card that looks like the flag being off.
type CardTableState = CardThresholds | 'error' | null;
let cardCache: { at: number; table: Promise<CardTableState> } | null = null;
function loadCardTable(now: number, current: CardTableState): Promise<CardTableState> {
  if (!cardCache || cardTableStale(cardCache.at, now, current === 'error' ? null : current) || current === 'error') {
    cardCache = { at: now, table: api.cardThresholds().then((t): CardTableState => t, (): CardTableState => 'error') };
  }
  return cardCache.table;
}
export function useCardThresholds(): CardTableState {
  const [table, setTable] = useState<CardTableState>(null);
  useEffect(() => {
    let live = true;
    let last: CardTableState = null;
    const refresh = () => loadCardTable(Date.now(), last).then((t) => { last = t; if (live) setTable(t); });
    refresh();
    const timer = window.setInterval(refresh, 5 * 60_000);
    return () => { live = false; window.clearInterval(timer); };
  }, []);
  return table;
}

/** A model % coloured as the card colours it: green (with a check) at or above the class threshold, red and struck through
 *  below it; plain when the flag is off or the market has no colour. */
export function ColouredValue({ text, decision }: { text: string; decision: CardColourDecision }) {
  if (!decision.colour) return <>{text}</>;
  const where = decision.colour === 'green' ? 'at or above the green line' : 'below the green line';
  return <span className={`model-pct model-pct-${decision.colour}`} title={decision.legend ?? undefined} aria-label={`${text}, ${where}`}>
    {decision.colour === 'red' ? <s>{text}</s> : text}{decision.colour === 'green' ? ' ✓' : ''}
  </span>;
}

/** The model % next to a card section, coloured identically to the card section itself. */
export function ModelPct({ probability, history }: { probability: number | null | undefined; history?: ForecastPercentageHistory | null }) {
  const state = useCardThresholds();
  const table = state === 'error' ? null : state;
  return <ColouredValue text={pct(probability, 1)} decision={cardColour(table, history?.sport, history?.market, probability, history?.line_source)} />;
}

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
  const state = useCardThresholds();
  const table = state === 'error' ? null : state;
  const colour = cardColour(table, h?.sport, h?.market, header?.probability ?? h?.percentage, h?.line_source);
  const key = colour.legend ? <><br /><span className={colour.legend.startsWith('Green = ') ? 'card-colour-key' : 'card-colour-status'}>{colour.legend}</span></>
    : state === 'error' ? <><br /><span className="muted">Colour key unavailable: card thresholds could not be loaded</span></> : null;
  const bookText = sportsbook ? sportsbookLine(sportsbook) : null;
  // The same model % repeated as "App 50.8% · …" in the sportsbook line is coloured the same (never a conditional DNB %).
  const shown = header ? pct(header.probability, 1) : h ? `${h.percentage}%` : null;
  const app = bookText && shown && colour.colour && bookText.startsWith(`App ${shown} · `) ? shown : null;
  const book = app && bookText ? <>App <ColouredValue text={app} decision={colour} />{bookText.slice(`App ${app}`.length)}</> : bookText;
  const basis = recordBasisLines(recordBasis, quoteTime, { market: h?.market, teams });
  const dnbPct = (h?.market as string | undefined) === 'draw_no_bet' ? h?.percentage ?? null
    : sportsbook?.basis?.startsWith('conditional on no draw') && header ? (header.probability * 100).toFixed(1) : null;
  const dnbNote = dnbPct != null ? <>{dnbModelNote(dnbPct)}<br /></> : null;
  const headLine = header && <>
    <strong>{header.pick}</strong> · {header.market}{header.line != null && <> · line {header.line > 0 ? '+' : ''}{header.line}</>} · app <ColouredValue text={pct(header.probability, 1)} decision={colour} /><br />
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
  const tg = telegramLine(h);
  const rate = h.wins + h.losses;
  return <span className="small percentage-record pick-evidence" title={`Same source: ${h.source}; model: ${h.model_version}. Current record, including results learned after older forecasts. The counts are OTHER settled games, as on the Telegram card; this game's own result is shown separately and never added. Lines vary within the same market and line source. Observed counts do not establish a future win rate.`}>
    {headLine}
    {dnbNote}
    {book && <>{bookLabel ? `${bookLabel}: ` : ''}{book}<br /></>}
    <strong>Observed: {recordLabel(h)}</strong><br />
    {h.sport.toUpperCase()} · {h.market_title} · {h.direction} · <ColouredValue text={`${h.percentage}%`} decision={colour} /> only
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
    {key}
  </span>;
}
