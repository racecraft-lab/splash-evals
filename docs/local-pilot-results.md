# Local pilot results

This page summarizes the reviewed sanitized export from a post-hoc local Splash/Qwen3.8
qualification run. The cases were reused rather than held out, so this evidence qualifies local
runtime and scorer operation only. It is not model capability evidence.

## Status

- Publication purpose: `post_hoc_runtime_scorer_qualification`
- Capability evidence: `false`
- Historical comparison: prohibited

## Sanitized qualification evidence

| Field | Reviewed value |
| --- | --- |
| Model | Splash/Qwen3.8 through local LM Studio |
| Scorer | `builtin-exact-v1` |
| Planned | 10 |
| Attempted | 10 |
| Completed | 10 |
| Scorable | 10 |
| Failed | 0 |
| Censored | 0 |
| Unattempted | 0 |
| End-to-end deployment success | 10/10 (`1.0`) |
| Wilson 95% interval | `0.7224672001371107`–`1.0` |

The end-to-end result shows that the local transport completed and the configured scorer accepted
the reused cases. The interval describes this qualification aggregate only; it is not a model
capability interval.

## Interpretation boundary

Because task selection was post-hoc, this export cannot establish held-out performance, model
capability, improvement, equivalence, ranking, or a frontier delta. Historical comparison is
prohibited for this evidence.

Raw prompts, responses, and reasoning are intentionally not hosted. They remain in the private
local-evidence zone with runtime identifiers and other non-public evidence. Readers can inspect
the public [benchmark and task-family descriptions](benchmark-tasks.md), the sanitized evidence
on this page and the [public dashboard](dashboard.md), and the
[historical frontier comparison](historical-frontier-comparison.md) for the comparison boundary.
