import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { wrapWideTables } from '../scripts/table-content.mjs';

const resultPath = new URL(
  '../../results/public/gpqa-diamond-splash-local-2026-09-20.json',
  import.meta.url,
);
const result = JSON.parse(readFileSync(resultPath, 'utf8'));

function collectKeys(value, keys = []) {
  if (Array.isArray(value)) {
    for (const item of value) collectKeys(item, keys);
  } else if (value && typeof value === 'object') {
    for (const [key, child] of Object.entries(value)) {
      keys.push(key);
      collectKeys(child, keys);
    }
  }
  return keys;
}

test('wide evidence tables receive a keyboard-accessible scroll region', () => {
  const table = '| Model | Test | Result |\n| --- | --- | --- |\n| Example | Mock | Not measured |\n';
  expect(wrapWideTables(table)).toContain('tabindex="0"');
  expect(wrapWideTables(table)).toContain(table);
  const narrow = '| Outcome | Cases |\n| --- | ---: |\n| Completed | 198 |\n';
  expect(wrapWideTables(narrow)).toBe(narrow);
});

test('public GPQA result preserves the measured capability and performance contract', () => {
  expect(result).toMatchObject({
    schema_version: 1,
    result_id: 'gpqa-diamond-splash-local-2026-09-20',
    purpose: 'public_capability_result',
    benchmark: {
      name: 'GPQA Diamond',
      requested_cases: 198,
      succeeded_cases: 198,
      errored_cases: 0,
      metric: 'accuracy',
      score: 0.5455,
    },
    evaluation: {
      framework: 'EvalScope',
      framework_version: '1.12.0',
      reasoning_effort: 'medium',
      batch_size: 1,
      retries: 0,
      max_output_tokens: 4096,
    },
    model: {
      public_label: 'Splash / Qwen3.8',
      evaluation_alias: 'racecraft-splash-local',
    },
    runtime: {
      provider: 'LM Studio',
      location: 'local',
      interface: 'OpenAI-compatible',
      duration_seconds: 8716,
    },
    performance: {
      latency_seconds: { average: 43.950018 },
      average_output_tokens_per_second: 64.13,
      output_token_cap: { limit: 4096, cases_at_limit: 81, total_cases: 198 },
      time_to_first_token: 'unavailable',
      time_per_output_token: 'unavailable',
    },
  });
  expect(result.benchmark.succeeded_cases + result.benchmark.errored_cases).toBe(
    result.benchmark.requested_cases,
  );
  expect(result.limitations).toContain(
    'Publisher-reported comparison scores may use protocols that are not identical to this evaluation.',
  );
  for (const digest of Object.values(result.provenance)) {
    expect(digest).toMatch(/^[a-f0-9]{64}$/);
  }
});

test('public result exposes only reviewed aggregate fields and an explicit evidence boundary', () => {
  expect(result.public_evidence_boundary).toEqual({
    included: 'Reviewed aggregate benchmark, execution, performance, and provenance metadata only.',
    excluded: [
      'raw prompts',
      'raw responses',
      'reasoning traces',
      'local paths',
      'private runtime identifiers',
    ],
  });

  const forbiddenKeys = new Set([
    'api_key',
    'api_url',
    'choices',
    'device_identifier',
    'local_path',
    'messages',
    'prompt',
    'raw_report',
    'reasoning_trace',
    'response',
    'runtime_identifier',
  ]);
  expect(collectKeys(result).filter((key) => forbiddenKeys.has(key))).toEqual([]);

  const serialized = JSON.stringify(result);
  expect(serialized).not.toContain('/Users/');
  expect(serialized).not.toContain('127.0.0.1');
  expect(serialized).not.toContain('localhost');
});
