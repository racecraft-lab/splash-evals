# Public results dashboard

This static page presents only the reviewed sanitized fields from the post-hoc local
qualification export. It does not query LM Studio or read the private evidence directory.

## Qualification status

| Signal | Reviewed public result | Interpretation |
| --- | --- | --- |
| Publication purpose | `post_hoc_runtime_scorer_qualification` | Local runtime and scorer qualification only. |
| Capability evidence | `false` | No model capability, improvement, equivalence, or ranking claim is permitted. |
| Model | Splash/Qwen3.8 through local LM Studio | Generic public model/runtime label; no private runtime identifier is published. |
| Scorer | `builtin-exact-v1` | Public scorer identifier used for the reviewed aggregate. |
| Planned / attempted / completed / scorable | 10 / 10 / 10 / 10 | All planned qualification cases completed and were scorable. |
| Failed / censored / unattempted | 0 / 0 / 0 | No qualification case entered these categories. |
| End-to-end deployment success | 10/10 (`1.0`) | The local transport completed and the configured scorer accepted the reused cases; this is not a capability score. |
| Wilson 95% interval | `0.7224672001371107`–`1.0` | Qualification-aggregate uncertainty only; not a model capability interval. |
| Historical frontier comparison | Prohibited | Post-hoc qualification evidence cannot support a frontier delta. |

## Evidence and privacy boundary

Raw prompts, responses, and reasoning are intentionally not hosted. They remain outside Git and
GitHub Pages together with private runtime evidence. Public review is limited to the sanitized
fields above, the [local pilot summary](local-pilot-results.md), the
[benchmark and task-family descriptions](benchmark-tasks.md), and the
[historical comparison limitations](historical-frontier-comparison.md).
