# Historical frontier comparison

This is the public, evidence-bounded comparison report for the current Splash evaluation
workspace. It uses only the dated records in `references/frontier/`; it does not run remote
models, copy benchmark questions, or infer a local score that was not measured.

## Status

- `primary_objective_status`: `pilot_only`
- `historical_comparison_status`: `blocked`

The local LM Studio evidence is a bounded synthetic/practical pilot. It does not contain an
aligned GPQA Diamond, IFEval, or Aider polyglot run with a benchmark sample manifest and the
required scorer/protocol fields. Therefore every local score in the record table below is
explicitly `null`, and no historical delta is reported.

The exact-intersection gate also remains closed for the historical catalog. The Aider records
have enough dated model/provider breadth for a candidate intersection, but their exact
benchmark version and dataset revision are `null`. GPQA and IFEval likewise lack the required
version/revision facts and do not provide a two-provider intersection. Unknown fields are not
treated as compatible coverage.

The catalog provider allowlist is exactly OpenAI, Anthropic, and Google. Current primary-source
research found no record from those providers with all immutable sample, prompt, scorer, and
sampling facts required for reproduction. The records therefore remain historical context even
after the local runtime evidence is strengthened.

## Record-level comparison ledger

`historical result` is the value already recorded from the cited primary source. `local score`
is intentionally `null`; it is not a zero and is not a placeholder for an unrun benchmark.
`catalog comparability` is the current record classification. The reason is summarized from
the record's `comparability_notes`.

| Record ID | Evidence class | Historical result | Local score | Catalog comparability | Comparability reason |
|---|---|---:|---:|---|---|
| `anthropic-claude-3.5-sonnet-2024-06-gpqa` | `published_historical_reference` | 59.4% accuracy | `null` | `unknown` | Exact snapshot and protocol are not established; GPQA versus GPQA Diamond and answer extraction need verification. |
| `aider-claude-3.5-sonnet-20241022-polyglot-pass1` | `published_historical_reference` | 22.2% first-attempt success | `null` | `incompatible` | Full 225-case six-language suite; local pilot is not the same suite and scorer/environment details are not fully paired. |
| `aider-claude-3.5-sonnet-20241022-polyglot-pass2` | `published_historical_reference` | 51.6% success after permitted repair | `null` | `incompatible` | Repair-after-failure is not independent pass@2, and the local pilot is not the 225-case suite. |
| `aider-claude-3.7-sonnet-20250219-no-thinking-polyglot-pass1` | `published_historical_reference` | 24.4% first-attempt success | `null` | `incompatible` | No-thinking is a distinct condition and the local pilot is not the full six-language suite. |
| `aider-claude-3.7-sonnet-20250219-no-thinking-polyglot-pass2` | `published_historical_reference` | 60.4% success after permitted repair | `null` | `incompatible` | Repair-after-failure is not independent pass@2; no-thinking and thinking conditions must remain separate. |
| `aider-claude-3.7-sonnet-20250219-thinking-32k-polyglot-pass1` | `published_historical_reference` | 29.3% first-attempt success | `null` | `incompatible` | The 32K thinking budget is a distinct condition and the local pilot is not the full suite. |
| `aider-claude-3.7-sonnet-20250219-thinking-32k-polyglot-pass2` | `published_historical_reference` | 64.9% success after permitted repair | `null` | `incompatible` | Repair-after-failure is not independent pass@2; the 32K thinking condition is distinct. |
| `aider-gemini-2.5-pro-preview-03-25-polyglot-pass1` | `published_historical_reference` | 40.9% first-attempt success | `null` | `incompatible` | March experimental/preview identity is distinct from later releases and the local pilot is not the full suite. |
| `aider-gemini-2.5-pro-preview-03-25-polyglot-pass2` | `published_historical_reference` | 72.9% success after permitted repair | `null` | `incompatible` | Repair-after-failure is not independent pass@2; the March experimental/preview identity is distinct. |
| `openai-gpt-4.1-2025-04-gpqa-diamond` | `published_historical_reference` | 66.3% accuracy | `null` | `partially_matched` | Exact API snapshot is undisclosed; publisher extraction and a bounded local subset prevent a direct delta. |
| `openai-gpt-4.1-2025-04-ifeval` | `published_historical_reference` | 87.4% publisher-reported IFEval | `null` | `partially_matched` | Exact snapshot, IFEval metric variant, and full protocol are not identified. |
| `openai-gpt-4o-2024-11-20-gpqa-diamond` | `published_historical_reference` | 46.0% accuracy | `null` | `partially_matched` | Source notes model-based extraction changes the result; the local pilot is only a subset unless separately aligned. |
| `openai-gpt-4o-2024-11-20-ifeval` | `published_historical_reference` | 81.0% publisher-reported IFEval | `null` | `partially_matched` | IFEval metric variant, dataset revision, sample count, template, and scorer revision are unknown. |
| `openai-o1-high-gpqa-diamond` | `published_historical_reference` | 75.7% accuracy | `null` | `partially_matched` | Exact o1 snapshot and reasoning budget are undisclosed; publisher extraction and a bounded subset prevent a direct delta. |
| `openai-o1-high-ifeval` | `published_historical_reference` | 92.2% publisher-reported IFEval | `null` | `partially_matched` | Exact snapshot/reasoning budget, IFEval metric variant, and full protocol are not identified. |

## What this does and does not establish

The records establish a dated, source-linked historical context and preserve the distinction
between benchmark families, model snapshots, thinking modes, and repair semantics. They do not
establish that Splash is above or below any historical model. No direct difference, ranking,
paired statistic, or uncertainty transfer is valid from the current pilot.

A future comparison requires a separately authorized run whose benchmark identity, exact
version, dataset revision, sample IDs, prompt/template, metric and unit, scorer/extractor,
reasoning mode, output budget, attempts, tool/scaffold policy, aggregation, model/runtime
condition, failure treatment, and denominators are recorded alongside the local results. Until
then, the supported conclusion is the scoped `pilot_only` result and the `blocked`
historical-comparison status.
