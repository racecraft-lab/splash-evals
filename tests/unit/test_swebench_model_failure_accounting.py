"""Synthetic summary/result policy tests; no SWE-bench task execution occurs."""

from __future__ import annotations

from pathlib import Path

import local_evals.swebench as swebench_module
from local_evals.benchmark_results import BenchmarkStatus


def _prepared_verified() -> swebench_module._Prepared:
    digest = "sha256:" + "a" * 64
    profile = {
        "mode": "verified",
        "model": "synthetic-local-model",
        "platform": "linux/amd64",
        "parameters": {
            "reasoning_effort": "xhigh",
            "temperature": 1.0,
            "top_p": 0.95,
            "max_output_tokens": 65_536,
        },
        "control_image_digest": digest,
    }
    task = swebench_module._Task(
        instance_id="synthetic-task",
        base_commit="a" * 40,
        repository="synthetic/project",
        problem_statement_sha256="b" * 64,
        record_sha256="c" * 64,
    )
    return swebench_module._Prepared(
        profile=profile,
        repo=Path("/synthetic/repo"),
        state=Path("/synthetic/state"),
        server_origin="http://127.0.0.1:1234/v1",
        manifest_sha256="d" * 64,
        image_bindings_sha256="e" * 64,
        dataset_id="synthetic/SWE-bench_Verified",
        dataset_revision="f" * 40,
        protocol_fingerprint="1" * 64,
        tasks=(task,) * 500,
        runner_config_sha256="2" * 64,
        blockers=(),
    )


def test_verified_summary_and_result_measure_all_model_failures() -> None:
    prepared = _prepared_verified()
    results = [{"status": "model_failure", "attempted": True} for _ in prepared.tasks]

    summary = swebench_module._benchmark_summary(prepared, results)
    benchmark = swebench_module._benchmark_result(prepared, "synthetic-run", summary)

    assert summary["status"] == "completed"
    assert summary["completed_count"] == 0
    assert summary["model_failure_count"] == 500
    assert summary["infrastructure_error_count"] == 0
    assert summary["resolution_rate"] == 0.0
    assert summary["capability_claim_allowed"] is True
    assert benchmark.status is BenchmarkStatus.COMPLETE
    assert benchmark.score == 0.0
    assert benchmark.counts.succeeded == 0
    assert benchmark.counts.errored == 500


def test_verified_summary_and_result_count_infrastructure_failure_as_unresolved() -> None:
    prepared = _prepared_verified()
    results = ([{"status": "completed", "grade": "resolved", "attempted": True}] * 499) + [
        {"status": "infrastructure_error", "attempted": False}
    ]

    summary = swebench_module._benchmark_summary(prepared, results)
    benchmark = swebench_module._benchmark_result(prepared, "synthetic-run", summary)

    assert summary["status"] == "completed"
    assert summary["resolved_count"] == 499
    assert summary["completed_count"] == 499
    assert summary["model_failure_count"] == 0
    assert summary["infrastructure_error_count"] == 1
    assert summary["capability_claim_allowed"] is True
    assert benchmark.status is BenchmarkStatus.COMPLETE
    assert benchmark.score == 499 / 500
    assert benchmark.counts.succeeded == 499
    assert benchmark.counts.errored == 1


def test_verified_summary_and_result_withhold_all_infrastructure_failures() -> None:
    prepared = _prepared_verified()
    results = [{"status": "infrastructure_error", "attempted": False}] * 500

    summary = swebench_module._benchmark_summary(prepared, results)
    benchmark = swebench_module._benchmark_result(prepared, "synthetic-run", summary)

    assert summary["status"] == "partial"
    assert summary["capability_claim_allowed"] is False
    assert benchmark.status is BenchmarkStatus.FAILED
    assert benchmark.score is None
