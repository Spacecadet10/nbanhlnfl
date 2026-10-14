import { useState } from 'react';
import { api } from '../api/client';
import type { MarketId, MemberOverlap, Sport } from '../api/types';
import { useApi } from '../api/useApi';
import { pct } from '../format';
import { modelName } from './Research';
import { AsyncView, Empty } from './States';

const PAGE = 50;
const HIGH_OVERLAP = 0.9;   // pairs agreeing this often are flagged: their unanimity is not two independent votes

/** The pairwise earlier-period pick overlap of a rule's members (backend member_overlap). A pair that agrees on almost every
 *  game — one member built on the other's inputs, e.g. players_injuries_v1 extends logistic_v2 — is not two independent
 *  agreeing members, so the rule carries fewer independent votes than its member count suggests. */
export function MemberOverlapNote({ overlap, members, showNote }: { overlap: MemberOverlap | null | undefined; members: string[];
  showNote?: boolean }) {
  if (!overlap) return null;
  const inRule = new Set(members);
  const pairs = overlap.pairs.filter((p) => inRule.has(p.a) && inRule.has(p.b));
  if (pairs.length === 0) return null;
  const high = pairs.filter((p) => p.share != null && p.share >= HIGH_OVERLAP);
  return <p className={high.length ? 'warn small' : 'small muted'} role="note">
    Member pick overlap ({overlap.period} period): {pairs.map((p) => `${modelName(p.a)} & ${modelName(p.b)} agree on ${p.share == null
      ? '—' : pct(p.share, 0)} of ${p.games} games`).join('; ')}.
    {high.length > 0 && ` ${high.length === 1 ? 'That pair agrees' : `${high.length} pairs agree`} on at least ${pct(HIGH_OVERLAP, 0)} of their games (not independent evidence): this rule has fewer independent votes than members.`}
    {showNote && <> {overlap.note}</>}
  </p>;
}

/** Every qualifying game of one exact rule (GET /api/research/thresholds/ledger), with a CSV download of the same rows. */
export function RuleLedgerTable({ sport, market, source, rule, period }: { sport: Sport; market: MarketId; source: string; rule: string;
  period: 'earlier' | 'later' }) {
  const [shown, setShown] = useState(PAGE);
  const { state, retry } = useApi((s) => api.researchRuleLedger(sport, market, source, rule, period, s), [sport, market, source, rule, period]);
  return <>
    <h3>Qualifying games · {period} period</h3>
    <AsyncView state={state} retry={retry}>{(l) => !l.rows?.length ? <Empty>No qualifying games in this period.</Empty> : <>
      <MemberOverlapNote overlap={l.member_overlap} members={l.members} />
      <p className="small">{l.games} games · dates are US Eastern ·{' '}
        <a href={api.researchRuleLedgerCsvUrl(sport, market, source, rule, period)} download>Download CSV ({period})</a>{' · '}
        <a href={api.researchRuleLedgerCsvUrl(sport, market, source, rule, 'all')} download>Download CSV (both periods)</a></p>
      <div className="table-wrap"><table className="table small">
        <thead><tr><th>Date</th><th>Game</th>{market !== 'winner' && market !== 'reg3' && <th>Line</th>}
          {l.members.map((m) => <th key={m}>{modelName(m)}</th>)}<th>Shared pick</th><th>Score</th><th>Actual</th><th>Result</th>
          <th>Best single ({l.best_single ? modelName(l.best_single) : '—'})</th></tr></thead>
        <tbody>{l.rows.slice(0, shown).map((r) => <tr key={r.game_id}>
          <td>{r.date}</td><td>{r.away ?? '?'} @ {r.home ?? '?'}</td>{market !== 'winner' && market !== 'reg3' && <td>{r.line ?? '—'}</td>}
          {r.members.map((m) => <td key={m.model}>{m.pick} {pct(m.p, 1)}</td>)}
          <td>{r.shared_pick}</td><td>{r.away_score ?? '—'}–{r.home_score ?? '—'}</td><td>{r.actual ?? 'pending'}</td>
          <td className={r.result === 'win' ? 'ok' : r.result === 'loss' ? 'bad' : ''}>{r.result}</td>
          <td>{r.best_single_pick ? `${r.best_single_pick} ${pct(r.best_single_p, 1)} · ${r.best_single_result}` : 'no pick'}</td></tr>)}</tbody>
      </table></div>
      {l.rows.length > shown && <button type="button" className="link-btn" onClick={() => setShown(shown + PAGE * 4)}>Show more ({l.rows.length - shown} left)</button>}
    </>}</AsyncView>
  </>;
}
