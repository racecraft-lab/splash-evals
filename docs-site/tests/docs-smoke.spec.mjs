import { expect, test } from '@playwright/test';

const ROUTES = [
  ['/', 'How well does Splash work on a local computer?'],
  ['/dashboard/', 'What we found'],
  ['/methodology/', 'How we tested'],
  ['/operations/', 'Run it yourself'],
  ['/sources/', 'Sources and evidence'],
];

const RETIRED_ROUTES = [
  ['/benchmark-tasks/', '/methodology/#benchmark-and-sample', 'Benchmark and sample'],
  [
    '/local-pilot-results/',
    '/methodology/#transport-and-scorer-qualification',
    'Transport and scorer qualification',
  ],
  ['/architecture/', '/operations/#local-system-boundary', 'Local system boundary'],
  ['/privacy/', '/methodology/#privacy-and-public-evidence', 'Privacy and public evidence'],
  [
    '/capability-readiness/',
    '/dashboard/#run-record-and-limitations',
    'Run record and limitations',
  ],
  [
    '/historical-frontier-comparison/',
    '/dashboard/#benchmark-comparison',
    'Benchmark comparison',
  ],
  ['/frontier-catalog/', '/sources/#catalog-semantics', 'Catalog semantics'],
  ['/frontier-verification/', '/sources/#transcription-checks', 'Transcription checks'],
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

test('overview leads readers to the measured result without stale setup-check claims', async ({
  page,
}) => {
  await page.goto('./');
  await expect(page.getByText('Capability results: not yet measured.', { exact: true })).toHaveCount(0);
  await expect(page.getByText(/setup-check report/i)).toHaveCount(0);
  await expect(page.getByText('54.6%', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('198 of 198', { exact: true })).toBeVisible();
  await expect(page.getByText('64.13 tok/s', { exact: true })).toBeVisible();
  await page.getByRole('link', { name: 'Explore the result', exact: true }).click();
  await expect(page).toHaveURL(/\/splash-evals\/dashboard\/$/);
  await expect(page.getByRole('heading', { name: 'What we found', exact: true })).toBeVisible();
});

test('results expose the reviewed GPQA result, runtime performance, and comparison limit', async ({
  page,
}) => {
  await page.goto(routeUrl('/dashboard/'));
  const splashRow = page
    .getByRole('row')
    .filter({ has: page.getByText('Splash / Qwen3.8 · local LM Studio · medium effort') });
  await expect(splashRow).toContainText('54.6%');
  await expect(splashRow).toContainText('198/198 completed');
  await expect(page.getByText('198 requested, 198 succeeded, 0 errored', { exact: true })).toBeVisible();
  await expect(page.getByRole('cell', { name: '43.95 s', exact: true })).toBeVisible();
  await expect(page.getByRole('cell', { name: '64.13 tokens/s', exact: true })).toBeVisible();
  await expect(page.getByText(/do not support a protocol-matched delta/i)).toBeVisible();
  await page
    .getByRole('link', { name: 'Inspect the primary sources and transcription status', exact: true })
    .click();
  await expect(page).toHaveURL(/\/splash-evals\/sources\/$/);
  await expect(page.getByRole('heading', { name: 'Sources and evidence', exact: true })).toBeVisible();
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
  await page.goto(routeUrl('/dashboard/'));
  await expect(page.getByLabel('Select theme')).toBeVisible();
  await expect(page.getByRole('button', { name: /Search/ })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Run it yourself', exact: true })).toBeVisible();
  await expect(page.getByText('54.6%', { exact: true }).first()).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
});

test('wide result table scrolls independently and remains keyboard accessible', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(routeUrl('/dashboard/'));
  const table = page.getByRole('region', { name: 'Data table with horizontal scrolling' }).first();
  await expect(page.getByText('More columns to the right:', { exact: false }).first()).toBeVisible();
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
    .filter({ hasText: /What we found|Splash scored 54.6%/ })
    .first();
  await expect(result).toBeVisible();
  await result.click();
  await expect(page).toHaveURL(/\/splash-evals\/dashboard\/(?:#.*)?$/);
  await expect(page.getByText('GPQA Diamond', { exact: true }).first()).toBeVisible();
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
          '.primary-nav a, .github-link, .reader-footer nav a, .reader-footer p, .reader-footer span, .page-intro h1, .intro-summary, .intro-visual strong, .intro-visual span, .intro-visual figcaption, .hero .sl-link-button, .finding h2, .finding p, .reader-section h2, .reader-section h3, .reader-section p, .reader-section a, .process strong, .process span, .document-sheet dt, .document-sheet dd, .document-sheet summary, .document-sheet td, .document-sheet th',
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
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(routeUrl('/dashboard/'));
  await expect(page.locator('.document-sheet a').first()).toHaveCSS('transition-duration', '0s');
  await expect(page.getByText('54.6%', { exact: true }).first()).toBeVisible();
});
