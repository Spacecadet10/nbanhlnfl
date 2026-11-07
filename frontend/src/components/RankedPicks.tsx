import { useState } from 'react';
import { api } from '../api/client';
import type { RankedPick, RankedPickEvidence, Sport } from '../api/types';
import { useApi } from '../api/useApi';
import { useDataRevision } from '../api/freshness';
import { pct, tipoffTime } from '../format';
import { AsyncView } from './States';
import { NoSkillNote } from './Skill';
import { ForecastPercentageRecord } from './ThresholdOverview';

const TOP = 10;
const GRADE: Record<string, { text: string; cls: string }> = {
  win: { text: 'Won', cls: 'ok' }, loss: { text: 'Lost', cls: 'warn' }, push: { text: 'Push', cls: 'muted' },
  pending: { text: 'Pending', cls: 'muted' }, void: { text: 'Void', cls: 'muted' }, unscorable: { text: 'No result', cls: 'muted' },
  unresolved: { text: 'Unresolved', cls: 'muted' },
};

const band = (e: RankedPickEvidence) => (e.hi == null ? `${pct(e.lo)}+` : `${pct(e.lo)}–${pct(e.hi)}`);
const source = (e: RankedPickEvidence) => (e.source === 'history' ? 'past seasons' : 'this season');

function Evidence({ e }: { e: RankedPickEvidence | null }) {
  if (!e) return <span className="muted">not enough past picks</span>;
  return <>
    won <strong>{pct(e.win_rate, 1)}</strong> of {e.settled}
    <br /><span className="small muted">{band(e)} picks, {source(e)} · at least {pct(e.lower, 1)}</span>
  </>;
}

function Result({ grade }: { grade: string | null }) {
  const g = grade ? GRADE[grade] : undefined;
  return g ? <span className={`small ${g.cls}`}>{g.text}</span> : <span className="muted">—</span>;
}

function Market({ p }: { p: RankedPick }) {
  if (p.market_prob == null) return <span className="muted small">no quote</span>;
  const diff = p.probability - p.market_prob;
  return <>{pct(p.market_prob, 1)}<br /><span className="small muted">{Math.abs(diff) < 0.005 ? 'same as model' : diff > 0 ? 'model higher' : 'model lower'}</span></>;
}

function RankTable({ picks, date }: { picks: RankedPick[]; date: string }) {
  return (
    <div className="table-wrap"><table className="table ranked-table">
      <thead><tr><th>#</th><th>Game</th><th>Pick</th><th>Model / exact-percentage record</th><th>Ranking threshold</th><th>Broader ranking history</th><th>Market</th><th>Result</th></tr></thead>
      <tbody>{picks.map((p) => (
        <tr key={`${p.game_id}:${p.market}`}>
          <th scope="row">{p.day_rank ?? '—'}</th>
          <td>{p.matchup}<br /><span className="small muted">{tipoffTime(p.tipoff, date)}{p.game_rank != null && ` · #${p.game_rank} of ${p.game_ranked} in this game`}</span></td>
          <td><strong>{p.pick_label}</strong><br /><span className="small muted">{p.market_title}</span>
            {!p.skill_proven && <><br /><NoSkillNote market={p.market} compact /></>}</td>
          <td>{pct(p.probability, 1)}<br /><ForecastPercentageRecord history={p.percentage_history} grade={p.grade} opposite={p.opposite_pick_label} startAt={p.tipoff} /></td>
          <td>{p.threshold == null ? <span className="muted">below grid</span> : <span className="badge badge-threshold">{pct(p.threshold)}+</span>}</td>
          <td><Evidence e={p.evidence} /></td>
          <td><Market p={p} /></td>
          <td><Result grade={p.grade} /></td>
        </tr>
      ))}</tbody>
    </table></div>
  );
}

/** One game's picks, best first: #1, #2… within the game, each with its threshold, evidence, market and day rank. */
function GamePickList({ picks }: { picks: RankedPick[] }) {
  return (
    <ol className="ranked-list">{picks.map((p) => (
        <li key={p.market} value={p.game_rank ?? undefined} className={p.game_rank == null ? 'muted' : undefined}>
          <strong>{p.game_rank != null ? `#${p.game_rank}` : 'Not ranked'}</strong>{' '}
          {p.pick_label} <span className="small muted">({p.market_title})</span>{' · '}
          model {pct(p.probability, 1)}{p.threshold != null && <> · <span className="badge badge-threshold">{pct(p.threshold)}+</span></>}
          {' · '}{p.evidence ? <>broader ranking history: {band(p.evidence)} picks ({source(p.evidence)}) won {pct(p.evidence.win_rate, 1)} of {p.evidence.settled}</> : 'not enough ranking history'}
          {p.market_prob != null && <> · market {pct(p.market_prob, 1)}</>}
          {' '}<Result grade={p.grade} />
          {p.day_rank != null && <span className="small muted"> · #{p.day_rank} of the day</span>}
          <br /><ForecastPercentageRecord history={p.percentage_history} grade={p.grade} opposite={p.opposite_pick_label} startAt={p.tipoff} />
        </li>
    ))}</ol>
  );
}

function ByGame({ picks, date }: { picks: RankedPick[]; date: string }) {
  const games: { id: string; matchup: string; tipoff: string; picks: RankedPick[] }[] = [];
  for (const p of picks) {     // picks arrive best first, so each game appears in the order of its best pick
    let g = games.find((x) => x.id === p.game_id);
    if (!g) games.push(g = { id: p.game_id, matchup: p.matchup, tipoff: p.tipoff, picks: [] });
    g.picks.push(p);
  }
  return <div className="ranked-games">{games.map((g) => (
    <div key={g.id} className="ranked-game">
      <h3>{g.matchup} <span className="small muted">{tipoffTime(g.tipoff, date)}</span></h3>
      <GamePickList picks={g.picks} />
    </div>
  ))}</div>;
}

/** A pick in plain words: the pick, its market, the threshold it reaches and how often picks like it have won. */
function Simple({ p, date, game }: { p: RankedPick; date?: string; game?: boolean }) {
  return <>
    <strong className="simple-pick">{p.pick_label}</strong>
    <span className="small muted">
      {' '}{game && date && <>{p.matchup}, {tipoffTime(p.tipoff, date)} · </>}{p.market_title}
      {' · '}model {pct(p.probability, 1)}
    </span>
    {' '}<Result grade={p.grade === 'pending' ? null : p.grade} />
    <br /><ForecastPercentageRecord history={p.percentage_history} grade={p.grade} opposite={p.opposite_pick_label} startAt={p.tipoff} />
  </>;
}

/** The Today card: the date's best picks in order, in plain words; every pick and the numbers sit behind "See every pick". */
export function RankedPicksCard({ sport, date }: { sport: Sport; date: string }) {
  const rev = useDataRevision();
  const { state, retry } = useApi((s) => api.canonicalRanked(sport, date, s), [sport, date], rev);
  const [view, setView] = useState<'day' | 'game'>('day');
  const [all, setAll] = useState(false);
  return (
    <div className={`card ranked-picks${state.status === 'success' && state.data.picks.length === 0 ? ' is-empty' : ''}`}>
      <h2>Best picks</h2>
      <AsyncView state={state} retry={retry}>{(d) => {
        const strong = d.picks.filter((p) => p.strong);
        const ranked = d.picks.filter((p) => p.day_rank != null);
        const unranked = d.picks.filter((p) => p.day_rank == null);
        const waiting = Object.values(d.without_forecast).reduce((a, b) => a + b, 0);
        if (d.picks.length === 0) {
          return <p className="muted small">No picks yet for this date. Picks appear about two hours before each game starts.</p>;
        }
        const shown = all ? strong : strong.slice(0, TOP);
        return <>
          {strong.length === 0
            ? <p className="muted">No strong picks on this date: no pick here is of a kind that has won more often than not.</p>
            : <ol className="best-list">{shown.map((p) => <li key={`${p.game_id}:${p.market}`}><Simple p={p} date={d.date} game /></li>)}</ol>}
          {strong.length > TOP && <button type="button" className="link-btn" onClick={() => setAll(!all)}>
            {all ? `Show the top ${TOP}` : `Show all ${strong.length} strong picks`}</button>}
          {waiting > 0 && <p className="small muted">{waiting} more picks appear closer to their games (about two hours before the start).</p>}
          <p className="small muted">Best first: picks like #1 have won most often in the past. Not betting advice.</p>
          <details className="ranked-more">
            <summary className="small">See every pick and the numbers</summary>
            <div className="segmented" role="group" aria-label="Ranking view">
              <button type="button" aria-pressed={view === 'day'} onClick={() => setView('day')}>Whole day</button>
              <button type="button" aria-pressed={view === 'game'} onClick={() => setView('game')}>By game</button>
            </div>
            {view === 'day' ? <>
              <RankTable picks={ranked} date={d.date} />
              {unranked.length > 0 && <details><summary className="small">{unranked.length} not ranked (fewer than {d.min_settled} past picks like them)</summary>
                <RankTable picks={unranked} date={d.date} /></details>}
            </> : <ByGame picks={d.picks} date={d.date} />}
            <p className="small muted">A high rank means picks like it have won often, not that it is good value: favourites are priced as
              favourites, so compare with the market column. {d.rule}</p>
          </details>
        </>;
      }}</AsyncView>
    </div>
  );
}

/** The game's ranked picks (same ranking as the Today card); null while loading or outside real mode. */
export function useGamePicks(sport: Sport, date: string, gameId: string, enabled = true): { picks: RankedPick[] | null; error: boolean } {
  const rev = useDataRevision();
  const { state } = useApi((s) => (enabled ? api.canonicalRanked(sport, date, s) : Promise.resolve(null)), [sport, date, enabled], rev);
  if (state.status !== 'success' || !state.data) return { picks: null, error: state.status === 'error' };
  return { picks: state.data.picks.filter((p) => p.game_id === gameId), error: false };
}

/** The game page: one clear best pick, then the others in order; the numbers sit behind "Why this order?". `compact`: the
 * ranked list only (the best pick is already shown in the card grid above it). */
export function GameRankedPicks({ sport, date, gameId, compact = false }: { sport: Sport; date: string; gameId: string; compact?: boolean }) {
  const rev = useDataRevision();
  const { state, retry } = useApi((s) => api.canonicalRanked(sport, date, s), [sport, date], rev);
  if (compact) {
    return (
      <AsyncView state={state} retry={retry}>{(d) => {
        const picks = d.picks.filter((p) => p.game_id === gameId);
        if (picks.length === 0) return null;
        const strong = picks.filter((p) => p.strong);
        return (
          <div className="card ranked-picks">
            <h2>Picks in this game, ranked</h2>
            {strong.length > 0
              ? <ol className="best-list">{strong.map((p) => <li key={p.market}><Simple p={p} /></li>)}</ol>
              : <p className="muted">No strong pick: none of this game's picks is of a kind that has won more often than not.</p>}
            <details className="ranked-more">
              <summary className="small">Why this order? Every pick in this game with the numbers</summary>
              <GamePickList picks={picks} />
              <p className="small muted">Ranked by how often picks like each one have won (same market, same probability band). A best pick is
                one whose kind has won more often than not. Not value: compare with the market's probability. Not betting advice.</p>
            </details>
          </div>
        );
      }}</AsyncView>
    );
  }
  return (
    <div className="card ranked-picks best-card">
      <AsyncView state={state} retry={retry}>{(d) => {
        const picks = d.picks.filter((p) => p.game_id === gameId);
        if (picks.length === 0) {
          return <><p className="best-label">Best pick</p><p className="best-empty">No pick yet</p>
            <p className="muted">Picks appear about two hours before the game starts.</p></>;
        }
        const strong = picks.filter((p) => p.strong);
        const [best, ...rest] = strong;
        return <>
          {best
            ? <>
                <p className="best-label">Best pick in this game</p>
                <p className="best-pick">{best.pick_label} <Result grade={best.grade === 'pending' ? null : best.grade} /></p>
                <p className="small muted">{best.market_title}
                  {' · '}model {pct(best.probability, 1)}
                  {best.day_rank != null && <> · #{best.day_rank} of the day</>}<br />
                  <ForecastPercentageRecord history={best.percentage_history} grade={best.grade} opposite={best.opposite_pick_label} startAt={best.tipoff} /></p>
                {rest.length > 0 && <>
                  <p className="best-label">Next best</p>
                  <ol className="best-list" start={2}>{rest.map((p) => <li key={p.market}><Simple p={p} /></li>)}</ol>
                </>}
              </>
            : <><p className="best-label">Best pick</p><p className="best-empty">No strong pick</p>
                <p className="muted">None of this game's picks is of a kind that has won more often than not.</p></>}
          <details className="ranked-more">
            <summary className="small">Why this order? Every pick in this game with the numbers</summary>
            <GamePickList picks={picks} />
            <p className="small muted">Ranked by how often picks like each one have won (same market, same probability band). A best pick is
              one whose kind has won more often than not. Not value: compare with the market's probability. Not betting advice.</p>
          </details>
        </>;
      }}</AsyncView>
    </div>
  );
}
