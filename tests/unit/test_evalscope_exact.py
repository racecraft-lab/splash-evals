from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_evals.evalscope_exact import (
    CONTEXT_SCORER_REVISION,
    TOOL_SCORER_REVISION,
    aggregate_exact_scores,
    normalize_context_answer,
    score_context_output,
    score_tool_output,
    validate_private_record,
)

TOOL_SCHEMA = {
    "type": "object",
    "properties": {"city": {"type": "string"}, "units": {"enum": ["c"]}},
    "required": ["city", "units"],
    "additionalProperties": False,
}


def _tool_call(name: str, arguments: object) -> SimpleNamespace:
    return SimpleNamespace(function=SimpleNamespace(name=name, arguments=arguments))


def test_tool_score_requires_one_exact_schema_valid_call() -> None:
    expected = {"city": "Chicago", "units": "c"}
    result = score_tool_output(
        expected_name="weather",
        expected_arguments=expected,
        schema=TOOL_SCHEMA,
        stop_reason="tool_calls",
        error=None,
        tool_calls=[_tool_call("weather", expected)],
    )
    assert result.passed == 1
    assert result.failure_code == "passed"


@pytest.mark.parametrize(
    ("stop_reason", "error", "calls", "failure_code"),
    [
        ("length", None, [_tool_call("weather", {"city": "Chicago", "units": "c"})], "truncated"),
        ("stop", None, [_tool_call("weather", {"city": "Chicago", "units": "c"})], "no_tool_call"),
        ("tool_calls", "timeout", [], "inference_error"),
        ("tool_calls", None, [], "tool_call_count"),
        (
            "tool_calls",
            None,
            [_tool_call("weather", {}), _tool_call("weather", {})],
            "tool_call_count",
        ),
        ("tool_calls", None, [_tool_call("other", {"city": "Chicago", "units": "c"})], "tool_name"),
        ("tool_calls", None, [_tool_call("weather", '{"city":')], "malformed_arguments"),
        (
            "tool_calls",
            None,
            [_tool_call("weather", '{"city":"Chicago","city":"Chicago","units":"c"}')],
            "malformed_arguments",
        ),
        ("tool_calls", None, [_tool_call("weather", {"city": "Chicago"})], "schema_mismatch"),
        (
            "tool_calls",
            None,
            [_tool_call("weather", {"city": "Chicago", "units": "c", "extra": 1})],
            "schema_mismatch",
        ),
        (
            "tool_calls",
            None,
            [_tool_call("weather", {"city": "Austin", "units": "c"})],
            "argument_mismatch",
        ),
    ],
)
def test_tool_score_failures_remain_in_denominator(
    stop_reason: str,
    error: str | None,
    calls: list[SimpleNamespace],
    failure_code: str,
) -> None:
    result = score_tool_output(
        expected_name="weather",
        expected_arguments={"city": "Chicago", "units": "c"},
        schema=TOOL_SCHEMA,
        stop_reason=stop_reason,
        error=error,
        tool_calls=calls,
    )
    assert result.passed == 0
    assert result.failure_code == failure_code


def test_context_score_is_exact_after_documented_normalization() -> None:
    assert normalize_context_answer("  CAFÉ\nblue  ") == "café blue"
    assert (
        score_context_output(
            targets=["Café blue", "azure"],
            prediction="  CAFE\u0301   BLUE ",
            stop_reason="stop",
            error=None,
        ).passed
        == 1
    )
    assert (
        score_context_output(
            targets=["Café blue"],
            prediction="Café blue.",
            stop_reason="stop",
            error=None,
        ).failure_code
        == "target_mismatch"
    )


def test_tool_argument_equality_is_json_type_exact() -> None:
    result = score_tool_output(
        expected_name="fixture",
        expected_arguments={"value": 1},
        schema={"type": "object"},
        stop_reason="tool_calls",
        error=None,
        tool_calls=[_tool_call("fixture", {"value": True})],
    )
    assert result.failure_code == "argument_mismatch"


@pytest.mark.parametrize(
    ("prediction", "stop_reason", "error", "failure_code"),
    [
        ("answer", "length", None, "truncated"),
        ("answer", "stop", "timeout", "inference_error"),
        ("", "stop", None, "malformed_prediction"),
        (None, "stop", None, "malformed_prediction"),
    ],
)
def test_context_score_fails_closed(
    prediction: str | None,
    stop_reason: str,
    error: str | None,
    failure_code: str,
) -> None:
    result = score_context_output(
        targets=["answer"], prediction=prediction, stop_reason=stop_reason, error=error
    )
    assert result.passed == 0
    assert result.failure_code == failure_code


def test_private_records_require_exact_scorer_inputs() -> None:
    tool_record = {
        "sample_id": "tool-01",
        "subset": "tool_json",
        "messages": [{"role": "user", "content": "private fixture"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "weather",
                    "description": "fixture",
                    "parameters": TOOL_SCHEMA,
                },
            }
        ],
        "expected_tool_call": {
            "name": "weather",
            "arguments": {"city": "Chicago", "units": "c"},
        },
    }
    validate_private_record("tool_json", tool_record)
    validate_private_record(
        "context",
        {
            "sample_id": "context-01",
            "subset": "context",
            "messages": [{"role": "user", "content": "private fixture"}],
            "targets": ["answer", "alternate"],
        },
    )

    invalid = dict(tool_record)
    invalid["expected_tool_call"] = {"name": "weather", "arguments": {"city": "Chicago"}}
    with pytest.raises(ValueError, match="expected arguments"):
        validate_private_record("tool_json", invalid)


def test_sanitized_aggregate_uses_fixed_planned_denominator() -> None:
    aggregate = aggregate_exact_scores(
        [1, 0, 1],
        planned_denominator=4,
        scorer_revision=TOOL_SCORER_REVISION,
    )
    assert aggregate == {
        "accuracy": 0.5,
        "passed": 2,
        "incorrect": 2,
        "observed": 3,
        "planned_denominator": 4,
        "scorer_revision": TOOL_SCORER_REVISION,
    }
    assert "prediction" not in aggregate
    assert CONTEXT_SCORER_REVISION != TOOL_SCORER_REVISION


@pytest.mark.parametrize(
    ("benchmark", "subset", "count"),
    [("racecraft_tool_json", "tool_json", 10), ("racecraft_context", "context", 8)],
)
def test_evalscope_mock_cli_runs_registered_exact_adapter_end_to_end(
    tmp_path: Path, benchmark: str, subset: str, count: int
) -> None:
    pytest.importorskip("evalscope")
    records_path = tmp_path / f"{subset}.jsonl"
    records = []
    for index in range(count):
        base = {
            "sample_id": f"synthetic-{index:02d}",
            "subset": subset,
            "messages": [{"role": "user", "content": "synthetic mock prompt"}],
        }
        if subset == "tool_json":
            records.append(
                {
                    **base,
                    "tools": [
                        {
                            "type": "function",
                            "function": {
                                "name": "fixture_tool",
                                "description": "synthetic fixture",
                                "parameters": {
                                    "type": "object",
                                    "properties": {"value": {"type": "integer"}},
                                    "required": ["value"],
                                    "additionalProperties": False,
                                },
                            },
                        }
                    ],
                    "expected_tool_call": {
                        "name": "fixture_tool",
                        "arguments": {"value": 1},
                    },
                }
            )
        else:
            records.append({**base, "targets": ["Default output from mockllm/model"]})
    records_path.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    dataset_args = {
        benchmark: {
            "dataset_id": str(records_path),
            "subset_list": [subset],
            "few_shot_num": 0,
            "few_shot_random": False,
            "shuffle": False,
        }
    }
    completed = subprocess.run(  # noqa: S603 - current venv interpreter, no shell
        [
            sys.executable,
            "-m",
            "local_evals.evalscope_exact",
            "eval",
            "--model",
            "offline-mock",
            "--model-id",
            "offline-mock",
            "--eval-type",
            "mock_llm",
            "--datasets",
            benchmark,
            "--dataset-args",
            json.dumps(dataset_args, separators=(",", ":")),
            "--eval-batch-size",
            "1",
            "--repeats",
            "1",
            "--work-dir",
            str(tmp_path / "work"),
            "--no-timestamp",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert "Traceback" not in completed.stderr
