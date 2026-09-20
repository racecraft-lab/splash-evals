# Contributing

Contributions should improve reproducible local evaluation or the fidelity of historical
evidence without widening the privacy or inference boundary.

## Before opening a pull request

```bash
uv sync --frozen
uv run ruff format --check .
uv run ruff check .
uv run pytest
uv run local-evals frontier validate --catalog references/frontier
uv run local-evals privacy audit --scope publication
```

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
