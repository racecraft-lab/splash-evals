# Results

This is the benchmark index. Each result keeps its score, runtime, method, sources, and limitations together so readers can tell measured evidence from work that is still pending.

<section class="reader-section cool" aria-label="Benchmark result index">

## Benchmark results

<!-- benchmark-card-grid -->

</section>

<section class="reader-section warm" aria-label="How to read benchmark status">

## Read the status before the score

- **Measured** means a complete, reviewed local aggregate exists in `results/public/`.
- **Pending** means no benchmark-specific local run has started.
- **Qualification** means the runner, agent loop, environment, and scorer are being checked. It is never a capability result.
- **Invalid** means evidence failed a required gate; its score is withheld until review or a clean rerun.
- **External reference** means a source-verified published observation. It is context, not a local result.

The two benchmark pages use the same evidence pattern, but their metrics are different. GPQA Diamond measures answer accuracy. SWE-bench Verified measures resolved software-engineering tasks through an agent, repository edits, and tests. Those percentages must never be combined into one score.

</section>

<section class="reader-section cool" aria-label="Compatibility links">

## Existing result links

Earlier links still have a clear destination:

- <span id="runtime-performance"></span>[GPQA Diamond runtime performance](gpqa-diamond.md#runtime-performance)
- <span id="benchmark-comparison"></span>[GPQA Diamond benchmark comparison](gpqa-diamond.md#benchmark-comparison)
- <span id="run-record-and-limitations"></span>[GPQA Diamond run record and limitations](gpqa-diamond.md#run-record-and-limitations)

</section>
