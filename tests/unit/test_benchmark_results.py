from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError

from local_evals.benchmark_results import (
    BenchmarkCounts,
    BenchmarkEvaluator,
    BenchmarkIdentity,
    BenchmarkProvenance,
    BenchmarkResult,
    BenchmarkStatus,
    EvaluatorClass,
    MetricUnit,
    ReferenceObservation,
    RuntimeMeasurements,
    TaskOutcomes,
    adapt_gpqa_public_result,
)

ROOT = Path(__file__).resolve().parents[2]
GPQA_RESULT = ROOT / "results/public/gpqa-diamond-splash-local-2026-09-20.json"
GPQA_FILE_SHA256 = "99ce46fa6a90fb6f3d5df051d4dd39c40e2801cb1e6ad6f1272b0154057d9082"


def _base_result(**updates: object) -> BenchmarkResult:
    values: dict[str, object] = {
        "result_id": "synthetic-result",
        "identity": BenchmarkIdentity(
            name="Synthetic Benchmark",
            variant="held-out",
            adapter="synthetic_adapter",
            dataset_provider="synthetic-provider",
            dataset_id="synthetic/dataset",
            dataset_revision=None,
            evaluation_version="v1",
            split="test",
            subset="default",
        ),
        "status": BenchmarkStatus.COMPLETE,
        "counts": BenchmarkCounts(requested=2, succeeded=2, errored=0),
        "metric_name": "accuracy",
        "metric_unit": MetricUnit.PROPORTION,
        "score": 0.0,
        "evaluator": BenchmarkEvaluator(
            name="Independent Evaluator",
            version="1.0",
            developer="Model Developer",
            model_label="Synthetic Model",
            evidence_class=EvaluatorClass.INDEPENDENT,
        ),
        "provenance": BenchmarkProvenance(
            source="synthetic fixture",
            artifacts={"report_sha256": "a" * 64},
        ),
        "limitations": ("Synthetic evidence only.",),
        "reference_observations": (),
    }
    values.update(updates)
    return BenchmarkResult.model_validate(values)


def test_nullable_score_preserves_unknown_as_distinct_from_zero() -> None:
    unknown = _base_result(score=None)
    measured_zero = _base_result(score=0.0)

    assert unknown.score is None
    assert measured_zero.score == 0.0
    assert unknown.model_dump(mode="json")["score"] is None
    assert measured_zero.model_dump(mode="json")["score"] == 0.0
    assert unknown.fingerprint_sha256() != measured_zero.fingerprint_sha256()


def test_task_outcomes_keep_failure_classes_and_exact_attempted_counts() -> None:
    outcomes = TaskOutcomes(resolved=1, unresolved=1, model_failure=1, infrastructure_error=1)

    assert outcomes.completed == 2
    assert outcomes.errors == 2
    assert outcomes.total == 4

    result = _base_result(
        status=BenchmarkStatus.PARTIAL,
        counts=BenchmarkCounts(requested=4, succeeded=2, errored=2),
        score=None,
        task_outcomes=outcomes,
    )
    assert result.task_outcomes == outcomes

    with pytest.raises(ValidationError, match="reconcile"):
        _base_result(
            status=BenchmarkStatus.PARTIAL,
            counts=BenchmarkCounts(requested=4, succeeded=2, errored=2),
            score=None,
            task_outcomes=TaskOutcomes(
                resolved=1, unresolved=1, model_failure=1, infrastructure_error=0
            ),
        )


def test_swebench_complete_result_keeps_model_failures_in_the_denominator() -> None:
    result = _base_result(
        identity=BenchmarkIdentity(
            name="SWE-bench Verified",
            variant="verified-500",
            adapter="mini-swe-agent-bash-docker",
            dataset_provider="princeton-nlp",
            dataset_id="SWE-bench_Verified",
            dataset_revision="c" * 64,
            evaluation_version="5.0.2",
            split="test",
            subset="verified",
        ),
        status=BenchmarkStatus.COMPLETE,
        counts=BenchmarkCounts(requested=500, succeeded=499, errored=1),
        score=498 / 500,
        task_outcomes=TaskOutcomes(
            resolved=498, unresolved=1, model_failure=1, infrastructure_error=0
        ),
    )

    assert result.score == 498 / 500
    assert result.task_outcomes is not None
    assert result.task_outcomes.total == 500

    with_infrastructure_error = _base_result(
        identity=result.identity,
        status=BenchmarkStatus.COMPLETE,
        counts=BenchmarkCounts(requested=500, succeeded=499, errored=1),
        score=498 / 500,
        task_outcomes=TaskOutcomes(
            resolved=498, unresolved=1, model_failure=0, infrastructure_error=1
        ),
    )
    # Upstream convention: an infrastructure error stays in the 500-task denominator.
    assert with_infrastructure_error.score == 498 / 500


def test_unknown_runtime_measurements_remain_null_not_zero() -> None:
    result = _base_result(runtime=RuntimeMeasurements())

    assert result.runtime is not None
    assert result.runtime.model_dump(mode="json") == {
        "duration_seconds": None,
        "average_latency_seconds": None,
        "output_tokens_per_second": None,
        "total_input_tokens": None,
        "total_output_tokens": None,
    }


def test_result_envelope_is_versioned_strict_and_immutable() -> None:
    result = _base_result()

    with pytest.raises(ValidationError, match="frozen"):
        result.score = 0.5  # type: ignore[misc]
    with pytest.raises(ValidationError, match="schema_version"):
        _base_result(schema_version=2)
    with pytest.raises(ValidationError, match="extra"):
        _base_result(unreviewed_field=True)


@pytest.mark.parametrize("status", [BenchmarkStatus.QUALIFICATION, BenchmarkStatus.INVALID])
def test_contract_supports_non_capability_lifecycle_statuses(status: BenchmarkStatus) -> None:
    result = _base_result(
        status=status,
        counts=BenchmarkCounts(requested=2, succeeded=0, errored=0),
        score=None,
    )

    assert result.status is status
    assert result.score is None
    with pytest.raises(ValidationError, match=f"{status.value} results cannot contain a score"):
        _base_result(status=status, score=0.0)


def test_pending_status_requires_no_attempted_cases() -> None:
    pending = _base_result(
        status=BenchmarkStatus.PENDING,
        counts=BenchmarkCounts(requested=2, succeeded=0, errored=0),
        score=None,
    )

    assert pending.status is BenchmarkStatus.PENDING
    with pytest.raises(ValidationError, match="pending"):
        _base_result(
            status=BenchmarkStatus.PENDING,
            counts=BenchmarkCounts(requested=2, succeeded=1, errored=0),
            score=None,
        )


def test_contract_rejects_identity_and_status_count_mismatches() -> None:
    with pytest.raises(ValidationError, match="variant"):
        BenchmarkIdentity.model_validate({**_base_result().identity.model_dump(), "variant": " "})

    with pytest.raises(ValidationError, match="complete"):
        _base_result(counts=BenchmarkCounts(requested=2, succeeded=1, errored=1))


def test_evaluator_and_developer_are_separate_even_when_names_match() -> None:
    evaluator = BenchmarkEvaluator(
        name="Provider Organization",
        version=None,
        developer="Provider Organization",
        model_label="Provider Model",
        evidence_class=EvaluatorClass.PROVIDER_REPORTED,
    )

    assert evaluator.name == evaluator.developer
    assert evaluator.evidence_class is EvaluatorClass.PROVIDER_REPORTED


def test_contract_validates_provenance_limitations_and_reference_observations() -> None:
    observation = ReferenceObservation(
        reference_id="historical-reference",
        source_record_id="validated-historical-reference",
        source_record_sha256="b" * 64,
        model_label="Historical Model",
        denominator=198,
        score=None,
        metric_name="accuracy",
        metric_unit=MetricUnit.PERCENT,
        source_url="https://example.invalid/reference",
        limitations=("The published score is unknown.",),
    )
    result = _base_result(reference_observations=(observation,))

    assert result.reference_observations[0].score is None

    with pytest.raises(ValidationError, match="SHA-256"):
        BenchmarkProvenance(source="fixture", artifacts={"report_sha256": "not-a-hash"})
    with pytest.raises(ValidationError, match="limitations"):
        _base_result(limitations=("",))
    with pytest.raises(ValidationError, match="https"):
        ReferenceObservation.model_validate(
            {
                **observation.model_dump(),
                "source_url": "http://example.invalid/reference",
            }
        )


def test_gpqa_adapter_preserves_source_artifact_and_exact_reported_values() -> None:
    source_bytes = GPQA_RESULT.read_bytes()
    payload = json.loads(source_bytes)
    original_payload = json.loads(source_bytes)

    result = adapt_gpqa_public_result(payload)

    assert GPQA_RESULT.read_bytes() == source_bytes
    assert payload == original_payload
    assert sha256(source_bytes).hexdigest() == GPQA_FILE_SHA256
    assert result.identity.model_dump(mode="json") == {
        "name": "GPQA Diamond",
        "variant": "diamond",
        "adapter": "gpqa_diamond",
        "dataset_provider": "modelscope",
        "dataset_id": "AI-ModelScope/gpqa_diamond",
        "dataset_revision": None,
        "evaluation_version": "v1.0",
        "split": "train",
        "subset": "default",
    }
    assert result.status is BenchmarkStatus.COMPLETE
    assert result.counts == BenchmarkCounts(requested=198, succeeded=198, errored=0)
    assert result.metric_name == "accuracy"
    assert result.metric_unit is MetricUnit.PROPORTION
    assert result.score == 0.5455
    assert result.evaluator == BenchmarkEvaluator(
        name="EvalScope",
        version="1.12.0",
        developer=None,
        model_label="Splash / Qwen3.8",
        evidence_class=EvaluatorClass.MEASURED_HERE,
    )
    assert result.provenance.artifacts == payload["provenance"]
    assert list(result.limitations) == payload["limitations"]
    assert result.reference_observations == ()


def test_gpqa_adapter_rejects_aliases_and_inconsistent_counts() -> None:
    payload = json.loads(GPQA_RESULT.read_text(encoding="utf-8"))
    payload["benchmark"]["adapter"] = "gpqa"
    with pytest.raises(ValueError, match="adapter"):
        adapt_gpqa_public_result(payload)

    payload = json.loads(GPQA_RESULT.read_text(encoding="utf-8"))
    payload["benchmark"]["succeeded_cases"] = 197
    with pytest.raises(ValueError, match="counts"):
        adapt_gpqa_public_result(payload)
