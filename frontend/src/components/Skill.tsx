import type { MarketId } from '../api/types';

/** Markets whose picks have shown no skill against the bookmaker's line (mirrors threshold_overview.NO_PROVEN_SKILL on the
 * backend; evidence: bookmaker-line replays 2021-24, where picks at the line won about half the time). */
export const NO_PROVEN_SKILL: MarketId[] = ['spread', 'total', 'team_total_home', 'team_total_away'];
export const noProvenSkill = (market: MarketId) => NO_PROVEN_SKILL.includes(market);

/** The plain note shown wherever such a market's estimate appears. */
export function NoSkillNote({ market, compact }: { market: MarketId; compact?: boolean }) {
  if (!noProvenSkill(market)) return null;
  return compact
    ? <span className="no-skill small">No proven skill against the line</span>
    : <p className="no-skill small">No proven skill against the bookmaker's line: at the line, past picks in this market won about half
        the time. Shown for information, not as a pick.</p>;
}
