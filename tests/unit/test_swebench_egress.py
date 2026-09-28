"""Grader egress gateway: argument, attestation, probe and profile gates (synthetic Docker)."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from local_evals import swebench as swebench_module
from local_evals.swebench import SwebenchError, SwebenchRuntime

IMAGE_ID = "sha256:" + "b" * 64
GATEWAY_ID = "c" * 64
GATEWAY = "swebench-egress-0123456789abcdef"
HOSTS = ["httpbin.org", "www.google.com"]
LIMITS = {
    "timeout_seconds": 600,
    "max_output_bytes": 1_048_576,
    "pids_limit": 256,
    "memory_bytes": 4_294_967_296,
}


def _gateway_inspect(**overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "Id": GATEWAY_ID,
        "State": {"Running": True},
        "Config": {
            "Image": IMAGE_ID,
            "User": "",
            "Env": [
                "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                f"ALLOW={','.join(HOSTS)}",
            ],
            "ExposedPorts": None,
        },
        "HostConfig": {
            "Privileged": False,
            "CapDrop": ["ALL"],
            "CapAdd": ["NET_ADMIN", "NET_RAW", "SETGID", "SETUID"],
            "NetworkMode": "swebench-egress-out",
            "Sysctls": {"net.ipv6.conf.all.disable_ipv6": "1"},
            "Binds": None,
            "PortBindings": {},
            "Tmpfs": {"/tmp": ""},  # noqa: S108 - synthetic container tmpfs
        },
        "NetworkSettings": {"Networks": {"swebench-egress-out": {}}},
        "Mounts": [],
    }
    for path, value in overrides.items():
        target = item
        keys = path.split(".")
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = value
    return item


class _GatewayRunner:
    def __init__(
        self,
        *,
        inspect: dict[str, Any] | None = None,
        probe_stdout: bytes = b"PASS running as uid 65532\nINFO note\n",
        probe_code: int = 0,
        logs: bytes = b"dnsmasq[1]: started, version 2.90\negress_proxy: started allow=\n",
    ) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.inspect = inspect or _gateway_inspect()
        self.probe_stdout = probe_stdout
        self.probe_code = probe_code
        self.logs = logs

    def __call__(self, args: Sequence[str], **_kwargs: Any) -> subprocess.CompletedProcess[Any]:
        command = tuple(args)
        self.calls.append(command)
        if command[1] == "logs":
            return subprocess.CompletedProcess(command, 0, self.logs, b"")
        if command[1] == "inspect":
            return subprocess.CompletedProcess(command, 0, json.dumps([self.inspect]).encode(), b"")
        if command[1:3] == ("run", "--rm"):
            return subprocess.CompletedProcess(command, self.probe_code, self.probe_stdout, b"")
        return subprocess.CompletedProcess(command, 0, b"", b"")


def _runtime(runner: _GatewayRunner) -> SwebenchRuntime:
    return SwebenchRuntime(docker_executable=Path("/usr/local/bin/docker"), runner=runner)


class _Prepared:
    profile = {"grader_egress": {"gateway_image_id": IMAGE_ID}}


def test_gateway_args_grant_only_the_frozen_capabilities() -> None:
    args = swebench_module.build_gateway_container_args(
        image_id=IMAGE_ID, hosts=HOSTS, name=GATEWAY
    )
    caps = {args[i + 1] for i, value in enumerate(args) if value == "--cap-add"}
    assert caps == {"NET_ADMIN", "NET_RAW", "SETUID", "SETGID"}
    assert args[args.index("--cap-drop") + 1] == "ALL"
    assert args[args.index("--network") + 1] == "swebench-egress-out"
    assert "ALLOW=httpbin.org,www.google.com" in args
    assert "--privileged" not in args and "-v" not in args and "--volume" not in args
    assert args[-1] == IMAGE_ID


@pytest.mark.parametrize(
    ("image_id", "hosts", "name"),
    [
        ("alpine:3.20", HOSTS, GATEWAY),
        (IMAGE_ID, ["httpbin.org,example.com"], GATEWAY),
        (IMAGE_ID, ["-bad.example"], GATEWAY),
        (IMAGE_ID, HOSTS, "swebench-grader-0123456789abcdef"),
    ],
)
def test_gateway_args_reject_unsafe_identity(image_id: str, hosts: list[str], name: str) -> None:
    with pytest.raises(SwebenchError):
        swebench_module.build_gateway_container_args(image_id=image_id, hosts=hosts, name=name)


def test_only_graders_may_join_a_gateway_namespace() -> None:
    common = {
        "image_reference": "local/racecraft-grader@sha256:" + "a" * 64,
        "image_digest": "sha256:" + "a" * 64,
        "platform": "linux/amd64",
        "limits": {
            "pids_limit": 256,
            "memory_bytes": 4_294_967_296,
            "cpus": "2.0",
            "nofile_limit": 1024,
            "tmpfs_bytes": 268_435_456,
            "timeout_seconds": 600,
            "max_output_bytes": 1_048_576,
            "max_turns": 10,
            "max_requests": 10,
            "max_task_output_tokens": 1024,
        },
    }
    try:
        grader = swebench_module.build_grader_container_args(
            **common,
            name="swebench-grader-0123456789abcdef",
            network_mode=f"container:{GATEWAY_ID}",
        )
    except SwebenchError as exc:  # limits schema drift would surface here, not silently pass
        pytest.fail(str(exc))
    assert grader[grader.index("--network") + 1] == f"container:{GATEWAY_ID}"
    with pytest.raises(SwebenchError):
        swebench_module.build_grader_container_args(
            **common, name="swebench-grader-0123456789abcdef", network_mode="host"
        )
    with pytest.raises(SwebenchError):
        swebench_module._container_args(
            **common,
            name="swebench-task-0123456789abcdef",
            role="task_image",
            writable_testbed=True,
            network_mode=f"container:{GATEWAY_ID}",
        )


def test_gateway_attestation_accepts_the_frozen_contract() -> None:
    runner = _GatewayRunner()
    gateway_id = swebench_module._attest_gateway(
        _runtime(runner), GATEWAY, LIMITS, image_id=IMAGE_ID, hosts=HOSTS
    )
    assert gateway_id == GATEWAY_ID


@pytest.mark.parametrize(
    "override",
    [
        {"State.Running": False},
        {"Config.Image": "sha256:" + "d" * 64},
        {"Config.Env": ["ALLOW=httpbin.org,www.google.com", "HTTPS_PROXY=x"]},
        {"Config.Env": ["ALLOW=httpbin.org"]},
        {"Config.User": "65532:65532"},
        {"HostConfig.CapAdd": ["NET_ADMIN", "NET_RAW", "SETGID", "SETUID", "SYS_ADMIN"]},
        {"HostConfig.Privileged": True},
        {"HostConfig.Binds": ["/:/host"]},
        {"HostConfig.NetworkMode": "host"},
        {"HostConfig.Sysctls": {}},
        {"NetworkSettings.Networks": {"swebench-egress-out": {}, "bridge": {}}},
        {"Mounts": [{"Type": "bind", "Source": "/", "Destination": "/host"}]},
    ],
)
def test_gateway_attestation_rejects_drift(override: dict[str, Any]) -> None:
    runner = _GatewayRunner(inspect=_gateway_inspect(**override))
    with pytest.raises(SwebenchError):
        swebench_module._attest_gateway(
            _runtime(runner), GATEWAY, LIMITS, image_id=IMAGE_ID, hosts=HOSTS
        )


def test_gateway_start_probes_as_grader_uid_and_root(tmp_path: Path) -> None:
    evidence = tmp_path / "run"
    evidence.mkdir(mode=0o700)
    runner = _GatewayRunner()
    gateway_id = swebench_module._start_egress_gateway(
        _runtime(runner),
        _Prepared(),  # type: ignore[arg-type]
        HOSTS,
        LIMITS,
        name=GATEWAY,
        evidence_dir=evidence,
        attempt_nonce="attempt",
    )
    assert gateway_id == GATEWAY_ID
    probes = [call for call in runner.calls if call[1:3] == ("run", "--rm")]
    assert [call[call.index("--user") + 1] for call in probes] == ["65532:65532", "0:0"]
    assert all(call[call.index("--network") + 1] == f"container:{GATEWAY_ID}" for call in probes)
    assert all(call[call.index("--cap-drop") + 1] == "ALL" for call in probes)
    assert len(list(evidence.glob("egress-probe-attempt-uid*.txt"))) == 2


@pytest.mark.parametrize(
    ("stdout", "code"),
    [
        (b"PASS a\nFAIL dropped 1.1.1.1:443 connected\n", 1),
        (b"PASS a\nFAIL something\n", 0),
        (b"PASS a\nunexpected line\n", 0),
        (b"", 0),
        (b"PASS a\n", 2),
    ],
)
def test_failed_probe_fails_closed(tmp_path: Path, stdout: bytes, code: int) -> None:
    evidence = tmp_path / "run"
    evidence.mkdir(mode=0o700)
    runner = _GatewayRunner(probe_stdout=stdout, probe_code=code)
    with pytest.raises(SwebenchError, match="probe failed"):
        swebench_module._start_egress_gateway(
            _runtime(runner),
            _Prepared(),  # type: ignore[arg-type]
            HOSTS,
            LIMITS,
            name=GATEWAY,
            evidence_dir=evidence,
            attempt_nonce="attempt",
        )


def test_gateway_that_never_becomes_ready_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(swebench_module, "_EGRESS_READY_SECONDS", 0)
    evidence = tmp_path / "run"
    evidence.mkdir(mode=0o700)
    runner = _GatewayRunner(logs=b"dnsmasq[1]: started\n")
    with pytest.raises(SwebenchError, match="did not become ready"):
        swebench_module._start_egress_gateway(
            _runtime(runner),
            _Prepared(),  # type: ignore[arg-type]
            HOSTS,
            LIMITS,
            name=GATEWAY,
            evidence_dir=evidence,
            attempt_nonce="attempt",
        )
    assert not any(call[1:3] == ("run", "--rm") for call in runner.calls)


def test_dead_gateway_after_grading_is_an_infrastructure_error() -> None:
    runner = _GatewayRunner(inspect=_gateway_inspect(**{"State.Running": False}))
    with pytest.raises(SwebenchError, match="stopped during grading"):
        swebench_module._require_gateway_running(_runtime(runner), GATEWAY, LIMITS, GATEWAY_ID)
    swebench_module._require_gateway_running(
        _runtime(_GatewayRunner()), GATEWAY, LIMITS, GATEWAY_ID
    )


def _egress_block(repo: Path, **overrides: Any) -> dict[str, Any]:
    block: dict[str, Any] = {
        "gateway_image_id": IMAGE_ID,
        "gateway_sources_sha256": swebench_module._egress_sources_sha256(repo),
        "network": "swebench-egress-out",
        "tasks": {
            "psf__requests-2317": ["httpbin.org", "www.google.com"],
            "psf__requests-2931": [],
        },
    }
    block.update(overrides)
    return block


def test_grader_egress_profile_block_validates() -> None:
    repo = Path.cwd()
    ids = {"psf__requests-2317", "psf__requests-2931", "astropy__astropy-12907"}
    swebench_module._validate_grader_egress(_egress_block(repo), repo, ids)


@pytest.mark.parametrize(
    "override",
    [
        {"network": "bridge"},
        {"gateway_sources_sha256": "0" * 64},
        {"gateway_image_id": "alpine:3.20"},
        {"tasks": {}},
        {"tasks": {"unknown__task-1": []}},
        {"tasks": {"psf__requests-2317": ["www.google.com", "httpbin.org"]}},
        {"tasks": {"psf__requests-2317": ["httpbin.org", "httpbin.org"]}},
        {"tasks": {"psf__requests-2317": ["*.google.com"]}},
    ],
)
def test_grader_egress_profile_block_rejects_drift(override: dict[str, Any]) -> None:
    repo = Path.cwd()
    ids = {"psf__requests-2317", "psf__requests-2931"}
    with pytest.raises(SwebenchError):
        swebench_module._validate_grader_egress(_egress_block(repo, **override), repo, ids)
