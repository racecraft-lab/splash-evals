from __future__ import annotations

import json
import runpy
import subprocess
from collections.abc import Callable, Sequence
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest

from local_evals.sandbox import (
    SandboxError,
    SandboxPolicy,
    SandboxQualification,
    SandboxQualificationError,
    SandboxRuntime,
    build_docker_run_args,
    public_qualification_evidence,
    public_result_evidence,
    qualify_sandbox,
    run_coding_case,
    validate_attestation,
)

_DIGEST = "sha256:" + ("a" * 64)
_IMAGE = f"racecraft/splash-coding-grader@{_DIGEST}"
_QUALIFICATION_CHECKS = {
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


def _paths_absent_probe() -> object:
    namespace = runpy.run_path("sandbox/coding/qualify.py", run_name="sandbox_qualify_test")
    return namespace["_paths_absent"]


def _policy(**overrides: object) -> SandboxPolicy:
    values: dict[str, object] = {
        "image_reference": _IMAGE,
        "image_digest": _DIGEST,
        "timeout_seconds": 5.0,
        "max_input_bytes": 4_096,
        "max_output_bytes": 4_096,
        "memory_bytes": 536_870_912,
        "cpus": "1.0",
        "pids_limit": 32,
        "nofile_limit": 64,
        "tmpfs_bytes": 67_108_864,
    }
    values.update(overrides)
    return SandboxPolicy(**values)  # type: ignore[arg-type]


def _attestation(policy: SandboxPolicy, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "schema_version": 1,
        "attestation_revision": "sandbox-v1",
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
        "cap_drop": ["ALL"],
        "no_new_privileges": True,
        "pids_limit": policy.pids_limit,
        "memory_bytes": policy.memory_bytes,
        "cpus": policy.cpus,
        "nofile_limit": policy.nofile_limit,
        "tmpfs_bytes": policy.tmpfs_bytes,
        "published_ports": [],
        "host_mounts": [],
        "host_home_mounted": False,
        "docker_socket_mounted": False,
        "secrets_present": False,
    }
    values.update(overrides)
    return values


class _Runner:
    def __init__(
        self,
        *,
        completed: subprocess.CompletedProcess[bytes] | None = None,
        failure: BaseException | None = None,
    ) -> None:
        self.completed = completed or subprocess.CompletedProcess(
            [],
            0,
            json.dumps(
                {
                    "schema_version": 1,
                    "passed": True,
                    "summary": {"passed": 1, "total": 1},
                    "tests": [
                        {
                            "name": "example",
                            "passed": True,
                            "timed_out": False,
                            "exit_code": 0,
                            "output_overflow": False,
                        }
                    ],
                }
            ).encode(),
            b"",
        )
        self.failure = failure
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def __call__(self, args: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        self.calls.append((tuple(args), dict(kwargs)))
        if len(self.calls) == 1 and self.failure is not None:
            raise self.failure
        if len(self.calls) > 1:
            return subprocess.CompletedProcess(args, 0, b"", b"")
        return self.completed


def _runtime(runner: _Runner) -> SandboxRuntime:
    return SandboxRuntime(
        docker_executable=Path("/usr/local/bin/docker"),
        runner=runner,
        nonce_factory=lambda: "0123456789abcdef",
    )


class _QualificationRunner(_Runner):
    def __init__(
        self,
        policy: SandboxPolicy,
        *,
        container_overrides: dict[str, object] | None = None,
        image_overrides: dict[str, object] | None = None,
        failed_check: str | None = None,
        cleanup_returncode: int = 0,
    ) -> None:
        super().__init__()
        self.policy = policy
        self.container_overrides = container_overrides or {}
        self.image_overrides = image_overrides or {}
        self.failed_check = failed_check
        self.cleanup_returncode = cleanup_returncode

    def _container_inspect(self) -> bytes:
        value: dict[str, object] = {
            "Image": _DIGEST,
            "State": {"Running": True},
            "Config": {
                "User": self.policy.user,
                "WorkingDir": self.policy.workdir,
                "ExposedPorts": None,
            },
            "HostConfig": {
                "NetworkMode": "none",
                "ReadonlyRootfs": True,
                "Privileged": False,
                "AutoRemove": True,
                "CapDrop": ["ALL"],
                "CapAdd": None,
                "SecurityOpt": ["no-new-privileges"],
                "PidsLimit": self.policy.pids_limit,
                "Memory": self.policy.memory_bytes,
                "MemorySwap": self.policy.memory_bytes,
                "NanoCpus": int(float(self.policy.cpus) * 1_000_000_000),
                "Ulimits": [
                    {
                        "Name": "nofile",
                        "Soft": self.policy.nofile_limit,
                        "Hard": self.policy.nofile_limit,
                    }
                ],
                "Tmpfs": {
                    "/tmp": (  # noqa: S108 - expected isolated container tmpfs.
                        f"rw,noexec,nosuid,nodev,size={self.policy.tmpfs_bytes}"
                    )
                },
                "PortBindings": {},
                "PublishAllPorts": False,
                "Binds": None,
                "Mounts": [],
                "VolumesFrom": None,
                "Devices": [],
                "DeviceRequests": [],
                "ExtraHosts": None,
            },
            "Mounts": [
                {
                    "Type": "tmpfs",
                    "Destination": "/tmp",  # noqa: S108 - expected container tmpfs.
                }
            ],
        }
        for dotted_key, replacement in self.container_overrides.items():
            target: dict[str, object] = value
            parts = dotted_key.split(".")
            for part in parts[:-1]:
                child = target[part]
                assert isinstance(child, dict)
                target = child
            target[parts[-1]] = replacement
        return json.dumps([value]).encode()

    def __call__(self, args: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        command = tuple(args)
        self.calls.append((command, dict(kwargs)))
        if command[1:3] == ("image", "inspect"):
            payload = [
                {
                    "Id": _DIGEST,
                    "RepoDigests": [_IMAGE],
                    "Os": "linux",
                    "Architecture": "arm64",
                }
            ]
            payload[0].update(self.image_overrides)
            return subprocess.CompletedProcess(args, 0, json.dumps(payload).encode(), b"")
        if command[1] == "inspect":
            return subprocess.CompletedProcess(args, 0, self._container_inspect(), b"")
        if command[1] == "rm":
            return subprocess.CompletedProcess(args, self.cleanup_returncode, b"", b"")
        if command[1] != "run":
            raise AssertionError(command)
        if "--detach" in command:
            return subprocess.CompletedProcess(args, 0, b"container-id\n", b"")
        if "--hang" in command:
            raise subprocess.TimeoutExpired(args, timeout=float(kwargs["timeout"]))
        if "--flood" in command:
            count = int(command[command.index("--flood") + 1])
            return subprocess.CompletedProcess(args, 0, b"x" * count, b"")
        if "--write-canary" in command:
            payload = {"schema_version": 1, "passed": True, "probe": "canary_written"}
            return subprocess.CompletedProcess(args, 0, json.dumps(payload).encode(), b"")
        if "--assert-canary-absent" in command:
            payload = {"schema_version": 1, "passed": True, "probe": "canary_absent"}
            return subprocess.CompletedProcess(args, 0, json.dumps(payload).encode(), b"")
        sentinel = command[command.index("--host-sentinel") + 1]
        assert Path(sentinel).is_file()
        checks = [
            {"name": name, "passed": name != self.failed_check}
            for name in sorted(_QUALIFICATION_CHECKS)
        ]
        payload = {
            "schema_version": 1,
            "passed": self.failed_check is None,
            "checks": checks,
        }
        return subprocess.CompletedProcess(
            args, 0 if self.failed_check is None else 1, json.dumps(payload).encode(), b""
        )


def test_policy_requires_exact_digest_binding() -> None:
    with pytest.raises(SandboxError, match="must end with the exact image digest"):
        _policy(image_reference="racecraft/grader:latest")

    with pytest.raises(SandboxError, match="lowercase sha256"):
        _policy(image_digest="sha256:not-a-digest")

    digest_only = _policy(image_reference=_DIGEST)
    assert digest_only.image_reference == digest_only.image_digest


def test_build_args_enforce_every_isolation_control_without_mounts_or_ports() -> None:
    policy = _policy()
    args = build_docker_run_args(
        policy,
        case_id="case-01",
        nonce="0123456789abcdef",
        docker_executable=Path("/usr/local/bin/docker"),
    )

    assert args == (
        "/usr/local/bin/docker",
        "run",
        "--rm",
        "--name",
        "racecraft-grade-case-01-0123456789abcdef",
        "--platform",
        "linux/arm64/v8",
        "--pull",
        "never",
        "--network",
        "none",
        "--read-only",
        "--user",
        "65532:65532",
        "--workdir",
        "/work",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges=true",
        "--pids-limit",
        "32",
        "--memory",
        "536870912",
        "--memory-swap",
        "536870912",
        "--cpus",
        "1.0",
        "--ulimit",
        "nofile=64:64",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=67108864",  # noqa: S108
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        "--env",
        "PYTHONUNBUFFERED=1",
        "--interactive",
        "--entrypoint",
        "/usr/local/bin/python",
        _IMAGE,
        "-I",
        "-B",
        "/opt/racecraft/grade.py",
    )
    forbidden = {"--volume", "-v", "--mount", "--publish", "-p", "--env-file"}
    assert forbidden.isdisjoint(args)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("qualified", 1),
        ("attestation_revision", "../escape"),
        ("container_runtime", "podman"),
        ("platform", "linux/amd64"),
        ("image_reference", f"racecraft/other@{_DIGEST}"),
        ("image_digest", "sha256:" + ("b" * 64)),
        ("policy_sha256", "b" * 64),
        ("disposable", False),
        ("fresh_container_per_case", False),
        ("network_mode", "bridge"),
        ("network_disabled", False),
        ("non_root_user", "0:0"),
        ("read_only_rootfs", False),
        ("cap_drop", []),
        ("no_new_privileges", False),
        ("pids_limit", 33),
        ("memory_bytes", 536_870_911),
        ("cpus", "2.0"),
        ("nofile_limit", 65),
        ("tmpfs_bytes", 67_108_863),
        ("published_ports", ["8080:80"]),
        ("host_mounts", ["/host-home/example:/work"]),
        ("host_home_mounted", True),
        ("docker_socket_mounted", True),
        ("secrets_present", True),
    ],
)
def test_attestation_rejects_adversarial_fields(field: str, value: object) -> None:
    policy = _policy()
    with pytest.raises(SandboxError, match="attestation"):
        validate_attestation(_attestation(policy, **{field: value}), policy)


def test_attestation_rejects_unknown_fields() -> None:
    policy = _policy()
    with pytest.raises(SandboxError, match="unexpected fields"):
        validate_attestation(_attestation(policy, invented_proof=True), policy)


def test_run_passes_request_on_stdin_without_shell_interpolation_and_tears_down() -> None:
    policy = _policy()
    runner = _Runner()
    request = {"schema_version": 1, "source": "$(touch /tmp/pwned); `id`", "tests": []}

    result = run_coding_case(
        policy,
        _attestation(policy),
        case_id="case-01",
        request=request,
        runtime=_runtime(runner),
    )

    assert result.status == "passed"
    assert result.protocol_valid is True
    assert len(runner.calls) == 2
    run_args, run_kwargs = runner.calls[0]
    assert "$(touch /tmp/pwned); `id`" not in run_args
    assert json.loads(bytes(run_kwargs["input"])) == request
    assert "shell" not in run_kwargs
    assert runner.calls[1][0] == (
        "/usr/local/bin/docker",
        "rm",
        "--force",
        "racecraft-grade-case-01-0123456789abcdef",
    )


def test_runner_tears_down_when_docker_raises() -> None:
    policy = _policy()
    runner = _Runner(failure=RuntimeError("docker failed"))

    with pytest.raises(RuntimeError, match="docker failed"):
        run_coding_case(
            policy,
            _attestation(policy),
            case_id="case-01",
            request={"schema_version": 1},
            runtime=_runtime(runner),
        )

    assert len(runner.calls) == 2
    assert runner.calls[1][0][1:] == (
        "rm",
        "--force",
        "racecraft-grade-case-01-0123456789abcdef",
    )


def test_cleanup_failure_refuses_to_return_a_result() -> None:
    policy = _policy()

    class CleanupFailureRunner(_Runner):
        def __call__(
            self, args: Sequence[str], **kwargs: object
        ) -> subprocess.CompletedProcess[bytes]:
            self.calls.append((tuple(args), dict(kwargs)))
            if len(self.calls) == 1:
                return subprocess.CompletedProcess(args, 0, b"ok", b"")
            return subprocess.CompletedProcess(args, 125, b"", b"daemon unavailable")

    with pytest.raises(SandboxError, match="could not prove container teardown"):
        run_coding_case(
            policy,
            _attestation(policy),
            case_id="case-01",
            request={"schema_version": 1},
            runtime=_runtime(CleanupFailureRunner()),
        )


def test_timeout_and_output_are_bounded() -> None:
    policy = _policy(max_output_bytes=4)
    timeout = subprocess.TimeoutExpired(["docker"], timeout=5.0, output=b"abcdef", stderr=b"ghijkl")
    runner = _Runner(failure=timeout)

    result = run_coding_case(
        policy,
        _attestation(policy),
        case_id="timeout",
        request={"schema_version": 1},
        runtime=_runtime(runner),
    )

    assert result.status == "timed_out"
    assert result.protocol_valid is False
    assert result.stdout == b"abcd"
    assert result.stderr == b"ghij"
    assert result.output_truncated is True
    assert runner.calls[0][1]["timeout"] == 5.0


def test_public_evidence_never_contains_raw_output_or_request() -> None:
    policy = _policy(max_output_bytes=16)
    runner = _Runner(
        completed=subprocess.CompletedProcess([], 1, b"private answer", b"private traceback")
    )
    result = run_coding_case(
        policy,
        _attestation(policy),
        case_id="case-01",
        request={"schema_version": 1, "source": "private source"},
        runtime=_runtime(runner),
    )

    evidence = public_result_evidence(result, policy)
    serialized = json.dumps(evidence, sort_keys=True)
    assert "private" not in serialized
    assert evidence == {
        "schema_version": 1,
        "case_id": "case-01",
        "status": "failed",
        "exit_code": 1,
        "timed_out": False,
        "protocol_valid": False,
        "output_truncated": True,
        "stdout_bytes": 14,
        "stderr_bytes": 17,
        "duration_ms": 0,
        "image_digest": _DIGEST,
        "policy_sha256": policy.sha256,
    }


def test_zero_exit_with_malformed_grader_output_fails_closed() -> None:
    policy = _policy()
    runner = _Runner(completed=subprocess.CompletedProcess([], 0, b"not-json", b""))

    result = run_coding_case(
        policy,
        _attestation(policy),
        case_id="case-01",
        request={"schema_version": 1},
        runtime=_runtime(runner),
    )

    assert result.status == "failed"
    assert result.protocol_valid is False


def test_zero_exit_cannot_override_a_valid_failed_grade_report() -> None:
    policy = _policy()
    report = {
        "schema_version": 1,
        "passed": False,
        "summary": {"passed": 0, "total": 1},
        "tests": [
            {
                "name": "failed",
                "passed": False,
                "timed_out": False,
                "exit_code": 1,
                "output_overflow": False,
            }
        ],
    }
    runner = _Runner(completed=subprocess.CompletedProcess([], 0, json.dumps(report).encode(), b""))

    result = run_coding_case(
        policy,
        _attestation(policy),
        case_id="case-01",
        request={"schema_version": 1},
        runtime=_runtime(runner),
    )

    assert result.status == "failed"
    assert result.protocol_valid is True


@pytest.mark.parametrize("case_id", ["../escape", "$(id)", "with space", "a" * 65])
def test_case_id_is_fail_closed(case_id: str) -> None:
    with pytest.raises(SandboxError, match="case_id"):
        build_docker_run_args(_policy(), case_id=case_id, nonce="0123456789abcdef")


def test_request_size_is_bounded_before_docker_starts() -> None:
    policy = _policy(max_input_bytes=8)
    runner = _Runner()
    with pytest.raises(SandboxError, match="input exceeds"):
        run_coding_case(
            policy,
            _attestation(policy),
            case_id="case-01",
            request={"source": "too large"},
            runtime=_runtime(runner),
        )
    assert runner.calls == []


def test_grader_entrypoint_uses_only_the_standard_library() -> None:
    sources = [
        Path("sandbox/coding/grade.py").read_text(encoding="utf-8"),
        Path("sandbox/coding/qualify.py").read_text(encoding="utf-8"),
    ]
    forbidden_imports = ("pytest", "numpy", "pandas", "requests", "evalscope")
    assert all(f"import {name}" not in source for source in sources for name in forbidden_imports)


def test_grading_image_is_digest_pinned_non_root_and_dependency_free() -> None:
    dockerfile = Path("sandbox/coding/Dockerfile").read_text(encoding="utf-8")
    assert (
        "FROM --platform=linux/arm64/v8 python:3.11-slim-bookworm@sha256:"
        "bbc491ed39611eede47b1058ad4afb9ea957fb3bf4a7f1b442c6a5628ab93bdc"
    ) in dockerfile
    assert "USER 65532:65532" in dockerfile
    assert "RUN " not in dockerfile
    assert "pip install" not in dockerfile
    assert "apt-get" not in dockerfile


def test_docker_build_context_is_closed_to_only_the_grader_inputs() -> None:
    ignore = Path("sandbox/coding/.dockerignore").read_text(encoding="utf-8").splitlines()
    assert ignore == ["*", "!Dockerfile", "!grade.py", "!qualify.py"]


@pytest.mark.parametrize("error_type", [PermissionError, OSError])
def test_sensitive_path_probe_treats_inaccessible_path_as_unobservable(
    monkeypatch: pytest.MonkeyPatch, error_type: type[OSError]
) -> None:
    probe = cast("Callable[[tuple[str, ...]], bool]", _paths_absent_probe())

    def inaccessible(_path: Path) -> bool:
        raise error_type("not observable")

    monkeypatch.setattr(Path, "exists", inaccessible)
    assert probe(("/root/.ssh",)) is True


def test_sensitive_path_probe_rejects_observable_existing_path(tmp_path: Path) -> None:
    probe = cast("Callable[[tuple[str, ...]], bool]", _paths_absent_probe())
    visible = tmp_path / "visible-sensitive-path"
    visible.write_text("observable", encoding="utf-8")

    assert probe((str(visible),)) is False


def test_qualification_exercises_all_probes_and_writes_private_attestation(
    tmp_path: Path,
) -> None:
    policy = _policy()
    runner = _QualificationRunner(policy)
    state = tmp_path / "private-state"
    state.mkdir(mode=0o700)
    target = state / "sandbox-attestation.json"

    qualification = qualify_sandbox(
        policy,
        attestation_revision="sandbox-v1",
        runtime=_runtime(runner),
        attestation_path=target,
    )

    assert isinstance(qualification, SandboxQualification)
    assert qualification.attestation.qualified is True
    assert qualification.attestation.image_digest == _DIGEST
    assert target.stat().st_mode & 0o777 == 0o600
    persisted = json.loads(target.read_text(encoding="utf-8"))
    assert persisted["qualified"] is True
    assert persisted["cap_drop"] == ["ALL"]
    commands = [call[0] for call in runner.calls]
    run_commands = [command for command in commands if command[1] == "run"]
    assert len(run_commands) == 6
    assert all("--network" in command and "none" in command for command in run_commands)
    assert all("--mount" not in command and "--publish" not in command for command in run_commands)
    assert any("--detach" in command for command in run_commands)
    assert any("--hang" in command for command in run_commands)
    assert any("--flood" in command for command in run_commands)
    assert any("--write-canary" in command for command in run_commands)
    assert any("--assert-canary-absent" in command for command in run_commands)
    names = [command[command.index("--name") + 1] for command in run_commands]
    assert len(names) == len(set(names))
    boundary = next(command for command in run_commands if "--host-sentinel" in command)
    assert not Path(boundary[boundary.index("--host-sentinel") + 1]).exists()
    assert sum(command[1] == "rm" for command in commands) == 6
    assert any(log.timed_out for log in qualification.private_logs)

    public = public_qualification_evidence(qualification)
    serialized = json.dumps(public, sort_keys=True)
    assert "command" not in serialized
    assert "stdout" not in serialized
    assert "stderr" not in serialized
    assert public["qualified"] is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("HostConfig.NetworkMode", "bridge"),
        ("HostConfig.ReadonlyRootfs", False),
        ("HostConfig.Privileged", True),
        ("HostConfig.CapDrop", []),
        ("HostConfig.PidsLimit", 999),
        ("HostConfig.Memory", 1),
        ("HostConfig.NanoCpus", 2_000_000_000),
        ("HostConfig.PortBindings", {"80/tcp": [{"HostPort": "8080"}]}),
        ("HostConfig.Binds", ["/host-home:/host"]),
        ("HostConfig.ExtraHosts", ["host.docker.internal:host-gateway"]),
        ("Config.User", "0:0"),
        ("Mounts", [{"Type": "bind", "Destination": "/host"}]),
    ],
)
def test_qualification_rejects_adversarial_container_inspection(field: str, value: object) -> None:
    policy = _policy()
    runner = _QualificationRunner(policy, container_overrides={field: deepcopy(value)})

    with pytest.raises(SandboxError, match="inspection does not match"):
        qualify_sandbox(
            policy,
            attestation_revision="sandbox-v1",
            runtime=_runtime(runner),
        )

    assert any(call[0][1] == "rm" for call in runner.calls)


@pytest.mark.parametrize("failed_check", sorted(_QUALIFICATION_CHECKS))
def test_each_qualification_probe_failure_never_materializes_attestation(
    tmp_path: Path, failed_check: str
) -> None:
    policy = _policy()
    runner = _QualificationRunner(policy, failed_check=failed_check)
    state = tmp_path / "private-state"
    state.mkdir(mode=0o700)
    target = state / "sandbox-attestation.json"

    with pytest.raises(
        SandboxQualificationError, match="probe qualification-boundary failed"
    ) as caught:
        qualify_sandbox(
            policy,
            attestation_revision="sandbox-v1",
            runtime=_runtime(runner),
            attestation_path=target,
        )

    assert not target.exists()
    assert caught.value.private_logs
    assert any(log.command[1] == "rm" for log in caught.value.private_logs)
    boundary_runs = [
        call[0] for call in runner.calls if call[0][1] == "run" and "--detach" not in call[0]
    ]
    assert boundary_runs
    assert any(call[0][1] == "rm" for call in runner.calls)


@pytest.mark.parametrize(
    "overrides",
    [
        {"Architecture": "amd64"},
        {"Id": "sha256:" + ("b" * 64), "RepoDigests": []},
        {"Os": "windows"},
    ],
)
def test_qualification_rejects_wrong_image_identity_or_platform(
    overrides: dict[str, object],
) -> None:
    policy = _policy()
    runner = _QualificationRunner(policy, image_overrides=overrides)

    with pytest.raises(SandboxError, match="exact policy image and platform"):
        qualify_sandbox(
            policy,
            attestation_revision="sandbox-v1",
            runtime=_runtime(runner),
        )


def test_qualification_cleanup_failure_blocks_attestation(tmp_path: Path) -> None:
    policy = _policy()
    runner = _QualificationRunner(policy, cleanup_returncode=125)
    state = tmp_path / "private-state"
    state.mkdir(mode=0o700)
    target = state / "sandbox-attestation.json"

    with pytest.raises(SandboxError, match="could not prove container teardown"):
        qualify_sandbox(
            policy,
            attestation_revision="sandbox-v1",
            runtime=_runtime(runner),
            attestation_path=target,
        )

    assert not target.exists()


def test_qualification_refuses_public_attestation_directory(tmp_path: Path) -> None:
    policy = _policy()
    runner = _QualificationRunner(policy)
    state = tmp_path / "public-state"
    state.mkdir(mode=0o755)

    with pytest.raises(SandboxError, match="must not be group or world accessible"):
        qualify_sandbox(
            policy,
            attestation_revision="sandbox-v1",
            runtime=_runtime(runner),
            attestation_path=state / "attestation.json",
        )
