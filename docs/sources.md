# Sources and evidence

This page distinguishes three questions: where a number came from, whether it was copied correctly, and whether it is fairly comparable with the local result.

<section class="reader-section cool" aria-label="Primary sources">

## Primary sources used here

| Evidence | Primary source | What it supports | Important limit |
|---|---|---|---|
| Splash / Qwen3.8 GPQA result | Reviewed local EvalScope 1.12.0 aggregate in this repository | 54.6% accuracy; 198/198 completed; local runtime performance | One public benchmark and one exact local condition |
| GPQA Diamond benchmark | [EvalScope GPQA documentation](https://evalscope.readthedocs.io/en/latest/get_started/supported_dataset/llm.html) and the pinned built-in adapter | Benchmark identity and runner contract | Public dataset; contamination cannot be excluded |
| GPT-5.6 Sol, Terra, Luna; GPT-5.5; Claude Opus 4.8 | [OpenAI GPT-5.6 announcement](https://openai.com/index/gpt-5-6/) | Publisher comparison-table percentages | Full protocol and immutable model snapshots are not disclosed for every row |
| GPT-5.5 | [OpenAI GPT-5.5 announcement](https://openai.com/index/introducing-gpt-5-5/) | 93.6% publisher-reported GPQA Diamond context | Same protocol limitations; not a matched local run |
| Claude Sonnet 5 | [Anthropic Sonnet 5 announcement](https://www.anthropic.com/news/claude-sonnet-5) | Model identity and release context | No GPQA Diamond value identified in the reviewed page |
| Claude Opus 5 | [Anthropic Opus 5 announcement](https://www.anthropic.com/news/claude-opus-5) | Model identity and release context | No GPQA Diamond value identified in the reviewed page |
| Older GPT-4o, GPT-4.1, and o1 context | [OpenAI GPT-4.1 appendix](https://openai.com/index/gpt-4-1/) | Dated GPQA and IFEval figures | Answer extraction materially affects the reported GPQA result |
| LM Studio runtime | [Headless service guide](https://lmstudio.ai/docs/developer/core/headless) and [`lms load` reference](https://lmstudio.ai/docs/cli/load) | Headless service and model loading commands | Project locality rules add fail-closed checks beyond the docs |

</section>

<section class="reader-section warm" aria-label="Catalog semantics">

## Catalog semantics

The repository's [`references/frontier`](https://github.com/racecraft-lab/splash-evals/tree/main/references/frontier) directory stores one factual record per model, benchmark, metric, and condition. Multiple records can describe the same model. A `null` field means **unknown**, never zero.

Catalog records are historical evidence, not provider configuration. They do not authorize remote inference and do not become directly comparable merely because the benchmark title matches.

</section>

<section class="reader-section cool" aria-label="Transcription checks">

## Transcription checks

The checked-in verification ledger performs a second pass over recorded numerical values and their cited locators. Its current GPQA Diamond records include:

| Record | Reported value | Check |
|---|---:|---|
| GPT-5.6 Sol | 94.6% | Second-pass match |
| GPT-5.6 Terra | 92.9% | Second-pass match |
| GPT-5.6 Luna | 92.3% | Second-pass match |
| GPT-5.5 | 93.6% | Second-pass match |
| GPT-4.1 | 66.3% | Second-pass match |
| GPT-4o (2024-11-20) | 46.0% | Second-pass match; extraction caveat applies |
| OpenAI o1 (high) | 75.7% | Second-pass match |

A transcription match means the repository copied the cited value correctly. It does **not** reproduce the provider's run, fill missing protocol fields, or establish a fair comparison.

[Browse the complete catalog](https://github.com/racecraft-lab/splash-evals/tree/main/references/frontier) · [Review the local result](dashboard.md)

</section>

<section class="reader-section warm" aria-label="Comparison status">

## Comparison status

The local GPQA result and the publisher rows share a benchmark name and metric family, so the numbers are useful directional context. They remain only partially matched or unknown because the available sources do not establish the same immutable dataset revision, exact item manifest, prompt, attempts, reasoning budget, extraction, and scorer for every model.

This site therefore reports the values side by side but does not claim an exact gap, rank, equivalence, or improvement.

</section>
