from __future__ import annotations

import json
from pathlib import Path

import pytest

import local_evals.runs as runs
from local_evals.benchmark_results import (
    BenchmarkCounts,
    BenchmarkEvaluator,
    BenchmarkIdentity,
    BenchmarkProvenance,
    BenchmarkResult,
    BenchmarkStatus,
    EvaluatorClass,
    MetricUnit,
    TaskOutcomes,
)
from local_evals.models import (
    DiscoveryReport,
    LocalityEvidence,
    LocalityStatus,
    ModelRecord,
    ModelRecordSource,
    QualifiedEndpoint,
)
from local_evals.runs import (
    ResumeRefused,
    ScoringError,
    Task,
    assemble_tool_arguments,
    parse_and_score,
    parse_tool_call,
    resume_run,
    run_scorer_self_test,
)


def test_scorer_self_test_has_at_least_twelve_synthetic_cases() -> None:
    result = run_scorer_self_test()

    assert result["passed"] is True
    assert result["case_count"] >= 12
    assert result["evidence_class"] == "synthetic_mock"


@pytest.mark.parametrize(
    "content",
    [
        "not-json",
        '<think>{"tool":"synthetic"}',
        '{"tool":"synthetic"',
    ],
)
def test_malformed_or_leading_angle_json_is_not_repaired(content: str) -> None:
    task = Task("synthetic-json", "tools", "synthetic", "json", {"tool": "synthetic"})

    result = parse_and_score(task, {"content": content, "finish_reason": "stop"})

    assert result == {
        "scorable": False,
        "score": None,
        "failure_type": "client_parser_error",
    }


def test_output_cap_is_censoring_not_parser_or_wrong_answer() -> None:
    task = Task("synthetic-json", "tools", "synthetic", "json", {"ok": True})

    result = parse_and_score(
        task,
        {"content": '{"ok": tr', "finish_reason": "length"},
    )

    assert result["scorable"] is False
    assert result["score"] is None
    assert result["failure_type"] == "output_budget_exhaustion"


def test_fragmented_tool_arguments_are_assembled_only_after_complete_json() -> None:
    result = assemble_tool_arguments(['{"city":', '"Synthetic City",', '"units":"metric"}'])

    assert result == {"city": "Synthetic City", "units": "metric"}


@pytest.mark.parametrize(
    "fragments",
    [
        ['{"city":"Synthetic City"'],
        ['<think>{"city":"Synthetic City"}'],
        ['{"city": invalid}'],
    ],
)
def test_malformed_or_leading_angle_tool_arguments_are_not_repaired(
    fragments: list[str],
) -> None:
    with pytest.raises(ScoringError, match="client_parser_error"):
        assemble_tool_arguments(fragments)


def test_parse_tool_call_preserves_fragment_order() -> None:
    response = {
        "tool_argument_fragments": ['{"city":', '"Synthetic City"}'],
    }

    result = parse_tool_call(response)

    assert result == {"city": "Synthetic City"}


def test_public_task_manifest_hashes_but_does_not_reveal_prompt_or_answer() -> None:
    task = Task(
        "synthetic-private-task",
        "harness",
        "synthetic held-out prompt",
        "exact",
        "synthetic held-out answer",
    )

    manifest = task.public_manifest()

    assert "prompt" not in manifest
    assert "expected" not in manifest
    assert len(manifest["prompt_sha256"]) == 64
    assert len(manifest["expected_sha256"]) == 64


def test_mock_only_mode_blocks_live_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_EVALS_TEST_MODE", "mock-only")

    with pytest.raises(runs.RunError, match="live inference is disabled"):
        runs._chat_once(
            "http://127.0.0.1:1234",
            None,
            "synthetic-local-model",
            "synthetic prompt",
            {"max_tokens": 16},
        )


def test_resume_refuses_protocol_fingerprint_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest = {
        "status": "partial",
        "config_id": "lmstudio-as-found",
        "suite": "smoke",
        "allow_expanded": False,
        "requested_settings": {"max_tokens": 64},
        "fingerprint": "definitely-not-the-current-fingerprint",
    }
    monkeypatch.setattr(runs, "load_run", lambda run_id, root=None: (manifest, []))
    monkeypatch.setattr(runs, "_run_dir", lambda run_id, root=None: tmp_path / run_id)
    monkeypatch.setattr(runs, "load_config", lambda name, root=None: {"id": name})
    monkeypatch.setattr(
        runs,
        "build_plan",
        lambda *args, **kwargs: {
            "model_id": "synthetic-local-model",
            "locality_evidence": {"status": "verified_local"},
            "model_instance_evidence": {"instance_id_sha256": "synthetic"},
            "protocol": {"scorer_version": "synthetic-v2"},
        },
    )

    with pytest.raises(ResumeRefused, match="fingerprint mismatch"):
        resume_run("synthetic-run", dry_run=True, root=tmp_path)


def test_resume_refuses_after_served_instance_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest = {"status": "partial"}
    attempts = [
        {
            "task_id": "synthetic-task",
            "score": {
                "scorable": False,
                "score": None,
                "failure_type": "served_model_instance_mismatch",
            },
        }
    ]
    monkeypatch.setattr(runs, "_run_dir", lambda *args, **kwargs: tmp_path)
    monkeypatch.setattr(runs, "load_run", lambda *args, **kwargs: (manifest, attempts))

    with pytest.raises(ResumeRefused, match="unverified served model instance"):
        resume_run("synthetic-run", dry_run=True, root=tmp_path)


def _discovery_report(
    locality_status: LocalityStatus,
    *,
    key: str = "splash",
    instance_id: str = "local-instance-7",
    selected_variant: str | None = "Qwen3.8-27B",
    file_revision: str | None = None,
    native_key: str | None = None,
    native_display_name: str | None = "Qwen3.8 27B Splash",
    native_architecture: str | None = "qwen3_8",
    include_native: bool = True,
) -> DiscoveryReport:
    records = [
        ModelRecord(
            key=key,
            source=ModelRecordSource.LMS_CLI_LOADED,
            instance_id=instance_id,
            selected_variant=selected_variant,
            file_revision=file_revision,
            engine="mlx",
            engine_version="synthetic-version",
            loaded=True,
        )
    ]
    if include_native:
        records.append(
            ModelRecord(
                key=native_key or key,
                source=ModelRecordSource.NATIVE_REST,
                display_name=native_display_name,
                publisher="qwen",
                architecture=native_architecture,
                model_format="mlx",
                model_type="llm",
                quantization="4bit",
                selected_variant=selected_variant,
                loaded=True,
                loaded_instance_ids=(instance_id,),
                reasoning_allowed=("off", "low", "medium", "high", "on"),
                reasoning_default="on",
            )
        )
    return DiscoveryReport(
        endpoint=QualifiedEndpoint(
            url="http://127.0.0.1:1234",
            host="127.0.0.1",
            port=1234,
            resolved_addresses=("127.0.0.1",),
        ),
        locality=LocalityEvidence(
            status=locality_status,
            endpoint_loopback=True,
            lm_link_state=(
                "not_linked" if locality_status is LocalityStatus.VERIFIED_LOCAL else "connected"
            ),
            local_instance_evidence=locality_status is LocalityStatus.VERIFIED_LOCAL,
            execution_device="private-device-identifier-must-not-be-recorded",
            reasons=("synthetic locality evidence",),
        ),
        cli_version="synthetic-cli-version",
        app_version="synthetic-app-version",
        models=tuple(records),
    )


def _local_config() -> dict[str, object]:
    return {
        "server": {
            "origin": "http://127.0.0.1:1234",
            "openai_base_url": "http://127.0.0.1:1234/v1",
            "api_key_env": "LM_STUDIO_API_KEY",
        },
        "model": {"key": "splash"},
        "operation_requested": {"max_tokens": 64},
        "reasoning_control": {
            "desired_mode": "on",
            "transmitted": "on",
        },
    }


def _qualified_core_plan() -> dict[str, object]:
    instance_hash = "b" * 64
    selection_hash = "a" * 64
    return {
        "schema_version": 1,
        "suite": "core",
        "status": "planned",
        "blockers": [],
        "runner": "evalscope-1.12",
        "experiment_id": "core-experiment",
        "model_id": "racecraft-splash-local",
        "model_is_splash": True,
        "evidence_class": "local_measurement",
        "held_out": True,
        "selection_status": "held_out_verified",
        "selection_hash": selection_hash,
        "selection_evidence": {
            "status": "held_out_verified",
            "ordered_sample_manifest_sha256": selection_hash,
            "manifest_source": "external",
            "frozen_before_tuning": True,
            "contamination_review_revision": "core-review-v1",
        },
        "calibration_heldout_separation": "held_out",
        "protocol": {
            "task_set": "private-frozen-core-v1",
            "scorer_version": "family-manifest-pinned",
        },
        "historical_protocol": {
            "benchmark_name": "Racecraft private frozen core",
            "benchmark_version": "private-frozen-core-v1",
            "split": "held_out",
            "dataset_revision": selection_hash,
            "sample_id_manifest": selection_hash,
            "metric_name": "accuracy",
            "metric_unit": "proportion",
            "sample_count": 60,
            "few_shot": 0,
            "prompts_or_template_revision": selection_hash,
            "reasoning_mode": "off",
            "output_budget": 4096,
            "attempts_per_task": 1,
            "aggregation": "mean_binary_score",
            "tool_access": "family_defined",
            "agent_scaffold_revision": None,
            "scorer_revision": "family-manifest-pinned",
            "answer_extraction": "family_qualified_exact",
            "higher_is_better": True,
            "failure_policy": "count_failures_as_incorrect",
            "denominator": "all_planned_samples",
        },
        "locality_evidence": {
            "status": "verified_local",
            "endpoint_loopback": True,
            "local_instance_evidence": True,
        },
        "model_instance_evidence": {
            "selection": "exact_loaded_record",
            "splash_attribution": "confirmed",
            "instance_id_sha256": instance_hash,
            "native_identity": {"loaded_instance_id_match": True},
        },
        "runtime_evidence": {
            "transport": "lmstudio_native_v1",
            "endpoint": "/api/v1/chat",
            "cli_version": "synthetic-cli-v1",
            "app_version": "synthetic-app-v1",
            "engine": "synthetic-engine",
            "engine_version": "synthetic-engine-v1",
        },
        "reasoning_evidence": {"requested": "off", "transmitted": "off"},
        "primary_objective_status_if_run": "answered_with_stated_scope",
        "limitations": [],
    }


def _qualified_family_results() -> list[dict[str, object]]:
    counts = {
        "gpqa_diamond": 12,
        "ifeval": 16,
        "mmlu_pro": 14,
        "tool_json": 10,
        "context": 8,
    }
    return [
        {
            "schema_version": 1,
            "family": family,
            "planned": count,
            "attempted": count,
            "scored": count,
            "correct": count - 1,
            "score": (count - 1) / count,
            "metric_name": "accuracy",
            "metric_unit": "proportion",
            "scorer_revision": f"{family}-scorer-v1",
            "scorer_provenance_sha256": str(index) * 64,
            "calibration_manifest_sha256": format(index, "x") * 64,
            "selection_manifest_sha256": format(index + 5, "x") * 64,
            "ordered_sample_ids_sha256": format(index + 10, "x") * 64,
            "dataset_tree_sha256": format(index + 1, "x") * 64,
            "dataset_index_sha256": format(index + 2, "x") * 64,
            "report_sha256": format(index + 3, "x") * 64,
            "failure_counts": {"incorrect": 1, "execution_error": 0, "unscored": 0},
        }
        for index, (family, count) in enumerate(counts.items(), start=1)
    ]


@pytest.mark.parametrize(
    "locality_status",
    [LocalityStatus.AMBIGUOUS_LM_LINK, LocalityStatus.REMOTE],
)
def test_model_resolution_blocks_ambiguous_or_remote_execution(
    monkeypatch: pytest.MonkeyPatch,
    locality_status: LocalityStatus,
) -> None:
    monkeypatch.setattr(
        runs,
        "discover_lmstudio",
        lambda *args, **kwargs: _discovery_report(locality_status),
    )

    attribution = runs._resolve_model(_local_config())

    assert any("not verified local" in blocker for blocker in attribution.blockers)
    assert attribution.locality_evidence["status"] == locality_status.value
    assert "execution_device" not in attribution.locality_evidence
    assert "private-device-identifier" not in repr(attribution.model_instance_evidence)


def test_alias_substring_alone_does_not_claim_splash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_identifier = "splash-qwen3.8"
    monkeypatch.setattr(
        runs,
        "discover_lmstudio",
        lambda *args, **kwargs: _discovery_report(
            LocalityStatus.VERIFIED_LOCAL,
            key=model_identifier,
            selected_variant=None,
            file_revision=None,
            include_native=False,
        ),
    )
    config = _local_config()
    config["model"] = {"key": model_identifier}

    attribution = runs._resolve_model(config)

    assert attribution.blockers == ()
    assert attribution.model_is_splash is False
    assert attribution.model_instance_evidence["splash_attribution"] == "not_confirmed"


def test_openai_alias_and_native_key_mismatch_do_not_claim_splash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _discovery_report(
        LocalityStatus.VERIFIED_LOCAL,
        key="synthetic-loaded-model",
        selected_variant=None,
        native_key="qwen/qwen3.8-27b-splash",
    )
    report = report.model_copy(
        update={
            "models": report.models
            + (
                ModelRecord(
                    key="splash-alias",
                    source=ModelRecordSource.OPENAI_COMPAT,
                ),
            )
        }
    )
    monkeypatch.setattr(runs, "discover_lmstudio", lambda *args, **kwargs: report)
    config = _local_config()
    config["model"] = {"key": "synthetic-loaded-model"}

    attribution = runs._resolve_model(config)

    assert attribution.blockers == ()
    assert attribution.model_is_splash is False
    assert attribution.model_instance_evidence["native_identity"] is None


def test_verified_local_splash_requires_corroborated_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runs,
        "discover_lmstudio",
        lambda *args, **kwargs: _discovery_report(LocalityStatus.VERIFIED_LOCAL),
    )

    attribution = runs._resolve_model(_local_config())

    assert attribution.blockers == ()
    assert attribution.model_id == "local-instance-7"
    assert attribution.model_is_splash is True
    assert attribution.model_instance_evidence["instance_id_sha256"] != "local-instance-7"
    assert attribution.model_instance_evidence["splash_attribution"] == "confirmed"
    native = attribution.model_instance_evidence["native_identity"]
    assert native["source"] == "native_rest"
    assert native["display_name"] == "Qwen3.8 27B Splash"
    assert "device" not in repr(native).casefold()


def test_publisher_qualified_native_key_corroborates_exact_bare_cli_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    splash_model = "qwen3.8-27b-splash"
    monkeypatch.setattr(
        runs,
        "discover_lmstudio",
        lambda *args, **kwargs: _discovery_report(
            LocalityStatus.VERIFIED_LOCAL,
            key=splash_model,
            native_key=f"qwen/{splash_model}",
        ),
    )
    config = _local_config()
    config["model"] = {"key": splash_model}

    assert runs._resolve_model(config).model_is_splash is True


def test_native_response_view_requires_matching_instance_and_ignores_reasoning() -> None:
    payload = {
        "model_instance_id": "selected-instance",
        "output": [
            {"type": "reasoning", "content": "private chain"},
            {"type": "message", "content": "final answer"},
        ],
        "stats": {"total_output_tokens": 9, "reasoning_output_tokens": 4},
    }

    view = runs._response_view(payload, "selected-instance")

    assert view["content"] == "final answer"
    assert view["served_instance_match"] is True
    assert view["response_instance_id_sha256"] == runs._sha256_text("selected-instance")
    assert "private chain" not in repr(view)


@pytest.mark.parametrize(
    ("served", "failure"),
    [
        (None, "missing_served_model_instance"),
        ("other-instance", "served_model_instance_mismatch"),
    ],
)
def test_native_response_view_blocks_unproven_instance(served: str | None, failure: str) -> None:
    payload: dict[str, object] = {"output": [{"type": "message", "content": "answer"}]}
    if served is not None:
        payload["model_instance_id"] = served

    view = runs._response_view(payload, "selected-instance")

    assert view["content"] is None
    assert view["protocol_error"] == failure
    assert view["served_instance_match"] is False


def test_historical_protocol_marks_practical_pilot_ineligible() -> None:
    protocol = runs._historical_protocol(
        "pilot",
        {"task_set": "builtin-heldout-pilot-v1", "scorer_version": "builtin-exact-v1"},
        sample_count=5,
        output_budget=512,
        reasoning_mode="on",
    )

    assert protocol["benchmark_name"] == "Racecraft practical task set"
    assert protocol["metric_unit"] == "proportion"
    assert protocol["split"] is None
    assert protocol["sample_id_manifest"] is None
    assert protocol["higher_is_better"] is True
    assert len(protocol) == 19


def test_historical_protocol_requires_immutable_selection_evidence_for_held_out() -> None:
    sample_manifest = "a" * 64
    protocol = runs._historical_protocol(
        "future-aligned",
        {
            "task_set": "future-aligned-v1",
            "scorer_version": "scorer-v1",
            "selection_evidence": {
                "ordered_sample_manifest_sha256": sample_manifest,
                "contamination_review_revision": "review-v1",
                "frozen_before_tuning": True,
            },
        },
        sample_count=10,
        output_budget=512,
        reasoning_mode="on",
    )

    assert protocol["split"] == "held_out"
    assert protocol["sample_id_manifest"] == sample_manifest


def test_plan_records_native_runtime_reasoning_and_practical_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(runs, "load_config", lambda *args, **kwargs: _local_config())
    monkeypatch.setattr(
        runs,
        "load_suite",
        lambda *args, **kwargs: {
            "expanded": False,
            "repetitions": 1,
            "max_output_tokens": 64,
            "task_set": "builtin-heldout-pilot-v1",
            "scorer_version": "builtin-exact-v1",
            "evidence_class": "local_measurement",
        },
    )
    monkeypatch.setattr(
        runs,
        "load_policies",
        lambda *args, **kwargs: {"initial_run_limits": {}},
    )
    monkeypatch.setattr(
        runs,
        "get_state_dir",
        lambda *args, **kwargs: tmp_path / "private-state",
    )
    monkeypatch.setattr(
        runs,
        "discover_lmstudio",
        lambda *args, **kwargs: _discovery_report(LocalityStatus.VERIFIED_LOCAL),
    )

    plan = runs.build_plan("pilot", "lmstudio-as-found", root=tmp_path)

    assert plan["blockers"] == []
    assert plan["runtime_evidence"] == {
        "transport": "lmstudio_native_v1",
        "endpoint": "/api/v1/chat",
        "cli_version": "synthetic-cli-version",
        "app_version": "synthetic-app-version",
        "engine": "mlx",
        "engine_version": "synthetic-version",
    }
    assert plan["reasoning_evidence"] == {
        "requested": "on",
        "transmitted": "on",
        "supported_options": ["off", "low", "medium", "high", "on"],
        "default": "on",
        "effective_status": "not_attempted",
    }
    assert plan["historical_protocol"]["benchmark_name"] == ("Racecraft practical task set")
    assert plan["held_out"] is False
    assert plan["selection_status"] == "post_hoc_exploratory"
    assert plan["historical_comparison_eligibility"]["eligible_for_frontier_deltas"] is False


def test_openai_compatible_reasoning_evidence_separates_effort_from_native_mode() -> None:
    evidence, blockers = runs._reasoning_evidence(
        {
            "reasoning_control": {
                "mechanism": "openai-compatible-reasoning-effort",
                "desired_mode": "on",
                "desired_effort": "medium",
                "transmitted": "medium",
            }
        },
        {
            "native_identity": {
                "reasoning_allowed": ["off", "on"],
                "reasoning_default": "on",
            }
        },
    )

    assert blockers == []
    assert evidence == {
        "requested": "medium",
        "transmitted": "medium",
        "supported_options": ["none", "minimal", "low", "medium", "high", "xhigh"],
        "model_supported_modes": ["off", "on"],
        "default": "on",
        "effective_status": "not_attempted",
    }


def test_openai_core_transport_blocks_native_reasoning_contract_during_planning() -> None:
    _evidence, blockers = runs._reasoning_evidence(
        {
            "reasoning_control": {
                "mechanism": "native-v1",
                "desired_mode": "on",
                "desired_effort": None,
                "transmitted": "on",
            }
        },
        {
            "native_identity": {
                "reasoning_allowed": ["off", "on"],
                "reasoning_default": "on",
            }
        },
        transport="openai-compatible-reasoning-effort",
    )

    assert blockers == [
        "Core EvalScope transport requires an OpenAI-compatible reasoning-effort contract."
    ]


def test_served_model_evidence_hashes_actual_response_instance() -> None:
    expected = "selected-instance"
    actual = "different-instance"
    manifest = {
        "reasoning_evidence": {"effective_status": "accepted_by_runtime"},
        "effective_settings_status": "accepted_by_runtime_not_read_back",
        "served_model_evidence": {"status": "verified", "match": True},
    }
    attempts = [
        {
            "served_instance_match": True,
            "response_instance_id_sha256": runs._sha256_text(actual),
        }
    ]

    runs._finalize_run_manifest(manifest, attempts, 1, None, expected)

    assert manifest["served_model_evidence"]["status"] == "not_verified"
    assert manifest["served_model_evidence"]["response_instance_id_sha256"] is None
    assert manifest["reasoning_evidence"]["effective_status"] == "not_verified"


def test_execute_refuses_before_inference_when_locality_is_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(runs, "load_config", lambda *args, **kwargs: _local_config())
    monkeypatch.setattr(
        runs,
        "load_suite",
        lambda *args, **kwargs: {
            "expanded": False,
            "repetitions": 1,
            "max_output_tokens": 64,
            "task_set": "builtin-smoke-v1",
            "scorer_version": "builtin-exact-v1",
            "evidence_class": "synthetic_harness",
        },
    )
    monkeypatch.setattr(
        runs,
        "load_policies",
        lambda *args, **kwargs: {
            "initial_run_limits": {
                "max_live_requests": 250,
                "max_generated_tokens": 250_000,
                "max_wall_minutes": 45,
            }
        },
    )
    monkeypatch.setattr(
        runs,
        "get_state_dir",
        lambda *args, **kwargs: tmp_path / "external-state",
    )
    monkeypatch.setattr(
        runs,
        "discover_lmstudio",
        lambda *args, **kwargs: _discovery_report(LocalityStatus.AMBIGUOUS_LM_LINK),
    )
    inference_called = False

    def forbidden_inference(*args: object, **kwargs: object) -> tuple[dict[str, object], float]:
        nonlocal inference_called
        inference_called = True
        raise AssertionError("inference must not be reached")

    monkeypatch.setattr(runs, "_chat_once", forbidden_inference)

    with pytest.raises(runs.RunError, match="not verified local"):
        runs.execute_run("smoke", "lmstudio-as-found", root=tmp_path)

    assert inference_called is False


def test_core_plan_uses_sanitized_evalscope_readiness_without_synthetic_tasks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = tmp_path / "external-state"
    profile = {
        "expanded": True,
        "repetitions": 1,
        "max_output_tokens": 4096,
        "task_set": "private-frozen-core-v1",
        "scorer_version": "family-manifest-pinned",
        "evidence_class": "local_measurement",
    }
    readiness = {
        "status": "ready",
        "blockers": [],
        "metadata": {
            "runner": "evalscope-1.12",
            "evalscope_version": "1.12.0",
            "total_samples": 60,
            "manifest_set_sha256": "a" * 64,
        },
    }
    monkeypatch.setattr(runs, "load_suite", lambda *args, **kwargs: profile)
    monkeypatch.setattr(runs, "load_config", lambda *args, **kwargs: _local_config())
    monkeypatch.setattr(
        runs,
        "load_policies",
        lambda *args, **kwargs: {"initial_run_limits": {}},
    )
    monkeypatch.setattr(runs, "get_state_dir", lambda *args, **kwargs: state)
    monkeypatch.setattr(
        runs,
        "_resolve_model",
        lambda config: runs.ModelAttribution(
            model_id="publisher/racecraft-splash-local",
            model_is_splash=True,
            locality_evidence={"status": "verified_local", "verified": True},
            model_instance_evidence={"selection": "exact_loaded_record"},
            blockers=(),
        ),
    )
    monkeypatch.setattr(
        runs,
        "_reasoning_evidence",
        lambda *args, **kwargs: ({"transmitted": "medium"}, []),
    )
    monkeypatch.setattr(runs, "inspect_core_readiness", lambda *args, **kwargs: readiness)
    monkeypatch.setattr(
        runs,
        "suite_tasks",
        lambda suite: (_ for _ in ()).throw(AssertionError("core must not use built-in tasks")),
    )

    plan = runs.build_execution_plan(
        "core", "lmstudio-as-found", allow_expanded=True, root=tmp_path
    )

    assert plan["runner"] == "evalscope-1.12"
    assert plan["core_readiness"] == readiness
    assert plan["tasks"] == []
    assert plan["sample_count"] == 60
    assert plan["request_count"] == 60
    assert plan["blockers"] == []
    assert plan["selection_hash"] == "a" * 64
    assert plan["selection_status"] == "held_out_verified"
    assert plan["selection_evidence"]["manifest_source"] == "external"
    assert plan["historical_protocol"]["sample_id_manifest"] == "a" * 64
    assert plan["historical_protocol"]["sample_count"] == 60
    assert plan["runtime_evidence"] == {
        "transport": "evalscope_openai_api",
        "endpoint": "/v1/chat/completions",
        "cli_version": None,
        "app_version": None,
        "engine": None,
        "engine_version": None,
        "evalscope_version": "1.12.0",
    }
    assert plan["output_directory"].startswith("external-state://runs/")
    assert str(state) not in json.dumps(plan)


def test_core_execution_dispatches_to_evalscope_after_plan_gates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = tmp_path / "external-state"
    profile = {
        "suite": "core",
        "scorer_version": "family-manifest-pinned",
        "task_set": "private-frozen-core-v1",
    }
    config = _local_config()
    plan = _qualified_core_plan()
    observed: dict[str, object] = {}
    monkeypatch.setattr(runs, "build_execution_plan", lambda *args, **kwargs: plan)
    monkeypatch.setattr(runs, "get_state_dir", lambda *args, **kwargs: state)
    monkeypatch.setattr(runs, "load_suite", lambda *args, **kwargs: profile)
    monkeypatch.setattr(runs, "load_config", lambda *args, **kwargs: config)

    def execute_core(*args: object, **kwargs: object) -> dict[str, object]:
        observed.update(kwargs)
        return {
            "status": "completed",
            "runner": "evalscope-1.12",
            "model_alias": "racecraft-splash-local",
            "reasoning_mode": "off",
            "runtime_evidence": {
                "transport": "evalscope_openai_api",
                "endpoint": "/v1/chat/completions",
                "evalscope_version": "1.12.0",
            },
            "family_results": _qualified_family_results(),
        }

    monkeypatch.setattr(runs, "execute_evalscope_core", execute_core)
    monkeypatch.setattr(
        runs,
        "suite_tasks",
        lambda suite: (_ for _ in ()).throw(AssertionError("core must not use built-in tasks")),
    )

    result = runs.execute_run("core", "lmstudio-as-found", allow_expanded=True, root=tmp_path)

    assert result["status"] == "completed"
    manifest = result["manifest"]
    assert manifest["schema_version"] == 2
    assert manifest["suite"] == "core"
    assert manifest["task_families"] == [
        "gpqa_diamond",
        "ifeval",
        "mmlu_pro",
        "tool_json",
        "context",
    ]
    assert manifest["aggregate"]["planned"] == 60
    assert manifest["aggregate"]["attempted"] == 60
    assert manifest["aggregate"]["scorable"] == 60
    assert manifest["aggregate"]["correct"] == 55
    assert manifest["aggregate"]["incorrect"] == 5
    assert manifest["aggregate"]["failed"] == 0
    assert manifest["scorer_evidence"]["eligible_for_capability_report"] is True
    assert manifest["served_model_evidence"] == {
        "status": "verified_request_binding_without_response_identity",
        "requested_instance_id_sha256": "b" * 64,
        "response_instance_id_sha256": None,
        "match": None,
        "verification_basis": ("verified_local_exact_instance_and_evalscope_report_model_alias"),
    }
    assert manifest["reasoning_evidence"]["effective_status"] == ("transmitted_not_read_back")
    assert manifest["effective_settings_status"] == "transmitted_not_read_back"
    assert manifest["primary_objective_status_if_run"] == "answered_with_stated_scope"
    assert runs._CORE_EVIDENCE_LIMITATION in manifest["limitations"]
    assert "private_task_manifest" not in manifest
    assert "raw_response" not in json.dumps(manifest)
    persisted = json.loads(
        (state / "runs" / result["run_id"] / "manifest.json").read_text(encoding="utf-8")
    )
    assert persisted == manifest
    assert observed["repo"] == tmp_path
    assert observed["state"] == state
    assert observed["server_origin"] == "http://127.0.0.1:1234/v1"
    assert observed["reasoning_mode"] == "off"


@pytest.mark.parametrize("mode", ["missing_family", "wrong_count", "mock", "calibration"])
def test_core_execution_rejects_incomplete_or_noncapability_results(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str
) -> None:
    state = tmp_path / "external-state"
    plan = _qualified_core_plan()
    if mode == "mock":
        plan["evidence_class"] = "synthetic_mock"
    if mode == "calibration":
        plan["held_out"] = False
        plan["selection_status"] = "calibration"
    family_results = _qualified_family_results()
    if mode == "missing_family":
        family_results.pop()
    if mode == "wrong_count":
        family_results[0]["scored"] = 11
    monkeypatch.setattr(runs, "build_execution_plan", lambda *args, **kwargs: plan)
    monkeypatch.setattr(runs, "get_state_dir", lambda *args, **kwargs: state)
    monkeypatch.setattr(
        runs,
        "load_suite",
        lambda *args, **kwargs: {
            "suite": "core",
            "scorer_version": "family-manifest-pinned",
        },
    )
    monkeypatch.setattr(runs, "load_config", lambda *args, **kwargs: _local_config())
    monkeypatch.setattr(
        runs,
        "execute_evalscope_core",
        lambda *args, **kwargs: {
            "status": "completed",
            "model_alias": "racecraft-splash-local",
            "reasoning_mode": "off",
            "runtime_evidence": {
                "transport": "evalscope_openai_api",
                "endpoint": "/v1/chat/completions",
                "evalscope_version": "1.12.0",
            },
            "family_results": family_results,
        },
    )

    with pytest.raises(runs.RunError, match="core capability manifest"):
        runs.execute_run("core", "lmstudio-as-found", allow_expanded=True, root=tmp_path)
    assert not (state / "runs").exists()


def test_resume_and_rescore_explicitly_refuse_evalscope_core_semantics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest = {"status": "partial", "suite": "core", "runner": "evalscope-1.12"}
    monkeypatch.setattr(runs, "_run_dir", lambda *args, **kwargs: tmp_path)
    monkeypatch.setattr(runs, "load_run", lambda *args, **kwargs: (manifest, []))

    with pytest.raises(runs.ResumeRefused, match="EvalScope core resume is unavailable"):
        runs.resume_run("core-run", dry_run=True, root=tmp_path)
    with pytest.raises(runs.RunError, match="EvalScope core rescore is unavailable"):
        runs.rescore_run("core-run", "builtin-exact-v1", root=tmp_path)


def _swebench_base_plan(suite: str, *, fingerprint: str = "a" * 64) -> dict[str, object]:
    return {
        "schema_version": 1,
        "experiment_id": "swebench-experiment",
        "suite": suite,
        "config_id": "lmstudio-as-found",
        "evidence_class": (
            "runtime_scorer_qualification"
            if suite == "swebench-qualification"
            else "local_measurement"
        ),
        "model_id": "racecraft-splash-local",
        "model_is_splash": True,
        "locality_evidence": {
            "status": "verified_local",
            "endpoint_loopback": True,
            "local_instance_evidence": True,
        },
        "model_instance_evidence": {
            "selection": "exact_loaded_record",
            "splash_attribution": "confirmed",
            "instance_id_sha256": "b" * 64,
            "native_identity": {"loaded_instance_id_match": True},
        },
        "runner": "swebench-local-sandbox-v1",
        "blockers": [],
        "sample_count": 10 if suite == "swebench-qualification" else 500,
        "request_count": 10 if suite == "swebench-qualification" else 500,
        "protocol": {"suite": suite},
        "protocol_fingerprint": fingerprint,
        "selection_hash": "c" * 64,
        "selection_status": (
            "qualification" if suite == "swebench-qualification" else "held_out_verified"
        ),
        "selection_evidence": {
            "manifest_sha256": "c" * 64,
            "protocol_fingerprint": fingerprint,
            "frozen_before_tuning": suite == "swebench-verified",
        },
        "held_out": suite == "swebench-verified",
        "allow_expanded": True,
        "limitations": [],
        "approval_evidence": {
            "required": suite == "swebench-verified",
            "status": "verified" if suite == "swebench-verified" else "not_required",
            "approved_fingerprint": fingerprint if suite == "swebench-verified" else None,
        },
    }


def _swebench_result(mode: str, *, run_id: str = "swebench-run") -> dict[str, object]:
    count = 10 if mode == "qualification" else 500
    qualification = mode == "qualification"
    benchmark = BenchmarkResult(
        result_id=run_id,
        identity=BenchmarkIdentity(
            name="SWE-bench Verified",
            variant="qualification-disjoint" if qualification else "verified-500",
            adapter="mini-swe-agent-bash-docker",
            dataset_provider="princeton-nlp",
            dataset_id="SWE-bench_Verified",
            dataset_revision="c" * 64,
            evaluation_version="4.1.0",
            split="test",
            subset="disjoint-qualification" if qualification else "verified",
        ),
        status=BenchmarkStatus.QUALIFICATION if qualification else BenchmarkStatus.COMPLETE,
        counts=BenchmarkCounts(requested=count, succeeded=count, errored=0),
        task_outcomes=TaskOutcomes(
            resolved=count - 1, unresolved=1, model_failure=0, infrastructure_error=0
        ),
        metric_name="resolution_rate",
        metric_unit=MetricUnit.PROPORTION,
        score=None if qualification else (count - 1) / count,
        evaluator=BenchmarkEvaluator(
            name="SWE-bench official grader",
            version="4.1.0",
            developer="SWE-bench",
            model_label="racecraft-splash-local",
            evidence_class=EvaluatorClass.MEASURED_HERE,
        ),
        provenance=BenchmarkProvenance(
            source="synthetic unit test",
            artifacts={
                "protocol_fingerprint": "a" * 64,
                "manifest": "c" * 64,
                "runner_config": "d" * 64,
                "task_image": "e" * 64,
                "grader_image": "f" * 64,
            },
        ),
        limitations=("Synthetic unit test.",),
    )
    summary = {
        "schema_version": 1,
        "status": "completed",
        "evidence_class": (
            "runtime_scorer_qualification" if qualification else "held_out_capability"
        ),
        "task_count": count,
        "attempted_count": count,
        "completed_count": count,
        "resolved_count": count - 1,
        "unresolved_count": 1,
        "model_failure_count": 0,
        "infrastructure_error_count": 0,
        "error_count": 0,
        "resolution_rate": None if qualification else (count - 1) / count,
        "protocol_fingerprint": "a" * 64,
        "manifest_sha256": "c" * 64,
        "runner_config_sha256": "d" * 64,
        "task_image_digest": "sha256:" + "e" * 64,
        "grader_image_digest": "sha256:" + "f" * 64,
        "non_capability": qualification,
        "capability_claim_allowed": not qualification,
    }
    return {
        "status": "completed",
        "run_id": run_id,
        "mode": mode,
        "task_count": count,
        "protocol_fingerprint": "a" * 64,
        "manifest_sha256": "c" * 64,
        "benchmark_summary": summary,
        "benchmark_result": benchmark.model_dump(mode="json"),
        "benchmark_result_fingerprint_sha256": benchmark.fingerprint_sha256(),
    }


def _swebench_outcome_result(
    *,
    model_failure: int = 0,
    infrastructure_error: int = 0,
    attempted_count: int | None = None,
    completed: int = 499,
    resolved: int | None = None,
    unresolved: int | None = None,
) -> dict[str, object]:
    result = _swebench_result("verified")
    count = 500
    errors = model_failure + infrastructure_error
    resolved = completed - 1 if resolved is None else resolved
    unresolved = 1 if unresolved is None else unresolved
    complete = completed + errors == count and infrastructure_error < count
    summary = result["benchmark_summary"]
    assert isinstance(summary, dict)
    summary.update(
        status="completed" if complete else "partial",
        attempted_count=(completed + model_failure if attempted_count is None else attempted_count),
        completed_count=completed,
        resolved_count=resolved,
        unresolved_count=unresolved,
        model_failure_count=model_failure,
        infrastructure_error_count=infrastructure_error,
        error_count=errors,
        resolution_rate=resolved / count,
        capability_claim_allowed=complete,
    )
    benchmark = BenchmarkResult.model_validate(result["benchmark_result"]).model_copy(
        update={
            "status": BenchmarkStatus.COMPLETE if complete else BenchmarkStatus.PARTIAL,
            "counts": BenchmarkCounts(requested=count, succeeded=completed, errored=errors),
            "task_outcomes": TaskOutcomes(
                resolved=resolved,
                unresolved=unresolved,
                model_failure=model_failure,
                infrastructure_error=infrastructure_error,
            ),
            "score": resolved / count if complete else None,
        }
    )
    result.update(
        status="completed" if complete else "partial",
        benchmark_result=benchmark.model_dump(mode="json"),
        benchmark_result_fingerprint_sha256=benchmark.fingerprint_sha256(),
    )
    return result


def test_swebench_model_failures_stay_in_the_500_denominator_and_are_reviewed() -> None:
    plan = _swebench_base_plan("swebench-verified")
    result = _swebench_outcome_result(model_failure=1)

    manifest = runs._swebench_run_manifest(plan, result)

    assert manifest["benchmark_summary"]["resolution_rate"] == 498 / 500
    assert manifest["benchmark_summary"]["model_failure_count"] == 1
    assert manifest["benchmark_summary"]["infrastructure_error_count"] == 0
    assert manifest["capability_evidence"] is True
    assert manifest["publication_eligible"] is True
    assert manifest["aggregate"] == {
        **manifest["aggregate"],
        "planned": 500,
        "attempted": 500,
        "model_failure": 1,
        "infrastructure_error": 0,
        "score": 498 / 500,
    }


def test_swebench_infrastructure_errors_are_distinct_and_count_as_unresolved() -> None:
    plan = _swebench_base_plan("swebench-verified")
    result = _swebench_outcome_result(infrastructure_error=1)

    manifest = runs._swebench_run_manifest(plan, result)

    assert manifest["aggregate"]["model_failure"] == 0
    assert manifest["aggregate"]["infrastructure_error"] == 1
    assert manifest["aggregate"]["failed"] == 1
    assert manifest["aggregate"]["attempted"] == 499
    assert manifest["aggregate"]["unattempted"] == 1
    assert manifest["aggregate"]["score"] == 498 / 500
    assert manifest["capability_claim_allowed"] is True
    assert manifest["publication_eligible"] is True


def test_swebench_attempted_count_uses_verified_response_evidence() -> None:
    plan = _swebench_base_plan("swebench-verified")
    result = _swebench_outcome_result(
        completed=497,
        resolved=496,
        unresolved=1,
        model_failure=1,
        infrastructure_error=2,
        attempted_count=499,
    )

    manifest = runs._swebench_run_manifest(plan, result)

    assert manifest["aggregate"]["planned"] == 500
    assert manifest["aggregate"]["attempted"] == 499
    assert manifest["aggregate"]["unattempted"] == 1
    assert manifest["aggregate"]["model_failure"] == 1
    assert manifest["aggregate"]["infrastructure_error"] == 2
    assert manifest["aggregate"]["failed"] == 3
    assert manifest["aggregate"]["score"] == 496 / 500
    assert manifest["capability_claim_allowed"] is True


def test_swebench_all_model_failures_are_a_measured_zero_of_500() -> None:
    plan = _swebench_base_plan("swebench-verified")
    result = _swebench_outcome_result(completed=0, resolved=0, unresolved=0, model_failure=500)

    manifest = runs._swebench_run_manifest(plan, result)

    assert manifest["aggregate"]["score"] == 0.0
    assert manifest["aggregate"]["failed"] == 500
    assert manifest["aggregate"]["model_failure"] == 500
    assert manifest["aggregate"]["infrastructure_error"] == 0
    assert manifest["capability_evidence"] is True


def test_swebench_incomplete_accounting_is_rejected_before_manifest() -> None:
    plan = _swebench_base_plan("swebench-verified")
    result = _swebench_outcome_result(completed=499)

    with pytest.raises(runs.RunError, match="orchestration contract"):
        runs._swebench_run_manifest(plan, result)


def test_swebench_mismatched_summary_and_result_counts_are_rejected() -> None:
    plan = _swebench_base_plan("swebench-verified")
    result = _swebench_result("verified")
    summary = result["benchmark_summary"]
    assert isinstance(summary, dict)
    summary["resolved_count"] = 398
    summary["resolution_rate"] = 398 / 500

    with pytest.raises(runs.RunError, match="orchestration contract"):
        runs._swebench_run_manifest(plan, result)


def test_swebench_attempt_count_cannot_omit_verified_responses() -> None:
    plan = _swebench_base_plan("swebench-verified")
    result = _swebench_result("verified")
    summary = result["benchmark_summary"]
    assert isinstance(summary, dict)
    summary["attempted_count"] = 0

    with pytest.raises(runs.RunError, match="orchestration contract"):
        runs._swebench_run_manifest(plan, result)


def test_swebench_full_plan_requires_exact_approval_and_frozen_protocol(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    base = _swebench_base_plan("swebench-verified")
    base["blockers"] = [
        "No licensed/version-pinned executable task manifest is installed for this expanded suite."
    ]
    profile = {
        "suite": "swebench-verified",
        "approval_marker": "racecraft-swebench-verified-full-v1",
        "swebench": {
            "mode": "verified",
            "protocol_approval": {"approved": True, "approved_fingerprint": "a" * 64},
        },
    }
    readiness = {
        "status": "ready",
        "blockers": [],
        "metadata": {
            "runner": "swebench-local-sandbox-v1",
            "task_count": 500,
            "protocol_fingerprint": "a" * 64,
            "manifest_sha256": "c" * 64,
        },
    }
    monkeypatch.setattr(runs, "build_plan", lambda *args, **kwargs: dict(base))
    monkeypatch.setattr(runs, "load_suite", lambda *args, **kwargs: profile)
    monkeypatch.setattr(runs, "load_config", lambda *args, **kwargs: _local_config())
    monkeypatch.setattr(
        runs,
        "_suite_plan_metadata",
        lambda *args, **kwargs: {
            "runner": "swebench-local-sandbox-v1",
            "swebench_readiness": readiness,
            "sample_count": 500,
            "blockers": [],
            "output_directory": "external-state://swebench/runs",
            "selection_hash": "c" * 64,
        },
    )

    blocked = runs.build_execution_plan(
        "swebench-verified", "lmstudio-as-found", allow_expanded=True, root=tmp_path
    )
    ready = runs.build_execution_plan(
        "swebench-verified",
        "lmstudio-as-found",
        allow_expanded=True,
        root=tmp_path,
        swebench_approval_marker="racecraft-swebench-verified-full-v1",
    )

    assert "full_run_budget_approval_missing" in blocked["blockers"]
    assert ready["blockers"] == []
    assert ready["held_out"] is True
    assert ready["approval_evidence"]["status"] == "verified"


def test_swebench_public_profiles_are_registered_and_fail_closed() -> None:
    repo = Path(__file__).resolve().parents[2]
    qualification = runs.load_suite("swebench-qualification", repo)
    verified = runs.load_suite("swebench-verified", repo)

    assert qualification["expanded"] is True
    assert qualification["swebench"]["mode"] == "qualification"
    # The frozen continuation cohort holds the three never-attempted tasks.
    assert qualification["swebench"]["task_count"] == 3
    assert qualification["swebench"]["protocol_approval"]["approved"] is False
    assert verified["expanded"] is True
    assert verified["approval_marker"] == "racecraft-swebench-verified-full-v1"
    assert verified["swebench"]["mode"] == "verified"
    assert verified["swebench"]["task_count"] == 500
    # Operator-approved 2026-09-25 for exactly this protocol fingerprint.
    assert verified["swebench"]["protocol_approval"] == {
        "approved": True,
        "approved_fingerprint": (
            "59f9d524fd9e40c786cd282e95d2a87b4f4ab7051e8bcb273eda3b6c02cfb724"
        ),
    }
    # The verified profile binds the served identity observed during qualification.
    assert verified["swebench"]["model_runtime"]["served_model_fingerprint"] == (
        "dea3084a7d67b3808fc26e4166cc51f4e85be87bad0b2040b91ec0c3a7c6eee0"
    )


def test_swebench_qualification_execution_is_always_non_capability(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = tmp_path / "external-state"
    plan = _swebench_base_plan("swebench-qualification")
    profile = {"suite": "swebench-qualification", "swebench": {"mode": "qualification"}}
    api = type(
        "Api",
        (),
        {
            "execute_swebench": staticmethod(
                lambda *args, **kwargs: _swebench_result("qualification")
            )
        },
    )
    monkeypatch.setattr(runs, "build_execution_plan", lambda *args, **kwargs: plan)
    monkeypatch.setattr(runs, "get_state_dir", lambda *args, **kwargs: state)
    monkeypatch.setattr(runs, "load_suite", lambda *args, **kwargs: profile)
    monkeypatch.setattr(runs, "load_config", lambda *args, **kwargs: _local_config())
    monkeypatch.setattr(runs, "_swebench_api", lambda: api)

    result = runs.execute_run(
        "swebench-qualification", "lmstudio-as-found", allow_expanded=True, root=tmp_path
    )

    manifest = result["manifest"]
    assert manifest["held_out"] is False
    assert manifest["non_capability"] is True
    assert manifest["capability_claim_allowed"] is False
    assert manifest["capability_evidence"] is False
    assert manifest["publication_eligible"] is False
    assert manifest["aggregate"]["score"] is None
    assert manifest["primary_objective_status_if_run"] == "pilot_only"
    assert (state / "runs" / "swebench-run" / "manifest.json").is_file()


def test_swebench_resume_requires_fresh_full_approval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = tmp_path / "external-state"
    manifest = {
        **_swebench_base_plan("swebench-verified"),
        "status": "partial",
        "run_id": "swebench-run",
    }
    observed: dict[str, object] = {}

    class Api:
        @staticmethod
        def resume_swebench(*args, **kwargs):
            observed["resume"] = kwargs
            return _swebench_result("verified")

    monkeypatch.setattr(runs, "_run_dir", lambda *args, **kwargs: state / "runs/swebench-run")
    monkeypatch.setattr(runs, "load_run", lambda *args, **kwargs: (manifest, []))
    monkeypatch.setattr(runs, "get_state_dir", lambda *args, **kwargs: state)
    monkeypatch.setattr(
        runs,
        "load_suite",
        lambda *args, **kwargs: {
            "suite": "swebench-verified",
            "approval_marker": "racecraft-swebench-verified-full-v1",
            "swebench": {"mode": "verified"},
        },
    )
    monkeypatch.setattr(runs, "load_config", lambda *args, **kwargs: _local_config())
    monkeypatch.setattr(runs, "_swebench_api", lambda: Api)
    monkeypatch.setattr(runs, "_persist_swebench_manifest", lambda *args, **kwargs: None)

    with pytest.raises(runs.ResumeRefused, match="approval marker"):
        runs.resume_run("swebench-run", dry_run=False, root=tmp_path)
    resumed = runs.resume_run(
        "swebench-run",
        dry_run=False,
        root=tmp_path,
        swebench_approval_marker="racecraft-swebench-verified-full-v1",
    )

    assert resumed["status"] == "completed"
    assert observed["resume"]["approval_marker"] == "racecraft-swebench-verified-full-v1"


def test_swebench_rescore_returns_report_without_changing_source_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = tmp_path / "external-state"
    source_result = _swebench_result("verified")
    manifest = runs._swebench_run_manifest(_swebench_base_plan("swebench-verified"), source_result)
    manifest_before = json.loads(json.dumps(manifest))
    run_id = str(manifest["run_id"])
    run_dir = state / "runs" / run_id
    run_dir.mkdir(parents=True)
    original_files = {
        "manifest.json": json.dumps(manifest, sort_keys=True).encode("utf-8"),
        "checkpoint-0000.json": b'{"status":"completed","grade":"unresolved"}',
        "result.json": b'{"aggregate":{"resolved":0,"planned":500}}',
        "aggregate.json": b'{"resolved":0,"planned":500}',
    }
    for name, content in original_files.items():
        (run_dir / name).write_bytes(content)
    before = {name: (run_dir / name).read_bytes() for name in original_files}
    report = {
        "schema_version": 1,
        "report_id": "a" * 64,
        "report_sha256": "b" * 64,
        "run_id": run_id,
        "protocol_fingerprint": manifest["protocol_fingerprint"],
        "manifest_sha256": manifest["selection_hash"],
        "task_count": manifest["sample_count"],
        "rescored_count": manifest["sample_count"],
        "tasks": [
            {"task_index": index, "rescored": True} for index in range(manifest["sample_count"])
        ],
    }
    persisted_manifests: list[dict[str, object]] = []

    class Api:
        @staticmethod
        def rescore_swebench(*args, **kwargs):
            return report

    monkeypatch.setattr(runs, "_run_dir", lambda *args, **kwargs: run_dir)
    monkeypatch.setattr(runs, "load_run", lambda *args, **kwargs: (manifest, []))
    monkeypatch.setattr(runs, "get_state_dir", lambda *args, **kwargs: state)
    monkeypatch.setattr(
        runs,
        "load_suite",
        lambda *args, **kwargs: {
            "suite": "swebench-verified",
            "approval_marker": "racecraft-swebench-verified-full-v1",
            "swebench": {"mode": "verified"},
        },
    )
    monkeypatch.setattr(runs, "load_config", lambda *args, **kwargs: _local_config())
    monkeypatch.setattr(runs, "_swebench_api", lambda: Api)
    monkeypatch.setattr(
        runs,
        "_persist_swebench_manifest",
        lambda _state, value: persisted_manifests.append(value),
    )

    rescored = runs.rescore_run(run_id, "swebench-grader-pinned", root=tmp_path)

    assert rescored["status"] == "rescored"
    assert rescored["scorer_version"] == "swebench-grader-pinned"
    assert {key: rescored[key] for key in report} == report
    assert rescored["report_id"] == report["report_id"]
    assert rescored["report_sha256"] == report["report_sha256"]
    assert rescored["tasks"] == report["tasks"]
    assert "manifest" not in rescored
    assert persisted_manifests == []
    assert manifest == manifest_before
    assert {name: (run_dir / name).read_bytes() for name in original_files} == before


def test_swebench_rescore_rejects_non_pinned_scorer_before_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest = {"suite": "swebench-verified"}
    scorer_called = False

    class Api:
        @staticmethod
        def rescore_swebench(*args, **kwargs):
            nonlocal scorer_called
            scorer_called = True
            raise AssertionError("a non-pinned scorer must be rejected before scoring")

    monkeypatch.setattr(runs, "_run_dir", lambda *args, **kwargs: tmp_path)
    monkeypatch.setattr(runs, "load_run", lambda *args, **kwargs: (manifest, []))
    monkeypatch.setattr(runs, "_swebench_api", lambda: Api)

    with pytest.raises(runs.RunError, match="requires the frozen grader"):
        runs.rescore_run("swebench-run", "unreviewed-grader-v2", root=tmp_path)

    assert scorer_called is False


def test_swebench_rescore_rejects_inconsistent_source_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest = runs._swebench_run_manifest(
        _swebench_base_plan("swebench-verified"), _swebench_result("verified")
    )
    aggregate = manifest["aggregate"]
    assert isinstance(aggregate, dict)
    aggregate["score"] = 0.01
    scorer_called = False

    class Api:
        @staticmethod
        def rescore_swebench(*args, **kwargs):
            nonlocal scorer_called
            scorer_called = True
            raise AssertionError("an inconsistent source manifest must fail before rescoring")

    monkeypatch.setattr(runs, "_swebench_api", lambda: Api)

    with pytest.raises(runs.RunError, match="source manifest failed validation"):
        runs._rescore_swebench_run(
            str(manifest["run_id"]), "swebench-grader-pinned", manifest, tmp_path
        )

    assert scorer_called is False


def test_swebench_run_that_raises_leaves_a_resumable_outer_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = tmp_path / "external-state"
    plan = _swebench_base_plan("swebench-verified")

    class TransportStopped(Exception):
        pass

    class Api:
        @staticmethod
        def execute_swebench(*args, **kwargs):
            kwargs["on_run_created"]("swebench-run")
            raise TransportStopped

    monkeypatch.setattr(runs, "_swebench_api", lambda: Api)
    with pytest.raises(TransportStopped):
        runs._execute_swebench_run(
            tmp_path, state, plan, _local_config(), {"swebench": {}}, "marker"
        )

    manifest = json.loads((state / "runs/swebench-run/manifest.json").read_text())
    assert manifest["run_id"] == "swebench-run"
    assert manifest["status"] == "blocked"
    assert manifest["protocol_fingerprint"] == plan["protocol_fingerprint"]
