// Wording of the learning coverage states (pure, unit-tested): the states stay separate and "not established" is never
// shown as a result.
import type { CoverageCell, CoverageRow, LearningCoverageState } from '../api/types.ts';

export const STATE_LABEL: Record<LearningCoverageState, string> = { yes: 'Yes', no: 'No', unknown: 'Unknown', not_established: 'Not established' };

export function cellLabel(c: CoverageCell | undefined): string {
  return c ? (STATE_LABEL[c.state] ?? c.state) : 'Unknown';
}

// `base_tested` (curated base-model test) is sent beside the typed row fields.
export type CoverageRowX = CoverageRow & { base_tested?: CoverageCell };

export const COLUMNS = [
  ['implemented', 'Market served'], ['trained', 'Trained (current definition)'], ['scheduled', 'Installed (plist)'],
  ['recording_prospectively', 'Recording after a fit'], ['forecasting_with_fits', 'Forecasting with fits'],
  ['base_tested', 'Base model test'], ['evaluated', 'Settled with fit'], ['improved_prospectively', 'Improved prospectively'],
] as const;

/** Inline caveat shown under a column heading and in every cell where the state alone could be misread. */
export const COLUMN_NOTE: Partial<Record<typeof COLUMNS[number][0], string>> = {
  scheduled: 'plist file found; not proof it is loaded',
  base_tested: 'curated one-time test of the base model, not a fit',
  evaluated: 'a count of settled forecasts that carried a fit, not a verdict',
};

export function marketLabel(market: string): string {
  return market.replace(/_/g, ' ').replace(/\bou\b/, 'O/U').replace(/\b1x2\b/, '1X2').replace(/\bbtts\b/, 'BTTS');
}
