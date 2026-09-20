# Local setup-check report

<section class="reader-section" aria-label="Research question">

## The question

Could the local Splash/Qwen3.8 setup complete the reused tasks and have its answers accepted by the configured checker?

</section>

<!-- qualification-finding -->

<section class="reader-section cool" aria-label="Run purpose and coverage">

<dl class="status-list">
<div><dt>Execution</dt><dd>Completed</dd></div>
<div><dt>Purpose</dt><dd>Testing-setup qualification</dd></div>
<div><dt>Historical comparison</dt><dd>Not permitted for this run</dd></div>
</dl>

## What ran

The local deployment ran through LM Studio. Cases were reused, and selection was assessed after the fact rather than kept separate as an unseen test set. The public record does not provide per-case or task-family outcomes. It is not a GPQA, IFEval, MMLU-Pro, or Aider benchmark result.

</section>

<section class="reader-section" aria-label="Recorded outcomes">

## What happened

<!-- qualification-table -->

</section>

<section class="reader-section warm" aria-label="Interpretation and limits">

## What it means

The local request path completed and the answer checker accepted these cases. That is useful evidence that the testing setup operates on this set.

**It does not establish performance on unfamiliar tasks, general model ability, improvement, a ranking, or equivalence to another model.** A small reused set cannot answer those questions. No direct historical score difference is allowed for this run.

</section>

<section class="reader-section" aria-label="Answer checking">

## How answers were checked

The run used the public scorer identifier `builtin-exact-v1`. Its acceptances are the configured checks for these reused cases, not independent human assessments of broad answer quality. No per-case answers or reasoning traces are included in the public export.

<details>
<summary>Technical classification and uncertainty</summary>

<!-- qualification-technical -->

The Wilson interval describes uncertainty for this qualification aggregate only. It does not correct for reused cases or post-hoc selection and must not be interpreted as an interval for model capability.

</details>

</section>

<section class="reader-section cool" aria-label="Next research steps">

## What happens next

The next capability study needs a frozen task set, a qualified checker, verified local execution, and cases held apart from setup and tuning. A historical comparison additionally needs matching task and scoring conditions.

[How we test](methodology.md) · [Historical comparison limits](historical-frontier-comparison.md) · [Back to all results](dashboard.md)

</section>

<section class="reader-section warm" aria-label="Public evidence boundary">

## Public evidence boundary

This report presents the previously reviewed sanitized qualification aggregate. Raw prompts, responses, reasoning, local identifiers, and runtime records remain private. Readers can inspect the public method and summary, but cannot independently regrade this run from this site. [Publication and privacy policy](privacy.md).

</section>
