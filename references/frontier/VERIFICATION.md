# Were the source numbers copied correctly?

The recorded numbers were checked against their cited sources in a second transcription pass. **That check is about copying accuracy, not fair comparability or independent reproduction of a provider's result.**

The local run is not eligible for a direct comparison with these records. [Why the comparison is not available](../../docs/historical-frontier-comparison.md).

<section class="reader-section cool" aria-label="Source transcription checks">

<details>
<summary>Inspect all source-number checks and record identifiers</summary>

This public ledger records the second-pass numerical transcription check for the dated
reference catalog. It contains no private identity, credentials, benchmark questions, model
responses, or local endpoint state.

## Check definition

For each `source_verified` record, a second pass re-read the primary source locator named in
the YAML record and checked the model/condition identity, reported numeric value, metric name,
metric unit, and source revision/content hash against the catalog entry. The result below is
`second_pass_match` when those fields agree. A null catalog field remains unknown; this check
does not turn it into a value and does not make a record directly comparable.

The GPT-5.6 HTML source is dynamic. Its recorded digest is explicitly a normalized hash of the
source-locator excerpt (publication date, table locator, and the published GPT-5.5/GPT-5.6 GPQA
values), not a claim that the entire rendered page is immutable. The official URL and locator
remain the authoritative source; the excerpt hash only makes the second-pass transcription
check repeatable.

| Record ID | Source family | Reported numeric value | Numeric transcription check |
|---|---|---:|---|
| `anthropic-claude-3.5-sonnet-2024-06-gpqa` | Anthropic evaluation reference | 59.4% | `second_pass_match` |
| `aider-claude-3.5-sonnet-20241022-polyglot-pass1` | Aider polyglot leaderboard | 22.2% | `second_pass_match` |
| `aider-claude-3.5-sonnet-20241022-polyglot-pass2` | Aider polyglot leaderboard | 51.6% | `second_pass_match` |
| `aider-claude-3.7-sonnet-20250219-no-thinking-polyglot-pass1` | Aider polyglot leaderboard | 24.4% | `second_pass_match` |
| `aider-claude-3.7-sonnet-20250219-no-thinking-polyglot-pass2` | Aider polyglot leaderboard | 60.4% | `second_pass_match` |
| `aider-claude-3.7-sonnet-20250219-thinking-32k-polyglot-pass1` | Aider polyglot leaderboard | 29.3% | `second_pass_match` |
| `aider-claude-3.7-sonnet-20250219-thinking-32k-polyglot-pass2` | Aider polyglot leaderboard | 64.9% | `second_pass_match` |
| `aider-gemini-2.5-pro-preview-03-25-polyglot-pass1` | Aider polyglot leaderboard | 40.9% | `second_pass_match` |
| `aider-gemini-2.5-pro-preview-03-25-polyglot-pass2` | Aider polyglot leaderboard | 72.9% | `second_pass_match` |
| `openai-gpt-4.1-2025-04-gpqa-diamond` | OpenAI GPT-4.1 appendix | 66.3% | `second_pass_match` |
| `openai-gpt-4.1-2025-04-ifeval` | OpenAI GPT-4.1 appendix | 87.4% | `second_pass_match` |
| `openai-gpt-4o-2024-11-20-gpqa-diamond` | OpenAI GPT-4.1 appendix | 46.0% | `second_pass_match` |
| `openai-gpt-4o-2024-11-20-ifeval` | OpenAI GPT-4.1 appendix | 81.0% | `second_pass_match` |
| `openai-o1-high-gpqa-diamond` | OpenAI GPT-4.1 appendix | 75.7% | `second_pass_match` |
| `openai-o1-high-ifeval` | OpenAI GPT-4.1 appendix | 92.2% | `second_pass_match` |
| `openai-gpt-5.5-2026-07-gpqa-diamond` | OpenAI GPT-5.6 announcement, Academic table | 93.6% | `second_pass_match` |
| `openai-gpt-5.6-sol-2026-07-gpqa-diamond` | OpenAI GPT-5.6 announcement, Academic table | 94.6% | `second_pass_match` |
| `openai-gpt-5.6-terra-2026-07-gpqa-diamond` | OpenAI GPT-5.6 announcement, Academic table | 92.9% | `second_pass_match` |
| `openai-gpt-5.6-luna-2026-07-gpqa-diamond` | OpenAI GPT-5.6 announcement, Academic table | 92.3% | `second_pass_match` |

The ledger verifies transcription only. It does not verify undocumented benchmark protocol
fields, establish an exact benchmark version or dataset revision, or authorize remote
inference. Those limitations remain represented as `null` in the catalog and as blocked or
partially matched in the comparison report.

</details>

</section>
