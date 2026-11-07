import { useState } from 'react';
import type { League, Team } from '../api/types';
import { useLeague } from './Selection';

// Team logos come from the same free providers as the data: ESPN's logo CDN for the NBA (ESPN abbreviations, as stored)
// and the NHL's own asset server for the NHL (NHL abbreviations, as stored). Only franchise teams are mapped; anything
// else (all-star, international and exhibition sides) shows the abbreviation monogram, as does any logo that fails to load.
const NBA = new Set(['ATL', 'BKN', 'BOS', 'CHA', 'CHI', 'CLE', 'DAL', 'DEN', 'DET', 'GS', 'HOU', 'IND', 'LAC', 'LAL', 'MEM',
  'MIA', 'MIL', 'MIN', 'NO', 'NY', 'OKC', 'ORL', 'PHI', 'PHX', 'POR', 'SA', 'SAC', 'TOR', 'UTAH', 'WSH']);
const NBA_ALIASES: Record<string, string> = { GSW: 'GS', NYK: 'NY', NOP: 'NO', SAS: 'SA', UTA: 'UTAH', WAS: 'WSH', BRK: 'BKN', PHO: 'PHX' };
const NHL = new Set(['ANA', 'BOS', 'BUF', 'CAR', 'CBJ', 'CGY', 'CHI', 'COL', 'DAL', 'DET', 'EDM', 'FLA', 'LAK', 'MIN', 'MTL',
  'NJD', 'NSH', 'NYI', 'NYR', 'OTT', 'PHI', 'PIT', 'SEA', 'SJS', 'STL', 'TBL', 'TOR', 'UTA', 'VAN', 'VGK', 'WPG', 'WSH']);

// NFL: ESPN's logo CDN with the stored ESPN abbreviations (checked 2026-11-01). Pro Bowl sides (AFC/NFC, IRV/RIC) get the monogram.
const NFL = new Set(['ARI', 'ATL', 'BAL', 'BUF', 'CAR', 'CHI', 'CIN', 'CLE', 'DAL', 'DEN', 'DET', 'GB', 'HOU', 'IND', 'JAX', 'KC',
  'LAC', 'LAR', 'LV', 'MIA', 'MIN', 'NE', 'NO', 'NYG', 'NYJ', 'PHI', 'PIT', 'SEA', 'SF', 'TB', 'TEN', 'WSH']);

// Soccer: ESPN's logo CDN by ESPN team id (abbreviations repeat across European clubs: Celtic and Celta are both CEL).
export function logoUrl(sport: League, abbr: string, teamId?: string): string | null {
  if (sport === 'soccer') {
    const id = teamId?.startsWith('soccer:') ? teamId.slice('soccer:'.length) : null;
    return id && /^\d+$/.test(id) ? `https://a.espncdn.com/combiner/i?img=/i/teamlogos/soccer/500/${id}.png&h=96&w=96` : null;
  }
  if (sport === 'nba') abbr = NBA_ALIASES[abbr] ?? abbr;
  if (sport === 'nba' && NBA.has(abbr)) {
    return `https://a.espncdn.com/combiner/i?img=/i/teamlogos/nba/500/${abbr.toLowerCase()}.png&h=96&w=96`;
  }
  if (sport === 'nhl' && NHL.has(abbr)) return `https://assets.nhle.com/logos/nhl/svg/${abbr}_light.svg`;
  if (sport === 'nfl' && NFL.has(abbr)) {
    return `https://a.espncdn.com/combiner/i?img=/i/teamlogos/nfl/500/${abbr.toLowerCase()}.png&h=96&w=96`;
  }
  return null;
}

/** A team's logo in a fixed square (no layout shift), lazily loaded; the abbreviation monogram when there is none. */
export function TeamLogo({ team, sport, size = 40 }: { team: Team & { team_id?: string }; sport?: League; size?: number }) {
  const [current] = useLeague();
  const [failed, setFailed] = useState<string | null>(null);
  const src = logoUrl(sport ?? current, team.abbr, team.team_id);
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
