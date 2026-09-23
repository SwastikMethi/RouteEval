import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { NewRun } from './App';
import { Leaderboard, Matrix } from './RunView';
import { Inspector } from './Inspector';
import { compatibility, eventSummary, request, sortedLeaders } from './api';
import { finishedRun, profiles, suite, system } from '../tests/fixtures';

describe('evaluation controls and evidence', () => {
  it('distinguishes verified model access from a model that has not been tested', () => {
    const verified = { ...profiles[0], adapter: 'codex', preflight: { ...profiles[0].preflight!, model_access: 'verified_in_previous_run' as const } };
    const untested = { ...profiles[1], adapter: 'codex', preflight: { ...profiles[1].preflight!, model_access: 'not_checked' as const } };
    render(<NewRun profiles={[verified, untested]} suites={[suite]} runs={[]} system={system} onProfiles={vi.fn()}/>);
    expect(screen.getByText('Model access: verified in a previous run')).toBeVisible();
    expect(screen.getByText('Model access: not verified by a completed run')).toBeVisible();
  });
  it('leaves previous execution failures unselected while allowing an explicit retry', () => {
    const rejected = { ...profiles[1], preflight: { ...profiles[1].preflight!, model_access: 'last_execution_failed' as const, warnings: ['Last execution: The sol model is not supported by this account.'] } };
    render(<NewRun profiles={[profiles[0], rejected]} suites={[suite]} runs={[]} system={system} onProfiles={vi.fn()}/>);
    const checkbox = screen.getByRole('checkbox', { name: 'Select Baseline agent' });
    expect(checkbox).toBeEnabled();
    expect(checkbox).not.toBeChecked();
    expect(screen.getByText(/The sol model is not supported/)).toBeVisible();
    expect(screen.getAllByText('CLI ready')).toHaveLength(2);
    expect(screen.getByRole('button', { name: /Start 2 evaluations/ })).toBeEnabled();
    fireEvent.click(checkbox);
    expect(screen.getByRole('button', { name: /Start 4 evaluations/ })).toBeEnabled();
  });
  it('never allows a missing CLI to be selected or started', () => {
    render(<NewRun profiles={[profiles[2]]} suites={[suite]} runs={[]} system={system} onProfiles={vi.fn()}/>);
    expect(screen.getByRole('checkbox', { name: 'Select Unavailable CLI' })).toBeDisabled();
    expect(screen.getByRole('button', { name: /Start 0 evaluations/ })).toBeDisabled();
    expect(screen.getByText(/CLI executable not found/)).toBeVisible();
  });
  it('recalculates invocations from selected ready profiles and repeat count', () => {
    render(<NewRun profiles={profiles} suites={[suite]} runs={[]} system={system} onProfiles={vi.fn()}/>);
    expect(screen.getByRole('button', { name: /Start 4 evaluations/ })).toBeEnabled();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select Baseline agent' }));
    fireEvent.change(screen.getByLabelText('Attempts per case'), { target: { value: '3' } });
    expect(screen.getByRole('button', { name: /Start 6 evaluations/ })).toBeEnabled();
    fireEvent.change(screen.getByLabelText('Timeout (seconds)'), { target: { value: '0' } });
    expect(screen.getByRole('button', { name: /Start 6 evaluations/ })).toBeDisabled();
  });
  it('opens an unscored infrastructure error without inventing a zero score', () => {
    const select = vi.fn(); const run = { ...finishedRun, attempts: [{ ...finishedRun.attempts[0], status: 'infrastructure_error', score: null, error_code: 'GRADER_MISSING' }] };
    render(<Matrix run={run} onSelect={select}/>);
    const cell = screen.getByRole('button', { name: /infrastructure error, score —/ });
    expect(within(cell).queryByText('0.0')).not.toBeInTheDocument();
    fireEvent.click(cell); expect(select).toHaveBeenCalledWith('attempt-ref-1');
  });
  it('disables cost rankings when a profile lacks prices', () => {
    render(<Leaderboard run={finishedRun}/>);
    expect(screen.getByRole('button', { name: 'Cost' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Value' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Speed' })).toBeEnabled();
  });
  it('puts missing metrics last and blocks incompatible suite fingerprints', () => {
    const leaders = [finishedRun.leaderboard[0], { ...finishedRun.leaderboard[1], mean_score: null, median_agent_duration_ms: null }];
    expect(sortedLeaders(leaders, 'quality')[0].profile_id).toBe('mock-reference');
    expect(sortedLeaders(leaders, 'speed')[0].profile_id).toBe('mock-reference');
    expect(compatibility(finishedRun, { ...finishedRun, id: 'another' })).toBeNull();
    expect(compatibility(finishedRun, { ...finishedRun, suite: { ...suite, hash: 'different' } })).toMatch(/fingerprints differ/);
    expect(compatibility(finishedRun, { ...finishedRun, suite: { ...suite, version: '2.0.0' } })).toMatch(/versions differ/);
  });
  it('adds the local mutation header and reports server errors', async () => {
    const fetcher = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ id: 'run' }), { headers: { 'Content-Type': 'application/json' } }));
    await request('/runs', { method: 'POST', body: '{}' });
    expect(fetcher).toHaveBeenCalledWith('/api/v1/runs', expect.objectContaining({ headers: expect.objectContaining({ 'X-RouteBench-Request': '1', 'Content-Type': 'application/json' }) }));
    fetcher.mockResolvedValue(new Response(JSON.stringify({ detail: 'Readiness changed.' }), { status: 409 }));
    await expect(request('/runs', { method: 'POST' })).rejects.toThrow('Readiness changed.'); fetcher.mockRestore();
  });
  it('reads the actual SSE event envelope and never displays hidden reasoning', () => {
    expect(eventSummary({ attempt_id: 'a1', event: { type: 'tool_call', tool: 'read_file', text: 'Read source file.' } })).toEqual({ id: 'a1', summary: 'Read source file.' });
    expect(eventSummary({ attempt_id: 'a1', event: { type: 'thinking', text: 'Private reasoning' } })).toBeNull();
  });
  it('does not offer reruns for any attempt in a security-blocked run', async () => {
    const attempt = { ...finishedRun.attempts[0], status: 'infrastructure_error', score: null, case: suite.cases[0], profile: profiles[0], prompt: suite.cases[0].prompt, fixture_hash: 'fixture', prompt_hash: 'prompt', command_preview: [] };
    const fetcher = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(attempt), { headers: { 'Content-Type': 'application/json' } }));
    render(<Inspector id={attempt.id} refreshKey={{ ...finishedRun, security_blocked: true }} onClose={vi.fn()} onRerun={vi.fn()}/>);
    await screen.findByText('Why this score?');
    expect(screen.queryByRole('button', { name: 'Rerun attempt' })).not.toBeInTheDocument();
    fetcher.mockRestore();
  });
});
