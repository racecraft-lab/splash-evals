from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml

import local_evals.frontier as frontier
from local_evals.frontier import (
    ComparisonRefused,
    FrontierValidationError,
    assess_comparability,
    compare_frontier,
    validate_catalog,
    validate_reference_record,
)


def _reference_record(
    *, score: float | None = 72.5, comparability: str = "unknown"
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "reference_id": "synthetic-provider-model-benchmark-condition",
        "provider": "OpenAI",
        "model_display_name": "Synthetic Frontier Model",
        "model_snapshot_id": "synthetic-model-2025-01-01",
        "measurement_date": "2025-01-15",
        "source_url": "https://example.invalid/synthetic-primary-source",
        "source_locator": "synthetic table 1",
        "source_revision_or_content_hash": "synthetic-source-revision",
        "evidence_class": "published_historical_reference",
        "benchmark": {
            "name": "Synthetic Benchmark",
            "version": "synthetic-v1",
            "split": "test",
            "dataset_revision": "synthetic-dataset-v1",
            "sample_count": 100,
            "sample_id_manifest": "synthetic-sample-manifest-v1",
            "metric_name": "accuracy",
            "metric_unit": "percent",
            "higher_is_better": True,
        },
        "protocol": {
            "few_shot": 0,
            "prompts_or_template_revision": "synthetic-template-v1",
            "reasoning_mode": "high",
            "output_budget": 512,
            "attempts_per_task": 1,
            "aggregation": "mean",
            "tool_access": False,
            "agent_scaffold_revision": "none",
            "scorer_revision": "synthetic-scorer-v1",
            "answer_extraction": "exact",
        },
        "reported_score": score,
        "comparability": comparability,
        "comparability_notes": ["Synthetic record used only for unit testing."],
        "verification_status": "source_verified",
    }


def _matching_local_protocol() -> dict[str, Any]:
    return {
        "benchmark_name": "Synthetic Benchmark",
        "benchmark_version": "synthetic-v1",
        "split": "test",
        "dataset_revision": "synthetic-dataset-v1",
        "sample_id_manifest": "synthetic-sample-manifest-v1",
        "metric_name": "accuracy",
        "metric_unit": "proportion",
        "higher_is_better": True,
        "sample_count": 100,
        "few_shot": 0,
        "prompts_or_template_revision": "synthetic-template-v1",
        "reasoning_mode": "high",
        "output_budget": 512,
        "attempts_per_task": 1,
        "aggregation": "mean",
        "tool_access": False,
        "agent_scaffold_revision": "none",
        "scorer_revision": "synthetic-scorer-v1",
        "answer_extraction": "exact",
    }


def _qualified_manifest(*, score: float = 0.8) -> dict[str, Any]:
    return {
        "suite": "pilot",
        "held_out": True,
        "evidence_class": "local_measurement",
        "model_is_splash": True,
        "locality_evidence": {
            "status": "verified_local",
            "endpoint_loopback": True,
            "local_instance_evidence": True,
        },
        "model_instance_evidence": {
            "selection": "exact_loaded_record",
            "splash_attribution": "confirmed",
            "model_key": "publisher/splash",
            "selected_variant": "Q4_K_M",
            "native_identity": {
                "model_key": "publisher/splash",
                "publisher": "publisher",
                "architecture": "qwen",
                "format": "gguf",
                "quantization": "Q4_K_M",
                "selected_variant": "Q4_K_M",
                "loaded_instance_id_match": True,
            },
        },
        "served_model_evidence": {
            "status": "verified",
            "requested_instance_id_sha256": "sha256:local-instance",
            "response_instance_id_sha256": "sha256:local-instance",
            "match": True,
        },
        "reasoning_evidence": {
            "requested": "high",
            "transmitted": "high",
            "supported_options": ["off", "low", "medium", "high"],
            "default": "medium",
            "effective_status": "accepted_by_runtime",
        },
        "runtime_evidence": {
            "transport": "lmstudio_native_v1",
            "endpoint": "/api/v1/chat",
            "cli_version": "1.0.0",
            "app_version": "1.0.0",
            "engine": "llama.cpp",
            "engine_version": "b1",
        },
        "historical_protocol": _matching_local_protocol(),
        "status": "completed",
        "aggregate": {
            "planned": 100,
            "attempted": 100,
            "completed": 100,
            "scorable": 100,
            "censored": 0,
            "unattempted": 0,
            "capability_conditional_on_valid_execution": {
                "total": 100,
                "rate": score,
            },
            "end_to_end_deployment_success": {
                "total": 100,
                "rate": score,
            },
        },
        "primary_objective_status_if_run": "pilot_only",
    }


def _write_comparable_catalog(
    catalog: Path, *, score: float = 72.5, metric_unit: str = "percent"
) -> None:
    catalog.mkdir()
    for index, provider in enumerate(("OpenAI", "Anthropic", "Google"), start=1):
        record = _reference_record()
        record["reference_id"] = f"synthetic-record-{index}"
        record["provider"] = provider
        record["model_display_name"] = f"Synthetic Frontier Model {index}"
        record["model_snapshot_id"] = f"synthetic-model-2025-01-{index:02d}"
        record["reported_score"] = score
        record["benchmark"]["metric_unit"] = metric_unit
        (catalog / f"record-{index}.yaml").write_text(yaml.safe_dump(record), encoding="utf-8")


def test_null_score_is_valid_stub_but_never_renderable_as_zero() -> None:
    result = validate_reference_record(_reference_record(score=None))

    assert result["valid"] is True
    assert result["record_kind"] == "stub"
    assert any("must not render as zero" in warning for warning in result["warnings"])


def test_null_score_cannot_claim_matched_comparability() -> None:
    result = validate_reference_record(_reference_record(score=None, comparability="matched"))

    assert result["valid"] is False
    assert any("null-score stub" in error for error in result["errors"])


def test_percent_score_scale_is_validated() -> None:
    result = validate_reference_record(_reference_record(score=125.0))

    assert result["valid"] is False
    assert any("[0, 100]" in error for error in result["errors"])


def test_numeric_score_requires_primary_source_locator_and_revision() -> None:
    record = _reference_record()
    record["source_locator"] = None
    record["source_revision_or_content_hash"] = None

    result = validate_reference_record(record)

    assert result["valid"] is False
    assert any("source_url and source_locator" in error for error in result["errors"])
    assert any("source revision" in error for error in result["errors"])


@pytest.mark.parametrize("provider", ["OpenAI", "Anthropic", "Google"])
def test_provider_allowlist_accepts_supported_providers(provider: str) -> None:
    record = _reference_record()
    record["provider"] = provider

    assert validate_reference_record(record)["valid"] is True


def test_provider_allowlist_rejects_every_other_value() -> None:
    record = _reference_record()
    record["provider"] = "Synthetic Provider"

    result = validate_reference_record(record)

    assert result["valid"] is False
    assert "provider must be one of: Anthropic, Google, OpenAI" in result["errors"]


def _write_catalog_records(catalog: Path, *, version: Any, dataset_revision: Any) -> None:
    catalog.mkdir()
    for index, provider in enumerate(("OpenAI", "Anthropic", "Anthropic"), start=1):
        record = _reference_record()
        record["reference_id"] = f"synthetic-record-{index}"
        record["provider"] = provider
        record["model_display_name"] = f"Synthetic Frontier Model {index}"
        record["model_snapshot_id"] = f"synthetic-model-2025-01-{index:02d}"
        record["benchmark"]["version"] = version
        record["benchmark"]["dataset_revision"] = dataset_revision
        (catalog / f"record-{index}.yaml").write_text(yaml.safe_dump(record), encoding="utf-8")


def test_catalog_gate_rejects_unknown_version_or_dataset_revision(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog"
    _write_catalog_records(catalog, version=None, dataset_revision=None)

    result = validate_catalog(catalog, root=tmp_path)

    assert result["schema_valid"] is True
    assert result["coverage_gate_met"] is False
    assert result["valid"] is False
    assert len(result["coverage_intersections"]) == 1
    row = result["coverage_intersections"][0]
    assert row["dated_model_count"] == 3
    assert row["provider_count"] == 2
    assert row["unknown_required_fields"] == [
        "benchmark.version",
        "benchmark.dataset_revision",
    ]
    assert row["meets_intersection_gate"] is False


def test_catalog_gate_accepts_only_explicit_version_and_dataset_revision(
    tmp_path: Path,
) -> None:
    catalog = tmp_path / "catalog"
    _write_catalog_records(
        catalog,
        version="synthetic-v1",
        dataset_revision="synthetic-dataset-v1",
    )

    result = validate_catalog(catalog, root=tmp_path)

    assert result["schema_valid"] is True
    assert result["coverage_gate_met"] is True
    assert result["valid"] is True
    row = result["coverage_intersections"][0]
    assert row["unknown_required_fields"] == []
    assert row["meets_intersection_gate"] is True


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("evidence_class", "archived_response_reanalysis", "reference.evidence_class"),
        ("verification_status", "verified", "reference.verification_status"),
        ("model_snapshot_id", None, "reference.model_snapshot_id"),
        ("measurement_date", None, "reference.measurement_date"),
    ],
)
def test_coverage_filters_and_describes_direct_reference_hard_gates(
    tmp_path: Path, field: str, value: Any, reason: str
) -> None:
    catalog = tmp_path / "catalog"
    _write_comparable_catalog(catalog)
    record_path = catalog / "record-1.yaml"
    record = yaml.safe_load(record_path.read_text(encoding="utf-8"))
    record[field] = value
    record_path.write_text(yaml.safe_dump(record), encoding="utf-8")

    result = validate_catalog(catalog, root=tmp_path)

    assert result["schema_valid"] is True
    assert result["coverage_gate_met"] is False
    assert result["direct_comparison_candidate_count"] == 2
    assert result["direct_comparison_excluded_count"] == 1
    assert result["direct_comparison_exclusions"][0]["reference_id"] == "synthetic-record-1"
    assert reason in result["direct_comparison_exclusions"][0]["reasons"]


def test_direct_difference_is_eligible_only_for_exact_protocol_match() -> None:
    result = assess_comparability(_matching_local_protocol(), _reference_record())

    assert result["eligible_for_direct_difference"] is True
    assert result["classification"] == "matched"
    assert result["missing_protocol_fields"] == []
    assert result["mismatched_protocol_fields"] == []


def test_compatible_metric_units_are_normalized_for_protocol_matching() -> None:
    result = assess_comparability(_matching_local_protocol(), _reference_record())

    assert result["eligible_for_direct_difference"] is True
    assert result["normalized_metric_unit"] == "proportion"


def test_direction_mismatch_refuses_direct_difference() -> None:
    reference = _reference_record()
    reference["benchmark"]["higher_is_better"] = False

    result = assess_comparability(_matching_local_protocol(), reference)

    assert result["eligible_for_direct_difference"] is False
    assert "benchmark.higher_is_better" in result["mismatched_protocol_fields"]


@pytest.mark.parametrize("unit", [None, "fraction", "percentage"])
def test_missing_or_ambiguous_local_metric_unit_refuses_direct_difference(
    unit: Any,
) -> None:
    local_protocol = _matching_local_protocol()
    local_protocol["metric_unit"] = unit

    result = assess_comparability(local_protocol, _reference_record())

    assert result["eligible_for_direct_difference"] is False
    assert "benchmark.metric_unit" in (
        result["missing_protocol_fields"] + result["invalid_protocol_fields"]
    )


def test_ambiguous_reference_metric_unit_is_schema_invalid() -> None:
    record = _reference_record()
    record["benchmark"]["metric_unit"] = "fraction"

    result = validate_reference_record(record)

    assert result["valid"] is False
    assert "benchmark.metric_unit must be proportion or percent" in result["errors"]


def test_reference_direction_must_be_boolean() -> None:
    record = _reference_record()
    record["benchmark"]["higher_is_better"] = "yes"

    result = validate_reference_record(record)

    assert result["valid"] is False
    assert "benchmark.higher_is_better must be boolean" in result["errors"]


def test_reference_identity_must_be_complete() -> None:
    record = _reference_record()
    record["model_display_name"] = ""

    result = validate_reference_record(record)

    assert result["valid"] is False
    assert "model_display_name must be nonblank" in result["errors"]


def test_release_period_alone_cannot_satisfy_exact_historical_identity() -> None:
    record = _reference_record()
    record["model_snapshot_id"] = None
    record["measurement_date"] = None
    record["release_period"] = "2025-Q1"

    validation = validate_reference_record(record)
    assessment = assess_comparability(_matching_local_protocol(), record)

    assert validation["valid"] is True
    assert assessment["eligible_for_direct_difference"] is False
    assert "reference.model_snapshot_id" in assessment["missing_protocol_fields"]
    assert "reference.measurement_date" in assessment["missing_protocol_fields"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("evidence_class", "archived_response_reanalysis"),
        ("verification_status", "verified"),
    ],
)
def test_reference_evidence_hard_gates_refuse_delta(field: str, value: str) -> None:
    record = _reference_record()
    record[field] = value

    assessment = assess_comparability(_matching_local_protocol(), record)

    assert assessment["eligible_for_direct_difference"] is False
    assert f"reference.{field}" in assessment["invalid_protocol_fields"]


def test_different_sample_count_refuses_direct_difference() -> None:
    reference = deepcopy(_reference_record())
    reference["benchmark"]["sample_count"] = 198

    result = assess_comparability(_matching_local_protocol(), reference)

    assert result["eligible_for_direct_difference"] is False
    assert result["classification"] == "incompatible"
    assert "benchmark.sample_count" in result["mismatched_protocol_fields"]
    assert "refused" in result["reason"].lower()


def test_unknown_answer_extraction_refuses_direct_difference() -> None:
    reference = deepcopy(_reference_record())
    reference["protocol"]["answer_extraction"] = None

    result = assess_comparability(_matching_local_protocol(), reference)

    assert result["eligible_for_direct_difference"] is False
    assert result["classification"] == "unknown"
    assert "protocol.answer_extraction" in result["missing_protocol_fields"]


def test_compare_frontier_reports_percentage_point_delta_from_local_unit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    catalog = tmp_path / "catalog"
    _write_comparable_catalog(catalog)
    manifest = _qualified_manifest(score=0.8)
    monkeypatch.setattr(frontier, "load_run", lambda run_id, root=None: (manifest, []))

    result = compare_frontier("synthetic-run", catalog, root=tmp_path)

    assert result["direct_comparison_count"] == 3
    assert result["comparisons"][0]["difference"] == pytest.approx(7.5)
    assert result["comparisons"][0]["difference_unit"] == "percentage_points"


def test_percent_local_value_is_normalized_against_proportion_reference(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    catalog = tmp_path / "catalog"
    _write_comparable_catalog(catalog, score=0.725, metric_unit="proportion")
    manifest = _qualified_manifest(score=80.0)
    manifest["historical_protocol"]["metric_unit"] = "percent"
    monkeypatch.setattr(frontier, "load_run", lambda run_id, root=None: (manifest, []))

    result = compare_frontier("synthetic-run", catalog, root=tmp_path)

    assert result["comparisons"][0]["difference"] == pytest.approx(7.5)
    assert result["comparisons"][0]["difference_unit"] == "percentage_points"


def test_protocol_defined_end_to_end_rate_is_used_for_delta(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    catalog = tmp_path / "catalog"
    _write_comparable_catalog(catalog)
    for record_path in catalog.glob("*.yaml"):
        record = yaml.safe_load(record_path.read_text(encoding="utf-8"))
        record["benchmark"]["metric_name"] = "end_to_end_deployment_success"
        record_path.write_text(yaml.safe_dump(record), encoding="utf-8")
    manifest = _qualified_manifest(score=0.8)
    manifest["historical_protocol"]["metric_name"] = "end_to_end_deployment_success"
    manifest["aggregate"]["capability_conditional_on_valid_execution"]["rate"] = 0.1
    monkeypatch.setattr(frontier, "load_run", lambda run_id, root=None: (manifest, []))

    result = compare_frontier("synthetic-run", catalog, root=tmp_path)

    assert result["comparisons"][0]["difference"] == pytest.approx(7.5)
    assert result["comparisons"][0]["local_score_aggregate"] == ("end_to_end_deployment_success")


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("suite",), "smoke"),
        (("evidence_class",), "synthetic_mock"),
        (("served_model_evidence", "match"), False),
        (("served_model_evidence", "response_instance_id_sha256"), "sha256:other"),
        (("reasoning_evidence", "effective_status"), "unknown"),
        (("reasoning_evidence", "transmitted"), "medium"),
        (("locality_evidence", "status"), "remote"),
        (("model_instance_evidence", "selection"), "alias_match"),
        (("model_instance_evidence", "native_identity", "publisher"), None),
        (
            ("model_instance_evidence", "native_identity", "loaded_instance_id_match"),
            False,
        ),
        (("model_instance_evidence", "selected_variant"), "Q5_K_M"),
        (("runtime_evidence", "engine_version"), None),
        (("runtime_evidence", "cli_version"), ""),
        (("historical_protocol", "attempts_per_task"), 2),
        (("status",), "partial"),
        (("aggregate", "planned"), 99),
        (("aggregate", "attempted"), 99),
        (("aggregate", "completed"), 99),
        (("aggregate", "scorable"), 99),
        (("aggregate", "censored"), 1),
        (("aggregate", "unattempted"), 1),
        (
            (
                "aggregate",
                "capability_conditional_on_valid_execution",
                "total",
            ),
            99,
        ),
        (
            ("aggregate", "capability_conditional_on_valid_execution", "rate"),
            None,
        ),
    ],
)
def test_local_evidence_gate_refuses_before_any_delta(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    path: tuple[str, ...],
    replacement: Any,
) -> None:
    catalog = tmp_path / "catalog"
    _write_comparable_catalog(catalog)
    manifest = _qualified_manifest()
    target = manifest
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement
    monkeypatch.setattr(frontier, "load_run", lambda run_id, root=None: (manifest, []))

    with pytest.raises(ComparisonRefused) as raised:
        compare_frontier("synthetic-run", catalog, root=tmp_path)

    report = yaml.safe_load(str(raised.value))
    assert report["direct_comparison_count"] == 0
    assert all(item["difference"] is None for item in report["comparisons"])
    assert report["local_evidence_gate_met"] is False


def test_generic_protocol_is_not_a_historical_protocol_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    catalog = tmp_path / "catalog"
    _write_comparable_catalog(catalog)
    manifest = _qualified_manifest()
    manifest["protocol"] = manifest.pop("historical_protocol")
    monkeypatch.setattr(frontier, "load_run", lambda run_id, root=None: (manifest, []))

    with pytest.raises(ComparisonRefused) as raised:
        compare_frontier("synthetic-run", catalog, root=tmp_path)

    report = yaml.safe_load(str(raised.value))
    assert report["direct_comparison_count"] == 0
    assert report["local_evidence_gate_met"] is False


def test_missing_historical_field_keeps_difference_null(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    catalog = tmp_path / "catalog"
    _write_comparable_catalog(catalog)
    manifest = _qualified_manifest()
    manifest["historical_protocol"].pop("answer_extraction")
    monkeypatch.setattr(frontier, "load_run", lambda run_id, root=None: (manifest, []))

    with pytest.raises(ComparisonRefused) as raised:
        compare_frontier("synthetic-run", catalog, root=tmp_path)

    report = yaml.safe_load(str(raised.value))
    assert all(item["difference"] is None for item in report["comparisons"])
    assert all(
        "protocol.answer_extraction" in item["assessment"]["missing_protocol_fields"]
        for item in report["comparisons"]
    )


def test_unmet_catalog_coverage_returns_detailed_comparison_refusal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    (catalog / "record.yaml").write_text(yaml.safe_dump(_reference_record()), encoding="utf-8")
    manifest = _qualified_manifest()
    monkeypatch.setattr(frontier, "load_run", lambda run_id, root=None: (manifest, []))

    with pytest.raises(ComparisonRefused) as raised:
        compare_frontier("synthetic-run", catalog, root=tmp_path)

    report = yaml.safe_load(str(raised.value))
    assert report["catalog_schema_valid"] is True
    assert report["coverage_gate_met"] is False
    assert len(report["comparisons"]) == 1


def test_schema_invalid_catalog_still_raises_validation_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    record = _reference_record()
    record["provider"] = "Not Allowed"
    (catalog / "record.yaml").write_text(yaml.safe_dump(record), encoding="utf-8")
    manifest = _qualified_manifest()
    monkeypatch.setattr(frontier, "load_run", lambda run_id, root=None: (manifest, []))

    with pytest.raises(FrontierValidationError):
        compare_frontier("synthetic-run", catalog, root=tmp_path)
