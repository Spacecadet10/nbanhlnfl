import type { Mode, RunType, SourceStatus } from '../api/types';
import { dateTime } from '../format';

const RUN_LABEL: Record<RunType, string> = { demo: 'Demo', backtest: 'Backtest', live: 'Live' };
const RUN_TITLE: Record<RunType, string> = {
  demo: 'Synthetic demo data — not real games or real results',
  backtest: 'Retrospective backtest: computed after the fact, not a forecast recorded before the game',
  live: 'Live forecast recorded before the game\'s cutoff',
};

export function RunBadge({ runType }: { runType: RunType }) {
  return <span className={`badge badge-${runType}`} title={RUN_TITLE[runType]}>{RUN_LABEL[runType]}</span>;
}

export function NearTipoffBadge() {
  return (
    <span className="badge badge-near" title="A separate forecast made shortly before tipoff from the freshest stored results. It is kept separate from the pre-game archive.">
      Near-tipoff
    </span>
  );
}

/** Where the data comes from: synthetic fixtures or stored real NBA data. */
export function ModeBadge({ mode }: { mode: Mode }) {
  return mode === 'demo'
    ? <RunBadge runType="demo" />
    : <span className="badge badge-real" title="Real schedule and results (NBA: ESPN; NHL: NHL stats; unofficial APIs)">Real data</span>;
}

export function SourceList({ sources }: { sources: SourceStatus[] }) {
  return (
    <ul className="sources" aria-label="Data sources">
      {sources.map((s) => (
        <li key={s.name} className={`source source-${s.freshness}`} title={s.note}>
          <span className="dot" aria-hidden />
          <span>{s.name}</span>
          <span className="muted">
            {s.freshness === 'missing' || !s.last_updated ? 'missing' : `${s.freshness} · ${dateTime(s.last_updated)}`}
          </span>
        </li>
      ))}
    </ul>
  );
}
