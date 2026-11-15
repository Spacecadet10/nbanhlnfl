import type { FreshnessCheck } from '../api/freshness';
import { useRefreshProblems } from '../api/refreshProblems';
import { dateTime, time } from '../format';

const sameDay = (iso: string) => new Date(iso).toDateString() === new Date().toDateString();
const when = (iso: string) => (sameDay(iso) ? time(iso) : dateTime(iso));

const MODE: Record<string, string> = {
  live: 'Live data',
  auto_snapshot: 'Copy, republished when the data changes',
  frozen_snapshot: 'Frozen copy: will not update automatically',
};

/** Separate source-fetch, copy-publication and forecast times, plus the next check and any stale/failed state
 *  (docs/REFRESH_PLAN.md). A recently published copy is not shown as proof that its source data is recent. */
export function FreshnessBar({ check }: { check: FreshnessCheck }) {
  const f = check.data;
  const problems = useRefreshProblems();
  const stale = problems.length > 0 && (
    <span className="freshness-flag" title={problems.join('\n')}>
      Update failed ({problems[0]}); showing the last loaded data, retrying
    </span>
  );
  if (!f || f.mode === 'demo') {
    if (!check.error && !stale) return null;
    return <div className="freshness-bar is-warn" role="status">{check.error && <span>Freshness check failed: {check.error}</span>}{stale}</div>;
  }
  const items: { key: string; label: string; value: string; warn?: boolean; title?: string }[] = [];
  items.push({ key: 'source', label: 'Data updated', value: f.source_fetched_at ? when(f.source_fetched_at) : 'never',
    warn: f.source_state !== 'fresh',
    title: f.source_state === 'stale' ? `Older than expected (more than ${Math.round((f.stale_after_seconds ?? 0) / 60)} min for the current activity)` : undefined });
  if (f.mode !== 'live') items.push({ key: 'copy', label: 'Copy published', value: f.published_at ? when(f.published_at) : 'unknown' });
  items.push(f.forecasts_supported
    ? { key: 'forecast', label: 'Forecast made', value: f.forecast_generated_at ? when(f.forecast_generated_at) : 'none yet' }
    : { key: 'forecast', label: 'Forecasts', value: 'not supported for NFL' });
  if (f.next_check_at && f.mode !== 'frozen_snapshot') items.push({ key: 'next', label: 'Next check', value: when(f.next_check_at) });
  return (
    <div className={`freshness-bar${f.source_state === 'stale' || f.failing || stale ? ' is-warn' : ''}`} role="status" aria-live="polite">
      <span className="freshness-mode">{MODE[f.mode] ?? f.mode}</span>
      {items.map((i) => (
        <span key={i.key} className={`freshness-item${i.warn ? ' is-warn' : ''}`} title={i.title}>
          <span className="freshness-label">{i.label}</span> {i.value}
        </span>
      ))}
      {f.source_state === 'stale' && <span className="freshness-flag">Source data stale</span>}
      {f.failing && f.last_failed_at && <span className="freshness-flag" title={f.last_error ?? undefined}>Last refresh failed {when(f.last_failed_at)}</span>}
      {f.publish_error && <span className="freshness-flag" title={f.publish_error}>Copy publication failed; showing the previous copy</span>}
      {stale}
      {check.error && <span className="freshness-flag">Check failed: {check.error}</span>}
      {check.paused && <span className="freshness-note">Checks paused (offline)</span>}
      {check.checking && <span className="freshness-note"><span className="freshness-dot" aria-hidden="true" />Updating…</span>}
    </div>
  );
}
