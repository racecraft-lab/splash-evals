import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { qualificationContent } from '../scripts/qualification-content.mjs';
import { wrapWideTables } from '../scripts/table-content.mjs';

const record = JSON.parse(readFileSync(new URL('../../docs/qualification-summary.json', import.meta.url), 'utf8'));

test('wide evidence tables receive a keyboard-accessible scroll region', () => {
  const table = '| Model | Test | Result |\n| --- | --- | --- |\n| Example | Mock | Not measured |\n';
  expect(wrapWideTables(table)).toContain('tabindex="0"');
  expect(wrapWideTables(table)).toContain(table);
  const narrow = '| Outcome | Cases |\n| --- | ---: |\n| Completed | 10 |\n';
  expect(wrapWideTables(narrow)).toBe(narrow);
});

test('reviewed public aggregate preserves its interpretation boundary', () => {
  const content = qualificationContent(record);
  expect(record.accepted).toBe(10);
  expect(record.planned).toBe(10);
  expect(content.finding).toContain('10 of 10 reused cases');
  expect(content.finding).toContain('Broader ability still needs a fresh test set');
  expect(content.technical).toContain('72.2%–100.0%');
});

for (const [name, change] of [
  ['missing count', { completed: null }],
  ['inconsistent count', { completed: 9 }],
  ['incomplete success', { accepted: 9 }],
  ['capability claim', { capability_evidence: true }],
  ['different purpose', { publication_purpose: 'model_capability' }],
  ['comparison claim', { historical_comparison: 'matched' }],
  ['missing interval', { wilson_95: null }],
  ['inverted interval', { wilson_95: [1, 0.7] }],
]) {
  test(`rejects ${name} instead of inventing a public result`, () => {
    expect(() => qualificationContent({ ...record, ...change })).toThrow();
  });
}
