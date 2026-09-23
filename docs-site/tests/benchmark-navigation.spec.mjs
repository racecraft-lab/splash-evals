import { expect, test } from '@playwright/test';

test('benchmark navigation keeps exact active state, keyboard access and mobile spacing', async ({ page }) => {
  await page.goto('/splash-evals/dashboard/');
  const nav = page.getByRole('navigation', { name: 'Benchmark results', exact: true });
  await expect(nav.getByRole('link', { name: 'All results' })).toHaveAttribute('aria-current', 'page');
  await nav.getByRole('link', { name: 'GPQA Diamond Measured' }).focus();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/dashboard\/gpqa-diamond\/$/);
  await expect(nav.locator('[aria-current="page"]')).toHaveText('GPQA DiamondMeasured');
  await nav.getByRole('link', { name: 'SWE-bench Verified Pending' }).click();
  await expect(page).toHaveURL(/\/dashboard\/swe-bench-verified\/$/);
  await expect(nav.locator('[aria-current="page"]')).toHaveText('SWE-bench VerifiedPending');
  for (const width of [1280, 783, 390, 320]) {
    await page.setViewportSize({ width, height: 1000 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    for (const link of await nav.getByRole('link').all()) {
      await expect(link).toBeVisible();
      expect(await link.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
    }
  }
});

test('benchmark navigation works without JavaScript', async ({ browser, baseURL }) => {
  const context = await browser.newContext({ javaScriptEnabled: false, baseURL });
  const page = await context.newPage();
  await page.goto('/splash-evals/dashboard/');
  const nav = page.getByRole('navigation', { name: 'Benchmark results', exact: true });
  await nav.getByRole('link', { name: 'SWE-bench Verified Pending' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'SWE-bench Verified', exact: true })).toBeVisible();
  await expect(nav.locator('[aria-current="page"]')).toHaveCount(1);
  await context.close();
});

test('benchmark explorer labels remain readable at narrow width', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 900 });
  for (const path of [
    '/splash-evals/dashboard/gpqa-diamond/',
    '/splash-evals/dashboard/swe-bench-verified/',
  ]) {
    await page.goto(path);
    const labels = page.locator('.explorer-model');
    await expect(labels.first()).toBeVisible();
    const maximumLines = await labels.evaluateAll((elements) => Math.max(...elements.map((element) => {
      const lineHeight = Number.parseFloat(getComputedStyle(element).lineHeight);
      return Math.round(element.getBoundingClientRect().height / lineHeight);
    })));
    expect(maximumLines).toBeLessThanOrEqual(3);
  }
});
