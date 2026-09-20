import { expect, test } from '@playwright/test';

const ROUTES = [
  ['/', 'Local model evaluation, grounded in evidence.'],
  ['/dashboard/', 'Public results dashboard'],
  ['/methodology/', 'Methodology'],
  ['/benchmark-tasks/', 'Benchmark task guide'],
  ['/local-pilot-results/', 'Local pilot results'],
  ['/architecture/', 'Architecture'],
  ['/privacy/', 'Privacy and publication'],
  ['/operations/', 'Operations'],
  ['/sources/', 'Historical source map'],
  ['/historical-frontier-comparison/', 'Historical frontier comparison'],
  ['/frontier-catalog/', 'Dated historical frontier references'],
  ['/frontier-verification/', 'Historical frontier verification ledger'],
];

function routeUrl(logicalPath) {
  return logicalPath === '/' ? './' : `.${logicalPath}`;
}

test.describe('Splash Evals documentation routes', () => {
  for (const [logicalPath, heading] of ROUTES) {
    test(`${logicalPath} renders its page heading`, async ({ page }) => {
      await page.goto(routeUrl(logicalPath));
      await expect(page.getByRole('main')).toBeVisible();
      await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible();
    });
  }

  test('homepage navigation keeps the GitHub Pages project base path', async ({ page }) => {
    await page.goto('./');
    await page.getByRole('link', { name: 'Methodology' }).first().click();
    await expect(page).toHaveURL(/\/splash-evals\/methodology\/$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Methodology' })).toBeVisible();
  });

  test('frontier verification link resolves inside the project site', async ({ page }) => {
    await page.goto('./');
    await page.getByRole('link', { name: 'Frontier verification ledger', exact: true }).click();
    await expect(page).toHaveURL(/\/splash-evals\/frontier-verification\/$/);
    await expect(
      page.getByRole('heading', { level: 1, name: 'Historical frontier verification ledger' }),
    ).toBeVisible();
  });

  test('first visit defaults to the corporate light theme and saves an explicit dark choice', async ({
    page,
  }) => {
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.goto(routeUrl('/'));
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    const themeSelect = page.locator('starlight-theme-select select');
    if (!(await themeSelect.isVisible())) {
      await page.getByRole('button', { name: 'Menu' }).click();
    }
    await themeSelect.selectOption('dark');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  });

  test('homepage loads without browser console errors', async ({ page }) => {
    const errors = [];
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(message.text());
    });
    page.on('pageerror', (error) => errors.push(error.message));
    await page.goto(routeUrl('/'));
    await page.waitForLoadState('networkidle');
    expect(errors).toEqual([]);
  });
});
