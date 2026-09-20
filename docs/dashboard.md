# What we found

**How close is Splash to the frontier? Not yet measured.** This page tracks the comparisons we want to make, the evidence available, and the gaps still to fill. No capability score or frontier ranking is available yet.

<section class="reader-section cool" aria-label="Local model evaluation card">

## Splash / Qwen3.8 at a glance

This evaluation card describes **the local deployment under study**, not a complete upstream model card. It keeps identity, measurement status, and limitations together. It is not a certification or an overall model rating.

| Field | Public record |
|---|---|
| Deployment under study | Splash / Qwen3.8 through local LM Studio |
| Research objective | Measure the gap to current and previous-generation frontier models, task by task |
| Capability evidence | Not yet measured on a held-out, qualified test set |
| Exact artifact and runtime configuration | Required in an eligible capability report; the display name alone is not a reproducible model identity |
| Speed and resource use | Not yet reported as approved public measurements |
| Direct comparison | Not yet available; matching benchmark and scoring conditions are required |
| Unsupported claims | A frontier-model ranking, broad ability claim, or claim of improvement |

[Understand the test method](methodology.md)

</section>

## Benchmark comparison board

### Current and previous-generation targets

Roster checked **2026-09-20**. These are planned comparisons, not completed runs or verified benchmark-score records. The primary target is the requested Sonnet, Opus, and GPT group; original Claude 4 releases remain additional historical anchors.

| Family | Newer requested model | Previous-generation anchor | Splash gap |
|---|---|---|---|
| Claude Sonnet | [Sonnet 5](https://www.anthropic.com/news/claude-sonnet-5) | Sonnet 4.6; original Sonnet 4 as older context | Not yet measured |
| Claude Opus | [Opus 5](https://www.anthropic.com/news/claude-opus-5) | Opus 4.8; original Opus 4 as older context | Not yet measured |
| OpenAI GPT | [GPT‑5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol) | [GPT‑5.5](https://developers.openai.com/api/docs/models/gpt-5.5) | Not yet measured |
| OpenAI GPT | [GPT‑5.6 Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra) | Not selected for this variant | Not yet measured |
| OpenAI GPT | [GPT‑5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna) | Not selected for this variant | Not yet measured |

The Claude release sources identify 4.6 and 4.8 as the immediate predecessors. **GPT‑5.6 Sol, Terra, and Luna are separate targets**, not interchangeable names or one combined score. Each needs its own model identifier, reasoning settings, test conditions, and results. Previous-generation anchors for Terra and Luna have not been selected. This requested group is **not the entire current frontier**: [OpenAI's catalog](https://developers.openai.com/api/docs/models) also lists GPT‑6 Astra, and the [Opus 5 release](https://www.anthropic.com/news/claude-opus-5) discusses Fable 5. Broader-frontier coverage still needs evidence review.

[What must happen before we can measure the gap](historical-frontier-comparison.md#the-next-study)

### Measurement status

**Splash benchmark scores are not yet measured.** The historical columns below show previously published reference numbers, not evaluations this project ran. No local score difference, ranking, or combined “AI score” is available.

<div class="evidence-track" role="group" aria-label="Comparison evidence status">
<div><strong>Requested frontier cohort</strong><span>Score and protocol review pending</span></div>
<div><strong>Local benchmark runs</strong><span>Not yet measured</span></div>
<div><strong>Fair comparison</strong><span>Not yet available</span></div>
</div>

Read each benchmark on its own. Different tasks, scoring methods, model versions, and reasoning settings must not be merged into one ranking. **Not yet measured is not zero.** “Not reported” means the source does not supply a field; “not comparable” means the test conditions do not support a score-to-score claim.

### Older reference archive

The following records predate the requested cohort. They remain useful historical context, but do not answer today's frontier-comparison question.

<section class="reader-section cool" aria-label="Reasoning benchmark references">

### Scientific reasoning · GPQA Diamond

Reported accuracy (%). These OpenAI appendix values are historical context only. Exact test metadata is incomplete, and the publisher's model-based answer extraction affects interpretation.

| Model / condition | Reported score | Source date and identity | Matched Splash / Qwen3.8 score |
|---|---:|---|---|
| GPT-4o | 46.0% | [2025-04-14 appendix](https://openai.com/index/gpt-4-1/); snapshot 2024-11-20 | Not yet measured |
| GPT-4.1 | 66.3% | [2025-04-14 appendix](https://openai.com/index/gpt-4-1/); exact snapshot not reported | Not yet measured |
| OpenAI o1 (high) | 75.7% | [2025-04-14 appendix](https://openai.com/index/gpt-4-1/); exact snapshot and reasoning budget not reported | Not yet measured |

**GPT-4o extraction caveat:** the source footnote describes a change from approximately 46% to 54% with model-based extraction. The catalog preserves the table's 46.0%; neither value supports a local delta here. Source publication dates are not measurement dates.

**Separate, unresolved split:** Claude 3.5 Sonnet (June 2024) has a [reported GPQA value of 59.4%](https://www.anthropic.com/news/the-case-for-targeted-regulation), but the catalog does not establish GPQA versus GPQA Diamond or the full protocol. It is deliberately excluded from the Diamond table.

</section>

<section class="reader-section warm" aria-label="Instruction following benchmark references">

### Instruction following · IFEval

Publisher-reported percentages. IFEval has multiple scoring variants; this source does not identify which variant these values use. Do not treat them as strict prompt-level accuracy or compare them with a local metric until that is resolved.

| Model / condition | Reported score | Source date and identity | Matched Splash / Qwen3.8 score |
|---|---:|---|---|
| GPT-4o | 81.0% | [2025-04-14 appendix](https://openai.com/index/gpt-4-1/); snapshot 2024-11-20 | Not yet measured |
| GPT-4.1 | 87.4% | [2025-04-14 appendix](https://openai.com/index/gpt-4-1/); exact snapshot not reported | Not yet measured |
| OpenAI o1 (high) | 92.2% | [2025-04-14 appendix](https://openai.com/index/gpt-4-1/); exact snapshot and reasoning budget not reported | Not yet measured |

</section>

<section class="reader-section cool" aria-label="Coding benchmark references">

### Coding · Aider polyglot

Reported success rates on the 225-case, six-language suite. **First attempt** and **after a permitted repair** are different metrics. The latter is not independent-sample pass@2. Rows are grouped by model and condition, not ranked; Aider versions and edit formats also differ.

| Model / condition | First attempt | After permitted repair | Recorded measurement date | Matched Splash / Qwen3.8 score |
|---|---:|---:|---|---|
| Claude 3.5 Sonnet · 2024-10-22 | 22.2% | 51.6% | 2025-01-17 | Not yet measured |
| Claude 3.7 Sonnet · 2025-02-19 · no thinking | 24.4% | 60.4% | 2025-02-24 | Not yet measured |
| Claude 3.7 Sonnet · 2025-02-19 · 32K thinking | 29.3% | 64.9% | 2025-02-24 | Not yet measured |
| Gemini 2.5 Pro Preview 03-25 | 40.9% | 72.9% | 2025-04-12 | Not yet measured |

Source: [Aider polyglot leaderboard](https://aider.chat/docs/leaderboards/). These are the dated records in this repository, not a claim about today's leaderboard. The small local setup check is incompatible with this full coding suite. An isolated coding environment and aligned task/scoring conditions are still required.

</section>

### Practical local work

| Area | Splash / Qwen3.8 result | Historical counterpart |
|---|---|---|
| Structured output and native tools | Not yet measured as capability evidence | No aligned dated reference in this catalog |
| Long context | Not yet measured as capability evidence | No aligned dated reference in this catalog |
| Repository engineering | Not yet measured as capability evidence | No aligned dated reference in this catalog |
| Speed and resource use | Not yet reported | No aligned local/historical measurement pair |

Before a Splash score appears, the public report must identify the model artifact and configuration, benchmark revision and split, sample count, scoring method, execution conditions, and uncertainty where applicable. A direct comparison additionally needs a documented protocol match. [Comparison requirements and limits](historical-frontier-comparison.md).

The numbers above come from the [checked-in historical catalog](../references/frontier/README.md) and its [source-number checks](../references/frontier/VERIFICATION.md). The presentation draws on [model-card reporting practice](https://huggingface.co/docs/hub/model-cards) and [HELM's separation of scenarios, metrics, and evaluation conditions](https://crfm.stanford.edu/2022/11/17/helm.html); it is not an official HELM or Hugging Face evaluation.

<section class="reader-section cool" aria-label="Open research questions">

## What is still unanswered?

- **Can it handle unfamiliar practical tasks?** We need a separate test set chosen before results are seen.
- **How close is it to current and previous-generation frontier models?** The requested target group is identified, but score/protocol review and matching local runs remain outstanding.
- **How fast or resource-efficient is it?** This public report does not include approved timing or memory measurements.

[Explore the historical comparison limits](historical-frontier-comparison.md) · [See the planned task families](benchmark-tasks.md)

</section>

## How to read a result

Completion tells you whether a test ran. Its purpose tells you what was being checked. Comparability tells you whether two scores can fairly be contrasted. These are separate questions: a completed run is not automatically capability evidence. Engineering checks are documented separately under [Methodology → Test-system validation](methodology.md#test-system-validation).

This dashboard is a static public summary, not a live connection to LM Studio. [What we publish and keep private](privacy.md).
