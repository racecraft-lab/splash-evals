# SWE-bench Verified

<!-- benchmark-outcome:swe-bench-verified -->

<section class="reader-section cool" aria-label="Why this test matters for a helper">

<p class="kicker">Why this test</p>

## Why it matters for a helper

Coding help is the main job we want a local helper to do. SWE-bench Verified is close to real work. Each of its 500 tasks is a real bug report from an open-source project. The model has to read the code, change it, and make the project's own tests pass. People checked every task to make sure it is fair and can be solved; that is the "Verified" part.

Fixing about 7 in 10 of these bugs, working alone on a local machine, is a strong sign the helper can take on everyday coding. It does not tell us how much a helper saves on a paid plan. [What it means](what-it-means.md) covers that.

Each task runs as a loop, one bug at a time:

<ol class="agent-loop">
  <li><span>1</span><div><strong>Open the issue and repository</strong><p>The agent receives one verified issue in its isolated task environment and inspects the checked-out code.</p></div></li>
  <li><span>2</span><div><strong>Plan and edit the implementation</strong><p>The agent reasons about the defect, changes repository files, and may inspect additional code needed for the fix.</p></div></li>
  <li><span>3</span><div><strong>Run relevant tests</strong><p>The agent uses the task environment's tools to check its work. Model-generated code never runs directly on the test machine itself.</p></div></li>
  <li><span>4</span><div><strong>Grade the final repository state</strong><p>The benchmark harness applies its scorer to the submitted patch in the verified isolation boundary and records whether the task is resolved.</p></div></li>
</ol>

This is very different from the science test, where the model answers one question at a time. Here the agent software, the project's code and packages, the tools, the time and token budgets, and the grader version are all part of what we measured.

</section>

<!-- benchmark-card:swe-bench-verified -->

<section class="reader-section warm" aria-label="SWE-bench external reference comparison">

<p class="kicker">Context</p>

## Benchmark comparison

How do cloud models do on the same test? These published scores use SWE-bench Verified, but with different agents and settings. Our row stays pinned at the top. The Vals AI rows use the same agent software, mini-swe-agent, but with different step limits, settings, and computers, so they are context, not a ranking. Any missing score is labeled as missing.

<aside class="method-note" role="note"><strong>Two different source groups:</strong> ten Vals AI observations use mini-swe-agent and are labeled “Vals AI independent.” Two Claude 4 observations use Anthropic's standard scaffold and are labeled “Provider-reported.” Do not rank or subtract across those harness groups.</aside>

<!-- benchmark-explorer:swe-bench-verified -->

[Inspect the coding-benchmark sources](sources.md#swe-bench-verified-sources)

</section>

<!-- benchmark-runtime:swe-bench-verified -->
