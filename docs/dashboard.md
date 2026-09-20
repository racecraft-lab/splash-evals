# Public results dashboard

This is a static view of the reviewed public aggregate. It does not query LM Studio or read the
private evidence directory. Every number below is already published in the sanitized local pilot
summary.

## Evaluation status

| Signal | Public result | What it proves |
| --- | --- | --- |
| Primary objective | `pilot_only` | A bounded practical pilot completed; the full research objective is not answered. |
| Local pilot | Verified | Ten reviewed requests ran through the verified local condition. |
| Accepted | 10/10 | All ten pilot responses passed the pilot scorer. |
| Failures / censored | 0 / 0 | No pilot request failed or was excluded. |
| Wilson 95% interval | 72.2467%–100% | Uncertainty remains wide because the sample is only ten tasks. |
| Completion tokens | 660 | Sanitized aggregate count, not raw response content. |
| Total request latency | 8.1253 seconds | Aggregate request latency for the bounded pilot. |
| Maximum concurrency | 1 | The pilot was sequential. |
| Cost | $0 | Local inference incurred no provider charge. |
| Historical frontier comparison | Blocked | No protocol-compatible local benchmark run supports a direct frontier delta. |

## Evidence available here

- [Local pilot results](local-pilot-results.md) provides the reviewed aggregate and runtime-setting summary.
- [Historical frontier comparison](historical-frontier-comparison.md) explains why no direct ranking or delta is reported.
- [Frontier verification ledger](../references/frontier/VERIFICATION.md) records the dated external evidence and its limits.
- [Benchmark task guide](benchmark-tasks.md) describes the evaluated task families without publishing restricted prompt text.

## Boundary

Raw prompts, responses, reasoning, device or instance identifiers, private paths, detailed runtime
inventory, and timestamped run identifiers remain outside Git and GitHub Pages. They are not needed
to interpret this aggregate, and publication would break the repository's two-zone privacy model.
