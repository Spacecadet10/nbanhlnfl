// Wording for the saved pregame colours (backend app/pick_colours.py). The API decides every colour and count.
import type { PcRow, PcTally } from '../api/types';

export const COLOUR_LABEL: Record<string, string> = {
  green: 'Green', red: 'Red', neutral: 'Neutral', no_record: 'No recovered record', unknown: 'Colour unknown',
};
export const VIEW_LABEL: Record<string, string> = { all: 'All forecasts', recommended: 'Recommended picks', sent: 'Confirmed Telegram sends' };
export const UNKNOWN_LABEL: Record<string, string> = {
  before_colour_rule: 'before this colour rule went live',
  before_tracking: 'before colours were saved, and no saved card proves it',
  later_forecast_not_saved: 'the forecast that counts replaced the one on the saved card',
  not_recorded: 'not saved before the start (recorder gap)',
  no_exact_record_shown: 'no exact-% record was shown',
};

export function tallyText(t: PcTally): string {
  if (t.predictions === 0) return 'None';
  const extra = [t.push ? `${t.push} push${t.push === 1 ? '' : 'es'}` : '', t.void ? `${t.void} void` : '', t.half ? `${t.half} half result${t.half === 1 ? '' : 's'}` : '',
    t.unsettled ? `${t.unsettled} unsettled` : ''].filter(Boolean);
  const rate = t.win_rate == null ? '' : ` (${(100 * t.win_rate).toFixed(1)}%)`;
  return [`${t.win}–${t.loss}${rate}`, ...extra].join(' · ');
}

export function returnsText(t: PcTally): string {
  if (t.predictions === 0) return '—';
  const cover = `${t.priced} of ${t.predictions} priced`;
  if (t.profit_units == null || t.roi == null) return `${cover} · no settled priced pick`;
  const sign = (n: number) => `${n >= 0 ? '+' : '−'}${Math.abs(n).toFixed(2)}`;
  return `${sign(t.profit_units)}u on ${t.priced_settled} (${n1(100 * t.roi)}%) · ${cover}`;
}
const n1 = (n: number) => `${n >= 0 ? '+' : '−'}${Math.abs(n).toFixed(1)}`;

export const recordOf = (w: number | null, l: number | null, p?: number | null, v?: number | null) =>
  w == null || l == null ? 'no record' : `${w}–${l}${p ? ` · ${p} push${p === 1 ? '' : 'es'}` : ''}${v ? ` · ${v} void` : ''}`;

export function supportText(r: PcRow): string {
  const s = r.support;
  if (!s) return `Colour unknown: ${UNKNOWN_LABEL[r.unknown_reason ?? ''] ?? r.unknown_reason ?? 'not saved'}`;
  const rec = s.recovered_wins == null ? `none (${(s.recovered_reason ?? 'not shown').replace(/_/g, ' ')})` : recordOf(s.recovered_wins, s.recovered_losses, s.recovered_pushes, s.recovered_voids);
  return `Live record then: ${recordOf(s.wins, s.losses, s.pushes, s.voids)} · recovered record then: ${rec}`;
}
