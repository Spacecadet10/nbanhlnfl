// The words of the shared pick-evidence section (PickEvidence.tsx), as pure functions so they are unit-tested
// (cardRecord.test.mjs). The same facts and wording as the Telegram card (backend app/telegram.py market_block):
// record counts are OTHER settled games; this game's own result is shown on its own line and never added to them.
// Every field the older API does not send is simply left out: nothing is invented.
import type { RecordBasis } from '../api/types';
import { selectionLabel, type SoccerMarketKey } from './soccerMarkets.ts';

/** The bucket rule (twin of backend bucket-record-v1): green = at least one win and win rate >= 75% (4w >= 3(w+l), exact integer
 *  maths); red = losses exceed wins; neutral otherwise, including no settled history. Pushes and voids are not in the denominator. */
export type BucketColour = 'green' | 'red' | 'neutral';
export function bucketColour(wins: number, losses: number): BucketColour {
  if (wins >= 1 && 4 * wins >= 3 * (wins + losses)) return 'green';
  return losses > wins ? 'red' : 'neutral';
}

/** One bucket's record as the API sends it (live, recomputed past seasons or recovered Telegram). */
export interface BucketRecord { wins: number; losses: number; pushes?: number; voids?: number; small_sample?: boolean; colour?: string }

/** The backend's colour when it sent a valid one (single decision), else the TS rule on the same counts. */
export function bucketColourOf(r: BucketRecord | null | undefined): BucketColour {
  if (!r) return 'neutral';
  return r.colour === 'green' || r.colour === 'red' || r.colour === 'neutral' ? r.colour : bucketColour(r.wins, r.losses);
}

export const SMALL_SAMPLE_BELOW = 30;   // twin of backend threshold_overview.MIN_LIVE_GRADED
function bucketText(r: BucketRecord): string {
  const settled = r.wins + r.losses;
  const pushes = r.pushes ? ` · ${r.pushes} ${r.pushes === 1 ? 'push' : 'pushes'}` : '';
  const voids = r.voids ? ` · ${r.voids} ${r.voids === 1 ? 'void' : 'voids'}` : '';
  const small = settled > 0 && (settled < SMALL_SAMPLE_BELOW || r.small_sample) ? ` · small sample (${settled} settled)` : '';
  const head = settled === 0 ? 'no settled history at this exact %' : `${r.wins}–${r.losses} (${Math.round((100 * r.wins) / settled)}%)`;
  return `${head}${pushes}${voids}${small}`;
}

/** The sample note printed beside every colour key (Telegram 'Sample note · …', the web legend): a colour is a record, and a short one is weak. */
export const SMALL_SAMPLE_NOTE = `Colours from fewer than ${SMALL_SAMPLE_BELOW} settled results are small samples.`;

/** A cue that does not depend on seeing the colour: green gets a check, red a down triangle (its record is a losing one); neutral nothing. */
export const COLOUR_CUE: Record<BucketColour, string> = { green: ' ✓', red: ' ▼', neutral: '' };

/** Why a % is coloured, with the record that supports it (tooltip and screen-reader text): "…so far: 8–1 (89%) · 1 push · small sample (9 settled)";
 *  from 30 settled on the count is added ("· 44 settled"). Without a record, the rule alone. Display only: the colour is decided elsewhere. */
export function colourReason(colour: BucketColour, r?: BucketRecord | null): string {
  if (colour === 'neutral') return '';
  const rule = colour === 'green' ? 'this exact % has won at least 75% of its settled picks so far' : 'this exact % has lost more than it has won so far';
  if (!r) return rule;
  const settled = r.wins + r.losses;
  return `${rule}: ${bucketText(r)}${settled >= SMALL_SAMPLE_BELOW && !r.small_sample ? ` · ${settled} settled` : ''}`;
}

/** Headline, the same text as the Telegram card: "57.3% · this exact % has gone 8–1 (89%) · 1 push · small sample (9 settled)". */
export function bucketHeadline(percentage: string, r: BucketRecord): string {
  const t = bucketText(r);
  return `${percentage}% · ${r.wins + r.losses === 0 ? t : `this exact % has gone ${t}`}`;
}

/** "11-02 to 11-16", the Telegram card's date range; '' when missing or unreadable. */
const periodText = (p: { from: string; to: string } | null | undefined) => (p && p.from.length >= 10 && p.to.length >= 10 ? ` ${p.from.slice(5, 10)} to ${p.to.slice(5, 10)}` : '');
const colourMark = (c: BucketColour) => (c === 'neutral' ? '' : ` · ${c} record`);

/** A recovered record as the API sends it: `line` is exactly the text Telegram prints (null = Telegram hides it). */
export type RecoveredRecord = Omit<BucketRecord, 'wins' | 'losses' | 'pushes' | 'voids'> & {
  wins?: number | null; losses?: number | null; pushes?: number | null; voids?: number | null; line?: string | null; available?: boolean;
  period?: { from: string; to: string } | null; overlap_live?: number | null };

/** The recovered Telegram record as its own line. The backend `line` is rendered verbatim, tinted by this record's own colour.
 *  Only an older API without `line` gets the local builder below (Telegram's formats; never 0–0, never the live record). */
export function telegramRecoveredLine(t: RecoveredRecord | null | undefined): { text: string; colour: BucketColour } {
  const base = 'Recovered Telegram cards';
  const none = 'model version not recorded';
  if (t && typeof t.line === 'string') return { text: t.line, colour: t.available === false ? 'neutral' : bucketColourOf({ wins: t.wins ?? 0, losses: t.losses ?? 0, colour: t.colour }) };
  if (!t || t.available === false) return { colour: 'neutral', text: `${base} (${none}) · recovered history unavailable` };
  const label = `${base}${periodText(t.period)} (${none})`;
  const rec = { ...t, wins: t.wins ?? 0, losses: t.losses ?? 0, pushes: t.pushes ?? 0, voids: t.voids ?? 0 };
  const n = t.overlap_live ? ` · ${t.overlap_live} ${t.overlap_live === 1 ? 'game' : 'games'} also in the live record, not added` : '';
  if (rec.wins + rec.losses === 0) {
    const pushes = t.pushes ? ` · ${t.pushes} ${t.pushes === 1 ? 'push' : 'pushes'}` : '';
    const voids = t.voids ? ` · ${t.voids} ${t.voids === 1 ? 'void' : 'voids'}` : '';
    return { colour: 'neutral', text: `${label} · no recovered history at this exact %${pushes}${voids}${n}` };
  }
  const colour = bucketColourOf(rec);
  return { colour, text: `${label} · ${bucketText(rec)}${colourMark(colour)}${n}` };
}

/** The recomputed past-seasons line: the backend `line` verbatim; null when Telegram hides it (`line` null); the local builder
 *  only when an older API sends no `line`. */
export function historyLine(h: (BucketRecord & { line?: string | null }) | null | undefined): { text: string; colour: BucketColour } | null {
  if (!h || h.line === null) return null;
  const colour = bucketColourOf(h);
  return { text: typeof h.line === 'string' ? h.line : `${bucketSubLine('Recomputed past seasons', h)}${colourMark(colour)}`, colour };
}

/** A separate, labelled line for the recovered / recomputed record; never merged into the headline counts. */
export function bucketSubLine(label: string, r: BucketRecord): string {
  return `${label} · ${bucketText(r)}`;
}

/** This game's own result, kept out of the counts above: it is never part of the record it is compared with. */
export function thisGameLine(grade: string | null | undefined): string {
  switch (grade) {
    case 'win': return "This game: won (not in the counts above)";
    case 'loss': return "This game: lost (not in the counts above)";
    case 'push': return 'This game: push (not in the counts above)';
    case 'half_win': return 'This game: half win (not in the counts above)';
    case 'half_loss': return 'This game: half loss (not in the counts above)';
    default: return "This game isn't counted until it is settled";
  }
}

/** Home and away from a ranked pick's "AWAY @ HOME" matchup; undefined when it has another shape. */
export function teamsOf(matchup: string | null | undefined): { home: string; away: string } | undefined {
  const m = matchup?.split(' @ ');
  return m && m.length === 2 ? { away: m[0], home: m[1] } : undefined;
}

/** The pick of a sent card in words ('ORL -9.5', 'Over 215.5', 'Draw'), never the raw side code; the API's pick_label wins.
 *  Sides follow markets.SIDE_LABELS: a/b = home/away (winner, spread, three-way) or over/under (totals); mid = draw. */
export function publishedPickText(p: NonNullable<RecordBasis['published']>, market: string | undefined, teams?: { home: string; away: string }): string {
  if (p.pick_label) return p.pick_label;
  if (teams && (market === 'double_chance' || market === 'draw_no_bet' || market === 'btts' || market?.startsWith('goals_') || market?.startsWith('corners_'))) {
    return selectionLabel(market as SoccerMarketKey, p.side ?? '', p.line ?? null, teams.home, teams.away);
  }
  const isTotal = market === 'total' || market === 'team_total_home' || market === 'team_total_away';
  const word = p.side === 'a' ? (isTotal ? 'over' : 'home') : p.side === 'b' ? (isTotal ? 'under' : 'away') : p.side === 'mid' ? 'draw' : (p.side ?? '?');
  let who = word === 'home' ? (teams?.home ?? 'Home') : word === 'away' ? (teams?.away ?? 'Away') : word.charAt(0).toUpperCase() + word.slice(1);
  if (isTotal && market !== 'total' && teams) who = `${market === 'team_total_home' ? teams.home : teams.away} ${word}`;
  if (p.line == null) return who;
  const signed = !isTotal;
  return `${who} ${signed && p.line > 0 ? '+' : ''}${p.line}`;
}

/** The record-basis notes of contract item 4: which forecast counts, and how a card that was sent differs. */
export function recordBasisLines(rb: RecordBasis | null | undefined, clock: (iso: string) => string | null,
  ctx: { market?: string; teams?: { home: string; away: string } } = {}): string[] {
  if (!rb) return [];
  const out: string[] = [];
  if (rb.pregame_forecasts > 1) {
    const at = clock(rb.generated_at);
    out.push(`Record uses the latest pregame forecast (#${rb.forecast_id}${at ? `, ${at}` : ''}); ${rb.pregame_forecasts - 1} earlier kept for audit`);
  }
  const p = rb.published;
  // Only when the sent pick differs (side, line or displayed %); the same pick re-forecast under another id adds nothing.
  const pct = (x: number | null | undefined) => (x == null ? null : (x * 100).toFixed(1));
  if (p && p.forecast_id !== rb.forecast_id && (p.side !== rb.side || p.line !== rb.line || pct(p.probability) !== pct(rb.probability))) {
    const grade = p.grade === 'win' ? 'won' : p.grade === 'loss' ? 'lost' : (p.grade ?? 'not graded');
    out.push(`Telegram card sent forecast #${p.forecast_id} (${publishedPickText(p, ctx.market, ctx.teams)}${p.probability == null ? '' : `, ${(p.probability * 100).toFixed(1)}%`}): ${grade}`);
  }
  return out;
}
