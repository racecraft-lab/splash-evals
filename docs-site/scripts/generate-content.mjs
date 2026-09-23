#!/usr/bin/env node
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { parse as parseYaml } from 'yaml';
import { wrapWideTables } from './table-content.mjs';
import { renderCodingOutcome, renderCodingRuntime } from './benchmark-detail.mjs';

const SCRIPT_PATH = 'docs-site/scripts/generate-content.mjs';
const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(SCRIPT_DIR, '../..');
const OUTPUT_DIR = path.join(REPO_ROOT, 'docs-site/src/content/docs');
const BASE = '/splash-evals';
const CHECK_MODE = process.argv.includes('--check');
let validatedRecordsPromise;

async function validatedRecords() {
  validatedRecordsPromise ??= readValidatedRecords();
  return validatedRecordsPromise;
}

async function readValidatedRecords() {
  const bundle = JSON.parse(await fs.readFile(path.join(REPO_ROOT, 'docs-site/src/data/benchmark-records.json'), 'utf8'));
  if (bundle.schema_version !== 1) throw new Error('Unsupported validated benchmark export.');
  const paths = [];
  for (const [directory, suffix] of [['results/public', '.json'], ['references/frontier', '.yaml']]) {
    for (const name of await fs.readdir(path.join(REPO_ROOT, directory))) {
      if (name.endsWith(suffix)) paths.push(`${directory}/${name}`);
    }
  }
  if (JSON.stringify(paths.sort()) !== JSON.stringify(Object.keys(bundle.input_sha256).sort())) {
    throw new Error('Benchmark input set changed; regenerate the validated Python export.');
  }
  for (const source of paths) {
    const bytes = await fs.readFile(path.join(REPO_ROOT, source));
    if (createHash('sha256').update(bytes).digest('hex') !== bundle.input_sha256[source]) {
      throw new Error(`Validated benchmark export is stale: ${source}`);
    }
  }
  return bundle.benchmarks;
}

const PAGES = [
  ['docs/index.md', 'index.md'],
  ['docs/dashboard.md', 'dashboard.md'],
  ['docs/gpqa-diamond.md', 'dashboard/gpqa-diamond.md'],
  ['docs/swe-bench-verified.md', 'dashboard/swe-bench-verified.md'],
  ['docs/methodology.md', 'methodology.md'],
  ['docs/operations.md', 'operations.md'],
  ['docs/sources.md', 'sources.md'],
];

const BENCHMARKS = {
  'gpqa-diamond': {
    benchmarkName: 'GPQA Diamond',
    label: 'GPQA Diamond',
    metric: 'Accuracy',
    localResult: 'results/public/gpqa-diamond-splash-local-2026-09-20.json',
    localSourceUrl:
      'https://github.com/racecraft-lab/splash-evals/blob/main/results/public/gpqa-diamond-splash-local-2026-09-20.json',
  },
  'swe-bench-verified': {
    benchmarkName: 'SWE-bench Verified',
    label: 'SWE-bench Verified',
    metric: 'Resolved tasks',
    localResult: null,
    requiredCompleteCount: 500,
    localSourceUrl: `${BASE}/sources/#swe-bench-verified-sources`,
  },
};

const NON_CAPABILITY_STATUSES = new Set(['pending', 'qualification', 'invalid', 'partial', 'failed']);

function validCount(value, label) {
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new Error(`${label} must be a non-negative integer.`);
  }
  return value;
}

const PENDING_LIFECYCLE = Object.freeze({
  status: 'pending',
  className: 'pending',
  label: 'Pending',
  detail: 'Qualification and full local result remain',
  capability: false,
  score: null,
  completion: 'No reviewed local result is published.',
});

function validatedLifecycleCounts(record) {
  const requested = validCount(record.counts?.requested, 'counts.requested');
  const succeeded = validCount(record.counts?.succeeded, 'counts.succeeded');
  const errored = validCount(record.counts?.errored, 'counts.errored');
  if (succeeded + errored > requested) {
    throw new Error('Benchmark lifecycle attempted cases cannot exceed requested cases.');
  }
  return { requested, succeeded, errored, attempted: succeeded + errored };
}

function completeLifecycleView(record, counts, options) {
  if (!Number.isFinite(record.score)) throw new Error('Complete results require a numeric score.');
  if (counts.requested === 0 || counts.succeeded !== counts.requested || counts.errored !== 0) {
    throw new Error('Complete results require every requested case to succeed without errors.');
  }
  if (options.requiredCompleteCount && counts.requested !== options.requiredCompleteCount) {
    throw new Error(`Complete ${options.benchmarkName ?? 'benchmark'} results require exactly ${options.requiredCompleteCount} cases.`);
  }
  const percentage = record.metric_unit === 'proportion' ? record.score * 100 : record.score;
  if (!Number.isFinite(percentage) || percentage < 0 || percentage > 100) {
    throw new Error('Complete result score is outside the supported percentage range.');
  }
  return {
    status: 'complete',
    className: 'measured',
    label: `${percentage.toFixed(2)}%`,
    detail: `${counts.succeeded} / ${counts.requested} completed`,
    capability: true,
    score: percentage,
    completion: `${counts.succeeded} / ${counts.requested} completed`,
  };
}

function nonCapabilityLifecycleView(record, counts) {
  if (!NON_CAPABILITY_STATUSES.has(record.status)) {
    throw new Error(`Unsupported benchmark lifecycle status: ${String(record.status)}`);
  }
  if (record.score !== null) throw new Error(`${record.status} results cannot contain a score.`);
  if (record.status === 'pending' && counts.attempted !== 0) {
    throw new Error('Pending results cannot contain attempted cases.');
  }
  const views = {
    pending: ['pending', 'Pending', 'Qualification and full local result remain'],
    qualification: ['qualification', 'Qualification', 'Qualification evidence only · not a capability result'],
    invalid: ['invalid', 'Invalid', 'Result withheld · review or rerun required'],
    partial: ['invalid', 'Incomplete', 'Incomplete run · not a capability result'],
    failed: ['invalid', 'Failed', 'Failed run · not a capability result'],
  };
  const [className, label, detail] = views[record.status];
  return {
    status: record.status,
    className,
    label,
    detail,
    capability: false,
    score: null,
    completion: counts.attempted ? `${counts.attempted} / ${counts.requested} attempted` : 'No capability result is published.',
  };
}

export function benchmarkLifecycleView(record, options = {}) {
  if (record === null || record === undefined) return { ...PENDING_LIFECYCLE };
  if (record.schema_version !== 1) throw new Error('Benchmark lifecycle schema_version must be 1.');
  if (options.benchmarkName && record.identity?.name !== options.benchmarkName) {
    throw new Error(`Benchmark lifecycle identity must be ${options.benchmarkName}.`);
  }
  const counts = validatedLifecycleCounts(record);
  return record.status === 'complete'
    ? completeLifecycleView(record, counts, options)
    : nonCapabilityLifecycleView(record, counts);
}

async function loadBenchmarkLifecycle(config) {
  const record = (await validatedRecords())[config.benchmarkName].result;
  return {
    record,
    view: benchmarkLifecycleView(record, {
      benchmarkName: config.benchmarkName,
      requiredCompleteCount: config.requiredCompleteCount,
    }),
  };
}

const routes = new Map(
  PAGES.map(([source, output]) => [
    source,
    output === 'index.md' ? `${BASE}/` : `${BASE}/${output.replace(/\.md$/, '')}/`,
  ]),
);

function normalizeRepoPath(value) {
  return value.split(path.sep).join('/');
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function safeSourceUrl(value, fallback) {
  if (typeof value !== 'string') return fallback;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' ? url.href : fallback;
  } catch {
    return value.startsWith(`${BASE}/`) ? value : fallback;
  }
}

function sourceClass(record) {
  if (record.evaluator_class === 'provider_reported') {
    return ['provider', 'Provider-reported'];
  }
  if (record.evaluator_class === 'cross_provider') {
    return ['cross-provider', 'Cross-provider'];
  }
  if (record.evaluator_class === 'independent' && typeof record.evaluator === 'string') {
    return ['independent', `${record.evaluator.trim()} independent`];
  }
  const url = typeof record.source_url === 'string' ? record.source_url : '';
  if (/epoch\.ai/i.test(url)) return ['independent', 'Independent'];
  if (/openai\.com/i.test(url) && record.developer === 'OpenAI') {
    return ['provider', 'Provider-reported'];
  }
  if (/anthropic\.com/i.test(url) && record.developer === 'Anthropic') {
    return ['provider', 'Provider-reported'];
  }
  return ['published', 'External reference'];
}

function modelFamily(model) {
  if (/claude/i.test(model)) return 'claude';
  if (/gpt|openai o\d/i.test(model)) return 'gpt';
  if (/gemini/i.test(model)) return 'gemini';
  return 'other';
}

async function loadFrontierRecords(benchmarkName) {
  return (await validatedRecords())[benchmarkName].references;
}

async function localObservation(config) {
  const { record, view } = await loadBenchmarkLifecycle(config);
  if (!config.localResult) {
    return {
      model: record?.evaluator.model_label ?? 'Splash / Qwen3.8',
      score: view.score,
      scoreLabel: view.label,
      evidence: view.completion,
      sourceClass: view.className,
      sourceLabel: view.capability ? 'Measured here' : `Local ${view.label.toLowerCase()}`,
      family: 'local',
      sourceUrl: config.localSourceUrl,
      local: true,
      completion: view.detail,
      lifecycle: view,
    };
  }
  const score = view.score;
  return {
    model: record.evaluator.model_label,
    score,
    scoreLabel: `${score.toFixed(2)}%`,
    evidence: `${record.counts.succeeded}/${record.counts.requested} completed · ${record.counts.errored} errors`,
    sourceClass: 'measured',
    sourceLabel: 'Measured here',
    family: 'local',
    sourceUrl: config.localSourceUrl,
    local: true,
    completion: view.completion,
  };
}

async function loadSweBenchCoverage() {
  const coveragePath = path.join(REPO_ROOT, 'references/benchmarks/swebench-verified-coverage.yaml');
  const coverage = parseYaml(await fs.readFile(coveragePath, 'utf8'));
  if (
    coverage.purpose !== 'external_source_coverage_only' ||
    coverage.comparability_status !== 'not_established'
  ) {
    throw new Error('SWE-bench coverage manifest does not preserve the pending local-result boundary.');
  }
  return coverage;
}

async function renderBenchmarkCards() {
  const gpqa = await localObservation(BENCHMARKS['gpqa-diamond']);
  const sweReferences = await loadFrontierRecords(BENCHMARKS['swe-bench-verified'].benchmarkName);
  const sweCoverage = await loadSweBenchCoverage();
  const swe = await localObservation(BENCHMARKS['swe-bench-verified']);
  if (sweReferences.length !== sweCoverage.source_covered_model_count) {
    throw new Error(`SWE-bench hub coverage expected ${sweCoverage.source_covered_model_count} records, found ${sweReferences.length}.`);
  }
  return `<div class="benchmark-grid">
  <article class="benchmark-card measured" data-benchmark-status="complete">
    <p class="benchmark-kicker">Measured capability result</p>
    <h3>GPQA Diamond</h3>
    <p class="benchmark-score"><strong>${escapeHtml(gpqa.scoreLabel)}</strong><span>${escapeHtml(BENCHMARKS['gpqa-diamond'].metric.toLowerCase())} · ${escapeHtml(gpqa.completion)}</span></p>
    <p>A full public science-reasoning benchmark run through EvalScope 1.12.0 and local LM Studio.</p>
    <a class="benchmark-link" href="${BASE}/dashboard/gpqa-diamond/">Explore GPQA Diamond</a>
  </article>
  <article class="benchmark-card ${escapeHtml(swe.lifecycle.className)}" data-benchmark-status="${escapeHtml(swe.lifecycle.status)}">
    <p class="benchmark-kicker">${swe.lifecycle.capability ? 'Measured capability result' : 'Coding evaluation'} · ${sweCoverage.source_covered_model_count} external references</p>
    <h3>SWE-bench Verified</h3>
    <p class="benchmark-score"><strong>${escapeHtml(swe.scoreLabel)}</strong><span>${escapeHtml(swe.completion)}</span></p>
    <p>${swe.lifecycle.capability ? 'A reviewed full 500-task local result is published separately from the historical reference context.' : 'The benchmark plan and external source coverage are published. No Splash coding score is claimed.'}</p>
    <a class="benchmark-link" href="${BASE}/dashboard/swe-bench-verified/">${swe.lifecycle.capability ? 'Explore SWE-bench Verified' : 'Review SWE-bench readiness'}</a>
  </article>
</div>`;
}

async function benchmarkObservations(config) {
  const local = await localObservation(config);
  const references = await loadFrontierRecords(config.benchmarkName);
  if (config.benchmarkName === 'SWE-bench Verified') {
    const coverage = await loadSweBenchCoverage();
    if (references.length !== coverage.source_covered_model_count) {
      throw new Error(`SWE-bench source coverage expected ${coverage.source_covered_model_count} records, found ${references.length}.`);
    }
  }
  const external = references.map((record) => {
    const [classification, label] = sourceClass(record);
    const metricUnit = record.metric_unit;
    const scoreLabel = record.score === null ? 'Unknown' : metricUnit === 'proportion'
      ? `${(record.score * 100).toFixed(1)}%`
      : `${record.score}%`;
    return {
      model: record.model_label,
      score: record.score === null ? null : metricUnit === 'proportion' ? record.score * 100 : record.score,
      scoreLabel,
      evidence: record.source_locator || 'See the cited source record.',
      sourceClass: classification,
      sourceLabel: label,
      family: modelFamily(record.model_label),
      sourceUrl: safeSourceUrl(record.source_url, `${BASE}/sources/`),
      local: false,
    };
  });
  return [local, ...external];
}

function benchmarkTable(observations) {
  const rows = observations.map((record) => {
    const score = record.score === null ? '' : String(record.score);
    return `<tr data-source-class="${escapeHtml(record.sourceClass)}" data-family="${escapeHtml(record.family)}" data-local="${record.local}" data-score="${escapeHtml(score)}" data-source-url="${escapeHtml(record.sourceUrl)}">
  <td>${record.local ? `<strong>${escapeHtml(record.model)}</strong>` : `<a href="${escapeHtml(record.sourceUrl)}">${escapeHtml(record.model)}</a>`}</td>
  <td>${escapeHtml(record.scoreLabel)}</td>
  <td>${escapeHtml(record.sourceLabel)}</td>
  <td>${escapeHtml(record.evidence)}</td>
</tr>`;
  }).join('\n');
  return `<div class="table-scroll benchmark-record" data-benchmark-table tabindex="0" role="region" aria-label="Complete source-labeled benchmark record">
<table>
  <thead><tr><th>Model / condition</th><th>Reported score</th><th>Evidence</th><th>Published condition</th></tr></thead>
  <tbody>
${rows}
  </tbody>
</table>
</div>`;
}

async function renderBenchmarkExplorer(key) {
  const config = BENCHMARKS[key];
  if (!config) throw new Error(`Unknown benchmark explorer: ${key}`);
  const observations = await benchmarkObservations(config);
  const externalCount = observations.filter((record) => !record.local).length;
  return `<div class="frontier-explorer" data-benchmark-explorer data-benchmark-label="${escapeHtml(config.label)}" data-metric-label="${escapeHtml(config.metric)}" aria-labelledby="${key}-explorer-title" aria-describedby="${key}-explorer-context">
  <div class="explorer-heading">
    <p class="explorer-eyebrow">Interactive evidence view</p>
    <h3 id="${key}-explorer-title">Explore the source-labeled observations</h3>
    <p id="${key}-explorer-context">The local row stays pinned while filters change the external context. Rows are not ranked, and missing scores remain visibly pending.</p>
  </div>
  <div class="explorer-controls" data-explorer-controls hidden></div>
  <div class="explorer-plot" data-explorer-plot>
    <p>${externalCount} source-verified external observations are available in the complete record below.</p>
  </div>
  <div class="explorer-detail" data-explorer-detail hidden aria-live="polite"></div>
  <details class="comparison-record">
    <summary>View the complete ${observations.length}-row source record</summary>
    ${benchmarkTable(observations)}
  </details>
</div>`;
}

async function expandBenchmarkExplorers(sourcePath, text) {
  const matches = [...text.matchAll(/<!--\s*benchmark-explorer:([a-z0-9-]+)\s*-->/g)];
  let expanded = text;
  for (const match of matches) {
    expanded = expanded.replace(match[0], await renderBenchmarkExplorer(match[1]));
  }
  if (expanded.includes('<!-- benchmark-card-grid -->')) {
    expanded = expanded.replace('<!-- benchmark-card-grid -->', await renderBenchmarkCards());
  }
  if (sourcePath === 'docs/swe-bench-verified.md') {
    const { record, view } = await loadBenchmarkLifecycle(BENCHMARKS['swe-bench-verified']);
    expanded = expanded
      .replace('<!-- benchmark-outcome:swe-bench-verified -->', renderCodingOutcome(record, view))
      .replace('<!-- benchmark-runtime:swe-bench-verified -->', renderCodingRuntime(record, view));
  }
  if (/benchmark-explorer:/.test(expanded)) {
    throw new Error(`Unexpanded benchmark explorer marker in ${sourcePath}`);
  }
  return expanded;
}

function rewriteLink(sourcePath, target) {
  if (/^(?:https?:|mailto:|#)/.test(target)) return target;
  if (target.startsWith('/')) {
    throw new Error(`Root-absolute internal link is not base-safe in ${sourcePath}: ${target}`);
  }

  const hashIndex = target.indexOf('#');
  const linkPath = hashIndex === -1 ? target : target.slice(0, hashIndex);
  const fragment = hashIndex === -1 ? '' : target.slice(hashIndex);
  const resolved = normalizeRepoPath(path.normalize(path.join(path.dirname(sourcePath), linkPath)));
  const route = routes.get(resolved);
  if (!route) return target;
  return `${route}${fragment}`;
}

function renderPage(sourcePath, text) {
  const heading = text.match(/^#\s+(.+)$/m);
  if (!heading) throw new Error(`Missing level-one heading in ${sourcePath}`);
  const title = heading[1].trim();
  const body = wrapWideTables(text
    .replace(/^#\s+.+\r?\n(?:\r?\n)?/, '')
    .replace(/^- \[GitHub feature audit\]\(github-feature-audit\.md\)\r?\n?/m, '')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_match, label, target) => {
      return `[${label}](${rewriteLink(sourcePath, target)})`;
    })
    .trim() + '\n').trim();
  const notice = `Generated by \`${SCRIPT_PATH}\` from \`${sourcePath}\`. Do not edit this file directly.`;
  const frontmatter =
    sourcePath === 'docs/index.md'
      ? `title: ${JSON.stringify(title)}
description: "How close can local Splash come to current and previous-generation frontier models? Explore comparison coverage, methods, and measurement gaps."
template: splash
tableOfContents: false
prev: false
next: false
hero:
  title: How well does Splash work on a local computer?
  tagline: How close can local AI come to the frontier? Explore the evidence and the gaps still to measure.
  actions:
    - text: See what we found
      link: /splash-evals/dashboard/
      variant: primary
    - text: How the tests work
      link: /splash-evals/methodology/
      variant: secondary`
      : `title: ${JSON.stringify(title)}\ntemplate: splash\ntableOfContents: false\nprev: false\nnext: false`;
  const pageBody = `<div class="document-sheet">\n\n${body}\n\n</div>`;
  return `---\n${frontmatter}\n---\n\n<!-- ${notice} -->\n\n${pageBody}\n`;
}

async function expectedPages() {
  const expected = new Map();
  for (const [sourcePath, outputName] of PAGES) {
    const source = path.join(REPO_ROOT, sourcePath);
    const text = await expandBenchmarkExplorers(sourcePath, await fs.readFile(source, 'utf8'));
    expected.set(outputName, renderPage(sourcePath, text));
  }
  return expected;
}

async function checkGenerated(expected) {
  const actualNames = (await recursiveMarkdownFiles(OUTPUT_DIR)).sort();
  const expectedNames = [...expected.keys()].sort();
  if (JSON.stringify(actualNames) !== JSON.stringify(expectedNames)) {
    throw new Error('Generated docs file set is stale; run pnpm --dir docs-site content:generate');
  }
  for (const [outputName, content] of expected) {
    const actual = await fs.readFile(path.join(OUTPUT_DIR, outputName), 'utf8');
    if (actual !== content) {
      throw new Error(`Generated docs are stale: ${outputName}`);
    }
  }
}

async function recursiveMarkdownFiles(directory, relative = '') {
  const entries = await fs.readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const relativePath = normalizeRepoPath(path.join(relative, entry.name));
    if (entry.isDirectory()) {
      files.push(...await recursiveMarkdownFiles(path.join(directory, entry.name), relativePath));
    } else if (entry.isFile() && entry.name.endsWith('.md')) {
      files.push(relativePath);
    }
  }
  return files;
}

async function writeGenerated(expected) {
  await fs.rm(OUTPUT_DIR, { recursive: true, force: true });
  await fs.mkdir(OUTPUT_DIR, { recursive: true });
  for (const [outputName, content] of expected) {
    await fs.mkdir(path.dirname(path.join(OUTPUT_DIR, outputName)), { recursive: true });
    await fs.writeFile(path.join(OUTPUT_DIR, outputName), content, 'utf8');
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const expected = await expectedPages();
  if (CHECK_MODE) {
    await checkGenerated(expected);
    console.log(`Verified ${expected.size} generated Starlight pages.`);
  } else {
    await writeGenerated(expected);
    console.log(`Generated ${expected.size} Starlight pages.`);
  }
}
