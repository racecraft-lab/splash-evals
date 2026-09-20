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
PROVIDERS = {"OpenAI", "Anthropic", "Google"}
METRIC_UNITS = {"proportion", "percent"}
REASONING_MODES = {"off", "on", "low", "medium", "high"}


def _nonblank(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _score_as_proportion(value: Any, unit: Any) -> float | None:
    """Normalize an explicitly declared score unit without guessing its scale."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    score = float(value)
    if unit == "proportion" and 0 <= score <= 1:
        return score
    if unit == "percent" and 0 <= score <= 100:
        return score / 100.0
    return None


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
        "provider",
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
    provider = record.get("provider")
    if provider not in PROVIDERS:
        errors.append("provider must be one of: Anthropic, Google, OpenAI")
    if not _nonblank(record.get("model_display_name")):
        errors.append("model_display_name must be nonblank")
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
    metric_unit = benchmark.get("metric_unit")
    if metric_unit not in METRIC_UNITS:
        errors.append("benchmark.metric_unit must be proportion or percent")
    if not isinstance(benchmark.get("higher_is_better"), bool):
        errors.append("benchmark.higher_is_better must be boolean")
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
        unit = metric_unit
        if unit == "percent" and not 0 <= float(score) <= 100:
            errors.append("percent score must be in [0, 100]")
        if unit == "proportion" and not 0 <= float(score) <= 1:
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
        "higher_is_better": key[6],
        "dated_model_count": len(value["models"]),
        "provider_count": len(value["providers"]),
        "unknown_required_fields": unknown_required_fields,
        "meets_intersection_gate": (
            not unknown_required_fields
            and len(value["models"]) >= 3
            and len(value["providers"]) >= 2
        ),
    }


def _direct_reference_gate_failures(record: dict[str, Any]) -> list[str]:
    failures = _reference_identity_gaps(record)
    if record.get("evidence_class") != "published_historical_reference":
        failures.append("reference.evidence_class")
    if record.get("verification_status") != "source_verified":
        failures.append("reference.verification_status")
    if record.get("reported_score") is None:
        failures.append("reference.reported_score")
    return failures


def _catalog_direct_coverage(records: list[dict[str, Any]]) -> dict[str, Any]:
    providers: set[str] = set()
    dated_models: set[str] = set()
    intersections: dict[tuple[Any, ...], dict[str, set[str]]] = {}
    exclusions: list[dict[str, Any]] = []
    for record in records:
        failures = _direct_reference_gate_failures(record)
        if failures:
            exclusions.append({"reference_id": record.get("reference_id"), "reasons": failures})
            continue
        provider = str(record["provider"])
        model = str(record["model_display_name"])
        snapshot = str(record["model_snapshot_id"])
        providers.add(provider)
        dated_models.add(f"{provider}::{model}::{snapshot}")
        benchmark = _as_mapping(record.get("benchmark"))
        key = (
            benchmark.get("name"),
            benchmark.get("version"),
            benchmark.get("split"),
            benchmark.get("dataset_revision"),
            benchmark.get("metric_name"),
            benchmark.get("metric_unit"),
            benchmark.get("higher_is_better"),
        )
        bucket = intersections.setdefault(key, {"models": set(), "providers": set()})
        bucket["models"].add(f"{provider}::{model}::{snapshot}")
        bucket["providers"].add(provider)
    return {
        "providers": providers,
        "dated_models": dated_models,
        "intersections": intersections,
        "exclusions": exclusions,
        "candidate_count": len(records) - len(exclusions),
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
    records = [_read_record(path) for path in files]
    results = [
        validate_reference_record(record, source_name=str(path.relative_to(repo)))
        for path, record in zip(files, records, strict=True)
    ]
    coverage = _catalog_direct_coverage(records)
    coverage_rows = [
        _coverage_row(key, value)
        for key, value in sorted(
            coverage["intersections"].items(),
            key=lambda item: tuple(str(value) for value in item[0]),
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
        "dated_model_count": len(coverage["dated_models"]),
        "provider_count": len(coverage["providers"]),
        "direct_comparison_candidate_count": coverage["candidate_count"],
        "direct_comparison_excluded_count": len(coverage["exclusions"]),
        "direct_comparison_exclusions": coverage["exclusions"],
        "coverage_gate_met": coverage_gate_met,
        "coverage_gate_definition": (
            "one shared benchmark across at least 3 published, source-verified model snapshots "
            "from at least 2 providers; each reference requires an immutable snapshot ID and "
            "measurement date plus explicit non-null exact benchmark version, dataset revision, "
            "split, metric, unit, and direction fields"
        ),
        "coverage_intersections": coverage_rows,
        "results": results,
    }


def _protocol_mapping(
    local_protocol: dict[str, Any],
    benchmark: dict[str, Any],
    protocol: dict[str, Any],
) -> dict[str, tuple[Any, Any]]:
    return {
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
        "benchmark.higher_is_better": (
            local_protocol.get("higher_is_better"),
            benchmark.get("higher_is_better"),
        ),
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


def _metric_unit_evidence(
    local_protocol: dict[str, Any], benchmark: dict[str, Any]
) -> tuple[list[str], list[str], str | None]:
    local_unit = local_protocol.get("metric_unit")
    reference_unit = benchmark.get("metric_unit")
    missing = ["benchmark.metric_unit"] if local_unit is None or reference_unit is None else []
    invalid = (
        ["benchmark.metric_unit"]
        if (local_unit is not None and local_unit not in METRIC_UNITS)
        or (reference_unit is not None and reference_unit not in METRIC_UNITS)
        else []
    )
    normalized = (
        "proportion" if local_unit in METRIC_UNITS and reference_unit in METRIC_UNITS else None
    )
    return missing, invalid, normalized


def _reference_identity_gaps(reference: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if not _nonblank(reference.get("provider")):
        missing.append("reference.provider")
    if not _nonblank(reference.get("model_display_name")):
        missing.append("reference.model_display_name")
    if not _nonblank(reference.get("model_snapshot_id")):
        missing.append("reference.model_snapshot_id")
    if not _nonblank(reference.get("measurement_date")):
        missing.append("reference.measurement_date")
    return missing


def assess_comparability(
    local_protocol: dict[str, Any], reference: dict[str, Any]
) -> dict[str, Any]:
    """Require exact protocol facts before computing a historical difference."""
    benchmark = _as_mapping(reference.get("benchmark"))
    protocol = _as_mapping(reference.get("protocol"))
    mapping = _protocol_mapping(local_protocol, benchmark, protocol)
    missing = [field for field, pair in mapping.items() if pair[0] is None or pair[1] is None]
    unit_missing, invalid, normalized_unit = _metric_unit_evidence(local_protocol, benchmark)
    direct_gate_failures = _direct_reference_gate_failures(reference)
    missing.extend(unit_missing)
    missing.extend(_reference_identity_gaps(reference))
    invalid.extend(
        field
        for field in direct_gate_failures
        if field in {"reference.evidence_class", "reference.verification_status"}
    )
    mismatched = [
        field
        for field, pair in mapping.items()
        if pair[0] is not None and pair[1] is not None and pair[0] != pair[1]
    ]
    missing = list(dict.fromkeys(missing))
    invalid = list(dict.fromkeys(invalid))
    eligible = (
        not missing
        and not invalid
        and not mismatched
        and reference.get("reported_score") is not None
    )
    return {
        "eligible_for_direct_difference": eligible,
        "missing_protocol_fields": missing,
        "invalid_protocol_fields": invalid,
        "mismatched_protocol_fields": mismatched,
        "normalized_metric_unit": normalized_unit,
        "classification": ("matched" if eligible else "incompatible" if mismatched else "unknown"),
        "reason": "Protocol and metric fields match."
        if eligible
        else "Direct comparison refused because protocol evidence is missing or different.",
    }


def _as_mapping(value: Any) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _top_level_evidence_failures(manifest: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    expected_top_level = {
        "suite": "pilot",
        "held_out": True,
        "evidence_class": "local_measurement",
        "model_is_splash": True,
    }
    for field, expected in expected_top_level.items():
        if manifest.get(field) != expected:
            failures.append(f"{field} must equal {expected!r}")
    return failures


def _locality_evidence_failures(manifest: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    locality = _as_mapping(manifest.get("locality_evidence"))
    if locality.get("status") != "verified_local":
        failures.append("locality_evidence.status must be verified_local")
    if locality.get("endpoint_loopback") is not True:
        failures.append("locality_evidence.endpoint_loopback must be true")
    if locality.get("local_instance_evidence") is not True:
        failures.append("locality_evidence.local_instance_evidence must be true")
    return failures


def _model_evidence_failures(manifest: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    model = _as_mapping(manifest.get("model_instance_evidence"))
    if model.get("selection") != "exact_loaded_record":
        failures.append("model_instance_evidence.selection must be exact_loaded_record")
    if model.get("splash_attribution") != "confirmed":
        failures.append("model_instance_evidence.splash_attribution must be confirmed")
    if not _nonblank(model.get("model_key")):
        failures.append("model_instance_evidence.model_key must be nonblank")
    if not any(_nonblank(model.get(field)) for field in ("file_revision", "selected_variant")):
        failures.append("model_instance_evidence requires an immutable artifact selector")
    native_identity = _as_mapping(model.get("native_identity"))
    for field in (
        "model_key",
        "publisher",
        "architecture",
        "format",
        "quantization",
        "selected_variant",
    ):
        if not _nonblank(native_identity.get(field)):
            failures.append(f"model_instance_evidence.native_identity.{field} must be nonblank")
    if native_identity.get("loaded_instance_id_match") is not True:
        failures.append(
            "model_instance_evidence.native_identity.loaded_instance_id_match must be true"
        )
    outer_variant = model.get("selected_variant")
    native_variant = native_identity.get("selected_variant")
    if _nonblank(outer_variant) and outer_variant != native_variant:
        failures.append(
            "model_instance_evidence selected_variant must match native_identity.selected_variant"
        )
    return failures


def _served_model_evidence_failures(manifest: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    served = _as_mapping(manifest.get("served_model_evidence"))
    requested_instance = served.get("requested_instance_id_sha256")
    response_instance = served.get("response_instance_id_sha256")
    if served.get("status") != "verified":
        failures.append("served_model_evidence.status must be verified")
    if not _nonblank(requested_instance) or not _nonblank(response_instance):
        failures.append("served model instance hashes must be nonblank")
    if requested_instance != response_instance or served.get("match") is not True:
        failures.append("served model instance must match the requested instance")
    return failures


def _reasoning_evidence_failures(manifest: dict[str, Any]) -> tuple[list[str], Any]:
    failures: list[str] = []
    reasoning = _as_mapping(manifest.get("reasoning_evidence"))
    requested_reasoning = reasoning.get("requested")
    transmitted_reasoning = reasoning.get("transmitted")
    supported_options = reasoning.get("supported_options")
    if requested_reasoning not in REASONING_MODES:
        failures.append("reasoning_evidence.requested is invalid")
    if requested_reasoning != transmitted_reasoning:
        failures.append("reasoning_evidence.requested must equal transmitted")
    if not isinstance(supported_options, list) or requested_reasoning not in supported_options:
        failures.append("requested reasoning mode must be supported")
    if reasoning.get("default") is not None and reasoning.get("default") not in REASONING_MODES:
        failures.append("reasoning_evidence.default is invalid")
    if reasoning.get("effective_status") != "accepted_by_runtime":
        failures.append("reasoning_evidence.effective_status must be accepted_by_runtime")
    return failures, requested_reasoning


def _runtime_evidence_failures(manifest: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    runtime = _as_mapping(manifest.get("runtime_evidence"))
    if runtime.get("transport") != "lmstudio_native_v1":
        failures.append("runtime_evidence.transport must be lmstudio_native_v1")
    if runtime.get("endpoint") != "/api/v1/chat":
        failures.append("runtime_evidence.endpoint must be /api/v1/chat")
    for field in ("cli_version", "engine", "engine_version"):
        if not _nonblank(runtime.get(field)):
            failures.append(f"runtime_evidence.{field} must be nonblank")
    if runtime.get("app_version") is not None and not isinstance(runtime.get("app_version"), str):
        failures.append("runtime_evidence.app_version must be a string or null")
    return failures


def _completion_evidence_failures(
    manifest: dict[str, Any], protocol: dict[str, Any], aggregate: dict[str, Any]
) -> list[str]:
    failures: list[str] = []
    if manifest.get("status") != "completed":
        failures.append("run status must be completed")
    sample_count = protocol.get("sample_count")
    if not isinstance(sample_count, int) or isinstance(sample_count, bool) or sample_count <= 0:
        failures.append("historical_protocol.sample_count must be a positive integer")
    for field in ("planned", "attempted", "completed", "scorable"):
        if aggregate.get(field) != sample_count:
            failures.append(f"aggregate.{field} must equal historical_protocol.sample_count")
    for field in ("censored", "unattempted"):
        if aggregate.get(field) != 0:
            failures.append(f"aggregate.{field} must equal zero")
    return failures


def _denominator_compatible_aggregate(metric_name: Any) -> str | None:
    if metric_name == "end_to_end_deployment_success":
        return "end_to_end_deployment_success"
    if metric_name in {"accuracy", "capability_conditional_on_valid_execution"}:
        return "capability_conditional_on_valid_execution"
    return None


def _protocol_score_evidence(
    manifest: dict[str, Any], requested_reasoning: Any
) -> tuple[list[str], Any, Any, float | None, str | None]:
    failures: list[str] = []
    historical_protocol = _as_mapping(manifest.get("historical_protocol"))
    if historical_protocol.get("attempts_per_task") != 1:
        failures.append("historical_protocol.attempts_per_task must equal 1")
    if historical_protocol.get("reasoning_mode") != requested_reasoning:
        failures.append("historical protocol reasoning mode must match runtime evidence")
    local_unit = historical_protocol.get("metric_unit")
    if local_unit not in METRIC_UNITS:
        failures.append("historical_protocol.metric_unit must be proportion or percent")
    aggregate = _as_mapping(manifest.get("aggregate"))
    failures.extend(_completion_evidence_failures(manifest, historical_protocol, aggregate))
    aggregate_name = _denominator_compatible_aggregate(historical_protocol.get("metric_name"))
    selected = _as_mapping(aggregate.get(aggregate_name)) if aggregate_name else {}
    if aggregate_name is None:
        failures.append(
            "historical_protocol.metric_name has no denominator-compatible local aggregate"
        )
    if selected.get("total") != historical_protocol.get("sample_count"):
        failures.append(
            "selected local aggregate total must equal historical_protocol.sample_count"
        )
    local_score = selected.get("rate")
    local_score_proportion = _score_as_proportion(local_score, local_unit)
    if local_score_proportion is None:
        failures.append("selected local aggregate must be numeric and in its declared unit")
    return failures, local_score, local_unit, local_score_proportion, aggregate_name


def _assess_local_evidence(manifest: dict[str, Any]) -> dict[str, Any]:
    """Fail closed unless a held-out local Splash pilot is fully attributable."""
    reasoning_failures, requested_reasoning = _reasoning_evidence_failures(manifest)
    protocol_failures, local_score, local_unit, local_score_proportion, aggregate_name = (
        _protocol_score_evidence(manifest, requested_reasoning)
    )
    failures = [
        *_top_level_evidence_failures(manifest),
        *_locality_evidence_failures(manifest),
        *_model_evidence_failures(manifest),
        *_served_model_evidence_failures(manifest),
        *reasoning_failures,
        *_runtime_evidence_failures(manifest),
        *protocol_failures,
    ]

    return {
        "met": not failures,
        "failures": failures,
        "local_score": local_score,
        "local_score_unit": local_unit if local_unit in METRIC_UNITS else None,
        "local_score_proportion": local_score_proportion,
        "local_score_aggregate": aggregate_name,
    }


def _comparison_item(
    record: dict[str, Any],
    local_protocol: dict[str, Any],
    local_evidence: dict[str, Any],
) -> dict[str, Any]:
    assessment = assess_comparability(local_protocol, record)
    benchmark = _as_mapping(record.get("benchmark"))
    item = {
        "reference_id": record.get("reference_id"),
        "model": record.get("model_display_name"),
        "date": record.get("measurement_date") or record.get("release_period"),
        "evidence_class": record.get("evidence_class"),
        "catalog_comparability": record.get("comparability"),
        "assessment": assessment,
        "local_score": local_evidence["local_score"],
        "local_score_unit": local_evidence["local_score_unit"],
        "local_score_aggregate": local_evidence["local_score_aggregate"],
        "historical_score": record.get("reported_score"),
        "historical_score_unit": benchmark.get("metric_unit"),
        "difference": None,
        "difference_unit": None,
    }
    historical_score_proportion = _score_as_proportion(
        record.get("reported_score"), benchmark.get("metric_unit")
    )
    if (
        local_evidence["met"]
        and assessment["eligible_for_direct_difference"]
        and local_evidence["local_score_proportion"] is not None
        and historical_score_proportion is not None
    ):
        item["difference"] = (
            local_evidence["local_score_proportion"] - historical_score_proportion
        ) * 100.0
        item["difference_unit"] = "percentage_points"
    return item


def compare_frontier(
    run_id: str, catalog: str | Path, *, root: Path | None = None
) -> dict[str, Any]:
    repo = root or project_root()
    validation = validate_catalog(catalog, root=repo)
    schema_valid = validation.get("schema_valid", validation.get("valid", False))
    coverage_gate_met = validation.get("coverage_gate_met", validation.get("valid", False))
    if not schema_valid:
        raise FrontierValidationError("frontier catalog contains invalid records")
    manifest, _ = load_run(run_id, repo)
    local_protocol = _as_mapping(manifest.get("historical_protocol"))
    local_evidence = _assess_local_evidence(manifest)
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
        comparisons.append(_comparison_item(record, local_protocol, local_evidence))
    direct_count = sum(1 for item in comparisons if item["difference"] is not None)
    report = {
        "schema_version": 1,
        "run_id": run_id,
        "catalog_schema_valid": schema_valid,
        "coverage_gate_met": coverage_gate_met,
        "local_evidence_gate_met": local_evidence["met"],
        "local_evidence_failures": local_evidence["failures"],
        "primary_objective_status": manifest.get("primary_objective_status_if_run", "blocked"),
        "direct_comparison_count": direct_count,
        "comparisons": comparisons,
        "status": (
            "compared"
            if direct_count and coverage_gate_met
            else "historical_comparison_unavailable"
        ),
        "limitation": (
            "Pilot/synthetic protocols cannot be subtracted from full historical benchmark "
            "aggregates unless all required fields match."
        ),
    }
    if not coverage_gate_met or not direct_count:
        raise ComparisonRefused(json.dumps(report, sort_keys=True))
    return report
