# GPQA Diamond result

<section class="reader-section cool finding" aria-label="Primary GPQA Diamond finding">

<p class="kicker">The headline</p>

## Splash scored 54.55% on GPQA Diamond

The local Splash / Qwen3.8 setup finished **198 of 198** questions with **0 request errors** and answered just over half correctly. This is a reviewed result for this exact local setup.

</section>

<section class="reader-section cool" aria-label="Why this test matters for a helper">

<p class="kicker">Why this test</p>

## Why it matters for a helper

A helper that reads code and documents for you has to reason carefully, not just match words. GPQA Diamond checks that skill. Experts in biology, chemistry, and physics wrote its 198 multiple-choice questions to be hard to answer with a quick web search.

A result just over half tells us the model can reason through hard material. It does not make it an expert on the hardest problems, and it says nothing about coding. The [coding test](swe-bench-verified.md) covers that.

</section>

<section class="reader-section cool" aria-label="GPQA model card summary">

<p class="kicker">The result</p>

## Evaluation card

The key facts of the run on one card. Open the details for the exact settings.

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

<p class="kicker">Context</p>

## Benchmark comparison

How do cloud models do on the same test? The explorer below shows every published score we checked, with its source. Those scores came from different prompts, thinking budgets, settings, and scoring, so they are **directional context**: useful to show scale, not a ranking or an exact gap. Our row stays pinned at the top.

<!-- benchmark-explorer:gpqa-diamond -->

[Inspect the primary sources and transcription status](sources.md#gpqa-diamond-sources)

</section>

<section class="reader-section cool" aria-label="GPQA runtime performance">

<p class="kicker">Speed</p>

## Runtime performance

How fast the test machine worked through the test. Speed describes this run, not how smart the model is.

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

<p class="kicker">Limits</p>

## Run record and limitations

What this result cannot tell you:

- **Public test:** the questions are public, so the model may have seen some of them during training.
- **Output cap:** 81 of 198 answers hit the 4,096-token length limit, which may have cut some reasoning short.
- **One skill:** this test does not measure coding, tool use, long documents, safety, or everyday reliability.
- **No protocol-matched frontier delta:** we do not report an exact gap to cloud models, because their tests were run differently.
- **Private evidence stays private:** prompts, answers, reasoning, local paths, and machine details stay out of the public repository.

[Inspect the reviewed public result and provenance hashes](https://github.com/racecraft-lab/splash-evals/blob/main/results/public/gpqa-diamond-splash-local-2026-09-20.json)

</section>
