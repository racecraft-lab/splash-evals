# How we tested

This page explains how we ran each test and why we are careful about comparisons. Both tests have a complete, reviewed result. They measure different things, so their scores are never combined.

<section class="reader-section cool" aria-label="Benchmark and sample">

<p class="kicker">The science test</p>

## GPQA Diamond method

In plain terms: we asked the model 198 hard science questions, one at a time, and checked each final answer letter against the answer key.

GPQA Diamond is the most difficult subset of Graduate-Level Google-Proof Q&A, a multiple-choice science benchmark written and validated by domain experts. EvalScope 1.12.0 resolved its built-in `gpqa_diamond` adapter to the `default` subset of the `train` split.

The run used all **198 questions**. It was zero-shot: no worked examples were added to the prompt. The adapter asked the model to reason step by step and end with `ANSWER: [LETTER]`. Accuracy was the mean of exact answer-letter matches.

The benchmark is public and widely studied. It is useful for comparison, but it is not a private held-out set and cannot eliminate training-data contamination.

</section>

<section class="reader-section cool" aria-label="SWE-bench Verified method">

<p class="kicker">The coding test</p>

## SWE-bench Verified method

In plain terms: for each of 500 real bugs, the model worked as an agent inside a sealed-off copy of the project. It read the code, made changes, and ran tests. Then a separate grader checked whether the project's own tests passed.

SWE-bench Verified evaluates repository-level issue resolution, not one-turn question answering. Each task requires an isolated repository environment and an agent that can inspect code, plan a fix, edit files, run relevant tests, and submit a final repository state for benchmark scoring.

The measured condition must record the exact task manifest and denominator, harness and agent-scaffold revisions, repository image and dependency state, tool access, model and runtime identity, time and token budgets, retries, test policy, scorer revision, and failure treatment. A change to any of those fields creates a different condition.

Before the full run, the grading container and host-isolation boundary were verified, and a qualification run on disjoint tasks proved that the agent, edit, test, and scorer loop works without running model-generated code directly on the test machine. The full run then used all **500 Verified tasks** with one attempt per task.

| Setting | Value |
|---|---|
| Agent | mini-SWE-agent 2.4.6, bash-only tools, Docker task containers |
| Grader | Official SWE-bench 5.0.2 harness in a separate grader container |
| Dataset | `SWE-bench/SWE-bench_Verified`, revision `78f471bf655a3137b2e8a75af1501690ec009ec3`, test split |
| Reasoning | `on` (the approved variant; `xhigh` is unsupported) |
| Sampling | Temperature 1.0, top-p 0.95, up to 65,536 output tokens per request |
| Context window | 131,072 tokens |
| Step limit / command timeout | 100 steps (upstream 250) / 120 s (upstream 60 s) |
| Task images | `linux/amd64` under Rosetta emulation on Apple silicon |
| Network | Agent containers offline; 12 graders reach only allowlisted hosts |
| Metric | Resolved tasks divided by all 500 tasks |

Model failures and infrastructure errors stay in the denominator and count as unresolved, following the upstream convention. The result record lists every deviation from the upstream setup, including five host-fault re-runs approved by the operator.

[Read the SWE-bench result and limitations](swe-bench-verified.md)

</section>

<section class="reader-section warm" aria-label="Evaluated condition">

<p class="kicker">The exact setup</p>

## The evaluated condition

These are the exact settings for the science test. Change any one of them and it becomes a different test.

| Setting | Value |
|---|---|
| Public model label | Splash / Qwen3.8 |
| API model alias | `racecraft-splash-local` |
| Runtime | LM Studio on the local test machine, OpenAI-compatible local endpoint |
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

<p class="kicker">Fair comparisons</p>

## What makes a fair comparison?

Two scores on the same test are only comparable if the test was run the same way. That is why we show cloud-model scores as context, not as a race.

Two scores are directly comparable only when the important conditions align: benchmark and dataset revision, exact item set, prompt, tools, attempts, reasoning budget, answer extraction, scorer, denominator, and failure treatment. For coding benchmarks, the agent scaffold, repository image, edit/test loop, and execution budget also have to align.

The current frontier-model sources do not disclose every one of those fields. Their scores are therefore shown as directional context. This project does not combine them into a leaderboard, an “AI score,” a percent-of-frontier badge, or an exact performance gap.

</section>

<section class="reader-section cool" aria-label="Privacy and public evidence">

<p class="kicker">Privacy</p>

## Privacy and public evidence

We publish the results and the method, but not the raw test material or anything private about the machine.

The public repository contains source code, configuration templates, factual references, and a reviewed aggregate result. It excludes raw benchmark prompts, model responses, reasoning traces, credentials, private runtime identifiers, local filesystem paths, and detailed execution logs.

That boundary protects private local state and restricted evidence, but it also limits independent review: readers can inspect the method and aggregate, but cannot regrade the run from this site. Public GitHub Actions builds static documentation with mock or synthetic data and has no connection to the test machine or LM Studio.

[Review the benchmark index](dashboard.md) · [Inspect the sources](sources.md)

</section>
