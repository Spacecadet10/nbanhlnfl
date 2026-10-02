import type { GameSummary } from '../api/types';
import { dateTime, pct } from '../format';

const names: Record<string, string> = { elo: 'Elo', logistic: 'Logistic', logistic_v2: 'Efficiency logistic',
  efficiency_margin: 'Efficiency', gbt_v1: 'Boosted trees', hockey_elo: 'Elo', goals_poisson: 'Goals model',
  availability_two_hour: 'Injury-aware logistic', hockey_logistic: 'Logistic', hockey_gbt: 'Boosted trees' };

export function ModelAgreement({ game: g, detail = false }: { game: GameSummary; detail?: boolean }) {
  const a = g.agreement;
  const side = a?.pick === 'home' ? g.home.abbr : g.away.abbr;
  return <section className="model-agreement" aria-label="Research model agreement">
    <p className="small"><strong>Winner agreement</strong> <span className="muted">· Research · {a?.policy === 'pregame' ? 'Night-before archive' : 'Within 2 hours'}</span></p>
    {!a || !a.available ? <p className="small muted">Not recorded for this game.</p> : <>
      <p><strong>{a.pick ? `${a.agreeing} of ${a.available} pick ${side}` : 'Models are split'}</strong>
        {a.status === 'partial' && <span className="warn"> · {a.available}/{a.expected} models available</span>}</p>
      {a.abstentions > 0 && <p className="small muted">{a.abstentions} model(s) at exactly 50% abstain.</p>}
      {detail && <>
        <p className="small muted">{a.generated_at && dateTime(a.generated_at)}. Agreement is a vote count, not a win probability or a replacement for the primary pick.</p>
        <div className="table-wrap"><table className="table"><thead><tr><th>Model</th><th>Pick</th><th>Confidence</th></tr></thead>
          <tbody>{a.models.map(m => <tr key={m.model_version}><th scope="row">{names[m.name] ?? m.name}</th>
            <td>{m.home_win_prob === .5 ? 'No preference' : m.home_win_prob > .5 ? g.home.abbr : g.away.abbr}</td>
            <td>{pct(Math.max(m.home_win_prob, 1 - m.home_win_prob), 1)}</td></tr>)}</tbody></table></div>
        {a.missing.length > 0 && <p className="small warn">Missing: {a.missing.map(n => names[n] ?? n).join(', ')}. Older predictions are not used to fill gaps.</p>}
      </>}
    </>}
  </section>;
}
