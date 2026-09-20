# Historical source map

The catalog stores short factual records, not copied articles or restricted data. Retrieval
dates record transcription work, not model measurement dates.

| Cohort member | Primary identity/result sources | Important limitation |
|---|---|---|
| GPT-4o (2024-11-20), OpenAI o1 (high), GPT-4.1 | [OpenAI GPT-4.1 release appendix](https://openai.com/index/gpt-4-1/) | GPQA uses model-based answer extraction; the appendix says this materially changes GPT-4o. |
| Claude 3.5 Sonnet (June 2024) | [Anthropic release](https://www.anthropic.com/news/claude-3-5-sonnet) and [Anthropic evaluation reference](https://www.anthropic.com/news/the-case-for-targeted-regulation) | June and October snapshots are distinct. The June GPQA protocol fields are incomplete. |
| Claude 3.5 Sonnet (2024-10-22) | [Aider leaderboard](https://aider.chat/docs/leaderboards/) | Full 225-case polyglot result with two-attempt semantics; not a pilot match. |
| Claude 3.7 Sonnet (2025-02-19) | [Anthropic release](https://www.anthropic.com/news/claude-3-7-sonnet) and [Aider leaderboard](https://aider.chat/docs/leaderboards/) | No-thinking and 32K-thinking conditions are separate. |
| Gemini 2.5 Pro Experimental (03-25) | [Google March 2025 release](https://blog.google/innovation-and-ai/models-and-research/google-deepmind/gemini-model-thinking-updates-march-2025/) and [Aider leaderboard](https://aider.chat/docs/leaderboards/) | March experimental, later previews, and stable releases are different models/conditions. |

The benchmark intersection with records for at least three dated models across at least two
providers is currently Aider polyglot. GPQA and IFEval contain three OpenAI conditions, but
do not alone satisfy the two-provider intersection. Full Aider results are kept as
historical context until the local adapter, 225-case corpus, edit format, attempts, and
sandbox are aligned. No checked-in record authorizes remote inference.

## Coverage and current comparison status

| Task family | Dated historical coverage | Planned/local coverage | Current comparison status |
|---|---|---|---|
| Aider polyglot | Claude 3.5 Sonnet 20241022; Claude 3.7 Sonnet 20250219 no-thinking and 32K-thinking; Gemini 2.5 Pro Preview 03-25 | Coding remains blocked until the isolated sandbox and full pinned adapter are verified | `incompatible` with a small pilot; pass1 and after-repair pass2 remain separate |
| GPQA Diamond | GPT-4o 2024-11-20, o1 high, GPT-4.1; Claude June 2024 record needs split/protocol confirmation | At most 12 authorized pilot questions | `partially_matched` or `unknown`; full-set historical delta unavailable |
| IFEval | GPT-4o 2024-11-20, o1 high, GPT-4.1 | At most 16 pilot prompts with four metrics preserved | `partially_matched`; source metric variant and full protocol are incomplete |
| Structured output and native tools | No aligned dated cohort responses | Synthetic and local practical tasks | historical comparison unavailable |
| Long context | No aligned dated cohort responses | Synthetic and local practical tasks | historical comparison unavailable |
| Repository engineering | No aligned dated cohort responses | Synthetic/public-repository tasks after sandbox qualification | historical comparison unavailable |

The current 10-case synthetic smoke/pilot evidence qualifies transport and scorer behavior
only. Its historical comparison is unavailable and it is not an intelligence result.
