// The words of the shared pick-evidence section (PickEvidence.tsx), as pure functions so they are unit-tested
// (cardRecord.test.mjs). The same facts and wording as the Telegram card (backend app/telegram.py market_block):
// record counts are OTHER settled games; this game's own result is shown on its own line and never added to them.
// Every field the older API does not send is simply left out: nothing is invented.
import type { ForecastPercentageHistory, RecordBasis } from '../api/types';

const md = (day: string) => day.slice(5, 10);

/** "Other settled games: 3 wins · 1 loss · 1 push · 4 settled", or the empty wording of the contract. */
export function liveRecordLine(h: ForecastPercentageHistory): string {
  if (h.settled === 0) return 'No settled results at this exact % yet';
  const pushes = h.pushes ? ` · ${h.pushes} ${h.pushes === 1 ? 'push' : 'pushes'}` : '';
  return `Other settled games: ${h.wins} ${h.wins === 1 ? 'win' : 'wins'} · ${h.losses} ${h.losses === 1 ? 'loss' : 'losses'}${pushes} · ${h.settled} settled`;
}

/** Which way the live record leans, from the OTHER games only (so the web and Telegram cards agree). */
export function recordLabel(h: ForecastPercentageHistory): string {
  const n = h.wins + h.losses;
  return n === 0 ? 'No settled matches' : n === 1 ? `First example ${h.wins ? 'won' : 'lost'}`
    : h.wins === h.losses ? 'Evenly split' : h.wins > h.losses ? 'More often right' : 'More often wrong';
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

/** "Recovered Telegram cards 11-02 to 11-14: 3W–2L · 1 push (6 settled) · model version not recorded", or what exists instead.
 *  null when the API sent neither the Telegram sub-record nor coverage (an older API). */
export function telegramLine(h: ForecastPercentageHistory): string | null {
  const t = h.telegram, cov = h.coverage;
  if (t) {
    const label = t.label ?? 'Recovered Telegram cards';
    const span = t.period ? ` ${md(t.period.from)} to ${md(t.period.to)}` : '';
    if (!t.settled) return `${label}${span}: none settled at this exact % · model version not recorded`;
    return `${label}${span}: ${t.wins}W–${t.losses}L${t.pushes ? ` · ${t.pushes} push` : ''} (${t.settled} settled)${t.small_sample ? ' · small sample' : ''} · model version not recorded`;
  }
  if (cov == null && h.telegram === undefined) return null;
  const p = cov?.telegram_period;
  if (p) return `Recovered Telegram cards ${md(p.from)} to ${md(p.to)}: none at this exact % · model version not recorded`;
  const sport = h.sport.toUpperCase();
  return `No recovered Telegram history for ${sport}${h.sport === 'ncaab' && cov?.live_since ? ` (recording began ${cov.live_since.slice(0, 10)})` : ''}`;
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
  if (p && p.forecast_id !== rb.forecast_id) {
    const grade = p.grade === 'win' ? 'won' : p.grade === 'loss' ? 'lost' : (p.grade ?? 'not graded');
    out.push(`Telegram card sent forecast #${p.forecast_id} (${publishedPickText(p, ctx.market, ctx.teams)}${p.probability == null ? '' : `, ${(p.probability * 100).toFixed(1)}%`}): ${grade}`);
  }
  return out;
}
