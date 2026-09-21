# Run it yourself

This repository is a local evaluation workbench, not a hosted model service. The public website cannot send requests to a model.

<section class="reader-section cool" aria-label="Local system boundary">

## Local system boundary

<figure class="system-boundary" aria-labelledby="local-boundary-title" aria-describedby="local-boundary-summary">
  <div class="boundary-heading">
    <div>
      <p class="boundary-eyebrow">Local execution path</p>
      <h3 id="local-boundary-title">One request. One Mac. One selected model.</h3>
    </div>
    <span class="boundary-status">Inference stays local</span>
  </div>
  <div class="local-boundary" aria-label="Local execution boundary on the operator's Mac">
    <p class="boundary-label">Operator's Mac · local trust boundary</p>
    <ol class="system-flow">
      <li>
        <span class="flow-index" aria-hidden="true">01</span>
        <span class="flow-role">Evaluation runner</span>
        <strong>EvalScope CLI</strong>
        <span>Loads the reviewed task and records the aggregate outcome.</span>
      </li>
      <li>
        <span class="flow-index" aria-hidden="true">02</span>
        <span class="flow-role">Loopback transport</span>
        <strong>OpenAI-compatible request</strong>
        <span><code>127.0.0.1</code> keeps model traffic on this computer.</span>
      </li>
      <li>
        <span class="flow-index" aria-hidden="true">03</span>
        <span class="flow-role">Local runtime</span>
        <strong>LM Studio / llmster</strong>
        <span>Serves the model through the local API endpoint.</span>
      </li>
      <li>
        <span class="flow-index" aria-hidden="true">04</span>
        <span class="flow-role">Model under test</span>
        <strong>Selected local model</strong>
        <span>Exactly one reviewed model instance answers the request.</span>
      </li>
    </ol>
    <aside class="remote-guard" role="note">
      <strong>Remote guard</strong>
      <span>A non-null LM Studio <code>deviceIdentifier</code> blocks the run from qualifying as local.</span>
    </aside>
  </div>
  <div class="evidence-boundary" aria-label="Evidence review and publication path after the local run">
    <article class="evidence-destination private">
      <span class="destination-label">Stays private · outside Git</span>
      <h4>Detailed run evidence</h4>
      <p>Datasets, prompts, responses, reasoning, logs, and detailed reports.</p>
    </article>
    <article class="evidence-destination review">
      <span class="destination-label">Required before publishing</span>
      <h4>Allowlist + review gate</h4>
      <p>Checks the aggregate, model alias, privacy boundary, secrets, identity, file type, and size.</p>
    </article>
    <article class="evidence-destination public">
      <span class="destination-label">May be published · after review</span>
      <h4>Public repository</h4>
      <p>Source, synthetic tests, references, and privacy-reviewed aggregates.</p>
    </article>
  </div>
  <aside class="ci-boundary" role="note"><strong>CI boundary:</strong> public GitHub Actions uses hosted runners and static or synthetic inputs. It has no route back to this Mac.</aside>
  <figcaption id="local-boundary-summary">EvalScope sends loopback-only requests to a selected model served by LM Studio. Detailed evidence stays private; only allowlisted, reviewed outputs can enter the public repository.</figcaption>
</figure>

LM Link may remain enabled, but the selected instance must be the local model. This project treats a non-null LM Studio `deviceIdentifier` as remote and blocks that condition for a local result.

</section>

<section class="reader-section warm" aria-label="Headless LM Studio setup">

## Start LM Studio headlessly

LM Studio documents `llmster` as its headless service. Review the local model key before loading it; do not substitute a linked device model.

```bash
lms daemon up
lms ls
lms load qwen3.8-27b-splash \
  --identifier racecraft-splash-local \
  --context-length 32768
lms ps --json
lms server start
```

For the completed run on this Mac, `qwen3.8-27b-splash` was the installed LM Studio model key and `racecraft-splash-local` was the API-facing alias. Before another run, `lms ls` must show that reviewed key and `lms ps --json` must show the alias with `deviceIdentifier: null`. Do not substitute a similarly named model or linked-device instance.

Official references: [LM Studio headless service](https://lmstudio.ai/docs/developer/core/headless) and [`lms load`](https://lmstudio.ai/docs/cli/load).

</section>

<section class="reader-section cool" aria-label="Run GPQA Diamond with EvalScope">

## Run GPQA Diamond

Install the repository dependencies, keep outputs outside the checkout, and use the exact public benchmark name. Remove any `--limit` option for the full evaluation.

```bash
evalscope eval \
  --model racecraft-splash-local \
  --api-url http://127.0.0.1:1234/v1 \
  --api-key EMPTY \
  --eval-type openai_api \
  --datasets gpqa_diamond \
  --dataset-hub modelscope \
  --eval-batch-size 1 \
  --generation-config '{"reasoning_effort":"medium","max_tokens":4096,"retries":0,"stream":false}' \
  --seed 42 \
  --work-dir <PRIVATE_OUTPUT_DIRECTORY> \
  --no-timestamp \
  --enable-progress-tracker \
  --collect-perf
```

Command-line flags can change across EvalScope releases. Use the pinned project version and check the current official EvalScope documentation before reproducing the run. Record the resolved task configuration, model identity, report, and hashes before interpreting a score.

After the run, restore only settings changed for the evaluation. Do not stop or unload services and models that were already running for the operator.

</section>
