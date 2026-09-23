import { expect, test } from '@playwright/test';

const ROUTES = [
  ['/', 'How well does Splash work on a local computer?'],
  ['/dashboard/', 'Results'],
  ['/dashboard/gpqa-diamond/', 'GPQA Diamond result'],
  ['/dashboard/swe-bench-verified/', 'SWE-bench Verified'],
  ['/methodology/', 'How we tested'],
  ['/operations/', 'Run it yourself'],
  ['/sources/', 'Sources and evidence'],
];

const RETIRED_ROUTES = [
  ['/benchmark-tasks/', '/methodology/#gpqa-diamond-method', 'GPQA Diamond method'],
  [
    '/local-pilot-results/',
    '/methodology/#the-evaluated-condition',
    'The evaluated condition',
  ],
  ['/architecture/', '/operations/#local-system-boundary', 'Local system boundary'],
  ['/privacy/', '/methodology/#privacy-and-public-evidence', 'Privacy and public evidence'],
  [
    '/capability-readiness/',
    '/dashboard/gpqa-diamond/#run-record-and-limitations',
    'Run record and limitations',
  ],
  [
    '/historical-frontier-comparison/',
    '/dashboard/gpqa-diamond/#benchmark-comparison',
    'Benchmark comparison',
  ],
  [
    '/frontier-catalog/',
    '/sources/#how-the-evidence-catalog-works',
    'How the evidence catalog works',
  ],
  [
    '/frontier-verification/',
    '/sources/#how-the-numbers-were-checked',
    'How the numbers were checked',
  ],
  ['/glossary/', '/#key-terms', 'Key terms'],
];

const routeUrl = (path) => (path === '/' ? './' : `.${path}`);

for (const [path, heading] of ROUTES) {
  test(`${path} retains its route, heading, navigation, and console health`, async ({ page }) => {
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(message.text());
    });
    const response = await page.goto(routeUrl(path));
    expect(response.status()).toBe(200);
    await expect(page.getByRole('heading', { level: 1, name: heading, exact: true })).toBeVisible();
    await expect(page.locator('.document-sheet')).toHaveCount(1);
    await expect(page.locator('.page-intro')).toHaveCount(1);
    await expect(page.locator('.intro-visual')).toBeVisible();
    await expect(page.locator('.intro-summary')).toBeVisible();
    await expect(page.locator('.document-sheet .reader-section.cool').first()).toBeVisible();
    await expect(
      page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link'),
    ).toHaveCount(5);
    await expect(
      page
        .getByRole('link', { name: 'GitHub', exact: true })
        .or(page.getByRole('link', { name: 'Source code', exact: true }))
        .filter({ visible: true })
        .first(),
    ).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
    for (const theme of ['light', 'dark']) {
      await page.getByLabel('Select theme').selectOption(theme);
      const exposed = await page.locator('main').evaluate((main) => {
        const unbacked = [];
        for (const node of main.querySelectorAll('h1,h2,h3,p,li,summary,td,th,dt,dd,a')) {
          if (!node.getClientRects().length) continue;
          let surface = node;
          let opaque = false;
          while (surface && surface !== main) {
            const style = getComputedStyle(surface);
            const rgba = style.backgroundColor.match(/[\d.]+/g)?.map(Number) ?? [];
            if (rgba.length === 3 || rgba[3] === 1) {
              opaque = style.backgroundImage === 'none';
              break;
            }
            surface = surface.parentElement;
          }
          if (!opaque) unbacked.push(node.textContent.slice(0, 70));
        }
        return unbacked;
      });
      expect(exposed, `${path} ${theme}: reading copy must sit on paper, not texture`).toEqual([]);
      if (process.env.DOCS_SITE_CAPTURE === '1') {
        await page.screenshot({
          path: test.info().outputPath(`page-${theme}.png`),
          fullPage: true,
        });
      }
    }
    expect(errors).toEqual([]);
  });
}

test('overview leads readers through the results hub to the measured GPQA result', async ({
  page,
}) => {
  await page.goto('./');
  const study = page.locator('.intro-study');
  await expect(study).toContainText('Splash');
  await expect(study).toContainText('Qwen3.8-27B');
  await expect(study).toContainText('54.55%');
  await expect(study).toContainText('20');
  await expect(study).toContainText('directional');
  const relationship = page.locator('.relationship-flow');
  await expect(relationship.getByRole('listitem')).toHaveCount(4);
  await expect(relationship).toContainText('Qwen Team');
  await expect(relationship).toContainText('Inco AI');
  await expect(relationship).toContainText('LM Studio');
  await expect(relationship).toContainText('Racecraft Lab');
  await expect(page.getByText('Capability results: not yet measured.', { exact: true })).toHaveCount(0);
  await expect(page.getByText(/setup-check report/i)).toHaveCount(0);
  await expect(page.getByText('54.55%', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('198 of 198', { exact: true })).toBeVisible();
  await expect(page.getByText('64.13 tok/s', { exact: true })).toBeVisible();
  await page.getByRole('link', { name: 'Explore the result', exact: true }).click();
  await expect(page).toHaveURL(/\/splash-evals\/dashboard\/$/);
  await expect(page.getByRole('heading', { name: 'Results', exact: true })).toBeVisible();
  await page.getByRole('link', { name: 'Explore GPQA Diamond', exact: true }).click();
  await expect(page).toHaveURL(/\/splash-evals\/dashboard\/gpqa-diamond\/$/);
  await expect(
    page.getByRole('heading', { name: 'Splash scored 54.55% on GPQA Diamond', exact: true }),
  ).toBeVisible();
});

test('results hub distinguishes the measured and pending benchmark states', async ({ page }) => {
  await page.goto(routeUrl('/dashboard/'));
  const cards = page.locator('.benchmark-card');
  await expect(cards).toHaveCount(2);
  await expect(cards.nth(0)).toContainText('GPQA Diamond');
  await expect(cards.nth(0)).toContainText('54.55%');
  await expect(cards.nth(0)).toContainText('198 / 198 completed');
  await expect(cards.nth(1)).toContainText('SWE-bench Verified');
  await expect(cards.nth(1)).toContainText('Pending');
  await expect(cards.nth(1)).toContainText('12 external references');
  await expect(cards.nth(1)).toHaveAttribute('data-benchmark-status', 'pending');
  await expect(page.getByText(/must never be combined into one score/i)).toBeVisible();
  await expect(page.getByRole('link', { name: 'Explore GPQA Diamond' })).toHaveAttribute(
    'href',
    '/splash-evals/dashboard/gpqa-diamond/',
  );
  await expect(page.getByRole('link', { name: 'Review SWE-bench readiness' })).toHaveAttribute(
    'href',
    '/splash-evals/dashboard/swe-bench-verified/',
  );
});

test('benchmark lifecycle copy never promotes qualification or invalid evidence to capability', async () => {
  const { benchmarkLifecycleView } = await import('../scripts/generate-content.mjs');
  const identity = {
    name: 'SWE-bench Verified',
    variant: 'verified',
  };
  const base = {
    schema_version: 1,
    result_id: 'synthetic-swebench-lifecycle',
    identity,
    counts: { requested: 500, succeeded: 0, errored: 0 },
    metric_name: 'resolved_tasks',
    metric_unit: 'proportion',
    score: null,
  };

  expect(benchmarkLifecycleView(null).label).toBe('Pending');
  expect(benchmarkLifecycleView({ ...base, status: 'qualification' })).toMatchObject({
    className: 'qualification',
    label: 'Qualification',
    capability: false,
  });
  expect(benchmarkLifecycleView({ ...base, status: 'invalid' })).toMatchObject({
    className: 'invalid',
    label: 'Invalid',
    capability: false,
  });
  expect(
    benchmarkLifecycleView({
      ...base,
      status: 'complete',
      counts: { requested: 500, succeeded: 500, errored: 0 },
      score: 0.42,
    }),
  ).toMatchObject({ className: 'measured', label: '42.00%', capability: true });
  expect(() =>
    benchmarkLifecycleView({ ...base, status: 'qualification', score: 0.42 }),
  ).toThrow(/cannot contain a score/i);
});

test('GPQA detail preserves the reviewed result, runtime performance, and comparison limit', async ({
  page,
}) => {
  await page.goto(routeUrl('/dashboard/gpqa-diamond/'));
  const evaluation = page.locator('.evaluation-summary');
  await expect(evaluation.locator(':scope > div')).toHaveCount(4);
  await expect(evaluation).toContainText('Splash / Qwen3.8');
  await expect(evaluation).toContainText('GPQA Diamond');
  await expect(evaluation).toContainText('54.55%');
  await expect(evaluation).toContainText('198 / 198');
  const runDetails = page.locator('details.evaluation-details');
  await expect(runDetails).not.toHaveAttribute('open', '');
  await runDetails.getByText('View run configuration and evidence boundary', { exact: true }).click();
  await expect(runDetails).toContainText('reasoning_effort: medium');
  await expect(runDetails).toContainText('EvalScope 1.12.0');
  await expect(runDetails).toContainText('no prompts, responses, or reasoning traces');
  await page.getByText('View the complete 24-row source record', { exact: true }).click();
  const splashRow = page
    .getByRole('row')
    .filter({ has: page.getByText('Splash / Qwen3.8', { exact: true }) });
  await expect(splashRow).toContainText('54.55%');
  await expect(splashRow).toContainText('198/198 completed · 0 errors');
  const performance = page.locator('.performance-summary');
  await expect(performance).toContainText('2 h 25 m 16 s');
  await expect(performance).toContainText('64.13 tokens/s');
  await expect(performance).toContainText('612,907');
  const performanceDetails = page.locator('details.performance-details');
  await performanceDetails.getByText('View latency and output details', { exact: true }).click();
  await expect(performanceDetails).toContainText('43.95 s');
  await expect(performanceDetails).toContainText('41.80 s');
  await expect(performanceDetails).toContainText('72.49 s');
  await expect(performanceDetails).toContainText('2,818 tokens');
  await expect(performanceDetails).toContainText('Time to first token');
  await expect(page.getByText(/no protocol-matched frontier delta/i)).toBeVisible();
  for (const model of [
    'Claude Sonnet 4',
    'Claude Opus 4',
    'Claude Sonnet 4.6',
    'Claude Opus 4.6',
    'Claude Opus 4.7',
    'Claude Opus 4.8',
    'Claude Sonnet 5',
    'Claude Opus 5',
    'GPT-5.5',
    'GPT-5.6 Sol',
    'GPT-5.6 Terra',
    'GPT-5.6 Luna',
  ]) {
    await expect(page.getByRole('row').filter({ hasText: model }).first()).toBeVisible();
  }
  await page
    .getByRole('link', { name: 'Inspect the primary sources and transcription status', exact: true })
    .click();
  await expect(page).toHaveURL(/\/splash-evals\/sources\/#gpqa-diamond-sources$/);
  await expect(page.getByRole('heading', { name: 'Sources and evidence', exact: true })).toBeVisible();
});

test('GPQA explorer keeps filters, keyboard details, and pinned local evidence', async ({
  page,
}) => {
  await page.goto(routeUrl('/dashboard/gpqa-diamond/'));
  const explorer = page.locator('[data-benchmark-explorer]');
  const evidence = explorer.locator('select[name="benchmark-source"]');
  const family = explorer.locator('select[name="benchmark-family"]');
  await expect(evidence).toHaveValue('all');
  await expect(family).toHaveValue('all');
  await expect(explorer.getByRole('listitem')).toHaveCount(24);
  await expect(explorer.getByText('Showing 23 external observations', { exact: false })).toBeVisible();

  await family.selectOption('gpt');
  await expect(explorer.getByRole('listitem')).toHaveCount(8);
  await expect(explorer.locator('.local-reference')).toHaveCount(1);
  await evidence.focus();
  await page.keyboard.press('Tab');
  await expect(family).toBeFocused();
  await family.selectOption('all');
  const observation = explorer.getByRole('button', {
    name: /Claude Opus 4\.7.*90%/,
  });
  await observation.focus();
  const detail = explorer.locator('[data-explorer-detail]');
  await expect(detail).toContainText('Independent');
  await expect(detail).toContainText('Directional context—not protocol matched');
  await expect(detail.getByText('90%', { exact: true })).toBeVisible();
  await expect(detail.getByRole('link', { name: 'Open the cited source' })).toHaveAttribute(
    'href',
    /epoch\.ai\/models\/claude-opus-4-7/,
  );
  await expect(explorer.locator('.explorer-status')).toContainText('Rows are not ranked');

  const list = explorer.locator('.explorer-list');
  await list.evaluate((element) => {
    element.scrollTop = element.scrollHeight;
  });
  const [listBox, localBox] = await Promise.all([
    list.boundingBox(),
    list.locator('.local-reference').boundingBox(),
  ]);
  expect(listBox).not.toBeNull();
  expect(localBox).not.toBeNull();
  expect(localBox.y).toBeGreaterThanOrEqual(listBox.y - 1);
  expect(localBox.y).toBeLessThanOrEqual(listBox.y + 2);
});

for (const [path, rowCount] of [
  ['/dashboard/gpqa-diamond/', 24],
  ['/dashboard/swe-bench-verified/', 13],
]) {
  test(`${path} keeps the local row pinned without overflow across viewports`, async ({ page }) => {
    await page.goto(routeUrl(path));
    const list = page.locator('.explorer-list');
    for (const viewport of [
      { width: 1280, height: 900 },
      { width: 1024, height: 900 },
      { width: 390, height: 1000 },
    ]) {
      await page.setViewportSize(viewport);
      await expect(list.getByRole('listitem')).toHaveCount(rowCount);
      const box = await list.boundingBox();
      expect(box.height).toBeLessThanOrEqual(448);
      expect(box.height).toBeGreaterThanOrEqual(300);
      await list.evaluate((element) => { element.scrollTop = element.scrollHeight; });
      const pinned = await list.locator('.local-reference').boundingBox();
      expect(pinned.y).toBeGreaterThanOrEqual(box.y - 1);
      expect(pinned.y).toBeLessThanOrEqual(box.y + 2);
      expect(await list.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
        true,
      );
    }
  });
}

test('SWE-bench detail keeps the local result pending and exposes 12 external references', async ({
  page,
}) => {
  await page.goto(routeUrl('/dashboard/swe-bench-verified/'));
  const summary = page.locator('.evaluation-summary');
  await expect(summary).toContainText('Pending');
  await expect(summary).toContainText('No capability percentage is reported');
  await expect(summary).not.toContainText(/\d+(?:\.\d+)?%/);
  await expect(page.getByText('No reviewed runtime measurements are available yet.')).toBeVisible();

  const explorer = page.locator('[data-benchmark-explorer]');
  await expect(explorer.getByRole('listitem')).toHaveCount(13);
  await expect(explorer.getByText('Showing 12 external observations', { exact: false })).toBeVisible();
  const local = explorer.locator('.local-reference');
  await expect(local).toContainText('Pending');
  await expect(local.locator('.explorer-score-dot')).toHaveCount(0);
  await local.getByRole('button').focus();
  await expect(explorer.locator('[data-explorer-detail]')).toContainText('Pending is not zero');

  const evidence = explorer.locator('select[name="benchmark-source"]');
  const family = explorer.locator('select[name="benchmark-family"]');
  await evidence.selectOption('independent');
  await expect(explorer.getByRole('listitem')).toHaveCount(11);
  await evidence.selectOption('provider');
  await expect(explorer.getByRole('listitem')).toHaveCount(3);
  await evidence.selectOption('all');
  await family.selectOption('gpt');
  await expect(explorer.getByRole('listitem')).toHaveCount(5);
  await expect(explorer.locator('.local-reference')).toHaveCount(1);
  await family.selectOption('all');
  const external = explorer.getByRole('button').filter({ hasNotText: 'Splash / Qwen3.8' }).first();
  await external.focus();
  await page.keyboard.press('Enter');
  await expect(explorer.locator('[data-explorer-detail]')).toContainText(
    'Directional context—not protocol matched',
  );
});

for (const [path, recordRows] of [
  ['/dashboard/gpqa-diamond/', 24],
  ['/dashboard/swe-bench-verified/', 13],
]) {
  test(`${path} retains the complete benchmark record when JavaScript is unavailable`, async ({
    browser,
    baseURL,
  }) => {
    const context = await browser.newContext({ javaScriptEnabled: false, baseURL });
    const page = await context.newPage();
    await page.goto(routeUrl(path));
    await expect(page.locator('[data-explorer-controls]')).toBeHidden();
    const record = page.locator('details.comparison-record');
    await expect(record).not.toHaveAttribute('open', '');
    await record.getByText(new RegExp(`View the complete ${recordRows}-row source record`)).click();
    const comparison = record.locator('[data-benchmark-table]');
    await expect(comparison.getByRole('row')).toHaveCount(recordRows + 1);
    await context.close();
  });
}

test('legacy GPQA dashboard deep links retain a discoverable destination and unchanged values', async ({
  page,
}) => {
  await page.goto(routeUrl('/dashboard/#benchmark-comparison'));
  const compatibility = page.locator('#benchmark-comparison');
  await expect(compatibility).toHaveCount(1);
  await compatibility.locator('xpath=following-sibling::a[1]').click();
  await expect(page).toHaveURL(/\/dashboard\/gpqa-diamond\/#benchmark-comparison$/);
  await expect(page.getByText('54.55%', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('198 / 198', { exact: true })).toBeVisible();
  await expect(page.getByText('64.13 tokens/s', { exact: true })).toBeVisible();
});

test('legacy GPQA runtime link resolves to the runtime section on the detail page', async ({ page }) => {
  await page.goto(routeUrl('/dashboard/#runtime-performance'));
  const compatibility = page.locator('#runtime-performance');
  await expect(compatibility).toHaveCount(1);
  await compatibility.locator('xpath=following-sibling::a[1]').click();
  await expect(page).toHaveURL(/\/dashboard\/gpqa-diamond\/#runtime-performance$/);
  await expect(page.getByRole('heading', { name: 'Runtime performance', exact: true })).toBeVisible();
  await expect(page.getByText('64.13 tokens/s', { exact: true })).toBeVisible();
});

test('benchmark hub cards do not collide at desktop, tablet, or mobile widths', async ({ page }) => {
  await page.goto(routeUrl('/dashboard/'));
  for (const viewport of [
    { width: 1280, height: 900 },
    { width: 1024, height: 900 },
    { width: 320, height: 800 },
  ]) {
    await page.setViewportSize(viewport);
    const cards = await page.locator('.benchmark-card').evaluateAll((nodes) =>
      nodes.map((node) => {
        const box = node.getBoundingClientRect();
        return { left: box.left, right: box.right, top: box.top, bottom: box.bottom };
      }),
    );
    expect(cards).toHaveLength(2);
    const overlap =
      Math.min(cards[0].right, cards[1].right) > Math.max(cards[0].left, cards[1].left) &&
      Math.min(cards[0].bottom, cards[1].bottom) > Math.max(cards[0].top, cards[1].top);
    expect(overlap).toBe(false);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
  }
});

test('operations explains the local model alias and trust boundary without a maintainer checklist', async ({
  page,
}) => {
  await page.goto(routeUrl('/operations/'));
  const diagram = page.locator('.system-boundary');
  await expect(diagram.locator('.system-flow > li')).toHaveCount(4);
  const steps = diagram.locator('[data-flow-trigger]');
  await expect(steps).toHaveCount(4);
  await expect(steps.nth(0)).toHaveAttribute('aria-expanded', 'true');
  await expect(steps.nth(1)).toHaveAttribute('aria-expanded', 'false');
  await expect(diagram.locator('#flow-detail-1')).toBeVisible();
  await expect(diagram.locator('#flow-detail-2')).toBeHidden();
  await steps.nth(2).click();
  await expect(steps.nth(0)).toHaveAttribute('aria-expanded', 'false');
  await expect(steps.nth(2)).toHaveAttribute('aria-expanded', 'true');
  await expect(diagram.locator('#flow-detail-3')).toBeVisible();
  await diagram.getByRole('button', { name: 'Reset' }).click();
  await expect(steps.nth(0)).toBeFocused();
  await expect(steps.nth(0)).toHaveAttribute('aria-expanded', 'true');
  await expect(diagram.locator('.evidence-destination')).toHaveCount(3);
  await diagram.locator('.boundary-disclosure summary').click();
  await expect(diagram).toContainText('Allowlist + review gate');
  await expect(diagram).toContainText('has no route back to this Mac');
  await expect(diagram).toContainText(
    'selected-instance check determines whether execution qualifies as local',
  );
  await expect(diagram).not.toContainText('keeps model traffic on this computer');
  const codingPath = diagram.locator('details.coding-execution-path');
  await expect(codingPath).toContainText('SWE-bench Verified');
  await codingPath.locator('summary').click();
  await expect(codingPath.getByRole('listitem')).toHaveCount(5);
  await expect(codingPath).toContainText('Open');
  await expect(codingPath).toContainText('Edit');
  await expect(codingPath).toContainText('Test');
  await expect(codingPath).toContainText('Submit');
  await expect(codingPath).toContainText('Official grade');
  await expect(codingPath).toContainText('separate fresh grader container');
  await expect(page.getByRole('code').filter({ hasText: /^qwen3\.8-27b-splash$/ }).first()).toBeVisible();
  await expect(page.getByText('racecraft-splash-local', { exact: true }).first()).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Review before publishing' })).toHaveCount(0);
});

test('operations flow remains compact and continuous at tablet width', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 900 });
  await page.goto(routeUrl('/operations/'));
  const boxes = await page.locator('.system-flow > li').evaluateAll((steps) =>
    steps.map((step) => {
      const box = step.getBoundingClientRect();
      return { x: box.x, y: box.y };
    }),
  );
  expect(boxes).toHaveLength(4);
  expect(boxes[1].y).toBeCloseTo(boxes[0].y, 0);
  expect(boxes[1].x).toBeGreaterThan(boxes[0].x);
  expect(boxes[2].x).toBeCloseTo(boxes[1].x, 0);
  expect(boxes[2].y).toBeGreaterThan(boxes[1].y);
  expect(boxes[3].y).toBeCloseTo(boxes[2].y, 0);
  expect(boxes[3].x).toBeCloseTo(boxes[0].x, 0);
});

test('operations architecture remains fully readable without JavaScript', async ({
  browser,
  baseURL,
}) => {
  const context = await browser.newContext({ javaScriptEnabled: false, baseURL });
  const page = await context.newPage();
  await page.goto(routeUrl('/operations/'));
  await expect(page.locator('[data-flow-controls]')).toBeHidden();
  await expect(page.locator('[data-flow-detail]')).toHaveCount(4);
  for (const detail of await page.locator('[data-flow-detail]').all()) {
    await expect(detail).toBeVisible();
  }
  const codingPath = page.locator('.system-boundary details.coding-execution-path');
  await codingPath.locator('summary').click();
  await expect(codingPath).toContainText('Official grade');
  await context.close();
});

test('sources present evidence as a concise map instead of duplicated score tables', async ({ page }) => {
  await page.goto(routeUrl('/sources/'));
  const sourceRecord = page.locator('.intro-source-record');
  await expect(sourceRecord).toContainText('54.55%');
  await expect(sourceRecord).toContainText('GPQA Diamond');
  await expect(sourceRecord).toContainText('Racecraft local run');
  await expect(sourceRecord).toContainText('Reviewed local result');
  const sourceVisual = page.locator('.intro-sources');
  for (const child of await sourceRecord.locator(':scope > *').all()) {
    const [visualBox, childBox] = await Promise.all([
      sourceVisual.boundingBox(),
      child.boundingBox(),
    ]);
    expect(visualBox).not.toBeNull();
    expect(childBox).not.toBeNull();
    expect(childBox.x).toBeGreaterThanOrEqual(visualBox.x - 1);
    expect(childBox.x + childBox.width).toBeLessThanOrEqual(visualBox.x + visualBox.width + 1);
  }
  await expect(page.getByText('Original source', { exact: true })).toHaveCount(0);
  await expect(page.locator('.source-card')).toHaveCount(4);
  await expect(page.locator('.evidence-principles > div')).toHaveCount(3);
  await expect(page.locator('.verification-summary > div')).toHaveCount(3);
  await expect(page.getByRole('heading', { name: 'Comparison roster source classes' })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Catalog semantics' })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Comparison status' })).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Review the checked-in verification record' })).toBeVisible();
});

for (const [retired, destination, heading] of RETIRED_ROUTES) {
  test(`${retired} redirects to retained reader content`, async ({ page }) => {
    await page.goto(routeUrl(retired));
    const escapedDestination = destination.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    await expect(page).toHaveURL(new RegExp(`/splash-evals${escapedDestination}$`));
    await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible();
  });
}

test('light and dark preserve SVG lockups, lab texture, section rhythm, and theme choice', async ({
  page,
}) => {
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.goto('./');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  for (const theme of ['light', 'dark']) {
    await page.getByLabel('Select theme').selectOption(theme);
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    await expect(page.locator('.identity img:visible')).toHaveCount(1);
    await expect(page.locator('.footer-identity img:visible')).toHaveCount(1);
    await expect(page.getByRole('img', { name: 'Splash Evals' })).toHaveCount(2);
    await expect(page.getByText('Local tests. Clear evidence. Stated limits.')).toHaveCount(0);
    const [headerLockup, footerLockup] = await page
      .getByRole('img', { name: 'Splash Evals' })
      .evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect().width));
    expect(footerLockup).toBeLessThan(headerLockup);
    await expect(page.locator('.hero img')).toHaveCount(0);
    await expect(page.locator('body')).toHaveCSS(
      'background-size',
      '40px 40px, 40px 40px, 4px 4px',
    );
    const surfaces = await page
      .locator('.reader-section')
      .evaluateAll((nodes) => nodes.map((node) => getComputedStyle(node).backgroundColor));
    expect(new Set(surfaces).size).toBe(3);
    if (process.env.DOCS_SITE_CAPTURE === '1') {
      await page.screenshot({
        path: test.info().outputPath(`home-${theme}.png`),
        fullPage: true,
      });
    }
  }
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
});

test('320px reflow retains controls and readable GPQA result', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await page.goto(routeUrl('/dashboard/gpqa-diamond/'));
  await expect(page.getByLabel('Select theme')).toBeVisible();
  await expect(page.getByRole('button', { name: /Search/ })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Run it yourself', exact: true })).toBeVisible();
  await expect(page.getByText('54.55%', { exact: true }).first()).toBeVisible();
  await expect(page.locator('.footer-identity').getByRole('img', { name: 'Splash Evals' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
});

test('results hero metadata stays inside its card at the desktop-tablet seam', async ({ page }) => {
  await page.setViewportSize({ width: 1246, height: 568 });
  await page.goto(routeUrl('/dashboard/gpqa-diamond/'));
  const contained = await page.locator('.intro-visual').evaluate((card) => {
    const outer = card.getBoundingClientRect();
    return [...card.querySelectorAll('.intro-count > *')].every((node) => {
      const inner = node.getBoundingClientRect();
      return (
        inner.left >= outer.left - 1 &&
        inner.right <= outer.right + 1 &&
        inner.top >= outer.top - 1 &&
        inner.bottom <= outer.bottom + 1
      );
    });
  });
  expect(contained).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
});

test('wide result table scrolls independently and remains keyboard accessible', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(routeUrl('/dashboard/gpqa-diamond/'));
  await page.getByText('View the complete 24-row source record', { exact: true }).click();
  const table = page.getByRole('region', { name: 'Complete source-labeled benchmark record' }).first();
  await table.focus();
  await page.keyboard.press('ArrowRight');
  await expect.poll(() => table.evaluate((node) => node.scrollLeft)).toBeGreaterThan(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
});

test('search finds GPQA Diamond and opens the result', async ({ page }) => {
  await page.goto('./');
  await page.getByRole('button', { name: /Search/ }).click();
  const search = page.getByRole('textbox', { name: 'Search', exact: true });
  await search.fill('GPQA Diamond');
  const result = page
    .getByRole('dialog')
    .getByRole('link')
    .filter({ hasText: 'GPQA Diamond result' })
    .first();
  await expect(result).toBeVisible();
  await result.click();
  await expect(page).toHaveURL(/\/splash-evals\/dashboard\/gpqa-diamond\/(?:#.*)?$/);
  await expect(
    page.getByRole('heading', { name: 'Splash scored 54.55% on GPQA Diamond', exact: true }),
  ).toBeVisible();
});

test('reader text and links meet AA contrast on both themed section surfaces', async ({ page }) => {
  for (const [path] of ROUTES) {
    await page.goto(routeUrl(path));
    for (const theme of ['light', 'dark']) {
      await page.getByLabel('Select theme').selectOption(theme);
      const failures = await page.evaluate(() => {
        const rgb = (color) => color.match(/[\d.]+/g)?.map(Number) ?? [];
        const luminance = (color) =>
          color
            .slice(0, 3)
            .map((value) => {
              const channel = value / 255;
              return channel <= 0.04045
                ? channel / 12.92
                : ((channel + 0.055) / 1.055) ** 2.4;
            })
            .reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0);
        const ratio = (a, b) =>
          (Math.max(luminance(a), luminance(b)) + 0.05) /
          (Math.min(luminance(a), luminance(b)) + 0.05);
        const failures = [];
        for (const node of document.querySelectorAll(
          '.primary-nav a, .github-link, .reader-footer nav a, .reader-footer p, .reader-footer span, .project-name strong, .page-intro h1, .intro-summary, .intro-visual strong, .intro-visual span, .intro-visual figcaption, .hero .sl-link-button, .finding h2, .finding p, .reader-section h2, .reader-section h3, .reader-section h4, .reader-section p, .reader-section a, .reader-section span, .reader-section button, .process strong, .process span, .document-sheet dt, .document-sheet dd, .document-sheet summary, .document-sheet td, .document-sheet th',
        )) {
          if (!node.textContent.trim() || !node.getClientRects().length) continue;
          const style = getComputedStyle(node);
          let ancestor = node;
          let background;
          while (ancestor) {
            const candidate = rgb(getComputedStyle(ancestor).backgroundColor);
            if (candidate.length === 3 || candidate[3] === 1) {
              background = candidate;
              break;
            }
            ancestor = ancestor.parentElement;
          }
          if (!background) throw new Error('Unresolved text background');
          const foreground = rgb(style.color);
          const large =
            Number.parseFloat(style.fontSize) >= 24 ||
            (Number.parseFloat(style.fontSize) >= 18.66 &&
              Number.parseInt(style.fontWeight, 10) >= 700);
          const required = large ? 3 : 4.5;
          if (ancestor === document.body || ancestor === document.documentElement) {
            const dark = document.documentElement.dataset.theme === 'dark';
            const texture = dark ? [124, 179, 221] : [0, 0, 0];
            const opacity = dark ? 1 - 0.93 * 0.93 * 0.96 : 1 - 0.95 * 0.95 * 0.97;
            const marked = background.map(
              (value, index) => texture[index] * opacity + value * (1 - opacity),
            );
            if (ratio(foreground, marked) < required) {
              failures.push(`texture: ${node.textContent}`);
            }
          }
          if (ratio(foreground, background) < required) failures.push(node.textContent);
        }
        return failures;
      });
      expect(failures).toEqual([]);
    }
  }
});

test('reduced motion disables link transitions without hiding result content', async ({ page }) => {
  await page.clock.install({ time: new Date('2026-09-21T10:00:00Z') });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(routeUrl('/dashboard/gpqa-diamond/'));
  await expect(page.locator('.document-sheet a').first()).toHaveCSS('transition-duration', '0s');
  await expect(page.locator('.explorer-score-dot').first()).toHaveCSS('transition-duration', '0s');
  await expect(page.getByText('54.55%', { exact: true }).first()).toBeVisible();

  await page.goto(routeUrl('/operations/'));
  await page.clock.pauseAt(new Date('2026-09-21T10:01:00Z'));
  const diagram = page.locator('.system-boundary');
  await expect(diagram.locator('.flow-trigger').first()).toHaveCSS('transition-duration', '0s');
  await diagram.getByRole('button', { name: 'Play GPQA request' }).click();
  await expect(diagram.locator('[data-flow-trigger]').first()).toHaveAttribute('aria-expanded', 'true');
  await expect(diagram.locator('.route-packet').first()).toHaveCSS('animation-name', 'none');
  await page.clock.runFor(32_000);
  await expect(diagram.locator('[data-flow-status]')).toContainText('Sequence complete');
  await expect(diagram.locator('[data-flow-trigger]').nth(3)).toHaveAttribute('aria-expanded', 'true');
});
