# Historical frontier comparison

> **Consolidated reader content.** The current public comparison is on [Results](dashboard.md#benchmark-comparison), with source and protocol notes on [Sources](sources.md#where-the-numbers-come-from). The old public URL redirects there.

## Current status

- `primary_objective_status`: `partially_answered`
- `local_capability_result`: `54.55%` GPQA Diamond accuracy
- `execution`: 198 requested, 198 succeeded, 0 errors
- `historical_comparison_status`: `directional_only`

The completed EvalScope 1.12.0 run establishes one local capability result for the exact Splash / Qwen3.8, LM Studio, zero-shot, `reasoning_effort: medium` condition. It replaces the earlier setup-only and unmeasured reader states.

It does not establish a protocol-matched rank or exact gap to a frontier model. The external figures come from provider reports, cross-provider comparison tables, or independent Epoch AI evaluations with different prompts, reasoning settings, trial counts, sampling, answer extraction, or dataset metadata.

## Requested comparison roster

The reader-facing Results page now covers:

- Claude Opus 4, Opus 4.6, Opus 4.7, Opus 4.8, and Opus 5;
- Claude Sonnet 4, Sonnet 4.6, and Sonnet 5; and
- GPT-5.5 plus GPT-5.6 Sol, Terra, and Luna.

Where provider and independent figures both exist, both are shown with their source class. They are never averaged. Missing protocol fields remain unknown rather than being treated as matches.

## Why there is no leaderboard

A direct comparison requires the same benchmark revision and items, prompt, tools, attempts, reasoning budget, sampling, answer extraction, scorer, denominator, and failure treatment. The reviewed external sources do not establish all of those fields against the local run.

The site therefore reports:

1. the measured local score and runtime performance;
2. external scores as directional context with explicit source labels; and
3. the protocol differences that prevent a head-to-head claim.

No percentage-point gap, ranking, equivalence claim, uncertainty transfer, or percent-of-frontier badge is supported.
