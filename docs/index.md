# How well does Splash work on a local computer?

<section class="reader-section" aria-label="The research question and current evidence">
<div class="reader-columns">
<div>

## The question

How close can local Splash / Qwen3.8 come to current and previous-generation frontier models on useful tasks? We want to measure the gap, not assume a win.

</div>
<div>

## What we know

**Capability results: not yet measured.** Published reference scores provide context, but there is no eligible local benchmark result or supported frontier comparison yet.

</div>
<div>

## What comes next

Freeze the tasks, scoring rules, and comparison conditions; run the local study; then publish the results and their limits. [See the comparison coverage](dashboard.md#benchmark-comparison-board).

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
