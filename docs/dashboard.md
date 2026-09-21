# What we found

<div class="finding" role="region" aria-label="Primary GPQA Diamond finding">

## Splash scored 54.55% on GPQA Diamond

The local Splash / Qwen3.8 deployment completed **198 of 198** questions with **0 request errors**. This is the first public capability result from this repository.

</div>

<section class="reader-section cool" aria-label="Model card summary">

## Evaluation card

The essentials are up front. Open the run details when you need to inspect the exact test condition.

<dl class="evaluation-summary">
  <div>
    <dt>System tested</dt>
    <dd><strong>Splash / Qwen3.8</strong><span>Local LM Studio alias: <code>racecraft-splash-local</code></span></dd>
  </div>
  <div>
    <dt>Benchmark</dt>
    <dd><strong>GPQA Diamond</strong><span>Full 198-question evaluation</span></dd>
  </div>
  <div class="primary-metric">
    <dt>Accuracy</dt>
    <dd><strong>54.55%</strong><span>54.6% rounded to one decimal</span></dd>
  </div>
  <div>
    <dt>Completion</dt>
    <dd><strong>198 / 198</strong><span>198 requested, 198 succeeded, 0 errored</span></dd>
  </div>
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

[Read the complete method](methodology.md)

</section>

<section class="reader-section warm" aria-label="Benchmark comparison">

## Benchmark comparison

These are reported **GPQA Diamond accuracy** figures. The Splash row was measured by this project. External rows are explicitly labeled as provider-reported, cross-provider, or independently evaluated by Epoch AI. Their prompt, sampling, answer extraction, reasoning budget, trial count, dataset revision, or exact item manifest may differ. They do not support a protocol-matched delta.

Source labels matter:

- **Measured here** is the reviewed local EvalScope result.
- **Provider-reported** is a model developer's own evaluation.
- **Cross-provider** is one developer reporting another developer's model.
- **Epoch independent** is an independently run evaluation shown with Epoch's displayed precision.

Do not subtract, average, or order these values to claim an exact gap or ranking.

<div class="frontier-explorer" data-gpqa-explorer data-sources-url="../sources/#where-the-numbers-come-from" aria-labelledby="gpqa-explorer-title" aria-describedby="gpqa-explorer-context">
  <div class="explorer-heading">
    <p class="explorer-eyebrow">Interactive evidence view</p>
    <h3 id="gpqa-explorer-title">Explore the reported scores</h3>
    <p id="gpqa-explorer-context">Scan every reported score on one 0–100 scale. Splash stays pinned as the local reference while the filters change which source-labeled observations appear.</p>
  </div>
  <div class="explorer-controls" data-explorer-controls hidden></div>
  <div class="explorer-plot" data-explorer-plot>
    <p>The complete source-labeled comparison is available in the expandable source table below.</p>
  </div>
  <div class="explorer-detail" data-explorer-detail hidden aria-live="polite"></div>
</div>

<details class="comparison-record">
<summary>View the complete 21-row source table</summary>

| Model / condition | GPQA Diamond | Evidence status |
|---|---:|---|
| **Splash / Qwen3.8 · local LM Studio · medium effort** | **54.6%** | Measured here; 198/198 completed |
| [GPT-5.6 Sol](https://openai.com/index/gpt-5-6/) | 94.6% | OpenAI-reported, directional |
| [GPT-5.6 Terra](https://openai.com/index/gpt-5-6/) | 92.9% | OpenAI-reported, directional |
| [GPT-5.6 Luna](https://openai.com/index/gpt-5-6/) | 92.3% | OpenAI-reported, directional |
| [GPT-5.5](https://openai.com/index/introducing-gpt-5-5/) | 93.6% | OpenAI-reported, directional |
| [Claude Sonnet 4 · Anthropic provider](https://www.anthropic.com/news/claude-4) | 70.0% | Without extended thinking; provider-reported, directional |
| [Claude Sonnet 4 · Epoch independent](https://epoch.ai/models/claude-sonnet-4) | 78% | Direct model scorecard, rounded; directional |
| [Claude Opus 4 · Anthropic provider](https://www.anthropic.com/news/claude-4) | 74.9% | Without extended thinking; provider-reported, directional |
| [Claude Opus 4 · Epoch independent](https://epoch.ai/models/claude-opus-4) | 76% | Direct model scorecard, rounded; directional |
| [Claude Sonnet 4.6 · Epoch independent](https://epoch.ai/models/claude-sonnet-4-6) | 87% | Direct model scorecard, rounded; directional |
| [Claude Sonnet 4.6 · Anthropic provider](https://www-cdn.anthropic.com/bbd8ef16d70b7a1665f14f306ee88b53f686aa75.pdf) | 89.9% | Adaptive thinking at max effort, 10-trial average; directional |
| [Claude Opus 4.6 · Epoch independent](https://epoch.ai/models/claude-opus-4-6) | 91% | Direct model scorecard, rounded; directional |
| [Claude Opus 4.6 · Anthropic provider](https://www-cdn.anthropic.com/bbd8ef16d70b7a1665f14f306ee88b53f686aa75.pdf) | 91.3% | Adaptive-thinking/max-effort comparison condition; directional |
| [Claude Opus 4.7 · Anthropic provider](https://www-cdn.anthropic.com/037f06850df7fbe871e206dad004c3db5fd50340/Claude%20Opus%204.7%20System%20Card.pdf) | 94.2% | Provider-reported, directional |
| [Claude Opus 4.7 · Epoch independent](https://epoch.ai/models/claude-opus-4-7) | 90% | Direct model scorecard, rounded; directional |
| [Claude Opus 4.8 · Epoch independent](https://epoch.ai/models/claude-opus-4-8) | 91% | Direct model scorecard, rounded; directional |
| [Claude Opus 4.8 · Anthropic provider](https://www-cdn.anthropic.com/0b4915911bb0d19eca5b5ee635c80fef830a37ea.pdf) | 93.6% | 25-trial average; provider-reported, directional |
| [Claude Opus 4.8 · OpenAI cross-provider](https://openai.com/index/gpt-5-6/) | 92.0% | OpenAI comparison table, directional |
| [Claude Sonnet 5 · Epoch independent](https://epoch.ai/models/claude-sonnet-5) | 91% | Direct model scorecard, rounded; Anthropic does not publish a GPQA figure |
| [Claude Opus 5 · Epoch independent](https://epoch.ai/models/claude-opus-5) | 94% | Direct model scorecard, rounded; Anthropic does not publish a GPQA figure |
| [Claude Opus 5 · OpenAI cross-provider](https://openai.com/index/gpt-6-astra/) | 93.7% | Anthropic's release page does not publish a GPQA figure |

Repeated model names are separate observations under different conditions. They demonstrate why this is a source-labeled comparison board rather than a ranking.

</details>

[Inspect the primary sources and transcription status](sources.md)

</section>

<section class="reader-section cool" aria-label="Runtime performance">

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

Time to first token and time per output token were not available from this compatible endpoint, so they are not estimated here.

</details>

</section>

<section class="reader-section warm" aria-label="Run record and limitations">

## Run record and limitations

- **Public, not private held-out:** GPQA Diamond is a widely available benchmark. This run measures the local system on that public set; it cannot rule out training-data contamination.
- **Output cap:** 81 of 198 responses reached the 4,096-token output ceiling. This may have affected some answers and is part of the tested condition.
- **One benchmark:** The result says nothing by itself about coding, instruction following, tool use, long context, safety, or general reliability.
- **No protocol-matched frontier delta:** Publisher figures remain directional until the dataset revision, item manifest, prompt, attempts, reasoning budget, and scorer are aligned.
- **No raw evidence published:** Benchmark prompts, responses, reasoning, local paths, and runtime identifiers remain outside Git.

[Inspect the reviewed public result and provenance hashes](https://github.com/racecraft-lab/splash-evals/blob/main/results/public/gpqa-diamond-splash-local-2026-09-20.json)

</section>
