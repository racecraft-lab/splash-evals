# GitHub feature audit

**Audit date:** 2026-09-19
**Repository target:** `racecraft-lab/splash-evals`
**Requested state:** shown below
**Actual state:** live API read-back and hosted workflow evidence are recorded below. The
repository was recreated only after the local privacy, identity, and publication gates passed.

| Feature | Requested | Actual | Verification required / blocker |
|---|---|---|---|
| Public repo, README, Apache-2.0, community profile | enabled | verified | Public repository files and community health files were read back after the audited root push. |
| Issues and forms | enabled | verified | Issues are enabled and the tracked forms contain no private state. |
| Discussions | deferred | disabled | No active moderation owner or conduct channel has been established. |
| Lightweight Project | if permission allows | not created | Non-blocking; no paid scope was requested. |
| Repository branch/tag rulesets | required | verified | Active no-bypass rulesets protect `main`, require `CI / final gate`, require PRs and squash merges, and prevent release-tag mutation/deletion. |
| GitHub squash provenance exception | outside-checkout evidence plus offline validation | verified | A GitHub-signed squash commit passed the hosted publication audit and final gate on run `35480361663`. |
| Independent approval / CODEOWNERS | team ownership required | reduced assurance | `racecraft-lab/maintainers` has repository maintain permission and one member; zero approvals plus mandatory CI is enforced because no independent reviewer is available. |
| Dependency graph | enabled | verified | GitHub recognized the tracked lockfile after the initial push. |
| Dependabot alerts and security updates | enabled | verified | Repository security settings were read back as enabled. |
| Dependabot weekly version updates | configured | verified | Dependabot opened update pull requests; scientific dependencies remain manual-review only. |
| Dependency review | required on PRs | verified | The dependency-review check completed on hosted pull-request runs. |
| CodeQL | default setup, once | verified | Default setup completed Python and Actions analysis, including post-merge run `35480361331`. |
| Secret scanning and repository push protection | enabled | verified | Secret scanning, push protection, and validity checks were read back as enabled without a trial. |
| Private vulnerability reporting | enabled | verified | The repository feature was read back as enabled. |
| Standard hosted Actions | enabled | verified | Pull-request and push gates ran on `ubuntu-24.04`; no self-hosted runner, repository secret, webhook, deploy key, or Mac bridge exists. |
| Pages | enabled from Actions | verified | Astro/Starlight builds with the explicit `/splash-evals/` project base, validates links and desktop/mobile routes before upload, and deploys through pinned Pages actions. |
| Draft release, checksums, SBOM, attestations | manual | configured | The workflow is restricted to protected `main`, scans inspected artifacts, emits checksums/SBOM/attestations, and creates draft prereleases for maintainer review. |
| Merge queue | deferred | disabled | Contributor volume does not justify it. |
| Wiki | disabled | verified | Versioned docs and Pages are canonical. |
| Packages, GHCR, Sponsors, Codespaces, Copilot, GitHub Models | not required | not enabled for evaluation | No initial value; GitHub Models is never used for evaluation. |

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
REST fields. The recreated repository, rulesets, security controls, hosted checks, CodeQL,
and Pages state were live-verified after publication.
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
