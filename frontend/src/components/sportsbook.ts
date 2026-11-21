// One wording for the app-versus-sportsbook line on every card (NBA, NHL, NFL, soccer). Pure, so it is unit-tested
// (sportsbook.test.mjs). Never compares against another line: the backend only reports 'exact' for the same market, side and line.
import type { CardThresholds, SportsbookComparison } from '../api/types';

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

export const DNB_APP = 'App DNB win chance, excluding draws';
export const DNB_BOOK = 'Sportsbook DNB win chance, excluding draws';

/** Draw no bet: the stored Model % is the 90-minute win chance (a draw is a push), and the exact-% history is bucketed on it. */
export const dnbModelNote = (pct: string) => `Model ${pct}% = 90-minute win chance (draw possible); exact-% history is bucketed on this 90-minute %`;

/** e.g. "App 61.0% · Sportsbook 52.4% no-vig (DraftKings -110/-110 at 223.5) · +8.6 pts · quote 2:29 PM · conditional on no push".
 *  Unavailable: the backend's own reason when it sent one, else the standard wording for the status. Never a number. */
export function sportsbookLine(c: SportsbookComparison): string {
  if (c.status !== 'exact' || c.book_probability == null) return c.reason || EMPTY[c.status === 'exact' ? 'no_quote' : c.status];
  const gap = c.gap_pp ?? gapPoints(c.app_probability, c.book_probability);
  const src = [c.provider, c.price, c.line != null ? `at ${c.line}` : null].filter(Boolean).join(' ');
  const at = c.retrieved_at ? quoteTime(c.retrieved_at) : null;
  // A conditional basis compares a different app % than the Model % shown elsewhere on the card: say both are conditional.
  const cond = /^(conditional on [^(]+?)\s*(\(.*\))?$/.exec(c.basis ?? '');
  // Draw no bet (the only market whose basis is 'conditional on no draw'): the exact labels of the Telegram card.
  const dnb = cond?.[1] === 'conditional on no draw';
  const head = dnb ? `${DNB_APP}: ${one(c.app_probability)}% · ${DNB_BOOK}: ${one(c.book_probability)}% no-vig`
    : cond ? `App ${one(c.app_probability)}% vs Sportsbook ${one(c.book_probability)}% no-vig, both ${cond[1]}`
    : `App ${one(c.app_probability)}% · Sportsbook ${one(c.book_probability)}% no-vig`;
  const note = cond ? cond[2]?.slice(1, -1) : c.basis;
  return `${head}${src ? ` (${src})` : ''} · ${formatGap(gap)}${at ? ` · quote ${at}` : ''}${note ? ` · ${note}` : ''}`;
}

// --- Card colouring (backend app/card_threshold.py; GET /api/card-thresholds) -------------------------------------------
// The backend stores each class's daily threshold and says which class every market is; this twin only compares. Exact
// decimal maths on the probability's shortest decimal string (the same digits as Python's str(p)): never a float compare.
export interface CardColourDecision { colour: 'green' | 'red' | null; legend: string | null; scope: string | null }

/** Tenths of a percent as an integer: String(p) rounded half-up to 0.001 (0.5495 -> 550, 0.5494 -> 549); null when the
 *  number has no plain decimal form (exponent notation) or is outside 0..1. */
export function tenthsOf(p: number | string): number | null {
  const m = /^(\d+)(?:\.(\d*))?$/.exec(String(p));
  if (!m) return null;
  const frac = (m[2] ?? '').padEnd(4, '0');
  const milli = Number(m[1]) * 1000 + Number(frac.slice(0, 3)) + (frac[3] >= '5' ? 1 : 0);
  return milli >= 0 && milli <= 1000 ? milli : null;
}

/** "55.0" -> 550 (the stored threshold, already in tenths). */
const thresholdTenths = (t: string) => { const m = /^(\d+)\.(\d)$/.exec(t); return m ? Number(m[1]) * 10 + Number(m[2]) : null; };

/** Green at or above the class (or sport x class cell) green_from, red below; no colour and no legend when the flag is off or
 *  the market has no class or no stored price; no colour but the status legend when the class's line may not colour today. */
export function cardColour(table: CardThresholds | null | undefined, sport: string | undefined, market: string | undefined,
  probability: number | string | null | undefined, lineSource?: string | null): CardColourDecision {
  const none: CardColourDecision = { colour: null, legend: null, scope: null };
  if (!table?.enabled || !sport || !market || probability == null) return none;
  const cls = table.routes?.[sport]?.[market];
  if (!cls || lineSource === (table.no_colour_line_source ?? 'model')) return none;
  const scopes = table.scopes ?? [];
  const line = scopes.find((s) => s.scope === `${sport}:${cls}`) ?? scopes.find((s) => s.scope === cls);
  if (!line) return none;
  // green_from is the backend's one decision (card_threshold.green_from): null when the line may not colour today (missing,
  // stale, unqualified). Older payloads without the field fall back to the threshold of a 'set'/'seed' line only.
  const from = line.green_from !== undefined ? line.green_from : (['set', 'seed'].includes(line.state) ? line.threshold : null);
  const t = from == null ? null : thresholdTenths(from);
  const v = typeof probability === 'string' ? (/^\d+\.\d$/.test(probability) ? thresholdTenths(probability) : null) : tenthsOf(probability);
  if (t == null || v == null) return { colour: null, legend: line.legend, scope: line.scope };   // status shown, never coloured
  return { colour: v >= t ? 'green' : 'red', legend: line.legend, scope: line.scope };
}

/** The Toronto day of `now` (the day a stored threshold colours cards on). */
export const torontoDay = (now: number) => new Date(now).toLocaleDateString('en-CA', { timeZone: 'America/Toronto' });

/** Refetch the stored lines when the cached copy is older than `maxAgeMs` or belongs to another Toronto day (a new day's
 *  line, or one the daily job wrote after the page loaded). */
export function cardTableStale(fetchedAt: number, now: number, table: CardThresholds | null | undefined, maxAgeMs = 10 * 60_000): boolean {
  if (now - fetchedAt > maxAgeMs) return true;
  return Boolean(table?.enabled && table.date && table.date !== torontoDay(now));
}
