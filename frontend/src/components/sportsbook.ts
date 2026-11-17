// One wording for the app-versus-sportsbook line on every card (NBA, NHL, NFL, soccer). Pure, so it is unit-tested
// (sportsbook.test.mjs). Never compares against another line: the backend only reports 'exact' for the same market, side and line.
import type { SportsbookComparison } from '../api/types';

/** "2:29 PM" in Toronto time, the zone of the Telegram cards; null when absent or unreadable. */
export function quoteTime(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const t = new Date(iso);
  return Number.isNaN(t.getTime()) ? null : t.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', timeZone: 'America/Toronto' });
}

const one = (p: number) => (p * 100).toFixed(1);

/** Signed gap in percentage points from the one-decimal numbers on screen, so the shown values subtract exactly. */
export function gapPoints(app: number, book: number): number {
  return Math.round((Number(one(app)) - Number(one(book))) * 10) / 10;
}

export const formatGap = (gap: number) => `${gap > 0 ? '+' : gap < 0 ? '−' : '±'}${Math.abs(gap).toFixed(1)} pts`;

const EMPTY: Record<Exclude<SportsbookComparison['status'], 'exact'>, string> = {
  no_quote: 'No sportsbook quote saved at this line',
  model_line: 'Model line: no sportsbook line to compare',
  unsupported: 'No sportsbook quotes are collected for this market',
};

/** e.g. "App 61.0% · Sportsbook 52.4% no-vig (DraftKings -110/-110 at 223.5) · +8.6 pts · quote 2:29 PM · conditional on no push".
 *  Unavailable: the backend's own reason when it sent one, else the standard wording for the status. Never a number. */
export function sportsbookLine(c: SportsbookComparison): string {
  if (c.status !== 'exact' || c.book_probability == null) return c.reason || EMPTY[c.status === 'exact' ? 'no_quote' : c.status];
  const gap = c.gap_pp ?? gapPoints(c.app_probability, c.book_probability);
  const src = [c.provider, c.price, c.line != null ? `at ${c.line}` : null].filter(Boolean).join(' ');
  const at = c.retrieved_at ? quoteTime(c.retrieved_at) : null;
  return `App ${one(c.app_probability)}% · Sportsbook ${one(c.book_probability)}% no-vig${src ? ` (${src})` : ''} · ${formatGap(gap)}`
    + `${at ? ` · quote ${at}` : ''}${c.basis ? ` · ${c.basis}` : ''}`;
}
