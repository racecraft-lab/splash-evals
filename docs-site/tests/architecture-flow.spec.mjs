import { test, expect } from '@playwright/test';

test('flow initializes after page replacement without duplicating controls', async ({ page }) => {
  await page.goto('/splash-evals/operations/');
  await page.locator('[data-architecture-flow]').evaluate((root) => {
    const replacement = root.cloneNode(true);
    replacement.removeAttribute('data-enhanced');
    root.replaceWith(replacement);
    document.dispatchEvent(new Event('astro:page-load'));
  });
  const diagram = page.locator('[data-architecture-flow]');
  await expect(diagram).toHaveAttribute('data-enhanced', 'true');
  await diagram.getByRole('button', { name: 'Next GPQA step' }).click();
  await expect(diagram).toHaveAttribute('data-stage', '1');
  await page.evaluate(() => document.dispatchEvent(new Event('astro:page-load')));
  await expect(diagram).toHaveAttribute('data-stage', '1');
  await diagram.getByRole('button', { name: 'Next GPQA step' }).click();
  await expect(diagram).toHaveAttribute('data-stage', '2');
});

test('request playback gives each stage time to read and obeys pause and manual controls', async ({ page }) => {
  await page.clock.install({ time: new Date('2026-09-21T10:00:00Z') });
  await page.goto('/splash-evals/operations/');
  await page.clock.pauseAt(new Date('2026-09-21T10:01:00Z'));
  const diagram = page.locator('[data-architecture-flow]');
  await expect(diagram).toHaveAttribute('data-stage', '0');
  await diagram.locator('[data-flow-play]').click();
  await page.clock.runFor(7_999);
  await expect(diagram).toHaveAttribute('data-stage', '0');
  await page.clock.runFor(1);
  await expect(diagram).toHaveAttribute('data-stage', '1');
  await page.clock.runFor(3_000);
  await diagram.getByRole('button', { name: 'Pause', exact: true }).click();
  await expect(diagram.locator('[data-flow-route="1"]')).toHaveCSS('animation-play-state', 'paused');
  await page.clock.runFor(24_000);
  await expect(diagram).toHaveAttribute('data-stage', '1');
  await diagram.locator('[data-flow-play]').click();
  await page.clock.runFor(4_999);
  await expect(diagram).toHaveAttribute('data-stage', '1');
  await page.clock.runFor(1);
  await expect(diagram).toHaveAttribute('data-stage', '2');
  await diagram.getByRole('button', { name: 'Pause', exact: true }).click();
  await diagram.locator('[data-flow-trigger]').nth(1).click();
  await diagram.getByRole('button', { name: 'Next GPQA step' }).click();
  await expect(diagram).toHaveAttribute('data-stage', '2');
  await expect(diagram).toHaveAttribute('data-playing', 'false');
  await diagram.locator('[data-flow-trigger]').nth(3).press('Enter');
  await expect(diagram).toHaveAttribute('data-stage', '3');
  await diagram.locator('[data-flow-play]').click();
  await expect(diagram).toHaveAttribute('data-stage', '0');
  await page.clock.runFor(32_000);
  await expect(diagram.getByRole('status')).toContainText('Sequence complete');
  await expect(diagram).toHaveAttribute('data-playing', 'false');
});

test('infographic components fit, stay balanced and keep a stable detail height at every breakpoint', async ({ page }) => {
  test.setTimeout(60_000);
  await page.goto('/splash-evals/operations/');
  const diagram = page.locator('[data-architecture-flow]');
  for (const width of [320, 390, 640, 929, 1024, 1440]) {
    await page.setViewportSize({ width, height: 1000 });
    for (const theme of ['light', 'dark']) {
      await page.getByLabel('Select theme').selectOption(theme);
      let detailHeight;
      for (let stage = 0; stage < 4; stage += 1) {
        await diagram.locator('[data-flow-trigger]').nth(stage).click();
        const geometry = await diagram.evaluate((root) => {
          const visible = (el) => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
          const failures = [];
          const bounds = root.getBoundingClientRect();
          for (const value of root.querySelectorAll('.flow-facts dd')) {
            if (getComputedStyle(value).paddingLeft !== '0px') failures.push('indented fact value');
          }
          for (const el of root.querySelectorAll('button, .flow-node-copy, .flow-detail, .flow-facts > div, .evidence-destination')) {
            if (!visible(el)) continue;
            const box = el.getBoundingClientRect();
            if (el.scrollWidth > el.clientWidth + 1 || box.left < bounds.left || box.right > bounds.right) failures.push(el.className);
          }
          for (const selector of ['.flow-actions', '.system-flow', '.flow-facts', '.flow-trigger', '.evidence-boundary']) {
            for (const group of root.querySelectorAll(selector)) {
              const boxes = [...group.children].filter(visible).map((el) => el.getBoundingClientRect());
              boxes.forEach((a, index) => {
                for (const b of boxes.slice(index + 1)) {
                  if (Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1 &&
                      Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1) failures.push('collision: ' + selector);
                }
              });
            }
          }
          const buttons = [...root.querySelectorAll('.flow-actions button')].map((el) => {
            const box = el.getBoundingClientRect();
            return { y: box.y, width: box.width, height: box.height };
          });
          const nodes = [...root.querySelectorAll('[data-flow-step]')].map((el) => el.getBoundingClientRect());
          const horizontalLayout = Math.abs(nodes[0].y - nodes[1].y) < 1;
          const origin = root.querySelector('.request-map').getBoundingClientRect();
          for (const path of root.querySelectorAll('[data-flow-route]')) {
            const index = Number(path.getAttribute('data-flow-route'));
            if (!horizontalLayout && index === 3) {
              if (getComputedStyle(path).display !== 'none') failures.push('mobile return route crosses nodes');
              continue;
            }
            const start = path.getPointAtLength(0);
            const end = path.getPointAtLength(path.getTotalLength());
            const from = nodes[index];
            const to = nodes[(index + 1) % 4];
            const sameRow = Math.abs(from.y - to.y) < 1;
            if (sameRow) {
              if (Math.sign(end.x - start.x) !== Math.sign(to.x - from.x) ||
                  Math.abs(start.y + origin.y - (from.y + from.height / 2)) > 1) failures.push('horizontal route ' + index);
            } else if (Math.sign(end.y - start.y) !== Math.sign(to.y - from.y) ||
                       Math.abs(start.x + origin.x - (from.x + from.width / 2)) > 1) failures.push('vertical route ' + index);
          }
          return { failures, buttons, height: root.querySelector('.flow-console').getBoundingClientRect().height };
        });
        expect(geometry.failures, width + 'px ' + theme + ' stage ' + stage).toEqual([]);
        for (const button of geometry.buttons) {
          expect(button.y).toBeCloseTo(geometry.buttons[0].y, 0);
          expect(button.width).toBeCloseTo(geometry.buttons[0].width, 0);
          expect(button.height).toBeCloseTo(geometry.buttons[0].height, 0);
          expect(button.height).toBeGreaterThanOrEqual(44);
        }
        detailHeight ??= geometry.height;
        expect(geometry.height).toBeCloseTo(detailHeight, 0);
      }
      const codingDisclosure = diagram.locator('details.coding-execution-path');
      const codingSummary = codingDisclosure.locator(':scope > summary');
      await codingSummary.click();
      expect(await codingSummary.evaluate((el) => parseFloat(getComputedStyle(el).paddingRight))).toBeGreaterThanOrEqual(28);
      await expect(codingDisclosure).toContainText('Official grade');
      for (const step of await codingDisclosure.locator('.agent-loop > li').all()) {
        expect(await step.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
      }
      await codingSummary.click();

      const privacyDisclosure = diagram.locator('details.boundary-disclosure');
      const privacySummary = privacyDisclosure.locator(':scope > summary');
      await privacySummary.click();
      expect(await privacySummary.evaluate((el) => parseFloat(getComputedStyle(el).paddingRight))).toBeGreaterThanOrEqual(28);
      for (const card of await diagram.locator('.evidence-destination').all()) {
        expect(await card.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
      }
      await privacySummary.click();
      const nav = await page.locator('.primary-nav a').evaluateAll((links) => links.map((link) => {
        const box = link.getBoundingClientRect();
        return { left: box.left, right: box.right };
      }));
      for (let index = 1; index < nav.length; index += 1) expect(nav[index].left - nav[index - 1].right).toBeGreaterThanOrEqual(6);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    }
  }
});
