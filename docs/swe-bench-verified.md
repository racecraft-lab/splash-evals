# SWE-bench Verified

<!-- benchmark-outcome:swe-bench-verified -->

<section class="reader-section warm" aria-label="SWE-bench external reference comparison">

## External reference explorer

These published observations use SWE-bench Verified, but the agents and testing conditions differ. The pinned Splash row shows its current reviewed status. Missing scores are labeled explicitly.

<aside class="method-note" role="note"><strong>Two different source groups:</strong> ten Vals AI observations use mini-swe-agent and are labeled “Vals AI independent.” Two Claude 4 observations use Anthropic's standard scaffold and are labeled “Provider-reported.” Do not rank or subtract across those harness groups.</aside>

<!-- benchmark-explorer:swe-bench-verified -->

[Inspect the coding-benchmark sources](sources.md#swe-bench-verified-sources)

</section>

<section class="reader-section cool" aria-label="SWE-bench agent method">

## How the evaluation works

<ol class="agent-loop">
  <li><span>1</span><div><strong>Open the issue and repository</strong><p>The agent receives one verified issue in its isolated task environment and inspects the checked-out code.</p></div></li>
  <li><span>2</span><div><strong>Plan and edit the implementation</strong><p>The agent reasons about the defect, changes repository files, and may inspect additional code needed for the fix.</p></div></li>
  <li><span>3</span><div><strong>Run relevant tests</strong><p>The agent uses the task environment's tools to check its work. Model-generated code is never executed directly on the Mac host.</p></div></li>
  <li><span>4</span><div><strong>Grade the final repository state</strong><p>The benchmark harness applies its scorer to the submitted patch in the verified isolation boundary and records whether the task is resolved.</p></div></li>
</ol>

This loop differs fundamentally from GPQA's one-request answer check. Agent scaffold, repository image, dependency state, tools, time and token budgets, retry policy, and scorer revision all belong to the measured condition.

[Read the multi-benchmark method](methodology.md#swe-bench-verified-method)

</section>

<!-- benchmark-runtime:swe-bench-verified -->
