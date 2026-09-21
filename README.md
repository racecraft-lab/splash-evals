# Splash Evals

Splash Evals is a privacy-first workbench for evaluating Splash/Qwen3.8 through an
existing **local LM Studio server** and placing the resulting evidence beside dated,
source-verified results for current and previous-generation frontier models. The goal is
to measure how close the local setup comes on comparable tasks, not to assume it wins.

The project keeps three claims separate:

- **Local measurement:** generated on the operator's Mac through a verified loopback
  LM Studio endpoint.
- **Published historical reference:** a factual result transcribed from a primary
  publisher or benchmark-operator source.
- **Mock result:** transport and scorer test data; never evidence of model capability.

There is no overall "IQ" score. Reports are capability-by-capability and preserve the
benchmark version, denominator, protocol, uncertainty, evidence class, and comparison
limitations. A working harness does not answer the Splash research question unless the
served model is verified as Splash.

## Safety boundary

- Public CI runs only on standard GitHub-hosted CPU runners with synthetic fixtures and
  mock loopback servers.
- CI never starts LM Studio, downloads model weights or datasets, calls a model provider,
  contacts a private machine, or uses a self-hosted runner.
- Raw prompts, responses, benchmark data, runtime inventories, credentials, and private
  settings live in an external state directory, never in this repository.
- Generated code is never executed on the host. Coding evaluation requires a separately
  verified disposable sandbox with no network, host-home mount, or Docker socket.

See the [measured result](docs/dashboard.md), [methodology](docs/methodology.md),
[operations guide](docs/operations.md), [source record](docs/sources.md), and the
[historical catalog](references/frontier/README.md).
The same public material is available in the
[Starlight documentation site](https://racecraft-lab.github.io/splash-evals/).

## Quick start

Requires Python 3.11+ and `uv`. Copy the example environment file and set only the
loopback endpoint and external state location appropriate to your machine:

```bash
cp .env.example .env
uv sync --frozen
uv run local-evals doctor
uv run local-evals frontier validate --catalog references/frontier
uv run local-evals frontier validate --catalog references/frontier --require-coverage
uv run local-evals run --suite smoke --config lmstudio-as-found
```

The first catalog command validates every record's schema and provenance. The strict
form also requires an exact shared benchmark version and dataset revision; it is the
research-comparison gate and intentionally fails while that evidence is unknown.

The smoke suite uses synthetic tasks. Live evaluation requires a verified local LM Studio
instance and writes evidence outside the checkout. Expanded studies require explicit
`--allow-expanded` authorization.

## Current public result

Splash / Qwen3.8 scored **54.6% accuracy** on the complete 198-question GPQA Diamond
benchmark through local LM Studio. All 198 requests completed. The reviewed public record
includes aggregate accuracy, execution counts, performance, configuration, limitations, and
provenance hashes; raw prompts, responses, reasoning, local paths, and private runtime
identifiers remain outside Git.

Publisher-reported frontier scores are directional context, not a protocol-matched ranking or
exact delta. See [What we found](docs/dashboard.md) and [How we tested](docs/methodology.md).

## License and upstream rights

New project code is licensed under Apache-2.0. Benchmark datasets, model weights,
historical responses, and upstream evaluation frameworks retain their own licenses and
access rules and are not redistributed here.

This project is independent and is not an official project of LM Studio, Splash, Qwen,
OpenAI, Anthropic, Google, Aider, or EvalScope.
