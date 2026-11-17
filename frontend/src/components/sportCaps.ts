// What each served sport has, in one place (no React): research views exist for NBA and NHL only, and NCAAB's Game Day has
// a conference filter. The API refuses research calls for NCAAB (404), so screens must not make them.
export type ServedSport = 'nba' | 'nhl' | 'ncaab';

/** Agreement, research selection, tracking, training, paper rules and recovery exist for NBA and NHL; NCAAB is research-free. */
export function hasResearch(sport: ServedSport): boolean {
  return sport !== 'ncaab';
}

/** The conference filter is an NCAAB feature (the API refuses it for any other sport). */
export function hasConferenceFilter(sport: ServedSport): boolean {
  return sport === 'ncaab';
}

/** The selected conference from the URL: only one the API listed; anything else (an old link, another season) means all. */
export function conferenceFrom(raw: string | null, available: readonly string[]): string | null {
  return raw !== null && available.includes(raw) ? raw : null;
}

/** The conference options in display order (alphabetical, no blanks, no repeats). */
export function conferenceOptions(names: readonly (string | null | undefined)[]): string[] {
  return [...new Set(names.filter((n): n is string => typeof n === 'string' && n.trim() !== '').map((n) => n.trim()))]
    .sort((a, b) => a.localeCompare(b));
}
