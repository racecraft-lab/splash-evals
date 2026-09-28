from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from local_evals.config import ConfigurationError
from local_evals.swebench import (
    _ADAPTER_KEYS,
    _APPROVED_LOCAL_PARAMETERS,
    SwebenchError,
    _canonical,
    _sha256,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import capture_swebench_runtime_identity as capture  # noqa: E402
from capture_swebench_runtime_identity import main as capture_main  # noqa: E402

_CONTROL_DIGEST = "sha256:" + "d" * 64
_CONTROL_REFERENCE = f"local/swebench-control@{_CONTROL_DIGEST}"


def _qualification_profile() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "mode": "qualification",
        "manifest_path": "swebench/qualification/manifest.json",
        "manifest_sha256": "1" * 64,
        "mini_swe_agent_version": "2.4.6",
        "mini_swe_agent_wheel_sha256": "2" * 64,
        "swebench_version": "5.0.2",
        "swebench_wheel_sha256": "3" * 64,
        "runner_config_sha256": "4" * 64,
        "model": "racecraft-splash-local",
        "model_runtime": {
            "adapter_revision_sha256": None,
            "served_model_fingerprint": None,
            "runtime_identity_sha256": None,
        },
        "image_bindings_path": "swebench/qualification/image-bindings.json",
        "image_bindings_sha256": None,
        "image_bindings_task_count": 10,
        "image_bindings_ordered_instance_ids_sha256": None,
        "control_image_reference": _CONTROL_REFERENCE,
        "control_image_digest": _CONTROL_DIGEST,
        "platform": "linux/amd64",
        "task_count": 10,
        "parameters": dict(_APPROVED_LOCAL_PARAMETERS),
        "resource_limits": {
            "timeout_seconds": 3600,
            "memory_bytes": 17179869184,
            "cpus": "4.0",
            "pids_limit": 512,
            "nofile_limit": 1024,
            "tmpfs_bytes": 268435456,
            "max_output_bytes": 10485760,
            "max_turns": 100,
            "max_requests": 100,
            "max_task_output_tokens": 65536,
        },
        "protocol_approval": {"approved": False, "approved_fingerprint": None},
    }


def _fixture(tmp_path: Path) -> tuple[Path, Path, bytes]:
    project_root = tmp_path / "repo"
    profile_path = project_root / "configs" / "profiles" / "swebench-qualification.yaml"
    profile_path.parent.mkdir(parents=True)
    profile_document = {
        "schema_version": 1,
        "suite": "swebench-qualification",
        "evidence_class": "runtime_scorer_qualification",
        "expanded": True,
        "runner": "swebench-local-sandbox-v1",
        "limitations": ["Synthetic unit-test profile."],
        "swebench": _qualification_profile(),
    }
    profile_path.write_text(yaml.safe_dump(profile_document, sort_keys=False), encoding="utf-8")
    state_dir = tmp_path / "private-state"
    state_dir.mkdir(mode=0o700)
    os.chmod(state_dir, 0o700)
    return project_root, state_dir, profile_path.read_bytes()


def _identity_evidence(**changes: object) -> dict[str, object]:
    evidence: dict[str, object] = {
        "locality_guard_configured": True,
        "served_instance_guard_configured": True,
        "multi_turn": True,
        "bash_only_actions": True,
        "transport_retries": 0,
        "cache_enabled": False,
        "redirects_enabled": False,
        "cloud_fallback": False,
        "max_requests_source": "profile.resource_limits.max_requests",
        "step_limit_source": "profile.resource_limits.max_turns",
        "wall_time_limit_source": "profile.resource_limits.timeout_seconds",
        "adapter_revision_sha256": "a" * 64,
        "served_model_fingerprint": "b" * 64,
        "runtime_identity_sha256": "c" * 64,
    }
    evidence.update(changes)
    assert set(evidence) == _ADAPTER_KEYS
    return evidence


def _install_probes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    identity_probe: object,
    parameter_probe: object,
) -> None:
    monkeypatch.setattr(capture, "_default_adapter_probe", identity_probe)
    monkeypatch.setattr(capture, "_default_parameter_probe", parameter_probe)


def _receipt_files(state_dir: Path) -> list[Path]:
    receipt_directory = state_dir / "swebench" / "qualification" / "runtime-identity"
    return list(receipt_directory.glob("runtime-identity-*.json"))


def test_capture_writes_owner_only_private_receipt_without_mutating_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root, state_dir, profile_bytes = _fixture(tmp_path)
    monkeypatch.setenv("LOCAL_EVALS_SWEBENCH_CONTROL_IMAGE_REFERENCE", _CONTROL_REFERENCE)
    monkeypatch.setenv("LOCAL_EVALS_SWEBENCH_CONTROL_IMAGE_DIGEST", _CONTROL_DIGEST)
    calls: list[str] = []

    def identity_probe(*_args: object) -> dict[str, object]:
        calls.append("identity")
        return _identity_evidence()

    def parameter_probe(
        _origin: str, _model: str, parameters: dict[str, object]
    ) -> dict[str, object]:
        calls.append("parameters")
        return {key: True for key in parameters}

    _install_probes(monkeypatch, identity_probe=identity_probe, parameter_probe=parameter_probe)
    receipt_path = capture.capture_runtime_identity_receipt(
        project_root=project_root,
        state_dir=state_dir,
        confirm_live_probe=True,
    )

    assert calls == ["identity", "parameters", "identity"]
    assert receipt_path.is_relative_to(state_dir)
    assert not receipt_path.is_relative_to(project_root)
    assert stat.S_IMODE(receipt_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(receipt_path.stat().st_mode) == 0o600
    receipt = json.loads(receipt_path.read_bytes())
    receipt_sha256 = receipt.pop("receipt_sha256")
    assert receipt_sha256 == _sha256(_canonical(receipt))
    assert receipt["profile_sha256"] == _sha256(profile_bytes)
    assert receipt["model_runtime"] == {
        "adapter_revision_sha256": "a" * 64,
        "runtime_identity_sha256": "c" * 64,
        "served_model_fingerprint": "b" * 64,
    }
    assert receipt["adapter_probe_evidence"]["cloud_fallback"] is False
    assert receipt["adapter_probe_evidence"]["step_limit_source"] == (
        "profile.resource_limits.max_turns"
    )
    assert "locality_verified_every_turn" not in receipt["adapter_probe_evidence"]
    assert "exact_served_instance_every_turn" not in receipt["adapter_probe_evidence"]
    assert receipt["probe_source_sha256"]["swebench_module_sha256"] == _sha256(
        Path(capture._default_adapter_probe.__code__.co_filename).read_bytes()
    )
    profile_path = project_root / "configs" / "profiles" / "swebench-qualification.yaml"
    assert profile_path.read_bytes() == profile_bytes


def test_capture_refuses_unsupported_parameter_evidence_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root, state_dir, _ = _fixture(tmp_path)
    monkeypatch.setenv("LOCAL_EVALS_SWEBENCH_CONTROL_IMAGE_REFERENCE", _CONTROL_REFERENCE)
    monkeypatch.setenv("LOCAL_EVALS_SWEBENCH_CONTROL_IMAGE_DIGEST", _CONTROL_DIGEST)
    _install_probes(
        monkeypatch,
        identity_probe=lambda *_args: _identity_evidence(),
        parameter_probe=lambda *_args: {"reasoning_effort": True},
    )

    with pytest.raises(SwebenchError, match="effective parameter probe"):
        capture.capture_runtime_identity_receipt(
            project_root=project_root,
            state_dir=state_dir,
            confirm_live_probe=True,
        )
    assert not _receipt_files(state_dir)


def test_capture_refuses_fallback_policy_before_parameter_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root, state_dir, _ = _fixture(tmp_path)
    monkeypatch.setenv("LOCAL_EVALS_SWEBENCH_CONTROL_IMAGE_REFERENCE", _CONTROL_REFERENCE)
    monkeypatch.setenv("LOCAL_EVALS_SWEBENCH_CONTROL_IMAGE_DIGEST", _CONTROL_DIGEST)
    parameter_calls = 0

    def parameter_probe(*_args: object) -> dict[str, object]:
        nonlocal parameter_calls
        parameter_calls += 1
        return {key: True for key in _APPROVED_LOCAL_PARAMETERS}

    _install_probes(
        monkeypatch,
        identity_probe=lambda *_args: _identity_evidence(cloud_fallback=True),
        parameter_probe=parameter_probe,
    )
    with pytest.raises(SwebenchError, match="configured policy"):
        capture.capture_runtime_identity_receipt(
            project_root=project_root,
            state_dir=state_dir,
            confirm_live_probe=True,
        )
    assert parameter_calls == 0
    assert not _receipt_files(state_dir)


def test_capture_refuses_mismatched_runner_limit_source_before_parameter_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root, state_dir, _ = _fixture(tmp_path)
    monkeypatch.setenv("LOCAL_EVALS_SWEBENCH_CONTROL_IMAGE_REFERENCE", _CONTROL_REFERENCE)
    monkeypatch.setenv("LOCAL_EVALS_SWEBENCH_CONTROL_IMAGE_DIGEST", _CONTROL_DIGEST)
    parameter_calls = 0

    def parameter_probe(*_args: object) -> dict[str, object]:
        nonlocal parameter_calls
        parameter_calls += 1
        return {key: True for key in _APPROVED_LOCAL_PARAMETERS}

    _install_probes(
        monkeypatch,
        identity_probe=lambda *_args: _identity_evidence(
            wall_time_limit_source="mini_swe_agent.yaml"
        ),
        parameter_probe=parameter_probe,
    )
    with pytest.raises(SwebenchError, match="configured policy"):
        capture.capture_runtime_identity_receipt(
            project_root=project_root,
            state_dir=state_dir,
            confirm_live_probe=True,
        )
    assert parameter_calls == 0
    assert not _receipt_files(state_dir)


def test_capture_refuses_xhigh_before_any_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root, state_dir, _ = _fixture(tmp_path)
    profile_path = project_root / "configs" / "profiles" / "swebench-qualification.yaml"
    document = yaml.safe_load(profile_path.read_bytes())
    document["swebench"]["parameters"]["reasoning_effort"] = "xhigh"
    profile_path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    monkeypatch.setenv("LOCAL_EVALS_SWEBENCH_CONTROL_IMAGE_REFERENCE", _CONTROL_REFERENCE)
    monkeypatch.setenv("LOCAL_EVALS_SWEBENCH_CONTROL_IMAGE_DIGEST", _CONTROL_DIGEST)
    probe_calls = 0

    def probe(*_args: object) -> dict[str, object]:
        nonlocal probe_calls
        probe_calls += 1
        return _identity_evidence()

    _install_probes(monkeypatch, identity_probe=probe, parameter_probe=probe)
    with pytest.raises(SwebenchError, match="approved local reasoning:on"):
        capture.capture_runtime_identity_receipt(
            project_root=project_root,
            state_dir=state_dir,
            confirm_live_probe=True,
        )
    assert probe_calls == 0
    assert not _receipt_files(state_dir)


def test_direct_api_requires_confirmation_before_any_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root, state_dir, _ = _fixture(tmp_path)
    probe_calls = 0

    def unexpected_probe(*_args: object) -> dict[str, object]:
        nonlocal probe_calls
        probe_calls += 1
        pytest.fail("a probe must not run without explicit confirmation")

    _install_probes(monkeypatch, identity_probe=unexpected_probe, parameter_probe=unexpected_probe)
    with pytest.raises(TypeError, match="confirm_live_probe"):
        capture.capture_runtime_identity_receipt(
            project_root=project_root,
            state_dir=state_dir,
        )
    assert probe_calls == 0
    assert not (state_dir / "swebench").exists()


def test_capture_rejects_nonqualified_endpoint_before_any_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root, state_dir, _ = _fixture(tmp_path)
    probe_calls = 0

    def unexpected_probe(*_args: object) -> dict[str, object]:
        nonlocal probe_calls
        probe_calls += 1
        pytest.fail("a probe must not run for a nonqualified endpoint")

    _install_probes(monkeypatch, identity_probe=unexpected_probe, parameter_probe=unexpected_probe)
    with pytest.raises(SwebenchError, match="exact endpoint"):
        capture.capture_runtime_identity_receipt(
            project_root=project_root,
            state_dir=state_dir,
            confirm_live_probe=True,
            server_origin="http://localhost:1234/v1",
        )
    assert probe_calls == 0
    assert not (state_dir / "swebench").exists()


def test_capture_preflights_private_state_subdirectories_before_any_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root, state_dir, _ = _fixture(tmp_path)
    swebench_state = state_dir / "swebench"
    swebench_state.mkdir(mode=0o700)
    insecure_permissions = (
        stat.S_IRUSR
        | stat.S_IWUSR
        | stat.S_IXUSR
        | stat.S_IRGRP
        | stat.S_IXGRP
        | stat.S_IROTH
        | stat.S_IXOTH
    )
    os.chmod(swebench_state, insecure_permissions)
    monkeypatch.setenv("LOCAL_EVALS_SWEBENCH_CONTROL_IMAGE_REFERENCE", _CONTROL_REFERENCE)
    monkeypatch.setenv("LOCAL_EVALS_SWEBENCH_CONTROL_IMAGE_DIGEST", _CONTROL_DIGEST)
    probe_calls = 0

    def unexpected_probe(*_args: object) -> dict[str, object]:
        nonlocal probe_calls
        probe_calls += 1
        pytest.fail("a probe must not run before private-state preflight")

    _install_probes(monkeypatch, identity_probe=unexpected_probe, parameter_probe=unexpected_probe)
    with pytest.raises(ConfigurationError, match="owner-only"):
        capture.capture_runtime_identity_receipt(
            project_root=project_root,
            state_dir=state_dir,
            confirm_live_probe=True,
        )
    assert probe_calls == 0
    assert not (state_dir / "swebench" / "qualification").exists()


def test_capture_refuses_identity_change_during_parameter_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root, state_dir, _ = _fixture(tmp_path)
    monkeypatch.setenv("LOCAL_EVALS_SWEBENCH_CONTROL_IMAGE_REFERENCE", _CONTROL_REFERENCE)
    monkeypatch.setenv("LOCAL_EVALS_SWEBENCH_CONTROL_IMAGE_DIGEST", _CONTROL_DIGEST)
    identity_calls = 0

    def identity_probe(*_args: object) -> dict[str, object]:
        nonlocal identity_calls
        identity_calls += 1
        if identity_calls == 1:
            return _identity_evidence()
        return _identity_evidence(served_model_fingerprint="e" * 64)

    _install_probes(
        monkeypatch,
        identity_probe=identity_probe,
        parameter_probe=lambda *_args: {key: True for key in _APPROVED_LOCAL_PARAMETERS},
    )
    with pytest.raises(SwebenchError) as exc_info:
        capture.capture_runtime_identity_receipt(
            project_root=project_root,
            state_dir=state_dir,
            confirm_live_probe=True,
        )
    assert str(exc_info.value) == (
        "served model or adapter identity changed during the settings probe "
        "(differing fields: served_model_fingerprint)"
    )
    assert identity_calls == 2
    assert not _receipt_files(state_dir)


def test_capture_refuses_profile_change_during_parameter_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root, state_dir, _ = _fixture(tmp_path)
    monkeypatch.setenv("LOCAL_EVALS_SWEBENCH_CONTROL_IMAGE_REFERENCE", _CONTROL_REFERENCE)
    monkeypatch.setenv("LOCAL_EVALS_SWEBENCH_CONTROL_IMAGE_DIGEST", _CONTROL_DIGEST)
    profile_path = project_root / "configs" / "profiles" / "swebench-qualification.yaml"

    def mutate_profile(*_args: object) -> dict[str, object]:
        profile_path.write_bytes(profile_path.read_bytes() + b"# probe-time mutation\n")
        return {key: True for key in _APPROVED_LOCAL_PARAMETERS}

    _install_probes(
        monkeypatch,
        identity_probe=lambda *_args: _identity_evidence(),
        parameter_probe=mutate_profile,
    )
    with pytest.raises(SwebenchError, match="profile, control identity, or probe code changed"):
        capture.capture_runtime_identity_receipt(
            project_root=project_root,
            state_dir=state_dir,
            confirm_live_probe=True,
        )
    assert not _receipt_files(state_dir)


def test_capture_refuses_invalid_identity_hash_before_parameter_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root, state_dir, _ = _fixture(tmp_path)
    monkeypatch.setenv("LOCAL_EVALS_SWEBENCH_CONTROL_IMAGE_REFERENCE", _CONTROL_REFERENCE)
    monkeypatch.setenv("LOCAL_EVALS_SWEBENCH_CONTROL_IMAGE_DIGEST", _CONTROL_DIGEST)
    parameter_calls = 0

    def parameter_probe(*_args: object) -> dict[str, object]:
        nonlocal parameter_calls
        parameter_calls += 1
        return {key: True for key in _APPROVED_LOCAL_PARAMETERS}

    _install_probes(
        monkeypatch,
        identity_probe=lambda *_args: _identity_evidence(served_model_fingerprint="not-a-sha256"),
        parameter_probe=parameter_probe,
    )
    with pytest.raises(SwebenchError, match="invalid SHA-256 fingerprint"):
        capture.capture_runtime_identity_receipt(
            project_root=project_root,
            state_dir=state_dir,
            confirm_live_probe=True,
        )
    assert parameter_calls == 0
    assert not _receipt_files(state_dir)


def test_cli_requires_explicit_live_probe_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    monkeypatch.setattr(
        capture,
        "capture_runtime_identity_receipt",
        lambda **_kwargs: pytest.fail("capture must not run without explicit confirmation"),
    )

    with pytest.raises(SystemExit) as exit_info:
        capture_main(["--project-root", str(project_root)])
    assert exit_info.value.code == 2


def test_cli_passes_explicit_live_probe_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    receipt_path = tmp_path / "private-state" / "runtime-identity.json"

    def capture_with_confirmation(**kwargs: object) -> Path:
        assert kwargs["confirm_live_probe"] is True
        return receipt_path

    monkeypatch.setattr(capture, "capture_runtime_identity_receipt", capture_with_confirmation)
    assert capture_main(["--project-root", str(project_root), "--confirm-live-probe"]) == 0
    assert capsys.readouterr().out.strip() == str(receipt_path)
