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
