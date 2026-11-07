import { Link, useLocation, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import type { NflCapability, SoccerMatch, SoccerMeta } from '../api/types';
import { useApi } from '../api/useApi';
import { useCarriedSearch, useCompetition } from '../components/Selection';
import { AsyncView, Empty } from '../components/States';
import { TeamLogo } from '../components/TeamLogo';
import { EmptyForecastState, Icon, MetricCard, PageTitle } from '../components/ui';
import { calendarDate, dateTime, FULL_DATE, tipoffTime } from '../format';
import { GameDateControl } from './NflPages';

// European soccer is data-only (backend app/soccer.py): fixtures, results and when each was learned, one competition at a
// time through /api/soccer/*. Nothing here shows or derives a forecast, odds, injury or metric. Results keep soccer's
// distinctions: a draw is a result; a match that went to extra time has no published 90-minute result; a shoot-out and the
// qualifier of a two-legged tie are reported as what they are, never as the winner of the match.

const seasonLabel = (season: number) => `${season}–${String(season + 1).slice(2)}`;
/** A real stage (League Phase, Round of 16 …); ESPN's domestic season names ('2026-27 LALIGA') are not stages. */
const stageOf = (m: SoccerMatch) =>
  (m.stage_label && m.stage_label !== m.competition && !/^\d{4}-\d{2}\s/.test(m.stage_label) ? m.stage_label : null);
const name = (m: SoccerMatch, side: 'home' | 'away' | null) => (side === 'home' ? m.home.name : side === 'away' ? m.away.name : null);

function sideText(m: SoccerMatch, side: SoccerMatch['regulation_result']) {
  if (side === 'draw') return 'Draw';
  return side ? `${name(m, side)} won` : null;
}

/** Short status for a card: kickoff time, or how a final match was decided. */
function statusLabel(m: SoccerMatch, date: string) {
  if (m.status === 'final') {
    if (m.decided_in === 'penalties') return m.home_shootout != null ? `AFTER EXTRA TIME · PENS ${m.home_shootout}–${m.away_shootout}` : 'AFTER EXTRA TIME · PENS';
    if (m.decided_in === 'extra_time') return 'AFTER EXTRA TIME';
    return m.regulation_result === 'draw' ? 'FULL TIME · DRAW' : 'FULL TIME';
  }
  if (m.status === 'scheduled') return tipoffTime(m.kickoff, date);
  if (m.status === 'in_progress') return 'IN PROGRESS';
  if (m.status === 'other') return `STATUS: ${m.source_status.replace(/^STATUS_/, '').replace(/_/g, ' ')}`;
  return m.status.toUpperCase();
}

function learned(m: SoccerMatch) {
  if (!m.result_available_at) return null;
  return m.result_availability_basis === 'assumed_tipoff_plus_4h'
    ? `Result imported later; counted as known 4 h after kickoff (${dateTime(m.result_available_at)})`
    : `Result first seen ${dateTime(m.result_available_at)}`;
}

/** Routes the soccer selection: Game day, a match, or the honest "not available" screens for everything else. */
export function SoccerScreens() {
  const { pathname } = useLocation();
  const [competition] = useCompetition();
  const meta = useApi((s) => api.soccerMeta(competition, s), [competition]);
  return (
    <AsyncView state={meta.state} retry={meta.retry}>
      {(m) => {
        if (pathname.startsWith('/games/')) return <SoccerMatchPage id={decodeURIComponent(pathname.slice('/games/'.length))} />;
        if (pathname === '/') return <SoccerGameDay meta={m} />;
        const page = pathname.startsWith('/performance') ? 'performance' : pathname.startsWith('/history') ? 'history'
          : pathname.startsWith('/daily-learning') ? 'research' : null;
        return page ? <SoccerUnavailable meta={m} page={page} />
          : <Empty>Page not found. <Link to="/?sport=soccer">Go to soccer matches</Link></Empty>;
      }}
    </AsyncView>
  );
}

function PredictionsUnavailable({ capabilities }: { capabilities: NflCapability[] }) {
  return (
    <details className="notice nfl-notice">
      <summary><strong>Soccer predictions are not available:</strong> fixtures and results only, with no forecasts, odds or injuries. <span className="link-btn">Why</span></summary>
      <ul className="nfl-capabilities small">
        {capabilities.map((c) => <li key={c.id}><strong>{c.label}:</strong> not available. <span className="muted">{c.reason}</span></li>)}
      </ul>
    </details>
  );
}

function SoccerGameDay({ meta }: { meta: SoccerMeta }) {
  const [params, setParams] = useSearchParams();
  const date = params.get('date') ?? meta.default_date;
  const setDate = (d: string | null) => {
    if (!d) return;
    const next = new URLSearchParams(params);
    next.set('date', d);
    setParams(next);
  };
  return (
    <section>
      <PageTitle icon="ball" plain title={`${meta.competition} matches`} sub="Fixtures and results from ESPN. Kickoff dates are US Eastern."
        right={date && <GameDateControl date={date} dates={meta.match_dates} defaultDate={meta.default_date} onChange={setDate} />} />
      <PredictionsUnavailable capabilities={meta.capabilities} />
      {date ? <SoccerSlate meta={meta} date={date} onChange={setDate} />
        : <Empty>No {meta.competition} matches are stored yet. Import days with <code>python -m app.cli soccer ingest --competition {meta.competition_id} --from YYYYMMDD --to YYYYMMDD</code> from <code>backend/</code>.</Empty>}
    </section>
  );
}

function SoccerSlate({ meta, date, onChange }: { meta: SoccerMeta; date: string; onChange: (d: string | null) => void }) {
  const { state, retry } = useApi((s) => api.soccerMatches(meta.competition_id, date, s), [meta.competition_id, date]);
  return (
    <AsyncView state={state} retry={retry}>
      {(d) => {
        const finals = d.matches.filter((m) => m.status === 'final');
        const draws = finals.filter((m) => m.end_of_play_result === 'draw').length;
        return (
          <>
            <div className="metrics today-metrics">
              <MetricCard icon="calendar" label="Matches on this date" tone="lime" value={d.matches.length}
                hint={d.matches.length ? `${finals.length} final, ${d.matches.length - finals.length} not final.` : d.fetched ? 'ESPN listed none.' : 'This day was not requested.'} />
              <MetricCard icon="scale" label="Draws" tone="dark" value={finals.length ? draws : '—'}
                hint={finals.length ? 'Level when play ended (a shoot-out does not change the score).' : 'No final result on this date.'} />
              <MetricCard icon="history" label="Last imported" tone="lavender" value={d.last_refresh_at ? dateTime(d.last_refresh_at) : '—'}
                hint={`Latest ${meta.competition} scoreboard request. Not refreshed automatically.`} />
            </div>
            {d.matches.length === 0 ? (
              <Empty>
                {d.fetched
                  ? <>ESPN listed no {meta.competition} matches on {calendarDate(date, FULL_DATE)}.</>
                  : <>{calendarDate(date, FULL_DATE)} has not been requested for {meta.competition}, so matches may exist that are not stored.</>}{' '}
                {d.prev_date && <button type="button" className="link-btn" onClick={() => onChange(d.prev_date)}>Previous match day ({calendarDate(d.prev_date)})</button>}
                {d.prev_date && d.next_date && ' · '}
                {d.next_date && <button type="button" className="link-btn" onClick={() => onChange(d.next_date)}>Next match day ({calendarDate(d.next_date)})</button>}
              </Empty>
            ) : <ul className="game-grid">{d.matches.map((m) => <SoccerCard key={m.match_id} match={m} date={date} />)}</ul>}
          </>
        );
      }}
    </AsyncView>
  );
}

function SoccerCard({ match: m, date }: { match: SoccerMatch; date: string }) {
  const carried = useCarriedSearch();
  // Bold only a side that led when play ended; after a shoot-out the card names the shoot-out and the tie separately.
  const led = (side: 'home' | 'away') => m.decided_in !== 'penalties' && m.end_of_play_result === side;
  const tags = [stageOf(m), m.leg ? `Leg ${m.leg}` : null].filter(Boolean);
  return (
    <li className="card game-card">
      <Link to={`/games/${encodeURIComponent(m.match_id)}${carried}`} className="card-link">
        <div className="game-card-head">
          <span className="game-time">{statusLabel(m, date)}{tags.map((t) => ` · ${t}`).join('')}</span>
        </div>
        {(['home', 'away'] as const).map((side) => (
          <div key={side} className={`team-line${led(side) ? ' is-winner' : ''}`}>
            <TeamLogo team={m[side]} sport="soccer" size={44} />
            <div><strong>{m[side].name}</strong><span>{side === 'home' ? 'Home' : 'Away'}</span></div>
            <b>{m.status === 'final' ? (side === 'home' ? m.home_score : m.away_score) : null}</b>
          </div>
        ))}
        {m.status === 'final' && m.qualified_team_id && (
          <p className="small muted">{m.qualified_team_id === m.home.team_id ? m.home.name : m.away.name} went through the tie
            {m.aggregate_home != null ? ` (aggregate ${m.aggregate_home}–${m.aggregate_away})` : ''}.</p>
        )}
        <p className="forecast-note">No soccer forecast: no validated model exists.</p>
        <div className="card-bottom small muted">
          <span>{learned(m) ?? (m.status === 'scheduled' ? `Kickoff ${dateTime(m.kickoff)}`
            : m.status === 'in_progress' ? 'Live scores are not collected; the result follows the next import.'
            : `Last updated ${dateTime(m.updated_at)}`)}</span>
          <span className="matchup-link">Matchup <Icon name="arrow" size={14} /></span>
        </div>
      </Link>
    </li>
  );
}

function ResultFacts({ m }: { m: SoccerMatch }) {
  if (m.status !== 'final') {
    return <p className="muted">No result yet ({m.status === 'other' ? m.source_status : m.status.replace('_', ' ')}). Live scores are not collected.</p>;
  }
  const qualified = m.qualified_team_id ? (m.qualified_team_id === m.home.team_id ? m.home.name : m.away.name) : null;
  return (
    <dl className="facts">
      <dt>Score when play ended</dt>
      <dd>{m.home.name} {m.home_score}–{m.away_score} {m.away.name}{m.decided_in !== 'regulation' ? ' (after extra time)' : ''}</dd>
      <dt>90-minute result</dt>
      <dd>{m.regulation_result ? sideText(m, m.regulation_result)
        : <span className="muted">Not available: the match went to extra time and ESPN publishes no 90-minute score.</span>}</dd>
      <dt>Extra time</dt><dd>{m.decided_in === 'regulation' ? 'Not played' : 'Played'}</dd>
      <dt>Penalty shoot-out</dt>
      <dd>{m.decided_in !== 'penalties' ? 'None'
        : m.home_shootout != null ? `${m.home_shootout}–${m.away_shootout}, ${name(m, m.shootout_winner)} won the shoot-out`
        : <span className="muted">A shoot-out was held; the source did not report its score.</span>}</dd>
      {(m.leg != null || m.aggregate_home != null) && <>
        <dt>Two-legged tie</dt>
        <dd>{m.leg != null ? `Leg ${m.leg}` : 'Leg not reported'}
          {m.aggregate_home != null ? ` · aggregate ${m.home.name} ${m.aggregate_home}–${m.aggregate_away} ${m.away.name} (as reported)` : ''}</dd>
        <dt>Went through</dt>
        <dd>{qualified ? <>{qualified} <span className="muted">(the tie, not this match)</span></> : <span className="muted">Not decided by this match, or not reported.</span>}</dd>
      </>}
      {m.note && <><dt>Source note</dt><dd>{m.note}</dd></>}
      <dt>Result known</dt><dd>{learned(m)}</dd>
    </dl>
  );
}

function SoccerMatchPage({ id }: { id: string }) {
  const carried = useCarriedSearch();
  const { state, retry } = useApi((s) => api.soccerMatch(id, s), [id]);
  return (
    <section>
      <Link to={`/${carried}`} className="back">‹ Soccer matches</Link>
      <AsyncView state={state} retry={retry}>
        {(d) => {
          const m = d.match;
          return (
            <>
              <section className="hero" aria-label="Match">
                <TeamLogo team={m.home} sport="soccer" size={104} />
                <div className="hero-center">
                  <span className="league-chip">{m.competition}</span>
                  <h2 className="hero-matchup">{m.home.name} <span>v</span> {m.away.name}</h2>
                  <p className="hero-time">{m.status === 'final'
                    ? <>{m.home_score}–{m.away_score}{m.decided_in === 'extra_time' ? ' after extra time' : m.decided_in === 'penalties'
                      ? ` after extra time${m.home_shootout != null ? `, ${m.home_shootout}–${m.away_shootout} on penalties` : ', decided on penalties'}` : m.regulation_result === 'draw' ? ' · draw' : ''}</>
                    : <>Kickoff: {dateTime(m.kickoff)}{m.status !== 'scheduled' && ` · ${m.status.replace('_', ' ')}`}</>}</p>
                </div>
                <TeamLogo team={m.away} sport="soccer" size={104} />
              </section>
              <div className="nfl-detail-grid">
                <div className="card">
                  <h2>Result</h2>
                  <ResultFacts m={m} />
                  {d.result_revisions.length > 1 && (
                    <p className="small muted">The source corrected this result {d.result_revisions.length - 1} time(s); every version is kept:{' '}
                      {d.result_revisions.map((r) => `${r.home_score}–${r.away_score}${r.home_shootout != null ? ` (pens ${r.home_shootout}–${r.away_shootout})` : ''} seen ${dateTime(r.observed_at)}`).join('; ')}.</p>
                  )}
                </div>
                <div className="card">
                  <h2>Match facts</h2>
                  <dl className="facts">
                    <dt>Competition</dt><dd>{m.competition}{stageOf(m) ? ` · ${stageOf(m)}` : ''}</dd>
                    <dt>Season</dt><dd>{seasonLabel(m.season)}</dd>
                    <dt>Kickoff</dt><dd>{dateTime(m.kickoff)}</dd>
                    <dt>Venue</dt><dd>{m.venue ?? <span className="muted">Not listed</span>}</dd>
                    <dt>Neutral site</dt><dd>{m.neutral_site == null ? <span className="muted">Not reported by the source</span> : m.neutral_site ? 'Yes' : 'No'}</dd>
                    <dt>Source status</dt><dd><code>{m.source_status}</code></dd>
                    <dt>First listed</dt><dd>{dateTime(m.first_seen_at)}</dd>
                    <dt>Last updated</dt><dd>{dateTime(m.updated_at)}</dd>
                  </dl>
                </div>
              </div>
              <div className="card">
                <h2>Forecast</h2>
                <EmptyForecastState>Not available. A soccer forecast needs three outcomes (home win, draw, away win) and a separately validated model. None exists, so no probability, pick, odds or accuracy is shown.</EmptyForecastState>
                <ul className="nfl-capabilities small">
                  {d.capabilities.filter((c) => ['forecasts', 'odds', 'injuries'].includes(c.id)).map((c) =>
                    <li key={c.id}><strong>{c.label}:</strong> not available. <span className="muted">{c.reason}</span></li>)}
                </ul>
              </div>
              <div className="card">
                <h2>Earlier meetings in the {m.competition}</h2>
                <p className="small muted">Stored final results between these teams in this competition before this kickoff, newest first.</p>
                <ResultTable matches={d.head_to_head} empty="No earlier meeting in this competition is stored." />
              </div>
              <div className="nfl-detail-grid">
                {([[m.home, d.home_recent], [m.away, d.away_recent]] as const).map(([team, rows]) => (
                  <div className="card" key={team.team_id}>
                    <h2><span className="team-cell"><TeamLogo team={team} sport="soccer" size={28} />{team.name}: last results</span></h2>
                    <p className="small muted">{m.competition} only, before this kickoff (up to five stored).</p>
                    <ResultTable matches={rows} team={team.team_id} empty="No earlier result in this competition is stored." />
                  </div>
                ))}
              </div>
            </>
          );
        }}
      </AsyncView>
    </section>
  );
}

/** Final results, home team first; with `team`, W/D/L for that team on the score when play ended (shoot-outs shown apart). */
function ResultTable({ matches, team, empty }: { matches: SoccerMatch[]; team?: string; empty: string }) {
  const carried = useCarriedSearch();
  if (!matches.length) return <p className="muted">{empty}</p>;
  const outcome = (m: SoccerMatch) => {
    if (!team || !m.end_of_play_result) return null;
    if (m.end_of_play_result === 'draw') return <span className="muted">D</span>;
    return (m.end_of_play_result === 'home') === (m.home.team_id === team) ? <span className="ok">W</span> : <span className="bad">L</span>;
  };
  return (
    <div className="table-wrap"><table className="table compare">
      <thead><tr><th>Date</th><th>Match</th><th>Score (home–away)</th>{team && <th>Result</th>}</tr></thead>
      <tbody>
        {matches.map((m) => (
          <tr key={m.match_id}>
            <td>{new Date(m.kickoff).toLocaleDateString([], FULL_DATE)}</td>
            <td><Link to={`/games/${encodeURIComponent(m.match_id)}${carried}`}>{m.home.abbr} v {m.away.abbr}</Link>
              {stageOf(m) && <span className="muted small"> · {stageOf(m)}</span>}</td>
            <td>{m.home_score}–{m.away_score}{m.decided_in === 'extra_time' ? ' aet' : m.decided_in === 'penalties'
              ? ` aet${m.home_shootout != null ? `, pens ${m.home_shootout}–${m.away_shootout}` : ', pens'}` : ''}</td>
            {team && <td>{outcome(m)}</td>}
          </tr>
        ))}
      </tbody>
    </table></div>
  );
}

const PAGES = {
  performance: { icon: 'performance', title: 'Soccer Performance', capability: 'performance' },
  history: { icon: 'history', title: 'Soccer Prediction History', capability: 'history' },
  research: { icon: 'learning', title: 'Soccer Daily Learning', capability: 'research' },
} as const;

/** Performance, history and daily learning: soccer predictions do not exist, so say so plainly and show what is stored. */
function SoccerUnavailable({ meta, page }: { meta: SoccerMeta; page: keyof typeof PAGES }) {
  const carried = useCarriedSearch();
  const p = PAGES[page];
  const cap = meta.capabilities.find((c) => c.id === p.capability);
  const forecasts = meta.capabilities.find((c) => c.id === 'forecasts');
  return (
    <section>
      <PageTitle icon={p.icon} title={p.title} sub="Not available: soccer predictions are not ready." />
      <Empty>
        <strong>{cap?.label ?? p.title}: not available.</strong> {cap?.reason} {forecasts?.reason}{' '}
        <Link to={`/${carried}`}>See {meta.competition} fixtures and results</Link>
      </Empty>
      <div className="metrics today-metrics">
        <MetricCard icon="layers" label={`${meta.competition} matches stored`} tone="lime" value={meta.matches_stored.toLocaleString()}
          hint={meta.seasons.length ? `Seasons ${meta.seasons.map(seasonLabel).join(', ')}.` : 'No season stored yet.'} />
        <MetricCard icon="calendar" label="Days requested" tone="dark" value={meta.fetched_dates.length}
          hint="Only requested days are stored; this is not full-season coverage." />
        <MetricCard icon="history" label="Last imported" tone="lavender" value={meta.last_refresh_at ? dateTime(meta.last_refresh_at) : '—'}
          hint="Latest scoreboard request for this competition." />
      </div>
    </section>
  );
}
