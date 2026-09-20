import { defineConfig, devices } from '@playwright/test';
import os from 'node:os';
import path from 'node:path';

const previewHost = '127.0.0.1';
const previewPort = Number.parseInt(process.env.DOCS_SITE_PREVIEW_PORT ?? '4321', 10);
if (!Number.isInteger(previewPort) || previewPort < 1 || previewPort > 65_535) {
  throw new Error('DOCS_SITE_PREVIEW_PORT must be an integer from 1 through 65535.');
}
const docsBasePath = '/splash-evals/';
const baseURL = `http://${previewHost}:${previewPort}${docsBasePath}`;
const artifactRoot =
  process.env.DOCS_SITE_SMOKE_ARTIFACT_DIR ?? path.join(os.tmpdir(), 'splash-evals-docs-smoke');

export default defineConfig({
  testDir: './tests',
  testMatch: '**/*.spec.mjs',
  timeout: 30_000,
  expect: { timeout: 5_000 },
  outputDir: path.join(artifactRoot, 'test-results'),
  reporter: [
    ['list'],
    ['html', { open: 'never', outputFolder: path.join(artifactRoot, 'html-report') }],
  ],
  use: {
    baseURL,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    video: 'off',
  },
  projects: [
    {
      name: 'desktop-chromium',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 900 } },
    },
    {
      name: 'mobile-chromium',
      use: { ...devices['Pixel 7'] },
    },
  ],
  webServer: {
    command: `ASTRO_PREVIEW_BACKGROUND=0 pnpm preview --host ${previewHost} --port ${previewPort}`,
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
