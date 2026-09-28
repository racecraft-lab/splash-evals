"""Strict shared benchmark-result records and compatibility adapters."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from enum import StrEnum
from hashlib import sha256
from typing import Any, Literal, Self
from urllib.parse import urlparse

from pydantic import Field, TypeAdapter, ValidationError, field_validator, model_validator

from .models import FrozenModel

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_STRICT_INTEGER = TypeAdapter(int, config={"strict": True})
_STRICT_STRING = TypeAdapter(str, config={"strict": True})


class BenchmarkAdapterError(ValueError):
    """A compatibility source cannot be represented without guessing."""


class BenchmarkStatus(StrEnum):
    PENDING = "pending"
    QUALIFICATION = "qualification"
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    INVALID = "invalid"


class MetricUnit(StrEnum):
    PROPORTION = "proportion"
    PERCENT = "percent"


class EvaluatorClass(StrEnum):
    PROVIDER_REPORTED = "provider_reported"
    INDEPENDENT = "independent"
    CROSS_PROVIDER = "cross_provider"
    MEASURED_HERE = "measured_here"


def _require_nonblank(value: str, label: str) -> str:
    if not value.strip():
        raise ValueError(f"{label} must be nonblank")
    return value


def _validate_score(value: float | None, unit: MetricUnit) -> None:
    if value is None:
        return
    upper = 1.0 if unit is MetricUnit.PROPORTION else 100.0
    if not 0.0 <= value <= upper:
        raise ValueError(f"score must be between 0 and {upper:g} for {unit.value}")


class BenchmarkIdentity(FrozenModel):
    name: str
    variant: str
    adapter: str
    dataset_provider: str
    dataset_id: str
    dataset_revision: str | None
    evaluation_version: str
    split: str
    subset: str

    @field_validator(
        "name",
        "variant",
        "adapter",
        "dataset_provider",
        "dataset_id",
        "evaluation_version",
        "split",
        "subset",
    )
    @classmethod
    def required_identity_text(cls, value: str, info: Any) -> str:
        return _require_nonblank(value, str(info.field_name))

    @field_validator("dataset_revision")
    @classmethod
    def optional_revision_is_nonblank(cls, value: str | None) -> str | None:
        if value is not None:
            return _require_nonblank(value, "dataset_revision")
        return value


class BenchmarkCounts(FrozenModel):
    requested: int = Field(ge=1, strict=True)
    succeeded: int = Field(ge=0, strict=True)
    errored: int = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def attempted_cases_do_not_exceed_requested_cases(self) -> Self:
        if self.succeeded + self.errored > self.requested:
            raise ValueError("counts cannot exceed requested cases")
        return self


class BenchmarkEvaluator(FrozenModel):
    name: str
    version: str | None
    developer: str | None
    model_label: str
    evidence_class: EvaluatorClass

    @field_validator("name", "model_label")
    @classmethod
    def required_evaluator_text(cls, value: str, info: Any) -> str:
        return _require_nonblank(value, str(info.field_name))

    @field_validator("version")
    @classmethod
    def optional_version_is_nonblank(cls, value: str | None) -> str | None:
        if value is not None:
            return _require_nonblank(value, "version")
        return value

    @field_validator("developer")
    @classmethod
    def optional_developer_is_nonblank(cls, value: str | None) -> str | None:
        if value is not None:
            return _require_nonblank(value, "developer")
        return value


class BenchmarkProvenance(FrozenModel):
    source: str
    artifacts: dict[str, str]

    @field_validator("source")
    @classmethod
    def source_is_nonblank(cls, value: str) -> str:
        return _require_nonblank(value, "source")

    @field_validator("artifacts")
    @classmethod
    def artifact_hashes_are_explicit_sha256(cls, value: dict[str, str]) -> dict[str, str]:
        if not value:
            raise ValueError("provenance artifacts must not be empty")
        for name, digest in value.items():
            _require_nonblank(name, "provenance artifact name")
            if not _SHA256.fullmatch(digest):
                raise ValueError("provenance artifact values must be lowercase SHA-256 digests")
        return value


class ReferenceObservation(FrozenModel):
    reference_id: str
    source_record_id: str
    source_record_sha256: str
    model_label: str
    denominator: int | None = Field(ge=1, strict=True)
    score: float | None
    metric_name: str
    metric_unit: MetricUnit
    source_url: str
    evaluator: str | None = None
    developer: str | None = None
    evaluator_class: EvaluatorClass | None = None
    source_locator: str | None = None
    limitations: tuple[str, ...] = ()

    @field_validator("reference_id", "source_record_id", "model_label", "metric_name")
    @classmethod
    def required_reference_text(cls, value: str, info: Any) -> str:
        return _require_nonblank(value, str(info.field_name))

    @field_validator("source_url")
    @classmethod
    def source_is_public_https(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("reference source_url must be an absolute public https URL")
        return value

    @field_validator("source_record_sha256")
    @classmethod
    def source_record_fingerprint_is_sha256(cls, value: str) -> str:
        if not _SHA256.fullmatch(value):
            raise ValueError("source_record_sha256 must be a lowercase SHA-256 digest")
        return value

    @field_validator("limitations")
    @classmethod
    def reference_limitations_are_nonblank(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("reference limitations must be nonblank")
        return value

    @model_validator(mode="after")
    def score_matches_unit(self) -> Self:
        _validate_score(self.score, self.metric_unit)
        return self


def _terminal_status_error(status: BenchmarkStatus, counts: BenchmarkCounts) -> str | None:
    if status is BenchmarkStatus.COMPLETE and (
        counts.succeeded != counts.requested or counts.errored != 0
    ):
        return "complete results require every requested case to succeed"
    if status is BenchmarkStatus.FAILED and (
        counts.succeeded != 0 or counts.errored != counts.requested
    ):
        return "failed results require every requested case to error"
    attempted = counts.succeeded + counts.errored
    if status is BenchmarkStatus.PARTIAL and (
        attempted == 0
        or (counts.succeeded == counts.requested and counts.errored == 0)
        or (counts.succeeded == 0 and counts.errored == counts.requested)
    ):
        return "partial results require an incomplete or mixed outcome"
    return None


def _lifecycle_status_error(
    status: BenchmarkStatus, counts: BenchmarkCounts, score: float | None
) -> str | None:
    if status is BenchmarkStatus.PENDING and counts.succeeded + counts.errored != 0:
        return "pending results cannot contain attempted cases"
    if (
        status
        in {
            BenchmarkStatus.PENDING,
            BenchmarkStatus.QUALIFICATION,
            BenchmarkStatus.INVALID,
            BenchmarkStatus.PARTIAL,
            BenchmarkStatus.FAILED,
        }
        and score is not None
    ):
        return f"{status.value} results cannot contain a score"
    return None


class TestedConfiguration(FrozenModel):
    """Published settings only; missing metadata remains unknown."""

    harness: str | None = None
    harness_version: str | None = None
    reasoning_effort: str | None = None
    temperature: float | None = Field(default=None, ge=0, allow_inf_nan=False, strict=True)
    top_p: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False, strict=True)
    max_output_tokens: int | None = Field(default=None, ge=1, strict=True)
    attempts_per_task: int | None = Field(default=None, ge=1, strict=True)


class RuntimeMeasurements(FrozenModel):
    """Aggregate runtime measurements with explicit units and no inferred defaults."""

    duration_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False, strict=True)
    average_latency_seconds: float | None = Field(
        default=None, ge=0, allow_inf_nan=False, strict=True
    )
    output_tokens_per_second: float | None = Field(
        default=None, ge=0, allow_inf_nan=False, strict=True
    )
    total_input_tokens: int | None = Field(default=None, ge=0, strict=True)
    total_output_tokens: int | None = Field(default=None, ge=0, strict=True)


class TaskOutcomes(FrozenModel):
    """Mutually exclusive task dispositions; never silently omit failed tasks."""

    resolved: int = Field(ge=0, strict=True)
    unresolved: int = Field(ge=0, strict=True)
    model_failure: int = Field(ge=0, strict=True)
    infrastructure_error: int = Field(ge=0, strict=True)

    @property
    def completed(self) -> int:
        return self.resolved + self.unresolved

    @property
    def errors(self) -> int:
        return self.model_failure + self.infrastructure_error

    @property
    def total(self) -> int:
        return self.completed + self.errors


class BenchmarkResult(FrozenModel):
    schema_version: Literal[1] = 1
    result_id: str
    identity: BenchmarkIdentity
    status: BenchmarkStatus
    counts: BenchmarkCounts
    metric_name: str
    metric_unit: MetricUnit
    score: float | None
    evaluator: BenchmarkEvaluator
    provenance: BenchmarkProvenance
    limitations: tuple[str, ...]
    reference_observations: tuple[ReferenceObservation, ...] = ()
    tested_configuration: TestedConfiguration | None = None
    runtime: RuntimeMeasurements | None = None
    task_outcomes: TaskOutcomes | None = None

    @field_validator("result_id", "metric_name")
    @classmethod
    def required_result_text(cls, value: str, info: Any) -> str:
        return _require_nonblank(value, str(info.field_name))

    @field_validator("limitations")
    @classmethod
    def limitations_are_nonblank_and_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("limitations must be nonblank")
        if len(set(value)) != len(value):
            raise ValueError("limitations must not contain duplicates")
        return value

    @model_validator(mode="after")
    def result_fields_are_consistent(self) -> Self:
        swebench_complete = (
            self.identity.name == "SWE-bench Verified" and self.status is BenchmarkStatus.COMPLETE
        )
        status_error = (
            None if swebench_complete else _terminal_status_error(self.status, self.counts)
        ) or _lifecycle_status_error(self.status, self.counts, self.score)
        if status_error is not None:
            raise ValueError(status_error)
        _validate_score(self.score, self.metric_unit)
        if self.task_outcomes is not None:
            outcomes = self.task_outcomes
            if (
                outcomes.completed != self.counts.succeeded
                or outcomes.errors != self.counts.errored
            ):
                raise ValueError("task outcomes must reconcile with completion and error counts")
        if self.identity.name == "SWE-bench Verified" and self.status is BenchmarkStatus.COMPLETE:
            if (
                self.identity.variant != "verified-500"
                or self.identity.subset != "verified"
                or self.counts.requested != 500
            ):
                raise ValueError("complete SWE-bench Verified requires the verified-500 variant")
            if self.task_outcomes is None:
                raise ValueError("complete SWE-bench Verified requires task outcomes")
            if self.task_outcomes.total != 500:
                raise ValueError("complete SWE-bench Verified requires exact task outcomes")
            expected_score = self.task_outcomes.resolved / 500
            if self.metric_unit is MetricUnit.PERCENT:
                expected_score *= 100
            if self.score != expected_score:
                raise ValueError("SWE-bench score must equal resolved tasks divided by 500")
        reference_ids = [item.reference_id for item in self.reference_observations]
        if len(set(reference_ids)) != len(reference_ids):
            raise ValueError("reference observations must have unique reference_id values")
        return self

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )

    def fingerprint_sha256(self) -> str:
        return sha256(self.canonical_json().encode("utf-8")).hexdigest()


def _mapping_field(source: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = source.get(name)
    if not isinstance(value, Mapping):
        raise BenchmarkAdapterError(f"GPQA artifact {name} must be an object")
    return value


def _exact_field(source: Mapping[str, Any], name: str, expected: Any) -> Any:
    value = source.get(name)
    if value != expected:
        raise BenchmarkAdapterError(f"GPQA artifact {name} does not match the reviewed contract")
    return value


def _string_field(source: Mapping[str, Any], name: str) -> str:
    try:
        value = _STRICT_STRING.validate_python(source.get(name))
    except ValidationError as exc:
        raise BenchmarkAdapterError(f"GPQA artifact {name} must be text") from exc
    if not value.strip():
        raise BenchmarkAdapterError(f"GPQA artifact {name} must be nonblank text")
    return value


def _integer_field(source: Mapping[str, Any], name: str) -> int:
    try:
        return _STRICT_INTEGER.validate_python(source.get(name))
    except ValidationError as exc:
        raise BenchmarkAdapterError(f"GPQA artifact {name} must be an integer") from exc


def _gpqa_status(counts: BenchmarkCounts) -> BenchmarkStatus:
    if counts.succeeded == counts.requested:
        return BenchmarkStatus.COMPLETE
    if counts.errored == counts.requested:
        return BenchmarkStatus.FAILED
    return BenchmarkStatus.PARTIAL


def adapt_gpqa_public_result(
    payload: Mapping[str, Any],
    *,
    reference_observations: Sequence[ReferenceObservation] = (),
) -> BenchmarkResult:
    """Normalize the reviewed GPQA public artifact without modifying it or guessing gaps."""

    _exact_field(payload, "schema_version", 1)
    _exact_field(payload, "purpose", "public_capability_result")
    benchmark = _mapping_field(payload, "benchmark")
    evaluation = _mapping_field(payload, "evaluation")
    model = _mapping_field(payload, "model")
    provenance = _mapping_field(payload, "provenance")

    _exact_field(benchmark, "name", "GPQA Diamond")
    _exact_field(benchmark, "adapter", "gpqa_diamond")
    _exact_field(benchmark, "dataset_id", "AI-ModelScope/gpqa_diamond")
    _exact_field(benchmark, "split", "train")
    _exact_field(benchmark, "subset", "default")
    _exact_field(benchmark, "metric", "accuracy")
    counts = BenchmarkCounts(
        requested=_integer_field(benchmark, "requested_cases"),
        succeeded=_integer_field(benchmark, "succeeded_cases"),
        errored=_integer_field(benchmark, "errored_cases"),
    )
    if counts != BenchmarkCounts(requested=198, succeeded=198, errored=0):
        raise BenchmarkAdapterError("GPQA artifact counts do not match the reviewed result")
    score = benchmark.get("score")
    if score is not None and (isinstance(score, bool) or not isinstance(score, (int, float))):
        raise BenchmarkAdapterError("GPQA artifact score must be numeric or null")
    limitations = payload.get("limitations")
    if not isinstance(limitations, list) or not all(isinstance(item, str) for item in limitations):
        raise BenchmarkAdapterError("GPQA artifact limitations must be a list of text")
    artifacts = dict(provenance)
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in artifacts.items()):
        raise BenchmarkAdapterError("GPQA artifact provenance must contain named SHA-256 values")

    return BenchmarkResult(
        result_id=_string_field(payload, "result_id"),
        identity=BenchmarkIdentity(
            name=_string_field(benchmark, "name"),
            variant="diamond",
            adapter=_string_field(benchmark, "adapter"),
            dataset_provider=_string_field(benchmark, "dataset_hub"),
            dataset_id=_string_field(benchmark, "dataset_id"),
            dataset_revision=benchmark.get("dataset_revision"),
            evaluation_version=_string_field(benchmark, "evaluation_version"),
            split=_string_field(benchmark, "split"),
            subset=_string_field(benchmark, "subset"),
        ),
        status=_gpqa_status(counts),
        counts=counts,
        metric_name=_string_field(benchmark, "metric"),
        metric_unit=MetricUnit.PROPORTION,
        score=float(score) if score is not None else None,
        evaluator=BenchmarkEvaluator(
            name=_string_field(evaluation, "framework"),
            version=_string_field(evaluation, "framework_version"),
            developer=None,
            model_label=_string_field(model, "public_label"),
            evidence_class=EvaluatorClass.MEASURED_HERE,
        ),
        provenance=BenchmarkProvenance(
            source="reviewed GPQA public capability artifact",
            artifacts=artifacts,
        ),
        limitations=tuple(limitations),
        reference_observations=tuple(reference_observations),
        tested_configuration=TestedConfiguration(
            harness=_string_field(evaluation, "framework"),
            harness_version=_string_field(evaluation, "framework_version"),
            reasoning_effort=evaluation.get("reasoning_effort"),
            max_output_tokens=evaluation.get("max_output_tokens"),
        ),
        runtime=_gpqa_runtime(payload),
    )


def _gpqa_runtime(payload: Mapping[str, Any]) -> RuntimeMeasurements:
    runtime = _mapping_field(payload, "runtime")
    performance = _mapping_field(payload, "performance")
    latency = _mapping_field(performance, "latency_seconds")
    return RuntimeMeasurements(
        duration_seconds=runtime.get("duration_seconds"),
        average_latency_seconds=latency.get("average"),
        output_tokens_per_second=performance.get("average_output_tokens_per_second"),
        total_input_tokens=performance.get("total_input_tokens"),
        total_output_tokens=performance.get("total_output_tokens"),
    )
