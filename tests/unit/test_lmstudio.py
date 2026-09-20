from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from local_evals.lmstudio import (
    CommandResult,
    LMStudioClient,
    LMStudioError,
    LMStudioRedirectError,
    _extract_models,
    chat_once,
    classify_model_instance_locality,
    discover,
    model_keys_equivalent,
    parse_usage,
    verify_lms_cli_executable,
)
from local_evals.models import (
    LocalityEvidence,
    LocalityStatus,
    ModelRecordSource,
    RequestBudgetLimits,
)
from local_evals.preflight import LocalityError, RequestBudget


def _verified_locality() -> LocalityEvidence:
    return LocalityEvidence(
        status=LocalityStatus.VERIFIED_LOCAL,
        endpoint_loopback=True,
        lm_link_state="disabled",
        local_instance_evidence=True,
        execution_device="synthetic-local-device",
    )


def _write_cli(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(0o700)
    return path


def test_macos_cli_guard_accepts_exact_lm_studio_match(tmp_path: Path) -> None:
    bundled_lm_studio = _write_cli(tmp_path / "lm-studio-lfs", b"lm-studio-cli")
    bundled_bionic = _write_cli(tmp_path / "bionic-lfs", b"bionic-cli")
    cached_target = _write_cli(tmp_path / "cached-target", b"lm-studio-cli")
    cached_link = tmp_path / "lms"
    cached_link.symlink_to(cached_target)

    resolved = verify_lms_cli_executable(
        cached_link,
        platform_name="darwin",
        lm_studio_paths=(bundled_lm_studio,),
        bionic_paths=(bundled_bionic,),
    )

    assert resolved == cached_target.resolve()


def test_macos_cli_guard_accepts_official_llmster_home_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_home = tmp_path / "home"
    user_home.mkdir()
    lm_studio_home = tmp_path / "lm-studio-home"
    versioned_cli = _write_cli(
        lm_studio_home / "llmster" / "0.0.25-1" / "llmster",
        b"official-llmster-cli",
    )
    cached_cli = lm_studio_home / "bin" / "lms"
    cached_cli.parent.mkdir()
    cached_cli.symlink_to(versioned_cli)
    (user_home / ".lmstudio-home-pointer").write_text(str(lm_studio_home), encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: user_home)

    resolved = verify_lms_cli_executable(
        cached_cli,
        platform_name="darwin",
        lm_studio_paths=(),
        bionic_paths=(),
    )

    assert resolved == versioned_cli.resolve()


def test_macos_cli_guard_refuses_same_bytes_outside_official_llmster_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_home = tmp_path / "home"
    user_home.mkdir()
    official_cli = _write_cli(
        user_home / ".lmstudio" / "bin" / "lms",
        b"official-llmster-cli",
    )
    spoof = _write_cli(tmp_path / "path-spoof-lms", official_cli.read_bytes())
    monkeypatch.setattr(Path, "home", lambda: user_home)

    with pytest.raises(LMStudioError, match="does not match"):
        verify_lms_cli_executable(
            spoof,
            platform_name="darwin",
            lm_studio_paths=(),
            bionic_paths=(),
        )


def test_macos_cli_guard_refuses_bionic_match_without_disclosing_details(
    tmp_path: Path,
) -> None:
    bundled_lm_studio = _write_cli(tmp_path / "lm-studio-lfs", b"lm-studio-cli")
    bundled_bionic = _write_cli(tmp_path / "bionic-lfs", b"bionic-cli")
    cached = _write_cli(tmp_path / "cached-lfs", b"bionic-cli")

    with pytest.raises(LMStudioError, match="conflicting Bionic CLI") as raised:
        verify_lms_cli_executable(
            cached,
            platform_name="darwin",
            lm_studio_paths=(bundled_lm_studio,),
            bionic_paths=(bundled_bionic,),
        )

    assert str(tmp_path) not in str(raised.value)
    assert "sha256" not in str(raised.value).casefold()


def test_macos_cli_guard_refuses_unknown_mismatch_without_disclosing_details(
    tmp_path: Path,
) -> None:
    bundled_lm_studio = _write_cli(tmp_path / "lm-studio-lfs", b"lm-studio-cli")
    bundled_bionic = _write_cli(tmp_path / "bionic-lfs", b"bionic-cli")
    cached = _write_cli(tmp_path / "cached-lfs", b"unknown-cli")

    with pytest.raises(LMStudioError, match="does not match") as raised:
        verify_lms_cli_executable(
            cached,
            platform_name="darwin",
            lm_studio_paths=(bundled_lm_studio,),
            bionic_paths=(bundled_bionic,),
        )

    assert str(tmp_path) not in str(raised.value)
    assert "sha256" not in str(raised.value).casefold()


def test_missing_usage_fields_remain_null() -> None:
    usage = parse_usage({"choices": []})

    assert usage.prompt_tokens is None
    assert usage.completion_tokens is None
    assert usage.total_tokens is None


def test_explicit_zero_usage_is_distinct_from_missing_usage() -> None:
    usage = parse_usage({"usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}})

    assert usage.prompt_tokens == 0
    assert usage.completion_tokens == 0
    assert usage.total_tokens == 0


def test_null_device_identifier_marks_selected_instance_local() -> None:
    payload = {
        "localDeviceIdentifier": "local-device",
        "models": [
            {
                "modelKey": "synthetic-model",
                "instanceIdentifier": "synthetic-instance",
                "deviceIdentifier": None,
            }
        ],
    }

    locality = classify_model_instance_locality(
        payload,
        model_or_instance_id="synthetic-instance",
        lm_link_state="disabled",
    )

    assert locality.status is LocalityStatus.VERIFIED_LOCAL
    assert locality.local_instance_evidence is True


def test_matching_non_null_device_identifier_is_not_local() -> None:
    payload = {
        "localDeviceIdentifier": "local-device",
        "models": [
            {
                "modelKey": "synthetic-model",
                "instanceIdentifier": "synthetic-instance",
                "deviceIdentifier": "local-device",
            }
        ],
    }

    locality = classify_model_instance_locality(
        payload,
        model_or_instance_id="synthetic-instance",
        lm_link_state="connected",
    )

    assert locality.status is LocalityStatus.AMBIGUOUS_LM_LINK
    assert locality.local_instance_evidence is False


def test_lms_peer_device_is_not_local() -> None:
    payload = {
        "localDeviceIdentifier": "local-device",
        "models": [
            {
                "modelKey": "synthetic-model",
                "instanceIdentifier": "synthetic-instance",
                "deviceIdentifier": "linked-peer-device",
            }
        ],
    }

    locality = classify_model_instance_locality(
        payload,
        model_or_instance_id="synthetic-instance",
        lm_link_state="connected",
    )

    assert locality.status is LocalityStatus.AMBIGUOUS_LM_LINK
    assert locality.local_instance_evidence is False
    assert locality.execution_device == "linked-peer"


def test_multiple_loaded_instances_without_target_are_ambiguous() -> None:
    payload = {
        "localDeviceIdentifier": "local-device",
        "models": [
            {"modelKey": "synthetic-a", "deviceIdentifier": None},
            {"modelKey": "synthetic-b", "deviceIdentifier": "linked-peer-device"},
        ],
    }

    locality = classify_model_instance_locality(
        payload,
        model_or_instance_id=None,
        lm_link_state="unknown",
    )

    assert locality.status is LocalityStatus.AMBIGUOUS_LM_LINK
    assert locality.local_instance_evidence is False


def test_current_lms_identifier_field_selects_local_instance() -> None:
    payload = [
        {
            "modelKey": "qwen3.8-27b-splash",  # gitleaks:allow - synthetic fixture key
            "identifier": "racecraft-eval-splash",
            "deviceIdentifier": None,
        },
        {
            "modelKey": "synthetic-linked-model",
            "identifier": "synthetic-linked-instance",
            "deviceIdentifier": "linked-peer-device",
        },
    ]

    locality = classify_model_instance_locality(
        payload,
        model_or_instance_id="racecraft-eval-splash",
        lm_link_state="connected",
    )

    assert locality.status is LocalityStatus.VERIFIED_LOCAL
    assert locality.local_instance_evidence is True
    assert locality.execution_device == "local"


def test_current_llmster_omitted_device_is_corroborated_by_unique_local_download() -> None:
    loaded = [
        {
            "modelKey": "qwen3.8-27b-splash",  # gitleaks:allow - fixture key
            "identifier": "racecraft-splash-local",
        }
    ]
    downloaded = {
        "models": [
            {
                "modelKey": "qwen3.8-27b-splash",  # gitleaks:allow - fixture key
                "deviceIdentifier": None,
            }
        ]
    }

    locality = classify_model_instance_locality(
        loaded,
        model_or_instance_id="racecraft-splash-local",
        lm_link_state="connected",
        downloaded_payload=downloaded,
    )

    assert locality.status is LocalityStatus.VERIFIED_LOCAL
    assert locality.local_instance_evidence is True
    assert locality.execution_device == "local"


def test_current_llmster_remote_download_cannot_corroborate_locality() -> None:
    loaded = [
        {
            "modelKey": "qwen3.8-27b-splash",  # gitleaks:allow - fixture key
            "identifier": "racecraft-splash-local",
        }
    ]
    downloaded = [
        {
            "modelKey": "qwen3.8-27b-splash",  # gitleaks:allow - fixture key
            "deviceIdentifier": "linked-peer-device",
        }
    ]

    locality = classify_model_instance_locality(
        loaded,
        model_or_instance_id="racecraft-splash-local",
        lm_link_state="connected",
        downloaded_payload=downloaded,
    )

    assert locality.status is LocalityStatus.AMBIGUOUS_LM_LINK
    assert locality.local_instance_evidence is False
    assert locality.execution_device == "linked-peer"


def test_current_llmster_duplicate_download_matches_are_ambiguous() -> None:
    loaded = [
        {
            "modelKey": "qwen3.8-27b-splash",  # gitleaks:allow - fixture key
            "identifier": "racecraft-splash-local",
        }
    ]
    downloaded = [
        {
            "modelKey": "qwen3.8-27b-splash",  # gitleaks:allow - fixture key
            "deviceIdentifier": None,
        },
        {
            "modelKey": "qwen3.8-27b-splash",  # gitleaks:allow - fixture key
            "deviceIdentifier": "linked-peer-device",
        },
    ]

    locality = classify_model_instance_locality(
        loaded,
        model_or_instance_id="racecraft-splash-local",
        lm_link_state="connected",
        downloaded_payload=downloaded,
    )

    assert locality.status is LocalityStatus.AMBIGUOUS_LM_LINK
    assert locality.local_instance_evidence is False


@pytest.mark.parametrize(
    "downloaded",
    [
        None,
        [{"modelKey": "different-model", "deviceIdentifier": None}],
        [{"modelKey": "qwen3.8-27b-splash"}],  # gitleaks:allow - fixture key
        [  # gitleaks:allow - fixture key
            {
                "modelKey": "qwen3.8-27b-splash",  # gitleaks:allow - fixture key
                "deviceIdentifier": 42,
            }
        ],
        [  # gitleaks:allow - fixture key
            {
                "modelKey": "QWEN3.8-27B-SPLASH",  # gitleaks:allow - fixture key
                "deviceIdentifier": None,
            }
        ],
        [{"modelKey": "incoai/qwen3.8-27b-splash", "deviceIdentifier": None}],
    ],
)
def test_current_llmster_uncorroborated_download_is_ambiguous(
    downloaded: object,
) -> None:
    loaded = [
        {
            "modelKey": "qwen3.8-27b-splash",  # gitleaks:allow - fixture key
            "identifier": "racecraft-splash-local",
        }
    ]

    locality = classify_model_instance_locality(
        loaded,
        model_or_instance_id="racecraft-splash-local",
        lm_link_state="connected",
        downloaded_payload=downloaded,
    )

    assert locality.status is LocalityStatus.AMBIGUOUS_LM_LINK
    assert locality.local_instance_evidence is False


def test_current_llmster_loaded_remote_device_cannot_be_overridden() -> None:
    loaded = [
        {
            "modelKey": "qwen3.8-27b-splash",  # gitleaks:allow - fixture key
            "identifier": "racecraft-splash-local",
            "deviceIdentifier": "linked-peer-device",
        }
    ]
    downloaded = [
        {
            "modelKey": "qwen3.8-27b-splash",  # gitleaks:allow - fixture key
            "deviceIdentifier": None,
        }
    ]

    locality = classify_model_instance_locality(
        loaded,
        model_or_instance_id="racecraft-splash-local",
        lm_link_state="connected",
        downloaded_payload=downloaded,
    )

    assert locality.status is LocalityStatus.AMBIGUOUS_LM_LINK
    assert locality.local_instance_evidence is False


def test_current_llmster_loaded_instance_without_model_key_is_ambiguous() -> None:
    loaded = [{"identifier": "racecraft-splash-local"}]
    downloaded = [{"identifier": "racecraft-splash-local", "deviceIdentifier": None}]

    locality = classify_model_instance_locality(
        loaded,
        model_or_instance_id="racecraft-splash-local",
        lm_link_state="connected",
        downloaded_payload=downloaded,
    )

    assert locality.status is LocalityStatus.AMBIGUOUS_LM_LINK
    assert locality.local_instance_evidence is False


def test_discover_correlates_current_llmster_payloads_without_leaking_devices() -> None:
    downloaded = [
        {
            "modelKey": "qwen3.8-27b-splash",  # gitleaks:allow - fixture key
            "identifier": "racecraft-splash-local",
            "deviceIdentifier": None,
        },
        {
            "modelKey": "remote-embedding",
            "identifier": "remote-embedding",
            "deviceIdentifier": "hal-private-device",
        },
    ]
    loaded = [
        {
            "modelKey": "qwen3.8-27b-splash",  # gitleaks:allow - fixture key
            "identifier": "racecraft-splash-local",
        },
        {
            "modelKey": "remote-embedding",
            "identifier": "remote-embedding",
            "deviceIdentifier": "hal-private-device",
        },
    ]
    commands = {
        ("--version",): "CLI commit: synthetic",
        ("ls", "--json"): json.dumps(downloaded),
        ("ps", "--json"): json.dumps(loaded),
        ("runtime", "ls"): "synthetic-runtime",
        ("server", "status"): "running",
        ("link", "status"): "connected",
    }

    def runner(args: tuple[str, ...]) -> CommandResult:
        return CommandResult(returncode=0, stdout=commands[args], stderr="")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/models":
            return httpx.Response(200, json={"models": []})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(404)

    report = discover(
        selected_model="racecraft-splash-local",
        runner=runner,
        transport=httpx.MockTransport(handler),
    )

    assert report.locality.status is LocalityStatus.VERIFIED_LOCAL
    serialized = repr(report.model_dump(mode="json"))
    assert "hal-private-device" not in serialized


def test_enabled_link_does_not_override_selected_local_instance_evidence() -> None:
    payload = {
        "models": [
            {
                "modelKey": "qwen3.8-27b-splash",  # gitleaks:allow - fixture key
                "identifier": "racecraft-splash-local",
                "deviceIdentifier": None,
            },
            {
                "modelKey": "hal/remote-model",
                "indexedModelIdentifier": "hal/remote-model",
                "identifier": "hal-remote-instance",
                "deviceIdentifier": "hal-device",
            },
        ]
    }

    locality = classify_model_instance_locality(
        payload,
        model_or_instance_id="racecraft-splash-local",
        lm_link_state="connected",
    )

    assert locality.status is LocalityStatus.VERIFIED_LOCAL
    assert locality.local_instance_evidence is True
    assert locality.execution_device == "local"


def test_enabled_link_selected_remote_instance_is_ambiguous() -> None:
    payload = {
        "models": [
            {
                "modelKey": "qwen3.8-27b-splash",  # gitleaks:allow - fixture key
                "identifier": "racecraft-splash-local",
                "deviceIdentifier": None,
            },
            {
                "modelKey": "hal/remote-model",
                "indexedModelIdentifier": "hal/remote-model",
                "identifier": "hal-remote-instance",
                "deviceIdentifier": "hal-device",
            },
        ]
    }

    locality = classify_model_instance_locality(
        payload,
        model_or_instance_id="hal-remote-instance",
        lm_link_state="connected",
    )

    assert locality.status is LocalityStatus.AMBIGUOUS_LM_LINK
    assert locality.local_instance_evidence is False


def test_single_loaded_instance_without_explicit_selection_is_ambiguous() -> None:
    payload = {
        "models": [
            {
                "modelKey": "qwen3.8-27b-splash",  # gitleaks:allow - fixture key
                "identifier": "racecraft-splash-local",
                "deviceIdentifier": None,
            }
        ]
    }

    locality = classify_model_instance_locality(
        payload,
        model_or_instance_id=None,
        lm_link_state="connected",
    )

    assert locality.status is LocalityStatus.AMBIGUOUS_LM_LINK
    assert locality.local_instance_evidence is False


def test_model_key_alias_cannot_select_loaded_instance() -> None:
    payload = {
        "models": [
            {
                "modelKey": "qwen3.8-27b-splash",  # gitleaks:allow - fixture key
                "identifier": "racecraft-splash-local",
                "deviceIdentifier": None,
            }
        ]
    }

    locality = classify_model_instance_locality(
        payload,
        model_or_instance_id="qwen3.8-27b-splash",  # gitleaks:allow - fixture key
        lm_link_state="connected",
    )

    assert locality.status is LocalityStatus.AMBIGUOUS_LM_LINK
    assert locality.local_instance_evidence is False


def test_native_identity_fields_are_retained_without_paths_or_devices() -> None:
    cli_loaded = [
        {
            "modelKey": "qwen/qwen3.8-27b-splash",
            "identifier": "racecraft-eval-splash",
            "deviceIdentifier": "private-device-id",
            "path": "/private/example/model.gguf",
        }
    ]
    native = {
        "models": [
            {
                "type": "llm",
                "publisher": "qwen",
                "key": "qwen/qwen3.8-27b-splash",
                "display_name": "Qwen3.8 27B Splash",
                "architecture": "qwen3_8",
                "format": "mlx",
                "quantization": {"name": "4bit", "bits_per_weight": 4},
                "selected_variant": "qwen/qwen3.8-27b-splash@4bit",
                "loaded_instances": [{"id": "racecraft-eval-splash", "config": {}}],
                "capabilities": {
                    "reasoning": {
                        "allowed_options": ["off", "low", "medium", "high", "on"],
                        "default": "on",
                    }
                },
            }
        ]
    }

    records = _extract_models(
        (cli_loaded, True, ModelRecordSource.LMS_CLI_LOADED),
        (native, False, ModelRecordSource.NATIVE_REST),
    )

    native_record = next(item for item in records if item.source is ModelRecordSource.NATIVE_REST)
    assert native_record.display_name == "Qwen3.8 27B Splash"
    assert native_record.publisher == "qwen"
    assert native_record.architecture == "qwen3_8"
    assert native_record.model_format == "mlx"
    assert native_record.quantization == "4bit"
    assert native_record.loaded_instance_ids == ("racecraft-eval-splash",)
    assert native_record.reasoning_allowed == ("off", "low", "medium", "high", "on")
    assert native_record.reasoning_default == "on"
    serialized = repr([item.model_dump(mode="json") for item in records])
    assert "private-device-id" not in serialized
    assert "/private/example" not in serialized


def test_model_key_equivalence_allows_only_exact_publisher_qualification() -> None:
    assert model_keys_equivalent("qwen3.8-27b-splash", "qwen/qwen3.8-27b-splash", "qwen")
    assert not model_keys_equivalent("splash", "qwen/qwen3.8-27b-splash", "qwen")
    assert not model_keys_equivalent("other/qwen3.8-27b-splash", "qwen3.8-27b-splash", "qwen")


def test_client_refuses_redirect_even_when_target_is_loopback() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(307, headers={"location": "http://127.0.0.1:1234/elsewhere"})

    with LMStudioClient("http://127.0.0.1:1234", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(LMStudioRedirectError, match="not followed"):
            client.get_json("/api/v1/models")


def test_client_identifies_remote_redirect_without_following_it() -> None:
    called = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called += 1
        return httpx.Response(302, headers={"location": "http://192.0.2.20:8080/remote"})

    with LMStudioClient("http://127.0.0.1:1234", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(LMStudioRedirectError, match="non-local redirect"):
            client.get_json("/api/v1/models")

    assert called == 1


def test_chat_once_blocks_uncertain_lm_link_before_transport() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    locality = LocalityEvidence(
        status=LocalityStatus.AMBIGUOUS_LM_LINK,
        endpoint_loopback=True,
        lm_link_state="unknown",
        local_instance_evidence=False,
    )

    with pytest.raises(LocalityError):
        chat_once(
            "http://127.0.0.1:1234",
            "synthetic-model",
            [{"role": "user", "content": "synthetic"}],
            {"max_tokens": 8},
            locality=locality,
            transport=httpx.MockTransport(handler),
        )

    assert called is False


def test_chat_once_uses_native_v1_and_pessimistically_charges_missing_usage() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.read().decode("utf-8")
        return httpx.Response(
            200,
            json={
                "id": "synthetic-response",
                "model_instance_id": "synthetic-instance",
                "output": [{"type": "message", "content": "synthetic answer"}],
            },
        )

    budget = RequestBudget(RequestBudgetLimits(max_generated_tokens=16, max_live_requests=1))
    response = chat_once(
        "http://127.0.0.1:1234",
        "synthetic-instance",
        [{"role": "user", "content": "synthetic prompt"}],
        {
            "max_output_tokens": 16,
            "temperature": 0.0,
            "top_p": 0.9,
            "top_k": 20,
            "reasoning": "on",
        },
        locality=_verified_locality(),
        budget=budget,
        transport=httpx.MockTransport(handler),
    )

    assert response["id"] == "synthetic-response"
    assert seen["url"] == "http://127.0.0.1:1234/api/v1/chat"
    assert '"store":false' in str(seen["body"])
    assert '"input":"synthetic prompt"' in str(seen["body"])
    assert '"reasoning":"on"' in str(seen["body"])
    assert '"max_output_tokens":16' in str(seen["body"])
    assert budget.usage.generated_tokens == 16
    assert budget.usage.live_requests == 1


def test_chat_once_refuses_history_before_transport() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    with pytest.raises(LMStudioError, match="exactly one user text input"):
        chat_once(
            "http://127.0.0.1:1234",
            "synthetic-instance",
            [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "prompt"},
            ],
            {"max_output_tokens": 8, "reasoning": "on"},
            locality=_verified_locality(),
            transport=httpx.MockTransport(handler),
        )

    assert called is False


@pytest.mark.parametrize("served", [None, "other-instance"])
def test_chat_once_refuses_missing_or_mismatched_served_instance(served: str | None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = {"output": [{"type": "message", "content": "answer"}], "stats": {}}
        if served is not None:
            payload["model_instance_id"] = served
        return httpx.Response(200, json=payload)

    with pytest.raises(LMStudioError, match="served model instance"):
        chat_once(
            "http://127.0.0.1:1234",
            "synthetic-instance",
            [{"role": "user", "content": "prompt"}],
            {"max_output_tokens": 8, "reasoning": "on"},
            locality=_verified_locality(),
            transport=httpx.MockTransport(handler),
        )
