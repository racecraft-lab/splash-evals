# How we tested

This page separates what was measured, how it was measured, and what each benchmark can support. GPQA Diamond has a completed local result. SWE-bench Verified remains a planned evaluation with qualification still pending.

<section class="reader-section cool" aria-label="Benchmark and sample">

## GPQA Diamond method

GPQA Diamond is the most difficult subset of Graduate-Level Google-Proof Q&A, a multiple-choice science benchmark written and validated by domain experts. EvalScope 1.12.0 resolved its built-in `gpqa_diamond` adapter to the `default` subset of the `train` split.

The run used all **198 questions**. It was zero-shot: no worked examples were added to the prompt. The adapter asked the model to reason step by step and end with `ANSWER: [LETTER]`. Accuracy was the mean of exact answer-letter matches.

The benchmark is public and widely studied. It is useful for comparison, but it is not a private held-out set and cannot eliminate training-data contamination.

</section>

<section class="reader-section cool" aria-label="SWE-bench Verified method">

## SWE-bench Verified method

SWE-bench Verified evaluates repository-level issue resolution, not one-turn question answering. Each task requires an isolated repository environment and an agent that can inspect code, plan a fix, edit files, run relevant tests, and submit a final repository state for benchmark scoring.

The measured condition must record the exact task manifest and denominator, harness and agent-scaffold revisions, repository image and dependency state, tool access, model and runtime identity, time and token budgets, retries, test policy, scorer revision, and failure treatment. A change to any of those fields creates a different condition.

No local SWE-bench percentage is published. Before a full run, the grading container and host-isolation boundary must be verified, and a qualification run must prove that the agent/edit/test/scorer loop works without executing model-generated code directly on the Mac host.

</section>

<section class="reader-section warm" aria-label="Evaluated condition">

## The evaluated condition

| Setting | Value |
|---|---|
| Public model label | Splash / Qwen3.8 |
| API model alias | `racecraft-splash-local` |
| Runtime | LM Studio on this Mac, OpenAI-compatible local endpoint |
| Eval runner | EvalScope 1.12.0, native backend |
| Reasoning effort | `medium` |
| Batch / concurrency | 1 |
| Retries | 0 |
| Maximum output | 4,096 tokens |
| Random seed | 42 |
| Metric | Accuracy, mean aggregation |
| Denominator | All 198 requested questions |

All requests completed, so no transport failures had to be scored or excluded. A future run under a different model file, quantization, context length, runtime, reasoning effort, prompt, token limit, or scorer is a different condition.

<aside class="method-note" role="note"><strong>Checks before the run:</strong> the workbench verified that the selected model was local, the loopback endpoint responded, and the scorer could parse the required answer format. Those engineering checks are separate from the 54.55% capability result.</aside>

</section>

<section class="reader-section warm" aria-label="How to read the comparison">

## What makes a fair comparison?

Two scores are directly comparable only when the important conditions align: benchmark and dataset revision, exact item set, prompt, tools, attempts, reasoning budget, answer extraction, scorer, denominator, and failure treatment. For coding benchmarks, the agent scaffold, repository image, edit/test loop, and execution budget also have to align.

The current frontier-model sources do not disclose every one of those fields. Their scores are therefore shown as directional context. This project does not combine them into a leaderboard, an “AI score,” a percent-of-frontier badge, or an exact performance gap.

</section>

<section class="reader-section cool" aria-label="Privacy and public evidence">

## Privacy and public evidence

The public repository contains source code, configuration templates, factual references, and a reviewed aggregate result. It excludes raw benchmark prompts, model responses, reasoning traces, credentials, private runtime identifiers, local filesystem paths, and detailed execution logs.

That boundary protects private local state and restricted evidence, but it also limits independent review: readers can inspect the method and aggregate, but cannot regrade the run from this site. Public GitHub Actions builds static documentation with mock or synthetic data and has no connection to this Mac or LM Studio.

[Review the benchmark index](dashboard.md) · [Inspect the sources](sources.md)

</section>
