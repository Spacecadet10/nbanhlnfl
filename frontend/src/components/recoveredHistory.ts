// Pure helpers for the recovered Telegram predictions (backend app/telegram_history.py): wording, merging and counting.
// Nothing here computes a probability; every percentage is the card's own text.
import type { TgEarlierCard, TgRow, TgTally } from '../api/types';

const GRADES: Record<string, string> = {
  win: 'Correct', loss: 'Wrong', push: 'Push', unsettled: 'Unsettled (no result)', pending: 'Pending', void: 'Void',
  unscorable: 'Not scorable', unresolved: 'Unresolved',
};
export const gradeLabel = (grade: string): string => GRADES[grade] ?? grade;

/** Which record counts the game and market, and how the card compares with the live forecast of it. */
export function overlapText(r: Pick<TgRow, 'overlap'>): string {
  const { status, live, version_id } = r.overlap;
  if (status === 'none' || !live) return 'Counted in the recovered record';
  if (status === 'same') return `Same as live forecast #${live.forecast_id}: counted once, in the live record`;
  const shown = `#${live.forecast_id} (${live.pick} ${live.percentage}%, ${gradeLabel(live.grade)})`;
  return status === 'earlier_live_version'
    ? `Matches live version #${version_id}, replaced before the start by ${shown}: the live forecast is the one counted`
    : `Differs from live forecast ${shown}: both shown, the live forecast is the one counted`;
}

type Counts = Pick<TgTally, 'predictions' | 'win' | 'loss' | 'push' | 'void' | 'unsettled' | 'settled' | 'win_rate'>;

/** "70–22 (76.1%) · 1 push · 2 unsettled"; a dash when there is no prediction at all. */
export function recordText(t: Counts | null | undefined): string {
  if (!t || t.predictions === 0) return '—';
  const parts = [];
  if (t.settled > 0) parts.push(`${t.win}–${t.loss} (${((t.win_rate ?? 0) * 100).toFixed(1)}%)`);
  if (t.push > 0) parts.push(`${t.push} push${t.push === 1 ? '' : 'es'}`);
  if (t.void > 0) parts.push(`${t.void} void`);
  if (t.unsettled > 0) parts.push(`${t.unsettled} unsettled`);
  return parts.join(' · ');
}

export function addTallies(all: Counts[]): Counts {
  const sum = (k: Exclude<keyof Counts, 'win_rate'>) => all.reduce((n, t) => n + t[k], 0);
  const win = sum('win'), loss = sum('loss');
  return { predictions: sum('predictions'), win, loss, push: sum('push'), void: sum('void'), unsettled: sum('unsettled'),
    settled: win + loss, win_rate: win + loss ? win / (win + loss) : null };
}

export type MergedRow<L, R> = { kind: 'live'; row: L } | { kind: 'recovered'; row: R };

/** Live and recovered rows in one list, newest start first; at the same start the live rows come first. */
export function mergeByStart<L, R extends { start_at: string }>(live: L[], startOf: (l: L) => string, recovered: R[]): MergedRow<L, R>[] {
  const at = (x: MergedRow<L, R>) => Date.parse(x.kind === 'live' ? startOf(x.row) : x.row.start_at);
  const rows: MergedRow<L, R>[] = [...live.map((row) => ({ kind: 'live' as const, row })), ...recovered.map((row) => ({ kind: 'recovered' as const, row }))];
  return rows.map((x, i) => ({ x, i })).sort((a, b) => at(b.x) - at(a.x) || a.i - b.i).map((e) => e.x);
}

/** The line note as the card printed it ("Bookmaker line at forecast · sportsbook 49.6% · underpriced"), or null. */
export const lineText = (r: Pick<TgRow, 'card_line_text'>): string | null => r.card_line_text;

export function earlierText(cards: Pick<TgEarlierCard, 'differs'>[]): string | null {
  if (cards.length === 0) return null;
  const n = cards.filter((c) => c.differs).length;
  return `${cards.length} earlier card${cards.length === 1 ? '' : 's'}, ${n ? `${n} different` : 'same prediction'}`;
}

export function unavailableText(reason: string | null, sport: string): string {
  return reason === 'no_rows_for_sport' ? `No recovered Telegram predictions for ${sport}: the source holds none.`
    : reason === 'file_missing' ? 'Recovered Telegram predictions are not imported in this data store.'
    : 'The recovered Telegram predictions could not be read.';
}
