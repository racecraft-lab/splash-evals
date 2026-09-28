from __future__ import annotations

import base64
import errno
import hashlib
import json
import math
import re
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import yaml

import local_evals.swebench as swebench_module
from local_evals.swebench import (
    FULL_RUN_APPROVAL_MARKER,
    MINI_SWE_AGENT_VERSION,
    SWEBENCH_VERSION,
    SwebenchError,
    SwebenchRuntime,
    build_grader_container_args,
    build_task_container_args,
    execute_swebench,
    inspect_swebench_readiness,
    rescore_swebench,
    resume_swebench,
)

_TASK_DIGEST = "sha256:" + "1" * 64
_GRADER_DIGEST = "sha256:" + "2" * 64
_CONTROL_DIGEST = "sha256:" + "3" * 64


def _start_raw_http_server(writer: Any) -> tuple[str, Any]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = int(listener.getsockname()[1])
    finished = threading.Event()

    def serve() -> None:
        try:
            listener.settimeout(2)
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(2)
                request = bytearray()
                while b"\r\n\r\n" not in request:
                    chunk = connection.recv(4096)
                    if not chunk:
                        return
                    request.extend(chunk)
                writer(connection)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            listener.close()
            finished.set()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    def stop() -> None:
        try:
            listener.close()
        except OSError:
            pass
        thread.join(timeout=2)
        assert finished.is_set()

    return f"http://127.0.0.1:{port}/v1", stop


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest(state: Path, count: int = 2, *, mode: str = "qualification") -> tuple[Path, str]:
    verified_ids = [f"verified-{index}" for index in range(500)]
    instance_ids = (
        verified_ids if mode == "verified" else [f"qualification-{index}" for index in range(count)]
    )
    records = [
        {
            "instance_id": instance_id,
            "repo": "example/project",
            "base_commit": f"{index + 1:040x}",
            "problem_statement": f"private problem {index}",
            "patch": f"private patch {index}",
            "test_patch": f"private test patch {index}",
            "version": "1.0",
            "FAIL_TO_PASS": [f"test_fail_{index}"],
            "PASS_TO_PASS": [f"test_pass_{index}"],
            "environment_setup_commit": f"{index + 2:040x}",
        }
        for index, instance_id in enumerate(instance_ids)
    ]
    record_lines = [
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode() for record in records
    ]
    records_raw = b"\n".join(record_lines) + b"\n"
    mode_dir = state / "swebench" / mode
    mode_dir.mkdir(parents=True, exist_ok=True)
    records_path = mode_dir / "records.jsonl"
    records_path.write_bytes(records_raw)
    tasks = [
        {
            "instance_id": record["instance_id"],
            "base_commit": record["base_commit"],
            "repository": record["repo"],
            "problem_statement_sha256": hashlib.sha256(
                record["problem_statement"].encode()
            ).hexdigest(),
            "record_sha256": hashlib.sha256(line).hexdigest(),
        }
        for record, line in zip(records, record_lines, strict=True)
    ]
    manifest = {
        "schema_version": 1,
        "benchmark": "swebench_verified",
        "dataset_id": "princeton-nlp/SWE-bench_Verified",
        "dataset_revision": "a" * 40,
        "source_sha256": "b" * 64,
        "records_path": f"swebench/{mode}/records.jsonl",
        "records_sha256": hashlib.sha256(records_raw).hexdigest(),
        "selection_policy": swebench_module._expected_selection_policy(
            mode, "princeton-nlp/SWE-bench_Verified"
        ),
        "split": "test",
        "task_count": len(tasks),
        "tasks": tasks,
        "ordered_instance_ids_sha256": hashlib.sha256(
            json.dumps([task["instance_id"] for task in tasks], separators=(",", ":")).encode()
        ).hexdigest(),
        "verified_500_instance_ids": verified_ids,
        "verified_500_ids_sha256": hashlib.sha256(
            json.dumps(verified_ids, separators=(",", ":")).encode()
        ).hexdigest(),
        "frozen_before_tuning": True,
        "license_authorized": True,
        "license_authorization_revision": "fixture-license-v1",
    }
    path = mode_dir / "manifest.json"
    path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return path, _sha256(path)


def _image_bindings(state: Path, manifest_path: Path, *, mode: str) -> tuple[Path, str, str]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bindings = []
    host_spec_dir = state / "swebench" / mode / "host-test-specs"
    host_spec_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    for index, task in enumerate(manifest["tasks"]):
        task_digest = "sha256:" + hashlib.sha256(f"task-{index}".encode()).hexdigest()
        grader_digest = "sha256:" + hashlib.sha256(f"grader-{index}".encode()).hexdigest()
        trusted_tests_sha256 = hashlib.sha256(f"tests-{index}".encode()).hexdigest()
        eval_script = "#!/bin/bash\necho synthetic official tests\n"
        test_spec_fields = {
            "instance_id": task["instance_id"],
            "image": "local/swebench-official-base:synthetic",
            "repo": task["repository"],
            "version": "1.0",
            "FAIL_TO_PASS": ["tests/test_synthetic.py::test_new"],
            "PASS_TO_PASS": ["tests/test_synthetic.py::test_existing"],
            "log_parser": "parse_log_pytest",
            "eval_type": "pass_and_fail",
            "eval_script": eval_script,
            "image_assets": None,
        }
        canonical_eval_script = "#!/bin/bash\nset -uxo pipefail\necho synthetic official tests\n"
        execution_eval_script = canonical_eval_script + 'exit "$SWEBENCH_TEST_EXIT_CODE"\n'
        scorer_entrypoint = (
            Path(swebench_module.__file__).resolve().parents[2]
            / "sandbox"
            / "swebench"
            / "scorer_entrypoint.py"
        )
        host_test_spec = {
            "schema_version": 1,
            "swebench_version": "5.0.2",
            "instance_id": task["instance_id"],
            "source_record_sha256": task["record_sha256"],
            "trusted_tests_sha256": trusted_tests_sha256,
            "source_eval_script_sha256": hashlib.sha256(eval_script.encode()).hexdigest(),
            "test_spec_sha256": hashlib.sha256(
                swebench_module._canonical(test_spec_fields)
            ).hexdigest(),
            "canonical_eval_script_b64": base64.b64encode(canonical_eval_script.encode()).decode(
                "ascii"
            ),
            "canonical_eval_script_sha256": hashlib.sha256(
                canonical_eval_script.encode()
            ).hexdigest(),
            "execution_eval_script_b64": base64.b64encode(execution_eval_script.encode()).decode(
                "ascii"
            ),
            "execution_eval_script_sha256": hashlib.sha256(
                execution_eval_script.encode()
            ).hexdigest(),
            "scorer_entrypoint_sha256": hashlib.sha256(scorer_entrypoint.read_bytes()).hexdigest(),
            "test_spec": test_spec_fields,
        }
        host_test_spec_raw = json.dumps(
            host_test_spec,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        host_test_spec_path = host_spec_dir / f"{task['instance_id']}.json"
        host_test_spec_path.write_bytes(host_test_spec_raw)
        host_test_spec_path.chmod(0o600)
        bindings.append(
            {
                "instance_id": task["instance_id"],
                "base_commit": task["base_commit"],
                "source_record_sha256": task["record_sha256"],
                "task_image_reference": f"local/swebench-task-{index}@{task_digest}",
                "task_image_digest": task_digest,
                "task_build_recipe_sha256": hashlib.sha256(
                    f"task-recipe-{index}".encode()
                ).hexdigest(),
                "task_build_inputs_sha256": hashlib.sha256(
                    f"task-inputs-{index}".encode()
                ).hexdigest(),
                "grader_image_reference": f"local/swebench-grader-{index}@{grader_digest}",
                "grader_image_digest": grader_digest,
                "grader_build_recipe_sha256": hashlib.sha256(
                    f"grader-recipe-{index}".encode()
                ).hexdigest(),
                "grader_build_inputs_sha256": hashlib.sha256(
                    f"grader-inputs-{index}".encode()
                ).hexdigest(),
                "official_scorer_revision_sha256": hashlib.sha256(
                    f"scorer-{index}".encode()
                ).hexdigest(),
                "trusted_tests_sha256": trusted_tests_sha256,
                "host_test_spec_path": (
                    Path("swebench") / mode / "host-test-specs" / host_test_spec_path.name
                ).as_posix(),
                "host_test_spec_sha256": hashlib.sha256(host_test_spec_raw).hexdigest(),
                "execution_eval_script_sha256": hashlib.sha256(
                    execution_eval_script.encode()
                ).hexdigest(),
            }
        )
    ordered_ids = [binding["instance_id"] for binding in bindings]
    ordered_ids_sha256 = hashlib.sha256(
        json.dumps(ordered_ids, separators=(",", ":")).encode()
    ).hexdigest()
    document = {
        "schema_version": 1,
        "benchmark": "swebench_verified",
        "mode": mode,
        "task_count": len(bindings),
        "execution_platform": "linux/amd64",
        "ordered_instance_ids_sha256": ordered_ids_sha256,
        "control_image_reference": f"local/swebench-control@{_CONTROL_DIGEST}",
        "control_image_digest": _CONTROL_DIGEST,
        "isolation_revision_sha256": "9" * 64,
        "bindings": bindings,
    }
    path = state / "swebench" / mode / "image-bindings.json"
    path.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return path, _sha256(path), ordered_ids_sha256


def _profile(
    repo: Path, state: Path, *, count: int = 2, mode: str = "qualification"
) -> dict[str, Any]:
    manifest, digest = _manifest(state, count, mode=mode)
    bindings_path, bindings_sha256, ordered_ids_sha256 = _image_bindings(state, manifest, mode=mode)
    effective_count = 500 if mode == "verified" else count
    config = repo / "sandbox" / "swebench" / "mini-swe-agent.yaml"
    profile: dict[str, Any] = {
        "schema_version": 1,
        "mode": mode,
        "manifest_path": str(manifest),
        "manifest_sha256": digest,
        "mini_swe_agent_version": MINI_SWE_AGENT_VERSION,
        "mini_swe_agent_wheel_sha256": (
            "a35463c553ac825c7773b03cfa69cd44958e3af20155dcc5711fdf9e4c67cd54"
        ),
        "swebench_version": SWEBENCH_VERSION,
        "swebench_wheel_sha256": "b7f0416a1e686eca22c2f749b5f816685a202835032f6683080e2b53545bbb62",
        "runner_config_sha256": _sha256(config),
        "model": "qwen3.8-27b-splash",
        "model_runtime": {
            "adapter_revision_sha256": "5" * 64,
            "served_model_fingerprint": "6" * 64,
            "runtime_identity_sha256": "7" * 64,
        },
        "image_bindings_path": str(bindings_path),
        "image_bindings_sha256": bindings_sha256,
        "image_bindings_task_count": effective_count,
        "image_bindings_ordered_instance_ids_sha256": ordered_ids_sha256,
        "control_image_reference": f"local/swebench-control@{_CONTROL_DIGEST}",
        "control_image_digest": _CONTROL_DIGEST,
        "platform": "linux/amd64",
        "task_count": effective_count,
        "parameters": {
            "reasoning_effort": "xhigh",
            "temperature": 1.0,
            "top_p": 0.95,
            "max_output_tokens": 65_536,
        },
        "resource_limits": {
            "timeout_seconds": 600,
            "memory_bytes": 4_294_967_296,
            "cpus": "4.0",
            "pids_limit": 256,
            "nofile_limit": 1024,
            "tmpfs_bytes": 268_435_456,
            "max_output_bytes": 1_048_576,
            "max_turns": 100,
            "max_requests": 100,
            "max_task_output_tokens": 6_553_600,
        },
        "protocol_approval": {"approved": False, "approved_fingerprint": None},
    }
    return profile


class _FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.fail_on: str | None = None
        self.mounts_override: list[dict[str, object]] | None = None
        self.removed_containers: set[str] = set()
        self.removed_volumes: set[str] = set()
        self.created_images: dict[str, str] = {}

    def __call__(
        self,
        args: Sequence[str],
        *,
        input: bytes | None = None,
        capture_output: bool = False,
        stdout: int | None = None,
        stderr: int | None = None,
        text: bool = False,
        check: bool = False,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[Any]:
        command = tuple(args)
        self.calls.append(command)
        if self.fail_on and self.fail_on in command:
            raise subprocess.SubprocessError("synthetic failure")
        if "info" in command:
            payload = '{"Architecture":"aarch64","OSType":"linux"}'
        elif "image" in command and "inspect" in command:
            payload = json.dumps(
                [
                    {
                        "Id": command[-1].split("@")[-1],
                        "Os": "linux",
                        "Architecture": "arm64" if "swebench-control@" in command[-1] else "amd64",
                    }
                ]
            )
        elif command[1:3] == ("create",) or command[1] == "create":
            name_index = command.index("--name") + 1
            name = command[name_index]
            image_reference = next(value for value in command if "@sha256:" in value)
            self.created_images[name] = image_reference
            self.removed_containers.discard(name)
            payload = ""
        elif command[1:3] == ("rm", "-f"):
            self.removed_containers.add(command[-1])
            payload = ""
        elif command[1:4] == ("volume", "rm", "-f"):
            self.removed_volumes.add(command[-1])
            payload = ""
        elif command[1:3] == ("container", "inspect") and command[-1] in self.removed_containers:
            message = f"Error: No such container: {command[-1]}"
            stdout_value: bytes | str = "" if text else b""
            stderr_value: bytes | str = message if text else message.encode()
            return subprocess.CompletedProcess(command, 1, stdout_value, stderr_value)
        elif command[1:3] == ("volume", "inspect") and command[-1] in self.removed_volumes:
            message = f"Error: No such volume: {command[-1]}"
            stdout_value = "" if text else b""
            stderr_value = message if text else message.encode()
            return subprocess.CompletedProcess(command, 1, stdout_value, stderr_value)
        elif command[1:3] == ("volume", "inspect"):
            payload = "[]"
        elif "inspect" in command:
            name = command[-1]
            mounts = self.mounts_override
            if mounts is None:
                mounts = (
                    [
                        {
                            "Type": "volume",
                            "Name": f"{name}-workspace",
                            "Destination": "/testbed",
                            "RW": True,
                        }
                    ]
                    if "-task-" in name or "-grader-" in name
                    else []
                )
            payload = json.dumps(
                [
                    {
                        "Config": {
                            "Image": self.created_images.get(name, ""),
                            "User": "65532:65532",
                            "Env": ["HOME=/tmp/home", "PATH=/usr/local/bin:/usr/bin:/bin"],
                            "ExposedPorts": None,
                        },
                        "HostConfig": {
                            "NetworkMode": "none",
                            "ReadonlyRootfs": True,
                            "Privileged": False,
                            "CapDrop": ["ALL"],
                            "SecurityOpt": ["no-new-privileges"],
                            "PidsLimit": 256,
                            "Memory": 4_294_967_296,
                            "Binds": None,
                            "PortBindings": {},
                        },
                        "Mounts": mounts,
                        "Image": self.created_images.get(name, "").split("@")[-1],
                    }
                ]
            )
        elif "version" in command:
            payload = "Docker version 27.0.0"
        else:
            payload = ""
        if text:
            return subprocess.CompletedProcess(command, 0, payload, "")
        return subprocess.CompletedProcess(command, 0, payload.encode(), b"")


class _ControlCleanupRunner:
    def __init__(
        self,
        name: str,
        labels: Mapping[str, str],
        *,
        present: bool = True,
        survive_remove: bool = False,
        fail_remove: bool = False,
        auto_remove_before_rm: bool = False,
        removal_in_progress_polls: int | None = None,
        vanishing_inspects: int | None = None,
    ) -> None:
        self.name = name
        # None: `docker ps` lists a present container; N: a --rm container that `ps` already
        # hides while `inspect` still finds it for N more inspections, then it is gone.
        self.vanishing_inspects = vanishing_inspects
        # None: no in-progress removal; N: container disappears after N more inspections.
        self.removal_in_progress_polls = removal_in_progress_polls
        self.removing = False
        self.labels = dict(labels)
        self.present = present
        self.survive_remove = survive_remove
        self.fail_remove = fail_remove
        self.auto_remove_before_rm = auto_remove_before_rm
        self.container_id = hashlib.sha256(name.encode()).hexdigest()
        self.base = _FakeRunner()

    @property
    def calls(self) -> list[tuple[str, ...]]:
        return self.base.calls

    def __call__(
        self,
        args: Sequence[str],
        *,
        input: bytes | None = None,
        capture_output: bool = False,
        stdout: int | None = None,
        stderr: int | None = None,
        text: bool = False,
        check: bool = False,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[Any]:
        command = tuple(args)
        if (
            "inspect" in command
            and "--format" in command
            and command[-1] in {self.name, self.container_id}
        ):
            self.base.calls.append(command)
            reference = command[-1]
            if self.vanishing_inspects is not None:
                if self.vanishing_inspects <= 0:
                    self.present = False
                else:
                    self.vanishing_inspects -= 1
            if self.removing and self.removal_in_progress_polls is not None:
                if self.removal_in_progress_polls <= 0:
                    self.present = False
                else:
                    self.removal_in_progress_polls -= 1
            if not self.present or reference not in {self.name, self.container_id}:
                return subprocess.CompletedProcess(
                    command, 1, b"", f"Error: No such container: {reference}".encode()
                )
            if command[command.index("--format") + 1] == "{{.Id}}":
                return subprocess.CompletedProcess(command, 0, self.container_id.encode(), b"")
            payload = json.dumps(self.labels).encode()
            return subprocess.CompletedProcess(command, 0, payload, b"")
        if "ps" in command and "--filter" in command:
            self.base.calls.append(command)
            listed = self.present and self.vanishing_inspects is None
            payload = f"{self.name}\n".encode() if listed else b""
            return subprocess.CompletedProcess(command, 0, payload, b"")
        if (
            "rm" in command
            and "-f" in command
            and command[-1]
            in {
                self.name,
                self.container_id,
            }
        ):
            self.base.calls.append(command)
            if self.auto_remove_before_rm:
                self.present = False
                return subprocess.CompletedProcess(
                    command, 1, b"", b"Error: No such container: already removed"
                )
            if self.fail_remove:
                return subprocess.CompletedProcess(command, 1, b"", b"synthetic remove failure")
            if self.removal_in_progress_polls is not None or self.removing:
                self.removing = True
                return subprocess.CompletedProcess(
                    command,
                    1,
                    b"",
                    b"Error response from daemon: removal of container "
                    + self.name.encode()
                    + b" is already in progress",
                )
            if not self.survive_remove:
                self.present = False
            return subprocess.CompletedProcess(command, 0, self.name.encode(), b"")
        return self.base(
            command,
            input=input,
            capture_output=capture_output,
            stdout=stdout,
            stderr=stderr,
            text=text,
            check=check,
            timeout=timeout,
        )


_RPC_FIXTURE_SCRIPT = """
import sys
import time

mode = sys.argv[1]
if mode == "lines":
    for line in sys.argv[2:]:
        sys.stdout.write(line)
        sys.stdout.write("\\n")
        sys.stdout.flush()
elif mode == "partial":
    sys.stdout.write("partial")
    sys.stdout.flush()
elif mode == "flood":
    sys.stdout.write("x" * int(sys.argv[2]))
    sys.stdout.flush()
elif mode == "stderr-flood":
    sys.stderr.write("x" * int(sys.argv[2]))
    sys.stderr.flush()
if mode in {"silent", "partial", "flood", "stderr-flood"}:
    time.sleep(30)
"""


def _rpc_fixture_command(mode: str, *lines: bytes, count: int | None = None) -> tuple[str, ...]:
    args = [sys.executable, "-u", "-c", _RPC_FIXTURE_SCRIPT, mode]
    if count is not None:
        args.append(str(count))
    args.extend(line.decode("utf-8") for line in lines)
    return tuple(args)


def _default_rpc_kwargs(
    tmp_path: Path, *, timeout_seconds: float = 0.2, max_output_bytes: int = 1024
) -> dict[str, object]:
    digest = "sha256:" + "a" * 64
    task_fingerprint = "a" * 64
    attempt_nonce = "0123456789abcdef"
    control_name, control_identity = swebench_module._control_container_identity(
        task_fingerprint_sha256=task_fingerprint,
        attempt_nonce=attempt_nonce,
        control_image_digest=digest,
    )
    control_labels = {
        swebench_module._CONTROL_LABEL_ROLE: "control",
        swebench_module._CONTROL_LABEL_IDENTITY: control_identity,
        swebench_module._CONTROL_LABEL_TASK: task_fingerprint,
        swebench_module._CONTROL_LABEL_ATTEMPT: attempt_nonce,
        swebench_module._CONTROL_LABEL_IMAGE: digest,
    }
    return {
        "docker_executable": "/usr/local/bin/docker",
        "control_image_reference": f"local/swebench-control@{digest}",
        "control_image_digest": digest,
        "control_container_name": control_name,
        "control_identity_sha256": control_identity,
        "evidence_dir": tmp_path,
        "runner": _ControlCleanupRunner(control_name, control_labels),
        "timeout_seconds": timeout_seconds,
        "max_output_bytes": max_output_bytes,
        "server_origin": "http://127.0.0.1:1234/v1",
        "model": "fixture-model",
        "instance_id": "task-instance",
        "task_fingerprint_sha256": task_fingerprint,
        "attempt_nonce": attempt_nonce,
        "served_model_fingerprint": "6" * 64,
    }


def _host_receipt(
    *,
    task_instance_id: str = "fixture-task",
    model_instance_id: str = "fixture-instance",
    task_fingerprint_sha256: str = "a" * 64,
    served_model_fingerprint: str = "6" * 64,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "task_instance_id_sha256": hashlib.sha256(task_instance_id.encode()).hexdigest(),
        "model_instance_id_sha256": hashlib.sha256(model_instance_id.encode()).hexdigest(),
        "task_fingerprint_sha256": task_fingerprint_sha256,
        "served_model_fingerprint": served_model_fingerprint,
        "request_sha256": "b" * 64,
        "response_sha256": "c" * 64,
        "response_schema": "openai_chat_completion",
    }


def _agent_result(
    *,
    status: object = "completed",
    include_host_receipt: object = True,
    **kwargs: object,
) -> dict[str, object]:
    status_text = str(status)
    instance_id = str(kwargs.get("instance_id", "fixture-instance"))
    model_instance_id = str(kwargs.get("model_instance_id", "fixture-instance"))
    task_fingerprint_sha256 = str(kwargs.get("task_fingerprint_sha256", "a" * 64))
    served_model_fingerprint = str(kwargs.get("served_model_fingerprint", "6" * 64))
    result: dict[str, object] = {
        "status": status_text,
        "attempted": True,
        "first_verified_response": True,
        "turn_count": 1,
        "request_count": 1,
        "output_tokens": kwargs.get("output_tokens", 1),
        "prompt_tokens": kwargs.get("prompt_tokens"),
        "finish_reason": kwargs.get("finish_reason"),
        "context_length": kwargs.get("context_length"),
        "context_truncation_status": kwargs.get("context_truncation_status"),
        "truncation_status": kwargs.get("truncation_status"),
        "trajectory_elapsed_seconds": kwargs.get("trajectory_elapsed_seconds"),
        "locality_checks": 1,
        "bash_actions_only": True,
        "effective_step_limit": kwargs.get("max_turns", 100),
        "effective_wall_time_limit_seconds": kwargs.get("timeout_seconds", 600),
        "trajectory_sha256": "3" * 64,
        "model_patch_sha256": "4" * 64 if status_text == "completed" else None,
        "prediction_handle": (
            "private://prediction-" + "4" * 64 + ".patch" if status_text == "completed" else None
        ),
        "host_response_receipt": (
            _host_receipt(
                task_instance_id=instance_id,
                model_instance_id=model_instance_id,
                task_fingerprint_sha256=task_fingerprint_sha256,
                served_model_fingerprint=served_model_fingerprint,
            )
            if include_host_receipt is not False
            else None
        ),
    }
    return result


def _fixture_agent(**kwargs: object) -> Mapping[str, object]:
    result = _agent_result(**kwargs)
    if result.get("status") == "completed" and kwargs.get("evidence_dir") is not None:
        instance_id = str(kwargs.get("instance_id", "fixture"))
        patch = f"diff --git a/{instance_id} b/{instance_id}\n".encode()
        digest = hashlib.sha256(patch).hexdigest()
        result["model_patch_sha256"] = digest
        result["prediction_handle"] = f"private://prediction-{digest}.patch"
        evidence_dir = Path(str(kwargs["evidence_dir"]))
        swebench_module._write_private_bytes(evidence_dir / f"prediction-{digest}.patch", patch)
    callback = kwargs.get("on_verified_response")
    receipt = result.get("host_response_receipt")
    if callable(callback) and isinstance(receipt, Mapping):
        callback(receipt)
    result["host_response_receipt"] = None
    return result


def _fixture_test_spec(task: Any, _state: Path) -> tuple[Any, str]:
    eval_script = "#!/bin/bash\nset -uxo pipefail\necho synthetic tests\n"
    test_spec = SimpleNamespace(
        instance_id=task.instance_id,
        image=task.grader_image_reference,
    )
    return test_spec, eval_script


def _frozen_test_spec_factory(repo: Path) -> Any:
    def factory(task: Any, state: Path) -> tuple[Any, str]:
        test_spec = swebench_module._load_frozen_test_spec(task, state, repo)
        # Production grades with the frozen execution script, not the canonical one.
        return test_spec, test_spec.execution_eval_script

    return factory


def _fixture_grader_capture(**kwargs: object) -> Mapping[str, object]:
    return {
        "schema_version": 1,
        "phase": "patch_ready",
        "launcher_exit_code": 0,
        "process_exit_code": 0,
        "eval_launcher_exit_code": 0,
        "eval_exit_code": 0,
        "timed_out": False,
        "patch_sha256": kwargs["model_patch_sha256"],
        "eval_script_sha256": kwargs["test_spec_eval_script_sha256"],
        "stdout": b"synthetic official test output\n>>>>> Test Exit Code: 0\n",
        "stderr": b"",
        "error": None,
    }


def _fixture_score(_trusted_run: Any) -> Any:
    return SimpleNamespace(status="resolved", official=True, resolved=True)


def _runtime(runner: _FakeRunner) -> SwebenchRuntime:
    scorer_entrypoint = (
        Path(swebench_module.__file__).resolve().parents[2]
        / "sandbox"
        / "swebench"
        / "scorer_entrypoint.py"
    )
    return SwebenchRuntime(
        docker_executable=Path("/usr/local/bin/docker"),
        runner=runner,
        parameter_probe=lambda _origin, _model, parameters: {key: True for key in parameters},
        adapter_probe=lambda _origin, _model, _parameters: {
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
            "adapter_revision_sha256": "5" * 64,
            "served_model_fingerprint": "6" * 64,
            "runtime_identity_sha256": "7" * 64,
        },
        agent_executor=_fixture_agent,
        grader_executor=_fixture_grader_capture,
        test_spec_factory=_fixture_test_spec,
        score_executor=_fixture_score,
        scorer_attestation=swebench_module._ScorerAttestation(
            control_image_reference=f"local/swebench-control@{_CONTROL_DIGEST}",
            control_image_digest=_CONTROL_DIGEST,
            platform=swebench_module._SCORER_PLATFORM,
            swebench_version=SWEBENCH_VERSION,
            scorer_entrypoint_sha256=hashlib.sha256(scorer_entrypoint.read_bytes()).hexdigest(),
        ),
        nonce_factory=lambda: "0123456789abcdef",
    )


def test_live_prediction_handle_must_match_the_patch_hash(tmp_path: Path) -> None:
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(Path.cwd(), state)
    executor = _runtime(_FakeRunner()).agent_executor
    assert executor is not None
    result = dict(executor())
    limits = profile["resource_limits"]
    assert swebench_module._validate_agent_result(result, limits) == result
    for handle in (
        "adapter://prediction-fixed",
        "private://prediction-" + "5" * 64 + ".patch",
        "private://../prediction-" + "4" * 64 + ".patch",
    ):
        with pytest.raises(SwebenchError, match="prediction evidence"):
            swebench_module._validate_agent_result({**result, "prediction_handle": handle}, limits)


def test_agent_result_rejects_mismatched_nondefault_effective_step_limit() -> None:
    result = _agent_result(max_turns=7, timeout_seconds=41)
    result["effective_step_limit"] = 8
    limits = {
        "max_turns": 7,
        "max_requests": 9,
        "timeout_seconds": 41,
        "max_task_output_tokens": 65_536,
    }

    with pytest.raises(SwebenchError, match="effective runner limits"):
        swebench_module._validate_agent_result(result, limits)


def test_agent_telemetry_accepts_nullable_values_without_fabricating_zeros() -> None:
    result = _agent_result(
        max_turns=2,
        timeout_seconds=120,
        prompt_tokens=None,
        finish_reason="length",
        context_length=32_768,
        context_truncation_status=None,
        truncation_status="generation_limit_reached",
        trajectory_elapsed_seconds=12.375,
    )
    limits = {
        "max_turns": 2,
        "max_requests": 2,
        "timeout_seconds": 120,
        "max_task_output_tokens": 2,
    }

    validated = swebench_module._validate_agent_result(result, limits)

    assert validated == result
    assert validated["prompt_tokens"] is None
    assert validated["context_truncation_status"] is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("prompt_tokens", True),
        ("context_length", False),
        ("finish_reason", 1),
        ("context_truncation_status", "unexpected"),
        ("truncation_status", "unexpected"),
        ("trajectory_elapsed_seconds", True),
        ("trajectory_elapsed_seconds", float("nan")),
        ("trajectory_elapsed_seconds", -0.1),
    ],
)
def test_agent_telemetry_rejects_invalid_nullable_values(field: str, value: object) -> None:
    result = _agent_result(max_turns=2, timeout_seconds=120)
    result[field] = value
    limits = {
        "max_turns": 2,
        "max_requests": 2,
        "timeout_seconds": 120,
        "max_task_output_tokens": 2,
    }

    with pytest.raises(SwebenchError, match="telemetry"):
        swebench_module._validate_agent_result(result, limits)


def test_served_model_fingerprint_ignores_only_remaining_instance_ttl() -> None:
    def fingerprint(
        *,
        ttl: int = 120,
        model_key: str = "fixture-model",
        loaded_context: int = 4096,
        selected_context: int = 4096,
        instance_id: str = "fixture-instance",
        model_extra: str = "kept",
        loaded_instance_extra: str = "kept",
        selected_instance_extra: str = "kept",
    ) -> str:
        model_record = {
            "key": model_key,
            "format": "gguf",
            "future_model_field": model_extra,
            "loaded_instances": [
                {
                    "id": instance_id,
                    "config": {"context_length": loaded_context},
                    "remaining_ttl_seconds": ttl,
                    "future_instance_field": loaded_instance_extra,
                }
            ],
        }
        instance = {
            "id": instance_id,
            "status": "loaded",
            "config": {"context_length": selected_context},
            "remaining_ttl_seconds": ttl,
            "future_instance_field": selected_instance_extra,
        }
        return swebench_module._served_model_fingerprint(model_record, instance)

    baseline = fingerprint()
    assert fingerprint(ttl=118) == baseline
    assert fingerprint(model_key="other-model") != baseline
    assert fingerprint(loaded_context=8192) != baseline
    assert fingerprint(selected_context=8192) != baseline
    assert fingerprint(instance_id="other-instance") != baseline
    assert fingerprint(model_extra="changed") != baseline
    assert fingerprint(loaded_instance_extra="changed") != baseline
    assert fingerprint(selected_instance_extra="changed") != baseline


def test_host_receipt_binds_request_to_the_same_loaded_model_instance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def loaded_snapshot(ttl: int) -> tuple[dict[str, object], dict[str, object]]:
        model_record: dict[str, object] = {
            "key": "fixture-model",
            "format": "gguf",
            "loaded_instances": [
                {
                    "id": "fixture-instance",
                    "config": {"context_length": 4096},
                    "remaining_ttl_seconds": ttl,
                    "future_instance_field": "bound",
                }
            ],
        }
        instance: dict[str, object] = {
            "id": "fixture-instance",
            "status": "loaded",
            "config": {"context_length": 4096},
            "remaining_ttl_seconds": ttl,
            "future_instance_field": "bound",
        }
        return model_record, instance

    before = loaded_snapshot(120)
    after = loaded_snapshot(118)
    served = swebench_module._served_model_fingerprint(*before)
    responses = [before, after]
    response = {
        "model": "fixture-model",
        "choices": [{"message": {"role": "assistant", "content": "OK"}}],
    }
    monkeypatch.setattr(
        swebench_module, "_loaded_model", lambda *_args, **_kwargs: responses.pop(0)
    )
    monkeypatch.setattr(swebench_module, "_checkpoint_sdk_chat", lambda *_args, **_kwargs: response)
    completion, receipt = swebench_module._host_verified_chat_response(
        "http://127.0.0.1:1234/v1",
        {"model": "fixture-model", "messages": [], "stream": False},
        expected_model="fixture-model",
        expected_task_fingerprint="a" * 64,
        task_instance_id="task-instance",
        expected_model_instance_id="fixture-instance",
        expected_served_model_fingerprint=served,
        deadline=time.monotonic() + 10,
        sdk_checkpoint=tmp_path / "checkpoint.json",
    )
    assert completion == response
    assert receipt["served_model_fingerprint"] == served
    assert receipt["task_instance_id_sha256"] == hashlib.sha256(b"task-instance").hexdigest()
    assert receipt["model_instance_id_sha256"] == hashlib.sha256(b"fixture-instance").hexdigest()


@pytest.mark.parametrize("selector", ["qwen3.8-27b-splash", "racecraft-splash-local"])
def test_loaded_model_accepts_exact_model_key_or_instance_id(
    selector: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = {
        "key": "qwen3.8-27b-splash",
        "publisher": "incoai",
        "loaded_instances": [{"id": "racecraft-splash-local", "status": "loaded"}],
    }
    monkeypatch.setattr(
        swebench_module,
        "_http_json",
        lambda *_args, **_kwargs: {"models": [model]},
    )

    selected_model, selected_instance = swebench_module._loaded_model(
        "http://127.0.0.1:1234/v1", selector
    )

    assert selected_model is model
    assert selected_instance is model["loaded_instances"][0]


def test_loaded_model_still_requires_one_exact_matching_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = {
        "key": "qwen3.8-27b-splash",
        "loaded_instances": [
            {"id": "first-instance", "status": "loaded"},
            {"id": "second-instance", "status": "loaded"},
        ],
    }
    monkeypatch.setattr(
        swebench_module,
        "_http_json",
        lambda *_args, **_kwargs: {"models": [model]},
    )

    with pytest.raises(SwebenchError, match="exactly one loaded instance"):
        swebench_module._loaded_model("http://127.0.0.1:1234/v1", "qwen3.8-27b-splash")


def test_http_transport_reads_loopback_json_and_closes_connection() -> None:
    payload = b'{"models": []}'

    def writer(connection: socket.socket) -> None:
        connection.sendall(
            b"HTTP/1.1 200 OK\r\n"
            + f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
            + payload
        )

    origin, stop = _start_raw_http_server(writer)
    try:
        assert swebench_module._http_json(origin, "/api/v1/models", timeout=1) == {"models": []}
    finally:
        stop()


def test_http_transport_reads_chunked_loopback_json() -> None:
    chunks = (b'{"models": ', b"[]}")

    def writer(connection: socket.socket) -> None:
        connection.sendall(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n")
        for chunk in chunks:
            connection.sendall(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
        connection.sendall(b"0\r\n\r\n")

    origin, stop = _start_raw_http_server(writer)
    try:
        assert swebench_module._http_json(origin, "/api/v1/models", timeout=1) == {"models": []}
    finally:
        stop()


def test_http_transport_enforces_absolute_slow_drip_read_deadline() -> None:
    payload = b'{"models": []}'

    def writer(connection: socket.socket) -> None:
        connection.sendall(
            b"HTTP/1.1 200 OK\r\n"
            + f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
        )
        for byte in payload:
            connection.sendall(bytes((byte,)))
            time.sleep(0.04)

    origin, stop = _start_raw_http_server(writer)
    started = time.monotonic()
    try:
        with pytest.raises(SwebenchError, match="HTTP deadline"):
            swebench_module._http_json(origin, "/api/v1/models", timeout=0.15)
    finally:
        stop()
    assert time.monotonic() - started < 1


def test_http_transport_bounds_stalled_read_deadline() -> None:
    def writer(connection: socket.socket) -> None:
        connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\nConnection: close\r\n\r\n")
        time.sleep(1)

    origin, stop = _start_raw_http_server(writer)
    try:
        with pytest.raises(SwebenchError, match="HTTP deadline"):
            swebench_module._http_json(origin, "/api/v1/models", timeout=0.1)
    finally:
        stop()


def test_http_transport_cancellation_closes_slow_read() -> None:
    started = threading.Event()

    def writer(connection: socket.socket) -> None:
        started.set()
        connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\nConnection: close\r\n\r\n")
        time.sleep(1)

    origin, stop = _start_raw_http_server(writer)
    cancelled = threading.Event()
    errors: list[BaseException] = []

    def call() -> None:
        try:
            swebench_module._http_json(
                origin,
                "/api/v1/models",
                timeout=5,
                cancelled=cancelled.is_set,
            )
        except BaseException as exc:  # test thread must report every failure
            errors.append(exc)

    thread = threading.Thread(target=call, daemon=True)
    thread.start()
    assert started.wait(timeout=1)
    cancelled.set()
    thread.join(timeout=1)
    try:
        assert not thread.is_alive()
        assert len(errors) == 1
        assert isinstance(errors[0], SwebenchError)
        assert "cancelled" in str(errors[0])
    finally:
        stop()


class _StalledHttpSelector:
    def __init__(self, *, ready: bool) -> None:
        self.ready = ready
        self.closed = False

    def register(self, *_args: object, **_kwargs: object) -> None:
        return None

    def unregister(self, *_args: object, **_kwargs: object) -> None:
        return None

    def select(self, _timeout: float) -> list[tuple[object, int]]:
        return [(object(), 0)] if self.ready else []

    def close(self) -> None:
        self.closed = True


class _StalledHttpSocket:
    def __init__(self, *, write_stalled: bool) -> None:
        self.write_stalled = write_stalled
        self.closed = False

    def setblocking(self, _enabled: bool) -> None:
        return None

    def connect_ex(self, _address: object) -> int:
        return errno.EINPROGRESS if not self.write_stalled else 0

    def getsockopt(self, _level: int, _option: int) -> int:
        return 0

    def send(self, _payload: bytes) -> int:
        if self.write_stalled:
            raise BlockingIOError
        return 0

    def recv(self, _size: int) -> bytes:
        return b""

    def shutdown(self, _how: int) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class _BufferedHttpSocket:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.closed = False

    def setblocking(self, _enabled: bool) -> None:
        return None

    def connect_ex(self, _address: object) -> int:
        return 0

    def getsockopt(self, _level: int, _option: int) -> int:
        return 0

    def send(self, payload: bytes) -> int:
        return len(payload)

    def recv(self, _size: int) -> bytes:
        payload, self.payload = self.payload, b""
        return payload

    def shutdown(self, _how: int) -> None:
        return None

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize("timeout", [math.nan, math.inf, -math.inf])
def test_http_transport_rejects_nonfinite_timeout(timeout: float) -> None:
    with pytest.raises(SwebenchError, match="finite"):
        swebench_module._http_json("http://127.0.0.1:1/v1", "/api/v1/models", timeout=timeout)


@pytest.mark.parametrize("deadline", [math.nan, math.inf, -math.inf])
def test_http_transport_rejects_nonfinite_deadline(deadline: float) -> None:
    with pytest.raises(SwebenchError, match="finite"):
        swebench_module._http_json(
            "http://127.0.0.1:1/v1",
            "/api/v1/models",
            timeout=1,
            deadline=deadline,
        )


def test_http_transport_rejects_crlf_in_authorization_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LM_API_TOKEN", "safe-token\r\nInjected: true")
    with pytest.raises(SwebenchError, match="authorization token is invalid"):
        swebench_module._http_json("http://127.0.0.1:1/v1", "/api/v1/models", timeout=1)


def _read_synthetic_headers(raw: bytes) -> tuple[int, Mapping[str, str], bytes]:
    sock = _BufferedHttpSocket(raw)
    selector = _StalledHttpSelector(ready=True)
    try:
        return swebench_module._http_read_headers(
            sock,
            selector,
            time.monotonic() + 1,
            None,
        )
    finally:
        selector.close()


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b"NOT-HTTP 200 OK\r\n\r\n", "status"),
        (b"HTTP/1.1 2000 OK\r\n\r\n", "status"),
        (
            b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\nContent-Length: 1\r\n\r\n",
            "framing",
        ),
        (
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nContent-Length: 1\r\n\r\n",
            "framing",
        ),
        (b"HTTP/1.1 200 OK\r\nTransfer-Encoding: gzip\r\n\r\n", "transfer encoding"),
    ],
)
def test_http_transport_rejects_malformed_status_and_framing(raw: bytes, message: str) -> None:
    with pytest.raises(SwebenchError, match=message):
        _read_synthetic_headers(raw)


def test_http_transport_enforces_header_delimiter_boundary() -> None:
    raw = b"HTTP/1.1 200 OK\r\nX-Test: " + b"x" * swebench_module._HTTP_HEADER_LIMIT + b"\r\n\r\n"
    with pytest.raises(SwebenchError, match="headers exceeded"):
        _read_synthetic_headers(raw)


def test_http_transport_enforces_chunk_header_delimiter_boundary() -> None:
    sock = _BufferedHttpSocket(b"")
    selector = _StalledHttpSelector(ready=True)
    try:
        with pytest.raises(SwebenchError, match="chunk header exceeded"):
            swebench_module._http_read_chunked_body(
                sock,
                selector,
                b"x" * (swebench_module._HTTP_HEADER_LIMIT + 1) + b"\r\n",
                time.monotonic() + 1,
                None,
            )
    finally:
        selector.close()


def test_http_transport_rechecks_deadline_after_json_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = b'{"models": []}'
    raw = (
        b"HTTP/1.1 200 OK\r\n"
        + f"Content-Length: {len(response)}\r\nConnection: close\r\n\r\n".encode()
        + response
    )
    fake_socket = _BufferedHttpSocket(raw)
    selector = _StalledHttpSelector(ready=True)
    monkeypatch.setattr(swebench_module, "_open_http_socket", lambda _host, _port: fake_socket)
    monkeypatch.setattr(swebench_module.selectors, "DefaultSelector", lambda: selector)
    original_mapping = swebench_module._mapping
    called = False

    def late_mapping(value: object, label: str) -> Mapping[str, Any]:
        nonlocal called
        called = True
        time.sleep(0.1)
        return original_mapping(value, label)

    monkeypatch.setattr(swebench_module, "_mapping", late_mapping)
    with pytest.raises(SwebenchError, match="HTTP deadline"):
        swebench_module._http_json("http://127.0.0.1:1/v1", "/api/v1/models", timeout=0.05)
    assert called
    assert fake_socket.closed
    assert selector.closed


def test_http_transport_bounds_stalled_connect_and_closes_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_socket = _StalledHttpSocket(write_stalled=False)
    selector = _StalledHttpSelector(ready=False)
    monkeypatch.setattr(swebench_module, "_open_http_socket", lambda _host, _port: fake_socket)
    monkeypatch.setattr(swebench_module.selectors, "DefaultSelector", lambda: selector)
    with pytest.raises(SwebenchError, match="HTTP deadline"):
        swebench_module._http_json("http://127.0.0.1:1/v1", "/api/v1/models", timeout=0.1)
    assert fake_socket.closed
    assert selector.closed


def test_http_transport_bounds_stalled_write_and_closes_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_socket = _StalledHttpSocket(write_stalled=True)
    selector = _StalledHttpSelector(ready=True)
    monkeypatch.setattr(swebench_module, "_open_http_socket", lambda _host, _port: fake_socket)
    monkeypatch.setattr(swebench_module.selectors, "DefaultSelector", lambda: selector)
    with pytest.raises(SwebenchError, match="HTTP deadline"):
        swebench_module._http_json("http://127.0.0.1:1/v1", "/api/v1/models", timeout=0.1)
    assert fake_socket.closed
    assert selector.closed


def test_default_agent_rpc_separates_task_and_model_instance_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_record = {"key": "fixture-model", "format": "gguf"}
    model_instance = {"id": "runtime-instance", "status": "loaded"}
    served = hashlib.sha256(
        swebench_module._canonical({"model": model_record, "instance": model_instance})
    ).hexdigest()
    agent_result = {
        "status": "completed",
        "attempted": True,
        "first_verified_response": True,
        "turn_count": 1,
        "request_count": 1,
        "output_tokens": 1,
        "prompt_tokens": None,
        "finish_reason": None,
        "context_length": None,
        "context_truncation_status": None,
        "truncation_status": None,
        "trajectory_elapsed_seconds": None,
        "locality_checks": 1,
        "bash_actions_only": True,
        "effective_step_limit": 100,
        "effective_wall_time_limit_seconds": 3600,
        "trajectory_sha256": "3" * 64,
        "model_patch_sha256": "4" * 64,
        "prediction_handle": "private://prediction-" + "4" * 64 + ".patch",
        "host_response_receipt": None,
    }
    lm_chat_line = swebench_module._canonical(
        {
            "op": "lm_chat",
            "body": {
                "model": "fixture-model",
                "messages": [],
                "stream": False,
            },
        }
    )
    result_line = swebench_module._canonical({"op": "result", "value": agent_result})
    monkeypatch.setattr(
        swebench_module,
        "_control_plane_args",
        lambda _kwargs: _rpc_fixture_command("lines", lm_chat_line, result_line),
    )
    monkeypatch.setattr(
        swebench_module,
        "_loaded_model",
        lambda *_args, **_kwargs: (model_record, model_instance),
    )
    monkeypatch.setattr(
        swebench_module,
        "_checkpoint_sdk_chat",
        lambda *_args, **_kwargs: {
            "model": "fixture-model",
            "choices": [{"message": {"role": "assistant", "content": "OK"}}],
        },
    )
    receipts: list[Mapping[str, object]] = []
    events: list[str] = []
    start_requests: list[Mapping[str, object]] = []
    original_rpc_reply = swebench_module._rpc_reply

    def recording_rpc_reply(process: Any, value: Mapping[str, object], *, deadline: float) -> None:
        events.append("start" if value.get("op") == "start" else "reply")
        if value.get("op") == "start":
            start_requests.append(value["request"])
        original_rpc_reply(process, value, deadline=deadline)

    monkeypatch.setattr(swebench_module, "_rpc_reply", recording_rpc_reply)

    def on_verified(receipt: Mapping[str, object]) -> None:
        receipts.append(receipt)
        events.append("callback")

    digest = "sha256:" + "a" * 64
    task_fingerprint = "a" * 64
    attempt_nonce = "0123456789abcdef"
    control_name, control_identity = swebench_module._control_container_identity(
        task_fingerprint_sha256=task_fingerprint,
        attempt_nonce=attempt_nonce,
        control_image_digest=digest,
    )
    control_labels = {
        swebench_module._CONTROL_LABEL_ROLE: "control",
        swebench_module._CONTROL_LABEL_IDENTITY: control_identity,
        swebench_module._CONTROL_LABEL_TASK: task_fingerprint,
        swebench_module._CONTROL_LABEL_ATTEMPT: attempt_nonce,
        swebench_module._CONTROL_LABEL_IMAGE: digest,
    }
    result = swebench_module._default_agent_executor(
        docker_executable="/usr/local/bin/docker",
        control_image_reference=f"local/swebench-control@{digest}",
        control_image_digest=digest,
        control_container_name=control_name,
        control_identity_sha256=control_identity,
        evidence_dir=tmp_path,
        runner=_ControlCleanupRunner(control_name, control_labels),
        timeout_seconds=1,
        max_output_bytes=1_048_576,
        server_origin="http://127.0.0.1:1234/v1",
        model="fixture-model",
        model_instance_id="runtime-instance",
        instance_id="task-instance",
        task_fingerprint_sha256=task_fingerprint,
        attempt_nonce=attempt_nonce,
        served_model_fingerprint=served,
        on_verified_response=on_verified,
        sdk_checkpoint=tmp_path / "checkpoint.json",
        max_requests=1,
        max_task_output_tokens=65536,
    )
    assert result["host_response_receipt"] == receipts[0]
    assert receipts[0]["task_instance_id_sha256"] == hashlib.sha256(b"task-instance").hexdigest()
    assert (
        receipts[0]["model_instance_id_sha256"] == hashlib.sha256(b"runtime-instance").hexdigest()
    )
    assert events == ["start", "callback", "reply"]
    assert "on_verified_response" not in start_requests[0]


def test_default_agent_rpc_rejects_container_supplied_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result_line = swebench_module._canonical(
        {
            "op": "result",
            "value": {
                "status": "infrastructure_error",
                "attempted": False,
                "first_verified_response": False,
                "turn_count": 0,
                "request_count": 0,
                "output_tokens": 0,
                "locality_checks": 0,
                "bash_actions_only": True,
                "effective_step_limit": 100,
                "effective_wall_time_limit_seconds": 3600,
                "trajectory_sha256": "3" * 64,
                "model_patch_sha256": None,
                "prediction_handle": None,
                "host_response_receipt": _host_receipt(),
            },
        }
    )
    monkeypatch.setattr(
        swebench_module,
        "_control_plane_args",
        lambda _kwargs: _rpc_fixture_command("lines", result_line),
    )
    digest = "sha256:" + "a" * 64
    task_fingerprint = "a" * 64
    attempt_nonce = "0123456789abcdef"
    control_name, control_identity = swebench_module._control_container_identity(
        task_fingerprint_sha256=task_fingerprint,
        attempt_nonce=attempt_nonce,
        control_image_digest=digest,
    )
    control_labels = {
        swebench_module._CONTROL_LABEL_ROLE: "control",
        swebench_module._CONTROL_LABEL_IDENTITY: control_identity,
        swebench_module._CONTROL_LABEL_TASK: task_fingerprint,
        swebench_module._CONTROL_LABEL_ATTEMPT: attempt_nonce,
        swebench_module._CONTROL_LABEL_IMAGE: digest,
    }
    with pytest.raises(SwebenchError, match="may not supply"):
        swebench_module._default_agent_executor(
            docker_executable="/usr/local/bin/docker",
            control_image_reference=f"local/swebench-control@{digest}",
            control_image_digest=digest,
            control_container_name=control_name,
            control_identity_sha256=control_identity,
            evidence_dir=tmp_path,
            runner=_ControlCleanupRunner(control_name, control_labels),
            timeout_seconds=1,
            max_output_bytes=1_048_576,
            model="fixture-model",
            instance_id="task-instance",
            task_fingerprint_sha256=task_fingerprint,
            attempt_nonce=attempt_nonce,
            served_model_fingerprint="6" * 64,
        )


@pytest.mark.parametrize("name_matches", [True, False])
def test_control_evidence_keeps_runner_bytes_under_their_sha256(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name_matches: bool
) -> None:
    # The runner hashes ASCII-escaped canonical JSON; re-encoding would break the name.
    data = json.dumps({"messages": ["caf\u00e9"]}, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(data if name_matches else data + b" ").hexdigest()
    filename = f"trajectory-{digest}.json"
    line = json.dumps(
        {
            "op": "write_evidence",
            "filename": filename,
            "data_b64": base64.b64encode(data).decode(),
        }
    ).encode()
    monkeypatch.setattr(
        swebench_module, "_control_plane_args", lambda _kwargs: _rpc_fixture_command("lines", line)
    )
    kwargs = _default_rpc_kwargs(tmp_path, timeout_seconds=2, max_output_bytes=1_048_576)
    expected = "exited before|stdin write failed" if name_matches else "do not match"
    with pytest.raises(SwebenchError, match=expected):
        swebench_module._default_agent_executor(**kwargs)
    written = tmp_path / filename
    if name_matches:
        assert written.read_bytes() == data
    else:
        assert not written.exists()


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("silent", "deadline"),
        ("partial", "deadline"),
        ("lines", "exited before"),
    ],
)
def test_default_agent_rpc_bounds_silent_no_newline_and_early_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    message: str,
) -> None:
    kwargs = _default_rpc_kwargs(tmp_path)
    monkeypatch.setattr(
        swebench_module,
        "_control_plane_args",
        lambda _kwargs: _rpc_fixture_command(mode),
    )
    started = time.monotonic()
    with pytest.raises(SwebenchError, match=message):
        swebench_module._default_agent_executor(**kwargs)
    assert time.monotonic() - started < 2


def test_default_agent_rpc_bounds_stdout_flood(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kwargs = _default_rpc_kwargs(tmp_path, timeout_seconds=1)
    monkeypatch.setattr(
        swebench_module,
        "_control_plane_args",
        lambda _kwargs: _rpc_fixture_command("flood", count=swebench_module._RPC_STDOUT_LIMIT + 1),
    )
    with pytest.raises(SwebenchError, match="output exceeded"):
        swebench_module._default_agent_executor(**kwargs)


def test_default_agent_rpc_drains_and_bounds_stderr_flood(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    max_output_bytes = 1024
    kwargs = _default_rpc_kwargs(tmp_path, timeout_seconds=1, max_output_bytes=max_output_bytes)
    monkeypatch.setattr(
        swebench_module,
        "_control_plane_args",
        lambda _kwargs: _rpc_fixture_command("stderr-flood", count=max_output_bytes + 1),
    )
    with pytest.raises(SwebenchError, match="stderr exceeded"):
        swebench_module._default_agent_executor(**kwargs)


def test_default_agent_rpc_bounds_initial_write_to_nonreading_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kwargs = _default_rpc_kwargs(tmp_path, timeout_seconds=0.2)
    kwargs["unread_payload"] = "x" * 1_000_000
    monkeypatch.setattr(
        swebench_module,
        "_control_plane_args",
        lambda _kwargs: _rpc_fixture_command("silent"),
    )
    started = time.monotonic()
    with pytest.raises(SwebenchError, match="deadline"):
        swebench_module._default_agent_executor(**kwargs)
    assert time.monotonic() - started < 2


def test_read_control_line_rejects_expired_buffered_message() -> None:
    selector = swebench_module.selectors.DefaultSelector()
    try:
        with pytest.raises(SwebenchError, match="deadline"):
            swebench_module._read_control_line(
                selector,
                0,
                1,
                bytearray(b'{"op":"result"}\n'),
                deadline=time.monotonic() - 1,
                stderr_bytes=0,
                stderr_limit=1024,
            )
    finally:
        selector.close()


def test_default_agent_rpc_closes_selectors_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_selector = swebench_module.selectors.DefaultSelector
    created: list[Any] = []

    class TrackingSelector:
        def __init__(self) -> None:
            self._inner = real_selector()
            self.closed = False
            created.append(self)

        def register(self, *args: object, **kwargs: object) -> object:
            return self._inner.register(*args, **kwargs)

        def select(self, *args: object, **kwargs: object) -> object:
            return self._inner.select(*args, **kwargs)

        def unregister(self, *args: object, **kwargs: object) -> object:
            return self._inner.unregister(*args, **kwargs)

        def close(self) -> None:
            self.closed = True
            self._inner.close()

    kwargs = _default_rpc_kwargs(tmp_path, timeout_seconds=0.05)
    monkeypatch.setattr(swebench_module.selectors, "DefaultSelector", TrackingSelector)
    monkeypatch.setattr(
        swebench_module,
        "_control_plane_args",
        lambda _kwargs: _rpc_fixture_command("silent"),
    )
    with pytest.raises(SwebenchError, match="deadline"):
        swebench_module._default_agent_executor(**kwargs)
    assert len(created) == 2
    assert all(selector.closed for selector in created)


def test_default_agent_rpc_bounds_late_lm_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kwargs = _default_rpc_kwargs(tmp_path, timeout_seconds=0.05)
    lm_models_line = swebench_module._canonical({"op": "lm_models"})
    observed: list[float] = []

    def slow_http(*_args: object, **http_kwargs: object) -> Mapping[str, object]:
        observed.append(float(http_kwargs["timeout"]))
        time.sleep(0.1)
        return {"models": []}

    monkeypatch.setattr(
        swebench_module,
        "_control_plane_args",
        lambda _kwargs: _rpc_fixture_command("lines", lm_models_line),
    )
    monkeypatch.setattr(swebench_module, "_http_json", slow_http)
    with pytest.raises(SwebenchError, match="deadline"):
        swebench_module._default_agent_executor(**kwargs)
    assert observed and observed[0] <= _host_control_bound(kwargs)


def test_default_agent_rpc_bounds_late_task_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kwargs = _default_rpc_kwargs(tmp_path, timeout_seconds=0.05)
    kwargs["container_name"] = "fixture-container"
    task_line = swebench_module._canonical({"op": "task_exec", "command": "true"})
    observed: list[float | None] = []

    def slow_runner(*_args: object, **runner_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed.append(runner_kwargs.get("timeout"))
        time.sleep(0.1)
        return subprocess.CompletedProcess((), 0, b"", b"")

    kwargs["runner"] = slow_runner
    monkeypatch.setattr(swebench_module, "_cleanup_control_container", lambda _kwargs: None)
    monkeypatch.setattr(
        swebench_module,
        "_control_plane_args",
        lambda _kwargs: _rpc_fixture_command("lines", task_line),
    )
    with pytest.raises(SwebenchError, match="deadline"):
        swebench_module._default_agent_executor(**kwargs)
    assert observed and observed[0] is not None
    assert float(observed[0]) <= _host_control_bound(kwargs)


def test_container_arguments_enforce_isolation_with_distinct_task_and_grader_volumes() -> None:
    limits = {
        "timeout_seconds": 600,
        "memory_bytes": 4_294_967_296,
        "cpus": "4.0",
        "pids_limit": 256,
        "nofile_limit": 1024,
        "tmpfs_bytes": 268_435_456,
        "max_output_bytes": 1_048_576,
        "max_turns": 100,
        "max_requests": 100,
        "max_task_output_tokens": 6_553_600,
    }
    task = build_task_container_args(
        image_reference=f"local/task@{_TASK_DIGEST}",
        image_digest=_TASK_DIGEST,
        platform="linux/arm64/v8",
        limits=limits,
        name="swebench-task-0123456789abcdef",
    )
    grader = build_grader_container_args(
        image_reference=f"local/grader@{_GRADER_DIGEST}",
        image_digest=_GRADER_DIGEST,
        platform="linux/arm64/v8",
        limits=limits,
        name="swebench-grader-0123456789abcdef",
    )
    for command in (task, grader):
        rendered = " ".join(command)
        assert "--network none" in rendered
        assert "--read-only" in command
        assert "--cap-drop ALL" in rendered
        assert "no-new-privileges" in rendered
        assert "--privileged" not in command
        assert "--volume" not in command
        assert "-v" not in command
        assert "--publish" not in command
        assert "/var/run/docker.sock" not in rendered
        expected_tmpfs = "/tmp:rw,noexec,nosuid,nodev,size=268435456"  # noqa: S108 - container tmpfs
        assert command[command.index("--tmpfs") + 1] == expected_tmpfs
        assert any(item.endswith(_TASK_DIGEST) or item.endswith(_GRADER_DIGEST) for item in command)
    task_mount = task[task.index("--mount") + 1]
    assert task_mount == ("type=volume,src=swebench-task-0123456789abcdef-workspace,dst=/testbed")
    grader_mount = grader[grader.index("--mount") + 1]
    assert grader_mount == (
        "type=volume,src=swebench-grader-0123456789abcdef-workspace,dst=/testbed"
    )
    assert task_mount != grader_mount


@pytest.mark.parametrize("tmpfs_bytes", [0, -1, True, 1.5])
def test_resource_limits_reject_invalid_tmpfs_bytes(tmpfs_bytes: object) -> None:
    limits = {
        "timeout_seconds": 600,
        "memory_bytes": 4_294_967_296,
        "cpus": "4.0",
        "pids_limit": 256,
        "nofile_limit": 1024,
        "tmpfs_bytes": tmpfs_bytes,
        "max_output_bytes": 1_048_576,
        "max_turns": 100,
        "max_requests": 100,
        "max_task_output_tokens": 6_553_600,
    }
    with pytest.raises(SwebenchError, match="resource_limits.tmpfs_bytes"):
        swebench_module._validate_limits(limits)


def _default_grader_call(
    tmp_path: Path,
    *,
    stdout: bytes,
    stderr: bytes = b"captured stderr",
    returncode: int = 0,
    max_output_bytes: int = 1024,
    runner: Any = None,
    docker_executable: str | Path = "/usr/local/bin/docker",
    timeout_seconds: float = 30,
    wire_stdout: bytes | None = None,
    client_stderr: bytes = b"",
    eval_client_stderr: bytes = b"",
    eval_timeout: bool = False,
    commands_out: list[tuple[str, ...]] | None = None,
    include_official_marker: bool = True,
) -> Mapping[str, object]:
    evidence_dir = tmp_path / "private-evidence"
    evidence_dir.mkdir(mode=0o700)
    patch = b"diff --git a/file b/file\n"
    patch_sha256 = hashlib.sha256(patch).hexdigest()
    (evidence_dir / f"prediction-{patch_sha256}.patch").write_bytes(patch)

    eval_script = "#!/bin/bash\nset -uxo pipefail\necho official test\n"
    eval_script_sha256 = hashlib.sha256(eval_script.encode()).hexdigest()
    entrypoint_response = {
        "schema_version": 1,
        "phase": "patch_ready",
        "process_exit_code": 0,
        "eval_exit_code": None,
        "timed_out": False,
        "patch_sha256": patch_sha256,
        "eval_script_sha256": eval_script_sha256,
        "stdout_b64": base64.b64encode(b"patch applied\n").decode("ascii"),
        "stderr_b64": base64.b64encode(b"").decode("ascii"),
        "error": None,
    }
    response_bytes = (
        json.dumps(entrypoint_response, sort_keys=True, separators=(",", ":")).encode()
        if wire_stdout is None
        else wire_stdout
    )

    calls: list[tuple[str, ...]] = []

    def fake_runner(args: Sequence[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        command = tuple(args)
        calls.append(command)
        if command[-1] == "/usr/local/bin/racecraft-swebench-grade":
            return subprocess.CompletedProcess(command, 0, response_bytes, client_stderr)
        if eval_timeout:
            raise subprocess.TimeoutExpired(
                cmd=command,
                timeout=timeout_seconds,
                output=b"partial official output\n",
                stderr=b"",
            )
        test_output = stdout + stderr
        if (
            include_official_marker
            and swebench_module._TEST_EXIT_CODE_RE.search(test_output) is None
        ):
            test_output += f">>>>> Test Exit Code: {returncode}\n".encode()
        return subprocess.CompletedProcess(command, returncode, test_output, eval_client_stderr)

    result = swebench_module._default_grader_executor(
        evidence_dir=evidence_dir,
        prediction_handle=f"private://prediction-{patch_sha256}.patch",
        model_patch_sha256=patch_sha256,
        instance_id="example__repo-1",
        container_name="swebench-grader-0123456789abcdef",
        docker_executable=str(docker_executable),
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
        test_spec_eval_script=eval_script,
        test_spec_eval_script_sha256=eval_script_sha256,
        runner=runner or fake_runner,
    )
    if runner is None:
        assert len(calls) == (1 if wire_stdout is not None or client_stderr else 2)
    if commands_out is not None:
        commands_out.extend(calls)
    return result


@pytest.mark.parametrize(
    "stdout",
    [
        b'{"status":"resolved","tests_passed":true}',
        b"not-json: {",
        b">>>>>> Test Exit Code: 0\nPASS test_example\n",
    ],
)
def test_default_grader_never_accepts_container_authored_verdict(
    tmp_path: Path, stdout: bytes
) -> None:
    result = _default_grader_call(tmp_path, stdout=stdout)

    assert set(result) == swebench_module._GRADER_CAPTURE_KEYS
    assert result["error"] is None
    assert result["patch_sha256"] == hashlib.sha256(b"diff --git a/file b/file\n").hexdigest()
    expected_output = stdout + b"captured stderr"
    if swebench_module._TEST_EXIT_CODE_RE.search(expected_output) is None:
        expected_output += b">>>>> Test Exit Code: 0\n"
    assert result["stdout"] == expected_output
    assert result["process_exit_code"] == 0
    assert result["eval_launcher_exit_code"] == 0
    assert result["eval_exit_code"] == 0
    assert result["phase"] == "patch_ready"
    assert "status" not in result and "grade" not in result


def test_default_grader_bounds_persisted_output_and_keeps_launcher_test_exits_distinct(
    tmp_path: Path,
) -> None:
    result = _default_grader_call(
        tmp_path,
        stdout=b"x" * 100,
        stderr=b"y" * 100,
        returncode=7,
        max_output_bytes=16,
    )

    assert result["error"] == "eval_output_limit"
    assert result["eval_exit_code"] is None
    combined = len(cast(bytes, result["stdout"])) + len(cast(bytes, result["stderr"]))
    assert combined <= 16 * 2 + 64 * 1024


def test_default_grader_preserves_an_observed_nonzero_test_exit(tmp_path: Path) -> None:
    result = _default_grader_call(
        tmp_path,
        stdout=b"official test failure\n",
        stderr=b"",
        returncode=7,
    )

    assert result["error"] is None
    assert result["eval_launcher_exit_code"] == 7
    assert result["eval_exit_code"] == 7


def test_default_grader_applies_then_invokes_baked_eval_exactly_once(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []
    result = _default_grader_call(
        tmp_path,
        stdout=b"official tests completed\n",
        stderr=b"",
        commands_out=calls,
    )

    assert result["error"] is None
    assert len(calls) == 2
    assert calls[0][-1] == "/usr/local/bin/racecraft-swebench-grade"
    assert calls[1][-3:] == (
        "/bin/bash",
        "-c",
        "exec /bin/bash /opt/racecraft/grader/test-spec-eval.sh 2>&1",
    )
    assert sum("test-spec-eval.sh" in argument for argument in calls[1]) == 1


def test_default_grader_timeout_during_eval_has_no_exit_receipt(tmp_path: Path) -> None:
    result = _default_grader_call(
        tmp_path,
        stdout=b"unused",
        eval_timeout=True,
    )

    assert result["phase"] == "patch_ready"
    assert result["timed_out"] is True
    assert result["eval_launcher_exit_code"] is None
    assert result["eval_exit_code"] is None
    assert result["error"] == "eval_timeout"
    assert result["stdout"] == b"partial official output\n"


def test_production_eval_timeout_drops_killed_launcher_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The bounded subprocess reports the killed launcher's status; it is not a receipt.
    real_call = _default_grader_call
    captured: list[tuple[str, ...]] = []

    def bounded(command: Sequence[str], **_kwargs: object) -> Any:
        captured.append(tuple(command))
        if tuple(command)[-1] == "/usr/local/bin/racecraft-swebench-grade":
            raise AssertionError("apply is served by the recorded response below")
        return swebench_module._BoundedProcessCapture(
            stdout=b"partial official output\n",
            stderr=b"",
            stdout_bytes=24,
            stderr_bytes=0,
            launcher_exit_code=-9,
            timed_out=True,
            output_exceeded=False,
        )

    apply_response: list[bytes] = []

    def fake_bounded(command: Sequence[str], **kwargs: object) -> Any:
        if tuple(command)[-1] == "/usr/local/bin/racecraft-swebench-grade":
            return swebench_module._BoundedProcessCapture(
                stdout=apply_response[0],
                stderr=b"",
                stdout_bytes=len(apply_response[0]),
                stderr_bytes=0,
                launcher_exit_code=0,
                timed_out=False,
                output_exceeded=False,
            )
        return bounded(command, **kwargs)

    patch = b"diff --git a/file b/file\n"
    eval_script = "#!/bin/bash\nset -uxo pipefail\necho official test\n"
    apply_response.append(
        json.dumps(
            {
                "schema_version": 1,
                "phase": "patch_ready",
                "process_exit_code": 0,
                "eval_exit_code": None,
                "timed_out": False,
                "patch_sha256": hashlib.sha256(patch).hexdigest(),
                "eval_script_sha256": hashlib.sha256(eval_script.encode()).hexdigest(),
                "stdout_b64": base64.b64encode(b"patch applied\n").decode("ascii"),
                "stderr_b64": "",
                "error": None,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    monkeypatch.setattr(swebench_module, "_run_bounded_subprocess", fake_bounded)
    result = real_call(tmp_path, stdout=b"unused", runner=subprocess.run)

    assert result["error"] == "eval_timeout"
    assert result["timed_out"] is True
    assert result["eval_launcher_exit_code"] is None
    assert result["eval_exit_code"] is None


def test_default_grader_refuses_docker_client_stderr(tmp_path: Path) -> None:
    result = _default_grader_call(
        tmp_path,
        stdout=b"unused",
        client_stderr=b"warning: transport ambiguity\n",
    )

    assert result["error"] == "docker_client_stderr"
    assert result["phase"] == "apply_failed"
    assert result["eval_exit_code"] is None


def test_default_grader_refuses_eval_when_docker_transport_writes_stderr(
    tmp_path: Path,
) -> None:
    result = _default_grader_call(
        tmp_path,
        stdout=b"official output but transport is ambiguous\n",
        eval_client_stderr=b"warning: exec stream closed early\n",
    )

    assert result["phase"] == "patch_ready"
    assert result["error"] == "docker_client_stderr"
    assert result["eval_launcher_exit_code"] == 0
    assert result["eval_exit_code"] is None


def test_default_grader_rejects_incomplete_official_log(tmp_path: Path) -> None:
    result = _default_grader_call(
        tmp_path,
        stdout=b"partial official test output with no exit marker\n",
        stderr=b"",
        include_official_marker=False,
    )

    assert result["error"] == "incomplete_official_log"
    assert result["eval_launcher_exit_code"] == 0
    assert result["eval_exit_code"] is None


def test_default_grader_records_timeout_without_inventing_test_exit(
    tmp_path: Path,
) -> None:
    def timed_out_runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired(
            cmd="docker exec grader",
            timeout=30,
            output=b"partial stdout",
            stderr=b"partial stderr",
        )

    result = _default_grader_call(
        tmp_path,
        stdout=b"unused",
        runner=timed_out_runner,
    )

    assert result["error"] == "apply_timeout"
    assert result["timed_out"] is True
    assert result["launcher_exit_code"] is None
    assert result["eval_exit_code"] is None
    assert result["stdout"] == b"partial stdout"
    assert result["stderr"] == b"partial stderr"


def test_default_grader_kills_noisy_launcher_at_combined_output_bound(
    tmp_path: Path,
) -> None:
    fake_docker = tmp_path / "noisy-docker"
    fake_docker.write_text(
        f"#!{sys.executable}\n"
        "import os\n"
        "chunk = b'x' * 65536\n"
        "while True:\n"
        "    os.write(1, chunk)\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o700)

    result = _default_grader_call(
        tmp_path,
        stdout=b"unused fake-runner output",
        max_output_bytes=4096,
        runner=subprocess.run,
        docker_executable=fake_docker,
    )

    assert result["error"] == "apply_output_limit"
    assert result["eval_exit_code"] is None
    assert cast(bytes, result["stdout"]).startswith(b"x" * 4096)


def test_default_grader_timeout_reaps_launcher_without_test_exit(
    tmp_path: Path,
) -> None:
    fake_docker = tmp_path / "sleeping-docker"
    fake_docker.write_text(
        f"#!{sys.executable}\nimport time\ntime.sleep(60)\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o700)
    started = time.monotonic()

    result = _default_grader_call(
        tmp_path,
        stdout=b"unused fake-runner output",
        max_output_bytes=4096,
        runner=subprocess.run,
        docker_executable=fake_docker,
        timeout_seconds=0.1,
    )

    assert time.monotonic() - started < 3
    assert result["error"] == "apply_timeout"
    assert result["timed_out"] is True
    assert result["eval_exit_code"] is None


def test_grader_attestation_accepts_its_exact_writable_workspace_volume() -> None:
    runner = _FakeRunner()
    name = "swebench-grader-0123456789abcdef"
    swebench_module._attest_container(
        _runtime(runner),
        name,
        {
            "timeout_seconds": 600,
            "max_output_bytes": 1_048_576,
            "pids_limit": 256,
            "memory_bytes": 4_294_967_296,
        },
        role="grader",
    )
    assert ("/usr/local/bin/docker", "inspect", name) in runner.calls


@pytest.mark.parametrize("role", ["task", "grader"])
@pytest.mark.parametrize(
    ("environment", "accepted"),
    [
        (["HOME=/tmp/home", "PATH=/usr/local/bin:/usr/bin:/bin"], True),
        (
            [
                "HOME=/tmp/home",
                "PATH=/usr/local/bin:/usr/bin:/bin",
                "TZ=Etc/UTC",
            ],
            True,
        ),
        (
            [
                "HOME=/tmp/home",
                "PATH=/usr/local/bin:/usr/bin:/bin",
                "TZ=UTC",
            ],
            False,
        ),
        (
            [
                "HOME=/tmp/home",
                "PATH=/usr/local/bin:/usr/bin:/bin",
                "TZ=Etc/UTC",
                "API_KEY=example",
            ],
            False,
        ),
    ],
)
def test_container_attestation_allows_only_expected_environment(
    role: str, environment: list[str], accepted: bool
) -> None:
    runner = _FakeRunner()
    name = f"swebench-{role}-0123456789abcdef"
    base_runner = runner

    def environment_runner(args: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        completed = base_runner(args, **kwargs)
        if tuple(args[1:2]) == ("inspect",):
            payload = json.loads(completed.stdout)
            payload[0]["Config"]["Env"] = environment
            return subprocess.CompletedProcess(
                tuple(args), completed.returncode, json.dumps(payload).encode(), completed.stderr
            )
        return completed

    runtime = replace(_runtime(runner), runner=environment_runner)
    limits = {
        "timeout_seconds": 600,
        "max_output_bytes": 1_048_576,
        "pids_limit": 256,
        "memory_bytes": 4_294_967_296,
    }
    if accepted:
        swebench_module._attest_container(runtime, name, limits, role=role)
    else:
        with pytest.raises(
            SwebenchError, match="container attestation does not match the safety contract"
        ):
            swebench_module._attest_container(runtime, name, limits, role=role)


def test_grader_attestation_rejects_any_unexpected_mount() -> None:
    runner = _FakeRunner()
    name = "swebench-grader-0123456789abcdef"
    runner.mounts_override = [
        {
            "Type": "volume",
            "Name": f"{name}-workspace",
            "Destination": "/testbed",
            "RW": True,
        },
        {
            "Type": "bind",
            "Name": "unexpected-host-path",
            "Destination": "/unexpected",
            "RW": True,
        },
    ]
    with pytest.raises(SwebenchError, match="grader workspace volume attestation failed"):
        swebench_module._attest_container(
            _runtime(runner),
            name,
            {
                "timeout_seconds": 600,
                "max_output_bytes": 1_048_576,
                "pids_limit": 256,
                "memory_bytes": 4_294_967_296,
            },
            role="grader",
        )


def test_grader_attestation_binds_live_container_and_image_digest() -> None:
    runner = _FakeRunner()
    name = "swebench-grader-0123456789abcdef"
    reference = f"local/swebench-grader@{_GRADER_DIGEST}"
    runner.created_images[name] = reference
    limits = {
        "timeout_seconds": 600,
        "max_output_bytes": 1_048_576,
        "pids_limit": 256,
        "memory_bytes": 4_294_967_296,
    }

    swebench_module._attest_container(
        _runtime(runner),
        name,
        limits,
        role="grader",
        image_reference=reference,
        image_digest=_GRADER_DIGEST,
    )
    assert any(call[1:4] == ("image", "inspect", reference) for call in runner.calls)

    runner.created_images[name] = f"local/other-grader@{_GRADER_DIGEST}"
    with pytest.raises(SwebenchError, match="container image identity drifted"):
        swebench_module._attest_container(
            _runtime(runner),
            name,
            limits,
            role="grader",
            image_reference=reference,
            image_digest=_GRADER_DIGEST,
        )


def test_default_control_plane_command_uses_exact_pinned_image() -> None:
    digest = "sha256:" + "a" * 64
    reference = f"local/swebench-control@{digest}"
    task_fingerprint = "b" * 64
    attempt_nonce = "0123456789abcdef"
    control_name, control_identity = swebench_module._control_container_identity(
        task_fingerprint_sha256=task_fingerprint,
        attempt_nonce=attempt_nonce,
        control_image_digest=digest,
    )
    command = swebench_module._control_plane_args(
        {
            "docker_executable": "/usr/local/bin/docker",
            "control_image_reference": reference,
            "control_image_digest": digest,
            "control_container_name": control_name,
            "control_identity_sha256": control_identity,
            "task_fingerprint_sha256": task_fingerprint,
            "attempt_nonce": attempt_nonce,
        }
    )
    assert command[:4] == ("/usr/local/bin/docker", "run", "--pull=never", "--rm")
    assert reference in command
    assert command[command.index("--name") + 1] == control_name
    rendered = " ".join(command)
    assert f"{swebench_module._CONTROL_LABEL_IDENTITY}={control_identity}" in rendered
    assert f"{swebench_module._CONTROL_LABEL_TASK}={task_fingerprint}" in rendered
    assert f"{swebench_module._CONTROL_LABEL_ATTEMPT}={attempt_nonce}" in rendered
    assert f"{swebench_module._CONTROL_LABEL_IMAGE}={digest}" in rendered
    assert "--network" in command and command[command.index("--network") + 1] == "none"
    assert "--read-only" in command
    assert "--mount" not in command and "--volume" not in command
    assert command[-2:] == ("python", "/opt/racecraft/runner.py")


def test_control_cleanup_removes_matching_container_and_verifies_absence(
    tmp_path: Path,
) -> None:
    kwargs = _default_rpc_kwargs(tmp_path)
    runner = kwargs["runner"]
    assert isinstance(runner, _ControlCleanupRunner)
    swebench_module._cleanup_control_container(kwargs)
    assert runner.present is False
    assert any("rm" in call and "-f" in call for call in runner.calls)


def test_control_cleanup_refuses_mismatched_labels_without_removal(tmp_path: Path) -> None:
    kwargs = _default_rpc_kwargs(tmp_path)
    expected_runner = kwargs["runner"]
    assert isinstance(expected_runner, _ControlCleanupRunner)
    wrong_labels = dict(expected_runner.labels)
    wrong_labels[swebench_module._CONTROL_LABEL_TASK] = "f" * 64
    runner = _ControlCleanupRunner(expected_runner.name, wrong_labels)
    kwargs["runner"] = runner
    with pytest.raises(SwebenchError, match="identity labels"):
        swebench_module._cleanup_control_container(kwargs)
    assert runner.present is True
    assert not any("rm" in call and "-f" in call for call in runner.calls)


def test_control_cleanup_fails_closed_when_container_survives_forced_remove(
    tmp_path: Path,
) -> None:
    kwargs = _default_rpc_kwargs(tmp_path)
    expected_runner = kwargs["runner"]
    assert isinstance(expected_runner, _ControlCleanupRunner)
    runner = _ControlCleanupRunner(
        expected_runner.name,
        expected_runner.labels,
        survive_remove=True,
    )
    kwargs["runner"] = runner
    with pytest.raises(SwebenchError, match="could not be verified"):
        swebench_module._cleanup_control_container(kwargs)
    assert runner.present is True


def test_control_cleanup_accepts_auto_remove_race_after_authoritative_absence(
    tmp_path: Path,
) -> None:
    kwargs = _default_rpc_kwargs(tmp_path)
    expected_runner = kwargs["runner"]
    assert isinstance(expected_runner, _ControlCleanupRunner)
    runner = _ControlCleanupRunner(
        expected_runner.name,
        expected_runner.labels,
        auto_remove_before_rm=True,
    )
    kwargs["runner"] = runner
    swebench_module._cleanup_control_container(kwargs)
    assert runner.present is False
    assert any("rm" in call and "-f" in call for call in runner.calls)


def test_control_cleanup_waits_for_in_progress_auto_remove(tmp_path: Path) -> None:
    kwargs = _default_rpc_kwargs(tmp_path)
    expected_runner = kwargs["runner"]
    assert isinstance(expected_runner, _ControlCleanupRunner)
    runner = _ControlCleanupRunner(
        expected_runner.name, expected_runner.labels, removal_in_progress_polls=3
    )
    kwargs["runner"] = runner
    swebench_module._cleanup_control_container(kwargs)
    assert runner.present is False


def test_control_cleanup_accepts_auto_removal_hidden_from_ps_before_inspect(
    tmp_path: Path,
) -> None:
    kwargs = _default_rpc_kwargs(tmp_path)
    expected_runner = kwargs["runner"]
    assert isinstance(expected_runner, _ControlCleanupRunner)
    runner = _ControlCleanupRunner(
        expected_runner.name, expected_runner.labels, vanishing_inspects=4
    )
    kwargs["runner"] = runner
    swebench_module._cleanup_control_container(kwargs)
    assert runner.present is False


def test_control_cleanup_fails_closed_when_hidden_container_never_disappears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kwargs = _default_rpc_kwargs(tmp_path)
    expected_runner = kwargs["runner"]
    assert isinstance(expected_runner, _ControlCleanupRunner)
    runner = _ControlCleanupRunner(
        expected_runner.name, expected_runner.labels, vanishing_inspects=10**9
    )
    kwargs["runner"] = runner
    monkeypatch.setattr(swebench_module, "_CONTROL_REMOVAL_WAIT_SECONDS", 0.2)
    with pytest.raises(SwebenchError, match="ambiguous"):
        swebench_module._cleanup_control_container(kwargs)


def test_control_cleanup_fails_closed_when_in_progress_removal_never_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kwargs = _default_rpc_kwargs(tmp_path)
    expected_runner = kwargs["runner"]
    assert isinstance(expected_runner, _ControlCleanupRunner)
    runner = _ControlCleanupRunner(
        expected_runner.name, expected_runner.labels, removal_in_progress_polls=10**9
    )
    kwargs["runner"] = runner
    monkeypatch.setattr(swebench_module, "_CONTROL_REMOVAL_WAIT_SECONDS", 0.2)
    with pytest.raises(SwebenchError, match="could not be verified"):
        swebench_module._cleanup_control_container(kwargs)
    assert runner.present is True


def test_default_agent_rpc_captures_immutable_control_id_before_exchange(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kwargs = _default_rpc_kwargs(tmp_path)
    control_id = "c" * 64
    result_line = swebench_module._canonical(
        {"op": "result", "value": {"host_response_receipt": None}}
    ).decode("utf-8")
    cidfile = tmp_path / f"{kwargs['control_container_name']}.cid"
    fixture = (
        "from pathlib import Path; import sys; import time; "
        "Path(sys.argv[1]).write_text(sys.argv[3], encoding='ascii'); "
        "print(sys.argv[2], flush=True); time.sleep(30)"
    )
    monkeypatch.setattr(
        swebench_module,
        "_control_plane_args",
        lambda _kwargs: (
            sys.executable,
            "-u",
            "-c",
            fixture,
            str(cidfile),
            result_line,
            control_id,
            "--cidfile",
        ),
    )
    observed: list[str] = []
    monkeypatch.setattr(
        swebench_module,
        "_cleanup_control_container",
        lambda cleanup_kwargs: observed.append(str(cleanup_kwargs["control_container_id"])),
    )
    result = swebench_module._default_agent_executor(
        **kwargs,
        on_control_container_id=lambda value: observed.append(value),
    )
    assert result["host_response_receipt"] is None
    assert observed == [control_id, control_id]


def test_checkpoint_recovery_uses_attested_control_id_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kwargs = _default_rpc_kwargs(tmp_path)
    runner = kwargs["runner"]
    assert isinstance(runner, _ControlCleanupRunner)
    digest = str(kwargs["control_image_digest"])
    monkeypatch.setenv(
        "LOCAL_EVALS_SWEBENCH_CONTROL_IMAGE_REFERENCE",
        str(kwargs["control_image_reference"]),
    )
    monkeypatch.setenv("LOCAL_EVALS_SWEBENCH_CONTROL_IMAGE_DIGEST", digest)
    prepared = SimpleNamespace(
        profile={"resource_limits": {"timeout_seconds": 1, "max_output_bytes": 1024}}
    )
    runtime = SimpleNamespace(
        docker_executable=Path(str(kwargs["docker_executable"])),
        runner=runner,
    )
    checkpoint = {
        "control_image_digest": digest,
        "control_container_name": kwargs["control_container_name"],
        "control_identity_sha256": kwargs["control_identity_sha256"],
        "control_container_id": runner.container_id,
        "task_fingerprint_sha256": kwargs["task_fingerprint_sha256"],
        "attempt_nonce": kwargs["attempt_nonce"],
    }
    swebench_module._recover_control_checkpoint(
        checkpoint,
        checkpoint_path=tmp_path / "checkpoint-0000.json",
        prepared=prepared,
        runtime=runtime,
    )
    assert runner.present is False


def _response_verified_checkpoint(
    checkpoint: dict[str, Any], sdk_budget: Mapping[str, Any]
) -> dict[str, Any]:
    recovered = dict(checkpoint)
    recovered.update(
        {
            "state": "response_verified",
            "attempted": True,
            "status": "response_verified",
            "sdk_budget": dict(sdk_budget),
            "output_tokens": None,
            "prompt_tokens": None,
            "finish_reason": None,
            "context_length": None,
            "context_truncation_status": None,
            "truncation_status": None,
            "trajectory_elapsed_seconds": None,
            "trajectory_sha256": None,
            "model_patch_sha256": None,
            "grader_evidence_sha256": None,
            "grade": None,
        }
    )
    name, identity = swebench_module._control_container_identity(
        task_fingerprint_sha256=recovered["task_fingerprint_sha256"],
        attempt_nonce=recovered["attempt_nonce"],
        control_image_digest=_CONTROL_DIGEST,
    )
    recovered.update(
        {
            "control_image_digest": _CONTROL_DIGEST,
            "control_container_name": name,
            "control_identity_sha256": identity,
        }
    )
    return recovered


@pytest.mark.parametrize(
    ("budget_state", "actual_tokens"),
    [("completed", 37), ("reserved", None), ("stopped", None)],
)
def test_resume_recovers_verified_response_without_retry_or_invented_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    budget_state: str,
    actual_tokens: int | None,
) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state, count=2)
    base_runtime = _runtime(_FakeRunner())
    initial = execute_swebench(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        runtime=base_runtime,
    )
    run_dir = state / "swebench" / "runs" / initial["run_id"]
    checkpoint_path = run_dir / "checkpoint-0000.json"
    second_checkpoint_path = run_dir / "checkpoint-0001.json"
    second_checkpoint = json.loads(second_checkpoint_path.read_text(encoding="utf-8"))
    second_checkpoint_path.unlink()
    budget = {
        "requests": 1,
        "charged_output_tokens": 64,
        "last_actual_output_tokens": actual_tokens,
        "actual_output_tokens": actual_tokens,
        "last_request_sha256": "a" * 64,
        "state": budget_state,
    }
    interrupted = _response_verified_checkpoint(
        json.loads(checkpoint_path.read_text(encoding="utf-8")), budget
    )
    swebench_module._write_private_json(checkpoint_path, interrupted)
    monkeypatch.setattr(
        swebench_module,
        "_control_image_identity",
        lambda: (str(profile["control_image_reference"]), _CONTROL_DIGEST),
    )
    cleanup_calls: list[Mapping[str, object]] = []
    monkeypatch.setattr(
        swebench_module,
        "_cleanup_control_container",
        lambda kwargs: cleanup_calls.append(kwargs),
    )
    attempted_ids: list[str] = []

    def record_attempt(**kwargs: object) -> Mapping[str, object]:
        attempted_ids.append(str(kwargs["instance_id"]))
        return _fixture_agent(**kwargs)

    resumed = resume_swebench(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        run_id=initial["run_id"],
        runtime=replace(base_runtime, agent_executor=record_attempt),
    )

    recovered = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert recovered["state"] == "terminal"
    assert recovered["status"] == "infrastructure_error"
    assert recovered["attempted"] is True
    assert recovered["sdk_budget"] == budget
    assert recovered["trajectory_sha256"] is None
    assert recovered["model_patch_sha256"] is None
    assert recovered["grader_evidence_sha256"] is None
    assert recovered["grade"] is None
    assert recovered["output_tokens"] is None
    assert recovered["prompt_tokens"] is None
    assert recovered["attempt_nonce"] == interrupted["attempt_nonce"]
    assert len(cleanup_calls) == 1
    assert len(attempted_ids) == 1
    assert (
        hashlib.sha256(attempted_ids[0].encode()).hexdigest()
        == second_checkpoint["instance_id_sha256"]
    )
    assert resumed["run_id"] == initial["run_id"]


@pytest.mark.parametrize(
    ("failure", "expected_error"),
    [
        ("identity", "control image binding drifted"),
        ("cleanup", "synthetic cleanup refusal"),
    ],
)
def test_resume_refuses_verified_response_recovery_on_cleanup_or_identity_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    expected_error: str,
) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state, count=1)
    base_runtime = _runtime(_FakeRunner())
    initial = execute_swebench(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        runtime=base_runtime,
    )
    run_dir = state / "swebench" / "runs" / initial["run_id"]
    checkpoint_path = run_dir / "checkpoint-0000.json"
    budget = {
        "requests": 1,
        "charged_output_tokens": 64,
        "last_actual_output_tokens": None,
        "actual_output_tokens": None,
        "last_request_sha256": "a" * 64,
        "state": "reserved",
    }
    interrupted = _response_verified_checkpoint(
        json.loads(checkpoint_path.read_text(encoding="utf-8")), budget
    )
    swebench_module._write_private_json(checkpoint_path, interrupted)
    before = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    mismatched_digest = f"sha256:{'f' * 64}"
    current_digest = mismatched_digest if failure == "identity" else _CONTROL_DIGEST
    monkeypatch.setattr(
        swebench_module,
        "_control_image_identity",
        lambda: (str(profile["control_image_reference"]), current_digest),
    )
    cleanup_calls: list[Mapping[str, object]] = []

    def fail_cleanup(kwargs: Mapping[str, object]) -> None:
        cleanup_calls.append(kwargs)
        raise SwebenchError("synthetic cleanup refusal")

    monkeypatch.setattr(swebench_module, "_cleanup_control_container", fail_cleanup)
    retry_calls = 0

    def reject_retry(**kwargs: object) -> Mapping[str, object]:
        nonlocal retry_calls
        retry_calls += 1
        return _fixture_agent(**kwargs)

    with pytest.raises(SwebenchError, match=expected_error):
        resume_swebench(
            profile,
            repo=repo,
            state=state,
            server_origin="http://127.0.0.1:1234/v1",
            run_id=initial["run_id"],
            runtime=replace(base_runtime, agent_executor=reject_retry),
        )

    assert retry_calls == 0
    if failure == "identity":
        assert cleanup_calls == []
    else:
        assert len(cleanup_calls) == 1
    assert json.loads(checkpoint_path.read_text(encoding="utf-8")) == before


def test_default_agent_rpc_timeout_cleans_control_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kwargs = _default_rpc_kwargs(tmp_path, timeout_seconds=0.05)
    runner = kwargs["runner"]
    assert isinstance(runner, _ControlCleanupRunner)
    monkeypatch.setattr(
        swebench_module,
        "_control_plane_args",
        lambda _kwargs: _rpc_fixture_command("silent"),
    )
    with pytest.raises(SwebenchError, match="deadline"):
        swebench_module._default_agent_executor(**kwargs)
    assert runner.present is False


def test_default_agent_rpc_cancelled_exchange_cleans_control_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kwargs = _default_rpc_kwargs(tmp_path)
    runner = kwargs["runner"]
    assert isinstance(runner, _ControlCleanupRunner)

    class ControlProcess:
        stdin = None
        stdout = None
        stderr = None

        def kill(self) -> None:
            return None

        def wait(self, *, timeout: float) -> int:
            return 1

    # Never launch Docker from a unit test; the exchange below fails before any I/O.
    monkeypatch.setattr(
        swebench_module.subprocess, "Popen", lambda *_args, **_kwargs: ControlProcess()
    )

    def cancelled_exchange(*_args: object, **_kwargs: object) -> Mapping[str, object]:
        raise SwebenchError("control-plane request cancelled")

    monkeypatch.setattr(swebench_module, "_control_plane_exchange", cancelled_exchange)
    with pytest.raises(SwebenchError, match="cancelled"):
        swebench_module._default_agent_executor(**kwargs)
    assert runner.present is False


def test_default_agent_rpc_crash_cleans_control_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kwargs = _default_rpc_kwargs(tmp_path)
    runner = kwargs["runner"]
    assert isinstance(runner, _ControlCleanupRunner)

    class CrashedProcess:
        stdin = None
        stdout = None
        stderr = None

        def kill(self) -> None:
            return None

        def wait(self, *, timeout: float) -> int:
            return 1

    monkeypatch.setattr(
        swebench_module.subprocess, "Popen", lambda *_args, **_kwargs: CrashedProcess()
    )

    def crashed_exchange(*_args: object, **_kwargs: object) -> Mapping[str, object]:
        raise KeyboardInterrupt("synthetic control crash")

    monkeypatch.setattr(swebench_module, "_control_plane_exchange", crashed_exchange)
    with pytest.raises(KeyboardInterrupt, match="synthetic control crash"):
        swebench_module._default_agent_executor(**kwargs)
    assert runner.present is False


def test_default_control_plane_refuses_digest_mismatch_or_unavailable_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digest = "sha256:" + "a" * 64
    task_fingerprint = "b" * 64
    attempt_nonce = "0123456789abcdef"
    control_name, control_identity = swebench_module._control_container_identity(
        task_fingerprint_sha256=task_fingerprint,
        attempt_nonce=attempt_nonce,
        control_image_digest=digest,
    )
    with pytest.raises(SwebenchError, match="exact digest"):
        swebench_module._control_plane_args(
            {
                "docker_executable": "/usr/local/bin/docker",
                "control_image_reference": f"local/swebench-control@sha256:{'b' * 64}",
                "control_image_digest": digest,
                "control_container_name": control_name,
                "control_identity_sha256": control_identity,
                "task_fingerprint_sha256": task_fingerprint,
                "attempt_nonce": attempt_nonce,
            }
        )

    def unavailable(*_args: object, **_kwargs: object) -> subprocess.Popen[bytes]:
        raise OSError("synthetic missing image")

    monkeypatch.setattr(swebench_module.subprocess, "Popen", unavailable)
    with pytest.raises(SwebenchError, match="control-plane image is unavailable"):
        swebench_module._default_agent_executor(
            docker_executable="/usr/local/bin/docker",
            control_image_reference=f"local/swebench-control@{digest}",
            control_image_digest=digest,
            control_container_name=control_name,
            control_identity_sha256=control_identity,
            evidence_dir=tmp_path,
            runner=subprocess.run,
            timeout_seconds=1,
            max_output_bytes=1024,
            task_fingerprint_sha256=task_fingerprint,
            attempt_nonce=attempt_nonce,
        )


def test_readiness_validates_frozen_manifest_and_parameter_support(
    tmp_path: Path,
) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state)
    report = inspect_swebench_readiness(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        runtime=_runtime(_FakeRunner()),
    )
    assert report["status"] == "ready"
    assert report["blockers"] == []
    assert report["metadata"]["mode"] == "qualification"
    assert report["metadata"]["non_capability"] is True
    assert "manifest_path" not in json.dumps(report)
    assert "image_bindings_path" not in json.dumps(report)


def test_readiness_rejects_legacy_singleton_image_authorization(tmp_path: Path) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state)
    profile["task_image_reference"] = f"local/task@{_TASK_DIGEST}"
    profile["task_image_digest"] = _TASK_DIGEST
    report = inspect_swebench_readiness(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        runtime=_runtime(_FakeRunner()),
    )
    assert report["status"] == "blocked"
    assert report["blockers"] == ["swebench profile has missing or unsupported fields"]


def test_image_bindings_bind_exact_source_record_and_build_inputs(tmp_path: Path) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state)
    bindings_path = Path(profile["image_bindings_path"])
    bindings = json.loads(bindings_path.read_text(encoding="utf-8"))
    bindings["bindings"][0]["source_record_sha256"] = "f" * 64
    bindings_path.write_text(
        json.dumps(bindings, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    profile["image_bindings_sha256"] = _sha256(bindings_path)
    report = inspect_swebench_readiness(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        runtime=_runtime(_FakeRunner()),
    )
    assert report["status"] == "blocked"
    assert report["blockers"] == ["image binding does not match its frozen source task"]


def test_execution_selects_each_instances_exact_task_and_grader_images(tmp_path: Path) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state, count=2)
    bindings = json.loads(Path(profile["image_bindings_path"]).read_text(encoding="utf-8"))[
        "bindings"
    ]
    runner = _FakeRunner()
    result = execute_swebench(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        runtime=_runtime(runner),
    )
    assert result["status"] == "completed"
    creates = [call for call in runner.calls if len(call) > 1 and call[1] == "create"]
    created_images = [
        next(item for item in call if item.startswith("local/swebench-")) for call in creates
    ]
    assert created_images == [
        bindings[0]["task_image_reference"],
        bindings[0]["grader_image_reference"],
        bindings[1]["task_image_reference"],
        bindings[1]["grader_image_reference"],
    ]
    checkpoints = sorted((state / "swebench" / "runs").glob("*/checkpoint-*.json"))
    assert [json.loads(path.read_text())["image_binding_sha256"] for path in checkpoints] == [
        hashlib.sha256(
            json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        for binding in bindings
    ]


def test_checked_in_profiles_require_private_per_instance_bindings() -> None:
    profiles = {}
    for name, count in (("swebench-qualification.yaml", 3), ("swebench-verified.yaml", 500)):
        profile = yaml.safe_load((Path("configs/profiles") / name).read_text())["swebench"]
        profiles[name] = profile
        assert "task_image_reference" not in profile
        assert "grader_image_reference" not in profile
        assert profile["image_bindings_task_count"] == count

    qualification = profiles["swebench-qualification.yaml"]
    assert qualification["image_bindings_sha256"] == (
        "e7762a3f20b9acfee756cf878de00f3d290d4a37f05868bb82ce67ec1fbbbb7a"
    )
    assert qualification["image_bindings_ordered_instance_ids_sha256"] == (
        "432e692e5692bbe379c2e26e0c5a09618410e568915670c3a2bdefe29b387cb2"
    )

    verified = profiles["swebench-verified.yaml"]
    assert verified["image_bindings_sha256"] == (
        "0ee0dac095d1ea165ce138f7b9266c6552742b50d6e30ac0d8f4ca14c0fa018e"
    )
    assert verified["image_bindings_ordered_instance_ids_sha256"] == (
        "33e18be7a9bd9f674790b63ed4d0b3fb17c176994802e3062b7d5a430a4e7d16"
    )


def test_manifest_path_may_be_private_state_relative(tmp_path: Path) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state)
    profile["manifest_path"] = Path(profile["manifest_path"]).relative_to(state).as_posix()
    report = inspect_swebench_readiness(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        runtime=_runtime(_FakeRunner()),
    )
    assert report["status"] == "ready"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("manifest_sha256", "0" * 64),
        ("image_bindings_sha256", "0" * 64),
        ("platform", "linux/arm64/v8"),
        ("mini_swe_agent_version", "latest"),
    ],
)
def test_readiness_fails_closed_on_protocol_drift(
    tmp_path: Path, field: str, value: object
) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state)
    profile[field] = value
    report = inspect_swebench_readiness(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        runtime=_runtime(_FakeRunner()),
    )
    assert report["status"] == "blocked"
    assert report["blockers"]


def test_parameter_probe_must_explicitly_accept_every_requested_parameter(tmp_path: Path) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state)
    runtime = _runtime(_FakeRunner())
    runtime = SwebenchRuntime(
        docker_executable=runtime.docker_executable,
        runner=runtime.runner,
        parameter_probe=lambda *_args: {"reasoning_effort": True},
        adapter_probe=runtime.adapter_probe,
        agent_executor=runtime.agent_executor,
        grader_executor=runtime.grader_executor,
        test_spec_factory=runtime.test_spec_factory,
        score_executor=runtime.score_executor,
        scorer_attestation=runtime.scorer_attestation,
        nonce_factory=runtime.nonce_factory,
    )
    report = inspect_swebench_readiness(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        runtime=runtime,
    )
    assert report["status"] == "blocked"
    assert report["blockers"] == ["LM Studio did not explicitly accept every requested parameter"]


def test_adapter_identity_must_match_the_frozen_protocol(tmp_path: Path) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state)
    runtime = _runtime(_FakeRunner())
    assert runtime.adapter_probe is not None
    evidence = dict(
        runtime.adapter_probe("http://127.0.0.1:1234/v1", profile["model"], profile["parameters"])
    )
    evidence["runtime_identity_sha256"] = "9" * 64
    runtime = SwebenchRuntime(
        docker_executable=runtime.docker_executable,
        runner=runtime.runner,
        parameter_probe=runtime.parameter_probe,
        adapter_probe=lambda *_args: evidence,
        agent_executor=runtime.agent_executor,
        grader_executor=runtime.grader_executor,
        test_spec_factory=runtime.test_spec_factory,
        score_executor=runtime.score_executor,
        scorer_attestation=runtime.scorer_attestation,
        nonce_factory=runtime.nonce_factory,
    )
    report = inspect_swebench_readiness(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        runtime=runtime,
    )
    assert report["status"] == "blocked"
    assert report["blockers"] == [
        "multi-turn adapter attestation does not match the frozen safety contract"
    ]


def test_qualification_caps_tasks_and_never_emits_capability_score(tmp_path: Path) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state, count=10)
    runner = _FakeRunner()
    result = execute_swebench(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        runtime=_runtime(runner),
    )
    benchmark = result["benchmark_summary"]
    assert result["status"] == "completed"
    assert benchmark["non_capability"] is True
    assert benchmark["capability_claim_allowed"] is False
    assert benchmark["resolution_rate"] is None
    assert benchmark["task_count"] == 10
    assert result["benchmark_result"]["identity"]["dataset_revision"] == "a" * 40
    assert (
        result["benchmark_result"]["provenance"]["artifacts"]["manifest"]
        == profile["manifest_sha256"]
    )
    run_dir = state / "swebench" / "runs" / result["run_id"]
    assert len(list(run_dir.glob("checkpoint-*.json"))) == 10
    assert not any("shell=True" in " ".join(call) for call in runner.calls)


def test_qualification_refuses_more_than_ten_tasks(tmp_path: Path) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state, count=11)
    report = inspect_swebench_readiness(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        runtime=_runtime(_FakeRunner()),
    )
    assert report["status"] == "blocked"
    assert "qualification is limited to at most 10 tasks" in report["blockers"]


def test_verified_full_run_requires_marker_and_matching_fingerprint(tmp_path: Path) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state, mode="verified")
    readiness = inspect_swebench_readiness(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        runtime=_runtime(_FakeRunner()),
    )
    fingerprint = readiness["metadata"]["protocol_fingerprint"]
    profile["protocol_approval"] = {
        "approved": True,
        "approved_fingerprint": fingerprint,
    }
    with pytest.raises(SwebenchError, match="explicit approval marker"):
        execute_swebench(
            profile,
            repo=repo,
            state=state,
            server_origin="http://127.0.0.1:1234/v1",
            runtime=_runtime(_FakeRunner()),
        )
    result = execute_swebench(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        approval_marker=FULL_RUN_APPROVAL_MARKER,
        runtime=_runtime(_FakeRunner()),
    )
    assert result["benchmark_summary"]["capability_claim_allowed"] is True
    assert result["benchmark_result"]["status"] == "complete"
    assert len(result["benchmark_result_fingerprint_sha256"]) == 64


def test_qualification_must_be_machine_proven_disjoint_from_verified_500(
    tmp_path: Path,
) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state, count=1)
    manifest_path = Path(profile["manifest_path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["tasks"][0]["instance_id"] = manifest["verified_500_instance_ids"][0]
    manifest["ordered_instance_ids_sha256"] = hashlib.sha256(
        json.dumps([manifest["tasks"][0]["instance_id"]], separators=(",", ":")).encode()
    ).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    profile["manifest_sha256"] = _sha256(manifest_path)
    report = inspect_swebench_readiness(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        runtime=_runtime(_FakeRunner()),
    )
    assert report["status"] == "blocked"
    assert report["blockers"] == ["qualification tasks must be disjoint from the Verified 500"]


def test_selection_policy_pins_prior_qualification_exclusion_evidence(tmp_path: Path) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state, count=1)
    manifest_path = Path(profile["manifest_path"])
    original_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    swebench_module._validate_manifest(profile, state)

    for field in (
        "prior_qualification_manifest_sha256",
        "prior_qualification_exclusions_sha256",
    ):
        manifest = json.loads(json.dumps(original_manifest))
        manifest["selection_policy"][field] = "0" * 64
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")), encoding="utf-8"
        )
        profile["manifest_sha256"] = _sha256(manifest_path)
        with pytest.raises(SwebenchError, match="manifest selection policy is unsupported"):
            swebench_module._validate_manifest(profile, state)


def test_manifest_binds_task_order_and_hashes_to_canonical_records(tmp_path: Path) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state, count=2)
    manifest_path = Path(profile["manifest_path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records_path = state / manifest["records_path"]
    lines = records_path.read_bytes().splitlines()
    records_path.write_bytes(lines[1] + b"\n" + lines[0] + b"\n")
    manifest["records_sha256"] = _sha256(records_path)
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    profile["manifest_sha256"] = _sha256(manifest_path)

    report = inspect_swebench_readiness(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        runtime=_runtime(_FakeRunner()),
    )
    assert report["status"] == "blocked"
    assert report["blockers"] == ["manifest tasks do not match canonical record order"]


def test_manifest_refuses_missing_grader_record_fields(tmp_path: Path) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state, count=1)
    manifest_path = Path(profile["manifest_path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records_path = state / manifest["records_path"]
    record = json.loads(records_path.read_text(encoding="utf-8"))
    del record["FAIL_TO_PASS"]
    raw = json.dumps(record, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    records_path.write_bytes(raw)
    manifest["records_sha256"] = hashlib.sha256(raw).hexdigest()
    manifest["tasks"][0]["record_sha256"] = hashlib.sha256(raw.rstrip(b"\n")).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    profile["manifest_sha256"] = _sha256(manifest_path)

    report = inspect_swebench_readiness(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        runtime=_runtime(_FakeRunner()),
    )
    assert report["status"] == "blocked"
    assert report["blockers"] == ["canonical record is missing required grader fields"]


def test_manifest_requires_immutable_dataset_revision(tmp_path: Path) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state, count=1)
    manifest_path = Path(profile["manifest_path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["dataset_revision"] = "main"
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    profile["manifest_sha256"] = _sha256(manifest_path)
    report = inspect_swebench_readiness(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        runtime=_runtime(_FakeRunner()),
    )
    assert report["status"] == "blocked"
    assert report["blockers"] == ["manifest dataset revision must be an immutable 40-hex commit"]


def test_verified_mode_refuses_wrong_denominator(tmp_path: Path) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state, mode="verified")
    profile["task_count"] = 499
    report = inspect_swebench_readiness(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        runtime=_runtime(_FakeRunner()),
    )
    assert report["status"] == "blocked"
    assert report["blockers"]


def test_task_and_grader_are_fresh_and_infrastructure_errors_are_counted(
    tmp_path: Path,
) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state, count=10)
    runner = _FakeRunner()
    runtime = _runtime(runner)
    runtime = SwebenchRuntime(
        docker_executable=runtime.docker_executable,
        runner=runtime.runner,
        parameter_probe=runtime.parameter_probe,
        adapter_probe=runtime.adapter_probe,
        agent_executor=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("agent failed")),
        grader_executor=runtime.grader_executor,
        test_spec_factory=runtime.test_spec_factory,
        score_executor=runtime.score_executor,
        scorer_attestation=runtime.scorer_attestation,
        nonce_factory=runtime.nonce_factory,
    )
    result = execute_swebench(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        runtime=runtime,
    )
    assert result["benchmark_summary"]["infrastructure_error_count"] == 10
    assert result["benchmark_summary"]["attempted_count"] == 0
    assert result["benchmark_summary"]["error_count"] == 10
    checkpoints = sorted((state / "swebench" / "runs").glob("*/checkpoint-*.json"))
    assert len(checkpoints) == 10
    assert all(
        json.loads(checkpoint.read_text())["attempted"] is False for checkpoint in checkpoints
    )
    rm_calls = [call for call in runner.calls if "rm" in call and "-f" in call]
    assert rm_calls
    assert any(call[1:4] == ("volume", "rm", "-f") for call in runner.calls)


def test_cleanup_failure_sticks_invalid_checkpoint_and_refuses_resume(
    tmp_path: Path,
) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state, count=1)
    runner = _FakeRunner()
    runner.fail_on = "swebench-grader-0123456789abcdef-workspace"
    score_calls: list[Any] = []
    base_runtime = _runtime(runner)

    def score_after_cleanup(trusted_run: Any) -> Any:
        score_calls.append(trusted_run)
        return _fixture_score(trusted_run)

    runtime = replace(base_runtime, score_executor=score_after_cleanup)
    with pytest.raises(SwebenchError, match="container workspace teardown failed"):
        execute_swebench(
            profile,
            repo=repo,
            state=state,
            server_origin="http://127.0.0.1:1234/v1",
            runtime=runtime,
        )
    run_dir = next((state / "swebench" / "runs").glob("*"))
    checkpoint = json.loads((run_dir / "checkpoint-0000.json").read_text(encoding="utf-8"))
    assert checkpoint["state"] == "invalid"
    assert checkpoint["status"] == "infrastructure_error"
    assert checkpoint["grade"] is None
    assert score_calls == []
    invalid = json.loads((run_dir / "invalid.json").read_text(encoding="utf-8"))
    assert invalid["reason"] == "cleanup_failed"
    workspace_removals = {call[-1] for call in runner.calls if call[1:4] == ("volume", "rm", "-f")}
    assert workspace_removals == {
        "swebench-grader-0123456789abcdef-workspace",
        "swebench-task-0123456789abcdef-workspace",
    }

    retry_calls = 0

    def retry_probe(**kwargs: object) -> Mapping[str, object]:
        nonlocal retry_calls
        retry_calls += 1
        return _fixture_agent(**kwargs)

    base = _runtime(_FakeRunner())
    retry = SwebenchRuntime(
        docker_executable=base.docker_executable,
        runner=base.runner,
        parameter_probe=base.parameter_probe,
        adapter_probe=base.adapter_probe,
        agent_executor=retry_probe,
        grader_executor=base.grader_executor,
        test_spec_factory=base.test_spec_factory,
        score_executor=base.score_executor,
        scorer_attestation=base.scorer_attestation,
        nonce_factory=base.nonce_factory,
    )
    with pytest.raises(SwebenchError, match="invalid after cleanup failure"):
        resume_swebench(
            profile,
            repo=repo,
            state=state,
            server_origin="http://127.0.0.1:1234/v1",
            run_id=run_dir.name,
            runtime=retry,
        )
    assert retry_calls == 0


def test_cleanup_requires_confirmed_container_and_workspace_absence() -> None:
    runner = _FakeRunner()
    name = "swebench-grader-0123456789abcdef"
    volume = f"{name}-workspace"
    observed = swebench_module._cleanup(
        _runtime(runner),
        name,
        {"timeout_seconds": 30, "max_output_bytes": 4096},
        workspace_volume=volume,
    )

    assert observed == (True, True)
    assert ("/usr/local/bin/docker", "container", "inspect", name) in runner.calls
    assert ("/usr/local/bin/docker", "volume", "inspect", volume) in runner.calls


def test_cleanup_accepts_exact_docker_daemon_missing_container_diagnostic() -> None:
    base_runner = _FakeRunner()
    name = "swebench-grader-0123456789abcdef"

    def daemon_runner(args: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        if tuple(args[1:3]) == ("container", "inspect"):
            diagnostic = f"Error response from daemon: No such container: {name}"
            return subprocess.CompletedProcess(tuple(args), 1, b"[]\n", diagnostic.encode())
        return base_runner(args, **kwargs)

    assert swebench_module._cleanup(
        replace(_runtime(base_runner), runner=daemon_runner),
        name,
        {"timeout_seconds": 30, "max_output_bytes": 4096},
    ) == (True, True)


def test_cleanup_accepts_exact_docker_daemon_missing_volume_diagnostic() -> None:
    base_runner = _FakeRunner()
    name = "swebench-grader-0123456789abcdef"
    volume = f"{name}-workspace"

    def daemon_runner(args: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        if tuple(args[1:3]) == ("volume", "inspect"):
            diagnostic = f"Error response from daemon: get {volume}: no such volume"
            return subprocess.CompletedProcess(tuple(args), 1, b"[]\n", diagnostic.encode())
        return base_runner(args, **kwargs)

    assert swebench_module._cleanup(
        replace(_runtime(base_runner), runner=daemon_runner),
        name,
        {"timeout_seconds": 30, "max_output_bytes": 4096},
        workspace_volume=volume,
    ) == (True, True)


@pytest.mark.parametrize(
    ("reported_name", "suffix"),
    [
        ("swebench-grader-0123456789abcde0", ""),
        ("swebench-grader-0123456789abcdef-extra", ""),
        ("swebench-grader-0123456789abcdef", " extra"),
    ],
)
def test_cleanup_rejects_spoofed_or_near_match_daemon_diagnostic(
    reported_name: str, suffix: str
) -> None:
    base_runner = _FakeRunner()
    name = "swebench-grader-0123456789abcdef"

    def daemon_runner(args: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        if tuple(args[1:3]) == ("container", "inspect"):
            diagnostic = f"Error response from daemon: No such container: {reported_name}{suffix}"
            return subprocess.CompletedProcess(tuple(args), 1, b"[]\n", diagnostic.encode())
        return base_runner(args, **kwargs)

    with pytest.raises(SwebenchError, match="container teardown could not be verified"):
        swebench_module._cleanup(
            replace(_runtime(base_runner), runner=daemon_runner),
            name,
            {"timeout_seconds": 30, "max_output_bytes": 4096},
        )


@pytest.mark.parametrize(
    ("reported_name", "suffix"),
    [
        ("swebench-grader-0123456789abcdef-workspace-extra", ""),
        ("swebench-grader-0123456789abcde0-workspace", ""),
        ("swebench-grader-0123456789abcdef-workspace", " trailing text"),
    ],
)
def test_cleanup_rejects_spoofed_or_near_match_volume_diagnostic(
    reported_name: str, suffix: str
) -> None:
    base_runner = _FakeRunner()
    name = "swebench-grader-0123456789abcdef"
    volume = f"{name}-workspace"

    def daemon_runner(args: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        if tuple(args[1:3]) == ("volume", "inspect"):
            diagnostic = f"Error response from daemon: get {reported_name}: no such volume{suffix}"
            return subprocess.CompletedProcess(tuple(args), 1, b"[]\n", diagnostic.encode())
        return base_runner(args, **kwargs)

    with pytest.raises(SwebenchError, match="container workspace teardown could not be verified"):
        swebench_module._cleanup(
            replace(_runtime(base_runner), runner=daemon_runner),
            name,
            {"timeout_seconds": 30, "max_output_bytes": 4096},
            workspace_volume=volume,
        )


@pytest.mark.parametrize(
    ("failed_probe", "message"),
    [
        (("container", "inspect"), "container teardown could not be verified"),
        (("volume", "inspect"), "workspace teardown could not be verified"),
    ],
)
def test_cleanup_rejects_daemon_error_as_absence_evidence(
    failed_probe: tuple[str, str], message: str
) -> None:
    base_runner = _FakeRunner()

    def ambiguous_runner(args: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        if tuple(args[1:3]) == failed_probe:
            return subprocess.CompletedProcess(
                tuple(args), 1, b"", b"Error: Docker daemon is unavailable"
            )
        return base_runner(args, **kwargs)

    name = "swebench-grader-0123456789abcdef"
    with pytest.raises(SwebenchError, match=message):
        swebench_module._cleanup(
            replace(_runtime(base_runner), runner=ambiguous_runner),
            name,
            {"timeout_seconds": 30, "max_output_bytes": 4096},
            workspace_volume=f"{name}-workspace",
        )


def test_control_scorer_prepare_must_match_frozen_canonical_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state, count=1)
    prepared = swebench_module._validate_profile(profile, repo, state)

    frozen = swebench_module._load_frozen_test_spec(prepared.tasks[0], state, repo)
    drifted = frozen.eval_script + "# drifted after regeneration\n"
    response = {
        "schema_version": 1,
        "action": "prepare",
        "scorer_status": "prepared",
        "error": None,
        "swebench_version": SWEBENCH_VERSION,
        "instance_id": frozen.instance_id,
        "test_spec_sha256": frozen.test_spec_sha256,
        "eval_script_b64": base64.b64encode(drifted.encode()).decode("ascii"),
        "eval_script_sha256": hashlib.sha256(drifted.encode()).hexdigest(),
        "scorer_entrypoint_sha256": frozen.scorer_entrypoint_sha256,
        "patch_sha256": None,
        "grading_log_sha256": None,
        "host_cleanup_confirmed": None,
        "resolved": None,
        "report": None,
    }
    calls: list[Mapping[str, object]] = []

    def prepare_call(request: Mapping[str, object], **_kwargs: object) -> Mapping[str, Any]:
        calls.append(request)
        return response

    monkeypatch.setattr(swebench_module, "_invoke_trusted_scorer", prepare_call)
    with pytest.raises(SwebenchError, match="differs from frozen inputs"):
        swebench_module._make_official_test_spec(
            prepared.tasks[0],
            state,
            prepared=prepared,
            runtime=_runtime(_FakeRunner()),
        )
    assert len(calls) == 1
    assert calls[0]["action"] == "prepare"


def test_official_score_rpc_is_bound_to_patch_log_and_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    prepared = swebench_module._validate_profile(_profile(repo, state, count=1), repo, state)
    task = prepared.tasks[0]
    test_spec = swebench_module._load_frozen_test_spec(task, state, repo)
    patch = "diff --git a/example.py b/example.py\n"
    patch_sha256 = hashlib.sha256(patch.encode()).hexdigest()
    grading_log = b"official grader captured test output\n"
    grading_log_sha256 = hashlib.sha256(grading_log).hexdigest()
    grading_log_path = state / f"grader-log-{grading_log_sha256}.txt"
    grading_log_path.write_bytes(grading_log)
    grading_log_path.chmod(0o600)
    trusted_run = SimpleNamespace(
        test_spec=test_spec,
        instance_id=task.instance_id,
        cleanup_ok=True,
        model_patch=patch,
        patch_sha256=patch_sha256,
        test_log_path=grading_log_path,
    )
    response = {
        "schema_version": 1,
        "action": "score",
        "scorer_status": "official",
        "error": None,
        "swebench_version": SWEBENCH_VERSION,
        "instance_id": test_spec.instance_id,
        "test_spec_sha256": test_spec.test_spec_sha256,
        "eval_script_b64": None,
        "eval_script_sha256": test_spec.eval_script_sha256,
        "scorer_entrypoint_sha256": test_spec.scorer_entrypoint_sha256,
        "patch_sha256": patch_sha256,
        "grading_log_sha256": grading_log_sha256,
        "host_cleanup_confirmed": True,
        "resolved": True,
        "report": {"resolved": True},
    }
    requests: list[Mapping[str, object]] = []

    def score_call(request: Mapping[str, object], **_kwargs: object) -> Mapping[str, Any]:
        requests.append(request)
        return response

    monkeypatch.setattr(swebench_module, "_invoke_trusted_scorer", score_call)
    result = swebench_module._default_score_executor(
        trusted_run,
        prepared=prepared,
        runtime=_runtime(_FakeRunner()),
    )
    assert result.status == "resolved"
    assert result.official is True
    assert result.resolved is True
    assert requests[0]["action"] == "score"
    assert requests[0]["host_cleanup_confirmed"] is True
    assert requests[0]["patch_sha256"] == patch_sha256
    assert requests[0]["grading_log_sha256"] == grading_log_sha256
    assert requests[0]["eval_script_sha256"] == test_spec.eval_script_sha256

    unclean_run = SimpleNamespace(**{**trusted_run.__dict__, "cleanup_ok": False})
    with pytest.raises(SwebenchError, match="cleaned-up task evidence"):
        swebench_module._default_score_executor(
            unclean_run,
            prepared=prepared,
            runtime=_runtime(_FakeRunner()),
        )
    assert len(requests) == 1


def test_grader_log_neutralizes_all_spoofed_exit_markers_and_preserves_raw_bytes(
    tmp_path: Path,
) -> None:
    repo = Path.cwd()
    state = tmp_path / "private-state"
    state.mkdir(mode=0o700)
    run_dir = state / "run"
    run_dir.mkdir(mode=0o700)
    prepared = swebench_module._validate_profile(_profile(repo, state), repo, state)
    task = prepared.tasks[0]
    raw_output = (
        b"+ echo '>>>>> Test Exit Code: 0'\n>>>>> Test Exit Code: 42\nPASS all synthetic checks\n"
    )
    patch_sha256 = "a" * 64
    eval_sha256 = "b" * 64
    capture = {
        "schema_version": 1,
        "phase": "patch_ready",
        "launcher_exit_code": 0,
        "process_exit_code": 0,
        "eval_launcher_exit_code": 7,
        "eval_exit_code": 7,
        "timed_out": False,
        "patch_sha256": patch_sha256,
        "eval_script_sha256": eval_sha256,
        "stdout": raw_output,
        "stderr": b"",
        "error": None,
    }

    persisted = swebench_module._persist_grader_capture(
        capture,
        task=task,
        prediction_sha256=patch_sha256,
        eval_script_sha256=eval_sha256,
        attempt_nonce="synthetic-attempt",
        run_dir=run_dir,
        limits=cast(Mapping[str, Any], prepared.profile["resource_limits"]),
    )
    log = (run_dir / cast(str, persisted["test_log_path"])).read_bytes()
    assert swebench_module._TEST_EXIT_CODE_RE.findall(log) == [b"7"]
    assert b"Test Exit Code [host-neutralized]" in log
    assert log.count(b">>>>> Test Exit Code:") == 1
    # The official parser slices from the eval script's own start marker.
    assert log.startswith(b"+ echo '>>>>> Test Exit Code [host-neutralized]'\n")
    assert b">>>>> Start Test Output" not in log

    nonce_sha = hashlib.sha256(task.instance_id.encode() + b"\0synthetic-attempt").hexdigest()
    raw_evidence = (run_dir / f"grader-capture-{nonce_sha}.bin").read_bytes()
    assert b"\nstdout\0" + raw_output + b"\nstderr\0" in raw_evidence


def test_invalid_host_eval_capture_never_invokes_official_scorer(tmp_path: Path) -> None:
    repo = Path.cwd()
    state = tmp_path / "private-state"
    state.mkdir(mode=0o700)
    run_dir = state / "run"
    evidence_dir = run_dir / "evidence"
    evidence_dir.mkdir(mode=0o700, parents=True)
    prepared = swebench_module._validate_profile(_profile(repo, state), repo, state)
    task = prepared.tasks[0]
    patch = b"diff --git a/example.py b/example.py\n"
    patch_sha256 = hashlib.sha256(patch).hexdigest()
    prediction_path = evidence_dir / f"prediction-{patch_sha256}.patch"
    prediction_path.write_bytes(patch)
    prediction_path.chmod(0o600)
    log = b">>>>> Start Test Output\npartial\n>>>>> End Test Output\n"
    log_sha256 = hashlib.sha256(log).hexdigest()
    log_path = run_dir / f"grader-log-{log_sha256}.txt"
    log_path.write_bytes(log)
    log_path.chmod(0o600)
    calls: list[object] = []

    status, grade = swebench_module._score_grader_capture(
        capture={
            "phase": "patch_ready",
            "test_log_path": log_path.name,
            "eval_exit_code": None,
            "timed_out": True,
            "capture_error": "eval_timeout",
        },
        task=task,
        agent={
            "prediction_handle": f"private://{prediction_path.name}",
            "model_patch_sha256": patch_sha256,
        },
        test_spec=object(),
        run_dir=run_dir,
        evidence_dir=evidence_dir,
        score_executor=lambda trusted_run: calls.append(trusted_run),
        cleanup_evidence=((True, True), (True, True)),
    )

    assert (status, grade) == ("infrastructure_error", None)
    assert calls == []


def test_official_score_executor_runs_only_after_cleanup_succeeds(tmp_path: Path) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state, count=1)
    runner = _FakeRunner()
    score_observations: list[tuple[bool, bool]] = []

    def score_after_cleanup(trusted_run: Any) -> Any:
        cleanup_seen = any(
            call[1:4] == ("volume", "rm", "-f") and call[-1].endswith("-workspace")
            for call in runner.calls
        )
        score_observations.append((trusted_run.cleanup_ok, cleanup_seen))
        return _fixture_score(trusted_run)

    runtime = replace(_runtime(runner), score_executor=score_after_cleanup)
    result = execute_swebench(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        runtime=runtime,
    )

    assert score_observations == [(True, True)]
    assert result["benchmark_summary"]["resolved_count"] == 1


def test_cleanup_pending_checkpoint_refuses_resume_after_cleanup_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state, count=1)

    def crash_cleanup(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt("synthetic crash during cleanup")

    monkeypatch.setattr(swebench_module, "_cleanup", crash_cleanup)
    with pytest.raises(KeyboardInterrupt, match="synthetic crash"):
        execute_swebench(
            profile,
            repo=repo,
            state=state,
            server_origin="http://127.0.0.1:1234/v1",
            runtime=_runtime(_FakeRunner()),
        )
    run_dir = next((state / "swebench" / "runs").glob("*"))
    checkpoint = json.loads((run_dir / "checkpoint-0000.json").read_text(encoding="utf-8"))
    assert checkpoint["state"] == "cleanup_pending"
    assert checkpoint["status"] == "infrastructure_error"

    retry_calls = 0

    def retry_probe(**kwargs: object) -> Mapping[str, object]:
        nonlocal retry_calls
        retry_calls += 1
        return _fixture_agent(**kwargs)

    base = _runtime(_FakeRunner())
    retry = SwebenchRuntime(
        docker_executable=base.docker_executable,
        runner=base.runner,
        parameter_probe=base.parameter_probe,
        adapter_probe=base.adapter_probe,
        agent_executor=retry_probe,
        grader_executor=base.grader_executor,
        test_spec_factory=base.test_spec_factory,
        score_executor=base.score_executor,
        scorer_attestation=base.scorer_attestation,
        nonce_factory=base.nonce_factory,
    )
    with pytest.raises(SwebenchError, match="reconciliation"):
        resume_swebench(
            profile,
            repo=repo,
            state=state,
            server_origin="http://127.0.0.1:1234/v1",
            run_id=run_dir.name,
            runtime=retry,
        )
    assert retry_calls == 0


def test_post_response_infrastructure_failure_is_attempted_and_terminal(tmp_path: Path) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state, count=1)
    runtime = _runtime(_FakeRunner())
    runtime = SwebenchRuntime(
        docker_executable=runtime.docker_executable,
        runner=runtime.runner,
        parameter_probe=runtime.parameter_probe,
        adapter_probe=runtime.adapter_probe,
        agent_executor=lambda **kwargs: _fixture_agent(status="infrastructure_error", **kwargs),
        grader_executor=runtime.grader_executor,
        test_spec_factory=runtime.test_spec_factory,
        score_executor=runtime.score_executor,
        scorer_attestation=runtime.scorer_attestation,
        nonce_factory=runtime.nonce_factory,
    )
    result = execute_swebench(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        runtime=runtime,
    )
    checkpoint = next((state / "swebench" / "runs").glob("*/checkpoint-0000.json"))
    assert json.loads(checkpoint.read_text())["attempted"] is True
    assert result["benchmark_summary"]["infrastructure_error_count"] == 1
    assert result["benchmark_summary"]["attempted_count"] == 1


def test_invalid_agent_result_after_verified_response_is_terminal_before_next_task(
    tmp_path: Path,
) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state, count=2)
    base_runtime = _runtime(_FakeRunner())
    agent_calls = 0
    test_spec_calls = 0

    def invalid_first_agent_result(**kwargs: object) -> Mapping[str, object]:
        nonlocal agent_calls
        agent_calls += 1
        if agent_calls != 1:
            return _fixture_agent(status="model_failure", **kwargs)
        result = dict(_fixture_agent(status="infrastructure_error", **kwargs))
        result["trajectory_sha256"] = "invalid"
        return result

    def check_checkpoint_before_next_task(task: Any, task_state: Path) -> Any:
        nonlocal test_spec_calls
        test_spec_calls += 1
        if test_spec_calls == 2:
            checkpoints = list((state / "swebench" / "runs").glob("*/checkpoint-0000.json"))
            assert len(checkpoints) == 1
            saved = json.loads(checkpoints[0].read_text())
            assert saved["state"] == "terminal"
            assert saved["status"] == "infrastructure_error"
            assert saved["attempted"] is True
            assert isinstance(saved["host_response_receipt"], dict)
            assert saved["trajectory_sha256"] is None
            assert saved["model_patch_sha256"] is None
            assert saved["grader_evidence_sha256"] is None
            assert saved["grade"] is None
        return _fixture_test_spec(task, task_state)

    runtime = SwebenchRuntime(
        docker_executable=base_runtime.docker_executable,
        runner=base_runtime.runner,
        parameter_probe=base_runtime.parameter_probe,
        adapter_probe=base_runtime.adapter_probe,
        agent_executor=invalid_first_agent_result,
        grader_executor=base_runtime.grader_executor,
        test_spec_factory=check_checkpoint_before_next_task,
        score_executor=base_runtime.score_executor,
        scorer_attestation=base_runtime.scorer_attestation,
        nonce_factory=base_runtime.nonce_factory,
    )

    result = execute_swebench(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        runtime=runtime,
    )

    assert result["benchmark_summary"]["infrastructure_error_count"] == 1
    assert result["benchmark_summary"]["model_failure_count"] == 1
    assert result["benchmark_summary"]["attempted_count"] == 2


def test_mixed_outcomes_count_only_verified_responses_as_attempted(tmp_path: Path) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state, count=4)
    runtime = _runtime(_FakeRunner())
    calls = 0

    def mixed_agent(**kwargs: object) -> Mapping[str, object]:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("failure before a verified response")
        status = {
            1: "completed",
            2: "model_failure",
            4: "infrastructure_error",
        }[calls]
        return _fixture_agent(status=status, **kwargs)

    runtime = SwebenchRuntime(
        docker_executable=runtime.docker_executable,
        runner=runtime.runner,
        parameter_probe=runtime.parameter_probe,
        adapter_probe=runtime.adapter_probe,
        agent_executor=mixed_agent,
        grader_executor=runtime.grader_executor,
        test_spec_factory=runtime.test_spec_factory,
        score_executor=runtime.score_executor,
        scorer_attestation=runtime.scorer_attestation,
        nonce_factory=lambda: f"{calls:016x}",
    )

    result = execute_swebench(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        runtime=runtime,
    )

    summary = result["benchmark_summary"]
    assert summary["completed_count"] == 1
    assert summary["model_failure_count"] == 1
    assert summary["infrastructure_error_count"] == 2
    assert summary["attempted_count"] == 3


def test_model_failure_counts_once_without_invoking_the_grader(tmp_path: Path) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state, count=1)
    runtime = _runtime(_FakeRunner())
    grader_calls = 0

    def grader(**_kwargs: object) -> Mapping[str, object]:
        nonlocal grader_calls
        grader_calls += 1
        return _fixture_grader_capture(**_kwargs)

    runtime = SwebenchRuntime(
        docker_executable=runtime.docker_executable,
        runner=runtime.runner,
        parameter_probe=runtime.parameter_probe,
        adapter_probe=runtime.adapter_probe,
        agent_executor=lambda **kwargs: _fixture_agent(status="model_failure", **kwargs),
        grader_executor=grader,
        test_spec_factory=runtime.test_spec_factory,
        score_executor=runtime.score_executor,
        scorer_attestation=runtime.scorer_attestation,
        nonce_factory=runtime.nonce_factory,
    )
    result = execute_swebench(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        runtime=runtime,
    )
    assert result["benchmark_summary"]["model_failure_count"] == 1
    assert result["benchmark_summary"]["completed_count"] == 0
    assert grader_calls == 0


def test_dispatch_crash_leaves_in_flight_checkpoint_and_resume_refuses_retry(
    tmp_path: Path,
) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state, count=1)
    dispatch_calls = 0

    def crash_after_dispatch(**_kwargs: object) -> Mapping[str, object]:
        nonlocal dispatch_calls
        dispatch_calls += 1
        raise KeyboardInterrupt("synthetic dispatch crash")

    initial = _runtime(_FakeRunner())
    initial = SwebenchRuntime(
        docker_executable=initial.docker_executable,
        runner=initial.runner,
        parameter_probe=initial.parameter_probe,
        adapter_probe=initial.adapter_probe,
        agent_executor=crash_after_dispatch,
        grader_executor=initial.grader_executor,
        test_spec_factory=initial.test_spec_factory,
        score_executor=initial.score_executor,
        scorer_attestation=initial.scorer_attestation,
        nonce_factory=initial.nonce_factory,
    )
    with pytest.raises(KeyboardInterrupt, match="synthetic dispatch crash"):
        execute_swebench(
            profile,
            repo=repo,
            state=state,
            server_origin="http://127.0.0.1:1234/v1",
            runtime=initial,
        )
    checkpoint = state / "swebench" / "runs" / "swebench-0123456789abcdef" / "checkpoint-0000.json"
    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert saved["state"] == "in_flight"
    assert saved["attempted"] is False
    assert dispatch_calls == 1

    retry_calls = 0

    def retry_probe(**kwargs: object) -> Mapping[str, object]:
        nonlocal retry_calls
        retry_calls += 1
        return _agent_result(**kwargs)

    retry = _runtime(_FakeRunner())
    retry = SwebenchRuntime(
        docker_executable=retry.docker_executable,
        runner=retry.runner,
        parameter_probe=retry.parameter_probe,
        adapter_probe=retry.adapter_probe,
        agent_executor=retry_probe,
        grader_executor=retry.grader_executor,
        test_spec_factory=retry.test_spec_factory,
        score_executor=retry.score_executor,
        scorer_attestation=retry.scorer_attestation,
        nonce_factory=retry.nonce_factory,
    )
    with pytest.raises(SwebenchError, match="reconciliation"):
        resume_swebench(
            profile,
            repo=repo,
            state=state,
            server_origin="http://127.0.0.1:1234/v1",
            run_id="swebench-0123456789abcdef",
            runtime=retry,
        )
    assert retry_calls == 0
    assert checkpoint.exists()


def test_default_rpc_crash_after_verified_response_terminalizes_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state, count=1)
    model_record = {"key": profile["model"], "format": "gguf"}
    model_instance = {"id": "runtime-instance", "status": "loaded"}
    served = hashlib.sha256(
        swebench_module._canonical({"model": model_record, "instance": model_instance})
    ).hexdigest()
    profile["model_runtime"]["served_model_fingerprint"] = served
    base = _runtime(_FakeRunner())
    assert base.adapter_probe is not None
    adapter = dict(base.adapter_probe("unused", profile["model"], profile["parameters"]))
    adapter["served_model_fingerprint"] = served
    lm_chat_line = swebench_module._canonical(
        {
            "op": "lm_chat",
            "body": {
                "model": profile["model"],
                "messages": [],
                "stream": False,
            },
        }
    )
    monkeypatch.setenv(
        "LOCAL_EVALS_SWEBENCH_CONTROL_IMAGE_REFERENCE", profile["control_image_reference"]
    )
    monkeypatch.setenv("LOCAL_EVALS_SWEBENCH_CONTROL_IMAGE_DIGEST", profile["control_image_digest"])
    monkeypatch.setattr(
        swebench_module,
        "_control_plane_args",
        lambda _kwargs: _rpc_fixture_command("lines", lm_chat_line),
    )
    monkeypatch.setattr(
        swebench_module,
        "_loaded_model",
        lambda *_args, **_kwargs: (model_record, model_instance),
    )
    monkeypatch.setattr(
        swebench_module,
        "sdk_chat_response",
        lambda *_args, **_kwargs: {
            "status": "completed",
            "model": profile["model"],
            "choices": [{"message": {"role": "assistant", "content": "OK"}}],
            "usage": {"completion_tokens": 1},
            "charged_output_tokens": 1,
        },
    )
    monkeypatch.setattr(swebench_module, "_cleanup_control_container", lambda _kwargs: None)
    initial = SwebenchRuntime(
        docker_executable=base.docker_executable,
        runner=base.runner,
        parameter_probe=base.parameter_probe,
        adapter_probe=lambda *_args: adapter,
        agent_executor=swebench_module._default_agent_executor,
        grader_executor=base.grader_executor,
        test_spec_factory=base.test_spec_factory,
        score_executor=base.score_executor,
        scorer_attestation=base.scorer_attestation,
        nonce_factory=base.nonce_factory,
    )
    result = execute_swebench(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        runtime=initial,
    )
    assert result["status"] == "partial"
    assert result["benchmark_summary"]["infrastructure_error_count"] == 1
    assert result["benchmark_summary"]["attempted_count"] == 1
    assert result["benchmark_summary"]["error_count"] == 1
    checkpoint = state / "swebench" / "runs" / "swebench-0123456789abcdef" / "checkpoint-0000.json"
    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert saved["state"] == "terminal"
    assert saved["status"] == "infrastructure_error"
    assert saved["attempted"] is True
    assert saved["trajectory_sha256"] is None
    assert saved["model_patch_sha256"] is None
    assert saved["grader_evidence_sha256"] is None
    assert saved["grade"] is None
    assert (
        saved["host_response_receipt"]["task_instance_id_sha256"]
        == hashlib.sha256(b"qualification-0").hexdigest()
    )
    assert (
        saved["host_response_receipt"]["model_instance_id_sha256"]
        == hashlib.sha256(b"runtime-instance").hexdigest()
    )

    retry_calls = 0

    def retry_probe(**kwargs: object) -> Mapping[str, object]:
        nonlocal retry_calls
        retry_calls += 1
        return _fixture_agent(**kwargs)

    retry = SwebenchRuntime(
        docker_executable=base.docker_executable,
        runner=base.runner,
        parameter_probe=base.parameter_probe,
        adapter_probe=lambda *_args: adapter,
        agent_executor=retry_probe,
        grader_executor=base.grader_executor,
        test_spec_factory=base.test_spec_factory,
        score_executor=base.score_executor,
        scorer_attestation=base.scorer_attestation,
        nonce_factory=base.nonce_factory,
    )
    resumed = resume_swebench(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        run_id="swebench-0123456789abcdef",
        runtime=retry,
    )
    assert resumed["benchmark_summary"]["infrastructure_error_count"] == 1
    assert resumed["benchmark_summary"]["attempted_count"] == 1
    assert resumed["benchmark_summary"]["error_count"] == 1
    assert retry_calls == 0


def test_untrusted_attempt_flags_without_host_receipt_are_not_attempted(
    tmp_path: Path,
) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state, count=1)
    grader_calls = 0

    def grader(**_kwargs: object) -> Mapping[str, object]:
        nonlocal grader_calls
        grader_calls += 1
        return _fixture_grader_capture(**_kwargs)

    def untrusted_flags(**kwargs: object) -> Mapping[str, object]:
        result = _agent_result(**kwargs)
        result["attempted"] = True
        result["first_verified_response"] = True
        result["host_response_receipt"] = None
        return result

    base = _runtime(_FakeRunner())
    runtime = SwebenchRuntime(
        docker_executable=base.docker_executable,
        runner=base.runner,
        parameter_probe=base.parameter_probe,
        adapter_probe=base.adapter_probe,
        agent_executor=untrusted_flags,
        grader_executor=grader,
        test_spec_factory=base.test_spec_factory,
        score_executor=base.score_executor,
        scorer_attestation=base.scorer_attestation,
        nonce_factory=base.nonce_factory,
    )
    result = execute_swebench(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        runtime=runtime,
    )
    checkpoint = next((state / "swebench" / "runs").glob("*/checkpoint-0000.json"))
    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert saved["state"] == "ambiguous"
    assert saved["attempted"] is False
    assert result["benchmark_summary"]["infrastructure_error_count"] == 1
    assert grader_calls == 0


@pytest.mark.parametrize(("reported_tokens", "actual_tokens"), [(99, 23), (0, None)])
def test_verified_receipt_and_telemetry_are_durable_before_grader_starts(
    tmp_path: Path, reported_tokens: int, actual_tokens: int | None
) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state, count=1)
    observed: list[dict[str, object]] = []
    telemetry = {
        "prompt_tokens": None,
        "finish_reason": "length",
        "context_length": 32_768,
        "context_truncation_status": None,
        "truncation_status": "generation_limit_reached",
        "trajectory_elapsed_seconds": 12.375,
    }

    def agent_with_telemetry(**kwargs: object) -> Mapping[str, object]:
        checkpoint_path = Path(str(kwargs["sdk_checkpoint"]))
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        checkpoint["sdk_budget"] = {
            "requests": 1,
            "charged_output_tokens": 65_536,
            "last_actual_output_tokens": actual_tokens,
            "actual_output_tokens": actual_tokens,
            "last_request_sha256": "9" * 64,
            "state": "completed",
        }
        checkpoint_path.write_text(json.dumps(checkpoint, sort_keys=True), encoding="utf-8")
        result = dict(_fixture_agent(**kwargs))
        result.update(telemetry)
        result["output_tokens"] = reported_tokens
        return result

    def grader(**kwargs: object) -> Mapping[str, object]:
        checkpoint = Path(str(kwargs["evidence_dir"])) / "checkpoint-0000.json"
        saved = json.loads(checkpoint.read_text(encoding="utf-8"))
        observed.append(saved)
        return _fixture_grader_capture(**kwargs)

    base = _runtime(_FakeRunner())
    runtime = SwebenchRuntime(
        docker_executable=base.docker_executable,
        runner=base.runner,
        parameter_probe=base.parameter_probe,
        adapter_probe=base.adapter_probe,
        agent_executor=agent_with_telemetry,
        grader_executor=grader,
        test_spec_factory=base.test_spec_factory,
        score_executor=base.score_executor,
        scorer_attestation=base.scorer_attestation,
        nonce_factory=base.nonce_factory,
    )
    result = execute_swebench(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        runtime=runtime,
    )
    assert result["status"] == "completed"
    assert len(observed) == 1
    assert observed[0]["state"] == "response_verified"
    assert observed[0]["attempted"] is True
    assert isinstance(observed[0]["host_response_receipt"], dict)
    assert observed[0]["output_tokens"] == actual_tokens
    assert observed[0]["sdk_budget"]["charged_output_tokens"] == 65_536
    assert observed[0]["prompt_tokens"] is None
    assert observed[0]["finish_reason"] == "length"
    assert observed[0]["context_length"] == 32_768
    assert observed[0]["context_truncation_status"] is None
    assert observed[0]["truncation_status"] == "generation_limit_reached"
    assert observed[0]["trajectory_elapsed_seconds"] == 12.375
    checkpoint = next((state / "swebench" / "runs").glob("*/checkpoint-0000.json"))
    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert saved["output_tokens"] == actual_tokens
    assert saved["sdk_budget"]["charged_output_tokens"] == 65_536


def test_checkpoint_task_fingerprint_drift_is_rejected_without_deletion(
    tmp_path: Path,
) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state, count=1)
    base = _runtime(_FakeRunner())
    first = execute_swebench(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        runtime=base,
    )
    checkpoint = state / "swebench" / "runs" / first["run_id"] / "checkpoint-0000.json"
    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    saved["task_fingerprint_sha256"] = "f" * 64
    checkpoint.write_text(json.dumps(saved, sort_keys=True), encoding="utf-8")
    retry_calls = 0

    def retry_probe(**kwargs: object) -> Mapping[str, object]:
        nonlocal retry_calls
        retry_calls += 1
        return _agent_result(**kwargs)

    retry = SwebenchRuntime(
        docker_executable=base.docker_executable,
        runner=base.runner,
        parameter_probe=base.parameter_probe,
        adapter_probe=base.adapter_probe,
        agent_executor=retry_probe,
        grader_executor=base.grader_executor,
        test_spec_factory=base.test_spec_factory,
        score_executor=base.score_executor,
        scorer_attestation=base.scorer_attestation,
        nonce_factory=base.nonce_factory,
    )
    with pytest.raises(SwebenchError, match="task fingerprint"):
        resume_swebench(
            profile,
            repo=repo,
            state=state,
            server_origin="http://127.0.0.1:1234/v1",
            run_id=first["run_id"],
            runtime=retry,
        )
    assert retry_calls == 0
    assert checkpoint.exists()
    assert json.loads(checkpoint.read_text(encoding="utf-8"))["task_fingerprint_sha256"] == "f" * 64


def test_resume_and_rescore_require_private_canonical_evidence(tmp_path: Path) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state)
    runtime = _runtime(_FakeRunner())
    for operation in (resume_swebench, rescore_swebench):
        with pytest.raises(SwebenchError, match="private run evidence is unavailable"):
            operation(
                profile,
                repo=repo,
                state=state,
                server_origin="http://127.0.0.1:1234/v1",
                run_id="swebench-aaaaaaaaaaaaaaaa",
                runtime=runtime,
            )


def test_rescore_replays_saved_evidence_without_execution_and_is_repeatable(
    tmp_path: Path,
) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state, count=1)
    start_runtime = replace(
        _runtime(_FakeRunner()), test_spec_factory=_frozen_test_spec_factory(repo)
    )
    first = execute_swebench(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        runtime=start_runtime,
    )
    run_dir = state / "swebench" / "runs" / first["run_id"]
    checkpoint_path = run_dir / "checkpoint-0000.json"
    capture_path = next(run_dir.glob("grader-capture-*.bin"))
    protected_paths = [
        run_dir / "canonical.json",
        run_dir / "result.json",
        checkpoint_path,
        capture_path,
        *run_dir.glob("prediction-*.patch"),
        *run_dir.glob("grader-log-*.txt"),
    ]
    protected_before = {path.name: path.read_bytes() for path in protected_paths}

    rescore_runner = _FakeRunner()
    score_calls: list[object] = []

    def rescore_only(trusted_run: object) -> object:
        score_calls.append(trusted_run)
        return SimpleNamespace(status="unresolved", official=True, resolved=False)

    def forbidden(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("rescore must not call inference or a grader executor")

    base = _runtime(rescore_runner)
    replay_runtime = replace(
        base,
        parameter_probe=forbidden,
        adapter_probe=forbidden,
        agent_executor=forbidden,
        grader_executor=forbidden,
        test_spec_factory=start_runtime.test_spec_factory,
        score_executor=rescore_only,
    )
    first_report = rescore_swebench(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        run_id=first["run_id"],
        runtime=replay_runtime,
    )
    second_report = rescore_swebench(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        run_id=first["run_id"],
        runtime=replay_runtime,
    )

    assert first_report == second_report
    assert first_report["rescored_count"] == 1
    assert first_report["tasks"][0]["original_grade"] == "resolved"
    assert first_report["tasks"][0]["grade"] == "unresolved"
    assert len(score_calls) == 2
    assert rescore_runner.calls == []
    assert {path.name: path.read_bytes() for path in protected_paths} == protected_before


@pytest.mark.parametrize("tamper", ["capture", "log", "missing_capture", "nonterminal"])
def test_rescore_rejects_tampered_or_nonterminal_evidence(tmp_path: Path, tamper: str) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state, count=1)
    start_runtime = replace(
        _runtime(_FakeRunner()), test_spec_factory=_frozen_test_spec_factory(repo)
    )
    first = execute_swebench(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        runtime=start_runtime,
    )
    run_dir = state / "swebench" / "runs" / first["run_id"]
    checkpoint_path = run_dir / "checkpoint-0000.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    capture_path = next(run_dir.glob("grader-capture-*.bin"))
    score_calls: list[object] = []
    base = _runtime(_FakeRunner())
    replay_runtime = replace(
        base,
        test_spec_factory=start_runtime.test_spec_factory,
        score_executor=lambda trusted_run: (
            score_calls.append(trusted_run)
            or SimpleNamespace(status="resolved", official=True, resolved=True)
        ),
    )
    if tamper == "capture":
        capture_path.write_bytes(capture_path.read_bytes() + b"tampered")
    elif tamper == "log":
        log_path = next(run_dir.glob("grader-log-*.txt"))
        log_path.write_bytes(log_path.read_bytes() + b"tampered")
    elif tamper == "missing_capture":
        capture_path.unlink()
    else:
        checkpoint["state"] = "response_verified"
        checkpoint["status"] = "response_verified"
        checkpoint["grader_evidence_sha256"] = None
        checkpoint["grade"] = None
        checkpoint_path.write_text(json.dumps(checkpoint, sort_keys=True), encoding="utf-8")

    with pytest.raises(SwebenchError):
        rescore_swebench(
            profile,
            repo=repo,
            state=state,
            server_origin="http://127.0.0.1:1234/v1",
            run_id=first["run_id"],
            runtime=replay_runtime,
        )
    assert score_calls == []
    assert replay_runtime.runner is not None
    assert replay_runtime.runner.calls == []


def test_assets_pin_official_packages_and_bash_only_tools() -> None:
    root = Path("sandbox/swebench")
    config = (root / "mini-swe-agent.yaml").read_text(encoding="utf-8")
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (root / ".dockerignore").read_text(encoding="utf-8")
    lock = (root / "control-plane-requirements.lock").read_text(encoding="utf-8")
    assert f'mini_swe_agent_version: "{MINI_SWE_AGENT_VERSION}"' in config
    assert "tools:" in config and "- bash" in config
    assert "python" not in config.split("tools:", 1)[1]
    assert "mini-swe-agent==2.4.6" in lock
    assert "swebench==5.0.2" in lock
    assert "a35463c553ac825c7773b03cfa69cd44958e3af20155dcc5711fdf9e4c67cd54" in lock
    assert "b7f0416a1e686eca22c2f749b5f816685a202835032f6683080e2b53545bbb62" in lock
    requirements = re.findall(r"(?m)^([A-Za-z0-9_.-]+)==\S+ \\$", lock)
    requirement_blocks = re.split(r"(?m)(?=^[A-Za-z0-9_.-]+==)", lock)[1:]
    assert len(requirements) == 107
    assert len(requirements) == len(set(requirements))
    assert len(requirement_blocks) == len(requirements)
    assert all("--hash=sha256:" in block for block in requirement_blocks)
    assert "--no-index" in dockerfile
    assert "--find-links=/wheels" in dockerfile
    assert "--only-binary=:all:" in dockerfile
    assert "--require-hashes" in dockerfile
    assert "python -m pip check" in dockerfile
    assert "--no-deps" not in dockerfile
    assert "COPY --chown=65532:65532 runner.py /opt/racecraft/runner.py" in dockerfile
    assert "MSWEA_GLOBAL_CONFIG_DIR=/tmp/mini-swe-agent" in dockerfile
    assert "MSWEA_SILENT_STARTUP=1" in dockerfile
    assert "python -I /opt/racecraft/runner.py --smoke-check" in dockerfile
    assert {
        "!Dockerfile",
        "!control-plane-requirements.lock",
        "!mini-swe-agent.yaml",
        "!runner.py",
        "!wheels/",
        "!wheels/*.whl",
    } <= set(dockerignore.splitlines())


def test_runner_smoke_check_is_offline_and_fail_closed() -> None:
    source = Path("sandbox/swebench/runner.py").read_text(encoding="utf-8")
    smoke_body = source.split("def _smoke_check()", 1)[1].split("def main()", 1)[0]
    assert "importlib.metadata.version(distribution) != expected" in smoke_body
    assert 'importlib.import_module("swebench")' in smoke_body
    assert 'agent.get("tools") != ["bash"]' in smoke_body
    assert "_rpc(" not in smoke_body
    assert "urllib" not in smoke_body


def _patch_failure_response(**overrides: object) -> bytes:
    response = {
        "schema_version": 1,
        "phase": "apply_failed",
        "process_exit_code": 7,
        "eval_exit_code": None,
        "timed_out": False,
        "patch_sha256": "a" * 64,
        "eval_script_sha256": "b" * 64,
        "stdout_b64": "",
        "stderr_b64": "",
        "error": "patch_failed",
        **overrides,
    }
    return json.dumps(response).encode()


@pytest.mark.parametrize(
    ("overrides", "accepted"),
    [
        ({}, True),
        ({"error": "reset_failed"}, False),
        ({"process_exit_code": 11}, False),
        ({"timed_out": True}, False),
        ({"patch_sha256": "c" * 64}, False),
    ],
)
def test_only_attested_patch_failure_is_a_model_outcome(
    overrides: dict[str, object], accepted: bool
) -> None:
    raw = _patch_failure_response(**overrides)
    assert (
        swebench_module._is_patch_failure_response(
            raw, patch_sha256="a" * 64, eval_script_sha256="b" * 64
        )
        is accepted
    )
    assert not swebench_module._is_patch_failure_response(
        b"not json", patch_sha256="a" * 64, eval_script_sha256="b" * 64
    )


def _host_control_bound(kwargs: Mapping[str, object]) -> float:
    wall_limit = float(cast(float, kwargs["timeout_seconds"]))
    return wall_limit + min(swebench_module._CONTROL_DEADLINE_MARGIN_SECONDS, wall_limit / 2)


def test_run_stops_before_a_task_when_the_frozen_model_is_not_served(tmp_path: Path) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state, count=2)
    base = _runtime(_FakeRunner())
    assert base.adapter_probe is not None
    base_probe = base.adapter_probe
    served = [True]

    def probe(origin: str, model: str, parameters: Mapping[str, object]) -> Mapping[str, object]:
        evidence = dict(base_probe(origin, model, parameters))
        if not served[0]:
            evidence["served_model_fingerprint"] = "0" * 64
        return evidence

    def agent_then_unload(**kwargs: object) -> Mapping[str, object]:
        result = _fixture_agent(**kwargs)
        served[0] = False
        return result

    runtime = replace(base, adapter_probe=probe, agent_executor=agent_then_unload)
    with pytest.raises(swebench_module._SdkTransportStopped, match="not being served"):
        execute_swebench(
            profile,
            repo=repo,
            state=state,
            server_origin="http://127.0.0.1:1234/v1",
            runtime=runtime,
        )
    run_dir = state / "swebench" / "runs" / "swebench-0123456789abcdef"
    assert json.loads((run_dir / "checkpoint-0000.json").read_text())["state"] == "terminal"
    assert not (run_dir / "checkpoint-0001.json").exists()


def test_resume_runs_tasks_that_never_reached_the_model(tmp_path: Path) -> None:
    repo = Path.cwd()
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    profile = _profile(repo, state, count=2)
    base = _runtime(_FakeRunner())

    def no_model_response(**kwargs: object) -> Mapping[str, object]:
        result = dict(_agent_result(**kwargs))
        result["host_response_receipt"] = None
        return result

    initial = execute_swebench(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        runtime=replace(base, agent_executor=no_model_response),
    )
    run_dir = state / "swebench" / "runs" / initial["run_id"]
    first = json.loads((run_dir / "checkpoint-0000.json").read_text())
    assert (first["state"], first["attempted"], first["sdk_budget"]) == ("ambiguous", False, None)

    resume_swebench(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        run_id=initial["run_id"],
        runtime=base,
    )

    for index in (0, 1):
        rerun = json.loads((run_dir / f"checkpoint-{index:04d}.json").read_text())
        assert (rerun["state"], rerun["status"]) == ("terminal", "completed")
        archived = list(run_dir.glob(f"never-attempted-checkpoint-{index:04d}-*.json"))
        assert len(archived) == 1
        assert json.loads(archived[0].read_text())["state"] == "ambiguous"
