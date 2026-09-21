# Agent safety contract

These rules apply to every automated change in this repository.

1. Preserve the research objective: evaluate verified Splash/Qwen3.8 through local LM
   Studio against dated current and previous-generation frontier evidence and practical tasks. Harness work and speed
   tuning are supporting work, not substitute results.
2. Never add a cloud inference route, hosted grader, provider fallback, web-chat
   automation, LM Link route, tunnel, self-hosted GitHub runner, repository-dispatch
   bridge, or workflow that can contact an operator machine.
3. CI may use synthetic fixtures and loopback mock servers only. A mock result must be
   labeled `mock` and must never enter a capability report as a measurement.
4. Keep raw prompts, responses, reasoning, datasets, machine/runtime inventories,
   settings snapshots, credentials, private paths, and audit denylist terms outside Git.
5. Never execute model-generated code on the host. Fail closed unless the grading
   container's isolation has been verified.
6. Do not turn null/unknown fields into zero or infer model snapshots, settings, scores,
   uncertainty, or comparability. Every number needs a source locator and unit.
7. Run the publication gate against staged files and outgoing history before every push,
   release, Pages deployment, or public result export. Do not print matched sensitive
   text.
8. Pin GitHub Actions to full commit SHAs with version/source comments. Keep workflow
   permissions minimal and never use `pull_request_target` to execute contributor code.
9. Do not create CODEOWNERS rules, conduct contacts, author names, emails, or team slugs
   unless the organization has verified them.
10. Preserve unrelated work. Do not weaken a failing privacy, locality, scientific, or
    release gate to make automation green.

## Evidence interpretation

- Qualification results, mocks, and passing harness tests do not establish model capability.
- A shared benchmark name does not establish comparability. Require compatible versions,
  datasets, protocols, scorers, budgets, attempt semantics, and denominators.
- Loopback connectivity does not establish physical locality. Require the repository's
  instance and execution evidence.

## Working approach

- Resolve discoverable questions from repository evidence. Ask only when different reasonable
  interpretations would materially change the outcome; make routine decisions within scope.
- Implement the smallest complete solution. Retain defensive checks at privacy, locality,
  grading, and publication boundaries.
- Report what changed, the relevant verification, and unresolved blockers. Broaden or repeat
  checks only when a change, failure, or unresolved concern justifies it.

## Read when relevant

| Task | Source |
| --- | --- |
| Development and validation | `CONTRIBUTING.md` |
| Runtime, locality, or grading | `docs/architecture.md`, `docs/operations.md`, and applicable files under `configs/` |
| Results and historical comparisons | `docs/methodology.md`, reviewed public result records, and `references/frontier/VERIFICATION.md` |
| Privacy, release, or publication | `docs/privacy.md` and the applicable workflow under `.github/workflows/` |
| Website content | Canonical files under `docs/` and `docs-site/scripts/generate-content.mjs` |

Files under `docs-site/src/content/docs/` are generated outputs. Edit their canonical `docs/`
sources and regenerate them; do not edit generated pages directly.

Keep this shared file under 100 lines and 6 KiB as a project editing budget. Keep changing
scores, model guidance, and runtime limits in their existing authoritative sources.
