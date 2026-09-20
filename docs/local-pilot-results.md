# Local pilot results

This page is the sanitized public summary of the reviewed local Splash/Qwen3.8 pilot. It
contains no raw prompts or responses, device IDs, machine hashes, private paths, credentials,
or raw runtime inventories.

## Status

- Primary objective status: `pilot_only`
- Local pilot: verified
- Historical frontier comparison: blocked

## As-found condition

- Pilot label: `verified as-found pilot`
- Loaded model identifier: `qwen3.8-27b-splash`
- Native identity: Qwen3.8 27B Splash by `incoai`
- Architecture: `qwen3_5`
- Format: `splash`
- Quantization: `4bit`
- Execution evidence: verified physical-locality evidence and loopback transport
- Requests: 10
- Accepted: 10/10
- Failures: 0
- Censored: 0
- Wilson 95% interval: 72.2467%–100%
- Completion tokens: 660
- Total request latency: 8.1253 seconds
- Maximum concurrency: 1
- Cost: $0
- Archived rescore: 0 new inference

## Reusable runtime settings

The as-found request transmitted `max_tokens=512` and `stream=false`. The as-found
`temperature`, `top_p`, and `top_k` values were preserved as found and were not transmitted.
The frozen candidate requested `temperature=1.0`, `top_p=0.95`, and `top_k=20`.

## Post-hoc candidate comparison

> **Exploratory only:** This is a post-hoc, order-reversed comparison on tasks that had
> already been viewed. It cannot recreate a pristine pre-tuning baseline, demonstrate
> improvement or equivalence, or support a direct historical frontier delta.

The frozen candidate pilot used the same 10 task IDs and scorer as the as-found run, with
10/10 accepted and a paired right-minus-left correctness difference of `0.0`. This is an
exploratory comparison only; the earlier
candidate's locality remains attribution-provisional and is not reported as verified
physical locality.

## Historical comparison limitation

The historical comparison gate remains blocked. No current local task has a
protocol-compatible immutable benchmark version and dataset revision matching the dated
records. The pilot is diagnostic/practical evidence only; it is not a GPQA, IFEval, or Aider
result, and no historical delta, ranking, or equivalence claim is reported.

See the [historical frontier comparison](historical-frontier-comparison.md),
[methodology](methodology.md), and [frontier verification ledger](../references/frontier/VERIFICATION.md)
for the evidence classes and source limitations.
