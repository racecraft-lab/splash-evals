# Contributing

Contributions should improve reproducible local evaluation or the fidelity of historical
evidence without widening the privacy or inference boundary.

## Development checks

Install the locked development environment and force model-capable paths into mock-only mode:

```bash
uv sync --frozen --extra dev
export LOCAL_EVALS_TEST_MODE=mock-only
```

During development, run the narrowest relevant test file or check first. Before opening a pull
request, run the complete repository checks once:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest
uv run local-evals frontier validate --catalog references/frontier
uv run local-evals privacy audit --scope publication
```

The ordinary frontier validation checks catalog schema and provenance. The stricter
`--require-coverage` form is the scientific comparison gate. It may remain blocked while an
exact shared benchmark version or dataset revision is unknown; do not weaken it or describe an
expected research blocker as a test failure.

The privacy audit requires the configured scanner, approved identity evidence, and any required
GitHub provenance. Report blockers separately from test failures. Its output must remain redacted,
and missing evidence remains blocking.

Use synthetic identifiers, `example.com`, and loopback addresses in tests. Never attach
desktop screenshots, raw model logs, private paths, credentials, restricted benchmark
questions, or dataset archives. Provide a minimal synthetic reproduction instead.

## Historical evidence submissions

Each result record must identify the exact dated model/condition, evidence class, primary
source URL and locator, benchmark revision/split, metric unit, denominator where known,
attempt semantics, protocol/scorer details, rights notes, and unknown fields. Do not infer
a snapshot from an alias. Keep conflicting records separate. A benchmark-name match alone
does not justify a direct score delta.

## Result submissions

Public result exports must be aggregate-only and created from the allowlisted publication
schema. State whether the result is a local measurement, archived-response reanalysis,
published historical reference, incompatible context, or mock. Include denominator and
limitations. Raw responses stay outside the repository.

Dependency, scorer, benchmark, action, and reference changes require human review because
they can change scientific meaning even when unit tests pass. Do not auto-merge them.

## Documentation site

The five public site pages under `docs-site/src/content/docs/` are generated from canonical files
under `docs/`. Edit the canonical source, then install and validate the site with the pinned tools:

```bash
corepack enable
corepack prepare pnpm@10.25.0 --activate
pnpm --dir docs-site install --frozen-lockfile
pnpm --dir docs-site exec playwright install --with-deps chromium
pnpm --dir docs-site content:generate
python3 scripts/docs_artifact.py prepare docs-site/dist
pnpm --dir docs-site validate
python3 scripts/docs_artifact.py verify docs-site/dist
```

## Operator and publication boundaries

Routine contributor checks use synthetic fixtures, loopback mocks, and mock-only mode. Commands
that discover or contact LM Studio, run an evaluation, change or restore runtime settings, collect
GitHub provenance, stage a result export, create a release, or deploy Pages are operator or
maintainer workflows. Follow `docs/operations.md`, `docs/privacy.md`, and the applicable workflow;
their authorization, evidence, and review requirements still apply.

## Agent-instruction rationale (2026-09-21)

The shared agent contract stays concise and routes task-specific procedures here. This follows
[OpenAI's current guidance](https://developers.openai.com/blog/rethinking-skills-and-prompts-for-gpt-6-astra)
to load only task-relevant context and calibrate verification, and
[Claude Code's current guidance](https://code.claude.com/docs/en/best-practices) to keep persistent
instructions short and move occasional workflows out of always-loaded context. The root
`CLAUDE.md` imports `AGENTS.md` using
[Anthropic's documented shared-file pattern](https://code.claude.com/docs/en/memory#share-one-file-with-other-coding-tools).
