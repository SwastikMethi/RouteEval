import { useEffect, useId, useRef, useState, type CSSProperties } from 'react';
import { createPortal } from 'react-dom';
import { money, number } from './api';
import type { CostEstimate, CostProvenance, Pricing } from './types';

export function CostLabel({ provenance, sources = [] }: { provenance?: CostProvenance; sources?: Pricing[] }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [position, setPosition] = useState<CSSProperties>({});
  const leaveTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const enter = () => { clearTimeout(leaveTimer.current); setHovered(true); setDismissed(false); };
  const leave = () => { leaveTimer.current = setTimeout(() => setHovered(false), 150); };
  useEffect(() => () => clearTimeout(leaveTimer.current), []);
  const locate = (element: HTMLElement) => {
    const rect = element.getBoundingClientRect();
    setPosition({ left: Math.max(12, Math.min(rect.left, window.innerWidth - 342)), ...(rect.bottom < window.innerHeight / 2 ? { top: rect.bottom + 8 } : { bottom: window.innerHeight - rect.top + 8 }) });
  };
  const visible = !dismissed && (open || hovered || focused);
  if (!provenance || provenance === 'unavailable') return <span className="cost-label muted">Unavailable</span>;
  const estimated = provenance === 'estimated' || provenance === 'mixed';
  return <span className="cost-info" onMouseEnter={e => { locate(e.currentTarget); enter(); }} onMouseLeave={leave}>
    <span className="cost-label">{provenance === 'mixed' ? 'Mixed costs' : estimated ? 'Estimated' : 'Reported'}</span>
    <button type="button" className="cost-info-button" aria-label="How cost is calculated" aria-describedby={visible ? id : undefined} aria-expanded={visible}
      onFocus={e => { locate(e.currentTarget); setFocused(true); setDismissed(false); }} onBlur={() => { setFocused(false); setOpen(false); }}
      onClick={e => { e.stopPropagation(); locate(e.currentTarget); setOpen(!open); setDismissed(open); }}
      onKeyDown={e => { if (e.key === 'Escape') { e.stopPropagation(); setOpen(false); setDismissed(true); } }}>ⓘ</button>
    {visible && createPortal(<span id={id} role="tooltip" className="cost-tooltip" style={position} onMouseEnter={enter} onMouseLeave={leave}>
      {provenance === 'mixed' && <span>Includes both CLI-reported costs and token-based estimates.</span>}
      {estimated ? <>
        <span>Estimated from recorded token usage × saved list prices.</span>
        <span>Codex input excludes cached tokens; cached input and output use separate rates, divided by 1,000,000. Reasoning is already included in output.</span>
        {sources.map((p, i) => <span key={i}>{p.source} · {p.effective_date}<br/>USD / million: input {p.input_per_million ?? '—'}, cached {p.cached_input_per_million ?? '—'}, output {p.output_per_million ?? '—'}{p.cache_write_per_million != null && `, cache write ${p.cache_write_per_million}`}.</span>)}
        {!sources.length && <span>The original pricing source was not recorded.</span>}
        <span>This is not your actual ChatGPT charge.</span>
      </> : <span>Reported by the CLI; not independently verified against a provider invoice.</span>}
    </span>, document.body)}
  </span>;
}

export function CostAmount({ value, provenance, sources }: { value?: number | null; provenance?: CostProvenance; sources?: Pricing[] }) {
  return <span className="cost-amount"><strong className="mono">{money(value)}</strong><CostLabel provenance={value == null ? 'unavailable' : provenance} sources={sources}/></span>;
}

export function CostBreakdown({ estimate }: { estimate: CostEstimate }) {
  return <details className="cost-breakdown"><summary>Estimated cost breakdown</summary>
    <p className="muted">{estimate.pricing.source} · price snapshot {estimate.pricing.effective_date}{estimate.backfilled && ' · calculated from saved usage'}</p>
    <div className="table-scroll"><table className="data-table"><thead><tr><th>Token bucket</th><th>Tokens</th><th>USD / million</th><th>Estimated USD</th></tr></thead><tbody>
      {Object.entries(estimate.buckets).map(([name, b]) => <tr key={name}><th>{name === 'input' ? 'Uncached input' : name.replaceAll('_', ' ')}</th><td>{number(b.tokens)}</td><td>{b.rate_per_million}</td><td>{money(b.cost_usd)}</td></tr>)}
    </tbody></table></div>
  </details>;
}
