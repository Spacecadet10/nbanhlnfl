// Wording of the learning coverage states (pure, unit-tested): the states stay separate and "not established" is never
// shown as a result.
import type { CoverageCell, LearningCoverageState } from '../api/types.ts';

export const STATE_LABEL: Record<LearningCoverageState, string> = { yes: 'Yes', no: 'No', unknown: 'Unknown', not_established: 'Not established' };

export function cellLabel(c: CoverageCell | undefined): string {
  return c ? (STATE_LABEL[c.state] ?? c.state) : 'Unknown';
}

export const COLUMNS = [
  ['implemented', 'Market served'], ['trained', 'Trained'], ['scheduled', 'Scheduled'], ['recording_prospectively', 'Recording after a fit'],
  ['forecasting_with_fits', 'Forecasting with fits'], ['evaluated', 'Evaluated'], ['improved_prospectively', 'Improved prospectively'],
] as const;

export function marketLabel(market: string): string {
  return market.replace(/_/g, ' ').replace(/\bou\b/, 'O/U').replace(/\b1x2\b/, '1X2').replace(/\bbtts\b/, 'BTTS');
}
