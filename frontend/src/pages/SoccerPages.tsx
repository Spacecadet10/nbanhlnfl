import { Link, useLocation, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import type { NflCapability, SoccerLiveForecast, SoccerMatch, SoccerMeta } from '../api/types';
import { useApi } from '../api/useApi';
import { useCarriedSearch, useCompetition } from '../components/Selection';
import { AsyncView, Empty } from '../components/States';
import { gameRoute } from '../components/gameRoute';
import { TeamLogo } from '../components/TeamLogo';
import { EmptyForecastState, Icon, MetricCard, PageTitle } from '../components/ui';
import { calendarDate, dateTime, FULL_DATE, tipoffTime } from '../format';
import { GameDateControl } from './NflPages';
import { ResearchGameDay, ResearchHistory, ResearchMatchPage, ResearchPerformance } from './SoccerResearch';

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
    if (m.decided_in === 'unknown') return 'FINAL · ENDING NOT ESTABLISHED';
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

/** Routes the soccer selection. When the store holds research forecasts (openfootball history + backtest ledger), Game
 * day, Performance and History show those, labelled Research backtest; otherwise the ESPN fixture screens and the honest
 * "not available" screens. A history match id (of:…) always opens its research page. */
export function SoccerScreens() {
  const { pathname } = useLocation();
  const [competition] = useCompetition();
  const research = useApi((s) => api.soccerResearchMeta(competition, s), [competition]);
  let matchId: string | null = null;
  if (pathname.startsWith('/games/')) {
    try { matchId = decodeURIComponent(pathname.slice('/games/'.length)); } catch { matchId = ''; }   // malformed: not found
  }
  if (matchId?.startsWith('of:')) return <ResearchMatchPage id={matchId} />;
  if (research.state.status === 'loading') return <AsyncView state={research.state} retry={research.retry}>{() => null}</AsyncView>;
  const r = research.state.status === 'success' && research.state.data.runs.length ? research.state.data : null;
  if (r && !matchId) {
    if (pathname === '/') return <ResearchGameDay meta={r} />;
    if (pathname.startsWith('/performance')) return <ResearchPerformance meta={r} />;
    if (pathname.startsWith('/history')) return <ResearchHistory meta={r} />;
  }
  return <SoccerEspnScreens researchError={research.state.status === 'error' ? research.state.error : null} />;
}

function SoccerEspnScreens({ researchError }: { researchError: string | null }) {
  const { pathname } = useLocation();
  const [competition] = useCompetition();
  const meta = useApi((s) => api.soccerMeta(competition, s), [competition]);
  return (
    <>
    {researchError && <p className="notice small">Soccer research forecasts are not available here: {researchError}</p>}
    <AsyncView state={meta.state} retry={meta.retry}>
      {(m) => {
        const game = gameRoute(pathname);
        if (game.kind === 'game') return <SoccerMatchPage id={game.id} />;
        if (game.kind === 'malformed') return <Empty>This match link is damaged or incomplete. <Link to="/?sport=soccer">Go to soccer matches</Link></Empty>;
        if (pathname === '/') return <SoccerGameDay meta={m} />;
        const page = pathname.startsWith('/performance') ? 'performance' : pathname.startsWith('/history') ? 'history'
          : pathname.startsWith('/daily-learning') ? 'research' : null;
        return page ? <SoccerUnavailable meta={m} page={page} />
          : <Empty>Page not found. <Link to="/?sport=soccer">Go to soccer matches</Link></Empty>;
      }}
    </AsyncView>
    </>
  );
}

function PredictionsUnavailable({ capabilities }: { capabilities: NflCapability[] }) {
  const live = capabilities.find((c) => c.id === 'forecasts' && c.available);
  if (live) {
    return (
      <details className="notice nfl-notice">
        <summary><strong>Live soccer forecasts:</strong> the 90-minute home/draw/away result only. <span className="link-btn">What else is not available</span></summary>
        <ul className="nfl-capabilities small">
          {capabilities.map((c) => <li key={c.id}><strong>{c.label}:</strong> {c.available ? 'available. ' : 'not available. '}<span className="muted">{c.reason}</span></li>)}
        </ul>
      </details>
    );
  }
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
      <SoccerLivePanel competition={meta.competition_id} name={meta.competition} />
      {date ? <SoccerSlate meta={meta} date={date} onChange={setDate} />
        : <Empty>No {meta.competition} matches are stored yet. A development store is filled from <code>backend/</code> with <code>NBA_DATA_DIR=&lt;scratch or development data directory&gt; .venv/bin/python -m app.cli soccer ingest --competition {meta.competition_id} --from YYYYMMDD --to YYYYMMDD</code> (never the live <code>data/</code> folder). That collects from ESPN, which the source review found not clearly permitted for this project: a user decision.</Empty>}
    </section>
  );
}

const OUTCOME_LABEL = { home: 'Home win', draw: 'Draw', away: 'Away win' } as const;
const pct = (p: number) => `${(p * 100).toFixed(1)}%`;

/** Live 90-minute forecasts of the sealed Elo (backend app/soccer_live.py): upcoming matches and those of the last 2 days.
 * Separate from the research backtest screens; shows the input check when a run was refused. */
function SoccerLivePanel({ competition, name }: { competition: SoccerMeta['competition_id']; name: string }) {
  const { state } = useApi((s) => api.soccerLive(competition, s), [competition]);
  if (state.status !== 'success') return null;            // no live store yet: the fixtures below still show
  const d = state.data;
  return (
    <section className="soccer-live" aria-label="Live soccer forecasts">
      <div className="soccer-live-head">
        <h2>Upcoming forecasts</h2>
        <span className="pill soccer-live-badge">{d.label}</span>
      </div>
      <p className="small muted">{d.settlement_rule} Model: {d.model.name}, frozen and tested once on held-out seasons; not a betting edge.</p>
      {!d.in_scope ? <Empty>{d.scope_note}</Empty> : (
        <>
          {d.check && !d.check.ok && (
            <div className="notice error-notice" role="alert">
              <strong>No new forecast: inputs refused at {dateTime(d.check.checked_at)}.</strong>
              <ul className="small">{d.check.problems.slice(0, 4).map((p) => <li key={p}>{p}</li>)}</ul>
            </div>
          )}
          {d.forecasts.length === 0
            ? <Empty>No live {name} forecast for the coming matches yet. Each match is forecast before 06:00 UTC on its date, once every earlier match is settled.</Empty>
            : <div className="soccer-live-list">{d.forecasts.map((f) => <SoccerLiveCard key={f.forecast_id} f={f} />)}</div>}
        </>
      )}
    </section>
  );
}

function SoccerLiveCard({ f }: { f: SoccerLiveForecast }) {
  const names = { home: `${f.home} win`, draw: 'Draw', away: `${f.away} win` } as const;
  return (
    <article className="soccer-live-card">
      <header><strong>{f.home} vs {f.away}</strong><span className="muted small">{dateTime(f.kickoff_at)}</span></header>
      <div className="soccer-live-probs">
        {(['home', 'draw', 'away'] as const).map((o) => (
          <div key={o} className={`soccer-live-prob${f.most_likely === o ? ' is-top' : ''}`}>
            <span className="small muted">{OUTCOME_LABEL[o]}</span>
            <strong>{pct(f.probabilities[o])}</strong>
            <span className="small">{names[o]}</span>
          </div>
        ))}
      </div>
      <p className="small muted">
        {f.result ? <>90-minute result: <strong>{names[f.result]}</strong> ({f.result === f.most_likely ? 'model\u2019s most likely outcome' : 'not the model\u2019s most likely outcome'}). </> : null}
        Forecast {dateTime(f.generated_at)} (cutoff {dateTime(f.cutoff_at)}); results through {f.inputs_through ? dateTime(f.inputs_through) : '—'}; ESPN data retrieved {dateTime(f.source_retrieved_at)}.
      </p>
    </article>
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
      <dd>{m.home.name} {m.home_score}–{m.away_score} {m.away.name}{m.decided_in === 'extra_time' || m.decided_in === 'penalties' ? ' (after extra time)' : ''}</dd>
      {m.decided_in === 'unknown' && <><dt>How it ended</dt>
        <dd><span className="muted">Not established: ESPN's fields disagree ({m.decision_note}). Nothing is guessed.</span></dd></>}
      <dt>90-minute result</dt>
      <dd>{m.regulation_result ? sideText(m, m.regulation_result)
        : m.decided_in === 'unknown'
        ? <span className="muted">Not available: it is not established whether the match went to extra time.</span>
        : <span className="muted">Not shown: the match went to extra time and ESPN's score fields include it. The 90-minute score can be derived from goal minutes; that derivation is not validated yet, so nothing is shown.</span>}</dd>
      <dt>Extra time</dt><dd>{m.decided_in === 'regulation' ? 'Not played' : m.decided_in === 'unknown' ? <span className="muted">Not established</span> : 'Played'}</dd>
      <dt>Penalty shoot-out</dt>
      <dd>{m.decided_in === 'unknown' ? <span className="muted">None reported by the source</span> : m.decided_in !== 'penalties' ? 'None'
        : m.home_shootout != null ? `${m.home_shootout}–${m.away_shootout}, ${name(m, m.shootout_winner)} won the shoot-out`
        : <span className="muted">A shoot-out was held; the source did not report its score.</span>}</dd>
      {(m.leg != null || m.aggregate_home != null) && <>
        <dt>Two-legged tie</dt>
        <dd>{m.leg != null ? `Leg ${m.leg}` : 'Leg not reported'}
          {m.aggregate_home != null ? ` · aggregate ${m.home.name} ${m.aggregate_home}–${m.aggregate_away} ${m.away.name} (as reported)` : ''}</dd>
        <dt>Went through</dt>
        <dd>{qualified ? <>{qualified} <span className="muted">(the tie, not this match)</span></> : <span className="muted">Not decided by this match, or not reported.</span>}</dd>
        {m.tie_facts_available_at && m.tie_facts_available_at !== m.result_available_at && <>
          <dt>Tie facts known</dt><dd>{dateTime(m.tie_facts_available_at)} <span className="muted">(later than the result)</span></dd></>}
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
                    ? <>{m.home_score}–{m.away_score}{m.decided_in === 'unknown' ? ' · ending not established' : m.decided_in === 'extra_time' ? ' after extra time' : m.decided_in === 'penalties'
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
                    <p className="small muted">{d.result_revisions.filter((r) => r.basis === 'observed').length > 1
                      ? `The source delivered ${d.result_revisions.filter((r) => r.basis === 'observed').length} versions of this result (a later version may only add tie facts such as the qualifier)`
                      : 'One version from the source'}{d.result_revisions.some((r) => r.basis === 'reclassified') ? ', re-read by a newer importer from the same retrieval' : ''}; every version is kept:{' '}
                      {d.result_revisions.map((r) => `${r.home_score}–${r.away_score}${r.decided_in === 'regulation' ? '' : r.decided_in === 'unknown' ? ' (ending unclear)' : ' aet'}`
                        + `${r.home_shootout != null ? `, pens ${r.home_shootout}–${r.away_shootout}` : ''}`
                        + `${r.aggregate_home != null ? `, agg ${r.aggregate_home}–${r.aggregate_away}` : ''}`
                        + `${r.qualified_team_id ? `, through: ${r.qualified_team_id === m.home.team_id ? m.home.name : m.away.name}` : ''}`
                        + (r.basis === 'reclassified' ? ' (re-read, same retrieval)' : ` (seen ${dateTime(r.observed_at)})`)).join('; ')}.</p>
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
            <td>{m.home_score}–{m.away_score}{m.decided_in === 'unknown' ? ' (ending unclear)' : m.decided_in === 'extra_time' ? ' aet' : m.decided_in === 'penalties'
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
