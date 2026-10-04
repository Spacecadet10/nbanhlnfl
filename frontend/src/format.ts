export const pct = (p: number | null | undefined, digits = 0) =>
  p == null ? '—' : `${(p * 100).toFixed(digits)}%`;

export const num = (n: number | null | undefined, digits = 3) => (n == null ? '—' : n.toFixed(digits));

export const time = (iso: string) =>
  new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', timeZoneName: 'short' });

/** Start time in the viewer's zone; adds the weekday when the local date differs from the slate's (US Eastern) date. */
export const tipoffTime = (iso: string, slateDate: string) => {
  const d = new Date(iso);
  const local = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  return local === slateDate ? time(iso) : `${d.toLocaleDateString([], { weekday: 'short' })} ${time(iso)}`;
};

/** American odds as quoted: +150, -120, EVEN. */
export const american = (price: number | null | undefined) =>
  price == null ? '—' : Math.abs(price) === 100 ? 'EVEN' : price > 0 ? `+${Math.round(price)}` : `${Math.round(price)}`;

export const dateTime = (iso: string) =>
  new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short' });

/** Formats a YYYY-MM-DD calendar date without timezone shifting. */
export const calendarDate = (ymd: string, opts: Intl.DateTimeFormatOptions = { weekday: 'short', month: 'short', day: 'numeric' }) => {
  const [y, m, d] = ymd.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString([], opts);
};

export const shiftDate = (ymd: string, days: number) => {
  const [y, m, d] = ymd.split('-').map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d + days));
  return dt.toISOString().slice(0, 10);
};

export const FULL_DATE: Intl.DateTimeFormatOptions = { month: 'short', day: 'numeric', year: 'numeric' };
