import { expect, test, type Page } from '@playwright/test';
import { finishedRun, profiles, runningRun, suite, system } from './fixtures';

async function mockApi(page: Page, options: { blocked?: boolean; mismatch?: boolean } = {}) {
  let started = false; let completed = false;
  const second = { ...finishedRun, id: 'run-two', suite: { ...suite, version: options.mismatch ? '2.0.0' : suite.version } };
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname.replace('/api/v1', '');
    const json = (data: unknown) => route.fulfill({ json: data });
    if (path === '/system') return json(system);
    if (path === '/profiles' || path === '/profiles/preflight') return json({ profiles: options.blocked ? [profiles[2]] : profiles });
    if (path === '/suites') return json({ suites: [suite] });
    if (path === '/runs' && route.request().method() === 'POST') {
      expect(route.request().headers()['x-routebench-request']).toBe('1');
      expect(route.request().postDataJSON().profile_ids).toEqual(['mock-reference', 'mock-baseline']);
      started = true; return json({ id: 'run-one', total_attempts: 4, warnings: [], events_url: '/api/v1/runs/run-one/events' });
    }
    if (path === '/runs') return json({ runs: options.mismatch ? [finishedRun, second] : started ? [completed ? finishedRun : runningRun] : [] });
    if (path.endsWith('/events') && path.startsWith('/runs/')) { await new Promise(resolve => setTimeout(resolve, 1200)); completed = true; return route.fulfill({ contentType: 'text/event-stream', body: 'id: 1\nevent: run.completed\ndata: {"run_id":"run-one"}\n\n' }); }
    if (path === '/runs/run-one') return json(started && !completed ? runningRun : finishedRun);
    if (path === '/runs/run-two') return json(second);
    if (path.endsWith('/router')) return json({ available: true, distribution: [{ model: 'test-fast', count: 2, share: .5, mean_score: 1, pass_rate: 1, median_agent_duration_ms: 12000, total_cost_usd: .02 }, { model: 'test-capable', count: 2, share: .5, mean_score: 1, pass_rate: 1, median_agent_duration_ms: 12000, total_cost_usd: .02 }], categories: [{ category: 'Bug repair', model: 'test-fast', count: 1 }, { category: 'Feature implementation', model: 'test-capable', count: 1 }], timelines: [{ attempt_id: 'attempt-ref-1', case_id: 'RB-PY-001', routes: finishedRun.attempts[0].routes }], observations: [{ case_id: 'RB-PY-001', type: 'potential_over_routing', message: 'A faster observed route also passed this case.' }] });
    if (path.endsWith('/diff')) return route.fulfill({ contentType: 'text/plain', body: 'diff --git a/config.py b/config.py\n--- a/config.py\n+++ b/config.py\n@@ -1 +1 @@\n-return value\n+return value.strip()\n' });
    if (path.endsWith('/events')) return json({ events: [{ type: 'tool_call', tool: 'read_file', turn_index: 1, summary: 'Read the task source.' }, { type: 'route', turn_index: 2, route: { provider: 'test', model: 'test-capable' } }], total: 2 });
    if (path.startsWith('/attempts/')) { const a = finishedRun.attempts.find(a => a.id === path.split('/')[2])!; return json({ ...a, case: suite.cases.find(c => c.id === a.case_id), profile: profiles.find(p => p.id === a.profile_id), prompt: suite.cases[0].prompt, fixture_hash: 'fixture-hash', prompt_hash: 'prompt-hash', command_preview: ['mock-agent', '--workspace', '<workspace>'] }); }
    if (path.startsWith('/artifacts/')) return route.fulfill({ contentType: 'text/plain', body: 'No stderr output.' });
    return route.fulfill({ status: 404, json: { detail: `Unexpected test request: ${path}` } });
  });
}

test('readiness prevents invalid evaluations', async ({ page }) => {
  await mockApi(page, { blocked: true }); await page.goto('/');
  await expect(page.getByRole('heading', { name: /Compare coding agents/ })).toBeVisible();
  await expect(page.getByRole('checkbox', { name: 'Select Unavailable CLI' })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Start 0 evaluations' })).toBeDisabled();
  await expect(page.getByText(/CLI executable not found/)).toBeVisible();
});

test('setup → live SSE matrix → evidence → charts → routing', async ({ page }) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await mockApi(page); await page.goto('/');
  await expect(page.getByRole('button', { name: 'Start 4 evaluations' })).toBeEnabled();
  await page.getByRole('button', { name: 'Start 4 evaluations' }).click();
  await expect(page.getByRole('heading', { name: 'Python smoke', exact: true })).toBeVisible();
  await expect(page.getByText('Provisional results.', { exact: false })).toBeVisible();
  await expect(page.getByText('Evidence saved', { exact: true })).toBeVisible({ timeout: 15000 });
  await page.getByRole('button', { name: /RB-PY-001, attempt 1, passed/ }).click();
  const drawer = page.getByRole('dialog'); await expect(drawer.getByText('Why this score?')).toBeVisible();
  await drawer.getByText('functional', { exact: true }).click(); await expect(drawer.getByText('4 passed in 0.12s')).toBeVisible();
  await drawer.getByRole('tab', { name: 'Diff', exact: true }).click(); await expect(drawer.getByText('+return value.strip()', { exact: true })).toBeVisible();
  await drawer.getByRole('tab', { name: 'Timeline' }).click(); await expect(drawer.getByText('Read the task source.')).toBeVisible();
  await drawer.getByRole('tab', { name: 'Output' }).click(); await expect(drawer.getByText('Fixed the configuration parser and verified edge cases.')).toBeVisible();
  await drawer.getByRole('tab', { name: 'Metadata' }).click(); await expect(drawer.getByText('fixture-hash')).toBeVisible();
  await page.keyboard.press('Escape'); await expect(drawer).not.toBeVisible();
  await page.getByRole('tab', { name: 'Results overview' }).click(); await expect(page.getByRole('heading', { name: 'Configuration leaderboard' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Cost', exact: true })).toBeDisabled();
  await expect(page.getByRole('heading', { name: 'Quality vs. agent time' })).toBeVisible();
  await page.getByRole('tab', { name: /Router analysis/ }).click(); await expect(page.getByRole('heading', { name: 'Per-case routing timeline' })).toBeVisible();
  await expect(page.getByText('Turn 2', { exact: true })).toBeVisible();
  await expect(page.getByText(/Observational comparison across/)).toBeVisible();
  expect(errors).toEqual([]);
});

test('history refuses comparisons across suite versions', async ({ page }) => {
  await mockApi(page, { mismatch: true }); await page.goto('/#/history');
  await page.getByRole('checkbox', { name: 'Compare run-one', exact: true }).check();
  await page.getByRole('checkbox', { name: 'Compare run-two', exact: true }).check();
  await page.getByRole('button', { name: 'Compare 2/2 runs' }).click();
  await expect(page.getByRole('alert')).toContainText('Suite versions differ: 1.0.0 and 2.0.0.');
  await expect(page.getByRole('heading', { name: 'Compatible run comparison' })).not.toBeVisible();
});

test('mobile matrix and inspector remain within the viewport', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 }); await mockApi(page); await page.goto('/#/runs/run-one');
  await page.getByRole('button', { name: /RB-PY-001, attempt 1, passed/ }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole('button', { name: 'Close attempt inspector' }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('estimated costs explain saved rates on hover, keyboard, and mobile tap', async ({ page }) => {
  const pricing = { source: 'Pi model catalog · global.openai.gpt-5.6-terra list price', effective_date: '2026-09-23', input_per_million: 2, cached_input_per_million: .2, output_per_million: 12 };
  const estimate = { model: 'gpt-5.6-terra', pricing, calculated_at: '2026-09-24T00:00:00Z', backfilled: true, buckets: { input: { tokens: 15949, rate_per_million: 2, cost_usd: .031898 }, cached_input: { tokens: 228096, rate_per_million: .2, cost_usd: .0456192 }, output: { tokens: 3180, rate_per_million: 12, cost_usd: .03816 } } };
  const run = { ...finishedRun, winners: { ...finishedRun.winners, cheapest_successful: 'mock-baseline' }, leaderboard: finishedRun.leaderboard.map(l => l.profile_id === 'mock-baseline' ? { ...l, total_cost_usd: .2313544, median_cost_usd: .1156772, cost_coverage: 1, cost_provenance: 'estimated', cost_sources: [pricing] } : l) };
  await mockApi(page);
  await page.route('**/api/v1/runs/run-one', route => route.fulfill({ json: run }));
  await page.route('**/api/v1/attempts/attempt-base-1', route => route.fulfill({ json: { ...finishedRun.attempts[1], cost_usd: .1156772, cost_provenance: 'estimated', cost_estimate: estimate, case: suite.cases[0], profile: profiles[1] } }));
  await page.goto('/#/runs/run-one');
  await page.getByRole('tab', { name: 'Results overview' }).click();
  const row = page.locator('#leader-mock-baseline');
  await expect(row.getByText('Estimated', { exact: true })).toBeVisible();
  const info = row.getByRole('button', { name: 'How cost is calculated' });
  await info.hover();
  await expect(page.getByRole('tooltip')).toContainText('input 2, cached 0.2, output 12');
  await info.focus();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('tooltip')).not.toBeVisible();
  await page.getByRole('tab', { name: 'Case matrix' }).click();
  await page.getByRole('button', { name: /RB-PY-001, attempt 1, failed/ }).click();
  const drawer = page.getByRole('dialog');
  await expect(drawer.getByText('$0.1157', { exact: true })).toBeVisible();
  await drawer.getByText('Estimated cost breakdown', { exact: true }).click();
  await expect(drawer.getByRole('cell', { name: '228,096', exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await drawer.getByRole('button', { name: 'How cost is calculated' }).click();
  const tooltip = page.getByRole('tooltip');
  await expect(tooltip).toContainText('not your actual ChatGPT charge');
  const bounds = await tooltip.boundingBox();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: '../.implementation/cost-estimate-mobile.png', fullPage: true });
});
