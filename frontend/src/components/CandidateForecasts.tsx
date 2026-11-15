import { api } from '../api/client';
import { useApi } from '../api/useApi';
import type { Sport } from '../api/types';
import { AsyncView } from './States';
import { dateTime, pct } from '../format';

const MARKET: Record<string, string> = { winner: 'Winner', reg3: 'Regulation three-way', spread: 'Spread / puck line', total: 'Total', team_total_home: 'Home team total', team_total_away: 'Away team total' };

/** Every candidate model's latest pregame forecast for one game, each model version on its own row. Research only. */
export function CandidateForecasts({ sport, gameId }: { sport: Sport; gameId: string }) {
  const { state, retry } = useApi(s => api.candidatesGame(sport, gameId, s), [sport, gameId]);
  return <details className="card"><summary><strong>Individual candidate models (research)</strong></summary>
    <AsyncView state={state} retry={retry}>{d => <>
      <p className="small muted">{d.mode === 'demo' ? 'DEMO · ' : ''}{d.note}</p>
      {d.forecasts.length === 0 ? <p>No candidate model recorded a pregame forecast for this game.</p> :
        <div className="table-wrap"><table className="table"><thead><tr><th>Market</th><th>Model</th><th>Forecast</th><th>Line (source)</th><th>Made / fit data through</th></tr></thead><tbody>
          {d.forecasts.map(f => <tr key={`${f.market}-${f.model_version}`}><td>{MARKET[f.market] ?? f.market}</td>
            <td>{f.candidate}{f.neural ? ' · neural network' : ''}<br /><code className="small">{f.model_version}</code></td>
            <td>{f.sides.map(s => <div key={s.label}>{s.label}: {pct(s.p, 1)}</div>)}</td>
            <td>{f.line == null ? '—' : f.line} ({f.line_source})</td>
            <td>{dateTime(f.generated_at)}{f.start_moved ? ' · start moved since' : ''}<br /><span className="small muted">{dateTime(f.training_cutoff)}</span></td></tr>)}
        </tbody></table></div>}
      {d.without_forecast.length > 0 && <p className="small">No forecast recorded for this game: {d.without_forecast.map(m => `${m.candidate}${m.neural ? ' (neural)' : ''}`).join(', ')}.</p>}
    </>}</AsyncView></details>;
}
