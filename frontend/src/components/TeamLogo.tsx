import { useState } from 'react';
import type { Sport, Team } from '../api/types';
import { useSport } from './Selection';

// Team logos come from the same free providers as the data: ESPN's logo CDN for the NBA (ESPN abbreviations, as stored)
// and the NHL's own asset server for the NHL (NHL abbreviations, as stored). Only franchise teams are mapped; anything
// else (all-star, international and exhibition sides) shows the abbreviation monogram, as does any logo that fails to load.
const NBA = new Set(['ATL', 'BKN', 'BOS', 'CHA', 'CHI', 'CLE', 'DAL', 'DEN', 'DET', 'GS', 'HOU', 'IND', 'LAC', 'LAL', 'MEM',
  'MIA', 'MIL', 'MIN', 'NO', 'NY', 'OKC', 'ORL', 'PHI', 'PHX', 'POR', 'SA', 'SAC', 'TOR', 'UTAH', 'WSH']);
const NHL = new Set(['ANA', 'BOS', 'BUF', 'CAR', 'CBJ', 'CGY', 'CHI', 'COL', 'DAL', 'DET', 'EDM', 'FLA', 'LAK', 'MIN', 'MTL',
  'NJD', 'NSH', 'NYI', 'NYR', 'OTT', 'PHI', 'PIT', 'SEA', 'SJS', 'STL', 'TBL', 'TOR', 'UTA', 'VAN', 'VGK', 'WPG', 'WSH']);

export function logoUrl(sport: Sport, abbr: string): string | null {
  if (sport === 'nba' && NBA.has(abbr)) {
    return `https://a.espncdn.com/combiner/i?img=/i/teamlogos/nba/500/${abbr.toLowerCase()}.png&h=96&w=96`;
  }
  if (sport === 'nhl' && NHL.has(abbr)) return `https://assets.nhle.com/logos/nhl/svg/${abbr}_light.svg`;
  return null;
}

/** A team's logo in a fixed square (no layout shift), lazily loaded; the abbreviation monogram when there is none. */
export function TeamLogo({ team, sport, size = 40 }: { team: Team; sport?: Sport; size?: number }) {
  const [current] = useSport();
  const [failed, setFailed] = useState<string | null>(null);
  const src = logoUrl(sport ?? current, team.abbr);
  const box = { width: size, height: size };
  if (!src || failed === src) {
    return <span className="team-logo team-monogram" style={{ ...box, fontSize: Math.max(9, Math.round(size / 4)) }} aria-hidden="true">{team.abbr}</span>;
  }
  return (
    <span className="team-logo" style={box}>
      <img src={src} alt={`${team.name} logo`} width={size} height={size} loading="lazy" decoding="async"
        onError={() => setFailed(src)} />
    </span>
  );
}
