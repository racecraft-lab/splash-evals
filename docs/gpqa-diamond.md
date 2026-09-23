# GPQA Diamond result

<div class="finding" role="region" aria-label="Primary GPQA Diamond finding">

## Splash scored 54.55% on GPQA Diamond

The local Splash / Qwen3.8 deployment completed **198 of 198** questions with **0 request errors**. This is a reviewed capability result for this exact local condition.

</div>

<section class="reader-section cool" aria-label="GPQA model card summary">

## Evaluation card

<dl class="evaluation-summary">
  <div><dt>System tested</dt><dd><strong>Splash / Qwen3.8</strong><span>Local LM Studio alias: <code>racecraft-splash-local</code></span></dd></div>
  <div><dt>Benchmark</dt><dd><strong>GPQA Diamond</strong><span>Full 198-question evaluation</span></dd></div>
  <div class="primary-metric"><dt>Accuracy</dt><dd><strong>54.55%</strong><span>54.6% rounded to one decimal</span></dd></div>
  <div><dt>Completion</dt><dd><strong>198 / 198</strong><span>198 requested, 198 succeeded, 0 errored</span></dd></div>
</dl>

<details class="evaluation-details">
<summary>View run configuration and evidence boundary</summary>

<dl class="evaluation-metadata">
  <div><dt>Dataset and scope</dt><dd>GPQA Diamond via EvalScope's built-in adapter, evaluation version <code>v1.0</code>; <code>train</code> split, <code>default</code> subset, zero-shot.</dd></div>
  <div><dt>Reasoning condition</dt><dd>OpenAI-compatible <code>reasoning_effort: medium</code>.</dd></div>
  <div><dt>Generation</dt><dd>Maximum 4,096 output tokens, batch size 1, retries 0.</dd></div>
  <div><dt>Runner</dt><dd>EvalScope 1.12.0 through LM Studio's local OpenAI-compatible endpoint.</dd></div>
  <div><dt>Public evidence</dt><dd>Aggregate result and provenance hashes only; no prompts, responses, or reasoning traces.</dd></div>
</dl>

</details>

[Read the GPQA method](methodology.md#gpqa-diamond-method)

</section>

<section class="reader-section warm" aria-label="GPQA benchmark comparison">

## Benchmark comparison

The explorer below is generated from the reviewed public result and source-verified records in `references/frontier/`. It shows one source-labeled observation per record. Different prompts, reasoning budgets, sampling, answer extraction, trial counts, dataset revisions, or item manifests mean the external values are **directional context**, not a protocol-matched ranking or exact gap.

<!-- benchmark-explorer:gpqa-diamond -->

[Inspect the primary sources and transcription status](sources.md#gpqa-diamond-sources)

</section>

<section class="reader-section cool" aria-label="GPQA runtime performance">

## Runtime performance

Performance describes this local run, not model quality.

<dl class="performance-summary">
  <div><dt>Wall-clock time</dt><dd><strong>2 h 25 m 16 s</strong><span>Complete 198-question run</span></dd></div>
  <div class="primary-metric"><dt>Output throughput</dt><dd><strong>64.13 tokens/s</strong><span>Average across the run</span></dd></div>
  <div><dt>Tokens processed</dt><dd><strong>612,907</strong><span>Total input and output volume</span></dd></div>
</dl>

<details class="performance-details">
<summary>View latency and output details</summary>

<dl class="performance-metadata">
  <div><dt>Average request latency</dt><dd>43.95 s</dd></div>
  <div><dt>Median request latency</dt><dd>41.80 s</dd></div>
  <div><dt>90th percentile latency</dt><dd>72.49 s</dd></div>
  <div><dt>Average output length</dt><dd>2,818 tokens</dd></div>
</dl>

Time to first token and time per output token were unavailable from this compatible endpoint, so they are not estimated here.

</details>

</section>

<section class="reader-section warm" aria-label="GPQA run record and limitations">

## Run record and limitations

- **Public, not private held-out:** this run cannot rule out training-data contamination.
- **Output cap:** 81 of 198 responses reached the 4,096-token output ceiling.
- **One benchmark:** this result does not measure coding, tool use, long context, safety, or general reliability.
- **No protocol-matched frontier delta:** external records stay directional until every material protocol field aligns.
- **Public evidence boundary:** prompts, responses, reasoning, local paths, and runtime identifiers remain outside Git.

[Inspect the reviewed public result and provenance hashes](https://github.com/racecraft-lab/splash-evals/blob/main/results/public/gpqa-diamond-splash-local-2026-09-20.json)

</section>
