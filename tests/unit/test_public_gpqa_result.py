from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RESULT_PATH = ROOT / "results" / "public" / "gpqa-diamond-splash-local-2026-09-20.json"


def _walk(value: Any) -> Iterator[tuple[str | None, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield None, child
            yield from _walk(child)


def test_public_gpqa_result_contains_reviewed_aggregate_evidence_only() -> None:
    result = json.loads(RESULT_PATH.read_text(encoding="utf-8"))

    assert result["schema_version"] == 1
    assert result["purpose"] == "public_capability_result"
    assert result["benchmark"] == {
        "name": "GPQA Diamond",
        "adapter": "gpqa_diamond",
        "dataset_hub": "modelscope",
        "dataset_id": "AI-ModelScope/gpqa_diamond",
        "dataset_revision": None,
        "evaluation_version": "v1.0",
        "split": "train",
        "subset": "default",
        "few_shot_examples": 0,
        "requested_cases": 198,
        "succeeded_cases": 198,
        "errored_cases": 0,
        "metric": "accuracy",
        "score": 0.5455,
    }
    assert result["evaluation"] == {
        "framework": "EvalScope",
        "framework_version": "1.12.0",
        "reasoning_effort": "medium",
        "batch_size": 1,
        "retries": 0,
        "max_output_tokens": 4096,
        "stream": False,
        "seed": 42,
    }
    assert result["model"] == {
        "public_label": "Splash / Qwen3.8",
        "evaluation_alias": "racecraft-splash-local",
    }
    assert result["runtime"] == {
        "provider": "LM Studio",
        "location": "local",
        "interface": "OpenAI-compatible",
        "duration_seconds": 8716,
    }

    performance = result["performance"]
    assert performance["latency_seconds"] == {
        "average": 43.950018,
        "standard_deviation": 22.289392,
        "minimum": 8.581749,
        "p25": 21.242902,
        "median": 41.800779,
        "p75": 65.588142,
        "p90": 72.486143,
        "p99": 76.502371,
        "maximum": 89.420508,
    }
    assert performance["average_output_tokens_per_second"] == 64.13
    assert performance["average_requests_per_second"] == 0.0228
    assert performance["average_input_tokens"] == 277
    assert performance["average_output_tokens"] == 2818.489899
    assert performance["total_input_tokens"] == 54846
    assert performance["total_output_tokens"] == 558061
    assert performance["total_tokens"] == 612907
    assert performance["output_token_cap"] == {
        "limit": 4096,
        "cases_at_limit": 81,
        "total_cases": 198,
    }
    assert performance["time_to_first_token"] == "unavailable"  # noqa: S105
    assert performance["time_per_output_token"] == "unavailable"  # noqa: S105

    assert result["provenance"] == {
        "report_sha256": "5863ba54c2ec07cb1e508585d8e2129e863e1573fda2a23cf2aae241bec5e56a",
        "task_config_sha256": ("f69e7d5ccead9ed80ebcf77eb26f7e5cdfe3e24c3483b9d06db5ef30863420e2"),
        "progress_sha256": ("f71604e1b66511b152428fb49e07334602309aaa70afe233b49347ec4f542c69"),
    }
    assert result["public_evidence_boundary"]["excluded"] == [
        "raw prompts",
        "raw responses",
        "reasoning traces",
        "local paths",
        "private runtime identifiers",
    ]
    assert result["limitations"] == [
        "The upstream dataset revision was not recorded by the resolved adapter.",
        "81 of 198 cases reached the 4096-token output cap.",
        (
            "Time to first token and time per output token were unavailable from the "
            "compatible endpoint."
        ),
        (
            "Publisher-reported comparison scores may use protocols that are not identical "
            "to this evaluation."
        ),
    ]


def test_public_gpqa_result_rejects_private_fields_and_absolute_paths() -> None:
    result = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
    forbidden_keys = {
        "api_key",
        "chain_of_thought",
        "endpoint_url",
        "local_path",
        "local_paths",
        "prompt",
        "prompts",
        "raw_prompt",
        "raw_prompts",
        "raw_response",
        "raw_responses",
        "reasoning",
        "response",
        "responses",
        "runtime_identifier",
        "runtime_identifiers",
    }
    absolute_path = re.compile(r"(?:^|\s)(?:/[A-Za-z0-9_.-]+|[A-Za-z]:[\\/])")

    for key, value in _walk(result):
        if key is not None:
            assert key.casefold() not in forbidden_keys
        if isinstance(value, str):
            assert absolute_path.search(value) is None
