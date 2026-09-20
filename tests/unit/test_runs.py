from __future__ import annotations

from pathlib import Path

import pytest

import local_evals.runs as runs
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
            "api_key_env": "LM_STUDIO_API_KEY",
        },
        "model": {"key": "splash"},
        "operation_requested": {"max_tokens": 64},
        "reasoning_control": {
            "desired_mode": "on",
            "transmitted": "on",
        },
    }


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
