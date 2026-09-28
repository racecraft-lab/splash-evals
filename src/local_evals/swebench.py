"""Fail-closed local SWE-bench qualification and execution contracts.

This module deliberately keeps generated shell actions behind an injected,
attested adapter.  The project never passes a model-generated command to a
host shell.  Docker and adapter implementations must be qualified separately
before a real task is allowed to start.
"""

from __future__ import annotations

import base64
import errno
import hashlib
import json
import math
import os
import re
import secrets
import selectors
import shutil
import socket
import stat
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import urlparse

import yaml

from .benchmark_results import (
    BenchmarkCounts,
    BenchmarkEvaluator,
    BenchmarkIdentity,
    BenchmarkProvenance,
    BenchmarkResult,
    BenchmarkStatus,
    EvaluatorClass,
    MetricUnit,
    TaskOutcomes,
    TestedConfiguration,
)
from .config import ConfigurationError, ExternalStatePaths, secure_resolve
from .swebench_sdk import MAX_OUTPUT, OutputBudget, sdk_chat_response
from .swebench_sdk import _reap as _reap_child_process

__all__ = [
    "FULL_RUN_APPROVAL_MARKER",
    "MINI_SWE_AGENT_VERSION",
    "SWEBENCH_VERSION",
    "SwebenchError",
    "SwebenchRuntime",
    "build_grader_container_args",
    "build_task_container_args",
    "execute_swebench",
    "inspect_swebench_readiness",
    "rescore_swebench",
    "resume_swebench",
]

MINI_SWE_AGENT_VERSION = "2.4.6"
MINI_SWE_AGENT_WHEEL_SHA256 = "a35463c553ac825c7773b03cfa69cd44958e3af20155dcc5711fdf9e4c67cd54"
SWEBENCH_VERSION = "5.0.2"
SWEBENCH_WHEEL_SHA256 = "b7f0416a1e686eca22c2f749b5f816685a202835032f6683080e2b53545bbb62"
FULL_RUN_APPROVAL_MARKER = "racecraft-swebench-verified-full-v1"
MAX_QUALIFICATION_TASKS = 10
VERIFIED_TASK_COUNT = 500

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_RUN_ID_RE = re.compile(r"swebench-[0-9a-f]{16}")
_INSTANCE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}")
_REVISION_RE = re.compile(r"[0-9a-f]{40}")
_RECORD_FIELDS = {
    "instance_id",
    "repo",
    "base_commit",
    "problem_statement",
    "patch",
    "test_patch",
    "version",
    "FAIL_TO_PASS",
    "PASS_TO_PASS",
    "environment_setup_commit",
}
_QUALIFICATION_SELECTION_SEED = "racecraft-swebench-qualification-v1"
_PROFILE_KEYS = {
    "schema_version",
    "mode",
    "manifest_path",
    "manifest_sha256",
    "mini_swe_agent_version",
    "mini_swe_agent_wheel_sha256",
    "swebench_version",
    "swebench_wheel_sha256",
    "runner_config_sha256",
    "model",
    "model_runtime",
    "image_bindings_path",
    "image_bindings_sha256",
    "image_bindings_task_count",
    "image_bindings_ordered_instance_ids_sha256",
    "control_image_reference",
    "control_image_digest",
    "platform",
    "task_count",
    "parameters",
    "resource_limits",
    "protocol_approval",
}
_PARAMETERS = {
    "reasoning_effort": "xhigh",
    "temperature": 1.0,
    "top_p": 0.95,
    "max_output_tokens": 65_536,
}
_APPROVED_LOCAL_PARAMETERS = {**_PARAMETERS, "reasoning_effort": "on"}
_LIMIT_KEYS = {
    "timeout_seconds",
    "memory_bytes",
    "cpus",
    "pids_limit",
    "nofile_limit",
    "tmpfs_bytes",
    "max_output_bytes",
    "max_turns",
    "max_requests",
    "max_task_output_tokens",
}
_MANIFEST_KEYS = {
    "schema_version",
    "benchmark",
    "dataset_id",
    "dataset_revision",
    "source_sha256",
    "records_path",
    "records_sha256",
    "selection_policy",
    "split",
    "task_count",
    "tasks",
    "ordered_instance_ids_sha256",
    "verified_500_instance_ids",
    "verified_500_ids_sha256",
    "frozen_before_tuning",
    "license_authorized",
    "license_authorization_revision",
}
_TASK_KEYS = {
    "instance_id",
    "base_commit",
    "repository",
    "problem_statement_sha256",
    "record_sha256",
}
_IMAGE_BINDINGS_KEYS = {
    "schema_version",
    "benchmark",
    "mode",
    "task_count",
    "ordered_instance_ids_sha256",
    "control_image_reference",
    "control_image_digest",
    "isolation_revision_sha256",
    "execution_platform",
    "bindings",
}
_IMAGE_BINDING_KEYS = {
    "instance_id",
    "base_commit",
    "source_record_sha256",
    "task_image_reference",
    "task_image_digest",
    "task_build_recipe_sha256",
    "task_build_inputs_sha256",
    "grader_image_reference",
    "grader_image_digest",
    "grader_build_recipe_sha256",
    "grader_build_inputs_sha256",
    "official_scorer_revision_sha256",
    "trusted_tests_sha256",
    "host_test_spec_path",
    "host_test_spec_sha256",
    "execution_eval_script_sha256",
}
_HOST_TEST_SPEC_KEYS = {
    "schema_version",
    "swebench_version",
    "instance_id",
    "source_record_sha256",
    "trusted_tests_sha256",
    "source_eval_script_sha256",
    "test_spec_sha256",
    "canonical_eval_script_b64",
    "canonical_eval_script_sha256",
    "execution_eval_script_b64",
    "execution_eval_script_sha256",
    "scorer_entrypoint_sha256",
    "test_spec",
}
_HOST_TEST_SPEC_FIELDS = {
    "instance_id",
    "image",
    "repo",
    "version",
    "FAIL_TO_PASS",
    "PASS_TO_PASS",
    "log_parser",
    "eval_type",
    "eval_script",
    "image_assets",
}
_SCORER_ENTRYPOINT_RESPONSE_KEYS = {
    "schema_version",
    "action",
    "scorer_status",
    "error",
    "swebench_version",
    "instance_id",
    "test_spec_sha256",
    "eval_script_b64",
    "eval_script_sha256",
    "scorer_entrypoint_sha256",
    "patch_sha256",
    "grading_log_sha256",
    "host_cleanup_confirmed",
    "resolved",
    "report",
}
_SCORER_ENTRYPOINT_PATH = "/opt/racecraft/scorer_entrypoint.py"
_SCORER_PLATFORM = "linux/arm64/v8"
_SCORER_REQUEST_LIMIT = 160 * 1024 * 1024
_SCORER_RESPONSE_LIMIT = 16 * 1024 * 1024
_SCORER_PATCH_LIMIT = 64 * 1024 * 1024
_SCORER_LOG_LIMIT = 64 * 1024 * 1024
_GRADER_ENTRYPOINT_RESPONSE_KEYS = {
    "schema_version",
    "phase",
    "process_exit_code",
    "eval_exit_code",
    "timed_out",
    "patch_sha256",
    "eval_script_sha256",
    "stdout_b64",
    "stderr_b64",
    "error",
}
_GRADER_CAPTURE_KEYS = {
    "schema_version",
    "phase",
    "launcher_exit_code",
    "process_exit_code",
    "eval_launcher_exit_code",
    "eval_exit_code",
    "timed_out",
    "patch_sha256",
    "eval_script_sha256",
    "stdout",
    "stderr",
    "error",
}
_TEST_EXIT_CODE_RE = re.compile(rb">>>>> Test Exit Code:\s*(-?\d+)")
_ADAPTER_KEYS = {
    "locality_guard_configured",
    "served_instance_guard_configured",
    "multi_turn",
    "bash_only_actions",
    "transport_retries",
    "cache_enabled",
    "redirects_enabled",
    "cloud_fallback",
    "max_requests_source",
    "step_limit_source",
    "wall_time_limit_source",
    "adapter_revision_sha256",
    "served_model_fingerprint",
    "runtime_identity_sha256",
}
_CHECKPOINT_SCHEMA_VERSION = 5
_CHECKPOINT_STATES = {
    "in_flight",
    "ambiguous",
    "response_verified",
    "cleanup_pending",
    "terminal",
    "invalid",
}
_CHECKPOINT_KEYS = {
    "sdk_budget",
    "schema_version",
    "task_index",
    "instance_id_sha256",
    "task_fingerprint_sha256",
    "image_binding_sha256",
    "manifest_sha256",
    "protocol_fingerprint",
    "attempt_nonce",
    "control_image_digest",
    "control_container_name",
    "control_identity_sha256",
    "control_container_id",
    "state",
    "attempted",
    "status",
    "host_response_receipt",
    "output_tokens",
    "prompt_tokens",
    "finish_reason",
    "context_length",
    "context_truncation_status",
    "truncation_status",
    "trajectory_elapsed_seconds",
    "trajectory_sha256",
    "model_patch_sha256",
    "grader_evidence_sha256",
    "grade",
}
_AGENT_TELEMETRY_KEYS = {
    "output_tokens",
    "prompt_tokens",
    "finish_reason",
    "context_length",
    "context_truncation_status",
    "truncation_status",
    "trajectory_elapsed_seconds",
}
_CONTEXT_TRUNCATION_STATUSES = {"context_length_reached"}
_TRUNCATION_STATUSES = {
    "none",
    "generation_limit_reached",
    "context_length_reached",
    "limit_reached_unspecified",
}
_HOST_RESPONSE_RECEIPT_KEYS = {
    "schema_version",
    "task_instance_id_sha256",
    "model_instance_id_sha256",
    "task_fingerprint_sha256",
    "served_model_fingerprint",
    "request_sha256",
    "response_sha256",
    "response_schema",
}
_RPC_STDOUT_LIMIT = 8_388_608
_CONTROL_REAP_TIMEOUT_SECONDS = 2.0
_CONTROL_CONTAINER_NAME_RE = re.compile(r"swebench-control-[0-9a-f]{16}")
_CONTROL_ATTEMPT_RE = re.compile(r"[0-9a-f]{16,64}")
_CONTROL_CONTAINER_ID_RE = re.compile(r"[0-9a-f]{64}")
_CONTROL_CREATE_GRACE_SECONDS = 2.0
_CONTROL_REMOVAL_WAIT_SECONDS = 30.0
_CONTROL_DEADLINE_MARGIN_SECONDS = 1200.0
# Upstream swebench_backticks uses 60 s; amd64 emulation on Apple silicon needs more.
_TASK_EXEC_TIMEOUT_SECONDS = 120
# Grader entrypoint verdict after every pinned SWE-bench apply mode failed: the prediction
# does not apply, which SWE-bench scores as not resolved rather than as a harness fault.
_GRADER_EXIT_PATCH_FAILED = 7
_GRADER_PATCH_FAILED = "patch_failed"
# The env block of pinned upstream swebench.yaml; fixed values, never host state. Its
# BASH_ENV=/root/.bashrc activates `testbed`; task images carry a readable copy.
_TASK_EXEC_UPSTREAM_ENV = {
    "PAGER": "cat",
    "MANPAGER": "cat",
    "LESS": "-R",
    "PIP_PROGRESS_BAR": "off",
    "TQDM_DISABLE": "1",
    "BASH_ENV": "/opt/racecraft/bashrc",
}
# Task containers are created with a narrow PATH; commands get the eval image's own PATH.
_TASK_EXEC_ENV = (
    "PATH=/opt/miniconda3/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    *(f"{key}={value}" for key, value in _TASK_EXEC_UPSTREAM_ENV.items()),
)
# Operator-approved grader egress (AGENTS.md rule 5): listed tasks' graders join a gateway's
# network namespace; everything but the task's allowlisted hosts is dropped or refused.
_EGRESS_NETWORK = "swebench-egress-out"
_EGRESS_SOURCES = ("Dockerfile", "gateway.sh", "egress_proxy.py", "probe.py")
_EGRESS_GATEWAY_CAPS = frozenset({"NET_ADMIN", "NET_RAW", "SETUID", "SETGID"})
_EGRESS_READY_SECONDS = 30
_EGRESS_PROBE_UIDS = (65532, 0)
_EGRESS_PROBE_TIMEOUT_SECONDS = 180
_EGRESS_HOST_RE = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}"
)
_CONTROL_LABEL_ROLE = "racecraft.swebench.role"
_CONTROL_LABEL_IDENTITY = "racecraft.swebench.identity"
_CONTROL_LABEL_TASK = "racecraft.swebench.task"
_CONTROL_LABEL_ATTEMPT = "racecraft.swebench.attempt"
_CONTROL_LABEL_IMAGE = "racecraft.swebench.image"
_HTTP_RESPONSE_LIMIT = 1_048_576
_HTTP_HEADER_LIMIT = 64 * 1024
_HTTP_READ_CHUNK = 64 * 1024
_HTTP_SELECT_SLICE_SECONDS = 0.05
_RUN_INVALID_KEYS = {
    "schema_version",
    "reason",
    "checkpoint",
    "manifest_sha256",
    "protocol_fingerprint",
    "cleanup_error_count",
}


class SwebenchError(ValueError):
    """Raised when a SWE-bench execution contract is incomplete or drifts."""


class _SdkTransportStopped(SwebenchError):
    """A cancelled/uncertain inference must stop the run, not advance the task loop."""


class _SdkTrajectoryBudgetExhausted(SwebenchError):
    """A finished trajectory cannot reserve another request within its budget."""


def _sdk_transport_fingerprint(repo: Path) -> str:
    paths = (
        "src/local_evals/swebench_sdk.py",
        "scripts/swebench-sdk/transport.mjs",
        "scripts/swebench-sdk/package.json",
        "scripts/swebench-sdk/package-lock.json",
    )
    return _sha256(_canonical({name: _sha256((repo / name).read_bytes()) for name in paths}))


def _sdk_budget_state(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    state = dict(_mapping(value, "SDK budget"))
    _exact_keys(
        state,
        {
            "requests",
            "charged_output_tokens",
            "last_actual_output_tokens",
            "actual_output_tokens",
            "last_request_sha256",
            "state",
        },
        "SDK budget",
    )
    if any(
        type(state[key]) is not int or state[key] < 0
        for key in ("requests", "charged_output_tokens")
    ):
        raise SwebenchError("SDK budget counters are invalid")
    actual = state["last_actual_output_tokens"]
    if actual is not None and (type(actual) is not int or not 0 <= actual <= MAX_OUTPUT):
        raise SwebenchError("SDK actual usage is invalid")
    total = state["actual_output_tokens"]
    if total is not None and (
        type(total) is not int or not 0 <= total <= state["charged_output_tokens"]
    ):
        raise SwebenchError("SDK cumulative actual usage is invalid")
    if state["state"] not in {"reserved", "completed", "stopped"} or not _is_sha256(
        state["last_request_sha256"]
    ):
        raise SwebenchError("SDK budget state is invalid")
    return state


def _checkpoint_sdk_chat(
    body: Mapping[str, object],
    *,
    checkpoint_path: Path,
    deadline: float,
    output_limit: int,
    request_limit: int,
    repo: Path,
) -> Mapping[str, Any]:
    """Reserve durably before the one-shot child; uncertain calls are never refunded."""
    checkpoint = dict(_mapping(json.loads(checkpoint_path.read_bytes()), "checkpoint"))
    old = _sdk_budget_state(checkpoint.get("sdk_budget"))
    if old is not None and old["state"] != "completed":
        raise _SdkTransportStopped("SDK request requires reconciliation; retry refused")
    spent = old["charged_output_tokens"] if old else 0
    requests = old["requests"] if old else 0
    if requests >= request_limit or output_limit - spent < MAX_OUTPUT:
        if old is not None and old["state"] == "completed":
            raise _SdkTrajectoryBudgetExhausted("SDK trajectory request budget exhausted")
        raise SwebenchError("SDK initial request exceeds the configured trajectory budget")
    state = {
        "requests": requests + 1,
        "charged_output_tokens": spent + MAX_OUTPUT,
        "last_actual_output_tokens": None,
        "actual_output_tokens": None,
        "last_request_sha256": _sha256(_canonical(body)),
        "state": "reserved",
    }
    checkpoint["sdk_budget"] = state
    _write_private_json(checkpoint_path, checkpoint)
    executable = shutil.which("node")
    if executable is None:
        raise _SdkTransportStopped("pinned SDK transport requires Node")
    try:
        response = sdk_chat_response(
            body,
            budget=OutputBudget(MAX_OUTPUT),
            deadline=deadline,
            node=Path(executable).resolve(),
            transport=repo / "scripts/swebench-sdk/transport.mjs",
        )
    except Exception as exc:
        # Keep the reservation; neither a child exception nor reaping proves inference stopped.
        raise _SdkTransportStopped("SDK completion is unverified") from exc
    if response.get("status") != "completed":
        state["state"] = "stopped"
        _write_private_json(checkpoint_path, checkpoint)
        raise _SdkTransportStopped("SDK inference stopped or completion is unverified")
    actual = _mapping(response.get("usage"), "SDK usage").get("completion_tokens")
    charge = response.get("charged_output_tokens")
    if (
        type(charge) is not int
        or not 0 <= charge <= MAX_OUTPUT
        or (actual is None and charge != MAX_OUTPUT)
        or (actual is not None and (type(actual) is not int or actual != charge))
    ):
        raise _SdkTransportStopped("SDK settlement is invalid")
    previous_actual = old["actual_output_tokens"] if old else 0
    state.update(
        state="completed",
        charged_output_tokens=spent + charge,
        last_actual_output_tokens=actual,
        actual_output_tokens=(
            None if previous_actual is None or actual is None else previous_actual + actual
        ),
    )
    _write_private_json(checkpoint_path, checkpoint)
    return response


class _Runner(Protocol):
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
    ) -> subprocess.CompletedProcess[Any]: ...


ParameterProbe = Callable[[str, str, Mapping[str, object]], Mapping[str, object]]
AdapterProbe = Callable[[str, str, Mapping[str, object]], Mapping[str, object]]
AgentExecutor = Callable[..., Mapping[str, object]]
GraderExecutor = Callable[..., Mapping[str, object]]
TestSpecFactory = Callable[["_Task", Path], tuple[Any, str]]
ScoreExecutor = Callable[[Any], Any]


@dataclass(frozen=True)
class _ScorerAttestation:
    """Explicit identity required when tests inject scorer callables."""

    control_image_reference: str
    control_image_digest: str
    platform: str
    swebench_version: str
    scorer_entrypoint_sha256: str


@dataclass(frozen=True)
class _ScorerTestSpec:
    """Host-owned frozen TestSpec inputs and generated eval bytes."""

    fields: Mapping[str, Any]
    instance_id: str
    image: str
    task_fingerprint_sha256: str
    eval_script: str
    execution_eval_script: str
    test_spec_sha256: str
    eval_script_sha256: str
    execution_eval_script_sha256: str
    scorer_entrypoint_sha256: str


@dataclass(frozen=True)
class _ScorerResult:
    status: str
    official: bool
    resolved: bool | None
    report: Mapping[str, Any] | None = None


_HttpCancellation = Callable[[], bool]


def _http_json(
    origin: str,
    path: str,
    *,
    body: Mapping[str, object] | None = None,
    timeout: float = 30,
    deadline: float | None = None,
    cancelled: _HttpCancellation | None = None,
) -> Mapping[str, Any]:
    """Make one loopback request under one absolute, cancellable deadline."""

    parsed = urlparse(origin)
    host = parsed.hostname
    if (
        parsed.scheme != "http"
        or host not in {"127.0.0.1", "::1", "localhost"}
        or parsed.username is not None
        or parsed.password is not None
        or not path.startswith("/")
        or "\r" in path
        or "\n" in path
    ):
        raise SwebenchError("local LM Studio HTTP origin or path is invalid")
    try:
        port = parsed.port or 80
    except ValueError as exc:
        raise SwebenchError("local LM Studio HTTP origin is invalid") from exc
    if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
        raise SwebenchError("local LM Studio HTTP timeout must be finite and positive")
    if deadline is not None and (isinstance(deadline, bool) or not math.isfinite(deadline)):
        raise SwebenchError("local LM Studio HTTP deadline must be finite")
    now = time.monotonic()
    request_deadline = now + timeout if deadline is None else min(deadline, now + timeout)
    _remaining_http_budget(request_deadline, cancelled)
    request_body = _canonical(body) if body is not None else b""
    host_header = f"[{host}]" if ":" in host else host
    if port != 80:
        host_header = f"{host_header}:{port}"
    headers = [
        f"{'POST' if body is not None else 'GET'} {path} HTTP/1.1",
        f"Host: {host_header}",
        "Accept: application/json",
        "Connection: close",
        f"Content-Length: {len(request_body)}",
    ]
    token = os.environ.get("LM_API_TOKEN")
    if token:
        if "\r" in token or "\n" in token:
            raise SwebenchError("local LM Studio HTTP authorization token is invalid")
        headers.append(f"Authorization: Bearer {token}")
    if body is not None:
        headers.append("Content-Type: application/json")
    wire_request = ("\r\n".join(headers) + "\r\n\r\n").encode() + request_body
    selector = selectors.DefaultSelector()
    sock: socket.socket | None = None
    try:
        sock = _open_http_socket(host, port)
        sock.setblocking(False)
        address: tuple[str, int] | tuple[str, int, int, int]
        if host == "::1":
            address = ("::1", port, 0, 0)
        else:
            address = ("127.0.0.1", port)
        _http_connect(sock, address, selector, request_deadline, cancelled)
        _http_send(sock, wire_request, selector, request_deadline, cancelled)
        status, response_headers, initial_body = _http_read_headers(
            sock, selector, request_deadline, cancelled
        )
        if status < 200 or status >= 300:
            raise SwebenchError(
                f"local LM Studio readiness request failed with HTTP status {status}"
            )
        raw = _http_read_body(
            sock,
            selector,
            response_headers,
            initial_body,
            request_deadline,
            cancelled,
        )
    except SwebenchError:
        raise
    except OSError as exc:
        raise SwebenchError("local LM Studio readiness request failed") from exc
    finally:
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        selector.close()
    _remaining_http_budget(request_deadline, cancelled)
    try:
        result = _mapping(json.loads(raw), "LM Studio response")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SwebenchError("local LM Studio readiness response is invalid") from exc
    _remaining_http_budget(request_deadline, cancelled)
    return result


def _remaining_http_budget(deadline: float, cancelled: _HttpCancellation | None = None) -> float:
    if isinstance(deadline, bool) or not math.isfinite(deadline):
        raise SwebenchError("local LM Studio HTTP deadline must be finite")
    if cancelled is not None and cancelled():
        raise SwebenchError("local LM Studio HTTP request cancelled")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SwebenchError("local LM Studio HTTP deadline exceeded")
    return remaining


def _open_http_socket(host: str, port: int) -> socket.socket:
    if host == "::1":
        return socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    if host in {"127.0.0.1", "localhost"}:
        return socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    raise SwebenchError("local LM Studio HTTP origin is not loopback")


def _http_wait(
    sock: socket.socket,
    selector: selectors.BaseSelector,
    events: int,
    deadline: float,
    cancelled: _HttpCancellation | None,
) -> None:
    selector.register(sock, events)
    try:
        while True:
            remaining = _remaining_http_budget(deadline, cancelled)
            ready = selector.select(min(remaining, _HTTP_SELECT_SLICE_SECONDS))
            if ready:
                return
    finally:
        try:
            selector.unregister(sock)
        except (KeyError, OSError, ValueError):
            pass


def _http_connect(
    sock: socket.socket,
    address: tuple[str, int] | tuple[str, int, int, int],
    selector: selectors.BaseSelector,
    deadline: float,
    cancelled: _HttpCancellation | None,
) -> None:
    result = sock.connect_ex(address)
    if result in {0, errno.EISCONN}:
        return
    if result not in {errno.EINPROGRESS, errno.EALREADY, errno.EWOULDBLOCK, errno.EINTR}:
        raise OSError(result, os.strerror(result))
    while True:
        _http_wait(sock, selector, selectors.EVENT_WRITE, deadline, cancelled)
        error = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
        if error in {0, errno.EISCONN}:
            return
        if error in {errno.EINPROGRESS, errno.EALREADY, errno.EWOULDBLOCK}:
            continue
        raise OSError(error, os.strerror(error))


def _http_send(
    sock: socket.socket,
    payload: bytes,
    selector: selectors.BaseSelector,
    deadline: float,
    cancelled: _HttpCancellation | None,
) -> None:
    offset = 0
    while offset < len(payload):
        _remaining_http_budget(deadline, cancelled)
        try:
            sent = sock.send(payload[offset:])
        except (BlockingIOError, InterruptedError):
            sent = 0
        if sent > 0:
            offset += sent
            continue
        _http_wait(sock, selector, selectors.EVENT_WRITE, deadline, cancelled)


def _http_recv(
    sock: socket.socket,
    selector: selectors.BaseSelector,
    deadline: float,
    cancelled: _HttpCancellation | None,
) -> bytes:
    while True:
        _http_wait(sock, selector, selectors.EVENT_READ, deadline, cancelled)
        try:
            return sock.recv(_HTTP_READ_CHUNK)
        except (BlockingIOError, InterruptedError):
            continue


def _http_read_headers(
    sock: socket.socket,
    selector: selectors.BaseSelector,
    deadline: float,
    cancelled: _HttpCancellation | None,
) -> tuple[int, Mapping[str, str], bytes]:
    buffer = bytearray()
    while True:
        _remaining_http_budget(deadline, cancelled)
        marker = buffer.find(b"\r\n\r\n")
        if marker >= 0:
            if marker > _HTTP_HEADER_LIMIT:
                raise SwebenchError("local LM Studio HTTP response headers exceeded their bound")
            header_bytes = bytes(buffer[:marker])
            body = bytes(buffer[marker + 4 :])
            break
        if len(buffer) > _HTTP_HEADER_LIMIT:
            raise SwebenchError("local LM Studio HTTP response headers exceeded their bound")
        chunk = _http_recv(sock, selector, deadline, cancelled)
        if not chunk:
            raise SwebenchError("local LM Studio HTTP response ended before its headers")
        buffer.extend(chunk)
    lines = header_bytes.split(b"\r\n")
    status_line = lines[0].split() if lines else []
    if (
        len(status_line) < 2
        or status_line[0] not in {b"HTTP/1.0", b"HTTP/1.1"}
        or len(status_line[1]) != 3
        or not status_line[1].isdigit()
    ):
        raise SwebenchError("local LM Studio HTTP response status is invalid")
    try:
        status = int(status_line[1])
    except ValueError as exc:
        raise SwebenchError("local LM Studio HTTP response status is invalid") from exc
    if status < 100 or status > 599:
        raise SwebenchError("local LM Studio HTTP response status is invalid")
    response_headers: dict[str, str] = {}
    for line in lines[1:]:
        try:
            name, value = line.split(b":", 1)
            name_text = name.decode("ascii").lower()
        except (UnicodeDecodeError, ValueError) as exc:
            raise SwebenchError("local LM Studio HTTP response headers are invalid") from exc
        if name_text in {"content-length", "transfer-encoding"} and name_text in response_headers:
            raise SwebenchError("local LM Studio HTTP response framing is ambiguous")
        response_headers[name_text] = value.decode("latin-1").strip()
    _validate_http_response_framing(response_headers)
    return status, response_headers, body


def _validate_http_response_framing(headers: Mapping[str, str]) -> str:
    content_length = headers.get("content-length")
    transfer_encoding = headers.get("transfer-encoding")
    if transfer_encoding is not None:
        if content_length is not None:
            raise SwebenchError("local LM Studio HTTP response framing is ambiguous")
        encodings = tuple(part.strip().lower() for part in transfer_encoding.split(","))
        if encodings != ("chunked",):
            raise SwebenchError("local LM Studio HTTP transfer encoding is unsupported")
        return "chunked"
    if content_length is not None:
        return "content-length"
    return "close"


def _http_read_body(
    sock: socket.socket,
    selector: selectors.BaseSelector,
    headers: Mapping[str, str],
    initial: bytes,
    deadline: float,
    cancelled: _HttpCancellation | None,
) -> bytes:
    _remaining_http_budget(deadline, cancelled)
    framing = _validate_http_response_framing(headers)
    content_length = headers.get("content-length")
    if framing == "content-length":
        if content_length is None:
            raise SwebenchError("local LM Studio HTTP response framing is invalid")
        try:
            expected = int(content_length)
        except ValueError as exc:
            raise SwebenchError("local LM Studio HTTP content length is invalid") from exc
        if expected < 0:
            raise SwebenchError("local LM Studio HTTP content length is invalid")
        if expected > _HTTP_RESPONSE_LIMIT:
            raise SwebenchError("local LM Studio readiness response exceeded its bound")
        body = bytearray(initial[:expected])
        while len(body) < expected:
            _remaining_http_budget(deadline, cancelled)
            chunk = _http_recv(sock, selector, deadline, cancelled)
            if not chunk:
                raise SwebenchError("local LM Studio HTTP response ended before its body")
            body.extend(chunk[: expected - len(body)])
        return bytes(body)
    if framing == "chunked":
        return _http_read_chunked_body(sock, selector, initial, deadline, cancelled)
    body = bytearray(initial)
    if len(body) > _HTTP_RESPONSE_LIMIT:
        raise SwebenchError("local LM Studio readiness response exceeded its bound")
    while True:
        _remaining_http_budget(deadline, cancelled)
        chunk = _http_recv(sock, selector, deadline, cancelled)
        if not chunk:
            return bytes(body)
        body.extend(chunk)
        if len(body) > _HTTP_RESPONSE_LIMIT:
            raise SwebenchError("local LM Studio readiness response exceeded its bound")


def _http_read_chunked_body(
    sock: socket.socket,
    selector: selectors.BaseSelector,
    initial: bytes,
    deadline: float,
    cancelled: _HttpCancellation | None,
) -> bytes:
    buffer = bytearray(initial)
    body = bytearray()

    def read_line() -> bytes:
        while True:
            _remaining_http_budget(deadline, cancelled)
            marker = buffer.find(b"\r\n")
            if marker >= 0:
                if marker > _HTTP_HEADER_LIMIT:
                    raise SwebenchError("local LM Studio HTTP chunk header exceeded its bound")
                line = bytes(buffer[:marker])
                del buffer[: marker + 2]
                return line
            if len(buffer) > _HTTP_HEADER_LIMIT:
                raise SwebenchError("local LM Studio HTTP chunk header exceeded its bound")
            chunk = _http_recv(sock, selector, deadline, cancelled)
            if not chunk:
                raise SwebenchError("local LM Studio HTTP response ended in a chunk")
            buffer.extend(chunk)

    def ensure(count: int) -> None:
        while len(buffer) < count:
            _remaining_http_budget(deadline, cancelled)
            chunk = _http_recv(sock, selector, deadline, cancelled)
            if not chunk:
                raise SwebenchError("local LM Studio HTTP response ended in a chunk")
            buffer.extend(chunk)

    while True:
        _remaining_http_budget(deadline, cancelled)
        line = read_line()
        try:
            size = int(line.split(b";", 1)[0].strip(), 16)
        except ValueError as exc:
            raise SwebenchError("local LM Studio HTTP chunk size is invalid") from exc
        if size < 0:
            raise SwebenchError("local LM Studio HTTP chunk size is invalid")
        if size == 0:
            while read_line():
                pass
            return bytes(body)
        if len(body) + size > _HTTP_RESPONSE_LIMIT:
            raise SwebenchError("local LM Studio readiness response exceeded its bound")
        ensure(size + 2)
        body.extend(buffer[:size])
        if buffer[size : size + 2] != b"\r\n":
            raise SwebenchError("local LM Studio HTTP chunk terminator is invalid")
        del buffer[: size + 2]


def _remaining_control_budget(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SwebenchError("control-plane RPC deadline exceeded")
    return remaining


def _loaded_model(
    origin: str,
    model: str,
    *,
    deadline: float | None = None,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    if deadline is None:
        payload = _http_json(origin, "/api/v1/models")
    else:
        payload = _http_json(
            origin,
            "/api/v1/models",
            timeout=_remaining_control_budget(deadline),
            deadline=deadline,
        )
    if deadline is not None:
        _remaining_control_budget(deadline)
    models = payload.get("models")
    if not isinstance(models, list):
        raise SwebenchError("LM Studio model inventory is invalid")
    matches: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    for candidate in models:
        if not isinstance(candidate, Mapping):
            continue
        candidate_key = candidate.get("key")
        instances = candidate.get("loaded_instances")
        if isinstance(instances, list):
            matches.extend(
                (cast(Mapping[str, Any], candidate), cast(Mapping[str, Any], instance))
                for instance in instances
                if isinstance(instance, Mapping)
                and (candidate_key == model or instance.get("id") == model)
            )
    if len(matches) != 1 or not _nonblank(matches[0][1].get("id"), "model instance id"):
        raise SwebenchError("LM Studio must expose exactly one loaded instance of the frozen model")
    return matches[0]


def _served_model_fingerprint(model_record: Mapping[str, Any], instance: Mapping[str, Any]) -> str:
    def without_remaining_ttl(instance_record: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: value for key, value in instance_record.items() if key != "remaining_ttl_seconds"
        }

    normalized_model = dict(model_record)
    loaded_instances = normalized_model.get("loaded_instances")
    if isinstance(loaded_instances, list):
        normalized_model["loaded_instances"] = [
            without_remaining_ttl(loaded_instance)
            if isinstance(loaded_instance, Mapping)
            else loaded_instance
            for loaded_instance in loaded_instances
        ]
    return _sha256(
        _canonical(
            {
                "model": normalized_model,
                "instance": without_remaining_ttl(instance),
            }
        )
    )


def _host_verified_chat_response(
    origin: str,
    body: Mapping[str, object],
    *,
    expected_model: str,
    expected_task_fingerprint: str,
    task_instance_id: str,
    expected_model_instance_id: str | None,
    expected_served_model_fingerprint: str,
    on_verified_response: Callable[[Mapping[str, Any]], None] | None = None,
    deadline: float | None = None,
    sdk_checkpoint: Path | None = None,
    sdk_output_limit: int = 0,
    sdk_request_limit: int = 0,
) -> tuple[Mapping[str, Any], dict[str, str | int]]:
    """Issue one host-owned request and return a receipt bound to its identity."""

    if body.get("model") != expected_model:
        raise SwebenchError("LM Studio request model drifted")
    if not _is_sha256(expected_task_fingerprint) or not _is_sha256(
        expected_served_model_fingerprint
    ):
        raise SwebenchError("LM Studio receipt binding is invalid")
    if deadline is None:
        before_model, before_instance = _loaded_model(origin, expected_model)
    else:
        before_model, before_instance = _loaded_model(origin, expected_model, deadline=deadline)
    before_id = _nonblank(before_instance.get("id"), "model instance id")
    before_fingerprint = _served_model_fingerprint(before_model, before_instance)
    if (
        expected_model_instance_id is not None and before_id != expected_model_instance_id
    ) or before_fingerprint != expected_served_model_fingerprint:
        raise SwebenchError("LM Studio served model identity drifted before response")
    if sdk_checkpoint is None or deadline is None:
        raise SwebenchError("SDK transport requires a durable checkpoint and absolute deadline")
    parsed = urlparse(origin)
    if parsed.hostname != "127.0.0.1" or parsed.port != 1234 or parsed.scheme != "http":
        raise SwebenchError("SDK transport requires the exact qualified loopback endpoint")
    response = _checkpoint_sdk_chat(
        body,
        checkpoint_path=sdk_checkpoint,
        deadline=deadline,
        output_limit=sdk_output_limit,
        request_limit=sdk_request_limit,
        repo=Path(__file__).resolve().parents[2],
    )
    if deadline is not None:
        _remaining_control_budget(deadline)
    response_model = response.get("model")
    choices = response.get("choices")
    if response_model not in {expected_model, before_id} or not isinstance(choices, list):
        raise SwebenchError("LM Studio completion shape is invalid")
    if len(choices) != 1:
        raise SwebenchError("LM Studio completion shape is invalid")
    choice = choices[0]
    if not isinstance(choice, Mapping) or not isinstance(choice.get("message"), Mapping):
        raise SwebenchError("LM Studio completion shape is invalid")
    if deadline is None:
        after_model, after_instance = _loaded_model(origin, expected_model)
    else:
        after_model, after_instance = _loaded_model(origin, expected_model, deadline=deadline)
    after_id = _nonblank(after_instance.get("id"), "model instance id")
    after_fingerprint = _served_model_fingerprint(after_model, after_instance)
    if (
        before_id != after_id
        or before_fingerprint != after_fingerprint
        or after_fingerprint != expected_served_model_fingerprint
    ):
        raise SwebenchError("LM Studio served model identity drifted after response")
    receipt: dict[str, str | int] = {
        "schema_version": 1,
        "task_instance_id_sha256": _sha256(task_instance_id.encode()),
        "model_instance_id_sha256": _sha256(before_id.encode()),
        "task_fingerprint_sha256": expected_task_fingerprint,
        "served_model_fingerprint": expected_served_model_fingerprint,
        "request_sha256": _sha256(_canonical(body)),
        "response_sha256": _sha256(_canonical(response)),
        "response_schema": "openai_chat_completion",
    }
    if on_verified_response is not None:
        on_verified_response(receipt)
    return response, receipt


def _default_parameter_probe(
    origin: str, model: str, parameters: Mapping[str, object]
) -> Mapping[str, object]:
    """Verify effective settings using the same SDK transport, never an HTTP fallback."""

    if dict(parameters) != _APPROVED_LOCAL_PARAMETERS:
        raise SwebenchError(
            "xhigh is unsupported locally; the explicit approved on variant is required"
        )
    parsed = urlparse(origin)
    if parsed.hostname != "127.0.0.1" or parsed.port != 1234 or parsed.scheme != "http":
        raise SwebenchError("SDK probe requires the exact qualified loopback endpoint")
    _loaded_model(origin, model)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with OK."}],
        "stream": False,
        "temperature": parameters["temperature"],
        "top_p": parameters["top_p"],
        "max_tokens": parameters["max_output_tokens"],
        "reasoning_effort": parameters["reasoning_effort"],
    }
    node = shutil.which("node")
    if node is None:
        raise SwebenchError("pinned SDK probe requires Node")
    response = sdk_chat_response(
        payload,
        budget=OutputBudget(MAX_OUTPUT),
        deadline=time.monotonic() + 120,
        node=Path(node).resolve(),
        transport=Path(__file__).resolve().parents[2] / "scripts/swebench-sdk/transport.mjs",
    )
    evidence = response.get("evidence", {})
    expected = {
        "reasoning": "on",
        "temperature": 1,
        "top_p": 0.95,
        "max_output_tokens": MAX_OUTPUT,
        "context_length": 131072,
    }
    if (
        response.get("status") != "completed"
        or not isinstance(evidence, Mapping)
        or any(evidence.get(key) != value for key, value in expected.items())
    ):
        raise _SdkTransportStopped("SDK effective-settings probe failed; inference stop unverified")
    return {key: True for key in parameters}


def _default_adapter_probe(
    origin: str, model: str, _parameters: Mapping[str, object]
) -> Mapping[str, object]:
    model_record, instance = _loaded_model(origin, model)
    served = _served_model_fingerprint(model_record, instance)
    control_reference, control_digest = _control_image_identity()
    runtime_identity = _sha256(
        _canonical(
            {
                "mini_swe_agent": MINI_SWE_AGENT_VERSION,
                "swebench": SWEBENCH_VERSION,
                "control_image_reference": control_reference,
                "control_image_digest": control_digest,
                "model": model,
                "instance_id": instance["id"],
            }
        )
    )
    assets_root = Path(__file__).resolve().parents[2] / "sandbox" / "swebench"
    adapter_assets = (
        "Dockerfile",
        "control-plane-requirements.lock",
        "mini-swe-agent.yaml",
        "runner.py",
    )
    adapter_revision = {name: _sha256((assets_root / name).read_bytes()) for name in adapter_assets}
    adapter_revision["sdk_transport"] = _sdk_transport_fingerprint(assets_root.parents[1])
    return {
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
        "adapter_revision_sha256": _sha256(_canonical(adapter_revision)),
        "served_model_fingerprint": served,
        "runtime_identity_sha256": runtime_identity,
    }


def _control_image_identity() -> tuple[str, str]:
    reference = os.environ.get("LOCAL_EVALS_SWEBENCH_CONTROL_IMAGE_REFERENCE", "")
    digest = os.environ.get("LOCAL_EVALS_SWEBENCH_CONTROL_IMAGE_DIGEST", "")
    checked_reference, checked_digest = _validate_image(reference, digest, "control_image")
    if not checked_reference.endswith(f"@{checked_digest}"):
        raise SwebenchError("control image reference must embed its exact digest")
    return checked_reference, checked_digest


def _control_container_identity(
    *,
    task_fingerprint_sha256: object,
    attempt_nonce: object,
    control_image_digest: object,
) -> tuple[str, str]:
    task_fingerprint = _nonblank(task_fingerprint_sha256, "task_fingerprint_sha256")
    if not _is_sha256(task_fingerprint):
        raise SwebenchError("control task fingerprint is invalid")
    nonce = _nonblank(attempt_nonce, "attempt_nonce")
    if _CONTROL_ATTEMPT_RE.fullmatch(nonce) is None:
        raise SwebenchError("control attempt nonce is invalid")
    image_digest = _nonblank(control_image_digest, "control_image_digest")
    if _DIGEST_RE.fullmatch(image_digest) is None:
        raise SwebenchError("control image digest is invalid")
    identity = _sha256(
        _canonical(
            {
                "schema_version": 1,
                "role": "control",
                "task_fingerprint_sha256": task_fingerprint,
                "attempt_nonce": nonce,
                "control_image_digest": image_digest,
            }
        )
    )
    return f"swebench-control-{identity[:16]}", identity


def _control_container_labels(kwargs: Mapping[str, object]) -> tuple[str, dict[str, str]]:
    image_digest = _nonblank(kwargs.get("control_image_digest"), "control_image_digest")
    expected_name, expected_identity = _control_container_identity(
        task_fingerprint_sha256=kwargs.get("task_fingerprint_sha256"),
        attempt_nonce=kwargs.get("attempt_nonce"),
        control_image_digest=image_digest,
    )
    name = _nonblank(kwargs.get("control_container_name"), "control_container_name")
    identity = _nonblank(kwargs.get("control_identity_sha256"), "control_identity_sha256")
    if (
        _CONTROL_CONTAINER_NAME_RE.fullmatch(name) is None
        or name != expected_name
        or identity != expected_identity
    ):
        raise SwebenchError("control container identity does not match the task attempt")
    return name, {
        _CONTROL_LABEL_ROLE: "control",
        _CONTROL_LABEL_IDENTITY: expected_identity,
        _CONTROL_LABEL_TASK: _nonblank(
            kwargs.get("task_fingerprint_sha256"), "task_fingerprint_sha256"
        ),
        _CONTROL_LABEL_ATTEMPT: _nonblank(kwargs.get("attempt_nonce"), "attempt_nonce"),
        _CONTROL_LABEL_IMAGE: image_digest,
    }


def _control_cidfile(kwargs: Mapping[str, object]) -> Path | None:
    evidence_dir = kwargs.get("evidence_dir")
    if evidence_dir is None:
        return None
    if isinstance(evidence_dir, Path):
        root = evidence_dir
    elif isinstance(evidence_dir, str) and evidence_dir.strip():
        root = Path(evidence_dir)
    else:
        raise SwebenchError("control evidence directory is invalid")
    if not root.is_absolute():
        raise SwebenchError("control evidence directory must be absolute")
    name, _labels = _control_container_labels(kwargs)
    return root / f"{name}.cid"


def _control_plane_args(kwargs: Mapping[str, object]) -> tuple[str, ...]:
    reference = _nonblank(kwargs.get("control_image_reference"), "control_image_reference")
    digest = _nonblank(kwargs.get("control_image_digest"), "control_image_digest")
    _validate_image(reference, digest, "control_image")
    if not reference.endswith(f"@{digest}"):
        raise SwebenchError("control image reference must embed its exact digest")
    name, labels = _control_container_labels(kwargs)
    label_args = tuple(
        item for key, value in labels.items() for item in ("--label", f"{key}={value}")
    )
    cidfile = _control_cidfile(kwargs)
    cidfile_args = ("--cidfile", str(cidfile)) if cidfile is not None else ()
    return (
        cast(str, kwargs["docker_executable"]),
        "run",
        "--pull=never",
        "--rm",
        "--name",
        name,
        *label_args,
        *cidfile_args,
        "-i",
        "--platform",
        "linux/arm64/v8",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "64",
        "--memory",
        "1073741824",
        "--cpus",
        "1.0",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=64m",  # noqa: S108 - container tmpfs
        "--user",
        "65532:65532",
        reference,
        "python",
        "/opt/racecraft/runner.py",
    )


def _rpc_reply(
    process: subprocess.Popen[bytes],
    value: Mapping[str, object],
    *,
    deadline: float,
) -> None:
    if process.stdin is None:
        raise SwebenchError("control-plane stdin is unavailable")
    try:
        fd = process.stdin.fileno()
        was_blocking = os.get_blocking(fd)
        os.set_blocking(fd, False)
    except (AttributeError, OSError, ValueError) as exc:
        raise SwebenchError("control-plane stdin is unavailable") from exc
    payload = _canonical(value) + b"\n"
    offset = 0
    writer = selectors.DefaultSelector()
    try:
        writer.register(fd, selectors.EVENT_WRITE)
        while offset < len(payload):
            remaining = _remaining_control_budget(deadline)
            if not writer.select(remaining):
                raise SwebenchError("control-plane RPC deadline exceeded")
            try:
                written = os.write(fd, payload[offset:])
            except BlockingIOError:
                continue
            except (BrokenPipeError, OSError) as exc:
                raise SwebenchError("control-plane stdin write failed") from exc
            if written <= 0:
                raise SwebenchError("control-plane stdin write failed")
            offset += written
    finally:
        writer.close()
        try:
            os.set_blocking(fd, was_blocking)
        except OSError:
            pass


def _close_control_streams(process: subprocess.Popen[bytes]) -> None:
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is None:
            continue
        try:
            stream.close()
        except OSError:
            pass


def _reap_control_process(
    process: subprocess.Popen[bytes],
    *,
    timeout: float = _CONTROL_REAP_TIMEOUT_SECONDS,
) -> None:
    kill_error: OSError | None = None
    wait_error: BaseException | None = None
    try:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        except OSError as exc:
            kill_error = exc
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            wait_error = exc
    finally:
        _close_control_streams(process)
    if wait_error is not None:
        raise SwebenchError(
            "control-plane process did not terminate within the reap deadline"
        ) from wait_error
    if kill_error is not None:
        raise SwebenchError("control-plane process could not be terminated") from kill_error


def _validated_control_container_id(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _CONTROL_CONTAINER_ID_RE.fullmatch(value) is None:
        raise SwebenchError("control container ID is invalid")
    return value


def _cleanup_control_container(kwargs: Mapping[str, object]) -> None:
    """Verify and remove only the attested control container for this attempt."""

    name, expected_labels = _control_container_labels(kwargs)
    runner_value = kwargs.get("runner")
    if not callable(runner_value):
        raise SwebenchError("control-container cleanup runner is unavailable")
    runner = cast(_Runner, runner_value)
    docker = _nonblank(kwargs.get("docker_executable"), "docker_executable")
    timeout_value = kwargs.get("timeout_seconds", 0)
    max_output_value = kwargs.get("max_output_bytes", 0)
    if (
        isinstance(timeout_value, bool)
        or not isinstance(timeout_value, (int, float, str))
        or isinstance(max_output_value, bool)
        or not isinstance(max_output_value, (int, float, str))
    ):
        raise SwebenchError("control-container cleanup limits are invalid")
    try:
        timeout = min(30.0, float(timeout_value))
        max_output_bytes = int(max_output_value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SwebenchError("control-container cleanup limits are invalid") from exc
    if not math.isfinite(timeout) or timeout <= 0 or max_output_bytes <= 0:
        raise SwebenchError("control-container cleanup limits are invalid")

    expected_id = _validated_control_container_id(kwargs.get("control_container_id"))
    cidfile = _control_cidfile(kwargs)
    if expected_id is None and cidfile is not None:
        try:
            raw_cid = cidfile.read_text(encoding="ascii").strip()
        except FileNotFoundError:
            raw_cid = ""
        except (OSError, UnicodeDecodeError) as exc:
            raise SwebenchError("control container ID file is unreadable") from exc
        if raw_cid:
            expected_id = _validated_control_container_id(raw_cid)
    control_id_required = kwargs.get("control_id_required") is True

    def invoke(args: tuple[str, ...]) -> tuple[subprocess.CompletedProcess[Any], bytes, bytes]:
        try:
            completed = runner(
                args,
                capture_output=True,
                text=False,
                check=False,
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SwebenchError("control-container cleanup command failed") from exc
        stdout = (
            completed.stdout
            if isinstance(completed.stdout, bytes)
            else str(completed.stdout).encode()
        )
        stderr = (
            completed.stderr
            if isinstance(completed.stderr, bytes)
            else str(completed.stderr).encode()
        )
        if len(stdout) + len(stderr) > max_output_bytes:
            raise SwebenchError("control-container cleanup output exceeded its bound")
        return completed, stdout, stderr

    def is_not_found(
        completed: subprocess.CompletedProcess[Any], stdout: bytes, stderr: bytes
    ) -> bool:
        if completed.returncode != 1:
            return False
        diagnostic = (stdout + stderr).lower()
        return any(marker in diagnostic for marker in (b"no such container", b"no such object"))

    def inspect_labels(reference: str) -> dict[str, str] | None:
        completed, stdout, stderr = invoke(
            (docker, "inspect", "--format", "{{json .Config.Labels}}", reference)
        )
        if completed.returncode != 0:
            if is_not_found(completed, stdout, stderr):
                return None
            raise SwebenchError("control-container inspection failed")
        try:
            value = json.loads(stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SwebenchError("control-container labels are invalid") from exc
        if not isinstance(value, Mapping) or any(
            not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()
        ):
            raise SwebenchError("control-container labels are invalid")
        return dict(value)

    def inspect_id(reference: str) -> str | None:
        completed, stdout, stderr = invoke((docker, "inspect", "--format", "{{.Id}}", reference))
        if completed.returncode != 0:
            if is_not_found(completed, stdout, stderr):
                return None
            raise SwebenchError("control-container ID inspection failed")
        try:
            return _validated_control_container_id(stdout.decode("ascii").strip())
        except (UnicodeDecodeError, SwebenchError) as exc:
            raise SwebenchError("control-container ID inspection is invalid") from exc

    def identity_names() -> list[str]:
        completed, stdout, _stderr = invoke(
            (
                docker,
                "ps",
                "-a",
                "--filter",
                f"label={_CONTROL_LABEL_IDENTITY}={expected_labels[_CONTROL_LABEL_IDENTITY]}",
                "--format",
                "{{.Names}}",
            )
        )
        if completed.returncode != 0:
            raise SwebenchError("control-container identity query failed")
        try:
            return [line for line in stdout.decode("utf-8").splitlines() if line]
        except UnicodeDecodeError as exc:
            raise SwebenchError("control-container identity query is invalid") from exc

    def verify_identity(labels: Mapping[str, str]) -> None:
        if any(labels.get(key) != value for key, value in expected_labels.items()):
            raise SwebenchError("control-container identity labels do not match the task attempt")

    def observe() -> tuple[str | None, bool]:
        nonlocal expected_id
        inspected = inspect_labels(name)
        name_id = inspect_id(name) if inspected is not None else None
        names = identity_names()
        if inspected is not None:
            verify_identity(inspected)
            if name_id is None:
                if inspect_labels(name) is None and not names:
                    return expected_id, False
                raise SwebenchError("control-container ID is unavailable")
            if expected_id is not None and name_id != expected_id:
                raise SwebenchError("control-container ID changed during cleanup")
            expected_id = name_id
            # An exiting --rm container can drop out of `docker ps` while `inspect` still finds
            # it; wait (bounded) for it to be listed again or to disappear.
            hidden_deadline = time.monotonic() + _CONTROL_REMOVAL_WAIT_SECONDS
            while not names and time.monotonic() < hidden_deadline:
                time.sleep(0.1)
                if inspect_labels(name) is None and inspect_labels(expected_id) is None:
                    if identity_names():
                        raise SwebenchError(
                            "control-container identity exists under an unexpected name"
                        )
                    return expected_id, False
                names = identity_names()
            if set(names) != {name}:
                raise SwebenchError("control-container identity query is ambiguous")
            id_labels = inspect_labels(expected_id)
            if id_labels is None:
                if inspect_labels(name) is None and not identity_names():
                    return expected_id, False
                raise SwebenchError("control-container ID and name could not be reconciled")
            verify_identity(id_labels)
            return expected_id, True
        if expected_id is not None and inspect_labels(expected_id) is not None:
            raise SwebenchError("control-container name and ID bindings diverged")
        if names:
            raise SwebenchError("control-container identity exists under an unexpected name")
        return expected_id, False

    grace_deadline = time.monotonic() + _CONTROL_CREATE_GRACE_SECONDS
    while True:
        observed_id, present = observe()
        if present:
            break
        if observed_id is not None:
            return
        if not control_id_required or time.monotonic() >= grace_deadline:
            if control_id_required:
                raise SwebenchError("control container ID was not captured")
            return
        time.sleep(min(0.02, max(0.0, grace_deadline - time.monotonic())))

    removed, stdout, stderr = invoke((docker, "rm", "-f", observed_id or name))
    # A --rm container that already exited is often mid-removal when cleanup starts.
    removal_in_progress = (
        removed.returncode == 1 and b"is already in progress" in (stdout + stderr).lower()
    )
    if (
        removed.returncode != 0
        and not is_not_found(removed, stdout, stderr)
        and not removal_in_progress
    ):
        raise SwebenchError("control-container teardown failed")
    removal_deadline = time.monotonic() + (
        _CONTROL_REMOVAL_WAIT_SECONDS if removal_in_progress else 0.0
    )
    while True:
        _removed_id, still_present = observe()
        if not still_present:
            return
        if time.monotonic() >= removal_deadline:
            raise SwebenchError("control-container cleanup could not be verified")
        time.sleep(0.1)


def _trusted_scorer_container_args(kwargs: Mapping[str, object]) -> tuple[str, ...]:
    reference = _nonblank(kwargs.get("control_image_reference"), "control_image_reference")
    digest = _nonblank(kwargs.get("control_image_digest"), "control_image_digest")
    _validate_image(reference, digest, "control_image")
    if not reference.endswith(f"@{digest}"):
        raise SwebenchError("trusted scorer image reference is not digest-pinned")
    name, labels = _control_container_labels(kwargs)
    label_args = tuple(
        item for key, value in labels.items() for item in ("--label", f"{key}={value}")
    )
    cidfile = _control_cidfile(kwargs)
    if cidfile is None:
        raise SwebenchError("trusted scorer container ID file is unavailable")
    return (
        _nonblank(kwargs.get("docker_executable"), "docker_executable"),
        "run",
        "--pull=never",
        "--name",
        name,
        *label_args,
        "--cidfile",
        str(cidfile),
        "-i",
        "--platform",
        _SCORER_PLATFORM,
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "64",
        "--memory",
        "1073741824",
        "--cpus",
        "1.0",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=64m",  # noqa: S108 - explicit private tmpfs only
        "--user",
        "65532:65532",
        reference,
        "python",
        "-I",
        _SCORER_ENTRYPOINT_PATH,
    )


def _inspect_trusted_scorer_container(
    runtime: SwebenchRuntime,
    kwargs: Mapping[str, object],
    *,
    container_id: str,
    image_id: str,
    timeout: float,
) -> None:
    docker = _nonblank(kwargs.get("docker_executable"), "docker_executable")
    inspected = _run(
        runtime,
        (docker, "inspect", container_id),
        timeout=min(30.0, timeout),
        max_output_bytes=1024 * 1024,
    )
    try:
        entries = json.loads(inspected.stdout)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SwebenchError("trusted scorer container identity is invalid") from exc
    if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], Mapping):
        raise SwebenchError("trusted scorer container identity is ambiguous")
    evidence = entries[0]
    config = evidence.get("Config")
    host_config = evidence.get("HostConfig")
    state = evidence.get("State")
    mounts = evidence.get("Mounts")
    if not isinstance(config, Mapping) or not isinstance(host_config, Mapping):
        raise SwebenchError("trusted scorer container configuration is unavailable")
    if not isinstance(state, Mapping) or not isinstance(mounts, list):
        raise SwebenchError("trusted scorer container state is unavailable")
    _name, expected_labels = _control_container_labels(kwargs)
    labels = config.get("Labels")
    cap_drop = host_config.get("CapDrop")
    security_options = host_config.get("SecurityOpt")
    binds = host_config.get("Binds")
    volumes_from = host_config.get("VolumesFrom")
    if (
        evidence.get("Id") != container_id
        or evidence.get("Image") != image_id
        or config.get("Image") != kwargs.get("control_image_reference")
        or not isinstance(labels, Mapping)
        or any(labels.get(key) != value for key, value in expected_labels.items())
        or state.get("Running") is not True
        or config.get("User") != "65532:65532"
        or host_config.get("NetworkMode") != "none"
        or host_config.get("ReadonlyRootfs") is not True
        or host_config.get("Privileged") is not False
        or not isinstance(cap_drop, list)
        or "ALL" not in cap_drop
        or not isinstance(security_options, list)
        or not any(str(option).startswith("no-new-privileges") for option in security_options)
        or binds not in ([], None)
        or volumes_from not in ([], None)
        or any(
            not isinstance(mount, Mapping)
            or mount.get("Type") != "tmpfs"
            or mount.get("Destination") != "/tmp"  # noqa: S108 - fixed container tmpfs
            for mount in mounts
        )
    ):
        raise SwebenchError("trusted scorer container identity or isolation is invalid")


def _invoke_trusted_scorer(
    request: Mapping[str, object],
    *,
    prepared: _Prepared,
    runtime: SwebenchRuntime,
    task: _Task | _ScorerTestSpec,
    action: str,
    test_spec_sha256: str,
) -> Mapping[str, Any]:
    prepare_keys = {
        "schema_version",
        "action",
        "swebench_version",
        "instance_id",
        "test_spec",
    }
    score_keys = prepare_keys | {
        "test_spec_sha256",
        "eval_script_sha256",
        "patch",
        "patch_sha256",
        "grading_log_b64",
        "grading_log_sha256",
        "host_cleanup_confirmed",
    }
    if set(request) != (prepare_keys if action == "prepare" else score_keys):
        raise SwebenchError("trusted scorer request fields are invalid")
    if (
        request.get("schema_version") != 1
        or request.get("action") != action
        or request.get("swebench_version") != SWEBENCH_VERSION
        or request.get("instance_id") != task.instance_id
    ):
        raise SwebenchError("trusted scorer request identity is invalid")
    try:
        payload = _canonical(request) + b"\n"
    except (TypeError, ValueError) as exc:
        raise SwebenchError("trusted scorer request is not canonical JSON") from exc
    if len(payload) > _SCORER_REQUEST_LIMIT:
        raise SwebenchError("trusted scorer request exceeds its byte bound")

    profile = prepared.profile
    reference = cast(str, profile["control_image_reference"])
    digest = cast(str, profile["control_image_digest"])
    docker = str(runtime.docker_executable)
    timeout = float(cast(Mapping[str, Any], profile["resource_limits"])["timeout_seconds"])
    image_result = _run(
        runtime,
        (docker, "image", "inspect", reference),
        timeout=min(30.0, timeout),
        max_output_bytes=1024 * 1024,
    )
    try:
        images = json.loads(image_result.stdout)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SwebenchError("trusted scorer image identity is invalid") from exc
    if (
        not isinstance(images, list)
        or len(images) != 1
        or not isinstance(images[0], Mapping)
        or images[0].get("Id") != digest
        or images[0].get("Os") != "linux"
        or images[0].get("Architecture") not in {"arm64", "aarch64"}
        or images[0].get("Variant") not in {None, "v8"}
    ):
        raise SwebenchError("trusted scorer image does not match its frozen ARM64 identity")
    image_id = cast(str, images[0]["Id"])

    task_fingerprint = _sha256(
        _canonical(
            {
                "schema_version": 1,
                "purpose": "trusted-swebench-scorer",
                "action": action,
                "task_fingerprint_sha256": (
                    _task_fingerprint(task)
                    if isinstance(task, _Task)
                    else task.task_fingerprint_sha256
                ),
                "test_spec_sha256": test_spec_sha256,
            }
        )
    )
    nonce = _nonblank(runtime.nonce_factory(), "control attempt nonce")
    container_name, identity_sha256 = _control_container_identity(
        task_fingerprint_sha256=task_fingerprint,
        attempt_nonce=nonce,
        control_image_digest=digest,
    )
    evidence_dir = prepared.state / "swebench-scorer-control"
    try:
        if evidence_dir.exists() and (evidence_dir.is_symlink() or not evidence_dir.is_dir()):
            raise SwebenchError("trusted scorer evidence directory is invalid")
        evidence_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(evidence_dir, 0o700)
    except OSError as exc:
        raise SwebenchError("trusted scorer evidence directory is unavailable") from exc
    kwargs: dict[str, object] = {
        "docker_executable": docker,
        "control_image_reference": reference,
        "control_image_digest": digest,
        "task_fingerprint_sha256": task_fingerprint,
        "attempt_nonce": nonce,
        "control_container_name": container_name,
        "control_identity_sha256": identity_sha256,
        "control_id_required": True,
        "control_container_id": None,
        "evidence_dir": evidence_dir,
        "runner": runtime.runner or subprocess.run,
        "timeout_seconds": timeout,
        "max_output_bytes": 1024 * 1024,
    }
    cidfile = _control_cidfile(kwargs)
    if cidfile is None or cidfile.exists():
        raise SwebenchError("trusted scorer container ID file is unavailable or stale")
    process: subprocess.Popen[bytes] | None = None
    process_started = False
    try:
        args = _trusted_scorer_container_args(kwargs)
        try:
            process = subprocess.Popen(  # noqa: S603 - fixed argv, digest-pinned image
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            process_started = True
        except OSError as exc:
            raise SwebenchError("pinned trusted scorer image could not be started") from exc
        deadline = time.monotonic() + timeout
        container_id: str | None = None
        while container_id is None:
            try:
                raw_container_id = cidfile.read_text(encoding="ascii").strip()
            except FileNotFoundError:
                raw_container_id = ""
            except (OSError, UnicodeDecodeError) as exc:
                raise SwebenchError("trusted scorer container ID file is unreadable") from exc
            if raw_container_id:
                container_id = _validated_control_container_id(raw_container_id)
                if container_id is None:
                    raise SwebenchError("trusted scorer container ID is invalid")
                kwargs["control_container_id"] = container_id
                break
            if process.poll() is not None:
                raise SwebenchError("trusted scorer exited before container identity capture")
            remaining = _remaining_control_budget(deadline)
            time.sleep(min(0.01, remaining))
        _inspect_trusted_scorer_container(
            runtime,
            kwargs,
            container_id=container_id,
            image_id=image_id,
            timeout=_remaining_control_budget(deadline),
        )
        _rpc_reply(process, request, deadline=deadline)
        if process.stdin is not None:
            process.stdin.close()
        if process.stdout is None or process.stderr is None:
            raise SwebenchError("trusted scorer output streams are unavailable")
        stdout_fd = process.stdout.fileno()
        stderr_fd = process.stderr.fileno()
        selector = selectors.DefaultSelector()
        stdout_buffer = bytearray()
        stderr_bytes = 0
        try:
            selector.register(stdout_fd, selectors.EVENT_READ)
            selector.register(stderr_fd, selectors.EVENT_READ)
            line, stderr_bytes = _read_control_line(
                selector,
                stdout_fd,
                stderr_fd,
                stdout_buffer,
                deadline=deadline,
                stderr_bytes=stderr_bytes,
                stderr_limit=_SCORER_RESPONSE_LIMIT,
            )
            if line is None:
                raise SwebenchError("trusted scorer exited without a response")
            trailing, stderr_bytes = _read_control_line(
                selector,
                stdout_fd,
                stderr_fd,
                stdout_buffer,
                deadline=deadline,
                stderr_bytes=stderr_bytes,
                stderr_limit=_SCORER_RESPONSE_LIMIT,
            )
            if trailing is not None or stdout_buffer or stderr_bytes:
                raise SwebenchError("trusted scorer emitted extra or untrusted output")
        finally:
            selector.close()
        try:
            return_code = process.wait(timeout=_remaining_control_budget(deadline))
        except subprocess.TimeoutExpired as exc:
            raise SwebenchError("trusted scorer exceeded its process deadline") from exc
        if return_code != 0:
            raise SwebenchError("trusted scorer process failed")
        try:
            response = _mapping(json.loads(line), "trusted scorer response")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SwebenchError("trusted scorer response is malformed JSON") from exc
        return response
    finally:
        cleanup_error: SwebenchError | None = None
        if process_started and process is not None:
            try:
                _reap_control_process(process)
            except SwebenchError as exc:
                cleanup_error = exc
            try:
                _cleanup_control_container(kwargs)
            except SwebenchError as exc:
                cleanup_error = exc
        try:
            cidfile.unlink(missing_ok=True)
        except OSError:
            cleanup_error = SwebenchError("trusted scorer container ID file cleanup failed")
        if cleanup_error is not None:
            raise cleanup_error


def _read_control_line(
    selector: selectors.BaseSelector,
    stdout_fd: int,
    stderr_fd: int,
    stdout_buffer: bytearray,
    *,
    deadline: float,
    stderr_bytes: int,
    stderr_limit: int,
) -> tuple[bytes | None, int]:
    while True:
        remaining = _remaining_control_budget(deadline)
        newline = stdout_buffer.find(b"\n")
        if newline >= 0:
            line = bytes(stdout_buffer[:newline])
            del stdout_buffer[: newline + 1]
            return line, stderr_bytes
        events = selector.select(remaining)
        if not events:
            raise SwebenchError("control-plane RPC deadline exceeded")
        for key, _ in events:
            try:
                chunk = os.read(cast(int, key.fileobj), 65_536)
            except OSError as exc:
                raise SwebenchError("control-plane stream read failed") from exc
            if not chunk:
                try:
                    selector.unregister(key.fileobj)
                except (KeyError, ValueError):
                    pass
                if cast(int, key.fileobj) == stdout_fd:
                    if stdout_buffer:
                        raise SwebenchError("control-plane RPC ended with an incomplete message")
                    return None, stderr_bytes
                continue
            if cast(int, key.fileobj) == stderr_fd:
                stderr_bytes += len(chunk)
                if stderr_bytes > stderr_limit:
                    raise SwebenchError("control-plane stderr exceeded its bound")
                continue
            stdout_buffer.extend(chunk)
            if len(stdout_buffer) > _RPC_STDOUT_LIMIT:
                raise SwebenchError("control-plane RPC output exceeded its bound")


def _checkpoint_control_fields(
    *,
    task_fingerprint_sha256: object,
    attempt_nonce: object,
    control_image_digest: object,
    control_container_name: object,
    control_identity_sha256: object,
    control_container_id: object,
) -> dict[str, str | None]:
    values = (
        control_image_digest,
        control_container_name,
        control_identity_sha256,
        control_container_id,
    )
    if all(value is None for value in values):
        return {
            "control_image_digest": None,
            "control_container_name": None,
            "control_identity_sha256": None,
            "control_container_id": None,
        }
    image_digest = _nonblank(control_image_digest, "control image digest")
    if _DIGEST_RE.fullmatch(image_digest) is None:
        raise SwebenchError("control image digest is invalid")
    name = _nonblank(control_container_name, "control container name")
    identity = _nonblank(control_identity_sha256, "control identity")
    expected_name, expected_identity = _control_container_identity(
        task_fingerprint_sha256=task_fingerprint_sha256,
        attempt_nonce=attempt_nonce,
        control_image_digest=image_digest,
    )
    if name != expected_name or identity != expected_identity:
        raise SwebenchError("checkpoint control identity does not match the task attempt")
    container_id = _validated_control_container_id(control_container_id)
    return {
        "control_image_digest": image_digest,
        "control_container_name": name,
        "control_identity_sha256": identity,
        "control_container_id": container_id,
    }


def _default_agent_executor(**kwargs: object) -> Mapping[str, object]:
    wall_limit = cast(float, kwargs["timeout_seconds"])
    # The agent's own wall limit must expire first so it can return TimeExceeded as a
    # model outcome; the host bound covers one in-flight generation plus teardown.
    deadline = time.monotonic() + wall_limit + min(_CONTROL_DEADLINE_MARGIN_SECONDS, wall_limit / 2)
    process: subprocess.Popen[bytes] | None = None
    process_started = False
    control_args = _control_plane_args(kwargs)
    id_callback = kwargs.get("on_control_container_id")
    kwargs["control_id_required"] = callable(id_callback) and "--cidfile" in control_args
    try:
        process = subprocess.Popen(  # noqa: S603 - fixed Docker argv and digest-pinned image
            control_args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        process_started = True
    except OSError as exc:
        raise SwebenchError("pinned SWE-bench control-plane image is unavailable") from exc
    try:
        if callable(id_callback) and "--cidfile" in control_args:
            cidfile = _control_cidfile(kwargs)
            if cidfile is None:
                raise SwebenchError("control container ID file is unavailable")
            container_id: str | None = None
            while container_id is None:
                try:
                    raw_cid = cidfile.read_text(encoding="ascii").strip()
                except FileNotFoundError:
                    raw_cid = ""
                except (OSError, UnicodeDecodeError) as exc:
                    raise SwebenchError("control container ID file is unreadable") from exc
                if raw_cid:
                    container_id = _validated_control_container_id(raw_cid)
                    kwargs["control_container_id"] = container_id
                    kwargs["control_id_required"] = True
                    id_callback(container_id)
                    break
                remaining = _remaining_control_budget(deadline)
                time.sleep(min(0.01, remaining))
        return _control_plane_exchange(cast(Any, process), kwargs, deadline=deadline)
    finally:
        reap_error: SwebenchError | None = None
        cleanup_error: SwebenchError | None = None
        if process_started:
            try:
                _reap_control_process(cast(Any, process))
            except SwebenchError as exc:
                reap_error = exc
            try:
                _cleanup_control_container(kwargs)
            except SwebenchError as exc:
                cleanup_error = exc
        if cleanup_error is not None:
            raise cleanup_error
        if reap_error is not None:
            raise reap_error


def _control_plane_exchange(
    process: subprocess.Popen[bytes],
    kwargs: Mapping[str, object],
    *,
    deadline: float,
) -> Mapping[str, object]:
    if process.stdin is None or process.stdout is None or process.stderr is None:
        raise SwebenchError("control-plane streams are unavailable")
    try:
        stdout_fd = process.stdout.fileno()
        stderr_fd = process.stderr.fileno()
    except (AttributeError, OSError, ValueError) as exc:
        raise SwebenchError("control-plane streams are unavailable") from exc
    selector = selectors.DefaultSelector()
    try:
        selector.register(stdout_fd, selectors.EVENT_READ)
        selector.register(stderr_fd, selectors.EVENT_READ)
        return _control_plane_exchange_loop(
            process,
            kwargs,
            selector=selector,
            stdout_fd=stdout_fd,
            stderr_fd=stderr_fd,
            deadline=deadline,
        )
    finally:
        selector.close()


def _control_plane_exchange_loop(
    process: subprocess.Popen[bytes],
    kwargs: Mapping[str, object],
    *,
    selector: selectors.BaseSelector,
    stdout_fd: int,
    stderr_fd: int,
    deadline: float,
) -> Mapping[str, object]:
    evidence_dir = Path(cast(str | os.PathLike[str], kwargs["evidence_dir"]))
    request = dict(kwargs)
    request.pop("runner", None)
    request.pop("evidence_dir", None)
    request.pop("config_path", None)
    request.pop("on_verified_response", None)
    request.pop("on_control_container_id", None)
    request.pop("control_id_required", None)
    request.pop("control_container_id", None)
    request.pop("sdk_checkpoint", None)
    host_runner = cast(_Runner, kwargs["runner"])
    _rpc_reply(process, {"op": "start", "request": request}, deadline=deadline)
    first_response_receipt: dict[str, Any] | None = None
    trajectory_budget_exhausted = False
    callback = kwargs.get("on_verified_response")
    if callback is not None and not callable(callback):
        raise SwebenchError("host response callback is invalid")
    on_verified_response = (
        cast(Callable[[Mapping[str, Any]], None], callback) if callback is not None else None
    )
    stdout_buffer = bytearray()
    stderr_bytes = 0
    while True:
        line, stderr_bytes = _read_control_line(
            selector,
            stdout_fd,
            stderr_fd,
            stdout_buffer,
            deadline=deadline,
            stderr_bytes=stderr_bytes,
            stderr_limit=cast(int, kwargs["max_output_bytes"]),
        )
        if line is None:
            raise SwebenchError("control-plane exited before producing evidence")
        try:
            message = _mapping(json.loads(line), "control-plane RPC")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SwebenchError("control-plane RPC is invalid") from exc
        operation = message.get("op")
        if operation == "lm_models":
            _rpc_reply(
                process,
                {
                    "ok": True,
                    "value": _http_json(
                        cast(str, kwargs["server_origin"]),
                        "/api/v1/models",
                        timeout=_remaining_control_budget(deadline),
                        deadline=deadline,
                    ),
                },
                deadline=deadline,
            )
        elif operation == "lm_chat":
            body = _mapping(message.get("body"), "LM Studio RPC body")
            try:
                response, receipt = _host_verified_chat_response(
                    cast(str, kwargs["server_origin"]),
                    body,
                    expected_model=cast(str, kwargs["model"]),
                    expected_task_fingerprint=cast(str, kwargs["task_fingerprint_sha256"]),
                    task_instance_id=cast(str, kwargs["instance_id"]),
                    expected_model_instance_id=(
                        cast(str, kwargs["model_instance_id"])
                        if kwargs.get("model_instance_id") is not None
                        else None
                    ),
                    expected_served_model_fingerprint=cast(str, kwargs["served_model_fingerprint"]),
                    on_verified_response=on_verified_response,
                    deadline=deadline,
                    sdk_checkpoint=cast(Path | None, kwargs.get("sdk_checkpoint")),
                    sdk_output_limit=cast(int, kwargs.get("max_task_output_tokens", 0)),
                    sdk_request_limit=cast(int, kwargs.get("max_requests", 0)),
                )
            except _SdkTrajectoryBudgetExhausted:
                # The prior request finished. Let the unchanged runner save its trajectory
                # and return its existing terminal model_failure, then clean up normally.
                trajectory_budget_exhausted = True
                _rpc_reply(
                    process,
                    {"ok": False, "error": "trajectory_budget_exhausted"},
                    deadline=deadline,
                )
                continue
            if first_response_receipt is None:
                first_response_receipt = receipt
            _rpc_reply(process, {"ok": True, "value": response}, deadline=deadline)
        elif operation in {"task_json", "task_exec", "task_diff"}:
            if trajectory_budget_exhausted:
                raise SwebenchError("exhausted trajectory may not execute another command")
            command: tuple[str, ...]
            if operation == "task_json":
                command = (
                    cast(str, kwargs["docker_executable"]),
                    "exec",
                    cast(str, kwargs["container_name"]),
                    "cat",
                    "/opt/racecraft/task.json",
                )
            elif operation == "task_diff":
                command = (
                    cast(str, kwargs["docker_executable"]),
                    "exec",
                    "-w",
                    "/testbed",
                    cast(str, kwargs["container_name"]),
                    "git",
                    "diff",
                    "--binary",
                    "HEAD",
                )
            else:
                command_text = _nonblank(message.get("command"), "task command")
                # Match pinned upstream DockerEnvironment: image PATH, its env block,
                # `bash -c`, and stderr merged into the observation. The in-container
                # timeout kills the command's process group and exits 124.
                command = (
                    cast(str, kwargs["docker_executable"]),
                    "exec",
                    "-w",
                    "/testbed",
                    *(item for pair in _TASK_EXEC_ENV for item in ("-e", pair)),
                    cast(str, kwargs["container_name"]),
                    "timeout",
                    "-k",
                    "5",
                    str(_TASK_EXEC_TIMEOUT_SECONDS),
                    "bash",
                    "-c",
                    "exec 2>&1\n" + command_text,
                )
            completed = host_runner(
                command,
                capture_output=True,
                text=False,
                check=False,
                timeout=min(_TASK_EXEC_TIMEOUT_SECONDS + 30.0, _remaining_control_budget(deadline)),
            )
            _remaining_control_budget(deadline)
            output = (
                completed.stdout
                if isinstance(completed.stdout, bytes)
                else str(completed.stdout).encode()
            )
            _rpc_reply(
                process,
                {
                    "ok": True,
                    "returncode": completed.returncode,
                    "output_b64": base64.b64encode(
                        output[: cast(int, kwargs["max_output_bytes"])]
                    ).decode(),
                },
                deadline=deadline,
            )
        elif operation == "write_evidence":
            filename = _nonblank(message.get("filename"), "evidence filename")
            if (
                re.fullmatch(r"(?:trajectory|prediction)-[0-9a-f]{64}\.(?:json|patch)", filename)
                is None
            ):
                raise SwebenchError("control-plane evidence filename is invalid")
            try:
                data = base64.b64decode(
                    _nonblank(message.get("data_b64"), "evidence bytes"), validate=True
                )
            except ValueError as exc:
                raise SwebenchError("control-plane evidence bytes are invalid") from exc
            if _sha256(data) != filename.split("-", 1)[1].split(".", 1)[0]:
                raise SwebenchError("control-plane evidence bytes do not match their name")
            if filename.endswith(".json"):
                try:
                    json.loads(data)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise SwebenchError("control-plane trajectory is not JSON") from exc
            _remaining_control_budget(deadline)
            # Keep the runner's exact bytes so the file name stays their SHA-256.
            _write_private_bytes(evidence_dir / filename, data)
            _remaining_control_budget(deadline)
            _rpc_reply(process, {"ok": True}, deadline=deadline)
        elif operation == "result":
            result = _mapping(message.get("value"), "mini-SWE-agent result")
            if trajectory_budget_exhausted and result.get("status") != "model_failure":
                raise SwebenchError("exhausted trajectory must be a terminal model failure")
            if result.get("host_response_receipt") is not None:
                raise SwebenchError("control-plane result may not supply a host response receipt")
            result = dict(result)
            result["host_response_receipt"] = (
                dict(first_response_receipt) if first_response_receipt is not None else None
            )
            return result
        else:
            raise SwebenchError("control-plane requested an unsupported operation")


@dataclass(frozen=True)
class _BoundedProcessCapture:
    stdout: bytes
    stderr: bytes
    stdout_bytes: int
    stderr_bytes: int
    launcher_exit_code: int | None
    timed_out: bool
    output_exceeded: bool


def _run_bounded_subprocess(
    args: Sequence[str],
    *,
    input_bytes: bytes,
    timeout: float,
    max_output_bytes: int,
) -> _BoundedProcessCapture:
    """Stream child pipes, retain at most the output budget, and kill/reap on overflow."""
    if timeout <= 0 or max_output_bytes <= 0:
        raise SwebenchError("grader process bounds are invalid")
    try:
        process = subprocess.Popen(  # noqa: S603 - fixed Docker argv, no shell, pinned image.
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        raise SwebenchError("grader launcher could not start") from exc
    if process.stdin is None or process.stdout is None or process.stderr is None:
        if not _reap_child_process(process):
            raise SwebenchError("grader launcher could not be reaped")
        raise SwebenchError("grader launcher pipes are unavailable")

    stdout_buffer = bytearray()
    stderr_buffer = bytearray()
    stdout_bytes = 0
    stderr_bytes = 0
    input_offset = 0
    timed_out = False
    output_exceeded = False
    deadline = time.monotonic() + timeout
    selector = selectors.DefaultSelector()
    try:
        for stream in (process.stdout, process.stderr):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)
        os.set_blocking(process.stdin.fileno(), False)
        if input_bytes:
            selector.register(process.stdin, selectors.EVENT_WRITE)
        else:
            process.stdin.close()

        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            events = selector.select(remaining)
            for key, _ in events:
                stream = cast(Any, key.fileobj)
                if stream is process.stdin:
                    try:
                        input_offset += os.write(
                            stream.fileno(), input_bytes[input_offset : input_offset + 65_536]
                        )
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        input_offset = len(input_bytes)
                    if input_offset >= len(input_bytes):
                        selector.unregister(stream)
                        stream.close()
                    continue

                chunk = os.read(stream.fileno(), 65_536)
                if not chunk:
                    selector.unregister(stream)
                    stream.close()
                    continue
                if stream is process.stdout:
                    stdout_bytes += len(chunk)
                    destination = stdout_buffer
                else:
                    stderr_bytes += len(chunk)
                    destination = stderr_buffer
                remaining_output = max_output_bytes - len(stdout_buffer) - len(stderr_buffer)
                destination.extend(chunk[: max(0, remaining_output)])
                if len(chunk) > max(0, remaining_output):
                    output_exceeded = True
                    break
            if output_exceeded:
                break

        if not timed_out and not output_exceeded:
            try:
                process.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                timed_out = True
    finally:
        selector.close()
        if not _reap_child_process(process):
            raise SwebenchError("grader launcher could not be reaped")

    return _BoundedProcessCapture(
        stdout=bytes(stdout_buffer),
        stderr=bytes(stderr_buffer),
        stdout_bytes=stdout_bytes,
        stderr_bytes=stderr_bytes,
        launcher_exit_code=process.returncode,
        timed_out=timed_out,
        output_exceeded=output_exceeded,
    )


def _default_grader_executor(**kwargs: object) -> Mapping[str, object]:
    evidence_dir = Path(cast(str | os.PathLike[str], kwargs["evidence_dir"]))
    handle = _nonblank(kwargs.get("prediction_handle"), "prediction_handle")
    match = re.fullmatch(r"private://(prediction-[0-9a-f]{64}\.patch)", handle)
    if match is None:
        raise SwebenchError("prediction evidence handle is invalid")
    prediction = secure_resolve(evidence_dir / match.group(1), must_exist=True)
    if prediction.parent != secure_resolve(evidence_dir, must_exist=True):
        raise SwebenchError("prediction evidence escaped its private directory")
    patch = prediction.read_bytes()
    if _sha256(patch) != kwargs.get("model_patch_sha256"):
        raise SwebenchError("prediction evidence digest drifted")
    try:
        patch.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise SwebenchError("prediction patch is not UTF-8") from exc
    eval_script = kwargs.get("test_spec_eval_script")
    if not isinstance(eval_script, str) or not eval_script:
        raise SwebenchError("trusted official eval script is unavailable")
    eval_script_bytes = eval_script.encode("utf-8")
    patch_sha256 = _sha256(patch)
    eval_script_sha256 = _sha256(eval_script_bytes)
    if kwargs.get("test_spec_eval_script_sha256") != eval_script_sha256:
        raise SwebenchError("trusted official eval script fingerprint drifted")
    maximum = kwargs.get("max_output_bytes")
    if (
        isinstance(maximum, bool)
        or not isinstance(maximum, int)
        or maximum <= 0
        or maximum > 64 * 1024 * 1024
    ):
        raise SwebenchError("grader output limit is invalid")
    test_output_limit = min(maximum, 4 * 1024 * 1024 - 1024)
    timeout_seconds = kwargs.get("timeout_seconds")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or timeout_seconds <= 0
    ):
        raise SwebenchError("grader timeout is invalid")
    payload = (
        _canonical(
            {
                "schema_version": 1,
                "patch_b64": base64.b64encode(patch).decode("ascii"),
                "eval_script_b64": base64.b64encode(eval_script_bytes).decode("ascii"),
                "expected_patch_sha256": patch_sha256,
                "expected_eval_script_sha256": eval_script_sha256,
                "timeout_seconds": max(1, min(math.ceil(timeout_seconds), 90_000)),
                "max_output_bytes": test_output_limit,
            }
        )
        + b"\n"
    )
    docker = cast(str, kwargs["docker_executable"])
    container = cast(str, kwargs["container_name"])
    runner = cast(_Runner, kwargs["runner"])
    launcher_limit = test_output_limit * 2 + 64 * 1024

    def run_cli(
        command: Sequence[str], *, input_bytes: bytes, timeout: float, output_limit: int
    ) -> tuple[int | None, bool, bytes, bytes, bool]:
        if runner is subprocess.run:
            completed = _run_bounded_subprocess(
                command,
                input_bytes=input_bytes,
                timeout=timeout,
                max_output_bytes=output_limit,
            )
            return (
                completed.launcher_exit_code,
                completed.timed_out,
                completed.stdout,
                completed.stderr,
                completed.output_exceeded,
            )
        try:
            process = runner(
                command,
                input=input_bytes,
                capture_output=True,
                text=False,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:

            def timeout_bytes(value: object) -> bytes:
                if value is None:
                    return b""
                if isinstance(value, bytes):
                    return value
                if isinstance(value, str):
                    return value.encode("utf-8", errors="replace")
                raise SwebenchError("grader output has an unsupported type")

            return (
                None,
                True,
                timeout_bytes(exc.stdout or exc.output),
                timeout_bytes(exc.stderr),
                False,
            )

        def output_bytes(value: object) -> bytes:
            if value is None:
                return b""
            if isinstance(value, bytes):
                return value
            if isinstance(value, str):
                return value.encode("utf-8", errors="replace")
            raise SwebenchError("grader output has an unsupported type")

        stdout_bytes = output_bytes(process.stdout)
        stderr_bytes = output_bytes(process.stderr)
        exceeded = len(stdout_bytes) + len(stderr_bytes) > output_limit
        return (
            process.returncode,
            False,
            stdout_bytes[:output_limit],
            stderr_bytes[: max(0, output_limit - min(len(stdout_bytes), output_limit))],
            exceeded,
        )

    apply_command = (docker, "exec", "-i", container, "/usr/local/bin/racecraft-swebench-grade")
    apply_exit, apply_timeout, apply_stdout, apply_stderr, apply_overflow = run_cli(
        apply_command,
        input_bytes=payload,
        timeout=float(timeout_seconds),
        output_limit=launcher_limit,
    )
    phase = "apply_failed"
    process_exit_code = apply_exit
    eval_launcher_exit_code: int | None = None
    eval_exit_code: int | None = None
    timed_out = apply_timeout
    stdout = b""
    stderr = b""
    error: str | None = None
    response_valid = False
    if apply_timeout:
        error = "apply_timeout"
        stdout, stderr = apply_stdout, apply_stderr
    elif apply_overflow:
        error = "apply_output_limit"
        stdout, stderr = apply_stdout, apply_stderr
    elif apply_stderr:
        error = "docker_client_stderr"
        stdout, stderr = apply_stdout, apply_stderr
    elif apply_exit != 0:
        error = "apply_launcher_exit"
        stdout, stderr = apply_stdout, apply_stderr
        if apply_exit == _GRADER_EXIT_PATCH_FAILED and _is_patch_failure_response(
            apply_stdout, patch_sha256=patch_sha256, eval_script_sha256=eval_script_sha256
        ):
            error = _GRADER_PATCH_FAILED
    else:
        try:
            response = _mapping(json.loads(apply_stdout), "grader entrypoint response")
            _exact_keys(
                response,
                _GRADER_ENTRYPOINT_RESPONSE_KEYS,
                "grader entrypoint response",
            )
            response_exit = response["process_exit_code"]
            response_timeout = response["timed_out"]
            response_error = response["error"]
            if response_error is not None and (
                not isinstance(response_error, str)
                or re.fullmatch(r"[a-z0-9_]{1,64}", response_error) is None
            ):
                raise SwebenchError("grader entrypoint error code is invalid")
            if (
                response["schema_version"] != 1
                or response["phase"] != "patch_ready"
                or type(response_exit) is not int
                or response_exit != 0
                or response["eval_exit_code"] is not None
                or response_timeout is not False
                or response_error is not None
                or apply_exit != response_exit
                or response["patch_sha256"] != patch_sha256
                or response["eval_script_sha256"] != eval_script_sha256
            ):
                raise SwebenchError("grader entrypoint did not attest patch readiness")
            setup_stdout_b64 = response["stdout_b64"]
            setup_stderr_b64 = response["stderr_b64"]
            if not isinstance(setup_stdout_b64, str) or not isinstance(setup_stderr_b64, str):
                raise SwebenchError("grader entrypoint streams are invalid")
            setup_stdout = base64.b64decode(setup_stdout_b64, validate=True)
            setup_stderr = base64.b64decode(setup_stderr_b64, validate=True)
            if len(setup_stdout) + len(setup_stderr) > test_output_limit:
                raise SwebenchError("grader entrypoint exceeded its setup output limit")
            response_valid = True
            phase = "patch_ready"
            process_exit_code = response_exit
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, SwebenchError):
            error = "malformed_entrypoint_response"
            stdout, stderr = apply_stdout, apply_stderr

    if response_valid:
        eval_command = (
            docker,
            "exec",
            "-i",
            "--workdir",
            "/testbed",
            "--user",
            "65532:65532",
            container,
            "/bin/bash",
            "-c",
            "exec /bin/bash /opt/racecraft/grader/test-spec-eval.sh 2>&1",
        )
        eval_launcher_exit_code, eval_timeout, stdout, stderr, eval_overflow = run_cli(
            eval_command,
            input_bytes=b"",
            timeout=float(timeout_seconds),
            output_limit=test_output_limit,
        )
        timed_out = eval_timeout
        if eval_timeout:
            # A killed launcher's status is not an eval exit receipt.
            eval_launcher_exit_code = None
            error = "eval_timeout"
        elif eval_overflow:
            error = "eval_output_limit"
        elif stderr:
            error = "docker_client_stderr"
        elif (
            eval_launcher_exit_code is None
            or type(eval_launcher_exit_code) is not int
            or not 0 <= eval_launcher_exit_code <= 255
            or eval_launcher_exit_code in {125, 126, 127}
        ):
            error = "ambiguous_eval_exit"
        elif _TEST_EXIT_CODE_RE.search(stdout) is None:
            error = "incomplete_official_log"
        else:
            # Docker CLI provides no independent ExecInspect receipt. Accept its
            # child status only when transport is clean and the complete bounded
            # output stream was observed; any ambiguity stays non-scoring.
            eval_exit_code = eval_launcher_exit_code

    capture: dict[str, object] = {
        "schema_version": 1,
        "phase": phase,
        "launcher_exit_code": apply_exit,
        "process_exit_code": process_exit_code,
        "eval_launcher_exit_code": eval_launcher_exit_code,
        "eval_exit_code": eval_exit_code,
        "timed_out": timed_out,
        "patch_sha256": patch_sha256,
        "eval_script_sha256": eval_script_sha256,
        "stdout": stdout,
        "stderr": stderr,
        "error": error,
    }
    if set(capture) != _GRADER_CAPTURE_KEYS:
        raise SwebenchError("grader capture fields are invalid")
    return capture


@dataclass(frozen=True)
class SwebenchRuntime:
    """Production defaults with injectable seams for hermetic tests."""

    docker_executable: Path = Path("/usr/local/bin/docker")
    runner: _Runner | None = None
    parameter_probe: ParameterProbe | None = _default_parameter_probe
    adapter_probe: AdapterProbe | None = _default_adapter_probe
    agent_executor: AgentExecutor | None = _default_agent_executor
    grader_executor: GraderExecutor | None = _default_grader_executor
    test_spec_factory: TestSpecFactory | None = None
    score_executor: ScoreExecutor | None = None
    scorer_attestation: _ScorerAttestation | None = None
    nonce_factory: Callable[[], str] = lambda: secrets.token_hex(8)
    clock: Callable[[], float] = time.monotonic


@dataclass(frozen=True)
class _Task:
    instance_id: str
    base_commit: str
    repository: str
    problem_statement_sha256: str
    record_sha256: str
    task_image_reference: str = ""
    task_image_digest: str = ""
    grader_image_reference: str = ""
    grader_image_digest: str = ""
    binding_sha256: str = ""
    host_test_spec_path: str = ""
    host_test_spec_sha256: str = ""
    execution_eval_script_sha256: str = ""


def _task_fingerprint(task: _Task) -> str:
    return _sha256(
        _canonical(
            {
                "instance_id": task.instance_id,
                "base_commit": task.base_commit,
                "repository": task.repository,
                "problem_statement_sha256": task.problem_statement_sha256,
                "record_sha256": task.record_sha256,
                "host_test_spec_sha256": task.host_test_spec_sha256,
            }
        )
    )


@dataclass(frozen=True)
class _Prepared:
    profile: Mapping[str, Any]
    repo: Path
    state: Path
    server_origin: str
    manifest_sha256: str
    image_bindings_sha256: str
    dataset_id: str
    dataset_revision: str
    protocol_fingerprint: str
    tasks: tuple[_Task, ...]
    runner_config_sha256: str
    blockers: tuple[str, ...]
    scorer_test_specs: Mapping[str, _ScorerTestSpec] = field(default_factory=dict)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SwebenchError(f"{label} must be a mapping")
    return cast(Mapping[str, Any], value)


def _exact_keys(value: Mapping[str, Any], keys: set[str], label: str) -> None:
    if set(value) != keys:
        raise SwebenchError(f"{label} has missing or unsupported fields")


def _nonblank(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SwebenchError(f"{label} must be nonblank")
    return value


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _validated_host_response_receipt(
    receipt: object,
    *,
    expected_task_instance_id_sha256: str | None = None,
    expected_model_instance_id_sha256: str | None = None,
    expected_task_fingerprint: str | None = None,
    expected_served_model_fingerprint: str | None = None,
) -> dict[str, Any]:
    mapping = _mapping(receipt, "host response receipt")
    _exact_keys(mapping, _HOST_RESPONSE_RECEIPT_KEYS, "host response receipt")
    if mapping.get("schema_version") != 1 or mapping.get("response_schema") != (
        "openai_chat_completion"
    ):
        raise SwebenchError("host response receipt schema is invalid")
    for key in (
        "task_instance_id_sha256",
        "model_instance_id_sha256",
        "task_fingerprint_sha256",
        "served_model_fingerprint",
        "request_sha256",
        "response_sha256",
    ):
        if not _is_sha256(mapping.get(key)):
            raise SwebenchError("host response receipt fingerprint is invalid")
    if (
        expected_task_instance_id_sha256 is not None
        and mapping["task_instance_id_sha256"] != expected_task_instance_id_sha256
    ):
        raise SwebenchError("host response receipt task instance binding drifted")
    if (
        expected_model_instance_id_sha256 is not None
        and mapping["model_instance_id_sha256"] != expected_model_instance_id_sha256
    ):
        raise SwebenchError("host response receipt model instance binding drifted")
    if (
        expected_task_fingerprint is not None
        and mapping["task_fingerprint_sha256"] != expected_task_fingerprint
    ):
        raise SwebenchError("host response receipt task binding drifted")
    if (
        expected_served_model_fingerprint is not None
        and mapping["served_model_fingerprint"] != expected_served_model_fingerprint
    ):
        raise SwebenchError("host response receipt model binding drifted")
    return dict(mapping)


def _expected_selection_policy(mode: object, dataset_id: str) -> dict[str, object]:
    if mode == "qualification":
        return {
            "schema_version": 1,
            "algorithm": "sha256_utf8_seed_nul_instance_id",
            "seed": _QUALIFICATION_SELECTION_SEED,
            "candidate_dataset_id": dataset_id,
            "candidate_split": "test",
            "exclusion": "swebench_verified_500_and_prior_qualification_instance_ids",
            "prior_qualification_manifest_sha256": (
                "099402f8f572ff4563a92281f953f6c7c5a1056a2e14f96fe914a6f9333d2145"
            ),
            "prior_qualification_exclusions_sha256": (
                "55b036b1f9f85eab8d88529a0a6100afb0fc4064a2beed08498d7e59b598066d"
            ),
            "rank_tiebreaker": "instance_id_ascending",
            "output_order": "instance_id_ascending",
            "evidence_class": "qualification_only_non_capability",
        }
    return {
        "schema_version": 1,
        "algorithm": "full_test_split_source_order",
        "candidate_dataset_id": dataset_id,
        "candidate_split": "test",
        "output_order": "source_order",
        "evidence_class": "held_out_capability",
    }


def _validate_image(reference: object, digest: object, label: str) -> tuple[str, str]:
    checked_reference = _nonblank(reference, f"{label}_reference")
    checked_digest = _nonblank(digest, f"{label}_digest")
    if _DIGEST_RE.fullmatch(checked_digest) is None:
        raise SwebenchError(f"{label}_digest must be an exact sha256 digest")
    if not checked_reference.endswith(f"@{checked_digest}") or checked_reference.count("@") != 1:
        raise SwebenchError(f"{label}_reference must bind the exact digest")
    if any(character.isspace() for character in checked_reference):
        raise SwebenchError(f"{label}_reference must not contain whitespace")
    return checked_reference, checked_digest


def _validate_origin(value: object) -> str:
    origin = _nonblank(value, "server_origin")
    try:
        parsed = urlparse(origin)
        _ = parsed.port
    except ValueError as exc:
        raise SwebenchError("server_origin is invalid") from exc
    forbidden_authority = parsed.username is not None or parsed.password is not None
    checks = (
        (parsed.scheme == "http", "server_origin must use HTTP"),
        (
            parsed.hostname in {"127.0.0.1", "::1", "localhost"},
            "server_origin must be loopback-only",
        ),
        (
            not forbidden_authority and not parsed.query and not parsed.fragment,
            "server_origin must not contain credentials, query, or fragment",
        ),
        (parsed.path.rstrip("/") == "/v1", "server_origin must end at the exact /v1 API root"),
    )
    for accepted, message in checks:
        if not accepted:
            raise SwebenchError(message)
    return parsed._replace(path="/v1", params="", query="", fragment="").geturl()


def _validate_limits(value: object) -> Mapping[str, Any]:
    limits = _mapping(value, "resource_limits")
    _exact_keys(limits, _LIMIT_KEYS, "resource_limits")
    for key in _LIMIT_KEYS - {"cpus"}:
        item = limits[key]
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            raise SwebenchError(f"resource_limits.{key} must be a positive integer")
    cpus = limits["cpus"]
    if not isinstance(cpus, str) or re.fullmatch(r"[1-9][0-9]*(?:\.[0-9]+)?", cpus) is None:
        raise SwebenchError("resource_limits.cpus must be a positive decimal string")
    if cast(int, limits["timeout_seconds"]) > 3600:
        raise SwebenchError("resource_limits.timeout_seconds exceeds the per-task bound")
    if cast(int, limits["max_turns"]) > 100 or cast(int, limits["max_requests"]) > 100:
        raise SwebenchError("resource_limits turn or request count exceeds the bound")
    return limits


def _private_path(path: Path, state: Path, label: str) -> Path:
    try:
        resolved = secure_resolve(path, must_exist=True)
        resolved.relative_to(state)
    except (ConfigurationError, ValueError) as exc:
        raise SwebenchError(f"{label} must be an existing private-state file") from exc
    try:
        mode = resolved.lstat().st_mode
    except OSError as exc:
        raise SwebenchError(f"{label} is unavailable") from exc
    if not stat.S_ISREG(mode):
        raise SwebenchError(f"{label} must be a regular file")
    return resolved


def _canonical_records(
    manifest: Mapping[str, Any], profile: Mapping[str, Any], state: Path
) -> tuple[Mapping[str, Any], ...]:
    mode = cast(str, profile["mode"])
    expected_relative = Path("swebench") / mode / "records.jsonl"
    records_value = _nonblank(manifest["records_path"], "manifest.records_path")
    records_relative = Path(records_value)
    if records_relative.is_absolute() or records_relative != expected_relative:
        raise SwebenchError("manifest records path is not canonical private state")
    records_path = _private_path(state / records_relative, state, "records")
    try:
        records_raw = records_path.read_bytes()
    except OSError as exc:
        raise SwebenchError("canonical records are unavailable") from exc
    if (
        not _is_sha256(manifest["records_sha256"])
        or _sha256(records_raw) != manifest["records_sha256"]
    ):
        raise SwebenchError("canonical records fingerprint does not match frozen bytes")
    records: list[Mapping[str, Any]] = []
    for raw_line in records_raw.splitlines():
        if not raw_line:
            raise SwebenchError("canonical records must not contain blank lines")
        try:
            record = _mapping(json.loads(raw_line), "canonical record")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SwebenchError("canonical records are invalid JSONL") from exc
        if not _RECORD_FIELDS.issubset(record):
            raise SwebenchError("canonical record is missing required grader fields")
        if _canonical(record) != raw_line:
            raise SwebenchError("canonical records are not deterministically serialized")
        instance_id = record["instance_id"]
        if not isinstance(instance_id, str) or _INSTANCE_RE.fullmatch(instance_id) is None:
            raise SwebenchError("canonical record instance ID is unsafe")
        if not isinstance(record["repo"], str) or not record["repo"].strip():
            raise SwebenchError("canonical record repository is invalid")
        if (
            not isinstance(record["base_commit"], str)
            or _REVISION_RE.fullmatch(record["base_commit"]) is None
        ):
            raise SwebenchError("canonical record base commit is invalid")
        if (
            not isinstance(record["problem_statement"], str)
            or not record["problem_statement"].strip()
        ):
            raise SwebenchError("canonical record problem statement is invalid")
        for patch_field in ("patch", "test_patch"):
            if not isinstance(record[patch_field], str):
                raise SwebenchError("canonical record patch fields are invalid")
        if not isinstance(record["version"], str) or not record["version"].strip():
            raise SwebenchError("canonical record version is invalid")
        for test_list_field in ("FAIL_TO_PASS", "PASS_TO_PASS"):
            value = record[test_list_field]
            if not (
                isinstance(value, str)
                or (isinstance(value, list) and all(isinstance(item, str) for item in value))
            ):
                raise SwebenchError("canonical record grader tests are invalid")
        if (
            not isinstance(record["environment_setup_commit"], str)
            or _REVISION_RE.fullmatch(record["environment_setup_commit"]) is None
        ):
            raise SwebenchError("canonical record environment commit is invalid")
        records.append(record)
    if not records:
        raise SwebenchError("canonical records must be nonempty")
    record_ids = [cast(str, record["instance_id"]) for record in records]
    if len(record_ids) != len(set(record_ids)):
        raise SwebenchError("canonical record instance IDs must be unique")
    return tuple(records)


def _validate_manifest(
    profile: Mapping[str, Any], state: Path
) -> tuple[tuple[_Task, ...], str, str, str]:
    configured_path = Path(_nonblank(profile["manifest_path"], "manifest_path"))
    if not configured_path.is_absolute():
        configured_path = state / configured_path
    manifest_path = _private_path(configured_path, state, "manifest")
    try:
        raw = manifest_path.read_bytes()
        manifest = _mapping(json.loads(raw), "manifest")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SwebenchError("manifest is unreadable or invalid JSON") from exc
    expected_sha = profile["manifest_sha256"]
    if not _is_sha256(expected_sha) or _sha256(raw) != expected_sha:
        raise SwebenchError("manifest fingerprint does not match frozen bytes")
    _exact_keys(manifest, _MANIFEST_KEYS, "manifest")
    if manifest["schema_version"] != 1 or manifest["benchmark"] != "swebench_verified":
        raise SwebenchError("manifest benchmark identity is unsupported")
    if manifest["split"] != "test":
        raise SwebenchError("manifest split must be test")
    dataset_id = _nonblank(manifest["dataset_id"], "manifest.dataset_id")
    dataset_revision = _nonblank(manifest["dataset_revision"], "manifest.dataset_revision")
    if _REVISION_RE.fullmatch(dataset_revision) is None:
        raise SwebenchError("manifest dataset revision must be an immutable 40-hex commit")
    if not _is_sha256(manifest["source_sha256"]):
        raise SwebenchError("manifest source fingerprint is invalid")
    expected_selection = _expected_selection_policy(profile["mode"], dataset_id)
    if manifest["selection_policy"] != expected_selection:
        raise SwebenchError("manifest selection policy is unsupported")
    if manifest["frozen_before_tuning"] is not True or manifest["license_authorized"] is not True:
        raise SwebenchError("manifest must be frozen and license-authorized")
    _nonblank(
        manifest["license_authorization_revision"],
        "manifest.license_authorization_revision",
    )
    records = _canonical_records(manifest, profile, state)
    verified_ids = manifest["verified_500_instance_ids"]
    if not isinstance(verified_ids, list) or len(verified_ids) != VERIFIED_TASK_COUNT:
        raise SwebenchError("manifest must include the exact Verified 500 ID set")
    if any(
        not isinstance(item, str) or _INSTANCE_RE.fullmatch(item) is None for item in verified_ids
    ):
        raise SwebenchError("Verified 500 IDs are invalid")
    if len(set(verified_ids)) != VERIFIED_TASK_COUNT:
        raise SwebenchError("Verified 500 IDs must be unique")
    if _sha256(_canonical(verified_ids)) != manifest["verified_500_ids_sha256"]:
        raise SwebenchError("Verified 500 ID fingerprint is invalid")
    mode = profile["mode"]
    raw_tasks = manifest["tasks"]
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise SwebenchError("manifest tasks must be a nonempty list")
    tasks: list[_Task] = []
    if len(raw_tasks) != len(records):
        raise SwebenchError("manifest task count does not match canonical records")
    for index, raw_task in enumerate(raw_tasks):
        task = _mapping(raw_task, "manifest task")
        _exact_keys(task, _TASK_KEYS, "manifest task")
        instance_id = _nonblank(task["instance_id"], "instance_id")
        if _INSTANCE_RE.fullmatch(instance_id) is None:
            raise SwebenchError("instance_id is unsafe")
        if mode == "qualification" and instance_id in verified_ids:
            raise SwebenchError("qualification tasks must be disjoint from the Verified 500")
        commit = _nonblank(task["base_commit"], "base_commit")
        problem_sha = task["problem_statement_sha256"]
        record_sha = task["record_sha256"]
        if (
            _REVISION_RE.fullmatch(commit) is None
            or not _is_sha256(problem_sha)
            or not _is_sha256(record_sha)
        ):
            raise SwebenchError("manifest task provenance is invalid")
        record = records[index]
        record_problem_sha = _sha256(cast(str, record["problem_statement"]).encode("utf-8"))
        record_sha_expected = _sha256(_canonical(record))
        if (
            instance_id != record["instance_id"]
            or commit != record["base_commit"]
            or task["repository"] != record["repo"]
            or problem_sha != record_problem_sha
            or record_sha != record_sha_expected
        ):
            raise SwebenchError("manifest tasks do not match canonical record order")
        tasks.append(
            _Task(
                instance_id,
                commit,
                _nonblank(task["repository"], "repository"),
                cast(str, problem_sha),
                cast(str, record_sha),
            )
        )
    ids = [task.instance_id for task in tasks]
    if len(set(ids)) != len(ids):
        raise SwebenchError("manifest instance IDs must be unique")
    if manifest["task_count"] != len(tasks) or profile["task_count"] != len(tasks):
        raise SwebenchError("task count does not match the frozen manifest")
    if _sha256(_canonical(ids)) != manifest["ordered_instance_ids_sha256"]:
        raise SwebenchError("ordered instance ID fingerprint is invalid")
    if mode == "verified" and (len(tasks) != VERIFIED_TASK_COUNT or ids != verified_ids):
        raise SwebenchError("verified mode requires the exact ordered Verified 500")
    return tuple(tasks), cast(str, expected_sha), dataset_id, dataset_revision


def _validated_bound_task(
    task: _Task,
    raw_binding: object,
    *,
    state: Path,
    mode: str,
) -> _Task:
    binding = _mapping(raw_binding, "image binding")
    _exact_keys(binding, _IMAGE_BINDING_KEYS, "image binding")
    if (
        binding["instance_id"] != task.instance_id
        or binding["base_commit"] != task.base_commit
        or binding["source_record_sha256"] != task.record_sha256
    ):
        raise SwebenchError("image binding does not match its frozen source task")
    provenance_hashes = (
        "task_build_recipe_sha256",
        "task_build_inputs_sha256",
        "grader_build_recipe_sha256",
        "grader_build_inputs_sha256",
        "official_scorer_revision_sha256",
        "trusted_tests_sha256",
    )
    if any(not _is_sha256(binding[key]) for key in provenance_hashes):
        raise SwebenchError("image binding build or grader provenance is invalid")
    task_reference, task_digest = _validate_image(
        binding["task_image_reference"], binding["task_image_digest"], "task_image"
    )
    grader_reference, grader_digest = _validate_image(
        binding["grader_image_reference"], binding["grader_image_digest"], "grader_image"
    )
    if task_reference == grader_reference or task_digest == grader_digest:
        raise SwebenchError("task and grader images must be separate immutable images")
    expected_spec_path = Path("swebench") / mode / "host-test-specs" / f"{task.instance_id}.json"
    spec_path_value = binding["host_test_spec_path"]
    spec_sha256 = binding["host_test_spec_sha256"]
    if (
        not isinstance(spec_path_value, str)
        or Path(spec_path_value) != expected_spec_path
        or not _is_sha256(spec_sha256)
    ):
        raise SwebenchError("image binding does not name the canonical host TestSpec")
    spec_path = _private_path(state / expected_spec_path, state, "host TestSpec")
    try:
        if stat.S_IMODE(spec_path.stat().st_mode) & 0o077:
            raise SwebenchError("host TestSpec must remain owner-only")
        spec_raw = spec_path.read_bytes()
        if _sha256(spec_raw) != spec_sha256:
            raise SwebenchError("host TestSpec fingerprint does not match frozen bytes")
        spec_document = _mapping(json.loads(spec_raw), "host TestSpec")
        _exact_keys(spec_document, _HOST_TEST_SPEC_KEYS, "host TestSpec")
        fields = _mapping(spec_document["test_spec"], "host TestSpec fields")
        _exact_keys(fields, _HOST_TEST_SPEC_FIELDS, "host TestSpec fields")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SwebenchError("host TestSpec is unreadable or invalid JSON") from exc
    if (
        spec_document["schema_version"] != 1
        or spec_document["swebench_version"] != SWEBENCH_VERSION
        or spec_document["instance_id"] != task.instance_id
        or spec_document["source_record_sha256"] != task.record_sha256
        or spec_document["trusted_tests_sha256"] != binding["trusted_tests_sha256"]
        or not _is_sha256(spec_document["source_eval_script_sha256"])
        or not _is_sha256(spec_document["test_spec_sha256"])
        or not _is_sha256(spec_document["canonical_eval_script_sha256"])
        or not _is_sha256(spec_document["execution_eval_script_sha256"])
        or spec_document["execution_eval_script_sha256"] != binding["execution_eval_script_sha256"]
    ):
        raise SwebenchError("host TestSpec provenance does not match image bindings")
    for key in ("instance_id", "image", "repo", "version", "log_parser", "eval_type"):
        if not isinstance(fields[key], str) or not fields[key].strip():
            raise SwebenchError(f"host TestSpec field {key} is invalid")
    for key in ("FAIL_TO_PASS", "PASS_TO_PASS"):
        tests = fields[key]
        if not isinstance(tests, list) or not all(isinstance(value, str) for value in tests):
            raise SwebenchError(f"host TestSpec field {key} is invalid")
    eval_script = fields["eval_script"]
    if (
        not isinstance(eval_script, str)
        or not eval_script
        or _sha256(eval_script.encode("utf-8")) != spec_document["source_eval_script_sha256"]
        or _sha256(_canonical(fields)) != spec_document["test_spec_sha256"]
    ):
        raise SwebenchError("host TestSpec source fingerprint is invalid")
    canonical_encoded = spec_document["canonical_eval_script_b64"]
    execution_encoded = spec_document["execution_eval_script_b64"]
    if not isinstance(canonical_encoded, str) or not isinstance(execution_encoded, str):
        raise SwebenchError("host TestSpec eval script bytes are unavailable")
    try:
        canonical_bytes = base64.b64decode(canonical_encoded, validate=True)
        execution_bytes = base64.b64decode(execution_encoded, validate=True)
    except ValueError as exc:
        raise SwebenchError("host TestSpec eval script encoding is invalid") from exc
    if (
        not canonical_bytes
        or len(canonical_bytes) > 4 * 1024 * 1024
        or canonical_encoded != base64.b64encode(canonical_bytes).decode("ascii")
        or _sha256(canonical_bytes) != spec_document["canonical_eval_script_sha256"]
        or len(execution_bytes) > 4 * 1024 * 1024
        or execution_encoded != base64.b64encode(execution_bytes).decode("ascii")
        or _sha256(execution_bytes) != spec_document["execution_eval_script_sha256"]
        or execution_bytes != canonical_bytes + b'exit "$SWEBENCH_TEST_EXIT_CODE"\n'
    ):
        raise SwebenchError("host TestSpec canonical or execution eval script is invalid")
    if fields["image_assets"] not in (None, [], {}):
        raise SwebenchError("host TestSpec image assets are unsupported by the isolated grader")
    return _Task(
        task.instance_id,
        task.base_commit,
        task.repository,
        task.problem_statement_sha256,
        task.record_sha256,
        task_reference,
        task_digest,
        grader_reference,
        grader_digest,
        _sha256(_canonical(dict(binding))),
        expected_spec_path.as_posix(),
        cast(str, spec_sha256),
        cast(str, spec_document["execution_eval_script_sha256"]),
    )


def _expected_scorer_entrypoint_sha256(repo: Path) -> str:
    path = repo / "sandbox" / "swebench" / "scorer_entrypoint.py"
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISREG(info.st_mode)
            or not resolved.is_relative_to(repo.resolve(strict=True))
        ):
            raise SwebenchError("trusted scorer entrypoint source is not a regular repo file")
        raw = path.read_bytes()
    except OSError as exc:
        raise SwebenchError("trusted scorer entrypoint source is unavailable") from exc
    if not raw or len(raw) > 4 * 1024 * 1024:
        raise SwebenchError("trusted scorer entrypoint source size is invalid")
    return _sha256(raw)


def _load_frozen_test_spec(task: _Task, state: Path, repo: Path) -> _ScorerTestSpec:
    if not task.host_test_spec_path or not _is_sha256(task.host_test_spec_sha256):
        raise SwebenchError("host TestSpec binding is unavailable")
    spec_path = _private_path(state / Path(task.host_test_spec_path), state, "host TestSpec")
    try:
        if stat.S_IMODE(spec_path.stat().st_mode) & 0o077:
            raise SwebenchError("host TestSpec must remain owner-only")
        spec_raw = spec_path.read_bytes()
        if _sha256(spec_raw) != task.host_test_spec_sha256:
            raise SwebenchError("host TestSpec fingerprint drifted before scoring")
        document = _mapping(json.loads(spec_raw), "host TestSpec")
        fields = dict(_mapping(document.get("test_spec"), "host TestSpec fields"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SwebenchError("host TestSpec is unavailable at scoring time") from exc
    if set(document) != _HOST_TEST_SPEC_KEYS or set(fields) != _HOST_TEST_SPEC_FIELDS:
        raise SwebenchError("host TestSpec fields are invalid")
    if (
        document.get("schema_version") != 1
        or document.get("swebench_version") != SWEBENCH_VERSION
        or document.get("instance_id") != task.instance_id
        or fields.get("instance_id") != task.instance_id
    ):
        raise SwebenchError("host TestSpec identity is invalid")
    source_eval_script = fields.get("eval_script")
    if not isinstance(source_eval_script, str) or not source_eval_script:
        raise SwebenchError("frozen source eval script is invalid")
    if document.get("source_eval_script_sha256") != _sha256(source_eval_script.encode("utf-8")):
        raise SwebenchError("frozen source eval script hash is invalid")
    test_spec_sha256 = document.get("test_spec_sha256")
    if not _is_sha256(test_spec_sha256) or test_spec_sha256 != _sha256(_canonical(fields)):
        raise SwebenchError("frozen official TestSpec input hash is invalid")
    eval_script_sha256 = document.get("canonical_eval_script_sha256")
    encoded_script = document.get("canonical_eval_script_b64")
    if not _is_sha256(eval_script_sha256) or not isinstance(encoded_script, str):
        raise SwebenchError("frozen canonical eval script is unavailable")
    try:
        eval_script_bytes = base64.b64decode(encoded_script, validate=True)
    except ValueError as exc:
        raise SwebenchError("frozen canonical eval script encoding is invalid") from exc
    if (
        not eval_script_bytes
        or len(eval_script_bytes) > 4 * 1024 * 1024
        or _sha256(eval_script_bytes) != eval_script_sha256
    ):
        raise SwebenchError("frozen canonical eval script hash is invalid")
    try:
        eval_script = eval_script_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise SwebenchError("frozen canonical eval script is not UTF-8") from exc
    execution_eval_script_sha256 = document.get("execution_eval_script_sha256")
    encoded_execution_script = document.get("execution_eval_script_b64")
    if not _is_sha256(execution_eval_script_sha256) or not isinstance(
        encoded_execution_script, str
    ):
        raise SwebenchError("frozen execution eval script is unavailable")
    try:
        execution_script_bytes = base64.b64decode(encoded_execution_script, validate=True)
    except ValueError as exc:
        raise SwebenchError("frozen execution eval script encoding is invalid") from exc
    if (
        len(execution_script_bytes) > 4 * 1024 * 1024
        or _sha256(execution_script_bytes) != execution_eval_script_sha256
        or execution_script_bytes != eval_script_bytes + b'exit "$SWEBENCH_TEST_EXIT_CODE"\n'
    ):
        raise SwebenchError("frozen execution eval script does not match canonical output")
    try:
        execution_eval_script = execution_script_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise SwebenchError("frozen execution eval script is not UTF-8") from exc
    scorer_entrypoint_sha256 = _expected_scorer_entrypoint_sha256(repo)
    if document.get("scorer_entrypoint_sha256") != scorer_entrypoint_sha256:
        raise SwebenchError("frozen scorer entrypoint hash differs from reviewed source")
    return _ScorerTestSpec(
        fields=fields,
        instance_id=task.instance_id,
        image=cast(str, fields["image"]),
        task_fingerprint_sha256=_task_fingerprint(task),
        eval_script=eval_script,
        execution_eval_script=execution_eval_script,
        test_spec_sha256=cast(str, test_spec_sha256),
        eval_script_sha256=cast(str, eval_script_sha256),
        execution_eval_script_sha256=cast(str, execution_eval_script_sha256),
        scorer_entrypoint_sha256=scorer_entrypoint_sha256,
    )


def _validate_scorer_response(
    response: Mapping[str, Any],
    *,
    action: str,
    test_spec: _ScorerTestSpec,
    patch_sha256: str | None = None,
    grading_log_sha256: str | None = None,
) -> _ScorerResult:
    if set(response) != _SCORER_ENTRYPOINT_RESPONSE_KEYS:
        raise SwebenchError("trusted scorer response fields are invalid")
    expected_status = "prepared" if action == "prepare" else "official"
    if (
        response.get("schema_version") != 1
        or response.get("action") != action
        or response.get("scorer_status") != expected_status
        or response.get("error") is not None
        or response.get("swebench_version") != SWEBENCH_VERSION
        or response.get("instance_id") != test_spec.instance_id
        or response.get("test_spec_sha256") != test_spec.test_spec_sha256
        or response.get("scorer_entrypoint_sha256") != test_spec.scorer_entrypoint_sha256
    ):
        raise SwebenchError("trusted scorer identity or status is invalid")
    if action == "prepare":
        encoded = response.get("eval_script_b64")
        if not isinstance(encoded, str):
            raise SwebenchError("trusted scorer omitted the canonical eval script")
        try:
            generated = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise SwebenchError("trusted scorer eval script encoding is invalid") from exc
        frozen = test_spec.eval_script.encode("utf-8")
        if (
            generated != frozen
            or encoded != base64.b64encode(frozen).decode("ascii")
            or response.get("eval_script_sha256") != test_spec.eval_script_sha256
            or _sha256(generated) != test_spec.eval_script_sha256
            or response.get("patch_sha256") is not None
            or response.get("grading_log_sha256") is not None
            or response.get("host_cleanup_confirmed") is not None
            or response.get("resolved") is not None
            or response.get("report") is not None
        ):
            raise SwebenchError("trusted scorer prepare result differs from frozen inputs")
        return _ScorerResult("prepared", False, None)
    if action != "score":
        raise SwebenchError("trusted scorer action is unsupported")
    resolved = response.get("resolved")
    report = response.get("report")
    if (
        response.get("eval_script_b64") is not None
        or response.get("eval_script_sha256") != test_spec.eval_script_sha256
        or response.get("patch_sha256") != patch_sha256
        or response.get("grading_log_sha256") != grading_log_sha256
        or response.get("host_cleanup_confirmed") is not True
        or not isinstance(resolved, bool)
        or not isinstance(report, Mapping)
    ):
        raise SwebenchError("trusted scorer score result is invalid")
    return _ScorerResult("resolved" if resolved else "unresolved", True, resolved, report)


def _make_official_test_spec(
    task: _Task,
    state: Path,
    *,
    prepared: _Prepared,
    runtime: SwebenchRuntime,
) -> tuple[_ScorerTestSpec, str]:
    """Verify TestSpec construction in the pinned, offline control image."""

    frozen = _load_frozen_test_spec(task, state, prepared.repo)
    request = {
        "schema_version": 1,
        "action": "prepare",
        "swebench_version": SWEBENCH_VERSION,
        "instance_id": task.instance_id,
        "test_spec": dict(frozen.fields),
    }
    response = _invoke_trusted_scorer(
        request,
        prepared=prepared,
        runtime=runtime,
        task=task,
        action="prepare",
        test_spec_sha256=frozen.test_spec_sha256,
    )
    _validate_scorer_response(response, action="prepare", test_spec=frozen)
    return frozen, frozen.eval_script


def _validated_binding_entries(
    document: Mapping[str, Any], profile: Mapping[str, Any], tasks: tuple[_Task, ...]
) -> list[object]:
    _exact_keys(document, _IMAGE_BINDINGS_KEYS, "image bindings")
    if (
        document["schema_version"] != 1
        or document["benchmark"] != "swebench_verified"
        or document["mode"] != profile["mode"]
        or document["execution_platform"] != profile["platform"]
    ):
        raise SwebenchError("image binding identity is unsupported")
    ordered_ids = [task.instance_id for task in tasks]
    ordered_ids_sha256 = _sha256(_canonical(ordered_ids))
    if (
        profile["image_bindings_task_count"] != len(tasks)
        or document["task_count"] != len(tasks)
        or profile["image_bindings_ordered_instance_ids_sha256"] != ordered_ids_sha256
        or document["ordered_instance_ids_sha256"] != ordered_ids_sha256
    ):
        raise SwebenchError("image bindings do not match the frozen task selection")
    control_reference, control_digest = _validate_image(
        profile["control_image_reference"], profile["control_image_digest"], "control_image"
    )
    if (
        document["control_image_reference"] != control_reference
        or document["control_image_digest"] != control_digest
        or not _is_sha256(document["isolation_revision_sha256"])
    ):
        raise SwebenchError("image bindings do not match the frozen execution boundary")
    raw_bindings = document["bindings"]
    if not isinstance(raw_bindings, list) or len(raw_bindings) != len(tasks):
        raise SwebenchError("image bindings must contain exactly one entry per frozen task")
    return raw_bindings


def _validate_image_bindings(
    profile: Mapping[str, Any], state: Path, tasks: tuple[_Task, ...]
) -> tuple[tuple[_Task, ...], str]:
    configured_path = Path(_nonblank(profile["image_bindings_path"], "image_bindings_path"))
    if not configured_path.is_absolute():
        configured_path = state / configured_path
    bindings_path = _private_path(configured_path, state, "image bindings")
    try:
        raw = bindings_path.read_bytes()
        document = _mapping(json.loads(raw), "image bindings")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SwebenchError("image bindings are unreadable or invalid JSON") from exc
    expected_sha = profile["image_bindings_sha256"]
    if not _is_sha256(expected_sha) or _sha256(raw) != expected_sha:
        raise SwebenchError("image binding fingerprint does not match frozen bytes")
    raw_bindings = _validated_binding_entries(document, profile, tasks)
    bound_tasks: list[_Task] = []
    image_identities: set[tuple[str, str]] = set()
    for task, raw_binding in zip(tasks, raw_bindings, strict=True):
        bound_task = _validated_bound_task(
            task,
            raw_binding,
            state=state,
            mode=cast(str, profile["mode"]),
        )
        task_identity = (bound_task.task_image_reference, bound_task.task_image_digest)
        grader_identity = (bound_task.grader_image_reference, bound_task.grader_image_digest)
        if task_identity in image_identities or grader_identity in image_identities:
            raise SwebenchError("each frozen task requires distinct task and grader images")
        image_identities.update((task_identity, grader_identity))
        bound_tasks.append(bound_task)
    return tuple(bound_tasks), cast(str, expected_sha)


def _validate_runner_config(repo: Path, expected_sha256: object) -> str:
    path = secure_resolve(repo / "sandbox" / "swebench" / "mini-swe-agent.yaml", must_exist=True)
    raw = path.read_bytes()
    if not _is_sha256(expected_sha256) or _sha256(raw) != expected_sha256:
        raise SwebenchError("mini-SWE-agent runner config fingerprint does not match")
    try:
        config = _mapping(yaml.safe_load(raw), "mini-SWE-agent config")
        environment = _mapping(config["environment"], "environment")
        agent = _mapping(config["agent"], "agent")
        model = _mapping(config["model"], "model")
    except (KeyError, yaml.YAMLError) as exc:
        raise SwebenchError("mini-SWE-agent config is invalid") from exc
    if environment.get("environment_class") != "racecraft-existing-offline-docker-exec":
        raise SwebenchError("mini-SWE-agent local environment is refused")
    if environment.get("interpreter") != ["bash", "-c"] or agent.get("tools") != ["bash"]:
        raise SwebenchError("mini-SWE-agent must expose bash only")
    if environment.get("timeout") != _TASK_EXEC_TIMEOUT_SECONDS:
        raise SwebenchError("mini-SWE-agent command timeout does not match the host")
    if environment.get("forward_env") != [] or environment.get("env") != _TASK_EXEC_UPSTREAM_ENV:
        raise SwebenchError("mini-SWE-agent environment may not forward host state or network")
    if model.get("protocol") != "lm-studio-openai-compatible-text-bash-v1":
        raise SwebenchError("mini-SWE-agent must use the explicit local OpenAI adapter")
    return expected_sha256


def _egress_sources_sha256(repo: Path) -> str:
    root = repo / "sandbox" / "swebench" / "egress"
    try:
        digests = {name: _sha256((root / name).read_bytes()) for name in _EGRESS_SOURCES}
    except OSError as exc:
        raise SwebenchError("grader egress gateway sources are unavailable") from exc
    return _sha256(_canonical(digests))


def _validate_grader_egress(value: object, repo: Path, task_ids: set[str]) -> None:
    egress = _mapping(value, "grader_egress")
    _exact_keys(
        egress,
        {"gateway_image_id", "gateway_sources_sha256", "network", "tasks"},
        "grader_egress",
    )
    image_id = egress["gateway_image_id"]
    if not isinstance(image_id, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
        raise SwebenchError("grader egress gateway image id is invalid")
    if egress["network"] != _EGRESS_NETWORK:
        raise SwebenchError("grader egress network is not the frozen network")
    if egress["gateway_sources_sha256"] != _egress_sources_sha256(repo):
        raise SwebenchError("grader egress gateway sources do not match the frozen fingerprint")
    tasks = _mapping(egress["tasks"], "grader_egress tasks")
    if not tasks or not set(tasks) <= task_ids:
        raise SwebenchError("grader egress tasks must be frozen task instance ids")
    for hosts in tasks.values():
        if (
            not isinstance(hosts, list)
            or hosts != sorted(set(hosts))
            or not all(isinstance(host, str) and _EGRESS_HOST_RE.fullmatch(host) for host in hosts)
        ):
            raise SwebenchError("grader egress allowlists must be sorted unique host names")


def _grader_egress_hosts(prepared: _Prepared, instance_id: str) -> list[str] | None:
    """Return the task's frozen egress allowlist, or None when its grader stays offline."""

    egress = prepared.profile.get("grader_egress")
    if egress is None:
        return None
    hosts = cast(Mapping[str, list[str]], egress["tasks"]).get(instance_id)
    return None if hosts is None else list(hosts)


def _validate_profile(profile: Mapping[str, Any], repo: Path, state: Path) -> _Prepared:
    # grader_egress is optional so profiles without it keep their frozen fingerprints.
    profile_keys = _PROFILE_KEYS | ({"grader_egress"} if "grader_egress" in profile else set())
    _exact_keys(profile, profile_keys, "swebench profile")
    if profile["schema_version"] != 1 or profile["mode"] not in {"qualification", "verified"}:
        raise SwebenchError("swebench profile schema or mode is unsupported")
    if profile["mini_swe_agent_version"] != MINI_SWE_AGENT_VERSION:
        raise SwebenchError("mini-SWE-agent version is not the qualified pin")
    if profile["mini_swe_agent_wheel_sha256"] != MINI_SWE_AGENT_WHEEL_SHA256:
        raise SwebenchError("mini-SWE-agent wheel fingerprint is not the qualified pin")
    if profile["swebench_version"] != SWEBENCH_VERSION:
        raise SwebenchError("SWE-bench version is not the qualified pin")
    if profile["swebench_wheel_sha256"] != SWEBENCH_WHEEL_SHA256:
        raise SwebenchError("SWE-bench wheel fingerprint is not the qualified pin")
    if profile["platform"] != "linux/amd64":
        raise SwebenchError("official SWE-bench task execution requires linux/amd64")
    parameters = _mapping(profile["parameters"], "parameters")
    if dict(parameters) not in (_PARAMETERS, _APPROVED_LOCAL_PARAMETERS):
        raise SwebenchError("requested model parameters must match the frozen protocol")
    limits = _validate_limits(profile["resource_limits"])
    model_runtime = _mapping(profile["model_runtime"], "model_runtime")
    _exact_keys(
        model_runtime,
        {"adapter_revision_sha256", "served_model_fingerprint", "runtime_identity_sha256"},
        "model_runtime",
    )
    if any(not _is_sha256(value) for value in model_runtime.values()):
        raise SwebenchError("model_runtime identities must be lowercase SHA-256 fingerprints")
    tasks, manifest_sha, dataset_id, dataset_revision = _validate_manifest(profile, state)
    tasks, image_bindings_sha = _validate_image_bindings(profile, state, tasks)
    config_sha = _validate_runner_config(repo, profile["runner_config_sha256"])
    if profile["mode"] == "qualification" and len(tasks) > MAX_QUALIFICATION_TASKS:
        raise SwebenchError("qualification is limited to at most 10 tasks")
    approval = _mapping(profile["protocol_approval"], "protocol_approval")
    _exact_keys(approval, {"approved", "approved_fingerprint"}, "protocol_approval")
    if "grader_egress" in profile:
        _validate_grader_egress(
            profile["grader_egress"], repo, {task.instance_id for task in tasks}
        )
    fingerprint_input = {
        key: profile[key]
        for key in profile_keys - {"manifest_path", "image_bindings_path", "protocol_approval"}
    }
    fingerprint_input["resource_limits"] = dict(limits)
    fingerprint_input["sdk_transport_sha256"] = _sdk_transport_fingerprint(repo)
    fingerprint_input["sdk_protocol"] = (
        "splash-local-on-sdk2-v1"
        if parameters["reasoning_effort"] == "on"
        else "published-xhigh-unsupported-locally"
    )
    protocol_fingerprint = _sha256(_canonical(fingerprint_input))
    return _Prepared(
        profile,
        repo,
        state,
        "",
        manifest_sha,
        image_bindings_sha,
        dataset_id,
        dataset_revision,
        protocol_fingerprint,
        tasks,
        config_sha,
        (),
    )


def _default_runner() -> _Runner:
    return cast(_Runner, subprocess.run)


def _run(
    runtime: SwebenchRuntime,
    args: Sequence[str],
    *,
    timeout: float,
    max_output_bytes: int,
) -> subprocess.CompletedProcess[Any]:
    runner = runtime.runner or _default_runner()
    try:
        completed = runner(args, capture_output=True, text=False, check=False, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SwebenchError("required local container command failed") from exc
    stdout = (
        completed.stdout if isinstance(completed.stdout, bytes) else str(completed.stdout).encode()
    )
    stderr = (
        completed.stderr if isinstance(completed.stderr, bytes) else str(completed.stderr).encode()
    )
    if len(stdout) + len(stderr) > max_output_bytes:
        raise SwebenchError("container command output exceeded its bound")
    if completed.returncode != 0:
        raise SwebenchError("required local container command failed")
    return completed


def _docker_preflight(prepared: _Prepared, runtime: SwebenchRuntime) -> None:
    docker = str(runtime.docker_executable)
    limits = prepared.profile["resource_limits"]
    timeout = cast(float, limits["timeout_seconds"])
    maximum = cast(int, limits["max_output_bytes"])
    info = _run(
        runtime,
        (docker, "info", "--format", "{{json .}}"),
        timeout=timeout,
        max_output_bytes=maximum,
    )
    try:
        payload = json.loads(info.stdout)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SwebenchError("Docker architecture evidence is invalid") from exc
    if payload.get("OSType") != "linux" or payload.get("Architecture") not in {"aarch64", "arm64"}:
        raise SwebenchError("Docker must provide a linux/arm64 runtime")
    images = [(task.task_image_reference, task.task_image_digest) for task in prepared.tasks] + [
        (task.grader_image_reference, task.grader_image_digest) for task in prepared.tasks
    ]
    frozen_control = (
        cast(str, prepared.profile["control_image_reference"]),
        cast(str, prepared.profile["control_image_digest"]),
    )
    images.append(frozen_control)
    if runtime.agent_executor is _default_agent_executor:
        if _control_image_identity() != frozen_control:
            raise SwebenchError("control-plane runtime does not match the frozen image identity")
    for reference, digest in dict.fromkeys(images):
        inspected = _run(
            runtime,
            (docker, "image", "inspect", reference),
            timeout=timeout,
            max_output_bytes=maximum,
        )
        try:
            image = json.loads(inspected.stdout)[0]
        except (TypeError, IndexError, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SwebenchError("Docker image identity evidence is invalid") from exc
        if image.get("Id") != digest:
            raise SwebenchError("Docker image identity does not match the frozen digest")
        expected_architecture = "arm64" if reference == frozen_control[0] else "amd64"
        if image.get("Os") != "linux" or image.get("Architecture") != expected_architecture:
            raise SwebenchError("Docker image platform does not match its frozen role")
    egress = prepared.profile.get("grader_egress")
    if egress is not None:
        gateway_image = _run(
            runtime,
            (docker, "image", "inspect", cast(str, egress["gateway_image_id"])),
            timeout=timeout,
            max_output_bytes=maximum,
        )
        network = _run(
            runtime,
            (docker, "network", "inspect", _EGRESS_NETWORK),
            timeout=timeout,
            max_output_bytes=maximum,
        )
        try:
            image = json.loads(gateway_image.stdout)[0]
            bridge = json.loads(network.stdout)[0]
        except (TypeError, IndexError, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SwebenchError("grader egress gateway evidence is invalid") from exc
        if (
            image.get("Id") != egress["gateway_image_id"]
            or image.get("Os") != "linux"
            or image.get("Architecture") != "arm64"
        ):
            raise SwebenchError("grader egress gateway image does not match its frozen identity")
        if bridge.get("Driver") != "bridge" or bridge.get("Internal") is not False:
            raise SwebenchError("grader egress network is not an outbound bridge")


def _adapter_blockers(prepared: _Prepared, runtime: SwebenchRuntime) -> list[str]:
    if runtime.parameter_probe is None:
        return ["LM Studio parameter support probe is unavailable"]
    parameters = cast(Mapping[str, object], prepared.profile["parameters"])
    accepted = runtime.parameter_probe(
        prepared.server_origin, cast(str, prepared.profile["model"]), parameters
    )
    if set(accepted) != set(parameters) or any(accepted[key] is not True for key in parameters):
        return ["LM Studio did not explicitly accept every requested parameter"]
    if runtime.adapter_probe is None:
        return ["qualified multi-turn LM Studio adapter is unavailable"]
    evidence = runtime.adapter_probe(
        prepared.server_origin, cast(str, prepared.profile["model"]), parameters
    )
    if set(evidence) != _ADAPTER_KEYS:
        return ["multi-turn adapter attestation is incomplete"]
    expected = {
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
        **dict(cast(Mapping[str, object], prepared.profile["model_runtime"])),
    }
    if dict(evidence) != expected:
        return ["multi-turn adapter attestation does not match the frozen safety contract"]
    if runtime.agent_executor is None or runtime.grader_executor is None:
        return ["SWE-bench execution adapters are unavailable"]
    return []


def _prepare_trusted_test_specs(
    prepared: _Prepared, runtime: SwebenchRuntime
) -> dict[str, _ScorerTestSpec]:
    injected_factory = runtime.test_spec_factory is not None
    injected_scorer = runtime.score_executor is not None
    if injected_factory or injected_scorer:
        attestation = runtime.scorer_attestation
        if (
            not injected_factory
            or not injected_scorer
            or not isinstance(attestation, _ScorerAttestation)
        ):
            raise SwebenchError(
                "injected scorer callables require a complete explicit scorer attestation"
            )
        expected_entrypoint = _expected_scorer_entrypoint_sha256(prepared.repo)
        if (
            attestation.control_image_reference != prepared.profile["control_image_reference"]
            or attestation.control_image_digest != prepared.profile["control_image_digest"]
            or attestation.platform != _SCORER_PLATFORM
            or attestation.swebench_version != SWEBENCH_VERSION
            or attestation.scorer_entrypoint_sha256 != expected_entrypoint
        ):
            raise SwebenchError("injected scorer attestation differs from frozen identity")
        for task in prepared.tasks:
            _load_frozen_test_spec(task, prepared.state, prepared.repo)
        return {}
    if runtime.scorer_attestation is not None:
        raise SwebenchError("default scorer may not use an injected scorer attestation")
    test_specs: dict[str, _ScorerTestSpec] = {}
    for task in prepared.tasks:
        test_spec, _script = _make_official_test_spec(
            task,
            prepared.state,
            prepared=prepared,
            runtime=runtime,
        )
        test_specs[task.instance_id] = test_spec
    return test_specs


def _prepare(
    profile: Mapping[str, Any],
    *,
    repo: str | os.PathLike[str],
    state: str | os.PathLike[str],
    server_origin: str,
    runtime: SwebenchRuntime,
    probe_adapter: bool = True,
) -> _Prepared:
    paths = ExternalStatePaths.resolve(
        project_root=repo, state_dir=state, require_project=True, require_state=True
    )
    prepared = _validate_profile(profile, paths.project_root, paths.state_dir)
    prepared = _Prepared(
        prepared.profile,
        prepared.repo,
        prepared.state,
        _validate_origin(server_origin),
        prepared.manifest_sha256,
        prepared.image_bindings_sha256,
        prepared.dataset_id,
        prepared.dataset_revision,
        prepared.protocol_fingerprint,
        prepared.tasks,
        prepared.runner_config_sha256,
        (),
    )
    _docker_preflight(prepared, runtime)
    scorer_blockers: tuple[str, ...] = ()
    scorer_test_specs: dict[str, _ScorerTestSpec] = {}
    try:
        scorer_test_specs = _prepare_trusted_test_specs(prepared, runtime)
    except SwebenchError as exc:
        scorer_blockers = (str(exc),)
    blockers = (
        scorer_blockers
        if scorer_blockers
        else tuple(_adapter_blockers(prepared, runtime))
        if probe_adapter
        else ()
    )
    return _Prepared(
        prepared.profile,
        prepared.repo,
        prepared.state,
        prepared.server_origin,
        prepared.manifest_sha256,
        prepared.image_bindings_sha256,
        prepared.dataset_id,
        prepared.dataset_revision,
        prepared.protocol_fingerprint,
        prepared.tasks,
        prepared.runner_config_sha256,
        blockers,
        scorer_test_specs,
    )


def _metadata(prepared: _Prepared | None, profile: Mapping[str, Any]) -> dict[str, object]:
    mode = profile.get("mode")
    return {
        "schema_version": 1,
        "protocol_fingerprint": prepared.protocol_fingerprint if prepared else None,
        "manifest_sha256": prepared.manifest_sha256 if prepared else profile.get("manifest_sha256"),
        "image_bindings_sha256": (
            prepared.image_bindings_sha256 if prepared else profile.get("image_bindings_sha256")
        ),
        "runner_config_sha256": (
            prepared.runner_config_sha256 if prepared else profile.get("runner_config_sha256")
        ),
        "image_bindings_schema_version": 1,
        "image_bindings_task_count": profile.get("image_bindings_task_count"),
        "control_image_digest": profile.get("control_image_digest"),
        "platform": profile.get("platform"),
        "mode": mode,
        "task_count": len(prepared.tasks) if prepared else profile.get("task_count"),
        "max_qualification_tasks": MAX_QUALIFICATION_TASKS,
        "non_capability": mode == "qualification",
        "full_run_requires_approval": mode == "verified",
        "requested_parameters": dict(profile.get("parameters", {}))
        if isinstance(profile.get("parameters"), Mapping)
        else {},
    }


def inspect_swebench_readiness(
    profile: Mapping[str, Any],
    *,
    repo: str | os.PathLike[str],
    state: str | os.PathLike[str],
    server_origin: str,
    runtime: SwebenchRuntime | None = None,
) -> dict[str, Any]:
    """Return sanitized readiness without private paths or instance IDs."""

    selected = runtime or SwebenchRuntime()
    try:
        prepared = _prepare(
            profile, repo=repo, state=state, server_origin=server_origin, runtime=selected
        )
    except (ConfigurationError, OSError, SwebenchError) as exc:
        return {"status": "blocked", "blockers": [str(exc)], "metadata": _metadata(None, profile)}
    return {
        "status": "blocked" if prepared.blockers else "ready",
        "blockers": list(prepared.blockers),
        "metadata": _metadata(prepared, profile),
    }


def _container_args(
    *,
    image_reference: str,
    image_digest: str,
    platform: str,
    limits: Mapping[str, Any],
    name: str,
    role: str,
    writable_testbed: bool,
    network_mode: str = "none",
) -> tuple[str, ...]:
    _validate_image(image_reference, image_digest, role)
    if network_mode != "none" and (
        role != "grader_image" or re.fullmatch(r"container:[0-9a-f]{64}", network_mode) is None
    ):
        raise SwebenchError("only a grader may join a verified egress gateway namespace")
    if platform not in {"linux/amd64", "linux/arm64/v8"} or not re.fullmatch(
        r"swebench-[a-z]+-[0-9a-f]{16}", name
    ):
        raise SwebenchError("container identity or platform is unsafe")
    _validate_limits(limits)
    command = [
        "/usr/local/bin/docker",
        "create",
        "--name",
        name,
        "--platform",
        platform,
        "--network",
        network_mode,
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        str(limits["pids_limit"]),
        "--memory",
        str(limits["memory_bytes"]),
        "--cpus",
        cast(str, limits["cpus"]),
        "--ulimit",
        f"nofile={limits['nofile_limit']}:{limits['nofile_limit']}",
        "--tmpfs",
        f"/tmp:rw,noexec,nosuid,nodev,size={limits['tmpfs_bytes']}",  # noqa: S108 - container tmpfs, not host
        "--user",
        "65532:65532",
        "--env",
        "HOME=/tmp/home",
        "--env",
        "PATH=/usr/local/bin:/usr/bin:/bin",
    ]
    if writable_testbed:
        command.extend(
            (
                "--mount",
                f"type=volume,src={name}-workspace,dst=/testbed",
            )
        )
    command.extend(
        (
            image_reference,
            "sleep",
            "infinity",
        )
    )
    return tuple(command)


def build_task_container_args(
    *,
    image_reference: str,
    image_digest: str,
    platform: str,
    limits: Mapping[str, Any],
    name: str,
) -> tuple[str, ...]:
    """Build fixed Docker arguments for a fresh, persistent, offline task container."""

    return _container_args(
        image_reference=image_reference,
        image_digest=image_digest,
        platform=platform,
        limits=limits,
        name=name,
        role="task_image",
        writable_testbed=True,
    )


def build_grader_container_args(
    *,
    image_reference: str,
    image_digest: str,
    platform: str,
    limits: Mapping[str, Any],
    name: str,
    network_mode: str = "none",
) -> tuple[str, ...]:
    """Build fixed Docker arguments for a separate fresh official grader.

    ``network_mode`` is ``none`` unless the task's frozen egress gateway is verified; then it is
    ``container:<gateway id>``.
    """

    return _container_args(
        image_reference=image_reference,
        image_digest=image_digest,
        platform=platform,
        limits=limits,
        name=name,
        role="grader_image",
        writable_testbed=True,
        network_mode=network_mode,
    )


def build_gateway_container_args(
    *, image_id: str, hosts: Sequence[str], name: str
) -> tuple[str, ...]:
    """Build fixed Docker arguments for one grader's egress gateway (harness code only)."""

    if re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None or not re.fullmatch(
        r"swebench-egress-[0-9a-f]{16}", name
    ):
        raise SwebenchError("egress gateway identity is unsafe")
    if not all(_EGRESS_HOST_RE.fullmatch(host) for host in hosts):
        raise SwebenchError("egress gateway allowlist is invalid")
    capabilities = [arg for cap in sorted(_EGRESS_GATEWAY_CAPS) for arg in ("--cap-add", cap)]
    return (
        "/usr/local/bin/docker",
        "run",
        "--detach",
        "--name",
        name,
        "--cap-drop",
        "ALL",
        *capabilities,
        "--sysctl",
        "net.ipv6.conf.all.disable_ipv6=1",
        "--tmpfs",
        "/tmp",  # noqa: S108 - gateway container tmpfs, not host
        "--network",
        _EGRESS_NETWORK,
        "--env",
        f"ALLOW={','.join(hosts)}",
        image_id,
    )


def _attest_gateway(
    runtime: SwebenchRuntime,
    name: str,
    limits: Mapping[str, Any],
    *,
    image_id: str,
    hosts: Sequence[str],
) -> str:
    """Verify a running gateway matches its frozen configuration; return its full id."""

    inspected = _run(
        runtime,
        (str(runtime.docker_executable), "inspect", name),
        timeout=30,
        max_output_bytes=cast(int, limits["max_output_bytes"]),
    )
    try:
        item = json.loads(inspected.stdout)[0]
        gateway_id = item["Id"]
        config = item["Config"]
        host = item["HostConfig"]
        networks = item["NetworkSettings"]["Networks"]
        running = item["State"]["Running"]
    except (TypeError, IndexError, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SwebenchError("egress gateway attestation is invalid") from exc
    environment = [value for value in config.get("Env", []) if not value.startswith("PATH=")]
    capabilities = {str(cap).removeprefix("CAP_") for cap in host.get("CapAdd") or []}
    if not (
        isinstance(gateway_id, str)
        and re.fullmatch(r"[0-9a-f]{64}", gateway_id)
        and running is True
        and config.get("Image") == image_id
        and not config.get("User")
        and environment == [f"ALLOW={','.join(hosts)}"]
        and not config.get("ExposedPorts")
        and host.get("Privileged") is False
        and host.get("CapDrop") == ["ALL"]
        and capabilities == _EGRESS_GATEWAY_CAPS
        and host.get("NetworkMode") == _EGRESS_NETWORK
        and set(networks) == {_EGRESS_NETWORK}
        and host.get("Sysctls") == {"net.ipv6.conf.all.disable_ipv6": "1"}
        and not host.get("Binds")
        and not host.get("PortBindings")
        and not item.get("Mounts")
        and host.get("Tmpfs") == {"/tmp": ""}  # noqa: S108 - gateway tmpfs, not host
    ):
        raise SwebenchError("egress gateway attestation does not match the frozen contract")
    return gateway_id


def _start_egress_gateway(
    runtime: SwebenchRuntime,
    prepared: _Prepared,
    hosts: Sequence[str],
    limits: Mapping[str, Any],
    *,
    name: str,
    evidence_dir: Path,
    attempt_nonce: str,
) -> str:
    """Start, attest and probe one grader's gateway; return its id. Fails closed."""

    egress = cast(Mapping[str, Any], prepared.profile["grader_egress"])
    image_id = cast(str, egress["gateway_image_id"])
    docker = str(runtime.docker_executable)
    maximum = cast(int, limits["max_output_bytes"])
    args = build_gateway_container_args(image_id=image_id, hosts=hosts, name=name)
    _run(runtime, (docker, *args[1:]), timeout=30, max_output_bytes=maximum)
    deadline = time.monotonic() + _EGRESS_READY_SECONDS
    while True:
        logs = _run(runtime, (docker, "logs", name), timeout=30, max_output_bytes=maximum)
        text = (logs.stdout or b"") + (logs.stderr or b"")
        if re.search(rb"dnsmasq\[\d+\]: started", text) and b"egress_proxy: started" in text:
            break
        if time.monotonic() >= deadline:
            raise SwebenchError("egress gateway did not become ready")
        time.sleep(1)
    gateway_id = _attest_gateway(runtime, name, limits, image_id=image_id, hosts=hosts)
    for uid in _EGRESS_PROBE_UIDS:
        probe = (
            docker,
            "run",
            "--rm",
            "--network",
            f"container:{gateway_id}",
            "--user",
            f"{uid}:{uid}",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--env",
            f"ALLOW={','.join(hosts)}",
            "--env",
            f"EXPECT_UID={uid}",
            "--entrypoint",
            "python3",
            image_id,
            "/opt/egress/probe.py",
        )
        runner = runtime.runner or _default_runner()
        try:
            completed = runner(
                probe,
                capture_output=True,
                text=False,
                check=False,
                timeout=_EGRESS_PROBE_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SwebenchError("egress gateway probe could not run") from exc
        transcript = bytes(completed.stdout or b"") + bytes(completed.stderr or b"")
        if len(transcript) > maximum:
            raise SwebenchError("egress gateway probe output exceeded its bound")
        _write_private_bytes(
            evidence_dir / f"egress-probe-{attempt_nonce}-uid{uid}-{_sha256(transcript)}.txt",
            transcript,
        )
        lines = transcript.decode("utf-8", errors="replace").splitlines()
        if (
            completed.returncode != 0
            or not any(line.startswith("PASS ") for line in lines)
            or any(not line.startswith(("PASS ", "INFO ")) for line in lines)
        ):
            raise SwebenchError("egress gateway isolation probe failed")
    return gateway_id


def _require_gateway_running(
    runtime: SwebenchRuntime, name: str, limits: Mapping[str, Any], gateway_id: str
) -> None:
    """A gateway that died mid-eval makes the grade an infrastructure error, not a verdict."""

    inspected = _run(
        runtime,
        (str(runtime.docker_executable), "inspect", name),
        timeout=30,
        max_output_bytes=cast(int, limits["max_output_bytes"]),
    )
    try:
        item = json.loads(inspected.stdout)[0]
        ok = item["Id"] == gateway_id and item["State"]["Running"] is True
    except (TypeError, IndexError, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SwebenchError("egress gateway liveness evidence is invalid") from exc
    if not ok:
        raise SwebenchError("egress gateway stopped during grading")


def _exception_diagnostic(exc: BaseException, *, task_index: int) -> dict[str, Any]:
    """Name each exception in the chain; keep only harness-authored messages."""

    chain: list[dict[str, str | None]] = []
    current: BaseException | None = exc
    while current is not None and len(chain) < 8:
        chain.append(
            {
                "type": type(current).__name__,
                "message": str(current) if isinstance(current, SwebenchError) else None,
            }
        )
        current = current.__cause__ or current.__context__
    return {"schema_version": 1, "task_index": task_index, "exception_chain": chain}


def _write_private_json(path: Path, value: object) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.stat().st_mode & 0o077:
        raise SwebenchError("private run directory permissions are too broad")
    temporary = path.with_suffix(path.suffix + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_canonical(value))
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _write_private_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.stat().st_mode & 0o077:
        raise SwebenchError("private run directory permissions are too broad")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _cleanup(
    runtime: SwebenchRuntime,
    name: str,
    limits: Mapping[str, Any],
    *,
    workspace_volume: str | None = None,
) -> tuple[bool, bool]:
    runner = runtime.runner or _default_runner()
    docker = str(runtime.docker_executable)
    timeout = min(30, cast(int, limits["timeout_seconds"]))
    maximum = cast(int, limits["max_output_bytes"])

    def invoke(args: tuple[str, ...]) -> tuple[subprocess.CompletedProcess[Any], bytes, bytes]:
        try:
            completed = runner(
                args,
                capture_output=True,
                text=False,
                check=False,
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            message = (
                "container workspace teardown failed"
                if args[1:3] == ("volume", "rm")
                else "container teardown failed"
            )
            raise SwebenchError(message) from exc
        stdout = (
            completed.stdout
            if isinstance(completed.stdout, bytes)
            else str(completed.stdout).encode()
        )
        stderr = (
            completed.stderr
            if isinstance(completed.stderr, bytes)
            else str(completed.stderr).encode()
        )
        if len(stdout) + len(stderr) > maximum:
            raise SwebenchError("container cleanup output exceeded its bound")
        return completed, stdout, stderr

    def confirms_absence(
        completed: subprocess.CompletedProcess[Any],
        stdout: bytes,
        stderr: bytes,
        *,
        resource: str,
        expected_name: str,
    ) -> bool:
        if completed.returncode != 1:
            return False
        try:
            diagnostic = stderr.decode("utf-8", errors="strict").strip()
        except UnicodeDecodeError:
            return False
        if resource == "container":
            legacy_pattern = rf"Error: No such (?:container|object): {re.escape(expected_name)}"
            if not stdout.strip() and re.fullmatch(legacy_pattern, diagnostic, flags=re.IGNORECASE):
                return True
            daemon_pattern = (
                rf"Error response from daemon: No such container: "
                rf"{re.escape(expected_name)}"
            )
            return stdout.strip() == b"[]" and re.fullmatch(daemon_pattern, diagnostic) is not None
        else:
            legacy_pattern = rf"Error: No such volume: {re.escape(expected_name)}"
            if not stdout.strip() and re.fullmatch(legacy_pattern, diagnostic, flags=re.IGNORECASE):
                return True
            daemon_pattern = (
                rf"Error response from daemon: get {re.escape(expected_name)}: "
                rf"no such volume"
            )
            return stdout.strip() == b"[]" and re.fullmatch(daemon_pattern, diagnostic) is not None

    removed, _stdout, _stderr = invoke((docker, "rm", "-f", name))
    if removed.returncode != 0:
        raise SwebenchError("container teardown failed")
    observed_container, stdout, stderr = invoke((docker, "container", "inspect", name))
    if not confirms_absence(
        observed_container,
        stdout,
        stderr,
        resource="container",
        expected_name=name,
    ):
        raise SwebenchError("container teardown could not be verified")
    volume_absent = True
    if workspace_volume is not None:
        removed, _stdout, _stderr = invoke((docker, "volume", "rm", "-f", workspace_volume))
        if removed.returncode != 0:
            raise SwebenchError("container workspace teardown failed")
        observed_volume, stdout, stderr = invoke((docker, "volume", "inspect", workspace_volume))
        volume_absent = confirms_absence(
            observed_volume,
            stdout,
            stderr,
            resource="volume",
            expected_name=workspace_volume,
        )
        if not volume_absent:
            raise SwebenchError("container workspace teardown could not be verified")
    return True, volume_absent


def _attest_container(
    runtime: SwebenchRuntime,
    name: str,
    limits: Mapping[str, Any],
    *,
    role: str,
    image_reference: str | None = None,
    image_digest: str | None = None,
    network_mode: str = "none",
) -> None:
    if network_mode != "none" and role != "grader":
        raise SwebenchError("only a grader may join a verified egress gateway namespace")
    inspected = _run(
        runtime,
        (str(runtime.docker_executable), "inspect", name),
        timeout=30,
        max_output_bytes=cast(int, limits["max_output_bytes"]),
    )
    try:
        item = json.loads(inspected.stdout)[0]
        config = item["Config"]
        host = item["HostConfig"]
        mounts = item.get("Mounts", [])
    except (TypeError, IndexError, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SwebenchError("container attestation is invalid") from exc
    expected_env = {"HOME=/tmp/home", "PATH=/usr/local/bin:/usr/bin:/bin"}
    observed_env = config.get("Env", [])
    environment_ok = (
        isinstance(observed_env, list)
        and all(isinstance(value, str) for value in observed_env)
        and len(observed_env) == len(set(observed_env))
        and set(observed_env) in (expected_env, expected_env | {"TZ=Etc/UTC"})
    )
    common_ok = (
        config.get("User") == "65532:65532"
        and environment_ok
        and not config.get("ExposedPorts")
        and host.get("NetworkMode") == network_mode
        and host.get("ReadonlyRootfs") is True
        and host.get("Privileged") is False
        and host.get("CapDrop") == ["ALL"]
        and "no-new-privileges" in host.get("SecurityOpt", [])
        and host.get("PidsLimit") == limits["pids_limit"]
        and host.get("Memory") == limits["memory_bytes"]
        and not host.get("Binds")
        and not host.get("PortBindings")
    )
    if not common_ok:
        raise SwebenchError("container attestation does not match the safety contract")
    if role in {"task", "grader"}:
        expected_name = f"{name}-workspace"
        if len(mounts) != 1 or not all(
            (
                mounts[0].get("Type") == "volume",
                mounts[0].get("Name") == expected_name,
                mounts[0].get("Destination") == "/testbed",
                mounts[0].get("RW") is True,
            )
        ):
            raise SwebenchError(f"{role} workspace volume attestation failed")
        if (image_reference is None) != (image_digest is None):
            raise SwebenchError("container image identity is incomplete")
        if image_reference is not None and image_digest is not None:
            expected_reference, expected_digest = _validate_image(
                image_reference, image_digest, f"{role}_image"
            )
            if config.get("Image") != expected_reference or item.get("Image") != expected_digest:
                raise SwebenchError(f"{role} container image identity drifted")
            inspected_image = _run(
                runtime,
                (str(runtime.docker_executable), "image", "inspect", expected_reference),
                timeout=30,
                max_output_bytes=8192,
            )
            try:
                image_info = json.loads(inspected_image.stdout)
                if (
                    not isinstance(image_info, list)
                    or len(image_info) != 1
                    or image_info[0].get("Id") != expected_digest
                    or image_info[0].get("Os") != "linux"
                    or image_info[0].get("Architecture") != "amd64"
                ):
                    raise SwebenchError(f"{role} image digest or platform attestation failed")
            except (
                AttributeError,
                IndexError,
                TypeError,
                UnicodeDecodeError,
                json.JSONDecodeError,
            ) as exc:
                raise SwebenchError(f"{role} image attestation is invalid") from exc
    else:
        raise SwebenchError("container role is invalid")


def _validated_agent_telemetry(value: Mapping[str, object]) -> dict[str, Any]:
    telemetry: dict[str, Any] = {}
    for key in ("output_tokens", "prompt_tokens", "context_length"):
        field_value = value.get(key)
        if field_value is not None and (type(field_value) is not int or field_value < 0):
            raise SwebenchError("agent telemetry is invalid")
        telemetry[key] = field_value

    finish_reason = value.get("finish_reason")
    if finish_reason is not None and (type(finish_reason) is not str or not finish_reason):
        raise SwebenchError("agent telemetry is invalid")
    telemetry["finish_reason"] = finish_reason

    context_truncation_status = value.get("context_truncation_status")
    if context_truncation_status is not None and (
        type(context_truncation_status) is not str
        or context_truncation_status not in _CONTEXT_TRUNCATION_STATUSES
    ):
        raise SwebenchError("agent telemetry is invalid")
    telemetry["context_truncation_status"] = context_truncation_status

    truncation_status = value.get("truncation_status")
    if truncation_status is not None and (
        type(truncation_status) is not str or truncation_status not in _TRUNCATION_STATUSES
    ):
        raise SwebenchError("agent telemetry is invalid")
    telemetry["truncation_status"] = truncation_status

    elapsed = value.get("trajectory_elapsed_seconds")
    if elapsed is not None:
        if not isinstance(elapsed, (int, float)) or isinstance(elapsed, bool) or elapsed < 0:
            raise SwebenchError("agent telemetry is invalid")
        try:
            finite_elapsed = math.isfinite(elapsed)
        except OverflowError:
            finite_elapsed = False
        if not finite_elapsed:
            raise SwebenchError("agent telemetry is invalid")
    telemetry["trajectory_elapsed_seconds"] = elapsed
    return telemetry


def _validate_agent_result(
    result: Mapping[str, object], limits: Mapping[str, Any]
) -> dict[str, Any]:
    expected = {
        "status",
        "attempted",
        "first_verified_response",
        "turn_count",
        "request_count",
        "output_tokens",
        "prompt_tokens",
        "finish_reason",
        "context_length",
        "context_truncation_status",
        "truncation_status",
        "trajectory_elapsed_seconds",
        "locality_checks",
        "bash_actions_only",
        "effective_step_limit",
        "effective_wall_time_limit_seconds",
        "trajectory_sha256",
        "model_patch_sha256",
        "prediction_handle",
        "host_response_receipt",
    }
    if (
        set(result) != expected
        or result.get("status") not in {"completed", "model_failure", "infrastructure_error"}
        or not isinstance(result.get("attempted"), bool)
        or not isinstance(result.get("first_verified_response"), bool)
    ):
        raise SwebenchError("agent result is incomplete")
    turns = result.get("turn_count")
    requests = result.get("request_count")
    tokens = result.get("output_tokens")
    if not all(
        isinstance(value, int) and not isinstance(value, bool)
        for value in (turns, requests, tokens)
    ):
        raise SwebenchError("agent budgets are invalid")
    receipt = result.get("host_response_receipt")
    if receipt is not None:
        receipt = _validated_host_response_receipt(receipt)
    minimum = 1 if result["status"] == "completed" else 0
    if not minimum <= cast(int, turns) <= cast(int, limits["max_turns"]) or not minimum <= cast(
        int, requests
    ) <= cast(int, limits["max_requests"]):
        raise SwebenchError("agent turn or request budget was exceeded")
    effective_step_limit = result.get("effective_step_limit")
    effective_wall_time_limit = result.get("effective_wall_time_limit_seconds")
    if (
        type(effective_step_limit) is not int
        or effective_step_limit != limits["max_turns"]
        or type(effective_wall_time_limit) is not int
        or effective_wall_time_limit != limits["timeout_seconds"]
    ):
        raise SwebenchError("effective runner limits do not match the frozen profile")
    if result.get("locality_checks") != turns or result.get("bash_actions_only") is not True:
        raise SwebenchError("agent adapter did not preserve locality or bash-only execution")
    if cast(int, tokens) > cast(int, limits["max_task_output_tokens"]):
        raise SwebenchError("agent output token budget was exceeded")
    if not _is_sha256(result.get("trajectory_sha256")):
        raise SwebenchError("agent trajectory fingerprint is invalid")
    if result["status"] == "completed":
        handle = result.get("prediction_handle")
        if (
            not _is_sha256(result.get("model_patch_sha256"))
            or not isinstance(handle, str)
            or handle != f"private://prediction-{result['model_patch_sha256']}.patch"
        ):
            raise SwebenchError("agent prediction evidence is invalid")
    elif (
        result.get("model_patch_sha256") is not None or result.get("prediction_handle") is not None
    ):
        raise SwebenchError("failed model attempts may not claim prediction evidence")
    validated = dict(result)
    validated.update(_validated_agent_telemetry(result))
    validated["host_response_receipt"] = receipt
    return validated


def _checkpoint_payload(
    prepared: _Prepared,
    task: _Task,
    index: int,
    *,
    attempt_nonce: str,
    state: str,
    attempted: bool,
    status: str,
    control_image_digest: str | None = None,
    control_container_name: str | None = None,
    control_identity_sha256: str | None = None,
    control_container_id: str | None = None,
    host_response_receipt: Mapping[str, Any] | None = None,
    output_tokens: int | None = None,
    prompt_tokens: int | None = None,
    finish_reason: str | None = None,
    context_length: int | None = None,
    context_truncation_status: str | None = None,
    truncation_status: str | None = None,
    trajectory_elapsed_seconds: float | int | None = None,
    trajectory_sha256: str | None = None,
    model_patch_sha256: str | None = None,
    grader_evidence_sha256: str | None = None,
    grade: str | None = None,
    sdk_budget: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if state not in _CHECKPOINT_STATES:
        raise SwebenchError("checkpoint state is invalid")
    if not isinstance(attempted, bool):
        raise SwebenchError("checkpoint attempted flag is invalid")
    receipt = (
        _validated_host_response_receipt(host_response_receipt)
        if host_response_receipt is not None
        else None
    )
    telemetry = _validated_agent_telemetry(
        {
            "output_tokens": output_tokens,
            "prompt_tokens": prompt_tokens,
            "finish_reason": finish_reason,
            "context_length": context_length,
            "context_truncation_status": context_truncation_status,
            "truncation_status": truncation_status,
            "trajectory_elapsed_seconds": trajectory_elapsed_seconds,
        }
    )
    control = _checkpoint_control_fields(
        task_fingerprint_sha256=_task_fingerprint(task),
        attempt_nonce=attempt_nonce,
        control_image_digest=control_image_digest,
        control_container_name=control_container_name,
        control_identity_sha256=control_identity_sha256,
        control_container_id=control_container_id,
    )
    return {
        "schema_version": _CHECKPOINT_SCHEMA_VERSION,
        "sdk_budget": _sdk_budget_state(sdk_budget),
        "task_index": index,
        "instance_id_sha256": _sha256(task.instance_id.encode()),
        "task_fingerprint_sha256": _task_fingerprint(task),
        "image_binding_sha256": task.binding_sha256,
        "manifest_sha256": prepared.manifest_sha256,
        "protocol_fingerprint": prepared.protocol_fingerprint,
        "attempt_nonce": _nonblank(attempt_nonce, "attempt nonce"),
        **control,
        "state": state,
        "attempted": attempted,
        "status": status,
        "host_response_receipt": receipt,
        **telemetry,
        "trajectory_sha256": trajectory_sha256,
        "model_patch_sha256": model_patch_sha256,
        "grader_evidence_sha256": grader_evidence_sha256,
        "grade": grade,
    }


def _recover_control_checkpoint(
    checkpoint: Mapping[str, Any],
    *,
    checkpoint_path: Path,
    prepared: _Prepared,
    runtime: SwebenchRuntime,
) -> None:
    recover_verified_response = (
        checkpoint.get("state") == "response_verified"
        and checkpoint.get("attempted") is True
        and checkpoint.get("status") == "response_verified"
        and checkpoint.get("grader_evidence_sha256") is None
        and checkpoint.get("grade") is None
    )
    if recover_verified_response:
        task_index = checkpoint.get("task_index")
        if type(task_index) is not int or not 0 <= task_index < len(prepared.tasks):
            raise SwebenchError("response checkpoint task index is invalid for recovery")
        task = prepared.tasks[task_index]
        served_fingerprint = cast(Mapping[str, Any], prepared.profile["model_runtime"])[
            "served_model_fingerprint"
        ]
        _validated_host_response_receipt(
            checkpoint.get("host_response_receipt"),
            expected_task_instance_id_sha256=_sha256(task.instance_id.encode()),
            expected_task_fingerprint=_task_fingerprint(task),
            expected_served_model_fingerprint=cast(str, served_fingerprint),
        )
    control_image_digest = checkpoint.get("control_image_digest")
    control_container_name = checkpoint.get("control_container_name")
    control_identity_sha256 = checkpoint.get("control_identity_sha256")
    control_fields = (control_image_digest, control_container_name, control_identity_sha256)
    if all(value is None for value in control_fields):
        if recover_verified_response:
            raise SwebenchError("response checkpoint has no durable control-container identity")
        return
    if any(value is None for value in control_fields):
        raise SwebenchError("checkpoint has no durable control-container identity")
    _current_reference, current_digest = _control_image_identity()
    if current_digest != control_image_digest:
        raise SwebenchError("control image binding drifted before checkpoint recovery")
    limits = cast(Mapping[str, Any], prepared.profile["resource_limits"])
    _cleanup_control_container(
        {
            "control_image_digest": control_image_digest,
            "control_container_name": control_container_name,
            "control_identity_sha256": control_identity_sha256,
            "control_container_id": checkpoint.get("control_container_id"),
            "control_id_required": True,
            "task_fingerprint_sha256": checkpoint.get("task_fingerprint_sha256"),
            "attempt_nonce": checkpoint.get("attempt_nonce"),
            "docker_executable": str(runtime.docker_executable),
            "evidence_dir": checkpoint_path.parent,
            "max_output_bytes": limits["max_output_bytes"],
            "runner": runtime.runner or _default_runner(),
            "timeout_seconds": limits["timeout_seconds"],
        }
    )
    if recover_verified_response:
        recovered = dict(checkpoint)
        recovered["state"] = "terminal"
        recovered["status"] = "infrastructure_error"
        _write_private_json(checkpoint_path, recovered)


def _task_checkpoint(
    path: Path,
    *,
    prepared: _Prepared,
    task: _Task,
    index: int,
    reconcile_control: Callable[[Mapping[str, Any]], None] | None = None,
) -> Mapping[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = _mapping(json.loads(path.read_bytes()), "checkpoint")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SwebenchError("private checkpoint is invalid") from exc
    _exact_keys(value, _CHECKPOINT_KEYS, "private checkpoint")
    telemetry = _validated_agent_telemetry(value)
    if value.get("schema_version") != _CHECKPOINT_SCHEMA_VERSION:
        raise SwebenchError("private checkpoint schema is unsupported")
    if value.get("task_index") != index:
        raise SwebenchError("private checkpoint task index drifted")
    if value.get("instance_id_sha256") != _sha256(task.instance_id.encode()):
        raise SwebenchError("private checkpoint instance binding drifted")
    if value.get("task_fingerprint_sha256") != _task_fingerprint(task):
        raise SwebenchError("private checkpoint task fingerprint drifted")
    if value.get("image_binding_sha256") != task.binding_sha256:
        raise SwebenchError("private checkpoint image binding drifted")
    if (
        value.get("manifest_sha256") != prepared.manifest_sha256
        or value.get("protocol_fingerprint") != prepared.protocol_fingerprint
    ):
        raise SwebenchError("private checkpoint protocol binding drifted")
    attempt_nonce = _nonblank(value.get("attempt_nonce"), "checkpoint attempt nonce")
    sdk_budget = _sdk_budget_state(value.get("sdk_budget"))
    _checkpoint_control_fields(
        task_fingerprint_sha256=value.get("task_fingerprint_sha256"),
        attempt_nonce=attempt_nonce,
        control_image_digest=value.get("control_image_digest"),
        control_container_name=value.get("control_container_name"),
        control_identity_sha256=value.get("control_identity_sha256"),
        control_container_id=value.get("control_container_id"),
    )
    state = value.get("state")
    attempted = value.get("attempted")
    status = value.get("status")
    receipt = value.get("host_response_receipt")
    if state not in _CHECKPOINT_STATES or not isinstance(attempted, bool):
        raise SwebenchError("private checkpoint state is invalid")
    if not attempted and any(field_value is not None for field_value in telemetry.values()):
        raise SwebenchError("private checkpoint has telemetry without a verified response")
    if state == "invalid":
        if status != "infrastructure_error" or value.get("grade") is not None:
            raise SwebenchError("private checkpoint invalidation state is invalid")
        if receipt is not None:
            served_fingerprint = cast(Mapping[str, Any], prepared.profile["model_runtime"])[
                "served_model_fingerprint"
            ]
            _validated_host_response_receipt(
                receipt,
                expected_task_instance_id_sha256=_sha256(task.instance_id.encode()),
                expected_task_fingerprint=_task_fingerprint(task),
                expected_served_model_fingerprint=cast(str, served_fingerprint),
            )
        raise SwebenchError("private checkpoint is invalid after cleanup failure")
    elif state in {"in_flight", "ambiguous"}:
        if (
            attempted is not False
            or receipt is not None
            or (state == "ambiguous" and status != "infrastructure_error")
        ):
            raise SwebenchError("private checkpoint state is invalid")
        if state == "in_flight" and status != "in_flight":
            raise SwebenchError("private checkpoint state is invalid")
    else:
        if not attempted:
            raise SwebenchError("private checkpoint terminal state is invalid")
        served_fingerprint = cast(Mapping[str, Any], prepared.profile["model_runtime"])[
            "served_model_fingerprint"
        ]
        _validated_host_response_receipt(
            receipt,
            expected_task_instance_id_sha256=_sha256(task.instance_id.encode()),
            expected_task_fingerprint=_task_fingerprint(task),
            expected_served_model_fingerprint=cast(str, served_fingerprint),
        )
        if state == "response_verified":
            if status != "response_verified":
                raise SwebenchError("private checkpoint response state is invalid")
            if any(value.get(key) is not None for key in ("grader_evidence_sha256", "grade")):
                raise SwebenchError("response checkpoint claims grader evidence")
            for key in ("trajectory_sha256", "model_patch_sha256"):
                if value.get(key) is not None and not _is_sha256(value.get(key)):
                    raise SwebenchError("private checkpoint response evidence is invalid")
        elif state not in {"terminal", "cleanup_pending"} or status not in {
            "completed",
            "model_failure",
            "infrastructure_error",
        }:
            raise SwebenchError("private checkpoint terminal state is invalid")
        elif status == "infrastructure_error" and value.get("trajectory_sha256") is None:
            if any(item is not None for item in telemetry.values()) or any(
                value.get(key) is not None
                for key in ("model_patch_sha256", "grader_evidence_sha256", "grade")
            ):
                raise SwebenchError("failed checkpoint claims unvalidated agent evidence")
        elif not _is_sha256(value.get("trajectory_sha256")):
            raise SwebenchError("private checkpoint trajectory fingerprint is invalid")
        elif status == "completed":
            if not _is_sha256(value.get("model_patch_sha256")):
                raise SwebenchError("private checkpoint prediction fingerprint is invalid")
            if not _is_sha256(value.get("grader_evidence_sha256")) or value.get("grade") not in {
                "resolved",
                "unresolved",
            }:
                raise SwebenchError("private checkpoint grader evidence is invalid")
        elif status == "model_failure":
            if (
                value.get("model_patch_sha256") is not None
                or value.get("grader_evidence_sha256") is not None
                or value.get("grade") is not None
            ):
                raise SwebenchError("model-failure checkpoint claims unsupported evidence")
        else:
            if value.get("model_patch_sha256") is not None and not _is_sha256(
                value.get("model_patch_sha256")
            ):
                raise SwebenchError("private checkpoint prediction fingerprint is invalid")
            if value.get("grader_evidence_sha256") is not None and not _is_sha256(
                value.get("grader_evidence_sha256")
            ):
                raise SwebenchError("private checkpoint grader evidence is invalid")
            if value.get("grade") is not None:
                raise SwebenchError("failed checkpoint may not claim a grade")
    if state == "response_verified" and reconcile_control is not None:
        reconcile_control(value)
        return _task_checkpoint(
            path,
            prepared=prepared,
            task=task,
            index=index,
        )
    sdk_stopped = sdk_budget is not None and sdk_budget["state"] != "completed"
    stopped_infrastructure_failure = (
        sdk_stopped
        and status == "infrastructure_error"
        and (state == "terminal" or (state == "ambiguous" and receipt is None))
        and value.get("grader_evidence_sha256") is None
        and value.get("grade") is None
    )
    if sdk_stopped and state == "in_flight" and reconcile_control is not None:
        # A cut-off inference with no verified response: account it as an infrastructure
        # error. Never retried, and the reserved output budget stays charged.
        reconcile_control(value)
        recovered = dict(value)
        recovered["state"] = "ambiguous"
        recovered["status"] = "infrastructure_error"
        _write_private_json(path, recovered)
        return _task_checkpoint(path, prepared=prepared, task=task, index=index)
    if sdk_stopped and not stopped_infrastructure_failure:
        if reconcile_control is not None:
            reconcile_control(value)
        raise _SdkTransportStopped("SDK checkpoint requires reconciliation; no retry or refund")
    if stopped_infrastructure_failure:
        return value
    if (
        state == "ambiguous"
        and reconcile_control is not None
        and sdk_budget is None
        and receipt is None
        and attempted is False
    ):
        # No SDK reservation means no request ever reached the model, so running the task
        # now is its first attempt, not a retry. Keep the old record as evidence.
        reconcile_control(value)
        path.rename(path.with_name(f"never-attempted-{path.stem}-{attempt_nonce}.json"))
        return None
    if state != "terminal":
        if reconcile_control is not None:
            reconcile_control(value)
        raise SwebenchError("task checkpoint requires reconciliation before resume")
    return value


def _mark_cleanup_invalid(
    checkpoint_path: Path,
    *,
    prepared: _Prepared,
    task: _Task,
    index: int,
    attempt_nonce: str,
    run_dir: Path,
    errors: Sequence[SwebenchError],
) -> None:
    try:
        current = _mapping(json.loads(checkpoint_path.read_bytes()), "checkpoint")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SwebenchError("cleanup failure could not inspect the task checkpoint") from exc

    def optional_sha256(key: str) -> str | None:
        value = current.get(key)
        return value if isinstance(value, str) and _is_sha256(value) else None

    receipt_value = current.get("host_response_receipt")
    receipt = cast(Mapping[str, Any], receipt_value) if isinstance(receipt_value, Mapping) else None
    control_fields = {
        key: current.get(key)
        for key in (
            "control_image_digest",
            "control_container_name",
            "control_identity_sha256",
            "control_container_id",
        )
    }
    invalid = _checkpoint_payload(
        prepared,
        task,
        index,
        attempt_nonce=attempt_nonce,
        state="invalid",
        attempted=current.get("attempted") is True,
        status="infrastructure_error",
        **control_fields,
        **_validated_agent_telemetry(current),
        host_response_receipt=receipt,
        trajectory_sha256=optional_sha256("trajectory_sha256"),
        model_patch_sha256=optional_sha256("model_patch_sha256"),
        grader_evidence_sha256=None,
        grade=None,
        sdk_budget=cast(Mapping[str, Any] | None, current.get("sdk_budget")),
    )
    _write_private_json(checkpoint_path, invalid)
    _write_private_json(
        run_dir / "invalid.json",
        {
            "schema_version": 1,
            "reason": "cleanup_failed",
            "checkpoint": checkpoint_path.name,
            "manifest_sha256": prepared.manifest_sha256,
            "protocol_fingerprint": prepared.protocol_fingerprint,
            "cleanup_error_count": len(errors),
        },
    )


def _persist_grader_capture(
    raw_capture: Mapping[str, object],
    *,
    task: _Task,
    prediction_sha256: str,
    eval_script_sha256: str,
    attempt_nonce: str,
    run_dir: Path,
    limits: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist host-received grader bytes without accepting a container verdict."""

    if set(raw_capture) != _GRADER_CAPTURE_KEYS or raw_capture.get("schema_version") != 1:
        raise SwebenchError("grader capture schema is invalid")
    for key, expected in (
        ("patch_sha256", prediction_sha256),
        ("eval_script_sha256", eval_script_sha256),
    ):
        if raw_capture.get(key) != expected:
            raise SwebenchError(f"grader capture {key} is not bound to host inputs")
    integer_fields = (
        "launcher_exit_code",
        "process_exit_code",
        "eval_launcher_exit_code",
        "eval_exit_code",
    )
    for key in integer_fields:
        value = raw_capture.get(key)
        if value is not None and type(value) is not int:
            raise SwebenchError(f"grader capture {key} is invalid")
    if not isinstance(raw_capture.get("timed_out"), bool):
        raise SwebenchError("grader capture timeout flag is invalid")
    stdout = raw_capture.get("stdout")
    stderr = raw_capture.get("stderr")
    if not isinstance(stdout, bytes) or not isinstance(stderr, bytes):
        raise SwebenchError("grader capture streams must be raw bytes")
    error = raw_capture.get("error")
    if error is not None and (
        not isinstance(error, str) or re.fullmatch(r"[a-z0-9_]{1,64}", error) is None
    ):
        raise SwebenchError("grader capture error code is invalid")
    maximum = cast(int, limits["max_output_bytes"])
    capture_limit = min(maximum, 4 * 1024 * 1024 - 1024)
    if len(stdout) + len(stderr) > capture_limit:
        raise SwebenchError("grader capture exceeds the official scorer log bound")
    launcher_exit_code = raw_capture["launcher_exit_code"]
    process_exit_code = raw_capture["process_exit_code"]
    eval_launcher_exit_code = raw_capture["eval_launcher_exit_code"]
    eval_exit_code = raw_capture["eval_exit_code"]
    timed_out = cast(bool, raw_capture["timed_out"])
    phase = raw_capture.get("phase")
    if phase not in {"patch_ready", "apply_failed"}:
        raise SwebenchError("grader capture phase is invalid")
    if (
        launcher_exit_code is not None
        and process_exit_code is not None
        and launcher_exit_code != process_exit_code
    ):
        raise SwebenchError("grader launcher and entrypoint exit codes differ")
    if timed_out and (eval_exit_code is not None or eval_launcher_exit_code is not None):
        raise SwebenchError("timed-out grader may not claim an eval exit receipt")
    if eval_exit_code is not None and eval_launcher_exit_code != eval_exit_code:
        raise SwebenchError("host eval exit does not match the Docker CLI receipt")
    if error is None and (
        timed_out
        or phase != "patch_ready"
        or launcher_exit_code != 0
        or process_exit_code != 0
        or eval_launcher_exit_code is None
        or eval_exit_code is None
        or not 0 <= cast(int, eval_exit_code) <= 255
        or eval_exit_code in {125, 126, 127}
        or stderr
        or _TEST_EXIT_CODE_RE.search(stdout) is None
    ):
        raise SwebenchError("successful grader capture has incomplete host process evidence")

    sanitized_stdout = _TEST_EXIT_CODE_RE.sub(b">>>>> Test Exit Code [host-neutralized]", stdout)
    # The eval script emits its own start/end markers, and the official parser slices
    # from the first of each; a leading marker here would slice setup output instead.
    log = sanitized_stdout + b"\n>>>>> End Test Output\n"
    if error is None and eval_exit_code is not None and not timed_out:
        log += f">>>>> Test Exit Code: {eval_exit_code}\n".encode("ascii")
    nonce_sha = _sha256(task.instance_id.encode() + b"\0" + attempt_nonce.encode())
    log_name = f"grader-log-{_sha256(log)}.txt"
    log_path = run_dir / log_name
    if log_path.exists():
        try:
            existing_metadata = log_path.lstat()
            existing_path = secure_resolve(log_path, must_exist=True)
            if (
                not stat.S_ISREG(existing_metadata.st_mode)
                or existing_path.parent != secure_resolve(run_dir, must_exist=True)
                or existing_metadata.st_mode & 0o077
                or existing_path.read_bytes() != log
            ):
                raise SwebenchError("existing host grading log is not identical private evidence")
        except OSError as exc:
            raise SwebenchError("existing host grading log is unavailable") from exc
    else:
        _write_private_bytes(log_path, log)
    capture_header = {
        "schema_version": 1,
        "instance_id_sha256": _sha256(task.instance_id.encode()),
        "prediction_sha256": prediction_sha256,
        "eval_script_sha256": eval_script_sha256,
        "attempt_nonce_sha256": nonce_sha,
        "phase": phase,
        "launcher_exit_code": launcher_exit_code,
        "process_exit_code": process_exit_code,
        "eval_launcher_exit_code": eval_launcher_exit_code,
        "eval_exit_code": eval_exit_code,
        "timed_out": timed_out,
        "error": error,
        "stdout_bytes": len(stdout),
        "stderr_bytes": len(stderr),
        "test_log_sha256": _sha256(log),
    }
    capture_bytes = (
        b"RACECRAFT-HOST-GRADER-CAPTURE-v2\0"
        + _canonical(capture_header)
        + b"\nstdout\0"
        + stdout
        + b"\nstderr\0"
        + stderr
        + b"\ntest-log\0"
        + log
    )
    evidence_name = f"grader-capture-{nonce_sha}.bin"
    evidence_path = run_dir / evidence_name
    _write_private_bytes(evidence_path, capture_bytes)
    return {
        "phase": phase,
        "prediction_sha256": prediction_sha256,
        "eval_script_sha256": eval_script_sha256,
        "grader_evidence_sha256": _sha256(capture_bytes),
        "test_log_path": log_name,
        "eval_exit_code": eval_exit_code,
        "timed_out": timed_out,
        "capture_error": error,
    }


def _default_score_executor(
    trusted_run: Any,
    *,
    prepared: _Prepared,
    runtime: SwebenchRuntime,
) -> _ScorerResult:
    test_spec = getattr(trusted_run, "test_spec", None)
    if not isinstance(test_spec, _ScorerTestSpec):
        raise SwebenchError("trusted scorer received an unbound TestSpec")
    instance_id = _nonblank(getattr(trusted_run, "instance_id", None), "instance_id")
    if instance_id != test_spec.instance_id or getattr(trusted_run, "cleanup_ok", None) is not True:
        raise SwebenchError("trusted scorer input is not bound to cleaned-up task evidence")
    patch = getattr(trusted_run, "model_patch", None)
    patch_sha256 = getattr(trusted_run, "patch_sha256", None)
    if not isinstance(patch, str):
        raise SwebenchError("trusted scorer patch is invalid")
    patch_bytes = patch.encode("utf-8")
    if (
        len(patch_bytes) > _SCORER_PATCH_LIMIT
        or not _is_sha256(patch_sha256)
        or _sha256(patch_bytes) != patch_sha256
    ):
        raise SwebenchError("trusted scorer patch hash or size is invalid")
    log_path = getattr(trusted_run, "test_log_path", None)
    if not isinstance(log_path, Path):
        raise SwebenchError("trusted scorer grading log path is invalid")
    try:
        grading_log = log_path.read_bytes()
    except OSError as exc:
        raise SwebenchError("trusted scorer grading log is unavailable") from exc
    if len(grading_log) > _SCORER_LOG_LIMIT:
        raise SwebenchError("trusted scorer grading log exceeds its byte bound")
    log_match = re.fullmatch(r"grader-log-([0-9a-f]{64})\.txt", log_path.name)
    grading_log_sha256 = _sha256(grading_log)
    if log_match is None or log_match.group(1) != grading_log_sha256:
        raise SwebenchError("trusted scorer grading log hash is invalid")
    request = {
        "schema_version": 1,
        "action": "score",
        "swebench_version": SWEBENCH_VERSION,
        "instance_id": instance_id,
        "test_spec": dict(test_spec.fields),
        "test_spec_sha256": test_spec.test_spec_sha256,
        "eval_script_sha256": test_spec.eval_script_sha256,
        "patch": patch,
        "patch_sha256": cast(str, patch_sha256),
        "grading_log_b64": base64.b64encode(grading_log).decode("ascii"),
        "grading_log_sha256": grading_log_sha256,
        "host_cleanup_confirmed": True,
    }
    response = _invoke_trusted_scorer(
        request,
        prepared=prepared,
        runtime=runtime,
        task=test_spec,
        action="score",
        test_spec_sha256=test_spec.test_spec_sha256,
    )
    return _validate_scorer_response(
        response,
        action="score",
        test_spec=test_spec,
        patch_sha256=cast(str, patch_sha256),
        grading_log_sha256=grading_log_sha256,
    )


def _is_patch_failure_response(
    raw: bytes, *, patch_sha256: object, eval_script_sha256: object
) -> bool:
    """Accept only the entrypoint's own attested verdict that the patch did not apply."""

    try:
        response = _mapping(json.loads(raw), "grader entrypoint response")
    except (UnicodeDecodeError, json.JSONDecodeError, SwebenchError):
        return False
    return (
        set(response) == _GRADER_ENTRYPOINT_RESPONSE_KEYS
        and response["schema_version"] == 1
        and response["phase"] == "apply_failed"
        and response["process_exit_code"] == _GRADER_EXIT_PATCH_FAILED
        and response["eval_exit_code"] is None
        and response["timed_out"] is False
        and response["error"] == _GRADER_PATCH_FAILED
        and response["patch_sha256"] == patch_sha256
        and response["eval_script_sha256"] == eval_script_sha256
    )


def _read_rescore_capture(
    *,
    task: _Task,
    checkpoint: Mapping[str, Any],
    test_spec: _ScorerTestSpec,
    run_dir: Path,
    max_output_bytes: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate immutable grader evidence before an offline scorer replay."""

    try:
        root_info = run_dir.lstat()
        root = run_dir.resolve(strict=True)
    except OSError as exc:
        raise SwebenchError("private run directory is unavailable for rescore") from exc
    if not stat.S_ISDIR(root_info.st_mode) or root_info.st_mode & 0o077:
        raise SwebenchError("private run directory permissions are invalid for rescore")

    def read_private(name: str, label: str, maximum: int) -> bytes:
        path = run_dir / name
        try:
            info = path.lstat()
            resolved = secure_resolve(path, must_exist=True)
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or resolved.parent != root:
                raise SwebenchError(f"{label} is not owner-only evidence in the run directory")
            data = resolved.read_bytes()
        except (OSError, ConfigurationError) as exc:
            raise SwebenchError(f"{label} is unavailable for rescore") from exc
        if len(data) > maximum:
            raise SwebenchError(f"{label} exceeds its rescore byte bound")
        return data

    patch_sha256 = checkpoint.get("model_patch_sha256")
    if not _is_sha256(patch_sha256):
        raise SwebenchError("terminal checkpoint has no prediction fingerprint for rescore")
    handle = f"private://prediction-{patch_sha256}.patch"
    patch_bytes = read_private(
        f"prediction-{patch_sha256}.patch", "private prediction", _SCORER_PATCH_LIMIT
    )
    if _sha256(patch_bytes) != patch_sha256:
        raise SwebenchError("private prediction digest does not match its terminal checkpoint")
    try:
        patch_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise SwebenchError("private prediction is not UTF-8 for rescore") from exc

    attempt_nonce = _nonblank(checkpoint.get("attempt_nonce"), "checkpoint attempt nonce")
    nonce_sha256 = _sha256(task.instance_id.encode() + b"\0" + attempt_nonce.encode())
    capture_name = f"grader-capture-{nonce_sha256}.bin"
    capture_bytes = read_private(
        capture_name, "private grader capture", 2 * _SCORER_LOG_LIMIT + 65_536
    )
    if _sha256(capture_bytes) != checkpoint.get("grader_evidence_sha256"):
        raise SwebenchError("private grader capture digest does not match its terminal checkpoint")

    prefix = b"RACECRAFT-HOST-GRADER-CAPTURE-v2\0"
    if not capture_bytes.startswith(prefix):
        raise SwebenchError("private grader capture framing is invalid")
    framed = capture_bytes[len(prefix) :]
    stdout_marker = b"\nstdout\0"
    header_end = framed.find(stdout_marker)
    if header_end <= 0 or header_end > 16_384:
        raise SwebenchError("private grader capture header framing is invalid")
    header_bytes = framed[:header_end]
    try:
        header = _mapping(json.loads(header_bytes), "private grader capture header")
        if _canonical(header) != header_bytes:
            raise SwebenchError("private grader capture header is not canonical")
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise SwebenchError("private grader capture header is invalid") from exc
    header_keys = {
        "schema_version",
        "instance_id_sha256",
        "prediction_sha256",
        "eval_script_sha256",
        "attempt_nonce_sha256",
        "phase",
        "launcher_exit_code",
        "process_exit_code",
        "eval_launcher_exit_code",
        "eval_exit_code",
        "timed_out",
        "error",
        "stdout_bytes",
        "stderr_bytes",
        "test_log_sha256",
    }
    _exact_keys(header, header_keys, "private grader capture header")
    patch_failed = header.get("error") == _GRADER_PATCH_FAILED
    if (
        header.get("schema_version") != 1
        or header.get("instance_id_sha256") != _sha256(task.instance_id.encode())
        or header.get("prediction_sha256") != patch_sha256
        # Execution grades with the frozen execution script, so captures record its hash.
        or header.get("eval_script_sha256") != test_spec.execution_eval_script_sha256
        or header.get("attempt_nonce_sha256") != nonce_sha256
        or header.get("phase") != ("apply_failed" if patch_failed else "patch_ready")
        or header.get("timed_out") is not False
        or header.get("error") not in {None, _GRADER_PATCH_FAILED}
        or not _is_sha256(header.get("test_log_sha256"))
    ):
        raise SwebenchError("private grader capture identity is not bound to this task attempt")
    stream_lengths = (header.get("stdout_bytes"), header.get("stderr_bytes"))
    if any(type(length) is not int or length < 0 for length in stream_lengths):
        raise SwebenchError("private grader capture stream lengths are invalid")
    if sum(cast(tuple[int, int], stream_lengths)) > min(max_output_bytes, 4 * 1024 * 1024 - 1024):
        raise SwebenchError("private grader capture streams exceed the frozen output limit")

    integers = (
        "launcher_exit_code",
        "process_exit_code",
        "eval_launcher_exit_code",
        "eval_exit_code",
    )
    if patch_failed and (
        header["launcher_exit_code"] != _GRADER_EXIT_PATCH_FAILED
        or header["process_exit_code"] != _GRADER_EXIT_PATCH_FAILED
        or header["eval_launcher_exit_code"] is not None
        or header["eval_exit_code"] is not None
    ):
        raise SwebenchError("patch-failure capture has inconsistent exit evidence")
    for name in integers if not patch_failed else ():
        value = header.get(name)
        if type(value) is not int or not 0 <= value <= 255:
            raise SwebenchError("private grader capture exit evidence is invalid")
    if not patch_failed and (
        header["launcher_exit_code"] != 0
        or header["process_exit_code"] != 0
        or header["eval_launcher_exit_code"] != header["eval_exit_code"]
        or header["eval_exit_code"] in {125, 126, 127}
    ):
        raise SwebenchError("completed checkpoint has unsuccessful grader process evidence")

    cursor = header_end + len(stdout_marker)
    stdout_length, stderr_length = cast(tuple[int, int], stream_lengths)
    stdout = framed[cursor : cursor + stdout_length]
    cursor += stdout_length
    stderr_marker = b"\nstderr\0"
    if framed[cursor : cursor + len(stderr_marker)] != stderr_marker:
        raise SwebenchError("private grader stdout framing is invalid")
    cursor += len(stderr_marker)
    stderr = framed[cursor : cursor + stderr_length]
    cursor += stderr_length
    log_marker = b"\ntest-log\0"
    if framed[cursor : cursor + len(log_marker)] != log_marker:
        raise SwebenchError("private grader stderr framing is invalid")
    cursor += len(log_marker)
    captured_log = framed[cursor:]
    if (
        len(captured_log) > _SCORER_LOG_LIMIT
        or not captured_log
        or (not patch_failed and (stderr or _TEST_EXIT_CODE_RE.search(stdout) is None))
    ):
        raise SwebenchError("private grader output is incomplete for rescore")
    sanitized_stdout = _TEST_EXIT_CODE_RE.sub(b">>>>> Test Exit Code [host-neutralized]", stdout)
    expected_log = sanitized_stdout + b"\n>>>>> End Test Output\n"
    if not patch_failed:
        expected_log += f">>>>> Test Exit Code: {header['eval_exit_code']}\n".encode("ascii")
    if captured_log != expected_log or _sha256(captured_log) != header["test_log_sha256"]:
        raise SwebenchError("private grader capture log is not bound to its raw output")
    log_name = f"grader-log-{header['test_log_sha256']}.txt"
    saved_log = read_private(log_name, "private grading log", _SCORER_LOG_LIMIT)
    if saved_log != captured_log:
        raise SwebenchError("private grading log differs from the immutable grader capture")

    capture = {
        "phase": header["phase"],
        "prediction_sha256": patch_sha256,
        "eval_script_sha256": header["eval_script_sha256"],
        "grader_evidence_sha256": _sha256(capture_bytes),
        "test_log_path": log_name,
        "eval_exit_code": header["eval_exit_code"],
        "timed_out": header["timed_out"],
        "capture_error": header["error"],
    }
    agent = {
        "prediction_handle": handle,
        "model_patch_sha256": patch_sha256,
    }
    return capture, agent


def _score_grader_capture(
    *,
    capture: Mapping[str, Any],
    task: _Task,
    agent: Mapping[str, Any],
    test_spec: Any,
    run_dir: Path,
    evidence_dir: Path,
    score_executor: ScoreExecutor,
    cleanup_evidence: Sequence[tuple[bool, bool]],
) -> tuple[str, str | None]:
    """Run the official scorer over host-captured bytes after cleanup succeeded."""

    handle = _nonblank(agent.get("prediction_handle"), "prediction_handle")
    match = re.fullmatch(r"private://(prediction-[0-9a-f]{64}\.patch)", handle)
    if match is None:
        raise SwebenchError("prediction evidence handle is invalid after grading")
    patch_path = secure_resolve(evidence_dir / match.group(1), must_exist=True)
    if patch_path.parent != secure_resolve(evidence_dir, must_exist=True):
        raise SwebenchError("prediction evidence escaped its private directory")
    patch_bytes = patch_path.read_bytes()
    patch_sha256 = cast(str, agent["model_patch_sha256"])
    if _sha256(patch_bytes) != patch_sha256:
        raise SwebenchError("prediction evidence changed before official scoring")
    try:
        model_patch = patch_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise SwebenchError("prediction evidence is not UTF-8 at scoring time") from exc
    log_name = cast(str, capture["test_log_path"])
    if re.fullmatch(r"grader-log-[0-9a-f]{64}\.txt", log_name) is None:
        raise SwebenchError("host-captured test log path is invalid")
    log_path = secure_resolve(run_dir / log_name, must_exist=True)
    if log_path.parent != secure_resolve(run_dir, must_exist=True):
        raise SwebenchError("host-captured test log escaped the private run directory")
    try:
        from .swebench_official_scorer import TrustedRun
    except ImportError as exc:
        raise SwebenchError("trusted SWE-bench scorer adapter is unavailable") from exc
    capture_error = capture.get("capture_error")
    test_exit = capture.get("eval_exit_code") if capture_error is None else None
    cleanup_ok = len(cleanup_evidence) == 2 and all(
        evidence == (True, True) for evidence in cleanup_evidence
    )
    if not cleanup_ok:
        raise SwebenchError("task and grader cleanup evidence is incomplete")
    if (
        capture_error == _GRADER_PATCH_FAILED
        and capture.get("phase") == "apply_failed"
        and capture.get("timed_out") is False
    ):
        # SWE-bench scores a prediction that no pinned apply mode accepts as not resolved.
        return "completed", "unresolved"
    if (
        capture_error is not None
        or capture.get("timed_out") is not False
        or capture.get("phase") != "patch_ready"
        or type(test_exit) is not int
        or not 0 <= test_exit <= 255
        or test_exit in {125, 126, 127}
    ):
        return "infrastructure_error", None
    trusted_run = TrustedRun(
        test_spec=test_spec,
        instance_id=task.instance_id,
        prediction_handle=handle,
        patch_sha256=patch_sha256,
        model_patch=model_patch,
        model_name_or_path="racecraft-local-splash",
        test_log_path=log_path,
        trusted_evidence_root=run_dir,
        exit_code=test_exit,
        timed_out=cast(bool, capture["timed_out"]),
        cleanup_ok=cleanup_ok,
    )
    score = score_executor(trusted_run)
    status = getattr(score, "status", None)
    official = getattr(score, "official", None)
    resolved = getattr(score, "resolved", None)
    if official is True and status in {"resolved", "unresolved"} and isinstance(resolved, bool):
        if (status == "resolved") != resolved:
            raise SwebenchError("official scorer resolution fields disagree")
        return "completed", status
    return "infrastructure_error", None


def _require_frozen_model(prepared: _Prepared, runtime: SwebenchRuntime) -> None:
    """Stop before a task starts unless the frozen model instance is still being served."""

    served = cast(Mapping[str, Any], prepared.profile["model_runtime"])["served_model_fingerprint"]
    if runtime.adapter_probe is None:
        raise _SdkTransportStopped("frozen model cannot be verified; run stopped before the task")
    try:
        evidence = runtime.adapter_probe(
            prepared.server_origin,
            cast(str, prepared.profile["model"]),
            cast(Mapping[str, object], prepared.profile["parameters"]),
        )
    except Exception as exc:
        raise _SdkTransportStopped(
            "frozen model is unavailable; run stopped before the task"
        ) from exc
    if evidence.get("served_model_fingerprint") != served:
        raise _SdkTransportStopped("frozen model is not being served; run stopped before the task")


def _execute_tasks(
    prepared: _Prepared,
    runtime: SwebenchRuntime,
    run_dir: Path,
) -> list[dict[str, Any]]:
    if runtime.agent_executor is None or runtime.grader_executor is None:
        raise SwebenchError("SWE-bench execution adapters are unavailable")
    agent_executor = runtime.agent_executor
    grader_executor = runtime.grader_executor
    test_spec_factory = runtime.test_spec_factory
    score_executor = runtime.score_executor
    if score_executor is None:

        def score_executor(trusted_run: Any) -> Any:
            return _default_score_executor(
                trusted_run,
                prepared=prepared,
                runtime=runtime,
            )

    limits = cast(Mapping[str, Any], prepared.profile["resource_limits"])
    results: list[dict[str, Any]] = []
    for index, task in enumerate(prepared.tasks):
        checkpoint_path = run_dir / f"checkpoint-{index:04d}.json"

        def reconcile_control(
            checkpoint: Mapping[str, Any],
            checkpoint_path: Path = checkpoint_path,
            prepared: _Prepared = prepared,
            runtime: SwebenchRuntime = runtime,
        ) -> None:
            _recover_control_checkpoint(
                checkpoint,
                checkpoint_path=checkpoint_path,
                prepared=prepared,
                runtime=runtime,
            )

        existing = _task_checkpoint(
            checkpoint_path,
            prepared=prepared,
            task=task,
            index=index,
            reconcile_control=reconcile_control,
        )
        if existing is not None:
            results.append(dict(existing))
            continue
        _require_frozen_model(prepared, runtime)
        if test_spec_factory is None:
            test_spec = prepared.scorer_test_specs.get(task.instance_id)
            if not isinstance(test_spec, _ScorerTestSpec):
                raise SwebenchError("official TestSpec was not frozen during readiness")
            official_eval_script = test_spec.execution_eval_script
            expected_eval_script_sha256 = test_spec.execution_eval_script_sha256
        else:
            test_spec, official_eval_script = test_spec_factory(task, prepared.state)
            expected_eval_script_sha256 = None
        if not isinstance(official_eval_script, str) or not official_eval_script:
            raise SwebenchError("pinned official TestSpec has no eval script")
        eval_script_sha256 = _sha256(official_eval_script.encode("utf-8"))
        if (
            expected_eval_script_sha256 is not None
            and eval_script_sha256 != expected_eval_script_sha256
        ):
            raise SwebenchError("official eval script drifted from frozen generated bytes")
        attempt_nonce = _nonblank(runtime.nonce_factory(), "attempt nonce")
        control_identity: dict[str, str] = {}
        if agent_executor is _default_agent_executor:
            control_reference, control_digest = _control_image_identity()
            control_name, control_fingerprint = _control_container_identity(
                task_fingerprint_sha256=_task_fingerprint(task),
                attempt_nonce=attempt_nonce,
                control_image_digest=control_digest,
            )
            control_identity = {
                "control_image_reference": control_reference,
                "control_image_digest": control_digest,
                "control_container_name": control_name,
                "control_identity_sha256": control_fingerprint,
            }
        control_state: dict[str, str | None] = {"control_container_id": None}

        def control_checkpoint_fields(
            identity: Mapping[str, str] = control_identity,
            state: Mapping[str, str | None] = control_state,
            checkpoint: Path = checkpoint_path,
        ) -> dict[str, Any]:
            sdk_budget = None
            if checkpoint.exists():
                sdk_budget = json.loads(checkpoint.read_bytes()).get("sdk_budget")
            return {
                "sdk_budget": sdk_budget,
                "control_image_digest": identity.get("control_image_digest"),
                "control_container_name": identity.get("control_container_name"),
                "control_identity_sha256": identity.get("control_identity_sha256"),
                "control_container_id": state["control_container_id"],
            }

        _write_private_json(
            checkpoint_path,
            _checkpoint_payload(
                prepared,
                task,
                index,
                attempt_nonce=attempt_nonce,
                state="in_flight",
                attempted=False,
                status="in_flight",
                **control_checkpoint_fields(),
            ),
        )
        task_name = f"swebench-task-{runtime.nonce_factory()}"
        grader_name = f"swebench-grader-{runtime.nonce_factory()}"
        egress_hosts = _grader_egress_hosts(prepared, task.instance_id)
        gateway_name = (
            None if egress_hosts is None else f"swebench-egress-{runtime.nonce_factory()}"
        )
        gateway_id: str | None = None
        task_started = False
        grader_started = False
        gateway_started = False
        cleanup_evidence: list[tuple[bool, bool]] = []
        verified_receipt: dict[str, Any] | None = None
        agent: dict[str, Any] | None = None
        pending_checkpoint: dict[str, Any] | None = None
        pending_result: dict[str, Any] | None = None
        pending_score: dict[str, Any] | None = None
        verified_receipt_ref: list[dict[str, Any] | None] = [None]

        def on_control_container_id(
            container_id: str,
            checkpoint_path: Path = checkpoint_path,
            task: _Task = task,
            index: int = index,
            attempt_nonce: str = attempt_nonce,
            receipt_ref: list[dict[str, Any] | None] = verified_receipt_ref,
            state_ref: dict[str, str | None] = control_state,
        ) -> None:
            validated_id = _validated_control_container_id(container_id)
            if validated_id is None:
                raise SwebenchError("control container ID is unavailable")
            if state_ref["control_container_id"] == validated_id:
                return
            state_ref["control_container_id"] = validated_id
            receipt = receipt_ref[0]
            _write_private_json(
                checkpoint_path,
                _checkpoint_payload(
                    prepared,
                    task,
                    index,
                    attempt_nonce=attempt_nonce,
                    state=("response_verified" if receipt is not None else "in_flight"),
                    attempted=receipt is not None,
                    status=("response_verified" if receipt is not None else "in_flight"),
                    **control_checkpoint_fields(),
                    host_response_receipt=receipt,
                ),
            )

        def on_verified_response(
            receipt: Mapping[str, Any],
            *,
            checkpoint_path: Path = checkpoint_path,
            task: _Task = task,
            index: int = index,
            attempt_nonce: str = attempt_nonce,
            receipt_ref: list[dict[str, Any] | None] = verified_receipt_ref,
        ) -> None:
            nonlocal verified_receipt
            if verified_receipt is not None:
                return
            validated = _validated_host_response_receipt(
                receipt,
                expected_task_instance_id_sha256=_sha256(task.instance_id.encode()),
                expected_task_fingerprint=_task_fingerprint(task),
                expected_served_model_fingerprint=cast(
                    str, prepared.profile["model_runtime"]["served_model_fingerprint"]
                ),
            )
            _write_private_json(
                checkpoint_path,
                _checkpoint_payload(
                    prepared,
                    task,
                    index,
                    attempt_nonce=attempt_nonce,
                    state="response_verified",
                    attempted=True,
                    status="response_verified",
                    **control_checkpoint_fields(),
                    host_response_receipt=validated,
                ),
            )
            verified_receipt = validated
            receipt_ref[0] = validated

        try:
            task_args = build_task_container_args(
                image_reference=task.task_image_reference,
                image_digest=task.task_image_digest,
                platform=cast(str, prepared.profile["platform"]),
                limits=limits,
                name=task_name,
            )
            task_args = (str(runtime.docker_executable), *task_args[1:])
            _run(
                runtime,
                task_args,
                timeout=30,
                max_output_bytes=cast(int, limits["max_output_bytes"]),
            )
            task_started = True
            _attest_container(runtime, task_name, limits, role="task")
            _run(
                runtime,
                (str(runtime.docker_executable), "start", task_name),
                timeout=30,
                max_output_bytes=cast(int, limits["max_output_bytes"]),
            )
            raw_agent = agent_executor(
                container_name=task_name,
                instance_id=task.instance_id,
                server_origin=prepared.server_origin,
                model=prepared.profile["model"],
                parameters=dict(prepared.profile["parameters"]),
                config_path=prepared.repo / "sandbox" / "swebench" / "mini-swe-agent.yaml",
                timeout_seconds=limits["timeout_seconds"],
                max_requests=limits["max_requests"],
                max_turns=limits["max_turns"],
                max_output_bytes=limits["max_output_bytes"],
                max_task_output_tokens=limits["max_task_output_tokens"],
                sdk_checkpoint=checkpoint_path,
                served_model_fingerprint=prepared.profile["model_runtime"][
                    "served_model_fingerprint"
                ],
                task_fingerprint_sha256=_task_fingerprint(task),
                attempt_nonce=attempt_nonce,
                problem_statement_sha256=task.problem_statement_sha256,
                docker_executable=str(runtime.docker_executable),
                evidence_dir=run_dir,
                runner=runtime.runner or _default_runner(),
                on_verified_response=on_verified_response,
                on_control_container_id=on_control_container_id,
                **control_identity,
            )
            agent = _validate_agent_result(raw_agent, limits)
            sdk_budget = _sdk_budget_state(
                json.loads(checkpoint_path.read_bytes()).get("sdk_budget")
            )
            if sdk_budget is not None:
                # Preserve host-observed usage; charged_output_tokens is a separate budget figure.
                agent["output_tokens"] = sdk_budget["actual_output_tokens"]
                agent["charged_output_tokens"] = sdk_budget["charged_output_tokens"]
            if verified_receipt is None:
                raise SwebenchError("host response receipt is unavailable")
            agent["host_response_receipt"] = verified_receipt
            if agent["status"] in {"model_failure", "infrastructure_error"}:
                checkpoint = _checkpoint_payload(
                    prepared,
                    task,
                    index,
                    attempt_nonce=attempt_nonce,
                    state="cleanup_pending",
                    attempted=True,
                    status=cast(str, agent["status"]),
                    **control_checkpoint_fields(),
                    **_validated_agent_telemetry(agent),
                    host_response_receipt=cast(Mapping[str, Any], agent["host_response_receipt"]),
                    trajectory_sha256=cast(str, agent["trajectory_sha256"]),
                )
                _write_private_json(checkpoint_path, checkpoint)
                pending_checkpoint = checkpoint
                continue
            response_checkpoint = _checkpoint_payload(
                prepared,
                task,
                index,
                attempt_nonce=attempt_nonce,
                state="response_verified",
                attempted=True,
                status="response_verified",
                **control_checkpoint_fields(),
                **_validated_agent_telemetry(agent),
                host_response_receipt=verified_receipt,
                trajectory_sha256=cast(str, agent["trajectory_sha256"]),
                model_patch_sha256=cast(str, agent["model_patch_sha256"]),
            )
            _write_private_json(checkpoint_path, response_checkpoint)
            if egress_hosts is not None and gateway_name is not None:
                gateway_started = True
                gateway_id = _start_egress_gateway(
                    runtime,
                    prepared,
                    egress_hosts,
                    limits,
                    name=gateway_name,
                    evidence_dir=run_dir,
                    attempt_nonce=attempt_nonce,
                )
            network_mode = "none" if gateway_id is None else f"container:{gateway_id}"
            grader_args = build_grader_container_args(
                image_reference=task.grader_image_reference,
                image_digest=task.grader_image_digest,
                platform=cast(str, prepared.profile["platform"]),
                limits=limits,
                name=grader_name,
                network_mode=network_mode,
            )
            grader_args = (str(runtime.docker_executable), *grader_args[1:])
            _run(
                runtime,
                grader_args,
                timeout=30,
                max_output_bytes=cast(int, limits["max_output_bytes"]),
            )
            grader_started = True
            _attest_container(
                runtime,
                grader_name,
                limits,
                role="grader",
                image_reference=task.grader_image_reference,
                image_digest=task.grader_image_digest,
                network_mode=network_mode,
            )
            _run(
                runtime,
                (str(runtime.docker_executable), "start", grader_name),
                timeout=30,
                max_output_bytes=cast(int, limits["max_output_bytes"]),
            )
            raw_capture = grader_executor(
                container_name=grader_name,
                instance_id=task.instance_id,
                prediction_handle=agent["prediction_handle"],
                model_patch_sha256=agent["model_patch_sha256"],
                test_spec_eval_script=official_eval_script,
                test_spec_eval_script_sha256=eval_script_sha256,
                evidence_dir=run_dir,
                runner=runtime.runner or _default_runner(),
                docker_executable=str(runtime.docker_executable),
                timeout_seconds=limits["timeout_seconds"],
                max_output_bytes=limits["max_output_bytes"],
                attempt_nonce=attempt_nonce,
            )
            if gateway_id is not None and gateway_name is not None:
                _require_gateway_running(runtime, gateway_name, limits, gateway_id)
            capture = _persist_grader_capture(
                raw_capture,
                task=task,
                prediction_sha256=cast(str, agent["model_patch_sha256"]),
                eval_script_sha256=eval_script_sha256,
                attempt_nonce=attempt_nonce,
                run_dir=run_dir,
                limits=limits,
            )
            checkpoint = _checkpoint_payload(
                prepared,
                task,
                index,
                attempt_nonce=attempt_nonce,
                state="cleanup_pending",
                attempted=True,
                status="infrastructure_error",
                **control_checkpoint_fields(),
                **_validated_agent_telemetry(agent),
                host_response_receipt=cast(Mapping[str, Any], agent["host_response_receipt"]),
                trajectory_sha256=cast(str, agent["trajectory_sha256"]),
                model_patch_sha256=cast(str, agent["model_patch_sha256"]),
                grader_evidence_sha256=cast(str, capture["grader_evidence_sha256"]),
            )
            _write_private_json(checkpoint_path, checkpoint)
            pending_checkpoint = checkpoint
            pending_score = {"capture": capture, "test_spec": test_spec}
        except _SdkTransportStopped:
            # Node reaping is not evidence that server inference stopped. Never start another task.
            raise
        except Exception as exc:
            _write_private_json(
                run_dir / f"diagnostic-{attempt_nonce}.json",
                _exception_diagnostic(exc, task_index=index),
            )
            if verified_receipt is not None and agent is not None:
                checkpoint = _checkpoint_payload(
                    prepared,
                    task,
                    index,
                    attempt_nonce=attempt_nonce,
                    state="cleanup_pending",
                    attempted=True,
                    status="infrastructure_error",
                    **control_checkpoint_fields(),
                    **_validated_agent_telemetry(agent),
                    host_response_receipt=verified_receipt,
                    trajectory_sha256=cast(str, agent["trajectory_sha256"]),
                    model_patch_sha256=(
                        cast(str, agent["model_patch_sha256"])
                        if agent.get("model_patch_sha256") is not None
                        else None
                    ),
                )
                _write_private_json(checkpoint_path, checkpoint)
                pending_checkpoint = checkpoint
            elif verified_receipt is not None:
                checkpoint = _checkpoint_payload(
                    prepared,
                    task,
                    index,
                    attempt_nonce=attempt_nonce,
                    state="cleanup_pending",
                    attempted=True,
                    status="infrastructure_error",
                    **control_checkpoint_fields(),
                    host_response_receipt=verified_receipt,
                )
                _write_private_json(checkpoint_path, checkpoint)
                pending_checkpoint = checkpoint
            else:
                checkpoint = _checkpoint_payload(
                    prepared,
                    task,
                    index,
                    attempt_nonce=attempt_nonce,
                    state="ambiguous",
                    attempted=False,
                    status="infrastructure_error",
                    **control_checkpoint_fields(),
                )
                _write_private_json(checkpoint_path, checkpoint)
                pending_result = checkpoint
        finally:
            errors: list[SwebenchError] = []
            for started, name, volume in (
                (grader_started, grader_name, f"{grader_name}-workspace"),
                (task_started, task_name, f"{task_name}-workspace"),
            ):
                if started:
                    try:
                        cleanup_evidence.append(
                            _cleanup(runtime, name, limits, workspace_volume=volume)
                        )
                    except SwebenchError as exc:
                        errors.append(exc)
            if gateway_started and gateway_name is not None:
                # Removed after its grader; not part of the task/grader cleanup evidence pair.
                try:
                    _cleanup(runtime, gateway_name, limits)
                except SwebenchError as exc:
                    errors.append(exc)
            if errors:
                _mark_cleanup_invalid(
                    checkpoint_path,
                    prepared=prepared,
                    task=task,
                    index=index,
                    attempt_nonce=attempt_nonce,
                    run_dir=run_dir,
                    errors=errors,
                )
                raise errors[0]
            if pending_score is not None and agent is not None and pending_checkpoint is not None:
                pending_capture = cast(Mapping[str, Any], pending_score["capture"])
                try:
                    status, grade = _score_grader_capture(
                        capture=pending_capture,
                        task=task,
                        agent=agent,
                        test_spec=pending_score["test_spec"],
                        run_dir=run_dir,
                        evidence_dir=run_dir,
                        score_executor=score_executor,
                        cleanup_evidence=cleanup_evidence,
                    )
                except Exception:
                    status, grade = "infrastructure_error", None
                pending_checkpoint["status"] = status
                pending_checkpoint["grade"] = grade
                _write_private_json(checkpoint_path, pending_checkpoint)
            if pending_checkpoint is not None:
                committed = dict(pending_checkpoint)
                committed["state"] = "terminal"
                _write_private_json(checkpoint_path, committed)
                results.append(committed)
            elif pending_result is not None:
                results.append(pending_result)
    return results


def _benchmark_summary(prepared: _Prepared, results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if any(type(result.get("attempted")) is not bool for result in results):
        raise SwebenchError("task attempt evidence is invalid")
    completed = sum(result.get("status") == "completed" for result in results)
    resolved = sum(result.get("grade") == "resolved" for result in results)
    model_failures = sum(result.get("status") == "model_failure" for result in results)
    infrastructure_errors = sum(
        result.get("status") == "infrastructure_error" for result in results
    )
    attempted = sum(result["attempted"] is True for result in results)
    errors = model_failures + infrastructure_errors
    qualification = prepared.profile["mode"] == "qualification"
    accounted = completed + errors == len(prepared.tasks)
    # Upstream convention: resolved / 500, with infrastructure errors counted as unresolved.
    capability_complete = (
        not qualification and accounted and infrastructure_errors < len(prepared.tasks)
    )
    qualification_complete = qualification and accounted and errors == 0
    budgets = [_sdk_budget_state(result.get("sdk_budget")) for result in results]
    known_budgets = [budget for budget in budgets if budget is not None]
    complete_usage = len(known_budgets) == len(prepared.tasks)
    actual_values = [budget["actual_output_tokens"] for budget in known_budgets]
    return {
        "charged_output_tokens": (
            sum(budget["charged_output_tokens"] for budget in known_budgets)
            if complete_usage
            else None
        ),
        "actual_output_tokens": (
            sum(actual_values)
            if complete_usage and all(value is not None for value in actual_values)
            else None
        ),
        "schema_version": 1,
        "status": "completed" if capability_complete or qualification_complete else "partial",
        "evidence_class": (
            "runtime_scorer_qualification" if qualification else "held_out_capability"
        ),
        "task_count": len(prepared.tasks),
        "attempted_count": attempted,
        "completed_count": completed,
        "resolved_count": resolved,
        "unresolved_count": completed - resolved,
        "model_failure_count": model_failures,
        "infrastructure_error_count": infrastructure_errors,
        "error_count": errors,
        "resolution_rate": None if qualification else resolved / len(prepared.tasks),
        "protocol_fingerprint": prepared.protocol_fingerprint,
        "manifest_sha256": prepared.manifest_sha256,
        "image_bindings_sha256": prepared.image_bindings_sha256,
        "runner_config_sha256": prepared.runner_config_sha256,
        "control_image_digest": prepared.profile["control_image_digest"],
        "non_capability": qualification,
        "capability_claim_allowed": capability_complete,
    }


def _benchmark_result(
    prepared: _Prepared, run_id: str, summary: Mapping[str, Any]
) -> BenchmarkResult:
    qualification = prepared.profile["mode"] == "qualification"
    status = BenchmarkStatus.QUALIFICATION
    if not qualification:
        capability_complete = (
            summary["completed_count"]
            + summary["model_failure_count"]
            + summary["infrastructure_error_count"]
            == summary["task_count"]
            and summary["infrastructure_error_count"] < summary["task_count"]
        )
        if capability_complete:
            status = BenchmarkStatus.COMPLETE
        elif summary["error_count"] == summary["task_count"]:
            status = BenchmarkStatus.FAILED
        elif summary["error_count"]:
            status = BenchmarkStatus.PARTIAL
        else:
            status = BenchmarkStatus.PARTIAL
    score = summary["resolution_rate"] if status is BenchmarkStatus.COMPLETE else None
    return BenchmarkResult(
        result_id=run_id,
        identity=BenchmarkIdentity(
            name="SWE-bench Verified",
            variant="qualification-disjoint" if qualification else "verified-500",
            adapter="mini-swe-agent-bash-docker",
            dataset_provider="princeton-nlp",
            dataset_id=prepared.dataset_id,
            dataset_revision=prepared.dataset_revision,
            evaluation_version=SWEBENCH_VERSION,
            split="test",
            subset="disjoint-qualification" if qualification else "verified",
        ),
        status=status,
        counts=BenchmarkCounts(
            requested=cast(int, summary["task_count"]),
            succeeded=cast(int, summary["completed_count"]),
            errored=cast(int, summary["error_count"]),
        ),
        metric_name="resolution_rate",
        metric_unit=MetricUnit.PROPORTION,
        score=cast(float | None, score),
        task_outcomes=TaskOutcomes(
            resolved=summary["resolved_count"],
            unresolved=summary["unresolved_count"],
            model_failure=summary["model_failure_count"],
            infrastructure_error=summary["infrastructure_error_count"],
        ),
        tested_configuration=TestedConfiguration(
            harness="mini-swe-agent-bash-docker",
            harness_version=MINI_SWE_AGENT_VERSION,
            reasoning_effort=prepared.profile["parameters"]["reasoning_effort"],
            temperature=prepared.profile["parameters"]["temperature"],
            top_p=prepared.profile["parameters"]["top_p"],
            max_output_tokens=prepared.profile["parameters"]["max_output_tokens"],
            attempts_per_task=1,
        ),
        evaluator=BenchmarkEvaluator(
            name="SWE-bench official grader",
            version=SWEBENCH_VERSION,
            developer=None,
            model_label=cast(str, prepared.profile["model"]),
            evidence_class=EvaluatorClass.MEASURED_HERE,
        ),
        provenance=BenchmarkProvenance(
            source="private local SWE-bench execution",
            artifacts={
                "protocol_fingerprint": prepared.protocol_fingerprint,
                "manifest": prepared.manifest_sha256,
                "image_bindings": prepared.image_bindings_sha256,
                "runner_config": prepared.runner_config_sha256,
                "control_image": cast(str, prepared.profile["control_image_digest"])[7:],
            },
        ),
        limitations=(
            "Qualification tasks are disjoint from SWE-bench Verified and are not "
            "capability evidence."
            if qualification
            else "Results apply only to the frozen local model, protocol, task images, "
            "and grader image.",
        ),
    )


def _approval(prepared: _Prepared, approval_marker: str | None) -> None:
    if prepared.profile["mode"] != "verified":
        return
    if approval_marker != FULL_RUN_APPROVAL_MARKER:
        raise SwebenchError("verified full run requires the exact explicit approval marker")
    approval = cast(Mapping[str, Any], prepared.profile["protocol_approval"])
    if (
        approval["approved"] is not True
        or approval["approved_fingerprint"] != prepared.protocol_fingerprint
    ):
        raise SwebenchError("verified full run approval does not match the protocol fingerprint")


def _canonical_record(prepared: _Prepared, run_id: str) -> dict[str, Any]:
    record: dict[str, Any] = {"schema_version": 1, "run_id": run_id}
    record["mode"] = prepared.profile["mode"]
    record["protocol_fingerprint"] = prepared.protocol_fingerprint
    record["manifest_sha256"] = prepared.manifest_sha256
    record["image_bindings_sha256"] = prepared.image_bindings_sha256
    record["task_count"] = len(prepared.tasks)
    record["created_before_first_response"] = True
    return record


def _result(prepared: _Prepared, run_id: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    summary = _benchmark_summary(prepared, results)
    benchmark = _benchmark_result(prepared, run_id, summary)
    serialized_benchmark = benchmark.model_dump(mode="json")
    completed_ids = [result["instance_id_sha256"] for result in results]
    return {
        "status": summary["status"],
        "run_id": run_id,
        "mode": prepared.profile["mode"],
        "task_count": len(prepared.tasks),
        "non_capability": prepared.profile["mode"] == "qualification",
        "protocol_fingerprint": prepared.protocol_fingerprint,
        "manifest_sha256": prepared.manifest_sha256,
        "image_bindings_sha256": prepared.image_bindings_sha256,
        "checkpoint_count": len(results),
        "completed_task_ids_sha256": _sha256(_canonical(completed_ids)),
        "runtime_evidence": {
            "transport": "lmstudio_sdk_2.0.0_local_multiturn_no_retry",
            "endpoint": "ws://127.0.0.1:1234",
            "mini_swe_agent_version": MINI_SWE_AGENT_VERSION,
            "swebench_version": SWEBENCH_VERSION,
            "platform": prepared.profile["platform"],
        },
        "benchmark_summary": summary,
        "benchmark_result": serialized_benchmark,
        "benchmark_result_fingerprint_sha256": benchmark.fingerprint_sha256(),
    }


def execute_swebench(
    profile: Mapping[str, Any],
    *,
    repo: str | os.PathLike[str],
    state: str | os.PathLike[str],
    server_origin: str,
    approval_marker: str | None = None,
    runtime: SwebenchRuntime | None = None,
    on_run_created: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run each task once with private checkpoints and a separate grader."""

    selected = runtime or SwebenchRuntime()
    prepared = _prepare(
        profile, repo=repo, state=state, server_origin=server_origin, runtime=selected
    )
    if prepared.blockers:
        raise SwebenchError("; ".join(prepared.blockers))
    _approval(prepared, approval_marker)
    run_id = f"swebench-{selected.nonce_factory()}"
    if _RUN_ID_RE.fullmatch(run_id) is None:
        raise SwebenchError("runtime produced an unsafe run ID")
    run_dir = prepared.state / "swebench" / "runs" / run_id
    run_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    _write_private_json(run_dir / "canonical.json", _canonical_record(prepared, run_id))
    if on_run_created is not None:
        on_run_created(run_id)
    results = _execute_tasks(prepared, selected, run_dir)
    sanitized = _result(prepared, run_id, results)
    _write_private_json(run_dir / "result.json", sanitized)
    return sanitized


def _load_run(prepared: _Prepared, run_id: str) -> Path:
    if _RUN_ID_RE.fullmatch(run_id) is None:
        raise SwebenchError("run_id is invalid")
    run_dir = prepared.state / "swebench" / "runs" / run_id
    invalid_path = run_dir / "invalid.json"
    if invalid_path.exists():
        try:
            invalid = _mapping(json.loads(invalid_path.read_bytes()), "invalid run marker")
            _exact_keys(invalid, _RUN_INVALID_KEYS, "invalid run marker")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SwebenchError("private invalid-run marker is unavailable") from exc
        if (
            invalid.get("schema_version") != 1
            or invalid.get("reason") != "cleanup_failed"
            or invalid.get("manifest_sha256") != prepared.manifest_sha256
            or invalid.get("protocol_fingerprint") != prepared.protocol_fingerprint
        ):
            raise SwebenchError("private invalid-run marker does not match the frozen protocol")
        raise SwebenchError("private run is invalid after cleanup failure")
    try:
        canonical = _mapping(json.loads((run_dir / "canonical.json").read_bytes()), "canonical run")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SwebenchError("private run evidence is unavailable") from exc
    if (
        canonical.get("protocol_fingerprint") != prepared.protocol_fingerprint
        or canonical.get("manifest_sha256") != prepared.manifest_sha256
        or canonical.get("image_bindings_sha256") != prepared.image_bindings_sha256
        or canonical.get("created_before_first_response") is not True
    ):
        raise SwebenchError("private run evidence does not match the frozen protocol")
    return run_dir


def resume_swebench(
    profile: Mapping[str, Any],
    *,
    repo: str | os.PathLike[str],
    state: str | os.PathLike[str],
    server_origin: str,
    run_id: str,
    approval_marker: str | None = None,
    runtime: SwebenchRuntime | None = None,
) -> dict[str, Any]:
    """Resume only never-attempted tasks from matching private evidence."""

    selected = runtime or SwebenchRuntime()
    prepared = _prepare(
        profile,
        repo=repo,
        state=state,
        server_origin=server_origin,
        runtime=selected,
        probe_adapter=False,
    )
    if prepared.blockers:
        raise SwebenchError("; ".join(prepared.blockers))
    _approval(prepared, approval_marker)
    run_dir = _load_run(prepared, run_id)
    pending = False
    for index, task in enumerate(prepared.tasks):
        checkpoint_path = run_dir / f"checkpoint-{index:04d}.json"

        def reconcile(checkpoint: Mapping[str, Any], path: Path = checkpoint_path) -> None:
            _recover_control_checkpoint(
                checkpoint, checkpoint_path=path, prepared=prepared, runtime=selected
            )

        existing = _task_checkpoint(
            checkpoint_path,
            prepared=prepared,
            task=task,
            index=index,
            reconcile_control=reconcile,
        )
        pending = pending or existing is None
    if pending:
        blockers = _adapter_blockers(prepared, selected)
        if blockers:
            raise SwebenchError("; ".join(blockers))
    results = _execute_tasks(prepared, selected, run_dir)
    sanitized = _result(prepared, run_id, results)
    _write_private_json(run_dir / "result.json", sanitized)
    return sanitized


def rescore_swebench(
    profile: Mapping[str, Any],
    *,
    repo: str | os.PathLike[str],
    state: str | os.PathLike[str],
    server_origin: str,
    run_id: str,
    runtime: SwebenchRuntime | None = None,
) -> dict[str, Any]:
    """Replay only the trusted scorer against immutable terminal-run evidence."""

    selected = runtime or SwebenchRuntime()
    paths = ExternalStatePaths.resolve(
        project_root=repo, state_dir=state, require_project=True, require_state=True
    )
    prepared = _validate_profile(profile, paths.project_root, paths.state_dir)
    run_dir = _load_run(prepared, run_id)
    checkpoints: list[dict[str, Any]] = []
    checkpoint_bytes: list[bytes] = []
    for index, task in enumerate(prepared.tasks):
        checkpoint_path = run_dir / f"checkpoint-{index:04d}.json"
        try:
            raw = checkpoint_path.read_bytes()
        except OSError as exc:
            raise SwebenchError("rescore requires a terminal checkpoint for every task") from exc
        checkpoint = _task_checkpoint(checkpoint_path, prepared=prepared, task=task, index=index)
        if checkpoint is None:
            raise SwebenchError("rescore requires a terminal checkpoint for every task")
        checkpoint_bytes.append(raw)
        checkpoints.append(dict(checkpoint))

    expected_result = _canonical(_result(prepared, run_id, checkpoints))
    result_path = run_dir / "result.json"
    try:
        result_info = result_path.lstat()
        result_resolved = secure_resolve(result_path, must_exist=True)
        source_result = result_resolved.read_bytes()
    except (OSError, ConfigurationError) as exc:
        raise SwebenchError("rescore requires the original private run result") from exc
    if (
        not stat.S_ISREG(result_info.st_mode)
        or result_info.st_mode & 0o077
        or result_resolved.parent != run_dir.resolve(strict=True)
        or source_result != expected_result
    ):
        raise SwebenchError("original result does not match the immutable terminal checkpoints")

    if selected.test_spec_factory is not None or selected.score_executor is not None:
        # Injected scorers are permitted only when their existing scorer identity
        # contract is satisfied. The default path below never probes a model.
        _prepare_trusted_test_specs(prepared, selected)

    score_executor: ScoreExecutor
    if selected.score_executor is None:

        def score_with_frozen_scorer(trusted_run: Any) -> Any:
            return _default_score_executor(trusted_run, prepared=prepared, runtime=selected)

        score_executor = score_with_frozen_scorer
    else:
        score_executor = selected.score_executor

    task_reports: list[dict[str, Any]] = []
    for index, (task, checkpoint) in enumerate(zip(prepared.tasks, checkpoints, strict=True)):
        original_status = cast(str, checkpoint["status"])
        grade: str | None = None
        report_status = original_status
        log_sha256: str | None = None
        if original_status == "completed":
            test_spec = _load_frozen_test_spec(task, prepared.state, prepared.repo)
            capture, agent = _read_rescore_capture(
                task=task,
                checkpoint=checkpoint,
                test_spec=test_spec,
                run_dir=run_dir,
                max_output_bytes=cast(
                    int,
                    cast(Mapping[str, Any], prepared.profile["resource_limits"])[
                        "max_output_bytes"
                    ],
                ),
            )
            log_sha256 = (
                cast(str, capture["test_log_path"]).removeprefix("grader-log-").removesuffix(".txt")
            )
            report_status, grade = _score_grader_capture(
                capture=capture,
                task=task,
                agent=agent,
                test_spec=test_spec,
                run_dir=run_dir,
                evidence_dir=run_dir,
                score_executor=score_executor,
                # A terminal completed checkpoint is written only after both
                # original task and grader cleanup checks succeed.
                cleanup_evidence=((True, True), (True, True)),
            )
        task_reports.append(
            {
                "task_index": index,
                "instance_id_sha256": checkpoint["instance_id_sha256"],
                "checkpoint_sha256": _sha256(checkpoint_bytes[index]),
                "original_status": original_status,
                "original_grade": checkpoint.get("grade"),
                "status": report_status,
                "grade": grade,
                "rescored": original_status == "completed",
                "prediction_sha256": checkpoint.get("model_patch_sha256"),
                "grader_evidence_sha256": checkpoint.get("grader_evidence_sha256"),
                "test_log_sha256": log_sha256,
            }
        )

    binding = {
        "run_id": run_id,
        "protocol_fingerprint": prepared.protocol_fingerprint,
        "manifest_sha256": prepared.manifest_sha256,
        "image_bindings_sha256": prepared.image_bindings_sha256,
        "source_result_sha256": _sha256(source_result),
        "source_checkpoints_sha256": _sha256(
            _canonical([_sha256(raw) for raw in checkpoint_bytes])
        ),
        "swebench_version": SWEBENCH_VERSION,
        "control_image_digest": prepared.profile["control_image_digest"],
        "scorer_entrypoint_sha256": _expected_scorer_entrypoint_sha256(prepared.repo),
    }
    report_id = _sha256(_canonical(binding))
    report = {
        "schema_version": 1,
        "report_id": report_id,
        **binding,
        "task_count": len(prepared.tasks),
        "rescored_count": sum(task["rescored"] is True for task in task_reports),
        "tasks": task_reports,
    }
    report_bytes = _canonical(report)
    report_path = run_dir / f"rescore-report-{report_id}.json"
    try:
        existing = report_path.lstat()
    except FileNotFoundError:
        _write_private_json(report_path, report)
    else:
        existing_resolved = secure_resolve(report_path, must_exist=True)
        if (
            not stat.S_ISREG(existing.st_mode)
            or existing.st_mode & 0o077
            or existing_resolved.parent != run_dir.resolve(strict=True)
            or existing_resolved.read_bytes() != report_bytes
        ):
            raise SwebenchError("existing private rescore report differs from this replay")
    return {**report, "report_sha256": _sha256(report_bytes)}
