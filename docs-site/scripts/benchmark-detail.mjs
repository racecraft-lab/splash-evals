// Render only normalized public records; the exporter owns scientific validation.
const html = (value) => String(value).replace(/[&<>"']/g, (character) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
})[character]);

const valueOrUnknown = (value) => value === null || value === undefined ? 'Not reported' : html(value);

export function codingIntro(view) {
  return {
    summary: `SWE-bench Verified tests whether an agent can resolve real repository issues. ${view.detail}.`,
    variant: 'results',
    caption: view.capability
      ? 'A reviewed local result. External records use different agents and testing conditions.'
      : 'No local capability score is published. External records use different agents and testing conditions.',
    result: {
      score: view.label,
      metric: 'SWE-bench Verified resolved tasks',
      completion: view.completion,
    },
  };
}

function item(label, value, note = '') {
  return `<div><dt>${html(label)}</dt><dd><strong>${html(value)}</strong>${note ? `<span>${html(note)}</span>` : ''}</dd></div>`;
}

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
    complete: 'This is the reviewed result for the full 500-task repository-repair benchmark. Published frontier observations provide context under different testing conditions.',
  };
  const counts = record?.counts;
  const outcomes = record?.task_outcomes;
  const accounting = outcomes
    ? `<p class="evaluation-caveat">${html(outcomes.resolved)} resolved / ${html(counts.requested)} requested · ${html(outcomes.unresolved)} unresolved · ${html(outcomes.model_failure)} model failures · ${html(outcomes.infrastructure_error)} infrastructure errors.</p>`
    : '';
  const completion = counts ? `${counts.succeeded} completed · ${counts.errored} errors · ${counts.requested} requested` : 'Qualification and full run remain';
  return `<div class="finding ${measured ? 'measured' : 'pending'}" role="region" aria-label="SWE-bench Verified status">

## ${html(title)}

${descriptions[view.status]}

${accounting}

</div>

<section class="reader-section cool" aria-label="SWE-bench evaluation card">

## Evaluation card

<dl class="evaluation-summary">
${item('System under study', record?.evaluator.model_label ?? 'Splash / Qwen3.8', 'Local LM Studio deployment')}
${item('Benchmark', 'SWE-bench Verified', record?.identity.variant === 'qualification-disjoint' ? 'Disjoint qualification tasks only' : '500 repository-repair tasks')}
${item('Local score', measured ? view.label : 'Not available', measured ? 'Resolved tasks' : 'No capability percentage is reported')}
${item('Status', measured ? 'Reviewed full run' : view.label, completion)}
</dl>

<aside class="method-note" role="note">${measured ? 'This score applies to the recorded model, agent, tools and testing conditions. Matching benchmark names does not establish a matched comparison.' : 'A setup check or qualification run is not a SWE-bench capability result. A local score requires a full reviewed 500-task evaluation.'}</aside>

</section>`;
}

export function renderCodingRuntime(record, view) {
  const runtime = record?.runtime;
  const configuration = record?.tested_configuration;
  const metrics = [
    ['Total time', runtime?.duration_seconds, 's'],
    ['Average request time', runtime?.average_latency_seconds, 's'],
    ['Output rate', runtime?.output_tokens_per_second, 'tokens/s'],
    ['Input tokens', runtime?.total_input_tokens, 'tokens'],
    ['Output tokens', runtime?.total_output_tokens, 'tokens'],
  ].filter(([, value]) => value !== null && value !== undefined);
  const metricsHtml = metrics.length
    ? `<dl class="evaluation-summary">${metrics.map(([label, value, unit]) => item(label, `${value} ${unit}`)).join('\n')}</dl>`
    : '<p>No reviewed runtime measurements are available yet.</p>';
  const limitations = record?.limitations ?? [
    'Qualification and a full local run are still required.',
    'External observations use different agents, settings or testing conditions.',
    'Raw tasks, trajectories, patches and logs remain private.',
  ];
  const technical = record ? `<details class="comparison-record">
<summary>Tested settings and provenance</summary>
<dl class="evaluation-summary">
${item('Evaluator', record.evaluator.name, record.evaluator.version ?? 'Version not reported')}
${item('Harness', configuration?.harness ?? 'Not reported', configuration?.harness_version ?? '')}
${item('Reasoning effort', configuration?.reasoning_effort ?? 'Not reported')}
${item('Maximum output', configuration?.max_output_tokens ?? 'Not reported')}
${item('Temperature', configuration?.temperature ?? 'Not reported')}
${item('Top-p', configuration?.top_p ?? 'Not reported')}
${item('Attempts per task', configuration?.attempts_per_task ?? 'Not reported')}
</dl>
<p>Dataset revision: <code>${valueOrUnknown(record.identity.dataset_revision)}</code></p>
<ul>${Object.entries(record.provenance.artifacts).map(([name, digest]) => `<li>${html(name)}: <code>${html(digest)}</code></li>`).join('')}</ul>
</details>` : '';
  return `<section class="reader-section cool" aria-label="SWE-bench runtime summary">

## Runtime summary

${view.status === 'qualification' ? '<p>These measurements describe the qualification sample only.</p>' : ''}
${metricsHtml}

</section>

<section class="reader-section warm" aria-label="SWE-bench limitations and next gate">

## Limitations and tested conditions

<ul>${limitations.map((limitation) => `<li>${html(limitation)}</li>`).join('')}</ul>
${technical}

</section>`;
}
