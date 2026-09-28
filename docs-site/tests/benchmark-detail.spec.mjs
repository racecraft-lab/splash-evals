import { expect, test } from '@playwright/test';
import { benchmarkLifecycleView } from '../scripts/generate-content.mjs';
import { codingIntro, renderCodingCard, renderCodingOutcome, renderCodingRuntime } from '../scripts/benchmark-detail.mjs';

function syntheticRecord(status) {
  return {
    schema_version: 1,
    status,
    identity: { name: 'SWE-bench Verified', variant: 'verified-500', dataset_revision: 'a'.repeat(40) },
    counts: { requested: 500, succeeded: status === 'complete' ? 500 : 0, errored: 0 },
    score: status === 'complete' ? 0.5 : null,
    metric_unit: 'proportion',
    evaluator: { name: 'Synthetic evaluator', model_label: 'Synthetic local model', version: 'test' },
    runtime: { duration_seconds: 1234, total_output_tokens: 5678 },
    task_outcomes: status === 'complete' ? { resolved: 250, unresolved: 250, model_failure: 0, infrastructure_error: 0 } : null,
    tested_configuration: { harness: 'Synthetic harness', reasoning_effort: 'xhigh', max_output_tokens: 65536 },
    provenance: { artifacts: { synthetic_only: 'b'.repeat(64) } },
    limitations: ['Synthetic test fixture only.', '<script>untrusted source text</script>'],
  };
}

for (const status of ['pending', 'qualification', 'invalid', 'partial', 'failed', 'complete']) {
  test(`coding detail renders ${status} without a false capability claim`, async () => {
    const record = syntheticRecord(status);
    if (status === 'partial') record.counts.succeeded = 2;
    if (status === 'failed') record.counts.errored = 500;
    const view = benchmarkLifecycleView(record, { requiredCompleteCount: 500 });
    const outcome = renderCodingOutcome(record, view) + renderCodingCard(record, view);
    const runtime = renderCodingRuntime(record, view);
    const intro = codingIntro(view);
    expect(intro.result.score).toBe(view.label);
    expect(intro.summary).toContain(view.detail);
    expect(intro.caption.includes('No local capability score')).toBe(status !== 'complete');
    expect(outcome.includes('50.00%')).toBe(status === 'complete');
    if (status === 'complete') expect(outcome).toContain('250 resolved / 500 requested');
    expect(runtime).toContain('1234 s');
    expect(runtime).toContain('Not reported');
    expect(runtime).not.toContain('<script>');
    expect(runtime).toContain('&lt;script&gt;');
    if (status === 'qualification') expect(runtime).toContain('qualification sample only');
  });
}

test('future measured detail reflows with expanded technical evidence', async ({ page }) => {
  await page.goto('/splash-evals/dashboard/swe-bench-verified/');
  const record = syntheticRecord('complete');
  const view = benchmarkLifecycleView(record, { requiredCompleteCount: 500 });
  const content = `${renderCodingOutcome(record, view)}${renderCodingCard(record, view)}${renderCodingRuntime(record, view)}`
    .replace(/^## (.+)$/gm, '<h2>$1</h2>');
  await page.locator('.document-sheet').evaluate((element, markup) => { element.innerHTML = markup; }, content);
  await page.getByText('View run configuration and evidence boundary', { exact: true }).click();
  for (const width of [1280, 929, 390, 320]) {
    await page.setViewportSize({ width, height: 1000 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await expect(page.getByText('b'.repeat(64), { exact: true })).toBeVisible();
    const cards = await page.locator('.evaluation-summary > div').all();
    for (const card of cards) {
      expect(await card.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
    }
  }
});

test('upstream SWE-bench scoring counts errors as unresolved only when the record reconciles', async () => {
  const record = syntheticRecord('complete');
  record.counts = { requested: 500, succeeded: 482, errored: 18 };
  record.task_outcomes = { resolved: 360, unresolved: 122, model_failure: 16, infrastructure_error: 2 };
  record.score = 0.72;
  expect(() => benchmarkLifecycleView(record, { requiredCompleteCount: 500 })).toThrow();
  const view = benchmarkLifecycleView(record, { requiredCompleteCount: 500, errorsCountAsUnresolved: true });
  expect(view).toMatchObject({ status: 'complete', label: '72.00%', capability: true });
  expect(view.completion).toBe('360 / 500 resolved · 18 errors counted unresolved');
  for (const broken of [
    { ...record, score: 360 / 482 },
    { ...record, counts: { requested: 500, succeeded: 480, errored: 18 } },
    { ...record, task_outcomes: { ...record.task_outcomes, model_failure: 15 } },
    { ...record, task_outcomes: { resolved: 0, unresolved: 0, model_failure: 0, infrastructure_error: 500 }, counts: { requested: 500, succeeded: 0, errored: 500 }, score: 0 },
  ]) {
    expect(() => benchmarkLifecycleView(broken, { requiredCompleteCount: 500, errorsCountAsUnresolved: true })).toThrow();
  }
});
