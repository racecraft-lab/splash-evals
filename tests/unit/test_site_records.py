"""Verify the static-site boundary against the shared public evidence contract."""

from __future__ import annotations

import json
from pathlib import Path
from shutil import copyfile

import pytest
from pydantic import ValidationError

from local_evals.site_records import (
    EXPORT_PATH,
    GPQA_PATH,
    build_site_records,
    serialized_site_records,
)

ROOT = Path(__file__).resolve().parents[2]


def test_checked_site_export_matches_validated_public_sources() -> None:
    assert (ROOT / EXPORT_PATH).read_text() == serialized_site_records(ROOT)
    bundle = build_site_records(ROOT)
    gpqa = bundle["benchmarks"]["GPQA Diamond"]["result"]
    assert gpqa["score"] == 0.5455
    assert gpqa["runtime"]["duration_seconds"] == 8716
    assert gpqa["tested_configuration"]["temperature"] is None
    assert gpqa["identity"]["dataset_revision"] is None
    coding = bundle["benchmarks"]["SWE-bench Verified"]
    assert coding["result"]["status"] == "complete"
    assert coding["result"]["score"] == 0.72
    assert coding["result"]["task_outcomes"] == {
        "resolved": 360,
        "unresolved": 122,
        "model_failure": 16,
        "infrastructure_error": 2,
    }
    assert coding["result"]["counts"] == {"requested": 500, "succeeded": 482, "errored": 18}
    assert len(coding["references"]) == 12
    assert sum(ref["evaluator"] == "Vals AI" for ref in coding["references"]) == 10


def test_restored_gpqa_references_are_cross_provider_and_keep_swe_roster() -> None:
    bundle = build_site_records(ROOT)
    gpqa = bundle["benchmarks"]["GPQA Diamond"]["references"]
    swe = bundle["benchmarks"]["SWE-bench Verified"]["references"]

    assert len(gpqa) == 23
    assert len(swe) == 12
    cross_provider = [ref for ref in gpqa if ref["evaluator_class"] == "cross_provider"]
    assert len(cross_provider) == 2
    assert {ref["developer"] for ref in cross_provider} == {"Anthropic"}
    assert {ref["evaluator"] for ref in cross_provider} == {"OpenAI"}


def test_generator_classifies_cross_provider_before_url_heuristics() -> None:
    source = (ROOT / "docs-site/scripts/generate-content.mjs").read_text(encoding="utf-8")
    branch = "if (record.evaluator_class === 'cross_provider')"
    assert branch in source
    assert "return ['cross-provider', 'Cross-provider'];" in source
    assert source.index(branch) < source.index("if (/openai\\.com/i.test(url)")


@pytest.fixture
def public_root(tmp_path: Path) -> Path:
    target = tmp_path / GPQA_PATH
    target.parent.mkdir(parents=True)
    copyfile(ROOT / GPQA_PATH, target)
    return tmp_path


def _coding_fixture(root: Path) -> dict:
    record = build_site_records(root)["benchmarks"]["GPQA Diamond"]["result"]
    record.update(result_id="synthetic-coding", score=0.5, metric_name="resolution_rate")
    record["counts"] = {"requested": 500, "succeeded": 500, "errored": 0}
    record["task_outcomes"] = {
        "resolved": 250,
        "unresolved": 250,
        "model_failure": 0,
        "infrastructure_error": 0,
    }
    record["identity"].update(
        name="SWE-bench Verified",
        variant="verified-500",
        subset="verified",
        dataset_id="SWE-bench/SWE-bench_Verified",
        split="test",
    )
    return record


def test_future_reviewed_coding_result_uses_the_same_contract(public_root: Path) -> None:
    record = _coding_fixture(public_root)
    (public_root / "results/public/coding.json").write_text(json.dumps(record))
    result = build_site_records(public_root)["benchmarks"]["SWE-bench Verified"]["result"]
    assert result["score"] == 0.5
    assert result["counts"]["requested"] == 500


@pytest.mark.parametrize(
    "change",
    ["variant", "denominator", "qualification_score", "unknown_field", "outcomes", "score"],
)
def test_site_rejects_invalid_or_mixed_coding_results(public_root: Path, change: str) -> None:
    record = _coding_fixture(public_root)
    if change == "variant":
        record["identity"]["variant"] = "lite"
    elif change == "denominator":
        record["counts"].update(requested=10, succeeded=10)
    elif change == "qualification_score":
        record["status"] = "qualification"
    elif change == "outcomes":
        record["task_outcomes"]["unresolved"] = 249
    elif change == "score":
        record["score"] = 0.9
    else:
        record["raw_trajectory"] = "must not pass the shared schema"
    (public_root / "results/public/coding.json").write_text(json.dumps(record))
    with pytest.raises(ValidationError):
        build_site_records(public_root)


def test_site_refuses_ambiguous_public_result_selection(public_root: Path) -> None:
    for name in ("coding-a.json", "coding-b.json"):
        (public_root / "results/public" / name).write_text(json.dumps(_coding_fixture(public_root)))
    with pytest.raises(ValueError, match="Multiple public results"):
        build_site_records(public_root)
