import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { CostAmount, CostLabel } from './Cost';
import { Leaderboard } from './RunView';
import { finishedRun } from '../tests/fixtures';
import type { Pricing } from './types';

const pricing: Pricing = { source: 'Pi model catalog · global.openai.gpt-5.6-terra list price', effective_date: '2026-09-23', input_per_million: 2, cached_input_per_million: .2, output_per_million: 12 };

describe('cost estimates', () => {
  it('shows the rounded amount and saved rates on hover, focus, and tap', async () => {
    render(<CostAmount value={.1156772} provenance="estimated" sources={[pricing]}/>);
    expect(screen.getByText('$0.1157')).toBeVisible();
    expect(screen.getByText('Estimated')).toBeVisible();
    const button = screen.getByRole('button', { name: 'How cost is calculated' });
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
    fireEvent.mouseEnter(button.parentElement!);
    expect(screen.getByRole('tooltip')).toHaveTextContent('2026-09-23');
    expect(screen.getByRole('tooltip')).toHaveTextContent('input 2, cached 0.2, output 12');
    fireEvent.mouseLeave(button.parentElement!);
    await waitFor(() => expect(screen.queryByRole('tooltip')).not.toBeInTheDocument());
    fireEvent.focus(button);
    expect(button).toHaveAttribute('aria-describedby', screen.getByRole('tooltip').id);
    fireEvent.keyDown(button, { key: 'Escape' });
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
    fireEvent.click(button);
    expect(screen.getByRole('tooltip')).toHaveTextContent('not your actual ChatGPT charge');
    fireEvent.click(button);
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
  });

  it('distinguishes reported, mixed, and unavailable costs without zero substitution', () => {
    const { rerender } = render(<CostAmount value={0} provenance="reported"/>);
    expect(screen.getByText('$0.0000')).toBeVisible();
    expect(screen.getByText('Reported')).toBeVisible();
    rerender(<CostLabel provenance="mixed" sources={[pricing]}/>);
    expect(screen.getByText('Mixed costs')).toBeVisible();
    fireEvent.focus(screen.getByRole('button'));
    expect(screen.getByRole('tooltip')).toHaveTextContent('both CLI-reported costs and token-based estimates');
    rerender(<CostAmount value={null} provenance="estimated" sources={[pricing]}/>);
    expect(screen.getByText('Unavailable')).toBeVisible();
    expect(screen.getByText('—')).toBeVisible();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('labels both the cheapest winner and leaderboard total with estimate provenance', () => {
    const run = { ...finishedRun, winners: { ...finishedRun.winners, cheapest_successful: 'mock-reference' }, leaderboard: finishedRun.leaderboard.map((row, i) => i ? row : { ...row, total_cost_usd: .1156772, median_cost_usd: .1156772, cost_provenance: 'estimated' as const, cost_sources: [pricing] }) };
    render(<Leaderboard run={run}/>);
    expect(screen.getAllByText('Estimated')).toHaveLength(2);
    expect(document.querySelector('a .cost-info-button')).toBeNull();
  });
});
