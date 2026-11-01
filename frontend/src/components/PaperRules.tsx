import { api } from '../api/client';
import type { PaperRule, Sport } from '../api/types';
import { useApi } from '../api/useApi';
import { calendarDate, pct } from '../format';
import { AsyncView } from './States';

const STATE: Record<PaperRule['state'], { text: string; cls: string }> = {
  interim: { text: 'In progress — not a verdict yet', cls: 'muted' },
  pass: { text: 'Passed', cls: 'ok' },
  fail: { text: 'Failed', cls: 'warn' },
  inconclusive: { text: 'Inconclusive (too few bets)', cls: 'muted' },
};

const signed = (x: number | null, digits = 1) => (x == null ? '—' : `${x > 0 ? '+' : ''}${(x * 100).toFixed(digits)}%`);

/** The sport's frozen paper rules: fixed before the games they count, no money, judged once at the end of the season. */
export function PaperRulesCard({ sport }: { sport: Sport }) {
  const { state, retry } = useApi((s) => api.paperRules(sport, s), [sport]);
  return (
    <div className="card">
      <h2>Paper rules (no money)</h2>
      <AsyncView state={state} retry={retry}>{(d) => d.rules.length === 0
        ? <p className="small muted">No paper rule for this sport.</p>
        : <>
          <p className="small muted">Fixed on {calendarDate(d.frozen_at.slice(0, 10))} before any game they count, and never changed this season.
            Each rule bets 1 unit at the bookmaker's price the forecast could see. It is judged once, after the regular season: it passes only
            with at least {d.min_bets} settled bets and a return that is above zero beyond luck.</p>
          {d.rules.map((r) => {
            const s = STATE[r.state];
            return <div key={r.id} className="paper-rule">
              <h3>{r.description}</h3>
              <p className={s.cls}><strong>{s.text}</strong></p>
              <p className="small">
                {r.bets} {r.bets === 1 ? 'bet' : 'bets'} · {r.wins}–{r.losses}{r.pushes > 0 ? `–${r.pushes}` : ''} settled{r.pending > 0 && ` · ${r.pending} pending`}
                {' '}· {r.units >= 0 ? '+' : ''}{r.units.toFixed(2)} units
                {r.return_per_bet != null && <> · {signed(r.return_per_bet)} per bet{r.ci95 && <span className="muted"> (95%: {signed(r.ci95[0])} to {signed(r.ci95[1])})</span>}</>}
              </p>
              {r.settled < d.min_bets && <p className="small muted">{d.min_bets - r.settled} more settled bets needed before a verdict is possible.</p>}
              <p className="small muted">Threshold {pct(r.min_confidence)} · price: {r.price}.</p>
            </div>;
          })}
          <p className="small muted">Rule file sha256 {d.sha256}… · {d.note}</p>
        </>}</AsyncView>
    </div>
  );
}
