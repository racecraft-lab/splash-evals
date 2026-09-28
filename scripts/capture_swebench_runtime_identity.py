"""Capture local SWE-bench qualification runtime identity evidence privately."""

from __future__ import annotations

import argparse
import os
import stat
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml

from local_evals.config import (
    ConfigurationError,
    ExternalStatePaths,
    _safe_config_path,
    secure_resolve,
)
from local_evals.swebench import (
    _ADAPTER_KEYS,
    _APPROVED_LOCAL_PARAMETERS,
    _PROFILE_KEYS,
    SwebenchError,
    _canonical,
    _default_adapter_probe,
    _default_parameter_probe,
    _is_sha256,
    _sha256,
    _validate_origin,
)

_PROFILE_PATH = Path("configs/profiles/swebench-qualification.yaml")
_RECEIPT_DIRECTORY = Path("swebench/qualification/runtime-identity")
_QUALIFIED_SERVER_ORIGIN = "http://127.0.0.1:1234/v1"
_MODEL_RUNTIME_KEYS = {
    "adapter_revision_sha256",
    "served_model_fingerprint",
    "runtime_identity_sha256",
}
_ADAPTER_POLICY = {
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
}


@dataclass(frozen=True)
class _ProfileContext:
    path: Path
    raw: bytes
    model: str
    parameters: Mapping[str, object]
    limits: Mapping[str, Any]
    control_identity: tuple[str, str]


def _validated_local_protocol(profile: Mapping[str, Any]) -> Mapping[str, object]:
    parameters = profile.get("parameters")
    if not isinstance(parameters, Mapping) or dict(parameters) != _APPROVED_LOCAL_PARAMETERS:
        raise SwebenchError(
            "runtime identity receipt requires the approved local reasoning:on settings"
        )
    approval = profile.get("protocol_approval")
    expected_approval = {"approved": False, "approved_fingerprint": None}
    if not isinstance(approval, Mapping) or dict(approval) != expected_approval:
        raise SwebenchError("runtime identity receipt refuses an approved or changed protocol")
    model_runtime = profile.get("model_runtime")
    if (
        not isinstance(model_runtime, Mapping)
        or set(model_runtime) != _MODEL_RUNTIME_KEYS
        or any(value is not None for value in model_runtime.values())
    ):
        raise SwebenchError("runtime identity fields must be empty before receipt capture")
    return cast(Mapping[str, object], parameters)


def _validated_runtime_settings(
    profile: Mapping[str, Any],
) -> tuple[str, Mapping[str, Any], tuple[str, str]]:
    model = profile.get("model")
    if not isinstance(model, str) or not model.strip():
        raise SwebenchError("qualification model name is missing")
    limits = profile.get("resource_limits")
    if not isinstance(limits, Mapping):
        raise SwebenchError("qualification resource limits are invalid")
    control_identity = (
        profile.get("control_image_reference"),
        profile.get("control_image_digest"),
    )
    if not all(isinstance(value, str) and value for value in control_identity):
        raise SwebenchError("qualification control image identity is incomplete")
    expected_control = cast(tuple[str, str], control_identity)
    configured_control = (
        os.environ.get("LOCAL_EVALS_SWEBENCH_CONTROL_IMAGE_REFERENCE"),
        os.environ.get("LOCAL_EVALS_SWEBENCH_CONTROL_IMAGE_DIGEST"),
    )
    if configured_control != expected_control:
        raise SwebenchError(
            "control image environment does not match the frozen qualification profile"
        )
    return model, limits, expected_control


def _load_profile_context(paths: ExternalStatePaths) -> _ProfileContext:
    profile_path = _safe_config_path(paths.project_root, _PROFILE_PATH)
    profile_bytes = profile_path.read_bytes()
    try:
        document = yaml.safe_load(profile_bytes)
    except yaml.YAMLError as exc:
        raise SwebenchError("qualification profile YAML is invalid") from exc
    if not isinstance(document, Mapping) or document.get("suite") != "swebench-qualification":
        raise SwebenchError("runtime identity receipt requires the qualification profile")
    profile = document.get("swebench")
    if not isinstance(profile, Mapping) or set(profile) != _PROFILE_KEYS:
        raise SwebenchError("qualification SWE-bench profile shape is unsupported")
    if profile.get("mode") != "qualification":
        raise SwebenchError("runtime identity receipt refuses non-qualification profiles")
    parameters = _validated_local_protocol(profile)
    model, limits, control_identity = _validated_runtime_settings(profile)
    return _ProfileContext(
        profile_path,
        profile_bytes,
        model,
        cast(Mapping[str, object], parameters),
        limits,
        control_identity,
    )


def _verified_identity_evidence(
    identity_probe: Callable[[str, str, Mapping[str, object]], Mapping[str, object]],
    origin: str,
    context: _ProfileContext,
) -> dict[str, Any]:
    evidence = identity_probe(origin, context.model, context.parameters)
    if not isinstance(evidence, Mapping) or set(evidence) != _ADAPTER_KEYS:
        raise SwebenchError("adapter identity evidence is incomplete")
    for key, expected in _ADAPTER_POLICY.items():
        value = evidence.get(key)
        if type(value) is not type(expected) or value != expected:
            raise SwebenchError("adapter configured policy does not match the qualified contract")
    result = dict(evidence)
    if any(not _is_sha256(result.get(key)) for key in _MODEL_RUNTIME_KEYS):
        raise SwebenchError("adapter identity evidence has an invalid SHA-256 fingerprint")
    return result


def _probe_source_hashes() -> dict[str, str]:
    module_path = Path(_default_adapter_probe.__code__.co_filename).resolve()
    return {
        "capture_script_sha256": _sha256(Path(__file__).resolve().read_bytes()),
        "swebench_module_sha256": _sha256(module_path.read_bytes()),
    }


def _ensure_private_directory(path: Path) -> None:
    secure_resolve(path, must_exist=False)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory_stat = path.stat()
    if (hasattr(os, "getuid") and directory_stat.st_uid != os.getuid()) or stat.S_IMODE(
        directory_stat.st_mode
    ) & 0o077:
        raise ConfigurationError("runtime identity receipt directories must be owner-only")


def _preflight_private_receipt_directory(state_dir: Path) -> Path:
    receipt_directory = state_dir / _RECEIPT_DIRECTORY
    for relative in (
        Path("."),
        Path("swebench"),
        Path("swebench/qualification"),
        _RECEIPT_DIRECTORY,
    ):
        _ensure_private_directory(state_dir / relative)
    return receipt_directory


def _write_private_receipt(state_dir: Path, receipt: Mapping[str, Any]) -> Path:
    if receipt.get("evidence_class") != "swebench_qualification_runtime_identity":
        raise SwebenchError("test-only runtime identity evidence cannot be exported")
    receipt_sha256 = cast(str, receipt["receipt_sha256"])
    receipt_directory = _preflight_private_receipt_directory(state_dir)
    receipt_path = receipt_directory / f"runtime-identity-{receipt_sha256}.json"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(receipt_path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_canonical(receipt))
            handle.flush()
            os.fsync(handle.fileno())
        directory_descriptor = os.open(receipt_directory, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except BaseException:
        receipt_path.unlink(missing_ok=True)
        raise
    return receipt_path


def capture_runtime_identity_receipt(
    *,
    project_root: str | os.PathLike[str],
    confirm_live_probe: bool,
    state_dir: str | os.PathLike[str] | None = None,
    server_origin: str = _QUALIFIED_SERVER_ORIGIN,
) -> Path:
    """Capture hashes after explicit confirmation and live probes pass."""

    if confirm_live_probe is not True:
        raise SwebenchError("explicit confirmation is required for a live SDK probe")
    if server_origin != _QUALIFIED_SERVER_ORIGIN:
        raise SwebenchError(
            f"runtime identity capture requires the exact endpoint {_QUALIFIED_SERVER_ORIGIN}"
        )
    paths = ExternalStatePaths.resolve(project_root=project_root, state_dir=state_dir)
    origin = _validate_origin(server_origin)
    if origin != _QUALIFIED_SERVER_ORIGIN:
        raise SwebenchError("runtime identity capture requires the exact qualified endpoint")
    context = _load_profile_context(paths)
    _preflight_private_receipt_directory(paths.state_dir)
    source_hashes_before = _probe_source_hashes()
    expected_supported = {key: True for key in context.parameters}
    identity_before = _verified_identity_evidence(_default_adapter_probe, origin, context)
    # The shared probe validates raw SDK evidence but exposes only support booleans;
    # this receipt preserves that API result without reconstructing measured values.
    parameter_evidence = _default_parameter_probe(origin, context.model, context.parameters)
    if (
        not isinstance(parameter_evidence, Mapping)
        or dict(parameter_evidence) != expected_supported
        or any(value is not True for value in parameter_evidence.values())
    ):
        raise SwebenchError("effective parameter probe did not verify every requested setting")
    identity_after = _verified_identity_evidence(_default_adapter_probe, origin, context)
    changed_fields = sorted(
        key for key in _MODEL_RUNTIME_KEYS if identity_before[key] != identity_after[key]
    )
    if changed_fields:
        raise SwebenchError(
            "served model or adapter identity changed during the settings probe "
            f"(differing fields: {', '.join(changed_fields)})"
        )
    if (
        context.path.read_bytes() != context.raw
        or (
            os.environ.get("LOCAL_EVALS_SWEBENCH_CONTROL_IMAGE_REFERENCE"),
            os.environ.get("LOCAL_EVALS_SWEBENCH_CONTROL_IMAGE_DIGEST"),
        )
        != context.control_identity
        or _probe_source_hashes() != source_hashes_before
    ):
        raise SwebenchError(
            "qualification profile, control identity, or probe code changed during probing"
        )

    receipt_payload: dict[str, Any] = {
        "schema_version": 1,
        "evidence_class": "swebench_qualification_runtime_identity",
        "captured_at_utc": datetime.now(UTC).isoformat(timespec="microseconds"),
        "profile_sha256": _sha256(context.raw),
        "model": context.model,
        "server_origin": origin,
        "parameters": dict(context.parameters),
        "parameter_probe_supported": dict(parameter_evidence),
        "adapter_probe_evidence": {key: identity_after[key] for key in sorted(_ADAPTER_KEYS)},
        "control_image_reference": context.control_identity[0],
        "control_image_digest": context.control_identity[1],
        "model_runtime": {key: identity_after[key] for key in sorted(_MODEL_RUNTIME_KEYS)},
        "probe_source_sha256": source_hashes_before,
    }
    receipt_sha256 = _sha256(_canonical(receipt_payload))
    receipt = {**receipt_payload, "receipt_sha256": receipt_sha256}
    return _write_private_receipt(paths.state_dir, receipt)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="SWE-bench repository root",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        help="private state root (defaults to ~/.local/state/splash-evals)",
    )
    parser.add_argument(
        "--server-origin",
        default="http://127.0.0.1:1234/v1",
        help="qualified loopback LM Studio API origin",
    )
    parser.add_argument(
        "--confirm-live-probe",
        action="store_true",
        help="allow one local SDK settings inference between identity snapshots",
    )
    args = parser.parse_args(argv)
    if not args.confirm_live_probe:
        parser.error(
            "--confirm-live-probe is required; this command sends a local SDK probe request"
        )
    try:
        receipt_path = capture_runtime_identity_receipt(
            project_root=args.project_root,
            confirm_live_probe=args.confirm_live_probe,
            state_dir=args.state_dir,
            server_origin=args.server_origin,
        )
    except (ConfigurationError, OSError, SwebenchError, ValueError) as exc:
        print(f"runtime identity receipt refused: {exc}", file=sys.stderr)
        return 2
    print(receipt_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
