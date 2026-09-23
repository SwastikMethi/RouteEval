import type { Leader, Profile, Run } from './types';

export const API = '/api/v1';
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    ...init,
    headers: { ...(init?.body ? { 'Content-Type': 'application/json' } : {}), ...(init?.method && init.method !== 'GET' ? { 'X-RouteBench-Request': '1' } : {}), ...init?.headers },
  });
  if (!response.ok) {
    let detail = `Request failed (${response.status}).`;
    try { const data = await response.json(); detail = typeof data.detail === 'string' ? data.detail : detail; } catch { /* The server may return a non-JSON error. */ }
    throw new Error(detail);
  }
  return response.headers.get('content-type')?.includes('application/json') ? response.json() : response.text() as Promise<T>;
}
export const isReady = (profile: Profile) => profile.enabled && profile.preflight?.status === 'ready';
export const active = (status: string) => ['queued', 'running', 'preparing', 'grading'].includes(status);
export function eventSummary(data: { attempt_id?: string; event?: { type?: string; summary?: string; text?: string; tool?: string | { name?: string } }; summary?: string; text?: string; tool?: string | { name?: string }; type?: string }) {
  const event = data.event || data;
  if (!data.attempt_id || /thinking|reasoning/i.test(event.type || '')) return null;
  const summary = event.summary || event.text || (typeof event.tool === 'string' ? event.tool : event.tool?.name);
  return summary ? { id: data.attempt_id, summary } : null;
}
export const percent = (value: number | null | undefined) => value == null ? '—' : `${(value * 100).toFixed(value === 1 || value === 0 ? 0 : 1)}%`;
export const score = (value: number | null | undefined) => value == null ? '—' : (value * 100).toFixed(1);
export const number = (value: number | null | undefined) => value == null ? '—' : value.toLocaleString();
export const money = (value: number | null | undefined) => value == null ? '—' : value > 0 && value < .0001 ? '<$0.0001' : `$${value.toFixed(4)}`;
export const duration = (ms: number | null | undefined) => ms == null ? '—' : ms < 1000 ? `${Math.round(ms)} ms` : ms < 60000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.floor(ms / 60000)}m ${Math.round(ms % 60000 / 1000)}s`;
export const date = (value: string | null | undefined) => value ? new Date(value).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—';
export const colors = ['#8D73FF', '#20E3B2', '#65A6FF', '#FFB454', '#EE91D8', '#83CECF'];
export function color(id: string) { let hash = 0; for (const char of id) hash = (hash * 31 + char.charCodeAt(0)) >>> 0; return `hsl(${hash % 360} 72% 72%)`; }
export type Sort = 'quality' | 'reliability' | 'speed' | 'cost' | 'value';
export function sortedLeaders(leaders: Leader[], sort: Sort) {
  const ascending = (value: number | null | undefined) => value ?? Infinity;
  const descending = (value: number | null | undefined) => value ?? -Infinity;
  return [...leaders].sort((a, b) => {
    if (sort === 'speed') return ascending(a.median_agent_duration_ms) - ascending(b.median_agent_duration_ms);
    if (sort === 'cost') return ascending(a.median_cost_usd) - ascending(b.median_cost_usd);
    if (sort === 'value') return descending(b.quality_per_dollar) - descending(a.quality_per_dollar);
    if (sort === 'reliability') return descending(b.pass_rate) - descending(a.pass_rate) || descending(b.completion_rate) - descending(a.completion_rate);
    return descending(b.mean_score) - descending(a.mean_score) || descending(b.pass_rate) - descending(a.pass_rate);
  });
}
export function compatibility(a: Run, b: Run): string | null {
  if (a.suite.id !== b.suite.id) return 'These runs use different suites.';
  if (a.suite.version !== b.suite.version) return `Suite versions differ: ${a.suite.version} and ${b.suite.version}.`;
  if (!a.suite.hash || !b.suite.hash || a.suite.hash !== b.suite.hash) return 'Suite fingerprints differ or are unavailable. Case compatibility cannot be verified.';
  const ids = (run: Run) => run.suite.cases.map(c => c.id).sort().join('|');
  return ids(a) !== ids(b) ? 'The case sets differ.' : null;
}
