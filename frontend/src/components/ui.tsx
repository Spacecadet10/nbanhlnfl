import type { ReactNode } from 'react';

// Small shared building blocks of the light design system. They carry layout and tone only; every value they show is
// passed in by the page, so a missing value stays visibly missing ("—" or the page's own wording), never invented.

const PATHS: Record<string, ReactNode> = {
  learning: <><path d="M20 12a8 8 0 1 1-2.34-5.66" /><path d="M20 4v5h-5" /></>,
  gameday: <><rect x="3.5" y="4.5" width="17" height="16" rx="2.5" /><path d="M3.5 9.5h17M8 3v3M16 3v3" /></>,
  performance: <><path d="M4 19h16" /><path d="M6 15l4-4 3 3 5-6" /><path d="M15 8h3v3" /></>,
  dashboard: <><circle cx="12" cy="12" r="8.5" /><circle cx="12" cy="12" r="4.5" /><circle cx="12" cy="12" r="1" /></>,
  history: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /></>,
  target: <><circle cx="12" cy="12" r="8.5" /><path d="M12 3.5v4M12 16.5v4M3.5 12h4M16.5 12h4" /></>,
  bars: <><path d="M5 19v-5M10 19v-9M15 19V8M20 19V5" /></>,
  spread: <><path d="M12 4v16M4 12h16" /></>,
  layers: <><path d="M12 4l8.5 4.5L12 13 3.5 8.5z" /><path d="M3.5 12.5L12 17l8.5-4.5" /></>,
  home: <><path d="M4 11l8-6.5 8 6.5" /><path d="M6.5 9.5V19h11V9.5" /></>,
  plane: <><path d="M3.5 13.5l17-7-5 14-3-6z" /></>,
  trophy: <><path d="M8 4h8v5a4 4 0 0 1-8 0z" /><path d="M8 6H5a3 3 0 0 0 3 4M16 6h3a3 3 0 0 1-3 4M12 13v4M8.5 20h7" /></>,
  scale: <><path d="M12 4v16M6 20h12M5 8h14" /><path d="M5 8l-2.5 6h5zM19 8l-2.5 6h5z" /></>,
  sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4" /></>,
  moon: <><path d="M19.5 14.5A8 8 0 1 1 9.5 4.5a6.5 6.5 0 0 0 10 10z" /></>,
  flask: <><path d="M9.5 3.5h5M10.5 3.5v6L5 18.5a1.5 1.5 0 0 0 1.3 2h11.4a1.5 1.5 0 0 0 1.3-2l-5.5-9v-6" /></>,
  chevron: <><path d="M9 6l6 6-6 6" /></>,
  info: <><circle cx="12" cy="12" r="8.5" /><path d="M12 11v5M12 8h.01" /></>,
  ball: <><circle cx="12" cy="12" r="8.5" /><path d="M3.5 12h17M12 3.5v17M6 6c3 3 3 9 0 12M18 6c-3 3-3 9 0 12" /></>,
  football: <><ellipse cx="12" cy="12" rx="9.5" ry="5.5" transform="rotate(-35 12 12)" /><path d="M8.5 15.5l7-7M10 11.5l2.5 2.5M11.5 10l2.5 2.5" /></>,
  puck: <><ellipse cx="12" cy="10" rx="8" ry="3.5" /><path d="M4 10v4c0 1.9 3.6 3.5 8 3.5s8-1.6 8-3.5v-4" /></>,
  workspace: <><rect x="4" y="4" width="7" height="7" rx="1.5" /><rect x="13" y="4" width="7" height="7" rx="1.5" /><rect x="4" y="13" width="7" height="7" rx="1.5" /><rect x="13" y="13" width="7" height="7" rx="1.5" /></>,
  calendar: <><rect x="3.5" y="4.5" width="17" height="16" rx="2.5" /><path d="M3.5 9.5h17M8 3v3M16 3v3" /></>,
  chevronDown: <><path d="M6 9l6 6 6-6" /></>,
  arrow: <><path d="M7 17L17 7M9 7h8v8" /></>,
  plusminus: <><path d="M12 4v10M7 9h10M7 19h10" /></>,
  airplane: <><path d="M10.5 13.5L4 11l1.5-1.5 7 1L17 6c1-1 2.6-1.4 3-1s0 2-1 3l-4.5 4.5 1 7L14 21l-2.5-6.5-3 3V20l-1.5 1-1-3.5L2.5 16.5 4 15h2.5z" /></>,
  targetArrow: <><circle cx="11" cy="13" r="7.5" /><circle cx="11" cy="13" r="3.5" /><path d="M11 13l8.5-8.5M16 4.5h3.5V8" /></>,
};

/** Outlined 24px icon (stroke = currentColor). */
export function Icon({ name, size = 20 }: { name: keyof typeof PATHS | string; size?: number }) {
  return (
    <svg className="icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">{PATHS[name]}</svg>
  );
}

/** Page title: a lavender icon tile (or a plain icon), the title and a short subtitle; `right` holds page controls. */
export function PageTitle({ icon, title, sub, plain = false, soft = false, right, badge }: {
  icon: string; title: ReactNode; sub?: ReactNode; plain?: boolean; soft?: boolean; right?: ReactNode; badge?: ReactNode;
}) {
  return (
    <div className="page-title">
      <span className={`page-title-icon${plain ? ' plain' : ''}${soft ? ' soft' : ''}`}><Icon name={icon} size={plain ? 40 : 26} /></span>
      <div className="page-title-text"><h1>{title}{badge}</h1>{sub && <p className="page-sub">{sub}</p>}</div>
      {right && <div className="page-title-right">{right}</div>}
    </div>
  );
}

export type Tone = 'plain' | 'dark' | 'lime' | 'lavender' | 'muted' | 'accent';

/** A statistic: small icon, label, large value and a short subtitle. `tone` is emphasis only. */
export function MetricCard({ icon, label, value, hint, tone = 'plain', children }: {
  icon?: string; label: ReactNode; value: ReactNode; hint?: ReactNode; tone?: Tone; children?: ReactNode;
}) {
  return (
    <div className={`metric-card metric-${tone}${icon ? ' has-icon' : ''}`}>
      {icon && <span className="metric-icon"><Icon name={icon} size={30} /></span>}
      <div className="metric-body">
        <div className="metric-label">{label}</div>
        <div className="metric-value">{value}</div>
        {hint && <div className="metric-hint">{hint}</div>}
        {children}
      </div>
    </div>
  );
}

/** The soft lavender box used wherever a forecast does not exist (yet). */
export function EmptyForecastState({ children }: { children: ReactNode }) {
  return <p className="forecast-note">{children}</p>;
}

/** Segmented tabs (lime when active). Buttons, not routes: the page keeps its own state. */
export function SegmentedTabs<T extends string>({ value, onChange, options, label }: {
  value: T; onChange: (v: T) => void; options: { id: T; label: ReactNode }[]; label: string;
}) {
  return (
    <div className="segmented tabs" role="tablist" aria-label={label}>
      {options.map((o) => (
        <button key={o.id} type="button" role="tab" aria-selected={value === o.id} aria-pressed={value === o.id}
          onClick={() => onChange(o.id)}>{o.label}</button>
      ))}
    </div>
  );
}
