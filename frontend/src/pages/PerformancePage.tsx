import { useEffect, useState } from 'react';
import { ThresholdResearch } from '../components/ConfidenceFilter';
import { api } from '../api/client';
import type { MarketId, PerformanceResponse, Policy, Sport } from '../api/types';
import { useApi } from '../api/useApi';
import { RunBadge } from '../components/Badges';
import { MarketReport, MarketVersionSelect } from '../components/MarketView';
import { AgreementTracking } from '../components/Tracking';
import { TrainingStatusCard } from '../components/Training';
import { CandidateRosterCard, HistoricalStartCard, CombinationRules, PoolSelectionCard, SelectionStatusCard, ResearchPerformance, RetrospectiveStudy } from '../components/Research';
import { DetailsToggle, MarketSelect, useDetails, useMarket, useSport } from '../components/Selection';
import { PolicyToggle, RunTypeToggle, usePolicy, useRunType } from '../components/RunTypeToggle';
import { AsyncView, Empty } from '../components/States';
import { calendarDate, FULL_DATE, num, pct } from '../format';

const SMALL_SAMPLE = 200;

export function PerformancePage() {
  const [sport] = useSport();
  const [market, setMarket] = useMarket();
  const [run, setRun] = useRunType();
  const [policyChoice, setPolicy] = usePolicy();
  const [details] = useDetails();
  const policy = run === 'live' || market !== 'winner' ? policyChoice : 'pregame';
  const meta = useApi((s) => api.meta(sport, s), [sport]);
  const { state, retry } = useApi((s) => api.performance(sport, run, policy, s), [sport, run, policy]);
  const markets = meta.state.status === 'success' ? meta.state.data.markets : [];
  if (market !== 'winner') {
    return (
      <section>
        <div className="page-head"><h1>Model performance <RunBadge runType="live" /></h1></div>
        {meta.state.status === 'success'
          ? <MarketSelect value={market} markets={markets} onChange={setMarket} />
          : meta.state.status === 'error' ? <p className="warn small">Market list unavailable: {meta.state.error}</p> : null}
        <div className="policy-row"><PolicyToggle value={policy} onChange={setPolicy} /></div>
        <MarketPerformance sport={sport} market={market} policy={policy} />
        <ResearchSection sport={sport} market={market} show={details} thresholds={policy === 'near_tipoff'} />
      </section>
    );
  }
  return (
    <section>
      {markets.length > 0 && <MarketSelect value={market} markets={markets} onChange={setMarket} />}
      <AsyncView state={state} retry={retry}>
        {(p) => (
          <>
            {p.mode === 'real' && run === 'live' && <div className="policy-row"><PolicyToggle value={policy} onChange={setPolicy} /></div>}
            <Report perf={p} run={run} setRun={setRun} />
          </>
        )}
      </AsyncView>
      {run === 'live' && policy === 'near_tipoff' && <AgreementReport sport={sport} />}
      <ResearchSection sport={sport} market={market} show={details} thresholds={run === 'live' && policy === 'near_tipoff'} />
    </section>
  );
}

/** Research and model-selection cards: loaded only when research details are shown, so the default page is the record. */
function ResearchSection({ sport, market, show, thresholds }: { sport: Sport; market: MarketId; show: boolean; thresholds: boolean }) {
  return (
    <>
      <div className="card">
        <h2>Research</h2>
        <p className="small muted">Model agreement, confidence thresholds, candidate models and the research selector. Research only: none of it is the primary forecast or a betting recommendation.</p>
        <DetailsToggle />
        {show && <p><a href="#joint-agreement">Explore model agreement + confidence ranges</a></p>}
      </div>
      <HistoricalStartCard sport={sport} market={market} />
      {show && <>
        {thresholds && <ThresholdResearch sport={sport} market={market} />}
        <SelectionStatusCard sport={sport} market={market} />
        <ResearchPerformance sport={sport} market={market} />
        <AgreementTracking sport={sport} market={market} />
        <CombinationRules sport={sport} market={market} />
        <CandidateRosterCard sport={sport} market={market} />
        <TrainingStatusCard sport={sport} />
        <PoolSelectionCard sport={sport} market={market} />
        <RetrospectiveStudy sport={sport} market={market} />
      </>}
    </>
  );
}

function Report({ perf: p, run, setRun }: { perf: PerformanceResponse; run: 'live' | 'backtest'; setRun: (r: 'live' | 'backtest') => void }) {
  return (
    <>
      <div className="page-head">
        <h1>Model performance <RunBadge runType={p.run_type} /></h1>
        {p.mode === 'real' && <RunTypeToggle value={run} onChange={setRun} />}
      </div>
      <p className="muted">{p.title}</p>
      <p className="notice" role="note">{p.note}</p>
      {p.sample_size === 0 ? (
        <Empty>
          {p.policy === 'near_tipoff'
            ? <>No two-hour forecasts have been scored yet. The scheduled odds jobs record them within two hours before eligible games; results are scored after the final.</>
            : p.run_type === 'live'
            ? <>No live forecasts have been scored yet. Live forecasts are recorded before regular-season and playoff games and scored after the final. <button type="button" className="link-btn" onClick={() => setRun('backtest')}>See the final test</button></>
            : 'No completed games have been scored yet.'}
        </Empty>
      ) : (
        <>
          <p className="muted">
            {p.sample_size} scored games · {p.date_from && calendarDate(p.date_from, FULL_DATE)} – {p.date_to && calendarDate(p.date_to, FULL_DATE)} · model <code>{p.model_version}</code>
          </p>
          {p.sample_size < SMALL_SAMPLE && (
            <p className="warn small">Small sample: with {p.sample_size} games these numbers are very noisy.</p>
          )}
          <div className="tiles">
            <Tile label="Picked winner" value={pct(p.accuracy, 1)} hint="Share of games where the favored side won." />
            <Tile label="Brier score" value={num(p.brier_score)} hint="Lower is better. 0.25 = always guessing 50%." />
            <Tile label="Log loss" value={num(p.log_loss)} hint="Lower is better. 0.693 = always guessing 50%." />
          </div>

          {p.baselines.length > 0 && (
            <div className="card">
              <h2>Against simple baselines</h2>
              <table className="table compare">
                <thead><tr><th /><th>Picked winner</th><th>Brier score</th><th>Log loss</th></tr></thead>
                <tbody>
                  <tr><th scope="row">This model</th><td>{pct(p.accuracy, 1)}</td><td>{num(p.brier_score)}</td><td>{num(p.log_loss)}</td></tr>
                  {p.baselines.map((b) => (
                    <tr key={b.name}><th scope="row">{b.name}</th><td>{pct(b.accuracy, 1)}</td><td>{num(b.brier_score)}</td><td>{num(b.log_loss)}</td></tr>
                  ))}
                </tbody>
              </table>
              {p.baselines.some((b) => b.accuracy == null) && <p className="small muted">— : a baseline that always says 50% never picks a winner, so it has no picked-winner rate. Compare it on Brier score and log loss.</p>}
            </div>
          )}

          <div className="card">
            <h2>Calibration</h2>
            <p className="small muted">When the model says X%, does the home team win about X% of the time?</p>
            <table className="table">
              <thead><tr><th>Predicted home win</th><th>Games</th><th>Avg predicted</th><th>Actual home wins</th></tr></thead>
              <tbody>
                {p.calibration.map((b) => (
                  <tr key={b.lower}>
                    <td>{pct(b.lower)}–{pct(b.upper)}</td>
                    <td>{b.count}</td>
                    <td>{pct(b.mean_predicted)}</td>
                    <td>
                      {b.observed_rate == null ? '—' : (
                        <span className="calib">
                          <span className="calib-track"><span className="calib-bar" style={{ width: `${b.observed_rate * 100}%` }} /></span>
                          <span>{pct(b.observed_rate)}</span>
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="card">
            <h2>Versus the market</h2>
            <p className="small muted">
              Only games with a market quote are compared: {p.market.matched_games} of {p.sample_size} ({pct(p.market.coverage)} coverage).
            </p>
            {p.market.matched_games === 0 ? <Empty>No games with market odds.</Empty> : (
              <table className="table compare">
                <thead><tr><th /><th>Brier score</th></tr></thead>
                <tbody>
                  <tr><th scope="row">Model</th><td>{num(p.market.model_brier)}</td></tr>
                  <tr><th scope="row">Market</th><td>{num(p.market.market_brier)}</td></tr>
                </tbody>
              </table>
            )}
          </div>

          <details className="card">
            <summary>What do these metrics mean?</summary>
            <dl className="facts">
              <dt>Brier score</dt><dd>Mean squared difference between the predicted probability and the result (1 = home win, 0 = loss).</dd>
              <dt>Log loss</dt><dd>Average negative log-probability assigned to what actually happened; punishes confident misses heavily.</dd>
              <dt>Calibration</dt><dd>Games grouped by predicted probability, comparing average prediction with the observed win rate.</dd>
              <dt>Market</dt><dd>Bookmaker odds converted to probabilities with the bookmaker margin removed.</dd>
            </dl>
          </details>
        </>
      )}
    </>
  );
}

function MarketPerformance({ sport, market, policy }: { sport: Sport; market: MarketId; policy: Policy }) {
  const [version, setVersion] = useState('');     // '' = current sealed market model
  useEffect(() => setVersion(''), [sport, market, policy]);
  const { state, retry } = useApi((s) => api.marketPerformance(sport, market, policy, s, version), [sport, market, policy, version]);
  return <AsyncView state={state} retry={retry}>{(p) => <>
    <MarketVersionSelect versions={p.versions ?? []} value={version} onChange={setVersion} />
    <MarketReport perf={p} />
  </>}</AsyncView>;
}

function Tile({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div className="tile">
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value}</div>
      <div className="tile-hint">{hint}</div>
    </div>
  );
}

function AgreementReport({ sport }: { sport: Sport }) {
  const { state, retry } = useApi((s) => api.agreementPerformance(sport, s), [sport]);
  return <div className="card"><h2>Winner agreement · Forward research</h2>
    <AsyncView state={state} retry={retry}>{p => <>
      <p className="small muted">{p.note}</p>
      {p.groups.length === 0 && <Empty>No complete model comparisons recorded yet.</Empty>}
      {p.groups.map(g => <div key={g.definition}>
        <p className="small muted">Definition {g.definition.slice(0, 8)} · {g.pending} pending · {g.excluded} excluded</p>
        <div className="table-wrap"><table className="table"><thead><tr><th>Rule</th><th>Record</th><th>Accuracy</th><th>Coverage</th></tr></thead>
          <tbody>{(['majority', 'unanimous'] as const).map(rule => <tr key={rule}>
            <th scope="row">{rule === 'majority' ? 'Strict majority' : 'All models agree'}</th>
            <td>{g[rule].wins}–{g[rule].losses} ({g[rule].picks} games)</td>
            <td>{pct(g[rule].accuracy, 1)}</td><td>{pct(g[rule].coverage, 1)}</td>
          </tr>)}</tbody></table></div>
      </div>)}
      <p className="small muted">{p.partial_games} games with incomplete model coverage. This descriptive record does not establish that agreement beats confidence filtering.</p>
    </>}</AsyncView></div>;
}
