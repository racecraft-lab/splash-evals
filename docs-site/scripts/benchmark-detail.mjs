// Render only normalized public records; the exporter owns scientific validation.
const html = (value) => String(value).replace(/[&<>"']/g, (character) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
})[character]);

const valueOrUnknown = (value) => value === null || value === undefined ? 'Not reported' : html(value);

export function codingIntro(view) {
  return {
    summary: `We asked the model to fix 500 real bugs from open-source projects, working on its own with a terminal and the projects' tests. The result: ${view.detail}.`,
    variant: 'results',
    caption: view.capability
      ? 'Our reviewed result. Published cloud-model scores use different agents and settings, so they are context, not a ranking.'
      : 'No local capability score is published. External records use different agents and testing conditions.',
    result: {
      score: view.label,
      metric: 'SWE-bench Verified resolved tasks',
      completion: view.completion,
    },
  };
}

function item(label, value, note = '', className = '') {
  return `<div${className ? ` class="${className}"` : ''}><dt>${html(label)}</dt><dd><strong>${html(value)}</strong>${note ? `<span>${html(note)}</span>` : ''}</dd></div>`;
}

function row(label, value) {
  return `<div><dt>${html(label)}</dt><dd>${value}</dd></div>`;
}

const present = (value) => value !== null && value !== undefined;

function duration(seconds) {
  const whole = Math.round(seconds);
  const parts = [[Math.floor(whole / 3600), 'h'], [Math.floor((whole % 3600) / 60), 'm'], [whole % 60, 's']];
  return parts.filter(([amount], index) => amount > 0 || index === 2).map(([amount, unit]) => `${amount} ${unit}`).join(' ');
}

// The SWE-bench page mirrors the GPQA page: headline, why this test (docs), evaluation card,
// benchmark comparison (docs), runtime performance, and run record and limitations.
export function renderCodingOutcome(record, view) {
  const measured = view.capability;
  const title = measured
    ? `Splash scored ${view.label} on SWE-bench Verified`
    : ({ pending: 'No local Splash score is published yet', qualification: 'Local qualification is recorded',
      invalid: 'The local result needs review', partial: 'The local run is incomplete', failed: 'The local run failed' })[view.status];
  const descriptions = {
    pending: 'The local evaluation is pending benchmark-specific qualification and a full reviewed run.',
    qualification: 'These checks establish compatibility and resource use. They do not measure performance on the 500 Verified tasks.',
    invalid: 'This run cannot support a capability result. Its limitations and available completion counts are shown below.',
    partial: 'Some tasks are unfinished or encountered errors. No headline coding score is shown.',
    failed: 'The run did not produce a usable capability result. Available execution evidence is retained for review.',
    complete: 'The local Splash / Qwen3.8 setup worked through all 500 real bugs and fixed about 7 in 10. This is a reviewed result for this exact local setup.',
  };
  const counts = record?.counts;
  const outcomes = record?.task_outcomes;
  const accounting = outcomes
    ? `<p class="evaluation-caveat">${html(outcomes.resolved)} resolved / ${html(counts.requested)} requested · ${html(outcomes.unresolved)} unresolved · ${html(outcomes.model_failure)} model failures · ${html(outcomes.infrastructure_error)} infrastructure errors.</p>`
    : '';
  return `<section class="reader-section cool finding ${measured ? 'measured' : 'pending'}" aria-label="SWE-bench Verified status">

<p class="kicker">The headline</p>

## ${html(title)}

${descriptions[view.status]}

${accounting}

</section>`;
}

export function renderCodingCard(record, view) {
  const measured = view.capability;
  const counts = record?.counts;
  const configuration = record?.tested_configuration;
  const completion = counts
    ? `${measured ? 'Reviewed full run · ' : ''}${counts.succeeded} succeeded, ${counts.errored} errored${measured ? ' (counted unresolved)' : ''}`
    : 'Qualification and full run remain';
  const artifacts = record
    ? Object.entries(record.provenance.artifacts).map(([name, digest]) => `${html(name)}: <code>${html(digest)}</code>`).join('<br />')
    : 'Not reported';
  return `<section class="reader-section cool" aria-label="SWE-bench evaluation card">

<p class="kicker">The result</p>

## Evaluation card

The key facts of the run on one card. Open the details for the exact settings.

<dl class="evaluation-summary">
${item('System tested', record?.evaluator.model_label ?? 'Splash / Qwen3.8', 'Local LM Studio deployment')}
${item('Benchmark', 'SWE-bench Verified', record?.identity.variant === 'qualification-disjoint' ? 'Disjoint qualification tasks only' : 'Full 500-task evaluation')}
${item('Resolved', measured ? view.label : 'Not available', measured ? 'Bugs fixed out of all 500 tasks' : 'No capability percentage is reported', 'primary-metric')}
${item('Completion', counts ? `${counts.requested} / ${counts.requested}` : 'Not available', completion)}
</dl>

<details class="evaluation-details">
<summary>View run configuration and evidence boundary</summary>

<dl class="evaluation-metadata">
${row('Dataset', `SWE-bench Verified, revision <code>${valueOrUnknown(record?.identity.dataset_revision)}</code>`)}
${row('Agent and tools', `${valueOrUnknown(configuration?.harness)} ${configuration?.harness_version ? html(configuration.harness_version) : ''}`.trim())}
${row('Reasoning condition', valueOrUnknown(configuration?.reasoning_effort))}
${row('Generation', `Maximum ${valueOrUnknown(configuration?.max_output_tokens)} output tokens, temperature ${valueOrUnknown(configuration?.temperature)}, top-p ${valueOrUnknown(configuration?.top_p)}, ${valueOrUnknown(configuration?.attempts_per_task)} attempt per task.`)}
${row('Evaluator', `${valueOrUnknown(record?.evaluator.name)} ${record?.evaluator.version ? html(record.evaluator.version) : ''}`.trim())}
${row('Public evidence', `Aggregate result and provenance hashes only; no tasks, trajectories, patches, or logs.<br />${artifacts}`)}
</dl>

${measured ? 'This score applies to the recorded model, agent, tools, and testing conditions. Matching benchmark names does not establish a matched comparison.' : 'A setup check or qualification run is not a SWE-bench capability result. A local score requires a full reviewed 500-task evaluation.'}

</details>

<p><a href="/splash-evals/methodology/#swe-bench-verified-method">Read the SWE-bench method</a></p>

</section>`;
}

export function renderCodingRuntime(record, view) {
  const runtime = record?.runtime;
  const measuredValue = (value, unit) => (present(value) ? `${value} ${unit}` : 'Not reported');
  const tokens = present(runtime?.total_input_tokens) && present(runtime?.total_output_tokens)
    ? runtime.total_input_tokens + runtime.total_output_tokens
    : null;
  const any = runtime && Object.values(runtime).some(present);
  const limitations = record?.limitations ?? [
    'Qualification and a full local run are still required.',
    'External observations use different agents, settings or testing conditions.',
    'Raw tasks, trajectories, patches and logs remain private.',
  ];
  return `<section class="reader-section cool" aria-label="SWE-bench runtime performance">

<p class="kicker">Speed</p>

## Runtime performance

How fast the test machine worked through the test. Speed describes this run, not how smart the model is.
${view.status === 'qualification' ? '<p>These measurements describe the qualification sample only.</p>' : ''}
${any ? '' : '<p>No reviewed runtime measurements are available yet.</p>'}

<dl class="performance-summary">
${item('Wall-clock time', present(runtime?.duration_seconds) ? duration(runtime.duration_seconds) : 'Not reported', present(runtime?.duration_seconds) ? `${runtime.duration_seconds} s total` : 'Not in the reviewed record')}
${item('Output throughput', measuredValue(runtime?.output_tokens_per_second, 'tokens/s'), present(runtime?.output_tokens_per_second) ? 'Average across the run' : 'Not in the reviewed record', 'primary-metric')}
${item('Tokens processed', present(tokens) ? tokens.toLocaleString('en-US') : 'Not reported', present(tokens) ? 'Total input and output volume' : 'Not in the reviewed record')}
</dl>

<details class="performance-details">
<summary>View latency and output details</summary>

<dl class="performance-metadata">
${row('Average request latency', html(measuredValue(runtime?.average_latency_seconds, 's')))}
${row('Median request latency', html(measuredValue(runtime?.median_latency_seconds, 's')))}
${row('90th percentile latency', html(measuredValue(runtime?.p90_latency_seconds, 's')))}
${row('Output tokens', html(measuredValue(runtime?.total_output_tokens, 'tokens')))}
</dl>

Values not in the reviewed record are shown as not reported. They are never estimated or set to zero.

</details>

</section>

<section class="reader-section warm" aria-label="SWE-bench run record and limitations">

<p class="kicker">Limits</p>

## Run record and limitations

What this result cannot tell you, and every change from the standard setup:

<ul>${limitations.map((limitation) => `<li>${html(limitation)}</li>`).join('')}</ul>

<p><a href="https://github.com/racecraft-lab/splash-evals/blob/main/results/public/swe-bench-verified-splash-local-2026-09-28.json">Inspect the reviewed public result and provenance hashes</a></p>

</section>`;
}
