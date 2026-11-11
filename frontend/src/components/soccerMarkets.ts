// Wording of the soccer goal/corner/double-chance/draw-no-bet selections (backend app/soccer_markets_live.py). Pure
// functions, unit-tested in soccerMarkets.test.mjs. Every number shown comes from the API; nothing here computes a
// probability. A bookmaker offer and a model line are always labelled differently.

export type SoccerMarketKey = 'goals_total' | 'goals_home' | 'goals_away' | 'btts' | 'corners_total' | 'corners_home'
  | 'corners_away' | 'corners_handicap' | 'double_chance' | 'draw_no_bet';
export type SettleResult = 'win' | 'half_win' | 'push' | 'half_loss' | 'loss';
export interface Outcomes { win: number; half_win: number; push: number; half_loss: number; loss: number }

const signed = (x: number) => (x > 0 ? `+${x}` : `${x}`);

/** 'Over 2.5 goals', 'Arsenal over 1.5 goals', 'Both teams to score: yes', 'Arsenal −0.5 corners', 'Arsenal or draw',
 * 'Arsenal (draw no bet)'. */
export function selectionLabel(market: SoccerMarketKey, side: string, line: number | null, home: string, away: string): string {
  const team = (s: string) => (s === 'home' ? home : s === 'away' ? away : 'draw');
  if (market === 'double_chance') return side.split('_').map(team).join(' or ');
  if (market === 'draw_no_bet') return `${team(side)} (draw no bet)`;
  if (market === 'btts') return `Both teams to score: ${side}`;
  if (market === 'corners_handicap') return `${team(side)} ${signed(line ?? 0)} corners`;
  const what = market.startsWith('goals') ? 'goals' : 'corners';
  const l = line == null ? '' : ` ${line}`;
  if (market.endsWith('_total')) return `${side === 'over' ? 'Over' : 'Under'}${l} ${what}`;
  return `${team(market.endsWith('_home') ? 'home' : 'away')} ${side}${l} ${what}`;
}

/** Whole lines can push and quarter lines split the stake: say which kind of line it is. */
export function lineKind(line: number | null): 'none' | 'half' | 'whole' | 'quarter' {
  if (line == null) return 'none';
  const q = Math.round(Math.abs(line) * 4) % 4;
  return q === 2 ? 'half' : q === 0 ? 'whole' : 'quarter';
}

export const pct1 = (p: number) => `${(p * 100).toFixed(1)}%`;

/** The probabilities a reader needs: the win chance, plus push / half results when the line allows them. */
export function outcomeText(p: Outcomes): string {
  const parts = [`${pct1(p.win)} win`];
  if (p.half_win > 1e-9) parts.push(`${pct1(p.half_win)} half win`);
  if (p.push > 1e-9) parts.push(`${pct1(p.push)} push`);
  if (p.half_loss > 1e-9) parts.push(`${pct1(p.half_loss)} half loss`);
  return parts.join(' · ');
}

export const RESULT_LABEL: Record<SettleResult, string> = {
  win: 'Won', half_win: 'Half won', push: 'Push (stake back)', half_loss: 'Half lost', loss: 'Lost',
};

/** The source badge: a bookmaker offer names the bookmaker and its price; a model line says it is one. */
export function sourceText(s: { source: 'model' | 'bookmaker'; provider?: string; american?: number; decimal?: number }): string {
  if (s.source === 'bookmaker' && s.american != null) {
    return `${s.provider ?? 'Bookmaker'} ${s.american > 0 ? '+' : ''}${s.american} (${(s.decimal ?? 0).toFixed(2)})`;
  }
  return 'Model line';
}

export const fairText = (d: number | null) => (d == null ? '—' : d.toFixed(2));

/** Expected return per unit staked at the bookmaker price, as a signed percentage of the stake. */
export const returnText = (er: number | undefined | null) =>
  (er == null ? '—' : `${er >= 1 ? '+' : '−'}${Math.abs((er - 1) * 100).toFixed(1)}%`);
