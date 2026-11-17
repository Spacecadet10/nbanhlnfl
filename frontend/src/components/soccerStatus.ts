// What the soccer screens say about forecasts and refreshes, derived only from what the API reports (backend
// app/soccer.py forecast_state, /api/soccer/live). Pure text so it is unit-tested (soccerStatus.test.mjs): the page must
// never say "no forecasts" beside recorded ones, and must tell apart forecasts recorded, matches still awaiting theirs,
// a competition with nothing stored, and a source that failed or went stale.

/** 'unknown': the API serving this page is older than the page and does not report the state (during an update). */
export type SoccerForecastState = 'recorded' | 'awaiting' | 'out_of_scope' | 'no_live_store' | 'unknown';
type Side = 'home' | 'draw' | 'away';
interface MatchForecast { probabilities: Record<Side, number>; most_likely: Side; result: Side | null }
interface LiveFacts {
  check: { checked_at: string } | null;
  refresh?: { last_refresh_at: string | null; max_age_hours: number; stale: boolean };   // absent from an older API
}
/** A match's forecast cutoff: `when` is the formatted time, `passed` whether it is already behind us. */
export interface ForecastCutoff { passed: boolean; when: string }

/** The state to describe: the API's own, or for an API that does not send it yet, what its capability line allows. */
export function forecastStateOf(state: SoccerForecastState | undefined, forecastsAvailable: boolean): SoccerForecastState {
  return state ?? (forecastsAvailable ? 'recorded' : 'unknown');
}

const pct = (p: number) => `${(p * 100).toFixed(1)}%`;

/** The notice above a competition's matches. `kind` picks the styling; `more` labels the expandable capability list. */
export function forecastBanner(state: SoccerForecastState, competition: string): { kind: 'live' | 'awaiting' | 'none'; title: string; text: string; more: string } {
  if (state === 'recorded') {
    return { kind: 'live', title: 'Live soccer forecasts:', text: 'the 90-minute home/draw/away result (with double chance and draw no bet from it); goal and corner markets only where their own tested model covers this competition.', more: 'What else is not available' };
  }
  if (state === 'awaiting') {
    return { kind: 'awaiting', title: `No ${competition} forecast is recorded yet:`,
      text: 'the live 90-minute model covers this competition; each match is forecast once, in the two hours before kickoff.', more: 'Details' };
  }
  if (state === 'out_of_scope') {
    return { kind: 'none', title: `No live forecast for ${competition}:`,
      text: 'it did not pass its held-out test, so only fixtures and results are shown.', more: 'Details' };
  }
  if (state === 'unknown') {
    return { kind: 'none', title: 'Forecast status not reported:',
      text: 'this server version does not say whether this competition is forecast; fixtures and results are shown.', more: 'Details' };
  }
  return { kind: 'none', title: 'No live soccer forecasts in this data store:', text: 'fixtures and results only.', more: 'Details' };
}

/** One match's forecast line: its recorded probabilities, or why there is none (yet). */
export function matchForecastNote(state: SoccerForecastState, status: string, forecast: MatchForecast | null | undefined, home: string, away: string,
  cutoff?: ForecastCutoff | null, twoHour?: ForecastCutoff | null, kind?: ForecastKind): string {
  if (forecast) {
    const names: Record<Side, string> = { home: `${home} win`, draw: 'Draw', away: `${away} win` };
    const p = forecast.probabilities;
    return `90-minute forecast: ${names.home} ${pct(p.home)} · Draw ${pct(p.draw)} · ${names.away} ${pct(p.away)}.`
      + (kind === 'daily' ? ' This is the older daily forecast, not a two-hour one.' : '')
      + (kind === 'original_kickoff' ? ' Made in the two hours before the original kickoff; the kickoff moved later.' : '')
      + (forecast.result ? ` 90-minute result: ${names[forecast.result]}.` : '');
  }
  if (state === 'unknown') return 'Forecast status is not reported by this server version.';
  if (state === 'no_live_store') return 'No forecast: this data store records none.';
  if (state === 'out_of_scope') return 'No forecast: this competition is outside the live model’s tested scope.';
  if (status !== 'scheduled') return 'No forecast was recorded for this match.';
  // `twoHour.when` = when this match's two-hour window opens; `passed` = its kickoff is behind us.
  if (twoHour) return twoHour.passed ? 'No two-hour forecast was saved for this match.'
    : `No forecast yet: the two-hour forecast is saved from ${twoHour.when} until kickoff.`;
  // A forecast is never made after its cutoff, so a scheduled match past it is no longer waiting for one.
  if (cutoff?.passed) return `No forecast: none was recorded before this match’s cutoff (${cutoff.when}), and none is made after it.`;
  return `Awaiting forecast: recorded ${cutoff ? `by ${cutoff.when} at the latest` : 'before its cutoff'}, once the earlier results it depends on are stored.`;
}

/** Hint under "Last imported": what refreshes this competition, and whether its data is too old for new forecasts. */
export function importHint(competition: string, live: LiveFacts | null, when: (iso: string) => string, state: SoccerForecastState = 'recorded'): string {
  const base = `Latest ${competition} scoreboard request`;
  if (!live || !live.refresh) return `${base}.`;
  if (live.refresh.stale) {
    const forecast = state === 'recorded' || state === 'awaiting';   // only these competitions have forecasts to refuse
    if (!live.refresh.last_refresh_at) return `No ${competition} scoreboard request is stored${forecast ? ', so no forecast can be made' : ''}.`;
    return `${base}: more than ${live.refresh.max_age_hours} h old${forecast ? ', so new forecasts are refused until it refreshes' : ''}.`;
  }
  return live.check
    ? `${base}. The live soccer job last checked this competition ${when(live.check.checked_at)}.`
    : `${base}. No live soccer job has checked this competition in this data store.`;
}

/** A competition with no stored match at all. */
export function emptyCompetition(competition: string, fetchedDays: number, lastRefresh: string | null, when: (iso: string) => string): string {
  if (fetchedDays > 0) {
    return `No ${competition} matches are stored: ESPN listed none on the ${fetchedDays} day${fetchedDays === 1 ? '' : 's'} requested so far`
      + `${lastRefresh ? ` (latest request ${when(lastRefresh)})` : ''}.`;
  }
  return `No ${competition} matches are stored, and no day has been requested for this competition yet.`;
}

/** The live forecast list could not be read: say so instead of silently showing fixtures only. */
export function liveLoadProblem(error: string): string {
  return `Live forecasts could not be loaded (${error}). Fixtures and results below are not affected.`;
}

/** The forecast sentence of the "Real data" note in the header. */
export function dataSourceForecastText(state: SoccerForecastState): string {
  const rest = ' Bookmaker prices: DraftKings as listed on ESPN (result, goals total, goal handicap) where available; no injury information is collected.';
  if (state === 'recorded') return `Live forecasts cover the 90-minute result, and goal and corner markets where tested.${rest}`;
  if (state === 'awaiting') return `A live 90-minute forecast model covers this competition; none is recorded for it yet.${rest}`;
  if (state === 'out_of_scope') return `No live forecast is made for this competition.${rest}`;
  if (state === 'unknown') return rest.trim();
  return `This data store records no live soccer forecast.${rest}`;
}

// --- What the two-hour forecast job saved for one date and competition -----------------------------------------------------
const TWO_HOURS = 2 * 3600 * 1000;
const t = (iso: string) => Date.parse(iso);
export type ForecastKind = 'two_hour' | 'original_kickoff' | 'daily' | 'none';
interface SavedForecast { generated_at: string; cutoff_at: string }
interface DayMatch { match_id: string; kickoff: string }
export interface DaySummary {
  total: number;
  elapsed: { count: number; twoHour: number; originalOnly: number; dailyOnly: number; none: number };
  upcoming: { count: number; twoHourSaved: number; notYet: number; dailyOnly: number; windowOpensAt: string | null };
  moved: { matchId: string; from: string; to: string; timelyForUpdated: boolean }[];
}

/** 'two_hour': generated in the two hours before the CURRENT kickoff; 'original_kickoff': on time only for the kickoff it was
 * made for (a two-hour row's cutoff_at is that kickoff) because the kickoff moved later; 'daily': any older forecast (the
 * 06:00 UTC daily one); 'none'. */
export function forecastKind(f: SavedForecast | null | undefined, kickoff: string): ForecastKind {
  if (!f) return 'none';
  const g = t(f.generated_at);
  const inWindow = (k: string) => g >= t(k) - TWO_HOURS && g < t(k);
  return inWindow(kickoff) ? 'two_hour' : inWindow(f.cutoff_at) ? 'original_kickoff' : 'daily';
}

export function daySummary(matches: DayMatch[], live: Record<string, SavedForecast | null | undefined>, nowMs: number): DaySummary {
  const s: DaySummary = { total: matches.length, elapsed: { count: 0, twoHour: 0, originalOnly: 0, dailyOnly: 0, none: 0 },
    upcoming: { count: 0, twoHourSaved: 0, notYet: 0, dailyOnly: 0, windowOpensAt: null }, moved: [] };
  let opens: number | null = null;
  for (const m of matches) {
    const f = live[m.match_id];
    const kind = forecastKind(f, m.kickoff);
    // Saved data supports a move only for a two-hour row: its cutoff_at is the kickoff at forecast time (a daily row's is 06:00 UTC).
    if (f && (kind === 'two_hour' || kind === 'original_kickoff') && t(f.generated_at) >= t(f.cutoff_at) - TWO_HOURS
      && t(f.generated_at) < t(f.cutoff_at) && Math.abs(t(f.cutoff_at) - t(m.kickoff)) >= 60000)
      s.moved.push({ matchId: m.match_id, from: f.cutoff_at, to: m.kickoff, timelyForUpdated: kind === 'two_hour' });
    if (t(m.kickoff) <= nowMs) {
      s.elapsed.count++;
      if (kind === 'two_hour') s.elapsed.twoHour++; else if (kind === 'original_kickoff') s.elapsed.originalOnly++;
      else if (kind === 'daily') s.elapsed.dailyOnly++; else s.elapsed.none++;
    } else {
      s.upcoming.count++;
      if (kind === 'two_hour') s.upcoming.twoHourSaved++;
      else {
        s.upcoming.notYet++;
        if (kind === 'daily') s.upcoming.dailyOnly++;
        const open = t(m.kickoff) - TWO_HOURS;
        if (opens === null || open < opens) opens = open;
      }
    }
  }
  if (opens !== null) s.upcoming.windowOpensAt = new Date(opens).toISOString().replace('.000Z', 'Z');
  return s;
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** One paragraph for the banner: counts only, from stored forecasts. `when` formats an ISO time. */
export function daySummaryText(s: DaySummary, when: (iso: string) => string): string {
  if (s.total === 0) return 'No matches on this date.';
  const parts = [`${plural(s.total, 'match', 'matches')} on this date.`];
  const e = s.elapsed;
  if (e.count) {
    const bits = [`${e.twoHour} with a forecast saved in the two hours before kickoff`];
    if (e.originalOnly) bits.push(`${e.originalOnly} on time only for its original kickoff`);
    if (e.dailyOnly) bits.push(`${e.dailyOnly} only with the older daily forecast`);
    if (e.none) bits.push(`${e.none} with no forecast`);
    parts.push(`${e.count} kicked off: ${bits.join(', ')}.`);
  }
  const u = s.upcoming;
  if (u.count) {
    parts.push(`${u.count} upcoming: ${u.twoHourSaved} with its two-hour forecast saved, ${u.notYet} not yet`
      + (u.dailyOnly ? ` (${u.dailyOnly} of them hold only the older daily forecast)` : '')
      + (u.windowOpensAt ? `; the first window opens ${when(u.windowOpensAt)}` : '') + '.');
  }
  for (const m of s.moved) parts.push(`Kickoff moved from ${when(m.from)} to ${when(m.to)} after the forecast: `
    + (m.timelyForUpdated ? 'on time for both.' : 'on time for the original kickoff, not the updated one.'));
  return parts.join(' ');
}
