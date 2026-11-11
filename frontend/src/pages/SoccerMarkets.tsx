import { api } from '../api/client';
import type { SoccerCompetitionId, SoccerMarketMatch, SoccerMarketSelection, SoccerMarketsResponse } from '../api/types';
import { useApi } from '../api/useApi';
import { AsyncView, Empty } from '../components/States';
import { fairText, lineKind, outcomeText, pct1, RESULT_LABEL, returnText, selectionLabel, sourceText } from '../components/soccerMarkets';
import { dateTime } from '../format';

// Goal, corner, double-chance and draw-no-bet selections (backend app/soccer_markets_live.py through /api/soccer/markets
// and /api/soccer/markets/history). Only stored forecasts are shown; a family without a tested model says why. Every row
// says whether its line is a bookmaker offer (provider, price, retrieval time) or a model line (fair odds). No market has
// proven skill against the bookmaker; a price comparison is information, never a recommendation.

const FAMILY_TITLE = { goals: 'Goals', corners: 'Corners' } as const;

function FamilyStatus({ d }: { d: SoccerMarketsResponse }) {
  return (
    <ul className="soccer-markets-status small">
      {(['goals', 'corners'] as const).map((f) => {
        const s = d.families[f];
        return (
          <li key={f}>
            <strong>{FAMILY_TITLE[f]}:</strong>{' '}
            {s.served && s.competitions.includes(d.competition)
              ? <>served ({s.markets.length} markets{s.withheld_markets?.length ? `; withheld after the test: ${s.withheld_markets.join(', ')}` : ''}).</>
              : s.served ? <>not served for this competition (its test scope: {s.competitions.join(', ') || 'none'}).</>
                : <>not served. <span className="muted">{s.reason}</span></>}
          </li>
        );
      })}
      <li><strong>Double chance, draw no bet:</strong> {d.derived.served ? 'from the tested 90-minute 1X2 forecast.' : 'no tested 1X2 forecast for this competition.'}</li>
    </ul>
  );
}

function SelectionRows({ sels, home, away }: { sels: SoccerMarketSelection[]; home: string; away: string }) {
  return (
    <>
      {sels.map((s, i) => (
        <tr key={`${s.market}-${s.side}-${s.line}-${s.source}-${i}`}>
          <td>
            {selectionLabel(s.market, s.side, s.line, home, away)}
            {lineKind(s.line) === 'quarter' && <span className="muted small"> · split stake</span>}
            {lineKind(s.line) === 'whole' && <span className="muted small"> · push possible</span>}
          </td>
          <td><strong>{pct1(s.win_share)}</strong><div className="muted small">{outcomeText(s.probabilities)}</div></td>
          <td>{fairText(s.fair_decimal)}</td>
          <td>
            <span className={`pill ${s.source === 'bookmaker' ? 'soccer-src-book' : 'soccer-src-model'}`}>{sourceText(s)}</span>
            {s.source === 'bookmaker' && s.retrieved_at && <div className="muted small">retrieved {dateTime(s.retrieved_at)}</div>}
          </td>
          <td>{s.market_probability != null ? pct1(s.market_probability) : '—'}</td>
          <td>{s.source === 'bookmaker' ? returnText(s.expected_return) : '—'}</td>
          <td>{s.result ? RESULT_LABEL[s.result] : '—'}</td>
        </tr>
      ))}
    </>
  );
}

const HEAD = (
  <thead>
    <tr><th>Selection</th><th>Model</th><th>Fair odds</th><th>Line source</th><th>Market (no vig)</th><th>Exp. return (model view)</th><th>Result</th></tr>
  </thead>
);

const DC_SIDES = ['home_draw', 'draw_away', 'home_away'] as const;

/** Owner request Nov 11: a sportsbook quote with its time, and clearly labelled derived fair prices (never a bet price). */
function MatchPrices({ m }: { m: SoccerMarketMatch }) {
  const b = m.prices?.bet365_double_chance;
  const d = m.prices?.derived_from_match_result_prices;
  if (!b && !d) return null;
  const dc = (side: string) => selectionLabel('double_chance', side, null, m.home, m.away);
  return (
    <div className="soccer-markets-group soccer-prices">
      <h4>Prices</h4>
      {b && (
        <p className="small"><span className="pill soccer-src-book">Bet365 quote</span>{' '}
          Double chance: {DC_SIDES.filter((s) => b.prices[s]).map((s) => `${dc(s)} ${b.prices[s]!.decimal.toFixed(2)}`).join(' · ')}
          <span className="muted"> · retrieved {dateTime(b.retrieved_at)}</span></p>
      )}
      {d && (
        <p className="small"><span className="pill soccer-src-model">Derived from match result prices</span>{' '}
          Double chance: {DC_SIDES.filter((s) => d.double_chance[s] != null).map((s) => `${dc(s)} ${d.double_chance[s]!.toFixed(2)}`).join(' · ')}
          {' · '}Draw no bet: {m.home} {d.draw_no_bet.home.toFixed(2)} · {m.away} {d.draw_no_bet.away.toFixed(2)}
          <span className="muted"> · from {d.provider} prices retrieved {dateTime(d.retrieved_at)}. Not a price you can bet at.</span></p>
      )}
    </div>
  );
}

function MatchMarkets({ m }: { m: SoccerMarketMatch }) {
  const groups: { title: string; sub: string; sels: SoccerMarketSelection[] }[] = [];
  for (const f of ['goals', 'corners'] as const) {
    const fc = m.families[f];
    if (fc) {
      groups.push({ title: FAMILY_TITLE[f], sels: fc.selections,
        sub: `${fc.label}. Expected ${fc.mean_home.toFixed(2)}–${fc.mean_away.toFixed(2)}; forecast ${dateTime(fc.generated_at)} (cutoff ${dateTime(fc.cutoff_at)})`
          + (fc.counts ? `; final ${fc.counts[0]}–${fc.counts[1]}` : '') + '.' });
    }
  }
  if (m.derived) {
    groups.push({ title: 'Double chance · draw no bet', sels: m.derived.selections,
      sub: `${m.derived.label}, made ${dateTime(m.derived.generated_at)}.` });
  }
  return (
    <details className="soccer-markets-match">
      <summary><strong>{m.home} vs {m.away}</strong> <span className="muted small">{dateTime(m.kickoff_at)}</span></summary>
      {groups.map((g) => (
        <div key={g.title} className="soccer-markets-group">
          <h4>{g.title}</h4>
          <p className="muted small">{g.sub}</p>
          <div className="table-wrap"><table className="table soccer-markets-table">{HEAD}<tbody>
            <SelectionRows sels={g.sels} home={m.home} away={m.away} />
          </tbody></table></div>
        </div>
      ))}
      <MatchPrices m={m} />
    </details>
  );
}

function Ranking({ d }: { d: SoccerMarketsResponse }) {
  if (!d.ranking.length) return null;
  return (
    <details className="soccer-markets-ranking">
      <summary><strong>All upcoming selections, by model probability</strong> <span className="muted small">({d.ranking.length})</span></summary>
      <p className="small muted">Ordered by the model&rsquo;s win probability only; a price never changes the order. {d.notes.no_proven_skill}</p>
      <div className="table-wrap"><table className="table soccer-markets-table">
        <thead><tr><th>Match</th><th>Selection</th><th>Model</th><th>Fair odds</th><th>Line source</th><th>Exp. return (model view)</th></tr></thead>
        <tbody>
          {d.ranking.map((s, i) => (
            <tr key={`${s.match_id}-${s.market}-${s.side}-${s.line}-${s.source}-${i}`}>
              <td>{s.home} vs {s.away}<div className="muted small">{dateTime(s.kickoff_at)}</div></td>
              <td>{s.market_title}: {selectionLabel(s.market, s.side, s.line, s.home, s.away)}</td>
              <td>{pct1(s.win_share)}</td><td>{fairText(s.fair_decimal)}</td>
              <td><span className={`pill ${s.source === 'bookmaker' ? 'soccer-src-book' : 'soccer-src-model'}`}>{sourceText(s)}</span></td>
              <td>{s.source === 'bookmaker' ? returnText(s.expected_return) : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table></div>
    </details>
  );
}

/** Soccer Game day: goal, corner, double-chance and draw-no-bet selections of the coming and last 2 days' matches. */
export function SoccerMarketsPanel({ competition, name }: { competition: SoccerCompetitionId; name: string }) {
  const { state, retry } = useApi((s) => api.soccerMarkets(competition, s), [competition]);
  return (
    <section className="soccer-live soccer-markets" aria-label="Soccer markets">
      <div className="soccer-live-head"><h2>Markets: goals, corners, double chance, draw no bet</h2></div>
      <AsyncView state={state} retry={retry}>
        {(d) => (
          <>
            <FamilyStatus d={d} />
            <p className="small muted">{d.settlement_rule}</p>
            <p className="small"><span className="pill soccer-src-book">Bookmaker offer</span> {d.notes.bookmaker_line.replace(/^Bookmaker offer:\s*/, '')}{' '}
              <span className="pill soccer-src-model">Model line</span> {d.notes.model_line.replace(/^Model line:\s*/, '')}</p>
            <p className="small muted">{d.notes.no_proven_skill} {d.notes.corners_no_bookmaker}</p>
            {(['goals', 'corners'] as const).map((f) => {
              const c = d.checks[f];
              return c && !c.ok ? (
                <div key={f} className="notice error-notice" role="alert">
                  <strong>No new {f} forecast: inputs refused at {dateTime(c.checked_at)}.</strong>
                  <ul className="small">{c.problems.slice(0, 4).map((p) => <li key={p}>{p}</li>)}</ul>
                </div>
              ) : null;
            })}
            {d.matches.length === 0
              ? <Empty>No stored {name} market forecast for the coming matches. A match gets one before 06:00 UTC on its date, from a tested model only.</Empty>
              : <div className="soccer-markets-list">{d.matches.map((m) => <MatchMarkets key={m.match_id} m={m} />)}</div>}
            <Ranking d={d} />
          </>
        )}
      </AsyncView>
    </section>
  );
}

/** Soccer History: settled live goal/corner forecasts and their record per market line. */
export function SoccerMarketsHistoryPanel({ competition, name }: { competition: SoccerCompetitionId; name: string }) {
  const { state, retry } = useApi((s) => api.soccerMarketsHistory(competition, s), [competition]);
  return (
    <section className="soccer-live soccer-markets" aria-label="Soccer market history">
      <div className="soccer-live-head"><h2>Goal and corner forecasts: live record</h2></div>
      <AsyncView state={state} retry={retry}>
        {(h) => {
          const fams = (['goals', 'corners'] as const).filter((f) => h.families[f]?.settled);
          if (!fams.length) {
            return <Empty>No settled live {name} goal or corner forecast yet. {(['goals', 'corners'] as const)
              .map((f) => (h.families_state[f].served ? null : `${FAMILY_TITLE[f]}: ${h.families_state[f].reason}`)).filter(Boolean).join(' ')}</Empty>;
          }
          return fams.map((f) => {
            const x = h.families[f]!;
            return (
              <div key={f} className="soccer-markets-group">
                <h4>{FAMILY_TITLE[f]}: {x.settled} settled live forecasts</h4>
                <p className="small muted">Mean predicted probability against the observed rate for each fixed line (live rows only, never backtests). Small samples move a lot.</p>
                <div className="table-wrap"><table className="table soccer-markets-table">
                  <thead><tr><th>Line</th><th>n</th><th>Predicted</th><th>Observed</th><th>Brier</th></tr></thead>
                  <tbody>{Object.entries(x.record).map(([k, r]) => (
                    <tr key={k}><td>{k.replace('@None', '').replace('@', ' ').replace(/_/g, ' ')}</td><td>{r.n}</td>
                      <td>{pct1(r.mean_predicted)}</td><td>{pct1(r.observed)}</td><td>{r.brier.toFixed(3)}</td></tr>
                  ))}</tbody>
                </table></div>
                <div className="table-wrap"><table className="table soccer-markets-table">
                  <thead><tr><th>Match</th><th>Expected</th><th>Final</th></tr></thead>
                  <tbody>{x.matches.slice(0, 50).map((m) => (
                    <tr key={m.forecast_id}><td>{m.home} vs {m.away}<div className="muted small">{dateTime(m.kickoff_at)}</div></td>
                      <td>{m.mean_home.toFixed(2)}–{m.mean_away.toFixed(2)}</td><td>{m.counts[0]}–{m.counts[1]}</td></tr>
                  ))}</tbody>
                </table></div>
              </div>
            );
          });
        }}
      </AsyncView>
    </section>
  );
}
