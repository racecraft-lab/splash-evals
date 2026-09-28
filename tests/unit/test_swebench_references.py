from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from local_evals.frontier import validate_reference_record

ROOT = Path(__file__).resolve().parents[2]
FRONTIER = ROOT / "references" / "frontier"
COVERAGE_PATH = ROOT / "references" / "benchmarks" / "swebench-verified-coverage.yaml"

EXPECTED: dict[str, tuple[str, str, float, int | None, str]] = {
    "anthropic-claude-opus-5-vals-swebench.yaml": (
        "Claude Opus 5",
        "Vals AI",
        97.0,
        485,
        "mini-swe-agent",
    ),
    "openai-gpt-5-6-sol-vals-swebench.yaml": (
        "GPT-5.6 Sol",
        "Vals AI",
        96.2,
        481,
        "mini-swe-agent",
    ),
    "openai-gpt-5-6-terra-vals-swebench.yaml": (
        "GPT-5.6 Terra",
        "Vals AI",
        95.4,
        477,
        "mini-swe-agent",
    ),
    "openai-gpt-5-6-luna-vals-swebench.yaml": (
        "GPT-5.6 Luna",
        "Vals AI",
        93.0,
        465,
        "mini-swe-agent",
    ),
    "anthropic-claude-opus-4-8-vals-swebench.yaml": (
        "Claude Opus 4.8",
        "Vals AI",
        88.6,
        443,
        "mini-swe-agent",
    ),
    "openai-gpt-5-5-vals-swebench.yaml": ("GPT-5.5", "Vals AI", 82.6, 413, "mini-swe-agent"),
    "anthropic-claude-opus-4-7-vals-swebench.yaml": (
        "Claude Opus 4.7",
        "Vals AI",
        82.0,
        410,
        "mini-swe-agent",
    ),
    "anthropic-claude-sonnet-5-vals-swebench.yaml": (
        "Claude Sonnet 5",
        "Vals AI",
        79.6,
        398,
        "mini-swe-agent",
    ),
    "anthropic-claude-opus-4-6-vals-swebench.yaml": (
        "Claude Opus 4.6",
        "Vals AI",
        78.2,
        391,
        "mini-swe-agent",
    ),
    "anthropic-claude-sonnet-4-6-vals-swebench.yaml": (
        "Claude Sonnet 4.6",
        "Vals AI",
        77.4,
        387,
        "mini-swe-agent",
    ),
    "anthropic-claude-sonnet-4-standard-swebench.yaml": (
        "Claude Sonnet 4",
        "Anthropic",
        72.7,
        None,
        "Anthropic simple scaffold",
    ),
    "anthropic-claude-opus-4-standard-swebench.yaml": (
        "Claude Opus 4",
        "Anthropic",
        72.5,
        None,
        "Anthropic simple scaffold",
    ),
}

VALS_SOURCE = "https://www.vals.ai/benchmarks/swebench"
VALS_HASH = "6d30bc0a858f9957f07070c2b3e60223f7e24f03bcb4464eadbab78367aad12e"
ANTHROPIC_SOURCE = "https://www.anthropic.com/news/claude-4"
ANTHROPIC_HASH = "f8bdec872490418168a1832e1ab5695007b7bfa707e6af78bd2f9d88d8005d8b"

EXPECTED_SETTINGS: dict[str, dict[str, Any]] = {
    "anthropic-claude-opus-5-vals-swebench.yaml": {
        "temperature": None,
        "top_p": None,
        "max_output_tokens": None,
        "reasoning": None,
        "reasoning_effort": None,
        "compute_effort": None,
        "other_settings": "provider_default_except_max_tokens",
    },
    "openai-gpt-5-6-sol-vals-swebench.yaml": {
        "temperature": None,
        "top_p": None,
        "max_output_tokens": 128000,
        "reasoning": None,
        "reasoning_effort": "max",
        "compute_effort": None,
        "other_settings": "provider_default_except_max_tokens",
    },
    "openai-gpt-5-6-terra-vals-swebench.yaml": {
        "temperature": None,
        "top_p": None,
        "max_output_tokens": 128000,
        "reasoning": None,
        "reasoning_effort": "max",
        "compute_effort": None,
        "other_settings": "provider_default_except_max_tokens",
    },
    "openai-gpt-5-6-luna-vals-swebench.yaml": {
        "temperature": None,
        "top_p": None,
        "max_output_tokens": 128000,
        "reasoning": None,
        "reasoning_effort": "max",
        "compute_effort": None,
        "other_settings": "provider_default_except_max_tokens",
    },
    "anthropic-claude-opus-4-8-vals-swebench.yaml": {
        "temperature": 1,
        "top_p": None,
        "max_output_tokens": 128000,
        "reasoning": None,
        "reasoning_effort": None,
        "compute_effort": "max",
        "other_settings": "provider_default_except_max_tokens",
    },
    "openai-gpt-5-5-vals-swebench.yaml": {
        "temperature": None,
        "top_p": None,
        "max_output_tokens": 128000,
        "reasoning": None,
        "reasoning_effort": "xhigh",
        "compute_effort": None,
        "other_settings": "provider_default_except_max_tokens",
    },
    "anthropic-claude-opus-4-7-vals-swebench.yaml": {
        "temperature": 1,
        "top_p": None,
        "max_output_tokens": 128000,
        "reasoning": None,
        "reasoning_effort": None,
        "compute_effort": "max",
        "other_settings": "provider_default_except_max_tokens",
    },
    "anthropic-claude-sonnet-5-vals-swebench.yaml": {
        "temperature": 1,
        "top_p": None,
        "max_output_tokens": 128000,
        "reasoning": None,
        "reasoning_effort": None,
        "compute_effort": "max",
        "other_settings": "provider_default_except_max_tokens",
    },
    "anthropic-claude-opus-4-6-vals-swebench.yaml": {
        "temperature": 1,
        "top_p": None,
        "max_output_tokens": 128000,
        "reasoning": None,
        "reasoning_effort": None,
        "compute_effort": "max",
        "other_settings": "provider_default_except_max_tokens",
    },
    "anthropic-claude-sonnet-4-6-vals-swebench.yaml": {
        "temperature": 1,
        "top_p": None,
        "max_output_tokens": 128000,
        "reasoning": True,
        "reasoning_effort": None,
        "compute_effort": "max",
        "other_settings": "provider_default_except_max_tokens",
    },
    "anthropic-claude-sonnet-4-standard-swebench.yaml": {
        "extended_thinking": False,
        "bash_tool": True,
        "string_replace_file_edit_tool": True,
        "planning_tool": False,
        "high_compute_parallel_sampling": False,
        "output_budget": None,
    },
    "anthropic-claude-opus-4-standard-swebench.yaml": {
        "extended_thinking": False,
        "bash_tool": True,
        "string_replace_file_edit_tool": True,
        "planning_tool": False,
        "high_compute_parallel_sampling": False,
        "output_budget": None,
    },
}


def _load(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


@pytest.mark.parametrize(("filename", "expected"), EXPECTED.items())
def test_swebench_external_reference_preserves_source_and_variant(
    filename: str, expected: tuple[str, str, float, int | None, str]
) -> None:
    model, evaluator, score, resolved_tasks, harness = expected
    record = _load(FRONTIER / filename)

    assert validate_reference_record(record, source_name=filename)["valid"] is True
    assert record["model_display_name"] == model
    assert record["reported_score"] == score
    assert record["benchmark"]["name"] == "SWE-bench Verified"
    assert record["benchmark"]["sample_count"] == 500
    assert record["external_evaluation"]["evaluator"] == evaluator
    assert record["evaluator"] == evaluator
    assert record["evaluator_class"] == (
        "independent" if evaluator == "Vals AI" else "provider_reported"
    )
    assert record["external_evaluation"]["harness_name"] == harness
    assert record["external_evaluation"]["denominator"] == 500
    assert record["external_evaluation"]["resolved_tasks"] == resolved_tasks
    assert record["external_evaluation"]["variant"]
    assert record["external_evaluation"]["unknowns"]
    assert record["external_evaluation"]["settings"] == EXPECTED_SETTINGS[filename]
    assert record["source_locator"]
    if evaluator == "Vals AI":
        assert record["source_url"] == VALS_SOURCE
        assert record["source_revision_or_content_hash"].endswith("sha256:" + VALS_HASH)
    else:
        assert record["source_url"] == ANTHROPIC_SOURCE
        assert record["source_revision_or_content_hash"].endswith("sha256:" + ANTHROPIC_HASH)
    assert record["retrieved_on"] == "2026-09-21"
    assert record["evidence_class"] == "context_only_incompatible_reference"
    assert record["comparability"] == "incompatible"
    notes = " ".join(record["comparability_notes"])
    assert "must not support an exact ranking or gap claim" in notes


def test_source_coverage_is_complete_but_never_claims_comparability() -> None:
    coverage = _load(COVERAGE_PATH)
    records = [_load(path) for path in sorted(FRONTIER.glob("*swebench*.yaml"))]
    ids = {record["reference_id"] for record in records}

    assert len(records) == 12
    assert coverage["purpose"] == "external_source_coverage_only"
    assert coverage["coverage_status"] == "source_covered"
    assert coverage["comparability_status"] == "not_established"
    assert coverage["local_measurement_status"] == "measured"
    assert coverage["requested_model_count"] == 12
    assert coverage["source_covered_model_count"] == 12
    assert coverage["direct_comparison_eligible_count"] == 0
    assert coverage["denominator_per_evaluation"] == 500
    assert len(coverage["roster"]) == 12
    assert {item["reference_id"] for item in coverage["roster"]} == ids
    assert {item["model"] for item in coverage["roster"]} == {
        expected[0] for expected in EXPECTED.values()
    }
    assert sum(group["record_count"] for group in coverage["source_groups"]) == 12
    assert all(record["comparability"] == "incompatible" for record in records)


def test_optional_evaluator_contract_fails_closed_when_partially_populated() -> None:
    record = _load(FRONTIER / "openai-gpt-5-6-sol-vals-swebench.yaml")
    record.pop("evaluator")
    result = validate_reference_record(record)
    assert result["valid"] is False
    assert "evaluator must be nonblank when evaluator metadata is present" in result["errors"]

    record["evaluator"] = "Vals AI"
    record["evaluator_class"] = "publisher_inferred"
    result = validate_reference_record(record)
    assert result["valid"] is False
    assert (
        "evaluator_class must be independent, provider_reported, or cross_provider"
        in result["errors"]
    )
