# How well does Splash work on a local computer?

<!-- qualification-finding -->

<section class="reader-section" aria-label="What the first check tells us">
<div class="reader-columns">
<div>

## What ran

A small set of previously used tasks ran through local LM Studio. This was a check of the testing setup, not a fresh test of general ability.

</div>
<div>

## What we learned

The local request path completed, and the configured answer checker accepted the reused cases. The setup can produce and check answers for those cases.

</div>
<div>

## What remains open

We still need fresh, preselected tests to measure broader ability. We cannot yet compare Splash fairly with older leading models.

</div>
</div>
</section>

<section class="reader-section cool" aria-label="The evaluation process">

## From a task to a result

An evaluation is a test with a recorded outcome. This project keeps the task, the answer check, and the interpretation separate.

<ol class="process">
<li><strong>Task</strong><span>Give the model an instruction with a defined way to check its answer.</span></li>
<li><strong>Local model</strong><span>Run the task on the local deployment through LM Studio.</span></li>
<li><strong>Answer check</strong><span>Check the response against the task's criteria.</span></li>
<li><strong>Report</strong><span>Record what happened, what it means, and what it does not prove.</span></li>
</ol>

[See how we test](methodology.md)

</section>

<section class="reader-section warm" aria-label="Project definitions">

## Understand the project

<div class="reader-columns">
<div>

### Splash / Qwen3.8

The public label for the local model deployment being studied. Results describe that tested setup, not every deployment of a model family.

</div>
<div>

### LM Studio

The application serving the model locally. This website is a static report; it cannot send requests to the model or access the test computer.

</div>
<div>

### This repository

The source code, test tools, dated reference records, and reviewed public summaries. Raw prompts, responses, and private runtime records stay outside it.

</div>
</div>

[Read the glossary](glossary.md) · [Explore the source code](https://github.com/racecraft-lab/splash-evals)

</section>
