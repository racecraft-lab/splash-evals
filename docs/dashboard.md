# What we found

One local setup-check report is available. It shows that the test pipeline worked on reused cases. **Broader model ability has not yet been established.**

<!-- qualification-finding -->

<section class="reader-section cool" aria-label="Local model evaluation card">

## Splash / Qwen3.8 at a glance

This evaluation card describes **the tested local setup**, not a complete upstream model card. It follows the model-card practice of keeping identity, evaluation conditions, results, and limitations together. It is not a certification or an overall model rating.

| Field | Public record |
|---|---|
| Tested setup | Splash / Qwen3.8 through local LM Studio |
| What was evaluated | A small, reused synthetic set for runtime and answer-checker qualification |
| Checker | `builtin-exact-v1`; configured answer checks, not an independent assessment of general quality |
| Capability evidence | Not yet measured on a held-out, qualified test set |
| Exact artifact and runtime configuration | Not included in this published aggregate; the display name alone is not a reproducible model identity |
| Speed and resource use | Not yet reported as approved public measurements |
| Appropriate use of this result | Confirming that the tested request-and-check path worked on these cases |
| Not supported by this result | A frontier-model ranking, broad ability claim, or claim of improvement |

[Inspect the local report](local-pilot-results.md) · [Understand the test method](methodology.md)

</section>

## Benchmark comparison board

**Splash benchmark scores are not yet measured.** The historical columns below show previously published reference numbers, not evaluations this project ran. No local score difference, ranking, or combined “AI score” is available.

<div class="evidence-track" role="group" aria-label="Comparison evidence status">
<div><strong>Historical sources</strong><span>Recorded with limitations</span></div>
<div><strong>Local benchmark runs</strong><span>Not yet measured</span></div>
<div><strong>Fair comparison</strong><span>Not yet available</span></div>
</div>

Read each benchmark on its own. Different tasks, scoring methods, model versions, and reasoning settings must not be merged into one ranking. **Not yet measured is not zero.** “Not reported” means the source does not supply a field; “not comparable” means the test conditions do not support a score-to-score claim.

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

## Read the results

<section class="reader-section" aria-label="Available report">

### Local setup check

**Published · Setup qualification · Not eligible for historical comparison**

The report explains what ran, how answers were checked, and why the result is not a general capability score.

[Read the setup-check report](local-pilot-results.md)

</section>

<section class="reader-section cool" aria-label="Open research questions">

## What is still unanswered?

- **Can it handle unfamiliar practical tasks?** We need a separate test set chosen before results are seen.
- **How does it compare with older leading models?** We have dated reference records, but no matching eligible local result.
- **How fast or resource-efficient is it?** This public report does not include approved timing or memory measurements.

[Explore the historical comparison limits](historical-frontier-comparison.md) · [See the planned task families](benchmark-tasks.md)

</section>

## How to read a result

Completion tells you whether a test ran. Its purpose tells you what was being checked. Comparability tells you whether two scores can fairly be contrasted. These are separate questions: a completed run is not automatically capability evidence.

This dashboard is a static public summary, not a live connection to LM Studio. [What we publish and keep private](privacy.md).
