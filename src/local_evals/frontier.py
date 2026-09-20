"""Validation and fail-closed comparison for dated frontier references."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import yaml

from .runs import RunError, load_run, project_root


class FrontierValidationError(RunError):
    """A historical record fails the project reference schema."""


class ComparisonRefused(RunError):
    """A requested historical comparison is not protocol-supported."""


EVIDENCE_CLASSES = {
    "local_measurement",
    "archived_response_reanalysis",
    "published_historical_reference",
    "context_only_incompatible_reference",
}
COMPARABILITY = {"matched", "partially_matched", "incompatible", "unknown"}


def _read_record(path: Path) -> dict[str, Any]:
    if path.suffix.casefold() == ".json":
        value = json.loads(path.read_text(encoding="utf-8"))
    else:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise FrontierValidationError(f"{path.name}: record must be a mapping")
    return value


def validate_reference_record(
    record: dict[str, Any], *, source_name: str = "record"
) -> dict[str, Any]:
    """Validate one record without treating an unmeasured stub as a zero score."""
    errors: list[str] = []
    warnings: list[str] = []
    required = (
        "schema_version",
        "reference_id",
        "evidence_class",
        "benchmark",
        "protocol",
        "comparability",
        "comparability_notes",
        "verification_status",
    )
    for field in required:
        if field not in record:
            errors.append(f"missing {field}")
    evidence_class = record.get("evidence_class")
    if evidence_class not in EVIDENCE_CLASSES:
        errors.append("invalid evidence_class")
    comparability = record.get("comparability")
    if comparability not in COMPARABILITY:
        errors.append("invalid comparability")
    if not isinstance(record.get("comparability_notes"), list) or not record.get(
        "comparability_notes"
    ):
        errors.append("comparability_notes must contain a human-readable reason")
    source_url = record.get("source_url")
    if source_url is not None:
        parsed = urlparse(str(source_url))
        if parsed.scheme != "https" or not parsed.netloc:
            errors.append("source_url must be an absolute https URL")
    benchmark = record.get("benchmark")
    protocol = record.get("protocol")
    if not isinstance(benchmark, dict):
        errors.append("benchmark must be a mapping")
        benchmark = {}
    if not isinstance(protocol, dict):
        errors.append("protocol must be a mapping")
        protocol = {}
    for field in ("name", "version", "split", "metric_name", "metric_unit", "higher_is_better"):
        if field not in benchmark:
            errors.append(
                f"benchmark.{field} is required (null is permitted only when explicitly unknown)"
            )
    if "attempts_per_task" not in protocol:
        errors.append(
            "protocol.attempts_per_task is required (null may represent an explicit unknown)"
        )
    score = record.get("reported_score")
    if score is None:
        warnings.append(
            "reported_score is null; this is a registry stub and must not render as zero"
        )
        if comparability == "matched":
            errors.append("a null-score stub cannot be comparability=matched")
        record_kind = "stub"
    elif not isinstance(score, (int, float)) or isinstance(score, bool):
        errors.append("reported_score must be numeric or null")
        record_kind = "invalid"
    else:
        unit = benchmark.get("metric_unit")
        if unit == "percent" and not 0 <= float(score) <= 100:
            errors.append("percent score must be in [0, 100]")
        if unit in {"proportion", "fraction"} and not 0 <= float(score) <= 1:
            errors.append("proportion score must be in [0, 1]")
        if not source_url or not record.get("source_locator"):
            errors.append("numeric score requires source_url and source_locator")
        if not record.get("source_revision_or_content_hash"):
            errors.append("numeric score requires source revision or content hash")
        record_kind = "measured_reference"
    if comparability == "matched":
        matched_requirements = {
            "model_snapshot_id": record.get("model_snapshot_id"),
            "measurement_date": record.get("measurement_date"),
            "benchmark.version": benchmark.get("version"),
            "benchmark.split": benchmark.get("split"),
            "benchmark.sample_count": benchmark.get("sample_count"),
            "protocol.attempts_per_task": protocol.get("attempts_per_task"),
            "protocol.scorer_revision": protocol.get("scorer_revision"),
            "protocol.answer_extraction": protocol.get("answer_extraction"),
        }
        missing = [key for key, value in matched_requirements.items() if value is None]
        if missing:
            errors.append("matched record lacks protocol evidence: " + ", ".join(missing))
    return {
        "source": source_name,
        "reference_id": record.get("reference_id"),
        "valid": not errors,
        "record_kind": record_kind,
        "errors": errors,
        "warnings": warnings,
    }


def _unknown_exact_benchmark_fields(version: Any, dataset_revision: Any) -> list[str]:
    """Return required benchmark identity fields that are absent or blank."""
    unknown: list[str] = []
    for field, field_value in (
        ("benchmark.version", version),
        ("benchmark.dataset_revision", dataset_revision),
    ):
        if field_value is None or (isinstance(field_value, str) and not field_value.strip()):
            unknown.append(field)
    return unknown


def _coverage_row(key: tuple[Any, ...], value: dict[str, set[str]]) -> dict[str, Any]:
    """Render one intersection while keeping unknown identity fields visible."""
    unknown_required_fields = _unknown_exact_benchmark_fields(key[1], key[3])
    return {
        "benchmark": key[0],
        "version": key[1],
        "split": key[2],
        "dataset_revision": key[3],
        "metric_name": key[4],
        "metric_unit": key[5],
        "dated_model_count": len(value["models"]),
        "provider_count": len(value["providers"]),
        "unknown_required_fields": unknown_required_fields,
        "meets_intersection_gate": (
            not unknown_required_fields
            and len(value["models"]) >= 3
            and len(value["providers"]) >= 2
        ),
    }


def validate_catalog(catalog: str | Path, *, root: Path | None = None) -> dict[str, Any]:
    repo = root or project_root()
    path = Path(catalog)
    if not path.is_absolute():
        path = repo / path
    resolved = path.resolve()
    if repo.resolve() not in resolved.parents and resolved != repo.resolve():
        raise FrontierValidationError(
            "frontier catalog must be inside the public source repository"
        )
    if not resolved.is_dir():
        raise FrontierValidationError("frontier catalog directory not found")
    files = sorted(
        candidate
        for candidate in resolved.rglob("*")
        if candidate.is_file() and candidate.suffix.casefold() in {".yaml", ".yml", ".json"}
    )
    if not files:
        raise FrontierValidationError("frontier catalog contains no reference records")
    results = [
        validate_reference_record(_read_record(path), source_name=str(path.relative_to(repo)))
        for path in files
    ]
    providers: set[str] = set()
    dated_models: set[str] = set()
    intersections: dict[tuple[Any, ...], dict[str, set[str]]] = {}
    for path in files:
        record = _read_record(path)
        if record.get("provider"):
            providers.add(str(record["provider"]))
        if record.get("model_display_name") and (
            record.get("model_snapshot_id") or record.get("release_period")
        ):
            dated_models.add(str(record["model_display_name"]))
        benchmark_value = record.get("benchmark")
        benchmark = (
            cast(dict[str, Any], benchmark_value) if isinstance(benchmark_value, dict) else {}
        )
        if (
            record.get("reported_score") is not None
            and record.get("provider")
            and record.get("model_display_name")
            and (record.get("model_snapshot_id") or record.get("release_period"))
        ):
            key = (
                benchmark.get("name"),
                benchmark.get("version"),
                benchmark.get("split"),
                benchmark.get("dataset_revision"),
                benchmark.get("metric_name"),
                benchmark.get("metric_unit"),
            )
            bucket = intersections.setdefault(key, {"models": set(), "providers": set()})
            model_identity = (
                f"{record.get('provider')}::{record.get('model_display_name')}::"
                f"{record.get('model_snapshot_id') or record.get('release_period')}"
            )
            bucket["models"].add(model_identity)
            bucket["providers"].add(str(record["provider"]))
    coverage_rows = [
        _coverage_row(key, value)
        for key, value in sorted(
            intersections.items(), key=lambda item: tuple(str(value) for value in item[0])
        )
    ]
    schema_valid = all(item["valid"] for item in results)
    coverage_gate_met = any(row["meets_intersection_gate"] for row in coverage_rows)
    return {
        "schema_version": 1,
        "catalog": str(resolved.relative_to(repo)),
        "record_count": len(results),
        "valid": schema_valid and coverage_gate_met,
        "schema_valid": schema_valid,
        "dated_model_count": len(dated_models),
        "provider_count": len(providers),
        "coverage_gate_met": coverage_gate_met,
        "coverage_gate_definition": (
            "one shared benchmark with explicit non-null exact version and dataset revision, "
            "plus matching split/metric fields, across at least 3 dated models from at least "
            "2 providers"
        ),
        "coverage_intersections": coverage_rows,
        "results": results,
    }


def assess_comparability(
    local_protocol: dict[str, Any], reference: dict[str, Any]
) -> dict[str, Any]:
    """Require exact protocol facts before computing a historical difference."""
    benchmark_value = reference.get("benchmark")
    protocol_value = reference.get("protocol")
    benchmark = cast(dict[str, Any], benchmark_value) if isinstance(benchmark_value, dict) else {}
    protocol = cast(dict[str, Any], protocol_value) if isinstance(protocol_value, dict) else {}
    mapping = {
        "benchmark.name": (local_protocol.get("benchmark_name"), benchmark.get("name")),
        "benchmark.version": (local_protocol.get("benchmark_version"), benchmark.get("version")),
        "benchmark.split": (local_protocol.get("split"), benchmark.get("split")),
        "benchmark.dataset_revision": (
            local_protocol.get("dataset_revision"),
            benchmark.get("dataset_revision"),
        ),
        "benchmark.sample_id_manifest": (
            local_protocol.get("sample_id_manifest"),
            benchmark.get("sample_id_manifest"),
        ),
        "benchmark.metric_name": (local_protocol.get("metric_name"), benchmark.get("metric_name")),
        "benchmark.metric_unit": (local_protocol.get("metric_unit"), benchmark.get("metric_unit")),
        "benchmark.sample_count": (
            local_protocol.get("sample_count"),
            benchmark.get("sample_count"),
        ),
        "protocol.few_shot": (local_protocol.get("few_shot"), protocol.get("few_shot")),
        "protocol.prompts_or_template_revision": (
            local_protocol.get("prompts_or_template_revision"),
            protocol.get("prompts_or_template_revision"),
        ),
        "protocol.reasoning_mode": (
            local_protocol.get("reasoning_mode"),
            protocol.get("reasoning_mode"),
        ),
        "protocol.output_budget": (
            local_protocol.get("output_budget"),
            protocol.get("output_budget"),
        ),
        "protocol.attempts_per_task": (
            local_protocol.get("attempts_per_task"),
            protocol.get("attempts_per_task"),
        ),
        "protocol.aggregation": (local_protocol.get("aggregation"), protocol.get("aggregation")),
        "protocol.tool_access": (local_protocol.get("tool_access"), protocol.get("tool_access")),
        "protocol.agent_scaffold_revision": (
            local_protocol.get("agent_scaffold_revision"),
            protocol.get("agent_scaffold_revision"),
        ),
        "protocol.scorer_revision": (
            local_protocol.get("scorer_revision"),
            protocol.get("scorer_revision"),
        ),
        "protocol.answer_extraction": (
            local_protocol.get("answer_extraction"),
            protocol.get("answer_extraction"),
        ),
    }
    missing = [field for field, pair in mapping.items() if pair[0] is None or pair[1] is None]
    mismatched = [
        field
        for field, pair in mapping.items()
        if pair[0] is not None and pair[1] is not None and pair[0] != pair[1]
    ]
    eligible = not missing and not mismatched and reference.get("reported_score") is not None
    return {
        "eligible_for_direct_difference": eligible,
        "missing_protocol_fields": missing,
        "mismatched_protocol_fields": mismatched,
        "classification": "matched" if eligible else "incompatible" if mismatched else "unknown",
        "reason": "Protocol and metric fields match."
        if eligible
        else "Direct comparison refused because protocol evidence is missing or different.",
    }


def compare_frontier(
    run_id: str, catalog: str | Path, *, root: Path | None = None
) -> dict[str, Any]:
    repo = root or project_root()
    validation = validate_catalog(catalog, root=repo)
    if not validation["valid"]:
        raise FrontierValidationError("frontier catalog contains invalid records")
    manifest, _ = load_run(run_id, repo)
    local_protocol = manifest.get("historical_protocol") or manifest.get("protocol") or {}
    local_score = (
        (manifest.get("aggregate") or {})
        .get("capability_conditional_on_valid_execution", {})
        .get("rate")
    )
    path = Path(catalog)
    if not path.is_absolute():
        path = repo / path
    comparisons: list[dict[str, Any]] = []
    for record_path in sorted(
        candidate
        for candidate in path.resolve().rglob("*")
        if candidate.suffix.casefold() in {".yaml", ".yml", ".json"}
    ):
        record = _read_record(record_path)
        assessment = assess_comparability(local_protocol, record)
        item = {
            "reference_id": record.get("reference_id"),
            "model": record.get("model_display_name"),
            "date": record.get("measurement_date") or record.get("release_period"),
            "evidence_class": record.get("evidence_class"),
            "catalog_comparability": record.get("comparability"),
            "assessment": assessment,
            "local_score": local_score,
            "historical_score": record.get("reported_score"),
            "difference": None,
        }
        if assessment["eligible_for_direct_difference"] and local_score is not None:
            unit = record["benchmark"]["metric_unit"]
            local_on_scale = float(local_score) * 100.0 if unit == "percent" else float(local_score)
            item["difference"] = local_on_scale - float(record["reported_score"])
        comparisons.append(item)
    direct_count = sum(1 for item in comparisons if item["difference"] is not None)
    report = {
        "schema_version": 1,
        "run_id": run_id,
        "primary_objective_status": manifest.get("primary_objective_status_if_run", "blocked"),
        "direct_comparison_count": direct_count,
        "comparisons": comparisons,
        "status": "compared" if direct_count else "historical_comparison_unavailable",
        "limitation": (
            "Pilot/synthetic protocols cannot be subtracted from full historical benchmark "
            "aggregates unless all required fields match."
        ),
    }
    if not direct_count:
        raise ComparisonRefused(json.dumps(report, sort_keys=True))
    return report
