import type { Team } from '../api/types';
import { pct } from '../format';

/** Horizontal split bar: away share on the left, home share on the right. */
export function ProbabilityBar({ home, away, homeProb, label = 'Model win probability' }: {
  home: Team; away: Team; homeProb: number; label?: string;
}) {
  const awayProb = 1 - homeProb;
  return (
    <div className="probbar" role="img" aria-label={`${label}: ${away.abbr} ${pct(awayProb, 1)}, ${home.abbr} ${pct(homeProb, 1)}`}>
      <div className="probbar-labels">
        <span className={awayProb > homeProb ? 'fav' : ''}>{away.abbr} {pct(awayProb, 1)}</span>
        <span className={homeProb >= awayProb ? 'fav' : ''}>{pct(homeProb, 1)} {home.abbr}</span>
      </div>
      <div className="probbar-track">
        <div className="probbar-away" style={{ width: `${awayProb * 100}%` }} />
        <div className="probbar-home" style={{ width: `${homeProb * 100}%` }} />
      </div>
    </div>
  );
}
