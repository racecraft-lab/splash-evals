# Operations

## Normal local sequence

```bash
uv run local-evals doctor
uv run local-evals lmstudio discover
uv run local-evals lmstudio snapshot
uv run local-evals plan --suite pilot --config lmstudio-as-found
uv run local-evals run --suite smoke --config lmstudio-as-found
uv run local-evals run --suite pilot --config lmstudio-as-found
uv run local-evals optimize --matrix bounded --baseline lmstudio-as-found --dry-run
uv run local-evals optimize --matrix bounded --baseline lmstudio-as-found
uv run local-evals run --suite pilot --config SELECTED_FROZEN_CANDIDATE
uv run local-evals compare --runs BASELINE_RUN CANDIDATE_RUN
uv run local-evals settings export --config SELECTED_FROZEN_CANDIDATE
uv run local-evals settings restore --snapshot SNAPSHOT_ID --dry-run
```

Execute restoration only after its dry-run confirms the current settings still equal the
project-written values. Stop only project-owned UI/workers/instances. Never unload all
models or stop an existing user server.

## Historical catalog and publication

```bash
uv run local-evals frontier validate --catalog references/frontier
uv run local-evals frontier validate --catalog references/frontier --require-coverage
uv run local-evals frontier compare --run SPLASH_RUN --catalog references/frontier
uv run local-evals privacy audit --scope publication
uv run local-evals publish prepare --run SPLASH_RUN --dry-run
```

The privacy audit is the implemented publication gate. It inspects the worktree, reachable Git
history, commit/tag identities, filenames, file categories, and the checksum-pinned Gitleaks scan.
Set the approved public identity and an external `LOCAL_EVALS_STATE_DIR` before invoking it.

Use the first validation for catalog integrity in public CI. Use
`--require-coverage` before claiming protocol-matched historical coverage; unknown
benchmark versions or dataset revisions keep that stricter gate closed.

Expanded core, long-context, precision, concurrency, and agentic runs require a separate
budget and `--allow-expanded`. Model or dataset downloads require separate authorization.

## Public automation boundary

CI uses standard GitHub-hosted Ubuntu runners, locked dependencies, and mock/synthetic data.
Successful pull requests upload no routine artifacts. Pages receives only allowlisted static
documentation. Release preparation is manual from protected `main`, produces a draft
prerelease, checks package members, creates checksums and a minimal CycloneDX SBOM, and
attests only sanitized artifacts. Public attestations record provenance in a public
transparency system; they do not certify capability, security, or privacy.

Never register a public-repository self-hosted runner, open a tunnel, use remote desktop,
install a workflow-polling launch agent, or dispatch a repository event to this workstation.
