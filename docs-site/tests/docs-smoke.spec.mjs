import { expect, test } from '@playwright/test';

const ROUTES = [
  ['/', 'How well does Splash work on a local computer?'],
  ['/dashboard/', 'What we found'],
  ['/methodology/', 'How we test'],
  ['/benchmark-tasks/', 'The tasks we plan to test'],
  ['/local-pilot-results/', 'Local setup-check report'],
  ['/architecture/', 'How the local system works'],
  ['/privacy/', 'Privacy and publication'],
  ['/operations/', 'Run it yourself'],
  ['/sources/', 'Historical sources and coverage'],
  ['/historical-frontier-comparison/', 'How close is Splash to the frontier?'],
  ['/frontier-catalog/', 'Guide to the historical catalog'],
  ['/frontier-verification/', 'Were the source numbers copied correctly?'],
  ['/glossary/', 'A short guide to the terms'],
];
const routeUrl = (path) => path === '/' ? './' : `.${path}`;

for (const [path, heading] of ROUTES) {
  test(`${path} retains its route, heading, navigation, and console health`, async ({ page }) => {
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });
    const response = await page.goto(routeUrl(path));
    expect(response.status()).toBe(200);
    await expect(page.getByRole('heading', { level: 1, name: heading, exact: true })).toBeVisible();
    await expect(page.locator('.document-sheet')).toHaveCount(1);
    await expect(page.locator('.page-intro')).toHaveCount(1);
    await expect(page.locator('.intro-visual')).toBeVisible();
    await expect(page.locator('.intro-summary')).toBeVisible();
    await expect(page.locator('.document-sheet .reader-section.cool').first()).toBeVisible();
    await expect(page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link')).toHaveCount(4);
    await expect(page.getByRole('link', { name: 'GitHub', exact: true }).or(page.getByRole('link', { name: 'Source code', exact: true })).filter({ visible: true }).first()).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    for (const theme of ['light', 'dark']) {
      await page.getByLabel('Select theme').selectOption(theme);
      const exposed = await page.locator('main').evaluate((main) => {
        const exposed = [];
        for (const node of main.querySelectorAll('h1,h2,h3,p,li,summary,td,th,dt,dd,a')) {
          if (!node.getClientRects().length) continue;
          let surface = node;
          let opaque = false;
          while (surface && surface !== main) {
            const style = getComputedStyle(surface);
            const rgba = style.backgroundColor.match(/[\d.]+/g).map(Number);
            if (rgba.length === 3 || rgba[3] === 1) {
              opaque = style.backgroundImage === 'none';
              break;
            }
            surface = surface.parentElement;
          }
          if (!opaque) exposed.push(node.textContent.slice(0, 70));
        }
        return exposed;
      });
      expect(exposed, `${path} ${theme}: reading copy must sit on paper, not texture`).toEqual([]);
      if (process.env.DOCS_SITE_CAPTURE === '1') {
        await page.screenshot({ path: test.info().outputPath(`page-${theme}.png`), fullPage: true });
      }
    }
    expect(errors).toEqual([]);
  });
}

test('novice journey keeps setup validation under methodology, not capability results', async ({ page }) => {
  await page.goto('./');
  await expect(page.locator('.finding, .intro-count, .intro-tally')).toHaveCount(0);
  await expect(page.getByText('Capability results: not yet measured.', { exact: true })).toBeVisible();
  await page.getByRole('link', { name: 'See what we found' }).click();
  await expect(page).toHaveURL(/\/splash-evals\/dashboard\/$/);
  await expect(page.locator('.finding, .intro-count, .intro-tally')).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Read the setup-check report' })).toHaveCount(0);
  await page.getByRole('link', { name: 'Methodology → Test-system validation', exact: true }).click();
  await expect(page).toHaveURL(/\/splash-evals\/methodology\/#test-system-validation$/);
  await expect(page.getByRole('heading', { name: 'Test-system validation', exact: true })).toBeVisible();
  await page.getByRole('link', { name: 'Read the setup-check report' }).click();
  await expect(page).toHaveURL(/\/splash-evals\/local-pilot-results\/$/);
  await expect(page.getByText('Not permitted for this run', { exact: true })).toBeVisible();
  await expect(page.locator('.finding')).toContainText('10 of 10 reused cases');
  await expect(page.locator('.finding')).toContainText('Broader ability still needs a fresh test set');
  await expect(page.locator('details')).not.toHaveAttribute('open');
  await page.getByText('Technical classification and uncertainty', { exact: true }).focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('details')).toHaveAttribute('open', '');
  await expect(page.locator('details')).toContainText('72.2%–100.0%');
});

test('results distinguish requested frontier targets from historical scores and unmeasured benchmarks', async ({ page }) => {
  await page.goto(routeUrl('/dashboard/'));
  await expect(page.getByRole('heading', { name: 'Benchmark comparison board' })).toBeVisible();
  await expect(page.locator('.intro-count, .intro-tally, .finding')).toHaveCount(0);
  await expect(page.locator('.intro-visual figcaption')).toContainText('Missing scores are not zero');
  await expect(page.getByRole('heading', { name: 'Current and previous-generation targets' })).toBeVisible();
  for (const model of ['Sonnet 5', 'Opus 5', 'GPT‑5.6 Sol', 'GPT‑5.6 Terra', 'GPT‑5.6 Luna', 'GPT‑5.5']) {
    await expect(page.getByRole('link', { name: model, exact: true })).toBeVisible();
  }
  await expect(page.getByRole('heading', { name: 'Older reference archive' })).toBeVisible();
  await expect(page.getByRole('group', { name: 'Comparison evidence status' })).toContainText('Not yet measured');
  if (page.viewportSize().width >= 800) {
    const tops = await page.locator('.evidence-track > div').evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect().top));
    expect(new Set(tops).size).toBe(1);
  }
  await expect(page.getByRole('cell', { name: 'Not yet measured', exact: true })).toHaveCount(15);
  for (const variant of ['Terra', 'Luna']) {
    const row = page.getByRole('row').filter({ has: page.getByRole('link', { name: `GPT‑5.6 ${variant}`, exact: true }) });
    await expect(row).toContainText('Not selected for this variant');
    await expect(row).toContainText('Not yet measured');
  }
  await expect(page.getByRole('cell', { name: '46.0%', exact: true })).toBeVisible();
  await expect(page.getByText('Not yet measured is not zero.', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Coding · Aider polyglot' })).toBeVisible();
});

test('comparison explanation leads to source-number checks without implying equivalence', async ({ page }) => {
  await page.goto(routeUrl('/historical-frontier-comparison/'));
  await expect(page.getByText('Not yet measured.', { exact: true })).toBeVisible();
  await expect(page.getByText('No matching local result', { exact: true })).toHaveCount(3);
  await page.getByRole('link', { name: 'Source-number checks', exact: true }).click();
  await expect(page).toHaveURL(/\/splash-evals\/frontier-verification\/$/);
  await expect(page.getByText('Inspect all source-number checks and record identifiers')).toBeVisible();
});

test('light and dark preserve SVG lockups, lab texture, section rhythm, and theme choice', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.goto('./');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  for (const theme of ['light', 'dark']) {
    await page.getByLabel('Select theme').selectOption(theme);
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    await expect(page.locator('.identity img:visible')).toHaveCount(1);
    await expect(page.locator('.footer-identity img:visible')).toHaveCount(1);
    await expect(page.locator('.hero img')).toHaveCount(0);
    await expect(page.locator('body')).toHaveCSS('background-size', '40px 40px, 40px 40px, 4px 4px');
    const surfaces = await page.locator('.reader-section').evaluateAll((nodes) => nodes.map((node) => getComputedStyle(node).backgroundColor));
    expect(new Set(surfaces).size).toBe(3);
    if (process.env.DOCS_SITE_CAPTURE === '1') await page.screenshot({ path: test.info().outputPath(`home-${theme}.png`), fullPage: true });
  }
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
});

test('320px reflow retains controls and readable result', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await page.goto('./');
  await expect(page.getByLabel('Select theme')).toBeVisible();
  await expect(page.getByRole('button', { name: /Search/ })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Run it yourself', exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});

test('wide task table scrolls independently and remains keyboard accessible', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(routeUrl('/benchmark-tasks/'));
  const table = page.getByRole('region', { name: 'Data table with horizontal scrolling' });
  await expect(page.getByText('More columns to the right:', { exact: false })).toBeVisible();
  await table.focus();
  await page.keyboard.press('ArrowRight');
  await expect.poll(() => table.evaluate((node) => node.scrollLeft)).toBeGreaterThan(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});

test('search finds the local report and returns to its content', async ({ page }) => {
  await page.goto('./');
  await page.getByRole('button', { name: /Search/ }).click();
  const search = page.getByRole('textbox', { name: 'Search', exact: true });
  await search.fill('setup');
  await expect(page.getByRole('dialog').getByRole('link', { name: /Local setup-check report/ }).first()).toBeVisible();
  await page.getByRole('dialog').getByRole('link', { name: /Local setup-check report/ }).first().click();
  await expect(page).toHaveURL(/\/splash-evals\/local-pilot-results\//);
});

test('reader text and links meet AA contrast on both themed section surfaces', async ({ page }) => {
  for (const path of ['/', '/dashboard/', '/local-pilot-results/']) {
    await page.goto(routeUrl(path));
    for (const theme of ['light', 'dark']) {
      await page.getByLabel('Select theme').selectOption(theme);
      const failures = await page.evaluate(() => {
        const rgb = (color) => color.match(/[\d.]+/g).map(Number);
        const luminance = (color) => color.slice(0, 3).map((v) => {
          const channel = v / 255;
          return channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4;
        }).reduce((sum, value, i) => sum + value * [.2126, .7152, .0722][i], 0);
        const ratio = (a, b) => (Math.max(luminance(a), luminance(b)) + .05) / (Math.min(luminance(a), luminance(b)) + .05);
        const failures = [];
        for (const node of document.querySelectorAll('.primary-nav a, .github-link, .reader-footer nav a, .reader-footer p, .reader-footer span, .page-intro h1, .intro-summary, .intro-visual strong, .intro-visual span, .intro-visual figcaption, .hero .sl-link-button, .finding h2, .finding p, .reader-section h2, .reader-section h3, .reader-section p, .reader-section a, .process strong, .process span, .document-sheet dt, .document-sheet dd, .document-sheet summary, .document-sheet td, .document-sheet th')) {
          if (!node.textContent.trim() || !node.getClientRects().length) continue;
          const style = getComputedStyle(node);
          let ancestor = node;
          let background;
          while (ancestor) {
            const candidate = rgb(getComputedStyle(ancestor).backgroundColor);
            if (candidate.length === 3 || candidate[3] === 1) { background = candidate; break; }
            ancestor = ancestor.parentElement;
          }
          if (!background) throw new Error('Unresolved text background');
          const foreground = rgb(style.color);
          const large = parseFloat(style.fontSize) >= 24 || (parseFloat(style.fontSize) >= 18.66 && parseInt(style.fontWeight, 10) >= 700);
          const required = large ? 3 : 4.5;
          // Worst-case overlap of both grid lines and dot; no texture on opaque sections/buttons.
          if (ancestor === document.body || ancestor === document.documentElement) {
            const dark = document.documentElement.dataset.theme === 'dark';
            const texture = dark ? [124, 179, 221] : [0, 0, 0];
            const opacity = dark ? 1 - .93 * .93 * .96 : 1 - .95 * .95 * .97;
            const marked = background.map((value, i) => texture[i] * opacity + value * (1 - opacity));
            if (ratio(foreground, marked) < required) failures.push(`texture: ${node.textContent}`);
          }
          if (ratio(foreground, background) < required) failures.push(node.textContent);
        }
        return failures;
      });
      expect(failures).toEqual([]);
    }
  }
});

test('process and definition sections preserve reading order without fake controls', async ({ page }) => {
  await page.goto('./');
  await expect(page.locator('.process > li')).toHaveCount(4);
  await expect(page.locator('.project-definitions dt')).toHaveText(['Splash / Qwen3.8', 'LM Studio', 'This repository']);
  await expect(page.locator('.project-definitions dd')).toHaveCount(3);
  await expect(page.locator('.process [tabindex], .project-definitions [tabindex], .process button')).toHaveCount(0);
  if (process.env.DOCS_SITE_CAPTURE === '1') {
    for (const theme of ['light', 'dark']) {
      await page.getByLabel('Select theme').selectOption(theme);
      for (const section of ['The evaluation process', 'Project definitions']) {
        await page.getByRole('region', { name: section, exact: true }).screenshot({ path: test.info().outputPath(`${section}-${theme}.png`) });
      }
    }
  }
});

test('technical disclosure has a comfortable target and visible keyboard feedback', async ({ page }) => {
  await page.goto(routeUrl('/local-pilot-results/'));
  const summary = page.locator('summary');
  await summary.focus();
  await expect(summary).toBeFocused();
  expect(await summary.evaluate((node) => node.getBoundingClientRect().height)).toBeGreaterThanOrEqual(44);
  await expect(summary).toHaveCSS('outline-style', 'solid');
  expect(await summary.evaluate((node) => getComputedStyle(node, '::before').display)).toBe('none');
  await summary.press('Enter');
  await expect(page.locator('details')).toHaveAttribute('open', '');
  await summary.press('Space');
  await expect(page.locator('details')).not.toHaveAttribute('open');
});

test('reduced motion disables section feedback transitions without hiding content', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(routeUrl('/local-pilot-results/'));
  const summary = page.locator('summary');
  expect(await summary.evaluate((node) => getComputedStyle(node, '::after').transitionDuration)).toBe('0s');
  await expect(page.locator('.document-sheet a').first()).toHaveCSS('transition-duration', '0s');
  await summary.press('Enter');
  await expect(page.locator('details')).toHaveAttribute('open', '');
  await expect(page.locator('details')).toContainText('72.2%–100.0%');
});
