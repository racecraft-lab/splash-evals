# Run it yourself

This repository is a local evaluation workbench, not a hosted model service. The public website cannot send requests to a model.

<section class="reader-section cool" aria-label="Local system boundary">

## Local system boundary

<figure class="system-boundary" data-architecture-flow aria-labelledby="local-boundary-title" aria-describedby="local-boundary-summary">
  <div class="boundary-heading">
    <div>
      <p class="boundary-eyebrow">Local execution path</p>
      <h3 id="local-boundary-title">One request. One Mac. One selected model.</h3>
    </div>
    <span class="boundary-status">Inference stays local</span>
  </div>
  <div class="flow-controls" data-flow-controls hidden>
    <span class="flow-actions">
      <button type="button" class="flow-play" data-flow-play aria-pressed="false">Play request</button>
      <button type="button" class="flow-reset" data-flow-reset>Reset</button>
    </span>
    <span class="flow-status" data-flow-status aria-live="polite">Ready · choose a stage or play the request.</span>
  </div>
  <div class="local-boundary" aria-label="Local execution boundary on the operator's Mac">
    <p class="boundary-label">Operator's Mac · local trust boundary</p>
    <ol class="system-flow">
      <li data-flow-step>
        <button type="button" class="flow-trigger" data-flow-trigger data-step-label="EvalScope prepares the task" aria-expanded="true" aria-controls="flow-detail-1">
          <span class="flow-index" aria-hidden="true">01</span>
          <span class="flow-role">Prepare</span>
          <strong>EvalScope</strong>
        </button>
      </li>
      <li data-flow-step>
        <button type="button" class="flow-trigger" data-flow-trigger data-step-label="The request crosses loopback" aria-expanded="true" aria-controls="flow-detail-2">
          <span class="flow-index" aria-hidden="true">02</span>
          <span class="flow-role">Send locally</span>
          <strong>Loopback API</strong>
        </button>
      </li>
      <li data-flow-step>
        <button type="button" class="flow-trigger" data-flow-trigger data-step-label="LM Studio invokes Splash" aria-expanded="true" aria-controls="flow-detail-3">
          <span class="flow-index" aria-hidden="true">03</span>
          <span class="flow-role">Generate</span>
          <strong>Splash runtime</strong>
        </button>
      </li>
      <li data-flow-step>
        <button type="button" class="flow-trigger" data-flow-trigger data-step-label="Qwen3.8 returns the answer" aria-expanded="true" aria-controls="flow-detail-4">
          <span class="flow-index" aria-hidden="true">04</span>
          <span class="flow-role">Return + score</span>
          <strong>Qwen3.8 answer</strong>
        </button>
      </li>
    </ol>
    <div class="flow-console" aria-label="Selected execution stage">
      <div class="flow-detail" id="flow-detail-1" data-flow-detail>
        <div>
          <p class="flow-kicker">01 · Build the test request</p>
          <h4>EvalScope loads one GPQA item</h4>
          <p>The runner combines the question, answer format, and fixed generation settings into one auditable request.</p>
        </div>
        <dl class="flow-facts">
          <div><dt>Input</dt><dd>Reviewed GPQA task</dd></div>
          <div><dt>Work</dt><dd>Apply the frozen run settings</dd></div>
          <div><dt>Output</dt><dd>One request envelope</dd></div>
        </dl>
      </div>
      <div class="flow-detail" id="flow-detail-2" data-flow-detail>
        <div>
          <p class="flow-kicker">02 · Cross the local bridge</p>
          <h4>The request travels over loopback</h4>
          <p>EvalScope calls LM Studio's OpenAI-compatible endpoint at <code>127.0.0.1</code>. The request never needs a public inference route.</p>
        </div>
        <dl class="flow-facts">
          <div><dt>Protocol</dt><dd>OpenAI-compatible JSON</dd></div>
          <div><dt>Route</dt><dd>This Mac only</dd></div>
          <div><dt>Endpoint</dt><dd><code>127.0.0.1:1234</code></dd></div>
        </dl>
      </div>
      <div class="flow-detail" id="flow-detail-3" data-flow-detail>
        <div>
          <p class="flow-kicker">03 · Run local inference</p>
          <h4>LM Studio invokes Inco AI's Splash engine</h4>
          <p>Splash executes the selected Qwen3.8-27B model on Apple silicon and streams generated tokens back through the local endpoint.</p>
        </div>
        <dl class="flow-facts">
          <div><dt>Host</dt><dd>LM Studio / llmster</dd></div>
          <div><dt>Engine</dt><dd>Inco AI Splash</dd></div>
          <div><dt>Measured pace</dt><dd>64.13 output tok/s</dd></div>
        </dl>
      </div>
      <div class="flow-detail" id="flow-detail-4" data-flow-detail>
        <div>
          <p class="flow-kicker">04 · Capture the evidence</p>
          <h4>The answer returns to EvalScope for scoring</h4>
          <p>EvalScope extracts the selected answer, checks it against the benchmark key, and adds the outcome to the 198-question aggregate.</p>
        </div>
        <dl class="flow-facts">
          <div><dt>Model</dt><dd>Qwen3.8-27B</dd></div>
          <div><dt>Scoring</dt><dd>Exact multiple-choice answer</dd></div>
          <div><dt>Run result</dt><dd>108 / 198 correct</dd></div>
        </dl>
      </div>
    </div>
    <aside class="remote-guard" role="note">
      <strong>Remote guard</strong>
      <span>The selected-instance check determines whether execution qualifies as local. A non-null LM Studio <code>deviceIdentifier</code> blocks that claim.</span>
    </aside>
  </div>
  <div class="evidence-boundary" aria-label="Evidence review and publication path after the local run">
    <article class="evidence-destination private">
      <span class="destination-label">Stays private · outside Git</span>
      <h4>Detailed run evidence</h4>
      <p>Prompts, responses, reasoning, logs, and detailed reports remain private.</p>
    </article>
    <article class="evidence-destination review">
      <span class="destination-label">Required before publishing</span>
      <h4>Allowlist + review gate</h4>
      <p>Privacy, identity, secrets, file type, and size are checked.</p>
    </article>
    <article class="evidence-destination public">
      <span class="destination-label">May be published · after review</span>
      <h4>Public repository</h4>
      <p>Only the approved aggregate and its public evidence are published.</p>
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
