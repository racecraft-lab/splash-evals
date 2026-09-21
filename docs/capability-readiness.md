# Capability-run readiness

> **Archived planning record.** This private 60-case, multi-benchmark study was not completed
> and is no longer a reader-facing site page. It was superseded by the full public GPQA Diamond
> run documented in [What we found](dashboard.md). The record remains here for protocol history.

This page is the tracked go/no-go record for the planned private Splash capability run. A
checked repository gate does not substitute for unfinished research preparation, and the
published post-hoc qualification aggregate is not capability evidence.

<section class="reader-section cool" aria-label="Completed safeguards">

## Completed gates

- [x] Public CI is confined to GitHub-hosted runners, mock or synthetic inputs, and static
  documentation. It has no self-hosted runner, LM Studio route, model download, or connection to
  the evaluation computer.
- [x] Public and private evidence zones are separated. Raw prompts, responses, reasoning,
  runtime identifiers, and private manifests remain outside Git.
- [x] The local request path and built-in exact scorer completed a reviewed post-hoc
  qualification run. Its public purpose is `post_hoc_runtime_scorer_qualification` and
  `capability_evidence` is `false`.
- [x] Dated historical records have source-number and provenance checks. These records provide
  context only; direct historical-comparison coverage remains unavailable.
- [x] The public export path fails closed, strips raw evidence, and requires explicit review.

</section>

<section class="reader-section warm" aria-label="Capability launch checklist">

## Required before a capability run

Complete these items in order. Evidence containing licensed tasks, prompts, expected answers, or
runtime details belongs in private state, not this checklist.

The private preparation packet was revalidated on 2026-09-20 against public commit `af53cd7`.
Its frozen manifest-set digest is
`1b16102c4c515dab6fe51e51f5f2515e5dfadc5bfb16eae71f4adeff6129508f`. The digest establishes
which reviewed packet is awaiting approval; it does not disclose or validate the private tasks.

1. [x] **Confirm official GPQA access and terms.** The private source record captures the
   authorized source, applicable terms, permitted local use, redistribution limits, exact split,
   and retrieval revision. Restricted questions remain outside Git.
2. [x] **Freeze private manifests before looking at results.** Seal the ordered task IDs,
   family membership, split, prompt/template revision, scorer revision, attempt policy, and
   selection hash for calibration and held-out sets. Prove that the sets are disjoint.
3. [x] **Pin IFEval and MMLU-Pro inputs.** Record their exact upstream repository or dataset
   revisions, configurations and splits, ordered sample IDs, and ordered-content hashes. A title
   such as “IFEval” or “MMLU-Pro” is not a revision.
4. [x] **Finish private practical-task sets.** Review and freeze the tool/JSON and context tasks,
   expected outcomes, rubrics, failure categories, ordering, and hashes. Publish only task-family
   descriptions and reviewed aggregates.
5. [x] **Qualify every scorer.** Use synthetic positive, negative, malformed, refusal, timeout,
   and output-limit fixtures for each task family. Pin scorer versions and keep the qualification
   report private until its sanitized summary passes publication review.
6. [x] **Keep coding outside this run.** The initial 60-case study contains no coding tasks.
   Coding remains disabled until the disposable sandbox receives a separate attestation for
   networking, mounts, Docker access, resource bounds, controlled inputs, and teardown.
7. [x] **Run controlled qualification checks.** Reused, non-held-out cases confirmed the local
   request and scorer path. A separate synthetic case then exercised the exact EvalScope to
   OpenAI-compatible `/v1/chat/completions` path with the explicit `medium` reasoning effort. The
   request was accepted and returned a response with zero retries. These checks qualify the
   transport only; neither is capability evidence.
8. [x] **Complete the corrected core dry-run.** Review the full plan without inference: exact
   manifests, counts, budgets, settings, output location, capability labels, stop conditions, and
   publication blockers. The corrected plan has a new experiment identifier, keeps the frozen
   sample and scorer digest, and names `medium` as its OpenAI-compatible reasoning effort. Any
   unresolved or inferred field blocks launch.
9. [ ] **Renew approval for the corrected held-out launch.** A human operator must review the
   corrected condition, confirm that held-out items remain unseen and unused for tuning,
   acknowledge the aborted request described below, authorize the bounded request and time
   budget, and record a new approval privately. The approval for the aborted experiment is not
   reused.
10. [ ] **Execute the new experiment once under its frozen contract.** Do not tune on held-out
    outcomes, silently retry, replace failed tasks, or change the scorer or model condition
    mid-run. A changed condition is another new experiment.
11. [ ] **Prepare and review the public export.** Export only allowlisted aggregates and generic
    public condition labels, rerun the privacy and publication gates, review the exact files, and
    state limitations. Do not claim a frontier delta while direct historical coverage is
    unavailable.

</section>

<section class="reader-section warm" aria-label="Aborted launch record">

## Aborted launch: no capability result

One authorized launch attempt reached LM Studio, but the OpenAI-compatible endpoint rejected the
native value `on` before inference because it is not a valid `reasoning_effort` value. That attempt
produced no model response, completion tokens, score, prediction file, report, or capability
manifest. Its private evidence is preserved as an aborted protocol-invalid experiment; it is not
resumable and is not published as a result.

The correction does not silently reinterpret `on`. It defines a new experiment whose transport
condition is the explicit OpenAI-compatible effort `medium`. A synthetic, non-held-out request
qualified that exact transport. The corrected experiment still requires renewed approval before
any held-out request is sent.

</section>

<section class="reader-section cool" aria-label="Frozen local runtime condition">

## Frozen headless LM Studio condition

LM Studio documents `llmster` as its recommended standalone daemon for headless use. Start it,
load the already-authorized local model under the study identifier, and then start the local API
server:

```bash
lms daemon up
lms ls
lms load <REVIEWED_LOCAL_MODEL_KEY> --identifier racecraft-splash-local --context-length 32768
lms ps
lms server start
```

The study contract is:

- selected API instance: `racecraft-splash-local`;
- context length: 32,768 tokens;
- batch size and maximum in-flight request count: 1;
- core transport: EvalScope through the OpenAI-compatible `/v1/chat/completions` endpoint;
- transmitted OpenAI-compatible reasoning effort: `medium`;
- native model intent: reasoning enabled; `medium` is a newly named transport condition and is
  not claimed to be equivalent to native `on`;
- effective reasoning setting: transmitted but not read back by the compatible endpoint;
- transport retries: 0;
- response caching: disabled;
- no JIT substitution, cloud fallback, or automatic replacement with another loaded model.

Use `lms ps` and the workbench discovery command to confirm the selected instance before the
smoke check and again before launch. If a reload is needed, unload only the project-owned
instance; never unload all models or stop an unrelated user service.

LM Link may remain enabled only when the exact selected `racecraft-splash-local` loaded-instance
record contains `deviceIdentifier: null`. A missing or non-null field blocks the run. A downloaded
model record with the same `modelKey` cannot replace this instance-bound evidence. This explicit
null check is a fail-closed workbench rule derived from the selected-instance record; LM Studio's
public documentation explains local and remote linked devices but does not promise this field-level
null contract. Re-qualify it after an LM Studio or llmster update.

Official LM Studio references:

- [Run LM Studio as a service (headless)](https://lmstudio.ai/docs/developer/core/headless) —
  recommends `llmster`, documents `lms daemon up`, and describes explicit versus JIT loading.
- [`lms load`](https://lmstudio.ai/docs/cli/load) — documents `--identifier`,
  `--context-length`, resource estimation, and model loading.
- [Add an LM Link device](https://lmstudio.ai/docs/lmlink/basics/add-device) — explains that
  linked remote models are device-associated and may be loaded separately from local models.

</section>

<section class="reader-section warm" aria-label="Current readiness conclusion">

## Current conclusion

No successful result exists **under this private 60-case contract**. One request was rejected
before inference, and the later malformed launch did not bind to the frozen sample, so the study
was stopped and not published as capability evidence. The separate full public GPQA Diamond run
is reported on the Results page. It does not complete this private study or create a
protocol-matched frontier delta.

</section>
