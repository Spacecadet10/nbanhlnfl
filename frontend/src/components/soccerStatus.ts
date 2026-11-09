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
    return { kind: 'live', title: 'Live soccer forecasts:', text: 'the 90-minute home/draw/away result only.', more: 'What else is not available' };
  }
  if (state === 'awaiting') {
    return { kind: 'awaiting', title: `No ${competition} forecast is recorded yet:`,
      text: 'the live 90-minute model covers this competition, and each match is forecast by 06:00 UTC on its match day.', more: 'Details' };
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
  cutoff?: ForecastCutoff | null): string {
  if (forecast) {
    const names: Record<Side, string> = { home: `${home} win`, draw: 'Draw', away: `${away} win` };
    const p = forecast.probabilities;
    return `90-minute forecast: ${names.home} ${pct(p.home)} · Draw ${pct(p.draw)} · ${names.away} ${pct(p.away)}.`
      + (forecast.result ? ` 90-minute result: ${names[forecast.result]}.` : '');
  }
  if (state === 'unknown') return 'Forecast status is not reported by this server version.';
  if (state === 'no_live_store') return 'No forecast: this data store records none.';
  if (state === 'out_of_scope') return 'No forecast: this competition is outside the live model’s tested scope.';
  if (status !== 'scheduled') return 'No forecast was recorded for this match.';
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
  const rest = ' No soccer odds or injury information is collected.';
  if (state === 'recorded') return `Live forecasts cover the 90-minute result only.${rest}`;
  if (state === 'awaiting') return `A live 90-minute forecast model covers this competition; none is recorded for it yet.${rest}`;
  if (state === 'out_of_scope') return `No live forecast is made for this competition.${rest}`;
  if (state === 'unknown') return rest.trim();
  return `This data store records no live soccer forecast.${rest}`;
}
