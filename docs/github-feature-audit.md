# GitHub feature audit

**Audit date:** 2026-09-19
**Repository target:** `racecraft-lab/splash-evals`
**Requested state:** shown below
**Actual state:** `not-yet-applied` for every row until repository/API/UI read-back and a
safe test pull request prove otherwise.

| Feature | Requested | Actual | Verification required / blocker |
|---|---|---|---|
| Public repo, README, Apache-2.0, community profile | enabled | not-yet-applied | Create only after privacy and identity gates; verify community profile detection. |
| Issues and forms | enabled | not-yet-applied | Verify repository setting and sanitized issue creation. |
| Discussions | deferred | not-yet-applied | Blocked until an active moderation owner and conduct channel are verified. |
| Lightweight Project | if permission allows | not-yet-applied | Non-blocking; do not request paid scope. |
| Repository branch/tag rulesets | required | not-yet-applied | Bootstrap CI first; then require stable `CI / final gate`; protect release tags. |
| GitHub squash provenance exception | local policy, outside-checkout evidence boundary, offline validator, current-main containment, and Actions-context binding implemented; clean-repo live readback pending | not-yet-applied | Only a sanitized, schema-validated API evidence file may exempt an already-merged squash ancestor; verify the clean public repository, ref, actor, identities, trees, current `main` containment, and `CI / final gate` check after publication. |
| Independent approval / CODEOWNERS | team ownership required | not-yet-applied | Create `racecraft-lab/maintainers`, add `fgabelmannjr`, grant `maintain` on this repository only, and verify GitHub recognizes the team rule. A second independent reviewer is not yet available. |
| Dependency graph | enabled | not-yet-applied | Verify lockfile coverage after first push. |
| Dependabot alerts and security updates | enabled | not-yet-applied | Read back repository security settings. |
| Dependabot weekly version updates | configured | not-yet-applied | Verify Actions and uv ecosystem behavior; never auto-merge scientific dependencies. |
| Dependency review | required on PRs | not-yet-applied | Verify API availability and a completed test PR. |
| CodeQL | default setup, once | not-yet-applied | Public-repository code scanning is free; enable after push and require a completed Python/Actions analysis, not just the switch. |
| Secret scanning and repository push protection | enabled | not-yet-applied | Public-repository secret scanning is free and automatic; verify repository-level state and push protection without a purchase or trial. |
| Private vulnerability reporting | enabled | not-yet-applied | `SECURITY.md` is not proof; read back the feature. |
| Standard hosted Actions | enabled | not-yet-applied | Restrict to GitHub-hosted CPU runners; no self-hosted labels, secrets, or Mac bridge. |
| Pages | enabled from Actions | not-yet-applied | Deploy allowlisted static docs and verify the real URL before setting homepage. |
| Draft release, checksums, SBOM, attestations | manual | not-yet-applied | Run only from protected main after scans; keep first release draft/prerelease. |
| Merge queue | deferred | not-yet-applied | Revisit only when contributor volume justifies it. |
| Wiki | disabled | not-yet-applied | Versioned docs and Pages are canonical. |
| Packages, GHCR, Sponsors, Codespaces, Copilot, GitHub Models | not required | not-yet-applied | No initial value; never use GitHub Models for evaluation. |

## Cost and storage policy

The organization currently reports the GitHub Team plan. That does not authorize use of
paid or Team-only capabilities for this project. Configuration is restricted to the
public-repository features GitHub documents as available with GitHub Free: repository
[rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets),
[standard hosted Actions](https://docs.github.com/en/actions/reference/runners/github-hosted-runners),
[Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages),
[code and secret scanning](https://docs.github.com/en/billing/concepts/product-billing/github-advanced-security),
and [private vulnerability reporting](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/report-privately).
The local squash-provenance collector uses the documented [commit](https://docs.github.com/en/rest/commits/commits),
[associated pull-request](https://docs.github.com/en/rest/commits/commits#list-pull-requests-associated-with-a-commit),
[branch](https://docs.github.com/en/rest/branches/branches) and
[compare](https://docs.github.com/en/rest/commits/commits#compare-two-commits),
[pull-request](https://docs.github.com/en/rest/pulls/pulls), and
[check-run](https://docs.github.com/en/rest/checks/runs#list-check-runs-for-a-git-reference)
REST fields, but this document does not claim that the recreated public repository or its
settings have been live-verified.
Do not enable larger runners, private/internal-repository security licensing, trials, or
other plan-dependent add-ons.

Use only standard GitHub-hosted runners. Successful PRs upload no routine artifacts. Failure
diagnostics, when explicitly sanitized, retain for one day. Build/release artifacts retain
for seven days before reviewed immutable release assets are published. Cache only dependency
downloads, never datasets, models, local profiles, credentials, or reports. Stop publication
if the organization's free storage/usage state cannot be verified; do not enable billing or
trials.

## Ruleset bootstrap

The first push cannot require a check that does not yet exist. Push the audited bootstrap,
run CI, record the exact check `CI / final gate`, then activate the ruleset: pull requests
required, conversation resolution, no force-push/deletion, and the stable check. If no
independent reviewer exists, use a documented PR-only zero-approval solo policy with
mandatory CI and record reduced review assurance. Never add a blanket bypass.

## Identity and gate-change review boundary

Contributor and Dependabot commits do not automatically qualify for the platform exception.
The supported path is to inspect the proposed diff, reproduce or re-author the accepted change
as the exact `Racecraft Lab Automation <info@racecraft.co>` identity, rerun the full gate, and
then merge. Evidence produced by Actions remains private outside the checkout; local evidence
is usable only after explicit operator review. GitHub activity remains account-associated. The
tracked policy stores only the approved actor login and platform committer contract; exact API
author/committer name and email metadata is retained only in the private evidence and matched
to the local Git object for that commit.

Changes to `.github/workflows/**`, `configs/policies.yaml`,
`src/local_evals/publication.py`, and `scripts/collect_github_provenance.py` require explicit
maintainer review. The repository's CODEOWNERS coverage is documented, but under the current
single-maintainer/zero-approval policy this review is procedural rather than an independently
enforced reviewer gate. The workflow therefore does not claim autonomous tamper resistance.
