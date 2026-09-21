# What we found

<div class="finding" role="region" aria-label="Primary GPQA Diamond finding">

## Splash scored 54.55% on GPQA Diamond

The local Splash / Qwen3.8 deployment completed **198 of 198** questions with **0 request errors**. This is the first public capability result from this repository.

</div>

<section class="reader-section cool" aria-label="Model card summary">

## Evaluation card

| Field | Reviewed public record |
|---|---|
| Model under test | Splash / Qwen3.8, served locally by LM Studio as `racecraft-splash-local` |
| Benchmark | GPQA Diamond, EvalScope built-in adapter, evaluation version `v1.0` |
| Scope | Full 198-question `train` split, `default` subset, zero-shot |
| Result | **54.55% accuracy** (54.6% rounded to one decimal place) |
| Execution | 198 requested, 198 succeeded, 0 errored |
| Reasoning condition | OpenAI-compatible `reasoning_effort: medium` |
| Generation | Maximum 4,096 output tokens, batch size 1, retries 0 |
| Runner | EvalScope 1.12.0 through LM Studio's local OpenAI-compatible endpoint |
| Public evidence | Aggregate result and provenance hashes only; no prompts, responses, or reasoning traces |

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

| Model / condition | GPQA Diamond | Evidence status |
|---|---:|---|
| **Splash / Qwen3.8 · local LM Studio · medium effort** | **54.6%** | Measured here; 198/198 completed |
| GPT-5.6 Sol | 94.6% | OpenAI-reported, directional |
| GPT-5.6 Terra | 92.9% | OpenAI-reported, directional |
| GPT-5.6 Luna | 92.3% | OpenAI-reported, directional |
| GPT-5.5 | 93.6% | OpenAI-reported, directional |
| Claude Sonnet 4 · Anthropic provider | 70.0% | Without extended thinking; provider-reported, directional |
| Claude Sonnet 4 · Epoch independent | 78% | Direct model scorecard, rounded; directional |
| Claude Opus 4 · Anthropic provider | 74.9% | Without extended thinking; provider-reported, directional |
| Claude Opus 4 · Epoch independent | 76% | Direct model scorecard, rounded; directional |
| Claude Sonnet 4.6 · Epoch independent | 87% | Direct model scorecard, rounded; directional |
| Claude Sonnet 4.6 · Anthropic provider | 89.9% | Adaptive thinking at max effort, 10-trial average; directional |
| Claude Opus 4.6 · Epoch independent | 91% | Direct model scorecard, rounded; directional |
| Claude Opus 4.6 · Anthropic provider | 91.3% | Adaptive-thinking/max-effort comparison condition; directional |
| Claude Opus 4.7 · Anthropic provider | 94.2% | Provider-reported, directional |
| Claude Opus 4.7 · Epoch independent | 90% | Direct model scorecard, rounded; directional |
| Claude Opus 4.8 · Epoch independent | 91% | Direct model scorecard, rounded; directional |
| Claude Opus 4.8 · Anthropic provider | 93.6% | 25-trial average; provider-reported, directional |
| Claude Opus 4.8 · OpenAI cross-provider | 92.0% | OpenAI comparison table, directional |
| Claude Sonnet 5 · Epoch independent | 91% | Direct model scorecard, rounded; Anthropic does not publish a GPQA figure |
| Claude Opus 5 · Epoch independent | 94% | Direct model scorecard, rounded; Anthropic does not publish a GPQA figure |
| Claude Opus 5 · OpenAI cross-provider | 93.7% | Anthropic's release page does not publish a GPQA figure |

Repeated model names are separate observations under different conditions. They demonstrate why this is a source-labeled comparison board rather than a ranking.

[Inspect the primary sources and transcription status](sources.md)

</section>

<section class="reader-section cool" aria-label="Runtime performance">

## Runtime performance

Performance describes this local run, not model quality.

| Measure | Result |
|---|---:|
| Total wall-clock time | 2 h 25 m 16 s |
| Average request latency | 43.95 s |
| Median request latency | 41.80 s |
| 90th percentile latency | 72.49 s |
| Average output throughput | 64.13 tokens/s |
| Average output length | 2,818 tokens |
| Total tokens processed | 612,907 |

Time to first token and time per output token were not available from this compatible endpoint, so they are not estimated here.

</section>

<section class="reader-section warm" aria-label="Run record and limitations">

## Run record and limitations

- **Public, not private held-out:** GPQA Diamond is a widely available benchmark. This run measures the local system on that public set; it cannot rule out training-data contamination.
- **Output cap:** 81 of 198 responses reached the 4,096-token output ceiling. This may have affected some answers and is part of the tested condition.
- **One benchmark:** The result says nothing by itself about coding, instruction following, tool use, long context, safety, or general reliability.
- **No protocol-matched frontier delta:** Publisher figures remain directional until the dataset revision, item manifest, prompt, attempts, reasoning budget, and scorer are aligned.
- **No raw evidence published:** Benchmark prompts, responses, reasoning, local paths, and runtime identifiers remain outside Git.

Public provenance digests:

| Artifact | SHA-256 |
|---|---|
| EvalScope report | `5863ba54c2ec07cb1e508585d8e2129e863e1573fda2a23cf2aae241bec5e56a` |
| Task configuration | `f69e7d5ccead9ed80ebcf77eb26f7e5cdfe3e24c3483b9d06db5ef30863420e2` |
| Progress record | `f71604e1b66511b152428fb49e07334602309aaa70afe233b49347ec4f542c69` |

</section>
