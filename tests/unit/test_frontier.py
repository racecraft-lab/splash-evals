from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml

import local_evals.frontier as frontier
from local_evals.frontier import (
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
        "provider": "Synthetic Provider",
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
            "reasoning_mode": "synthetic-fixed",
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
        "verification_status": "verified",
    }


def _matching_local_protocol() -> dict[str, Any]:
    return {
        "benchmark_name": "Synthetic Benchmark",
        "benchmark_version": "synthetic-v1",
        "split": "test",
        "dataset_revision": "synthetic-dataset-v1",
        "sample_id_manifest": "synthetic-sample-manifest-v1",
        "metric_name": "accuracy",
        "metric_unit": "percent",
        "sample_count": 100,
        "few_shot": 0,
        "prompts_or_template_revision": "synthetic-template-v1",
        "reasoning_mode": "synthetic-fixed",
        "output_budget": 512,
        "attempts_per_task": 1,
        "aggregation": "mean",
        "tool_access": False,
        "agent_scaffold_revision": "none",
        "scorer_revision": "synthetic-scorer-v1",
        "answer_extraction": "exact",
    }


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


def _write_catalog_records(catalog: Path, *, version: Any, dataset_revision: Any) -> None:
    catalog.mkdir()
    for index, provider in enumerate(("Provider A", "Provider B", "Provider B"), start=1):
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


def test_direct_difference_is_eligible_only_for_exact_protocol_match() -> None:
    result = assess_comparability(_matching_local_protocol(), _reference_record())

    assert result["eligible_for_direct_difference"] is True
    assert result["classification"] == "matched"
    assert result["missing_protocol_fields"] == []
    assert result["mismatched_protocol_fields"] == []


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


def test_compare_frontier_accepts_normalized_protocol_from_run_manifest(
    monkeypatch, tmp_path: Path
) -> None:
    record = _reference_record()
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    (catalog / "record.yaml").write_text(yaml.safe_dump(record), encoding="utf-8")
    manifest = {
        "protocol": _matching_local_protocol(),
        "aggregate": {"capability_conditional_on_valid_execution": {"rate": 0.8}},
        "primary_objective_status_if_run": "pilot_only",
    }
    monkeypatch.setattr(frontier, "load_run", lambda run_id, root=None: (manifest, []))
    monkeypatch.setattr(frontier, "validate_catalog", lambda catalog, root=None: {"valid": True})

    result = compare_frontier("synthetic-run", catalog, root=tmp_path)

    assert result["direct_comparison_count"] == 1
    assert result["comparisons"][0]["difference"] == pytest.approx(7.5)
