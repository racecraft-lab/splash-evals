# Sources and evidence

See who measured each result, where it was published, and what was different about the test. Those details explain why the scores are useful context but not always a direct head-to-head comparison.

<section class="reader-section cool" aria-label="Sources behind the numbers">

## Where the numbers come from

<div class="source-map">
  <article class="source-card measured">
    <span class="source-card-label">Measured here</span>
    <h3>Splash / Qwen3.8</h3>
    <p>The 54.55% score, 198/198 completion count, and runtime figures come from the reviewed local EvalScope aggregate.</p>
    <ul>
      <li><a href="https://github.com/racecraft-lab/splash-evals/blob/main/results/public/gpqa-diamond-splash-local-2026-09-20.json">Reviewed public result</a></li>
      <li><a href="https://evalscope.readthedocs.io/en/latest/get_started/supported_dataset/llm.html">EvalScope GPQA documentation</a></li>
    </ul>
    <p class="source-limit"><strong>Limit:</strong> one public benchmark and one exact local condition.</p>
  </article>
  <article class="source-card provider">
    <span class="source-card-label">Model providers</span>
    <h3>OpenAI and Anthropic</h3>
    <p>Provider publications supply the GPT and Claude figures labeled “provider-reported” on Results.</p>
    <ul>
      <li><a href="https://openai.com/index/gpt-5-6/">OpenAI GPT-5.6 comparison</a> and <a href="https://openai.com/index/introducing-gpt-5-5/">GPT-5.5 announcement</a></li>
      <li><a href="https://www.anthropic.com/news/claude-4">Claude 4 announcement</a></li>
      <li><a href="https://www-cdn.anthropic.com/bbd8ef16d70b7a1665f14f306ee88b53f686aa75.pdf">Claude 4.6 system card</a>, <a href="https://www-cdn.anthropic.com/037f06850df7fbe871e206dad004c3db5fd50340/Claude%20Opus%204.7%20System%20Card.pdf">Opus 4.7 system card</a>, and <a href="https://www-cdn.anthropic.com/0b4915911bb0d19eca5b5ee635c80fef830a37ea.pdf">Opus 4.8 system card</a></li>
      <li><a href="https://www.anthropic.com/news/claude-sonnet-5">Sonnet 5</a> and <a href="https://www.anthropic.com/news/claude-opus-5">Opus 5</a> announcements</li>
    </ul>
    <p class="source-limit"><strong>Limit:</strong> providers do not disclose every field needed to match the local protocol.</p>
  </article>
  <article class="source-card independent">
    <span class="source-card-label">Independent evaluation</span>
    <h3>Epoch AI scorecards</h3>
    <p>Epoch supplies independently run, rounded GPQA observations for the requested Claude roster from Sonnet 4 through Opus 5.</p>
    <ul>
      <li><a href="https://epoch.ai/models/search">Epoch AI model catalog</a></li>
      <li><a href="https://epoch.ai/benchmarks/gpqa-diamond">Epoch GPQA Diamond methodology</a></li>
    </ul>
    <p class="source-limit"><strong>Limit:</strong> the displayed scorecards are not protocol-matched to this EvalScope run.</p>
  </article>
  <article class="source-card context">
    <span class="source-card-label">Method and runtime context</span>
    <h3>Older frontier records + LM Studio</h3>
    <p>Dated OpenAI records supply older-frontier context. LM Studio documentation supports the headless runtime commands—not the evaluation result.</p>
    <ul>
      <li><a href="https://openai.com/index/gpt-4-1/">OpenAI GPT-4.1 appendix</a></li>
      <li><a href="https://lmstudio.ai/docs/developer/core/headless">LM Studio headless guide</a> and <a href="https://lmstudio.ai/docs/cli/load"><code>lms load</code> reference</a></li>
    </ul>
    <p class="source-limit"><strong>Limit:</strong> answer extraction affects older GPQA figures; project locality checks go beyond runtime documentation.</p>
  </article>
</div>

<p class="source-guidance"><strong>How to read the labels:</strong> “measured here” is this project's local result; “provider-reported” comes from the model developer; “cross-provider” is one developer reporting another's model; “independent” comes from Epoch. The <a href="/splash-evals/dashboard/#benchmark-comparison">Results explorer</a> keeps these labels attached to every observation.</p>

</section>

<section class="reader-section warm" aria-label="How evidence records work">

## How the evidence catalog works

<dl class="evidence-principles">
  <div><dt>One condition per record</dt><dd>A record ties one model to one benchmark, metric, source, and testing condition. The same model can have several legitimate records.</dd></div>
  <div><dt>Unknown stays unknown</dt><dd>A missing field means the source did not establish it. It is never silently treated as zero or filled by assumption.</dd></div>
  <div><dt>Same benchmark is not the same protocol</dt><dd>Scores become directly comparable only when the dataset, prompt, attempts, reasoning budget, extraction, and scoring rules align.</dd></div>
</dl>

[Browse the evidence records](https://github.com/racecraft-lab/splash-evals/tree/main/references/frontier) · [See what a fair comparison requires](methodology.md#what-makes-a-fair-comparison)

</section>

<section class="reader-section cool" aria-label="How reported numbers were checked">

## How the numbers were checked

We checked each published score against its original report, then recorded the details that affect how the result should be read.

<div class="verification-summary">
  <div><strong>Value</strong><span>Does the saved percentage match the cited source?</span></div>
  <div><strong>Locator</strong><span>Can another reviewer find the figure on the named page, table, or scorecard?</span></div>
  <div><strong>Condition</strong><span>Did we preserve qualifications such as thinking mode, trial count, or rounding?</span></div>
</div>

<aside class="verification-limit" role="note"><strong>What a match means:</strong> the repository copied the cited value and its stated condition correctly. It does <strong>not</strong> reproduce the provider's run or turn the score into a protocol-matched comparison.</aside>

[Review the checked-in verification record](https://github.com/racecraft-lab/splash-evals/blob/main/references/frontier/VERIFICATION.md) · [Review the local result](dashboard.md)

</section>
