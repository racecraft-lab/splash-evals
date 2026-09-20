"""Fail-closed Docker boundary for grading generated Python programs.

The request and raw process output are private evidence.  Only
``public_result_evidence`` is suitable for publication.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol, cast

__all__ = [
    "SandboxAttestation",
    "SandboxError",
    "SandboxPolicy",
    "SandboxQualification",
    "SandboxQualificationError",
    "SandboxQualificationLog",
    "SandboxResult",
    "SandboxRuntime",
    "build_docker_run_args",
    "public_qualification_evidence",
    "public_result_evidence",
    "qualify_sandbox",
    "run_coding_case",
    "validate_attestation",
]

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_CASE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_NONCE_RE = re.compile(r"[0-9a-f]{16}\Z")
_CPU_RE = re.compile(r"(?:0\.[1-9][0-9]*|[1-9][0-9]*(?:\.[0-9]+)?)\Z")
_ATTESTATION_KEYS = frozenset(
    {
        "schema_version",
        "attestation_revision",
        "qualified",
        "container_runtime",
        "platform",
        "image_reference",
        "image_digest",
        "policy_sha256",
        "disposable",
        "fresh_container_per_case",
        "network_mode",
        "network_disabled",
        "non_root_user",
        "read_only_rootfs",
        "cap_drop",
        "no_new_privileges",
        "pids_limit",
        "memory_bytes",
        "cpus",
        "nofile_limit",
        "tmpfs_bytes",
        "published_ports",
        "host_mounts",
        "host_home_mounted",
        "docker_socket_mounted",
        "secrets_present",
    }
)
_QUALIFICATION_CHECKS = frozenset(
    {
        "dns_unreachable",
        "ipv4_unreachable",
        "ipv6_unreachable",
        "host_gateway_unreachable",
        "host_paths_absent",
        "secrets_absent",
        "docker_socket_absent",
        "non_root",
        "capabilities_empty",
        "image_write_refused",
        "etc_write_refused",
        "input_write_refused",
        "pids_bounded",
        "memory_bounded",
        "loop_bounded",
        "traversal_refused",
        "symlink_escape_refused",
    }
)


class SandboxError(ValueError):
    """Raised before execution when sandbox proof or policy is unsafe."""


class SandboxQualificationError(SandboxError):
    """Qualification failure carrying private command evidence for diagnosis."""

    def __init__(self, message: str, private_logs: tuple[SandboxQualificationLog, ...]) -> None:
        super().__init__(message)
        self.private_logs = private_logs


class _Runner(Protocol):
    def __call__(
        self, args: Sequence[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]: ...


@dataclass(frozen=True, slots=True)
class SandboxPolicy:
    """Immutable execution controls bound to one exact grading image."""

    image_reference: str
    image_digest: str
    platform: str = "linux/arm64/v8"
    timeout_seconds: float = 10.0
    max_input_bytes: int = 262_144
    max_output_bytes: int = 65_536
    memory_bytes: int = 536_870_912
    cpus: str = "1.0"
    pids_limit: int = 32
    nofile_limit: int = 64
    tmpfs_bytes: int = 67_108_864
    user: str = "65532:65532"
    workdir: str = "/work"

    def __post_init__(self) -> None:
        if not _DIGEST_RE.fullmatch(self.image_digest):
            raise SandboxError("image_digest must be a lowercase sha256 digest")
        named_digest = self.image_reference.endswith(f"@{self.image_digest}")
        digest_only = self.image_reference == self.image_digest
        if not named_digest and not digest_only:
            raise SandboxError("image_reference must end with the exact image digest")
        if any(character.isspace() for character in self.image_reference):
            raise SandboxError("image_reference must not contain whitespace")
        if named_digest and self.image_reference.count("@") != 1:
            raise SandboxError("named image_reference must contain one digest separator")
        if self.platform != "linux/arm64/v8":
            raise SandboxError("platform must be linux/arm64/v8")
        if not 0 < self.timeout_seconds <= 300:
            raise SandboxError("timeout_seconds must be between 0 and 300")
        for label, value in (
            ("max_input_bytes", self.max_input_bytes),
            ("max_output_bytes", self.max_output_bytes),
            ("memory_bytes", self.memory_bytes),
            ("pids_limit", self.pids_limit),
            ("nofile_limit", self.nofile_limit),
            ("tmpfs_bytes", self.tmpfs_bytes),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise SandboxError(f"{label} must be a positive integer")
        if not _CPU_RE.fullmatch(self.cpus):
            raise SandboxError("cpus must be a positive decimal string")
        if self.user == "0" or self.user.startswith("0:"):
            raise SandboxError("user must be non-root")
        if not re.fullmatch(r"[1-9][0-9]*:[1-9][0-9]*", self.user):
            raise SandboxError("user must be a numeric non-root uid:gid")
        if self.workdir != "/work":
            raise SandboxError("workdir must be /work")

    @property
    def sha256(self) -> str:
        """Return the canonical policy fingerprint used by attestations."""

        encoded = json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class SandboxAttestation:
    """Validated proof that the runtime matches ``SandboxPolicy`` exactly."""

    schema_version: int
    attestation_revision: str
    qualified: bool
    container_runtime: str
    platform: str
    image_reference: str
    image_digest: str
    policy_sha256: str
    disposable: bool
    fresh_container_per_case: bool
    network_mode: str
    network_disabled: bool
    non_root_user: str
    read_only_rootfs: bool
    cap_drop: tuple[str, ...]
    no_new_privileges: bool
    pids_limit: int
    memory_bytes: int
    cpus: str
    nofile_limit: int
    tmpfs_bytes: int
    published_ports: tuple[str, ...]
    host_mounts: tuple[str, ...]
    host_home_mounted: bool
    docker_socket_mounted: bool
    secrets_present: bool


@dataclass(frozen=True, slots=True)
class SandboxResult:
    """Private per-case result; stdout and stderr must not be published."""

    case_id: str
    status: str
    exit_code: int | None
    timed_out: bool
    protocol_valid: bool
    stdout: bytes
    stderr: bytes
    output_truncated: bool
    stdout_bytes: int
    stderr_bytes: int
    duration_ms: int


@dataclass(frozen=True, slots=True)
class SandboxQualificationLog:
    """Private command transcript retained outside public evidence."""

    command: tuple[str, ...]
    returncode: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool


@dataclass(frozen=True, slots=True)
class SandboxQualification:
    """Completed qualification and its private command transcript."""

    attestation: SandboxAttestation
    private_logs: tuple[SandboxQualificationLog, ...]


@dataclass(frozen=True, slots=True)
class SandboxRuntime:
    """Injectable local process dependencies for one sandbox invocation."""

    docker_executable: Path = Path("/usr/local/bin/docker")
    runner: _Runner | None = None
    nonce_factory: Callable[[], str] = lambda: secrets.token_hex(8)
    clock: Callable[[], float] = time.monotonic


_DEFAULT_RUNTIME = SandboxRuntime()


def _strict_bool(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise SandboxError(f"sandbox attestation {label} must be a boolean")
    return value


def _strict_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SandboxError(f"sandbox attestation {label} must be an integer")
    return value


def _strict_str(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise SandboxError(f"sandbox attestation {label} must be a string")
    return value


def _string_tuple(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise SandboxError(f"sandbox attestation {label} must be a string list")
    return tuple(value)


def _parse_attestation(evidence: Mapping[str, Any]) -> SandboxAttestation:
    actual_keys = frozenset(evidence)
    missing = _ATTESTATION_KEYS - actual_keys
    unexpected = actual_keys - _ATTESTATION_KEYS
    if missing:
        raise SandboxError(f"sandbox attestation missing fields: {', '.join(sorted(missing))}")
    if unexpected:
        raise SandboxError(
            f"sandbox attestation has unexpected fields: {', '.join(sorted(unexpected))}"
        )

    return SandboxAttestation(
        schema_version=_strict_int(evidence["schema_version"], "schema_version"),
        attestation_revision=_strict_str(evidence["attestation_revision"], "attestation_revision"),
        qualified=_strict_bool(evidence["qualified"], "qualified"),
        container_runtime=_strict_str(evidence["container_runtime"], "container_runtime"),
        platform=_strict_str(evidence["platform"], "platform"),
        image_reference=_strict_str(evidence["image_reference"], "image_reference"),
        image_digest=_strict_str(evidence["image_digest"], "image_digest"),
        policy_sha256=_strict_str(evidence["policy_sha256"], "policy_sha256"),
        disposable=_strict_bool(evidence["disposable"], "disposable"),
        fresh_container_per_case=_strict_bool(
            evidence["fresh_container_per_case"], "fresh_container_per_case"
        ),
        network_mode=_strict_str(evidence["network_mode"], "network_mode"),
        network_disabled=_strict_bool(evidence["network_disabled"], "network_disabled"),
        non_root_user=_strict_str(evidence["non_root_user"], "non_root_user"),
        read_only_rootfs=_strict_bool(evidence["read_only_rootfs"], "read_only_rootfs"),
        cap_drop=_string_tuple(evidence["cap_drop"], "cap_drop"),
        no_new_privileges=_strict_bool(evidence["no_new_privileges"], "no_new_privileges"),
        pids_limit=_strict_int(evidence["pids_limit"], "pids_limit"),
        memory_bytes=_strict_int(evidence["memory_bytes"], "memory_bytes"),
        cpus=_strict_str(evidence["cpus"], "cpus"),
        nofile_limit=_strict_int(evidence["nofile_limit"], "nofile_limit"),
        tmpfs_bytes=_strict_int(evidence["tmpfs_bytes"], "tmpfs_bytes"),
        published_ports=_string_tuple(evidence["published_ports"], "published_ports"),
        host_mounts=_string_tuple(evidence["host_mounts"], "host_mounts"),
        host_home_mounted=_strict_bool(evidence["host_home_mounted"], "host_home_mounted"),
        docker_socket_mounted=_strict_bool(
            evidence["docker_socket_mounted"], "docker_socket_mounted"
        ),
        secrets_present=_strict_bool(evidence["secrets_present"], "secrets_present"),
    )


def _attestation_expectations(policy: SandboxPolicy) -> dict[str, object]:
    return {
        "schema_version": 1,
        "qualified": True,
        "container_runtime": "docker",
        "platform": policy.platform,
        "image_reference": policy.image_reference,
        "image_digest": policy.image_digest,
        "policy_sha256": policy.sha256,
        "disposable": True,
        "fresh_container_per_case": True,
        "network_mode": "none",
        "network_disabled": True,
        "non_root_user": policy.user,
        "read_only_rootfs": True,
        "cap_drop": ("ALL",),
        "no_new_privileges": True,
        "pids_limit": policy.pids_limit,
        "memory_bytes": policy.memory_bytes,
        "cpus": policy.cpus,
        "nofile_limit": policy.nofile_limit,
        "tmpfs_bytes": policy.tmpfs_bytes,
        "published_ports": (),
        "host_mounts": (),
        "host_home_mounted": False,
        "docker_socket_mounted": False,
        "secrets_present": False,
    }


def validate_attestation(evidence: Mapping[str, Any], policy: SandboxPolicy) -> SandboxAttestation:
    """Validate a strict, closed attestation schema against ``policy``."""

    attestation = _parse_attestation(evidence)
    for field, expected_value in _attestation_expectations(policy).items():
        if getattr(attestation, field) != expected_value:
            raise SandboxError(f"sandbox attestation does not prove {field}")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", attestation.attestation_revision):
        raise SandboxError("sandbox attestation revision is invalid")
    return attestation


def _container_name(case_id: str, nonce: str) -> str:
    if not _CASE_ID_RE.fullmatch(case_id):
        raise SandboxError("case_id must contain only safe identifier characters")
    if not _NONCE_RE.fullmatch(nonce):
        raise SandboxError("container nonce must be 16 lowercase hexadecimal characters")
    return f"racecraft-grade-{case_id}-{nonce}"


def _docker_path(docker_executable: Path) -> str:
    if not docker_executable.is_absolute() or docker_executable.name != "docker":
        raise SandboxError("docker executable must be an absolute path named docker")
    return str(docker_executable)


def _docker_run_args(
    policy: SandboxPolicy,
    *,
    case_id: str,
    nonce: str,
    docker_executable: Path,
    entrypoint_args: tuple[str, ...],
    detached: bool = False,
) -> tuple[str, ...]:
    docker = _docker_path(docker_executable)
    name = _container_name(case_id, nonce)
    arguments: tuple[str, ...] = (
        docker,
        "run",
        "--rm",
        "--name",
        name,
        "--platform",
        policy.platform,
        "--pull",
        "never",
        "--network",
        "none",
        "--read-only",
        "--user",
        policy.user,
        "--workdir",
        policy.workdir,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges=true",
        "--pids-limit",
        str(policy.pids_limit),
        "--memory",
        str(policy.memory_bytes),
        "--memory-swap",
        str(policy.memory_bytes),
        "--cpus",
        policy.cpus,
        "--ulimit",
        f"nofile={policy.nofile_limit}:{policy.nofile_limit}",
        "--tmpfs",
        f"/tmp:rw,noexec,nosuid,nodev,size={policy.tmpfs_bytes}",  # noqa: S108
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        "--env",
        "PYTHONUNBUFFERED=1",
        "--interactive",
    )
    if detached:
        arguments += ("--detach",)
    return arguments + (
        "--entrypoint",
        "/usr/local/bin/python",
        policy.image_reference,
        "-I",
        "-B",
        *entrypoint_args,
    )


def build_docker_run_args(
    policy: SandboxPolicy,
    *,
    case_id: str,
    nonce: str,
    docker_executable: Path = Path("/usr/local/bin/docker"),
) -> tuple[str, ...]:
    """Build a shell-free, no-network, no-mount grading invocation."""

    return _docker_run_args(
        policy,
        case_id=case_id,
        nonce=nonce,
        docker_executable=docker_executable,
        entrypoint_args=("/opt/racecraft/grade.py",),
    )


def _bounded(data: bytes | str | None, limit: int) -> tuple[bytes, int, bool]:
    if data is None:
        raw = b""
    elif isinstance(data, str):
        raw = data.encode("utf-8", errors="replace")
    else:
        raw = data
    return raw[:limit], len(raw), len(raw) > limit


def _cleanup(
    runner: _Runner,
    docker: str,
    name: str,
    *,
    cleanup_timeout_seconds: float = 10.0,
    logs: list[SandboxQualificationLog] | None = None,
) -> None:
    command = (docker, "rm", "--force", name)
    try:
        completed = runner(
            command,
            input=None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=cleanup_timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError, RuntimeError) as error:
        if logs is not None:
            logs.append(SandboxQualificationLog(command, None, b"", str(error).encode(), False))
        raise SandboxError("could not prove container teardown") from error
    if logs is not None:
        logs.append(
            SandboxQualificationLog(
                command, completed.returncode, completed.stdout, completed.stderr, False
            )
        )
    stderr = completed.stderr.decode("utf-8", errors="replace")
    already_removed = completed.returncode == 1 and "No such container" in stderr
    if completed.returncode != 0 and not already_removed:
        raise SandboxError("could not prove container teardown")


def _qualification_command(
    runner: _Runner,
    command: tuple[str, ...],
    logs: list[SandboxQualificationLog],
    *,
    timeout: float,
) -> subprocess.CompletedProcess[bytes]:
    try:
        completed = runner(
            command,
            input=None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        stdout, _, _ = _bounded(error.stdout, 1_048_576)
        stderr, _, _ = _bounded(error.stderr, 1_048_576)
        logs.append(SandboxQualificationLog(command, None, stdout, stderr, True))
        raise
    except (OSError, subprocess.SubprocessError, RuntimeError) as error:
        logs.append(SandboxQualificationLog(command, None, b"", str(error).encode(), False))
        raise SandboxError("sandbox qualification command failed") from error
    logs.append(
        SandboxQualificationLog(
            command, completed.returncode, completed.stdout, completed.stderr, False
        )
    )
    return completed


def _json_mapping(raw: bytes, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SandboxError(f"{label} returned invalid JSON") from error
    if not isinstance(value, Mapping):
        raise SandboxError(f"{label} did not return an object")
    return cast(Mapping[str, Any], value)


def _verify_image_inspect(raw: bytes, policy: SandboxPolicy) -> str:
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SandboxError("image inspection returned invalid JSON") from error
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], Mapping):
        raise SandboxError("image inspection returned an unexpected shape")
    image = cast(Mapping[str, Any], payload[0])
    image_id = image.get("Id")
    repo_digests = image.get("RepoDigests")
    digest_bound = image_id == policy.image_digest or (
        isinstance(repo_digests, list) and policy.image_reference in repo_digests
    )
    if (
        not isinstance(image_id, str)
        or not _DIGEST_RE.fullmatch(image_id)
        or not digest_bound
        or image.get("Os") != "linux"
        or image.get("Architecture") != "arm64"
    ):
        raise SandboxError("image inspection does not match the exact policy image and platform")
    return image_id


def _expected_nano_cpus(policy: SandboxPolicy) -> int:
    try:
        value = Decimal(policy.cpus) * Decimal(1_000_000_000)
    except InvalidOperation as error:  # pragma: no cover - policy validation already rejects this
        raise SandboxError("invalid CPU policy") from error
    return int(value)


def _verify_container_inspect(raw: bytes, policy: SandboxPolicy, image_id: str) -> None:
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SandboxError("container inspection returned invalid JSON") from error
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], Mapping):
        raise SandboxError("container inspection returned an unexpected shape")
    container = cast(Mapping[str, Any], payload[0])
    config = container.get("Config")
    host = container.get("HostConfig")
    state = container.get("State")
    mounts = container.get("Mounts")
    if not all(isinstance(value, Mapping) for value in (config, host, state)):
        raise SandboxError("container inspection omitted required configuration")
    config = cast(Mapping[str, Any], config)
    host = cast(Mapping[str, Any], host)
    state = cast(Mapping[str, Any], state)
    if not isinstance(mounts, list):
        raise SandboxError("container inspection omitted mounts")
    ulimits = host.get("Ulimits")
    expected_ulimit = {
        "Name": "nofile",
        "Soft": policy.nofile_limit,
        "Hard": policy.nofile_limit,
    }
    tmpfs = host.get("Tmpfs")
    security = host.get("SecurityOpt")
    safe_mounts = all(
        isinstance(mount, Mapping)
        and mount.get("Type") == "tmpfs"
        and mount.get("Destination") == "/tmp"  # noqa: S108 - isolated container tmpfs.
        for mount in mounts
    )
    checks = (
        container.get("Image") == image_id,
        state.get("Running") is True,
        config.get("User") == policy.user,
        config.get("WorkingDir") == policy.workdir,
        not config.get("ExposedPorts"),
        host.get("NetworkMode") == "none",
        host.get("ReadonlyRootfs") is True,
        host.get("Privileged") is False,
        host.get("AutoRemove") is True,
        host.get("CapDrop") == ["ALL"],
        not host.get("CapAdd"),
        isinstance(security, list)
        and any(str(option).startswith("no-new-privileges") for option in security),
        host.get("PidsLimit") == policy.pids_limit,
        host.get("Memory") == policy.memory_bytes,
        host.get("MemorySwap") == policy.memory_bytes,
        host.get("NanoCpus") == _expected_nano_cpus(policy),
        isinstance(ulimits, list) and expected_ulimit in ulimits,
        isinstance(tmpfs, Mapping)
        and isinstance(tmpfs.get("/tmp"), str)  # noqa: S108 - policy mount target.
        and set(str(tmpfs["/tmp"]).split(","))  # noqa: S108 - policy mount target.
        == {"rw", "noexec", "nosuid", "nodev", f"size={policy.tmpfs_bytes}"},
        not host.get("PortBindings"),
        host.get("PublishAllPorts") is False,
        not host.get("Binds"),
        not host.get("Mounts"),
        not host.get("VolumesFrom"),
        not host.get("Devices"),
        not host.get("DeviceRequests"),
        not host.get("ExtraHosts"),
        safe_mounts,
    )
    if not all(checks):
        raise SandboxError("container inspection does not match every sandbox policy control")


def _verify_probe_report(raw: bytes) -> None:
    report = _json_mapping(raw, "sandbox probe")
    if frozenset(report) != {"schema_version", "passed", "checks"}:
        raise SandboxError("sandbox probe report has unexpected fields")
    checks = report["checks"]
    if (
        report["schema_version"] != 1
        or report["passed"] is not True
        or not isinstance(checks, list)
    ):
        raise SandboxError("sandbox probe did not pass")
    parsed: dict[str, bool] = {}
    for check in checks:
        if not isinstance(check, Mapping) or frozenset(check) != {"name", "passed"}:
            raise SandboxError("sandbox probe check is malformed")
        name = check.get("name")
        passed = check.get("passed")
        if not isinstance(name, str) or type(passed) is not bool or name in parsed:
            raise SandboxError("sandbox probe check is malformed")
        parsed[name] = passed
    if frozenset(parsed) != _QUALIFICATION_CHECKS or not all(parsed.values()):
        raise SandboxError("sandbox probe coverage is incomplete or failed")


def _verify_simple_probe(raw: bytes, expected_probe: str) -> None:
    report = _json_mapping(raw, expected_probe)
    if report != {"schema_version": 1, "passed": True, "probe": expected_probe}:
        raise SandboxError(f"{expected_probe} probe failed")


def _run_qualifier_probe(
    policy: SandboxPolicy,
    runtime: SandboxRuntime,
    runner: _Runner,
    logs: list[SandboxQualificationLog],
    *,
    case_id: str,
    mode: tuple[str, ...] = (),
    expect_timeout: bool = False,
) -> subprocess.CompletedProcess[bytes] | None:
    nonce = runtime.nonce_factory()
    command = _docker_run_args(
        policy,
        case_id=case_id,
        nonce=nonce,
        docker_executable=runtime.docker_executable,
        entrypoint_args=("/opt/racecraft/qualify.py", *mode),
    )
    name = _container_name(case_id, nonce)
    docker = command[0]
    try:
        try:
            completed = _qualification_command(
                runner, command, logs, timeout=policy.timeout_seconds
            )
        except subprocess.TimeoutExpired:
            if expect_timeout:
                return None
            raise SandboxError("sandbox qualification probe exceeded its bound") from None
    finally:
        _cleanup(runner, docker, name, logs=logs)
    if expect_timeout:
        raise SandboxError("unbounded loop escaped the sandbox timeout")
    if completed.returncode != 0:
        raise SandboxError(f"sandbox qualification probe {case_id} failed")
    return completed


def _inspect_qualification_container(
    policy: SandboxPolicy,
    runtime: SandboxRuntime,
    runner: _Runner,
    logs: list[SandboxQualificationLog],
    image_id: str,
) -> None:
    nonce = runtime.nonce_factory()
    case_id = "qualification-inspect"
    command = _docker_run_args(
        policy,
        case_id=case_id,
        nonce=nonce,
        docker_executable=runtime.docker_executable,
        entrypoint_args=("/opt/racecraft/qualify.py", "--hold"),
        detached=True,
    )
    name = _container_name(case_id, nonce)
    docker = command[0]
    try:
        started = _qualification_command(runner, command, logs, timeout=policy.timeout_seconds)
        if started.returncode != 0:
            raise SandboxError("qualification inspection container did not start")
        inspect_command = (docker, "inspect", name)
        inspected = _qualification_command(
            runner, inspect_command, logs, timeout=policy.timeout_seconds
        )
        if inspected.returncode != 0:
            raise SandboxError("qualification container inspection failed")
        _verify_container_inspect(inspected.stdout, policy, image_id)
    finally:
        _cleanup(runner, docker, name, logs=logs)


def _attestation_mapping(attestation: SandboxAttestation) -> dict[str, object]:
    value = asdict(attestation)
    value["cap_drop"] = list(attestation.cap_drop)
    value["published_ports"] = list(attestation.published_ports)
    value["host_mounts"] = list(attestation.host_mounts)
    return value


def _write_attestation(path: Path, attestation: SandboxAttestation) -> None:
    if not path.is_absolute():
        raise SandboxError("attestation path must be absolute")
    parent = path.parent.resolve(strict=True)
    if stat.S_IMODE(parent.stat().st_mode) & 0o077:
        raise SandboxError("attestation directory must not be group or world accessible")
    if path.is_symlink():
        raise SandboxError("attestation path must not be a symlink")
    temporary = parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        payload = json.dumps(
            _attestation_mapping(attestation), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        path.chmod(0o600)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _qualify_sandbox(
    policy: SandboxPolicy,
    *,
    attestation_revision: str,
    runtime: SandboxRuntime,
    attestation_path: Path | None = None,
    logs: list[SandboxQualificationLog],
) -> SandboxQualification:
    runner = runtime.runner or cast(_Runner, subprocess.run)
    docker = _docker_path(runtime.docker_executable)
    image_command = (docker, "image", "inspect", policy.image_reference)
    image = _qualification_command(runner, image_command, logs, timeout=policy.timeout_seconds)
    if image.returncode != 0:
        raise SandboxError("qualified image is not present locally")
    image_id = _verify_image_inspect(image.stdout, policy)
    _inspect_qualification_container(policy, runtime, runner, logs, image_id)

    with tempfile.NamedTemporaryFile(prefix="racecraft-host-sentinel-", mode="wb") as sentinel:
        sentinel.write(secrets.token_bytes(32))
        sentinel.flush()
        main = _run_qualifier_probe(
            policy,
            runtime,
            runner,
            logs,
            case_id="qualification-boundary",
            mode=("--host-sentinel", sentinel.name),
        )
    if main is None:  # pragma: no cover - not an expected-timeout probe
        raise SandboxError("sandbox boundary probe produced no result")
    _verify_probe_report(main.stdout)

    canary_write = _run_qualifier_probe(
        policy,
        runtime,
        runner,
        logs,
        case_id="qualification-canary-write",
        mode=("--write-canary",),
    )
    if canary_write is None:  # pragma: no cover - not an expected-timeout probe
        raise SandboxError("sandbox canary write produced no result")
    _verify_simple_probe(canary_write.stdout, "canary_written")
    canary_absent = _run_qualifier_probe(
        policy,
        runtime,
        runner,
        logs,
        case_id="qualification-canary-reset",
        mode=("--assert-canary-absent",),
    )
    if canary_absent is None:  # pragma: no cover - not an expected-timeout probe
        raise SandboxError("sandbox canary reset produced no result")
    _verify_simple_probe(canary_absent.stdout, "canary_absent")

    _run_qualifier_probe(
        policy,
        runtime,
        runner,
        logs,
        case_id="qualification-timeout",
        mode=("--hang",),
        expect_timeout=True,
    )
    flood = _run_qualifier_probe(
        policy,
        runtime,
        runner,
        logs,
        case_id="qualification-output",
        mode=("--flood", str(policy.max_output_bytes + 1)),
    )
    if flood is None:  # pragma: no cover - not an expected-timeout probe
        raise SandboxError("sandbox output probe produced no result")
    _, output_bytes, output_cut = _bounded(flood.stdout, policy.max_output_bytes)
    if output_bytes != policy.max_output_bytes + 1 or not output_cut:
        raise SandboxError("sandbox output bound was not exercised exactly")

    evidence = _attestation_expectations(policy)
    evidence["attestation_revision"] = attestation_revision
    evidence["cap_drop"] = ["ALL"]
    evidence["published_ports"] = []
    evidence["host_mounts"] = []
    attestation = validate_attestation(evidence, policy)
    if attestation_path is not None:
        _write_attestation(attestation_path, attestation)
    return SandboxQualification(attestation, tuple(logs))


def qualify_sandbox(
    policy: SandboxPolicy,
    *,
    attestation_revision: str,
    runtime: SandboxRuntime = _DEFAULT_RUNTIME,
    attestation_path: Path | None = None,
) -> SandboxQualification:
    """Exercise and inspect the real Docker boundary before emitting an attestation."""

    logs: list[SandboxQualificationLog] = []
    try:
        return _qualify_sandbox(
            policy,
            attestation_revision=attestation_revision,
            runtime=runtime,
            attestation_path=attestation_path,
            logs=logs,
        )
    except (OSError, subprocess.SubprocessError, SandboxError) as error:
        raise SandboxQualificationError(str(error), tuple(logs)) from error


def public_qualification_evidence(
    qualification: SandboxQualification,
) -> dict[str, object]:
    """Return a sanitized qualification summary without commands or raw output."""

    attestation = qualification.attestation
    return {
        "schema_version": 1,
        "qualified": attestation.qualified,
        "attestation_revision": attestation.attestation_revision,
        "image_digest": attestation.image_digest,
        "policy_sha256": attestation.policy_sha256,
        "qualification_event_count": len(qualification.private_logs),
    }


def _timeout_result(
    case_id: str, error: subprocess.TimeoutExpired, policy: SandboxPolicy, duration_ms: int
) -> SandboxResult:
    stdout, stdout_bytes, stdout_cut = _bounded(error.stdout, policy.max_output_bytes)
    stderr, stderr_bytes, stderr_cut = _bounded(error.stderr, policy.max_output_bytes)
    return SandboxResult(
        case_id=case_id,
        status="timed_out",
        exit_code=None,
        timed_out=True,
        protocol_valid=False,
        stdout=stdout,
        stderr=stderr,
        output_truncated=stdout_cut or stderr_cut,
        stdout_bytes=stdout_bytes,
        stderr_bytes=stderr_bytes,
        duration_ms=duration_ms,
    )


def _completed_result(
    case_id: str,
    completed: subprocess.CompletedProcess[bytes],
    policy: SandboxPolicy,
    duration_ms: int,
) -> SandboxResult:
    stdout, stdout_bytes, stdout_cut = _bounded(completed.stdout, policy.max_output_bytes)
    stderr, stderr_bytes, stderr_cut = _bounded(completed.stderr, policy.max_output_bytes)
    report_passed = _grade_report_passed(stdout)
    protocol_valid = report_passed is not None
    return SandboxResult(
        case_id=case_id,
        status="passed" if completed.returncode == 0 and report_passed is True else "failed",
        exit_code=completed.returncode,
        timed_out=False,
        protocol_valid=protocol_valid,
        stdout=stdout,
        stderr=stderr,
        output_truncated=stdout_cut or stderr_cut,
        stdout_bytes=stdout_bytes,
        stderr_bytes=stderr_bytes,
        duration_ms=duration_ms,
    )


def _grade_report_passed(raw: bytes) -> bool | None:
    try:
        report = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(report, dict) or frozenset(report) != {
        "schema_version",
        "passed",
        "summary",
        "tests",
    }:
        return None
    summary = report["summary"]
    tests = report["tests"]
    if (
        type(report["schema_version"]) is not int
        or report["schema_version"] != 1
        or type(report["passed"]) is not bool
        or not isinstance(summary, dict)
        or frozenset(summary) != {"passed", "total"}
        or not isinstance(tests, list)
        or not tests
    ):
        return None
    valid_tests = all(
        isinstance(test, dict)
        and frozenset(test) == {"name", "passed", "timed_out", "exit_code", "output_overflow"}
        and isinstance(test["name"], str)
        and type(test["passed"]) is bool
        and type(test["timed_out"]) is bool
        and (test["exit_code"] is None or type(test["exit_code"]) is int)
        and type(test["output_overflow"]) is bool
        for test in tests
    )
    passed_count = sum(test["passed"] is True for test in tests) if valid_tests else -1
    protocol_valid = (
        valid_tests
        and type(summary["passed"]) is int
        and type(summary["total"]) is int
        and summary == {"passed": passed_count, "total": len(tests)}
        and report["passed"] is (passed_count == len(tests))
    )
    return report["passed"] if protocol_valid else None


def run_coding_case(
    policy: SandboxPolicy,
    attestation_evidence: Mapping[str, Any],
    *,
    case_id: str,
    request: Mapping[str, Any],
    runtime: SandboxRuntime = _DEFAULT_RUNTIME,
) -> SandboxResult:
    """Run one private grading request in one fresh, attested container."""

    payload = json.dumps(request, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(payload) > policy.max_input_bytes:
        raise SandboxError("grading input exceeds max_input_bytes")
    validate_attestation(attestation_evidence, policy)
    selected_runner = runtime.runner or cast(_Runner, subprocess.run)
    nonce = runtime.nonce_factory()
    args = build_docker_run_args(
        policy,
        case_id=case_id,
        nonce=nonce,
        docker_executable=runtime.docker_executable,
    )
    docker = args[0]
    name = _container_name(case_id, nonce)
    started = runtime.clock()
    completed: subprocess.CompletedProcess[bytes] | None = None
    timeout_error: subprocess.TimeoutExpired | None = None
    try:
        try:
            completed = selected_runner(
                args,
                input=payload,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=policy.timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            timeout_error = error
    finally:
        _cleanup(selected_runner, docker, name)
    duration_ms = max(0, int((runtime.clock() - started) * 1000))
    if timeout_error is not None:
        return _timeout_result(case_id, timeout_error, policy, duration_ms)
    if completed is None:  # pragma: no cover - defensive invariant
        raise SandboxError("sandbox runner produced no result")
    return _completed_result(case_id, completed, policy, duration_ms)


def public_result_evidence(result: SandboxResult, policy: SandboxPolicy) -> dict[str, object]:
    """Return aggregate-safe evidence with no request, stdout, or stderr content."""

    return {
        "schema_version": 1,
        "case_id": result.case_id,
        "status": result.status,
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "protocol_valid": result.protocol_valid,
        "output_truncated": result.output_truncated,
        "stdout_bytes": result.stdout_bytes,
        "stderr_bytes": result.stderr_bytes,
        "duration_ms": result.duration_ms,
        "image_digest": policy.image_digest,
        "policy_sha256": policy.sha256,
    }
