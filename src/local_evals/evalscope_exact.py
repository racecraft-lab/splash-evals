"""Project-owned EvalScope adapters for exact tool and context scoring.

The private datasets and raw model outputs remain in external state.  This
module intentionally exposes only deterministic pass/fail decisions and
aggregate counters; it never logs prompts, targets, arguments, or responses.
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from jsonschema import (  # type: ignore[import-untyped]
    Draft202012Validator,
    SchemaError,
    ValidationError,
)

TOOL_BENCHMARK = "racecraft_tool_json"
CONTEXT_BENCHMARK = "racecraft_context"
ADAPTER_REVISION = "racecraft-evalscope-exact-v1"
TOOL_SCORER_REVISION = "tool-json-schema-exact-v1"
CONTEXT_SCORER_REVISION = "context-exact-v1"
TOOL_PLANNED_DENOMINATOR = 10
CONTEXT_PLANNED_DENOMINATOR = 8

_REGISTERED = False


@dataclass(frozen=True)
class ExactScore:
    """A leak-free per-sample scoring decision."""

    passed: int
    failure_code: str


def normalize_context_answer(value: str) -> str:
    """Normalize Unicode, case, and whitespace without dropping punctuation."""

    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def _failed(code: str) -> ExactScore:
    return ExactScore(passed=0, failure_code=code)


def _tool_parts(tool_call: object) -> tuple[object, object, object]:
    if isinstance(tool_call, Mapping):
        function = tool_call.get("function")
        if isinstance(function, Mapping):
            return function.get("name"), function.get("arguments"), tool_call.get("parse_error")
        return None, None, tool_call.get("parse_error")
    function = getattr(tool_call, "function", None)
    return (
        getattr(function, "name", None),
        getattr(function, "arguments", None),
        getattr(tool_call, "parse_error", None),
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_nonfinite_number(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _json_values_equal(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return set(left) == set(right) and all(
            _json_values_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _json_values_equal(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return left == right


def score_tool_output(
    *,
    expected_name: str,
    expected_arguments: Mapping[str, Any],
    schema: Mapping[str, Any],
    stop_reason: object,
    error: object,
    tool_calls: Sequence[object] | None,
) -> ExactScore:
    """Score one tool result using exact name, object, and JSON Schema checks."""

    if error:
        return _failed("inference_error")
    if stop_reason == "length":
        return _failed("truncated")
    if stop_reason != "tool_calls":
        return _failed("no_tool_call")
    calls = list(tool_calls or ())
    if len(calls) != 1:
        return _failed("tool_call_count")
    name, arguments, parse_error = _tool_parts(calls[0])
    if parse_error:
        return _failed("malformed_arguments")
    if name != expected_name:
        return _failed("tool_name")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(
                arguments,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_nonfinite_number,
            )
        except (json.JSONDecodeError, ValueError):
            return _failed("malformed_arguments")
    if not isinstance(arguments, Mapping):
        return _failed("malformed_arguments")
    try:
        Draft202012Validator.check_schema(dict(schema))
        Draft202012Validator(dict(schema)).validate(dict(arguments))
    except (SchemaError, ValidationError):
        return _failed("schema_mismatch")
    try:
        json.dumps(arguments, allow_nan=False)
        json.dumps(expected_arguments, allow_nan=False)
    except (TypeError, ValueError):
        return _failed("malformed_arguments")
    if not _json_values_equal(dict(arguments), dict(expected_arguments)):
        return _failed("argument_mismatch")
    return ExactScore(passed=1, failure_code="passed")


def score_context_output(
    *,
    targets: Sequence[str],
    prediction: object,
    stop_reason: object,
    error: object,
) -> ExactScore:
    """Score one context result by exact normalized target membership."""

    if error:
        return _failed("inference_error")
    if stop_reason == "length":
        return _failed("truncated")
    if stop_reason != "stop":
        return _failed("incomplete")
    if not isinstance(prediction, str):
        return _failed("malformed_prediction")
    normalized = normalize_context_answer(prediction)
    if not normalized:
        return _failed("malformed_prediction")
    normalized_targets = {normalize_context_answer(target) for target in targets}
    if normalized not in normalized_targets:
        return _failed("target_mismatch")
    return ExactScore(passed=1, failure_code="passed")


def aggregate_exact_scores(
    scores: Sequence[int], *, planned_denominator: int, scorer_revision: str
) -> dict[str, int | float | str]:
    """Return a sanitized aggregate with absent observations scored incorrect."""

    if planned_denominator < 1 or len(scores) > planned_denominator:
        raise ValueError("observed scores exceed the fixed planned denominator")
    if any(score not in {0, 1} for score in scores):
        raise ValueError("exact scores must be binary")
    passed = sum(scores)
    return {
        "accuracy": passed / planned_denominator,
        "passed": passed,
        "incorrect": planned_denominator - passed,
        "observed": len(scores),
        "planned_denominator": planned_denominator,
        "scorer_revision": scorer_revision,
    }


def _model_output_fields(output: object) -> tuple[object, object, object, Sequence[object]]:
    error = getattr(output, "error", None)
    try:
        dynamic_output = cast(Any, output)
        stop_reason = dynamic_output.stop_reason
        message = dynamic_output.message
        prediction = getattr(message, "text", None)
        tool_calls = getattr(message, "tool_calls", None) or ()
    except (AttributeError, IndexError, TypeError):
        return None, error or "malformed_output", None, ()
    if not isinstance(tool_calls, Sequence) or isinstance(tool_calls, (str, bytes)):
        return stop_reason, error or "malformed_output", prediction, ()
    return stop_reason, error, prediction, tool_calls


def _messages(record: Mapping[str, Any], family: str) -> list[Mapping[str, Any]]:
    messages = record.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError(f"{family} record messages must be a nonempty list")
    checked: list[Mapping[str, Any]] = []
    for message in messages:
        if not isinstance(message, Mapping):
            raise ValueError(f"{family} record messages must contain objects")
        if message.get("role") not in {"system", "user", "assistant"}:
            raise ValueError(f"{family} record message role is invalid")
        if not isinstance(message.get("content"), str) or not message["content"].strip():
            raise ValueError(f"{family} record message content must be nonblank")
        checked.append(message)
    return checked


def _validate_tool_record(record: Mapping[str, Any]) -> None:
    _messages(record, "tool_json")
    tools = record.get("tools")
    expected = record.get("expected_tool_call")
    if not isinstance(tools, list) or not tools:
        raise ValueError("tool_json record tools must be a nonempty list")
    if not isinstance(expected, Mapping):
        raise ValueError("tool_json expected tool call must be an object")
    name = expected.get("name")
    arguments = expected.get("arguments")
    if not isinstance(name, str) or not name.strip() or not isinstance(arguments, Mapping):
        raise ValueError("tool_json expected tool call is invalid")
    matching_schema: Mapping[str, Any] | None = None
    for tool in tools:
        if not isinstance(tool, Mapping) or tool.get("type") != "function":
            raise ValueError("tool_json tools must be function definitions")
        function = tool.get("function")
        if not isinstance(function, Mapping):
            raise ValueError("tool_json function definition is invalid")
        if function.get("name") == name:
            parameters = function.get("parameters")
            if isinstance(parameters, Mapping):
                matching_schema = parameters
    if matching_schema is None:
        raise ValueError("tool_json expected function has no declared schema")
    try:
        Draft202012Validator.check_schema(dict(matching_schema))
        Draft202012Validator(dict(matching_schema)).validate(dict(arguments))
    except (SchemaError, ValidationError) as exc:
        raise ValueError("tool_json expected arguments do not satisfy the declared schema") from exc


def _validate_context_record(record: Mapping[str, Any]) -> None:
    _messages(record, "context")
    targets = record.get("targets")
    if not isinstance(targets, list) or not targets:
        raise ValueError("context targets must be a nonempty list")
    if any(
        not isinstance(target, str) or not normalize_context_answer(target) for target in targets
    ):
        raise ValueError("context targets must contain nonblank strings")
    normalized = [normalize_context_answer(cast(str, target)) for target in targets]
    if len(normalized) != len(set(normalized)):
        raise ValueError("context normalized targets must be unique")


def validate_private_record(family: str, record: Mapping[str, Any]) -> None:
    """Validate private scorer inputs before any model inference can begin."""

    if family == "tool_json":
        _validate_tool_record(record)
    elif family == "context":
        _validate_context_record(record)
    else:
        raise ValueError(f"unsupported exact-adapter family: {family}")


def _aggregate_evalscope(
    sample_scores: Sequence[Any], *, planned_denominator: int, scorer_revision: str
) -> list[Any]:
    from evalscope.api.metric import AggScore

    binary = [int(score.score.value.get("accuracy", 0)) for score in sample_scores]
    aggregate = aggregate_exact_scores(
        binary,
        planned_denominator=planned_denominator,
        scorer_revision=scorer_revision,
    )
    return [
        AggScore(
            metric_name="accuracy",
            aggregation="mean",
            score=aggregate["accuracy"],
            num=planned_denominator,
            metadata={key: value for key, value in aggregate.items() if key != "accuracy"},
        )
    ]


def register_adapters() -> None:
    """Register the two version-pinned local adapters with EvalScope 1.12."""

    global _REGISTERED
    if _REGISTERED:
        return

    from evalscope.api.benchmark import (
        BenchmarkMeta,
        DefaultDataAdapter,
        FunctionCallAdapter,
    )
    from evalscope.api.dataset import Sample
    from evalscope.api.messages import dict_to_chat_message
    from evalscope.api.metric import Score
    from evalscope.api.registry import register_benchmark
    from evalscope.api.tool import ToolInfo

    @register_benchmark(
        BenchmarkMeta(
            name=TOOL_BENCHMARK,
            pretty_name="Racecraft exact tool JSON",
            description="Private frozen tool-call cases scored by exact schema and arguments.",
            dataset_id="private-jsonl-required",
            subset_list=["tool_json"],
            default_subset="tool_json",
            eval_split="test",
            few_shot_num=0,
            few_shot_mode="disabled",
            metric_list=["accuracy"],
            aggregation="mean",
            primary_metric="accuracy",
            evaluation_version="v1.0",
        )
    )
    class RacecraftToolJSONAdapter(FunctionCallAdapter):  # type: ignore[misc]
        def load_from_disk(self, **kwargs: Any) -> Any:
            return super().load_from_disk(use_local_loader=True)

        def record_to_sample(self, record: dict[str, Any]) -> Any:
            validate_private_record("tool_json", record)
            expected = cast(Mapping[str, Any], record["expected_tool_call"])
            tools = cast(list[Mapping[str, Any]], record["tools"])
            messages = _messages(record, "tool_json")
            return Sample(
                input=[dict_to_chat_message(dict(message)) for message in messages],
                target="",
                tools=[ToolInfo.model_validate(tool["function"]) for tool in tools],
                metadata={
                    "expected_name": expected["name"],
                    "expected_arguments": dict(cast(Mapping[str, Any], expected["arguments"])),
                    "schema": next(
                        cast(Mapping[str, Any], tool["function"])["parameters"]
                        for tool in tools
                        if cast(Mapping[str, Any], tool["function"])["name"] == expected["name"]
                    ),
                },
            )

        def match_score(
            self,
            original_prediction: object,
            filtered_prediction: object,
            reference: object,
            task_state: Any,
        ) -> Any:
            output = task_state.output
            stop_reason, error, _prediction, tool_calls = _model_output_fields(output)
            decision = score_tool_output(
                expected_name=task_state.metadata["expected_name"],
                expected_arguments=task_state.metadata["expected_arguments"],
                schema=task_state.metadata["schema"],
                stop_reason=stop_reason,
                error=error,
                tool_calls=tool_calls,
            )
            return Score(
                value={"accuracy": decision.passed},
                main_score_name="accuracy",
                metadata={"failure_code": decision.failure_code},
            )

        def aggregate_scores(self, sample_scores: list[Any]) -> list[Any]:
            return _aggregate_evalscope(
                sample_scores,
                planned_denominator=TOOL_PLANNED_DENOMINATOR,
                scorer_revision=TOOL_SCORER_REVISION,
            )

    @register_benchmark(
        BenchmarkMeta(
            name=CONTEXT_BENCHMARK,
            pretty_name="Racecraft exact context retrieval",
            description="Private frozen context cases scored by exact normalized targets.",
            dataset_id="private-jsonl-required",
            subset_list=["context"],
            default_subset="context",
            eval_split="test",
            few_shot_num=0,
            few_shot_mode="disabled",
            metric_list=["accuracy"],
            aggregation="mean",
            primary_metric="accuracy",
            evaluation_version="v1.0",
        )
    )
    class RacecraftContextAdapter(DefaultDataAdapter):  # type: ignore[misc]
        def load_from_disk(self, **kwargs: Any) -> Any:
            return super().load_from_disk(use_local_loader=True)

        def record_to_sample(self, record: dict[str, Any]) -> Any:
            validate_private_record("context", record)
            targets = cast(list[str], record["targets"])
            messages = _messages(record, "context")
            return Sample(
                input=[dict_to_chat_message(dict(message)) for message in messages],
                target=targets,
                metadata={
                    "normalized_targets": [normalize_context_answer(target) for target in targets]
                },
            )

        def match_score(
            self,
            original_prediction: object,
            filtered_prediction: object,
            reference: object,
            task_state: Any,
        ) -> Any:
            output = task_state.output
            stop_reason, error, prediction, _tool_calls = _model_output_fields(output)
            decision = score_context_output(
                targets=task_state.metadata["normalized_targets"],
                prediction=prediction,
                stop_reason=stop_reason,
                error=error,
            )
            return Score(
                value={"accuracy": decision.passed},
                main_score_name="accuracy",
                metadata={"failure_code": decision.failure_code},
            )

        def aggregate_scores(self, sample_scores: list[Any]) -> list[Any]:
            return _aggregate_evalscope(
                sample_scores,
                planned_denominator=CONTEXT_PLANNED_DENOMINATOR,
                scorer_revision=CONTEXT_SCORER_REVISION,
            )

    _REGISTERED = True


def main() -> None:
    """Register exact adapters, then delegate argument parsing to EvalScope."""

    register_adapters()
    from evalscope.cli.cli import run_cmd

    run_cmd()


if __name__ == "__main__":
    main()
